
from flask import current_app
from app import db
from datetime import datetime, timedelta, timezone
import json
import time
from itsdangerous import TimedJSONWebSignatureSerializer as Serializer
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from uuid import uuid4

class Permission:
  FOLLOW = 1
  COMMENT = 2
  WRITE = 4
  MODERATE = 8
  ADMIN = 16

post_tag_relations = db.Table('post_tag_relations',
  db.Column('post_id', db.Integer, db.ForeignKey('tags.id')),
  db.Column('tag_id', db.Integer, db.ForeignKey('posts.id'))
)

class Role(db.Model):
  __tablename__ = 'roles'
  id = db.Column(db.Integer, primary_key = True)
  name = db.Column(db.String(64), unique = True)
  permissions = db.Column(db.Integer)
  default = db.Column(db.Boolean, default = False, index = True)
  users = db.relationship('User', backref='role', lazy = 'dynamic')

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
  id_string = db.Column(db.String(64), unique = True, index = True)
  email = db.Column(db.String(64), unique = True, index = True)
  email_verified_at = db.Column(db.DateTime)
  email_version = db.Column(db.Integer, nullable=False, default=0, server_default='0')
  auth_version = db.Column(db.Integer, nullable=False, default=0, server_default='0')
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
  stat_events = db.relationship('StatEvent', backref = 'author', lazy = 'dynamic')

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
    s = Serializer(current_app.config['SECRET_KEY'], expires_in = expiration)
    return s.dumps({'id': self.id, 'version': self.auth_version or 0}).decode('utf-8')

  @staticmethod
  def verify_auth_token(token):
    s = Serializer(current_app.config['SECRET_KEY'])
    try:
      data = s.loads(token.encode('utf-8'))
    except:
      return None
    if not isinstance(data, dict) or type(data.get('id')) is not int or data.get('type'):
      return None
    user = User.query.get(data['id'])
    if user is None or data.get('version', 0) != (user.auth_version or 0):
      return None
    return user
  
  def can(self, permission):
    if self.is_guest and permission & ~(Permission.FOLLOW | Permission.COMMENT):
      return False
    return self.role is not None and self.role.has_permission(permission)

  @property
  def is_guest(self):
    return bool(self.id_string and self.id_string.startswith('guest:'))
  
  def is_administrator(self):
    return self.can(Permission.ADMIN)

  def avatar_url(self):
    if self.avatar and self.avatar.startswith('generated:'):
      from .guest import avatar_url
      return avatar_url(self.avatar[len('generated:'):])
    if self.is_guest:
      from .guest import avatar_url
      return avatar_url(self.avatar)
    return current_app.config['QI_NIU_LINK_URL'] + '/' + self.avatar

  def to_json(self, include_email=False):
    result = {
      'id': self.id,
      'username': self.username,
      'is_guest': self.is_guest,
      'avatar': self.avatar_url(),
      'about_me': self.about_me,
      'admin': self.is_administrator()
    }
    if include_email:
      result['email'] = self.email
    return result

  def get_detail(self):
    return self.to_json(include_email=True)


class GuestRateLimit(db.Model):
  """Shared, transactional limits across application workers; no raw IPs."""
  __tablename__ = 'guest_rate_limits'
  key = db.Column(db.String(64), primary_key=True)
  hits = db.Column(db.Integer, nullable=False, default=0)
  expires_at = db.Column(db.BigInteger, nullable=False, index=True)


class EmailLoginChallenge(db.Model):
  __tablename__ = 'email_login_challenges'
  id = db.Column(db.String(64), primary_key=True)
  email = db.Column(db.String(64), nullable=False, index=True)
  code_digest = db.Column(db.String(64), nullable=False)
  user_id = db.Column(db.Integer, nullable=True)
  email_version = db.Column(db.Integer, nullable=False, default=0)
  expires_at = db.Column(db.BigInteger, nullable=False, index=True)
  attempts = db.Column(db.Integer, nullable=False, default=0)
  ready = db.Column(db.Boolean, nullable=False, default=False)
  consumed_at = db.Column(db.BigInteger, nullable=True)


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

