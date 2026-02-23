
import os
import sys
import json

# Garante que o diretório atual está no path
sys.path.append(os.getcwd())

from flask import Flask
from extensions import db
from config import Config
from models import Turno

def remove_weekends():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    
    with app.app_context():
        print("Iniciando remoção de Sábado (5) e Domingo (6) de todos os turnos...")
        
        turnos = Turno.query.all()
        count = 0
        
        for t in turnos:
            # 1. Atualiza dias_semana (string "0,1,2,3,4,5,6")
            dias = t.dias_semana_list
            novos_dias = [d for d in dias if d < 5] # Mantém apenas 0-4 (Seg-Sex)
            t.dias_semana = ','.join(map(str, novos_dias))
            
            # 2. Atualiza dias_complexos_json (JSON dict com chaves "5" e "6")
            if t.dias_complexos_json:
                try:
                    complexos = json.loads(t.dias_complexos_json)
                    # Remove chaves de sábado e domingo
                    complexos.pop("5", None)
                    complexos.pop("6", None)
                    t.dias_complexos_json = json.dumps(complexos)
                except Exception as e:
                    print(f"Erro ao processar JSON do turno {t.id}: {e}")
            
            count += 1
            print(f"[OK] Turno '{t.nome}' atualizado.")

        db.session.commit()
        print(f"\nSucesso: {count} turnos atualizados (Sáb/Dom removidos).")

if __name__ == '__main__':
    remove_weekends()
