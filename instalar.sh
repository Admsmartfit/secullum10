#!/bin/bash
# instalar.sh
# Script para PREPARAR o ambiente Linux para o Secullum10 sem iniciar o sistema.

echo "================================================="
echo " Instalador Secullum10 - Preparacao do Servidor  "
echo "================================================="

# 1. Verifica se Docker e docker-compose estao instalados
if ! command -v docker &> /dev/null
then
    echo "[+] Docker nao encontrado. Instalando Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    rm get-docker.sh
else
    echo "[✓] Docker ja esta instalado."
fi

if ! command -v docker-compose &> /dev/null
then
    echo "[+] docker-compose nao encontrado. Obtendo ultima versao..."
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
else
    echo "[✓] docker-compose ja esta instalado."
fi

# 2. Configurações preliminares do arquivo .env não são feitas aqui (é interativo)

# 3. Permissões de pastas locais
echo "[+] Configurando diretórios necessários..."
mkdir -p instance
mkdir -p uploads/prontuario
chmod -R 777 instance
chmod -R 777 uploads

# 4. Modo de Banco de Dados
echo "================================================="
echo " Escolha o tipo de banco de dados para a preparação:"
echo " 1) Banco de Dados Interno (Local/Docker)"
echo " 2) Banco de Dados Externo (Você já possui um Postgres)"
echo "================================================="
read -p "Digite 1 ou 2: " tipo_inst

if [ "$tipo_inst" == "1" ]; then
    echo "Configurando Banco de Dados Interno para build..."
    echo "SECRET_KEY=sua_chave_secreta_super_segura" > .env
    echo "FLASK_ENV=production" >> .env
    echo "REDIS_URL=redis://redis:6379/0" >> .env
    echo "DATABASE_URL=postgresql://secullum_user:secullum_pass@db:5432/secullum10" >> .env
    echo "================================================="
    echo " Construindo as imagens Docker (Aguarde...)      "
    echo "================================================="
    sudo docker-compose -f docker-compose.yml -f docker-compose-db.yml build
else
    echo "Configurando Banco de Dados Externo para build..."
    echo "SECRET_KEY=sua_chave_secreta_super_segura" > .env
    echo "FLASK_ENV=production" >> .env
    echo "REDIS_URL=redis://redis:6379/0" >> .env
    read -p "Digite a sua DATABASE_URL (ex: postgresql://user:pass@192.168.0.10:5432/db): " user_db
    if [ -z "$user_db" ]; then
        user_db="postgresql://postgres:postgres@SEU_IP_AQUI:5432/secullum10"
    fi
    echo "DATABASE_URL=$user_db" >> .env
    echo "================================================="
    echo " Construindo as imagens Docker (Aguarde...)      "
    echo "================================================="
    sudo docker-compose build
fi

echo "================================================="
echo " [✓] Instalacao e Preparacao concluidas!         "
echo " O sistema AINDA NAO ESTA RODANDO.               "
echo " Para ligar o sistema, use o docker-compose up   "
echo "================================================="
