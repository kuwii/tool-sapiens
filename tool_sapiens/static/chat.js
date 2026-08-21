/* Tool Sapiens 聊天页：面向 agent 用户。 */
'use strict';

const logEl = document.getElementById('chat-log');
const statusEl = document.getElementById('status');
const inputArea = document.getElementById('input-area');
const promptInput = document.getElementById('prompt-input');
const sendBtn = document.getElementById('send-btn');
const newSessionBtn = document.getElementById('new-session');
const sessionListEl = document.getElementById('session-list');
const killBtn = document.getElementById('kill-btn');
const llmLink = document.getElementById('llm-link');

let currentSid = '';
let renderedEvents = 0;

newSessionBtn.addEventListener('click', async () => {
  const result = await api('POST', '/api/sessions');
  if (!result.ok) {
    statusEl.textContent = errorMessage(result);
    return;
  }
  const sid = result.data.session.meta.id;
  setSessionId(sid);
  resetView();
  statusEl.textContent = '';
  promptInput.focus();
});

sendBtn.addEventListener('click', sendPrompt);
promptInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendPrompt();
  }
});

killBtn.addEventListener('click', async () => {
  if (!currentSid || killBtn.disabled) {
    return;
  }
  killBtn.disabled = true;
  killBtn.textContent = '终止中……';
  const result = await api('POST', `/api/llm/${currentSid}/kill`);
  if (!result.ok) {
    statusEl.textContent = errorMessage(result);
    killBtn.disabled = false;
    killBtn.textContent = '终止';
    return;
  }
  // 立即刷新轮询
  poll();
});

function resetView() {
  renderedEvents = 0;
  logEl.textContent = '';
  promptInput.value = '';
}

/* renderSessionList — 已在 common.js 中提供 */

async function sendPrompt() {
  const text = promptInput.value.trim();
  if (!text || !currentSid || sendBtn.disabled) {
    return;
  }
  sendBtn.disabled = true;
  const result = await api('POST', `/api/sessions/${currentSid}/prompt`, { prompt: text });
  if (!result.ok) {
    statusEl.textContent = errorMessage(result);
    sendBtn.disabled = false;
    return;
  }
  promptInput.value = '';
  statusEl.textContent = '';
  // 状态与事件由下一轮轮询渲染，届时按 session 状态解禁输入区
}

function appendEvent(event) {
  const block = el('div', 'msg msg-' + event.type);
  if (event.type === 'tool_call') {
    const paramsText = JSON.stringify(event.params, null, 2);
    const header = document.createElement('div');
    header.className = 'msg-tool-header';
    header.innerHTML = '<span class="msg-role">工具调用</span><span class="msg-tool-name">' + escapeHtml(event.name) + '</span><span class="msg-chevron">›</span>';
    const content = document.createElement('div');
    content.className = 'msg-tool-content';
    content.textContent = paramsText;
    header.addEventListener('click', function () {
      block.classList.toggle('expanded');
    });
    block.appendChild(header);
    block.appendChild(content);
  } else if (event.type === 'tool_result') {
    const result = event.result;
    let resultText = '';
    let isError = false;
    if (result && result.ok) {
      resultText = result.text || '';
    } else if (result) {
      resultText = '失败：' + (result.text || '');
      isError = true;
    } else {
      resultText = '(无结果)';
    }
    const firstLine = resultText.split('\n')[0] || '';
    const preview = firstLine.length > 80 ? firstLine.slice(0, 80) + '…' : firstLine;
    const header = document.createElement('div');
    header.className = 'msg-tool-header';
    header.innerHTML = '<span class="msg-role">工具结果</span><span class="msg-tool-preview">' + escapeHtml(preview) + '</span><span class="msg-chevron">›</span>';
    if (isError) {
      header.classList.add('msg-error');
    }
    const content = document.createElement('div');
    content.className = 'msg-tool-content' + (isError ? ' msg-error' : '');
    content.textContent = resultText;
    header.addEventListener('click', function () {
      block.classList.toggle('expanded');
    });
    block.appendChild(header);
    block.appendChild(content);
  } else {
    const roleLabel = event.type === 'user_prompt' ? '用户' : 'LLM';
    block.appendChild(el('div', 'msg-role', roleLabel));
    const body = el('div', 'msg-text', event.text);
    block.appendChild(body);
  }
  logEl.appendChild(block);
  logEl.scrollTop = logEl.scrollHeight;
}

function escapeHtml(text) {
  var div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

async function poll() {
  // 加载 session 列表
  const listResult = await api('GET', '/api/sessions');
  let sessionsList = [];
  if (listResult.ok && listResult.data.sessions) {
    sessionsList = listResult.data.sessions;
  }
    renderSessionList(sessionListEl, sessionsList, currentSid, true);

  const sid = getSessionId();
  llmLink.href = '/llm#' + (sid || '');
  if (!sid) {
    statusEl.textContent = '请选择或新建一个 session';
    killBtn.hidden = true;
    return;
  }
  // 检查 session 是否仍存在于列表中
  const exists = sessionsList.some(s => s.id === sid);
  if (!exists) {
    currentSid = '';
    resetView();
    statusEl.textContent = `session ${sid} 不存在`;
    killBtn.hidden = true;
    return;
  }
  if (sid !== currentSid) {
    currentSid = sid;
    resetView();
  }
  const result = await api('GET', `/api/sessions/${sid}`);
  if (result.status === 404) {
    currentSid = '';
    resetView();
    statusEl.textContent = `session ${sid} 不存在`;
    killBtn.hidden = true;
    return;
  }
  if (!result.ok) {
    statusEl.textContent = errorMessage(result);
    return;
  }
  const session = result.data.session;
  while (renderedEvents < session.events.length) {
    appendEvent(session.events[renderedEvents]);
    renderedEvents += 1;
  }
  const awaiting = session.state === 'awaiting_llm';
  const executing = session.state === 'executing';
  promptInput.disabled = awaiting || executing;
  sendBtn.disabled = awaiting || executing;
  inputArea.classList.toggle('disabled', awaiting || executing);
  killBtn.hidden = !executing;
  if (executing) {
    statusEl.textContent = '命令执行中……';
    killBtn.disabled = false;
    killBtn.textContent = '终止';
  } else if (awaiting) {
    statusEl.textContent = '等待"LLM"响应……（切到 /llm 页扮演它）';
  } else {
    statusEl.textContent = '';
    if (!awaiting && !executing && document.activeElement !== promptInput) {
      promptInput.focus();
    }
  }
}

startPolling(poll);
