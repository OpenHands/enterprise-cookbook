# Enterprise Cookbook Style Guide

How to write an example and its README so it works on GitHub and renders well as
a page on docs.openhands.dev. For how changes are reviewed and published, see
[CONTRIBUTING.md](./CONTRIBUTING.md).

[`command-blacklist`](./command-blacklist/) is the reference example. When this
guide and an existing example disagree, follow this guide.

## Writing Style

Follow the docs site's
[Documentation Style Guide](https://github.com/OpenHands/docs/blob/main/openhands/DOC_STYLE_GUIDE.md).
In short:

- Use **Title Case** for first- and second-level headings (`#`, `##`).
- Keep sentences short. Start with the simplest way to run the example, then add
  options and variations.
- Use bullet lists for options and numbered lists for steps that must happen in
  order.
- Use tabs when there is more than one way to do the same thing, and notes or
  warnings for asides. In a README you write these as the GitHub Markdown
  described in [Docs Components](#docs-components).

The rest of this guide covers what is specific to cookbook examples.

## Example Layout

```text
my-example/
├── README.md          # what it does and how to run it
├── example.yaml       # publishes it to docs.openhands.dev (optional)
├── my_example.py      # the script the README runs
└── my-plugin/         # any plugin, skill, or config the example loads
```

- Name the directory in kebab-case after what the example shows, not how it is
  built: `conversation-tags`, not `tags-api-script`.
- Keep the example self-contained. Link to another example rather than importing
  from it.
- Put tests in `tests/` only when the example is a reusable package; see
  [`conversation-metrics`](./conversation-metrics/).

## README Structure

```markdown
# Command Blacklist

One or two paragraphs: what the example does and when you would use it.

## How It Works

## Prerequisites

## Run It

## <Topic Sections>

## APIs Used

## Related
```

| Section | Required | What goes in it |
|---|---|---|
| `#` title | Yes | The example's name in Title Case. On the docs site it is replaced by the `title` in `example.yaml`, so make them match. |
| Introduction | Yes | What the example does and why you'd use it, before any heading. On the docs site it follows the `description` from `example.yaml`, so don't repeat that sentence. |
| How It Works | Yes | The flow, in a few sentences or a diagram. Use a mermaid sequence diagram when several parties are involved. |
| Prerequisites | When needed | Anything beyond an OpenHands API key and `pip install requests`, such as a sandbox spec, a GitHub token, or another example's plugin. |
| Run It | Yes | The shortest command that runs the example, then what it prints, then options. Use tabs for alternative ways to run it. |
| Topic sections | Optional | Explanations specific to the example, such as "The Hook", "Tag Rules", or "Plugin Structure". |
| APIs Used | When it calls the API | A table of the endpoints the example calls and what for. |
| Related | Yes | Links to related examples and docs pages, as cards. |

Use these exact section names, so readers find the same thing in the same place
in every example. Avoid "Usage" or "Try It" for Run It, and "Related Examples" or
"Related Documentation" for Related.

## Docs Components

Write ordinary GitHub Markdown. Headings, lists, tables, links, images, and code
blocks pass through to the docs site unchanged. The constructs below also become
[Mintlify components](https://mintlify.com/docs/components) on the docs site,
while still reading well on GitHub.

| In the README | On the docs site | Use it for |
|---|---|---|
| `> [!NOTE]` | `<Note>` | Context that helps but isn't essential |
| `> [!TIP]` | `<Tip>` | A shortcut or a better way to do something |
| `> [!IMPORTANT]` | `<Info>` | Something the reader must know to succeed |
| `> [!WARNING]`, `> [!CAUTION]` | `<Warning>` | Something that can fail or cause damage |
| `<details><summary>Title</summary> … </details>` | `<Accordion>` | Background that most readers can skip, such as "Why inline?" |
| A `mermaid` code block | A diagram | Flows between parties; replace ASCII diagrams with these |
| `<!-- docs:tabs -->` … `<!-- /docs:tabs -->` | `<Tabs>`, one per heading inside | Alternative ways to do the same thing |
| `<!-- docs:steps -->` … `<!-- /docs:steps -->` | `<Steps>`, one per heading inside | A procedure whose steps each need explanation |
| `<!-- docs:cards -->` around a link list | `<CardGroup>` | The Related section |
| `<!-- docs:github-only -->` … `<!-- /docs:github-only -->` | Omitted | Content that only makes sense on GitHub |
| A code block that names a file | The full file | See [Code Blocks](#code-blocks) |

Use these only where they fit the content. A plain section is better than tabs
with one tab.

### Alerts

Put the marker on its own line:

```markdown
> [!WARNING]
> The agent may refuse an obviously dangerous command before the hook runs.
```

A plain `>` blockquote stays a blockquote. Use an alert for asides instead.

### Tabs and Steps

Put a heading for each tab or step inside the markers. On GitHub they read as
ordinary sections:

```markdown
<!-- docs:tabs -->

### Load via API

…

### Launch via badge

…

<!-- /docs:tabs -->
```

Leave a blank line before and after each `docs:` comment.

### Cards

Each list item is a link, a ` - `, and a short description:

```markdown
## Related

<!-- docs:cards -->

- [`load-plugin`](../load-plugin/) - Programmatic plugin loading
- [OpenHands Hooks Guide](https://docs.openhands.dev/sdk/guides/hooks) - Full hook documentation

<!-- /docs:cards -->
```

Cards for published examples use that example's icon; other cards get a book or
external-link icon.

## Links and Images

- **Other examples**: link relatively, as `../load-plugin/`. The link points to
  the example's docs page once it is published, and to GitHub until then.
- **Files in this repository**: link relatively, as `./safety-guardian/`. On the
  docs site these go to GitHub at the commit that was rendered.
- **Docs pages**: use the full URL without `.md`, as
  `https://docs.openhands.dev/sdk/guides/hooks`. It becomes a link within the docs
  site.
- **Images**: use a relative path to a file in the example's directory, with alt
  text that describes the image. The docs site loads it from GitHub.

A relative link to something that doesn't exist fails the build.

## Code Blocks

- Always give a language: `bash` for commands, `python`, `json`, `yaml`, and
  `text` for program output.
- Write commands to run from the example's directory, and make them
  copy-pasteable: no `$` prompts, and placeholders in capitals, as `$OH_API_KEY`.
- Follow the command with what it prints, in a `text` block, trimmed to the lines
  that matter.

### Blocks That Show a Real File

When a code block shows a file from the example, name the file after the
language. The path is relative to the example's directory:

````markdown
```json safety-guardian/hooks/hooks.json
{ … }
```
````

The build fails unless the block matches the file exactly, so the README can't
drift from the code. For a long file, add `excerpt` and show only the part that
matters:

````markdown
```json safety-guardian/hooks/hooks.json excerpt
```
````

The docs page always shows the full file, collapsed if it is long.

### Not Supported

Raw HTML other than `<details>` and `<summary>` fails the build, as do unknown or
unclosed `docs:` comments. Errors name the file and line.

## Example Code

Scripts should run with nothing but an API key:

- **Python 3.10 or later**, using the standard library plus `requests`, or
  `websockets` for streaming. Say what to install in the README.
- **A complete demo by default.** With no arguments, the script creates what it
  needs, shows the result, and deletes what it created. A `--keep` flag leaves it
  running for inspection.
- **The standard options.** Read each one from a flag, falling back to an
  environment variable:

  | Flag | Environment variable | Default |
  |---|---|---|
  | `--api-key` | `OH_API_KEY` | Required |
  | `--base-url` | `OH_API_BASE` | `https://app.all-hands.dev` |
  | `--poll-timeout` | `POLL_TIMEOUT` | `180` seconds |
  | `--sandbox-spec-id` | `SANDBOX_SPEC_ID` | The server's default |
  | `--sandbox-id` | `SANDBOX_ID` | Create a new sandbox |
  | `--message` | `INITIAL_MESSAGE` | A short message for the example |

- **Never print secrets**, including API keys, tokens, and secret values the
  example sets.
- **Pass pre-commit**: ruff, pycodestyle, and pyright, as configured in
  [`.pre-commit-config.yaml`](./.pre-commit-config.yaml).

### Launch Badges

For an example that loads a plugin, a badge lets readers launch it in OpenHands
Cloud with one click. Generate the badge with
[`launch-plugin-badge/build_launch_url.py`](./launch-plugin-badge/) rather than
editing the URL by hand. The plugin details are base64-encoded in the URL, so
mistakes are hard to spot:

```bash
python launch-plugin-badge/build_launch_url.py \
  --repo-path command-blacklist/safety-guardian \
  --message "…" \
  --label "Try Safety Guardian"
```

The source must be `github:OpenHands/enterprise-cookbook` (the default) and the
ref `main`.

## example.yaml

```yaml
title: Command Blacklist
description: Block known-dangerous shell commands with a PreToolUse hook bundled in a plugin.
category: Guardrails
icon: shield-halved
```

| Field | Required | Shown as | Guidance |
|---|---|---|---|
| `title` | Yes | Page title, sidebar entry, and overview card | Title Case, two to four words, matching the README's `#` heading |
| `description` | Yes | Subtitle under the page title, and overview card text | One sentence saying what the example does. Start with a verb, not "This example". |
| `category` | Yes | Sidebar group and overview section | One of the categories in [`cookbook.yaml`](./cookbook.yaml) |
| `icon` | No | Sidebar and card icon | A [Font Awesome](https://fontawesome.com/search?ic=free) icon name that no other example in the category uses |

Other keys fail the build.

## Upleveling Checklist

Use this when bringing an existing example up to this guide. One example per pull
request.

**Required to publish**

- [ ] `example.yaml` added, following [example.yaml](#exampleyaml).
- [ ] `npm run check` in `tools/docs-render` passes.
- [ ] `#` and `##` headings are in Title Case, and the `#` heading matches `title`.
- [ ] Sections follow [README Structure](#readme-structure), with the standard
      names.
- [ ] Links to docs pages use full URLs without `.md`, and links to other
      examples are relative.
- [ ] Launch badges are regenerated with `build_launch_url.py`, with the source
      `github:OpenHands/enterprise-cookbook` and ref `main`.
- [ ] No leftover references to old branches or the former repository name
      (`jpshackelford/oh-examples`).

**Polish**

- [ ] Asides are alerts, not plain blockquotes or "Note:" paragraphs.
- [ ] ASCII diagrams are replaced with mermaid diagrams.
- [ ] Alternative ways to run the example are tabs.
- [ ] Optional background is in a `<details>` block.
- [ ] Code blocks that show a real file name that file.
- [ ] Related links are cards.

**Code**

- [ ] The script runs with only an API key, and cleans up unless `--keep` is
      given.
- [ ] Flags and environment variables follow [Example Code](#example-code); for
      example, `OH_API_BASE` rather than `OH_API_URL`.
- [ ] The docs preview looks right. Check each changed page from the preview
      comment.
