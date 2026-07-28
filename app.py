"""Interface Streamlit: upload → análise → tabela + PDF interativo sincronizados → aplicar → download."""
# DIAGNÓSTICO (temporário): imprime o stack Python de um segfault nativo nos
# logs. Mantido só neste deploy de reintrodução da feature; remover depois de
# confirmada a estabilidade. O crash original era pyarrow 25.0.0 no data_editor
# (resolvido travando pyarrow no requirements.txt).
import faulthandler
faulthandler.enable()

import os
import re

import fitz  # PyMuPDF
import streamlit as st

from tarjador.ui.pdf_review import pdf_review, pdf_media_url
from tarjador.core.detector import DEFAULT_ENTITIES, BACKEND
from tarjador.core.pipeline import analyze, analyze_seals, apply_redactions
from tarjador.core.redactor import (IMAGE_ENTITY_TIPO, SEAL_ENTITY_TIPO,
                                    cpf_descaracterizado)

# Tipos cuja localização vem de um RETÂNGULO (não de busca por texto): selo de
# assinatura e imagem com cara de assinatura/carimbo. Ambos são desenhados no
# visualizador direto pelo rect da ocorrência.
_TIPOS_POR_RECT = (SEAL_ENTITY_TIPO, IMAGE_ENTITY_TIPO)
from tarjador.core.validator import PDFValidationError

# 200 MB = o teto do próprio `st.file_uploader` (server.maxUploadSize padrão).
# Alinhar os dois evita a situação boba de o Streamlit aceitar o upload e o
# app recusar depois. O limite antigo (50) era conservadorismo do PoC; quem
# precisa de outro valor põe a variável de ambiente (o Tarjador Desktop sobe
# para 500, ver desktop/run_tarjador.py).
MAX_FILE_SIZE_MB = int(os.environ.get("MAX_FILE_SIZE_MB", "200"))

# HF_TOKEN dos Secrets → variável de ambiente, antes de qualquer uso do modelo
# de IA (o `_ner_bert` lê o ambiente no import, que é preguiçoso). Sem token, o
# download do BERT no Hugging Face Hub é rate-limitado e pode ficar pendurado
# — problema real no Streamlit Cloud (o HF Space não sofre disso: roda dentro
# da infra do próprio Hub). O token é só para LER o Hub público; não acopla um
# deploy ao outro. Ausente, o app segue funcionando (ver `garantir_disponivel`
# em _ner_bert: cai para as heurísticas com aviso, em vez de travar).
def _config(nome: str, padrao: str = "") -> str:
    """Configuração do DEPLOY: lê dos Secrets do Streamlit, caindo para a
    variável de ambiente. É assim que um mesmo código se comporta diferente em
    cada ambiente (HF Space x Streamlit Cloud) sem branch nem fork: o que muda
    é config, não código."""
    try:
        if nome in st.secrets:
            return str(st.secrets[nome])
    except Exception:
        pass  # sem secrets.toml (execução local)
    return os.environ.get(nome, padrao)


# Secrets → ambiente, ANTES de qualquer uso do modelo de IA (o `_ner_bert` lê o
# ambiente no import, que é preguiçoso). HF_TOKEN evita o rate-limit de download
# anônimo do Hub. HF_HUB_* ficam disponíveis para isolar problema de rede em
# produção sem novo deploy de código (ver comentário no topo de _ner_bert.py).
for _var in ("HF_TOKEN", "HF_HUB_DISABLE_XET", "HF_HUB_DOWNLOAD_TIMEOUT"):
    _val = _config(_var)
    if _val and not os.environ.get(_var):
        os.environ[_var] = _val



def _plural(n: int, singular: str, plural: str) -> str:
    """`n` + a forma flexionada — evita o "1 ocorrência(s) removida(s)", que
    além de feio obriga o leitor a resolver a concordância de cabeça."""
    return f"{n} {singular if n == 1 else plural}"


def _check(texto: str) -> str:
    """Linha do resumo de conformidade com o ✓ em verde (classe `tj-check`,
    CSS global). Só para o ✓ — "—"/"○" do mesmo resumo mantêm a cor padrão,
    são estado diferente (não tentado / mantido de propósito)."""
    return f"<span class='tj-check'>✓</span> {texto}"


def _gid(g) -> str:
    """Chave estável de um grupo (tipo + texto) para sincronizar tabela e PDF."""
    return f"{g.tipo}\x1f{g.texto}"


@st.cache_data(show_spinner=False, max_entries=2)
def _pdf_b64(pdf_bytes: bytes) -> str:
    """PDF em base64 para mandar nos args do componente — plano B de quando a
    URL /media/… não é servível (ver `pdf_b64` em pdf_review). Cacheado: a
    conversão é reaproveitada entre reruns, e o Streamlit deduplica mensagens
    grandes idênticas, então o binário não retrafega a cada clique."""
    import base64
    return base64.b64encode(pdf_bytes).decode("ascii")


@st.cache_data(show_spinner=False, max_entries=16)
def _search_rects(pdf_bytes: bytes, page_index: int, textos: tuple):
    """Retângulos (search_for) de cada texto na página — cacheado. Não muda com
    o marcar nem com o toggle de domínio, então evita re-buscar no PDF a cada
    rerun (custo do recolorir).

    Retorna `{texto: {"full": [rects], "local": [rects], "pos": [offsets]}}`.
    Para e-mails, `local` isola a parte antes do `@` (mesma lógica do redactor);
    para os demais textos `local` é igual a `full`. `pos` são os offsets de cada
    hit em `page.get_text()` (ordem de leitura, paralela a `full`) — permite a
    _boxes_todas_paginas desenhar só as ocorrências DETECTADAS quando o mesmo
    texto reaparece na página em outro papel (RG vs. protocolo idêntico).
    """
    page = fitz.open(stream=pdf_bytes, filetype="pdf")[page_index]
    page_text = page.get_text()
    out = {}
    for t in textos:
        if not t:
            continue
        full = page.search_for(t)
        full_t = [(r.x0, r.y0, r.x1, r.y1) for r in full]
        # IGNORECASE: parear com search_for, que é caixa-insensível (nomes
        # podem estar "Fulano" no corpo e "FULANO" na assinatura).
        pos = [m.start() for m in re.finditer(re.escape(t), page_text, re.IGNORECASE)]
        if "@" in t:
            local = t.split("@", 1)[0]
            local_t = []
            for fr in full:
                alvos = page.search_for(local, clip=fr + (-1, -1, 1, 1)) if local else []
                if not alvos:
                    alvos = [fr]  # falha segura: cobre o e-mail inteiro
                local_t.extend((r.x0, r.y0, r.x1, r.y1) for r in alvos)
        else:
            local_t = full_t
        out[t] = {"full": full_t, "local": local_t, "pos": pos}
    return out


_BOXES_CACHE_STATE_KEY = "_boxes_cache"


