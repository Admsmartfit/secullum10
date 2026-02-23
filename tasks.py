"""
Celery tasks – jobs assincronos e agendados (Etapas 1-4).
"""
from celery.utils.log import get_task_logger
from datetime import date, timedelta

logger = get_task_logger(__name__)


def register_tasks(celery):

    @celery.task(name='tasks.sync_secullum')
    def sync_secullum():
        from services.sync_service import sync_funcionarios, sync_batidas
        logger.info('[CELERY] Sync Secullum...')
        ok_f, msg_f = sync_funcionarios()
        logger.info(f'Funcionarios: {msg_f}')
        data_fim = date.today().strftime('%Y-%m-%d')
        data_inicio = (date.today() - timedelta(days=2)).strftime('%Y-%m-%d')
        ok_b, msg_b = sync_batidas(data_inicio, data_fim)
        logger.info(f'Batidas: {msg_b}')
        return {'funcionarios': msg_f, 'batidas': msg_b}

    @celery.task(name='tasks.bot_ausencia')
    def bot_ausencia():
        from models import AlocacaoDiaria, Batida
        from services.whatsapp_bot import enviar_texto
        hoje = date.today()
        alocacoes = AlocacaoDiaria.query.filter_by(data=hoje).all()
        func_com_batida = {b.funcionario_id for b in Batida.query.filter_by(data=hoje).all()}
        enviados = 0
        for aloc in alocacoes:
            if aloc.funcionario_id in func_com_batida:
                continue
            func = aloc.funcionario
            if not func or not func.celular:
                continue
            msg = (f'Ola, {func.nome.split()[0]}! Voce ainda nao registrou ponto hoje. '
                   'Aconteceu algo? Responda esta mensagem.')
            if enviar_texto(celular=func.celular, mensagem=msg, func_id=func.id, tipo='ausencia'):
                enviados += 1
        logger.info(f'[bot_ausencia] {enviados} mensagens enviadas.')
        return {'enviados': enviados}

    @celery.task(name='tasks.checkin_previo')
    def checkin_previo():
        from datetime import datetime
        from models import AlocacaoDiaria
        from services.whatsapp_bot import enviar_texto
        agora = datetime.now()
        hoje = agora.date()
        hora_alvo = agora.hour + 1
        for aloc in AlocacaoDiaria.query.filter_by(data=hoje).all():
            if aloc.turno.hora_inicio.hour != hora_alvo:
                continue
            func = aloc.funcionario
            if not func or not func.celular or aloc.pre_checkin:
                continue
            enviar_texto(
                celular=func.celular,
                mensagem=(f'Lembrete: turno "{aloc.turno.nome}" começa em 1 hora '
                          f'({aloc.turno.hora_inicio.strftime("%H:%M")}). '
                          'Responda SIM para confirmar presenca.'),
                func_id=func.id, tipo='checkin',
            )

    @celery.task(name='tasks.calcular_banco_horas_todos')
    def calcular_banco_horas_todos():
        """Recalcula e persiste saldos de banco de horas para todos os funcionários
        com alocações nos últimos 30 dias. Executado diariamente às 01:00."""
        from datetime import date, timedelta
        from models import AlocacaoDiaria
        from services.banco_horas_service import salvar_saldos
        hoje = date.today()
        data_ini = hoje - timedelta(days=30)
        ids = {a.funcionario_id for a in
               AlocacaoDiaria.query.filter(AlocacaoDiaria.data >= data_ini).all()}
        erros = 0
        for fid in ids:
            try:
                salvar_saldos(fid, data_ini, hoje)
            except Exception as e:
                logger.error(f'[banco_horas] Erro para {fid}: {e}')
                erros += 1
        logger.info(f'[banco_horas] {len(ids)} funcionários recalculados, {erros} erros.')
        return {'calculados': len(ids), 'erros': erros}

    @celery.task(name='tasks.processar_webhook_whatsapp')
    def processar_webhook_whatsapp(data: dict):
        from blueprints.whatsapp import _processar_mensagem
        _processar_mensagem(data)

    @celery.task(name='tasks.alerta_documentos_vencendo')
    def alerta_documentos_vencendo():
        """RF5.4 – E-mail ao RH listando documentos que vencem em ≤ 30 dias."""
        import os
        from datetime import date, timedelta
        from models import ProntuarioDoc
        limite = date.today() + timedelta(days=30)
        docs = ProntuarioDoc.query.filter(
            ProntuarioDoc.data_vencimento.isnot(None),
            ProntuarioDoc.data_vencimento <= limite,
        ).all()
        if not docs:
            return {'docs': 0}
        rh_email = os.getenv('RH_EMAIL', '')
        if not rh_email:
            logger.warning('[alerta_docs] RH_EMAIL não configurado.')
            return {'docs': len(docs), 'enviado': False}
        linhas = []
        for d in docs:
            vencido = d.data_vencimento < date.today()
            status = 'VENCIDO' if vencido else f'vence {d.data_vencimento.strftime("%d/%m/%Y")}'
            linhas.append(f'- {d.funcionario.nome}: {d.tipo} ({d.nome_arquivo}) – {status}')
        try:
            from flask_mail import Message
            from extensions import mail
            msg = Message(
                subject=f'⚠️ {len(docs)} documento(s) vencendo – Secullum Hub',
                recipients=[rh_email],
                body='Documentos que requerem atenção:\n\n' + '\n'.join(linhas),
            )
            mail.send(msg)
            logger.info(f'[alerta_docs] E-mail para {rh_email}: {len(docs)} docs.')
        except Exception as e:
            logger.error(f'[alerta_docs] Falha ao enviar e-mail: {e}')
        return {'docs': len(docs)}

    return sync_secullum
