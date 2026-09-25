import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class RealGestao77SeedTest(unittest.TestCase):
    """Cobre o caminho 'não-fake' do modo teste assistido: criação real de
    cooperado/appointment na 77Gestão. Usa um Gestao77Client mockado com o
    payload de resposta confirmado manualmente contra a API real, para não
    depender de rede nem criar dados reais durante os testes automatizados."""

    def setUp(self):
        self.database = tempfile.NamedTemporaryFile(delete=False)
        self.database.close()
        os.environ["DATABASE_PATH"] = self.database.name
        os.environ["DEV_FAKE_ZAPI"] = "true"
        os.environ["DEV_FAKE_GESTAO77"] = "false"
        os.environ["WHATSAPP_PROVIDER"] = "zapi"
        os.environ["GESTAO77_EMAIL"] = ""

        import nova_guarda.config as config
        import nova_guarda.services as services
        import nova_guarda.storage as storage
        from nova_guarda.state import AGENDA_STATE, RECEIVED_EVENTS, TERMS_STATE

        config.DATABASE_PATH = Path(self.database.name)
        storage.DATABASE_PATH = Path(self.database.name)
        config.DATABASE_URL = ""
        storage.DATABASE_URL = ""
        config.DEV_FAKE_ZAPI = True
        services.DEV_FAKE_ZAPI = True

        # gestao77_mode fica "real" por padrão (SETTING_DEFAULTS), então
        # fake_gestao77_enabled() cai no fallback do env DEV_FAKE_GESTAO77=false.
        RECEIVED_EVENTS.clear()
        AGENDA_STATE.clear()
        TERMS_STATE.clear()

        self.storage = storage
        self.phone = "5513999199293"

    def tearDown(self):
        Path(self.database.name).unlink(missing_ok=True)

    def test_seed_cooperator_creates_real_partner_via_api(self):
        from nova_guarda.gestao77_service import seed_test_cooperator

        fake_client = Mock()
        fake_client.create_partner.return_value = {
            "status": "success",
            "partner": {
                "id": 603,
                "name": "TESTE Codex Cooperado",
                "type": "cooperado",
                "active": 1,
                "phones": [{"country_code": "+55", "number": 13999199293, "type": 3}],
            },
        }

        with patch("nova_guarda.gestao77_service.Gestao77Client.from_env", return_value=fake_client):
            cooperator = seed_test_cooperator(self.phone, "TESTE Codex Cooperado")

        self.assertEqual(cooperator["onboarding_status"], "accepted")
        self.assertEqual(cooperator["partner_id"], "603")

        payload = fake_client.create_partner.call_args[0][0]
        self.assertEqual(payload["type"], "cooperado")
        self.assertEqual(payload["person_type"], 1)
        self.assertTrue(payload["nrlp"])
        self.assertEqual(payload["phones"], [{"country_code": "+55", "number": 13999199293, "type": 3}])
        self.assertIn("cooperative_member_settings", payload)
        self.assertEqual(payload["cooperative_member_settings"]["service_id"], 367)

    def test_seed_booking_creates_real_appointment_via_api(self):
        from nova_guarda.gestao77_service import seed_test_booking_and_send

        self.storage.upsert_cooperator(
            self.phone,
            "accepted",
            {"id": 603, "name": "TESTE Codex Cooperado", "type": "cooperado", "active": 1},
        )

        fake_client = Mock()
        fake_client.create_appointment.return_value = {
            "status": "success",
            "appointment": {
                "id": 23,
                "partner_id": 603,
                "booking_id": 3,
                "booking": {"id": 3, "status": "awaiting_approval"},
            },
        }
        # send_booking_to_partner faz sync do status "sent" via update_booking_schedule_response,
        # que também chama Gestao77Client.from_env() quando não está em modo fake.
        fake_client.update_booking_schedule_response.return_value = {"status": "success"}

        with patch("nova_guarda.gestao77_service.Gestao77Client.from_env", return_value=fake_client):
            result = seed_test_booking_and_send(self.phone, "Cliente Teste")

        self.assertEqual(result["booking_id"], "3")
        self.assertEqual(result["appointment_id"], "23")

        payload = fake_client.create_appointment.call_args[0][0]
        self.assertEqual(payload["partner_id"], 603)
        self.assertEqual(payload["customer_id"], 7957)
        self.assertEqual(payload["type"], "appointment")
        self.assertTrue(payload["start_at"].endswith("Z"))
        self.assertTrue(payload["end_at"].endswith("Z"))

        booking = self.storage.get_booking("3")
        self.assertEqual(booking["local_status"], "sent")
        self.assertEqual(booking["appointment_id"], "23")

    def test_seed_booking_accepts_top_level_booking_id_fallback(self):
        """Reproduz o bug real: a 77Gestão cria o appointment e o booking de
        verdade (confirmado via GET logo em seguida), mas a resposta imediata
        do POST /appointments às vezes não traz o objeto "booking" aninhado
        populado ainda - só o booking_id solto no nível raiz do appointment.
        Isso não pode ser tratado como falha: o booking já existe de verdade
        na 77Gestão, criar de novo só geraria escalas órfãs."""
        from nova_guarda.gestao77_service import seed_test_booking_and_send

        self.storage.upsert_cooperator(
            self.phone,
            "accepted",
            {"id": 602, "name": "Vinicius Moreira - Teste WhatsApp", "type": "cooperado", "active": 1},
        )

        fake_client = Mock()
        fake_client.create_appointment.return_value = {
            "status": "success",
            "appointment": {
                "id": 27,
                "partner_id": 602,
                "booking_id": 8,
                "booking": None,
            },
        }
        fake_client.update_booking_schedule_response.return_value = {"status": "success"}

        with patch("nova_guarda.gestao77_service.Gestao77Client.from_env", return_value=fake_client):
            result = seed_test_booking_and_send(self.phone, "Cliente Teste")

        self.assertEqual(result["booking_id"], "8")
        self.assertEqual(result["appointment_id"], "27")

        booking = self.storage.get_booking("8")
        self.assertEqual(booking["local_status"], "sent")

    def test_seed_booking_survives_gestao77_schedule_response_failure(self):
        """Reproduz o bug relatado: a 77Gestão cria o appointment normalmente,
        mas o endpoint de sincronização de status de volta (schedule-response)
        falha (ex.: 422, ainda não validado contra a API real). A escala já
        foi criada e a mensagem de WhatsApp já foi enviada nesse ponto, então
        isso não pode aparecer como falha do teste assistido para quem clicou
        em "Rodar teste completo"."""
        from nova_guarda.gestao77_service import seed_test_booking_and_send

        self.storage.upsert_cooperator(
            self.phone,
            "accepted",
            {"id": 603, "name": "TESTE Codex Cooperado", "type": "cooperado", "active": 1},
        )

        fake_client = Mock()
        fake_client.create_appointment.return_value = {
            "status": "success",
            "appointment": {
                "id": 23,
                "partner_id": 603,
                "booking_id": 4,
                "booking": {"id": 4, "status": "awaiting_approval"},
            },
        }
        fake_client.update_booking_schedule_response.side_effect = RuntimeError(
            "422 Client Error: Unprocessable Content for url: "
            "https://app.77gestao.com.br/api/v1/bookings/4/schedule-response"
        )

        with patch("nova_guarda.gestao77_service.Gestao77Client.from_env", return_value=fake_client):
            result = seed_test_booking_and_send(self.phone, "Cliente Teste")

        self.assertEqual(result["booking_id"], "4")
        self.assertEqual(result["appointment_id"], "23")
        self.assertIn("gestao77_sync_error", result["send_result"])

        booking = self.storage.get_booking("4")
        self.assertEqual(booking["local_status"], "sent")
        self.assertTrue(booking["sent_at"])

    def test_seed_booking_without_partner_id_is_rejected(self):
        from nova_guarda.gestao77_service import seed_test_booking_and_send

        self.storage.upsert_cooperator(self.phone, "accepted", {"name": "Sem partner id"})

        with self.assertRaises(RuntimeError):
            seed_test_booking_and_send(self.phone, "Cliente Teste")


if __name__ == "__main__":
    unittest.main()
