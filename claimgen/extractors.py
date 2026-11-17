"""
Ways to read a bill. Every extractor returns the SAME shape: a billgen-style
label dict (provider, patient, bill_date, due_date, line_items, totals, ...)
plus optional "confidence": {field_path: 0..1} and "fields" (boxes on the image).

To plug in your trained model later, fill in `extract_with_model`.
"""
import json
from pathlib import Path


def from_json(data) -> dict:
    """A billgen label file (or anything already in that shape)."""
    if isinstance(data, (str, Path)):
        data = json.loads(Path(data).read_text(encoding="utf-8"))
    elif isinstance(data, (bytes, bytearray)):
        data = json.loads(data.decode("utf-8"))
    if "provider" not in data or "line_items" not in data:
        raise ValueError("This JSON does not look like a bill (needs 'provider' and 'line_items').")
    return data


def find_label_for_image(image_name: str, folders: list) -> dict | None:
    """billgen images have a JSON with the same name in ../labels — use it if we can find it."""
    stem = Path(image_name).stem
    for folder in folders:
        if not folder:
            continue
        folder = Path(folder)
        for cand in (folder / "labels" / f"{stem}.json", folder / f"{stem}.json"):
            if cand.exists():
                return from_json(cand)
    return None


def extract_with_model(image_bytes: bytes) -> dict | None:
    """TODO: run the trained extraction model here and return the billgen-shaped dict.
    Return None while no model is set up."""
    return None
