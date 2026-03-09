"""
Serviço de geração de documentos contratuais.

Fluxo:
  1. Carrega template .docx de storage/templates/
  2. Injeta dados via docxtpl (tags {{campo}})
  3. Converte para PDF via LibreOffice headless (Linux) ou docx2pdf (Windows)
  4. Envia por e-mail ao líder da unidade via Flask-Mail
"""
import json
import logging
import os
import subprocess
import tempfile
from datetime import date, timedelta

from flask import current_app
from flask_mail import Message

from extensions import db, mail
from models import Configuracao, EnvioDocumento, UnidadeLider

logger = logging.getLogger(__name__)


# ── Helpers de configuração ────────────────────────────────────────────────────

def _get_cfg(chave: str, default: str = '') -> str:
    row = Configuracao.query.filter_by(chave=chave).first()
    return row.valor if row else default


def _config_empresa() -> dict:
    chaves = [
        'empresa_nome', 'empresa_cnpj', 'empresa_socio', 'socio_cpf',
        'empresa_endereco', 'empresa_cidade', 'empresa_uf', 'empresa_cep',
    ]
    return {c: _get_cfg(c) for c in chaves}



def _salario_extenso(valor) -> str:
    """Converte valor decimal para texto em português.
    Ex: 2500.00 → 'dois mil e quinhentos reais'
    """
    if valor is None:
        return ''
    try:
        from num2words import num2words
        return num2words(float(valor), lang='pt_BR', to='currency')
    except Exception:
        # Fallback sem num2words
        return f'R$ {float(valor):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _contexto_funcionario(func) -> dict:
    partes = [func.endereco or '', func.bairro or '', func.cidade or '', func.uf or '']
    endereco_completo = ', '.join(p for p in partes if p)
    return {
        'nome_funcionario': func.nome or '',
        'cpf_funcionario':  func.cpf or '',
        'rg_funcionario':   func.rg or '',
        'profissao':        func.funcao or '',
        'estado_civil':     func.estado_civil or '',
        'endereco_func':    endereco_completo,
        'endereco':         func.endereco or '',
        'bairro':           func.bairro or '',
        'cidade':           func.cidade or '',
        'uf':               func.uf or '',
        'cep':              func.cep or '',
        'departamento':     func.departamento or '',
        'admissao':         func.admissao.strftime('%d/%m/%Y') if func.admissao else '',
        'data_admissao':    _admissao_extenso(func.admissao),
        'email_funcionario':  func.email or '',
        'celular_funcionario': func.celular or '',
    }


_MESES_PT = [
    'janeiro','fevereiro','março','abril','maio','junho',
    'julho','agosto','setembro','outubro','novembro','dezembro'
]


def _contexto_datas_hoje() -> dict:
    hoje = date.today()
    return {
        'data_hoje':     hoje.strftime('%d/%m/%Y'),
        'dia_hoje':      str(hoje.day),
        'mes_hoje':      _MESES_PT[hoje.month - 1],          # "setembro"
        'mes_hoje_num':  hoje.strftime('%m'),                 # "09"
        'ano_hoje':      str(hoje.year),
    }


def _contexto_experiencia(func) -> dict:
    dias_str = _get_cfg('experiencia_dias', '45')
    try:
        dias = int(dias_str)
    except ValueError:
        dias = 45

    inicio = func.admissao
    if inicio:
        # Contrato de experiência pode ser prorrogado (máx 90 dias CLT)
        fim = inicio + timedelta(days=dias)
        return {
            'experiencia_dias':        str(dias),
            'inicio_experiencia':      inicio.strftime('%d/%m/%Y'),
            'fim_experiencia':         fim.strftime('%d/%m/%Y'),
            'fim_experiencia_extenso': _data_extenso(fim),
        }
    return {
        'experiencia_dias':        str(dias),
        'inicio_experiencia':      '',
        'fim_experiencia':         '',
        'fim_experiencia_extenso': '',
    }


def _data_extenso(d) -> str:
    return f'{d.day} de {_MESES_PT[d.month - 1]} de {d.year}'


def _fmt_brl(valor) -> str:
    """Formata valor numérico como R$ 1.234,56."""
    return f'R$ {float(valor):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _contexto_salario(func) -> dict:
    from models import TabelaSalarial
    row = TabelaSalarial.query.filter_by(funcao=func.funcao or '').first()

    def _campo(val):
        if val is None:
            return ('', '')
        return (_fmt_brl(val), _salario_extenso(val))

    sal_fmt, sal_ext = _campo(row.salario if row else None)
    aux_fmt, aux_ext = _campo(row.auxilio_alimentacao if row else None)
    pre_fmt, pre_ext = _campo(row.premiacao if row else None)

    return {
        'salario':                    sal_fmt,
        'salario_extenso':            sal_ext,
        'auxilio_alimentacao':        aux_fmt,
        'auxilio_alimentacao_extenso': aux_ext,
        'premiacao':                  pre_fmt,
        'premiacao_extenso':          pre_ext,
    }


