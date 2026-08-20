"""六个工具执行器：list / read / create / edit（阶段 3）+ terminal（阶段 4）。

所有文件路径相对于工具工作目录（服务器启动时的工作目录）。
无沙箱——人类即信任边界。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading

# 工具工作目录。main() 启动时初始化为 os.getcwd()。
work_dir: str = ''


def set_work_dir(path: str):
    """设置工具工作目录（main 启动时调用）。"""
    global work_dir
    work_dir = os.path.abspath(path)


def _resolve(path: str) -> str:
    """将相对路径解析到工具工作目录下，防路径穿越。"""
    target = os.path.realpath(os.path.join(work_dir, path))
    if not target.startswith(work_dir + os.sep) and target != work_dir:
        raise ToolError(f'路径不允许："{path}" 超出了工作目录。')
    return target


class ToolError(Exception):
    """工具执行时返回给 LLM 的错误。"""
    pass


# 工具名 -> 参数名列表（全部必填）。
TOOL_SPECS = {
    'list': ['path'],
    'read': ['path'],
    'create': ['path', 'content'],
    'edit': ['path', 'old', 'new'],
    'terminal': ['command'],
}


def execute(name: str, params: dict) -> dict:
    """执行同步工具调用。返回 {'ok': bool, 'text': str}。

    terminal 工具是异步的，不经过此分发——由 loop 层单独处理。
    """
    dispatch = {
        'list': _list,
        'read': _read,
        'create': _create,
        'edit': _edit,
    }
    fn = dispatch.get(name)
    if fn is None:
        return {'ok': False, 'text': f'内部错误：工具 "{name}" 未注册。'}
    try:
        return fn(params)
    except ToolError as exc:
        return {'ok': False, 'text': str(exc)}
    except OSError as exc:
        return {'ok': False, 'text': f'文件系统错误：{exc}'}


def _list(params: dict) -> dict:
    """列出目录内的文件与子目录。"""
    path = params['path']
    target = _resolve(path)
    if not os.path.isdir(target):
        raise ToolError(f'路径不是目录："{path}"')
    entries = sorted(os.listdir(target))
    if not entries:
        return {'ok': True, 'text': f'目录 "{path}" 为空。'}
    lines = []
    for name in entries:
        full = os.path.join(target, name)
        kind = 'dir' if os.path.isdir(full) else 'file'
        lines.append(f'  {kind}  {name}')
    return {'ok': True, 'text': f'{path}/\n' + '\n'.join(lines)}


def _read(params: dict) -> dict:
    """读取文件内容。"""
    path = params['path']
    target = _resolve(path)
    if not os.path.isfile(target):
        raise ToolError(f'文件不存在或不是普通文件："{path}"')
    with open(target, encoding='utf-8', errors='replace') as f:
        content = f.read()
    return {'ok': True, 'text': content}


def _create(params: dict) -> dict:
    """创建文件（写入完整内容）。文件已存在时覆盖。"""
    path = params['path']
    content = params['content']
    target = _resolve(path)
    # 确保父目录存在
    parent = os.path.dirname(target)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(target, 'w', encoding='utf-8') as f:
        f.write(content)
    return {'ok': True, 'text': f'文件已创建："{path}"（{len(content)} 字符）。'}


def _edit(params: dict) -> dict:
    """原文 → 新文替换。原文未命中时返回错误，由 LLM 自行重试。"""
    path = params['path']
    old = params['old']
    new = params['new']
    target = _resolve(path)
    if not os.path.isfile(target):
        raise ToolError(f'文件不存在："{path}"')
    with open(target, encoding='utf-8', errors='replace') as f:
        content = f.read()
    if old not in content:
        return {
            'ok': False,
            'text': (
                f'原文未命中：在 "{path}" 中找不到要替换的文本片段。\n'
                f'请读取文件确认当前内容后再重试。'
            ),
        }
    # 只替换第一次出现
    new_content = content.replace(old, new, 1)
    with open(target, 'w', encoding='utf-8') as f:
        f.write(new_content)
    return {'ok': True, 'text': f'已替换 "{path}" 中的一处文本。'}


# ── Terminal 异步执行（阶段 4）─────────────────────────────────────────

def start_terminal_task(command: str) -> dict:
    """启动 terminal 命令的后台执行。

    返回 task 字典，包含输出缓冲、进程句柄和线程。
    调用方（loop）负责将 task 存到 session 中并推进状态。
    """
    output_lock = threading.Lock()
    output_chunks: list[str] = []

    def _append(text: str):
        with output_lock:
            output_chunks.append(text)

    def _run():
        try:
            if sys.platform == 'win32':
                creation_flags = 0x00000200  # CREATE_NEW_PROCESS_GROUP
            else:
                creation_flags = 0
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=work_dir,
                creationflags=creation_flags,
            )
            with output_lock:
                task['proc'] = proc
            # communicate() 安全地读取并等待进程结束
            stdout_bytes, _ = proc.communicate()
            if stdout_bytes:
                _append(stdout_bytes.decode('utf-8', errors='replace'))
            with output_lock:
                task['exit_code'] = proc.returncode
                task['done'] = True
        except Exception as exc:
            with output_lock:
                _append(f'(执行异常: {exc})')
                task['exit_code'] = -1
                task['done'] = True

    task: dict = {
        'command': command,
        'output_lock': output_lock,
        'output_chunks': output_chunks,
        'proc': None,
        'thread': threading.Thread(target=_run, daemon=True),
        'done': False,
        'exit_code': None,
        'killed': False,
    }
    task['thread'].start()
    return task


def terminal_task_status(task: dict) -> dict:
    """返回 terminal task 的当前状态快照。

    返回 {'done': bool, 'output': str, 'exit_code': int|None, 'killed': bool}。
    """
    with task['output_lock']:
        return {
            'done': task['done'],
            'output': ''.join(task['output_chunks']),
            'exit_code': task['exit_code'],
            'killed': task['killed'],
        }


def kill_terminal_task(task: dict) -> str:
    """终止 terminal task 的进程树。返回已有输出。

    Windows 下杀进程组；Linux/macOS 下杀进程 + 进程组。
    """
    with task['output_lock']:
        proc = task['proc']
        already_killed = task['killed']
        if already_killed:
            return ''.join(task['output_chunks'])
        task['killed'] = True
    if proc is None:
        return terminal_task_status(task)['output']
    try:
        if sys.platform == 'win32':
            # Windows: 杀进程组（负 OS process id = process group id）
            os.kill(-proc.pid, signal.CTRL_BREAK_EVENT)
        else:
            try:
                os.killpg(os.getpgid(proc.pid), 15)  # TERM
            except OSError:
                proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except OSError:
            pass
    with task['output_lock']:
        task['done'] = True
        task['exit_code'] = -1
    return terminal_task_status(task)['output']


def terminal_task_output(task: dict) -> str:
    """返回当前已收集的输出（不阻塞）。"""
    with task['output_lock']:
        return ''.join(task['output_chunks'])
