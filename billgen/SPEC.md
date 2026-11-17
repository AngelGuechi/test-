# SPEC — Synthetic Medical Bill Statement Generator

## 1. Goal

Make **thousands of fake (but realistic) medical billing statement images**, each with a matching **answer file (JSON)**.
The images train a model to **read** the bill. The JSON tells the model the right answers, and is shaped so it can be turned into a **claim** later.

All people, providers, addresses and IDs are **made up**. No real patient data. No real provider names.

---

## 2. What every image MUST show

| # | Field | Example |
|---|-------|---------|
| 1 | Provider name + address (+ phone) | Lakeside Family Clinic, 410 Elm St, Austin TX 78701 |
| 2 | Patient name + address | Maria Lopez, 22 Oak Ave, Dallas TX 75201 |
| 3 | Bill / statement date | 2026-10-03 |
| 4 | Dates of service (one per line item) | 2026-09-12 |
| 5 | Service codes + amounts | 99213 – Office visit – $145.00 |
| 6 | Patient identifier (links to provider system) | Account # LFC-0048213 |
| 7 | Due date | 2026-11-02 |

Plus totals: charges, insurance paid, adjustments, **amount due**.

---

## 3. The simple idea (how it works)

```
 fake data  ──►  fill an HTML template  ──►  take a screenshot  ──►  messy it up a bit  ──►  PNG + JSON
 (Python)          (Jinja2)                  (Playwright)           (Pillow)
```

1. **Make up a bill** in Python (names, codes, prices, dates).
2. **Pour it into a page design** (HTML template). Several designs = several "providers' looks".
3. **Take a picture** of the page with a headless browser.
4. **Make it look real**: slight tilt, blur, noise, like a scan or phone photo.
5. **Save** the picture and the answer file side by side.

Why this way: HTML is the fastest way to make many different-looking layouts, and the browser also tells us **exactly where each field sits** on the image (bounding boxes) for free.

---

## 4. Tools (keep it small)

| Need | Tool |
|------|------|
| Language | Python 3.11+ |
| Fake names / addresses | `Faker` (locale `en_US`, optional `es_MX`) |
| Templates | `Jinja2` |
| HTML → PNG + field boxes | `playwright` (Chromium) |
| Scan/photo effects | `Pillow` + `numpy` |
| Run many at once | `multiprocessing` (built in) |

Install:
```bash
pip install faker jinja2 playwright pillow numpy
playwright install chromium
```

---

## 5. Folder layout

```
billgen/
├── SPEC.md
├── generate.py          # one command does everything
├── data/
│   ├── codes.csv        # service codes, our own short descriptions, price range
│   └── providers.json   # (optional) fixed provider list; else generated
├── templates/
│   ├── t01_clinic.html
│   ├── t02_hospital.html
│   ├── t03_lab.html
│   └── base.css
└── out/
    ├── images/  bill_000001.png ...
    ├── labels/  bill_000001.json ...
    └── manifest.csv
```

---

## 6. The answer file (JSON label)

One per image, same name. This is the "truth".

```json
{
  "id": "bill_000001",
  "template": "t01_clinic",
  "provider": {
    "name": "Lakeside Family Clinic",
    "address": "410 Elm St, Austin, TX 78701",
    "phone": "(512) 555-0142",
    "npi": "1234567893",
    "tax_id": "74-1234567"
  },
  "patient": {
    "name": "Maria Lopez",
    "address": "22 Oak Ave, Dallas, TX 75201",
    "dob": "1984-03-17",
    "account_number": "LFC-0048213"
  },
  "statement_number": "ST-2026-10-88412",
  "bill_date": "2026-10-03",
  "due_date": "2026-11-02",
  "line_items": [
    {
      "date_of_service": "2026-09-12",
      "code": "99213",
      "code_type": "CPT",
      "description": "Office visit, est. patient",
      "units": 1,
      "charge": 145.00,
      "insurance_paid": 98.40,
      "adjustment": 20.00,
      "patient_owes": 26.60
    }
  ],
  "totals": {
    "total_charges": 145.00,
    "insurance_paid": 98.40,
    "adjustments": 20.00,
    "amount_due": 26.60
  },
  "image": {"file": "bill_000001.png", "width": 1224, "height": 1584, "augmentation": {"rotate": 1.3, "blur": 0.6, "noise": 4.2, "jpeg_q": 71}},
  "fields": [
    {"field": "provider.name", "text": "Lakeside Family Clinic", "box": [x0, y0, x1, y1], "quad": [[x,y],[x,y],[x,y],[x,y]]},
    {"field": "patient.account_number", "text": "LFC-0048213", "box": [...], "quad": [...]},
    {"field": "line_items[0].code", "text": "99213", "box": [...], "quad": [...]}
  ]
}
```

