# Jump Desktop / Bazzite session automation

This is custom, local automation, not a Jump-supported integration. Jump's service,
configuration, packages, display settings, and input devices are left unchanged.

## Activate

From a terminal as ryan (do not run the whole script with sudo):

```sh
bash ~/jump-mode.sh enable
```

This may ask for sudo solely to enable user lingering. Lingering keeps the user
manager and our watcher alive across graphical logout. The helper records whether
lingering was originally enabled. It installs and enables a user systemd unit.

WARNING: With an existing authenticated Jump connection, enabling the watcher
will request KDE after about 5 seconds. Session switching can close all running
games/applications and lose unsaved work. Jump may disconnect. Reconnect manually.
This cannot guarantee remote access to the replacement session.

Automatic suspend is enabled by default. A working Wake-on-LAN (or other wake)
solution is required: Jump alone cannot connect while the PC is asleep.

## Behavior

- Replay /var/log/jump-connect/access.log for the currently running Jump service.
- Only a successful LocalUser authentication for the current user can trigger KDE.
- Switch using `steamosctl switch-to-desktop-mode plasma.desktop`.
- Wait at least 5 minutes after that switch request before considering a return.
- Once all connections are closed for at least 2 minutes, return using
  `steamosctl switch-to-game-mode`.
- After Gaming Mode is confirmed, start a 30-second suspend countdown. A Jump
  connection or connection attempt during that window cancels sleep. Only an
  automatic return from a managed KDE session arms this one-shot countdown;
  ordinary Gaming Mode startup does not.
- Suspend using `systemctl --no-ask-password --check-inhibitors=yes --no-block suspend`.
  Respect sleep inhibitors and authorization; do not force sleep or install
  special privileges. Submission is asynchronous; the system journal records
  whether sleep actually succeeds. Failure does not cause repeated attempts.
- The countdown is consumed before requesting suspend, so waking by WoL does
  not immediately put the PC back to sleep. A watcher restart, manual pause,
  unknown state, or departure from Gaming Mode also cancels pending sleep.
- Reconnecting cancels the disconnect countdown. Multiple connections must all end.
- Only return automatically from KDE opened by this watcher. A KDE session that
  was already running when the watcher started is not claimed or closed.
- Unknown login/connection state inhibits actions. Other-user/pending connections
  also inhibit actions. Log rotation/truncation and Jump restart while managing
  KDE trigger a safety pause. A failed switch or unexpected manual exit from KDE
  pauses rather than repeatedly switching sessions.
- Persistent transition guards survive watcher restart on the same boot.
- Previous-boot state and connection log entries are not used to trigger a switch.
- Timing uses the monotonic clock; suspend time does not consume countdowns.

The watcher polls every 3 seconds. Connection detection relies on Jump's present
access-log format; a future change may need a parser update. Logs larger than
4 MiB inhibit automation instead of replaying a potentially incomplete tail.
The code is deliberately fail-closed rather than trying to recover every case.

## Controls

```sh
bash ~/jump-mode.sh status    # service + read-only session/connection snapshot
bash ~/jump-mode.sh logs      # last 50 watcher journal records
bash ~/jump-mode.sh pause     # pause switching and cancel pending suspend
bash ~/jump-mode.sh resume    # resume, unless a safety fault is recorded
bash ~/jump-mode.sh sleep-off # disable only automatic suspend (persistent)
bash ~/jump-mode.sh sleep-on  # enable suspend for the next managed return
bash ~/jump-mode.sh disable   # stop and disable only our watcher
bash ~/jump-mode.sh reset     # stop watcher, archive its state, clear pause
```

After a safety fault, inspect logs, then use `reset` followed by `enable`. Resetting
in KDE relinquishes ownership: the watcher will not automatically close that KDE
session. None of the disable/pause/reset commands switches the current session.
A switch already submitted to SteamOS Manager cannot necessarily be cancelled.
Likewise, a suspend request already submitted to logind cannot necessarily be
cancelled. Pause/sleep-off are checked again immediately before submission, but
there is an unavoidable small race between the last check and the request.

## Rollback

```sh
bash ~/jump-mode.sh rollback
```

This stops/disables and removes our user unit. It does not switch the current
session or delete diagnostic files. If lingering was initially disabled, the
helper prints the optional command to restore that setting:

```sh
sudo loginctl disable-linger ryan
```

Check whether other user services now depend on lingering before restoring it.
Scripts are under ~/.local/share/jump-session-switch; private state is under
~/.local/state/jump-session-switch. The helper is ~/jump-mode.sh.
The user unit is ~/.config/systemd/user/jump-session-switch.service.
No system-level unit or Jump unit is modified. The default boot login mode is
not changed; it was Gaming Mode at implementation time.

## Validation

```sh
cd ~/.local/share/jump-session-switch
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v
python3 watcher.py --once
python3 watcher.py --dry-run
```

Unit tests and a live, read-only dry run have passed. Both session-switch directions
were subsequently verified in the live journal, including disconnect -> Gaming
Mode and reconnect -> KDE. Suspend policy and command arguments are unit-tested
with the suspend command mocked; actual suspend/resume has not yet been tested.
Dry-run prints intended actions but never invokes steamosctl, submits suspend,
or writes the live policy state. Ctrl+C stops it. Do not mistake simulated
transitions for validated suspend/resume.
