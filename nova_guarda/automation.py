import logging
from datetime import datetime, timedelta
from typing import Any

import requests

from nova_guarda.alerts import raise_alert
from nova_guarda.gestao77_service import (
    agenda_data_from_booking,
    list_pending_partner_bookings,
    refresh_booking_appointments,
    retry_pending_gestao77_syncs,
    send_booking_to_partner,
    send_followup_reminder,
    send_checkin_to_partner,
    send_checkout_to_partner,
)
from nova_guarda.messages import normalize_phone
from nova_guarda.onboarding import dispatch_pending_terms, ensure_operational_access, mode_allows_phone
from nova_guarda.storage import (
    all_settings,
    auto_resolve_alerts,
    get_appointment,
    list_appointments,
    list_bookings,
    list_bookings_for_checkin,
    mark_entity_alerted,
    mark_entity_reminded,
    purge_bookings_without_gestao77_id,
    purge_webhook_events,
    save_poller_run,
    set_settings,
)
from nova_guarda.timezone import BR_TZ, br_now, br_timestamp


logger = logging.getLogger(__name__)

TERMS_MAX_ATTEMPTS = 3
WEBHOOK_EVENT_RETENTION_DAYS = 7
CHECKIN_FALLBACK_SHIFT_HOURS = 12
FOLLOW_UP_MAX_DELAY = timedelta(hours=48)
STALE_APPOINTMENT_MESSAGES = {
    "checkin_pending": "Check-in do atendimento {appointment_id} sem resposta de {phone} depois do lembrete (possível falta).",
    "location_pending": "{phone} disse que chegou ao atendimento {appointment_id}, mas não enviou a localização.",
    "checkout_pending": "Check-out do atendimento {appointment_id} sem resposta de {phone} depois do lembrete.",
    "checkout_location_pending": "{phone} disse que finalizou o atendimento {appointment_id}, mas não enviou a localização.",
    "late_reported": "{phone} avisou atraso no atendimento {appointment_id} e não confirmou a chegada.",
}


def run_automation_once(month: int | None = None, year: int | None = None, limit: int | None = None) -> dict[str, Any]:
    started_at = br_timestamp()
    settings = all_settings()
    now = br_now()
    explicit_period = month is not None or year is not None
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
        "terms": [],
        "checkins": [],
        "checkouts": [],
        "followups": [],
        "retry": {},
    }

    try:
        payload["purged_fake_bookings"] = purge_bookings_without_gestao77_id()
        bookings = list_pending_partner_bookings(month, year)
        if not explicit_period:
            # Escala do mês seguinte liberada para envio não espera o mês virar.
            next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
            try:
                known = {str(booking.get("booking_id")) for booking in bookings}
                bookings += [
                    booking
                    for booking in list_pending_partner_bookings(next_month, next_year)
                    if str(booking.get("booking_id")) not in known
                ]
            except (RuntimeError, requests.RequestException, ValueError) as exc:
                payload["next_month_error"] = str(exc)

        # Escala que voltou para a fila porque o WhatsApp não entregou: a 77Gestão
        # já a considera enviada, então ela não vem mais na consulta acima.
        known = {str(booking.get("booking_id")) for booking in bookings}
        bookings += [
            booking
            for booking in list_bookings("pending")
            if booking.get("whatsapp_message_id") and str(booking.get("booking_id")) not in known
        ]

        if terms_dispatch_due(settings, now):
            payload["terms"] = dispatch_pending_terms(bookings, skip_sent_today=True)
            record_terms_dispatch(settings, now, payload["terms"])

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
            if not mode_allows_phone(phone):
                item["reason"] = "Modo teste: fora da lista de cooperados de teste."
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

        payload["followups"] = run_stale_sweep(settings, now)
        payload["alerts_resolved"] = auto_resolve_alerts()
        purge_webhook_events((now - timedelta(days=WEBHOOK_EVENT_RETENTION_DAYS)).isoformat(timespec="seconds"))

        payload["retry"] = retry_pending_gestao77_syncs()
        has_errors = any(
            item.get("error")
            for group in ("items", "terms", "checkins", "checkouts", "followups")
            for item in payload[group]
        )
        retry_failed = not payload.get("retry", {}).get("ok", True)
        status = "partial" if has_errors or retry_failed else "success"
        run = save_poller_run(status, payload, started_at=started_at)
        return {"ok": status == "success", "run": run, **payload}
    except Exception as exc:
        logger.exception("Erro na automação Nova Guarda: %s", exc)
        payload["error"] = str(exc)
        run = save_poller_run("failed", payload, error=str(exc), started_at=started_at)
        return {"ok": False, "run": run, **payload}


