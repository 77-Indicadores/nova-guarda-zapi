import os
import tempfile
import unittest
from pathlib import Path


class OnboardingFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database = tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite3")
        self.database.close()
        os.environ["DATABASE_PATH"] = self.database.name
        os.environ["DEV_FAKE_ZAPI"] = "true"
        os.environ["DEV_FAKE_GESTAO77"] = "true"
        os.environ["WHATSAPP_PROVIDER"] = "zapi"

        import nova_guarda.config as config
        import nova_guarda.services as services
        import nova_guarda.storage as storage

        config.DATABASE_PATH = Path(self.database.name)
        storage.DATABASE_PATH = Path(self.database.name)
        config.DEV_FAKE_ZAPI = True
        services.DEV_FAKE_ZAPI = True

        from nova_guarda.routes import create_app
        from nova_guarda.state import RECEIVED_EVENTS, TERMS_STATE

        RECEIVED_EVENTS.clear()
        TERMS_STATE.clear()
        self.app = create_app()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        Path(self.database.name).unlink(missing_ok=True)

    def send_message(self, phone: str, message: str):
        return self.client.post(
            "/dev/simulate-whatsapp",
            json={"phone": phone, "message": message},
        )

    def cooperator(self, phone: str):
        from nova_guarda.messages import normalize_phone
        from nova_guarda.storage import get_cooperator

        return get_cooperator(normalize_phone(phone))

    def last_auto_reply_text(self) -> str:
        from nova_guarda.state import RECEIVED_EVENTS

        for event in RECEIVED_EVENTS:
            payload = event.get("payload", {})
            if payload.get("type") == "AutoReply":
                return payload.get("text", {}).get("message", "")
        return ""

    def test_activation_sends_terms_and_persists_terms_sent(self):
        response = self.send_message("13 99919-9293", "ativar")

        self.assertEqual(response.status_code, 200)
        cooperator = self.cooperator("13 99919-9293")
        self.assertIsNotNone(cooperator)
        self.assertEqual(cooperator["phone"], "5513999199293")
        self.assertEqual(cooperator["onboarding_status"], "terms_sent")
        self.assertEqual(cooperator["partner_name"], "Cooperado Teste")

    def test_terms_acceptance_activates_cooperator(self):
        self.send_message("13 99919-9293", "ativar")
        response = self.send_message("13 99919-9293", "terms_accept")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.cooperator("13 99919-9293")["onboarding_status"], "accepted")
        self.assertIn("ativo", self.last_auto_reply_text())

    def test_terms_rejection_blocks_cooperator_and_reentry(self):
        self.send_message("13 99919-9293", "ativar")
        self.send_message("13 99919-9293", "terms_reject")
        response = self.send_message("13 99919-9293", "ativar")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.cooperator("13 99919-9293")["onboarding_status"], "rejected")
        self.assertIn("bloqueado", self.last_auto_reply_text())

    def test_terms_sent_reentry_does_not_duplicate_and_resends_terms(self):
        self.send_message("13 99919-9293", "ativar")
        first = self.cooperator("13 99919-9293")
        response = self.send_message("13 99919-9293", "iniciar")
        second = self.cooperator("13 99919-9293")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(second["onboarding_status"], "terms_sent")
        self.assertEqual(first["created_at"], second["created_at"])

    def test_accepted_reentry_answers_already_active(self):
        self.send_message("13 99919-9293", "ativar")
        self.send_message("13 99919-9293", "terms_accept")
        response = self.send_message("13 99919-9293", "começar")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.cooperator("13 99919-9293")["onboarding_status"], "accepted")
        self.assertIn("já está ativo", self.last_auto_reply_text())

    def test_not_found_phone_is_not_activated(self):
        response = self.send_message("13 99999-0000", "ativar")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.cooperator("13 99999-0000")["onboarding_status"], "not_started")
        self.assertIn("Não encontrei", self.last_auto_reply_text())

    def test_inactive_partner_is_not_activated(self):
        response = self.send_message("13 99999-1111", "ativar")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.cooperator("13 99999-1111")["onboarding_status"], "not_started")
        self.assertIn("não está ativo", self.last_auto_reply_text())

    def test_operational_route_is_blocked_without_acceptance(self):
        response = self.client.post(
            "/api/send-message",
            json={"phone": "5513999199293", "mode": "agenda", "client_name": "Teste"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("ativar", response.get_json()["error"])

    def test_operational_route_is_allowed_after_acceptance(self):
        self.send_message("13 99919-9293", "ativar")
        self.send_message("13 99919-9293", "terms_accept")
        response = self.client.post(
            "/api/send-message",
            json={"phone": "5513999199293", "mode": "agenda", "client_name": "Teste"},
        )

        self.assertEqual(response.status_code, 200)

    def test_meta_cloud_payload_is_normalized_for_activation(self):
        response = self.client.post(
            "/webhook",
            json={
                "entry": [
                    {
                        "changes": [
                            {
                                "value": {
                                    "contacts": [{"profile": {"name": "Cooperado Teste"}}],
                                    "messages": [
                                        {
                                            "from": "5513999199293",
                                            "type": "text",
                                            "text": {"body": "ativar"},
                                        }
                                    ],
                                }
                            }
                        ]
                    }
                ]
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.cooperator("5513999199293")["onboarding_status"], "terms_sent")


if __name__ == "__main__":
    unittest.main()
