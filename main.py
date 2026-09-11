from flask import Flask, render_template, request, redirect, session, flash, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from pathlib import Path
from datetime import date, timedelta, datetime
from functools import wraps
import sqlite3, os, json

app = Flask(__name__)
app.secret_key = os.getenv("GPS_SECRET_KEY", "dev-change-me")
DB = Path(__file__).with_name("gpsplatform.db")

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def login_required(admin=False):
    def deco(fn):
        @wraps(fn)
        def wrap(*a, **kw):
            if "user_id" not in session:
                return redirect("/")
            if admin and session.get("role") != "admin":
                return redirect("/dashboard")
            return fn(*a, **kw)
        return wrap
    return deco

def device_state(d):
    if d["service_status"] == "final":
        return "final"
    if d["service_status"] == "temporary":
        return "temporary"
    if d["subscription_end"]:
        try:
            end = date.fromisoformat(d["subscription_end"])
            if end < date.today():
                return "expired"
            if end <= date.today() + timedelta(days=7):
                return "expiring"
        except ValueError:
            pass
    return "active"

def parse_dmy(value):
    value=(value or "").strip()
    if not value: return None
    for fmt in ("%d/%m/%Y","%Y-%m-%d"):
        try: return datetime.strptime(value,fmt).date().isoformat()
        except ValueError: pass
    return None

@app.template_filter("dmy")
def dmy(value):
    if not value: return "-"
    try:
        return datetime.fromisoformat(str(value).replace("Z","")).strftime("%d/%m/%Y")
    except Exception: return str(value)

@app.template_filter("dmytime")
def dmytime(value):
    if not value: return "-"
    try:
        return datetime.fromisoformat(str(value).replace("Z","")).strftime("%d/%m/%Y %H:%M")
    except Exception: return str(value)

def can_access_device(d):
    if not d:
        return False
    return session.get("role") == "admin" or d["user_id"] == session.get("user_id")

@app.route("/", methods=["GET","POST"])
def login():
    if request.method == "POST":
        c=db()
        u=c.execute("SELECT * FROM users WHERE username=?", (request.form["username"].strip(),)).fetchone()
        c.close()
        if not u or not check_password_hash(u["password_hash"], request.form["password"]):
            flash("اسم المستخدم أو كلمة المرور غير صحيحة", "error")
            return render_template("login.html")
        if not u["is_active"]:
            flash("هذا الحساب موقوف من الإدارة", "error")
            return render_template("login.html")
        session.clear()
        session.update(user_id=u["id"], username=u["username"], role=u["role"])
        # "تذكرني" يجعل جلسة تسجيل الدخول مستمرة بدل أن تنتهي مع إغلاق المتصفح.
        session.permanent = bool(request.form.get("remember"))
        if session.permanent:
            app.permanent_session_lifetime = timedelta(days=30)
        return redirect("/admin" if u["role"]=="admin" else "/dashboard")
    if session.get("user_id"):
        return redirect("/admin" if session.get("role")=="admin" else "/dashboard")
    return render_template("login.html")

@app.get("/logout")
def logout():
    session.clear()
    return redirect("/")

