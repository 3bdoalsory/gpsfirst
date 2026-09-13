import socket, sqlite3, threading, math, json
from pathlib import Path
from datetime import date, datetime, timedelta
DB=Path(__file__).with_name("gpsplatform.db")
def db(): c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c
def dec(x,d,lon=False):
    n=3 if lon else 2;v=float(x[:n])+float(x[n:])/60
    return -v if d in ("S","W") else v
def parse(t):
    p=t.strip().split(",")
    if len(p)<11 or not p[0].startswith("*HQ") or p[4]!="A": raise ValueError
    return p[1],dec(p[5],p[6]),dec(p[7],p[8],True),round(float(p[9] or 0)*1.852,2),float(p[10] or 0)
def distance_m(a,b):
    lat1,lon1,lat2,lon2=map(math.radians,[a["latitude"],a["longitude"],b["latitude"],b["longitude"]])
    h=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371008.8*2*math.asin(min(1,math.sqrt(h)))
def refresh_pending_immobilize(c,d):
    req=c.execute("SELECT * FROM immobilize_requests WHERE device_pk=? AND status='pending_stop' ORDER BY id DESC LIMIT 1",(d["id"],)).fetchone()
    if not req:return
    pts=c.execute("SELECT latitude,longitude,speed,created_at FROM gps_data WHERE device_id=? ORDER BY id DESC LIMIT 3",(d["device_id"],)).fetchall()
    if len(pts)<2:return
    try:
        span=(datetime.fromisoformat(pts[0]["created_at"])-datetime.fromisoformat(pts[-1]["created_at"])).total_seconds()
        stopped=span>=20 and all((x["speed"] or 0)<=1 for x in pts) and distance_m(pts[0],pts[-1])<=25
    except Exception: stopped=False
    if stopped:
        c.execute("UPDATE immobilize_requests SET status='ready_for_provider',ready_at=CURRENT_TIMESTAMP WHERE id=?",(req["id"],))
        c.execute("INSERT INTO service_audit(user_id,username_snapshot,device_pk,action,result) VALUES(?,?,?,?,?)",(req["user_id"],req["username_snapshot"],d["id"],"vehicle_stop_request","ready_for_provider"))
def point_in_polygon(lat,lon,poly):
    inside=False; j=len(poly)-1
    for i in range(len(poly)):
        yi,xi=poly[i]; yj,xj=poly[j]
        hit=((xi>lon)!=(xj>lon)) and (lat < (yj-yi)*(lon-xi)/((xj-xi) or 1e-12)+yi)
        if hit: inside=not inside
        j=i
    return inside
def handle(s,a):
    try:
        raw=s.recv(4096)
        if not raw:return
        try: did,lat,lon,spd,head=parse(raw.decode(errors="ignore"))
        except:return
        c=db();d=c.execute("SELECT * FROM devices WHERE device_id=?",(did,)).fetchone()
        if not d or d["service_status"] in ("final","temporary") or not d["user_id"]:c.close();return
        if d["subscription_end"] and date.fromisoformat(d["subscription_end"])<date.today():c.close();return
        c.execute("INSERT INTO gps_data(device_id,latitude,longitude,speed,heading,raw_data) VALUES(?,?,?,?,?,?)",
                  (did,lat,lon,spd,head,raw.decode(errors="ignore")))
        refresh_pending_immobilize(c,d)
        for f in c.execute("""SELECT g.* FROM geofences g JOIN geofence_devices gd ON gd.geofence_id=g.id
                              WHERE gd.device_pk=? AND g.user_id=? AND g.is_active=1""",(d["id"],d["user_id"])).fetchall():
            try: poly=json.loads(f["polygon_json"])
            except Exception: poly=[]
            inside=len(poly)>=3 and point_in_polygon(lat,lon,poly)
            kind=f'geofence:{f["id"]}:{"in" if inside else "out"}'
            last=c.execute("""SELECT kind FROM notifications WHERE user_id=? AND device_pk=? AND kind LIKE ?
                              ORDER BY id DESC LIMIT 1""",
                           (d["user_id"],d["id"],f'geofence:{f["id"]}:%')).fetchone()
            if not last or last["kind"]!=kind:
                ok=(inside and f["alert_type"] in("enter","both")) or ((not inside) and f["alert_type"] in("exit","both"))
                if ok:
                    c.execute("""INSERT INTO notifications(user_id,device_pk,kind,title,message)
                                 VALUES(?,?,?,?,?)""",
                              (d["user_id"],d["id"],kind,"تنبيه منطقة",
                               f'الجهاز {d["platform_id"]} {"دخل" if inside else "خرج من"} منطقة {f["name"]}'))
        c.commit();c.close();print("Saved",did,lat,lon,spd)
    finally:s.close()
# Keep only the most recent 90 days of GPS history.
try:
    c=db(); c.execute("DELETE FROM gps_data WHERE created_at < datetime('now','-90 days')"); c.commit(); c.close()
except Exception as e: print("GPS retention cleanup skipped:",e)

srv=socket.socket();srv.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);srv.bind(("0.0.0.0",8090));srv.listen(50)
print("GPS server listening on 8090")
while True:
    s,a=srv.accept();threading.Thread(target=handle,args=(s,a),daemon=True).start()
