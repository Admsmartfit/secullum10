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

# 2. Configuracoes preliminares do arquivo .env
if [ ! -f ".env" ]; then
    echo "[+] Arquivo .env nao encontrado. Criando um modelo basico."
    echo "SECRET_KEY=sua_chave_secreta_super_segura" > .env
    echo "FLASK_ENV=production" >> .env
    echo "REDIS_URL=redis://redis:6379/0" >> .env
    echo "DATABASE_URL=postgresql://postgres:postgres@SEU_IP_AQUI:5432/secullum10" >> .env
    echo "" >> .env
    
    echo "⚠️  ATENCAO: Foi gerado um arquivo .env basico."
    echo "============================================================"
    echo " SE O SEU BANCO DE DADOS POSTGRES ESTA NESTE SERVIDOR LINUX:"
    echo " Mude 'SEU_IP_AQUI' no arquivo .env para o IP real do servidor "
    echo " (ex: 192.168.0.100). NUNCA USE 'localhost' AQUI DENTRO DO DOCKER!"
    echo "============================================================"
fi

# 3. Permissoes de pastas locais (Uploads e Banco local caso use SQLite)
echo "[+] Configurando diretorios necessarios..."
mkdir -p instance
mkdir -p uploads/prontuario
chmod -R 777 instance
chmod -R 777 uploads

# 4. Compilando as Imagens Docker (Build)
echo "================================================="
echo " Construindo as imagens Docker (Aguarde...)      "
echo "================================================="
sudo docker-compose build

echo "================================================="
echo " [✓] Instalacao e Preparacao concluidas!         "
echo " O sistema AINDA NAO ESTA RODANDO.               "
echo " Para ligar o sistema, use o arquivo executar.sh "
echo "================================================="
