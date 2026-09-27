import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { englishCatalogue } from './messages';

/** Every key a screen asks for must exist in English. */
function sourceFiles(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      if (entry === 'node_modules' || entry.startsWith('.')) continue;
      const path = join(dir, entry);
      if (statSync(path).isDirectory()) walk(path);
      else if (/\.tsx?$/.test(path) && !/\.test\.tsx?$/.test(path))
        out.push(path);
    }
  };
  walk(root);
  return out;
}

const STATIC_KEY = /\bt\(\s*'([^']+)'/g;
/** `t(`incidents.status.${row.status}`)`: only the literal prefix is checkable. */
const TEMPLATE_KEY = /\bt\(\s*`([^`$]*)\$\{/g;

describe('message catalogue usage', () => {
  const files = [...sourceFiles('components'), ...sourceFiles('app')];
  const english = englishCatalogue();

  it('reads a non-trivial number of source files', () => {
    expect(files.length).toBeGreaterThan(10);
  });

  it('every literal key used in a screen exists in English', () => {
    const missing: string[] = [];
    for (const file of files) {
      const text = readFileSync(file, 'utf8');
      for (const match of text.matchAll(STATIC_KEY)) {
        if (!(match[1] in english)) missing.push(`${match[1]} (${file})`);
      }
    }
    expect(missing).toEqual([]);
  });

  it('every interpolated key has at least one English key under its prefix', () => {
    const keys = Object.keys(english);
    const unmatched: string[] = [];
    for (const file of files) {
      const text = readFileSync(file, 'utf8');
      for (const match of text.matchAll(TEMPLATE_KEY)) {
        const prefix = match[1];
        if (prefix && !keys.some((key) => key.startsWith(prefix))) {
          unmatched.push(`${prefix}* (${file})`);
        }
      }
    }
    expect(unmatched).toEqual([]);
  });
});
