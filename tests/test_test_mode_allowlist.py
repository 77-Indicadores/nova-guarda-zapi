import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestModeAllowlistTest(unittest.TestCase):
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
        self.client.post("/login", data={"username": "admin", "password": "admin"})
        self.test_phone = "5513999199293"
        self.real_phone = "5513988887777"
        for phone, name in ((self.test_phone, "Cooperado Teste"), (self.real_phone, "Cooperado Real")):
            storage.upsert_cooperator(phone, "accepted", {"id": phone[-4:], "name": name, "active": 1})

    def tearDown(self) -> None:
        Path(self.database.name).unlink(missing_ok=True)

    def enable_test_mode(self, phones=None):
        phones = [self.test_phone] if phones is None else phones
        self.storage.set_settings(
            {
                "operation_mode": "test",
                "terms_auto_enabled": "0",
                "test_cooperators": json.dumps([{"phone": phone, "partner_id": "", "name": ""} for phone in phones]),
            }
        )

    def run_cycle(self, bookings, at=None):
        from nova_guarda.automation import run_automation_once
        from nova_guarda.timezone import br_now

        with patch("nova_guarda.automation.br_now", return_value=at or br_now()), patch(
            "nova_guarda.automation.list_pending_partner_bookings", return_value=bookings
        ), patch("nova_guarda.automation.send_booking_to_partner", return_value={"status": "sent"}) as send_mock, patch(
            "nova_guarda.automation.retry_pending_gestao77_syncs", return_value={"ok": True, "results": []}
        ):
            result = run_automation_once(month=8, year=2026)
        result["sent_to"] = [call.args[1] for call in send_mock.call_args_list]
        return result

    def bookings(self):
        return [
            {"booking_id": "b-test", "phone": self.test_phone, "name": "Cooperado Teste"},
            {"booking_id": "b-real", "phone": self.real_phone, "name": "Cooperado Real"},
        ]

    def test_test_mode_sends_bookings_only_to_marked_cooperators(self):
        self.enable_test_mode()

        result = self.run_cycle(self.bookings())

        self.assertEqual(result["sent_to"], [self.test_phone])
        blocked = [item for item in result["items"] if item["booking_id"] == "b-real"][0]
        self.assertIn("Modo teste", blocked["reason"])
        self.assertTrue(result["ok"])

    def test_production_mode_ignores_the_test_list(self):
        self.enable_test_mode()
        self.storage.set_settings({"operation_mode": "production"})

        result = self.run_cycle(self.bookings())

        self.assertEqual(result["sent_to"], [self.test_phone, self.real_phone])

    def test_test_mode_with_empty_list_sends_to_nobody(self):
        self.enable_test_mode([])

        self.assertEqual(self.run_cycle(self.bookings())["sent_to"], [])

    def test_test_mode_daily_terms_go_only_to_marked_cooperators(self):
        from nova_guarda.timezone import br_now

        new_test, new_real = "5513977776666", "5513966665555"
        self.enable_test_mode([new_test])
        self.storage.set_settings({"terms_auto_enabled": "1"})
        bookings = [
            {"booking_id": "b-1", "phone": new_test, "partner_name": "Novo Teste"},
            {"booking_id": "b-2", "phone": new_real, "partner_name": "Novo Real"},
        ]

        result = self.run_cycle(bookings, at=br_now().replace(hour=8))

        self.assertEqual([item["phone"] for item in result["terms"]], [new_test])
        self.assertIsNone(self.storage.get_cooperator(new_real))

    def test_test_mode_blocks_manual_terms_for_unmarked_phone(self):
        self.enable_test_mode()

        self.client.post("/cooperados/enviar-termo", data={"phone": "5513966665555"})

        self.assertIsNone(self.storage.get_cooperator("5513966665555"))

    def test_test_mode_blocks_checkin_for_unmarked_cooperator(self):
        from nova_guarda.timezone import br_now

        self.enable_test_mode()
        for phone, booking_id in ((self.test_phone, "b-test"), (self.real_phone, "b-real")):
            self.storage.upsert_booking(
                {
                    "id": 1,
                    "name": "Cooperado",
                    "booking_id": booking_id,
                    "phone": phone,
                    "first_appointment_id": f"apt-{booking_id}",
                    "appointments": [{"id": f"apt-{booking_id}", "start_at": br_now().isoformat()}],
                }
            )
            self.storage.update_booking_local_status(booking_id, "confirmed")

        result = self.run_cycle([])

        self.assertEqual([item["appointment_id"] for item in result["checkins"]], ["apt-b-test"])
        self.assertIsNone(self.storage.get_appointment("apt-b-real"))

    def test_settings_page_lists_candidates_and_saves_selection(self):
        self.storage.set_settings({"operation_mode": "test"})

        html = self.client.get("/configuracoes").get_data(as_text=True)
        self.assertIn("Cooperados de teste", html)
        self.assertIn("Cooperado Real", html)

        response = self.client.post("/teste-assistido/cooperados", data={"phones": [self.real_phone]})
        self.assertEqual(response.status_code, 302)

        from nova_guarda.onboarding import mode_allows_phone, test_cooperators

        self.assertEqual(test_cooperators(), [{"phone": self.real_phone, "partner_id": "7777", "name": "Cooperado Real"}])
        self.assertTrue(mode_allows_phone(self.real_phone))
        self.assertFalse(mode_allows_phone(self.test_phone))
        self.assertIn("enviando só para 1 cooperado", self.client.get("/").get_data(as_text=True))

    def test_settings_page_hides_test_list_in_production(self):
        html = self.client.get("/configuracoes").get_data(as_text=True)

        self.assertNotIn("Salvar cooperados de teste", html)

    def test_quick_test_sends_terms_first_then_creates_booking_without_new_partner(self):
        new_phone = "5513977776666"
        self.enable_test_mode([new_phone])

        self.client.post("/teste-assistido/escala", data={"phone": new_phone})
        self.assertEqual(self.storage.get_cooperator(new_phone)["onboarding_status"], "terms_sent")
        self.assertEqual(self.storage.list_bookings(), [])

        self.client.post("/dev/simulate-whatsapp", json={"phone": new_phone, "message": "terms_accept"})
        self.client.post("/teste-assistido/escala", data={"phone": new_phone})

        bookings = self.storage.list_bookings()
        self.assertEqual(len(bookings), 1)
        self.assertEqual(bookings[0]["phone"], new_phone)
        self.assertEqual(bookings[0]["local_status"], "sent")

    def test_typed_phone_assisted_test_joins_the_test_list(self):
        typed_phone = "5513955554444"
        self.enable_test_mode()

        self.client.post("/teste-assistido/completo", data={"phone": typed_phone, "client_name": "Avulso"})

        from nova_guarda.onboarding import mode_allows_phone

        self.assertTrue(mode_allows_phone(typed_phone))


if __name__ == "__main__":
    unittest.main()
