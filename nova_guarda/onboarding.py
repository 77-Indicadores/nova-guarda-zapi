import os
from typing import Any

from nova_guarda.clients import Gestao77Client
from nova_guarda.messages import build_terms_buttons_message, build_terms_message, normalize_phone
from nova_guarda.services import append_fake_sent_message, send_terms_document, send_zapi_terms_buttons, send_zapi_text
from nova_guarda.storage import (
    get_cooperator,
    save_sync_event,
    upsert_cooperator,
)


ACTIVATION_COMMANDS = {
    "ativar",
    "iniciar",
    "começar",
    "comecar",
    "começar atendimento",
    "comecar atendimento",
    "quero ativar",
    "quero iniciar",
}

ONBOARDING_STATUSES = {"not_started", "terms_sent", "accepted", "rejected"}


def is_activation_command(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    return normalized in ACTIVATION_COMMANDS


def fake_gestao77_enabled() -> bool:
    return os.getenv("DEV_FAKE_GESTAO77", "false").strip().lower() in {"1", "true", "yes", "sim"}


def handle_activation_request(phone: str, text: str) -> dict[str, Any]:
    phone = normalize_phone(phone)
    existing = get_cooperator(phone)

    if existing and existing.get("onboarding_status") == "accepted":
        reply = "Seu cadastro já está ativo para receber comunicações operacionais da Nova Guarda."
        response = send_zapi_text(phone, reply)
        return {"status": "accepted", "reply": reply, "response": response, "cooperator": existing}

    if existing and existing.get("onboarding_status") == "rejected":
        reply = (
            "Existe uma recusa do termo de uso e consentimento registrada para este número. "
            "Por enquanto, o fluxo permanece bloqueado. Fale com a equipe da Nova Guarda."
        )
        response = send_zapi_text(phone, reply)
        return {"status": "rejected", "reply": reply, "response": response, "cooperator": existing}

    if existing and existing.get("onboarding_status") == "terms_sent":
        send_terms_flow(phone, existing.get("partner_payload", {}), resend=True)
        cooperator = upsert_cooperator(phone, "terms_sent", existing.get("partner_payload", {}), text)
        return {"status": "terms_sent", "reply": "Termo de uso e consentimento reenviado para aceite.", "cooperator": cooperator}

    partner = find_allowed_partner_by_phone(phone)
    if not partner:
        cooperator = upsert_cooperator(phone, "not_started", {}, text)
        reply = "Não encontrei seu número no cadastro da Nova Guarda. Fale com a equipe para atualizar seu WhatsApp."
        response = send_zapi_text(phone, reply)
        save_sync_event("cooperator", phone, "activation:not_found", True, {"phone": phone}, {"reply": reply})
        return {"status": "not_started", "reply": reply, "response": response, "cooperator": cooperator}

    if not partner_is_active_cooperator(partner):
        cooperator = upsert_cooperator(phone, "not_started", partner, text)
        reply = "Seu cadastro foi encontrado, mas não está ativo para receber comunicações por este fluxo. Fale com a equipe da Nova Guarda."
        response = send_zapi_text(phone, reply)
        save_sync_event("cooperator", phone, "activation:inactive", True, {"phone": phone}, {"partner_id": partner.get("id")})
        return {"status": "not_started", "reply": reply, "response": response, "cooperator": cooperator}

    send_terms_flow(phone, partner)
    cooperator = upsert_cooperator(phone, "terms_sent", partner, text)
    save_sync_event("cooperator", phone, "activation:terms_sent", True, {"phone": phone}, {"partner_id": partner.get("id")})
    return {"status": "terms_sent", "reply": "Termo de uso e consentimento enviado para aceite.", "cooperator": cooperator}


def accept_terms(phone: str, text: str) -> dict[str, Any]:
    phone = normalize_phone(phone)
    existing = get_cooperator(phone)
    if not existing:
        reply = "Antes de aceitar o termo, envie “ativar” para validarmos seu cadastro na Nova Guarda."
        response = send_zapi_text(phone, reply)
        return {"status": "not_started", "reply": reply, "response": response}

    if existing.get("onboarding_status") == "rejected":
        reply = "O termo já foi recusado neste número e o fluxo está bloqueado. Fale com a equipe da Nova Guarda."
        response = send_zapi_text(phone, reply)
        return {"status": "rejected", "reply": reply, "response": response, "cooperator": existing}

    cooperator = upsert_cooperator(phone, "accepted", existing.get("partner_payload", {}), text)
    reply = "Aceite registrado com sucesso. Seu cadastro está ativo para receber comunicações operacionais da Nova Guarda."
    response = send_zapi_text(phone, reply)
    save_sync_event("cooperator", phone, "terms:accepted", True, {"phone": phone}, {"partner_id": cooperator.get("partner_id")})
    return {"status": "accepted", "reply": reply, "response": response, "cooperator": cooperator}


def reject_terms(phone: str, text: str) -> dict[str, Any]:
    phone = normalize_phone(phone)
    existing = get_cooperator(phone)
    partner = existing.get("partner_payload", {}) if existing else {}
    cooperator = upsert_cooperator(phone, "rejected", partner, text)
    reply = "Recusa registrada. O fluxo de comunicações operacionais da Nova Guarda ficará bloqueado para este número."
    response = send_zapi_text(phone, reply)
    save_sync_event("cooperator", phone, "terms:rejected", True, {"phone": phone}, {"partner_id": cooperator.get("partner_id")})
    return {"status": "rejected", "reply": reply, "response": response, "cooperator": cooperator}


def ensure_operational_access(phone: str) -> tuple[bool, str]:
    cooperator = get_cooperator(normalize_phone(phone))
    if not cooperator:
        return False, "Envie “ativar” primeiro para validar seu cadastro e aceitar o termo de uso e consentimento."
    status = cooperator.get("onboarding_status")
    if status == "accepted":
        return True, ""
    if status == "terms_sent":
        return False, "Seu termo ainda está pendente. Aceite o termo para receber escala e check-in."
    if status == "rejected":
        return False, "Este número recusou o termo e está bloqueado para escala e check-in."
    return False, "Envie “ativar” primeiro para liberar o fluxo."


def find_allowed_partner_by_phone(phone: str) -> dict[str, Any] | None:
    phone = normalize_phone(phone)
    if fake_gestao77_enabled():
        return fake_partner_for_phone(phone)

    payload = Gestao77Client.from_env().list_partners("cooperado")
    partners = payload.get("partners", [])
    if not isinstance(partners, list):
        return None

    for partner in partners:
        if isinstance(partner, dict) and partner_phone_matches(partner, phone):
            return partner
    return None


def partner_is_active_cooperator(partner: dict[str, Any]) -> bool:
    return str(partner.get("type", "")).lower() == "cooperado" and str(partner.get("active", "")) in {"1", "true", "True"}


def partner_phone_matches(partner: dict[str, Any], phone: str) -> bool:
    expected = normalize_phone(phone)
    for candidate in partner_phone_numbers(partner):
        if normalize_phone(candidate) == expected:
            return True
    return False


def partner_phone_numbers(partner: dict[str, Any]) -> list[str]:
    numbers: list[str] = []
    phones = partner.get("phones", [])
    if not isinstance(phones, list):
        return numbers

    for item in phones:
        if not isinstance(item, dict) or not item.get("number"):
            continue
        country_code = "".join(char for char in str(item.get("country_code", "")) if char.isdigit())
        number = "".join(char for char in str(item.get("number", "")) if char.isdigit())
        numbers.append(f"{country_code}{number}" if country_code and not number.startswith(country_code) else number)
    return numbers


def fake_partner_for_phone(phone: str) -> dict[str, Any] | None:
    if phone.endswith("0000"):
        return None
    if phone.endswith("1111"):
        return {
            "id": "fake-inactive",
            "name": "Cooperado Inativo",
            "type": "cooperado",
            "active": 0,
            "phones": [{"country_code": "+55", "number": phone[2:]}],
        }
    return {
        "id": "fake-partner",
        "name": "Cooperado Teste",
        "type": "cooperado",
        "active": 1,
        "phones": [{"country_code": "+55", "number": phone[2:]}],
    }


def send_terms_flow(phone: str, partner: dict[str, Any], resend: bool = False) -> None:
    name = str(partner.get("name") or "Cooperado").strip()
    intro = (
        "Seu termo de uso e consentimento ainda está pendente. Reenviei as opções de aceite."
        if resend
        else "Antes de continuar, salve este contato da Nova Guarda para receber comunicados de escala, check-in e orientações operacionais."
    )
    intro_response = send_zapi_text(phone, intro)
    append_fake_sent_message(phone, "onboarding", intro, intro_response)

    message = build_terms_message({"client_name": name})
    document_response = send_terms_document(phone, message)
    append_fake_sent_message(phone, "terms", message, document_response)

    buttons_response = send_zapi_terms_buttons(phone)
    append_fake_sent_message(phone, "terms_buttons", build_terms_buttons_message(), buttons_response)
