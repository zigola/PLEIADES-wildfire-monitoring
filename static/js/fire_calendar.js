/* PLEIADES - Burning activity calendar (MODIS + VIIRS harmonized).
   Plain JavaScript, no build step. Leaflet is optional: if it fails to load the
   map shows a notice and every other panel keeps working. */
(function () {
    "use strict";

    var root = document.getElementById("fc-app");
    if (!root) return;
    if (root.getAttribute("data-fc-init")) return; // never initialise the page (and its map) twice
    root.setAttribute("data-fc-init", "1");

    var URLS = { summary: root.dataset.summaryUrl, cells: root.dataset.cellsUrl, calendar: root.dataset.calendarUrl, day: root.dataset.dayUrl, compare: root.dataset.compareUrl, map: root.dataset.mapUrl };
    var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var DEFAULT_AOI = { south: 20.84, west: -17.06, north: 35.74, east: -1.54 };
    var PRESETS = {
        morocco: ["Morocco & Western Sahara", 20.84, -17.06, 35.74, -1.54],
        north: ["Northern Morocco (Rif, Gharb)", 33.8, -6.8, 35.9, -3.4],
        atlas: ["Middle & High Atlas", 31.0, -8.0, 33.8, -3.5],
        tadla: ["Tadla / Beni Mellal", 32.0, -7.2, 33.0, -5.6],
        oriental: ["Oriental", 33.0, -3.5, 35.3, -1.2],
        souss: ["Souss-Massa", 29.5, -10.2, 31.0, -8.0]
    };
    // single-hue sequential ramp (light = more activity), zero cells are a dark neutral
    var RAMP = ["#141d33", "#5c2c1c", "#a8421f", "#e8662e", "#ffa352", "#ffe0b3"];
    var C_MODIS = "#4aa8ff", C_VIIRS = "#f0a04b", C_HARM = "#f3f6fb";

    var $ = function (id) { return document.getElementById(id); };
    var state = { aoi: null, minConf: "n", scope: "", sensor: "", data: null, cal: null, year: null, day: null, drawing: false, dayPage: 1, dayRows: "" };
    var map = null, aoiRect = null, cellLayer = null, cellRenderer = null, reqId = 0, mapHasFittedCells = false, cellData = null, cellsAsSquares = null;
    var mapBox = null, mapObserver = null, mapRaf = 0, baseLayer = null, tileOk = 0, tileErr = 0, usedFallbackTiles = false;
    var TILES = {
        esri: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", "Tiles &copy; Esri"],
        osm: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png", "&copy; OpenStreetMap contributors"]
    };

    /* ---------------------------------------------------------------- helpers */
    function fmt(n, d) { return (n === null || n === undefined || isNaN(n)) ? "-" : Number(n).toFixed(d === undefined ? 0 : d); }
    function pad(n) { return (n < 10 ? "0" : "") + n; }
    function isoFromDoy(y, doy) { var d = new Date(Date.UTC(y, 0, doy)); return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate()); }
    function isLeap(y) { return y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0); }
    function daysIn(y) { return isLeap(y) ? 366 : 365; }
    function mdLabel(md) { var p = md.split("-"); return MONTHS[+p[0] - 1] + " " + +p[1]; }
    function mdToBin(md) { var p = md.split("-"); var doy = Math.round((Date.UTC(2001, +p[0] - 1, +p[1]) - Date.UTC(2001, 0, 1)) / 864e5) + 1; return Math.min(Math.floor((doy - 1) / 7), 51); }
    function binDays(y, b) { return b === 51 ? daysIn(y) - 357 : 7; }
    function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
    function setText(id, t) { var e = $(id); if (e) e.textContent = t; }
    function showError(msg) { var e = $("fc-error"); e.textContent = msg || ""; e.hidden = !msg; }

    var tip = $("fc-tip");
    function showTip(evt, html) {
        tip.innerHTML = html; tip.hidden = false;
        var pad8 = 14, w = tip.offsetWidth, h = tip.offsetHeight;
        var x = evt.clientX + pad8, y = evt.clientY + pad8;
        if (x + w > window.innerWidth - 8) x = evt.clientX - w - pad8;
        if (y + h > window.innerHeight - 8) y = evt.clientY - h - pad8;
        tip.style.left = Math.max(8, x) + "px"; tip.style.top = Math.max(8, y) + "px";
    }
    function hideTip() { tip.hidden = true; }

    /* ------------------------------------------------------------- AOI + URL */
    function readQuery() {
        var q = new URLSearchParams(window.location.search), a = {};
        ["south", "west", "north", "east"].forEach(function (k) { a[k] = parseFloat(q.get(k)); });
        var ok = ["south", "west", "north", "east"].every(function (k) { return isFinite(a[k]); });
        if (q.get("conf") === "l") state.minConf = "l";
        if (["MODIS", "VIIRS"].indexOf(q.get("sensor")) >= 0) state.sensor = q.get("sensor");
        if (["real", "demo", "all"].indexOf(q.get("scope")) >= 0) state.scope = q.get("scope");
        if (q.get("name")) $("area-name").value = q.get("name").slice(0, 80);
        return ok && validAoi(a) ? a : Object.assign({}, DEFAULT_AOI);
    }
    function validAoi(a) { return a.south >= -90 && a.north <= 90 && a.south < a.north && a.west >= -180 && a.east <= 180 && a.west < a.east; }
    function aoiParams() {
        var a = state.aoi;
        return "south=" + a.south + "&west=" + a.west + "&north=" + a.north + "&east=" + a.east + "&min_conf=" + state.minConf + (state.scope ? "&scope=" + state.scope : "") + (state.sensor ? "&sensor=" + state.sensor : "");
    }
    function syncInputs() {
        ["south", "west", "north", "east"].forEach(function (k) { $("aoi-" + k).value = state.aoi[k]; });
        var f = document.querySelectorAll(".save-" + "aoi");
        f.forEach(function (i) { i.value = state.aoi[i.dataset.k]; });
        var nxt = $("save-next"); if (nxt) nxt.value = window.location.pathname + window.location.search;
        $("conf-low").checked = state.minConf === "l";
        $("scope-select").value = state.scope; $("sensor-select").value = state.sensor;
        var key = Object.keys(PRESETS).filter(function (p) { var v = PRESETS[p]; return v[1] === state.aoi.south && v[2] === state.aoi.west && v[3] === state.aoi.north && v[4] === state.aoi.east; })[0];
        $("aoi-preset").value = key || "custom";
    }
    function pushUrl() {
        var q = new URLSearchParams();
        ["south", "west", "north", "east"].forEach(function (k) { q.set(k, state.aoi[k]); });
        if (state.minConf === "l") q.set("conf", "l");
        if (state.scope) q.set("scope", state.scope);
        if (state.sensor) q.set("sensor", state.sensor);
        if (state.year) q.set("year", state.year);
        var name = $("area-name").value.trim(); if (name) q.set("name", name);
        history.replaceState(null, "", window.location.pathname + "?" + q.toString());
        var nxt = $("save-next"); if (nxt) nxt.value = window.location.pathname + window.location.search;
    }
    function setAoi(a, fit) {
        state.aoi = { south: +(+a.south).toFixed(4), west: +(+a.west).toFixed(4), north: +(+a.north).toFixed(4), east: +(+a.east).toFixed(4) };
        syncInputs(); drawAoi(fit); load();
    }

    /* -------------------------------------------------------------- data load */
    function load() {
        showError("");
        var my = ++reqId;
        root.classList.add("is-loading");
        fetch(URLS.summary + "?" + aoiParams(), { headers: { Accept: "application/json" } })
            .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || "Request failed"); return j; }); })
            .then(function (d) { if (my !== reqId) return; state.data = d; state.day = null; onData(); })
            .catch(function (e) { if (my !== reqId) return; showError("Could not load the calendar: " + e.message); })
            .then(function () { if (my === reqId) root.classList.remove("is-loading"); });
    }

    function onData() {
        var d = state.data, banner = $("data-banner"), body = $("fc-body");
        banner.className = "data-banner"; banner.hidden = false;
        if (!d.has_data) {
            banner.classList.add("warn");
            banner.innerHTML = "<strong>No hotspot data in the database yet.</strong> Import NASA FIRMS data with <code>python manage.py import_firms --api ...</code> (needs <code>FIRMS_MAP_KEY</code>) or load clearly labelled synthetic data with <code>python manage.py seed_demo_data</code>.";
            body.hidden = true; return;
        }
        body.hidden = false;
        ensureMap();   // the map container only has a size now that #fc-body is visible
        if (d.scope === "demo") { banner.classList.add("demo"); banner.innerHTML = "<strong>Synthetic demonstration data.</strong> These detections are generated for development; they are <em>not</em> NASA observations. Import NASA FIRMS data (Data management) to work with real fire records."; }
        else if (d.scope === "all" && d.data_mode === "mixed") { banner.classList.add("warn"); banner.innerHTML = "<strong>Real and synthetic data are combined.</strong> Results are not scientifically meaningful. Pick one data category."; }
        else { banner.hidden = true; }

        var years = d.years;
        var sel = $("year-select"); sel.innerHTML = "";
        years.forEach(function (y) { var o = document.createElement("option"); o.value = y; o.textContent = y; sel.appendChild(o); });
        var wanted = parseInt(new URLSearchParams(window.location.search).get("year"), 10);
        if (years.indexOf(state.year) < 0) state.year = years.indexOf(wanted) >= 0 ? wanted : (years.length > 1 ? years[years.length - 2] : years[0]);
        sel.value = state.year;
        ["cmp-a", "cmp-b"].forEach(function (id, k) { var o = $(id); o.innerHTML = sel.innerHTML; var idx = years.indexOf(state.year); o.value = k === 0 ? state.year : years[Math.max(0, idx - 1)]; });
        renderAll();
    }

    function renderAll() {
        renderKpis(); renderHeatmap(); renderClimatology(); renderValidation(); renderAnomalies(); renderPeriods(); renderTable();
        loadYear(); loadCells(); pushUrl();
    }

    /* -------------------------------------------------------------------- KPIs */
    function renderKpis() {
        var d = state.data, h = d.harmonization;
        setText("kpi-years", d.years.length ? d.years[0] + "–" + d.years[d.years.length - 1] : "-");
        setText("kpi-scale", h.method === "single-sensor" ? "n/a" : "×" + fmt(h.scale, 2));
        var note = { "area-overlap": "VIIRS cell-days → MODIS-equivalent (calibrated on this area)", fallback: "Area too small: region-wide factor used", "insufficient-overlap": "Too little overlap to calibrate", "no-overlap": "Sensors never overlap", "single-sensor": "Only one sensor in the data" }[h.method] || "";
        setText("kpi-scale-note", note);
        setText("kpi-r", h.r_weekly === null ? "-" : fmt(h.r_weekly, 2));
        setText("kpi-r-note", h.overlap_days ? "weekly correlation over " + h.overlap_days.toLocaleString() + " overlap days" : "needs both sensors");
        var p = d.critical_periods[0];
        setText("kpi-peak", p ? mdLabel(p.start_md) + " → " + mdLabel(p.end_md) : "none found");
        setText("kpi-peak-note", p ? "typical fire season (" + p.weeks + " weeks)" : "not enough activity in this area");
        setText("kpi-unusual", String(d.anomalies.length));
    }

    /* ----------------------------------------------------------- yearly heatmap */
    function dailyMap(year) {
        var m = {}; (state.data.daily[String(year)] || []).forEach(function (r) { m[r[0]] = r; }); return m;
    }
    function p98() {
        var vals = [];
        Object.keys(state.data.daily).forEach(function (y) { state.data.daily[y].forEach(function (r) { if (r[1] > 0) vals.push(r[1]); }); });
        if (!vals.length) return 1; vals.sort(function (a, b) { return a - b; });
        return Math.max(1, vals[Math.floor(vals.length * 0.98)]);
    }
    function level(v, max) { if (v <= 0) return 0; var t = Math.sqrt(v / max); return t < 0.2 ? 1 : t < 0.4 ? 2 : t < 0.65 ? 3 : t < 0.9 ? 4 : 5; }
    function coverageRange() {
        var c = state.data.coverage, lo = null, hi = null;
        Object.keys(c).forEach(function (k) { if (lo === null || c[k][0] < lo) lo = c[k][0]; if (hi === null || c[k][1] > hi) hi = c[k][1]; });
        return [Date.parse(lo), Date.parse(hi)];
    }
    function covered(y, doy, rng) { var t = Date.UTC(y, 0, doy); return t >= rng[0] && t <= rng[1]; }

    function calMap() {
        var m = {};
        if (state.cal && state.cal.year === state.year) state.cal.days.forEach(function (d) { m[doyOf(d.date)] = d; });
        return m;
    }
    function doyOf(iso) { var p = iso.split("-"); return Math.round((Date.UTC(+p[0], +p[1] - 1, +p[2]) - Date.UTC(+p[0], 0, 1)) / 864e5) + 1; }
    function renderHeatmap() {
        var y = state.year, dm = dailyMap(y), cm = calMap(), haveCal = Object.keys(cm).length > 0, max = p98(), rng = coverageRange();
        var CELL = 13, GAP = 3, STEP = CELL + GAP, LEFT = 34, TOP = 22;
        var startWd = (new Date(Date.UTC(y, 0, 1)).getUTCDay() + 6) % 7; // Monday = 0
        var cols = Math.ceil((daysIn(y) + startWd) / 7);
        var W = LEFT + cols * STEP + 4, H = TOP + 7 * STEP + 4, out = [];
        out.push('<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Burning activity calendar for ' + y + '" class="heat">');
        ["Mon", "Wed", "Fri"].forEach(function (l, i) { out.push('<text x="0" y="' + (TOP + i * 2 * STEP + 11) + '" class="axis-t">' + l + "</text>"); });
        for (var m = 0; m < 12; m++) {
            var doy1 = Math.round((Date.UTC(y, m, 1) - Date.UTC(y, 0, 1)) / 864e5) + 1;
            out.push('<text x="' + (LEFT + Math.floor((doy1 - 1 + startWd) / 7) * STEP) + '" y="12" class="axis-t">' + MONTHS[m] + "</text>");
        }
        for (var doy = 1; doy <= daysIn(y); doy++) {
            var col = Math.floor((doy - 1 + startWd) / 7), row = (doy - 1 + startWd) % 7;
            var rec = dm[doy], v = rec ? rec[1] : 0, c = cm[doy], cls;
            if (haveCal) {
                // status comes from the traceable per-day calendar API: no_data / zero / detections
                cls = c.status === "no_data" ? "nodata" : c.status === "zero" ? "zero" : "c" + Math.max(1, level(Math.max(v, 0.0001), max));
                if (c.high) cls += " high";
            } else { cls = covered(y, doy, rng) ? "c" + level(v, max) : "nodata"; }
            var sel = state.day === doy ? " sel" : "";
            out.push('<rect x="' + (LEFT + col * STEP) + '" y="' + (TOP + row * STEP) + '" width="' + CELL + '" height="' + CELL + '" rx="3" class="cell ' + cls + sel + '" data-doy="' + doy + '"/>');
        }
        out.push("</svg>");
        $("heatmap").innerHTML = out.join("");

        var svg = $("heatmap").querySelector("svg");
        svg.addEventListener("mousemove", function (e) {
            var t = e.target; if (!t.dataset || !t.dataset.doy) { hideTip(); return; }
            var doy = +t.dataset.doy, r = dm[doy], iso = isoFromDoy(y, doy), c = cm[doy];
            if (c) {
                var head = "<b>" + iso + "</b><br>";
                if (c.status === "no_data") showTip(e, head + "No observations in the database for this area and day.<br><span class='tt-m'>Missing data, not zero fire.</span>");
                else showTip(e, head + (c.status === "zero" ? "Observed, <b>zero detections</b>" : "<b>" + c.total + "</b> detections") + "<br><span class='tt-m'>MODIS " + c.modis + " · VIIRS " + c.viirs + " (raw detections)</span>" + (c.status === "detections" ? "<br><span class='tt-m'>Harmonized: " + fmt(r ? r[1] : 0, 1) + " cell-days</span>" : "") + (c.high ? "<br><b style='color:#ff8a8a'>Unusually high</b> <span class='tt-m'>z = " + fmt(c.z, 1) + " vs 28 days</span>" : ""));
            } else if (!covered(y, doy, rng)) showTip(e, "<b>" + iso + "</b><br>No satellite coverage in the data");
            else showTip(e, "<b>" + iso + "</b><br>Harmonized activity: <b>" + fmt(r ? r[1] : 0, 1) + "</b> cell-days");
        });
        svg.addEventListener("mouseleave", hideTip);
        svg.addEventListener("click", function (e) {
            var t = e.target; if (!t.dataset || !t.dataset.doy) return;
            state.day = state.day === +t.dataset.doy ? null : +t.dataset.doy; state.dayPage = 1; renderHeatmap(); loadCells(); loadDay(false);
        });
        $("heat-legend").innerHTML = '<span class="lg-i"><i class="sw nodata"></i>No data</span><span class="lg-i"><i class="sw zero"></i>Observed, zero detections</span><span>Fewer</span>' + [1, 2, 3, 4, 5].map(function (i) { return '<i class="sw c' + i + '"></i>'; }).join("") + "<span>More</span><span class='lg-i'><i class='sw high'></i>Unusually high day</span><span class='lg-note'>colour = harmonized cell-days, 98th percentile = " + fmt(max, 0) + "</span>";
        setText("heat-title", "Burning activity in " + y);
    }

    /* ------------------------------------------- per-day calendar, day detail, compare */
    function loadYear() {
        if (!URLS.calendar) return;
        var y = state.year, my = reqId;
        fetch(URLS.calendar + "?" + aoiParams() + "&start=" + y + "-01-01&end=" + y + "-12-31")
            .then(function (r) { return r.json(); })
            .then(function (c) { if (my !== reqId || state.year !== y || !c.days) return; c.year = y; state.cal = c; renderHeatmap(); renderMonthly(); loadDay(false); })
            .catch(function () { state.cal = null; });
    }
    function renderMonthly() {
        var c = state.cal; if (!c) return;
        var rows = MONTHS.map(function (n, i) {
            var m = c.months[state.year + "-" + pad(i + 1)] || { modis: 0, viirs: 0, active_days: 0, zero_days: 0, no_data_days: 0, high_days: 0 };
            return "<tr><th scope='row'>" + n + "</th><td>" + m.modis + "</td><td>" + m.viirs + "</td><td>" + m.active_days + "</td><td>" + m.zero_days + "</td><td>" + m.no_data_days + "</td><td>" + m.high_days + "</td></tr>";
        }).join("");
        $("month-table").innerHTML = "<thead><tr><th>" + state.year + "</th><th>MODIS detections</th><th>VIIRS detections</th><th>Days with detections</th><th>Observed, zero</th><th>No data</th><th>Unusually high</th></tr></thead><tbody>" + rows + "</tbody>";
        var s = c.summary;
        setText("cal-summary", s.active_days + " days with detections · " + s.zero_days + " observed with none · " + s.no_data_days + " without data · " + s.high_days + " unusually high");
        setText("cal-method", c.method.status + ". High day: " + c.method.high_day + ".");
    }
    function loadDay(more) {
        var host = $("day-body"), panel = $("day-panel");
        if (!state.day) { panel.hidden = true; return; }
        panel.hidden = false;
        var iso = isoFromDoy(state.year, state.day);
        if (!more) { state.dayPage = 1; host.innerHTML = "<p class='empty-note'>Loading " + iso + "…</p>"; }
        fetch(URLS.day + "?" + aoiParams() + "&date=" + iso + "&page=" + state.dayPage + "&page_size=50")
            .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || "failed"); return j; }); })
            .then(function (d) {
                if (state.day === null || isoFromDoy(state.year, state.day) !== iso) return;
                var label = { detections: "Days with detections", zero: "Observed, zero detections", no_data: "No data for this day" }[d.status];
                var head = "<div class='day-head'><strong>" + iso + "</strong><span class='badge " + (d.status === "no_data" ? "badge-planned" : "badge-live") + "'>" + label + "</span><span>MODIS " + d.modis + " · VIIRS " + d.viirs + "</span><span class='tt-m'>observed by: " + (d.covered.length ? d.covered.join(", ") : "none") + "</span>" + (d.total ? "<a class='primary-button small' href='" + URLS.map + "?" + aoiParams() + "&start=" + iso + "&end=" + iso + "'>Open this day on the map</a>" : "") + "</div>";
                var rows = d.results.map(function (r) {
                    return "<tr><td>" + r.time_utc + "</td><td>" + esc(r.sensor) + "</td><td>" + esc(r.satellite) + "</td><td>" + r.lat.toFixed(4) + ", " + r.lon.toFixed(4) + "</td><td>" + (r.frp_mw === undefined ? "-" : r.frp_mw) + "</td><td>" + (r.confidence === undefined ? "-" : esc(r.confidence)) + "</td><td>" + esc(r.daynight || "-") + "</td><td>" + esc(r.product || r.category) + "</td></tr>";
                }).join("");
                state.dayRows = (more ? state.dayRows : "") + rows;
                var table = state.dayRows ? "<div class='table-scroll'><table class='data-table'><thead><tr><th>UTC</th><th>Sensor</th><th>Satellite</th><th>Lat, lon</th><th>FRP MW</th><th>Conf.</th><th>D/N</th><th>Source</th></tr></thead><tbody>" + state.dayRows + "</tbody></table></div>" : "<p class='empty-note'>No detections recorded for this day.</p>";
                var next = d.page < d.pages ? "<button type='button' class='link-btn' id='day-more'>Load more (" + d.total + " in total)</button>" : "";
                host.innerHTML = head + table + next;
                var mb = $("day-more"); if (mb) mb.addEventListener("click", function () { state.dayPage++; loadDay(true); });
            }).catch(function (e) { host.innerHTML = "<p class='fc-error'>" + esc(e.message) + "</p>"; });
    }
    function runCompare() {
        var a = $("cmp-a").value, b = $("cmp-b").value, ms = $("cmp-from").value, me = $("cmp-to").value;
        if (!a || !b) return;
        var mmdd = function (m, last) { m = +m; var d = last ? new Date(Date.UTC(2001, m, 0)).getUTCDate() : 1; return pad(m) + "-" + pad(d); };
        var s0 = mmdd(ms, false), e0 = mmdd(me, true);
        if (+ms > +me) { $("cmp-out").innerHTML = "<p class='fc-error'>The first month must not come after the last month.</p>"; return; }
        var q = aoiParams() + "&start=" + a + "-" + s0 + "&end=" + a + "-" + e0 + "&compare_start=" + b + "-" + s0 + "&compare_end=" + b + "-" + e0;
        $("cmp-out").innerHTML = "<p class='empty-note'>Comparing…</p>";
        fetch(URLS.compare + "?" + q).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || "failed"); return j; }); }).then(function (d) {
            var A = d.a.summary, B = d.b.summary;
            function row(label, x, y) { return "<tr><th scope='row'>" + label + "</th><td>" + x + "</td><td>" + y + "</td></tr>"; }
            var chg = function (v) { return v === null ? "n/a" : (v > 0 ? "+" : "") + v + " %"; };
            $("cmp-out").innerHTML = "<div class='table-scroll'><table class='data-table'><thead><tr><th>" + MONTHS[+ms - 1] + "–" + MONTHS[+me - 1] + "</th><th>" + a + "</th><th>" + b + "</th></tr></thead><tbody>" +
                row("MODIS detections", A.modis, B.modis) + row("VIIRS detections", A.viirs, B.viirs) + row("Days with detections", A.active_days, B.active_days) + row("Observed, zero detections", A.zero_days, B.zero_days) + row("Days without data", A.no_data_days, B.no_data_days) + row("Unusually high days", A.high_days, B.high_days) +
                row("Change in MODIS / VIIRS", chg(d.change.modis), chg(d.change.viirs)) + "</tbody></table></div><p class='chart-note'>" + esc(d.note) + "</p>";
        }).catch(function (e) { $("cmp-out").innerHTML = "<p class='fc-error'>" + esc(e.message) + "</p>"; });
    }

    /* ------------------------------------------------------ generic line chart */
    function lineChart(host, o) {
        var W = 900, H = o.height || 270, L = 46, R = 92, T = 14, B = 30, w = W - L - R, h = H - T - B;
        var ymax = 0;
        o.series.forEach(function (s) { s.values.forEach(function (v) { if (v !== null && v > ymax) ymax = v; }); });
        if (o.band) o.band.hi.forEach(function (v) { if (v > ymax) ymax = v; });
        ymax = niceMax(ymax || 1);
        var n = o.n, X = function (i) { return L + (n <= 1 ? 0 : i / (n - 1) * w); }, Y = function (v) { return T + h - v / ymax * h; };
        var p = ['<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(o.label) + '" class="lc">'];
        (o.shades || []).forEach(function (s) { p.push('<rect x="' + X(s[0]) + '" y="' + T + '" width="' + Math.max(2, X(s[1]) - X(s[0])) + '" height="' + h + '" class="shade"/>'); });
        for (var g = 0; g <= 4; g++) { var gv = ymax * g / 4, gy = Y(gv); p.push('<line x1="' + L + '" x2="' + (W - R) + '" y1="' + gy + '" y2="' + gy + '" class="grid"/><text x="' + (L - 8) + '" y="' + (gy + 4) + '" text-anchor="end" class="axis-t">' + fmt(gv, ymax < 10 ? 1 : 0) + "</text>"); }
        for (var i = 0; i < n; i++) { var lab = o.xTick(i); if (lab) p.push('<text x="' + X(i) + '" y="' + (H - 10) + '" text-anchor="middle" class="axis-t">' + lab + "</text>"); }
        if (o.band) {
            var up = o.band.hi.map(function (v, i) { return X(i) + "," + Y(v); }), dn = o.band.lo.map(function (v, i) { return X(i) + "," + Y(v); }).reverse();
            p.push('<polygon points="' + up.concat(dn).join(" ") + '" class="band"/>');
        }
        (o.markers || []).forEach(function (m) { p.push('<line x1="' + X(m.i) + '" x2="' + X(m.i) + '" y1="' + T + '" y2="' + (T + h) + '" class="marker"/><text x="' + (X(m.i) + 5) + '" y="' + (T + 11) + '" class="axis-t">' + esc(m.label) + "</text>"); });
        var labels = [];
        o.series.forEach(function (s) {
            var segs = [], cur = [];
            s.values.forEach(function (v, i) { if (v === null) { if (cur.length) segs.push(cur); cur = []; } else cur.push(X(i) + "," + Y(v)); });
            if (cur.length) segs.push(cur);
            segs.forEach(function (sg) { p.push('<polyline points="' + sg.join(" ") + '" fill="none" stroke="' + s.color + '" stroke-width="' + (s.w || 2) + '" stroke-linejoin="round" stroke-linecap="round"' + (s.dash ? ' stroke-dasharray="' + s.dash + '"' : "") + "/>"); });
            var li = -1; s.values.forEach(function (v, i) { if (v !== null) li = i; });
            if (li >= 0 && s.label) labels.push({ x: X(li) + 8, y: Y(s.values[li]) + 4, t: s.label });
        });
        labels.sort(function (a, b) { return a.y - b.y; });
        for (var q = 1; q < labels.length; q++) if (labels[q].y - labels[q - 1].y < 14) labels[q].y = labels[q - 1].y + 14;
        labels.forEach(function (l) { p.push('<text x="' + l.x + '" y="' + l.y + '" class="dl" fill="currentColor">' + esc(l.t) + "</text>"); });
        p.push('<line class="xhair" y1="' + T + '" y2="' + (T + h) + '" x1="0" x2="0" hidden/><rect class="hit" x="' + L + '" y="' + T + '" width="' + w + '" height="' + h + '" fill="transparent"/></svg>');
        host.innerHTML = p.join("");
        var svg = host.querySelector("svg"), xh = svg.querySelector(".xhair"), hit = svg.querySelector(".hit");
        hit.addEventListener("mousemove", function (e) {
            var r = svg.getBoundingClientRect(), px = (e.clientX - r.left) / r.width * W;
            var i = Math.max(0, Math.min(n - 1, Math.round((px - L) / w * (n - 1))));
            xh.setAttribute("x1", X(i)); xh.setAttribute("x2", X(i)); xh.removeAttribute("hidden"); showTip(e, o.tip(i));
        });
        hit.addEventListener("mouseleave", function () { xh.setAttribute("hidden", ""); hideTip(); });
    }
    function niceMax(v) { var e = Math.pow(10, Math.floor(Math.log10(v))), f = v / e; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e; }
    function legend(host, items) { host.innerHTML = items.map(function (i) { return '<span class="lg"><svg width="26" height="10" aria-hidden="true"><line x1="0" y1="5" x2="26" y2="5" stroke="' + i.color + '" stroke-width="' + (i.w || 2.5) + '"' + (i.dash ? ' stroke-dasharray="' + i.dash + '"' : "") + '/></svg>' + esc(i.name) + "</span>"; }).join(""); }

    /* ---------------------------------------------------------- climatology */
    function weeklyOf(year) {
        var dm = dailyMap(year), rng = coverageRange(), arr = [];
        for (var b = 0; b < 52; b++) {
            var sum = 0, ok = true, len = binDays(year, b);
            for (var k = 0; k < len; k++) { var doy = b * 7 + 1 + k; if (!covered(year, doy, rng)) { ok = false; break; } if (dm[doy]) sum += dm[doy][1]; }
            arr.push(ok ? sum / len * 7 : null);
        }
        return arr;
    }
    function renderClimatology() {
        var d = state.data, bins = d.climatology.bins, y = state.year;
        var mean = bins.map(function (b) { return b.mean; });
        var lo = bins.map(function (b) { return Math.max(0, b.mean - b.std); }), hi = bins.map(function (b) { return b.mean + b.std; });
        var yr = weeklyOf(y);
        var shades = d.critical_periods.map(function (p) { return [mdToBin(p.start_md), mdToBin(p.end_md)]; });
        lineChart($("clim-chart"), {
            n: 52, label: "Typical weekly burning activity compared with " + y, band: { lo: lo, hi: hi },
            shades: shades,
            series: [{ name: "Typical", color: C_MODIS, values: mean, w: 2, dash: "5 4", label: "Typical" }, { name: String(y), color: C_HARM, values: yr, w: 2.8, label: String(y) }],
            xTick: function (i) { var m = new Date(Date.UTC(2001, 0, i * 7 + 1)).getUTCMonth(), pm = i === 0 ? -1 : new Date(Date.UTC(2001, 0, (i - 1) * 7 + 1)).getUTCMonth(); return m !== pm ? MONTHS[m] : null; },
            tip: function (i) {
                var b = bins[i], v = yr[i], z = (v !== null && b.std > 0) ? (v - b.mean) / Math.max(b.std, Math.sqrt(Math.max(b.mean, 1))) : null;
                return "<b>Week of " + isoFromDoy(y, i * 7 + 1) + "</b><br>Typical: <b>" + fmt(b.mean, 1) + "</b> ± " + fmt(b.std, 1) + " (" + b.n + " yrs)<br>" + y + ": <b>" + (v === null ? "no data" : fmt(v, 1)) + "</b>" + (z !== null ? "<br><span class='tt-m'>z = " + fmt(z, 1) + "</span>" : "");
            }
        });
        legend($("clim-legend"), [{ name: "Typical week (all years) ± 1 sd", color: C_MODIS, dash: "5 4", w: 2 }, { name: "Selected year " + y, color: C_HARM, w: 3 }, { name: "Critical period (shaded)", color: "#ff7b3a", w: 6 }]);
        setText("clim-note", d.climatology.baseline_years + " complete years form the baseline. Values are MODIS-equivalent active cell-days per week.");
    }

    /* ------------------------------------------------------- validation chart */
    function renderValidation() {
        var d = state.data, years = d.years, pts = [];
        years.forEach(function (y) { var m = d.monthly[String(y)]; if (m) for (var k = 0; k < 12; k++) pts.push({ y: y, m: k, mo: m.modis[k], vi: m.viirs[k], ha: m.harmonized[k] }); });
        var cov = d.coverage, viirsStart = cov.VIIRS ? cov.VIIRS[0] : null, markers = [];
        if (viirsStart && cov.MODIS) { var vy = +viirsStart.slice(0, 4), vm = +viirsStart.slice(5, 7) - 1, idx = pts.findIndex(function (p) { return p.y === vy && p.m === vm; }); if (idx > 0) markers.push({ i: idx, label: "VIIRS starts" }); }
        function zeroNull(arr, ok) { return arr.map(function (v, i) { return ok(i) ? v : null; }); }
        var modisOk = function (i) { return !cov.MODIS || (pts[i].y + "-" + pad(pts[i].m + 1)) >= cov.MODIS[0].slice(0, 7) && (pts[i].y + "-" + pad(pts[i].m + 1)) <= cov.MODIS[1].slice(0, 7); };
        var viirsOk = function (i) { return !!cov.VIIRS && (pts[i].y + "-" + pad(pts[i].m + 1)) >= cov.VIIRS[0].slice(0, 7) && (pts[i].y + "-" + pad(pts[i].m + 1)) <= cov.VIIRS[1].slice(0, 7); };
        lineChart($("valid-chart"), {
            n: pts.length, label: "Monthly raw MODIS and VIIRS cell-days compared with the harmonized series", markers: markers, height: 290,
            series: [
                { name: "MODIS raw", color: C_MODIS, values: zeroNull(pts.map(function (p) { return p.mo; }), modisOk), w: 1.8, dash: "6 4", label: "MODIS" },
                { name: "VIIRS raw", color: C_VIIRS, values: zeroNull(pts.map(function (p) { return p.vi; }), viirsOk), w: 1.8, dash: "2 3", label: "VIIRS" },
                { name: "Harmonized", color: C_HARM, values: pts.map(function (p) { return p.ha; }), w: 2.8, label: "Harmonized" }
            ],
            xTick: function (i) { return (pts[i].m === 0 && pts[i].y % 2 === 0) ? String(pts[i].y) : null; },
            tip: function (i) { var p = pts[i]; return "<b>" + MONTHS[p.m] + " " + p.y + "</b><br>MODIS raw: " + p.mo + "<br>VIIRS raw: " + p.vi + "<br>Harmonized: <b>" + fmt(p.ha, 1) + "</b>"; }
        });
        legend($("valid-legend"), [{ name: "MODIS raw (cell-days)", color: C_MODIS, dash: "6 4", w: 2 }, { name: "VIIRS raw (cell-days)", color: C_VIIRS, dash: "2 3", w: 2.5 }, { name: "Harmonized (MODIS-equivalent)", color: C_HARM, w: 3 }]);
    }

    /* --------------------------------------------------- anomalies / periods */
    function renderAnomalies() {
        var rows = state.data.anomalies, body = $("anom-body");
        if (!rows.length) { body.innerHTML = '<tr><td colspan="5" class="empty">No unusual week found for this area with the current settings.</td></tr>'; return; }
        body.innerHTML = rows.slice(0, 12).map(function (a, i) {
            return '<tr class="' + (a.level === "extreme" ? "ext" : "") + '"><td>' + a.start + " → " + a.end.slice(5) + "</td><td>" + fmt(a.value, 1) + "</td><td>" + fmt(a.baseline, 1) + "</td><td>" + fmt(a.z, 1) + '</td><td><button type="button" class="link-btn" data-i="' + i + '">View ' + a.year + "</button></td></tr>";
        }).join("");
        body.querySelectorAll("button").forEach(function (b) {
            b.addEventListener("click", function () { var a = rows[+b.dataset.i]; state.year = a.year; $("year-select").value = a.year; state.day = null; renderAll(); $("calendar").scrollIntoView({ behavior: "smooth" }); });
        });
    }
    function renderPeriods() {
        var host = $("periods"), ps = state.data.critical_periods;
        if (!ps.length) { host.innerHTML = '<p class="empty-note">No clear fire season in this area.</p>'; return; }
        host.innerHTML = ps.map(function (p, i) { return '<div class="period"><span class="badge badge-planned">' + (i === 0 ? "Main season" : "Secondary") + "</span><strong>" + mdLabel(p.start_md) + " → " + mdLabel(p.end_md) + "</strong><small>" + p.weeks + " weeks · typical " + fmt(p.typical_weekly, 1) + " cell-days/week · peak week of " + mdLabel(p.peak_start_md) + "</small></div>"; }).join("");
    }
    function renderTable() {
        var m = state.data.monthly[String(state.year)]; if (!m) return;
        var rows = MONTHS.map(function (n, i) { return "<tr><th scope='row'>" + n + "</th><td>" + m.modis[i] + "</td><td>" + m.viirs[i] + "</td><td>" + fmt(m.harmonized[i], 1) + "</td></tr>"; }).join("");
        $("year-table").innerHTML = "<thead><tr><th>Month " + state.year + "</th><th>MODIS raw</th><th>VIIRS raw</th><th>Harmonized</th></tr></thead><tbody>" + rows + "</tbody>";
    }

    /* ----------------------------------------------------------------- map */
    /* The map lives inside #fc-body, which stays display:none until the first data
       response arrives. Leaflet measures its container once, when it is created, so a
       map built inside a hidden parent caches a 0x0 size and only ever requests a few
       tiles. The map is therefore created only once its container has a real size, and
       re-measured whenever that size changes (unhide, resize, layout shift, tab/page
       restore). */
    function boxHasSize() { return !!mapBox && mapBox.clientWidth > 0 && mapBox.clientHeight > 0; }

    function addBaseLayer(kind) {
        if (baseLayer) { map.removeLayer(baseLayer); baseLayer = null; }
        tileOk = 0; tileErr = 0;
        baseLayer = L.tileLayer(TILES[kind][0], { maxZoom: 19, keepBuffer: 4, updateWhenIdle: false, attribution: TILES[kind][1] });
        baseLayer.on("tileload", function () { tileOk++; });
        baseLayer.on("tileerror", function () {
            tileErr++;
            // Primary basemap unreachable (blocked host, outage): switch once to OpenStreetMap.
            if (kind === "esri" && !usedFallbackTiles && tileOk === 0 && tileErr >= 4) { usedFallbackTiles = true; addBaseLayer("osm"); }
            else if (tileOk === 0 && tileErr >= 4) setText("map-caption", "Basemap tiles could not be loaded. Check your connection or try again.");
        });
        baseLayer.addTo(map);
    }

    function refreshMap() {
        mapRaf = 0;
        if (!map) { ensureMap(); return; }
        if (!boxHasSize()) return;          // hidden right now: nothing to measure, wait for the next size change
        map.invalidateSize({ animate: false });
    }
    function scheduleRefresh() { if (!mapRaf) mapRaf = window.requestAnimationFrame(refreshMap); }

    function ensureMap() {
        if (map || !mapBox || !window.L || !boxHasSize()) return;
        if (mapBox._leaflet_id) return;     // container already owned by a Leaflet map
        map = L.map(mapBox, { zoomControl: true, attributionControl: true, preferCanvas: true }).setView([31.5, -7], 5);
        addBaseLayer("esri");
        if (window.getComputedStyle(map.getPane("tilePane")).position !== "absolute") {
            setText("map-caption", "The Leaflet stylesheet did not load, so the map cannot lay out its tiles. Reload the page.");
        }
        cellRenderer = L.canvas({ padding: 0.3 });
        cellLayer = L.layerGroup().addTo(map);
        map.on("zoomend", function () { if (cellData && (cellPixelWidth() >= CELL_MIN_PX) !== cellsAsSquares) drawCells(); });
        var start = null;
        map.on("mousedown", function (e) { if (!state.drawing) return; start = e.latlng; if (aoiRect) aoiRect.setBounds([start, start]); });
        map.on("mousemove", function (e) { if (state.drawing && start && aoiRect) aoiRect.setBounds([start, e.latlng]); });
        map.on("mouseup", function (e) {
            if (!state.drawing || !start) return;
            var a = L.latLngBounds(start, e.latlng); start = null; endDraw();
            if (Math.abs(a.getNorth() - a.getSouth()) < 0.05 || Math.abs(a.getEast() - a.getWest()) < 0.05) { showError("Area too small. Drag a larger rectangle."); drawAoi(false); return; }
            setAoi({ south: a.getSouth(), west: a.getWest(), north: a.getNorth(), east: a.getEast() }, false);
        });
        drawAoi(true);
        if (state.data && state.data.has_data && state.year) loadCells();   // data arrived before the map could exist
        scheduleRefresh();
    }

    function destroyMap() {
        if (mapObserver) { mapObserver.disconnect(); mapObserver = null; }
        if (mapRaf) { window.cancelAnimationFrame(mapRaf); mapRaf = 0; }
        if (map) { map.remove(); map = null; }
        aoiRect = null; cellLayer = null; cellRenderer = null; baseLayer = null; mapHasFittedCells = false; cellData = null; cellsAsSquares = null;
    }

    function initMap() {
        var box = mapBox = $("fc-map");
        if (!window.L) { box.innerHTML = '<p class="map-fallback">The map could not be loaded (Leaflet is unavailable). The calendar and charts still work.</p>'; return; }
        ensureMap();
        if (window.ResizeObserver) { mapObserver = new ResizeObserver(scheduleRefresh); mapObserver.observe(box); }
        window.addEventListener("resize", scheduleRefresh);
        window.addEventListener("orientationchange", scheduleRefresh);
        window.addEventListener("load", scheduleRefresh);                 // late CSS / fonts change layout
        document.addEventListener("visibilitychange", function () { if (!document.hidden) scheduleRefresh(); });
        window.addEventListener("pageshow", function (e) { if (e.persisted) scheduleRefresh(); });   // back/forward cache restore
        window.addEventListener("pagehide", function (e) { if (!e.persisted) destroyMap(); });
    }
    function endDraw() { state.drawing = false; if (map) { map.dragging.enable(); map.getContainer().classList.remove("drawing"); } $("btn-draw").classList.remove("on"); $("btn-draw").textContent = "Draw area on map"; }
    function drawAoi(fit) {
        if (!map) return;
        var b = [[state.aoi.south, state.aoi.west], [state.aoi.north, state.aoi.east]];
        if (aoiRect) aoiRect.setBounds(b); else aoiRect = L.rectangle(b, { color: "#4aa8ff", weight: 2, fillOpacity: 0.06, interactive: false }).addTo(map);
        if (fit) map.fitBounds(b, { padding: [20, 20] });
    }
    /* A 5 km cell is only 1-3 screen pixels wide at national zoom, so a plain rectangle is effectively
       invisible there. Below CELL_MIN_PX the cell is drawn as a small marker of fixed size at its centre
       (same colour, same tooltip); at higher zoom it is drawn as the true 5 km square. */
    var CELL_MIN_PX = 8;
    function cellPixelWidth() { return cellData && map ? cellData.grid * 256 * Math.pow(2, map.getZoom()) / 360 : 0; }
    function cellTooltip(c) {
        return "<strong>Active 5 km cell</strong><br>" + c.days + " active day" + (c.days > 1 ? "s" : "") +
            "<br>Fire radiative power: " + fmt(c.frp, 1) + " MW<br>Sensor: " +
            (c.sensors === "MV" ? "MODIS + VIIRS" : c.sensors === "M" ? "MODIS" : "VIIRS");
    }
    function drawCells() {
        if (!map || !cellLayer || !cellData) return;
        cellLayer.clearLayers();
        var half = cellData.grid / 2, squares = cellPixelWidth() >= CELL_MIN_PX;
        cellsAsSquares = squares;
        cellData.cells.forEach(function (c) {
            var lv = Math.max(1, level(c.days, cellData.max)), color = RAMP[Math.max(2, lv)];
            var shape = squares
                ? L.rectangle([[c.lat - half, c.lon - half], [c.lat + half, c.lon + half]],
                    { renderer: cellRenderer, color: "#6b2418", weight: 0.7, opacity: 0.9, fillColor: color, fillOpacity: 0.82, interactive: true })
                : L.circleMarker([c.lat, c.lon],
                    { renderer: cellRenderer, radius: lv >= 4 ? 6 : 4.5, color: "#6b2418", weight: 1.2, opacity: 0.95, fillColor: color, fillOpacity: 0.92, interactive: true });
            shape.bindTooltip(cellTooltip(c));
            shape.addTo(cellLayer);
        });
    }
    function loadCells() {
        if (!map) return;
        var y = state.year, start = y + "-01-01", end = y + "-12-31", label = String(y);
        if (state.day) { start = end = isoFromDoy(y, state.day); label = start; }
        var my = reqId;
        fetch(URLS.cells + "?" + aoiParams() + "&start=" + start + "&end=" + end, {
            headers: { Accept: "application/json" }
        })
            .then(function (r) {
                return r.json().then(function (d) {
                    if (!r.ok) throw new Error(d.error || ("HTTP " + r.status));
                    return d;
                });
            })
            .then(function (d) {
                if (my !== reqId) return;
                if (!Array.isArray(d.cells)) throw new Error("The fire-cells API returned an unexpected response.");
                cellLayer.clearLayers();
                var valid = d.cells.filter(function (c) {
                    return Number.isFinite(Number(c.lat)) && Number.isFinite(Number(c.lon)) &&
                        Number.isFinite(Number(c.days)) && Number(c.lat) >= -90 && Number(c.lat) <= 90 &&
                        Number(c.lon) >= -180 && Number(c.lon) <= 180;
                });
                var max = valid.reduce(function (m, c) { return Math.max(m, Number(c.days)); }, 1);
                var grid = Number(d.grid_deg) > 0 ? Number(d.grid_deg) : 0.05;
                cellData = { cells: valid.map(function (c) { return { lat: Number(c.lat), lon: Number(c.lon), days: Number(c.days), frp: c.frp, sensors: c.sensors }; }), grid: grid, max: max };
                drawCells();
                var bounds = cellData.cells.map(function (c) { return [c.lat, c.lon]; });
                if (valid.length && !mapHasFittedCells) {
                    map.fitBounds(L.latLngBounds(bounds), { padding: [28, 28], maxZoom: 8 });
                    mapHasFittedCells = true;
                }
                setText("map-caption", valid.length ? label + ": " + Number(d.total_cells || valid.length).toLocaleString() +
                    " active 5 km cell" + (Number(d.total_cells || valid.length) > 1 ? "s" : "") +
                    (d.truncated ? ", showing the most active " + valid.length : "") : label + ": no fire detections in this area");
            })
            .catch(function (err) {
                if (my === reqId) setText("map-caption", "Fire layer unavailable: " + err.message);
            });
    }

    /* ------------------------------------------------------------------ export */
    function exportCsv() {
        var d = state.data, rows = ["date,harmonized_cell_days,modis_cell_days,viirs_cell_days"];
        Object.keys(d.daily).sort().forEach(function (y) { d.daily[y].forEach(function (r) { rows.push(isoFromDoy(+y, r[0]) + "," + r[1] + "," + r[2] + "," + r[3]); }); });
        var a = document.createElement("a"), a2 = state.aoi;
        a.href = URL.createObjectURL(new Blob([rows.join("\n") + "\n"], { type: "text/csv" }));
        a.download = "polaris_burning_activity_" + a2.south + "_" + a2.west + "_" + a2.north + "_" + a2.east + ".csv";
        document.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    }

    /* ------------------------------------------------------------------ wiring */
    function wire() {
        var sel = $("aoi-preset");
        Object.keys(PRESETS).forEach(function (k) { var o = document.createElement("option"); o.value = k; o.textContent = PRESETS[k][0]; sel.appendChild(o); });
        var c = document.createElement("option"); c.value = "custom"; c.textContent = "Custom area"; sel.appendChild(c);
        sel.addEventListener("change", function () { var p = PRESETS[sel.value]; if (p) setAoi({ south: p[1], west: p[2], north: p[3], east: p[4] }, true); });
        $("aoi-form").addEventListener("submit", function (e) {
            e.preventDefault();
            var a = {}; ["south", "west", "north", "east"].forEach(function (k) { a[k] = parseFloat($("aoi-" + k).value); });
            if (!Object.keys(a).every(function (k) { return isFinite(a[k]); }) || !validAoi(a)) { showError("Enter valid coordinates: south < north (-90 to 90) and west < east (-180 to 180)."); return; }
            setAoi(a, true);
        });
        $("conf-low").addEventListener("change", function () { state.minConf = this.checked ? "l" : "n"; load(); });
        $("btn-draw").addEventListener("click", function () {
            if (!map) return showError("The map is not available, use the coordinate fields instead.");
            if (state.drawing) { endDraw(); return; }
            state.drawing = true; map.dragging.disable(); map.getContainer().classList.add("drawing");
            this.classList.add("on"); this.textContent = "Drag a rectangle…";
        });
        $("year-select").addEventListener("change", function () { state.year = +this.value; state.day = null; renderAll(); });
        $("year-prev").addEventListener("click", function () { var i = state.data.years.indexOf(state.year); if (i > 0) { state.year = state.data.years[i - 1]; $("year-select").value = state.year; state.day = null; renderAll(); } });
        $("year-next").addEventListener("click", function () { var i = state.data.years.indexOf(state.year); if (i < state.data.years.length - 1) { state.year = state.data.years[i + 1]; $("year-select").value = state.year; state.day = null; renderAll(); } });
        $("btn-csv").addEventListener("click", exportCsv);
        $("scope-select").addEventListener("change", function () { state.scope = this.value; load(); });
        $("sensor-select").addEventListener("change", function () { state.sensor = this.value; load(); });
        $("cmp-run").addEventListener("click", runCompare);
        $("btn-detections").addEventListener("click", function () { window.location = this.dataset.url + "?" + aoiParams(); });
        $("area-name").addEventListener("change", pushUrl);
        $("btn-clear-day").addEventListener("click", function () { state.day = null; renderHeatmap(); loadCells(); loadDay(false); });
        document.addEventListener("keydown", function (e) { if (e.key === "Escape" && state.drawing) { endDraw(); drawAoi(false); } });
        window.addEventListener("scroll", hideTip, { passive: true });
    }

    window.PLEIADES_LIVE = { aoi: function () { return state.aoi; }, reload: load };
    state.aoi = readQuery();
    wire();
    initMap();
    syncInputs();
    drawAoi(true);
    load();
})();
