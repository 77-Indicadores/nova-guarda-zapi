import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch


class AutomationTest(unittest.TestCase):
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
        config.DEV_FAKE_ZAPI = True
        services.DEV_FAKE_ZAPI = True

        RECEIVED_EVENTS.clear()
        AGENDA_STATE.clear()
        TERMS_STATE.clear()

        self.storage = storage
        self.app = create_app()
        self.client = self.app.test_client()
        self.phone = "5513999199293"

    def tearDown(self) -> None:
        Path(self.database.name).unlink(missing_ok=True)

    def login(self) -> None:
        self.client.post("/login", data={"username": "admin", "password": "admin"})

    def accept_cooperator(self) -> None:
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

    def test_automation_sends_only_to_accepted_cooperator_and_records_run(self):
        self.accept_cooperator()
        bookings = [
            {"booking_id": "booking-1", "phone": self.phone, "name": "Cooperado Teste"},
            {"booking_id": "booking-2", "phone": "5513999990000", "name": "Sem aceite"},
        ]

        with patch("nova_guarda.automation.list_pending_partner_bookings", return_value=bookings), patch(
            "nova_guarda.automation.send_booking_to_partner",
            return_value={"booking_id": "booking-1", "status": "sent"},
        ) as send_mock, patch(
            "nova_guarda.automation.retry_pending_gestao77_syncs",
            return_value={"ok": True, "results": []},
        ):
            from nova_guarda.automation import run_automation_once

            result = run_automation_once(month=8, year=2026, limit=25)

        self.assertFalse(result["ok"])
        send_mock.assert_called_once_with("booking-1", self.phone)
        self.assertEqual(result["run"]["status"], "partial")
        self.assertEqual(len(self.storage.list_poller_runs()), 1)
        skipped = [item for item in result["items"] if item["booking_id"] == "booking-2"][0]
        self.assertIn("ativar", skipped["error"])

    def test_operational_ui_is_monitoring_not_manual_send_console(self):
        self.login()
        response = self.client.get("/escalas")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Monitoramento do fluxo automático", html)
        self.assertNotIn("Enviar escala", html)
        self.assertNotIn("Enviar check-in", html)
        self.assertNotIn("Enviar check-out", html)

    def test_automation_sends_checkin_for_eligible_booking(self):
        from nova_guarda.timezone import br_now

        self.accept_cooperator()
        start_at = (br_now() + timedelta(minutes=30)).isoformat()
        self.storage.upsert_booking(
            {
                "id": 597,
                "name": "Cooperado Teste",
                "booking_id": "booking-checkin",
                "schedule_status": "sent",
                "phone": self.phone,
                "today_appointment_id": "appointment-checkin",
                "appointments": [{"id": "appointment-checkin", "start_at": start_at}],
            },
            local_status="confirmed",
        )

        with patch("nova_guarda.automation.list_pending_partner_bookings", return_value=[]), patch(
            "nova_guarda.automation.retry_pending_gestao77_syncs",
            return_value={"ok": True, "results": []},
        ):
            from nova_guarda.automation import run_automation_once

            result = run_automation_once(month=8, year=2026)

        self.assertTrue(result["checkins"][0]["ok"])
        appointment = self.storage.get_appointment("appointment-checkin")
        self.assertEqual(appointment["local_status"], "checkin_pending")

    def test_automation_sends_checkout_for_checked_in_appointment(self):
        from nova_guarda.timezone import br_now

        self.accept_cooperator()
        start_at = (br_now() - timedelta(minutes=90)).isoformat()
        self.storage.upsert_appointment(
            {"id": "appointment-checkout", "start_at": start_at},
            "booking-checkout",
            self.phone,
            "checked_in",
        )

        with patch("nova_guarda.automation.list_pending_partner_bookings", return_value=[]), patch(
            "nova_guarda.automation.retry_pending_gestao77_syncs",
            return_value={"ok": True, "results": []},
        ):
            from nova_guarda.automation import run_automation_once

            result = run_automation_once(month=8, year=2026)

        self.assertTrue(result["checkouts"][0]["ok"])
        appointment = self.storage.get_appointment("appointment-checkout")
        self.assertEqual(appointment["local_status"], "checkout_pending")


if __name__ == "__main__":
    unittest.main()