def _boxes_todas_paginas(pdf_bytes, grupos, tarjar_dominio_email,
                         tarjar_nomes_assinatura, descaracterizar_cpf, sig):
    """Geometria de TODAS as caixas clicáveis do documento, por página, para
    o componente de revisão (viewer.js). Substitui o par _single_page_pdf +
    _page_annotations do visualizador antigo: os VISUAIS (prévia preta,
    tracejado salmão, anel teal) agora são overlays HTML desenhados no
    cliente — nenhum clique reescreve PDF no servidor nem re-renderiza canvas.

    Regras de filtragem idênticas às da tarja final (apply_redactions):
    e-mail cobre só a parte local sem o toggle de domínio; RG e nome misto
    restringem às ocorrências detectadas (pareamento por offset); nome com
    "tarjar assinaturas" desligado preserva as ocorrências de assinatura;
    selos (imagem) usam o rect da ocorrência, não search_for.

    Retorna `(boxes, gid_box_pages)`:
    - boxes: {página: [[gid, x0, y0, x1, y1, selo, mask], ...]} em pontos PDF
      (origem topo-esquerdo — o viewer posiciona em % da página). `mask` é o
      texto substituto do CPF descaracterizado (`***.456.789-**`) — o viewer
      mostra a prévia como texto mascarado em fundo claro, não tarja preta —
      ou None nos demais casos.
    - gid_box_pages: {gid: [página de cada caixa]}, na ordem (página, y, x) —
      a MESMA em que o viewer.js monta os overlays, para o ciclo de navegação
      (nav_occ % n) apontar pra mesma caixa nos dois lados.

    Cache MANUAL via session_state (não `st.cache_data`): esse decorator
    precisaria hashear `grupos` inteiro — uma lista de dataclasses aninhadas
    (uma por entidade, cada uma com sua lista de ocorrências) — A CADA rerun
    do fragment, ou seja, a cada clique. Em documentos grandes (centenas de
    entidades/ocorrências, ex. relatórios fundiários com 250+ imóveis) esse
    hash chega a custar mais que o cálculo em si, e num servidor fraco
    (Streamlit Cloud free tier) trava a UI por vários segundos a cada
    interação. `sig` é uma tupla barata (a mesma cache_key da análise, mais
    tarjar_dominio_email) que identifica o resultado sem tocar em `grupos` —
    comparação O(1), sem percorrer a estrutura."""
    cache = st.session_state.get(_BOXES_CACHE_STATE_KEY)
    if cache is not None and cache[0] == sig:
        return cache[1]
    boxes = {}
    pad = 2.0
    for pag in sorted({p for g in grupos for p in g.paginas}):
        on_page = [g for g in grupos if pag in g.paginas and g.texto]
        # Selos e imagens não têm texto pesquisável: não entram no search_for.
        textos = tuple(dict.fromkeys(
            g.texto for g in on_page if g.tipo not in _TIPOS_POR_RECT))
        rects_por_texto = _search_rects(pdf_bytes, pag, textos)  # cacheado
        linhas = []

        def _add(gid, box, selo=False, mask=None):
            x0, y0, x1, y1 = box
            linhas.append([gid, x0 - pad, y0 - pad, x1 + pad, y1 + pad, selo,
                           mask])

        for g in on_page:
            gid = _gid(g)
            occs_page = [o for o in g.ocorrencias if o.pagina == pag]
            if g.tipo in _TIPOS_POR_RECT:
                for o in occs_page:
                    if o.rect:
                        # `selo=True` no viewer.js = preenchimento preto OPACO
                        # (não translúcido): a caixa precisa COBRIR a imagem por
                        # baixo, senão o selo/assinatura continuaria visível na
                        # prévia mesmo estando marcado para remoção.
                        _add(gid, o.rect, selo=True)
                continue
            info_t = rects_por_texto.get(g.texto)
            if not info_t:
                continue
            # Nome misto (corpo + assinatura) com a flag desligada: as
            # ocorrências de assinatura não ganham caixa nem tarja (espelha
            # pipeline.apply_redactions).
            preservar_assinatura = (
                g.tipo == "Nome" and not tarjar_nomes_assinatura
                and not g.em_assinatura
                and any(o.em_assinatura for o in g.ocorrencias)
            )
            if preservar_assinatura:
                occs_page = [o for o in occs_page if not o.em_assinatura]
                if not occs_page:
                    continue  # nesta página o nome só está em assinatura
            # E-mail sem "tarjar domínio": desenha só a parte antes do @.
            if "@" in g.texto and not tarjar_dominio_email:
                desenhar = info_t["local"]
            else:
                desenhar = info_t["full"]
                # Tarja por posição (RG e Nome misto): restringe às ocorrências
                # DETECTADAS — a mesma string pode ser outra entidade na página
                # (RG vs. protocolo idêntico) ou estar em assinatura preservada.
                if g.tipo == "RG" or preservar_assinatura:
                    pos = info_t["pos"]
                    if pos and len(pos) == len(desenhar):
                        inicios = {o.inicio for o in occs_page}
                        keep = [r for p, r in zip(pos, desenhar) if p in inicios]
                        if keep:
                            desenhar = keep
            mask = (cpf_descaracterizado(g.texto)
                    if descaracterizar_cpf and g.tipo == "CPF" else None)
            for box in desenhar:
                _add(gid, box, mask=mask)
        if linhas:
            boxes[pag] = linhas
    gid_box_pages = {}
    for pag in sorted(boxes):
        for b in sorted(boxes[pag], key=lambda b: (b[2], b[1])):  # (y0, x0)
            gid_box_pages.setdefault(b[0], []).append(pag)
    resultado = (boxes, gid_box_pages)
    st.session_state[_BOXES_CACHE_STATE_KEY] = (sig, resultado)
    return resultado


def _kpis_html(n_tot, n_marc, n_pags, n_pags_ocorr):
    """Quatro cartões de resumo (mini-dashboard) no topo do painel."""
    cards = ""
    for rot, val, cor in [("🔎 encontradas", n_tot, "#6366f1"),
                          ("⬛ tarjadas", n_marc, "#e11d48"),
                          ("📄 páginas", n_pags, "#0ea5e9"),
                          ("📑 com ocorrência", n_pags_ocorr, "#f59e0b")]:
        cards += (
            f'<div style="flex:1;background:rgba(127,127,127,.08);border:1px solid '
            f'rgba(127,127,127,.2);border-left:3px solid {cor};border-radius:8px;'
            f'padding:6px 10px;"><div style="font-size:20px;font-weight:700;">{val}</div>'
            f'<div style="font-size:10px;opacity:.7;text-transform:uppercase;">{rot}</div></div>'
        )
    st.markdown(f'<div style="display:flex;gap:6px;margin-bottom:8px;">{cards}</div>',
                unsafe_allow_html=True)


def _nav_row_click(gid, n_boxes):
    """Clique no texto de uma linha: foca a entidade; clicar de novo na MESMA
    linha cicla entre as ocorrências (caixas) dela no documento. O scroll até
    a caixa é do componente do PDF, que recebe [gid, nav_occ] nos args e usa
    a mesma ordem de caixas (página, y, x) — ver _boxes_todas_paginas.
    `st.button` por linha (não `st.pills`): é o widget mais barato do
    Streamlit; com ~300 ocorrências, isso evita a lentidão da versão antiga."""
    if st.session_state.get("nav_focus") == gid:
        st.session_state.nav_occ = (
            st.session_state.get("nav_occ", 0) + 1) % max(n_boxes, 1)
    else:
        st.session_state.nav_focus = gid
        st.session_state.nav_occ = 0


def _sync_marcar_dos_checkboxes():
    """Traz para `marcar` o estado atual dos checkboxes da tabela ANTES de
    calcular o KPI e renderizar o PDF — assim contador e documento ficam em
    sincronia no mesmo rerun. O widget-state já reflete o clique do usuário no
    início do rerun; aqui só copiamos para `marcar`.

    Mudança detectada aqui nasceu NA TABELA → bumpa py_ver, o sinal para o
    componente do PDF adotar o `marcar` dos args (sem o bump ele trataria o
    valor como eco do próprio envio e ignoraria — ver viewer.js)."""
    marcar = st.session_state.marcar
    ev = st.session_state.ed_ver
    mudou = False
    for gid in list(marcar):
        k = f"chk_{gid}_{ev}"
        if k in st.session_state and marcar[gid] != bool(st.session_state[k]):
            marcar[gid] = bool(st.session_state[k])
            mudou = True
    if mudou:
        st.session_state.py_ver += 1


def _aplicar_valor_componente():
    """Aplica em `marcar` os toggles feitos POR CLIQUE NO DOCUMENTO (valor do
    componente, disponível no session_state no início do rerun). Dedup por
    `seq`. NÃO bumpa py_ver (a mudança já está no componente; devolvê-la
    seria eco). Bumpa ed_ver para re-keyar os checkboxes da tabela, que
    reinicializam do `marcar` novo.

    O componente manda o SNAPSHOT COMPLETO de `marcar` a cada envio, não um
    diff — com documentos grandes (~300 entidades), o rerun do fragment é
    caro o bastante para um lote de cliques seguinte disparar antes do
    Python terminar de processar o anterior; o Streamlit descarta/supera o
    rerun em andamento, e um diff (só os gids que mudaram NAQUELE envio)
    some para sempre — o clique parecia ter "pegado" na tela, mas o servidor
    nunca soube dele, e ele reaparecia desmarcado assim que qualquer outra
    coisa bumpasse py_ver. Snapshot completo é idempotente: se um envio se
    perde, o próximo já carrega tudo acumulado, nada fica órfão."""
    val = st.session_state.get("pdfrev")
    if not isinstance(val, dict):
        return
    seq = val.get("seq")
    if not seq or seq == st.session_state.get("_comp_seq"):
        return
    st.session_state._comp_seq = seq
    # Tarjas manuais (rolo): o componente é o dono da lista — adota inteira.
    st.session_state.manual_rects = [
        tuple(r) for r in (val.get("manual") or []) if len(r) == 5]
    marcar = st.session_state.marcar
    mudou = False
    for gid, v in (val.get("marcar") or {}).items():
        if gid in marcar and marcar[gid] != bool(v):
            marcar[gid] = bool(v)
            mudou = True
    if mudou:
        st.session_state.ed_ver += 1


def _marcar_todos(gids, key):
    """Checkbox no cabeçalho da coluna Tarjar: marca/desmarca todas as linhas
    visíveis (respeita o filtro de tipo). Bumpar ed_ver re-keia os checkboxes
    das linhas, que reinicializam de `marcar` — mesmo mecanismo da
    sincronização PDF → tabela, então o custo é o de um rerun de fragment
    que já acontece a cada clique."""
    v = bool(st.session_state[key])
    marcar = st.session_state.marcar
    for gid in gids:
        marcar[gid] = v
    st.session_state.ed_ver += 1
    st.session_state.py_ver += 1  # mudança nascida no Python → componente adota


def _on_ordem_change():
    """Máquina de estados da pílula de ordenamento (`sort_mode`): "occ"
    (ordem de ocorrência, padrão), "az" (A-Z) e "za" (Z-A). O pill alfabético
    é um só, cujo rótulo troca A-Z↔Z-A conforme o modo — então reclicá-lo
    alterna o sentido em vez de desmarcar.

    A leitura vem do valor do próprio widget (`ordem_lista`): ao reclicar o
    pill já selecionado, `st.pills` (seleção única) devolve None — é esse None
    que interpretamos como "reclique". Como o rótulo clicado se perde no
    deselect, desambiguamos pelo modo atual: em "az" o reclique vira "za" e
    vice-versa; em "occ" o reclique em Ocorrência não muda nada."""
    val = st.session_state.get("ordem_lista")
    mode = st.session_state.get("sort_mode", "occ")
    if val == "Ocorrência":
        mode = "occ"
    elif val in ("A-Z", "Z-A"):
        mode = "az"
    elif val is None:  # reclique no pill já selecionado
        if mode == "az":
            mode = "za"
        elif mode == "za":
            mode = "az"
    st.session_state.sort_mode = mode


