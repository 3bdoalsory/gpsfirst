# GPS Platform V9.6 — cumulative update from V9.5
Base: V9.5.
Includes the approved post-V9.5 client/admin/history/geofence/share/offline/overspeed refinements. Database upgrades are additive and preserve existing data.
Important: tracker power-disconnect notifications are emitted only when the ingest payload explicitly provides `power_disconnected=true` or an explicit disconnected `external_power` value; no raw-packet guess is used.
