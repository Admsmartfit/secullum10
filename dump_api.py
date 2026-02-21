from secullum_api import SecullumAPI
import os
import json
from dotenv import load_dotenv

load_dotenv()

def dump_keys():
    email = os.getenv('SECULLUM_EMAIL') or "ricardo.landeiro@smartfit.com"
    senha = os.getenv('SECULLUM_PASSWORD') or "Spetra@100"
    banco = os.getenv('SECULLUM_BANCO') or "73365" 
    
    api = SecullumAPI(email, senha, banco)
    if api.autenticar():
        funcionarios = api.listar_funcionarios()
        if funcionarios:
            # Pegar as chaves de todos e ver quais variam
            all_keys = set()
            for f in funcionarios:
                all_keys.update(f.keys())
            
            # Tentar encontrar um que seja diferente
            # Talvez ordenar por nome e pegar uns que o usuário saiba que são demitidos?
            # Por agora, vamos apenas salvar as chaves num JSON
            with open('api_keys.json', 'w') as f:
                json.dump(sorted(list(all_keys)), f)
            
            # Salvar um exemplo completo num JSON
            with open('api_example.json', 'w') as f:
                json.dump(funcionarios[0], f, indent=4)
                
            print(f"Dumped {len(all_keys)} keys to api_keys.json and example to api_example.json")
    else:
        print("Auth failed")

if __name__ == "__main__":
    dump_keys()
