import json
import math
import os
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

DB = Path(os.getenv("GPS_DB_PATH", str(Path(__file__).with_name("gpsplatform.db"))))
INGEST_URL = os.getenv("GPS_INGEST_URL", "").strip()
INGEST_TOKEN = os.getenv("GPS_INGEST_TOKEN", "").strip()
_LAST_CLEANUP = 0.0
_CLEANUP_LOCK = threading.Lock()


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def dec(x, d, lon=False):
    n = 3 if lon else 2
    v = float(x[:n]) + float(x[n:]) / 60
    return -v if d in ("S", "W") else v


def parse(t):
    p = t.strip().split(",")
    if len(p) < 11 or not p[0].startswith("*HQ") or p[4] != "A":
        raise ValueError
    return p[1], dec(p[5], p[6]), dec(p[7], p[8], True), round(float(p[9] or 0) * 1.852, 2), float(p[10] or 0)


def setting_float(c, key, default):
    try:
        r = c.execute("SELECT value FROM system_settings WHERE key=?", (key,)).fetchone()
        return float(r["value"]) if r else float(default)
    except Exception:
        return float(default)


def distance_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, [a["latitude"], a["longitude"], b["latitude"], b["longitude"]])
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2-lon1)/2)**2
    return 6371008.8 * 2 * math.asin(min(1, math.sqrt(h)))


def refresh_pending_immobilize(c, d):
    req = c.execute("SELECT * FROM immobilize_requests WHERE device_pk=? AND status='pending_stop' ORDER BY id DESC LIMIT 1", (d["id"],)).fetchone()
    if not req:
        return
    pts = c.execute("SELECT latitude,longitude,speed,acc,created_at FROM gps_data WHERE device_id=? ORDER BY id DESC LIMIT 3", (d["device_id"],)).fetchall()
    if len(pts) < 2:
        return
    try:
        min_seconds = setting_float(c, "stop_min_seconds", 20)
        max_speed = setting_float(c, "stop_max_speed_kmh", 1)
        max_drift = setting_float(c, "stop_max_drift_m", 25)
        span = (datetime.fromisoformat(pts[0]["created_at"]) - datetime.fromisoformat(pts[-1]["created_at"])).total_seconds()
        known_acc = [x["acc"] for x in pts if x["acc"] is not None]
        stopped = (span >= min_seconds and all((x["speed"] or 0) <= max_speed for x in pts)
                   and (not known_acc or not any(int(x) == 1 for x in known_acc))
                   and distance_m(pts[0], pts[-1]) <= max_drift)
    except Exception:
        stopped = False
    if stopped:
        c.execute("UPDATE immobilize_requests SET status='ready_for_provider',ready_at=CURRENT_TIMESTAMP WHERE id=?", (req["id"],))
        c.execute("INSERT INTO service_audit(user_id,username_snapshot,device_pk,action,result) VALUES(?,?,?,?,?)",
                  (req["user_id"], req["username_snapshot"], d["id"], "vehicle_stop_request", "ready_for_provider"))


def point_in_polygon(lat, lon, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        yi, xi = poly[i]
        yj, xj = poly[j]
        hit = ((xi > lon) != (xj > lon)) and (lat < (yj-yi) * (lon-xi) / ((xj-xi) or 1e-12) + yi)
        if hit:
            inside = not inside
        j = i
    return inside


def cleanup_old_gps(c):
    global _LAST_CLEANUP
    with _CLEANUP_LOCK:
        now = time.time()
        if now - _LAST_CLEANUP < 3600:
            return
        days = max(1, int(setting_float(c, "history_retention_days", 90)))
        c.execute(f"DELETE FROM gps_data WHERE created_at < datetime('now','-{days} days')")
        _LAST_CLEANUP = now


def post_to_web(did, lat, lon, spd, head, raw_text):
    payload = json.dumps({
        "device_id": did,
        "latitude": lat,
        "longitude": lon,
        "speed": spd,
        "speed_unit": "kmh",
        "heading": head,
        "raw_data": raw_text,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if INGEST_TOKEN:
        headers["Authorization"] = f"Bearer {INGEST_TOKEN}"
    req = urllib.request.Request(INGEST_URL, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as response:
        if response.status >= 300:
            raise RuntimeError(f"HTTP {response.status}")


def save_local(did, lat, lon, spd, head, raw_text):
    c = db()
    d = c.execute("SELECT * FROM devices WHERE device_id=?", (did,)).fetchone()
    if not d or d["service_status"] in ("final", "temporary") or not d["user_id"]:
        c.close()
        return False
    if d["subscription_end"] and date.fromisoformat(d["subscription_end"]) < date.today():
        c.close()
        return False
    c.execute("INSERT INTO gps_data(device_id,latitude,longitude,speed,heading,acc,raw_data) VALUES(?,?,?,?,?,?,?)",
              (did, lat, lon, spd, head, None, raw_text))
    refresh_pending_immobilize(c, d)
    for f in c.execute("""SELECT g.* FROM geofences g JOIN geofence_devices gd ON gd.geofence_id=g.id
                          WHERE gd.device_pk=? AND g.user_id=? AND g.is_active=1""", (d["id"], d["user_id"])).fetchall():
        try:
            poly = json.loads(f["polygon_json"])
        except Exception:
            poly = []
        inside = len(poly) >= 3 and point_in_polygon(lat, lon, poly)
        kind = f'geofence:{f["id"]}:{"in" if inside else "out"}'
        last = c.execute("""SELECT kind FROM notifications WHERE user_id=? AND device_pk=? AND kind LIKE ?
                            ORDER BY id DESC LIMIT 1""", (d["user_id"], d["id"], f'geofence:{f["id"]}:%')).fetchone()
        if not last or last["kind"] != kind:
            ok = (inside and f["alert_type"] in ("enter", "both")) or ((not inside) and f["alert_type"] in ("exit", "both"))
            if ok:
                c.execute("""INSERT INTO notifications(user_id,device_pk,kind,title,message) VALUES(?,?,?,?,?)""",
                          (d["user_id"], d["id"], kind, "تنبيه منطقة",
                           f'المركبة {d["platform_id"]} {"دخلت" if inside else "خرجت من"} منطقة {f["name"]}'))
    cleanup_old_gps(c)
    c.commit()
    c.close()
    return True


def handle(s, a):
    try:
        raw = s.recv(4096)
        if not raw:
            return
        raw_text = raw.decode(errors="ignore")
        try:
            did, lat, lon, spd, head = parse(raw_text)
        except Exception:
            return
        try:
            if INGEST_URL:
                post_to_web(did, lat, lon, spd, head, raw_text)
                print("Forwarded", did, lat, lon, spd)
            elif save_local(did, lat, lon, spd, head, raw_text):
                print("Saved", did, lat, lon, spd)
        except urllib.error.HTTPError as e:
            print("Gateway HTTP error:", e.code, e.read().decode(errors="ignore"))
        except Exception as e:
            print("Gateway error:", e)
    finally:
        s.close()


srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("0.0.0.0", int(os.getenv("GPS_TCP_PORT", "8090"))))
srv.listen(50)
print("GPS server listening on", os.getenv("GPS_TCP_PORT", "8090"))
if INGEST_URL:
    print("HTTP bridge enabled ->", INGEST_URL)
while True:
    s, a = srv.accept()
    threading.Thread(target=handle, args=(s, a), daemon=True).start()
