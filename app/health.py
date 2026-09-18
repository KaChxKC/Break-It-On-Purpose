from flask import Blueprint, current_app, jsonify

health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health():
    db = current_app.config["DB"]
    cfg = current_app.config["APP_CONFIG"]
    db_ok = db.ping()
    return (
        jsonify(
            status="ok" if db_ok else "degraded",
            instance=cfg.instance_id,
            database="ok" if db_ok else "unreachable",
        ),
        200 if db_ok else 503,
    )
