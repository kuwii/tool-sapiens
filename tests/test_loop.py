"""loop 模块的 unittest：状态机转移与 sessions 的配合。"""

import unittest

from tool_sapiens import loop, sessions
from tool_sapiens.loop import LoopError


class SubmitPromptTest(unittest.TestCase):
    def setUp(self):
        self.session = sessions.create_session()

    def test_first_prompt_builds_pending_input_with_system_prompt(self):
        pending = loop.submit_prompt(self.session, '你好')
        self.assertEqual(self.session['state'], 'awaiting_llm')
        self.assertEqual(pending, self.session['pending_input'])
        self.assertIn('[system]', pending)
        self.assertIn('[user]\n你好', pending)
        self.assertIsNone(self.session['last_error'])

    def test_user_prompt_event_appended(self):
        loop.submit_prompt(self.session, '你好')
        event = self.session['events'][0]
        self.assertEqual(event['type'], 'user_prompt')
        self.assertEqual(event['text'], '你好')

    def test_second_turn_has_no_system_prompt(self):
        loop.submit_prompt(self.session, '第一条')
        loop.submit_response(self.session, '收到')
        loop.submit_prompt(self.session, '第二条')
        self.assertNotIn('[system]', self.session['pending_input'])
        self.assertIn('[user]\n第二条', self.session['pending_input'])

    def test_prompt_is_stripped(self):
        loop.submit_prompt(self.session, '  你好  ')
        self.assertEqual(self.session['events'][0]['text'], '你好')

    def test_empty_prompt_rejected(self):
        with self.assertRaises(LoopError):
            loop.submit_prompt(self.session, '   ')
        self.assertEqual(self.session['state'], 'idle')
        self.assertEqual(self.session['events'], [])

    def test_prompt_rejected_when_awaiting_llm(self):
        loop.submit_prompt(self.session, '你好')
        with self.assertRaises(LoopError):
            loop.submit_prompt(self.session, '又来一条')
        self.assertEqual(self.session['state'], 'awaiting_llm')


class SubmitResponseTest(unittest.TestCase):
    def setUp(self):
        self.session = sessions.create_session()
        loop.submit_prompt(self.session, '你好')

    def test_plain_text_response_ends_turn(self):
        loop.submit_response(self.session, '你好！我是 Tool Sapiens。')
        self.assertEqual(self.session['state'], 'idle')
        self.assertIsNone(self.session['pending_input'])
        self.assertIsNone(self.session['last_error'])
        event = self.session['events'][-1]
        self.assertEqual(event['type'], 'llm_output')
        self.assertEqual(event['text'], '你好！我是 Tool Sapiens。')

    def test_broken_tag_keeps_awaiting_and_sets_last_error(self):
        pending = self.session['pending_input']
        # 开始 tag 少了双引号：无论有无工具表都能检出结构错误
        loop.submit_response(self.session, '我试试调工具。\n<tool name=edit>\n<path>a</path>')
        self.assertEqual(self.session['state'], 'awaiting_llm')
        self.assertEqual(self.session['pending_input'], pending)
        self.assertIsNotNone(self.session['last_error'])
        self.assertIn('开始 tag 写坏', self.session['last_error'])
        # 没有产生新事件
        self.assertEqual(len(self.session['events']), 1)
        self.assertEqual(self.session['events'][0]['type'], 'user_prompt')

    def test_retry_after_broken_tag_succeeds(self):
        loop.submit_response(self.session, '<tool name=edit>\n<path>a</path>')
        loop.submit_response(self.session, '重写一遍，好了。')
        self.assertEqual(self.session['state'], 'idle')
        self.assertIsNone(self.session['last_error'])
        self.assertEqual(self.session['events'][-1]['text'], '重写一遍，好了。')

    def test_unknown_tool_rejected(self):
        # 形式完整但工具名不存在：按未知工具打回
        loop.submit_response(
            self.session, '<tool name="nonexistent">\n</tool>')
        self.assertEqual(self.session['state'], 'awaiting_llm')
        self.assertIn('未知工具', self.session['last_error'])

    def test_response_rejected_when_idle(self):
        loop.submit_response(self.session, '收到')
        with self.assertRaises(LoopError):
            loop.submit_response(self.session, '没人问我')


