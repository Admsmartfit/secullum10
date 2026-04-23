Após auditar o código que enviou (`blueprints/escalas.py` e `templates/escalas/gantt.html`), identifiquei exatamente porque a página continua a dar erro, o motivo pelo qual o filtro falha e o porquê de o gráfico ficar vazio.

### 🔍 Auditoria e Identificação de Erros

1.  **Filtro de Departamento "Burro" (Front-end):**
    No arquivo `gantt.html`, o select de funcionários (`#filtFunc`) é carregado com **todos** os funcionários do banco de dados na hora em que a página abre. Não existe nenhum código JavaScript programado para "escutar" a mudança do `#filtDept` e esconder os funcionários que não pertencem àquele departamento.
2.  **Omissão do Horário Base (Back-end):**
    No arquivo `escalas.py` (linha 335, na função `gantt_dados()`), o código faz a seguinte busca: `AlocacaoDiaria.query.filter_by(data=data_ref)`. 
    Como expliquei anteriormente, a tabela `AlocacaoDiaria` serve apenas para **exceções**. Se o funcionário estiver a cumprir o seu "Contrato Normal" (Horário Base), ele não tem registo na `AlocacaoDiaria` para aquele dia. Como o sistema não o encontra aí, aborta a operação, não desenha o turno planeado e, consequentemente, não desenha as batidas (porque a linha inteira do funcionário é ignorada).
3.  **Problema de Escala de Data (Início e Fim):**
    O Gráfico de Gantt foi desenhado para analisar a linha do tempo de **um único dia** (`data=date.today()`). Transformar isso num período de dias exige que a tabela passe a mostrar uma linha por "Funcionário + Dia", caso contrário os dados de terça-feira iriam sobrepor-se aos de segunda-feira no mesmo gráfico.

Para resolvermos tudo isto de forma robusta, elaborei o PRD abaixo.

---

# 📄 PRD: Refatoração do Gráfico de Gantt (Planejado vs. Realizado)

## 1. Visão Geral
O objetivo desta atualização é corrigir falhas de carregamento de dados (Horário Base vs. Exceções), implementar inteligência de interface nos filtros de departamento e expandir a capacidade de análise do Gráfico de Gantt para suportar períodos (Data de Início e Data de Fim) em vez de apenas um dia isolado.

## 2. Requisitos Funcionais

### 2.1. Filtro em Cascata (Departamento ➔ Funcionário)
* **Regra:** Ao selecionar um Departamento no menu dropdown, o campo de Funcionário deve ser esvaziado e recarregado apenas com os profissionais pertencentes àquele departamento.
* **Técnica:** Em vez de fazer chamadas pesadas ao servidor a cada clique, adicionaremos o atributo `data-dept` em cada `<option>` do HTML. O JavaScript interceptará o evento `change` do Select2 e esconderá/mostrará as opções em tempo real.

### 2.2. Parâmetro de Período (Data Início e Fim)
* **Inputs:** Substituir o campo único "DATA" por dois campos: `Data Início` e `Data Fim`.
* **Segurança/Performance:** Como renderizar gráficos em HTML consome memória RAM do navegador, o sistema deve limitar a consulta a um **máximo de 7 a 10 dias** por visualização. Se o gestor escolher um período maior, o sistema exibirá um aviso.
* **Layout da Tabela:** A primeira coluna da tabela, que antes exibia apenas "Funcionário", passará a exibir "Data" e "Funcionário" (Ex: `Segunda, 12/04 - Ricardo`). 

### 2.3. Motor de Resolução de Turnos (Correção do Erro de Dados Vazios)
* A rota `/gantt/dados` no back-end (`escalas.py`) será completamente reescrita.
* **Lógica de loop:** O Python fará um loop entre a `Data Início` e a `Data Fim`. Para cada dia e cada funcionário filtrado, aplicará a seguinte prioridade:
    1.  Verifica se há Exceção Manual na tabela `AlocacaoDiaria`. Se sim, usa esse turno.
    2.  Se não, verifica se o funcionário tem um `Horario Base` (Contrato) cadastrado e se o dia da semana atual faz parte desse contrato. Se sim, usa esse turno.
    3.  Se não houver nenhum dos dois (ou for "FOLGA"), ignora a linha.
* Após definir qual é o turno real do dia, cruza a informação com a tabela `Batida`.

