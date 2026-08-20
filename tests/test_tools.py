"""tools 模块的 unittest：四个同步工具执行器。"""

import os
import tempfile
import unittest

from tool_sapiens import tools


class ToolBase(unittest.TestCase):
    """每个测试用例用一个临时目录作为工具工作目录。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        tools.set_work_dir(self.tmpdir)
        # 写入一些种子文件
        with open(os.path.join(self.tmpdir, 'hello.txt'), 'w') as f:
            f.write('Hello, world!')
        os.makedirs(os.path.join(self.tmpdir, 'subdir'))
        with open(os.path.join(self.tmpdir, 'subdir', 'nested.txt'), 'w') as f:
            f.write('nested content')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)


class ListToolTest(ToolBase):
    def test_list_root(self):
        result = tools.execute('list', {'path': '.'})
        self.assertTrue(result['ok'])
        self.assertIn('file  hello.txt', result['text'])
        self.assertIn('dir  subdir', result['text'])

    def test_list_subdir(self):
        result = tools.execute('list', {'path': 'subdir'})
        self.assertTrue(result['ok'])
        self.assertIn('file  nested.txt', result['text'])

    def test_list_empty_dir(self):
        os.makedirs(os.path.join(self.tmpdir, 'empty'))
        result = tools.execute('list', {'path': 'empty'})
        self.assertTrue(result['ok'])
        self.assertIn('为空', result['text'])

    def test_list_nonexistent(self):
        result = tools.execute('list', {'path': 'no-such-dir'})
        self.assertFalse(result['ok'])

    def test_list_on_file(self):
        result = tools.execute('list', {'path': 'hello.txt'})
        self.assertFalse(result['ok'])
        self.assertIn('不是目录', result['text'])

    def test_list_path_traversal_blocked(self):
        result = tools.execute('list', {'path': '..'})
        self.assertFalse(result['ok'])
        self.assertIn('不允许', result['text'])


class ReadToolTest(ToolBase):
    def test_read_existing(self):
        result = tools.execute('read', {'path': 'hello.txt'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['text'], 'Hello, world!')

    def test_read_nested(self):
        result = tools.execute('read', {'path': 'subdir/nested.txt'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['text'], 'nested content')

    def test_read_nonexistent(self):
        result = tools.execute('read', {'path': 'no-such.txt'})
        self.assertFalse(result['ok'])
        self.assertIn('不存在', result['text'])

    def test_read_directory(self):
        result = tools.execute('read', {'path': 'subdir'})
        self.assertFalse(result['ok'])

    def test_read_path_traversal_blocked(self):
        result = tools.execute('read', {'path': '../LICENSE'})
        self.assertFalse(result['ok'])
        self.assertIn('不允许', result['text'])


class CreateToolTest(ToolBase):
    def test_create_new_file(self):
        result = tools.execute('create', {'path': 'new.txt', 'content': 'fresh'})
        self.assertTrue(result['ok'])
        self.assertIn('已创建', result['text'])
        with open(os.path.join(self.tmpdir, 'new.txt')) as f:
            self.assertEqual(f.read(), 'fresh')

    def test_create_overwrite(self):
        result = tools.execute('create', {'path': 'hello.txt', 'content': 'overwritten'})
        self.assertTrue(result['ok'])
        with open(os.path.join(self.tmpdir, 'hello.txt')) as f:
            self.assertEqual(f.read(), 'overwritten')

    def test_create_in_new_subdir(self):
        result = tools.execute('create', {'path': 'a/b/c.txt', 'content': 'deep'})
        self.assertTrue(result['ok'])
        path = os.path.join(self.tmpdir, 'a', 'b', 'c.txt')
        self.assertTrue(os.path.isfile(path))
        with open(path) as f:
            self.assertEqual(f.read(), 'deep')

    def test_create_with_special_content(self):
        content = '<div class="app">hi</div>\nline2'
        result = tools.execute('create', {'path': 'html.txt', 'content': content})
        self.assertTrue(result['ok'])
        with open(os.path.join(self.tmpdir, 'html.txt')) as f:
            self.assertEqual(f.read(), content)

    def test_create_path_traversal_blocked(self):
        result = tools.execute('create', {'path': '../evil.txt', 'content': 'bad'})
        self.assertFalse(result['ok'])
        self.assertIn('不允许', result['text'])


class EditToolTest(ToolBase):
    def test_edit_success(self):
        result = tools.execute('edit', {
            'path': 'hello.txt',
            'old': 'Hello, world!',
            'new': 'Hello, Tool Sapiens!',
        })
        self.assertTrue(result['ok'])
        with open(os.path.join(self.tmpdir, 'hello.txt')) as f:
            self.assertEqual(f.read(), 'Hello, Tool Sapiens!')

    def test_edit_only_first_occurrence(self):
        with open(os.path.join(self.tmpdir, 'dup.txt'), 'w') as f:
            f.write('aaa\naaa\naaa')
        result = tools.execute('edit', {
            'path': 'dup.txt',
            'old': 'aaa',
            'new': 'bbb',
        })
        self.assertTrue(result['ok'])
        with open(os.path.join(self.tmpdir, 'dup.txt')) as f:
            self.assertEqual(f.read(), 'bbb\naaa\naaa')

    def test_edit_old_not_found(self):
        result = tools.execute('edit', {
            'path': 'hello.txt',
            'old': 'NOTFOUND',
            'new': 'xxx',
        })
        self.assertFalse(result['ok'])
        self.assertIn('未命中', result['text'])

    def test_edit_nonexistent_file(self):
        result = tools.execute('edit', {
            'path': 'no-such.txt',
            'old': 'x',
            'new': 'y',
        })
        self.assertFalse(result['ok'])
        self.assertIn('不存在', result['text'])

    def test_edit_multiline(self):
        with open(os.path.join(self.tmpdir, 'multi.py'), 'w') as f:
            f.write('def add(a, b):\n    return a - b\ndef sub(a, b):\n    return a - b')
        result = tools.execute('edit', {
            'path': 'multi.py',
            'old': 'def add(a, b):\n    return a - b',
            'new': 'def add(a, b):\n    return a + b',
        })
        self.assertTrue(result['ok'])
        with open(os.path.join(self.tmpdir, 'multi.py')) as f:
            content = f.read()
        self.assertIn('return a + b', content)


class ExecuteUnknownToolTest(ToolBase):
    def test_unknown_dispatch(self):
        result = tools.execute('unknown_tool', {})
        self.assertFalse(result['ok'])
        self.assertIn('未注册', result['text'])


if __name__ == '__main__':
    unittest.main()
