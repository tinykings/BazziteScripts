#!/usr/bin/env python3
"""Jump authentication -> KDE; confirmed disconnect -> Gaming Mode.

Uses Jump's public access log and Bazzite's supported steamosctl API.
Never modifies or restarts Jump. Unknown state inhibits switching.
"""
import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import pwd
import re
import subprocess
import time

HOME = Path.home()
STATE_DIR = HOME / '.local/state/jump-session-switch'
ACCESS_LOG = Path('/var/log/jump-connect/access.log')
BOOT_ID = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
USER = pwd.getpwuid(os.getuid()).pw_name
CONNECT_DELAY = 5
DISCONNECT_DELAY = 120
HANDOFF_GRACE = 300
SWITCH_TIMEOUT = 90


def log(message):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), message, flush=True)


def command(args, timeout=12):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed ({result.returncode}): {result.stderr.strip()[:300]}')
    return result.stdout.strip()


def properties(text):
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def service_info():
    p = properties(command(['systemctl', 'show', 'jump-connect.service',
                            '-p', 'ActiveState', '-p', 'MainPID',
                            '-p', 'ExecMainStartTimestampMonotonic']))
    if p.get('ActiveState') != 'active' or int(p.get('MainPID', '0')) <= 0:
        raise RuntimeError('Jump is not active; connection state is unknown')
    start = int(p.get('ExecMainStartTimestampMonotonic', '0')) / 1_000_000
    if not start:
        raise RuntimeError('Jump service start time unavailable')
    return p['MainPID'], start


def parse_access(text, service_start, wall_minus_mono, username):
    """Replay complete log for this Jump service lifetime, not previous boots."""
    connections = {}
    for line in text.splitlines():
        match = re.match(r'^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2} [AP]M)\s+(.*)$', line)
        if not match:
            continue
        stamp = datetime.strptime(match[1], '%m/%d/%Y %I:%M:%S %p').timestamp()
        moment = stamp - wall_minus_mono
        # Access timestamps have one-second precision.
        if moment < math.floor(service_start):
            continue
        body = match[2]
        ident = re.search(r'\bConnectionID:([^,\s]+)', body)
        if not ident:
            continue
        ident = ident[1]
        if body.startswith('Connection closed:'):
            connections.pop(ident, None)
        elif body.startswith(('Incomming Connection Established:', 'Incoming Connection Established:',
                              'Incomming Connection Request:', 'Incoming Connection Request:')):
            connections.setdefault(ident, {'user': None, 'auth': 0})
        elif body.startswith('Authentication Succeeded:'):
            local = re.search(r'\bLocalUser:([^,\s]+)', body)
            connections[ident] = {'user': local[1] if local else None, 'auth': moment}
    own = [c for c in connections.values() if c['user'] == username]
    # Established but unauthenticated sessions and other users inhibit all switches.
    others = any(c['user'] != username for c in connections.values())
    return bool(own), others, max((c['auth'] for c in own), default=0), len(connections)


def connection_snapshot():
    before = service_info()
    stat = ACCESS_LOG.stat()
    if stat.st_size > 4 * 1024 * 1024:
        raise RuntimeError('Access log exceeds safety limit; refusing incomplete replay')
    text = ACCESS_LOG.read_text(errors='replace')
    if text and not text.endswith('\n'):
        raise RuntimeError('Access log ends with an incomplete entry; retrying next poll')
    after_stat = ACCESS_LOG.stat()
    if (stat.st_ino, stat.st_size, stat.st_mtime_ns) != (after_stat.st_ino, after_stat.st_size, after_stat.st_mtime_ns):
        raise RuntimeError('Access log changed while reading; retrying next poll')
    if service_info() != before:
        raise RuntimeError('Jump service changed while reading; retrying next poll')
    snapshot = parse_access(text, before[1], time.time() - time.monotonic(), USER)
    return before[0], snapshot


def session_mode():
    sessions = json.loads(command(['loginctl', 'list-sessions', '--json=short', '--no-pager']))
    active = []
    for session in sessions:
        if session.get('seat') != 'seat0':
            continue
        p = properties(command(['loginctl', 'show-session', session['session'],
                                '-p', 'User', '-p', 'Active', '-p', 'Desktop', '-p', 'Type']))
        if p.get('Active') != 'yes':
            continue
        if p.get('User') != str(os.getuid()):
            return 'other'
        desktop = p.get('Desktop', '').lower()
        if 'gamescope' in desktop:
            active.append('game')
        elif 'plasma' in desktop or 'kde' in desktop:
            active.append('desktop')
        else:
            active.append('other')
    return active[0] if len(active) == 1 else 'unknown'


