# Autodati 交接文档

> 写于 2026-10-18。面向接手开发的人：这份文档说明**现在是什么状态、为什么这样设计、哪里还没验证**。
> 面向使用者的说明在 [README.md](README.md)。

## 1. 项目位置与运行

- 代码目录：`E:\WorkSpace\Autodati\Stealth-Auto-Answer-Helper\`（用户自己从 `E:\WorkSpace\Autodati\` 移入该子目录，**不是** git 仓库）
- 运行：`python main.py`（已在 Python 3.14.8 / Windows 11 26200 / 1920×1080 / DPI 100% 下验证）
- 依赖：`pip install -r requirements.txt`（mss、pillow、pyautogui、keyboard、pystray、requests）
- 建议管理员运行：`keyboard` 的全局热键在提权程序中需要管理员权限

## 2. 当前交互模型（最重要）

用户经过多轮调整后确定了现在的两热键模型，**不要再改回“一个热键走完全流程”**：

| 热键 | 行为 |
| --- | --- |
| `Ctrl+Alt+Y` | 截全屏 → 调多模态 AI → 得到这一题的答案，存成“当前轮次”。**会丢弃上一轮未输入完的剩余答案**（用户明确要求：重新截图就以新答案为准）。若判为选择题，顺带把鼠标平滑移到选项上方，不点击。 |
| `Ctrl+Alt+H` | 取当前轮次里**下一个尚未输入的**答案，输入到光标所在位置。**一次只输入一项**。 |
| `Esc` / 鼠标移到屏幕左上角 | 中止 |

多空题的完整操作：按 Y 拿到 `["3","4","90","120"]` → 用户点第 1 空 → 按 H 输入 `3` → 用户点第 2 空 → 按 H 输入 `4` → …… 依此类推。

### 为什么改成这样

最初是“AI 返回坐标 → 程序点那个输入框 → 输入”。实测（日志 14:18–14:19）暴露三个问题：

1. 视觉模型把填空题的输入框当成可悬停的“选项”，返回漂浮坐标（点到了 `(1056,353)`）；
2. 4 空题时模型没按新格式返回，直接把 `3,4,90,120` 当单答案整串输出；
3. 同一页面模型置信度在 0.60–0.95 间波动，坐标误差几十像素。

结论：**让视觉模型做像素级定位不可靠**。现在的设计把“定位输入框”外包给用户（用户自己点），程序只负责“知道有几个空、下一个该填什么”，这类信息模型给得很准。`move_mouse` / `clear_before_typing` 仍保留在配置里。

### 已废弃的旧设计（了解即可，避免误改回去）

- `Pipeline.start_async()` 一体化流程 → 已拆成 `start_scan()` / `start_type()`
- `Answer.blank_targets`、点击输入框聚焦、`Config.click_target`
- `current_blank` 字段仍保留在 `Answer` 里，**但新提示词已不再要求模型返回它**；解析逻辑保留以兼容旧格式。

## 3. 代码结构

| 文件 | 职责 | 改动注意 |
| --- | --- | --- |
| `main.py` | DPI 感知、单实例锁、启动 UI | DPI 必须在创建窗口前设置 |
| `ui.py` | tkinter 界面、托盘、状态轮询 | `_poll_status` 每 150ms 刷新两个按钮可用态 |
| `pipeline.py` | **核心**：`start_scan` / `start_type` 两段式，`Session` 管理待输入队列 | 所有耗时操作必须放在工作线程 |
| `ai_client.py` | OpenAI 兼容请求、`SYSTEM_PROMPT`、JSON 容错解析、`Answer` | 提示词改动对识别准确率影响最大 |
| `input_engine.py` | 逐字输入、贝塞尔鼠标移动、`PyAutoGUIKeyboard` 适配器 | 见下方坑 1 |
| `humanizer.py` | 对数正态击键间隔、错字自纠、鼠标时长 | |
| `screen.py` | mss 截图、缩放、JPEG data URL | |
| `hotkey.py` | 注册 `SCAN_HOTKEY` / `TYPE_HOTKEY` / `ABORT_HOTKEY` | 常量集中在这里 |
| `config.py` | `Config` 数据类、DPAPI 加密 API Key | 新增配置项要同时改 `ui.py` 的 vars 和 `_collect()` |
| `storage.py` | `logs/history.jsonl` | |
| `prompts.py` | 提示词模板 | |
| `logging_setup.py` | 日志 + API Key 打码 | |

`Answer` 关键方法：`answers_in_order()`（本次要依次输入的全部项）、`next_answer()`、`advance_blank()`、`mark_filled()`。

## 4. 踩过的坑（务必先读）

1. **pyautogui 没有 `press`/`release`**，只有 `keyDown`/`keyUp`。曾因为测试用自建假键盘实现了 `press/release` 而漏检，线上报 `AttributeError: module 'pyautogui' has no attribute 'release'`。现在由 `PyAutoGUIKeyboard` 适配器隔离，`tests/test_core.py::TestPyAutoGUIBackend` 专门盯着这个接口。
2. **测试桩必须模拟真实返回类型**。`pyautogui.position()` 返回 `Point`（有 `.x/.y` 也可解包），`pyautogui.click` 等必须打桩，否则测试会真的去操作桌面。`_check_stop` 已同时兼容 Point 和元组。
3. **中文弯引号会直接导致 SyntaxError**。编辑 `SYSTEM_PROMPT` 这类长字符串时，若写作工具把 `"` 换成 `“”`，整个模块无法导入。每次改完跑一次 `python -m compileall`。
4. **本环境的自动化进程无法把按键送入前台应用**。`SendInput` 返回成功、记事本也拿到焦点，但字符不落地，`pyautogui.write` / `hotkey` 同样无效。因此**真实键入只能由用户手工验证**，不要为此反复尝试。
5. **多空题答案被合并**是模型行为，不是解析 bug。靠提示词（明确要求 `blanks` 数组、给出反例）缓解；解析侧无法可靠拆分 `"3,4,90,120"`（逗号也可能是答案本身）。