## 3. Impacto no Back-end (`blueprints/escalas.py`)
A função `gantt_dados` deixará de fazer um `join` simples e passará a usar um mapa em memória:
1. Pega todos os funcionários ativos (filtrando pelo JS/Select).
2. Pega todas as `AlocacaoDiaria` do período para os ids filtrados.
3. Pega todas as `Batidas` do período.
4. Constrói o JSON linha a linha cruzando Dia, Turno Efetivo e Batidas.

## 4. Impacto no Front-end (`templates/escalas/gantt.html`)
* **HTML:** Alterar o painel de filtros inserindo `<input type="date" id="filtDataFim">`. 
* **Tabela:** Adicionar `<th>Data</th>` na cabecera.
* **JavaScript JS:** * Adicionar script para filtrar o Select2 `onChange` do departamento.
    * Garantir que a função `toPct(horaStr)` continue a calcular a percentagem na barra (de 05:00 a 24:00) de forma correta, mas iterando sobre os dados que agora trarão a propriedade `data_formatada`.

## 5. Critérios de Aceite (Como saberemos que está pronto?)
1. [ ] Quando seleciono "Recepção", só vejo recepcionistas.
2. [ ] Se a "Maria" trabalha de Segunda a Sexta por contrato e não teve exceção criada no calendário, ela APARCE no gráfico na mesma.
3. [ ] Consigo selecionar do dia 01/10 ao dia 05/10 e ver 5 linhas para a Maria (uma debaixo da outra) com as marcações de ponto desenhadas.

---

Com o PRD validado, vamos avançar com a implementação prática. Para aplicar esta inteligência de interface, período de datas e a resolução robusta do "Contrato vs. Exceção", siga os dois passos abaixo.

### Passo 1: Atualizar o Back-end (`blueprints/escalas.py`)

Abra o ficheiro `blueprints/escalas.py`.
1. Vá até ao topo do ficheiro e certifique-se de que a ferramenta `timedelta` está importada juntamente com o `date`:
   ```python
   from datetime import date, timedelta
   ```

2. Substitua a função `gantt_dados` (no final do ficheiro) por esta nova versão que itera num período de dias e resolve os conflitos de horário:

```python
@escalas_bp.route('/gantt/dados')
@login_required
def gantt_dados():
    """JSON: linhas do Gantt cruzando Horário Base e Exceções num período de datas."""
    data_inicio_str = request.args.get('data_inicio', date.today().strftime('%Y-%m-%d'))
    data_fim_str    = request.args.get('data_fim', date.today().strftime('%Y-%m-%d'))
    dept            = request.args.get('dept', '').strip()
    func_id         = request.args.get('func_id', '').strip()

    try:
        data_inicio = date.fromisoformat(data_inicio_str)
        data_fim    = date.fromisoformat(data_fim_str)
    except ValueError:
        return jsonify([])

    # Limite de Segurança: Máximo de 15 dias de visualização em simultâneo 
    # para não sobrecarregar a RAM do navegador com a renderização dos gráficos
    if (data_fim - data_inicio).days > 15:
        data_fim = data_inicio + timedelta(days=15)
    if data_fim < data_inicio:
        data_fim = data_inicio

    # 1. Buscar os funcionários ativos com base no filtro
    q_func = Funcionario.query.filter_by(ativo=True)
    q_func = _filtrar_dept(q_func, dept)
    if func_id:
        q_func = q_func.filter(Funcionario.id == func_id)
    
    funcionarios = q_func.order_by(Funcionario.nome).all()
    func_ids = [f.id for f in funcionarios]

    if not func_ids:
        return jsonify([])

    # 2. Buscar alocações diárias (exceções manuais) no período
    alocacoes_q = AlocacaoDiaria.query.filter(
        AlocacaoDiaria.data >= data_inicio,
        AlocacaoDiaria.data <= data_fim,
        AlocacaoDiaria.funcionario_id.in_(func_ids)
    ).all()
    aloc_map = {(a.funcionario_id, a.data): a for a in alocacoes_q}

    # 3. Buscar as batidas no período e garantir formato HH:MM
    batidas_q = Batida.query.filter(
        Batida.data >= data_inicio,
        Batida.data <= data_fim,
        Batida.funcionario_id.in_(func_ids)
    ).all()
    
    batidas_por_func_data = {}
    for b in batidas_q:
        batidas_por_func_data.setdefault((b.funcionario_id, b.data), []).append(b.hora.strftime('%H:%M'))

    # 4. Construir as linhas iterando dia a dia para cada funcionário
    rows = []
    curr_date = data_inicio
    
    while curr_date <= data_fim:
        data_str_formatada = curr_date.strftime('%d/%m/%Y')
        weekday = curr_date.weekday()

        for f in funcionarios:
            aloc = aloc_map.get((f.id, curr_date))
            turno = None
            warning = None

            # Dá prioridade à Alocação Manual. Se não houver, usa o Horário Base
            if aloc:
                turno = aloc.turno
                warning = aloc.compliance_warning
            elif f.horario_base and weekday in f.horario_base.dias_semana_list:
                turno = f.horario_base

            # Ignorar a linha se não tiver turno ou se o turno for "FOLGA"
            if not turno or turno.nome.upper() == 'FOLGA':
                continue

            h_ini, h_fim, _ = turno.get_horario_dia(weekday)
            if not h_ini or not h_fim:
                continue

            batidas = sorted(batidas_por_func_data.get((f.id, curr_date), []))
            
            rows.append({
                'data_formatada':   data_str_formatada,
                'funcionario_id':   str(f.id),
                'funcionario_nome': f.nome,
                'departamento':     f.departamento or '—',
                'turno_nome':       turno.nome,
                'turno_color':      turno.color or '#4f46e5',
                'planejado_inicio': h_ini.strftime('%H:%M'),
                'planejado_fim':    h_fim.strftime('%H:%M'),
                'batidas':          batidas,
                'presente':         bool(batidas),
                'compliance_warning': warning,
            })
        
        # Avança para o dia seguinte
        curr_date += timedelta(days=1)

    return jsonify(rows)
```

