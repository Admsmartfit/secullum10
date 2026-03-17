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


def sync_secullum():
    """Lógica central do sync (pode ser chamada via Celery ou APScheduler)."""
    from services.sync_service import sync_funcionarios, sync_batidas_incremental
    logger.info('[CELERY] Sync Secullum...')
    ok_f, msg_f = sync_funcionarios()
    logger.info(f'Funcionarios: {msg_f}')
    ok_b, msg_b = sync_batidas_incremental()
    logger.info(f'Batidas: {msg_b}')
    return {'funcionarios': msg_f, 'batidas': msg_b}


def verificar_inconsistencias_dia_anterior():
    """
    Verifica se as batidas do dia anterior diferem entre o banco local
    e o Secullum. Se houver divergências, re-sincroniza o dia todo.
    Roda a cada minuto mas self-limita pelo horário configurado.
    """
    if _get_cfg('verificar_incons_ativo', '0') != '1':
        return {'skipped': True, 'reason': 'desativado'}

    hora_cfg = _get_cfg('verificar_incons_hora', '01:00')
    from zoneinfo import ZoneInfo
    _tz_br = ZoneInfo('America/Sao_Paulo')
    agora = datetime.now(_tz_br)
    hora_agora = agora.strftime('%H:%M')

    # Só executa quando o horário atual está dentro de uma janela de 5 min
    try:
        h, m = [int(x) for x in hora_cfg.split(':')]
        alvo_min = h * 60 + m
        atual_min = agora.hour * 60 + agora.minute
        if not (0 <= atual_min - alvo_min < 5):
            return {'skipped': True, 'reason': 'fora do horario', 'hora_cfg': hora_cfg, 'hora_agora': hora_agora}
    except (ValueError, AttributeError):
        return {'skipped': True, 'reason': 'hora_cfg invalida'}

    # Evita rodar repetidas vezes na mesma janela configurada de hoje
    hoje_str = agora.strftime('%Y-%m-%d')
    chk_val = f"{hoje_str}_{hora_cfg}"
    if _get_cfg('verificar_incons_ultimo_auto_chk', '') == chk_val:
        return {'skipped': True, 'reason': 'ja executou neste horario hoje'}

    # NÃO marcamos como executado aqui — só após concluir com sucesso
    ontem = (agora.date() - timedelta(days=1))
    ontem_str = ontem.strftime('%Y-%m-%d')

    logger.info(f'[verificar_incons] Verificando divergências para {ontem_str}...')

    try:
        from services.sync_service import sync_batidas as _sync_batidas

        # 1. Sincroniza todas as batidas do dia anterior primeiro
        ok_sync, msg_sync = _sync_batidas(ontem_str, ontem_str)
        resultado = f'Sync do dia anterior ({ontem_str}) concluída: {msg_sync}'
        _set_cfg('verificar_incons_ultimo_resultado', resultado)
        logger.info(f'[verificar_incons] {resultado}')

        # 2. Somente depois de atualizar, envia o relatório
        try:
            from models import NotificationRule, UnidadeLider
            from extensions import db as _db
            from services.notification_processor import (
                _gerar_relatorio_inconsistencias,
                _gerar_relatorio_por_departamento,
                _normalizar_celular,
            )
            from services.whatsapp_bot import enviar_texto

            relatorio_global = _gerar_relatorio_inconsistencias(ontem)
            relatorios_dept  = _gerar_relatorio_por_departamento(ontem)

            # ── Envia para cada líder de departamento (SEMPRE, sem precisar de NotificationRule) ──
            unidades = UnidadeLider.query.filter(UnidadeLider.celular_lider.isnot(None)).all()
            alvos = [
                {'celular': _normalizar_celular(u.celular_lider), 'dept': u.departamento}
                for u in unidades if u.celular_lider
            ]
            total_env = 0
            for alvo in alvos:
                dept = alvo['dept']
                texto_dept = relatorios_dept.get(dept) or (
                    f'📋 Inconsistências — {dept} — {ontem_str}\n\n✅ Nenhuma inconsistência encontrada.'
                )
                try:
                    if enviar_texto(celular=alvo['celular'], mensagem=texto_dept, tipo='relatorio'):
                        total_env += 1
                except Exception as e_env:
                    logger.error(f'[verificar_incons] Falha ao enviar para líder de "{dept}" ({alvo["celular"]}): {e_env}')

            # ── Envia relatório global para gestor geral (apenas se existir NotificationRule ativa) ──
            regras = NotificationRule.query.filter_by(ativo=True, condition_type='INCONSISTENCY_REPORT').all()
            for regra in regras:
                if regra.dest_manager:
                    from services.config_service import get_gestor_celular
                    cel = get_gestor_celular()
                    if cel:
                        try:
                            if enviar_texto(celular=_normalizar_celular(cel), mensagem=relatorio_global, tipo='relatorio'):
                                total_env += 1
                        except Exception as e_env:
                            logger.error(f'[verificar_incons] Falha ao enviar relatório global: {e_env}')
                regra.mensagens_enviadas = (regra.mensagens_enviadas or 0) + 1
                regra.ultima_execucao = agora
            if regras:
                _db.session.commit()

            if total_env > 0:
                logger.info(f'[verificar_incons] {total_env} mensagem(ns) de inconsistência enviada(s).')
        except Exception as e:
            logger.error(f'[verificar_incons] Falha ao enviar relatório: {e}')

        # Marca como executado APENAS após o trabalho completo
        _set_cfg('verificar_incons_ultimo_auto_chk', chk_val)
        _set_cfg('verificar_incons_ultimo_run', agora.isoformat())

        return {'ok': ok_sync, 'divergencias': 0, 'msg': resultado}

    except Exception as e:
        msg = f'Erro inesperado: {e}'
        _set_cfg('verificar_incons_ultimo_resultado', f'ERRO: {msg}')
        logger.error(f'[verificar_incons] {msg}')
        return {'ok': False, 'msg': msg}


