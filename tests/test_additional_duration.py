import unittest
import unittest.mock
from datetime import timedelta
from unittest.mock import patch

from tests import test_self_healing


class AdditionalDurationFlowTest(unittest.TestCase):
    """Plantão avulso ("Duração Adicional" na 77Gestão) não tem escala para
    confirmar, mas o cooperado recebe check-in e check-out normalmente."""

    setUp = test_self_healing.SelfHealingFlowTest.setUp
    tearDown = test_self_healing.SelfHealingFlowTest.tearDown
    now = test_self_healing.SelfHealingFlowTest.now
    run_cycle = test_self_healing.SelfHealingFlowTest.run_cycle

    def extra(self, appointment_id="14470", starts_in=timedelta(minutes=30), hours=6):
        start = (self.now() + starts_in).replace(microsecond=0)
        return {
            "id": appointment_id,
            "type": "additional_duration",
            "partner_id": 597,
            "booking_id": None,
            "customer_id": 7970,
            "customer": {"id": 7970, "name": "EDIFICIO TUMIN"},
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=hours)).isoformat(),
        }

    def fake_77(self, items):
        fake = unittest.mock.Mock()
        fake.list_additional_durations.return_value = items
        fake.get_partner.return_value = {"partner": {"name": "Cooperado Teste", "phones": [{"number": self.phone}]}}
        return patch("nova_guarda.gestao77_service.Gestao77Client.from_env", return_value=fake)

    def checkins(self):
        return [item for item in self.cycle["checkins"] if item.get("action") == "checkin_sent"]

    def test_additional_duration_gets_checkin_without_schedule(self):
        with self.fake_77([self.extra()]):
            self.cycle = self.run_cycle()

        booking = self.storage.get_booking("extra-14470")
        self.assertEqual(booking["local_status"], "confirmed")
        self.assertEqual(booking["gestao77_status"], "confirmed")
        self.assertEqual([item["appointment_id"] for item in self.checkins()], ["14470"])
        self.assertEqual(self.storage.get_appointment("14470")["local_status"], "checkin_pending")
        # Nada de escala/PDF para confirmar.
        schedules = [
            event
            for event in self.storage.list_conversation_events(200)
            if any(str(b.get("id", "")).startswith("booking_confirm") for b in event["payload"].get("buttons") or [])
        ]
        self.assertEqual(schedules, [])
        self.assertNotIn("extra-14470", [b["booking_id"] for b in self.storage.list_pending_booking_syncs()])

    def test_checkin_only_inside_the_window(self):
        with self.fake_77([self.extra(starts_in=timedelta(hours=5))]):
            self.cycle = self.run_cycle()
        self.assertEqual(self.checkins(), [])
        self.assertEqual(self.storage.get_booking("extra-14470")["local_status"], "confirmed")

    def test_next_cycle_does_not_resend_checkin(self):
        with self.fake_77([self.extra()]):
            self.run_cycle()
            self.cycle = self.run_cycle(at=self.now() + timedelta(minutes=2))
        self.assertEqual(self.checkins(), [])

    def test_removed_in_gestao77_before_start_stops_checkin(self):
        with self.fake_77([self.extra(starts_in=timedelta(hours=5))]):
            self.run_cycle()
        with self.fake_77([]):
            self.cycle = self.run_cycle()
        self.assertEqual(self.storage.get_booking("extra-14470")["local_status"], "removed")
        later = self.now() + timedelta(hours=4, minutes=30)
        with self.fake_77([]):
            self.cycle = self.run_cycle(at=later)
        self.assertEqual(self.checkins(), [])

    def test_gestao77_offline_does_not_break_the_cycle(self):
        with patch("nova_guarda.gestao77_service.Gestao77Client.from_env", side_effect=RuntimeError("fora")):
            cycle = self.run_cycle()
        self.assertIn("error", cycle["additional_durations"])
        self.assertTrue(cycle["run"])


if __name__ == "__main__":
    unittest.main()
