"""Entrypoint da API (rotas /api) — ver tarjador/api/routes.py."""
from __future__ import annotations

from fastapi import FastAPI

from .api.routes import router as api_router


app = FastAPI(
    title="Tarjador de PDFs",
    version="0.1.0",
    description="API para tarjar informações pessoais em PDFs (LGPD).",
)

app.include_router(api_router)


@app.get("/health", tags=["infra"])
def health() -> dict[str, str]:
    return {"status": "ok"}
