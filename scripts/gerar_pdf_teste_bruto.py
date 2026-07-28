"""Gera `teste_bruto.pdf` (raiz do projeto, gitignored): estresse de ~35 páginas
reunindo TODAS as situações já exercitadas nos testes anteriores, enterradas em
muito texto burocrático de enchimento.

Cobertura:
- CPF em máscaras variadas: padrão, sem máscara, com espaços, "CPF nº",
  descaracterizado (***.123.456-**), parcial, inválido (controle negativo).
- RG com contexto (deve detectar) e controles negativos; CIN → CPF.
- CNPJ válido e inválido.
- Telefones: celular/fixo com e sem máscara, +55, 0800, máscara exótica.
- E-mails: pessoais, funcionais por domínio público, institucionais por parte
  local, lista fixa (BB/Banco Mundial), cabeçalhos De/Para/Cc, repetições.
- Nomes: no corpo, em tabela, em bloco de assinatura (teste da flag).
- Negativos numéricos: NUP, SEI, CNJ, normas, valores, datas, CEP, matrícula,
  empenho, código verificador — nada disso pode virar ocorrência.
- Imagens: selo gov.br RASTERIZADO (texto dentro da imagem, como o selo real —
  só sai com OCR+flatten), selo textual estilo SEI (pesquisável, contraste),
  assinatura manuscrita, rubrica, carimbo, logotipo, brasão, foto 3x4,
  negativos (ícone minúsculo, faixa de cabeçalho no topo) e uma página
  inteiramente escaneada (raster) com CPF dentro.
- Repetições dos mesmos dados em páginas distantes (grupos multi-página).

Uso:  python scripts/gerar_pdf_teste_bruto.py
"""
from pathlib import Path
from textwrap import wrap

import fitz  # PyMuPDF

SAIDA = "teste_bruto.pdf"

# ---------------------------------------------------------------- dados válidos


def _cpf(base9: str) -> str:
    """CPF válido (com DV calculado) no formato 000.000.000-00."""
    nums = [int(c) for c in base9]
    for peso_ini in (10, 11):
        s = sum(n * p for n, p in zip(nums, range(peso_ini, 1, -1)))
        d = 11 - s % 11
        nums.append(0 if d > 9 else d)
    txt = "".join(map(str, nums))
    return f"{txt[:3]}.{txt[3:6]}.{txt[6:9]}-{txt[9:]}"


def _cnpj(base12: str) -> str:
    """CNPJ válido no formato 00.000.000/0000-00."""
    nums = [int(c) for c in base12]
    for _ in range(2):
        pesos = list(range(len(nums) - 7, 1, -1)) + list(range(9, 1, -1))
        s = sum(n * p for n, p in zip(nums, pesos))
        d = 11 - s % 11
        nums.append(0 if d > 9 else d)
    t = "".join(map(str, nums))
    return f"{t[:2]}.{t[2:5]}.{t[5:8]}/{t[8:12]}-{t[12:]}"


CPF_JOAO = _cpf("456865909")        # protagonista — repete em várias páginas
CPF_MARIA = _cpf("390533447")
CPF_CARLOS = _cpf("529982247")
CPF_ANA = _cpf("111444777")
CPF_PEDRO = _cpf("987654321")
CPF_TESTEMUNHA = _cpf("123456789")
CNPJ_ALFA = _cnpj("112223330001")
CNPJ_BETA = _cnpj("445556660001")

SEM_MASCARA = CPF_JOAO.replace(".", "").replace("-", "")
DESCARACTERIZADO = f"***.{CPF_JOAO[4:11]}-**"

# ------------------------------------------------------------ texto de enchimento

FRASES = [
    "Considerando o disposto na legislação de regência e nos atos normativos"
    " aplicáveis à espécie, passa-se ao exame do feito.",
    "O expediente foi autuado, registrado e distribuído na forma regimental,"
    " observados os prazos e as formalidades legais.",
    "Cumpre observar que a matéria já foi objeto de deliberação em ocasiões"
    " anteriores, sem alteração do entendimento então firmado.",
    "A unidade técnica manifestou-se pela regularidade do procedimento,"
    " ressalvados os apontamentos constantes do relatório preliminar.",
    "Nos termos do art. 26 da Lei nº 9.784, de 29 de janeiro de 1999, a"
    " intimação do interessado far-se-á por meio que assegure a ciência.",
    "O processo encontra-se instruído com a documentação exigida pelos"
    " normativos internos, conforme certificado pela secretaria.",
    "Registre-se que eventuais impugnações deverão ser apresentadas no prazo"
    " improrrogável de dez dias úteis, contados da publicação.",
    "A autoridade competente, no uso das atribuições que lhe confere o"
    " regimento interno, decidiu pelo prosseguimento do feito.",
    "As diligências determinadas foram integralmente cumpridas, juntando-se"
    " aos autos os comprovantes respectivos.",
    "Não se verificou, no período examinado, qualquer ocorrência capaz de"
    " comprometer a lisura do procedimento administrativo.",
    "Encaminhem-se os autos à unidade de origem para ciência e adoção das"
    " providências cabíveis, com posterior arquivamento.",
    "A presente manifestação não vincula a autoridade superior, a quem"
    " compete a decisão final sobre a matéria.",
    "Os valores indicados foram conferidos com os registros do sistema"
    " integrado de administração financeira, sem divergências.",
    "Ressalte-se que o interessado foi regularmente notificado e deixou"
    " transcorrer in albis o prazo para manifestação.",
    "A publicação do extrato no diário oficial supre, para todos os efeitos,"
    " a exigência de publicidade dos atos administrativos.",
    "Eventuais dúvidas poderão ser dirimidas junto à unidade protocolizadora,"
    " no horário de expediente regular.",
    "O parecer jurídico opinou pela possibilidade do prosseguimento, desde"
    " que atendidas as recomendações nele consignadas.",
    "Trata-se de procedimento de natureza meramente ordinatória, sem"
    " repercussão sobre direitos de terceiros.",
]


