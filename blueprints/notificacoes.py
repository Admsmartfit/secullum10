"""
Módulo de Regras de Notificação WhatsApp (Fase 4).
CRUD de regras + execução manual para teste.
"""
from collections import defaultdict  # noqa: F401 — usado em index()
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required
from extensions import db
from models import NotificationRule, BotKeywordRule

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
    'alerta':    'Alerta Operacional',
    'automacao': 'Automação de Rotina',
    'bot':       'Interação com o Bot',
    'geral':     'Geral',
}

# Sugestão automática: condition_type → categoria padrão
CONDITION_CATEGORIA = {
    'ESCALA_ENVIO':         'automacao',
    'ABSENCE':              'alerta',
    'LATE_ENTRY':           'alerta',
    'EARLY_LEAVE':          'alerta',
    'OVERTIME':             'alerta',
    'INTERJORNADA':         'alerta',
    'DESCANSO_DOMINGO_F':   'automacao',
    'INCONSISTENCY_REPORT': 'automacao',
    'PRE_CHECKIN':          'alerta',
    'DAILY_ABSENCE':        'alerta',
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
    'bot_msg_atestado_func',
    'bot_msg_atestado_lider',
]

BOT_MSG_LABELS = {
    'bot_msg_sim_func':             '✅ Resposta ao "SIM" (funcionário confirmou presença)',
    'bot_msg_nao_func':             '❌ Resposta ao "NÃO" (funcionário confirmou ausência)',
    'bot_msg_nao_lider':            '⚠️ Notificação ao gestor quando funcionário confirma ausência',
    'bot_msg_justificativa_func':   '📝 Confirmação ao funcionário que enviou justificativa',
    'bot_msg_justificativa_lider':  '📋 Notificação ao gestor com a justificativa do funcionário',
    'bot_msg_transbordo_lider':     '💬 Encaminhar mensagem livre ao gestor (sem inconsistência)',
    'bot_msg_atestado_func':        '🩺 Confirmação ao funcionário que enviou atestado',
    'bot_msg_atestado_lider':       '🏥 Notificação ao gestor sobre atestado recebido',
}

