import logging
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nova_guarda.clients import WhatsAppOfficialClient, ZapiClient
from nova_guarda.config import (
    DEV_FAKE_ZAPI,
    TERMS_PDF_PATH,
    WHATSAPP_TEMPLATE_BOOKING,
    WHATSAPP_TEMPLATE_CHECKIN,
    WHATSAPP_TEMPLATE_CHECKOUT,
    WHATSAPP_TEMPLATE_LANGUAGE,
    WHATSAPP_TEMPLATE_TERMS,
)
from nova_guarda.flows import agenda_status_label, checkin_status_label, next_agenda_status
from nova_guarda.messages import build_terms_buttons_message, format_schedule
from nova_guarda.state import AGENDA_STATE, RECEIVED_EVENTS
from nova_guarda.timezone import BR_TZ, br_now, br_timestamp


logger = logging.getLogger(__name__)


def timestamp() -> str:
    return br_timestamp()


def whatsapp_provider() -> str:
    try:
        from nova_guarda.storage import get_setting

        configured = get_setting("active_provider").strip().lower()
        if configured:
            return configured
    except Exception:
        pass
    return os.getenv("WHATSAPP_PROVIDER", "zapi").strip().lower()


def whatsapp_client() -> ZapiClient | WhatsAppOfficialClient:
    provider = whatsapp_provider()
    if provider in {"official", "whatsapp_official", "meta", "cloud"}:
        return WhatsAppOfficialClient()
    if provider == "zapi":
        return ZapiClient()
    raise RuntimeError(f"WHATSAPP_PROVIDER inválido: {provider}")


OFFICIAL_PROVIDERS = {"official", "whatsapp_official", "meta", "cloud"}
WHATSAPP_WINDOW_HOURS = 24


def is_simulated_phone(phone: str) -> bool:
    """Cooperado de teste com canal "Chat Dev": em modo teste, nada do que o
    bot envia para ele passa pelo WhatsApp. A mensagem é só registrada na
    conversa e aparece no Chat Dev, que funciona como alternativa ao WhatsApp
    (o resto do fluxo, inclusive a 77Gestão, segue igual)."""
    import json

    from nova_guarda.storage import get_setting

    try:
        if get_setting("operation_mode").strip().lower() != "test":
            return False
        items = json.loads(get_setting("test_cooperators") or "[]")
    except Exception:
        return False
    digits = "".join(char for char in str(phone) if char.isdigit())
    return any(
        isinstance(item, dict) and item.get("channel") == "chat" and str(item.get("phone")) == digits
        for item in (items if isinstance(items, list) else [])
    )


def meta_templates_enabled() -> bool:
    """Templates só valem no provider oficial e quando ligados em Configurações
    (depois de aprovados na conta da Meta que envia as mensagens)."""
    if DEV_FAKE_ZAPI or whatsapp_provider() not in OFFICIAL_PROVIDERS:
        return False
    from nova_guarda.storage import get_setting

    return get_setting("meta_templates_enabled") == "1"


def whatsapp_window_open(phone: str) -> bool:
    """A Meta só aceita mensagem comum até 24h depois da última mensagem do cooperado."""
    from nova_guarda.storage import last_inbound_message_at

    try:
        last_inbound = datetime.strptime(last_inbound_message_at(phone), "%d/%m/%Y %H:%M:%S").replace(tzinfo=BR_TZ)
    except ValueError:
        return False
    return br_now() - last_inbound < timedelta(hours=WHATSAPP_WINDOW_HOURS)


def template_text(value: Any, fallback: str = "Não informado") -> str:
    # Variável de template não pode ser vazia nem ter quebra de linha.
    return " ".join(str(value or "").split())[:300] or fallback


def template_schedule(agenda_data: dict[str, str]) -> tuple[str, str]:
    date_text, time_text = format_schedule(agenda_data)
    return template_text(date_text), template_text(time_text)


def send_meta_template(phone: str, name: str, body_parameters: list[str], button_payloads: list[str]) -> dict[str, Any]:
    payload = WhatsAppOfficialClient().send_template(
        phone, name, WHATSAPP_TEMPLATE_LANGUAGE, body_parameters, button_payloads
    )
    logger.info("Resposta envio template %s WhatsApp: %s", name, payload)
    return payload


