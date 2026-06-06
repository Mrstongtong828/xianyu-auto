from pathlib import Path
import re
import unittest


REPLY_SERVER = Path(__file__).resolve().parents[1] / "reply_server.py"


def read_reply_server() -> str:
    return REPLY_SERVER.read_text(encoding="utf-8")


class ReplyServerHighRiskAuthTests(unittest.TestCase):
    def test_admin_routes_use_unified_admin_dependency(self):
        source = read_reply_server()
        admin_routes = [
            "@app.get('/admin/users')",
            "@app.delete('/admin/users/{user_id}')",
            "@app.put('/admin/users/{user_id}/admin-status')",
            "@app.get('/admin/data/{table_name}')",
            "@app.get('/admin/data/{table_name}/export')",
            "@app.delete('/admin/data/{table_name}/{record_id}')",
            "@app.delete('/admin/data/{table_name}')",
        ]

        for route in admin_routes:
            route_index = source.index(route)
            next_route = source.find("\n@app.", route_index + 1)
            block = source[route_index: next_route if next_route != -1 else len(source)]
            self.assertIn("Depends(require_admin)", block, route)

    def test_update_routes_use_unified_admin_check(self):
        source = read_reply_server()
        update_routes = [
            "@app.post('/api/update/apply')",
            "@app.post('/api/update/restart')",
        ]

        for route in update_routes:
            route_index = source.index(route)
            next_route = source.find("\n@app.", route_index + 1)
            block = source[route_index: next_route if next_route != -1 else len(source)]
            self.assertIn("is_admin_user(current_user)", block, route)
            self.assertNotRegex(block, re.compile(r"username['\"]?\s*==\s*['\"]admin['\"]"))

    def test_session_tokens_do_not_use_static_jwt_secret_fallback(self):
        source = read_reply_server()

        self.assertIn("def generate_token", source)
        self.assertIn("secrets.token_urlsafe(32)", source)
        self.assertNotIn("JWT_SECRET_KEY = ", source)
        self.assertNotIn("jwt.encode", source)
        self.assertNotIn("jwt.decode", source)


if __name__ == "__main__":
    unittest.main()
