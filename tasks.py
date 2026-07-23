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

        # 2. Somente depois de atualizar, envia o relatório via serviço centralizado
        try:
            from services.report_service import disparar_relatorio_inconsistencias
            total_env = disparar_relatorio_inconsistencias(ontem)
            
            # Atualizamos o resultado para refletir os envios
            resultado = f"{resultado}. Relatórios enviados: {total_env}."
            _set_cfg('verificar_incons_ultimo_resultado', resultado)
            
            if total_env > 0:
                logger.info(f'[verificar_incons] {total_env} mensagem(ns) enviada(s).')
        except Exception as e:
            logger.error(f'[verificar_incons] Falha ao disparar relatórios: {e}')

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

    # PRD Antiban Fase 1: tasks.processar_fila_notificacoes removida — o despacho
    # da fila agora é feito por services/envio_dispatcher.py via APScheduler
    # (services/auto_sync.py), não mais por esta task/beat.

    @celery.task(name='tasks.processar_evento_instancia')
    def processar_evento_instancia(payload: dict):
        """PRD Antiban Fase 0: classifica o evento bruto de conexão/desconexão
        da instância Mega-API (recebido dentro do webhook já existente) e
        alerta por e-mail em caso de desconexão."""
        from blueprints.whatsapp import _processar_evento_instancia
        result = _processar_evento_instancia(payload)
        logger.info(f'[processar_evento_instancia] {result}')
        return result

    @celery.task(name='tasks.processar_regras_evento_sync')
    def processar_regras_evento_sync():
        from services.notification_processor import processar_regras_evento
        result = processar_regras_evento('EVENT_SYNC')
        logger.info(f'[regras_evento_sync] {result}')
        return result

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

    # ── Avaliação 360° ─────────────────────────────────────────────────────────

    @celery.task(name='tasks.avaliacao_verificar_disparo')
    def avaliacao_verificar_disparo():
        """Verifica diariamente se algum ciclo pendente deve ser disparado hoje.
        Cria e dispara automaticamente quando `proximo_ciclo_data` == hoje.
        """
        from models import CicloAvaliacao
        from services.avaliacao_service import criar_ciclo, gerar_tokens_ciclo, enviar_convites_ciclo

        hoje = date.today()
        # Encontra o último ciclo de cada departamento com proximo_ciclo_data == hoje
        ciclos_trigger = CicloAvaliacao.query.filter(
            CicloAvaliacao.proximo_ciclo_data == hoje,
            CicloAvaliacao.status.in_(['fechado', 'inconclusivo']),
        ).all()

        disparados = 0
        for ciclo_anterior in ciclos_trigger:
            try:
                novo = criar_ciclo(departamento=ciclo_anterior.departamento)
                gerar_tokens_ciclo(novo.id)
                enviar_convites_ciclo(novo.id)
                disparados += 1
                logger.info(f'[avaliacao] Ciclo automático {novo.id} criado (departamento={novo.departamento})')
            except Exception as e:
                logger.error(f'[avaliacao] Erro ao disparar ciclo automático: {e}')

        return {'disparados': disparados, 'data': str(hoje)}

    @celery.task(name='tasks.avaliacao_lembretes')
    def avaliacao_lembretes():
        """Envia lembretes de 24h e 48h para tokens não respondidos."""
        from models import CicloAvaliacao
        from services.avaliacao_service import enviar_lembretes

        ciclos_ativos = CicloAvaliacao.query.filter_by(status='ativo').all()
        total_24h = total_48h = 0

        for ciclo in ciclos_ativos:
            r24 = enviar_lembretes(ciclo.id, horas=24)
            r48 = enviar_lembretes(ciclo.id, horas=48)
            total_24h += r24.get('lembretes_enviados', 0)
            total_48h += r48.get('lembretes_enviados', 0)

        return {'lembretes_24h': total_24h, 'lembretes_48h': total_48h}

    @celery.task(name='tasks.avaliacao_fechar_expirados')
    def avaliacao_fechar_expirados():
        """Fecha automaticamente ciclos cujo prazo de coleta expirou.
        PRD §7: se amostra de alunos < 10, reagenda novo disparo em 7 dias.
        """
        from models import CicloAvaliacao, TokenAvaliacao, ScoreAvaliacao
        from extensions import db
        from services.avaliacao_service import (fechar_ciclo, calcular_scores_ciclo,
                                                 criar_ciclo, gerar_tokens_ciclo,
                                                 enviar_convites_ciclo)

        hoje = date.today()
        ciclos = CicloAvaliacao.query.filter(
            CicloAvaliacao.status == 'ativo',
            CicloAvaliacao.data_fim_coleta < hoje,
        ).all()

        fechados = reagendados = 0
        for ciclo in ciclos:
            try:
                # Calcula scores antes de fechar para verificar amostra
                scores = calcular_scores_ciclo(ciclo.id)

                # PRD §7: verifica se algum professor ficou com amostra insuficiente
                scores_inconclusivos = [s for s in scores if not s.get('conclusivo')]
                if scores_inconclusivos:
                    # Reagenda novo disparo em 7 dias conforme PRD §7
                    novo_ciclo = criar_ciclo(departamento=ciclo.departamento)
                    # Sobrescreve data_inicio e data_fim_coleta para 7 dias à frente
                    novo_ciclo.data_inicio = hoje + timedelta(days=7)
                    novo_ciclo.data_fim_coleta = hoje + timedelta(days=10)
                    db.session.commit()
                    gerar_tokens_ciclo(novo_ciclo.id)
                    enviar_convites_ciclo(novo_ciclo.id)
                    reagendados += 1
                    logger.info(
                        f'[avaliacao] Ciclo {ciclo.id} inconclusivo '
                        f'({len(scores_inconclusivos)} prof.) — reagendado ciclo {novo_ciclo.id} '
                        f'para {novo_ciclo.data_inicio}'
                    )

                fechar_ciclo(ciclo.id)
                fechados += 1
                logger.info(f'[avaliacao] Ciclo {ciclo.id} fechado automaticamente.')
            except Exception as e:
                logger.error(f'[avaliacao] Erro ao fechar ciclo {ciclo.id}: {e}')

        return {'fechados': fechados, 'reagendados': reagendados}

    @celery.task(name='tasks.avaliacao_timeout_12h')
    def avaliacao_timeout_12h():
        """PRD v3.0 §5: Cancela estados AVALIACAO_* com mais de 12h sem interação."""
        try:
            with app.app_context():
                from models import ChatState
                from extensions import db
                limite = datetime.utcnow() - timedelta(hours=12)
                expirados = ChatState.query.filter(
                    ChatState.estado.like('AVALIACAO_%'),
                    ChatState.atualizado_em < limite,
                ).all()
                for s in expirados:
                    s.estado = 'IDLE'
                    s.contexto = '{}'
                db.session.commit()
                logger.info(f'[avaliacao] Timeout 12h: {len(expirados)} estado(s) resetado(s).')
                return {'resetados': len(expirados)}
        except Exception as e:
            logger.error(f'[avaliacao] Erro no timeout 12h: {e}')
            return {'erro': str(e)}

    return {
        'sync_secullum': celery.tasks.get('tasks.sync_secullum'),
        'verificar_incons': celery.tasks.get('tasks.verificar_inconsistencias_dia_anterior')
    }
