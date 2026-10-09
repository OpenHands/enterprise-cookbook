#!/usr/bin/env python3
"""Print the current weather and a 4-hour precipitation forecast for a city.

Uses the free Open-Meteo geocoding and forecast APIs, which need no API key.
Standard library only, so it runs in any sandbox with Python 3.10 or later.

    python3 weather.py Tokyo
    python3 weather.py "New York"
"""

import argparse
import json
import sys
from datetime import datetime
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen


GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_HOURS = 4


def get_json(url: str, params: dict) -> dict:
    with urlopen(f"{url}?{urlencode(params)}", timeout=15) as resp:
        return json.load(resp)


def find_city(name: str) -> dict | None:
    data = get_json(GEOCODING_URL, {"name": name, "count": 1, "format": "json"})
    results = data.get("results") or []
    return results[0] if results else None


def get_forecast(city: dict) -> dict:
    return get_json(
        FORECAST_URL,
        {
            "latitude": city["latitude"],
            "longitude": city["longitude"],
            "timezone": city.get("timezone", "auto"),
            "current": "temperature_2m,precipitation",
            "hourly": "precipitation_probability",
            "forecast_hours": FORECAST_HOURS,
        },
    )


def format_report(city: dict, forecast: dict) -> str:
    place = ", ".join(p for p in (city["name"], city.get("country")) if p)
    current = forecast["current"]
    celsius = current["temperature_2m"]
    fahrenheit = celsius * 9 / 5 + 32
    local_time = datetime.fromisoformat(current["time"]).strftime("%Y-%m-%d %H:%M")
    zone = forecast.get("timezone_abbreviation", "")

    lines = [
        f"City Weather Report for {place}",
        "",
        f"- Current Time: {local_time} {zone}".rstrip(),
        f"- Temperature: {fahrenheit:.0f}°F / {celsius:.0f}°C",
        f"- Current Precipitation: {current['precipitation']:.1f} mm",
        "",
        f"Precipitation Forecast (Next {FORECAST_HOURS} Hours):",
        "| Time  | Probability |",
        "|-------|-------------|",
    ]
    hourly = forecast["hourly"]
    for time, chance in zip(hourly["time"], hourly["precipitation_probability"]):
        hour = datetime.fromisoformat(time).strftime("%H:%M")
        lines.append(f"| {hour} | {chance}% |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print the current weather and forecast for a city."
    )
    parser.add_argument("city", nargs="+", help="City name, e.g. Tokyo")
    city_name = " ".join(parser.parse_args().city)

    try:
        city = find_city(city_name)
        if city is None:
            print(f"No city found named {city_name!r}.", file=sys.stderr)
            return 1
        print(format_report(city, get_forecast(city)))
    except URLError as e:
        print(f"Could not reach Open-Meteo: {e.reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
