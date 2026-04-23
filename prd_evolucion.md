Esta é uma excelente melhoria de **UX (Experiência do Utilizador)**. No fechamento da folha de ponto, o RH precisa bater o olho e saber rapidamente se aquele dia com horas extras ou faltas era um dia normal, um fim de semana ou um feriado.

Abaixo, apresento o **PRD (Documento de Requisitos do Produto)** para esta funcionalidade, seguido do guia de implementação técnica.

---

### 📄 PRD: Identificação Visual de Fins de Semana e Feriados no Espelho

**1. Visão Geral**
O módulo de "Espelho de Ponto" passará a colorir automaticamente o fundo das linhas (ou da célula de data) baseando-se no tipo do dia: Sábado, Domingo ou Feriado. Isso agilizará a auditoria visual por parte dos gestores e do RH.

**2. Regras de Negócio e Hierarquia**
Um dia só pode ter uma cor de destaque. Se um Feriado cair num Domingo, a cor do Feriado terá prioridade, pois dita regras de horas extras diferentes (geralmente 100%).
* **Prioridade 1:** Feriado (Fundo Amarelo Claro)
* **Prioridade 2:** Domingo (Fundo Vermelho/Rosa Claro)
* **Prioridade 3:** Sábado (Fundo Azul Claro)
* **Prioridade 4:** Dia Útil (Sem cor de fundo / Padrão do sistema)

**3. Paleta de Cores (Cores Pastel/Claras)**
* Sábado: `#f1f5f9` (Cinza/Azulado muito claro)
* Domingo: `#ffe4e6` (Rosa muito claro)
* Feriado: `#fef08a` (Amarelo pastel)

**4. Requisitos Técnicos**
* **Back-end (`blueprints/espelho.py`):** O sistema já converte as strings de data em objetos `datetime`. Precisamos adicionar uma verificação de `weekday()` (onde 5 = Sábado, 6 = Domingo) e cruzar a data com a tabela `Feriado` do banco de dados. A variável `tipo_dia` será passada no dicionário para o HTML.
* **Front-end (`templates/batidas.html`):** Adicionar regras de CSS na tabela de renderização usando blocos condicionais do Jinja2 (`{% if ... %}`).

---

### 🛠️ Como Implementar (Passo a Passo)

Para aplicar esta melhoria no seu sistema, precisaremos alterar dois arquivos:

#### Passo 1: Atualizar o Back-end (`blueprints/espelho.py`)
Abra o arquivo `blueprints/espelho.py`. Precisamos fazer com que ele busque os feriados e classifique o dia.

**1. No topo do ficheiro, adicione `Feriado` aos seus imports (se já não estiver lá):**
```python
from models import Batida, Funcionario, AlocacaoDiaria, Feriado
```

**2. Dentro da rota/função principal do espelho, logo antes de agrupar as batidas (onde começa o `for (d_str, fid, nome), horas in agrupado.items():`), adicione a busca de feriados:**
```python
    # (Novo) Busca todos os feriados do mês/ano para não ter que ir ao banco a cada linha
    feriados_db = Feriado.query.all() 
    datas_feriados = [f.data for f in feriados_db] # Lista com as datas dos feriados
```

**3. Dentro do loop que processa cada dia (`for (d_str, fid, nome)...`), adicione a lógica para classificar a variável `tipo_dia`:**
```python
        # Código que já existe...
        data_obj = datetime.strptime(d_str, '%Y-%m-%d').date()
        
        # --- NOVA LÓGICA DE CLASSIFICAÇÃO DE DIA ---
        tipo_dia = 'normal'
        if data_obj in datas_feriados:
            tipo_dia = 'feriado'
        elif data_obj.weekday() == 6:  # 6 = Domingo no Python
            tipo_dia = 'domingo'
        elif data_obj.weekday() == 5:  # 5 = Sábado no Python
            tipo_dia = 'sabado'
        # ------------------------------------------

        # ... resto das validações do seu código ...

        # No final do loop, ao adicionar ao `batidas_agrupadas.append({ ... })`, inclua a nova chave:
        batidas_agrupadas.append({
            'data': d_str,
            'funcionario_id': fid,
            'funcionario': nome,
            'horas': horas_ordenadas,
            'status': status_lista,
            'tipo_dia': tipo_dia  # <-- NOVA CHAVE INCLUÍDA AQUI
        })
```

#### Passo 2: Atualizar o Visual (`templates/batidas.html`)
Abra o arquivo `templates/batidas.html`.

**1. Adicione o CSS no topo do arquivo (ou no bloco `{% block extra_css %}`):**
```html
<style>
    /* Cores de fundo bem claras para não atrapalhar a leitura do texto */
    .bg-sabado { background-color: #f1f5f9 !important; }   /* Azul/Cinza claro */
    .bg-domingo { background-color: #ffe4e6 !important; }  /* Rosa claro */
    .bg-feriado { background-color: #fef08a !important; }  /* Amarelo claro */
</style>
```

**2. Modifique a Tabela:**
Procure pela linha onde começa o loop que desenha a tabela (`{% for linha in batidas_agrupadas %}`). Altere a tag `<tr>` para imprimir a classe CSS dinamicamente com base na nova variável que criamos no back-end.

```html
<tr class="{% if linha.tipo_dia == 'sabado' %}bg-sabado{% elif linha.tipo_dia == 'domingo' %}bg-domingo{% elif linha.tipo_dia == 'feriado' %}bg-feriado{% endif %}">
    
    <td>
        <strong>{{ linha.data | format_date_br }}</strong>
        {% if linha.tipo_dia == 'feriado' %}
            <br><span class="badge bg-warning text-dark" style="font-size: 0.65rem;">Feriado</span>
        {% endif %}
    </td>
    <td>{{ linha.funcionario }}</td>
    </tr>
```

**O que vai acontecer após esta mudança?**
Ao acessar o `/config/espelho`, as linhas que corresponderem a finais de semana ou feriados ganharão automaticamente um fundo com as cores claras. Além disso, adicionei uma pequena "badge" (etiqueta) extra de "Feriado" embaixo da data, para que, além da cor amarela, fique escrito o porquê de estar amarelo.