def terms_dispatch_due(settings: dict[str, str], now: datetime) -> bool:
    """Envio do termo roda uma vez por dia, no primeiro ciclo a partir do
    horário configurado (07h por padrão, início do dia operacional)."""
    if settings.get("terms_auto_enabled", "1") != "1":
        return False
    send_hour = positive_int(settings.get("terms_send_hour"), 7, minimum=0, maximum=23)
    if now.hour < send_hour:
        return False
    return settings.get("terms_last_dispatch_date") != now.date().isoformat()


def record_terms_dispatch(settings: dict[str, str], now: datetime, results: list[dict[str, Any]]) -> None:
    """Fecha o disparo do dia. Se algum envio falhou por erro do provider, o
    próximo ciclo tenta de novo só quem falhou, até TERMS_MAX_ATTEMPTS; depois
    disso o que restar vira alerta para a equipe."""
    today = now.date().isoformat()
    attempts_date, _, attempts_count = str(settings.get("terms_dispatch_attempts") or "").partition(":")
    attempts = (int(attempts_count) if attempts_date == today and attempts_count.isdigit() else 0) + 1
    failed = [item for item in results if item.get("retryable")]
    if failed and attempts < TERMS_MAX_ATTEMPTS:
        set_settings({"terms_dispatch_attempts": f"{today}:{attempts}"})
        return
    for item in failed:
        raise_alert(
            "terms_failed",
            "cooperator",
            item["phone"],
            item["phone"],
            f"Termo não enviado para {item['phone']} após {attempts} tentativa(s): {item.get('error')}",
        )
    set_settings({"terms_last_dispatch_date": today, "terms_dispatch_attempts": ""})


def run_stale_sweep(settings: dict[str, str], now: datetime) -> list[dict[str, Any]]:
    """Procura tudo que está parado esperando o cooperado. Primeiro manda um
    lembrete; se continuar sem resposta pelo mesmo prazo, abre alerta para a
    equipe. Nenhum estado fica parado em silêncio."""
    booking_wait = timedelta(hours=positive_int(settings.get("booking_reminder_hours"), 12, minimum=1, maximum=720))
    presence_wait = timedelta(minutes=positive_int(settings.get("presence_reminder_minutes"), 15, minimum=1, maximum=1440))
    results: list[dict[str, Any]] = []

    for booking in list_bookings("sent"):
        item = follow_up("booking", booking["booking_id"], booking, parse_br_datetime(booking.get("sent_at")), booking_wait, now)
        if item:
            results.append(item)

    for status in STALE_APPOINTMENT_MESSAGES:
        for appointment in list_appointments(status):
            since = parse_br_datetime(appointment.get("updated_at"))
            wait = presence_wait
            if status == "checkin_pending":
                # O check-in sai antes do horário; o silêncio só conta a partir do início do atendimento.
                start_at = parse_br_datetime((appointment.get("payload") or {}).get("start_at"))
                since = max(since, start_at) if since and start_at else since
            elif status == "late_reported":
                wait += timedelta(minutes=int(appointment.get("late_minutes") or 0))
            item = follow_up("appointment", appointment["appointment_id"], appointment, since, wait, now)
            if item:
                results.append(item)
    return results


