"""Tool Sapiens HTTP 服务：只 bind 127.0.0.1，在网络层拒绝外部连接。

提供两类端点：
- /api/...：AJAX 端点（sessions / prompt / llm / response / kill）。
- /、/llm、/static/*：双页面前端与静态资源（都在包内 static/ 目录）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import loop
from . import sessions
from . import tools
from .loop import LoopError

DEFAULT_PORT = 8765

# 持久化数据目录：~/.local/share/tool-sapiens/sessions/
def _default_data_dir() -> str:
    base = os.path.expanduser('~/.local/share/tool-sapiens')
    return os.path.join(base, 'sessions')
_SID = r'(?P<sid>[0-9a-f]+)'
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
# 固定映射，不做任意路径拼接，天然没有路径穿越问题。
_STATIC_FILES = {
    '/': ('chat.html', 'text/html; charset=utf-8'),
    '/llm': ('llm.html', 'text/html; charset=utf-8'),
    '/static/style.css': ('style.css', 'text/css; charset=utf-8'),
    '/static/common.js': ('common.js', 'text/javascript; charset=utf-8'),
    '/static/chat.js': ('chat.js', 'text/javascript; charset=utf-8'),
    '/static/llm.js': ('llm.js', 'text/javascript; charset=utf-8'),
}


class Handler(BaseHTTPRequestHandler):
    server_version = 'ToolSapiens'

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path in _STATIC_FILES:
            return self._send_file(*_STATIC_FILES[path])
        if path.startswith('/static/'):
            return self._send_json(404, {'error': f'静态资源不存在：{path}'})
        if path == '/api/sessions':
            return self._send_json(200, {'sessions': sessions.list_sessions()})
        match = re.fullmatch(rf'/api/sessions/{_SID}', path)
        if match:
            session = sessions.get_session(match['sid'])
            if session is None:
                return self._send_json(404, {'error': f'session {match["sid"]} 不存在。'})
            with sessions.get_lock(match['sid']):
                loop.check_terminal_task(session)
                return self._send_json(200, {'session': sessions.snapshot(session)})
        match = re.fullmatch(rf'/api/llm/{_SID}', path)
        if match:
            session = sessions.get_session(match['sid'])
            if session is None:
                return self._send_json(404, {'error': f'session {match["sid"]} 不存在。'})
            with sessions.get_lock(match['sid']):
                return self._send_json(200, self._llm_view(session))
        return self._send_json(404, {'error': f'未知端点：GET {path}'})

    def do_POST(self):
        path = self.path.split('?', 1)[0]
        if path == '/api/sessions':
            session = sessions.create_session()
            return self._send_json(201, {'session': sessions.snapshot(session)})
        match = re.fullmatch(rf'/api/sessions/{_SID}/prompt', path)
        if match:
            return self._with_session(match['sid'], self._post_prompt)
        match = re.fullmatch(rf'/api/llm/{_SID}/response', path)
        if match:
            return self._with_session(match['sid'], self._post_response)
        match = re.fullmatch(rf'/api/llm/{_SID}/kill', path)
        if match:
            return self._with_session(match['sid'], self._post_kill)
        return self._send_json(404, {'error': f'未知端点：POST {path}'})

    def _with_session(self, sid, action):
        session = sessions.get_session(sid)
        if session is None:
            return self._send_json(404, {'error': f'session {sid} 不存在。'})
        body = self._read_json()
        if body is None:
            return self._send_json(400, {'error': '请求体必须是合法的 JSON 对象。'})
        with sessions.get_lock(sid):
            return action(session, body)

    def _post_prompt(self, session, body):
        prompt = body.get('prompt')
        if not isinstance(prompt, str):
            return self._send_json(400, {'error': '缺少 prompt 字段（字符串）。'})
        try:
            loop.submit_prompt(session, prompt)
        except LoopError as exc:
            return self._send_json(exc.status, {'error': str(exc)})
        return self._send_json(200, {'session': sessions.snapshot(session)})

    def _post_response(self, session, body):
        response = body.get('response')
        if not isinstance(response, str):
            return self._send_json(400, {'error': '缺少 response 字段（字符串）。'})
        try:
            loop.submit_response(session, response)
        except LoopError as exc:
            return self._send_json(exc.status, {'error': str(exc)})
        return self._send_json(200, self._llm_view(session))

    def _post_kill(self, session, body):
        try:
            output = loop.kill_terminal_task(session)
        except LoopError as exc:
            return self._send_json(exc.status, {'error': str(exc)})
        return self._send_json(200, self._llm_view(session))

    def _llm_view(self, session):
        view = {
            'state': session['state'],
            'pending_input': session['pending_input'],
            'last_error': session['last_error'],
        }
        if session['state'] == 'executing':
            task = session.get('terminal_task')
            if task is not None:
                status = tools.terminal_task_status(task)
                view['terminal_status'] = status
        return view

    def _read_json(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b''
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _send_json(self, status, obj):
        payload = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, filename, content_type):
        try:
            with open(os.path.join(_STATIC_DIR, filename), 'rb') as fp:
                payload = fp.read()
        except OSError:
            return self._send_json(500, {'error': f'静态资源缺失：{filename}'})
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main(argv=None):
    # 控制台/重定向编码不含中文时不崩溃，退化为替换字符
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')
    parser = argparse.ArgumentParser(description='Tool Sapiens 服务（只响应 127.0.0.1）')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT,
                        help=f'监听端口（默认 {DEFAULT_PORT}）')
    args = parser.parse_args(argv)
    tools.set_work_dir(os.getcwd())
    data_dir = args.data_dir if hasattr(args, 'data_dir') and args.data_dir else _default_data_dir()
    sessions.init_store(data_dir)
    httpd = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    httpd.daemon_threads = True
    print(f'Tool Sapiens 启动：http://127.0.0.1:{args.port}（Ctrl+C 停止）')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n停止。')
    finally:
        httpd.server_close()


if __name__ == '__main__':
    main()
