"""
backend/app.py — Multi-user API + real-time quote WebSocket for IndiaScreen

WHY THIS EXISTS:
    Two remaining drawbacks addressed here:

    1. "Single-user architecture" — the Streamlit app + per-machine SQLite
       cache had no concept of separate users. This FastAPI app puts
       portfolios/watchlists behind per-user auth (backend/auth.py) and
       SQLAlchemy models (backend/db.py) that work against Postgres in
       production, so multiple people can use one deployment safely.

    2. "No real-time streaming" — NSE doesn't expose a public WebSocket, so
       the underlying fetch is still polling (screener.providers.DataFeed).
       What was missing was the *next* layer: pushing updates to connected
       clients as they happen, instead of every client independently
       polling NSE (which multiplies exactly the rate-limit/blocking risk
       covered in DRAWBACKS.md item 1). `/ws/quotes` here polls upstream
       once per interval on the server side and fans that out to every
       connected client over a WebSocket — one upstream poll, N pushed
       updates, instead of N independent polls.

Run:
    uvicorn backend.app:app --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .auth import (create_access_token, get_current_user, hash_password,
                    verify_password)
from .db import PortfolioEntry, User, WatchlistEntry, get_db, init_db

logger = logging.getLogger(__name__)

app = FastAPI(title="IndiaScreen API", version="3.0")


@app.on_event("startup")
def _startup():
    init_db()


# ── Schemas ──────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    email: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PortfolioIn(BaseModel):
    symbol: str
    qty: float
    buy_price: float
    buy_date: str  # ISO date


class WatchlistIn(BaseModel):
    symbol: str


# ── Auth routes ──────────────────────────────────────────────────────────────

@app.post("/auth/register", response_model=Token)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(400, "Email already registered")
    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token({"sub": user.email})
    return Token(access_token=token)


@app.post("/auth/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(401, "Incorrect email or password")
    token = create_access_token({"sub": user.email})
    return Token(access_token=token)


# ── Portfolio routes (per-user, replaces single-machine portfolio/tracker.py store) ──

@app.get("/portfolio")
def list_portfolio(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    entries = db.query(PortfolioEntry).filter(PortfolioEntry.user_id == user.id).all()
    return [
        {"id": e.id, "symbol": e.symbol, "qty": e.qty,
         "buy_price": e.buy_price, "buy_date": e.buy_date}
        for e in entries
    ]


@app.post("/portfolio")
def add_holding(payload: PortfolioIn, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    entry = PortfolioEntry(user_id=user.id, symbol=payload.symbol.upper(),
                            qty=payload.qty, buy_price=payload.buy_price,
                            buy_date=payload.buy_date)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"id": entry.id, "status": "added"}


@app.delete("/portfolio/{entry_id}")
def delete_holding(entry_id: int, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    entry = db.query(PortfolioEntry).filter(
        PortfolioEntry.id == entry_id, PortfolioEntry.user_id == user.id,
    ).first()
    if not entry:
        raise HTTPException(404, "Not found")
    db.delete(entry)
    db.commit()
    return {"status": "deleted"}


# ── Watchlist routes (feeds the WebSocket below) ────────────────────────────

@app.get("/watchlist")
def list_watchlist(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    entries = db.query(WatchlistEntry).filter(WatchlistEntry.user_id == user.id).all()
    return [{"id": e.id, "symbol": e.symbol} for e in entries]


@app.post("/watchlist")
def add_watch(payload: WatchlistIn, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    entry = WatchlistEntry(user_id=user.id, symbol=payload.symbol.upper())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"id": entry.id, "status": "added"}


# ── Health endpoint (DRAWBACKS.md: "No pipeline health monitoring") ─────────

@app.get("/health")
def health():
    from health.monitor import HealthMonitor
    hm = HealthMonitor()
    return hm.status()


# ── Real-time quote WebSocket ────────────────────────────────────────────────
# One background poll loop per symbol-set, fanned out to all subscribed
# clients — NOT one poll per client. This is the piece that was missing:
# even with resilient fetching, N clients independently polling NSE
# multiplies the exact rate-limit/blocking risk DataFeed's circuit breaker
# is designed to survive, not eliminate.

class QuoteBroadcaster:
    def __init__(self, poll_interval_seconds: float = 5.0):
        self.poll_interval = poll_interval_seconds
        self._subscribers: dict[str, set[WebSocket]] = {}
        self._task: Optional[asyncio.Task] = None

    async def subscribe(self, ws: WebSocket, symbol: str):
        symbol = symbol.upper()
        self._subscribers.setdefault(symbol, set()).add(ws)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll_loop())

    def unsubscribe(self, ws: WebSocket, symbol: str):
        symbol = symbol.upper()
        subs = self._subscribers.get(symbol)
        if subs:
            subs.discard(ws)
            if not subs:
                del self._subscribers[symbol]

    async def _poll_loop(self):
        from screener.providers import DataFeed
        feed = DataFeed()
        while self._subscribers:
            symbols = list(self._subscribers.keys())
            loop = asyncio.get_event_loop()
            for sym in symbols:
                result = await loop.run_in_executor(None, feed.get_quote, sym)
                if not result.ok:
                    continue
                payload = json.dumps({"symbol": sym, **result.data, "stale": result.is_stale})
                dead = []
                for ws in self._subscribers.get(sym, set()):
                    try:
                        await ws.send_text(payload)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    self.unsubscribe(ws, sym)
            await asyncio.sleep(self.poll_interval)


broadcaster = QuoteBroadcaster()


@app.websocket("/ws/quotes/{symbol}")
async def ws_quotes(websocket: WebSocket, symbol: str):
    await websocket.accept()
    await broadcaster.subscribe(websocket, symbol)
    try:
        while True:
            # Keep the connection open; client doesn't need to send anything.
            await websocket.receive_text()
    except WebSocketDisconnect:
        broadcaster.unsubscribe(websocket, symbol)
