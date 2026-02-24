Com base na análise do código-fonte fornecido (Secullum10) e no seu problema específico de gestão de escala (cobertura de 7 dias com restrições da CLT para mulheres aos domingos), elaborei uma estratégia de melhoria.

O seu sistema atual já possui a estrutura base (`models.py` com `Turno` e `EscalaTrabalho`, e um serviço `motor_clt.py`), mas falta uma camada de **inteligência de agendamento** para resolver automaticamente conflitos como o do "Domingo da Mulher".

Abaixo apresento a análise e o PRD (Documento de Requisitos de Produto) detalhado.

---

### Análise do Cenário e do Código

**O Problema:**
Você tem uma operação ininterrupta (7 dias). A "Recepcionista D" (fixa de domingo) precisa folgar pelo menos 1 domingo a cada 15 dias (conforme Art. 386 da CLT para mulheres, embora muitas convenções usem 1 por mês). Quando ela folga, uma das outras 3 (A, B ou C) precisa cobrir, o que desorganiza a folga delas durante a semana.

**O Estado Atual do Sistema (Code Review):**

1. **`models.py`**: Já suporta a criação de escalas, mas parece armazenar dias individuais. Faltam conceitos de "Ciclos" ou "Padrões de Revezamento".
2. **`services/motor_clt.py`**: Existe um esboço de validação, mas precisa ser expandido para verificar especificamente a regra de "Domingos consecutivos para mulheres".
3. **Interface (`templates/escalas/`)**: Parece focada em visualização ou inserção manual (um a um ou em lote simples). Isso torna a administração "complicada" como você citou.

---

### PRD: Módulo de Escala Inteligente e Revezamento Automático

**Visão Geral:**
Implementar um gerador de escalas baseado em regras que automatize o preenchimento do calendário, garantindo a cobertura da recepção e respeitando automaticamente a regra do domingo para mulheres, sugerindo trocas inteligentes.

#### 1. Funcionalidades Chave (Solução Proposta)

##### 1.1. Cadastro de "Padrões de Revezamento" (Shift Patterns)

Em vez de lançar dias soltos, o sistema deve permitir criar padrões.

* **Padrão A (Semanal):** Seg-Sex (Sáb alternado).
* **Padrão B (Fim de Semana):** Apenas Domingos e Feriados.
* **Regra de Exceção:** "A cada X domingos trabalhados, 1 folga obrigatória".

##### 1.2. Gerador Automático de Cobertura (The Solver)

Um algoritmo que preenche a escala do mês seguinte com um clique.

* **Lógica:** O sistema aloca a "Recepcionista D" em todos os domingos.
* **Validação CLT:** O `motor_clt.py` detecta que no 2º (ou 3º) domingo ela *precisa* folgar.
* **Resolução de Conflito:** O sistema busca entre as Recepcionistas A, B e C qual tem o menor saldo no **Banco de Horas** (já existente no seu sistema) e sugere a escala dela para esse domingo específico, gerando uma folga compensatória para ela na semana.

##### 1.3. Interface de Matriz de Cobertura (Heatmap)

Uma visualização onde as linhas são os funcionários e as colunas os dias, mas com uma linha extra no rodapé: "Cobertura".

* Se o dia tiver 0 recepcionistas, fica vermelho.
* Se tiver 1, fica verde.
* Isso permite ao operador ver "buracos" na escala instantaneamente.

---

#### 2. Requisitos Técnicos (Baseado no seu código)

##### 2.1. Atualização do `models.py`

Adicionar suporte a padrões e regras de restrição.

```python
# Sugestão de alteração/adição no models.py

class RegraEscala(db.Model):
    __tablename__ = 'regra_escala'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100)) # Ex: "Regra Mulher Domingo"
    tipo_restricao = db.Column(db.String(50)) # Ex: "DOMINGO_QUINZENAL"
    genero_aplicavel = db.Column(db.String(1)) # 'F', 'M' ou 'A' (Ambos)
    ativo = db.Column(db.Boolean, default=True)

class PadraoTurno(db.Model):
    """Define templates como 6x1, 5x2, ou Fixo Domingo"""
    __tablename__ = 'padrao_turno'
    id = db.Column(db.Integer, primary_key=True)
    descricao = db.Column(db.String(100))
    dias_trabalho = db.Column(db.Integer) # Ex: 6
    dias_folga = db.Column(db.Integer) # Ex: 1

```

