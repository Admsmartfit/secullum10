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
    import unicodedata
    import re
    try:
        params = {'json': 'true', 'ano': ano, 'estado': uf, 'token': token}

        # Prioriza código IBGE (busca exata); senão sanitiza nome da cidade
        if cidade_ibge:
            params['ibge'] = cidade_ibge
        else:
            cidade_limpa = unicodedata.normalize('NFKD', cidade).encode('ASCII', 'ignore').decode('utf-8')
            cidade_limpa = re.sub(r'\s+', '_', cidade_limpa.strip().upper())
            params['cidade'] = cidade_limpa

        r = requests.get('https://api.calendario.com.br/', params=params, timeout=15)
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
    except Exception as e:
        print(f'[Feriados] Erro na API Calendario.com.br: {e}')
        return []


# ── Fonte 3: biblioteca Python holidays — fallback offline ───────────────────

def _holidays_python(ano: int, uf: str = None) -> List[Dict]:
    try:
        import holidays as hol
        # 'subdiv' é o parâmetro correto em versões modernas da biblioteca holidays
        br = hol.country_holidays('BR', subdiv=uf, years=ano) if uf else hol.country_holidays('BR', years=ano)
        tipo = 'estadual' if uf else 'nacional'
        return [{'data': str(d), 'descricao': n, 'tipo': tipo, 'uf': uf}
                for d, n in br.items()]
    except Exception as e:
        print(f'[Feriados] Erro na biblioteca offline holidays: {e}')
        return []


# ── Sincronização principal ───────────────────────────────────────────────────

def sincronizar_feriados(ano: int, usuario_id: int = None) -> Dict:
    """
    Sincroniza feriados nacionais e municipais para `ano`.
    Retorna {'criados': N, 'avisos': [...]}
    """
    from models import Feriado, UnidadeLider, Configuracao
    from extensions import db
    from flask import current_app

    criados = 0
    avisos: List[str] = []

    try:
        from sqlalchemy import or_, and_
        # ── Nacionais via BrasilAPI ───────────────────────────────────────────────
        nacionais = _brasil_api(ano)
        if not nacionais:
            nacionais = _holidays_python(ano)
            avisos.append('BrasilAPI indisponível — fallback biblioteca holidays para nacionais.')

        # Converte usuario_id para int se possível (Flask-Login costuma retornar string)
        u_id = None
        if usuario_id is not None:
            try:
                u_id = int(usuario_id)
            except (ValueError, TypeError):
                u_id = None

        for item in nacionais:
            try:
                d = date.fromisoformat(item['data'])
            except ValueError:
                continue
            if not Feriado.query.filter_by(data=d, tipo='nacional').first():
                db.session.add(Feriado(
                    data=d, descricao=item['descricao'], tipo='nacional',
                    fonte='brasilapi', ativo=True, criado_por_id=u_id,
                ))
                criados += 1
        db.session.flush()

        # ── Token para Calendario.com.br ─────────────────────────────────────────
        token = None
        try:
            cfg = Configuracao.query.filter_by(chave='calendario_api_token').first()
            if cfg:
                token = cfg.valor
        except Exception:
            pass

        # ── Municipais e Estaduais por cada cidade/UF ativa em UnidadeLider ────────
        unidades = UnidadeLider.query.filter(
            UnidadeLider.empresa_uf.isnot(None),
            UnidadeLider.empresa_cidade.isnot(None),
        ).all()

        vistos_uf: set = set()
        vistos_mun: set = set()

        for u in unidades:
            uf = u.empresa_uf
            cidade = u.empresa_cidade
            ibge = getattr(u, 'cidade_ibge', None)

            # --- Sincroniza ESTADUAL uma vez por UF ---
            if uf not in vistos_uf:
                vistos_uf.add(uf)
                # Biblioteca holidays é mais precisa para estaduais brasileiros
                estaduais = [h for h in _holidays_python(ano, uf) if h.get('tipo') == 'estadual']
                for h in estaduais:
                    try: d = date.fromisoformat(h['data'])
                    except: continue
                    # Evita duplicar se for feriado nacional (hierarquia)
                    if not Feriado.query.filter_by(data=d, tipo='nacional').first():
                        if not Feriado.query.filter_by(data=d, tipo='estadual', uf=uf).first():
                            db.session.add(Feriado(
                                data=d, descricao=h['descricao'], tipo='estadual',
                                uf=uf, fonte='holidays', ativo=True, criado_por_id=u_id,
                            ))
                            criados += 1
                db.session.flush()

            # --- Sincroniza MUNICIPAL uma vez por Município (se tiver token) ---
            if ibge and ibge not in vistos_mun and token:
                vistos_mun.add(ibge)
                municipais_raw = _calendario_api(ano, uf, cidade, token, ibge)
                for item in municipais_raw:
                    try:
                        ds = item['data']
                        if '/' in ds:
                            dd, mm, yy = ds.split('/')
                            d = date(int(yy), int(mm), int(dd))
                        else: d = date.fromisoformat(ds)
                    except: continue

                    tipo = item.get('tipo', 'municipal')
                    if tipo == 'nacional': continue
                    if tipo == 'estadual':
                        # Respeita hierarquia estadual também
                        if not Feriado.query.filter_by(data=d, tipo='nacional').first():
                            if not Feriado.query.filter_by(data=d, tipo='estadual', uf=uf).first():
                                db.session.add(Feriado(
                                    data=d, descricao=item['descricao'], tipo='estadual',
                                    uf=uf, fonte='calendario', ativo=True, criado_por_id=u_id,
                                ))
                                criados += 1
                        continue
                    
                    # Municipal puro
                    if not Feriado.query.filter(
                        Feriado.data == d,
                        or_(
                            Feriado.tipo == 'nacional',
                            and_(Feriado.tipo == 'estadual', Feriado.uf == uf),
                            and_(Feriado.tipo == 'municipal', Feriado.cidade_ibge == ibge)
                        )
                    ).first():
                        db.session.add(Feriado(
                            data=d, descricao=item['descricao'], tipo='municipal',
                            uf=uf, cidade_ibge=ibge, fonte='calendario',
                            ativo=True, criado_por_id=u_id,
                        ))
                        criados += 1
                db.session.flush()

        db.session.commit()
        clear_cache()
    except Exception as e:
        db.session.rollback()
        import traceback
        err_msg = f'ERRO em sincronizar_feriados: {str(e)}\n{traceback.format_exc()}'
        print(err_msg)
        if current_app:
            current_app.logger.error(err_msg)
        return {'criados': 0, 'avisos': [f'Erro crítico na sincronização: {str(e)}']}

    return {'criados': criados, 'avisos': avisos}