def filler(inicio: int, n: int) -> str:
    """Parágrafo com n frases do pool, começando em `inicio` (cíclico)."""
    return " ".join(FRASES[(inicio + i) % len(FRASES)] for i in range(n))


def par(texto: str, largura: int = 96):
    """Quebra um parágrafo em itens de linha para o renderizador."""
    return [("", linha) for linha in wrap(texto, largura)] + [("", "")]


# ---------------------------------------------------------------------- imagens


def _img_selo_govbr():
    """Selo gov.br RASTERIZADO: nome/CPF/data viram pixels, como no selo real
    (xref 0, imagem inline). Sem OCR, search_for não encontra nada aqui."""
    d = fitz.open()
    pg = d.new_page(width=340, height=90)
    pg.draw_rect(pg.rect, color=(0.85, 0.85, 0.85), width=0.8)
    pg.insert_text((12, 26), "gov", fontsize=20, color=(0.0, 0.22, 0.47))
    pg.insert_text((48, 26), ".br", fontsize=20, color=(0.1, 0.6, 0.28))
    pg.insert_text((12, 46), "Documento assinado digitalmente", fontsize=9,
                   color=(0.2, 0.2, 0.2))
    pg.insert_text((12, 60), "JOSE CARLOS ALMEIDA FILHO", fontsize=10,
                   color=(0, 0, 0))
    pg.insert_text((12, 72), f"CPF: {CPF_JOAO}  Data: 15/07/2026 14:32:07",
                   fontsize=8, color=(0.25, 0.25, 0.25))
    pg.insert_text((12, 83), "Verifique em https://validar.iti.gov.br",
                   fontsize=7, color=(0.35, 0.35, 0.35))
    return pg.get_pixmap(dpi=200)


def _img_assinatura():
    d = fitz.open()
    pg = d.new_page(width=220, height=70)
    pg.draw_line((15, 55), (205, 55), color=(0, 0, 0), width=1)
    pg.draw_bezier((25, 50), (60, 5), (95, 65), (130, 20),
                   color=(0.05, 0.05, 0.35), width=2.2)
    pg.draw_bezier((110, 30), (140, 60), (170, 10), (200, 45),
                   color=(0.05, 0.05, 0.35), width=2.2)
    return pg.get_pixmap(dpi=150)


def _img_rubrica():
    d = fitz.open()
    pg = d.new_page(width=90, height=45)
    pg.draw_bezier((10, 35), (30, 5), (50, 40), (80, 12),
                   color=(0.1, 0.1, 0.4), width=2)
    return pg.get_pixmap(dpi=150)


def _img_carimbo():
    d = fitz.open()
    pg = d.new_page(width=150, height=150)
    pg.draw_circle((75, 75), 68, color=(0.7, 0.1, 0.1), width=3)
    pg.draw_circle((75, 75), 58, color=(0.7, 0.1, 0.1), width=1)
    pg.insert_text((36, 72), "CONFERE", fontsize=13, color=(0.7, 0.1, 0.1))
    pg.insert_text((42, 88), "COM O", fontsize=11, color=(0.7, 0.1, 0.1))
    pg.insert_text((37, 103), "ORIGINAL", fontsize=11, color=(0.7, 0.1, 0.1))
    return pg.get_pixmap(dpi=150)


def _img_logotipo():
    d = fitz.open()
    pg = d.new_page(width=240, height=80)
    for i, cor in enumerate([(0.9, 0.3, 0.1), (0.1, 0.5, 0.8), (0.2, 0.7, 0.3)]):
        pg.draw_rect(fitz.Rect(12 + i * 26, 18, 32 + i * 26, 62),
                     color=cor, fill=cor)
    pg.insert_text((100, 52), "ACME S.A.", fontsize=20, color=(0.15, 0.15, 0.15))
    return pg.get_pixmap(dpi=150)


def _img_brasao():
    d = fitz.open()
    pg = d.new_page(width=120, height=140)
    pg.draw_polyline([(60, 10), (110, 32), (110, 85), (60, 130), (10, 85),
                      (10, 32), (60, 10)],
                     color=(0.15, 0.25, 0.5), fill=(0.85, 0.88, 0.95), width=2)
    pg.draw_line((60, 25), (60, 115), color=(0.15, 0.25, 0.5), width=1.5)
    pg.draw_line((20, 70), (100, 70), color=(0.15, 0.25, 0.5), width=1.5)
    pg.insert_text((42, 78), "MG", fontsize=16, color=(0.15, 0.25, 0.5))
    return pg.get_pixmap(dpi=150)


def _img_foto():
    d = fitz.open()
    pg = d.new_page(width=105, height=140)
    pg.draw_rect(pg.rect, color=(0.75, 0.8, 0.85), fill=(0.75, 0.8, 0.85))
    pg.draw_circle((52, 55), 24, color=(0.4, 0.45, 0.5), fill=(0.4, 0.45, 0.5))
    pg.draw_oval(fitz.Rect(15, 85, 90, 165),
                 color=(0.4, 0.45, 0.5), fill=(0.4, 0.45, 0.5))
    return pg.get_pixmap(dpi=150)


def _img_icone():
    d = fitz.open()
    pg = d.new_page(width=40, height=40)
    pg.draw_rect(pg.rect, color=(0.2, 0.6, 0.3), fill=(0.2, 0.6, 0.3))
    pg.insert_text((13, 26), "OK", fontsize=13, color=(1, 1, 1))
    return pg.get_pixmap(dpi=150)


