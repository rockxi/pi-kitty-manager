const $ = (selector) => document.querySelector(selector);
const make = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
let machines = [];
let selected = null;
let query = '';
let serverConnected = false;

function machineState(machine) {
  if (!machine.online) return ['offline', 'Нет связи'];
  if (!machine.daemon_connected) return ['warning', 'Daemon недоступен'];
  return ['online', 'Подключён'];
}
function badge(kind, label) {
  const element = make('span', `badge ${kind}`);
  element.append(make('span', 'badge-dot'), document.createTextNode(label));
  return element;
}
function status(agent, machine) {
  if (!machine.online || !machine.daemon_connected || agent.presence !== 'online') return ['offline', 'Нет связи'];
  if (agent.activity === 'busy') return ['busy', 'Работает'];
  if (agent.activity === 'idle') return ['idle', 'Ожидает'];
  return ['offline', 'Неизвестно'];
}
function visibleAgents(machine) {
  const q = query.trim().toLocaleLowerCase();
  return machine.agents.filter(a => !q || [a.session_name, a.cwd, a.model].some(s => s.toLocaleLowerCase().includes(q)));
}
function drawNavigation() {
  const all = $('#all-machines');
  all.classList.toggle('selected', selected === null);
  all.setAttribute('aria-current', selected === null ? 'page' : 'false');
  $('#nav-count').textContent = machines.reduce((n, m) => n + (m.online && m.daemon_connected ? m.agents.length : 0), 0);
  const nav = $('#machines-nav');
  nav.replaceChildren();
  if (!machines.length) nav.append(make('p', 'nav-empty', 'Нет подключений'));
  for (const machine of machines) {
    const button = make('button', `nav-item machine-nav${selected === machine.id ? ' selected' : ''}`);
    button.type = 'button';
    button.setAttribute('aria-current', selected === machine.id ? 'page' : 'false');
    button.append(make('span', 'nav-symbol', '▣'), make('span', 'nav-name', machine.name), make('span', `machine-dot ${machineState(machine)[0]}`));
    button.addEventListener('click', () => { closeChat(); selected = machine.id; render(); });
    nav.append(button);
  }
}
function agentRow(agent, machine) {
  const row = make('div', 'agent-row');
  const icon = make('div', 'agent-icon', 'π');
  icon.setAttribute('aria-hidden', 'true');
  const detail = make('div', 'agent-detail');
  detail.append(make('strong', 'agent-name', agent.session_name || 'Без названия'));
  const meta = make('div', 'agent-meta');
  meta.append(make('span', '', agent.model || 'Модель неизвестна'), make('span', 'meta-sep', '·'), make('span', '', agent.source === 'extension' ? 'pi extension' : 'Обнаружен локально'));
  detail.append(meta);
  row.append(icon, detail, badge(...status(agent, machine)));
  if (agent.source === 'extension') {
    const open = make('button', 'open-chat', 'Открыть диалог');
    open.type = 'button';
    open.addEventListener('click', () => openChat(machine, agent));
    const rename = make('button', 'open-chat', 'Переименовать');
    rename.type = 'button'; rename.addEventListener('click', () => openRename(machine, agent));
    row.append(rename, open);
  }
  return row;
}
function machineCard(machine) {
  const section = make('section', 'machine-card');
  const heading = make('div', 'machine-heading');
  const left = make('div', 'machine-ident');
  const icon = make('div', 'computer-icon', '▣'); icon.setAttribute('aria-hidden', 'true');
  const label = make('div'); label.append(make('h2', '', machine.name), make('p', '', `${machine.agents.length} ${plural(machine.agents.length, ['агент', 'агента', 'агентов'])}`));
  left.append(icon, label);
  const actions = make('div', 'machine-actions');
  actions.append(badge(...machineState(machine)));
  if (machine.online && machine.daemon_connected && machine.launch_enabled) {
    const create = make('button', 'open-chat', '+ Новый агент');
    create.type = 'button'; create.addEventListener('click', () => openLaunch(machine)); actions.append(create);
  }
  heading.append(left, actions);
  section.append(heading);
  if (!machine.online) section.append(make('p', 'machine-message', 'Последние данные до отключения.'));
  else if (!machine.daemon_connected) section.append(make('p', 'machine-message', 'Клиент подключён, но локальный pi-kitty daemon недоступен.'));
  const agents = visibleAgents(machine);
  if (!agents.length) {
    section.append(make('p', 'machine-empty', query ? 'Нет совпадений на этом компьютере.' : 'Нет активных pi-агентов.'));
    return section;
  }
  const groups = new Map();
  for (const agent of agents) {
    const folder = agent.cwd || 'Папка неизвестна';
    if (!groups.has(folder)) groups.set(folder, []);
    groups.get(folder).push(agent);
  }
  for (const [folder, items] of [...groups.entries()].sort((a,b) => a[0].localeCompare(b[0]))) {
    const group = make('div', 'folder-group');
    const title = make('div', 'folder-title');
    title.append(make('span', 'folder-icon', '▱'), make('span', 'folder-path', folder));
    group.append(title);
    for (const agent of items) group.append(agentRow(agent, machine));
    section.append(group);
  }
  return section;
}
function plural(n, forms) {
  return forms[n % 10 === 1 && n % 100 !== 11 ? 0 : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? 1 : 2];
}
function render() {
  drawNavigation();
  const chosen = machines.find(m => m.id === selected);
  const title = chosen ? chosen.name : 'Все агенты';
  $('#crumb').textContent = currentChat ? ($('#chat-title').textContent || 'Диалог') : title;
  $('#page-title').textContent = title;
  const online = machines.filter(m => m.online && m.daemon_connected);
  const active = online.reduce((n,m) => n + m.agents.length, 0);
  $('#subtitle').textContent = chosen ? 'Агенты и рабочие папки на этом компьютере.' : 'Ваши pi-агенты в одном месте.';
  const summary = $('#summary'); summary.replaceChildren();
  if (machines.length) {
    const number = make('strong', '', String(active));
    summary.append(number, make('span', '', `${plural(active, ['активный агент', 'активных агента', 'активных агентов'])} · ${online.length} ${plural(online.length, ['компьютер', 'компьютера', 'компьютеров'])} в сети`));
  }
  const content = $('#content'); content.replaceChildren();
  if (!machines.length) {
    const empty = make('section', 'empty');
    empty.append(make('div', 'empty-icon', '▣'), make('h2', '', serverConnected ? 'Подключите компьютер' : 'Нет связи с сервером'), make('p', '', serverConnected ? 'Запустите клиент pi-kitty на компьютере с агентами. После подключения они появятся здесь.' : 'Проверьте, что Docker-сервер запущен, и обновите страницу.'));
    if (serverConnected) {
      const code = make('code', 'setup-code', 'python3 -m pi_kitty.web_client --token-file .env');
      empty.append(code, make('small', '', 'Запустите из каталога проекта. Подробности — в README.'));
    }
    content.append(empty);
  } else {
    const shown = chosen ? [chosen] : machines;
    for (const machine of shown) content.append(machineCard(machine));
  }
  const connection = $('#connection');
  connection.replaceChildren(make('span', `connection-dot${serverConnected ? ' connected' : ''}`), make('span', '', serverConnected ? 'Сервер подключён' : 'Нет связи с сервером'));
}
async function refresh() {
  try {
    const response = await fetch('/api/machines', {cache: 'no-store'});
    if (response.status === 401) { showLogin(); return; }
    if (!response.ok) throw new Error('server error');
    const data = await response.json();
    machines = data.machines.sort((a,b) => a.name.localeCompare(b.name));
    if (selected && !machines.some(m => m.id === selected)) selected = null;
    serverConnected = true;
  } catch { serverConnected = false; }
  render();
  syncChatRoute();
}
$('#all-machines').addEventListener('click', () => { closeChat(); selected = null; render(); });
$('#search').addEventListener('input', (event) => { query = event.target.value; render(); });
document.addEventListener('keydown', (event) => {
  if (event.key === '/' && !event.metaKey && !event.ctrlKey && !event.altKey && !['INPUT','TEXTAREA'].includes(document.activeElement.tagName)) { event.preventDefault(); $('#search').focus(); }
  if (event.key === 'Escape' && document.activeElement === $('#search')) $('#search').blur();
});
let launchMachine = null;
let launchId = null;
let launchPolling = false;
function openLaunch(machine) {
  launchMachine = machine.id; launchId = null;
  const select = $('#launch-machine'); select.replaceChildren();
  for (const item of machines.filter(m => m.online && m.daemon_connected && m.launch_enabled)) {
    const option = make('option', '', item.name); option.value = item.id; select.append(option);
  }
  select.value = machine.id;
  updateLaunchFolders();
  $('#launch-status').textContent = ''; $('#launch-ack').hidden = true;
  $('#launch-dialog').showModal(); refreshLaunch();
}
function updateLaunchFolders() {
  const machine = machines.find(m => m.id === $('#launch-machine').value);
  launchMachine = machine?.id || null; launchId = null;
  const list = $('#launch-folders'); list.replaceChildren();
  for (const folder of [...new Set((machine?.agents || []).map(a => a.cwd).filter(Boolean))]) {
    const option = document.createElement('option'); option.value = folder; list.append(option);
  }
  $('#launch-cwd').value = machine?.agents.find(a => a.cwd)?.cwd || '';
  refreshLaunch();
}
async function refreshLaunch() {
  if (!$('#launch-dialog').open || !launchMachine || launchPolling) return;
  const machine = launchMachine; launchPolling = true;
  try {
    const response = await fetch('/api/launch-status?' + new URLSearchParams({machine}), {cache:'no-store'});
    if (!response.ok) throw new Error('Не удалось узнать состояние запуска.');
    const data = await response.json(); if (machine !== launchMachine) return;
    const state = data.launch?.state; launchId = data.launch?.id || null;
    const message = {queued:'Запрос ожидает передачи на компьютер…', dispatching:'Запускаем вкладку и ждём подтверждения…', unconfirmed:'Запуск не подтверждён. Проверьте kitty, прежде чем создавать агента повторно.', started:'Вкладка создана. Агент появится в списке после запуска pi.', failed:'Не удалось создать вкладку. Проверьте путь, установку pi и доступность kitty.', expired:'Компьютер не получил запрос вовремя. Можно попробовать снова.', acknowledged:'Можно отправить новый запрос.'};
    $('#launch-status').textContent = message[state] || '';
    $('#launch-submit').disabled = state === 'queued' || state === 'dispatching' || state === 'unconfirmed';
    $('#launch-ack').hidden = state !== 'unconfirmed';
  } catch (error) { $('#launch-status').textContent = error.message; $('#launch-submit').disabled = true; }
  finally { launchPolling = false; }
}
$('#launch-machine').addEventListener('change', updateLaunchFolders);
$('#launch-close').addEventListener('click', () => $('#launch-dialog').close());
$('#launch-form').addEventListener('submit', async event => {
  event.preventDefault(); $('#launch-submit').disabled = true;
  try {
    const response = await fetch('/api/launch', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({machine:launchMachine,cwd:$('#launch-cwd').value.trim()})});
    if (!response.ok) throw new Error(response.status === 409 ? 'Компьютер недоступен или предыдущий запуск ещё не подтверждён.' : 'Не удалось отправить запрос. Проверьте путь.');
    $('#launch-status').textContent = 'Запрос ожидает передачи на компьютер…'; await refreshLaunch();
  } catch (error) { $('#launch-status').textContent = error.message; await refreshLaunch(); }
});
$('#launch-ack').addEventListener('click', async () => {
  if (!launchId || !confirm('Вы проверили kitty? Новый запрос может создать ещё одну вкладку.')) return;
  const response = await fetch('/api/launch-ack', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({machine:launchMachine,id:launchId})});
  if (!response.ok) $('#launch-status').textContent = 'Состояние изменилось. Обновите страницу.';
  await refreshLaunch();
});
let renameTarget = null;
let renameInfo = null;
let renamePolling = false;
function openRename(machine, agent) {
  renameTarget = {machine: machine.id, agent: agent.agent_id}; renameInfo = null;
  $('#rename-input').value = agent.session_name || '';
  $('#rename-status').textContent = 'Проверяем связь с агентом…';
  $('#rename-submit').disabled = true; $('#rename-ack').hidden = true;
  $('#rename-dialog').showModal(); refreshRename();
}
async function refreshRename() {
  if (!$('#rename-dialog').open || !renameTarget || renamePolling) return;
  const target = renameTarget; renamePolling = true;
  try {
    const response = await fetch('/api/chat?' + new URLSearchParams(target), {cache:'no-store'});
    if (!response.ok) throw new Error('Не удалось связаться с агентом.');
    const data = await response.json(); if (target !== renameTarget) return;
    renameInfo = data;
    const state = data.rename?.state;
    const statuses = {queued:'Ожидаем передачи агенту…', dispatching:'Сохраняем имя…', unconfirmed:'Сохранение не подтверждено. Проверьте имя в kitty перед повтором.', accepted:'Имя установлено в сессии pi.', rejected:'Агент не смог сохранить имя. Проверьте сессию.', expired:'Агент не получил запрос или сессия изменилась.', acknowledged:'Можно повторить сохранение.'};
    $('#rename-status').textContent = !data.available ? 'Агент не подключён к веб-чату.' : !data.rename_supported ? 'Обновите расширение и выполните /reload в pi.' : (statuses[state] || '');
    $('#rename-submit').disabled = !data.available || !data.rename_supported || ['queued','dispatching','unconfirmed'].includes(state);
    $('#rename-ack').hidden = state !== 'unconfirmed';
  } catch (error) { $('#rename-status').textContent = error.message; $('#rename-submit').disabled = true; }
  finally { renamePolling = false; }
}
$('#rename-close').addEventListener('click', () => $('#rename-dialog').close());
$('#rename-dialog').addEventListener('close', () => { renameTarget = null; renameInfo = null; });
$('#rename-form').addEventListener('submit', async event => {
  event.preventDefault();
  const name = $('#rename-input').value.trim();
  if (!name || !renameTarget || !renameInfo?.generation || !renameInfo?.session) return;
  $('#rename-submit').disabled = true;
  try {
    const response = await fetch('/api/rename', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...renameTarget,generation:renameInfo.generation,session:renameInfo.session,name})});
    if (!response.ok) throw new Error(response.status === 409 ? 'Агент недоступен или сессия изменилась.' : 'Не удалось сохранить имя.');
    $('#rename-status').textContent = 'Ожидаем передачи агенту…'; await refreshRename();
  } catch (error) { $('#rename-status').textContent = error.message; $('#rename-submit').disabled = false; }
});
$('#rename-ack').addEventListener('click', async () => {
  if (!renameTarget || !renameInfo?.rename?.id || !confirm('Проверьте имя агента в kitty перед повторной попыткой. Продолжить?')) return;
  const response = await fetch('/api/rename-ack', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...renameTarget,id:renameInfo.rename.id})});
  if (!response.ok) $('#rename-status').textContent = 'Состояние изменилось. Обновите страницу.';
  await refreshRename();
});
let currentChat = null;
let chatGeneration = null;
let chatSession = null;
let chatImageSupported = false;
let chatCommandsSupported = false;
let chatCommands = [];
let slashIndex = 0;
let chatReading = false;
let chatSending = false;
let chatAttachments = [];
const IMAGE_LIMIT = 4 * 1024 * 1024;
function renderAttachments() {
  const container = $('#chat-attachments'); container.replaceChildren();
  chatAttachments.forEach((image, index) => {
    const item = make('div', 'chat-attachment');
    const preview = document.createElement('img'); preview.src = `data:${image.mimeType};base64,${image.data}`; preview.alt = image.name;
    const remove = make('button', 'attachment-remove', '×'); remove.type = 'button'; remove.setAttribute('aria-label', `Удалить ${image.name}`);
    remove.addEventListener('click', () => { chatAttachments.splice(index, 1); renderAttachments(); });
    item.append(preview, remove); container.append(item);
  });
}
async function addImages(files) {
  if (chatReading || !chatImageSupported) return;
  const selected = Array.from(files); const target = currentChat; const session = chatSession;
  if (!selected.length) return;
  if (selected.some(file => !['image/png','image/jpeg','image/webp'].includes(file.type)) || selected.length + chatAttachments.length > 3 || selected.reduce((sum,file) => sum+file.size,0) + chatAttachments.reduce((sum,image) => sum+image.size,0) > IMAGE_LIMIT) {
    $('#chat-error').textContent = 'Можно добавить до 3 изображений PNG, JPEG или WebP общим размером до 4 МБ.'; return;
  }
  chatReading = true; $('#chat-send').disabled = true; $('#chat-attach').disabled = true; $('#chat-error').textContent = '';
  try {
    const images = await Promise.all(selected.map(file => new Promise((resolve,reject) => {
      const reader = new FileReader(); reader.onerror = () => reject(new Error('Не удалось прочитать изображение.'));
      reader.onload = () => resolve({type:'image',mimeType:file.type,data:String(reader.result).split(',')[1],name:file.name || 'Изображение',size:file.size}); reader.readAsDataURL(file);
    })));
    if (target !== currentChat || session !== chatSession) return;
    chatAttachments.push(...images); renderAttachments();
  } catch (error) { if (target === currentChat) $('#chat-error').textContent = error.message; }
  finally { chatReading = false; if (target === currentChat) refreshChat(); }
}
function markdownContent(text) {
  const content = make('div', 'markdown');
  if (!window.marked || !window.DOMPurify) { content.textContent = text; return content; }
  const raw = marked.parse(text, {gfm:true, breaks:false});
  content.innerHTML = DOMPurify.sanitize(raw, {ALLOWED_TAGS:['p','br','strong','em','del','code','pre','blockquote','ul','ol','li','a','h1','h2','h3','h4','h5','h6','hr','table','thead','tbody','tr','th','td'], ALLOWED_ATTR:['href','title'], ALLOWED_URI_REGEXP:/^https?:\/\//i});
  content.querySelectorAll('a').forEach(link => { link.target = '_blank'; link.rel = 'noopener noreferrer'; });
  return content;
}
let chatMessages = '';
let chatPolling = false;
function openChat(machine, agent, navigate = true) {
  currentChat = {machine: machine.id, agent: agent.agent_id};
  if (navigate) history.pushState(null, '', '#/chat?' + new URLSearchParams(currentChat));
  $('#dashboard-view').hidden = true; $('#chat-page').hidden = false;
  chatGeneration = null; chatSession = null; chatImageSupported = false; chatAttachments = []; renderAttachments();
  chatMessages = '';
  $('#chat-title').textContent = agent.session_name || 'Без названия';
  $('#chat-computer').textContent = machine.name;
  $('#chat-error').textContent = '';
  $('#chat-messages').replaceChildren();
  document.title = `${agent.session_name || 'Диалог'} — Pi Kitty`;
  $('#crumb').textContent = agent.session_name || 'Диалог';
  refreshChat();
}
async function refreshChat() {
  if (!currentChat || chatPolling) return;
  const selectedChat = currentChat;
  chatPolling = true;
  try {
    const params = new URLSearchParams(selectedChat);
    const response = await fetch('/api/chat?' + params, {cache: 'no-store'});
    if (response.status === 401) { showLogin(); return; }
    if (!response.ok) throw new Error('Не удалось загрузить диалог.');
    const data = await response.json();
    if (selectedChat !== currentChat) return;
    if (chatSession && data.session !== chatSession && chatAttachments.length) {
      chatAttachments = []; renderAttachments(); $('#chat-error').textContent = 'Сессия изменилась. Прикрепите изображения заново.';
    }
    chatGeneration = data.generation; chatSession = data.session; chatImageSupported = data.image_supported === true;
    chatCommandsSupported = data.commands_supported === true; chatCommands = data.commands || []; renderSlashSuggestions();
    const delivery = data.delivery?.state;
    $('#chat-status').textContent = !data.available ? 'Диалог недоступен. Включите веб-чат в kitty и перезапустите pi.' : data.pending ? 'Сообщение ожидает передачи…' : delivery === 'unconfirmed' ? 'Передача не подтверждена. Не отправляйте повторно, пока не проверите диалог.' : delivery === 'rejected' ? 'Агент не принял сообщение. Проверьте его состояние.' : delivery === 'expired' ? 'Время доставки истекло. Сообщение не передано.' : data.busy ? 'Агент работает…' : delivery === 'accepted' ? 'Сообщение принято агентом.' : 'Агент готов';
    $('#chat-send').disabled = !data.available || data.busy || data.pending || delivery === 'unconfirmed' || chatReading || chatSending;
    $('#chat-attach').disabled = !data.available || !chatImageSupported || chatReading || chatSending;
    $('#chat-attach').title = chatImageSupported ? 'Прикрепить изображение' : 'Для изображений обновите расширение и выполните /reload в pi.';
    const serialized = JSON.stringify(data.messages);
    if (serialized !== chatMessages) {
      chatMessages = serialized;
      const list = $('#chat-messages'); list.replaceChildren();
      if (!data.messages.length) list.append(make('p', 'chat-empty', 'В этой сессии пока нет текстовых сообщений.'));
      for (const message of data.messages) {
        const item = make('div', `chat-message ${message.role}`);
        item.append(make('strong', '', message.role === 'user' ? 'Вы' : 'Агент'));
        if (message.text) item.append(message.role === 'assistant' ? markdownContent(message.text) : make('p', '', message.text));
        if (message.image_count) item.append(make('span', 'chat-image-indicator', `Изображений: ${message.image_count}`));
        list.append(item);
      }
      list.scrollTop = list.scrollHeight;
    }
  } catch (error) { $('#chat-error').textContent = error.message || 'Нет связи с сервером.'; }
  finally { chatPolling = false; }
}
function closeChat(navigate = true) {
  if (!currentChat && !location.hash.startsWith('#/chat?')) return;
  currentChat = null; chatGeneration = null; chatSession = null; chatAttachments = []; renderAttachments(); $('#chat-image-input').value = '';
  $('#chat-page').hidden = true; $('#dashboard-view').hidden = false; document.title = 'Pi Kitty — агенты';
  if (navigate) history.pushState(null, '', location.pathname + location.search);
  render();
}
function syncChatRoute() {
  if (!location.hash.startsWith('#/chat?')) { if (currentChat) closeChat(false); return; }
  const params = new URLSearchParams(location.hash.slice(7));
  const machine = machines.find(item => item.id === params.get('machine'));
  const agent = machine?.agents.find(item => item.agent_id === params.get('agent'));
  if (machine && agent && (!currentChat || currentChat.machine !== machine.id || currentChat.agent !== agent.agent_id)) openChat(machine, agent, false);
}
window.addEventListener('popstate', syncChatRoute);
$('#chat-close').addEventListener('click', () => closeChat());
function renderSlashSuggestions() {
  const list = $('#slash-list'); const input = $('#chat-input'); const match = /^\/([^\s]*)$/.exec(input.value);
  list.replaceChildren();
  if (!match || !chatCommandsSupported) { list.hidden = true; return; }
  const choices = chatCommands.filter(command => command.name.toLowerCase().includes(match[1].toLowerCase())).slice(0, 12);
  if (!choices.length) { list.hidden = true; return; }
  slashIndex = Math.min(slashIndex, choices.length - 1); list.hidden = false;
  choices.forEach((command, index) => {
    const button = make('button', 'slash-option'); button.type = 'button'; button.setAttribute('role','option'); button.setAttribute('aria-selected', String(index === slashIndex));
    button.append(make('strong', '', '/' + command.name), make('span', '', command.description || command.source));
    button.addEventListener('click', () => { input.value = '/' + command.name + ' '; list.hidden = true; input.focus(); }); list.append(button);
  });
}
$('#chat-input').addEventListener('input', () => { slashIndex = 0; renderSlashSuggestions(); });
$('#chat-input').addEventListener('keydown', event => {
  const list = $('#slash-list'); if (list.hidden) return;
  const choices = list.querySelectorAll('button');
  if (event.key === 'Escape') { event.preventDefault(); list.hidden = true; }
  else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); slashIndex = (slashIndex + (event.key === 'ArrowDown' ? 1 : -1) + choices.length) % choices.length; renderSlashSuggestions(); }
  else if (event.key === 'Tab' || event.key === 'Enter') { event.preventDefault(); choices[slashIndex]?.click(); }
});
$('#chat-attach').addEventListener('click', () => $('#chat-image-input').click());
$('#chat-image-input').addEventListener('change', event => { addImages(event.target.files); event.target.value = ''; });
document.addEventListener('paste', event => {
  if (!currentChat || $('#chat-page').hidden || (event.target !== $('#chat-input') && event.target?.closest?.('input,textarea,[contenteditable="true"]'))) return;
  const files = Array.from(event.clipboardData?.items || []).filter(item => item.kind === 'file' && item.type.startsWith('image/')).map(item => item.getAsFile()).filter(Boolean);
  if (!files.length) return;
  event.preventDefault();
  if (!chatImageSupported) { $('#chat-error').textContent = 'Для вставки изображений обновите расширение и выполните /reload в pi.'; return; }
  addImages(files);
});
$('#chat-form').addEventListener('submit', async event => {
  event.preventDefault();
  const input = $('#chat-input'); const message = input.value.trim();
  if ((!message && !chatAttachments.length) || !currentChat || !chatGeneration || chatReading || chatSending) return;
  const slash = message.startsWith('/');
  if (slash && (!chatCommandsSupported || !chatCommands.some(command => command.name === message.split(/\s+/, 1)[0].slice(1)))) { $('#chat-error').textContent = chatCommandsSupported ? 'Команда недоступна для выполнения через веб-чат. Встроенные команды доступны в kitty.' : 'Для команд обновите расширение и выполните /reload в pi.'; return; }
  if (slash && chatAttachments.length) { $('#chat-error').textContent = 'Команды нельзя отправлять вместе с изображениями.'; return; }
  const target = currentChat; chatSending = true;
  $('#chat-send').disabled = true; $('#chat-attach').disabled = true; $('#chat-error').textContent = '';
  try {
    const images = chatAttachments.map(({type,mimeType,data}) => ({type,mimeType,data}));
    const response = await fetch('/api/send', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...target, generation: chatGeneration, session:chatSession, message, images, slash})});
    if (!response.ok) throw new Error(response.status === 409 ? 'Агент занят, сессия изменилась или нужно обновить расширение.' : response.status === 413 ? 'Изображения слишком большие.' : 'Не удалось отправить сообщение.');
    if (target !== currentChat) return;
    input.value = ''; chatAttachments = []; renderAttachments(); $('#chat-status').textContent = 'Сообщение ожидает передачи…';
  } catch (error) { if (target === currentChat) $('#chat-error').textContent = error.message || 'Нет связи с сервером. Проверьте диалог перед повтором.'; }
  finally { chatSending = false; if (target === currentChat) refreshChat(); }
});
function showLogin() {
  machines = []; render();
  if (!$('#login-dialog').open) $('#login-dialog').showModal();
}
$('#login-dialog').addEventListener('cancel', event => event.preventDefault());
$('#login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.target.querySelector('button'); button.disabled = true;
  try {
    const response = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({login:$('#login-name').value, password:$('#login-password').value})});
    $('#login-password').value = '';
    if (!response.ok) throw new Error(response.status === 429 ? 'Слишком много попыток. Попробуйте позже.' : 'Неверный логин или пароль.');
    $('#login-error').textContent = ''; $('#login-dialog').close(); await refresh();
  } catch (error) { $('#login-error').textContent = error.message || 'Нет связи с сервером.'; }
  finally { button.disabled = false; }
});
$('#logout').addEventListener('click', async () => {
  await fetch('/api/logout', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
  closeChat();
  showLogin();
});
refresh();
setInterval(refresh, 3000);
setInterval(refreshChat, 2000);
setInterval(refreshLaunch, 2000);
setInterval(refreshRename, 2000);
