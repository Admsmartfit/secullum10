"""
Migration: adiciona campo 'tipo_turno' em turnos.
Execute: python migration_tipo_turno.py
"""
from app import app
from extensions import db


def run():
    with app.app_context():
        conn = db.engine.connect()
        trans = conn.begin()
        try:
            conn.execute(db.text("""
                ALTER TABLE turnos
                ADD COLUMN IF NOT EXISTS tipo_turno VARCHAR(1)
            """))
            trans.commit()
            print("Migration concluída: coluna tipo_turno adicionada em turnos.")
        except Exception as e:
            trans.rollback()
            print(f"Erro na migration: {e}")
            raise
        finally:
            conn.close()


if __name__ == '__main__':
    run()
