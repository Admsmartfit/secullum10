"""
Geração de PDF do espelho de ponto via ReportLab – RF4.4.
"""
from io import BytesIO
from datetime import date as date_type
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
)


# Paleta inspirada no tema da aplicação
DARK = colors.HexColor('#1e293b')
ACCENT = colors.HexColor('#38bdf8')
LIGHT_GRAY = colors.HexColor('#94a3b8')
SUCCESS = colors.HexColor('#22c55e')
DANGER = colors.HexColor('#ef4444')


def gerar_espelho_pdf(funcionario, batidas_agrupadas: list, data_inicio, data_fim) -> BytesIO:
    """
    Gera o PDF do espelho de ponto de um funcionário no período.

    :param funcionario: instância de Funcionario
    :param batidas_agrupadas: lista de dicts {data, horas:[...]}  (já filtrada pelo funcionário)
    :param data_inicio: date
    :param data_fim: date
    :return: BytesIO com o PDF
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=f'Espelho de Ponto – {funcionario.nome}',
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'title', parent=styles['Heading1'],
        fontSize=16, textColor=DARK, spaceAfter=2 * mm,
    )
    sub_style = ParagraphStyle(
        'sub', parent=styles['Normal'],
        fontSize=9, textColor=LIGHT_GRAY, spaceAfter=6 * mm,
    )
    normal = ParagraphStyle(
        'normal', parent=styles['Normal'],
        fontSize=9, textColor=DARK,
    )

    elements = []

    # ── Cabeçalho ──────────────────────────────────────────────────────────────
    elements.append(Paragraph('Espelho de Ponto', title_style))
    elements.append(Paragraph(
        f'Funcionário: <b>{funcionario.nome}</b> &nbsp;|&nbsp; '
        f'CPF: {funcionario.cpf or "—"} &nbsp;|&nbsp; '
        f'Função: {funcionario.funcao or "—"}',
        sub_style,
    ))
    elements.append(Paragraph(
        f'Período: {data_inicio.strftime("%d/%m/%Y")} a {data_fim.strftime("%d/%m/%Y")} &nbsp;|&nbsp; '
        f'Departamento: {funcionario.departamento or "—"}',
        sub_style,
    ))
    elements.append(HRFlowable(width='100%', color=ACCENT, thickness=1.5, spaceAfter=4 * mm))

    if not batidas_agrupadas:
        elements.append(Paragraph('Nenhuma batida registrada no período.', normal))
        doc.build(elements)
        buf.seek(0)
        return buf

    # ── Tabela ─────────────────────────────────────────────────────────────────
    header = ['Data', 'Entrada 1', 'Saída 1', 'Entrada 2', 'Saída 2', 'Total (h)']
    table_data = [header]

    for dia in batidas_agrupadas:
        horas = dia.get('horas', [])
        # Preencher até 4 batidas (2 pares)
        padded = horas[:4] + [''] * (4 - min(len(horas), 4))
        # Calcular total trabalhado (pares entrada/saída)
        total = 0.0
        for i in range(0, len(horas) - 1, 2):
            try:
                from datetime import datetime as dt
                h_e = dt.strptime(horas[i], '%H:%M')
                h_s = dt.strptime(horas[i + 1], '%H:%M')
                if h_s <= h_e:
                    from datetime import timedelta
                    h_s += timedelta(hours=24)
                diff = (h_s - h_e).seconds / 3600
                if 0 < diff <= 16:
                    total += diff
            except Exception:
                pass
        total_str = f'{total:.1f}h' if total > 0 else '—'

        # Formata data
        try:
            from datetime import datetime as dt
            d = dt.strptime(dia['data'], '%Y-%m-%d').strftime('%d/%m/%Y')
        except Exception:
            d = dia['data']

        table_data.append([d] + padded + [total_str])

    col_widths = [28 * mm, 24 * mm, 24 * mm, 24 * mm, 24 * mm, 20 * mm]
    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), DARK),
        ('TEXTCOLOR', (0, 0), (-1, 0), ACCENT),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, LIGHT_GRAY),
        ('ROWHEIGHT', (0, 0), (-1, -1), 7 * mm),
    ]))
    elements.append(t)

    # ── Rodapé ─────────────────────────────────────────────────────────────────
    elements.append(Spacer(1, 6 * mm))
    elements.append(HRFlowable(width='100%', color=LIGHT_GRAY, thickness=0.5))
    elements.append(Spacer(1, 2 * mm))
    elements.append(Paragraph(
        f'Gerado em {date_type.today().strftime("%d/%m/%Y")} – Secullum Hub',
        ParagraphStyle('footer', parent=styles['Normal'], fontSize=7, textColor=LIGHT_GRAY),
    ))

    doc.build(elements)
    buf.seek(0)
    return buf
