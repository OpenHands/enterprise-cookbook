# Launch Plugin Badge

This example builds on [`load-plugin`](../load-plugin/). That one calls the API
with your key to start a conversation with a plugin loaded. Here we make a
**no-code launch link** anyone can click — perfect for an HTML `<button>` or a
Markdown badge in a plugin's README.

Like `load-plugin`, this example is **self-contained**: it ships its own
[`dad-joke/`](./dad-joke/) plugin and the launch links load it straight from
this repo on GitHub.

The link points at the OpenHands frontend `/launch` route:

```
https://app.all-hands.dev/launch?plugins=<BASE64>&message=<URL-ENCODED>
```

When opened, the frontend decodes `plugins`, shows a confirmation modal
(pre-filling any parameter fields), and on submit calls
`POST /api/v1/app-conversations` — **the exact call `load-plugin` makes by
hand.** The user supplies their own auth by being logged in, so the link
contains no secrets.

> **Official docs:** [Plugin Launcher](https://docs.openhands.dev/openhands/usage/cloud/plugin-launcher)
> is the reference for the `/launch` route — the `plugins`/`message` params,
> how `parameters` become editable inputs, and a simpler unencoded format for
> development. This example is a runnable companion to that page.
>
> Full end-to-end trace (marketplace → directory → frontend → app server → SDK):
> [Plugin Launch Flow design doc](https://github.com/OpenHands/OpenHands/blob/main/enterprise/doc/design-doc/plugin-launch-flow.md).

## Try it

These are the actual badges this example generates — click one to launch a
conversation with the bundled [`dad-joke`](./dad-joke/) plugin:

[![Tell a dad joke](https://img.shields.io/badge/Tell%20a%20dad%20joke-blue)](https://app.all-hands.dev/launch?plugins=W3sic291cmNlIjogImdpdGh1YjpPcGVuSGFuZHMvZW50ZXJwcmlzZS1jb29rYm9vayIsICJyZWYiOiAibWFpbiIsICJyZXBvX3BhdGgiOiAibGF1bmNoLXBsdWdpbi1iYWRnZS9kYWQtam9rZSIsICJwYXJhbWV0ZXJzIjogeyJhbmltYWwiOiAiZHVjayJ9fV0%3D&message=%2Fdad-joke%3Aabout)
&nbsp;
[![Open with dad-joke loaded](https://img.shields.io/badge/Open%20with%20dad--joke%20loaded-blue)](https://app.all-hands.dev/launch?plugins=W3sic291cmNlIjogImdpdGh1YjpPcGVuSGFuZHMvZW50ZXJwcmlzZS1jb29rYm9vayIsICJyZWYiOiAibWFpbiIsICJyZXBvX3BhdGgiOiAibGF1bmNoLXBsdWdpbi1iYWRnZS9kYWQtam9rZSJ9XQ%3D%3D)

- **Tell a dad joke** — runs `/dad-joke:about` immediately ([variant 1](#1-run-a-skill-on-launch--entry-command)).
- **Open with dad-joke loaded** — loads the plugin and waits for your prompt ([variant 2](#2-just-load-the-plugin--user-prompts-after)).

> The badges fetch the plugin from this repo's **default branch**, so they work
> once this example is merged to `main`. Testing from a branch? Regenerate them
> with `--ref your-branch` (see below).

## Run it

```bash
python build_launch_url.py
```

With no arguments it prints two fully worked examples (below). Pass flags to
build your own:

```bash
python build_launch_url.py \
    --source github:OpenHands/enterprise-cookbook \
    --repo-path launch-plugin-badge/dad-joke \
    --message "/dad-joke:about" \
    --param animal="duck" \
    --label "Tell a dad joke"
```

No API key needed — this only *constructs* URLs.

## Walkthrough: encoding the launch URL

The whole trick is turning a list of plugin specs into one URL-safe query
parameter. Three steps (`encode_plugins` in [`build_launch_url.py`](./build_launch_url.py)):

```python
import base64, json

plugins = [
    {
        "source": "github:OpenHands/enterprise-cookbook",
        "ref": "main",
        "repo_path": "launch-plugin-badge/dad-joke",
        "parameters": {"animal": "duck"},
    }
]

raw     = json.dumps(plugins)                      # 1. list[dict] -> JSON text
encoded = base64.b64encode(raw.encode("utf-8"))    # 2. UTF-8 bytes -> base64
plugins_param = encoded.decode("ascii")            # 3. bytes -> str for the URL
```

Then assemble the URL, URL-escaping each query value (`build_launch_url`):

```python
from urllib.parse import quote

url = (
    "https://app.all-hands.dev/launch"
    f"?plugins={quote(plugins_param, safe='')}"
    f"&message={quote('/dad-joke:about', safe='')}"
)
```

Notes:

- **Default `json.dumps` separators** (`", "` / `": "`) are kept, matching the
  encoding the OpenHands plugin directory uses.
- **URL-escape the base64.** Standard base64 can contain `+`, `/`, and `=`,
  which are unsafe in a query string. `quote(..., safe="")` turns the `=`
  padding into `%3D`, etc. The frontend reverses this automatically.
- It's reversible — `decode_plugins()` (base64-decode → `json.loads`) gets you
  back the original list. The script asserts this round-trip on every run.

### The plugin spec fields

| Field | Meaning | Required |
|-------|---------|----------|
| `source` | Where the plugin lives (`github:owner/repo`) | yes |
| `ref` | Git ref/branch/tag | recommended |
| `repo_path` | Plugin sub-directory within the source | if not repo root |
| `parameters` | **Default** values to pre-fill the launch modal's form | no |

`parameters` are only *defaults for the form*. The user can edit them in the
modal before starting; the app server then formats the final values into the
conversation's first message. (The SDK's `PluginSource` itself has no
`parameters` field — see the design doc's "Parameter Journey".)

### Simpler format for quick tests

For local or staging experiments you can skip base64 entirely and pass
unencoded query params — `plugin_source`, `plugin_ref`, `plugin_repo_path`:

```
https://app.all-hands.dev/launch?plugin_source=github:OpenHands/enterprise-cookbook&plugin_ref=main&plugin_repo_path=launch-plugin-badge/dad-joke
```

The encoded `plugins` form is what you want for shareable badges — and it's the
only one that supports multiple plugins (or pre-filled `parameters`) in a single
link. Both formats are documented on the
[Plugin Launcher](https://docs.openhands.dev/openhands/usage/cloud/plugin-launcher)
page.

## Two variants

### 1. Run a skill on launch — entry command

Set `message` to the plugin's entry slash command. The conversation starts and
**immediately runs the skill**. `dad-joke` declares `entry_command: "about"`, so
its command is `/dad-joke:about`, and the `animal` parameter pre-fills the modal:

```python
build_launch_url(
    plugins=[{
        "source": "github:OpenHands/enterprise-cookbook",
        "ref": "main",
        "repo_path": "launch-plugin-badge/dad-joke",
        "parameters": {"animal": "duck"},   # pre-fills the modal
    }],
    message="/dad-joke:about",              # auto-runs after launch
)
```

HTML button:

```html
<a href="https://app.all-hands.dev/launch?plugins=W3sic291cmNlIjogImdpdGh1YjpPcGVuSGFuZHMvZW50ZXJwcmlzZS1jb29rYm9vayIsICJyZWYiOiAibWFpbiIsICJyZXBvX3BhdGgiOiAibGF1bmNoLXBsdWdpbi1iYWRnZS9kYWQtam9rZSIsICJwYXJhbWV0ZXJzIjogeyJhbmltYWwiOiAiZHVjayJ9fV0%3D&message=%2Fdad-joke%3Aabout"><button>Tell a dad joke</button></a>
```

Markdown badge:

```markdown
[![Tell a dad joke](https://img.shields.io/badge/Tell%20a%20dad%20joke-blue)](https://app.all-hands.dev/launch?plugins=W3sic291cmNlIjogImdpdGh1YjpPcGVuSGFuZHMvZW50ZXJwcmlzZS1jb29rYm9vayIsICJyZWYiOiAibWFpbiIsICJyZXBvX3BhdGgiOiAibGF1bmNoLXBsdWdpbi1iYWRnZS9kYWQtam9rZSIsICJwYXJhbWV0ZXJzIjogeyJhbmltYWwiOiAiZHVjayJ9fV0%3D&message=%2Fdad-joke%3Aabout)
```

### 2. Just load the plugin — user prompts after

Omit `message`. The conversation starts with the plugin's skills loaded but
**no first action**, so the user types their own prompt. The `dad-joke` skill is
keyword-triggered: when the user asks for a joke, it asks for their favorite
animal and then delivers.

```python
build_launch_url(
    plugins=[{
        "source": "github:OpenHands/enterprise-cookbook",
        "ref": "main",
        "repo_path": "launch-plugin-badge/dad-joke",
    }],
    # no message -> agent waits for the user
)
```

Markdown badge:

```markdown
[![Open with dad-joke loaded](https://img.shields.io/badge/Open%20with%20dad--joke%20loaded-blue)](https://app.all-hands.dev/launch?plugins=W3sic291cmNlIjogImdpdGh1YjpPcGVuSGFuZHMvZW50ZXJwcmlzZS1jb29rYm9vayIsICJyZWYiOiAibWFpbiIsICJyZXBvX3BhdGgiOiAibGF1bmNoLXBsdWdpbi1iYWRnZS9kYWQtam9rZSJ9XQ%3D%3D)
```

| | Variant 1 (entry command) | Variant 2 (load only) |
|---|---|---|
| `message` query param | `/dad-joke:about` | *(omitted)* |
| On launch | Runs the skill immediately | Waits for the user's prompt |
| Best for | One-click "do the thing" demos | "Open a workspace with X available" |

## Use the functions in your own tooling

`build_launch_url.py` is importable:

```python
from build_launch_url import build_launch_url, html_button, markdown_badge, plugin_spec

url = build_launch_url(
    [plugin_spec("github:owner/repo", "plugins/my-plugin", parameters={"x": "1"})],
    message="/my-plugin:start",
)
print(markdown_badge("Try my plugin", url))
print(html_button("Try my plugin", url))
```

## Related

- [`load-plugin`](../load-plugin/) — the programmatic equivalent (the API call
  this link ultimately triggers).
- [Plugin Launcher](https://docs.openhands.dev/openhands/usage/cloud/plugin-launcher)
  — official docs for the `/launch` route.
- [Plugin Marketplace](https://docs.openhands.dev/enterprise/plugin-marketplace)
  — the **plugin directory**: a browseable catalog (served at `/plugins`, with a
  `/api/plugins` API) that builds launch links like these from a marketplace
  source repo. This example is what that directory does, by hand.
- [Plugins overview](https://docs.openhands.dev/overview/plugins) — what plugins
  are and the format they follow.
- [Plugin Launch Flow design doc](https://github.com/OpenHands/OpenHands/blob/main/enterprise/doc/design-doc/plugin-launch-flow.md)
  — the full marketplace → directory → frontend → app server → SDK journey.
