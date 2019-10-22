
from flask import current_app
from app import db
from datetime import datetime
import time
from itsdangerous import TimedJSONWebSignatureSerializer as Serializer


class Permission:
  FOLLOW = 1
  COMMENT = 2
  WRITE = 4
  MODERATE = 8
  ADMIN = 16

class Role(db.Model):
  __tablename__ = 'roles'
  id = db.Column(db.Integer, primary_key = True)
  name = db.Column(db.String(64), unique = True)
  permissions = db.Column(db.Integer)
  default = db.Column(db.Boolean, default = False, index = True)
  users = db.relationship('User', backref='role')

  def __init__(self, **kwargs):
    super(Role, self).__init__(**kwargs)
    if self.permissions is None:
      self.permissions = 0

  @staticmethod
  def insert_roles():
    roles = {
      'User': [Permission.FOLLOW, Permission.COMMENT],
      'Moderator': [Permission.FOLLOW, Permission.COMMENT, Permission.WRITE, Permission.MODERATE],
      'Administrator': [Permission.FOLLOW, Permission.COMMENT, Permission.WRITE, Permission.MODERATE, Permission.ADMIN]
    }
    default_role = 'User'
    for r in roles:
      role = Role.query.filter_by(name = r).first()
      if role is None:
        role = Role(name = r)
      role.reset_permissions()
      for perm in roles[r]:
        role.add_permission(perm)
      role.default = (role.name == default_role)
      db.session.add(role)
    db.session.commit()
  
  def has_permission(self, permission):
    return self.permissions & permission == permission

  def add_permission(self, permission):
    if not self.has_permission(permission):
      self.permissions += permission
  
  def remove_permission(self, permission):
    if self.has_permission(permission):
      self.permissions -= permission
    
  def reset_permissions(self):
    self.permissions = 0 

  def __repr__(self):
    return "<Role %r>" % self.name

class User(db.Model):
  __tablename__ = 'users'
  id = db.Column(db.Integer, primary_key = True)
  id_string = db.Column(db.String(32), unique = True, index = True)
  email = db.Column(db.String(64), unique = True, index = True)
  username = db.Column(db.String(64), index = True)
  avatar = db.Column(db.String(64), default="default")
  role_id = db.Column(db.Integer, db.ForeignKey('roles.id'))
  member_since = db.Column(db.DateTime(), default = datetime.utcnow)
  last_seen = db.Column(db.DateTime(), default = datetime.utcnow)
  about_me = db.Column(db.Text())

  posts = db.relationship('Post', backref = 'author', lazy = 'dynamic')
  comments = db.relationship('Comment', backref = 'author', lazy = 'dynamic')
  likes = db.relationship('Like', backref = 'author', lazy = 'dynamic')
  messages = db.relationship('Message', backref = 'author', lazy = 'dynamic')
 
  def __init__(self, **kwargs):
    super(User, self).__init__(**kwargs)
    if not self.role:
      self.role = Role.query.filter_by(default = True).first()

  @staticmethod
  def generate_fake(count = 10):
    from random import seed, randint
    import forgery_py

    seed()
    for i in range(count):
      u = User(username = forgery_py.internet.user_name(),
        about_me=forgery_py.lorem_ipsum.sentence(),
        member_since=forgery_py.date.date(True))
      db.session.add(u)
      try:
        db.session.commit()
        print('auto create user %s done' % (i + 1))
      except:
        db.session.rollback()

  def generate_auth_token(self, expiration):
    print(expiration)
    s = Serializer(current_app.config['SECRET_KEY'], expires_in = expiration)
    return s.dumps({'id': self.id}).decode('utf-8')

  @staticmethod
  def verify_auth_token(token):
    s = Serializer(current_app.config['SECRET_KEY'])
    try:
      data = s.loads(token.encode('utf-8'))
    except:
      return None
    print(data)
    return User.query.get(data['id'])
  
  def can(self, permission):
    return self.role is not None and self.role.has_permission(permission)
  
  def is_administrator(self):
    return self.can(Permission.ADMIN)

  def to_json(self):
    return {
      'id': self.id,
      'username': self.username,
      'avatar': self.avatar,
      'about_me': self.about_me,
      'admin': self.is_administrator(),
    }

  def get_detail(self):
    return {
      'id': self.id,
      'username': self.username,
      'avatar': self.avatar,
      'about_me': self.about_me,
      'admin': self.is_administrator(),
    }
  