def _img_cabecalho():
    d = fitz.open()
    pg = d.new_page(width=400, height=50)
    pg.draw_rect(pg.rect, color=(0.1, 0.2, 0.45), fill=(0.1, 0.2, 0.45))
    pg.insert_text((14, 32), "MINISTERIO DA GESTAO PUBLICA", fontsize=15,
                   color=(1, 1, 1))
    return pg.get_pixmap(dpi=150)


def _img_pagina_escaneada():
    """Página A4 inteira rasterizada: simula documento escaneado. O CPF e o
    telefone aqui dentro NÃO existem como texto — só OCR alcança."""
    d = fitz.open()
    pg = d.new_page(width=595, height=842)
    pg.draw_rect(pg.rect, color=(0.96, 0.95, 0.92), fill=(0.96, 0.95, 0.92))
    y = 80
    for linha in [
        "DECLARACAO",
        "",
        "Declaro, para os devidos fins, que JOSE CARLOS ALMEIDA FILHO,",
        f"inscrito no CPF sob o n. {CPF_JOAO}, portador do RG",
        "12.345.678-2 SSP/SP, residente nesta capital, compareceu a esta",
        "reparticao no dia 14/07/2026 e apresentou a documentacao exigida.",
        "",
        "Telefone para contato: (11) 98765-4321",
        "E-mail: jcalmeida.filho@gmail.com",
        "",
        "Por ser expressao da verdade, firmo a presente.",
    ]:
        pg.insert_text((70, y), linha, fontsize=13, color=(0.1, 0.1, 0.1))
        y += 26
    pg.draw_bezier((90, 620), (140, 560), (200, 640), (260, 580),
                   color=(0.08, 0.08, 0.3), width=2.4)
    pg.draw_line((80, 640), (300, 640), color=(0, 0, 0), width=1)
    pg.insert_text((80, 660), "Jose Carlos Almeida Filho", fontsize=11,
                   color=(0.1, 0.1, 0.1))
    return pg.get_pixmap(dpi=110)


IMAGENS = {}


# ------------------------------------------------------------------ conteúdo

