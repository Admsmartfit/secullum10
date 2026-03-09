"""
Módulo de Regras de Notificação WhatsApp (Fase 4).
CRUD de regras + execução manual para teste.
"""
from collections import defaultdict  # noqa: F401 — usado em index()
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
    'EVENT_HOURLY':  'A cada hora (verificação contínua)',
}

CONDITION_LABELS = {
    'LATE_ENTRY':            'Atraso na entrada',
    'EARLY_LEAVE':           'Saída antecipada',
    'ABSENCE':               'Ausência (sem ponto)',
    'OVERTIME':              'Hora extra na saída',
    'INTERJORNADA':          'Violação de interjornada',
    'ESCALA_ENVIO':          'Envio de escala semanal',
    'INCONSISTENCY_REPORT':  'Relatório de inconsistências (dia anterior)',
    'DESCANSO_DOMINGO_F':    'Descanso dominical feminino (CLT art. 386)',
    'PRE_CHECKIN':           'Lembrete pré-turno (1h antes)',
    'DAILY_ABSENCE':         'Cobrança de ausência (sem ponto no dia)',
}

# ── Categorias ─────────────────────────────────────────────────────────────────
CATEGORIA_LABELS = {
    'geral':      'Mensagens Gerais',
    'bot':        'Interação com o Bot',
    'alerta':     'Alertas / Regras',
    'fechamento': 'Fechamentos',
}

# Sugestão automática: condition_type → categoria padrão
CONDITION_CATEGORIA = {
    'ESCALA_ENVIO':         'geral',
    'ABSENCE':              'bot',
    'LATE_ENTRY':           'alerta',
    'EARLY_LEAVE':          'alerta',
    'OVERTIME':             'alerta',
    'INTERJORNADA':         'alerta',
    'DESCANSO_DOMINGO_F':   'alerta',
    'INCONSISTENCY_REPORT': 'fechamento',
    'PRE_CHECKIN':          'automacao',
    'DAILY_ABSENCE':        'automacao',
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
    'INCONSISTENCY_REPORT': {
        'manager':  '{relatorio}',
        'employee': '',
    },
    'DESCANSO_DOMINGO_F': {
        'manager':  '{full_name} trabalhou todos os dias da semana incluindo domingo (CLT art. 386 — descanso feminino). Verifique a escala.',
        'employee': '',
    },
    'PRE_CHECKIN': {
        'manager':  '',
        'employee': 'Olá, {name}! Seu turno {turno} começa em 1h ({inicio}). Responda SIM para confirmar presença.',
    },
    'DAILY_ABSENCE': {
        'manager':  'O funcionário {full_name} não registrou ponto hoje. Turno: {turno} ({inicio}).',
        'employee': 'Olá, {name}! Você ainda não registrou ponto hoje (turno {turno} às {inicio}). Aconteceu algo?',
    },
    'ESCALA_ENVIO': {
        'manager':  '',
        'employee': '{{escala}}',
    },
}


# ── Configurações do Chatbot (Respostas do Robô) ───────────────────────────────
BOT_MSG_CHAVES = [
    'bot_msg_sim_func',
    'bot_msg_nao_func',
    'bot_msg_nao_lider',
    'bot_msg_justificativa_func',
    'bot_msg_justificativa_lider',
    'bot_msg_transbordo_lider',
]

BOT_MSG_LABELS = {
    'bot_msg_sim_func':             '✅ Resposta ao "SIM" (funcionário confirmou presença)',
    'bot_msg_nao_func':             '❌ Resposta ao "NÃO" (funcionário confirmou ausência)',
    'bot_msg_nao_lider':            '⚠️ Notificação ao gestor quando funcionário confirma ausência',
    'bot_msg_justificativa_func':   '📝 Confirmação ao funcionário que enviou justificativa',
    'bot_msg_justificativa_lider':  '📋 Notificação ao gestor com a justificativa do funcionário',
    'bot_msg_transbordo_lider':     '💬 Encaminhar mensagem livre ao gestor (sem inconsistência)',
}

BOT_MSG_DEFAULTS = {
    'bot_msg_sim_func':             'Perfeito, {{nome}}! Presença confirmada. Bom turno! 👍',
    'bot_msg_nao_func':             'Entendido! Sua ausência foi registrada. Qualquer dúvida, entre em contato com o RH.',
    'bot_msg_nao_lider':            '⚠️ {{nome}} confirmou AUSÊNCIA hoje.',
    'bot_msg_justificativa_func':   '✅ Recebido! Sua justificativa para {{data}} foi registrada no espelho de ponto.',
    'bot_msg_justificativa_lider':  '📝 *{{nome}}* enviou uma justificativa:\n"{{mensagem}}"',
    'bot_msg_transbordo_lider':     '💬 Mensagem de *{{nome}}*:\n"{{mensagem}}"',
}


def _save_from_form(regra: NotificationRule, form):
    cond = form.get('condition_type', 'LATE_ENTRY')
    regra.nome               = (form.get('nome') or '').strip() or f'Regra {cond}'
    regra.ativo              = form.get('ativo') == '1'
    regra.categoria          = form.get('categoria') or CONDITION_CATEGORIA.get(cond, 'alerta')
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
    from models import Configuracao
    regras = NotificationRule.query.order_by(
        NotificationRule.ativo.desc(), NotificationRule.id
    ).all()
    grupos = defaultdict(list)
    for r in regras:
        grupos[r.categoria or 'alerta'].append(r)

    # Separar regras por tab
    alertas    = [r for r in regras if r.trigger_type == 'EVENT_SYNC']
    automacoes = [r for r in regras if r.trigger_type in ('DAILY', 'WEEKLY', 'EVENT_HOURLY', 'EVENT_ABSENCE')]

    # Lê configurações do chatbot da tabela Configuracao
    bot_cfg = {}
    for chave in BOT_MSG_CHAVES:
        row = Configuracao.query.filter_by(chave=chave).first()
        bot_cfg[chave] = row.valor if (row and row.valor) else BOT_MSG_DEFAULTS.get(chave, '')

    return render_template(
        'notificacoes/index.html',
        regras=regras,
        grupos=grupos,
        alertas=alertas,
        automacoes=automacoes,
        bot_cfg=bot_cfg,
        bot_msg_labels=BOT_MSG_LABELS,
        bot_msg_defaults=BOT_MSG_DEFAULTS,
        categorias=CATEGORIA_LABELS,
        trigger_labels=TRIGGER_LABELS,
        condition_labels=CONDITION_LABELS,
        condition_categoria=CONDITION_CATEGORIA,
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


@notificacoes_bp.route('/chatbot/salvar', methods=['POST'])
@login_required
def chatbot_salvar():
    from models import Configuracao
    for chave in BOT_MSG_CHAVES:
        valor = request.form.get(chave, '').strip()
        if not valor:
            continue
        row = Configuracao.query.filter_by(chave=chave).first()
        if row:
            row.valor = valor
        else:
            db.session.add(Configuracao(chave=chave, valor=valor))
    db.session.commit()
    flash('Configurações do robô salvas!', 'success')
    return redirect(url_for('notificacoes.index') + '#tab-chatbot')


@notificacoes_bp.route('/defaults/<condition_type>')
@login_required
def get_defaults(condition_type):
    return jsonify(_DEFAULTS.get(condition_type, {'manager': '', 'employee': ''}))
