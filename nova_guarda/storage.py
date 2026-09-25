import json
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any

from nova_guarda.config import DATABASE_PATH, DATABASE_URL
from nova_guarda.timezone import br_iso_timestamp


def timestamp() -> str:
    return br_iso_timestamp()


def using_postgres() -> bool:
    return DATABASE_URL.startswith(("postgresql://", "postgres://"))


def sql(query: str) -> str:
    """Traduz placeholders `?` (estilo sqlite3) para `%s` (estilo psycopg) quando necessário."""
    return query.replace("?", "%s") if using_postgres() else query


class _PostgresConnection:
    """Encapsula uma conexão psycopg para aceitar o mesmo estilo de chamada
    (`conn.execute(query_com_?, params)`) usado em todo este módulo para SQLite."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, query: str, params: tuple[Any, ...] = ()):
        return self._conn.execute(sql(query), params)

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self._conn.execute(statement)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


@contextmanager
def connect() -> Iterator[Any]:
    if using_postgres():
        import psycopg
        from psycopg.rows import dict_row

        raw_conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        conn = _PostgresConnection(raw_conn)
        try:
            yield conn
            raw_conn.commit()
        except Exception:
            raw_conn.rollback()
            raise
        finally:
            raw_conn.close()
    else:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def insert_returning_id(conn: Any, query: str, params: tuple[Any, ...]) -> int:
    """Executa um INSERT e retorna o id gerado, nos dois dialetos suportados."""
    if using_postgres():
        cursor = conn.execute(query.rstrip().rstrip(";") + " RETURNING id", params)
        return cursor.fetchone()["id"]
    cursor = conn.execute(query, params)
    return cursor.lastrowid


def init_db() -> None:
    # id_pk é a única diferença de dialeto no schema: as demais colunas usam
    # tipos (TEXT/INTEGER) e sintaxe (ON CONFLICT ... DO UPDATE, COALESCE,
    # NULLIF) suportados de forma idêntica por SQLite e Postgres.
    id_pk = "BIGSERIAL PRIMARY KEY" if using_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with connect() as conn:
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS bookings (
                booking_id TEXT PRIMARY KEY,
                partner_id TEXT,
                partner_name TEXT,
                phone TEXT,
                provider TEXT,
                appointment_id TEXT,
                schedule_status TEXT,
                local_status TEXT NOT NULL,
                gestao77_status TEXT,
                whatsapp_message_id TEXT,
                payload_json TEXT NOT NULL,
                sent_at TEXT,
                answered_at TEXT,
                synced_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_events (
                id {id_pk},
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                action TEXT NOT NULL,
                ok INTEGER NOT NULL,
                request_json TEXT NOT NULL,
                response_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS appointments (
                appointment_id TEXT PRIMARY KEY,
                booking_id TEXT NOT NULL,
                phone TEXT NOT NULL,
                provider TEXT,
                local_status TEXT NOT NULL,
                gestao77_status TEXT,
                checkin_message_id TEXT,
                checkout_message_id TEXT,
                inbound_checkin_event_id TEXT,
                inbound_checkout_event_id TEXT,
                inbound_late_event_id TEXT,
                inbound_no_show_event_id TEXT,
                late_minutes INTEGER,
                no_show_reason TEXT,
                payload_json TEXT NOT NULL,
                checked_in_at TEXT,
                checked_out_at TEXT,
                late_reported_at TEXT,
                no_show_reported_at TEXT,
                checkin_synced_at TEXT,
                checkout_synced_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cooperators (
                phone TEXT PRIMARY KEY,
                partner_id TEXT,
                partner_name TEXT,
                partner_payload_json TEXT NOT NULL,
                onboarding_status TEXT NOT NULL,
                terms_sent_at TEXT,
                accepted_at TEXT,
                rejected_at TEXT,
                last_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS configuracoes (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS poller_runs (
                id {id_pk},
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                payload_json TEXT NOT NULL,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS conversation_events (
                id {id_pk},
                received_at TEXT NOT NULL,
                event_type TEXT,
                phone TEXT,
                payload_json TEXT NOT NULL
            );
            """
        )
        ensure_column(conn, "bookings", "provider", "TEXT")
        ensure_column(conn, "bookings", "appointment_id", "TEXT")
        ensure_column(conn, "bookings", "gestao77_status", "TEXT")
        ensure_column(conn, "bookings", "whatsapp_message_id", "TEXT")
        ensure_column(conn, "bookings", "synced_at", "TEXT")
        ensure_column(conn, "appointments", "provider", "TEXT")
        ensure_column(conn, "appointments", "gestao77_status", "TEXT")
        ensure_column(conn, "appointments", "checkin_message_id", "TEXT")
        ensure_column(conn, "appointments", "checkout_message_id", "TEXT")
        ensure_column(conn, "appointments", "inbound_checkin_event_id", "TEXT")
        ensure_column(conn, "appointments", "inbound_checkout_event_id", "TEXT")
        ensure_column(conn, "appointments", "inbound_late_event_id", "TEXT")
        ensure_column(conn, "appointments", "inbound_no_show_event_id", "TEXT")
        ensure_column(conn, "appointments", "late_minutes", "INTEGER")
        ensure_column(conn, "appointments", "no_show_reason", "TEXT")
        ensure_column(conn, "appointments", "checked_in_at", "TEXT")
        ensure_column(conn, "appointments", "checked_out_at", "TEXT")
        ensure_column(conn, "appointments", "late_reported_at", "TEXT")
        ensure_column(conn, "appointments", "no_show_reported_at", "TEXT")
        ensure_column(conn, "appointments", "checkin_synced_at", "TEXT")
        ensure_column(conn, "appointments", "checkout_synced_at", "TEXT")