def schedule_template_parameters(agenda_data: dict[str, str], with_address: bool = True) -> list[str]:
    date_text, time_text = template_schedule(agenda_data)
    parameters = [template_text(agenda_data.get("client_name"), "cooperado(a)")]
    if with_address:
        parameters.append(template_text(agenda_data.get("client_address"), "Endereço não informado"))
    return parameters + [date_text, time_text, template_text(agenda_data.get("service"), "Atendimento Nova Guarda")]


def terms_template_required(phone: str) -> bool:
    return meta_templates_enabled() and not is_simulated_phone(phone) and not whatsapp_window_open(phone)


def send_terms_template(phone: str, name: str) -> dict[str, Any]:
    return send_meta_template(
        phone, WHATSAPP_TEMPLATE_TERMS, [template_text(name, "cooperado(a)")], ["terms_accept", "terms_reject"]
    )


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
    if DEV_FAKE_ZAPI or is_simulated_phone(phone):
        return fake_zapi_response("send-text", phone, {"message": message})

    payload = whatsapp_client().send_text(phone, message)
    logger.info("Resposta envio WhatsApp (%s): %s", whatsapp_provider(), payload)
    return payload


def send_zapi_document(phone: str, document_path: Path, caption: str) -> dict[str, Any]:
    if DEV_FAKE_ZAPI or is_simulated_phone(phone):
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


def send_zapi_agenda_buttons(
    phone: str,
    message: str,
    booking_id: str = "",
    agenda_data: dict[str, str] | None = None,
) -> dict[str, Any]:
    suffix = f":{booking_id}" if booking_id else ""
    buttons = [
        {"id": f"booking_confirm{suffix}", "label": "Confirmar"},
        {"id": f"booking_decline{suffix}", "label": "Recusar"},
    ]
    if DEV_FAKE_ZAPI or is_simulated_phone(phone):
        return fake_zapi_response("send-button-list", phone, {"message": message, "buttons": buttons})

    if agenda_data is not None and meta_templates_enabled():
        return send_meta_template(
            phone,
            WHATSAPP_TEMPLATE_BOOKING,
            schedule_template_parameters(agenda_data),
            [button["id"] for button in buttons],
        )

    payload = whatsapp_client().send_button_list(
        phone,
        message,
        buttons,
    )
    logger.info("Resposta envio botões WhatsApp (%s): %s", whatsapp_provider(), payload)
    return payload


def send_zapi_schedule(
    phone: str,
    message: str,
    booking_id: str,
    schedule_data: dict[str, Any],
    pdf_content: bytes | None = None,
    file_name: str = "escala.pdf",
) -> dict[str, Any]:
    """Envia a escala de trabalho do período com o PDF e os botões
    Confirmar / Recusar, que carregam o booking."""
    buttons = [
        {"id": f"booking_confirm:{booking_id}", "label": "Confirmar"},
        {"id": f"booking_decline:{booking_id}", "label": "Recusar"},
    ]
    if DEV_FAKE_ZAPI or is_simulated_phone(phone):
        payload: dict[str, Any] = {"message": message, "buttons": buttons}
        if pdf_content:
            payload.update({"fileName": file_name, "document_url": f"/escalas/{booking_id}/pdf"})
        return fake_zapi_response("send-button-list", phone, payload)

    client = whatsapp_client()
    if isinstance(client, WhatsAppOfficialClient):
        media_id = client.upload_media(pdf_content, file_name) if pdf_content else ""
        if meta_templates_enabled():
            period = template_text(schedule_data.get("schedule_period"))
            days = int(schedule_data.get("schedule_days") or 0)
            span = f"{days} dia(s), de {schedule_data.get('schedule_first')} a {schedule_data.get('schedule_last')}"
            response = client.send_template(
                phone,
                WHATSAPP_TEMPLATE_BOOKING,
                WHATSAPP_TEMPLATE_LANGUAGE,
                [template_text(schedule_data.get("client_name"), "cooperado(a)"), period, template_text(span)],
                [button["id"] for button in buttons],
                header_document_id=media_id,
                header_document_name=file_name,
            )
        else:
            response = client.send_button_list(phone, message, buttons, document_id=media_id, document_name=file_name)
    else:
        if pdf_content:
            caption = f"Escala de trabalho - {schedule_data.get('schedule_period') or ''}".strip(" -")
            client.send_document_pdf_bytes(phone, pdf_content, file_name, caption)
        response = client.send_button_list(phone, message, buttons)
    logger.info("Resposta envio escala WhatsApp (%s): %s", whatsapp_provider(), response)
    return response


