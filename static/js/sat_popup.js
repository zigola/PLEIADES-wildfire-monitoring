/* PLEIADES: one detailed hotspot panel shared by every satellite map.
   SAT.popupHtml(p) -> HTML with four sections (identification, satellite, thermal, provenance).
   Only values present in the stored record are shown; anything missing reads "Not available".
   SAT.markerStyle(p) -> Leaflet circleMarker options (size from FRP when provided, confidence class, sensor colour). */
(function () {
    "use strict";
    var S = window.SAT = window.SAT || {};
    var NA = "Not available";
    var COLORS = { MODIS: "#4aa8ff", VIIRS: "#f0a04b" };
    var CAT = { nasa_standard: "NASA FIRMS standard processing (SP)", nasa_nrt: "NASA FIRMS near real-time (NRT)", nasa_imported_csv: "NASA FIRMS CSV download", synthetic_demo: "SYNTHETIC demonstration data, not NASA" };
    var DN = { D: "Day", N: "Night" };
    var CLASS = { l: "low", n: "nominal", h: "high" };
    function esc(s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
    function has(v) { return v !== undefined && v !== null && v !== ""; }
    function val(v, unit) { return has(v) ? esc(v) + (unit || "") : '<span class="na">' + NA + "</span>"; }
    function utc(s) { var d = new Date(s); return isNaN(d) ? esc(s) : d.toISOString().replace("T", " ").slice(0, 16) + " UTC"; }
    function ageDays(iso) {
        var p = String(iso || "").split("-"); if (p.length !== 3) return null;
        var t = new Date(), today = Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate());
        return Math.round((today - Date.UTC(+p[0], +p[1] - 1, +p[2])) / 864e5);
    }
    function classification(p) {
        if (p.category === "synthetic_demo") return "SYNTHETIC demonstration data (not a NASA observation)";
        var a = ageDays(p.date);
        if (a === null) return "NASA observation";
        return a <= 2 ? "Verified NASA satellite observation (current: acquired " + (a <= 0 ? "today" : a + " day(s) ago") + ")"
                      : "Historical NASA observation (acquired " + p.date + ")";
    }
    function confidence(p) {
        if (!has(p.confidence)) return null;
        var cls = p.confidence_class ? " (class " + (CLASS[p.confidence_class] || p.confidence_class) + ")" : "";
        return p.sensor === "MODIS" ? p.confidence + " % (MODIS scale)" + cls : p.confidence + " (VIIRS scale)" + cls;
    }
    function section(title, rows) {
        return "<h4>" + esc(title) + "</h4><table>" + rows.map(function (r) { return "<tr><th>" + esc(r[0]) + "</th><td>" + r[1] + "</td></tr>"; }).join("") + "</table>";
    }
    S.popupHtml = function (p) {
        var obs = has(p.date) ? esc(p.date) + (has(p.time_utc) ? " " + esc(p.time_utc) : "") + " UTC" : '<span class="na">' + NA + "</span>";
        var html = section("A. Identification", [
            ["Observation ID", val(p.id)], ["Latitude", val(p.lat)], ["Longitude", val(p.lon)],
            ["Acquisition date (UTC)", val(p.date)], ["Acquisition time (UTC)", val(p.time_utc)]]) +
            section("B. Satellite information", [
            ["Satellite", val(p.satellite)], ["Instrument", val(p.instrument)], ["Sensor", val(p.sensor)],
            ["NASA FIRMS product", val(p.product)], ["Product version", val(p.version)]]) +
            section("C. Thermal information", [
            ["Brightness temperature (K)", val(p.brightness_k)], ["Secondary brightness temperature (K)", val(p.brightness_2_k)],
            ["Fire radiative power (MW)", val(p.frp_mw)], ["Detection confidence", has(confidence(p)) ? esc(confidence(p)) : '<span class="na">' + NA + "</span>"],
            ["Scan dimension (km)", val(p.scan_km)], ["Track dimension (km)", val(p.track_km)], ["Day / night", has(p.daynight) ? esc(DN[p.daynight] || p.daynight) : '<span class="na">' + NA + "</span>"]]) +
            section("D. Data provenance", [
            ["Original NASA source", has(p.source) ? esc(p.source) : (CAT[p.category] ? esc(CAT[p.category]) : '<span class="na">' + NA + "</span>")],
            ["Product identifier", val(p.product)], ["Observation timestamp", obs],
            ["Import timestamp", has(p.imported_at) ? utc(p.imported_at) : '<span class="na">' + NA + "</span>"],
            ["Data classification", esc(classification(p))]]);
        return '<div class="pop pop-detail"><b style="color:' + (COLORS[p.sensor] || "#ccc") + '">' + esc(p.sensor) + " detection · " + esc(p.satellite || "satellite not provided") + "</b>" + html +
            "<small>Satellite thermal anomaly, not a confirmed wildfire. Brightness temperature is what the sensor measured for the pixel, not the physical temperature of a fire.</small></div>";
    };
    /* FRP drives the symbol size when the record has one; a missing FRP keeps the default size (no value is invented). */
    S.markerStyle = function (p) {
        var modis = p.sensor === "MODIS", frp = has(p.frp_mw) ? Number(p.frp_mw) : null, k = p.confidence_class;
        var grow = frp === null || !isFinite(frp) || frp < 0 ? 0 : Math.min(5, (modis ? 0.45 : 0.9) * Math.sqrt(frp));
        var radius = (modis ? 6 : 3) + grow, c = COLORS[modis ? "MODIS" : "VIIRS"];
        if (modis) return { radius: radius, color: c, weight: k === "h" ? 3 : k === "l" ? 1 : 2, fillOpacity: 0 };
        return { radius: radius, color: c, weight: 1, fillColor: c, fillOpacity: k === "h" ? 0.95 : k === "l" ? 0.4 : 0.78 };
    };
})();
