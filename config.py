import os
basedir = os.path.abspath(os.path.dirname(__file__))

class Config:
  SECRET_KEY = os.environ.get('SECRET_KEY') or 'abc+1s'
  SQLALCHEMY_TRACK_MODIFICATIONS = False
  MAIL_SERVER = 'smtp.qq.com'
  MAIL_PORT = 465
  MAIL_USE_SSL = True
  MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
  MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
  MAIL_SENDER = os.environ.get('MAIL_SENDER')
  MAIL_SUBJECT_PREFIX = os.environ.get('MAIL_SUBJECT_PREFIX')
  FLASK_ADMIN = os.environ.get('FLASK_ADMIN')
  FLASK_QQ_SECRET = os.environ.get('FLASK_QQ_SECRET')
  FLASK_QQ_CLIENT_ID = os.environ.get('FLASK_QQ_CLIENT_ID')
  FLASK_POSTS_PER_PAGE = int(os.environ.get('FLASK_POSTS_PER_PAGE'))
  FLASK_BBS_PER_PAGE = int(os.environ.get('FLASK_BBS_PER_PAGE'))
  QI_NIU_ACCESS_KEY = os.environ.get('QI_NIU_ACCESS_KEY')
  QI_NIU_SECRET_KEY = os.environ.get('QI_NIU_SECRET_KEY')
  ENV = os.environ.get('FLASK_CONFIG') or 'default'
  ALGOLIA_APP_ID = os.environ.get('ALGOLIA_APP_ID')
  ALGOLIA_ADMIN_API_KEY = os.environ.get('ALGOLIA_ADMIN_API_KEY')
  SQLALCHEMY_POOL_RECYCLE = 300

  @staticmethod
  def init_app(app):
    pass

class DevelopmentConfig(Config):
  DEBUG = True
  SQLALCHEMY_DATABASE_URI = os.environ.get('DEV_DATABASE_URL')
  QI_NIU_BUCKET = os.environ.get('DEV_QI_NIU_BUCKET')
  QI_NIU_LINK_URL = os.environ.get('DEV_QI_NIU_LINK_URL')
  GIT_BACKUP_DIR = os.environ.get('DEV_GIT_BACKUP_DIR')
  DOMAIN = os.environ.get('DEV_DOMAIN')
  FLASK_GITHUB_SECRET = os.environ.get('DEV_FLASK_GITHUB_SECRET')
  FLASK_GITHUB_CLIENT_ID = os.environ.get('DEV_FLASK_GITHUB_CLIENT_ID')
  ALGOLIA_INDEX = os.environ.get('DEV_ALGOLIA_INDEX')

class TestingConfig(Config):
  TESTING = True
  SQLALCHEMY_DATABASE_URI = os.environ.get('TEST_DATABASE_URL')
  QI_NIU_BUCKET = os.environ.get('DEV_QI_NIU_BUCKET')
  QI_NIU_LINK_URL = os.environ.get('DEV_QI_NIU_LINK_URL')
  GIT_BACKUP_DIR = os.environ.get('DEV_GIT_BACKUP_DIR')
  DOMAIN = os.environ.get('DEV_DOMAIN')
  FLASK_GITHUB_SECRET = os.environ.get('DEV_FLASK_GITHUB_SECRET')
  FLASK_GITHUB_CLIENT_ID = os.environ.get('DEV_FLASK_GITHUB_CLIENT_ID')
  ALGOLIA_INDEX = os.environ.get('DEV_ALGOLIA_INDEX')

class ProductionConfig(Config):
  DEBUG = False
  SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
  QI_NIU_BUCKET = os.environ.get('QI_NIU_BUCKET')
  QI_NIU_LINK_URL = os.environ.get('QI_NIU_LINK_URL')
  GIT_BACKUP_DIR = os.environ.get('GIT_BACKUP_DIR')
  DOMAIN = os.environ.get('DOMAIN')
  FLASK_GITHUB_SECRET = os.environ.get('FLASK_GITHUB_SECRET')
  FLASK_GITHUB_CLIENT_ID = os.environ.get('FLASK_GITHUB_CLIENT_ID')
  ALGOLIA_INDEX = os.environ.get('ALGOLIA_INDEX')

config = {
  'development': DevelopmentConfig,
  'testing': TestingConfig,
  'production': ProductionConfig,
  'default': DevelopmentConfig
}
