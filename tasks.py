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
        from zoneinfo import ZoneInfo
        agora = datetime.now(ZoneInfo('America/Sao_Paulo'))
        data_inicio = (agora - timedelta(hours=janela_horas)).strftime('%Y-%m-%d')
        hora_inicio = (agora - timedelta(hours=janela_horas)).strftime('%H:%M')
        data_fim = agora.strftime('%Y-%m-%d')
        hora_fim = agora.strftime('%H:%M')
        ok, msg = sync_batidas(data_inicio, data_fim, hora_inicio, hora_fim)
        logger.info(f'[sync_completa] {msg}')
        return {'ok': ok, 'msg': msg}

    # ── Verificação diária: DB vs Secullum (dia anterior) ─────────────────────

    @celery.task(name='tasks.verificar_inconsistencias_dia_anterior')
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
            from datetime import time as dtime
            alvo_min = h * 60 + m
            atual_min = agora.hour * 60 + agora.minute
            if not (0 <= atual_min - alvo_min < 5):
                return {'skipped': True, 'reason': 'fora do horario', 'hora_cfg': hora_cfg, 'hora_agora': hora_agora}
        except (ValueError, AttributeError):
            return {'skipped': True, 'reason': 'hora_cfg invalida'}

        # Evita rodar mais de uma vez por dia
        ultimo_run = _get_cfg('verificar_incons_ultimo_run', '')
        hoje_str = agora.strftime('%Y-%m-%d')
        if ultimo_run.startswith(hoje_str):
            return {'skipped': True, 'reason': 'ja executou hoje'}

        _set_cfg('verificar_incons_ultimo_run', agora.isoformat())

        ontem = (agora.date() - timedelta(days=1))
        ontem_str = ontem.strftime('%Y-%m-%d')

        logger.info(f'[verificar_incons] Verificando divergências para {ontem_str}...')

        # ── Comparar batidas locais vs API Secullum ────────────────────────────
        import os
        try:
            from services.config_service import get_secullum_api
            from services.sync_service import parse_date
            from models import Batida, Funcionario

            api = get_secullum_api()
            registros_api = api.buscar_batidas(ontem_str, ontem_str)
            if registros_api is None:
                msg = 'Falha ao conectar com a API Secullum.'
                _set_cfg('verificar_incons_ultimo_resultado', f'ERRO: {msg}')
                logger.error(f'[verificar_incons] {msg}')
                return {'ok': False, 'msg': msg}

            _MARCACOES_ESPECIAIS = {
                'ATESTAD', 'ATESTADO', 'FOLGA', 'FALTA', 'FERIAS',
                'NEUTRO', 'DSRFOL', 'DSRFALTA', 'COMPENSAR',
            }

            sec_map = {}
            for reg in registros_api:
                fid = str(reg.get('FuncionarioId'))
                d = parse_date(reg.get('Data'))
                if not d:
                    continue
                horas = []
                for i in range(1, 6):
                    for campo in [f'Entrada{i}', f'Saida{i}']:
                        hora = (reg.get(campo) or '').strip()
                        if hora and hora.upper() not in _MARCACOES_ESPECIAIS and hora not in ('00:00', '00:00:00'):
                            partes = hora.split(':')
                            if len(partes) >= 2:
                                horas.append(f'{partes[0]}:{partes[1]}')
                if horas:
                    sec_map[(fid, d)] = horas

            from extensions import db
            batidas_locais = (
                Batida.query
                .join(Funcionario, Batida.funcionario_id == Funcionario.id)
                .filter(Funcionario.ativo == True, Batida.data == ontem)
                .all()
            )
            local_map = {}
            for b in batidas_locais:
                local_map.setdefault((b.funcionario_id, b.data), []).append(b)

            divergencias = [
                (fid, dia)
                for (fid, dia) in set(sec_map.keys()) | set(local_map.keys())
                if len(sec_map.get((fid, dia), [])) != len(local_map.get((fid, dia), []))
            ]

            n_div = len(divergencias)
            logger.info(f'[verificar_incons] {n_div} divergência(s) encontrada(s) para {ontem_str}.')

            if n_div == 0:
                resultado = f'OK – nenhuma divergência em {ontem_str}.'
                _set_cfg('verificar_incons_ultimo_resultado', resultado)
                return {'ok': True, 'divergencias': 0, 'msg': resultado}

            # ── Re-sincroniza o dia completo ───────────────────────────────────
            from services.sync_service import sync_batidas as _sync_batidas
            ok_sync, msg_sync = _sync_batidas(ontem_str, ontem_str)
            resultado = f'{n_div} divergência(s) em {ontem_str} → re-sync: {msg_sync}'
            _set_cfg('verificar_incons_ultimo_resultado', resultado)
            logger.info(f'[verificar_incons] {resultado}')
            return {'ok': ok_sync, 'divergencias': n_div, 'msg': resultado}

        except Exception as e:
            msg = f'Erro inesperado: {e}'
            _set_cfg('verificar_incons_ultimo_resultado', f'ERRO: {msg}')
            logger.error(f'[verificar_incons] {msg}')
            return {'ok': False, 'msg': msg}

    return sync_secullum
