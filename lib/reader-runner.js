/** Package entry point; the implementation also ships inside standalone skills. */
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { main } from '../zotero/scripts/reader-runner.mjs';
export { componentPlan, uvAsset, downloadVerified, main } from '../zotero/scripts/reader-runner.mjs';

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().then(code => { process.exitCode = code; }).catch(error => {
    console.error(`Reader setup: ${error.message}`);
    process.exitCode = 1;
  });
}
