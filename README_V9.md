# V9 — 2026-09-21

Applied requested refinements:
1. Service audit uses one row per stop/restore request and updates its result; Arabic service/result labels distinguish stop vs restore.
2. Client service UI/API no longer exposes raw device responses; removed the S26 connection-test UI/route and related test code.
3. Selected vehicle card shows battery percentage inside battery icon and GSM as signal bars at the top.
4. History route and speed chart use identical thresholds: <=40 green, <=80 orange, <=120 red, >120 dark red, with legend.
5. Geofence color selector is a live colored circular swatch.
6. Automatic Recent Alerts box shows unread notifications only; bell history still retains all notifications.
7. Clicking the currently selected vehicle again, either marker or list row, clears selection and returns to general map state.

Preserved S20 stop/restore path and gateway architecture.
