# Disabling Skills for Every Conversation on an Account

Keep specific OpenHands skills out of every conversation an account starts,
using the Cloud **Settings API** — the same endpoint the UI's *Settings →
Skills* page toggles. Once installed, the deny-list applies to every
`POST /api/v1/app-conversations` call that account makes, without the
caller having to pass anything on each request.

`POST /api/v1/settings` with `{"disabled_skills": [...]}` persists an
**account-level** deny-list. On every conversation start the App Server
computes `effective_disabled_skills = member ∪ profile ∪ request` and drops
matching skill names from the `<SKILLS>` block that ships in the first
`SystemPromptEvent`. A skill disabled at any level stays off.

Feature shipped in OpenHands Enterprise **1.62.0**
([enterprise#335](https://github.com/OpenHands/enterprise/pull/335)) and is
also live on OpenHands Cloud.

## When to use this

Prefer the **account-level** setting for anything policy-shaped:

- The team should never see a certain skill (e.g. no external git
  integrations on an internal build; no `docker` when your sandbox base
  image can't run nested Docker).
- The deny-list must be enforced consistently — you can't accidentally
  start a conversation without it because you forgot the field.
- You don't want callers of the API to have to remember to include
  `disabled_skills` on every request.

Reach for the **per-request** field (`disabled_skills` on
`POST /api/v1/app-conversations`) only as an escape hatch:

- One-off runs that need extra skills off, on top of the account default.
- Ad-hoc experiments where you're trying deny-lists without touching the
  persisted setting.

## Precedence

`effective_disabled_skills` (from
`openhands/app_server/app_conversation/live_status_app_conversation_service.py`)
takes the union of three sources, order-preserving, de-duplicated:

| Level | Where it lives | How to set it |
|---|---|---|
| Account (member) | `user.disabled_skills` | `POST /api/v1/settings` with `disabled_skills: [...]` — this tutorial |
| Launched Agent Profile | `agent_context.disabled_skills` on the resolved profile | `POST /api/v1/settings/profiles/{name}` |
| Per-request | `disabled_skills` on the start request | `POST /api/v1/app-conversations` with `disabled_skills: [...]` |

A name absent from the discovered skill catalog is a **harmless no-op** —
you can safely include speculative names without failing the request. Names
are matched exactly (case-sensitive) against the `<name>` values returned by
`GET /api/v1/skills/search?q=<prefix>` (a bare call with no `q` returns
microagents mixed in; pass a prefix like `?q=github` to see the skill names
that appear in the `<SKILLS>` block).

## Prerequisites

```bash
pip install requests
export OH_API_KEY="your-api-key"   # Cloud: Profile → API Keys.
                                   # OHE: Settings → API Keys on your instance.
```

The account whose API key you use is the one whose `disabled_skills`
setting the script mutates and then restores.

## Run

```bash
# default: 'no-git-integrations' gallery, account-level only
python disabled_skills.py

# pick a different gallery
python disabled_skills.py --gallery no-docker
python disabled_skills.py --gallery no-github-automations

# explicit skill names (overrides --gallery)
python disabled_skills.py --disable docker kubernetes pdflatex

# also start a second conversation with additional per-request skills
# to demonstrate account ∪ request:
python disabled_skills.py --per-request

# target an OpenHands Enterprise instance:
python disabled_skills.py --base-url https://your-ohe.example.com

# keep conversations for inspection (account setting is still restored):
python disabled_skills.py --keep
```

The script:

1. Reads and remembers the current account-level `disabled_skills`.
2. Installs the requested deny-list via `POST /api/v1/settings`.
3. Starts a conversation with **no** `disabled_skills` on the request,
   proving the account-level setting alone keeps the skills out.
4. Parses `<SKILLS>` in `dynamic_context.text` and asserts every disabled
   name is absent.
5. Optionally repeats with per-request `disabled_skills` to show union.
6. Deletes the conversation(s) and sandbox(es), and restores the original
   account-level setting — the beta instance is left as it was found.

Exit status is non-zero if any verification fails.

### Real output

Captured against `https://app.all-hands.dev` with `--per-request`:

```
=== install account-level deny-list ===
  disabling: ['github', 'gitlab', 'bitbucket', 'bitbucket-cloud', 'bitbucket-data-center', 'azure-devops']
  previous account setting: []
  new account setting:      ['github', 'gitlab', 'bitbucket', 'bitbucket-cloud', 'bitbucket-data-center', 'azure-devops']

=== conversation 1: no per-request disabled_skills ===
  start-task status: WAITING_FOR_SANDBOX
  start-task status: WAITING_FOR_SANDBOX
  start-task status: WAITING_FOR_SANDBOX
  start-task status: WAITING_FOR_SANDBOX
  start-task status: WAITING_FOR_SANDBOX
  start-task status: SETTING_UP_SKILLS
  start-task status: SETTING_UP_SKILLS
  start-task status: STARTING_CONVERSATION
  start-task status: STARTING_CONVERSATION
  start-task status: READY
  conversation: 445d3864b33f4ad594522abddee5169b sandbox: 3sHSpWbMEFoSvEzl9g7NyN
  account-only: loaded 103 skills
    - github                                   absent
    - gitlab                                   absent
    - bitbucket                                absent
    - bitbucket-cloud                          absent
    - bitbucket-data-center                    absent
    - azure-devops                             absent
  PASS (account-only): all denied skills absent from SystemPromptEvent

=== conversation 2: per-request disabled_skills = ['flarglebargle'] (union should be ['github', 'gitlab', 'bitbucket', 'bitbucket-cloud', 'bitbucket-data-center', 'azure-devops', 'flarglebargle']) ===
  start-task status: WAITING_FOR_SANDBOX
  start-task status: WAITING_FOR_SANDBOX
  start-task status: WAITING_FOR_SANDBOX
  start-task status: WAITING_FOR_SANDBOX
  start-task status: WAITING_FOR_SANDBOX
  start-task status: SETTING_UP_SKILLS
  start-task status: SETTING_UP_SKILLS
  start-task status: STARTING_CONVERSATION
  start-task status: READY
  conversation: f73fc9ad970a4cb88f1030851f47f8a9 sandbox: 2V2xzlr0wGG6oRLLho59jf
  account + request: loaded 102 skills
    - github                                   absent
    - gitlab                                   absent
    - bitbucket                                absent
    - bitbucket-cloud                          absent
    - bitbucket-data-center                    absent
    - azure-devops                             absent
    - flarglebargle                            absent
  PASS (account + request): all denied skills absent from SystemPromptEvent

=== cleanup ===
  deleted conversation 445d3864b33f4ad594522abddee5169b
  deleted sandbox 3sHSpWbMEFoSvEzl9g7NyN
  deleted conversation f73fc9ad970a4cb88f1030851f47f8a9
  deleted sandbox 2V2xzlr0wGG6oRLLho59jf

=== restore original account-level setting ===
  restored account setting: []
```

Conversation 1 loaded 103 skills; conversation 2 loaded 102 (the extra
`flarglebargle` denial removed one). Every denied name is absent from
the `<SKILLS>` block of the `SystemPromptEvent`.

## How it works

### 1. Install the account-level deny-list

```python
requests.post(
    f"{base_url}/api/v1/settings",
    headers={"Authorization": f"Bearer {api_key}"},
    json={"disabled_skills": ["github", "gitlab", "bitbucket"]},
)
```

This is a partial settings save — the server deep-merges, so sending just
this one field leaves every other setting untouched. It's the exact call
the UI's *Settings → Skills* page makes via
`useSkillMutations.saveDisabledSkills` in
[`frontend/src/hooks/mutation/use-skill-mutations.ts`](https://github.com/OpenHands/enterprise/blob/main/frontend/src/hooks/mutation/use-skill-mutations.ts).

One footgun: the server treats a `null` payload as "no change" (it copies
the previously-persisted list forward). To clear the deny-list, send `[]`.
`effective_disabled_skills` treats `None` and `[]` identically, so the
observable behaviour is the same.

### 2. Start a conversation with nothing on the request

```python
requests.post(
    f"{base_url}/api/v1/app-conversations",
    headers=headers,
    json={
        "initial_message": {
            "role": "user",
            "content": [{"type": "text", "text": "Say hello."}],
        },
        "title": "no per-request disabled_skills",
    },
)
```

No `disabled_skills` field is sent — the account-level setting is doing
all the work.

### 3. Verify from the `SystemPromptEvent`

The `SystemPromptEvent` is the first event in every conversation. Its
`dynamic_context.text` contains a `<SKILLS>...</SKILLS>` block listing
every skill the agent can invoke:

```xml
<SKILLS>
The following skills are available. ...
<available_skills>
  <skill>
    <name>github-resilience</name>
    <description>...</description>
  </skill>
  ...
</available_skills>
</SKILLS>
```

The script parses out every `<name>` and asserts none of the denied names
are present:

```python
event = requests.get(
    f"{base_url}/api/v1/conversation/{conv_id}/events/search",
    headers=headers,
    params={"kind__eq": "SystemPromptEvent", "limit": 1},
).json()["items"][0]

skills_block = re.search(
    r"<SKILLS>(.*?)</SKILLS>",
    event["dynamic_context"]["text"],
    re.DOTALL,
).group(1)
loaded = re.findall(r"<name>([^<]+)</name>", skills_block)

assert set(disabled_names).isdisjoint(loaded)
```

See the [`custom-system-prompt`](../custom-system-prompt/) tutorial for
the full shape of `SystemPromptEvent`.

### 4. Cleanup and restore

```python
requests.delete(f"{base_url}/api/v1/app-conversations/{conv_id}", headers=headers)
requests.delete(
    f"{base_url}/api/v1/sandboxes/{sandbox_id}",
    headers=headers,
    params={"sandbox_id": sandbox_id},
)
requests.post(
    f"{base_url}/api/v1/settings",
    headers=headers,
    json={"disabled_skills": original or []},
)
```

The script always restores the original account setting, even on error
(via `try / finally`), so a failed run never leaves the account with a
stuck deny-list.

## Deny-list gallery

Common recipes, all exposed through `--gallery`:

### `no-git-integrations`

Drop every git-hosting integration skill. Useful for internal-only
environments or when you're deliberately keeping the agent off customer
repos.

```python
[
    "github",
    "gitlab",
    "bitbucket",
    "bitbucket-cloud",
    "bitbucket-data-center",
    "azure-devops",
]
```

### `no-browser`

Turn off skills whose primary value is browsing/preview flows. (The
browser *tool* itself is off via the agent config, not this deny-list;
see [`../custom-agent-no-browser/`](../custom-agent-no-browser/).)

```python
["frontend-design", "vercel"]
```

### `no-docker`

Keep the agent out of Docker and Kubernetes skills — appropriate when
your sandbox base image doesn't support nested Docker.

```python
["docker", "kubernetes"]
```

### `no-github-automations`

Keep the general `github` skill available but hide the automation
recipes (workflows, monitors, watchdogs) if they're not for this team.

```python
[
    "github-actions",
    "github-agents-md-maintainer",
    "github-delivery-watchdog",
    "github-issue-to-pr",
    "github-issue-triage",
    "github-pr-review",
    "github-pr-reviewer",
    "github-repo-monitor",
    "github-stale-ci-pr-closer",
]
```

Roll your own with `--disable name1 name2 name3`. Skill names come from
`GET /api/v1/skills/search?q=<prefix>`.

## Per-request escape hatch

Set `disabled_skills` on the start request to *extend* the deny-list for
a single conversation:

```python
requests.post(
    f"{base_url}/api/v1/app-conversations",
    headers=headers,
    json={
        "disabled_skills": ["flarglebargle"],
        "initial_message": {
            "role": "user",
            "content": [{"type": "text", "text": "Say hello."}],
        },
        "title": "one-off deny-list bump",
    },
)
```

The three levels union — you can't *re-enable* an account- or
profile-level denial by omitting it here. If the account has `[github]`
and the request sends `[docker]`, the effective deny-list is
`[github, docker]`.

## Endpoints used

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/settings` | GET | Read current account `disabled_skills` (for restore) |
| `/api/v1/settings` | POST | Install / clear the account-level deny-list |
| `/api/v1/app-conversations` | POST | Start the conversation (`disabled_skills` optional) |
| `/api/v1/app-conversations/start-tasks` | GET | Poll the start task for `app_conversation_id` |
| `/api/v1/conversation/{id}/events/search` | GET | Read `SystemPromptEvent` to verify |
| `/api/v1/app-conversations/{id}` | DELETE | Delete the conversation |
| `/api/v1/sandboxes/{id}?sandbox_id={id}` | DELETE | Delete the sandbox |
| `/api/v1/skills/search?q=<prefix>` | GET | Discover valid skill names for the deny-list |

All calls use `Authorization: Bearer <OH_API_KEY>`.

## Feature availability

- OpenHands Cloud (currently deployed)
- OpenHands Enterprise **1.62.0+**
  ([enterprise#335](https://github.com/OpenHands/enterprise/pull/335))

To verify on a specific deployment, look for `disabled_skills` in both the
`GETSettingsModel` and the `AppConversationStartRequest-Input` schemas:

```bash
curl -s https://your-deployment/openapi.json | jq '
  .components.schemas.GETSettingsModel.properties.disabled_skills,
  .components.schemas["AppConversationStartRequest-Input"].properties.disabled_skills
'
```

To confirm the endpoint you're calling is the same one the UI uses,
grep the enterprise frontend:

```bash
gh repo clone OpenHands/enterprise
grep -rn "disabled_skills" enterprise/frontend/src/hooks/mutation/
# -> use-skill-mutations.ts calls SettingsService.saveSettings({ disabled_skills })
# -> settings-service.api.ts posts to /api/v1/settings
```

## Related examples

- [`../custom-system-prompt/`](../custom-system-prompt/) — sibling
  tutorial covering the other half of enterprise#335: replacing the
  static system prompt.
- [`../custom-agent-no-browser/`](../custom-agent-no-browser/) — turn off
  the `browser` *tool* (a different customisation axis; deny-list here
  operates on *skills*).
- [`../load-plugin/`](../load-plugin/) — start a conversation with a
  plugin pre-loaded, using the same start-task polling flow.

## Related documentation

- [OpenHands Enterprise PR #335](https://github.com/OpenHands/enterprise/pull/335)
- [OpenHands SDK — Agent Settings](https://docs.openhands.dev/sdk/guides/agent-settings)
