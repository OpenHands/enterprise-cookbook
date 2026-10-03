// Renders example READMEs into Mintlify MDX pages for the docs Cookbook tab.
// Deterministic: output depends only on the files in this repo and the ref.
// See README.md in this directory for the authoring conventions.
import fs from 'node:fs';
import path from 'node:path';
import {compile} from '@mdx-js/mdx';
import remarkGfm from 'remark-gfm';
import remarkMdx from 'remark-mdx';
import remarkParse from 'remark-parse';
import remarkStringify from 'remark-stringify';
import {unified} from 'unified';
import {SKIP, visit} from 'unist-util-visit';
import YAML from 'yaml';

export const REPO = 'OpenHands/enterprise-cookbook';
export const GENERATED_MARKER = `GENERATED from ${REPO}`;
const DOCS_DIR = 'cookbook';
const EXPANDABLE_LINES = 25;
const ALERTS = {NOTE: 'Note', TIP: 'Tip', IMPORTANT: 'Info', WARNING: 'Warning', CAUTION: 'Warning'};
const DIRECTIVES = {tabs: ['Tabs', 'Tab'], steps: ['Steps', 'Step'], cards: null, 'github-only': null};
const EXAMPLE_KEYS = new Set(['title', 'description', 'category', 'icon']);

export class RenderError extends Error {
  constructor(errors) {
    super(errors.join('\n'));
    this.errors = errors;
  }
}

// ---------- loading ----------

export function loadSite(root) {
  const config = YAML.parse(fs.readFileSync(path.join(root, 'cookbook.yaml'), 'utf8'));
  const categories = config.categories.map((c) => c.name);
  const errors = [];
  const examples = new Map();
  for (const dir of fs.readdirSync(root).sort()) {
    const metaPath = path.join(root, dir, 'example.yaml');
    if (!fs.existsSync(metaPath)) continue;
    const meta = YAML.parse(fs.readFileSync(metaPath, 'utf8')) ?? {};
    const where = `${dir}/example.yaml`;
    for (const key of Object.keys(meta)) {
      if (!EXAMPLE_KEYS.has(key)) errors.push(`${where}: unknown key "${key}"`);
    }
    for (const key of ['title', 'description', 'category']) {
      if (typeof meta[key] !== 'string' || !meta[key].trim()) errors.push(`${where}: "${key}" is required`);
    }
    if (meta.category && !categories.includes(meta.category)) {
      errors.push(`${where}: category "${meta.category}" is not one of: ${categories.join(', ')}`);
    }
    if (!fs.existsSync(path.join(root, dir, 'README.md'))) errors.push(`${dir}: example.yaml without README.md`);
    examples.set(dir, {slug: dir, ...meta});
  }
  if (errors.length) throw new RenderError(errors);
  return {root, config, examples};
}

// ---------- mdast helpers ----------

function jsx(name, attrs = {}, children = []) {
  const attributes = Object.entries(attrs)
    .filter(([, v]) => v !== undefined)
    .map(([k, v]) => {
      if (v === true) return {type: 'mdxJsxAttribute', name: k, value: null};
      if (typeof v === 'number') {
        return {type: 'mdxJsxAttribute', name: k, value: {type: 'mdxJsxAttributeValueExpression', value: String(v)}};
      }
      return {type: 'mdxJsxAttribute', name: k, value: v};
    });
  return {type: 'mdxJsxFlowElement', name, attributes, children};
}

const toText = (n) => (n.value ?? '') + (n.children ?? []).map(toText).join('');
const line = (n) => n.position?.start.line ?? '?';
const comment = (n) => n.type === 'html' && /^<!--[\s\S]*-->$/.test(n.value.trim());
const directive = (n) => (n.type === 'html' ? n.value.trim().match(/^<!--\s*(\/?)docs:([\w-]+)\s*-->$/) : null);

function parseMarkdown(text) {
  return unified().use(remarkParse).use(remarkGfm).parse(text);
}

function stringify(tree) {
  return unified().use(remarkGfm).use(remarkMdx).use(remarkStringify, {bullet: '-', fences: true}).stringify(tree);
}

function frontmatter(fields) {
  return `---\n${YAML.stringify(fields, {lineWidth: 0})}---\n\n`;
}

function generatedComment(ref, source) {
  return {type: 'mdxFlowExpression', value: `/* ${GENERATED_MARKER}@${ref} (${source}). Edit the source, not this file. */`};
}

// ---------- example pages ----------

class ExampleRenderer {
  constructor(site, slug, ref) {
    this.site = site;
    this.slug = slug;
    this.ref = ref;
    this.dir = path.join(site.root, slug);
    this.where = `${slug}/README.md`;
    this.errors = [];
  }

