from flask import Flask
from flask_mail import Mail
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event, exc, select
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from config import config
from .socket import socketio

mail = Mail()
db = SQLAlchemy()

@event.listens_for(Engine, 'engine_connect')
def _ping_connection(connection, branch):
  if branch:
    return
  save_should_close_with_result = connection.should_close_with_result
  connection.should_close_with_result = False
  try:
    connection.scalar(select([1]))
  except exc.DBAPIError as err:
    if err.connection_invalidated:
      connection.scalar(select([1]))
    else:
      raise
  finally:
    connection.should_close_with_result = save_should_close_with_result

def create_app(config_name):
  app = Flask(config_name)
  app.config.from_object(config[config_name])
  config[config_name].init_app(app)

  # SHA-256 fields contain arbitrary bytes. Tell PyMySQL to mark binary
  # literals explicitly instead of having MySQL interpret them as UTF-8.
  database_uri = app.config.get('SQLALCHEMY_DATABASE_URI')
  if database_uri:
    database_url = make_url(database_uri)
    if database_url.drivername == 'mysql+pymysql':
      database_url.query.setdefault('binary_prefix', 'true')
      app.config['SQLALCHEMY_DATABASE_URI'] = str(database_url)

  mail.init_app(app)
  db.init_app(app)
  socketio.init_app(app)
  from .api import api as api_blueprint
  app.register_blueprint(api_blueprint, url_prefix='/api')

  return app
