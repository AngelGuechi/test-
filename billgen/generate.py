#!/usr/bin/env python3
"""
Synthetic medical bill statement generator.

Makes fake billing statement images (PNG/JPG) + a JSON answer file for each.
See SPEC.md for the full design.

    python generate.py --count 1000 --seed 42 --out out --workers 4
"""
import argparse
import csv
import io
import json
import math
import os
import random
import string
from collections import defaultdict
from datetime import date, timedelta
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from faker import Faker
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent
PAGE_W, PAGE_H = 816, 1056  # US Letter at 96 dpi (CSS pixels)

# --------------------------------------------------------------------------
# Style / variety tables
# --------------------------------------------------------------------------
PLACE_WORDS = [
    "Lakeside", "Riverbend", "Cedar Ridge", "Oakwood", "Pinecrest", "Willow Creek",
    "Harbor View", "Sunrise", "Meadowbrook", "Silver Lake", "Stonebridge", "Eastgate",
    "Bluebonnet", "Highland", "Prairie", "Copperfield", "Westbrook", "Maple Grove",
    "Red Rock", "Clearwater", "Elmhurst", "Northfield", "Southpoint", "Ironwood",
]

NAME_PATTERNS = {
    "clinic": ["{place} Family Clinic", "{place} Primary Care", "{surname} Family Medicine",
               "{place} Medical Group", "{city} Internal Medicine"],
    "urgent_care": ["{place} Urgent Care", "QuickCare of {city}", "{place} Walk-In Clinic",
                    "{city} Express Care"],
    "hospital": ["{place} Regional Medical Center", "{city} Community Hospital",
                 "{place} General Hospital", "{city} Health System"],
    "lab": ["{place} Diagnostic Laboratories", "{surname} Clinical Lab",
            "{city} Pathology Associates", "{place} Lab Services"],
    "imaging": ["{place} Imaging Center", "{city} Radiology Associates",
                "Advanced Imaging of {city}", "{place} MRI & CT"],
    "dental": ["{place} Dental Care", "{surname} Family Dentistry", "{city} Smile Studio",
               "{place} Dental Group"],
    "pt": ["{place} Physical Therapy", "{place} Rehab & Sports Therapy",
           "{surname} PT & Wellness", "{city} Movement Clinic"],
}

# Which page designs a provider type may use
TEMPLATES_FOR_TYPE = {
    "clinic": ["t01_clinic", "t02_hospital", "t03_lab"],
    "urgent_care": ["t01_clinic", "t02_hospital"],
    "hospital": ["t02_hospital", "t01_clinic"],
    "lab": ["t03_lab", "t01_clinic"],
    "imaging": ["t02_hospital", "t03_lab"],
    "dental": ["t01_clinic", "t03_lab"],
    "pt": ["t01_clinic", "t03_lab"],
}

ID_LABELS = ["Account #", "Account Number", "Patient ID", "MRN", "Acct No.",
             "Guarantor #", "Patient Account", "Medical Record #", "Account No."]
TITLES = ["BILLING STATEMENT", "PATIENT STATEMENT", "STATEMENT", "STATEMENT OF ACCOUNT",
          "Patient Statement", "Billing Statement", "INVOICE / STATEMENT"]
FONTS = [
    "Arial, 'Liberation Sans', Helvetica, sans-serif",
    "Calibri, Carlito, 'Liberation Sans', sans-serif",
    "Verdana, 'DejaVu Sans', sans-serif",
    "Georgia, 'DejaVu Serif', serif",
    "'Times New Roman', 'Liberation Serif', serif",
    "Inter, Helvetica, Arial, sans-serif",
    "Tahoma, 'DejaVu Sans Condensed', sans-serif",
]
MONO_FONTS = ["'Courier New', Courier, monospace", "'DejaVu Sans Mono', monospace"]
ACCENTS = ["#3b4a63", "#1f5f8b", "#2e7d5b", "#7a2e3b", "#5b3e8c", "#00666e",
           "#8a4b08", "#244c8f", "#4a4a4a", "#0b6e4f", "#a23b2a", "#365e9d"]
