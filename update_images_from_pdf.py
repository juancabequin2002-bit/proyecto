from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import fitz
from PIL import Image
from rapidocr_onnxruntime import RapidOCR


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "malu_makeup.db"
DEFAULT_PDF = Path(r"C:\Users\juanc\Downloads\IVANA MAQUILLAJE.pdf")
REPORT_PATH = BASE_DIR / "image_update_report.json"
MANUAL_OVERRIDES = {
    "BAQ564": {"page": 4, "crop": (0.0, 0.0, 1.0, 1.0)},
    "BQF2053": {"page": 14, "crop": (0.0, 0.46, 1.0, 1.0)},
    "CQT1981": {"page": 27, "crop": (0.0, 0.46, 1.0, 1.0)},
    "LLI1735": {"page": 45, "crop": (0.0, 0.0, 1.0, 1.0)},
    "TPI1414": {"page": 56, "crop": (0.0, 0.0, 1.0, 1.0)},
    "SRO1919": {"page": 118, "crop": (0.0, 0.48, 1.0, 1.0)},
    "PQT1980": {"page": 175, "crop": (0.0, 0.0, 1.0, 0.52)},
    "DOX888": {"page": 184, "crop": (0.0, 0.48, 1.0, 1.0)},
    "CMO2402": {"page": 189, "crop": (0.0, 0.0, 1.0, 0.34)},
    "OMT1667": {"page": 199, "crop": (0.0, 0.0, 1.0, 1.0)},
    "P100": {"page": 220, "crop": (0.0, 0.0, 0.38, 1.0)},
}


def norm(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def load_products() -> dict[str, dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, reference, image_path FROM products WHERE reference != '' ORDER BY id"
    ).fetchall()
    conn.close()
    return {row["reference"]: dict(row) for row in rows}


def find_matches(ocr_result: list, product_refs: set[str]) -> list[dict]:
    matches = []
    for item in ocr_result or []:
        box, text, score = item
        normalized_text = norm(text)
        for ref in product_refs:
            if len(ref) < 4:
                continue
            if norm(ref) in normalized_text:
                xs = [point[0] for point in box]
                ys = [point[1] for point in box]
                matches.append(
                    {
                        "reference": ref,
                        "text": text,
                        "score": float(score),
                        "bbox": [min(xs), min(ys), max(xs), max(ys)],
                    }
                )
    deduped = {}
    for match in matches:
        current = deduped.get(match["reference"])
        if current is None or match["score"] > current["score"]:
            deduped[match["reference"]] = match
    return list(deduped.values())


def crop_boxes(page_size: tuple[int, int], matches: list[dict]) -> dict[str, tuple[int, int, int, int]]:
    width, height = page_size
    if len(matches) == 1:
        ref = matches[0]["reference"]
        return {ref: (0, 0, width, height)}

    centers_x = [(item["bbox"][0] + item["bbox"][2]) / 2 for item in matches]
    centers_y = [(item["bbox"][1] + item["bbox"][3]) / 2 for item in matches]
    spread_x = max(centers_x) - min(centers_x)
    spread_y = max(centers_y) - min(centers_y)
    axis = "y" if spread_y >= spread_x else "x"

    items = sorted(matches, key=lambda item: ((item["bbox"][1] + item["bbox"][3]) / 2, (item["bbox"][0] + item["bbox"][2]) / 2) if axis == "y" else ((item["bbox"][0] + item["bbox"][2]) / 2, (item["bbox"][1] + item["bbox"][3]) / 2))
    crops: dict[str, tuple[int, int, int, int]] = {}

    if axis == "y":
        centers = [((item["bbox"][1] + item["bbox"][3]) / 2) for item in items]
        for index, item in enumerate(items):
            top = 0 if index == 0 else int((centers[index - 1] + centers[index]) / 2) - 35
            bottom = height if index == len(items) - 1 else int((centers[index] + centers[index + 1]) / 2) + 35
            crops[item["reference"]] = (0, max(0, top), width, min(height, bottom))
    else:
        centers = [((item["bbox"][0] + item["bbox"][2]) / 2) for item in items]
        for index, item in enumerate(items):
            left = 0 if index == 0 else int((centers[index - 1] + centers[index]) / 2) - 35
            right = width if index == len(items) - 1 else int((centers[index] + centers[index + 1]) / 2) + 35
            crops[item["reference"]] = (max(0, left), 0, min(width, right), height)
    return crops


def save_crop(image: Image.Image, product: dict, crop: tuple[int, int, int, int], report: dict, page_number: int, mode: str) -> None:
    target = BASE_DIR / product["image_path"].lstrip("/").replace("/", "\\")
    target.parent.mkdir(parents=True, exist_ok=True)
    image.crop(crop).save(target)
    report["updated"].append(
        {
            "reference": product["reference"],
            "name": product["name"],
            "page": page_number,
            "target": str(target),
            "mode": mode,
        }
    )


def apply_manual_overrides(doc: fitz.Document, products: dict[str, dict], updated_refs: set[str], report: dict) -> None:
    for reference, override in MANUAL_OVERRIDES.items():
        if reference in updated_refs or reference not in products:
            continue
        page_number = override["page"]
        page = doc.load_page(page_number - 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.55, 1.55), alpha=False)
        image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        left, top, right, bottom = override["crop"]
        crop = (
            int(image.width * left),
            int(image.height * top),
            int(image.width * right),
            int(image.height * bottom),
        )
        save_crop(image, products[reference], crop, report, page_number, "manual_override")
        updated_refs.add(reference)


def update_images(pdf_path: Path = DEFAULT_PDF) -> dict:
    products = load_products()
    doc = fitz.open(pdf_path)
    ocr = RapidOCR()
    report = {"updated": [], "unmatched": [], "pages": []}

    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.55, 1.55), alpha=False)
        image_path = BASE_DIR / "_ocr_page.png"
        pix.save(image_path)

        result, _ = ocr(str(image_path))
        matches = find_matches(result, set(products))
        if not matches:
            continue

        image = Image.open(image_path)
        crops = crop_boxes(image.size, matches)
        report["pages"].append({"page": page_index + 1, "references": [item["reference"] for item in matches]})

        for item in matches:
            product = products[item["reference"]]
            save_crop(image, product, crops[item["reference"]], report, page_index + 1, "ocr")

    image_path.unlink(missing_ok=True)
    updated_refs = {item["reference"] for item in report["updated"]}
    apply_manual_overrides(doc, products, updated_refs, report)
    report["unmatched"] = [
        {"reference": product["reference"], "name": product["name"]}
        for product in products.values()
        if len(product["reference"]) >= 4 and product["reference"] not in updated_refs
    ]
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    summary = update_images()
    print(f"Imágenes actualizadas: {len(summary['updated'])}")
    print(f"Referencias sin match automático: {len(summary['unmatched'])}")
