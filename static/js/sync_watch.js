/* PLEIADES: shows the scheduled NASA FIRMS synchronisation status and refreshes the page data when new observations
   were imported. Polls GET /api/fire/sync/status/ every minute. Needs #live-bar (data-sync-url) and, optionally,
   window.PLEIADES_LIVE = { aoi(), reload() } from the page script. */
(function () {
    "use strict";
    var bar = document.getElementById("live-bar");
    if (!bar || !bar.dataset.syncUrl) return;
    var el = document.getElementById("sync-status"), POLL = 60000, started = false, lastRun = null;
    function utc(s) { var d = new Date(s); return isNaN(d) ? s : d.toISOString().replace("T", " ").slice(0, 16) + " UTC"; }
    function aoiQuery() {
        var a = window.PLEIADES_LIVE && window.PLEIADES_LIVE.aoi && window.PLEIADES_LIVE.aoi();
        return a ? "?south=" + a.south + "&west=" + a.west + "&north=" + a.north + "&east=" + a.east : "";
    }
    function text(d) {
        var parts = ["Automatic NASA check every " + Math.round(d.interval_seconds / 60) + " min."];
        if (!d.last_run) { parts.push("No scheduled check has run yet on this server."); return parts.join(" "); }
        parts.push(d.last_success_at ? "Last successful check: " + utc(d.last_success_at) + "." : "No successful check yet.");
        if (d.last_run.status !== "ok" && d.last_run.status !== "partial" && d.last_run.status !== "running") parts.push("Latest check status: " + d.last_run.status + ".");
        if (d.newest_observation_utc) parts.push("Newest observation stored: " + utc(d.newest_observation_utc) + " (satellite acquisition time).");
        if (d.last_new_data_at) parts.push("New data last imported: " + utc(d.last_new_data_at) + ".");
        if (d.next_check_utc) parts.push((d.overdue ? "Next check overdue (was due " : "Next check due about ") + utc(d.next_check_utc) + (d.overdue ? ")." : "."));
        var sk = Object.keys(d.skipped_families || {});
        if (sk.length) parts.push("Not requested because NASA no longer delivers new data: " + sk.join(", ") + ".");
        return parts.join(" ");
    }
    function tick() {
        if (document.hidden) return;
        fetch(bar.dataset.syncUrl + aoiQuery(), { headers: { Accept: "application/json" }, credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.error) return;
                if (el) el.textContent = text(d);
                if (started && d.last_new_data_run !== lastRun && d.last_new_data_run) {
                    window.dispatchEvent(new CustomEvent("pleiades:newdata", { detail: d }));
                    if (window.PLEIADES_LIVE && window.PLEIADES_LIVE.reload) window.PLEIADES_LIVE.reload();
                }
                started = true; lastRun = d.last_new_data_run;
            }).catch(function () {});
    }
    tick(); setInterval(tick, POLL);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) tick(); });
})();
