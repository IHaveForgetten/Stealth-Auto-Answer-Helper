"""OpenAI 兼容的多模态 chat/completions 调用，以及答案解析。

后端差异（Qwen/GLM/DeepSeek/本地 vLLM 等）主要出现在：
- 是否接受 response_format
- 是否把思考过程放在 reasoning_content
所以这里做降级重试，并且 JSON 解析尽量宽容。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import requests

from logging_setup import get_logger

log = get_logger()

SYSTEM_PROMPT = """你是屏幕答题助手。用户会给你一张电脑屏幕截图，里面可能有题目、选项、或正在填写的表单。

任务：
1. 识别截图中的题目。如果有多道题，只回答最显眼/最当前的一道；如果确实无法判断是哪道，回答 first_visible。
2. 给出正确答案。答案要能直接填进答题框，不要带"答案是""第3空"这类前缀。
3. 如果题目有选项（A/B/C/D 或按钮式选项），给出正确选项的字母和内容，并估算该选项在截图中的像素坐标。
4. 如果题目有多个空（例如 ①②③④、____、____、____、____，或页面上有"第1空/第2空…"多个输入框），必须把每个空的答案分开放在 blanks 数组里，按页面上从上到下、从左到右的顺序排列。绝对不要把多个空的答案用逗号、顿号或空格合并成一个字符串。
   - 例：四个空分别填 3、4、90、120，则 blanks 为 ["3","4","90","120"]，而不是 "3,4,90,120"。
   - blank_count 必须等于 blanks 的长度，也等于页面上输入框的个数。

输出要求：只输出一个 JSON 对象，不要 markdown 代码块，不要任何解释文字。格式：
{"type": "text" | "choice" | "fill" | "unknown",
 "question": "识别到的题目原文（可截断）",
 "blank_count": 1,
 "answer": "单空题/简答题的答案；多空题时填 blanks 的第一项",
 "blanks": ["第1空的答案", "第2空的答案"] | null,
 "choice": {"label": "B", "text": "选项内容"} | null,
 "target": {"x": 123, "y": 456} | null,
 "confidence": 0.0 ~ 1.0}

