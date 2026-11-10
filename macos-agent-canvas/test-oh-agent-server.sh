#!/bin/bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export OHAS_HOME="$TMP/server"
export OHAS_CLIPBOARD="$TMP/clipboard"
export OHAS_TARGET_HOME="$TMP/target-home"
export OHAS_TEST_LOADED="$TMP/loaded"
export OHAS_TEST_BOOTOUT="$TMP/bootout-pending"
export OHAS_TEST_CALLS="$TMP/launchctl-calls"
export TMP_USER_COMMANDS="$TMP/user-commands"
mkdir -p "$TMP/bin" "$OHAS_HOME" "$OHAS_TARGET_HOME/.local/bin"
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
if [ "${OHAS_DENY_SUDO:-0}" = 1 ]; then exit 1; fi
if [ "$1" = -H ]; then
  shift 2
  echo "$1" >>"$TMP_USER_COMMANDS"
  shift
  /bin/bash -c 'cd "$1" && shift && exec "$@"' bash "$OHAS_TARGET_HOME" "$@"
  exit $?
fi
case "$*" in
  -v) exit 0 ;;
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
chmod +x "$TMP/bin/id" "$TMP/bin/sudo" "$TMP/bin/pbcopy" "$OHAS_TARGET_HOME/.local/bin/uv"
export PATH="$TMP/bin:$PATH"

# Source the actual functions without dispatching a CLI command. This lets the
# test point the privileged plist at a temporary file instead of /Library.
source <(sed '/^\[ \$# -ge 1 \]/,$d' "$HERE/oh-agent-server.sh")
PLIST="$TMP/agent-server.plist"
touch "$PLIST"
wait_healthy() { return 0; }
write_config 127.0.0.1 18100 old-key encryption-key

[ "$(cmd_key)" = old-key ]
[ "$(cmd_key --copy)" = '==> Session API key copied to clipboard' ]
[ "$(cat "$OHAS_CLIPBOARD")" = old-key ]
if (cmd_key --unknown >/dev/null 2>&1); then exit 1; fi

output="$(cmd_key --reset)"
load_config
[ "$OH_SESSION_API_KEYS_0" != old-key ]
[ "${#OH_SESSION_API_KEYS_0}" -eq 64 ]
[ "$OH_SECRET_KEY" = encryption-key ]
[ "$OHAS_HOST:$OHAS_PORT" = 127.0.0.1:18100 ]
[ "${output##*$'\n'}" = "$OH_SESSION_API_KEYS_0" ]
[ ! -f "$OHAS_HOME/restarts" ]

before="$OH_SESSION_API_KEYS_0"
touch "$OHAS_HOME/loaded"
output="$(cmd_key --copy --reset)"
load_config
[ "$OH_SESSION_API_KEYS_0" != "$before" ]
[ "$(cat "$OHAS_CLIPBOARD")" = "$OH_SESSION_API_KEYS_0" ]
[ "$(cat "$OHAS_HOME/restarts")" = restart ]
[ "${output##*$'\n'}" = '==> Session API key copied to clipboard' ]

output="$(cmd_start)"
[[ "$output" == *'Agent Server: http://127.0.0.1:18100'* ]]
[[ "$output" == *'/v1/'* && "$output" == *'/sockets/'* && "$output" == *'key [--copy] [--reset]'* ]]
output="$(cmd_restart)"
[[ "$output" == *'/openapi.json'* && "$output" == *'/ready'* ]]
[ "$(wc -l <"$OHAS_HOME/restarts" | tr -d ' ')" -eq 2 ]

before="$OH_SESSION_API_KEYS_0"
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
cmd_install --user agenttest --host 127.0.0.1 >"$TMP/install-output" 2>&1
grep -Fq 'WARNING: Anyone who can reach this server and has the X-Session-API-Key can execute arbitrary shell commands as agenttest.' "$TMP/install-output"
grep -Fq 'Keep the key secret' "$TMP/install-output"
[ "$(tail -n 2 "$TMP/install-output" | head -n 1)" = 'WARNING: Anyone who can reach this server and has the X-Session-API-Key can execute arbitrary shell commands as agenttest.' ]
[ "$(tail -n 1 "$TMP/install-output")" = '         Keep the key secret; use --host 127.0.0.1 for local-only access.' ]
[ "$(plutil -extract UserName raw -o - "$PLIST")" = agenttest ]
[ "$(plutil -extract EnvironmentVariables.HOME raw -o - "$PLIST")" = "$OHAS_TARGET_HOME" ]
[ "$(plutil -extract WorkingDirectory raw -o - "$PLIST")" = "$OHAS_HOME/data" ]
[ "$(stat -f '%Sp' "$OHAS_HOME")" = drwx------ ]
[ "$(stat -f '%Sp' "$OHAS_HOME/config.env")" = -rw------- ]
[ "$(stat -f '%Sp' "$OHAS_HOME/run-server.sh")" = -rwxr-xr-x ]
[ "$(grep -c '^agenttest$' "$TMP_USER_COMMANDS")" -gt 0 ]
load_config
before="$OH_SESSION_API_KEYS_0"
[ -n "$before" ]

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
