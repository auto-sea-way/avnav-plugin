// "Route to here": a route over water from the boat to the crosshair, made by asw and saved after the warning.
const TITLE = "Route to here";
const WARNING = "This route only goes around land. It does not know water depth, rocks or traffic rules. " +
    "Check every point on the chart before you use it.";
const ON_LAND = {
    Start: "The start is on land. Check the GPS position.",
    Destination: "The destination is on land. Move the crosshair onto the water and make the route again."
};
const DEFAULT_OPTIONS = {shore_buffer: 0, canals: true};

export default (api) => {
    const {Htm, React} = api.modules();
    const {useState, useEffect} = React;

    const base = api.getBaseUrl().replace(/\/$/, ""); // AvNav 20261029 gives it with a trailing slash
    // throws an Error with a message for the sailor; ctl lets the caller abort the request
    const call = async (cmd, params = {}, seconds = 15, ctl = new AbortController()) => {
        const timer = setTimeout(() => ctl.abort(), seconds * 1000);
        let r;
        try {
            r = await fetch(`${base}/api/${cmd}?${new URLSearchParams(params)}`, {signal: ctl.signal});
        } catch (e) {
            throw new Error(ctl.signal.aborted ? `The asw plugin did not answer in ${seconds} seconds.`
                : `The asw plugin does not answer: ${e.message}`);
        } finally {
            clearTimeout(timer);
        }
        if (!r.ok) throw new Error(`The asw plugin does not answer: HTTP ${r.status}`);
        return r.json();
    };
    // ctx: the click event; an event from a dialog button opens the new dialog on top of that dialog
    const say = (text, ctx, buttons = [{name: "ok", shortText: "OK"}]) =>
        api.showDialog({title: TITLE, text, buttons}, ctx);
    // the map can report a longitude past 180 after panning; keep others as they are (no float noise)
    const lon180 = (lon) => (lon >= -180 && lon <= 180 ? lon : (((lon + 180) % 360) + 360) % 360 - 180);
    let busy = false;

    // the boat position and the crosshair now, or the reason why there is none
    const ends = () => {
        const lat = api.getStoreData("nav.gps.lat"), lon = api.getStoreData("nav.gps.lon");
        if (!api.getStoreData("nav.gps.valid") || typeof lat !== "number" || typeof lon !== "number") {
            return "There is no GPS fix. The route starts at the boat position, so it needs a GPS fix.";
        }
        const center = api.getStoreData("map.centerPosition");
        if (!center || typeof center.lat !== "number" || typeof center.lon !== "number") {
            return "The chart has no crosshair position yet. Move the chart and try again.";
        }
        return {from: {lat, lon: lon180(lon)}, to: {lat: center.lat, lon: lon180(center.lon)}};
    };

    // the status line of the download and the start of asw; next(status) when it is ready or stopped
    const Progress = ({first, next}) => {
        const [s, setS] = useState(first);
        useEffect(() => {
            let stop = false, timer;
            const tick = async () => {
                const n = await call("status").catch((e) => ({state: "error", detail: e.message}));
                if (stop) return;
                setS(n);
                if (n.state === "downloading" || n.state === "starting") timer = setTimeout(tick, 1000);
                else next(n);
            };
            timer = setTimeout(tick, 1000);
            return () => {
                stop = true;
                clearTimeout(timer);
            };
        }, []);
        return Htm`<p>${s.detail}</p>`;
    };

    // the sailor pressed To here to get a route, so make it when asw is ready
    const progress = async (first, ctx) => {
        let open = true, close = () => {};
        const next = (n) => {
            if (!open) return;
            open = false;
            close();
            n.state === "ready" ? go(ctx) : notReady(n, ctx);
        };
        close = await api.showDialog({
            title: TITLE, html: Htm`<${Progress} first=${first} next=${next}/>`,
            buttons: [{name: "cancel", shortText: "Close"}],
            onClose: () => (open = false)
        }, ctx);
    };

    const notReady = (s, ctx) => {
        if (s.state !== "missing" && s.state !== "error") return progress(s, ctx);
        // The status text tells the way to this dialog; inside it, say only the size.
        const text = s.state === "missing" ? "Map data is not installed. The download is about 540 MB, use Wi-Fi." : s.detail;
        return say(text, ctx, [
            {name: "cancel", shortText: "Cancel"},
            {
                name: "download", shortText: s.state === "missing" ? "Download map data" : "Try again",
                onClick: async () => {
                    try {
                        progress(await call("download"), ctx);
                    } catch (e) {
                        say(e.message, ctx);
                    }
                }
            }
        ]);
    };

    const optionsButton = (options, ctx) => ({name: "options", shortText: "Options", onClick: () => askOptions(options, ctx)});

    const result = (r, options, ctx) => {
        let saving = false;
        return api.showDialog({
        title: TITLE,
        html: Htm`<div className="aswResult">
            <p>Distance: <b>${r.distance_nm.toFixed(1)} nm</b></p>
            <p>Options: ${options.shore_buffer} nm from shore, canals ${options.canals ? "allowed" : "avoided"}</p>
            ${r.land.map((n) => Htm`<p className="aswLand">${ON_LAND[n] || n}</p>`)}
            <p className="aswWarning">${WARNING}</p>
        </div>`,
        parameters: [{name: "name", displayName: "Route name", type: "STRING"}],
        values: {name: r.name},
        buttons: [
            {name: "cancel", shortText: "Cancel"},
            optionsButton(options, ctx),
            {
                name: "ok", shortText: "Save route", close: false,
                onClick: async (ev, values, close) => {
                    const name = (values.name || "").trim();
                    const save = async (overwrite) => {
                        if (saving) return; // a second press while the first save runs
                        saving = true;
                        let s;
                        try {
                            s = await call("save", {id: r.id, name, overwrite: overwrite ? 1 : 0});
                        } catch (e) {
                            return say(e.message, ev);
                        } finally {
                            saving = false;
                        }
                        if (s.exists) {
                            return say(`A route with the name "${name}" exists. Replace it?`, ev, [
                                {name: "cancel", shortText: "Cancel"},
                                {name: "ok", shortText: "Replace", onClick: () => save(true)}
                            ]);
                        }
                        if (!s.ok) return say(s.error, ev);
                        await close();
                        const saved = `The route "${s.name}" is saved. Open it on the Routes page.`;
                        if (!api.editRoute) return api.showToast(saved, 5000); // AvNav without editRoute
                        // the route editor on the chart; ctx is the To here click, a dialog button event has no page history
                        api.editRoute(s.name, ctx).catch((e) => api.showToast(`${saved} ${e.message}`, 8000));
                    };
                    save(false);
                }
            }
        ]
        }, ctx);
    };

    // close: the options dialog when the options come from it; it stays open if the route fails
    const makeRoute = async (options, ev, ctx, close) => {
        const e = ends(); // the boat moves, and the chart can move during a download: read both ends now
        if (typeof e === "string") return say(e, ev);
        if (busy) return say("A route is still being made. Wait for it.", ev);
        busy = true;
        const ctl = new AbortController();
        let finished = false, cancelled = false, closeWait = () => {};
        try {
            closeWait = await api.showDialog({
                title: TITLE, text: "Making the route. This can take some seconds.",
                buttons: [{name: "cancel", shortText: "Cancel"}],
                onClose: () => {
                    if (finished) return;
                    cancelled = true; // the sailor cancelled: stop waiting and drop the late result
                    ctl.abort();
                }
            }, ev);
            const r = await call("route", {data: JSON.stringify({...e, ...options})}, 120, ctl);
            finished = true;
            closeWait();
            if (cancelled) return;
            if (!r.ok) return close ? say(r.error, ev) : say(r.error, ev, [{name: "ok", shortText: "OK"}, optionsButton(options, ctx)]);
            if (close) await close();
            result(r, options, ctx);
        } catch (err) {
            finished = true;
            closeWait();
            if (!cancelled) say(err.message, ev);
        } finally {
            busy = false;
        }
    };

    const askOptions = (options, ctx) => api.showDialog({
        title: TITLE,
        parameters: [
            {name: "shore_buffer", displayName: "Distance from shore (nm)", type: "FLOAT", list: [0, 5]},
            {name: "avoid_canals", displayName: "Avoid canals", type: "BOOLEAN"}
        ],
        values: {shore_buffer: options.shore_buffer, avoid_canals: !options.canals},
        buttons: [
            {name: "cancel", shortText: "Cancel"},
            {
                name: "ok", shortText: "Make route", close: false,
                onClick: (ev, values, close) => {
                    const sb = parseFloat(values.shore_buffer);
                    if (!(sb >= 0 && sb <= 5)) return say("Distance from shore must be 0 to 5 nm.", ev);
                    const o = {shore_buffer: sb, canals: !values.avoid_canals};
                    call("options", {data: JSON.stringify(o)}).catch(() => {}); // only remembers the options
                    makeRoute(o, ev, ctx, close);
                }
            }
        ]
    }, ctx);

    // To here: the route at once with the options from last time
    const go = async (ctx) => {
        let status;
        try {
            status = await call("status");
        } catch (e) {
            return say(e.message, ctx);
        }
        if (status.state !== "ready") return notReady(status, ctx);
        const o = await call("options").catch(() => DEFAULT_OPTIONS);
        makeRoute("shore_buffer" in o ? {shore_buffer: o.shore_buffer, canals: o.canals} : DEFAULT_OPTIONS, ctx, ctx);
    };

    api.registerUserButton({
        name: "aswRouteHere",
        shortText: "To here",
        longText: "Route to here",
        onClick: (ev) => go(ev)
    }, "navpage");
};
