"""Aplicação de tarjas verdadeiras (remoção de conteúdo, não apenas retângulo)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Callable

import fitz  # PyMuPDF


@dataclass
class SanitizationReport:
    """O que a sanitização encontrou no arquivo (e removeu), além das tarjas.

    Alimenta o resumo de conformidade exibido junto do download — cada campo
    reflete o que EXISTIA no PDF original, não apenas o que a limpeza tentou
    fazer (limpar um arquivo que não tinha anexos não é "anexos removidos").
    """
    metadados: bool = False        # havia metadados (Info dict/XMP); apagados
    anexos: int = 0                # arquivos embutidos/anexados; removidos
    javascript: bool = False       # havia JavaScript; removido
    texto_invisivel: str = "nenhum"  # "removido" | "nenhum" | "preservado_ocr"

# Tipo de entidade usado para representar selos de assinatura digital na
# tabela de revisão (mesmo formato de tipo/texto dos grupos do detector).
SEAL_ENTITY_TIPO = "Selo"

def cpf_descaracterizado(cpf: str) -> str:
    """Forma descaracterizada do CPF: os 3 primeiros algarismos e os 2 dígitos
    verificadores viram `*`, o miolo fica visível — `***.456.789-**`.

    Opera sobre o texto COMO ESTÁ no documento, preservando a máscara
    original (pontos/traço ficam onde estavam); um CPF sem pontuação
    (`12345678901`) vira `***456789**`.
    """
    out = []
    idx = 0
    for ch in cpf:
        if ch.isdigit():
            out.append("*" if idx < 3 or idx >= 9 else ch)
            idx += 1
        else:
            out.append(ch)
    return "".join(out)


# Imagem com cara de assinatura/carimbo (assinatura manuscrita escaneada, selo
# de plataforma sem texto reconhecível, brasão sobre a linha de assinatura...).
# Achada por HEURÍSTICA de geometria — não por âncora de texto nem OCR —, então
# não tem a confiança de um selo confirmado; por isso é um tipo à parte, que o
# usuário vê e pode desmarcar. Antes essa heurística existia SÓ na exportação:
# o arquivo saía com a imagem apagada sem que ela tivesse aparecido na revisão.
IMAGE_ENTITY_TIPO = "Imagem"


def occurrence_rects(page, texto: str, inicios: list[int] | None):
    """Retângulos do `texto` na página, restritos às ocorrências DETECTADAS.

    `inicios`: offsets (em `page.get_text()`) das ocorrências que o detector
    de fato encontrou — para entidades com contexto obrigatório (RG), o mesmo
    número pode aparecer de novo na página como protocolo/matrícula e NÃO
    deve ser tarjado. O pareamento hit↔offset é por ordinal: `search_for` e
    `finditer` percorrem a página na mesma ordem de leitura.

    Fallback seguro: com `inicios=None` (chamador sem offsets) ou qualquer
    ambiguidade — contagens divergentes (hit quebrado em duas linhas gera 2
    retângulos por ocorrência), caixa diferente (search_for é
    caixa-insensível, finditer não) — devolve TODOS os hits: sobre-tarjar é
    o lado seguro; nunca deixar um dado detectado sem tarja.
    """
    rects = page.search_for(texto)
    if inicios is None:
        return rects
    # IGNORECASE: search_for é caixa-insensível; sem isso, um nome "Fulano de
    # Tal" no corpo e "FULANO DE TAL" na assinatura desalinharia as contagens
    # e cairia sempre no fallback (grafias só de caixa têm o mesmo tamanho,
    # então os ordinais continuam pareados).
    positions = [m.start() for m in
                 re.finditer(re.escape(texto), page.get_text(), re.IGNORECASE)]
    if not positions or len(positions) != len(rects):
        return rects
    alvo = set(inicios)
    keep = [r for pos, r in zip(positions, rects) if pos in alvo]
    return keep or rects


def redact_pdf(pdf_bytes: bytes, textos_por_pagina: dict[int, list[str]],
               redact_email_domain: bool = False,
               inicios_por_pagina: dict[int, dict[str, list[int]]] | None = None,
               rects_por_pagina: dict[int, list[tuple]] | None = None,
               selos_por_pagina: dict[int, list[tuple]] | None = None,
               substituir_por_pagina: dict[int, dict[str, str]] | None = None,
               achatar_tudo: bool = False,
               ) -> tuple[bytes, SanitizationReport]:
    """Tarja em cada página os textos indicados.

    `textos_por_pagina`: mapa `{numero_pagina_0indexed: [textos]}`.
    `inicios_por_pagina`: mapa `{pagina: {texto: [offsets detectados]}}` —
    quando presente, restringe a tarja às ocorrências detectadas (ver
    `occurrence_rects`); texto ausente do mapa mantém o comportamento antigo
    (tarja todos os hits).
    `rects_por_pagina`: mapa `{pagina: [(x0, y0, x1, y1), ...]}` em pontos
    PDF — tarjas por RETÂNGULO, sem texto associado (rolo de tarja manual da
    interface). Passam pelo mesmo `add_redact_annot` + `apply_redactions`
    dos textos: remoção real de conteúdo, não retângulo por cima.
    `selos_por_pagina`: mapa `{pagina: [rects]}` dos selos/imagens de
    assinatura APROVADOS pelo usuário (detectados por `detect_seal_pages`).
    `substituir_por_pagina`: mapa `{pagina: {texto: substituto}}` —
    descaracterização (hoje, de CPF): o texto original é REMOVIDO do stream
    pelo mesmo `apply_redactions` das tarjas (sem pintar retângulo) e o
    substituto (`***.456.789-**`) é escrito no lugar, com fonte ajustada ao
    retângulo. Se o espaço for pequeno demais para escrever legível (ex.:
    número quebrado em duas linhas), a área recebe tarja preta — falha
    segura, o dado nunca fica exposto.
    `achatar_tudo`: modo segurança máxima — depois das tarjas, rasteriza
    TODAS as páginas do documento (não só as com selo), removendo qualquer
    texto/objeto oculto que sobreviva por trás delas. Custo: o PDF final
    perde texto pesquisável/selecionável e fica maior.

    Sobre os selos: esta função NÃO os procura por conta própria. Antes ela
    redetectava (âncora de texto + heurística de imagem + OCR) e apagava o que
    achasse — ou seja, o PDF saía com coisas removidas que nunca apareceram na
    revisão, e o usuário não tinha como impedir. Agora ela remove EXATAMENTE os
    retângulos que recebe, nem mais nem menos: a detecção é responsabilidade de
    `detect_seal_pages`, e a decisão, do usuário.

    Aplica `apply_redactions` (PyMuPDF), que **remove o conteúdo do stream**
    do PDF — não é apenas um retângulo preto por cima. Widgets de assinatura
    sob um rect aprovado são apagados antes (o PDF os desenha por cima do
    conteúdo, então sobreviveriam à redação). Imagem INLINE sob um rect
    aprovado (o caso do selo gov.br rasterizado) não é removível por
    `apply_redactions`: essa página é achatada, com o rect pintado de preto.

    Retorna `(bytes_do_pdf, SanitizationReport)` — o relatório diz o que a
    sanitização estrutural encontrou e removeu (metadados, anexos, JS, texto
    invisível), para exibição ao usuário.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        # Texto invisível: detectado ANTES das tarjas. O relatório descreve o
        # que o ARQUIVO RECEBIDO tinha — e um texto invisível pode conter dado
        # pessoal (é o esconderijo clássico), sendo removido como tarja antes
        # de chegar ao scrub. Detectando depois, o app diria "nenhum
        # encontrado" sobre um arquivo que tinha, e o usuário perderia a
        # informação de que havia conteúdo oculto ali.
        #
        # `_has_ocr_text_layer` é heurística de PÁGINA (imagem cobrindo boa
        # parte da área) e pode disparar em documento nato digital com imagem
        # grande (timbre, capa) — por isso só decide COMO tratar o texto
        # invisível que exista de fato; quem decide SE existe é
        # `_has_invisible_text` (render mode real, via get_texttrace).
        has_invisible = _has_invisible_text(doc)
        ocr_layer = _has_ocr_text_layer(doc) if has_invisible else False

        pages_to_redact = set(textos_por_pagina.keys())
        if rects_por_pagina:
            pages_to_redact.update(rects_por_pagina.keys())
        if selos_por_pagina:
            pages_to_redact.update(selos_por_pagina.keys())
        if substituir_por_pagina:
            pages_to_redact.update(substituir_por_pagina.keys())

        achatar: dict[int, list] = {}  # páginas com imagem inline sob um selo

        for pnum in sorted(pages_to_redact):
            if pnum < 0 or pnum >= doc.page_count:
                continue
            page = doc[pnum]

            # Tarja textos
            textos = textos_por_pagina.get(pnum, [])
            for texto in textos:
                if "@" in texto:
                    # E-mail: por padrão tarja apenas a parte local (antes do
                    # @); o domínio não é dado pessoal e permanece visível.
                    # Com `redact_email_domain`, cobre o endereço inteiro.
                    _redact_email_local_part(page, texto,
                                             redact_domain=redact_email_domain)
                    continue
                inicios = None
                if inicios_por_pagina is not None:
                    inicios = inicios_por_pagina.get(pnum, {}).get(texto)
                for rect in occurrence_rects(page, texto, inicios):
                    page.add_redact_annot(rect, fill=(0, 0, 0))

            # CPFs descaracterizados: o número original sai do stream pela
            # MESMA redação real das tarjas (sem fill — o fundo da página
            # fica como está) e o retângulo é anotado para receber a forma
            # mascarada DEPOIS do apply_redactions (que apagaria qualquer
            # coisa escrita antes dele na área).
            substituicoes: list[tuple[fitz.Rect, str]] = []
            subs = (substituir_por_pagina or {}).get(pnum, {})
            for texto, novo in subs.items():
                for rect in page.search_for(texto):
                    page.add_redact_annot(rect, fill=False)
                    substituicoes.append((fitz.Rect(rect), novo))

            # Tarjas manuais (retângulos do rolo), recortadas à página para
            # um arrasto até a borda não gerar rect degenerado.
            if rects_por_pagina:
                for r in rects_por_pagina.get(pnum, []):
                    rect = fitz.Rect(r) & page.rect
                    if not rect.is_empty:
                        page.add_redact_annot(rect, fill=(0, 0, 0))

            # Selos/imagens de assinatura aprovados
            selos = [fitz.Rect(r) & page.rect
                     for r in (selos_por_pagina or {}).get(pnum, [])]
            selos = [r for r in selos if not r.is_empty]
            if selos:
                _delete_widgets_under(page, selos)
                inline = _inline_image_rects(page, selos)
                if inline:
                    achatar[pnum] = selos
                for rect in selos:
                    page.add_redact_annot(rect, fill=(0, 0, 0))

            page.apply_redactions()

            # Escreve as formas mascaradas nas áreas recém-esvaziadas. Antes
            # do achatamento (abaixo): em página achatada o texto substituto
            # vai junto no raster.
            for rect, novo in substituicoes:
                _inserir_texto_substituto(page, rect, novo)

        # Imagem inline (selo gov.br rasterizado) não sai por apply_redactions:
        # achata a página com o selo pintado de preto — remoção real, não
        # retângulo por cima de uma imagem intacta. Ordem reversa: `delete_page`
        # + `insert_pdf` invalidariam os índices seguintes.
        for pnum in sorted(achatar, reverse=True):
            _flatten_page_seals(doc, pnum, achatar[pnum])

        # Segurança máxima: as páginas com selo já saíram achatadas acima —
        # acha as demais também, sem retângulo nenhum para pintar (as tarjas
        # de texto já foram queimadas no raster por `apply_redactions`).
        if achatar_tudo:
            for pnum in range(doc.page_count - 1, -1, -1):
                if pnum not in achatar:
                    _flatten_page_seals(doc, pnum, [])

        # Inventário do que existe no arquivo ANTES da limpeza — vira o
        # relatório de sanitização mostrado ao usuário no download.
        report = SanitizationReport()
        report.metadados = any(
            v for k, v in doc.metadata.items() if k not in ("format", "encryption")
        ) or bool(doc.xref_xml_metadata())
        report.anexos = _count_attachments(doc)
        report.javascript = _has_javascript(doc)

        # Remove anotações de anexo (FileAttachment) manualmente, ANTES do
        # scrub(). Bug conhecido do PyMuPDF 1.25.5 (versão do Streamlit
        # Cloud): a implementação interna de scrub() chama
        # `annot.update_file(buffer=...)`, mas nessa versão o parâmetro do
        # binding se chama `buffer_` — `TypeError: unexpected keyword
        # argument 'buffer'`. Removendo a anotação inteira aqui (em vez de
        # só esvaziar o arquivo anexado) e desligando `attached_files` no
        # scrub evita esse caminho quebrado.
        for page in doc:
            for annot in list(page.annots(types=(fitz.PDF_ANNOT_FILE_ATTACHMENT,))):
                page.delete_annot(annot)

        # Sanitização estrutural (scrub): além das tarjas, remove do arquivo
        # tudo que pode vazar informação fora do conteúdo visível — metadados
        # (Info dict + XMP), anexos/arquivos embutidos, JavaScript, miniaturas
        # de página e valores preenchidos de formulário. `redactions=False`
        # porque as tarjas já foram aplicadas página a página acima.
        #
        # `hidden_text` (texto com render mode 3, invisível) só é removido
        # quando o documento NÃO é escaneado+OCR: nesses, a camada invisível
        # é o próprio texto selecionável/pesquisável, e removê-la deixaria o
        # PDF final sem seleção nem busca. `has_invisible`/`ocr_layer` foram
        # medidos lá em cima, no arquivo COMO RECEBIDO (ver comentário lá).
        doc.scrub(
            attached_files=False,  # já removidas manualmente acima
            embedded_files=True,
            javascript=True,
            thumbnails=True,
            reset_fields=True,
            reset_responses=True,
            metadata=True,
            xml_metadata=True,
            hidden_text=not ocr_layer,
            clean_pages=True,
            remove_links=False,  # links legítimos (ex.: validar.iti.gov.br) ficam
            redactions=False,
        )
        # O scrub esvazia o CÓDIGO do JavaScript (`/JS ()`), mas deixa as ações
        # para trás: o PDF continua declarando `/S /JavaScript` no OpenAction e
        # na name tree. Ninguém executa nada (o script é vazio), mas o arquivo
        # ainda "tem JavaScript" para qualquer ferramenta que olhe — inclusive
        # a nossa. Numa ferramenta que promete remoção, isso é meia-verdade.
        # Aqui as ações somem de fato, e o `garbage=4` do save leva os objetos.
        _strip_javascript(doc)

        # Texto invisível: removido por nós, não pelo scrub (que é no-op nisso
        # — ver `_remove_hidden_text`). Só quando NÃO é camada de OCR: ali o
        # texto invisível é o próprio conteúdo pesquisável do documento
        # escaneado, e apagá-lo deixaria o PDF sem busca nem seleção.
        if has_invisible and not ocr_layer:
            _remove_hidden_text(doc)

        if not has_invisible:
            report.texto_invisivel = "nenhum"
        elif ocr_layer:
            report.texto_invisivel = "preservado_ocr"
        else:
            # Confere no arquivo final (mesma detecção por render mode) em vez
            # de confiar que o scrub fez o serviço — "removido" só se sobrou
            # nada mesmo.
            report.texto_invisivel = ("nenhum" if _has_invisible_text(doc)
                                      else "removido")

        out = BytesIO()
        doc.save(
            out,
            garbage=4,   # remove objetos órfãos
            deflate=True,
            clean=True,  # sanitiza streams
        )
        return out.getvalue(), report
    finally:
        doc.close()


