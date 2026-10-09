---
argument-hint: <city>
description: Report the current weather and precipitation forecast for a city
---

# City Weather Report

Report the weather for the city named in **$ARGUMENTS** by running the script
bundled with this plugin. Don't call the weather APIs yourself.

## Instructions

1. Read the city from the arguments: **$ARGUMENTS**. If it is empty, ask the
   user which city they want and wait for their answer.
2. Find the script. The skill location given above is this file,
   `<plugin>/commands/now.md`. The script is in the same plugin at
   `<plugin>/skills/city-weather/scripts/weather.py`.
3. Run it with the city as its argument, quoted:

   ```bash
   python3 <plugin>/skills/city-weather/scripts/weather.py "<city>"
   ```

4. Show the script's output to the user exactly as printed. If it exits with an
   error, show the error and stop.

## Examples

`/city-weather:now Tokyo`
`/city-weather:now New York`
