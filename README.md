# enterprise-cookbook

A collection of example code for working with the OpenHands API.

## Examples

Examples are grouped by topic. Some examples span more than one topic and are
listed in every section that fits, so a given example may appear more than once.

Jump to a section:

- [Sandbox lifecycle](#sandbox-lifecycle)
- [Conversation monitoring & reacting](#conversation-monitoring--reacting)
- [Secrets & authentication](#secrets--authentication)
- [Plugins, skills & MCP](#plugins-skills--mcp)
- [Custom agents & tools](#custom-agents--tools)
- [Guardrails](#guardrails)
- [Hooks](#hooks)

### Sandbox lifecycle

Create, attach to, and tear down sandboxes (runtimes).

| Example | Description |
|---------|-------------|
| [start-sandbox](./start-sandbox/) | Start a sandbox (no conversation) and run commands via the agent-server REST API |
| [clone-and-attach](./clone-and-attach/) | Clone a repo + run `.openhands/setup.sh` in a sandbox, then attach a conversation to it |
| [archive-sandbox](./archive-sandbox/) | Archive/delete a conversation to release its **Persistent Volume Claim (PVC)** and free up storage resources |
| [warm-sandbox-pool](./warm-sandbox-pool/) | Keep a pool of pre-initialized "warm" sandboxes that late-bind to conversations on demand, taking sandbox startup and initialization out of the end user's wait |

### Conversation monitoring & reacting

Observe conversations and react to their state.

| Example | Description |
|---------|-------------|
| [conversation-metrics](./conversation-metrics/) | CLI tool to retrieve cost and token usage for conversations |
| [conversation-tags](./conversation-tags/) | Attach arbitrary key-value **metadata** to a conversation via `tags` (e.g. an external `environment_url`) and read it back from `AppConversation.tags` |
| [react-to-state-websocket](./react-to-state-websocket/) | React to conversation `execution_status` changes over the agent-server **WebSocket** (`/sockets/events/{id}`) instead of polling — two approaches (Cloud-attach vs. agent-direct) with trade-offs, plus in-sandbox hook alternatives |
| [watch-terminal-state](./watch-terminal-state/) | Deep dive on [react-to-state-websocket](./react-to-state-websocket/): detect a **confirmed terminal `execution_status`** over the WebSocket — handles both event shapes, treats `finished` as advisory (Stop-hook revertible), and uses log-safe first-message auth |
| [server-info-idle](./server-info-idle/) | Poll `GET /server_info.idle_time` — the **same idle signal `runtime-api` uses to reap sandboxes** — to detect when the workspace has gone quiet (Cloud-first; `--local` fallback) |
| [finish-callback](./finish-callback/) | Notify an external URL the moment a conversation finishes with a **Stop hook** (push instead of poll); includes a local receiver server to prove the end-to-end flow |

### Secrets & authentication

Inject credentials and manage identity.

| Example | Description |
|---------|-------------|
| [per-conversation-secrets](./per-conversation-secrets/) | Inject per-conversation secrets via REST API — both as bash env vars and to template an **MCP server** config (`.mcp.json`) bundled in a plugin |
| [service-account-github-pat](./service-account-github-pat/) | Use one OpenHands SaaS account as a **service account**, overriding the managed `GITHUB_TOKEN` with each user's GitHub PAT per conversation |
| [gpg-commit-signing](./gpg-commit-signing/) | Configure **GPG commit signing** on **every** conversation (not just when a repo is selected) with a `SessionStart` hook that imports a key from a custom secret |

### Plugins, skills & MCP

Extend conversations with plugins, skills, and MCP servers.

| Example | Description |
|---------|-------------|
| [load-plugin](./load-plugin/) | Minimal: start a conversation with a plugin pre-loaded via the REST API |
| [launch-plugin-badge](./launch-plugin-badge/) | Build a no-code `/launch` link, HTML button, or README badge that loads a plugin |
| [upload-skills](./upload-skills/) | Upload a local agent-skills directory into a sandbox, then start a conversation that uses them |
| [test-mcp-config](./test-mcp-config/) | Validate **MCP server configs** (connection/auth) against a sandbox's agent-server via `POST /api/mcp/test`, before using them in a conversation |
| [per-conversation-secrets](./per-conversation-secrets/) | Template an **MCP server** config (`.mcp.json`) bundled in a plugin using per-conversation secrets injected via REST API (also listed under Secrets & authentication) |

### Custom agents & tools

Configure the agent and add custom tools.

| Example | Description |
|---------|-------------|
| [custom-agent-no-browser](./custom-agent-no-browser/) | Configure agent tools via the agent-server API (excludes the browser tool) |
| [custom-agent-with-tool](./custom-agent-with-tool/) | Add custom server-side tools via source file upload + tool_module_qualnames |
| [custom-agent-with-pip-tool](./custom-agent-with-pip-tool/) | Load a custom tool from a published pip package (pip install --target + tool_module_qualnames) |
| [custom-system-prompt](./custom-system-prompt/) | Override the default OpenHands system prompt with a custom one for specialized agents (e.g., research assistant, code reviewer) |
| [disabled-skills](./disabled-skills/) | Persist an account-level deny-list of skills so every conversation starts with them off (same call the UI Skills-Settings page makes) |

### Guardrails

Constrain what the agent can do with PreToolUse hooks.

| Example | Description |
|---------|-------------|
| [command-blacklist](./command-blacklist/) | Block dangerous shell commands with PreToolUse hooks (blacklist approach with snarky messages) |
| [command-whitelist](./command-whitelist/) | Only allow approved shell commands with PreToolUse hooks (whitelist approach for strict security) |
| [workspace-isolation](./workspace-isolation/) | **Advanced:** Enforce directory boundaries with hooks - prevent agents from navigating/writing outside assigned workspace (based on jpshackelford/lxa) |

### Hooks

Examples that use agent hooks, grouped here by hook type. Each is also listed
under its primary topic above.

| Example | Hook | Description |
|---------|------|-------------|
| [command-blacklist](./command-blacklist/) | PreToolUse | Block dangerous shell commands (blacklist approach with snarky messages) |
| [command-whitelist](./command-whitelist/) | PreToolUse | Only allow approved shell commands (whitelist approach for strict security) |
| [workspace-isolation](./workspace-isolation/) | PreToolUse | **Advanced:** Enforce directory boundaries — prevent agents from navigating/writing outside assigned workspace |
| [gpg-commit-signing](./gpg-commit-signing/) | SessionStart | Import a GPG key from a custom secret to sign commits on **every** conversation |
| [finish-callback](./finish-callback/) | Stop | Notify an external URL the moment a conversation finishes (push instead of poll) |

## Published on docs.openhands.dev

Examples with an `example.yaml` are also published as pages in the
[Enterprise Cookbook](https://docs.openhands.dev/cookbook) tab of the OpenHands docs. Pull
requests get a preview link for those pages before merge. See
[CONTRIBUTING.md](./CONTRIBUTING.md) for how changes are published and
[STYLEGUIDE.md](./STYLEGUIDE.md) for how to write an example.

## API Versions

OpenHands has two API versions:

- **V0 API** (Legacy) - Deprecated since v1.0.0, scheduled for removal April 1, 2026
- **V1 API** - Current recommended API

These examples aim to support both API versions where possible, with graceful fallback behavior.

## Getting Started

Each example has its own README with installation and usage instructions.

### Authentication

Most examples require an OpenHands API key. Set it as an environment variable:

```bash
export OH_API_KEY="your-api-key"
```

Or pass it via command-line argument (see individual example documentation).

## Related Resources

- [OpenHands Documentation](https://docs.openhands.dev/)
- [OpenHands API Reference](https://app.all-hands.dev/docs)
- [oh-websocket-example](https://github.com/jpshackelford/oh-websocket-example) - V0 WebSocket API example

## License

MIT License - see [LICENSE](LICENSE) for details.