def _paginas():
    """Lista de páginas; cada página é uma lista de itens.
    Itens: ("t", txt) título 15pt | ("h", txt) seção 11.5pt | ("", txt) corpo
    9.5pt | ("s", txt) nota 7.5pt | ("gap", n) espaço | ("img", chave, rect).
    """
    P = []

    # ---- 1: capa / ofício
    P.append([
        ("img", "cabecalho", fitz.Rect(60, 40, 460, 90)),
        ("gap", 90),
        ("t", "OFÍCIO Nº 1.234/2026/GAB — TESTE BRUTO DO TARJADOR"),
        ("", ""),
        ("", "Processo NUP: 17944.000464/2026-14"),
        ("", "Protocolo SEI nº 0023634429 — código verificador 0023608094"),
        ("", "Brasília/DF, 15 de julho de 2026."),
        ("", ""),
        ("h", "Assunto: encaminhamento de documentação — dados pessoais diversos"),
        ("", ""),
        *par("Ao Senhor JOSE CARLOS ALMEIDA FILHO, inscrito no CPF sob o nº "
             f"{CPF_JOAO}, residente e domiciliado nesta capital. "
             + filler(0, 3)),
        *par(filler(3, 4)),
        *par("Para contato com este gabinete, utilize gabinete@gestao.gov.br "
             "ou o telefone (61) 3412-2842, ramal 4457. Atendimento das 8h "
             "às 18h. " + filler(7, 2)),
        ("", "Atenciosamente,"),
        ("", ""),
        ("", "Ana Beatriz Pereira da Costa"),
        ("", "Chefe de Gabinete — Matrícula 7654321"),
    ])

    # ---- 2: ofício corpo com dados espalhados
    P.append([
        ("t", "Página 2 — corpo do ofício (dados no meio de texto denso)"),
        ("", ""),
        *par(filler(1, 4)),
        *par("No curso da instrução, a interessada MARIA APARECIDA DE SOUZA, "
             f"CPF {CPF_MARIA}, celular (11) 98765-4321, requereu vista dos "
             "autos, o que foi deferido. " + filler(5, 3)),
        *par(filler(9, 4)),
        *par("O requerente indicou o e-mail jcalmeida.filho@gmail.com para "
             "as comunicações do art. 26 da Lei 9.784/1999. " + filler(12, 3)),
        *par(filler(15, 3)),
    ])

    # ---- 3: CPF em todas as máscaras
    P.append([
        ("t", "Página 3 — CPF: máscaras variadas"),
        ("", ""),
        ("h", "Devem ser detectados:"),
        ("", f"1. Máscara padrão: {CPF_JOAO} (o mesmo da página 1 — grupo"),
        ("", "   deve acumular ocorrências)."),
        ("", f"2. Sem máscara: {SEM_MASCARA}"),
        ("", f"3. Com rótulo: CPF nº {CPF_CARLOS}, inscrito na Receita."),
        ("", f"4. CPF/MF: {CPF_ANA} (rótulo antigo)."),
        ("", f"5. No fim de frase, com ponto: o CPF é {CPF_PEDRO}."),
        ("", ""),
        ("h", "Casos-limite (conferir o comportamento):"),
        ("", f"6. Já descaracterizado: {DESCARACTERIZADO} — saída do próprio"),
        ("", "   Tarjador; não deveria virar nova ocorrência."),
        ("", f"7. Máscara exótica com espaços: {CPF_JOAO.replace('.', ' ').replace('-', ' ')}"),
        ("", "   (pode escapar do regex — anotar o resultado)."),
        ("", ""),
        ("h", "Controles negativos (NÃO detectar):"),
        ("", "8. CPF inválido (DV errado): 293.849.249-00."),
        ("", "9. Onze dígitos que não são CPF: 11111111111."),
        ("", ""),
        *par(filler(2, 5)),
    ])

    # ---- 4: parecer com CPFs enterrados
    P.append([
        ("t", "Página 4 — parecer com CPFs enterrados no texto"),
        ("", ""),
        *par(filler(4, 5)),
        *par("Em meio à análise, constatou-se que o sócio-administrador, "
             f"Sr. Pedro Henrique Lima, CPF {CPF_PEDRO}, firmou o instrumento "
             "sem poderes específicos. " + filler(10, 4)),
        *par(filler(14, 5)),
        *par("A testemunha instrumentária, inscrita no CPF "
             f"{CPF_TESTEMUNHA}, confirmou a autenticidade da firma. "
             + filler(1, 3)),
    ])

    # ---- 5: telefones
    P.append([
        ("t", "Página 5 — telefones em máscaras variadas"),
        ("", ""),
        ("h", "Devem ser detectados:"),
        ("", "1. Celular com máscara: (11) 98765-4321 (repete a pág. 2)."),
        ("", "2. Celular sem parênteses: 11 98765-4321."),
        ("", "3. Celular sem máscara: 11987654321."),
        ("", "4. Com DDI: +55 11 98765-4321."),
        ("", "5. Fixo: (61) 3412-2842 (repete a pág. 1)."),
        ("", "6. Fixo com ponto: 61.3412.2842 (máscara exótica — anotar)."),
        ("", "7. Celular de MG: (31) 99911-2233."),
        ("", ""),
        ("h", "Casos a conferir:"),
        ("", "8. 0800 institucional: 0800 728 2822 (funcional — conferir"),
        ("", "   tratamento)."),
        ("", "9. Curto de atendimento: 4002-8922."),
        ("", ""),
        *par("Contexto em prosa: a servidora pediu retorno pelo telefone "
             "(21) 3333-4444, no horário comercial, ou pelo celular "
             "(21) 99876-5432 após as 18h. " + filler(6, 4)),
        *par(filler(11, 4)),
    ])

    # ---- 6: negativos numéricos
    P.append([
        ("t", "Página 6 — números que NÃO são dados pessoais"),
        ("s", "Nenhum item desta página pode virar ocorrência de telefone/CPF/RG."),
        ("", ""),
        ("", "1. Processo NUP: 08620.008109/2024-85."),
        ("", "2. Protocolo: PVL02.002287/2025-59."),
        ("", "3. SEI nº 0023634429 — código verificador 0023608094."),
        ("", "4. Numeração CNJ: 0001234-56.2026.8.26.0100."),
        ("", "5. Norma: MP 2.179-36/2001; Lei 3.225/2025 de 19/08/2025."),
        ("", "6. Valor: R$ 50.000.000,00 (cinquenta milhões de reais)."),
        ("", "7. Valor: R$ 1.234.567,89 conforme laudo de avaliação."),
        ("", "8. Data e hora: 14/04/2026 08:48:36."),
        ("", "9. CEP: 83.005-340; CEP sem máscara: 80010000."),
        ("", "10. Matrícula funcional 7654321; empenho 2026NE000123."),
        ("", "11. Licitação regida pela Lei nº 8.666, de 21 de junho de 1993."),
        ("", "12. Código de barras: 34191.79001 01043.510047 91020.150008."),
        ("", ""),
        *par(filler(8, 5)),
        *par(filler(13, 4)),
    ])

    # ---- 7: e-mails mix
    P.append([
        ("t", "Página 7 — e-mails: pessoais × funcionais × institucionais"),
        ("", ""),
        ("h", "Pessoais (checkbox deve vir MARCADO):"),
        ("", "1. jcalmeida.filho@gmail.com (repete a pág. 2)."),
        ("", "2. Luana Martins <luana.martins@outlook.com> pediu cópia."),
        ("", "3. Acadêmico: prof.ricardo@ufmg.edu.br (edu.br fica fora da"),
        ("", "   regra de domínio funcional)."),
        ("", ""),
        ("h", "Funcionais por domínio público (desmarcados):"),
        ("", "4. ana.pereira@fazenda.gov.br juntou parecer."),
        ("", "5. carlos.souza@prefeitura.sp.gov.br (municipal)."),
        ("", "6. maria.santos@tjsp.jus.br (Judiciário)."),
        ("", "7. pedro.lima@camara.leg.br (Legislativo)."),
        ("", "8. julia.costa@mpsp.mp.br; rafael.alves@defensoria.rs.def.br."),
        ("", "9. sgt.oliveira@eb.mil.br; auditoria@tce.sp.tc.br."),
        ("", ""),
        ("h", "Institucionais por parte local (desmarcados):"),
        ("", "10. gabinete@construtoraalfa.com.br (domínio privado)."),
        ("", "11. noreply@bancobeta.com.br — mensagem automática."),
        ("", ""),
        ("h", "Lista fixa (desmarcados):"),
        ("", "12. fernando.tal@bb.com.br (nominal, mas Banco do Brasil)."),
        ("", "13. jsmith@worldbank.org (Banco Mundial)."),
    ])

    # ---- 8: cabeçalho de e-mail / thread
    P.append([
        ("t", "Página 8 — thread de e-mail transcrita nos autos"),
        ("", ""),
        ("", "De: Ana Pereira <ana.pereira@fazenda.gov.br>"),
        ("", "Para: Jose Carlos Almeida Filho <jcalmeida.filho@gmail.com>"),
        ("", "Cc: gabinete@gestao.gov.br; Fernando Tal <fernando.tal@bb.com.br>"),
        ("", "Assunto: RE: Conciliação de repasses — exercício 2026"),
        ("", "Data: seg., 13 jul. 2026 09:41"),
        ("", ""),
        *par("Prezado senhor, segue a planilha consolidada. Qualquer "
             "divergência, retornar por este e-mail ou pelo (61) 3412-2842. "
             + filler(3, 2)),
        ("", ""),
        ("", "-------- Mensagem original --------"),
        ("", "De: Jose Carlos Almeida Filho <jcalmeida.filho@gmail.com>"),
        ("", "Para: Ana Pereira <ana.pereira@fazenda.gov.br>"),
        ("", "Assunto: Conciliação de repasses — exercício 2026"),
        ("", ""),
        *par("Prezada Ana, solicito a conciliação dos repasses do primeiro "
             "semestre. Meu celular para urgências: (11) 98765-4321. "
             + filler(9, 2)),
        ("", "João — enviado do meu celular"),
        ("", ""),
        *par(filler(15, 4)),
    ])

    # ---- 9: RGs com contexto
    P.append([
        ("t", "Página 9 — RG com contexto (TODOS devem ser detectados)"),
        ("", ""),
        ("", "1. Apresentou o RG nº 12.345.678-2, SSP/SP, em cópia autenticada."),
        ("", "2. RG: 45.678.912-1 SSP/SP, conforme documento anexo."),
        ("", "3. Portador do RG 10.000.006-X, expedido pela SSP/SP em 1998."),
        ("", "4. Carteira de Identidade nº 1.234.567 SSP/BA."),
        ("", "5. Registro Geral 9.876.543-2, órgão expedidor SSP/MG."),
        ("", "6. Identidade nº 12345678 IFP/RJ, no ato da assinatura."),
        ("", "7. RG 7654321 SESP/PR, natural de Curitiba."),
        ("", "8. R.G. 23.456.789-0 IGP/RS, conforme certidão."),
        ("", "9. Cédula de identidade 3.456.789 PC/MG."),
        ("", ""),
        *par("Em prosa: compareceu a portadora da identidade 98.765.432 "
             "SEJUSP/MS, acompanhada do cônjuge, portador do RG 34.998.152-X "
             "SSP/SP, ambos qualificados nos autos. " + filler(0, 3)),
        *par(filler(4, 4)),
    ])

    # ---- 10: RG negativos + CIN
    P.append([
        ("t", "Página 10 — RG: controles negativos e CIN"),
        ("", ""),
        ("h", "NÃO devem ser detectados como RG:"),
        ("", "1. A referência 12.345.678-9 consta do anexo técnico citado."),
        ("", "2. RG 0023634429 (dez dígitos — tamanho não é de RG)."),
        ("", "3. RG 1111111 (dígitos todos iguais)."),
        ("", "4. RG 123.456 (curto demais)."),
        ("", "5. Nota fiscal 12.345.678-2 emitida pela contratada (mesmo"),
        ("", "   número do item 1 da pág. 9, mas SEM contexto de identidade)."),
        ("", ""),
        ("h", "CIN — deve sair como CPF, nunca como RG:"),
        ("", f"6. Portador da CIN (novo RG) nº {CPF_CARLOS}, emitida em 2024."),
        ("", f"7. Identificado pelo RG {CPF_ANA} (padrão novo, usa o CPF)."),
        ("", ""),
        *par(filler(7, 5)),
        *par(filler(12, 4)),
    ])

    # ---- 11: CNPJ e contratos
    P.append([
        ("t", "Página 11 — CNPJ e dados societários"),
        ("", ""),
        ("", f"1. Construtora Alfa Ltda., CNPJ {CNPJ_ALFA}."),
        ("", f"2. Banco Beta S.A., inscrito no CNPJ sob o nº {CNPJ_BETA}."),
        ("", "3. CNPJ inválido (controle): 11.222.333/0001-99."),
        ("", ""),
        *par("Do contrato: a CONTRATADA, Construtora Alfa Ltda., CNPJ "
             f"{CNPJ_ALFA}, neste ato representada por seu sócio Pedro "
             f"Henrique Lima, CPF {CPF_PEDRO}, RG 7.654.321-8 SSP/PR, e a "
             "CONTRATANTE celebram o presente instrumento. " + filler(2, 3)),
        *par(filler(6, 5)),
        *par(filler(11, 4)),
    ])

    # ---- 12: tabela de servidores
    tabela = [
        ("Nome", "CPF", "Matrícula", "E-mail"),
        ("Ana Beatriz Pereira da Costa", CPF_ANA, "7654321",
         "ana.pereira@fazenda.gov.br"),
        ("Carlos Eduardo Souza", CPF_CARLOS, "1122334",
         "carlos.souza@prefeitura.sp.gov.br"),
        ("Maria Aparecida de Souza", CPF_MARIA, "9988776",
         "maria.souza@hotmail.com"),
    ]
    itens_tab = [("t", "Página 12 — quadro de pessoal (dados tabulares)"),
                 ("", "")]
    for linha in tabela:
        itens_tab.append(("", "  ".join(f"{c:<30}" if i == 0 else f"{c:<22}"
                                        for i, c in enumerate(linha)).rstrip()))
    itens_tab += [("", ""), *par(filler(9, 5)), *par(filler(14, 4))]
    P.append(itens_tab)

    # ---- 13: despacho com nomes no corpo
    P.append([
        ("t", "Página 13 — despacho (nomes no corpo do texto)"),
        ("", ""),
        *par("Encaminho o requerimento de Maria Oliveira Santos, CPF "
             f"{CPF_MARIA}, para as providências cabíveis. " + filler(0, 4)),
        *par("Intime-se o procurador constituído, Dr. Rafael Augusto de "
             "Alves Barbosa, OAB/RS 123.456, bem como a preposta Juliana "
             "Ferreira da Costa. " + filler(5, 4)),
        *par(filler(10, 5)),
    ])

    # ---- 14: bloco de assinatura + rubrica (teste da flag de nomes)
    P.append([
        ("t", "Página 14 — nome no corpo E na assinatura (teste da flag)"),
        ("", ""),
        ("s", 'Com "Tarjar nomes em assinaturas" DESLIGADO: tarja só no parágrafo;'),
        ("s", "a assinatura do rodapé fica visível. LIGADO: tarja nos dois."),
        ("", ""),
        *par("Encaminho o requerimento de Maria Oliveira Santos, CPF "
             f"{CPF_MARIA}, para as providências cabíveis, conforme despacho "
             "anterior. " + filler(2, 4)),
        *par(filler(7, 5)),
        ("gap", 60),
        ("", "Atenciosamente,"),
        ("img", "rubrica", fitz.Rect(70, 560, 150, 600)),
        ("gap", 55),
        ("", "Maria Oliveira Santos"),
        ("", "Secretária Municipal de Administração"),
        ("", "maria.santos@prefeitura.sp.gov.br — (11) 3222-1100"),
    ])

    # ---- 15: selo gov.br rasterizado
    P.append([
        ("t", "Página 15 — selo gov.br RASTERIZADO (imagem)"),
        ("", ""),
        ("s", "O selo abaixo é IMAGEM: o nome e o CPF dentro dele não existem"),
        ("s", "como texto. search_for não encontra; exige OCR + flatten."),
        ("", ""),
        *par(filler(3, 5)),
        *par(filler(8, 4)),
        ("img", "selo_govbr", fitz.Rect(60, 560, 400, 650)),
        ("cap", "^ selo de assinatura gov.br (raster — se não sair, assino em cima)",
         60, 665),
    ])

    # ---- 16: selo textual estilo SEI (pesquisável)
    P.append([
        ("t", "Página 16 — selo de assinatura TEXTUAL (contraste com a pág. 15)"),
        ("", ""),
        *par(filler(1, 5)),
        ("gap", 30),
        ("", "-" * 78),
        ("", "Documento assinado eletronicamente por JOSE CARLOS ALMEIDA"),
        ("", "FILHO, Analista, em 15/07/2026, às 14:32, conforme horário"),
        ("", "oficial de Brasília, com fundamento no art. 6º, § 1º, do"),
        ("", "Decreto nº 8.539, de 8 de outubro de 2015."),
        ("", "-" * 78),
        ("", "A autenticidade deste documento pode ser conferida no site"),
        ("", "https://sei.exemplo.gov.br/verifica, informando o código"),
        ("", "verificador 0023608094 e o código CRC 4A7B21F0."),
        ("", "-" * 78),
        ("", ""),
        *par(filler(6, 4)),
    ])

    # ---- 17: assinatura manuscrita + carimbo
    P.append([
        ("t", "Página 17 — assinatura manuscrita e carimbo (imagens)"),
        ("", ""),
        *par("Assinado por Carlos Eduardo Pereira, Diretor. Contato: "
             "carlos.pereira@empresa.com.br / (21) 3333-4444. CPF: "
             f"{CPF_CARLOS}. " + filler(4, 3)),
        *par(filler(8, 4)),
        ("img", "assinatura", fitz.Rect(60, 620, 260, 685)),
        ("img", "carimbo", fitz.Rect(330, 600, 440, 710)),
        ("cap", "^ assinatura manuscrita", 60, 700),
        ("cap", "^ carimbo redondo", 330, 725),
    ])

    # ---- 18: logotipo, brasão, foto
    P.append([
        ("t", "Página 18 — logotipo, brasão e foto 3x4 (itens separados)"),
        ("", ""),
        *par("As imagens abaixo devem virar itens independentes na revisão — "
             "o usuário decide uma a uma. " + filler(12, 3)),
        ("img", "logotipo", fitz.Rect(60, 300, 300, 380)),
        ("cap", "^ logotipo", 60, 395),
        ("img", "brasao", fitz.Rect(60, 480, 160, 600)),
        ("img", "foto", fitz.Rect(330, 480, 425, 605)),
        ("cap", "^ brasão", 60, 615),
        ("cap", "^ foto 3x4", 330, 620),
    ])

    # ---- 19: negativos de imagem
    P.append([
        ("img", "cabecalho", fitz.Rect(60, 40, 460, 90)),
        ("gap", 90),
        ("t", "Página 19 — controles NEGATIVOS de imagem"),
        ("", ""),
        ("s", "A faixa no topo é cabeçalho (grande, mas no TOPO): não detectar."),
        ("s", "O ícone no rodapé é minúsculo (abaixo do tamanho de selo): idem."),
        ("", ""),
        *par(filler(0, 5)),
        *par(filler(5, 5)),
        *par(filler(10, 4)),
        ("img", "icone", fitz.Rect(500, 740, 530, 770)),
    ])

    # ---- 20-21: ata longa com dados enterrados
    P.append([
        ("t", "Página 20 — ata de reunião (dados enterrados, parte 1)"),
        ("", ""),
        *par("Aos quatorze dias do mês de julho de dois mil e vinte e seis, "
             "reuniram-se os membros do colegiado. " + filler(2, 4)),
        *par("Com a palavra, o conselheiro Pedro Henrique Lima informou seu "
             "novo celular, (41) 98811-2233, para as convocações urgentes. "
             + filler(7, 4)),
        *par(filler(12, 5)),
    ])
    P.append([
        ("t", "Página 21 — ata de reunião (parte 2)"),
        ("", ""),
        *par(filler(3, 5)),
        *par("Deliberou-se oficiar a interessada pelo endereço eletrônico "
             "maria.souza@hotmail.com, com cópia para a unidade técnica em "
             "protocolo@gestao.gov.br. " + filler(9, 3)),
        *par("Constou da lista de presença o CPF sem máscara "
             f"{SEM_MASCARA}, transcrito do formulário físico. "
             + filler(14, 3)),
        *par(filler(1, 4)),
    ])

    # ---- 22-23: parecer denso com citações (negativos) + positivos escondidos
    P.append([
        ("t", "Página 22 — parecer jurídico (citações densas de normas)"),
        ("", ""),
        *par("Fundamenta-se o presente na Lei nº 8.666/1993, na Lei nº "
             "14.133, de 1º de abril de 2021, na MP 2.179-36/2001, no "
             "Decreto nº 8.539/2015 e na IN SEGES/ME nº 65/2021. "
             + filler(0, 4)),
        *par("Os autos nº 0001234-56.2026.8.26.0100 e o NUP "
             "08620.008109/2024-85 tramitam em apenso. " + filler(6, 4)),
        *par("Em que pese o volume de referências numéricas, apenas UM dado "
             "pessoal consta desta página: o CPF da procuradora, "
             f"{CPF_ANA}, citado incidentalmente. " + filler(11, 3)),
    ])
    P.append([
        ("t", "Página 23 — parecer jurídico (continuação)"),
        ("", ""),
        *par(filler(4, 5)),
        *par("No mérito, verificou-se que o subscritor da inicial, portador "
             "do RG 45.678.912-1 SSP/SP (já citado na página 9 — o grupo "
             "deve somar as ocorrências), detém legitimidade. "
             + filler(9, 4)),
        *par(filler(14, 5)),
    ])

    # ---- 24: repetições multi-página
    P.append([
        ("t", "Página 24 — repetições (grupos devem listar várias páginas)"),
        ("", ""),
        ("", f"1. CPF {CPF_JOAO} — 4ª aparição (págs. 1, 3, 15* e aqui)."),
        ("s", "   *na pág. 15 está DENTRO da imagem do selo: só conta com OCR."),
        ("", "2. Celular (11) 98765-4321 — págs. 2, 5, 8 e aqui."),
        ("", "3. E-mail jcalmeida.filho@gmail.com — págs. 2, 7, 8 e aqui."),
        ("", "4. Fixo (61) 3412-2842 — págs. 1, 8 e aqui."),
        ("", f"5. CPF {CPF_MARIA} — págs. 2, 13, 14 e aqui."),
        ("", ""),
        *par(filler(8, 5)),
        *par(filler(13, 5)),
    ])

    # ---- 25: extrato bancário fake
    P.append([
        ("t", "Página 25 — demonstrativo financeiro (valores = negativos)"),
        ("", ""),
        ("", f"Titular: Jose Carlos Almeida Filho — CPF {CPF_JOAO}"),
        ("", "Agência 1234-5, conta corrente 67.890-1 (conferir tratamento)."),
        ("", ""),
        ("", "Data        Histórico                       Valor (R$)"),
        ("", "01/07/2026  Transferência recebida          12.345,67"),
        ("", "03/07/2026  Pagamento boleto 34191.79001    -1.234,56"),
        ("", "07/07/2026  Tarifa pacote serviços          -89,90"),
        ("", "10/07/2026  TED enviada                     -50.000,00"),
        ("", "14/07/2026  Saldo em conta                  961.021,21"),
        ("", ""),
        *par(filler(2, 5)),
        *par(filler(7, 4)),
    ])

    # ---- 26: endereços e CEPs
    P.append([
        ("t", "Página 26 — endereços (CEP não é dado tarjável por padrão)"),
        ("", ""),
        *par("Correspondência para Rua das Flores, nº 100, apto. 42, Bairro "
             "Centro, Curitiba/PR, CEP 83.005-340, aos cuidados de MARIA "
             "APARECIDA DE SOUZA. " + filler(5, 3)),
        *par("Endereço alternativo: SQN 410, Bloco B, Brasília/DF, CEP "
             "80010000, telefone do porteiro (61) 3555-8899. "
             + filler(10, 3)),
        *par(filler(15, 5)),
    ])

    # ---- 27: lista de contatos mista
    P.append([
        ("t", "Página 27 — lista de contatos mista"),
        ("", ""),
        ("", "Contato                          Telefone           E-mail"),
        ("", "Gabinete (funcional)             (61) 3412-2842     gabinete@gestao.gov.br"),
        ("", "Ana Pereira (funcional)          (61) 3412-2001     ana.pereira@fazenda.gov.br"),
        ("", "Jose Carlos (pessoal)            (11) 98765-4321    jcalmeida.filho@gmail.com"),
        ("", "Luana Martins (pessoal)          (21) 99876-5432    luana.martins@outlook.com"),
        ("", "Ouvidoria (funcional)            0800 728 2822      ouvidoria@gestao.gov.br"),
        ("", "Protocolo (funcional)            (61) 3412-2000     protocolo@gestao.gov.br"),
        ("", "Maria Souza (pessoal)            (31) 99911-2233    maria.souza@hotmail.com"),
        ("", ""),
        *par(filler(4, 5)),
        *par(filler(9, 4)),
    ])

    # ---- 28: página quase vazia, só assinatura
    P.append([
        ("gap", 250),
        ("", "Última folha de assinaturas do anexo IV."),
        ("img", "assinatura", fitz.Rect(200, 420, 400, 485)),
        ("gap", 200),
        ("", "                    Testemunha: Ana Paula Rodrigues"),
        ("", f"                    CPF {CPF_TESTEMUNHA} — (31) 99911-2233"),
    ])

    # ---- 29: certidão com descaracterizado repetido
    P.append([
        ("t", "Página 29 — certidão (CPF descaracterizado em uso real)"),
        ("", ""),
        *par("CERTIFICO, para os devidos fins, que em consulta aos sistemas "
             f"desta unidade, o contribuinte de CPF {DESCARACTERIZADO} "
             "(já descaracterizado na origem — não deve virar ocorrência) "
             "não possui pendências. " + filler(0, 3)),
        *par(f"A empresa Construtora Alfa Ltda., CNPJ {CNPJ_ALFA}, figura "
             "como responsável solidária. " + filler(4, 4)),
        *par(filler(8, 5)),
        *par(filler(13, 4)),
    ])

    # ---- 30: página inteira escaneada (raster)
    P.append([
        ("img", "pagina_escaneada", fitz.Rect(0, 0, 595, 842)),
        ("cap", "Página 30: TUDO nesta página é imagem (scan) — CPF, RG, telefone"
         " e e-mail só aparecem com OCR.", 60, 830),
    ])

    # ---- 31-32: recurso e contrarrazões
    P.append([
        ("t", "Página 31 — recurso administrativo (dados espalhados)"),
        ("", ""),
        *par(filler(6, 5)),
        *par("O recorrente, qualificado nos autos, atualizou seu endereço "
             "eletrônico para jc.almeida2026@yahoo.com.br e o telefone para "
             "+55 11 98765-4321. " + filler(11, 3)),
        *par(filler(16, 5)),
    ])
    P.append([
        ("t", "Página 32 — contrarrazões"),
        ("", ""),
        *par(filler(1, 5)),
        *par("A recorrida, por sua procuradora, Dra. Juliana Ferreira da "
             "Costa, OAB/SP 654.321, requer a manutenção da decisão. "
             "Telefone do escritório: 11.3040.5060 (máscara exótica — "
             "anotar). " + filler(7, 3)),
        *par(filler(12, 5)),
    ])

    # ---- 33: CNJ denso + um celular
    P.append([
        ("t", "Página 33 — numeração CNJ densa (negativos) + 1 celular"),
        ("", ""),
        ("", "Processos conexos (nenhum pode virar ocorrência):"),
        ("", "  0001234-56.2026.8.26.0100    0009876-54.2025.8.26.0053"),
        ("", "  1000222-33.2024.4.03.6100    5001111-22.2026.4.04.7000"),
        ("", "  0801234-56.2026.8.07.0001    17944.000464/2026-14 (NUP)"),
        ("", ""),
        *par("Único dado pessoal desta página: o oficial de justiça deixou "
             "contato no celular (41) 98811-2233 (repete a pág. 20). "
             + filler(3, 4)),
        *par(filler(8, 5)),
        *par(filler(14, 4)),
    ])

    # ---- 34: múltiplos blocos de assinatura
    P.append([
        ("t", "Página 34 — encerramento com múltiplos signatários"),
        ("", ""),
        *par("Nada mais havendo a tratar, lavrou-se o presente termo, que "
             "vai assinado pelos presentes. " + filler(10, 3)),
        ("gap", 40),
        ("", "________________________________"),
        ("", "Jose Carlos Almeida Filho"),
        ("", f"CPF {CPF_JOAO} — Analista"),
        ("gap", 30),
        ("", "________________________________"),
        ("", "Maria Oliveira Santos"),
        ("", "Secretária Municipal de Administração"),
        ("gap", 30),
        ("", "________________________________"),
        ("", "Pedro Henrique Lima"),
        ("", f"CPF {CPF_PEDRO} — Conselheiro"),
        ("img", "rubrica", fitz.Rect(440, 700, 520, 745)),
    ])

    # ---- 35: página final — selo raster de novo + carimbo
    P.append([
        ("t", "Página 35 — validação final"),
        ("", ""),
        *par("Confere com o original arquivado nesta unidade. "
             + filler(15, 3)),
        *par(filler(2, 4)),
        ("img", "selo_govbr", fitz.Rect(60, 480, 400, 570)),
        ("cap", "^ selo gov.br rasterizado (2ª aparição — mesma imagem da pág. 15)",
         60, 585),
        ("img", "carimbo", fitz.Rect(430, 620, 540, 730)),
        ("cap", "FIM DO DOCUMENTO DE TESTE — 35 páginas.", 60, 745),
    ])

    return P


