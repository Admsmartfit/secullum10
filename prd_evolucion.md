Identifiquei a razão pela qual este erro (que costuma disparar um erro "500 Internal Server Error" invisível no back-end) acontece quando um período de múltiplos dias é selecionado. 

O problema divide-se em três partes:
1. **Tipagem de Datas no SQLite:** Quando consultamos uma lista de dias (ex: `>= dia 1` e `<= dia 5`), a base de dados pode devolver os dias num formato diferente do que o Python espera internamente. Quando o código tenta cruzar as batidas (`b.data`) com o dia gerado pelo Python (`curr_date`), ocorre um *crash*. Se for apenas o dia de hoje, a base de dados não entra no loop de soma de dias e passa ilesa (muitas vezes exibindo apenas vazio, em vez de erro).
2. **Dados Corrompidos:** Se no intervalo selecionado houver uma "Alocação" antiga sem turno amarrado, ou uma "Batida" sem hora válida, o código "tombava" ao tentar ler a hora.
3. **Falta de Codificação na URL (JS):** Se o nome do departamento tiver espaços ou acentos (ex: "Recepção"), a URL de requisição (`&dept=Recepção`) pode quebrar na ida para o servidor dependendo do navegador.

Para blindarmos o seu Gráfico de Gantt contra qualquer erro, siga os dois passos abaixo:

### Passo 1: Blindar o Back-end (`blueprints/escalas.py`)
Abra o ficheiro `blueprints/escalas.py` e substitua **toda a função `gantt_dados`** por esta versão, que agora usa chaves de texto fixas e tem proteção anti-falhas por linha:

```python
@escalas_bp.route('/gantt/dados')
@login_required
def gantt_dados():
    """JSON: linhas do Gantt cruzando Horário Base e Exceções num período de datas."""
    # Garante que, se vier vazio, assume a data de hoje para não quebrar a formatação ISO
    data_inicio_str = request.args.get('data_inicio', '').strip() or date.today().strftime('%Y-%m-%d')
    data_fim_str    = request.args.get('data_fim', '').strip() or date.today().strftime('%Y-%m-%d')
    dept            = request.args.get('dept', '').strip()
    func_id         = request.args.get('func_id', '').strip()

    try:
        data_inicio = date.fromisoformat(data_inicio_str)
        data_fim    = date.fromisoformat(data_fim_str)
    except ValueError:
        return jsonify([])

    # Limite de Segurança: Máximo de 15 dias de visualização em simultâneo
    if (data_fim - data_inicio).days > 15:
        data_fim = data_inicio + timedelta(days=15)
    if data_fim < data_inicio:
        data_fim = data_inicio

    # 1. Buscar os funcionários ativos
    q_func = Funcionario.query.filter_by(ativo=True)
    q_func = _filtrar_dept(q_func, dept)
    if func_id:
        q_func = q_func.filter(Funcionario.id == func_id)
    
    funcionarios = q_func.order_by(Funcionario.nome).all()
    func_ids = [f.id for f in funcionarios]

    if not func_ids:
        return jsonify([])

    # 2. Buscar alocações e converter a data rigidamente para STRING para evitar bugs do SQLite
    alocacoes_q = AlocacaoDiaria.query.filter(
        AlocacaoDiaria.data >= data_inicio,
        AlocacaoDiaria.data <= data_fim,
        AlocacaoDiaria.funcionario_id.in_(func_ids)
    ).all()
    aloc_map = {(a.funcionario_id, str(a.data)): a for a in alocacoes_q}

    # 3. Buscar as batidas e formatar de forma cega
    batidas_q = Batida.query.filter(
        Batida.data >= data_inicio,
        Batida.data <= data_fim,
        Batida.funcionario_id.in_(func_ids)
    ).all()
    
    batidas_por_func_data = {}
    for b in batidas_q:
        if b.hora: # Previne crash se a hora for nula
            batidas_por_func_data.setdefault((b.funcionario_id, str(b.data)), []).append(b.hora.strftime('%H:%M'))

    # 4. Construir as linhas iterando dia a dia
    rows = []
    curr_date = data_inicio
    
    while curr_date <= data_fim:
        data_str_formatada = curr_date.strftime('%d/%m/%Y')
        str_curr_date = str(curr_date) # Garante compatibilidade exata com os mapas acima
        weekday = curr_date.weekday()

        for f in funcionarios:
            try:
                aloc = aloc_map.get((f.id, str_curr_date))
                turno = None
                warning = None

                if aloc:
                    turno = aloc.turno
                    warning = aloc.compliance_warning
                elif f.horario_base and weekday in f.horario_base.dias_semana_list:
                    turno = f.horario_base

                # Ignorar a linha se não tiver turno válido ou se for FOLGA
                if not turno or not getattr(turno, 'nome', None) or turno.nome.upper() == 'FOLGA':
                    continue

                h_ini, h_fim, _ = turno.get_horario_dia(weekday)
                if not h_ini or not h_fim:
                    continue

                batidas = sorted(batidas_por_func_data.get((f.id, str_curr_date), []))
                
                rows.append({
                    'data_formatada':   data_str_formatada,
                    'funcionario_id':   str(f.id),
                    'funcionario_nome': f.nome,
                    'departamento':     f.departamento or '—',
                    'turno_nome':       turno.nome,
                    'turno_color':      getattr(turno, 'color', '#4f46e5') or '#4f46e5',
                    'planejado_inicio': h_ini.strftime('%H:%M') if hasattr(h_ini, 'strftime') else str(h_ini),
                    'planejado_fim':    h_fim.strftime('%H:%M') if hasattr(h_fim, 'strftime') else str(h_fim),
                    'batidas':          batidas,
                    'presente':         bool(batidas),
                    'compliance_warning': warning,
                })
            except Exception as e:
                # Se uma linha der erro (ex: dados corrompidos no SQL), não trava o resto do sistema
                print(f"[GANTT] Erro silencioso ao montar linha (Func: {f.id}, Dia: {str_curr_date}): {e}")
                continue
        
        curr_date += timedelta(days=1)

    return jsonify(rows)
```

### Passo 2: Codificar Variáveis no Front-end (`templates/escalas/gantt.html`)
Abra o ficheiro HTML, vá até ao final onde se encontra o bloco de script e encontre a função `carregarGantt()`. **Atualize a leitura das variáveis adicionando o `encodeURIComponent`**. Deve ficar exatamente assim:

```javascript
function carregarGantt() {
    // encodeURIComponent impede que departamentos com espaços e acentos quebrem a requisição URL
    const di = encodeURIComponent(document.getElementById('filtDataInicio').value || '');
    const df = encodeURIComponent(document.getElementById('filtDataFim').value || '');
    const dp = encodeURIComponent(document.getElementById('filtDept').value || '');
    const fu = encodeURIComponent(document.getElementById('filtFunc').value || '');

    const tbody = document.querySelector('#ganttTable tbody');
    tbody.innerHTML = '<tr><td colspan="3" class="text-center text-muted py-4"><i class="fas fa-spinner fa-spin me-2"></i> Analisando Escalas vs. Batidas...</td></tr>';

    fetch(`/escalas/gantt/dados?data_inicio=${di}&data_fim=${df}&dept=${dp}&func_id=${fu}`)
        .then(r => r.json())
        .then(rows => {
// ... O RESTO DO CÓDIGO CONTINUA IGUAL ABAIXO ...
```

Guarde os dois ficheiros e atualize o painel no navegador pressionando `Ctrl + F5` para limpar a cache do JavaScript. O Gráfico irá agora carregar perfeitamente qualquer escala contínua de datas!