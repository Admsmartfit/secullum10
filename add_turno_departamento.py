"""Adds departamento column to turnos table."""
import os, sys
sys.path.append(os.getcwd())

from flask import Flask
from extensions import db
from config import Config
from sqlalchemy import text

def run():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    with app.app_context():
        import models  # noqa
        with db.engine.connect() as conn:
            try:
                conn.execute(text(
                    "ALTER TABLE turnos ADD COLUMN IF NOT EXISTS departamento VARCHAR(200)"
                ))
                conn.commit()
                print("OK: coluna 'departamento' adicionada em turnos.")
            except Exception as e:
                print(f"Erro: {e}")

if __name__ == '__main__':
    run()
