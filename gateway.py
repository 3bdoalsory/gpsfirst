import json, os, re, socketserver, threading, time, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

LISTEN_HOST=os.getenv("GPS_LISTEN_HOST","0.0.0.0")
LISTEN_PORT=int(os.getenv("GPS_LISTEN_PORT","8090"))
WEB_BASE=os.getenv("GPS_WEB_BASE","https://gpsfirst.onrender.com").rstrip("/")
INGEST_URL=os.getenv("GPS_INGEST_URL",WEB_BASE+"/api/tracker")
TOKEN=os.getenv("GPS_GATEWAY_TOKEN",os.getenv("GPS_INGEST_TOKEN","")).strip()
DEVICE_ID_RE=re.compile(r"^\*HQ,([^,]+),")
RAW_LOG=Path(os.getenv("GPS_RAW_LOG", str(Path(__file__).with_name("raw_tcp.log"))))
RAW_LOG_LOCK=threading.Lock()

def headers():
    h={"Content-Type":"application/json"}
    if TOKEN:h["Authorization"]="Bearer "+TOKEN
    return h

def api(path,method="GET",payload=None,timeout=12):
    data=None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request(WEB_BASE+path,data=data,headers=headers(),method=method)
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read().decode(errors="replace")
        return json.loads(raw or "{}")

def dm(v,d,lon=False):
    n=3 if lon else 2;x=float(v[:n])+float(v[n:])/60
    return -x if d.upper() in ("S","W") else x

def parse_v8(packet):
    p=packet.strip().rstrip("#").split(",")
    if len(p)<13 or not p[0].startswith("*HQ") or p[2]!="V8" or p[4]!="A":raise ValueError("not_v8")
    gsm=None;battery=None;acc=None;device_time=None
    try:gsm=max(0,min(31,int(float(p[-3]))))
    except:pass
    try:battery=max(0,min(100,int(float(p[-1]))))
    except:pass
    # ACC calibration is based ONLY on the verified tests from device 9176515870.
    # In the 32-bit status field (p[12]), the ACC state is carried by bit 0x20
    # of the third byte: FFFFDFFF => ON (bit clear), FFFFFBFF => OFF (bit set).
    # We read that bit only; other status flags may change independently.
    try:
        status=p[12].strip().upper()
        if re.fullmatch(r"[0-9A-F]{8}",status):
            third=int(status[4:6],16)
            acc=1 if (third & 0x20)==0 else 0
    except:pass
    try:
        # V8: HHMMSS in p[3], DDMMYY in p[11]. Used to reject buffered/old packets.
        device_time=datetime.strptime(p[11].strip()+p[3].strip(),"%d%m%y%H%M%S").isoformat(timespec="seconds")
    except:pass
    return {"device_id":p[1].strip(),"latitude":dm(p[5],p[6]),"longitude":dm(p[7],p[8],True),
            "speed":round(float(p[9] or 0)*1.852,2),"speed_unit":"kmh","heading":float(p[10] or 0),
            "acc":acc,"device_time":device_time,"gsm_signal":gsm,"battery_percent":battery,"raw_data":packet}

def log_raw(packet,ip=None,port=None):
    try:
        RAW_LOG.parent.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now().isoformat(timespec="seconds")
        peer=f" {ip}:{port}" if ip is not None else ""
        with RAW_LOG_LOCK, RAW_LOG.open("a",encoding="utf-8") as f:
            f.write(f"{stamp}{peer} {packet}\n")
            f.flush()
    except Exception as e:
        print("RAW log error:",e)

def post_tracker(packet):
    try:
        body=json.dumps(parse_v8(packet)).encode()
        req=urllib.request.Request(INGEST_URL,data=body,headers=headers(),method="POST")
        with urllib.request.urlopen(req,timeout=12) as r:
            print("Web",r.status,r.read().decode(errors="replace"))
    except Exception as e: print("Tracker forward:",e)

def response_code(packet):
    u=packet.upper()
    for code in ("S20",):
        if f",V4,{code}," in u or f",{code}," in u:
            return code
    return None

class TrackerHandler(socketserver.BaseRequestHandler):
    def handle(self):
        ip,port=self.client_address;print(f"TCP connected: {ip}:{port}")
        self.device_id=None;self.alive=True;self.send_lock=threading.Lock();self.pending_commands={}
        poller=threading.Thread(target=self.command_loop,daemon=True);poller.start()
        buf=b""
        try:
            while True:
                data=self.request.recv(4096)
                if not data:break
                buf+=data
                while b"#" in buf:
                    b,buf=buf.split(b"#",1);packet=(b+b"#").decode(errors="replace").strip()
                    if not packet:continue
                    print("RAW TCP:",packet)
                    log_raw(packet,ip,port)
                    m=DEVICE_ID_RE.match(packet)
                    if m:self.device_id=m.group(1).strip()
                    code=response_code(packet)
                    if code and self.device_id:
                        cid=self.pending_commands.pop(code,None)
                        try:api("/api/gateway/command-response","POST",{"device_id":self.device_id,"command_id":cid,"raw":packet})
                        except Exception as e:print("Command response:",e)
                    else:post_tracker(packet)
        except Exception as e:print("TCP error:",e)
        finally:self.alive=False;print(f"TCP disconnected: {ip}:{port}")

    def command_loop(self):
        while self.alive:
            try:
                if self.device_id:
                    x=api("/api/gateway/commands/"+self.device_id)
                    for cmd in x.get("commands",[]):
                        cid=cmd["id"]
                        raw=cmd["command_text"].encode()
                        with self.send_lock:self.request.sendall(raw)
                        print("TCP COMMAND:",cmd["command_text"])
                        ctype=cmd.get("command_type")
                        # Only one S20 command is fetched at a time, so its ACK can be matched safely.
                        self.pending_commands["S20"]=cid
                        api(f"/api/gateway/commands/{cid}/sent","POST",{})
            except Exception as e:
                if self.alive:print("Command poll:",e)
            time.sleep(2)

class Server(socketserver.ThreadingMixIn,socketserver.TCPServer):
    allow_reuse_address=True;daemon_threads=True

if __name__=="__main__":
    print("="*68);print(f"GPS bidirectional Gateway TCP {LISTEN_HOST}:{LISTEN_PORT} -> {WEB_BASE}");print(f"RAW TCP log: {RAW_LOG.resolve()}");print("="*68)
    with Server((LISTEN_HOST,LISTEN_PORT),TrackerHandler) as server:
        try:server.serve_forever()
        except KeyboardInterrupt:print("Gateway stopped")
