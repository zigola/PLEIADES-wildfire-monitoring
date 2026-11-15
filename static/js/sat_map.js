/* PLEIADES satellite module: interactive MODIS / VIIRS detection map (Leaflet). */
(function () {
    "use strict";
    var root = document.getElementById("sat-app");
    if (!root || !window.SAT || root.dataset.page !== "map") return;
    var S = window.SAT, $ = S.$, esc = S.esc, C = S.colors;
    var box = $("sat-map");
    if (!window.L) { box.innerHTML = '<p class="map-fallback">The map could not be loaded (Leaflet is unavailable). Detections are still available in the CSV export and the other pages.</p>'; S.init(function () { return S.get("overview").then(S.banner); }); return; }

    var map = L.map(box).setView([31.5, -7], 5);
    L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", {
            maxZoom: 19,
            attribution: "Tiles &copy; Esri"
        }).addTo(map);
    var renderer = L.canvas({ padding: 0.4 });
    var layers = { MODIS: L.layerGroup().addTo(map), VIIRS: L.layerGroup().addTo(map) }, clusterLayer = L.layerGroup().addTo(map), aoiRect = null, drawing = false, start = null;
    var FIELDS = [["date", "Date"], ["time_utc", "Time (UTC)"], ["satellite", "Satellite"], ["instrument", "Instrument"], ["confidence", "Confidence"], ["brightness_k", "Brightness (K)"],
        ["brightness_2_k", "Brightness 2 (K)"], ["frp_mw", "FRP (MW)"], ["scan_km", "Scan (km)"], ["track_km", "Track (km)"], ["daynight", "Day/night"], ["product", "Product"], ["version", "Version"], ["category", "Data source"], ["overlap_group", "MODIS/VIIRS overlap group"], ["import_id", "Import #"]];
    var CAT = { nasa_standard: "NASA FIRMS (SP)", nasa_nrt: "NASA FIRMS (NRT)", nasa_imported_csv: "NASA FIRMS CSV", synthetic_demo: "SYNTHETIC demo, not NASA" };
    var DN = { D: "Day", N: "Night" };

    function legacyPopup(p) {
        var rows = ['<tr><th>Latitude</th><td>' + p.lat + '</td></tr><tr><th>Longitude</th><td>' + p.lon + "</td></tr>"];
        FIELDS.forEach(function (f) { var v = p[f[0]]; if (v === undefined || v === null || v === "") return; if (f[0] === "category") v = CAT[v] || v; if (f[0] === "daynight") v = DN[v] || v; rows.push("<tr><th>" + f[1] + "</th><td>" + esc(v) + "</td></tr>"); });
        return '<div class="pop"><b style="color:' + (p.sensor === "MODIS" ? C.modis : C.viirs) + '">' + esc(p.sensor) + " detection</b><table>" + rows.join("") + "</table><small>Thermal anomaly, not a confirmed wildfire.</small></div>";
    }
    function popup(p) { return S.popupHtml ? S.popupHtml(p) : legacyPopup(p); }
    function legend() {
        $("map-legend").innerHTML = '<span class="lg"><svg width="16" height="16"><rect x="2" y="2" width="12" height="12" fill="none" stroke="' + C.modis + '" stroke-width="2"/></svg>MODIS (about 1 km)</span><span class="lg"><svg width="16" height="16"><circle cx="8" cy="8" r="4" fill="' + C.viirs + '"/></svg>VIIRS (375 m)</span><span class="lg"><svg width="16" height="16"><rect x="2" y="2" width="12" height="12" fill="none" stroke="#fff" stroke-dasharray="3 2"/></svg>Cluster</span><span class="lg">Symbol size grows with FRP when the record has one; VIIRS opacity and MODIS outline weight show the confidence class. These are satellite measurements, not fire temperatures.</span>';
    }
    function drawAoi() {
        var b = [[S.state.south, S.state.west], [S.state.north, S.state.east]];
        if (aoiRect) aoiRect.setBounds(b); else aoiRect = L.rectangle(b, { color: "#4aa8ff", weight: 2, fillOpacity: 0.04, interactive: false }).addTo(map);
    }
    function toggleLayers() { ["MODIS", "VIIRS"].forEach(function (s) { var on = $("m-" + s.toLowerCase()).checked; if (on && !map.hasLayer(layers[s])) map.addLayer(layers[s]); if (!on) map.removeLayer(layers[s]); }); }

    var lastGeo = null;
    function fillSatellites(features) {
        var sel = $("m-sat"); if (!sel) return;
        var keep = sel.value, names = {};
        features.forEach(function (f) { if (f.properties.satellite) names[f.properties.satellite] = 1; });
        sel.innerHTML = '<option value="">All satellites</option>' + Object.keys(names).sort().map(function (n) { return '<option value="' + esc(n) + '">' + esc(n) + "</option>"; }).join("");
        sel.value = names[keep] ? keep : "";
    }
    function drawFeatures() {
        layers.MODIS.clearLayers(); layers.VIIRS.clearLayers();
        if (!lastGeo) return;
        var want = $("m-sat") ? $("m-sat").value : "", shown = 0;
        lastGeo.features.forEach(function (f) {
            var p = f.properties; if (want && p.satellite !== want) return;
            var style = S.markerStyle ? S.markerStyle(p) : (p.sensor === "MODIS" ? { radius: 7, color: C.modis, weight: 2, fillOpacity: 0 } : { radius: 3.5, color: C.viirs, weight: 1, fillColor: C.viirs, fillOpacity: 0.85 });
            style.renderer = renderer;
            L.circleMarker([p.lat, p.lon], style).bindTooltip(esc(p.sensor) + " · " + esc(p.satellite || "satellite not provided") + " · " + esc(p.date || "")).bindPopup(function () { return popup(p); }, { maxWidth: 360 }).addTo(layers[p.sensor]);
            shown++;
        });
        var note = $("map-sat-note"); if (note) note.textContent = want ? shown.toLocaleString() + " of the loaded detections are from " + want + "." : "";
    }

    function load() {
        var dn = $("m-daynight").value;
        $("map-caption").textContent = "loading…";
        drawAoi();
        var tasks = [S.get("overview"), S.get("detections", { format: "geojson", daynight: dn })];
        if ($("m-clusters").checked) tasks.push(S.get("clusters", { eps_km: 2, eps_hours: 24, min_pts: 3 }).catch(function (e) { return { error: e.message }; }));
        return Promise.all(tasks).then(function (r) {
            if (!S.banner(r[0])) { $("map-caption").textContent = "no data"; return; }
            var g = r[1]; clusterLayer.clearLayers(); lastGeo = g;
            g.features.forEach(function (f) { var p = f.properties; p.lat = f.geometry.coordinates[1]; p.lon = f.geometry.coordinates[0]; });
            fillSatellites(g.features);
            drawFeatures();
            $("map-caption").textContent = g.total ? (g.truncated ? "showing the " + g.limit.toLocaleString() + " most recent of " + g.total.toLocaleString() + " detections. Narrow the area or dates to see all." : g.total.toLocaleString() + " detections") : "No detections for this selection (check dates, confidence and sensors).";
            if (r[2]) {
                if (r[2].error) S.error(r[2].error);
                else r[2].clusters.forEach(function (c) { var b = c.bbox, pad = 0.01; L.rectangle([[b[0] - pad, b[1] - pad], [b[2] + pad, b[3] + pad]], { color: "#fff", weight: 1.5, dashArray: "4 3", fillOpacity: 0.03 }).bindTooltip("Cluster of " + c.n + " detections (MODIS " + c.modis + ", VIIRS " + c.viirs + "), " + c.first + " → " + c.last).addTo(clusterLayer); });
            }
            toggleLayers();
        });
    }
    if ($("m-sat")) $("m-sat").addEventListener("change", function () { drawFeatures(); toggleLayers(); });
    $("m-modis").addEventListener("change", toggleLayers); $("m-viirs").addEventListener("change", toggleLayers);
    $("m-clusters").addEventListener("change", function () { S.onApply(); }); $("m-daynight").addEventListener("change", function () { S.onApply(); });
    $("btn-view").addEventListener("click", function () { var b = map.getBounds(); S.setAoi({ south: Math.max(-90, b.getSouth()), west: Math.max(-180, b.getWest()), north: Math.min(90, b.getNorth()), east: Math.min(180, b.getEast()) }); });
    $("btn-draw").addEventListener("click", function () {
        drawing = !drawing; this.classList.toggle("on", drawing); this.textContent = drawing ? "Drag a rectangle…" : "Draw area on map";
        if (drawing) { map.dragging.disable(); box.classList.add("drawing"); } else { map.dragging.enable(); box.classList.remove("drawing"); }
    });
    map.on("mousedown", function (e) { if (drawing) start = e.latlng; });
    map.on("mousemove", function (e) { if (drawing && start && aoiRect) aoiRect.setBounds([start, e.latlng]); });
    map.on("mouseup", function (e) {
        if (!drawing || !start) return; var b = L.latLngBounds(start, e.latlng); start = null; drawing = false; map.dragging.enable(); box.classList.remove("drawing");
        $("btn-draw").classList.remove("on"); $("btn-draw").textContent = "Draw area on map";
        if (b.getNorth() - b.getSouth() < 0.02 || b.getEast() - b.getWest() < 0.02) { S.error("Area too small. Drag a larger rectangle."); drawAoi(); return; }
        S.setAoi({ south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast() });
    });
    legend();
    var first = true;
    S.init(function () { return load().then(function () { if (first) { first = false; map.fitBounds([[S.state.south, S.state.west], [S.state.north, S.state.east]]); } }); }, { defaultDays: 30 });
})();
