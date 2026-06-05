import json
import os
import tempfile
import unittest

from db_manager import DBManager


class AISharedModelGroupTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        self.manager = DBManager(self.db_path)
        self.manager.create_user("operator", "operator@example.com", "secret123")
        self.manager.save_cookie("acct_a", "sid=a", user_id=1)
        self.manager.save_cookie("acct_b", "sid=b", user_id=1)
        self.manager.save_cookie("acct_other", "sid=o", user_id=2)

    def tearDown(self):
        try:
            self.manager.conn.close()
        finally:
            self.tmpdir.cleanup()

    def test_bound_accounts_use_shared_model_group_settings(self):
        self.manager.save_ai_reply_settings(
            "acct_a",
            {
                "ai_enabled": True,
                "model_name": "account-model",
                "api_key": "account-key",
                "base_url": "https://account.example/v1",
                "api_type": "openai",
                "max_discount_percent": 5,
                "max_discount_amount": 50,
                "max_bargain_rounds": 1,
                "custom_prompts": json.dumps({"tone": "account"}),
            },
        )
        preset_id = self.manager.save_ai_config_preset(
            user_id=1,
            preset_name="shared-service",
            model_name="shared-model",
            api_key="shared-key",
            base_url="https://shared.example/v1",
            api_type="anthropic",
            max_discount_percent=12,
            max_discount_amount=120,
            max_bargain_rounds=4,
            custom_prompts=json.dumps({"tone": "shared"}),
        )

        self.assertTrue(self.manager.bind_ai_config_preset_accounts(1, preset_id, ["acct_a", "acct_b"]))

        acct_a = self.manager.get_effective_ai_reply_settings("acct_a")
        acct_b = self.manager.get_effective_ai_reply_settings("acct_b")

        self.assertEqual(acct_a["source"], "preset")
        self.assertEqual(acct_a["preset_id"], preset_id)
        self.assertEqual(acct_a["model_name"], "shared-model")
        self.assertEqual(acct_a["api_key"], "shared-key")
        self.assertEqual(acct_a["max_discount_percent"], 12)
        self.assertEqual(acct_a["custom_prompts"], json.dumps({"tone": "shared"}))
        self.assertEqual(acct_b["source"], "preset")
        self.assertEqual(acct_b["model_name"], "shared-model")

    def test_unbound_account_keeps_independent_settings(self):
        self.manager.save_ai_reply_settings(
            "acct_a",
            {
                "ai_enabled": True,
                "model_name": "account-model",
                "api_key": "account-key",
                "base_url": "https://account.example/v1",
                "api_type": "openai",
                "custom_prompts": json.dumps({"tone": "account"}),
            },
        )

        settings = self.manager.get_effective_ai_reply_settings("acct_a")

        self.assertEqual(settings["source"], "account")
        self.assertEqual(settings["model_name"], "account-model")
        self.assertEqual(settings["api_key"], "account-key")

    def test_deleting_model_group_removes_bindings_without_deleting_account_settings(self):
        self.manager.save_ai_reply_settings(
            "acct_a",
            {
                "ai_enabled": True,
                "model_name": "account-model",
                "api_key": "account-key",
                "base_url": "https://account.example/v1",
                "api_type": "openai",
            },
        )
        preset_id = self.manager.save_ai_config_preset(
            user_id=1,
            preset_name="shared-service",
            model_name="shared-model",
            api_key="shared-key",
            base_url="https://shared.example/v1",
            api_type="openai",
        )
        self.manager.bind_ai_config_preset_accounts(1, preset_id, ["acct_a"])

        self.assertTrue(self.manager.delete_ai_config_preset(1, preset_id))

        settings = self.manager.get_effective_ai_reply_settings("acct_a")
        self.assertEqual(settings["source"], "account")
        self.assertEqual(settings["model_name"], "account-model")
        self.assertEqual(self.manager.get_ai_config_preset_bindings(1, preset_id), [])


if __name__ == "__main__":
    unittest.main()
