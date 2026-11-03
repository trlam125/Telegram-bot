"""PDF, DOCX and text extraction, plus active document context."""
import io
from pathlib import Path
from typing import List, Optional
from docx import Document
from pypdf import PdfReader
from telegram.ext import ContextTypes

def decode_text_file(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1258", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")

def extract_pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    pages: List[str] = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"\n--- Trang {index} ---\n{text.strip()}")
    return "\n".join(pages).strip()

def extract_docx_text(data: bytes) -> str:
    doc = Document(io.BytesIO(data))
    parts: List[str] = []

    for paragraph in doc.paragraphs:
        value = paragraph.text.strip()
        if value:
            parts.append(value)

    for table_index, table in enumerate(doc.tables, start=1):
        rows = []
        for row in table.rows:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            rows.append(" | ".join(cells))
        if rows:
            parts.append(f"\n[Bảng {table_index}]\n" + "\n".join(rows))

    return "\n".join(parts).strip()

def extract_document_text(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return extract_pdf_text(data)
    if ext == ".docx":
        return extract_docx_text(data)
    if ext in {".txt", ".md", ".csv"}:
        return decode_text_file(data)

    raise ValueError(f"Định dạng {ext or 'không xác định'} chưa được hỗ trợ")

def build_document_context(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    name = context.user_data.get("document_name")
    text = context.user_data.get("document_text")
    if not name or not text:
        return None

    return (
        f"Tài liệu người dùng đang làm việc: {name}\n"
        f"--- BẮT ĐẦU TÀI LIỆU ---\n{text}\n--- KẾT THÚC TÀI LIỆU ---"
    )
