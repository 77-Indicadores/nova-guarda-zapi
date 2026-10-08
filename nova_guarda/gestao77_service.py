import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from nova_guarda.alerts import raise_alert
from nova_guarda.clients import Gestao77Client
from nova_guarda.config import GESTAO77_TEST_CUSTOMER_ID, GESTAO77_TEST_SERVICE_ID
from nova_guarda.flows import agenda_status_label
from nova_guarda.flows import checkin_status_label
from nova_guarda.messages import (
    build_agenda_message,
    build_checkin2_message,
    build_checkin_message,
    build_checkout_message,
    build_schedule_message,
    normalize_phone,
    schedule_details,
    schedule_period_label,
)
from nova_guarda.services import append_fake_sent_message, send_zapi_agenda_buttons, send_zapi_checkin_options
from nova_guarda.schedule_pdf import build_schedule_pdf
from nova_guarda.services import send_zapi_schedule
from nova_guarda.services import send_zapi_checkout_button, send_zapi_location_request
from nova_guarda.services import whatsapp_provider
from nova_guarda.state import AGENDA_STATE
from nova_guarda.storage import (
    cooperator_has_accepted_terms,
    delete_local_data_for_phone,
    get_appointment,
    get_appointment_awaiting_location,
    get_booking,
    get_cooperator,
    get_latest_appointment_by_phone,
    get_latest_booking_by_phone,
    list_blocked_syncs,
    list_pending_appointment_syncs,
    list_pending_booking_syncs,
    mark_appointment_checkin_sent,
    mark_appointment_checkout_location_pending,
    mark_appointment_checkout_sent,
    mark_appointment_late,
    mark_appointment_location_pending,
    mark_appointment_no_show,
    save_appointment_checkin_location,
    save_appointment_checkout_location,
    mark_appointment_synced,
    mark_booking_synced,
    mark_booking_whatsapp_sent,
    mark_sync_blocked,
    save_sync_event,
    transition_booking_response,
    transition_appointment_checkin,
    transition_appointment_checkout,
    update_booking_appointments,
    update_booking_local_status,
    upsert_appointment,
    upsert_booking,
    upsert_cooperator,
)
from nova_guarda.timezone import BR_TZ, br_now


# Só "aguardando envio" é a deixa para o bot. "awaiting_approval" é escala
# ainda não liberada pela equipe na 77Gestão (e é o status padrão de quem nem
# tem escala no mês): a API recusa sent/confirmed/declined a partir dele.
PENDING_SCHEDULE_STATUSES = {"awaiting_send"}
BOOKING_SYNCED_STATUSES = {"sent", "confirmed", "declined"}
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
        client = Gestao77Client.from_env()
        booking = get_booking(booking_id) or {}
        if status != "sent" and booking.get("gestao77_status") not in BOOKING_SYNCED_STATUSES:
            # A 77Gestão só aceita confirmed/declined depois de sent. Se o
            # "sent" não chegou lá (falha anterior), reenvia na ordem.
            sent_response = _post_booking_status(client, booking_id, "sent")
            mark_booking_synced(booking_id, "sent")
            save_sync_event("booking", booking_id, "schedule_response:sent", True, {"status": "sent"}, sent_response)
        response = _post_booking_status(client, booking_id, status)
        mark_booking_synced(booking_id, status)
        save_sync_event("booking", booking_id, f"schedule_response:{status}", True, request_payload, response)
        return response
    except Exception as exc:
        save_sync_event("booking", booking_id, f"schedule_response:{status}", False, request_payload, error=str(exc))
        block_rejected_sync("booking", booking_id, status, exc)
        raise


BOOKING_STATUS_ORDER = {"awaiting_approval": 0, "awaiting_send": 1, "sent": 2, "confirmed": 3, "declined": 3}


