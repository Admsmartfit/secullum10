"""
Motor de processamento de regras de notificação WhatsApp (Fase 4).
Avalia condições de negócio e despacha mensagens via whatsapp_bot.
"""
import os
from datetime import datetime, date

from extensions import db
from models import NotificationRule, AlocacaoDiaria, Batida, Funcionario

GESTOR_CELULAR = os.getenv('GESTOR_CELULAR', '')


# ── Helpers ────────────────────────────────────────────────────────────────────

def _render(template: str, func, minutos: int = 0, aloc=None, data_ref=None) -> str:
    if not template:
        return ''
    partes = func.nome.split()
    subs = {
        '{name}':      partes[0] if partes else func.nome,
        '{full_name}': func.nome,
        '{minutes}':   str(minutos),
        '{turno}':     aloc.turno.nome if aloc else '',
        '{inicio}':    aloc.turno.hora_inicio.strftime('%H:%M') if aloc else '',
        '{fim}':       aloc.turno.hora_fim.strftime('%H:%M') if aloc else '',
        '{data}':      (data_ref or date.today()).strftime('%d/%m/%Y'),
    }
    for var, val in subs.items():
        template = template.replace(var, val)
    return template


def _celular_gestor(func) -> str:
    if func and func.departamento:
        from models import UnidadeLider
        ul = UnidadeLider.query.filter_by(departamento=func.departamento).first()
        if ul and ul.celular_lider:
            return ul.celular_lider
    return GESTOR_CELULAR


def _combine(d: date, t) -> datetime:
    return datetime.combine(d, t)


def _parse_hora(hora_str: str, data_ref: date) -> datetime:
    return datetime.strptime(hora_str[:5], '%H:%M').replace(
        year=data_ref.year, month=data_ref.month, day=data_ref.day
    )


# ── Checadores de condição ─────────────────────────────────────────────────────

def _checar_atraso(func_id, data_ref: date, aloc, threshold: int):
    batidas = (Batida.query.filter_by(funcionario_id=func_id, data=data_ref)
               .order_by(Batida.hora).all())
    if not batidas:
        return False, 0
    primeira = _parse_hora(batidas[0].hora, data_ref)
    ini_turno = _combine(data_ref, aloc.turno.hora_inicio)
    diff = (primeira - ini_turno).total_seconds() / 60
    return (True, int(diff)) if diff > threshold else (False, 0)


def _checar_hora_extra(func_id, data_ref: date, aloc, threshold: int):
    batidas = (Batida.query.filter_by(funcionario_id=func_id, data=data_ref)
               .order_by(Batida.hora).all())
    if len(batidas) < 2:
        return False, 0
    ultima = _parse_hora(batidas[-1].hora, data_ref)
    fim_turno = _combine(data_ref, aloc.turno.hora_fim)
    diff = (ultima - fim_turno).total_seconds() / 60
    return (True, int(diff)) if diff > threshold else (False, 0)


def _checar_antecipacao(func_id, data_ref: date, aloc, threshold: int):
    batidas = (Batida.query.filter_by(funcionario_id=func_id, data=data_ref)
               .order_by(Batida.hora).all())
    if len(batidas) < 2:
        return False, 0
    ultima = _parse_hora(batidas[-1].hora, data_ref)
    fim_turno = _combine(data_ref, aloc.turno.hora_fim)
    diff = (fim_turno - ultima).total_seconds() / 60
    return (True, int(diff)) if diff > threshold else (False, 0)


def _checar_ausencia(func_id, data_ref: date) -> bool:
    return Batida.query.filter_by(funcionario_id=func_id, data=data_ref).count() == 0


# ── Envio ──────────────────────────────────────────────────────────────────────

def _enviar(regra: NotificationRule, func, minutos: int, aloc, data_ref: date) -> int:
    from services.whatsapp_bot import enviar_texto
    enviados = 0

    if regra.dest_employee and func.celular:
        msg = _render(regra.template_employee or '', func, minutos, aloc, data_ref)
        if msg and enviar_texto(celular=func.celular, mensagem=msg, func_id=func.id, tipo='regra'):
            enviados += 1

    if regra.dest_manager:
        cel = _celular_gestor(func)
        if cel:
            msg = _render(regra.template_manager or '', func, minutos, aloc, data_ref)
            if msg and enviar_texto(celular=cel, mensagem=msg, func_id=func.id, tipo='regra'):
                enviados += 1

    if regra.dest_rh and GESTOR_CELULAR:
        msg = _render(regra.template_manager or '', func, minutos, aloc, data_ref)
        if msg and enviar_texto(celular=GESTOR_CELULAR, mensagem=msg, func_id=func.id, tipo='regra'):
            enviados += 1

    return enviados


# ── Processador principal ──────────────────────────────────────────────────────

def processar_regras_evento(trigger_type: str, data_ref: date = None) -> dict:
    """
    Avalia todas as regras ativas para o trigger dado.
    Chamado após sync de batidas ou manualmente.
    """
    if data_ref is None:
        data_ref = date.today()

    regras = NotificationRule.query.filter_by(ativo=True, trigger_type=trigger_type).all()
    if not regras:
        return {'regras': 0, 'mensagens': 0}

    alocacoes = (
        AlocacaoDiaria.query
        .filter_by(data=data_ref)
        .join(Funcionario)
        .filter(Funcionario.ativo == True)
        .all()
    )

    total = 0
    now_t = datetime.now().time()

    for regra in regras:
        enviados_regra = 0
        for aloc in alocacoes:
            func = aloc.funcionario
            if not func:
                continue

            # Janela de expediente
            if regra.only_working_hours:
                if not (aloc.turno.hora_inicio <= now_t <= aloc.turno.hora_fim):
                    continue

            threshold = regra.threshold_minutes or 15
            matched, minutos = False, 0

            if regra.condition_type == 'LATE_ENTRY':
                matched, minutos = _checar_atraso(func.id, data_ref, aloc, threshold)
            elif regra.condition_type == 'OVERTIME':
                matched, minutos = _checar_hora_extra(func.id, data_ref, aloc, threshold)
            elif regra.condition_type == 'EARLY_LEAVE':
                matched, minutos = _checar_antecipacao(func.id, data_ref, aloc, threshold)
            elif regra.condition_type == 'ABSENCE':
                matched = _checar_ausencia(func.id, data_ref)
            elif regra.condition_type == 'INTERJORNADA':
                from services.motor_clt import validar_interjornada
                if validar_interjornada(func.id, data_ref, aloc.turno):
                    matched = True

            if matched:
                enviados_regra += _enviar(regra, func, minutos, aloc, data_ref)

        if enviados_regra > 0:
            regra.mensagens_enviadas = (regra.mensagens_enviadas or 0) + enviados_regra
            regra.ultima_execucao = datetime.utcnow()
        total += enviados_regra

    db.session.commit()
    return {'regras': len(regras), 'mensagens': total}


def processar_regras_agendadas() -> dict:
    """
    Verifica regras DAILY e WEEKLY para a hora/dia atual.
    Chamado a cada hora via Celery beat.
    """
    agora = datetime.now()
    hora_atual = agora.hour
    dia_atual  = agora.weekday()

    regras = NotificationRule.query.filter(
        NotificationRule.ativo == True,
        NotificationRule.trigger_type.in_(['DAILY', 'WEEKLY']),
        NotificationRule.trigger_hour == hora_atual,
    ).all()

    total = 0
    for regra in regras:
        if regra.trigger_type == 'WEEKLY' and regra.trigger_weekday != dia_atual:
            continue
        result = processar_regras_evento(regra.trigger_type)
        total += result.get('mensagens', 0)

    return {'total': total}
