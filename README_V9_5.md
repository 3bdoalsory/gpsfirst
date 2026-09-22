# GPS Platform V9.5 — cumulative notes over verified V9.4

Built directly from `gps_platform_v9_4_verified_from_v9_2_2026-09-21.zip`.

Implemented pending notes:
1. Enlarged the actual history speed chart (plot, axes, labels, line and points) while preserving the agreed speed thresholds/colors.
2. Client subscription date turns red when 30 days or less remain.
3. Duplicate account usernames are blocked (case-insensitive on create/edit).
4. Duplicate real Device IDs are blocked before insertion; DB UNIQUE remains as a second guard.
5. Navigation label is now `تسجيل الخروج`; `/logout` still clears the session.
6. Live moving vehicle marker uses `#00b86b`, distinct from history-route green `#35d07f`.
7. Offline behavior uses the existing 120-second communication timeout: live marker is removed, battery becomes `—`, GSM is empty/no-signal, while the vehicle stays in the client list.
8. Dashboard, history and geofence maps now use Google Maps JavaScript API. Geofences remain editable and support click-to-draw / click-start-to-close. Map key is read only from `GOOGLE_MAPS_API_KEY` environment variable.

Deployment environment:
- Set `GOOGLE_MAPS_API_KEY` to the current Google Maps browser key.
- Keep the existing production DB path and ingest token unchanged.
- No database file is included in this package.
