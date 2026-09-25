import logging

import requests


logger = logging.getLogger(__name__)


class GeocodingClient:
    def reverse_geocode(self, latitude: float, longitude: float) -> str:
        try:
            response = requests.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={
                    "lat": latitude,
                    "lon": longitude,
                    "format": "jsonv2",
                    "zoom": 18,
                    "addressdetails": 1,
                },
                headers={"User-Agent": "NovaGuardaCheckin/1.0"},
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
            return str(payload.get("display_name", ""))
        except requests.RequestException as exc:
            logger.warning("Não foi possível obter endereço aproximado: %s", exc)
            return ""
