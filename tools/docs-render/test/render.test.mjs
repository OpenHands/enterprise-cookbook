import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {test} from 'node:test';
import {GENERATED_MARKER, RenderError, loadSite, renderExample, writeToDocs} from '../render.mjs';

const COOKBOOK_YAML = `tab: Cookbook
after_tab: Enterprise
overview_group: Overview
title: Cookbook
description: Examples.
categories:
  - name: Guardrails
  - name: Hooks
`;

function makeRepo(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'cookbook-'));
  const all = {'cookbook.yaml': COOKBOOK_YAML, ...files};
  for (const [rel, content] of Object.entries(all)) {
    fs.mkdirSync(path.dirname(path.join(root, rel)), {recursive: true});
    fs.writeFileSync(path.join(root, rel), content);
  }
  return root;
}

const meta = (title = 'Demo', category = 'Guardrails') => `title: ${title}\ndescription: A demo.\ncategory: ${category}\n`;

async function render(readme, extra = {}) {
  const root = makeRepo({'demo/example.yaml': meta(), 'demo/README.md': readme, ...extra});
  return renderExample(loadSite(root), 'demo', {ref: 'abc123'});
}

async function renderErrors(readme, extra = {}) {
  try {
    await render(readme, extra);
  } catch (e) {
    if (e instanceof RenderError) return e.errors.join('\n');
    throw e;
  }
  assert.fail('expected a RenderError');
}