def follow_up(
    entity_type: str,
    entity_id: str,
    entity: dict[str, Any],
    since: datetime | None,
    wait: timedelta,
    now: datetime,
) -> dict[str, Any] | None:
    status = str(entity.get("local_status") or "")
    phone = normalize_phone(str(entity.get("phone") or ""))
    item: dict[str, Any] = {"entity_type": entity_type, "entity_id": entity_id, "phone": phone, "status": status, "ok": True}
    if not mode_allows_phone(phone):
        return None

    if entity.get("reminder_status") != status:
        if not since or now - since < wait:
            return None
        if now - since > wait + FOLLOW_UP_MAX_DELAY:
            # Histórico antigo (ex.: escalas de meses atrás): não vira lembrete em massa.
            return None
        item["action"] = "reminder"
        try:
            send_followup_reminder(entity_type, entity)
        except (PermissionError, RuntimeError, requests.RequestException, ValueError) as exc:
            item.update({"ok": False, "error": str(exc)})
        # Marca mesmo se o envio falhar: o próximo passo é o alerta, não repetir lembrete sem fim.
        mark_entity_reminded(entity_type, entity_id, status)
        return item

    if entity.get("alert_status") == status:
        return None
    reminded_at = parse_br_datetime(entity.get("reminder_at"))
    if not reminded_at or now - reminded_at < wait:
        return None
    if entity_type == "booking":
        message = f"Escala {entity_id} de {entity.get('partner_name') or phone} continua sem resposta depois do lembrete."
    else:
        message = STALE_APPOINTMENT_MESSAGES[status].format(appointment_id=entity_id, phone=phone)
    raise_alert("no_response", entity_type, entity_id, phone, message, status)
    mark_entity_alerted(entity_type, entity_id, status)
    item["action"] = "alert"
    return item


def run_checkin_dispatch(settings: dict[str, str]) -> list[dict[str, Any]]:
    """Dispara o check-in de cada atendimento da escala confirmada que entrou
    na janela — uma escala pode ter vários atendimentos (um por dia)."""
    lead_minutes = positive_int(settings.get("checkin_lead_minutes"), 120, minimum=0, maximum=10080)
    now = br_now()
    results: list[dict[str, Any]] = []
    for booking in list_bookings_for_checkin():
        phone = normalize_phone(str(booking.get("phone") or ""))
        base_item = {"booking_id": booking.get("booking_id"), "phone": phone, "ok": False, "action": "skipped"}
        if not phone:
            results.append({**base_item, "error": "Booking sem appointment ou telefone."})
            continue
        if not mode_allows_phone(phone):
            continue
        allowed, gate_message = ensure_operational_access(phone)
        if not allowed:
            results.append({**base_item, "error": gate_message})
            continue

        booking = refresh_booking_appointments(booking)
        for appointment in booking_appointments(booking):
            appointment_id = str(appointment.get("id") or appointment.get("appointment_id") or "")
            if not appointment_id or appointment_is_cancelled(appointment):
                continue
            item = {**base_item, "appointment_id": appointment_id}
            starts_at = parse_br_datetime(appointment.get("start_at") or appointment.get("starts_at"))
            if not starts_at:
                item["error"] = "Appointment sem horário interpretável."
                results.append(item)
                continue
            # Atendimento de dia anterior não recebe check-in atrasado. A folga
            # mínima cobre turno curto confirmado depois do horário de fim.
            ends_at = max(
                parse_br_datetime(appointment.get("end_at") or appointment.get("ends_at")) or starts_at,
                starts_at + timedelta(hours=CHECKIN_FALLBACK_SHIFT_HOURS),
            )
            if now < starts_at - timedelta(minutes=lead_minutes) or now > ends_at:
                continue
            existing = get_appointment(appointment_id)
            if existing and existing.get("local_status") != "sent":
                continue
            try:
                result = send_checkin_to_partner(
                    appointment_id,
                    phone,
                    agenda_data_from_booking(booking, appointment),
                    use_location_link=True,
                    booking_id=str(booking.get("booking_id") or ""),
                )
                item.update({"ok": True, "action": "checkin_sent", "result": result})
            except (PermissionError, RuntimeError, requests.RequestException, ValueError) as exc:
                item["error"] = str(exc)
            results.append(item)
    return results


def booking_appointments(booking: dict[str, Any]) -> list[dict[str, Any]]:
    payload = booking.get("payload") or {}
    appointments = payload.get("appointments") if isinstance(payload.get("appointments"), list) else []
    appointments = [item for item in appointments if isinstance(item, dict)]
    if appointments:
        return appointments
    # Escala importada sem a lista de appointments: usa o appointment único do booking.
    start_at = payload.get("schedule_date") or payload.get("date")
    return [{"id": booking.get("appointment_id"), "start_at": start_at}] if booking.get("appointment_id") else []


def appointment_is_cancelled(appointment: dict[str, Any]) -> bool:
    return str(appointment.get("status") or "").strip().lower() in {"cancelled", "canceled", "cancelado", "cancelada"}


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
        if not mode_allows_phone(phone):
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
