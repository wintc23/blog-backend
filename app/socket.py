from flask_socketio import SocketIO, send, emit, join_room
from flask import request

socketio = SocketIO(path = '/api/socket.io', cors_allowed_origins = '*')
user_map = {}

@socketio.on('bind-user', namespace="/api")
def on_bind (data):
  from .models import User
  try:
    user = User.verify_auth_token(data['token'])
    print(user, 'bind-user')
    if not user.id in user_map:
      user_map[user.id] = request.sid
    else:
      join_room(user_map[user.id])
  except e:
    print('socket error; Invalid token', e)

def send_if_online (user_id, data):
  if user_id in user_map:
    send(data, room = user_map[user_id], namespace="/api")
    return True
  return False

def notify (user_id, data):
  print('notify------', data, user_id)
  msg_data = {
    "type": 'notify',
    "data": data
  }
  return send_if_online(user_id, msg_data)