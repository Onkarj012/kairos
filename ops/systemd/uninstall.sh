#!/usr/bin/env bash
set -euo pipefail

unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
for timer in kairos-evening-build-picks kairos-morning-publish-reminder kairos-evening-report; do
    systemctl --user disable --now "$timer.timer" 2>/dev/null || true
done
rm -f "$unit_dir"/kairos-evening-build-picks.{service,timer} \
    "$unit_dir"/kairos-morning-publish-reminder.{service,timer} \
    "$unit_dir"/kairos-evening-report.{service,timer}
printf 'Removed Kairos user unit files. Run systemctl --user daemon-reload to refresh the manager.\n'
