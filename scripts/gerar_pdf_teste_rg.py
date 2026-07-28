"""Gera `teste_rg.pdf` (na raiz do projeto, gitignored) para avaliação manual
do RGRecognizer: RGs em formatos variados COM contexto (devem ser detectados),
controles negativos SEM contexto (não devem), e CIN/CPF (devem sair como CPF).

Uso:  python scripts/gerar_pdf_teste_rg.py
"""
from pathlib import Path

import fitz  # PyMuPDF

LINHAS = [
    ("t", "Documento de teste — detecção de RG"),
    ("", ""),
    ("h", "Seção A — RGs com contexto (TODOS devem ser detectados)"),
    ("", "1. O requerente apresentou o RG nº 12.345.678-2, SSP/SP, em cópia autenticada."),
    ("", "2. RG: 45.678.912-1 SSP/SP, conforme documento anexo aos autos."),
    ("", "3. Portador do RG 10.000.006-X, expedido pela SSP/SP em 12/03/1998."),
    ("", "4. Carteira de Identidade nº 1.234.567 SSP/BA, emitida em Salvador."),
    ("", "5. Registro Geral 9.876.543-2, órgão expedidor SSP/MG."),
    ("", "6. Apresentou identidade nº 12345678 IFP/RJ no ato da assinatura."),
    ("", "7. RG 7654321 SESP/PR, natural de Curitiba."),
    ("", "8. Documento de identidade: 8.765.432-1, segunda via."),
    ("", "9. R.G. 23.456.789-0 IGP/RS, conforme certidão."),
    ("", "10. Cédula de identidade 3.456.789 PC/MG, expedida em Belo Horizonte."),
    ("", "11. Nome: João da Silva — RG: 123456789 — CPF: 111.444.777-35."),
    ("", "12. A portadora da identidade 98.765.432 SEJUSP/MS compareceu ao ato."),
    ("", "13. RG 34.998.152-X SSP/SP (formato com X, DV de outro estado)."),
    ("", ""),
    ("h", "Seção B — controles negativos (NADA deve ser marcado nesta seção)"),
    ("", "Os itens abaixo são valores administrativos comuns em documentos públicos."),
    ("", "1. O imóvel está avaliado em R$ 1.234.567,89 conforme laudo."),
    ("", "2. Endereço: Rua das Flores, 100 — CEP 83.005-340, Curitiba/PR."),
    ("", "3. Correspondência para a caixa postal, CEP: 80010000."),
    ("", "4. Processo SEI nº 0023634429, em tramitação."),
    ("", "5. Atendimento registrado sob protocolo 123456789."),
    ("", "6. Servidor de matrícula 7654321, lotado na secretaria."),
    ("", "7. Autos do processo nº 0001234-56.2026.8.26.0100 (numeração CNJ)."),
    ("", "8. Contato: (46) 99911-2233, horário comercial."),
    ("", "9. Despesa empenhada na nota 2026NE000123."),
    ("", "10. Licitação regida pela Lei nº 8.666, de 21 de junho de 1993."),
    ("", "11. Documento conferido pelo código verificador 987654321."),
    ("", "12. A referência 12.345.678-9 consta do anexo técnico citado acima."),
    ("", ""),
    ("h", "Seção C — CIN e CPF (devem aparecer como CPF, nunca como RG)"),
    ("", "1. Portador da CIN (novo RG) nº 529.982.247-25, emitida em 2024."),
    ("", "2. Identificado pelo RG 111.444.777-35 (documento no padrão novo, usa o CPF)."),
    ("", "3. CPF 529.982.247-25, inscrito na Receita Federal."),
    ("", ""),
    ("h", "Seção D — casos-limite (NÃO devem ser detectados)"),
    ("", "1. RG 0023634429 (dez dígitos — tamanho não é de RG)."),
    ("", "2. RG 1111111 (dígitos todos iguais)."),
    ("", "3. RG 123.456 (seis dígitos — curto demais)."),
    ("p", ""),  # quebra de página
    ("h", "Seção E — repetições (grupos com MÚLTIPLAS ocorrências)"),
    ("", "1. Confirma-se o RG nº 12.345.678-2 SSP/SP já citado na Seção A"),
    ("", "   (mesmo número: o grupo deve listar mais de uma página)."),
    ("", "2. O RG 45.678.912-1 SSP/SP foi reapresentado no balcão."),
    ("", "3. E novamente o mesmo: RG 45.678.912-1 SSP/SP, em outra linha"),
    ("", "   (três ocorrências no total: uma na Seção A, duas nesta)."),
    ("", "4. CPF 111.444.777-35 repetido aqui também (Seções A e E)."),
    ("", "5. Já o número 12.345.678-2 nesta frase vem sem palavras que o marquem"),
    ("", "   (deve ficar de fora: mesma string do item 1, sem contexto)."),
    ("p", ""),  # quebra de página
    ("h", "Seção F — nome no corpo E na assinatura (teste da flag)"),
    ("", "Com \"Tarjar nomes em assinaturas\" DESLIGADO, o nome abaixo deve ser"),
    ("", "tarjado só no parágrafo; a assinatura no rodapé fica visível."),
    ("", "LIGADO, tarja nos dois lugares."),
    ("", ""),
    ("", "Encaminho o requerimento de Maria Oliveira Santos, CPF 390.533.447-05,"),
    ("", "para as providências cabíveis, conforme despacho anterior."),
    ("", "O processo seguirá o rito ordinário previsto no regimento interno."),
    ("", "Nada mais havendo a tratar, encerra-se o presente expediente."),
    ("", "Publique-se e cumpra-se na forma da legislação vigente."),
    ("", ""),
    ("", "Atenciosamente,"),
    ("", ""),
    ("", ""),
    ("", "Maria Oliveira Santos"),
    ("", "Secretária Municipal de Administração"),
]

MARGEM_X = 50
Y_INICIO = 60
ALTURA_LINHA = 18


def main() -> None:
    doc = fitz.open()
    page = doc.new_page()  # A4
    y = Y_INICIO
    for estilo, texto in LINHAS:
        if estilo == "p":
            page = doc.new_page()
            y = Y_INICIO
            continue
        if y > page.rect.height - 60:
            page = doc.new_page()
            y = Y_INICIO
        if estilo == "t":
            page.insert_text((MARGEM_X, y), texto, fontsize=16)
            y += ALTURA_LINHA
        elif estilo == "h":
            y += 6
            page.insert_text((MARGEM_X, y), texto, fontsize=12)
        else:
            page.insert_text((MARGEM_X, y), texto, fontsize=10)
        y += ALTURA_LINHA

    destino = Path(__file__).resolve().parent.parent / "teste_rg.pdf"
    doc.save(destino)
    doc.close()
    print(f"Gerado: {destino}")


if __name__ == "__main__":
    main()
