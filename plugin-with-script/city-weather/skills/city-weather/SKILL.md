---
name: city-weather
description: Report the current weather and a 4-hour precipitation forecast for a city by running the bundled weather.py script.
triggers:
  - weather in
  - weather for
  - forecast for
---

# City Weather

Use this skill when the user asks about the weather in a city. Get the report by
running the bundled script rather than calling weather APIs yourself.

## Instructions

1. Take the city from the user's message. If they didn't name one, ask which
   city they want and wait for their answer.
2. Run `scripts/weather.py`, which is relative to this skill's directory, with
   the city as its argument:

   ```bash
   python3 <skill-directory>/scripts/weather.py "<city>"
   ```

3. Show the script's output to the user exactly as printed. If it exits with an
   error, show the error and stop.

## Files

- `scripts/weather.py`: looks up the city with the Open-Meteo geocoding API,
  fetches the current conditions and hourly forecast, and prints the report.
  Python 3.10 or later, standard library only, no API key.