---

### Passo 2: Atualizar o Front-end (`templates/escalas/gantt.html`)

Abra o ficheiro `templates/escalas/gantt.html`. Faremos alterações na estrutura visual e no JavaScript.

**1. Substitua a zona dos filtros e da cabecera da tabela:**
*(Procure pelo bloco `row g-2 align-items-end mb-4` e substitua todo o código HTML dos selects e cabecera da tabela por este)*

```html
        <div class="row g-2 align-items-end mb-4">
            <div class="col-md-2">
                <label class="form-label small">Data Início</label>
                <input type="date" class="form-control" id="filtDataInicio" value="{{ data_hoje }}">
            </div>
            <div class="col-md-2">
                <label class="form-label small">Data Fim</label>
                <input type="date" class="form-control" id="filtDataFim" value="{{ data_hoje }}">
            </div>
            <div class="col-md-3">
                <label class="form-label small">Departamento</label>
                <select class="form-select select2" id="filtDept">
                    <option value="">(Todos)</option>
                    {% for d in departamentos %}
                    <option value="{{ d }}">{{ d }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="col-md-3">
                <label class="form-label small">Funcionário</label>
                <select class="form-select select2" id="filtFunc">
                    <option value="">(Todos)</option>
                    {% for f in funcionarios %}
                    <option value="{{ f.id }}" data-dept="{{ f.departamento or '' }}">{{ f.nome }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="col-md-2 text-end">
                <button class="btn btn-primary w-100" onclick="carregarGantt()">
                    <i class="fas fa-sync-alt me-1"></i> Atualizar
                </button>
            </div>
        </div>

        <div class="table-responsive" style="min-height: 400px;">
            <table class="table table-sm align-middle" id="ganttTable">
                <thead class="table-light">
                    <tr>
                        <th style="width: 100px;">Data</th>
                        <th style="width: 250px;">Funcionário / Turno</th>
                        <th>Linha do Tempo (05:00 - 23:59)</th>
                    </tr>
                </thead>
                <tbody>
                    </tbody>
            </table>
        </div>
```

**2. Substitua todo o bloco `<script>` no final do ficheiro por este código com a lógica de Cascata e Datas:**

