import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class BookingFlowTest(unittest.TestCase):
    def setUp(self):
        self.database = tempfile.NamedTemporaryFile(delete=False)
        self.database.close()
        os.environ["DATABASE_PATH"] = self.database.name
        os.environ["DEV_FAKE_ZAPI"] = "true"
        os.environ["DEV_FAKE_GESTAO77"] = "true"
        os.environ["WHATSAPP_PROVIDER"] = "zapi"

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

        RECEIVED_EVENTS.clear()
        AGENDA_STATE.clear()
        TERMS_STATE.clear()

        self.storage = storage
        self.app = create_app()
        self.client = self.app.test_client()
        self.client.post("/login", data={"username": "admin", "password": "admin"})
        self.phone = "5513999199293"
        self.booking_id = "booking-1"

    def tearDown(self):
        Path(self.database.name).unlink(missing_ok=True)

    def accept_cooperator(self):
        self.storage.upsert_cooperator(
            self.phone,
            "accepted",
            {
                "id": 597,
                "name": "Cooperado Teste",
                "active": 1,
                "type": "cooperado",
                "phones": [{"country_code": "+55", "number": "13999199293"}],
            },
        )

    def create_booking(self, booking_id=None):
        return self.storage.upsert_booking(
            {
                "id": 597,
                "name": "Cooperado Teste",
                "booking_id": booking_id or self.booking_id,
                "schedule_status": "awaiting_approval",
                "phone": self.phone,
                "appointments": [
                    {
                        "id": "appointment-1",
                        "start_at": "2026-08-30T12:00:00Z",
                        "customer": {"name": "Cliente Teste"},
                    }
                ],
            }
        )

    def post_booking_send(self, booking_id=None):
        return self.client.post(
            f"/api/bookings/{booking_id or self.booking_id}/send",
            json={"phone": self.phone},
        )

    def send_reply(self, message):
        return self.client.post(
            "/webhook",
            json={
                "type": "ReceivedCallback",
                "fromMe": False,
                "isGroup": False,
                "phone": self.phone,
                "chatName": "Cooperado Teste",
                "senderName": "Cooperado Teste",
                "text": {"message": message},
            },
        )

    def sync_count(self, status):
        with self.storage.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM sync_events
                WHERE entity_type = 'booking'
                  AND entity_id = ?
                  AND action = ?
                  AND ok = 1
                """,
                (self.booking_id, f"schedule_response:{status}"),
            ).fetchone()
        return row["total"]

    def failed_sync_count(self, status):
        with self.storage.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM sync_events
                WHERE entity_type = 'booking'
                  AND entity_id = ?
                  AND action = ?
                  AND ok = 0
                """,
                (self.booking_id, f"schedule_response:{status}"),
            ).fetchone()
        return row["total"]

    def test_cooperator_without_accepted_does_not_receive_booking(self):
        self.storage.upsert_cooperator(self.phone, "terms_sent", {"id": 597, "name": "Cooperado Teste"})
        self.create_booking()

        response = self.post_booking_send()

        self.assertEqual(response.status_code, 403)
        booking = self.storage.get_booking(self.booking_id)
        self.assertEqual(booking["local_status"], "pending")

    def test_successful_booking_send_generates_sent_after_whatsapp_success(self):
        self.accept_cooperator()
        self.create_booking()

        response = self.post_booking_send()

        self.assertEqual(response.status_code, 200)
        booking = self.storage.get_booking(self.booking_id)
        self.assertEqual(booking["local_status"], "sent")
        self.assertEqual(booking["gestao77_status"], "sent")
        self.assertEqual(booking["provider"], "zapi")
        self.assertTrue(booking["sent_at"])
        self.assertEqual(self.sync_count("sent"), 1)

    def test_provider_failure_does_not_generate_sent(self):
        self.accept_cooperator()
        self.create_booking()

        with patch("nova_guarda.gestao77_service.send_zapi_agenda_buttons", side_effect=RuntimeError("provider fora")):
            response = self.post_booking_send()

        self.assertEqual(response.status_code, 502)
        booking = self.storage.get_booking(self.booking_id)
        self.assertEqual(booking["local_status"], "pending")
        self.assertIsNone(booking.get("sent_at"))
        self.assertEqual(self.sync_count("sent"), 0)

    def test_gestao77_sent_failure_keeps_local_sent_for_retry(self):
        self.accept_cooperator()
        self.create_booking()
        client = Mock()
        client.update_booking_schedule_response.side_effect = RuntimeError("77 fora")

        with patch("nova_guarda.gestao77_service.fake_gestao77_enabled", return_value=False), patch(
            "nova_guarda.gestao77_service.Gestao77Client.from_env",
            return_value=client,
        ):
            response = self.post_booking_send()

        self.assertEqual(response.status_code, 502)
        booking = self.storage.get_booking(self.booking_id)
        self.assertEqual(booking["local_status"], "sent")
        self.assertIsNone(booking.get("gestao77_status"))
        self.assertEqual(self.failed_sync_count("sent"), 1)

    def test_confirmation_generates_confirmed(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_booking_send()

        response = self.send_reply("booking_confirm")

        self.assertEqual(response.status_code, 200)
        booking = self.storage.get_booking(self.booking_id)
        self.assertEqual(booking["local_status"], "confirmed")
        self.assertEqual(booking["gestao77_status"], "confirmed")
        self.assertTrue(booking["answered_at"])
        self.assertEqual(self.sync_count("confirmed"), 1)

    def test_gestao77_confirmation_failure_keeps_local_confirmed_for_retry(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_booking_send()
        client = Mock()
        client.update_booking_schedule_response.side_effect = RuntimeError("77 fora")

        with patch("nova_guarda.gestao77_service.fake_gestao77_enabled", return_value=False), patch(
            "nova_guarda.gestao77_service.Gestao77Client.from_env",
            return_value=client,
        ):
            response = self.send_reply("booking_confirm")

        self.assertEqual(response.status_code, 200)
        booking = self.storage.get_booking(self.booking_id)
        self.assertEqual(booking["local_status"], "confirmed")
        self.assertEqual(booking["gestao77_status"], "sent")
        self.assertEqual(self.failed_sync_count("confirmed"), 1)

    def test_decline_generates_declined(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_booking_send()

        response = self.send_reply("booking_decline")

        self.assertEqual(response.status_code, 200)
        booking = self.storage.get_booking(self.booking_id)
        self.assertEqual(booking["local_status"], "declined")
        self.assertEqual(booking["gestao77_status"], "declined")
        self.assertEqual(self.sync_count("declined"), 1)

    def test_duplicate_event_does_not_duplicate_post(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_booking_send()

        self.send_reply("booking_confirm")
        self.send_reply("booking_confirm")

        booking = self.storage.get_booking(self.booking_id)
        self.assertEqual(booking["local_status"], "confirmed")
        self.assertEqual(self.sync_count("confirmed"), 1)

    def test_missing_booking_returns_error(self):
        self.accept_cooperator()

        response = self.post_booking_send("missing-booking")

        self.assertEqual(response.status_code, 502)
        self.assertIn("não encontrado", response.get_json()["error"])

    def test_gestao77_unavailable_on_pending_bookings_returns_502(self):
        with patch("nova_guarda.gestao77_service.Gestao77Client.from_env", side_effect=RuntimeError("77 fora")):
            response = self.client.post("/api/gestao77/pending-bookings", json={"month": 8, "year": 2026})

        self.assertEqual(response.status_code, 502)
        self.assertIn("77 fora", response.get_json()["error"])

    def test_out_of_order_response_does_not_change_final_status(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_booking_send()

        self.send_reply("booking_confirm")
        self.send_reply("booking_decline")

        booking = self.storage.get_booking(self.booking_id)
        self.assertEqual(booking["local_status"], "confirmed")
        self.assertEqual(booking["gestao77_status"], "confirmed")
        self.assertEqual(self.sync_count("confirmed"), 1)
        self.assertEqual(self.sync_count("declined"), 0)
