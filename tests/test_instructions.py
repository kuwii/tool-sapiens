"""全局 / 项目级指令注入首轮输入的 unittest。"""

import os
import shutil
import tempfile
import unittest

from tool_sapiens import loop, sessions, tools


class InstructionsTest(unittest.TestCase):
    def setUp(self):
        # 临时目录充当工作目录；备份并替换全局指令路径
        self.tmpdir = tempfile.mkdtemp()
        tools.set_work_dir(self.tmpdir)
        self._orig_global_path = loop.GLOBAL_AGENTS_PATH
        self.global_path = os.path.join(self.tmpdir, 'global-AGENTS.md')
        loop.GLOBAL_AGENTS_PATH = self.global_path
        self.session = sessions.create_session()

    def tearDown(self):
        loop.GLOBAL_AGENTS_PATH = self._orig_global_path
        shutil.rmtree(self.tmpdir)

    def _write_global(self, text):
        with open(self.global_path, 'w', encoding='utf-8') as f:
            f.write(text)

    def _write_project(self, text):
        with open(os.path.join(self.tmpdir, 'AGENTS.md'), 'w',
                  encoding='utf-8') as f:
            f.write(text)

    @staticmethod
    def _count_user_blocks(pending):
        """统计真实 USER 块数。

        前导 preamble 的格式示例只用 SYSTEM 标签，不会出现 END OF USER 行；
        而每个真实块恰好有一个 '\n└----- END OF USER ------' 结尾行。
        """
        return pending.count('\n└----- END OF USER ------')

    def test_no_instruction_files_single_user_block(self):
        """两个文件都不存在时，首轮只有 system + 用户提示词两个块。"""
        pending = loop.submit_prompt(self.session, '你好')
        self.assertIn('START OF SYSTEM', pending)
        self.assertEqual(self._count_user_blocks(pending), 1)
        self.assertIn('你好', pending)

    def test_global_instruction_injected_as_user_block(self):
        self._write_global('全局规则：回复用文言文。')
        pending = loop.submit_prompt(self.session, '你好')
        self.assertEqual(self._count_user_blocks(pending), 2)
        self.assertIn('全局规则：回复用文言文。', pending)
        self.assertIn('全局指令', pending)
        # 顺序：system → 全局指令 → 用户提示词
        self.assertLess(pending.index('全局规则'),
                        pending.index('你好'))

    def test_project_instruction_injected_as_user_block(self):
        self._write_project('项目规则：代码注释用中文。')
        pending = loop.submit_prompt(self.session, '你好')
        self.assertEqual(self._count_user_blocks(pending), 2)
        self.assertIn('项目规则：代码注释用中文。', pending)
        self.assertIn('项目级指令', pending)

    def test_both_instructions_two_separate_blocks(self):
        """两个文件都在时，各占一个独立的 C 字形块，先全局后项目。"""
        self._write_global('GLOBAL RULE')
        self._write_project('PROJECT RULE')
        pending = loop.submit_prompt(self.session, '你好')
        # 全局、项目、用户提示词各占一个块
        self.assertEqual(self._count_user_blocks(pending), 3)
        # 各自独占一个块：全局与项目不在同一个 START 与 END 之间
        first_end = pending.index('END OF USER')
        self.assertLess(pending.index('GLOBAL RULE'), first_end)
        self.assertGreater(pending.index('PROJECT RULE'), first_end)

    def test_instructions_not_repeated_on_later_turns(self):
        self._write_global('GLOBAL RULE')
        self._write_project('PROJECT RULE')
        loop.submit_prompt(self.session, '第一条')
        loop.submit_response(self.session, '收到')
        loop.submit_prompt(self.session, '第二条')
        pending = self.session['pending_input']
        self.assertNotIn('GLOBAL RULE', pending)
        self.assertNotIn('PROJECT RULE', pending)
        self.assertNotIn('START OF SYSTEM', pending)

    def test_unreadable_file_skipped_silently(self):
        """读取失败（如路径是目录）时静默跳过，不影响首轮输入生成。"""
        os.makedirs(self.global_path)  # 让 open() 抛 OSError
        pending = loop.submit_prompt(self.session, '你好')
        self.assertEqual(self._count_user_blocks(pending), 1)

    def test_empty_file_skipped(self):
        with open(self.global_path, 'w', encoding='utf-8') as f:
            f.write('   \n')
        pending = loop.submit_prompt(self.session, '你好')
        self.assertEqual(self._count_user_blocks(pending), 1)


if __name__ == '__main__':
    unittest.main()
