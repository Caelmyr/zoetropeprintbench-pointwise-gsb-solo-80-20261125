from pathlib import Path
from flask import Flask, jsonify, render_template
from werkzeug.exceptions import HTTPException
from .store import Store
from .assets import Assets
from .frames import Frames
from .masks import Masks
from .results import Results
from .jobs import Jobs
from .profiles import Profiles
from .regions import Regions
from .api import create_api
from .validation import Invalid, Conflict


def create_app(data_dir=None):
    root = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
    app.config['MAX_CONTENT_LENGTH'] = 96 * 1024 * 1024
    app.json.ensure_ascii = False
    store = Store(data_dir or root / 'data')
    assets = Assets(store)
    results = Results(store, assets)
    services = {'store': store, 'assets': assets, 'frames': Frames(store, assets), 'masks': Masks(store, assets), 'results': results, 'jobs': Jobs(store, assets, results), 'profiles': Profiles(store)}
    services['regions'] = Regions(store, assets, results)
    app.extensions.update(services)
    app.register_blueprint(create_api(services))

    @app.get('/prepare')
    def index():
        return render_template('prepare.html')

    @app.errorhandler(Conflict)
    def conflict(error):
        return jsonify({'error': str(error), 'type': 'revision_conflict'}), 409

    @app.errorhandler(Invalid)
    def invalid(error):
        return jsonify({'error': str(error), 'type': 'invalid'}), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify({'error': '上传不得超过96MB' if error.code == 413 else error.description}), error.code

    @app.errorhandler(Exception)
    def failure(error):
        app.logger.exception('图像处理失败')
        return jsonify({'error': '处理失败，请检查服务日志'}), 500

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        if response.mimetype == 'application/json':
            response.headers['Cache-Control'] = 'no-store'
        return response

    return app
