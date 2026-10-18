"""端到端自测：本地假 OpenAI 服务 + 假键鼠，验证一次完整答题流程。

用法：python tests/e2e_check.py
不会真的驱动键盘鼠标、不会访问外网。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline  # noqa: E402
import screen  # noqa: E402
from config import Config  # noqa: E402

RESPONSE = {
    "type": "text",
    "question": "1 + 1 = ?",
    "answer": "2",
    "choice": None,
    "target": None,
    "confidence": 0.97,
}


class _Handler(BaseHTTPRequestHandler):
    last_payload: dict = {}

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        type(self).last_payload = body
        if self.headers.get("Authorization") != "Bearer test-key":
            self.send_response(401)
            self.end_headers()
            return
        data = {
            "choices": [
                {"message": {"role": "assistant", "content": json.dumps(RESPONSE, ensure_ascii=False)}}
            ]
        }
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class FakeKeyboard:
    def __init__(self):
        self.events: list[str] = []

    def press(self, key):
        self.events.append(f"down:{key}")

    def release(self, key):
        self.events.append(f"up:{key}")

    def write(self, text):
        self.events += list(text)


class FakeMouse:
    def __init__(self):
        self.x, self.y = 960, 540
        self.track = []

    def position(self):
        return self.x, self.y

    def moveTo(self, x, y):
        self.x, self.y = x, y
        self.track.append((x, y))


def main() -> int:
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    cfg = Config(
        base_url=f"http://127.0.0.1:{port}/v1",
        model="fake-vl",
        api_key="test-key",
        speed="fast",
        start_delay_min=0,
        start_delay_max=0,
        move_mouse=False,
    )

    # 打桩：真实截图换成纯色图，键鼠换成假后端
    pipeline.screen.capture = lambda max_side: screen.Screenshot(
        image=__import__("PIL.Image", fromlist=["Image"]).new("RGB", (480, 270), "white"),
        scale=4.0,
        origin_x=0,
        origin_y=0,
        width=1920,
        height=1080,
    )
    kb, mouse = FakeKeyboard(), FakeMouse()
    pipeline.type_text = lambda text, hz, stop, on_action=None: _fake_type(kb, text)
    pipeline.move_mouse_smooth = lambda x, y, hz, stop, backend=None: mouse.moveTo(x, y)

    statuses: list[str] = []
    pipe = pipeline.Pipeline(cfg, statuses.append)
    assert pipe.start_async()
    deadline = time.time() + 15
    while pipe.running and time.time() < deadline:
        time.sleep(0.05)
    srv.shutdown()

    typed = "".join(e[5:] for e in kb.events if e.startswith("down:") and len(e) == 6)

    print("状态回调：", statuses)
    print("输入内容：", repr(typed))
    print("请求 model：", _Handler.last_payload.get("model"))
    msg = _Handler.last_payload["messages"][-1]["content"]
    print("是否携带图片：", any(p.get("type") == "image_url" for p in msg))

    ok = (
        typed == "2"
        and any("完成" in s for s in statuses)
        and _Handler.last_payload.get("model") == "fake-vl"
        and any(p.get("type") == "image_url" for p in msg)
    )
    print("结果：", "通过" if ok else "失败")
    return 0 if ok else 1


def _fake_type(kb: FakeKeyboard, text: str) -> None:
    for ch in text:
        kb.press(ch)
        kb.release(ch)
        time.sleep(0.005)


if __name__ == "__main__":
    sys.exit(main())