test('page has frontmatter from example.yaml, a source card, and no H1', async () => {
  const mdx = await render('# Demo title\n\nHello.\n');
  assert.match(mdx, /^---\ntitle: Demo\ndescription: A demo.\n---\n/);
  assert.match(mdx, new RegExp(GENERATED_MARKER));
  assert.match(mdx, /<Card title="View source on GitHub" icon="github" href="https:\/\/github.com\/OpenHands\/enterprise-cookbook\/tree\/abc123\/demo" horizontal \/>/);
  assert.doesNotMatch(mdx, /# Demo title/);
});

test('GitHub alerts become Mintlify callouts', async () => {
  const mdx = await render('> [!NOTE]\n> Plain note.\n\n> [!WARNING]\n> Careful.\n\n> [!TIP]\n> Hint.\n');
  assert.match(mdx, /<Note>\n\s+Plain note.\n<\/Note>/);
  assert.match(mdx, /<Warning>\n\s+Careful.\n<\/Warning>/);
  assert.match(mdx, /<Tip>/);
});

test('ordinary blockquotes stay blockquotes', async () => {
  assert.match(await render('> Just a quote.\n'), /^> Just a quote\.$/m);
});

test('unknown alert type is an error', async () => {
  assert.match(await renderErrors('> [!SHOUT]\n> Loud.\n'), /README.md:1: unknown alert \[!SHOUT\]/);
});

test('docs:tabs splits on headings', async () => {
  const mdx = await render('<!-- docs:tabs -->\n\n### First\n\nOne.\n\n### Second\n\nTwo.\n\n<!-- /docs:tabs -->\n');
  assert.match(mdx, /<Tabs>\n\s+<Tab title="First">\n\s+One.\n\s+<\/Tab>\n\n\s+<Tab title="Second">/);
});

test('docs:steps splits on headings', async () => {
  const mdx = await render('<!-- docs:steps -->\n\n### Install\n\nA.\n\n### Run\n\nB.\n\n<!-- /docs:steps -->\n');
  assert.match(mdx, /<Steps>\n\s+<Step title="Install">/);
});

test('docs:github-only content is dropped', async () => {
  const mdx = await render('Keep.\n\n<!-- docs:github-only -->\n\nGitHub only.\n\n<!-- /docs:github-only -->\n');
  assert.match(mdx, /Keep\./);
  assert.doesNotMatch(mdx, /GitHub only/);
});

test('directive errors: unknown, unclosed, stray closing', async () => {
  assert.match(await renderErrors('<!-- docs:carousel -->\n\nx\n\n<!-- /docs:carousel -->\n'), /unknown directive docs:carousel/);
  assert.match(await renderErrors('<!-- docs:tabs -->\n\n### A\n\nx\n'), /docs:tabs is not closed/);
  assert.match(await renderErrors('x\n\n<!-- /docs:tabs -->\n'), /closing docs:tabs without an opening one/);
});

test('ordinary HTML comments are dropped', async () => {
  const mdx = await render('Before.\n\n<!-- a note to maintainers -->\n\nAfter.\n');
  assert.doesNotMatch(mdx, /maintainers/);
});

test('details becomes an Accordion', async () => {
  const mdx = await render('<details>\n<summary>Why <code>this</code>?</summary>\n\nBecause.\n\n</details>\n');
  assert.match(mdx, /<Accordion title="Why this\?">\n\s+Because.\n<\/Accordion>/);
});

test('raw HTML is an error', async () => {
  assert.match(await renderErrors('<div align="center">x</div>\n'), /raw HTML is not supported/);
  assert.match(await renderErrors('Line one<br>line two\n'), /raw HTML is not supported: <br>/);
});

test('links: published examples, unpublished examples, files, docs site, images', async () => {
  const mdx = await render(
    [
      '[pub](../other/) [unpub](../draft/) [file](./script.py) [dir](./sub/)',
      '[docs](https://docs.openhands.dev/sdk/guides/hooks.md#x) [ext](https://example.com) [anchor](#top)',
      '![img](./pic.png)',
    ].join('\n\n'),
    {
      'other/example.yaml': meta('Other'),
      'other/README.md': '# Other\n',
      'draft/README.md': '# Draft\n',
      'demo/script.py': 'print(1)\n',
      'demo/sub/x.txt': 'x',
      'demo/pic.png': 'png',
    },
  );
  assert.match(mdx, /\[pub\]\(\/cookbook\/other\)/);
  assert.match(mdx, /\[unpub\]\(https:\/\/github.com\/OpenHands\/enterprise-cookbook\/tree\/abc123\/draft\)/);
  assert.match(mdx, /\[file\]\(https:\/\/github.com\/OpenHands\/enterprise-cookbook\/blob\/abc123\/demo\/script.py\)/);
  assert.match(mdx, /\[dir\]\(https:\/\/github.com\/OpenHands\/enterprise-cookbook\/tree\/abc123\/demo\/sub\)/);
  assert.match(mdx, /\[docs\]\(\/sdk\/guides\/hooks#x\)/);
  assert.match(mdx, /\[ext\]\(https:\/\/example.com\)/);
  assert.match(mdx, /\[anchor\]\(#top\)/);
  assert.match(mdx, /!\[img\]\(https:\/\/raw.githubusercontent.com\/OpenHands\/enterprise-cookbook\/abc123\/demo\/pic.png\)/);
});

test('broken relative links are errors', async () => {
  assert.match(await renderErrors('[gone](./missing.py)\n'), /link "\.\/missing.py" points to demo\/missing.py, which does not exist/);
  assert.match(await renderErrors('[up](../../etc/passwd)\n'), /points outside the repository/);
});

test('docs:cards builds a card grid with icons', async () => {
  const mdx = await render('<!-- docs:cards -->\n\n- [Other](../other/) - Sibling example\n- [Site](https://example.com) - External\n\n<!-- /docs:cards -->\n', {
    'other/example.yaml': `${meta('Other')}icon: star\n`,
    'other/README.md': '# Other\n',
  });
  assert.match(mdx, /<CardGroup cols=\{2\}>/);
  assert.match(mdx, /<Card title="Other" href="\/cookbook\/other" icon="star">\n\s+Sibling example/);
  assert.match(mdx, /<Card title="Site" href="https:\/\/example.com" icon="arrow-up-right-from-square">/);
});

test('docs:cards rejects anything but a list of links', async () => {
  assert.match(await renderErrors('<!-- docs:cards -->\n\nJust text.\n\n<!-- /docs:cards -->\n'), /must contain exactly one list of links/);
});

test('code block with a file path must match the file', async () => {
  const extra = {'demo/hook.json': '{"a": 1}\n'};
  assert.match(await render('```json hook.json\n{"a": 1}\n```\n', extra), /```json hook.json\n\{"a": 1\}\n```/);
  assert.match(await renderErrors('```json hook.json\n{"a": 2}\n```\n', extra), /code block differs from hook.json/);
});

test('excerpt code blocks show the full file on the docs site', async () => {
  const long = Array.from({length: 30}, (_, i) => `line ${i}`).join('\n');
  const mdx = await render('```text big.txt excerpt\nline 0\n...\n```\n', {'demo/big.txt': `${long}\n`});
  assert.match(mdx, /```text big.txt expandable\nline 0\nline 1\n/);
  assert.match(mdx, /line 29\n```/);
});

test('code block file errors: missing, outside the example', async () => {
  assert.match(await renderErrors('```python main.py\nx\n```\n'), /code block file "main.py" does not exist/);
  assert.match(await renderErrors('```text ../other/x.txt\nx\n```\n', {'other/x.txt': 'x'}), /outside this example/);
});

test('non-path code titles pass through', async () => {
  assert.match(await render('```bash Terminal\nls\n```\n'), /```bash Terminal\nls\n```/);
  assert.match(await render('```\nplain\n```\n'), /```text\nplain\n```/);
});

test('MDX-special characters in text are escaped', async () => {
  const mdx = await render('Use {braces} when a < b.\n');
  assert.match(mdx, /\\\{braces\}/);
  assert.match(mdx, /a \\< b/);
});

test('example.yaml validation', () => {
  const root = makeRepo({'demo/example.yaml': 'title: X\ncategory: Nope\ncolour: red\n', 'demo/README.md': '# X\n'});
  assert.throws(() => loadSite(root), (e) => {
    const msg = e.errors.join('\n');
    return /unknown key "colour"/.test(msg) && /"description" is required/.test(msg) && /category "Nope" is not one of/.test(msg);
  });
});

function makeDocs() {
  const docs = fs.mkdtempSync(path.join(os.tmpdir(), 'docs-'));
  const docsJson = {navigation: {tabs: [{tab: 'Home'}, {tab: 'Enterprise'}, {tab: 'Last'}]}, redirects: [{source: '/a', destination: '/b'}]};
  fs.writeFileSync(path.join(docs, 'docs.json'), `${JSON.stringify(docsJson, null, 2)}\n`);
  return docs;
}

const readJson = (p) => JSON.parse(fs.readFileSync(p, 'utf8'));

test('writeToDocs: pages, index, nav after Enterprise, idempotent', async () => {
  const root = makeRepo({
    'b-demo/example.yaml': meta('B demo', 'Hooks'),
    'b-demo/README.md': '# B\n',
    'a-demo/example.yaml': meta('A demo', 'Guardrails'),
    'a-demo/README.md': '# A\n',
    'unlisted/README.md': '# Not published\n',
  });
  const docs = makeDocs();
  const summary = await writeToDocs(root, docs, {ref: 'main'});
  assert.deepEqual(summary, {ref: 'main', pages: ['a-demo', 'b-demo'], removed: []});
  assert.deepEqual(fs.readdirSync(path.join(docs, 'cookbook')).sort(), ['a-demo.mdx', 'b-demo.mdx', 'index.mdx']);

  const json = readJson(path.join(docs, 'docs.json'));
  assert.deepEqual(json.navigation.tabs.map((t) => t.tab), ['Home', 'Enterprise', 'Cookbook', 'Last']);
  assert.deepEqual(json.navigation.tabs[2].groups, [
    {group: 'Overview', pages: ['cookbook/index']},
    {group: 'Guardrails', pages: ['cookbook/a-demo']},
    {group: 'Hooks', pages: ['cookbook/b-demo']},
  ]);
  const index = fs.readFileSync(path.join(docs, 'cookbook/index.mdx'), 'utf8');
  assert.ok(index.indexOf('## Guardrails') < index.indexOf('## Hooks'));

  const before = fs.readFileSync(path.join(docs, 'docs.json'), 'utf8');
  await writeToDocs(root, docs, {ref: 'main'});
  assert.equal(fs.readFileSync(path.join(docs, 'docs.json'), 'utf8'), before);
});

test('writeToDocs: renaming the tab replaces it in place', async () => {
  const root = makeRepo({'demo/example.yaml': meta(), 'demo/README.md': '# D\n'});
  const docs = makeDocs();
  await writeToDocs(root, docs);
  fs.writeFileSync(path.join(root, 'cookbook.yaml'), COOKBOOK_YAML.replace('tab: Cookbook', 'tab: Enterprise Cookbook'));

  await writeToDocs(root, docs);
  const tabs = readJson(path.join(docs, 'docs.json')).navigation.tabs;
  assert.deepEqual(tabs.map((t) => t.tab), ['Home', 'Enterprise', 'Enterprise Cookbook', 'Last']);
});

test('writeToDocs: removed examples are deleted and redirected; re-added ones drop the redirect', async () => {
  const root = makeRepo({'demo/example.yaml': meta(), 'demo/README.md': '# D\n'});
  const docs = makeDocs();
  await writeToDocs(root, docs);
  fs.rmSync(path.join(root, 'demo/example.yaml'));

  assert.deepEqual((await writeToDocs(root, docs)).removed, ['demo']);
  assert.ok(!fs.existsSync(path.join(docs, 'cookbook/demo.mdx')));
  assert.deepEqual(readJson(path.join(docs, 'docs.json')).redirects, [
    {source: '/a', destination: '/b'},
    {source: '/cookbook/demo', destination: '/cookbook'},
  ]);

  fs.writeFileSync(path.join(root, 'demo/example.yaml'), meta());
  await writeToDocs(root, docs);
  assert.deepEqual(readJson(path.join(docs, 'docs.json')).redirects, [{source: '/a', destination: '/b'}]);
});

test('writeToDocs refuses to touch hand-written files in cookbook/', async () => {
  const root = makeRepo({'demo/example.yaml': meta(), 'demo/README.md': '# D\n'});
  const docs = makeDocs();
  fs.mkdirSync(path.join(docs, 'cookbook'));
  fs.writeFileSync(path.join(docs, 'cookbook/manual.mdx'), 'hand written');
  await assert.rejects(writeToDocs(root, docs), /cookbook\/manual.mdx: not generated by this tool/);
});
