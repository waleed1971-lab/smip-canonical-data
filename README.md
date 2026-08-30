# SMIP Canonical Daily Data

مستودع عام مخصص لنسخة **SMIP canonical daily** القابلة للتدقيق. لا يحتوي على مفاتيح أو مسارات إدارية أو ملفات RAW أو baseline أو state.

| الملف | الوصف |
|---|---|
| `manifest.json` | إصدار البيانات، وقت إنشاء snapshot، أول وآخر جلسة، عدد الصفوف والرموز، schema، وسياسة منع leakage |
| `canonical-daily.csv.gz` | البيانات اليومية المضغوطة بصيغة CSV |

## النسخة الحالية

- Dataset version: `smip-canonical-daily-v1-326b35b2b527b9a1`
- First session: `2019-01-01`
- Last session: `2026-08-27`
- Rows: `392269`
- Symbols: `287`
- SHA256: `326b35b2b527b9a111d2d2ec89dcb394faf8ca989386cbd6fcb274ac4dfe90ac`

يجب تنزيل `manifest.json` أولاً، ثم تنزيل الملف المضغوط ومطابقة SHA256 قبل استخدامه. إذا لم تتطابق البصمة، تتوقف العملية `fail-closed` ولا يبدأ أي بحث.

## Schema

```text
symbol,date,open,high,low,close,volume,value_traded,trades_count,is_final,partial,source_layer,source_run_id,source_file_sha256
```

لا يتضمن الملف labels أو targets أو عوائد مستقبلية أو adjusted close أو أي أعمدة مشتقة من المستقبل. لا يغيّر نشر البيانات الحكم الحالي: `NO_PROVEN_EDGE`.