DATE_FMTS = ["%m/%d/%Y", "%b {d}, %Y", "%Y-%m-%d", "%m-%d-%Y", "%d-%b-%Y", "%B {d}, %Y"]
DOS_FMTS = ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%b {d}"]
MONEY_FMTS = ["${:,.2f}", "{:,.2f}", "$ {:,.2f}"]
ID_PATTERNS = ["{ini}-#######", "########", "20##-#####", "###-###-##",
               "{ini}######", "A########", "#######-##", "{ini}#####-#"]
STMT_PATTERNS = ["ST-######", "INV-#####", "########", "S{ini}-######", "#####-##"]
INS_A = ["Blue", "Summit", "Evergreen", "Liberty", "Keystone", "Harbor", "Pioneer",
         "Granite", "Beacon", "Horizon", "Sterling", "Cardinal"]
INS_B = ["Health Plan", "Mutual Health", "Care Network", "Benefit Plan", "Health Alliance",
         "Medical Plan"]


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def fill_pattern(rng, pattern, ini=""):
    out = pattern.replace("{ini}", ini)
    out = "".join(rng.choice(string.digits) if c == "#" else c for c in out)
    if out.startswith("A") and pattern.startswith("A"):
        out = rng.choice(string.ascii_uppercase) + out[1:]
    return out


def npi(rng):
    """10-digit NPI with a valid Luhn check digit (fake but well-formed)."""
    base = str(rng.choice([1, 2])) + "".join(rng.choice(string.digits) for _ in range(8))
    digits = [int(c) for c in "80840" + base]
    total = 0
    for i, dgt in enumerate(reversed(digits)):
        if i % 2 == 0:
            dgt *= 2
            if dgt > 9:
                dgt -= 9
        total += dgt
    return base + str((10 - total % 10) % 10)


def fmt_date(d, fmt):
    return d.strftime(fmt.replace("{d}", str(d.day)))


def fmt_money(cents, fmt):
    s = fmt.format(abs(cents) / 100)
    return ("-" + s) if cents < 0 else s


def initials(name):
    words = [w for w in name.replace("&", " ").split() if w[0].isalpha() and w.lower() not in ("of", "and")]
    return "".join(w[0] for w in words[:3]).upper()


def street_only(fake):
    """Street line without Apt/Suite (we add those ourselves, once)."""
    return f"{fake.building_number()} {fake.street_name()}"


def addr(line1, city, state, zipc, line2=""):
    return {"line1": line1, "line2": line2, "city": city, "state": state, "zip": zipc}


def addr_str(a):
    street = a["line1"] + (" " + a["line2"] if a["line2"] else "")
    return f"{street}, {a['city']}, {a['state']} {a['zip']}"


def load_codes():
    by_type = defaultdict(list)
    with open(ROOT / "data" / "codes.csv", newline="") as f:
        for r in csv.DictReader(f):
            r["min_price"] = float(r["min_price"])
            r["max_price"] = float(r["max_price"])
            r["max_units"] = int(r["max_units"])
            by_type[r["provider_type"]].append(r)
    return dict(by_type)


