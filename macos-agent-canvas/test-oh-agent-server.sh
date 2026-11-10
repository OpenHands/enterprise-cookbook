#!/bin/bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
assert_not_in_file() {
  if grep -Fq "$1" "$2"; then
    echo "unexpected text in $2" >&2
    exit 1
  fi
}
export OHAS_HOME="$TMP/server"
export OHAS_CLIPBOARD="$TMP/clipboard"
export OHAS_TARGET_HOME="$TMP/target-home"
export OHAS_TEST_LOADED="$TMP/loaded"
export OHAS_TEST_BOOTOUT="$TMP/bootout-pending"
export OHAS_TEST_CALLS="$TMP/launchctl-calls"
export OHAS_TEST_AUDIT="$TMP/sudo-audit"
export TMP_USER_COMMANDS="$TMP/user-commands"
mkdir -p "$TMP/bin" "$OHAS_HOME" "$OHAS_TARGET_HOME/.local/bin"
chmod 700 "$OHAS_HOME"
cat >"$TMP/bin/id" <<'EOF'
#!/bin/bash
case "$*" in
  '-P agenttest') echo "agenttest:*:800:20::0:0:Agent Test:$OHAS_TARGET_HOME:/bin/bash" ;;
  '-gn agenttest') echo staff ;;
  *) /usr/bin/id "$@" ;;
esac
EOF
cat >"$OHAS_TARGET_HOME/.local/bin/uv" <<'EOF'
#!/bin/bash
case "$1" in
  venv) mkdir -p "$4/bin"; printf '#!/bin/bash\nexit 0\n' >"$4/bin/python"; chmod +x "$4/bin/python" ;;
  pip) exit 0 ;;
  *) exit 1 ;;
esac
EOF
cat >"$TMP/bin/sudo" <<'EOF'
#!/bin/bash
if [ "${OHAS_SESSION_API_KEY+x}" = x ]; then echo 'supplied key leaked to sudo' >&2; exit 1; fi
if [ "${OHAS_DENY_SUDO:-0}" = 1 ]; then exit 1; fi
if [ "$1" = -H ]; then
  shift 2
  echo "$1" >>"$TMP_USER_COMMANDS"
  shift
  /bin/bash -c 'cd "$1" && shift && exec "$@"' bash "$OHAS_TARGET_HOME" "$@"
  exit $?
fi
case "$*" in
  -v) echo validate >>"$OHAS_TEST_AUDIT"; exit 0 ;;
  '-n -l /bin/launchctl print system/dev.openhands.agent-server'|'-n -l /bin/launchctl kickstart -k system/dev.openhands.agent-server')
    [ "${OHAS_DENY_UNATTENDED:-0}" != 1 ] ;;
  '-n /bin/launchctl print system/dev.openhands.agent-server')
    [ "${OHAS_DENY_UNATTENDED:-0}" != 1 ] && { test -f "${OHAS_HOME:-}/loaded" || test -f "$OHAS_TEST_LOADED"; } ;;
  '-n /bin/launchctl kickstart -k system/dev.openhands.agent-server')
    [ "${OHAS_DENY_UNATTENDED:-0}" != 1 ] || exit 1
    echo unattended-restart >>"$OHAS_TEST_AUDIT" ;;
  'launchctl print system/dev.openhands.agent-server') test -f "${OHAS_HOME:-}/loaded" || test -f "$OHAS_TEST_LOADED" ;;
  'launchctl bootout system/dev.openhands.agent-server')
    [ "${OHAS_TEST_DENY_BOOTOUT:-0}" != 1 ] || exit 1
    echo bootout >>"$OHAS_TEST_CALLS"
    rm -f "${OHAS_HOME:-}/loaded" "$OHAS_TEST_LOADED"
    touch "$OHAS_TEST_BOOTOUT" ;;
  'launchctl bootstrap system '*)
    echo bootstrap >>"$OHAS_TEST_CALLS"
    if [ -f "$OHAS_TEST_BOOTOUT" ]; then
      rm -f "$OHAS_TEST_BOOTOUT"
      exit 5
    fi
    touch "$OHAS_TEST_LOADED" ;;
  'launchctl enable system/dev.openhands.agent-server') exit 0 ;;
  'launchctl kickstart system/dev.openhands.agent-server') exit 0 ;;
  'launchctl kickstart -k system/dev.openhands.agent-server')
    echo kickstart >>"$OHAS_TEST_CALLS"
    [ -z "${OHAS_HOME:-}" ] || echo restart >>"$OHAS_HOME/restarts" ;;
  install*) cp "${@: -2}" ;;
  'rm -f '*)
    [ "${OHAS_TEST_DENY_PLIST_RM:-0}" != 1 ] || exit 1
    [ "${OHAS_TEST_KEEP_PLIST:-0}" != 1 ] || exit 0
    /bin/rm "${@:2}" ;;
  *) exit 1 ;;
