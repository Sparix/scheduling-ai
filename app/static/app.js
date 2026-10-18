const messages = document.querySelector('#messages');
const form = document.querySelector('#chat-form');
const input = document.querySelector('#message');
const send = document.querySelector('#send');
const status = document.querySelector('#status');
const account = document.querySelector('#account');
const clock = document.querySelector('#clock');
let sessionId = null;

function addMessage(label, text, className = '') {
  const article = document.createElement('article');
  article.className = className;
  const heading = document.createElement('strong');
  heading.textContent = label;
  article.append(heading);
  if (text) {
    const paragraph = document.createElement('p');
    paragraph.textContent = text;
    article.append(paragraph);
  }
  messages.append(article);
  messages.scrollTo({top: messages.scrollHeight, behavior: 'smooth'});
  return article;
}

function addJSON(parent, label, data, open = false) {
  const details = document.createElement('details');
  details.open = open;
  const summary = document.createElement('summary');
  summary.textContent = label;
  const pre = document.createElement('pre');
  pre.textContent = JSON.stringify(data, null, 2);
  details.append(summary, pre);
  parent.append(details);
}

function stepLabel(step) {
  if (step.target) return `Navigate → ${step.kind} ${step.target}`;
  if (step.action) return `Action → ${step.action}: ${step.applied ? 'applied' : 'refused'}`;
  return `Handoff → ${step.agent} agent`;
}

function render(body) {
  for (const call of body.tool_calls || []) {
    const article = addMessage('Tool · ' + call.tool, '', call.error ? 'tool error' : 'tool');
    addJSON(article, 'Arguments', JSON.parse(call.arguments || '{}'));
    addJSON(article, 'Result', call.error || call.result);
  }
  const result = body.result;
  if (result.type === 'error') {
    addMessage('Assistant', result.message, 'error');
    return;
  }
  const article = addMessage('Assistant', result.text);
  for (const step of result.steps || []) addJSON(article, stepLabel(step), step, true);
  if (result.options?.length) {
    const options = document.createElement('div');
    options.className = 'options';
    for (const option of result.options) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = option;
      button.addEventListener('click', () => submit(option));
      options.append(button);
    }
    article.append(options);
  }
}

async function submit(text) {
  if (!text || send.disabled) return;
  for (const button of messages.querySelectorAll('.options button')) button.disabled = true;
  send.disabled = true;
  status.textContent = 'Working…';
  input.value = '';
  addMessage('You', text);
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Demo-User': account.value},
      body: JSON.stringify({message: text, session_id: sessionId}),
    });
    const body = await response.json();
    if (body.session_id) sessionId = body.session_id;
    render(body);
  } catch {
    addMessage('Error', 'The request failed.', 'error');
  } finally {
    send.disabled = false;
    status.textContent = '';
    input.focus();
  }
}

async function loadAccounts() {
  const body = await (await fetch('/api/context')).json();
  for (const user of body.accounts) {
    const option = document.createElement('option');
    option.value = user.user_id;
    option.textContent = `${user.name} (${user.role})`;
    account.append(option);
  }
  account.value = body.user.user_id;
  clock.textContent = 'Now: ' + body.now.replace('T', ' ');
}

account.addEventListener('change', () => {
  sessionId = null;
  messages.replaceChildren();
  addMessage('System', 'Signed in as ' + account.selectedOptions[0].textContent, 'muted');
});

form.addEventListener('submit', event => {
  event.preventDefault();
  submit(input.value.trim());
});

loadAccounts();
