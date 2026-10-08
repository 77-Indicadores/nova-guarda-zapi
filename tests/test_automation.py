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

    def run_at_hour(self, hour: int, bookings: list[dict]) -> dict:
        from nova_guarda.automation import run_automation_once
        from nova_guarda.timezone import br_now

        now = br_now().replace(hour=hour, minute=5)
        with patch("nova_guarda.automation.br_now", return_value=now), patch(
            "nova_guarda.automation.list_pending_partner_bookings", return_value=bookings
        ), patch("nova_guarda.automation.send_booking_to_partner", return_value={"status": "sent"}), patch(
            "nova_guarda.automation.retry_pending_gestao77_syncs",
            return_value={"ok": True, "results": []},
        ):
            return run_automation_once(month=8, year=2026, limit=25)

    def test_daily_terms_dispatch_sends_once_to_pending_cooperators_without_ativar(self):
        self.accept_cooperator()
        rejected_phone = "5513988887777"
        self.storage.upsert_cooperator(rejected_phone, "rejected", {"id": 1, "name": "Recusou"})
        new_phone = "5513977776666"
        bookings = [
            {"booking_id": "b-accepted", "phone": self.phone, "name": "Cooperado Teste"},
            {"booking_id": "b-rejected", "phone": rejected_phone, "name": "Recusou"},
            {"booking_id": "b-new", "phone": new_phone, "partner_id": "77", "partner_name": "Novo Cooperado"},
            {"booking_id": "b-new-2", "phone": new_phone, "partner_id": "77", "partner_name": "Novo Cooperado"},
        ]

        before = self.run_at_hour(6, bookings)
        self.assertEqual(before["terms"], [])
        self.assertIsNone(self.storage.get_cooperator(new_phone))

        result = self.run_at_hour(7, bookings)
        self.assertEqual([item["phone"] for item in result["terms"]], [new_phone])
        self.assertTrue(result["terms"][0]["ok"])
        cooperator = self.storage.get_cooperator(new_phone)
        self.assertEqual(cooperator["onboarding_status"], "terms_sent")
        self.assertEqual(cooperator["partner_name"], "Novo Cooperado")
        self.assertEqual(self.storage.get_cooperator(rejected_phone)["onboarding_status"], "rejected")

        again = self.run_at_hour(9, bookings)
        self.assertEqual(again["terms"], [])

    def test_daily_terms_dispatch_respects_disabled_setting(self):
        self.storage.set_settings({"terms_auto_enabled": "0"})
        result = self.run_at_hour(8, [{"booking_id": "b-new", "phone": "5513977776666", "name": "Novo"}])

        self.assertEqual(result["terms"], [])
        self.assertIsNone(self.storage.get_cooperator("5513977776666"))

    def test_manual_terms_send_from_cooperators_page(self):
        self.login()
        phone = "5513977776666"
        response = self.client.post("/cooperados/enviar-termo", data={"phone": phone})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.storage.get_cooperator(phone)["onboarding_status"], "terms_sent")

        self.client.post("/dev/simulate-whatsapp", json={"phone": phone, "message": "terms_accept"})
        self.assertEqual(self.storage.get_cooperator(phone)["onboarding_status"], "accepted")

        self.client.post("/cooperados/enviar-termo", data={"phone": phone})
        self.assertEqual(self.storage.get_cooperator(phone)["onboarding_status"], "accepted")

    def test_automation_sends_only_to_accepted_cooperator_and_records_run(self):
        self.storage.set_settings({"terms_auto_enabled": "0"})
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

    def test_automation_does_not_send_checkin_for_unconfirmed_booking(self):
        from nova_guarda.timezone import br_now

        self.accept_cooperator()
        start_at = (br_now() + timedelta(minutes=30)).isoformat()
        self.storage.upsert_booking(
            {
                "id": 597,
                "name": "Cooperado Teste",
                "booking_id": "booking-unconfirmed",
                "schedule_status": "sent",
                "phone": self.phone,
                "today_appointment_id": "appointment-unconfirmed",
                "appointments": [{"id": "appointment-unconfirmed", "start_at": start_at}],
            },
            local_status="sent",
        )

        with patch("nova_guarda.automation.list_pending_partner_bookings", return_value=[]), patch(
            "nova_guarda.automation.retry_pending_gestao77_syncs",
            return_value={"ok": True, "results": []},
        ):
            from nova_guarda.automation import run_automation_once

            result = run_automation_once(month=8, year=2026)

        self.assertEqual(result["checkins"], [])
        booking = self.storage.get_booking("booking-unconfirmed")
        self.assertEqual(booking["local_status"], "sent")

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