esac
EOF
cat >"$TMP/bin/pbcopy" <<'EOF'
#!/bin/bash
cat >"$OHAS_CLIPBOARD"
EOF
cat >"$TMP/bin/ifconfig" <<'EOF'
#!/bin/bash
cat <<'INTERFACES'
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
    inet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,RUNNING,MULTICAST> mtu 1500
    inet 192.0.2.10 netmask 0xffffff00
    inet6 2001:db8::10 prefixlen 64
    inet6 fdb1:83a1:b887:5826:894:f40e:cb4a:dd2d prefixlen 64
    status: active
en1: flags=8863<UP,BROADCAST,RUNNING,MULTICAST> mtu 1500
    inet 198.51.100.4 netmask 0xffffff00
    status: inactive
awdl0: flags=8863<UP,BROADCAST,RUNNING,MULTICAST> mtu 1500
    inet6 fe80::1234%awdl0 prefixlen 64
    status: active
utun3: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1500
    inet 10.8.0.1 netmask 0xffffffff
INTERFACES
EOF
chmod +x "$TMP/bin/id" "$TMP/bin/sudo" "$TMP/bin/pbcopy" "$TMP/bin/ifconfig" "$OHAS_TARGET_HOME/.local/bin/uv"
export PATH="$TMP/bin:$PATH"

# Source the actual functions without dispatching a CLI command. This lets the
# test point the privileged plist at a temporary file instead of /Library.
source <(sed '/^\[ \$# -ge 1 \]/,$d' "$HERE/oh-agent-server.sh")
PLIST="$TMP/agent-server.plist"
touch "$PLIST"
wait_healthy() { return 0; }
write_config 127.0.0.1 18100 old-key encryption-key

[ "$(stat -f '%Sp' "$OHAS_HOME")" = drwx------ ]
[ "$(stat -f '%Sp' "$CONFIG")" = -rw------- ]
[ "$(cmd_key)" = old-key ]
[ "$(cmd_key --copy)" = '==> Session API key copied to clipboard' ]
[ "$(cat "$OHAS_CLIPBOARD")" = old-key ]
if (cmd_key --unknown >/dev/null 2>&1); then exit 1; fi
if (cmd_key --defer-restart >/dev/null 2>&1); then exit 1; fi
if (cmd_key --unattended >/dev/null 2>&1); then exit 1; fi
if (cmd_key --reset --defer-restart --unattended >/dev/null 2>&1); then exit 1; fi
if (SERVICE_USER=agenttest cmd_key --reset --defer-restart >/dev/null 2>&1); then exit 1; fi
if (SERVICE_USER=agenttest cmd_key --reset --unattended >/dev/null 2>&1); then exit 1; fi

export OHAS_DENY_SUDO=1 OHAS_SESSION_API_KEY='deferred-reset_key.1234567890abcdefghij'
touch "$OHAS_HOME/loaded"
output="$(cmd_key --reset --defer-restart)"
load_config
[ "$OH_SESSION_API_KEYS_0" = "$OHAS_SESSION_API_KEY" ]
[ "$output" = '==> Session API key saved; restart the service to apply it' ]
[ ! -e "$OHAS_HOME/restarts" ] && [ ! -e "$OHAS_TEST_AUDIT" ]
unset OHAS_DENY_SUDO OHAS_SESSION_API_KEY
rm -f "$OHAS_HOME/loaded"
write_config 127.0.0.1 18100 old-key encryption-key

output="$(cmd_key --reset)"
load_config
[ "$OH_SESSION_API_KEYS_0" != old-key ]
[ "${#OH_SESSION_API_KEYS_0}" -eq 64 ]
[ "$(stat -f '%Sp' "$CONFIG")" = -rw------- ]
[ "$OH_SECRET_KEY" = encryption-key ]
[ "$OHAS_HOST:$OHAS_PORT" = 127.0.0.1:18100 ]
[ "$output" = "==> Session API key rotated; service is stopped. Run: $0 start" ]
[[ "$output" != *"$OH_SESSION_API_KEYS_0"* ]]
[ "$(cmd_key)" = "$OH_SESSION_API_KEYS_0" ]
[ ! -f "$OHAS_HOME/restarts" ]

