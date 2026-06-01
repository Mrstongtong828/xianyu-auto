import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from db_manager import DBManager
from reply_server import (
    ItemPublishTaskCreateRequest,
    cancel_item_publish_task as cancel_item_publish_task_endpoint,
    confirm_item_publish_task as confirm_item_publish_task_endpoint,
    create_item_publish_task as create_item_publish_task_endpoint,
    get_item_publish_task as get_item_publish_task_endpoint,
    list_item_publish_tasks as list_item_publish_tasks_endpoint,
    start_item_publish_task as start_item_publish_task_endpoint,
    _match_published_items,
    _merge_publish_item_candidates,
    _persist_matched_published_item,
    _validate_publish_task_payload,
)
from utils.item_publisher import (
    DEFAULT_PUBLISH_URL,
    XianyuItemPublisher,
    load_publish_selector_config,
    visible_text_has_publish_form_signal,
)


class PublishTaskDatabaseTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = DBManager(self.db_path)

    def tearDown(self):
        self.db.close()
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_create_update_and_list_publish_task(self):
        task_id = self.db.create_item_publish_task(
            user_id=7,
            account_id="account_001",
            title="授权测试商品",
            description="描述",
            price="19.90",
            category_keyword="手机壳",
            images_json=json.dumps(
                [{"image_url": "static/uploads/images/demo.jpg"}],
                ensure_ascii=False,
            ),
        )

        self.assertIsInstance(task_id, int)
        task = self.db.get_item_publish_task(task_id)
        self.assertEqual(task["status"], "draft")
        self.assertEqual(task["image_count"], 1)
        self.assertEqual(
            task["images_json_parsed"],
            [{"image_url": "static/uploads/images/demo.jpg"}],
        )

        updated = self.db.update_item_publish_task(
            task_id,
            status="waiting_manual_confirm",
            matched_item_ids=["123456"],
        )
        self.assertTrue(updated)
        task = self.db.get_item_publish_task(task_id)
        self.assertEqual(task["status"], "waiting_manual_confirm")
        self.assertEqual(task["matched_item_ids_parsed"], ["123456"])

        tasks = self.db.get_item_publish_tasks(user_id=7, account_id="account_001")
        self.assertEqual([item["id"] for item in tasks], [task_id])


class PublishTaskValidationTest(unittest.TestCase):
    def test_validate_publish_payload_requires_basic_fields(self):
        invalid_cases = [
            ItemPublishTaskCreateRequest(
                account_id="",
                title="商品",
                price="1.00",
                images=["static/uploads/images/demo.jpg"],
            ),
            ItemPublishTaskCreateRequest(
                account_id="account_001",
                title="",
                price="1.00",
                images=["static/uploads/images/demo.jpg"],
            ),
            ItemPublishTaskCreateRequest(
                account_id="account_001",
                title="商品",
                price="0",
                images=["static/uploads/images/demo.jpg"],
            ),
            ItemPublishTaskCreateRequest(
                account_id="account_001",
                title="商品",
                price="1.00",
                images=[],
            ),
            ItemPublishTaskCreateRequest(
                account_id="account_001",
                title="商品",
                price="1.00",
                images=["static/uploads/images/not_image.txt"],
            ),
            ItemPublishTaskCreateRequest(
                account_id="account_001",
                title="商品",
                price="1.00",
                images=["/static/uploads/images/../secret.jpg"],
            ),
        ]

        for request_data in invalid_cases:
            with self.subTest(request_data=request_data):
                with self.assertRaises(HTTPException):
                    _validate_publish_task_payload(request_data)

    def test_validate_publish_payload_normalizes_images(self):
        request_data = ItemPublishTaskCreateRequest(
            account_id=" account_001 ",
            title=" 授权测试商品 ",
            description=" 描述 ",
            price="19.90",
            category_keyword=" 手机壳 ",
            images=["/static/uploads/images/a.jpg", "static/uploads/images/b.jpg"],
        )

        payload = _validate_publish_task_payload(request_data)

        self.assertEqual(payload["account_id"], "account_001")
        self.assertEqual(payload["title"], "授权测试商品")
        self.assertEqual(payload["price"], "19.90")
        self.assertEqual(
            payload["images"],
            [
                {"image_url": "/static/uploads/images/a.jpg"},
                {"image_url": "/static/uploads/images/b.jpg"},
            ],
        )

    def test_validate_publish_payload_allows_exactly_nine_images(self):
        request_data = ItemPublishTaskCreateRequest(
            account_id="account_001",
            title="授权测试商品",
            price="19.90",
            images=[f"/static/uploads/images/{index}.jpg" for index in range(9)],
        )

        payload = _validate_publish_task_payload(request_data)

        self.assertEqual(len(payload["images"]), 9)

    def test_validate_publish_payload_rejects_more_than_nine_images(self):
        request_data = ItemPublishTaskCreateRequest(
            account_id="account_001",
            title="授权测试商品",
            price="19.90",
            images=[f"/static/uploads/images/{index}.jpg" for index in range(10)],
        )

        with self.assertRaises(HTTPException) as error:
            _validate_publish_task_payload(request_data)

        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("1-9张", error.exception.detail)


class PublishTaskRouteTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = DBManager(self.db_path)
        self.current_user = {"user_id": 7, "username": "operator", "is_admin": False}
        self.db.save_cookie("owned_account", "sid=owned; token=abc", user_id=7)
        self.db.save_cookie("other_account", "sid=other; token=abc", user_id=8)

    def tearDown(self):
        self.db.close()
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def _build_request(self, account_id="owned_account"):
        return ItemPublishTaskCreateRequest(
            account_id=account_id,
            title="Authorized route test item",
            description="Route-level create test",
            price="12.50",
            category_keyword="phone case",
            images=["/static/uploads/images/route-demo.jpg"],
        )

    def test_create_and_list_routes_use_only_current_users_authorized_account(self):
        with patch("reply_server.db_manager", self.db):
            created = create_item_publish_task_endpoint(self._build_request(), self.current_user)
            listed = list_item_publish_tasks_endpoint(current_user=self.current_user)
            fetched = get_item_publish_task_endpoint(created["task"]["id"], self.current_user)

        self.assertTrue(created["success"])
        self.assertEqual(created["task"]["account_id"], "owned_account")
        self.assertEqual(created["task"]["status"], "draft")
        self.assertEqual(created["task"]["images"], [{"image_url": "/static/uploads/images/route-demo.jpg"}])
        self.assertEqual([task["id"] for task in listed["tasks"]], [created["task"]["id"]])
        self.assertEqual(fetched["task"]["id"], created["task"]["id"])
        self.assertEqual(self.db.get_item_publish_tasks(user_id=8), [])

    def test_create_route_rejects_account_not_owned_by_current_user(self):
        with patch("reply_server.db_manager", self.db):
            with self.assertRaises(HTTPException) as error:
                create_item_publish_task_endpoint(self._build_request("other_account"), self.current_user)

        self.assertEqual(error.exception.status_code, 403)

    def test_waiting_task_without_active_browser_is_marked_for_restart(self):
        task_id = self.db.create_item_publish_task(
            user_id=7,
            account_id="owned_account",
            title="Waiting task after service restart",
            description="No live browser remains after backend restart",
            price="12.50",
            category_keyword="phone case",
            images_json=json.dumps([{"image_url": "/static/uploads/images/route-demo.jpg"}], ensure_ascii=False),
            status="waiting_manual_confirm",
        )

        async def clear_active_browser():
            from utils.item_publisher import close_active_publisher

            await close_active_publisher(task_id)

        asyncio.run(clear_active_browser())

        with patch("reply_server.db_manager", self.db):
            listed = list_item_publish_tasks_endpoint(current_user=self.current_user)
            fetched = get_item_publish_task_endpoint(task_id, self.current_user)

        self.assertFalse(listed["tasks"][0]["active_browser_available"])
        self.assertFalse(fetched["task"]["active_browser_available"])

    def test_start_route_reuses_active_browser_when_waiting_for_manual_auth(self):
        class FakePublisher:
            def __init__(self):
                self.continued = False
                self.started = False
                self.closed = False

            async def continue_after_manual_auth(self):
                self.continued = True
                return {
                    "success": True,
                    "status": "waiting_manual_confirm",
                    "browser_screenshot": "/static/uploads/images/resumed.png",
                    "error_message": "",
                    "error_details": "",
                    "notes": json.dumps({"resumed": True}, ensure_ascii=False),
                }

            async def start(self):
                self.started = True
                return {"success": False, "status": "failed", "error_message": "should not start new browser"}

            async def close(self):
                self.closed = True

            async def save_screenshot(self, suffix):
                return ""

        task_id = self.db.create_item_publish_task(
            user_id=7,
            account_id="owned_account",
            title="Need manual auth",
            description="Continue after operator handles auth",
            price="12.50",
            category_keyword="phone case",
            images_json=json.dumps([{"image_url": "/static/uploads/images/route-demo.jpg"}], ensure_ascii=False),
            status="waiting_manual_confirm",
        )

        fake_publisher = FakePublisher()

        async def run_case():
            from utils.item_publisher import close_active_publisher, register_active_publisher

            register_active_publisher(task_id, fake_publisher)
            try:
                with patch("reply_server.db_manager", self.db):
                    return await start_item_publish_task_endpoint(task_id, self.current_user)
            finally:
                await close_active_publisher(task_id)

        result = asyncio.run(run_case())
        task = self.db.get_item_publish_task(task_id)

        self.assertTrue(result["success"])
        self.assertTrue(result["task"]["active_browser_available"])
        self.assertTrue(fake_publisher.continued)
        self.assertFalse(fake_publisher.started)
        self.assertTrue(fake_publisher.closed)
        self.assertEqual(task["status"], "waiting_manual_confirm")
        self.assertEqual(task["browser_screenshot"], "/static/uploads/images/resumed.png")
        self.assertTrue(json.loads(task["notes"])["continued_active_browser"])

    def test_start_route_marks_task_failed_when_account_cookie_is_empty(self):
        self.db.save_cookie("empty_cookie_account", "", user_id=7)
        task_id = self.db.create_item_publish_task(
            user_id=7,
            account_id="empty_cookie_account",
            title="Cookie missing task",
            description="Should fail before opening browser",
            price="12.50",
            category_keyword="phone case",
            images_json=json.dumps([{"image_url": "/static/uploads/images/route-demo.jpg"}], ensure_ascii=False),
            status="draft",
        )

        async def run_case():
            with patch("reply_server.db_manager", self.db):
                return await start_item_publish_task_endpoint(task_id, self.current_user)

        result = asyncio.run(run_case())
        task = self.db.get_item_publish_task(task_id)

        self.assertFalse(result["success"])
        self.assertEqual(task["status"], "failed")
        self.assertIn("Cookie为空", task["error_message"])
        self.assertTrue(task["finished_at"])

    def test_start_route_marks_task_failed_when_visible_browser_is_unavailable(self):
        task_id = self.db.create_item_publish_task(
            user_id=7,
            account_id="owned_account",
            title="No visible browser task",
            description="Docker without VNC should fail before opening publish flow",
            price="12.50",
            category_keyword="phone case",
            images_json=json.dumps([{"image_url": "/static/uploads/images/route-demo.jpg"}], ensure_ascii=False),
            status="draft",
        )
        env_overrides = {
            "DISPLAY": "",
            "WAYLAND_DISPLAY": "",
            "USE_XVFB": "false",
            "ENABLE_VNC": "false",
        }

        async def run_case():
            with patch("reply_server.db_manager", self.db), patch(
                "utils.item_publisher.sys.platform",
                "linux",
            ), patch.dict(os.environ, env_overrides):
                return await start_item_publish_task_endpoint(task_id, self.current_user)

        result = asyncio.run(run_case())
        task = self.db.get_item_publish_task(task_id)

        self.assertFalse(result["success"])
        self.assertEqual(task["status"], "failed")
        self.assertIn("可见浏览器", task["error_message"])
        self.assertTrue(task["finished_at"])

    def test_cancel_route_closes_active_browser_and_marks_task_cancelled(self):
        class FakePublisher:
            def __init__(self):
                self.closed = False

            async def close(self):
                self.closed = True

        task_id = self.db.create_item_publish_task(
            user_id=7,
            account_id="owned_account",
            title="Cancel waiting task",
            description="Close active publish browser",
            price="12.50",
            category_keyword="phone case",
            images_json=json.dumps([{"image_url": "/static/uploads/images/route-demo.jpg"}], ensure_ascii=False),
            status="waiting_manual_confirm",
        )
        fake_publisher = FakePublisher()

        async def run_case():
            from utils.item_publisher import get_active_publisher, register_active_publisher

            register_active_publisher(task_id, fake_publisher)
            with patch("reply_server.db_manager", self.db):
                result = await cancel_item_publish_task_endpoint(task_id, self.current_user)
            return result, get_active_publisher(task_id)

        result, active_after_cancel = asyncio.run(run_case())
        task = self.db.get_item_publish_task(task_id)

        self.assertTrue(result["success"])
        self.assertTrue(fake_publisher.closed)
        self.assertIsNone(active_after_cancel)
        self.assertEqual(task["status"], "cancelled")
        self.assertTrue(task["finished_at"])
        self.assertEqual(task["error_message"], "用户取消发布任务")

    def test_cancel_route_rejects_published_task(self):
        task_id = self.db.create_item_publish_task(
            user_id=7,
            account_id="owned_account",
            title="Already published",
            description="Cannot cancel",
            price="12.50",
            category_keyword="phone case",
            images_json=json.dumps([{"image_url": "/static/uploads/images/route-demo.jpg"}], ensure_ascii=False),
            status="published",
        )

        async def run_case():
            with patch("reply_server.db_manager", self.db):
                return await cancel_item_publish_task_endpoint(task_id, self.current_user)

        with self.assertRaises(HTTPException) as error:
            asyncio.run(run_case())

        self.assertEqual(error.exception.status_code, 400)

    def test_confirm_route_marks_task_published_and_persists_matched_item_info(self):
        class FakeXianyuLive:
            def __init__(self, cookies_str, account_id, register_instance=False):
                self.cookies_str = cookies_str
                self.account_id = account_id
                self.register_instance = register_instance
                self.closed = False

            async def get_all_items(self, sync_item_details=True):
                return {
                    "success": True,
                    "total_count": 2,
                    "total_pages": 1,
                    "items": [
                        {"id": "old_1", "title": "Authorized route test item", "price": "12.50"},
                        {
                            "id": "new_1",
                            "title": "Authorized route test item",
                            "price": "12.50",
                            "description": "Published by confirm route",
                        },
                    ],
                }

            async def close_session(self):
                self.closed = True

        task_id = self.db.create_item_publish_task(
            user_id=7,
            account_id="owned_account",
            title="Authorized route test item",
            description="Route-level create test",
            price="12.50",
            category_keyword="phone case",
            images_json=json.dumps([{"image_url": "/static/uploads/images/route-demo.jpg"}], ensure_ascii=False),
            status="waiting_manual_confirm",
        )
        self.db.update_item_publish_task(
            task_id,
            notes=json.dumps({"before_item_ids": ["old_1"]}, ensure_ascii=False),
        )

        async def run_case():
            with patch("reply_server.db_manager", self.db), patch("XianyuAutoAsync.XianyuLive", FakeXianyuLive):
                return await confirm_item_publish_task_endpoint(task_id, self.current_user)

        result = asyncio.run(run_case())
        task = self.db.get_item_publish_task(task_id)
        saved_item = self.db.get_item_info("owned_account", "new_1")

        self.assertTrue(result["success"])
        self.assertEqual(task["status"], "published")
        self.assertEqual(task["platform_item_id"], "new_1")
        self.assertEqual(task["matched_item_ids_parsed"], ["new_1"])
        self.assertIsNotNone(saved_item)
        self.assertEqual(saved_item["item_title"], "Authorized route test item")
        self.assertEqual(saved_item["item_price"], "12.50")
        self.assertTrue(json.loads(task["notes"])["matched_item_info_persisted"])

    def test_confirm_route_keeps_waiting_when_sync_finds_no_matching_item(self):
        class FakeXianyuLive:
            def __init__(self, cookies_str, account_id, register_instance=False):
                self.cookies_str = cookies_str
                self.account_id = account_id
                self.register_instance = register_instance

            async def get_all_items(self, sync_item_details=True):
                return {
                    "success": True,
                    "total_count": 1,
                    "total_pages": 1,
                    "items": [{"id": "other_1", "title": "Different item", "price": "12.50"}],
                }

            async def close_session(self):
                pass

        task_id = self.db.create_item_publish_task(
            user_id=7,
            account_id="owned_account",
            title="Authorized route test item",
            description="Route-level create test",
            price="12.50",
            category_keyword="phone case",
            images_json=json.dumps([{"image_url": "/static/uploads/images/route-demo.jpg"}], ensure_ascii=False),
            status="waiting_manual_confirm",
        )
        self.db.update_item_publish_task(
            task_id,
            notes=json.dumps({"before_item_ids": []}, ensure_ascii=False),
        )

        async def run_case():
            with patch("reply_server.db_manager", self.db), patch("XianyuAutoAsync.XianyuLive", FakeXianyuLive):
                return await confirm_item_publish_task_endpoint(task_id, self.current_user)

        result = asyncio.run(run_case())
        task = self.db.get_item_publish_task(task_id)
        notes = json.loads(task["notes"])

        self.assertFalse(result["success"])
        self.assertEqual(task["status"], "waiting_manual_confirm")
        self.assertEqual(task["matched_item_ids_parsed"], [])
        self.assertIn("没有匹配到新发布商品ID", task["error_message"])
        self.assertEqual(notes["last_confirm_sync"]["candidate_item_count"], 1)

    def test_confirm_route_keeps_waiting_when_sync_raises_so_operator_can_retry(self):
        class FakePublisher:
            def __init__(self):
                self.closed = False

            async def export_cookie_string(self):
                return ""

            async def close(self):
                self.closed = True

        class FakeXianyuLive:
            def __init__(self, cookies_str, account_id, register_instance=False):
                self.cookies_str = cookies_str
                self.account_id = account_id
                self.register_instance = register_instance
                self.closed = False

            async def get_all_items(self, sync_item_details=True):
                raise RuntimeError("token expired while syncing")

            async def close_session(self):
                self.closed = True

        task_id = self.db.create_item_publish_task(
            user_id=7,
            account_id="owned_account",
            title="Authorized route test item",
            description="Route-level create test",
            price="12.50",
            category_keyword="phone case",
            images_json=json.dumps([{"image_url": "/static/uploads/images/route-demo.jpg"}], ensure_ascii=False),
            status="waiting_manual_confirm",
        )
        fake_publisher = FakePublisher()

        async def run_case():
            from utils.item_publisher import get_active_publisher, register_active_publisher

            register_active_publisher(task_id, fake_publisher)
            with patch("reply_server.db_manager", self.db), patch("XianyuAutoAsync.XianyuLive", FakeXianyuLive):
                result = await confirm_item_publish_task_endpoint(task_id, self.current_user)
            return result, get_active_publisher(task_id)

        try:
            result, active_after_sync_error = asyncio.run(run_case())
            task = self.db.get_item_publish_task(task_id)

            self.assertFalse(result["success"])
            self.assertEqual(task["status"], "waiting_manual_confirm")
            self.assertIn("同步账号商品失败", task["error_message"])
            self.assertEqual(task["finished_at"], "")
            self.assertIs(active_after_sync_error, fake_publisher)
            self.assertFalse(fake_publisher.closed)
        finally:
            async def cleanup():
                from utils.item_publisher import close_active_publisher

                await close_active_publisher(task_id)

            asyncio.run(cleanup())


