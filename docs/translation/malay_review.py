"""Build the Malay review sheet: every English text with its machine-written Malay, side by side, for a
native speaker to check (status checklist P4-F4). Run from the repo root:

    python3 docs/translation/malay_review.py

Writes docs/translation/malay-review.csv (UTF-8 with BOM, opens in Excel or Google Sheets). The
reviewer fills in "Corrected Malay" and "Note"; a developer copies the corrections back into
frontend/src/i18n/ms.json or the email templates. Re-running keeps existing corrections and notes.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).with_name("malay-review.csv")
I18N = ROOT / "frontend" / "src" / "i18n"
TEMPLATES = ROOT / "backend" / "app" / "domains" / "notifications" / "templates"
HEADER = ["Where", "Key", "English", "Malay (machine-written)", "Corrected Malay", "Note"]


def flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))
        else:
            out[key] = str(v)
    return out


def rows() -> list[list[str]]:
    en = flatten(json.loads((I18N / "en.json").read_text()))
    ms = flatten(json.loads((I18N / "ms.json").read_text()))
    out = [["App screen", k, en[k], ms.get(k, "(missing)")] for k in en]
    out += [["App screen", k, "(not in English)", ms[k]] for k in ms if k not in en]
    for tdir in sorted(p for p in TEMPLATES.iterdir() if p.is_dir()):
        for part in ("subject", "txt", "html"):
            e, m = tdir / f"en.{part}", tdir / f"ms.{part}"
            if e.exists():
                text = m.read_text().strip() if m.exists() else "(missing)"
                out.append([f"Email: {tdir.name}", part, e.read_text().strip(), text])
    # a few phrases kept in code: the branded email footer and the role names in the staff welcome email
    out += [
        ["Email footer (templates.py)", "FOOTER", "Sent by {name} through Anjung.", "Dihantar oleh {name} melalui Anjung."],
        ["Email footer (templates.py)", "REG", "Registration no.", "No. pendaftaran"],
        ["Email: invite_staff (staff_invite.py)", "ROLE.org_admin", "an administrator", "pentadbir"],
        ["Email: invite_staff (staff_invite.py)", "ROLE.property_manager", "a property manager", "pengurus hartanah"],
    ]
    return out


def main() -> None:
    kept: dict[tuple[str, str], tuple[str, str]] = {}
    if OUT.exists():
        with OUT.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r.get("Corrected Malay") or r.get("Note"):
                    kept[(r["Where"], r["Key"])] = (r["Corrected Malay"], r["Note"])
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for r in rows():
            w.writerow([*r, *kept.get((r[0], r[1]), ("", ""))])
    print(f"{OUT.relative_to(ROOT)}: {len(rows())} texts")


if __name__ == "__main__":
    main()
