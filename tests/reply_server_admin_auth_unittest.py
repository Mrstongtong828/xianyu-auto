from pathlib import Path
import unittest


class ReplyServerAdminAuthTests(unittest.TestCase):
    def setUp(self):
        self.namespace = self.load_admin_namespace()

    def load_admin_namespace(self):
        source = Path("reply_server.py").read_text(encoding="utf-8")
        start = source.index("ADMIN_USERNAME")
        constant_line_end = source.index("\n", start)
        function_start = source.index("def is_admin_user")
        function_end = source.index("\n\n\ndef verify_admin_token", function_start)
        snippet = source[start:constant_line_end] + "\n\n" + source[function_start:function_end]
        namespace = {"Dict": dict, "Any": object}
        exec(snippet, namespace)
        return namespace

    def test_is_admin_user_accepts_explicit_admin_flag(self):
        is_admin_user = self.namespace["is_admin_user"]

        self.assertTrue(is_admin_user({"username": "operator", "is_admin": True}))

    def test_is_admin_user_accepts_legacy_admin_username(self):
        is_admin_user = self.namespace["is_admin_user"]

        self.assertTrue(is_admin_user({"username": "admin", "is_admin": False}))

    def test_is_admin_user_rejects_regular_users(self):
        is_admin_user = self.namespace["is_admin_user"]

        self.assertFalse(is_admin_user({"username": "operator", "is_admin": False}))
        self.assertFalse(is_admin_user({"username": "operator"}))


if __name__ == "__main__":
    unittest.main()
