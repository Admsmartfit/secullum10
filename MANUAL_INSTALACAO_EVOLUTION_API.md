# Manual de Instalação: Evolution API (Linux + Docker + Cloudflare Tunnel)

Este manual cobre a instalação da **Evolution API v2.x** no mesmo servidor Linux onde o **Secullum10** está sendo executado. Para **evitar conflito de portas** com o Secullum10 ou outros serviços na porta `8080`, a Evolution API é configurada na porta **`8085`** e conta com seus próprios serviços de banco de dados e cache (PostgreSQL e Redis) containerizados para evitar dependências manuais.

---

## 🏗️ Visão Geral da Arquitetura no Servidor

| Aplicação | Porta Interna | Banco de Dados / Cache | URL / Tunnel Externa |
| :--- | :--- | :--- | :--- |
| **Secullum10** | `5020` | PostgreSQL (`5432`) / Redis (`6379`) | `https://ponto.ricardo.home.nom.br` |
| **Evolution API** | **`8085`** | Postgres Embarcado / Redis Embarcado | `https://evolution.ricardo.home.nom.br` |

---

## 📋 Passo 1: Clonar o Repositório e Limpar Compose Padrão

Acesse o servidor Linux via terminal (SSH) e prepare a pasta da aplicação:

```bash
# 1. Entre no diretório de aplicações
cd /opt

# 2. Clone o repositório oficial da Evolution API
sudo git clone https://github.com/evolution-foundation/evolution-api.git evolution-api

# 3. Entre no diretório e defina as permissões necessárias
cd evolution-api
sudo chown -R $USER:$USER /opt/evolution-api

# 4. REMOVER arquivos compostos padrões para evitar conflito de nomenclatura (.yml x .yaml)
rm -f docker-compose.yml docker-compose.yaml
```

---

## ⚙️ Passo 2: Configurar o Arquivo `.env` da Evolution API

Crie o arquivo `.env` dentro da pasta `/opt/evolution-api`:

```bash
cp .env.example .env
nano .env
```

Configure as variáveis principais (**note a URI do banco apontando para `evolution_postgres`**):

```env
# ── SERVER CONFIG ─────────────────────────────────────────────────────────────
SERVER_TYPE=http
SERVER_PORT=8085
SERVER_URL=https://evolution.ricardo.home.nom.br

# ── API KEY DE SEGURANÇA ──────────────────────────────────────────────────────
AUTHENTICATION_TYPE=apikey
AUTHENTICATION_API_KEY=SuaChaveSecretaSUPERForte123!
AUTHENTICATION_EXPOSE_IN_FETCH_INSTANCES=true

# ── DATABASE (PostgreSQL Embarcado) ───────────────────────────────────────────
DATABASE_ENABLED=true
DATABASE_PROVIDER=postgresql
DATABASE_CONNECTION_URI=postgresql://postgres:postgres@evolution_postgres:5432/evolution_db?schema=public
DATABASE_CLIENT_NAME=evolution_v2

# ── REDIS CACHE (Redis Embarcado) ─────────────────────────────────────────────
CACHE_REDIS_ENABLED=true
CACHE_REDIS_URI=redis://evolution_redis:6379/0
CACHE_REDIS_PREFIX_KEY=evolution
CACHE_REDIS_SAVE_INSTANCES=true

# ── WEBHOOKS ──────────────────────────────────────────────────────────────────
WEBHOOK_GLOBAL_ENABLED=false
WEBHOOK_GLOBAL_URL=
```

---

## 🐳 Passo 3: Configurar o `docker-compose.yml` (Com Banco Embarcado)

Crie o arquivo `docker-compose.yml` na pasta `/opt/evolution-api`:

```bash
nano docker-compose.yml
```

Cole o conteúdo completo abaixo:

