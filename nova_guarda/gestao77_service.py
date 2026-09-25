import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from nova_guarda.clients import Gestao77Client
from nova_guarda.config import GESTAO77_TEST_CUSTOMER_ID, GESTAO77_TEST_SERVICE_ID
from nova_guarda.flows import agenda_status_label
from nova_guarda.flows import checkin_status_label
from nova_guarda.messages import build_agenda_message, build_checkin2_message, build_checkin_message, normalize_phone
from nova_guarda.services import append_fake_sent_message, send_zapi_agenda_buttons, send_zapi_checkin_options
from nova_guarda.services import send_zapi_checkout_button
from nova_guarda.services import whatsapp_provider
from nova_guarda.state import AGENDA_STATE
from nova_guarda.storage import (
    cooperator_has_accepted_terms,
    delete_local_data_for_phone,
    get_appointment,
    get_booking,
    get_cooperator,
    get_latest_appointment_by_phone,
    get_latest_booking_by_phone,
    list_pending_appointment_syncs,
    list_pending_booking_syncs,
    mark_appointment_checkin_sent,
    mark_appointment_checkout_sent,
    mark_appointment_late,
    mark_appointment_no_show,
    mark_appointment_synced,
    mark_booking_synced,
    mark_booking_whatsapp_sent,
    save_sync_event,
    transition_booking_response,
    transition_appointment_checkin,
    transition_appointment_checkout,
    update_booking_local_status,
    upsert_appointment,
    upsert_booking,
    upsert_cooperator,
)
from nova_guarda.timezone import BR_TZ, br_now


PENDING_SCHEDULE_STATUSES = {"awaiting_send", "awaiting_approval"}
BOOKING_FINAL_STATUSES = {"confirmed", "declined"}


