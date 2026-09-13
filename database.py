import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash

DB = Path(__file__).with_name("gpsplatform.db")

def connect():
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
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS devices(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      platform_id INTEGER UNIQUE NOT NULL,
      device_id TEXT UNIQUE NOT NULL,
      name TEXT NOT NULL,
      plate TEXT DEFAULT '',
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
      raw_data TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_gps_device_time ON gps_data(device_id, created_at);

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

    CREATE TABLE IF NOT EXISTS service_audit(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      username_snapshot TEXT NOT NULL,
      device_pk INTEGER NOT NULL,
      action TEXT NOT NULL,
      result TEXT NOT NULL DEFAULT 'recorded',
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Upgrade existing V3 databases without deleting users/devices/GPS history.
    ucols={r[1] for r in c.execute("PRAGMA table_info(users)")}
    if "allow_immobilize" not in ucols:
        c.execute("ALTER TABLE users ADD COLUMN allow_immobilize INTEGER NOT NULL DEFAULT 0")
    dcols={r[1] for r in c.execute("PRAGMA table_info(devices)")}
    if "plate" not in dcols:
        c.execute("ALTER TABLE devices ADD COLUMN plate TEXT DEFAULT ''")
    if "vehicle_model" not in dcols:
        c.execute("ALTER TABLE devices ADD COLUMN vehicle_model TEXT DEFAULT ''")
    if "vehicle_color" not in dcols:
        c.execute("ALTER TABLE devices ADD COLUMN vehicle_color TEXT DEFAULT ''")

    # V3 used circular geofences. Replace only that table with polygon schema.
    gcols={r[1] for r in c.execute("PRAGMA table_info(geofences)")}
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
      is_active INTEGER NOT NULL DEFAULT 1,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(user_id) REFERENCES users(id),
      FOREIGN KEY(device_pk) REFERENCES devices(id)
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS geofence_devices(
      geofence_id INTEGER NOT NULL,
      device_pk INTEGER NOT NULL,
      PRIMARY KEY(geofence_id,device_pk),
      FOREIGN KEY(geofence_id) REFERENCES geofences(id) ON DELETE CASCADE,
      FOREIGN KEY(device_pk) REFERENCES devices(id) ON DELETE CASCADE
    )
    """)
    # Migrate legacy one-device zones into the new multi-device relation.
    for r in c.execute("SELECT id,device_pk FROM geofences WHERE device_pk IS NOT NULL").fetchall():
        c.execute("INSERT OR IGNORE INTO geofence_devices(geofence_id,device_pk) VALUES(?,?)",(r[0],r[1]))

    if not c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
        c.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                  ("admin", generate_password_hash("1234"), "admin"))
    c.commit(); c.close()
    print("Database ready:", DB)
    print("Admin: admin / 1234")

if __name__ == "__main__": init()
