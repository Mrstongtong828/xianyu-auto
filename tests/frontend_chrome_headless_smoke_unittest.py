import http.server
import json
import os
from pathlib import Path
import socketserver
import subprocess
import tempfile
import threading
import unittest

from playwright.sync_api import sync_playwright


ROOT_DIR = Path(__file__).resolve().parents[1]


def find_chrome_executable():
    candidates = [
        os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


class QuietStaticHandler(http.server.SimpleHTTPRequestHandler):
    API_RESPONSES = {
        "/registration-status": {"enabled": False},
        "/login-info-status": {"enabled": False},
        "/login-captcha-status": {"enabled": False},
        "/api/login-captcha-enabled": {"enabled": False},
        "/captcha/check-required": {"required": False},
        "/verify": {"authenticated": False},
    }

    def do_GET(self):
        if self.path in self.API_RESPONSES:
            payload = json.dumps(self.API_RESPONSES[self.path]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

    def log_message(self, *args):
        return None


class FrontendChromeHeadlessSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chrome = find_chrome_executable()
        if not cls.chrome:
            raise unittest.SkipTest("Chrome/Edge executable not found")

        handler = lambda *args, **kwargs: QuietStaticHandler(*args, directory=str(ROOT_DIR), **kwargs)
        cls.httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "httpd"):
            cls.httpd.shutdown()
            cls.httpd.server_close()

    def render_screenshot(self, path, width, height):
        with tempfile.TemporaryDirectory() as tmpdir:
            screenshot = Path(tmpdir) / f"{path.strip('/').replace('/', '-')}-{width}x{height}.png"
            profile_dir = Path(tmpdir) / "profile"
            url = f"http://127.0.0.1:{self.port}{path}"
            result = subprocess.run(
                [
                    self.chrome,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-sandbox",
                    f"--user-data-dir={profile_dir}",
                    f"--window-size={width},{height}",
                    f"--screenshot={screenshot}",
                    url,
                ],
                cwd=str(ROOT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )

            combined_output = f"{result.stdout}\n{result.stderr}"
            self.assertTrue(screenshot.exists(), combined_output)
            self.assertGreater(screenshot.stat().st_size, 10_000, combined_output)

    def render_with_console_capture(self, path, width, height):
        url = f"http://127.0.0.1:{self.port}{path}"
        console_errors = []
        page_errors = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=self.chrome,
                headless=True,
            )
            try:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.on(
                    "console",
                    lambda msg: console_errors.append(msg.text)
                    if msg.type in {"error", "pageerror"}
                    else None,
                )
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                response = page.goto(url, wait_until="domcontentloaded")
                self.assertIsNotNone(response)
                self.assertLess(response.status, 400)
                page.wait_for_timeout(500)
                body_box = page.locator("body").bounding_box()
                self.assertIsNotNone(body_box)
                self.assertGreater(body_box["width"], 100)
                self.assertGreater(body_box["height"], 100)
            finally:
                browser.close()

        combined_errors = "\n".join(console_errors + page_errors)
        blocked_patterns = ("SyntaxError", "ReferenceError", "TypeError", "HTML parsing")
        self.assertFalse(
            any(pattern in combined_errors for pattern in blocked_patterns),
            combined_errors,
        )

    def test_login_page_renders_desktop_and_mobile(self):
        self.render_screenshot("/static/login.html", 1366, 900)
        self.render_screenshot("/static/login.html", 390, 844)
        self.render_with_console_capture("/static/login.html", 1366, 900)
        self.render_with_console_capture("/static/login.html", 390, 844)

    def test_index_page_renders_desktop_and_mobile(self):
        self.render_screenshot("/static/index.html", 1366, 900)
        self.render_screenshot("/static/index.html", 390, 844)
        self.render_with_console_capture("/static/index.html", 1366, 900)
        self.render_with_console_capture("/static/index.html", 390, 844)


if __name__ == "__main__":
    unittest.main()
