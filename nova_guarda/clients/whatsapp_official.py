import os
from pathlib import Path
from typing import Any

import requests

from nova_guarda.config import (
    TERMS_DOCUMENT_URL,
    WHATSAPP_OFFICIAL_BASE_URL,
    WHATSAPP_OFFICIAL_PHONE_NUMBER_ID,
    WHATSAPP_OFFICIAL_TOKEN,
)


class WhatsAppOfficialClient:
    def __init__(
        self,
        phone_number_id: str | None = None,
        token: str | None = None,
        base_url: str = WHATSAPP_OFFICIAL_BASE_URL,
    ) -> None:
        self.phone_number_id = phone_number_id or os.getenv("WHATSAPP_OFFICIAL_PHONE_NUMBER_ID", WHATSAPP_OFFICIAL_PHONE_NUMBER_ID)
        self.token = token or os.getenv("WHATSAPP_OFFICIAL_TOKEN", WHATSAPP_OFFICIAL_TOKEN)
        self.base_url = base_url.rstrip("/")

    def send_text(self, phone: str, message: str) -> dict[str, Any]:
        return self._post(
            {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": message},
            }
        )

    def send_document_pdf(self, phone: str, document_path: Path, file_name: str, caption: str) -> dict[str, Any]:
        document_url = os.getenv("TERMS_DOCUMENT_URL", TERMS_DOCUMENT_URL).strip()
        if not document_url:
            raise RuntimeError(
                "Configure TERMS_DOCUMENT_URL com uma URL pública do PDF para enviar documentos pela Cloud API."
            )

        return self._post(
            {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "document",
                "document": {
                    "link": document_url,
                    "filename": file_name,
                    "caption": caption,
                },
            }
        )

    def send_button_list(self, phone: str, message: str, buttons: list[dict[str, str]]) -> dict[str, Any]:
        return self._post(
            {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": message},
                    "action": {
                        "buttons": [
                            {
                                "type": "reply",
                                "reply": {
                                    "id": button["id"],
                                    "title": button.get("label") or button.get("title") or button["id"],
                                },
                            }
                            for button in buttons[:3]
                        ]
                    },
                },
            }
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
            {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "interactive",
                "interactive": {
                    "type": "list",
                    "body": {"text": message},
                    "action": {
                        "button": button_label,
                        "sections": [
                            {
                                "title": title,
                                "rows": [
                                    {
                                        "id": option["id"],
                                        "title": option.get("title") or option.get("label") or option["id"],
                                        "description": option.get("description", ""),
                                    }
                                    for option in options
                                ],
                            }
                        ],
                    },
                },
            }
        )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.phone_number_id or not self.token:
            raise RuntimeError("Configure WHATSAPP_OFFICIAL_PHONE_NUMBER_ID e WHATSAPP_OFFICIAL_TOKEN.")

        response = requests.post(
            f"{self.base_url}/{self.phone_number_id}/messages",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
