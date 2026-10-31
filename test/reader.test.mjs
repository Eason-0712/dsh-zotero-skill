import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, existsSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import vm from 'node:vm';
import { componentPlan, uvAsset, downloadVerified } from '../lib/reader-runner.js';

test('component bootstrap uses pinned digests and rejects arbitrary install names', () => {
  assert.match(uvAsset('win32', 'x64').sha256, /^[a-f0-9]{64}$/);
  assert.match(uvAsset('win32', 'x64').url, /releases\/download\/0\.9\.7/);
  assert.throws(() => componentPlan('untrusted-pip-argument'));
  assert.throws(() => uvAsset('unknown', 'unknown'));
});

test('checksum failure leaves no executable or partial archive', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'reader-download-'));
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(new TextEncoder().encode('wrong bytes'));
  try {
    const output = join(directory, 'uv.zip');
    await assert.rejects(downloadVerified({ url: 'https://example.invalid', sha256: '0'.repeat(64) }, output), /checksum/);
    assert.equal(existsSync(output), false);
    assert.equal(existsSync(`${output}.partial`), false);
    await downloadVerified({ url: 'https://example.invalid', sha256: createHash('sha256').update('wrong bytes').digest('hex') }, output);
    assert.equal(readFileSync(output, 'utf8'), 'wrong bytes');
  } finally { globalThis.fetch = original; rmSync(directory, { recursive: true, force: true }); }
});

test('companion inherits authorization and releases its endpoint', async () => {
  class AuthorizedEndpoint { async init() { throw new Error('Zotero authorization required'); } }
  const item = { id: 1, libraryID: 1, isRegularItem: () => true, toResponseJSON: async () => ({ key: 'ABC12345' }) };
  const Zotero = {
    initializationPromise: Promise.resolve(),
    Server: { LocalAPI: { Items: AuthorizedEndpoint }, Endpoints: {} },
    getActiveZoteroPane: () => ({ getSelectedItems: () => [item, item] }),
    Libraries: { get: () => ({ libraryType: 'user' }) },
  };
  const context = vm.createContext({ Zotero });
  vm.runInContext(readFileSync(new URL('../zotero/bridge/bootstrap.js', import.meta.url), 'utf8'), context);
  await context.startup();
  const Endpoint = Zotero.Server.Endpoints['/api/dsh-reader/selection'];
  const endpoint = new Endpoint();
  await assert.rejects(endpoint.init(), /authorization required/);
  const response = await endpoint.run();
  assert.equal(response[0], 200);
  assert.equal(JSON.parse(response[2]).length, 1);
  assert.equal(JSON.parse(response[2])[0].api_prefix, '/api/users/0');
  context.shutdown();
  assert.equal(Zotero.Server.Endpoints['/api/dsh-reader/selection'], undefined);
});
