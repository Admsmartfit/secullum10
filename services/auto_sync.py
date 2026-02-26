"""
Auto-sync de batidas via APScheduler (roda dentro do processo Flask).
Não requer Celery Worker/Beat separado.

Dois jobs:
  - job_rapida  : sync incremental (desde última sync até agora)
  - job_completa: sync com janela configurável (N horas atrás → agora)

Os intervalos são lidos da tabela `configuracoes` a cada execução,
então mudanças na config têm efeito no próximo ciclo sem restart.
"""
from datetime import datetime, timedelta
import logging

logger = logging.getLogger('auto_sync')

# Instância global do scheduler (criada uma vez em init_scheduler)
_scheduler = None


def _get_cfg(chave, default):
    try:
        from models import Configuracao
        row = Configuracao.query.filter_by(chave=chave).first()
        return row.valor if row and row.valor is not None else default
    except Exception:
        return default


def _set_cfg(chave, valor):
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
        logger.error(f'[auto_sync] Erro ao salvar {chave}: {e}')


def _deve_rodar(chave_ativo, chave_intervalo, chave_ultimo_run, intervalo_default):
    """Retorna True se o job deve rodar agora (ativo + intervalo atingido)."""
    if _get_cfg(chave_ativo, '1') != '1':
        return False
    intervalo = int(_get_cfg(chave_intervalo, str(intervalo_default)))
    ultimo_str = _get_cfg(chave_ultimo_run, '')
    if ultimo_str:
        try:
            ultimo = datetime.fromisoformat(ultimo_str)
            if (datetime.now() - ultimo).total_seconds() < intervalo * 60:
                return False
        except ValueError:
            pass
    return True


def job_rapida(app):
    """Sync incremental — roda a cada minuto, self-limita pelo intervalo configurado."""
    with app.app_context():
        if not _deve_rodar('sync_rapida_ativo', 'sync_rapida_intervalo_min',
                           'sync_rapida_ultimo_run', 10):
            return
        try:
            _set_cfg('sync_rapida_ultimo_run', datetime.now().isoformat())
            from services.sync_service import sync_batidas_incremental
            ok, msg = sync_batidas_incremental()
            logger.info(f'[sync_rapida] {msg}')
        except Exception as e:
            logger.error(f'[sync_rapida] Erro: {e}')


def job_completa(app):
    """Sync com janela — roda a cada 5 min, self-limita pelo intervalo configurado."""
    with app.app_context():
        if not _deve_rodar('sync_completa_ativo', 'sync_completa_intervalo_min',
                           'sync_completa_ultimo_run', 60):
            return
        try:
            janela = int(_get_cfg('sync_completa_janela_horas', '12'))
            _set_cfg('sync_completa_ultimo_run', datetime.now().isoformat())
            from services.sync_service import sync_batidas
            agora = datetime.now()
            ok, msg = sync_batidas(
                (agora - timedelta(hours=janela)).strftime('%Y-%m-%d'),
                agora.strftime('%Y-%m-%d'),
                (agora - timedelta(hours=janela)).strftime('%H:%M'),
                agora.strftime('%H:%M'),
            )
            logger.info(f'[sync_completa] {msg}')
        except Exception as e:
            logger.error(f'[sync_completa] Erro: {e}')


def init_scheduler(app):
    """Inicializa o APScheduler e registra os jobs. Chame uma vez em create_app()."""
    global _scheduler

    # Evita duplo registro no modo debug (Werkzeug reloader)
    import os
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'false':
        return  # processo pai do reloader — não registrar aqui

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    _scheduler = BackgroundScheduler(timezone='America/Sao_Paulo')

    _scheduler.add_job(
        func=job_rapida,
        args=[app],
        trigger=IntervalTrigger(minutes=1),
        id='sync_rapida',
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    _scheduler.add_job(
        func=job_completa,
        args=[app],
        trigger=IntervalTrigger(minutes=5),
        id='sync_completa',
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    _scheduler.start()
    logger.info('[auto_sync] APScheduler iniciado (sync_rapida: 1min, sync_completa: 5min)')

    # Garante shutdown limpo
    import atexit
    def _shutdown():
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
    atexit.register(_shutdown)
