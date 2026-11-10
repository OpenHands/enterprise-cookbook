# macOS Agent Canvas local backend

Work in progress, GitHub-only (no `example.yaml`), and not yet tested on macOS.

`oh-agent-server.sh` installs the OpenHands agent-server from
[OpenHands/software-agent-sdk](https://github.com/OpenHands/software-agent-sdk) as a launchd
LaunchDaemon on macOS. The service runs as your user, listens on port `18100` on all interfaces, and
starts at boot. Use it as a local (host process) backend for Agent Canvas.

```bash
./oh-agent-server.sh install [--port N] [--host H] [--ref GIT_REF]
./oh-agent-server.sh start | stop | restart | status
./oh-agent-server.sh logs [-f]
./oh-agent-server.sh key     # session API key for the X-Session-API-Key header
./oh-agent-server.sh remove [--purge] [--yes]
```

Run it as your normal user. It calls `sudo` only for `/Library/LaunchDaemons` and `launchctl`.

## Notes

- Install directory: `~/.openhands-agent-server` (venv, `config.env` with the keys, logs, data).
  Needs `uv`.
- `install` defaults to the latest PyPI release. `--ref` installs a branch, tag, or commit from
  GitHub. Re-running `install` upgrades and keeps the keys, port, and host.
- `stop` also disables the service at boot until you run `start`.
- The service runs agents with your user's permissions and no container boundary. `/api/*` needs the
  session key. `/docs`, `/server_info`, `/health`, `/ready`, and `/alive` do not.
- Because it binds `0.0.0.0`, `install` and `status` warn when the macOS application firewall is on.
- Add it to Agent Canvas under Manage Backends with `http://<host>:18100` and the session key.
  Conversations run on the host unless `OH_CONVERSATION_RUNTIME=docker` is set or Settings -> Runtime
  selects Docker.

## Open items

- Verify `launchctl` and `socketfilterfw` behavior on macOS.
- Confirm the `socketfilterfw --getglobalstate` output format and whether `--getappblocked` is usable.

This was created by an AI agent (OpenHands) on behalf of jpshackelford.
