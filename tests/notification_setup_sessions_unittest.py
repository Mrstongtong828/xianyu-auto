import os
import tempfile
import time
import unittest

from db_manager import DBManager


class NotificationSetupSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        self.manager = DBManager(self.db_path)

    def tearDown(self):
        try:
            self.manager.conn.close()
        finally:
            self.tmpdir.cleanup()

    def test_session_can_be_created_validated_and_consumed_once(self):
        session = self.manager.create_notification_setup_session(
            token_hash="hash-one",
            user_id=42,
            expires_at=time.time() + 600,
        )

        self.assertEqual(session["user_id"], 42)
        self.assertIsNone(session["used_at"])

        active = self.manager.get_notification_setup_session("hash-one")
        self.assertIsNotNone(active)
        self.assertEqual(active["user_id"], 42)

        channel_id = self.manager.consume_notification_setup_session("hash-one", channel_id=7)
        self.assertEqual(channel_id, session["id"])

        consumed = self.manager.get_notification_setup_session("hash-one")
        self.assertIsNone(consumed)

    def test_expired_and_unknown_sessions_are_not_returned(self):
        self.manager.create_notification_setup_session(
            token_hash="expired",
            user_id=42,
            expires_at=time.time() - 1,
        )

        self.assertIsNone(self.manager.get_notification_setup_session("expired"))
        self.assertIsNone(self.manager.get_notification_setup_session("missing"))


if __name__ == "__main__":
    unittest.main()
