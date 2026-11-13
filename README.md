# Autodati · 屏幕答题助手

按下 **Ctrl + Alt + Y** → 截屏 → 交给多模态 AI → 按人的节奏把答案逐字符打出来。

## 行为约定

软件只会做两件事，**不会点击、不会按回车、不会切换窗口**：

1. 若题目是选择题且 AI 定位到了选项坐标：把鼠标平滑移动到该选项上方，由你决定是否点击。
2. 否则：把答案逐字符键入到你当前光标所在的位置。

任何时候按 **Esc**，或把鼠标甩到屏幕左上角，都会立即停止。

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
| 选择题移动鼠标 | 关闭后只输入字母，不移动鼠标 |
| 开始前停顿 | 触发后等待的秒数范围 |

击键间隔取对数正态分布并叠加随机长停顿；鼠标轨迹为随机三次贝塞尔曲线 + 缓入缓出 + 末端 1–2px 修正。

## 目录结构

```
main.py          入口、DPI 感知、单实例
ui.py            tkinter 界面与托盘
pipeline.py      截屏 → AI → 移动/输入 的主流程
screen.py        mss 截屏、缩放、JPEG data URL
ai_client.py     OpenAI 兼容请求与 JSON 容错解析
input_engine.py  逐字符输入、贝塞尔鼠标移动
humanizer.py     时间分布、错字、鼠标参数
hotkey.py        全局热键
config.py        配置读写与 DPAPI 加密
storage.py       答题历史
prompts.py       提示词模板
logs/            autodati.log、history.jsonl
```

## 自测

```bat
python tests\test_core.py    :: 13 项逻辑单测（假键鼠，不触发真实输入）
python tests\e2e_check.py    :: 本地假 OpenAI 服务，跑通完整流程
python tests\live_smoke.py   :: 真实截图 + 真实鼠标移动（不按键）
python tests\ui_build.py     :: 无事件循环构建界面
```

## 常见问题

- **热键没反应**：可能已有一个实例在跑（单实例锁），或需要以管理员身份启动。
- **答案为空 / 未识别**：换用更强的视觉模型，或把「截图最长边」提到 2048；也可在提示词模板里选「自定义」写死场景。
- **中文答案**：程序会退化为剪贴板粘贴（Ctrl+V），因为无法在没有输入法上下文时逐字键入中文。
- **坐标偏差**：程序已做 DPI 感知；若使用多显示器且主屏不在 (0,0)，仍可能出现偏移，建议只用主屏。
- **API 返回 400**：部分服务不接受 `response_format`，程序会自动去掉后重试一次。
