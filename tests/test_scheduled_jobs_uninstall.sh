#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
sandbox="$(mktemp -d /tmp/kairos-uninstall-test.XXXXXX)"
trap 'rm -rf "$sandbox"' EXIT
mkdir -p "$sandbox/bin"
cat > "$sandbox/bin/systemctl" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_CALLS"
[[ "${FAKE_FAIL:-}" != "$*" ]]
FAKE
chmod +x "$sandbox/bin/systemctl"
export PATH="$sandbox/bin:$PATH"
jobs=(kairos-evening-build-picks kairos-morning-publish-reminder kairos-evening-report)
for scenario in success timer-failure service-failure; do
    export XDG_CONFIG_HOME="$sandbox/$scenario/config"
    export FAKE_CALLS="$sandbox/$scenario/calls"
    export FAKE_FAIL=""
    unit_dir="$XDG_CONFIG_HOME/systemd/user"
    mkdir -p "$unit_dir"
    for job in "${jobs[@]}"; do
        printf 'synthetic\n' > "$unit_dir/$job.timer"
        printf 'synthetic\n' > "$unit_dir/$job.service"
    done
    case "$scenario" in
        timer-failure) FAKE_FAIL="--user disable --now ${jobs[0]}.timer" ;;
        service-failure) FAKE_FAIL="--user stop ${jobs[0]}.service" ;;
    esac
    if bash "$repo_root/ops/systemd/uninstall.sh" > "$sandbox/$scenario/output" 2>&1; then
        [[ "$scenario" == success ]]
        for job in "${jobs[@]}"; do
            [[ ! -e "$unit_dir/$job.timer" && ! -e "$unit_dir/$job.service" ]]
        done
        expected="$sandbox/expected"
        for job in "${jobs[@]}"; do printf '%s\n' "--user disable --now $job.timer"; done > "$expected"
        for job in "${jobs[@]}"; do printf '%s\n' "--user stop $job.service"; done >> "$expected"
        cmp "$expected" "$FAKE_CALLS"
    else
        [[ "$scenario" != success ]]
        for job in "${jobs[@]}"; do
            [[ -e "$unit_dir/$job.timer" && -e "$unit_dir/$job.service" ]]
        done
    fi
done
printf 'PASS: fake-manager success, timer failure, service failure\n'
