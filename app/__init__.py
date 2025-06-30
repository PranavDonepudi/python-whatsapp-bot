from flask import Flask
from app.config import load_configurations, configure_logging
from app.routes.webhook import webhook_blueprint
from app.routes.jobs import jobs_blueprint
from app.routes.bulk_send import bulk_send_bp


def create_app():
    app = Flask(__name__)

    # Load configurations and logging settings
    load_configurations(app)
    configure_logging()

    # Import and register blueprints, if any
    app.register_blueprint(bulk_send_bp)
    app.register_blueprint(webhook_blueprint)
    app.register_blueprint(jobs_blueprint)

    return app
