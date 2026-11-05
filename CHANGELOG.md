# Changelog

## 0.1.1 (2026-11-05)

- New installs download asw 0.11.1. Its routes are shorter, because they no longer turn in open water far from any land: Las Palmas to Mindelo at 0.2 nm from shore is 865.2 nm (was 912.8 nm). The map data is the same.

## 0.1.0 (2026-11-04)

- "Route to here" button on the AvNav chart page: one press makes a route over water from the boat to the crosshair with the options from last time. The route is saved after a warning.
- Options: distance from shore and avoid canals. They are stored as plugin parameters, so you can also change them in the settings of the plugin in AvNav.
- Downloads the asw program and the compressed map data (asw 0.11.0) when you press the button, shows the progress, and makes the route when the map data is ready.
- The distance is measured as AvNav measures it, so both show the same number.
- After saving, the route opens in the route editor on the chart if AvNav has the plugin function `editRoute`.