class PostType(db.Model):
  __tablename__ = 'post_type'
  id = db.Column(db.Integer, primary_key = True)
  name = db.Column(db.String(64))
  alias = db.Column(db.String(32), index = True)
  default = db.Column(db.Boolean, default = False, index = True)
  special = db.Column(db.SmallInteger, default = 0)
  sort = db.Column(db.Integer)
  posts = db.relationship('Post', backref='type', lazy='dynamic')

  def to_json(self):
    return {
      'id': self.id,
      'name': self.name,
      'alias': self.alias,
      'default': self.default,
      'special': self.special,
      'sort': self.sort
    }

  @staticmethod
  def insert_types():
    types = {
      'blog': {
        'name': '技术博客',
        'sort': 1,
        'special': 0,
        'default': True
      },
      'note': {
        'name': '读书笔记',
        'sort': 2,
        'special': 0,
        'default': False
      },
      'essay': {
        'name': '随笔',
        'sort': 3,
        'special': 0,
        'default': False
      },
      'about_me': {
        'name': '关于',
        'sort': 4,
        'special': 1,
        'default': False
      }
    }
    for alias in types:
      data = types[alias]
      post_type = PostType.query.filter_by(alias = alias).first()
      if not post_type:
        post_type = PostType(alias = alias)
      post_type.name = data['name']
      post_type.sort = data['sort']
      post_type.special = data['special']
      post_type.default = data['default']
      db.session.add(post_type)
    db.session.commit()

class Post(db.Model):
  __tablename__ = 'posts'
  id = db.Column(db.Integer, primary_key = True)
  title = db.Column(db.Text)
  body = db.Column(db.Text)
  body_html = db.Column(db.Text)
  hide = db.Column(db.Boolean, default = False)
  secret_code = db.Column(db.Text, default = '')
  abstract = db.Column(db.Text)
  abstract_image = db.Column(db.Text())
  timestamp = db.Column(db.DateTime, index = True, default = datetime.utcnow)
  author_id = db.Column(db.Integer, db.ForeignKey('users.id'))
  type_id = db.Column(db.Integer, db.ForeignKey('post_type.id'))
  comments = db.relationship('Comment', backref = 'post', lazy = 'dynamic')
  read_times = db.Column(db.Integer, default = 0)
  likes = db.relationship('Like', backref = 'post', lazy = 'dynamic')

  @staticmethod
  def generate_fake(count = 100):
    from random import seed, randint
    import forgery_py

    seed()
    user_count = User.query.count()
    type_count = PostType.query.count()
    for i in range(count):
      u = User.query.offset(randint(0, user_count - 1)).first()
      post_type = PostType.query.offset(randint(0, type_count - 1)).first()
      p = Post(body = forgery_py.lorem_ipsum.sentences(randint(50, 100)),
        read_times = randint(0, 100),
        title = forgery_py.lorem_ipsum.sentences(1),
        abstract = forgery_py.lorem_ipsum.sentences(randint(5, 20)),
        timestamp = forgery_py.date.date(True),
        type = post_type,
        author = u)
      db.session.add(p)
      try:
        db.session.commit()
        print('auto create post %s done' % (i + 1))
      except:
        db.session.rollback()
  
  def add_read(self):
    self.read_times += 1

  def is_about_me(self):
    return self.type.alias == 'about_me'

  def to_json(self):
    json_post = {
      'id': self.id,
      'author_id': self.author.id,
      'title': self.title,
      'abstract': self.abstract,
      'hide': self.hide,
      'body': self.body,
      'body_html': self.body_html or self.body,
      'timestamp': time.mktime(self.timestamp.timetuple()),
      'read_times': self.read_times,
      'likes': self.likes.count(),
      'type': self.type_id,
      'abstract_image': self.abstract_image
    }
    return json_post
  
  def abstract_json(self):
    json_post = {
      'id': self.id,
      'author_id': self.author.id,
      'title': self.title,
      'timestamp': time.mktime(self.timestamp.timetuple()),
      'abstract': self.abstract,
      'read_times': self.read_times,
      'likes': self.likes.count(),
      'comment_times': self.comments.count(),
      'type': self.type_id,
      'abstract_image': self.abstract_image
    }
    return json_post


