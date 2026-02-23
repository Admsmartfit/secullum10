"""Migration: color em turnos, compliance_warning em alocacoes_diarias, solicitacoes_troca."""
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
        import models  # noqa – registra todos os models
        with db.engine.connect() as conn:
            stmts = [
                # Phase 1: cor no turno
                "ALTER TABLE turnos ADD COLUMN IF NOT EXISTS color VARCHAR(7) DEFAULT '#4f46e5'",
                # Phase 2: compliance_warning na alocação
                "ALTER TABLE alocacoes_diarias ADD COLUMN IF NOT EXISTS compliance_warning TEXT",
                # Phase 3: tabela de trocas
                """CREATE TABLE IF NOT EXISTS solicitacoes_troca (
                    id SERIAL PRIMARY KEY,
                    solicitante_id VARCHAR(50) REFERENCES funcionarios(id),
                    alocacao_origem_id INTEGER REFERENCES alocacoes_diarias(id),
                    candidato_id VARCHAR(50) REFERENCES funcionarios(id),
                    alocacao_destino_id INTEGER REFERENCES alocacoes_diarias(id),
                    status VARCHAR(30) NOT NULL DEFAULT 'PENDENTE',
                    obs_solicitante TEXT,
                    obs_gestor TEXT,
                    criado_em TIMESTAMP DEFAULT NOW(),
                    atualizado_em TIMESTAMP DEFAULT NOW()
                )""",
            ]
            for stmt in stmts:
                try:
                    conn.execute(text(stmt))
                    conn.commit()
                    print(f"OK: {stmt[:60]}…")
                except Exception as e:
                    print(f"SKIP/ERR: {e}")

if __name__ == '__main__':
    run()
