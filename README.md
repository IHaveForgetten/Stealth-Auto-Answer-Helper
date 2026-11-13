# Autodati · 屏幕答题助手

`Ctrl + Alt + Y` 截屏取答案 → `Ctrl + Alt + H` 把答案按人的节奏逐字符打进输入框。

## 两个热键

| 热键 | 作用 |
| --- | --- |
| `Ctrl + Alt + Y` | 截全屏并让 AI 作答。会**丢弃上一轮还没输入完的剩余答案**，一切以这次截图为准。选择题会顺带把鼠标移到选项上方（不点击）。 |
| `Ctrl + Alt + H` | 把**下一个**答案输入到当前光标位置。一次只输入一项。 |
| `Esc` / 鼠标甩到左上角 | 立即中止 |

命令按钮同样有「取答案」「输入一项」「中止」三个，对应前两个热键。

## 多空题怎么用

以 4 个空为例：

1. 按 `Ctrl+Alt+Y`，AI 返回 4 项答案，状态栏显示「已得到 4 个答案；每按一次 CTRL + ALT + H 输入一个，下一个：3」。
2. 鼠标点第 1 个空，按 `Ctrl+Alt+H`，程序输入 `3`，状态栏提示「请切到下一个空后按 CTRL + ALT + H（剩余 3 项）」。
3. 点第 2 个空，再按 `Ctrl+Alt+H`，输入 `4`；重复直到填完。
4. 填错或题目变了，直接再按 `Ctrl+Alt+Y` 重新取答案，旧答案作废。

## 行为约定

- 选择题：只悬停到选项上方，**点击由你完成**。
- 填空题：只往你当前光标所在的输入框里打字，**不自己找输入框、不自己点**。
- 不会按回车、不会提交、不会切换窗口。
- 输入前默认会 `Ctrl+A` + `Delete` 清空该输入框，避免新旧答案连在一起（可在设置里关闭）。

## 安装

```bat
pip install -r requirements.txt
```

Windows 11 + Python 3.14 已验证。依赖：`mss`、`pillow`、`pyautogui`、`keyboard`、`pystray`、`requests`。

## 运行

```bat
python main.py
```

建议以管理员身份运行（`keyboard` 的全局热键在部分全屏/提权程序中需要管理员权限，界面状态栏会提示）。关闭窗口即退出；最小化后会驻留系统托盘。

## 配置

界面「API」页填写：

| 字段 | 说明 |
| --- | --- |
| Base URL | OpenAI 兼容地址，例如 `https://api.openai.com/v1`、`https://dashscope.aliyuncs.com/compatible-mode/v1`、本地 `http://127.0.0.1:8000/v1` |
| 模型 | 必须支持图像输入，例如 `gpt-4o`、`qwen-vl-max`、`glm-4v` |
| API Key | 用 Windows DPAPI 加密后保存在 `config.json`（仅当前 Windows 用户可解密） |

「测试连接」会请求 `/models` 检查 Key 与网络。

## 拟人化参数

| 参数 | 作用 |
| --- | --- |
| 输入速度 | 快 / 标准 / 慢，对应每字符延迟中位数 45 / 85 / 140 ms |
| 错字自纠概率 | 按该概率打出相邻键（QWERTY 邻位），停顿后 Backspace 更正 |
| 选择题移动鼠标 | 关闭后取答案时不动鼠标 |
| 输入前清空当前空 | 按 H 输入前先 Ctrl+A + Delete，清掉光标所在输入框的旧内容 |
| 开始前停顿 | 截屏前等待的秒数范围 |

击键间隔取对数正态分布并叠加随机长停顿；鼠标轨迹为随机三次贝塞尔曲线 + 缓入缓出 + 末端 1-2px 修正。

## 提示词模板

「API」页可选模板。多空页面建议选 **多空填空（每空一项）**。

程序只关心一个字段：`blanks`。模型返回的 JSON：

```json
{"type": "fill", "blank_count": 4, "answer": "3",
 "blanks": ["3", "4", "90", "120"], "confidence": 0.9}
```

- `blanks`：按页面顺序排列的每个空的答案，长度即空数。**每按一次 H 消耗一项**。
- `answer`：单空题或简答题的答案。
- `target`：仅选择题使用，指向要选的那个选项，程序只把鼠标移过去。

## 目录结构

```
main.py          入口、DPI 感知、单实例
ui.py            tkinter 界面与托盘
pipeline.py      两段式流程：取答案 / 输入下一项
screen.py        mss 截屏、缩放、JPEG data URL
ai_client.py     OpenAI 兼容请求与 JSON 容错解析
input_engine.py  逐字符输入、贝塞尔鼠标移动
humanizer.py     时间分布、错字、鼠标参数
hotkey.py        全局热键（Y / H / Esc）
config.py        配置读写与 DPAPI 加密
storage.py       答题历史
prompts.py       提示词模板
HANDOVER.md      开发交接文档
logs/            autodati.log、history.jsonl
```

## 自测

```bat
python tests\test_core.py          :: 21 项逻辑单测（假键鼠，不触发真实输入）
python tests\test_two_hotkeys.py   :: 两热键流程：逐空输入、重按 Y 丢弃旧答案
python tests\e2e_check.py          :: 本地假 OpenAI 服务，验证请求链路
python tests\live_smoke.py         :: 真实截图 + 真实鼠标移动（不按键）
python tests\ui_build.py           :: 无事件循环构建界面
python tests\ui_shot.py [api|beh]  :: 截图界面到 logs\ui_shot.png
```

## 常见问题

- **热键没反应**：可能已有一个实例在跑（单实例锁），或需要以管理员身份启动。
- **按 H 没反应**：先看状态栏——没有待输入答案时会提示先按 Y。也可能是上一轮还在运行。
- **多个空的答案被串进同一格**：说明模型没按格式把答案分开。重新按 Y，或换用「多空填空（每空一项）」模板。
- **答案为空 / 未识别**：换用更强的视觉模型，或把「截图最长边」提到 2048。
- **中文答案**：程序会退化为剪贴板粘贴（Ctrl+V），会覆盖你当前的剪贴板。
- **API 返回 400**：部分服务不接受 `response_format`，程序会自动去掉后重试一次。
