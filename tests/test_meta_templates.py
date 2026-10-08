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
                "appointments": [
                    {
                        "id": "9001",
                        "start_at": "2026-10-08T16:00:00Z",
                        "end_at": "2026-10-08T22:00:00Z",
                        "customer": {"name": "Cliente X"},
                    }
                ],
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

    def send_booking_with_pdf(self):
        """Envia a escala com a 77Gestão devolvendo o PDF e a Meta aceitando o upload."""
        from nova_guarda.gestao77_service import send_booking_to_partner

        with patch("nova_guarda.gestao77_service.fetch_schedule_pdf", return_value=b"%PDF-1.4 escala"), patch(
            "nova_guarda.clients.whatsapp_official.WhatsAppOfficialClient.upload_media", return_value="media-1"
        ) as upload:
            send_booking_to_partner("880", self.phone)
        return upload

    def test_booking_goes_out_as_period_template_with_pdf_and_booking_id(self):
        self.accept_cooperator()
        self.create_booking()

        upload = self.send_booking_with_pdf()

        upload.assert_called_once()
        self.assertEqual(upload.call_args.args[0], b"%PDF-1.4 escala")
        message = self.sent[0]
        self.assertEqual(message["type"], "template")
        self.assertEqual(self.template(message)["name"], "nova_guarda_escala_periodo_v1")
        self.assertEqual(self.template(message)["language"], {"code": "pt_BR"})
        header = [c for c in self.template(message)["components"] if c["type"] == "header"][0]
        self.assertEqual(header["parameters"][0]["document"]["id"], "media-1")
        self.assertTrue(header["parameters"][0]["document"]["filename"].endswith(".pdf"))
        self.assertEqual(self.button_payloads(message), ["booking_confirm:880", "booking_decline:880"])
        self.assertEqual(self.body_texts(message), ["Cooperado Teste", "outubro/2026", "1 dia(s), de 08/10 a 08/10"])
        self.assertEqual(self.storage.get_booking("880")["whatsapp_message_id"], "wamid.1")

    def test_booking_inside_window_is_one_message_with_pdf_header_and_buttons(self):
        self.storage.set_settings({"meta_templates_enabled": "0"})
        self.accept_cooperator()
        self.create_booking()

        self.send_booking_with_pdf()

        message = self.sent[0]
        self.assertEqual(message["type"], "interactive")
        self.assertEqual(message["interactive"]["header"]["document"]["id"], "media-1")
        self.assertIn("escala de trabalho de outubro/2026", message["interactive"]["body"]["text"])
        self.assertIn("PDF", message["interactive"]["body"]["text"])
        self.assertNotIn("Horário:", message["interactive"]["body"]["text"])

    def test_booking_is_not_sent_when_the_schedule_pdf_fails(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.accept_cooperator()
        self.create_booking()

        with patch("nova_guarda.gestao77_service.fetch_schedule_pdf", side_effect=RuntimeError("77 sem PDF")):
            with self.assertRaises(RuntimeError):
                send_booking_to_partner("880", self.phone)

        self.assertEqual(self.sent, [])
        self.assertEqual(self.storage.get_booking("880")["local_status"], "pending")

    def test_template_button_reply_from_meta_confirms_the_booking(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.accept_cooperator()
        self.create_booking()
        send_booking_to_partner("880", self.phone)
        self.sent.clear()

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
        self.assertIn("Escala de outubro/2026 confirmada", self.sent[-1]["text"]["body"])
        self.assertIn("check-in em cada dia de trabalho", self.sent[-1]["text"]["body"])

    def test_checkin_and_checkout_templates_carry_the_appointment(self):
        from nova_guarda.gestao77_service import agenda_data_from_booking, send_checkin_to_partner, send_checkout_to_partner

        self.accept_cooperator()
        self.create_booking()
        self.storage.update_booking_local_status("880", "confirmed")
        booking = self.storage.get_booking("880")

        send_checkin_to_partner("9001", self.phone, agenda_data_from_booking(booking), booking_id="880")

        checkin = self.sent[-1]
        self.assertEqual(self.template(checkin)["name"], "nova_guarda_checkin_atendimento_v3")
        self.assertEqual(
            self.button_payloads(checkin),
            ["checkin2_arrived:9001", "checkin_late:9001", "checkin_not_going:9001"],
        )
        self.assertEqual(self.body_texts(checkin), ["Cooperado Teste", "08/10/2026", "13:00 às 19:00", "Cliente X"])

        self.storage.mark_appointment_location_pending("9001")
        self.storage.transition_appointment_checkin("9001")
        send_checkout_to_partner("9001", self.phone)

        checkout = self.sent[-1]
        self.assertEqual(self.template(checkout)["name"], "nova_guarda_checkout_atendimento_v3")
        self.assertEqual(self.button_payloads(checkout), ["checkout_confirm:9001"])
        self.assertEqual(self.body_texts(checkout), ["Cooperado Teste", "08/10/2026", "13:00 às 19:00", "Cliente X"])

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
