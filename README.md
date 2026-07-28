# Tarjador Desktop — downloads

Instaladores Windows do **Tarjador** ([tarjador.ia.br](https://tarjador.ia.br)): remoção de dados pessoais (CPF, nomes, e-mails, selos de assinatura) de PDFs, em conformidade com a LGPD e a LAI.

**O documento nunca sai da sua máquina** — o aplicativo processa tudo localmente, escuta apenas em `127.0.0.1` e funciona sem internet.

➡️ **[Baixar a versão mais recente](https://github.com/mariosalaroli/tarjador-releases/releases/latest)**

| Edição | Arquivo | Tamanho | Conteúdo |
|---|---|---|---|
| Leve | `TarjadorSetup-Leve.exe` | 143 MB | detecção completa + OCR de selos, sem IA |
| Completa | `TarjadorSetup-Completa.exe` | 648 MB | tudo da Leve + IA de nomes (BERTimbau jurídico) offline |

Requisitos: Windows 10/11, 64 bits. Instalar uma edição por cima da outra troca a edição. Verifique os downloads com o `SHA256SUMS.txt` de cada release.

> ⚠️ Os executáveis ainda não são assinados digitalmente; o SmartScreen pode avisar na primeira execução (**Mais informações → Executar assim mesmo**).

Projeto sem fins lucrativos, feito para a administração pública brasileira.

## Licença

Software livre sob **GNU Affero General Public License v3.0** — copyright © 2026
Mario Salaroli. Você pode usar, estudar, modificar e redistribuir; versões
modificadas, inclusive as oferecidas como serviço em rede, devem manter o
código-fonte disponível.

Cada instalação traz o `LICENSE`, o `THIRD-PARTY-NOTICES.txt` e a pasta
`licenses/` com os textos integrais das licenças dos componentes de terceiros
(Tesseract, pdf.js, spaCy, PyTorch e outros).
