"""Detecção de PII — versão COMPLETA com Presidio + spaCy.

Requer: presidio-analyzer, spacy, pt_core_news_sm (ou md/lg).
Importado condicionalmente por detector.py.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Iterable

import fitz  # PyMuPDF
import phonenumbers
from presidio_analyzer import (
    AnalysisExplanation,
    AnalyzerEngine,
    LocalRecognizer,
    Pattern,
    PatternRecognizer,
    RecognizerRegistry,
    RecognizerResult,
)
from presidio_analyzer.nlp_engine import NlpEngineProvider

from ..config import settings


# ---------------------------------------------------------------------------
# Entidades e mapeamento
# ---------------------------------------------------------------------------
DISPLAY_TO_PRESIDIO: dict[str, list[str]] = {
    "CPF": ["CPF"],
    "CNPJ": ["CNPJ"],
    # RG legado (7-9 dígitos, formato varia por estado). A CIN — novo RG,
    # emitida desde 2022 — usa o número do CPF, então cai no CPFRecognizer e
    # é exibida como CPF (decisão de produto: não rotular CIN como RG).
    "RG": ["RG_BR"],
    "E-mail": ["EMAIL_ADDRESS"],
    # Celular e Telefone (fixo) compartilham o mesmo recognizer/entidade do
    # Presidio (PHONE_NUMBER_BR, candidatos por regex); a distinção real entre
    # os dois — e a validação de que é mesmo um telefone (não protocolo/SEI,
    # CPF, CNPJ) — acontece depois, via `_classify_phone_br` (phonenumbers/
    # libphonenumber). Por isso o mapeamento abaixo é só para resolver quais
    # entidades do Presidio rodar; PRESIDIO_TO_DISPLAY nunca é consultado para
    # PHONE_NUMBER_BR (ver o loop de detecção).
    "Celular": ["PHONE_NUMBER_BR"],
    "Telefone": ["PHONE_NUMBER_BR"],
    "Nome": ["PERSON"],
}

PRESIDIO_TO_DISPLAY: dict[str, str] = {
    v: k for k, vs in DISPLAY_TO_PRESIDIO.items() for v in vs
}

DEFAULT_ENTITIES: list[str] = list(DISPLAY_TO_PRESIDIO.keys())


# ---------------------------------------------------------------------------
# Validação de dígitos
# ---------------------------------------------------------------------------
def _digits(s: str) -> list[int]:
    return [int(c) for c in s if c.isdigit()]


def validate_cpf(cpf: str) -> bool:
    d = _digits(cpf)
    if len(d) != 11 or len(set(d)) == 1:
        return False
    def dv(nums, start):
        s = sum(n * (start - i) for i, n in enumerate(nums))
        r = (s * 10) % 11
        return 0 if r == 10 else r
    return dv(d[:9], 10) == d[9] and dv(d[:10], 11) == d[10]


def validate_cnpj(cnpj: str) -> bool:
    d = _digits(cnpj)
    if len(d) != 14 or len(set(d)) == 1:
        return False
    def dv(nums, weights):
        s = sum(n * w for n, w in zip(nums, weights))
        r = s % 11
        return 0 if r < 2 else 11 - r
    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    w2 = [6] + w1
    return dv(d[:12], w1) == d[12] and dv(d[:13], w2) == d[13]


def validate_rg_sp(rg: str) -> bool:
    """Dígito verificador do padrão SSP-SP (módulo 11, pesos 2..9; DV 10 = X,
    11 = 0). É o ÚNICO estado com algoritmo documentado — RG não tem padrão
    nacional e a maioria dos estados nem tem DV. Por isso este validador só
    serve de BÔNUS de score no RGRecognizer (nunca de filtro): falhar aqui não
    descarta o candidato, que pode ser um RG legítimo de outro estado."""
    limpo = re.sub(r"[^\dXx]", "", rg)
    if len(limpo) != 9 or not limpo[:8].isdigit():
        return False
    soma = sum(int(c) * w for c, w in zip(limpo[:8], range(2, 10)))
    resto = 11 - (soma % 11)
    dv = "X" if resto == 10 else str(0 if resto == 11 else resto)
    return limpo[8].upper() == dv


# ---------------------------------------------------------------------------
# Reconhecedores custom
# ---------------------------------------------------------------------------
class CPFRecognizer(PatternRecognizer):
    def __init__(self):
        super().__init__(
            supported_entity="CPF",
            patterns=[Pattern("CPF", r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b", 0.6)],
            supported_language="pt",
        )
    def validate_result(self, pattern_text):
        return validate_cpf(pattern_text)


class CNPJRecognizer(PatternRecognizer):
    def __init__(self):
        super().__init__(
            supported_entity="CNPJ",
            patterns=[Pattern("CNPJ", r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b", 0.6)],
            supported_language="pt",
        )
    def validate_result(self, pattern_text):
        return validate_cnpj(pattern_text)


# Candidato a RG: 7 a 9 dígitos, com máscara de pontos completa ou sem
# máscara, DV final opcional (dígito ou X). Lookaround bloqueia matches que
# são pedaço de um número maior (CPF/CNPJ mascarados, processo CNJ, SEI) e
# valores monetários (",dd" logo após). O que a regex não bloqueia, o
# analyze() descarta: contagem de dígitos fora de 7-9 (protocolo de 10+),
# dígitos todos iguais e — o filtro decisivo — falta de contexto.
_RG_CANDIDATE_RE = re.compile(
    r"(?<![\d./-])(\d{1,2}(?:\.\d{3}){2}|\d{7,9})(?:-?([\dXx]))?(?![\d./-]|,\d)"
)

# Palavras de contexto que autorizam um candidato a virar detecção. RG não
# tem validação matemática na maioria dos estados (ver validate_rg_sp), então
# SEM contexto todo número de 7-9 dígitos seria falso positivo em potencial
# (CEP sem máscara, matrícula, ID interno). Roda sobre a janela já minúscula
# e sem acentos. Siglas de órgão expedidor: SSP e variantes (SESP/SEJUSP/
# SEDS), institutos de identificação (IIRGD-SP, IFP-RJ, IGP-RS/SC, ITEP-RN,
# POLITEC-MT) e "PC/UF" (Polícia Civil — só com a barra/hífen de UF, "PC"
# solto é ambíguo demais).
_RG_CONTEXT_RE = re.compile(
    r"\b(?:rg|r\.g\.?|registro\s+geral|identidade|"
    r"ssp|sesp|sejusp|seds|iirgd|ifp|igp|itep|politec|"
    r"pc\s*[/-]\s*[a-z]{2}|"
    r"orgao\s+(?:expedidor|emissor)|expedidor|emissor|"
    r"doc(?:umento)?\.?\s+de\s+ident)\b"
)

_RG_JANELA_ANTES = 45   # "RG nº ...", "portador da cédula de identidade ..."
_RG_JANELA_DEPOIS = 30  # "... SSP/SP", "... expedido pela SSP"


class RGRecognizer(LocalRecognizer):
    """RG legado (Registro Geral) — detecção por regex + contexto OBRIGATÓRIO.

    Não existe padrão nacional de RG: cada SSP estadual define formato e
    quantidade de dígitos (7 a 9), e só SP tem dígito verificador documentado.
    Um candidato numérico sozinho é indistinguível de CEP/matrícula/ID, então
    este recognizer NÃO emite nada sem palavra de contexto (RG, identidade,
    SSP/órgão expedidor...) na vizinhança — decisão de produto para conter
    falso positivo. Quando o DV de SP confere, o score sobe (0.6 → 0.75).

    CIN (novo RG): usa o número do CPF (11 dígitos) — nunca casa com a regex
    daqui (7-9 dígitos) e é detectada/exibida como CPF pelo CPFRecognizer,
    como pedido pelo usuário.
    """

    def __init__(self):
        super().__init__(
            supported_entities=["RG_BR"],
            supported_language="pt",
            context=["rg", "identidade", "registro geral", "ssp",
                     "órgão expedidor", "expedidor"],
        )

    def load(self) -> None:
        pass

    def analyze(self, text, entities, nlp_artifacts=None):
        if "RG_BR" not in entities:
            return []
        results = []
        for m in _RG_CANDIDATE_RE.finditer(text):
            digitos = [c for c in m.group(0) if c.isdigit()]
            # 7-9 dígitos contando o DV numérico (X não conta como dígito).
            if not (7 <= len(digitos) <= 9) or len(set(digitos)) == 1:
                continue
            janela = (
                text[max(0, m.start() - _RG_JANELA_ANTES):m.start()]
                + " | "  # impede palavra de contexto "atravessar" o número
                + text[m.end():m.end() + _RG_JANELA_DEPOIS]
            )
            if not _RG_CONTEXT_RE.search(_strip_accents(janela).lower()):
                continue
            score = 0.75 if validate_rg_sp(m.group(0)) else 0.6
            explicacao = AnalysisExplanation(
                recognizer=self.__class__.__name__,
                original_score=score,
                textual_explanation="Candidato por regex + palavra de contexto de RG",
            )
            results.append(
                RecognizerResult(
                    entity_type="RG_BR",
                    start=m.start(),
                    end=m.end(),
                    score=score,
                    analysis_explanation=explicacao,
                )
            )
        return results


class PhoneBRRecognizer(LocalRecognizer):
    """Localiza candidatos a telefone BR com phonenumbers.PhoneNumberMatcher.

    Regex fixa (o formato antigo deste recognizer) só reconhecia as máscaras
    que alguém já tinha pensado antes — perdia "+55 46 99911-2233", "046-
    99911-2233" ou espaço depois do 9º dígito ("46 9 9911-2233"). O
    PhoneNumberMatcher da libphonenumber já sabe todas as variações de
    formatação válidas no Brasil, então acha o candidato de qualquer jeito
    escrito. `leniency=VALID` já é estrito o bastante para não confundir CNPJ,
    CPF, protocolo/SEI, data ou valor monetário com telefone (testado).

    A validação fina (DDD real, tipo do número) e a distinção Celular x
    Telefone ficam em `_classify_phone_br`, chamado depois no laço principal.
    """

    def __init__(self):
        super().__init__(
            supported_entities=["PHONE_NUMBER_BR"],
            supported_language="pt",
            context=["tel", "telefone", "celular", "fone", "contato"],
        )

    def load(self) -> None:
        pass

    def analyze(self, text, entities, nlp_artifacts=None):
        if "PHONE_NUMBER_BR" not in entities:
            return []
        results = []
        for m in phonenumbers.PhoneNumberMatcher(
            text, "BR", leniency=phonenumbers.Leniency.VALID
        ):
            # analysis_explanation precisa existir (não None) — o realce por
            # palavra de contexto do Presidio (tel/telefone/celular/...) chama
            # métodos nela incondicionalmente.
            explicacao = AnalysisExplanation(
                recognizer=self.__class__.__name__,
                original_score=0.7,
                textual_explanation="Reconhecido via phonenumbers.PhoneNumberMatcher (BR)",
            )
            results.append(
                RecognizerResult(
                    entity_type="PHONE_NUMBER_BR",
                    start=m.start,
                    end=m.end,
                    score=0.7,
                    analysis_explanation=explicacao,
                )
            )
        return results


# Números de protocolo/ID/SEI (ex.: "ID. 0023608094", "SEI nº 0023634429",
# "código verificador 0023634429") têm o mesmo formato de 10 dígitos que a
# regex de telefone fixo — sem validação de dígitos (como CPF/CNPJ), a
# única forma de descartar é pelo contexto que precede o número.
_PHONE_FALSE_POSITIVE_CONTEXT_RE = re.compile(
    r"(?:\bID\.?|c[óo]digo\s+verificador|\bSEI\b|protocolo|matr[íi]cula|matr\.?|"
    r"processo)\s*(?:n[ºo°]?\.?)?\s*:?\s*$",
    re.IGNORECASE,
)


def _looks_like_document_id(text: str, start: int, window: int = 40) -> bool:
    preceding = text[max(0, start - window):start]
    return bool(_PHONE_FALSE_POSITIVE_CONTEXT_RE.search(preceding))


# Referências a normativos (ex.: "MP 2.179-36/2001", "Lei 9.496/1997") têm o
# mesmo problema do protocolo/SEI: os dígitos concatenados formam um número
# que a libphonenumber valida como telefone BR — "2.179-36/2001" vira
# 2179362001 e é classificado como móvel. O que NENHUM telefone tem, mas toda
# referência a norma tem, é o sufixo "/AAAA" (barra + ano) no fim. Não dá pra
# rejeitar "/" em geral: telefone usa barra como separador de DDD
# ("021/3333-4444", "21/99911-2233"); por isso a âncora no FIM ($) — só
# descarta o que termina em barra+ano, preservando esses telefones legítimos.
_NORMATIVO_REF_RE = re.compile(r"/(?:19|20)\d{2}$")


def _looks_like_normativo_ref(trecho: str) -> bool:
    return bool(_NORMATIVO_REF_RE.search(trecho))


# Número de processo administrativo no formato NUP (Número Único de Protocolo,
# padrão do governo federal): "17944.000464/2026-14", "08620.008109/2024-85",
# "PVL02.002287/2025-59". Precisa ser casado no TEXTO INTEIRO, não no trecho
# que o Presidio devolve: a regex de telefone recorta só o começo do número
# ("17944.000464/") e joga fora o "/2026-14" — justamente o pedaço que
# denunciaria que aquilo é um protocolo. Sem o sufixo, sobram 11 dígitos que a
# libphonenumber valida como celular de DDD 17, e o protocolo virava "Celular".
# Blocos folgados (2-6 e 4-7 dígitos) para cobrir as variações de órgão; o que
# não varia — e nenhum telefone tem — é o "ANO-DD" no fim.
# Os separadores aceitam "_" além de "/" e "-": ao virar nome de anexo, o SEI
# higieniza o NUP trocando "/" e "-" por "_" ("17944.000464_2026_14.pdf"), e
# essa variante precisa ser reconhecida como protocolo tanto quanto a original,
# senão o "17944.000464" do nome do arquivo escapa do guard e vira "Celular".
_PROTOCOLO_NUP_RE = re.compile(r"\d{2,6}\.\d{4,7}[/_](?:19|20)\d{2}[-_]\d{2}")


def _classify_phone_br(trecho: str) -> str | None:
    """Classifica um candidato a telefone brasileiro com phonenumbers (Google
    libphonenumber) — biblioteca já instalada transitivamente via
    presidio-analyzer, agora usada diretamente aqui.

    Substitui o "começa com 9" da regex por validação de verdade: confere o
    DDD e o formato contra a tabela real de faixas do Brasil, e descarta como
    efeito colateral protocolo/SEI, CPF e CNPJ que passariam pela regex sem
    isso (`_looks_like_document_id` continua rodando antes, como 2ª camada).

    Retorna "Celular", "Telefone" (fixo/outros tipos válidos) ou None
    (não valida como telefone brasileiro).
    """
    # Referência a norma que termina em "/AAAA" nunca é telefone (nem móvel nem
    # fixo) — descarta antes de parsear. Sem isso, tanto "2.179-36/2001" (móvel)
    # quanto muitos N.NNN-NN/AAAA que concatenam num fixo válido passariam.
    if _looks_like_normativo_ref(trecho):
        return None
    try:
        numero = phonenumbers.parse(trecho, "BR")
    except phonenumbers.NumberParseException:
        return None
    # "BR" acima só vale como região PADRÃO — um número com "+" e código de
    # país explícito (ex.: "+1 202-555-0173") é interpretado pelo próprio
    # código, ignorando o padrão. Sem essa checagem, número estrangeiro
    # sintaticamente válido (só não é do Brasil) passaria como "Telefone".
    if phonenumbers.region_code_for_number(numero) != "BR":
        return None
    if not phonenumbers.is_valid_number(numero):
        return None
    if phonenumbers.number_type(numero) == phonenumbers.PhoneNumberType.MOBILE:
        # Celular brasileiro vigente tem 11 dígitos nacionais: DDD (2) + 9 + 8,
        # com o "9" (nono dígito, obrigatório em todo o país desde 2016) logo
        # após o DDD. A libphonenumber ainda tipa como MOBILE o formato antigo
        # de 8 dígitos e concatenações espúrias como "2.179-36/2001"
        # (→ 2179362001, sem o 9). Exigir o nono dígito remove esses sem perder
        # celular real — todo celular atual segue a regra, com ou sem DDI, pois
        # `national_number` já vem sem o código de país ("+55 11 9...").
        nacional = str(numero.national_number)
        if len(nacional) != 11 or nacional[2] != "9":
            return None
        return "Celular"
    return "Telefone"


class EmailRecognizer(PatternRecognizer):
    def __init__(self):
        super().__init__(
            supported_entity="EMAIL_ADDRESS",
            patterns=[
                Pattern("Email", r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", 0.85)
            ],
            supported_language="pt",
        )


# ---------------------------------------------------------------------------
# Classificação da parte local do e-mail: nome de pessoa x órgão/função
# ---------------------------------------------------------------------------
# E-mails funcionais/institucionais (gabinete@, secretaria@, rh@, siglas como
# smf@) são detectados normalmente, mas vêm DESMARCADOS na tabela de revisão —
# o usuário decide caso a caso. E-mails que parecem de pessoa (gustavo.mendes@)
# e casos ambíguos vêm MARCADOS (padrão seguro: na dúvida, tarja).
_EMAIL_ORG_TOKENS = {
    "gabinete", "secretaria", "secretariado", "protocolo", "ouvidoria",
    "contato", "contatos", "atendimento", "comunicacao", "imprensa",
    "assessoria", "financeiro", "financas", "rh", "recursoshumanos", "dp",
    "juridico", "procuradoria", "licitacao", "licitacoes", "compras",
    "administracao", "administrativo", "admin", "diretoria", "presidencia",
    "prefeitura", "prefeito", "camara", "setor", "departamento",
    "coordenacao", "controladoria", "tesouraria", "contabilidade",
    "almoxarifado", "patrimonio", "transparencia", "sac", "faleconosco",
    "noreply", "naoresponder", "suporte", "ti", "tecnologia", "sistemas",
    "webmaster", "info", "geral", "chefia", "cerimonial", "recepcao",
    "portaria", "arquivo", "biblioteca", "educacao", "saude", "obras",
    "fazenda", "planejamento", "cultura", "esporte", "turismo",
    "ambiente", "meioambiente", "agricultura", "social", "habitacao",
    "seguranca", "transito", "transporte", "servidor", "servicos",
    "servico", "email", "mail", "cadastro", "orcamento", "cpl", "cgm",
    "secretario", "controle", "credito", "apoio", "gestor", "coordenador",
    # Siglas de unidades de bancos/órgãos (Caixa, Tesouro, PGFN) que aparecem
    # como parte local — incorporadas conforme surgem nos documentos reais.
    "gecoa", "gecod", "codiv", "sudip", "cenop", "digov", "gepac", "serap",
    "pgafin", "pgfn", "stn", "cof", "regovjd", "capag",
    # Palavras funcionais que aparecem em display names de órgão/setor.
    "governo", "produtos", "contratos", "arrecadacao", "saneamento",
    "infraestrutura", "conducao",
    # Caixas de secretaria/setor municipal (governo BR).
    "economia", "tributos", "engenharia", "urbanismo", "esportes",
    "juventude", "convenios", "fiscalizacao", "expediente", "frota",
    # Caixas funcionais em inglês (organismos internacionais: World Bank, BID...).
    "team", "teams", "operations", "group", "dept", "department", "unit",
    "office", "staff", "committee", "secretariat", "division", "section",
    "bureau", "board", "support", "helpdesk", "service", "services",
    "billing", "payroll", "notifications", "newsletter", "distribution",
    "procurement", "treasury", "finance", "accounting", "communications",
    "press", "media", "careers", "recruiting", "recruitment", "sales",
    "marketing", "contact", "help", "desk",
}

# Palavras longas e inequívocas: valem também como substring da parte local
# inteira, cobrindo variações grudadas (ex.: "gabinetedoprefeito").
_EMAIL_ORG_SUBSTRINGS = (
    "gabinete", "secretaria", "ouvidoria", "prefeitura", "protocolo",
    "licitac", "comunicac", "administrac", "presidencia", "diretoria",
    "controladoria", "tesouraria", "contabilidade", "transparencia",
    "imprensa", "juridico", "financeiro", "atendimento", "assessoria",
    "procuradoria", "recursoshumanos", "faleconosco", "coordenac",
    "departamento", "almoxarifado", "planejamento",
    # e-mails no-reply em suas variações (naoresponda/naoresponder/naoresponde,
    # no-reply/no.reply) — sempre institucionais.
    "naorespond", "noreply",
)


# Sufixos de domínio restritos a entes públicos no Registro.br — um por
# "poder": Executivo (gov.br, incluindo estaduais uf.gov.br e municipais),
# Legislativo (leg.br), Judiciário (jus.br), Ministério Público (mp.br),
# Defensorias (def.br), Forças Armadas (mil.br) e Tribunais de Contas (tc.br).
# Como só ente público consegue registrar nessas categorias, o sufixo sozinho
# basta para classificar o e-mail como FUNCIONAL (fornecido pelo órgão) —
# mesmo com parte local nominal (joao.silva@orgao.gov.br). edu.br fica de
# fora de propósito: inclui instituições privadas e e-mails nominais de
# professores/alunos (na dúvida, o padrão do app é marcar).
_EMAIL_FUNCTIONAL_SUFFIXES = (
    "gov.br", "leg.br", "jus.br", "mp.br", "def.br", "mil.br", "tc.br",
)

# Instituições fora dos sufixos públicos cujo e-mail é sempre de trabalho —
# recorrentes nos documentos reais (bancos federais e organismos
# internacionais). Ampliar aqui conforme aparecerem outros.
_EMAIL_FUNCTIONAL_DOMAINS = (
    "bb.com.br",      # Banco do Brasil
    "worldbank.org",  # Banco Mundial
    "iadb.org",       # BID
    "imf.org",        # FMI
)


def email_domain_is_functional(email: str) -> bool:
    """O domínio do e-mail é de órgão público (ou instituição da lista fixa)?

    True → e-mail funcional, fornecido pelo órgão: deve vir DESMARCADO na
    revisão por padrão, mesmo que a parte local seja o nome do servidor.
    """
    dominio = email.rsplit("@", 1)[-1].strip().lower().rstrip(".")
    if not dominio:
        return False
    for suf in _EMAIL_FUNCTIONAL_SUFFIXES + _EMAIL_FUNCTIONAL_DOMAINS:
        if dominio == suf or dominio.endswith("." + suf):
            return True
    return False


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def email_local_is_institutional(email: str) -> bool:
    """A parte local do e-mail (antes do @) parece de órgão/função, não pessoa?

    True → e-mail funcional (deve vir DESMARCADO na revisão).
    False → parece nome de pessoa ou é ambíguo (deve vir MARCADO).
    """
    local = _strip_accents(email.split("@", 1)[0].strip().lower())
    if not local:
        return False
    tokens = [re.sub(r"\d+", "", t) for t in re.split(r"[._\-]+", local)]
    tokens = [t for t in tokens if t]
    if not tokens:
        return False
    if any(t in _EMAIL_ORG_TOKENS for t in tokens):
        return True
    compact = re.sub(r"[^a-z]", "", local)
    if any(sub in compact for sub in _EMAIL_ORG_SUBSTRINGS):
        return True
    # Sigla: token único, curto e sem vogais (ex.: smf, cgm, pmv, dp).
    if len(tokens) == 1:
        t = tokens[0]
        if 2 <= len(t) <= 6 and not any(v in t for v in "aeiou"):
            return True
    return False


# ---------------------------------------------------------------------------
# Classificação pelo "display name" do cabeçalho: NOME <email>
# ---------------------------------------------------------------------------
# Em e-mails do SEI o remetente/destinatário aparece como `Exibição <endereço>`.
# O texto de exibição é um sinal muito mais forte que a parte local: se começa
# com um nome próprio (Título Case) é pessoa; se começa com sigla/CAIXA ALTA,
# cargo, ou tem barra de hierarquia (`MF/PGFN...`, `STN/SUDIP/CODIV`) é órgão.
# Olhamos só o PRIMEIRO token — assim "Alison de Oliveira Barcelos-GECOD-CODIV"
# (pessoa com sufixo de setor) continua sendo classificado como pessoa.
_DISPLAY_NAME_FIRST_RE = re.compile(
    r"^[A-ZÁÀÂÃÉÊÍÓÔÕÚÜÇ][a-záàâãéêíóôõúüç]+$"
)
# Cargos/funções (masc. e fem.) — usado aqui e no bloco de assinatura.
_CARGOS_PATTERN = (
    r"(?:Governador(?:a)?|Prefeit[oa]|Secret[áa]ri[oa]|Procurador(?:a)?|"
    r"Diretor(?:a)?|Coordenador(?:a)?|Presidente|Presidenta|Vice|Chefe|"
    r"Assessor(?:a)?|Gerente|Superintendente|Contador(?:a)?|"
    r"Controlador(?:a)?|Auditor(?:a)?|Ouvidor(?:a)?|Defensor(?:a)?|"
    r"Delegad[oa]|Promotor(?:a)?|Desembargador(?:a)?|"
    r"Engenheir[oa]|Arquitet[oa]|Analista|T[ée]cnic[oa]|Tesoureir[oa]|"
    r"Advogad[oa]|Assistente|Agente|Economista|Contabilista|Oficial|"
    r"Ju[ií]za?|Ministr[oa]|Senador(?:a)?|Deputad[oa]|Vereador(?:a)?|"
    r"Conselheir[oa]|Servidor(?:a)?|Respons[áa]vel|Titular)"
)
_CARGO_FULL_RE = re.compile(_CARGOS_PATTERN + r"$", re.IGNORECASE)


def _extract_email_display_name(text: str, start: int) -> str:
    """Extrai o texto de exibição imediatamente antes de `<email>` (ou "")."""
    prefix = text[:start]
    if not prefix.endswith("<"):
        return ""
    head = prefix[:-1]  # remove o '<'
    cut = max(head.rfind("\n"), head.rfind(">"), head.rfind(";"), head.rfind(":"))
    display = head[cut + 1:].strip().strip('"\'')
    return display[-90:]  # limita para não arrastar texto de campos anteriores


def _tem_vogal(token: str) -> bool:
    return any(v in _strip_accents(token).lower() for v in "aeiou")


def _is_org_word(token: str) -> bool:
    norm = _strip_accents(token).lower()
    return norm in _EMAIL_ORG_TOKENS or any(sub in norm for sub in _EMAIL_ORG_SUBSTRINGS)


def _looks_like_person_name(display: str) -> bool:
    """O texto inteiro parece um nome de pessoa (inclusive em CAIXA ALTA)?

    Ex.: "FERNANDO ANTONIO TENORIO", "MARIANA APARECIDA DA SILVA ABE".
    Rejeita se algum token for palavra de órgão, cargo, ou sigla sem vogal
    (ex.: "CONTRATOS ARRECADACAO ITAU", "CENOP NEG ST PUB EST").
    """
    tokens = [t for t in re.split(r"\s+", display.strip()) if t]
    sig = []
    for t in tokens:
        t2 = t.strip(".,;:()<>[]\"'-")
        low = _strip_accents(t2).lower()
        if not t2 or low in _CONECTORES:
            continue
        if len(t2) == 1 and t2.isalpha():   # inicial (ex.: "F")
            continue
        sig.append(t2)
    if len(sig) < 2:
        return False
    for t2 in sig:
        if not t2.replace("-", "").isalpha():
            return False
        if len(t2) < 3 or not _tem_vogal(t2):   # token curto/sem vogal = sigla
            return False
        if _is_org_word(t2) or _CARGO_FULL_RE.match(t2):
            return False
    return True


def _classify_display_name(display: str) -> str | None:
    """Classifica um texto de exibição: "personal", "institutional" ou None.

    Olha primeiro o PRIMEIRO token (nome próprio -> pessoa; sigla/cargo/órgão
    -> instituição), para que "Alison de Oliveira Barcelos-GECOD-CODIV"
    (pessoa com sufixo de setor) continue sendo pessoa.
    """
    display = (display or "").strip()
    if not display:
        return None
    if "/" in display:  # hierarquia de órgão (MF/PGFN..., STN/SUDIP/CODIV)
        return "institutional"
    m = re.search(r"\S+", display)
    if not m:
        return None
    first = m.group(0).strip(".,;:()<>[]\"'")
    if not first:
        return None
    if _CARGO_FULL_RE.match(first):            # "Governador AP", "Secretário ..."
        return "institutional"
    if _is_org_word(first):                    # "Prefeitura", "Gabinete", "GECOA"...
        return "institutional"
    if _DISPLAY_NAME_FIRST_RE.match(first):     # nome próprio Título Case -> pessoa
        return "personal"
    if _looks_like_person_name(display):        # nome de pessoa em CAIXA ALTA
        return "personal"
    core = first.replace("-", "").replace(".", "")
    if core.isalpha() and first.isupper() and len(core) >= 2:
        # sigla/acrônimo, inclusive encadeado (MF, STN, PGE, UEG-GEPAC-CONTROLE)
        return "institutional"
    return None


def email_display_verdict(text: str, start: int) -> str | None:
    """Classifica o e-mail pelo display name do cabeçalho `NOME <email>`."""
    return _classify_display_name(_extract_email_display_name(text, start))


def email_is_institutional(email: str, text: str = "", start: int = -1) -> bool:
    """Decide se o e-mail deve vir DESMARCADO (funcional/institucional) na revisão.

    Precedência: 1) domínio de órgão público/instituição conhecida — vence
    até display name de pessoa, porque um `Fulano <fulano@orgao.gov.br>` é
    e-mail FUNCIONAL (fornecido pelo órgão) mesmo sendo nominal; 2) display
    name do cabeçalho (`NOME <email>`); 3) heurística da parte local.
    Padrão seguro: na dúvida, MARCA (não-institucional).
    """
    if email_domain_is_functional(email):
        return True
    if text and start >= 0:
        verdict = email_display_verdict(text, start)
        if verdict == "personal":
            return False
        if verdict == "institutional":
            return True
    return email_local_is_institutional(email)


# ---------------------------------------------------------------------------
# Nomes de pessoas no cabeçalho de e-mail: "Fulano de Tal <email>"
# ---------------------------------------------------------------------------
# Em e-mails do SEI (De/Para) cada destinatário vem como "Nome <endereço>".
# O nome é PII e precisa ser tarjado — só o endereço não basta. Reaproveita
# _classify_display_name: só tarja quando o display é de pessoa (exclui GECOA,
# CENOP, "CONTRATOS ARRECADACAO ITAU", etc.).
_EMAIL_ANGLE_RE = re.compile(
    r"<\s*[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\s*>"
)


def _extract_names_from_email_headers(text):
    achados = []
    for m in _EMAIL_ANGLE_RE.finditer(text):
        lt = m.start()  # posição do '<'
        head = text[:lt]
        cut = max(head.rfind("\n"), head.rfind(">"), head.rfind(";"), head.rfind(":"))
        seg = text[cut + 1:lt]
        raw = seg.strip()
        if not raw or _classify_display_name(raw) != "personal":
            continue
        # exige >=2 tokens significativos (evita tarjar 1º nome comum isolado)
        sig = [p for p in re.split(r"\s+", raw)
               if len(p) > 1 and _strip_accents(p).lower() not in _CONECTORES]
        if len(sig) < 2:
            continue
        start = cut + 1 + (len(seg) - len(seg.lstrip()))
        achados.append((start, start + len(raw), raw))
    return achados


# ---------------------------------------------------------------------------
# Analyzer singleton
# ---------------------------------------------------------------------------
_analyzer: AnalyzerEngine | None = None
_analyzer_ner: AnalyzerEngine | None = None


def _build_analyzer(usar_ner: bool = False) -> AnalyzerEngine:
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "pt", "model_name": settings.spacy_model}],
        }
    )
    nlp_engine = provider.create_engine()
    registry = RecognizerRegistry(supported_languages=["pt"])
    registry.add_recognizer(CPFRecognizer())
    registry.add_recognizer(CNPJRecognizer())
    registry.add_recognizer(RGRecognizer())
    registry.add_recognizer(PhoneBRRecognizer())
    registry.add_recognizer(EmailRecognizer())
    if usar_ner:
        # NER de pessoas via spaCy (opt-in): mais cobertura, mais falso positivo.
        from presidio_analyzer.predefined_recognizers import SpacyRecognizer
        registry.add_recognizer(SpacyRecognizer(supported_language="pt"))
    return AnalyzerEngine(nlp_engine=nlp_engine, registry=registry, supported_languages=["pt"])


def get_analyzer(usar_ner: bool = False) -> AnalyzerEngine:
    global _analyzer, _analyzer_ner
    if usar_ner:
        if _analyzer_ner is None:
            _analyzer_ner = _build_analyzer(usar_ner=True)
        return _analyzer_ner
    if _analyzer is None:
        _analyzer = _build_analyzer(usar_ner=False)
    return _analyzer


# ---------------------------------------------------------------------------
# Heurística de nome próximo a CPF
# ---------------------------------------------------------------------------
_NAME_TOKEN = (
    r"(?:[A-ZÁÀÂÃÉÊÍÓÔÕÚÜÇ][A-Za-zÁÀÂÃÉÊÍÓÔÕÚÜÇáàâãéêíóôõúüç''\-]+"
    r"|[A-ZÁÀÂÃÉÊÍÓÔÕÚÜÇ]{2,})"
)
_NAME_CONN = r"(?:de|da|do|das|dos|e|di|del|von|van)"
_NAME_SEQ_RE = re.compile(
    rf"(?:{_NAME_TOKEN})(?:\s+(?:{_NAME_CONN}\s+)?{_NAME_TOKEN}){{1,5}}"
)
# Conectores que nunca podem iniciar um nome (ex.: após remover o stopword
# "Ministério" de "Ministério da Fazenda" sobra "da Fazenda" — inválido).
_CONECTORES = {"de", "da", "do", "das", "dos", "e", "di", "del", "von", "van"}
_NAME_STOPWORDS = {
    "CPF", "CNPJ", "RG", "IDENTIDADE", "PORTADOR", "PORTADORA", "NOME",
    "CLIENTE", "TITULAR", "REQUERENTE", "CONTRATANTE", "CONTRATADO",
    "SIGNATÁRIO", "ASSINANTE", "RESPONSÁVEL",
    "RUA", "AVENIDA", "PRAÇA", "BAIRRO", "CIDADE", "ESTADO", "UF", "CEP",
    "TELEFONE", "CELULAR", "EMAIL", "E-MAIL", "DATA", "NASCIMENTO",
    "BRASIL", "BRASILEIRO", "BRASILEIRA",
    "PREFEITURA", "MUNICÍPIO", "MUNICIPIO", "MUNICIPAL",
    "GOVERNO", "UNIÃO", "UNIAO", "REPÚBLICA", "REPUBLICA", "FEDERAL",
    "PODER", "EXECUTIVO", "LEGISLATIVO", "JUDICIÁRIO", "JUDICIARIO",
    "SECRETARIA", "MINISTÉRIO", "MINISTERIO", "CÂMARA", "CAMARA",
    "ASSEMBLEIA", "TRIBUNAL", "CONSELHO", "COMISSÃO", "COMISSAO",
    "DEPARTAMENTO", "DIRETORIA", "COORDENAÇÃO", "COORDENACAO",
    "AUTARQUIA", "FUNDAÇÃO", "FUNDACAO", "INSTITUTO", "AGÊNCIA", "AGENCIA",
    "RELATÓRIO", "RELATORIO", "DEMONSTRATIVO", "ANEXO", "TABELA",
    "ORÇAMENTO", "ORCAMENTO", "ORÇAMENTOS", "ORCAMENTOS",
    "FISCAL", "SEGURIDADE", "SOCIAL", "GESTÃO", "GESTAO",
    "EXERCÍCIO", "EXERCICIO", "PERÍODO", "PERIODO", "REFERÊNCIA", "REFERENCIA",
    "QUADRIMESTRE", "SEMESTRE", "TRIMESTRE", "BIMESTRE", "MÊS", "MES", "ANO",
    "LTDA", "ME", "EPP", "EIRELI", "MEI",
    "BANCO", "CAIXA", "COOPERATIVA", "CONSÓRCIO", "CONSORCIO",
    "EMPRESA", "COMPANHIA", "CORPORAÇÃO", "CORPORACAO",
}


def _extract_names_near_cpfs(text, cpf_starts, window=80):
    achados = []
    for pos in cpf_starts:
        ini_janela = max(0, pos - window)
        janela = text[ini_janela:pos]
        matches = list(_NAME_SEQ_RE.finditer(janela))
        if not matches:
            continue
        m = matches[-1]
        nome = m.group(0).strip(" ,;:\n\t-")
        palavras = [p for p in re.split(r"\s+", nome) if p]
        if not palavras or all(p.upper() in _NAME_STOPWORDS for p in palavras):
            continue
        while palavras and (palavras[0].upper() in _NAME_STOPWORDS
                            or palavras[0].lower() in _CONECTORES):
            palavras.pop(0)
        if len(palavras) < 2:
            continue
        nome = " ".join(palavras)
        offset_local = m.group(0).find(palavras[0])
        start = ini_janela + m.start() + max(offset_local, 0)
        end = start + len(nome)
        achados.append((start, end, nome))
    return achados


# ---------------------------------------------------------------------------
# Heurística: assinatura eletrônica (SEI, e-Proc, gov.br, ICP-Brasil, etc.)
# ---------------------------------------------------------------------------
# Casa o gatilho em português ("Assinado digitalmente/eletronicamente por
# NOME") e em inglês ("Signed by NOME"), seguido do nome. O nome pode terminar
# em vírgula (stamp do SEI), dois-pontos ou quebra de linha (selo gov.br /
# ICP-Brasil: "Assinado digitalmente por NOME\nDN: ...") ou fim do texto — por
# isso o terminador é um lookahead permissivo, não mais uma vírgula obrigatória.
# Como esta regex também compõe `_signature_regions`, todo nome capturado aqui
# (ou que caia sobre a região) vira origem "assinatura" e só é pré-marcado
# quando `tarjar_nomes_assinatura` está ligado no sidebar.
_ASSINATURA_ELETRONICA_RE = re.compile(
    r"(?:assinado\s+(?:eletronicamente|digitalmente|de\s+forma\s+(?:eletrônica|digital))\s+por"
    r"|signed\s+by)"
    r"[\s:]+"  # espaço e/ou ":" entre o gatilho e o nome ("... por: NOME")
    r"([A-ZÁÀÂÃÉÊÍÓÔÕÚÜÇ][A-ZÁÀÂÃÉÊÍÓÔÕÚÜÇ\s]+?)"
    r"(?:\s*-\s*Matr\.?\s*[\w-]+)?"  # sufixo opcional de matrícula (SEI): "- Matr.0319100-1"
    r"\s*(?=[,:;\n]|$)",  # termina em vírgula/dois-pontos/ponto-e-vírgula/quebra de linha/fim
    re.IGNORECASE,
)

# Nome da linha de assinatura: aceita tanto CAIXA ALTA ("VALMIR BARREIRA")
# quanto Título Case ("Elimar Rener Martines Lorenzon"), reaproveitando a
# mesma gramática de _NAME_SEQ_RE.
_BLOCO_ASSINATURA_RE = re.compile(
    rf"^[ \t]*({_NAME_SEQ_RE.pattern})[ \t]*$\s*"
    r"(?:" + _CARGOS_PATTERN + r")\b",  # \b evita casar "Procurador" em "Procuradoria"
    re.MULTILINE,
)

# Heurística: rodapé de plataformas de assinatura eletrônica (ex.: 1Doc)
# "Assinado por N pessoas: NOME1 e NOME2" / "NOME1, NOME2 e NOME3".
_ASSINADO_POR_PESSOAS_RE = re.compile(
    r"assinado\s+por\s+\d+\s+pessoas?\s*:\s*(.+?)(?=\n|Para\s+verificar|$)",
    re.IGNORECASE,
)
_PESSOA_SEP_RE = re.compile(r",\s*|\s+e\s+")

# Heurística: listas de verificação de assinatura com "NOME (CPF ...)"
# (ex.: página "Verificação das Assinaturas" do 1Doc).
_NOME_ANTES_CPF_PAREN_RE = re.compile(
    rf"({_NAME_SEQ_RE.pattern})\s*\(\s*CPF\b",
)


def _extract_names_from_signatures(text):
    """Aplica as heurísticas de nome em bloco de assinatura e agrega achados.

    A deduplicação usa (posição, texto exato) — não o nome normalizado em
    caixa alta — porque a mesma pessoa costuma aparecer em variações de
    caixa DIFERENTES em posições DIFERENTES da página (ex.: "Fulano de
    Tal" no bloco de assinatura e "FULANO DE TAL" no rodapé do SEI); cada
    uma é um texto literal distinto que precisa da sua própria tarja via
    `search_for`. Deduplicar por nome normalizado faria a heurística que
    roda depois "engolir" um achado legítimo só por ser a mesma pessoa.
    """
    achados = []
    seen = set()

    def _registra(start, end, nome):
        key = (start, nome)
        if key in seen:
            return
        seen.add(key)
        achados.append((start, end, nome))

    for m in _ASSINATURA_ELETRONICA_RE.finditer(text):
        nome = m.group(1).strip()
        palavras = [p for p in re.split(r"\s+", nome) if p]
        if not palavras or all(p.upper() in _NAME_STOPWORDS for p in palavras):
            continue
        if len(palavras) < 2:
            continue
        nome = " ".join(palavras)
        _registra(m.start(1), m.end(1), nome)

    for m in _BLOCO_ASSINATURA_RE.finditer(text):
        nome = m.group(1).strip()
        palavras = [p for p in re.split(r"\s+", nome) if p]
        if not palavras or all(p.upper() in _NAME_STOPWORDS for p in palavras):
            continue
        while palavras and (palavras[0].upper() in _NAME_STOPWORDS
                            or palavras[0].lower() in _CONECTORES):
            palavras.pop(0)
        if len(palavras) < 2:
            continue
        nome = " ".join(palavras)
        offset_local = m.group(1).find(palavras[0])
        start = m.start(1) + max(offset_local, 0)
        _registra(start, start + len(nome), nome)

    for m in _ASSINADO_POR_PESSOAS_RE.finditer(text):
        lista = m.group(1)
        base = m.start(1)
        for nome_bruto in _PESSOA_SEP_RE.split(lista):
            palavras = [p for p in re.split(r"\s+", nome_bruto.strip()) if p]
            if not palavras or all(p.upper() in _NAME_STOPWORDS for p in palavras):
                continue
            while palavras and palavras[0].upper() in _NAME_STOPWORDS:
                palavras.pop(0)
            if len(palavras) < 2:
                continue
            nome = " ".join(palavras)
            start = base + lista.find(nome_bruto.strip())
            _registra(start, start + len(nome), nome)

    for m in _NOME_ANTES_CPF_PAREN_RE.finditer(text):
        nome_bruto = m.group(1)
        palavras = [p for p in re.split(r"\s+", nome_bruto.strip()) if p]
        if not palavras or all(p.upper() in _NAME_STOPWORDS for p in palavras):
            continue
        while palavras and (palavras[0].upper() in _NAME_STOPWORDS
                            or palavras[0].lower() in _CONECTORES):
            palavras.pop(0)
        if len(palavras) < 2:
            continue
        nome = " ".join(palavras)
        offset_local = m.group(1).find(palavras[0])
        start = m.start(1) + max(offset_local, 0)
        _registra(start, start + len(nome), nome)

    return achados


# ---------------------------------------------------------------------------
# Regiões de assinatura (para classificar nomes do NER como "de assinatura")
# ---------------------------------------------------------------------------
# Linha composta só de traço de assinatura (____ ou ——); o nome/cargo costuma
# vir logo abaixo. Exige a linha inteira ser underline/travessão para não casar
# o sublinhado de campos de formulário ("Nome: ______").
_SIGN_UNDERLINE_RE = re.compile(r"^[ \t]*[_–—]{6,}[ \t]*$", re.MULTILINE)

# Linha que é essencialmente só um cargo (rótulo do bloco de assinatura):
# "Prefeito Municipal", "Engenheira Civil Municipal", "Secretário de Fazenda".
# O nome costuma vir nas linhas acima — às vezes com CREA/CPF/matrícula no
# meio, o que impede `_BLOCO_ASSINATURA_RE` (nome+cargo adjacentes) de casar.
# Limita o resto da linha a 60 chars para não pegar parágrafo do corpo que
# comece com uma palavra de cargo.
_CARGO_LINHA_RE = re.compile(
    r"^[ \t]*(?:" + _CARGOS_PATTERN + r")\b[^\n]{0,60}$",
    re.MULTILINE | re.IGNORECASE,
)

# Carimbo de assinatura digital (Adobe/gov.br): o timestamp "Dados: 2026.03.16
# 21:53:02 -03'00'" é exclusivo desses selos. Junto com "Assinado ... por",
# serve de âncora para marcar TODA a janela do carimbo como assinatura — dentro
# do selo o nome estilizado do signatário e o CPF dele ficam em linhas vizinhas,
# e o nome que o detector acha por proximidade de CPF precisa cair na região.
_SELO_TIMESTAMP_RE = re.compile(
    r"Dados:\s*\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2}",
)


def _fim_de_n_linhas(text: str, pos: int, n: int = 3) -> int:
    """Offset do fim da n-ésima linha a partir de `pos` (ou fim do texto)."""
    end = pos
    for _ in range(n):
        nl = text.find("\n", end)
        if nl == -1:
            return len(text)
        end = nl + 1
    return end


def _inicio_de_n_linhas_antes(text: str, pos: int, n: int = 3) -> int:
    """Offset do início da linha que está n linhas acima da linha de `pos`."""
    start = text.rfind("\n", 0, pos)
    if start == -1:
        return 0
    for _ in range(n):
        nl = text.rfind("\n", 0, start)
        if nl == -1:
            return 0
        start = nl
    return start + 1


def _signature_regions(text: str) -> list[tuple[int, int]]:
    """Intervalos de caractere que são bloco de assinatura.

    Reúne as heurísticas de `_extract_names_from_signatures` (agora guardando
    a região inteira, não só o nome), a linha-traço de assinatura seguida de
    nome/cargo, e uma linha de cargo com as linhas acima (onde o nome costuma
    estar). Serve para classificar um nome achado pelo NER como "de assinatura"
    quando o span dele cai dentro de uma dessas regiões.
    """
    regioes: list[tuple[int, int]] = []
    for rx in (_BLOCO_ASSINATURA_RE,
               _ASSINADO_POR_PESSOAS_RE, _NOME_ANTES_CPF_PAREN_RE):
        for m in rx.finditer(text):
            regioes.append((m.start(), m.end()))
    # Carimbo digital: janela de linhas ao redor da âncora ("Assinado ... por"
    # ou o timestamp "Dados: ..."), pois o nome estilizado e o CPF do signatário
    # ficam em linhas vizinhas DENTRO do mesmo selo (senão o nome pego por
    # proximidade de CPF viria marcado mesmo sendo só assinatura).
    for rx in (_ASSINATURA_ELETRONICA_RE, _SELO_TIMESTAMP_RE):
        for m in rx.finditer(text):
            regioes.append((_inicio_de_n_linhas_antes(text, m.start(), 4),
                            _fim_de_n_linhas(text, m.end(), 3)))
    for m in _SIGN_UNDERLINE_RE.finditer(text):
        regioes.append((m.start(), _fim_de_n_linhas(text, m.end(), 3)))
    for m in _CARGO_LINHA_RE.finditer(text):
        regioes.append((_inicio_de_n_linhas_antes(text, m.start(), 3), m.end()))
    return regioes


def _span_em_assinatura(start: int, end: int,
                        regioes: list[tuple[int, int]]) -> bool:
    return any(start < r_fim and end > r_ini for r_ini, r_fim in regioes)


# ---------------------------------------------------------------------------
# Modelos de saída
# ---------------------------------------------------------------------------
@dataclass
class Occurrence:
    pagina: int
    inicio: int
    fim: int
    texto: str
    score: float
    # True quando o span cai dentro de um bloco de assinatura.
    em_assinatura: bool = False
    # Retângulo (x0, y0, x1, y1 em pontos PDF) da ocorrência, quando ela não
    # tem texto pesquisável na página — caso dos selos de assinatura (imagem).
    # Para as demais entidades fica None: o rect vem de `page.search_for`.
    rect: tuple[float, float, float, float] | None = None


@dataclass
class EntityGroup:
    tipo: str
    texto: str
    score: float
    ocorrencias: list[Occurrence] = field(default_factory=list)
    # Estado inicial do checkbox "Tarjar?" na revisão. Normalmente True; para
    # e-mails institucionais (gabinete@, secretaria@, siglas) vem False.
    tarjar_default: bool = True
    # Como a entidade foi detectada (ex.: "Regex", "Assinatura", "NER (IA)").
    origem: str = ""
    # True quando TODAS as ocorrências do nome estão em bloco de assinatura.
    em_assinatura: bool = False
    # E-mail funcional/institucional (domínio de órgão público, caixa
    # institucional tipo gabinete@, display name de órgão). Guardado no grupo
    # para a UI poder reaplicar o pré-marcado quando o usuário alterna a flag
    # "Tarjar e-mails funcionais" — sem reanálise (o contexto de display name
    # não existe mais fora da detecção).
    institucional: bool = False

    @property
    def paginas(self) -> list[int]:
        return sorted({o.pagina for o in self.ocorrencias})


# ---------------------------------------------------------------------------
# Detecção
# ---------------------------------------------------------------------------
def _resolve_presidio_entities(displays):
    out = set()
    for d in displays:
        out.update(DISPLAY_TO_PRESIDIO.get(d, []))
    return sorted(out)


# Origem de cada nome (para a coluna "Origem" e para o toggle de assinatura).
_ORIGEM_LABEL = {
    "proximo_cpf": "Próximo a CPF",
    "cabecalho_email": "Cabeçalho de e-mail",
    "assinatura": "Assinatura",
    "ner_bert": "NER jurídico (BERT)",
    "ner": "NER (spaCy)",
}
_ORIGEM_PRECEDENCIA = ["proximo_cpf", "cabecalho_email", "assinatura",
                       "ner_bert", "ner"]


def _origem_display(origens: set) -> str:
    for o in _ORIGEM_PRECEDENCIA:
        if o in origens:
            return _ORIGEM_LABEL[o]
    return ""


def _eh_caixa_alta(s: str) -> bool:
    """True se `s` tem letras e está todo em MAIÚSCULAS."""
    return s == s.upper() and s != s.lower()


def _limpa_nome(raw: str) -> str:
    """Normaliza um nome bruto (do NER): remove stopwords/conectores das pontas.

    Retorna "" se não sobrar um nome plausível (>=2 palavras, sem dígitos).
    """
    palavras = [p for p in re.split(r"\s+", raw.strip()) if p]
    if not palavras or all(p.upper() in _NAME_STOPWORDS for p in palavras):
        return ""
    while palavras and (palavras[0].upper() in _NAME_STOPWORDS
                        or palavras[0].lower() in _CONECTORES):
        palavras.pop(0)
    while palavras and (palavras[-1].upper() in _NAME_STOPWORDS
                        or palavras[-1].lower() in _CONECTORES):
        palavras.pop()
    if len(palavras) < 2:
        return ""
    if any(ch.isdigit() for p in palavras for ch in p):
        return ""
    return " ".join(palavras)


def detect_entities(pdf_bytes: bytes, tipos_display: Iterable[str],
                    usar_ner: bool = False,
                    usar_ner_bert: bool = False,
                    sempre_heuristicas: bool = False,
                    tarjar_nomes_assinatura: bool = True,
                    on_progress: Callable[[int, int], None] | None = None,
                    on_warning: Callable[[str], None] | None = None,
                    on_status: Callable[[str], None] | None = None) -> list[EntityGroup]:
    # Materializa e guarda os tipos pedidos (Celular/Telefone precisam saber
    # quais dos dois o usuário realmente quer, já que os dois compartilham a
    # mesma entidade PHONE_NUMBER_BR no Presidio — ver _classify_phone_br).
    tipos_display = list(tipos_display)
    tipos_pedidos = set(tipos_display)
    presidio_entities = _resolve_presidio_entities(tipos_display)
    if not presidio_entities:
        return []

    quer_nome = "PERSON" in presidio_entities
    entidades_analise = set(presidio_entities)
    if quer_nome:
        entidades_analise.add("CPF")
    # CPF/CNPJ rodam SEMPRE que se procura telefone — mesmo que o usuário não
    # tenha pedido esses tipos. Não é para exibi-los (o filtro por
    # `presidio_entities` mais abaixo continua valendo), e sim para poder
    # descartar o telefone que na verdade É um CPF/CNPJ: ver `_doc_spans`.
    if "PHONE_NUMBER_BR" in presidio_entities:
        entidades_analise.update(("CPF", "CNPJ"))

    # Com IA (spaCy ou BERT) ligada, as heurísticas de nome (proximidade a
    # CPF, bloco de assinatura, cabeçalho de e-mail) tendem a só somar falso
    # positivo ao que a IA já acerta sozinha — por padrão ficam desligadas
    # nesse caso. "Sempre considerar heurísticas" restaura o comportamento
    # combinado de antes.
    usa_ia_nome = usar_ner or usar_ner_bert
    roda_heuristicas = not usa_ia_nome or sempre_heuristicas

    # BERT: aquece o modelo ANTES da varredura. Se ele não puder ser carregado
    # (Hub lento/fora do ar, sem rede, memória insuficiente), o app NÃO trava
    # nem quebra: segue sem a IA e liga as heurísticas de nome no lugar —
    # detecção pior, mas funcionando — e avisa quem chamou.
    if usar_ner_bert and quer_nome:
        from ._ner_bert import garantir_disponivel
        if on_status is not None:
            # Sem isso a barra ficaria parada em "Analisando o documento..."
            # enquanto o modelo carrega — parece travamento. Assim que o modelo
            # fica pronto, o progresso por página assume.
            on_status("Carregando o modelo BERTimbau...")
        problema = garantir_disponivel()
        if problema:
            usar_ner_bert = False
            usa_ia_nome = usar_ner
            roda_heuristicas = True
            if on_warning is not None:
                on_warning(
                    f"A detecção de nomes com IA foi desligada nesta análise: "
                    f"{problema}. As heurísticas de nome (proximidade a CPF, "
                    f"blocos de assinatura, cabeçalho de e-mail) foram usadas "
                    f"no lugar — revise os nomes com atenção redobrada."
                )

    analyzer = get_analyzer(usar_ner and quer_nome)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    grupos: dict[tuple[str, str], EntityGroup] = {}
    origens_por_nome: dict[str, set] = {}
    spans_registrados: set[tuple[int, int, str]] = set()

    sig_regions: list[tuple[int, int]] = []

    def _add_nome(nome, pnum, start, end, score, origem):
        em_ass = origem == "assinatura" or _span_em_assinatura(start, end, sig_regions)
        # Agrupa a mesma pessoa em grafias de caixa diferentes ("FULANO DE TAL"
        # e "Fulano de Tal") numa linha só — a busca da tarja já é
        # caixa-insensível, então separá-las só confunde a revisão.
        norm = nome.casefold()
        span_key = (start, end, "PERSON")
        if span_key in spans_registrados:
            # já registrado nesta página por origem anterior — só acumula origem
            origens_por_nome.setdefault(norm, set()).add(origem)
            if em_ass:
                origens_por_nome[norm].add("assinatura")
            return
        spans_registrados.add(span_key)
        key = ("Nome", norm)
        grupo = grupos.get(key)
        if grupo is None:
            grupo = EntityGroup(tipo="Nome", texto=nome, score=score)
            grupos[key] = grupo
        elif _eh_caixa_alta(grupo.texto) and not _eh_caixa_alta(nome):
            # prefere exibir a grafia em title case ("Fulano de Tal")
            grupo.texto = nome
        grupo.score = max(grupo.score, score)
        grupo.ocorrencias.append(
            Occurrence(pagina=pnum, inicio=start, fim=end, texto=nome, score=score,
                       em_assinatura=em_ass)
        )
        origens_por_nome.setdefault(norm, set()).add(origem)
        if em_ass:
            origens_por_nome[norm].add("assinatura")

    try:
        for pnum, page in enumerate(doc):
            if on_progress is not None:
                on_progress(pnum + 1, doc.page_count)
            text = page.get_text()
            if not text.strip():
                continue

            results = analyzer.analyze(text=text, language="pt", entities=sorted(entidades_analise))
            spans_registrados = set()

            # Trechos desta página que são OUTRO documento, não telefone. Um
            # candidato a telefone que caia em cima de um deles é descartado:
            # - CPF/CNPJ: validados por dígito verificador (dois dígitos que só
            #   fecham para o número certo), enquanto "telefone" é só formato +
            #   faixa de DDD. Um CPF sem pontuação ("49901699649") tem 11
            #   dígitos e passa por celular de DDD 49 — entre as duas leituras,
            #   a de validação mais forte vence.
            # - Protocolo/NUP ("17944.000464/2026-14"): o formato inteiro é
            #   inequívoco, mas a regex de telefone recorta só o começo dele.
            #   Por isso o casamento é feito aqui, no texto completo.
            _doc_spans = [(r.start, r.end) for r in results
                          if r.entity_type in ("CPF", "CNPJ")]
            _doc_spans += [(m.start(), m.end())
                           for m in _PROTOCOLO_NUP_RE.finditer(text)]

            def _sobrepoe_documento(ini, fim):
                return any(ini < d_fim and d_ini < fim
                           for d_ini, d_fim in _doc_spans)

            for r in results:
                if r.entity_type not in presidio_entities:
                    continue
                if r.entity_type == "PERSON":
                    continue  # nomes tratados pelas heurísticas + NER abaixo
                if r.entity_type == "PHONE_NUMBER_BR":
                    if _looks_like_document_id(text, r.start):
                        continue
                    if _sobrepoe_documento(r.start, r.end):
                        continue
                trecho = text[r.start:r.end].strip()
                if not trecho:
                    continue
                if r.entity_type == "PHONE_NUMBER_BR":
                    # Celular x Telefone (fixo) pelo tipo real do número
                    # (libphonenumber), não pelo "começa com 9" da regex; e
                    # descarta o que não validar como telefone de verdade.
                    display = _classify_phone_br(trecho)
                    if display is None or display not in tipos_pedidos:
                        continue
                else:
                    display = PRESIDIO_TO_DISPLAY.get(r.entity_type, r.entity_type)
                span_key = (r.start, r.end, r.entity_type)
                if span_key in spans_registrados:
                    continue
                spans_registrados.add(span_key)
                key = (display, trecho)
                grupo = grupos.get(key)
                if grupo is None:
                    grupo = EntityGroup(tipo=display, texto=trecho, score=r.score,
                                        origem="Regex")
                    if r.entity_type == "EMAIL_ADDRESS":
                        grupo.institucional = email_is_institutional(
                            trecho, text, r.start)
                        grupo.tarjar_default = not grupo.institucional
                    grupos[key] = grupo
                grupo.score = max(grupo.score, r.score)
                grupo.ocorrencias.append(
                    Occurrence(pagina=pnum, inicio=r.start, fim=r.end, texto=trecho, score=r.score)
                )

            if quer_nome:
                # Regiões de assinatura são calculadas sempre (mesmo com as
                # heurísticas de nome desligadas), pois servem para classificar
                # os nomes achados pelo NER como "de assinatura".
                sig_regions = _signature_regions(text)
                if roda_heuristicas:
                    # 1. Nomes próximos a CPF (sinal forte)
                    id_starts = [r.start for r in results if r.entity_type == "CPF"]
                    for start, end, nome in _extract_names_near_cpfs(text, id_starts):
                        _add_nome(nome, pnum, start, end, 0.85, "proximo_cpf")

                    # 2. Nomes em blocos de assinatura (SEI, etc.)
                    for start, end, nome in _extract_names_from_signatures(text):
                        _add_nome(nome, pnum, start, end, 0.80, "assinatura")

                    # 3. Nomes no cabeçalho de e-mail (Nome <email>)
                    for start, end, nome in _extract_names_from_email_headers(text):
                        _add_nome(nome, pnum, start, end, 0.80, "cabecalho_email")

                # 4. NER spaCy (opt-in)
                if usar_ner:
                    for r in results:
                        if r.entity_type != "PERSON":
                            continue
                        raw = text[r.start:r.end]
                        nome = _limpa_nome(raw)
                        if not nome:
                            continue
                        off = raw.find(nome.split()[0])
                        start = r.start + max(off, 0)
                        _add_nome(nome, pnum, start, start + len(nome), r.score, "ner")

                # 5. NER BERT jurídico / LeNER-Br (opt-in, carregado sob demanda)
                if usar_ner_bert:
                    from ._ner_bert import detect_person_names_bert
                    for start, end, nome, score in detect_person_names_bert(text):
                        _add_nome(nome, pnum, start, end, score, "ner_bert")
    finally:
        doc.close()

    # Finaliza os grupos de Nome: define a origem exibida e marca os que só
    # aparecem em assinatura. Nomes 100% de assinatura vêm desmarcados quando
    # `tarjar_nomes_assinatura` está desligado (pedido da área de negócio).
    for (_display, norm), grupo in grupos.items():
        if grupo.tipo != "Nome":
            continue
        grupo.origem = _origem_display(origens_por_nome.get(norm, set()))
        if grupo.ocorrencias and all(o.em_assinatura for o in grupo.ocorrencias):
            grupo.em_assinatura = True
            if not tarjar_nomes_assinatura:
                grupo.tarjar_default = False

    return sorted(grupos.values(), key=lambda g: (g.tipo, g.texto.lower()))
