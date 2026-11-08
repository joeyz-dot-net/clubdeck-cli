#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Clubhouse TUI — 方向键控制的文字界面客户端(零依赖,Windows 原生 msvcrt + ANSI)。
脱离 Clubdeck 运行,直接打 clubhouseapi.com。

按键:
  ↑/↓        上下移动选中
  Enter       进入/确认
  Backspace   返回上级
  PgUp/PgDn   列表翻页
  q / Esc     退出(顶层)或返回(子层)
  /           搜索(在搜索页直接输入)

用法:
  python chcli_tui.py
"""
import os
import sys
import json
import re
import time
import threading
import shutil
import uuid
from pathlib import Path
import requests

import clubdeck_config
import hanzi
from room_session import RoomSessionManager
from vad_segment import has_volume_activity, transcribe_with_vad

CRASH_LOG = clubdeck_config.PROJECT_DIR / "logs" / "tui-crash.log"


def _log_crash(message: str) -> None:
    """把退出/异常线索追加到 logs/tui-crash.log，便于排查「运行一段时间被调出房间」。"""
    try:
        CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(CRASH_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] pid={os.getpid()} {message}\n")
    except Exception:
        pass


API_ROOT = "https://www.clubhouseapi.com/api"
DEVICE_ID = ""  # compatibility export; runtime uses profile or a persisted UUIDv4
CLIENT_DEVICE_ID_FILE = clubdeck_config.DATA_DIR / "clubhouse-device-id.txt"
# Verified against the current official Android package (APKPure 26.08.11,
# versionCode 1037961; iOS App Store also 26.08.11).  These three values must
# move together: new-room stage endpoints reject stale/mixed identities.
APP_VERSION = "26.08.11"
APP_BUILD = "1037961"
UA = "clubhouse/android/1037961"
# Official Android creates this once when the app session opens and reuses it
# for every API request until the process restarts.
CLIENT_SESSION_ID = str(uuid.uuid4())

# ---------- options (config file clubdeck.config.json; env vars only as legacy override) ----------
CH_AUTO_TRANSCRIBE = clubdeck_config.env_or("tui", "auto_transcribe", True, "CH_AUTO_TRANSCRIBE", cast=bool)
CH_WHISPER_MODEL = clubdeck_config.resolve_whisper_model(
    clubdeck_config.env_or("tui", "whisper_model", "auto", "CH_WHISPER_MODEL", cast=str)
)
CH_TRANSCRIBE_SECONDS = clubdeck_config.resolve_transcribe_seconds(
    clubdeck_config.env_or("tui", "transcribe_seconds", "auto", "CH_TRANSCRIBE_SECONDS", cast=str),
    CH_WHISPER_MODEL,
)
CH_WHISPER_VAD = clubdeck_config.env_or("tui", "whisper_vad", False, "CH_WHISPER_VAD", cast=bool)
CH_STARTUP_SELF_CHECK = clubdeck_config.env_or(
    "tui", "startup_self_check", True, "CH_STARTUP_SELF_CHECK", cast=bool
)

CH_TRANSCRIBE_MAX_LINES = max(1, clubdeck_config.env_or("tui", "transcribe_max_lines", 20, "CH_TRANSCRIBE_MAX_LINES", cast=int))


def reload_model_settings():
    """Recompute model/chunk-length from current config (used by the settings UI)."""
    global CH_WHISPER_MODEL, CH_TRANSCRIBE_SECONDS, CH_WHISPER_VAD
    CH_WHISPER_MODEL = clubdeck_config.resolve_whisper_model(
        clubdeck_config.env_or("tui", "whisper_model", "auto", "CH_WHISPER_MODEL", cast=str)
    )
    CH_TRANSCRIBE_SECONDS = clubdeck_config.resolve_transcribe_seconds(
        clubdeck_config.env_or("tui", "transcribe_seconds", "auto", "CH_TRANSCRIBE_SECONDS", cast=str),
        CH_WHISPER_MODEL,
    )
    CH_WHISPER_VAD = clubdeck_config.env_or("tui", "whisper_vad", False, "CH_WHISPER_VAD", cast=bool)
    return CH_WHISPER_MODEL, CH_TRANSCRIBE_SECONDS


def reload_tui_settings():
    """Recompute all TUI settings from current config (used by the settings UI)."""
    global CH_AUTO_TRANSCRIBE, CH_WHISPER_MODEL, CH_TRANSCRIBE_SECONDS
    global CH_TRANSCRIBE_MAX_LINES
    global CH_WHISPER_VAD
    global CH_STARTUP_SELF_CHECK
    CH_AUTO_TRANSCRIBE = clubdeck_config.env_or("tui", "auto_transcribe", True, "CH_AUTO_TRANSCRIBE", cast=bool)
    CH_WHISPER_MODEL = clubdeck_config.resolve_whisper_model(
        clubdeck_config.env_or("tui", "whisper_model", "auto", "CH_WHISPER_MODEL", cast=str)
    )
    CH_TRANSCRIBE_SECONDS = clubdeck_config.resolve_transcribe_seconds(
        clubdeck_config.env_or("tui", "transcribe_seconds", "auto", "CH_TRANSCRIBE_SECONDS", cast=str),
        CH_WHISPER_MODEL,
    )
    CH_WHISPER_VAD = clubdeck_config.env_or("tui", "whisper_vad", False, "CH_WHISPER_VAD", cast=bool)
    CH_STARTUP_SELF_CHECK = clubdeck_config.env_or(
        "tui", "startup_self_check", True, "CH_STARTUP_SELF_CHECK", cast=bool
    )
    CH_TRANSCRIBE_MAX_LINES = max(1, clubdeck_config.env_or(
        "tui", "transcribe_max_lines", 20, "CH_TRANSCRIBE_MAX_LINES", cast=int
    ))
    AudioBridge.PLAYBACK_DEVICE = str(clubdeck_config.env_or(
        "tui", "agora_playback_device", "default", "CH_AGORA_PLAYBACK_DEVICE", cast=str
    )).strip()
    return CH_WHISPER_MODEL, CH_TRANSCRIBE_SECONDS


def param_defs_for(section):
    """某分区的可编辑参数列表;配置 DEFAULTS 里新增的参数会自动出现在设置菜单。"""
    return clubdeck_config.param_defs_for(section)


# ---------- ANSI helpers ----------
ESC = "\x1b"
def cls(): sys.stdout.write(ESC + "[2J" + ESC + "[H")
def hide_cursor(): sys.stdout.write(ESC + "[?25l")
def show_cursor(): sys.stdout.write(ESC + "[?25h")

_ALT_DEPTH = 0


def enter_alt():
    """进入备用屏幕缓冲区：重绘与滚动彻底隔离，内容不会持续向下滚动。"""
    global _ALT_DEPTH
    _ALT_DEPTH += 1
    if _ALT_DEPTH == 1:
        sys.stdout.write(ESC + "[?1049h")
        sys.stdout.flush()


def leave_alt():
    """退出备用屏幕缓冲区，恢复进入前的终端画面。"""
    global _ALT_DEPTH
    if _ALT_DEPTH <= 0:
        return
    _ALT_DEPTH -= 1
    if _ALT_DEPTH == 0:
        sys.stdout.write(ESC + "[?1049l")
        sys.stdout.flush()


C_RESET = "\x1b[0m"
C_DIM = "\x1b[2m"
C_BOLD = "\x1b[1m"
C_CYAN = "\x1b[36m"
C_GREEN = "\x1b[32m"
C_YELLOW = "\x1b[33m"
C_RED = "\x1b[31m"
C_MAGENTA = "\x1b[35m"
C_INV = "\x1b[7m"


def disp_width(text):
    """Display columns of text: East Asian wide/fullwidth chars count as 2."""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(text))


def fit(text, width):
    """Truncate text so it occupies at most `width` display columns.

    Wide (CJK) characters are never split, and embedded newlines are
    flattened, so one logical line always stays one physical terminal line.
    """
    if width <= 0:
        return ""
    import unicodedata
    out = []
    used = 0
    for ch in str(text):
        if ch in "\r\n":
            ch = " "
        wch = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + wch > width:
            break
        out.append(ch)
        used += wch
    return "".join(out)

TOKEN = ""
USER_ID = ""
_CLUBDECK_PROFILE_IDENTITY = {}
_FALLBACK_DEVICE_IDS = {}


def _as_absolute_or_which(value: str) -> str:
    """把配置值变成可执行路径：绝对/相对路径直接 abspath，裸命令名(如 node)走 PATH 查找。"""
    raw = os.path.expandvars(os.path.expanduser(value))
    if os.path.sep in raw or (os.path.altsep and os.path.altsep in raw):
        return os.path.abspath(raw)
    found = shutil.which(raw)
    return found or raw


def _resolve_project_path(value: str) -> str:
    """Anchor relative path-like values to the project root."""
    if (os.path.sep in value or (os.path.altsep and os.path.altsep in value)) and not os.path.isabs(value):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), value)
    return value


def _project_electron_runtime() -> str:
    """项目自带独立 Electron 12(ABI 87) 运行时：third_party/electron12/electron.exe。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "third_party", "electron12", "electron.exe")


