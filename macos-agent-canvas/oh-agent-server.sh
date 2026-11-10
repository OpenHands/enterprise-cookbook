#!/bin/bash
# Install and manage the OpenHands agent-server (OpenHands/software-agent-sdk)
# as a macOS launchd system service (LaunchDaemon) that:
#   - starts at boot, before anyone logs in
#   - runs as the installing user, or the account selected with --user
#   - listens on port 18100 by default
#
# Run this script as your normal user. It uses sudo for launchd and, when
# --user selects another account, for that account's install files.
#
# Compatible with the stock macOS bash 3.2.

set -uo pipefail

# Remove the supplied key from the environment before invoking any child process.
if [ "${OHAS_SESSION_API_KEY+x}" = x ]; then export -n OHAS_SESSION_API_KEY; fi

LABEL="dev.openhands.agent-server"
PLIST="/Library/LaunchDaemons/${LABEL}.plist"

SERVICE_USER="$(id -un)"
SERVICE_GROUP="$(id -gn)"
SERVICE_HOME="$HOME"
SERVICE_PATH="$PATH"

set_root() {
  ROOT="$1"
  VENV="$ROOT/venv"
  DATA="$ROOT/data"
  LOGS="$ROOT/logs"
  CONFIG="$ROOT/config.env"
  WRAPPER="$ROOT/run-server.sh"
}
set_root "${OHAS_HOME:-$HOME/.openhands-agent-server}"

DEFAULT_PORT=18100
DEFAULT_HOST=0.0.0.0
GIT_URL="https://github.com/OpenHands/software-agent-sdk"
PACKAGES="openhands-sdk openhands-tools openhands-workspace openhands-agent-server"

die()  { echo "error: $*" >&2; exit 1; }
info() { echo "==> $*"; }

usage() {
  cat <<EOF
Usage: $(basename "$0") <command> [options]

Commands:
  install [--port N] [--host H] [--ref GIT_REF] [--user USER]
                  Install agent-server into $ROOT and register the service.
                  --user runs it as USER (default: existing service user,
                  or your account on a first install).
                  Re-running upgrades the packages and keeps your keys unless
                  OHAS_SESSION_API_KEY supplies a replacement session key.
                  Default: latest release from PyPI. --ref installs that
                  branch/tag/commit of $GIT_URL instead.
  remove [--purge] [--yes]
                  Stop and unregister the service. --purge also deletes
                  $ROOT (venv, config, keys, conversations).
  start           Start the service (and re-enable it at boot).
  stop            Stop the service and keep it from starting at boot until
                  you run 'start' again.
  restart         Restart the service.
  status          Show install state, launchd state, PID and /health result.
  logs [-f]       Show the last log lines (-f to follow).
  key [--copy] [--reset]
                  Display the X-Session-API-Key, or copy it to the clipboard.
                  --reset rotates the key without displaying it and restarts
                  the service (sudo required); combine with --copy to copy it.

Environment:
  OHAS_HOME             Install directory (default: ~/.openhands-agent-server)
  OHAS_SESSION_API_KEY  Optional key for install or key --reset (>=32 characters;
                        letters, digits, . _ ~ + / = -). Not used by plain key.
EOF
}

[ "$(uname -s)" = "Darwin" ] || die "this script only supports macOS"
[ "$(id -u)" -ne 0 ] || die "run as your normal user, not root; sudo is used where needed"

select_service_user() {
  local entry
  case "$1" in ''|[-.]*|*[!a-zA-Z0-9_.-]*) die "invalid user: $1" ;; esac
  entry="$(id -P "$1" 2>/dev/null)" || die "user not found: $1"
  SERVICE_USER="$1"
  SERVICE_GROUP="$(id -gn "$1")"
  SERVICE_HOME="$(printf '%s\n' "$entry" | cut -d: -f9)"
  [ -d "$SERVICE_HOME" ] || die "user home directory not found: $SERVICE_HOME"
  if [ "$SERVICE_USER" != "$(id -un)" ]; then
    SERVICE_PATH="$SERVICE_HOME/.local/bin:$SERVICE_HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  fi
}

as_service_user() {
  if [ "$SERVICE_USER" = "$(id -un)" ]; then
    "$@"
  else
    sudo -H -u "$SERVICE_USER" /bin/bash -c 'cd "$1" && shift && exec "$@"' bash "$SERVICE_HOME" "$@"
  fi
}

