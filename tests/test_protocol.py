"""protocol 模块的 unittest：轮输入渲染、system prompt、响应解析与错误说明。"""

import unittest

from tool_sapiens import protocol

# 测试用工具定义：工具名 -> 参数名列表（全部必填）。
SPECS = {
    'list': ['path'],
    'read': ['path'],
    'create': ['path', 'content'],
    'edit': ['path', 'old', 'new'],
    'terminal': ['command'],
    'noop': [],
}


class RenderTurnInputTest(unittest.TestCase):
    def test_first_turn_has_system_and_user(self):
        text = protocol.render_turn_input([('system', 'SYS'), ('user', '你好')])
        self.assertEqual(text, '[system]\nSYS\n\n[user]\n你好')

    def test_single_part(self):
        text = protocol.render_turn_input([('user', '只有这段')])
        self.assertEqual(text, '[user]\n只有这段')


class BuildSystemPromptTest(unittest.TestCase):
    def test_no_tools(self):
        prompt = protocol.build_system_prompt({})
        self.assertIn('无', prompt)
        self.assertIn('纯文本', prompt)

    def test_with_tools(self):
        prompt = protocol.build_system_prompt(SPECS)
        self.assertIn('edit', prompt)
        self.assertIn('<path>', prompt)
        self.assertIn('noop', prompt)
        self.assertNotIn('## 当前可用工具\n无', prompt)


class ParsePlainTextTest(unittest.TestCase):
    def test_plain_prose(self):
        result = protocol.parse_response('我想了一下。\n答案是 42。', SPECS)
        self.assertTrue(result.ok)
        self.assertEqual(result.output, '我想了一下。\n答案是 42。')
        self.assertEqual(result.calls, [])

    def test_strips_surrounding_whitespace(self):
        result = protocol.parse_response('  hello  \n', SPECS)
        self.assertTrue(result.ok)
        self.assertEqual(result.output, 'hello')

    def test_empty_response_is_ok_with_empty_output(self):
        result = protocol.parse_response('', SPECS)
        self.assertTrue(result.ok)
        self.assertEqual(result.output, '')
        self.assertEqual(result.calls, [])


class ParseToolCallsTest(unittest.TestCase):
    def test_single_call_with_prose(self):
        text = (
            '我来修这个 bug。\n'
            '<tool name="edit">\n'
            '<path>src/main.py</path>\n'
            '<old>def add(a, b):\n'
            '    return a - b</old>\n'
            '<new>def add(a, b):\n'
            '    return a + b</new>\n'
            '</tool>'
        )
        result = protocol.parse_response(text, SPECS)
        self.assertTrue(result.ok)
        self.assertEqual(result.output, '我来修这个 bug。')
        self.assertEqual(len(result.calls), 1)
        call = result.calls[0]
        self.assertEqual(call.name, 'edit')
        self.assertEqual(call.params['path'], 'src/main.py')
        self.assertEqual(call.params['old'], 'def add(a, b):\n    return a - b')
        self.assertEqual(call.params['new'], 'def add(a, b):\n    return a + b')

    def test_param_content_is_raw_and_unescaped(self):
        text = (
            '<tool name="create">'
            '<path>index.html</path>'
            '<content><div class="app">hi</div></content>'
            '</tool>'
        )
        result = protocol.parse_response(text, SPECS)
        self.assertTrue(result.ok)
        self.assertEqual(
            result.calls[0].params['content'], '<div class="app">hi</div>')

    def test_multiple_calls_keep_order(self):
        text = (
            '先读文件。\n'
            '<tool name="read"><path>a.txt</path></tool>\n'
            '再跑命令。\n'
            '<tool name="terminal"><command>dir</command></tool>\n'
            '完毕。'
        )
        result = protocol.parse_response(text, SPECS)
        self.assertTrue(result.ok)
        self.assertEqual([c.name for c in result.calls], ['read', 'terminal'])
        self.assertIn('先读文件。', result.output)
        self.assertIn('再跑命令。', result.output)
        self.assertIn('完毕。', result.output)

    def test_tool_without_params(self):
        result = protocol.parse_response('<tool name="noop"></tool>', SPECS)
        self.assertTrue(result.ok)
        self.assertEqual(result.calls[0].params, {})

    def test_whitespace_between_params_is_allowed(self):
        text = '<tool name="read">\n  <path>a.txt</path>\n</tool>'
        result = protocol.parse_response(text, SPECS)
        self.assertTrue(result.ok)
        self.assertEqual(result.calls[0].params['path'], 'a.txt')


class ParseBrokenTest(unittest.TestCase):
    def assert_broken(self, text, contains=None, specs=None):
        result = protocol.parse_response(text, SPECS if specs is None else specs)
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.calls, [])
        if contains is not None:
            self.assertIn(contains, result.error)

    def test_missing_tool_closer(self):
        self.assert_broken('<tool name="read"><path>a</path>', '闭合 tag')

    def test_response_ends_mid_block(self):
        self.assert_broken(
            '我试试\n<tool name="edit">\n<path>a</path>\n<old>x</old>', '闭合 tag')

    def test_param_missing_closer(self):
        self.assert_broken('<tool name="read"><path>a</tool>', '</path>')

    def test_unknown_tool(self):
        self.assert_broken(
            '<tool name="foo"><x>1</x></tool>', '未知工具')

    def test_unknown_tool_lists_available(self):
        self.assert_broken('<tool name="foo"></tool>', 'edit')

    def test_tool_call_when_no_tools_available(self):
        self.assert_broken(
            '<tool name="read"><path>a</path></tool>',
            '没有可用工具', specs={})

    def test_missing_required_param(self):
        self.assert_broken(
            '<tool name="edit"><path>a</path></tool>', '缺少参数')

    def test_unknown_param(self):
        self.assert_broken(
            '<tool name="read"><path>a</path><extra>1</extra></tool>',
            '未知参数')

    def test_duplicate_param(self):
        self.assert_broken(
            '<tool name="read"><path>a</path><path>b</path></tool>',
            '写了两次')

    def test_stray_closer_alone(self):
        self.assert_broken('你好 </tool>', '孤立')

    def test_stray_closer_before_opener(self):
        self.assert_broken(
            '</tool><tool name="read"><path>a</path></tool>', '孤立')

    def test_bad_opener_missing_quotes(self):
        self.assert_broken(
            '<tool name=read><path>a</path></tool>', '开始 tag 写坏')

    def test_bad_opener_missing_name(self):
        self.assert_broken('<tool></tool>', '开始 tag 写坏')

    def test_opener_cut_off(self):
        self.assert_broken('写到一半 <tool name="ed', '写坏')

    def test_raw_text_between_params(self):
        self.assert_broken(
            '<tool name="read"><path>a</path>这里不该有字</tool>', '写坏')


if __name__ == '__main__':
    unittest.main()
