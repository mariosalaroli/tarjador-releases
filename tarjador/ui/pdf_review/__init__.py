"""Componente customizado de revisão do PDF (frontend em `frontend/`).

Visualizador pdf.js com scroll contínuo, zoom por Ctrl+rolagem e toggle de
tarja por clique — tudo client-side, sem rerun por interação. O contrato de
sincronização com o app (py_ver / seq) está documentado no viewer.js.

Sem toolchain de build: o frontend é JS vanilla servido estático por
`declare_component(path=...)`, com o pdf.js vendorado em `frontend/pdfjs/`.
"""
from pathlib import Path

import streamlit.components.v1 as components

_FRONTEND = str(Path(__file__).parent / "frontend")
_component = components.declare_component("tarjador_pdf_review", path=_FRONTEND)


def pdf_review(*, pdf_url: str, n_pages: int, boxes: dict, marcar: dict,
               py_ver: int, nav: list | None, manual: list | None = None,
               pdf_b64: str = "", height: int = 960, key: str | None = None):
    """Renderiza o visualizador e retorna o último valor enviado por ele:
    `{"seq": int, "marcar": {gid: bool}, "manual": [[pág, x0, y0, x1, y1]]}`
    (ou None antes do 1º envio). `marcar` é o SNAPSHOT COMPLETO do estado do
    componente, não um diff — necessário para idempotência (ver viewer.js e
    `_aplicar_valor_componente` em app.py).

    `boxes`: {página: [[gid, x0, y0, x1, y1, selo, mask], ...]} em pontos PDF
    (origem topo-esquerdo, referencial do PyMuPDF); `mask` é o texto
    substituto de um CPF descaracterizado (a prévia mostra o número mascarado
    em fundo claro, não tarja preta) ou None. `nav`: [gid, tick] para
    scrollar até uma ocorrência (tick cicla entre elas). `py_ver` avisa o
    componente que `marcar` mudou DO LADO PYTHON (tabela/marcar-tudo) — sem
    esse bump, o marcar dos args é tratado como eco e ignorado. `manual`:
    tarjas do rolo já conhecidas — o componente só as adota na montagem
    (restauração pós-F5); em operação normal a lista nasce e vive nele.

    `pdf_b64`: plano B para o binário. Normalmente o componente busca o PDF
    na `pdf_url` (/media/…, servido da RAM) — mas em ambientes que colocam
    autenticação ou CDN entre o navegador e o app (Streamlit Cloud), essa
    requisição volta como página HTML em vez do arquivo. Quando isso
    acontece, o componente devolve `{"need_bytes": true}` e o app reenvia o
    PDF aqui, pela conexão que já está autenticada. Só é preenchido nesse
    caso: onde a URL funciona (HF Space), o binário não trafega nos args.
    """
    return _component(pdf_url=pdf_url, n_pages=n_pages, boxes=boxes,
                      marcar=marcar, py_ver=py_ver, nav=nav,
                      manual=manual or [], pdf_b64=pdf_b64, height=height,
                      key=key, default=None)


def pdf_media_url(pdf_bytes: bytes, nonce: str) -> str:
    """URL /media/… do PDF, servida DA MEMÓRIA pelo media_file_manager do
    Streamlit — o binário não trafega nos args do componente (que são
    re-enviados a cada rerun) nem toca disco (promessa de privacidade do app).

    `nonce` diferencia documentos (entra na coordenada de registro): o
    manager deduplica por hash do conteúdo, então chamar a cada rerun é
    barato e mantém o arquivo vivo (a limpeza remove o que nenhum rerun
    recente registrou). API interna do Streamlit (>=1.41,<1.60 testado aqui);
    se mudar em upgrade, este é o lugar a revisar.
    """
    from streamlit.runtime import get_instance
    from streamlit.runtime.scriptrunner import get_script_run_ctx

    ctx = get_script_run_ctx()
    session_id = ctx.session_id if ctx else "nosession"
    return get_instance().media_file_mgr.add(
        pdf_bytes, "application/pdf", f"{session_id}.pdf_review.{nonce}")