export OHAS_SESSION_API_KEY='provided-reset_key.1234567890abcdefghij'
output="$(cmd_key --reset)"
load_config
[ "$OH_SESSION_API_KEYS_0" = "$OHAS_SESSION_API_KEY" ]
[[ "$output" != *"$OHAS_SESSION_API_KEY"* ]]
[ "$(stat -f '%Sp' "$CONFIG")" = -rw------- ]
unset OHAS_SESSION_API_KEY

before="$OH_SESSION_API_KEYS_0"
touch "$OHAS_HOME/loaded"
export OHAS_SESSION_API_KEY='provided-copy_key.4567890abcdefghijklm'
output="$(cmd_key --copy --reset)"
load_config
[ "$OH_SESSION_API_KEYS_0" = "$OHAS_SESSION_API_KEY" ]
unset OHAS_SESSION_API_KEY
[ "$OH_SESSION_API_KEYS_0" != "$before" ]
[ "$(cat "$OHAS_CLIPBOARD")" = "$OH_SESSION_API_KEYS_0" ]
[ "$(cat "$OHAS_HOME/restarts")" = restart ]
[ "${output##*$'\n'}" = '==> Session API key copied to clipboard' ]
[[ "$output" != *"$OH_SESSION_API_KEYS_0"* ]]

output="$(cmd_start)"
[[ "$output" == *'Agent Server: http://127.0.0.1:18100'* ]]
[[ "$output" == *'/v1/'* && "$output" == *'/sockets/'* && "$output" == *'key [--copy] [--reset [--defer-restart|--unattended]]'* ]]
output="$(cmd_restart)"
[[ "$output" == *'/openapi.json'* && "$output" == *'/ready'* ]]
[ "$(wc -l <"$OHAS_HOME/restarts" | tr -d ' ')" -eq 2 ]

before="$OH_SESSION_API_KEYS_0"
output="$(cmd_key --reset)"
load_config
[ "$OH_SESSION_API_KEYS_0" != "$before" ]
[ "$output" = '==> Session API key rotated; existing clients must use the new key' ]
[[ "$output" != *"$OH_SESSION_API_KEYS_0"* ]]
[ "$(cat "$OHAS_CLIPBOARD")" = "$before" ]
[ "$(wc -l <"$OHAS_HOME/restarts" | tr -d ' ')" -eq 3 ]

before="$OH_SESSION_API_KEYS_0"
export OHAS_SESSION_API_KEY='unattended-reset_key.1234567890abcdefghij'
export OHAS_DENY_UNATTENDED=1
if (cmd_key --reset --unattended >/dev/null 2>&1); then exit 1; fi
load_config
[ "$OH_SESSION_API_KEYS_0" = "$before" ]
unset OHAS_DENY_UNATTENDED
validations_before="$(grep -c '^validate$' "$OHAS_TEST_AUDIT")"
output="$(cmd_key --reset --unattended)"
load_config
[ "$OH_SESSION_API_KEYS_0" = "$OHAS_SESSION_API_KEY" ]
[ "$output" = '==> Session API key rotated; existing clients must use the new key' ]
[ "$(grep -c '^validate$' "$OHAS_TEST_AUDIT")" -eq "$validations_before" ]
[ "$(tail -n 1 "$OHAS_TEST_AUDIT")" = unattended-restart ]
unset OHAS_SESSION_API_KEY

before="$OH_SESSION_API_KEYS_0"
for invalid in '' short "invalid'key-012345678901234567890123456789" $'bad\nkey-012345678901234567890123456789' "$before"; do
  if (OHAS_SESSION_API_KEY="$invalid" cmd_key --reset >/dev/null 2>&1); then exit 1; fi
done
load_config
[ "$OH_SESSION_API_KEYS_0" = "$before" ]
[ "$(wc -l <"$OHAS_HOME/restarts" | tr -d ' ')" -eq 3 ]
export OHAS_DENY_SUDO=1
if (cmd_key --reset >/dev/null 2>&1); then exit 1; fi
load_config
[ "$OH_SESSION_API_KEYS_0" = "$before" ]
unset OHAS_DENY_SUDO
rm -f "$PLIST"