BOT_MSG_DEFAULTS = {
    'bot_msg_sim_func':             'Perfeito, {{nome}}! Presença confirmada. Bom turno! 👍',
    'bot_msg_nao_func':             'Entendido! Sua ausência foi registrada. Qualquer dúvida, entre em contato com o RH.',
    'bot_msg_nao_lider':            '⚠️ {{nome}} confirmou AUSÊNCIA hoje.',
    'bot_msg_justificativa_func':   '✅ Recebido! Sua justificativa para {{data}} foi registrada no espelho de ponto.',
    'bot_msg_justificativa_lider':  '📝 *{{nome}}* enviou uma justificativa:\n"{{mensagem}}"',
    'bot_msg_transbordo_lider':     '💬 Mensagem de *{{nome}}*:\n"{{mensagem}}"',
    'bot_msg_atestado_func':        '✅ Atestado recebido com sucesso em {{data}}! O RH foi notificado.',
    'bot_msg_atestado_lider':       '🩺 *{{nome}}* enviou um atestado médico em {{data}}. Verifique no prontuário.',
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
    regra.dest_custom        = 'dest_custom' in form
    regra.custom_phone       = (form.get('custom_phone') or '').strip() or None
    tmpl_mgr  = (form.get('template_manager')  or '').strip()
    tmpl_emp  = (form.get('template_employee') or '').strip()
    regra.template_manager   = tmpl_mgr  or _DEFAULTS.get(cond, {}).get('manager', '')
    regra.template_employee  = tmpl_emp  or _DEFAULTS.get(cond, {}).get('employee', '')
    regra.template_employee_tipo       = form.get('template_employee_tipo') or 'texto'
    regra.template_employee_interativo = form.get('template_employee_interativo') or None
    regra.template_manager_tipo        = form.get('template_manager_tipo') or 'texto'
    regra.template_manager_interativo  = form.get('template_manager_interativo') or None
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

    keyword_rules = BotKeywordRule.query.order_by(BotKeywordRule.id).all()

    # Mensagens customizadas (bot_custom_*)
    from models import Configuracao as _Cfg
    custom_msgs = []
    custom_rows = _Cfg.query.filter(_Cfg.chave.like('bot_custom_%')).filter(
        ~_Cfg.chave.like('%__label')
    ).order_by(_Cfg.id).all()
    for row in custom_rows:
        label_row = _Cfg.query.filter_by(chave=f'{row.chave}__label').first()
        custom_msgs.append({
            'chave': row.chave,
            'label': label_row.valor if label_row else row.chave,
            'valor': row.valor,
        })

    # Labels/descrições customizadas dos itens do Menu Interativo
    _MENU_DEFAULTS = {
        'menu_escala':   {'titulo': '📅 Minha Escala',    'desc': 'Ver turnos dos próximos 7 dias'},
        'menu_atraso':   {'titulo': '⏰ Informar Atraso', 'desc': 'Avisar que vai atrasar'},
        'menu_ausencia': {'titulo': '🚨 Informar Ausência','desc': 'Confirmar falta hoje'},
        'menu_atestado': {'titulo': '🩺 Enviar Atestado', 'desc': 'Foto ou PDF do atestado médico'},
        'menu_rh':       {'titulo': '👤 Falar com RH',    'desc': 'Encaminhar para atendimento humano'},
    }
    menu_itens = []
    for btn_id, defaults in _MENU_DEFAULTS.items():
        t_row = _Cfg.query.filter_by(chave=f'{btn_id}__titulo').first()
        d_row = _Cfg.query.filter_by(chave=f'{btn_id}__desc').first()
        menu_itens.append({
            'id': btn_id,
            'titulo': t_row.valor if t_row else defaults['titulo'],
            'desc':   d_row.valor if d_row else defaults['desc'],
        })

    return render_template(
        'notificacoes/index.html',
        regras=regras,
        grupos=grupos,
        alertas=alertas,
        automacoes=automacoes,
        bot_cfg=bot_cfg,
        bot_msg_labels=BOT_MSG_LABELS,
        bot_msg_defaults=BOT_MSG_DEFAULTS,
        keyword_rules=keyword_rules,
        custom_msgs=custom_msgs,
        menu_itens=menu_itens,
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
    regra.ultima_execucao = datetime.now(ZoneInfo('America/Sao_Paulo'))
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


@notificacoes_bp.route('/chatbot/msg/salvar', methods=['POST'])
@login_required
def chatbot_msg_salvar():
    """Salva uma única mensagem do sistema (chamado via AJAX ou form individual)."""
    from models import Configuracao
    chave = (request.form.get('chave') or '').strip()
    valor = (request.form.get('valor') or '').strip()
    if not chave:
        return jsonify({'ok': False, 'erro': 'Chave obrigatória'}), 400
    row = Configuracao.query.filter_by(chave=chave).first()
    if row:
        row.valor = valor
    else:
        db.session.add(Configuracao(chave=chave, valor=valor))
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True})
    flash('Mensagem salva!', 'success')
    return redirect(url_for('notificacoes.index') + '#tab-chatbot')


@notificacoes_bp.route('/chatbot/msg/nova', methods=['POST'])
@login_required
def chatbot_msg_nova():
    """Cria uma nova mensagem customizada do sistema."""
    from models import Configuracao
    label = (request.form.get('label') or '').strip()
    chave = (request.form.get('chave') or '').strip()
    valor = (request.form.get('valor') or '').strip()
    if not label or not chave or not valor:
        flash('Label, chave e mensagem são obrigatórios.', 'danger')
        return redirect(url_for('notificacoes.index') + '#tab-chatbot')
    # Prefixo bot_custom_ garante namespace separado dos predefinidos
    chave_full = f'bot_custom_{chave}' if not chave.startswith('bot_') else chave
    if Configuracao.query.filter_by(chave=chave_full).first():
        flash(f'Chave "{chave_full}" já existe.', 'warning')
        return redirect(url_for('notificacoes.index') + '#tab-chatbot')
    db.session.add(Configuracao(chave=chave_full, valor=valor))
    db.session.add(Configuracao(chave=f'{chave_full}__label', valor=label))
    db.session.commit()
    flash(f'Mensagem "{label}" criada!', 'success')
    return redirect(url_for('notificacoes.index') + '#tab-chatbot')


