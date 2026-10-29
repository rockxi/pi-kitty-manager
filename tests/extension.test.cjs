const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const {createRequire} = require('node:module');
const {test} = require('node:test');

const root = process.env.PI_PACKAGE_ROOT || path.join(
  execFileSync('npm', ['root', '-g'], {encoding: 'utf8'}).trim(), '@earendil-works/pi-coding-agent');
const hostRequire = createRequire(path.join(root, 'package.json'));
const {createJiti} = hostRequire('jiti');
const jiti = createJiti(__filename);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function eventually(fn, timeout = 4000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { if (fn()) return; await sleep(10); }
  assert.ok(fn(), 'expected condition before deadline');
}

test('extension publishes metadata, parallel tool state, reload identity and reconnect', async () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'pk-ext-'));
  const socketPath = path.join(folder, 'd.sock');
  const original = process.env.PI_KITTY_SOCKET;
  process.env.PI_KITTY_SOCKET = socketPath;
  const peers = new Set();
  const messages = [];
  function makeServer() {
    return net.createServer(sock => {
      peers.add(sock);
      sock.on('close', () => peers.delete(sock));
      sock.on('error', () => {});
      let buffer = '';
      sock.on('data', data => {
        buffer += data.toString();
        let index;
        while ((index = buffer.indexOf('\n')) >= 0) {
          const message = JSON.parse(buffer.slice(0, index));
          buffer = buffer.slice(index + 1);
          messages.push(message);
          sock.write(JSON.stringify({v: 1, id: message.id, result: {accepted: true}}) + '\n');
        }
      });
    });
  }
  let server = makeServer();
  await new Promise(resolve => server.listen(socketPath, resolve));
  const handlers = new Map();
  const api = {on: (event, fn) => {handlers.set(event, fn); return () => {};}, registerCommand: () => {}};
  let idle = true, name = 'Настоящий диалог';
  const ctx = {cwd: '/work/backend', mode: 'tui', isIdle: () => idle,
    model: {provider: 'test', id: 'model'},
    sessionManager: {getSessionId: () => 'session-a', getSessionName: () => name}};
  const latest = () => messages.filter(m => m.method !== 'agent.unregister').at(-1)?.params;
  try {
    const extension = await jiti.import(path.join(__dirname, '../pi-extension/index.ts'), {default: true});
    extension(api);
    assert.equal(messages.length, 0, 'factory must not open resources');
    handlers.get('session_start')({}, ctx);
    await eventually(() => latest()?.session_name === name);
    const first = latest();
    assert.equal(first.activity, 'idle');
    assert.equal(first.pid, process.pid);
    assert.equal(first.cwd, '/work/backend');
    assert.equal(first.session_id, 'session-a');
    idle = false;
    handlers.get('before_agent_start')({}, ctx);
    handlers.get('tool_execution_start')({toolCallId: 'a'}, ctx);
    handlers.get('tool_execution_start')({toolCallId: 'b'}, ctx);
    await eventually(() => latest()?.phase === 'tools');
    assert.equal(latest().activity, 'busy');
    handlers.get('tool_execution_end')({toolCallId: 'a'}, ctx);
    await sleep(60);
    assert.equal(latest().phase, 'tools', 'one tool remains active');
    assert.equal(handlers.has('agent_end'), false, 'low-level end must not imply idle');
    idle = true;
    handlers.get('agent_settled')({}, ctx);
    await eventually(() => latest()?.activity === 'idle');
    name = 'Переименованный диалог';
    handlers.get('session_info_changed')({}, ctx);
    await eventually(() => latest()?.session_name === name);
    handlers.get('session_shutdown')({}, ctx);
    handlers.get('session_start')({}, ctx);
    await eventually(() => latest()?.generation !== first.generation);
    assert.equal(latest().agent_id, first.agent_id, 'reload preserves process identity');
    const generation = latest().generation;
    for (const peer of peers) peer.destroy();
    await new Promise(resolve => server.close(resolve));
    server = makeServer();
    await new Promise(resolve => server.listen(socketPath, resolve));
    const count = messages.length;
    await eventually(() => messages.length > count && messages.at(-1).method === 'agent.register');
    assert.equal(latest().generation, generation, 'reconnect keeps runtime generation');
    assert.equal(latest().agent_id, first.agent_id);
    assert.ok(messages.every(m => m.v === 1));
    assert.ok(messages.every(m => !('prompt' in m.params) && !('messages' in m.params)));
  } finally {
    handlers.get('session_shutdown')?.({}, ctx);
    await sleep(120);
    for (const peer of peers) peer.destroy();
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(folder, {recursive: true, force: true});
    if (original === undefined) delete process.env.PI_KITTY_SOCKET;
    else process.env.PI_KITTY_SOCKET = original;
  }
});
