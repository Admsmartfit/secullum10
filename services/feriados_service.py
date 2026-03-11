"""
Motor de Feriados: BrasilAPI (nacionais) + Calendario.com.br (municipais) + fallback holidays.
Ordem: BrasilAPI → Calendario.com.br → biblioteca Python holidays (offline).
"""
import requests
from datetime import date
from typing import Dict, List, Optional

# Cache em memória: {(ano, mes, ibge, uf): frozenset de dates}
_CACHE: Dict = {}


def clear_cache() -> None:
    """Limpa cache após sync."""
    _CACHE.clear()


def is_feriado(data_ref: date,
               cidade_ibge: Optional[str] = None,
               uf: Optional[str] = None) -> bool:
    """
    Verifica se `data_ref` é feriado para a localidade.
    Cobre: nacionais + estadual (uf) + municipal (cidade_ibge).
    Usa cache por mês para não bater no banco em loops de 200 funcionários.
    """
    from flask import has_app_context
    if not has_app_context():
        return False

    key = (data_ref.year, data_ref.month, cidade_ibge or '', uf or '')
    if key not in _CACHE:
        _CACHE[key] = _load_mes(data_ref.year, data_ref.month, cidade_ibge, uf)
    return data_ref in _CACHE[key]


def _load_mes(ano: int, mes: int,
              cidade_ibge: Optional[str],
              uf: Optional[str]) -> frozenset:
    import calendar
    from models import Feriado
    from sqlalchemy import or_, and_

    ult = calendar.monthrange(ano, mes)[1]
    d_ini, d_fim = date(ano, mes, 1), date(ano, mes, ult)

    conds = [and_(Feriado.tipo == 'nacional')]
    if uf:
        conds.append(and_(Feriado.tipo == 'estadual', Feriado.uf == uf))
    if cidade_ibge:
        conds.append(and_(Feriado.tipo == 'municipal', Feriado.cidade_ibge == cidade_ibge))

    rows = Feriado.query.filter(
        Feriado.data >= d_ini,
        Feriado.data <= d_fim,
        Feriado.ativo == True,
        or_(*conds),
    ).all()
    return frozenset(f.data for f in rows)


# ── Fonte 1: BrasilAPI — nacionais, gratuita, sem token ──────────────────────

def _brasil_api(ano: int) -> List[Dict]:
    try:
        r = requests.get(
            f'https://brasilapi.com.br/api/feriados/v1/{ano}',
            timeout=10,
        )
        r.raise_for_status()
        return [{'data': i['date'], 'descricao': i['name'], 'tipo': 'nacional'}
                for i in r.json()]
    except Exception:
        return []


# ── Fonte 2: Calendario.com.br — estadual + municipal, requer token ───────────

def _calendario_api(ano: int, uf: str, cidade: str,
                    token: str, cidade_ibge: str = None) -> List[Dict]:
    try:
        r = requests.get(
            'https://api.calendario.com.br/',
            params={'json': 'true', 'ano': ano, 'estado': uf,
                    'cidade': cidade, 'token': token},
            timeout=15,
        )
        r.raise_for_status()
        result = []
        for item in r.json():
            tc = item.get('type_code', '')
            if tc == 'F':
                tipo = 'nacional'
            elif tc == 'FL':
                tipo = 'estadual'
            elif tc == 'FC':
                tipo = 'municipal'
            else:
                continue  # pula pontos facultativos
            result.append({
                'data': item['date'],       # DD/MM/YYYY
                'descricao': item['name'],
                'tipo': tipo,
                'uf': uf,
                'cidade_ibge': cidade_ibge,
            })
        return result
    except Exception:
        return []


# ── Fonte 3: biblioteca Python holidays — fallback offline ───────────────────

def _holidays_python(ano: int, uf: str = None) -> List[Dict]:
    try:
        import holidays as hol
        br = hol.Brazil(state=uf, years=ano) if uf else hol.Brazil(years=ano)
        tipo = 'estadual' if uf else 'nacional'
        return [{'data': str(d), 'descricao': n, 'tipo': tipo, 'uf': uf}
                for d, n in br.items()]
    except Exception:
        return []


# ── Sincronização principal ───────────────────────────────────────────────────

def sincronizar_feriados(ano: int, usuario_id: int = None) -> Dict:
    """
    Sincroniza feriados nacionais e municipais para `ano`.
    Retorna {'criados': N, 'avisos': [...]}
    """
    from models import Feriado, UnidadeLider, Configuracao
    from extensions import db

    criados = 0
    avisos: List[str] = []

    # ── Nacionais via BrasilAPI ───────────────────────────────────────────────
    nacionais = _brasil_api(ano)
    if not nacionais:
        nacionais = _holidays_python(ano)
        avisos.append('BrasilAPI indisponível — fallback biblioteca holidays para nacionais.')

    for item in nacionais:
        try:
            d = date.fromisoformat(item['data'])
        except ValueError:
            continue
        if not Feriado.query.filter_by(data=d, tipo='nacional').first():
            db.session.add(Feriado(
                data=d, descricao=item['descricao'], tipo='nacional',
                fonte='brasilapi', ativo=True, criado_por_id=usuario_id,
            ))
            criados += 1

    # ── Token para Calendario.com.br ─────────────────────────────────────────
    token = None
    try:
        cfg = Configuracao.query.filter_by(chave='calendario_api_token').first()
        if cfg:
            token = cfg.valor
    except Exception:
        pass

    # ── Municipais por cada cidade/UF ativa em UnidadeLider ──────────────────
    unidades = UnidadeLider.query.filter(
        UnidadeLider.empresa_uf.isnot(None),
        UnidadeLider.empresa_cidade.isnot(None),
    ).all()

    seen: set = set()
    for u in unidades:
        key = (u.empresa_uf, u.empresa_cidade)
        if key in seen:
            continue
        seen.add(key)
        ibge = getattr(u, 'cidade_ibge', None)

        if token:
            municipais = _calendario_api(ano, u.empresa_uf, u.empresa_cidade, token, ibge)
        else:
            municipais = []

        if not municipais:
            municipais = _holidays_python(ano, u.empresa_uf)
            if municipais:
                avisos.append(
                    f'Calendario.com.br indisponível para {u.empresa_cidade}/{u.empresa_uf}'
                    f' — fallback holidays (apenas estaduais).'
                )

        for item in municipais:
            try:
                ds = item['data']
                if '/' in ds:
                    dd, mm, yy = ds.split('/')
                    d = date(int(yy), int(mm), int(dd))
                else:
                    d = date.fromisoformat(ds)
            except (ValueError, TypeError):
                continue

            tipo = item.get('tipo', 'municipal')
            ibge_val = item.get('cidade_ibge') or ibge
            uf_val = item.get('uf') or u.empresa_uf

            if not Feriado.query.filter_by(
                data=d, tipo=tipo, uf=uf_val, cidade_ibge=ibge_val
            ).first():
                db.session.add(Feriado(
                    data=d, descricao=item['descricao'], tipo=tipo,
                    uf=uf_val, cidade_ibge=ibge_val,
                    fonte='calendario' if token else 'holidays',
                    ativo=True, criado_por_id=usuario_id,
                ))
                criados += 1

    db.session.commit()
    clear_cache()
    return {'criados': criados, 'avisos': avisos}