if (select_service_user missing-account >/dev/null 2>&1); then exit 1; fi
if (select_service_user '--bad' >/dev/null 2>&1); then exit 1; fi
export OHAS_HOME="$TMP/managed"
set_root "$OHAS_HOME"
if (OHAS_SESSION_API_KEY=short cmd_install --user agenttest >/dev/null 2>&1); then exit 1; fi
if (OHAS_SESSION_API_KEY="invalid'key-012345678901234567890123456789" cmd_install --user agenttest >/dev/null 2>&1); then exit 1; fi
[ ! -f "$CONFIG" ]
export OHAS_SESSION_API_KEY='provided-install_key.1234567890abcdefghij'
cmd_install --user agenttest --host 127.0.0.1 >"$TMP/install-output" 2>&1
grep -Fq 'WARNING: Anyone who can reach this server and has the X-Session-API-Key can execute arbitrary shell commands as agenttest.' "$TMP/install-output"
grep -Fq 'Keep the key secret' "$TMP/install-output"
[ "$(tail -n 2 "$TMP/install-output" | head -n 1)" = 'WARNING: Anyone who can reach this server and has the X-Session-API-Key can execute arbitrary shell commands as agenttest.' ]
[ "$(tail -n 1 "$TMP/install-output")" = '         Keep the key secret; use --host 127.0.0.1 for local-only access.' ]
[ "$(tail -n 4 "$TMP/install-output" | head -n 2 | wc -l | tr -d ' ')" -eq 2 ]
[ -z "$(tail -n 4 "$TMP/install-output" | head -n 2 | tr -d '[:space:]')" ]
awk '/^  Session API key:/{if (prev != "" || prev2 != "") exit 1; found=1} {prev2=prev; prev=$0} END {if (!found) exit 1}' "$TMP/install-output"
grep -Fq "Copy without displaying:   $0 key --copy" "$TMP/install-output"
grep -Fq "Display in terminal:       $0 key" "$TMP/install-output"
grep -Fq "Rotate without displaying: $0 key --reset" "$TMP/install-output"
grep -Fq 'Custom key: Set OHAS_SESSION_API_KEY for install or key --reset.' "$TMP/install-output"
grep -Fq 'Requires 32+ chars: A-Z a-z 0-9 . _ ~ + / = -' "$TMP/install-output"
[ "$(plutil -extract UserName raw -o - "$PLIST")" = agenttest ]
[ "$(plutil -extract EnvironmentVariables.HOME raw -o - "$PLIST")" = "$OHAS_TARGET_HOME" ]
[ "$(plutil -extract WorkingDirectory raw -o - "$PLIST")" = "$OHAS_HOME/data" ]
[ "$(stat -f '%Sp' "$OHAS_HOME")" = drwx------ ]
[ "$(stat -f '%Sp' "$OHAS_HOME/config.env")" = -rw------- ]
[ "$(stat -f '%Sp' "$OHAS_HOME/run-server.sh")" = -rwxr-xr-x ]
[ "$(grep -c '^agenttest$' "$TMP_USER_COMMANDS")" -gt 0 ]
load_config
before="$OH_SESSION_API_KEYS_0"
[ "$before" = "$OHAS_SESSION_API_KEY" ]
unset OHAS_SESSION_API_KEY
assert_not_in_file "$before" "$TMP/install-output"
assert_not_in_file 'Local network URLs' "$TMP/install-output"

unset OHAS_HOME
select_installed_service
[ "$ROOT" = "$TMP/managed" ]
[ "$SERVICE_USER" = agenttest ]
bootouts_before="$(grep -c '^bootout$' "$OHAS_TEST_CALLS" || true)"
cmd_install --host 127.0.0.1 >"$TMP/reinstall-output" 2>&1
grep -Fq 'WARNING: Anyone who can reach this server and has the X-Session-API-Key can execute arbitrary shell commands as agenttest.' "$TMP/reinstall-output"
[ "$(tail -n 2 "$TMP/reinstall-output" | head -n 1)" = 'WARNING: Anyone who can reach this server and has the X-Session-API-Key can execute arbitrary shell commands as agenttest.' ]
[ "$(tail -n 1 "$TMP/reinstall-output")" = '         Keep the key secret; use --host 127.0.0.1 for local-only access.' ]
[ "$(grep -c '^bootout$' "$OHAS_TEST_CALLS")" -eq "$bootouts_before" ]
[ "$(tail -n 1 "$OHAS_TEST_CALLS")" = kickstart ]
load_config
[ "$OH_SESSION_API_KEYS_0" = "$before" ]
[[ "$(cat "$TMP/reinstall-output")" == *'Runs as: agenttest'* ]]
assert_not_in_file "$before" "$TMP/reinstall-output"

export OHAS_SESSION_API_KEY='provided-upgrade_key.1234567890abcdefghij'
cmd_install --host 127.0.0.1 >"$TMP/override-output" 2>&1
load_config
[ "$OH_SESSION_API_KEYS_0" = "$OHAS_SESSION_API_KEY" ]
[ "$OH_SESSION_API_KEYS_0" != "$before" ]
assert_not_in_file "$OHAS_SESSION_API_KEY" "$TMP/override-output"
unset OHAS_SESSION_API_KEY
before="$OH_SESSION_API_KEYS_0"

