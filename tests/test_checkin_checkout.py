import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo


class CheckinCheckoutFlowTest(unittest.TestCase):
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
        self.appointment_id = "appointment-1"

    def tearDown(self):
        Path(self.database.name).unlink(missing_ok=True)

    def accept_cooperator(self):
        self.storage.upsert_cooperator(
            self.phone,
            "accepted",
            {"id": 597, "name": "Cooperado Teste", "active": 1, "type": "cooperado"},
        )

    def create_booking(self, booking_id=None, appointment_id=None, status="confirmed", start_at="2026-08-30T12:00:00Z"):
        booking_id = booking_id or self.booking_id
        appointment_id = appointment_id or self.appointment_id
        booking = self.storage.upsert_booking(
            {
                "id": 597,
                "name": "Cooperado Teste",
                "booking_id": booking_id,
                "schedule_status": "awaiting_approval",
                "phone": self.phone,
                "first_appointment_id": appointment_id,
                "appointments": [
                    {
                        "id": appointment_id,
                        "start_at": start_at,
                        "customer": {"name": "Cliente Teste"},
                    }
                ],
            }
        )
        self.storage.update_booking_local_status(booking_id, status)
        return booking

    def post_checkin_send(self, appointment_id=None, booking_id=None):
        return self.client.post(
            f"/api/appointments/{appointment_id or self.appointment_id}/send-checkin",
            json={
                "phone": self.phone,
                "booking_id": booking_id or self.booking_id,
                "client_name": "Cooperado Teste",
                "schedule_date": "30/08/2026",
                "schedule_time": "09:00",
            },
        )

    def post_checkout_send(self, appointment_id=None):
        return self.client.post(
            f"/api/appointments/{appointment_id or self.appointment_id}/checkout",
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

    def send_location(self, address="Rua Teste, 100 - Santos"):
        return self.client.post(
            "/webhook",
            json={
                "type": "ReceivedCallback",
                "fromMe": False,
                "isGroup": False,
                "phone": self.phone,
                "location": {"latitude": -23.96, "longitude": -46.33, "address": address},
            },
        )

    def sync_count(self, appointment_id, status):
        with self.storage.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM sync_events
                WHERE entity_type = 'appointment'
                  AND entity_id = ?
                  AND action = ?
                  AND ok = 1
                """,
                (appointment_id, f"status:{status}"),
            ).fetchone()
        return row["total"]

    def failed_sync_count(self, appointment_id, status):
        with self.storage.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM sync_events
                WHERE entity_type = 'appointment'
                  AND entity_id = ?
                  AND action = ?
                  AND ok = 0
                """,
                (appointment_id, f"status:{status}"),
            ).fetchone()
        return row["total"]

    def test_cooperator_without_acceptance_cannot_start_checkin(self):
        self.create_booking()

        response = self.post_checkin_send()

        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.storage.get_appointment(self.appointment_id))

    def test_missing_appointment_is_rejected(self):
        self.accept_cooperator()
        self.create_booking()

        response = self.post_checkin_send("missing-appointment")

        self.assertEqual(response.status_code, 502)
        self.assertIn("não pertence", response.get_json()["error"])

    def test_appointment_from_other_booking_is_rejected(self):
        self.accept_cooperator()
        self.create_booking()

        response = self.post_checkin_send(self.appointment_id, "other-booking")

        self.assertEqual(response.status_code, 502)
        self.assertIn("não encontrado", response.get_json()["error"])

    def test_valid_checkin_generates_checked_in(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()

        self.send_reply(f"checkin_arrived:{self.appointment_id}")
        pending = self.storage.get_appointment(self.appointment_id)
        self.assertEqual(pending["local_status"], "location_pending")
        self.assertEqual(self.sync_count(self.appointment_id, "checked_in"), 0)

        response = self.send_location()

        self.assertEqual(response.status_code, 200)
        appointment = self.storage.get_appointment(self.appointment_id)
        self.assertEqual(appointment["local_status"], "checked_in")
        self.assertEqual(appointment["gestao77_status"], "checked_in")
        self.assertTrue(appointment["checked_in_at"])
        self.assertEqual(appointment["checkin_address"], "Rua Teste, 100 - Santos")
        self.assertEqual(self.sync_count(self.appointment_id, "checked_in"), 1)

    def test_duplicate_checkin_does_not_duplicate_post(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()

        self.send_reply(f"checkin_arrived:{self.appointment_id}")
        self.send_reply(f"checkin_arrived:{self.appointment_id}")
        self.send_location()
        self.send_location()

        self.assertEqual(self.sync_count(self.appointment_id, "checked_in"), 1)

    def test_location_without_pending_checkin_does_not_check_in(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()

        self.send_location()

        appointment = self.storage.get_appointment(self.appointment_id)
        self.assertEqual(appointment["local_status"], "checkin_pending")
        self.assertEqual(self.sync_count(self.appointment_id, "checked_in"), 0)

    def test_arrival_after_reported_delay_still_requires_location(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()
        self.send_reply(f"late_15:{self.appointment_id}")

        self.send_reply(f"checkin_arrived:{self.appointment_id}")
        self.assertEqual(self.storage.get_appointment(self.appointment_id)["local_status"], "location_pending")
        self.send_location()

        self.assertEqual(self.storage.get_appointment(self.appointment_id)["local_status"], "checked_in")

    def test_checkin_is_not_resent_while_waiting_location_or_after_no_show(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()
        self.send_reply(f"checkin_arrived:{self.appointment_id}")

        response = self.post_checkin_send()

        self.assertTrue(response.get_json()["result"]["idempotent"])
        self.assertEqual(self.storage.get_appointment(self.appointment_id)["local_status"], "location_pending")

    def last_bot_text(self):
        for event in self.storage.list_conversation_events(20):
            if event["payload"].get("type") == "AutoReply":
                return event["payload"]["text"]["message"]
        return ""

    def test_out_of_order_taps_get_a_specific_explanation(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()

        self.send_reply(f"checkout_confirm:{self.appointment_id}")
        self.assertIn("ainda não fez o check-in", self.last_bot_text())

        self.send_reply(f"checkin_arrived:{self.appointment_id}")
        self.send_location()
        self.send_reply(f"checkin_arrived:{self.appointment_id}")
        self.assertIn("check-in deste atendimento já está registrado", self.last_bot_text())
        self.send_reply(f"late_15:{self.appointment_id}")
        self.assertIn("não é preciso informar atraso", self.last_bot_text())

        self.send_reply("checkin_arrived:outro-atendimento")
        self.assertIn("Não encontrei este atendimento", self.last_bot_text())

    def test_arrival_after_no_show_is_explained(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()
        self.send_reply(f"reason_personal:{self.appointment_id}")

        self.send_reply(f"checkin_arrived:{self.appointment_id}")

        self.assertIn("informou que não iria", self.last_bot_text())
        self.assertEqual(self.storage.get_appointment(self.appointment_id)["local_status"], "no_show_reported")

    def test_changing_the_delay_replaces_the_late_alert(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()

        self.send_reply(f"late_60:{self.appointment_id}")
        self.send_reply(f"late_15:{self.appointment_id}")

        alerts = self.storage.list_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertIn("15 min", alerts[0]["message"])

    def test_checkout_without_checkin_is_blocked(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()

        response = self.post_checkout_send()

        self.assertEqual(response.status_code, 502)
        self.assertIn("depois de checked_in", response.get_json()["error"])
        self.assertEqual(self.sync_count(self.appointment_id, "checked_out"), 0)

    def test_valid_checkout_generates_checked_out(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()
        self.send_reply(f"checkin_arrived:{self.appointment_id}")
        self.send_location()
        self.post_checkout_send()

        self.send_reply(f"checkout_confirm:{self.appointment_id}")
        self.send_reply(f"checkout_confirm:{self.appointment_id}")
        pending = self.storage.get_appointment(self.appointment_id)
        self.assertEqual(pending["local_status"], "checkout_location_pending")
        self.assertEqual(self.sync_count(self.appointment_id, "checked_out"), 0)

        response = self.send_location("Rua da Saída, 200 - Santos")
        self.send_location("Rua da Saída, 200 - Santos")

        self.assertEqual(response.status_code, 200)
        appointment = self.storage.get_appointment(self.appointment_id)
        self.assertEqual(appointment["local_status"], "checked_out")
        self.assertEqual(appointment["gestao77_status"], "checked_out")
        self.assertTrue(appointment["checked_out_at"])
        self.assertEqual(appointment["checkout_address"], "Rua da Saída, 200 - Santos")
        self.assertEqual(appointment["checkin_address"], "Rua Teste, 100 - Santos")
        self.assertEqual(self.sync_count(self.appointment_id, "checked_out"), 1)

    def test_finishing_without_location_gets_reminder_then_alert(self):
        from datetime import timedelta

        from nova_guarda.automation import run_automation_once
        from nova_guarda.timezone import br_now

        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()
        self.send_reply(f"checkin_arrived:{self.appointment_id}")
        self.send_location()
        self.post_checkout_send()
        self.send_reply(f"checkout_confirm:{self.appointment_id}")
        self.storage.set_settings({"terms_auto_enabled": "0"})

        def cycle(minutes):
            at = br_now() + timedelta(minutes=minutes)
            with patch("nova_guarda.automation.br_now", return_value=at), patch(
                "nova_guarda.storage.timestamp", return_value=at.isoformat(timespec="seconds")
            ), patch("nova_guarda.automation.list_pending_partner_bookings", return_value=[]), patch(
                "nova_guarda.automation.retry_pending_gestao77_syncs", return_value={"ok": True, "results": []}
            ):
                return run_automation_once(month=8, year=2026)["followups"]

        self.assertEqual([item["action"] for item in cycle(20)], ["reminder"])
        self.assertEqual([item["action"] for item in cycle(40)], ["alert"])
        self.assertIn("finalizou", self.storage.list_alerts()[0]["message"])
        self.assertEqual(self.storage.get_appointment(self.appointment_id)["local_status"], "checkout_location_pending")

    def test_old_click_does_not_change_current_appointment(self):
        old_id = "appointment-old"
        self.accept_cooperator()
        self.create_booking(appointment_id=old_id)
        self.post_checkin_send(old_id)
        self.create_booking(booking_id="booking-2", appointment_id=self.appointment_id)
        self.post_checkin_send(self.appointment_id, "booking-2")

        self.send_reply(f"checkin_arrived:{old_id}")
        self.send_location()

        current = self.storage.get_appointment(self.appointment_id)
        old = self.storage.get_appointment(old_id)
        self.assertEqual(current["local_status"], "checkin_pending")
        self.assertEqual(old["local_status"], "checked_in")

    def test_gestao77_failure_preserves_local_checkin_for_retry(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()
        client = Mock()
        client.update_appointment_status.side_effect = RuntimeError("77 fora")

        with patch("nova_guarda.gestao77_service.fake_gestao77_enabled", return_value=False), patch(
            "nova_guarda.gestao77_service.Gestao77Client.from_env",
            return_value=client,
        ):
            self.send_reply(f"checkin_arrived:{self.appointment_id}")
            response = self.send_location()

        self.assertEqual(response.status_code, 200)
        appointment = self.storage.get_appointment(self.appointment_id)
        self.assertEqual(appointment["local_status"], "checked_in")
        self.assertIsNone(appointment.get("gestao77_status"))
        self.assertEqual(self.failed_sync_count(self.appointment_id, "checked_in"), 1)

    def test_timezone_selects_brazil_day_on_utc_boundary(self):
        from nova_guarda.gestao77_service import select_today_appointment

        appointments = [{"id": "late-utc", "start_at": "2026-08-31T02:30:00Z"}]
        br_time = datetime(2026, 8, 30, 23, 50, tzinfo=ZoneInfo("America/Sao_Paulo"))

        with patch("nova_guarda.gestao77_service.br_now", return_value=br_time):
            selected = select_today_appointment(appointments)

        self.assertEqual(selected["id"], "late-utc")
