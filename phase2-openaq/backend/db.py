from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, create_engine, select, desc,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    # PostgreSQL is the default. SQLite can still be selected explicitly for tests.
    return os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://aqi_user:aqi_password@localhost:5432/smart_aqi",
    )


DATABASE_URL = _database_url()
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Location(Base):
    __tablename__ = "locations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    locality: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    region: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    source_location_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    stations: Mapped[list["Station"]] = relationship(back_populates="location", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("source", "source_location_id", name="uq_location_source_id"),)


class Station(Base):
    __tablename__ = "stations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(50), default="openaq")
    source_station_id: Mapped[str] = mapped_column(String(100))
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location: Mapped[Location] = relationship(back_populates="stations")
    measurements: Mapped[list["Measurement"]] = relationship(back_populates="station", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("source", "source_station_id", name="uq_station_source_id"),)


class Measurement(Base):
    __tablename__ = "measurements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id", ondelete="CASCADE"), index=True)
    parameter: Mapped[str] = mapped_column(String(30), index=True)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    source: Mapped[str] = mapped_column(String(50), default="openaq")
    aqi_us: Mapped[int | None] = mapped_column(Integer, nullable=True)
    station: Mapped[Station] = relationship(back_populates="measurements")

    __table_args__ = (
        UniqueConstraint("station_id", "parameter", "measured_at", name="uq_measurement_station_parameter_time"),
        Index("ix_measurements_parameter_time", "parameter", "measured_at"),
    )


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True)
    station_id: Mapped[int | None] = mapped_column(ForeignKey("stations.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    is_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class ApiSource(Base):
    __tablename__ = "api_sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    base_url: Mapped[str] = mapped_column(String(255))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


def init_db() -> None:
    """Create tables for local development. Use Alembic migrations for deployed upgrades."""
    Base.metadata.create_all(bind=engine)


def get_session():
    return SessionLocal()


def get_latest_measurements(limit: int = 100) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.execute(
            select(Measurement, Station, Location)
            .join(Station, Measurement.station_id == Station.id)
            .join(Location, Station.location_id == Location.id)
            .order_by(desc(Measurement.measured_at)).limit(limit)
        ).all()
        return [
            {"measurement_id": m.id, "station_id": s.id, "station_name": s.name,
             "location_id": loc.id, "location": loc.name, "city": loc.locality,
             "country": loc.country, "latitude": s.latitude or loc.latitude,
             "longitude": s.longitude or loc.longitude, "parameter": m.parameter,
             "value": m.value, "unit": m.unit, "measured_at": m.measured_at,
             "aqi": m.aqi_us, "source": m.source}
            for m, s, loc in rows
        ]
