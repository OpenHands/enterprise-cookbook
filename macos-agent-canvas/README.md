# macOS Agent Canvas local backend

Work in progress, GitHub-only (no `example.yaml`).

`oh-agent-server.sh` installs the OpenHands agent-server from
[OpenHands/software-agent-sdk](https://github.com/OpenHands/software-agent-sdk) as a launchd
LaunchDaemon on macOS. The service runs as the installing user by default (or the account given
with `--user`), listens on port `18100` on all interfaces, and starts at boot. Use it as a local
(host process) backend for Agent Canvas.

```bash
./oh-agent-server.sh install [--port N] [--host H] [--ref GIT_REF] [--user USER]
./oh-agent-server.sh start | stop | restart | status
./oh-agent-server.sh logs [-f]
./oh-agent-server.sh key                  # display the X-Session-API-Key
./oh-agent-server.sh key --copy           # copy without printing it
./oh-agent-server.sh key --reset          # rotate and restart without displaying
./oh-agent-server.sh key --reset --copy   # rotate, restart, and copy without displaying
./oh-agent-server.sh remove [--purge] [--yes]
```

To use your own session key on first install, set `OHAS_SESSION_API_KEY`. Set it to a **different**
value with `key --reset` to rotate, or on `install` to replace a key during an upgrade. Without it,
install generates a key and upgrades retain it; `key --reset` generates a fresh one. For example,
to enter a key without putting its value in shell history:

```bash
read -rsp 'New session API key: ' OHAS_SESSION_API_KEY; echo
export OHAS_SESSION_API_KEY
./oh-agent-server.sh install   # or: ./oh-agent-server.sh key --reset
unset OHAS_SESSION_API_KEY
```

Supplied keys must be at least 32 characters and contain only letters, digits, `. _ ~ + / = -`.
An explicitly empty or unsafe value is rejected before modifying the installation. The key is
not printed during install or rotation, and the script removes it from the environment passed to
child processes. It remains in the key-bearing `config.env` with restricted permissions.

Run it as your normal administrator account, not root. It calls `sudo` for launchd and, with
`--user`, to create files and install packages as the selected account. That account must already
exist, have a writable home directory, and be able to run `uv` (either installed for that account
or through Homebrew). To choose the storage directory as well:

```bash
OHAS_HOME=/path/writable/by/service-account ./oh-agent-server.sh install --user service-account
./oh-agent-server.sh status
```

Without `OHAS_HOME`, the default is `~service-account/.openhands-agent-server`. Subsequent commands
read the user and storage path from the installed launchd plist. Changing `--user` without also
selecting a writable directory creates a new installation under that user's home; existing keys,
conversations, and files are **not** migrated. Reinstalling for the same user and path preserves them.

Install prints routes and commands, then lists how to copy, display, or rotate the session key
without displaying it by default. It ends with a security warning. `start` and `restart` print the
server summary. A key reset invalidates the old key for clients; if the service is stopped, it stays
stopped and uses the new key on the next `start`.

## Notes

- Install directory: the selected user's `~/.openhands-agent-server` by default, or `OHAS_HOME`
  (venv, `config.env` with the keys, logs, data). The installer sets the directory to mode `700`
  and the key-bearing `config.env` to mode `600`, owned by the service user. Neither install nor
  key rotation prints the key; only explicitly running `key` does.
- When listening on `0.0.0.0` (IPv4), summaries and `status` list active non-loopback IPv4
  interface URLs (including VPN addresses). IPv6 URLs are omitted because that listener cannot
  accept them. These are local interface addresses, not a public-IP lookup or a guarantee that
  firewall or network routing permits a connection.
- `install` defaults to the latest PyPI release. `--ref` installs a branch, tag, or commit from
  GitHub. Re-running `install` upgrades and keeps the keys (unless overridden), port, and host.
- `stop` also disables the service at boot until you run `start`.
- `remove` unregisters the service and deletes its launchd plist; `--purge` also deletes the install
  directory. If stopping the service or removing the plist fails, the install directory is kept.
- Anyone who can reach the server and has the `X-Session-API-Key` can execute arbitrary shell commands
  as the selected user; keep the key secret and restrict network access. There is no container boundary.
  `/api/*`, `/v1/*`, and `/sockets/*` need the session key. `/docs`, `/redoc`, `/openapi.json`,
  `/server_info`, `/health`, `/ready`, and `/alive` do not.
- Because it binds `0.0.0.0`, `install` and `status` warn when the macOS application firewall is on.
- Add it to Agent Canvas under Manage Backends with `http://<host>:18100` and the session key.
  Conversations run on the host unless `OH_CONVERSATION_RUNTIME=docker` is set or Settings -> Runtime
  selects Docker.

## Open items

- Verify `launchctl` and `socketfilterfw` behavior on macOS.
- Confirm the `socketfilterfw --getglobalstate` output format and whether `--getappblocked` is usable.

This was created by an AI agent (OpenHands) on behalf of jpshackelford.
