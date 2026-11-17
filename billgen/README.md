# billgen — fake medical bill images for training

Makes realistic **fake** medical billing statements as images, each with a JSON answer file.
Full design: [SPEC.md](SPEC.md).

## Windows: the easy way

Double-click `run.bat`. The first time, it installs everything (a few minutes). Then it makes 100 bills in the `out` folder.
To make more, open a terminal in this folder and type `run.bat 2000`.

## Setup by hand (once)

```bash
pip install faker jinja2 playwright pillow numpy
playwright install chromium
```

## Make bills

```bash
python generate.py --count 1000 --seed 42 --out out
python check.py --out out          # checks every bill + draws boxes on 20 previews
```

Output:

```
out/images/bill_000001.png     the picture
out/labels/bill_000001.json    the answers + where each value is on the picture
out/manifest.csv               one row per bill
out/providers.json             the fake facilities used
out/preview/*.jpg              pictures with field boxes drawn on (from check.py)
```

## Useful options

| Option | What it does |
|---|---|
| `--count 5000` | how many bills |
| `--seed 7` | different seed = different bills (same seed = same bills again) |
| `--providers 300` | how many different fake facilities |
| `--templates t01_clinic,t03_lab` | only use some designs |
| `--no-augment` | all clean, no scan/photo effects |
| `--aug-prob 0.5` | share of images made to look scanned (default 0.7) |
| `--format jpg` | ~5x smaller files than PNG |
| `--scale 2` | bigger images (default 1.5 = 1224×1584) |
| `--workers 8` | use more CPU cores |
| `--start-index 5001` | pick the first bill number yourself (normally not needed) |

Speed: about 330 bills/minute on 2 CPU cores.

**Keep going later:** run the same command again with the same `--out` folder. It continues after the last bill (e.g. 1001, 1002, ...) and the manifest lists all of them. Keep the same `--seed` so the same fake facilities are reused.

## What each bill has

Provider name/address/phone (+ NPI, tax ID on some), patient name/address/DOB,
patient account # (format and label differ per provider), statement #, bill date,
due date, line items (date of service, code, description, units, charge,
insurance paid, adjustment, patient balance) and totals.

Designs: `t01_clinic` (framed, like the sample), `t02_hospital` (header bar + summary boxes),
`t03_lab` (tear-off payment stub on top). Each fake facility keeps the same design, colors,
fonts, date style and ID format on every bill, like a real billing system does.

## Add a new design

1. Copy `templates/t01_clinic.html` to `templates/t04_something.html` and restyle it.
2. Wrap every value in a tag with `data-field`, e.g.
   `<span data-field="patient.account_number">{{ pt.account_number }}</span>`.
3. Add `"t04_something"` to `TEMPLATES_FOR_TYPE` in `generate.py`.
4. Run a small batch and `check.py` — it fails if a required field is not printed.

## Add service codes

Add rows to `data/codes.csv`. Write your own short descriptions (official CPT wording is licensed).
