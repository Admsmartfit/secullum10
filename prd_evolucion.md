PRD: Secullum10 Enterprise - Evolution v2.0
1. Visão Geral
Transformar o sistema atual em uma plataforma de gestão de RH moderna, minimalista e proativa. O foco sai de apenas "visualizar dados" para "gerir exceções e comunicação", garantindo compliance CLT e automatizando a comunicação via WhatsApp.

📅 Fase 1: Correções Críticas & Estabilidade (Imediato)
Objetivo: Garantir que o básico funcione perfeitamente antes de embelezar ou adicionar complexidade.

1.1. Correção do Bug: Espelho de Ponto Individual
Problema: A rota /espelho?funcionario_id=259 carrega dados, mas exibe todos os funcionários.

Causa Provável: No arquivo blueprints/espelho.py (ou app.py), a query ao banco de dados não está aplicando o filtro .filter_by(funcionario_id=...) ou WHERE quando o parâmetro GET é recebido.

Solução Técnica:

Capturar o request.args.get('funcionario_id').

Se existir, filtrar a query SQL/SQLAlchemy de Batidas e Calculos.

Garantir que o template batidas.html ou espelho.html receba apenas o objeto do funcionário filtrado, não a lista completa.

1.2. Refatoração de Base
Organização: Garantir que todas as rotas estejam usando o padrão de Blueprints (já iniciado, mas precisa verificar se app.py ainda tem lógica solta).

Banco de Dados: Confirmar a migração total para PostgreSQL (usando migrate_sqlite_to_pg.py) para suportar as queries complexas do motor de regras.

🎨 Fase 2: Redesign UI/UX (Moderno & Minimalista)
Objetivo: Limpar a interface, reduzir o ruído visual e facilitar a navegação.

