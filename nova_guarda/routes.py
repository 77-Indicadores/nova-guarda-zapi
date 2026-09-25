import logging
import os
import uuid

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string, request

load_dotenv(encoding="utf-8-sig")

import nova_guarda.services as services
from nova_guarda.gestao77_service import (
    list_pending_partner_bookings,
    record_local_late,
    record_local_no_show,
    retry_pending_gestao77_syncs,
    mark_appointment_checked_out,
    mark_booking_sent,
    send_booking_to_partner,
    send_checkin_to_partner,
    send_checkout_to_partner,
    sync_appointment_checkout,
    sync_booking_reply_for_phone,
    sync_checkin_for_phone,
)
from nova_guarda.onboarding import (
    accept_terms,
    ensure_operational_access,
    handle_activation_request,
    is_activation_command,
    reject_terms,
)
from nova_guarda.config import (
    PORT,
    PUBLIC_BASE_URL,
    WHATSAPP_VERIFY_TOKEN,
    WEBHOOK_PATH,
)
from nova_guarda.state import AGENDA_STATE, LOCATION_LINKS, RECEIVED_EVENTS, TERMS_STATE
from nova_guarda.storage import get_cooperator, init_db, list_bookings, upsert_booking
from nova_guarda.timezone import br_now, br_timestamp


from nova_guarda.simulator import CHAT_HTML, DEV_CHAT_HTML, LOCATION_HTML


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


from nova_guarda.flows import (
    agenda_data_from_payload,
    agenda_status_label,
    checkin_status_label,
    classify_agenda_reply,
    classify_checkin_reply,
    is_terms_acceptance,
    is_terms_rejection,
)
from nova_guarda.messages import (
    build_agenda_message,
    build_checkin2_message,
    build_checkin_message,
    build_checkin_reply,
    build_confirmation_reply,
    build_terms_buttons_message,
    build_terms_message,
    get_payload_text,
    normalize_phone,
)


def create_location_link(phone: str) -> str:
    token = uuid.uuid4().hex
    LOCATION_LINKS[token] = {
        "phone": phone,
        "created_at": br_timestamp(),
        "status": "pending",
    }
    base_url = PUBLIC_BASE_URL or f"http://localhost:{PORT}"
    return f"{base_url}/checkin-location/{token}"


def reverse_geocode(latitude: float, longitude: float) -> str:
    from nova_guarda.clients.geocoding import GeocodingClient

    return GeocodingClient().reverse_geocode(latitude, longitude)


from nova_guarda.services import (
    send_zapi_agenda_buttons,
    send_zapi_checkin_options,
    send_zapi_late_buttons,
    send_zapi_no_show_reasons,
    send_zapi_terms_buttons,
    send_zapi_text,
    append_fake_sent_message,
    send_terms_document,
    update_agenda_state,
)


