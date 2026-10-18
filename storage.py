"""历史记录：每次答题追加一行 JSONL，便于回溯与调参。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ai_client import Answer
from logging_setup import log_dir


def history_path() -> Path:
    return log_dir() / "history.jsonl"


def record(answer: Answer, *, model: str, typed: bool, elapsed: float, error: str | None = None) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": model,
        "type": answer.type,
        "question": answer.question[:300],
        "answer": answer.answer[:1000],
        "choice": {"label": answer.choice_label, "text": answer.choice_text},
        "target": answer.target,
        "confidence": answer.confidence,
        "typed": typed,
        "elapsed_sec": round(elapsed, 2),
        "error": error,
    }
    with history_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def last_answers(limit: int = 5) -> list[dict]:
    path = history_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
