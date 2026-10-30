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
  process.env.XDG_CONFIG_HOME = folder; process.env.PI_KITTY_WEB_TOKEN_FILE = token; process.env.PI_KITTY_WEB_CHAT = '1'; process.env.PI_KITTY_WEB_SERVER = 'https://agents.rockxi.ru';
  const requests = [], sent = [];
  global.fetch = async (url, options) => {
    requests.push({url, body: JSON.parse(options.body), auth:options.headers.Authorization});
    return {ok:true, json:async () => ({message:requests.length === 1 ? {id:'command-id',text:'hello'} : null})};
  };
  const {WebChat} = await jiti.import(path.join(__dirname, '../pi-extension/web-chat.ts'));
  const ctx = {isIdle:()=>true, sessionManager:{getSessionId:()=> 'session-a', getBranch:()=>[
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
    assert.equal(requests[0].url, 'https://agents.rockxi.ru/api/chat-poll');
    assert.equal(requests[0].body.machine, 'a-machine');
    assert.deepEqual(requests[0].body.messages, [{role:'user',text:'previous',image_count:0},{role:'assistant',text:'answer',image_count:0}]);
    assert.deepEqual(requests[1].body.receipt, {id:'command-id',state:'accepted'});
    assert.match(requests[0].auth, /^Bearer v1\.\d+\.[a-f0-9]{64}$/);
  } finally { chat.stop(); global.fetch = originalFetch; process.env = old; fs.rmSync(folder, {recursive:true,force:true}); }
});

test('slash dispatch requires a listed command and explicitly expands templates', async () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'pk-slash-'));
  const old = {...process.env}; const originalFetch = global.fetch;
  fs.mkdirSync(path.join(folder, 'pi-kitty/web-client'), {recursive:true});
  fs.writeFileSync(path.join(folder, 'pi-kitty/web-client/machine-id'), 'a-machine');
  const token = path.join(folder, '.env'); fs.writeFileSync(token, 'PI_KITTY_WEB_TOKEN=' + 'x'.repeat(40));
  process.env.XDG_CONFIG_HOME = folder; process.env.PI_KITTY_WEB_TOKEN_FILE = token; process.env.PI_KITTY_WEB_CHAT = '1';
  const requests = [], sent = [];
  global.fetch = async (url, options) => { requests.push(JSON.parse(options.body)); return {ok:true,json:async()=>({message:requests.length === 1 ? {id:'slash-id',text:'/skill:test args',session:'session-a',slash:true} : null})}; };
  const {WebChat} = await jiti.import(path.join(__dirname, '../pi-extension/web-chat.ts'));
  const ctx = {isIdle:()=>true,sessionManager:{getSessionId:()=> 'session-a',getBranch:()=>[]}};
  const pi = {getCommands:()=>[{name:'skill:test',description:'test',source:'skill'}],sendUserMessage:(...args)=>sent.push(args)};
  const chat = new WebChat(pi,ctx,'agent','generation');
  try {
    chat.start(); for (let i=0; i<70 && requests.length<2; i++) await wait(50);
    assert.equal(requests[0].commands_supported,true); assert.equal(requests[0].commands[0].name,'skill:test');
    assert.equal(sent[0][0],'/skill:test args'); assert.equal(sent[0][1].expandPromptTemplates,true);
    assert.deepEqual(requests[1].receipt,{id:'slash-id',state:'accepted'}); assert.equal(sent.length,1);
  } finally {chat.stop(); global.fetch=originalFetch; process.env=old; fs.rmSync(folder,{recursive:true,force:true});}
});

