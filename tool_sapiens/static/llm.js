/* Tool Sapiens LLM 页：面向扮演 LLM 的人。左输入、右响应。 */
'use strict';

const inputEl = document.getElementById('llm-input');
const sessionStateEl = document.getElementById('session-state');
const errorEl = document.getElementById('last-error');
const responseInput = document.getElementById('response-input');
const submitBtn = document.getElementById('submit-btn');
const chatLink = document.getElementById('chat-link');
const sessionListEl = document.getElementById('session-list');
const copyInputBtn = document.getElementById('copy-input-btn');
const modeFullBtn = document.getElementById('mode-full-btn');
const modeLatestBtn = document.getElementById('mode-latest-btn');
const inputNoteEl = document.getElementById('input-note');

let currentSid = '';
let lastPending = null;
let lastError = undefined; // 与 null 区分：undefined = 还没轮询过
let submitting = false;

/* 输入展示模式：latest = 仅最新（默认，与旧行为一致）；full = 全量输入 */
let inputMode = 'latest';

const NOTE_FULL = '全量输入：这是本轮对话完整的、发给 LLM 的全量输入'
  + '（如同真实 LLM API 每次发送的完整 messages 列表）。';
const NOTE_LATEST = '仅最新：为方便"作为人类的你"阅读，这里只展示本轮对话新增的输入部分；'
  + '完整输入可切换到"全量输入"查看。';

/* ── 提交响应 ── */

submitBtn.addEventListener('click', submitResponse);

/* 提交按钮状态：ready 可提交；busy 提交中；submitted 已提交（无待响应）；off 页面不可用 */
function setSubmitMode(mode) {
  responseInput.disabled = mode !== 'ready';
  submitBtn.disabled = mode !== 'ready';
  submitBtn.textContent =
    mode === 'busy' ? '提交中……'
    : mode === 'submitted' ? '已提交'
    : '提交响应';
}

function setInputDisplay(text, isHint) {
  inputEl.textContent = text;
  inputEl.classList.toggle('placeholder', !!isHint);
}

/* 统一的输入区渲染：内容变化时才重写 DOM（避免轮询打断滚动/选区） */
function applyInputView(view) {
  let changed = false;
  if (inputEl.textContent !== view.text
    || inputEl.classList.contains('placeholder') !== !!view.hint) {
    inputEl.textContent = view.text;
    inputEl.classList.toggle('placeholder', !!view.hint);
    inputEl.scrollTop = 0;
    changed = true;
  }
  inputEl.classList.toggle('dimmed', !!view.dim);
  copyInputBtn.hidden = !view.copy;
  const note = view.note || '';
  if (inputNoteEl.textContent !== note) {
    inputNoteEl.textContent = note;
  }
  inputNoteEl.hidden = !note;
  return changed;
}

function updateModeBar() {
  modeFullBtn.classList.toggle('active', inputMode === 'full');
  modeLatestBtn.classList.toggle('active', inputMode === 'latest');
}

function setInputMode(mode) {
  if (inputMode === mode) {
    return;
  }
  inputMode = mode;
  updateModeBar();
  poll(); // 立即按新模式重新渲染，不等下一个轮询周期
}

modeFullBtn.addEventListener('click', () => setInputMode('full'));
modeLatestBtn.addEventListener('click', () => setInputMode('latest'));
updateModeBar();

copyInputBtn.addEventListener('click', () => {
  navigator.clipboard.writeText(inputEl.textContent);
});

async function submitResponse() {
  if (!currentSid || submitting) {
    return;
  }
  submitting = true;
  setSubmitMode('busy');
  const result = await api('POST', `/api/llm/${currentSid}/response`,
    { response: responseInput.value });
  submitting = false;
  if (!result.ok) {
    setSubmitMode('ready');
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
    applyInputView({
      text: '还没有 session。去聊天页（/）新建一个，然后回这里扮演 LLM。',
      hint: true,
    });
    sessionStateEl.textContent = '无 session';
    errorEl.hidden = true;
    setSubmitMode('off');
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
    applyInputView({ text: `session ${sid} 不存在`, hint: true });
    sessionStateEl.textContent = '404';
    setSubmitMode('off');
    return;
  }

  const result = await api('GET', `/api/llm/${sid}`);
  if (result.status === 404) {
    currentSid = '';
    lastPending = null;
    applyInputView({ text: `session ${sid} 不存在`, hint: true });
    sessionStateEl.textContent = '404';
    setSubmitMode('off');
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
      const changed = applyInputView({
        text: `[执行中]\n命令输出（实时更新）：\n${view.terminal_status.output || '(暂无输出)'}`,
      });
      if (changed) {
        inputEl.scrollTop = inputEl.scrollHeight;
      }
    }
    setSubmitMode('busy');
  } else if (view.state === 'awaiting_llm') {
    sessionStateEl.textContent = '轮到你了：阅读左侧输入，在右侧写下响应';
    if (inputMode === 'full' && view.full_input) {
      applyInputView({ text: view.full_input, copy: true, note: NOTE_FULL });
    } else if (view.pending_input) {
      applyInputView({
        text: view.pending_input,
        copy: true,
        note: NOTE_LATEST,
      });
    } else {
      applyInputView({ text: '等待用户输入', hint: true, note: NOTE_LATEST });
    }
    if (view.pending_input !== lastPending) {
      lastPending = view.pending_input;
      inputEl.scrollTop = 0;
    }
    setSubmitMode(view.pending_input ? 'ready' : 'submitted');
  } else {
    sessionStateEl.textContent = '等待用户在聊天页提交新的提示词……';
    lastPending = null;
    if (inputMode === 'full' && view.full_input) {
      // 等待新提示词期间仍可阅读全量历史：颜色变暗，但可滚动、选中、复制
      applyInputView({
        text: view.full_input,
        dim: true,
        copy: true,
        note: NOTE_FULL,
      });
    } else {
      applyInputView({ text: '等待用户输入', hint: true, note: NOTE_LATEST });
    }
    setSubmitMode('submitted');
  }
  if (view.last_error !== lastError) {
    lastError = view.last_error;
    errorEl.textContent = view.last_error || '';
    errorEl.hidden = !view.last_error;
  }
}

startPolling(poll);
