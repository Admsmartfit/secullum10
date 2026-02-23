Com base na análise do código atual do seu sistema (que utiliza Python/Flask, SQLAlchemy e integração com a API da Secullum) e nos requisitos solicitados, elaborei este **Documento de Requisitos de Produto (PRD)**.

Este documento foi desenhado para ser tecnicamente viável dentro da sua arquitetura atual, aproveitando modelos já existentes como `Funcionario`, `Turno` e `EscalaTrabalho`.

---

# Documento de Requisitos de Produto (PRD)

**Projeto:** Gestão Visual de Escalas e Compliance (Secullum10 Evolution)
**Versão:** 1.0
**Status:** Rascunho / Aprovação

## 1. Visão Geral

Implementar uma interface visual interativa para o gerenciamento de escalas de trabalho, substituindo a entrada manual de dados por uma interface de "Arrastar e Soltar". O sistema incluirá validações automáticas de leis trabalhistas (CLT/Compliance) e um portal de autoatendimento para colaboradores, sincronizando as alterações finais com o Ponto Secullum Web.

## 2. Público-Alvo

1. **Gestores de RH/Departamento Pessoal:** Responsáveis pelo planejamento das escalas.
2. **Gerentes de Loja/Unidade:** Responsáveis pela operação diária.
3. **Colaboradores:** Usuários finais que consultarão escalas e solicitarão trocas.

---

## 3. Requisitos Funcionais

### 3.1. Interface de Arrastar e Soltar (Drag-and-Drop)

**Objetivo:** Permitir a alocação de turnos de forma visual.

* **Descrição:** Implementar uma "Visão de Recursos" (Resource View) no calendário.
* **Linhas (Y-Axis):** Lista de Funcionários (agrupados por Departamento/Loja).
* **Colunas (X-Axis):** Dias do mês ou horas do dia.
* **Elementos:** Os "Turnos" serão blocos arrastáveis listados em uma barra lateral.


* **Comportamento:**
* O gestor arrasta um bloco de "Turno" (ex: 08:00 - 17:00) da barra lateral para a célula correspondente ao Funcionário/Dia.
* O gestor pode arrastar um turno já alocado de um dia para outro ou de um funcionário para outro.
* **Backend:** Ao soltar (evento `drop`), o sistema deve disparar uma requisição AJAX para atualizar a tabela `escala_trabalho`.



### 3.2. Codificação por Cores (Color Coding)

**Objetivo:** Identificação visual rápida de tipos de turno e status.

* **Descrição:**
* Utilizar o campo `color` já existente no modelo `Turno` (`models.py`) para renderizar o fundo do bloco do evento.
* **Legenda Visual:**
* **Turnos:** Manhã (Verde), Tarde (Azul), Noite (Roxo), Madrugada (Laranja).
* **Folgas:** Cinza ou Hachurado.
* **Status de Aprovação:** Adicionar uma borda ou ícone ao evento (Borda Pontilhada = Pendente, Borda Sólida = Confirmado).
* **Alertas:** Ícone vermelho pulsante no canto do evento em caso de violação de regra.





### 3.3. Visão de Linha do Tempo (Gantt Style)

**Objetivo:** Visualizar a cobertura da equipe ao longo das horas do dia.

* **Descrição:** Uma visão alternativa ao calendário mensal, focada no dia ou semana.
* O eixo X representa as 24 horas do dia.
* As barras mostram o início e fim exato do trabalho.
* Visualização clara de intervalos (buracos) onde não há ninguém escalado para um departamento específico.


* **Requisito Técnico:** Utilizar biblioteca frontend compatível (ex: FullCalendar Scheduler ou biblioteca Gantt JS).

### 3.4. Alertas de Compliance e Regras Automáticas

**Objetivo:** "Camada de Inteligência" para prevenir passivo trabalhista.

* **Mecanismo:** Antes de salvar qualquer alteração no banco de dados (no evento `onEventDrop` ou `onEventResize`), o backend deve validar as regras.
* **Regras Obrigatórias (MVP):**
1. **Interjornada:** Verificar se há menos de 11 horas entre o fim do turno do dia anterior e o início do novo turno.
2. **Folga Semanal (DSR):** Alertar se o funcionário trabalhar mais de 6 dias consecutivos sem folga.
3. **Duplicidade:** Impedir dois turnos no mesmo dia para o mesmo funcionário (exceto se configurado como extra).
4. **Conflito de Férias/Afastamento:** Verificar na tabela de `Afastamentos` (integrada via API Secullum) se o funcionário está disponível.


* **Interface:** Exibir um modal de confirmação ("Este turno viola a regra de interjornada. Deseja forçar a escalação?") ou bloquear a ação dependendo da gravidade.

### 3.5. Gestão de Disponibilidade e Trocas (Self-Service)

**Objetivo:** Descentralizar a gestão de trocas.

