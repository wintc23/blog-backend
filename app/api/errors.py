from flask import jsonify
from . import api

def bad_request(message, notify = False):
  response = jsonify({'error': 'bad request', 'message': message, 'notify': notify})
  response.status_code = 400
  return response

def unauthorized(message, notify = False):
  response = jsonify({'error': 'unauthorized', 'message': message, 'notify': notify})
  response.status_code = 401
  return response


def forbidden(message, notify = False):
  response = jsonify({'error': 'forbidden', 'message': message, 'notify': notify})
  response.status_code = 403
  return response

def not_found(message, notify = False):
  response = jsonify({'error': 'not found', 'message': message, 'notify': notify})
  response.status_code = 404
  return response

def server_error(message, notify = False):
  response = jsonify({'error': 'server error', 'message': message, 'notify': notify})
  response.status_code = 500
  return response