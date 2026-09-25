import logging
import os
import uuid
from pathlib import Path
from typing import Any

from nova_guarda.clients import WhatsAppOfficialClient, ZapiClient
from nova_guarda.config import DEV_FAKE_ZAPI, TERMS_PDF_PATH
from nova_guarda.flows import agenda_status_label, checkin_status_label, next_agenda_status
from nova_guarda.messages import build_terms_buttons_message
from nova_guarda.state import AGENDA_STATE, RECEIVED_EVENTS
from nova_guarda.timezone import br_timestamp


logger = logging.getLogger(__name__)


def timestamp() -> str:
    return br_timestamp()


def whatsapp_provider() -> str:
    return os.getenv("WHATSAPP_PROVIDER", "zapi").strip().lower()


def whatsapp_client() -> ZapiClient | WhatsAppOfficialClient:
    provider = whatsapp_provider()
    if provider in {"official", "whatsapp_official", "meta", "cloud"}:
        return WhatsAppOfficialClient()
    if provider == "zapi":
        return ZapiClient()
    raise RuntimeError(f"WHATSAPP_PROVIDER inválido: {provider}")


def update_agenda_state(phone: str, decision: str, text: str) -> dict[str, Any]:
    state = AGENDA_STATE.setdefault(
        phone,
        {
            "phone": phone,
            "status": "pending",
            "status_label": agenda_status_label("pending"),
            "agenda": {},
            "history": [],
        },
    )

    previous_status = state.get("status", "pending")
    next_status = next_agenda_status(previous_status, decision)

    state["status"] = next_status
    state["status_label"] = agenda_status_label(next_status)
    state["last_reply"] = text
    state["updated_at"] = timestamp()
    state.setdefault("history", []).append(
        {
            "at": state["updated_at"],
            "reply": text,
            "from": previous_status,
            "to": next_status,
        }
    )

    return state


def send_zapi_text(phone: str, message: str) -> dict[str, Any]:
    if DEV_FAKE_ZAPI:
        return fake_zapi_response("send-text", phone, {"message": message})

    payload = whatsapp_client().send_text(phone, message)
    logger.info("Resposta envio WhatsApp (%s): %s", whatsapp_provider(), payload)
    return payload


def send_zapi_document(phone: str, document_path: Path, caption: str) -> dict[str, Any]:
    if DEV_FAKE_ZAPI:
        return fake_zapi_response(
            "send-document/pdf",
            phone,
            {"fileName": "Termo de Aceite - Nova Guarda.pdf", "caption": caption},
        )

    payload = whatsapp_client().send_document_pdf(
        phone,
        document_path,
        "Termo de Aceite - Nova Guarda.pdf",
        caption,
    )
    logger.info("Resposta envio documento WhatsApp (%s): %s", whatsapp_provider(), payload)
    return payload


def send_zapi_agenda_buttons(phone: str, message: str) -> dict[str, Any]:
    buttons = [
        {"id": "booking_confirm", "label": "Confirmar"},
        {"id": "booking_decline", "label": "Recusar"},
    ]
    if DEV_FAKE_ZAPI:
        return fake_zapi_response("send-button-list", phone, {"message": message, "buttons": buttons})

    payload = whatsapp_client().send_button_list(
        phone,
        message,
        buttons,
    )
    logger.info("Resposta envio botões WhatsApp (%s): %s", whatsapp_provider(), payload)
    return payload


def send_zapi_terms_buttons(phone: str) -> dict[str, Any]:
    message = build_terms_buttons_message()
    buttons = [
        {"id": "terms_accept", "label": "Li e aceito"},
        {"id": "terms_reject", "label": "Não aceito"},
    ]
    if DEV_FAKE_ZAPI:
        return fake_zapi_response("send-button-list", phone, {"message": message, "buttons": buttons})

    payload = whatsapp_client().send_button_list(
        phone,
        message,
        buttons,
    )
    logger.info("Resposta envio botões de aceite WhatsApp (%s): %s", whatsapp_provider(), payload)
    return payload


