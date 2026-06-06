import hashlib
import os
import sys
import tempfile
import types
import unittest

if "bcrypt" not in sys.modules:
    sys.modules["bcrypt"] = types.SimpleNamespace(
        gensalt=lambda: b"salt",
        hashpw=lambda password, salt: hashlib.sha256(password).hexdigest().encode("utf-8"),
        checkpw=lambda password, hashed: hashlib.sha256(password).hexdigest().encode("utf-8") == hashed,
    )
if "PIL" not in sys.modules:
    sys.modules["PIL"] = types.ModuleType("PIL")
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


class DefaultAdminPasswordTests(unittest.TestCase):
    def setUp(self):
        self.original_password = os.environ.pop("ADMIN_INITIAL_PASSWORD", None)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")

    def tearDown(self):
        if self.original_password is not None:
            os.environ["ADMIN_INITIAL_PASSWORD"] = self.original_password
        else:
            os.environ.pop("ADMIN_INITIAL_PASSWORD", None)
        self.tmpdir.cleanup()

    def test_initial_admin_password_no_longer_defaults_to_admin123(self):
        manager = DBManager(self.db_path)
        try:
            self.assertFalse(manager.verify_user_password("admin", "admin123"))
        finally:
            manager.conn.close()

    def test_initial_admin_password_can_be_set_from_environment(self):
        os.environ["ADMIN_INITIAL_PASSWORD"] = "configured-strong-password"
        manager = DBManager(self.db_path)
        try:
            self.assertTrue(manager.verify_user_password("admin", "configured-strong-password"))
            self.assertFalse(manager.verify_user_password("admin", "admin123"))
        finally:
            manager.conn.close()


if __name__ == "__main__":
    unittest.main()
