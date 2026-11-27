# Data Licensing Notes

(DRAWBACKS.md item: "Data licensing is genuinely unresolved")

This project's default data sources — yfinance (unofficial Yahoo scraper)
and direct calls to `nseindia.com`'s website API — are not licensed for
redistribution, and their terms of service are not written with "an
automated tool scrapes this and shows/sends derived output to other
people" in mind.

**For personal, single-user research use**, this is the same category of
activity as opening the NSE website in a browser yourself and reading the
numbers — low risk, and how this tool is designed to be used by default.

**The moment output derived from these sources is shown to, or sent to,
other people** — a shared dashboard, a Telegram channel with subscribers
who aren't you, a paid product — you are redistributing data obtained
outside its intended licensing terms, and that's a genuine legal exposure,
not a formality. This is separate from (and in addition to) the SEBI
investment-advice concerns already covered in `compliance/DISCLAIMER.md`.

## If you need to go beyond personal use

Consider licensed data instead of scraped data:

| Provider | What it offers |
|---|---|
| NSE Data & Analytics | Official NSE data feeds, licensed for redistribution (paid) |
| Kite Connect (Zerodha) | Official broker API with market data, licensed for apps built on top of it |
| TrueData / Global Data Feed | Commercial Indian market data vendors with redistribution-friendly licensing |
| AlphaVantage / Polygon.io | International vendors with an India/NSE data tier on paid plans |

None of these are integrated in this codebase — swapping one in means
implementing a new `Provider` subclass in `screener/providers.py` (see
that file's docstring) and pointing `DataFeed` at it instead of/alongside
`YFinanceProvider`.

## What this rework does NOT do

It does not change the underlying data source — it still defaults to
yfinance + NSE scraping, because that's what makes this free to run. It
just makes that choice explicit and documented, rather than assumed.
