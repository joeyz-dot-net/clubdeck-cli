# -*- coding: utf-8 -*-
"""Clubhouse 房间会话保活与恢复。

Agora 桥进程存活不代表 Clubhouse 服务端仍把账号留在房间。房间客户端必须
定期调用 ``/active_ping``；连续失败或桥进程退出时，本管理器串行执行完整重进房。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable


def _env_float(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


ACTIVE_PING_INTERVAL_SECONDS = _env_float(
    "CLUB_ROOM_ACTIVE_PING_SECONDS", 20.0, 5.0
)
ACTIVE_PING_FAILURE_LIMIT = _env_int(
    "CLUB_ROOM_ACTIVE_PING_FAILURES", 2, 1
)


def response_error(response: Any) -> str:
    """返回 Clubhouse API 响应里的错误；空字典视为成功响应。"""
    if response is None:
        return "no response"
    if not isinstance(response, dict):
        return f"invalid response: {type(response).__name__}"
    message = str(response.get("error_message") or "").strip()
    if response.get("success") is False:
        return message or "request failed"
    return message


class RoomSessionManager:
    """维护一个已加入的 Clubhouse + Agora 房间会话。

    ``tick`` 可由任意已有后台循环定期调用。类内部只允许一个恢复过程运行，
    因而保活失败和桥进程退出不会同时触发两次 ``join_channel``。
    """

    def __init__(
        self,
        *,
        channel: str,
        user_id: str,
        api_call: Callable[[str, dict[str, Any]], Any],
        audio: Any,
        join_body: Callable[[str], dict[str, Any]],
        on_joined: Callable[[dict[str, Any]], None] | None = None,
        log: Callable[[str], None] | None = None,
        active_ping_interval: float = ACTIVE_PING_INTERVAL_SECONDS,
        active_ping_failure_limit: int = ACTIVE_PING_FAILURE_LIMIT,
        recovery_base_delay: float = 5.0,
        recovery_max_delay: float = 60.0,
        recovery_cooldown: float = 300.0,
        stop_wait_timeout: float = 35.0,
        clock: Callable[[], float] = time.time,
    ):
        self.channel = str(channel)
        self.user_id = str(user_id or "")
        self.api_call = api_call
        self.audio = audio
        self.join_body = join_body
        self.on_joined = on_joined
        self.log = log or (lambda _line: None)
        self.active_ping_interval = max(5.0, float(active_ping_interval))
        self.active_ping_failure_limit = max(1, int(active_ping_failure_limit))
        self.recovery_base_delay = max(0.0, float(recovery_base_delay))
        self.recovery_max_delay = max(
            self.recovery_base_delay, float(recovery_max_delay)
        )
        self.recovery_cooldown = max(1.0, float(recovery_cooldown))
        self.stop_wait_timeout = max(0.0, float(stop_wait_timeout))
        self.clock = clock

        self.last_active_ping_at = 0.0
        self.last_active_ping_success_at = 0.0
        self.active_ping_successes = 0
        self.last_active_ping_log_at = 0.0
        self.active_ping_failures = 0
        self.recovery_reason = ""
        self.recovery_attempts = 0
        self.next_recovery_at = 0.0
        self.last_rejoin_at = 0.0
        self.rejoin_count = 0
        self.last_error = ""
        self.stopped = False
        self._recovery_lock = threading.RLock()

    def mark_joined(self, now: float | None = None) -> None:
        """记录一次有效进房；首次 active_ping 在一个完整间隔后发送。"""
        now = self.clock() if now is None else float(now)
        self.last_active_ping_at = now
        # join_channel 成功不等于 active_ping 已经通过。分开记录，避免状态页和
        # 长时验收把“刚进房”误报成“保活已验证”。
        self.last_active_ping_success_at = 0.0
        self.active_ping_successes = 0
        self.last_active_ping_log_at = 0.0
        self.active_ping_failures = 0
        self.recovery_reason = ""
        self.recovery_attempts = 0
        self.next_recovery_at = 0.0
        self.last_error = ""

    def stop(self) -> None:
        """禁止后续保活，并等待正在进行的重连完成清理。

        音频桥启动最多会等待 JOINED 约 10 秒。若用户恰在这段时间离房，必须先
        让恢复线程看到 ``stopped`` 并回收它刚启动的桥，再由调用方做最终 leave；
        否则可能在界面退出后留下一个重新进房的孤儿进程。
        """
        if self.stopped:
            return
        self.stopped = True
        self.recovery_reason = ""
        acquired = self._recovery_lock.acquire(timeout=self.stop_wait_timeout)
        if acquired:
            self._recovery_lock.release()
        else:
            self.log(
                f"[session] 等待自动重连停止超时({self.stop_wait_timeout:g}s)，"
                "调用方将继续执行最终清理"
            )

    @property
    def healthy(self) -> bool:
        return (
            not self.stopped
            and not self.recovery_reason
            and self.active_ping_failures == 0
            and bool(getattr(self.audio, "active", False))
        )

    @property
    def status(self) -> str:
        if self.stopped:
            return "房间会话已停止"
        if self.recovery_reason:
            return f"房间待重连（{self.recovery_reason}）"
        if self.active_ping_failures:
            return f"房间保活失败 {self.active_ping_failures} 次"
        if self.last_active_ping_success_at:
            return "房间保活正常"
        return "等待房间保活"

    def request_recovery(
        self,
        reason: str,
        *,
        now: float | None = None,
        immediate: bool = False,
    ) -> None:
        if self.stopped:
            return
        now = self.clock() if now is None else float(now)
        if not self.recovery_reason:
            self.recovery_reason = str(reason or "room session unhealthy")
            delay = 0.0 if immediate else self.recovery_base_delay
            self.next_recovery_at = now + delay
            self.log(f"[session] 检测到{self.recovery_reason}，准备自动重进房 {self.channel}")
        elif immediate:
            self.next_recovery_at = min(self.next_recovery_at, now)

    def _record_ping_failure(self, error: str, now: float) -> None:
        self.active_ping_failures += 1
        self.last_error = error
        self.log(
            f"[session] active_ping 失败({self.active_ping_failures}/"
            f"{self.active_ping_failure_limit}): {error}"
        )
        invalid_session = "invalid request" in error.lower()
        if invalid_session or self.active_ping_failures >= self.active_ping_failure_limit:
            self.request_recovery(
                f"房间保活失效: {error}", now=now, immediate=True
            )

    def _active_ping(self, now: float) -> bool:
        self.last_active_ping_at = now
        try:
            response = self.api_call("/active_ping", {"channel": self.channel})
            error = response_error(response)
        except Exception as exc:
            error = str(exc) or type(exc).__name__
        if error:
            self._record_ping_failure(error, now)
            return False
        if self.stopped:
            return False

        self.last_active_ping_success_at = now
        self.active_ping_successes += 1
        self.active_ping_failures = 0
        self.last_error = ""
        if (
            self.active_ping_successes == 1
            or now - self.last_active_ping_log_at >= 300.0
        ):
            self.last_active_ping_log_at = now
            self.log(
                f"[session] active_ping 成功 {self.channel} "
                f"(本次会话 {self.active_ping_successes} 次)"
            )
        if self.recovery_reason.startswith("房间保活失效"):
            self.recovery_reason = ""
            self.next_recovery_at = 0.0
        return True

    def _recover(self, now: float) -> bool:
        if self.stopped or not self.recovery_reason:
            return False
        if not self._recovery_lock.acquire(blocking=False):
            return False
        reason = self.recovery_reason
        try:
            if self.stopped:
                return False
            self.log(f"[session] 开始自动重进房 {self.channel}: {reason}")
            try:
                if bool(getattr(self.audio, "active", False)):
                    self.audio.stop(reason=f"room-recovery:{reason}")
            except Exception as exc:
                self.log(f"[session] 停止旧音频桥失败，继续重连: {exc}")

            try:
                self.api_call("/leave_channel", {"channel": self.channel})
            except Exception:
                pass
            if self.stopped:
                return False

            joined = self.api_call("/join_channel", self.join_body(self.channel))
            error = response_error(joined)
            token = joined.get("token") if isinstance(joined, dict) else ""
            if error or not token:
                raise RuntimeError(error or "join_channel 未返回 token")
            if self.stopped:
                try:
                    self.api_call("/leave_channel", {"channel": self.channel})
                except Exception:
                    pass
                return False

            uid = int(self.user_id) if self.user_id else 0
            self.audio.start(self.channel, token, uid)
            if self.stopped:
                try:
                    self.audio.stop(reason=f"session-stop-during-recovery:{self.channel}")
                finally:
                    try:
                        self.api_call("/leave_channel", {"channel": self.channel})
                    except Exception:
                        pass
                return False
            if self.on_joined is not None:
                try:
                    self.on_joined(joined)
                except Exception as exc:
                    self.log(f"[session] 重进房后状态刷新失败: {exc}")
            self.last_rejoin_at = now
            self.rejoin_count += 1
            self.mark_joined(now)
            self.log(f"[session] 自动重进房成功 {self.channel}")
            return True
        except Exception as exc:
            self.recovery_attempts += 1
            self.last_error = str(exc)
            if self.recovery_attempts >= 8:
                delay = self.recovery_cooldown
                self.recovery_attempts = 0
                self.log(
                    f"[session] 自动重连连续失败过多，{int(delay)}s 后继续尝试: {exc}"
                )
            else:
                delay = min(
                    self.recovery_max_delay,
                    self.recovery_base_delay * (2 ** max(0, self.recovery_attempts - 1)),
                )
                self.log(
                    f"[session] 自动重连失败({self.recovery_attempts}): {exc}；"
                    f"{int(delay)}s 后重试"
                )
            self.next_recovery_at = now + delay
            return False
        finally:
            self._recovery_lock.release()

    def refresh_stage_token(self) -> bool:
        """上台后刷新 Agora token（listener -> speaker）并只重启桥，不整房 leave/rejoin。"""
        if self.stopped:
            return False
        try:
            joined = self.api_call("/join_channel", self.join_body(self.channel))
            error = response_error(joined)
            token = joined.get("token") if isinstance(joined, dict) else ""
            if error or not token:
                raise RuntimeError(error or "join_channel 未返回 token")
            if self.stopped:
                return False
            uid = int(self.user_id) if self.user_id else 0
            self.audio.start(self.channel, token, uid)
            if self.on_joined is not None:
                try:
                    self.on_joined(joined)
                except Exception as exc:
                    self.log(f"[session] 上台后状态刷新失败: {exc}")
            self.log(f"[session] 上台后已刷新 Agora token 并重启桥 {self.channel}")
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.log(f"[session] 上台后 token 刷新失败: {exc}")
            return False

    def tick(self, now: float | None = None) -> bool:
        """执行一次健康检查；返回本次是否完成了自动重进房。"""
        if self.stopped:
            return False
        now = self.clock() if now is None else float(now)
        if not bool(getattr(self.audio, "active", False)):
            self.request_recovery("音频桥进程已退出", now=now, immediate=False)
        elif now - self.last_active_ping_at >= self.active_ping_interval:
            self._active_ping(now)

        if (
            self.recovery_reason
            and now >= self.next_recovery_at
            and not self.stopped
        ):
            return self._recover(now)
        return False