# Abreviações na coluna "Tipo" da tabela (coluna estreita).
_TIPO_ABREV = {"Telefone": "Tel", "Celular": "Cel"}


def _tabela_unificada(alvo, gid_box_pages):
    """Seleção (checkbox) + Navegação (texto clicável) numa lista só, com
    ordenação por ocorrência ou alfabética. `gid_box_pages` (de
    _boxes_todas_paginas) dá a página de cada caixa da entidade, na ordem do
    ciclo de navegação — realça na coluna Páginas a página da ocorrência
    focada.

    Checkbox: um `st.checkbox` por entidade em vez do data_editor. O
    data_editor guarda edições como delta sobre a base e, em rerun com
    latência (Cloud), reverte a célula ("desmarca sozinho"). Um st.checkbox tem
    estado próprio e autoritativo — não reverte. A key inclui ed_ver: quando o
    clique numa caixa do PDF muda `marcar`, ed_ver muda, os checkboxes ganham
    key nova e re-inicializam de `marcar` (sincroniza PDF → tabela).

    Navegação: `st.button` (não `st.pills`) por linha — ver _nav_row_click.
    Buttons são o widget mais barato do Streamlit; com ~300 ocorrências num
    documento real, isso é o que evita o mesmo problema de lentidão que a
    versão anterior (pílula por linha) teve.

    `alvo` é a lista já filtrada pelo tipo escolhido — pode esconder linhas
    já marcadas de outros tipos; o contador no rodapé avisa quando isso
    acontece."""
    marcar = st.session_state.marcar
    ev = st.session_state.ed_ver
    nav_focus = st.session_state.get("nav_focus")
    nav_occ = st.session_state.get("nav_occ", 0)

    c_tit, c_ord = st.columns([3, 2], vertical_alignment="center")
    c_tit.markdown(
        "##### Navegação e seleção",
        help="Marque/desmarque para tarjar. O checkbox no topo da coluna Tarjar "
             "marca/desmarca todas as linhas visíveis de uma vez. Clique no texto "
             "encontrado para ir até a página onde a informação aparece — clique de "
             "novo na mesma linha para pular para a próxima ocorrência, se houver "
             "mais de uma. Fica sincronizado com os cliques no documento e com o "
             "contador.",
    )
    with c_ord:
        mode = st.session_state.get("sort_mode", "occ")
        # O pill alfabético troca de rótulo conforme o sentido; sincroniza o
        # valor persistido do widget com o rótulo atual, senão o estado antigo
        # ("A-Z") ficaria fora da lista de opções ao passar para "za" ("Z-A").
        sel = {"occ": "Ocorrência", "az": "A-Z", "za": "Z-A"}[mode]
        alpha = "Z-A" if mode == "za" else "A-Z"
        if st.session_state.get("ordem_lista") != sel:
            st.session_state["ordem_lista"] = sel
        st.pills("Ordem", ["Ocorrência", alpha],
                 selection_mode="single", label_visibility="collapsed",
                 key="ordem_lista", on_change=_on_ordem_change)
    if mode == "az":
        alvo = sorted(alvo, key=lambda g: g.texto.lower())
    elif mode == "za":
        alvo = sorted(alvo, key=lambda g: g.texto.lower(), reverse=True)
    else:
        # Ordem em que a entidade aparece no PDF (página, posição no texto);
        # quando há várias ocorrências, vale a 1ª.
        alvo = sorted(alvo, key=lambda g: min((o.pagina, o.inicio) for o in g.ocorrencias))

    # 1ª coluna larga o bastante para caber caixa + rótulo "Tarjar" numa linha
    # só (o cabeçalho agora é um checkbox rotulado, não um título centralizado).
    ratios = [1.7, 1.6, 5.0, 1.4]
    # Altura fixa (com rolagem) só entra quando o conteúdo real chegaria a
    # ocupar uma fatia grande do painel do PDF ao lado — daí o limiar alto (11
    # linhas): com poucas linhas (ex.: 5 grupos), o container encolhe para o
    # conteúdo (altura "content", padrão do Streamlit). Quando passa do
    # limiar, a altura CRESCE com a quantidade real de linhas até o teto.
    # O teto de 960 (mesma altura do iframe do PDF) é só o valor server-side:
    # como a tabela começa ABAIXO dos KPIs/filtro/título, 960 faria o fundo
    # dela passar do fundo do iframe. O ajuste fino é client-side —
    # clampTabela() em viewer.js mede "fundo do iframe − topo da tabela" e
    # reescreve o height inline do container fixo (âncora
    # `st-key-tabela_selecao`), só encolhendo, nunca crescendo além deste
    # teto. Sem JS (âncora não achada), vale o teto de 960.
    _TABELA_LIMIAR_ITENS = 11
    _TABELA_ALTURA_LINHA = 46  # estimativa: checkbox + gap padrão do container
    _TABELA_ALTURA_MAX = 960
    tabela_kwargs = {"border": False, "key": "tabela_selecao"}
    if len(alvo) > _TABELA_LIMIAR_ITENS:
        linhas = len(alvo) + 1  # +1 pelo cabeçalho (Tarjar/Tipo/Texto/Páginas)
        tabela_kwargs["height"] = min(_TABELA_ALTURA_MAX, linhas * _TABELA_ALTURA_LINHA + 16)
    with st.container(**tabela_kwargs):
        h = st.columns(ratios, vertical_alignment="center")
        # Cabeçalho "Tarjar" é um checkbox de marcar/desmarcar tudo (só as
        # linhas visíveis no filtro). `marcar` já foi sincronizado com os
        # checkboxes das linhas no início do fragment, então o agregado está
        # fresco neste rerun. A key inclui ev E o agregado: quando uma linha
        # muda o agregado (ou o ed_ver bumpa), a key muda e o checkbox
        # reinicializa de `value` — sem isso, ficaria preso no estado antigo.
        todos_on = all(marcar.get(_gid(g), True) for g in alvo)
        k_all = f"chk_all_{ev}_{int(todos_on)}"
        _TITULO = ("<div style='text-align:center;font-size:.85rem;"
                   "font-weight:600;opacity:.6'>{}</div>")
        with h[0]:
            # A palavra "Tarjar" NÃO é o rótulo do checkbox: é um markdown igual
            # ao das outras colunas. Como rótulo, ela herdava as métricas do
            # widget (line-height/padding próprios) e descia alguns pixels em
            # relação aos demais títulos. Sendo o mesmo elemento que eles, o
            # alinhamento passa a ser automático — o container `cabec_tarjar`
            # (CSS no topo) só coloca a caixa e o título lado a lado, centrados.
            with st.container(key="cabec_tarjar"):
                st.checkbox("Marcar todos", value=todos_on, key=k_all,
                            on_change=_marcar_todos, label_visibility="collapsed",
                            args=([_gid(g) for g in alvo], k_all))
                st.markdown(_TITULO.format("Tarjar"), unsafe_allow_html=True)
        for col, titulo in zip(h[1:], ["Tipo", "Texto encontrado", "Páginas"]):
            # st.caption não centraliza; replica o estilo (menor/apagado) em HTML.
            col.markdown(_TITULO.format(titulo), unsafe_allow_html=True)
        for g in alvo:
            gid = _gid(g)
            tipo = _TIPO_ABREV.get(g.tipo, g.tipo)
            bp = gid_box_pages.get(gid) or ()
            pag_ativa = bp[nav_occ % len(bp)] if gid == nav_focus and bp else None
            pgs = ", ".join(
                f"<span style='color:#14b8a6;font-weight:600'>{p + 1}</span>"
                if p == pag_ativa else str(p + 1)
                for p in g.paginas
            )
            c = st.columns(ratios, vertical_alignment="center")
            marcar[gid] = c[0].checkbox(
                "tarjar", value=marcar.get(gid, True),
                key=f"chk_{gid}_{ev}", label_visibility="collapsed",
            )
            c[1].markdown(f"<span style='font-size:.85rem'>{tipo}</span>",
                          unsafe_allow_html=True)
            with c[2]:
                # Nome misto (corpo + assinatura) com a flag desligada: avisa
                # que a assinatura ficará visível — sem isso, a caixa ausente
                # na assinatura pareceria falha de detecção.
                ajuda = None
                if (g.tipo == "Nome" and not tarjar_assinaturas
                        and not g.em_assinatura
                        and any(o.em_assinatura for o in g.ocorrencias)):
                    ajuda = ("✍️ Aparece também em assinatura. Com \"Tarjar "
                             "nomes em assinaturas\" desligado, só as "
                             "ocorrências no corpo do texto serão tarjadas.")
                if st.button(g.texto, key=f"navrow_{gid}", use_container_width=True,
                            help=ajuda,
                            type="tertiary" if gid != nav_focus else "secondary"):
                    _nav_row_click(gid, len(gid_box_pages.get(gid) or ()))
                    st.rerun(scope="fragment")
            c[3].markdown(f"<span style='font-size:.85rem'>{pgs}</span>",
                          unsafe_allow_html=True)


st.set_page_config(page_title="Tarjador.ia", page_icon="🔒", layout="wide")

st.title("🔒 Tarjador.ia")
st.markdown(
    "Envie um PDF, revise as informações pessoais encontradas e baixe a versão sanitizada e "
    "tarjada, em conformidade com a LGPD e a LAI."
)

# As cores de destaque (pílulas, tags do sidebar, botões) vêm do tema
# (primaryColor = teal em .streamlit/config.toml) — mecanismo oficial e
# confiável, sem CSS mirando estrutura interna do Streamlit.