class PersonalProfile(db.Model):
  """The site's independently managed homepage profile (single row, id=1)."""
  __tablename__ = 'personal_profiles'
  id = db.Column(db.Integer, primary_key=True, autoincrement=False)
  site_name = db.Column(db.String(128), nullable=False, default='', server_default='')
  display_name = db.Column(db.String(128), nullable=False, default='')
  avatar_url = db.Column(db.Text, nullable=False, default='')
  tagline = db.Column(db.String(255), nullable=False, default='')
  introduction = db.Column(db.String(500), nullable=False, default='')
  bio = db.Column(db.Text, nullable=False, default='')
  portfolio_introduction = db.Column(db.String(500), nullable=False, default='', server_default='')
  links_json = db.Column(db.Text, nullable=False, default='[]')
  moments_json = db.Column(db.Text, nullable=True, default='[]')
  contact_email = db.Column(db.String(254), nullable=False, default='', server_default='')
  wechat_id = db.Column(db.String(128), nullable=False, default='', server_default='')
  wechat_qr_url = db.Column(db.String(2048), nullable=False, default='', server_default='')
  contact_note = db.Column(db.String(500), nullable=False, default='', server_default='')
  updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

  def to_json(self):
    return {
      'id': 1,
      'site_name': self.site_name or '',
      'display_name': self.display_name or '',
      'avatar_url': self.avatar_url or '',
      'tagline': self.tagline or '',
      'introduction': self.introduction or '',
      'bio': self.bio or '',
      'portfolio_introduction': self.portfolio_introduction or '',
      'links': json.loads(self.links_json or '[]'),
      'moments': json.loads(self.moments_json or '[]'),
      'contact_email': self.contact_email or '',
      'wechat_id': self.wechat_id or '',
      'wechat_qr_url': self.wechat_qr_url or '',
      'contact_note': self.contact_note or '',
    }


class LifeMoment(db.Model):
  __tablename__ = 'life_moments'
  id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
  date = db.Column(db.Date, nullable=False)
  category = db.Column(db.String(16), nullable=False)
  text = db.Column(db.Text, nullable=False)
  occurred_at = db.Column(db.DateTime, nullable=True)
  images_json = db.Column(db.Text, nullable=True)
  image_url = db.Column(db.Text, nullable=False)
  image_alt = db.Column(db.String(120), nullable=False, default='')
  location = db.Column(db.String(60), nullable=False, default='')
  created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
  updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
  __table_args__ = (db.Index('ix_life_moments_chronology', 'date', 'occurred_at', 'created_at', 'id'),)

  def pictures(self):
    return json.loads(self.images_json) if self.images_json is not None else ([{'url': self.image_url, 'description': self.image_alt or ''}] if self.image_url else [])

  def to_json(self, include_hidden=False):
    images = [picture for picture in self.pictures() if include_hidden or picture.get('is_public', True)]
    cover = images[0] if images else {}
    return {
      'id': self.id, 'date': self.date.isoformat(), 'category': self.category,
      'text': self.text, 'image_url': cover.get('url', ''),
      'image_alt': cover.get('description', ''), 'location': self.location,
      'occurred_at': self.occurred_at.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8))).isoformat() if self.occurred_at else None,
      'images': images,
    }


