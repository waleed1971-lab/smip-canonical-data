# SMIP Canonical Daily Data

مستودع عام مخصص لنسخة **SMIP canonical daily** القابلة للتدقيق. لا يحتوي على مفاتيح أو مسارات إدارية أو ملفات RAW أو baseline أو state.

| الملف | الوصف |
|---|---|
| `manifest.json` | إصدار البيانات، وقت إنشاء snapshot، أول وآخر جلسة، عدد الصفوف والرموز، schema، وسياسة منع leakage |
| `canonical-daily.csv.gz` | البيانات اليومية المضغوطة بصيغة CSV |
| `parts/canonical-YYYY-MM.csv` | نفس صفوف canonical مقسمة حسب الشهر كنص UTF-8، وكل ملف أقل من 1 MB |

## النسخة الحالية

- Dataset version: `smip-canonical-daily-v1-326b35b2b527b9a1`
- First session: `2019-01-01`
- Last session: `2026-08-27`
- Rows: `392269`
- Symbols: `287`
- SHA256: `326b35b2b527b9a111d2d2ec89dcb394faf8ca989386cbd6fcb274ac4dfe90ac`

يجب تنزيل `manifest.json` أولاً، ثم تنزيل الملف المضغوط ومطابقة SHA256 قبل استخدامه. إذا لم تتطابق البصمة، تتوقف العملية `fail-closed` ولا يبدأ أي بحث.

## الأجزاء النصية

يحتوي `manifest.json` على مصفوفة `text_parts` التي تسجل لكل شهر رابط GitHub raw، وعدد الصفوف والرموز، وأول وآخر جلسة، والحجم، وSHA256. يوجد **92 شهرًا في 94 ملفًا**؛ شُطر شهرا يوليو وأغسطس 2026 إلى جزأين مرقمين لأن الملف الشهري المفرد تجاوز حد 1 MB. أكبر جزء منشور حجمه **999,947 بايت**.

نمط الرابط المعتاد:

`https://raw.githubusercontent.com/waleed1971-lab/smip-canonical-data/main/parts/canonical-YYYY-MM.csv`

وعند وجود جزء فرعي:

`https://raw.githubusercontent.com/waleed1971-lab/smip-canonical-data/main/parts/canonical-YYYY-MM-partNN.csv`

لا يلزم تخمين الأسماء؛ تُستخدم روابط `url` بالترتيب الظاهر داخل `text_parts` في manifest.

لإعادة تركيب النص canonical حرفيًا: تُقرأ كل الأجزاء مع الاحتفاظ بنسخة واحدة من الـheader، ثم تُجمع صفوف البيانات الأصلية وتُرتب تصاعديًا حسب `symbol` ثم `date` قبل كتابتها بسطر LF. يجب أن تطابق النتيجة `canonical_text.sha256` و`canonical_text.size_bytes` في manifest. التحديث الآلي للمستودع **غير مفعل** حتى ينجح الاختبار end-to-end من بيئة المتابعة.

## Schema

```text
symbol,date,open,high,low,close,volume,value_traded,trades_count,is_final,partial,source_layer,source_run_id,source_file_sha256
```

لا يتضمن الملف labels أو targets أو عوائد مستقبلية أو adjusted close أو أي أعمدة مشتقة من المستقبل. لا يغيّر نشر البيانات الحكم الحالي: `NO_PROVEN_EDGE`.
