/* PLEIADES satellite module: shared filters, fetch helpers, tiny SVG charts. Plain JS, no build step. */
(function () {
    "use strict";
    var SAT = window.SAT = {};
    var root = document.getElementById("sat-app");
    if (!root) return;
    var $ = SAT.$ = function (id) { return document.getElementById(id); };
    var API = (root.dataset.api || "/api/fire/overview/").replace(/overview\/$/, "");
    SAT.API = API;
    var PRESETS = {
        morocco: ["Morocco & Western Sahara", 20.84, -17.06, 35.74, -1.54],
        north: ["Northern Morocco (Rif, Gharb)", 33.8, -6.8, 35.9, -3.4],
        atlas: ["Middle & High Atlas", 31.0, -8.0, 33.8, -3.5],
        tadla: ["Tadla / Beni Mellal", 32.0, -7.2, 33.0, -5.6],
        oriental: ["Oriental", 33.0, -3.5, 35.3, -1.2],
        souss: ["Souss-Massa", 29.5, -10.2, 31.0, -8.0]
    };
    var NUM = ["south", "west", "north", "east"];
    var st = SAT.state = { south: 20.84, west: -17.06, north: 35.74, east: -1.54, start: "", end: "", confM: "n", confV: "n", scope: "", modis: true, viirs: true };
    SAT.colors = { modis: "#4aa8ff", viirs: "#f0a04b" };

    SAT.esc = function (s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
    SAT.fmt = function (n, d) { return (n === null || n === undefined || isNaN(n)) ? "-" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d === undefined ? 0 : d, minimumFractionDigits: d === undefined ? 0 : d }); };
    SAT.dt = function (s) { if (!s) return "-"; var d = new Date(s); return isNaN(d) ? String(s) : d.toISOString().replace("T", " ").slice(0, 16) + " UTC"; };
    SAT.error = function (m) { var e = $("sat-error"); if (!e) return; e.textContent = m || ""; e.hidden = !m; };

    /* ----- filters <-> URL ----- */
    function readQuery() {
        var q = new URLSearchParams(location.search);
        NUM.forEach(function (k) { var v = parseFloat(q.get(k)); if (isFinite(v)) st[k] = v; });
        st.start = q.get("start") || ""; st.end = q.get("end") || "";
        var ok = function (v) { return ["all", "l", "n", "h"].indexOf(v) >= 0 ? v : null; }, base = ok(q.get("min_conf")) || "n";
        st.confM = ok(q.get("modis_conf")) || base; st.confV = ok(q.get("viirs_conf")) || base;
        st.scope = ["real", "demo", "all"].indexOf(q.get("scope")) >= 0 ? q.get("scope") : "";
        var s = (q.get("sensor") || "MODIS,VIIRS").split(",");
        st.modis = s.indexOf("MODIS") >= 0; st.viirs = s.indexOf("VIIRS") >= 0;
    }
    SAT.params = function (extra) {
        var q = new URLSearchParams();
        NUM.forEach(function (k) { q.set(k, st[k]); });
        if (st.start) q.set("start", st.start);
        if (st.end) q.set("end", st.end);
        if (st.confM === st.confV) q.set("min_conf", st.confM); else { q.set("modis_conf", st.confM); q.set("viirs_conf", st.confV); }
        if (st.scope) q.set("scope", st.scope);
        var s = []; if (st.modis) s.push("MODIS"); if (st.viirs) s.push("VIIRS");
        if (s.length === 1) q.set("sensor", s[0]);
        Object.keys(extra || {}).forEach(function (k) { if (extra[k] !== "" && extra[k] !== null && extra[k] !== undefined) q.set(k, extra[k]); });
        return q.toString();
    };
    function syncForm() {
        NUM.forEach(function (k) { var i = $("f-" + k); if (i) i.value = st[k]; });
        if ($("f-start")) { $("f-start").value = st.start; $("f-end").value = st.end; $("f-conf-modis").value = st.confM; $("f-conf-viirs").value = st.confV; $("f-scope").value = st.scope; $("f-modis").checked = st.modis; $("f-viirs").checked = st.viirs; }
        var sel = $("f-preset");
        if (sel) { var key = Object.keys(PRESETS).filter(function (p) { var v = PRESETS[p]; return v[1] === st.south && v[2] === st.west && v[3] === st.north && v[4] === st.east; })[0]; sel.value = key || "custom"; }
    }
    function pushUrl() {
        history.replaceState(null, "", location.pathname + "?" + SAT.params());
        document.querySelectorAll("a[data-keep]").forEach(function (a) { a.href = a.pathname + "?" + SAT.params(); });
        var ex = $("btn-export"); if (ex && root.dataset.exportUrl) ex.href = root.dataset.exportUrl + "?" + SAT.params();
    }
    SAT.setAoi = function (a) { NUM.forEach(function (k) { st[k] = +(+a[k]).toFixed(4); }); syncForm(); pushUrl(); if (SAT.onApply) SAT.onApply(); };
    function readForm() {
        var a = {}; NUM.forEach(function (k) { a[k] = parseFloat($("f-" + k).value); });
        if (!NUM.every(function (k) { return isFinite(a[k]); }) || !(a.south >= -90 && a.north <= 90 && a.south < a.north && a.west >= -180 && a.east <= 180 && a.west < a.east)) {
            SAT.error("Enter valid coordinates: south < north (-90 to 90) and west < east (-180 to 180)."); return false;
        }
        var s = $("f-start").value, e = $("f-end").value;
        if (s && e && s > e) { SAT.error("The start date must not be after the end date."); return false; }
        if (!$("f-modis").checked && !$("f-viirs").checked) { SAT.error("Select at least one sensor."); return false; }
        SAT.error(""); NUM.forEach(function (k) { st[k] = a[k]; });
        st.start = s; st.end = e; st.confM = $("f-conf-modis").value; st.confV = $("f-conf-viirs").value; st.scope = $("f-scope").value; st.modis = $("f-modis").checked; st.viirs = $("f-viirs").checked;
        return true;
    }

    /* ----- fetch ----- */
    SAT.get = function (path, extra) {
        return fetch(API + path + "?" + SAT.params(extra), { headers: { Accept: "application/json" } })
            .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || "Request failed (" + r.status + ")"); return j; }); });
    };
    SAT.busy = function (on) { root.classList.toggle("is-loading", on); };

    /* ----- banner ----- */
    SAT.banner = function (d) {
        var b = $("sat-banner"); if (!b) return true;
        b.className = "data-banner"; b.hidden = false;
        var body = $("sat-body");
        if (d.data_mode === "empty" || d.scope === "empty") {
            b.classList.add("warn"); b.innerHTML = "<strong>No satellite data stored yet.</strong> An operator can import NASA FIRMS data (needs <code>FIRMS_MAP_KEY</code>) or load clearly labelled synthetic demonstration data from <a href=\"" + SAT.esc(document.querySelector("a[href*='/data/']") ? document.querySelector("a[href*='/data/']").pathname : "#") + "\">Data management</a>.";
            if (body) body.hidden = true; return false;
        }
        if (body) body.hidden = false;
        if (d.scope === "demo") { b.classList.add("demo"); b.innerHTML = "<strong>Synthetic demonstration data.</strong> These detections are generated for development and are <em>not</em> NASA observations."; }
        else if (d.scope === "all" && d.data_mode === "mixed") { b.classList.add("warn"); b.innerHTML = "<strong>Real and synthetic data are combined.</strong> Results mix NASA observations with generated rows and are not scientifically meaningful. Choose one category."; }
        else b.hidden = true;
        return true;
    };

    /* ----- charts ----- */
    function nice(v) { if (v <= 0) return 1; var e = Math.pow(10, Math.floor(Math.log10(v))), f = v / e; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e; }
    SAT.legend = function (host, items) { host.innerHTML = items.map(function (i) { return '<span class="lg"><svg width="14" height="10" aria-hidden="true"><rect width="14" height="10" rx="2" fill="' + i.color + '"/></svg>' + SAT.esc(i.name) + "</span>"; }).join(""); };
    SAT.bars = function (host, o) {
        var W = 900, H = o.height || 250, L = 52, R = 8, T = 10, B = 32, w = W - L - R, h = H - T - B, n = o.labels.length;
        if (!n) { host.innerHTML = '<p class="empty-note">No data for this selection.</p>'; return; }
        var max = 0; o.series.forEach(function (s) { s.values.forEach(function (v) { if (v > max) max = v; }); });
        max = nice(max || 1);
        var gw = w / n, bw = Math.max(1, gw * 0.8 / o.series.length), p = ['<svg viewBox="0 0 ' + W + " " + H + '" class="lc" role="img" aria-label="' + SAT.esc(o.label || "chart") + '">'];
        for (var g = 0; g <= 4; g++) { var v = max * g / 4, y = T + h - v / max * h; p.push('<line x1="' + L + '" x2="' + (W - R) + '" y1="' + y + '" y2="' + y + '" class="grid"/><text x="' + (L - 6) + '" y="' + (y + 4) + '" text-anchor="end" class="axis-t">' + SAT.fmt(v, max < 10 ? 1 : 0) + "</text>"); }
        var step = Math.max(1, Math.ceil(n / 14));
        o.labels.forEach(function (lab, i) {
            var x0 = L + i * gw + gw * 0.1;
            o.series.forEach(function (s, k) {
                var v = s.values[i] || 0, bh = v / max * h;
                if (v > 0) p.push('<rect x="' + (x0 + k * bw) + '" y="' + (T + h - bh) + '" width="' + bw + '" height="' + bh + '" fill="' + s.color + '"><title>' + SAT.esc(lab + " · " + s.name + ": " + SAT.fmt(v, v % 1 ? 1 : 0)) + "</title></rect>");
            });
            if (i % step === 0) p.push('<text x="' + (L + i * gw + gw / 2) + '" y="' + (H - 10) + '" text-anchor="middle" class="axis-t">' + SAT.esc(lab) + "</text>");
        });
        p.push("</svg>"); host.innerHTML = p.join("");
    };
    SAT.table = function (host, head, rows, empty) {
        if (!rows.length) { host.innerHTML = '<tbody><tr><td class="empty">' + SAT.esc(empty || "Nothing to show.") + "</td></tr></tbody>"; return; }
        host.innerHTML = "<thead><tr>" + head.map(function (h) { return "<th>" + SAT.esc(h) + "</th>"; }).join("") + "</tr></thead><tbody>" +
            rows.map(function (r) { return "<tr>" + r.map(function (c) { return "<td>" + c + "</td>"; }).join("") + "</tr>"; }).join("") + "</tbody>";
    };

    /* ----- init ----- */
    SAT.init = function (apply, opts) {
        opts = opts || {};
        var sel = $("f-preset");
        if (sel) {
            Object.keys(PRESETS).forEach(function (k) { var o = document.createElement("option"); o.value = k; o.textContent = PRESETS[k][0]; sel.appendChild(o); });
            var c = document.createElement("option"); c.value = "custom"; c.textContent = "Custom area"; sel.appendChild(c);
            sel.addEventListener("change", function () { var p = PRESETS[sel.value]; if (p) SAT.setAoi({ south: p[1], west: p[2], north: p[3], east: p[4] }); });
        }
        readQuery(); syncForm(); pushUrl();
        SAT.onApply = function () { SAT.error(""); SAT.busy(true); Promise.resolve(apply()).catch(function (e) { SAT.error(e.message); }).then(function () { SAT.busy(false); }); };
        var form = $("sat-filters");
        if (form) form.addEventListener("submit", function (e) { e.preventDefault(); if (readForm()) { pushUrl(); SAT.onApply(); } });
        var go = function () { SAT.onApply(); };
        if (opts.defaultDays && !st.start && !st.end) {  // default window: last N days of the stored data
            SAT.get("overview").then(function (d) {
                if (d.data_last) { var e = new Date(d.data_last), s = new Date(e.getTime() - (opts.defaultDays - 1) * 864e5); st.end = d.data_last; st.start = s.toISOString().slice(0, 10); if (d.data_first && st.start < d.data_first) st.start = d.data_first; syncForm(); pushUrl(); }
                go();
            }).catch(function () { go(); });
        } else go();
    };

    /* hook for static/js/live_refresh.js (refresh-from-NASA button) */
    window.PLEIADES_LIVE = { aoi: function () { return { south: st.south, west: st.west, north: st.north, east: st.east }; }, reload: function () { if (SAT.onApply) SAT.onApply(); else location.reload(); } };
})();
