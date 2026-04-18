from __future__ import annotations

import json
import shutil
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
SOURCE_DIR = Path(r"C:\Users\juanc\Downloads\IVANA MAQUILLAJE")
REPORT_PATH = BASE_DIR / "image_update_report.json"


def main() -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    updated = 0

    for item in report["updated"]:
        page_number = int(item["page"])
        source_file = SOURCE_DIR / f"{page_number - 1}.jpg"
        target_file = Path(item["target"])
        if not source_file.exists():
            continue
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)
        updated += 1

    print(f"Imágenes completas copiadas: {updated}")


if __name__ == "__main__":
    main()