def _project_sdk_dir() -> str:
    """项目自带 agora-electron-sdk 目录：third_party/agora-electron-sdk。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "third_party", "agora-electron-sdk")


def resolve_bridge_runtime() -> str:
    """解析运行 agora_bridge.js 的可执行文件，带回退链：

    1. 显式 `bridge_runtime`（tui.bridge_runtime / CLUBDECK_BRIDGE_RUNTIME）
    2. 项目自带独立 Electron 12(ABI 87)：third_party/electron12/electron.exe（默认）

    即使文件不存在也返回解析路径，方便调用方给出精确报错。
    """
    candidates = []
    cfg_runtime = str(clubdeck_config.env_or(
        "tui", "bridge_runtime", "", "CLUBDECK_BRIDGE_RUNTIME", cast=str
    )).strip()
    if cfg_runtime:
        candidates.append(_as_absolute_or_which(_resolve_project_path(cfg_runtime)))
    candidates.append(_project_electron_runtime())
    for candidate in dict.fromkeys(candidates):
        if os.path.isfile(candidate):
            return candidate
    return candidates[0] if candidates else ""


def resolve_agora_sdk_dir() -> str:
    """agora-electron-sdk 目录：显式 agora_sdk_dir / CLUBDECK_SDK_DIR 优先，
    其次项目自带 third_party/agora-electron-sdk（默认）。"""
    sdk = str(clubdeck_config.env_or("tui", "agora_sdk_dir", "", "CLUBDECK_SDK_DIR", cast=str)).strip()
    if sdk:
        return _as_absolute_or_which(_resolve_project_path(sdk))
    project_sdk = _project_sdk_dir()
    if os.path.isdir(project_sdk):
        return project_sdk
    return ""


def load_clubdeck_profile(bases=None):
    """Load Clubdeck's local profile without logging any credential fields."""
    candidates = bases or [os.environ.get("APPDATA", ""), os.path.expanduser("~/AppData/Roaming")]
    seen = set()
    for base in candidates:
        if not base:
            continue
        p = os.path.join(base, "Clubdeck", "profile.json")
        p_key = os.path.normcase(os.path.abspath(p))
        if p_key in seen:
            continue
        seen.add(p_key)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    d = json.load(fh)
                if isinstance(d, dict):
                    return d
            except Exception:
                pass
    return {}


def set_clubdeck_profile_identity(profile):
    """Keep only non-secret client identity fields from a Clubdeck profile."""
    global _CLUBDECK_PROFILE_IDENTITY
    allowed = (
        "deviceId", "device_id", "appVersion", "app_version", "appBuild",
        "app_build", "userAgent", "user_agent", "languages", "locale",
        "acceptLanguages", "accept_language", "deviceMfr", "device_mfr",
        "deviceModel", "device_model", "keyboards", "storefrontCountryCode",
        "storefront_country_code",
    )
    source = profile if isinstance(profile, dict) else {}
    _CLUBDECK_PROFILE_IDENTITY = {
        key: source[key] for key in allowed if source.get(key) not in (None, "")
    }


def load_token_from_clubdeck():
    profile = load_clubdeck_profile()
    set_clubdeck_profile_identity(profile)
    return profile.get("token", ""), str(profile.get("userId", ""))


def configure_credentials(token="", user_id="", *, profile=None):
    """Bind credentials to client identity without mixing different accounts.

    Clubdeck's device identity is adopted only when both selected credentials
    match that profile.  Environment/secrets credentials for another account
    therefore use CH_DEVICE_ID/config or the persisted installation UUID.
    """
    profile = load_clubdeck_profile() if profile is None else profile
    source = profile if isinstance(profile, dict) else {}
    selected_token = str(token or "").strip()
    selected_user_id = str(user_id or "").strip()
    profile_token = str(source.get("token") or "").strip()
    profile_user_id = str(source.get("userId") or "").strip()
    if not selected_token and not selected_user_id:
        selected_token = profile_token
        selected_user_id = profile_user_id
    elif selected_token and not selected_user_id and selected_token == profile_token:
        selected_user_id = profile_user_id
    elif selected_user_id and not selected_token and selected_user_id == profile_user_id:
        selected_token = profile_token
    matches_profile = bool(
        selected_token
        and selected_user_id
        and selected_token == profile_token
        and selected_user_id == profile_user_id
    )
    set_clubdeck_profile_identity(source if matches_profile else {})
    return selected_token, selected_user_id


def _identity_value(source, *keys):
    for key in keys:
        value = source.get(key) if isinstance(source, dict) else None
        if value not in (None, ""):
            if isinstance(value, (list, tuple)):
                return ",".join(str(item) for item in value if item not in (None, ""))
            return str(value).strip()
    return ""


def _version_key(value):
    return tuple(int(part) for part in re.findall(r"\d+", str(value)))


def _build_key(value):
    digits = re.findall(r"\d+", str(value))
    return int(digits[-1]) if digits else -1


def _valid_device_id(value):
    text = str(value or "").strip()
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError, TypeError):
        return ""
    return text if parsed.version == 4 else ""


def _load_or_create_device_id(path=None):
    """Return a stable local UUIDv4 when Clubdeck has no valid device ID."""
    target = Path(path or CLIENT_DEVICE_ID_FILE)
    cache_key = os.path.normcase(os.path.abspath(str(target)))
    cached = _FALLBACK_DEVICE_IDS.get(cache_key, "")
    if cached:
        return cached
    try:
        existing = _valid_device_id(target.read_text(encoding="utf-8"))
        if existing:
            _FALLBACK_DEVICE_IDS[cache_key] = existing
            return existing
    except OSError:
        pass
    generated = str(uuid.uuid4())
    # Cache before persisting so a read-only data directory still keeps one
    # coherent device identity for every request in this process.
    _FALLBACK_DEVICE_IDS[cache_key] = generated
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(generated + "\n", encoding="utf-8")
        tmp.replace(target)
    except OSError:
        # Still avoid the repository-wide historical UUID.  This process keeps
        # the generated identity even if the local persistence write fails.
        pass
    return generated


