from pathlib import Path
import types
from typing import Any, Dict, Optional
import unittest


class AdminAuditHelperTests(unittest.TestCase):
    def load_helper_namespace(self, fake_db_manager):
        source = Path("reply_server.py").read_text(encoding="utf-8")
        start = source.index("def get_request_client_ip")
        end = source.index("\n\n\ndef _get_blacklist_block_by_cookie", start)
        namespace = {
            "Any": Any,
            "Dict": Dict,
            "Optional": Optional,
            "Request": object,
            "mask_sensitive_text": str,
            "db_manager": fake_db_manager,
            "logger": types.SimpleNamespace(warning=lambda *args, **kwargs: None),
        }
        exec(source[start:end], namespace)
        return namespace

    def test_record_admin_audit_writes_actor_target_and_forwarded_ip(self):
        calls = []
        fake_db_manager = types.SimpleNamespace(
            record_admin_audit_log=lambda **kwargs: calls.append(kwargs) or 99
        )
        namespace = self.load_helper_namespace(fake_db_manager)
        request = types.SimpleNamespace(
            headers={"X-Forwarded-For": "203.0.113.10, 10.0.0.2"},
            client=types.SimpleNamespace(host="127.0.0.1"),
        )

        log_id = namespace["record_admin_audit"](
            {"user_id": 7, "username": "admin"},
            action="user.delete",
            target_type="user",
            target_id="42",
            request=request,
            details={"username": "operator"},
        )

        self.assertEqual(log_id, 99)
        self.assertEqual(calls[0]["actor_user_id"], 7)
        self.assertEqual(calls[0]["actor_username"], "admin")
        self.assertEqual(calls[0]["action"], "user.delete")
        self.assertEqual(calls[0]["target_type"], "user")
        self.assertEqual(calls[0]["target_id"], "42")
        self.assertEqual(calls[0]["result"], "success")
        self.assertEqual(calls[0]["ip_address"], "203.0.113.10")
        self.assertEqual(calls[0]["details"], {"username": "operator"})

    def test_record_admin_audit_does_not_raise_when_db_logging_fails(self):
        def raise_error(**kwargs):
            raise RuntimeError("db unavailable")

        namespace = self.load_helper_namespace(
            types.SimpleNamespace(record_admin_audit_log=raise_error)
        )

        log_id = namespace["record_admin_audit"](
            {"user_id": 7, "username": "admin"},
            action="backup.download",
        )

        self.assertIsNone(log_id)


if __name__ == "__main__":
    unittest.main()
