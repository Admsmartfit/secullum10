#!/bin/bash
# desinstalar.sh
# Módulo de desinstalação total do sistema Secullum10

echo "================================================="
echo " !!! AVISO DE DESINSTALAÇÃO TOTAL !!!"
echo "================================================="
echo "Este script irá apagar completamente o sistema:"
echo " 1. Parar e remover todos os contêineres Docker do app"
echo " 2. Apagar as imagens do Secullum10"
echo " 3. Apagar o banco de dados interno (volumes do Docker)"
echo " 4. Apagar arquivos de log e uploads locais (opcional)"
echo "================================================="
echo ""

read -p "Você tem CERTEZA que deseja prosseguir e apagar tudo? [s/N]: " confirma

if [[ "$confirma" == "s" || "$confirma" == "S" ]]; then
    DB_ARGS="-f docker-compose.yml"
    if grep -q "@db:5432" .env 2>/dev/null; then
        DB_ARGS="-f docker-compose.yml -f docker-compose-db.yml"
    fi

    echo "[+] Parando contêineres e apagando volumes..."
    # --rmi all remove imagens, -v remove os volumes atrelados
    sudo docker-compose $DB_ARGS down -v --rmi all
    
    echo "[+] Apagando diretórios temporários e de configuração local..."
    rm -rf instance/ uploads/ .env
    
    echo ""
    echo "[✓] Desinstalação concluída com sucesso. O sistema Secullum10 foi removido."
else
    echo "Operação cancelada. Nenhuma alteração foi feita."
fi