def list_pending_partner_bookings(month: int, year: int, body: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    client = Gestao77Client.from_env()
    bookings = client.list_partner_bookings_by_status(
        month=month,
        year=year,
        statuses=PENDING_SCHEDULE_STATUSES,
        body=body,
    )
    return [upsert_booking(enrich_booking(client, booking)) for booking in bookings]


def enrich_booking(client: Gestao77Client, booking: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(booking)
    partner_id = enriched.get("id") or enriched.get("partner_id")
    booking_id = enriched.get("booking_id")

    if partner_id:
        partner_payload = client.get_partner(partner_id)
        partner = partner_payload.get("partner", partner_payload)
        if isinstance(partner, dict):
            enriched["partner"] = partner
            enriched["phones"] = partner.get("phones", [])

    if booking_id:
        appointments_payload = client.list_appointments_by_booking(booking_id)
        appointments = appointments_payload.get("appointments", [])
        if isinstance(appointments, list):
            enriched["appointments"] = appointments
            if appointments and isinstance(appointments[0], dict):
                enriched["first_appointment_id"] = appointments[0].get("id")
            today_appointment = select_today_appointment(appointments)
            if today_appointment:
                enriched["today_appointment_id"] = today_appointment.get("id")

    return enriched


def mark_booking_sent(booking_id: int | str) -> dict[str, Any]:
    return update_booking_schedule_response(booking_id, "sent")


def mark_booking_confirmed(booking_id: int | str) -> dict[str, Any]:
    return update_booking_schedule_response(booking_id, "confirmed")


def mark_booking_declined(booking_id: int | str) -> dict[str, Any]:
    return update_booking_schedule_response(booking_id, "declined")


def mark_appointment_checked_in(appointment_id: int | str, address: str = "") -> dict[str, Any]:
    return update_appointment_status(appointment_id, "checked_in", address)


def mark_appointment_checked_out(appointment_id: int | str, address: str = "") -> dict[str, Any]:
    return update_appointment_status(appointment_id, "checked_out", address)


def _safe_gestao77_sync(func, *args, **kwargs) -> dict[str, Any]:
    """Executa uma sincronização de status de volta para a 77Gestão sem deixar
    uma falha (endpoint/payload ainda não confirmado contra a API real, fora
    do ar, etc.) quebrar o fluxo local do cooperado, que já avançou antes
    dessa chamada. A falha já fica registrada em Registros via save_sync_event
    dentro da própria função de sync (que ainda propaga a exceção lá), então
    aqui só evitamos que ela suba para quem depende do fluxo de WhatsApp."""
    try:
        return func(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - falha de integração não deve travar o fluxo local
        return {"ok": False, "error": str(exc)}


def update_booking_schedule_response(booking_id: int | str, status: str) -> dict[str, Any]:
    request_payload = {"status": status}
    if fake_gestao77_enabled():
        response = {"ok": True, "fake": True, "booking_id": str(booking_id), "status": status}
        mark_booking_synced(booking_id, status)
        save_sync_event("booking", booking_id, f"schedule_response:{status}", True, request_payload, response)
        return response

    try:
        response = Gestao77Client.from_env().update_booking_schedule_response(str(booking_id), status)
        mark_booking_synced(booking_id, status)
        save_sync_event("booking", booking_id, f"schedule_response:{status}", True, request_payload, response)
        return response
    except Exception as exc:
        save_sync_event("booking", booking_id, f"schedule_response:{status}", False, request_payload, error=str(exc))
        raise


def update_appointment_status(appointment_id: int | str, status: str, address: str = "") -> dict[str, Any]:
    request_payload = {"status": status, "address": address}
    if fake_gestao77_enabled():
        response = {"ok": True, "fake": True, "appointment_id": str(appointment_id), "status": status, "address": address}
        mark_appointment_synced(appointment_id, status)
        save_sync_event("appointment", appointment_id, f"status:{status}", True, request_payload, response)
        return response

    try:
        response = Gestao77Client.from_env().update_appointment_status(str(appointment_id), status, address)
        mark_appointment_synced(appointment_id, status)
        save_sync_event("appointment", appointment_id, f"status:{status}", True, request_payload, response)
        return response
    except Exception as exc:
        save_sync_event("appointment", appointment_id, f"status:{status}", False, request_payload, error=str(exc))
        raise


def fake_gestao77_enabled() -> bool:
    try:
        from nova_guarda.storage import get_setting

        if get_setting("gestao77_mode").strip().lower() == "fake":
            return True
    except Exception:
        pass
    return os.getenv("DEV_FAKE_GESTAO77", "false").strip().lower() in {"1", "true", "yes", "sim"}


def send_booking_to_partner(booking_id: int | str, phone: str = "") -> dict[str, Any]:
    booking = get_booking(booking_id)
    if not booking:
        raise RuntimeError(f"Booking {booking_id} não encontrado no storage local.")

    phone = normalize_phone(phone or booking.get("phone", ""))
    if not phone:
        raise RuntimeError("Booking não tem telefone. Informe phone no body ou confirme qual campo vem da 77Gestão.")
    if not cooperator_has_accepted_terms(phone):
        raise PermissionError("Cooperado ainda não aceitou os termos.")

    if booking.get("local_status") in {"sent", "confirmed", "declined"}:
        sync_response = None
        if booking.get("local_status") == "sent" and booking.get("gestao77_status") != "sent":
            sync_response = mark_booking_sent(booking_id)
        return {
            "booking_id": str(booking_id),
            "phone": phone,
            "status": booking.get("local_status"),
            "idempotent": True,
            "gestao77": sync_response,
        }

    agenda_data = agenda_data_from_booking(booking)
    message = build_agenda_message(agenda_data)
    response_payload = send_zapi_agenda_buttons(phone, message)
    append_fake_sent_message(phone, "agenda", message, response_payload)
    sent_booking = mark_booking_whatsapp_sent(booking_id, phone, whatsapp_provider(), response_payload)

    AGENDA_STATE[phone] = {
        "phone": phone,
        "mode": "agenda",
        "status": "sent",
        "status_label": agenda_status_label("sent"),
        "agenda": {**agenda_data, "booking_id": str(booking_id)},
        "booking_id": str(booking_id),
        "last_reply": "",
        "updated_at": sent_booking.get("updated_at", ""),
        "history": [],
    }

    sync_response = mark_booking_sent(booking_id)
    return {
        "booking_id": str(booking_id),
        "phone": phone,
        "status": "sent",
        "message": message,
        "whatsapp": response_payload,
        "gestao77": sync_response,
    }


def send_checkin_to_partner(
    appointment_id: int | str,
    phone: str,
    agenda_data: dict[str, str],
    use_location_link: bool = True,
    booking_id: str | int = "",
) -> dict[str, Any]:
    phone = normalize_phone(phone)
    if not phone:
        raise RuntimeError("Informe phone para enviar o check-in.")
    if not cooperator_has_accepted_terms(phone):
        raise PermissionError("Cooperado ainda não aceitou os termos.")

    appointment = ensure_appointment_for_checkin(appointment_id, phone, booking_id)
    if appointment.get("local_status") in {"checkin_pending", "checked_in", "checkout_pending", "checked_out"}:
        return {
            "appointment_id": str(appointment_id),
            "booking_id": appointment.get("booking_id"),
            "phone": phone,
            "status": appointment.get("local_status"),
            "idempotent": True,
        }

    mode = "checkin2" if use_location_link else "checkin"
    message = build_checkin2_message(agenda_data) if use_location_link else build_checkin_message(agenda_data)
    response_payload = send_zapi_checkin_options(
        phone,
        message,
        use_location_link=use_location_link,
        appointment_id=str(appointment_id),
    )
    append_fake_sent_message(phone, mode, message, response_payload)
    stored_appointment = mark_appointment_checkin_sent(appointment_id, whatsapp_provider(), response_payload)

    AGENDA_STATE[phone] = {
        "phone": phone,
        "mode": mode,
        "status": "checkin_pending",
        "status_label": checkin_status_label("checkin_pending"),
        "agenda": {**agenda_data, "appointment_id": str(appointment_id), "booking_id": stored_appointment.get("booking_id", "")},
        "booking_id": stored_appointment.get("booking_id", ""),
        "appointment_id": str(appointment_id),
        "last_reply": "",
        "updated_at": stored_appointment.get("updated_at", ""),
        "history": [],
    }

    return {
        "appointment_id": str(appointment_id),
        "booking_id": stored_appointment.get("booking_id", ""),
        "phone": phone,
        "status": "checkin_pending",
        "message": message,
        "whatsapp": response_payload,
    }


def sync_booking_reply_for_phone(phone: str, decision: str) -> dict[str, Any] | None:
    phone = normalize_phone(phone)
    state = AGENDA_STATE.get(phone, {})
    booking_id = state.get("booking_id") or state.get("agenda", {}).get("booking_id")
    if not booking_id:
        booking = get_latest_booking_by_phone(phone)
        booking_id = booking.get("booking_id") if booking else None
    if not booking_id:
        return None

    if decision == "confirmed":
        return sync_booking_response(booking_id, "confirmed")
    if decision in {"cancelled", "declined"}:
        return sync_booking_response(booking_id, "declined")
    return None


def sync_booking_response(booking_id: int | str, target_status: str) -> dict[str, Any]:
    changed, booking = transition_booking_response(booking_id, target_status)
    if not changed and booking.get("gestao77_status") == target_status:
        return {
            "booking_id": str(booking_id),
            "status": target_status,
            "idempotent": True,
            "response": None,
        }

    if target_status == "confirmed":
        response = _safe_gestao77_sync(mark_booking_confirmed, booking_id)
    elif target_status == "declined":
        response = _safe_gestao77_sync(mark_booking_declined, booking_id)
    else:
        raise ValueError(f"Status de escala inválido: {target_status}")

    return {
        "booking_id": str(booking_id),
        "status": target_status,
        "idempotent": not changed,
        "response": response,
    }


def sync_checkin_for_phone(phone: str, status: str, address: str = "") -> dict[str, Any] | None:
    phone = normalize_phone(phone)
    appointment_id = appointment_id_from_checkin_status(status)
    if not appointment_id:
        state = AGENDA_STATE.get(phone, {})
        appointment_id = state.get("appointment_id") or state.get("agenda", {}).get("appointment_id")
    if not appointment_id:
        appointment = get_latest_appointment_by_phone(phone)
        appointment_id = appointment.get("appointment_id") if appointment else None
    if not appointment_id:
        raise KeyError("Nenhum appointment de check-in encontrado para este telefone.")

    return sync_appointment_checkin(appointment_id, address=address, event_id=status)


def ensure_appointment_for_checkin(appointment_id: int | str, phone: str, booking_id: str | int = "") -> dict[str, Any]:
    appointment_id = str(appointment_id)
    booking = None
    if booking_id:
        booking = get_booking(booking_id)
        if not booking:
            raise RuntimeError(f"Booking {booking_id} não encontrado.")
        if normalize_phone(booking.get("phone", "")) != phone:
            raise PermissionError("Appointment não pertence ao telefone informado.")
        if str(booking.get("appointment_id") or "") and str(booking.get("appointment_id")) != appointment_id:
            raise ValueError("Appointment não pertence ao booking informado.")
    else:
        bookings = [item for item in [get_latest_booking_by_phone(phone)] if item]
        booking = next((item for item in bookings if str(item.get("appointment_id") or "") == appointment_id), None)
        if not booking:
            booking = bookings[0] if bookings else None

    if not booking:
        raise RuntimeError("Não encontrei booking do cooperado para este appointment.")
    if booking.get("local_status") not in {"sent", "confirmed"}:
        raise ValueError("Check-in só pode ser enviado para escala enviada ou confirmada.")

    payload = booking.get("payload") or {}
    appointments = payload.get("appointments") if isinstance(payload.get("appointments"), list) else []
    matched = next((item for item in appointments if str(item.get("id")) == appointment_id), None)
    if not matched and str(booking.get("appointment_id") or "") == appointment_id:
        matched = {"id": appointment_id}
    if not matched:
        raise ValueError("Appointment não pertence ao booking informado.")

    return upsert_appointment(matched, booking.get("booking_id"), phone, "sent")


def send_checkout_to_partner(appointment_id: int | str, phone: str = "") -> dict[str, Any]:
    appointment = get_appointment(appointment_id)
    if not appointment:
        raise RuntimeError(f"Appointment {appointment_id} não encontrado no storage local.")
    phone = normalize_phone(phone or appointment.get("phone", ""))
    if not cooperator_has_accepted_terms(phone):
        raise PermissionError("Cooperado ainda não aceitou os termos.")
    if appointment.get("local_status") == "checked_out":
        return {"appointment_id": str(appointment_id), "phone": phone, "status": "checked_out", "idempotent": True}
    if appointment.get("local_status") != "checked_in":
        raise ValueError("Check-out só pode ser enviado depois de checked_in.")

    message = "Você já finalizou este atendimento?"
    response_payload = send_zapi_checkout_button(phone, message, str(appointment_id))
    append_fake_sent_message(phone, "checkout", message, response_payload)
    stored_appointment = mark_appointment_checkout_sent(appointment_id, whatsapp_provider(), response_payload)
    AGENDA_STATE[phone] = {
        "phone": phone,
        "mode": "checkout",
        "status": "checkout_pending",
        "status_label": checkin_status_label("checkout_pending"),
        "agenda": {"appointment_id": str(appointment_id), "booking_id": stored_appointment.get("booking_id", "")},
        "booking_id": stored_appointment.get("booking_id", ""),
        "appointment_id": str(appointment_id),
        "last_reply": "",
        "updated_at": stored_appointment.get("updated_at", ""),
        "history": [],
    }
    return {"appointment_id": str(appointment_id), "phone": phone, "status": "checkout_pending", "whatsapp": response_payload}


def sync_appointment_checkin(appointment_id: int | str, address: str = "", event_id: str = "") -> dict[str, Any]:
    changed, appointment = transition_appointment_checkin(appointment_id, event_id)
    if not changed and appointment.get("gestao77_status") in {"checked_in", "checked_out"}:
        return {"appointment_id": str(appointment_id), "status": "checked_in", "idempotent": True, "response": None}
    response = _safe_gestao77_sync(mark_appointment_checked_in, appointment_id, address)
    return {"appointment_id": str(appointment_id), "status": "checked_in", "idempotent": not changed, "response": response}


def sync_appointment_checkout(appointment_id: int | str, address: str = "", event_id: str = "") -> dict[str, Any]:
    changed, appointment = transition_appointment_checkout(appointment_id, event_id)
    if not changed and appointment.get("gestao77_status") == "checked_out":
        return {"appointment_id": str(appointment_id), "status": "checked_out", "idempotent": True, "response": None}
    response = _safe_gestao77_sync(mark_appointment_checked_out, appointment_id, address)
    return {"appointment_id": str(appointment_id), "status": "checked_out", "idempotent": not changed, "response": response}


def record_local_late(phone: str, status: str) -> dict[str, Any]:
    phone = normalize_phone(phone)
    appointment_id = appointment_id_from_checkin_status(status)
    minutes = late_minutes_from_status(status)
    if not appointment_id or minutes == 0:
        raise ValueError("Evento de atraso inválido.")
    appointment = get_appointment(appointment_id)
    if not appointment or normalize_phone(appointment.get("phone", "")) != phone:
        raise PermissionError("Evento de atraso não pertence ao appointment atual.")
    changed, stored = mark_appointment_late(appointment_id, minutes, status)
    return {
        "appointment_id": appointment_id,
        "booking_id": stored.get("booking_id", ""),
        "phone": phone,
        "status": "late_reported",
        "late_minutes": minutes,
        "idempotent": not changed,
        "gestao77_sync": None,
    }


def record_local_no_show(phone: str, status: str) -> dict[str, Any]:
    phone = normalize_phone(phone)
    appointment_id = appointment_id_from_checkin_status(status)
    reason = no_show_reason_from_status(status)
    if not appointment_id or not reason:
        raise ValueError("Evento de não comparecimento inválido.")
    appointment = get_appointment(appointment_id)
    if not appointment or normalize_phone(appointment.get("phone", "")) != phone:
        raise PermissionError("Evento de não comparecimento não pertence ao appointment atual.")
    changed, stored = mark_appointment_no_show(appointment_id, reason, status)
    return {
        "appointment_id": appointment_id,
        "booking_id": stored.get("booking_id", ""),
        "phone": phone,
        "status": "no_show_reported",
        "reason": reason,
        "idempotent": not changed,
        "gestao77_sync": None,
    }


def retry_pending_gestao77_syncs() -> dict[str, Any]:
    results: list[dict[str, Any]] = []

    for booking in list_pending_booking_syncs():
        booking_id = booking["booking_id"]
        status = booking["local_status"]
        try:
            response = update_booking_schedule_response(booking_id, status)
            results.append({"entity_type": "booking", "entity_id": booking_id, "status": status, "ok": True, "response": response})
        except Exception as exc:
            results.append({"entity_type": "booking", "entity_id": booking_id, "status": status, "ok": False, "error": str(exc)})

    for appointment in list_pending_appointment_syncs():
        appointment_id = appointment["appointment_id"]
        status = appointment["local_status"]
        try:
            response = update_appointment_status(appointment_id, status)
            results.append({"entity_type": "appointment", "entity_id": appointment_id, "status": status, "ok": True, "response": response})
        except Exception as exc:
            results.append({"entity_type": "appointment", "entity_id": appointment_id, "status": status, "ok": False, "error": str(exc)})

    return {"ok": all(item["ok"] for item in results), "results": results}


def appointment_id_from_checkin_status(status: str) -> str:
    if ":" not in status:
        return ""
    prefix, value = status.split(":", 1)
    if prefix in {
        "checkin_arrived",
        "checkin2_arrived",
        "checkout_confirm",
        "checkin_late",
        "checkin_not_going",
        "late_15",
        "late_30",
        "late_60",
        "reason_personal",
        "reason_access",
        "reason_client_cancelled",
        "reason_other",
    }:
        return value.strip()
    return ""


def late_minutes_from_status(status: str) -> int:
    prefix = status.split(":", 1)[0]
    return {"late_15": 15, "late_30": 30, "late_60": 60}.get(prefix, 0)


def no_show_reason_from_status(status: str) -> str:
    prefix = status.split(":", 1)[0]
    return {
        "reason_personal": "problema pessoal",
        "reason_access": "sem acesso ao local",
        "reason_client_cancelled": "cliente cancelou",
        "reason_other": "outro motivo",
    }.get(prefix, "")


def agenda_data_from_booking(booking: dict[str, Any]) -> dict[str, str]:
    payload = booking.get("payload") or {}
    appointments = payload.get("appointments") if isinstance(payload.get("appointments"), list) else []
    appointment = select_today_appointment(appointments) or (appointments[0] if appointments and isinstance(appointments[0], dict) else {})
    customer = appointment.get("customer") if isinstance(appointment.get("customer"), dict) else {}
    return {
        "client_name": str(payload.get("name") or booking.get("partner_name") or "Cooperado").strip(),
        "client_address": str(payload.get("address") or payload.get("client_address") or "").strip(),
        "schedule_date": str(payload.get("date") or payload.get("schedule_date") or appointment.get("start_at") or "").strip(),
        "schedule_time": str(payload.get("time") or payload.get("schedule_time") or "").strip(),
        "service": str(payload.get("service") or customer.get("name") or "Escala da cooperativa").strip(),
        "appointment_id": str(payload.get("today_appointment_id") or payload.get("first_appointment_id") or appointment.get("id") or "").strip(),
    }


def select_today_appointment(appointments: list[dict[str, Any]]) -> dict[str, Any] | None:
    today = br_now().date()
    for appointment in appointments:
        if not isinstance(appointment, dict):
            continue
        start_at = str(appointment.get("start_at") or "")
        if not start_at:
            continue
        try:
            starts_at = datetime.fromisoformat(start_at.replace("Z", "+00:00")).astimezone(BR_TZ)
        except ValueError:
            continue
        if starts_at.date() == today:
            return appointment
    return None


def _phone_to_partner_phone_entry(phone: str) -> dict[str, Any]:
    national = phone[2:] if phone.startswith("55") and len(phone) > 10 else phone
    return {"country_code": "+55", "number": int(national), "type": 3}


def seed_test_cooperator(phone: str, name: str = "") -> dict[str, Any]:
    """Cria um cooperado pronto para receber escala, permitindo repetir o teste
    com vários telefones diferentes. Em modo fake, cria só localmente. Fora do
    modo fake, cria de verdade na 77Gestão via POST /partners (payload
    confirmado manualmente contra a API real) e espelha o retorno localmente."""
    phone = normalize_phone(phone)
    if not phone:
        raise ValueError("Informe um telefone para criar o cooperado de teste.")

    partner_name = name.strip() or "Cooperado Teste"

    if fake_gestao77_enabled():
        partner = {
            "id": f"teste-{phone}",
            "name": partner_name,
            "type": "cooperado",
            "active": 1,
        }
        cooperator = upsert_cooperator(phone, "accepted", partner, "teste_assistido:seed")
        save_sync_event(
            "cooperator",
            phone,
            "teste_assistido:seed_cooperator",
            True,
            {"phone": phone, "name": partner_name},
        )
        return cooperator

    payload = {
        "type": "cooperado",
        "person_type": 1,
        "name": partner_name,
        "nrlp": f"TESTE{uuid.uuid4().hex[:10].upper()}",
        "active": 1,
        "email": f"teste.{uuid.uuid4().hex[:8]}@novaguarda.local",
        "phones": [_phone_to_partner_phone_entry(phone)],
        "addresses": [],
        "cooperative_member_settings": {
            "service_id": int(GESTAO77_TEST_SERVICE_ID),
            "cost": 2600,
            "monthly_hours": 198,
        },
    }
    client = Gestao77Client.from_env()
    result = client.create_partner(payload)
    partner = result.get("partner", result)
    cooperator = upsert_cooperator(phone, "accepted", partner, "teste_assistido:seed_real")
    save_sync_event("cooperator", phone, "teste_assistido:seed_cooperator_real", True, payload, result)
    return cooperator


def seed_test_booking_and_send(phone: str, client_name: str = "") -> dict[str, Any]:
    """Cria uma escala + appointment de teste e já dispara a mensagem de agenda
    pelo WhatsApp configurado (real ou fake, conforme DEV_FAKE_ZAPI/provider).
    Em modo fake, monta tudo localmente com IDs sintéticos. Fora do modo fake,
    cria o appointment de verdade na 77Gestão via POST /appointments (que cria/
    associa o booking automaticamente), usando o cliente e serviço de teste já
    confirmados como válidos na API real."""
    phone = normalize_phone(phone)
    if not phone:
        raise ValueError("Informe um telefone para gerar a escala de teste.")
    if not cooperator_has_accepted_terms(phone):
        raise PermissionError("Crie o cooperado de teste (aceito) antes de gerar uma escala.")

    if fake_gestao77_enabled():
        suffix = uuid.uuid4().hex[:8]
        booking_id = f"teste-{suffix}"
        appointment_id = f"teste-apt-{suffix}"
        start_at = br_now().strftime("%Y-%m-%dT%H:%M:%S")

        upsert_booking(
            {
                "id": f"teste-{phone}",
                "name": client_name.strip() or "Cooperado Teste",
                "booking_id": booking_id,
                "schedule_status": "awaiting_approval",
                "today_appointment_id": appointment_id,
                "appointments": [
                    {
                        "id": appointment_id,
                        "start_at": start_at,
                        "customer": {"name": client_name.strip() or "Cliente Teste"},
                    }
                ],
            },
            phone=phone,
        )
        send_result = send_booking_to_partner(booking_id, phone)
        return {"booking_id": booking_id, "appointment_id": appointment_id, "send_result": send_result}

    cooperator = get_cooperator(phone)
    partner_id = str((cooperator or {}).get("partner_id") or "").strip()
    if not partner_id:
        raise RuntimeError("Cooperado sem partner_id da 77Gestão. Recrie o cooperado de teste.")

    start = br_now().astimezone(timezone.utc)
    # Janela curta de propósito: isso é dado de teste assistido, feito para
    # demonstração ao vivo. Um turno real duraria horas, mas aí o check-out
    # automático (via poller) só ficaria elegível bem depois do fim do turno,
    # o que inviabiliza testar o ciclo completo em uma apresentação.
    end = start + timedelta(minutes=5)
    start_at = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_at = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = {
        "partner_id": int(partner_id),
        "customer_id": int(GESTAO77_TEST_CUSTOMER_ID),
        "start_at": start_at,
        "end_at": end_at,
        "notes": f"Teste assistido Nova Guarda - {client_name.strip() or 'Cliente Teste'}",
        "type": "appointment",
    }
    client = Gestao77Client.from_env()
    try:
        result = client.create_appointment(payload)
    except (RuntimeError, requests.RequestException) as exc:
        save_sync_event("cooperator", phone, "teste_assistido:seed_booking_real", False, payload, error=str(exc))
        raise
    appointment = result.get("appointment", result)
    appointment_id = str(appointment.get("id") or "").strip()
    booking_info = appointment.get("booking") or {}
    # A 77Gestão confirmadamente cria o booking (visto via GET logo em seguida),
    # mas a resposta imediata do POST às vezes não traz o objeto "booking"
    # aninhado ainda populado. Aceita também o booking_id no nível raiz do
    # appointment como fallback antes de considerar isso uma falha real.
    booking_id = str(booking_info.get("id") or appointment.get("booking_id") or "").strip()
    if not appointment_id or not booking_id:
        raise RuntimeError("77Gestão não retornou appointment_id/booking_id ao criar o appointment.")

    upsert_booking(
        {
            "id": partner_id,
            "name": client_name.strip() or "Cooperado Teste",
            "booking_id": booking_id,
            "schedule_status": booking_info.get("status") or "awaiting_approval",
            "today_appointment_id": appointment_id,
            "appointments": [
                {
                    "id": appointment_id,
                    "start_at": start_at,
                    "customer": {"name": client_name.strip() or "Cliente Teste"},
                }
            ],
        },
        phone=phone,
    )
    save_sync_event("booking", booking_id, "teste_assistido:seed_booking_real", True, payload, result)

    try:
        send_result = send_booking_to_partner(booking_id, phone)
    except (RuntimeError, requests.RequestException) as exc:
        # A escala já foi criada na 77Gestão e o storage local acima já
        # associou o telefone a ela. Se o WhatsApp já tiver sido enviado (só a
        # sincronização de volta pro 77Gestão falhou), não é uma falha do
        # teste assistido: o cooperado já recebeu a mensagem normalmente.
        booking = get_booking(booking_id)
        if booking and booking.get("local_status") == "sent":
            send_result = {"booking_id": str(booking_id), "phone": phone, "status": "sent", "gestao77_sync_error": str(exc)}
        else:
            raise
    return {"booking_id": booking_id, "appointment_id": appointment_id, "send_result": send_result}


def run_full_assisted_test(phone: str, client_name: str = "", force_new_cooperator: bool = False) -> dict[str, Any]:
    """Ponto de entrada único do modo teste assistido: garante um cooperado
    ativo (reaproveitando o existente por padrão, para não duplicar cooperados
    reais na 77Gestão a cada clique) e cria + envia uma escala de teste nova.

    force_new_cooperator=True sempre cria um cooperado novo, mesmo que já
    exista um aceito para esse telefone — útil para testar explicitamente a
    criação, mas normalmente desnecessário."""
    phone = normalize_phone(phone)
    if not phone:
        raise ValueError("Informe um telefone para rodar o teste completo.")

    existing = None if force_new_cooperator else get_cooperator(phone)
    if not existing or existing.get("onboarding_status") != "accepted":
        seed_test_cooperator(phone, client_name)

    return seed_test_booking_and_send(phone, client_name)


def reset_test_phone(phone: str) -> dict[str, int]:
    """Apaga cooperado/escala/appointment/eventos locais de um telefone, para
    poder repetir o teste assistido do zero (ativar de novo). Nunca escreve na
    77Gestão: uma escala real que já avançou lá não volta a ficar pendente só
    porque o estado local foi apagado — para gerar uma escala nova, use o
    "Rodar teste completo" normalmente depois do reset."""
    phone = normalize_phone(phone)
    if not phone:
        raise ValueError("Informe um telefone para resetar.")

    counts = delete_local_data_for_phone(phone)
    save_sync_event("cooperator", phone, "teste_assistido:reset", True, {"phone": phone}, counts)
    return counts
