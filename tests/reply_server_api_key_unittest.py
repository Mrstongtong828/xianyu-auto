from pathlib import Path
import sys
import types
import unittest


class ReplyServerApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.original_db_manager = sys.modules.get("db_manager")
        self.namespace = self.load_api_key_namespace()

    def tearDown(self):
        if self.original_db_manager is None:
            sys.modules.pop("db_manager", None)
        else:
            sys.modules["db_manager"] = self.original_db_manager

    def install_fake_db_manager(self, secret_value):
        fake_db_manager = types.SimpleNamespace(
            db_manager=types.SimpleNamespace(
                get_system_setting=lambda key: secret_value
            )
        )
        sys.modules["db_manager"] = fake_db_manager

    def load_api_key_namespace(self):
        source = Path("reply_server.py").read_text(encoding="utf-8")
        constant_line_start = source.index("API_SECRET_KEY")
        constant_line_end = source.index("\n", constant_line_start)
        function_start = source.index("def verify_api_key")
        function_end = source.index("\n\n\n@app.post('/send-message'", function_start)
        snippet = source[constant_line_start:constant_line_end] + "\n\n" + source[function_start:function_end]
        namespace = {
            "logger": types.SimpleNamespace(
                warning=lambda *args, **kwargs: None,
                error=lambda *args, **kwargs: None,
            )
        }
        exec(snippet, namespace)
        return namespace

    def test_default_fallback_api_key_is_not_accepted_when_secret_is_missing(self):
        self.install_fake_db_manager(None)

        self.assertFalse(self.namespace["verify_api_key"]("xianyu_api_secret_2024"))

    def test_configured_api_key_is_required(self):
        self.install_fake_db_manager("configured-secret")

        self.assertTrue(self.namespace["verify_api_key"]("configured-secret"))
        self.assertFalse(self.namespace["verify_api_key"]("xianyu_api_secret_2024"))
        self.assertFalse(self.namespace["verify_api_key"](""))


if __name__ == "__main__":
    unittest.main()
