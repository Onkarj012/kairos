#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$unit_dir"
for unit in "$repo_root"/ops/systemd/kairos-*.service "$repo_root"/ops/systemd/kairos-*.timer; do
    sed "s|__KAIROS_REPO_ROOT__|$repo_root|g" "$unit" > "$unit_dir/$(basename "$unit")"
done
printf 'Installed unit files in %s. Owner action required: review them, then run systemctl --user daemon-reload and enable/start selected timers. Nothing was enabled or started.\n' "$unit_dir"
