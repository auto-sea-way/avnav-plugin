<!-- Draft for avnav#435. Not posted. Post it only after a Pi test and after the plugin repo is public. -->

Hello,

I made a plugin for this: https://github.com/auto-sea-way/avnav-plugin

It adds a "Route to here" button to the chart page. You put the crosshair on the destination and press the button. The plugin makes a route over water from the boat position to the crosshair that goes around land, shows the distance and a warning, and saves it as a normal AvNav route. The routing is done by [auto-sea-way](https://github.com/auto-sea-way/asw), an open source sea router that runs on the same device as AvNav, so it works offline after the map data is downloaded once (about 540 MB, only when the sailor presses a button).

The limits: it gives the shortest route over water. It is not weather routing, there are no polars, wind or currents. It does not know depth, rocks or traffic rules, so the sailor must check every point. A saved route is like any other route, AvNav will follow it and send it to an autopilot. The warning before saving is the only guard.

It needs Linux arm64 or x86_64 (Raspberry Pi 4 or 5 with a 64-bit OS), 2 GB RAM or more, and about 2.1 GB free disk. I have not tested it on a real Pi yet. If you or someone else can test it, I would be glad to hear the result.

If something in the plugin should work in another way, or if you want it in the plugin list, please tell me and I will change it.
