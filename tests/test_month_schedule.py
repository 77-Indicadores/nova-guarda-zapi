import unittest
import unittest.mock
from datetime import timedelta
from unittest.mock import patch

from tests import test_self_healing

BR_OCT = "2026-11-{day:02d}T06:00:00-03:00"


class MonthScheduleFlowTest(unittest.TestCase):
    """O cooperado recebe e responde o mês inteiro de uma vez, mesmo quando a
    77Gestão tem vários bookings no mês (ex.: dias pares e ímpares)."""

    # Mesmo ambiente do fluxo de autocorreção, sem herdar os testes dele.
    setUp = test_self_healing.SelfHealingFlowTest.setUp
    tearDown = test_self_healing.SelfHealingFlowTest.tearDown
    now = test_self_healing.SelfHealingFlowTest.now
    create_booking = test_self_healing.SelfHealingFlowTest.create_booking
    run_cycle = test_self_healing.SelfHealingFlowTest.run_cycle
    webhook = test_self_healing.SelfHealingFlowTest.webhook
    reply = test_self_healing.SelfHealingFlowTest.reply
    auto_replies = test_self_healing.SelfHealingFlowTest.auto_replies

    def month_booking(self, booking_id, days, status="pending"):
        return self.create_booking(
            booking_id,
            appointments=[{"id": f"apt-{booking_id}-{day}", "start_at": BR_OCT.format(day=day)} for day in days],
            status=status,
        )

    def sent_schedules(self):
        return [
            event
            for event in self.storage.list_conversation_events(200)
            if any(str(button.get("id", "")).startswith("booking_confirm") for button in event["payload"].get("buttons") or [])
        ]

    def test_two_bookings_of_the_month_go_in_one_message_with_all_days(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.month_booking("62", [22, 24, 26])
        self.month_booking("63", [21, 23])

        with patch("nova_guarda.gestao77_service.fetch_schedule_pdf", return_value=b"%PDF") as pdf:
            result = send_booking_to_partner("62")
            again = send_booking_to_partner("63")

        self.assertEqual(result["month_booking_ids"], ["62", "63"])
        self.assertTrue(again["idempotent"])
        self.assertEqual(self.storage.get_booking("62")["local_status"], "sent")
        self.assertEqual(self.storage.get_booking("63")["local_status"], "sent")
        schedules = self.sent_schedules()
        self.assertEqual(len(schedules), 1)
        self.assertIn("booking_confirm:62,63", [b["id"] for b in schedules[0]["payload"]["buttons"]])
        shifts = pdf.call_args[0][1]["schedule_shifts"]
        self.assertEqual([shift["date"][:2] for shift in shifts], ["21", "22", "23", "24", "26"])

    def test_confirming_the_month_confirms_every_booking(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.month_booking("62", [22, 24])
        self.month_booking("63", [21, 23])
        send_booking_to_partner("62")

        self.reply("booking_confirm:62,63")

        self.assertEqual(self.storage.get_booking("62")["local_status"], "confirmed")
        self.assertEqual(self.storage.get_booking("63")["local_status"], "confirmed")
        confirmed = self.auto_replies("confirmed")
        self.assertEqual(len(confirmed), 1)

    def test_declining_the_month_declines_every_booking(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.month_booking("62", [22])
        self.month_booking("63", [21])
        send_booking_to_partner("62")

        self.reply("booking_decline:62,63")

        self.assertEqual(self.storage.get_booking("62")["local_status"], "declined")
        self.assertEqual(self.storage.get_booking("63")["local_status"], "declined")

    def test_reply_with_a_booking_of_another_phone_changes_nothing(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.month_booking("62", [22])
        self.storage.upsert_booking(
            {"id": 1, "name": "Outro", "booking_id": "99", "phone": "5513900000000", "appointments": []}
        )
        send_booking_to_partner("62")

        self.reply("booking_confirm:62,99")

        self.assertEqual(self.storage.get_booking("62")["local_status"], "sent")

    def test_other_months_and_answered_bookings_stay_out(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.month_booking("62", [22])
        self.create_booking(
            "70", appointments=[{"id": "apt-nov", "start_at": "2026-12-03T06:00:00-03:00"}], status="pending"
        )
        self.month_booking("45", [12], status="confirmed")

        result = send_booking_to_partner("62")

        self.assertEqual(result["month_booking_ids"], ["62"])
        self.assertEqual(self.storage.get_booking("70")["local_status"], "pending")

    def test_month_without_answer_gets_one_reminder(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.month_booking("62", [22, 24])
        self.month_booking("63", [21, 23])
        send_booking_to_partner("62")
        before = len(self.sent_schedules())

        later = self.now() + timedelta(hours=13)
        result = self.run_cycle(at=later)

        reminders = [item for item in result["followups"] if item["entity_type"] == "booking"]
        self.assertEqual([item["action"] for item in reminders], ["reminder"])
        self.assertEqual(len(self.sent_schedules()), before + 1)
        self.assertIn("booking_confirm:62,63", [b["id"] for b in self.sent_schedules()[0]["payload"]["buttons"]])
        self.assertEqual(self.run_cycle(at=later + timedelta(minutes=5))["followups"], [])

    def test_undelivered_month_message_requeues_every_booking(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.month_booking("62", [22])
        self.month_booking("63", [21])
        message_id = send_booking_to_partner("62")["whatsapp"]["messageId"]

        self.client.post(
            "/webhook",
            json={"type": "MessageStatusCallback", "phone": self.phone, "messageId": message_id, "status": "failed", "error": "131047"},
        )

        self.assertEqual(self.storage.get_booking("62")["local_status"], "pending")
        self.assertEqual(self.storage.get_booking("63")["local_status"], "pending")

    def test_booking_entirely_in_the_past_is_not_sent_nor_grouped(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        today = self.now()
        past = (today - timedelta(days=3)).replace(hour=6, minute=0, second=0, microsecond=0)
        future = (today + timedelta(days=3)).replace(hour=6, minute=0, second=0, microsecond=0)
        self.create_booking("19", appointments=[{"id": "apt-old", "start_at": past.isoformat()}], status="pending")
        self.create_booking("68", appointments=[{"id": "apt-new", "start_at": future.isoformat()}], status="pending")

        with self.assertRaises(ValueError):
            send_booking_to_partner("19")
        result = send_booking_to_partner("68")

        if past.month == future.month:
            self.assertEqual(result["month_booking_ids"], ["68"])
        self.assertEqual(self.storage.get_booking("19")["local_status"], "pending")

    # escala alterada pela equipe -------------------------------------------------
    def import_from_gestao77(self, members, day=27):
        from nova_guarda.gestao77_service import list_pending_partner_bookings

        fake = unittest.mock.Mock()
        fake.list_partner_bookings_by_status.return_value = members
        fake.get_partner.return_value = {"partner": {"phones": []}}
        fake.list_appointments_by_booking.side_effect = lambda booking_id: {
            "appointments": [{"id": f"apt-{booking_id}-27", "start_at": BR_OCT.format(day=day)}]
        }
        with patch("nova_guarda.gestao77_service.Gestao77Client.from_env", return_value=fake):
            return list_pending_partner_bookings(11, 2026)

    def member(self, booking_id, status="awaiting_send"):
        return {"id": 597, "name": "Cooperado Teste", "booking_id": booking_id, "phone": self.phone, "schedule_status": status}

    def test_changed_schedule_back_to_awaiting_send_is_sent_again_with_notice(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.import_from_gestao77([self.member("62")])
        send_booking_to_partner("62")
        self.reply("booking_confirm:62")
        self.assertEqual(self.storage.get_booking("62")["local_status"], "confirmed")

        # Liberar o mês de novo sem mudar nada não reenvia.
        self.import_from_gestao77([self.member("62")])
        self.assertEqual(self.storage.get_booking("62")["local_status"], "confirmed")

        # A equipe altera o horário e a 77Gestão volta para "aguardando envio".
        self.import_from_gestao77([self.member("62")], day=28)
        self.assertEqual(self.storage.get_booking("62")["local_status"], "pending")
        send_booking_to_partner("62")

        last = self.sent_schedules()[0]["payload"]
        self.assertIn("Sua escala foi alterada", last.get("message") or last["response"]["payload"]["message"])
        self.assertEqual(self.storage.get_booking("62")["local_status"], "sent")
        self.reply("booking_confirm:62")
        self.assertEqual(self.storage.get_booking("62")["local_status"], "confirmed")

    def test_first_send_has_no_changed_notice(self):
        from nova_guarda.gestao77_service import send_booking_to_partner

        self.import_from_gestao77([self.member("62")])
        send_booking_to_partner("62")

        last = self.sent_schedules()[0]["payload"]
        self.assertNotIn("alterada", last.get("message") or last["response"]["payload"]["message"])

    def test_booking_still_awaiting_its_own_sent_sync_is_not_reopened(self):
        # Envio feito, mas o "sent" ainda não chegou na 77Gestão: não é alteração.
        self.import_from_gestao77([self.member("62")])
        self.storage.update_booking_local_status("62", "sent")

        self.import_from_gestao77([self.member("62")])

        self.assertEqual(self.storage.get_booking("62")["local_status"], "sent")