def nmea_to_decimal(value, hemisphere):
    """Convert SinoTrack/NMEA DDMM.MMMM or DDDMM.MMMM to decimal degrees."""
    raw = float(value)
    degrees = int(raw // 100)
    minutes = raw - (degrees * 100)
    decimal = degrees + (minutes / 60.0)
    if hemisphere.upper() in ("S", "W"):
        decimal = -decimal
    return decimal


def parse_sinotrack_hq(packet):
    """Parse the HQ V8 GPS packet fields used by the platform."""
    packet = (packet or "").strip()
    if not packet.startswith("*HQ,"):
        raise ValueError("unsupported_packet")

    parts = packet.rstrip("#").split(",")
    if len(parts) < 12:
        raise ValueError("incomplete_packet")

    device_id = parts[1].strip()
    gps_status = parts[4].strip().upper()
    if gps_status != "A":
        raise ValueError("gps_fix_invalid")

    latitude = nmea_to_decimal(parts[5], parts[6])
    longitude = nmea_to_decimal(parts[7], parts[8])
    speed = float(parts[9] or 0)
    heading = float(parts[10] or 0)

    # The tracker is configured on timezone 0, so this timestamp is UTC-like
    # and is stored in the same ISO format already used by gps_data.
    tracker_time = datetime.strptime(parts[11] + parts[3], "%d%m%y%H%M%S")

    return {
        "device_id": device_id,
        "latitude": latitude,
        "longitude": longitude,
        "speed": speed,
        "heading": heading,
        "created_at": tracker_time.isoformat(timespec="seconds"),
    }


@app.post("/api/tracker")
def receive_tracker():
    """Receive a SinoTrack packet from the TCP gateway, parse it and store GPS data."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="invalid_json"), 400

    raw_packet = str(data.get("raw_packet", "")).strip()
    gateway_device_id = str(data.get("device_id", "")).strip()
    if not raw_packet:
        return jsonify(ok=False, error="raw_packet_required"), 400

    try:
        point = parse_sinotrack_hq(raw_packet)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except Exception as exc:
        print("[TRACKER PARSE ERROR]", repr(exc), raw_packet)
        return jsonify(ok=False, error="packet_parse_failed"), 400

    if gateway_device_id and gateway_device_id != point["device_id"]:
        return jsonify(ok=False, error="device_id_mismatch"), 400

    c = db()
    try:
        device = c.execute(
            "SELECT * FROM devices WHERE device_id=?",
            (point["device_id"],)
        ).fetchone()

        if not device:
            print("[TRACKER] Parsed but device is not registered:", point["device_id"])
            return jsonify(
                ok=True,
                message="tracker_packet_parsed",
                stored=False,
                reason="device_not_registered",
                **point
            ), 200

        state = device_state(device)
        if state in ("expired", "temporary", "final"):
            print("[TRACKER] Packet ignored because device state is", state)
            return jsonify(
                ok=True,
                message="tracker_packet_ignored",
                stored=False,
                reason=state,
                device_id=point["device_id"]
            ), 200

        c.execute(
            """INSERT INTO gps_data(device_id,latitude,longitude,speed,heading,created_at)
               VALUES(?,?,?,?,?,?)""",
            (
                point["device_id"],
                point["latitude"],
                point["longitude"],
                point["speed"],
                point["heading"],
                point["created_at"],
            )
        )
        c.commit()

        print("=" * 70)
        print("[TRACKER] GPS point stored")
        print("Device ID:", point["device_id"])
        print("Latitude:", point["latitude"])
        print("Longitude:", point["longitude"])
        print("Speed:", point["speed"])
        print("Heading:", point["heading"])
        print("Tracker time:", point["created_at"])
        print("=" * 70)

        return jsonify(
            ok=True,
            message="tracker_packet_stored",
            stored=True,
            **point
        ), 200
    except sqlite3.Error as exc:
        c.rollback()
        print("[TRACKER DATABASE ERROR]", repr(exc))
        return jsonify(ok=False, error="database_error"), 500
    finally:
        c.close()


@app.get("/dashboard")
@login_required()
def dashboard():
    if session["role"]=="admin":
        return redirect("/admin")
    c=db()
    rows=c.execute("""
      SELECT d.*,
       (SELECT latitude FROM gps_data g WHERE g.device_id=d.device_id ORDER BY g.id DESC LIMIT 1) latitude,
       (SELECT longitude FROM gps_data g WHERE g.device_id=d.device_id ORDER BY g.id DESC LIMIT 1) longitude,
       (SELECT speed FROM gps_data g WHERE g.device_id=d.device_id ORDER BY g.id DESC LIMIT 1) speed,
       (SELECT heading FROM gps_data g WHERE g.device_id=d.device_id ORDER BY g.id DESC LIMIT 1) heading,
       (SELECT created_at FROM gps_data g WHERE g.device_id=d.device_id ORDER BY g.id DESC LIMIT 1) last_update
      FROM devices d
      WHERE d.user_id=? AND d.service_status!='final'
      ORDER BY d.platform_id
    """,(session["user_id"],)).fetchall()
    notes=c.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 8",
                    (session["user_id"],)).fetchall()
    audits=c.execute("""SELECT a.*,d.platform_id,d.name device_name
                        FROM service_audit a JOIN devices d ON d.id=a.device_pk
                        WHERE a.user_id=? ORDER BY a.id DESC LIMIT 20""",
                     (session["user_id"],)).fetchall()
    fences=c.execute("""SELECT g.id,g.name,g.polygon_json,g.alert_type,g.is_active
                        FROM geofences g
                        WHERE g.user_id=? AND g.is_active=1 ORDER BY g.id""",(session["user_id"],)).fetchall()
    c.close()
    devices=[]
    for x in rows:
        z=dict(x); z["state"]=device_state(x); z["subscription_soon"]=False
        if x["subscription_end"]:
            try: z["subscription_soon"]=(date.fromisoformat(x["subscription_end"])-date.today()).days <= 30
            except ValueError: pass
        devices.append(z)
    fence_data=[]
    for f in fences:
        z=dict(f)
        try: z["polygon"]=json.loads(z.get("polygon_json") or "[]")
        except Exception: z["polygon"]=[]
        fence_data.append(z)
    return render_template("dashboard.html", devices=devices, notes=notes, audits=audits, fences=fence_data)

@app.get("/api/latest/<int:pid>")
@login_required()
def latest(pid):
    c=db(); d=c.execute("SELECT * FROM devices WHERE platform_id=?",(pid,)).fetchone()
    if not can_access_device(d): c.close(); return jsonify(error="forbidden"),403
    st=device_state(d)
    if st in ("expired","temporary","final"): c.close(); return jsonify(available=False,state=st)
    p=c.execute("""SELECT latitude,longitude,speed,heading,created_at FROM gps_data
                   WHERE device_id=? ORDER BY id DESC LIMIT 1""",(d["device_id"],)).fetchone()
    c.close()
    return jsonify(available=bool(p),state=st,**(dict(p) if p else {}))

@app.get("/history/<int:pid>")
@login_required()
def history(pid):
    c=db(); d=c.execute("SELECT * FROM devices WHERE platform_id=?",(pid,)).fetchone(); c.close()
    if not can_access_device(d) or device_state(d) in ("expired","temporary","final"):
        return redirect("/dashboard")
    return render_template("history.html", device=d)

@app.get("/api/history/<int:pid>")
@login_required()
def history_api(pid):
    try:
        s=datetime.fromisoformat(request.args["start"])
        e=datetime.fromisoformat(request.args["end"])
    except:
        return jsonify(error="حدد الفترة"),400
    if e<s or e-s>timedelta(days=90):
        return jsonify(error="الحد الأقصى 90 يومًا"),400
    c=db(); d=c.execute("SELECT * FROM devices WHERE platform_id=?",(pid,)).fetchone()
    if not can_access_device(d) or device_state(d) in ("expired","temporary","final"):
        c.close(); return jsonify(error="forbidden"),403
    rows=c.execute("""SELECT latitude,longitude,speed,heading,created_at FROM gps_data
                      WHERE device_id=? AND created_at BETWEEN ? AND ?
                      ORDER BY created_at""",(d["device_id"],request.args["start"],request.args["end"])).fetchall()
    c.close()
    pts=[dict(r) for r in rows if r["latitude"] is not None and r["longitude"] is not None]
    stops=[]; start=None
    for i,p in enumerate(pts):
        if (p["speed"] or 0)<=1 and start is None: start=i
        if start is not None and ((p["speed"] or 0)>1 or i==len(pts)-1):
            j=i-1 if (p["speed"] or 0)>1 else i
            try:
                t1=datetime.fromisoformat(pts[start]["created_at"]); t2=datetime.fromisoformat(pts[j]["created_at"])
                mins=int((t2-t1).total_seconds()/60)
                if mins>=5:
                    stops.append({"latitude":pts[start]["latitude"],"longitude":pts[start]["longitude"],
                                  "from":pts[start]["created_at"],"to":pts[j]["created_at"],"minutes":mins})
            except: pass
            start=None
    return jsonify(points=pts,stops=stops)

@app.route("/geofences",methods=["GET","POST"])
@login_required()
def geofences():
    if session["role"]=="admin": return redirect("/admin")
    c=db()
    if request.method=="POST":
        try: poly=json.loads(request.form.get("polygon_json","[]"))
        except Exception: poly=[]
        selected=[int(x) for x in request.form.getlist("device_pks") if x.isdigit()]
        allowed={r["id"] for r in c.execute("SELECT id FROM devices WHERE user_id=? AND service_status!='final'",(session["user_id"],)).fetchall()}
        selected=[x for x in selected if x in allowed]
        if len(poly)>=3 and selected:
            cur=c.execute("""INSERT INTO geofences(user_id,device_pk,name,polygon_json,alert_type,sms_enabled)
                             VALUES(?,?,?,?,?,?)""",
                          (session["user_id"],selected[0],request.form["name"],json.dumps(poly),request.form["alert_type"],1 if request.form.get("sms_enabled") else 0))
            fid=cur.lastrowid
            c.executemany("INSERT INTO geofence_devices(geofence_id,device_pk) VALUES(?,?)",[(fid,x) for x in selected])
            c.commit(); flash("تم حفظ الزون بنجاح","ok")
        else: flash("ارسم الزون وحدد مركبة واحدة على الأقل","error")
        c.close(); return redirect("/geofences")
    ds=c.execute("SELECT * FROM devices WHERE user_id=? AND service_status!='final' ORDER BY platform_id",(session["user_id"],)).fetchall()
    fs=c.execute("SELECT * FROM geofences WHERE user_id=? ORDER BY id DESC",(session["user_id"],)).fetchall()
    fence_data=[]
    for row in fs:
        z=dict(row)
        try: z["polygon"]=json.loads(z["polygon_json"])
        except Exception: z["polygon"]=[]
        z["device_pks"]=[r["device_pk"] for r in c.execute("SELECT device_pk FROM geofence_devices WHERE geofence_id=?",(z["id"],)).fetchall()]
        fence_data.append(z)
    c.close()
    return render_template("geofences.html",devices=ds,fences=fence_data)

@app.post("/geofences/<int:fid>/edit")
@login_required()
def geofence_edit(fid):
    c=db(); f=c.execute("SELECT * FROM geofences WHERE id=? AND user_id=?",(fid,session["user_id"])).fetchone()
    if not f: c.close(); return redirect("/geofences")
    try: poly=json.loads(request.form.get("polygon_json","[]"))
    except Exception: poly=[]
    selected=[int(x) for x in request.form.getlist("device_pks") if x.isdigit()]
    allowed={r["id"] for r in c.execute("SELECT id FROM devices WHERE user_id=? AND service_status!='final'",(session["user_id"],)).fetchall()}
    selected=[x for x in selected if x in allowed]
    if len(poly)>=3 and selected:
        c.execute("UPDATE geofences SET device_pk=?,name=?,polygon_json=?,alert_type=?,sms_enabled=?,is_active=? WHERE id=?",
                  (selected[0],request.form["name"],json.dumps(poly),request.form["alert_type"],1 if request.form.get("sms_enabled") else 0,1 if request.form.get("is_active") else 0,fid))
        c.execute("DELETE FROM geofence_devices WHERE geofence_id=?",(fid,))
        c.executemany("INSERT INTO geofence_devices(geofence_id,device_pk) VALUES(?,?)",[(fid,x) for x in selected])
        c.commit(); flash("تم تعديل الزون","ok")
    else: flash("يجب إبقاء مركبة واحدة على الأقل ضمن الزون","error")
    c.close(); return redirect("/geofences")

@app.post("/geofences/<int:fid>/delete")
@login_required()
def geofence_delete(fid):
    c=db(); c.execute("DELETE FROM geofence_devices WHERE geofence_id=?",(fid,)); c.execute("DELETE FROM geofences WHERE id=? AND user_id=?",(fid,session["user_id"])); c.commit(); c.close()
    flash("تم حذف الزون","ok"); return redirect("/geofences")

@app.get("/admin")
@login_required(admin=True)
def admin():
    c=db()
    clients=c.execute("""SELECT u.*,COUNT(d.id) device_count FROM users u
                         LEFT JOIN devices d ON d.user_id=u.id
                         WHERE u.role='client' GROUP BY u.id ORDER BY u.id DESC""").fetchall()
    devices=c.execute("""SELECT d.*,u.username FROM devices d LEFT JOIN users u ON u.id=d.user_id
                         ORDER BY d.platform_id""").fetchall()
    audits=c.execute("""SELECT a.*,d.platform_id,d.name device_name FROM service_audit a
                        JOIN devices d ON d.id=a.device_pk ORDER BY a.id DESC LIMIT 100""").fetchall()
    c.close()
    return render_template("admin.html",clients=clients,devices=devices,audits=audits)

@app.post("/admin/password")
@login_required(admin=True)
def admin_password():
    current=request.form.get("current_password","")
    new=request.form.get("new_password","")
    confirm=request.form.get("confirm_password","")
    c=db(); u=c.execute("SELECT * FROM users WHERE id=? AND role='admin'",(session["user_id"],)).fetchone()
    if not u or not check_password_hash(u["password_hash"],current):
        c.close(); flash("كلمة المرور الحالية غير صحيحة","error"); return redirect("/admin#security")
    if len(new)<4 or new!=confirm:
        c.close(); flash("تأكد من كلمة المرور الجديدة وتأكيدها","error"); return redirect("/admin#security")
    c.execute("UPDATE users SET password_hash=? WHERE id=?",(generate_password_hash(new),u["id"])); c.commit(); c.close()
    flash("تم تغيير كلمة مرور الإدارة","ok"); return redirect("/admin#security")

@app.post("/admin/user")
@login_required(admin=True)
def add_user():
    c=db()
    try:
        c.execute("INSERT INTO users(username,password_hash,role,phone) VALUES(?,?,'client',?)",
                  (request.form["username"].strip(),generate_password_hash(request.form["password"]),
                   request.form.get("phone","").strip()))
        c.commit();flash("تم إنشاء الحساب","ok")
    except sqlite3.IntegrityError: flash("اسم المستخدم موجود مسبقًا","error")
    c.close();return redirect("/admin#accounts")

@app.post("/admin/user/<int:uid>/edit")
@login_required(admin=True)
def edit_user(uid):
    c=db()
    c.execute("UPDATE users SET username=?,phone=? WHERE id=? AND role='client'",
              (request.form["username"].strip(),request.form.get("phone","").strip(),uid))
    if request.form.get("password","").strip():
        c.execute("UPDATE users SET password_hash=? WHERE id=?",
                  (generate_password_hash(request.form["password"]),uid))
    c.commit();c.close();flash("تم تعديل الحساب","ok");return redirect("/admin#accounts")

@app.post("/admin/user/<int:uid>/toggle")
@login_required(admin=True)
def toggle_user(uid):
    c=db();u=c.execute("SELECT is_active FROM users WHERE id=? AND role='client'",(uid,)).fetchone()
    if u:c.execute("UPDATE users SET is_active=? WHERE id=?",(0 if u["is_active"] else 1,uid));c.commit()
    c.close();return redirect("/admin#accounts")

@app.post("/admin/device")
@login_required(admin=True)
def add_device():
    c=db();pid=c.execute("SELECT COALESCE(MAX(platform_id),10000)+1 n FROM devices").fetchone()["n"]
    try:
        c.execute("""INSERT INTO devices(platform_id,device_id,name,plate,user_id,subscription_start,subscription_end)
                     VALUES(?,?,?,?,?,?,?)""",
                  (pid,request.form["device_id"].strip(),request.form["name"].strip(),request.form.get("plate","").strip(),
                   int(request.form["user_id"]) if request.form.get("user_id") else None,
                   parse_dmy(request.form.get("subscription_start")),parse_dmy(request.form.get("subscription_end"))))
        c.commit();flash(f"تمت إضافة الجهاز برقم الشركة {pid}","ok")
    except sqlite3.IntegrityError:flash("Device ID موجود مسبقًا","error")
    c.close();return redirect("/admin#devices")

@app.post("/admin/device/<int:pid>/edit")
@login_required(admin=True)
def edit_device(pid):
    c=db()
    c.execute("""UPDATE devices SET name=?,plate=?,user_id=?,subscription_start=?,subscription_end=?
                 WHERE platform_id=?""",
              (request.form["name"].strip(),request.form.get("plate","").strip(),
               int(request.form["user_id"]) if request.form.get("user_id") else None,
               parse_dmy(request.form.get("subscription_start")),
               parse_dmy(request.form.get("subscription_end")),pid))
    c.commit();c.close();flash("تم تعديل الجهاز","ok");return redirect("/admin#devices")

@app.post("/admin/device/<int:pid>/temporary")
@login_required(admin=True)
def temporary_device(pid):
    c=db(); d=c.execute("SELECT service_status FROM devices WHERE platform_id=?",(pid,)).fetchone()
    if d:
        c.execute("UPDATE devices SET service_status=? WHERE platform_id=?",
                  ("active" if d["service_status"]=="temporary" else "temporary",pid)); c.commit()
    c.close(); return redirect("/admin#devices")

@app.post("/admin/device/<int:pid>/final")
@login_required(admin=True)
def final_device(pid):
    c=db();d=c.execute("SELECT service_status FROM devices WHERE platform_id=?",(pid,)).fetchone()
    if d:
        c.execute("UPDATE devices SET service_status=? WHERE platform_id=?",
                  ("active" if d["service_status"]=="final" else "final",pid));c.commit()
    c.close();return redirect("/admin#devices")

@app.post("/service/<int:pid>")
@login_required()
def service_request(pid):
    c=db();d=c.execute("SELECT * FROM devices WHERE platform_id=?",(pid,)).fetchone()
    if not can_access_device(d) or device_state(d) in ("expired","temporary","final"):
        c.close();return jsonify(error="forbidden"),403
    c.execute("""INSERT INTO service_audit(user_id,username_snapshot,device_pk,action,result)
                 VALUES(?,?,?,?,?)""",
              (session["user_id"],session["username"],d["id"],"vehicle_stop_request","recorded_only"))
    c.commit();c.close()
    return jsonify(ok=True,message="تم تسجيل طلب الخدمة في سجل العمليات")

if __name__=="__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
