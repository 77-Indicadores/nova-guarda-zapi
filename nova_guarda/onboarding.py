import json
from typing import Any

import requests

from nova_guarda.clients import Gestao77Client
from nova_guarda.gestao77_service import fake_gestao77_enabled
from nova_guarda.messages import build_terms_buttons_message, build_terms_message, normalize_phone
from nova_guarda.services import (
    append_fake_sent_message,
    meta_templates_enabled,
    send_terms_document,
    send_terms_template,
    send_zapi_terms_buttons,
    send_zapi_text,
    terms_template_required,
)
from nova_guarda.timezone import br_now
from nova_guarda.storage import (
    get_cooperator,
    get_setting,
    list_cooperators,
    set_settings,
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


TEST_MODE_BLOCK_MESSAGE = "Modo teste ativo: este telefone não está na lista de cooperados de teste."


def test_mode_enabled() -> bool:
    return get_setting("operation_mode").strip().lower() == "test"


def test_cooperators() -> list[dict[str, str]]:
    """Cooperados marcados em Configurações para receber mensagens em modo teste."""
    try:
        items = json.loads(get_setting("test_cooperators") or "[]")
    except ValueError:
        return []
    return [item for item in items if isinstance(item, dict) and item.get("phone")] if isinstance(items, list) else []


def add_test_cooperator(phone: str, name: str = "", partner_id: str = "") -> None:
    phone = normalize_phone(phone)
    items = test_cooperators()
    if not phone or phone in {item["phone"] for item in items}:
        return
    items.append({"phone": phone, "partner_id": partner_id, "name": name})
    set_settings({"test_cooperators": json.dumps(items, ensure_ascii=False)})


def set_test_cooperator_channel(phone: str, channel: str) -> None:
    """Canal do cooperado de teste: "whatsapp" (real) ou "chat" (Chat Dev)."""
    if channel not in {"whatsapp", "chat"}:
        raise ValueError("Canal inválido.")
    phone = normalize_phone(phone)
    items = test_cooperators()
    for item in items:
        if item["phone"] == phone:
            item["channel"] = channel
    set_settings({"test_cooperators": json.dumps(items, ensure_ascii=False)})


def mode_allows_phone(phone: str) -> bool:
    """Trava do modo teste: com ele ligado, a automação só fala com os
    cooperados de teste. Em produção não restringe nada."""
    if not test_mode_enabled():
        return True
    return normalize_phone(phone) in {normalize_phone(item["phone"]) for item in test_cooperators()}


def list_test_candidates() -> list[dict[str, str]]:
    """Cooperados que podem ser marcados como teste: os ativos da 77Gestão
    (ou, com a 77Gestão em modo fake, os que já existem localmente)."""
    if fake_gestao77_enabled():
        return [
            {"phone": row["phone"], "partner_id": row.get("partner_id") or "", "name": row.get("partner_name") or ""}
            for row in list_cooperators()
        ]

    payload = Gestao77Client.from_env().list_partners("cooperado")
    partners = payload.get("partners", [])
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for partner in partners if isinstance(partners, list) else []:
        if not isinstance(partner, dict) or not partner_is_active_cooperator(partner):
            continue
        numbers = partner_phone_numbers(partner)
        phone = normalize_phone(numbers[0]) if numbers else ""
        if not phone or phone in seen:
            continue
        seen.add(phone)
        candidates.append({"phone": phone, "partner_id": str(partner.get("id") or ""), "name": str(partner.get("name") or "")})
    return sorted(candidates, key=lambda item: item["name"].lower())


def is_activation_command(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    return normalized in ACTIVATION_COMMANDS


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


def send_terms_to_phone(phone: str, partner: dict[str, Any] | None = None, source: str = "manual") -> dict[str, Any]:
    """Envia o termo por iniciativa da Nova Guarda (disparo diário ou manual),
    sem o cooperado precisar mandar “ativar”. Nunca reenvia para quem já
    aceitou ou recusou."""
    phone = normalize_phone(phone)
    item: dict[str, Any] = {"phone": phone, "ok": False, "action": "skipped"}
    if not phone:
        item["error"] = "Telefone não informado."
        return item

    if not mode_allows_phone(phone):
        item["error"] = TEST_MODE_BLOCK_MESSAGE
        return item

    existing = get_cooperator(phone)
    status = existing.get("onboarding_status") if existing else "not_started"
    if status == "accepted":
        item["reason"] = "Cooperado já aceitou o termo."
        return item
    if status == "rejected":
        item["reason"] = "Cooperado recusou o termo e está bloqueado."
        return item

    partner = partner or (existing.get("partner_payload") if existing else None) or find_allowed_partner_by_phone(phone)
    if not partner:
        item["error"] = "Telefone não encontrado no cadastro de cooperados da 77Gestão."
        return item
    if str(partner.get("active", "1")) in {"0", "false", "False"}:
        item["error"] = "Cooperado inativo na 77Gestão."
        return item

    send_terms_flow(phone, partner, resend=status == "terms_sent")
    cooperator = upsert_cooperator(phone, "terms_sent", partner, f"termo:{source}")
    save_sync_event("cooperator", phone, f"terms:sent:{source}", True, {"phone": phone}, {"partner_id": partner.get("id")})
    item.update({"ok": True, "action": "terms_sent", "partner_name": cooperator.get("partner_name", "")})
    return item


def dispatch_pending_terms(
    bookings: list[dict[str, Any]],
    source: str = "auto",
    skip_sent_today: bool = False,
) -> list[dict[str, Any]]:
    """Envia o termo para cada cooperado com escala pendente que ainda não aceitou.
    Com skip_sent_today, quem já recebeu o termo hoje não recebe de novo (usado
    quando o disparo diário é repetido só para quem falhou)."""
    today = br_now().date().isoformat()
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for booking in bookings:
        phone = normalize_phone(str(booking.get("phone") or ""))
        if not phone or phone in seen or not mode_allows_phone(phone):
            continue
        seen.add(phone)
        existing = get_cooperator(phone)
        if existing and existing.get("onboarding_status") in {"accepted", "rejected"}:
            continue
        if (
            skip_sent_today
            and existing
            and existing.get("onboarding_status") == "terms_sent"
            and str(existing.get("updated_at") or "")[:10] == today
        ):
            continue
        try:
            item = send_terms_to_phone(phone, partner_from_booking(booking), source)
        except (RuntimeError, requests.RequestException, ValueError) as exc:
            item = {"phone": phone, "ok": False, "action": "skipped", "error": str(exc), "retryable": True}
        item["booking_id"] = str(booking.get("booking_id") or "")
        results.append(item)
    return results


def partner_from_booking(booking: dict[str, Any]) -> dict[str, Any]:
    payload = booking.get("payload") if isinstance(booking.get("payload"), dict) else {}
    partner = payload.get("partner")
    if isinstance(partner, dict) and partner:
        return partner
    return {
        "id": booking.get("partner_id") or "",
        "name": booking.get("partner_name") or booking.get("name") or "",
    }


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
    if meta_templates_enabled():
        # O aceite pode ter vindo pelo template, que não carrega o PDF.
        try:
            send_terms_document(phone, build_terms_message({"client_name": cooperator.get("partner_name") or "Cooperado"}))
        except (RuntimeError, requests.RequestException, ValueError):
            pass
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
    if not mode_allows_phone(phone):
        return False, TEST_MODE_BLOCK_MESSAGE
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
    if terms_template_required(phone):
        # Fora da janela de 24h a Meta só aceita template: ele abre a conversa
        # já com os botões de aceite. O PDF segue quando o cooperado responder.
        send_terms_template(phone, name)
        return

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