def send_zapi_checkin_options(phone: str, message: str, use_location_link: bool = False, appointment_id: str = "") -> dict[str, Any]:
    arrived_id = "checkin2_arrived" if use_location_link else "checkin_arrived"
    if appointment_id:
        arrived_id = f"{arrived_id}:{appointment_id}"
    options = [
        {
            "id": arrived_id,
            "title": "Sim, cheguei",
            "description": "Confirmar chegada ao local de atendimento",
        },
        {
            "id": f"checkin_late:{appointment_id}" if appointment_id else "checkin_late",
            "title": "Vou atrasar",
            "description": "Informar previsão de atraso",
        },
        {
            "id": f"checkin_not_going:{appointment_id}" if appointment_id else "checkin_not_going",
            "title": "Não vou",
            "description": "Informar motivo para a Nova Guarda",
        },
    ]
    if DEV_FAKE_ZAPI:
        return fake_zapi_response("send-option-list", phone, {"message": message, "options": options})

    payload = whatsapp_client().send_option_list(
        phone,
        message,
        "Check-in",
        "Responder",
        options,
    )
    logger.info("Resposta envio check-in WhatsApp (%s): %s", whatsapp_provider(), payload)
    return payload


def send_zapi_checkout_button(phone: str, message: str, appointment_id: str) -> dict[str, Any]:
    buttons = [{"id": f"checkout_confirm:{appointment_id}", "label": "Finalizar"}]
    if DEV_FAKE_ZAPI:
        return fake_zapi_response("send-button-list", phone, {"message": message, "buttons": buttons})

    payload = whatsapp_client().send_button_list(phone, message, buttons)
    logger.info("Resposta envio check-out WhatsApp (%s): %s", whatsapp_provider(), payload)
    return payload


def send_zapi_late_buttons(phone: str, appointment_id: str = "") -> dict[str, Any]:
    buttons = [
        {"id": f"late_15:{appointment_id}" if appointment_id else "late_15", "label": "15 minutos"},
        {"id": f"late_30:{appointment_id}" if appointment_id else "late_30", "label": "30 minutos"},
        {"id": f"late_60:{appointment_id}" if appointment_id else "late_60", "label": "1 hora"},
    ]
    if DEV_FAKE_ZAPI:
        return fake_zapi_response("send-button-list", phone, {"message": "Qual é sua previsão de atraso?", "buttons": buttons})

    payload = whatsapp_client().send_button_list(
        phone,
        "Qual é sua previsão de atraso?",
        buttons,
    )
    logger.info("Resposta envio atraso WhatsApp (%s): %s", whatsapp_provider(), payload)
    return payload


def send_zapi_no_show_reasons(phone: str, appointment_id: str = "") -> dict[str, Any]:
    options = [
        {
            "id": f"reason_personal:{appointment_id}" if appointment_id else "reason_personal",
            "title": "Problema pessoal",
            "description": "Motivo pessoal",
        },
        {
            "id": f"reason_access:{appointment_id}" if appointment_id else "reason_access",
            "title": "Sem acesso ao local",
            "description": "Não consegui acessar o local",
        },
        {
            "id": f"reason_client_cancelled:{appointment_id}" if appointment_id else "reason_client_cancelled",
            "title": "Cliente cancelou",
            "description": "Atendimento cancelado no local",
        },
        {
            "id": f"reason_other:{appointment_id}" if appointment_id else "reason_other",
            "title": "Outro motivo",
            "description": "Motivo não listado",
        },
    ]
    if DEV_FAKE_ZAPI:
        return fake_zapi_response("send-option-list", phone, {"message": "Informe o motivo do não comparecimento:", "options": options})

    payload = whatsapp_client().send_option_list(
        phone,
        "Informe o motivo do não comparecimento:",
        "Motivo",
        "Escolher motivo",
        options,
    )
    logger.info("Resposta envio motivos WhatsApp (%s): %s", whatsapp_provider(), payload)
    return payload


def send_terms_document(phone: str, message: str) -> dict[str, Any]:
    return send_zapi_document(phone, TERMS_PDF_PATH, message)


def fake_zapi_response(endpoint: str, phone: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = {
        "ok": True,
        "fake": True,
        "endpoint": endpoint,
        "phone": phone,
        "messageId": f"fake-{uuid.uuid4().hex}",
        "payload": payload,
    }
    logger.info("Resposta fake Z-API: %s", response)
    return response


def append_fake_sent_message(phone: str, mode: str, message: str, response_payload: dict[str, Any]) -> None:
    if not isinstance(response_payload, dict) or not response_payload.get("fake"):
        return

    fake_payload = response_payload.get("payload", {}) if isinstance(response_payload, dict) else {}
    RECEIVED_EVENTS.appendleft(
        {
            "received_at": timestamp(),
            "payload": {
                "type": "AutoReply",
                "phone": phone,
                "mode": mode,
                "text": {"message": message},
                "buttons": fake_payload.get("buttons", []),
                "options": fake_payload.get("options", []),
                "response": response_payload,
            },
        }
    )
