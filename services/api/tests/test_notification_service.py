import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.domains.notification.service import queue_notification_audit, send_wecom_robot_notification
from app.models import User


class NotificationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", future=True)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)
        Base.metadata.create_all(self.engine)
        self.db = self.session_factory()
        self.user = User(
            email="han.teacher@example.com",
            display_name="韩老师",
            password_hash="hashed-password",
            is_active=True,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _create_audit(self):
        return queue_notification_audit(
            self.db,
            user_id=self.user.id,
            reminder_job_id=None,
            channel="wecom_robot",
            provider="wecom_robot",
            subject="Synora 提醒：教学例会",
            recipient="企业微信群机器人",
            payload={"markdown": "**Synora 日程提醒**", "body": "测试提醒"},
        )

    @patch("app.domains.notification.service.get_settings", return_value=SimpleNamespace(wecom_robot_webhook=""))
    def test_wecom_notification_fails_when_webhook_missing(self, _settings_mock) -> None:
        audit = self._create_audit()

        updated = send_wecom_robot_notification(self.db, audit.id)

        self.assertEqual(updated.status, "failed")
        self.assertEqual(updated.retry_count, 1)
        self.assertEqual(updated.error_message, "未配置企业微信群机器人 Webhook。")

    @patch("app.domains.notification.service.get_settings", return_value=SimpleNamespace(wecom_robot_webhook="https://example.com/webhook"))
    @patch("app.domains.notification.service.httpx.post")
    def test_wecom_notification_marks_delivered_when_errcode_zero(self, post_mock: Mock, _settings_mock) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        post_mock.return_value = response
        audit = self._create_audit()

        updated = send_wecom_robot_notification(self.db, audit.id)

        self.assertEqual(updated.status, "delivered")
        self.assertEqual(updated.retry_count, 0)
        self.assertIsNotNone(updated.delivered_at)
        self.assertEqual(updated.external_id, f"wecom-{audit.id}")
        self.assertEqual(post_mock.call_args.args[0], "https://example.com/webhook")

    @patch("app.domains.notification.service.get_settings", return_value=SimpleNamespace(wecom_robot_webhook="https://global.example.com/webhook"))
    @patch("app.domains.notification.service.httpx.post")
    def test_wecom_notification_prefers_user_webhook(self, post_mock: Mock, _settings_mock) -> None:
        self.user.wecom_robot_webhook = "https://user.example.com/webhook"
        self.db.commit()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        post_mock.return_value = response
        audit = self._create_audit()

        updated = send_wecom_robot_notification(self.db, audit.id)

        self.assertEqual(updated.status, "delivered")
        self.assertEqual(post_mock.call_args.args[0], "https://user.example.com/webhook")

    @patch("app.domains.notification.service.get_settings", return_value=SimpleNamespace(wecom_robot_webhook="https://example.com/webhook"))
    @patch("app.domains.notification.service.httpx.post")
    def test_wecom_notification_fails_when_errcode_non_zero(self, post_mock: Mock, _settings_mock) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"errcode": 93000, "errmsg": "invalid webhook url"}
        post_mock.return_value = response
        audit = self._create_audit()

        updated = send_wecom_robot_notification(self.db, audit.id)

        self.assertEqual(updated.status, "failed")
        self.assertEqual(updated.retry_count, 1)
        self.assertEqual(updated.error_message, "企业微信群机器人返回错误码 93000：invalid webhook url")

    @patch("app.domains.notification.service.get_settings", return_value=SimpleNamespace(wecom_robot_webhook="https://example.com/webhook"))
    @patch("app.domains.notification.service.httpx.post", side_effect=httpx.TimeoutException("timed out"))
    def test_wecom_notification_fails_on_timeout(self, _post_mock, _settings_mock) -> None:
        audit = self._create_audit()

        updated = send_wecom_robot_notification(self.db, audit.id)

        self.assertEqual(updated.status, "failed")
        self.assertEqual(updated.retry_count, 1)
        self.assertEqual(updated.error_message, "企业微信群机器人请求超时。")

    @patch("app.domains.notification.service.get_settings", return_value=SimpleNamespace(wecom_robot_webhook="https://example.com/webhook"))
    @patch(
        "app.domains.notification.service.httpx.post",
        side_effect=httpx.RequestError("boom", request=httpx.Request("POST", "https://example.com/webhook")),
    )
    def test_wecom_notification_fails_on_network_error(self, _post_mock, _settings_mock) -> None:
        audit = self._create_audit()

        updated = send_wecom_robot_notification(self.db, audit.id)

        self.assertEqual(updated.status, "failed")
        self.assertEqual(updated.retry_count, 1)
        self.assertEqual(updated.error_message, "企业微信群机器人网络请求失败。")

    @patch("app.domains.notification.service.get_settings", return_value=SimpleNamespace(wecom_robot_webhook="https://example.com/webhook"))
    @patch("app.domains.notification.service.httpx.post")
    def test_wecom_notification_fails_on_invalid_json(self, post_mock: Mock, _settings_mock) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
        post_mock.return_value = response
        audit = self._create_audit()

        updated = send_wecom_robot_notification(self.db, audit.id)

        self.assertEqual(updated.status, "failed")
        self.assertEqual(updated.retry_count, 1)
        self.assertEqual(updated.error_message, "企业微信群机器人返回了无法解析的响应。")


if __name__ == "__main__":
    unittest.main()