# --------------------------------------------------------------------------
# Providers: each one has a fixed look + ID format (like real billing systems)
# --------------------------------------------------------------------------
def make_providers(n, seed, templates=None):
    rng = random.Random(seed)
    fake = Faker("en_US")
    fake.seed_instance(seed)
    providers = []
    for i in range(n):
        ptype = rng.choice(list(NAME_PATTERNS))
        city = fake.city()
        name = rng.choice(NAME_PATTERNS[ptype]).format(
            place=rng.choice(PLACE_WORDS), surname=fake.last_name(), city=city)
        allowed = TEMPLATES_FOR_TYPE[ptype]
        if templates:
            allowed = [t for t in allowed if t in templates] or templates
        ini = initials(name)
        providers.append({
            "provider_id": f"P{i:04d}",
            "type": ptype,
            "name": name,
            "address": addr(street_only(fake), city, fake.state_abbr(include_territories=False),
                            fake.zipcode(), rng.choice(["", "", "", f"Suite {rng.randint(100, 950)}"])),
            "phone": fake.numerify("(###) 555-01##"),
            "npi": npi(rng),
            "tax_id": fake.numerify("##-#######"),
            "initials": ini,
            "id_pattern": rng.choice(ID_PATTERNS),
            "stmt_pattern": rng.choice(STMT_PATTERNS),
            "due_days": rng.choice([15, 21, 25, 30, 30, 30, 45]),
            "style": {
                "template": rng.choice(allowed),
                "accent": rng.choice(ACCENTS),
                "font": rng.choice(FONTS),
                "mono": rng.choice(MONO_FONTS),
                "title": rng.choice(TITLES),
                "id_label": rng.choice(ID_LABELS),
                "date_fmt": rng.choice(DATE_FMTS),
                "dos_fmt": rng.choice(DOS_FMTS),
                "money_fmt": rng.choice(MONEY_FMTS),
                "show_npi": rng.random() < 0.6,
                "font_size": rng.choice([12, 12.5, 13, 13.5, 14]),
            },
        })
    return providers


