from pathlib import Path
import unittest


STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


def read_static_html(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


class StaticHtmlTextEncodingTests(unittest.TestCase):
    def test_login_page_keeps_readable_chinese_copy_without_default_password(self):
        text = read_static_html("login.html")

        self.assertIn("登录 - 闲鱼管理系统", text)
        self.assertIn("欢迎登录", text)
        self.assertIn("用户名/密码", text)
        self.assertIn("安全验证", text)
        self.assertNotIn("admin123", text)
        self.assertNotIn("fillDefaultCredentials", text)
        self.assertNotIn("闂", text)
        self.assertNotIn("鐧", text)

    def test_index_page_keeps_readable_chinese_navigation(self):
        text = read_static_html("index.html")

        self.assertIn("闲鱼管理系统", text)
        self.assertIn("仪表盘", text)
        self.assertIn("经营管理", text)
        self.assertIn("管理员功能", text)
        self.assertIn("系统日志", text)
        self.assertNotIn("闂", text)
        self.assertNotIn("鐧", text)


if __name__ == "__main__":
    unittest.main()
