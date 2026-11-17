#!/usr/bin/env python3
"""
Checks a generated dataset and makes preview images with field boxes drawn on.

    python check.py --out out --previews 20
"""
import argparse
import json
import random
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw

REQUIRED = ["provider.name", "provider.address", "patient.name", "patient.address",
            "bill_date", "due_date", "patient.account_number"]
REQUIRED_PER_LINE = ["date_of_service", "code", "charge"]

COLORS = {"provider": "#1f77b4", "patient": "#d62728", "bill_date": "#2ca02c", "due_date": "#2ca02c",
          "statement_number": "#7f7f7f", "line_items": "#ff7f0e", "totals": "#9467bd"}


def check_one(label, img_size):
    errs = []
    lab = label
    # 1. required values present
    for k in ["provider.name", "provider.address", "patient.name", "patient.address",
              "patient.account_number", "bill_date", "due_date"]:
        obj = lab
        for part in k.split("."):
            obj = obj.get(part) if isinstance(obj, dict) else None
        if not obj:
            errs.append(f"missing value {k}")
    if not lab["line_items"]:
        errs.append("no line items")

    # 2. money adds up (to the cent)
    cents = lambda x: round(x * 100)
    t = lab["totals"]
    for key, line_key in [("total_charges", "charge"), ("insurance_paid", "insurance_paid"),
                          ("adjustments", "adjustment"), ("amount_due", "patient_owes")]:
        if sum(cents(i[line_key]) for i in lab["line_items"]) != cents(t[key]):
            errs.append(f"total {key} != sum of lines")
    for n, i in enumerate(lab["line_items"]):
        if cents(i["charge"]) - cents(i["insurance_paid"]) - cents(i["adjustment"]) != cents(i["patient_owes"]):
            errs.append(f"line {n} does not add up")
        if cents(i["unit_price"]) * i["units"] != cents(i["charge"]):
            errs.append(f"line {n} units x price != charge")
    if cents(t["total_charges"]) - cents(t["insurance_paid"]) - cents(t["adjustments"]) != cents(t["amount_due"]):
        errs.append("totals do not add up")

    # 3. dates in order
    bd, dd = date.fromisoformat(lab["bill_date"]), date.fromisoformat(lab["due_date"])
    if not bd < dd:
        errs.append("bill_date not before due_date")
    for n, i in enumerate(lab["line_items"]):
        if not date.fromisoformat(i["date_of_service"]) < bd:
            errs.append(f"line {n} service date not before bill date")

    # 4. every required field is printed, with a box inside the image
    w, h = img_size
    seen = {}
    for f in lab["fields"]:
        x0, y0, x1, y1 = f["box"]
        if not (-2 <= x0 < x1 <= w + 2 and -2 <= y0 < y1 <= h + 2):
            errs.append(f"box outside image: {f['field']}")
        if not f["text"]:
            errs.append(f"empty text: {f['field']}")
        seen[f["field"]] = True
    for k in REQUIRED:
        if k not in seen:
            errs.append(f"not printed: {k}")
    for n in range(len(lab["line_items"])):
        for k in REQUIRED_PER_LINE:
            if f"line_items[{n}].{k}" not in seen:
                errs.append(f"not printed: line_items[{n}].{k}")
    return errs


def draw_preview(img, label, path):
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)
    for f in label["fields"]:
        group = f["field"].split(".")[0].split("[")[0]
        c = COLORS.get(group, "#17becf")
        d.polygon([tuple(pt) for pt in f["quad"]], outline=c, width=2)
        d.text((f["box"][0], f["box"][1] - 11), f["field"], fill=c)
    img.save(path, quality=85)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--previews", type=int, default=20)
    a = ap.parse_args()
    out = Path(a.out)
    labels = sorted((out / "labels").glob("*.json"))
    bad = 0
    pick = set(random.Random(0).sample(range(len(labels)), min(a.previews, len(labels))))
    (out / "preview").mkdir(exist_ok=True)
    for n, lp in enumerate(labels):
        lab = json.loads(lp.read_text())
        ip = out / "images" / lab["image"]["file"]
        if not ip.exists():
            print(f"{lab['id']}: image missing")
            bad += 1
            continue
        img = Image.open(ip)
        if img.size != (lab["image"]["width"], lab["image"]["height"]):
            print(f"{lab['id']}: size mismatch")
        errs = check_one(lab, img.size)
        if errs:
            bad += 1
            print(f"{lab['id']}: " + "; ".join(errs[:5]))
        if n in pick:
            draw_preview(img, lab, out / "preview" / f"{lab['id']}.jpg")
    print(f"Checked {len(labels)} bills: {len(labels) - bad} OK, {bad} with problems. "
          f"Previews in {out / 'preview'}")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