# --------------------------------------------------------------------------
# One fake bill
# --------------------------------------------------------------------------
def make_bill(idx, seed, providers, codes, end_date):
    rng = random.Random(seed * 1_000_003 + idx)
    fake = Faker("en_US")
    fake.seed_instance(seed * 1_000_003 + idx)
    prov = rng.choice(providers)

    bill_date = end_date - timedelta(days=rng.randint(0, 730))
    due_date = bill_date + timedelta(days=prov["due_days"])

    # 1-3 visits, 5-90 days before the bill date
    n_visits = rng.choices([1, 2, 3], weights=[6, 3, 1])[0]
    visits = sorted({bill_date - timedelta(days=rng.randint(5, 90)) for _ in range(n_visits)})
    n_visits = len(visits)  # duplicates collapse
    n_items = rng.choices(range(1, 13), weights=[10, 14, 14, 12, 10, 8, 6, 5, 4, 3, 2, 2])[0]

    pool = codes[prov["type"]]
    insured = rng.random() < 0.8
    items = []
    for k in range(n_items):
        c = rng.choice(pool)
        units = 1 if c["max_units"] == 1 or rng.random() < 0.6 else rng.randint(1, c["max_units"])
        unit_cents = int(round(rng.uniform(c["min_price"], c["max_price"]) * 100))
        if rng.random() < 0.4:  # many systems use whole-dollar prices
            unit_cents = unit_cents // 100 * 100
        charge = unit_cents * units
        if insured:
            ins = int(charge * rng.uniform(0.3, 0.85))
            adj = int(charge * rng.uniform(0.0, 0.25))
            if rng.random() < 0.1:  # sometimes insurance denies the line
                ins, adj = 0, 0
        else:
            ins = 0
            adj = int(charge * rng.uniform(0.1, 0.3)) if rng.random() < 0.5 else 0
        adj = min(adj, charge - ins)
        owes = charge - ins - adj
        items.append({
            "date_of_service": visits[min(k * n_visits // n_items, n_visits - 1)],
            "code": c["code"], "code_type": c["code_type"], "description": c["description"],
            "units": units, "unit_price": unit_cents, "charge": charge,
            "insurance_paid": ins, "adjustment": adj, "patient_owes": owes,
        })

    totals = {
        "total_charges": sum(i["charge"] for i in items),
        "insurance_paid": sum(i["insurance_paid"] for i in items),
        "adjustments": sum(i["adjustment"] for i in items),
    }
    totals["amount_due"] = totals["total_charges"] - totals["insurance_paid"] - totals["adjustments"]

    patient = {
        "name": fake.name(),
        "address": addr(street_only(fake), fake.city(), fake.state_abbr(include_territories=False),
                        fake.zipcode(), fake.secondary_address() if rng.random() < 0.25 else ""),
        # born at least 1 year before the earliest service date
        "dob": min(visits) - timedelta(days=rng.randint(365, 90 * 365)),
        "account_number": fill_pattern(rng, prov["id_pattern"], prov["initials"]),
        "insurance": f"{rng.choice(INS_A)} {rng.choice(INS_B)}" if insured else None,
    }
    return {
        "id": f"bill_{idx:06d}",
        "provider": prov,
        "patient": patient,
        "statement_number": fill_pattern(rng, prov["stmt_pattern"], prov["initials"]),
        "bill_date": bill_date,
        "due_date": due_date,
        "line_items": items,
        "totals": totals,
    }


def bill_to_label(bill):
    """Clean, JSON-ready answer file (ISO dates, dollars as numbers)."""
    p, pt = bill["provider"], bill["patient"]
    dollars = lambda c: round(c / 100, 2)
    return {
        "id": bill["id"],
        "template": p["style"]["template"],
        "provider": {
            "provider_id": p["provider_id"], "type": p["type"], "name": p["name"],
            "address": addr_str(p["address"]), "address_parts": p["address"],
            "phone": p["phone"], "npi": p["npi"], "tax_id": p["tax_id"],
        },
        "patient": {
            "name": pt["name"], "address": addr_str(pt["address"]), "address_parts": pt["address"],
            "dob": pt["dob"].isoformat(), "account_number": pt["account_number"],
            "account_label": p["style"]["id_label"], "insurance": pt["insurance"],
        },
        "statement_number": bill["statement_number"],
        "bill_date": bill["bill_date"].isoformat(),
        "due_date": bill["due_date"].isoformat(),
        "line_items": [{
            "date_of_service": i["date_of_service"].isoformat(), "code": i["code"],
            "code_type": i["code_type"], "description": i["description"], "units": i["units"],
            "unit_price": dollars(i["unit_price"]), "charge": dollars(i["charge"]),
            "insurance_paid": dollars(i["insurance_paid"]), "adjustment": dollars(i["adjustment"]),
            "patient_owes": dollars(i["patient_owes"]),
        } for i in bill["line_items"]],
        "totals": {k: dollars(v) for k, v in bill["totals"].items()},
    }


# --------------------------------------------------------------------------
# Scan / photo effects (boxes move with the image)
# --------------------------------------------------------------------------
def rotate_quad(q, angle, w, h, nw, nh):
    """Move 4 corner points the same way PIL's rotate(expand=True) moves the pixels."""
    t = math.radians(angle)
    cx, cy, ncx, ncy = w / 2, h / 2, nw / 2, nh / 2
    out = []
    for x, y in q:
        dx, dy = x - cx, y - cy
        out.append([dx * math.cos(t) + dy * math.sin(t) + ncx, -dx * math.sin(t) + dy * math.cos(t) + ncy])
    return out


def quad_to_box(q):
    xs, ys = [p[0] for p in q], [p[1] for p in q]
    return [min(xs), min(ys), max(xs), max(ys)]


def augment(img, fields, rng):
    info = {}
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    h, w = arr.shape[:2]

    # paper tint
    tint = np.array([rng.uniform(0.93, 1.0), rng.uniform(0.92, 1.0), rng.uniform(0.86, 1.0)])
    arr *= tint
    # soft shadow / uneven light
    if rng.random() < 0.5:
        g = np.linspace(rng.uniform(0.75, 0.95), 1.0, w if rng.random() < 0.5 else h)
        if rng.random() < 0.5:
            g = g[::-1]
        arr *= g[None, :, None] if g.shape[0] == w else g[:, None, None]
        info["shadow"] = True
    img = Image.fromarray(arr.clip(0, 255).astype(np.uint8))
    bg = tuple(int(255 * v * 0.97) for v in tint)

    # tilt
    angle = rng.uniform(-3, 3)
    if abs(angle) > 0.2:
        img2 = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=bg)
        nw, nh = img2.size
        for f in fields:
            f["quad"] = rotate_quad(f["quad"], angle, w, h, nw, nh)
            f["box"] = quad_to_box(f["quad"])
        img = img2
        info["rotate"] = round(angle, 2)

    # blur
    if rng.random() < 0.5:
        r = rng.uniform(0.3, 1.1)
        img = img.filter(ImageFilter.GaussianBlur(r))
        info["blur"] = round(r, 2)
    # noise
    arr = np.asarray(img).astype(np.float32)
    sd = rng.uniform(2, 10)
    arr += np.random.default_rng(rng.randint(0, 2**31)).normal(0, sd, arr.shape)
    img = Image.fromarray(arr.clip(0, 255).astype(np.uint8))
    info["noise"] = round(sd, 1)
    # fax / copier look
    if rng.random() < 0.3:
        img = ImageOps.grayscale(img).convert("RGB")
        info["gray"] = True
    # jpeg artifacts
    q = rng.randint(40, 90)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=q)
    img = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    info["jpeg_q"] = q
    return img, info


