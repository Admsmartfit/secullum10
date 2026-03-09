"""
Migração: adiciona coluna 'categoria' à tabela notification_rules.

Execute com:
    python migration_notificacao_categoria.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from extensions import db

app = create_app()

# Mapeamento padrão: condition_type → categoria
_DEFAULTS = {
    'ESCALA_ENVIO':         'geral',
    'ABSENCE':              'bot',
    'LATE_ENTRY':           'alerta',
    'EARLY_LEAVE':          'alerta',
    'OVERTIME':             'alerta',
    'INTERJORNADA':         'alerta',
    'DESCANSO_DOMINGO_F':   'alerta',
    'INCONSISTENCY_REPORT': 'fechamento',
}

with app.app_context():
    conn = db.engine.connect()
    trans = conn.begin()
    try:
        conn.execute(db.text("""
            ALTER TABLE notification_rules
            ADD COLUMN IF NOT EXISTS categoria VARCHAR(30) DEFAULT 'alerta'
        """))
        # Preenche categoria existente com base no condition_type
        for cond, cat in _DEFAULTS.items():
            conn.execute(db.text(
                "UPDATE notification_rules SET categoria = :cat "
                "WHERE condition_type = :cond AND (categoria IS NULL OR categoria = 'alerta')"
            ), {'cat': cat, 'cond': cond})
        trans.commit()
        print("Migração concluída: coluna 'categoria' adicionada a notification_rules.")
    except Exception as e:
        trans.rollback()
        print(f"Erro: {e}")
        raise
    finally:
        conn.close()
