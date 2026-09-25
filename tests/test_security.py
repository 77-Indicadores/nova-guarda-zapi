import hashlib
import hmac
import os
import tempfile
import unittest
from pathlib import Path


class ApiRequiresAuthenticationTest(unittest.TestCase):
    def setUp(self):
        self.database = tempfile.NamedTemporaryFile(delete=False)
        self.database.close()
        os.environ["DATABASE_PATH"] = self.database.name
        os.environ["DEV_FAKE_ZAPI"] = "false"
        os.environ["DEV_FAKE_GESTAO77"] = "true"
        os.environ["WHATSAPP_PROVIDER"] = "zapi"
        os.environ.pop("WHATSAPP_APP_SECRET", None)

        import nova_guarda.config as config
        import nova_guarda.services as services
        import nova_guarda.storage as storage
        from nova_guarda.app import create_app
        from nova_guarda.state import AGENDA_STATE, RECEIVED_EVENTS, TERMS_STATE

        config.DATABASE_PATH = Path(self.database.name)
        storage.DATABASE_PATH = Path(self.database.name)
        config.DATABASE_URL = ""
        storage.DATABASE_URL = ""
        config.DEV_FAKE_ZAPI = False
        services.DEV_FAKE_ZAPI = False

        RECEIVED_EVENTS.clear()
        AGENDA_STATE.clear()
        TERMS_STATE.clear()

        self.storage = storage
        self.app = create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        Path(self.database.name).unlink(missing_ok=True)

    def test_send_message_without_session_is_rejected(self):
        response = self.client.post(
            "/api/send-message",
            json={"phone": "5513999199293", "message": "oi"},
        )
        self.assertEqual(response.status_code, 401)

    def test_booking_send_without_session_is_rejected(self):
        response = self.client.post(
            "/api/bookings/booking-1/send",
            json={"phone": "5513999199293"},
        )
        self.assertEqual(response.status_code, 401)

    def test_retry_pending_syncs_without_session_is_rejected(self):
        response = self.client.post("/api/gestao77/retry-pending-syncs")
        self.assertEqual(response.status_code, 401)

    def test_events_without_session_is_rejected(self):
        response = self.client.get("/api/events")
        self.assertEqual(response.status_code, 401)

    def test_dashboard_without_session_redirects_to_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_dev_simulate_whatsapp_is_blocked_when_dev_fake_zapi_disabled(self):
        response = self.client.post(
            "/dev/simulate-whatsapp",
            json={"phone": "5513999199293", "message": "ativar"},
        )
        self.assertEqual(response.status_code, 302)

    def test_api_accessible_after_login(self):
        self.client.post("/login", data={"username": "admin", "password": "admin"})
        response = self.client.get("/api/events")
        self.assertEqual(response.status_code, 200)


class WebhookSignatureTest(unittest.TestCase):
    def setUp(self):
        self.database = tempfile.NamedTemporaryFile(delete=False)
        self.database.close()
        os.environ["DATABASE_PATH"] = self.database.name
        os.environ["DEV_FAKE_ZAPI"] = "true"
        os.environ["DEV_FAKE_GESTAO77"] = "true"
        os.environ["WHATSAPP_PROVIDER"] = "zapi"
        os.environ["WHATSAPP_APP_SECRET"] = "test-secret"

        import nova_guarda.config as config
        import nova_guarda.services as services
        import nova_guarda.storage as storage
        from nova_guarda.app import create_app
        from nova_guarda.state import AGENDA_STATE, RECEIVED_EVENTS, TERMS_STATE

        config.DATABASE_PATH = Path(self.database.name)
        storage.DATABASE_PATH = Path(self.database.name)
        config.DATABASE_URL = ""
        storage.DATABASE_URL = ""
        config.DEV_FAKE_ZAPI = True
        services.DEV_FAKE_ZAPI = True
        config.WHATSAPP_APP_SECRET = "test-secret"

        import nova_guarda.routes as routes
        routes.WHATSAPP_APP_SECRET = "test-secret"

        RECEIVED_EVENTS.clear()
        AGENDA_STATE.clear()
        TERMS_STATE.clear()

        self.storage = storage
        self.app = create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        import nova_guarda.config as config
        import nova_guarda.routes as routes

        os.environ.pop("WHATSAPP_APP_SECRET", None)
        config.WHATSAPP_APP_SECRET = ""
        routes.WHATSAPP_APP_SECRET = ""
        Path(self.database.name).unlink(missing_ok=True)

    def signed_headers(self, body: bytes) -> dict:
        digest = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
        return {"X-Hub-Signature-256": f"sha256={digest}"}

    def test_webhook_without_signature_is_rejected(self):
        response = self.client.post("/webhook", json={"type": "ReceivedCallback", "phone": "5513999199293"})
        self.assertEqual(response.status_code, 403)

    def test_webhook_with_invalid_signature_is_rejected(self):
        response = self.client.post(
            "/webhook",
            json={"type": "ReceivedCallback", "phone": "5513999199293"},
            headers={"X-Hub-Signature-256": "sha256=deadbeef"},
        )
        self.assertEqual(response.status_code, 403)

    def test_webhook_with_valid_signature_is_accepted(self):
        import json

        body = json.dumps({"type": "ReceivedCallback", "phone": "5513999199293", "fromMe": True}).encode()
        response = self.client.post(
            "/webhook",
            data=body,
            content_type="application/json",
            headers=self.signed_headers(body),
        )
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
