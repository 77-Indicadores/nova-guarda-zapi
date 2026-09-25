import os
import tempfile
import unittest
from pathlib import Path


class AssistedTestModeTest(unittest.TestCase):
    """Cobre o modo teste assistido: criar cooperado + escala local sem depender
    de a 77Gestão suportar criação de dados, permitindo repetir a demonstração
    do fluxo do cooperado quantas vezes for preciso."""

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

    def tearDown(self):
        Path(self.database.name).unlink(missing_ok=True)

    def test_seed_cooperator_creates_accepted_cooperator(self):
        from nova_guarda.gestao77_service import seed_test_cooperator

        cooperator = seed_test_cooperator(self.phone, "Fulano de Teste")

        self.assertEqual(cooperator["onboarding_status"], "accepted")
        self.assertEqual(cooperator["partner_name"], "Fulano de Teste")
        self.assertTrue(self.storage.cooperator_has_accepted_terms(self.phone))

    def test_seed_cooperator_requires_phone(self):
        from nova_guarda.gestao77_service import seed_test_cooperator

        with self.assertRaises(ValueError):
            seed_test_cooperator("", "Sem telefone")

    def test_seed_booking_without_cooperator_is_rejected(self):
        from nova_guarda.gestao77_service import seed_test_booking_and_send

        with self.assertRaises(PermissionError):
            seed_test_booking_and_send(self.phone, "Cliente Teste")

    def test_seed_booking_creates_and_sends_real_pipeline(self):
        from nova_guarda.gestao77_service import seed_test_booking_and_send, seed_test_cooperator

        seed_test_cooperator(self.phone, "Fulano de Teste")
        result = seed_test_booking_and_send(self.phone, "Cliente Teste")

        booking = self.storage.get_booking(result["booking_id"])
        self.assertEqual(booking["local_status"], "sent")
        self.assertEqual(booking["phone"], self.phone)
        self.assertTrue(booking["sent_at"])

        # A escala deve seguir o pipeline real dali pra frente: responder no
        # webhook como o cooperado confirmando presença.
        response = self.client.post(
            "/webhook",
            json={
                "type": "ReceivedCallback",
                "fromMe": False,
                "isGroup": False,
                "phone": self.phone,
                "text": {"message": "booking_confirm"},
            },
        )
        self.assertEqual(response.status_code, 200)
        booking = self.storage.get_booking(result["booking_id"])
        self.assertEqual(booking["local_status"], "confirmed")

    def test_seed_booking_can_be_repeated_for_multiple_bookings(self):
        from nova_guarda.gestao77_service import seed_test_booking_and_send, seed_test_cooperator

        seed_test_cooperator(self.phone, "Fulano de Teste")
        first = seed_test_booking_and_send(self.phone, "Cliente 1")
        second = seed_test_booking_and_send(self.phone, "Cliente 2")

        self.assertNotEqual(first["booking_id"], second["booking_id"])
        self.assertNotEqual(first["appointment_id"], second["appointment_id"])

    def test_route_seed_cooperator_via_ui(self):
        response = self.client.post("/teste-assistido/cooperado", data={"phone": self.phone, "client_name": "Via UI"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.storage.cooperator_has_accepted_terms(self.phone))

    def test_route_seed_escala_via_ui(self):
        self.client.post("/teste-assistido/cooperado", data={"phone": self.phone, "client_name": "Via UI"})
        response = self.client.post("/teste-assistido/escala", data={"phone": self.phone, "client_name": "Via UI"})
        self.assertEqual(response.status_code, 302)
        bookings = self.storage.list_bookings("sent")
        self.assertEqual(len(bookings), 1)


if __name__ == "__main__":
    unittest.main()
