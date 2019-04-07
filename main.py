import os
from app.models import User, Role, Post, Like, Comment, PostType, Message

from flask_script import Manager, Shell
from flask_migrate import Migrate, MigrateCommand
from app import create_app, db
from flask_cors import *

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
    db = db)

manager.add_command('shell', Shell(make_context = make_shell_context))
manager.add_command('db', MigrateCommand)

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