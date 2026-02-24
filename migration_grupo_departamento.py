"""
Migration: cria tabela grupos_departamento e insere o grupo inicial "Praia do Canto".
Execute: python migration_grupo_departamento.py
"""
import json
from app import app
from extensions import db


def run():
    with app.app_context():
        conn = db.engine.connect()
        trans = conn.begin()
        try:
            conn.execute(db.text("""
                CREATE TABLE IF NOT EXISTS grupos_departamento (
                    id                 SERIAL PRIMARY KEY,
                    nome               VARCHAR(200) NOT NULL UNIQUE,
                    departamentos_json TEXT NOT NULL DEFAULT '[]',
                    criado_em          TIMESTAMP DEFAULT NOW()
                )
            """))

            # Verifica se o grupo "Praia do Canto" já existe
            exists = conn.execute(db.text(
                "SELECT id FROM grupos_departamento WHERE nome = 'Praia do Canto'"
            )).fetchone()

            if not exists:
                depts = json.dumps(
                    ['PRAIA FITNESS', 'FUNCIONAL DA PRAIA'],
                    ensure_ascii=False
                )
                conn.execute(db.text(
                    "INSERT INTO grupos_departamento (nome, departamentos_json) VALUES (:n, :d)"
                ), {'n': 'Praia do Canto', 'd': depts})
                print("Grupo 'Praia do Canto' criado com ['PRAIA FITNESS', 'FUNCIONAL DA PRAIA'].")
            else:
                print("Grupo 'Praia do Canto' já existe.")

            trans.commit()
            print("Migration concluída com sucesso.")
        except Exception as e:
            trans.rollback()
            print(f"Erro: {e}")
            raise
        finally:
            conn.close()


if __name__ == '__main__':
    run()
