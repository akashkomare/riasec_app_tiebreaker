import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Base configuration"""
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev_secret_key_change_in_production')
    DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'
    
    # Tie-breaker configuration
    TIE_BREAKER_DELTA = int(os.environ.get('TIE_BREAKER_DELTA', 2))
    
    # Session configuration
    SESSION_PERMANENT = False
    SESSION_TYPE = 'filesystem'

class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 1800

class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}