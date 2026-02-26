#!/bin/bash
# executar.sh
# Script para EXECUTAR o Secullum10 de forma flexível.

echo "================================================="
echo " Execucao do Secullum10                          "
echo "================================================="
echo "O sistema foi projetado para rodar em modo Docker."
echo "Como voce gostaria de executar o aplicativo agora?"
echo ""
echo "  [1] MODO DE TESTE (Logs visiveis na tela, encerra ao fechar o terminal)"
echo "  [2] MODO PERMANENTE (Roda em segundo plano, sempre online, reinicia automaticamente)"
echo "  [3] DESLIGAR (Para completamente o sistema)"
echo ""
read -p "Escolha uma opcao (1, 2 ou 3): " opcao

case $opcao in
  1)
    echo ""
    echo "[+] Iniciando em MODO DE TESTE..."
    echo "================================================="
    echo " DICA: Verifique se nao vai aparecer erro de     "
    echo " 'Connection refused' ou Banco de dados.         "
    echo " Aperte CTRL+C a qualquer momento para parar.    "
    echo "================================================="
    echo ""
    sudo docker-compose up
    ;;
  2)
    echo ""
    echo "[+] Iniciando em MODO PERMANENTE (Segundo Plano)..."
    sudo docker-compose up -d
    echo ""
    echo "[✓] Sistema esta online e rodando em segundo plano!"
    echo "Ele vai religar sozinho mesmo se o servidor Linux reiniciar."
    echo ""
    echo "-> Para ver os erros no modo permanente, digite:"
    echo "   sudo docker logs secullum10_web"
    echo "-> Para desligar depois, rode este script e escolha a opcao 3."
    ;;
  3)
    echo ""
    echo "[-] Parando todos os servicos Secullum10..."
    sudo docker-compose down
    echo "[✓] Sistema desligado com sucesso."
    ;;
  *)
    echo "Opcao invalida. Saindo."
    ;;
esac
