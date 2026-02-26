from flask import Flask
from dotenv import load_dotenv

load_dotenv()


def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')

    # ── Extensions ────────────────────────────────────────────────────────────
    from extensions import db, login_manager, migrate, mail
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    mail.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Faça login para acessar esta página.'
    login_manager.login_message_category = 'warning'

    @login_manager.user_loader
    def load_user(user_id):
        from models import Usuario
        return Usuario.query.get(int(user_id))

    # ── Blueprints ────────────────────────────────────────────────────────────
    from blueprints.auth import auth_bp
    from blueprints.dashboard import dashboard_bp
    from blueprints.funcionarios import funcionarios_bp
    from blueprints.espelho import espelho_bp
    from blueprints.relatorios import relatorios_bp
    from blueprints.api_sync import api_sync_bp
    from blueprints.escalas import escalas_bp
    from blueprints.financeiro import financeiro_bp
    from blueprints.whatsapp import whatsapp_bp
    from blueprints.marketplace import marketplace_bp
    from blueprints.prontuario import prontuario_bp
    from blueprints.config_hub import config_hub_bp
    from blueprints.notificacoes import notificacoes_bp
    from blueprints.trocas import trocas_bp
    from blueprints.inconsistencias import inconsistencias_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(funcionarios_bp)
    app.register_blueprint(espelho_bp)
    app.register_blueprint(relatorios_bp)
    app.register_blueprint(api_sync_bp)
    app.register_blueprint(escalas_bp)
    app.register_blueprint(financeiro_bp)
    app.register_blueprint(whatsapp_bp)
    app.register_blueprint(marketplace_bp)
    app.register_blueprint(prontuario_bp)
    app.register_blueprint(config_hub_bp)
    app.register_blueprint(notificacoes_bp)
    app.register_blueprint(trocas_bp)
    app.register_blueprint(inconsistencias_bp)

    # ── Celery beat schedule ───────────────────────────────────────────────────
    from extensions import make_celery
    celery = make_celery(app)
    from tasks import register_tasks
    register_tasks(celery)

    from celery.schedules import crontab
    celery.conf.beat_schedule = {
        'sync-secullum-every-15min': {
            'task': 'tasks.sync_secullum',
            'schedule': crontab(minute='*/15'),
        },
        'bot-ausencia-09h': {
            'task': 'tasks.bot_ausencia',
            'schedule': crontab(hour=9, minute=0),
        },
        'checkin-previo-every-hour': {
            'task': 'tasks.checkin_previo',
            'schedule': crontab(minute=0),
        },
        'calcular-banco-horas-daily': {
            'task': 'tasks.calcular_banco_horas_todos',
            'schedule': crontab(hour=1, minute=0),
        },
        'alerta-documentos-daily': {
            'task': 'tasks.alerta_documentos_vencendo',
            'schedule': crontab(hour=8, minute=0),
        },
        'processar-regras-agendadas-hourly': {
            'task': 'tasks.processar_regras_agendadas',
            'schedule': crontab(minute=5),  # :05 de cada hora
        },
        'sync-horarios-daily': {
            'task': 'tasks.sync_horarios_e_alocacoes',
            'schedule': crontab(hour=2, minute=0),  # 02:00 – gera alocações dos próximos 60 dias
        },
        'sync-batidas-rapida': {
            'task': 'tasks.sync_batidas_rapida',
            'schedule': crontab(minute='*'),  # verifica a cada minuto, self-limita por config
        },
        'sync-batidas-completa': {
            'task': 'tasks.sync_batidas_completa',
            'schedule': crontab(minute='*/5'),  # verifica a cada 5 min, self-limita por config
        },
    }
    celery.conf.timezone = 'America/Sao_Paulo'
    app.extensions['celery'] = celery

    # ── Context processors ────────────────────────────────────────────────────
    @app.context_processor
    def inject_sidebar_badges():
        """Injeta contadores de alertas no template base (sidebar badges)."""
        try:
            from flask_login import current_user
            if not current_user.is_authenticated:
                return {}
            from datetime import date, timedelta
            from sqlalchemy import func, distinct
            from models import BancoHorasSaldo, ProntuarioDoc
            from services.banco_horas_service import get_config

            # Badge banco de horas
            limite_dias = int(get_config('banco_horas_limite_dias', 30) or 30)
            data_limite = date.today() - timedelta(days=limite_dias)
            alertas_bh = db.session.query(
                func.count(distinct(BancoHorasSaldo.funcionario_id))
            ).filter(
                BancoHorasSaldo.data <= data_limite,
                BancoHorasSaldo.saldo_dia > 0,
            ).scalar() or 0

            # Badge documentos vencendo (RF5.4)
            alerta_docs = ProntuarioDoc.query.filter(
                ProntuarioDoc.data_vencimento.isnot(None),
                ProntuarioDoc.data_vencimento <= date.today() + timedelta(days=30),
            ).count()

            return {
                'alertas_banco_horas': int(alertas_bh),
                'alertas_docs': int(alerta_docs),
            }
        except Exception:
            return {'alertas_banco_horas': 0, 'alertas_docs': 0}

    # ── DB init ───────────────────────────────────────────────────────────────
    with app.app_context():
        import models  # noqa: garante que os models estão registrados
        db.create_all()

    # ── Auto-sync de batidas (APScheduler – roda no mesmo processo) ───────────
    from services.auto_sync import init_scheduler
    init_scheduler(app)

    return app


app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5010)
