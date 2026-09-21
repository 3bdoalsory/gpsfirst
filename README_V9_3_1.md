# GPS Platform V9.3.1 — Admin Mobile Scroll Fix

Built on V9.3.

- Fixes the actual mobile admin scroll conflict.
- The admin-page class is now rendered on `<body>` before CSS layout, instead of being added late by JavaScript.
- The client-only mobile `body { overflow:hidden }` rule is now explicitly scoped to `body:not(.admin-page)`.
- Removes accidental literal `\n\n` tokens that were present before the V9.3 refinement CSS block.
- Keeps the V9.3 Geofence color and History chart changes unchanged.
