/** Managed local components; no model keys, endpoints or inference calls. */
import { createHash } from 'node:crypto';
import { createWriteStream, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, spawnSync } from 'node:child_process';
import { Readable, Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';

const SKILL_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const UV_VERSION = '0.9.7';
// Official release SHA256 digests, pinned independently of the download response.
const UV = {
  'win32-x64': ['x86_64-pc-windows-msvc.zip', '5d250c32d3604e28dbe18dc65c668ff628c53e00dde2c642576e831e4a60da64'],
  'win32-arm64': ['aarch64-pc-windows-msvc.zip', '4482ad2544544e1966b6d933d38e27368ce0307739d293322c10459f698a1629'],
  'linux-x64': ['x86_64-unknown-linux-gnu.tar.gz', 'b26fcc8dfa1c39b5a5613445af3be3eefda45d9a39359bee271eafe34913583e'],
  'linux-arm64': ['aarch64-unknown-linux-gnu.tar.gz', '8b3d31a154673c6d357727d2083a33525b515589d153fa5b5455e1db9e9e6363'],
  'darwin-x64': ['x86_64-apple-darwin.tar.gz', '41946d87e1576c297d6d3cca88b089b6942b8777a5a25e70de1ef8c57b94b9cf'],
  'darwin-arm64': ['aarch64-apple-darwin.tar.gz', '35572b9619fc14d67fc1cd72582c3cfc5c9c66d97f310192e04f26fb3fe96005'],
};
const COMPONENTS = {
  pdf: ['pdfplumber>=0.11.9,<0.12', 'pypdf>=6.10,<7', 'pypdfium2>=4.30,<6', 'reportlab>=4.4.9,<5'],
  ocr: ['rapidocr_onnxruntime>=1.4,<2'],
  font: ['fonttools>=4.50,<5'],
};
const FONT = {
  name: 'NotoSansSC-VF.ttf', sha256: 'd68bafcb48a2707749396aa12bbbd833cb70401f3a9a689fd2902c7e0d295964',
  url: 'https://raw.githubusercontent.com/notofonts/noto-cjk/f8d157532fbfaeda587e826d4cd5b21a49186f7c/Sans/Variable/TTF/Subset/NotoSansSC-VF.ttf',
};
// Package indexes are tried in order; the first entry is the configured/official one.
const PYPI_INDEXES = ['https://pypi.org/simple', 'https://mirrors.aliyun.com/pypi/simple'];
const INSTANCE_FONT = 'import sys; from fontTools.ttLib import TTFont; from fontTools.varLib.instancer import instantiateVariableFont; f=TTFont(sys.argv[1]); instantiateVariableFont(f,{"wght":400},inplace=True); f.save(sys.argv[2])';

/** Mirrors are a deployment choice, not a code constant: DSH_READER_PYPI_INDEX wins when set. */
export function pipIndexes(env = process.env) {
  const configured = String(env.DSH_READER_PYPI_INDEX || '').split(/[\s,]+/).filter(Boolean);
  return configured.length ? configured : [...PYPI_INDEXES];
}

/** Python interpreters to probe when the pinned uv download is unreachable. */
export function pythonCandidates(env = process.env, platform = process.platform) {
  const configured = String(env.DSH_READER_PYTHON || '').trim();
  if (configured) return [{ command: configured, prefix: [] }];
  const names = platform === 'win32'
    ? [['py', ['-3']], ['python', []], ['python3', []]]
    : [['python3', []], ['python', []]];
  return names.map(([command, prefix]) => ({ command, prefix }));
}

function probePython(candidate) {
  const result = spawnSync(candidate.command, [...candidate.prefix, '-c', 'import sys;print("%d.%d" % sys.version_info[:2])'],
    { encoding: 'utf8', windowsHide: true, timeout: 20_000 });
  if (result.status !== 0) return null;
  const match = /^(\d+)\.(\d+)$/.exec(String(result.stdout || '').trim());
  if (!match) return null;
  return Number(match[1]) === 3 && Number(match[2]) >= 10 ? { ...candidate, version: `${match[1]}.${match[2]}` } : null;
}

/** First usable Python 3.10+ interpreter; the skill already needs one to run zotero.py. */
export function findSystemPython(env = process.env, platform = process.platform) {
  for (const candidate of pythonCandidates(env, platform)) {
    const found = probePython(candidate);
    if (found) return found;
  }
  return null;
}

export function componentPlan(name) {
  if (!Object.hasOwn(COMPONENTS, name)) throw new Error(`Unknown component: ${name}`);
  return [...COMPONENTS[name]];
}

export function uvAsset(platform = process.platform, arch = process.arch) {
  const asset = UV[`${platform}-${arch}`];
  if (!asset) throw new Error(`Automatic runtime unavailable on ${platform}/${arch}; use Python 3.10+ directly`);
  return { name: `uv-${asset[0]}`, sha256: asset[1], url: `https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${asset[0]}` };
}

export async function downloadVerified(asset, destination) {
  const response = await fetch(asset.url, { signal: AbortSignal.timeout(180_000) });
  if (!response.ok || !response.body) throw new Error(`Component download failed: HTTP ${response.status}`);
  const hash = createHash('sha256');
  let received = 0, last = 0;
  const total = Number(response.headers.get('content-length')) || null;
  const temp = `${destination}.partial`;
  try {
    await pipeline(Readable.fromWeb(response.body), new Transform({ transform(chunk, _encoding, callback) {
      received += chunk.length;
      if (received > 100 * 1024 * 1024) return callback(new Error('Runtime archive exceeds 100 MiB'));
      hash.update(chunk);
      if (Date.now() - last > 1000) {
        process.stderr.write(`${JSON.stringify({ stage: 'download-component', component: asset.name, bytes: received, total })}\n`);
        last = Date.now();
      }
      callback(null, chunk);
    } }), createWriteStream(temp));
    if (hash.digest('hex') !== asset.sha256) throw new Error('Runtime checksum mismatch; archive discarded');
    const { renameSync } = await import('node:fs');
    renameSync(temp, destination);
  } finally {
    rmSync(temp, { force: true });
  }
}

function run(command, args, { filterLegacyWarnings = false, ...options } = {}) {
  return new Promise((accept, reject) => {
    const child = spawn(command, args, { stdio: filterLegacyWarnings ? ['inherit', 'inherit', 'pipe'] : 'inherit', windowsHide: true, ...options });
    if (filterLegacyWarnings) {
      // uv's old-file notices can otherwise swamp first-use progress with hundreds of lines.
      let buffer = '';
      child.stderr.on('data', chunk => {
        buffer += chunk.toString();
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) if (!line.startsWith('WARN Skipping file for ')) process.stderr.write(line + '\n');
      });
      child.stderr.on('end', () => { if (buffer && !buffer.startsWith('WARN Skipping file for ')) process.stderr.write(buffer); });
    }
    child.once('error', reject);
    child.once('exit', (code, signal) => code === 0 ? accept() : reject(new Error(`${command} exited ${code ?? signal}`)));
  });
}