class PublishItemMatchingTest(unittest.TestCase):
    def test_match_published_items_skips_existing_items(self):
        task = {
            "title": "授权测试商品",
            "price": "19.90",
            "notes": json.dumps({"before_item_ids": ["old_1"]}, ensure_ascii=False),
        }
        items = [
            {"id": "old_1", "title": "授权测试商品", "price": "19.90"},
            {"id": "new_1", "title": "授权测试商品", "price": "19.90"},
            {"id": "new_2", "title": "其他商品", "price": "19.90"},
        ]

        self.assertEqual(_match_published_items(task, items), ["new_1"])

    def test_match_published_items_supports_db_item_info_shape(self):
        task = {
            "title": "授权测试商品",
            "price": "19.90",
            "notes": json.dumps({"before_item_ids": ["old_1"]}, ensure_ascii=False),
        }
        items = [
            {"item_id": "old_1", "item_title": "授权测试商品", "item_price": "19.90"},
            {"item_id": "new_db_1", "item_title": "授权测试商品", "item_price": "19.90"},
        ]

        self.assertEqual(_match_published_items(task, items), ["new_db_1"])

    def test_match_published_items_normalizes_price_format(self):
        task = {
            "title": "授权测试商品",
            "price": "19.90",
            "notes": json.dumps({"before_item_ids": []}, ensure_ascii=False),
        }
        items = [
            {"id": "new_1", "title": "授权测试商品", "price": "¥19.9"},
            {"id": "new_2", "title": "授权测试商品", "price": "20.00"},
        ]

        self.assertEqual(_match_published_items(task, items), ["new_1"])

    def test_merge_publish_item_candidates_prefers_returned_items_and_adds_db_items(self):
        merged = _merge_publish_item_candidates(
            [{"id": "api_1", "title": "接口商品"}, {"id": "same_1", "title": "接口优先"}],
            [{"item_id": "same_1", "item_title": "数据库重复"}, {"item_id": "db_1", "item_title": "数据库商品"}],
        )

        self.assertEqual([item.get("id") or item.get("item_id") for item in merged], ["api_1", "same_1", "db_1"])
        self.assertEqual(merged[1]["title"], "接口优先")

    def test_persist_matched_published_item_writes_item_info(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db = DBManager(db_path)
        try:
            items = [{"id": "new_1", "title": "授权测试商品", "price": "19.90", "description": "发布描述"}]
            with patch("reply_server.db_manager", db):
                self.assertTrue(_persist_matched_published_item("account_001", "new_1", items))

            saved = db.get_item_info("account_001", "new_1")
            self.assertIsNotNone(saved)
            self.assertEqual(saved["item_title"], "授权测试商品")
            self.assertEqual(saved["item_price"], "19.90")
        finally:
            db.close()
            try:
                os.remove(db_path)
            except OSError:
                pass


class PublishSelectorConfigTest(unittest.TestCase):
    def test_load_publish_selector_config_uses_default_file(self):
        config = load_publish_selector_config()

        self.assertEqual(config["publish_url"], DEFAULT_PUBLISH_URL)
        self.assertIn('input[type="file"]', config["file_input_selectors"])
        self.assertIn("text=添加首图", config["upload_trigger_selectors"])
        self.assertIn("验证码", config["risk_keywords"])
        self.assertTrue(config["config_path"].endswith("item_publish_selectors.json"))

    def test_load_publish_selector_config_allows_json_override_with_fallbacks(self):
        fd, config_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(config_path, "w", encoding="utf-8") as file:
                json.dump(
                    {
                        "publish_url": "https://example.test/publish",
                        "title_selectors": ["input[data-testid='title']"],
                        "price_selectors": [],
                        "risk_keywords": ["人工验证"],
                    },
                    file,
                    ensure_ascii=False,
                )

            config = load_publish_selector_config(config_path)

            self.assertEqual(config["publish_url"], "https://example.test/publish")
            self.assertEqual(config["title_selectors"], ["input[data-testid='title']"])
            self.assertIn('input[type="number"]', config["price_selectors"])
            self.assertEqual(config["risk_keywords"], ["人工验证"])
        finally:
            try:
                os.remove(config_path)
            except OSError:
                pass


class PublishPublisherSafetyTest(unittest.TestCase):
    def _build_task(self):
        return {
            "id": 99,
            "account_id": "account_001",
            "title": "授权测试商品",
            "description": "描述",
            "price": "19.90",
            "category_keyword": "手机壳",
            "images_json_parsed": [{"image_url": "static/uploads/images/demo.jpg"}],
        }

    def test_visible_browser_is_required_without_display_or_vnc(self):
        env_overrides = {
            "DISPLAY": "",
            "WAYLAND_DISPLAY": "",
            "USE_XVFB": "false",
            "ENABLE_VNC": "false",
        }
        with patch("utils.item_publisher.sys.platform", "linux"), patch.dict(os.environ, env_overrides):
            publisher = XianyuItemPublisher(self._build_task(), "token=abc")

            self.assertFalse(publisher.can_open_visible_browser())

    def test_cookie_entries_cover_goofish_and_taobao_publish_domains(self):
        publisher = XianyuItemPublisher(self._build_task(), "a=1; b=2; a=1; invalid")

        self.assertEqual(
            publisher._build_cookie_entries(),
            [
                {"name": "a", "value": "1", "domain": ".goofish.com", "path": "/"},
                {"name": "a", "value": "1", "domain": ".taobao.com", "path": "/"},
                {"name": "b", "value": "2", "domain": ".goofish.com", "path": "/"},
                {"name": "b", "value": "2", "domain": ".taobao.com", "path": "/"},
            ],
        )

    def test_image_url_to_path_rejects_paths_outside_upload_dir(self):
        publisher = XianyuItemPublisher(self._build_task(), "token=abc")

        safe_path = publisher._image_url_to_path("/static/uploads/images/demo.jpg")

        self.assertIsNotNone(safe_path)
        self.assertTrue(str(safe_path).endswith("static\\uploads\\images\\demo.jpg"))
        self.assertIsNone(publisher._image_url_to_path("/static/uploads/images/../secret.jpg"))
        self.assertIsNone(publisher._image_url_to_path("/static/uploads/images/%2e%2e/secret.jpg"))
        self.assertIsNone(publisher._image_url_to_path("C:/tmp/secret.jpg"))

    def test_description_includes_title_when_publish_page_has_no_title_field(self):
        publisher = XianyuItemPublisher(self._build_task(), "token=abc")

        self.assertEqual(publisher._build_description_value(title_was_filled=False), "授权测试商品\n\n描述")
        self.assertEqual(publisher._build_description_value(title_was_filled=True), "描述")

    def test_auth_detection_ignores_login_copy_on_visible_publish_form(self):
        class FakeLocator:
            def __init__(self, text=""):
                self.text = text

            async def inner_text(self, timeout=3000):
                return self.text

            async def count(self):
                return 0

        class FakePage:
            url = "https://www.goofish.com/publish"

            def locator(self, selector):
                if selector == "body":
                    return FakeLocator("发闲置\n宝贝图片\n宝贝描述\n登录后可查看更多")
                return FakeLocator()

        async def run_case():
            publisher = XianyuItemPublisher(self._build_task(), "token=abc")
            publisher.page = FakePage()
            return await publisher._is_auth_or_risk_page()

        self.assertFalse(asyncio.run(run_case()))

    def test_auth_detection_does_not_block_visible_publish_form_with_safety_copy(self):
        class FakeLocator:
            def __init__(self, text="", count=0):
                self.text = text
                self._count = count

            async def inner_text(self, timeout=3000):
                return self.text

            async def count(self):
                return self._count

        class FakePage:
            url = "https://www.goofish.com/publish"

            def locator(self, selector):
                if selector == "body":
                    return FakeLocator("基础信息\n宝贝图片\n宝贝描述\n安全验证提示：请勿绕过平台验证")
                if selector == 'textarea[placeholder*="描述"]':
                    return FakeLocator(count=1)
                if selector == 'input[placeholder*="价格"]':
                    return FakeLocator(count=1)
                return FakeLocator()

        async def run_case():
            publisher = XianyuItemPublisher(self._build_task(), "token=abc")
            publisher.page = FakePage()
            return await publisher._is_auth_or_risk_page()

        self.assertFalse(asyncio.run(run_case()))

    def test_publish_form_text_signal_matches_real_xianyu_publish_page_copy(self):
        visible_text = "\n".join(
            [
                "发闲置",
                "基础信息",
                "宝贝图片 *",
                "+ 添加首图",
                "宝贝描述 *",
                "描述一下宝贝的品牌型号、货品来源...",
                "价格",
                "发货设置",
                "登录后可查看更多订单消息",
                "安全验证提示：请勿绕过平台验证",
            ]
        )

        self.assertTrue(visible_text_has_publish_form_signal(visible_text))

    def test_publish_form_text_signal_matches_current_xianyu_copy(self):
        visible_text = "\n".join(
            [
                "发布闲置",
                "基础信息",
                "宝贝图片 *",
                "+ 添加首图",
                "宝贝描述 *",
                "描述一下宝贝的品牌型号、货品来源...",
                "价格",
                "发货设置",
            ]
        )

        self.assertTrue(visible_text_has_publish_form_signal(visible_text))

    def test_selector_config_uses_real_chinese_copy(self):
        config = load_publish_selector_config()
        selector_payload = json.dumps(config, ensure_ascii=False)

        self.assertIn('textarea[placeholder*="描述"]', config["description_selectors"])
        self.assertIn('input[placeholder*="价格"]', config["price_selectors"])
        self.assertIn("text=添加首图", config["upload_trigger_selectors"])
        self.assertNotIn("\ufffd", selector_payload)

    def test_auth_detection_rechecks_slow_publish_form_before_blocking(self):
        class FakeLocator:
            def __init__(self, page, selector):
                self.page = page
                self.selector = selector

            async def inner_text(self, timeout=3000):
                if self.selector == "body":
                    if self.page.form_ready:
                        return "发闲置\n基础信息\n宝贝图片\n宝贝描述\n价格\n发货设置"
                    return "安全验证中，请稍候"
                return ""

            async def count(self):
                if not self.page.form_ready:
                    return 0
                if self.selector in {
                    'textarea[placeholder*="描述"]',
                    'input[placeholder*="价格"]',
                }:
                    return 1
                return 0

        class FakePage:
            url = "https://www.goofish.com/publish"

            def __init__(self):
                self.form_ready = False
                self.waits = []

            def locator(self, selector):
                return FakeLocator(self, selector)

            async def wait_for_timeout(self, timeout):
                self.waits.append(timeout)
                self.form_ready = True

        async def run_case():
            publisher = XianyuItemPublisher(self._build_task(), "token=abc")
            fake_page = FakePage()
            publisher.page = fake_page
            is_blocked = await publisher._is_auth_or_risk_page()
            return is_blocked, fake_page

        is_blocked, fake_page = asyncio.run(run_case())

        self.assertFalse(is_blocked)
        self.assertTrue(fake_page.waits)

    def test_upload_images_falls_back_to_single_file_when_batch_upload_fails(self):
        class FakeInput:
            def __init__(self):
                self.calls = []

            async def set_input_files(self, files):
                self.calls.append(files)
                if isinstance(files, list):
                    raise RuntimeError("single file input")

        class FakeLocator:
            def __init__(self, fake_input):
                self.first = fake_input

            async def count(self):
                return 1

        class FakePage:
            def __init__(self):
                self.fake_input = FakeInput()
                self.waits = []

            def locator(self, selector):
                return FakeLocator(self.fake_input)

            async def wait_for_timeout(self, timeout):
                self.waits.append(timeout)

        async def run_case():
            publisher = XianyuItemPublisher(self._build_task(), "token=abc")
            fake_page = FakePage()
            publisher.page = fake_page
            ok = await publisher._upload_images(["first.png", "second.png"])
            return ok, fake_page

        ok, fake_page = asyncio.run(run_case())

        self.assertTrue(ok)
        self.assertEqual(fake_page.fake_input.calls, [["first.png", "second.png"], "first.png", "second.png"])

    def test_upload_images_uses_file_chooser_trigger_when_input_is_not_ready(self):
        class EmptyLocator:
            first = None

            async def count(self):
                return 0

        class TriggerElement:
            def __init__(self, page):
                self.page = page

            async def scroll_into_view_if_needed(self, timeout=2000):
                self.page.scrolled = True

            async def click(self, timeout=2500):
                self.page.clicked = True

        class TriggerLocator:
            def __init__(self, page):
                self.first = TriggerElement(page)

            async def count(self):
                return 1

        class FakeFileChooser:
            def __init__(self):
                self.files = None

            async def set_files(self, files):
                self.files = files

        class FakeFileChooserContext:
            def __init__(self, file_chooser):
                self.file_chooser = file_chooser

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            @property
            def value(self):
                async def resolve():
                    return self.file_chooser
                return resolve()

        class FakePage:
            def __init__(self):
                self.file_chooser = FakeFileChooser()
                self.clicked = False
                self.scrolled = False
                self.waits = []

            def locator(self, selector):
                if selector == "text=添加首图":
                    return TriggerLocator(self)
                return EmptyLocator()

            def expect_file_chooser(self, timeout=2500):
                return FakeFileChooserContext(self.file_chooser)

            async def wait_for_timeout(self, timeout):
                self.waits.append(timeout)

        async def run_case():
            publisher = XianyuItemPublisher(self._build_task(), "token=abc")
            publisher.page = FakePage()
            ok = await publisher._upload_images(["first.png", "second.png"])
            return ok, publisher.page

        ok, fake_page = asyncio.run(run_case())

        self.assertTrue(ok)
        self.assertTrue(fake_page.clicked)
        self.assertTrue(fake_page.scrolled)
        self.assertEqual(fake_page.file_chooser.files, ["first.png", "second.png"])


class PublishPublisherBrowserFlowTest(unittest.TestCase):
    def setUp(self):
        self.project_root = Path(__file__).resolve().parents[1]
        self.upload_dir = self.project_root / "static" / "uploads" / "images"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.image_path = self.upload_dir / "publish_browser_flow_fixture.png"
        self.image_path.write_bytes(
            bytes.fromhex(
                "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                "0000000a49444154789c6360000002000152730a790000000049454e44ae426082"
            )
        )
        fd, html_path = tempfile.mkstemp(suffix=".html")
        os.close(fd)
        self.html_path = Path(html_path)
        self.html_path.write_text(
            """<!doctype html>
<html>
<body>
  <input type="file" accept="image/*" id="images" multiple>
  <input placeholder="标题" id="title">
  <textarea placeholder="描述" id="desc"></textarea>
  <input placeholder="价格" id="price">
  <input placeholder="类目" id="category">
  <button id="finalPublish">发布</button>
  <output id="fileCount"></output>
  <output id="publishClickCount">0</output>
  <script>
    document.getElementById('images').addEventListener('change', event => {
      document.getElementById('fileCount').textContent = String(event.target.files.length);
    });
    document.getElementById('finalPublish').addEventListener('click', () => {
      const output = document.getElementById('publishClickCount');
      output.textContent = String(Number(output.textContent || '0') + 1);
    });
  </script>
</body>
</html>
""",
            encoding="utf-8",
        )

    def tearDown(self):
        for path in (self.image_path, self.html_path):
            try:
                path.unlink()
            except OSError:
                pass

    def _build_task(self):
        return {
            "id": 199,
            "account_id": "account_001",
            "title": "本地浏览器发布测试商品",
            "description": "发布器会填写这段描述，但不会点最终发布",
            "price": "29.90",
            "category_keyword": "手机壳",
            "publish_url": self.html_path.as_uri(),
            "images_json_parsed": [{"image_url": "/static/uploads/images/publish_browser_flow_fixture.png"}],
        }

    def test_browser_flow_fills_local_publish_page_and_waits_for_manual_confirm(self):
        async def run_case():
            publisher = XianyuItemPublisher(self._build_task(), "a=1; b=2")
            publisher.headless = True
            try:
                with patch.object(publisher, "can_open_visible_browser", return_value=True), patch.object(
                    publisher,
                    "_try_upload_images_to_cdn",
                    new=AsyncMock(),
                ):
                    result = await publisher.start()

                self.assertTrue(result["success"])
                self.assertEqual(result["status"], "waiting_manual_confirm")
                self.assertEqual(await publisher.page.input_value("#title"), "本地浏览器发布测试商品")
                self.assertEqual(await publisher.page.input_value("#desc"), "发布器会填写这段描述，但不会点最终发布")
                self.assertEqual(await publisher.page.input_value("#price"), "29.90")
                self.assertEqual(await publisher.page.input_value("#category"), "手机壳")
                self.assertEqual(await publisher.page.text_content("#fileCount"), "1")
                self.assertEqual(await publisher.page.locator("#finalPublish").count(), 1)
                self.assertEqual(await publisher.page.text_content("#publishClickCount"), "0")
                notes = json.loads(result["notes"])
                self.assertEqual(notes["current_url"], self.html_path.as_uri())
                self.assertIn("diagnostics", notes)
                self.assertTrue(notes["diagnostics"]["upload_ok"])
                self.assertTrue(notes["diagnostics"]["desc_ok"])
                self.assertTrue(notes["diagnostics"]["price_ok"])
                self.assertTrue(notes["diagnostics"]["category_ok"])
            finally:
                screenshot = result.get("browser_screenshot", "") if "result" in locals() else ""
                if screenshot.startswith("/static/uploads/images/"):
                    try:
                        (self.project_root / screenshot.lstrip("/")).unlink()
                    except OSError:
                        pass
                await publisher.close()

        try:
            asyncio.run(run_case())
        except Exception as exc:
            message = str(exc)
            if "Executable doesn't exist" in message or "playwright install" in message:
                raise unittest.SkipTest("Playwright Chromium is not installed in this environment") from exc
            raise


class PublishFrontendStaticTest(unittest.TestCase):
    def setUp(self):
        self.project_root = Path(__file__).resolve().parents[1]
        self.index_html = (self.project_root / "static" / "index.html").read_text(encoding="utf-8")
        self.app_js = (self.project_root / "static" / "js" / "app.js").read_text(encoding="utf-8")

    def test_publish_image_uploader_supports_drag_drop_and_paste(self):
        self.assertIn("publishImageDropZone", self.index_html)
        self.assertIn("publishImageUploadHint", self.index_html)
        self.assertIn("Ctrl+V", self.index_html)
        self.assertIn("const PUBLISH_IMAGE_LIMIT = 9", self.app_js)
        self.assertIn("document.addEventListener('paste'", self.app_js)
        self.assertIn("dropZone.addEventListener('drop'", self.app_js)
        self.assertIn("addPublishImageFiles(Array.from(event.dataTransfer?.files || []), '拖拽')", self.app_js)
        self.assertIn("const files = [...publishImageFiles]", self.app_js)

    def test_waiting_manual_confirm_task_can_continue_active_browser(self):
        self.assertIn("'waiting_manual_confirm'].includes(task.status)", self.app_js)
        self.assertIn("继续填写", self.app_js)

    def test_waiting_task_restart_label_when_no_active_browser(self):
        self.assertIn("active_browser_available", self.app_js)
        self.assertIn("重新打开并填写", self.app_js)
        self.assertIn("浏览器连接已断开", self.app_js)

    def test_publish_task_detail_modal_exposes_diagnostics(self):
        self.assertIn("let itemPublishTasksCache = []", self.app_js)
        self.assertIn("function showItemPublishTaskDetail", self.app_js)
        self.assertIn("notes_parsed", self.app_js)
        self.assertIn("CDN 预上传结果", self.app_js)
        self.assertIn("发布器警告", self.app_js)
        self.assertIn("最近检测同步", self.app_js)
        self.assertIn("showItemPublishTaskDetail(${task.id})", self.app_js)

    def test_publish_task_detail_modal_shows_browser_diagnostics(self):
        self.assertIn("current_url", self.app_js)
        self.assertIn("page_title", self.app_js)
        self.assertIn("diagnostics", self.app_js)

    def test_waiting_manual_confirm_toast_surfaces_manual_action_error(self):
        self.assertIn("startData.task?.error_message", self.app_js)
        self.assertIn("data.task?.error_message", self.app_js)
        self.assertIn("发布页等待人工处理", self.app_js)


if __name__ == "__main__":
    unittest.main()
