"""
Migration v2: Extensão do modelo Feriado para suporte multi-nível.
Adiciona: tipo, uf, cidade_ibge, fonte, ativo, criado_por_id
Remove: unique constraint em data (mesma data pode ser nacional + municipal)
Adiciona: cidade_ibge em unidades_lideres

Execute: python migration_feriados_v2.py
"""
from app import create_app
from extensions import db

app = create_app()
with app.app_context():
    sqls = [
        # Novos campos em feriados
        "ALTER TABLE feriados ADD COLUMN IF NOT EXISTS tipo         VARCHAR(20) NOT NULL DEFAULT 'personalizado'",
        "ALTER TABLE feriados ADD COLUMN IF NOT EXISTS uf           VARCHAR(2)",
        "ALTER TABLE feriados ADD COLUMN IF NOT EXISTS cidade_ibge  VARCHAR(10)",
        "ALTER TABLE feriados ADD COLUMN IF NOT EXISTS fonte        VARCHAR(50)",
        "ALTER TABLE feriados ADD COLUMN IF NOT EXISTS ativo        BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE feriados ADD COLUMN IF NOT EXISTS criado_por_id INTEGER",
        # Remove unique constraint na coluna data (pode haver feriado nacional e municipal no mesmo dia)
        "ALTER TABLE feriados DROP CONSTRAINT IF EXISTS feriados_data_key",
        # Índices parciais de unicidade por tipo
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_feriado_nacional  ON feriados (data)             WHERE tipo = 'nacional'",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_feriado_estadual  ON feriados (data, uf)         WHERE tipo = 'estadual'",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_feriado_municipal ON feriados (data, cidade_ibge) WHERE tipo = 'municipal'",
        # IBGE em unidades_lideres
        "ALTER TABLE unidades_lideres ADD COLUMN IF NOT EXISTS cidade_ibge VARCHAR(10)",
    ]
    with db.engine.connect() as conn:
        for sql in sqls:
            try:
                conn.execute(db.text(sql))
                print(f"  ✓ {sql[:70]}...")
            except Exception as e:
                print(f"  ! ERRO: {e}")
        conn.commit()
    print("✓ migration_feriados_v2 concluída.")
