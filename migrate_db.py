"""
Script de migração do banco de dados.
ATENÇÃO: Este script irá recriar todas as tabelas. Use com cuidado!
"""
import os
from app import app, db
from models import Funcionario, Batida, Configuracao

def migrate():
    with app.app_context():
        print("Iniciando migração do banco de dados...")

        # Backup do banco existente (se existir)
        db_path = 'instance/secullum.db'
        if os.path.exists(db_path):
            import shutil
            from datetime import datetime
            backup_name = f'instance/secullum_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
            shutil.copy2(db_path, backup_name)
            print(f"✓ Backup criado: {backup_name}")

        # Remover todas as tabelas
        print("Removendo tabelas antigas...")
        db.drop_all()

        # Criar novas tabelas com a estrutura atualizada
        print("Criando novas tabelas...")
        db.create_all()

        print("✓ Migração concluída com sucesso!")
        print("\nPróximos passos:")
        print("1. Execute a sincronização de funcionários: /sync")
        print("2. Execute a sincronização de batidas: /sync-batidas")

if __name__ == '__main__':
    response = input("ATENÇÃO: Este script irá recriar o banco de dados. Continuar? (sim/não): ")
    if response.lower() in ['sim', 's', 'yes', 'y']:
        migrate()
    else:
        print("Migração cancelada.")