async function setupLock(directory, callback) {
  mkdirSync(directory, { recursive: true });
  const lock = join(directory, '.setup-lock');
  const deadline = Date.now() + 600_000;
  for (;;) {
    try { mkdirSync(lock); break; } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      if (Date.now() > deadline) throw new Error(`Component setup is busy. If it crashed, remove ${lock} after checking no setup is running.`);
      await new Promise(done => setTimeout(done, 1000));
    }
  }
  try { return await callback(); } finally { rmSync(lock, { recursive: true, force: true }); }
}

async function ensureUv(directory) {
  const asset = uvAsset();
  const uvDir = join(directory, `uv-${UV_VERSION}`);
  const binary = process.platform === 'win32' ? 'uv.exe' : 'uv';
  const uv = join(uvDir, binary);
  if (existsSync(uv)) return uv;
  mkdirSync(uvDir, { recursive: true });
  const archive = join(directory, asset.name);
  await downloadVerified(asset, archive);
  // Windows ships bsdtar (ZIP supported); Unix tar reads the official tarball.
  await run('tar', ['-xf', archive, '-C', uvDir, ...(asset.name.endsWith('.zip') ? [] : ['--strip-components=1', '--no-same-owner'])]);
  rmSync(archive, { force: true });
  if (!existsSync(uv)) throw new Error('Archive did not contain the expected uv binary');
  return uv;
}

