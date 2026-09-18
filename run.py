from dotenv import load_dotenv
from waitress import serve

from app import create_app
from app.config import Config

load_dotenv()
config = Config.from_env()
app = create_app(config)


if __name__ == "__main__":
    print(
        f"Serving on http://{config.app_host}:{config.app_port} "
        f"(instance={config.instance_id}, threads={config.waitress_threads})"
    )
    serve(app, host=config.app_host, port=config.app_port, threads=config.waitress_threads)
