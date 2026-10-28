import unittest
from unittest.mock import Mock, patch

import requests

from tests import test_checkin_checkout


def http_error(status_code, body):
    response = Mock(status_code=status_code)
    response.json.return_value = body
    return requests.HTTPError(f"{status_code} Client Error", response=response)


class FlowRobustnessTest(unittest.TestCase):
    """Registrar e responder são passos separados, e a resposta diz o estado
    real: casos vistos em produção ou achados na auditoria do fluxo."""

    base = test_checkin_checkout.CheckinCheckoutFlowTest
    setUp = base.setUp
    tearDown = base.tearDown
    accept_cooperator = base.accept_cooperator
    create_booking = base.create_booking
    post_checkin_send = base.post_checkin_send
    post_checkout_send = base.post_checkout_send
    send_reply = base.send_reply
    send_location = base.send_location

    def replies(self, status=None):
        return [
            event["payload"]
            for event in self.storage.list_conversation_events(200)
            if event["payload"].get("type") == "AutoReply" and (status is None or event["payload"].get("status") == status)
        ]

    def checked_in(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()
        self.send_reply(f"checkin_arrived:{self.appointment_id}")
        self.send_location()

    # 1. recusa da 77Gestão não vira "registrado" ---------------------------------
    def test_checkin_refused_by_gestao77_is_not_reported_as_done(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()
        self.send_reply(f"checkin_arrived:{self.appointment_id}")

        refused = http_error(422, {"message": "O atendimento não está em andamento."})
        with patch("nova_guarda.gestao77_service.update_appointment_status", side_effect=refused):
            self.send_location()

        appointment = self.storage.get_appointment(self.appointment_id)
        self.assertEqual(appointment["local_status"], "location_pending")
        reply = self.replies("gestao77_rejected")[0]["text"]["message"]
        self.assertIn("não aceitou o check-in", reply)
        self.assertIn("O atendimento não está em andamento.", reply)
        self.assertNotIn("registrado com sucesso", reply)
        self.assertTrue(any(alert["kind"] == "gestao77_rejected" for alert in self.storage.list_alerts()))

    def test_gestao77_unavailable_still_registers_locally_for_retry(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()
        self.send_reply(f"checkin_arrived:{self.appointment_id}")

        with patch(
            "nova_guarda.gestao77_service.update_appointment_status", side_effect=requests.ConnectionError("fora")
        ):
            self.send_location()

        self.assertEqual(self.storage.get_appointment(self.appointment_id)["local_status"], "checked_in")
        self.assertIn("registrado com sucesso", self.replies("checked_in")[0]["text"]["message"])

    # 2. localização vai para o atendimento que pediu -------------------------------
    def test_location_goes_to_the_appointment_that_asked_for_it(self):
        self.accept_cooperator()
        self.create_booking("b-old", "apt-old", start_at="2026-08-29T12:00:00Z")
        self.create_booking("b-new", "apt-new", start_at="2026-08-30T12:00:00Z")
        self.post_checkin_send("apt-old", "b-old")
        self.post_checkin_send("apt-new", "b-new")
        with patch("nova_guarda.storage.timestamp", return_value="2026-08-29T09:00:00"):
            self.send_reply("checkin_arrived:apt-old")  # ontem: tocou e nunca mandou a localização
        with patch("nova_guarda.storage.timestamp", return_value="2026-08-30T09:00:00"):
            self.send_reply("checkin_arrived:apt-new")
        # Algo mexe no atendimento antigo depois (lembrete, atualização da escala).
        with self.storage.connect() as conn:
            conn.execute("UPDATE appointments SET updated_at = '2026-08-30T10:00:00' WHERE appointment_id = 'apt-old'")

        self.send_location()

        self.assertEqual(self.storage.get_appointment("apt-new")["local_status"], "checked_in")
        self.assertEqual(self.storage.get_appointment("apt-old")["local_status"], "location_pending")

    # 3. atraso / não vou: registrado não vira "não consegui" -----------------------
    def test_late_is_answered_as_registered_even_if_the_alert_fails(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()

        with patch("nova_guarda.routes.raise_alert", side_effect=RuntimeError("banco ocupado")):
            self.send_reply(f"late_15:{self.appointment_id}")

        self.assertEqual(self.storage.get_appointment(self.appointment_id)["local_status"], "late_reported")
        self.assertIn("Atraso de 15 minutos registrado", self.replies("late_reported")[0]["text"]["message"])
        self.assertEqual(self.replies("refused"), [])

    def test_late_after_arriving_asks_for_the_location(self):
        self.accept_cooperator()
        self.create_booking()
        self.post_checkin_send()
        self.send_reply(f"checkin_arrived:{self.appointment_id}")

        self.send_reply(f"late_15:{self.appointment_id}")

        self.assertIn("envie sua localização atual", self.replies("refused")[0]["text"]["message"])

    def test_finalizar_with_failed_location_button_gets_the_request_as_text(self):
        self.checked_in()
        self.post_checkout_send()

        with patch("nova_guarda.routes.send_zapi_location_request", side_effect=RuntimeError("meta fora")):
            self.send_reply(f"checkout_confirm:{self.appointment_id}")

        self.assertEqual(self.storage.get_appointment(self.appointment_id)["local_status"], "checkout_location_pending")
        reply = self.replies("location_requested")[0]["text"]["message"]
        self.assertIn("check-out", reply)
        self.assertEqual(self.replies("presence_sync_error"), [])

    # 4. resposta digitada vale para as escalas do mês que aguardam resposta --------
    def test_typed_confirmation_answers_every_schedule_waiting_for_an_answer(self):
        self.accept_cooperator()
        self.create_booking("old", "apt-old", status="confirmed")
        self.create_booking("62", "apt-62", status="sent")
        self.create_booking("63", "apt-63", status="sent")
        # O estado em memória aponta para a escala do último check-in.
        self.post_checkin_send("apt-old", "old")

        self.send_reply("confirmar")

        self.assertEqual(self.storage.get_booking("62")["local_status"], "confirmed")
        self.assertEqual(self.storage.get_booking("63")["local_status"], "confirmed")

    # 5. passo feito direto no app da 77Gestão não gera lembrete -------------------
    def test_no_reminder_when_checkout_was_done_in_the_gestao77_app(self):
        from datetime import timedelta

        from nova_guarda.automation import follow_up
        from nova_guarda.timezone import br_now

        self.checked_in()
        self.post_checkout_send()
        self.send_reply(f"checkout_confirm:{self.appointment_id}")
        appointment = self.storage.get_appointment(self.appointment_id)

        client = Mock()
        client.get_appointment.return_value = {"checked_out_at": "2026-08-30T21:00:00.000000Z"}
        now = br_now()
        with patch("nova_guarda.gestao77_service.fake_gestao77_enabled", return_value=False), patch(
            "nova_guarda.gestao77_service.Gestao77Client.from_env", return_value=client
        ), patch("nova_guarda.automation.send_followup_reminder") as reminder:
            item = follow_up(
                "appointment", self.appointment_id, appointment, now - timedelta(minutes=20), timedelta(minutes=15), now
            )

        reminder.assert_not_called()
        self.assertEqual(item["action"], "reconciled")
        self.assertEqual(self.storage.get_appointment(self.appointment_id)["local_status"], "checked_out")

    # 6. texto livre do cooperado chega à equipe -----------------------------------
    def test_free_text_from_cooperator_becomes_a_team_alert(self):
        self.accept_cooperator()

        self.send_reply("Está errado isso a escala está sem folgas e com horários errados")
        self.send_reply("ok")

        alerts = [alert for alert in self.storage.list_alerts() if alert["kind"] == "cooperator_message"]
        self.assertEqual(len(alerts), 1)
        self.assertIn("sem folgas", alerts[0]["message"])
        self.assertIn("encaminhada para a equipe", self.replies("unrecognized")[0]["text"]["message"])

    # 7. erro inesperado no webhook não volta 500 para o provider -------------------
    def test_unexpected_webhook_error_returns_ok_and_alerts(self):
        self.accept_cooperator()
        with patch("nova_guarda.routes.handle_webhook_payload", side_effect=TypeError("bug")):
            response = self.send_reply("qualquer")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(alert["kind"] == "webhook_error" for alert in self.storage.list_alerts()))


if __name__ == "__main__":
    unittest.main()