  error(node, message) {
    this.errors.push(`${this.where}:${line(node)}: ${message}`);
  }

  rewriteUrl(node) {
    const url = node.url;
    if (/^https:\/\/docs\.openhands\.dev\//.test(url)) {
      return url.replace('https://docs.openhands.dev', '').replace(/\.mdx?(?=#|$)/, '');
    }
    if (/^[a-z][a-z0-9+.-]*:|^#|^\//i.test(url)) return url;
    const [rawPath, hash] = url.split('#');
    const anchor = hash ? `#${hash}` : '';
    const rel = path.posix.normalize(path.posix.join(this.slug, decodeURI(rawPath))).replace(/\/$/, '');
    if (rel === '..' || rel.startsWith('../')) {
      this.error(node, `link "${url}" points outside the repository`);
      return url;
    }
    const target = rel.split('/')[0];
    if (this.site.examples.has(target) && (rel === target || rel === `${target}/README.md`)) {
      return `/${DOCS_DIR}/${target}${anchor}`;
    }
    const full = path.join(this.site.root, rel);
    if (!fs.existsSync(full)) {
      this.error(node, `link "${url}" points to ${rel}, which does not exist`);
      return url;
    }
    if (node.type === 'image') return `https://raw.githubusercontent.com/${REPO}/${this.ref}/${rel}`;
    const kind = fs.statSync(full).isDirectory() ? 'tree' : 'blob';
    return `https://github.com/${REPO}/${kind}/${this.ref}/${rel}${anchor}`;
  }

  splitByHeading(nodes, [wrapper, item], start) {
    const depth = nodes.find((n) => n.type === 'heading')?.depth;
    if (!depth) {
      this.error(start, `docs directive needs a heading for each ${item.toLowerCase()}`);
      return [];
    }
    const groups = [];
    for (const n of nodes) {
      if (n.type === 'heading' && n.depth === depth) groups.push({title: toText(n), children: []});
      else if (groups.length) groups.at(-1).children.push(n);
      else this.error(n, `content before the first ${item.toLowerCase()} heading`);
    }
    return [jsx(wrapper, {}, groups.map((g) => jsx(item, {title: g.title}, g.children)))];
  }

  cards(nodes, start) {
    const list = nodes.length === 1 && nodes[0].type === 'list' ? nodes[0] : null;
    if (!list) {
      this.error(start, 'docs:cards must contain exactly one list of links');
      return [];
    }
    const cards = list.children.map((item) => {
      const para = item.children[0];
      const link = para?.type === 'paragraph' ? para.children.find((c) => c.type === 'link') : null;
      if (!link || item.children.length !== 1) {
        this.error(item, 'each docs:cards item must be a single line starting with a link');
        return null;
      }
      const description = para.children
        .slice(para.children.indexOf(link) + 1)
        .map(toText)
        .join('')
        .replace(/^\s*[-–—:]\s*/, '')
        .trim();
      const target = link.url.startsWith(`/${DOCS_DIR}/`) ? this.site.examples.get(link.url.split('/')[2]) : null;
      const icon = target?.icon ?? (link.url.startsWith('/') ? 'book-open' : 'arrow-up-right-from-square');
      const children = description ? [{type: 'paragraph', children: [{type: 'text', value: description}]}] : [];
      return jsx('Card', {title: toText(link), href: link.url, icon}, children);
    });
    return [jsx('CardGroup', {cols: 2}, cards.filter(Boolean))];
  }

  accordion(children, i) {
    const open = children[i];
    let summary = open.value.match(/<summary>([\s\S]*?)<\/summary>/);
    let bodyStart = i + 1;
    if (!summary && children[i + 1]?.type === 'html') {
      summary = children[i + 1].value.match(/^<summary>([\s\S]*?)<\/summary>$/);
      bodyStart = i + 2;
    }
    const end = children.findIndex((m, j) => j >= bodyStart && m.type === 'html' && m.value.trim() === '</details>');
    if (!summary || end === -1) {
      this.error(open, '<details> needs a <summary> and a closing </details> on its own line');
      return {nodes: [], next: end === -1 ? children.length : end};
    }
    const title = summary[1].replace(/<[^>]+>/g, '').trim();
    return {nodes: [jsx('Accordion', {title}, this.blocks(children.slice(bodyStart, end)))], next: end};
  }

  blocks(children) {
    const out = [];
    for (let i = 0; i < children.length; i++) {
      const n = children[i];
      const d = directive(n);
      if (d) {
        const [, closing, name] = d;
        if (!(name in DIRECTIVES)) {
          this.error(n, `unknown directive docs:${name} (known: ${Object.keys(DIRECTIVES).join(', ')})`);
          continue;
        }
        if (closing) {
          this.error(n, `closing docs:${name} without an opening one`);
          continue;
        }
        const end = children.findIndex((m, j) => j > i && directive(m)?.[1] === '/' && directive(m)[2] === name);
        if (end === -1) {
          this.error(n, `docs:${name} is not closed with <!-- /docs:${name} -->`);
          continue;
        }
        const inner = this.blocks(children.slice(i + 1, end));
        if (name === 'cards') out.push(...this.cards(inner, n));
        else if (DIRECTIVES[name]) out.push(...this.splitByHeading(inner, DIRECTIVES[name], n));
        i = end;
        continue;
      }
      if (n.type === 'html' && /^<details>/.test(n.value.trim())) {
        const {nodes, next} = this.accordion(children, i);
        out.push(...nodes);
        i = next;
        continue;
      }
      if (comment(n)) continue;
      out.push(n);
    }
    return out;
  }

  code(node) {
    if (!node.lang) node.lang = 'text';
    const tokens = (node.meta ?? '').split(/\s+/).filter(Boolean);
    const first = tokens[0];
    if (!first || !(first.includes('/') || /^[\w.-]+\.[A-Za-z0-9]+$/.test(first))) return;
    const full = path.resolve(this.dir, first);
    if (!full.startsWith(this.dir + path.sep)) {
      this.error(node, `code block file "${first}" is outside this example`);
      return;
    }
    if (!fs.existsSync(full) || !fs.statSync(full).isFile()) {
      this.error(node, `code block file "${first}" does not exist (path is relative to ${this.slug}/)`);
      return;
    }
    const content = fs.readFileSync(full, 'utf8').replace(/\n+$/, '');
    const excerpt = tokens.includes('excerpt');
    if (!excerpt && node.value.replace(/\n+$/, '') !== content) {
      this.error(node, `code block differs from ${first}; paste the file exactly or mark the block "excerpt"`);
    }
    node.value = content;
    const rest = tokens.slice(1).filter((t) => t !== 'excerpt' && t !== 'expandable');
    if (content.split('\n').length > EXPANDABLE_LINES) rest.unshift('expandable');
    node.meta = [first, ...rest].join(' ');
  }

  async render() {
    const meta = this.site.examples.get(this.slug);
    const tree = parseMarkdown(fs.readFileSync(path.join(this.dir, 'README.md'), 'utf8'));

    const h1 = tree.children.filter((n) => n.type === 'heading' && n.depth === 1);
    h1.slice(1).forEach((n) => this.error(n, 'only one top-level "# " heading is allowed'));
    tree.children = tree.children.filter((n) => n !== h1[0]);

    visit(tree, ['link', 'image', 'definition'], (n) => {
      n.url = this.rewriteUrl(n);
    });
    tree.children = this.blocks(tree.children);

    visit(tree, (n, index, parent) => {
      if (n.type === 'blockquote') {
        const first = n.children[0]?.children?.[0];
        const m = first?.type === 'text' ? first.value.match(/^\[!(\w+)\][ \t]*\n?/) : null;
        if (m) {
          if (!ALERTS[m[1]]) this.error(n, `unknown alert [!${m[1]}] (known: ${Object.keys(ALERTS).join(', ')})`);
          first.value = first.value.slice(m[0].length);
          parent.children[index] = jsx(ALERTS[m[1]] ?? 'Note', {}, n.children);
          return SKIP;
        }
      }
      if (n.type === 'code') this.code(n);
      if (n.type === 'html') {
        if (comment(n)) {
          parent.children.splice(index, 1);
          return index;
        }
        this.error(n, `raw HTML is not supported: ${n.value.trim().slice(0, 40)}`);
      }
    });

    tree.children.unshift(
      generatedComment(this.ref, this.where),
      jsx('Card', {
        title: 'View source on GitHub',
        icon: 'github',
        href: `https://github.com/${REPO}/tree/${this.ref}/${this.slug}`,
        horizontal: true,
      }),
    );

    if (this.errors.length) throw new RenderError(this.errors);
    const fields = {title: meta.title, description: meta.description};
    if (meta.icon) fields.icon = meta.icon;
    const mdx = frontmatter(fields) + stringify(tree);
    await assertCompiles(mdx, `${DOCS_DIR}/${this.slug}.mdx`);
    return mdx;
  }
}

export function renderExample(site, slug, {ref = 'main'} = {}) {
  return new ExampleRenderer(site, slug, ref).render();
}

async function assertCompiles(mdx, where) {
  try {
    await compile(mdx.replace(/^---\n[\s\S]*?\n---\n/, ''), {remarkPlugins: [remarkGfm]});
  } catch (e) {
    throw new RenderError([`${where}: generated MDX does not compile (converter bug): ${e.message}`]);
  }
}

// ---------- index page and navigation ----------

function byCategory(site) {
  return site.config.categories
    .map((c) => ({...c, examples: [...site.examples.values()].filter((e) => e.category === c.name)}))
    .filter((c) => c.examples.length);
}

export async function renderIndex(site, {ref = 'main'} = {}) {
  const {config} = site;
  const children = [generatedComment(ref, 'cookbook.yaml'), ...parseMarkdown(config.intro ?? '').children];
  for (const category of byCategory(site)) {
    children.push({type: 'heading', depth: 2, children: [{type: 'text', value: category.name}]});
    if (category.description) children.push(...parseMarkdown(category.description).children);
    children.push(
      jsx(
        'CardGroup',
        {cols: 2},
        category.examples.map((e) =>
          jsx('Card', {title: e.title, icon: e.icon, href: `/${DOCS_DIR}/${e.slug}`}, [
            {type: 'paragraph', children: [{type: 'text', value: e.description}]},
          ]),
        ),
      ),
    );
  }
  const mdx = frontmatter({title: config.title, description: config.description}) + stringify({type: 'root', children});
  await assertCompiles(mdx, `${DOCS_DIR}/index.mdx`);
  return mdx;
}

export function updateDocsJson(docsJson, site, removedSlugs = []) {
  const {config} = site;
  const tab = {
    tab: config.tab,
    groups: [
      {group: config.overview_group, pages: [`${DOCS_DIR}/index`]},
      ...byCategory(site).map((c) => ({group: c.name, pages: c.examples.map((e) => `${DOCS_DIR}/${e.slug}`)})),
    ],
  };
  const tabs = docsJson.navigation.tabs;
  const existing = tabs.findIndex((t) => t.tab === config.tab);
  if (existing !== -1) tabs[existing] = tab;
  else {
    const after = tabs.findIndex((t) => t.tab === config.after_tab);
    tabs.splice(after === -1 ? tabs.length : after + 1, 0, tab);
  }

  const live = new Set([...site.examples.keys()].map((s) => `/${DOCS_DIR}/${s}`));
  const redirects = (docsJson.redirects ?? []).filter((r) => !live.has(r.source));
  for (const slug of removedSlugs) {
    const source = `/${DOCS_DIR}/${slug}`;
    if (!redirects.some((r) => r.source === source)) redirects.push({source, destination: `/${DOCS_DIR}`});
  }
  docsJson.redirects = redirects;
  return docsJson;
}

// ---------- whole site ----------

export async function renderSite(root, {ref = 'main'} = {}) {
  const site = loadSite(root);
  const pages = new Map();
  const errors = [];
  for (const slug of site.examples.keys()) {
    try {
      pages.set(`${slug}.mdx`, await renderExample(site, slug, {ref}));
    } catch (e) {
      if (!(e instanceof RenderError)) throw e;
      errors.push(...e.errors);
    }
  }
  if (errors.length) throw new RenderError(errors);
  pages.set('index.mdx', await renderIndex(site, {ref}));
  return {site, pages};
}

export async function writeToDocs(root, docsDir, {ref = 'main'} = {}) {
  const {site, pages} = await renderSite(root, {ref});
  const outDir = path.join(docsDir, DOCS_DIR);
  fs.mkdirSync(outDir, {recursive: true});

  const removed = [];
  for (const file of fs.readdirSync(outDir)) {
    if (pages.has(file)) continue;
    const full = path.join(outDir, file);
    if (!file.endsWith('.mdx') || !fs.readFileSync(full, 'utf8').includes(GENERATED_MARKER)) {
      throw new RenderError([`${DOCS_DIR}/${file}: not generated by this tool; ${DOCS_DIR}/ must only hold generated pages`]);
    }
    fs.rmSync(full);
    removed.push(file.replace(/\.mdx$/, ''));
  }
  for (const [file, mdx] of pages) fs.writeFileSync(path.join(outDir, file), mdx);

  const docsJsonPath = path.join(docsDir, 'docs.json');
  const docsJson = updateDocsJson(JSON.parse(fs.readFileSync(docsJsonPath, 'utf8')), site, removed);
  fs.writeFileSync(docsJsonPath, `${JSON.stringify(docsJson, null, 2)}\n`);

  return {ref, pages: [...site.examples.keys()], removed};
}
