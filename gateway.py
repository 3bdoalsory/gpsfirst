import json, os, re, socketserver, threading, time, urllib.request, urllib.error
from datetime import datetime

LISTEN_HOST=os.getenv("GPS_LISTEN_HOST","0.0.0.0")
LISTEN_PORT=int(os.getenv("GPS_LISTEN_PORT","8090"))
WEB_BASE=os.getenv("GPS_WEB_BASE","https://gpsfirst.onrender.com").rstrip("/")
INGEST_URL=os.getenv("GPS_INGEST_URL",WEB_BASE+"/api/tracker")
TOKEN=os.getenv("GPS_GATEWAY_TOKEN",os.getenv("GPS_INGEST_TOKEN","")).strip()
DEVICE_ID_RE=re.compile(r"^\*HQ,([^,]+),")

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
    if len(p)<11 or not p[0].startswith("*HQ") or p[2]!="V8" or p[4]!="A":raise ValueError("not_v8")
    gsm=None;battery=None
    try:gsm=max(0,min(31,int(float(p[-3]))))
    except:pass
    try:battery=max(0,min(100,int(float(p[-1]))))
    except:pass
    return {"device_id":p[1].strip(),"latitude":dm(p[5],p[6]),"longitude":dm(p[7],p[8],True),
            "speed":round(float(p[9] or 0)*1.852,2),"speed_unit":"kmh","heading":float(p[10] or 0),
            "gsm_signal":gsm,"battery_percent":battery,"raw_data":packet}

def post_tracker(packet):
    try:
        body=json.dumps(parse_v8(packet)).encode()
        req=urllib.request.Request(INGEST_URL,data=body,headers=headers(),method="POST")
        with urllib.request.urlopen(req,timeout=12) as r:
            print("Web",r.status,r.read().decode(errors="replace"))
    except Exception as e: print("Tracker forward:",e)

def response_code(packet):
    u=packet.upper()
    for code in ("S20","S26"):
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
        sent=set()
        while self.alive:
            try:
                if self.device_id:
                    x=api("/api/gateway/commands/"+self.device_id)
                    for cmd in x.get("commands",[]):
                        cid=cmd["id"]
                        if cid in sent:continue
                        raw=cmd["command_text"].encode()
                        with self.send_lock:self.request.sendall(raw)
                        print("TCP COMMAND:",cmd["command_text"])
                        ctype=cmd.get("command_type")
                        self.pending_commands["S26" if ctype=="diagnostic" else "S20"]=cid
                        api(f"/api/gateway/commands/{cid}/sent","POST",{})
                        sent.add(cid)
            except Exception as e:
                if self.alive:print("Command poll:",e)
            time.sleep(2)

class Server(socketserver.ThreadingMixIn,socketserver.TCPServer):
    allow_reuse_address=True;daemon_threads=True

if __name__=="__main__":
    print("="*68);print(f"GPS bidirectional Gateway TCP {LISTEN_HOST}:{LISTEN_PORT} -> {WEB_BASE}");print("="*68)
    with Server((LISTEN_HOST,LISTEN_PORT),TrackerHandler) as server:
        try:server.serve_forever()
        except KeyboardInterrupt:print("Gateway stopped")
