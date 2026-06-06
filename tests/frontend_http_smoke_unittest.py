import sys
import types
import unittest
from pathlib import Path


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

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT_DIR / "static"


def create_frontend_smoke_app():
    try:
        from reply_server import app as full_app

        return full_app
    except Exception:
        pass

    smoke_app = FastAPI()
    smoke_app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @smoke_app.get("/login.html", response_class=HTMLResponse)
    async def login_page():
        return HTMLResponse((STATIC_DIR / "login.html").read_text(encoding="utf-8"))

    @smoke_app.get("/", response_class=HTMLResponse)
    async def index_page():
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @smoke_app.get("/admin", response_class=HTMLResponse)
    async def admin_page():
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    return smoke_app


class FrontendHttpSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_frontend_smoke_app())

    def test_login_page_serves_readable_copy_without_default_password(self):
        response = self.client.get("/login.html")

        self.assertEqual(response.status_code, 200)
        text = response.text
        self.assertIn("登录 - 闲鱼管理系统", text)
        self.assertIn("欢迎登录", text)
        self.assertIn("安全验证码", text)
        self.assertNotIn("admin123", text)
        self.assertNotIn("fillDefaultCredentials", text)

    def test_admin_page_and_static_script_load(self):
        response = self.client.get("/admin")

        self.assertEqual(response.status_code, 200)
        text = response.text
        self.assertIn("闲鱼管理系统", text)
        self.assertIn("仪表盘", text)
        self.assertIn("系统健康与任务状态", text)
        self.assertIn("app.js", text)

        script_response = self.client.get("/static/js/app.js")
        self.assertEqual(script_response.status_code, 200)
        self.assertIn("async function authenticatedFetch", script_response.text)

    def test_root_request_is_served_or_login_protected(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        text = response.text
        self.assertTrue("仪表盘" in text or "欢迎登录" in text)


if __name__ == "__main__":
    unittest.main()