class Comment(db.Model):
  __tablename__ = 'comments'
  id = db.Column(db.Integer, primary_key = True)
  body = db.Column(db.Text)
  post_id = db.Column(db.Integer, db.ForeignKey('posts.id'))
  author_id = db.Column(db.Integer, db.ForeignKey('users.id'))
  comments = db.relationship('Comment', backref = db.backref('response', remote_side=[id]), lazy = 'dynamic')
  response_id = db.Column(db.Integer, db.ForeignKey('comments.id'))
  timestamp = db.Column(db.DateTime, index = True, default = datetime.utcnow)
  hide = db.Column(db.Boolean, default = True)

  def to_json(self):
    return {
      'id': self.id,
      'body': self.body,
      'author_id': self.author_id,
      'post_id': self.post_id,
      'response_id': self.response_id,
      'timestamp': time.mktime(self.timestamp.timetuple()),
      'hide': self.hide
    }

  @staticmethod
  def generate_fake(count=100):
    from random import seed, randint
    import forgery_py
    
    seed()
    user_count = User.query.count()
    post_count = Post.query.count()
    for i in range(count):
      u = User.query.offset(randint(0, user_count - 1)).first()
      p = Post.query.offset(randint(0, post_count - 1)).first()
      comment = Comment(body=forgery_py.lorem_ipsum.sentences(randint(2,5)),
        timestamp=forgery_py.date.date(True),
        author=u,
        post=p
      )
      db.session.add(comment)
      try:
        db.session.commit()
        print('auto create comment %s done' % (i + 1))
      except:
        db.session.rollback()

class Like(db.Model):
  __tabname__ = 'likes'
  id = db.Column(db.Integer, primary_key = True)
  post_id = db.Column(db.Integer, db.ForeignKey('posts.id'))
  author_id = db.Column(db.Integer, db.ForeignKey('users.id'))
  timestamp = db.Column(db.DateTime, index = True, default = datetime.utcnow)

  @staticmethod
  def generate_fake(count=100):
    from random import seed, randint
    import forgery_py
    
    seed()
    user_count = User.query.count()
    post_count = Post.query.count()
    for i in range(count):
      u = User.query.offset(randint(0, user_count - 1)).first()
      p = Post.query.offset(randint(0, post_count - 1)).first()
      like = Like(timestamp=forgery_py.date.date(True), author=u, post=p)
      db.session.add(like)
      try:
        db.session.commit()
        print('auto create like %s done' % ( i + 1 ))
      except:
        db.session.rollback()

class Message(db.Model):
  __tablename__ = 'messages'
  id = db.Column(db.Integer, primary_key = True)
  body = db.Column(db.Text)
  author_id = db.Column(db.Integer, db.ForeignKey('users.id'))
  comments = db.relationship('Message', backref=db.backref('root_response', remote_side=[id]), lazy = 'dynamic')
  response_id = db.Column(db.Integer)
  root_response_id = db.Column(db.Integer, db.ForeignKey('messages.id'))
  hide = db.Column(db.Boolean, default = True)
  timestamp = db.Column(db.DateTime, index = True, default = datetime.utcnow)

  def to_json(self):
    return {
      'id': self.id,
      'body': self.body,
      'author_id': self.author_id,
      'timestamp': time.mktime(self.timestamp.timetuple()),
      'response_id': self.response_id,
      'root_response_id': self.root_response_id,
      'hide': self.hide
    }

  @staticmethod
  def generate_fake(count = 100):
    from random import seed, randint
    import forgery_py

    seed()
    user_count = User.query.count()
    for i in range(count):
      params = {}
      u = User.query.offset(randint(0, user_count - 1)).first()
      params['author'] = u
      if i % 3 == 0:
        message_count = Message.query.count()
        if (message_count > 1):
          message = Message.query.offset(randint(0, message_count - 1)).first()
          params['response_id'] = message.id
          if (message.response_id):
            params['root_response_id'] = message.root_response_id
          else:
            params['root_response_id'] = message.id
          params['body'] = forgery_py.lorem_ipsum.sentences(randint(10, 20))
      msg = Message(**params)
      db.session.add(msg)
      try:
        db.session.commit()
        print('auto create msg %s done' % (i +  1))
      except:
        db.session.rollback()

# class Visit(db.Model):
#   __tablename__ = 'site_info'
#   timestamp = db.Column(db.DateTime, index = True, default = datetime.utcnow)
#   # ip = db.Column()
#   click_times = db.Column(db.Integer, default = 0)
  
#   def to_json(self):
#     return {
#       'start_time': time.mktime(self.start_time.timetuple()),
#       'click_times': self.click_times,
#       'post_num': Post.query.count()
#     }