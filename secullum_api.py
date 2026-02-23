import requests

class SecullumAPI:
    def __init__(self, email, senha, banco):
        self.auth_url = "https://autenticador.secullum.com.br"
        self.base_url = "https://pontowebintegracaoexterna.secullum.com.br/IntegracaoExterna"
        self.email = email
        self.senha = senha
        self.banco = banco
        self.token = None

    def autenticar(self):
        """Realiza o login via Autenticador Secullum e armazena o token."""
        url = f"{self.auth_url}/Token"
        payload = {
            "grant_type": "password",
            "username": self.email,
            "password": self.senha,
            "client_id": 3
        }
        
        try:
            # Importante: deve ser x-www-form-urlencoded
            response = requests.post(url, data=payload)
            
            if response.status_code == 200:
                self.token = response.json().get('access_token')
                return True
            else:
                print(f"Erro Autenticação: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"Erro na requisição de autenticação: {e}")
        return False

    def _get_headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "secullumidbancoselecionado": self.banco,
            "Content-Type": "application/json; charset=utf-8",
            "Accept-Language": "pt-BR"
        }

    def listar_funcionarios(self, limite=None):
        """Retorna a lista de funcionários ativos, com opção de limite."""
        if not self.token: 
            if not self.autenticar(): return []
            
        url = f"{self.base_url}/Funcionarios"
        params = {}
        if limite:
            params['$top'] = limite # Padrão OData comum no Secullum
            
        response = requests.get(url, headers=self._get_headers(), params=params)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Erro ao listar funcionários: {response.status_code} - {response.text}")
            return []

    def listar_horarios(self):
        """Retorna todos os horários cadastrados em /Horarios (com Dias, Entradas, Saidas)."""
        if not self.token:
            if not self.autenticar():
                return []
        try:
            r = requests.get(f"{self.base_url}/Horarios", headers=self._get_headers(), timeout=30)
            if r.status_code == 200:
                return r.json()
            print(f"Erro ao listar horários: {r.status_code} - {r.text[:200]}")
        except Exception as e:
            print(f"Erro listar_horarios: {e}")
        return []

    def buscar_batidas(self, data_inicio, data_fim, hora_inicio=None, hora_fim=None):
        """
        Busca todas as batidas no período. 
        data_inicio/data_fim: YYYY-MM-DD
        hora_inicio/hora_fim: HH:mm (opcional)
        """
        if not self.token:
            if not self.autenticar(): return []
            
        url = f"{self.base_url}/Batidas"
        params = {
            "dataInicio": data_inicio,
            "dataFim": data_fim
        }
        if hora_inicio:
            params["horaInicio"] = hora_inicio
        if hora_fim:
            params["horaFim"] = hora_fim
            
        response = requests.get(url, headers=self._get_headers(), params=params)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Erro ao buscar batidas: {response.status_code} - {response.text}")
            return []
