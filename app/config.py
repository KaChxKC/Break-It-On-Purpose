import os
import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Runtime configuration, sourced entirely from the environment."""

    database_url: str
    db_pool_size: int
    db_max_overflow: int
    app_host: str
    app_port: int
    waitress_threads: int
    latency_window: int
    instance_id: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            database_url=os.environ["DATABASE_URL"],
            db_pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            db_max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
            app_host=os.getenv("APP_HOST", "0.0.0.0"),
            app_port=int(os.getenv("APP_PORT", "8000")),
            waitress_threads=int(os.getenv("WAITRESS_THREADS", "8")),
            latency_window=int(os.getenv("LATENCY_WINDOW", "500")),
            # Blank INSTANCE_ID falls back to the hostname; on EC2 this becomes
            # the instance id via user-data so the status page can name the box.
            instance_id=os.getenv("INSTANCE_ID") or socket.gethostname(),
        )
