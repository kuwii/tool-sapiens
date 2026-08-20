# Tool Sapiens

"Agent driven by AI (All-human, Ironically)" / "由能工智人驱动的智能体"。

整活性质的伪 agent：软件是 agent，"智能"由人扮演。纯 Python 标准库后端 + 手搓前端，零外部依赖，只响应 127.0.0.1。

## 背景

- `brainstorm-brief.md` — 共识简报（定位、动机、形态、成功标准、结论）。
- `brainstorm-minutes.md` — 对话纪要（决策理由）。

## 代码结构

按库布局组织，便于日后做成库：

- `tool-sapiens.py` — 根目录薄入口，只调用 `tool_sapiens.main()`；启动方式 `python tool-sapiens.py [--port N]`。
- `tool_sapiens/` — 全部实现：`server.py`、`sessions.py`、`loop.py`、`protocol.py`、`tools.py`。前端静态资源与持久化读写也收进包内。
- `tests/` — unittest，从根目录跑 `python -m unittest`。

## agent 知识索引

- `.agents/skills/architecture/SKILL.md` — 架构方案与接口约定。

## 规则

- 零外部依赖：Python 仅标准库，前端手搓，不引入第三方库/CDN。
- 开发中发现需要改动需求/架构时，先与用户确认，不就地改。
