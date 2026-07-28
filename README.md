# Tarjador — código-fonte e downloads

App para remoção de dados pessoais (CPF, nomes, e-mails, selos de assinatura) de
PDFs, em conformidade com a LGPD e a LAI. Projeto sem fins lucrativos, feito
para a administração pública brasileira.

Use direto no navegador em [tarjador.ia.br](https://tarjador.ia.br), ou instale
a versão desktop (documento nunca sai da sua máquina) — veja abaixo.

Este repositório é também o **código-fonte correspondente**, exigido pela
licença AGPL-3.0, do que roda em [tarjador.ia.br](https://tarjador.ia.br) e no
espelho [tarjador.streamlit.app](https://tarjador.streamlit.app): a árvore
abaixo (`app.py`, `tarjador/`, `desktop/`) é a mesma coisa que está hospedada,
sem os binários de terceiros que cada ambiente baixa/monta à parte (Tesseract,
pesos do BERT).

## Downloads (Windows)

➡️ **[Baixar a versão mais recente](https://github.com/mariosalaroli/tarjador-releases/releases/latest)**

| Edição | Arquivo | Tamanho | Conteúdo |
|---|---|---|---|
| Leve | `TarjadorSetup-Leve.exe` | 143 MB | detecção completa + OCR de selos, sem IA |
| Completa | `TarjadorSetup-Completa.exe` | 648 MB | tudo da Leve + IA de nomes (BERTimbau jurídico) offline |

Requisitos: Windows 10/11, 64 bits. Instalar uma edição por cima da outra troca a edição. Verifique os downloads com o `SHA256SUMS.txt` de cada release.

> ⚠️ Os executáveis ainda não são assinados digitalmente; o SmartScreen pode avisar na primeira execução (**Mais informações → Executar assim mesmo**).

Detalhes de build do desktop (as duas edições, empacotamento, etc.) estão em
[desktop/README.md](desktop/README.md).

## O que é detectado

- **CPF / CNPJ** — regex com validação dos dígitos verificadores
- **RG** — regex + palavra de contexto obrigatória (RG, identidade, SSP/órgão expedidor) na vizinhança, já que não existe padrão nacional de formato; opcional (desmarcado por padrão, como CNPJ e Telefone)
- **Celular / Telefone** — reconhece o número em qualquer máscara e valida de verdade com `phonenumbers` (libphonenumber): confere se o DDD existe e se o formato bate com o plano de numeração brasileiro
- **E-mail** — a tarja cobre por padrão só a parte antes do `@` (domínio não é dado pessoal); endereços institucionais/funcionais vêm listados mas desmarcados por padrão
- **Nomes** — por padrão via IA (BERTimbau/BERT afinado em textos jurídicos, LeNER-Br), com heurísticas de texto (proximidade a CPF, blocos de assinatura) como alternativa ou complemento configurável
- **Selos de assinatura digital** (gov.br/ICP-Brasil e similares), quando são imagem — via OCR (Tesseract), opcional

Tipos a detectar, uso de IA e demais opções ficam configuráveis na barra lateral.

## Segurança e privacidade

- **Tarja real** — o conteúdo é removido do stream do PDF (`apply_redactions` do PyMuPDF), não é um retângulo por cima; não dá para recuperar via copiar/colar ou extração de texto
- **Metadados limpos** — título, autor, ferramenta e datas de criação/edição são apagados do PDF gerado
- **Processamento local** — nada é enviado para serviços de terceiros; a análise roda inteiramente no servidor que hospeda o app (ou na sua máquina, na versão desktop)
- **Nada gravado em disco** — o PDF trafega e é processado em memória (bytes em RAM/`BytesIO`, sem arquivo temporário no servidor); ao fim da sessão, o conteúdo é descartado

## Estrutura

```
app.py                 # UI Streamlit (upload, tabela de revisão, PDF interativo, download)
tarjador/core/
├── validator.py        # valida tamanho, senha, texto extraível
├── detector.py          # fachada dos detectores (regex BR + Presidio + spaCy/BERT)
├── _detector_full.py    # implementação da detecção (CPF/CNPJ/telefone/e-mail/nome)
├── _ner_bert.py          # NER com BERTimbau (LeNER-Br)
├── redactor.py           # PyMuPDF: tarja real + limpeza de metadados
└── pipeline.py            # orquestra analyze() / apply_redactions()
tarjador/api/           # API HTTP (FastAPI) equivalente, com autenticação por chave —
                        # não deployada (código pronto, roda à parte via uvicorn)
desktop/                # empacotamento Windows (PyInstaller + Inno Setup) — ver desktop/README.md
```

## Como rodar localmente

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Requer Tesseract instalado (`packages.txt` lista `tesseract-ocr` + `tesseract-ocr-por`) se for usar a detecção de selos de assinatura.

## Limitações conhecidas

- **Endereço não está no escopo** desta versão (depende de NER pouco confiável)
- Nomes dependem de NER; pode haver falso positivo/negativo — revise na tabela antes de aplicar
- Limite de tamanho de arquivo configurável via variável de ambiente `MAX_FILE_SIZE_MB` (padrão 200MB)

## Licença

Software livre sob **GNU Affero General Public License v3.0** — copyright © 2026
Mario Salaroli (ver [LICENSE](LICENSE)). Você pode usar, estudar, modificar e
redistribuir; versões modificadas, inclusive as oferecidas como serviço em
rede, devem manter o código-fonte disponível (cláusula 13).

`THIRD-PARTY-NOTICES.txt` e `desktop/vendor/licenses/` trazem os créditos e
textos integrais das licenças dos componentes de terceiros (Tesseract, pdf.js,
spaCy, PyTorch e outros).
