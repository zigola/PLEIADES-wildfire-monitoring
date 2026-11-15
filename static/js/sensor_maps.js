/* PLEIADES: three synchronized maps, sourced only from the local detections API. */
(function () {
    "use strict";
    var root = document.getElementById("sat-app");
    if (!root || root.dataset.page !== "map") return;

    var endpoint = root.dataset.detectionsUrl;
    if (!window.L) {
        ["modis-only-count", "viirs-only-count", "sensor-comparison-count"].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.textContent = "Map library could not be loaded. Check the internet connection and refresh.";
        });
        return;
    }
    var specs = [
        {id: "modis-only-map", count: "modis-only-count", sensor: "MODIS", title: "MODIS"},
        {id: "viirs-only-map", count: "viirs-only-count", sensor: "VIIRS", title: "VIIRS"},
        {id: "sensor-comparison-map", count: "sensor-comparison-count", sensor: "ALL", title: "MODIS + VIIRS"}
    ];
    var maps = {};
    var layers = {};
    var renderers = {};
    var colors = {MODIS: "#3989d6", VIIRS: "#e68a32"};

    function params(sensor) {
        /* Same filters as the rest of the page (including per-sensor confidence). */
        return window.SAT.params(sensor === "ALL" ? {format: "geojson"} : {sensor: sensor, format: "geojson"});
    }

    function legacyPopup(p) {
        var rows = [
            ["Sensor", p.sensor], ["Satellite", p.satellite], ["Product", p.product],
            ["Date", p.date], ["UTC", p.time_utc], ["FRP (MW)", p.frp_mw],
            ["Confidence", p.confidence_raw], ["Day/night", p.daynight],
            ["Brightness (K)", p.brightness_k], ["Pixel scan (km)", p.scan_km],
            ["Pixel track (km)", p.track_km], ["Source category", p.category]
        ].filter(function (r) { return r[1] !== undefined && r[1] !== null && r[1] !== ""; });
        return rows.map(function (r) {
            return "<b>" + String(r[0]).replace(/[&<>]/g, "") + ":</b> " +
                String(r[1]).replace(/[&<>]/g, "") ;
        }).join("<br>");
    }

    function popup(p) { return window.SAT && window.SAT.popupHtml ? window.SAT.popupHtml(p) : legacyPopup(p); }

    function init(spec) {
        var el = document.getElementById(spec.id);
        if (!el) return;
        var map = L.map(el, {preferCanvas: true, worldCopyJump: true}).setView([30.5, -7.5], 5);
        L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", {
            maxZoom: 19,
            attribution: "Tiles &copy; Esri"
        }).addTo(map);
        var renderer = L.canvas();
        renderer.addTo(map);
        maps[spec.id] = map;
        layers[spec.id] = L.layerGroup().addTo(map);
        renderers[spec.id] = renderer;
    }

    function loadOne(spec) {
        var count = document.getElementById(spec.count);
        if (!maps[spec.id]) return Promise.resolve();
        count.textContent = "Loading " + spec.title + "…";
        return fetch(endpoint + "?" + params(spec.sensor), {headers: {Accept: "application/geo+json, application/json"}})
            .then(function (r) {
                return r.json().then(function (d) {
                    if (!r.ok) throw new Error(d.error || ("HTTP " + r.status));
                    return d;
                });
            })
            .then(function (data) {
                var group = layers[spec.id];
                group.clearLayers();
                var bounds = [];
                (data.features || []).forEach(function (feature) {
                    var xy = feature.geometry && feature.geometry.coordinates;
                    if (!xy || xy.length < 2) return;
                    var p = feature.properties || {};
                    p.lat = xy[1]; p.lon = xy[0];
                    var sensor = p.sensor === "MODIS" ? "MODIS" : "VIIRS";
                    var style = {
                        radius: sensor === "MODIS" ? 4 : 3,
                        color: colors[sensor], weight: 1, fillColor: colors[sensor],
                        fillOpacity: 0.72
                    };
                    if (window.SAT && window.SAT.markerStyle) style = window.SAT.markerStyle(p);
                    style.renderer = renderers[spec.id];
                    var marker = L.circleMarker([xy[1], xy[0]], style).bindPopup(function () { return popup(p); }, {maxWidth: 360});
                    marker.addTo(group);
                    bounds.push([xy[1], xy[0]]);
                });
                if (bounds.length) maps[spec.id].fitBounds(bounds, {padding: [18,18], maxZoom: 9});
                var total = Number(data.total || 0);
                var shown = (data.features || []).length;
                count.textContent = shown.toLocaleString() + " displayed / " + total.toLocaleString() +
                    " matching records" + (data.truncated ? " — display limit reached; narrow the area or dates." : ".");
            })
            .catch(function (err) {
                count.textContent = "Could not load " + spec.title + ": " + err.message;
            });
    }

    specs.forEach(init);
    function reload() { specs.forEach(loadOne); }
    reload();

    /* Reuse the shared filter lifecycle so preset changes and form submits refresh all maps. */
    if (window.SAT && typeof window.SAT.onApply === "function") {
        var originalApply = window.SAT.onApply;
        window.SAT.onApply = function () {
            var result = originalApply.apply(this, arguments);
            reload();
            return result;
        };
    }
    window.addEventListener("resize", function () {
        Object.keys(maps).forEach(function (id) { maps[id].invalidateSize(); });
    });
})();