SETTING_DEFAULTS = {
    "operation_mode": "production",
    "active_provider": "",
    "gestao77_mode": "real",
    "test_phone": "",
    "test_partner_name": "Cooperado Teste",
    "test_booking_id": "test-booking-1",
    "test_appointment_id": "test-appointment-1",
    "automation_enabled": "0",
    "poll_interval_minutes": "5",
    "automation_send_limit": "25",
    "auto_checkin_enabled": "1",
    "checkin_lead_minutes": "120",
    "checkout_after_minutes": "60",
}

EDITABLE_SETTINGS = set(SETTING_DEFAULTS)


def ensure_column(conn: Any, table: str, column: str, definition: str) -> None:
    if using_postgres():
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
        return
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def upsert_booking(member: dict[str, Any], phone: str = "", local_status: str = "pending") -> dict[str, Any]:
    init_db()
    booking_id = str(member.get("booking_id") or member.get("id") or "").strip()
    if not booking_id:
        raise ValueError("Booking sem booking_id.")

    now = timestamp()
    partner_id = str(member.get("id", "")).strip()
    partner_name = str(member.get("name", "")).strip()
    schedule_status = str(member.get("schedule_status", "")).strip()
    phone = phone or extract_phone(member)
    appointment_id = str(
        member.get("today_appointment_id") or member.get("first_appointment_id") or member.get("appointment_id") or ""
    ).strip()

    with connect() as conn:
        existing = conn.execute("SELECT * FROM bookings WHERE booking_id = ?", (booking_id,)).fetchone()
        created_at = existing["created_at"] if existing else now
        local_status = existing["local_status"] if existing and existing["local_status"] != "pending" else local_status
        conn.execute(
            """
            INSERT INTO bookings (
                booking_id, partner_id, partner_name, phone, appointment_id, schedule_status, local_status,
                payload_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(booking_id) DO UPDATE SET
                partner_id = excluded.partner_id,
                partner_name = excluded.partner_name,
                phone = COALESCE(NULLIF(excluded.phone, ''), bookings.phone),
                appointment_id = COALESCE(NULLIF(excluded.appointment_id, ''), bookings.appointment_id),
                schedule_status = excluded.schedule_status,
                local_status = excluded.local_status,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                booking_id,
                partner_id,
                partner_name,
                phone,
                appointment_id,
                schedule_status,
                local_status,
                json.dumps(member, ensure_ascii=False),
                created_at,
                now,
            ),
        )

    return get_booking(booking_id) or {}


def get_booking(booking_id: str | int) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM bookings WHERE booking_id = ?", (str(booking_id),)).fetchone()
    return row_to_booking(row) if row else None


def get_latest_booking_by_phone(phone: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM bookings WHERE phone = ? ORDER BY updated_at DESC LIMIT 1",
            (phone,),
        ).fetchone()
    return row_to_booking(row) if row else None


def list_bookings(status: str | None = None) -> list[dict[str, Any]]:
    init_db()
    query = "SELECT * FROM bookings"
    params: tuple[Any, ...] = ()
    if status:
        query += " WHERE local_status = ?"
        params = (status,)
    query += " ORDER BY updated_at DESC"
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [row_to_booking(row) for row in rows]


def list_bookings_for_checkin() -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM bookings
            WHERE local_status IN ('sent', 'confirmed')
              AND COALESCE(appointment_id, '') != ''
            ORDER BY updated_at ASC
            """
        ).fetchall()
    return [row_to_booking(row) for row in rows]


