import asyncio
import unittest
from unittest import mock

import config
from XianyuAutoAsync import ConnectionState, XianyuLive


class _FakeTokenRefreshResponse:
    def __init__(self):
        self.status = 200
        self.headers = {}
        self.json_content_type = object()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        self.json_content_type = content_type
        return {
            "ret": ["SUCCESS::调用成功"],
            "data": {
                "accessToken": "oauth_access_token",
            },
        }


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.post_calls = []

    def post(self, *args, **kwargs):
        self.post_calls.append(
            {
                "args": args,
                "kwargs": kwargs,
            }
        )
        return self.response


class XianyuTokenRefreshRequestTest(unittest.IsolatedAsyncioTestCase):
    def _make_notification_live(self):
        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "status_notice_test"
        live.notification_lock = asyncio.Lock()
        live.last_notification_time = {}
        live.pending_notification_keys = set()
        live.message_stream_notification_cooldown = 60
        live.token_refresh_notification_cooldown = 18000
        live.notification_cooldown = 60
        live._safe_str = lambda exc: str(exc)
        return live

    async def test_status_token_notifications_are_silent(self):
        live = self._make_notification_live()
        status_types = [
            "token_refresh",
            "cookie_refresh_success",
            "password_login_success",
            "slider_success",
            "slider_recovered_success",
        ]

        with mock.patch("XianyuAutoAsync.dispatch_account_notifications", return_value=True) as dispatch_mock:
            for notification_type in status_types:
                await live.send_token_refresh_notification(
                    "刷新Cookie成功" if notification_type == "cookie_refresh_success" else "Token刷新异常",
                    notification_type,
                )
            await live.send_token_refresh_notification(
                "扫码登录稳定期内，自动密码登录刷新已跳过",
                "token_refresh",
            )

        dispatch_mock.assert_not_awaited()
        self.assertEqual(live.last_notification_time, {})
        self.assertEqual(live.pending_notification_keys, set())

    async def test_manual_verification_token_notification_still_dispatches(self):
        live = self._make_notification_live()

        with mock.patch("XianyuAutoAsync.dispatch_account_notifications", return_value=True) as dispatch_mock:
            await live.send_token_refresh_notification(
                "需要二维码验证",
                "token_refresh",
                verification_url="https://passport.goofish.com/mini_login.htm?verify=true",
                verification_type="qr_verify",
            )

        dispatch_mock.assert_awaited_once()
        self.assertEqual(dispatch_mock.call_args.kwargs["notification_type"], "token_refresh")
        self.assertIn("需要二维码验证", dispatch_mock.call_args.args[1])

    async def test_refresh_token_reuses_session_and_passes_proxy(self):
        fake_response = _FakeTokenRefreshResponse()
        fake_session = _FakeSession(fake_response)

        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "token_refresh_proxy_test"
        live.session = fake_session
        live._http_proxy_url = "http://127.0.0.1:8888"
        live.device_id = "device-id"
        live.cookies_str = "_m_h5_tk=test_token_12345; cookie2=dummy_cookie2"
        live.current_token = None
        live.last_token_refresh_time = 0
        live.last_message_received_time = 123
        live.message_cookie_refresh_cooldown = 0
        live.max_captcha_verification_count = 3
        live.last_token_refresh_status = None
        live.last_token_refresh_error_message = None
        live.restarted_in_browser_refresh = True
        live.init_auth_failures = 2
        live.last_init_failure_reason = "old_reason"
        live.last_init_failure_type = "old_type"
        live._skip_db_cookie_reload_for_token_refresh = True

        create_session_called = False

        async def fake_create_session():
            nonlocal create_session_called
            create_session_called = True

        live.create_session = fake_create_session
        live._reload_latest_cookies_from_db = lambda *_args, **_kwargs: None
        live._extract_set_cookie_updates = lambda headers: {}
        live._build_cookie_string_with_updates = lambda cookie_string, updates: cookie_string
        live._need_captcha_verification = lambda _payload: False
        live._consume_pending_slider_success_notice = lambda: False
        live.clear_qr_login_grace = lambda *_args, **_kwargs: None
        live.clear_init_auth_failure_state = lambda *_args, **_kwargs: None

        async def fail_send_notification(*_args, **_kwargs):
            raise AssertionError("success path should not send token refresh notification")

        live.send_token_refresh_notification = fail_send_notification

        token = await live._refresh_token_impl(allow_password_login_recovery=False)

        self.assertEqual(token, "oauth_access_token")
        self.assertFalse(create_session_called)
        self.assertEqual(live.current_token, "oauth_access_token")
        self.assertEqual(live.last_token_refresh_status, "success")
        self.assertIsNone(live.last_token_refresh_error_message)
        self.assertEqual(live.last_message_received_time, 0)
        self.assertEqual(len(fake_session.post_calls), 1)
        request = fake_session.post_calls[0]
        self.assertEqual(request["kwargs"]["proxy"], "http://127.0.0.1:8888")
        self.assertEqual(fake_response.json_content_type, None)

    def test_conservative_keepalive_config_defaults_to_three_day_token_refresh(self):
        self.assertEqual(config.TOKEN_REFRESH_INTERVAL, 259200)
        self.assertEqual(config.TOKEN_RETRY_INTERVAL, 3600)
        self.assertEqual(config.SESSION_KEEPALIVE_INTERVAL, 600)
        self.assertEqual(config.RISK_CONTROL.get("qr_login_grace_minutes"), 30)

    async def test_session_keepalive_cookie_updates_preserve_existing_protected_fields(self):
        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "session_keepalive_cookie_merge_test"
        live.cookies = {
            "unb": "account-1",
            "sgcookie": "existing_sgcookie",
            "cookie2": "existing_cookie2",
            "_m_h5_tk": "existing_token_123",
            "_m_h5_tk_enc": "existing_token_enc",
            "t": "existing_t",
            "cna": "existing_cna",
        }
        live.cookies_str = live._serialize_cookies(live.cookies)
        live.session = None
        live.myid = "account-1"
        live.device_id = "device-id"

        persisted = []

        async def fake_update_config_cookies():
            persisted.append(live.cookies_str)

        live.update_config_cookies = fake_update_config_cookies

        changed = await live._apply_response_cookie_updates(
            {
                "Set-Cookie": [
                    "cookie2=; Path=/; Domain=.goofish.com",
                    "x5sec=fresh_x5sec; Path=/; Domain=.goofish.com",
                ]
            },
            "session_keepalive",
        )

        self.assertTrue(changed)
        self.assertEqual(live.cookies["cookie2"], "existing_cookie2")
        self.assertEqual(live.cookies["x5sec"], "fresh_x5sec")
        self.assertEqual(len(persisted), 1)

    async def test_handle_captcha_verification_marks_slider_scene_as_token_refresh(self):
        created_sliders = []

        class _FakeSlider:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                self.risk_trigger_scene = None
                created_sliders.append(self)

            async def async_run(self, verification_url):
                self.verification_url = verification_url
                return False, None

        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "token_refresh_captcha_scene_test"
        live.cookies_str = "_m_h5_tk=test_token_12345; cookie2=dummy_cookie2"
        live.proxy_config = {}
        live.connection_state = ConnectionState.DISCONNECTED
        live.ws = None
        live._safe_str = lambda exc: str(exc)

        async def fake_send_notification(*_args, **_kwargs):
            return None

        live.send_token_refresh_notification = fake_send_notification

        with mock.patch("XianyuAutoAsync.db_manager.get_cookie_details", return_value={}), \
             mock.patch("XianyuAutoAsync.log_captcha_event"), \
             mock.patch("utils.xianyu_slider_stealth.XianyuSliderStealth", _FakeSlider):
            result = await live._handle_captcha_verification(
                {"data": {"url": "https://example.com/punish?action=captcha"}}
            )

        self.assertIsNone(result)
        self.assertEqual(len(created_sliders), 1)
        self.assertEqual(created_sliders[0].risk_trigger_scene, "token_refresh")

    async def test_handle_captcha_verification_enables_account_persistent_profile_for_token_refresh(self):
        created_sliders = []

        class _FakeSlider:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                self.risk_trigger_scene = None
                created_sliders.append(self)

            async def async_run(self, verification_url):
                self.verification_url = verification_url
                return False, None

        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "token_refresh_persistent_profile_test"
        live.cookies_str = "_m_h5_tk=test_token_12345; cookie2=dummy_cookie2"
        live.proxy_config = {}
        live.connection_state = ConnectionState.DISCONNECTED
        live.ws = None
        live._safe_str = lambda exc: str(exc)

        async def fake_send_notification(*_args, **_kwargs):
            return None

        live.send_token_refresh_notification = fake_send_notification

        with mock.patch("XianyuAutoAsync.db_manager.get_cookie_details", return_value={}), \
             mock.patch("XianyuAutoAsync.log_captcha_event"), \
             mock.patch("utils.xianyu_slider_stealth.XianyuSliderStealth", _FakeSlider):
            result = await live._handle_captcha_verification(
                {"data": {"url": "https://example.com/punish?action=captcha"}}
            )

        self.assertIsNone(result)
        self.assertEqual(len(created_sliders), 1)
        self.assertTrue(created_sliders[0].kwargs.get("use_account_persistent_profile"))

    async def test_password_login_refresh_marks_missing_credentials(self):
        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "missing_credentials_test"
        live.cookies_str = "cookie2=old_cookie"
        live.last_token_refresh_status = None
        live.last_token_refresh_error_message = None
        live._safe_str = lambda exc: str(exc)
        live._normalize_risk_trigger_scene = lambda *_args, **_kwargs: "token_refresh"
        live._build_risk_event_meta = lambda **kwargs: kwargs
        live._create_risk_log = lambda **_kwargs: "risk-log-id"
        live._update_risk_log = mock.Mock()
        live.is_manual_refresh_active = lambda *_args, **_kwargs: False
        live._is_account_pause_status = lambda *_args, **_kwargs: False
        live._should_defer_auth_recovery_for_qr_grace = lambda: False
        live._get_active_password_login_failure_backoff = lambda *_args, **_kwargs: None
        live.send_token_refresh_notification = mock.AsyncMock()

        with mock.patch("XianyuAutoAsync.db_manager.mark_stale_risk_control_logs_failed", return_value=0), \
             mock.patch("XianyuAutoAsync.db_manager.get_cookie_details", return_value={
                 "cookie_value": "cookie2=old_cookie",
                 "username": "",
                 "password": "",
                 "show_browser": False,
             }), \
             mock.patch("XianyuAutoAsync.log_captcha_event"), \
             mock.patch.object(XianyuLive, "acquire_auth_recovery_lock", return_value=(True, None)), \
             mock.patch.object(XianyuLive, "release_auth_recovery_lock") as release_lock:
            result = await live._try_password_login_refresh("令牌/Session过期")

        self.assertFalse(result)
        self.assertEqual(live.last_token_refresh_status, "missing_credentials")
        self.assertEqual(live.last_token_refresh_error_message, "未配置用户名或密码，无法自动刷新Cookie")
        live.send_token_refresh_notification.assert_awaited_once()
        live._update_risk_log.assert_called()
        release_lock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
