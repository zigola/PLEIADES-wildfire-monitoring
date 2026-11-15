/* PLEIADES: "Same date, every year" panel of the Burning Activity Calendar.
   Uses GET /api/fire/same-date/ (server-side aggregation). A date that was never imported reads "not imported",
   never 0. Only real detections appear here: this table contains no estimate. */
(function () {
    "use strict";
    var panel = document.getElementById("same-date-panel");
    if (!panel) return;
    var $ = function (id) { return document.getElementById(id); };
    function esc(s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
    function f(v, d) { return v === null || v === undefined ? "Not available" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d }); }
    $("sd-date").value = new Date().toISOString().slice(0, 10);

    function query() {
        var a = window.PLEIADES_LIVE && window.PLEIADES_LIVE.aoi && window.PLEIADES_LIVE.aoi();
        var q = new URLSearchParams({ date: $("sd-date").value, window: $("sd-window").value });
        if (a) ["south", "west", "north", "east"].forEach(function (k) { q.set(k, a[k]); });
        var sensor = $("sensor-select") && $("sensor-select").value; if (sensor) q.set("sensor", sensor);
        var scope = $("scope-select") && $("scope-select").value; if (scope) q.set("scope", scope);
        q.set("min_conf", $("conf-low") && $("conf-low").checked ? "l" : "n");
        if ($("sd-sat").value) q.set("satellite", $("sd-sat").value);
        return q.toString();
    }
    function cell(s) {
        if (!s) return "<td colspan=\"3\">-</td>";
        if (s.status === "no_data") return "<td colspan=\"3\" class=\"muted\">not imported (unknown, not zero)</td>";
        var tag = s.status === "partial" ? " <small>(partly imported)</small>" : "";
        if (!s.detections) return "<td>0" + tag + "</td><td>-</td><td>-</td>";
        return "<td>" + f(s.detections, 0) + tag + "</td><td>" + (s.frp ? f(s.frp.mean_mw, 1) + " MW" : "Not available") + "</td><td>" + (s.frp ? f(s.frp.max_mw, 1) + " MW" : "Not available") + "</td>";
    }
    function run() {
        var err = $("sd-error"); err.hidden = true;
        if (!/^\d{4}-\d{2}-\d{2}$/.test($("sd-date").value)) { err.textContent = "Enter a valid date."; err.hidden = false; return; }
        fetch(panel.dataset.url + "?" + query(), { headers: { Accept: "application/json" } })
            .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || "Request failed"); return j; }); })
            .then(function (d) {
                var sensors = d.sensors;
                if (!d.years.length) { $("sd-table").innerHTML = "<tbody><tr><td class=\"empty\">No stored data for this area yet. Import history in Data management.</td></tr></tbody>"; $("sd-note").textContent = d.note; return; }
                var head = "<tr><th rowspan=\"2\">Year</th><th rowspan=\"2\">Date</th>" + sensors.map(function (s) { return "<th colspan=\"3\">" + esc(s) + "</th>"; }).join("") + "</tr><tr>" +
                    sensors.map(function () { return "<th>Detections</th><th>FRP mean</th><th>FRP max</th>"; }).join("") + "</tr>";
                var body = d.years.slice().reverse().map(function (y) {
                    return "<tr><th scope=\"row\">" + y.year + (y.this_year ? " <small>(selected year)</small>" : "") + "</th><td>" + esc(y.date) + "</td>" + sensors.map(function (s) { return cell(y.sensors[s]); }).join("") + "</tr>";
                }).join("");
                $("sd-table").innerHTML = "<thead>" + head + "</thead><tbody>" + body + "</tbody>";
                $("sd-note").innerHTML = esc(d.note) + ' For today, <a href="' + esc(panel.dataset.todayPage) + '?date=' + esc(d.date) + '">see verified observations or a labelled estimate</a>.';
            })
            .catch(function (e) { err.textContent = "Could not load: " + e.message; err.hidden = false; });
    }
    $("sd-run").addEventListener("click", run);
    window.addEventListener("pleiades:newdata", function () { if ($("sd-table").innerHTML) run(); });
})();
