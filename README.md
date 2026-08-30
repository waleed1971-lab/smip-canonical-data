# SMIP Canonical Daily Data

مستودع عام مخصص لنسخة **SMIP canonical daily** القابلة للتدقيق. لا يحتوي على مفاتيح أو مسارات إدارية أو ملفات RAW أو baseline أو state.

| الملف | الوصف |
|---|---|
| `manifest.json` | إصدار البيانات، وقت إنشاء snapshot، أول وآخر جلسة، عدد الصفوف والرموز، schema، وسياسة منع leakage |
| `canonical-daily.csv.gz` | البيانات اليومية المضغوطة بصيغة CSV |
| `parts/canonical-YYYY.csv` | نفس صفوف canonical مقسمة حسب السنة كنص UTF-8 للموصلات التي لا تقرأ gzip الثنائي |

## النسخة الحالية

- Dataset version: `smip-canonical-daily-v1-326b35b2b527b9a1`
- First session: `2019-01-01`
- Last session: `2026-08-27`
- Rows: `392269`
- Symbols: `287`
- SHA256: `326b35b2b527b9a111d2d2ec89dcb394faf8ca989386cbd6fcb274ac4dfe90ac`

يجب تنزيل `manifest.json` أولاً، ثم تنزيل الملف المضغوط ومطابقة SHA256 قبل استخدامه. إذا لم تتطابق البصمة، تتوقف العملية `fail-closed` ولا يبدأ أي بحث.

## الأجزاء النصية

يحتوي `manifest.json` على مصفوفة `text_parts` التي تسجل لكل سنة رابط GitHub raw، وعدد الصفوف والرموز، وأول وآخر جلسة، والحجم، وSHA256. يمكن قراءة الأجزاء مباشرة:

| السنة | الرابط |
|---|---|
| 2019 | `https://raw.githubusercontent.com/waleed1971-lab/smip-canonical-data/main/parts/canonical-2019.csv` |
| 2020 | `https://raw.githubusercontent.com/waleed1971-lab/smip-canonical-data/main/parts/canonical-2020.csv` |
| 2021 | `https://raw.githubusercontent.com/waleed1971-lab/smip-canonical-data/main/parts/canonical-2021.csv` |
| 2022 | `https://raw.githubusercontent.com/waleed1971-lab/smip-canonical-data/main/parts/canonical-2022.csv` |
| 2023 | `https://raw.githubusercontent.com/waleed1971-lab/smip-canonical-data/main/parts/canonical-2023.csv` |
| 2024 | `https://raw.githubusercontent.com/waleed1971-lab/smip-canonical-data/main/parts/canonical-2024.csv` |
| 2025 | `https://raw.githubusercontent.com/waleed1971-lab/smip-canonical-data/main/parts/canonical-2025.csv` |
| 2026 | `https://raw.githubusercontent.com/waleed1971-lab/smip-canonical-data/main/parts/canonical-2026.csv` |

لإعادة تركيب النص canonical حرفيًا: تُقرأ كل الأجزاء مع الاحتفاظ بنسخة واحدة من الـheader، ثم تُجمع صفوف البيانات الأصلية وتُرتب تصاعديًا حسب `symbol` ثم `date` قبل كتابتها بسطر LF. يجب أن تطابق النتيجة `canonical_text.sha256` و`canonical_text.size_bytes` في manifest. التحديث الآلي للمستودع **غير مفعل** حتى ينجح الاختبار end-to-end من بيئة المتابعة.

## Schema

```text
symbol,date,open,high,low,close,volume,value_traded,trades_count,is_final,partial,source_layer,source_run_id,source_file_sha256
```

لا يتضمن الملف labels أو targets أو عوائد مستقبلية أو adjusted close أو أي أعمدة مشتقة من المستقبل. لا يغيّر نشر البيانات الحكم الحالي: `NO_PROVEN_EDGE`.
