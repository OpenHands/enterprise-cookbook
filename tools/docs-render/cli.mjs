#!/usr/bin/env node
// usage:
//   node cli.mjs check  [--examples DIR] [--ref REF]
//   node cli.mjs render --docs DIR [--examples DIR] [--ref REF] [--summary FILE]
import fs from 'node:fs';
import path from 'node:path';
import {parseArgs} from 'node:util';
import {RenderError, renderSite, writeToDocs} from './render.mjs';

const {positionals, values} = parseArgs({
  allowPositionals: true,
  options: {
    examples: {type: 'string', default: path.resolve(import.meta.dirname, '../..')},
    docs: {type: 'string'},
    ref: {type: 'string', default: 'main'},
    summary: {type: 'string'},
  },
});

const command = positionals[0];
try {
  if (command === 'check') {
    const {site} = await renderSite(path.resolve(values.examples), {ref: values.ref});
    console.log(`OK: ${site.examples.size} example(s) render cleanly: ${[...site.examples.keys()].join(', ')}`);
  } else if (command === 'render' && values.docs) {
    const summary = await writeToDocs(path.resolve(values.examples), path.resolve(values.docs), {ref: values.ref});
    if (values.summary) fs.writeFileSync(values.summary, `${JSON.stringify(summary, null, 2)}\n`);
    console.log(`Rendered ${summary.pages.length} page(s); removed ${summary.removed.length}.`);
  } else {
    console.error('usage: cli.mjs check [--examples DIR] | render --docs DIR [--examples DIR] [--ref REF] [--summary FILE]');
    process.exit(2);
  }
} catch (e) {
  if (!(e instanceof RenderError)) throw e;
  for (const err of e.errors) {
    console.error(process.env.GITHUB_ACTIONS ? `::error::${err}` : `error: ${err}`);
  }
  process.exit(1);
}
