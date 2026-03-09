"""
Migração: adiciona colunas 'tipo_regra' e 'data_referencia' às tabelas
whatsapp_log e notificacao_fila.

Execute com:
    python migration_whatsapp_log_v2.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from extensions import db

app = create_app()

_ALTERACOES = [
    # (tabela, coluna, tipo_sql)
    ('whatsapp_log',     'tipo_regra',      'VARCHAR(50)'),
    ('whatsapp_log',     'data_referencia', 'DATE'),
    ('notificacao_fila', 'tipo_regra',      'VARCHAR(50)'),
    ('notificacao_fila', 'data_referencia', 'DATE'),
]

with app.app_context():
    conn = db.engine.connect()
    trans = conn.begin()
    try:
        for tabela, coluna, tipo in _ALTERACOES:
            try:
                conn.execute(db.text(
                    f"ALTER TABLE {tabela} ADD COLUMN IF NOT EXISTS {coluna} {tipo}"
                ))
                print(f"  ✓ {tabela}.{coluna} ({tipo})")
            except Exception as e:
                # Coluna já existe ou tabela não existe — continua
                print(f"  ⚠ {tabela}.{coluna}: {e}")
        trans.commit()
        print("\nMigração concluída.")
    except Exception as e:
        trans.rollback()
        print(f"Erro: {e}")
        raise
    finally:
        conn.close()
