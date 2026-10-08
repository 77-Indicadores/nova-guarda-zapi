import logging
from typing import Any

import requests

from nova_guarda.services import send_zapi_text
from nova_guarda.storage import (
    count_alerts,
    create_alert,
    get_appointment_by_message_id,
    get_booking_by_message_id,
    get_setting,
    revert_undelivered_booking,
    revert_undelivered_checkin,
)


logger = logging.getLogger(__name__)


def raise_alert(
    kind: str,
    entity_type: str,
    entity_id: str | int,
    phone: str,
    message: str,
    entity_status: str = "",
) -> bool:
    """Abre um alerta no painel e, se houver telefone de alerta configurado,
    avisa a equipe pelo WhatsApp. Com entity_status preenchido, o alerta fecha
    sozinho quando a entidade sai desse status."""
    created = create_alert(kind, entity_type, entity_id, phone, message, entity_status)
    if created:
        notify_team(message)
    return created


def notify_team(message: str) -> None:
    alert_phone = get_setting("alert_phone").strip()
    if not alert_phone:
        return
    try:
        send_zapi_text(alert_phone, f"[Nova Guarda] {message}")
    except (RuntimeError, requests.RequestException, ValueError) as exc:
        logger.exception("Erro ao avisar a equipe sobre alerta: %s", exc)


def handle_delivery_failure(message_id: str, phone: str, reason: str) -> dict[str, Any]:
    """O WhatsApp avisou que uma mensagem enviada não foi entregue. Escala e
    check-in voltam para a fila uma única vez; se falhar de novo, fica só o
    alerta para a equipe, sem insistir em um erro permanente."""
    reason = reason or "motivo não informado"
    booking = get_booking_by_message_id(message_id) if message_id else None
    if booking and booking.get("local_status") == "sent":
        booking_id = booking["booking_id"]
        first_failure = count_alerts("delivery_failed", "booking", booking_id) == 0
        if first_failure:
            revert_undelivered_booking(booking_id)
        raise_alert(
            "delivery_failed",
            "booking",
            booking_id,
            phone,
            f"Escala {booking_id} não foi entregue no WhatsApp de {booking.get('partner_name') or phone}: {reason}."
            + (" Reenviando no próximo ciclo." if first_failure else " Reenvio automático já tentado."),
            "pending" if first_failure else "sent",
        )
        return {"entity_type": "booking", "entity_id": booking_id, "retry": first_failure}

    appointment = get_appointment_by_message_id(message_id) if message_id else None
    if appointment and appointment.get("local_status") in {"checkin_pending", "checkout_pending"}:
        appointment_id = appointment["appointment_id"]
        status = appointment["local_status"]
        first_failure = status == "checkin_pending" and count_alerts("delivery_failed", "appointment", appointment_id) == 0
        if first_failure:
            revert_undelivered_checkin(appointment_id)
        raise_alert(
            "delivery_failed",
            "appointment",
            appointment_id,
            phone,
            f"Mensagem de {'check-in' if status == 'checkin_pending' else 'check-out'} do atendimento "
            f"{appointment_id} não foi entregue para {phone}: {reason}."
            + (" Reenviando no próximo ciclo." if first_failure else ""),
            "sent" if first_failure else status,
        )
        return {"entity_type": "appointment", "entity_id": appointment_id, "retry": first_failure}

    raise_alert("delivery_failed", "cooperator", phone, phone, f"Mensagem não entregue para {phone}: {reason}.")
    return {"entity_type": "cooperator", "entity_id": phone, "retry": False}
