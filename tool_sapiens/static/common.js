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
