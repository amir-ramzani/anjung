# Malay review

The Malay text in the app and the emails was machine-written. Before go-live a native speaker should check it (status
checklist P4-F4).

## For the reviewer

Open `malay-review.csv` in Excel or Google Sheets. Each row is one text:

| Column | What it is |
|---|---|
| Where | The app screen, an email (with its template name), or a phrase kept in code |
| Key | Where a developer finds it |
| English | The English text |
| Malay (machine-written) | What the app shows now |
| Corrected Malay | Fill this in only where the Malay is wrong, unnatural, or too formal |
| Note | Anything else (for example "use *penyewa* consistently") |

Keep the parts in curly brackets and after a dollar sign exactly as they are, for example `{{name}}`, `{{count}}`,
`$link`, `$org_name`. The app fills these in. Texts ending in `_one` and `_other` are the singular and plural forms; in
Malay they are usually the same.

## For developers

```
python3 docs/translation/malay_review.py
```

rebuilds the sheet from `frontend/src/i18n/en.json`, `ms.json` and the email templates
(`backend/app/domains/notifications/templates/<template>/{en,ms}.{subject,txt,html}`). Corrections and notes already in
the sheet are kept. Copy each correction into `ms.json` or the template, then run the frontend tests (the i18n test
checks that every key exists in both languages).