class Post(db.Model):
  __tablename__ = 'posts'
  id = db.Column(db.Integer, primary_key = True)
  title = db.Column(db.Text)
  keywords = db.Column(db.String(64)) # 关键字
  description = db.Column(db.String(128)) # 描述
  # MEDIUMTEXT (16 MB) — plain TEXT's 65 KB ceiling isn't enough once
  # a post is saved through BlockNote: the full-HTML exporter wraps
  # every paragraph / heading / list item in `.bn-block-outer >
  # .bn-block > .bn-block-content` markup, inflating long articles
  # past the limit (confirmed: a 40 KB TinyMCE post ballooned to
  # ~68 KB after `blocksToFullHTML` and MySQL rejected the UPDATE).
  body_html = db.Column(MEDIUMTEXT)
  hide = db.Column(db.Boolean, default = False)
  secret_code = db.Column(db.Text, default = '')
  abstract = db.Column(db.Text)
  abstract_image = db.Column(db.Text())
  read_times = db.Column(db.Integer, default = 0)
  timestamp = db.Column(db.DateTime, index = True, default = datetime.utcnow)
  author_id = db.Column(db.Integer, db.ForeignKey('users.id'))
  type_id = db.Column(db.Integer, db.ForeignKey('post_type.id'))
  topic_id = db.Column(db.Integer, db.ForeignKey('topics.id'))
  comments = db.relationship('Comment', backref = 'post', lazy = 'dynamic')
  likes = db.relationship('Like', backref = 'post', lazy = 'dynamic')
  tags = db.relationship('Tag',
    secondary = post_tag_relations,
    backref = db.backref('posts', lazy = 'dynamic'),
    lazy='dynamic')

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
      p = Post(body_html = forgery_py.lorem_ipsum.sentences(randint(50, 100)),
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
      'body_html': self.body_html,
      'timestamp': time.mktime(self.timestamp.timetuple()),
      'read_times': self.read_times,
      'likes': self.likes.count(),
      'type_id': self.type_id,
      'abstract_image': self.abstract_image,
      'keywords': self.keywords,
      'topic_id': self.topic_id,
      'description': self.description
    }
    return json_post
  
  def abstract_json(self):
    json_post = {
      'id': self.id,
      'author_id': self.author.id,
      'title': self.title,
      'hide': self.hide,
      'timestamp': time.mktime(self.timestamp.timetuple()),
      'abstract': self.abstract,
      'read_times': self.read_times,
      'likes': self.likes.count(),
      'comment_times': self.comments.count(),
      'type': self.type_id,
      'abstract_image': self.abstract_image,
      'topic': self.topic_id
    }
    return json_post


