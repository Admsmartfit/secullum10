
import os
import sys

# Garante que o diretório atual está no path para importar models e extensões
sys.path.append(os.getcwd())

from flask import Flask
from extensions import db
from config import Config

def apply_migration():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    
    with app.app_context():
        # Importante importar os modelos antes de qualquer operação no DB
        import models
        from sqlalchemy import text
        
        print("Adicionando novas colunas à tabela 'turnos'...")
        
        try:
            # Adiciona intervalo_minutos
            db.session.execute(text("ALTER TABLE turnos ADD COLUMN intervalo_minutos INTEGER DEFAULT 60;"))
            print("[OK] Coluna 'intervalo_minutos' adicionada.")
        except Exception as e:
            print(f"[AVISO] Erro ao adicionar 'intervalo_minutos' (provavelmente já existe): {e}")

        try:
            # Adiciona dias_complexos_json
            db.session.execute(text("ALTER TABLE turnos ADD COLUMN dias_complexos_json TEXT;"))
            print("[OK] Coluna 'dias_complexos_json' adicionada.")
        except Exception as e:
            print(f"[AVISO] Erro ao adicionar 'dias_complexos_json' (provavelmente já existe): {e}")

        db.session.commit()
        print("Migração concluída.")

if __name__ == '__main__':
    apply_migration()
