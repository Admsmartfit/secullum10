"""
Migração: cria tabelas do chatbot interativo WhatsApp.
  - chat_states      : estado de conversa por funcionário
  - bot_keyword_rules: respostas por palavra-chave (CRUD via painel)

Execute com:
    python migration_chat_states.py
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
            CREATE TABLE IF NOT EXISTS chat_states (
                id              SERIAL PRIMARY KEY,
                funcionario_id  VARCHAR(50) NOT NULL UNIQUE REFERENCES funcionarios(id) ON DELETE CASCADE,
                estado          VARCHAR(50) NOT NULL DEFAULT 'IDLE',
                contexto        TEXT,
                atualizado_em   TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(db.text("""
            CREATE INDEX IF NOT EXISTS idx_chat_states_func
            ON chat_states (funcionario_id)
        """))
        conn.execute(db.text("""
            CREATE TABLE IF NOT EXISTS bot_keyword_rules (
                id                  SERIAL PRIMARY KEY,
                keyword             VARCHAR(100) NOT NULL,
                resposta            TEXT NOT NULL,
                apenas_funcionario  BOOLEAN DEFAULT TRUE,
                ativo               BOOLEAN DEFAULT TRUE,
                criado_em           TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(db.text("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_kw_rule_keyword
            ON bot_keyword_rules (UPPER(keyword))
        """))
        trans.commit()
        print("Migração concluída: chat_states e bot_keyword_rules criadas.")
    except Exception as e:
        trans.rollback()
        print(f"Erro: {e}")
        raise
    finally:
        conn.close()
