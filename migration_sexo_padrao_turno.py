"""
Migration: adiciona campo 'sexo' em funcionarios e cria tabela 'padroes_turno'.
Execute: python migration_sexo_padrao_turno.py
"""
from app import app
from extensions import db


def run():
    with app.app_context():
        conn = db.engine.connect()
        trans = conn.begin()
        try:
            # 1. Adiciona coluna sexo em funcionarios (se ainda não existir)
            conn.execute(db.text("""
                ALTER TABLE funcionarios
                ADD COLUMN IF NOT EXISTS sexo VARCHAR(1)
            """))

            # 2. Cria tabela padroes_turno
            conn.execute(db.text("""
                CREATE TABLE IF NOT EXISTS padroes_turno (
                    id            SERIAL PRIMARY KEY,
                    nome          VARCHAR(100) NOT NULL,
                    descricao     TEXT,
                    dias_trabalho INTEGER NOT NULL DEFAULT 5,
                    dias_folga    INTEGER NOT NULL DEFAULT 2,
                    turno_id      INTEGER REFERENCES turnos(id) ON DELETE SET NULL,
                    departamento  VARCHAR(200),
                    ativo         BOOLEAN NOT NULL DEFAULT TRUE,
                    criado_em     TIMESTAMP DEFAULT NOW()
                )
            """))

            trans.commit()
            print("Migration concluída com sucesso.")
        except Exception as e:
            trans.rollback()
            print(f"Erro na migration: {e}")
            raise
        finally:
            conn.close()


if __name__ == '__main__':
    run()
