"""Validação de PDFs antes do processamento.

Nesta versão (PoC) não suportamos OCR: se o PDF não tem texto extraível
(escaneado / apenas imagem), rejeitamos com mensagem clara.
"""
from __future__ import annotations

import fitz  # PyMuPDF


class PDFValidationError(Exception):
    """Erro de validação de PDF (mensagem amigável para o usuário)."""


def validate_pdf(pdf_bytes: bytes, max_size_mb: int = 10) -> dict:
    if not pdf_bytes:
        raise PDFValidationError("Arquivo vazio.")

    size_mb = len(pdf_bytes) / (1024 * 1024)
    if size_mb > max_size_mb:
        raise PDFValidationError(
            f"Arquivo tem {size_mb:.1f} MB e excede o limite de {max_size_mb} MB."
        )

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:  # pragma: no cover
        raise PDFValidationError(f"Não foi possível abrir o PDF: {e}") from e

    try:
        if doc.needs_pass:
            raise PDFValidationError("PDF protegido por senha não é suportado.")
        if doc.page_count == 0:
            raise PDFValidationError("PDF sem páginas.")

        total_text = "".join(page.get_text() for page in doc)
        chars = len(total_text.strip())
        if chars < 20:
            raise PDFValidationError(
                "PDF não tem texto extraível (parece ser escaneado ou apenas imagens). "
                "OCR ainda não é suportado nesta versão — envie um PDF com texto."
            )

        return {
            "pages": doc.page_count,
            "size_bytes": len(pdf_bytes),
            "chars": chars,
        }
    finally:
        doc.close()