# Moldura da página do PDF: borda + cantos arredondados no iframe do
# componente (o container em si, visto de fora — CSS interno ao iframe não
# alcançaria o pdf.js lá dentro).
st.markdown(
    """
    <style>
    [class*="st-key-pdfframe"]{ border-radius:10px; overflow:hidden;
        border:4px solid #464646; }
    [class*="st-key-pdfframe"] iframe{ display:block; border:0; }
    /* Checkbox "Tarjar" (marcar tudo) no cabeçalho da tabela: rótulo com o
       mesmo estilo dos demais títulos de coluna (menor/apagado) e sempre em
       uma linha só — sem o nowrap, a coluna estreita quebra "Tarjar" no meio. */
    /* Cabeçalho da coluna Tarjar: caixa de "marcar todos" + o título, lado a
       lado e centrados. O título é markdown (não o rótulo do checkbox), para
       ficar idêntico aos títulos das outras colunas — ver _tabela_unificada.
       O bloco vertical padrão do Streamlit empilharia os dois e esticaria cada
       filho na linha inteira; daí o flex-row e o flex:0 0 auto (mesmo truque
       do pagebox da paginação antiga). */
    /* Alinhado à ESQUERDA (não centrado): os checkboxes das linhas ficam no
       início da coluna, então centrar o cabeçalho jogaria a caixa de "marcar
       todos" para a direita, fora da coluna vertical que ela comanda. O título
       acompanha a caixa e fica um pouco mais à esquerda — é o preço de a caixa
       estar na prumada certa, que é o que importa. */
    [class*="st-key-cabec_tarjar"]{
        display:flex !important; flex-direction:row !important;
        align-items:center !important; justify-content:flex-start !important;
        width:100% !important; gap:.3rem;
    }
    [class*="st-key-cabec_tarjar"] > div{
        flex:0 0 auto !important; width:auto !important;
    }
    /* O checkbox tem espaçamento próprio que desalinharia a dupla. */
    [class*="st-key-cabec_tarjar"] [data-testid="stCheckbox"]{
        margin:0 !important; padding:0 !important;
    }
    [class*="st-key-cabec_tarjar"] p{ white-space:nowrap; }
    /* ✓ do resumo de conformidade (seção 3): verde em vez do texto padrão —
       sinaliza "checagem feita" (achando algo ou não) nas 5 linhas do
       relatório. Mesmo verde do banner "PDF válido" (tj-valido), com o
       mesmo ajuste para tema escuro. */
    .tj-check{ color:#1a7f37; font-weight:600; }
    @media (prefers-color-scheme:dark){ .tj-check{ color:#3dd68c; } }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Modal "Mais informações"
# ---------------------------------------------------------------------------
@st.dialog("Como o Tarjador funciona", width="large")
def _mostrar_mais_informacoes():
    st.markdown(
        """
        ##### Detecção de dados pessoais

        - **CPF / CNPJ** — regex com validação dos 2 dígitos verificadores.
          O CPF pode ser **descaracterizado** (padrão): os 3 primeiros
          algarismos e os 2 verificadores viram asteriscos
          (`***.123.456-**`) e o número original é removido do arquivo —
          ou **tarjado** por inteiro, conforme a opção na barra lateral
        - **Celular / Telefone** — reconhece o número em qualquer máscara
          (com ou sem `+55`, parênteses, traço, espaço, tudo junto) e valida
          de verdade com `phonenumbers` (libphonenumber, a biblioteca de
          telefone do Google — a mesma por trás de apps como o WhatsApp),
          não com uma regex solta. Ela confere se o **DDD existe de fato**
          (o Brasil tem uma lista fechada de DDDs válidos, não é qualquer
          par de dígitos) e se o **formato bate com o plano de numeração**
          brasileiro (celular tem o 9º dígito e 9 números após o DDD; fixo
          tem 8 e começa entre 2 e 5) — e é esse tipo real do número, não
          "começa com 9", que separa **Celular** de **Telefone**. Como
          efeito colateral, isso descarta protocolo/SEI, CPF e CNPJ
          formatados, que têm o mesmo tanto de dígitos mas não validam como
          telefone. Importante: é validação de formato/plano de numeração,
          não confirma se o número está ativo ou pertence a alguém — o
          Tarjador não tem acesso a isso
        - **E-mail** — regex de formato; a tarja cobre só a parte antes do
          `@` (o domínio não é dado pessoal e permanece visível). E-mails que
          parecem de pessoa (ex.: `joao.silva@gmail.com`) já vêm marcados; os
          **funcionais** são listados, mas vêm **desmarcados** para você
          decidir (a opção "Tarjar e-mails funcionais" inverte esse padrão).
          Contam como funcionais: e-mails em domínio de ente público —
          `gov.br`, `leg.br`, `jus.br`, `mp.br`, `def.br`, `mil.br`, `tc.br`,
          categorias que o Registro.br só concede a órgãos públicos — mesmo
          quando a parte local é o nome do servidor
          (`joao.silva@orgao.gov.br`); caixas institucionais em qualquer
          domínio (`gabinete@`, `secretaria@`, siglas); e instituições
          conhecidas (Banco do Brasil, Banco Mundial, BID, FMI)
        - **Nomes** — por padrão, IA (BERT treinado em textos jurídicos, LeNER-Br)
          que reconhece pessoas no corpo do texto sem confundir com leis ou órgãos.
          Se a IA for desligada, entram no lugar heurísticas de texto: proximidade
          a CPF, blocos de assinatura e formatos de assinatura eletrônica (SEI,
          gov.br, 1Doc e similares) — também disponíveis para rodar em conjunto
          com a IA, em "Avançado"
        - **Selos de assinatura digital** (gov.br/ICP-Brasil e similares) — quando
          são imagem (não texto), detecção por OCR (Tesseract). Aparecem como item
          na tabela de revisão, junto com as demais entidades
        - **Imagens** — demais imagens do documento (assinatura manuscrita
          digitalizada, carimbo, foto, brasão/logo), uma a uma na tabela de
          revisão ("Imagem 1", "Imagem 2"...). Mesmo OCR dos selos, então
          liga junto sem custo extra; a heurística olha só posição/tamanho,
          não o conteúdo — pode listar algo que não é dado pessoal, revise
          antes de aplicar
        """
    )
    st.divider()
    st.markdown(
        """
        ##### Segurança e privacidade

        - **Tarja real** — o conteúdo é removido do stream do PDF
          (`apply_redactions` do PyMuPDF), não é um retângulo por cima; não dá
          para recuperar via copiar/colar ou extração de texto
        - **Metadados limpos** — título, autor, ferramenta usada e datas de
          criação/edição são apagados de todo PDF gerado (Info dict e XMP)
        - **Anexos e arquivos embutidos removidos** — PDF pode carregar outros
          arquivos dentro dele (anexos, embedded files); o documento tarjado
          sai sem nenhum
        - **JavaScript removido** — scripts embutidos no PDF (ações de
          abertura, formulários dinâmicos) são eliminados
        - **Texto invisível removido** — texto oculto no arquivo (invisível na
          tela, mas presente no conteúdo) é apagado. Exceção: em documento
          escaneado com OCR, essa camada invisível é o próprio texto
          pesquisável e é preservada para o PDF continuar selecionável
        - **Formulários e miniaturas limpos** — valores preenchidos em campos
          de formulário são zerados e miniaturas de página (que guardam uma
          imagem antiga do conteúdo) são descartadas
        - **Arquivo compactado e saneado** — objetos órfãos e versões
          anteriores do conteúdo não vão para o arquivo final (`garbage
          collection` + `clean` do PyMuPDF)
        - **Processamento local** — nada é enviado para serviços de terceiros;
          a análise roda inteiramente no servidor que hospeda esta aplicação
        - **Nada gravado em disco** — o PDF trafega e é processado em memória
          (bytes em RAM/`BytesIO`, sem arquivo temporário no servidor);
          termina a sessão (fecha a aba ou o processo reinicia), o conteúdo
          é descartado
        """
    )
    st.divider()
    st.caption(f"Motor de detecção: {BACKEND}")
    # Aviso de licença e créditos. Não é enfeite: a CC BY-SA do modelo de
    # português do spaCy EXIGE atribuição visível, e a AGPL exige que o
    # usuário de um serviço em rede saiba como obter o código-fonte
    # (cláusula 13 — é o caso dos deploys web).
    st.caption(
        "Software livre sob "
        "[AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html) · "
        "Copyright © 2026 Mario Salaroli · "
        "[Código-fonte](https://github.com/mariosalaroli/tarjador-releases)"
    )
    st.caption(
        "Construído sobre PyMuPDF (AGPL), Streamlit, spaCy e Presidio. "
        "Modelo de português `pt_core_news_sm` por Explosion AI "
        "(CC BY-SA 4.0); NER jurídico BERTimbau/LeNER-Br por Pierre Guillou; "
        "OCR por Tesseract (Apache 2.0); visualizador por pdf.js "
        "(Mozilla, Apache 2.0)."
    )


# ---------------------------------------------------------------------------
# Toggles de Selos/Imagens como FRAGMENT: são os únicos, dentre os da sidebar,
# que disparam um passo CARO (OCR página a página) — os demais só reanalisam
# regex/IA, rápido o bastante para o rerun completo (inevitável para widget
# fora de fragment) passar despercebido. Sem isto, ligar o toggle refazia o
# script inteiro DURANTE o OCR: a sidebar e o restante da página ficavam sob
# o mesmo rerun "cru" do OCR, por segundos, em vez de só a barra de progresso
# mexer. Com o toggle DENTRO do fragment, um clique nele faz um rerun
# ESCOPADO (só esta função) — o OCR roda e a barra progride sem tocar o
# resto do app.
#
# Só funciona se este fragment tiver `pdf_bytes` em mãos SEM precisar
# reexecutar o `st.file_uploader` (que fica mais abaixo no script, fora do
# fragment) — por isso lê de `st.session_state["pdf_bytes"]`, escrito logo
# após o upload (ver abaixo). Na primeira análise de um arquivo NOVO
# (session_state ainda não tem os bytes deste arquivo neste ponto do rerun),
# a função não tenta OCR — o bloco de "resgate" mais abaixo (mesmo código,
# fora do fragment) cobre esse caso; um upload novo já dispara um rerun
# completo por conta da reanálise do texto, então não há nada a proteger aí.
#
# Como o resto do app (montagem de `grupos`, `_secao_revisao`) fica FORA
# deste fragment, ele não vê a mudança de um rerun escopado sozinho — por
# isso, ao concluir (ou ao só virar o toggle sem precisar de OCR novo), força
# UM rerun completo (`scope="app"`) só quando o resultado realmente mudou.
# Esse rerun final é rápido (o OCR já ficou pronto, cacheado) — o "refresh"
# visível vira um piscar rápido no fim, não os vários segundos do OCR.
@st.fragment
def _secao_selos():
    detectar_selos = st.toggle(
        "Selos de assinatura digital",
        value=False,
        help="Encontra e cobre os carimbos de assinatura eletrônica (gov.br / ICP-Brasil e similares) que são imagem. Usa OCR, então deixa a análise mais lenta — ligue quando o documento tiver esses selos e quiser removê-los.",
    )
    detectar_imagens = st.toggle(
        "Imagens",
        value=False,
        help="Encontra outras imagens do documento (assinatura manuscrita "
             "digitalizada, carimbo, foto, brasão/logo) e lista uma a uma "
             "para revisão — \"Imagem 1\", \"Imagem 2\"... A heurística olha "
             "só posição e tamanho, não o conteúdo, então pode pegar coisa "
             "que não é dado pessoal (marque/desmarque cada uma na tabela). "
             "Compartilha o mesmo OCR de \"Selos de assinatura digital\", "
             "então ligar as duas juntas não deixa a análise mais lenta que "
             "ligar uma só.",
    )

    pdf_bytes = st.session_state.get("pdf_bytes")
    file_id = st.session_state.get("pdf_file_id")
    if pdf_bytes is not None and file_id is not None and (detectar_selos or detectar_imagens):
        if st.session_state.get("_selos_key") != file_id:
            progress_ph = st.empty()
            barra = progress_ph.progress(0.0, text="Verificando selos e imagens...")

            def _prog_selos(pagina_atual, total_paginas):
                barra.progress(
                    min(max(pagina_atual / max(total_paginas, 1), 0.0), 1.0),
                    text=f"Verificando selos e imagens — página "
                         f"{pagina_atual}/{total_paginas}",
                )

            st.session_state["selo_grupos"] = analyze_seals(
                pdf_bytes, on_progress=_prog_selos)
            st.session_state["_selos_key"] = file_id
            progress_ph.empty()

    # `_selos_flags_aplicados` é mantido em dia pelo código logo após o
    # upload (fora deste fragment) em TODO rerun completo — então um
    # descompasso aqui só pode vir de um rerun ESCOPADO deste fragment
    # (toggle clicado, aquele código de fora nem rodou nesta passada). Só
    # nesse caso vale a pena forçar o rerun completo.
    if file_id is not None:
        assinatura = (detectar_selos, detectar_imagens, file_id)
        if st.session_state.get("_selos_flags_aplicados") != assinatura:
            st.session_state["_selos_flags_aplicados"] = assinatura
            st.rerun(scope="app")

    return detectar_selos, detectar_imagens


# ---------------------------------------------------------------------------
# Sidebar: configurações
# ---------------------------------------------------------------------------
# Sidebar no formato "seções + interruptores" (proposta A do demo_sidebar.py):
# dois grupos com título — o que detectar / como tratar — com st.toggle no
# lugar de checkbox e rótulos curtos; a explicação completa de cada opção
# mora no tooltip (ícone ? ao lado do rótulo). Mesmos controles, valores
# padrão e variáveis de antes: só a apresentação mudou.
with st.sidebar:
    st.header("Configurações")
    st.markdown("##### 🔎 O que detectar")
    tipos = st.multiselect(
        "Tipos de informação pessoal a detectar",
        options=DEFAULT_ENTITIES,
        default=[e for e in DEFAULT_ENTITIES if e not in ("CNPJ", "Telefone", "RG")],
        label_visibility="collapsed",
        help="Tipos de informação pessoal a procurar no documento.",
    )
    # A edição LEVE do Tarjador Desktop (instalador de ~175 MB) não embarca
    # torch/transformers nem os pesos do modelo. Em vez de oferecer um botão
    # que falharia no clique, o toggle vem desligado e desabilitado, dizendo
    # onde a IA existe. Fora do desktop a variável não existe e nada muda:
    # `_sem_ia` é False, e o toggle é o de sempre (ligado e habilitado).
    _sem_ia = os.environ.get("TARJADOR_EDITION", "").lower() == "lite"
    usar_ner_bert = st.toggle(
        "Nomes com IA",
        value=not _sem_ia,
        disabled=_sem_ia,
        help="Modelo **BERTimbau** (BERT), afinado com textos jurídicos brasileiros LeNER-Br. "
             "Reconhece leis, órgãos e datas como tais, e não como nome de pessoa, sendo preciso em reconhecer "
             "nomes em ofícios e pareceres jurídicos."
             + ("\n\n**Não disponível nesta edição.** Esta é a *Edição Leve*, "
                "que não inclui o modelo de IA. Os nomes continuam sendo "
                "detectados pelas heurísticas e pelo spaCy. Para usar a IA, "
                "instale a *Edição Completa*." if _sem_ia else ""),
    )
    detectar_selos, detectar_imagens = _secao_selos()
    st.markdown("##### ⬛ Como tratar")
    modo_cpf = st.radio(
        "CPF",
        ["Descaracterizar", "Tarjar"],
        horizontal=True,
        help="**Descaracterizar** (padrão): substitui os 3 primeiros "
             "algarismos e os 2 dígitos verificadores por asteriscos, "
             "mantendo o miolo visível — `***.123.456-**`. O número original "
             "é removido de verdade do arquivo (mesma remoção da tarja); o "
             "que fica é só a forma descaracterizada. **Tarjar**: cobre o "
             "CPF inteiro com tarja preta. Selos e imagens continuam sendo "
             "tarjados nos dois modos.",
    )
    descaracterizar_cpf = modo_cpf == "Descaracterizar"
    tarjar_emails_funcionais = st.toggle(
        "Tarjar e-mails funcionais",
        value=False,
        help="E-mail **funcional** é o fornecido por órgão ou instituição: "
             "domínio de ente público (gov.br, leg.br, jus.br, mp.br, def.br, "
             "mil.br, tc.br — mesmo quando a parte local é o nome do "
             "servidor), caixas institucionais (gabinete@, secretaria@, "
             "siglas) e instituições conhecidas (Banco do Brasil, Banco "
             "Mundial, BID, FMI). Em documento oficial normalmente podem "
             "permanecer visíveis, então vêm **desmarcados** na tabela por "
             "padrão — você ainda pode marcá-los caso a caso. Ligado, vêm "
             "marcados.",
    )
    tarjar_dominio_email = st.toggle(
        "Tarjar domínio de e-mail",
        value=False,
        help="Por padrão, a tarja de e-mail cobre só a parte antes do `@` "
             "(o domínio não é dado pessoal e permanece visível). Ligue para "
             "cobrir o e-mail inteiro, incluindo o domínio — vale tanto para a "
             "prévia (retângulo cinza/preto na página) quanto para a tarja final.",
    )
    tarjar_assinaturas = st.toggle(
        "Tarjar nomes em assinaturas",
        value=False,
        help="Nomes que só aparecem em bloco de assinatura (assinatura eletrônica "
             "do SEI, nome sobre cargo, linha de assinatura) costumam não precisar "
             "ser ocultados. Desligado (padrão), esses nomes vêm **desmarcados** na "
             "tabela — você ainda pode marcá-los caso a caso. Ligado, vêm marcados.",
    )
    st.divider()
    with st.expander("⚙️ Avançado"):
        usar_ner = st.toggle(
            "Nomes com IA — spaCy (rápido)",
            value=False,
            help="Modelo spaCy que procura nomes de pessoas no corpo do texto, é leve e rápido, mas em textos jurídicos ou legais erra mais — pode marcar leis e órgãos como se fossem nome.",
        )
        sempre_heuristicas = st.toggle(
            "Sempre considerar heurísticas",
            value=False,
            help="Com a IA ligada, as heurísticas de nome (proximidade a CPF, bloco de assinatura, cabeçalho de e-mail) ficam desligadas por padrão — a IA sozinha já cobre esses casos e as heurísticas podem somar falso positivo. Ligue para rodar as duas juntas.",
        )
        achatar_tudo = st.toggle(
            "Segurança máxima (achatar)",
            value=False,
            help="Ao gerar o PDF, transforma **todas as páginas em imagem** — "
                 "não só as áreas tarjadas. Isso garante que nenhum texto, "
                 "objeto oculto ou camada escondida sobreviva por trás das "
                 "tarjas, mesmo em PDFs manipulados de forma incomum. "
                 "Efeitos colaterais: o arquivo fica maior e perde a seleção "
                 "e a busca de texto (deixa de ser pesquisável). Use quando "
                 "quiser o máximo de garantia e não precisar copiar texto do "
                 "PDF final.",
        )
    if st.button("ℹ️ Mais informações", use_container_width=True):
        _mostrar_mais_informacoes()
    # Download do Tarjador Desktop. Só nos deploys WEB: dentro do app
    # instalado (TARJADOR_EDITION definida pelo lançador) oferecer "instale no
    # computador" seria ruído. As URLs são estáveis por construção —
    # `releases/latest/download/` + nome de asset SEM versão (o build publica
    # cópias com esses nomes): versão nova = release nova, zero mudança aqui.
    if not os.environ.get("TARJADOR_EDITION"):
        # Repositório PÚBLICO só de releases: o repo do fonte é privado, e
        # release de repo privado dá 404 para qualquer visitante — descoberto
        # com os botões já no ar. Assets e histórico do fonte ficam separados.
        _REL = "https://github.com/mariosalaroli/tarjador-releases/releases"
        with st.expander("🖥️ Instalar no computador"):
            st.caption(
                "O documento **nunca sai da sua máquina** — processa tudo "
                "localmente e funciona sem internet."
            )
            st.link_button(
                "⬇️ Edição Leve — 143 MB",
                f"{_REL}/latest/download/TarjadorSetup-Leve.exe",
                help="Detecção completa (CPF, nomes, e-mails, selos com OCR), "
                     "sem o modelo de IA — os nomes vêm das heurísticas e do "
                     "spaCy. Ocupa ~580 MB instalado.",
                use_container_width=True,
            )
            st.link_button(
                "⬇️ Edição Completa — 650 MB",
                f"{_REL}/latest/download/TarjadorSetup-Completa.exe",
                help="Tudo da Leve + a IA de nomes (**BERTimbau** jurídico, "
                     "o mesmo deste site), funcionando offline. Ocupa "
                     "~1,6 GB instalado. Instalar por cima da Leve atualiza.",
                use_container_width=True,
            )
            st.caption(f"Windows 10/11, 64 bits. [Todas as versões e "
                       f"verificação SHA-256]({_REL}).")
    else:
        # Rodando no Tarjador Desktop: mostrar edição e versão. Sem isto, um
        # chamado de suporte começa com "qual versão você tem?" e o usuário
        # não tem como saber (a versão existe nas propriedades do .exe, mas
        # ninguém abre isso).
        _ed = "Completa" if os.environ["TARJADOR_EDITION"] == "full" else "Leve"
        _ver = os.environ.get("TARJADOR_VERSION", "")
        st.caption(f"Tarjador Desktop · Edição {_ed}"
                   + (f" · v{_ver}" if _ver else ""))

# Começa a baixar o modelo de IA JÁ, em segundo plano, enquanto o usuário
# escolhe o arquivo — em vez de só quando a análise pedir. Em rede lenta (o
# Streamlit Cloud demora minutos para puxar os ~430 MB do Hugging Face Hub)
# isso é a diferença entre a análise esperar do zero e achar o modelo pronto.
# Não bloqueia nada: se ainda não estiver pronto na hora da análise, ela segue
# com as heurísticas e o download continua para a próxima.
if usar_ner_bert:
    from tarjador.core._ner_bert import iniciar_carga
    iniciar_carga()

# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
st.subheader("1. Envie o PDF")
uploaded = st.file_uploader(
    "Selecione o arquivo",
    type=["pdf"],
    help="Arraste ou clique para selecionar um arquivo PDF.",
    label_visibility="collapsed",
)

if uploaded is None:
    st.info("Aguardando upload de um arquivo PDF.")
    st.stop()

pdf_bytes = uploaded.getvalue()
# Bytes/id em session_state: é como o fragment _secao_selos (definido e
# executado ANTES deste ponto do script, lá na sidebar) enxerga o arquivo sem
# precisar rechamar st.file_uploader — ver comentário na definição dele.
st.session_state["pdf_bytes"] = pdf_bytes
st.session_state["pdf_file_id"] = uploaded.file_id
# Sincroniza a mesma assinatura que o fragment usa para decidir se precisa
# forçar um rerun completo (ver final de _secao_selos). Isto roda DEPOIS do
# fragment, na mesma passada de QUALQUER rerun completo — inclusive o
# primeiro em que `file_id` passa a existir, que sem isto o fragment via
# como "mudou" (nada em session_state ainda tinha essa combinação) e
# disparava um st.rerun(scope="app") por conta própria. Esse rerun extra,
# colidindo com o rerun que outro widget da sidebar (ex.: o multiselect de
# tipos) já estava fazendo, é o que derrubava o arquivo do
# `st.file_uploader` por uma passada ("Aguardando upload..." com o PDF
# ainda anexado). Escrever aqui deixa o fragment sempre já "em dia" ao
# final de um rerun completo — só falta forçar o rerun de fato quando a
# MUDANÇA nasce dentro do próprio fragment (rerun escopado, sem passar por
# aqui), que é o único caso em que isto é necessário.
st.session_state["_selos_flags_aplicados"] = (
    detectar_selos, detectar_imagens, uploaded.file_id)

# ---------------------------------------------------------------------------
# Análise
# ---------------------------------------------------------------------------
# A análise é dividida em DUAS partes cacheadas de forma independente, para
# que os toggles da sidebar não custem uma reanálise do documento inteiro:
#
# 1. Entidades de texto (`cache_key`) — regex/IA. Só refaz quando muda o
#    arquivo, os tipos ou os motores de detecção. NÃO inclui `detectar_selos`,
#    `detectar_imagens` nem `tarjar_assinaturas` (ver abaixo).
# 2. Selos e imagens (`_selos_key`, só o arquivo) — é o passo CARO (OCR
#    página a página) e independe de qualquer outra opção. `detectar_selos` e
#    `detectar_imagens` disparam o MESMO OCR (rodado uma vez) e cada um só
#    filtra o tipo que quer do resultado guardado — ligar a 1ª vez roda o
#    OCR; desligar tira o tipo na hora; religar reaproveita sem OCR de novo.
#
# `tarjar_assinaturas` fica FORA das duas: não muda a detecção em nada — só
# decide se um nome que aparece exclusivamente em bloco de assinatura vem
# pré-marcado (`tarjar_default`) e se as ocorrências em assinatura ganham
# caixa/tarja. Ambos são aplicados depois, sobre os grupos já analisados.
cache_key = (uploaded.file_id, usar_ner, usar_ner_bert,
             sempre_heuristicas, tuple(tipos))
recem_analisado = False
if st.session_state.get("_cache_key") != cache_key:
    # Novo arquivo ou motor alterado — analisar novamente. Barra de progresso
    # real (não animação): analyze() chama on_progress página a página,
    # durante a própria detecção — reflete o processamento de fato.
    progress_ph = st.empty()
    barra = progress_ph.progress(0.0, text="Analisando o documento...")

    def _atualiza_progresso(frac, rotulo):
        barra.progress(min(max(frac, 0.0), 1.0), text=rotulo)

    avisos: list[str] = []

    try:
        info, grupos_base = analyze(
            pdf_bytes, tipos, max_size_mb=MAX_FILE_SIZE_MB,
            detectar_selos=False,  # selos têm cache próprio, ver abaixo
            usar_ner=usar_ner,
            usar_ner_bert=usar_ner_bert,
            sempre_heuristicas=sempre_heuristicas,
            # Sempre True aqui: o efeito do toggle é reaplicado depois sobre os
            # grupos, sem reanálise (`tarjar_default` de nomes de assinatura).
            tarjar_nomes_assinatura=True,
            on_progress=_atualiza_progresso,
            on_warning=avisos.append,
            # Rótulo honesto enquanto o modelo de IA carrega: sem isso a barra
            # fica em "Analisando o documento..." durante um download longo e
            # parece travamento.
            on_status=lambda msg: barra.progress(0.0, text=msg),
        )
    except PDFValidationError as e:
        progress_ph.empty()
        st.error(str(e))
        st.stop()

    progress_ph.empty()
    st.session_state["info"] = info
    st.session_state["grupos_base"] = grupos_base
    st.session_state["avisos"] = avisos
    st.session_state["_cache_key"] = cache_key
    recem_analisado = True

info = st.session_state["info"]
grupos_base = st.session_state["grupos_base"]

# Avisos não fatais da análise (ex.: a IA de nomes não pôde ser carregada e as
# heurísticas entraram no lugar). Persistem enquanto a análise for a mesma —
# é informação que o usuário precisa ter em mente ao revisar, não um flash.
for _aviso in st.session_state.get("avisos", []):
    st.warning(f"⚠️ {_aviso}")


# Selos e imagens: mesmo OCR caro (detect_seal_pages), rodado uma vez por
# arquivo e guardado inteiro (os dois tipos juntos) — "Detectar selos" e
# "Detectar imagens" apenas FILTRAM esse resultado cada um pro seu tipo, sem
# refazer o OCR. Ligar as duas juntas custa o mesmo que ligar uma só; ligar
# a 2ª depois de já ter rodado a 1ª não refaz nada.
#
# Este bloco é o caminho de RESGATE, não o principal: o normal é o fragment
# _secao_selos (na sidebar) já ter feito o OCR num rerun escopado, e aqui só
# sobra filtrar o cache. Este bloco só faz o OCR de verdade quando o arquivo
# É NOVO — nesse caso o fragment rodou ANTES do upload aparecer no
# session_state (ver comentário na definição dele) e não pôde OCRar; mas um
# upload novo já força um rerun completo por causa da reanálise do texto, daí
# não ter o mesmo problema de "refresh" que motivou o fragment.
selo_grupos = []
if detectar_selos or detectar_imagens:
    if st.session_state.get("_selos_key") != uploaded.file_id:
        progress_ph = st.empty()
        barra = progress_ph.progress(0.0, text="Verificando selos e imagens...")

        def _prog_selos(pagina_atual, total_paginas):
            barra.progress(
                min(max(pagina_atual / max(total_paginas, 1), 0.0), 1.0),
                text=f"Verificando selos e imagens — página "
                     f"{pagina_atual}/{total_paginas}",
            )

        st.session_state["selo_grupos"] = analyze_seals(
            pdf_bytes, on_progress=_prog_selos)
        st.session_state["_selos_key"] = uploaded.file_id
        progress_ph.empty()
    todos_selo_grupos = st.session_state.get("selo_grupos") or []
    if detectar_selos:
        selo_grupos += [g for g in todos_selo_grupos if g.tipo == SEAL_ENTITY_TIPO]
    if detectar_imagens:
        selo_grupos += [g for g in todos_selo_grupos if g.tipo == IMAGE_ENTITY_TIPO]

# `tarjar_assinaturas`: reaplica o pré-marcado dos nomes que aparecem SÓ em
# bloco de assinatura. É idempotente (não acumula) e não custa nada — em vez
# de reanalisar, mexe num booleano por grupo.
for g in grupos_base:
    if g.tipo == "Nome" and g.em_assinatura:
        g.tarjar_default = bool(tarjar_assinaturas)

# `tarjar_emails_funcionais`: mesmo mecanismo para os e-mails funcionais/
# institucionais (domínio de órgão público, caixa institucional, display de
# órgão — classificação feita na análise e guardada em `institucional`).
# Fica FORA da cache_key da análise pelo mesmo motivo do toggle de
# assinaturas: só muda pré-marcado, não detecção.
for g in grupos_base:
    if g.tipo == "E-mail" and g.institucional:
        g.tarjar_default = bool(tarjar_emails_funcionais)

grupos = list(grupos_base) + list(selo_grupos)

# Banner "PDF válido" que some sozinho após 4s (animação CSS — não bloqueia o
# script). Só aparece logo após a análise, para não repiscar a cada rerun
# (clique num checkbox, navegação, etc.).
if recem_analisado:
    _msg_valido = (f"✅ <b>PDF válido</b> — {info['pages']} páginas, "
                   f"{info['chars']} caracteres extraídos.")
    st.html(
        "<style>"
        "@keyframes tjFadeValido{"
        "0%,80%{opacity:1;}"
        "100%{opacity:0;max-height:0;margin:0;padding-top:0;padding-bottom:0;"
        "border-width:0;overflow:hidden;}}"
        ".tj-valido{background:rgba(33,195,84,.10);color:#1a7f37;"
        "border:1px solid rgba(33,195,84,.25);border-radius:.5rem;"
        "padding:.75rem 1rem;font-size:.9rem;max-height:80px;margin-bottom:1rem;"
        "animation:tjFadeValido .6s ease 4s forwards;}"
        "@media (prefers-color-scheme:dark){.tj-valido{color:#3dd68c;}}"
        f"</style><div class='tj-valido'>{_msg_valido}</div>"
    )

if not grupos:
    st.warning("Nenhuma informação pessoal detectada para os tipos selecionados.")
    st.stop()

# ---------------------------------------------------------------------------
# Estado único (fonte de verdade) da coluna "Tarjar?", compartilhado entre a
# tabela e o clique no PDF. Reinicia do zero só quando a ANÁLISE muda (arquivo
# novo, outro motor de detecção, outros tipos). Os toggles de selo e de
# assinatura NÃO resetam: eles apenas acrescentam/removem entidades ou mudam o
# pré-marcado de um subconjunto — a seleção que o usuário já fez à mão nas
# demais entidades é preservada (antes, qualquer um dos dois jogava tudo fora).
# ---------------------------------------------------------------------------
if st.session_state.get("_marcar_sig") != cache_key:
    st.session_state._marcar_sig = cache_key
    st.session_state.marcar = {_gid(g): bool(g.tarjar_default) for g in grupos}
    st.session_state.ed_ver = 0
    # py_ver: versão das mudanças NASCIDAS NO PYTHON (tabela/marcar-tudo);
    # quando bumpa, o componente do PDF adota o `marcar` dos args. _comp_seq
    # deduplica os envios do componente (ver _aplicar_valor_componente).
    st.session_state.py_ver = 0
    st.session_state._comp_seq = None
    st.session_state.manual_rects = []
    st.session_state.nav_focus = None
    st.session_state.nav_occ = 0
    st.session_state._assin_flag = tarjar_assinaturas
    st.session_state._email_func_flag = tarjar_emails_funcionais

marcar = st.session_state.marcar
_mudou_py = False

# Toggle de assinaturas: reaplica o pré-marcado APENAS nos nomes que só
# aparecem em bloco de assinatura — o resto da seleção fica como está.
if st.session_state.get("_assin_flag") != tarjar_assinaturas:
    st.session_state._assin_flag = tarjar_assinaturas
    for g in grupos:
        if g.tipo == "Nome" and g.em_assinatura:
            marcar[_gid(g)] = bool(tarjar_assinaturas)
    _mudou_py = True

# Toggle de e-mails funcionais: idem, só nos e-mails classificados como
# funcionais/institucionais na análise — a seleção manual do resto fica.
if st.session_state.get("_email_func_flag") != tarjar_emails_funcionais:
    st.session_state._email_func_flag = tarjar_emails_funcionais
    for g in grupos:
        if g.tipo == "E-mail" and g.institucional:
            marcar[_gid(g)] = bool(tarjar_emails_funcionais)
    _mudou_py = True

# Reconcilia as chaves com os grupos atuais: entra o selo quando a opção é
# ligada (com seu default), some quando é desligada. Sem tocar no resto.
gids_atuais = {_gid(g) for g in grupos}
for g in grupos:
    if _gid(g) not in marcar:
        marcar[_gid(g)] = bool(g.tarjar_default)
        _mudou_py = True
for gid in [gid for gid in marcar if gid not in gids_atuais]:
    del marcar[gid]
    _mudou_py = True

# Mudança nascida no Python: o componente do PDF precisa adotar o novo estado
# (sem esse bump ele trataria os args como eco do próprio envio e ignoraria).
if _mudou_py and "py_ver" in st.session_state:
    st.session_state.py_ver += 1
    st.session_state.ed_ver += 1  # re-keia os checkboxes da tabela

# ---------------------------------------------------------------------------
# Seção de revisão como FRAGMENT: cliques em checkbox/caixa do PDF/página
# rerodam SÓ esta seção, não o script inteiro — no servidor hospedado é a
# diferença entre resposta imediata e um rerun completo a cada clique.
# O estado compartilhado (marcar, ed_ver, py_ver, nav) vive no session_state,
# então o comportamento não muda — só o escopo do rerun. Os st.rerun() aqui
# dentro usam scope="fragment": widgets do fragment disparam rerun de
# fragment, então o escopo é sempre válido nesses handlers.
@st.fragment
def _secao_revisao():
    # Ordem importa: 1º os toggles vindos do componente do PDF (o valor já
    # está no session_state na chegada do rerun — consumir aqui evita um
    # segundo rerun), 2º os checkboxes da tabela — assim KPI, tabela e
    # documento refletem tudo no MESMO rerun.
    _aplicar_valor_componente()
    _sync_marcar_dos_checkboxes()

    # O componente não conseguiu buscar o PDF pela URL /media/… e pediu os
    # bytes (acontece atrás de autenticação/CDN, como no Streamlit Cloud, onde
    # a requisição volta como página HTML). Reenvia o binário pelos args, pela
    # conexão que já está autenticada. A flag é sticky: uma vez que o ambiente
    # se mostrou incapaz de servir a URL, não adianta tentar de novo.
    _val_comp = st.session_state.get("pdfrev")
    if isinstance(_val_comp, dict) and _val_comp.get("need_bytes"):
        st.session_state.pdf_via_args = True

    # Geometria de todas as caixas (cacheada — só recalcula em novo documento/
    # análise/toggle de sidebar). Usada pelo componente e pelo ciclo de
    # navegação da tabela. `sig`: identidade barata (cache_key da análise +
    # tarjar_dominio_email) — ver docstring de _boxes_todas_paginas sobre por
    # que isso substitui st.cache_data aqui. Spinner: em cache hit (comum, a
    # cada clique) passa despercebido; no 1º cálculo de um documento grande
    # (dezenas de páginas × centenas de buscas de texto) dá feedback visível
    # no lugar do silêncio que parecia travamento após a barra de análise
    # chegar a 100%.
    with st.spinner("Preparando marcações no documento..."):
        boxes, gid_box_pages = _boxes_todas_paginas(
            pdf_bytes, grupos, tarjar_dominio_email, tarjar_assinaturas,
            descaracterizar_cpf,
            sig=(cache_key, tarjar_dominio_email, tarjar_assinaturas,
                 detectar_selos, detectar_imagens, descaracterizar_cpf))

    st.subheader("2. Revise as informações pessoais encontradas")

    col_panel, col_pdf = st.columns([5, 8], gap="medium")

    # ---------------------------------------------------------------------------
    # Painel (esquerda, 1/3): KPIs + filtro de tipo + tabela de Seleção/Navegação.
    # Roda antes do PDF no código: clique na linha já ajusta a página.
    # ---------------------------------------------------------------------------
    with col_panel:
        # Container com key → âncora que o JS do visualizador usa para achar
        # esta coluna no DOM do Streamlit e desenhar o divisor arrastável
        # entre ela e o PDF (ver setupSplitter() em viewer.js).
        with st.container(key="painel_revisao"):
            n_tot = len(grupos)
            n_marc = sum(1 for g in grupos if marcar.get(_gid(g), False))
            # Páginas com pelo menos uma ocorrência encontrada (distintas).
            n_pags_ocorr = len({p for g in grupos for p in g.paginas})
            _kpis_html(n_tot, n_marc, info["pages"], n_pags_ocorr)

            # Filtro por tipo (pílulas, seleção múltipla) com a contagem no
            # rótulo. Afeta a tabela abaixo — quando um ou mais tipos estão
            # ativos, entidades dos demais tipos ficam ocultas mas continuam
            # marcadas se já estavam (ver contador). Nenhum tipo selecionado =
            # mostra todos (o padrão inicial).
            cont_tipo = {}
            for g in grupos:
                cont_tipo[g.tipo] = cont_tipo.get(g.tipo, 0) + 1
            rot_tipo = {f"{t} · {c}": t for t, c in cont_tipo.items()}
            with st.container(key="wrap_tipo"):
                escolha = st.pills("Tipo", list(rot_tipo),
                                   selection_mode="multi", label_visibility="collapsed",
                                   key="filtro_tipo")
            tipos_sel = {rot_tipo[e] for e in (escolha or [])}
            alvo = [g for g in grupos if not tipos_sel or g.tipo in tipos_sel]

            # Seleção + Navegação numa lista só — ver _tabela_unificada.
            _tabela_unificada(alvo, gid_box_pages)
            alvo_gids = {_gid(g) for g in alvo}
            foco = st.session_state.get("nav_focus")
            nav_gid = foco if foco in alvo_gids else None

    # ---------------------------------------------------------------------------
    # PDF (direita, 2/3): componente customizado (viewer.js) — scroll contínuo,
    # zoom por Ctrl+rolagem e toggle de tarja por clique, tudo client-side.
    # A navegação de página (barra, campo, "próxima encontrada") vive na
    # toolbar do próprio componente; nenhum desses gestos passa pelo servidor.
    # ---------------------------------------------------------------------------
    with col_pdf:
        # Container com key → moldura (borda + cantos arredondados) via CSS
        # mirando o iframe do componente (ver bloco de estilo no topo).
        # height fixa (mesmo teto da lista de Navegação e Seleção, 960px):
        # o iframe nasce já com a altura certa, sem pulo de layout.
        # O valor do componente (toggles por clique no documento) NÃO é lido
        # aqui: _aplicar_valor_componente o consome no INÍCIO do fragment —
        # um clique no PDF custa um rerun só, não dois.
        with st.container(key="pdfframe"):
            pdf_review(
                pdf_url=pdf_media_url(pdf_bytes, str(uploaded.file_id)),
                pdf_b64=(_pdf_b64(pdf_bytes)
                         if st.session_state.get("pdf_via_args") else ""),
                n_pages=info["pages"],
                boxes=boxes,
                marcar=marcar,
                py_ver=st.session_state.py_ver,
                nav=([nav_gid, st.session_state.get("nav_occ", 0)]
                     if nav_gid else None),
                manual=st.session_state.get("manual_rects") or [],
                height=960,
                key="pdfrev",
            )

    marcados = sum(1 for v in marcar.values() if v)

    # ---------------------------------------------------------------------------
    # Aplicar tarjas e baixar
    # ---------------------------------------------------------------------------
    st.subheader("3. Aplicar tarjas e baixar")

    # Sem entidade marcada, ainda dá para baixar: o PDF sai sem tarjas, mas
    # passa pelas demais limpezas (metadados, anexos, JavaScript, texto
    # invisível) — e o resumo abaixo deixa claro que nada foi tarjado.
    manual_rects = st.session_state.get("manual_rects") or []
    tem_tarja = bool(marcados or manual_rects)
    if manual_rects:
        n_manual = len(manual_rects)
        st.caption(
            f"🖌️ {_plural(n_manual, 'tarja manual', 'tarjas manuais')} do rolo "
            f"{'será aplicada' if n_manual == 1 else 'serão aplicadas'} junto."
        )
    if not tem_tarja:
        st.info(
            "Nenhuma entidade marcada — o PDF será gerado **sem tarjas**, "
            "apenas com a sanitização do arquivo (metadados, anexos, "
            "JavaScript e texto invisível)."
        )
    rotulo_btn = ("Aplicar tarjas, sanitizar e gerar o PDF" if tem_tarja
                  else "Sanitizar e gerar PDF (sem tarjas)")
    if st.button(rotulo_btn, type="primary", use_container_width=True):
        with st.spinner("Tarjando..." if tem_tarja else "Sanitizando..."):
            aprovados = [g for g in grupos if marcar.get(_gid(g), False)]
            if marcados and not aprovados:
                st.error("Erro ao localizar entidades. Recarregue a página e tente novamente.")
                st.stop()
            redacted, total_ocorr, san = apply_redactions(
                pdf_bytes, aprovados, redact_email_domain=tarjar_dominio_email,
                tarjar_nomes_assinatura=tarjar_assinaturas,
                manual_rects=manual_rects, achatar_tudo=achatar_tudo,
                descaracterizar_cpf=descaracterizar_cpf)

        paginas_tarjadas = ({o.pagina for g in aprovados for o in g.ocorrencias}
                            | {int(r[0]) for r in manual_rects})
        if total_ocorr:
            st.success("✓ **PDF sanitizado e tarjado gerado com sucesso.**")
        else:
            st.success("✓ **PDF sanitizado gerado** — nenhuma tarja aplicada.")

        # Resumo de conformidade: o que existia no arquivo e foi tratado.
        # "✓" (verde, via _check) = a checagem/limpeza rodou (achando algo ou
        # não — checar e não achar nada também é trabalho feito); "—" só para
        # o caso em que a ação nem foi tentada (nenhuma tarja marcada); "○" =
        # achou e manteve de propósito (decisão, não ausência) — os dois
        # ficam na cor padrão, não são a mesma confirmação do ✓.
        if total_ocorr:
            n_pag = len(paginas_tarjadas)
            linha_redacao = _check(
                f"**Redação permanente** — "
                f"{_plural(total_ocorr, 'ocorrência removida', 'ocorrências removidas')}"
                f" do conteúdo em {_plural(n_pag, 'página', 'páginas')}"
            )
        else:
            linha_redacao = ("— **Redação permanente** — nenhuma tarja "
                             "aplicada (nenhuma entidade marcada)")
        # CPFs descaracterizados (***.456.789-**) são uma REDAÇÃO PERMANENTE
        # à parte — o número original também sai do stream, mas não deixa
        # tarja preta — por isso contam separado de "ocorrências removidas"
        # acima, não como subconjunto silencioso dela. Só aparece quando o
        # modo estava ligado E havia pelo menos um CPF entre os aprovados.
        n_cpf_descarac = (
            sum(len(g.ocorrencias) for g in aprovados if g.tipo == "CPF")
            if descaracterizar_cpf else 0
        )
        linha_invisivel = {
            "removido": _check("**Texto invisível** — encontrado e removido"),
            "nenhum": _check("**Texto invisível** — nenhum encontrado"),
            "preservado_ocr": ("○ **Texto invisível** — mantido: é a camada de "
                               "OCR deste documento escaneado, necessária para "
                               "busca e seleção de texto"),
        }[san.texto_invisivel]
        linhas = [linha_redacao]
        if n_cpf_descarac:
            linhas.append(_check(
                f"**CPF descaracterizado** — "
                f"{_plural(n_cpf_descarac, 'CPF alterado', 'CPFs alterados')}"
                f" para o formato `***.456.789-**`"
            ))
        linhas += [
            _check("**Metadados** — autor, título, ferramenta e datas apagados"
                   if san.metadados else "**Metadados** — nenhum encontrado"),
            linha_invisivel,
            _check(
                f"**Anexos** — "
                f"{_plural(san.anexos, 'arquivo embutido removido', 'arquivos embutidos removidos')}"
                if san.anexos else "**Anexos** — nenhum encontrado"),
            _check("**JavaScript** — encontrado e removido"
                   if san.javascript else "**JavaScript** — nenhum encontrado"),
        ]
        st.markdown("\n".join(f"- {linha}" for linha in linhas),
                    unsafe_allow_html=True)

        sufixo = "_tarjado.pdf" if total_ocorr else "_sanitizado.pdf"
        nome_saida = uploaded.name.rsplit(".", 1)[0] + sufixo
        st.download_button(
            label="⬇️ Baixar PDF tarjado" if total_ocorr else "⬇️ Baixar PDF sanitizado",
            data=redacted,
            file_name=nome_saida,
            mime="application/pdf",
            use_container_width=True,
        )


_secao_revisao()
