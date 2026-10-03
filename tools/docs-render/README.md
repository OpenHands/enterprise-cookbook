# Publishing examples to docs.openhands.dev

Examples in this repository can also appear as pages in the **Cookbook** tab of
[docs.openhands.dev](https://docs.openhands.dev). Each page is generated from the
example's `README.md` by the converter in this directory. The conversion is
deterministic and has no manual step, so the docs page always matches the README.

## Publish an example

Add an `example.yaml` next to the example's `README.md`:

```yaml
title: Command blacklist
description: Block known-dangerous shell commands with a PreToolUse hook.
category: Guardrails      # one of the categories in /cookbook.yaml
icon: shield-halved       # optional; any Font Awesome icon name
```

Examples without an `example.yaml` are not published. The page title and
description come from this file, so the README's `# ` heading is dropped.

## What happens to a pull request

1. **Docs render** checks that every published example converts cleanly.
2. **Docs preview** opens a draft PR in OpenHands/docs with the rendered pages
   and comments on your PR with a Mintlify preview link to each changed page. The
   `docs-preview` status fails if the page doesn't build or the docs checks fail.
3. After merge, **Docs publish** updates a single `cookbook-sync` PR in
   OpenHands/docs. Nothing under `cookbook/` in the docs repository is edited by
   hand.

## Writing the README

Write ordinary GitHub Markdown; it should read well on GitHub. Headings, lists,
tables, links, images, and code blocks pass through unchanged. These constructs
also become docs components:

| In the README | On the docs site |
|---|---|
| `> [!NOTE]`, `> [!TIP]`, `> [!IMPORTANT]`, `> [!WARNING]`, `> [!CAUTION]` | `<Note>`, `<Tip>`, `<Info>`, `<Warning>`, `<Warning>` |
| `<details><summary>Title</summary> … </details>` | `<Accordion title="Title">` |
| A `mermaid` code block | A rendered diagram |
| A code block whose info string names a file, such as `json hooks/hooks.json` | The real file, titled with its path, collapsed if long |
| `<!-- docs:tabs -->` … `<!-- /docs:tabs -->` | `<Tabs>`, one tab per heading inside |
| `<!-- docs:steps -->` … `<!-- /docs:steps -->` | `<Steps>`, one step per heading inside |
| `<!-- docs:cards -->` around a list of `[link](url) - description` items | `<CardGroup>` |
| `<!-- docs:github-only -->` … `<!-- /docs:github-only -->` | Omitted from the docs page |

Write each GitHub alert with the marker on its own line:

```markdown
> [!WARNING]
> The agent may refuse an obviously dangerous command before the hook runs.
```

The `docs:` comments are invisible on GitHub, where tabs and steps read as
ordinary sections. Leave a blank line before and after each comment.

### Links

- Links to another published example (`../load-plugin/`) become links to its docs
  page. Links to an unpublished example go to GitHub.
- Links to files or directories in this repository go to GitHub.
- Links to `https://docs.openhands.dev/...` become links within the docs site.
- Relative links must point to something that exists.

### Code blocks that name a file

A code block such as ```` ```json safety-guardian/hooks/hooks.json ```` (path
relative to the example directory) must match that file exactly, so the README
can't drift from the code. To show only part of a long file in the README, add
`excerpt`: ```` ```json safety-guardian/hooks/hooks.json excerpt ````. The docs
page always shows the full file.

### Not supported

Raw HTML other than `<details>` and `<summary>` is rejected, as are unknown
`docs:` comments and unclosed ones. Errors name the file and line.

## Running locally

```bash
cd tools/docs-render
npm ci
npm test          # converter tests
npm run check     # render every published example and report errors
```

To see the pages in the docs site, render into a checkout of OpenHands/docs and
run Mintlify's dev server there:

```bash
node tools/docs-render/cli.mjs render --docs ../docs
cd ../docs && npx mint dev
```
