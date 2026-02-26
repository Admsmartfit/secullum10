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

# 2. Configurações preliminares
if [ ! -f ".env" ]; then
    echo "[+] Arquivo .env não encontrado. Copiando do ambiente atual."
    # Se ele estiver trazendo tudo no zip, env já estará lá.
    if [ -f "config.py" ]; then
        echo "SECRET_KEY=sua_chave_secreta_super_segura" >> .env
        echo "FLASK_ENV=production" >> .env
        echo "REDIS_URL=redis://redis:6379/0" >> .env
        echo "DATABASE_URL=postgresql://secullum_user:secullum_pass@localhost:5432/secullum10" >> .env
        echo "" >> .env
        echo "⚠️  ATENÇÃO: Foi gerado um arquivo .env basico."
        echo "Por favor, configure as chaves como banco de dados e senhas no arquivo .env posteriormente."
    fi
fi

# 3. Permissões de pastas
echo "[+] Configurando diretórios de uploads..."
mkdir -p instance
mkdir -p uploads/prontuario
chmod -R 777 instance
chmod -R 777 uploads

# 4. Compilação e Build
echo "============================================="
echo " Subindo as instâncias Docker do sistema..."
echo "============================================="

sudo docker-compose up -d --build

echo "============================================="
echo " [✓] Serviço instalado com SUCESSO! 😊"
echo " Aplicação rodando no Background (Linux) na porta 5020."
echo " - Acesse (via localhost): http://localhost:5020"
echo ""
echo " Consulte o MANUAL_DE_INSTALACAO_LINUX.md para vincular no Cloudflare Tunnel!"
echo "============================================="