@dataclass
class Policy:
    owned: bool = False
    transition: str = ''
    requested_at: float = 0
    handoff_until: float = 0
    connected_since: float | None = None
    disconnected_since: float | None = None
    auth_cutoff: float = 0
    paused_reason: str = ''
    jump_pid: str = ''
    log_identity: str = ''
    log_size: int = 0

    def step(self, now, mode, connected, others, latest_auth):
        if self.paused_reason:
            return None
        if self.transition:
            if mode == self.transition:
                target = self.transition
                self.transition = ''
                if target == 'game':
                    self.owned = False
                    self.auth_cutoff = now
                    self.connected_since = None
                    self.disconnected_since = None
            elif now - self.requested_at >= SWITCH_TIMEOUT:
                self.paused_reason = f'{self.transition} switch not observed within {SWITCH_TIMEOUT}s'
            return None
        if others or mode not in ('game', 'desktop'):
            self.connected_since = None
            self.disconnected_since = None
            return None
        if mode == 'game':
            self.disconnected_since = None
            if self.owned:
                self.paused_reason = 'KDE was left outside automation; respecting manual override'
                return None
            if connected and latest_auth >= self.auth_cutoff:
                if self.connected_since is None:
                    self.connected_since = now
                if now - self.connected_since >= CONNECT_DELAY:
                    return 'desktop'
            else:
                self.connected_since = None
        elif self.owned:
            self.connected_since = None
            if connected:
                self.disconnected_since = None
            else:
                if self.disconnected_since is None:
                    self.disconnected_since = now
                if now >= self.handoff_until and now - self.disconnected_since >= DISCONNECT_DELAY:
                    return 'game'
        return None

    def requested(self, target, now):
        self.transition = target
        self.requested_at = now
        self.connected_since = None
        self.disconnected_since = None
        if target == 'desktop':
            self.owned = True
            self.handoff_until = now + HANDOFF_GRACE


def save(policy):
    path = STATE_DIR / 'state.json'
    temporary = STATE_DIR / 'state.json.tmp'
    temporary.write_text(json.dumps({'boot_id': BOOT_ID, **asdict(policy)}, indent=2) + '\n')
    temporary.replace(path)


def load():
    path = STATE_DIR / 'state.json'
    if not path.exists():
        return Policy()
    data = json.loads(path.read_text())
    if data.pop('boot_id', None) != BOOT_ID:
        return Policy()
    return Policy(**data)


def switch(target):
    args = ['steamosctl', 'switch-to-desktop-mode', 'plasma.desktop'] if target == 'desktop' else ['steamosctl', 'switch-to-game-mode']
    log('REQUESTING SESSION SWITCH: ' + ' '.join(args))
    output = command(args, timeout=30)
    if output:
        log(output[:500])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='log proposed actions only; never switch or save live state')
    parser.add_argument('--once', action='store_true', help='print one read-only snapshot and exit')
    args = parser.parse_args()
    if args.once:
        pid, (connected, others, latest, total) = connection_snapshot()
        print(json.dumps({'mode': session_mode(), 'jump_pid': pid,
                          'authenticated_as_current_user': connected, 'other_or_pending_connections': others,
                          'total_established_connections': total, 'latest_auth_monotonic': latest}, indent=2))
        return
    os.umask(0o077)
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Dry-run may coexist with the service; only a single live watcher is allowed.
    lock = (STATE_DIR / ('dry-run.lock' if args.dry_run else 'watcher.lock')).open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    policy = Policy() if args.dry_run else load()
    log('DRY RUN: no switches' if args.dry_run else 'Watcher active: authenticated connect -> KDE, confirmed idle -> game')
    last_status = None
    try:
        while True:
            try:
                paused_file = STATE_DIR / 'paused'
                if paused_file.exists():
                    policy.connected_since = policy.disconnected_since = None
                    status = ('paused by user',)
                else:
                    pid, (connected, others, latest, total) = connection_snapshot()
                    mode = session_mode()
                    log_stat = ACCESS_LOG.stat()
                    identity = f'{log_stat.st_dev}:{log_stat.st_ino}'
                    if policy.log_identity and (policy.log_identity != identity or log_stat.st_size < policy.log_size):
                        policy.paused_reason = 'Jump access log rotated or truncated; connection history is uncertain'
                    policy.log_identity = identity
                    policy.log_size = log_stat.st_size
                    if policy.jump_pid and policy.jump_pid != pid and policy.owned:
                        policy.paused_reason = 'Jump restarted while KDE was managed; connection history is uncertain'
                    policy.jump_pid = pid
                    action = policy.step(time.monotonic(), mode, connected, others, latest)
                    status = (mode, connected, others, total, policy.owned, policy.transition, policy.paused_reason)
                    if action:
                        if args.dry_run:
                            status += ('would switch to ' + action,)
                        else:
                            # Check again immediately before a potentially destructive action.
                            pid2, (connected2, others2, latest2, total2) = connection_snapshot()
                            mode2 = session_mode()
                            if (pid2, connected2, others2, mode2) != (pid, connected, others, mode) or (action == 'desktop' and latest2 < policy.auth_cutoff):
                                log('State changed before switch; deferring')
                                continue
                            policy.requested(action, time.monotonic())
                            save(policy)  # Persist loop guard before terminating the graphical session.
                            try:
                                switch(action)
                            except Exception as exc:
                                policy.paused_reason = 'Switch failed; no automatic retries: ' + str(exc)
                                log(policy.paused_reason)
                            save(policy)
                    if not args.dry_run:
                        save(policy)
                if status != last_status:
                    log('State: ' + repr(status))
                    last_status = status
            except Exception as exc:
                # Never interpret unreadable/ambiguous state as a disconnect.
                policy.connected_since = policy.disconnected_since = None
                status = ('inhibited', str(exc))
                if status != last_status:
                    log('INHIBITED: ' + str(exc))
                    last_status = status
            time.sleep(3)
    except KeyboardInterrupt:
        log('Observer stopped; current session left unchanged')


if __name__ == '__main__':
    main()