* **App/Portal do Colaborador:**
* **Minha Escala:** Visualização apenas dos seus turnos.
* **Ofertar Turno:** Botão para disponibilizar um turno para troca.
* **Pegar Turno:** Visualizar turnos ofertados por colegas do mesmo departamento e candidatar-se.


* **Fluxo de Aprovação:**
1. Colaborador A solicita troca.
2. Colaborador B aceita.
3. Gestor recebe notificação (Email ou Dashboard).
4. Gestor aprova -> Sistema valida Compliance para ambos -> Escala atualizada.



---

## 4. Arquitetura Técnica (Baseado no seu código)

### 4.1. Banco de Dados (PostgreSQL via SQLAlchemy)

Será necessário ajustar/criar as seguintes tabelas no `models.py`:

**Atualizar `Turno`:**

* Garantir que o campo `color` (já existente) seja hexadecimal.
* Adicionar campo `tipo` (Enum: 'TRABALHO', 'FOLGA', 'FERIAS').

**Atualizar `EscalaTrabalho`:**

* Adicionar `status` (Enum: 'RASCUNHO', 'PUBLICADO').
* Adicionar `compliance_warning` (Texto: Armazena o aviso de erro legal, se houver).

**Nova Tabela `SolicitacaoTroca`:**

```python
class SolicitacaoTroca(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    solicitante_id = db.Column(db.Integer, db.ForeignKey('funcionario.id'))
    turno_origem_id = db.Column(db.Integer, db.ForeignKey('escala_trabalho.id'))
    candidato_id = db.Column(db.Integer, db.ForeignKey('funcionario.id'), nullable=True)
    turno_destino_id = db.Column(db.Integer, db.ForeignKey('escala_trabalho.id'), nullable=True)
    status = db.Column(db.String(20)) # PENDENTE, APROVADO, REJEITADO
    data_solicitacao = db.Column(db.DateTime, default=datetime.utcnow)

```

### 4.2. API Endpoints (Flask Blueprints)

Criar rotas em `blueprints/escalas.py` ou um novo `blueprints/scheduler.py`:

* `GET /api/scheduler/events`: Retorna JSON com eventos para o calendário (formato FullCalendar).
* `POST /api/scheduler/move`: Endpoint para Drag-and-Drop. Recebe `{funcionario_id, data, turno_id}`. Executa validação de compliance e retorna 200 (OK) ou 409 (Conflito/Alerta).
* `POST /api/scheduler/publish`: Efetiva as escalas de "Rascunho" para "Publicado" e dispara a sincronização com a API Secullum.

### 4.3. Integração com Secullum Ponto Web

* **Referência:** `Integracao_Externa_Ponto_Web.pdf` (Página 9 - Cadastro de Horários e Página 23 - Cadastro de Funcionários).
* **Lógica:** O Secullum Ponto Web trabalha vinculando um `HorarioNumero` ao funcionário.
* Ao alterar a escala no seu sistema, o `tasks.py` deve identificar qual "Horário" no Secullum corresponde à combinação de turnos da semana/mês ou utilizar a funcionalidade de **Escala Cíclica** ou **Alteração de Horário Provisória** se a API suportar (verificar endpoints de *Troca de Horário* ou *Inclusão de Ponto* caso a escala seja tratada como exceção).
* *Nota:* Se a API da Secullum não permitir alterar turnos por dia facilmente, o sistema funcionará como a "verdade" gerencial, e a exportação para o Secullum pode ser feita via arquivo texto (layout de importação) ou ajustando o horário do funcionário via API (`PUT /Funcionarios`).



---

## 5. Plano de Implementação (Fases)

### Fase 1: Visualização e Drag-and-Drop (Front-end Core)

* Instalar biblioteca de calendário no frontend (recomendação: **FullCalendar** com plugin de *ResourceTimeline*).
* Criar API `GET` para alimentar o calendário com dados de `EscalaTrabalho`.
* Implementar API `POST` simples para salvar a movimentação.
* Habilitar renderização de cores baseada na tabela `Turno`.

### Fase 2: Motor de Compliance (Back-end Logic)

* Criar serviço `ComplianceService` em Python.
* Implementar regra de 11h de descanso (Interjornada).
* Implementar verificação de folga no 7º dia.
* Conectar serviço ao endpoint de `POST` (salvamento).

### Fase 3: Autoatendimento e Trocas

* Criar interface móvel simplificada para colaboradores.
* Implementar fluxo de solicitação e aprovação de trocas.

### Fase 4: Sincronização Bidirecional

* Garantir que alterações feitas no sistema reflitam no Ponto Secullum (via API `secullum_api.py`).
* Importar batidas realizadas (`Integracao_Externa_Ponto_Web.pdf` - Rota Batidas) para comparar **Planejado vs. Realizado** na visão de Gantt.