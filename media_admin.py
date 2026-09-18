"""Index existing owned images, reserve code references, and retry garbage collection."""
import argparse
import json
import os
from pathlib import Path
from uuid import uuid4


def index_existing(static_roots=(), apply=False):
    from app import db
    from sqlalchemy import inspect
    from app.media import TRACKED_FIELDS, content_keys, extract_keys, image_url
    from app.media_models import MediaAsset, MediaReference
    references = {}
    owners = {}
    tables = set(inspect(db.engine).get_table_names())
    models = {model.__tablename__: model for model in db.Model._decl_class_registry.values()
              if isinstance(model, type) and getattr(model, '__tablename__', None) in TRACKED_FIELDS
              and model.__tablename__ in tables}
    for kind, model in models.items():
        for item in db.session.query(model).all():
            for key in content_keys(item):
                references.setdefault(key, set()).add((kind, str(item.id)))
                owner = item.id if kind == 'users' else getattr(item, 'author_id', None)
                if owner:
                    owners.setdefault(key, owner)
    for root in static_roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for path in root.rglob('*'):
            if path.is_file() and path.suffix in ('.ts', '.tsx', '.js', '.css', '.html'):
                for key in extract_keys([path.read_text(encoding='utf-8', errors='replace')]):
                    references.setdefault(key, set()).add(('site-static', 'code'))
    created, added = 0, 0
    for key, refs in references.items():
        asset = MediaAsset.query.filter_by(storage_key=key).first()
        if asset is None:
            created += 1
            asset = MediaAsset(id=uuid4().hex, storage_key=key, url=image_url(key), owner_id=owners.get(key),
                mime_type='image/unknown', byte_size=0, width=0, height=0, status='ready')
            if apply:
                db.session.add(asset)
                db.session.flush()
        if asset.status in ('deleting', 'deleted'):
            raise ValueError('Referenced image is being deleted; restore it before reindexing')
        for kind, content_id in refs:
            exists = MediaReference.query.filter_by(asset_id=asset.id, content_type=kind, content_id=content_id).first()
            if exists is None:
                added += 1
                if apply:
                    db.session.add(MediaReference(asset_id=asset.id, content_type=kind, content_id=content_id))
        if apply:
            asset.delete_after = None
    if apply:
        db.session.commit()
    else:
        db.session.rollback()
    return {'images': len(references), 'new_images': created, 'new_references': added, 'applied': apply}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['index', 'cleanup'])
    parser.add_argument('--apply', action='store_true', help='Persist the index; never deletes objects')
    args = parser.parse_args()
    from app import create_app
    from app.media import cleanup_images
    app = create_app(os.environ.get('FLASK_CONFIG', 'development'))
    with app.app_context():
        if args.command == 'cleanup':
            print(json.dumps({'deleted': cleanup_images(limit=100)}))
        else:
            root = Path(__file__).resolve().parent
            roots = [root / 'templates'] + [root.parent / 'blog-next' / folder for folder in ('app', 'components', 'lib')]
            print(json.dumps(index_existing(roots, apply=args.apply)))


if __name__ == '__main__':
    main()
