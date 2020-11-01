import os
from app.models import User, Role, Post, Like, Comment, PostType, Message, Tag, Topic
from app.socket import socketio
from flask_script import Manager, Shell
from flask_migrate import Migrate, MigrateCommand
from app import create_app, db
from flask_cors import *
from app.email import send_email
from app.baidu import auto_push
from app.api.users import save_file, save_all_user_avatar
from app.algolia import save_all_posts, delete_all_posts

app = create_app(os.getenv('FLASK_CONFIG') or 'default')
CORS(app, supports_credentials=True)

manager = Manager(app)
migrate = Migrate(app, db)

def make_shell_context():
  return dict(
    User = User,
    Role = Role,
    Post = Post,
    Like = Like,
    Comment = Comment,
    PostType = PostType,
    app = app,
    Message = Message,
    Tag = Tag,
    Topic = Topic,
    send_email = send_email,
    save_file = save_file,
    auto_push = auto_push,
    save_all_posts = save_all_posts,
    delete_all_posts = delete_all_posts,
    save_all_user_avatar = save_all_user_avatar,
    db = db)

manager.add_command('shell', Shell(make_context = make_shell_context))
manager.add_command('db', MigrateCommand)
manager.add_command('run', socketio.run(app=app, host='0.0.0.0', port=5000))

@manager.command
def init_db():
  """ init test data """
  Role.insert_roles()
  User.generate_fake()
  PostType.insert_types()
  Post.generate_fake()
  Comment.generate_fake()
  Like.generate_fake()
  Message.generate_fake()

if __name__ == '__main__':
  manager.run()