select_installed_service() {
  local user workdir
  [ -f "$PLIST" ] || return 0
  user="$(plutil -extract UserName raw -o - "$PLIST" 2>/dev/null)" || die "cannot read service user from $PLIST"
  select_service_user "$user"
  if [ -n "${OHAS_HOME:-}" ]; then
    set_root "$OHAS_HOME"
  else
    workdir="$(plutil -extract WorkingDirectory raw -o - "$PLIST" 2>/dev/null)" || die "cannot read install directory from $PLIST"
    case "$workdir" in */data) set_root "${workdir%/data}" ;; *) die "invalid service working directory: $workdir" ;; esac
  fi
}

installed() { [ -f "$PLIST" ] && as_service_user test -f "$CONFIG"; }
loaded()    { sudo launchctl print "system/$LABEL" >/dev/null 2>&1; }
require_installed() { installed || die "not installed; run: $0 install"; }

load_config() {
  local line value contents
  if [ "$SERVICE_USER" = "$(id -un)" ]; then
    # shellcheck disable=SC1090
    . "$CONFIG"
    return
  fi
  contents="$(as_service_user cat "$CONFIG")" || die "cannot read $CONFIG as $SERVICE_USER"
  while IFS= read -r line; do
    value="${line#*=}"
    value="${value#\'}"
    value="${value%\'}"
    case "$line" in
      OHAS_HOST=*) OHAS_HOST="$value" ;;
      OHAS_PORT=*) OHAS_PORT="$value" ;;
      OH_SESSION_API_KEYS_0=*) OH_SESSION_API_KEYS_0="$value" ;;
      OH_SECRET_KEY=*) OH_SECRET_KEY="$value" ;;
      OHAS_PATH=*) OHAS_PATH="$value" ;;
    esac
  done <<< "$contents"
  [ -n "${OH_SESSION_API_KEYS_0:-}" ] || die "missing session API key in $CONFIG"
}

health_url() {
  local host="$OHAS_HOST"
  [ "$host" = "0.0.0.0" ] && host=127.0.0.1
  echo "http://$host:$OHAS_PORT/ready"
}

show_network_urls() {
  local urls
  urls="$(ifconfig -a 2>/dev/null | awk -v port="$OHAS_PORT" '
    function emit(    i) {
      if (active && name !~ /^(lo[0-9]*|awdl[0-9]*|llw[0-9]*)$/) {
        for (i = 1; i <= count; i++) {
          printf "    %s: http://%s:%s\n", name, address[i], port
        }
      }
    }
    /^[^[:space:]]+: flags=/ {
      emit(); name = $1; sub(/:$/, "", name); count = 0
      active = ($0 ~ /<UP,[^>]*RUNNING/)
      next
    }
    $1 == "inet" && $2 !~ /^(127\.|169\.254\.)/ && $2 != "0.0.0.0" {
      address[++count] = $2
    }
    $1 == "status:" { active = ($2 == "active") }
    END { emit() }
  ')"
  if [ -n "$urls" ]; then
    echo
    echo "  Local network URLs (firewall and routing permitting):"
    printf '%s\n' "$urls"
  fi
}