def process_webhook_payload(payload: dict) -> None:
    payload = normalize_incoming_whatsapp_payload(payload)
    logger.info("Payload recebido do WhatsApp: %s", payload)
    RECEIVED_EVENTS.appendleft(
        {
            "received_at": br_timestamp(),
            "payload": payload,
        }
    )

    if payload.get("type") == "ReceivedCallback" and not payload.get("fromMe"):
        phone = normalize_phone(str(payload.get("phone", "")).strip())
        text = get_payload_text(payload)
        decision = classify_agenda_reply(text)
        checkin_decision = classify_checkin_reply(text)
        location = payload.get("location")

        if phone and is_activation_command(text) and not payload.get("isGroup"):
            try:
                result = handle_activation_request(phone, text)
                TERMS_STATE[phone] = terms_state_from_cooperator(phone)
                if result.get("response"):
                    append_onboarding_reply(phone, result["status"], result.get("reply", ""), result.get("response"))
                RECEIVED_EVENTS.appendleft(
                    {
                        "received_at": br_timestamp(),
                        "payload": {
                            "type": "Onboarding",
                            "phone": phone,
                            "status": result["status"],
                            "text": {"message": result.get("reply", "")},
                        },
                    }
                )
            except (RuntimeError, requests.RequestException, ValueError) as exc:
                logger.exception("Erro ao iniciar onboarding: %s", exc)
                send_onboarding_error(phone)

        elif phone and is_terms_acceptance(text) and not payload.get("isGroup"):
            try:
                result = accept_terms(phone, text)
                TERMS_STATE[phone] = terms_state_from_cooperator(phone)
                append_onboarding_reply(phone, result["status"], result.get("reply", ""), result.get("response"))
                RECEIVED_EVENTS.appendleft(
                    {
                        "received_at": br_timestamp(),
                        "payload": {
                            "type": "Onboarding",
                            "phone": phone,
                            "status": result["status"],
                            "text": {"message": result.get("reply", "")},
                            "response": result.get("response"),
                        },
                    }
                )
            except (RuntimeError, requests.RequestException, ValueError) as exc:
                logger.exception("Erro ao confirmar aceite dos termos: %s", exc)
                send_onboarding_error(phone)

        elif phone and is_terms_rejection(text) and not payload.get("isGroup"):
            try:
                result = reject_terms(phone, text)
                TERMS_STATE[phone] = terms_state_from_cooperator(phone)
                append_onboarding_reply(phone, result["status"], result.get("reply", ""), result.get("response"))
                RECEIVED_EVENTS.appendleft(
                    {
                        "received_at": br_timestamp(),
                        "payload": {
                            "type": "Onboarding",
                            "phone": phone,
                            "status": result["status"],
                            "text": {"message": result.get("reply", "")},
                            "response": result.get("response"),
                        },
                    }
                )
            except (RuntimeError, requests.RequestException, ValueError) as exc:
                logger.exception("Erro ao confirmar recusa dos termos: %s", exc)
                send_onboarding_error(phone)

        elif phone and isinstance(location, dict) and not payload.get("isGroup"):
            latitude = location.get("latitude")
            longitude = location.get("longitude")
            state = AGENDA_STATE.setdefault(
                phone,
                {
                    "phone": phone,
                    "status": "checkin_sent",
                    "status_label": checkin_status_label("checkin_sent"),
                    "agenda": {},
                    "history": [],
                },
            )
            state["status"] = "location_received"
            state["status_label"] = checkin_status_label("location_received")
            state["location"] = {
                "latitude": latitude,
                "longitude": longitude,
                "address": location.get("address", ""),
                "url": location.get("url", ""),
                "maps_url": f"https://www.google.com/maps?q={latitude},{longitude}" if latitude and longitude else "",
            }
            state["updated_at"] = br_timestamp()
            state.setdefault("history", []).append(
                {
                    "at": state["updated_at"],
                    "reply": "location",
                    "to": "location_received",
                    "location": state["location"],
                }
            )
            sync_result = None
            try:
                sync_result = sync_checkin_for_phone(phone, "location_received", state["location"].get("address", ""))
            except (RuntimeError, requests.RequestException, ValueError) as exc:
                logger.exception("Erro ao sincronizar check-in no 77Gestão: %s", exc)

            try:
                reply = build_checkin_reply("location_received")
                response_payload = send_zapi_text(phone, reply)
                RECEIVED_EVENTS.appendleft(
                    {
                        "received_at": br_timestamp(),
                        "payload": {
                            "type": "AutoReply",
                            "phone": phone,
                            "status": "location_received",
                            "text": {"message": reply},
                            "gestao77_sync": sync_result,
                            "response": response_payload,
                        },
                    }
                )
            except (RuntimeError, requests.RequestException, ValueError) as exc:
                logger.exception("Erro ao confirmar localização recebida: %s", exc)

        elif phone and decision and not payload.get("isGroup"):
            try:
                sync_result = sync_booking_reply_for_phone(phone, decision)
            except (KeyError, RuntimeError, requests.RequestException, ValueError) as exc:
                logger.exception("Erro ao sincronizar resposta da escala no 77Gestão: %s", exc)
                reply = "Não consegui registrar sua resposta da escala agora. Fale com a equipe da Nova Guarda."
                try:
                    response_payload = send_zapi_text(phone, reply)
                    RECEIVED_EVENTS.appendleft(
                        {
                            "received_at": br_timestamp(),
                            "payload": {
                                "type": "AutoReply",
                                "phone": phone,
                                "status": "booking_sync_error",
                                "text": {"message": reply},
                                "response": response_payload,
                            },
                        }
                    )
                except (RuntimeError, requests.RequestException, ValueError) as send_exc:
                    logger.exception("Erro ao enviar falha de escala: %s", send_exc)
                return

            next_status = "confirmed" if decision == "confirmed" else "cancelled"
            state = update_agenda_state(phone, next_status, text)
            reply = build_confirmation_reply(state["status"], state.get("agenda"))

            try:
                response_payload = send_zapi_text(phone, reply)
                RECEIVED_EVENTS.appendleft(
                    {
                        "received_at": br_timestamp(),
                        "payload": {
                            "type": "AutoReply",
                            "phone": phone,
                            "status": state["status"],
                            "text": {"message": reply},
                            "gestao77_sync": sync_result,
                            "response": response_payload,
                        },
                    }
                )
            except (RuntimeError, requests.RequestException, ValueError) as exc:
                logger.exception("Erro ao enviar resposta automática: %s", exc)
        elif phone and checkin_decision and not payload.get("isGroup"):
            if checkin_decision.startswith("checkin_late:"):
                appointment_id = checkin_decision.split(":", 1)[1]
                try:
                    response_payload = send_zapi_late_buttons(phone, appointment_id)
                    RECEIVED_EVENTS.appendleft(
                        {
                            "received_at": br_timestamp(),
                            "payload": {
                                "type": "AutoReply",
                                "phone": phone,
                                "status": "late_pending",
                                "text": {"message": "Qual é sua previsão de atraso?"},
                                "response": response_payload,
                            },
                        }
                    )
                except (RuntimeError, requests.RequestException, ValueError) as exc:
                    logger.exception("Erro ao enviar opções de atraso: %s", exc)
                return
            if checkin_decision.startswith("checkin_not_going:"):
                appointment_id = checkin_decision.split(":", 1)[1]
                try:
                    response_payload = send_zapi_no_show_reasons(phone, appointment_id)
                    RECEIVED_EVENTS.appendleft(
                        {
                            "received_at": br_timestamp(),
                            "payload": {
                                "type": "AutoReply",
                                "phone": phone,
                                "status": "no_show_pending",
                                "text": {"message": "Informe o motivo do não comparecimento:"},
                                "response": response_payload,
                            },
                        }
                    )
                except (RuntimeError, requests.RequestException, ValueError) as exc:
                    logger.exception("Erro ao enviar motivos de não comparecimento: %s", exc)
                return
            if checkin_decision.startswith(("late_15:", "late_30:", "late_60:")):
                try:
                    local_result = record_local_late(phone, checkin_decision)
                    reply = f"Atraso de {local_result['late_minutes']} minutos registrado pela Nova Guarda."
                    response_payload = send_zapi_text(phone, reply)
                    RECEIVED_EVENTS.appendleft(
                        {
                            "received_at": br_timestamp(),
                            "payload": {
                                "type": "AutoReply",
                                "phone": phone,
                                "status": "late_reported",
                                "text": {"message": reply},
                                "local_event": local_result,
                                "response": response_payload,
                            },
                        }
                    )
                except (PermissionError, KeyError, RuntimeError, requests.RequestException, ValueError) as exc:
                    logger.exception("Erro ao registrar atraso local: %s", exc)
                    reply = "Não consegui registrar seu atraso agora. Fale com a equipe da Nova Guarda."
                    try:
                        send_zapi_text(phone, reply)
                    except (RuntimeError, requests.RequestException, ValueError):
                        pass
                return
            if checkin_decision.startswith(("reason_personal:", "reason_access:", "reason_client_cancelled:", "reason_other:")):
                try:
                    local_result = record_local_no_show(phone, checkin_decision)
                    reply = f"Não comparecimento registrado com motivo: {local_result['reason']}."
                    response_payload = send_zapi_text(phone, reply)
                    RECEIVED_EVENTS.appendleft(
                        {
                            "received_at": br_timestamp(),
                            "payload": {
                                "type": "AutoReply",
                                "phone": phone,
                                "status": "no_show_reported",
                                "text": {"message": reply},
                                "local_event": local_result,
                                "response": response_payload,
                            },
                        }
                    )
                except (PermissionError, KeyError, RuntimeError, requests.RequestException, ValueError) as exc:
                    logger.exception("Erro ao registrar não comparecimento local: %s", exc)
                    reply = "Não consegui registrar o não comparecimento agora. Fale com a equipe da Nova Guarda."
                    try:
                        send_zapi_text(phone, reply)
                    except (RuntimeError, requests.RequestException, ValueError):
                        pass
                return

            try:
                if checkin_decision.startswith("checkout_confirm:"):
                    appointment_id = checkin_decision.split(":", 1)[1]
                    sync_result = sync_appointment_checkout(appointment_id, event_id=checkin_decision)
                    next_status = "checked_out"
                else:
                    sync_result = sync_checkin_for_phone(phone, checkin_decision)
                    next_status = "checked_in"
            except (KeyError, RuntimeError, requests.RequestException, ValueError) as exc:
                logger.exception("Erro ao sincronizar presença no 77Gestão: %s", exc)
                reply = "Não consegui registrar sua presença agora. Fale com a equipe da Nova Guarda."
                try:
                    response_payload = send_zapi_text(phone, reply)
                    RECEIVED_EVENTS.appendleft(
                        {
                            "received_at": br_timestamp(),
                            "payload": {
                                "type": "AutoReply",
                                "phone": phone,
                                "status": "presence_sync_error",
                                "text": {"message": reply},
                                "response": response_payload,
                            },
                        }
                    )
                except (RuntimeError, requests.RequestException, ValueError) as send_exc:
                    logger.exception("Erro ao enviar falha de presença: %s", send_exc)
                return

            state = AGENDA_STATE.setdefault(phone, {"phone": phone, "agenda": {}, "history": []})
            state["status"] = next_status
            state["status_label"] = checkin_status_label(next_status)
            state["last_reply"] = text
            state["updated_at"] = br_timestamp()
            state["appointment_id"] = str(sync_result.get("appointment_id", state.get("appointment_id", "")))
            state.setdefault("history", []).append({"at": state["updated_at"], "reply": text, "to": next_status})

            try:
                if next_status == "checked_out":
                    reply = "Check-out registrado com sucesso pela Nova Guarda."
                else:
                    reply = "Check-in registrado com sucesso pela Nova Guarda."
                response_payload = send_zapi_text(phone, reply)

                RECEIVED_EVENTS.appendleft(
                    {
                        "received_at": br_timestamp(),
                        "payload": {
                            "type": "AutoReply",
                            "phone": phone,
                            "status": next_status,
                            "text": {"message": reply},
                            "gestao77_sync": sync_result,
                            "response": response_payload,
                        },
                    }
                )
            except (RuntimeError, requests.RequestException, ValueError) as exc:
                logger.exception("Erro ao enviar resposta automática de check-in: %s", exc)

