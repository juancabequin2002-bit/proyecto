from __future__ import annotations

import os
import sqlite3
from urllib.parse import quote

from flask import Flask, jsonify, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from import_catalog import DATABASE_PATH, ensure_catalog_imported


WHATSAPP_NUMBER = "573214107108"
STORE_PHONE = "3214107108"
STORE_ADDRESS = "Direccion pendiente por confirmar"
STORE_CITY = "Atencion por WhatsApp y entregas a convenir"
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("MALU_SECRET_KEY", "malu-makeup-secret-key")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def seed_admin() -> None:
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL
        )
        """
    )
    existing = conn.execute("SELECT id FROM admins WHERE username = ?", (DEFAULT_ADMIN_USER,)).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
            (DEFAULT_ADMIN_USER, generate_password_hash(DEFAULT_ADMIN_PASSWORD)),
        )
    conn.commit()
    conn.close()


def admin_required() -> tuple[bool, tuple]:
    if not session.get("is_admin"):
        return False, (jsonify({"ok": False, "message": "Debes iniciar sesion como administrador."}), 401)
    return True, ()


def serialize_product(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "reference": row["reference"],
        "description": row["description"],
        "cost_price": row["cost_price"],
        "sale_price": row["sale_price"],
        "promotion_text": row["promotion_text"],
        "stock": row["stock"],
        "image_path": row["image_path"],
        "source_page": row["source_page"],
        "is_active": bool(row["is_active"]),
    }


def fetch_products(active_only: bool = True) -> list[dict]:
    conn = get_db()
    query = """
        SELECT id, name, category, reference, description, cost_price, sale_price,
               promotion_text, stock, image_path, source_page, is_active
        FROM products
    """
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY category, name"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [serialize_product(row) for row in rows]


def contact_context() -> dict:
    return {
        "whatsapp_link": f"https://wa.me/{WHATSAPP_NUMBER}?text=Hola%20Ma%26LuMakeup,%20quiero%20informacion%20sobre%20los%20productos",
        "contact_phone": STORE_PHONE,
        "contact_address": STORE_ADDRESS,
        "contact_city": STORE_CITY,
    }


@app.route("/")
def home():
    products = fetch_products(active_only=True)
    featured = [item for item in products if item["stock"] > 0][:8]
    return render_template(
        "home.html",
        featured=featured,
        stats={"products": len(products), "available": sum(1 for item in products if item["stock"] > 0)},
        **contact_context(),
    )


@app.route("/catalogo")
def catalog():
    return render_template("catalog.html", **contact_context())


@app.route("/admin")
def admin():
    if not session.get("is_admin"):
        return render_template("admin_login.html", default_user=DEFAULT_ADMIN_USER, default_password=DEFAULT_ADMIN_PASSWORD)
    return render_template("admin.html")


@app.get("/api/products")
def api_products():
    include_all = session.get("is_admin") and request.args.get("all") == "1"
    return jsonify({"ok": True, "products": fetch_products(active_only=not include_all)})


@app.post("/api/admin/login")
def admin_login():
    username = request.json.get("username", "").strip()
    password = request.json.get("password", "")
    conn = get_db()
    admin_row = conn.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()
    conn.close()
    if admin_row is None or not check_password_hash(admin_row["password_hash"], password):
        return jsonify({"ok": False, "message": "Usuario o contrasena incorrectos."}), 401
    session["is_admin"] = True
    session["admin_user"] = username
    return jsonify({"ok": True})


@app.post("/api/admin/logout")
def admin_logout():
    session.clear()
    return jsonify({"ok": True})


@app.patch("/api/admin/products/<int:product_id>")
def admin_update_product(product_id: int):
    authorized, error = admin_required()
    if not authorized:
        return error

    payload = request.json or {}
    fields = ["name", "category", "reference", "description", "cost_price", "sale_price", "promotion_text", "stock", "is_active"]
    updates = []
    values = []
    for field in fields:
        if field in payload:
            updates.append(f"{field} = ?")
            values.append(payload[field])

    if not updates:
        return jsonify({"ok": False, "message": "No hay cambios para guardar."}), 400

    values.append(product_id)
    conn = get_db()
    conn.execute(f"UPDATE products SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?", tuple(values))
    conn.commit()
    row = conn.execute(
        """
        SELECT id, name, category, reference, description, cost_price, sale_price,
               promotion_text, stock, image_path, source_page, is_active
        FROM products WHERE id = ?
        """,
        (product_id,),
    ).fetchone()
    conn.close()
    return jsonify({"ok": True, "product": serialize_product(row)})


@app.post("/api/orders")
def create_order():
    payload = request.json or {}
    customer_name = payload.get("customer_name", "").strip()
    phone = payload.get("phone", "").strip()
    city = payload.get("city", "").strip()
    address = payload.get("address", "").strip()
    notes = payload.get("notes", "").strip()
    items = payload.get("items", [])

    if not customer_name or not phone or not address or not items:
        return jsonify({"ok": False, "message": "Completa nombre, telefono, direccion y al menos un producto."}), 400

    notes_for_storage = notes
    if address:
        notes_for_storage = f"Direccion: {address}" if not notes else f"Direccion: {address}\n{notes}"

    conn = None
    try:
        conn = get_db()
        conn.execute("BEGIN")
        selected_items = []
        subtotal = 0

        for item in items:
            product_id = int(item.get("product_id", 0))
            quantity = int(item.get("quantity", 0))
            if quantity <= 0:
                continue

            product = conn.execute(
                "SELECT id, name, sale_price, stock, is_active FROM products WHERE id = ?",
                (product_id,),
            ).fetchone()

            if product is None or not product["is_active"]:
                raise ValueError("Uno de los productos ya no esta disponible.")
            if product["stock"] < quantity:
                raise ValueError(f"No hay suficiente inventario para {product['name']}.")

            total = product["sale_price"] * quantity
            subtotal += total
            selected_items.append((product, quantity, total))

        if not selected_items:
            raise ValueError("No se encontro ningun producto valido en la compra.")

        cursor = conn.execute(
            "INSERT INTO orders (customer_name, phone, city, notes, subtotal) VALUES (?, ?, ?, ?, ?)",
            (customer_name, phone, city, notes_for_storage, subtotal),
        )
        order_id = cursor.lastrowid

        for product, quantity, total in selected_items:
            conn.execute(
                "INSERT INTO order_items (order_id, product_id, quantity, unit_price, total_price) VALUES (?, ?, ?, ?, ?)",
                (order_id, product["id"], quantity, product["sale_price"], total),
            )
            conn.execute(
                "UPDATE products SET stock = stock - ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (quantity, product["id"]),
            )

        conn.commit()
    except ValueError as exc:
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({"ok": False, "message": str(exc)}), 400
    finally:
        if conn:
            conn.close()

    order_lines = [
        "Hola Ma&LuMakeup, quiero registrar este pedido:",
        f"Pedido #{order_id}",
        f"Cliente: {customer_name}",
        f"Telefono: {phone}",
    ]

    if city:
        order_lines.append(f"Ciudad o municipio: {city}")

    order_lines.append(f"Direccion: {address}")

    order_lines.append("")
    order_lines.append("Productos seleccionados:")

    for product, quantity, total in selected_items:
        order_lines.append(f"- {product['name']} x{quantity} = ${total:,.0f}".replace(",", "."))

    order_lines.append("")
    order_lines.append(f"Total del pedido: ${subtotal:,.0f}".replace(",", "."))

    if notes:
        order_lines.append(f"Notas: {notes}")

    order_lines.append("")
    order_lines.append("Quedo atento(a) para confirmar la compra.")

    whatsapp_text = quote("\n".join(order_lines))

    return jsonify(
        {
            "ok": True,
            "order_id": order_id,
            "message": "Pedido guardado correctamente.",
            "whatsapp_url": f"https://wa.me/{WHATSAPP_NUMBER}?text={whatsapp_text}",
        }
    )


def ensure_schema() -> None:
    ensure_catalog_imported()
    seed_admin()


ensure_schema()


if __name__ == "__main__":
    app.run(debug=True)