def resolve_client_identity(profile=None, *, env=None, config=None):
    """Resolve one coherent, non-secret Clubhouse mobile client identity.

    Explicit environment overrides win.  Otherwise the configured verified
    tuple is used, unless Clubdeck has a strictly newer complete tuple.  The
    stable device ID/language/locale continue to come from the logged-in
    Clubdeck profile when they are not explicitly configured.
    """
    env = os.environ if env is None else env
    profile = _CLUBDECK_PROFILE_IDENTITY if profile is None else profile
    if config is None:
        loaded = clubdeck_config.load()
        config = loaded.get("clubhouse", {}) if isinstance(loaded, dict) else {}
    config = config if isinstance(config, dict) else {}

    env_tuple_keys = ("CH_APP_VERSION", "CH_APP_BUILD", "CH_USER_AGENT")
    present_env_tuple = [key for key in env_tuple_keys if str(env.get(key) or "").strip()]
    if present_env_tuple and len(present_env_tuple) != len(env_tuple_keys):
        missing = ", ".join(key for key in env_tuple_keys if key not in present_env_tuple)
        raise ValueError(f"incomplete Clubhouse client identity override; missing {missing}")

    config_tuple_keys = ("app_version", "app_build", "user_agent")
    present_config_tuple = [
        key for key in config_tuple_keys if str(config.get(key) or "").strip()
    ]
    if present_config_tuple and len(present_config_tuple) != len(config_tuple_keys):
        missing = ", ".join(key for key in config_tuple_keys if key not in present_config_tuple)
        raise ValueError(f"incomplete Clubhouse client identity config; missing {missing}")

    explicit_env_tuple = len(present_env_tuple) == len(env_tuple_keys)
    explicit_config_tuple = len(present_config_tuple) == len(config_tuple_keys)
    if explicit_env_tuple:
        configured_version = str(env["CH_APP_VERSION"]).strip()
        configured_build = str(env["CH_APP_BUILD"]).strip()
        configured_ua = str(env["CH_USER_AGENT"]).strip()
    elif explicit_config_tuple:
        configured_version = str(config["app_version"]).strip()
        configured_build = str(config["app_build"]).strip()
        configured_ua = str(config["user_agent"]).strip()
    else:
        configured_version, configured_build, configured_ua = APP_VERSION, APP_BUILD, UA

    profile_version = _identity_value(profile, "appVersion", "app_version")
    profile_build = _identity_value(profile, "appBuild", "app_build")
    explicit_app_tuple = explicit_env_tuple or explicit_config_tuple
    profile_tuple_is_newer = (
        _version_key(profile_version) > _version_key(configured_version)
        or (
            _version_key(profile_version) == _version_key(configured_version)
            and _build_key(profile_build) > _build_key(configured_build)
        )
    )
    use_profile_tuple = (
        not explicit_app_tuple
        and profile_version
        and profile_build
        and profile_tuple_is_newer
    )
    if use_profile_tuple:
        app_version = profile_version
        app_build = profile_build
        user_agent = _identity_value(profile, "userAgent", "user_agent")
    else:
        app_version = configured_version
        app_build = configured_build
        user_agent = configured_ua

    # Clubdeck 2.x stores the generic value "clubhouse/android".  The modern
    # official API identity includes the build suffix.
    if not user_agent or ("clubhouse/android" in user_agent.lower() and app_build not in user_agent):
        user_agent = f"clubhouse/android/{app_build}"

    explicit_device_id = str(env.get("CH_DEVICE_ID") or config.get("device_id") or "").strip()
    if explicit_device_id:
        device_id = _valid_device_id(explicit_device_id)
        if not device_id:
            raise ValueError("CH_DEVICE_ID/clubhouse.device_id must be a UUIDv4")
    else:
        profile_device_id = _identity_value(profile, "deviceId", "device_id")
        device_id = _valid_device_id(profile_device_id) or _load_or_create_device_id()
    languages = str(
        env.get("CH_LANGUAGES")
        or config.get("languages")
        or _identity_value(profile, "languages")
        or "en-US"
    ).strip()
    locale = str(
        env.get("CH_LOCALE")
        or config.get("locale")
        or _identity_value(profile, "locale")
        or "en_US"
    ).strip()
    accept_language = str(
        env.get("CH_ACCEPT_LANGUAGE")
        or config.get("accept_language")
        or _identity_value(profile, "acceptLanguages", "accept_language")
        or languages.split(",", 1)[0]
        or locale.replace("_", "-")
        or "en-US"
    ).strip()
    device_mfr = str(
        env.get("CH_DEVICE_MFR")
        or config.get("device_mfr")
        or _identity_value(profile, "deviceMfr", "device_mfr")
        or "Google"
    ).strip()
    device_model = str(
        env.get("CH_DEVICE_MODEL")
        or config.get("device_model")
        or _identity_value(profile, "deviceModel", "device_model")
        or "Pixel 8"
    ).strip()
    keyboards = str(
        env.get("CH_KEYBOARDS")
        or config.get("keyboards")
        or _identity_value(profile, "keyboards")
        or "Gboard"
    ).strip()
    # Match the current Android interceptor's header-safe keyboard-name filter.
    keyboards = re.sub(r"[^A-Za-z _-]", "", keyboards).strip() or "Gboard"
    storefront_country_code = str(
        env.get("CH_STOREFRONT_COUNTRY_CODE")
        or config.get("storefront_country_code")
        or _identity_value(profile, "storefrontCountryCode", "storefront_country_code")
        or ""
    ).strip()
    return {
        "device_id": device_id,
        "app_version": app_version,
        "app_build": app_build,
        "user_agent": user_agent,
        "languages": languages,
        "locale": locale,
        "accept_language": accept_language,
        "device_mfr": device_mfr,
        "device_model": device_model,
        "keyboards": keyboards,
        "storefront_country_code": storefront_country_code,
    }


