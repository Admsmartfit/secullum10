
import requests

urls = [
    "https://pontowebintegracaoexterna.secullum.com.br/swagger/index.html",
    "https://pontowebintegracaoexterna.secullum.com.br/api/v1/swagger.json",
    "https://pontowebintegracaoexterna.secullum.com.br/api/swagger.json",
    "https://pontowebintegracaoexterna.secullum.com.br/help",
]

for url in urls:
    print(f"Trying {url}...")
    try:
        resp = requests.get(url, timeout=5)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            print(f"SUCCESS! {url}")
            print(resp.text[:200])
    except Exception as e:
        print(f"Error: {e}")
