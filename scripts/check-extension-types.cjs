#!/usr/bin/env node
// Type-check against the installed pi API without adding a second pi runtime.
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const root = process.env.PI_PACKAGE_ROOT || path.join(
  execFileSync('npm', ['root', '-g'], {encoding: 'utf8'}).trim(), '@earendil-works/pi-coding-agent');
const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'pk-types-'));
try {
  const config = path.join(folder, 'tsconfig.json');
  fs.writeFileSync(config, JSON.stringify({compilerOptions: {
    strict: true, noEmit: true, target: 'ES2022', module: 'NodeNext', moduleResolution: 'NodeNext', skipLibCheck: true,
    typeRoots: [path.join(root, 'node_modules/@types')],
    paths: {'@earendil-works/pi-coding-agent': [path.join(root, 'dist/index.d.ts')]},
  }, files: [path.resolve(__dirname, '../pi-extension/index.ts')]}));
  execFileSync('tsc', ['-p', config], {stdio: 'inherit'});
} finally {
  fs.rmSync(folder, {recursive: true, force: true});
}
