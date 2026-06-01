from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from loguru import logger

try:
    from config import RISK_CONTROL
except Exception:
    RISK_CONTROL = {}


DEFAULT_RISK_CONTROL = {
    "safe_mode_enabled": True,
    "global_account_min_interval_seconds": 12,
    "task_cooldown_seconds": 180,
    "high_risk_cooldown_seconds": 1800,
    "max_consecutive_risk_failures": 2,
    "auto_pause_on_session_expired": True,
    "auto_pause_on_verify_required": True,
    "auto_rate_batch_limit": 1,
    "auto_red_flower_batch_limit": 1,
    "auto_task_interval_seconds": 900,
    "auto_order_action_delay_min_seconds": 15,
    "auto_order_action_delay_max_seconds": 45,
    "item_polish_delay_min_seconds": 20,
    "item_polish_delay_max_seconds": 60,
    "publish_action_delay_min_seconds": 30,
    "publish_action_delay_max_seconds": 90,
}

HIGH_RISK_KEYWORDS = (
    "session过期",
    "session expired",
    "令牌过期",
    "token",
    "401",
    "403",
    "需要验证",
    "validate",
    "captcha",
    "punish",
    "rgv587",
    "风控",
    "被挤爆",
)


@dataclass
class RiskDecision:
    allowed: bool
    status: str = "allowed"
    reason: str = ""
    wait_seconds: float = 0.0


