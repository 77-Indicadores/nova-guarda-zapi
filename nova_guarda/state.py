from collections import deque
from typing import Any


RECEIVED_EVENTS: deque[dict[str, Any]] = deque(maxlen=50)
AGENDA_STATE: dict[str, dict[str, Any]] = {}
TERMS_STATE: dict[str, dict[str, Any]] = {}
LOCATION_LINKS: dict[str, dict[str, Any]] = {}