```yaml
version: '3.8'

services:
  evolution-api:
    build: .
    container_name: evolution_api
    restart: always
    ports:
      - "8085:8085"
    env_file:
      - .env
    environment:
      - SERVER_PORT=8085
      - DATABASE_ENABLED=true
      - DATABASE_PROVIDER=postgresql
      - DATABASE_CONNECTION_URI=postgresql://postgres:postgres@evolution_postgres:5432/evolution_db?schema=public
      - CACHE_REDIS_ENABLED=true
      - CACHE_REDIS_URI=redis://evolution_redis:6379/0
    depends_on:
      - evolution_postgres
      - evolution_redis
    volumes:
      - evolution_instances:/evolution/instances

  evolution_postgres:
    image: postgres:15-alpine
    container_name: evolution_postgres
    restart: always
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: evolution_db
    volumes:
      - postgres_data:/var/lib/postgresql/data

  evolution_redis:
    image: redis:7-alpine
    container_name: evolution_redis
    restart: always
    volumes:
      - redis_data:/data

volumes:
  evolution_instances:
  postgres_data:
  redis_data:
```

> *(Salve no nano com `Ctrl + O`, `Enter` e saia com `Ctrl + X`)*

---

## 🚀 Passo 4: Subir a Evolution API

Suba os contêineres Docker (API + PostgreSQL + Redis):

```bash
# Iniciar os contêineres e compilar em segundo plano
sudo docker compose up -d --build

# Verificar se todos os 3 contêineres estão rodando (evolution_api, evolution_postgres, evolution_redis)
sudo docker compose ps

# Acompanhar os logs de inicialização da API
sudo docker compose logs -f evolution-api
```

### Teste de Acesso Local
Verifique a resposta da API localmente na porta `8085`:
```bash
curl http://localhost:8085
```

---

## 🌐 Passo 5: Configurar Roteamento no Cloudflare Tunnel

Para expor a Evolution API publicamente via HTTPS:

1. Abra a configuração do `cloudflared`:
   ```bash
   sudo nano /etc/cloudflared/config.yml
   ```

2. Adicione o hostname da Evolution API direcionando para a porta **`8085`** **antes** de `http_status:404`:

   ```yaml
   tunnel: 101f11c8-d843-456a-8c9f-4936efcfe076
   credentials-file: /home/ricardo/.cloudflared/101f11c8-d843-456a-8c9f-4936efcfe076.json

   ingress:
     # Nova rota para o Secullum
     - hostname: ponto.ricardo.home.nom.br
       service: http://localhost:5020

     # Sua rota antiga
     - hostname: ricardo.home.nom.br
       service: http://localhost:5010

     # Evolution API (WhatsApp)
     - hostname: evolution.ricardo.home.nom.br
       service: http://localhost:8085

     # Fallback 404
     - service: http_status:404
   ```

3. No painel de gerenciamento DNS na Cloudflare, crie a entrada CNAME:
   * **Host:** `evolution`
   * **Target:** `101f11c8-d843-456a-8c9f-4936efcfe076.cfargotunnel.com`

4. Reinicie o serviço do Cloudflare Tunnel:
   ```bash
   sudo systemctl restart cloudflared
   ```

---

## 🔗 Passo 6: Integração no Secullum10

Atualize o arquivo `.env` da aplicação Secullum10 com o endereço da Evolution API.

**Não use `http://localhost:8085` direto** — o Secullum10 roda dentro do container
`secullum10_web` (stack Docker separado da Evolution API), que tem sua própria rede isolada;
"localhost" ali aponta para o próprio container, não para o host físico, então a Evolution API
fica inacessível ("Connection refused") mesmo os dois estando na mesma máquina.

Como os dois ficam no mesmo servidor Linux e a Evolution só precisa ser alcançada
internamente (não por HTTPS/Cloudflare), a solução é o `docker-compose.yml` do Secullum10
mapear um hostname especial para o host físico. Adicione em cada serviço que acessa WhatsApp
(`web`, `celery_worker`, `celery_beat`):

```yaml
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

E no `.env` do Secullum10:

```env
EVOLUTION_HOST=http://host.docker.internal:8085
EVOLUTION_API_KEY=SuaChaveSecretaSUPERForte123!
EVOLUTION_INSTANCE=secullum10
```

(Alternativa, se preferir expor a Evolution publicamente via Cloudflare Tunnel — Passo 5 — e
não se importar com o tráfego saindo e voltando pela internet: `EVOLUTION_HOST=https://evolution.ricardo.home.nom.br`.)

---

## 🛠️ Comandos de Manutenção

* **Ver Logs:** `sudo docker compose logs -f evolution-api`
* **Reiniciar Serviço:** `sudo docker compose restart`
* **Atualizar Versão:**
  ```bash
  git pull
  sudo docker compose pull
  sudo docker compose up -d --build
  ```