async function runtime(directory) {
  return setupLock(directory, async () => {
    const python = join(directory, 'venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
    let uv = null;
    try {
      uv = await ensureUv(directory);
    } catch (error) {
      // A blocked download must not end the feature: fall back to the system interpreter.
      process.stderr.write(`Managed uv unavailable (${error.message}); falling back to the system Python.\n`);
    }
    if (!existsSync(python)) {
      if (uv) {
        process.stderr.write('Preparing a private Python 3.12 environment (first run only)...\n');
        await run(uv, ['--no-config', 'venv', '--python', '3.12', join(directory, 'venv')], {
          env: { ...process.env, UV_PYTHON_INSTALL_DIR: join(directory, 'python'), UV_PYTHON_DOWNLOADS: 'automatic' },
        });
      } else {
        const system = findSystemPython();
        if (!system) {
          throw new Error('Cannot prepare the reader runtime: the uv download failed and no Python 3.10+ was found. '
            + 'Install Python 3.10+ (or point DSH_READER_PYTHON at one) and retry.');
        }
        process.stderr.write(`Preparing a private Python ${system.version} environment with ${system.command}...\n`);
        await run(system.command, [...system.prefix, '-m', 'venv', join(directory, 'venv')]);
      }
    }
    return { python, uv };
  });
}

async function pipInstall(runtime, packages) {
  const base = runtime.uv
    ? { command: runtime.uv, args: ['--no-config', 'pip', 'install', '--python', runtime.python] }
    : { command: runtime.python, args: ['-m', 'pip', 'install'] };
  let failure = null;
  for (const index of pipIndexes()) {
    try {
      await run(base.command, [...base.args, '--only-binary', ':all:', '--index-url', index, ...packages], { filterLegacyWarnings: true });
      return index;
    } catch (error) {
      failure = error;
      process.stderr.write(`Package index ${index} failed; trying the next one.\n`);
    }
  }
  throw failure || new Error('No package index configured');
}

async function install(directory, runtime, name) {
  return setupLock(directory, async () => {
    const packages = componentPlan(name);
    const identity = createHash('sha256').update(JSON.stringify(packages)).digest('hex');
    const receipt = join(directory, `${name}.json`);
    if (existsSync(receipt) && JSON.parse(readFileSync(receipt, 'utf8')).identity === identity) return;
    process.stderr.write(`Preparing ${name} components (first use; progress follows)...\n`);
    await pipInstall(runtime, packages);
    let fontSource = 'bundled';
    if (name === 'font') {
      const fonts = join(directory, 'fonts');
      mkdirSync(fonts, { recursive: true });
      const source = join(fonts, FONT.name);
      try {
        await downloadVerified(FONT, source);
        await run(runtime.python, ['-c', INSTANCE_FONT, source, join(fonts, 'NotoSansSC-Regular.ttf')]);
      } catch (error) {
        // Restricted networks cannot reach the pinned font host; pdf.font_file() then picks a
        // system CJK face, so the feature still works instead of failing at first use.
        fontSource = 'system';
        writeFileSync(join(fonts, 'font-fallback.json'), JSON.stringify({ reason: error.message, at: new Date().toISOString() }));
        process.stderr.write(`Bundled font unavailable (${error.message}); using a system CJK font instead.\n`);
      } finally {
        rmSync(source, { force: true });
      }
    }
    writeFileSync(receipt, JSON.stringify({ identity, packages, font: fontSource }));
  });
}

async function invoke(python, args) {
  return new Promise((accept, reject) => {
    let component;
    const child = spawn(python, [join(SKILL_ROOT, 'scripts/zotero.py'), ...args], { stdio: ['inherit', 'inherit', 'pipe'], windowsHide: true });
    let tail = '';
    child.stderr.on('data', bytes => {
      process.stderr.write(bytes);
      tail = (tail + bytes.toString()).slice(-4000);
      component = /READER_COMPONENT_REQUIRED:(pdf|ocr|font)/.exec(tail)?.[1] ?? component;
    });
    child.once('error', reject);
    child.once('exit', code => accept({ code: code ?? 1, component }));
  });
}

export async function main(args = process.argv.slice(2)) {
  const base = resolve(process.env.ZOTERO_SKILL_CONFIG_DIR || join(homedir(), '.zotero-skill'));
  const directory = join(base, 'reader-runtime');
  const managed = await runtime(directory);
  for (let attempt = 0; attempt < 4; attempt++) {
    const result = await invoke(managed.python, args);
    if (result.code === 0) return 0;
    if (!result.component) return result.code;
    await install(directory, managed, result.component);
  }
  throw new Error('Component preparation did not resolve the dependency; inspect the progress log');
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().then(code => { process.exitCode = code; }).catch(error => { console.error(`Reader setup: ${error.message}`); process.exitCode = 1; });
}
