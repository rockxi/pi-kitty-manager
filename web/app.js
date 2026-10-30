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
    button.addEventListener('click', () => { selected = machine.id; render(); });
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
    row.append(open);
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
  heading.append(left, badge(...machineState(machine)));
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
  $('#crumb').textContent = title;
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
}
$('#all-machines').addEventListener('click', () => { selected = null; render(); });
$('#search').addEventListener('input', (event) => { query = event.target.value; render(); });
document.addEventListener('keydown', (event) => {
  if (event.key === '/' && !event.metaKey && !event.ctrlKey && !event.altKey && !['INPUT','TEXTAREA'].includes(document.activeElement.tagName)) { event.preventDefault(); $('#search').focus(); }
  if (event.key === 'Escape' && document.activeElement === $('#search')) $('#search').blur();
});
let currentChat = null;
let chatGeneration = null;
let chatMessages = '';
let chatPolling = false;
function openChat(machine, agent) {
  currentChat = {machine: machine.id, agent: agent.agent_id};
  chatGeneration = null;
  chatMessages = '';
  $('#chat-title').textContent = agent.session_name || 'Без названия';
  $('#chat-computer').textContent = machine.name;
  $('#chat-error').textContent = '';
  $('#chat-messages').replaceChildren();
  $('#chat-dialog').showModal();
  refreshChat();
}
async function refreshChat() {
  if (!currentChat || chatPolling) return;
  const selectedChat = currentChat;
  chatPolling = true;
  try {
    const params = new URLSearchParams(selectedChat);
    const response = await fetch('/api/chat?' + params, {cache: 'no-store'});
    if (response.status === 401) { $('#chat-dialog').close(); showLogin(); return; }
    if (!response.ok) throw new Error('Не удалось загрузить диалог.');
    const data = await response.json();
    if (selectedChat !== currentChat) return;
    chatGeneration = data.generation;
    const delivery = data.delivery?.state;
    $('#chat-status').textContent = !data.available ? 'Диалог недоступен. Включите веб-чат в kitty и перезапустите pi.' : data.pending ? 'Сообщение ожидает передачи…' : delivery === 'unconfirmed' ? 'Передача не подтверждена. Не отправляйте повторно, пока не проверите диалог.' : delivery === 'rejected' ? 'Агент не принял сообщение. Проверьте его состояние.' : delivery === 'expired' ? 'Время доставки истекло. Сообщение не передано.' : data.busy ? 'Агент работает…' : delivery === 'accepted' ? 'Сообщение принято агентом.' : 'Агент готов';
    $('#chat-send').disabled = !data.available || data.busy || data.pending || delivery === 'unconfirmed';
    const serialized = JSON.stringify(data.messages);
    if (serialized !== chatMessages) {
      chatMessages = serialized;
      const list = $('#chat-messages'); list.replaceChildren();
      if (!data.messages.length) list.append(make('p', 'chat-empty', 'В этой сессии пока нет текстовых сообщений.'));
      for (const message of data.messages) {
        const item = make('div', `chat-message ${message.role}`);
        item.append(make('strong', '', message.role === 'user' ? 'Вы' : 'Агент'), make('p', '', message.text));
        list.append(item);
      }
      list.scrollTop = list.scrollHeight;
    }
    $('#chat-error').textContent = '';
  } catch (error) { $('#chat-error').textContent = error.message || 'Нет связи с сервером.'; }
  finally { chatPolling = false; }
}
$('#chat-close').addEventListener('click', () => $('#chat-dialog').close());
$('#chat-dialog').addEventListener('close', () => { currentChat = null; chatGeneration = null; });
$('#chat-form').addEventListener('submit', async event => {
  event.preventDefault();
  const input = $('#chat-input'); const message = input.value.trim();
  if (!message || !currentChat || !chatGeneration) return;
  $('#chat-send').disabled = true; $('#chat-error').textContent = '';
  try {
    const response = await fetch('/api/send', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...currentChat, generation: chatGeneration, message})});
    if (!response.ok) throw new Error(response.status === 409 ? 'Агент занят или сессия изменилась. Обновите диалог.' : 'Не удалось отправить сообщение.');
    input.value = ''; $('#chat-status').textContent = 'Сообщение ожидает передачи…';
    await refreshChat();
  } catch (error) { $('#chat-error').textContent = error.message || 'Нет связи с сервером.'; }
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
  if ($('#chat-dialog').open) $('#chat-dialog').close();
  showLogin();
});
refresh();
setInterval(refresh, 3000);
setInterval(refreshChat, 2000);
