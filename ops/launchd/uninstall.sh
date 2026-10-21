#!/usr/bin/env bash
# Unload and remove the daily paper runner launchd jobs.
set -euo pipefail

AGENTS="$HOME/Library/LaunchAgents"
GUI_UID="$(id -u)"
for LABEL in com.kairos.daily.place com.kairos.daily.reconcile com.kairos.daily.signal; do
	launchctl bootout "gui/$GUI_UID/$LABEL" 2>/dev/null || true
	rm -f "$AGENTS/$LABEL.plist"
	printf 'Removed %s\n' "$AGENTS/$LABEL.plist"
done
