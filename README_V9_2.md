# V9.2 command queue hotfix

- Removed the Gateway local `sent` cache; Render/SQLite command status is now the source of truth.
- Gateway command API returns one queued command at a time per device.
- Prevents multiple S20 commands from being in flight and overwriting ACK correlation.
- Removed S26 response handling from the active gateway path.
- Restore remains S20 `1,0`; immobilize remains S20 `1,1`.