def register_tasks(celery):
    """Registra as tarefas no Celery Beat."""
    
    @celery.task(name='tasks.sync_secullum')
    def task_sync_secullum():
        return sync_secullum()

    @celery.task(name='tasks.bot_ausencia')
    def bot_ausencia():
        from services.notification_processor import processar_regras_evento
        result = processar_regras_evento('EVENT_ABSENCE')
        logger.info(f'[bot_ausencia] {result}')
        return result

    @celery.task(name='tasks.checkin_previo')
    def checkin_previo():
        from services.notification_processor import processar_regras_evento
        result = processar_regras_evento('EVENT_HOURLY')
        logger.info(f'[checkin_previo] {result}')
        return result

    @celery.task(name='tasks.calcular_banco_horas_todos')
    def calcular_banco_horas_todos():
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
        from services.notification_processor import processar_regras_agendadas as _proc
        result = _proc()
        logger.info(f'[regras_agendadas] {result}')
        return result

    @celery.task(name='tasks.processar_fila_notificacoes')
    def processar_fila_notificacoes():
        from services.notification_processor import processar_fila_notificacoes as _proc
        result = _proc()
        logger.info(f'[fila_notificacoes] {result}')
        return result

    @celery.task(name='tasks.processar_regras_evento_sync')
    def processar_regras_evento_sync():
        from services.notification_processor import processar_regras_evento
        result = processar_regras_evento('EVENT_SYNC')
        logger.info(f'[regras_evento_sync] {result}')
        return result

    @celery.task(name='tasks.sync_horarios_e_alocacoes')
    def sync_horarios_e_alocacoes():
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
        from models import Funcionario, TemplateDocumento
        from services.documento_service import gerar_pdf_de_template, enviar_documentos_para_lider
        from services.whatsapp_bot import enviar_documento as enviar_wa
        hoje = date.today()
        if hoje.day != 1:
            return {'status': 'dia_incorreto'}
        template = TemplateDocumento.query.filter_by(nome='Acordo de Banco de Horas').first()
        if not template:
            return {'status': 'template_faltando'}
        funcionarios = Funcionario.query.filter_by(ativo=True).all()
        enviados = 0
        for f in funcionarios:
            try:
                pdf_bytes, nome_pdf = gerar_pdf_de_template(template, f)
                enviar_documentos_para_lider(f, [(pdf_bytes, nome_pdf)])
                if f.celular:
                    enviar_wa(celular=f.celular, pdf_bytes=pdf_bytes, filename=nome_pdf, func_id=f.id)
                enviados += 1
            except Exception as e:
                logger.error(f'[acordos_mensais] Erro para {f.id}: {e}')
        return {'enviados': enviados}

    @celery.task(name='tasks.sincronizar_feriados_anuais')
    def sincronizar_feriados_anuais():
        """Roda em 1º de Janeiro: importa feriados nacionais e municipais do novo ano."""
        from services.feriados_service import sincronizar_feriados
        hoje = date.today()
        if hoje.month != 1 or hoje.day != 1:
            return {'skipped': True, 'reason': 'não é 1º de Janeiro'}
        result = sincronizar_feriados(hoje.year)
        logger.info(f'[feriados_anuais] {result}')
        return result

    @celery.task(name='tasks.sync_batidas_rapida')
    def sync_batidas_rapida():
        from services.sync_service import sync_batidas_incremental
        ok, msg = sync_batidas_incremental()
        return {'ok': ok, 'msg': msg}

    @celery.task(name='tasks.sync_batidas_completa')
    def sync_batidas_completa():
        from services.sync_service import sync_batidas
        janela_horas = int(_get_cfg('sync_completa_janela_horas', '12'))
        from zoneinfo import ZoneInfo
        agora = datetime.now(ZoneInfo('America/Sao_Paulo'))
        data_inicio = (agora - timedelta(hours=janela_horas)).strftime('%Y-%m-%d')
        hora_inicio = (agora - timedelta(hours=janela_horas)).strftime('%H:%M')
        data_fim = agora.strftime('%Y-%m-%d')
        hora_fim = agora.strftime('%H:%M')
        ok, msg = sync_batidas(data_inicio, data_fim, hora_inicio, hora_fim)
        return {'ok': ok, 'msg': msg}

    @celery.task(name='tasks.verificar_inconsistencias_dia_anterior')
    def task_verificar_inconsistencias():
        return verificar_inconsistencias_dia_anterior()

    return {
        'sync_secullum': celery.tasks.get('tasks.sync_secullum'),
        'verificar_incons': celery.tasks.get('tasks.verificar_inconsistencias_dia_anterior')
    }
