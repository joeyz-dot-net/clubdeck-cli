"""Project configuration.

Non-sensitive options live in `clubdeck.config.json` (auto-created on first run).
Sensitive values (tokens, API keys) stay in environment variables only and are
never read from the config file.

Precedence for non-sensitive options: environment variable (legacy override) >
config file > built-in default.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable


# uv/portable Windows Python resolves the real interpreter outside the venv,
# so native extensions do not automatically see the MSVC runtime DLLs placed
# in <venv>\Scripts by the msvc-runtime wheel. Keep the directory handle alive
# for this process before faster-whisper/ctranslate2 is imported.
_DLL_DIRECTORY_HANDLES: list[Any] = []
if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
    _venv_scripts = Path(sys.prefix) / "Scripts"
    if _venv_scripts.is_dir():
        try:
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(_venv_scripts)))
        except OSError:
            pass


PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = Path(os.environ.get("CLUBDECK_CONFIG", str(PROJECT_DIR / "clubdeck.config.json")))
# 敏感值单独存放的文件(不进 clubdeck.config.json;可用 CLUBDECK_SECRETS 覆盖路径)
SECRETS_FILE = Path(os.environ.get("CLUBDECK_SECRETS", str(PROJECT_DIR / "clubdeck.secrets.json")))
MODELS_DIR = PROJECT_DIR / "models"
HF_HUB_DIR = MODELS_DIR / "huggingface" / "hub"
# 运行时状态目录（默认 data/；可用 CLUBDECK_DATA_DIR 覆盖，测试/多环境隔离用）
DATA_DIR = Path(os.environ.get("CLUBDECK_DATA_DIR", str(PROJECT_DIR / "data")))
LAST_ROOM_FILE = DATA_DIR / "last_room.json"

# 默认建房参数：普通好友房（仅好友可见、关回放、无需举手、自动上台）；
# 俱乐部房由平台强制“举手才能上台”（permission=1），无法关闭。
DEFAULT_ROOM_SOCIAL_CLUB_ID = 2235382207989919642
DEFAULT_ROOM_SOCIAL_CLUB_NAME = "飞猪音乐"


DEFAULTS: dict[str, Any] = {
    # Clubhouse mobile API identity.  Keep version/build/User-Agent as one
    # verified tuple; new-room stage actions reject stale or mixed identities.
    "clubhouse": {
        "device_id": "",
        # Empty uses the verified built-in tuple. Override all three together.
        "app_version": "",
        "app_build": "",
        "user_agent": "",
        "languages": "",
        "locale": "",
        "accept_language": "",
        "device_mfr": "",
        "device_model": "",
        "keyboards": "",
        "storefront_country_code": "",
    },
    "tui": {
        "auto_transcribe": True,
        "transcribe_seconds": "auto",
        "whisper_model": "auto",
        "whisper_vad": False,
        "transcribe_max_lines": 20,
        "startup_self_check": True,
        "agora_playback_device": "default",
        "bridge_runtime": "",
        "agora_sdk_dir": "",
    },
}


# 设置菜单可编辑参数(非敏感);label/type/choices 用于 TUI 设置界面。
# type: bool(回车切换) / int(数字输入) / str(文本输入) / choice(选项列表)
PARAM_DEFS: list[dict[str, Any]] = [
    {"section": "tui", "key": "auto_transcribe", "label": "自动转写", "type": "bool"},
    {"section": "tui", "key": "whisper_model", "label": "转写模型", "type": "choice",
     "choices": ["auto", "tiny", "base", "small", "medium"]},
    {"section": "tui", "key": "transcribe_seconds", "label": "每段转写秒数(auto 或秒数)", "type": "str"},
    {"section": "tui", "key": "whisper_vad", "label": "转写 VAD", "type": "bool"},
    {"section": "tui", "key": "transcribe_max_lines", "label": "屏幕保留行数", "type": "int", "min": 1},
    {"section": "tui", "key": "startup_self_check", "label": "启动时运行检查", "type": "bool"},
    {"section": "tui", "key": "agora_playback_device", "label": "房间音频输出设备", "type": "choice",
     "choices_fn": "list_audio_output_devices"},
    {"section": "tui", "key": "bridge_runtime", "label": "Electron 12 路径(留空使用仓库版本)", "type": "str"},
    {"section": "tui", "key": "agora_sdk_dir", "label": "Agora SDK 目录(留空使用仓库版本)", "type": "str"},
]

SENSITIVE_PARAMS: list[dict[str, Any]] = [
    {"key": "token", "label": "Clubdeck Token", "help": "登录令牌(CH_TOKEN)"},
    {"key": "user_id", "label": "Clubdeck 用户ID", "help": "当前账号 user_id(CH_USER_ID)"},
]

PARAM_CATEGORY: dict[tuple[str, str], str] = {
    ("tui", "auto_transcribe"): "转写",
    ("tui", "whisper_model"): "转写",
    ("tui", "transcribe_seconds"): "转写",
    ("tui", "whisper_vad"): "转写",
    ("tui", "transcribe_max_lines"): "转写",
    ("tui", "startup_self_check"): "启动",
    ("tui", "agora_playback_device"): "音频",
    ("tui", "bridge_runtime"): "音频",
    ("tui", "agora_sdk_dir"): "音频",
}



def param_category(meta: dict[str, Any]) -> str:
    if meta.get("section") == "secrets":
        return "高级与敏感"
    return PARAM_CATEGORY.get((meta.get("section", ""), meta.get("key", "")), "其他")


def param_defs_for(section: str) -> list[dict[str, Any]]:
    """某分区的可编辑参数列表;配置 DEFAULTS 里新增的参数会自动出现。"""
    if section == "secrets":
        return [dict(x, section="secrets") for x in SENSITIVE_PARAMS]
    known = {d["key"]: d for d in PARAM_DEFS if d["section"] == section}
    out: list[dict[str, Any]] = []
    for key, default in DEFAULTS.get(section, {}).items():
        meta = known.get(key)
        if meta:
            out.append(meta)
        else:
            kind = "bool" if isinstance(default, bool) else ("int" if isinstance(default, int) else "str")
            out.append({"section": section, "key": key, "label": key, "type": kind})
    return out


def _list_audio_devices(kind: str) -> list[str]:
    """枚举系统音频设备名;找不到 soundcard 或枚举失败时至少返回 'default'。"""
    try:
        import soundcard as sc
    except Exception:
        return ["default"]
    try:
        if kind == "input":
            devices = sc.all_microphones(include_loopback=True)
        elif kind == "output":
            try:
                devices = sc.all_speakers(include_loopback=False)
            except TypeError:
                devices = sc.all_speakers()
        else:
            return ["default"]
        names: list[str] = []
        seen: set[str] = set()
        for dev in devices:
            name = str(getattr(dev, "name", "") or "").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        return ["default"] + names
    except Exception:
        return ["default"]


def list_audio_input_devices() -> list[str]:
    """可用录音输入设备(含虚拟声卡等 loopback 采集源);首项为系统默认。"""
    return _list_audio_devices("input")


def list_audio_output_devices() -> list[str]:
    """可用播放输出设备;首项为系统默认。"""
    return _list_audio_devices("output")


def resolve_choices(d: dict[str, Any]) -> list[Any]:
    """参数的可选值:优先静态 choices,否则按 choices_fn 动态枚举设备等。"""
    static = d.get("choices")
    if static is not None:
        return list(static)
    fn_name = d.get("choices_fn")
    if fn_name:
        fn = globals().get(fn_name)
        if callable(fn):
            try:
                values = fn()
            except Exception:
                values = []
            return list(values or [])
    return []


# Keys that must never be supplied by the config file; environment only.
SENSITIVE_KEYS = frozenset({"token", "user_id", "api_key", "password", "secret"})

_cache: tuple[Path, dict[str, Any]] | None = None  # (来源文件, 合并后的配置)


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _strip_sensitive(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(key, str):
            lowered = key.strip().lower()
            if any(marker in lowered for marker in SENSITIVE_KEYS):
                continue
        if isinstance(value, dict):
            out[key] = _strip_sensitive(value)
        else:
            out[key] = value
    return out


def load(path: str | os.PathLike | None = None, *, refresh: bool = False) -> dict[str, Any]:
    """Merged config: built-in defaults + config file (sensitive keys ignored)."""
    global _cache
    cfg_path = Path(path) if path else Path(CONFIG_FILE)
    if not refresh and _cache is not None and _cache[0] == cfg_path:
        return _cache[1]
    data: dict[str, Any] = {}
    if cfg_path.is_file():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = _strip_sensitive(raw)
        except (OSError, ValueError):
            data = {}
    merged = _merge(DEFAULTS, data)
    _cache = (cfg_path, merged)
    return merged


def ensure_file(path: str | os.PathLike | None = None) -> Path:
    """Write the default config template if the file does not exist yet."""
    cfg_path = Path(path) if path else Path(CONFIG_FILE)
    if cfg_path.exists():
        return cfg_path
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(DEFAULTS, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return cfg_path


def save_last_room(
    channel: str,
    room_name: str = "",
    data_dir: str | os.PathLike | None = None,
    *,
    mode: str = "",
) -> None:
    """记录最近一次成功进入的房间（data/last_room.json，供无参数重进/CLI 复用）。

    写入失败只静默跳过，不影响进房流程本身。
    """
    if not channel:
        return
    try:
        import time

        base = Path(data_dir) if data_dir is not None else DATA_DIR
        base.mkdir(parents=True, exist_ok=True)
        payload = {
            "channel": str(channel),
            "room_name": str(room_name or ""),
            "entered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if mode:
            payload["mode"] = str(mode)
        target = base / "last_room.json"
        tmp = target.with_name("last_room.json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
    except Exception:
        pass


def load_last_room(data_dir: str | os.PathLike | None = None) -> dict[str, Any] | None:
    """返回最近一次进入的房间记录；没有记录或文件损坏时返回 None。"""
    try:
        base = Path(data_dir) if data_dir is not None else DATA_DIR
        target = base / "last_room.json"
        if not target.is_file():
            return None
        data = json.loads(target.read_text(encoding="utf-8"))
        if data.get("channel"):
            return data
    except Exception:
        return None
    return None


def set_option(section: str, key: str, value: Any, path: str | os.PathLike | None = None) -> Path:
    """Write one option into the config file (preserving other keys) and refresh the cache."""
    cfg_path = Path(path) if path else Path(CONFIG_FILE)
    data: dict[str, Any] = {}
    if cfg_path.is_file():
        try:
            loaded = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
    data.setdefault(section, {})[key] = value
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    load(cfg_path, refresh=True)
    return cfg_path


def load_secrets(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """敏感参数(独立文件),与普通配置分开存放;文件缺失或损坏时返回空。"""
    cfg_path = Path(path) if path else Path(SECRETS_FILE)
    try:
        # ``clubdeck.secrets.json`` may be written by Windows editors or
        # PowerShell with a UTF-8 BOM.  ``utf-8-sig`` accepts both BOM and
        # BOM-less files, so a valid secret file is not silently treated as
        # missing.
        raw = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def get_secret(key: str, default: str = "", path: str | os.PathLike | None = None) -> str:
    return str(load_secrets(path).get(key, default))


def _restrict_secrets_file(path: Path) -> None:
    """Windows 上限制 ACL 为当前用户;POSIX 上 chmod 600。"""
    try:
        if os.name == "nt":
            user = os.environ.get("USERNAME", "")
            if user:
                import subprocess
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(F)"],
                    check=False, capture_output=True, timeout=10,
                )
        else:
            os.chmod(path, 0o600)
    except Exception:
        pass


def set_secret(key: str, value: Any, path: str | os.PathLike | None = None) -> Path:
    """把一个敏感值写入独立 secrets 文件(空值删除);普通配置文件永远不存敏感值。"""
    cfg_path = Path(path) if path else Path(SECRETS_FILE)
    data = load_secrets(cfg_path)
    text = "" if value is None else str(value)
    if text:
        data[str(key)] = text
    else:
        data.pop(str(key), None)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _restrict_secrets_file(cfg_path)
    return cfg_path


def env_or(
    section: str,
    key: str,
    default: Any,
    env_name: str = "",
    *,
    cast: Callable[[str], Any] = str,
) -> Any:
    """Option value: env var (if set) > config file > default."""
    if env_name and env_name in os.environ:
        raw = os.environ[env_name]
        if cast is bool:
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        try:
            return cast(raw)
        except (TypeError, ValueError):
            return default
    value = load().get(section, {}).get(key, default)
    return value


def host_specs() -> tuple[int | None, float | None]:
    """(logical CPUs, total RAM in GB); either may be None if undetectable."""
    cpus = os.cpu_count() or 0
    ram_gb: float | None = None
    if os.name == "nt":
        try:
            import ctypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                ram_gb = status.ullTotalPhys / (1024 ** 3)
        except Exception:
            ram_gb = None
    else:
        try:
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        ram_gb = int(line.split()[1]) / 1024 / 1024
                        break
        except Exception:
            ram_gb = None
    return (cpus or None), (round(ram_gb, 1) if ram_gb else None)


def auto_whisper_model() -> str:
    """Pick the most balanced whisper model for the running host."""
    cpus, ram_gb = host_specs()
    cpus = cpus or 0
    if cpus <= 0:
        return "small"
    if cpus <= 4 or (ram_gb is not None and ram_gb < 8):
        return "tiny"
    if cpus <= 6 or (ram_gb is not None and ram_gb < 16):
        return "base"
    if cpus <= 12 or (ram_gb is not None and ram_gb < 64):
        return "small"
    return "medium"


def resolve_whisper_model(value: Any) -> str:
    """'auto'/'default'/empty resolves to the host-adapted model; anything else is used as-is."""
    text = str(value or "").strip().lower()
    if text in ("", "auto", "default"):
        return auto_whisper_model()
    return text


# 每段录音秒数:模型越快,单段可以越长(减少转写次数且不落后)
WHISPER_CHUNK_SECONDS: dict[str, int] = {
    "tiny": 25,
    "base": 20,
    "small": 15,
    "medium": 10,
    "large": 8,
}


def auto_transcribe_seconds(whisper_model: str) -> int:
    """Model-adapted recording chunk length for room transcription."""
    name = str(whisper_model or "").strip().lower()
    for key, seconds in WHISPER_CHUNK_SECONDS.items():
        if key in name:
            return seconds
    return 15


def resolve_transcribe_seconds(value: Any, whisper_model: str) -> int:
    """'auto'/empty uses the model-adapted length; a number is used as-is (clamped to >=1s)."""
    text = str(value or "").strip().lower()
    if text in ("", "auto", "default"):
        return auto_transcribe_seconds(whisper_model)
    try:
        return max(1, int(text))
    except (TypeError, ValueError):
        return auto_transcribe_seconds(whisper_model)


def ensure_model_cache() -> Path:
    """Point HuggingFace model downloads at the repo's models/ directory."""
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(HF_HUB_DIR))
    HF_HUB_DIR.mkdir(parents=True, exist_ok=True)
    return HF_HUB_DIR
