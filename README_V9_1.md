# GPS Platform V9.1 — 2026-09-21

Hotfix فوق V9.

- إصلاح خطأ JavaScript في `templates/dashboard.html` كان يمنع تهيئة الخريطة ويظهر في Console: `Uncaught SyntaxError: missing ) after argument list`.
- سبب الخطأ كان بقايا كتلة JavaScript مكررة بعد دالة `selectVehicle`.
- تم حذف الكتلة المكررة مع إبقاء تعديلات V9 المعتمدة.
- تم التحقق من Python compilation لكل main/database/gateway/tracker_server.
- تم التحقق من parsing لجميع قوالب Jinja.
- تم استخراج JavaScript الخاص بـ dashboard بعد استبدال قيم Jinja وفحصه بواسطة `node --check` بنجاح.
