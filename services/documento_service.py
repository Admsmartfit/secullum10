"""
Serviço de geração de documentos contratuais.

Fluxo:
  1. Carrega template .docx de storage/templates/
  2. Injeta dados via docxtpl (tags {{campo}})
  3. Converte para PDF via LibreOffice headless (Linux) ou docx2pdf (Windows)
  4. Envia por e-mail ao líder da unidade via Flask-Mail
"""
import io
import json
import logging
import os
import re
import subprocess
import tempfile
import zipfile
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


def _config_empresa(departamento: str = None) -> dict:
    """Retorna dados da empresa.
    Prioridade: UnidadeLider do departamento → config global (Configuracao).
    """
    # Tenta buscar dados específicos do departamento
    if departamento:
        try:
            from models import UnidadeLider
            unidade = UnidadeLider.query.filter_by(departamento=departamento).first()
            if unidade and unidade.empresa_nome:
                return {
                    'empresa_nome':     unidade.empresa_nome     or '',
                    'empresa_cnpj':     unidade.empresa_cnpj     or '',
                    'empresa_socio':    unidade.empresa_socio    or '',
                    'socio_cpf':        unidade.socio_cpf        or '',
                    'empresa_endereco': unidade.empresa_endereco or '',
                    'empresa_cidade':   unidade.empresa_cidade   or '',
                    'empresa_uf':       unidade.empresa_uf       or '',
                    'empresa_cep':      unidade.empresa_cep      or '',
                }
        except Exception:
            pass
    # Fallback: configuração global
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
    # Prioridade: dias configurado no departamento → config global
    dias = 45
    if func.departamento:
        try:
            from models import UnidadeLider
            unidade = UnidadeLider.query.filter_by(departamento=func.departamento).first()
            if unidade and unidade.experiencia_dias:
                dias = unidade.experiencia_dias
            else:
                dias = int(_get_cfg('experiencia_dias', '45'))
        except Exception:
            dias = int(_get_cfg('experiencia_dias', '45') or 45)
    else:
        try:
            dias = int(_get_cfg('experiencia_dias', '45'))
        except ValueError:
            dias = 45

    inicio = func.admissao
    if inicio:
        # Contrato de experiência pode ser prorrogado (máx 90 dias CLT)
        fim = inicio + timedelta(days=dias)
        fim_total = inicio + timedelta(days=90)
        return {
            'experiencia_dias':              str(dias),
            'inicio_experiencia':            inicio.strftime('%d/%m/%Y'),
            'fim_experiencia':               fim.strftime('%d/%m/%Y'),
            'fim_experiencia_extenso':       _data_extenso(fim),
            'fim_experiencia_total':         fim_total.strftime('%d/%m/%Y'),
            'fim_experiencia_total_extenso': _data_extenso(fim_total),
        }
    return {
        'experiencia_dias':              str(dias),
        'inicio_experiencia':            '',
        'fim_experiencia':               '',
        'fim_experiencia_extenso':       '',
        'fim_experiencia_total':         '',
        'fim_experiencia_total_extenso': '',
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


def _sanitize_docx(docx_path: str) -> io.BytesIO:
    """
    Escapa chaves literais `{` e `}` no .docx que NÃO fazem parte de tags
    Jinja2/docxtpl (`{{var}}`, `{%block%}`).

    Causa do erro "unexpected '}'": Word insere `}` em texto normal (cláusulas,
    referências a artigos, etc.) e o Jinja2 do docxtpl confunde com fechamento
    de uma expressão `{{...}}`.

    Estratégia: percorre os nós <w:t>...</w:t> do XML interno do .docx,
    protege os pares legítimos com sentinelas, escapa os restantes, restaura.
    """
    # Sentinelas que nunca aparecem em texto natural
    _OO = '\x00\x01'   # {{
    _CC = '\x02\x03'   # }}
    _PO = '\x04\x05'   # {%
    _PC = '\x06\x07'   # %}

    W_T_RE = re.compile(r'(<w:t(?:\s[^>]*)?>)(.*?)(</w:t>)', re.DOTALL)

    def _fix_node(m: re.Match) -> str:
        o, text, c = m.group(1), m.group(2), m.group(3)
        text = text.replace('{{', _OO).replace('}}', _CC)
        text = text.replace('{%', _PO).replace('%}', _PC)
        text = text.replace('{', '{{').replace('}', '}}')
        text = (text.replace(_OO, '{{').replace(_CC, '}}')
                    .replace(_PO, '{%').replace(_PC, '%}'))
        return o + text + c

    def _process_xml(data: bytes) -> bytes:
        xml = data.decode('utf-8', errors='replace')
        return W_T_RE.sub(_fix_node, xml).encode('utf-8')

    out = io.BytesIO()
    with zipfile.ZipFile(docx_path, 'r') as zin:
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.startswith('word/') and item.filename.endswith('.xml'):
                    data = _process_xml(data)
                zout.writestr(item, data)
    out.seek(0)
    return out


def gerar_pdf_de_template(template_doc, funcionario) -> tuple:
    """
    Preenche o .docx e converte para PDF.
    Retorna (bytes_pdf, nome_arquivo_pdf).
    """
    from docxtpl import DocxTemplate
    from jinja2.exceptions import TemplateSyntaxError

    docx_path = os.path.join(templates_dir(), template_doc.arquivo_nome)
    if not os.path.exists(docx_path):
        raise FileNotFoundError(
            f'Template não encontrado no servidor: {template_doc.arquivo_nome}'
        )

    ctx = _config_empresa(departamento=funcionario.departamento)
    ctx.update(_contexto_funcionario(funcionario))
    ctx.update(_contexto_salario(funcionario))
    ctx.update(_contexto_datas_hoje())
    ctx.update(_contexto_experiencia(funcionario))
    ctx.update(_contexto_banco_horas(funcionario))

    tpl = DocxTemplate(docx_path)
    try:
        tpl.render(ctx)
    except TemplateSyntaxError as e:
        # O .docx contém { ou } literais no texto que o Jinja2 interpreta como
        # tags malformadas. Tenta corrigir automaticamente e renderizar de novo.
        logger.warning(
            '[documento_service] TemplateSyntaxError em "%s": %s — tentando sanitizar.',
            template_doc.nome, e,
        )
        try:
            sanitized = _sanitize_docx(docx_path)
            tpl = DocxTemplate(sanitized)
            tpl.render(ctx)
        except Exception as e2:
            raise ValueError(
                f'Erro ao gerar "{template_doc.nome}": {e}\n\n'
                f'O documento .docx contém chaves "{{" ou "}}" fora de uma tag '
                f'{{{{variavel}}}}. Abra o arquivo no Word, localize os caracteres '
                f'problemáticos e remova-os ou envolva-os em {{% raw %}}...{{% endraw %}}.'
            ) from e2

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


def _libreoffice_exe() -> str | None:
    """Localiza o executável do LibreOffice/soffice no sistema."""
    import shutil, sys

    # Nomes de comando diretos (funciona se estiver no PATH)
    for cmd in ('soffice', 'libreoffice'):
        if shutil.which(cmd):
            return cmd

    # Caminhos fixos comuns no Windows
    if sys.platform == 'win32':
        caminhos_windows = [
            r'C:\Program Files\LibreOffice\program\soffice.exe',
            r'C:\Program Files (x86)\LibreOffice\program\soffice.exe',
        ]
        for p in caminhos_windows:
            if os.path.exists(p):
                return p

    return None


def _convert_to_pdf(docx_path: str, output_dir: str) -> str:
    """Converte .docx → .pdf. Tenta LibreOffice headless, depois docx2pdf."""
    exe = _libreoffice_exe()

    if exe:
        try:
            result = subprocess.run(
                [
                    exe, '--headless',
                    '--convert-to', 'pdf',
                    '--outdir', output_dir,
                    docx_path,
                ],
                capture_output=True,
                timeout=120,
            )
            if result.returncode == 0:
                base = os.path.splitext(os.path.basename(docx_path))[0]
                pdf = os.path.join(output_dir, base + '.pdf')
                if os.path.exists(pdf):
                    return pdf
            logger.warning(
                '[documento_service] LibreOffice retornou %d: %s',
                result.returncode,
                result.stderr.decode(errors='replace'),
            )
        except subprocess.TimeoutExpired:
            logger.warning('[documento_service] LibreOffice timeout.')
        except Exception as exc:
            logger.warning('[documento_service] LibreOffice erro: %s', exc)
    else:
        logger.warning('[documento_service] LibreOffice não encontrado no PATH nem em caminhos padrão.')

    # Fallback: python-docx2pdf (requer Microsoft Word instalado)
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
        'Verifique se o LibreOffice está instalado e acessível. '
        'No Windows, o executável deve estar em: '
        r'C:\Program Files\LibreOffice\program\soffice.exe'
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
