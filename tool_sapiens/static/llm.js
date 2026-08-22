/* Tool Sapiens LLM 页：面向扮演 LLM 的人。左输入、右响应。 */
'use strict';

const inputEl = document.getElementById('llm-input');
const sessionStateEl = document.getElementById('session-state');
const errorEl = document.getElementById('last-error');
const responseInput = document.getElementById('response-input');
const submitBtn = document.getElementById('submit-btn');
const chatLink = document.getElementById('chat-link');
const sessionListEl = document.getElementById('session-list');

let currentSid = '';
let lastPending = null;
let lastError = undefined; // 与 null 区分：undefined = 还没轮询过
let submitting = false;

/* ── 提交响应 ── */

submitBtn.addEventListener('click', submitResponse);

function setWorking(busy) {
  responseInput.disabled = busy;
  submitBtn.disabled = busy;
  submitBtn.textContent = busy ? '提交中……' : '提交响应';
}

async function submitResponse() {
  if (!currentSid || submitting) {
    return;
  }
  submitting = true;
  setWorking(true);
  const result = await api('POST', `/api/llm/${currentSid}/response`,
    { response: responseInput.value });
  submitting = false;
  setWorking(false);
  if (!result.ok) {
    errorEl.textContent = errorMessage(result);
    errorEl.hidden = false;
    return;
  }
  if (result.data.last_error) {
    responseInput.value = '';
  }
  poll(); // 立即刷新，不等下一个轮询周期
}

/* ── 轮询 ── */

async function poll() {
  const sid = getSessionId();
  if (!sid) {
    currentSid = '';
    lastPending = null;
    lastError = undefined;
    inputEl.textContent = '还没有 session。去聊天页（/）新建一个，然后回这里扮演 LLM。';
    sessionStateEl.textContent = '无 session';
    errorEl.hidden = true;
    setWorking(true);
    chatLink.hidden = true;
    return;
  }
  if (sid !== currentSid) {
    currentSid = sid;
    lastPending = null;
    lastError = undefined;
    responseInput.value = '';
  }
  chatLink.hidden = false;
  chatLink.href = '/#' + sid;

  /* session 列表 */
  const listResult = await api('GET', '/api/sessions');
  let sessionsList = [];
  if (listResult.ok && listResult.data.sessions) {
    sessionsList = listResult.data.sessions;
  }
  renderSessionList(sessionListEl, sessionsList, currentSid);

  /* 检查 session 是否仍存在于列表中 */
  const exists = sessionsList.some(s => s.id === sid);
  if (!exists) {
    currentSid = '';
    lastPending = null;
    inputEl.textContent = `session ${sid} 不存在`;
    sessionStateEl.textContent = '404';
    setWorking(true);
    return;
  }

  const result = await api('GET', `/api/llm/${sid}`);
  if (result.status === 404) {
    currentSid = '';
    lastPending = null;
    inputEl.textContent = `session ${sid} 不存在`;
    sessionStateEl.textContent = '404';
    setWorking(true);
    return;
  }
  if (!result.ok) {
    errorEl.textContent = errorMessage(result);
    errorEl.hidden = false;
    return;
  }
  const view = result.data;
  if (view.state === 'executing') {
    sessionStateEl.textContent = 'terminal 命令执行中……（聊天页可见终止按钮）';
    if (view.terminal_status) {
      inputEl.textContent = `[执行中]\n命令输出（实时更新）：\n${view.terminal_status.output || '(暂无输出)'}`;
      inputEl.scrollTop = inputEl.scrollHeight;
    }
    setWorking(true);
  } else if (view.state === 'awaiting_llm') {
    sessionStateEl.textContent = '轮到你了：阅读左侧输入，在右侧写下响应';
    if (view.pending_input !== lastPending) {
      lastPending = view.pending_input;
      inputEl.textContent = view.pending_input
        || '（当前没有待处理的输入）';
      inputEl.scrollTop = 0;
    }
    setWorking(false);
  } else {
    sessionStateEl.textContent = '等待用户在聊天页提交新的提示词……';
    setWorking(true);
  }
  if (view.last_error !== lastError) {
    lastError = view.last_error;
    errorEl.textContent = view.last_error || '';
    errorEl.hidden = !view.last_error;
  }
}

startPolling(poll);
