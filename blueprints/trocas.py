"""Blueprint: Solicitação de Troca de Turno (PRD Fase 3)."""
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import SolicitacaoTroca, AlocacaoDiaria, Funcionario, Turno
from services.motor_clt import validar_alocacao

trocas_bp = Blueprint('trocas', __name__, url_prefix='/trocas')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_funcionario_atual():
    """Retorna o Funcionario vinculado ao usuário logado (por nome)."""
    return Funcionario.query.filter(
        Funcionario.nome.ilike(f'%{current_user.nome.split()[0]}%'),
        Funcionario.ativo == True
    ).first()


# ── Gestor: lista de trocas pendentes ────────────────────────────────────────

@trocas_bp.route('/')
@login_required
def index():
    trocas = (
        SolicitacaoTroca.query
        .order_by(SolicitacaoTroca.criado_em.desc())
        .limit(200)
        .all()
    )
    return render_template('trocas/index.html', trocas=trocas)


# ── Funcionário: minha escala + oferta de turno ───────────────────────────────

@trocas_bp.route('/minha-escala')
@login_required
def minha_escala():
    from datetime import date, timedelta
    hoje = date.today()
    fim  = hoje + timedelta(days=30)
    func = _get_funcionario_atual()
    alocacoes = []
    if func:
        alocacoes = (
            AlocacaoDiaria.query
            .filter(
                AlocacaoDiaria.funcionario_id == func.id,
                AlocacaoDiaria.data >= hoje,
                AlocacaoDiaria.data <= fim,
            )
            .join(Turno)
            .order_by(AlocacaoDiaria.data)
            .all()
        )
    trocas_abertas = (
        SolicitacaoTroca.query
        .filter_by(status='PENDENTE')
        .all()
    )
    return render_template('trocas/minha_escala.html',
                           func=func,
                           alocacoes=alocacoes,
                           trocas_abertas=trocas_abertas)


# ── Solicitar troca ───────────────────────────────────────────────────────────

@trocas_bp.route('/solicitar/<int:aloc_id>', methods=['POST'])
@login_required
def solicitar(aloc_id):
    aloc = AlocacaoDiaria.query.get_or_404(aloc_id)
    obs  = request.form.get('obs', '').strip()

    # Verifica se já existe pedido pendente para esta alocação
    existente = SolicitacaoTroca.query.filter_by(
        alocacao_origem_id=aloc_id,
        status='PENDENTE'
    ).first()
    if existente:
        flash('Já existe um pedido de troca em aberto para este turno.', 'warning')
        return redirect(url_for('trocas.minha_escala'))

    troca = SolicitacaoTroca(
        solicitante_id=aloc.funcionario_id,
        alocacao_origem_id=aloc_id,
        obs_solicitante=obs,
        status='PENDENTE',
    )
    db.session.add(troca)
    db.session.commit()
    flash('Pedido de troca enviado! Aguarde um colega aceitar e o gestor aprovar.', 'success')
    return redirect(url_for('trocas.minha_escala'))


# ── Aceitar troca (candidato se candidata) ────────────────────────────────────

@trocas_bp.route('/<int:troca_id>/aceitar', methods=['POST'])
@login_required
def aceitar(troca_id):
    troca = SolicitacaoTroca.query.get_or_404(troca_id)
    if troca.status != 'PENDENTE':
        flash('Esta troca não está mais disponível.', 'warning')
        return redirect(url_for('trocas.minha_escala'))

    aloc_destino_id = request.form.get('aloc_destino_id')
    if not aloc_destino_id:
        flash('Selecione o seu turno para trocar.', 'danger')
        return redirect(url_for('trocas.minha_escala'))

    aloc_dest = AlocacaoDiaria.query.get_or_404(int(aloc_destino_id))
    troca.candidato_id = aloc_dest.funcionario_id
    troca.alocacao_destino_id = aloc_dest.id
    troca.status = 'AGUARDANDO_APROVACAO'
    db.session.commit()
    flash('Você aceitou a troca! Aguardando aprovação do gestor.', 'success')
    return redirect(url_for('trocas.minha_escala'))


# ── Gestor: aprovar troca ─────────────────────────────────────────────────────

@trocas_bp.route('/<int:troca_id>/aprovar', methods=['POST'])
@login_required
def aprovar(troca_id):
    troca = SolicitacaoTroca.query.get_or_404(troca_id)
    if troca.status != 'AGUARDANDO_APROVACAO':
        flash('Esta troca não está aguardando aprovação.', 'warning')
        return redirect(url_for('trocas.index'))

    aloc_a = troca.alocacao_origem
    aloc_b = troca.alocacao_destino

    # Valida CLT para os dois lados
    erros = []
    erros += validar_alocacao(aloc_b.funcionario_id, aloc_a.data, aloc_a.turno)
    erros += validar_alocacao(aloc_a.funcionario_id, aloc_b.data, aloc_b.turno)
    bloqueantes = [e for e in erros if e.get('severity', 'error') == 'error']
    if bloqueantes:
        msgs = '; '.join(e['message'] for e in bloqueantes)
        troca.obs_gestor = f'Reprovado automaticamente: {msgs}'
        troca.status = 'REJEITADO'
        db.session.commit()
        flash(f'Troca rejeitada — infração CLT detectada: {msgs}', 'danger')
        return redirect(url_for('trocas.index'))

    # Executa a troca: swap funcionario_id entre as duas alocações
    func_a = aloc_a.funcionario_id
    func_b = aloc_b.funcionario_id
    aloc_a.funcionario_id = func_b
    aloc_b.funcionario_id = func_a

    troca.status = 'APROVADO'
    troca.obs_gestor = request.form.get('obs_gestor', '').strip() or None
    troca.atualizado_em = datetime.utcnow()
    db.session.commit()
    flash('Troca aprovada e escalas atualizadas!', 'success')
    return redirect(url_for('trocas.index'))


# ── Gestor: rejeitar troca ────────────────────────────────────────────────────

@trocas_bp.route('/<int:troca_id>/rejeitar', methods=['POST'])
@login_required
def rejeitar(troca_id):
    troca = SolicitacaoTroca.query.get_or_404(troca_id)
    troca.status = 'REJEITADO'
    troca.obs_gestor = request.form.get('obs_gestor', '').strip() or None
    troca.atualizado_em = datetime.utcnow()
    db.session.commit()
    flash('Troca rejeitada.', 'warning')
    return redirect(url_for('trocas.index'))


# ── API: trocas abertas (para minha_escala) ───────────────────────────────────

@trocas_bp.route('/api/abertas')
@login_required
def api_abertas():
    trocas = SolicitacaoTroca.query.filter_by(status='PENDENTE').all()
    return jsonify([{
        'id':           t.id,
        'solicitante':  t.solicitante.nome,
        'data':         str(t.alocacao_origem.data),
        'turno':        t.alocacao_origem.turno.nome,
        'obs':          t.obs_solicitante,
    } for t in trocas])
