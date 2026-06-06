import types
import unittest
from pathlib import Path

from admin_health_summary import build_admin_health_summary


class AdminHealthSummaryTests(unittest.TestCase):
    def test_build_admin_health_summary_aggregates_runtime_and_failures(self):
        current_user = {"user_id": 1, "username": "admin", "is_admin": True}
        cookies = {
            "online": "masked-cookie-a",
            "offline": "masked-cookie-b",
        }
        details = {
            "online": {"status_note": "", "enabled": True},
            "offline": {"status_note": "token expired", "enabled": False},
        }

        db_manager = types.SimpleNamespace(
            get_all_cookies=lambda user_id=None: cookies,
            get_cookie_details=lambda cookie_id: details.get(cookie_id, {}),
            get_scheduled_task_logs=lambda **kwargs: [
                {"id": 7, "task_type": "auto_delivery", "status": "failed", "message": "failed with secret-token"}
            ],
            get_risk_control_logs=lambda **kwargs: [
                {"id": 8, "cookie_id": "offline", "event_type": "slider", "processing_status": "pending", "event_description": "captcha pending"}
            ] if kwargs.get("cookie_id") == "offline" else [],
        )
        captcha_controller = types.SimpleNamespace(
            active_sessions={
                "s1": {"completed": False},
                "s2": {"completed": True},
            }
        )

        summary = build_admin_health_summary(
            current_user=current_user,
            db_manager=db_manager,
            runtime_status_builder=lambda cookie_id: {
                "running": cookie_id == "online",
                "message_stream_ready": cookie_id == "online",
                "has_current_token": cookie_id == "online",
            },
            captcha_controller=captcha_controller,
        )

        self.assertTrue(summary["success"])
        self.assertEqual(summary["accounts"]["total"], 2)
        self.assertEqual(summary["accounts"]["online"], 1)
        self.assertEqual(summary["accounts"]["offline"], 1)
        self.assertEqual(summary["credentials"]["suspected_expired"], 1)
        self.assertEqual(summary["captcha"]["pending"], 1)
        self.assertEqual(summary["recent_failures"]["count"], 2)
        self.assertIn("generated_at", summary)
        self.assertNotIn("masked-cookie-a", str(summary))
        self.assertNotIn("secret-token", str(summary))

    def test_reply_server_delegates_health_summary_to_module(self):
        source = Path("reply_server.py").read_text(encoding="utf-8")

        self.assertIn("import admin_health_summary", source)
        self.assertIn("admin_health_summary.build_admin_health_summary", source)
        self.assertNotIn("def build_admin_health_summary", source)


if __name__ == "__main__":
    unittest.main()
