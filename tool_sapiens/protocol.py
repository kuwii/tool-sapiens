"""Tool Sapiens 协议：轮输入渲染、人类响应解析与错误说明。

- 渲染：把一轮的多段输入（system / user / 工具结果等）整合成一个长篇。
- 解析：人类响应为整篇散文，tag 外文本即 LLM 输出；工具调用以
  `<tool name="工具名">` 开、对应闭合 tag 结尾，参数用子 tag，内部原文不转义。
- 写坏（缺闭合 tag、未知工具名、缺参数等）时生成说明性错误，供 LLM 页展示重试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TOOL_OPEN = re.compile(r'<tool\s+name="([^"]+)"\s*>')
_PARAM_OPEN = re.compile(r'<([A-Za-z_][A-Za-z0-9_-]*)>')
_WHITESPACE = ' \t\r\n'


@dataclass
class ToolCall:
    name: str
    params: dict


@dataclass
class ParseResult:
    output: str = ''
    calls: list = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def render_turn_input(parts) -> str:
    """把本轮输入整合成一个长篇。parts 为 (角色, 内容) 列表。"""
    return '\n\n'.join(f'[{role}]\n{content}' for role, content in parts)


def build_system_prompt(tool_specs) -> str:
    """生成 system prompt。tool_specs：工具名 -> 参数名列表（全部必填）。"""
    lines = [
        '你是 Tool Sapiens 的大模型，这个 agent 的"智能"由你担任。',
        '',
        '【响应格式】',
        '- 直接用文字写出你的思考与对用户的回复；tag 外的文本就是你的输出。',
        '- 需要调用工具时，使用如下形式的 tag：',
        '',
        '<tool name="工具名">',
        '<参数名>参数值（原文，不用转义）</参数名>',
        '</tool>',
        '',
        '- 一次响应可以包含多个工具调用，按书写顺序依次执行，'
        '全部结果会作为下一轮输入回传给你。',
        '- tag 必须成对写完整；写坏时你会收到错误说明，重写即可。',
        '',
        '【当前可用工具】',
    ]
    if tool_specs:
        for name, params in tool_specs.items():
            if params:
                param_text = '、'.join(f'<{p}>' for p in params)
                lines.append(f'- {name}：参数 {param_text}')
            else:
                lines.append(f'- {name}：无参数')
    else:
        lines.append('无。请只用纯文本响应，不要包含任何工具调用 tag。')
    return '\n'.join(lines)


def parse_response(text: str, tool_specs) -> ParseResult:
    """解析人类响应。

    tool_specs：工具名 -> 参数名列表（全部必填，出现列表外的参数也算写坏）。
    解析成功返回 output 与按序的 ToolCall 列表；写坏时 error 说明怎么坏的。
    """
    output_parts = []
    calls = []
    pos = 0
    while True:
        open_idx = text.find('<tool', pos)
        close_idx = text.find('</tool>', pos)
        if open_idx == -1:
            if close_idx != -1:
                return ParseResult(error=_stray_closer_error())
            output_parts.append(text[pos:])
            break
        if close_idx != -1 and close_idx < open_idx:
            return ParseResult(error=_stray_closer_error())
        output_parts.append(text[pos:open_idx])
        call, end, error = _parse_block(text, open_idx, tool_specs)
        if error is not None:
            return ParseResult(error=error)
        calls.append(call)
        pos = end
    return ParseResult(output=''.join(output_parts).strip(), calls=calls)


def _stray_closer_error() -> str:
    return '多出了孤立的 </tool> 闭合 tag：前面没有与之配对的 <tool name="..."> 开始 tag。'


def _snippet(text: str, start: int) -> str:
    return text[start:start + 40].split('\n', 1)[0] or '…'


def _parse_block(text: str, start: int, tool_specs):
    """解析从 start（`<tool` 处）开始的一个工具调用块。

    返回 (ToolCall | None, 块结尾下标 | None, 错误说明 | None)。
    """
    opener = _TOOL_OPEN.match(text, start)
    if opener is None:
        return None, None, (
            f'工具调用开始 tag 写坏了："{_snippet(text, start)}"，'
            f'应写成 <tool name="工具名"> 的形式（name 用双引号）。'
        )
    name = opener.group(1)
    if name not in tool_specs:
        if tool_specs:
            known = '、'.join(tool_specs)
            return None, None, f'未知工具 "{name}"：当前可用工具有 {known}。'
        return None, None, (
            f'未知工具 "{name}"：当前没有可用工具，请只用纯文本响应。'
        )
    required = tool_specs[name]
    params = {}
    pos = opener.end()
    while True:
        while pos < len(text) and text[pos] in _WHITESPACE:
            pos += 1
        if pos >= len(text):
            return None, None, f'工具调用 "{name}" 缺少闭合 tag：响应在块中间就结束了。'
        if text.startswith('</tool>', pos):
            missing = [p for p in required if p not in params]
            if missing:
                missing_text = '、'.join(f'<{p}>' for p in missing)
                return None, None, f'工具调用 "{name}" 缺少参数：{missing_text}。'
            return ToolCall(name, params), pos + len('</tool>'), None
        param = _PARAM_OPEN.match(text, pos)
        if param is None:
            return None, None, (
                f'工具调用 "{name}" 内部写坏了："{_snippet(text, pos)}"。'
                f'参数要写成 <参数名>参数值</参数名> 的成对子 tag，参数之间只留空白。'
            )
        pname = param.group(1)
        if pname in params:
            return None, None, f'工具调用 "{name}" 的参数 <{pname}> 写了两次。'
        if pname not in required:
            expected = '、'.join(f'<{p}>' for p in required) or '（无参数）'
            return None, None, (
                f'工具调用 "{name}" 含未知参数 <{pname}>，它需要的参数是：{expected}。'
            )
        closer = f'</{pname}>'
        close_idx = text.find(closer, param.end())
        if close_idx == -1:
            return None, None, f'工具调用 "{name}" 的参数 <{pname}> 缺少闭合 tag {closer}。'
        params[pname] = text[param.end():close_idx]
        pos = close_idx + len(closer)
