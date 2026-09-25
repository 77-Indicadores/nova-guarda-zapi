import base64
import os
from pathlib import Path
from typing import Any

import requests

from nova_guarda.config import ZAPI_BASE_URL


class ZapiClient:
    def __init__(
        self,
        instance_id: str | None = None,
        instance_token: str | None = None,
        client_token: str | None = None,
        base_url: str = ZAPI_BASE_URL,
    ) -> None:
        self.instance_id = instance_id or os.getenv("ZAPI_INSTANCE_ID", "")
        self.instance_token = instance_token or os.getenv("ZAPI_INSTANCE_TOKEN", "")
        self.client_token = client_token or os.getenv("ZAPI_CLIENT_TOKEN", "")
        self.base_url = base_url.rstrip("/")

    def send_text(self, phone: str, message: str) -> dict[str, Any]:
        return self._post(
            "send-text",
            {"phone": phone, "message": message},
        )

    def send_document_pdf(self, phone: str, document_path: Path, file_name: str, caption: str) -> dict[str, Any]:
        document_base64 = base64.b64encode(document_path.read_bytes()).decode("ascii")
        return self._post(
            "send-document/pdf",
            {
                "phone": phone,
                "document": f"data:application/pdf;base64,{document_base64}",
                "fileName": file_name,
                "caption": caption,
            },
        )

    def send_button_list(self, phone: str, message: str, buttons: list[dict[str, str]]) -> dict[str, Any]:
        return self._post(
            "send-button-list",
            {
                "phone": phone,
                "message": message,
                "buttonList": {"buttons": buttons},
            },
        )

    def send_option_list(
        self,
        phone: str,
        message: str,
        title: str,
        button_label: str,
        options: list[dict[str, str]],
    ) -> dict[str, Any]:
        return self._post(
            "send-option-list",
            {
                "phone": phone,
                "message": message,
                "optionList": {
                    "title": title,
                    "buttonLabel": button_label,
                    "options": options,
                },
            },
        )

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        send_url = (
            f"{self.base_url}/instances/{self.instance_id}"
            f"/token/{self.instance_token}/{endpoint}"
        )

        response = requests.post(
            send_url,
            headers={
                "Client-Token": self.client_token,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )

        response.raise_for_status()
        response_payload = response.json()
        if isinstance(response_payload, dict) and response_payload.get("error"):
            raise RuntimeError(f"Z-API retornou erro: {response_payload}")

        return response_payload
