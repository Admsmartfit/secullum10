"""
Migração: Adiciona tipo_regra e data_referencia à tabela whatsapp_logs (PRD 2.0).

Execute com:
    python migration_whatsapp_log_prd.py
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
        print("Adicionando colunas tipo_regra e data_referencia à tabela whatsapp_logs...")
        
        # Adiciona tipo_regra
        conn.execute(db.text("""
            ALTER TABLE whatsapp_logs
            ADD COLUMN IF NOT EXISTS tipo_regra VARCHAR(50);
        """))
        
        # Adiciona data_referencia
        conn.execute(db.text("""
            ALTER TABLE whatsapp_logs
            ADD COLUMN IF NOT EXISTS data_referencia DATE;
        """))
        
        # Adiciona colunas em notificacao_fila
        conn.execute(db.text("""
            ALTER TABLE notificacao_fila
            ADD COLUMN IF NOT EXISTS tipo_regra VARCHAR(50),
            ADD COLUMN IF NOT EXISTS data_referencia DATE;
        """))
        
        # Cria índice para busca rápida (idempotência)
        conn.execute(db.text("""
            CREATE INDEX IF NOT EXISTS idx_whatsapp_logs_idemp
            ON whatsapp_logs (funcionario_id, tipo_regra, data_referencia);
        """))
        
        trans.commit()
        print("Migração concluída com sucesso.")
    except Exception as e:
        trans.rollback()
        print(f"Erro na migração: {e}")
        raise
    finally:
        conn.close()