show_server_summary() {
  local host="$OHAS_HOST"
  [ "$host" = "0.0.0.0" ] && host=localhost
  cat <<EOF

  Agent Server: http://$host:$OHAS_PORT
  Listening on: $OHAS_HOST:$OHAS_PORT
  Runs as: $SERVICE_USER (home: $SERVICE_HOME)
  Stores files in: $ROOT
  Starts at boot
EOF
  [ "$OHAS_HOST" != "0.0.0.0" ] || show_network_urls
  cat <<EOF

  Routes:
    /api/*          Agent server API (X-Session-API-Key required)
    /v1/*           OpenAI-compatible API (X-Session-API-Key required)
    /sockets/*      Conversation WebSockets (X-Session-API-Key required)
    /openapi.json   OpenAPI specification
    /docs           Interactive API docs
    /redoc          ReDoc API docs
    /server_info    Server information
    /health         Health check
    /ready          Readiness check
    /alive          Liveness check

  Commands:
    $0 status | start | stop | restart
    $0 logs [-f]
    $0 key [--copy] [--reset]
    $0 install [--port N] [--host H] [--ref GIT_REF] [--user USER]
    $0 remove [--purge] [--yes]
EOF
}

wait_healthy() {
  local i
  for i in $(seq 1 60); do
    curl -fsS --max-time 2 "$(health_url)" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

find_uv() {
  local c
  for c in "$SERVICE_HOME/.local/bin/uv" "$SERVICE_HOME/.cargo/bin/uv" \
           /opt/homebrew/bin/uv /usr/local/bin/uv; do
    as_service_user test -x "$c" && { echo "$c"; return 0; }
  done
  if [ "$SERVICE_USER" = "$(id -un)" ]; then
    c="$(command -v uv 2>/dev/null)"
    [ -n "$c" ] && [ -x "$c" ] && { echo "$c"; return 0; }
  fi
  return 1
}

write_wrapper() {
  cat <<'EOF' | as_service_user /bin/bash -c 'cat >"$1" && chmod 755 "$1"' bash "$WRAPPER" || die "could not write $WRAPPER"
#!/bin/bash
# Generated by oh-agent-server.sh. launchd runs this as the selected user.
ROOT="$(cd "$(dirname "$0")" && pwd)"
set -a
. "$ROOT/config.env"
set +a
export PATH="$OHAS_PATH"
cd "$ROOT/data" || exit 1
exec "$ROOT/venv/bin/python" -m openhands.agent_server --host "$OHAS_HOST" --port "$OHAS_PORT"
EOF
}

write_config() {
  local host="$1" port="$2" session_key="$3" secret_key="$4"
  cat <<EOF | as_service_user /bin/bash -c 'umask 077; cat >"$1" && chmod 600 "$1"' bash "$CONFIG" || die "could not write $CONFIG"
OHAS_HOST='$host'
OHAS_PORT='$port'
OH_SESSION_API_KEYS_0='$session_key'
OH_SECRET_KEY='$secret_key'
OHAS_PATH='$SERVICE_PATH'
EOF
}

validate_session_key_override() {
  [ "${#OHAS_SESSION_API_KEY}" -ge 32 ] || die "OHAS_SESSION_API_KEY must be at least 32 characters"
  case "$OHAS_SESSION_API_KEY" in
    *[!a-zA-Z0-9._~+/=-]*) die "OHAS_SESSION_API_KEY may only contain letters, digits, . _ ~ + / = -" ;;
  esac
  export -n OHAS_SESSION_API_KEY
}

warn_if_firewall_blocks() {
  local sfw=/usr/libexec/ApplicationFirewall/socketfilterfw
  [ "$OHAS_HOST" = "127.0.0.1" ] && return 0
  [ -x "$sfw" ] || return 0

  local state py
  state="$("$sfw" --getglobalstate 2>/dev/null)"
  case "$state" in
    *"enabled"*|*"State = 1"*|*"State = 2"*) ;;
    *) return 0 ;;
  esac

  py="$(as_service_user "$VENV/bin/python" -c 'import os,sys;print(os.path.realpath(sys.executable))' 2>/dev/null)"
  echo "warning: the macOS application firewall is ON. Incoming connections from other machines" >&2
  echo "         to port $OHAS_PORT may be blocked, and a boot-time daemon cannot show the approval prompt." >&2
  echo "         To allow them:" >&2
  echo "           sudo $sfw --add '$py'" >&2
  echo "           sudo $sfw --unblockapp '$py'" >&2
  echo "         Or use System Settings > Network > Firewall > Options." >&2
}

write_plist() {
  local tmp
  tmp="$(mktemp)"
  cat >"$tmp" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>UserName</key><string>$SERVICE_USER</string>
  <key>GroupName</key><string>$SERVICE_GROUP</string>
  <key>ProgramArguments</key>
  <array><string>$WRAPPER</string></array>
  <key>WorkingDirectory</key><string>$DATA</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>$SERVICE_HOME</string>
    <key>USER</key><string>$SERVICE_USER</string>
    <key>LOGNAME</key><string>$SERVICE_USER</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOGS/agent-server.log</string>
  <key>StandardErrorPath</key><string>$LOGS/agent-server.log</string>
</dict>
</plist>
EOF
  plutil -lint "$tmp" >/dev/null || { rm -f "$tmp"; die "generated plist is invalid"; }
  PLIST_CHANGED=0
  if [ ! -f "$PLIST" ] || ! cmp -s "$tmp" "$PLIST"; then
    sudo install -m 644 -o root -g wheel "$tmp" "$PLIST" || { rm -f "$tmp"; die "could not write $PLIST"; }
    PLIST_CHANGED=1
  fi
  rm -f "$tmp"
}

bootstrap_service() {
  local attempt
  for attempt in 1 2 3 4 5; do
    sudo launchctl bootstrap system "$PLIST" && return 0
    [ "$attempt" -eq 5 ] && break
    sleep 1
  done
  die "launchctl bootstrap failed; check launchd logs for $LABEL"
}

cmd_install() {
  local port="" host="" ref="" user="" existing_user=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --port) port="${2:?--port needs a value}"; shift 2 ;;
      --host) host="${2:?--host needs a value}"; shift 2 ;;
      --ref)  ref="${2:?--ref needs a value}"; shift 2 ;;
      --user) user="${2:?--user needs a value}"; shift 2 ;;
      *) die "unknown option for install: $1" ;;
    esac
  done
  if [ "${OHAS_SESSION_API_KEY+x}" = x ]; then validate_session_key_override; fi

  if [ -f "$PLIST" ]; then
    existing_user="$(plutil -extract UserName raw -o - "$PLIST" 2>/dev/null)" || die "cannot read service user from $PLIST"
  fi
  if [ -n "$user" ] && [ "$existing_user" != "$user" ]; then
    select_service_user "$user"
    [ -n "${OHAS_HOME:-}" ] || set_root "$SERVICE_HOME/.openhands-agent-server"
    [ -z "$existing_user" ] || info "Changing service user from $existing_user to $user; old files are not migrated"
  else
    select_installed_service
  fi
  case "$ROOT" in /*) ;; *) die "OHAS_HOME must be an absolute path" ;; esac
  if printf '%s\n' "$ROOT" "$SERVICE_HOME" "$SERVICE_PATH" | grep -q "[<>&']"; then
    die "user home, OHAS_HOME, and PATH cannot contain XML metacharacters or apostrophes"
  fi
  [ "$SERVICE_USER" = "$(id -un)" ] || sudo -v || die "sudo authorization required for --user"

  local session_key="" secret_key=""
  if as_service_user test -f "$CONFIG"; then
    # Keep keys, port and host stable across upgrades unless overridden.
    load_config
    prev_host="$OHAS_HOST"; prev_port="$OHAS_PORT"
    session_key="$OH_SESSION_API_KEYS_0"; secret_key="$OH_SECRET_KEY"
    host="${host:-$prev_host}"
    port="${port:-$prev_port}"
  fi
  host="${host:-$DEFAULT_HOST}"
  port="${port:-$DEFAULT_PORT}"
  case "$port" in ''|*[!0-9]*) die "invalid port: $port" ;; esac
  if [ "${OHAS_SESSION_API_KEY+x}" = x ]; then session_key="$OHAS_SESSION_API_KEY"; fi
  [ -n "$session_key" ] || session_key="$(openssl rand -hex 32)"
  [ -n "$secret_key" ]  || secret_key="$(openssl rand -hex 32)"

  local uv
  uv="$(find_uv)" || die "uv not found for $SERVICE_USER. Install it for that user or via brew."

  info "Preparing $ROOT for $SERVICE_USER"
  as_service_user mkdir -p "$ROOT" "$DATA" "$LOGS" || die "$SERVICE_USER cannot create $ROOT"
  as_service_user chmod 700 "$ROOT" || die "$SERVICE_USER cannot write $ROOT"

  if ! as_service_user test -x "$VENV/bin/python"; then
    info "Creating Python 3.12 virtualenv"
    as_service_user "$uv" venv --python 3.12 "$VENV" || die "could not create virtualenv"
  fi

  local specs="" p
  if [ -n "$ref" ]; then
    info "Installing agent-server from $GIT_URL @ $ref"
    for p in $PACKAGES; do
      specs="$specs ${p}@git+${GIT_URL}@${ref}#subdirectory=${p}"
    done
  else
    info "Installing latest agent-server release from PyPI"
    specs="$PACKAGES"
  fi
  # shellcheck disable=SC2086
  as_service_user "$uv" pip install --python "$VENV/bin/python" -U $specs || die "package install failed"

  write_config "$host" "$port" "$session_key" "$secret_key"
  write_wrapper

  info "Checking $PLIST (sudo required if changed)"
  write_plist
  if loaded && [ "$PLIST_CHANGED" -eq 0 ]; then
    info "Service already loaded; restarting to apply package or config changes"
    sudo launchctl enable "system/$LABEL" || die "launchctl enable failed"
    sudo launchctl kickstart -k "system/$LABEL" || die "launchctl restart failed"
  else
    if loaded; then
      info "Service already loaded; replacing launchd registration"
      sudo launchctl bootout "system/$LABEL" || die "launchctl bootout failed"
    fi
    sudo launchctl enable "system/$LABEL" || die "launchctl enable failed"
    bootstrap_service
  fi

  load_config
  info "Waiting for $(health_url)"
  if wait_healthy; then
    info "Installed and running"
  else
    echo "warning: service did not become healthy within 60s; check: $0 logs" >&2
  fi
  show_server_summary
  echo
  echo
  echo "  Session API key:"
  echo "    Copy without displaying:   $0 key --copy"
  echo "    Display in terminal:       $0 key"
  echo "    Rotate without displaying: $0 key --reset"
  echo "    Custom key: Set OHAS_SESSION_API_KEY for install or key --reset."
  echo "                Requires 32+ chars: A-Z a-z 0-9 . _ ~ + / = -"
  warn_if_firewall_blocks
  echo >&2
  echo >&2
  printf 'WARNING: Anyone who can reach this server and has the X-Session-API-Key can execute arbitrary shell commands as %s.\n' "$SERVICE_USER" >&2
  echo '         Keep the key secret; use --host 127.0.0.1 for local-only access.' >&2
}

cmd_start() {
  require_installed
  load_config
  sudo launchctl enable "system/$LABEL"
  if loaded; then
    sudo launchctl kickstart "system/$LABEL" || die "start failed"
  else
    sudo launchctl bootstrap system "$PLIST" || die "launchctl bootstrap failed"
  fi
  info "Waiting for $(health_url)"
  wait_healthy || die "started but not healthy after 60s; check: $0 logs"
  info "Started"
  show_server_summary
}

cmd_stop() {
  require_installed
  sudo launchctl disable "system/$LABEL"
  if loaded; then
    sudo launchctl bootout "system/$LABEL" || die "stop failed"
  fi
  info "Stopped (will stay stopped across reboots until 'start')"
}

cmd_restart() {
  require_installed
  load_config
  sudo launchctl enable "system/$LABEL"
  if loaded; then
    sudo launchctl kickstart -k "system/$LABEL" || die "restart failed"
  else
    sudo launchctl bootstrap system "$PLIST" || die "launchctl bootstrap failed"
  fi
  info "Waiting for $(health_url)"
  wait_healthy || die "restarted but not healthy after 60s; check: $0 logs"
  info "Restarted"
  show_server_summary
}

cmd_remove() {
  local purge=0 yes=0 real_root real_home
  while [ $# -gt 0 ]; do
    case "$1" in
      --purge) purge=1 ;;
      --yes|-y) yes=1 ;;
      *) die "unknown option for remove: $1" ;;
    esac
    shift
  done

  if [ "$purge" -eq 1 ]; then
    case "$ROOT" in /*) ;; *) die "OHAS_HOME must be an absolute path" ;; esac
    case "$ROOT" in /|"$SERVICE_HOME"|"$HOME") die "refusing to purge a home directory or /" ;; esac
    if as_service_user test -d "$ROOT"; then
      real_root="$(as_service_user /bin/bash -c 'cd "$1" && pwd -P' bash "$ROOT")" || die "cannot inspect $ROOT"
      real_home="$(as_service_user /bin/bash -c 'cd "$1" && pwd -P' bash "$SERVICE_HOME")" || die "cannot inspect $SERVICE_HOME"
      case "$real_root" in /|"$real_home"|"$HOME") die "refusing to purge a home directory or /" ;; esac
    fi
  fi
  if [ "$purge" -eq 1 ] && [ "$yes" -eq 0 ]; then
    printf 'This deletes %s, including conversations and keys. Continue? [y/N] ' "$ROOT"
    read -r reply
    case "$reply" in y|Y|yes) ;; *) die "aborted" ;; esac
  fi

  sudo -v || die "sudo authorization required to remove the service"
  if loaded; then
    info "Stopping service"
    sudo launchctl bootout "system/$LABEL" || die "could not stop $LABEL; installation left intact"
  fi
  sudo launchctl enable "system/$LABEL" >/dev/null 2>&1 || die "could not reset launchd state for $LABEL"
  if [ -f "$PLIST" ] || [ -L "$PLIST" ]; then
    info "Removing $PLIST"
    sudo rm -f "$PLIST" || die "could not remove $PLIST; installation left intact"
    [ ! -e "$PLIST" ] && [ ! -L "$PLIST" ] || die "could not remove $PLIST; installation left intact"
  fi
  if [ "$purge" -eq 1 ]; then
    info "Deleting $ROOT"
    as_service_user rm -rf "$ROOT" || die "could not delete $ROOT"
  else
    info "Kept $ROOT (use --purge to delete it)"
  fi
  info "Removed"
}

cmd_status() {
  if ! installed; then
    echo "installed: no"
    exit 3
  fi
  load_config
  echo "installed: yes ($PLIST)"
  echo "runs as: $SERVICE_USER (home: $SERVICE_HOME)"
  echo "install dir: $ROOT"
  echo "listen: $OHAS_HOST:$OHAS_PORT"
  [ "$OHAS_HOST" != "0.0.0.0" ] || show_network_urls
  warn_if_firewall_blocks
  if sudo launchctl print-disabled system 2>/dev/null | grep -q "\"$LABEL\" => disabled"; then
    echo "boot start: disabled (stopped by user)"
  else
    echo "boot start: enabled"
  fi
  local out state pid version base_url
  base_url="$(health_url)"; base_url="${base_url%/ready}"
  if out="$(sudo launchctl print "system/$LABEL" 2>/dev/null)"; then
    state="$(echo "$out" | sed -n 's/^[[:space:]]*state = //p' | head -1)"
    pid="$(echo "$out" | sed -n 's/^[[:space:]]*pid = //p' | head -1)"
    echo "launchd: loaded, state=${state:-unknown}${pid:+, pid=$pid}"
  else
    echo "launchd: not loaded"
  fi
  if curl -fsS --max-time 3 "$(health_url)" >/dev/null 2>&1; then
    echo "ready: OK ($(health_url))"
    version="$(curl -fsS --max-time 3 "$base_url/server_info" 2>/dev/null | sed -n 's/.*"version":"\([^"]*\)".*/\1/p')"
    [ -n "$version" ] && echo "version: $version"
  else
    echo "ready: unreachable ($(health_url))"
    exit 1
  fi
}

