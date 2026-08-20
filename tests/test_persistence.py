"""第5阶段：持久化 + session 列表的 unittest。

测试覆盖：
- JSON 文件读写、数据目录创建
- 重启恢复（重新 init_store 后 session 状态完整）
- 自动标题生成
- awaiting_llm 状态的 pending_input 恢复
- session 列表 API
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from tool_sapiens import loop, sessions, tools
from tool_sapiens.server import Handler


class GenerateTitleTest(unittest.TestCase):
    def test_short_text_becomes_title(self):
        title = sessions._generate_title('Hello world')
        self.assertEqual(title, 'Hello world')

    def test_long_text_truncated(self):
        text = 'a' * 50
        title = sessions._generate_title(text)
        self.assertEqual(len(title), 31)  # 30 chars + '…'
        self.assertTrue(title.endswith('…'))

    def test_whitespace_only_returns_default(self):
        title = sessions._generate_title('   ')
        self.assertEqual(title, '新 session')

    def test_chinese_title(self):
        title = sessions._generate_title('帮我分析一下这个项目的架构')
        self.assertIn('帮我', title)

    def test_title_strips_whitespace(self):
        title = sessions._generate_title('  hello  ')
        self.assertEqual(title, 'hello')


class FileStoreTest(unittest.TestCase):
    """JSON 文件后端核心测试。"""

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        sessions.init_store(self.data_dir)
        tools.set_work_dir(self.data_dir)

    def tearDown(self):
        shutil.rmtree(self.data_dir)

    def test_create_session_persists_to_disk(self):
        s = sessions.create_session()
        sid = s['meta']['id']
        # 磁盘上有对应文件
        path = os.path.join(self.data_dir, f'{sid}.json')
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        self.assertEqual(data['meta']['id'], sid)
        self.assertEqual(data['state'], 'idle')
        self.assertEqual(data['events'], [])

    def test_reload_recovers_sessions(self):
        s1 = sessions.create_session()
        sid1 = s1['meta']['id']
        loop.submit_prompt(s1, '第一条消息')
        # 此时 s1 在 awaiting_llm，有事件和 pending_input
        self.assertEqual(s1['state'], 'awaiting_llm')
        pending = s1['pending_input']
        # 模拟重启
        sessions.init_store(self.data_dir)
        # 恢复后
        s1_restored = sessions.get_session(sid1)
        self.assertIsNotNone(s1_restored)
        self.assertEqual(s1_restored['state'], 'awaiting_llm')
        self.assertEqual(s1_restored['pending_input'], pending)
        self.assertEqual(len(s1_restored['events']), 1)
        self.assertEqual(s1_restored['events'][0]['type'], 'user_prompt')

    def test_reload_recovers_multiple_sessions(self):
        s1 = sessions.create_session()
        s2 = sessions.create_session()
        sid1 = s1['meta']['id']
        sid2 = s2['meta']['id']
        loop.submit_prompt(s1, 'session 1')
        loop.submit_prompt(s2, 'session 2')
        # 模拟重启
        sessions.init_store(self.data_dir)
        # 两个 session 都应恢复
        self.assertIsNotNone(sessions.get_session(sid1))
        self.assertIsNotNone(sessions.get_session(sid2))
        meta_list = sessions.list_sessions()
        self.assertEqual(len(meta_list), 2)

    def test_list_sessions_empty(self):
        self.assertEqual(sessions.list_sessions(), [])

    def test_list_sessions_after_create(self):
        s1 = sessions.create_session()
        s2 = sessions.create_session()
        meta_list = sessions.list_sessions()
        self.assertEqual(len(meta_list), 2)
        ids = [m['id'] for m in meta_list]
        self.assertIn(s1['meta']['id'], ids)
        self.assertIn(s2['meta']['id'], ids)

    def test_snapshot_excludes_terminal_task(self):
        s = sessions.create_session()
        s['terminal_task'] = {'proc': None, 'done': False}
        snap = sessions.snapshot(s)
        self.assertNotIn('terminal_task', snap)
        # 原 session 恢复 terminal_task
        self.assertIsNotNone(s.get('terminal_task'))

    def test_flush_writes_to_disk(self):
        s = sessions.create_session()
        sessions.append_event(s, 'user_prompt', text='hello')
        sessions.flush(s)
        # 磁盘上有更新
        path = os.path.join(self.data_dir, f'{s["meta"]["id"]}.json')
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        self.assertEqual(len(data['events']), 1)


class AutoTitleTest(unittest.TestCase):
    """自动标题：首次 user_prompt 时生成。"""

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        sessions.init_store(self.data_dir)
        tools.set_work_dir(self.data_dir)

    def tearDown(self):
        shutil.rmtree(self.data_dir)

    def test_title_generated_on_first_prompt(self):
        s = sessions.create_session()
        self.assertEqual(s['meta']['title'], '')
        loop.submit_prompt(s, '帮我分析项目架构')
        self.assertTrue(s['meta']['title'])
        self.assertIn('帮我', s['meta']['title'])

    def test_title_not_overwritten_on_second_prompt(self):
        s = sessions.create_session()
        loop.submit_prompt(s, '第一条消息')
        title1 = s['meta']['title']
        loop.submit_response(s, '收到')
        loop.submit_prompt(s, '第二条消息')
        self.assertEqual(s['meta']['title'], title1)

    def test_title_persists_across_reload(self):
        s = sessions.create_session()
        loop.submit_prompt(s, '测试标题生成')
        title1 = s['meta']['title']
        sessions.init_store(self.data_dir)
        s2 = sessions.get_session(s['meta']['id'])
        self.assertEqual(s2['meta']['title'], title1)


class PersistenceAPITest(unittest.TestCase):
    """通过 HTTP API 验证持久化：重启后 404 → 恢复。"""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.httpd.daemon_threads = True
        port = cls.httpd.server_address[1]
        cls.base = f'http://127.0.0.1:{port}'
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        sessions.init_store(self.data_dir)
        tools.set_work_dir(self.data_dir)

    def tearDown(self):
        shutil.rmtree(self.data_dir)

    def _post(self, path, body):
        data = json.dumps(body).encode('utf-8')
        req = urllib.request.Request(
            self.base + path, data=data,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def test_create_submit_reload_restore(self):
        # 创建 session
        result = self._post('/api/sessions', {})
        sid = result['session']['meta']['id']
        # 提交 prompt
        self._post(f'/api/sessions/{sid}/prompt', {'prompt': '持久化测试'})
        # 验证状态
        s = self._get(f'/api/sessions/{sid}')
        self.assertEqual(s['session']['state'], 'awaiting_llm')
        # 模拟重启
        sessions.init_store(self.data_dir)
        # 恢复后仍可用
        s2 = self._get(f'/api/sessions/{sid}')
        self.assertEqual(s2['session']['state'], 'awaiting_llm')
        self.assertEqual(len(s2['session']['events']), 1)
        self.assertEqual(s2['session']['events'][0]['text'], '持久化测试')

    def test_session_list_api(self):
        self._post('/api/sessions', {})
        self._post('/api/sessions', {})
        result = self._get('/api/sessions')
        self.assertEqual(len(result['sessions']), 2)
        for meta in result['sessions']:
            self.assertIn('id', meta)
            self.assertIn('title', meta)
            self.assertIn('created_at', meta)

    def test_auto_title_via_api(self):
        self._post('/api/sessions', {})
        result = self._post('/api/sessions', {})
        sid = result['session']['meta']['id']
        self._post(f'/api/sessions/{sid}/prompt', {'prompt': 'API 自动标题测试'})
        s = self._get(f'/api/sessions/{sid}')
        self.assertTrue(s['session']['meta']['title'])
        self.assertIn('API', s['session']['meta']['title'])


if __name__ == '__main__':
    unittest.main()
