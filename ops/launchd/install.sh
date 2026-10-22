#!/usr/bin/env bash
# Render and load the daily paper runner launchd jobs.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if ! UV="$(command -v uv)"; then
	printf '%s\n' "uv was not found on PATH. Install uv before running this script." >&2
	exit 1
fi
# The runner reads the NSE holiday PDF with pdftotext, and launchd starts with a bare PATH.
if ! PDFTOTEXT="$(command -v pdftotext)"; then
	printf '%s\n' "pdftotext was not found on PATH. Install poppler before running this script." >&2
	exit 1
fi
JOB_PATH="$(dirname "$PDFTOTEXT"):/usr/bin:/bin:/usr/sbin:/sbin"
if [[ "$(date +%Z)" != "IST" ]]; then
	printf '%s\n' "This script requires the Mac timezone to be IST (date +%Z must print IST)." >&2
	exit 1
fi

AGENTS="$HOME/Library/LaunchAgents"
LOGS="$REPO/derived_data/daily-runner/runner-v1/logs"
mkdir -p "$AGENTS" "$LOGS"
GUI_UID="$(id -u)"
for JOB in place reconcile signal; do
	LABEL="com.kairos.daily.$JOB"
	FILE="$AGENTS/$LABEL.plist"
	sed -e "s#__REPO__#$REPO#g" -e "s#__UV__#$UV#g" -e "s#__PATH__#$JOB_PATH#g" "$REPO/ops/launchd/$LABEL.plist" > "$FILE"
	plutil -lint "$FILE"
	launchctl bootout "gui/$GUI_UID/$LABEL" 2>/dev/null || true
	launchctl bootstrap "gui/$GUI_UID" "$FILE"
done

for LABEL in com.kairos.daily.place com.kairos.daily.reconcile com.kairos.daily.signal; do
	launchctl print "gui/$GUI_UID/$LABEL" | grep -E 'state|path'
done

printf '%s\n' "Run these power settings yourself if needed:" \
	"sudo pmset -c sleep 0" \
	"sudo pmset repeat wakeorpoweron MTWRFSU 08:40:00"