class ToolExecutionTest(unittest.TestCase):
    """阶段 3：工具调用顺序执行、结果回灌、事件流。"""

    def setUp(self):
        import tempfile
        from tool_sapiens import tools
        self.tmpdir = tempfile.mkdtemp()
        tools.set_work_dir(self.tmpdir)
        # 写入种子文件
        import os
        with open(os.path.join(self.tmpdir, 'a.txt'), 'w') as f:
            f.write('original content')
        self.session = sessions.create_session()
        loop.submit_prompt(self.session, '帮我操作文件')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_single_tool_call_stays_awaiting_llm(self):
        """有工具调用时，状态保持 awaiting_llm，pending_input 含工具结果。"""
        loop.submit_response(self.session, '<tool name="read"><path>a.txt</path></tool>')
        self.assertEqual(self.session['state'], 'awaiting_llm')
        self.assertIsNotNone(self.session['pending_input'])
        self.assertIn('original content', self.session['pending_input'])

    def test_tool_events_appended(self):
        """工具调用和结果事件按顺序追加。"""
        loop.submit_response(self.session, '<tool name="read"><path>a.txt</path></tool>')
        types = [e['type'] for e in self.session['events']]
        self.assertIn('tool_call', types)
        self.assertIn('tool_result', types)
        # tool_call 在 tool_result 之前
        call_idx = types.index('tool_call')
        result_idx = types.index('tool_result')
        self.assertLess(call_idx, result_idx)

    def test_multiple_tool_calls_executed_in_order(self):
        """多个工具调用按顺序执行，结果全部回灌。"""
        text = (
            '<tool name="read"><path>a.txt</path></tool>'
            '<tool name="list"><path>.</path></tool>'
        )
        loop.submit_response(self.session, text)
        types = [e['type'] for e in self.session['events']]
        # llm_output, tool_call, tool_result, tool_call, tool_result
        self.assertEqual(types.count('tool_call'), 2)
        self.assertEqual(types.count('tool_result'), 2)
        # pending_input 中包含两个工具的结果
        self.assertIn('original content', self.session['pending_input'])
        self.assertIn('a.txt', self.session['pending_input'])

    def test_tool_error_result_in_next_input(self):
        """工具执行失败时，错误信息回灌到下一轮输入。"""
        loop.submit_response(self.session, '<tool name="read"><path>no-such.txt</path></tool>')
        self.assertEqual(self.session['state'], 'awaiting_llm')
        self.assertIn('失败', self.session['pending_input'])

    def test_edit_tool_via_loop(self):
        """通过 loop 调用 edit 工具，原文未命中时返回错误。"""
        import os
        loop.submit_response(self.session, (
            '<tool name="edit">'
            '<path>a.txt</path>'
            '<old>NOTFOUND</old>'
            '<new>replacement</new>'
            '</tool>'
        ))
        self.assertEqual(self.session['state'], 'awaiting_llm')
        self.assertIn('未命中', self.session['pending_input'])
        # 文件内容不变
        with open(os.path.join(self.tmpdir, 'a.txt')) as f:
            self.assertEqual(f.read(), 'original content')

    def test_plain_response_after_tool_still_goes_idle(self):
        """工具执行后，LLM 页提交纯文本响应（无工具），状态回 idle。"""
        loop.submit_response(self.session, '<tool name="read"><path>a.txt</path></tool>')
        # 此时 awaiting_llm，pending_input 含工具结果
        loop.submit_response(self.session, '文件内容是 original content。')
        self.assertEqual(self.session['state'], 'idle')
        self.assertIsNone(self.session['pending_input'])


if __name__ == '__main__':
    unittest.main()