def make_headers(token, user_id):
    identity = resolve_client_identity()
    result = {
        "Authorization": f"Token {token}",
        "CH-DeviceId": identity["device_id"],
        "CH-AppVersion": identity["app_version"],
        "CH-AppBuild": identity["app_build"],
        "CH-UserID": str(user_id),
        "CH-Locale": identity["locale"],
        "CH-Session-Id": CLIENT_SESSION_ID,
        "CH-DeviceMfr": identity["device_mfr"],
        "CH-DeviceModel": identity["device_model"],
        "CH-Keyboards": identity["keyboards"],
        "Accept-Language": identity["accept_language"],
        "User-Agent": identity["user_agent"],
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if identity["storefront_country_code"]:
        result["CH-Storefront-Country-Code"] = identity["storefront_country_code"]
    return result


def headers():
    return make_headers(TOKEN, USER_ID)


def api(path, body=None, method="POST"):
    url = API_ROOT + path
    try:
        if method == "GET":
            r = requests.get(url, headers=headers(), params=body, timeout=20)
        else:
            r = requests.post(url, headers=headers(), json=body or {}, timeout=20)
        if r.status_code == 404:
            return {
                "success": False,
                "status_code": 404,
                "error_message": f"endpoint {path} not found (404)",
            }
        try:
            result = r.json()
        except ValueError:
            detail = str(getattr(r, "text", "") or "").strip()[:200]
            return {
                "success": False,
                "status_code": r.status_code,
                "error_message": f"HTTP {r.status_code}: {detail or 'non-JSON response'}",
            }
        if not isinstance(result, dict):
            return {
                "success": False,
                "status_code": r.status_code,
                "error_message": f"HTTP {r.status_code}: invalid response",
            }
        if r.status_code >= 400:
            result["success"] = False
            if result.get("status_code") is None:
                result["status_code"] = r.status_code
            if not result.get("error_message"):
                result["error_message"] = f"HTTP {r.status_code}"
        return result
    except Exception as e:
        return {"success": False, "error_message": str(e)}


# ---------- room loaders ----------
def load_rooms(cursor=""):
    """One page of active rooms from get_feed_v3; returns (rows, next_cursor)."""
    d = api("/get_feed_v3", {"next_cursor": cursor})
    items = d.get("items", [])
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        ch = it.get("channel")
        if not ch:
            continue
        priv = "私" if ch.get("is_private") else "公"
        spk = ch.get("num_speakers") or 0
        total = ch.get("num_all") or 0
        rows.append({
            "title": f"[{priv}] {ch.get('topic') or '(no topic)'} · {spk}讲/{total}人",
            "id": ch.get("channel"),
            "obj": ch,
        })
    # 人数多的房间排前面
    rows.sort(key=lambda r: ((r["obj"].get("num_all") or 0), (r["obj"].get("num_speakers") or 0)), reverse=True)
    return rows, d.get("next_cursor") or d.get("cursor") or ""


def friendly_room_name(channel: str, max_pages: int = 3) -> str:
    """按 channel 从活跃房间 feed 里查友好房名。

    音乐房的 join 响应 topic 是当前曲目而非房间名，因此持久化和展示都以
    feed 的 topic 为准；查不到时返回空串（调用方回退到 channel id）。
    """
    if not channel:
        return ""
    cursor = ""
    for _ in range(max_pages):
        try:
            rows, cursor = load_rooms(cursor)
        except Exception:
            break
        for r in rows:
            if r.get("id") == channel:
                obj = r.get("obj") or {}
                privacy = obj.get("privacy_settings") or {}
                # topic 是房间标题；频道名称在频道的 privacy_settings.display_text
                # 中，例如「飞猪音乐」。
                return str(
                    privacy.get("display_text")
                    or (obj.get("social_club") or {}).get("name")
                    or obj.get("channel_name")
                    or obj.get("topic")
                    or ""
                ).strip()
        if not cursor:
            break
    return ""


def leave_room(channel):
    return api("/leave_channel", {"channel": channel})


class AudioBridge:
    """Manages the listening bridge and transient room-audio recording."""
    # 生效的音频桥运行时：显式 bridge_runtime > 项目自带 Electron 12(ABI 87，默认)
    BRIDGE_RUNTIME = resolve_bridge_runtime()
    # 生效的 agora-electron-sdk 目录；默认项目自带 third_party/agora-electron-sdk
    AGORA_SDK_DIR = resolve_agora_sdk_dir()
    BRIDGE_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agora_bridge.js")
    APP_ID = "938de3e8055e42b281bb8c6f69c21f78"  # from profile.json agoraKey
    # 长跑保护：音量历史只保留最近 N 秒/最多 N 条，避免逐段扫描越来越慢、内存无界增长
    VOLUME_HISTORY_KEEP_SECONDS = 600.0
    VOLUME_HISTORY_MAX = 7200
    # 桥输出队列上限：消费跟不上时丢弃最旧行而不是无限积压
    BRIDGE_QUEUE_MAX = 5000
    PLAYBACK_DEVICE = str(clubdeck_config.env_or(
        "tui", "agora_playback_device", "default", "CH_AGORA_PLAYBACK_DEVICE", cast=str
    )).strip()

    @classmethod
    def validate_runtime(cls) -> None:
        """拉起桥之前快速失败，给出可操作的报错（而不是等 JOINED 超时）。"""
        runtime = str(cls.BRIDGE_RUNTIME or "")
        if not runtime or not os.path.isfile(runtime):
            raise FileNotFoundError(
                f"音频桥运行时未找到: {runtime or '(未配置)'}。"
                "默认使用项目自带 third_party/electron12/electron.exe；"
                "也可设置 tui.bridge_runtime / CLUBDECK_BRIDGE_RUNTIME 覆盖。"
            )
        sdk_dir = str(cls.AGORA_SDK_DIR or "")
        if not sdk_dir:
            raise ValueError(
                f"运行时 {runtime} 需要 agora_sdk_dir / CLUBDECK_SDK_DIR "
                "指向抽取的 agora-electron-sdk 目录（含 build/Release/agora_node_ext.node；"
                "运行时必须是 ABI 87 的 Electron 12，独立 Node 14 是 ABI 83 无法加载）。"
            )
        if sdk_dir:
            addon = os.path.join(sdk_dir, "build", "Release", "agora_node_ext.node")
            if not os.path.isfile(addon):
                raise FileNotFoundError(
                    f"SDK 目录 {sdk_dir} 里未找到 {addon}；请确认目录指向 agora-electron-sdk。"
                )

    @classmethod
    def bridge_env(cls) -> dict:
        """桥子进程环境：Electron 作纯 Node 用；显式 SDK 目录时注入 CLUBDECK_SDK_DIR。"""
        env = dict(os.environ)
        env["ELECTRON_RUN_AS_NODE"] = "1"  # 对独立 node.exe 无副作用
        if cls.AGORA_SDK_DIR:
            env["CLUBDECK_SDK_DIR"] = cls.AGORA_SDK_DIR
        return env

    def __init__(self):
        self.proc = None
        self.volume = {}  # uid -> volume
        self.volume_history = []  # [(timestamp, {uid: volume}), ...] for speaker attribution
        self.self_uid: str = ""
        self.joined = False
        self.last_error = ""
        self.last_exit_code: int | None = None
        # None means the child ended unexpectedly.  A non-empty reason records
        # the local control path which deliberately asked it to leave.
        self.last_stop_reason: str | None = None
        self._queue = None
        self._reader = None
        self._log_fh = None
        # 串行化 start/stop 生命周期：自动重连线程与上台刷新 token 线程可能并发
        # 调用 start()/stop()，无锁时后一个 stop() 会杀掉前一个刚 spawn 的桥，
        # 造成“双方都以为持有会话、实际只有一桥存活”的竞态。RLock 允许 start()
        # 内部复用 stop()。
        import threading as _threading
        self._op_lock = _threading.RLock()

    def _bridge_log_path(self) -> str:
        import datetime
        return os.path.join(clubdeck_config.PROJECT_DIR, "logs",
                            f"bridge-{datetime.datetime.now():%Y%m%d}.log")

    def _bridge_log(self, text: str) -> None:
        try:
            if self._log_fh is not None:
                # 按大小轮转：单日日志超过 5MB 时截断重开，避免无限膨胀
                if (self._log_fh.tell() > 5 * 1024 * 1024
                        and not text.startswith("[bridge] stop")):
                    try:
                        self._log_fh.close()
                    except Exception:
                        pass
                    self._log_fh = open(
                        self._bridge_log_path(), "a", encoding="utf-8", errors="replace"
                    )
                self._log_fh.write(text if text.endswith("\n") else text + "\n")
                self._log_fh.flush()
        except Exception:
            pass

    @property
    def active(self):
        if self.proc is not None and self.proc.poll() is not None and self.last_exit_code is None:
            code = getattr(self.proc, "returncode", None)
            self.last_exit_code = self.proc.poll() if code is None else code
            self._bridge_log(f"[bridge] exited code={self.last_exit_code}")
        return self.proc is not None and self.proc.poll() is None

    def _reader_loop(self, stream, queue):
        import threading
        for line in stream:
            raw = line.rstrip("\n")
            # VOLUME 每 500ms 一次（≈17 万行/天），不进日志以免按天无限膨胀；
            # 其它事件（JOINED/USER_*/CTRL/ERROR）保留便于排查。
            if not raw.startswith("[EVT] VOLUME"):
                self._bridge_log("[bridge] " + raw)
            if queue.qsize() >= self.BRIDGE_QUEUE_MAX:
                continue  # 消费跟不上时丢弃积压，避免内存无界增长
            queue.put(line)
        queue.put(None)  # EOF sentinel

    def _trim_volume_history(self, now: float | None = None) -> None:
        """裁剪当前房间的音量事件，供本段 VAD 回退判断使用。"""
        if not self.volume_history:
            return
        now = time.time() if now is None else now
        cutoff = now - self.VOLUME_HISTORY_KEEP_SECONDS
        drop = 0
        while drop < len(self.volume_history) and self.volume_history[drop][0] < cutoff:
            drop += 1
        if drop:
            del self.volume_history[:drop]
        over = len(self.volume_history) - self.VOLUME_HISTORY_MAX
        if over > 0:
            del self.volume_history[:over]

    def start(self, channel, token, uid):
        with self._op_lock:
            self.stop(reason=f"replace-before-start:{channel}")
            import subprocess, queue, threading
            self.validate_runtime()
            env = type(self).bridge_env()
            command = self._command(channel, token, uid)
            logged_command = list(command)
            if len(logged_command) > 4:
                logged_command[4] = "<redacted-token>"
            log_dir = os.path.join(clubdeck_config.PROJECT_DIR, "logs")
            os.makedirs(log_dir, exist_ok=True)
            self._log_fh = open(self._bridge_log_path(), "a", encoding="utf-8", errors="replace")
            self._bridge_log(f"[bridge] start channel={channel} uid={uid} cmd={' '.join(logged_command)}")
            self.proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log_fh,
                env=env, text=True, bufsize=1,
            )
            self._bridge_log(f"[bridge] spawned pid={getattr(self.proc, 'pid', '?')}")
            self.volume = {}
            self.volume_history = []
            self.self_uid = str(uid or "")
            self.joined = False
            self.last_error = ""
            self.last_exit_code = None
            self.last_stop_reason = None
            self._queue = queue.Queue()
            self._reader = threading.Thread(target=self._reader_loop, args=(self.proc.stdout, self._queue), daemon=True)
            self._reader.start()
            try:
                self._wait_joined()
            except Exception:
                self.stop(reason=f"join-failed:{channel}")  # do not leave an orphaned bridge process behind on failure
                raise

    def _wait_joined(self, timeout: float = 10.0) -> None:
        """Block until the bridge confirms join (JOINED/REJOINED), or fail fast.

        Lines consumed while waiting are re-queued so regular consumers still
        see them.  A non-zero joinChannel ret or a missing JOINED event makes
        the adapter failure visible instead of silently reporting success.
        """
        deadline = time.time() + timeout
        early: list[str] = []
        while time.time() < deadline:
            try:
                line = self._queue.get(timeout=0.5)
            except Exception:
                line = False  # no data within this wait slice
            if line is False:
                if self.proc is not None and self.proc.poll() is not None:
                    raise RuntimeError(
                        f"audio bridge exited before joining (code {self.proc.returncode})"
                    )
                continue
            if line is None:
                raise RuntimeError("audio bridge stdout closed before join confirmed")
            line = line.strip()
            if not line:
                continue
            ev = self._parse_line(line)
            if ev is not None and ev[0] == "joined":
                self.joined = True
                for old in early:
                    self._queue.put(old)
                self._queue.put(line)
                return
            if ev is not None and ev[0] == "error":
                self.last_error = line
            early.append(line)
        self.joined = False
        reason = f" ({self.last_error})" if self.last_error else ""
        raise RuntimeError(f"audio bridge did not confirm join within {int(timeout)}s{reason}")

    def _command(self, channel, token, uid):
        """Build an audio-only listening bridge command."""
        command = [self.BRIDGE_RUNTIME, self.BRIDGE_JS, self.APP_ID, channel, token, str(uid), "--listen"]
        if self.PLAYBACK_DEVICE and self.PLAYBACK_DEVICE.lower() != "default":
            command.extend(["--device", self.PLAYBACK_DEVICE])
        return command

    def stop(self, reason: str = "requested"):
        with self._op_lock:
            stop_error = None
            if self.proc:
                import subprocess

                proc = self.proc
                pid = getattr(proc, "pid", "?")
                self.last_stop_reason = str(reason or "requested")
                self._bridge_log(f"[bridge] stop requested pid={pid} reason={self.last_stop_reason}")
                try:
                    proc.stdin.write("q\n")
                    proc.stdin.flush()
                except Exception:
                    pass
                try:
                    code = proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._bridge_log(f"[bridge] graceful stop timeout pid={pid}; forcing kill")
                    try:
                        proc.kill()
                        code = proc.wait(timeout=5)
                    except Exception as exc:
                        stop_error = RuntimeError(
                            f"audio bridge pid={pid} did not exit after forced kill: {exc}"
                        )
                        code = getattr(proc, "returncode", None)
                except Exception as exc:
                    stop_error = RuntimeError(f"audio bridge pid={pid} stop failed: {exc}")
                    code = getattr(proc, "returncode", None)
                if stop_error is None:
                    self.proc = None
                    self.last_exit_code = code
                self._bridge_log(
                    f"[bridge] stop pid={pid} exit_code={code} reason={self.last_stop_reason}"
                )
                for stream_name in ("stdin", "stdout"):
                    try:
                        stream = getattr(proc, stream_name, None)
                        if stream is not None:
                            stream.close()
                    except Exception:
                        pass
            if self._log_fh is not None:
                try:
                    self._log_fh.close()
                except Exception:
                    pass
                self._log_fh = None
            self._queue = None
            self._reader = None
            self.joined = False
            if stop_error is not None:
                raise stop_error

    def start_recording(self, wav_path, position=0):
        if not self.active:
            return False
        import base64
        encoded_path = base64.b64encode(os.path.abspath(wav_path).encode("utf-8")).decode("ascii")
        try:
            self.proc.stdin.write(f"r64:{encoded_path}:{int(position)}\n")
            self.proc.stdin.flush()
            return True
        except Exception:
            return False

    def stop_recording(self):
        if not self.active:
            return False
        try:
            self.proc.stdin.write("x\n")
            self.proc.stdin.flush()
            return True
        except Exception:
            return False

    def read_events(self):
        """Non-blocking: drain all queued stdout lines; returns list of (type, payload)."""
        if not self.active or self._queue is None:
            return []
        events = []
        while True:
            try:
                line = self._queue.get_nowait()
            except Exception:
                break
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            ev = self._parse_line(line)
            if ev:
                events.append(ev)
        return events

    def _parse_line(self, line):
        if line.startswith("[EVT] VOLUME"):
            parts = line.replace("[EVT] VOLUME", "").strip().split()
            snapshot = {}
            for p in parts:
                try:
                    u, v = p.split(":")
                    if u == "ME":
                        u = self.self_uid or "0"
                    if not u.isdigit():
                        continue
                    snapshot[int(u)] = int(v)
                except Exception:
                    pass
            self.volume = snapshot
            if snapshot:
                self.volume_history.append((time.time(), snapshot))
                self._trim_volume_history()
            return ("volume", dict(snapshot))
        if line.startswith("[CTRL] recording="):
            return ("recording", line)
        if line.startswith("[EVT] JOINED") or line.startswith("[EVT] REJOINED"):
            self.joined = True
            return ("joined", line)
        if line.startswith("[EVT] ERROR"):
            return ("error", line)
        return None


