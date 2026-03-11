"""
Migração: adiciona campos de empresa à tabela unidades_lideres.
Execute: python migration_empresa_por_departamento.py
"""
from app import create_app
from extensions import db

app = create_app()
with app.app_context():
    cols = [
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS empresa_nome      VARCHAR(300)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS empresa_cnpj      VARCHAR(30)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS empresa_socio     VARCHAR(300)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS socio_cpf         VARCHAR(20)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS empresa_endereco  VARCHAR(400)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS empresa_cidade    VARCHAR(200)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS empresa_uf        VARCHAR(5)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS empresa_cep       VARCHAR(15)",
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS experiencia_dias  INTEGER DEFAULT 45",
    ]
    with db.engine.connect() as conn:
        for sql in cols:
            conn.execute(db.text(sql))
        conn.commit()
    print("✓ Migração empresa_por_departamento concluída.")
