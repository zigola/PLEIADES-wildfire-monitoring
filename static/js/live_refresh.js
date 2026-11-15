/* "Refresh now from NASA FIRMS" button (satellite pages + burning calendar).
   Needs window.PLEIADES_LIVE = { aoi(): {south,west,north,east}, reload() } from the page script. */
(function () {
    "use strict";
    var bar = document.getElementById("live-bar"), btn = document.getElementById("btn-live"), msg = document.getElementById("live-status");
    if (!bar || !btn) return;
    var DAYS = 3;
    function csrf() { var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/); return m ? decodeURIComponent(m[1]) : ""; }
    function say(t) { msg.textContent = t; }
    function aoi() { return (window.PLEIADES_LIVE && window.PLEIADES_LIVE.aoi()) || { south: 20.84, west: -17.06, north: 35.74, east: -1.54 }; }
    function n(v) { return Number(v).toLocaleString(); }
    function utc(s) { var d = new Date(s); return isNaN(d) ? s : d.toISOString().replace("T", " ").slice(0, 16) + " UTC"; }

    function status() {
        var a = aoi(), q = "south=" + a.south + "&west=" + a.west + "&north=" + a.north + "&east=" + a.east;
        return fetch(bar.dataset.statusUrl + "?" + q, { headers: { Accept: "application/json" }, credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.error) return;
                if (!d.configured) { btn.disabled = true; say("No NASA FIRMS MAP_KEY on the server: live refresh is unavailable."); return; }
                if (!d.public) { btn.title = "Operators only"; }
                say((d.last_sync_utc ? "Last live sync of this area: " + utc(d.last_sync_utc) + ". " : "No live sync of this area yet. ") +
                    "Next refresh pulls " + d.window[0] + " to " + d.window[1] + " (UTC).");
            }).catch(function () {});
    }

    btn.addEventListener("click", function () {
        btn.disabled = true; say("Contacting NASA FIRMS, this can take a few seconds…");
        var b = aoi(); b.days = DAYS;
        fetch(bar.dataset.refreshUrl, { method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), Accept: "application/json" }, body: JSON.stringify(b) })
            .then(function (r) { return r.json().catch(function () { return {}; }); })
            .then(function (d) {
                if (d.status === "ok" || d.status === "partial") {
                    say("NASA FIRMS " + d.start + " to " + d.end + ": " + n(d.rows_received) + " detections received, " + n(d.rows_created) + " new." +
                        (d.status === "partial" ? " Some requests failed, try again later." : ""));
                    if (window.PLEIADES_LIVE) window.PLEIADES_LIVE.reload();
                } else { say(d.message || d.error || "Refresh failed."); }
            })
            .catch(function () { say("Could not reach the server."); })
            .then(function () { btn.disabled = false; });
    });
    status();
})();
