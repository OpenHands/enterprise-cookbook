# Contributing to the Enterprise Cookbook

Each directory in this repository is a standalone, runnable example for the
OpenHands API. Examples are read in two places: here on GitHub, and as pages in
the [Enterprise Cookbook](https://docs.openhands.dev/cookbook) tab of docs.openhands.dev,
which is generated from each example's `README.md`.

This guide covers the process: how a change gets from a pull request to the docs
site. For how to write an example and its README, see
[STYLEGUIDE.md](./STYLEGUIDE.md).

## Adding or Upleveling an Example

**A new example** gets its own top-level directory with a kebab-case name, a
`README.md`, and the code it runs. Follow the layout and README structure in the
style guide, and add the example to its category in the root
[README.md](./README.md#examples).

**Upleveling an existing example** means bringing it up to the style guide so it
can be published. Work through the
[upleveling checklist](./STYLEGUIDE.md#upleveling-checklist), one example per
pull request, so each preview shows a single page. [`command-blacklist`](./command-blacklist/)
is the reference.

## Publishing to docs.openhands.dev

An example is published once its directory has an `example.yaml`:

```yaml
title: Command Blacklist
description: Block known-dangerous shell commands with a PreToolUse hook bundled in a plugin.
category: Guardrails
icon: shield-halved
```

Examples without one stay GitHub-only, which is the right choice for
experimental or work-in-progress examples. The fields are described in the
[style guide](./STYLEGUIDE.md#exampleyaml).

`category` must be one of the categories in [`cookbook.yaml`](./cookbook.yaml),
which also sets the tab's title, position, and overview page. To add a category,
add it there and to the root README in the same pull request.

## What Happens on a Pull Request

| Check | What it does | Required to merge |
|---|---|---|
| `pre-commit` | Formatting and linting: ruff, pycodestyle, pyright, yamlfmt, whitespace | Yes |
| `test` | `uv run pytest` | Yes |
| `render` (Docs render) | Converts every published example and fails on anything the converter rejects | No |
| `docs-preview` (Docs preview) | Builds a preview of the docs site with your change | No |
| `pr-review` (PR Review by OpenHands) | An AI code review | No |

Treat `render` and `docs-preview` as required for any change to a published
example, even though GitHub doesn't enforce them.

### The Docs Preview

For each pull request, the **Docs preview** workflow:

1. Renders the published examples from your branch.
2. Opens or updates a draft pull request in
   [OpenHands/docs](https://github.com/OpenHands/docs) on a
   `cookbook-preview/pr-<number>` branch, titled "Enterprise Cookbook preview: … (do not
   merge)". Mintlify builds a preview site for it.
3. Waits for the Mintlify deployment and the docs repository's checks, such as
   internal links and link rot.
4. Comments on your pull request with the preview URL and a link to each page
   that changed, then sets the `docs-preview` status.

Each push updates the same docs pull request and the same comment. If your
change doesn't affect any published page, no docs pull request is opened.
Changed pages are listed relative to what is live on docs.openhands.dev.

When `docs-preview` fails, the status links to the workflow run:

- **Render failed**: the log names the file and line, for example
  `conversation-tags/README.md:129: link "./NOTES.md" … does not exist`. Fix the
  README and push.
- **Docs checks failed**: open the draft docs pull request linked in the comment
  to see which check failed, usually a broken link.

When your pull request is merged or closed, the draft docs pull request is closed
and its branch deleted. It is never merged.

### Pull Requests From Forks

The preview and the AI review need repository secrets, so they only run for
branches in this repository. A pull request from a fork still gets `render`, so
README errors are caught. If you have write access, push your branch here
instead of to a fork to get a preview.

### AI Review

The OpenHands review runs when a pull request is opened (unless it's a draft) or
marked ready for review. To run it again, add the `review-this` label or request
a review from `openhands-agent`.

## After Merge

On every push to `main`, nightly, and on demand, the **Docs publish** workflow
renders all published examples and keeps a single pull request in OpenHands/docs
up to date on the `cookbook-sync` branch. Once the docs repository's checks and
Mintlify preview pass, the workflow approves and squash-merges that pull request
as `openhands-agent`, which puts the changes live. Nothing in the docs repository
needs a manual review. The approval is skipped, and the job fails, if the pull
request changes anything other than `cookbook/` and `docs.json`, or if the docs
checks fail; a docs maintainer then reviews it by hand.

The nightly run rebuilds the pull request on top of the latest docs, so it stays
mergeable when other docs changes touch `docs.json`. If nothing changed, the run
does nothing. To run it by hand, use **Run workflow** on the
[Docs publish](https://github.com/OpenHands/enterprise-cookbook/actions/workflows/docs-publish.yml) page.

The workflows authenticate to OpenHands/docs as `openhands-release-bot`. The job
that holds that credential only copies rendered files; it never installs
dependencies or runs code from the pull request. The approval comes from a second
identity, stored as the `DOCS_APPROVER_TOKEN` repository secret, because the
author of a pull request cannot approve it. That token needs write access to
OpenHands/docs and runs only on pushes to `main`.

## The Docs Repository

Everything under `cookbook/` in OpenHands/docs, and the Enterprise Cookbook tab in its
`docs.json`, is generated from this repository:

- Don't edit those files in the docs repository. The next sync overwrites them.
  Make the change here.
- Don't merge the draft `cookbook-preview/*` pull requests.
- To fix something on a published page, change the example's `README.md` or
  `example.yaml` here.

## Local Checks

```bash
uv sync --group dev
uv run pre-commit install        # run the hooks on every commit
uv run pre-commit run --all-files
uv run pytest
```

To check the docs rendering, with Node.js 22 as in CI:

```bash
cd tools/docs-render
npm ci
npm run check
```

To see the pages on a local copy of the docs site, see
[tools/docs-render](./tools/docs-render/README.md#previewing-locally).

## Reviews and Merging

Changes to `main` go through a pull request with at least one approving review
and passing `pre-commit` and `test` checks. Pull requests are squash-merged, so
the pull request title becomes the commit message on `main`. Write it as a short
description of the change.
