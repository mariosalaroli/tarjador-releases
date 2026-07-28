from pydantic import BaseModel, Field


class ValidationOut(BaseModel):
    pages: int
    size_bytes: int
    chars: int


class EntityGroupOut(BaseModel):
    tipo: str = Field(..., description="Tipo de PII: CPF, CNPJ, E-mail, Telefone, Nome")
    texto: str = Field(..., description="Trecho detectado como PII")
    ocorrencias: int = Field(..., description="Quantas vezes aparece no documento")
    paginas: list[int] = Field(..., description="Páginas (1-indexed) em que aparece")
    score: float = Field(..., description="Confiança da detecção (0.0 a 1.0)")


class ErrorOut(BaseModel):
    detail: str