# --------------------------------------------------------------------------
# Rendering worker (one browser per process, reused for every bill)
# --------------------------------------------------------------------------
_W = {}

FIELD_JS = """() => Array.from(document.querySelectorAll('[data-field]')).map(e => {
  const rg = document.createRange(); rg.selectNodeContents(e);   // tight box around the text itself
  const r = rg.getBoundingClientRect();
  return {f: e.dataset.field, t: e.innerText.trim(),
          b: [r.left + scrollX, r.top + scrollY, r.right + scrollX, r.bottom + scrollY]};
})"""


def launch_browser(pw):
    """Use Playwright's own Chromium; if it is missing (e.g. blocked download),
    fall back to Microsoft Edge or Google Chrome already installed on the computer."""
    errors = []
    for channel in (None, "msedge", "chrome"):
        if os.environ.get("BILLGEN_SKIP_BUNDLED") and channel is None:
            continue
        try:
            return pw.chromium.launch(channel=channel) if channel else pw.chromium.launch()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{channel or 'bundled chromium'}: {str(e).splitlines()[0]}")
    raise RuntimeError("Could not start a browser. Tried:\n  " + "\n  ".join(errors) +
                       "\nFix: run  .venv\\Scripts\\python.exe -m playwright install chromium"
                       "  or install Microsoft Edge / Google Chrome.")


def init_worker(cfg, providers, codes):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = launch_browser(pw)
    page = browser.new_page(viewport={"width": PAGE_W, "height": PAGE_H},
                            device_scale_factor=cfg["scale"])
    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")),
                      autoescape=select_autoescape(["html"]))
    _W.update(cfg=cfg, providers=providers, codes=codes, pw=pw, browser=browser, page=page, env=env,
              css=(ROOT / "templates" / "base.css").read_text())


