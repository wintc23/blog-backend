"""Independent notification worker; never starts inside a web request."""
import argparse
import os
import signal
import time
from app import create_app, db
from app.interaction_notifications import run_one


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    app = create_app(os.environ.get('FLASK_CONFIG', 'production'))
    def timeout(*_):
        raise TimeoutError("Notification transport timed out")
    signal.signal(signal.SIGALRM, timeout)
    with app.app_context():
        while True:
            try:
                signal.alarm(90)
                worked = run_one()
            except Exception as error:
                db.session.rollback()
                app.logger.error('Notification worker retry: %s', type(error).__name__)
                worked = False
            finally:
                signal.alarm(0)
                db.session.remove()
            if args.once:
                break
            if not worked:
                time.sleep(2)


if __name__ == '__main__':
    main()
