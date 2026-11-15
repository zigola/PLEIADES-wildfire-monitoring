/* PLEIADES: year quick-select for the map filters. Years come from GET /api/fire/history/years/ (what is really stored
   for the selected area), so a year is offered only when detections exist. Choosing one fills From/To and applies.
   Also shows a one-line "today" status under the map: verified observations, an estimate, or unavailable. */
(function () {
    "use strict";
    var root = document.getElementById("sat-app");
    if (!root || root.dataset.page !== "map" || !window.SAT) return;
    var S = window.SAT, start = document.getElementById("f-start"), end = document.getElementById("f-end"), form = document.getElementById("sat-filters");
    var base = (root.dataset.detectionsUrl || "").replace(/detections\/?$/, "");
    if (!start || !end || !form || !base) return;
    var lab = document.createElement("label"); lab.className = "f";
    lab.innerHTML = '<span>Year</span><select id="f-year"><option value="">Custom dates</option></select>';
    start.parentNode.parentNode.insertBefore(lab, start.parentNode);
    var sel = lab.querySelector("select");

    function fillYears() {
        fetch(base + "history/years/?" + S.params({}), { headers: { Accept: "application/json" } })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (!d.years) return;
                var cur = (start.value && end.value && start.value.slice(0, 4) === end.value.slice(0, 4) && start.value.slice(5) === "01-01" && end.value.slice(5) === "12-31") ? start.value.slice(0, 4) : "";
                sel.innerHTML = '<option value="">Custom dates</option>' + d.years.slice().reverse().map(function (y) {
                    return '<option value="' + y.year + '">' + y.year + " (" + y.total.toLocaleString() + ")</option>";
                }).join("");
                sel.value = cur;
            }).catch(function () {});
    }
    sel.addEventListener("change", function () {
        if (!sel.value) return;
        start.value = sel.value + "-01-01"; end.value = sel.value + "-12-31";
        form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
    });
    form.addEventListener("submit", function () { setTimeout(fillYears, 400); });
    fillYears();

    var strip = document.getElementById("today-strip");
    if (strip) {
        var a = S.state, q = "south=" + a.south + "&west=" + a.west + "&north=" + a.north + "&east=" + a.east;
        var page = (document.querySelector("a[href*='/today/']") || {}).pathname || "";
        fetch(base + "today/?" + q, { headers: { Accept: "application/json" } }).then(function (r) { return r.json(); }).then(function (d) {
            if (!d.primary_category) return;
            var msg = { A: "Today (UTC): verified NASA observations are stored for this area.", C: "Today (UTC): NASA has not published observations for this area yet. A labelled statistical estimate is available.", D: "Today (UTC): no NASA observation and no reliable estimate." }[d.primary_category];
            strip.textContent = msg + " ";
            if (page) { var l = document.createElement("a"); l.href = page + "?date=" + d.date; l.textContent = "Details"; strip.appendChild(l); }
            strip.hidden = false;
        }).catch(function () {});
    }
})();
