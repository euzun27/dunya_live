"""
DUNYATEK document creation — Word (.docx), Excel (.xlsx), PDF and plain text.

Files go to Documents\\DUNYATEK by default (or Desktop / any folder the user names).
An existing file is never overwritten: a number is appended instead. The result
contains the full path, so mail_send can attach it straight away.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from plugins._dunyatek_common import output_dir, user_folder

FONT_DIR = Path(r"C:\Windows\Fonts")

PLUGIN = {
    "name": "document_create",
    "description": (
        "Creates a document file on the PC: Word (docx), Excel (xlsx), PDF or text (txt). "
        "Use for 'Word'de teklif hazırla', 'bunu Excel tablosu yap', 'PDF olarak kaydet', "
        "'rapor oluştur'. YOU write the full content. content: text lines — '# ' starts a "
        "heading, '## ' a sub-heading, '- ' a bullet. table: JSON array of rows, first row "
        "= headers, e.g. [[\"Ürün\",\"Adet\"],[\"Kalem\",5]] (required for xlsx, optional "
        "for docx/pdf). Numbers in tables should be real numbers, not strings. Saved to "
        "Documents\\DUNYATEK unless a folder (desktop/documents/downloads or a path) is "
        "given. Returns the full path; to e-mail it, pass that path to mail_send."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "format": {"type": "STRING", "description": "docx | xlsx | pdf | txt"},
            "filename": {"type": "STRING", "description": "File name without extension, e.g. 'Teklif_ABC_Firmasi'"},
            "title": {"type": "STRING", "description": "Document title (top of the page / sheet name)"},
            "content": {"type": "STRING", "description": "Body text ('# ' heading, '- ' bullet)"},
            "table": {"type": "STRING", "description": "Optional JSON array of rows; first row = headers"},
            "folder": {"type": "STRING", "description": "Optional: desktop | documents | downloads | full path"},
        },
        "required": ["format"],
    },
}


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (name or "").strip()).strip(". ")
    return name[:100] or f"DUNYATEK_{datetime.now():%Y%m%d_%H%M}"


def _target(folder: str, filename: str, ext: str) -> Path:
    base = user_folder(folder) if folder else output_dir()
    if not base.is_absolute():
        base = output_dir()
    base.mkdir(parents=True, exist_ok=True)
    stem = _safe_name(filename)
    path = base / f"{stem}.{ext}"
    n = 2
    while path.exists():
        path = base / f"{stem} ({n}).{ext}"
        n += 1
    return path


def _rows(table: str) -> list[list]:
    if not table:
        return []
    try:
        data = json.loads(table)
    except Exception:
        # Fallback: CSV-ish text, ';' or ',' separated
        sep = ";" if ";" in table else ","
        data = [[c.strip() for c in line.split(sep)] for line in table.strip().splitlines() if line.strip()]
    return [list(r) if isinstance(r, (list, tuple)) else [r] for r in data] if isinstance(data, list) else []


def _lines(content: str) -> list[tuple[str, str]]:
    out = []
    for raw in (content or "").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            out.append(("h2", line[3:]))
        elif line.startswith("# "):
            out.append(("h1", line[2:]))
        elif re.match(r"^\s*[-•*]\s+", line):
            out.append(("li", re.sub(r"^\s*[-•*]\s+", "", line)))
        else:
            out.append(("p", line))
    return out


def _docx(path: Path, title: str, content: str, rows: list[list]) -> None:
    from docx import Document
    doc = Document()
    if title:
        doc.add_heading(title, level=0)
    for kind, text in _lines(content):
        if kind == "h1":
            doc.add_heading(text, level=1)
        elif kind == "h2":
            doc.add_heading(text, level=2)
        elif kind == "li":
            doc.add_paragraph(text, style="List Bullet")
        else:
            doc.add_paragraph(text)
    if rows:
        width = max(len(r) for r in rows)
        t = doc.add_table(rows=len(rows), cols=width)
        t.style = "Table Grid"
        for i, r in enumerate(rows):
            for j in range(width):
                cell = t.cell(i, j)
                cell.text = "" if j >= len(r) or r[j] is None else str(r[j])
                if i == 0:
                    for run in cell.paragraphs[0].runs:
                        run.bold = True
    doc.save(str(path))


def _xlsx(path: Path, title: str, content: str, rows: list[list]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = _safe_name(title)[:31] if title else "Sayfa1"
    if not rows:
        rows = [[line] for _, line in _lines(content)] or [[""]]
    for r in rows:
        ws.append(r)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    for col in ws.columns:
        width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(60, max(10, width + 2))
    ws.freeze_panes = "A2"
    wb.save(str(path))


def _pdf(path: Path, title: str, content: str, rows: list[list]) -> None:
    from fpdf import FPDF
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    regular, bold = FONT_DIR / "arial.ttf", FONT_DIR / "arialbd.ttf"
    if regular.exists():   # a Unicode TTF is needed for ç ğ ı ö ş ü
        pdf.add_font("TR", "", str(regular))
        pdf.add_font("TR", "B", str(bold if bold.exists() else regular))
        family = "TR"
    else:
        family = "Helvetica"
    pdf.add_page()
    if title:
        pdf.set_font(family, "B", 18)
        pdf.multi_cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    for kind, text in _lines(content):
        if kind == "h1":
            pdf.set_font(family, "B", 15); pdf.ln(2); pdf.multi_cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        elif kind == "h2":
            pdf.set_font(family, "B", 13); pdf.ln(1); pdf.multi_cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")
        elif kind == "li":
            pdf.set_font(family, "", 11); pdf.multi_cell(0, 6, "•  " + text, new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.set_font(family, "", 11); pdf.multi_cell(0, 6, text or " ", new_x="LMARGIN", new_y="NEXT")
    if rows:
        pdf.ln(4)
        pdf.set_font(family, "", 10)
        with pdf.table() as table:
            for i, r in enumerate(rows):
                row = table.row()
                for v in r:
                    row.cell("" if v is None else str(v))
    pdf.output(str(path))


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        fmt = (parameters.get("format") or "").strip().lower().lstrip(".")
        fmt = {"word": "docx", "doc": "docx", "excel": "xlsx", "xls": "xlsx", "text": "txt"}.get(fmt, fmt)
        if fmt not in ("docx", "xlsx", "pdf", "txt"):
            return "Desteklenen biçimler: docx (Word), xlsx (Excel), pdf, txt."
        title = (parameters.get("title") or "").strip()
        content = parameters.get("content") or ""
        rows = _rows(parameters.get("table") or "")
        if fmt == "xlsx" and not rows and not content.strip():
            return "Excel için tablo verisi (table) gerekli."
        path = _target(parameters.get("folder") or "", parameters.get("filename") or title, fmt)

        if fmt == "docx":
            _docx(path, title, content, rows)
        elif fmt == "xlsx":
            _xlsx(path, title, content, rows)
        elif fmt == "pdf":
            _pdf(path, title, content, rows)
        else:
            text = (title + "\n\n" if title else "") + content
            if rows:
                text += "\n\n" + "\n".join("\t".join("" if v is None else str(v) for v in r) for r in rows)
            path.write_text(text, encoding="utf-8")

        if player:
            try:
                player.write_log(f"SYS: Belge oluşturuldu → {path}")
            except Exception:
                pass
        return (f"Belge oluşturuldu: {path}\n"
                "Erdal Bey'e dosya adını ve klasörü (Belgeler > DUNYATEK) kısaca söyle. "
                "Mail ile gönderilecekse bu tam yolu mail_send attachments'a ver.")
    except Exception as e:
        return f"Belge oluşturulamadı: {e.__class__.__name__}: {str(e)[:150]}"