test('image commands are delivered once to the intended session through the pi API', async () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'pk-image-'));
  const old = {...process.env}; const originalFetch = global.fetch;
  fs.mkdirSync(path.join(folder, 'pi-kitty/web-client'), {recursive:true});
  fs.writeFileSync(path.join(folder, 'pi-kitty/web-client/machine-id'), 'a-machine');
  const token = path.join(folder, '.env'); fs.writeFileSync(token, 'PI_KITTY_WEB_TOKEN=' + 'x'.repeat(40));
  process.env.XDG_CONFIG_HOME = folder; process.env.PI_KITTY_WEB_TOKEN_FILE = token; process.env.PI_KITTY_WEB_CHAT = '1';
  const requests = [], sent = [];
  const image = {type:'image',mimeType:'image/png',data:'iVBORw0KGgo='};
  global.fetch = async (url, options) => { requests.push(JSON.parse(options.body)); return {ok:true,json:async()=>({message:requests.length === 1 ? {id:'image-id',text:'look',images:[image],session:'session-a'} : null})}; };
  const {WebChat} = await jiti.import(path.join(__dirname, '../pi-extension/web-chat.ts'));
  const ctx = {isIdle:()=>true,sessionManager:{getSessionId:()=> 'session-a',getBranch:()=>[{type:'message',message:{role:'user',content:[image]}}]}};
  const chat = new WebChat({sendUserMessage:(...args)=>sent.push(args)},ctx,'agent','generation');
  try {
    chat.start(); for (let i=0; i<70 && requests.length<2; i++) await wait(50);
    assert.deepEqual(sent[0][0],[{type:'text',text:'look'},image]);
    assert.equal(requests[0].image_supported,true);
    assert.deepEqual(requests[0].messages,[{role:'user',text:'',image_count:1}]);
    assert.deepEqual(requests[1].receipt,{id:'image-id',state:'accepted'});
    assert.equal(sent.length,1);
  } finally {chat.stop(); global.fetch=originalFetch; process.env=old; fs.rmSync(folder,{recursive:true,force:true});}
});

test('rename uses the pi session API and acknowledges once even while busy', async () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'pk-rename-'));
  const old = {...process.env}; const originalFetch = global.fetch;
  fs.mkdirSync(path.join(folder, 'pi-kitty/web-client'), {recursive:true});
  fs.writeFileSync(path.join(folder, 'pi-kitty/web-client/machine-id'), 'a-machine');
  const token = path.join(folder, '.env'); fs.writeFileSync(token, 'PI_KITTY_WEB_TOKEN=' + 'x'.repeat(40));
  process.env.XDG_CONFIG_HOME = folder; process.env.PI_KITTY_WEB_TOKEN_FILE = token; process.env.PI_KITTY_WEB_CHAT = '1';
  const requests = [], names = [];
  global.fetch = async (url, options) => { requests.push(JSON.parse(options.body)); return {ok:true,json:async()=>({rename:requests.length === 1 ? {id:'rename-id',name:'Новое имя',session:'session-a'} : null})}; };
  const {WebChat} = await jiti.import(path.join(__dirname, '../pi-extension/web-chat.ts'));
  const ctx = {isIdle:()=>false,sessionManager:{getSessionId:()=> 'session-a',getBranch:()=>[]}};
  const chat = new WebChat({setSessionName:name=>names.push(name)},ctx,'agent','generation');
  try {
    chat.start(); for (let i=0; i<70 && requests.length<2; i++) await wait(50);
    assert.deepEqual(names,['Новое имя']);
    assert.equal(requests[0].rename_supported,true); assert.equal(requests[0].session,'session-a');
    assert.deepEqual(requests[1].rename_receipt,{id:'rename-id',state:'accepted'});
  } finally {chat.stop(); global.fetch=originalFetch; process.env=old; fs.rmSync(folder,{recursive:true,force:true});}
});

test('chat URL security rejects unencrypted remote and disabled verification', async () => {
  const {serverOrigin} = await jiti.import(path.join(__dirname, '../pi-extension/web-chat.ts'));
  assert.equal(serverOrigin('http://localhost:8500/'), 'http://127.0.0.1:8500');
  assert.equal(serverOrigin('https://agents.rockxi.ru/'), 'https://agents.rockxi.ru');
  for (const url of ['http://agents.rockxi.ru', 'http://localhost:9000', 'https://user@agents.rockxi.ru', 'https://@agents.rockxi.ru', 'https://agents.rockxi.ru/path', 'https://agents.rockxi.ru/?query=yes']) assert.throws(() => serverOrigin(url));
  const old = process.env.NODE_TLS_REJECT_UNAUTHORIZED;
  try { process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0'; assert.throws(() => serverOrigin('https://agents.rockxi.ru')); }
  finally { if (old === undefined) delete process.env.NODE_TLS_REJECT_UNAUTHORIZED; else process.env.NODE_TLS_REJECT_UNAUTHORIZED = old; }
});
