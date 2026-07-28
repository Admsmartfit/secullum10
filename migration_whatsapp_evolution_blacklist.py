"""
Migração — Blacklist absoluta de opt-out (migração Mega-API → Evolution API).

  whatsapp_blacklist (nova):
    - números que recusaram opt-in ou pediram para parar (palavra-chave de
      opt-out). Checado em TODO envio real (services/whatsapp_bot.py::_bloqueado),
      sem exceção de cargo/regra.

Segue o mesmo padrão dos scripts anteriores desta área
(migration_whatsapp_fase0_fase1.py, migration_whatsapp_fase4_optin.py):
script standalone com ALTER/CREATE idempotente (IF NOT EXISTS), não uma
revisão Alembic — esta área do projeto nunca usou migrations/versions/.

Execute com:
    python migration_whatsapp_evolution_blacklist.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from extensions import db

app = create_app()

with app.app_context():
    conn = db.engine.connect()
    trans = conn.begin()
    try:
        conn.execute(db.text("""
            CREATE TABLE IF NOT EXISTS whatsapp_blacklist (
                id        SERIAL PRIMARY KEY,
                celular   VARCHAR(20) NOT NULL UNIQUE,
                motivo    VARCHAR(50) DEFAULT 'OPT_OUT',
                criado_em TIMESTAMP DEFAULT NOW()
            )
        """))
        print("  ✓ whatsapp_blacklist criada")

        conn.execute(db.text(
            "CREATE INDEX IF NOT EXISTS idx_whatsapp_blacklist_celular "
            "ON whatsapp_blacklist (celular)"
        ))
        print("  ✓ índice idx_whatsapp_blacklist_celular criado")

        trans.commit()
        print("\nMigração concluída.")
    except Exception as e:
        trans.rollback()
        print(f"Erro na migração: {e}")
        raise
    finally:
        conn.close()
