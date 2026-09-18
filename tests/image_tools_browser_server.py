"""Manual browser fixture. Local SQLite only; image output is explicitly a test fixture."""
import os
import sys
import threading
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import jsonify
from flask_cors import CORS
from test_image_tools import ImageToolsTests
from app import db
from app.image_tools import provider
from app.image_tools.worker import run_one
from app.image_tool_models import ImageToolAsset

fixture = ImageToolsTests()
fixture.setUp()
provider.generate = lambda *args, **kwargs: fixture.png('seagreen')
app = fixture.app
CORS(app, supports_credentials=True)

@app.route('/fixture/auth')
def fixture_auth():
    return jsonify(token=fixture.regular.generate_auth_token(3600), user=fixture.regular.to_json())

@app.route('/fixture/admin')
def fixture_admin():
    return jsonify(token=fixture.owner.generate_auth_token(3600), user=fixture.owner.to_json())

# The standard API already includes user info; root-layout calls can use empty fixtures.
@app.route('/fixture/info')
def fixture_info():
    return jsonify(message='Isolated browser fixture, no real model calls')

def worker():
    while True:
        with app.app_context():
            try:
                run_one()
            except Exception:
                db.session.rollback()
            finally:
                db.session.remove()
        time.sleep(1)

threading.Thread(target=worker, daemon=True).start()
if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8014, threaded=True, use_reloader=False)