def update_booking_local_status(booking_id: str | int, status: str) -> None:
    init_db()
    now = timestamp()
    answered_at = now if status in {"confirmed", "declined"} else None
    sent_at = now if status == "sent" else None
    with connect() as conn:
        conn.execute(
            """
            UPDATE bookings
            SET local_status = ?,
                sent_at = COALESCE(?, sent_at),
                answered_at = COALESCE(?, answered_at),
                updated_at = ?
            WHERE booking_id = ?
            """,
            (status, sent_at, answered_at, now, str(booking_id)),
        )


def mark_booking_whatsapp_sent(
    booking_id: str | int,
    phone: str,
    provider: str,
    response_payload: dict[str, Any],
) -> dict[str, Any]:
    init_db()
    booking = get_booking(booking_id)
    if not booking:
        raise ValueError(f"Booking {booking_id} não encontrado.")
    if booking.get("local_status") in {"confirmed", "declined"}:
        return booking

    now = timestamp()
    messages = response_payload.get("messages")
    message_id = response_payload.get("messageId") or response_payload.get("message_id") or response_payload.get("id")
    if not message_id and isinstance(messages, list) and messages and isinstance(messages[0], dict):
        message_id = messages[0].get("id")
    message_id = str(message_id or "").strip()
    with connect() as conn:
        conn.execute(
            """
            UPDATE bookings
            SET local_status = 'sent',
                phone = ?,
                provider = ?,
                whatsapp_message_id = COALESCE(NULLIF(?, ''), whatsapp_message_id),
                sent_at = COALESCE(sent_at, ?),
                updated_at = ?
            WHERE booking_id = ?
            """,
            (phone, provider, message_id, now, now, str(booking_id)),
        )
    return get_booking(booking_id) or {}


def mark_booking_synced(booking_id: str | int, status: str) -> dict[str, Any]:
    init_db()
    now = timestamp()
    with connect() as conn:
        conn.execute(
            """
            UPDATE bookings
            SET gestao77_status = ?,
                synced_at = ?,
                updated_at = ?
            WHERE booking_id = ?
            """,
            (status, now, now, str(booking_id)),
        )
    return get_booking(booking_id) or {}


def transition_booking_response(booking_id: str | int, target_status: str) -> tuple[bool, dict[str, Any]]:
    init_db()
    if target_status not in {"confirmed", "declined"}:
        raise ValueError(f"Status de escala inválido: {target_status}")

    now = timestamp()
    with connect() as conn:
        row = conn.execute("SELECT * FROM bookings WHERE booking_id = ?", (str(booking_id),)).fetchone()
        if not row:
            raise KeyError(f"Booking {booking_id} não encontrado.")
        current = row["local_status"]
        if current == target_status:
            return False, row_to_booking(row)
        if current in {"confirmed", "declined"}:
            raise ValueError(f"Transição inválida: {current} -> {target_status}")
        if current != "sent":
            raise ValueError(f"Transição inválida: {current} -> {target_status}")

        conn.execute(
            """
            UPDATE bookings
            SET local_status = ?,
                answered_at = COALESCE(answered_at, ?),
                updated_at = ?
            WHERE booking_id = ?
            """,
            (target_status, now, now, str(booking_id)),
        )

    return True, get_booking(booking_id) or {}


def save_sync_event(
    entity_type: str,
    entity_id: str | int,
    action: str,
    ok: bool,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sync_events (
                entity_type, entity_id, action, ok, request_json, response_json, error, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_type,
                str(entity_id),
                action,
                1 if ok else 0,
                json.dumps(request_payload, ensure_ascii=False),
                json.dumps(response_payload, ensure_ascii=False) if response_payload is not None else None,
                error,
                timestamp(),
            ),
        )