class Comment(db.Model):
  __tablename__ = 'comments'
  id = db.Column(db.Integer, primary_key = True)
  body = db.Column(db.Text)
  post_id = db.Column(db.Integer, db.ForeignKey('posts.id'))
  digest_id = db.Column(db.Integer, db.ForeignKey('ai_digests.id'), nullable=True, index=True)
  author_id = db.Column(db.Integer, db.ForeignKey('users.id'))
  comments = db.relationship('Comment', backref = db.backref('response', remote_side=[id]), lazy = 'dynamic')
  response_id = db.Column(db.Integer, db.ForeignKey('comments.id'))
  timestamp = db.Column(db.DateTime, index = True, default = datetime.utcnow)
  hide = db.Column(db.Boolean, default = True)

  def to_json(self):
    from .digest_models import AiDigest
    target = AiDigest.query.get(self.digest_id) if self.digest_id else self.post
    return {
      'id': self.id,
      'body': self.body,
      'author_id': self.author_id,
      'response_id': self.response_id,
      'timestamp': time.mktime(self.timestamp.timetuple()),
      'hide': self.hide,
      'post_id': self.post_id,
      'digest_id': self.digest_id,
      'post_title': target.title if target else '',
      'target_url': '/ai-news/{}'.format(self.digest_id) if self.digest_id else '/article/{}'.format(self.post_id)
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

  def to_json(self):
    print(Post, self.post_id)
    post = Post.query.get(self.post_id or -1)

    return {
      'id': self.id,
      "author_id": self.author_id,
      'post_id': self.post_id,
      'post_title': post.title if post else '',
      "timestamp": time.mktime(self.timestamp.timetuple())
    }

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

class Tag(db.Model):
  __tablename__ = 'tags'
  id = db.Column(db.Integer, primary_key = True)
  title = db.Column(db.String(64))


  def to_json(self):
    return {
      'id': self.id,
      'title': self.title,
      'post_count': self.posts.filter_by(hide = False).count()
    }

class Topic(db.Model):
  __tablename__ = 'topics'
  id = db.Column(db.Integer, primary_key = True)
  title = db.Column(db.Text)
  posts = db.relationship('Post', backref = 'topic', lazy = 'dynamic')

  def to_json(self):
    return {
      'id': self.id,
      'title': self.title,
      'post_count': self.posts.count()
    }

class FriendLink(db.Model):
  __tablename__ = 'friend_link'
  id = db.Column(db.Integer, primary_key = True)
  title = db.Column(db.String(64))
  link = db.Column(db.String(64))
  motto = db.Column(db.Text)
  logo = db.Column(db.Text(65535))
  hide = db.Column(db.Boolean, default = True)
  timestamp = db.Column(db.DateTime, default = datetime.utcnow)
  author_id = db.Column(db.Integer, db.ForeignKey('users.id'))

  def to_json(self):
    return {
      'id': self.id,
      'title': self.title,
      'link': self.link,
      'motto': self.motto,
      'logo': self.logo,
      'hide': self.hide,
      'timestamp': self.timestamp,
      'author_id': self.author_id
    }

  def to_abstract(self):
    return {
      'id': self.id,
      'title': self.title,
      'link': self.link
    }


class Product(db.Model):
  """A portfolio item managed independently from blog posts.

  The repeated presentation fields are stored as JSON text so the current
  MySQL/SQLAlchemy stack does not depend on a native JSON column.  The API
  always exposes them as arrays and validates writes before persisting them.
  """
  __tablename__ = 'products'
  id = db.Column(db.Integer, primary_key = True)
  name = db.Column(db.String(128), nullable = False)
  slug = db.Column(db.String(128), unique = True, index = True, nullable = False)
  tagline = db.Column(db.String(255))
  summary = db.Column(db.Text)
  platform = db.Column(db.String(64))
  version = db.Column(db.String(32))
  status = db.Column(db.String(32), default = 'developing')
  status_label = db.Column(db.String(64), default = '开发中')
  logo_url = db.Column(db.Text)
  cover_url = db.Column(db.Text)
  accent_color = db.Column(db.String(16), default = '#2d8cf0')
  highlights_json = db.Column(db.Text)
  features_json = db.Column(MEDIUMTEXT)
  steps_json = db.Column(MEDIUMTEXT)
  screenshots_json = db.Column(MEDIUMTEXT)
  links_json = db.Column(db.Text)
  story_html = db.Column(MEDIUMTEXT)
  published = db.Column(db.Boolean, default = False, index = True)
  featured = db.Column(db.Boolean, default = False, index = True)
  sort = db.Column(db.Integer, default = 0, index = True)
  created_at = db.Column(db.DateTime, default = datetime.utcnow)
  updated_at = db.Column(db.DateTime, default = datetime.utcnow)
  sections = db.relationship(
    'ProductSection',
    backref = 'product',
    lazy = 'dynamic',
    cascade = 'all, delete-orphan',
    order_by = 'ProductSection.sort'
  )

  def _json_list(self, value):
    if not value:
      return []
    try:
      data = json.loads(value)
      return data if isinstance(data, list) else []
    except:
      return []

  def to_json(self):
    return {
      'id': self.id,
      'name': self.name,
      'slug': self.slug,
      'tagline': self.tagline,
      'summary': self.summary,
      'platform': self.platform,
      'version': self.version,
      'status': self.status,
      'status_label': self.status_label,
      'logo_url': self.logo_url,
      'cover_url': self.cover_url,
      'accent_color': self.accent_color,
      'highlights': self._json_list(self.highlights_json),
      'features': self._json_list(self.features_json),
      'steps': self._json_list(self.steps_json),
      'screenshots': self._json_list(self.screenshots_json),
      'links': self._json_list(self.links_json),
      'story_html': self.story_html,
      'sections': [section.to_json() for section in self.sections.order_by(ProductSection.sort.asc(), ProductSection.id.asc()).all()],
      'published': self.published,
      'featured': self.featured,
      'sort': self.sort,
      'created_at': time.mktime(self.created_at.timetuple()) if self.created_at else None,
      'updated_at': time.mktime(self.updated_at.timetuple()) if self.updated_at else None
    }


class ProductSection(db.Model):
  __tablename__ = 'product_sections'
  id = db.Column(db.Integer, primary_key = True)
  product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable = False, index = True)
  type = db.Column(db.String(32), nullable = False)
  title = db.Column(db.String(128))
  subtitle = db.Column(db.String(128))
  layout = db.Column(db.String(32), default = 'default')
  content_json = db.Column(MEDIUMTEXT)
  visible = db.Column(db.Boolean, default = True)
  sort = db.Column(db.Integer, default = 0, index = True)
  created_at = db.Column(db.DateTime, default = datetime.utcnow)
  updated_at = db.Column(db.DateTime, default = datetime.utcnow)

  def content_data(self):
    if not self.content_json:
      return {}
    try:
      data = json.loads(self.content_json)
      return data if isinstance(data, dict) else {}
    except:
      return {}

  def to_json(self):
    return {
      'id': self.id,
      'product_id': self.product_id,
      'type': self.type,
      'title': self.title,
      'subtitle': self.subtitle,
      'layout': self.layout,
      'content': self.content_data(),
      'visible': self.visible,
      'sort': self.sort,
      'created_at': time.mktime(self.created_at.timetuple()) if self.created_at else None,
      'updated_at': time.mktime(self.updated_at.timetuple()) if self.updated_at else None
    }