def normalize_incoming_whatsapp_payload(payload: dict) -> dict:
    if payload.get("type") or payload.get("phone"):
        return payload

    try:
        value = payload["entry"][0]["changes"][0]["value"]
        message = value["messages"][0]
    except (KeyError, IndexError, TypeError):
        return payload

    contact = (value.get("contacts") or [{}])[0]
    normalized = {
        "type": "ReceivedCallback",
        "fromMe": False,
        "isGroup": False,
        "phone": normalize_phone(str(message.get("from", ""))),
        "chatName": contact.get("profile", {}).get("name", ""),
        "senderName": contact.get("profile", {}).get("name", ""),
    }

    message_type = message.get("type")
    if message_type == "text":
        normalized["text"] = {"message": message.get("text", {}).get("body", "")}
    elif message_type == "interactive":
        interactive = message.get("interactive", {})
        if interactive.get("type") == "button_reply":
            reply = interactive.get("button_reply", {})
            normalized["buttonReply"] = {"id": reply.get("id", ""), "message": reply.get("title", "")}
        elif interactive.get("type") == "list_reply":
            reply = interactive.get("list_reply", {})
            normalized["listResponseMessage"] = {
                "selectedRowId": reply.get("id", ""),
                "title": reply.get("title", ""),
            }
    elif message_type == "location":
        location = message.get("location", {})
        normalized["location"] = {
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "address": location.get("address") or location.get("name") or "",
            "url": location.get("url", ""),
        }

    return normalized


