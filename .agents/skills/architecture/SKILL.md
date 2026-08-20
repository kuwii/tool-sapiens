---
name: architecture
description: Tool Sapiens 架构方案——技术选型及理由、模块划分、核心数据流、关键接口约定（端点、状态机、tag 协议、持久化格式）
user-invocable: false
---

# Tool Sapiens 架构方案

## 技术选型及理由

- 后端：Python 标准库 `http.server.ThreadingHTTPServer`。零依赖硬约束；线程模型匹配"少量长时子进程 + 轮询"形态。
- 前端：手搓 HTML/CSS/JS，双页面，`fetch` 轮询。
- 持久化：每 session 一个 JSON 文件，目录 `~/.local/share/tool-sapiens/sessions/`。
- 网络边界：socket 直接 bind 127.0.0.1，在网络层拒绝外部连接（不依赖 Host 头检查）。
- 并发：单用户本地场景，每 session 一把锁。
- 零外部依赖：Python 仅标准库，前端无第三方库/CDN。

## 模块划分

按库布局组织：实现都在 `tool_sapiens/` 包内，根目录 `tool-sapiens.py` 是薄入口（只调 `tool_sapiens.main()`）；包内用相对导入。

- `tool_sapiens/server`：路由、静态资源、AJAX 端点。
- `tool_sapiens/sessions`：session 存储（创建/列表/加载/持久化/事件追加）。
- `tool_sapiens/loop`：状态机与 loop 推进（生成轮输入、调度工具执行、状态转移）。
- `tool_sapiens/protocol`：渲染"给 LLM 的输入"（整合长篇）；解析人类响应（原文 tag 扫描）；写坏时生成说明性错误。
- `tool_sapiens/tools`：五个工具执行器；terminal 后台线程执行，Windows 下按进程组杀（子进程树一起死）。
- 前端：chat 页、llm 页、共享轮询/渲染逻辑（静态资源也放包内）。

## 核心数据流

- 聊天页 POST prompt → loop 生成本轮输入（首轮含 system prompt）→ 状态 `awaiting_llm`，pending 输入落盘 → LLM 页轮询展示。
- LLM 页 POST 响应 → protocol 解析：
  - 写坏 → 保持 `awaiting_llm`，附 `last_error` → LLM 页展示重试；
  - 无工具调用 → 记 LLM 输出事件 → `idle`；
  - 有工具调用 → 顺序执行；terminal 进入 `executing`（聊天页显示运行中 + 终止按钮）；结束/被终止 → 工具结果事件 → 生成下一轮输入 → `awaiting_llm`。
- 两页轮询各自端点，增量渲染。

## 关键接口约定

### 状态机

三态：`idle`（等待用户提示词）/ `awaiting_llm`（可附 last_error）/ `executing`（terminal 运行中）。

### tag 协议

- tag 外文本 = LLM 输出。
- 工具调用以 `<tool name="工具名">` 开、以对应的闭合 tag（`</` + `tool>`）结尾；参数用子 tag（如 `<path>`、`<old>`、`<new>`、`<content>`、`<command>`），内部原文不转义；解析器扫描配对闭合 tag。一次响应可含多个工具调用，顺序执行。
- 示例：

```text
我来修这个 bug。
<tool name="edit">
<path>src/main.py</path>
<old>def add(a, b):
    return a - b</old>
<new>def add(a, b):
    return a + b</new>
</tool>
```

- 写坏（缺闭合 tag、未知工具名、缺参数等）→ `last_error` 说明怎么坏的，人类重试。

### HTTP 端点

- `GET /`（聊天页）、`GET /llm`（LLM 页）、静态资源
- `GET /api/sessions`（列表）、`POST /api/sessions`（新建）
- `GET /api/sessions/{id}` — 聊天页轮询：状态 + 事件流（含 terminal 运行信息）
- `POST /api/sessions/{id}/prompt` — 用户提交提示词
- `POST /api/sessions/{id}/kill` — 终止 terminal
- `GET /api/llm/{id}` — LLM 页轮询：pending 输入 + last_error
- `POST /api/llm/{id}/response` — 人类提交响应

### 持久化格式

每 session 一个 JSON：`{meta: {id, title, created_at}, events: [], state, pending_input, last_error}`。
事件类型：`user_prompt` / `llm_output` / `tool_call` / `tool_result`（append-only，可扩展）。

### 其他

- 端口默认 8765，命令行参数可覆盖。
- Windows 下 terminal 终止按进程组杀，保证子进程树一起被杀。