class StatEvent(db.Model):
  __tablename__ = 'stat_event'
  id = db.Column(db.Integer, primary_key = True)
  name = db.Column(db.String(32), index = True)
  params = db.Column(db.Text)
  timestamp = db.Column(db.DateTime, default = datetime.utcnow)
  author_id = db.Column(db.Integer, db.ForeignKey('users.id'))
  visitor_id = db.Column(db.String(32), index = True)
  ip = db.Column(db.String(40), index = True)

  def to_json(self):
    return {
      "id": self.id,
      "name": self.name,
      "timestamp": self.timestamp,
      "author_id": self.author_id,
      "visitor_id": self.visitor_id,
      "params": self.params,
      "ip": self.ip,
    }

class AiAccessKey(db.Model):
  __tablename__ = 'ai_access_keys'
  id = db.Column(db.Integer, primary_key = True)
  name = db.Column(db.String(128), nullable = False)
  key_hash = db.Column(db.String(64), unique = True, index = True, nullable = False)
  key_preview = db.Column(db.String(32), nullable = False)
  key_encrypted = db.Column(db.Text)
  enabled = db.Column(db.Boolean, default = True, index = True)
  usage_limit = db.Column(db.Integer)
  usage_count = db.Column(db.Integer, default = 0)
  expires_at = db.Column(db.DateTime)
  created_at = db.Column(db.DateTime, default = datetime.utcnow)
  last_used_at = db.Column(db.DateTime)
  created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
  sessions = db.relationship('AiChatSession', backref = 'access_key', lazy = 'dynamic')

  def is_available(self):
    if not self.enabled:
      return False
    if self.expires_at and self.expires_at < datetime.utcnow():
      return False
    if self.usage_limit is not None and self.usage_count >= self.usage_limit:
      return False
    return True

  def to_json(self):
    return {
      'id': self.id,
      'name': self.name,
      'key_preview': self.key_preview,
      'enabled': self.enabled,
      'usage_limit': self.usage_limit,
      'usage_count': self.usage_count,
      'expires_at': time.mktime(self.expires_at.timetuple()) if self.expires_at else None,
      'created_at': time.mktime(self.created_at.timetuple()) if self.created_at else None,
      'last_used_at': time.mktime(self.last_used_at.timetuple()) if self.last_used_at else None,
      'created_by_id': self.created_by_id
    }

