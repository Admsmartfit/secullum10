"""
Módulo de Regras de Notificação WhatsApp (Fase 4).
CRUD de regras + execução manual para teste.
"""
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required
from extensions import db
from models import NotificationRule

notificacoes_bp = Blueprint('notificacoes', __name__, url_prefix='/notificacoes')

TRIGGER_LABELS = {
    'EVENT_SYNC':    'Ao sincronizar batidas',
    'EVENT_ABSENCE': 'Ao detectar ausência',
    'DAILY':         'Diário (hora configurável)',
    'WEEKLY':        'Semanal (dia configurável)',
}

CONDITION_LABELS = {
    'LATE_ENTRY':   'Atraso na entrada',
    'EARLY_LEAVE':  'Saída antecipada',
    'ABSENCE':      'Ausência (sem ponto)',
    'OVERTIME':     'Hora extra na saída',
    'INTERJORNADA': 'Violação de interjornada',
    'ESCALA_ENVIO': 'Envio de escala ao funcionário',
}

# Templates padrão por tipo de condição
_DEFAULTS = {
    'LATE_ENTRY': {
        'manager':  'O funcionário {full_name} está {minutes} min atrasado no turno {turno} ({inicio}).',
        'employee': 'Olá, {name}! Identificamos {minutes} min de atraso no seu ponto. Turno: {turno} ({inicio}). Por favor, regularize.',
    },
    'ABSENCE': {
        'manager':  'O funcionário {full_name} não registrou ponto hoje. Turno: {turno} ({inicio}).',
        'employee': 'Olá, {name}! Você não registrou ponto hoje (turno {turno} às {inicio}). Responda esta mensagem.',
    },
    'OVERTIME': {
        'manager':  '{full_name} está fazendo hora extra de {minutes} min após o turno {turno} ({fim}).',
        'employee': 'Olá, {name}! Identificamos {minutes} min de hora extra após o turno {turno} ({fim}).',
    },
    'EARLY_LEAVE': {
        'manager':  '{full_name} saiu {minutes} min antes do término do turno {turno} ({fim}).',
        'employee': 'Olá, {name}! Saída {minutes} min antes do fim do turno {turno} ({fim}) foi registrada.',
    },
    'INTERJORNADA': {
        'manager':  '{full_name} possui intervalo de interjornada abaixo de 11h (CLT art. 66).',
        'employee': '',
    },
    'ESCALA_ENVIO': {
        'manager':  '',
        'employee': 'Olá, {name}! Sua escala: {turno} — {inicio} às {fim} ({data}).',
    },
}


def _save_from_form(regra: NotificationRule, form):
    cond = form.get('condition_type', 'LATE_ENTRY')
    regra.nome               = (form.get('nome') or '').strip() or f'Regra {cond}'
    regra.ativo              = form.get('ativo') == '1'
    regra.trigger_type       = form.get('trigger_type', 'EVENT_SYNC')
    regra.trigger_hour       = int(form.get('trigger_hour') or 8)
    regra.trigger_weekday    = int(form.get('trigger_weekday') or 4)
    regra.condition_type     = cond
    regra.threshold_minutes  = int(form.get('threshold_minutes') or 15)
    regra.dest_employee      = 'dest_employee' in form
    regra.dest_manager       = 'dest_manager' in form
    regra.dest_rh            = 'dest_rh' in form
    tmpl_mgr  = (form.get('template_manager')  or '').strip()
    tmpl_emp  = (form.get('template_employee') or '').strip()
    regra.template_manager   = tmpl_mgr  or _DEFAULTS.get(cond, {}).get('manager', '')
    regra.template_employee  = tmpl_emp  or _DEFAULTS.get(cond, {}).get('employee', '')
    regra.only_working_hours = 'only_working_hours' in form
    regra.send_immediately   = 'send_immediately' in form


@notificacoes_bp.route('/')
@login_required
def index():
    regras = NotificationRule.query.order_by(
        NotificationRule.ativo.desc(), NotificationRule.id
    ).all()
    return render_template(
        'notificacoes/index.html',
        regras=regras,
        trigger_labels=TRIGGER_LABELS,
        condition_labels=CONDITION_LABELS,
        defaults_json=_DEFAULTS,
    )


@notificacoes_bp.route('/nova', methods=['POST'])
@login_required
def nova():
    regra = NotificationRule()
    _save_from_form(regra, request.form)
    db.session.add(regra)
    db.session.commit()
    flash(f'Regra "{regra.nome}" criada!', 'success')
    return redirect(url_for('notificacoes.index'))


@notificacoes_bp.route('/<int:rid>/editar', methods=['POST'])
@login_required
def editar(rid):
    regra = NotificationRule.query.get_or_404(rid)
    _save_from_form(regra, request.form)
    db.session.commit()
    flash(f'Regra "{regra.nome}" atualizada!', 'success')
    return redirect(url_for('notificacoes.index'))


@notificacoes_bp.route('/<int:rid>/excluir', methods=['POST'])
@login_required
def excluir(rid):
    regra = NotificationRule.query.get_or_404(rid)
    nome = regra.nome
    db.session.delete(regra)
    db.session.commit()
    flash(f'Regra "{nome}" excluída.', 'warning')
    return redirect(url_for('notificacoes.index'))


@notificacoes_bp.route('/<int:rid>/toggle', methods=['POST'])
@login_required
def toggle(rid):
    regra = NotificationRule.query.get_or_404(rid)
    regra.ativo = not regra.ativo
    db.session.commit()
    return jsonify({'ok': True, 'ativo': regra.ativo})


@notificacoes_bp.route('/<int:rid>/executar', methods=['POST'])
@login_required
def executar(rid):
    """Executa manualmente uma regra (para teste)."""
    regra = NotificationRule.query.get_or_404(rid)
    from services.notification_processor import processar_regras_evento
    result = processar_regras_evento(regra.trigger_type)
    regra.ultima_execucao = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True, 'mensagens': result.get('mensagens', 0)})


@notificacoes_bp.route('/defaults/<condition_type>')
@login_required
def get_defaults(condition_type):
    return jsonify(_DEFAULTS.get(condition_type, {'manager': '', 'employee': ''}))