def upsert_appointment(
    appointment: dict[str, Any],
    booking_id: str | int,
    phone: str,
    local_status: str = "sent",
) -> dict[str, Any]:
    init_db()
    appointment_id = str(appointment.get("id") or appointment.get("appointment_id") or "").strip()
    if not appointment_id:
        raise ValueError("Appointment sem id.")

    existing = get_appointment(appointment_id)
    now = timestamp()
    created_at = existing["created_at"] if existing else now
    local_status = existing["local_status"] if existing and existing["local_status"] != "sent" else local_status

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO appointments (
                appointment_id, booking_id, phone, local_status, payload_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(appointment_id) DO UPDATE SET
                booking_id = excluded.booking_id,
                phone = excluded.phone,
                local_status = excluded.local_status,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                appointment_id,
                str(booking_id),
                phone,
                local_status,
                json.dumps(appointment, ensure_ascii=False),
                created_at,
                now,
            ),
        )
    return get_appointment(appointment_id) or {}


def get_appointment(appointment_id: str | int) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM appointments WHERE appointment_id = ?", (str(appointment_id),)).fetchone()
    return row_to_appointment(row) if row else None


def get_latest_appointment_by_phone(phone: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM appointments WHERE phone = ? ORDER BY updated_at DESC LIMIT 1",
            (phone,),
        ).fetchone()
    return row_to_appointment(row) if row else None


def mark_appointment_checkin_sent(
    appointment_id: str | int,
    provider: str,
    response_payload: dict[str, Any],
) -> dict[str, Any]:
    return update_appointment_message_state(appointment_id, "checkin_pending", provider, response_payload, "checkin_message_id")


def mark_appointment_checkout_sent(
    appointment_id: str | int,
    provider: str,
    response_payload: dict[str, Any],
) -> dict[str, Any]:
    return update_appointment_message_state(appointment_id, "checkout_pending", provider, response_payload, "checkout_message_id")


def update_appointment_message_state(
    appointment_id: str | int,
    status: str,
    provider: str,
    response_payload: dict[str, Any],
    message_column: str,
) -> dict[str, Any]:
    init_db()
    appointment = get_appointment(appointment_id)
    if not appointment:
        raise KeyError(f"Appointment {appointment_id} não encontrado.")
    if appointment.get("local_status") == "checked_out":
        return appointment

    message_id = extract_provider_message_id(response_payload)
    now = timestamp()
    with connect() as conn:
        conn.execute(
            f"""
            UPDATE appointments
            SET local_status = ?,
                provider = ?,
                {message_column} = COALESCE(NULLIF(?, ''), {message_column}),
                updated_at = ?
            WHERE appointment_id = ?
            """,
            (status, provider, message_id, now, str(appointment_id)),
        )
    return get_appointment(appointment_id) or {}


def transition_appointment_checkin(appointment_id: str | int, event_id: str = "") -> tuple[bool, dict[str, Any]]:
    return transition_appointment_presence(
        appointment_id,
        target_status="checked_in",
        allowed_from={"checkin_pending"},
        event_column="inbound_checkin_event_id",
        time_column="checked_in_at",
        event_id=event_id,
    )


def transition_appointment_checkout(appointment_id: str | int, event_id: str = "") -> tuple[bool, dict[str, Any]]:
    return transition_appointment_presence(
        appointment_id,
        target_status="checked_out",
        allowed_from={"checked_in", "checkout_pending"},
        event_column="inbound_checkout_event_id",
        time_column="checked_out_at",
        event_id=event_id,
    )


def transition_appointment_presence(
    appointment_id: str | int,
    target_status: str,
    allowed_from: set[str],
    event_column: str,
    time_column: str,
    event_id: str = "",
) -> tuple[bool, dict[str, Any]]:
    init_db()
    now = timestamp()
    with connect() as conn:
        row = conn.execute("SELECT * FROM appointments WHERE appointment_id = ?", (str(appointment_id),)).fetchone()
        if not row:
            raise KeyError(f"Appointment {appointment_id} não encontrado.")
        current = row["local_status"]
        if current == target_status:
            return False, row_to_appointment(row)
        if target_status == "checked_in" and current == "checked_out":
            raise ValueError("Não é permitido novo check-in depois do check-out.")
        if current not in allowed_from:
            raise ValueError(f"Transição inválida: {current} -> {target_status}")

        conn.execute(
            f"""
            UPDATE appointments
            SET local_status = ?,
                {event_column} = COALESCE(NULLIF(?, ''), {event_column}),
                {time_column} = COALESCE({time_column}, ?),
                updated_at = ?
            WHERE appointment_id = ?
            """,
            (target_status, event_id, now, now, str(appointment_id)),
        )
    return True, get_appointment(appointment_id) or {}


