"""Orquestração do fluxo: validar → detectar → aplicar tarjas."""
from __future__ import annotations

from typing import Callable

from .detector import EntityGroup, Occurrence, detect_entities
from .redactor import (IMAGE_ENTITY_TIPO, SEAL_ENTITY_TIPO, SanitizationReport,
                       cpf_descaracterizado, detect_seal_pages, redact_pdf)
from .validator import validate_pdf


def analyze(pdf_bytes: bytes, tipos_display: list[str], max_size_mb: int = 10,
            detectar_selos: bool = True, usar_ner: bool = False,
            usar_ner_bert: bool = False, sempre_heuristicas: bool = False,
            tarjar_nomes_assinatura: bool = True,
            on_progress: Callable[[float, str], None] | None = None,
            on_warning: Callable[[str], None] | None = None,
            on_status: Callable[[str], None] | None = None):
    """`on_progress(fracao, rotulo)`: fracao 0.0-1.0, chamado página a página
    durante a detecção — reflete o processamento real (não é animação), então
    dá pra plugar direto num `st.progress`. A detecção de entidades ocupa
    70% da barra quando os selos também são detectados (0%-100% senão); a
    verificação de selos, os 30% finais.

    `on_warning(mensagem)`: avisos não fatais para exibir ao usuário — hoje,
    a IA de nomes ter sido desligada por falha ao carregar o modelo (a
    detecção continua, com heurísticas no lugar).
    """
    info = validate_pdf(pdf_bytes, max_size_mb=max_size_mb)

    peso_entidades = 0.7 if detectar_selos else 1.0

    def _prog_entidades(pagina_atual, total_paginas):
        if on_progress is not None:
            frac = (pagina_atual / total_paginas) * peso_entidades
            on_progress(frac, f"Analisando o documento — página {pagina_atual}/{total_paginas}")

    grupos = detect_entities(pdf_bytes, tipos_display, usar_ner=usar_ner,
                             usar_ner_bert=usar_ner_bert,
                             sempre_heuristicas=sempre_heuristicas,
                             tarjar_nomes_assinatura=tarjar_nomes_assinatura,
                             on_progress=_prog_entidades if on_progress else None,
                             on_warning=on_warning,
                             on_status=on_status)

    if detectar_selos:
        def _prog_selos(pagina_atual, total_paginas):
            if on_progress is not None:
                frac = peso_entidades + (pagina_atual / total_paginas) * (1.0 - peso_entidades)
                on_progress(frac, f"Verificando selos de assinatura — página {pagina_atual}/{total_paginas}")

        grupos.extend(analyze_seals(
            pdf_bytes, on_progress=_prog_selos if on_progress else None))

    return info, grupos


