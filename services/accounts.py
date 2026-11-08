"""多账户登录：保存/导入/选择多个 Clubdeck 账户 profile。

每个账户 = 一份完整 Clubdeck profile 快照（token、userId、deviceId 及设备指纹）。
存储于 DATA_DIR/accounts/<name>.json（含敏感 token，ACL 收紧 + gitignore）；
active 标记记在 DATA_DIR/accounts/active.json。

选择优先级（select_account）：
  1. 显式 arg（--account）→ 2. env[CH_ACCOUNT] → 3. 无账户时走旧兜底
  → 4. 恰一个账户 → 5. active 有效 → 6. 交互菜单 → 7. 抛 AccountError。

账户切换只换 profile 快照喂给 chcli_tui.configure_credentials(..., profile=快照)，
设备指纹绑定不串号（复用其「不混合账户」不变量）。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import clubdeck_config


class AccountError(RuntimeError):
    """账户选择/导入/管理错误。"""


# 账户目录与 active 标记（跟随 clubdeck_config.DATA_DIR）
def accounts_dir() -> Path:
    base = Path(clubdeck_config.DATA_DIR)
    return base / "accounts"


def _active_file() -> Path:
    return accounts_dir() / "active.json"


_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def sanitize_name(name: str) -> str:
    """账户名仅允许 [A-Za-z0-9_-]（作文件名，防路径穿越）。"""
    text = str(name or "").strip()
    if not _NAME_RE.match(text):
        raise AccountError(f"非法账户名：{text!r}（只允许字母/数字/下划线/连字符）")
    return text


def _restrict(path: Path) -> None:
    """账户快照含 token，收紧 ACL（Windows 当前用户；POSIX chmod 600）。"""
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


def _profile_path(name: str) -> Path:
    return accounts_dir() / f"{sanitize_name(name)}.json"


def list_accounts() -> list[dict[str, Any]]:
    """返回账户摘要列表（不含 token）。"""
    d = accounts_dir()
    out: list[dict[str, Any]] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        if p.name == "active.json":
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        name = p.stem
        out.append({
            "name": name,
            "user_id": str(data.get("userId") or ""),
            "device_id": str(data.get("deviceId") or data.get("device_id") or ""),
            "has_token": bool(str(data.get("token") or "").strip()),
        })
    return out


def load_account(name: str) -> dict[str, Any] | None:
    """读一个账户的完整 profile 快照；不存在/损坏返回 None。"""
    p = _profile_path(name)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_account(name: str, profile: dict[str, Any]) -> Path:
    """原子写入一个账户快照（tmp+replace），并收紧 ACL。"""
    name = sanitize_name(name)
    if not isinstance(profile, dict):
        raise AccountError("账户 profile 必须是 dict")
    d = accounts_dir()
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{name}.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    _restrict(target)
    return target


def import_account(name: str, profile: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """从实时 Clubdeck profile.json（或传入的 profile）导入一份账户快照。

    返回 (name, 摘要)。校验必须含 token + userId，缺失则报 AccountError。
    """
    name = sanitize_name(name)
    if profile is None:
        import chcli_tui

        profile = chcli_tui.load_clubdeck_profile()
    if not isinstance(profile, dict):
        raise AccountError("未取得 Clubdeck profile，请先在 Clubdeck 登录该账户")
    token = str(profile.get("token") or "").strip()
    user_id = str(profile.get("userId") or "").strip()
    if not token or not user_id:
        raise AccountError("Clubdeck profile 缺 token/userId，请先在 Clubdeck 登录该账户")
    save_account(name, profile)
    return name, {
        "name": name,
        "user_id": user_id,
        "device_id": str(profile.get("deviceId") or profile.get("device_id") or ""),
        "has_token": bool(token),
    }


def remove_account(name: str) -> bool:
    """删除账户；若为 active 则一并清除。"""
    name = sanitize_name(name)
    p = _profile_path(name)
    existed = p.is_file()
    if existed:
        try:
            p.unlink()
        except OSError:
            return False
    if get_active_account() == name:
        _clear_active()
    return existed


def get_active_account() -> str | None:
    try:
        data = json.loads(_active_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = str(data.get("name") or "").strip() if isinstance(data, dict) else ""
    return name or None


def _clear_active() -> None:
    try:
        _active_file().unlink(missing_ok=True)
    except OSError:
        pass


def set_active_account(name: str) -> None:
    name = sanitize_name(name)
    if load_account(name) is None:
        raise AccountError(f"账户不存在：{name}")
    d = accounts_dir()
    d.mkdir(parents=True, exist_ok=True)
    target = _active_file()
    tmp = target.with_name("active.json.tmp")
    tmp.write_text(json.dumps({"name": name}, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(target)


def _legacy_fallback(env: dict[str, str]) -> tuple[str, str, dict[str, Any]]:
    """无任何已存账户时走旧兜底：环境变量 → secrets → 实时 profile。"""
    token = str(env.get("CH_TOKEN") or clubdeck_config.get_secret("token") or "").strip()
    user_id = str(env.get("CH_USER_ID") or clubdeck_config.get_secret("user_id") or "").strip()
    import chcli_tui

    profile = chcli_tui.load_clubdeck_profile()
    return token, user_id, profile


def _pick_interactive(names: list[dict[str, Any]]) -> str:
    """交互终端上弹编号菜单选择账户。"""
    print("\n请选择登录账户：")
    for i, acc in enumerate(names, 1):
        uid = acc.get("user_id") or "-"
        dev = acc.get("device_id") or "-"
        cur = acc.get("name")
        active = get_active_account()
        mark = "  (当前)" if active == cur else ""
        print(f"  {i}. {cur}{mark}  userId={uid}  deviceId={dev}")
    while True:
        try:
            raw = input("输入序号（回车用当前默认）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise AccountError("已取消选择账户") from None
        if not raw:
            active = get_active_account()
            if active:
                return active
            print("无默认账户，请输入序号。")
            continue
        try:
            idx = int(raw)
        except ValueError:
            print("请输入数字序号。")
            continue
        if 1 <= idx <= len(names):
            return names[idx - 1]["name"]
        print(f"序号需在 1..{len(names)} 之间。")


def select_account(
    arg: str = "",
    env: dict[str, str] | None = None,
    interactive: bool = True,
) -> tuple[str, str, dict[str, Any]]:
    """解析本次登录账户，返回 (token, user_id, profile 快照)。

    profile 永远返回完整 dict（实时或快照），供 configure_credentials(..., profile=...)
    正确绑定设备指纹。选中账户缺失时 fail-closed（抛 AccountError）。
    """
    env = os.environ if env is None else env

    # 1) 显式 arg / 2) env 指定
    explicit = str(arg or "").strip() or str(env.get("CH_ACCOUNT") or "").strip()
    if explicit:
        profile = load_account(explicit)
        if profile is None:
            raise AccountError(f"账户不存在：{explicit}（可用账户：{', '.join(a['name'] for a in list_accounts()) or '无'}）")
        return (
            str(profile.get("token") or "").strip(),
            str(profile.get("userId") or "").strip(),
            profile,
        )

    accounts = list_accounts()

    # 3) 无账户 → 旧兜底
    if not accounts:
        return _legacy_fallback(env)

    # 4) 恰一个账户
    if len(accounts) == 1:
        name = accounts[0]["name"]
        profile = load_account(name) or {}
        return (
            str(profile.get("token") or "").strip(),
            str(profile.get("userId") or "").strip(),
            profile,
        )

    # 5) active 有效
    active = get_active_account()
    if active and load_account(active) is not None:
        profile = load_account(active) or {}
        return (
            str(profile.get("token") or "").strip(),
            str(profile.get("userId") or "").strip(),
            profile,
        )

    # 6) 交互菜单
    if interactive and sys.stdin and sys.stdin.isatty():
        name = _pick_interactive(accounts)
        set_active_account(name)
        profile = load_account(name) or {}
        return (
            str(profile.get("token") or "").strip(),
            str(profile.get("userId") or "").strip(),
            profile,
        )

    # 7) fail-closed
    names = ", ".join(a["name"] for a in accounts)
    raise AccountError(
        f"存在多个账户且未指定：{names}。请用 --account <名字> 或 CH_ACCOUNT=<名字> 指定。"
    )
