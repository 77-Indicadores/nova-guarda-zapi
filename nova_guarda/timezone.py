from datetime import datetime
from zoneinfo import ZoneInfo


BR_TZ = ZoneInfo("America/Sao_Paulo")


def br_now() -> datetime:
    return datetime.now(BR_TZ)


def br_timestamp() -> str:
    return br_now().strftime("%d/%m/%Y %H:%M:%S")


def br_iso_timestamp() -> str:
    return br_now().isoformat(timespec="seconds")
