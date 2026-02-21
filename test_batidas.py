from secullum_api import SecullumAPI
import os
from dotenv import load_dotenv
from datetime import date, timedelta
import json

load_dotenv()

def test_batidas():
    email = os.getenv('SECULLUM_EMAIL')
    senha = os.getenv('SECULLUM_PASSWORD')
    banco = os.getenv('SECULLUM_BANCO')

    api = SecullumAPI(email, senha, banco)
    print("Autenticando...")

    if api.autenticar():
        print("Autenticação bem-sucedida!")

        # Buscar batidas dos últimos 7 dias
        data_fim = date.today()
        data_inicio = data_fim - timedelta(days=7)

        print(f"\nBuscando batidas de {data_inicio} a {data_fim}...")
        batidas = api.buscar_batidas(
            data_inicio.strftime('%Y-%m-%d'),
            data_fim.strftime('%Y-%m-%d')
        )

        print(f"\nEncontradas {len(batidas) if batidas else 0} batidas.")

        if batidas:
            print("\n=== PRIMEIRA BATIDA ===")
            primeira = batidas[0]
            print(json.dumps(primeira, indent=2, ensure_ascii=False))

            print("\n=== CHAVES DISPONÍVEIS ===")
            for key in sorted(primeira.keys()):
                print(f"  - {key}: {type(primeira.get(key)).__name__}")
        else:
            print("\nNenhuma batida retornada pela API.")
    else:
        print("Falha na autenticação.")

if __name__ == "__main__":
    test_batidas()
