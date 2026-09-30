# Custom System Prompt via App Conversation API

Override the OpenHands system prompt for one conversation with your own text,
using the Cloud **App Conversation API**.

`POST /api/v1/app-conversations` accepts a `system_prompt` field. When set,
it **replaces** the default static system prompt for that conversation. The
per-conversation dynamic context — repo context, loaded skills, custom
secrets, current datetime — is still appended automatically, so your custom
prompt composes with whatever runtime context the sandbox provides.

Feature shipped in OpenHands Enterprise **1.62.0**
([enterprise#335](https://github.com/OpenHands/enterprise/pull/335)) and is
also live on OpenHands Cloud.

## When to use this

- Your agent has a specialised role (research assistant, code reviewer,
  documentation writer) and the default coding-agent framing gets in the way.
- You're building a domain-specific product on top of OpenHands and want to
  own the prompt without asking the agent to *ignore* the default one.
- You need to enforce specific behaviour or persona for a single
  conversation without changing user- or org-level settings.

For long-lived overrides, put the text in an **agent profile** instead;
`system_prompt` is a per-conversation override.

## What gets replaced, and what doesn't

`SystemPromptEvent` is the first event in every conversation and carries
two structured fields:

| Field | Contains | Overridden by `system_prompt`? |
|---|---|---|
| `system_prompt` | The static system prompt (a `{cache_prompt, type, text}` object) | **Yes** — its `.text` becomes your custom prompt verbatim |
| `dynamic_context` | Runtime context: repo context, `<SKILLS>`, `<CUSTOM_SECRETS>`, `<CURRENT_DATETIME>`, host info | No — still appended by the server |

`system_message_suffix` is a *separate* field on the same request and is
appended to the dynamic context; you can set both.

## Prerequisites

```bash
pip install requests
export OH_API_KEY="your-api-key"   # Cloud: Profile → API Keys.
                                   # OHE: Settings → API Keys on your instance.
```

You do **not** need to set LLM credentials — the App Server uses whatever
LLM your account is configured with.

## Run

```bash
python custom_system_prompt.py

# or against an OpenHands Enterprise instance:
python custom_system_prompt.py --base-url https://your-ohe.example.com

# supply your own prompt / message:
python custom_system_prompt.py \
    --prompt "You are Sir Reginald, a pirate. Every reply starts with 'Arrr!'" \
    --message "Say hello."

# keep resources for inspection (no cleanup at the end):
python custom_system_prompt.py --keep
```

The script starts a conversation with `system_prompt` set, fetches the first
`SystemPromptEvent`, asserts the custom text is there, and cleans up.
Exit status is non-zero if verification fails.

### Real output

Captured against an OpenHands Enterprise 1.67.0 instance:

```
=== start conversation ===
  custom prompt: You are a research assistant. Answer questions concisely and accurately. When wr...
  start-task status: WAITING_FOR_SANDBOX
  start-task status: WAITING_FOR_SANDBOX
  start-task status: WAITING_FOR_SANDBOX
  start-task status: WAITING_FOR_SANDBOX
  start-task status: SETTING_UP_SKILLS
  start-task status: SETTING_UP_SKILLS
  start-task status: STARTING_CONVERSATION
  start-task status: STARTING_CONVERSATION
  start-task status: READY
conversation: 7698ab13bb994b3181ea01d23652a2ca
sandbox: 3U13tOWV00GZpo1bhnhMBf

=== verify SystemPromptEvent ===
  system_prompt.text (124 chars): You are a research assistant. Answer questions concisely and accurately. When writing code, focus on clarity and simplic...
  dynamic_context.text (47806 chars) — appended automatically; contains repo/skills/secrets/datetime blocks

  PASS: custom prompt found in SystemPromptEvent

=== cleanup ===
  deleted conversation 7698ab13bb994b3181ea01d23652a2ca
  deleted sandbox 3U13tOWV00GZpo1bhnhMBf
```

## How it works

### 1. Start the conversation

`POST /api/v1/app-conversations` is asynchronous: it returns a
[`AppConversationStartTask`](https://docs.all-hands.dev/) whose `id` is the
**task id**, not the conversation id. Omit `sandbox_id` and the App Server
provisions a fresh sandbox for you.

```python
payload = {
    "system_prompt": "You are a research assistant...",
    "initial_message": {
        "role": "user",
        "content": [{"type": "text", "text": "Say hello."}],
    },
    "title": "custom-system-prompt demo",
}
task = requests.post(
    f"{base_url}/api/v1/app-conversations",
    headers={"Authorization": f"Bearer {api_key}"},
    json=payload,
).json()
task_id = task["id"]
```

### 2. Wait for `app_conversation_id`

Poll `/api/v1/app-conversations/start-tasks?ids=<task_id>` until the task
reaches `READY` and hands back `app_conversation_id` (and, if the server
provisioned one, `sandbox_id`). The endpoint is a "search by ids"
lookup — `ids` is a required, repeatable query parameter and the
response is a JSON array of the matching tasks.

```python
while not conv_id:
    time.sleep(3)
    item = requests.get(
        f"{base_url}/api/v1/app-conversations/start-tasks",
        headers=headers,
        params={"ids": task_id},
    ).json()[0]
    if item["status"] == "ERROR":
        raise RuntimeError(item.get("detail"))
    conv_id = item.get("app_conversation_id")
    sandbox_id = item.get("sandbox_id") or sandbox_id
```

### 3. Verify from the `SystemPromptEvent`

The Cloud events endpoint mirrors the agent-server view, so you can read
the event without needing the sandbox's `session_api_key`.

```python
event = requests.get(
    f"{base_url}/api/v1/conversation/{conv_id}/events/search",
    headers=headers,
    params={"kind__eq": "SystemPromptEvent", "limit": 1},
).json()["items"][0]

assert custom_prompt in event["system_prompt"]["text"]
```

Note the shape: `event["system_prompt"]` is a `{cache_prompt, type, text}`
object — the actual prompt string is at `.text`.

### 4. Cleanup

`DELETE /api/v1/app-conversations/{id}` frees the conversation. Delete the
sandbox separately when you're done with it. The sandbox delete endpoint
is `DELETE /api/v1/sandboxes/{id}` and additionally requires `sandbox_id`
as a query parameter — pass the same id in both places, or the server
returns HTTP 422.

```python
requests.delete(f"{base_url}/api/v1/app-conversations/{conv_id}", headers=headers)
requests.delete(
    f"{base_url}/api/v1/sandboxes/{sandbox_id}",
    headers=headers,
    params={"sandbox_id": sandbox_id},
)
```

## Prompt gallery

Swap `--prompt` (or `SYSTEM_PROMPT`) for any of these to try them out.

### Research assistant

```
You are a research assistant. Answer questions concisely and accurately.
When writing code, focus on clarity and simplicity. Always cite your sources
when making factual claims.
```

### Code reviewer

```
You are a code reviewer. Analyze code for correctness, security, and
maintainability. Provide specific, actionable feedback. Focus on material
issues, not style preferences.
```

### Documentation writer

```
You are a technical documentation writer. Write clear, accurate, and
comprehensive documentation. Use examples to illustrate concepts.
Structure content with clear headings and logical flow.
```

### Data analyst

```
You are a data analyst. Analyze data to extract insights and patterns.
Use appropriate statistical methods and visualizations. Explain your
findings clearly with supporting evidence.
```

## Composition with `system_message_suffix`

`system_message_suffix` is a separate field appended to the dynamic
context. You can use both together:

```python
payload = {
    "system_prompt": "You are a research assistant...",
    "system_message_suffix": "Focus on academic papers published after 2020.",
    ...
}
```

The resulting prompt structure is:

```
[system_prompt.text          — your custom static prompt]

[dynamic_context.text        — repo context, skills, secrets, datetime]
[system_message_suffix       — appended to the dynamic context]
```

## Planning agent (`agent_type=plan`)

When `agent_type=plan` is set alongside `system_prompt`, the custom prompt
still replaces the built-in planning static prompt, but the planning tools
and workflow instructions are kept — the agent keeps its ability to create
and update plans, with your text as the foundation.

## ACP agents

For ACP (Agent Communication Protocol) agents, which delegate to external
CLIs (Claude Code, Gemini CLI, etc.), `system_prompt` is **ignored** with a
server-side warning (`app_conversation_start:system_prompt_ignored_for_acp_agent`).
ACP agents own their own system prompt and cannot be overridden via the REST
API.

## Endpoints used

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/app-conversations` | POST | Start the conversation (returns a start task) |
| `/api/v1/app-conversations/start-tasks` | GET | Poll the start task for `app_conversation_id` |
| `/api/v1/conversation/{id}/events/search` | GET | Read `SystemPromptEvent` to verify |
| `/api/v1/app-conversations/{id}` | DELETE | Delete the conversation |
| `/api/v1/sandboxes/{id}?sandbox_id={id}` | DELETE | Delete the sandbox |

All calls use `Authorization: Bearer <OH_API_KEY>`.

## Feature availability

- OpenHands Cloud (currently deployed)
- OpenHands Enterprise **1.62.0+**
  ([enterprise#335](https://github.com/OpenHands/enterprise/pull/335))

To verify on a specific deployment, look for `system_prompt` in the
`AppConversationStartRequest` schema:

```bash
curl -s https://your-deployment/openapi.json \
  | jq '.components.schemas["AppConversationStartRequest-Input"].properties.system_prompt'
```

## Related examples

- [`../conversation-tags/`](../conversation-tags/) — same App Conversation
  API pattern, showing how to attach and read back arbitrary metadata.
- [`../load-plugin/`](../load-plugin/) — start a conversation with a plugin
  pre-loaded, using the same start-task polling flow.
- [`../custom-agent-no-browser/`](../custom-agent-no-browser/) — configure
  which tools the agent has access to (different customisation axis).

## Related documentation

- [OpenHands SDK — Agent Settings](https://docs.openhands.dev/sdk/guides/agent-settings)
- [OpenHands Enterprise PR #335](https://github.com/OpenHands/enterprise/pull/335)