## 5. 测试现状

| 命令 | 覆盖 | 状态 |
| --- | --- | --- |
| `python tests\test_core.py` | 21 项：时间分布、逐字输入、错字回删、中止、JSON 容错、pyautogui 接口适配 | 通过 |
| `python tests\test_two_hotkeys.py` | 17 项检查：四空题逐次 H 只输一项、重按 Y 丢弃旧答案、单空题、无答案时 H 不动作、不自动点击 | 通过 |
| `python tests\ui_build.py` | 无事件循环构建界面、配置收集 | 通过 |
| `python tests\ui_shot.py [api\|beh]` | 截图到 `logs\ui_shot.png`，人工/模型目检界面 | 通过 |
| `python tests\e2e_check.py` | 本地假 OpenAI 服务跑请求链路 | 通过（可能需更新为两段式） |
| `python tests\live_pipeline.py` | 记事本 + 真实键鼠 | **需用户手工跑** |
| `python tests\live_smoke.py` | 真实截图 + 真实鼠标 | 通过 |

**未验证 / 已知风险**

- 提示词改动（`blanks` 必须分开）在 `deepseek-v4.1-flash` 上的实际效果**尚未回归验证**，需要用户用真实页面测一次；若仍返回合并字符串，解析会把它当单答案只填第 1 空。
- 输入前清空用的是 `Ctrl+A` + `Delete`，在富文本框里行为可能不同。
- 中文答案走剪贴板粘贴，会覆盖用户剪贴板。

## 6. 下一步建议

1. **先验证提示词**：让用户用真实 4 空题按 Y，检查 `logs/autodati.log` 里 `本轮答案 ... 共 N 项`，确认 N 是否为 4。
2. 若模型仍合并答案，在 `parse_answer` 增加“按页面上检测到的输入框数量拆分”的兜底，但要谨慎：仅当 `blank_count > 1` 且答案中含分隔符时启用。
3. 把 `log_dir()` 返回的路径从 `logs/` 挪到 `%LOCALAPPDATA%`（当前与代码混放，截图与历史都在 `logs/`）。
4. `tests/e2e_check.py` 仍按旧的一体化流程打桩，若继续使用需改成 `start_scan()` + `start_type()`。

## 7. 用户偏好

- 中文交流；回答先说结论，别铺垫。
- 不允许程序自动点击选项、不允许按回车提交；选择题只悬停，点击由用户做。
- 重新按 `Ctrl+Alt+Y` 必须无条件以新截图为准。
- 调试时喜欢看具体证据（日志行号、测试结果），不接受“应该可以”的推测。
