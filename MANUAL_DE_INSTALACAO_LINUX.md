# Manual de Instalação e Deploy (Linux + Cloudflare Tunnel)

Este manual cobre a instalação do **Secullum10** no seu servidor Linux local utilizando o **Docker** configurado para rodar na porta `5020`, e seu posterior roteamento através do Cloudflare Tunnel.

## Pré-requisitos
- Servidor Linux com Ubuntu/Debian (ou similar).
- **Docker** e **Docker Compose** instalados no servidor.
- Serviço `cloudflared` configurado (se já possui um app rodando na porta `5010`, isso provavelmente já está configurado no painel da Cloudflare).

---

## 1. Instalando o Docker e o Secullum10 (Modo Automático via Script)

Para facilitar, utilize o script `install_secullum.sh`. Este script possui módulos de **escolha inteligente** e ajuda a configurar automaticamente os bancos de dados.

```bash
# 1. Entre no servidor via SSH ou terminal local
# 2. Entre na pasta do projeto:
cd /caminho/para/secullum10

# 3. Dê permissão de execução no script
chmod +x install_secullum.sh

# 4. Execute a instalação (pode requerer sudo)
sudo ./install_secullum.sh
```

**Durante a instalação, você será questionado qual tipo deseja:**
1. **Instalação de Teste / Local (Com banco de dados embarcado):** O próprio instalador sobe um contêiner PostgreSQL exclusivo para a aplicação. Escolha essa opção caso queira uma instalação definitiva rápida e sem depender de banco de dados externo da infraestrutura.
2. **Instalação Definitiva Externa:** A aplicação não utilizará um banco via Docker. Você deve fornecer as credenciais e o IP de um banco de dados PostgreSQL existente na sua rede local. (NÃO USE LOCALHOST, pois se refere ao próprio docker).

O script inicializa a aplicação (Web + Trabalhador Celery + Redis + DB, se aplicável).

## 1.1 Desinstalação Total do Sistema

Caso tenha tentado instalar e queira recomeçar, criamos um módulo de *desinstalação total*. Ele remove os contêineres e limpa os bancos de dados criados:
```bash
chmod +x desinstalar.sh
sudo ./desinstalar.sh
```

## 2. Instalação Manual (Passo a Passo)

Se preferir não usar o script:

1. Acesse seu projeto via terminal no Linux:
   ```bash
   cd /caminho/para/secullum10
   ```
2. Crie ou configure um arquivo `.env` referenciando seu banco de dados, configurando senhas etc:
   ```bash
   cp .env.example .env
   ```
   *(Abra o .env usando o comando `nano .env` se precisar alterar a conexão do banco ou variáveis)*

3. Levante todos os serviços utilizando Docker Compose:
   - Para modo com seu próprio banco de dados:
     ```bash
     docker-compose up -d --build
     ```
   - Para modo com o banco de dados interno ativado:
     ```bash
     docker-compose -f docker-compose.yml -f docker-compose-db.yml up -d --build
     ```

## 3. Configurando o Tunnelamento do Cloudflare (Para Porta 5020)

Uma vez que o contêiner Docker esteja rodando com sucesso no Linux local, a aplicação responderá pela porta **5020**.

### Caso utilize o painel Cloudflare Zero Trust (Recomendado)
A forma mais fácil (já que você informou que tem o túnel `101f11c8...cfargotunnel.com` estabelecido):
1. Entre no Painel do **Cloudflare Zero Trust** (https://one.dash.cloudflare.com/).
2. Vá em **Networks > Tunnels** (Redes > Túneis) e selecione o seu túnel existente (o que cobre o `ricardo.home.nom.br`).
3. Clique em **Configure** (Configurar), depois vá na aba **Public Hostname** (Nomes de host públicos).
4. Clique em **Add a public hostname** (Adicionar nome de host público).
5. Preencha as configurações:
   - **Subdomain:** `secullum` (formando `secullum.ricardo.home.nom.br`)
   - **Domain:** `ricardo.home.nom.br`
   - **Type:** `HTTP`
   - **URL:** `localhost:5020` (ou o IP interno do servidor, como `192.168.0.100:5020`)
6. Salve a configuração ("Save hostname").

**Pronto!** A aplicação agora estará acessível online em [https://secullum.ricardo.home.nom.br](https://secullum.ricardo.home.nom.br).

### Caso utilize um arquivo local `config.yml` para o cloudflared
Se você configurou o túnel no seu servidor manualmente pelo arquivo `config.yml` (normalmente em `/etc/cloudflared/config.yml` ou `~/.cloudflared/config.yml`), adicione uma nova regra antes da rota "catch-all":

```yaml
tunnel: 101f11c8-d843-456a-8c9f-4936efcfe076
credentials-file: /etc/cloudflared/101f11c8-d843-456a-8c9f-4936efcfe076.json

ingress:
  # Nova rota para a porta 5020
  - hostname: secullum.ricardo.home.nom.br
    service: http://localhost:5020

  # Rota existente para a porta 5010
  - hostname: sistema-antigo.ricardo.home.nom.br  # (exemplo)
    service: http://localhost:5010
    
  - service: http_status:404
```

Reinicie o serviço cloudflared em seguida:
```bash
sudo systemctl restart cloudflared
```

---

## Dúvidas frequentes e troubleshooting

- **O banco de dados:** Caso deseje criar um banco PostgreSQL containerizado apenas para este app, basta descomentar a seção de `db: ...` no arquivo `docker-compose.yml`, além das variáveis `DATABASE_URL`.
- **Analisar Logs:**
  Para ver o que está acontecendo no log da web, digite:
  ```bash
  docker-compose logs -f web
  ```
  Para ver o funcionamento das rotinas de Celery (Trabalhador):
  ```bash
  docker-compose logs -f celery_worker
  ```
