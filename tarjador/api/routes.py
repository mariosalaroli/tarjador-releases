from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from ..config import settings
from ..core.detector import DEFAULT_ENTITIES
from ..core.pipeline import analyze, process_auto
from ..core.validator import PDFValidationError
from .auth import require_api_key
from .schemas import EntityGroupOut, ValidationOut

router = APIRouter(
    prefix="/api",
    tags=["tarjador"],
    dependencies=[Depends(require_api_key)],
)


def _parse_entities(entities_csv: str) -> list[str]:
    tipos = [t.strip() for t in entities_csv.split(",") if t.strip()]
    invalid = [t for t in tipos if t not in DEFAULT_ENTITIES]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Tipos inválidos: {invalid}. "
                f"Válidos: {DEFAULT_ENTITIES}"
            ),
        )
    return tipos or DEFAULT_ENTITIES


def _output_filename(original: str | None) -> str:
    base = (original or "documento.pdf").rsplit("/", 1)[-1]
    if base.lower().endswith(".pdf"):
        base = base[:-4]
    return f"{base}_tarjado.pdf"


@router.post(
    "/validate",
    response_model=ValidationOut,
    summary="Valida se o PDF é adequado (tem texto extraível e está dentro do limite)",
)
async def validate_endpoint(file: UploadFile = File(...)) -> ValidationOut:
    pdf = await file.read()
    try:
        info, _ = analyze(pdf, tipos_display=[], max_size_mb=settings.max_file_size_mb,
                          detectar_selos=False)
    except PDFValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ValidationOut(**info)


@router.post(
    "/detect",
    response_model=list[EntityGroupOut],
    summary="Detecta PII no PDF sem aplicar tarjas (retorna JSON)",
)
async def detect_endpoint(
    file: UploadFile = File(...),
    entities: str = Form(
        ",".join(DEFAULT_ENTITIES),
        description="Lista de tipos separados por vírgula",
    ),
) -> list[EntityGroupOut]:
    pdf = await file.read()
    tipos = _parse_entities(entities)
    try:
        _info, grupos = analyze(pdf, tipos, max_size_mb=settings.max_file_size_mb)
    except PDFValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return [
        EntityGroupOut(
            tipo=g.tipo,
            texto=g.texto,
            ocorrencias=len(g.ocorrencias),
            paginas=[p + 1 for p in g.paginas],
            score=round(g.score, 3),
        )
        for g in grupos
    ]


@router.post(
    "/redact",
    summary="Detecta e tarja PII no PDF, retornando o PDF tarjado (application/pdf)",
    responses={
        200: {"content": {"application/pdf": {}}},
        400: {"description": "PDF inválido ou tipos inválidos"},
        401: {"description": "API key ausente ou inválida"},
    },
)
async def redact_endpoint(
    file: UploadFile = File(...),
    entities: str = Form(
        ",".join(DEFAULT_ENTITIES),
        description="Lista de tipos separados por vírgula",
    ),
) -> Response:
    pdf = await file.read()
    tipos = _parse_entities(entities)
    try:
        redacted, grupos, total = process_auto(
            pdf, tipos, max_size_mb=settings.max_file_size_mb
        )
    except PDFValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    detalhe = ",".join(
        f"{tipo}={sum(len(g.ocorrencias) for g in grupos if g.tipo == tipo)}"
        for tipo in sorted({g.tipo for g in grupos})
    )
    return Response(
        content=redacted,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{_output_filename(file.filename)}"',
            "X-Entities-Total": str(total),
            "X-Entities-Detail": detalhe or "none",
        },
    )
