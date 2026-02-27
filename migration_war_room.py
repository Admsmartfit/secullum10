
from app import create_app
from extensions import db
from sqlalchemy import text

def apply_migration():
    app = create_app()
    with app.app_context():
        print("Iniciando migracao para o padrao War Room...")
        
        try:
            # 1. Adiciona horario_base_id em funcionarios
            print("Adicionando colunas em 'funcionarios'...")
            db.session.execute(text("ALTER TABLE funcionarios ADD COLUMN IF NOT EXISTS horario_base_id INTEGER REFERENCES turnos(id)"))
            
            # 2. Adiciona colunas em turnos
            print("Adicionando colunas em 'turnos'...")
            db.session.execute(text("ALTER TABLE turnos ADD COLUMN IF NOT EXISTS funcao VARCHAR(100)"))
            
            # 3. Adiciona is_excecao em alocacoes_diarias
            print("Adicionando coluna 'is_excecao' em 'alocacoes_diarias'...")
            db.session.execute(text("ALTER TABLE alocacoes_diarias ADD COLUMN IF NOT EXISTS is_excecao BOOLEAN DEFAULT TRUE"))
            db.session.execute(text("UPDATE alocacoes_diarias SET is_excecao = TRUE WHERE is_excecao IS NULL"))
            
            db.session.commit()
            print("[OK] Colunas adicionadas com sucesso!")
            
        except Exception as e:
            db.session.rollback()
            print(f"[ERRO] Falha ao aplicar migracao: {e}")
            print("Dica: Se o banco estiver vazio, voce tambem pode usar 'py migrate_db.py' para recriar tudo.")

if __name__ == "__main__":
    apply_migration()
