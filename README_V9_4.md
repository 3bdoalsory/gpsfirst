# GPS Platform V9.4 — verified usability build

Built directly from the proven V9.2 command-hotfix baseline.

Changes:
1. Geofence color selector is a visible circular swatch; it updates live and applies the same color immediately to new and saved polygons.
2. History speed chart is larger/taller and uses the same speedColor thresholds as the route: <=40 green, <=80 orange, <=120 red, >120 dark red.
3. Admin mobile page has dedicated vertical touch scrolling and no longer inherits the client map body scroll lock.
4. V9.2 main.py and gateway.py command logic is preserved unchanged.
5. CSS URL is cache-busted (?v=942) so browsers/Render do not keep showing the previous stylesheet.
