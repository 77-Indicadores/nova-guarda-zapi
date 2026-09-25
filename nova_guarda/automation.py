import logging
from datetime import datetime, timedelta
from typing import Any

import requests

from nova_guarda.gestao77_service import (
    agenda_data_from_booking,
    list_pending_partner_bookings,
    retry_pending_gestao77_syncs,
    send_booking_to_partner,
    send_checkin_to_partner,
    send_checkout_to_partner,
)
from nova_guarda.messages import normalize_phone
from nova_guarda.onboarding import ensure_operational_access
from nova_guarda.storage import all_settings, list_appointments, list_bookings_for_checkin, save_poller_run
from nova_guarda.timezone import BR_TZ, br_now, br_timestamp


logger = logging.getLogger(__name__)


def run_automation_once(month: int | None = None, year: int | None = None, limit: int | None = None) -> dict[str, Any]:
    started_at = br_timestamp()
    settings = all_settings()
    now = br_now()
    month = int(month or now.month)
    year = int(year or now.year)
    limit = int(limit if limit is not None else settings.get("automation_send_limit") or 25)
    if limit < 0:
        limit = 0

    payload: dict[str, Any] = {
        "month": month,
        "year": year,
        "limit": limit,
        "items": [],
        "checkins": [],
        "checkouts": [],
        "retry": {},
    }

    try:
        bookings = list_pending_partner_bookings(month, year)
        sent_count = 0
        for booking in bookings:
            booking_id = str(booking.get("booking_id") or "")
            phone = normalize_phone(str(booking.get("phone") or ""))
            item = {
                "booking_id": booking_id,
                "phone": phone,
                "partner_name": booking.get("partner_name") or booking.get("name") or "",
                "ok": False,
                "action": "skipped",
            }

            if not booking_id:
                item["error"] = "Booking sem booking_id."
                payload["items"].append(item)
                continue
            if not phone:
                item["error"] = "Booking sem telefone mapeado."
                payload["items"].append(item)
                continue
            if limit and sent_count >= limit:
                item["error"] = "Limite da execução atingido."
                payload["items"].append(item)
                continue

            allowed, gate_message = ensure_operational_access(phone)
            if not allowed:
                item["error"] = gate_message
                payload["items"].append(item)
                continue

            try:
                result = send_booking_to_partner(booking_id, phone)
                sent_count += 1
                item.update({"ok": True, "action": "sent", "result": result})
            except (PermissionError, RuntimeError, requests.RequestException, ValueError) as exc:
                item["error"] = str(exc)
            payload["items"].append(item)

        if settings.get("auto_checkin_enabled", "1") == "1":
            payload["checkins"] = run_checkin_dispatch(settings)
            payload["checkouts"] = run_checkout_dispatch(settings)

        payload["retry"] = retry_pending_gestao77_syncs()
        has_errors = any(item.get("error") for group in ("items", "checkins", "checkouts") for item in payload[group])
        retry_failed = not payload.get("retry", {}).get("ok", True)
        status = "partial" if has_errors or retry_failed else "success"
        run = save_poller_run(status, payload, started_at=started_at)
        return {"ok": status == "success", "run": run, **payload}
    except Exception as exc:
        logger.exception("Erro na automação Nova Guarda: %s", exc)
        payload["error"] = str(exc)
        run = save_poller_run("failed", payload, error=str(exc), started_at=started_at)
        return {"ok": False, "run": run, **payload}


