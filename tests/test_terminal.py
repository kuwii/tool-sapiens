"""阶段 4：terminal 工具 + executing 状态 + kill 的 unittest。"""

import os
import sys
import tempfile
import threading
import time
import unittest

from tool_sapiens import loop, sessions, tools
from tool_sapiens.loop import LoopError


class TerminalToolTest(unittest.TestCase):
    """terminal 工具执行器的基础测试。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        tools.set_work_dir(self.tmpdir)

    def tearDown(self):
        import shutil
        try:
            shutil.rmtree(self.tmpdir)
        except PermissionError:
            pass  # Windows 上残留进程可能还在占用

    def test_short_command_completes(self):
        """短命令正常执行并返回输出。"""
        task = tools.start_terminal_task('python -c "print(\'hello\')"')
        task['thread'].join(timeout=5)
        status = tools.terminal_task_status(task)
        self.assertTrue(status['done'])
        self.assertIn('hello', status['output'])

    def test_command_exit_code_zero(self):
        """成功命令退出码为 0。"""
        task = tools.start_terminal_task('python -c "print(\'ok\')"')
        task['thread'].join(timeout=5)
        status = tools.terminal_task_status(task)
        self.assertEqual(status['exit_code'], 0)

    @unittest.skipIf(sys.platform != 'win32', 'Windows-only kill test')
    def test_kill_long_command_windows(self):
        """长命令可以被终止（Windows）。"""
        task = tools.start_terminal_task('ping -n 30 127.0.0.1')
        time.sleep(0.5)  # 等进程启动
        status = tools.terminal_task_status(task)
        self.assertFalse(status['done'])
        tools.kill_terminal_task(task)
        status = tools.terminal_task_status(task)
        self.assertTrue(status['done'])
        self.assertTrue(status['killed'])

    def test_terminal_task_output_non_blocking(self):
        """terminal_task_output 不阻塞。"""
        task = tools.start_terminal_task('python -c "print(\'quick\')"')
        # 立即读取输出（可能为空，也可能已有输出）
        output = tools.terminal_task_output(task)
        # 不抛异常即可
        self.assertIsInstance(output, str)
        task['thread'].join(timeout=5)


class LoopTerminalTest(unittest.TestCase):
    """loop 层 terminal 集成测试。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        tools.set_work_dir(self.tmpdir)
        sessions.init_store(self.tmpdir)
        self.session = sessions.create_session()
        loop.submit_prompt(self.session, '跑个命令')

    def tearDown(self):
        import shutil
        try:
            shutil.rmtree(self.tmpdir)
        except PermissionError:
            pass  # Windows 上残留进程可能还在占用

    def test_terminal_call_enters_executing(self):
        """提交含 terminal 调用的响应 → 进入 executing 状态。"""
        loop.submit_response(
            self.session,
            '<tool name="terminal"><command>python -c "print(\'done\')"</command></tool>'
        )
        self.assertEqual(self.session['state'], 'executing')
        self.assertIsNotNone(self.session.get('terminal_task'))
        # 事件流中有 tool_call
        types = [e['type'] for e in self.session['events']]
        self.assertIn('tool_call', types)

    def test_check_terminal_task_completes(self):
        """短命令执行完后 check_terminal_task 推进状态。"""
        loop.submit_response(
            self.session,
            '<tool name="terminal"><command>python -c "print(\'result\')"</command></tool>'
        )
        self.assertEqual(self.session['state'], 'executing')
        # 等后台线程完成
        task = self.session['terminal_task']
        task['thread'].join(timeout=5)
        loop.check_terminal_task(self.session)
        self.assertEqual(self.session['state'], 'awaiting_llm')
        # 有 tool_result 事件
        types = [e['type'] for e in self.session['events']]
        self.assertIn('tool_result', types)
        # pending_input 含工具结果
        self.assertIn('result', self.session['pending_input'])

    def test_kill_terminal_task_via_loop(self):
        """通过 loop.kill_terminal_task 终止任务。"""
        loop.submit_response(
            self.session,
            '<tool name="terminal"><command>python -c "import time; time.sleep(30)"</command></tool>'
        )
        self.assertEqual(self.session['state'], 'executing')
        time.sleep(0.5)  # 等进程启动
        output = loop.kill_terminal_task(self.session)
        self.assertEqual(self.session['state'], 'awaiting_llm')
        self.assertIsNone(self.session.get('terminal_task'))
        # 有终止说明（在 tool_result 事件里）
        result_event = [e for e in self.session['events'] if e['type'] == 'tool_result'][-1]
        self.assertIn('终止', result_event['result']['text'])

    def test_kill_when_not_executing_raises(self):
        """非 executing 状态下 kill 抛异常。"""
        with self.assertRaises(LoopError) as ctx:
            loop.kill_terminal_task(self.session)
        self.assertIn('只有 executing 才能终止', str(ctx.exception))

    def test_sync_tools_still_work_with_terminal_available(self):
        """同步工具（如 read）在有 terminal 的情况下仍正常工作。"""
        import tempfile
        import os
        tmpdir = tempfile.mkdtemp()
        tools.set_work_dir(tmpdir)
        try:
            with open(os.path.join(tmpdir, 'f.txt'), 'w') as f:
                f.write('data')
            loop.submit_response(
                self.session,
                '<tool name="read"><path>f.txt</path></tool>'
            )
            self.assertEqual(self.session['state'], 'awaiting_llm')
            self.assertIn('data', self.session['pending_input'])
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_terminal_task_excluded_from_snapshot(self):
        """session 快照中不包含 terminal_task 对象。"""
        loop.submit_response(
            self.session,
            '<tool name="terminal"><command>python -c "print(\'x\')" </command></tool>'
        )
        snapshot = sessions.snapshot(self.session)
        self.assertNotIn('terminal_task', snapshot)


class LoopCheckTerminalTaskSafeTest(unittest.TestCase):
    """check_terminal_task 在非 executing 状态下安全无操作。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        sessions.init_store(self.tmpdir)
        tools.set_work_dir(self.tmpdir)

    def tearDown(self):
        import shutil
        try:
            shutil.rmtree(self.tmpdir)
        except PermissionError:
            pass

    def test_no_op_when_idle(self):
        session = sessions.create_session()
        loop.check_terminal_task(session)  # 不抛异常
        self.assertEqual(session['state'], 'idle')

    def test_no_op_when_awaiting_llm(self):
        session = sessions.create_session()
        loop.submit_prompt(session, 'hi')
        loop.check_terminal_task(session)  # 不抛异常
        self.assertEqual(session['state'], 'awaiting_llm')


if __name__ == '__main__':
    unittest.main()