def terms_state_from_cooperator(phone: str) -> dict:
    cooperator = get_cooperator(phone)
    if not cooperator:
        return {"phone": phone, "status": "not_started", "status_label": "Ativação não iniciada"}

    status = cooperator.get("onboarding_status", "not_started")
    labels = {
        "not_started": "Ativação não iniciada",
        "terms_sent": "Aguardando aceite",
        "accepted": "Termos aceitos",
        "rejected": "Termos recusados",
    }
    return {
        "phone": phone,
        "status": status,
        "status_label": labels.get(status, status),
        "partner_id": cooperator.get("partner_id", ""),
        "partner_name": cooperator.get("partner_name", ""),
        "terms_sent_at": cooperator.get("terms_sent_at"),
        "accepted_at": cooperator.get("accepted_at"),
        "rejected_at": cooperator.get("rejected_at"),
    }


def append_onboarding_reply(phone: str, status: str, reply: str, response_payload: dict | None = None) -> None:
    RECEIVED_EVENTS.appendleft(
        {
            "received_at": br_timestamp(),
            "payload": {
                "type": "AutoReply",
                "phone": phone,
                "status": status,
                "text": {"message": reply},
                "response": response_payload or {},
            },
        }
    )


def send_onboarding_error(phone: str) -> None:
    reply = "Não consegui validar seu cadastro agora. Tente novamente em alguns minutos."
    try:
        response_payload = send_zapi_text(phone, reply)
    except (RuntimeError, requests.RequestException, ValueError):
        response_payload = {}
    append_onboarding_reply(phone, "onboarding_error", reply, response_payload)


