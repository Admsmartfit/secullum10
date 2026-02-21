from secullum_api import SecullumAPI
import os
from dotenv import load_dotenv

load_dotenv()

def test_connection():
    email = os.getenv('SECULLUM_EMAIL') or "ricardo.landeiro@smartfit.com"
    senha = os.getenv('SECULLUM_PASSWORD') or "Spetra@100"
    banco = os.getenv('SECULLUM_BANCO') or "73365" 
    
    api = SecullumAPI(email, senha, banco)
    print(f"Tentando autenticar para {email}...")
    if api.autenticar():
        print("Autenticação bem-sucedida!")
        print("Buscando funcionários...")
        funcionarios = api.listar_funcionarios()
        print(f"Encontrados {len(funcionarios)} funcionários.")
        if funcionarios:
            print("\nCHAVES:")
            f = funcionarios[0]
            for k in sorted(f.keys()):
                print(k)
    else:
        print("Falha na autenticação.")

if __name__ == "__main__":
    test_connection()
