from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import List

from sqlalchemy import (Column, DateTime, Float, Integer, MetaData, String,
                        create_engine)
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

logger = logging.getLogger("aqi-db")

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "aqi.db"
DATABASE_URL = f"sqlite:///{DB_FILE.as_posix()}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
Session = scoped_session(sessionmaker(bind=engine))
Base = declarative_base()


class SensorModel(Base):
    __tablename__ = "sensors"
    id = Column(String, primary_key=True)
    location = Column(String)
    baseline_pm25 = Column(Float)
    baseline_pm10 = Column(Float)
    last_seen = Column(DateTime)
    online = Column(String)
    health = Column(String)


class ReadingModel(Base):
    __tablename__ = "readings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, index=True)
    sensor_id = Column(String, index=True)
    pm25 = Column(Float)
    pm10 = Column(Float)
    aqi = Column(Integer)


class AlertModel(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, index=True)
    title = Column(String)
    description = Column(String)
    severity = Column(String)


def init_db() -> None:
    try:
        Base.metadata.create_all(engine)
        logger.info("Initialized SQLite DB at %s", DB_FILE)
    except OperationalError as exc:
        logger.error("Failed to initialize DB: %s", exc)


def save_reading(timestamp: datetime, sensor_id: str, pm25: float, pm10: float, aqi: int) -> None:
    try:
        session = Session()
        r = ReadingModel(timestamp=timestamp, sensor_id=sensor_id, pm25=pm25, pm10=pm10, aqi=aqi)
        session.add(r)
        session.commit()
        session.close()
    except Exception:
        logger.exception("Failed to save reading to DB")


def save_alert(timestamp: datetime, title: str, description: str, severity: str) -> None:
    try:
        session = Session()
        a = AlertModel(timestamp=timestamp, title=title, description=description, severity=severity)
        session.add(a)
        session.commit()
        session.close()
    except Exception:
        logger.exception("Failed to save alert to DB")


def get_recent_readings(limit: int = 100) -> List[ReadingModel]:
    session = Session()
    try:
        rows = session.query(ReadingModel).order_by(ReadingModel.timestamp.desc()).limit(limit).all()
        return rows
    finally:
        session.close()
