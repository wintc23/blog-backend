"""Run separately from the web server: python image_tools.py seed|worker|cleanup."""
import argparse
import os
import time
from app import create_app, db
from app.image_tools.worker import seed, run_one, cleanup


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['seed', 'worker', 'cleanup', 'migrate-storage'])
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--source')
    parser.add_argument('--kind', choices=['image-tools', 'generation'], default='image-tools')
    parser.add_argument('--delete-source', action='store_true')
    args = parser.parse_args()
    app = create_app(os.environ.get('FLASK_CONFIG', 'development'))
    with app.app_context():
        if args.command == 'migrate-storage':
            from app.image_tools.migration import migrate
            if not args.source:
                parser.error('--source is required for migration')
            print('Verified objects:', migrate(args.source, args.kind, args.delete_source))
        elif args.command == 'seed':
            seed()
        elif args.command == 'cleanup':
            cleanup()
        else:
            while True:
                worked = run_one()
                db.session.remove()
                if args.once:
                    break
                if not worked:
                    time.sleep(2)


if __name__ == '__main__':
    main()