def _tokeniza_conteudo(data: bytes) -> list[tuple[int, int, str]]:
    """Quebra um content stream de PDF em tokens `(início, fim, tipo)`.

    Tipos: `num`, `str` (literal ou hex), `arr`, `name`, `dict`, `op`. Basta
    para achar os operadores de texto e seus operandos — não é um parser de PDF
    completo, e não precisa ser.
    """
    toks: list[tuple[int, int, str]] = []
    i, n = 0, len(data)
    DELIM = b" \t\r\n/[]<>(){}%"
    while i < n:
        c = data[i:i + 1]
        if c in b" \t\r\n":
            i += 1
            continue
        if c == b"%":                                    # comentário
            j = data.find(b"\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == b"(":                                    # string literal
            j, prof = i + 1, 1
            while j < n and prof:
                ch = data[j:j + 1]
                if ch == b"\\":
                    j += 2
                    continue
                if ch == b"(":
                    prof += 1
                elif ch == b")":
                    prof -= 1
                j += 1
            toks.append((i, j, "str"))
            i = j
            continue
        if c == b"<":
            if data[i + 1:i + 2] == b"<":                # dicionário
                j, prof = i, 0
                while j < n:
                    if data[j:j + 2] == b"<<":
                        prof += 1
                        j += 2
                        continue
                    if data[j:j + 2] == b">>":
                        prof -= 1
                        j += 2
                        if prof == 0:
                            break
                        continue
                    j += 1
                toks.append((i, j, "dict"))
                i = j
                continue
            j = data.find(b">", i)                       # string hex
            j = n if j < 0 else j + 1
            toks.append((i, j, "str"))
            i = j
            continue
        if c == b"[":                                    # array (pode ter string)
            j, prof = i + 1, 1
            while j < n and prof:
                ch = data[j:j + 1]
                if ch == b"(":
                    k, p2 = j + 1, 1
                    while k < n and p2:
                        cc = data[k:k + 1]
                        if cc == b"\\":
                            k += 2
                            continue
                        if cc == b"(":
                            p2 += 1
                        elif cc == b")":
                            p2 -= 1
                        k += 1
                    j = k
                    continue
                if ch == b"[":
                    prof += 1
                elif ch == b"]":
                    prof -= 1
                j += 1
            toks.append((i, j, "arr"))
            i = j
            continue
        if c == b"/":                                    # nome
            j = i + 1
            while j < n and data[j:j + 1] not in DELIM:
                j += 1
            toks.append((i, j, "name"))
            i = j
            continue
        j = i                                            # número ou operador
        while j < n and data[j:j + 1] not in DELIM:
            j += 1
        if j == i:
            j = i + 1
        tok = data[i:j]
        tipo = "num" if re.fullmatch(rb"[+-]?[0-9.]+", tok) else "op"
        toks.append((i, j, tipo))
        i = j
    return toks


# Modos de renderização de texto que NÃO pintam glifo nenhum: 3 (invisível) e
# 7 (só entra no clip). É neles que se esconde texto num PDF.
_TR_INVISIVEIS = (3, 7)
# Operadores que mostram texto, e quantos operandos cada um consome antes de si.
_OPS_TEXTO = {b"Tj": 1, b"TJ": 1, b"'": 1, b'"': 3}


def _filtra_texto_invisivel(data: bytes) -> tuple[bytes, bool]:
    """Devolve o content stream sem os operadores de texto que rodam em modo
    invisível — e se algo foi de fato removido.

    Acompanha o modo corrente (`N Tr`) respeitando a pilha `q`/`Q`: o modo faz
    parte do estado gráfico, então um `Q` restaura o que valia antes.
    """
    toks = _tokeniza_conteudo(data)
    cortes: list[tuple[int, int]] = []
    modo = 0
    pilha: list[int] = []

    for idx, (ini, fim, tipo) in enumerate(toks):
        if tipo != "op":
            continue
        op = data[ini:fim]
        if op == b"q":
            pilha.append(modo)
        elif op == b"Q":
            modo = pilha.pop() if pilha else 0
        elif op == b"Tr" and idx and toks[idx - 1][2] == "num":
            p_ini, p_fim, _ = toks[idx - 1]
            try:
                modo = int(float(data[p_ini:p_fim]))
            except ValueError:
                pass
        elif op in _OPS_TEXTO and modo in _TR_INVISIVEIS:
            # remove o operador E os operandos que o alimentam
            n_operandos = _OPS_TEXTO[op]
            corte_ini, j, achados = ini, idx - 1, 0
            while j >= 0 and achados < n_operandos:
                corte_ini = toks[j][0]
                achados += 1
                j -= 1
            cortes.append((corte_ini, fim))

    if not cortes:
        return data, False
    saida = bytearray()
    anterior = 0
    for ini, fim in cortes:
        saida += data[anterior:ini]
        anterior = fim
    saida += data[anterior:]
    return bytes(saida), True


def _remove_hidden_text(doc) -> None:
    """Remove o texto invisível (render mode 3/7) de todas as páginas.

    NÃO usa o `scrub(hidden_text=True)` do PyMuPDF: ele é NO-OP nas versões
    atuais. A implementação dele compara cada linha do content stream com a
    string exata `b"3 Tr"`, contando que `clean_contents()` produza um operador
    por linha — mas o `clean_contents()` de hoje devolve o stream inteiro numa
    linha só, e a comparação nunca casa. O app dependia disso em silêncio: o
    texto oculto ficava no arquivo e o relatório dizia "nenhum encontrado",
    que é o pior desfecho possível — garantia falsa sobre dado que continua lá.

    Aqui a varredura é por TOKEN, então independe de formatação do stream.
    """
    for page in doc:
        try:
            page.clean_contents()  # consolida num único /Contents
            xrefs = page.get_contents()
            if not xrefs:
                continue
            dados = page.read_contents()
            novo, mudou = _filtra_texto_invisivel(dados)
            if mudou:
                doc.update_stream(xrefs[0], novo)
        except Exception:
            continue


def _acao_e_javascript(doc, valor) -> bool:
    """A ação apontada por `valor` (par `(tipo, texto)` de `xref_get_key`) é
    JavaScript? Referência indireta é resolvida; ação de outro tipo (ir para
    página, abrir URL) devolve False — só o JS é alvo."""
    if not valor:
        return False
    tipo, texto = valor
    if tipo == "xref":
        try:
            texto = doc.xref_object(int(texto.split()[0]))
        except Exception:
            return False
    return "/JavaScript" in (texto or "")


def _strip_javascript(doc) -> None:
    """Remove as AÇÕES JavaScript que o `scrub` deixa para trás.

    O scrub zera o corpo do script (`/JS ()`) mas mantém a ação declarada —
    o arquivo continua anunciando `/S /JavaScript`. Aqui as referências são
    cortadas; os objetos viram órfãos e o `garbage=4` do save os elimina.

    Cobre os três lugares em que o JS mora num PDF real (formulário de órgão
    público costuma ter os três): a name tree `/Names /JavaScript`, a
    `/OpenAction` do catálogo, e as ações `/A`/`/AA` de campos e anotações.
    Ações que NÃO são JavaScript (ir para página, abrir link legítimo) ficam.
    """
    cat = doc.pdf_catalog()

    # 1. name tree do catálogo — some inteira (só guarda JS mesmo)
    doc.xref_set_key(cat, "Names/JavaScript", "null")

    # 2. ação de abertura — só se for JavaScript
    if _acao_e_javascript(doc, doc.xref_get_key(cat, "OpenAction")):
        doc.xref_set_key(cat, "OpenAction", "null")

    # 3. ações de campos de formulário e anotações (/A = ao clicar,
    #    /AA = gatilhos adicionais: ao editar, ao formatar, ao validar...)
    for page in doc:
        for annot in page.annots():
            for chave in ("A", "AA"):
                try:
                    if _acao_e_javascript(doc, doc.xref_get_key(annot.xref, chave)):
                        doc.xref_set_key(annot.xref, chave, "null")
                    elif chave == "AA":
                        # /AA é um dicionário de gatilhos; qualquer um pode ser
                        # JS mesmo que o dicionário em si não pareça.
                        _limpa_gatilhos_aa(doc, annot.xref)
                except Exception:
                    continue

    # 4. Os objetos de ação em si. Desligá-los do catálogo os torna órfãos, mas
    #    o `garbage=4` do save não os leva de forma confiável — e um objeto
    #    órfão continua NO ARQUIVO, ainda declarando `/S /JavaScript` para
    #    quem inspecionar. Zerar o objeto resolve na fonte, sem depender do
    #    coletor: sobra um dicionário vazio, que não é ação de nada.
    for xref in range(1, doc.xref_length()):
        try:
            tipo, val = doc.xref_get_key(xref, "S")
        except Exception:
            continue
        if tipo == "name" and val.lstrip("/") == "JavaScript":
            try:
                doc.update_object(xref, "<<>>")
            except Exception:
                continue


def _limpa_gatilhos_aa(doc, xref) -> None:
    """Zera os gatilhos JavaScript dentro do dicionário `/AA` de um campo."""
    for gatilho in ("K", "F", "V", "C", "D", "U", "Fo", "Bl"):
        chave = f"AA/{gatilho}"
        try:
            if _acao_e_javascript(doc, doc.xref_get_key(xref, chave)):
                doc.xref_set_key(xref, chave, "null")
        except Exception:
            continue


def _count_attachments(doc) -> int:
    """Arquivos carregados dentro do PDF: embutidos + anotações de anexo."""
    n = doc.embfile_count()
    for page in doc:
        try:
            n += sum(1 for _ in page.annots(types=(fitz.PDF_ANNOT_FILE_ATTACHMENT,)))
        except Exception:
            pass
    return n


def _has_javascript(doc) -> bool:
    """O PDF contém alguma ação JavaScript?

    Varre os objetos procurando `/S /JavaScript` — pega tanto a name tree
    `/Names /JavaScript` do catálogo quanto ações soltas (OpenAction,
    anotações, campos de formulário).
    """
    for xref in range(1, doc.xref_length()):
        try:
            t, val = doc.xref_get_key(xref, "S")
        except Exception:
            continue
        if t == "name" and val.lstrip("/") == "JavaScript":
            return True
    return False


def _has_invisible_text(doc) -> bool:
    """Existe texto com render mode invisível (Tr 3) em alguma página?

    `get_text()` não distingue texto visível de invisível na contagem de
    caracteres; `get_texttrace()` expõe o render mode real por span
    (`type == 3` é exatamente o modo "invisível" do operador Tr do PDF).
    Usado para não confundir "página parece um scan" (heurística de imagem
    grande) com "existe de fato texto invisível" — são perguntas diferentes.
    """
    for page in doc:
        try:
            trace = page.get_texttrace()
        except Exception:
            continue
        if any(span.get("type") == 3 for span in trace):
            return True
    return False


def _has_ocr_text_layer(doc) -> bool:
    """Heurística: o documento é escaneado com camada de OCR?

    Página com texto extraível E uma imagem cobrindo ≥70% da área é o padrão
    de scanner+OCR (imagem visível por cima, texto invisível por baixo).
    Basta uma página assim para preservar o texto invisível do documento
    inteiro — falso positivo aqui só deixa de remover texto oculto (perda
    menor); falso negativo tiraria a busca/seleção de um PDF escaneado.
    Páginas achatadas por `_flatten_page_seals` não disparam (viram imagem
    sem camada de texto).
    """
    for page in doc:
        if not page.get_text().strip():
            continue
        try:
            infos = page.get_image_info()
        except Exception:
            continue
        page_area = abs(page.rect)
        if page_area <= 0:
            continue
        for info in infos:
            r = fitz.Rect(info["bbox"])
            if not r.is_empty and not r.is_infinite and abs(r) >= page_area * 0.7:
                return True
    return False


def _inserir_texto_substituto(page, rect, texto: str) -> None:
    """Escreve `texto` dentro de `rect` (área que acabou de ser esvaziada pela
    redação), com a fonte ajustada para caber em largura e altura.

    Fallback seguro: se o retângulo for pequeno/estreito demais para o texto
    sair legível (ex.: CPF quebrado em duas linhas, que gera dois retângulos
    parciais), a área é pintada de preto — vira tarja comum, nunca fica um
    vão em branco no lugar de um dado que deveria estar tratado.
    """
    fontname = "helv"
    largura_1pt = fitz.get_text_length(texto, fontname=fontname, fontsize=1)
    fontsize = min(rect.height * 0.8, rect.width / max(largura_1pt, 1e-6))
    while fontsize >= 3.0:
        rc = page.insert_textbox(rect, texto, fontname=fontname,
                                 fontsize=fontsize, color=(0, 0, 0),
                                 align=fitz.TEXT_ALIGN_CENTER)
        if rc >= 0:  # coube (sobra vertical não negativa)
            return
        fontsize *= 0.85
    page.draw_rect(rect, color=None, fill=(0, 0, 0))


# ---------------------------------------------------------------------------
# E-mail: tarja apenas a parte local (antes do @)
# ---------------------------------------------------------------------------
def _redact_email_local_part(page, email: str, redact_domain: bool = False) -> None:
    """Tarja o e-mail, por padrão só a parte local (antes do @).

    O domínio (após o @) não é informação pessoal, então fica visível.
    Localiza cada ocorrência do e-mail completo e, dentro daquele retângulo,
    procura a parte local para cobrir apenas ela. Se não conseguir isolar
    (ex.: quebra de linha, espaçamento atípico), tarja o e-mail inteiro —
    falha segura, nunca deixa o endereço completo exposto.

    Com `redact_domain=True`, cobre o endereço inteiro (incluindo o domínio).
    """
    if redact_domain:
        for full in page.search_for(email):
            page.add_redact_annot(full, fill=(0, 0, 0))
        return
    local = email.split("@", 1)[0]
    for full in page.search_for(email):
        alvos = page.search_for(local, clip=full + (-1, -1, 1, 1)) if local else []
        if not alvos:
            alvos = [full]  # falha segura: cobre o e-mail inteiro
        for rect in alvos:
            page.add_redact_annot(rect, fill=(0, 0, 0))


# ---------------------------------------------------------------------------
# Selos RASTERIZADOS (imagem inline/embutida) — removidos via OCR + flatten
# ---------------------------------------------------------------------------
# Texto que o OCR deve encontrar SOBREPOSTO à imagem candidata para confirmá-la
# como carimbo de assinatura (não basta estar em qualquer lugar da página — a
# intersecção com o bbox da imagem é que evita falso positivo em imagens
# legítimas). Cobre gov.br/ICP-Brasil e outras plataformas de assinatura
# municipal (ex.: IPM) que usam o padrão "Assinado por NOME em DATA.".
_SEAL_OCR_ANCHORS = ("assinado digitalmente", "validar.iti", "Verifique em", "Assinado por")

# Locais comuns do tessdata (Streamlit Cloud/Debian instala via packages.txt).
_TESSDATA_CANDIDATES = (
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/usr/share/tesseract-ocr/tessdata",
    "/usr/share/tessdata",
    r"C:\Program Files\Tesseract-OCR\tessdata",
)


def _ensure_tessdata() -> bool:
    """Garante TESSDATA_PREFIX apontando para um tessdata existente."""
    prefix = os.environ.get("TESSDATA_PREFIX")
    if prefix and os.path.isdir(prefix):
        return True
    import glob
    cands = list(_TESSDATA_CANDIDATES) + sorted(
        glob.glob("/usr/share/tesseract-ocr/*/tessdata"), reverse=True
    )
    for cand in cands:
        if os.path.isdir(cand):
            os.environ["TESSDATA_PREFIX"] = cand
            return True
    return False


def _page_ocr_textpage(page, dpi: int = 300):
    """TextPage via OCR da página (ou None se Tesseract indisponível)."""
    if not _ensure_tessdata():
        return None
    for lang in ("por", "eng"):
        try:
            return page.get_textpage_ocr(flags=0, language=lang, dpi=dpi, full=True)
        except Exception:
            continue
    return None


def _detect_seal_rects(page) -> list:
    """Localiza selos gov.br rasterizados na página via OCR.

    Só marca uma imagem como selo se o OCR encontrar texto-âncora do gov.br
    sobre ela (evita tarjar imagens legítimas — fotos, gráficos, brasões).
    Retorna lista de `fitz.Rect` (coordenadas da página).
    """
    # Imagens candidatas: pequenas (não a de página inteira), tamanho de selo.
    candidates = []
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        return []
    for info in infos:
        r = fitz.Rect(info["bbox"])
        if r.is_empty or r.is_infinite:
            continue
        if r.width >= page.rect.width * 0.7:  # imagem de página inteira
            continue
        if 25 < r.width < 340 and 10 < r.height < 170:
            candidates.append(r)
    if not candidates:
        return []

    tp = _page_ocr_textpage(page)
    if tp is None:
        return []
    anchors: list = []
    for a in _SEAL_OCR_ANCHORS:
        anchors.extend(page.search_for(a, textpage=tp))
    if not anchors:
        return []

    seals = []
    for c in candidates:
        c_exp = c + (-3, -3, 3, 3)
        if any(c_exp.intersects(ar) for ar in anchors):
            seals.append(c)
    return seals


def _flatten_page_seals(doc, pno: int, seal_rects: list, dpi: int = 200) -> None:
    """Rasteriza a página com os selos pintados de preto e a substitui.

    Como o texto já foi tarjado antes, o raster resultante contém as tarjas de
    texto + os selos cobertos. O selo deixa de existir como dado recuperável
    (não é retângulo por cima de imagem intacta).
    """
    page = doc[pno]
    w, h = page.rect.width, page.rect.height
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat)
    for r in seal_rects:
        ir = (r * mat).round()
        pix.set_rect(ir, (0, 0, 0))
    tmp = fitz.open()
    try:
        newpage = tmp.new_page(width=w, height=h)
        newpage.insert_image(newpage.rect, pixmap=pix)
        doc.delete_page(pno)
        doc.insert_pdf(tmp, start_at=pno)
    finally:
        tmp.close()


def _inline_image_rects(page, alvos: list) -> list:
    """Imagens INLINE da página que ficam sob algum dos `alvos`.

    Imagem inline mora dentro do content stream (xref 0), não como objeto
    próprio — `apply_redactions` não consegue removê-la, e a área "tarjada"
    continuaria contendo a imagem original recuperável. É o caso do selo gov.br
    rasterizado. Achar alguma aqui é o gatilho para achatar a página.
    """
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        return []
    out = []
    for info in infos:
        if info.get("xref", 0) != 0:  # 0 = inline
            continue
        r = fitz.Rect(info["bbox"])
        if any(r.intersects(a) for a in alvos):
            out.append(r)
    return out


def _delete_widgets_under(page, alvos: list) -> None:
    """Apaga os widgets (campos de formulário/assinatura) sob os `alvos`.

    O PDF desenha widgets POR CIMA do conteúdo: sem apagá-los, o selo continua
    visível mesmo com o conteúdo tarjado embaixo. Re-busca a cada apagada em vez
    de iterar uma lista fixa: no pymupdf 1.25 (produção) apagar um widget
    invalida os objetos Widget já obtidos, e apagar um stale falha em silêncio —
    era assim que o 2º selo de um documento escapava. O limite evita laço
    infinito caso alguma remoção não pegue.
    """
    for _ in range(len(alvos) + 4):
        alvo = next(
            (w for w in page.widgets()
             if any(fitz.Rect(w.rect).intersects(a) for a in alvos)),
            None,
        )
        if alvo is None:
            return
        try:
            page.delete_widget(alvo)
        except Exception:
            return


# Âncoras de texto do selo gov.br / ICP-Brasil (Assinador ITI)
_SEAL_TOP_ANCHOR = "Documento assinado digitalmente"
_SEAL_BOTTOM_ANCHORS = ("validar.iti.gov.br", "Verifique em")


def _govbr_seal_rects_on_page(page, pad: float = 3.0) -> list:
    """Retângulos (bloco inteiro) dos selos gov.br / ICP-Brasil na página.

    Estratégia por âncora de texto (robusta, independe de tamanho de imagem):
    localiza `"Documento assinado digitalmente"` (topo) e `"validar.iti.gov.br"`
    / `"Verifique em"` (base). Entre essas linhas está o selo completo — nome do
    signatário, "Nome civil", data e URL. Une todas as palavras da faixa vertical
    mais o logo gov.br (imagem/desenho à esquerda) num retângulo por selo.

    Não altera a página — só calcula a geometria. Alimenta `detect_seal_pages`;
    o retângulo daqui é o MESMO que a exportação vai remover, então prévia e
    tarja coincidem por construção.
    """
    tops = list(page.search_for(_SEAL_TOP_ANCHOR))
    if not tops:
        return []
    bottoms: list = []
    for anchor in _SEAL_BOTTOM_ANCHORS:
        bottoms.extend(page.search_for(anchor))
    if not bottoms:
        return []

    words = page.get_text("words")  # (x0, y0, x1, y1, palavra, bloco, linha, n)
    extras: list = []  # logo (imagem) + desenhos que compõem o selo
    for img in page.get_images(full=True):
        try:
            extras.extend(page.get_image_rects(img[0]))
        except Exception:
            pass
    try:
        extras.extend(d["rect"] for d in page.get_drawings())
    except Exception:
        pass

    page_w = page.rect.width
    rects: list = []
    for top in sorted(tops, key=lambda r: r.y0):
        # âncora inferior mais próxima abaixo do topo, na mesma coluna
        cand = [b for b in bottoms if b.y0 > top.y0 - 1 and abs(b.x0 - top.x0) < 120]
        if not cand:
            continue
        bottom = min(cand, key=lambda b: b.y0 - top.y0)
        y0, y1 = top.y0 - pad, bottom.y1 + pad

        rect = fitz.Rect(top)
        for w in words:
            wy = (w[1] + w[3]) / 2
            if y0 <= wy <= y1:
                rect |= fitz.Rect(w[0], w[1], w[2], w[3])
        for r in extras:
            # ignora fora da faixa, linhas finas (divisórias) e barras full-width
            if r.y1 < y0 or r.y0 > y1:
                continue
            if r.height < 3 or r.width < 3:
                continue
            if r.width > page_w * 0.6:
                continue
            rect |= r

        rect += (-pad, -pad, pad, pad)
        rects.append(rect)

    return rects


def _signature_image_rects(page) -> list:
    """Imagens/objetos com cara de assinatura ou carimbo na página (heurística).

    NÃO altera a página — só calcula geometria. Esta varredura vivia DENTRO da
    exportação (`_redact_signature_images_on_page`, agora removida): o PDF saía
    com essas imagens apagadas sem que elas jamais tivessem aparecido na
    revisão. Agora ela alimenta a detecção, vira o tipo `IMAGE_ENTITY_TIPO` na
    tabela/visualizador, e a exportação remove apenas o que ficou marcado.

    Cobre três tipos de elemento, todos filtrados por posição (60% inferior da
    página, onde ficam as assinaturas) e por tamanho típico de selo/carimbo:
    1. Imagens raster (PNG/JPEG) — assinatura manuscrita escaneada, selo-imagem
    2. Form XObjects (vetoriais) — muitos selos são XObject com logo + texto
    3. Anotações de assinatura (widget/stamp)
    """
    half_y = page.rect.height * 0.4  # 60% inferior da página
    achados: list = []

    def _ja_coberto(rect) -> bool:
        return any(abs(r.x0 - rect.x0) < 5 and abs(r.y0 - rect.y0) < 5
                   for r in achados)

    def _tamanho_de_selo(rect) -> bool:
        """Tamanho típico de selo: 60–450 pt largura, 30–280 pt altura."""
        w, h = rect.width, rect.height
        return 60 <= w <= 450 and 30 <= h <= 280 and w * h <= 80000

    def _considera(bbox) -> None:
        if bbox is None:
            return
        r = fitz.Rect(bbox)
        if r.is_empty or r.is_infinite:
            return
        if r.y0 < half_y or not _tamanho_de_selo(r) or _ja_coberto(r):
            return
        achados.append(r)

    # 1. Imagens raster
    try:
        for img_info in page.get_images(full=True):
            for rect in page.get_image_rects(img_info[0]):
                _considera(rect)
    except Exception:
        pass

    # 2. Form XObjects (vetoriais)
    try:
        for xobj in page.get_xobjects():
            # xobj = (xref, name, invoker, bbox_on_page)
            if len(xobj) >= 4:
                _considera(xobj[3])
    except Exception:
        pass  # get_xobjects pode não estar disponível em todas as versões

    # 3. Anotações de assinatura (widget/stamp)
    try:
        for annot in page.annots():
            if annot.type[0] in (2, 19, 20):  # Stamp, Widget, FileAttachment
                _considera(annot.rect)
    except Exception:
        pass

    return achados


def _signature_widget_rects(page) -> list:
    """Selos que são campo de assinatura (widget `Signature`) na página.

    Detectados por GEOMETRIA — não dependem de OCR, ao contrário de
    `_detect_seal_rects`. Muitos selos gov.br/ICP-Brasil (Assinador ITI) são
    widgets de assinatura cujo texto é imagem: `search_for` não acha (sem
    âncora de texto) e o OCR pode falhar num selo e acertar noutro, então a
    prévia mostrava só parte dos selos. Como `page.widgets()` lista todos de
    forma determinística, aqui garantimos que a prévia veja todo selo que a
    exportação vai tarjar. Filtra por tamanho típico de selo pra não pegar
    campos de assinatura vazios gigantes/minúsculos.
    """
    out: list = []
    try:
        widgets = list(page.widgets())
    except Exception:
        return out
    for w in widgets:
        try:
            if w.field_type_string != "Signature":
                continue
        except Exception:
            continue
        r = fitz.Rect(w.rect)
        if r.is_empty or r.is_infinite:
            continue
        if 25 < r.width < 400 and 10 < r.height < 200:
            out.append(r)
    return out


def detect_seal_pages(pdf_bytes: bytes,
                      on_progress: Callable[[int, int], None] | None = None
                      ) -> list[tuple[int, tuple[float, float, float, float], str]]:
    """Detecta (sem alterar o PDF) os selos de assinatura e as imagens com cara
    de assinatura/carimbo do documento.

    Retorna `(pagina_0indexed, (x0, y0, x1, y1), tipo)`, com `tipo` sendo
    `SEAL_ENTITY_TIPO` ou `IMAGE_ENTITY_TIPO` — o rect permite ao visualizador
    realçar/marcar o item, e a exportação remove EXATAMENTE esses retângulos
    (os que o usuário mantiver marcados), nunca mais do que isso.

    Selo (confiança alta — confirmado por estrutura ou texto):
    1. campo de assinatura (widget), por geometria, determinístico
    2. selo em texto/vetor, pela âncora `_SEAL_TOP_ANCHOR`
    3. selo rasterizado, confirmado por OCR (`_detect_seal_rects`) — só roda se
       1 e 2 não acharam nada, porque OCR é caro e pode falhar num selo e não
       noutro; assim todo selo-widget aparece de forma determinística.

    Imagem (confiança menor — só heurística de posição/tamanho): tudo o que
    `_signature_image_rects` acha e que NÃO coincide com um selo já achado
    acima. É aqui que entra a assinatura manuscrita escaneada.
    """
    def _novo(rect, existentes) -> bool:
        r = fitz.Rect(rect)
        return not any(r.intersects(fitz.Rect(e)) for e in existentes)

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        achados: list[tuple[int, tuple[float, float, float, float], str]] = []
        for pno in range(doc.page_count):
            if on_progress is not None:
                on_progress(pno + 1, doc.page_count)
            page = doc[pno]

            selos = _signature_widget_rects(page)
            for tr in _govbr_seal_rects_on_page(page):
                if _novo(tr, selos):
                    selos.append(tr)
            if not selos:
                selos = _detect_seal_rects(page)

            # Imagens que não são um selo já identificado (a mesma imagem não
            # deve virar dois itens na tabela).
            imagens = [r for r in _signature_image_rects(page)
                       if _novo(r, selos)]

            achados.extend((pno, tuple(r), SEAL_ENTITY_TIPO) for r in selos)
            achados.extend((pno, tuple(r), IMAGE_ENTITY_TIPO) for r in imagens)
        return achados
    finally:
        doc.close()
