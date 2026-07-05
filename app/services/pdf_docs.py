from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

from fpdf import FPDF

from db import fetchall, fetchone
from flask_base import fmt_dt, fmt_qty

_ROOT = Path(__file__).resolve().parents[2]

_FONT_CANDIDATES = (
    _ROOT / "static" / "fonts" / "DejaVuSans.ttf",
    _ROOT / "static" / "fonts" / "Arial.ttf",
    Path(r"C:\Windows\Fonts\arial.ttf"),
    Path(r"C:\Windows\Fonts\Arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
)

_STATUS_RU = {
    "draft": "Черновик",
    "posted": "Проведён",
}


def _pdf_font_file() -> Path:
    for p in _FONT_CANDIDATES:
        if p.is_file():
            return p
    raise RuntimeError(
        "Не найден шрифт для PDF. Положите Arial.ttf или DejaVuSans.ttf в static/fonts/"
    )


def _safe_text(v) -> str:
    if v is None:
        return "—"
    s = str(v).strip()
    return s if s else "—"


def _status_ru(status: str | None) -> str:
    return _STATUS_RU.get((status or "").strip(), status or "—")


class _DocPDF(FPDF):
    def __init__(self, title: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self._doc_title = title
        font = _pdf_font_file()
        self.add_font("DocFont", "", str(font))
        self.add_font("DocFont", "B", str(font))
        self.set_auto_page_break(auto=True, margin=18)

    def header(self):
        self.set_font("DocFont", "B", 11)
        self.cell(0, 8, "OrderControl", ln=True, align="L")
        self.set_font("DocFont", "", 9)
        self.set_text_color(90, 90, 90)
        self.cell(0, 5, self._doc_title, ln=True, align="L")
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def footer(self):
        self.set_y(-12)
        self.set_font("DocFont", "", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"Стр. {self.page_no()}/{{nb}}", align="C")
        self.set_text_color(0, 0, 0)


def _meta_block(pdf: _DocPDF, rows: list[tuple[str, str]]) -> None:
    pdf.set_font("DocFont", "", 10)
    col_w = (pdf.w - pdf.l_margin - pdf.r_margin) / 2
    for label, value in rows:
        pdf.set_font("DocFont", "B", 10)
        pdf.cell(32, 6, f"{label}:", ln=0)
        pdf.set_font("DocFont", "", 10)
        pdf.multi_cell(col_w - 32, 6, _safe_text(value))
    pdf.ln(2)


def _items_table(
    pdf: _DocPDF,
    headers: list[str],
    col_widths: list[float],
    rows: list[list[str]],
    aligns: list[str] | None = None,
) -> None:
    if aligns is None:
        aligns = ["L"] * len(headers)
    pdf.set_font("DocFont", "B", 9)
    pdf.set_fill_color(240, 240, 240)
    for i, h in enumerate(headers):
        pdf.cell(col_widths[i], 7, h, border=1, align=aligns[i], fill=True)
    pdf.ln()
    pdf.set_font("DocFont", "", 9)
    if not rows:
        pdf.cell(sum(col_widths), 8, "Нет позиций", border=1, align="C")
        pdf.ln()
        return
    for row in rows:
        for i, cell in enumerate(row):
            txt = _safe_text(cell)
            if len(txt) > 80:
                txt = txt[:77] + "…"
            pdf.cell(col_widths[i], 7, txt, border=1, align=aligns[i])
        pdf.ln()


def _load_stock_in(db, doc_id: int) -> tuple[dict, list[dict]]:
    doc = fetchone(
        db,
        """
        SELECT d.id, d.doc_no, d.status, d.note, d.created_at, d.posted_at,
               u.username AS created_by_name
        FROM stock_in_docs d
        LEFT JOIN users u ON u.id = d.created_by
        WHERE d.id=?
        """,
        (doc_id,),
    )
    if not doc:
        raise LookupError(doc_id)
    items = fetchall(
        db,
        """
        SELECT p.name, p.sku, p.unit, i.qty
        FROM stock_in_items i
        JOIN products p ON p.id = i.product_id
        WHERE i.doc_id=?
        ORDER BY i.id
        """,
        (doc_id,),
    )
    return dict(doc), [dict(r) for r in items]


def _load_stock_out(db, doc_id: int) -> tuple[dict, list[dict]]:
    doc = fetchone(
        db,
        """
        SELECT d.id, d.doc_no, d.status, d.note, d.created_at, d.posted_at,
               u.username AS created_by_name, cl.full_name AS client_name
        FROM stock_out_docs d
        LEFT JOIN users u ON u.id = d.created_by
        LEFT JOIN clients cl ON cl.id = d.client_id
        WHERE d.id=?
        """,
        (doc_id,),
    )
    if not doc:
        raise LookupError(doc_id)
    items = fetchall(
        db,
        """
        SELECT p.name, p.sku, p.unit, i.qty
        FROM stock_out_items i
        JOIN products p ON p.id = i.product_id
        WHERE i.doc_id=?
        ORDER BY i.id
        """,
        (doc_id,),
    )
    return dict(doc), [dict(r) for r in items]


def _load_inventory(db, doc_id: int) -> tuple[dict, list[dict]]:
    doc = fetchone(
        db,
        """
        SELECT d.id, d.doc_no, d.status, d.note, d.created_at, d.posted_at,
               u.username AS created_by_name
        FROM inventory_docs d
        LEFT JOIN users u ON u.id = d.created_by
        WHERE d.id=?
        """,
        (doc_id,),
    )
    if not doc:
        raise LookupError(doc_id)
    items = fetchall(
        db,
        """
        SELECT p.name, p.sku, p.unit, i.qty_system, i.qty_actual,
               (i.qty_actual - i.qty_system) AS qty_diff
        FROM inventory_items i
        JOIN products p ON p.id = i.product_id
        WHERE i.doc_id=?
        ORDER BY p.name
        """,
        (doc_id,),
    )
    return dict(doc), [dict(r) for r in items]


def _pdf_filename(doc_no: str, prefix: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (doc_no or prefix))
    return f"{safe}.pdf"


def build_stock_in_pdf(db, doc_id: int) -> tuple[bytes, str]:
    doc, items = _load_stock_in(db, doc_id)
    pdf = _DocPDF("Документ прихода")
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.set_font("DocFont", "B", 14)
    pdf.cell(0, 10, f"Приход · {_safe_text(doc.get('doc_no'))}", ln=True)
    pdf.ln(1)

    _meta_block(
        pdf,
        [
            ("Статус", _status_ru(doc.get("status"))),
            ("Дата", fmt_dt(doc.get("created_at"))),
            ("Проведён", fmt_dt(doc.get("posted_at")) if doc.get("posted_at") else "—"),
            ("Автор", doc.get("created_by_name")),
            ("Примечание", doc.get("note")),
        ],
    )

    total_qty = sum(float(it["qty"]) for it in items)
    table_rows = [
        [
            str(n),
            _safe_text(it["name"]),
            _safe_text(it.get("sku")),
            fmt_qty(it["qty"]),
            _safe_text(it.get("unit") or "шт"),
        ]
        for n, it in enumerate(items, start=1)
    ]
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    _items_table(
        pdf,
        ["№", "Товар", "Артикул", "Кол-во", "Ед."],
        [10, usable - 10 - 32 - 22 - 16, 32, 22, 16],
        table_rows,
        ["C", "L", "L", "R", "C"],
    )
    pdf.ln(2)
    pdf.set_font("DocFont", "B", 10)
    pdf.cell(0, 7, f"Позиций: {len(items)} · Итого: {fmt_qty(total_qty)}", ln=True)

    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue(), _pdf_filename(doc.get("doc_no"), "IN")


def build_stock_out_pdf(db, doc_id: int) -> tuple[bytes, str]:
    doc, items = _load_stock_out(db, doc_id)
    pdf = _DocPDF("Документ отпуска")
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.set_font("DocFont", "B", 14)
    pdf.cell(0, 10, f"Отпуск · {_safe_text(doc.get('doc_no'))}", ln=True)
    pdf.ln(1)

    _meta_block(
        pdf,
        [
            ("Статус", _status_ru(doc.get("status"))),
            ("Клиент", doc.get("client_name")),
            ("Дата", fmt_dt(doc.get("created_at"))),
            ("Проведён", fmt_dt(doc.get("posted_at")) if doc.get("posted_at") else "—"),
            ("Автор", doc.get("created_by_name")),
            ("Примечание", doc.get("note")),
        ],
    )

    total_qty = sum(float(it["qty"]) for it in items)
    table_rows = [
        [
            str(n),
            _safe_text(it["name"]),
            _safe_text(it.get("sku")),
            fmt_qty(it["qty"]),
            _safe_text(it.get("unit") or "шт"),
        ]
        for n, it in enumerate(items, start=1)
    ]
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    _items_table(
        pdf,
        ["№", "Товар", "Артикул", "Кол-во", "Ед."],
        [10, usable - 10 - 32 - 22 - 16, 32, 22, 16],
        table_rows,
        ["C", "L", "L", "R", "C"],
    )
    pdf.ln(2)
    pdf.set_font("DocFont", "B", 10)
    pdf.cell(0, 7, f"Позиций: {len(items)} · Итого: {fmt_qty(total_qty)}", ln=True)

    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue(), _pdf_filename(doc.get("doc_no"), "OUT")


def build_inventory_pdf(db, doc_id: int) -> tuple[bytes, str]:
    doc, items = _load_inventory(db, doc_id)
    pdf = _DocPDF("Документ инвентаризации")
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.set_font("DocFont", "B", 14)
    pdf.cell(0, 10, f"Инвентаризация · {_safe_text(doc.get('doc_no'))}", ln=True)
    pdf.ln(1)

    diff_count = sum(
        1 for it in items if abs(float(it["qty_actual"]) - float(it["qty_system"])) > 1e-6
    )
    _meta_block(
        pdf,
        [
            ("Статус", _status_ru(doc.get("status"))),
            ("Дата", fmt_dt(doc.get("created_at"))),
            ("Проведён", fmt_dt(doc.get("posted_at")) if doc.get("posted_at") else "—"),
            ("Автор", doc.get("created_by_name")),
            ("Расхождений", str(diff_count)),
            ("Примечание", doc.get("note")),
        ],
    )

    table_rows = []
    for n, it in enumerate(items, start=1):
        diff = float(it["qty_diff"])
        diff_s = fmt_qty(diff)
        if diff > 0:
            diff_s = f"+{diff_s}"
        table_rows.append(
            [
                str(n),
                _safe_text(it["name"]),
                fmt_qty(it["qty_system"]),
                fmt_qty(it["qty_actual"]),
                diff_s,
                _safe_text(it.get("unit") or "шт"),
            ]
        )

    usable = pdf.w - pdf.l_margin - pdf.r_margin
    _items_table(
        pdf,
        ["№", "Товар", "В системе", "Факт", "Разница", "Ед."],
        [10, usable - 10 - 24 - 24 - 24 - 14, 24, 24, 24, 14],
        table_rows,
        ["C", "L", "R", "R", "R", "C"],
    )
    pdf.ln(2)
    pdf.set_font("DocFont", "B", 10)
    pdf.cell(0, 7, f"Позиций: {len(items)} · Расхождений: {diff_count}", ln=True)

    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue(), _pdf_filename(doc.get("doc_no"), "INV")


def build_document_pdf(db, doc_kind: str, doc_id: int) -> tuple[bytes, str]:
    kind = (doc_kind or "").strip().lower()
    if kind in ("stock_in", "in"):
        return build_stock_in_pdf(db, doc_id)
    if kind in ("stock_out", "out"):
        return build_stock_out_pdf(db, doc_id)
    if kind in ("inventory", "inv"):
        return build_inventory_pdf(db, doc_id)
    raise ValueError(doc_kind)
