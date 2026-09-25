from typing import Any

from nova_guarda.config import TERMS_ACCEPTANCE_TEXT, TERMS_REJECTION_TEXT


def classify_agenda_reply(text: str) -> str | None:
    normalized = text.strip().lower()
    if normalized in {"1", "confirmar", "confirmado", "confirmada", "booking_confirm", "confirmed"}:
        return "confirmed"
    if normalized in {"2", "cancelar", "cancelado", "cancelada", "recusar", "recusado", "recusada", "booking_decline", "declined"}:
        return "cancelled"
    return None


def classify_checkin_reply(text: str) -> str | None:
    normalized = text.strip().lower()
    if normalized.startswith((
        "checkin_arrived:",
        "checkin2_arrived:",
        "checkout_confirm:",
        "checkin_late:",
        "checkin_not_going:",
        "late_15:",
        "late_30:",
        "late_60:",
        "reason_personal:",
        "reason_access:",
        "reason_client_cancelled:",
        "reason_other:",
    )):
        return normalized
    if normalized in {"sim, cheguei", "cheguei", "sim", "checkin_arrived"}:
        return "arrived"
    if normalized in {"checkin2_arrived"}:
        return "arrived_link"
    if normalized in {"vou atrasar", "atrasarei", "atrasar", "checkin_late"}:
        return "late"
    if normalized in {"não vou", "nao vou", "não irei", "nao irei", "checkin_not_going"}:
        return "not_going"
    if normalized in {"15 minutos", "15 min", "late_15"}:
        return "late_15"
    if normalized in {"30 minutos", "30 min", "late_30"}:
        return "late_30"
    if normalized in {"1 hora", "60 minutos", "60 min", "late_60"}:
        return "late_60"
    if normalized in {"problema pessoal", "reason_personal"}:
        return "reason_personal"
    if normalized in {"sem acesso ao local", "reason_access"}:
        return "reason_access"
    if normalized in {"cliente cancelou", "reason_client_cancelled"}:
        return "reason_client_cancelled"
    if normalized in {"outro motivo", "reason_other"}:
        return "reason_other"
    return None


def is_terms_acceptance(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    return normalized in {
        TERMS_ACCEPTANCE_TEXT,
        "terms_accept",
        "aceito os termos",
        "li e aceito",
        "eu li e aceito os termos",
    }


def is_terms_rejection(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    return normalized in {
        TERMS_REJECTION_TEXT,
        "terms_reject",
        "nao aceito os termos",
        "não aceito",
        "nao aceito",
    }


def agenda_status_label(status: str) -> str:
    labels = {
        "pending": "Aguardando resposta",
        "sent": "Escala enviada",
        "confirmed": "Agenda confirmada",
        "cancelled": "Agenda cancelada",
        "conflict": "Revisar manualmente",
    }
    return labels.get(status, status)


def checkin_status_label(status: str) -> str:
    labels = {
        "checkin_sent": "Check-in enviado",
        "checkin_pending": "Aguardando check-in",
        "checked_in": "Check-in registrado",
        "checkout_pending": "Aguardando check-out",
        "checked_out": "Check-out registrado",
        "arrived": "Chegou ao local",
        "location_received": "Localização recebida",
        "location_requested": "Solicitou envio de localização",
        "late": "Aguardando tempo de atraso",
        "late_pending": "Aguardando tempo de atraso",
        "late_reported": "Atraso registrado",
        "late_15": "Vai atrasar 15 minutos",
        "late_30": "Vai atrasar 30 minutos",
        "late_60": "Vai atrasar 1 hora",
        "not_going": "Não vai comparecer",
        "no_show_pending": "Aguardando motivo de não comparecimento",
        "no_show_reported": "Não comparecimento registrado",
        "reason_personal": "Não vai: problema pessoal",
        "reason_access": "Não vai: sem acesso ao local",
        "reason_client_cancelled": "Não vai: cliente cancelou",
        "reason_other": "Não vai: outro motivo",
    }
    return labels.get(status, status)


def next_agenda_status(previous_status: str, decision: str) -> str:
    if previous_status == "cancelled" and decision == "confirmed":
        return "conflict"
    return decision


def agenda_data_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    return {
        "client_name": str(payload.get("client_name", "")).strip(),
        "client_address": str(payload.get("client_address", "")).strip(),
        "schedule_date": str(payload.get("schedule_date", "")).strip(),
        "schedule_time": str(payload.get("schedule_time", "")).strip(),
        "service": str(payload.get("service", "")).strip(),
    }
