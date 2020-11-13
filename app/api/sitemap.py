from flask import jsonify
from sqlalchemy import and_, func

from . import api
from ..models import Post, Tag, PostType, post_tag_relations

@api.route('/get-sitemap-info/')
def get_sitemap_info():
  hide_post_type = PostType.query.filter_by(special = 1).first()
  post_list = Post.query.filter(and_(Post.hide == False, Post.type_id != hide_post_type.id)).all()
  post_list = list(map(lambda post: post.id, post_list))

  # tag_list = db.session.query(Tag, func.count(post_tag_relations.post_id)).outerjoin(post_tag_relations).group_by(Tag).all()
  # tag_list = list(map(lambda [tag]: tag.id, tag_list))
  tag_list = []
  for tag in Tag.query.all():
    if tag.posts.count():
      tag_list.append(tag.id)

  return jsonify({
    'posts': post_list,
    'tags': tag_list
  })