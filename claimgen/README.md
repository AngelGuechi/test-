# Easy Claim AI

Turns a medical bill into a **CMS‑1500 insurance claim** (JSON + PDF), or lets you type a claim in by hand.
Full design: [SPEC.md](SPEC.md).

## Windows: the easy way

Double-click **`run.bat`**. (Colors and theme live in `.streamlit/config.toml`.) The first time it installs everything (a few minutes).
Your browser opens at http://localhost:8501. Keep the black window open while you use the app.

## By hand

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Using it

**Start** with one of:
- 📷 **Bill image** — bills from the bill generator are read automatically (their answer file is found by name in the bill generator's `out` folder).
- 📄 **Bill JSON** — a billgen answer file, or your model's output in the same shape.
- ✍️ **By hand** — empty form.
- Or pick a bill from the bill generator folder (set the folder under Settings, top right).

**Fill and check** (4 tabs). The dot next to each field:
- green: came from the bill
- orange: filled in for you, please check
- red: required and empty

**Filled in for you** (orange, to check):
- **Patient sex** — from the first name (also while typing by hand).
- **Complaint category** — from the procedure codes (e.g. 73721 → Knee, 87880 → Respiratory infection).
- **Diagnosis** — only when the procedure itself shows the reason (checkup → Z00.00, flu shot → Z23,
  mammogram → Z12.31, dental cleaning → Z01.20). Otherwise the most likely codes are listed at the top.
- **Place of service** and **diagnosis pointer**.

**Test mode** (Settings, top right): also auto-picks the most likely diagnosis when the procedure doesn't show it.
For synthetic test data only — real claims need the diagnosis from the medical record.

A person still always fills **insurance type (1)** and **member ID (1a)**.

**Create**: tab 4 lists every problem. When there are no errors, **Create claim** saves
`claims/CLM-YYYYMMDD-####.json` and `.pdf` and gives you download buttons.
**Save as draft** saves what you have so far to `claims/drafts/`.

## Batch (many bills at once)

In the app: **Batch** at the top. Or:

```bash
python batch.py --bills ../billgen/out/labels --out claims/drafts
```
(Windows: `batch.bat "C:\...\billgen\out\labels"`)

Makes one draft claim per bill + `report.csv` listing which boxes each one still needs.

## Files

| File | What it is |
|---|---|
| `app.py` | the screens |
| `claim_model.py` | what a claim is + all the rules |
| `mapping.py` | bill fields → claim boxes |
| `extractors.py` | ways to read a bill — **put your trained model in `extract_with_model`** |
| `pdf_cms1500.py` | draws the claim PDF |
| `store.py` | saves claims, makes claim IDs |
| `batch.py` | many bills → draft claims |
| `data/icd10_common.csv` | ~100 common diagnosis codes for the search box (add more rows any time) |
| `data/pos_codes.csv` | place-of-service codes |
| `data/code_hints.csv` | procedure code → complaint category + likely diagnoses (edit to tune) |
| `data/first_names_sex.csv` | first name → sex, for the sex autofill |

## Plugging in the trained model later

Edit `extract_with_model(image_bytes)` in `extractors.py` so it returns a dict shaped like a billgen
answer file (`provider`, `patient`, `bill_date`, `line_items`, `totals`). Optionally add
`"confidence": {"patient.name": 0.72, ...}` — fields under 0.9 show as 🟡 in the form.
Nothing else needs to change.