cmd_install --host 0.0.0.0 >"$TMP/network-output" 2>&1
grep -Fq 'Local network URLs (firewall and routing permitting):' "$TMP/network-output"
grep -Fq 'en0: http://192.0.2.10:18100' "$TMP/network-output"
if grep -Eq '2001:db8|fdb1:83a1|http://\[' "$TMP/network-output"; then
  echo 'IPv6 URL advertised by IPv4-only server' >&2
  exit 1
fi
grep -Fq 'utun3: http://10.8.0.1:18100' "$TMP/network-output"
if grep -Eq 'en1:|lo0:|awdl0:' "$TMP/network-output"; then
  echo 'inactive or local-only interface advertised' >&2
  exit 1
fi
assert_not_in_file "$before" "$TMP/network-output"

cmd_install --user agenttest --host 127.0.0.1 >"$TMP/same-user-output"
[ "$ROOT" = "$TMP/managed" ]
load_config
[ "$OH_SESSION_API_KEYS_0" = "$before" ]
export OHAS_HOME="$OHAS_TARGET_HOME/.openhands-agent-server"
cmd_install --user agenttest --host 127.0.0.1 >"$TMP/new-home-output"
[ "$(tail -n 2 "$OHAS_TEST_CALLS" | tr '\n' ' ')" = 'bootstrap bootstrap ' ]
[ "$ROOT" = "$OHAS_TARGET_HOME/.openhands-agent-server" ]
[ -f "$TMP/managed/config.env" ]
load_config
[ "$OH_SESSION_API_KEYS_0" != "$before" ]
[ "$(plutil -extract WorkingDirectory raw -o - "$PLIST")" = "$ROOT/data" ]

select_installed_service
[ "$(cmd_key)" = "$OH_SESSION_API_KEYS_0" ]
export OHAS_HOME="$ROOT"
before="$OH_SESSION_API_KEYS_0"
cmd_key --reset >"$TMP/managed-reset-output"
load_config
[ "$OH_SESSION_API_KEYS_0" != "$before" ]
echo 'target-owned-log' >"$LOGS/agent-server.log"
[ "$(cmd_logs)" = target-owned-log ]

if (set_root "$OHAS_TARGET_HOME"; cmd_remove --purge --yes >/dev/null 2>&1); then exit 1; fi
[ -f "$PLIST" ]

set_root "$TMP/previous-user"
mkdir -p "$ROOT" "$DATA" "$LOGS"
SERVICE_USER="$(id -un)"
SERVICE_GROUP="$(id -gn)"
SERVICE_HOME="$HOME"
write_config 127.0.0.1 18100 previous-user-key previous-secret
write_plist
unset OHAS_HOME
cmd_install --user agenttest --host 127.0.0.1 >"$TMP/change-user-output"
[ "$ROOT" = "$OHAS_TARGET_HOME/.openhands-agent-server" ]
[ "$(plutil -extract UserName raw -o - "$PLIST")" = agenttest ]
[ -f "$TMP/previous-user/config.env" ]
[[ "$(cat "$TMP/change-user-output")" == *'old files are not migrated'* ]]

export OHAS_DENY_SUDO=1
if (cmd_remove --purge --yes >/dev/null 2>&1); then exit 1; fi
[ -f "$PLIST" ] && [ -d "$ROOT" ]
unset OHAS_DENY_SUDO

export OHAS_TEST_DENY_BOOTOUT=1
if (cmd_remove --purge --yes >/dev/null 2>&1); then exit 1; fi
[ -f "$PLIST" ] && [ -d "$ROOT" ]
unset OHAS_TEST_DENY_BOOTOUT

export OHAS_TEST_DENY_PLIST_RM=1
if (cmd_remove --purge --yes >/dev/null 2>&1); then exit 1; fi
[ -f "$PLIST" ] && [ -d "$ROOT" ]
unset OHAS_TEST_DENY_PLIST_RM

export OHAS_TEST_KEEP_PLIST=1
if (cmd_remove --purge --yes >/dev/null 2>&1); then exit 1; fi
[ -f "$PLIST" ] && [ -d "$ROOT" ]
unset OHAS_TEST_KEEP_PLIST

cmd_remove --purge --yes >"$TMP/remove-output"
[ ! -e "$PLIST" ] && [ ! -e "$ROOT" ]

write_plist
cmd_remove --purge --yes >"$TMP/retry-remove-output"
[ ! -e "$PLIST" ]

echo 'Installer user and key tests passed'
