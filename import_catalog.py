from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import fitz


BASE_DIR = Path(__file__).resolve().parent
PDF_PATH = BASE_DIR / "source" / "27_de_mayo_MaLuMakeup_solo_precio_venta (1).pdf"
STATIC_DIR = BASE_DIR / "static"
IMAGE_DIR = STATIC_DIR / "catalog"
DATABASE_PATH = BASE_DIR / "malu_makeup.db"
SKIP_PAGES = 6

COMMON_LABELS = {
    "EMPRENDEDOR",
    "DISTRIBUIDOR",
    "PRECIO DE VENTA",
    "MAQUILLAJE  |  ROSTRO",
    "MAQUILLAJE | ROSTRO",
    "SÍGUENOS EN :",
    "SIGUENOS EN :",
    "WWW.MAQUILLAJETRENDYSHOP.COM",
    "NUEVA",
    "IMAGEN",
    "LANZAMIENTO",
    "CATEGORÍA",
}

PRICE_PATTERN = re.compile(r"\$ ?([\d.]+)")
REFERENCE_PATTERN = re.compile(r"Ref\s+([A-Z0-9]+)", re.IGNORECASE)


@dataclass
class TextItem:
    text: str
    size: float
    bbox: tuple[float, float, float, float]


def clean_text(value: str) -> str:
    value = " ".join(value.replace("\ufb01", "fi").replace("\ufb02", "fl").split())
    return value.strip()


def parse_money(text: str) -> int | None:
    match = PRICE_PATTERN.search(text)
    if not match:
        return None
    return int(match.group(1).replace(".", ""))


def crop_mode_for_titles(title: TextItem, page_width: float) -> tuple[float, float]:
    center_x = (title.bbox[0] + title.bbox[2]) / 2
    if center_x < page_width * 0.35:
        return 0.0, page_width * 0.58
    if center_x > page_width * 0.65:
        return page_width * 0.42, page_width
    return 0.0, page_width


def collect_text(page: fitz.Page) -> list[TextItem]:
    data = page.get_text("dict")
    items: list[TextItem] = []
    for block in data["blocks"]:
        for line in block.get("lines", []):
            text = clean_text("".join(span["text"] for span in line["spans"]))
            if not text:
                continue
            size = max(span["size"] for span in line["spans"])
            items.append(TextItem(text=text, size=size, bbox=tuple(line["bbox"])))
    return items


