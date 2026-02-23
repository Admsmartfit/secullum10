import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev_key_secullum_123')
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/secullum10')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }

    # Redis / Celery
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    CELERY_RESULT_BACKEND = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

    # Secullum API
    SECULLUM_EMAIL = os.getenv('SECULLUM_EMAIL')
    SECULLUM_PASSWORD = os.getenv('SECULLUM_PASSWORD')
    SECULLUM_BANCO = os.getenv('SECULLUM_BANCO')

    # Flask-Mail (Etapa 5 – alertas de documentos)
    MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.getenv('MAIL_PORT', 587))
    MAIL_USE_TLS = os.getenv('MAIL_USE_TLS', 'true').lower() == 'true'
    MAIL_USERNAME = os.getenv('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER', 'noreply@secullum10.com')
    RH_EMAIL = os.getenv('RH_EMAIL', '')

    # Upload de prontuário
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads', 'prontuario')
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB
