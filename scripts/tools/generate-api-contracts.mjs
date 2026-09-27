#!/usr/bin/env node
/**
 * Generate or verify the versioned OpenAPI snapshot and TypeScript contracts.
 */

import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDirectory, '..', '..');
const mode = process.argv.at(2);
const openapiGenerator = resolve(
  repositoryRoot,
  'node_modules',
  '.bin',
  process.platform === 'win32'
    ? 'openapi-typescript.cmd'
    : 'openapi-typescript',
);
// The API imports scikit-learn/joblib for the risk module, so the interpreter
// must be one the service is installed into.
const serviceVenvPython = join(
  repositoryRoot,
  'api',
  '.venv',
  process.platform === 'win32' ? 'Scripts' : 'bin',
  process.platform === 'win32' ? 'python.exe' : 'python',
);
const pythonCommand =
  process.env.PYTHON ??
  (existsSync(serviceVenvPython) ? serviceVenvPython : 'python3');

if (!['--write', '--check'].includes(mode)) {
  throw new Error(
    'Usage: node scripts/tools/generate-api-contracts.mjs --write|--check',
  );
}

if (!existsSync(openapiGenerator)) {
  throw new Error(
    'openapi-typescript is not installed. Run `npm ci` from the repository root.',
  );
}

function run(command, args) {
  execFileSync(command, args, {
    cwd: repositoryRoot,
    stdio: 'inherit',
  });
}

function generate(outputDirectory) {
  const snapshotPath = join(outputDirectory, 'openapi.json');
  const typesDirectory = join(outputDirectory, 'src');
  const typesPath = join(typesDirectory, 'openapi.d.ts');

  run(pythonCommand, [
    'scripts/tools/export_openapi.py',
    '--output',
    snapshotPath,
  ]);
  run(openapiGenerator, [snapshotPath, '-o', typesPath]);

  return { snapshotPath, typesPath };
}

if (mode === '--write') {
  generate(resolve(repositoryRoot, 'contracts'));
  process.stdout.write(
    'Generated OpenAPI snapshot and TypeScript contracts.\n',
  );
} else {
  const temporaryDirectory = mkdtempSync(join(tmpdir(), 'rasta-contracts-'));

  try {
    const actual = generate(temporaryDirectory);
    const expected = {
      snapshotPath: resolve(
        repositoryRoot,
        'contracts',
        'openapi.json',
      ),
      typesPath: resolve(
        repositoryRoot,
        'contracts',
        'src',
        'openapi.d.ts',
      ),
    };

    for (const key of Object.keys(expected)) {
      if (!existsSync(expected[key])) {
        throw new Error(
          `Missing generated contract: ${expected[key]}. Run npm run contracts:generate.`,
        );
      }
      if (
        readFileSync(actual[key]).compare(readFileSync(expected[key])) !== 0
      ) {
        throw new Error(
          `Generated contract drift: ${expected[key]}. Run npm run contracts:generate and review the change.`,
        );
      }
    }

    process.stdout.write(
      'OpenAPI snapshot and TypeScript contracts are current.\n',
    );
  } finally {
    rmSync(temporaryDirectory, { recursive: true, force: true });
  }
}
