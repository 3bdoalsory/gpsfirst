import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash

DB = Path(__file__).with_name("gpsplatform.db")

def connect():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def add_column_if_missing(c, table, column, definition):
    cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

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
      last_battery_level INTEGER,
      last_battery_source TEXT,
      last_battery_at TIMESTAMP,
      last_acc INTEGER,
      last_gps_valid INTEGER,
      last_satellites INTEGER,
      last_gsm_signal INTEGER,
      last_status_hex TEXT,
      last_mcc INTEGER,
      last_mnc INTEGER,
      last_lac INTEGER,
      last_cell_id INTEGER,
      telemetry_updated_at TIMESTAMP,
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
      gps_valid INTEGER,
      acc INTEGER,
      battery_level INTEGER,
      battery_source TEXT,
      satellites INTEGER,
      gsm_signal INTEGER,
      status_hex TEXT,
      mcc INTEGER,
      mnc INTEGER,
      lac INTEGER,
      cell_id INTEGER,
      extra_json TEXT,
      raw_data TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_gps_device_time ON gps_data(device_id, created_at);

    CREATE TABLE IF NOT EXISTS tracker_events(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      device_id TEXT,
      packet_type TEXT,
      battery_level INTEGER,
      gps_valid INTEGER,
      acc INTEGER,
      satellites INTEGER,
      gsm_signal INTEGER,
      status_hex TEXT,
      raw_data TEXT NOT NULL,
      parsed_json TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_tracker_events_device_time ON tracker_events(device_id, created_at);

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

    # Safe upgrade for databases from the previous version.
    for col, definition in [
        ("plate", "TEXT DEFAULT ''"),
        ("last_battery_level", "INTEGER"),
        ("last_battery_source", "TEXT"),
        ("last_battery_at", "TIMESTAMP"),
        ("last_acc", "INTEGER"),
        ("last_gps_valid", "INTEGER"),
        ("last_satellites", "INTEGER"),
        ("last_gsm_signal", "INTEGER"),
        ("last_status_hex", "TEXT"),
        ("last_mcc", "INTEGER"),
        ("last_mnc", "INTEGER"),
        ("last_lac", "INTEGER"),
        ("last_cell_id", "INTEGER"),
        ("telemetry_updated_at", "TIMESTAMP"),
    ]:
        add_column_if_missing(c, "devices", col, definition)

    for col, definition in [
        ("gps_valid", "INTEGER"),
        ("acc", "INTEGER"),
        ("battery_level", "INTEGER"),
        ("battery_source", "TEXT"),
        ("satellites", "INTEGER"),
        ("gsm_signal", "INTEGER"),
        ("status_hex", "TEXT"),
        ("mcc", "INTEGER"),
        ("mnc", "INTEGER"),
        ("lac", "INTEGER"),
        ("cell_id", "INTEGER"),
        ("extra_json", "TEXT"),
        ("raw_data", "TEXT"),
    ]:
        add_column_if_missing(c, "gps_data", col, definition)

    # Upgrade old circular geofences without deleting data.
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

    for r in c.execute("SELECT id,device_pk FROM geofences WHERE device_pk IS NOT NULL").fetchall():
        c.execute("INSERT OR IGNORE INTO geofence_devices(geofence_id,device_pk) VALUES(?,?)",(r[0],r[1]))

    if not c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
        c.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                  ("admin", generate_password_hash("1234"), "admin"))

    c.commit()
    c.close()
    print("Database ready:", DB)
    print("Admin: admin / 1234")

if __name__ == "__main__":
    init()
