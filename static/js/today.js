/* PLEIADES: "Today and estimates" page. Renders the A/B/C/D answer of GET /api/fire/today/.
   A = verified NASA observations, B = historical observations, C = statistical estimate, D = unavailable.
   Estimates are always drawn dashed and labelled; they never carry coordinates or a detection count. */
(function () {
    "use strict";
    var root = document.getElementById("today-app");
    if (!root) return;
    var $ = function (id) { return document.getElementById(id); };
    var PRESETS = {
        tadla: ["Tadla / Beni Mellal", 32.0, -7.2, 33.0, -5.6],
        morocco: ["Morocco & Western Sahara", 20.84, -17.06, 35.74, -1.54],
        north: ["Northern Morocco (Rif, Gharb)", 33.8, -6.8, 35.9, -3.4],
        atlas: ["Middle & High Atlas", 31.0, -8.0, 33.8, -3.5],
        oriental: ["Oriental", 33.0, -3.5, 35.3, -1.2],
        souss: ["Souss-Massa", 29.5, -10.2, 31.0, -8.0]
    };
    var state = { preset: "tadla", date: "", primary: null };
    function esc(s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
    function utc(s) { var d = new Date(s); return isNaN(d) ? esc(s) : d.toISOString().replace("T", " ").slice(0, 16) + " UTC"; }
    function num(v, d) { return (v === null || v === undefined) ? "Not available" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d === undefined ? 2 : d }); }
    function todayUtc() { return new Date().toISOString().slice(0, 10); }

    var sel = $("td-preset");
    Object.keys(PRESETS).forEach(function (k) { var o = document.createElement("option"); o.value = k; o.textContent = PRESETS[k][0]; sel.appendChild(o); });

    function readQuery() {
        var q = new URLSearchParams(location.search);
        if (PRESETS[q.get("area")]) state.preset = q.get("area");
        state.date = /^\d{4}-\d{2}-\d{2}$/.test(q.get("date") || "") ? q.get("date") : todayUtc();
        sel.value = state.preset; $("td-date").value = state.date;
    }
    function bbox() { var p = PRESETS[state.preset]; return { south: p[1], west: p[2], north: p[3], east: p[4] }; }
    window.PLEIADES_LIVE = { aoi: bbox, reload: function () { return load(true); } };
    function showError(m) { var e = $("td-error"); e.textContent = m || ""; e.hidden = !m; }

    function sensorsOf(d, cat) { return d.sensors.filter(function (s) { return s.category === cat; }); }

    function cardA(d) {
        var rows = sensorsOf(d, "A"); if (!rows.length) return "";
        var html = rows.map(function (s) {
            if (s.verified_zero) return '<p class="sensor-line"><strong>' + esc(s.sensor) + "</strong>: queried successfully, 0 detections. " + esc(s.note) + "</p>";
            var o = s.observed || {};
            return '<p class="sensor-line"><strong>' + esc(s.sensor) + "</strong>: " + num(s.detections, 0) + " detections stored" +
                (o.latest_acq_utc ? ", latest acquisition " + utc(o.latest_acq_utc) : "") +
                (o.platforms && o.platforms.length ? ", satellites " + esc(o.platforms.join(", ")) : "") +
                (o.products && o.products.length ? ", products " + esc(o.products.join(", ")) : "") +
                (o.last_imported_at ? ", imported " + utc(o.last_imported_at) : "") + ". " + esc(s.note) + "</p>";
        }).join("");
        return '<div class="cat-card cat-a"><h3><span class="tag">A</span>' + esc(d.labels.A) + "</h3>" + html +
            '<p class="chart-note"><a href="' + esc(root.dataset.mapUrl) + "?" + qs(d.date) + '">Open these detections on the map</a> to inspect every measurement.</p></div>';
    }
    function qs(date) { var a = bbox(); return "south=" + a.south + "&west=" + a.west + "&north=" + a.north + "&east=" + a.east + "&start=" + date + "&end=" + date + "&min_conf=all"; }

    function cardB(d) {
        if (!d.has_historical) return "";
        var years = {}, names = [];
        d.sensors.forEach(function (s) { names.push(s.sensor); s.historical.forEach(function (h) { (years[h.year] = years[h.year] || { date: h.date })[s.sensor] = h.detections; }); });
        var ys = Object.keys(years).sort(function (a, b) { return b - a; });
        var head = "<tr><th>Year</th><th>Date</th>" + names.map(function (n) { return "<th>" + esc(n) + " detections</th>"; }).join("") + "</tr>";
        var body = ys.map(function (y) { return "<tr><td>" + y + "</td><td>" + esc(years[y].date) + "</td>" + names.map(function (n) { return "<td>" + (years[y][n] === undefined ? "Not available" : years[y][n]) + "</td>"; }).join("") + "</tr>"; }).join("");
        return '<div class="cat-card cat-b"><h3><span class="tag">B</span>' + esc(d.labels.B) + '</h3><p class="chart-note">NASA records for the same calendar date in earlier years. These are not current observations.</p>' +
            '<div class="table-scroll"><table class="data-table sd-table"><thead>' + head + "</thead><tbody>" + body + "</tbody></table></div></div>";
    }

    function cardC(d) {
        var rows = sensorsOf(d, "C"); if (!rows.length) return "";
        return rows.map(function (s) {
            var e = s.estimate, i = e.indicators, ci = i.share_of_years_with_detection_95ci;
            var grid = [["Typical detections per day", num(i.mean_detections_per_day, 2)], ["Median", num(i.median_detections_per_day, 2)],
                ["10th to 90th percentile", num(i.p10_detections_per_day, 2) + " to " + num(i.p90_detections_per_day, 2)],
                ["Share of years with a detection", num(i.share_of_years_with_detection * 100, 0) + " % (95 % CI " + num(ci[0] * 100, 0) + " to " + num(ci[1] * 100, 0) + " %)"],
                ["Earlier years used", e.n_years + " (" + e.reference_period[0] + " to " + e.reference_period[1] + ")"],
                ["Historical detections counted", num(e.n_detections, 0)]];
            return '<div class="cat-card cat-c"><h3><span class="tag">C</span>' + esc(d.labels.C) + " · " + esc(s.sensor) + "</h3>" +
                '<p class="not-obs">This is not a satellite detection and does not say that a fire exists. No location is estimated.</p><p>' + esc(s.note) + "</p>" +
                '<div class="est-grid">' + grid.map(function (g) { return "<div><span>" + esc(g[0]) + "</span><strong>" + esc(g[1]) + "</strong></div>"; }).join("") + "</div>" +
                "<p><strong>Method.</strong> " + esc(e.methodology) + "</p><p><strong>Uncertainty.</strong> " + esc(e.uncertainty) + "</p>" +
                "<p><strong>Limitations</strong></p><ul>" + e.limitations.map(function (l) { return "<li>" + esc(l) + "</li>"; }).join("") + "</ul></div>";
        }).join("");
    }

    function cardD(d) {
        var rows = sensorsOf(d, "D"); if (!rows.length) return "";
        return '<div class="cat-card cat-d"><h3><span class="tag">D</span>' + esc(d.labels.D) + "</h3>" + rows.map(function (s) {
            return '<p class="sensor-line"><strong>' + esc(s.sensor) + "</strong>: " + esc(s.reason || "No data.") + "</p>";
        }).join("") + "</div>";
    }

    function render(d) {
        var head = '<div class="fc-panel"><div class="fc-panel-head"><h2>' + esc(d.date) + (d.is_today ? " (today, UTC)" : "") + '</h2><small>' +
            esc(d.primary_category === "A" ? "Verified NASA observations are available" : d.primary_category === "C" ? "No NASA observation stored yet: showing an estimate" : "No NASA observation and no reliable estimate") +
            "</small></div><p class=\"chart-note\">" + esc(d.disclaimer) + "</p></div>";
        $("td-out").innerHTML = head + cardA(d) + cardC(d) + cardD(d) + cardB(d);
    }

    function load(fromSync) {
        showError("");
        var a = bbox(), q = new URLSearchParams({ south: a.south, west: a.west, north: a.north, east: a.east, date: state.date });
        history.replaceState(null, "", location.pathname + "?area=" + state.preset + "&date=" + state.date);
        return fetch(root.dataset.todayUrl + "?" + q.toString(), { headers: { Accept: "application/json" } })
            .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || "Request failed"); return j; }); })
            .then(function (d) {
                var was = state.primary; state.primary = d.primary_category;
                render(d);
                var fl = $("td-flash");
                if (fromSync && was && was !== "A" && d.primary_category === "A") {
                    fl.textContent = "NASA published new observations for this date. They were imported and replace the provisional estimate shown before.";
                    fl.hidden = false;
                } else if (!fromSync) { fl.hidden = true; }
            })
            .catch(function (e) { showError("Could not load: " + e.message); $("td-out").innerHTML = ""; });
    }

    $("td-form").addEventListener("submit", function (e) {
        e.preventDefault();
        var v = $("td-date").value;
        if (!/^\d{4}-\d{2}-\d{2}$/.test(v)) { showError("Enter a valid date."); return; }
        state.preset = sel.value; state.date = v; load(false);
    });
    readQuery(); load(false);
})();
