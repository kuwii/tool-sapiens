"""session 存储：JSON 文件后端，服务重启后状态完整恢复。

每个 session 一个 JSON 文件，存放在数据目录中。
session 结构（持久化格式）：
{meta: {id, title, created_at}, events: [], state, pending_input, last_error}
事件类型：user_prompt / llm_output / tool_call / tool_result（append-only）。

模块级 API 保持与内存版一致，调用方无需改动。
"""

from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from datetime import datetime, timezone

_LOCK = threading.Lock()  # 保护 _STORE / _LOCKS 映射本身
_DATA_DIR: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _data_dir() -> str:
    """返回 session 数据目录，不存在则创建。"""
    global _DATA_DIR
    if _DATA_DIR is None:
        raise RuntimeError('FileStore 未初始化，请先调用 server.main() 或 init_store()')
    return _DATA_DIR


def _session_file(sid: str) -> str:
    return os.path.join(_data_dir(), f'{sid}.json')


def _generate_sid() -> str:
    """生成不与已有文件冲突的 8 位 hex ID。"""
    for _ in range(100):
        sid = uuid.uuid4().hex[:8]
        if not os.path.isfile(_session_file(sid)):
            return sid
    raise RuntimeError('无法生成不冲突的 session ID（极罕见）')


def _generate_title(text: str) -> str:
    """从用户提示词生成 session 标题：取前 30 个字符（去掉首尾空白）。"""
    title = text.strip()
    if len(title) > 30:
        title = title[:30] + '…'
    return title if title else '新 session'


def _load_session(sid: str) -> dict | None:
    """从磁盘加载 session 数据（不含 terminal_task）。"""
    path = _session_file(sid)
    if not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _save_session(session: dict):
    """原子写入 session 到磁盘。先 pop terminal_task，写完后恢复。"""
    task = session.pop('terminal_task', None)
    try:
        tmp = _session_file(session['meta']['id']) + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(session, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _session_file(session['meta']['id']))
    finally:
        if task is not None:
            session['terminal_task'] = task


# ── 内存缓存 ──────────────────────────────────────────────────────────
# 热路径（轮询）从内存读，写时同步落盘。
_STORE: dict[str, dict] = {}
_LOCKS: dict[str, threading.Lock] = {}


def init_store(data_dir: str):
    """初始化文件存储：创建数据目录、加载已有 session 到内存缓存。"""
    global _DATA_DIR
    _DATA_DIR = data_dir
    os.makedirs(data_dir, exist_ok=True)
    with _LOCK:
        _STORE.clear()
        _LOCKS.clear()
        # 扫描数据目录，加载所有 .json 文件
        for name in os.listdir(data_dir):
            if not name.endswith('.json'):
                continue
            sid = name[:-5]
            data = _load_session(sid)
            if data is not None:
                _STORE[sid] = data
                _LOCKS[sid] = threading.Lock()


def create_session() -> dict:
    # 未初始化时自动用临时目录（兼容不关心持久化的测试）
    if _DATA_DIR is None:
        import tempfile
        tmp = tempfile.mkdtemp(prefix='tool-sapiens-')
        init_store(tmp)
    with _LOCK:
        sid = _generate_sid()
        session = {
            'meta': {'id': sid, 'title': '', 'created_at': _now()},
            'events': [],
            'state': 'idle',
            'pending_input': None,
            'last_error': None,
        }
        _STORE[sid] = session
        _LOCKS[sid] = threading.Lock()
        _save_session(session)
        return session


def get_session(sid: str):
    with _LOCK:
        return _STORE.get(sid)


def get_lock(sid: str):
    with _LOCK:
        return _LOCKS.get(sid)


def list_sessions() -> list:
    with _LOCK:
        return [copy.deepcopy(s['meta']) for s in _STORE.values()]


def append_event(session: dict, event_type: str, **payload) -> dict:
    event = {'type': event_type, 'timestamp': _now()}
    event.update(payload)
    session['events'].append(event)
    return event


def set_title(session: dict, title: str):
    """设置 session 标题并落盘。"""
    session['meta']['title'] = title
    _save_session(session)


def snapshot(session: dict) -> dict:
    """返回 session 的深拷贝快照，排除不可序列化的 terminal_task。"""
    task = session.pop('terminal_task', None)
    try:
        s = copy.deepcopy(session)
    finally:
        if task is not None:
            session['terminal_task'] = task
    return s


def flush(session: dict):
    """将 session 当前状态强制刷盘（状态变更、事件追加后调用）。"""
    _save_session(session)
