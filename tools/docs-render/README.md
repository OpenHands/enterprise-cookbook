# docs-render

The converter that turns each published example's `README.md` into a page in the
Cookbook tab of docs.openhands.dev. These are notes for maintaining it.

- To write an example that renders well, see [STYLEGUIDE.md](../../STYLEGUIDE.md).
- For how pull requests get docs previews and how changes are published, see
  [CONTRIBUTING.md](../../CONTRIBUTING.md).

## What It Generates

Given this repository and a checkout of OpenHands/docs, `render` writes:

- `cookbook/<example>.mdx` for every example with an `example.yaml`.
- `cookbook/index.mdx`, the overview page, built from
  [`cookbook.yaml`](../../cookbook.yaml): one section per category, with a card
  for each example.
- The Cookbook tab in `docs.json`, placed after `after_tab`, with the overview
  group and one group per category. The rest of `docs.json` is rewritten byte for
  byte, so the diff touches only the Cookbook tab.
- A redirect to `/cookbook` for each page that is no longer published, so old
  links keep working. A redirect is dropped when its page is published again.

It deletes pages under `cookbook/` for examples that are no longer published, and
refuses to run if `cookbook/` contains a file it didn't generate. The output
depends only on its inputs, so a second run changes nothing.

## Commands

```bash
npm ci
npm test                                         # converter tests
npm run check                                    # render every example, report errors
node cli.mjs render --docs ../../../docs         # write pages into a docs checkout
```

`render` options:

| Option | Default | Purpose |
|---|---|---|
| `--docs DIR` | Required | The OpenHands/docs checkout to write into |
| `--examples DIR` | This repository | The directory containing the examples and `cookbook.yaml` |
| `--ref REF` | `main` | The commit or branch that links to files on GitHub point at |
| `--summary FILE` | None | Write the pages added, changed, and removed as JSON, used for the preview comment |

## Previewing Locally

Render into a checkout of OpenHands/docs next to this repository and run
Mintlify's dev server there:

```bash
node tools/docs-render/cli.mjs render --docs ../docs
cd ../docs && npx mint dev
```

## How the Workflows Use It

| File | Role |
|---|---|
| [`.github/actions/render-docs`](../../.github/actions/render-docs/action.yml) | Runs `render` against a fresh docs checkout and uploads the result. Has no secrets. |
| [`.github/workflows/docs-render.yml`](../../.github/workflows/docs-render.yml) | Runs `npm test` and `npm run check` on every pull request and push. |
| [`.github/workflows/docs-preview.yml`](../../.github/workflows/docs-preview.yml) | Pushes the rendered files to a draft docs pull request per cookbook pull request and reports back. |
| [`.github/workflows/docs-publish.yml`](../../.github/workflows/docs-publish.yml) | Keeps the `cookbook-sync` docs pull request up to date from `main`. |
| [`.github/scripts/docs_sync.py`](../../.github/scripts/docs_sync.py) | Pushes to the docs repository, waits for its checks, and posts the comment and `docs-preview` status. |

## Changing the Converter

The conversion rules authors rely on are documented in
[STYLEGUIDE.md](../../STYLEGUIDE.md#docs-components). When you add or change a
rule, update that section in the same pull request and add a test in
`test/render.test.mjs`.
