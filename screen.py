"""截屏：抓主屏，缩放到模型友好尺寸，编码为 data URL。"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

import mss
from PIL import Image

from logging_setup import get_logger

log = get_logger()


@dataclass
class Screenshot:
    image: Image.Image
    """发送给模型的（可能已缩放）图像。"""
    scale: float
    """模型图像坐标 × scale = 屏幕物理坐标。"""
    origin_x: int
    """虚拟桌面左上角在物理坐标中的偏移，用于多显示器。"""
    origin_y: int
    width: int
    height: int

    def to_data_url(self, quality: int = 82) -> str:
        buf = io.BytesIO()
        self.image.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"


def _grab_primary() -> tuple[Image.Image, int, int]:
    factory = getattr(mss, "MSS", mss.mss)
    with factory() as sct:
        mon = sct.monitors[1]
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return img, mon["left"], mon["top"]


def capture(max_side: int = 1568) -> Screenshot:
    """截取主显示器。DPI 感知由 main.py 负责设置。"""
    img, ox, oy = _grab_primary()
    w, h = img.size
    scale = 1.0
    longest = max(w, h)
    if max_side and longest > max_side:
        scale = longest / max_side
        img = img.resize((round(w / scale), round(h / scale)), Image.LANCZOS)
    shot = Screenshot(img, scale, ox, oy, w, h)
    log.info("截图完成 原始=%dx%d 发送=%dx%d scale=%.3f", w, h, img.width, img.height, scale)
    return shot
