"""Spatial analysis: density, repeated activity, MODIS-vs-VIIRS comparison and ST-DBSCAN.

ST-DBSCAN parameters (defaults) and why:
  eps_km    = 2.0   about two MODIS pixels, several VIIRS pixels: detections of one fire front
                    and adjacent pixels fall inside it, unrelated fires tens of km away do not.
  eps_hours = 24    chains the several daily overpasses (Terra ~10:30, Aqua/SNPP ~13:30, NOAA-20
                    ~13:10, night passes) so a burning event persisting over days stays one cluster.
  min_pts   = 3     a single pixel is common noise (sun glint, small agricultural fire); three
                    detections give a minimal spatio-temporal footprint.
Clusters are groups of thermal-anomaly detections that are close in space and time. They are NOT
confirmed wildfires: they can be agricultural burning, gas flares, industry or volcanoes.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date, timedelta

from satellite.harmonization.pipeline import obs_minutes

GRID_DEG = 0.05
MAX_CLUSTER_POINTS = 30000


def _km_xy(lat, lon):
    return (111.32 * lon * math.cos(math.radians(lat)), 110.57 * lat)


def st_dbscan(points, eps_km=2.0, eps_hours=24.0, min_pts=3):
    """points: list of (id, lat, lon, minutes). Returns (labels {id: cluster or -1}, n_clusters).

    Neighbourhood: planar distance in km (equirectangular, fine for eps <= 10 km) AND
    |dt| <= eps_hours. Grid hashing keeps it near O(n).
    """
    eps_min = eps_hours * 60
    xy = {}
    grid = defaultdict(list)
    for pid, lat, lon, t in points:
        x, y = _km_xy(lat, lon)
        xy[pid] = (x, y, t)
        grid[(math.floor(x / eps_km), math.floor(y / eps_km), math.floor(t / eps_min))].append(pid)

    def neighbours(pid):
        x, y, t = xy[pid]
        cx, cy, ct = math.floor(x / eps_km), math.floor(y / eps_km), math.floor(t / eps_min)
        out = []
        for i in (cx - 1, cx, cx + 1):
            for j in (cy - 1, cy, cy + 1):
                for k in (ct - 1, ct, ct + 1):
                    for q in grid.get((i, j, k), ()):
                        qx, qy, qt = xy[q]
                        if abs(qt - t) <= eps_min and math.hypot(qx - x, qy - y) <= eps_km:
                            out.append(q)
        return out

    labels, cluster = {}, 0
    for pid, *_ in points:
        if pid in labels:
            continue
        nb = neighbours(pid)
        if len(nb) < min_pts:
            labels[pid] = -1
            continue
        labels[pid] = cluster
        queue = [q for q in nb if q != pid]
        while queue:
            q = queue.pop()
            if labels.get(q, -1) == -1:
                labels[q] = cluster
                qn = neighbours(q)
                if len(qn) >= min_pts:
                    queue.extend(x for x in qn if x not in labels or labels[x] == -1)
        cluster += 1
    return labels, cluster


def clusters(qs, eps_km=2.0, eps_hours=24.0, min_pts=3, limit=50):
    """Cluster active detections of a queryset. Raises ValueError if the selection is too large."""
    if qs.filter(is_demo=True).exists() and qs.filter(is_demo=False).exists():
        raise ValueError("The selection mixes real NASA and synthetic rows; clusters are only computed within one category.")
    rows = list(qs.order_by().values_list("id", "sensor", "latitude", "longitude", "acq_date", "acq_time", "frp"))
    if len(rows) > MAX_CLUSTER_POINTS:
        raise ValueError(f"{len(rows)} detections selected; clustering is limited to {MAX_CLUSTER_POINTS}. "
                         "Narrow the area or the period.")
    pts = [(r[0], r[2], r[3], obs_minutes(r[4], r[5])) for r in rows]
    info = {r[0]: r for r in rows}
    labels, n = st_dbscan(pts, eps_km, eps_hours, min_pts)
    members = defaultdict(list)
    for pid, lab in labels.items():
        members[lab].append(info[pid])
    out = []
    for lab, recs in members.items():
        if lab == -1:
            continue
        lats, lons = [r[2] for r in recs], [r[3] for r in recs]
        days = sorted({r[4] for r in recs})
        sensors = Counter(r[1] for r in recs)
        frp = {s: round(sum(r[6] or 0 for r in recs if r[1] == s), 1) for s in sensors}
        t0 = min(obs_minutes(r[4], r[5]) for r in recs)
        t1 = max(obs_minutes(r[4], r[5]) for r in recs)
        out.append({"id": lab, "n": len(recs), "modis": sensors.get("MODIS", 0), "viirs": sensors.get("VIIRS", 0),
                    "lat": round(sum(lats) / len(lats), 4), "lon": round(sum(lons) / len(lons), 4),
                    "bbox": [min(lats), min(lons), max(lats), max(lons)],
                    "first": days[0].isoformat(), "last": days[-1].isoformat(), "active_days": len(days),
                    "duration_hours": round((t1 - t0) / 60, 1), "frp_sum_mw": frp,
                    "max_frp_mw": round(max((r[6] or 0) for r in recs), 1)})
    out.sort(key=lambda c: -c["n"])
    return {"clusters": out[:limit], "n_clusters": len(out), "n_points": len(rows),
            "n_noise": sum(1 for v in labels.values() if v == -1),
            "parameters": {"eps_km": eps_km, "eps_hours": eps_hours, "min_pts": min_pts},
            "interpretation": "Clusters are spatio-temporal groups of satellite thermal-anomaly detections, not confirmed "
                              "wildfires. They may be agricultural burning, flares, industrial heat or volcanoes. "
                              "FRP is summed per sensor and never across sensors."}


def density(qs, cell_deg=GRID_DEG, limit=4000):
    """Detections per grid cell per sensor, plus the number of distinct active days."""
    cells = {}
    for sensor, day, lat, lon in qs.order_by().values_list("sensor", "acq_date", "latitude", "longitude").iterator(chunk_size=5000):
        c = (math.floor(lat / cell_deg), math.floor(lon / cell_deg))
        e = cells.setdefault(c, {"MODIS": 0, "VIIRS": 0, "days": set()})
        e[sensor] += 1
        e["days"].add(day)
    rows = sorted(cells.items(), key=lambda kv: -(kv[1]["MODIS"] + kv[1]["VIIRS"]))
    out = [{"lat": round((c[0] + .5) * cell_deg, 4), "lon": round((c[1] + .5) * cell_deg, 4),
            "modis": e["MODIS"], "viirs": e["VIIRS"], "days": len(e["days"])} for c, e in rows[:limit]]
    return {"cells": out, "total_cells": len(rows), "truncated": len(rows) > limit, "cell_deg": cell_deg}


def repeated_activity(dens, min_days=5, limit=20):
    """Cells active on many distinct days. May indicate persistent heat sources (industry, flares),
    recurring agricultural burning, or a long fire, so the list is a screening aid only."""
    rep = [c for c in dens["cells"] if c["days"] >= min_days]
    rep.sort(key=lambda c: -c["days"])
    return {"min_days": min_days, "cells": rep[:limit], "count": len(rep)}


def sensor_cell_comparison(dens):
    """Where do the sensors see activity? Counts of 5 km cells seen by both / only one sensor."""
    both = sum(1 for c in dens["cells"] if c["modis"] and c["viirs"])
    only_m = sum(1 for c in dens["cells"] if c["modis"] and not c["viirs"])
    only_v = sum(1 for c in dens["cells"] if c["viirs"] and not c["modis"])
    xs = [math.log1p(c["modis"]) for c in dens["cells"]]
    ys = [math.log1p(c["viirs"]) for c in dens["cells"]]
    from satellite.harmonize import pearson
    r = pearson(xs, ys)
    n = both + only_m + only_v
    return {"cells_both": both, "cells_modis_only": only_m, "cells_viirs_only": only_v,
            "share_both": round(both / n, 3) if n else None,
            "log_count_correlation": None if r is None else round(r, 3),
            "truncated": dens["truncated"],
            "note": "VIIRS (375 m) detects smaller fires, so cells seen only by VIIRS are expected; "
                    "cells seen only by MODIS are rarer and often reflect overpass timing or cloud."}
