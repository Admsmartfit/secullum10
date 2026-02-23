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
from models import ProntuarioDoc, Funcionario, FeedbackAula, AlocacaoDiaria

prontuario_bp = Blueprint('prontuario', __name__)

ALLOWED_EXTENSIONS = {'pdf', 'jpg', 'jpeg', 'png'}
TIPOS_DOC = ['ASO', 'CNH', 'Certidão', 'Contrato', 'Curso', 'Diploma', 'Outro']


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
