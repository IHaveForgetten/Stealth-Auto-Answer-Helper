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
2. 给出正确答案。
3. 如果题目有选项（A/B/C/D 或按钮式选项），给出正确选项的字母和内容，并估算该选项在截图中的像素坐标。

输出要求：只输出一个 JSON 对象，不要 markdown 代码块，不要任何解释文字。格式：
{"type": "text" | "choice" | "unknown",
 "question": "识别到的题目原文（可截断）",
 "answer": "要输入到答题框里的答案。只写答案本身，不要写“答案是”这类前缀",
 "choice": {"label": "B", "text": "选项内容"} | null,
 "target": {"x": 123, "y": 456} | null,
 "confidence": 0.0 ~ 1.0}

坐标说明：x、y 是相对这张截图左上角的像素坐标，原点 (0,0) 在左上角，x 向右、y 向下。指向选项文本行的中心。
不确定答案时 type 用 unknown、answer 留空、confidence 给低值。"""

USER_PROMPT_DEFAULT = "请解答截图中的题目。"


@dataclass
class Answer:
    type: str = "unknown"
    question: str = ""
    answer: str = ""
    choice_label: str | None = None
    choice_text: str | None = None
    target: tuple[int, int] | None = None
    confidence: float = 0.0
    raw: str = ""
    extras: dict = field(default_factory=dict)

    def summary(self) -> str:
        parts = []
        if self.question:
            q = self.question.replace("\n", " ")
            parts.append(q[:60] + ("…" if len(q) > 60 else ""))
        if self.choice_label:
            parts.append(f"选项 {self.choice_label}")
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
