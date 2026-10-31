# AvNav plugin for auto-sea-way

This plugin adds a "Route to here" button to the chart page of [AvNav](https://github.com/wellenvogel/avnav). Put the crosshair on your destination and press the button. The plugin asks [auto-sea-way](https://github.com/auto-sea-way/asw) (asw) for a route over water from the boat to the crosshair that goes around land, shows the distance, and saves the route after you have read a warning. asw runs on the same device as AvNav, so it works without internet at sea. Internet is needed once, to download the map data.

The idea comes from [avnav#435](https://github.com/wellenvogel/avnav/issues/435).

## Read this first

- The route is the shortest way over water that avoids land. It ignores polars, wind and currents, so it is not a weather router.
- asw does not know water depth, rocks, tides or traffic rules. Check every point of the route on the chart. This plugin is not for navigation.
- A saved route is a normal AvNav route. AvNav follows it and sends it to an autopilot like any other route. The warning before saving is the only guard, so check the route before you activate it.

## Requirements

- AvNav 20261029 daily build or the coming 202610xx release.
- Linux, arm64 or x86_64. For a Raspberry Pi this means Pi 4 or 5 with a 64-bit OS.
- 2 GB RAM or more. With less RAM the plugin works but routing is slow.
- About 2.1 GB free disk space. The download is about 540 MB and it unpacks to about 1.43 GB.
- The plugin downloads asw 0.11.0.

## Install

1. Download `avnav-asw-<version>.zip` from the releases page.
2. In AvNav open the Plugins page and upload the zip.
3. Restart AvNav if it asks for it. The plugin status says that the map data is missing.
4. Connect to Wi-Fi, open the chart page, press "To here" and then "Download map data". The plugin downloads the asw program and the map data and shows the progress. When the map data is ready, it makes the route. The download starts only when you press the button.

The files are stored in the `asw` folder of the AvNav data directory, so they stay when you update the plugin.

## Use

1. Move the chart so that the crosshair is on the destination.
2. Press "To here". The boat position from GPS is the start. Without a GPS fix the plugin cannot make a route.
3. Read the result: the distance in nautical miles, the options, a note if the start or the destination is on land, and the warning.
4. To change the distance from shore (0 to 5 nm) or to avoid canals, press "Options" and then "Make route". The plugin remembers the options for the next route.
5. Change the route name if you want, and press "Save route". If a route with this name exists, the plugin asks before it replaces it.
6. Open the Routes page, check the route on the chart, and use it like any other route.

![Chart page](docs/img/chart-page.png)

![Result dialog](docs/img/result-dialog.png)

![Saved route](docs/img/saved-route.png)

## Build

`./build.sh` writes `dist/avnav-asw-<version>.zip`. `python3 tests/selfcheck.py` runs the checks.

## License

MIT or Apache 2.0, at your option.
