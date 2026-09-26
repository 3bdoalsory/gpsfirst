import os
import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash

DB = Path(os.getenv("GPS_DB_PATH", str(Path(__file__).with_name("gpsplatform.db"))))


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init():
    c = connect()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'client',
      phone TEXT,
      is_active INTEGER NOT NULL DEFAULT 1,
      allow_immobilize INTEGER NOT NULL DEFAULT 0,
      overspeed_enabled INTEGER NOT NULL DEFAULT 0,
      overspeed_limit REAL DEFAULT 100,
      language TEXT NOT NULL DEFAULT 'ar',
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS devices(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      platform_id INTEGER UNIQUE NOT NULL,
      device_id TEXT UNIQUE NOT NULL,
      name TEXT NOT NULL,
      plate TEXT DEFAULT '',
      vehicle_model TEXT DEFAULT '',
      vehicle_color TEXT DEFAULT '',
      tracker_phone TEXT DEFAULT '',
      admin_notes TEXT DEFAULT '',
      user_id INTEGER,
      service_status TEXT NOT NULL DEFAULT 'active',
      subscription_start DATE,
      subscription_end DATE,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS gps_data(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      device_id TEXT NOT NULL,
      latitude REAL,
      longitude REAL,
      speed REAL DEFAULT 0,
      heading REAL DEFAULT 0,
      acc INTEGER,
      gsm_signal INTEGER,
      battery_percent INTEGER,
      raw_data TEXT,
      device_time TIMESTAMP,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_gps_device_time ON gps_data(device_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_gps_created_at ON gps_data(created_at);

    CREATE TABLE IF NOT EXISTS notifications(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      device_pk INTEGER,
      kind TEXT NOT NULL,
      title TEXT NOT NULL,
      message TEXT NOT NULL,
      is_read INTEGER NOT NULL DEFAULT 0,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS immobilize_requests(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      username_snapshot TEXT NOT NULL,
      device_pk INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending_stop',
      requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      ready_at TIMESTAMP,
      completed_at TIMESTAMP,
      result TEXT,
      FOREIGN KEY(user_id) REFERENCES users(id),
      FOREIGN KEY(device_pk) REFERENCES devices(id)
    );
    CREATE INDEX IF NOT EXISTS idx_immobilize_device_status ON immobilize_requests(device_pk,status);


    CREATE TABLE IF NOT EXISTS device_commands(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      device_pk INTEGER NOT NULL,
      request_id INTEGER,
      command_type TEXT NOT NULL,
      command_text TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'queued',
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      sent_at TIMESTAMP,
      confirmed_at TIMESTAMP,
      response_text TEXT,
      FOREIGN KEY(device_pk) REFERENCES devices(id),
      FOREIGN KEY(request_id) REFERENCES immobilize_requests(id)
    );
    CREATE INDEX IF NOT EXISTS idx_device_commands_status ON device_commands(device_pk,status);

    CREATE TABLE IF NOT EXISTS service_audit(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      username_snapshot TEXT NOT NULL,
      device_pk INTEGER NOT NULL,
      action TEXT NOT NULL,
      result TEXT NOT NULL DEFAULT 'recorded',
      request_id INTEGER,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS system_settings(
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS location_shares(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      token TEXT UNIQUE NOT NULL,
      device_pk INTEGER NOT NULL,
      user_id INTEGER NOT NULL,
      expires_at TIMESTAMP NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(device_pk) REFERENCES devices(id) ON DELETE CASCADE,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_location_shares_token ON location_shares(token);
    """)

    # Upgrade existing databases without deleting users/devices/GPS history.
    acols = {r[1] for r in c.execute("PRAGMA table_info(service_audit)")}
    if "request_id" not in acols:
        c.execute("ALTER TABLE service_audit ADD COLUMN request_id INTEGER")
    ucols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
    if "allow_immobilize" not in ucols:
        c.execute("ALTER TABLE users ADD COLUMN allow_immobilize INTEGER NOT NULL DEFAULT 0")
    if "overspeed_enabled" not in ucols:
        c.execute("ALTER TABLE users ADD COLUMN overspeed_enabled INTEGER NOT NULL DEFAULT 0")
    if "overspeed_limit" not in ucols:
        c.execute("ALTER TABLE users ADD COLUMN overspeed_limit REAL DEFAULT 100")

    ucols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
    if "language" not in ucols:
        c.execute("ALTER TABLE users ADD COLUMN language TEXT NOT NULL DEFAULT 'ar'")

    dcols = {r[1] for r in c.execute("PRAGMA table_info(devices)")}
    if "plate" not in dcols:
        c.execute("ALTER TABLE devices ADD COLUMN plate TEXT DEFAULT ''")
    if "vehicle_model" not in dcols:
        c.execute("ALTER TABLE devices ADD COLUMN vehicle_model TEXT DEFAULT ''")
    if "vehicle_color" not in dcols:
        c.execute("ALTER TABLE devices ADD COLUMN vehicle_color TEXT DEFAULT ''")
    if "tracker_phone" not in dcols:
        c.execute("ALTER TABLE devices ADD COLUMN tracker_phone TEXT DEFAULT ''")
    if "admin_notes" not in dcols:
        c.execute("ALTER TABLE devices ADD COLUMN admin_notes TEXT DEFAULT ''")

    gpscols = {r[1] for r in c.execute("PRAGMA table_info(gps_data)")}
    if "acc" not in gpscols:
        c.execute("ALTER TABLE gps_data ADD COLUMN acc INTEGER")
    if "gsm_signal" not in gpscols:
        c.execute("ALTER TABLE gps_data ADD COLUMN gsm_signal INTEGER")
    if "battery_percent" not in gpscols:
        c.execute("ALTER TABLE gps_data ADD COLUMN battery_percent INTEGER")
    if "device_time" not in gpscols:
        c.execute("ALTER TABLE gps_data ADD COLUMN device_time TIMESTAMP")
    c.execute("CREATE INDEX IF NOT EXISTS idx_gps_device_device_time ON gps_data(device_id, device_time)")

    # V3 used circular geofences. Preserve it as backup and create polygon schema.
    gcols = {r[1] for r in c.execute("PRAGMA table_info(geofences)")}
    if gcols and "polygon_json" not in gcols:
        c.execute("ALTER TABLE geofences RENAME TO geofences_v3_circle_backup")
    c.execute("""
    CREATE TABLE IF NOT EXISTS geofences(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      device_pk INTEGER,
      name TEXT NOT NULL,
      polygon_json TEXT NOT NULL,
      alert_type TEXT NOT NULL DEFAULT 'both',
      sms_enabled INTEGER NOT NULL DEFAULT 0,
      color TEXT NOT NULL DEFAULT '#29c7e8',
      is_active INTEGER NOT NULL DEFAULT 1,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(user_id) REFERENCES users(id),
      FOREIGN KEY(device_pk) REFERENCES devices(id)
    )
    """)
    gcols2 = {r[1] for r in c.execute("PRAGMA table_info(geofences)")}
    if "color" not in gcols2:
        c.execute("ALTER TABLE geofences ADD COLUMN color TEXT NOT NULL DEFAULT '#29c7e8'")
    c.execute("""
    CREATE TABLE IF NOT EXISTS geofence_devices(
      geofence_id INTEGER NOT NULL,
      device_pk INTEGER NOT NULL,
      PRIMARY KEY(geofence_id,device_pk),
      FOREIGN KEY(geofence_id) REFERENCES geofences(id) ON DELETE CASCADE,
      FOREIGN KEY(device_pk) REFERENCES devices(id) ON DELETE CASCADE
    )
    """)
    for r in c.execute("SELECT id,device_pk FROM geofences WHERE device_pk IS NOT NULL").fetchall():
        c.execute("INSERT OR IGNORE INTO geofence_devices(geofence_id,device_pk) VALUES(?,?)", (r[0], r[1]))

    defaults = {
        "stop_min_seconds": "20",
        "stop_max_speed_kmh": "1",
        "stop_max_drift_m": "25",
        "history_retention_days": "90",
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO system_settings(key,value) VALUES(?,?)", (k, v))

    if not c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
        c.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                  ("admin", generate_password_hash("1234"), "admin"))
    c.commit()
    c.close()
    print("Database ready:", DB)


if __name__ == "__main__":
    init()