```html
{% block extra_js %}
<script>
$(document).ready(function() {
    $('.select2').select2({ theme: 'bootstrap-5' });

    // 1. Lógica do Filtro em Cascata: Departamento -> Funcionário
    const $funcSelect = $('#filtFunc');
    const funcOptions = $funcSelect.find('option').clone(); // Guarda as opções de origem em memória

    $('#filtDept').on('change', function() {
        const dept = $(this).val();
        
        $funcSelect.empty(); // Limpa os funcionários do ecrã
        
        // Recarrega apenas os funcionários do departamento selecionado
        funcOptions.each(function() {
            const funcDept = $(this).attr('data-dept') || '';
            if (!dept || funcDept === dept || $(this).val() === '') {
                $funcSelect.append($(this).clone());
            }
        });
        
        $funcSelect.val(''); // Volta à opção "Todos"
        $funcSelect.trigger('change.select2'); // Atualiza a renderização
        
        carregarGantt(); // Dispara o motor de visualização
    });

    // 2. Dispara automaticamente a pesquisa ao mudar dados
    $('#filtDataInicio, #filtDataFim, #filtFunc').on('change', carregarGantt);
    
    // Inicia a primeira vez
    carregarGantt();
});

function toPct(horaStr) {
    if (!horaStr) return 0;
    const [h, m] = horaStr.split(':').map(Number);
    const startHour = 5;
    const totalHours = 19; // De 05:00 às 23:59
    const dec = h + (m / 60);
    let pct = ((dec - startHour) / totalHours) * 100;
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    return pct;
}

function carregarGantt() {
    const di = document.getElementById('filtDataInicio').value;
    const df = document.getElementById('filtDataFim').value;
    const dp = document.getElementById('filtDept').value;
    const fu = document.getElementById('filtFunc').value;

    const tbody = document.querySelector('#ganttTable tbody');
    tbody.innerHTML = '<tr><td colspan="3" class="text-center text-muted py-4"><i class="fas fa-spinner fa-spin me-2"></i> Analisando Escalas vs. Batidas...</td></tr>';

    fetch(`/escalas/gantt/dados?data_inicio=${di}&data_fim=${df}&dept=${dp}&func_id=${fu}`)
        .then(r => r.json())
        .then(rows => {
            if(!rows.length) {
                tbody.innerHTML = '<tr><td colspan="3" class="text-center text-muted py-4">Nenhum funcionário com turno ou batida para os filtros selecionados.</td></tr>';
                return;
            }

            let html = '';
            rows.forEach(r => {
                const startPct = toPct(r.planejado_inicio);
                const endPct = toPct(r.planejado_fim);
                const widthPct = endPct - startPct;

                let hoursHtml = '';
                for(let i=5; i<=23; i++) {
                    hoursHtml += `<div style="flex:1; text-align:left; border-left:1px solid #e2e8f0; padding-left:2px; font-size:0.7rem; color:#94a3b8;">${i}h</div>`;
                }

                let batidasHtml = '';
                if (r.batidas) {
                    r.batidas.forEach(b => {
                        batidasHtml += `<div class="bar-punch" style="left: ${toPct(b)}%;" title="Batida: ${b}"></div>`;
                    });
                }

                html += `
                <tr>
                    <td class="fw-bold align-middle text-primary">${r.data_formatada}</td>
                    <td class="align-middle">
                        <div class="fw-bold text-dark">${r.funcionario_nome}</div>
                        <div class="text-muted" style="font-size:0.75rem;">${r.turno_nome}</div>
                    </td>
                    <td>
                        <div class="gantt-container">
                            <div class="gantt-hours">
                                ${hoursHtml}
                            </div>
                            <div class="gantt-row">
                                <div class="bar-planned" style="left: ${startPct}%; width: ${widthPct}%; background-color: ${r.turno_color}40; border-color: ${r.turno_color};" title="Planejado: ${r.planejado_inicio} - ${r.planejado_fim}"></div>
                                ${batidasHtml}
                            </div>
                        </div>
                    </td>
                </tr>`;
            });
            tbody.innerHTML = html;
        })
        .catch(err => {
            console.error(err);
            tbody.innerHTML = '<tr><td colspan="3" class="text-center text-danger py-4">Erro ao carregar dados.</td></tr>';
        });
}
</script>
{% endblock %}
```

Com estas alterações gravadas, recarregue o painel pressionando `Ctrl + F5`. Agora pode parametrizar uma semana inteira de datas, os departamentos limparão a lista dos funcionários, e a plataforma desenhará perfeitamente as rotinas base com as marcações de ponto correspondentes em cima delas!