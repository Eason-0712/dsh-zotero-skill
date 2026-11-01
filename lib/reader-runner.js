/** Package entry point; the implementation also ships inside standalone skills. */
import { realpathSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { main } from '../zotero/scripts/reader-runner.mjs';
export { componentPlan, uvAsset, downloadVerified, pipIndexes, pythonCandidates, findSystemPython, main } from '../zotero/scripts/reader-runner.mjs';

function sameFile(left, right) {
  // Linked installs resolve to a different real path than argv[1].
  try { return realpathSync(resolve(left)) === realpathSync(resolve(right)); } catch { return false; }
}

if (process.argv[1] && sameFile(process.argv[1], fileURLToPath(import.meta.url))) {
  main().then(code => { process.exitCode = code; }).catch(error => {
    console.error(`Reader setup: ${error.message}`);
    process.exitCode = 1;
  });
}
