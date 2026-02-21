
import requests

urls = [
    "https://73365.pontoweb.com.br/api/v1/Acesso/Login",
    "https://pontoweb.secullum.com.br/73365/api/v1/Acesso/Login",
    "https://73365.pontoweb.com.br/api/Acesso/Login",
    "https://pontoweb.secullum.com.br/api/v1/Acesso/Login",
]

payload = {
    "Email": "ricardo.landeiro@smartfit.com",
    "Senha": "Spetra@100",
    "Cnpj": "73365"
}

for url in urls:
    print(f"Trying {url}...")
    try:
        resp = requests.post(url, json=payload, timeout=5)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            print(f"SUCCESS! {url}")
            print(resp.text)
            break
        else:
            print(f"Body: {resp.text[:100]}")
    except Exception as e:
        print(f"Error: {e}")
    print("-" * 20)