说明：
- 填空/多空题 type 用 fill；纯文字简答 type 用 text；选择题 type 用 choice。
- 单个空时 blanks 可省略或为 null，answer 即该空内容。
- target 只对选择题有意义：指向要选的那个选项文本行的中心（截图坐标，原点在左上角）。填空题不需要给 target。
- 不确定答案时 type 用 unknown、answer 留空、confidence 给低值。"""

USER_PROMPT_DEFAULT = "请解答截图中的题目。"


@dataclass
class Answer:
    type: str = "unknown"
    question: str = ""
    answer: str = ""
    choice_label: str | None = None
    choice_text: str | None = None
    target: tuple[int, int] | None = None
    blanks: list[str] = field(default_factory=list)
    """多空题：按页面顺序的全部空答案，仅用于展示与核对。"""
    current_blank: int | None = None
    """当前已聚焦的输入框是第几个空（从 1 开始）；单空题为 1。"""
    blank_count: int = 0
    """页面上空的总数。"""
    confidence: float = 0.0
    raw: str = ""
    extras: dict = field(default_factory=dict)

    @property
    def is_multi_blank(self) -> bool:
        return len(self.blanks) > 1

    def answers_in_order(self) -> list[str]:
        """本次要依次输入的内容：多空题按空位顺序返回，其它情况只有一项。"""
        if self.is_multi_blank:
            return [b for b in self.blanks if b.strip()]
        return [self.answer] if self.answer.strip() else []

    def next_answer(self) -> str:
        """取当前该输入的那一项；多空题按顺序逐个消费。"""
        if self.is_multi_blank:
            if self.current_blank is not None and 1 <= self.current_blank <= len(self.blanks):
                return self.blanks[self.current_blank - 1]
            return self.blanks[0]
        return self.answer

    def advance_blank(self) -> None:
        """把 current_blank 推进到下一个尚未填写的空。"""
        if not self.is_multi_blank:
            return
        cur = self.current_blank or 1
        self.current_blank = min(cur + 1, len(self.blanks))

    def mark_filled(self, index: int) -> None:
        """记录第 index 个空已经填过，避免重复输入。"""
        self.extras.setdefault("filled", [])
        if index not in self.extras["filled"]:
            self.extras["filled"].append(index)

    def summary(self) -> str:
        parts = []
        if self.question:
            q = self.question.replace("\n", " ")
            parts.append(q[:50] + ("…" if len(q) > 50 else ""))
        if self.choice_label:
            parts.append(f"选项 {self.choice_label}")
        if self.blank_count > 1 or self.is_multi_blank:
            idx = f"第{self.current_blank}空" if self.current_blank else "当前空未知"
            parts.append(f"{idx}：{(self.answer or '')[:40]}")
        else:
            detail = (self.answer or "").replace("\n", " ")
            if detail:
                parts.append(detail[:80] + ("…" if len(detail) > 80 else ""))
        parts.append(f"置信度 {self.confidence:.2f}")
        return " | ".join(parts) if parts else "（无内容）"


class AIError(RuntimeError):
    pass


def _post(url: str, payload: dict, api_key: str, timeout: int) -> requests.Response:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "Autodati/1.0",
    }
    return requests.post(url, headers=headers, json=payload, timeout=timeout)


def ask(
    base_url: str,
    model: str,
    api_key: str,
    image_data_url: str,
    user_prompt: str = USER_PROMPT_DEFAULT,
    timeout: int = 90,
) -> Answer:
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": image_data_url, "detail": "high"}},
            ],
        },
    ]
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
    }

    log.info("请求模型 %s @ %s", model, url)
    try:
        resp = _post(url, payload, api_key, timeout)
    except requests.RequestException as exc:
        raise AIError(f"网络请求失败：{exc}") from exc

    # 部分兼容实现不认识 response_format，去掉后重试一次
    if resp.status_code in (400, 422):
        log.warning("首个请求被拒（%s），去掉 response_format 重试", resp.status_code)
        payload.pop("response_format", None)
        try:
            resp = _post(url, payload, api_key, timeout)
        except requests.RequestException as exc:
            raise AIError(f"网络请求失败：{exc}") from exc

    if resp.status_code >= 400:
        body = resp.text[:400]
        raise AIError(f"API 返回 {resp.status_code}：{body}")

    try:
        data = resp.json()
        message = data["choices"][0]["message"]
    except (ValueError, KeyError, IndexError) as exc:
        raise AIError(f"响应结构异常：{resp.text[:300]}") from exc

    content = message.get("content") or ""
    if isinstance(content, list):  # 少数实现返回分段内容
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))

    answer = parse_answer(content)
    if not content.strip():
        reasoning = message.get("reasoning_content") or ""
        log.warning("content 为空，reasoning_content 长度=%d", len(reasoning))
    log.info("模型回答：%s", answer.summary())
    return answer


# ------------------------------------------------------------------ 解析

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    candidates = []
    m = _JSON_BLOCK.search(text)
    if m:
        candidates.append(m.group(1))
    candidates.append(text)

    # 从第一个 { 开始做括号配平，取最外层对象
    start = text.find("{")
    if start >= 0:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _as_float(value, default: float = 0.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, f))


def _as_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _clean_answer(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    text = text.strip()
    # 去掉常见的复述前缀，让输入框里只出现答案本身
    text = re.sub(r"^(答案\s*[:：]|正确答案\s*[:：]|答案是\s*[:：]?|答\s*[:：])\s*", "", text)
    return text.strip()


def parse_answer(content: str) -> Answer:
    obj = _extract_json(content)
    if obj is None:
        # 完全没有 JSON：整段文本当答案
        text = _clean_answer(content)
        return Answer(
            type="text" if text else "unknown",
            answer=text,
            confidence=0.3 if text else 0.0,
            raw=content,
        )

    ans = Answer(raw=content)
    ans.type = str(obj.get("type") or "unknown").lower()
    ans.question = str(obj.get("question") or "")
    ans.answer = _clean_answer(obj.get("answer") or "")
    ans.confidence = _as_float(obj.get("confidence"))

    blanks = obj.get("blanks")
    if isinstance(blanks, list):
        ans.blanks = [_clean_answer(b) for b in blanks if b not in (None, "")]
    ans.blank_count = _as_int(obj.get("blank_count")) or len(ans.blanks)
    ans.current_blank = _as_int(obj.get("current_blank"))

    # 模型有时只给 blanks 不给 current_blank：多空题就退回到第一个空
    if ans.current_blank is None and ans.blank_count > 1:
        log.warning("多空题但未给出 current_blank，按第 1 空处理")
        ans.current_blank = 1
    if ans.current_blank is not None and not (1 <= ans.current_blank <= max(1, ans.blank_count)):
        log.warning("current_blank=%s 越界（共 %s 空），已忽略", ans.current_blank, ans.blank_count)
        ans.current_blank = 1

    # answer 缺省时，用对应空位的答案补齐
    if not ans.answer and ans.blanks:
        idx = (ans.current_blank or 1) - 1
        if 0 <= idx < len(ans.blanks):
            ans.answer = ans.blanks[idx]

    choice = obj.get("choice")
    if isinstance(choice, dict):
        label = choice.get("label")
        text = choice.get("text")
        ans.choice_label = str(label).strip() if label not in (None, "") else None
        ans.choice_text = str(text).strip() if text not in (None, "") else None
    # 有些模型会平铺字段
    if ans.choice_label is None and obj.get("choice_label"):
        ans.choice_label = str(obj["choice_label"]).strip()

    target = obj.get("target")
    if isinstance(target, dict):
        try:
            tx, ty = float(target["x"]), float(target["y"])
            ans.target = (int(round(tx)), int(round(ty)))
        except (KeyError, TypeError, ValueError):
            ans.target = None

    if ans.type == "unknown" and not ans.answer:
        return ans

    if not ans.answer:
        if ans.choice_label and ans.choice_text:
            ans.answer = ans.choice_text
        elif ans.choice_label:
            ans.answer = ans.choice_label

    return ans
