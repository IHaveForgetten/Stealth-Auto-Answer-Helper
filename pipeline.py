"""答题流程，分成两个可独立触发阶段：

- scan：Ctrl+Alt+Y 截屏 → AI → 得到一份答案并存入当前轮次
- type：Ctrl+Alt+H 取当前轮次里下一个尚未输入的答案，逐字输入到光标处

多空题每按一次输入热键只填一个空；用户自己切到下一个空后再按一次。
再次按截屏热键会丢弃当前轮次剩余答案，以新截图为准。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import pyautogui

import ai_client
import screen
import storage
from config import Config
from humanizer import Humanizer
from input_engine import Interrupted, move_mouse_smooth, type_text, PyAutoGUIKeyboard
from logging_setup import get_logger
from prompts import prompt_for

log = get_logger()

StatusCb = Callable[[str], None]


@dataclass
class RunResult:
    answer: ai_client.Answer | None = None
    typed: bool = False
    elapsed: float = 0.0
    error: str | None = None
    screenshot: screen.Screenshot | None = None


@dataclass
class Session:
    """一次截屏得到的答案，以及还没输入的剩余项。"""

    answer: ai_client.Answer
    pending: list[str] = field(default_factory=list)
    """尚未输入的答案，按顺序消费。"""
    round_no: int = 0

    @property
    def remaining(self) -> int:
        return len(self.pending)

    def describe(self) -> str:
        total = len(self.pending) + len(self.answer.extras.get("filled", []))
        if total <= 1:
            return self.pending[0][:40] if self.pending else "已输入完"
        filled = len(self.answer.extras.get("filled", []))
        nxt = self.pending[0][:20] if self.pending else ""
        return f"还有 {self.remaining} 项待输入，下一个：{nxt}（已完成 {filled}/{total}）"


class Pipeline:
    def __init__(self, cfg: Config, on_status: StatusCb) -> None:
        self.cfg = cfg
        self.on_status = on_status
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._running = False
        self._session: Session | None = None
        self._round_seed = 0

    # ------------------------------------------------------------ 控制

    def start_scan(self) -> bool:
        """截屏取答案。会丢弃上一轮尚未输入的剩余答案。"""
        with self._lock:
            if self._running:
                return False
            if self._session is not None and self._session.remaining:
                log.info("新一轮截屏，丢弃上一轮剩余 %d 项答案", self._session.remaining)
            self._session = None
            self._running = True
            self._stop.clear()
        threading.Thread(target=self._run_scan, daemon=True, name="pipeline-scan").start()
        return True

    def start_type(self) -> bool:
        """输入下一个答案；没有待输入内容时不启动。"""
        with self._lock:
            if self._running:
                return False
            if self._session is None or not self._session.pending:
                return False
            self._running = True
            self._stop.clear()
        threading.Thread(target=self._run_type, daemon=True, name="pipeline-type").start()
        return True

    def stop(self) -> None:
        if self._running:
            log.info("收到中止请求")
            self._stop.set()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def session(self) -> Session | None:
        return self._session

    def has_pending(self) -> bool:
        return bool(self._session and self._session.pending)

    # ------------------------------------------------------------ 截屏取答案

    def _run_scan(self) -> None:
        started = time.perf_counter()
        result = RunResult()
        try:
            self._do_scan(result)
        except Interrupted:
            result.error = "已中止"
            self.on_status("已中止")
        except ai_client.AIError as exc:
            result.error = str(exc)
            log.error("AI 调用失败：%s", exc)
            self.on_status(f"AI 失败：{exc}")
        except Exception as exc:  # 线程里的兜底，避免静默失败
            result.error = f"{type(exc).__name__}: {exc}"
            log.exception("截屏取答案流程异常")
            self.on_status(f"出错：{exc}")
        finally:
            result.elapsed = time.perf_counter() - started
            if result.answer is not None or result.error:
                self._record(result)
            with self._lock:
                self._running = False

    def _do_scan(self, result: RunResult) -> None:
        cfg = self.cfg
        if not cfg.api_key:
            raise ai_client.AIError("尚未配置 API Key")

        self.on_status("截图中…")
        shot = screen.capture(cfg.max_image_side)
        result.screenshot = shot
        self._check_stop()

        self.on_status("AI 解析中…")
        data_url = shot.to_data_url(cfg.jpeg_quality)
        answer = ai_client.ask(
            cfg.base_url,
            cfg.model,
            cfg.api_key,
            data_url,
            user_prompt=prompt_for(cfg.prompt_template, cfg.custom_prompt),
            timeout=cfg.request_timeout,
        )
        result.answer = answer
        self._check_stop()

        pending = answer.answers_in_order()
        if answer.type == "choice":
            pending = []
        if not pending:
            self.on_status("未识别到题目或答案为空")
            return

        self._round_seed += 1
        self._session = Session(answer=answer, pending=list(pending), round_no=self._round_seed)
        answer.extras["shot"] = shot

        if answer.type == "choice" and answer.target is not None:
            self._hover_choice(answer, shot)

        if len(pending) == 1:
            self.on_status(f"已得到答案：{pending[0][:60]}（按 {_type_key()} 输入）")
        else:
            self.on_status(
                f"已得到 {len(pending)} 个答案；每按一次 {_type_key()} 输入一个，"
                f"下一个：{pending[0][:30]}"
            )
        log.info("本轮答案 %s，共 %d 项：%s", answer.summary(), len(pending), pending)

    # ------------------------------------------------------------ 输入一个

    def _hover_choice(self, answer: ai_client.Answer, shot: screen.Screenshot) -> None:
        """选择题：只把鼠标移到选项上方，点击仍由用户完成。"""
        if not self.cfg.move_mouse or answer.target is None:
            return
        tx, ty = answer.target
        if not (0 <= tx < shot.image.width and 0 <= ty < shot.image.height):
            return
        sx = shot.origin_x + int(round(tx * shot.scale))
        sy = shot.origin_y + int(round(ty * shot.scale))
        log.info("鼠标移动到选项 (%d, %d)", sx, sy)
        self.on_status("移动到选项…")
        move_mouse_smooth(sx, sy, self._humanizer(), self._stop)

    def _run_type(self) -> None:
        started = time.perf_counter()
        result = RunResult()
        try:
            self._do_type(result)
        except Interrupted:
            result.error = "已中止"
            self.on_status("已中止")
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            log.exception("输入流程异常")
            self.on_status(f"出错：{exc}")
        finally:
            result.elapsed = time.perf_counter() - started
            if result.typed or result.error:
                self._record(result)
            with self._lock:
                self._running = False

    def _do_type(self, result: RunResult) -> None:
        cfg = self.cfg
        session = self._session
        if session is None or not session.pending:
            self.on_status("还没有答案，请先按 Ctrl+Alt+Y 截图取答案")
            return

        answer = session.answer
        result.answer = answer
        hz = self._humanizer()

        text = session.pending[0]
        index = len(answer.extras.get("filled", [])) + 1
        total = index + session.remaining - 1

        self.on_status(f"准备输入第 {index}/{total} 项（{len(text)} 字符）…")
        time.sleep(hz.think_delay())
        self._check_stop()

        # 不再靠 AI 坐标点击：用户已把光标放在目标输入框里，这里直接输入
        if cfg.clear_before_typing and cfg.click_target:
            _select_all_and_delete()
        try:
            type_text(text, hz, self._stop, backend=PyAutoGUIKeyboard())
        finally:
            _release_modifiers()

        session.pending.pop(0)
        answer.mark_filled(index)
        result.typed = True
        log.info("已输入第 %d/%d 项：%s", index, total, text)

        if session.pending:
            self.on_status(
                f"已输入第 {index}/{total} 项：{text[:30]}；"
                f"请切到下一个空后按 {_type_key()}（剩余 {session.remaining} 项）"
            )
        else:
            self.on_status(f"全部 {total} 项已输入完成")

    # ------------------------------------------------------------ 公用

    def _humanizer(self) -> Humanizer:
        preset = self.cfg.speed_preset()
        return Humanizer(
            median_delay=preset["median"],
            sigma=preset["sigma"],
            typo_rate=self.cfg.typo_rate,
        )

    def _record(self, result: RunResult) -> None:
        try:
            storage.record(
                result.answer or ai_client.Answer(),
                model=self.cfg.model,
                typed=result.typed,
                elapsed=result.elapsed,
                error=result.error,
            )
        except OSError:
            log.exception("写入历史失败")

    def _check_stop(self) -> None:
        if self._stop.is_set():
            raise Interrupted()
        # 鼠标撞到屏幕左上角也能中止（pyautogui.FAILSAFE 已被关闭，这里手动实现）
        try:
            pos = pyautogui.position()
            # 真实返回 Point（有 .x/.y），测试桩可能返回元组
            x = pos.x if hasattr(pos, "x") else pos[0]
            y = pos.y if hasattr(pos, "y") else pos[1]
            if x <= 0 and y <= 0:
                log.warning("检测到鼠标位于左上角，判定为中止")
                self._stop.set()
                raise Interrupted()
        except OSError:
            pass


def _type_key() -> str:
    from hotkey import TYPE_HOTKEY, pretty

    return pretty(TYPE_HOTKEY)


def _ensure_current_blank(answer: ai_client.Answer) -> None:
    """保证多空题总能定位到一个空：缺 current_blank 时取第 1 空，缺 answer 时取该空答案。"""
    if answer.blank_count <= 1 and not answer.is_multi_blank:
        return
    if answer.current_blank is None:
        log.warning("多空题未给出 current_blank，按第 1 空处理")
        answer.current_blank = 1
    if not answer.answer and answer.blanks:
        idx = answer.current_blank - 1
        if 0 <= idx < len(answer.blanks):
            answer.answer = answer.blanks[idx]


def _select_all_and_delete() -> None:
    """输入前清空当前输入框里已有的内容，避免新旧答案连在一起。"""
    try:
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("delete")
    except Exception:  # noqa: BLE001 - 清空失败不应中断答题
        log.debug("清空输入框失败", exc_info=True)


def _release_modifiers() -> None:
    for key in ("shift", "ctrl", "alt", "win"):
        try:
            pyautogui.keyUp(key)
        except Exception:  # noqa: BLE001 - 尽力而为，不掩盖原始异常
            pass