def create_app() -> Flask:
    app = Flask(__name__)
    init_db()

    @app.post(WEBHOOK_PATH)
    def webhook():
        payload = request.get_json(silent=True)

        if payload is None:
            payload = {
                "raw_body": request.get_data(as_text=True),
                "content_type": request.content_type,
            }

        process_webhook_payload(payload)

        return jsonify(
            {
                "ok": True,
                "message": "Evento recebido com sucesso",
                "payload": payload,
            }
        ), 200

    @app.get(WEBHOOK_PATH)
    def verify_webhook():
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token and token == WHATSAPP_VERIFY_TOKEN:
            return str(challenge or ""), 200
        return jsonify({"ok": False, "error": "Webhook não verificado."}), 403

    @app.get("/")
    def chat():
        return render_template_string(CHAT_HTML, port=PORT)

    @app.get("/dev/chat")
    def dev_chat():
        return render_template_string(DEV_CHAT_HTML)

    @app.post("/dev/simulate-whatsapp")
    def simulate_whatsapp():
        payload = request.get_json(silent=True) or {}
        phone = normalize_phone(str(payload.get("phone", "")))
        message = str(payload.get("message", "")).strip()
        location = payload.get("location")

        if not phone:
            return jsonify({"ok": False, "error": "Informe um telefone."}), 400

        if not message and not isinstance(location, dict):
            return jsonify({"ok": False, "error": "Informe uma mensagem ou localização."}), 400

        zapi_payload = {
            "type": "ReceivedCallback",
            "fromMe": False,
            "isGroup": False,
            "phone": phone,
            "chatName": str(payload.get("chatName", "")).strip() or "Cooperado Teste",
            "senderName": str(payload.get("senderName", "")).strip() or "Cooperado Teste",
        }

        if isinstance(location, dict):
            zapi_payload["location"] = {
                "latitude": location.get("latitude"),
                "longitude": location.get("longitude"),
                "address": str(location.get("address", "")).strip(),
                "url": str(location.get("url", "")).strip(),
            }
        else:
            zapi_payload["text"] = {"message": message}

        previous_fake_zapi = services.DEV_FAKE_ZAPI
        previous_fake_gestao77 = os.getenv("DEV_FAKE_GESTAO77")
        services.DEV_FAKE_ZAPI = True
        os.environ["DEV_FAKE_GESTAO77"] = "true"
        try:
            process_webhook_payload(zapi_payload)
        finally:
            services.DEV_FAKE_ZAPI = previous_fake_zapi
            if previous_fake_gestao77 is None:
                os.environ.pop("DEV_FAKE_GESTAO77", None)
            else:
                os.environ["DEV_FAKE_GESTAO77"] = previous_fake_gestao77

        return jsonify({"ok": True, "payload": zapi_payload}), 200

    @app.post("/dev/start-flow")
    def start_dev_flow():
        payload = request.get_json(silent=True) or {}
        phone = normalize_phone(str(payload.get("phone", "")))
        mode = str(payload.get("mode", "")).strip().lower()
        agenda_data = agenda_data_from_payload(payload)

        if not phone:
            return jsonify({"ok": False, "error": "Informe um telefone."}), 400
        if mode not in {"agenda", "checkin", "checkin2", "terms"}:
            return jsonify({"ok": False, "error": "Modo dev inválido."}), 400

        if mode == "agenda":
            message = build_agenda_message(agenda_data)
        elif mode == "checkin":
            message = build_checkin_message(agenda_data)
        elif mode == "checkin2":
            message = build_checkin2_message(agenda_data)
        else:
            message = build_terms_message(agenda_data)

        previous_fake_zapi = services.DEV_FAKE_ZAPI
        previous_fake_gestao77 = os.getenv("DEV_FAKE_GESTAO77")
        services.DEV_FAKE_ZAPI = True
        os.environ["DEV_FAKE_GESTAO77"] = "true"
        try:
            if mode == "agenda":
                dev_booking_id = str(payload.get("booking_id") or "dev-booking-1")
                upsert_booking(
                    {
                        "id": payload.get("partner_id") or "dev-partner-1",
                        "name": agenda_data.get("client_name") or "Cooperado Teste",
                        "booking_id": dev_booking_id,
                        "schedule_status": "awaiting_approval",
                        "phone": phone,
                    }
                )
                response_payload = send_zapi_agenda_buttons(phone, message)
                append_fake_sent_message(phone, mode, message, response_payload)
                mark_booking_sent(dev_booking_id)
            elif mode in {"checkin", "checkin2"}:
                response_payload = send_zapi_checkin_options(phone, message, use_location_link=mode == "checkin2")
                append_fake_sent_message(phone, mode, message, response_payload)
            else:
                response_payload = send_terms_document(phone, message)
                append_fake_sent_message(phone, mode, message, response_payload)
                terms_buttons_payload = send_zapi_terms_buttons(phone)
                append_fake_sent_message(phone, "terms_buttons", build_terms_buttons_message(), terms_buttons_payload)
        finally:
            services.DEV_FAKE_ZAPI = previous_fake_zapi
            if previous_fake_gestao77 is None:
                os.environ.pop("DEV_FAKE_GESTAO77", None)
            else:
                os.environ["DEV_FAKE_GESTAO77"] = previous_fake_gestao77

        if mode in {"agenda", "checkin", "checkin2"}:
            AGENDA_STATE[phone] = {
                "phone": phone,
                "mode": mode,
                "status": "checkin_sent" if mode in {"checkin", "checkin2"} else "pending",
                "status_label": checkin_status_label("checkin_sent") if mode in {"checkin", "checkin2"} else agenda_status_label("pending"),
                "agenda": agenda_data,
                "booking_id": str(payload.get("booking_id") or "dev-booking-1") if mode == "agenda" else "",
                "appointment_id": str(payload.get("appointment_id") or "") if mode in {"checkin", "checkin2"} else "",
                "last_reply": "",
                "updated_at": br_timestamp(),
                "history": [],
            }

        return jsonify({"ok": True}), 200

    @app.get("/health")
    def healthcheck():
        return jsonify(
            {
                "status": "online",
                "porta": PORT,
                "webhook": WEBHOOK_PATH,
            }
        ), 200

    @app.get("/checkin-location/<token>")
    def location_checkin_page(token: str):
        if token not in LOCATION_LINKS:
            return "Link de localização inválido ou expirado.", 404
        return render_template_string(LOCATION_HTML)

    @app.post("/checkin-location/<token>")
    def receive_location_checkin(token: str):
        link = LOCATION_LINKS.get(token)
        if not link:
            return jsonify({"ok": False, "message": "Link inválido ou expirado."}), 404

        payload = request.get_json(silent=True) or {}
        latitude = payload.get("latitude")
        longitude = payload.get("longitude")
        accuracy = payload.get("accuracy")

        if latitude is None or longitude is None:
            return jsonify({"ok": False, "message": "Localização não recebida."}), 400

        phone = link["phone"]
        address = reverse_geocode(float(latitude), float(longitude))
        maps_url = f"https://www.google.com/maps?q={latitude},{longitude}"
        location_payload = {
            "latitude": latitude,
            "longitude": longitude,
            "accuracy": accuracy,
            "address": address,
            "maps_url": maps_url,
        }

        link["status"] = "received"
        link["location"] = location_payload
        link["received_at"] = br_timestamp()

        state = AGENDA_STATE.setdefault(
            phone,
            {
                "phone": phone,
                "status": "checkin_sent",
                "status_label": checkin_status_label("checkin_sent"),
                "agenda": {},
                "history": [],
            },
        )
        state["status"] = "location_received"
        state["status_label"] = checkin_status_label("location_received")
        state["location"] = location_payload
        state["updated_at"] = link["received_at"]
        state.setdefault("history", []).append(
            {
                "at": link["received_at"],
                "reply": "location_link",
                "to": "location_received",
                "location": location_payload,
            }
        )
        sync_result = None
        try:
            sync_result = sync_checkin_for_phone(phone, "location_received", address or maps_url)
        except (RuntimeError, requests.RequestException, ValueError) as exc:
            logger.exception("Erro ao sincronizar localização/check-in no 77Gestão: %s", exc)

        RECEIVED_EVENTS.appendleft(
            {
                "received_at": link["received_at"],
                "payload": {
                    "type": "LocationLinkCallback",
                    "phone": phone,
                    "location": location_payload,
                    "gestao77_sync": sync_result,
                },
            }
        )

        reply = (
            "Sua localização foi registrada com sucesso.\n"
            f"Local aproximado: {address or maps_url}\n"
            "Obrigado pelas informações."
        )
        try:
            response_payload = send_zapi_text(phone, reply)
            RECEIVED_EVENTS.appendleft(
                {
                    "received_at": br_timestamp(),
                    "payload": {
                        "type": "AutoReply",
                        "phone": phone,
                        "status": "location_received",
                        "text": {"message": reply},
                        "response": response_payload,
                    },
                }
            )
        except (RuntimeError, requests.RequestException, ValueError) as exc:
            logger.exception("Erro ao confirmar localização do link: %s", exc)

        return jsonify({"ok": True, "message": "Localização enviada. Obrigado!"}), 200

    @app.get("/api/events")
    def list_events():
        return jsonify({"events": list(RECEIVED_EVENTS)}), 200

    @app.get("/api/agenda-status")
    def agenda_status():
        phone = normalize_phone(str(request.args.get("phone", "")))
        if not phone:
            return jsonify({"status": "empty", "status_label": "Aguardando envio"}), 200

        state = AGENDA_STATE.get(phone)
        if not state:
            return jsonify({"status": "empty", "status_label": "Aguardando envio"}), 200

        return jsonify(state), 200

    @app.get("/api/terms-status")
    def terms_status():
        phone = normalize_phone(str(request.args.get("phone", "")))
        if not phone:
            return jsonify({"status": "empty", "status_label": "Aceite não enviado"}), 200

        stored_state = terms_state_from_cooperator(phone)
        if stored_state["status"] != "not_started":
            return jsonify(stored_state), 200

        state = TERMS_STATE.get(phone)
        if not state:
            return jsonify({"status": "empty", "status_label": "Aceite não enviado"}), 200

        return jsonify(state), 200

    @app.post("/api/gestao77/pending-bookings")
    def gestao77_pending_bookings():
        payload = request.get_json(silent=True) or {}
        now = br_now()
        month = int(payload.get("month") or request.args.get("month") or now.month)
        year = int(payload.get("year") or request.args.get("year") or now.year)
        body = payload.get("body")
        if body is not None and not isinstance(body, dict):
            return jsonify({"ok": False, "error": "body deve ser um objeto JSON."}), 400

        try:
            bookings = list_pending_partner_bookings(month, year, body)
        except (RuntimeError, requests.RequestException, ValueError) as exc:
            logger.exception("Erro ao consultar escalas pendentes no 77Gestão: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 502

        return jsonify({"ok": True, "month": month, "year": year, "bookings": bookings}), 200

    @app.get("/api/bookings")
    def api_bookings():
        status = request.args.get("status")
        return jsonify({"ok": True, "bookings": list_bookings(status)}), 200

    @app.post("/api/gestao77/retry-pending-syncs")
    def api_retry_pending_syncs():
        result = retry_pending_gestao77_syncs()
        return jsonify(result), 200 if result["ok"] else 207

    @app.post("/api/bookings/<booking_id>/send")
    def api_send_booking(booking_id: str):
        payload = request.get_json(silent=True) or {}
        phone = str(payload.get("phone", "")).strip()
        allowed, gate_message = ensure_operational_access(phone)
        if not allowed:
            return jsonify({"ok": False, "error": gate_message}), 403
        try:
            result = send_booking_to_partner(booking_id, phone)
        except PermissionError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        except (RuntimeError, requests.RequestException, ValueError) as exc:
            logger.exception("Erro ao enviar escala %s: %s", booking_id, exc)
            return jsonify({"ok": False, "error": str(exc)}), 502
        return jsonify({"ok": True, "result": result}), 200

    @app.post("/api/appointments/<appointment_id>/checkout")
    def api_appointment_checkout(appointment_id: str):
        payload = request.get_json(silent=True) or {}
        phone = str(payload.get("phone", "")).strip()
        try:
            result = send_checkout_to_partner(appointment_id, phone)
        except PermissionError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        except (RuntimeError, requests.RequestException, ValueError) as exc:
            logger.exception("Erro ao registrar check-out %s: %s", appointment_id, exc)
            return jsonify({"ok": False, "error": str(exc)}), 502
        return jsonify({"ok": True, "result": result}), 200

    @app.post("/api/appointments/<appointment_id>/send-checkin")
    def api_send_appointment_checkin(appointment_id: str):
        payload = request.get_json(silent=True) or {}
        phone = str(payload.get("phone", "")).strip()
        allowed, gate_message = ensure_operational_access(phone)
        if not allowed:
            return jsonify({"ok": False, "error": gate_message}), 403
        agenda_data = agenda_data_from_payload(payload)
        use_location_link = str(payload.get("mode", "checkin2")).strip().lower() != "checkin"
        try:
            result = send_checkin_to_partner(
                appointment_id,
                phone,
                agenda_data,
                use_location_link,
                booking_id=str(payload.get("booking_id") or ""),
            )
        except PermissionError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        except (RuntimeError, requests.RequestException, ValueError) as exc:
            logger.exception("Erro ao enviar check-in %s: %s", appointment_id, exc)
            return jsonify({"ok": False, "error": str(exc)}), 502
        return jsonify({"ok": True, "result": result}), 200

    @app.post("/api/gestao77/import-and-send-pending")
    def import_and_send_pending():
        payload = request.get_json(silent=True) or {}
        now = br_now()
        month = int(payload.get("month") or now.month)
        year = int(payload.get("year") or now.year)
        phone_by_booking = payload.get("phone_by_booking") or {}
        if not isinstance(phone_by_booking, dict):
            return jsonify({"ok": False, "error": "phone_by_booking deve ser um objeto."}), 400

        try:
            bookings = list_pending_partner_bookings(month, year, payload.get("body"))
            results = []
            for booking in bookings:
                booking_id = str(booking.get("booking_id", ""))
                phone = str(phone_by_booking.get(booking_id) or booking.get("phone") or "")
                if not phone:
                    results.append({"booking_id": booking_id, "ok": False, "error": "Sem telefone para envio."})
                    continue
                allowed, gate_message = ensure_operational_access(phone)
                if not allowed:
                    results.append({"booking_id": booking_id, "ok": False, "error": gate_message})
                    continue
                try:
                    sent = send_booking_to_partner(booking_id, phone)
                    results.append({"booking_id": booking_id, "ok": True, "result": sent})
                except PermissionError as exc:
                    results.append({"booking_id": booking_id, "ok": False, "error": str(exc)})
                except (RuntimeError, requests.RequestException, ValueError) as exc:
                    results.append({"booking_id": booking_id, "ok": False, "error": str(exc)})
        except (RuntimeError, requests.RequestException, ValueError) as exc:
            logger.exception("Erro ao importar/enviar escalas pendentes: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 502

        return jsonify({"ok": True, "month": month, "year": year, "results": results}), 200

    @app.post("/api/send-message")
    def send_message():
        payload = request.get_json(silent=True) or {}
        phone = normalize_phone(str(payload.get("phone", "")))
        message = str(payload.get("message", "")).strip()
        mode = str(payload.get("mode", "text")).strip().lower()
        agenda_data = agenda_data_from_payload(payload)

        if mode == "agenda":
            message = build_agenda_message(agenda_data)
        elif mode == "checkin":
            message = build_checkin_message(agenda_data)
        elif mode == "checkin2":
            message = build_checkin2_message(agenda_data)
        elif mode == "terms":
            message = build_terms_message(agenda_data)

        if not phone:
            return jsonify({"ok": False, "error": "Informe um telefone com DDI e DDD."}), 400

        if len(phone) < 10:
            return jsonify({"ok": False, "error": "Telefone muito curto."}), 400

        if not message:
            return jsonify({"ok": False, "error": "Digite uma mensagem."}), 400

        if mode == "terms" and TERMS_STATE.get(phone, {}).get("status") == "accepted":
            return jsonify({"ok": False, "error": "Este contato já aceitou os termos."}), 409

        if mode in {"agenda", "checkin", "checkin2"}:
            allowed, gate_message = ensure_operational_access(phone)
            if not allowed:
                return jsonify({"ok": False, "error": gate_message}), 403

        try:
            if mode == "agenda":
                response_payload = send_zapi_agenda_buttons(phone, message)
                append_fake_sent_message(phone, mode, message, response_payload)
            elif mode in {"checkin", "checkin2"}:
                response_payload = send_zapi_checkin_options(
                    phone,
                    message,
                    use_location_link=mode == "checkin2",
                )
                append_fake_sent_message(phone, mode, message, response_payload)
            elif mode == "terms":
                response_payload = send_terms_document(phone, message)
                append_fake_sent_message(phone, mode, message, response_payload)
                terms_buttons_payload = send_zapi_terms_buttons(phone)
                append_fake_sent_message(phone, "terms_buttons", build_terms_buttons_message(), terms_buttons_payload)
                TERMS_STATE[phone] = {
                    "phone": phone,
                    "status": "sent",
                    "status_label": "Aguardando aceite",
                    "sent_at": br_timestamp(),
                }
            else:
                response_payload = send_zapi_text(phone, message)
        except (RuntimeError, requests.RequestException, ValueError) as exc:
            logger.exception("Erro ao enviar mensagem pela Z-API: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 502

        RECEIVED_EVENTS.appendleft(
            {
                "received_at": br_timestamp(),
                "payload": {
                    "type": "SentMessage",
                    "phone": phone,
                    "mode": mode,
                    "agenda": agenda_data if mode == "agenda" else {},
                    "text": {"message": message},
                    "response": response_payload,
                },
            }
        )

        if mode in {"agenda", "checkin", "checkin2"}:
            AGENDA_STATE[phone] = {
                "phone": phone,
                "mode": mode,
                "status": "checkin_sent" if mode in {"checkin", "checkin2"} else "pending",
                "status_label": checkin_status_label("checkin_sent") if mode in {"checkin", "checkin2"} else agenda_status_label("pending"),
                "agenda": {
                    **agenda_data,
                    "booking_id": str(payload.get("booking_id") or "") if mode == "agenda" else "",
                    "appointment_id": str(payload.get("appointment_id") or "") if mode in {"checkin", "checkin2"} else "",
                },
                "booking_id": str(payload.get("booking_id") or "") if mode == "agenda" else "",
                "appointment_id": str(payload.get("appointment_id") or "") if mode in {"checkin", "checkin2"} else "",
                "last_reply": "",
                "updated_at": br_timestamp(),
                "history": [],
            }

        return jsonify({"ok": True, "response": response_payload}), 200

    return app


def main() -> None:
    global PUBLIC_BASE_URL
    load_dotenv(encoding="utf-8-sig")

    app = create_app()

    logger.info("Servidor Flask iniciado na porta %s", PORT)
    logger.info("URL local: http://127.0.0.1:%s", PORT)
    logger.info("Webhook local: http://127.0.0.1:%s%s", PORT, WEBHOOK_PATH)

    app.run(host="0.0.0.0", port=PORT, use_reloader=False)

if __name__ == "__main__":
    main()
