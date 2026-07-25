from __future__ import annotations

from langchain_core.tools import tool

from agent.infra.http import fetch_json, tool_json

# Codes météo WMO — https://open-meteo.com/en/docs
WMO_CODES = {
    0: "ciel dégagé",
    1: "plutôt dégagé",
    2: "partiellement nuageux",
    3: "couvert",
    45: "brouillard",
    48: "brouillard givrant",
    51: "bruine légère",
    53: "bruine modérée",
    55: "bruine dense",
    61: "pluie faible",
    63: "pluie modérée",
    65: "pluie forte",
    71: "neige faible",
    73: "neige modérée",
    75: "neige forte",
    80: "averses faibles",
    81: "averses modérées",
    82: "averses violentes",
    95: "orage",
    96: "orage avec grêle",
    99: "orage violent avec grêle",
}


def describe(code: int | None) -> str:
    return WMO_CODES.get(code, f"code météo {code}")


@tool(parse_docstring=True)
async def weather_forecast(city: str) -> str:
    """Donne la météo actuelle et les prévisions sur 3 jours pour une ville.

    À utiliser dès qu'une question porte sur le temps qu'il fait ou qu'il va faire.

    Args:
        city: Nom de la ville, ex. 'Paris' ou 'Lyon, France'.
    """

    async def run() -> dict:
        geo = await fetch_json(
            "https://geocoding-api.open-meteo.com/v1/search",
            {"name": city, "count": 1, "language": "fr"},
        )
        results = geo.get("results") or []
        if not results:
            raise RuntimeError(f'Ville introuvable : "{city}"')
        place = results[0]

        data = await fetch_json(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "forecast_days": 3,
                "timezone": "auto",
            },
        )
        current, daily = data["current"], data["daily"]

        location = ", ".join(
            part for part in (place.get("name"), place.get("admin1"), place.get("country")) if part
        )

        return {
            "location": location,
            "current": {
                "temperature": f"{current['temperature_2m']}°C",
                "feelsLike": f"{current['apparent_temperature']}°C",
                "wind": f"{current['wind_speed_10m']} km/h",
                "conditions": describe(current["weather_code"]),
            },
            "forecast": [
                {
                    "date": date,
                    "min": f"{daily['temperature_2m_min'][i]}°C",
                    "max": f"{daily['temperature_2m_max'][i]}°C",
                    "conditions": describe(daily["weather_code"][i]),
                }
                for i, date in enumerate(daily["time"])
            ],
        }

    return await tool_json(run)
