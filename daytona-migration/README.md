# Migrating from Daytona to OpenHands

A concept-first guide for teams moving off [Daytona](https://www.daytona.io/docs/)
onto OpenHands. It maps Daytona's primitives to OpenHands', then shows two
migration scenarios end to end with runnable scripts.

If you only skim one thing, read [Mental model](#mental-model-one-api-vs-two)
below — the single biggest shift is that OpenHands has **two API layers**, and
every OpenHands sandbox already has an **agent running inside it**.

## Mental model: one API vs. two

Daytona is essentially one control-plane API (`app.daytona.io/api`) plus a
per-sandbox Toolbox API (`proxy.app.daytona.io/toolbox/{id}`) that you drive to
run code and manage files. You bring the agent logic.

OpenHands splits into two layers:

| Layer | What it is | Daytona analogue | Base URL | Auth header |
|---|---|---|---|---|
| **Cloud App Server** | Control plane: create/manage sandboxes and *agent conversations* | Daytona **Platform API** | `https://app.all-hands.dev` | `X-Session-API-Key: <OH_API_KEY>` |
| **Agent Server** | Runs *inside every sandbox* (port 60000, reverse-proxied to a public URL). REST + WebSocket for bash, files, git — **and a built-in AI agent** | Daytona **Toolbox API** (but far richer) | `sandbox.exposed_urls[name=="AGENT_SERVER"]` | `X-Session-API-Key: <sandbox session_api_key>` |

The most important gotcha for a Daytona user: **there are two keys.** Your Cloud
`OH_API_KEY` talks to `app.all-hands.dev`; each sandbox returns its own
`session_api_key` (in the sandbox-create response) that you use for every
agent-server call. They are not interchangeable.

## Concept mapping

| Daytona | OpenHands | Notes |
|---|---|---|
| Sandbox | **Sandbox** | Same idea: an isolated computer |
| `Daytona()` client / Platform API | **Cloud App Server** (`app.all-hands.dev`) | Control plane |
| Toolbox API (per-sandbox) | **Agent Server** (`exposed_urls[AGENT_SERVER]`, port 60000) | In-sandbox ops |
| Snapshot | `sandbox_spec` (`GET /api/v1/sandbox-specs`) | Runtime image selection |
| `DAYTONA_API_KEY` | `OH_API_KEY` **plus** per-sandbox `session_api_key` | Two keys now |
| `sandbox.process.exec(cmd)` | `POST /api/bash/execute_bash_command` | Direct equivalent |
| `sandbox.process.code_run(code)` | run via bash, or let the agent do it | No stateless "code_run" |
| `sandbox.fs.*` | `/api/file/upload`, `/api/file/download`, `/api/file/archive` | `?path=` query param |
| `sandbox.git.*` | `/api/git/changes`, `/api/git/diff`, `/api/git/commits` | Git ops |
| `daytona.start/stop` | `POST /api/v1/sandboxes/{id}/pause` / `resume` | Lifecycle |
| `daytona.delete(sandbox)` | `DELETE /api/v1/sandboxes/{id}?sandbox_id=<id>` | Id in path **and** query |
| Region / `Target` (`us`/`eu`) | managed by Cloud | Not user-selectable the same way |
| *(no equivalent)* | **Conversations / the built-in agent** | OpenHands' core value-add |

## The two scenarios

### Scenario 1 — Use the OpenHands agent that ships in every sandbox

The big difference from Daytona: **you don't have to build an agent.** Every
OpenHands sandbox already runs the OpenHands agent, and you can drive it at two
levels:

- **High level — Cloud App Server.** `POST /api/v1/app-conversations` starts a
  conversation on a managed sandbox and the Cloud layer injects your account's
  LLM credentials for you. This is the "just give the agent a task" path.
- **Low level — agent-server.** `POST /api/conversations` on the sandbox's own
  agent-server gives you full control of the `agent`/`llm` block and returns the
  conversation id synchronously. You supply the model + key yourself.

Either way you read the agent's progress the same way: the agent-server
WebSocket at `/sockets/events/{conversation_id}`.

Run it:

```bash
export OH_API_KEY=...        # Cloud API key (required for both levels)
export LLM_API_KEY=...       # model key (required only for the low-level path)
pip install -r requirements.txt
python scenario_1_openhands_agent.py --task "Write a hello-world FastAPI app"
# force the low-level agent-server path:
python scenario_1_openhands_agent.py --mode agent-server --llm-model anthropic/claude-sonnet-4-5
```

`scenario_1_openhands_agent.py` starts a sandbox, opens a conversation (Cloud
high-level by default, agent-server low-level with `--mode agent-server`),
streams `execution_status` over the WebSocket until the agent finishes, and
prints the agent's final response.

### Scenario 2 — Run Claude Code or Codex inside an OpenHands sandbox

You can also use OpenHands sandboxes purely as managed, isolated infrastructure
for **someone else's** coding agent. OpenHands ships an **ACP (Agent Client
Protocol)** integration: instead of the OpenHands agent calling an LLM directly,
it launches an external agent CLI as a subprocess and bridges it into the
conversation. Supported backends (from the `ACP_PROVIDERS` registry in
`openhands-sdk`):

| `acp_server` | Backend | Pre-installed CLI | Credential (secrets channel) |
|---|---|---|---|
| `claude-code` | Claude Code | `claude-agent-acp` | `ANTHROPIC_API_KEY` (or `ANTHROPIC_BASE_URL`) |
| `codex` | OpenAI Codex | `codex-acp` | `OPENAI_API_KEY` (or `OPENAI_BASE_URL`) |
| `gemini-cli` | Gemini CLI | `gemini` | `GEMINI_API_KEY` (or `GEMINI_BASE_URL`) |

These CLIs are already installed in the default OpenHands sandbox image, so you
don't provision anything. You select the backend with an `agent` block whose
`kind` is `ACPAgent` (`acp_server` + optional `acp_model`), and you pass the
provider credential through the **conversation secrets channel** — never the
agent's `llm` field, which ACP ignores.

> Credentials for ACP backends ride the `secrets` channel keyed by the
> provider's env-var name (e.g. `ANTHROPIC_API_KEY`). The subprocess makes its
> own model calls; OpenHands only bridges stdin/stdout.

Run it:

```bash
export OH_API_KEY=...              # Cloud API key
export ANTHROPIC_API_KEY=...       # for --provider claude-code
# or: export OPENAI_API_KEY=...    # for --provider codex
pip install -r requirements.txt
python scenario_2_acp_agent.py --provider claude-code --task "Refactor utils.py"
python scenario_2_acp_agent.py --provider codex --acp-model gpt-5.5 --task "Add tests"
```

`scenario_2_acp_agent.py` starts a sandbox, creates a conversation on the
agent-server with an `ACPAgent` block, injects the provider credential via
`secrets`, and streams the run over the WebSocket. You can switch the ACP model
mid-conversation with `POST /api/conversations/{id}/switch_acp_model`.

## APIs used

### 1. Cloud App Server — manages the sandbox lifecycle

- Base URL: `https://app.all-hands.dev`
- Auth header: `X-Session-API-Key: <OH_API_KEY>`
- Endpoints:
  - `POST /api/v1/sandboxes` — start a sandbox (optional `?sandbox_spec_id=…`)
  - `GET  /api/v1/sandboxes?id=<id>` — batch-get sandboxes by id
    (`SandboxInfo` with `status`, `session_api_key`, `exposed_urls`)
  - `POST /api/v1/app-conversations` — start a conversation on a sandbox
    (Scenario 1 high-level); returns a start task (async)
  - `GET  /api/v1/app-conversations/start-tasks?ids=<id>` — poll for the
    `app_conversation_id`
  - `DELETE /api/v1/sandboxes/{id}?sandbox_id=<id>` — clean up

### 2. Agent Server — runs inside the sandbox

- Base URL: the `exposed_urls` entry with `name == "AGENT_SERVER"`. It listens
  on port 60000 internally and is reverse-proxied to HTTPS — always use the
  `https://…` URL, never the internal port.
- Auth header: `X-Session-API-Key: <session_api_key>` from the sandbox-create
  call (different from your Cloud key).
- Endpoints:
  - `POST /api/bash/execute_bash_command` — run a command (parity path)
  - `POST /api/file/upload?path=…`, `GET /api/file/download?path=…` — files
  - `GET  /api/git/changes?path=…` — git status
  - `POST /api/conversations` — create a conversation directly (Scenario 1
    low-level and Scenario 2). Body needs a `workspace` and an `agent`; an
    `initial_message` starts it running immediately.
  - `POST /api/conversations/{id}/switch_acp_model` — swap the ACP model at
    runtime (Scenario 2); body `{ "model": "…" }`
  - `GET  /api/conversations/{id}/agent_final_response` — the agent's answer
  - `GET  /sockets/events/{conversation_id}` — WebSocket event stream.
    Authenticate with a first frame `{"type":"auth","session_api_key":"…"}`.
    Add `?resend_mode=all` to replay events since the conversation started.

> Both OpenAPI schemas are self-describing. Fetch the Cloud spec at
> `https://app.all-hands.dev/openapi.json`, and the Agent Server spec at
> `<agent_server_url>/openapi.json` once a sandbox is `RUNNING`.

## Gotchas for Daytona users

- **Two keys.** Cloud `OH_API_KEY` vs. per-sandbox `session_api_key`.
- **Never hit port 60000 directly** — use the proxied `exposed_urls` URL.
- **Conversation start is async at the Cloud level** — `POST /app-conversations`
  returns a start task you poll; the agent-server `POST /api/conversations` is
  synchronous.
- **No stateless `code_run`.** Use `/api/bash/*`, or hand the work to the agent.
- **ACP credentials go through `secrets`, not `llm`.** For Scenario 2, set
  `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` in the request's
  `secrets` map — the ACP subprocess reads them from its environment.
- **Agent-server webhooks are disabled on Cloud** (`GET /api/init` → 404). Use
  the WebSocket (with `?resend_mode=all`) or an in-sandbox Stop hook instead.

## See also

- [start-sandbox](../start-sandbox/) — the minimal create-sandbox + bash flow
  this guide's parity mapping builds on.
- [react-to-state-websocket](../react-to-state-websocket/) — the two
  conversation-creation approaches (Cloud-attach vs. agent-direct) and the
  WebSocket state-watching pattern reused here.
- [per-conversation-secrets](../per-conversation-secrets/) — injecting secrets
  into a conversation (the same channel Scenario 2 uses for ACP credentials).
- [`daytona_side_by_side.py`](./daytona_side_by_side.py) — a call-for-call
  reference mapping Daytona SDK calls to OpenHands HTTP requests.
