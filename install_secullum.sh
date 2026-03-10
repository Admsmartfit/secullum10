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

# 4. Configurar .env
if [ -f ".env" ]; then
    echo "============================================="
    echo " Arquivo .env encontrado!"
    echo " 1) Manter o .env existente (recomendado se só está atualizando)"
    echo " 2) Reconfigurar tudo (cria novo .env)"
    echo "============================================="
    read -p "Digite 1 ou 2: " opcao_env
else
    opcao_env="2"
fi

if [ "$opcao_env" == "2" ]; then
    echo "============================================="
    echo " Configurando credenciais..."
    echo "============================================="

    # Secullum API
    read -p "E-mail de acesso ao Secullum: " secullum_email
    read -p "Senha do Secullum: " secullum_password
    read -p "ID do banco Secullum (secullumidbancoselecionado): " secullum_banco

    # WhatsApp / MegaAPI
    echo ""
    echo "--- WhatsApp / MegaAPI (deixe em branco para configurar depois) ---"
    read -p "MEGAAPI_HOST (ex: apistart01.megaapi.com.br): " mega_host
    read -p "MEGAAPI_INSTANCE: " mega_instance
    read -p "MEGAAPI_TOKEN: " mega_token
    read -p "MEGAAPI_SECRET: " mega_secret
    read -p "Celular do gestor (ex: 5527988010899): " gestor_cel

    # Banco de dados
    echo ""
    echo "============================================="
    echo " Escolha o tipo de instalação:"
    echo " 1) Banco de Dados Interno do Docker"
    echo " 2) Banco de Dados Externo (Postgres na rede)"
    echo "============================================="
    read -p "Digite 1 ou 2: " tipo_inst

    if [ "$tipo_inst" == "1" ]; then
        db_url="postgresql://secullum_user:secullum_pass@db:5432/secullum10"
    else
        read -p "Digite a sua DATABASE_URL (ex: postgresql://user:pass@192.168.0.10:5432/db): " db_url
        if [ -z "$db_url" ]; then
            db_url="postgresql://postgres:postgres@SEU_IP_AQUI:5432/secullum10"
        fi
    fi

    cat > .env <<EOF
SECRET_KEY=sua_chave_secreta_super_segura
FLASK_ENV=production
REDIS_URL=redis://redis:6379/0
DATABASE_URL=$db_url

# API Secullum
SECULLUM_EMAIL=$secullum_email
SECULLUM_PASSWORD=$secullum_password
SECULLUM_BANCO=$secullum_banco

# WhatsApp / MegaAPI
MEGAAPI_HOST=${mega_host:-apistart01.megaapi.com.br}
MEGAAPI_INSTANCE=$mega_instance
MEGAAPI_TOKEN=$mega_token
MEGAAPI_SECRET=$mega_secret
GESTOR_CELULAR=$gestor_cel

# Flask-Mail (opcional)
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=
MAIL_PASSWORD=
MAIL_DEFAULT_SENDER=
RH_EMAIL=

# Whisper (opcional)
OPENAI_API_KEY=
EOF
    echo "[✓] Arquivo .env criado."