def mark_appointment_synced(appointment_id: str | int, status: str) -> dict[str, Any]:
    init_db()
    now = timestamp()
    sync_column = "checkin_synced_at" if status == "checked_in" else "checkout_synced_at"
    with connect() as conn:
        conn.execute(
            f"""
            UPDATE appointments
            SET gestao77_status = ?,
                {sync_column} = ?,
                updated_at = ?
            WHERE appointment_id = ?
            """,
            (status, now, now, str(appointment_id)),
        )
    return get_appointment(appointment_id) or {}


def mark_appointment_late(appointment_id: str | int, minutes: int, event_id: str = "") -> tuple[bool, dict[str, Any]]:
    init_db()
    now = timestamp()
    with connect() as conn:
        row = conn.execute("SELECT * FROM appointments WHERE appointment_id = ?", (str(appointment_id),)).fetchone()
        if not row:
            raise KeyError(f"Appointment {appointment_id} não encontrado.")
        current = row["local_status"]
        if current in {"checked_in", "checkout_pending", "checked_out", "no_show_reported"}:
            raise ValueError(f"Transição inválida: {current} -> late_reported")
        if current == "late_reported" and row["late_minutes"] == minutes:
            return False, row_to_appointment(row)
        if current not in {"checkin_pending", "late_pending", "late_reported"}:
            raise ValueError(f"Transição inválida: {current} -> late_reported")

        conn.execute(
            """
            UPDATE appointments
            SET local_status = 'late_reported',
                inbound_late_event_id = COALESCE(NULLIF(?, ''), inbound_late_event_id),
                late_minutes = ?,
                late_reported_at = COALESCE(late_reported_at, ?),
                updated_at = ?
            WHERE appointment_id = ?
            """,
            (event_id, minutes, now, now, str(appointment_id)),
        )
    return True, get_appointment(appointment_id) or {}


def mark_appointment_no_show(appointment_id: str | int, reason: str, event_id: str = "") -> tuple[bool, dict[str, Any]]:
    init_db()
    now = timestamp()
    with connect() as conn:
        row = conn.execute("SELECT * FROM appointments WHERE appointment_id = ?", (str(appointment_id),)).fetchone()
        if not row:
            raise KeyError(f"Appointment {appointment_id} não encontrado.")
        current = row["local_status"]
        if current in {"checked_in", "checkout_pending", "checked_out"}:
            raise ValueError(f"Transição inválida: {current} -> no_show_reported")
        if current == "no_show_reported" and row["no_show_reason"] == reason:
            return False, row_to_appointment(row)
        if current not in {"checkin_pending", "no_show_pending", "no_show_reported", "late_reported"}:
            raise ValueError(f"Transição inválida: {current} -> no_show_reported")

        conn.execute(
            """
            UPDATE appointments
            SET local_status = 'no_show_reported',
                inbound_no_show_event_id = COALESCE(NULLIF(?, ''), inbound_no_show_event_id),
                no_show_reason = ?,
                no_show_reported_at = COALESCE(no_show_reported_at, ?),
                updated_at = ?
            WHERE appointment_id = ?
            """,
            (event_id, reason, now, now, str(appointment_id)),
        )
    return True, get_appointment(appointment_id) or {}


def list_pending_booking_syncs() -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM bookings
            WHERE local_status IN ('sent', 'confirmed', 'declined')
              AND COALESCE(gestao77_status, '') != local_status
            ORDER BY updated_at ASC
            """
        ).fetchall()
    return [row_to_booking(row) for row in rows]


def list_pending_appointment_syncs() -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM appointments
            WHERE local_status IN ('checked_in', 'checked_out')
              AND COALESCE(gestao77_status, '') != local_status
            ORDER BY updated_at ASC
            """
        ).fetchall()
    return [row_to_appointment(row) for row in rows]


