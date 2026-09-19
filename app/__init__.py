from flask import Flask
from dotenv import load_dotenv

from .config import Config
from .db import Database
from .metrics import MetricsRegistry
from .instrumentation import register_instrumentation
from .models import init_models
from .health import health_bp
from .routes import main_bp


def create_app(config: Config | None = None) -> Flask:
    load_dotenv()
    config = config or Config.from_env()

    app = Flask(__name__)
    app.config["APP_CONFIG"] = config
    app.config["DB"] = Database(
        config.database_url, config.db_pool_size, config.db_max_overflow
    )
    app.config["METRICS"] = MetricsRegistry(config.latency_window)

    register_instrumentation(app, app.config["METRICS"])
    app.register_blueprint(health_bp)
    app.register_blueprint(main_bp)

    # Best-effort schema setup: the app must still boot when the database is
    # unreachable (e.g. during a DB-fault chaos experiment).
    try:
        init_models(app.config["DB"].engine)
    except Exception:
        app.logger.warning("DB schema init skipped: database unreachable at startup.")

    return app
