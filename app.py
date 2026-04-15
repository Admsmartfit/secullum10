from flask import Flask
from dotenv import load_dotenv

load_dotenv()


def _run_safe_migrations(db):
    """Applies ALTER TABLE migrations that are safe to run multiple times (IF NOT EXISTS)."""
    import logging
    log = logging.getLogger('migrations')
    migrations = [
        # Added for Motor A/B/C (Etapa 1)
        "ALTER TABLE turnos ADD COLUMN IF NOT EXISTS tipo_turno VARCHAR(1)",
        # Added for custom phone on notification rules
        "ALTER TABLE notification_rules ADD COLUMN IF NOT EXISTS dest_custom BOOLEAN DEFAULT FALSE",
        "ALTER TABLE notification_rules ADD COLUMN IF NOT EXISTS custom_phone VARCHAR(20)",
        # Added for War Room baseline
        "ALTER TABLE funcionarios ADD COLUMN IF NOT EXISTS horario_base_id INTEGER",
        "ALTER TABLE turnos ADD COLUMN IF NOT EXISTS funcao VARCHAR(100)",
        "ALTER TABLE alocacoes_diarias ADD COLUMN IF NOT EXISTS is_excecao BOOLEAN DEFAULT TRUE",
        # Added for feriados estaduais/municipais
        "ALTER TABLE feriados ADD COLUMN IF NOT EXISTS tipo VARCHAR(20) NOT NULL DEFAULT 'personalizado'",
        "ALTER TABLE feriados ADD COLUMN IF NOT EXISTS uf VARCHAR(2)",
        "ALTER TABLE feriados ADD COLUMN IF NOT EXISTS cidade_ibge VARCHAR(10)",
        "ALTER TABLE feriados ADD COLUMN IF NOT EXISTS fonte VARCHAR(50)",
        "ALTER TABLE feriados ADD COLUMN IF NOT EXISTS criado_por_id INTEGER",
        "ALTER TABLE feriados ADD COLUMN IF NOT EXISTS ativo BOOLEAN NOT NULL DEFAULT TRUE",
        # Added for UnidadeLider empresa fields
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS empresa_nome VARCHAR(300)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS empresa_cnpj VARCHAR(30)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS empresa_socio VARCHAR(300)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS socio_cpf VARCHAR(20)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS empresa_endereco VARCHAR(400)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS empresa_cidade VARCHAR(200)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS empresa_uf VARCHAR(5)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS empresa_cep VARCHAR(15)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS cidade_ibge VARCHAR(10)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS experiencia_dias INTEGER DEFAULT 45",
        # Avaliação 360° — novas colunas adicionadas em v2
        "ALTER TABLE ciclos_avaliacao ADD COLUMN IF NOT EXISTS departamento VARCHAR(200)",
        "ALTER TABLE ciclos_avaliacao ADD COLUMN IF NOT EXISTS proximo_ciclo_data DATE",
        "ALTER TABLE tokens_avaliacao ADD COLUMN IF NOT EXISTS lembrete_24h_em TIMESTAMP",
        "ALTER TABLE tokens_avaliacao ADD COLUMN IF NOT EXISTS lembrete_48h_em TIMESTAMP",
        "ALTER TABLE tokens_avaliacao ADD COLUMN IF NOT EXISTS expira_em TIMESTAMP",
        "ALTER TABLE scores_avaliacao ADD COLUMN IF NOT EXISTS token_resultado VARCHAR(64)",
        "ALTER TABLE scores_avaliacao ADD COLUMN IF NOT EXISTS resultado_enviado_em TIMESTAMP",
    ]
    for sql in migrations:
        try:
            db.session.execute(db.text(sql))
        except Exception as e:
            log.warning(f'[migration] skipped: {e}')
            db.session.rollback()
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


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
    from blueprints.avaliacoes import avaliacoes_bp, avaliacoes_public_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(funcionarios_bp)
    app.register_blueprint(espelho_bp)
    app.register_blueprint(relatorios_bp)
    app.register_blueprint(api_sync_bp)
    app.register_blueprint(escalas_bp)
    app.register_blueprint(financeiro_bp)
    app.register_blueprint(whatsapp_bp)
    # Alias para o webhook configurado no MegaAPI: POST /webhook/whatsapp
    from blueprints.whatsapp import webhook as _wh_handler
    app.add_url_rule('/webhook/whatsapp', 'webhook_megaapi_alias', _wh_handler, methods=['POST'])
    app.register_blueprint(marketplace_bp)
    app.register_blueprint(prontuario_bp)
    app.register_blueprint(config_hub_bp)
    app.register_blueprint(notificacoes_bp)
    app.register_blueprint(trocas_bp)
    app.register_blueprint(inconsistencias_bp)
    app.register_blueprint(avaliacoes_bp)
    app.register_blueprint(avaliacoes_public_bp)

    # ── Controlo de acesso por nível ──────────────────────────────────────────
    # Rotas permitidas ao nível 'gerente'. Adicione prefixos aqui quando quiser
    # liberar mais funcionalidades.
    GERENTE_WHITELIST = (
        '/login',                   # login
        '/logout',                  # logout  ← rota real (sem prefixo /auth/)
        '/static/',                 # ficheiros estáticos
        '/config/espelho',          # Espelho de Ponto
        '/config/funcionarios',     # Funcionários (visualização)
        '/inconsistencias/',        # Inconsistências
        '/escalas/',                # Gestão de Escalas
        '/prontuario/alertas',      # Prontuários
        '/prontuario/ver/',         # Prontuário individual
    )

    @app.before_request
    def restringir_gerente():
        from flask import request, redirect, url_for, flash
        from flask_login import current_user
        if not current_user.is_authenticated:
            return  # o login_required trata isto
        if current_user.nivel_acesso != 'gerente':
            return  # administrador e outros passam sem restrição
        path = request.path
        # dashboard (raiz exata) sempre permitido
        if path == '/':
            return
        if any(path.startswith(p) for p in GERENTE_WHITELIST):
            return  # rota permitida
        flash('Acesso não autorizado para o seu perfil.', 'warning')
        return redirect(url_for('espelho.espelho'))

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
'sync-batidas-rapida': {
            'task': 'tasks.sync_batidas_rapida',
            'schedule': crontab(minute='*'),  # verifica a cada minuto, self-limita por config
        },
        'sync-batidas-completa': {
            'task': 'tasks.sync_batidas_completa',
            'schedule': crontab(minute='*/5'),  # verifica a cada 5 min, self-limita por config
        },
        'processar-fila-notificacoes-hourly': {
            'task': 'tasks.processar_fila_notificacoes',
            'schedule': crontab(minute=10),  # :10 de cada hora — despacha fila de desconexão
        },
        'verificar-inconsistencias-dia-anterior': {
            'task': 'tasks.verificar_inconsistencias_dia_anterior',
            'schedule': crontab(minute='*'),  # verifica a cada minuto, self-limita pelo horário configurado
        },
        'sincronizar-feriados-anuais': {
            'task': 'tasks.sincronizar_feriados_anuais',
            'schedule': crontab(hour=6, minute=0),  # 06:00 diário; self-limita para 1º de Janeiro
        },
        # Avaliação 360°
        'avaliacao-verificar-disparo-daily': {
            'task': 'tasks.avaliacao_verificar_disparo',
            'schedule': crontab(hour=8, minute=30),  # 08:30 — verifica ciclos para disparar hoje
        },
        'avaliacao-lembretes-hourly': {
            'task': 'tasks.avaliacao_lembretes',
            'schedule': crontab(minute=20),  # :20 de cada hora — envia lembretes 24h/48h pendentes
        },
        'avaliacao-fechar-expirados-daily': {
            'task': 'tasks.avaliacao_fechar_expirados',
            'schedule': crontab(hour=0, minute=30),  # 00:30 — fecha ciclos com prazo vencido
        },
        'avaliacao-timeout-12h': {
            'task': 'tasks.avaliacao_timeout_12h',
            'schedule': crontab(minute=0),  # a cada hora — reseta estados AVALIACAO_* parados há 12h
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
        # Safe migrations for columns added after initial schema creation
        _run_safe_migrations(db)

    # ── Auto-sync de batidas (APScheduler – roda no mesmo processo) ───────────
    from services.auto_sync import init_scheduler
    init_scheduler(app)

    return app


app = create_app()
celery_app = app.extensions['celery']

if __name__ == '__main__':
    app.run(debug=True, port=5020)