else
    echo "[✓] Mantendo credenciais do .env existente."
    echo ""
    echo "============================================="
    echo " Qual banco de dados usar no Docker?"
    echo " 1) Banco de Dados Interno do Docker (recomendado)"
    echo " 2) Banco de Dados Externo (Postgres na rede)"
    echo "============================================="
    read -p "Digite 1 ou 2: " tipo_inst

    # Ajusta valores que mudam entre desenvolvimento e Docker
    # FLASK_ENV
    sed -i 's/^FLASK_ENV=.*/FLASK_ENV=production/' .env
    # REDIS_URL — sempre aponta para o container redis
    if grep -q "^REDIS_URL=" .env; then
        sed -i 's|^REDIS_URL=.*|REDIS_URL=redis://redis:6379/0|' .env
    else
        echo "REDIS_URL=redis://redis:6379/0" >> .env
    fi
    # DATABASE_URL
    if [ "$tipo_inst" == "1" ]; then
        if grep -q "^DATABASE_URL=" .env; then
            sed -i 's|^DATABASE_URL=.*|DATABASE_URL=postgresql://secullum_user:secullum_pass@db:5432/secullum10|' .env
        else
            echo "DATABASE_URL=postgresql://secullum_user:secullum_pass@db:5432/secullum10" >> .env
        fi
    else
        current_db=$(grep "^DATABASE_URL=" .env | cut -d'=' -f2-)
        echo "  DATABASE_URL atual: $current_db"
        read -p "  Nova DATABASE_URL (Enter para manter): " new_db
        if [ -n "$new_db" ]; then
            sed -i "s|^DATABASE_URL=.*|DATABASE_URL=$new_db|" .env
        fi
    fi
    echo "[✓] .env ajustado para ambiente Docker."
fi

# 5. Compilação e Build
echo "============================================="
if [ "$tipo_inst" == "1" ]; then
    echo " Subindo os contêineres com DB interno..."
    echo "============================================="
    sudo docker-compose -f docker-compose.yml -f docker-compose-db.yml up -d --build
else
    echo " Subindo os contêineres conectando no banco externo..."
    echo "============================================="
    sudo docker-compose up -d --build
fi

# 5. Aguarda o banco de dados ficar pronto e cria usuário admin
echo "============================================="
echo " Aguardando banco de dados ficar pronto..."
echo "============================================="

CONTAINER_DB="secullum10_db"
for i in $(seq 1 30); do
    if sudo docker exec "$CONTAINER_DB" pg_isready -U secullum_user -d secullum10 -q 2>/dev/null; then
        echo "[✓] Banco de dados pronto."
        break
    fi
    echo "  ... aguardando ($i/30)"
    sleep 3
done

echo "[+] Aguardando container web iniciar (até 60s)..."
for i in $(seq 1 20); do
    STATUS=$(sudo docker inspect --format='{{.State.Status}}' secullum10_web 2>/dev/null)
    if [ "$STATUS" == "running" ]; then
        echo "[✓] Container web está rodando."
        break
    fi
    echo "  ... aguardando web ($i/20) - status: $STATUS"
    sleep 3
done
sleep 5  # aguarda gunicorn/Flask inicializar dentro do container

echo "[+] Criando usuário administrador padrão (admin@admin.com)..."
ADMIN_OUTPUT=$(sudo docker exec secullum10_web python -c "
from app import create_app
from extensions import db
from models import Usuario
app = create_app()
with app.app_context():
    u = Usuario.query.filter_by(email='admin@admin.com').first()
    if not u:
        u = Usuario(nome='Administrador', email='admin@admin.com', nivel_acesso='administrador')
        db.session.add(u)
    u.set_senha('admin123')
    u.nivel_acesso = 'administrador'
    db.session.commit()
    print('OK')
" 2>&1)

if echo "$ADMIN_OUTPUT" | grep -q "OK"; then
    echo "[✓] Usuário admin@admin.com criado/atualizado com senha admin123"
else
    echo "[!] AVISO: Falha ao criar usuário admin. Saída:"
    echo "$ADMIN_OUTPUT"
    echo ""
    echo "    Tente manualmente após a instalação:"
    echo "    sudo docker exec secullum10_web python create_admin.py"
fi

echo "============================================="
echo " [✓] Serviço instalado com SUCESSO! 😊"
echo " Aplicação rodando no Background (Linux) na porta 5020."
echo " - Acesse (via localhost se no servidor): http://localhost:5020"
echo " - Ou via IP: http://SEU_IP:5020"
echo ""
echo " Login padrão: admin@admin.com / admin123 (ALTERE APÓS O PRIMEIRO ACESSO!)
 Consulte o MANUAL_DE_INSTALACAO_LINUX.md para vincular no Cloudflare Tunnel!"
echo " Use desinstalar.sh caso precise remover tudo limpo."
echo "============================================="
