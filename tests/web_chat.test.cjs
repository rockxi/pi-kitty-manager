const assert = require('node:assert/strict');
const {test} = require('node:test');
const {execFileSync} = require('node:child_process');
const {createRequire} = require('node:module');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const root = process.env.PI_PACKAGE_ROOT || path.join(execFileSync('npm', ['root', '-g'], {encoding:'utf8'}).trim(), '@earendil-works/pi-coding-agent');
const {createJiti} = createRequire(path.join(root, 'package.json'))('jiti');
const jiti = createJiti(__filename);
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

test('opt-in chat sends active branch and accepts a command once', async () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'pk-chat-'));
  const old = {...process.env}; const originalFetch = global.fetch;
  fs.mkdirSync(path.join(folder, 'pi-kitty/web-client'), {recursive:true});
  fs.writeFileSync(path.join(folder, 'pi-kitty/web-client/machine-id'), 'a-machine');
  const token = path.join(folder, '.env'); fs.writeFileSync(token, 'PI_KITTY_WEB_TOKEN=' + 'x'.repeat(40));
  process.env.XDG_CONFIG_HOME = folder; process.env.PI_KITTY_WEB_TOKEN_FILE = token; process.env.PI_KITTY_WEB_CHAT = '1';
  const requests = [], sent = [];
  global.fetch = async (url, options) => {
    requests.push({url, body: JSON.parse(options.body), auth:options.headers.Authorization});
    return {ok:true, json:async () => ({message:requests.length === 1 ? {id:'command-id',text:'hello'} : null})};
  };
  const {WebChat} = await jiti.import(path.join(__dirname, '../pi-extension/web-chat.ts'));
  const ctx = {isIdle:()=>true, sessionManager:{getBranch:()=>[
    {type:'message',message:{role:'user',content:'previous'}},
    {type:'message',message:{role:'assistant',content:[{type:'text',text:'answer'}]}},
    {type:'message',message:{role:'toolResult',content:[{type:'text',text:'secret'}]}},
  ]}};
  const chat = new WebChat({sendUserMessage:(...a)=>sent.push(a)}, ctx, 'agent', 'generation');
  try {
    chat.start();
    for (let i=0; i<70 && requests.length<2; i++) await wait(50);
    assert.equal(sent.length, 1);
    assert.equal(sent[0][0], 'hello'); assert.equal(sent[0][1].expandPromptTemplates, false);
    assert.equal(requests[0].url, 'http://127.0.0.1:8500/api/chat-poll');
    assert.deepEqual(requests[0].body.messages, [{role:'user',text:'previous'},{role:'assistant',text:'answer'}]);
    assert.deepEqual(requests[1].body.receipt, {id:'command-id',state:'accepted'});
    assert.match(requests[0].auth, /^Bearer v1\.\d+\.[a-f0-9]{64}$/);
  } finally { chat.stop(); global.fetch = originalFetch; process.env = old; fs.rmSync(folder, {recursive:true,force:true}); }
});
