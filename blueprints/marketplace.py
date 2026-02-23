"""
RF5.1 – Marketplace de Turnos Vagos
RF5.2 – Matching Algorithm (verifica conflitos CLT antes de aprovar)
"""
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import MarketplaceTurno, Candidatura, Turno, Funcionario, AlocacaoDiaria

marketplace_bp = Blueprint('marketplace', __name__, url_prefix='/marketplace')


# ── Lista de vagas ────────────────────────────────────────────────────────────

@marketplace_bp.route('/')
@login_required
def index():
    vagas = (
        MarketplaceTurno.query
        .filter(MarketplaceTurno.data >= date.today(), MarketplaceTurno.status != 'cancelado')
        .order_by(MarketplaceTurno.data)
        .all()
    )
    # Para cada vaga, verificar se o usuário atual já se candidatou
    candidaturas_usuario = {}
    if current_user.nivel_acesso != 'gestor':
        # Buscar funcionário ligado ao usuário (por e-mail)
        func = Funcionario.query.filter_by(email=current_user.email).first()
        if func:
            for c in Candidatura.query.filter_by(funcionario_id=func.id).all():
                candidaturas_usuario[c.marketplace_id] = c.status

    return render_template(
        'marketplace/index.html',
        vagas=vagas,
        candidaturas_usuario=candidaturas_usuario,
        is_gestor=current_user.nivel_acesso == 'gestor',
    )


# ── CRUD de vagas (gestor) ────────────────────────────────────────────────────

@marketplace_bp.route('/nova', methods=['GET', 'POST'])
@login_required
def nova_vaga():
    if current_user.nivel_acesso != 'gestor':
        flash('Apenas gestores podem criar vagas.', 'danger')
        return redirect(url_for('marketplace.index'))

    turnos = Turno.query.order_by(Turno.nome).all()

    if request.method == 'POST':
        vaga = MarketplaceTurno(
            gestor_id=current_user.id,
            titulo=request.form['titulo'],
            data=date.fromisoformat(request.form['data']),
            turno_id=int(request.form['turno_id']),
            valor_hora=float(request.form.get('valor_hora', 0) or 0),
            descricao=request.form.get('descricao', ''),
            status='aberto',
        )
        db.session.add(vaga)
        db.session.commit()
        flash('Vaga criada com sucesso!', 'success')
        return redirect(url_for('marketplace.index'))

    return render_template('marketplace/vaga_form.html', turnos=turnos, vaga=None)


@marketplace_bp.route('/<int:vaga_id>/cancelar', methods=['POST'])
@login_required
def cancelar_vaga(vaga_id):
    if current_user.nivel_acesso != 'gestor':
        return jsonify({'error': 'forbidden'}), 403
    vaga = MarketplaceTurno.query.get_or_404(vaga_id)
    vaga.status = 'cancelado'
    db.session.commit()
    flash('Vaga cancelada.', 'warning')
    return redirect(url_for('marketplace.index'))


# ── Candidatura (professor) ───────────────────────────────────────────────────

@marketplace_bp.route('/<int:vaga_id>/candidatar', methods=['POST'])
@login_required
def candidatar(vaga_id):
    vaga = MarketplaceTurno.query.get_or_404(vaga_id)
    if vaga.status != 'aberto':
        flash('Esta vaga não está disponível.', 'warning')
        return redirect(url_for('marketplace.index'))

    func = Funcionario.query.filter_by(email=current_user.email).first()
    if not func:
        flash('Seu cadastro de funcionário não foi encontrado.', 'danger')
        return redirect(url_for('marketplace.index'))

    existing = Candidatura.query.filter_by(
        marketplace_id=vaga_id, funcionario_id=func.id
    ).first()
    if existing:
        flash('Você já se candidatou a esta vaga.', 'info')
        return redirect(url_for('marketplace.index'))

    candidatura = Candidatura(marketplace_id=vaga_id, funcionario_id=func.id, status='pendente')
    db.session.add(candidatura)
    vaga.status = 'candidatura'
    db.session.commit()
    flash('Candidatura enviada! Aguarde aprovação do gestor.', 'success')
    return redirect(url_for('marketplace.index'))


# ── Aprovação com matching CLT (gestor) ───────────────────────────────────────

@marketplace_bp.route('/candidatura/<int:cand_id>/aprovar', methods=['POST'])
@login_required
def aprovar_candidatura(cand_id):
    if current_user.nivel_acesso != 'gestor':
        return jsonify({'error': 'forbidden'}), 403

    cand = Candidatura.query.get_or_404(cand_id)
    vaga = cand.vaga
    func = cand.funcionario

    # RF5.2 – Verificar conflitos antes de alocar
    conflito = AlocacaoDiaria.query.filter_by(
        funcionario_id=func.id, data=vaga.data
    ).first()
    if conflito:
        flash(
            f'{func.nome} já tem alocação em {vaga.data.strftime("%d/%m/%Y")} '
            f'(turno: {conflito.turno.nome}). Conflito de escala detectado.',
            'danger',
        )
        return redirect(url_for('marketplace.index'))

    # RF5.2 – Validação CLT
    from services.motor_clt import validar_alocacao
    infracoes = validar_alocacao(func.id, vaga.data, vaga.turno_id)
    if infracoes:
        msgs = '; '.join(i['message'] for i in infracoes)
        flash(f'Infração CLT detectada: {msgs}', 'danger')
        return redirect(url_for('marketplace.index'))

    # Alocar funcionário no turno
    aloc = AlocacaoDiaria(
        funcionario_id=func.id,
        turno_id=vaga.turno_id,
        data=vaga.data,
    )
    db.session.add(aloc)
    cand.status = 'aprovado'
    vaga.status = 'aprovado'
    db.session.commit()

    # Notificar funcionário via WhatsApp se tiver celular
    if func.celular:
        try:
            from services.whatsapp_bot import enviar_texto
            enviar_texto(
                celular=func.celular,
                mensagem=(
                    f'✅ Sua candidatura para "{vaga.titulo}" em '
                    f'{vaga.data.strftime("%d/%m/%Y")} foi APROVADA!\n'
                    f'Turno: {vaga.turno.nome} '
                    f'({vaga.turno.hora_inicio.strftime("%H:%M")}–'
                    f'{vaga.turno.hora_fim.strftime("%H:%M")})'
                ),
                func_id=func.id,
                tipo='marketplace_aprovado',
            )
        except Exception:
            pass

    flash(f'Candidatura de {func.nome} aprovada e alocação criada!', 'success')
    return redirect(url_for('marketplace.index'))


@marketplace_bp.route('/candidatura/<int:cand_id>/rejeitar', methods=['POST'])
@login_required
def rejeitar_candidatura(cand_id):
    if current_user.nivel_acesso != 'gestor':
        return jsonify({'error': 'forbidden'}), 403
    cand = Candidatura.query.get_or_404(cand_id)
    cand.status = 'rejeitado'
    # Reabrir vaga se não houver outras candidaturas pendentes
    pendentes = Candidatura.query.filter_by(
        marketplace_id=cand.marketplace_id, status='pendente'
    ).count()
    if pendentes == 0:
        cand.vaga.status = 'aberto'
    db.session.commit()
    flash('Candidatura rejeitada.', 'warning')
    return redirect(url_for('marketplace.index'))
