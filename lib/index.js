/**
 * dsh-zotero-skill — mount the bundled Zotero skill into the agent catalog.
 *
 * Mounting this plugin registers a skill provider on `ctx.skills` that serves
 * the `zotero/` directory shipped inside the package. Installing the plugin is
 * therefore enough: the skill appears in the catalog with no copy step into
 * `~/.dsh/skills`. The Python scripts stay next to `SKILL.md` and keep working
 * through the host's command tool.
 *
 * The provider interface is a plain object, so this package declares no runtime
 * dependencies and cannot fail on a peer range at install time.
 *
 * @module dsh-zotero-skill
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

/** Precedence rank for packaged skill providers and bundled roots. */
const BUNDLED_SKILL_RANK = 600;

/** Provider name registered on `ctx.skills`. */
export const PROVIDER_NAME = 'dsh-zotero-skill';

const PACKAGE_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SKILL_DIR = join(PACKAGE_ROOT, 'zotero');
const SKILL_FILE = join(SKILL_DIR, 'SKILL.md');

/** Public skill-name grammar: lowercase kebab-case. */
const SKILL_NAME_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/**
 * Parse the minimal YAML frontmatter used by `SKILL.md` (scalar `key: value`).
 * @param {string} text - raw file contents.
 * @returns {{ attrs: Record<string, string>, body: string }} parsed parts.
 */
export function parseFrontmatter(text) {
  const clean = String(text).replace(/^\uFEFF/, '');
  const match = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(clean);
  if (!match) return { attrs: {}, body: clean };

  /** @type {Record<string, string>} */
  const attrs = {};
  for (const line of match[1].split(/\r?\n/)) {
    const pair = /^([A-Za-z0-9_-]+)\s*:\s*(.*)$/.exec(line);
    if (!pair) continue;
    let value = pair[2].trim();
    const quoted =
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"));
    if (quoted && value.length >= 2) value = value.slice(1, -1);
    attrs[pair[1]] = value;
  }
  return { attrs, body: clean.slice(match[0].length) };
}

/**
 * Read the bundled skill into a provider candidate.
 * @param {(message: string) => void} warn - warning sink.
 * @returns {object | undefined} candidate, or `undefined` when unusable.
 */
function readCandidate(warn) {
  let parsed;
  try {
    parsed = parseFrontmatter(readFileSync(SKILL_FILE, 'utf8'));
  } catch (error) {
    warn(`cannot read ${SKILL_FILE}: ${error.message}`);
    return undefined;
  }

  const name = String(parsed.attrs.name || '').trim();
  if (!SKILL_NAME_RE.test(name)) {
    warn(`skipping bundled skill: name must be kebab-case, got ${JSON.stringify(parsed.attrs.name)}`);
    return undefined;
  }
  const description = String(parsed.attrs.description || '').trim();
  if (!description) {
    warn(`skipping ${name}: description is required`);
    return undefined;
  }

  return {
    name,
    description,
    invocation: { modelInvocable: true, userInvocable: true },
    source: 'bundled',
    provider: PROVIDER_NAME,
    path: SKILL_FILE,
    resourceBase: { kind: 'directory', path: SKILL_DIR },
    rank: BUNDLED_SKILL_RANK,
    locator: SKILL_FILE,
  };
}

/**
 * Create the bundled skill provider registered on `ctx.skills`.
 * The file is re-read on every lookup so edits show up without a restart.
 * @param {(message: string) => void} [warn] - warning sink.
 * @returns {{ name: string, list: Function, get: Function }} the provider.
 */
export function createProvider(warn = () => {}) {
  return {
    name: PROVIDER_NAME,

    /**
     * List the bundled skill.
     * @param {{ signal?: AbortSignal }} [lookup] - lookup options.
     * @returns {Promise<object[]>} candidates.
     */
    async list(lookup) {
      if (lookup?.signal?.aborted) return [];
      const candidate = readCandidate(warn);
      return candidate ? [candidate] : [];
    },

    /**
     * Load the bundled skill body.
     * @param {object} candidate - candidate from `list`.
     * @param {{ signal?: AbortSignal }} [lookup] - lookup options.
     * @returns {Promise<object | undefined>} full definition, or `undefined`.
     */
    async get(candidate, lookup) {
      if (lookup?.signal?.aborted) return undefined;
      let parsed;
      try {
        parsed = parseFrontmatter(readFileSync(candidate.locator, 'utf8'));
      } catch (error) {
        warn(`cannot load ${candidate.name}: ${error.message}`);
        return undefined;
      }
      return {
        ...candidate,
        content: parsed.body.replace(/^\s*\n/, ''),
      };
    },
  };
}

/**
 * Register the bundled skill provider.
 * @param {object} ctx - Cordis context.
 */
export function apply(ctx) {
  const warn = (message) => {
    try {
      const logger = typeof ctx?.logger === 'function' ? ctx.logger(PROVIDER_NAME) : ctx?.logger;
      if (logger && typeof logger.warn === 'function') {
        logger.warn(message);
        return;
      }
    } catch {
      /* fall through to the console */
    }
    try {
      console.warn(`[${PROVIDER_NAME}] ${message}`);
    } catch {
      /* nothing left to report to */
    }
  };

  ctx.inject(['skills'], (skillCtx) => {
    skillCtx.skills.registerProvider(() => createProvider(warn));
  });
}
