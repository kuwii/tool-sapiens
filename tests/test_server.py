"""server 模块的 unittest：静态资源端点（双页面 + js/css）。"""

import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tool_sapiens.server import Handler


class StaticPagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.httpd.daemon_threads = True
        cls.base = f'http://127.0.0.1:{cls.httpd.server_address[1]}'
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return resp.status, resp.headers.get('Content-Type', ''), resp.read()

    def test_chat_page_served(self):
        status, ctype, body = self.get('/')
        self.assertEqual(status, 200)
        self.assertIn('text/html', ctype)
        text = body.decode('utf-8')
        self.assertIn('聊天', text)
        self.assertIn('chat.js', text)

    def test_llm_page_served(self):
        status, ctype, body = self.get('/llm')
        self.assertEqual(status, 200)
        self.assertIn('text/html', ctype)
        text = body.decode('utf-8')
        self.assertIn('给 LLM 的输入', text)
        self.assertIn('llm.js', text)

    def test_static_assets_served(self):
        assets = {
            '/static/style.css': 'text/css',
            '/static/common.js': 'text/javascript',
            '/static/chat.js': 'text/javascript',
            '/static/llm.js': 'text/javascript',
        }
        for path, prefix in assets.items():
            with self.subTest(path=path):
                status, ctype, body = self.get(path)
                self.assertEqual(status, 200)
                self.assertTrue(ctype.startswith(prefix), ctype)
                self.assertGreater(len(body), 0)

    def test_unknown_static_is_404(self):
        try:
            urllib.request.urlopen(self.base + '/static/nope.js', timeout=5)
            self.fail('应当返回 404')
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)


if __name__ == '__main__':
    unittest.main()
