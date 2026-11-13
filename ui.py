"""tkinter 主界面：配置、状态、手动触发。"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import tkinter as tk
from dataclasses import replace
from tkinter import ttk, messagebox

import requests

import storage
from ai_client import parse_answer
from config import SPEED_PRESETS, Config, save_config
from hotkey import HotkeyManager, SCAN_HOTKEY, TYPE_HOTKEY, pretty
from logging_setup import get_logger, log_dir
from pipeline import Pipeline
from prompts import TEMPLATES, DEFAULT_TEMPLATE

log = get_logger()

APP_TITLE = "Autodati · 屏幕答题助手"
PAD = 10


class App:
    def __init__(self, root: tk.Tk, cfg: Config) -> None:
        self.root = root
        self.cfg = cfg
        self.status_q: queue.Queue[str] = queue.Queue()
        self.pipeline = Pipeline(cfg, self._status_from_thread)
        self.hotkeys = HotkeyManager()
        self._tray = None
        self._busy_tick = 0

        root.title(APP_TITLE)
        root.geometry("560x620")
        root.minsize(520, 560)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.vars = {
            "base_url": tk.StringVar(value=cfg.base_url),
            "model": tk.StringVar(value=cfg.model),
            "api_key": tk.StringVar(value=cfg.api_key),
            "max_image_side": tk.StringVar(value=str(cfg.max_image_side)),
            "speed": tk.StringVar(value=cfg.speed),
            "typo_rate": tk.DoubleVar(value=cfg.typo_rate * 100),
            "move_mouse": tk.BooleanVar(value=cfg.move_mouse),
            "click_target": tk.BooleanVar(value=cfg.click_target),
            "clear_before_typing": tk.BooleanVar(value=cfg.clear_before_typing),
            "prompt_template": tk.StringVar(value=cfg.prompt_template),
            "custom_prompt": tk.StringVar(value=cfg.custom_prompt),
            "start_delay": tk.StringVar(
                value=f"{cfg.start_delay_min:g}-{cfg.start_delay_max:g}"
            ),
        }

        self._build()
        self.hotkeys.register(self.scan, self.type_next, self.abort)
        self._poll_status()
        self.root.after(300, self._check_admin)

    # ------------------------------------------------------------ 布局

    def _build(self) -> None:
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=PAD, pady=(PAD, 0))

        api = ttk.Frame(nb, padding=PAD)
        beh = ttk.Frame(nb, padding=PAD)
        nb.add(api, text="API")
        nb.add(beh, text="拟人化")
        self.notebook = nb

        # ---- API 页
        api.columnconfigure(0, weight=1)
        rows = [
            ("Base URL", "base_url", "例如 https://api.openai.com/v1"),
            ("模型", "model", "需支持图像输入，例如 gpt-4o / qwen-vl-max / glm-4v"),
            ("API Key", "api_key", "以 DPAPI 加密保存在本地 config.json"),
        ]
        r = 0
        for label, key, hint in rows:
            ttk.Label(api, text=label).grid(row=r, column=0, sticky="w", pady=(8, 2))
            r += 1
            ttk.Entry(
                api, textvariable=self.vars[key], show="•" if key == "api_key" else ""
            ).grid(row=r, column=0, sticky="ew")
            r += 1
            ttk.Label(api, text=hint, foreground="#777").grid(row=r, column=0, sticky="w")
            r += 1

        ttk.Label(api, text="截图最长边（像素）").grid(row=r, column=0, sticky="w", pady=(10, 2))
        r += 1
        ttk.Combobox(
            api, textvariable=self.vars["max_image_side"],
            values=["768", "1024", "1568", "2048"], width=10, state="readonly",
        ).grid(row=r, column=0, sticky="w")
        r += 1

        ttk.Label(api, text="提示词模板").grid(row=r, column=0, sticky="w", pady=(10, 2))
        r += 1
        tpl_values = list(TEMPLATES.keys()) + ["自定义"]
        ttk.Combobox(
            api, textvariable=self.vars["prompt_template"], values=tpl_values,
            state="readonly", width=24,
        ).grid(row=r, column=0, sticky="w")
        r += 1

        ttk.Label(api, text="自定义提示词（仅当模板选「自定义」时生效）").grid(
            row=r, column=0, sticky="w", pady=(10, 2)
        )
        r += 1
        self.custom_text = tk.Text(api, height=4, wrap="word")
        self.custom_text.grid(row=r, column=0, sticky="ew")
        self.custom_text.insert("1.0", self.cfg.custom_prompt)
        r += 1

        btns = ttk.Frame(api)
        btns.grid(row=r, column=0, sticky="w", pady=(12, 0))
        ttk.Button(btns, text="保存配置", command=self.save).pack(side="left")
        ttk.Button(btns, text="测试连接", command=self.test_connection).pack(side="left", padx=6)
        ttk.Button(btns, text="打开配置目录", command=self._open_config_dir).pack(side="left")

        # ---- 拟人化页
        r = 0
        ttk.Label(beh, text="输入速度").grid(row=r, column=0, sticky="w")
        speed_frame = ttk.Frame(beh)
        speed_frame.grid(row=r, column=1, sticky="w")
        for key, preset in SPEED_PRESETS.items():
            ttk.Radiobutton(
                speed_frame, text=f"{preset['label']}（{preset['median'] * 1000:.0f}ms/字）",
                value=key, variable=self.vars["speed"],
            ).pack(side="left", padx=(0, 10))

        r += 1
        ttk.Label(beh, text="错字自纠概率").grid(row=r, column=0, sticky="w", pady=(10, 0))
        box = ttk.Frame(beh)
        box.grid(row=r, column=1, sticky="ew", pady=(10, 0))
        ttk.Scale(box, from_=0, to=20, variable=self.vars["typo_rate"], orient="horizontal",
                  length=200, command=lambda _=None: self._sync_typo_label()).pack(side="left")
        self.typo_label = ttk.Label(box, text=f"{self.vars['typo_rate'].get():.0f}%", width=5)
        self.typo_label.pack(side="left", padx=6)

        r += 1
        ttk.Checkbutton(
            beh, text="选择题：把鼠标平滑移到选项上方（不点击）",
            variable=self.vars["move_mouse"],
        ).grid(row=r, column=0, columnspan=2, sticky="w", pady=(10, 0))

        r += 1
        ttk.Checkbutton(
            beh, text="输入前清空当前空（Ctrl+A + Delete），避免新旧答案连在一起",
            variable=self.vars["clear_before_typing"],
        ).grid(row=r, column=0, columnspan=2, sticky="w", pady=(6, 0))

        r += 1
        ttk.Label(beh, text="开始前停顿（秒）").grid(row=r, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(beh, textvariable=self.vars["start_delay"], width=12).grid(
            row=r, column=1, sticky="w", pady=(10, 0)
        )

        r += 1
        note = (
            f"{pretty(SCAN_HOTKEY)}：截屏并获取答案（会丢弃上一轮未输入完的剩余答案）\n"
            f"{pretty(TYPE_HOTKEY)}：把下一个答案输入到当前光标处，一次只输入一项\n"
            "多空题：填完一个空后，你自己点到下一个空，再按一次输入热键。\n"
            "中止：Esc 或把鼠标甩到左上角。软件不会按回车、不会切换窗口。"
        )
        ttk.Label(beh, text=note, justify="left", foreground="#444", wraplength=470).grid(
            row=r + 1, column=0, columnspan=2, sticky="w", pady=(16, 0)
        )
        beh.columnconfigure(1, weight=1)

        # ---- 底部
        bottom = ttk.Frame(self.root, padding=(PAD, PAD))
        bottom.pack(fill="both", expand=False)
        self.scan_btn = ttk.Button(
            bottom, text=f"取答案 {pretty(SCAN_HOTKEY)}", command=self.scan
        )
        self.scan_btn.pack(side="left")
        self.type_btn = ttk.Button(
            bottom, text=f"输入一项 {pretty(TYPE_HOTKEY)}", command=self.type_next
        )
        self.type_btn.pack(side="left", padx=6)
        ttk.Button(bottom, text="中止", command=self.abort).pack(side="left")
        ttk.Button(bottom, text="历史记录", command=self._show_history).pack(side="left", padx=6)

        self.status = ttk.Label(self.root, text="就绪", relief="sunken", anchor="w", padding=(6, 4))
        self.status.pack(fill="x", side="bottom")
        self.root.bind("<Unmap>", self._on_unmap)

    # ------------------------------------------------------------ 配置

    def _select_tab(self, index: int) -> None:
        self.notebook.select(index)

    def _sync_typo_label(self) -> None:
        self.typo_label.configure(text=f"{self.vars['typo_rate'].get():.0f}%")

    def _collect(self) -> Config | None:
        try:
            max_side = int(self.vars["max_image_side"].get())
        except ValueError:
            max_side = 1568
        try:
            lo, hi = self.vars["start_delay"].get().replace("～", "-").split("-")
            start_min, start_max = float(lo), float(hi)
        except ValueError:
            start_min, start_max = self.cfg.start_delay_min, self.cfg.start_delay_max

        return replace(
            self.cfg,
            base_url=self.vars["base_url"].get().strip() or Config.base_url,
            model=self.vars["model"].get().strip(),
            api_key=self.vars["api_key"].get().strip(),
            prompt_template=self.vars["prompt_template"].get(),
            custom_prompt=self.custom_text.get("1.0", "end").strip(),
            max_image_side=max_side,
            speed=self.vars["speed"].get(),
            typo_rate=self.vars["typo_rate"].get() / 100.0,
            move_mouse=self.vars["move_mouse"].get(),
            click_target=self.vars["click_target"].get(),
            clear_before_typing=self.vars["clear_before_typing"].get(),
            start_delay_min=start_min,
            start_delay_max=max(start_min, start_max),
        )

    def save(self, quiet: bool = False) -> bool:
        cfg = self._collect()
        if cfg is None:
            return False
        if not cfg.model:
            messagebox.showwarning(APP_TITLE, "请填写模型名称")
            return False
        self.cfg = cfg
        self.pipeline.cfg = cfg
        try:
            save_config(cfg)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"保存失败：{exc}")
            return False
        if not quiet:
            self._set_status("配置已保存")
        return True

    # ------------------------------------------------------------ 动作

    def scan(self) -> None:
        """Ctrl+Alt+Y：截屏并取回答案，丢弃上一轮尚未输入的剩项。"""
        if self.save(quiet=True) is False:
            return
        if not self.cfg.api_key:
            self._set_status("请先在 API 页填写 API Key")
            self.root.after(0, lambda: messagebox.showwarning(APP_TITLE, "请先填写 API Key"))
            return
        if self.pipeline.running:
            self._set_status("上一次还没结束，按 Esc 可中止")
            return
        self._set_status("开始截屏…")
        threading.Thread(target=self._delayed_scan, daemon=True).start()

    def type_next(self) -> None:
        """Ctrl+Alt+H：把当前答案输入到光标处，一次只输入一项。"""
        if self.pipeline.running:
            self._set_status("上一次还没结束，按 Esc 可中止")
            return
        if not self.pipeline.has_pending():
            self._set_status(f"没有待输入的答案，请先按 {pretty(SCAN_HOTKEY)} 截图取答案")
            return
        self.pipeline.start_type()

    def _delayed_scan(self) -> None:
        import time

        delay = self.cfg.start_delay_min + (self.cfg.start_delay_max - self.cfg.start_delay_min) * 0.5
        time.sleep(max(0.0, delay))
        if not self.pipeline.start_scan():
            self._set_status("已在运行中")

    def abort(self) -> None:
        self.pipeline.stop()

    def test_connection(self) -> None:
        if self.save(quiet=True) is False:
            return
        cfg = self.cfg
        if not cfg.api_key:
            messagebox.showwarning(APP_TITLE, "请先填写 API Key")
            return
        self._set_status("测试连接中…")

        def work() -> None:
            try:
                resp = requests.get(
                    cfg.base_url.rstrip("/") + "/models",
                    headers={"Authorization": f"Bearer {cfg.api_key}"},
                    timeout=20,
                )
                if resp.status_code >= 400:
                    msg = f"连接失败 {resp.status_code}：{resp.text[:200]}"
                else:
                    try:
                        ids = [m.get("id") for m in resp.json().get("data", [])]
                        msg = f"连接正常，可用模型 {len(ids)} 个" + (f"（含 {cfg.model}）" if cfg.model in ids else f"，未见 {cfg.model}")
                    except ValueError:
                        msg = "连接正常（/models 返回非 JSON，服务端可能不支持该端点）"
            except requests.RequestException as exc:
                msg = f"连接失败：{exc}"
            self._set_status(msg)

        threading.Thread(target=work, daemon=True).start()

    def _show_history(self) -> None:
        items = storage.last_answers(8)
        win = tk.Toplevel(self.root)
        win.title("最近答题记录")
        win.geometry("640x420")
        text = tk.Text(win, wrap="word")
        text.pack(fill="both", expand=True)
        if not items:
            text.insert("1.0", "还没有记录。")
        for it in items:
            text.insert("end", f"[{it['ts']}] {it['model']}  type={it['type']}  conf={it['confidence']}\n")
            text.insert("end", f"  题目：{it['question']}\n")
            text.insert("end", f"  答案：{it['answer']}\n")
            if it.get("error"):
                text.insert("end", f"  错误：{it['error']}\n")
            text.insert("end", "\n")
        text.configure(state="disabled")

    def _open_config_dir(self) -> None:
        os.startfile(str(log_dir().parent))  # noqa: S606

    # ------------------------------------------------------------ 状态

    def _status_from_thread(self, msg: str) -> None:
        self.status_q.put(msg)

    def _set_status(self, msg: str) -> None:
        self.status.configure(text=msg)

    def _poll_status(self) -> None:
        latest = None
        try:
            while True:
                latest = self.status_q.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            self._set_status(latest)
        if self.pipeline.running:
            self._busy_tick = 12  # 约 1.8s 的宽限，避免流程刚启动时按钮又亮起
        elif self._busy_tick > 0:
            self._busy_tick -= 1
        busy = self._busy_tick > 0
        self.scan_btn.configure(state="disabled" if busy else "normal")
        self.type_btn.configure(
            state="normal" if not busy and self.pipeline.has_pending() else "disabled"
        )
        self.root.after(150, self._poll_status)

    def _check_admin(self) -> None:
        try:
            import ctypes

            is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            is_admin = False
        if not is_admin:
            self._set_status("提示：非管理员运行时，热键在部分全屏/管理员程序中可能无效")

    # ------------------------------------------------------------ 生命周期

    def _on_unmap(self, event) -> None:
        if event.widget is self.root and self.root.state() == "iconic":
            self._minimize_hint()

    def _minimize_hint(self) -> None:
        if self._tray is not None:
            return
        try:
            import pystray
            from PIL import Image, ImageDraw

            img = Image.new("RGB", (64, 64), "#2d6cdf")
            d = ImageDraw.Draw(img)
            d.ellipse((14, 14, 50, 50), fill="white")
            menu = pystray.Menu(
                pystray.MenuItem("显示窗口", lambda: self.root.after(0, self._restore), default=True),
                pystray.MenuItem("截屏取答案", lambda: self.scan()),
                pystray.MenuItem("输入下一个答案", lambda: self.type_next()),
                pystray.MenuItem("退出", lambda: self.root.after(0, self._on_close)),
            )
            self._tray = pystray.Icon("autodati", img, APP_TITLE, menu)
            threading.Thread(target=self._tray.run, daemon=True).start()
        except Exception:
            log.debug("托盘不可用，仅最小化到任务栏", exc_info=True)

    def _restore(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
            self._tray = None

    def _on_close(self) -> None:
        self.pipeline.stop()
        self.hotkeys.unregister()
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
        self.root.destroy()

    def run_forever(self) -> None:
        self.root.mainloop()