def send_zapi_terms_buttons(phone: str) -> dict[str, Any]:
    message = build_terms_buttons_message()
    buttons = [
        {"id": "terms_accept", "label": "Li e aceito"},
        {"id": "terms_reject", "label": "Não aceito"},
    ]
    if DEV_FAKE_ZAPI or is_simulated_phone(phone):
        return fake_zapi_response("send-button-list", phone, {"message": message, "buttons": buttons})

    payload = whatsapp_client().send_button_list(
        phone,
        message,
        buttons,
    )
    logger.info("Resposta envio botões de aceite WhatsApp (%s): %s", whatsapp_provider(), payload)
    return payload


def send_zapi_checkin_options(
    phone: str,
    message: str,
    use_location_link: bool = False,
    appointment_id: str = "",
    agenda_data: dict[str, str] | None = None,
) -> dict[str, Any]:
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
    if DEV_FAKE_ZAPI or is_simulated_phone(phone):
        return fake_zapi_response("send-option-list", phone, {"message": message, "options": options})

    if agenda_data is not None and meta_templates_enabled():
        return send_meta_template(
            phone,
            WHATSAPP_TEMPLATE_CHECKIN,
            schedule_template_parameters(agenda_data, with_address=False),
            [option["id"] for option in options],
        )

    payload = whatsapp_client().send_option_list(
        phone,
        message,
        "Check-in",
        "Responder",
        options,
    )
    logger.info("Resposta envio check-in WhatsApp (%s): %s", whatsapp_provider(), payload)
    return payload


def send_zapi_location_request(phone: str, message: str) -> dict[str, Any]:
    if DEV_FAKE_ZAPI or is_simulated_phone(phone):
        return fake_zapi_response("send-text", phone, {"message": message})

    client = whatsapp_client()
    # A Cloud API tem um botão nativo "Enviar localização"; na Z-API o
    # cooperado envia pelo clipe do WhatsApp.
    if isinstance(client, WhatsAppOfficialClient):
        payload = client.send_location_request(phone, message)
    else:
        payload = client.send_text(phone, message)
    logger.info("Resposta pedido de localização WhatsApp (%s): %s", whatsapp_provider(), payload)
    return payload


def send_zapi_checkout_button(
    phone: str,
    message: str,
    appointment_id: str,
    agenda_data: dict[str, str] | None = None,
) -> dict[str, Any]:
    buttons = [{"id": f"checkout_confirm:{appointment_id}", "label": "Finalizar"}]
    if DEV_FAKE_ZAPI or is_simulated_phone(phone):
        return fake_zapi_response("send-button-list", phone, {"message": message, "buttons": buttons})

    if agenda_data is not None and meta_templates_enabled():
        return send_meta_template(
            phone,
            WHATSAPP_TEMPLATE_CHECKOUT,
            schedule_template_parameters(agenda_data, with_address=False),
            [buttons[0]["id"]],
        )

    payload = whatsapp_client().send_button_list(phone, message, buttons)
    logger.info("Resposta envio check-out WhatsApp (%s): %s", whatsapp_provider(), payload)
    return payload


def send_zapi_late_buttons(phone: str, appointment_id: str = "") -> dict[str, Any]:
    buttons = [
        {"id": f"late_15:{appointment_id}" if appointment_id else "late_15", "label": "15 minutos"},
        {"id": f"late_30:{appointment_id}" if appointment_id else "late_30", "label": "30 minutos"},
        {"id": f"late_60:{appointment_id}" if appointment_id else "late_60", "label": "1 hora"},
    ]
    if DEV_FAKE_ZAPI or is_simulated_phone(phone):
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
    if DEV_FAKE_ZAPI or is_simulated_phone(phone):
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
    event = {
        "received_at": timestamp(),
        "payload": {
            "type": "AutoReply",
            "phone": phone,
            "mode": mode,
            "text": {"message": message},
            "buttons": fake_payload.get("buttons", []),
            "document_url": fake_payload.get("document_url", ""),
            "options": fake_payload.get("options", []),
            "response": response_payload,
        },
    }
    RECEIVED_EVENTS.appendleft(event)
    try:
        from nova_guarda.storage import save_conversation_event

        save_conversation_event(event)
    except Exception:
        logger.exception("Erro ao persistir mensagem fake enviada.")
