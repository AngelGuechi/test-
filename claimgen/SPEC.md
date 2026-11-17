# SPEC — Easy Claim AI (bill → CMS‑1500 claim)

## 1. Goal

Turn a medical bill into an **insurance claim**, two ways:

1. **From a bill image** – the fields read from the bill fill the claim for you. You check them and fix them.
2. **By hand** – you type every field into a form and get a claim. No image needed.

Both ways end the same: a **checked claim** saved as **JSON** (for computers) and **PDF** (CMS‑1500 look, for people).

Works with the bills from `billgen`, and later with the trained extraction model.

---

## 2. The simple idea

```
  bill image ──► read fields ──┐
                               ├──► claim form (edit) ──► check rules ──► claim.json + claim.pdf
  bill JSON  ──► load fields ──┤
                               │
  nothing    ──► empty form ───┘
```

- **Read**: get the bill's fields (name, address, codes, money…).
- **Map**: put each field in the right claim box (e.g. account # → Box 26).
- **Fill gaps**: a bill doesn't have everything a claim needs (like diagnosis codes). The form shows these in red so a person fills them in.
- **Check**: rules catch mistakes (bad NPI, missing diagnosis, totals that don't add up).
- **Save**: JSON + PDF.

---

## 3. Stack (small and simple)

| Need | Tool | Why |
|------|------|-----|
| Language | Python 3.12 | Same as billgen |
| Screens / form | **Streamlit** | A web app in one Python file; tables you can edit; runs with one command |
| Claim rules | **Pydantic** | Describes the claim once; checks types and rules automatically |
| PDF | **ReportLab** | Draws the CMS‑1500‑style form |
| Read the bill image | Swappable "extractor" (see §6) | Start with billgen JSON; plug the trained model in later |
| Storage | Plain files (`claims/`) | No database to set up |

Install:
```bash
pip install -r requirements.txt   # streamlit, pydantic, reportlab, pillow, pandas
```
Run:
```bash
streamlit run app.py
```

---

## 4. Folder layout

```
claimgen/
├── SPEC.md
├── app.py             # the screens (Streamlit)
├── claim_model.py     # what a claim is + the rules (Pydantic)
├── mapping.py         # bill fields → claim boxes
├── extractors.py      # ways to read a bill (json | model | llm)
├── pdf_cms1500.py     # draws the claim PDF
├── store.py           # saves claims, makes claim IDs
├── batch.py           # many bills → many claims, no screens
├── run.bat / batch.bat  # double-click start on Windows
├── requirements.txt
├── data/
│   ├── icd10_common.csv     # common diagnosis codes for the picker
│   └── pos_codes.csv        # place-of-service codes (11 office, 23 ER, 81 lab…)
└── claims/
    ├── CLM-20261117-0001.json
    └── CLM-20261117-0001.pdf
```

---

## 5. Screens

One page: a start screen, then the claim form in 4 tabs (Patient & insurance · Diagnosis & services · Provider · Check & create). A summary line on top always shows how many fields are red/yellow.

### Screen 1 — Start
Three buttons:
- **Upload bill image** (PNG/JPG/PDF)
- **Upload bill JSON** (a billgen label, or the model's output)
- **Start empty claim** (manual)

### Screen 2 — Review & edit
- **Left**: the bill image with boxes drawn around the fields that were read (when there's an image).
- **Right**: the claim form, in sections that follow the CMS‑1500:
  Patient · Insured/Insurance · Diagnosis · Service lines · Provider · Totals.
- Every field has a color:
  - 🟢 **green** = came from the bill
  - 🟡 **yellow** = came from the bill but the reader wasn't sure → please look
  - 🔴 **red** = required and empty → must fill in
  - ⚪ **white** = typed by a person
- **Service lines** are an editable table (add/remove rows).
- **Totals** are calculated by themselves.
- Dropdowns for things with fixed choices: sex, insurance type, relationship to insured, place of service, diagnosis codes (searchable).

### Screen 3 — Check
- A list of problems, each with a link to the field. Example: "Line 3: diagnosis pointer is empty".
- **Generate** button stays off until there are no red errors (yellow warnings are allowed).

### Screen 4 — Done
- Download **claim.json** and **claim.pdf**.
- Saved in `claims/`.
- "New claim" button.

### Batch tab
- Pick a folder of bill JSONs → makes one *draft* claim per bill + a report of which ones are missing info.
- Command line version: `python batch.py --bills ../billgen/out/labels --out claims/`

---

## 6. Reading the bill (extractors)

All extractors give back the **same shape**: the billgen label JSON (provider, patient, bill_date, line_items, totals…), plus a `confidence` (0–1) per field. So the rest of the app never changes.

| Extractor | What it does | When |
|-----------|--------------|------|
| `json` | Loads a billgen label file (all confidence = 1.0) | **v1** – testing the whole flow today |
| `model` | Runs **your trained model** on the image | When the model is ready – just plug it in |
| `llm` (optional) | Sends the image to a vision AI (e.g. Claude API) and asks for the JSON | If you want real images to work **now**, before the model is trained. Needs an API key; check your data rules before sending patient data out. |

Image upload in v1: if a matching billgen JSON exists next to the image (same name), use it. Otherwise say "no extractor set up yet" and open the empty form with the image shown for manual typing.

---

## 7. Claim fields and where they come from

**From the bill** (filled automatically):

| CMS‑1500 box | Claim field | From bill field |
|---|---|---|
| 2 | Patient name (Last, First) | `patient.name` (split + reorder) |
| 3 | Patient birth date | `patient.dob` |
| 5 | Patient address | `patient.address_parts` |
| 11c | Insurance plan name | `patient.insurance` |
| 24A | Dates of service (from/to) | `line_items[i].date_of_service` |
| 24D | Procedure code (CPT/HCPCS) | `line_items[i].code` |
| 24F | Charges | `line_items[i].charge` |
| 24G | Units | `line_items[i].units` |
| 25 | Federal tax ID (EIN) | `provider.tax_id` |
| 26 | Patient account # | `patient.account_number` |
| 28 | Total charge | sum of 24F |
| 29 | Amount paid | `totals.insurance_paid` (editable; this is what was already paid) |
| 32 | Service facility name/address | `provider.name` + `provider.address_parts` |
| 32a / 33a | NPI | `provider.npi` |
| 33 | Billing provider name/address/phone | `provider.name`, `address_parts`, `phone` |

**Not on the bill** (a person fills in; red until filled):

| Box | Field | Help given |
|---|---|---|
| 1 | Insurance type (Medicare, Medicaid, Group…) | dropdown |
| 1a | Insured's member ID | text |
| 3 | Patient sex | dropdown |
| 4, 7 | Insured's name + address | "same as patient" checkbox (default on) |
| 6 | Relationship to insured | dropdown (default "Self") |
| 11 | Group number | text (optional) |
| 21 | **Diagnosis codes (ICD‑10)** A–L | searchable list; at least 1 |
| 24B | Place of service | dropdown, **guessed from provider type** (clinic → 11, ER → 23, lab → 81) – shown yellow |
| 24E | Diagnosis pointer per line | defaults to "A" when only one diagnosis – shown yellow |
| 27 | Accept assignment | yes/no (default yes) |
| 12, 13, 31 | Signatures | "Signature on file" checkbox + date |

> Rule: the app **never makes up diagnosis codes**. They must come from a person or from records.

Optional upgrade: add member ID, sex, diagnosis and place of service to **billgen** bills, so more boxes fill automatically.

---

## 8. Rules (checks)

**Errors (block Generate)**
- Required boxes filled: 1, 1a, 2, 3, 5, 6, 21 (≥1), 24A, 24B, 24D, 24E, 24F, 24G, 25, 26, 28, 33, 33a.
- NPI = 10 digits and passes the check-digit test (Luhn with the `80840` prefix).
- EIN = `##-#######`.
- ICD‑10 format: letter + digit + letter/digit, optional dot + 1–4 more (e.g. `J02.9`). (A code that has the right format but is not in our short list is only a warning.)
- Procedure code format: CPT `#####` or `####F/T`, HCPCS `A####`.
- Each line's diagnosis pointer points to a filled diagnosis letter (A–L), max 4 per line.
- Dates: service date ≤ today; service date ≤ bill date; birth date < service date.
- Money: charges > 0; total charge = sum of lines (to the cent); amount paid ≤ total charge.
- Units: whole number ≥ 1.

**Warnings (allowed)**
- Fields that were guessed (yellow) not reviewed yet.
- Dental codes (`D####`) → "dental claims normally use the ADA form, not CMS‑1500".
- More than 6 service lines → claim is split into more pages (CMS‑1500 holds 6 lines per page), each page with its own totals.
- Same code + same date twice → possible duplicate.

---

## 9. Claim JSON (the output)

```json
{
  "claim_id": "CLM-20261117-0001",
  "created_at": "2026-11-17T14:20:00-06:00",
  "status": "ready",
  "source": {"type": "bill_json", "bill_id": "bill_000029", "file": "bill_000029.json"},
  "form": "CMS-1500 (02/12)",
  "insurance": {"type": "group", "plan_name": "Blue Mutual Health", "member_id": "XJH442190011", "group_number": "G-88123", "payer_address": ""},
  "patient": {"last_name": "Weeks", "first_name": "Christine", "dob": "1987-01-10", "sex": "F",
              "address": {"line1": "627 David Locks", "city": "Port Jenniferland", "state": "SD", "zip": "45949"},
              "account_number": "51291257", "relationship_to_insured": "self"},
  "insured": {"same_as_patient": true},
  "diagnoses": [{"letter": "A", "code": "M25.561"}],
  "lines": [
    {"date_from": "2026-02-02", "date_to": "2026-02-02", "pos": "11", "code": "73721", "modifiers": [],
     "dx_pointer": "A", "charge": 1017.00, "units": 1, "rendering_npi": "2978207159"}
  ],
  "totals": {"total_charge": 6163.67, "amount_paid": 3188.88},
  "billing_provider": {"name": "Prairie Imaging Center", "npi": "2978207159", "tax_id": "86-5281685",
                       "phone": "(629) 555-0114",
                       "address": {"line1": "925 Brandi Divide", "city": "Codyfurt", "state": "MI", "zip": "41163"}},
  "service_facility": {"same_as_billing": true},
  "accept_assignment": true,
  "signature_on_file": true,
  "field_sources": {"patient.last_name": {"from": "bill", "confidence": 1.0}, "diagnoses[0].code": {"from": "manual"}},
  "validation": {"errors": [], "warnings": ["24B place of service was guessed"]}
}
```

`field_sources` remembers where every value came from (bill or person). This is useful later to measure how good the model is.

---

## 10. PDF

- One letter-size page per 6 service lines.
- Drawn to look like the CMS‑1500 layout (boxes + numbers + labels), black ink.
- Not the official red OCR form. For real paper submission, values would be printed onto the official red form; that's a later option.

---

## 11. Build plan (fastest order)

| Step | What | Done when |
|---|---|---|
| 1 | `claim_model.py` (Pydantic claim + rules) | A test claim passes / fails the right rules |
| 2 | `mapping.py` (billgen JSON → draft claim) | All 1,000 billgen bills become drafts; only the "not on bill" boxes are empty |
| 3 | `pdf_cms1500.py` | A claim prints as a readable 1–2 page PDF |
| 4 | `app.py`: Start → Edit → Check → Done, with manual entry | Can make a claim by hand and from a bill JSON |
| 5 | Image view with field boxes + colors | Uploading a billgen image shows its boxes and fills the form |
| 6 | `batch.py` + `run.bat` | Folder of bills → folder of draft claims + report |
| 7 | Plug in an image extractor (`model` or `llm`) | A new image with no JSON fills the form |
| 8 (later) | X12 **837P** electronic claim file | Same claim exported as an EDI file for clearinghouses |

Steps 1–4 = a working claim builder. Steps 5–7 make it faster to use.

---

## 12. Not in this version

- Sending claims to insurers or clearinghouses
- 837P electronic files (step 8, later)
- Dental (ADA) and hospital facility (UB‑04) claim forms
- Users / logins / database
- Choosing diagnosis codes automatically

---

## 13. Build status (v1)

Built: steps 1–6 (rules, mapping, PDF, app with manual entry and bill image/JSON, image with field boxes + colors, batch + Windows starters).
Tested: all 1,000 billgen bills map to drafts where only boxes 1, 1a, 3 and 21 are left for a person; full app flow creates `CLM-…json` + `.pdf`.
Next: step 7 (plug the trained model into `extractors.extract_with_model`), step 8 (837P).

## 14. v1.1 changes

- Name: **Easy Claim AI**, big blue header. Theme white + blue (`.streamlit/config.toml`). No emojis on buttons; field status shown as colored dots.
- Autofill (all shown orange = "filled in for you, please check"):
  - Patient sex from first name (`data/first_names_sex.csv`).
  - Complaint category from procedure codes (`data/code_hints.csv`); stored as `complaint_category` in the claim JSON.
  - Diagnosis only when implied by the procedure (preventive, vaccine, screening, dental cleaning); otherwise likely codes are sorted first.
  - Test mode (off by default): auto-picks the top suggested diagnosis — synthetic data only.
- Services tab order: service lines → complaint category → diagnoses (so suggestions follow the codes).

## 15. v1.2 changes

- No sidebar. Top bar: logo + name on the left, Claim / Batch switch and Settings on the right.
- Calmer look: no gradient banner, Inter font, small corner radius, Streamlit menus hidden.
- Claim page header shows the patient name, the source, and small status tags (required / to review / ready).