2.1. Novo Design System
Estilo: Migrar para um layout "Clean Dashboard" (Fundo cinza muito claro #f8f9fa, Cards brancos com sombras suaves, Tipografia Sans-serif moderna como Inter ou Roboto).

Menu Lateral: Substituir o menu superior por uma Sidebar retrátil escura ou branca minimalista, liberando espaço vertical.

Paleta de Cores:

Primária: Azul Índigo (Ação).

Alerta: Laranja Suave (Atrasos).

Erro: Vermelho Suave (Faltas/CLT).

Sucesso: Verde Esmeralda (Compliance).

2.2. Melhorias Específicas de UX
Filtros Inteligentes: Em todas as listas (Funcionários, Escalas), substituir dropdowns nativos por componentes de busca com autocomplete (ex: Select2 ou similar).

Dashboards: Remover tabelas gigantes da tela inicial. Substituir por "Widgets de Resumo" (Ex: "3 Funcionários Atrasados Hoje", "5 Conflitos de Escala").

⚖️ Fase 3: Módulo de Escalas Avançado (Visual & Compliance)
Objetivo: Tornar a gestão de escalas visual e à prova de multas trabalhistas.

3.1. Interface de Calendário (Visual)
Visualização: Implementar biblioteca de calendário (ex: FullCalendar).

Filtros de View:

Visão Mensal (Grid clássico).

Visão Semanal (Detalhada por hora).

Filtros Laterais: Checkbox por Cargo, Departamento ou Empresa.

Edição: Drag & Drop para mover um funcionário de um turno para outro. Clique no dia para abrir modal de edição rápida.

3.2. Motor de Validação CLT (O "Guardião")
Funcionamento: Ao tentar salvar uma escala, o backend (services/motor_clt.py) deve validar:

Interjornada: Alerta se intervalo entre fim do turno D e início do turno D+1 for < 11h.

Intrajornada: Alerta se turno > 6h não tiver intervalo de 1h (ou conforme regra).

Carga Semanal: Somar horas planejadas na semana (Seg-Dom). Se > 44h, exibir alerta vermelho crítico.

DSR: Verificar se existe pelo menos 1 folga em 7 dias (preferencialmente domingo).

Feedback Visual: Turnos problemáticos ficam com borda vermelha e ícone de alerta no calendário.

3.3. Integração na Tela de Funcionários
Aba "Escala Atual": Em /funcionarios/<id>, adicionar uma aba ou card que mostra: "Turno de Hoje: 08:00 - 17:00" e "Próxima Folga: Sábado".

🤖 Fase 4: Motor de Regras de WhatsApp (Automação)
Objetivo: Criar um sistema flexível de "Gatilho -> Condição -> Ação".

4.1. Construtor de Regras (Interface)
Criar uma nova tela Configurações > Regras de Notificação com um formulário lógico:

Gatilho (Quando analisar?):

Tempo: Diário (ex: 08:00), Semanal (ex: Sexta 14:00).

Evento: Ao sincronizar batida, Ao detectar ausência.

Condições (O que procurar?):

Atraso: Batida realizada > X minutos após início da escala.

Antecipação: Batida realizada > X minutos antes do início.

Falta: Sem batida após X minutos do início.

Hora Extra: Saída > X minutos após fim da escala.

Compliance: Violação de Interjornada detectada.

Destinatário (Quem recebe?):

O próprio Funcionário.

O Gerente do Departamento (precisa ter vínculo no cadastro).

Grupo de RH.

Janela de Envio (Restrição de Horário):

Checkbox: "Enviar apenas durante expediente do funcionário?" (Sim/Não).

Checkbox: "Enviar imediatamente (24h)?" (Para alertas críticos ao gestor).

4.2. Regras de Envio de Escala
Configuração específica para envio de PDF/Texto da escala:

Frequência: Mensal (dia 25), Semanal (Sexta-feira), ou 3 Dias Antes.

Formato: Resumo texto ("Sua escala: Seg 8-17, Ter 8-17...") ou PDF anexo.

🛠️ Detalhamento Técnico das Tarefas (Backlog)
Sprint 1: Fixes & Setup
Fix: Alterar query em blueprints/espelho.py para suportar filtro por ID.

DB: Validar integridade do banco PostgreSQL com as novas tabelas de regras.

Frontend: Instalar novo template base (Jinja2 + CSS framework novo).

Sprint 2: Escalas Visual
Frontend: Integrar FullCalendar na rota /escalas.

API: Criar endpoint JSON que retorna eventos de escala formatados para o calendário.

Backend: Implementar lógica de verificação de 44h semanais e Interjornada no save da escala.

Sprint 3: Motor de Notificação (Backend)
Model: Criar tabela NotificationRules (tipo, threshold_minutos, target_audience, schedule_config).

Service: Criar NotificationProcessor que roda via Cron/Celery.

Logica:

Buscar regras ativas.

Comparar Batidas (Real) vs Alocacoes (Escala).

Gerar fila de mensagens.

Verificar "Janela de Envio" (Se for fora do expediente e a regra proibir, agendar para o próximo início de turno).

Sprint 4: Frontend de Regras e Finalização
UI: Criar formulário de criação de regras de WhatsApp.

UI: Atualizar tela de detalhes do funcionário com dados da escala.

Testes: Simular cenários de atraso e verificar geração de mensagem.

Exemplo de Estrutura de Regra (JSON no Banco de Dados)
JSON
{
  "rule_name": "Alerta de Atraso Crítico",
  "trigger_type": "EVENT_SYNC",
  "conditions": {
    "type": "LATE_ENTRY",
    "threshold_minutes": 15
  },
  "actions": [
    {
      "channel": "WHATSAPP",
      "recipient": "MANAGER",
      "template": "O funcionário {name} está atrasado há {minutes} minutos."
    },
    {
      "channel": "WHATSAPP",
      "recipient": "EMPLOYEE",
      "template": "Identificamos um atraso no seu ponto. Por favor, justifique."
    }
  ],
  "constraints": {
    "only_working_hours": true
  }
}