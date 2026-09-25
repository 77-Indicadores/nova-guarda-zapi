import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class LocalEventsAndRetryTest(unittest.TestCase):
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
        self.client = create_app().test_client()
        self.client.post("/login", data={"username": "admin", "password": "admin"})
        self.phone = "5513999199293"
        self.booking_id = "booking-1"
        self.appointment_id = "appointment-1"

    def tearDown(self):
        Path(self.database.name).unlink(missing_ok=True)

    def setup_checkin_pending(self, appointment_id=None, booking_id=None):
        appointment_id = appointment_id or self.appointment_id
        booking_id = booking_id or self.booking_id
        self.storage.upsert_cooperator(self.phone, "accepted", {"id": 597, "name": "Cooperado Teste"})
        self.storage.upsert_booking(
            {
                "id": 597,
                "name": "Cooperado Teste",
                "booking_id": booking_id,
                "phone": self.phone,
                "first_appointment_id": appointment_id,
                "appointments": [{"id": appointment_id, "start_at": "2026-08-30T12:00:00Z"}],
            }
        )
        self.storage.update_booking_local_status(booking_id, "confirmed")
        self.storage.mark_booking_synced(booking_id, "confirmed")
        self.client.post(
            f"/api/appointments/{appointment_id}/send-checkin",
            json={"phone": self.phone, "booking_id": booking_id},
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

    def appointment_sync_count(self):
        with self.storage.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM sync_events WHERE entity_type = 'appointment'").fetchone()
        return row["total"]

    def test_late_15_30_60_are_local_only(self):
        for minutes in (15, 30, 60):
            appointment_id = f"appointment-{minutes}"
            self.setup_checkin_pending(appointment_id, f"booking-{minutes}")

            self.send_reply(f"late_{minutes}:{appointment_id}")

            appointment = self.storage.get_appointment(appointment_id)
            self.assertEqual(appointment["local_status"], "late_reported")
            self.assertEqual(appointment["late_minutes"], minutes)
            self.assertTrue(appointment["late_reported_at"])

        self.assertEqual(self.appointment_sync_count(), 0)

    def test_duplicate_late_does_not_change_or_sync(self):
        self.setup_checkin_pending()

        self.send_reply(f"late_15:{self.appointment_id}")
        first = self.storage.get_appointment(self.appointment_id)
        self.send_reply(f"late_15:{self.appointment_id}")
        second = self.storage.get_appointment(self.appointment_id)

        self.assertEqual(second["local_status"], "late_reported")
        self.assertEqual(second["late_minutes"], 15)
        self.assertEqual(first["late_reported_at"], second["late_reported_at"])
        self.assertEqual(self.appointment_sync_count(), 0)

    def test_no_show_is_local_only(self):
        self.setup_checkin_pending()

        self.send_reply(f"reason_personal:{self.appointment_id}")

        appointment = self.storage.get_appointment(self.appointment_id)
        self.assertEqual(appointment["local_status"], "no_show_reported")
        self.assertEqual(appointment["no_show_reason"], "problema pessoal")
        self.assertTrue(appointment["no_show_reported_at"])
        self.assertEqual(self.appointment_sync_count(), 0)

    def test_duplicate_no_show_does_not_sync(self):
        self.setup_checkin_pending()

        self.send_reply(f"reason_access:{self.appointment_id}")
        self.send_reply(f"reason_access:{self.appointment_id}")

        appointment = self.storage.get_appointment(self.appointment_id)
        self.assertEqual(appointment["local_status"], "no_show_reported")
        self.assertEqual(appointment["no_show_reason"], "sem acesso ao local")
        self.assertEqual(self.appointment_sync_count(), 0)

    def test_wrong_appointment_is_rejected(self):
        self.setup_checkin_pending()

        self.send_reply("late_15:missing-appointment")

        appointment = self.storage.get_appointment(self.appointment_id)
        self.assertEqual(appointment["local_status"], "checkin_pending")
        self.assertEqual(self.appointment_sync_count(), 0)

    def test_old_event_does_not_change_current_appointment(self):
        old_id = "appointment-old"
        self.setup_checkin_pending(old_id, "booking-old")
        self.setup_checkin_pending(self.appointment_id, self.booking_id)

        self.send_reply(f"reason_other:{old_id}")

        current = self.storage.get_appointment(self.appointment_id)
        old = self.storage.get_appointment(old_id)
        self.assertEqual(current["local_status"], "checkin_pending")
        self.assertEqual(old["local_status"], "no_show_reported")

    def test_retry_pending_syncs_and_repeated_retry_do_not_duplicate_completed(self):
        self.setup_checkin_pending()
        self.send_reply(f"checkin_arrived:{self.appointment_id}")
        appointment = self.storage.get_appointment(self.appointment_id)
        self.assertEqual(appointment["gestao77_status"], "checked_in")

        self.storage.upsert_booking({"id": 597, "name": "Cooperado Teste", "booking_id": "booking-pending", "phone": self.phone})
        self.storage.update_booking_local_status("booking-pending", "sent")
        pending_appointment_id = "appointment-pending"
        self.storage.upsert_appointment({"id": pending_appointment_id}, "booking-pending", self.phone, "checked_out")

        response = self.client.post("/api/gestao77/retry-pending-syncs")
        repeated = self.client.post("/api/gestao77/retry-pending-syncs")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(len(response.get_json()["results"]), 2)
        self.assertEqual(len(repeated.get_json()["results"]), 0)

    def test_retry_real_client_called_only_for_pending_items(self):
        self.storage.upsert_booking({"id": 597, "name": "Cooperado Teste", "booking_id": "booking-pending", "phone": self.phone})
        self.storage.update_booking_local_status("booking-pending", "confirmed")
        self.storage.upsert_appointment({"id": "appointment-pending"}, "booking-pending", self.phone, "checked_in")
        client = Mock()
        client.update_booking_schedule_response.return_value = {"ok": True}
        client.update_appointment_status.return_value = {"ok": True}

        with patch("nova_guarda.gestao77_service.fake_gestao77_enabled", return_value=False), patch(
            "nova_guarda.gestao77_service.Gestao77Client.from_env",
            return_value=client,
        ):
            first = self.client.post("/api/gestao77/retry-pending-syncs")
            second = self.client.post("/api/gestao77/retry-pending-syncs")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(client.update_booking_schedule_response.call_count, 1)
        self.assertEqual(client.update_appointment_status.call_count, 1)
