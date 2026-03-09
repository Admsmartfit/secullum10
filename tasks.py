"""
Celery tasks – jobs assincronos e agendados (Etapas 1-4).
"""
from celery.utils.log import get_task_logger
from datetime import date, datetime, timedelta

logger = get_task_logger(__name__)


def _get_cfg(chave, default):
    """Lê valor de Configuracao pelo chave, retornando default se não existir."""
    try:
        from models import Configuracao
        row = Configuracao.query.filter_by(chave=chave).first()
        return row.valor if row and row.valor is not None else default
    except Exception:
        return default


def _set_cfg(chave, valor):
    """Salva ou atualiza Configuracao."""
    try:
        from models import Configuracao
        from extensions import db
        row = Configuracao.query.filter_by(chave=chave).first()
        if row:
            row.valor = str(valor)
        else:
            db.session.add(Configuracao(chave=chave, valor=str(valor)))
        db.session.commit()
    except Exception as e:
        logger.error(f'[_set_cfg] Erro ao salvar {chave}: {e}')


def register_tasks(celery):

    @celery.task(name='tasks.sync_secullum')
    def sync_secullum():
        from services.sync_service import sync_funcionarios, sync_batidas_incremental
        logger.info('[CELERY] Sync Secullum...')
        ok_f, msg_f = sync_funcionarios()
        logger.info(f'Funcionarios: {msg_f}')
        ok_b, msg_b = sync_batidas_incremental()
        logger.info(f'Batidas: {msg_b}')
        return {'funcionarios': msg_f, 'batidas': msg_b}

    @celery.task(name='tasks.bot_ausencia')
    def bot_ausencia():
        """Proxy: executa regras DAILY_ABSENCE ativas no banco de dados."""
        from services.notification_processor import processar_regras_evento
        result = processar_regras_evento('EVENT_ABSENCE')
        logger.info(f'[bot_ausencia] {result}')
        return result

    @celery.task(name='tasks.checkin_previo')
    def checkin_previo():
        """Proxy: executa regras PRE_CHECKIN ativas no banco de dados."""
        from services.notification_processor import processar_regras_evento
        result = processar_regras_evento('EVENT_HOURLY')
        logger.info(f'[checkin_previo] {result}')
        return result

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

    @celery.task(name='tasks.processar_regras_agendadas')
    def processar_regras_agendadas():
        """Processa regras DAILY/WEEKLY para a hora atual."""
        from services.notification_processor import processar_regras_agendadas as _proc
        result = _proc()
        logger.info(f'[regras_agendadas] {result}')
        return result

    @celery.task(name='tasks.processar_fila_notificacoes')
    def processar_fila_notificacoes():
        """Despacha mensagens enfileiradas (Direito à Desconexão) cujo horário chegou."""
        from services.notification_processor import processar_fila_notificacoes as _proc
        result = _proc()
        logger.info(f'[fila_notificacoes] {result}')
        return result

    @celery.task(name='tasks.processar_regras_evento_sync')
    def processar_regras_evento_sync():
        """Processa regras EVENT_SYNC após cada ciclo de sync de batidas."""
        from services.notification_processor import processar_regras_evento
        result = processar_regras_evento('EVENT_SYNC')
        logger.info(f'[regras_evento_sync] {result}')
        return result

    @celery.task(name='tasks.sync_horarios_e_alocacoes')
    def sync_horarios_e_alocacoes():
        """Sincroniza Horários da API Secullum e gera AlocacaoDiaria para 60 dias."""
        from datetime import date, timedelta
        from services.sync_service import sync_horarios, sync_alocacoes
        ok_h, msg_h = sync_horarios()
        logger.info(f'[sync_horarios] {msg_h}')
        data_ini = date.today().strftime('%Y-%m-%d')
        data_fim = (date.today() + timedelta(days=60)).strftime('%Y-%m-%d')
        ok_a, msg_a = sync_alocacoes(data_ini, data_fim)
        logger.info(f'[sync_alocacoes] {msg_a}')
        return {'horarios': msg_h, 'alocacoes': msg_a}

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

    @celery.task(name='tasks.gerar_acordos_mensais')
    def gerar_acordos_mensais():
        """Periodic Task: Dia 01 – Gera e envia acordos de banco de horas."""
        from models import Funcionario, TemplateDocumento
        from services.documento_service import gerar_pdf_de_template, enviar_documentos_para_lider
        from services.whatsapp_bot import enviar_documento as enviar_wa
        
        hoje = date.today()
        if hoje.day != 1:
            logger.info('[acordos_mensais] Hoje não é dia 01, abortando.')
            return {'status': 'dia_incorreto'}

        template = TemplateDocumento.query.filter_by(nome='Acordo de Banco de Horas').first()
        if not template:
            logger.error('[acordos_mensais] Template "Acordo de Banco de Horas" não encontrado.')
            return {'status': 'template_faltando'}

        funcionarios = Funcionario.query.filter_by(ativo=True).all()
        enviados = 0
        
        for f in funcionarios:
            try:
                pdf_bytes, nome_pdf = gerar_pdf_de_template(template, f)
                
                # 1. Enviar para Líder (e-mail)
                enviar_documentos_para_lider(f, [(pdf_bytes, nome_pdf)])
                
                # 2. Enviar para Funcionário (WhatsApp)
                if f.celular:
                    enviar_wa(
                        celular=f.celular,
                        pdf_bytes=pdf_bytes,
                        filename=nome_pdf,
                        caption=f'Olá, {f.nome}! Segue seu acordo de banco de horas para assinatura.',
                        func_id=f.id
                    )
                enviados += 1
            except Exception as e:
                logger.error(f'[acordos_mensais] Erro para {f.id}: {e}')

        return {'enviados': enviados}

    # ── Sync automático de batidas (configurável) ─────────────────────────────

    @celery.task(name='tasks.sync_batidas_rapida')
    def sync_batidas_rapida():
        """Sync incremental. Roda a cada minuto mas self-limita pelo intervalo configurado."""
        if _get_cfg('sync_rapida_ativo', '1') != '1':
            return {'skipped': True, 'reason': 'desativado'}

        intervalo = int(_get_cfg('sync_rapida_intervalo_min', '10'))
        ultimo_str = _get_cfg('sync_rapida_ultimo_run', '')
        if ultimo_str:
            try:
                ultimo = datetime.fromisoformat(ultimo_str)
                if (datetime.now() - ultimo).total_seconds() < intervalo * 60:
                    return {'skipped': True, 'reason': 'interval'}
            except ValueError:
                pass

        _set_cfg('sync_rapida_ultimo_run', datetime.now().isoformat())
        from services.sync_service import sync_batidas_incremental
        ok, msg = sync_batidas_incremental()
        logger.info(f'[sync_rapida] {msg}')
        return {'ok': ok, 'msg': msg}

    @celery.task(name='tasks.sync_batidas_completa')
    def sync_batidas_completa():
        """Sync completo com janela configurável. Roda a cada 5 min, self-limita pelo intervalo."""
        if _get_cfg('sync_completa_ativo', '1') != '1':
            return {'skipped': True, 'reason': 'desativado'}

        intervalo = int(_get_cfg('sync_completa_intervalo_min', '60'))
        ultimo_str = _get_cfg('sync_completa_ultimo_run', '')
        if ultimo_str:
            try:
                ultimo = datetime.fromisoformat(ultimo_str)
                if (datetime.now() - ultimo).total_seconds() < intervalo * 60:
                    return {'skipped': True, 'reason': 'interval'}
            except ValueError:
                pass

        janela_horas = int(_get_cfg('sync_completa_janela_horas', '12'))
        _set_cfg('sync_completa_ultimo_run', datetime.now().isoformat())

        from services.sync_service import sync_batidas
        agora = datetime.now()
        data_inicio = (agora - timedelta(hours=janela_horas)).strftime('%Y-%m-%d')
        hora_inicio = (agora - timedelta(hours=janela_horas)).strftime('%H:%M')
        data_fim = agora.strftime('%Y-%m-%d')
        hora_fim = agora.strftime('%H:%M')
        ok, msg = sync_batidas(data_inicio, data_fim, hora_inicio, hora_fim)
        logger.info(f'[sync_completa] {msg}')
        return {'ok': ok, 'msg': msg}

    return sync_secullum
