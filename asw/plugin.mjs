// "Route to here": a route over water from the boat to the crosshair, made by asw and saved after the warning.
const TITLE = "Route to here";
const WARNING = "This route only goes around land. It does not know water depth, rocks or traffic rules. " +
    "Check every point on the chart before you use it.";
const ON_LAND = {Start: "The start is on land.", Destination: "The destination is on land."};

export default (api) => {
    const {Htm} = api.modules();

    const base = api.getBaseUrl().replace(/\/$/, ""); // AvNav 20261029 gives it with a trailing slash
    const call = async (cmd, params = {}) => {
        const r = await fetch(`${base}/api/${cmd}?${new URLSearchParams(params)}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    };
    // ctx: the click event; an event from a dialog button opens the new dialog on top of that dialog
    const say = (text, ctx, buttons = [{name: "ok", shortText: "OK"}]) =>
        api.showDialog({title: TITLE, text, buttons}, ctx);
    const lon180 = (lon) => ((lon + 540) % 360) - 180; // the map can report a longitude past 180 after panning
    let busy = false;

    const notReady = (s, ctx) => {
        if (s.state !== "missing" && s.state !== "error") return say(s.detail, ctx);
        return say(s.detail, ctx, [
            {name: "cancel", shortText: "Cancel"},
            {
                name: "download", shortText: s.state === "missing" ? "Download map data" : "Try again",
                onClick: async () => {
                    try {
                        say((await call("download")).detail, ctx);
                    } catch (e) {
                        say(`The asw plugin does not answer: ${e.message}`, ctx);
                    }
                }
            }
        ]);
    };

    const result = (r, ctx) => api.showDialog({
        title: TITLE,
        html: Htm`<div className="aswResult">
            <p>Distance: <b>${r.distance_nm.toFixed(1)} nm</b></p>
            ${r.land.map((n) => Htm`<p className="aswLand">${ON_LAND[n] || n}</p>`)}
            <p className="aswWarning">${WARNING}</p>
        </div>`,
        parameters: [{name: "name", displayName: "Route name", type: "STRING"}],
        values: {name: r.name},
        buttons: [
            {name: "cancel", shortText: "Cancel"},
            {
                name: "ok", shortText: "Save route", close: false,
                onClick: async (ev, values, close) => {
                    const name = (values.name || "").trim();
                    const save = async (overwrite) => {
                        let s;
                        try {
                            s = await call("save", {id: r.id, name, overwrite: overwrite ? 1 : 0});
                        } catch (e) {
                            return say(`The asw plugin does not answer: ${e.message}`, ev);
                        }
                        if (s.exists) {
                            return say(`A route with the name "${name}" exists. Replace it?`, ev, [
                                {name: "cancel", shortText: "Cancel"},
                                {name: "ok", shortText: "Replace", onClick: () => save(true)}
                            ]);
                        }
                        if (!s.ok) return say(s.error, ev);
                        await close();
                        say(`The route "${s.name}" is saved. You find it on the Routes page.`, ctx);
                    };
                    save(false);
                }
            }
        ]
    }, ctx);

    const makeRoute = async (ev, values, close, ctx, from, to) => {
        const sb = parseFloat(values.shore_buffer);
        if (!(sb >= 0 && sb <= 5)) return say("Distance from shore must be 0 to 5 nm.", ev);
        const options = {shore_buffer: sb, canals: !values.avoid_canals, arctic: !!values.arctic};
        if (busy) return;
        busy = true;
        let closeWait = () => {};
        try {
            closeWait = await api.showDialog({title: TITLE, text: "Making the route. This can take some seconds."}, ev);
            call("options", {data: JSON.stringify(options)}).catch(() => {}); // only remembers the options
            const r = await call("route", {data: JSON.stringify({from, to, ...options})});
            closeWait();
            if (!r.ok) return say(r.error, ev);
            await close();
            result(r, ctx);
        } catch (e) {
            closeWait();
            say(`The asw plugin does not answer: ${e.message}`, ev);
        } finally {
            busy = false;
        }
    };

    const start = async (ctx) => {
        let status, options;
        try {
            [status, options] = await Promise.all([call("status"), call("options")]);
        } catch (e) {
            return say(`The asw plugin does not answer: ${e.message}`, ctx);
        }
        if (status.state !== "ready") return notReady(status, ctx);
        const lat = api.getStoreData("nav.gps.lat"), lon = api.getStoreData("nav.gps.lon");
        if (!api.getStoreData("nav.gps.valid") || typeof lat !== "number" || typeof lon !== "number") {
            return say("There is no GPS fix. The route starts at the boat position, so it needs a GPS fix.", ctx);
        }
        const center = api.getStoreData("map.centerPosition");
        if (!center || typeof center.lat !== "number" || typeof center.lon !== "number") {
            return say("The chart has no crosshair position yet. Move the chart and try again.", ctx);
        }
        const from = {lat, lon: lon180(lon)}, to = {lat: center.lat, lon: lon180(center.lon)};
        api.showDialog({
            title: TITLE,
            text: "A route over water from the boat to the crosshair.",
            parameters: [
                {name: "shore_buffer", displayName: "Distance from shore (nm)", type: "FLOAT", list: [0, 5]},
                {name: "avoid_canals", displayName: "Avoid canals", type: "BOOLEAN"},
                {name: "arctic", displayName: "Allow Arctic", type: "BOOLEAN"}
            ],
            values: {shore_buffer: options.shore_buffer, avoid_canals: !options.canals, arctic: options.arctic},
            buttons: [
                {name: "cancel", shortText: "Cancel"},
                {
                    name: "ok", shortText: "Make route", close: false,
                    onClick: (ev, values, close) => makeRoute(ev, values, close, ctx, from, to)
                }
            ]
        }, ctx);
    };

    api.registerUserButton({
        name: "aswRouteHere",
        shortText: "To here",
        longText: "Route to here",
        onClick: (ev) => start(ev)
    }, "navpage");
};