def _is_rejected_transition(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    return isinstance(exc, requests.HTTPError) and response is not None and response.status_code == 422


def _post_booking_status(client: Gestao77Client, booking_id: int | str, status: str) -> dict[str, Any]:
    """Envia o status da escala. Se a 77Gestão recusar a transição, confere o
    status que está lá: quando ela já está nesse status (ou adiante, no caso
    de "sent"), a recusa é só um envio repetido e conta como sincronizado."""
    try:
        return client.update_booking_schedule_response(str(booking_id), status)
    except requests.HTTPError as exc:
        if not _is_rejected_transition(exc):
            raise
        remote = client.get_booking_status(booking_id)
        already_there = remote == status or (
            status == "sent" and BOOKING_STATUS_ORDER.get(remote, -1) > BOOKING_STATUS_ORDER["sent"]
        )
        if not already_there:
            raise
        return {"status": "success", "idempotent": True, "remote_status": remote}


def update_appointment_status(appointment_id: int | str, status: str, address: str = "") -> dict[str, Any]:
    request_payload = {"status": status, "address": address}
    if fake_gestao77_enabled():
        response = {"ok": True, "fake": True, "appointment_id": str(appointment_id), "status": status, "address": address}
        mark_appointment_synced(appointment_id, status)
        save_sync_event("appointment", appointment_id, f"status:{status}", True, request_payload, response)
        return response

    try:
        client = Gestao77Client.from_env()
        try:
            response = client.update_appointment_status(str(appointment_id), status, address)
        except requests.HTTPError as exc:
            # Recusa de um envio repetido: se a 77Gestão já tem esse check-in/out, está sincronizado.
            if not _is_rejected_transition(exc):
                raise
            remote = client.get_appointment(appointment_id)
            if not remote.get("checked_in_at" if status == "checked_in" else "checked_out_at"):
                raise
            response = {"status": "success", "idempotent": True}
        mark_appointment_synced(appointment_id, status)
        save_sync_event("appointment", appointment_id, f"status:{status}", True, request_payload, response)
        return response
    except Exception as exc:
        save_sync_event("appointment", appointment_id, f"status:{status}", False, request_payload, error=str(exc))
        block_rejected_sync("appointment", appointment_id, status, exc)
        raise


def block_rejected_sync(entity_type: str, entity_id: int | str, status: str, exc: Exception) -> None:
    """Erro 422 é a 77Gestão dizendo que a transição não é permitida: repetir
    não resolve. Tira da fila de retry e avisa a equipe, em vez de deixar o
    local e a 77Gestão divergindo em silêncio."""
    if not _is_rejected_transition(exc):
        return
    mark_sync_blocked(entity_type, entity_id, status)
    label = "Escala" if entity_type == "booking" else "Atendimento"
    raise_alert(
        "sync_rejected",
        entity_type,
        entity_id,
        "",
        f"{label} {entity_id}: a 77Gestão recusou o status {status}. O registro local e a 77Gestão estão "
        f"diferentes e precisam de ajuste manual lá. Detalhe: {str(exc)[-200:]}",
    )


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

    agenda_data = schedule_data_from_booking(booking)
    message, response_payload = send_schedule_message(booking, phone, agenda_data)
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
    # Qualquer status além de "sent" significa que o check-in já foi enviado
    # (aguardando localização, atraso, não vou...): não reenvia a cada ciclo.
    if appointment.get("local_status") != "sent":
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
        agenda_data=agenda_data,
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


def sync_booking_reply_for_phone(phone: str, decision: str, booking_id: str = "") -> dict[str, Any] | None:
    phone = normalize_phone(phone)
    if booking_id:
        booking = get_booking(booking_id)
        if not booking or normalize_phone(booking.get("phone", "")) != phone:
            raise PermissionError("Resposta de escala não pertence a este telefone.")
    state = AGENDA_STATE.get(phone, {})
    booking_id = booking_id or state.get("booking_id") or state.get("agenda", {}).get("booking_id")
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


def request_checkin_location(phone: str, status: str) -> dict[str, Any]:
    """Registra o “cheguei” e deixa o appointment aguardando a localização do
    WhatsApp, que é obrigatória para concluir o check-in."""
    phone = normalize_phone(phone)
    appointment_id = appointment_id_from_checkin_status(status)
    if not appointment_id:
        appointment = get_latest_appointment_by_phone(phone)
        appointment_id = appointment.get("appointment_id") if appointment else ""
    appointment = get_appointment(appointment_id) if appointment_id else None
    if not appointment or normalize_phone(appointment.get("phone", "")) != phone:
        raise PermissionError("Check-in não pertence a um appointment deste telefone.")
    changed, stored = mark_appointment_location_pending(appointment_id)
    return {
        "appointment_id": str(appointment_id),
        "booking_id": stored.get("booking_id", ""),
        "phone": phone,
        "status": "location_pending",
        "idempotent": not changed,
    }


def sync_checkin_location_for_phone(phone: str, latitude: Any, longitude: Any, address: str) -> dict[str, Any] | None:
    """Conclui o check-in do appointment que aguarda localização. Retorna None
    quando o telefone não tem check-in aguardando localização."""
    phone = normalize_phone(phone)
    appointment = get_appointment_awaiting_location(phone)
    if not appointment:
        return None
    appointment_id = appointment["appointment_id"]
    if appointment.get("local_status") == "checkout_location_pending":
        save_appointment_checkout_location(appointment_id, latitude, longitude, address)
        return sync_appointment_checkout(appointment_id, address=address, event_id="location")
    save_appointment_checkin_location(appointment_id, latitude, longitude, address)
    return sync_appointment_checkin(appointment_id, address=address, event_id="location")


def request_checkout_location(phone: str, status: str) -> dict[str, Any]:
    """Registra o “finalizei” e deixa o appointment aguardando a localização do
    WhatsApp, que é obrigatória para concluir o check-out."""
    phone = normalize_phone(phone)
    appointment_id = appointment_id_from_checkin_status(status)
    appointment = get_appointment(appointment_id) if appointment_id else None
    if not appointment or normalize_phone(appointment.get("phone", "")) != phone:
        raise PermissionError("Check-out não pertence a um appointment deste telefone.")
    changed, stored = mark_appointment_checkout_location_pending(appointment_id)
    return {
        "appointment_id": str(appointment_id),
        "booking_id": stored.get("booking_id", ""),
        "phone": phone,
        "status": "checkout_location_pending",
        "idempotent": not changed,
    }


APPOINTMENTS_REFRESH_MINUTES = 60
TEST_SHIFT_MINUTES = 5
# A escala de teste repete o turno em dias seguidos: uma escala é feita de vários dias.
TEST_SCHEDULE_DAYS = 3
TEST_SHIFT_MAX_ATTEMPTS = 6


def refresh_booking_appointments(booking: dict[str, Any]) -> dict[str, Any]:
    """Reconcilia os appointments da escala com a 77Gestão (no máximo uma vez
    por hora por escala): atendimento novo passa a receber check-in e
    atendimento removido/cancelado lá deixa de receber. Se a consulta falhar,
    segue com a lista local."""
    if fake_gestao77_enabled():
        return booking
    payload = booking.get("payload") or {}
    now = br_now()
    try:
        refreshed_at = datetime.fromisoformat(str(payload.get("appointments_refreshed_at") or ""))
    except ValueError:
        refreshed_at = None
    if refreshed_at and now - refreshed_at < timedelta(minutes=APPOINTMENTS_REFRESH_MINUTES):
        return booking
    booking_id = booking.get("booking_id")
    try:
        result = Gestao77Client.from_env().list_appointments_by_booking(booking_id)
    except (RuntimeError, requests.RequestException, ValueError):
        return booking
    appointments = result.get("appointments") if isinstance(result, dict) else None
    if not isinstance(appointments, list):
        return booking
    update_booking_appointments(booking_id, appointments, now.isoformat(timespec="seconds"))
    return get_booking(booking_id) or booking


def send_followup_reminder(entity_type: str, entity: dict[str, Any]) -> dict[str, Any]:
    """Lembrete único para um estado que está parado esperando o cooperado."""
    phone = normalize_phone(entity.get("phone", ""))
    status = entity.get("local_status")
    if not phone:
        raise ValueError("Sem telefone para enviar lembrete.")
    if entity_type == "booking":
        _, response = send_schedule_message(
            entity, phone, schedule_data_from_booking(entity), prefix="Lembrete: sua escala ainda aguarda resposta.\n\n"
        )
        return response

    appointment_id = str(entity.get("appointment_id"))
    agenda_data = agenda_data_for_appointment(entity)
    if status in {"checkin_pending", "late_reported"}:
        message = f"Lembrete: você já chegou ao local do atendimento?\n\n{schedule_details(agenda_data)}\n\nResponda abaixo."
        response = send_zapi_checkin_options(
            phone, message, use_location_link=True, appointment_id=appointment_id, agenda_data=agenda_data
        )
        append_fake_sent_message(phone, "checkin2", message, response)
    elif status in {"location_pending", "checkout_location_pending"}:
        step = "check-out" if status == "checkout_location_pending" else "check-in"
        message = f"Lembrete: envie sua localização atual por aqui para concluir o {step}."
        response = send_zapi_location_request(phone, message)
        append_fake_sent_message(phone, "checkin2", message, response)
    elif status == "checkout_pending":
        message = f"Lembrete: você já finalizou este atendimento?\n\n{schedule_details(agenda_data)}"
        response = send_zapi_checkout_button(phone, message, appointment_id, agenda_data)
        append_fake_sent_message(phone, "checkout", message, response)
    else:
        raise ValueError(f"Status sem lembrete: {status}")
    return response


def ensure_appointment_for_checkin(appointment_id: int | str, phone: str, booking_id: str | int = "") -> dict[str, Any]:
    appointment_id = str(appointment_id)
    booking = None
    if booking_id:
        booking = get_booking(booking_id)
        if not booking:
            raise RuntimeError(f"Booking {booking_id} não encontrado.")
        if normalize_phone(booking.get("phone", "")) != phone:
            raise PermissionError("Appointment não pertence ao telefone informado.")
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

    agenda_data = agenda_data_for_appointment(appointment)
    message = build_checkout_message(agenda_data)
    response_payload = send_zapi_checkout_button(phone, message, str(appointment_id), agenda_data)
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


BLOCKED_SYNC_RECHECK_MINUTES = 15


def retry_pending_gestao77_syncs() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    # Recusas da 77Gestão saem da fila normal, mas são reconferidas de tempos em
    # tempos: se a situação lá mudou (ou já estava certa), a divergência se desfaz sozinha.
    recheck_before = (br_now() - timedelta(minutes=BLOCKED_SYNC_RECHECK_MINUTES)).isoformat(timespec="seconds")

    for booking in list_pending_booking_syncs() + list_blocked_syncs("booking", recheck_before):
        booking_id = booking["booking_id"]
        status = booking["local_status"]
        try:
            response = update_booking_schedule_response(booking_id, status)
            results.append({"entity_type": "booking", "entity_id": booking_id, "status": status, "ok": True, "response": response})
        except Exception as exc:
            results.append({"entity_type": "booking", "entity_id": booking_id, "status": status, "ok": False, "error": str(exc)})

    for appointment in list_pending_appointment_syncs() + list_blocked_syncs("appointment", recheck_before):
        appointment_id = appointment["appointment_id"]
        status = appointment["local_status"]
        try:
            address = str(appointment.get("checkin_address" if status == "checked_in" else "checkout_address") or "")
            response = update_appointment_status(appointment_id, status, address)
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


def agenda_data_from_booking(booking: dict[str, Any], appointment: dict[str, Any] | None = None) -> dict[str, str]:
    payload = booking.get("payload") or {}
    appointments = payload.get("appointments") if isinstance(payload.get("appointments"), list) else []
    appointment = appointment or select_today_appointment(appointments) or (
        appointments[0] if appointments and isinstance(appointments[0], dict) else {}
    )
    customer = appointment.get("customer") if isinstance(appointment.get("customer"), dict) else {}
    return {
        "client_name": str(payload.get("name") or booking.get("partner_name") or "Cooperado").strip(),
        "client_address": str(payload.get("address") or payload.get("client_address") or "").strip(),
        "schedule_date": str(payload.get("date") or payload.get("schedule_date") or appointment.get("start_at") or "").strip(),
        "schedule_time": str(payload.get("time") or payload.get("schedule_time") or "").strip(),
        "schedule_end": str(appointment.get("end_at") or "").strip(),
        "service": str(payload.get("service") or customer.get("name") or "Escala da cooperativa").strip(),
        "appointment_id": str(appointment.get("id") or payload.get("today_appointment_id") or payload.get("first_appointment_id") or "").strip(),
    }


WEEKDAY_NAMES = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]