class RiskGovernor:
    def __init__(
        self,
        db: Any,
        config: Optional[Dict[str, Any]] = None,
        *,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], Any] = asyncio.sleep,
    ):
        merged = dict(DEFAULT_RISK_CONTROL)
        merged.update(RISK_CONTROL or {})
        merged.update(config or {})
        self.config = merged
        self.db = db
        self.clock = clock
        self.sleeper = sleeper
        self._locks: Dict[str, asyncio.Lock] = {}

    def _enabled(self) -> bool:
        value = self.config.get("safe_mode_enabled", True)
        return str(value).lower() not in {"0", "false", "no", "off"}

    def _state(self, cookie_id: str) -> Dict[str, Any]:
        raw = self.db.get_account_risk_state(cookie_id) or {}
        next_allowed = raw.get("next_allowed_at") or {}
        if isinstance(next_allowed, str):
            try:
                next_allowed = json.loads(next_allowed) if next_allowed else {}
            except Exception:
                next_allowed = {}
        raw["next_allowed_at"] = next_allowed if isinstance(next_allowed, dict) else {}
        return raw

    def _is_high_risk(self, reason: Any) -> bool:
        text = str(reason or "").lower()
        return any(keyword.lower() in text for keyword in HIGH_RISK_KEYWORDS)

    def _task_delay_range(self, task_type: str) -> tuple[float, float]:
        if task_type == "item_polish":
            return (
                float(self.config.get("item_polish_delay_min_seconds", 20) or 20),
                float(self.config.get("item_polish_delay_max_seconds", 60) or 60),
            )
        if task_type == "product_publish":
            return (
                float(self.config.get("publish_action_delay_min_seconds", 30) or 30),
                float(self.config.get("publish_action_delay_max_seconds", 90) or 90),
            )
        return (
            float(self.config.get("auto_order_action_delay_min_seconds", 15) or 15),
            float(self.config.get("auto_order_action_delay_max_seconds", 45) or 45),
        )

    def jitter_delay(self, task_type: str) -> float:
        low, high = self._task_delay_range(task_type)
        if high < low:
            high = low
        return random.uniform(low, high)

    def check(self, cookie_id: str, task_type: str) -> RiskDecision:
        if not self._enabled():
            return RiskDecision(True)
        cookie_id = str(cookie_id or "").strip()
        if not cookie_id:
            return RiskDecision(False, "blocked", "missing cookie_id")

        now = self.clock()
        state = self._state(cookie_id)
        if int(state.get("paused") or 0):
            return RiskDecision(False, "paused", state.get("pause_reason") or "account paused")

        next_allowed = state.get("next_allowed_at") or {}
        allowed_at = float(next_allowed.get(task_type) or 0)
        if allowed_at > now:
            return RiskDecision(False, "cooldown", "task cooldown", allowed_at - now)

        return RiskDecision(True)

    async def wait_before_action(self, cookie_id: str, task_type: str) -> RiskDecision:
        decision = self.check(cookie_id, task_type)
        if not decision.allowed:
            return decision

        lock = self._locks.setdefault(str(cookie_id), asyncio.Lock())
        async with lock:
            state = self._state(cookie_id)
            now = self.clock()
            min_interval = float(self.config.get("global_account_min_interval_seconds", 12) or 0)
            last_action_at = float(state.get("last_action_at") or 0)
            wait_seconds = max(0.0, last_action_at + min_interval - now)
            if wait_seconds > 0:
                logger.info(f"风控限速：账号 {cookie_id} 任务 {task_type} 等待 {wait_seconds:.1f}s")
                await self.sleeper(wait_seconds)
                now = self.clock()
            self.db.upsert_account_risk_state(cookie_id, last_action_at=now, updated_at=now)
        return RiskDecision(True)

    def record_success(self, cookie_id: str, task_type: str) -> None:
        if not self._enabled():
            return
        state = self._state(cookie_id)
        next_allowed = dict(state.get("next_allowed_at") or {})
        next_allowed.pop(task_type, None)
        self.db.upsert_account_risk_state(
            cookie_id,
            consecutive_failures=0,
            last_success_at=self.clock(),
            next_allowed_at=next_allowed,
            updated_at=self.clock(),
        )

    def record_failure(self, cookie_id: str, task_type: str, reason: Any) -> Dict[str, Any]:
        if not self._enabled():
            return {"paused": False}
        now = self.clock()
        state = self._state(cookie_id)
        failures = int(state.get("consecutive_failures") or 0) + 1
        high_risk = self._is_high_risk(reason)
        threshold = max(1, int(self.config.get("max_consecutive_risk_failures", 2) or 2))
        should_pause = failures >= threshold
        reason_text = str(reason or "unknown risk failure")
        lower_reason = reason_text.lower()
        if self.config.get("auto_pause_on_session_expired", True) and ("session过期" in lower_reason or "session expired" in lower_reason):
            should_pause = True
        if self.config.get("auto_pause_on_verify_required", True) and any(word in lower_reason for word in ("需要验证", "captcha", "validate", "punish")):
            should_pause = True

        cooldown = float(
            self.config.get("high_risk_cooldown_seconds" if high_risk else "task_cooldown_seconds", 180) or 180
        )
        next_allowed = dict(state.get("next_allowed_at") or {})
        next_allowed[task_type] = now + cooldown

        updates = {
            "consecutive_failures": failures,
            "last_failure_at": now,
            "last_failure_reason": reason_text[:500],
            "next_allowed_at": next_allowed,
            "updated_at": now,
        }
        if should_pause:
            updates.update({
                "paused": 1,
                "pause_reason": reason_text[:500],
                "paused_at": now,
            })
        self.db.upsert_account_risk_state(cookie_id, **updates)

        try:
            self.db.add_risk_control_log(
                cookie_id,
                event_type="account_risk_guard",
                trigger_scene=task_type,
                result_code="account_paused" if should_pause else "task_cooldown",
                event_description=f"风控保护：{reason_text[:200]}",
                processing_status="paused" if should_pause else "cooldown",
                processing_result="账号自动化已暂停" if should_pause else "任务已进入冷却",
                error_message=reason_text[:500],
            )
        except Exception as exc:
            logger.debug(f"记录风控日志失败: {exc}")

        if should_pause:
            logger.warning(f"风控保护：账号 {cookie_id} 已暂停自动化，原因：{reason_text}")
        return {"paused": should_pause, "consecutive_failures": failures, "cooldown_seconds": cooldown}

    def resume_account(self, cookie_id: str, operator: str = "manual") -> bool:
        now = self.clock()
        result = self.db.upsert_account_risk_state(
            cookie_id,
            paused=0,
            pause_reason="",
            paused_at=None,
            consecutive_failures=0,
            last_failure_reason="",
            next_allowed_at={},
            updated_at=now,
        )
        try:
            self.db.add_risk_control_log(
                cookie_id,
                event_type="account_risk_guard",
                trigger_scene="manual_resume",
                result_code="manual_resume",
                event_description=f"用户手动恢复账号自动化: {operator}",
                processing_status="success",
                processing_result="账号风控暂停已解除",
            )
        except Exception as exc:
            logger.debug(f"记录恢复风控日志失败: {exc}")
        return bool(result)


def create_risk_governor(db: Any, config: Optional[Dict[str, Any]] = None) -> RiskGovernor:
    return RiskGovernor(db, config=config)