# ---------- key reading (msvcrt, Windows-native, zero deps) ----------
def enable_vt():
    """Windows 控制台启用 ANSI VT 处理，让清屏/光标定位原地刷新生效。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if handle not in (None, -1, 0) and kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


def read_key():
    import msvcrt
    b = msvcrt.getwch() if hasattr(msvcrt, 'getwch') else msvcrt.getch()
    if hasattr(msvcrt, 'getwch'):
        if b in ("\x00", "\xe0"):
            b2 = msvcrt.getwch()
            mapping = {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT",
                       "G": "END", "O": "HOME", "I": "PGUP", "Q": "PGDN"}
            return mapping.get(b2, "KEY_" + b2)
        return b
    else:
        if b in (b"\x00", b"\xe0"):
            b2 = msvcrt.getch()
            mapping = {b"H": "UP", b"P": "DOWN", b"K": "LEFT", b"M": "RIGHT",
                       b"G": "END", b"O": "HOME", b"I": "PGUP", b"Q": "PGDN"}
            return mapping.get(b2, "KEY_" + str(b2))
        try:
            return b.decode()
        except Exception:
            return str(b)


# ---------- TUI core ----------
HEIGHT = None
WIDTH = None


def term_size():
    try:
        import shutil
        return shutil.get_terminal_size((80, 24))
    except Exception:
        return (80, 24)


def page_height():
    """Usable list rows per screen (header + hint take the rest)."""
    return max(1, term_size().lines - 4)


def rows_per_screen(rows):
    """How many list rows fit on screen, accounting for two-line rows (title+sub)."""
    has_sub = any(r.get("sub") and not r.get("dim") for r in rows)
    return max(1, page_height() // 2) if has_sub else page_height()


def draw_header(title):
    w = term_size().columns
    left = " Clubhouse TUI "
    right = "[q 退出 Esc 返回]"
    room = fit("  " + str(title), max(1, w - disp_width(left) - disp_width(right)))
    pad = max(0, w - disp_width(left) - disp_width(room) - disp_width(right))
    sys.stdout.write(C_CYAN + C_BOLD + left + C_RESET)
    sys.stdout.write(C_DIM + room + C_RESET + " " * pad + C_DIM + right + C_RESET + "\n")
    sys.stdout.write(C_DIM + "─" * w + C_RESET + "\n")


def draw_list(rows, sel, top, hint=""):
    page_lines = page_height()
    w = w_col()
    rendered = 0
    for idx, r in enumerate(rows[top:], start=top):
        lines_needed = 2 if (r.get("sub") and not r.get("dim")) else 1
        if rendered + lines_needed > page_lines:
            break
        line = r.get("title", "")
        sub = r.get("sub", "")
        if r.get("dim"):
            sys.stdout.write(C_DIM + fit(" " + line, w) + C_RESET + "\n")
        elif idx == sel:
            sys.stdout.write(C_INV + fit(" " + line, w) + C_RESET + "\n")
            if sub:
                sys.stdout.write(C_DIM + fit("  " + sub, w) + C_RESET + "\n")
        else:
            sys.stdout.write(fit(" " + line, w) + "\n")
            if sub:
                sys.stdout.write(C_DIM + fit("  " + sub, w) + C_RESET + "\n")
        rendered += lines_needed
    # fill remaining lines
    for _ in range(max(0, page_lines - rendered)):
        sys.stdout.write("\n")
    if hint:
        sys.stdout.write(C_DIM + fit(hint, w) + C_RESET)


def w_col():
    return term_size().columns


def draw_keyvals(rows, sel, top):
    page = page_height()
    w = w_col()
    for i, (k, v) in enumerate(rows[top:top + page]):
        idx = top + i
        k = str(k)
        if not k.strip():
            line = fit("  " + str(v), w)
            if not line.strip():
                continue
            sys.stdout.write(line + "\n")
        elif idx == sel:
            sys.stdout.write(C_INV + fit(f" {k}: {v}", w) + C_RESET + "\n")
        else:
            head = f" {k}:"
            sys.stdout.write(C_DIM + head + C_RESET + fit(" " + str(v), max(1, w - disp_width(head))) + "\n")
    for _ in range(page - min(page, len(rows) - top)):
        sys.stdout.write("\n")


def draw_room(frame):
    rows = list(frame.get("rows", []))
    error = frame.get("_audio_error")
    if error:
        rows.append(("音频状态", error))
    draw_keyvals(rows, frame.get("sel", 0), frame.get("top", 0))


class Screen:
    """A stack of frames. Each frame: {'type': 'list'|'kv'|'input', 'rows': [...], 'sel': int,
    'top': int, 'title': str, 'loader': callable returning rows, 'on_enter': handler,
    'on_back': handler, 'query': str}"""

    def __init__(self):
        self.frames = []
        self.running = True
        # 渲染锁：房内刷新线程 / 转写线程 / 主线程共用，串行化 rows 修改与全屏重绘
        self._render_lock = threading.RLock()

    @property
    def cur(self):
        return self.frames[-1] if self.frames else None

    def push(self, frame):
        frame.setdefault("sel", 0)
        frame.setdefault("top", 0)
        frame.setdefault("query", "")
        self.frames.append(frame)

    def pop(self):
        if len(self.frames) > 1:
            self.frames.pop()

    def refresh(self, clear: bool = True):
        """Clear the screen (可选), then redraw the current frame (刷新前清屏)."""
        with self._render_lock:
            if clear:
                cls()
            f = self.cur
            if f is None:
                return
            draw_header(f.get("title", ""))
            if f["type"] == "list":
                draw_list(f["rows"], f["sel"], f["top"], f.get("hint", ""))
            elif f["type"] == "kv":
                if f.get("room_channel"):
                    draw_room(f)
                else:
                    draw_keyvals(f["rows"], f["sel"], f["top"])
            elif f["type"] == "input":
                hint = f.get("hint", "")
                sys.stdout.write(C_YELLOW + fit(hint, w_col()) + C_RESET + "\n")
                sys.stdout.write(fit("> " + f["query"], max(1, w_col() - 1)) + "█\n")
            sys.stdout.flush()

    def move(self, delta):
        f = self.cur
        if f["type"] == "list" and f["rows"]:
            n = len(f["rows"])
            f["sel"] = max(0, min(n - 1, f["sel"] + delta))
            page = rows_per_screen(f["rows"])
            if f["sel"] < f["top"]:
                f["top"] = f["sel"]
            elif f["sel"] >= f["top"] + page:
                f["top"] = f["sel"] - page + 1
        elif f["type"] == "kv":
            if f.get("room_channel"):
                # 房间视图：↑/↓ 滚动房间信息和当前转写区。
                f["info_top"] = max(0, int(f.get("info_top", 0)) + delta)
            else:
                n = len(f["rows"])
                f["sel"] = max(0, min(n - 1, f["sel"] + delta))

    def page(self, delta):
        f = self.cur
        page = rows_per_screen(f["rows"]) if f["type"] == "list" else page_height()
        self.move(delta * page)

    def _maybe_page_end(self):
        """Auto-load the next page when the user reaches the bottom of a paged list."""
        f = self.cur
        if f["type"] != "list" or not f["rows"] or not f.get("on_page_end"):
            return
        if f["sel"] >= len(f["rows"]) - 1:
            f["on_page_end"](f)

    def enter(self):
        f = self.cur
        if f["type"] == "input":
            if f.get("on_submit"):
                f["on_submit"](f["query"])
            return
        if not f["rows"]:
            return
        row = f["rows"][f["sel"]]
        if f.get("on_enter"):
            f["on_enter"](row)

    def back(self):
        f = self.cur
        if f.get("on_back"):
            f["on_back"]()
        else:
            self.pop()

    def handle(self, key):
        f = self.cur
        if f["type"] == "input":
            if key == "\r" or key == "\n":
                self.enter()
            elif key in ("\x08", "\x7f", "BACKSPACE"):
                f["query"] = f["query"][:-1]
            elif key in ("\x1b", "ESC"):
                self.pop()
            elif len(key) == 1 and key.isprintable():
                f["query"] += key
            return
        # A room leave destroys the live Agora/audio session.  Require a
        # deliberate second press so a stray q/L/Esc cannot look like a random
        # room dropout.  Any non-leave key cancels the pending confirmation.
        leave_keys = ("l", "L", "q", "Q", "\x1b", "ESC")
        if f.get("room_channel") and key in leave_keys:
            now = time.monotonic()
            if now <= float(f.get("_leave_confirm_until", 0.0)):
                _log_crash(
                    f"用户确认离房 room={f.get('room_channel')} key={key!r}"
                )
                if f.get("on_leave"):
                    f["on_leave"]()
                else:
                    self.back()
                return
            f["_leave_confirm_until"] = now + 2.0
            f.setdefault("_normal_hint", f.get("hint", ""))
            f["hint"] = "再次按 L / q / Esc 确认离开房间（2 秒内）"
            _log_crash(
                f"收到离房按键(待确认) room={f.get('room_channel')} key={key!r}"
            )
            return
        if f.get("_leave_confirm_until"):
            f["_leave_confirm_until"] = 0.0
            f["hint"] = f.get("_normal_hint", f.get("hint", ""))
        if key == "UP":
            self.move(-1)
        elif key == "DOWN":
            self.move(1)
            self._maybe_page_end()
        elif key == "PGUP":
            self.page(-1)
        elif key == "PGDN":
            self.page(1)
            self._maybe_page_end()
        elif key == "HOME":
            self.move(-10 ** 9)
        elif key == "END":
            self.move(10 ** 9)
            self._maybe_page_end()
        elif key in ("\r", "\n"):
            self.enter()
        elif key in ("l", "L") and f.get("on_leave"):
            f["on_leave"]()
        elif key in ("t", "T") and f.get("on_transcribe"):
            f["on_transcribe"]()
        elif key in ("\x1b", "ESC"):
            self.back()
        elif key in ("q", "Q"):
            if len(self.frames) <= 1:
                self.running = False
            else:
                self.back()


# ---------- app wiring ----------
def make_app():
    app = Screen()
    audio = AudioBridge()
    app._audio = audio  # expose for main loop pump

    def push_settings():
        rows = [
            {"title": "转写参数", "sub": "模型 / 片段长度 / 设备", "obj": "tui"},
            {"title": "敏感参数（单独保存）", "sub": "登录凭证保存在 clubdeck.secrets.json", "obj": "secrets"},
        ]
        app.push({"type": "list", "title": "设置", "rows": rows, "hint": "Enter=进入  Esc=返回",
                  "on_enter": lambda r: push_param_list(r["obj"])})

    def param_value_text(d):
        """参数当前值文本;敏感值只显示是否已设置。"""
        if d.get("section") == "secrets":
            return "已设置" if clubdeck_config.get_secret(d["key"]) else "未设置"
        section = d.get("section", "tui")
        default = clubdeck_config.DEFAULTS.get(section, {}).get(d["key"])
        v = clubdeck_config.load().get(section, {}).get(d["key"], default)
        if v is True:
            return "开"
        if v is False:
            return "关"
        if d.get("type") == "choice" and d.get("choices_fn") and not str(v).strip():
            return "默认设备"
        return str(v)

    def refresh_param_row(frame, d):
        for row in frame.get("rows", []):
            if row.get("obj") is d:
                row["sub"] = param_value_text(d)
                return

    def push_param_list(section):
        defs = param_defs_for(section)
        rows = [{"title": d["label"], "sub": param_value_text(d), "obj": d} for d in defs]
        title = {"tui": "转写参数", "secrets": "敏感参数（单独保存）"}[section]
        app.push({"type": "list", "title": title, "rows": rows, "hint": "Enter=修改  Esc=返回",
                  "on_enter": lambda r: edit_param(r["obj"])})

    def edit_param(d):
        kind = d.get("type", "str")
        if d.get("section") == "secrets":
            kind = "secret"
        if kind == "bool":
            toggle_bool(d)
        elif kind == "choice":
            push_choice_picker(d)
        else:
            current = "" if kind == "secret" else param_value_text(d)
            hint = ("输入后按 Enter 保存到 clubdeck.secrets.json（空=清除）  Esc=取消" if kind == "secret"
                    else f"输入新值后按 Enter（当前: {current}）  Esc=取消")
            app.push({"type": "input", "title": f"修改 {d['label']}", "query": current,
                      "hint": hint, "on_submit": lambda q: apply_value(d, q)})

    def toggle_bool(d):
        section = d["section"]
        v = clubdeck_config.load().get(section, {}).get(d["key"], False)
        clubdeck_config.set_option(section, d["key"], not v)
        if section == "tui":
            reload_tui_settings()
        refresh_param_row(app.cur, d)
        app.refresh()

    def push_choice_picker(d):
        section = d["section"]
        current = str(clubdeck_config.load().get(section, {}).get(
            d["key"], clubdeck_config.DEFAULTS.get(section, {}).get(d["key"], "")))
        choices = clubdeck_config.resolve_choices(d)
        rows = [{"title": f"{'✓ ' if str(c) == current else '  '}{c}", "obj": c} for c in choices]
        if d.get("choices_fn"):
            # 设备枚举可能失败或设备名有变化,保留手动输入入口
            rows.append({"title": "输入自定义设备名…", "obj": "__custom__"})
        app.push({"type": "list", "title": f"选择 {d['label']}（当前 {current}）", "rows": rows,
                  "hint": "↑/↓ 选择  Enter=确认  Esc=返回",
                  "on_enter": lambda r: push_custom_device_input(d) if r["obj"] == "__custom__" else apply_value(d, r["obj"])})

    def push_custom_device_input(d):
        section = d["section"]
        current = str(clubdeck_config.load().get(section, {}).get(
            d["key"], clubdeck_config.DEFAULTS.get(section, {}).get(d["key"], "")))
        app.push({"type": "input", "title": f"输入 {d['label']}",
                  "query": "" if not current.strip() or current == "default" else current,
                  "hint": "输入设备名（可只输关键字，如 Yamaha）后按 Enter  Esc=取消",
                  "on_submit": lambda q: apply_value(d, q)})

    def apply_value(d, raw):
        if d.get("type") == "int":
            try:
                v = int(str(raw).strip())
            except (TypeError, ValueError):
                app.cur["hint"] = f"无效数字: {raw}，请重新输入  Esc=取消"
                app.cur["query"] = ""
                app.refresh()
                return
            v = max(d.get("min", -2 ** 31), v)
            if "max" in d:
                v = min(d["max"], v)
        elif d.get("type") == "choice":
            choices = clubdeck_config.resolve_choices(d)
            if raw not in choices:
                if d.get("choices_fn") and str(raw or "").strip():
                    # 动态设备枚举失败或设备名有变化时,允许直接输入设备名
                    v = str(raw).strip()
                else:
                    app.cur["hint"] = f"无效选项: {raw}，请重新选择  Esc=取消"
                    app.refresh()
                    return
            else:
                v = raw
        else:
            v = str(raw or "").strip()
        if d.get("section") == "secrets":
            clubdeck_config.set_secret(d["key"], v)
        else:
            clubdeck_config.set_option(d["section"], d["key"], v)
            if d["section"] == "tui":
                reload_tui_settings()
                if d["key"] == "whisper_model":
                    app._whisper_model = None  # 下次转写加载新模型
        app.pop()
        refresh_param_row(app.cur, d)
        app.refresh()

    def load_more_rooms(frame):
        """Fetch the next page of rooms and append it to the list frame."""
        cursor = frame.get("_room_cursor", "")
        if frame.get("_room_loading") or not cursor:
            return
        frame["_room_loading"] = True
        frame["rows"] = [r for r in frame["rows"] if not r.get("load_more")]
        frame["rows"].append({"title": "加载更多房间中...", "load_more": True, "dim": True})
        app.refresh()
        try:
            rows, next_cursor = load_rooms(cursor)
        except Exception as e:
            frame["rows"] = [r for r in frame["rows"] if not r.get("load_more")] + [
                {"title": f"加载更多失败: {e}", "load_more": True, "dim": True}
            ]
            app.refresh()
            return
        finally:
            frame["_room_loading"] = False
        existing = [r for r in frame["rows"] if not r.get("load_more")]
        seen = {r.get("id") for r in existing if r.get("id")}
        merged = existing + [r for r in rows if r.get("id") and r["id"] not in seen]
        frame["rows"] = merged
        frame["_room_cursor"] = next_cursor if next_cursor and next_cursor != cursor else ""
        frame["title"] = f"活跃房间 ({len(merged)})"
        if frame["_room_cursor"]:
            frame["rows"].append({"title": "▼ 加载更多房间 (PgDn / Enter)", "load_more": True, "dim": True})
        app.refresh()

    def push_rooms():
        rows, cursor = load_rooms("")
        frame = {
            "type": "list",
            "title": f"活动房间 ({len(rows)})",
            "rows": rows,
            "hint": "Enter=加入并实时转写  PgDn/到底=加载更多  Esc=返回",
            "on_enter": lambda r: (load_more_rooms(frame) if r.get("load_more")
                                   else join_and_show(r["obj"])),
            "_room_cursor": cursor or "",
            "_room_loading": False,
            "on_page_end": lambda f: load_more_rooms(f),
        }
        if cursor:
            frame["rows"].append({"title": "▼ 加载更多房间 (PgDn / Enter)", "load_more": True, "dim": True})
        app.push(frame)

    def join_and_show(ch):
        # get fresh join credentials (token) then start audio
        # 音乐房 join 响应的 topic 是当前曲目;友好房名一律以 feed 为准
        privacy = ch.get("privacy_settings") or {}
        friendly = (
            privacy.get("display_text")
            or (ch.get("social_club") or {}).get("name")
            or ch.get("channel_name")
            or ch.get("topic")
            or friendly_room_name(ch.get("channel") or "")
            or ""
        ).strip()
        # 进房 API 失败时也要有稳定的标题，不能依赖成功分支才赋值。
        app._room_name = friendly or ch["channel"]
        app._audio_started = False
        body = {"channel": ch["channel"], "logging_context": {
            "batch_id": None, "channel_id": None, "feed_rank": None, "is_explore": None,
            "lisener_ids": None, "reasons": None, "source": -1, "speaker_ids": None}}
        d = api("/join_channel", body)
        token = d.get("token") or ""
        ok = d.get("success", True)
        detail = []
        if ok and token:
            try:
                audio.start(ch["channel"], token, int(USER_ID) if USER_ID else 0)
            except Exception as e:
                leave_room(ch["channel"])
                detail = [("error", f"audio bridge start failed: {e}")]
            else:
                chd = d.get("channel", {}) or {}
                if isinstance(chd, str):
                    chd = {"channel": chd}
                # 房间列表的 topic 是友善房间名;join 响应有时不带,按优先级取第一个非空值
                app._room_name = friendly or chd.get("topic") or d.get("topic") or ""
                app._audio_started = True
                clubdeck_config.save_last_room(ch["channel"], app._room_name)
                app._whisper_model = None
                app._transcribing = False
                app._auto_transcribe = CH_AUTO_TRANSCRIBE
                app._last_transcribe_at = time.time()
                app._transcribe_stall_logged_at = 0.0
                detail = [
                    ("topic", app._room_name or "(none)"),
                    ("channel", chd.get("channel") or d.get("channel")),
                    ("session", "房间保活正常"),
                    ("audio", "已连接 · 实时转写"),
                    ("transcribe", f"自动转写: {'开' if app._auto_transcribe else '关'} (每 {CH_TRANSCRIBE_SECONDS}s, 模型 {CH_WHISPER_MODEL})"),
                ]
        else:
            detail = [("error", d.get("error_message") or "join failed (no token)")]
        app.push({"type": "kv", "title": "聆听模式 — " + (app._room_name or ch["channel"]), "rows": detail,
                  "hint": "T=切换实时转写 L=离开 ↑↓=滚动 Esc=返回",
                  "on_back": None, "room_channel": ch["channel"], "info_top": 0})
        room_frame = app.cur

        room_session = None
        if getattr(app, "_audio_started", False):
            room_session = RoomSessionManager(
                channel=ch["channel"],
                user_id=str(USER_ID or ""),
                api_call=api,
                audio=audio,
                join_body=lambda channel: {
                    "channel": channel,
                    "logging_context": {
                        "batch_id": None,
                        "channel_id": None,
                        "feed_rank": None,
                        "is_explore": None,
                        "lisener_ids": None,
                        "reasons": None,
                        "source": -1,
                        "speaker_ids": None,
                    },
                },
                log=_log_crash,
            )
            room_session.mark_joined()
            room_frame["rows"] = [
                (key, room_session.status if key == "session" else value)
                for key, value in room_frame["rows"]
            ]
        app._room_session = room_session

        def leave_and_back():
            app._auto_transcribe = False
            # Disable the watchdog before stopping the bridge.  Previously its
            # background loop could observe the intentional stop and report it
            # as an unexpected dropout while the API leave was still running.
            app._audio_started = False
            if room_session is not None:
                room_session.stop()
            audio.stop(reason=f"user-leave:{ch['channel']}")
            leave_room(ch["channel"])
            app.pop()
        app.cur["on_back"] = leave_and_back
        app.cur["on_leave"] = leave_and_back

        def set_transcribe(frame, status, lines=None):
            """原地替换当前房间的转写区块；不写入历史记录。"""
            with app._render_lock:
                if lines is None:
                    lines = [v for k, v in frame["rows"] if str(k) == "   "]
                rows = [r for r in frame["rows"] if r[0] not in ("transcribe", "   ")]
                block = [("transcribe", status)] + [("   ", fit(ln, w_col() - 2)) for ln in lines]
                rows.extend(block)
                frame["rows"] = rows

        # Capture one temporary room-audio segment through Agora, then transcribe it.
        import threading as _th

        def do_transcribe(seconds=15):
            """Transcribe one temporary room-audio segment; discard its WAV after use."""
            frame = app.cur
            recording_started = False
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                set_transcribe(frame, f"缺少依赖: {e.name} (pip install faster-whisper)")
                if app.cur is frame:
                    app.refresh()
                return
            set_transcribe(frame, f"自动转写中: 录音 {seconds}s (模型 {CH_WHISPER_MODEL})...")
            if app.cur is frame:
                room_frame["_last_refresh"] = time.time()
                app.refresh()
            rec_start = time.time()
            try:
                with tempfile.TemporaryDirectory(prefix="clubdeck-transcribe-") as temp_dir:
                    wav = os.path.join(temp_dir, "room-audio.wav")
                    if not audio.start_recording(wav, position=2):
                        raise RuntimeError("audio bridge is not active")
                    recording_started = True
                    time.sleep(seconds)
                    audio.stop_recording()
                    recording_started = False
                    time.sleep(0.5)
                    if not os.path.isfile(wav) or os.path.getsize(wav) <= 44:
                        raise RuntimeError("Agora did not produce a valid WAV recording")
                    model = app._whisper_model
                    if model is None:
                        model = WhisperModel(CH_WHISPER_MODEL, device="cpu", compute_type="int8")
                        app._whisper_model = model
                    history = list(getattr(audio, "volume_history", []))
                    fallback_active = CH_WHISPER_VAD and has_volume_activity(
                        history, rec_start, rec_start + seconds, self_uid=USER_ID
                    )
                    segs = transcribe_with_vad(
                        model,
                        wav,
                        vad=CH_WHISPER_VAD,
                        fallback_on_activity=fallback_active,
                    )
                    lines_out = []
                    for s in segs:
                        text = hanzi.to_simplified(s.text.strip())
                        if text:
                            lines_out.append(f"- {text}")
                    if lines_out:
                        set_transcribe(
                            frame,
                            f"--- 实时转写 (模型 {CH_WHISPER_MODEL}) ---",
                            lines_out[-CH_TRANSCRIBE_MAX_LINES:],
                        )
                    else:
                        set_transcribe(frame, "(无语音内容)")
            except Exception as e:
                set_transcribe(frame, f"转写失败: {e}")
            finally:
                if recording_started:
                    audio.stop_recording()
            if app.cur is frame:
                app.refresh()

        def on_transcribe():
            """Toggle auto transcription (T key)."""
            app._auto_transcribe = not getattr(app, "_auto_transcribe", True)
            state = "开" if app._auto_transcribe else "关"
            with app._render_lock:
                rows = []
                replaced = False
                for k, v in app.cur["rows"]:
                    if str(k) == "transcribe" and not replaced:
                        rows.append(("transcribe", f"自动转写已{state} (每 {CH_TRANSCRIBE_SECONDS}s 一段)"))
                        replaced = True
                    else:
                        rows.append((k, v))
                app.cur["rows"] = rows
            app.refresh()

        app.cur["on_transcribe"] = on_transcribe

        def auto_loop():
            while True:
                if app.cur is not room_frame or not room_frame.get("room_channel"):
                    return
                app._last_transcribe_at = time.time()
                if not getattr(app, "_auto_transcribe", False):
                    time.sleep(1)
                    continue
                if getattr(app, "_transcribing", False):
                    time.sleep(1)
                    continue
                app._transcribing = True
                try:
                    do_transcribe(CH_TRANSCRIBE_SECONDS)
                except Exception as exc:
                    # 转写线程不允许静默死亡：出错也写进状态行并继续下一段
                    try:
                        set_transcribe(room_frame, f"转写线程异常: {exc}")
                    except Exception:
                        pass
                    if app.cur is room_frame:
                        try:
                            app.refresh()
                        except Exception:
                            pass
                finally:
                    app._transcribing = False
                time.sleep(1)

        if CH_AUTO_TRANSCRIBE:
            _th.Thread(target=auto_loop, daemon=True).start()

        def pump_volume():
            """Drain bridge events and refresh the volume/audio-error overlay without waiting for a keypress."""
            if not getattr(app, "_audio_started", False):
                return
            if not audio.active:
                code = getattr(audio, "last_exit_code", None)
                if not room_frame.get("_audio_error_logged"):
                    room_frame["_audio_error_logged"] = True
                    _log_crash(
                        f"音频桥断开 room={ch['channel']} exit_code={code} "
                        f"last_error={getattr(audio, 'last_error', '') or '-'}"
                    )
                with app._render_lock:
                    suffix = f" code={code}" if code is not None else ""
                    room_frame["_audio_error"] = f"音频已断开（桥进程退出{suffix}），按 L 离开后重新进入"
                    room_frame.pop("_volume", None)
                return
            audio.read_events()
        app._pump_volume = pump_volume

        def ensure_room_session():
            """保活 Clubhouse 会话，并统一恢复服务端失效或音频桥退出。"""
            if not getattr(app, "_audio_started", False) or room_session is None:
                return
            room_session.tick()
            with app._render_lock:
                rows = []
                for key, value in room_frame["rows"]:
                    rows.append((key, room_session.status if key == "session" else value))
                room_frame["rows"] = rows
                if room_session.recovery_reason:
                    room_frame["_audio_error"] = room_session.status
                elif audio.active:
                    room_frame.pop("_audio_error", None)

        def room_loop():
            while app.cur is room_frame and room_frame.get("room_channel"):
                ensure_room_session()
                if getattr(app, "_auto_transcribe", False):
                    now = time.time()
                    stale = now - getattr(app, "_last_transcribe_at", now)
                    threshold = max(120.0, 3.0 * float(CH_TRANSCRIBE_SECONDS))
                    if stale > threshold and now - getattr(app, "_transcribe_stall_logged_at", 0.0) > 600.0:
                        app._transcribe_stall_logged_at = now
                        _log_crash(f"转写循环可能卡死: {int(stale)}s 无心跳 room={ch['channel']}")
                time.sleep(1.0)

        if room_session is not None:
            _th.Thread(target=room_loop, daemon=True).start()

    # main menu
    def join_last_room():
        last = clubdeck_config.load_last_room()
        if not last:
            return
        topic = friendly_room_name(str(last["channel"])) or str(last.get("room_name") or "")
        join_and_show({"channel": last["channel"], "topic": topic})

    menu = []
    _last = clubdeck_config.load_last_room()
    if _last:
        menu.append({
            "title": f"上次房间: {_last.get('room_name') or _last['channel']}",
            "sub": f"channel={_last['channel']} · 实时转写",
            "fn": join_last_room,
        })
    menu += [
        {"title": "活动房间", "sub": "手动选择房间并开始转写", "fn": push_rooms},
        {"title": "设置", "sub": "转写模型与登录凭证", "fn": push_settings},
        {"title": "退出", "sub": "", "fn": lambda: setattr(app, "running", False)},
    ]
    menu_rows = [{"title": m["title"], "sub": m["sub"], "obj": m} for m in menu]
    app.push({"type": "list", "title": "主菜单", "rows": menu_rows,
              "on_enter": lambda r: r["obj"]["fn"]()})
    return app


def countdown_to_main(seconds: int = 5) -> None:
    """自检结果停留：不按键时 N 秒后自动进入主菜单；
    按任意键停止倒计时、停留在自检结果，再按一次任意键才进入主菜单。"""
    import msvcrt

    deadline = time.time() + seconds
    stopped = False
    try:
        while True:
            if msvcrt.kbhit():
                try:
                    msvcrt.getwch()
                except Exception:
                    msvcrt.getch()
                if not stopped:
                    stopped = True
                    sys.stdout.write("\r已停止倒计时，停留在自检结果。再按任意键进入主菜单...     ")
                    sys.stdout.flush()
                    continue
                break
            if stopped:
                time.sleep(0.2)
                continue
            if time.time() >= deadline:
                break
            remaining = max(1, int(deadline - time.time() + 0.999))
            sys.stdout.write(f"\r自检结果已保留在上方。按任意键停止倒计时（{remaining} 秒后自动进入主菜单）...")
            sys.stdout.flush()
            time.sleep(0.2)
    finally:
        sys.stdout.write("\n")
        sys.stdout.flush()


def main():
    global TOKEN, USER_ID
    _log_crash("TUI 启动")

    def _excepthook(exc_type, exc, tb):
        import traceback

        _log_crash("未捕获异常:\n" + "".join(traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = _excepthook
    enable_vt()
    # 实时转写通过 Agora 音频桥采集房间混音，不依赖虚拟声卡。
    account_name = ""
    if "--account" in sys.argv:
        idx = sys.argv.index("--account")
        if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("-"):
            account_name = sys.argv[idx + 1]
    clubdeck_config.ensure_file()
    clubdeck_config.ensure_model_cache()
    from services import accounts

    try:
        _token, _uid, _profile = accounts.select_account(arg=account_name, interactive=True)
    except accounts.AccountError as exc:
        print(f"账户选择失败：{exc}")
        sys.exit(1)
    TOKEN, USER_ID = configure_credentials(_token, _uid, profile=_profile)
    if not TOKEN or not USER_ID:
        print("未找到 token:设置环境变量 CH_TOKEN 和 CH_USER_ID,或让 Clubdeck 保持登录。")
        sys.exit(1)
    if CH_STARTUP_SELF_CHECK:
        import self_check
        print("启动自检中（转写依赖 / Agora 音频桥）...", flush=True)
        self_check.ensure_requirements()
        self_check.run_self_check(None, full=True, print_progress=True)
        countdown_to_main()
    enter_alt()
    try:
        hide_cursor()
        app = make_app()
        app.refresh()
        last_vol_refresh = 0
        while app.running:
            key = read_key()
            app.handle(key)
            app.refresh()
            # pump audio bridge events when in the room view
            f = app.cur
            if f and f.get("room_channel"):
                import time as _t
                now = _t.time()
                if now - last_vol_refresh > 0.5:
                    last_vol_refresh = now
                    pump = getattr(app, "_pump_volume", None)
                    if pump:
                        pump()
                    if app.cur is f:
                        f["_last_refresh"] = time.time()
                        app.refresh()
    except KeyboardInterrupt:
        _log_crash("退出: Ctrl+C")
        pass
    finally:
        # stop audio bridge if running
        try:
            audio = getattr(app, "_audio", None)
            if audio:
                audio.stop(reason="tui-shutdown")
        except Exception:
            pass
        show_cursor()
        leave_alt()
        cls()
        _log_crash("TUI 退出")
        print("bye")


if __name__ == "__main__":
    main()