def extract_provider_message_id(response_payload: dict[str, Any]) -> str:
    messages = response_payload.get("messages")
    message_id = response_payload.get("messageId") or response_payload.get("message_id") or response_payload.get("id")
    if not message_id and isinstance(messages, list) and messages and isinstance(messages[0], dict):
        message_id = messages[0].get("id")
    return str(message_id or "").strip()


def get_cooperator(phone: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM cooperators WHERE phone = ?", (phone,)).fetchone()
    return row_to_cooperator(row) if row else None


def upsert_cooperator(
    phone: str,
    status: str,
    partner: dict[str, Any] | None = None,
    last_message: str = "",
) -> dict[str, Any]:
    init_db()
    existing = get_cooperator(phone)
    partner = partner or existing.get("partner_payload", {}) if existing else partner or {}
    now = timestamp()
    created_at = existing["created_at"] if existing else now
    terms_sent_at = existing.get("terms_sent_at") if existing else None
    accepted_at = existing.get("accepted_at") if existing else None
    rejected_at = existing.get("rejected_at") if existing else None

    if status == "terms_sent":
        terms_sent_at = terms_sent_at or now
    elif status == "accepted":
        accepted_at = accepted_at or now
    elif status == "rejected":
        rejected_at = rejected_at or now

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO cooperators (
                phone, partner_id, partner_name, partner_payload_json, onboarding_status,
                terms_sent_at, accepted_at, rejected_at, last_message, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                partner_id = excluded.partner_id,
                partner_name = excluded.partner_name,
                partner_payload_json = excluded.partner_payload_json,
                onboarding_status = excluded.onboarding_status,
                terms_sent_at = excluded.terms_sent_at,
                accepted_at = excluded.accepted_at,
                rejected_at = excluded.rejected_at,
                last_message = excluded.last_message,
                updated_at = excluded.updated_at
            """,
            (
                phone,
                str(partner.get("id", "")).strip(),
                str(partner.get("name", "")).strip(),
                json.dumps(partner, ensure_ascii=False),
                status,
                terms_sent_at,
                accepted_at,
                rejected_at,
                last_message,
                created_at,
                now,
            ),
        )

    return get_cooperator(phone) or {}


def cooperator_has_accepted_terms(phone: str) -> bool:
    cooperator = get_cooperator(phone)
    return bool(cooperator and cooperator.get("onboarding_status") == "accepted")


def get_setting(key: str) -> str:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT value FROM configuracoes WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else SETTING_DEFAULTS.get(key, "")


def all_settings() -> dict[str, str]:
    init_db()
    settings = dict(SETTING_DEFAULTS)
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM configuracoes").fetchall()
    for row in rows:
        settings[row["key"]] = row["value"]
    return settings


def set_settings(updates: dict[str, str]) -> None:
    init_db()
    now = timestamp()
    with connect() as conn:
        for key, value in updates.items():
            if key not in EDITABLE_SETTINGS:
                continue
            conn.execute(
                """
                INSERT INTO configuracoes (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )


def list_cooperators(status: str | None = None) -> list[dict[str, Any]]:
    init_db()
    query = "SELECT * FROM cooperators"
    params: tuple[Any, ...] = ()
    if status:
        query += " WHERE onboarding_status = ?"
        params = (status,)
    query += " ORDER BY updated_at DESC"
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [row_to_cooperator(row) for row in rows]


def list_appointments(status: str | None = None) -> list[dict[str, Any]]:
    init_db()
    query = "SELECT * FROM appointments"
    params: tuple[Any, ...] = ()
    if status:
        query += " WHERE local_status = ?"
        params = (status,)
    query += " ORDER BY updated_at DESC"
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [row_to_appointment(row) for row in rows]


def list_sync_events(limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sync_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [row_to_sync_event(row) for row in rows]


def save_conversation_event(event: dict[str, Any]) -> dict[str, Any]:
    init_db()
    payload = event.get("payload") or {}
    received_at = str(event.get("received_at") or timestamp())
    event_type = str(payload.get("type") or "")
    phone = str(payload.get("phone") or "")
    with connect() as conn:
        new_id = insert_returning_id(
            conn,
            """
            INSERT INTO conversation_events (received_at, event_type, phone, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (received_at, event_type, phone, json.dumps(payload, ensure_ascii=False, default=str)),
        )
        row = conn.execute("SELECT * FROM conversation_events WHERE id = ?", (new_id,)).fetchone()
    return row_to_conversation_event(row)


def list_conversation_events(limit: int = 120) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM conversation_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [row_to_conversation_event(row) for row in rows]


def dashboard_metrics() -> dict[str, Any]:
    init_db()
    with connect() as conn:
        cooperators = conn.execute(
            "SELECT onboarding_status status, COUNT(*) total FROM cooperators GROUP BY onboarding_status"
        ).fetchall()
        bookings = conn.execute(
            "SELECT local_status status, COUNT(*) total FROM bookings GROUP BY local_status"
        ).fetchall()
        appointments = conn.execute(
            "SELECT local_status status, COUNT(*) total FROM appointments GROUP BY local_status"
        ).fetchall()
        pending_booking_syncs = conn.execute(
            """
            SELECT COUNT(*) total FROM bookings
            WHERE local_status IN ('sent', 'confirmed', 'declined')
              AND COALESCE(gestao77_status, '') != local_status
            """
        ).fetchone()
        pending_appointment_syncs = conn.execute(
            """
            SELECT COUNT(*) total FROM appointments
            WHERE local_status IN ('checked_in', 'checked_out')
              AND COALESCE(gestao77_status, '') != local_status
            """
        ).fetchone()
        failed_syncs = conn.execute("SELECT COUNT(*) total FROM sync_events WHERE ok = 0").fetchone()
        last_poller_run = conn.execute(
            "SELECT * FROM poller_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return {
        "cooperators": {row["status"]: row["total"] for row in cooperators},
        "bookings": {row["status"]: row["total"] for row in bookings},
        "appointments": {row["status"]: row["total"] for row in appointments},
        "pending_syncs": (pending_booking_syncs["total"] if pending_booking_syncs else 0)
        + (pending_appointment_syncs["total"] if pending_appointment_syncs else 0),
        "failed_syncs": failed_syncs["total"] if failed_syncs else 0,
        "last_poller_run": row_to_poller_run(last_poller_run) if last_poller_run else None,
    }


def save_poller_run(status: str, payload: dict[str, Any], error: str = "", started_at: str | None = None) -> dict[str, Any]:
    init_db()
    started = started_at or timestamp()
    finished = timestamp()
    with connect() as conn:
        run_id = insert_returning_id(
            conn,
            """
            INSERT INTO poller_runs (status, started_at, finished_at, payload_json, error)
            VALUES (?, ?, ?, ?, ?)
            """,
            (status, started, finished, json.dumps(payload, ensure_ascii=False, default=str), error),
        )
        row = conn.execute("SELECT * FROM poller_runs WHERE id = ?", (run_id,)).fetchone()
    return row_to_poller_run(row)


def list_poller_runs(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM poller_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [row_to_poller_run(row) for row in rows]


def extract_phone(member: dict[str, Any]) -> str:
    for key in ("phone", "mobile", "cellphone", "whatsapp", "telephone", "celular", "telefone"):
        value = member.get(key)
        if value:
            return "".join(char for char in str(value) if char.isdigit())

    phones = member.get("phones")
    if isinstance(phones, list):
        for phone in phones:
            if not isinstance(phone, dict):
                continue
            number = phone.get("number")
            if not number:
                continue
            country_code = "".join(char for char in str(phone.get("country_code", "")) if char.isdigit())
            digits = "".join(char for char in str(number) if char.isdigit())
            return f"{country_code}{digits}" if country_code and not digits.startswith(country_code) else digits
    return ""


def row_to_booking(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["payload"] = json.loads(data.pop("payload_json") or "{}")
    return data


def row_to_cooperator(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["partner_payload"] = json.loads(data.pop("partner_payload_json") or "{}")
    return data


def row_to_appointment(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["payload"] = json.loads(data.pop("payload_json") or "{}")
    return data


def row_to_sync_event(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["request"] = json.loads(data.pop("request_json") or "{}")
    response_json = data.pop("response_json")
    data["response"] = json.loads(response_json or "{}") if response_json else {}
    return data


def row_to_poller_run(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["payload"] = json.loads(data.pop("payload_json") or "{}")
    return data


def row_to_conversation_event(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["payload"] = json.loads(data.pop("payload_json") or "{}")
    return data
