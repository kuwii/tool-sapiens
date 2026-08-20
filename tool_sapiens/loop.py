"""状态机与 loop 推进：生成轮输入、调度工具执行、状态转移。

三态：idle / awaiting_llm / executing（阶段 4 terminal 用）。
"""

from __future__ import annotations

from . import protocol
from . import sessions
from . import tools

# 当前可用工具：工具名 -> 参数名列表（全部必填）。
TOOL_SPECS = tools.TOOL_SPECS


class LoopError(Exception):
    """状态不允许的操作。status 为对应 HTTP 状态码。"""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def submit_prompt(session: dict, text: str) -> str:
    """idle 时提交用户提示词，生成本轮输入并进入 awaiting_llm。

    首次 user_prompt 时自动生成 session 标题。
    """
    if session['state'] != 'idle':
        raise LoopError(f'当前状态 {session["state"]}，只有 idle 才能提交提示词。')
    text = text.strip()
    if not text:
        raise LoopError('提示词不能为空。', status=400)
    first_turn = not any(e['type'] == 'user_prompt' for e in session['events'])
    sessions.append_event(session, 'user_prompt', text=text)
    # 自动标题：首次提示词时生成
    if first_turn and not session['meta']['title']:
        sessions.set_title(session, sessions._generate_title(text))
    parts = []
    if first_turn:
        parts.append(('system', protocol.build_system_prompt(TOOL_SPECS)))
    parts.append(('user', text))
    session['pending_input'] = protocol.render_turn_input(parts)
    session['last_error'] = None
    session['state'] = 'awaiting_llm'
    sessions.flush(session)
    return session['pending_input']


def submit_response(session: dict, text: str):
    """awaiting_llm 时提交人类"LLM 响应"。

    写坏 → 保持 awaiting_llm，写 last_error，等重试；
    正常且无工具调用 → 记 llm_output 事件，回 idle；
    有同步工具调用 → 顺序执行、记 tool_call / tool_result 事件、
    把结果回灌为下一轮输入，回到 awaiting_llm；
    有 terminal 调用 → 启动后台执行、记 tool_call 事件、进入 executing 状态。
    """
    if session['state'] != 'awaiting_llm':
        raise LoopError(f'当前状态 {session["state"]}，只有 awaiting_llm 才能提交响应。')
    result = protocol.parse_response(text, TOOL_SPECS)
    if not result.ok:
        session['last_error'] = result.error
        return None
    sessions.append_event(session, 'llm_output', text=result.output)
    if not result.calls:
        session['state'] = 'idle'
        session['pending_input'] = None
        session['last_error'] = None
        sessions.flush(session)
        return result.output

    # 检查是否包含 terminal 调用（异步，进入 executing）
    terminal_call = next((c for c in result.calls if c.name == 'terminal'), None)
    sync_calls = [c for c in result.calls if c.name != 'terminal']

    if terminal_call is not None:
        # 先执行所有同步工具调用
        tool_results = []
        for call in sync_calls:
            sessions.append_event(session, 'tool_call', name=call.name, params=call.params)
            tool_result = tools.execute(call.name, call.params)
            sessions.append_event(session, 'tool_result', name=call.name, result=tool_result)
            tool_results.append(tool_result)
        # 启动 terminal 任务
        sessions.append_event(session, 'tool_call', name='terminal', params=terminal_call.params)
        task = tools.start_terminal_task(terminal_call.params['command'])
        session['terminal_task'] = task
        session['state'] = 'executing'
        sessions.flush(session)
        # pending_input 保留当前输入（LLM 页继续看到之前的输入）
        return result.output

    # 纯同步工具调用（阶段 3 逻辑）
    tool_results = []
    for call in result.calls:
        sessions.append_event(session, 'tool_call', name=call.name, params=call.params)
        tool_result = tools.execute(call.name, call.params)
        sessions.append_event(session, 'tool_result', name=call.name, result=tool_result)
        tool_results.append(tool_result)
    # 生成下一轮输入：工具结果回灌
    parts = []
    parts.append(('assistant', result.output))
    for i, tr in enumerate(tool_results):
        call = result.calls[i]
        if tr['ok']:
            parts.append(('tool', f'工具 "{call.name}" 执行结果：\n{tr["text"]}'))
        else:
            parts.append(('tool', f'工具 "{call.name}" 执行失败：{tr["text"]}'))
    session['pending_input'] = protocol.render_turn_input(parts)
    session['last_error'] = None
    # 状态保持 awaiting_llm，等待 LLM 根据工具结果继续响应
    return result.output


def check_terminal_task(session: dict):
    """检查 executing 状态的 terminal 任务是否完成。

    完成 → 记 tool_result 事件、生成下一轮输入、回 awaiting_llm。
    未完成 → 无操作（调用方轮询此函数）。
    """
    if session['state'] != 'executing':
        return
    task = session.get('terminal_task')
    if task is None:
        return
    status = tools.terminal_task_status(task)
    if not status['done']:
        return
    # 任务完成，生成结果
    output = status['output']
    if status['killed']:
        output += '\n(进程已被终止)'
    result = {
        'ok': status['exit_code'] == 0,
        'text': output,
    }
    sessions.append_event(session, 'tool_result', name='terminal', result=result)
    # 生成下一轮输入
    parts = []
    # 找到最近的 llm_output 文本
    for e in reversed(session['events']):
        if e['type'] == 'llm_output':
            parts.append(('assistant', e['text']))
            break
    if result['ok']:
        parts.append(('tool', f'工具 "terminal" 执行结果：\n{result["text"]}'))
    else:
        parts.append(('tool', f'工具 "terminal" 执行失败：{result["text"]}'))
    session['pending_input'] = protocol.render_turn_input(parts)
    session['last_error'] = None
    session['state'] = 'awaiting_llm'
    session['terminal_task'] = None


def kill_terminal_task(session: dict) -> str:
    """终止正在执行的 terminal 任务。

    返回终止后的输出文本（供前端展示）。
    """
    if session['state'] != 'executing':
        raise LoopError(f'当前状态 {session["state"]}，只有 executing 才能终止。')
    task = session.get('terminal_task')
    if task is None:
        raise LoopError('没有正在执行的 terminal 任务。')
    output = tools.kill_terminal_task(task)
    # 记 tool_result 事件并回 awaiting_llm
    result = {
        'ok': False,
        'text': output + '\n(进程已被终止)',
    }
    sessions.append_event(session, 'tool_result', name='terminal', result=result)
    parts = []
    for e in reversed(session['events']):
        if e['type'] == 'llm_output':
            parts.append(('assistant', e['text']))
            break
    parts.append(('tool', f'工具 "terminal" 执行失败：{result["text"]}'))
    session['pending_input'] = protocol.render_turn_input(parts)
    session['last_error'] = None
    session['state'] = 'awaiting_llm'
    session['terminal_task'] = None
    return output
