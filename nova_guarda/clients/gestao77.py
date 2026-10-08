import os
from typing import Any

import requests


def _raise_with_body(response: requests.Response) -> None:
    """Como o raise_for_status() padrão não inclui o corpo da resposta, os
    erros de validação da 77Gestão (400/422 com detalhes em JSON) ficavam
    invisíveis nos logs e em Registros. Anexa o corpo (truncado) na mensagem
    para dar visibilidade real do motivo da rejeição."""
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        body = (response.text or "").strip()
        if body:
            raise requests.HTTPError(f"{exc} | resposta: {body[:500]}", response=response) from exc
        raise


class Gestao77Client:
    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("GESTAO77_BASE_URL", "https://app.77gestao.com.br/api/v1")).rstrip("/")
        self.token = token or os.getenv("GESTAO77_TOKEN", "") or os.getenv("GESTAO77_JWT", "")

    @classmethod
    def from_env(cls) -> "Gestao77Client":
        client = cls()
        if not client.token and os.getenv("GESTAO77_EMAIL"):
            client.token = client.login(
                os.getenv("GESTAO77_EMAIL", ""),
                os.getenv("GESTAO77_PASSWORD", ""),
            )
        return client

    def login(self, email: str, password: str) -> str:
        if not email or not password:
            raise RuntimeError("Configure GESTAO77_EMAIL e GESTAO77_PASSWORD.")

        response = requests.post(
            f"{self.base_url}/login",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
            },
            json={"email": email, "password": password},
            timeout=15,
        )
        response.raise_for_status()

        token = response.json().get("token")
        if not token:
            raise RuntimeError("Login no 77Gestao não retornou token.")
        return str(token)

    def list_partner_booking_summary(
        self,
        month: int,
        year: int,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._get(f"/bookings/summary/partner?month={month}&year={year}&skipLoader=1")

    def list_partner_bookings_by_status(
        self,
        month: int,
        year: int,
        statuses: set[str],
        body: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        payload = self.list_partner_booking_summary(month, year, body)
        members = payload.get("cooperative_members", [])
        if not isinstance(members, list):
            return []
        return [
            member
            for member in members
            # O resumo lista todos os cooperados, inclusive quem não tem escala
            # no mês (booking_id nulo): só interessa quem tem booking de fato.
            if isinstance(member, dict)
            and member.get("booking_id")
            and str(member.get("schedule_status", "")) in statuses
        ]

    def update_appointment_status(self, appointment_id: str, status: str, address: str = "") -> dict[str, Any]:
        return self._post(
            f"/appointments/{appointment_id}/status",
            {
                "status": status,
                "address": address,
            },
        )

    def update_booking_schedule_response(self, booking_id: str, status: str) -> dict[str, Any]:
        return self._post(
            f"/bookings/{booking_id}/schedule-response",
            {
                "status": status,
            },
        )

    def get_partner(self, partner_id: str | int) -> dict[str, Any]:
        return self._get(f"/partners/{partner_id}")

    def list_partners(self, partner_type: str = "cooperado") -> dict[str, Any]:
        query = f"?type={partner_type}&skipLoader=1" if partner_type else "?skipLoader=1"
        return self._get(f"/partners{query}")

    def create_partner(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/partners", payload)

    def create_appointment(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/appointments", payload)

    def list_appointments_by_booking(self, booking_id: str | int) -> dict[str, Any]:
        """A 77Gestão ignora o filtro booking_id e devolve os appointments de
        todas as escalas (confirmado contra a API real), então o filtro é
        refeito aqui: sem isso uma escala herdaria atendimentos de outros
        cooperados."""
        payload = self._get(f"/appointments?booking_id={booking_id}&skipLoader=1")
        appointments = payload.get("appointments") if isinstance(payload, dict) else None
        if isinstance(appointments, list):
            payload["appointments"] = [
                item
                for item in appointments
                if isinstance(item, dict) and str(item.get("booking_id")) == str(booking_id)
            ]
        return payload

    def get_booking_status(self, booking_id: str | int) -> str:
        """Status atual da escala na 77Gestão (vem junto dos appointments dela)."""
        appointments = self.list_appointments_by_booking(booking_id).get("appointments") or []
        for item in appointments:
            booking = item.get("booking") if isinstance(item, dict) else None
            if isinstance(booking, dict) and booking.get("status"):
                return str(booking["status"])
        return ""

    def get_appointment(self, appointment_id: str | int) -> dict[str, Any]:
        payload = self._get(f"/appointments/{appointment_id}")
        appointment = payload.get("appointment", payload) if isinstance(payload, dict) else {}
        return appointment if isinstance(appointment, dict) else {}

    def release_booking_for_send(self, booking_id: str | int) -> dict[str, Any]:
        """Mesmo passo do botão "Enviar escala" da 77Gestão: leva a escala de
        "aguardando aprovação" para "aguardando envio", único status a partir
        do qual ela aceita sent/confirmed/declined."""
        return self._request(
            "PUT", f"/bookings/{booking_id}/cooperative-member-schedule-status", {"status": "awaiting_send"}
        )

    def _get(self, path: str) -> dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("GESTAO77_BASE_URL não configurado.")
        if not self.token:
            raise RuntimeError("Configure GESTAO77_TOKEN ou GESTAO77_EMAIL/GESTAO77_PASSWORD.")

        response = requests.get(
            f"{self.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json, text/plain, */*",
            },
            timeout=30,
        )
        _raise_with_body(response)
        return response.json()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, payload)

    def _request(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("GESTAO77_BASE_URL não configurado.")
        if not self.token:
            raise RuntimeError("Configure GESTAO77_TOKEN ou GESTAO77_EMAIL/GESTAO77_PASSWORD.")

        response = requests.request(
            method,
            f"{self.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
            },
            json=payload,
            timeout=30,
        )
        _raise_with_body(response)
        return response.json()
