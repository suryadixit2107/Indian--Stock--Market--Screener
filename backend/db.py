"""
backend/db.py — Database layer for the multi-user backend

WHY THIS EXISTS (DRAWBACKS.md item: "Single-user architecture"):
    v0.2/v3's portfolio tracker and cache were per-machine SQLite files in
    the running user's home directory — no concept of "user A's portfolio"
    vs "user B's portfolio," no auth, nothing you could point more than one
    person at.

    This uses SQLAlchemy so the same models work against SQLite (local dev,
    zero setup) or Postgres (production, concurrent users) by changing only
    `DATABASE_URL` — no code change needed to go from "one person testing
    this" to "a small team using it."
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import (Column, DateTime, Float, ForeignKey, Integer,
                         String, create_engine)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./indiascreen_backend.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True)
    email         = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow)

    portfolio_entries = relationship("PortfolioEntry", back_populates="owner",
                                      cascade="all, delete-orphan")
    watchlist_entries = relationship("WatchlistEntry", back_populates="owner",
                                      cascade="all, delete-orphan")


class PortfolioEntry(Base):
    """Per-user holding — replaces the single-machine portfolio/tracker.py store."""
    __tablename__ = "portfolio_entries"
    id         = Column(Integer, primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    symbol     = Column(String, nullable=False)
    qty        = Column(Float, nullable=False)
    buy_price  = Column(Float, nullable=False)
    buy_date   = Column(String, nullable=False)  # ISO date string
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="portfolio_entries")


class WatchlistEntry(Base):
    """Per-user watchlist — feeds the real-time quote WebSocket."""
    __tablename__ = "watchlist_entries"
    id         = Column(Integer, primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    symbol     = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="watchlist_entries")


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency — yields a session, closes it after the request."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
