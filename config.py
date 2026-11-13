"""配置读写。API Key 以 Windows DPAPI（当前用户作用域）加密后落盘。"""

from __future__ import annotations

import base64
import copy
import ctypes
import ctypes.wintypes as wt
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"

SPEED_PRESETS = {
    # 每字符延迟的中位数（秒）与对数正态 sigma，单位：秒
    "fast": {"median": 0.045, "sigma": 0.45, "label": "快"},
    "normal": {"median": 0.085, "sigma": 0.55, "label": "标准"},
    "slow": {"median": 0.14, "sigma": 0.6, "label": "慢"},
}


@dataclass
class Config:
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    api_key: str = ""
    prompt_template: str = "通用答题"
    custom_prompt: str = ""
    max_image_side: int = 1568
    jpeg_quality: int = 82
    speed: str = "normal"
    typo_rate: float = 0.06
    move_mouse: bool = True
    click_target: bool = True
    """填空/文本题：把鼠标移到当前输入框并点击，确保光标落在正确的空里。"""
    clear_before_typing: bool = True
    """点击聚焦后先 Ctrl+A + Delete 清空该空已有内容，再输入新答案。"""
    start_delay_min: float = 0.5
    start_delay_max: float = 1.4
    request_timeout: int = 90
    extra: dict = field(default_factory=dict)

    @property
    def chat_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def speed_preset(self) -> dict:
        return SPEED_PRESETS.get(self.speed, SPEED_PRESETS["normal"])


# ---------------------------------------------------------------- DPAPI

class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wt.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    in_blob, _keep = _blob(data)
    out_blob = _DataBlob()
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    try:
        return "dpapi:" + base64.b64encode(_dpapi(plain.encode("utf-8"), True)).decode()
    except OSError:
        # 非 Windows 或 DPAPI 不可用时退化为明文标记，README 中有说明
        return "plain:" + base64.b64encode(plain.encode("utf-8")).decode()


def decrypt_secret(stored: str) -> str:
    if not stored:
        return ""
    if stored.startswith("dpapi:"):
        try:
            return _dpapi(base64.b64decode(stored[6:]), False).decode("utf-8")
        except OSError:
            return ""
    if stored.startswith("plain:"):
        return base64.b64decode(stored[6:]).decode("utf-8")
    return stored


# ---------------------------------------------------------------- load / save

_FIELDS = {f for f in Config.__dataclass_fields__}


def load_config() -> Config:
    cfg = Config()
    if not CONFIG_PATH.exists():
        return cfg
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return cfg

    if "api_key_enc" in raw:
        cfg.api_key = decrypt_secret(raw.pop("api_key_enc"))
    for key, value in raw.items():
        if key in _FIELDS and key != "api_key":
            setattr(cfg, key, value)
    return cfg


def save_config(cfg: Config) -> None:
    data = asdict(copy.deepcopy(cfg))
    data.pop("extra", None)
    api_key = data.pop("api_key", "")
    data["api_key_enc"] = encrypt_secret(api_key)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)