class AiChatSession(db.Model):
  __tablename__ = 'ai_chat_sessions'
  id = db.Column(db.Integer, primary_key = True)
  access_key_id = db.Column(db.Integer, db.ForeignKey('ai_access_keys.id'), nullable = False, index = True)
  title = db.Column(db.String(128), default = '新的会话')
  codex_session_id = db.Column(db.String(128), index = True)
  status = db.Column(db.String(16), default = 'active', index = True)
  pinned = db.Column(db.Boolean, default = False, index = True)
  created_at = db.Column(db.DateTime, default = datetime.utcnow)
  updated_at = db.Column(db.DateTime, default = datetime.utcnow)
  last_message_at = db.Column(db.DateTime)
  messages = db.relationship('AiChatMessage', backref = 'session', lazy = 'dynamic')

  def touch(self):
    now = datetime.utcnow()
    self.updated_at = now
    self.last_message_at = now

  def to_json(self):
    return {
      'id': self.id,
      'title': self.title,
      'codex_session_id': self.codex_session_id,
      'status': self.status,
      'pinned': self.pinned,
      'created_at': time.mktime(self.created_at.timetuple()) if self.created_at else None,
      'updated_at': time.mktime(self.updated_at.timetuple()) if self.updated_at else None,
      'last_message_at': time.mktime(self.last_message_at.timetuple()) if self.last_message_at else None
    }

class AiChatMessage(db.Model):
  __tablename__ = 'ai_chat_messages'
  id = db.Column(db.Integer, primary_key = True)
  session_id = db.Column(db.Integer, db.ForeignKey('ai_chat_sessions.id'), nullable = False, index = True)
  role = db.Column(db.String(16), nullable = False)
  content = db.Column(MEDIUMTEXT)
  content_type = db.Column(db.String(16), default = 'text')
  codex_message_id = db.Column(db.String(128))
  status = db.Column(db.String(16), default = 'completed')
  metadata_json = db.Column(db.Text)
  created_at = db.Column(db.DateTime, default = datetime.utcnow, index = True)
  attachments = db.relationship('AiChatAttachment', backref = 'message', lazy = 'dynamic')

  def metadata_data(self):
    if not self.metadata_json:
      return {}
    try:
      return json.loads(self.metadata_json)
    except:
      return {}

  def to_json(self):
    return {
      'id': self.id,
      'session_id': self.session_id,
      'role': self.role,
      'content': self.content,
      'content_type': self.content_type,
      'codex_message_id': self.codex_message_id,
      'status': self.status,
      'metadata': self.metadata_data(),
      'created_at': time.mktime(self.created_at.timetuple()) if self.created_at else None,
      'attachments': [item.to_json() for item in self.attachments.all()]
    }

class AiChatAttachment(db.Model):
  __tablename__ = 'ai_chat_attachments'
  id = db.Column(db.Integer, primary_key = True)
  message_id = db.Column(db.Integer, db.ForeignKey('ai_chat_messages.id'), nullable = False, index = True)
  file_key = db.Column(db.String(255), nullable = False)
  file_url = db.Column(db.Text)
  mime_type = db.Column(db.String(128))
  size = db.Column(db.Integer)
  created_at = db.Column(db.DateTime, default = datetime.utcnow)

  def to_json(self):
    return {
      'id': self.id,
      'message_id': self.message_id,
      'file_key': self.file_key,
      'file_url': self.file_url,
      'mime_type': self.mime_type,
      'size': self.size,
      'created_at': time.mktime(self.created_at.timetuple()) if self.created_at else None
    }
