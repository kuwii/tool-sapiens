/* Tool Sapiens 前端共享逻辑：API 封装、session 选择、轮询、工具函数。 */
'use strict';

const POLL_INTERVAL_MS = 1000;
const SESSION_KEY = 'tool-sapiens-session';

async function api(method, url, body) {
  const options = { method };
  if (body !== undefined) {
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(body);
  }
  const resp = await fetch(url, options);
  let data = null;
  try { data = await resp.json(); } catch (e) { /* 非 JSON 响应忽略 */ }
  return { ok: resp.ok, status: resp.status, data };
}

function getSessionId() {
  const hash = location.hash.replace(/^#/, '').trim();
  if (/^[0-9a-f]+$/.test(hash)) {
    return hash;
  }
  return localStorage.getItem(SESSION_KEY) || '';
}

function setSessionId(sid) {
  localStorage.setItem(SESSION_KEY, sid);
  history.replaceState(null, '', '#' + sid);
}

function startPolling(fn) {
  fn();
  setInterval(fn, POLL_INTERVAL_MS);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function errorMessage(result) {
  return (result.data && result.data.error) || `请求失败（HTTP ${result.status}）`;
}

/* ── 共享：session 列表渲染 ── */

function renderSessionList(container, sessions, currentSid) {
  var seen = new Set();
  var unique = [];
  for (var i = 0; i < sessions.length; i++) {
    if (!seen.has(sessions[i].id)) {
      seen.add(sessions[i].id);
      unique.push(sessions[i]);
    }
  }
  container.textContent = '';
  for (var j = 0; j < unique.length; j++) {
    (function (s) {
      var item = el('div', 'session-item' + (s.id === currentSid ? ' active' : ''), '');
      var title = s.title || ('session ' + s.id);
      item.textContent = title.length > 30 ? title.slice(0, 30) + '\u2026' : title;
      item.title = title + ' (' + s.id + ')';
      item.addEventListener('click', function () {
        setSessionId(s.id);
      });
      container.appendChild(item);
    })(unique[j]);
  }
}
