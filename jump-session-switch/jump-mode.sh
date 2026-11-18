#!/usr/bin/env bash
# Manage only our session watcher. Never stops/reconfigures Jump Desktop.
set -euo pipefail
if [[ $(id -u) -eq 0 ]]; then
    printf 'Run this as your normal desktop user, not with sudo.\n' >&2
    exit 1
fi
base="$HOME/.local/share/jump-session-switch"
state="$HOME/.local/state/jump-session-switch"
unit="$HOME/.config/systemd/user/jump-session-switch.service"
name=jump-session-switch.service
mkdir -p "$state"
chmod 700 "$state"
case "${1:-status}" in
    enable)
        printf 'Enabling session automation. Current applications may close and Jump may disconnect.\n'
        printf 'Authenticated Jump connection -> KDE after 5 seconds.\n'
        printf 'No connections -> Gaming Mode after 2 minutes, with a 5-minute switch grace period.\n'
        if [[ -e "$unit" ]] && ! cmp -s "$base/$name" "$unit"; then
            printf 'Existing unit differs from our template; refusing to overwrite it.\n' >&2
            exit 1
        fi
        if [[ -e "$state/state.json" ]]; then
            python3 - "$state/state.json" <<'PY'
import json,sys
from pathlib import Path
s=json.load(open(sys.argv[1]))
boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
if s.get('boot_id') == boot and s.get('paused_reason'):
    print('Safety pause: ' + s['paused_reason'])
    raise SystemExit('Inspect status/logs, then use reset followed by enable for a fresh start.')
PY
        fi
        before=$(loginctl show-user "$(id -un)" -p Linger --value)
        if [[ ! -e "$state/linger-before" ]]; then
            printf '%s\n' "$before" > "$state/linger-before"
        fi
        if [[ "$before" != yes ]]; then
            printf 'Enabling user lingering so the watcher survives graphical logout.\n'
            sudo loginctl enable-linger "$(id -un)"
        fi
        rm -f "$state/paused"
        install -D -m 644 "$base/$name" "$unit"
        systemctl --user daemon-reload
        systemctl --user enable --now "$name"
        printf 'Enabled. Reconnect through Jump if the session changes.\n'
        ;;
    disable)
        touch "$state/paused"
        systemctl --user disable --now "$name" || true
        printf 'Automation disabled. Current graphical session and Jump left unchanged.\n'
        ;;
    pause)
        touch "$state/paused"
        printf 'Automation paused. Current session left unchanged.\n'
        ;;
    resume)
        rm -f "$state/paused"
        systemctl --user start "$name"
        printf 'Resumed unless a safety fault is recorded. Use status to check.\n'
        ;;
    reset)
        touch "$state/paused"
        systemctl --user stop "$name" || true
        if [[ -e "$state/state.json" ]]; then
            mv "$state/state.json" "$state/state-before-reset-$(date +%s).json"
        fi
        rm -f "$state/paused"
        printf 'State reset; watcher remains stopped. Run enable to activate again.\n'
        printf 'An already-running KDE session will not be automatically closed after this reset.\n'
        ;;
    status)
        systemctl --user status "$name" --no-pager --lines=8 || true
        python3 "$base/watcher.py" --once || true
        if [[ -e "$state/paused" ]]; then printf '\nManual pause is set.\n'; fi
        if [[ -e "$state/state.json" ]]; then
            python3 - "$state/state.json" <<'PY'
import json,sys
s=json.load(open(sys.argv[1]))
print('\nManaged KDE:', s.get('owned'), '| transition:', s.get('transition'))
print('Safety pause:', s.get('paused_reason') or 'none')
PY
        fi
        ;;
    logs)
        journalctl --user -u "$name" --no-pager -n 50
        ;;
    rollback)
        touch "$state/paused"
        systemctl --user disable --now "$name" || true
        if [[ -e "$unit" ]]; then
            if ! cmp -s "$base/$name" "$unit"; then
                printf 'Unit changed since installation; refusing to remove it.\n' >&2
                exit 1
            fi
            rm "$unit"
            systemctl --user daemon-reload
        fi
        printf 'Watcher unit removed. Scripts/logs retained; current graphical session unchanged.\n'
        if [[ -e "$state/linger-before" ]] && grep -qx no "$state/linger-before"; then
            printf 'User lingering was previously off. To restore it, after checking other user services:\n'
            printf '  sudo loginctl disable-linger %s\n' "$(id -un)"
        fi
        ;;
    *)
        printf 'Usage: bash ~/jump-mode.sh {enable|disable|pause|resume|reset|status|logs|rollback}\n' >&2
        exit 2
        ;;
esac