def analyze_seals(pdf_bytes: bytes,
                  on_progress: Callable[[int, int], None] | None = None
                  ) -> list[EntityGroup]:
    """Detecta selos de assinatura e imagens, e devolve os EntityGroups
    correspondentes (lista vazia se não houver nada).

    Dois tipos, com níveis de confiança e granularidades diferentes:

    - `SEAL_ENTITY_TIPO`: selo confirmado (widget de assinatura, âncora de texto
      do gov.br/ICP-Brasil, ou selo rasterizado confirmado por OCR). Todos os
      selos vão num grupo só: são o mesmo carimbo repetido, e faz pouco sentido
      tarjar um e deixar outro.

    - `IMAGE_ENTITY_TIPO`: **um grupo por imagem** ("Imagem 1", "Imagem 2"...),
      numeradas na ordem de leitura do documento (página, depois posição
      vertical). Aqui a granularidade importa: a heurística enxerga posição e
      tamanho, não conteúdo — as imagens que ela pega podem ser uma assinatura
      manuscrita, um carimbo, um logotipo ou um brasão, e o usuário precisa
      poder tarjar uma e preservar outra. Vêm pré-marcadas, como o selo.

    Separado de `analyze` porque é o passo CARO (Tesseract página a página) e
    porque seu resultado não depende de nenhuma outra opção de detecção: só do
    arquivo. Isso deixa a interface cachear os selos por conta própria e
    ligar/desligar a opção sem reanalisar o documento inteiro.

    `on_progress(pagina_atual, total_paginas)`: progresso do OCR.
    """
    achados = detect_seal_pages(pdf_bytes, on_progress=on_progress)
    if not achados:
        return []

    grupos: list[EntityGroup] = []

    selos = [(pag, rect) for pag, rect, tipo in achados
             if tipo == SEAL_ENTITY_TIPO]
    if selos:
        grupos.append(EntityGroup(
            tipo=SEAL_ENTITY_TIPO,
            texto="carimbo de assinatura digital",
            score=1.0,
            ocorrencias=[
                Occurrence(pagina=pag, inicio=0, fim=0, texto="", score=1.0,
                           rect=rect)
                for pag, rect in selos
            ],
            origem="Selo (estrutura/OCR)",
        ))

    # Ordem de leitura: página, depois topo→base, depois esquerda→direita. É a
    # mesma ordem em que o visualizador desenha as caixas, então "Imagem 2" na
    # tabela é a segunda imagem que o usuário encontra descendo o documento.
    imagens = sorted(
        ((pag, rect) for pag, rect, tipo in achados
         if tipo == IMAGE_ENTITY_TIPO),
        key=lambda pr: (pr[0], pr[1][1], pr[1][0]),
    )
    for i, (pag, rect) in enumerate(imagens, start=1):
        grupos.append(EntityGroup(
            tipo=IMAGE_ENTITY_TIPO,
            texto=f"Imagem {i}",
            score=1.0,
            ocorrencias=[
                Occurrence(pagina=pag, inicio=0, fim=0, texto="", score=1.0,
                           rect=rect)
            ],
            origem="Imagem (posição/tamanho)",
        ))

    return grupos


