from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


class Database:
    """Owns the SQLAlchemy engine and exposes connection-pool telemetry.

    The pool is kept small and fails fast on exhaustion so that the
    connection-pool-exhaustion chaos experiment (Phase 5) produces a clear,
    measurable signal rather than hanging indefinitely.
    """

    def __init__(self, url: str, pool_size: int, max_overflow: int) -> None:
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self.engine: Engine = create_engine(
            url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=5,        # fail fast when the pool is exhausted
            pool_pre_ping=True,    # transparently discard connections dropped by an RDS reboot
            connect_args={"connect_timeout": 5},  # fail fast when the DB host is unreachable
            future=True,
        )

    @property
    def capacity(self) -> int:
        """Maximum concurrent connections the pool will hand out."""
        return self._pool_size + self._max_overflow

    def pool_stats(self) -> dict:
        pool = self.engine.pool
        checked_out = pool.checkedout()
        return {
            "size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": checked_out,
            "overflow": pool.overflow(),
            "capacity": self.capacity,
            "utilization": round(checked_out / self.capacity, 4) if self.capacity else 0.0,
        }

    def ping(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
