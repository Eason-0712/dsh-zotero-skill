import assert from 'node:assert/strict';
import { test } from 'node:test';

import { apply, createProvider, parseFrontmatter, PROVIDER_NAME } from '../lib/index.js';

test('lists the bundled zotero skill', async () => {
  const provider = createProvider();
  const items = await provider.list();
  assert.equal(items.length, 1);
  const [skill] = items;
  assert.equal(skill.name, 'zotero');
  assert.equal(skill.provider, PROVIDER_NAME);
  assert.ok(skill.description.includes('Zotero'));
  assert.equal(skill.rank, 600);
  assert.ok(skill.resourceBase.path.endsWith('zotero'));
});

test('loads the body without frontmatter', async () => {
  const provider = createProvider();
  const [candidate] = await provider.list();
  const loaded = await provider.get(candidate);
  assert.ok(loaded.content.includes('每日自动收录'));
  assert.ok(!loaded.content.startsWith('---'));
  assert.equal(loaded.name, 'zotero');
});

test('parses quoted and plain frontmatter values', () => {
  const parsed = parseFrontmatter('---\nname: demo\ndescription: "a: b"\n---\nbody');
  assert.equal(parsed.attrs.name, 'demo');
  assert.equal(parsed.attrs.description, 'a: b');
  assert.equal(parsed.body, 'body');
});

test('apply registers the provider on ctx.skills', () => {
  let provider;
  const ctx = {
    inject(_deps, callback) {
      callback({
        skills: {
          registerProvider(factory) {
            provider = factory({});
          },
        },
      });
    },
  };
  apply(ctx);
  assert.equal(provider.name, PROVIDER_NAME);
});