def render_one(idx):
    cfg = _W["cfg"]
    bill = make_bill(idx, cfg["seed"], _W["providers"], _W["codes"], cfg["end_date"])
    st = bill["provider"]["style"]
    html = _W["env"].get_template(st["template"] + ".html").render(
        b=bill, p=bill["provider"], pt=bill["patient"], s=st, base_css=_W["css"],
        d=lambda x: fmt_date(x, st["date_fmt"]),
        dos=lambda x: fmt_date(x, st["dos_fmt"]),
        m=lambda c: fmt_money(c, st["money_fmt"]),
        astr=addr_str,
    )
    page = _W["page"]
    page.set_content(html, wait_until="load")
    png = page.screenshot(full_page=True, type="png")
    raw = page.evaluate(FIELD_JS)

    sc = cfg["scale"]
    # one entry per place a value is printed (a value can appear more than once)
    fields = []
    for r in raw:
        x0, y0, x1, y1 = [v * sc for v in r["b"]]
        fields.append({"field": r["f"], "text": r["t"], "box": [x0, y0, x1, y1],
                       "quad": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]})  # TL, TR, BR, BL

    img = Image.open(io.BytesIO(png)).convert("RGB")
    rng = random.Random(cfg["seed"] * 7_919 + idx)
    aug = None
    if rng.random() < cfg["aug_prob"]:
        img, aug = augment(img, fields, rng)
    for f in fields:
        f["box"] = [round(v, 1) for v in f["box"]]
        f["quad"] = [[round(x, 1), round(y, 1)] for x, y in f["quad"]]

    label = bill_to_label(bill)
    label["image"] = {"file": f"{bill['id']}.{cfg['fmt']}", "width": img.width, "height": img.height,
                      "augmentation": aug}
    label["fields"] = fields  # every printed value: field, exact text, box [x0,y0,x1,y1], quad (4 corners)

    out = Path(cfg["out"])
    if cfg["fmt"] == "jpg":
        img.save(out / "images" / f"{bill['id']}.jpg", quality=92)
    else:
        img.save(out / "images" / f"{bill['id']}.png", optimize=False)
    (out / "labels" / f"{bill['id']}.json").write_text(json.dumps(label, indent=2))
    return [bill["id"], label["image"]["file"], f"{bill['id']}.json", st["template"],
            bill["provider"]["provider_id"], bill["provider"]["name"], len(bill["line_items"]),
            label["totals"]["amount_due"], aug is not None]


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="out")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--providers", type=int, default=150, help="how many different fake facilities")
    ap.add_argument("--templates", default="", help="comma list, e.g. t01_clinic,t03_lab (default: all)")
    ap.add_argument("--aug-prob", type=float, default=0.7, help="share of images made to look scanned")
    ap.add_argument("--no-augment", action="store_true")
    ap.add_argument("--scale", type=float, default=1.5, help="pixels per CSS px (1.5 = 1224x1584)")
    ap.add_argument("--format", choices=["png", "jpg"], default="png")
    ap.add_argument("--end-date", default=date.today().isoformat(), help="latest possible bill date")
    ap.add_argument("--start-index", type=int, default=None,
                    help="first bill number (default: continue after the last bill already in --out)")
    a = ap.parse_args()

    out = Path(a.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)

    templates = [t.strip() for t in a.templates.split(",") if t.strip()] or None
    providers = make_providers(a.providers, a.seed, templates)
    codes = load_codes()
    (out / "providers.json").write_text(json.dumps(providers, indent=2, default=str))

    cfg = {"seed": a.seed, "out": str(out), "scale": a.scale, "fmt": a.format,
           "aug_prob": 0.0 if a.no_augment else a.aug_prob,
           "end_date": date.fromisoformat(a.end_date)}
    if a.start_index is None:  # continue where the last run stopped
        done = [int(p.stem.split("_")[1]) for p in (out / "labels").glob("bill_*.json")]
        a.start_index = max(done, default=0) + 1
    print(f"Making bills {a.start_index} to {a.start_index + a.count - 1} in {out.resolve()}")
    idxs = range(a.start_index, a.start_index + a.count)
    header = ["id", "image", "label", "template", "provider_id", "provider_name",
              "n_items", "amount_due", "augmented"]

    # check once that a browser starts, so a problem stops the run right away
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            b = launch_browser(pw)
            print(f"Browser OK: {b.browser_type.name} {b.version}")
            b.close()
    except RuntimeError as e:
        raise SystemExit(str(e))

    rows = []
    workers = min(a.workers, a.count)
    if workers <= 1:
        init_worker(cfg, providers, codes)
        for n, i in enumerate(idxs, 1):
            rows.append(render_one(i))
            if n % 50 == 0 or n == a.count:
                print(f"  {n}/{a.count}", flush=True)
    else:
        with Pool(workers, initializer=init_worker, initargs=(cfg, providers, codes)) as pool:
            for n, row in enumerate(pool.imap_unordered(render_one, idxs, chunksize=4), 1):
                rows.append(row)
                if n % 50 == 0 or n == a.count:
                    print(f"  {n}/{a.count}", flush=True)

    # manifest lists every bill in the folder (old runs + this run)
    all_rows = []
    for lp in sorted((out / "labels").glob("bill_*.json")):
        lab = json.loads(lp.read_text())
        all_rows.append([lab["id"], lab["image"]["file"], lp.name, lab["template"],
                         lab["provider"]["provider_id"], lab["provider"]["name"], len(lab["line_items"]),
                         lab["totals"]["amount_due"], lab["image"]["augmentation"] is not None])
    with open(out / "manifest.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(all_rows)
    print(f"Done: made {len(rows)} new bills. Folder now has {len(all_rows)} bills -> {out.resolve()}")


if __name__ == "__main__":
    main()
