"""
alerts/telegram_bot.py — Opt-in scan digest bot (IndiaScreen v3)

CHANGED FROM v0.2 (see /DRAWBACKS.md, item "Regulatory exposure"):
    - Signals are relabeled through compliance.relabel_signal() before
      being sent — no bare "Buy"/"Sell"/"Strong Buy" text goes out.
    - Every message carries the non-advisory disclosure
      (compliance.with_disclaimer).
    - Default mode is *pull*: a user messages the bot /scan to request a
      run for themselves. The old cron-style "push to everyone at 4 PM"
      mode still exists (`--broadcast`) but is opt-in and clearly labeled,
      because unsolicited broadcast of algorithmic trade-style calls is the
      specific pattern regulators have targeted — a user-initiated pull
      request is a materially different (and safer) posture.
    - Basic per-chat rate limiting so the bot can't be mistaken for a spam/
      tip-broadcast service if run against a large chat list.
"""

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

from compliance import relabel_signal, with_disclaimer

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
MIN_SECONDS_BETWEEN_MESSAGES_PER_CHAT = 3.0


class TelegramBot:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token or os.getenv("TELEGRAM_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self._last_sent: dict[str, float] = {}
        if not self.token:
            raise ValueError("TELEGRAM_TOKEN not set (env var or constructor arg)")

    def _rate_limit(self, chat_id: str) -> None:
        last = self._last_sent.get(chat_id, 0.0)
        wait = MIN_SECONDS_BETWEEN_MESSAGES_PER_CHAT - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        self._last_sent[chat_id] = time.time()

    def send_message(self, text: str, chat_id: Optional[str] = None) -> bool:
        chat_id = chat_id or self.chat_id
        if not chat_id:
            raise ValueError("No chat_id provided or configured")
        self._rate_limit(chat_id)
        url = TELEGRAM_API.format(token=self.token, method="sendMessage")
        try:
            resp = requests.post(url, json={
                "chat_id": chat_id, "text": text, "parse_mode": "Markdown",
            }, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    def format_digest(self, results, index_label: str = "Nifty 50") -> str:
        """
        Build a compliance-safe digest. `results` is a DataFrame with at
        least columns: symbol, price, signal.
        """
        import pandas as pd  # local import keeps module import light

        if results is None or (isinstance(results, pd.DataFrame) and results.empty):
            return with_disclaimer(
                f"*IndiaScreen scan — {index_label}*\nNo stocks matched your criteria today.",
                short=True,
            )

        df = results.copy()
        df["signal"] = df["signal"].map(relabel_signal)

        lines = [f"*IndiaScreen scan — {index_label}*", ""]
        for label in [
            "Strong Match (bullish criteria)", "Match (bullish criteria)",
            "Match (bearish criteria)", "Strong Match (bearish criteria)",
        ]:
            subset = df[df["signal"] == label]
            if subset.empty:
                continue
            lines.append(f"*{label}*")
            for _, row in subset.head(8).iterrows():
                price = row.get("price", row.get("ltp", "?"))
                lines.append(f"  • {row['symbol']} — ₹{price}")
            lines.append("")

        return with_disclaimer("\n".join(lines).strip())

    def send_digest(self, results, index_label: str = "Nifty 50",
                     chat_id: Optional[str] = None) -> bool:
        text = self.format_digest(results, index_label)
        return self.send_message(text, chat_id=chat_id)

    def broadcast_digest(self, results, chat_ids: list[str],
                          index_label: str = "Nifty 50") -> dict[str, bool]:
        """
        Opt-in broadcast mode. Only use this for a list of chat_ids that
        have explicitly subscribed (e.g. sent /subscribe to the bot) —
        see README "Compliance notes" before wiring this into a cron job
        aimed at people who did not request it.
        """
        text = self.format_digest(results, index_label)
        return {cid: self.send_message(text, chat_id=cid) for cid in chat_ids}


def run_pull_scan(chat_id: str, index: str = "nifty50", **filters) -> bool:
    """Handler for a user-initiated /scan command: run the screener now,
    for this one requester, and send only to them."""
    from screener.scanner import Screener

    bot = TelegramBot()
    s = Screener(index=index)
    results = s.run(**filters)
    return bot.send_digest(results, index_label=index.upper(), chat_id=chat_id)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="IndiaScreen Telegram bot")
    parser.add_argument("--test", action="store_true", help="Send a test message")
    parser.add_argument("--index", default="nifty50")
    args = parser.parse_args()

    bot = TelegramBot()
    if args.test:
        ok = bot.send_message(with_disclaimer(
            "✅ IndiaScreen bot connected. This bot only sends scan digests "
            "when you explicitly request them.", short=True,
        ))
        print("Sent." if ok else "Failed — check TELEGRAM_TOKEN/CHAT_ID.")
