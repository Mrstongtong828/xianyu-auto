from datetime import datetime
from typing import Any, Callable, Dict, List

try:
    from loguru import logger
except Exception:
    class _FallbackLogger:
        def warning(self, *args, **kwargs):
            return None

    logger = _FallbackLogger()

from utils.time_utils import LOCAL_TIMEZONE


def _mask_summary_error(value: Any) -> str:
    text = str(value or "")
    for marker in ("token", "cookie", "password", "secret"):
        text = text.replace(marker, "***")
        text = text.replace(marker.upper(), "***")
    return text[:200]


def _health_summary_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _health_summary_failure_status(status_value: Any) -> bool:
    normalized = str(status_value or "").strip().lower()
    return normalized in {
        "failed",
        "failure",
        "error",
        "exception",
        "cookie_expired",
        "missing_template",
        "timeout",
        "pending",
        "processing",
        "unresolved",
    }


def _health_summary_task_entry(log: Dict[str, Any], source: str) -> Dict[str, Any]:
    return {
        "source": source,
        "id": log.get("id"),
        "task_type": log.get("task_type") or log.get("event_type") or source,
        "cookie_id": str(log.get("cookie_id") or ""),
        "status": log.get("status") or log.get("processing_status") or "",
        "created_at": log.get("updated_at") or log.get("created_at") or "",
    }


def build_admin_health_summary(
    current_user: Dict[str, Any],
    db_manager: Any,
    runtime_status_builder: Callable[[str], Dict[str, Any]],
    captcha_controller: Any = None,
) -> Dict[str, Any]:
    """Build an admin-only dashboard health snapshot without exposing secrets."""
    cookies = db_manager.get_all_cookies()
    if not isinstance(cookies, dict):
        cookies = {}

    accounts = []
    online_count = 0
    disabled_count = 0
    suspected_expired_count = 0

    for cookie_id in cookies.keys():
        account_id = str(cookie_id or "").strip()
        if not account_id:
            continue

        try:
            details = db_manager.get_cookie_details(account_id) or {}
        except Exception as exc:
            logger.warning(f"health summary cookie detail failed: {_mask_summary_error(exc)}")
            details = {}

        try:
            runtime_status = runtime_status_builder(account_id) or {}
        except Exception as exc:
            logger.warning(f"health summary runtime failed: {_mask_summary_error(exc)}")
            runtime_status = {}

        enabled = details.get("enabled")
        if enabled is None:
            enabled = True
        enabled = _health_summary_bool(enabled)
        running = bool(runtime_status.get("running"))
        stream_ready = bool(runtime_status.get("message_stream_ready"))
        token_ready = bool(runtime_status.get("has_current_token"))
        status_note = str(details.get("status_note") or "").strip()

        if running and stream_ready:
            online_count += 1
        if not enabled:
            disabled_count += 1
        if status_note or not token_ready:
            suspected_expired_count += 1

        accounts.append({
            "id": account_id,
            "enabled": enabled,
            "running": running,
            "message_stream_ready": stream_ready,
            "token_ready": token_ready,
            "status_note": status_note,
        })

    captcha_sessions = getattr(captcha_controller, "active_sessions", {}) if captcha_controller else {}
    if not isinstance(captcha_sessions, dict):
        captcha_sessions = {}
    pending_captcha = [
        {"session_id": str(session_id), "completed": bool(data.get("completed"))}
        for session_id, data in captcha_sessions.items()
        if isinstance(data, dict) and not data.get("completed")
    ]

    recent_failures: List[Dict[str, Any]] = []
    try:
        task_logs = db_manager.get_scheduled_task_logs(
            user_id=None,
            task_type=None,
            limit=20,
            offset=0,
        )
        for log in task_logs or []:
            if _health_summary_failure_status(log.get("status")):
                recent_failures.append(_health_summary_task_entry(log, "scheduled_task"))
    except Exception as exc:
        logger.warning(f"health summary task log failed: {_mask_summary_error(exc)}")

    try:
        for account_id in list(cookies.keys())[:50]:
            risk_logs = db_manager.get_risk_control_logs(cookie_id=str(account_id), limit=5)
            for log in risk_logs or []:
                if _health_summary_failure_status(log.get("processing_status")):
                    recent_failures.append(_health_summary_task_entry(log, "risk_control"))
    except Exception as exc:
        logger.warning(f"health summary risk log failed: {_mask_summary_error(exc)}")

    recent_failures = recent_failures[:10]
    offline_count = max(0, len(accounts) - online_count)
    health_level = "healthy"
    if pending_captcha or recent_failures or suspected_expired_count:
        health_level = "warning"
    if offline_count == len(accounts) and accounts:
        health_level = "critical"

    return {
        "success": True,
        "generated_at": datetime.now(LOCAL_TIMEZONE).isoformat(),
        "summary": {
            "level": health_level,
            "issues": len(pending_captcha) + len(recent_failures) + suspected_expired_count,
        },
        "accounts": {
            "total": len(accounts),
            "online": online_count,
            "offline": offline_count,
            "disabled": disabled_count,
            "items": accounts[:20],
        },
        "credentials": {
            "suspected_expired": suspected_expired_count,
            "token_unready": len([account for account in accounts if not account.get("token_ready")]),
        },
        "captcha": {
            "active": len(captcha_sessions),
            "pending": len(pending_captcha),
            "sessions": pending_captcha[:10],
        },
        "recent_failures": {
            "count": len(recent_failures),
            "items": recent_failures,
        },
    }
