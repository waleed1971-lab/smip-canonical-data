# SMIP Canonical Daily Data

مستودع عام مخصص لنسخة **SMIP canonical daily** القابلة للتدقيق. لا يحتوي على مفاتيح أو مسارات إدارية أو ملفات RAW أو baseline أو state.

| الملف | الوصف |
|---|---|
| `manifest.json` | إصدار البيانات، وقت إنشاء snapshot، أول وآخر جلسة، عدد الصفوف والرموز، schema، وسياسة منع leakage |
| `canonical-daily.csv.gz` | البيانات اليومية المضغوطة بصيغة CSV |
| `parts/canonical-YYYY-MM.csv` | نفس صفوف canonical مقسمة حسب الشهر كنص UTF-8، وكل ملف أقل من 1 MB |
| `repository-integrity-receipt.json` | إيصال PASS صغير يربط snapshot المفحوص بالـcommit وتشغيل GitHub والبصمات التفصيلية |

## النسخة الحالية

القيم المتغيرة مثل `dataset_version` و`last_session` و`row_count` و`symbol_count` وSHA256 تُقرأ دائمًا من `manifest.json`، ثم تُطابق مع `repository-integrity-receipt.json`. لا تُستخدم أرقام ثابتة من README كعقد بيانات.

يجب تنزيل `manifest.json` أولاً، ثم تنزيل الملف المضغوط ومطابقة SHA256 قبل استخدامه. إذا لم تتطابق البصمة، تتوقف العملية `fail-closed` ولا يبدأ أي بحث.

## الأجزاء النصية

يحتوي `manifest.json` على مصفوفة `text_parts` التي تسجل لكل شهر رابط GitHub raw، وعدد الصفوف والرموز، وأول وآخر جلسة، والحجم، وSHA256. يوجد **92 شهرًا في 94 ملفًا**؛ شُطر شهرا يوليو وأغسطس 2026 إلى جزأين مرقمين لأن الملف الشهري المفرد تجاوز حد 1 MB. أكبر جزء منشور حجمه **999,947 بايت**.

نمط الرابط المعتاد:

`https://raw.githubusercontent.com/waleed1971-lab/smip-canonical-data/main/parts/canonical-YYYY-MM.csv`

وعند وجود جزء فرعي:

`https://raw.githubusercontent.com/waleed1971-lab/smip-canonical-data/main/parts/canonical-YYYY-MM-partNN.csv`

لا يلزم تخمين الأسماء؛ تُستخدم روابط `url` بالترتيب الظاهر داخل `text_parts` في manifest.

لإعادة تركيب النص canonical حرفيًا: تُقرأ كل الأجزاء مع الاحتفاظ بنسخة واحدة من الـheader، ثم تُجمع صفوف البيانات الأصلية وتُرتب تصاعديًا حسب `symbol` ثم `date` قبل كتابتها بسطر LF. يجب أن تطابق النتيجة `canonical_text.sha256` و`canonical_text.size_bytes` في manifest.

## التحديث الآلي

التحديث الآلي **مفعل** عبر GitHub Actions بعد نجاح اختبار Repository-side integrity gate من بيئة المتابعة. يعمل بتسع محاولات احتياطية في أيام التداول السعودية: 16:05 و16:25 و16:45 و17:05 و17:25، ثم محاولات متأخرة عند 21:45 و22:15 و22:45 و23:15 بتوقيت الرياض، وحد freshness التشغيلي الساعة 23:45. يسحب فقط مساري البحث العامين للقراءة من `smip-server.onrender.com` ولا يحتاج Token أو سرًا جديدًا. تعدد المحاولات يعالج تأخر توفر المصدر أو سقوط تشغيل cron لدى GitHub؛ وبسبب idempotency لا ينشئ أي commit إضافي إذا لم توجد جلسة أحدث.

التسلسل ثابت: تنزيل manifest وcanonical، فحص المصدر وSHA256 والـschema ومنع leakage، إثبات أن التاريخ القديم لم يتغير وأن الصفوف الجديدة لاحقة فقط، بناء الأجزاء الشهرية، التحقق وإعادة البناء، ثم commit للبيانات. بعد ذلك فقط يُشغّل workflow مستقل على commit البيانات نفسه لإصدار attestation موقّع ونشر إيصال PASS. إذا لم توجد جلسة أحدث فلا ينشأ commit. وإذا فشل أي فحص، لا تُنشر بيانات جديدة ولا يتغير آخر إيصال PASS.

هذا الإثبات هو `Repository-side integrity attestation`، وليس `consumer-side byte verification`. يبدأ المستهلك من الإيصال، ويربط `commit_sha` و`workflow.run_id`، ثم يطابق manifest وfreshness قبل أي بحث.

## Schema

```text
symbol,date,open,high,low,close,volume,value_traded,trades_count,is_final,partial,source_layer,source_run_id,source_file_sha256
```

لا يتضمن الملف labels أو targets أو عوائد مستقبلية أو adjusted close أو أي أعمدة مشتقة من المستقبل. لا يغيّر نشر البيانات الحكم الحالي: `NO_PROVEN_EDGE`.
