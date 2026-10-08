import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch


class SelfHealingFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database = tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite3")
        self.database.close()
        os.environ["DATABASE_PATH"] = self.database.name
        os.environ["DEV_FAKE_ZAPI"] = "true"
        os.environ["DEV_FAKE_GESTAO77"] = "true"
        os.environ["WHATSAPP_PROVIDER"] = "zapi"

        import nova_guarda.config as config
        import nova_guarda.routes as routes
        import nova_guarda.services as services
        import nova_guarda.storage as storage
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
        routes._FALLBACK_REPLIED_AT.clear()

        self.storage = storage
        self.app = routes.create_app()
        self.client = self.app.test_client()
        self.client.post("/login", data={"username": "admin", "password": "admin"})
        self.phone = "5513999199293"
        storage.set_settings({"terms_auto_enabled": "0"})
        storage.upsert_cooperator(self.phone, "accepted", {"id": 597, "name": "Cooperado Teste", "active": 1})

    def tearDown(self) -> None:
        Path(self.database.name).unlink(missing_ok=True)

    def now(self):
        from nova_guarda.timezone import br_now

        return br_now()

    def create_booking(self, booking_id="booking-1", appointments=None, status="confirmed"):
        appointments = appointments or [{"id": f"apt-{booking_id}", "start_at": self.now().isoformat()}]
        self.storage.upsert_booking(
            {
                "id": 597,
                "name": "Cooperado Teste",
                "booking_id": booking_id,
                "phone": self.phone,
                "first_appointment_id": appointments[0]["id"],
                "appointments": appointments,
            }
        )
        if status != "pending":
            self.storage.update_booking_local_status(booking_id, status)
        return self.storage.get_booking(booking_id)

    def run_cycle(self, at=None, bookings=None, **kwargs):
        from nova_guarda.automation import run_automation_once

        kwargs.setdefault("month", 8)
        kwargs.setdefault("year", 2026)
        at = at or self.now()
        # O relógio simulado vale também para o que o ciclo grava (ex.: hora do lembrete).
        with patch("nova_guarda.automation.br_now", return_value=at), patch(
            "nova_guarda.storage.timestamp", return_value=at.isoformat(timespec="seconds")
        ), patch(
            "nova_guarda.automation.list_pending_partner_bookings", return_value=bookings or []
        ) as list_mock, patch(
            "nova_guarda.automation.retry_pending_gestao77_syncs", return_value={"ok": True, "results": []}
        ):
            result = run_automation_once(**kwargs)
        result["list_calls"] = list_mock.call_args_list
        return result

    def webhook(self, **payload):
        body = {"type": "ReceivedCallback", "fromMe": False, "isGroup": False, "phone": self.phone}
        body.update(payload)
        return self.client.post("/webhook", json=body)

    def reply(self, message, **extra):
        return self.webhook(text={"message": message}, **extra)

    def auto_replies(self, status):
        return [
            event
            for event in self.storage.list_conversation_events(200)
            if event["payload"].get("type") == "AutoReply" and event["payload"].get("status") == status
        ]

    # 1. check-in por atendimento -------------------------------------------------
    def test_checkin_is_sent_for_each_appointment_of_the_booking_on_its_day(self):
        now = self.now()
        self.create_booking(
            appointments=[
                {"id": "apt-day-1", "start_at": (now + timedelta(minutes=30)).isoformat()},
                {"id": "apt-day-2", "start_at": (now + timedelta(days=1, minutes=30)).isoformat()},
                {"id": "apt-cancelled", "start_at": (now + timedelta(minutes=30)).isoformat(), "status": "cancelled"},
            ]
        )

        first = self.run_cycle()
        self.assertEqual([item["appointment_id"] for item in first["checkins"]], ["apt-day-1"])
        self.assertIsNone(self.storage.get_appointment("apt-day-2"))
        self.assertIsNone(self.storage.get_appointment("apt-cancelled"))

        second = self.run_cycle(at=now + timedelta(days=1))
        self.assertEqual([item["appointment_id"] for item in second["checkins"]], ["apt-day-2"])
        self.assertEqual(self.storage.get_appointment("apt-day-2")["local_status"], "checkin_pending")

    def test_past_appointment_does_not_receive_late_checkin(self):
        now = self.now()
        self.create_booking(
            appointments=[
                {
                    "id": "apt-old",
                    "start_at": (now - timedelta(days=2)).isoformat(),
                    "end_at": (now - timedelta(days=2) + timedelta(hours=8)).isoformat(),
                }
            ]
        )

        result = self.run_cycle()

        self.assertEqual(result["checkins"], [])
        self.assertIsNone(self.storage.get_appointment("apt-old"))

    # 2. botão carrega a escala -------------------------------------------------
    def test_booking_button_answers_its_own_booking_not_the_latest(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.create_booking("booking-old", status="pending")
        self.create_booking("booking-new", status="pending")
        send_booking_to_partner("booking-old", self.phone)
        send_booking_to_partner("booking-new", self.phone)

        self.reply("booking_confirm:booking-old")

        self.assertEqual(self.storage.get_booking("booking-old")["local_status"], "confirmed")
        self.assertEqual(self.storage.get_booking("booking-new")["local_status"], "sent")

    def test_booking_button_from_another_phone_is_rejected(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.create_booking("booking-1", status="pending")
        send_booking_to_partner("booking-1", self.phone)

        self.webhook(phone="5513988887777", text={"message": "booking_confirm:booking-1"})

        self.assertEqual(self.storage.get_booking("booking-1")["local_status"], "sent")

    # 3. status de entrega -------------------------------------------------------
    def test_failed_delivery_requeues_booking_once_and_alerts(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.create_booking("booking-1", status="pending")
        send_booking_to_partner("booking-1", self.phone)
        message_id = self.storage.get_booking("booking-1")["whatsapp_message_id"]
        status_payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "statuses": [
                                    {
                                        "id": message_id,
                                        "status": "failed",
                                        "recipient_id": self.phone,
                                        "errors": [{"code": 131047, "title": "Re-engagement message"}],
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }

        self.client.post("/webhook", json=status_payload)

        self.assertEqual(self.storage.get_booking("booking-1")["local_status"], "pending")
        alerts = self.storage.list_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertIn("131047", alerts[0]["message"])

        # Reentrega do mesmo status não gera segundo efeito.
        self.client.post("/webhook", json=status_payload)
        self.assertEqual(len(self.storage.list_alerts(open_only=False)), 1)

        # Reenviada e falhou de novo: não volta para a fila outra vez.
        cycle = self.run_cycle()
        self.assertEqual(self.storage.get_booking("booking-1")["local_status"], "sent")
        self.assertEqual(cycle["alerts_resolved"], 1)
        from nova_guarda.alerts import handle_delivery_failure

        result = handle_delivery_failure(self.storage.get_booking("booking-1")["whatsapp_message_id"], self.phone, "131047")
        self.assertFalse(result["retry"])
        self.assertEqual(self.storage.get_booking("booking-1")["local_status"], "sent")

    # 4. varredura de estados parados -------------------------------------------
    def test_unanswered_booking_gets_one_reminder_then_alert_then_self_resolves(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        now = self.now()
        self.create_booking("booking-1", status="pending")
        send_booking_to_partner("booking-1", self.phone)

        self.assertEqual(self.run_cycle()["followups"], [])

        reminder = self.run_cycle(at=now + timedelta(hours=13))
        self.assertEqual([item["action"] for item in reminder["followups"]], ["reminder"])
        self.assertEqual(self.run_cycle(at=now + timedelta(hours=14))["followups"], [])

        alert = self.run_cycle(at=now + timedelta(hours=26))
        self.assertEqual([item["action"] for item in alert["followups"]], ["alert"])
        self.assertEqual(len(self.storage.list_alerts()), 1)
        self.assertEqual(self.run_cycle(at=now + timedelta(hours=40))["followups"], [])

        self.reply("booking_confirm:booking-1")
        resolved = self.run_cycle(at=now + timedelta(hours=41))
        self.assertEqual(resolved["alerts_resolved"], 1)
        self.assertEqual(self.storage.list_alerts(), [])

    def test_old_unanswered_bookings_do_not_get_mass_reminders(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.create_booking("booking-1", status="pending")
        send_booking_to_partner("booking-1", self.phone)

        result = self.run_cycle(at=self.now() + timedelta(days=20))

        self.assertEqual(result["followups"], [])
        self.assertEqual(self.storage.list_alerts(), [])

    def test_short_shift_confirmed_after_its_end_still_gets_checkin(self):
        now = self.now()
        self.create_booking(
            appointments=[
                {
                    "id": "apt-short",
                    "start_at": (now - timedelta(minutes=20)).isoformat(),
                    "end_at": (now - timedelta(minutes=15)).isoformat(),
                }
            ]
        )

        result = self.run_cycle()

        self.assertEqual([item["appointment_id"] for item in result["checkins"]], ["apt-short"])

    def test_arrival_without_location_gets_reminder_then_alert(self):
        now = self.now()
        self.create_booking()
        self.run_cycle()
        self.reply("checkin2_arrived:apt-booking-1")
        self.assertEqual(self.storage.get_appointment("apt-booking-1")["local_status"], "location_pending")

        reminder = self.run_cycle(at=now + timedelta(minutes=20))
        alert = self.run_cycle(at=now + timedelta(minutes=40))

        self.assertEqual([item["action"] for item in reminder["followups"]], ["reminder"])
        self.assertEqual([item["action"] for item in alert["followups"]], ["alert"])
        self.assertIn("não enviou a localização", self.storage.list_alerts()[0]["message"])

    def test_no_show_opens_alert_for_the_team(self):
        self.create_booking()
        self.run_cycle()

        self.reply("reason_personal:apt-booking-1")
        self.reply("reason_personal:apt-booking-1")

        alerts = self.storage.list_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertIn("NÃO VAI", alerts[0]["message"])

        response = self.client.post(f"/alertas/{alerts[0]['id']}/resolver")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.storage.list_alerts(), [])

    def test_dashboard_shows_open_alerts(self):
        self.storage.create_alert("no_show", "appointment", "apt-1", self.phone, "Alerta de teste visível")

        html = self.client.get("/").get_data(as_text=True)

        self.assertIn("Alerta de teste visível", html)

    # integração 77Gestão ------------------------------------------------------
    def real_client(self):
        from unittest.mock import Mock

        return Mock()

    def test_appointments_of_other_bookings_are_filtered_out(self):
        from nova_guarda.clients.gestao77 import Gestao77Client

        everything = {
            "appointments": [
                {"id": 1, "booking_id": 1},
                {"id": 32, "booking_id": 13},
                {"id": 2, "booking_id": 1},
            ]
        }
        with patch.object(Gestao77Client, "_get", return_value=everything):
            result = Gestao77Client(token="x").list_appointments_by_booking(13)

        self.assertEqual([item["id"] for item in result["appointments"]], [32])

    def test_only_released_bookings_with_booking_id_are_imported(self):
        from nova_guarda.clients.gestao77 import Gestao77Client
        from nova_guarda.gestao77_service import PENDING_SCHEDULE_STATUSES

        summary = {
            "cooperative_members": [
                {"id": 601, "booking_id": None, "schedule_status": "awaiting_approval"},
                {"id": 602, "booking_id": 13, "schedule_status": "awaiting_approval"},
                {"id": 697, "booking_id": 11, "schedule_status": "awaiting_send"},
                {"id": 698, "booking_id": None, "schedule_status": "awaiting_send"},
            ]
        }
        with patch.object(Gestao77Client, "_get", return_value=summary):
            members = Gestao77Client(token="x").list_partner_bookings_by_status(10, 2026, PENDING_SCHEDULE_STATUSES)

        self.assertEqual([member["booking_id"] for member in members], [11])

    def test_fake_bookings_from_members_without_booking_are_purged(self):
        import json

        self.create_booking("13", status="declined")
        with self.storage.connect() as conn:
            conn.execute(
                """
                INSERT INTO bookings (booking_id, partner_id, partner_name, phone, local_status, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("602", "602", "Sem escala", self.phone, "declined", json.dumps({"id": 602, "booking_id": None}), "x", "x"),
            )

        result = self.run_cycle()

        self.assertEqual(result["purged_fake_bookings"], 1)
        self.assertIsNone(self.storage.get_booking("602"))
        self.assertIsNotNone(self.storage.get_booking("13"))

    def test_member_without_booking_id_is_never_stored_as_booking(self):
        with self.assertRaises(ValueError):
            self.storage.upsert_booking({"id": 602, "name": "Sem escala", "booking_id": None})

    def test_rejected_transition_stops_retrying_and_alerts(self):
        import requests

        from nova_guarda.gestao77_service import retry_pending_gestao77_syncs

        self.create_booking("13", status="declined")
        response = requests.Response()
        response.status_code = 422
        client = self.real_client()
        client.update_booking_schedule_response.side_effect = requests.HTTPError("422 transição", response=response)

        with patch("nova_guarda.gestao77_service.fake_gestao77_enabled", return_value=False), patch(
            "nova_guarda.gestao77_service.Gestao77Client.from_env", return_value=client
        ):
            first = retry_pending_gestao77_syncs()
            second = retry_pending_gestao77_syncs()

        self.assertFalse(first["ok"])
        self.assertEqual(second["results"], [])
        self.assertEqual(client.update_booking_schedule_response.call_count, 1)
        self.assertEqual(self.storage.get_booking("13")["gestao77_status"], "blocked:declined")
        self.assertEqual([alert["kind"] for alert in self.storage.list_alerts()], ["sync_rejected"])

    def test_transient_sync_failure_keeps_retrying_without_alert(self):
        from nova_guarda.gestao77_service import retry_pending_gestao77_syncs

        self.create_booking("13", status="sent")
        client = self.real_client()
        client.update_booking_schedule_response.side_effect = RuntimeError("77 fora")

        with patch("nova_guarda.gestao77_service.fake_gestao77_enabled", return_value=False), patch(
            "nova_guarda.gestao77_service.Gestao77Client.from_env", return_value=client
        ):
            retry_pending_gestao77_syncs()
            retry_pending_gestao77_syncs()

        self.assertEqual(client.update_booking_schedule_response.call_count, 2)
        self.assertEqual(self.storage.list_alerts(), [])

    # escala do período ----------------------------------------------------------
    def schedule_booking(self):
        return self.create_booking(
            "55",
            status="pending",
            appointments=[
                {"id": "a1", "start_at": "2026-10-03T11:00:00Z", "end_at": "2026-10-03T20:00:00Z", "customer": {"name": "Cliente A"}},
                {"id": "a2", "start_at": "2026-10-10T11:00:00Z", "end_at": "2026-10-10T20:00:00Z", "customer": {"name": "Cliente A"}},
                {"id": "a3", "start_at": "2026-10-30T11:00:00Z", "end_at": "2026-10-30T20:00:00Z", "customer": {"name": "Condomínio São João"}},
                {"id": "a4", "start_at": "2026-10-31T11:00:00Z", "status": "cancelled"},
            ],
        )

    def pdf_text(self, content: bytes) -> str:
        import re
        import zlib

        chunks = []
        for match in re.finditer(rb"/Length\s+(\d+).*?stream\r?\n", content, re.S):
            raw = content[match.end() : match.end() + int(match.group(1))]
            try:
                chunks.append(zlib.decompress(raw).decode("latin-1"))
            except zlib.error:
                continue
        return "\n".join(chunks)

    def test_schedule_message_describes_the_whole_period_not_one_day(self):
        from nova_guarda.gestao77_service import schedule_data_from_booking
        from nova_guarda.messages import build_schedule_message

        data = schedule_data_from_booking(self.schedule_booking())
        message = build_schedule_message(data, has_pdf=True)

        self.assertEqual((data["schedule_period"], data["schedule_days"]), ("outubro/2026", 3))
        self.assertIn("Sua escala de trabalho de outubro/2026", message)
        self.assertIn("Dias de trabalho: 3 (de 03/10 a 30/10)", message)
        self.assertIn("PDF", message)
        self.assertNotIn("Horário:", message)

    def test_schedule_pdf_has_only_the_days_of_this_booking(self):
        from nova_guarda.gestao77_service import fetch_schedule_pdf

        # Outra escala do mesmo cooperado no mesmo mês, já confirmada: não pode entrar no PDF.
        self.create_booking(
            "44",
            status="confirmed",
            appointments=[{"id": "old", "start_at": "2026-10-01T11:00:00Z", "end_at": "2026-10-01T20:00:00Z"}],
        )

        content = fetch_schedule_pdf(self.schedule_booking())

        self.assertTrue(content.startswith(b"%PDF"))
        text = self.pdf_text(content)
        for expected in ("03/10/2026", "10/10/2026", "30/10/2026", "08:00", "17:00", "Cliente A", "Cooperado Teste"):
            self.assertIn(expected, text)
        self.assertIn("Condomínio São João", text)
        self.assertNotIn("01/10/2026", text)
        self.assertNotIn("31/10/2026", text)

    def test_schedule_crossing_months_uses_the_date_range_as_period(self):
        from nova_guarda.gestao77_service import schedule_data_from_booking

        booking = self.create_booking(
            "56",
            status="pending",
            appointments=[
                {"id": "b1", "start_at": "2026-10-30T11:00:00Z"},
                {"id": "b2", "start_at": "2026-11-02T11:00:00Z"},
            ],
        )

        self.assertEqual(schedule_data_from_booking(booking)["schedule_period"], "30/10 a 02/11/2026")

    def test_assisted_test_creates_a_multi_day_schedule(self):
        from nova_guarda.gestao77_service import TEST_SCHEDULE_DAYS, schedule_data_from_booking, seed_test_booking_and_send

        result = seed_test_booking_and_send(self.phone, "Cooperado Teste")

        booking = self.storage.get_booking(result["booking_id"])
        data = schedule_data_from_booking(booking)
        self.assertEqual(data["schedule_days"], TEST_SCHEDULE_DAYS)
        self.assertEqual(len(booking["payload"]["appointments"]), TEST_SCHEDULE_DAYS)
        self.assertEqual(booking["local_status"], "sent")

    def test_schedule_pdf_route_serves_the_generated_pdf(self):
        self.create_booking("55", status="pending")

        response = self.client.get("/escalas/55/pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertTrue(response.data.startswith(b"%PDF"))
        self.assertEqual(self.client.get("/escalas/inexistente/pdf").status_code, 404)

    def test_checkin_and_checkout_messages_show_start_and_end_time(self):
        from nova_guarda.gestao77_service import agenda_data_from_booking
        from nova_guarda.messages import build_checkin_message, build_checkout_message

        booking = self.schedule_booking()
        appointment = booking["payload"]["appointments"][0]
        data = agenda_data_from_booking(booking, appointment)

        checkin = build_checkin_message(data)
        checkout = build_checkout_message(data)

        for message in (checkin, checkout):
            self.assertIn("Data: 03/10/2026", message)
            self.assertIn("Horário: 08:00 às 17:00", message)
            self.assertIn("Atendimento: Cliente A", message)
            self.assertNotIn("Endereço", message)

    # 6. mês seguinte ------------------------------------------------------------
    def test_cycle_also_imports_next_month_bookings(self):
        from nova_guarda.timezone import br_now

        december = br_now().replace(month=12, day=10)
        result = self.run_cycle(at=december, month=None, year=None)

        periods = [call.args for call in result["list_calls"]]
        self.assertEqual(periods, [(12, december.year), (1, december.year + 1)])

    # 7. termo: repete só quem falhou ---------------------------------------------
    def test_failed_terms_dispatch_is_retried_then_becomes_alert(self):
        self.storage.set_settings({"terms_auto_enabled": "1"})
        now = self.now().replace(hour=8, minute=0)
        bookings = [{"booking_id": "b-new", "phone": "5513977776666", "partner_name": "Novo"}]

        with patch("nova_guarda.onboarding.send_terms_flow", side_effect=RuntimeError("provider fora")):
            first = self.run_cycle(at=now, bookings=bookings)
            self.assertTrue(first["terms"][0]["retryable"])
            self.assertEqual(self.storage.get_setting("terms_last_dispatch_date"), "")
            self.run_cycle(at=now, bookings=bookings)
            self.run_cycle(at=now, bookings=bookings)

        self.assertEqual(self.storage.get_setting("terms_last_dispatch_date"), now.date().isoformat())
        self.assertEqual([alert["kind"] for alert in self.storage.list_alerts()], ["terms_failed"])
        self.assertEqual(self.run_cycle(at=now, bookings=bookings)["terms"], [])

    def test_terms_retry_does_not_resend_to_who_already_received_today(self):
        self.storage.set_settings({"terms_auto_enabled": "1"})
        now = self.now().replace(hour=8, minute=0)
        ok_phone, bad_phone = "5513977776666", "5513966665555"
        bookings = [
            {"booking_id": "b-ok", "phone": ok_phone, "partner_name": "Ok"},
            {"booking_id": "b-bad", "phone": bad_phone, "partner_name": "Falha"},
        ]
        from nova_guarda.onboarding import send_terms_flow as real_send

        def flaky(phone, partner, resend=False):
            if phone == bad_phone:
                raise RuntimeError("provider fora")
            return real_send(phone, partner, resend)

        with patch("nova_guarda.onboarding.send_terms_flow", side_effect=flaky):
            self.run_cycle(at=now, bookings=bookings)
        second = self.run_cycle(at=now, bookings=bookings)

        self.assertEqual([item["phone"] for item in second["terms"]], [bad_phone])
        self.assertTrue(second["terms"][0]["ok"])

    # 8. mensagem não reconhecida -------------------------------------------------
    def test_unrecognized_message_gets_guidance_once(self):
        self.reply("oi, tudo bem?")
        self.reply("alguém aí?")

        replies = self.auto_replies("unrecognized")
        self.assertEqual(len(replies), 1)
        self.assertIn("botões", replies[0]["payload"]["text"]["message"])

    def test_unrecognized_message_while_waiting_location_asks_location_again(self):
        self.create_booking()
        self.run_cycle()
        self.reply("checkin2_arrived:apt-booking-1")

        self.reply("já estou aqui")

        self.assertIn("localização", self.auto_replies("unrecognized")[0]["payload"]["text"]["message"])

    def test_unknown_number_gets_no_automatic_reply(self):
        self.webhook(phone="5513900001111", text={"message": "oi"})

        self.assertEqual(self.auto_replies("unrecognized"), [])

    # 9. deduplicação do webhook ---------------------------------------------------
    def test_redelivered_webhook_event_is_processed_once(self):
        self.reply("oi", messageId="wamid.1")
        self.reply("oi", messageId="wamid.1")

        received = [
            event
            for event in self.storage.list_conversation_events(200)
            if event["payload"].get("messageId") == "wamid.1"
        ]
        self.assertEqual(len(received), 1)


if __name__ == "__main__":
    unittest.main()
