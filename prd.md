Com certeza. Como um dev que já passou por muitas integrações de sistemas legados e APIs de terceiros, entendo que o segredo aqui é unir a robustez da API da Secullum com a agilidade e simplicidade do Flask (baseado no `hrsystem-flask`).

Abaixo, apresento o **PRD (Product Requirements Document)** detalhado para o desenvolvimento dessa ferramenta.

---

# PRD: Painel de Monitoramento e Conciliação – Ponto Secullum Web

## 1. Visão Geral do Produto

O objetivo é criar uma interface web (Dashboard) que consuma dados em tempo real (ou via cache sincronizado) da API do **Ponto Secullum Web**. O sistema servirá como um braço direito para o RH, permitindo visualizar batidas de ponto, identificar inconsistências e facilitar o fechamento da folha sem precisar navegar nos menus complexos do sistema nativo.

## 2. Referências Técnicas e Arquitetura

Para este projeto, utilizaremos uma arquitetura **BFF (Backend For Frontend)** em Python.

* **Referência de API (Secullum):** Utilizaremos a lógica de autenticação e endpoints mapeados no repositório [PontoWebIntegracaoExternaExemplo](https://github.com/Secullum/PontoWebIntegracaoExternaExemplo.git).
* **Referência de UI/UX (RH System):** O esqueleto de rotas, gestão de usuários e templates HTML/Bootstrap virá do [hrsystem-flask](https://github.com/nivedrn/hrsystem-flask.git).

### Stack Tecnológica

* **Backend:** Python 3.10+ / Flask.
* **Frontend:** HTML5, Jinja2, Bootstrap 5 (padrão do `hrsystem-flask`).
* **Banco de Dados:** SQLite (para cache local de funcionários e configurações).
* **Integração:** Biblioteca `requests` para consumo da API REST da Secullum.

---

## 3. Requisitos Funcionais (RF)

| ID | Requisito | Descrição |
| --- | --- | --- |
| **RF01** | **Autenticação Secullum** | O sistema deve permitir configurar as credenciais da API (Email/Senha/Token) e realizar o login via endpoint `/Acesso/Login`. |
| **RF02** | **Sincronização de Funcionários** | Importar a lista de funcionários ativos para o banco local para evitar chamadas excessivas à API. |
| **RF03** | **Visualização de Batidas** | Uma tela onde o usuário seleciona um período e o sistema lista todas as batidas formatadas. |
| **RF04** | **Filtros de Dashboard** | Filtrar batidas por departamento, funcionário ou data. |
| **RF05** | **Exportação Simples** | Botão para gerar um relatório em PDF ou Excel das batidas visualizadas na tela. |

---

## 4. Requisitos Não Funcionais (RNF)

* **Segurança:** As credenciais da API da Secullum não devem ficar expostas no código (uso de `.env`).
* **Performance:** Implementar um sistema de *cache* de 15 minutos para as batidas de ponto, evitando atingir o rate-limit da API.
* **Responsividade:** O painel deve ser acessível via tablet e desktop (herança do Bootstrap do `hrsystem-flask`).

---

## 5. Mapeamento de Fluxo de Dados

A integração seguirá o fluxo abaixo:

1. **Handshake:** O Flask envia `POST /api/v1/Acesso/Login`. Recebe o `Token`.
2. **Identificação:** O Flask busca `GET /api/v1/Funcionarios`.
3. **Coleta:** O Flask busca `GET /api/v1/Batidas?dataInicio=X&dataFim=Y`.
4. **Processamento:** O Python agrupa as batidas por `PIS` ou `CPF` para exibir no HTML de forma legível (Entrada 1, Saída 1, Entrada 2, Saída 2).

---

## 6. Caminhos de Implementação (Roadmap)

### Fase 1: Setup e Conexão (O "Coração" do PontoWeb)

Baseado no exemplo da Secullum, você deve criar um módulo `secullum_api.py`:

* Implementar a classe `SecullumClient`.
* Método para renovar o Token automaticamente quando expirar.

### Fase 2: Adaptação do `hrsystem-flask`

* Remover as tabelas de funcionários originais do `hrsystem-flask` e substituí-las pela estrutura da Secullum.
* Criar uma rota `/dashboard/ponto` que renderize o template de batidas.

### Fase 3: Frontend (HTML)

* Utilizar os cards do Bootstrap para mostrar métricas rápidas: "Batidas hoje", "Funcionários Ausentes", "Horas Extras Estimadas".

---

## 7. Exemplo de Estrutura de Código Sugerida

```python
# No seu app.py (Baseado no hrsystem-flask)
from flask import Flask, render_template
import requests

app = Flask(__name__)

@app.route('/espelho-ponto/<funcionario_id>')
def espelho_ponto(funcionario_id):
    # Lógica baseada no PontoWebIntegracaoExternaExemplo
    api_token = "OBTER_VIA_LOGIN"
    headers = {'Authorization': f'Bearer {api_token}'}
    
    # Busca batidas reais na API Secullum
    response = requests.get(f"https://pontowebintegracaoexterna.secullum.com.br/api/v1/Batidas?FuncionarioId={funcionario_id}", headers=headers)
    batidas = response.json()
    
    return render_template('ponto_detalhe.html', batidas=batidas)

```

> **Nota de Atenção:**  usuario:ricardo.landeiro@smartfit.com Senha : Spetra@100  Banco: 73365 