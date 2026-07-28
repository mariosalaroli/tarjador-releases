---
title: Tarjador de PDFs
emoji: 🔒
colorFrom: gray
colorTo: indigo
sdk: docker
app_port: 8501
pinned: false
---

# Tarjador de PDFs

App Streamlit para tarjar (redigir) informações pessoais (LGPD) em PDFs: envie um documento, revise as informações pessoais encontradas numa tabela sincronizada com o PDF, marque/desmarque o que tarjar e baixe a versão final.

## Como usar

1. Envie um PDF
2. Revise a tabela de informações pessoais encontradas — marque/desmarque cada uma, ou clique diretamente numa informação destacada no PDF pra tarjar/desfazer
3. Clique em **Aplicar tarjas e baixar** e baixe o PDF tarjado

## O que é detectado

- **CPF / CNPJ** — regex com validação dos dígitos verificadores
- **RG** — regex + palavra de contexto obrigatória (RG, identidade, SSP/órgão expedidor) na vizinhança, já que não existe padrão nacional de formato; opcional (desmarcado por padrão, como CNPJ e Telefone)
- **Celular / Telefone** — reconhece o número em qualquer máscara e valida de verdade com `phonenumbers` (libphonenumber): confere se o DDD existe e se o formato bate com o plano de numeração brasileiro (é isso que separa Celular de Telefone, não só "começa com 9")
- **E-mail** — a tarja cobre por padrão só a parte antes do `@` (domínio não é dado pessoal); endereços institucionais/funcionais (`gabinete@`, `secretaria@`, etc.) vêm listados mas desmarcados por padrão
- **Nomes** — por padrão via IA (BERTimbau/BERT afinado em textos jurídicos, LeNER-Br), com heurísticas de texto (proximidade a CPF, blocos de assinatura) como alternativa ou complemento configurável
- **Selos de assinatura digital** (gov.br/ICP-Brasil e similares), quando são imagem — via OCR (Tesseract), opcional (mais lento)

Tipos a detectar, uso de IA e demais opções ficam configuráveis na barra lateral.

## Segurança e privacidade

- **Tarja real** — o conteúdo é removido do stream do PDF (`apply_redactions` do PyMuPDF), não é um retângulo por cima; não dá para recuperar via copiar/colar ou extração de texto
- **Metadados limpos** — título, autor, ferramenta e datas de criação/edição são apagados do PDF gerado
- **Processamento local** — nada é enviado para serviços de terceiros; a análise roda inteiramente no servidor que hospeda o app
- **Nada gravado em disco** — o PDF trafega e é processado em memória (bytes em RAM/`BytesIO`, sem arquivo temporário no servidor); ao fim da sessão (aba fechada ou processo reiniciado), o conteúdo é descartado

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
- Limite de tamanho de arquivo configurável via variável de ambiente `MAX_FILE_SIZE_MB` (padrão 200MB, o mesmo teto do `st.file_uploader`)

## Licença

Tarjador — remoção de dados pessoais em documentos PDF
Copyright (C) 2026 Mario Salaroli

Este programa é software livre: você pode redistribuí-lo e/ou modificá-lo sob os
termos da **GNU Affero General Public License, versão 3** (ver [LICENSE](LICENSE)),
conforme publicada pela Free Software Foundation.

Ele é distribuído na esperança de que seja útil, mas **sem nenhuma garantia** —
sem sequer a garantia implícita de comerciabilidade ou adequação a uma finalidade
específica.

A AGPL é a licença exigida pelo [PyMuPDF](https://github.com/pymupdf/PyMuPDF),
que é o motor de leitura e tarja de PDF do projeto: o copyleft alcança a obra
combinada. Na prática isso significa que quem usar o Tarjador — inclusive como
serviço em rede — deve manter o código correspondente disponível.

Os componentes de terceiros incluídos na distribuição e suas licenças estão em
[THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt), gerado por
`desktop/gera_notices.py`. Destaques de atribuição obrigatória: o modelo de
português do spaCy (`pt_core_news_sm`, Explosion AI) é CC BY-SA 4.0, e Tesseract,
pdf.js, Streamlit, PyTorch e Transformers são Apache 2.0.
