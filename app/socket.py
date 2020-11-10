from flask_socketio import SocketIO, send, emit, join_room, leave_room
from flask import request

socketio = SocketIO(cors_allowed_origins = '*')
user_map = {}
sid_map = {}

@socketio.on('bind-user', namespace="/api")
def on_bind (data):
  from .models import User
  try:
    user = User.verify_auth_token(data['token'])
    if not user.id in user_map:
      user_map[user.id] = request.sid
    else:
      join_room(user_map[user.id])
    sid_map[request.sid] = user_map[user.id]
  except:
    print('socket error; Invalid token')

@socketio.on('disconnect', namespace="/api")
def on_disconnect ():
  sid = request.sid
  if sid in sid_map:
    room_sid = sid_map.pop(sid)
    leave_room(room_sid)
    if not room_sid in sid_map.values():
      socketio.close_room(room_sid)
      if room_sid in user_map:
        user_map.pop(room_sid)
        print(sid_map, user_map)

def send_if_online (user_id, data):
  if user_id in user_map:
    send(data, room = user_map[user_id], namespace="/api")
    return True
  return False

def notify (user_id, data):
  msg_data = {
    "type": 'notify',
    "data": data
  }
  return send_if_online(user_id, msg_data)