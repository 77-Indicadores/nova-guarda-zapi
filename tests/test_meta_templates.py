import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class MetaTemplatesTest(unittest.TestCase):
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
        from nova_guarda.routes import create_app
        from nova_guarda.state import AGENDA_STATE, RECEIVED_EVENTS, TERMS_STATE

        config.DATABASE_PATH = Path(self.database.name)
        storage.DATABASE_PATH = Path(self.database.name)
        config.DATABASE_URL = ""
        storage.DATABASE_URL = ""
        RECEIVED_EVENTS.clear()
        AGENDA_STATE.clear()
        TERMS_STATE.clear()

        self.services = services
        self.storage = storage
        self.app = create_app()
        self.client = self.app.test_client()
        self.phone = "5513999199293"
        storage.set_settings({"active_provider": "official", "meta_templates_enabled": "1"})

        # Provider oficial "de verdade", mas com a chamada HTTP capturada.
        self.sent: list[dict] = []

        def fake_post(_client, payload):
            self.sent.append(payload)
            return {"messages": [{"id": f"wamid.{len(self.sent)}"}]}

        services.DEV_FAKE_ZAPI = False
        self.post_patch = patch("nova_guarda.clients.whatsapp_official.WhatsAppOfficialClient._post", fake_post)
        self.post_patch.start()
        self.signature_patch = patch("nova_guarda.routes.WHATSAPP_APP_SECRET", "")
        self.signature_patch.start()

    def tearDown(self) -> None:
        self.post_patch.stop()
        self.signature_patch.stop()
        self.services.DEV_FAKE_ZAPI = True
        Path(self.database.name).unlink(missing_ok=True)

    def accept_cooperator(self):
        self.storage.upsert_cooperator(self.phone, "accepted", {"id": 597, "name": "Cooperado Teste", "active": 1})

    def create_booking(self):
        self.storage.upsert_booking(
            {
                "id": 597,
                "name": "Cooperado Teste",
                "booking_id": "880",
                "phone": self.phone,
                "address": "Rua Exemplo, 123",
                "first_appointment_id": "9001",
                "appointments": [{"id": "9001", "start_at": "2026-10-08T16:00:00Z", "customer": {"name": "Cliente X"}}],
            }
        )

    def template(self, payload):
        return payload["template"]

    def button_payloads(self, payload):
        return [
            component["parameters"][0]["payload"]
            for component in self.template(payload)["components"]
            if component["type"] == "button"
        ]

    def body_texts(self, payload):
        body = [c for c in self.template(payload)["components"] if c["type"] == "body"][0]
        return [parameter["text"] for parameter in body["parameters"]]

    def test_booking_goes_out_as_template_with_booking_id_in_buttons(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.accept_cooperator()
        self.create_booking()

        send_booking_to_partner("880", self.phone)

        message = self.sent[0]
        self.assertEqual(message["type"], "template")
        self.assertEqual(self.template(message)["name"], "nova_guarda_escala_confirmacao_v2")
        self.assertEqual(self.template(message)["language"], {"code": "pt_BR"})
        self.assertEqual(self.button_payloads(message), ["booking_confirm:880", "booking_decline:880"])
        self.assertEqual(
            self.body_texts(message),
            ["Cooperado Teste", "Rua Exemplo, 123", "08/10/2026", "13:00", "Cliente X"],
        )
        self.assertEqual(self.storage.get_booking("880")["whatsapp_message_id"], "wamid.1")

    def test_template_button_reply_from_meta_confirms_the_booking(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.accept_cooperator()
        self.create_booking()
        send_booking_to_partner("880", self.phone)

        self.client.post(
            "/webhook",
            json={
                "entry": [
                    {
                        "changes": [
                            {
                                "value": {
                                    "messages": [
                                        {
                                            "from": self.phone,
                                            "id": "wamid.in.1",
                                            "type": "button",
                                            "button": {"payload": "booking_confirm:880", "text": "Confirmar"},
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ]
            },
        )

        self.assertEqual(self.storage.get_booking("880")["local_status"], "confirmed")

    def test_checkin_and_checkout_templates_carry_the_appointment(self):
        from nova_guarda.gestao77_service import agenda_data_from_booking, send_checkin_to_partner, send_checkout_to_partner

        self.accept_cooperator()
        self.create_booking()
        self.storage.update_booking_local_status("880", "confirmed")
        booking = self.storage.get_booking("880")

        send_checkin_to_partner("9001", self.phone, agenda_data_from_booking(booking), booking_id="880")

        checkin = self.sent[-1]
        self.assertEqual(self.template(checkin)["name"], "nova_guarda_checkin_atendimento_v2")
        self.assertEqual(
            self.button_payloads(checkin),
            ["checkin2_arrived:9001", "checkin_late:9001", "checkin_not_going:9001"],
        )
        self.assertEqual(len(self.body_texts(checkin)), 5)

        self.storage.mark_appointment_location_pending("9001")
        self.storage.transition_appointment_checkin("9001")
        send_checkout_to_partner("9001", self.phone)

        checkout = self.sent[-1]
        self.assertEqual(self.template(checkout)["name"], "nova_guarda_checkout_atendimento_v3")
        self.assertEqual(self.button_payloads(checkout), ["checkout_confirm:9001"])
        self.assertEqual(self.body_texts(checkout), ["Cooperado Teste", "08/10/2026", "13:00", "Cliente X"])

    def test_terms_outside_window_use_only_the_template(self):
        from nova_guarda.onboarding import send_terms_to_phone

        result = send_terms_to_phone(self.phone, {"id": 597, "name": "Cooperado Teste", "active": 1})

        self.assertTrue(result["ok"])
        self.assertEqual([message["type"] for message in self.sent], ["template"])
        self.assertEqual(self.template(self.sent[0])["name"], "nova_guarda_ativacao_termo")
        self.assertEqual(self.button_payloads(self.sent[0]), ["terms_accept", "terms_reject"])

    def test_terms_inside_window_keep_pdf_and_buttons(self):
        from nova_guarda.onboarding import send_terms_to_phone
        from nova_guarda.timezone import br_timestamp

        self.storage.save_conversation_event(
            {"received_at": br_timestamp(), "payload": {"type": "ReceivedCallback", "phone": self.phone}}
        )

        with patch.dict(os.environ, {"TERMS_DOCUMENT_URL": "https://example.test/termo.pdf"}):
            send_terms_to_phone(self.phone, {"id": 597, "name": "Cooperado Teste", "active": 1})

        self.assertEqual([message["type"] for message in self.sent], ["text", "document", "interactive"])

    def test_templates_disabled_keeps_interactive_messages(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.storage.set_settings({"meta_templates_enabled": "0"})
        self.accept_cooperator()
        self.create_booking()

        send_booking_to_partner("880", self.phone)

        self.assertEqual(self.sent[0]["type"], "interactive")


if __name__ == "__main__":
    unittest.main()
