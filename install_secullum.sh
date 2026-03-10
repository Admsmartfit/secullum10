#!/bin/bash
# install_secullum.sh
# Script automatizado para instalação e deploy do sistema Secullum10.

echo "============================================="
echo " Instalador Secullum10 - Deploy para Linux "
echo "============================================="

# 1. Verifica se Docker e docker-compose estão instalados
if ! command -v docker &> /dev/null
then
    echo "[+] Docker não encontrado. Instalando Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    rm get-docker.sh
else
    echo "[✓] Docker já está instalado."
fi

if ! command -v docker-compose &> /dev/null
then
    echo "[+] docker-compose não encontrado. Obtendo última versão..."
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
else
    echo "[✓] docker-compose já está instalado."
fi

# 2. Configurações preliminares não são feitas aqui (agora é interativo)

# 3. Permissões de pastas
echo "[+] Configurando diretórios de uploads..."
mkdir -p instance
mkdir -p uploads/prontuario
chmod -R 777 instance
chmod -R 777 uploads

# 4. Compilação e Build
echo "============================================="
echo " Escolha o tipo de instalação:"
echo " 1) Instalação de Teste/Local (com Banco de Dados Interno do Docker)"
echo " 2) Instalação Definitiva (conectar a um Postgres Externo na sua rede)"
echo "============================================="
read -p "Digite 1 ou 2: " tipo_inst

if [ "$tipo_inst" == "1" ]; then
    echo "Configurando Banco de Dados Interno..."
    echo "SECRET_KEY=sua_chave_secreta_super_segura" > .env
    echo "FLASK_ENV=production" >> .env
    echo "REDIS_URL=redis://redis:6379/0" >> .env
    echo "DATABASE_URL=postgresql://secullum_user:secullum_pass@db:5432/secullum10" >> .env
    echo "============================================="
    echo " Subindo os contêineres com DB interno..."
    echo "============================================="
    sudo docker-compose -f docker-compose.yml -f docker-compose-db.yml up -d --build
else
    echo "Configurando Banco de Dados Externo..."
    echo "SECRET_KEY=sua_chave_secreta_super_segura" > .env
    echo "FLASK_ENV=production" >> .env
    echo "REDIS_URL=redis://redis:6379/0" >> .env
    read -p "Digite a sua DATABASE_URL (ex: postgresql://user:pass@192.168.0.10:5432/db): " user_db
    if [ -z "$user_db" ]; then
        user_db="postgresql://postgres:postgres@SEU_IP_AQUI:5432/secullum10"
    fi
    echo "DATABASE_URL=$user_db" >> .env
    echo "============================================="
    echo " Subindo os contêineres conectando no banco externo..."
    echo "============================================="
    sudo docker-compose up -d --build
fi

echo "============================================="
echo " [✓] Serviço instalado com SUCESSO! 😊"
echo " Aplicação rodando no Background (Linux) na porta 5020."
echo " - Acesse (via localhost se no servidor): http://localhost:5020"
echo " - Ou via IP: http://SEU_IP:5020"
echo ""
echo " Consulte o MANUAL_DE_INSTALACAO_LINUX.md para vincular no Cloudflare Tunnel!"
echo " Use desinstalar.sh caso precise remover tudo limpo."
echo "============================================="