def run_checkin_dispatch(settings: dict[str, str]) -> list[dict[str, Any]]:
    lead_minutes = positive_int(settings.get("checkin_lead_minutes"), 120, minimum=0, maximum=10080)
    now = br_now()
    results: list[dict[str, Any]] = []
    for booking in list_bookings_for_checkin():
        appointment_id = str(booking.get("appointment_id") or "")
        phone = normalize_phone(str(booking.get("phone") or ""))
        item = {
            "booking_id": booking.get("booking_id"),
            "appointment_id": appointment_id,
            "phone": phone,
            "ok": False,
            "action": "skipped",
        }
        if not appointment_id or not phone:
            item["error"] = "Booking sem appointment ou telefone."
            results.append(item)
            continue
        allowed, gate_message = ensure_operational_access(phone)
        if not allowed:
            item["error"] = gate_message
            results.append(item)
            continue
        appointment_at = appointment_start_at(booking)
        if not appointment_at:
            item["error"] = "Appointment sem horário interpretável."
            results.append(item)
            continue
        if now < appointment_at - timedelta(minutes=lead_minutes):
            item["reason"] = "Fora da janela de check-in."
            item["appointment_at"] = appointment_at.isoformat()
            results.append(item)
            continue
        try:
            result = send_checkin_to_partner(
                appointment_id,
                phone,
                agenda_data_from_booking(booking),
                use_location_link=True,
                booking_id=str(booking.get("booking_id") or ""),
            )
            item.update({"ok": True, "action": "checkin_sent", "result": result})
        except (PermissionError, RuntimeError, requests.RequestException, ValueError) as exc:
            item["error"] = str(exc)
        results.append(item)
    return results


def run_checkout_dispatch(settings: dict[str, str]) -> list[dict[str, Any]]:
    after_minutes = positive_int(settings.get("checkout_after_minutes"), 60, minimum=0, maximum=10080)
    now = br_now()
    results: list[dict[str, Any]] = []
    for appointment in list_appointments("checked_in"):
        appointment_id = str(appointment.get("appointment_id") or "")
        phone = normalize_phone(str(appointment.get("phone") or ""))
        item = {
            "booking_id": appointment.get("booking_id"),
            "appointment_id": appointment_id,
            "phone": phone,
            "ok": False,
            "action": "skipped",
        }
        if not appointment_id or not phone:
            item["error"] = "Appointment sem id ou telefone."
            results.append(item)
            continue
        reference_at = appointment_reference_at(appointment)
        if not reference_at:
            item["error"] = "Appointment sem horário interpretável para check-out."
            results.append(item)
            continue
        if now < reference_at + timedelta(minutes=after_minutes):
            item["reason"] = "Fora da janela de check-out."
            item["reference_at"] = reference_at.isoformat()
            results.append(item)
            continue
        try:
            result = send_checkout_to_partner(appointment_id, phone)
            item.update({"ok": True, "action": "checkout_sent", "result": result})
        except (PermissionError, RuntimeError, requests.RequestException, ValueError) as exc:
            item["error"] = str(exc)
        results.append(item)
    return results


def appointment_start_at(booking: dict[str, Any]) -> datetime | None:
    payload = booking.get("payload") or {}
    appointment_id = str(booking.get("appointment_id") or "")
    appointments = payload.get("appointments") if isinstance(payload.get("appointments"), list) else []
    appointment = next((item for item in appointments if str(item.get("id") or item.get("appointment_id") or "") == appointment_id), None)
    if not appointment and appointments and isinstance(appointments[0], dict):
        appointment = appointments[0]
    return parse_br_datetime(
        (appointment or {}).get("start_at")
        or (appointment or {}).get("starts_at")
        or payload.get("schedule_date")
        or payload.get("date")
    )


def appointment_reference_at(appointment: dict[str, Any]) -> datetime | None:
    payload = appointment.get("payload") or {}
    return parse_br_datetime(
        payload.get("end_at")
        or payload.get("ends_at")
        or payload.get("start_at")
        or payload.get("starts_at")
        or payload.get("schedule_date")
        or payload.get("date")
    )


def parse_br_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%d/%m/%Y")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in formats:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=BR_TZ)
    return parsed.astimezone(BR_TZ)


def positive_int(value: str | int | None, default: int, minimum: int = 0, maximum: int = 10080) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))