def apply_redactions(pdf_bytes: bytes, aprovados: list[EntityGroup],
                     redact_email_domain: bool = False,
                     tarjar_nomes_assinatura: bool = True,
                     manual_rects: list[tuple] | None = None,
                     achatar_tudo: bool = False,
                     descaracterizar_cpf: bool = False,
                     ) -> tuple[bytes, int, SanitizationReport]:
    """Aplica tarjas apenas nos grupos aprovados pelo usuário — e SOMENTE
    neles: selos e imagens de assinatura são removidos pelos retângulos que
    vieram na detecção, não por uma busca própria da exportação.

    `redact_email_domain`: se True, a tarja de e-mail cobre o endereço inteiro
    (incluindo o domínio); se False (padrão), cobre só a parte antes do `@`.

    `manual_rects`: tarjas por retângulo do rolo manual da interface —
    `[(pagina_0indexed, x0, y0, x1, y1), ...]` em pontos PDF. Cada uma conta
    como uma ocorrência no total retornado.

    `achatar_tudo`: modo segurança máxima — rasteriza TODAS as páginas do
    PDF final, não só as que têm tarja. Ver `redact_pdf`.

    `descaracterizar_cpf`: quando True, os grupos de tipo CPF não recebem
    tarja preta — o número original é removido do stream (mesma redação
    real) e a forma descaracterizada (`***.456.789-**`) é escrita no lugar.
    Só vale para CPF em TEXTO: selos e imagens continuam tarjados, e um CPF
    que a página achatada carrega em raster já saiu tratado antes do
    achatamento.

    `tarjar_nomes_assinatura`: quando False, um Nome MISTO (aparece no corpo E
    em bloco de assinatura) é tarjado só nas ocorrências de corpo — a
    assinatura permanece visível. Grupos 100% de assinatura que o usuário
    marcou explicitamente tarjam tudo (a marcação manual é a intenção).

    Com `aprovados` vazio, nenhuma tarja é aplicada, mas a sanitização
    estrutural (metadados, anexos, JavaScript, texto invisível) roda mesmo
    assim — o usuário pode querer só as limpezas.

    Retorna (bytes_do_pdf, total_de_ocorrencias_tarjadas, SanitizationReport).
    """
    textos_por_pagina: dict[int, list[str]] = {}
    # Offsets das ocorrências detectadas, por página/texto — preenchido só
    # quando a tarja deve mirar posições específicas em vez de todos os hits
    # do texto: (a) RG, cuja mesma string pode ser outra coisa na página
    # (protocolo/valor sem contexto); (b) Nome misto com a flag de assinatura
    # desligada (tarja no corpo, preserva na assinatura). Nos demais casos,
    # texto igual = mesma informação e tarjar todos os hits é o desejado —
    # inclusive nomes comuns, onde a IA pode detectar uma aparição e falhar
    # noutra idêntica (o texto-igual cobre a falha).
    inicios_por_pagina: dict[int, dict[str, list[int]]] = {}
    # Selos/imagens de assinatura APROVADOS, por página. A tarja remove
    # exatamente estes retângulos — ela não sai mais procurando selo por conta
    # própria (era assim que o PDF saía com imagens apagadas que nunca tinham
    # aparecido na revisão).
    selos_por_pagina: dict[int, list[tuple]] = {}
    # CPFs a descaracterizar (substituir por ***.456.789-**), por página.
    substituir_por_pagina: dict[int, dict[str, str]] = {}
    total = 0
    for grupo in aprovados:
        if descaracterizar_cpf and grupo.tipo == "CPF":
            for occ in grupo.ocorrencias:
                (substituir_por_pagina.setdefault(occ.pagina, {})
                 )[occ.texto] = cpf_descaracterizado(occ.texto)
                total += 1
            continue
        if grupo.tipo in (SEAL_ENTITY_TIPO, IMAGE_ENTITY_TIPO):
            for occ in grupo.ocorrencias:
                if occ.rect:
                    selos_por_pagina.setdefault(occ.pagina, []).append(occ.rect)
                    total += 1
            continue
        # Nome misto (corpo + assinatura) com a flag desligada: preserva as
        # ocorrências de assinatura. `not grupo.em_assinatura` exclui os
        # grupos 100% de assinatura — se o usuário marcou um desses na mão,
        # a intenção é tarjar, e filtrá-lo deixaria a marcação sem efeito.
        preservar_assinatura = (
            grupo.tipo == "Nome" and not tarjar_nomes_assinatura
            and not grupo.em_assinatura
            and any(o.em_assinatura for o in grupo.ocorrencias)
        )
        for occ in grupo.ocorrencias:
            if preservar_assinatura and occ.em_assinatura:
                continue
            textos_por_pagina.setdefault(occ.pagina, []).append(occ.texto)
            if grupo.tipo == "RG" or preservar_assinatura:
                (inicios_por_pagina.setdefault(occ.pagina, {})
                 .setdefault(occ.texto, []).append(occ.inicio))
            total += 1

    # deduplica textos por página (a busca já cobre todas as ocorrências)
    for p, textos in textos_por_pagina.items():
        textos_por_pagina[p] = list(dict.fromkeys(textos))

    rects_por_pagina: dict[int, list[tuple]] = {}
    for r in manual_rects or []:
        rects_por_pagina.setdefault(int(r[0]), []).append(tuple(r[1:5]))
        total += 1

    redacted, san_report = redact_pdf(pdf_bytes, textos_por_pagina,
                                      redact_email_domain=redact_email_domain,
                                      inicios_por_pagina=inicios_por_pagina,
                                      rects_por_pagina=rects_por_pagina or None,
                                      selos_por_pagina=selos_por_pagina or None,
                                      substituir_por_pagina=(
                                          substituir_por_pagina or None),
                                      achatar_tudo=achatar_tudo)
    return redacted, total, san_report


def process_auto(pdf_bytes: bytes, tipos_display: list[str], max_size_mb: int = 10):
    """Fluxo direto para a API (sem preview): valida, detecta, tarja tudo."""
    _info, grupos = analyze(pdf_bytes, tipos_display, max_size_mb=max_size_mb)
    redacted, total, _san = apply_redactions(pdf_bytes, grupos)
    return redacted, grupos, total
