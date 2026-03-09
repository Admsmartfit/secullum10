"""
Migração: renomeia os níveis de acesso de usuários.
  gestor    → administrador
  professor → funcionario

Execute com:
    python migration_nivel_acesso.py
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
            UPDATE usuarios SET nivel_acesso = 'administrador' WHERE nivel_acesso = 'gestor'
        """))
        conn.execute(db.text("""
            UPDATE usuarios SET nivel_acesso = 'funcionario'   WHERE nivel_acesso = 'professor'
        """))
        trans.commit()

        from models import Usuario
        total = db.session.query(Usuario).count()
        adm   = db.session.query(Usuario).filter_by(nivel_acesso='administrador').count()
        ger   = db.session.query(Usuario).filter_by(nivel_acesso='gerente').count()
        fun   = db.session.query(Usuario).filter_by(nivel_acesso='funcionario').count()
        print(f"Migração concluída. Total: {total}  |  administrador: {adm}  |  gerente: {ger}  |  funcionario: {fun}")
    except Exception as e:
        trans.rollback()
        print(f"Erro na migração: {e}")
        raise
    finally:
        conn.close()