cmd_logs() {
  as_service_user test -f "$LOGS/agent-server.log" || die "no log file yet at $LOGS/agent-server.log"
  if [ "${1:-}" = "-f" ]; then
    as_service_user tail -n 100 -f "$LOGS/agent-server.log"
  else
    as_service_user tail -n 100 "$LOGS/agent-server.log"
  fi
}

cmd_key() {
  local copy=0 reset=0 was_loaded=0 session_key
  while [ $# -gt 0 ]; do
    case "$1" in
      --copy) copy=1 ;;
      --reset) reset=1 ;;
      *) die "unknown option for key: $1" ;;
    esac
    shift
  done
  as_service_user test -f "$CONFIG" || die "not installed; run: $0 install"
  [ "$copy" -eq 0 ] || command -v pbcopy >/dev/null 2>&1 || die "pbcopy not found"
  load_config
  if [ "$reset" -eq 1 ]; then
    if [ "${OHAS_SESSION_API_KEY+x}" = x ]; then
      validate_session_key_override
      [ "$OHAS_SESSION_API_KEY" != "$OH_SESSION_API_KEYS_0" ] || die "new session API key must differ from the current key"
    fi
    require_installed
    sudo -v || die "sudo authorization required to restart the service"
    loaded && was_loaded=1
    session_key="${OHAS_SESSION_API_KEY:-$(openssl rand -hex 32)}"
    write_config "$OHAS_HOST" "$OHAS_PORT" "$session_key" "$OH_SECRET_KEY"
    load_config
    if [ "$was_loaded" -eq 1 ]; then
      sudo launchctl kickstart -k "system/$LABEL" || die "key updated but restart failed; run: $0 restart"
      wait_healthy || die "key updated but service is not ready; check: $0 logs"
      info "Session API key rotated; existing clients must use the new key"
    else
      info "Session API key rotated; service is stopped. Run: $0 start"
    fi
  fi
  if [ "$copy" -eq 1 ]; then
    printf '%s' "$OH_SESSION_API_KEYS_0" | pbcopy || die "could not copy session API key"
    info "Session API key copied to clipboard"
  elif [ "$reset" -eq 0 ]; then
    echo "$OH_SESSION_API_KEYS_0"
  fi
}

[ $# -ge 1 ] || { usage; exit 1; }
cmd="$1"; shift
case "$cmd" in install|-h|--help|help) ;; *) select_installed_service ;; esac
case "$cmd" in
  install) cmd_install "$@" ;;
  remove|uninstall) cmd_remove "$@" ;;
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_restart ;;
  status)  cmd_status ;;
  logs)    cmd_logs "$@" ;;
  key)     cmd_key ;;
  -h|--help|help) usage ;;
  *) usage; exit 1 ;;
esac