@notificacoes_bp.route('/chatbot/msg/excluir', methods=['POST'])
@login_required
def chatbot_msg_excluir():
    """Remove uma mensagem customizada (nunca remove as predefinidas)."""
    from models import Configuracao
    chave = (request.form.get('chave') or '').strip()
    if chave in BOT_MSG_CHAVES:
        flash('Mensagens predefinidas não podem ser excluídas.', 'danger')
        return redirect(url_for('notificacoes.index') + '#tab-chatbot')
    Configuracao.query.filter(
        Configuracao.chave.in_([chave, f'{chave}__label'])
    ).delete(synchronize_session=False)
    db.session.commit()
    flash('Mensagem removida.', 'warning')
    return redirect(url_for('notificacoes.index') + '#tab-chatbot')


@notificacoes_bp.route('/defaults/<condition_type>')
@login_required
def get_defaults(condition_type):
    return jsonify(_DEFAULTS.get(condition_type, {'manager': '', 'employee': ''}))


# ── CRUD: Regras de Palavra-chave do Chatbot ───────────────────────────────────

@notificacoes_bp.route('/keyword/nova', methods=['POST'])
@login_required
def keyword_nova():
    kw = (request.form.get('keyword') or '').strip().upper()
    resp = (request.form.get('resposta') or '').strip()
    if not kw or not resp:
        flash('Palavra-chave e resposta são obrigatórias.', 'danger')
        return redirect(url_for('notificacoes.index') + '#tab-chatbot')
    existing = BotKeywordRule.query.filter_by(keyword=kw).first()
    if existing:
        flash(f'Palavra-chave "{kw}" já existe. Use editar.', 'warning')
        return redirect(url_for('notificacoes.index') + '#tab-chatbot')
    rule = BotKeywordRule(
        keyword=kw,
        resposta=resp,
        tipo_msg=request.form.get('tipo_msg') or 'texto',
        interativo_json=request.form.get('interativo_json') or None,
        apenas_funcionario='apenas_funcionario' in request.form,
        ativo=True,
    )
    db.session.add(rule)
    db.session.commit()
    flash(f'Palavra-chave "{kw}" criada!', 'success')
    return redirect(url_for('notificacoes.index') + '#tab-chatbot')


@notificacoes_bp.route('/keyword/<int:kid>/editar', methods=['POST'])
@login_required
def keyword_editar(kid):
    rule = BotKeywordRule.query.get_or_404(kid)
    rule.keyword = (request.form.get('keyword') or '').strip().upper() or rule.keyword
    rule.resposta = (request.form.get('resposta') or '').strip() or rule.resposta
    rule.tipo_msg = request.form.get('tipo_msg') or rule.tipo_msg or 'texto'
    rule.interativo_json = request.form.get('interativo_json') or None
    rule.apenas_funcionario = 'apenas_funcionario' in request.form
    db.session.commit()
    flash(f'Palavra-chave "{rule.keyword}" atualizada!', 'success')
    return redirect(url_for('notificacoes.index') + '#tab-chatbot')


@notificacoes_bp.route('/keyword/<int:kid>/toggle', methods=['POST'])
@login_required
def keyword_toggle(kid):
    rule = BotKeywordRule.query.get_or_404(kid)
    rule.ativo = not rule.ativo
    db.session.commit()
    return jsonify({'ok': True, 'ativo': rule.ativo})


@notificacoes_bp.route('/keyword/<int:kid>/excluir', methods=['POST'])
@login_required
def keyword_excluir(kid):
    rule = BotKeywordRule.query.get_or_404(kid)
    kw = rule.keyword
    db.session.delete(rule)
    db.session.commit()
    flash(f'Palavra-chave "{kw}" removida.', 'warning')
    return redirect(url_for('notificacoes.index') + '#tab-chatbot')