# ------------------------------------------------------------------- renderer

MARGEM_X = 60
Y_INICIO = 70


def main() -> None:
    IMAGENS.update({
        "selo_govbr": _img_selo_govbr(),
        "assinatura": _img_assinatura(),
        "rubrica": _img_rubrica(),
        "carimbo": _img_carimbo(),
        "logotipo": _img_logotipo(),
        "brasao": _img_brasao(),
        "foto": _img_foto(),
        "icone": _img_icone(),
        "cabecalho": _img_cabecalho(),
        "pagina_escaneada": _img_pagina_escaneada(),
    })

    doc = fitz.open()
    for itens in _paginas():
        page = doc.new_page()  # A4
        y = Y_INICIO
        for item in itens:
            tipo = item[0]
            if tipo == "img":
                _, chave, rect = item
                page.insert_image(rect, pixmap=IMAGENS[chave])
                continue
            if tipo == "cap":  # legenda com posição absoluta
                _, texto, x, yy = item
                page.insert_text((x, yy), texto, fontsize=7.5)
                continue
            if tipo == "gap":
                y += item[1]
                continue
            texto = item[1]
            if y > page.rect.height - 50:
                break  # nunca estourar a página
            if tipo == "t":
                page.insert_text((MARGEM_X, y), texto, fontsize=13.5)
                y += 24
            elif tipo == "h":
                y += 4
                page.insert_text((MARGEM_X, y), texto, fontsize=11)
                y += 18
            elif tipo == "s":
                page.insert_text((MARGEM_X, y), texto, fontsize=7.5)
                y += 12
            else:
                page.insert_text((MARGEM_X, y), texto, fontsize=9.5)
                y += 15

    destino = Path(__file__).resolve().parent.parent / SAIDA
    doc.save(destino)
    n = doc.page_count
    doc.close()
    print(f"Gerado: {destino} ({n} páginas)")


if __name__ == "__main__":
    main()
