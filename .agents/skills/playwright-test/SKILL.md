---
name: playwright-test
description: 启动项目并用 Playwright 在浏览器中进行功能测试
user-invocable: true
---

# Playwright 功能测试

启动 Tool Sapiens 服务，用 Playwright MCP 工具在浏览器中执行端到端功能测试。

## ⚠️ 工具选择：只用 Playwright MCP，禁止使用 computer_use__*

本项目通过 MCP 配置了 **Playwright** 浏览器自动化工具。**必须**使用 `mcp__playwright__*` 系列工具操作浏览器。

**禁止**使用 `computer_use__*` 系列工具（如 `computer_use__launch_app`、`computer_use__list_apps`、`computer_use__get_window_state` 等）。这些是 macOS 桌面 UI 自动化工具，依赖 AX tree / WindowServer / TCC 权限，与网页测试无关，在非 macOS 平台或非桌面场景下会超时或报错。

### 正确用法

```
mcp__playwright__browser_navigate   → 打开 URL
mcp__playwright__browser_snapshot   → 获取页面可访问性快照（优先用这个，而非截图）
mcp__playwright__browser_click      → 点击元素
mcp__playwright__browser_type       → 输入文本
mcp__playwright__browser_take_screenshot → 截图（仅用于记录，不用于定位元素）
mcp__playwright__browser_wait_for   → 等待文本/时间
```

### 典型错误

```
❌ computer_use__launch_app {"name": "Edge"}   → 超时
❌ computer_use__list_apps {}                   → 超时
❌ computer_use__get_window_state {pid, ...}    → 超时
```

### 判断标准

如果工具名以 `computer_use__` 开头 → **不要用**。如果以 `mcp__playwright__` 开头 → **用这个**。

## 临时文件保存规范

测试过程中 Agent 自行保存的所有临时文件（截图、快照、日志等）**必须**保存在 `.playwright-mcp/tmp/` 目录下。

- `.playwright-mcp/` 目录由 Playwright MCP server 自动生成，包含自动保存的快照和截图
- 将 Agent 主动保存的临时文件集中放在 `.playwright-mcp/tmp/` 子目录，便于区分自动生成的文件和手动保存的文件
- `.playwright-mcp/` 已被 `.gitignore` 排除，不会提交到版本控制
- 不要将测试临时文件保存到项目根目录或其他位置
- 测试完成后可以清理 `.playwright-mcp/tmp/` 中的文件
- Playwright MCP server 自动生成的文件（如 `page-{timestamp}.yml`）会直接保存在 `.playwright-mcp/` 根目录，这是正常行为

## 测试流程

### 1. 启动服务

启动前先找一个可用的端口：从 **8765** 开始检查，如果 8765 被占用则依次递增，找到第一个未被占用的端口。

```powershell
# 查找可用端口（从 8765 开始递增）
$port = 8765
while ($true) {
    $conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if (-not $conn) { break }
    $port++
}
echo $port
```

记录输出的端口号，以后台模式启动服务：

```bash
python tool-sapiens.py --port <找到的端口>
```

等待几秒后用 `curl` 或 `mcp__playwright__browser_navigate` 确认服务就绪（HTTP 200）。

### 2. 打开浏览器

使用 `mcp__playwright__browser_navigate` 打开 `http://127.0.0.1:<找到的端口>/`。

### 3. 测试清单

按顺序执行以下测试，每项用 `mcp__playwright__browser_snapshot` 验证结果：

| # | 测试项 | 操作 | 预期结果 |
|---|--------|------|----------|
| 1 | 聊天页加载 | 导航到 `/` | 页面标题含"聊天"，有输入框和发送按钮 |
| 2 | 创建 session | 点击"新建 session" | URL 变为 `/#<hex>`，侧栏显示 session ID |
| 3 | 发送消息 | 输入文本，点击发送 | 消息出现在对话区，输入框禁用，显示等待 LLM 响应 |
| 4 | LLM 页加载 | 导航到 `/llm` | 页面标题含"LLM"，左侧显示 system prompt + 用户消息 |
| 5 | LLM 文字回复 | 在 LLM 页输入回复并提交 | 聊天页显示 LLM 回复，输入框恢复可用 |
| 6 | list 工具 | LLM 响应中写 `<tool name="list"><path>.</path></tool>` | 工具结果列出目录文件 |
| 7 | read 工具 | LLM 响应中写 `<tool name="read"><path>project-plan.md</path></tool>` | 工具结果返回文件内容 |
| 8 | create 工具 | LLM 响应中写 `<tool name="create"><path>test-output.txt</path><content>test</content></tool>` | 工具结果提示文件已创建 |
| 9 | edit 工具 | LLM 响应中写 `<tool name="edit">` 修改上一步创建的文件 | 工具结果提示已替换 |
| 10 | terminal 工具 | LLM 响应中写 `<tool name="terminal"><command>echo hello</command></tool>` | 工具结果包含 "hello" |

### 4. 截图

在关键步骤截图保存到 `.playwright-mcp/tmp/`：
- 聊天页初始状态
- 对话完成后全貌（fullPage）
- LLM 页状态

### 5. 清理

- 终止后台服务进程
- 删除测试创建的临时文件

### 6. 输出报告

以表格形式汇报每项测试的通过/失败状态，附上关键截图。如有失败项，描述具体现象。
