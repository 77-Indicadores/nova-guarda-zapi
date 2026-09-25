import os
from typing import Any

import requests


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
            if isinstance(member, dict) and str(member.get("schedule_status", "")) in statuses
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

    def list_appointments_by_booking(self, booking_id: str | int) -> dict[str, Any]:
        return self._get(f"/appointments?booking_id={booking_id}&skipLoader=1")

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
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("GESTAO77_BASE_URL não configurado.")
        if not self.token:
            raise RuntimeError("Configure GESTAO77_TOKEN ou GESTAO77_EMAIL/GESTAO77_PASSWORD.")

        response = requests.post(
            f"{self.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