def title_candidates(items: list[TextItem]) -> list[TextItem]:
    picked = []
    for item in items:
        upper = item.text.upper()
        if item.size < 34:
            continue
        if item.text.isdigit() or len(item.text) < 6:
            continue
        if "PRECIO" in upper or "TONO" in upper or "REF" in upper:
            continue
        if upper in COMMON_LABELS:
            continue
        if sum(char.isalpha() for char in item.text) < 5:
            continue
        picked.append(item)

    deduped = []
    seen: set[tuple[str, int, int]] = set()
    for item in sorted(picked, key=lambda current: (-current.size, current.bbox[1], current.bbox[0])):
        key = (item.text.lower(), round(item.bbox[0]), round(item.bbox[1]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return sorted(deduped, key=lambda current: (current.bbox[1], current.bbox[0]))


def reference_items(items: list[TextItem]) -> list[tuple[TextItem, str]]:
    refs = []
    seen = set()
    for item in items:
        match = REFERENCE_PATTERN.search(item.text)
        if not match:
            continue
        ref = match.group(1).upper()
        key = (ref, round(item.bbox[0]), round(item.bbox[1]))
        if key in seen:
            continue
        seen.add(key)
        refs.append((item, ref))
    return refs


def section_bounds_for_ref(
    ref_item: TextItem,
    refs: list[tuple[TextItem, str]],
    page_height: float,
) -> tuple[float, float]:
    ordered_refs = sorted((item for item, _ in refs), key=lambda current: current.bbox[1])
    current_index = next((index for index, item in enumerate(ordered_refs) if item is ref_item), None)
    if current_index is None:
        return 0.0, page_height

    current_center = (ref_item.bbox[1] + ref_item.bbox[3]) / 2
    if current_index == 0:
        top = 0.0
    else:
        previous_item = ordered_refs[current_index - 1]
        previous_center = (previous_item.bbox[1] + previous_item.bbox[3]) / 2
        top = (previous_center + current_center) / 2

    if current_index == len(ordered_refs) - 1:
        bottom = page_height
    else:
        next_item = ordered_refs[current_index + 1]
        next_center = (next_item.bbox[1] + next_item.bbox[3]) / 2
        bottom = (current_center + next_center) / 2

    return max(0.0, top), min(page_height, bottom)


def text_items_in_section(items: list[TextItem], top: float, bottom: float) -> list[TextItem]:
    section_items = []
    for item in items:
        center_y = (item.bbox[1] + item.bbox[3]) / 2
        if top <= center_y <= bottom:
            section_items.append(item)
    return section_items


def visual_column_bounds(
    section_items: list[TextItem],
    page_width: float,
    section_height: float,
) -> tuple[float, float]:
    if section_height <= 0:
        return 0.0, page_width

    step = 24
    threshold = max(120.0, section_height * 0.40)
    bins = []
    for start in range(0, int(page_width), step):
        end = min(page_width, start + step)
        occupancy = 0.0
        for item in section_items:
            width_overlap = max(0.0, min(end, item.bbox[2]) - max(start, item.bbox[0]))
            if width_overlap <= 0:
                continue
            occupancy += item.bbox[3] - item.bbox[1]
        bins.append((start, end, occupancy))

    best = None
    current_start = None
    current_end = None
    for start, end, occupancy in bins:
        if occupancy <= threshold:
            current_start = start if current_start is None else current_start
            current_end = end
            continue
        if current_start is not None:
            width = current_end - current_start
            if best is None or width > (best[1] - best[0]):
                best = (current_start, current_end)
            current_start = None
            current_end = None

    if current_start is not None and current_end is not None:
        width = current_end - current_start
        if best is None or width > (best[1] - best[0]):
            best = (current_start, current_end)

    if best is None or (best[1] - best[0]) < page_width * 0.22:
        return 0.0, page_width

    x0 = max(0.0, best[0] - 18)
    x1 = min(page_width, best[1] + 18)
    return x0, x1


def description_for_title(items: list[TextItem], title: TextItem, price: TextItem | None) -> str:
    snippets = []
    lower_bound = title.bbox[3] + 5
    upper_bound = price.bbox[1] - 10 if price else title.bbox[3] + 260
    for item in items:
        if item.bbox[1] < lower_bound or item.bbox[1] > upper_bound:
            continue
        upper = item.text.upper()
        if "PRECIO" in upper or "TONO" in upper or "VENTA" in upper or "UNIDAD" in upper:
            continue
        if len(item.text) < 18:
            continue
        if sum(char.isalpha() for char in item.text) < 12:
            continue
        snippets.append(item.text)
    return " ".join(snippets[:4]).strip()


def image_blocks(page: fitz.Page) -> list[tuple[float, float, float, float]]:
    blocks = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 1:
            continue
        bbox = tuple(block.get("bbox", (0.0, 0.0, 0.0, 0.0)))
        if bbox[2] - bbox[0] < 80 or bbox[3] - bbox[1] < 80:
            continue
        blocks.append(bbox)
    return blocks


def pick_image_block(
    blocks: list[tuple[float, float, float, float]],
    title: TextItem,
    price: TextItem,
    x0: float,
    x1: float,
    page_width: float,
    page_height: float,
) -> tuple[float, float, float, float] | None:
    best_score = None
    best_block = None
    title_center_x = (title.bbox[0] + title.bbox[2]) / 2

    for block in blocks:
        bx0, by0, bx1, by1 = block
        width = bx1 - bx0
        height = by1 - by0

        if width > page_width * 0.92 and height > page_height * 0.92:
            continue
        if bx1 < x0 or bx0 > x1:
            continue
        if by0 >= price.bbox[1]:
            continue

        center_x = (bx0 + bx1) / 2
        score = abs(center_x - title_center_x)

        if by1 < title.bbox[3]:
            score += 180
        else:
            score += abs(by1 - price.bbox[1]) * 0.1

        if width < 120 or height < 120:
            score += 120

        if best_score is None or score < best_score:
            best_score = score
            best_block = block

    return best_block


def pair_products(page: fitz.Page) -> list[dict]:
    items = collect_text(page)
    titles = title_candidates(items)
    prices = [item for item in items if "Precio:" in item.text]
    sale_prices = [item for item in items if "PRECIO DE VENTA" in item.text.upper()]
    refs = reference_items(items)
    products = []

    for ref_item, ref in refs:
        section_top, section_bottom = section_bounds_for_ref(ref_item, refs, page.rect.height)

        nearby_titles = []
        for title in titles:
            if abs(title.bbox[0] - ref_item.bbox[0]) > 260:
                continue
            if title.bbox[1] > ref_item.bbox[1] + 40:
                continue
            if ref_item.bbox[1] - title.bbox[1] > 230:
                continue
            nearby_titles.append(title)
        nearby_titles = sorted(nearby_titles, key=lambda current: current.bbox[1])
        if not nearby_titles:
            continue
        name = " ".join(dict.fromkeys(title.text for title in nearby_titles[:2]))
        title = nearby_titles[0]

        nearby_prices = []
        for price in prices:
            center_y = (price.bbox[1] + price.bbox[3]) / 2
            if center_y < section_top or center_y > section_bottom:
                continue
            score = abs(price.bbox[1] - ref_item.bbox[1]) + abs(price.bbox[0] - ref_item.bbox[0]) * 0.25
            if price.bbox[1] <= ref_item.bbox[1]:
                score += 120
            nearby_prices.append((score, price))
        if not nearby_prices:
            continue

        _, selected_price = sorted(nearby_prices, key=lambda current: current[0])[0]
        sale_options = []
        for item in sale_prices:
            center_y = (item.bbox[1] + item.bbox[3]) / 2
            if center_y < section_top or center_y > section_bottom:
                continue
            score = abs(item.bbox[1] - selected_price.bbox[1]) + abs(item.bbox[0] - selected_price.bbox[0]) * 0.25
            sale_options.append((score, item))
        matching_sale = sorted(sale_options, key=lambda current: current[0])[0][1] if sale_options else None

        if selected_price.bbox[1] - ref_item.bbox[1] > 500:
            continue

        products.append(
            {
                "name": name,
                "reference": ref,
                "description": description_for_title(items, title, selected_price),
                "cost_price": parse_money(selected_price.text) or 0,
                "sale_price": parse_money(matching_sale.text) if matching_sale else (parse_money(selected_price.text) or 0),
                "title": title,
                "price": selected_price,
                "section_top": section_top,
                "section_bottom": section_bottom,
            }
        )

    unique = []
    seen = set()
    for product in products:
        key = (product["name"].lower(), product["reference"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(product)
    return unique


def export_product_image(page: fitz.Page, product: dict, index: int) -> str:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    page_width = page.rect.width
    page_height = page.rect.height
    title = product["title"]
    price = product["price"]
    section_top = float(product.get("section_top", 0.0))
    section_bottom = float(product.get("section_bottom", page_height))
    items = text_items_in_section(collect_text(page), section_top, section_bottom)
    x0, x1 = visual_column_bounds(items, page_width, section_bottom - section_top)

    top_margin = 16
    bottom_margin = 16
    focus_start = min(title.bbox[1], price.bbox[1]) - 120
    crop_y0 = max(0.0, section_top + top_margin, focus_start)
    crop_y1 = min(page_height - 72, section_bottom - bottom_margin)
    rect = fitz.Rect(x0, crop_y0, x1, crop_y1)

    if rect.width < page_width * 0.25 or rect.height < 180:
        rect = fitz.Rect(0.0, crop_y0, page_width, crop_y1)

    pix = page.get_pixmap(matrix=fitz.Matrix(3.0, 3.0), clip=rect, alpha=False)
    safe_ref = product["reference"] or f"page-{page.number + 1}-{index}"
    target = IMAGE_DIR / f"{safe_ref.lower()}.png"
    pix.save(target)
    return f"/static/catalog/{target.name}"


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'Catálogo',
            reference TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            cost_price INTEGER NOT NULL DEFAULT 0,
            sale_price INTEGER NOT NULL DEFAULT 0,
            promotion_text TEXT NOT NULL DEFAULT '',
            stock INTEGER NOT NULL DEFAULT 20,
            image_path TEXT NOT NULL DEFAULT '',
            source_page INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            city TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            subtotal INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price INTEGER NOT NULL,
            total_price INTEGER NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
        """
    )


def detect_category(page_text: str, current_category: str) -> str:
    upper = page_text.upper()
    mappings = {
        "ROSTRO": "Rostro",
        "CEJAS": "Cejas",
        "LABIOS": "Labios",
        "OJOS": "Ojos",
        "CORPORAL": "Corporal",
        "ACCESORIOS": "Accesorios",
        "BROCHAS Y ESPONJAS": "Brochas y esponjas",
        "CUIDADO FACIAL": "Cuidado facial",
        "HOMBRES": "Hombres",
        "DISNEY": "Disney",
        "MATTEL": "Mattel",
        "MINI": "Mini",
        "TRENDYLOVERS": "Trendylovers",
    }
    for key, value in mappings.items():
        if key in upper:
            return value
    return current_category


def ensure_catalog_imported() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    ensure_tables(conn)

    if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] > 0:
        conn.close()
        return

    doc = fitz.open(PDF_PATH)
    current_category = "Catálogo"
    for page_number in range(SKIP_PAGES, doc.page_count):
        page = doc.load_page(page_number)
        page_text = clean_text(page.get_text("text"))
        current_category = detect_category(page_text, current_category)
        if "Precio:" not in page_text:
            continue

        for index, product in enumerate(pair_products(page), start=1):
            image_path = export_product_image(page, product, index)
            promotion_text = "Disponible para entrega inmediata" if product["sale_price"] <= 15000 else "Promo especial de temporada"
            conn.execute(
                """
                INSERT INTO products (
                    name, category, reference, description, cost_price, sale_price,
                    promotion_text, stock, image_path, source_page
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product["name"],
                    current_category,
                    product["reference"],
                    product["description"],
                    product["cost_price"],
                    product["sale_price"],
                    promotion_text,
                    20,
                    image_path,
                    page_number + 1,
                ),
            )

    conn.commit()
    conn.close()


def rebuild_catalog() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DATABASE_PATH)
    ensure_tables(conn)
    conn.execute("DELETE FROM products")
    conn.execute("DELETE FROM sqlite_sequence WHERE name = 'products'")
    conn.commit()
    conn.close()

    for image_file in IMAGE_DIR.glob("*.png"):
        image_file.unlink(missing_ok=True)

    ensure_catalog_imported()


if __name__ == "__main__":
    rebuild_catalog()
