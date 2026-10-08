from datetime import datetime
from typing import Any

from nova_guarda.timezone import BR_TZ


def normalize_phone(value: str) -> str:
    phone = "".join(char for char in str(value) if char.isdigit())
    if len(phone) in {10, 11}:
        return f"55{phone}"
    return phone


def get_payload_text(payload: dict[str, Any]) -> str:
    text = payload.get("text")
    if isinstance(text, dict) and text.get("message"):
        return str(text["message"])

    for key in ("buttonReply", "buttonsResponseMessage", "listResponseMessage"):
        value = payload.get(key)
        if isinstance(value, dict):
            for text_key in ("selectedRowId", "id", "selectedButtonId", "selectedDisplayText", "title", "message"):
                if value.get(text_key):
                    return str(value[text_key])

    return ""


def format_schedule(data: dict[str, str]) -> tuple[str, str]:
    """Data e horário legíveis para o cooperado. A 77Gestão costuma mandar só o
    início do atendimento em ISO/UTC (ex.: 2026-10-08T16:58:35Z)."""
    date_text = str(data.get("schedule_date") or "").strip()
    time_text = str(data.get("schedule_time") or "").strip()
    try:
        parsed = datetime.fromisoformat(date_text.replace("Z", "+00:00"))
    except ValueError:
        return date_text, time_text
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(BR_TZ)
    return parsed.strftime("%d/%m/%Y"), time_text or parsed.strftime("%H:%M")


def build_agenda_message(data: dict[str, str]) -> str:
    client = data.get("client_name") or "cooperado(a)"
    address = data.get("client_address") or "Endereço não informado"
    date, time = format_schedule(data)
    date = date or "Data não informada"
    time = time or "Horário não informado"
    service = data.get("service") or "Atendimento Nova Guarda"

    return (
        f"Olá, {client}. Aqui é a Nova Guarda.\n\n"
        "Sua escala está disponível para confirmação:\n\n"
        f"Endereço: {address}\n"
        f"Data: {date}\n"
        f"Horário: {time}\n"
        f"Atendimento: {service}\n\n"
        "Confirme abaixo se você poderá atender esta escala."
    )


def build_checkin_message(data: dict[str, str]) -> str:
    client = data.get("client_name") or "cooperado(a)"
    address = data.get("client_address") or "Endereço não informado"
    date, time = format_schedule(data)
    date = date or "Data não informada"
    time = time or "Horário não informado"
    service = data.get("service") or "Atendimento Nova Guarda"

    return (
        f"Olá, {client}. Aqui é a Nova Guarda.\n\n"
        "Está na hora de confirmar sua chegada para o atendimento:\n\n"
        f"Endereço: {address}\n"
        f"Data: {date}\n"
        f"Horário: {time}\n"
        f"Atendimento: {service}\n\n"
        "Selecione a opção que corresponde à sua situação agora."
    )


def build_checkin2_message(data: dict[str, str]) -> str:
    return build_checkin_message(data)


def build_terms_message(data: dict[str, str]) -> str:
    client = data.get("client_name") or "cooperado(a)"
    return (
        f"Termo de uso e consentimento - Nova Guarda\n"
        f"Cooperado(a): {client}"
    )


def build_terms_buttons_message() -> str:
    return (
        "Para receber avisos de escala, check-in e orientações operacionais por este WhatsApp, "
        "precisamos do seu aceite no termo de uso e consentimento.\n\n"
        "Leia o PDF enviado acima e escolha uma opção. Seu aceite ficará registrado pela Nova Guarda."
    )


def build_confirmation_reply(status: str, agenda: dict[str, Any] | None = None) -> str:
    agenda = agenda or {}
    client = agenda.get("client_name") or "sua agenda"
    date, time = format_schedule(agenda)
    date = date or "a data combinada"
    time = time or "o horário combinado"

    if status == "confirmed":
        return f"Escala confirmada, {client}. Data: {date}. Horário: {time}."
    if status == "cancelled":
        return "Recusa registrada. A Nova Guarda recebeu sua resposta para esta escala."
    if status == "conflict":
        return "Recebemos sua nova resposta. Como ela altera uma escala já recusada, a equipe Nova Guarda fará a revisão manual."
    return "Resposta recebida pela Nova Guarda."


def build_checkin_reply(status: str) -> str:
    if status == "arrived":
        return (
            "Para concluir o check-in, envie sua localização atual por aqui "
            "(clipe 📎 > Localização > Enviar localização atual). "
            "O check-in só é registrado depois que a localização chegar."
        )
    if status == "location_received":
        return "Localização recebida com sucesso pela Nova Guarda."
    if status == "late":
        return "Informe a previsão de atraso para registrarmos internamente."
    if status == "late_15":
        return "Atraso de 15 minutos registrado pela Nova Guarda."
    if status == "late_30":
        return "Atraso de 30 minutos registrado pela Nova Guarda."
    if status == "late_60":
        return "Atraso de 1 hora registrado pela Nova Guarda."
    if status == "not_going":
        return "Informe o motivo do não comparecimento para registrarmos internamente."
    if status == "reason_personal":
        return "Não comparecimento registrado com motivo: problema pessoal."
    if status == "reason_access":
        return "Não comparecimento registrado com motivo: sem acesso ao local."
    if status == "reason_client_cancelled":
        return "Não comparecimento registrado com motivo: cliente cancelou."
    if status == "reason_other":
        return "Não comparecimento registrado com motivo: outro. Se necessário, envie mais detalhes em uma nova mensagem."
    return "Resposta de check-in recebida pela Nova Guarda."
