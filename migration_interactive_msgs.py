"""
Migração: adiciona colunas de mensagem interativa em bot_keyword_rules e notification_rules.

  bot_keyword_rules:
    - tipo_msg        VARCHAR(20) DEFAULT 'texto'
    - interativo_json TEXT

  notification_rules:
    - template_employee_tipo        VARCHAR(20) DEFAULT 'texto'
    - template_employee_interativo  TEXT
    - template_manager_tipo         VARCHAR(20) DEFAULT 'texto'
    - template_manager_interativo   TEXT

Execute com:
    python migration_interactive_msgs.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from extensions import db

app = create_app()

with app.app_context():
    conn = db.engine.connect()
    trans = conn.begin()
    try:
        conn.execute(db.text("""
            ALTER TABLE bot_keyword_rules
                ADD COLUMN IF NOT EXISTS tipo_msg        VARCHAR(20) DEFAULT 'texto',
                ADD COLUMN IF NOT EXISTS interativo_json TEXT
        """))
        conn.execute(db.text("""
            ALTER TABLE notification_rules
                ADD COLUMN IF NOT EXISTS template_employee_tipo        VARCHAR(20) DEFAULT 'texto',
                ADD COLUMN IF NOT EXISTS template_employee_interativo  TEXT,
                ADD COLUMN IF NOT EXISTS template_manager_tipo         VARCHAR(20) DEFAULT 'texto',
                ADD COLUMN IF NOT EXISTS template_manager_interativo   TEXT
        """))
        trans.commit()
        print("Migração concluída: colunas interativas adicionadas.")
    except Exception as e:
        trans.rollback()
        print(f"Erro: {e}")
        raise
    finally:
        conn.close()
