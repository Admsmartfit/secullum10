"""
Serviço de configurações do sistema.
Lê configurações do banco de dados (tabela Configuracao) com fallback para variáveis de ambiente.
Isso permite editar credenciais pelo painel /config sem precisar editar o .env no servidor.
"""
import os


def get_setting(chave_db: str, env_var: str, default: str = '') -> str:
    """Retorna configuração: DB (Configuracao) > variável de ambiente > default."""
    try:
        from models import Configuracao
        row = Configuracao.query.filter_by(chave=chave_db).first()
        if row and row.valor:
            return row.valor
    except Exception:
        pass
    return os.getenv(env_var, default) or default


def get_secullum_api():
    """Retorna instância configurada da SecullumAPI."""
    try:
        from secullum_api import SecullumAPI
    except ModuleNotFoundError:
        import sys, os
        # Adiciona pasta raiz ao sys.path para background jobs / schedulers
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from secullum_api import SecullumAPI
        
    return SecullumAPI(
        get_setting('secullum_email',    'SECULLUM_EMAIL',    ''),
        get_setting('secullum_password', 'SECULLUM_PASSWORD', ''),
        get_setting('secullum_banco',    'SECULLUM_BANCO',    ''),
    )


def get_megaapi_config() -> dict:
    """Retorna configurações da MegaAPI."""
    return {
        'host':     get_setting('megaapi_host',     'MEGAAPI_HOST',     'apistart01.megaapi.com.br'),
        'instance': get_setting('megaapi_instance', 'MEGAAPI_INSTANCE', ''),
        'token':    get_setting('megaapi_token',    'MEGAAPI_TOKEN',    ''),
        'secret':   get_setting('megaapi_secret',   'MEGAAPI_SECRET',   ''),
    }


def get_gestor_celular() -> str:
    return get_setting('gestor_celular', 'GESTOR_CELULAR', '')
