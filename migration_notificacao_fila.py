"""
Migração: cria tabela notificacao_fila para o sistema de Direito à Desconexão.

Execute com:
    python migration_notificacao_fila.py
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
            CREATE TABLE IF NOT EXISTS notificacao_fila (
                id          SERIAL PRIMARY KEY,
                regra_id    INTEGER REFERENCES notification_rules(id) ON DELETE SET NULL,
                funcionario_id VARCHAR(50) REFERENCES funcionarios(id) ON DELETE SET NULL,
                celular     VARCHAR(20) NOT NULL,
                mensagem    TEXT NOT NULL,
                tipo        VARCHAR(50),
                enviar_apos TIMESTAMP,
                status      VARCHAR(20) DEFAULT 'pendente',
                tentativas  INTEGER DEFAULT 0,
                criada_em   TIMESTAMP DEFAULT NOW(),
                enviado_em  TIMESTAMP
            )
        """))
        conn.execute(db.text("""
            CREATE INDEX IF NOT EXISTS idx_fila_status_hora
            ON notificacao_fila (status, enviar_apos)
        """))
        trans.commit()
        print("Migração concluída: tabela notificacao_fila criada.")
    except Exception as e:
        trans.rollback()
        print(f"Erro na migração: {e}")
        raise
    finally:
        conn.close()
