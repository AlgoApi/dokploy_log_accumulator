from __future__ import annotations

import logging
import time
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.dialects.mysql import CHAR, insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import DATABASE_URL, DB_CONNECT_DELAY_SEC, DB_CONNECT_RETRIES

logger = logging.getLogger(__name__)

connect_args = {"charset": "utf8mb4"}
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args=connect_args,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class Settings(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dokploy_url: Mapped[str] = mapped_column(String(512), default="")
    dokploy_api_key_enc: Mapped[str] = mapped_column(Text, default="")
    project_id: Mapped[str] = mapped_column(String(128), default="")
    poll_interval_sec: Mapped[int] = mapped_column(Integer, default=60)
    log_since: Mapped[str] = mapped_column(String(32), default="2m")
    log_tail: Mapped[int] = mapped_column(Integer, default=300)
    level_filter: Mapped[str] = mapped_column(String(32), default="warning_error")
    exclude_patterns: Mapped[list] = mapped_column(JSON, default=list)
    exclude_regex: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    keyword_mode: Mapped[str] = mapped_column(String(16), default="any")
    max_lines_per_service: Mapped[int] = mapped_column(Integer, default=500)
    self_application_id: Mapped[str] = mapped_column(String(128), default="")


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_key: Mapped[str] = mapped_column(String(255), unique=True)
    dokploy_type: Mapped[str] = mapped_column(String(32))
    application_id: Mapped[str] = mapped_column(String(128), default="")
    compose_id: Mapped[str] = mapped_column(String(128), default="")
    container_id: Mapped[str] = mapped_column(String(128), default="")
    name: Mapped[str] = mapped_column(String(255), default="")
    app_name: Mapped[str] = mapped_column(String(255), default="")
    compose_type: Mapped[str] = mapped_column(String(64), default="")
    project_id: Mapped[str] = mapped_column(String(128), default="")
    environment_id: Mapped[str] = mapped_column(String(128), default="")
    dokploy_path: Mapped[str] = mapped_column(String(1024), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    logs: Mapped[list["LogLine"]] = relationship(
        "LogLine",
        back_populates="service",
        cascade="all, delete-orphan",
    )


class LogLine(Base):
    __tablename__ = "log_lines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"),
        index=True,
    )
    timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    level: Mapped[str] = mapped_column(String(32), default="info")
    message: Mapped[str] = mapped_column(Text)
    raw: Mapped[str] = mapped_column(Text)
    source_label: Mapped[str] = mapped_column(String(255), default="")
    line_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    service: Mapped[Service] = relationship(back_populates="logs")

    __table_args__ = (Index("ix_log_lines_service_created", "service_id", "created_at"),)


def wait_for_db() -> None:
    last_error: Exception | None = None
    for attempt in range(1, DB_CONNECT_RETRIES + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Connected to MariaDB")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "MariaDB not ready (attempt %s/%s): %s",
                attempt,
                DB_CONNECT_RETRIES,
                exc,
            )
            time.sleep(DB_CONNECT_DELAY_SEC)
    raise RuntimeError("Could not connect to MariaDB") from last_error


def init_db() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        existing = session.get(Settings, 1)
        if existing is None:
            session.add(Settings(id=1))
            session.commit()


def get_settings(session) -> Settings:
    settings = session.get(Settings, 1)
    if settings is None:
        settings = Settings(id=1)
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


def insert_log_ignore(session, **values) -> bool:
    stmt = insert(LogLine).prefix_with("IGNORE").values(**values)
    result = session.execute(stmt)
    return bool(result.rowcount)


def trim_service_logs(session, service_id: int, max_lines: int) -> None:
    if max_lines <= 0:
        return
    keep_ids = list(
        session.scalars(
            select(LogLine.id)
            .where(LogLine.service_id == service_id)
            .order_by(LogLine.created_at.desc(), LogLine.id.desc())
            .limit(max_lines)
        )
    )
    if not keep_ids:
        return
    session.query(LogLine).filter(
        LogLine.service_id == service_id,
        LogLine.id.notin_(keep_ids),
    ).delete(synchronize_session=False)
