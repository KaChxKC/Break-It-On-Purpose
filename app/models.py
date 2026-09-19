from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str] = mapped_column(String(200))


def init_models(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def db_check(engine: Engine) -> dict:
    """Write a row, read it back, delete it — a self-cleaning read/write proof."""
    with Session(engine) as session:
        event = Event(created_at=datetime.now(timezone.utc), note="db-check")
        session.add(event)
        session.commit()
        written_id = event.id

        read_back = session.get(Event, written_id)
        note = read_back.note

        session.delete(read_back)
        session.commit()
    return {"wrote_id": written_id, "read_note": note}
