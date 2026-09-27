#!/usr/bin/env node
/**
 * Drops vinext's downloaded-font cache when it points outside this checkout.
 */
import { existsSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const client = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const cache = join(client, '.vinext', 'fonts');

if (existsSync(cache)) {
  const stale = readdirSync(cache, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .some((entry) => {
      const sheet = join(cache, entry.name, 'style.css');
      if (!existsSync(sheet)) return false;
      const paths = [
        ...readFileSync(sheet, 'utf8').matchAll(/url\(([^)]+)\)/g),
      ].map((m) => m[1]);
      return paths.some(
        (path) => path.startsWith('/') && !path.startsWith(cache),
      );
    });
  if (stale) {
    rmSync(cache, { recursive: true, force: true });
    console.log(
      'Removed a font cache written for another folder; the build downloads it again.',
    );
  }
}
