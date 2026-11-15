/* PLEIADES satellite module: overview, harmonization, history and data-management pages. */
(function () {
    "use strict";
    var root = document.getElementById("sat-app");
    if (!root || !window.SAT) return;
    var S = window.SAT, $ = S.$, esc = S.esc, fmt = S.fmt, page = root.dataset.page;
    var C = S.colors;

    function kv(host, rows) { host.innerHTML = rows.map(function (r) { return "<div><span>" + esc(r[0]) + "</span><strong>" + r[1] + "</strong></div>"; }).join(""); }
    var CAT = { nasa_standard: "NASA FIRMS, Standard Processing (SP)", nasa_nrt: "NASA FIRMS, Near Real-Time (NRT)", nasa_imported_csv: "NASA FIRMS CSV download", synthetic_demo: "Synthetic demonstration data (not NASA)" };

    /* ------------------------------------------------------------ overview */
    function overview() {
        return S.get("overview").then(function (d) {
            if (!S.banner(d)) return;
            $("k-total").textContent = fmt(d.total); $("k-modis").textContent = fmt(d.modis); $("k-viirs").textContent = fmt(d.viirs);
            $("k-period").textContent = (d.period.start || "-") + " → " + (d.period.end || "-");
            $("k-period-note").textContent = d.data_first ? "data available " + d.data_first + " → " + d.data_last : "no detections in this selection";
            $("k-update").textContent = d.last_update ? S.dt(d.last_update) : "no NASA import yet";
            $("aoi-text").textContent = "S " + d.aoi.south + " W " + d.aoi.west + " N " + d.aoi.north + " E " + d.aoi.east;
            $("source-text").textContent = d.source;
            $("cat-list").innerHTML = Object.keys(d.categories).length ? Object.keys(d.categories).map(function (k) { return '<span class="chip ' + (k === "synthetic_demo" ? "demo" : "nasa") + '">' + esc(CAT[k] || k) + " · " + fmt(d.categories[k]) + "</span>"; }).join("") : '<span class="empty-note">none</span>';
            S.table($("prod-table"), ["Product", "Detections"], d.products.map(function (p) { return ["<code>" + esc(p.id) + "</code> " + esc(p.label), fmt(p.n)]; }), "No product metadata (legacy rows or synthetic data).");
            S.table($("plat-table"), ["Sensor", "Platform", "Detections"], d.platforms.map(function (p) { return [esc(p.sensor), esc(p.platform), fmt(p.n)]; }), "No detections.");
            var q = d.quality, rows = [];
            if (q.records) {
                rows = [["Records", fmt(q.records)], ["Confidence not provided", fmt(q.missing_confidence)], ["Low-confidence detections", fmt(q.low_confidence)],
                    ["FRP missing", fmt(q.missing_frp)], ["Brightness missing", fmt(q.missing_brightness)], ["In MODIS/VIIRS overlap groups", fmt(q.in_overlap_groups)],
                    ["Exact duplicates flagged (all data)", fmt(q.exact_duplicates_flagged)], ["Rows rejected at import (all imports)", fmt(q.rejected_rows)],
                    ["Rows without product metadata", fmt(q.rows_without_product_metadata)]];
            }
            S.table($("qual-table"), ["Indicator", "Value"], rows, "No records.");
        });
    }

    /* ------------------------------------------------------- harmonization */
    function stat(o, unit) { return o ? fmt(o.median, 1) + " (p10 " + fmt(o.p10, 1) + " – p90 " + fmt(o.p90, 1) + ")" + (unit || "") : "n/a"; }
    function harmonization() {
        return Promise.all([S.get("overview"), S.get("harmonization", { max_minutes: $("hm-min").value, max_radius_km: $("hm-rad").value }), S.get("spatial"), S.get("temporal")]).then(function (r) {
            var ov = r[0], h = r[1], sp = r[2], tp = r[3];
            if (!S.banner(ov)) return;
            var M = h.sensors.MODIS, V = h.sensors.VIIRS;
            $("h-total").textContent = fmt(h.unified_total); $("h-dups").textContent = fmt(h.exact_duplicates.flagged);
            $("h-groups").textContent = fmt(h.overlap.groups);
            $("h-groups-note").textContent = h.overlap.groups ? "MODIS and VIIRS detections that likely see the same heat source" : "run the harmonization to compute overlaps";
            $("h-run").textContent = h.last_run ? S.dt(h.last_run.finished_at) : "never run";
            $("h-run-note").textContent = h.last_run ? h.last_run.status + " · " + fmt(h.last_run.stats.rows) + " rows" : "an operator runs it in Data management";
            function conf(c) { return "low " + fmt(c.low) + " · nominal " + fmt(c.nominal) + " · high " + fmt(c.high) + " · n/p " + fmt(c.not_provided); }
            S.table($("h-sensors"), ["Measure", "MODIS", "VIIRS"], [
                ["Detections", fmt(M.n), fmt(V.n)], ["FRP, MW (median, p10–p90)", stat(M.frp_mw), stat(V.frp_mw)],
                ["Brightness, K (MODIS T21 / VIIRS I-4)", stat(M.brightness_k), stat(V.brightness_k)],
                ["Second channel, K (MODIS T31 / VIIRS I-5)", stat(M.brightness_2_k), stat(V.brightness_2_k)],
                ["Pixel size, km (max of scan/track)", stat(M.pixel_km), stat(V.pixel_km)],
                ["Confidence classes", conf(M.confidence), conf(V.confidence)],
                ["Day / night", fmt(M.day_night.D) + " / " + fmt(M.day_night.N), fmt(V.day_night.D) + " / " + fmt(V.day_night.N)]]);
            var o = h.overlap;
            kv($("h-overlap"), [["Exact duplicates flagged", fmt(h.exact_duplicates.flagged)], ["Rule", esc(h.exact_duplicates.rule)],
                ["Overlap groups", fmt(o.groups)], ["MODIS records with a VIIRS neighbour", o.share_modis_with_viirs_neighbour === null ? "n/a" : fmt(o.share_modis_with_viirs_neighbour * 100, 1) + " %"],
                ["VIIRS records with a MODIS neighbour", o.share_viirs_with_modis_neighbour === null ? "n/a" : fmt(o.share_viirs_with_modis_neighbour * 100, 1) + " %"],
                ["Groups within " + o.coincident_groups_within_minutes.minutes + " min", fmt(o.coincident_groups_within_minutes.groups)],
                ["Median time difference", o.median_time_difference_min === null ? "n/a" : fmt(o.median_time_difference_min, 0) + " min"],
                ["Sum VIIRS FRP / MODIS FRP", stat(o.viirs_to_modis_frp_ratio)], ["Match criteria (heuristic defaults)", "≤ " + o.parameters.max_minutes + " min and footprints overlap (radius capped at " + o.parameters.max_radius_km + " km)"], ["Note", esc(o.note)]]);
            var mt = h.matching;
            if (!mt.available) { $("h-match-kpis").innerHTML = ""; $("h-match").innerHTML = '<p class="empty-note">' + esc(mt.reason) + "</p>"; }
            else {
                $("h-match-kpis").innerHTML = [["Input detections", mt.input_total, "MODIS " + fmt(mt.input_modis) + " · VIIRS " + fmt(mt.input_viirs)], ["Matched MODIS", mt.matched_modis, "has a VIIRS neighbour"], ["Matched VIIRS", mt.matched_viirs, "has a MODIS neighbour"],
                    ["Unmatched MODIS", mt.unmatched_modis, "no VIIRS neighbour found"], ["Unmatched VIIRS", mt.unmatched_viirs, "no MODIS neighbour found"], ["Exact duplicates", h.input_summary.exact_duplicates_flagged_not_deleted, "flagged, not deleted"]]
                    .map(function (k) { return '<div class="kpi"><span>' + k[0] + "</span><strong>" + fmt(k[1]) + "</strong><small>" + esc(k[2]) + "</small></div>"; }).join("");
                $("hm-status").textContent = mt.parameter_status.statement;
                $("hm-explain").textContent = "Time window: " + mt.parameter_status.time_window + " Radius: " + mt.parameter_status.radius +
                    (mt.effective_radius_km ? " In this selection the effective radius ranges from " + mt.effective_radius_km.min + " to " + mt.effective_radius_km.max + " km." : "");
                kv($("h-match"), [["Match groups", fmt(mt.match_groups)], ["Rule (heuristic)", esc(mt.parameters.distance_rule) + "; ≤ " + mt.parameters.max_minutes + " min; radius capped at " + mt.parameters.max_radius_km + " km"], ["How to read", esc(mt.reading)]]);
            }
            $("h-notes").innerHTML = h.sensor_notes.map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("");
            var c = sp.sensor_comparison;
            kv($("h-spatial"), [["Cells seen by both", fmt(c.cells_both)], ["Cells seen only by MODIS", fmt(c.cells_modis_only)], ["Cells seen only by VIIRS", fmt(c.cells_viirs_only)],
                ["Share seen by both", c.share_both === null ? "n/a" : fmt(c.share_both * 100, 1) + " %"], ["Correlation of log counts", c.log_count_correlation === null ? "n/a" : fmt(c.log_count_correlation, 2)],
                ["Note", esc(c.note) + (c.truncated ? " (only the most active cells were compared)" : "")]]);
            var hrs = tp.acquisition.hours_utc;
            S.legend($("h-hour-legend"), [{ name: "MODIS", color: C.modis }, { name: "VIIRS", color: C.viirs }]);
            S.bars($("h-hours"), { labels: Array.from({ length: 24 }, function (_, i) { return i + "h"; }), series: [{ name: "MODIS", color: C.modis, values: hrs.MODIS }, { name: "VIIRS", color: C.viirs, values: hrs.VIIRS }], label: "Detections by acquisition hour (UTC)" });
            S.table($("h-plat"), ["Platform", "Detections", "Median acquisition time (UTC)"], tp.acquisition.platforms.map(function (p) { return [esc(p.platform), fmt(p.n), esc(p.median_utc)]; }), "No detections.");
        });
    }

    /* -------------------------------------------------------------- history */
    var tChart = function () { return S.get("temporal", { period: $("t-period").value }).then(function (t) {
        S.bars($("t-chart"), { labels: t.series.map(function (x) { return x.period; }), series: [{ name: "MODIS", color: C.modis, values: t.series.map(function (x) { return x.modis; }) }, { name: "VIIRS", color: C.viirs, values: t.series.map(function (x) { return x.viirs; }) }], label: "Detections over time" });
        return t; }); };
    function anomRows(items) {
        return items.map(function (a) {
            var src = typeof a.sources === "string" ? esc(a.sources) : "MODIS " + a.sources.MODIS + " · VIIRS " + a.sources.VIIRS;
            return [esc(a.period[0] === a.period[1] ? a.period[0] : a.period[0] + " → " + a.period[1]), fmt(a.value, 1), fmt(a.baseline, 1), fmt(a.z, 1), '<span class="badge ' + (a.level === "extreme" ? "badge-planned" : "badge-live") + '">' + esc(a.level) + "</span>",
                '<details><summary>Why flagged</summary><p>' + esc(a.why) + "<br><b>Metric:</b> " + esc(a.metric) + "<br><b>Baseline:</b> " + esc(a.baseline_description) + "<br><b>Threshold:</b> " + esc(a.threshold) + "<br><b>Sources:</b> " + src + "<br><b>Limits:</b> " + esc(a.limitations) + "</p></details>"];
        });
    }
    var AH = ["Period", "Value", "Baseline", "z", "Level", "Explanation"];
    function clusters() {
        return S.get("clusters", { eps_km: $("cl-eps").value, eps_hours: $("cl-h").value, min_pts: $("cl-min").value }).then(function (c) {
            S.table($("cl-table"), ["#", "Detections", "MODIS", "VIIRS", "Centre", "First → last", "Active days", "FRP MW (M / V)"], c.clusters.map(function (k, i) {
                return [i + 1, fmt(k.n), fmt(k.modis), fmt(k.viirs), k.lat + ", " + k.lon, esc(k.first + " → " + k.last), k.active_days, fmt(k.frp_sum_mw.MODIS || 0, 0) + " / " + fmt(k.frp_sum_mw.VIIRS || 0, 0)]; }), "No cluster with these parameters.");
            $("cl-note").textContent = c.n_clusters + " clusters from " + fmt(c.n_points) + " detections (" + fmt(c.n_noise) + " unclustered). " + c.interpretation;
        }).catch(function (e) { $("cl-table").innerHTML = ""; $("cl-note").textContent = e.message; });
    }
    function history() {
        return Promise.all([S.get("overview"), S.get("temporal"), S.get("anomalies").catch(function (e) { return { error: e.message }; }), S.get("spatial")]).then(function (r) {
            var ov = r[0], t = r[1], a = r[2], sp = r[3];
            if (!S.banner(ov)) return;
            S.legend($("hist-legend"), [{ name: "MODIS", color: C.modis }, { name: "VIIRS", color: C.viirs }]);
            S.bars($("y-chart"), { labels: t.yearly.map(function (y) { return String(y.year); }), series: [{ name: "MODIS", color: C.modis, values: t.yearly.map(function (y) { return y.modis; }) }, { name: "VIIRS", color: C.viirs, values: t.yearly.map(function (y) { return y.viirs; }) }], label: "Detections per year" });
            var mn = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
            S.bars($("s-chart"), { labels: mn, series: [{ name: "MODIS", color: C.modis, values: t.seasonal.map(function (m) { return m.modis_mean || 0; }) }, { name: "VIIRS", color: C.viirs, values: t.seasonal.map(function (m) { return m.viirs_mean || 0; }) }], label: "Mean detections per month" });
            S.bars($("t-chart"), { labels: t.series.map(function (x) { return x.period; }), series: [{ name: "MODIS", color: C.modis, values: t.series.map(function (x) { return x.modis; }) }, { name: "VIIRS", color: C.viirs, values: t.series.map(function (x) { return x.viirs; }) }], label: "Detections over time" });
            if (a.error) { $("a-daily").innerHTML = ""; $("a-weekly").innerHTML = ""; $("a-note").textContent = a.error; }
            else {
                S.table($("a-daily"), AH, anomRows(a.daily), "No unusually active day in this period (rolling 28-day baseline).");
                S.table($("a-weekly"), AH, anomRows(a.weekly), "No unusual week: this needs at least 4 other years of data for the same weeks.");
                $("a-note").textContent = a.limitations;
            }
            S.table($("rep-table"), ["Cell centre", "Active days", "MODIS", "VIIRS"], sp.repeated.cells.map(function (c) { return [c.lat + ", " + c.lon, c.days, fmt(c.modis), fmt(c.viirs)]; }), "No cell with repeated activity.");
            return clusters();
        });
    }

    /* ---------------------------------------------------------- data page */
    var timer = null;
    function imports() {
        return fetch(root.dataset.importsUrl).then(function (r) { return r.json(); }).then(function (d) {
            var running = d.imports.some(function (j) { return j.status === "running"; });
            $("imp-status").textContent = running ? "running, refreshing every 5 s" : "up to date";
            S.table($("imp-table"), ["When", "Source", "Product", "Area / period", "Status", "Received", "New", "Dupl.", "Rejected", "Days ok/failed"], d.imports.map(function (j) {
                var rej = j.invalid ? j.invalid + ' <small title="' + esc(JSON.stringify(j.invalid_reasons)) + '">(reasons)</small>' : "0";
                return [esc(S.dt(j.started_at)), esc(j.kind_label), "<code>" + esc(j.product) + "</code>",
                    (j.bbox ? esc(j.bbox.map(function (x) { return Math.round(x * 100) / 100; }).join(", ")) : "n/a") + "<br><small>" + esc((j.start || "?") + " → " + (j.end || "?")) + (j.coverage_declared ? "" : " (inferred)") + "</small>",
                    '<span class="badge ' + (j.status === "success" ? "badge-live" : "badge-planned") + '">' + esc(j.status_label) + "</span>" + (j.message ? '<br><small title="' + esc(j.message) + '">' + esc(j.message.slice(0, 80)) + "</small>" : ""),
                    fmt(j.received), fmt(j.created), fmt(j.duplicates), rej, j.days_ok + " / " + j.days_failed]; }), "No import yet.");
            clearTimeout(timer); if (running) timer = setTimeout(imports, 5000);
        });
    }
    function data() {
        var b = $("btn-key");
        if (b) b.addEventListener("click", function () {
            fetch(root.dataset.keyUrl).then(function (r) { return r.json(); }).then(function (k) { $("key-detail").textContent = k.ok ? "key accepted: " + k.used + "/" + k.limit + " transactions per " + k.interval : (k.message || "key check failed"); });
        });
        return imports();
    }

    /* ------------------------------------------------------------ quality */
    function metric(m, fmtFn) { return m.available ? '<span>' + (fmtFn ? fmtFn(m.value) : fmt(m.value)) + "</span>" : '<span class="na" title="' + esc(m.note) + '">not available</span>'; }
    function quality() {
        return fetch(root.dataset.qualityUrl).then(function (r) { return r.json(); }).then(function (q) {
            var d = q.database, im = q.imports;
            $("q-kpis").innerHTML = [["Total rows", d.total_rows], ["Real NASA observations", d.real_nasa], ["Synthetic demo", d.synthetic_demo], ["MODIS (real)", d.modis], ["VIIRS (real)", d.viirs], ["Duplicates flagged", d.duplicates_flagged]]
                .map(function (k) { return '<div class="kpi"><span>' + k[0] + "</span><strong>" + (k[1].available ? fmt(k[1].value) : '<em class="na">n/a</em>') + "</strong><small>" + esc(k[1].note) + "</small></div>"; }).join("");
            function sec(host, s) {
                S.table($(host), ["Indicator", "Value"], [["Records", metric(s.total)], ["MODIS", metric(s.modis)], ["VIIRS", metric(s.viirs)], ["First acquisition date", metric(s.date_first, esc)], ["Last acquisition date", metric(s.date_last, esc)],
                    ["Days with detections", metric(s.days_with_data)], ["Confidence not provided", metric(s.missing_confidence)], ["Low-confidence class", metric(s.low_confidence)], ["FRP missing", metric(s.missing_frp)],
                    ["Brightness missing", metric(s.missing_brightness)], ["Stored rows with invalid coordinates", metric(s.out_of_range_coordinates)]], "No rows.");
            }
            sec("q-real", q.real); sec("q-demo", q.synthetic);
            S.table($("q-imp"), ["Indicator", "Value"], [["Imports run (NASA API/CSV)", metric(im.count)], ["Last successful NASA API import", metric(im.last_successful_nasa_import, function (v) { return esc(S.dt(v)) + " <small>" + esc(im.last_successful_nasa_import.note) + "</small>"; })],
                ["Rows received", metric(im.rows_received)], ["Rows stored", metric(im.rows_created)], ["Already stored (skipped)", metric(im.rows_duplicate_skipped)], ["Rows rejected by validation", metric(im.rows_rejected)],
                ["Failed imports", metric(im.failed_imports)], ["Partial imports", metric(im.partial_imports)], ["Not run: no API key", metric(im.not_run_no_key)], ["Days that failed", metric(im.failed_days)]]);
            var rs = im.rejected_by_reason; $("q-reasons").innerHTML = rs && Object.keys(rs).length ? Object.keys(rs).map(function (k) { return '<span class="chip">' + esc(k) + " · " + fmt(rs[k]) + "</span>"; }).join("") : '<span class="empty-note">' + (rs === null ? "not available: no import yet" : "none rejected") + "</span>";
            S.table($("q-errors"), ["When", "Product", "Status", "Message"], im.recent_problems.map(function (e) { return [esc(S.dt(e.when)), "<code>" + esc(e.product) + "</code>", esc(e.status), esc(e.message || "-")]; }), "No import problems recorded.");
            $("q-notes").innerHTML = q.notes.map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("");
        });
    }

    var apply = { overview: overview, harmonization: harmonization, history: history, data: function () { return Promise.resolve(); }, quality: function () { return Promise.resolve(); } }[page];
    if (page === "harmonization") $("hm-form").addEventListener("submit", function (e) { e.preventDefault(); S.onApply(); });
    if (page === "history") {
        $("t-period").addEventListener("change", function () { tChart(); });
        $("cl-form").addEventListener("submit", function (e) { e.preventDefault(); clusters(); });
    }
    if (page === "quality") { quality().catch(function (e) { S.error(e.message); }); S.banner = function () { return true; }; fetch(S.API + "overview/").then(function (r) { return r.json(); }).then(function (d) { var b = $("sat-banner"); if (d.data_mode === "empty") { b.className = "data-banner warn"; b.hidden = false; b.textContent = "The database holds no satellite rows yet; every figure below is therefore not available."; } else if (d.data_mode === "demo") { b.className = "data-banner demo"; b.hidden = false; b.textContent = "Only synthetic demonstration data is stored. No real NASA observation is available."; } }); }
    else S.init(apply, {});
    if (page === "data") data();
})();