##### 2.2. Melhoria no `services/motor_clt.py`

Implementar a verificação específica que está lhe causando dor de cabeça.

```python
# services/motor_clt.py

def validar_escala_domingo_mulher(funcionario_id, data_escala, session):
    funcionario = session.query(Funcionario).get(funcionario_id)
    
    # Se não for mulher, ignora a regra específica (ou aplica regra geral)
    if funcionario.sexo != 'F':
        return True, "OK"

    # Busca os últimos domingos trabalhados
    # Lógica: Se trabalhou no domingo passado, este deve ser folga?
    # Depende da configuração (1x1 ou quinzenal)
    
    ultimos_domingos = buscar_turnos_em_domingos_anteriores(funcionario_id, data_escala, limit=1)
    
    if len(ultimos_domingos) >= 1: 
        # Já trabalhou no último domingo.
        # Pela regra estrita (quinzenal), hoje deve ser folga.
        return False, "Violação Art. 386 CLT: Revezamento quinzenal obrigatório p/ mulheres."
    
    return True, "OK"

```

##### 2.3. Endpoint de "Auto-Completar" (`blueprints/escalas.py`)

Criar uma rota `/api/escalas/gerar_automatico` que recebe o mês/ano.

1. Limpa a escala futura (rascunho).
2. Aplica os turnos fixos.
3. Roda o `validar_escala_domingo_mulher`.
4. Onde falhar (o domingo de folga obrigatória), o sistema procura um "Curinga" (outra recepcionista) e aloca o turno, marcando como "Sugestão do Sistema".

---

#### 3. UX/UI - Referências de Mercado

Para facilitar para o operador, sugiro implementar uma interface semelhante a sistemas como **Planday** ou **Deputy**:

1. **View de Conflitos (Alertas):**
* Ao abrir a tela de escalas, não mostre apenas a tabela. Mostre um painel superior: *"Atenção: Dia 15/10 (Domingo) está descoberto pois Recepcionista D precisa folgar."*
* Ao lado do alerta, um botão: **"Resolver Automaticamente"**.


2. **Botão "Resolver Automaticamente":**
* Ao clicar, o sistema abre um modal: *"Sugiro escalar a Recepcionista A para este domingo (ela tem -10h no banco). Em troca, sugiro dar folga para ela na Terça-feira dia 17/10."*
* O operador só clica em "Aplicar".


3. **Visualização em Linha do Tempo (Gantt):**
* Utilize a biblioteca **FullCalendar Scheduler** (versão Resource Timeline) ou **Vis.js Timeline**.
* Mostre as 4 recepcionistas uma abaixo da outra.
* Os Domingos devem ter uma cor de fundo destacada visualmente para facilitar a conferência da regra.



#### 4. Plano de Ação (Roadmap)

1. **Imediato (Correção Rápida):**
* No arquivo `templates/escalas/calendario.html`, adicione uma lógica visual (Javascript) que pinte de **vermelho** a célula se uma funcionária mulher for alocada em dois domingos consecutivos. Isso já ajuda o operador visualmente.


2. **Curto Prazo (Back-end):**
* Atualizar `motor_clt.py` com a regra do Art. 386.
* Criar o script Python `sugerir_cobertura.py` que recebe uma data vazia e retorna qual funcionário é o melhor candidato para cobrir (baseado em saldo de Banco de Horas).


3. **Médio Prazo (Front-end):**
* Criar a tela de "Geração de Escala Mensal" onde o operador define as regras bases e o sistema preenche os 30 dias de uma vez.



Esta abordagem transforma o problema de "administrar escala manualmente" em "gerenciar exceções", onde o sistema faz o trabalho pesado de alocação e o operador apenas aprova as trocas necessárias para cobrir as folgas obrigatórias.