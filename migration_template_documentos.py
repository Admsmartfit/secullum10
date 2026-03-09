"""
Migração: cria tabelas template_documentos e envios_documento
e adiciona coluna estado_civil na tabela funcionarios.

Execute com:
    python migration_template_documentos.py
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
            ALTER TABLE funcionarios
            ADD COLUMN IF NOT EXISTS estado_civil VARCHAR(30)
        """))

        conn.execute(db.text("""
            CREATE TABLE IF NOT EXISTS template_documentos (
                id          SERIAL PRIMARY KEY,
                nome        VARCHAR(200) NOT NULL,
                descricao   TEXT,
                arquivo_nome VARCHAR(300) NOT NULL,
                ativo       BOOLEAN DEFAULT TRUE,
                criado_em   TIMESTAMP DEFAULT NOW()
            )
        """))

        conn.execute(db.text("""
            CREATE TABLE IF NOT EXISTS envios_documento (
                id                  SERIAL PRIMARY KEY,
                funcionario_id      VARCHAR(50) NOT NULL REFERENCES funcionarios(id),
                email_destinatario  VARCHAR(200) NOT NULL,
                templates_enviados  TEXT,
                enviado_por_id      INTEGER REFERENCES usuarios(id),
                criado_em           TIMESTAMP DEFAULT NOW()
            )
        """))

        conn.execute(db.text("""
            CREATE TABLE IF NOT EXISTS tabela_salarial (
                id                   SERIAL PRIMARY KEY,
                funcao               VARCHAR(200) NOT NULL UNIQUE,
                salario              NUMERIC(10,2),
                auxilio_alimentacao  NUMERIC(10,2),
                premiacao            NUMERIC(10,2),
                atualizado_em        TIMESTAMP DEFAULT NOW()
            )
        """))

        # Adiciona colunas caso a tabela já exista de versão anterior
        conn.execute(db.text("ALTER TABLE tabela_salarial ALTER COLUMN salario DROP NOT NULL"))
        conn.execute(db.text("ALTER TABLE tabela_salarial ADD COLUMN IF NOT EXISTS auxilio_alimentacao NUMERIC(10,2)"))
        conn.execute(db.text("ALTER TABLE tabela_salarial ADD COLUMN IF NOT EXISTS premiacao NUMERIC(10,2)"))

        trans.commit()
        print("Migração concluída com sucesso.")
    except Exception as e:
        trans.rollback()
        print(f"Erro na migração: {e}")
        raise
    finally:
        conn.close()
