"""
RF5.3 – Prontuário Digital (upload/download de documentos)
RF5.4 – Alertas de vencimento
RF5.6 – QR Code de feedback de aula
"""
import os
from datetime import date, timedelta
from io import BytesIO
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, send_file, current_app, jsonify, abort)
from flask_login import login_required
from werkzeug.utils import secure_filename
from extensions import db
from flask_login import current_user
from models import ProntuarioDoc, Funcionario, FeedbackAula, AlocacaoDiaria, TemplateDocumento, EnvioDocumento, TabelaSalarial, Configuracao

prontuario_bp = Blueprint('prontuario', __name__)

ALLOWED_EXTENSIONS = {'pdf', 'jpg', 'jpeg', 'png'}
TIPOS_DOC = ['ASO', 'CNH', 'Certidão', 'Contrato', 'Curso', 'Diploma', 'Outro']


def _somente_admin(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.nivel_acesso != 'administrador':
            flash('Acesso restrito a administradores.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated


def _allowed(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ── Prontuário por funcionário (RF5.3) ────────────────────────────────────────

@prontuario_bp.route('/prontuario/<func_id>')
@login_required
def prontuario(func_id):
    func = Funcionario.query.get_or_404(func_id)
    docs = (
        ProntuarioDoc.query
        .filter_by(funcionario_id=func_id)
        .order_by(ProntuarioDoc.data_vencimento)
        .all()
    )
    hoje = date.today()
    alerta_30 = date.today() + timedelta(days=30)
    return render_template(
        'prontuario/index.html',
        func=func,
        docs=docs,
        hoje=hoje,
        alerta_30=alerta_30,
        tipos=TIPOS_DOC,
    )


@prontuario_bp.route('/prontuario/<func_id>/upload', methods=['POST'])
@login_required
def upload_doc(func_id):
    func = Funcionario.query.get_or_404(func_id)
    arquivo = request.files.get('arquivo')
    if not arquivo or arquivo.filename == '':
        flash('Nenhum arquivo selecionado.', 'danger')
        return redirect(url_for('prontuario.prontuario', func_id=func_id))

    if not _allowed(arquivo.filename):
        flash('Tipo de arquivo não permitido. Use PDF, JPG ou PNG.', 'danger')
        return redirect(url_for('prontuario.prontuario', func_id=func_id))

    fname = secure_filename(f'{func_id}_{arquivo.filename}')
    upload_dir = current_app.config['UPLOAD_FOLDER']
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, fname)
    arquivo.save(filepath)

    venc_str = request.form.get('data_vencimento', '')
    doc = ProntuarioDoc(
        funcionario_id=func_id,
        tipo=request.form.get('tipo', 'Outro'),
        nome_arquivo=arquivo.filename,
        arquivo_path=fname,
        data_vencimento=date.fromisoformat(venc_str) if venc_str else None,
    )
    db.session.add(doc)
    db.session.commit()
    flash('Documento enviado com sucesso!', 'success')
    return redirect(url_for('prontuario.prontuario', func_id=func_id))


@prontuario_bp.route('/prontuario/doc/<int:doc_id>/download')
@login_required
def download_doc(doc_id):
    doc = ProntuarioDoc.query.get_or_404(doc_id)
    upload_dir = current_app.config['UPLOAD_FOLDER']
    filepath = os.path.join(upload_dir, doc.arquivo_path)
    if not os.path.exists(filepath):
        abort(404)
    return send_file(filepath, as_attachment=True, download_name=doc.nome_arquivo)


@prontuario_bp.route('/prontuario/doc/<int:doc_id>/excluir', methods=['POST'])
@login_required
def excluir_doc(doc_id):
    doc = ProntuarioDoc.query.get_or_404(doc_id)
    func_id = doc.funcionario_id
    upload_dir = current_app.config['UPLOAD_FOLDER']
    filepath = os.path.join(upload_dir, doc.arquivo_path)
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except OSError:
        pass
    db.session.delete(doc)
    db.session.commit()
    flash('Documento excluído.', 'warning')
    return redirect(url_for('prontuario.prontuario', func_id=func_id))


# ── Alertas de documentos vencendo (RF5.4) ───────────────────────────────────

@prontuario_bp.route('/prontuario/alertas')
@login_required
def alertas_docs():
    limite = date.today() + timedelta(days=30)
    docs = (
        ProntuarioDoc.query
        .filter(
            ProntuarioDoc.data_vencimento.isnot(None),
            ProntuarioDoc.data_vencimento <= limite,
        )
        .join(Funcionario)
        .order_by(ProntuarioDoc.data_vencimento)
        .all()
    )
    return render_template('prontuario/alertas.html', docs=docs, hoje=date.today())


# ── QR Code de feedback de aula (RF5.6) ──────────────────────────────────────

@prontuario_bp.route('/qrcode/<int:alocacao_id>')
def qrcode_feedback(alocacao_id):
    """Gera imagem PNG do QR Code que aponta para o form de feedback."""
    import qrcode
    aloc = AlocacaoDiaria.query.get_or_404(alocacao_id)
    url = url_for('prontuario.feedback_form', alocacao_id=alocacao_id, _external=True)
    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name=f'feedback_{alocacao_id}.png')


@prontuario_bp.route('/feedback/<int:alocacao_id>', methods=['GET', 'POST'])
def feedback_form(alocacao_id):
    """Form público de feedback — acessado via QR Code pelo aluno."""
    aloc = AlocacaoDiaria.query.get_or_404(alocacao_id)

    if request.method == 'POST':
        nota = int(request.form.get('nota', 0))
        if nota < 1 or nota > 5:
            flash('Nota inválida. Escolha entre 1 e 5.', 'danger')
            return redirect(url_for('prontuario.feedback_form', alocacao_id=alocacao_id))

        fb = FeedbackAula(
            alocacao_id=alocacao_id,
            nota=nota,
            comentario=request.form.get('comentario', '').strip(),
        )
        db.session.add(fb)
        db.session.commit()
        return render_template('prontuario/feedback_obrigado.html', func=aloc.funcionario)

    return render_template('prontuario/feedback_form.html', aloc=aloc)


# ── Geração de Documentos Contratuais ────────────────────────────────────────

@prontuario_bp.route('/prontuario/gerar-documentos', methods=['GET', 'POST'])
@login_required
def gerar_documentos():
    from services.documento_service import gerar_pdf_de_template, enviar_documentos_para_lider

    funcionarios = Funcionario.query.filter_by(ativo=True).order_by(Funcionario.nome).all()
    templates = TemplateDocumento.query.filter_by(ativo=True).order_by(TemplateDocumento.nome).all()

    if request.method == 'POST':
        func_id = request.form.get('func_id')
        template_ids = request.form.getlist('template_ids')

        if not func_id or not template_ids:
            flash('Selecione um funcionário e ao menos um documento.', 'danger')
            return redirect(url_for('prontuario.gerar_documentos'))

        func = Funcionario.query.get_or_404(func_id)
        selecionados = TemplateDocumento.query.filter(
            TemplateDocumento.id.in_(template_ids)
        ).all()

        erros = []
        pdfs = []
        for tmpl in selecionados:
            try:
                pdf_bytes, nome_pdf = gerar_pdf_de_template(tmpl, func)
                pdfs.append((pdf_bytes, nome_pdf))
            except Exception as exc:
                erros.append(f'{tmpl.nome}: {exc}')

        if erros:
            for e in erros:
                flash(f'Erro ao gerar "{e}"', 'danger')
            return redirect(url_for('prontuario.gerar_documentos'))

        acao = request.form.get('acao', 'enviar')

        if acao == 'baixar':
            # Baixar único PDF se apenas um template selecionado
            if len(pdfs) == 1:
                pdf_bytes, nome_pdf = pdfs[0]
                return send_file(
                    BytesIO(pdf_bytes),
                    as_attachment=True,
                    download_name=nome_pdf,
                    mimetype='application/pdf',
                )
            # Múltiplos: ZIP
            import zipfile
            buf = BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for pdf_bytes, nome_pdf in pdfs:
                    zf.writestr(nome_pdf, pdf_bytes)
            buf.seek(0)
            return send_file(
                buf,
                as_attachment=True,
                download_name=f'documentos_{func.nome}.zip'.replace(' ', '_'),
                mimetype='application/zip',
            )

        # acao == 'enviar'
        try:
            email_dest = enviar_documentos_para_lider(func, pdfs, enviado_por=current_user)
            flash(
                f'{len(pdfs)} documento(s) gerado(s) e enviado(s) para {email_dest}.',
                'success',
            )
        except Exception as exc:
            flash(f'Documentos gerados, mas falha no envio: {exc}', 'warning')

        return redirect(url_for('prontuario.gerar_documentos'))

    logs = (
        EnvioDocumento.query
        .order_by(EnvioDocumento.criado_em.desc())
        .limit(20)
        .all()
    )
    return render_template(
        'prontuario/gerar_documentos.html',
        funcionarios=funcionarios,
        templates=templates,
        logs=logs,
    )


# ── Gerenciamento de Templates .docx ─────────────────────────────────────────

@prontuario_bp.route('/prontuario/templates')
@login_required
def gerenciar_templates():
    templates = TemplateDocumento.query.order_by(TemplateDocumento.nome).all()
    return render_template('prontuario/templates_manager.html', templates=templates)


@prontuario_bp.route('/prontuario/templates/upload', methods=['POST'])
@login_required
def upload_template():
    arquivo = request.files.get('arquivo')
    nome = request.form.get('nome', '').strip()
    descricao = request.form.get('descricao', '').strip()

    if not arquivo or arquivo.filename == '':
        flash('Nenhum arquivo selecionado.', 'danger')
        return redirect(url_for('prontuario.gerenciar_templates'))

    if not arquivo.filename.lower().endswith('.docx'):
        flash('Apenas arquivos .docx são permitidos.', 'danger')
        return redirect(url_for('prontuario.gerenciar_templates'))

    if not nome:
        flash('Informe um nome para o template.', 'danger')
        return redirect(url_for('prontuario.gerenciar_templates'))

    storage = os.path.join(current_app.root_path, 'storage', 'templates')
    os.makedirs(storage, exist_ok=True)

    fname = secure_filename(arquivo.filename)
    # Evita colisão de nomes
    base, ext = os.path.splitext(fname)
    counter = 1
    while os.path.exists(os.path.join(storage, fname)):
        fname = f'{base}_{counter}{ext}'
        counter += 1

    arquivo.save(os.path.join(storage, fname))

    db.session.add(TemplateDocumento(
        nome=nome,
        descricao=descricao or None,
        arquivo_nome=fname,
    ))
    db.session.commit()
    flash(f'Template "{nome}" enviado com sucesso.', 'success')
    return redirect(url_for('prontuario.gerenciar_templates'))


@prontuario_bp.route('/prontuario/templates/<int:tmpl_id>/excluir', methods=['POST'])
@login_required
def excluir_template(tmpl_id):
    tmpl = TemplateDocumento.query.get_or_404(tmpl_id)
    storage = os.path.join(current_app.root_path, 'storage', 'templates')
    filepath = os.path.join(storage, tmpl.arquivo_nome)
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except OSError:
        pass
    db.session.delete(tmpl)
    db.session.commit()
    flash(f'Template "{tmpl.nome}" excluído.', 'warning')
    return redirect(url_for('prontuario.gerenciar_templates'))


# ── Tabela Salarial por Função ────────────────────────────────────────────────

@prontuario_bp.route('/prontuario/salarios', methods=['GET'])
@login_required
@_somente_admin
def tabela_salarial():
    # Funções únicas cadastradas no sistema
    funcoes_db = (
        db.session.query(Funcionario.funcao)
        .filter(Funcionario.ativo == True, Funcionario.funcao.isnot(None))
        .distinct()
        .order_by(Funcionario.funcao)
        .all()
    )
    funcoes = [f[0] for f in funcoes_db if f[0]]
    salarios = {s.funcao: s for s in TabelaSalarial.query.all()}
    cfg_exp = Configuracao.query.filter_by(chave='experiencia_dias').first()
    experiencia_dias = int(cfg_exp.valor) if cfg_exp else 45
    return render_template('prontuario/tabela_salarial.html',
                           funcoes=funcoes, salarios=salarios,
                           experiencia_dias=experiencia_dias)


@prontuario_bp.route('/prontuario/salarios/config', methods=['POST'])
@login_required
@_somente_admin
def salvar_config_documentos():
    dias_str = request.form.get('experiencia_dias', '').strip()
    try:
        dias = int(dias_str)
        if dias <= 0 or dias > 365:
            raise ValueError
    except ValueError:
        return jsonify({'ok': False, 'erro': 'Valor inválido (1–365 dias)'}), 400

    row = Configuracao.query.filter_by(chave='experiencia_dias').first()
    if row:
        row.valor = str(dias)
    else:
        db.session.add(Configuracao(chave='experiencia_dias', valor=str(dias)))
    db.session.commit()
    return jsonify({'ok': True, 'experiencia_dias': dias})


@prontuario_bp.route('/prontuario/salarios/salvar', methods=['POST'])
@login_required
@_somente_admin
def salvar_salario():
    funcao = request.form.get('funcao', '').strip()
    if not funcao:
        return jsonify({'ok': False, 'erro': 'Função não informada'}), 400

    def _parse(campo):
        v = request.form.get(campo, '').strip().replace(',', '.')
        if not v:
            return None
        val = float(v)
        if val < 0:
            raise ValueError
        return val

    try:
        salario = _parse('salario')
        aux_alim = _parse('auxilio_alimentacao')
        premiacao = _parse('premiacao')
    except ValueError:
        return jsonify({'ok': False, 'erro': 'Valor inválido (deve ser ≥ 0)'}), 400

    row = TabelaSalarial.query.filter_by(funcao=funcao).first()
    if row:
        row.salario = salario
        row.auxilio_alimentacao = aux_alim
        row.premiacao = premiacao
    else:
        db.session.add(TabelaSalarial(
            funcao=funcao,
            salario=salario,
            auxilio_alimentacao=aux_alim,
            premiacao=premiacao,
        ))
    db.session.commit()
    return jsonify({'ok': True, 'funcao': funcao})


@prontuario_bp.route('/prontuario/salarios/excluir', methods=['POST'])
@login_required
@_somente_admin
def excluir_salario():
    funcao = request.form.get('funcao', '').strip()
    row = TabelaSalarial.query.filter_by(funcao=funcao).first()
    if row:
        db.session.delete(row)
        db.session.commit()
    return jsonify({'ok': True})


@prontuario_bp.route('/prontuario/api/funcionario/<func_id>')
@login_required
def api_funcionario_dados(func_id):
    """AJAX: retorna dados do funcionário para preview na tela de geração."""
    func = Funcionario.query.get_or_404(func_id)
    unidade = __import__('models').UnidadeLider.query.filter_by(
        departamento=func.departamento
    ).first()
    email_lider = None
    nome_lider = None
    if unidade and unidade.lider:
        email_lider = unidade.lider.email
        nome_lider = unidade.lider.nome
    return jsonify({
        'nome': func.nome,
        'funcao': func.funcao or '—',
        'departamento': func.departamento or '—',
        'cpf': func.cpf or '—',
        'estado_civil': func.estado_civil or '—',
        'admissao': func.admissao.strftime('%d/%m/%Y') if func.admissao else '—',
        'email_lider': email_lider,
        'nome_lider': nome_lider,
    })
