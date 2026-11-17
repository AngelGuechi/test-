#!/usr/bin/env python3
"""
Many bills -> many DRAFT claims + a report of what each one still needs.

    python batch.py --bills ../billgen/out/labels --out claims/drafts
"""
import argparse
import csv
from pathlib import Path

from claim_model import check
from extractors import from_json
from mapping import bill_to_claim
from store import save_claim

REPORT_COLS = ["bill_id", "claim_file", "lines", "total_charge", "errors", "warnings", "boxes_to_fill"]


def run_batch(bills_folder, out_folder, pdf=False, limit=None, progress=None):
    files = sorted(Path(bills_folder).glob("*.json"))
    if limit:
        files = files[:limit]
    out = Path(out_folder)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for n, f in enumerate(files, 1):
        try:
            bill = from_json(f)
        except Exception as e:  # noqa: BLE001
            rows.append({"bill_id": f.stem, "claim_file": "", "errors": 1, "boxes_to_fill": f"bad file: {e}"})
            continue
        claim = bill_to_claim(bill, {"file": f.name})
        claim.claim_id = f"DRAFT-{bill.get('id', f.stem)}"
        jp, _ = save_claim(claim, out, pdf=pdf, final=False)
        errors, warnings = check(claim)
        rows.append({
            "bill_id": bill.get("id", f.stem), "claim_file": jp.name, "lines": len(claim.lines),
            "total_charge": claim.total_charge, "errors": len(errors), "warnings": len(warnings),
            "boxes_to_fill": " ".join(sorted({e["box"] for e in errors}, key=lambda b: (len(b), b))),
        })
        if progress:
            progress(n, len(files))
    with open(out / "report.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REPORT_COLS)
        w.writeheader()
        w.writerows(rows)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bills", required=True, help="folder with bill JSON files (billgen out/labels)")
    ap.add_argument("--out", default="claims/drafts")
    ap.add_argument("--pdf", action="store_true", help="also make a PDF for each draft")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    rows = run_batch(a.bills, a.out, a.pdf, a.limit,
                     progress=lambda n, t: print(f"  {n}/{t}", flush=True) if n % 100 == 0 or n == t else None)
    need = {}
    for r in rows:
        for b in str(r.get("boxes_to_fill", "")).split():
            need[b] = need.get(b, 0) + 1
    print(f"Made {len(rows)} draft claims in {Path(a.out).resolve()}  (report.csv)")
    print("Boxes a person still has to fill:", ", ".join(f"{b} ({n})" for b, n in sorted(need.items())))


if __name__ == "__main__":
    main()
