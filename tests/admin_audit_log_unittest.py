import os
import sys
import tempfile
import types
import unittest

if "bcrypt" not in sys.modules:
    sys.modules["bcrypt"] = types.SimpleNamespace(
        gensalt=lambda: b"salt",
        hashpw=lambda password, salt: b"$2b$stubbed-hash",
        checkpw=lambda password, hashed: hashed == b"$2b$stubbed-hash",
    )
if "PIL" not in sys.modules:
    pil_module = types.ModuleType("PIL")
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = types.SimpleNamespace()
    sys.modules["PIL.ImageDraw"] = types.SimpleNamespace()
    sys.modules["PIL.ImageFont"] = types.SimpleNamespace()
if "loguru" not in sys.modules:
    sys.modules["loguru"] = types.SimpleNamespace(
        logger=types.SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
            debug=lambda *args, **kwargs: None,
        )
    )

from db_manager import DBManager


class AdminAuditLogTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        self.manager = DBManager(self.db_path)

    def tearDown(self):
        try:
            self.manager.conn.close()
        finally:
            self.tmpdir.cleanup()

    def test_admin_audit_log_can_be_recorded_and_listed(self):
        log_id = self.manager.record_admin_audit_log(
            actor_user_id=1,
            actor_username="admin",
            action="user.delete",
            target_type="user",
            target_id="42",
            result="success",
            ip_address="127.0.0.1",
            details={"username": "operator"},
        )

        self.assertIsInstance(log_id, int)

        result = self.manager.get_admin_audit_logs(limit=10)
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["logs"]), 1)

        row = result["logs"][0]
        self.assertEqual(row["id"], log_id)
        self.assertEqual(row["actor_user_id"], 1)
        self.assertEqual(row["actor_username"], "admin")
        self.assertEqual(row["action"], "user.delete")
        self.assertEqual(row["target_type"], "user")
        self.assertEqual(row["target_id"], "42")
        self.assertEqual(row["result"], "success")
        self.assertEqual(row["ip_address"], "127.0.0.1")
        self.assertEqual(row["details"]["username"], "operator")
        self.assertIn("created_at", row)

    def test_admin_audit_logs_support_filters_and_pagination(self):
        delete_log_id = self.manager.record_admin_audit_log(1, "admin", "user.delete", "user", "42")
        backup_log_id = self.manager.record_admin_audit_log(2, "owner", "backup.download", "backup", "db")
        promote_log_id = self.manager.record_admin_audit_log(1, "admin", "user.promote", "user", "7")

        with self.manager.lock:
            cursor = self.manager.conn.cursor()
            cursor.executemany(
                "UPDATE admin_audit_logs SET created_at = ? WHERE id = ?",
                [
                    ("2026-06-06 10:00:00", delete_log_id),
                    ("2026-06-06 12:00:00", backup_log_id),
                    ("2026-06-06 14:00:00", promote_log_id),
                ],
            )
            self.manager.conn.commit()

        by_actor = self.manager.get_admin_audit_logs(actor_user_id=1, limit=10)
        self.assertEqual(by_actor["total"], 2)
        self.assertEqual([row["action"] for row in by_actor["logs"]], ["user.promote", "user.delete"])

        by_action = self.manager.get_admin_audit_logs(action="backup.download", limit=10)
        self.assertEqual(by_action["total"], 1)
        self.assertEqual(by_action["logs"][0]["actor_username"], "owner")

        page = self.manager.get_admin_audit_logs(limit=1, offset=1)
        self.assertEqual(page["total"], 3)
        self.assertEqual(len(page["logs"]), 1)
        self.assertEqual(page["logs"][0]["action"], "backup.download")

        by_time = self.manager.get_admin_audit_logs(
            start_time="2026-06-06 11:59:00",
            end_time="2026-06-06 12:01:00",
            limit=10,
        )
        self.assertEqual(by_time["total"], 1)
        self.assertEqual(by_time["logs"][0]["action"], "backup.download")


if __name__ == "__main__":
    unittest.main()