def _contexto_banco_horas(func, data_ref: date = None) -> dict:
    if data_ref is None:
        data_ref = date.today()
    from models import BancoHorasSaldo
    res = {
        'saldo_dia': '0.00',
        'saldo_acumulado': '0.00',
        'saldo_dia_extenso': 'zero horas',
        'saldo_acumulado_extenso': 'zero horas',
    }
    s = BancoHorasSaldo.query.filter_by(funcionario_id=func.id, data=data_ref).first()
    if s:
        res['saldo_dia'] = f"{float(s.saldo_dia):.2f}"
        res['saldo_acumulado'] = f"{float(s.saldo_acumulado):.2f}"
        # Simplified extense since it's hours, not currency
        res['saldo_dia_extenso'] = f"{float(s.saldo_dia):.2f} horas"
        res['saldo_acumulado_extenso'] = f"{float(s.saldo_acumulado):.2f} horas"
    return res


def _admissao_extenso(d) -> str:
    if not d:
        return ''
    return _data_extenso(d)


# ── Geração de PDF ─────────────────────────────────────────────────────────────

def templates_dir() -> str:
    return os.path.join(current_app.root_path, 'storage', 'templates')


def gerar_pdf_de_template(template_doc, funcionario) -> tuple:
    """
    Preenche o .docx e converte para PDF.
    Retorna (bytes_pdf, nome_arquivo_pdf).
    """
    from docxtpl import DocxTemplate

    docx_path = os.path.join(templates_dir(), template_doc.arquivo_nome)
    if not os.path.exists(docx_path):
        raise FileNotFoundError(
            f'Template não encontrado no servidor: {template_doc.arquivo_nome}'
        )

    ctx = _config_empresa()
    ctx.update(_contexto_funcionario(funcionario))
    ctx.update(_contexto_salario(funcionario))
    ctx.update(_contexto_datas_hoje())
    ctx.update(_contexto_experiencia(funcionario))
    ctx.update(_contexto_banco_horas(funcionario))

    tpl = DocxTemplate(docx_path)
    tpl.render(ctx)

    with tempfile.TemporaryDirectory() as tmpdir:
        filled_docx = os.path.join(tmpdir, 'filled.docx')
        tpl.save(filled_docx)
        pdf_path = _convert_to_pdf(filled_docx, tmpdir)
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()

    nome_pdf = (
        f'{funcionario.nome}_{template_doc.nome}.pdf'
        .replace(' ', '_')
        .replace('/', '-')
    )
    return pdf_bytes, nome_pdf


def _convert_to_pdf(docx_path: str, output_dir: str) -> str:
    """Converte .docx → .pdf. Tenta LibreOffice (Linux) depois docx2pdf (Windows)."""
    # Tentativa 1: LibreOffice headless
    try:
        result = subprocess.run(
            [
                'libreoffice', '--headless',
                '--convert-to', 'pdf',
                '--outdir', output_dir,
                docx_path,
            ],
            capture_output=True,
            timeout=60,
        )
        if result.returncode == 0:
            base = os.path.splitext(os.path.basename(docx_path))[0]
            return os.path.join(output_dir, base + '.pdf')
        logger.warning('[documento_service] LibreOffice retornou %d: %s',
                       result.returncode, result.stderr.decode())
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning('[documento_service] LibreOffice indisponível: %s', exc)

    # Tentativa 2: python-docx2pdf (Windows com Word)
    try:
        from docx2pdf import convert
        pdf_path = docx_path.replace('.docx', '.pdf')
        convert(docx_path, pdf_path)
        if os.path.exists(pdf_path):
            return pdf_path
    except Exception as exc:
        logger.warning('[documento_service] docx2pdf falhou: %s', exc)

    raise RuntimeError(
        'Não foi possível converter o documento para PDF. '
        'Instale LibreOffice (Linux) ou Microsoft Word (Windows).'
    )


# ── Envio de e-mail ────────────────────────────────────────────────────────────

def enviar_documentos_para_lider(funcionario, pdfs: list, enviado_por=None) -> str:
    """
    Envia PDFs por e-mail ao líder da unidade do funcionário.

    :param funcionario: instância Funcionario
    :param pdfs: lista de (bytes_pdf, nome_arquivo)
    :param enviado_por: instância Usuario (para log)
    :returns: e-mail do destinatário
    :raises ValueError: se não houver líder configurado
    :raises RuntimeError: se envio falhar
    """
    unidade = UnidadeLider.query.filter_by(departamento=funcionario.departamento).first()
    if not unidade or not unidade.lider:
        raise ValueError(
            f'Nenhum líder configurado para o departamento "{funcionario.departamento}". '
            'Configure em Configurações → Unidades/Líderes.'
        )

    email_lider = unidade.lider.email
    empresa_nome = _get_cfg('empresa_nome', 'Empresa')
    nomes_templates = [nome for _, nome in pdfs]

    msg = Message(
        subject=f'Documentos para Assinatura – {funcionario.nome}',
        recipients=[email_lider],
        body=(
            f'Olá, {unidade.lider.nome},\n\n'
            f'Seguem em anexo os documentos de {funcionario.nome} '
            f'({funcionario.funcao or "—"}) para assinatura.\n\n'
            f'Departamento: {funcionario.departamento or "—"}\n'
            f'Documentos: {", ".join(nomes_templates)}\n\n'
            f'Atenciosamente,\n{empresa_nome}'
        ),
    )

    for pdf_bytes, nome in pdfs:
        msg.attach(nome, 'application/pdf', pdf_bytes)

    mail.send(msg)

    # Registra log
    log = EnvioDocumento(
        funcionario_id=funcionario.id,
        email_destinatario=email_lider,
        templates_enviados=json.dumps(nomes_templates, ensure_ascii=False),
        enviado_por_id=enviado_por.id if enviado_por else None,
    )
    db.session.add(log)
    db.session.commit()

    return email_lider