def schedule_data_from_booking(booking: dict[str, Any]) -> dict[str, Any]:
    """Resumo da escala de trabalho: o conjunto de dias deste booking (pode ser
    uma semana, um mês ou cruzar meses), e não um atendimento só."""
    data: dict[str, Any] = dict(agenda_data_from_booking(booking))
    payload = booking.get("payload") or {}
    appointments = payload.get("appointments") if isinstance(payload.get("appointments"), list) else []
    shifts: list[tuple[datetime, datetime | None, str]] = []
    for appointment in appointments:
        if not isinstance(appointment, dict):
            continue
        if str(appointment.get("status") or "").strip().lower() in {"cancelled", "canceled", "cancelado", "cancelada"}:
            continue
        starts_at = _parse_br(appointment.get("start_at"))
        if not starts_at:
            continue
        customer = appointment.get("customer") if isinstance(appointment.get("customer"), dict) else {}
        shifts.append((starts_at, _parse_br(appointment.get("end_at")), str(customer.get("name") or "").strip()))
    shifts.sort(key=lambda item: item[0])

    days = sorted({shift[0].date() for shift in shifts})
    first, last = (days[0], days[-1]) if days else (br_now().date(), br_now().date())
    if (first.month, first.year) == (last.month, last.year):
        period = schedule_period_label(first.month, first.year)
    else:
        period = f"{first:%d/%m} a {last:%d/%m/%Y}"
    data.update(
        {
            "booking_id": str(booking.get("booking_id") or ""),
            "schedule_period": period,
            "schedule_days": len(days),
            "schedule_first": first.strftime("%d/%m") if days else "",
            "schedule_last": last.strftime("%d/%m") if days else "",
            "schedule_shifts": [
                {
                    "date": f"{start:%d/%m/%Y}",
                    "weekday": WEEKDAY_NAMES[start.weekday()],
                    "time": f"{start:%H:%M}" + (f" às {end:%H:%M}" if end else ""),
                    "customer": customer,
                }
                for start, end, customer in shifts
            ],
        }
    )
    return data