- `fields` = one entry for **every place** a value is printed (a value can be printed twice, e.g. account # on the tear-off stub and in the detail). `text` is exactly what is printed (e.g. `-$98.40`, `Oct 3, 2026`); the top part of the JSON has the clean value (`98.40`, `2026-10-03`).
- `box` = straight rectangle in pixels. `quad` = the 4 exact corners (top-left, top-right, bottom-right, bottom-left) — useful when the page is tilted.
- Models that only need text answers (e.g. Donut) can ignore `fields`. Layout models (e.g. LayoutLM) use them.

---

## 7. Rules so the fake bills make sense

**Dates**
- Dates of service: 5–90 days **before** bill date.
- Bill date: random day in the last 2 years.
- Due date: bill date + 15, 21, 30 or 45 days (pick per provider and keep it the same for that provider).

**Money**
- Each code has a price range in `codes.csv`; pick a price, round to cents.
- `insurance_paid` = 0–85% of charge. `adjustment` = 0–25%. `patient_owes` = what's left (never below 0).
- Totals **must** equal the sum of the lines (checked by code, see §11).

**Line items**: 1–12 per bill. Same or different dates of service.

**Patient identifier** (the link to the provider's system)
- Each provider gets its **own ID format**, so the model learns many shapes:
  - `LFC-0048213` (prefix + 7 digits)
  - `MRN 00384751`
  - `ACCT# 2026-77812`
  - `9938-221-04`
- Label it differently per template too: "Account #", "Patient ID", "MRN", "Guarantor #", "Acct No."

**Provider types** (mix): family clinic, hospital, urgent care, lab, imaging center, dental, physical therapy.
Codes are picked to match the type (labs get lab codes, dental gets D-codes, etc.).

**Codes file (`codes.csv`)**
```
code,code_type,provider_type,description,min_price,max_price
99213,CPT,clinic,Office visit est. patient,90,180
80053,CPT,lab,Comprehensive metabolic panel,20,90
71046,CPT,imaging,Chest X-ray 2 views,60,250
D1110,CDT,dental,Adult cleaning,70,160
J1100,HCPCS,clinic,Dexamethasone injection,5,40
```
Start with ~60 common codes. **Write our own short descriptions** (official CPT long descriptions are licensed — don't copy them).

---

## 8. Templates (the "looks")

Start with **3**, grow to **8+**. Each one is different on purpose:

| Template | Look |
|----------|------|
| t01_clinic | Like the sample: header left, statement box right, simple table |
| t02_hospital | Logo bar, two-column patient/guarantor block, long table with insurance columns |
| t03_lab | Compact, small font, remittance stub at the bottom ("detach and return") |
| t04+ | Landscape, dark header, Spanish labels, dotted lines, multiple pages... |

Each template also randomly varies: font (4–5 web-safe fonts), accent color, date format (`10/03/2026`, `Oct 3, 2026`, `2026-10-03`), money format (`$1,234.50` vs `1234.50`), and where blocks sit.

**Every value in the HTML gets a tag** so we can find its box:
```html
<span data-field="patient.account_number">{{ patient.account_number }}</span>
```

Logos: simple made-up shapes/initials drawn with CSS or SVG. **No real logos or real company names.**

---

## 9. Making it look real (augmentation)

Applied to ~70% of images, random strength:

- Small rotation (±3°) and slight perspective (phone photo)
- Blur, JPEG quality 40–90
- Gray noise, paper tint, light shadow gradient
- Grayscale for ~30% (looks like a fax/scan)

Boxes are transformed with the same math so they still line up.
Keep ~30% clean so the model also sees perfect PDFs.

---

## 10. One command

```bash
python generate.py --count 5000 --seed 42 --out out/ --workers 8
```

Options: `--templates t01,t03`, `--no-augment`, `--lang es`, `--pdf` (also save PDF).

Fast path: open **one** browser per worker and reuse it; just swap the page content for each bill. Expect hundreds of images per minute on a laptop.

---

## 11. Checks (run automatically)

After generating, a quick check script confirms:
1. Every PNG has a JSON with all 7 required fields filled.
2. Totals = sum of lines (to the cent).
3. Service dates < bill date < due date.
4. Every required field has a box inside the image.
5. Spot check: save 20 random images with boxes drawn on top (`out/preview/`) so a human can look.

---

## 12. Mapping to a claim (CMS‑1500) — for later

| Bill field | CMS‑1500 box |
|------------|--------------|
| Patient name | 2 |
| Patient address | 5 |
| Patient DOB | 3 |
| Dates of service | 24A |
| Service code | 24D |
| Charge | 24F |
| Units | 24G |
| Patient account # | 26 |
| Total charge | 28 |
| Amount paid | 29 |
| Billing provider name/address/NPI | 33 |

Optional add-on: put ICD‑10 diagnosis codes on some templates (Box 21) if the claim step needs them.

---

## 13. Build plan (fastest order)

| Step | What | Done when |
|------|------|-----------|
| 1 | `codes.csv` (~60 codes) + data maker in `generate.py` | Prints a valid fake bill JSON |
| 2 | Template `t01_clinic.html` (copy of the sample look) | Renders one PNG |
| 3 | Box capture with `data-field` | JSON has boxes; preview shows them in the right spots |
| 4 | Loop + workers + `--count` | 1,000 images made in one run |
| 5 | Checks script (§11) | All checks pass |
| 6 | Templates t02, t03 | 3 looks in the mix |
| 7 | Augmentation + box transform | Messy images, boxes still match |
| 8 | More templates, Spanish labels, multi-page | 8+ looks |

Steps 1–5 = a working first dataset. Everything after just adds variety.

---

## 14. Not in this version

- Real patient or provider data
- Handwriting
- Training the model itself
- Actually submitting claims