def _parse_br(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(BR_TZ) if parsed.tzinfo else parsed.replace(tzinfo=BR_TZ)


def fetch_schedule_pdf(booking: dict[str, Any], schedule_data: dict[str, Any] | None = None) -> bytes | None:
    """PDF da escala, gerado aqui só com os dias deste booking. Sem dias
    interpretáveis não há PDF (a mensagem segue sem anexo)."""
    schedule_data = schedule_data or schedule_data_from_booking(booking)
    if not schedule_data.get("schedule_shifts"):
        return None
    return build_schedule_pdf(schedule_data)


def send_schedule_message(
    booking: dict[str, Any],
    phone: str,
    schedule_data: dict[str, Any],
    prefix: str = "",
) -> tuple[str, dict[str, Any]]:
    pdf_content = fetch_schedule_pdf(booking, schedule_data)
    message = prefix + build_schedule_message(schedule_data, has_pdf=bool(pdf_content))
    file_name = f"Escala Nova Guarda - {schedule_data['schedule_period'].replace('/', ' ')}.pdf"
    response = send_zapi_schedule(phone, message, str(booking.get("booking_id")), schedule_data, pdf_content, file_name)
    append_fake_sent_message(phone, "agenda", message, response)
    return message, response


def agenda_data_for_appointment(appointment: dict[str, Any]) -> dict[str, str]:
    """Dados da escala para mensagens de um atendimento já registrado localmente."""
    booking = get_booking(appointment.get("booking_id") or "") or {}
    details = appointment.get("payload") if isinstance(appointment.get("payload"), dict) else {}
    details = {**details, "id": appointment.get("appointment_id")}
    return agenda_data_from_booking(booking, details)


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


def seed_test_booking_and_send(phone: str, client_name: str = "", customer_name: str = "") -> dict[str, Any]:
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
        first_start = br_now()
        start_at = first_start.strftime("%Y-%m-%dT%H:%M:%S")
        extra_days = [
            {
                "id": f"{appointment_id}-d{offset + 1}",
                "start_at": (first_start + timedelta(days=offset)).strftime("%Y-%m-%dT%H:%M:%S"),
                "end_at": (first_start + timedelta(days=offset, minutes=TEST_SHIFT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S"),
                "customer": {"name": customer_name.strip() or client_name.strip() or "Cliente Teste"},
            }
            for offset in range(1, TEST_SCHEDULE_DAYS)
        ]

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
                        "end_at": (first_start + timedelta(minutes=TEST_SHIFT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S"),
                        "customer": {"name": customer_name.strip() or client_name.strip() or "Cliente Teste"},
                    },
                    *extra_days,
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

    client = Gestao77Client.from_env()
    now = br_now().astimezone(timezone.utc)
    # Janela curta de propósito: isso é dado de teste assistido, feito para
    # demonstração ao vivo. Um turno real duraria horas, mas aí o check-out
    # automático (via poller) só ficaria elegível bem depois do fim do turno,
    # o que inviabiliza testar o ciclo completo em uma apresentação.
    duration = timedelta(minutes=TEST_SHIFT_MINUTES)
    result: dict[str, Any] = {}
    for attempt in range(TEST_SHIFT_MAX_ATTEMPTS):
        # A 77Gestão recusa atendimento em horário que o cooperado já tem outro
        # (mesmo de escala recusada). O teste anterior dura poucos minutos,
        # então o novo é encaixado logo depois dele.
        start = now + attempt * (duration + timedelta(seconds=10))
        start_at = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_at = (start + duration).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = {
            "partner_id": int(partner_id),
            "customer_id": int(GESTAO77_TEST_CUSTOMER_ID),
            "start_at": start_at,
            "end_at": end_at,
            "notes": f"Teste assistido Nova Guarda - {client_name.strip() or 'Cliente Teste'}",
            "type": "appointment",
            # Mesma recorrência da tela da 77Gestão: os dias seguintes entram
            # no mesmo booking (criar um a um geraria um booking por dia).
            "repeat": True,
            "repeat_until": (start.astimezone(BR_TZ).date() + timedelta(days=TEST_SCHEDULE_DAYS - 1)).isoformat(),
            "repeat_pattern": "weekdays",
            "repeat_days_of_week": [0, 1, 2, 3, 4, 5, 6],
        }
        try:
            result = client.create_appointment(payload)
            break
        except (RuntimeError, requests.RequestException) as exc:
            if "agendamento neste hor" in str(exc) and attempt < TEST_SHIFT_MAX_ATTEMPTS - 1:
                continue
            save_sync_event("cooperator", phone, "teste_assistido:seed_booking_real", False, payload, error=str(exc))
            raise
    appointment = result.get("appointment", result)
    appointment_id = str(appointment.get("id") or "").strip()
    booking_info = appointment.get("booking") or {}
    created_booking_id = str(booking_info.get("id") or appointment.get("booking_id") or "").strip()
    if created_booking_id:
        # A escala nasce "aguardando aprovação"; sem liberar para envio a
        # 77Gestão recusa sent/confirmed/declined e o teste nunca sincroniza.
        release_request = {"status": "awaiting_send"}
        try:
            released = client.release_booking_for_send(created_booking_id)
            save_sync_event("booking", created_booking_id, "teste_assistido:release_for_send", True, release_request, released)
        except (RuntimeError, requests.RequestException) as exc:
            save_sync_event(
                "booking", created_booking_id, "teste_assistido:release_for_send", False, release_request, error=str(exc)
            )
    # A 77Gestão confirmadamente cria o booking (visto via GET logo em seguida),
    # mas a resposta imediata do POST às vezes não traz o objeto "booking"
    # aninhado ainda populado. Aceita também o booking_id no nível raiz do
    # appointment como fallback antes de considerar isso uma falha real.
    booking_id = str(booking_info.get("id") or appointment.get("booking_id") or "").strip()
    if not appointment_id or not booking_id:
        raise RuntimeError("77Gestão não retornou appointment_id/booking_id ao criar o appointment.")

    test_customer = {"name": customer_name.strip() or client_name.strip() or "Cliente Teste"}
    schedule_appointments = [{"id": appointment_id, "start_at": start_at, "end_at": end_at, "customer": test_customer}]
    try:
        # Lista real do booking: traz os dias criados pela recorrência e o cliente de verdade.
        listed = client.list_appointments_by_booking(booking_id)
        listed_appointments = listed.get("appointments") if isinstance(listed, dict) else None
        if isinstance(listed_appointments, list) and listed_appointments:
            schedule_appointments = listed_appointments
    except (RuntimeError, requests.RequestException):
        pass

    upsert_booking(
        {
            "id": partner_id,
            "name": client_name.strip() or "Cooperado Teste",
            "booking_id": booking_id,
            "schedule_status": booking_info.get("status") or "awaiting_approval",
            "today_appointment_id": appointment_id,
            "appointments": schedule_appointments,
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


def run_test_for_existing_cooperator(phone: str) -> dict[str, Any]:
    """Teste assistido para um cooperado que já existe na 77Gestão: nunca cria
    cooperado novo. Sem aceite, envia o termo; com aceite, cria e envia uma
    escala de teste para ele."""
    from nova_guarda.onboarding import send_terms_to_phone

    phone = normalize_phone(phone)
    if not phone:
        raise ValueError("Informe o telefone do cooperado de teste.")
    cooperator = get_cooperator(phone)
    if not cooperator or cooperator.get("onboarding_status") != "accepted":
        terms = send_terms_to_phone(phone, source="teste")
        if not terms.get("ok"):
            raise ValueError(terms.get("error") or terms.get("reason") or "Não foi possível enviar o termo.")
        return {"action": "terms_sent", "phone": phone}
    result = seed_test_booking_and_send(phone, cooperator.get("partner_name") or "", customer_name="Cliente Teste")
    return {"action": "booking_sent", "phone": phone, **result}


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
