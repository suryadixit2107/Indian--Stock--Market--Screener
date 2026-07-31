"""
ui/app.py — Full Streamlit dashboard for IndiaScreen v0.2.0

Run with:  streamlit run ui/app.py

Tabs:
  1. Screener        — filter stocks with technical + fundamental + delivery
  2. NL Screener     — type plain English, get results
  3. Live Quotes     — real-time NSE prices
  4. Option Chain    — PCR, max pain, OI buildup for any symbol
  5. Sector Rotation — which sectors are leading/lagging
  6. Candlestick     — pattern detection on any symbol
  7. Backtest        — strategy backtester with equity curve
  8. Portfolio       — track your holdings with P&L and XIRR
  9. FII / DII       — institutional flow data
  10. About          — setup guide
"""

import logging
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="IndiaScreen v0.2",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.metric-card {
    background: #f8f9fa; border-radius: 8px;
    padding: 12px 16px; margin: 4px 0;
}
.signal-strong-buy  { color: #0F6E56; font-weight: 600; }
.signal-buy         { color: #1D9E75; }
.signal-hold        { color: #854F0B; }
.signal-sell        { color: #993C1D; }
.signal-strong-sell { color: #7B1D05; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

st.title("📈 IndiaScreen v0.2")
st.caption("NSE/BSE Screener · Live Prices · Option Chain · Sector Rotation · Portfolio · NL Search")

# ── Tabs ──────────────────────────────────────────────────────────────────────
(tab_screen, tab_nl, tab_live, tab_options,
 tab_sectors, tab_candles, tab_bt, tab_port,
 tab_fii, tab_about) = st.tabs([
    "🔍 Screener", "🤖 NL Search", "⚡ Live",
    "⚙️ Options", "🔄 Sectors", "🕯 Patterns",
    "⏪ Backtest", "💼 Portfolio", "🏦 FII/DII", "ℹ️ About",
])

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("🔍 Screener Filters")
    index    = st.selectbox("Index", ["nifty50","nifty500","sensex"], index=0)
    period   = st.selectbox("Period", ["6mo","1y","2y"], index=1)
    exchange = st.selectbox("Exchange", ["NSE","BSE"], index=0)

    st.subheader("Technical")
    rsi_range    = st.slider("RSI (14)", 0, 100, (0, 70))
    above_200    = st.checkbox("Above 200 DMA")
    macd_bull    = st.checkbox("MACD bullish crossover")
    st_up        = st.checkbox("Supertrend bullish")
    near_52h     = st.checkbox("Near 52-week high")
    vol_ratio    = st.slider("Min volume ratio", 0.5, 5.0, 1.0, 0.1)
    delivery     = st.slider("Min delivery %", 0, 90, 0, 5)

    st.subheader("Fundamental")
    from screener.fundamentals import PRESETS
    preset   = st.selectbox("Preset", ["None"] + list(PRESETS.keys()))
    pe_range = st.slider("P/E range", 0, 150, (0, 50))
    roe_min  = st.slider("Min ROE (%)", 0, 50, 0)
    de_max   = st.slider("Max D/E", 0.0, 5.0, 3.0, 0.1)
    cap_min  = st.number_input("Min Mkt Cap (₹Cr)", value=0, step=1000)

    sectors_all = [
        "Banking & Finance","IT & Technology","Pharma & Healthcare",
        "FMCG","Auto","Energy & Power","Infrastructure","Telecom",
        "Metals & Mining","Consumer","Chemicals",
    ]
    sel_sectors = st.multiselect("Sectors (empty = all)", sectors_all)

    st.subheader("Output")
    sig_filter = st.multiselect(
        "Signal filter",
        ["Strong Buy","Buy","Hold","Sell","Strong Sell"],
        default=["Strong Buy","Buy"],
    )
    top_n    = st.slider("Top N results", 5, 100, 20)
    sort_col = st.selectbox("Sort by", ["fund_score","rsi_14","pe_ratio","market_cap_cr","delivery_pct"])
    run_btn  = st.button("▶ Run Screen", type="primary", use_container_width=True)


# ── Helper: colour signal ────────────────────────────────────────────────────
def color_signal(val):
    m = {
        "Strong Buy":  "background:#1D9E75;color:white",
        "Buy":         "background:#9FE1CB;color:#085041",
        "Hold":        "background:#FAC775;color:#633806",
        "Sell":        "background:#F5C4B3;color:#993C1D",
        "Strong Sell": "background:#D85A30;color:white",
    }
    return m.get(val, "")

def color_rsi(val):
    if pd.isna(val): return ""
    if val > 70: return "color:#D85A30"
    if val < 30: return "color:#1D9E75"
    return ""

def results_table(results):
    cols = ["symbol","company_name","sector","price","change_pct",
            "rsi_14","pe_ratio","roe","market_cap_cr",
            "delivery_pct","above_200dma","fund_score","signal"]
    cols = [c for c in cols if c in results.columns]
    rename = {
        "symbol":"Symbol","company_name":"Company","sector":"Sector",
        "price":"Price(₹)","change_pct":"Chg%","rsi_14":"RSI",
        "pe_ratio":"P/E","roe":"ROE%","market_cap_cr":"MktCap(Cr)",
        "delivery_pct":"Del%","above_200dma":">200DMA",
        "fund_score":"FScore","signal":"Signal",
    }
    styled = (
        results[cols].rename(columns=rename).style
        .applymap(color_signal, subset=["Signal"])
        .applymap(color_rsi,    subset=["RSI"])
        .format({
            "Price(₹)":"₹{:.2f}","Chg%":"{:+.2f}%","RSI":"{:.1f}",
            "P/E":"{:.1f}","ROE%":"{:.1f}%","MktCap(Cr)":"₹{:,.0f}",
            "Del%":"{:.1f}%","FScore":"{:.0f}",
        }, na_rep="—")
    )
    st.dataframe(styled, use_container_width=True, height=480)

def candlestick_chart(df, title="", indicators=True):
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"], name="OHLC",
    ))
    if indicators and "sma_50" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["sma_50"], name="50 SMA",
            line=dict(color="orange", width=1), opacity=0.8,
        ))
    if indicators and "sma_200" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["sma_200"], name="200 SMA",
            line=dict(color="red", width=1.5), opacity=0.9,
        ))
    if indicators and "bb_upper" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["bb_upper"], name="BB Upper",
            line=dict(color="grey", width=1, dash="dot"), opacity=0.5,
        ))
        fig.add_trace(go.Scatter(
            x=df.index, y=df["bb_lower"], name="BB Lower",
            line=dict(color="grey", width=1, dash="dot"), opacity=0.5,
            fill="tonexty", fillcolor="rgba(200,200,200,0.08)",
        ))
    fig.update_layout(
        title=title, xaxis_rangeslider_visible=False,
        height=480, margin=dict(l=0,r=0,t=30,b=0),
    )
    return fig


# ════════════════════════════════════════════════════════
# TAB 1 — SCREENER
# ════════════════════════════════════════════════════════
with tab_screen:
    if run_btn:
        with st.spinner(f"Scanning {index.upper()}…"):
            from screener.scanner import Screener
            from screener.cache   import Cache
            cache   = Cache()
            cached  = cache.load_today_signals()

            if not cached.empty and index == "nifty50":
                st.info("Loaded from today's cache — click ▶ again after 4 PM to refresh.")
                results = cached.head(top_n)
            else:
                s = Screener(index=index, period=period, exchange=exchange)
                results = s.run(
                    rsi_min=rsi_range[0] or None, rsi_max=rsi_range[1],
                    above_200dma=True if above_200 else None,
                    macd_bullish=True if macd_bull else None,
                    supertrend_up=True if st_up else None,
                    near_52w_high=True if near_52h else None,
                    vol_ratio_min=vol_ratio if vol_ratio>1 else None,
                    delivery_min=delivery or None,
                    pe_min=pe_range[0] or None, pe_max=pe_range[1],
                    roe_min=roe_min/100 if roe_min else None,
                    debt_equity_max=de_max if de_max<5 else None,
                    market_cap_min_cr=cap_min or None,
                    sectors=sel_sectors or None,
                    fundamental_preset=None if preset=="None" else preset,
                    signals=sig_filter or None,
                    top_n=top_n, sort_by=sort_col,
                )
                if not results.empty:
                    cache.save_signals(results)

        if results.empty:
            st.warning("No stocks matched. Try relaxing filters.")
        else:
            c1,c2,c3,c4,c5 = st.columns(5)
            c1.metric("Passed", len(results))
            c2.metric("Strong Buy", len(results[results["signal"]=="Strong Buy"]))
            c3.metric("Buy",        len(results[results["signal"]=="Buy"]))
            c4.metric("Hold",       len(results[results["signal"]=="Hold"]))
            c5.metric("Sell",       len(results[results["signal"].isin(["Sell","Strong Sell"])]))

            results_table(results)

            st.download_button(
                "⬇️ Download CSV", results.to_csv(index=False),
                file_name=f"indiascreen_{index}_{pd.Timestamp.now().date()}.csv",
                mime="text/csv",
            )

            # Click a symbol → candlestick chart
            sym_chart = st.selectbox("📊 Chart a stock", [""] + results["symbol"].tolist())
            if sym_chart:
                from screener.fetch       import fetch_ohlcv
                from screener.technicals  import compute_all
                df_c = fetch_ohlcv(sym_chart, period=period, exchange=exchange)
                if not df_c.empty:
                    df_c = compute_all(df_c)
                    st.plotly_chart(candlestick_chart(df_c.tail(120), title=sym_chart, indicators=True),
                                    use_container_width=True)
    else:
        st.info("Set filters in the sidebar, then click **▶ Run Screen**.")


# ════════════════════════════════════════════════════════
# TAB 2 — NL SCREENER
# ════════════════════════════════════════════════════════
with tab_nl:
    st.subheader("🤖 Natural Language Screener")
    st.markdown("Describe what you want in plain English — no need to remember filter names.")

    from screener.nl_screener import NLScreener
    nls = NLScreener()

    if not nls.api_key:
        st.warning(
            "⚠️ No ANTHROPIC_API_KEY configured — queries below are parsed by a "
            "simple keyword matcher, not the AI parser. It handles common "
            "phrasing well but can misread ambiguous queries. Set the key in "
            "`.env` for full AI parsing (see DRAWBACKS.md, 'Silent capability "
            "downgrade')."
        )

    st.markdown("**Example queries:**")
    for ex in nls.suggest_queries()[:4]:
        if st.button(ex, key=f"nlbtn_{ex[:20]}"):
            st.session_state["nl_query"] = ex

    nl_query = st.text_input(
        "Your query",
        value=st.session_state.get("nl_query", ""),
        placeholder="e.g. Show me oversold banking stocks with P/E below 15",
    )

    if st.button("🔍 Search", type="primary") and nl_query:
        with st.spinner("Parsing query and scanning…"):
            filters = nls.parse(nl_query)
            st.json(filters)
            nl_results = nls.run(nl_query)

        if nl_results.empty:
            st.warning("No stocks found. Try a broader query.")
        else:
            st.success(f"Found {len(nl_results)} stocks")
            results_table(nl_results)


# ════════════════════════════════════════════════════════
# TAB 3 — LIVE QUOTES
# ════════════════════════════════════════════════════════
with tab_live:
    st.subheader("⚡ Live NSE Quotes")

    from screener.live import is_market_open, get_live_quote, get_top_gainers, get_top_losers
    from screener.fetch import NIFTY50_SYMBOLS

    is_open = is_market_open()
    status  = "🟢 Market OPEN" if is_open else "🔴 Market CLOSED"
    st.markdown(f"**{status}** (NSE: 9:15 AM – 3:30 PM IST)")

    col1, col2 = st.columns(2)
    with col1:
        sym_live = st.text_input("Enter NSE symbol", value="RELIANCE")
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        fetch_live = st.button("⚡ Get Live Quote")

    if fetch_live and sym_live:
        with st.spinner(f"Fetching live quote for {sym_live}…"):
            q = get_live_quote(sym_live.upper())

        if "error" in q:
            st.error(f"Could not fetch quote: {q['error']}")
        else:
            c1,c2,c3,c4 = st.columns(4)
            chg_color  = "normal" if q["change_pct"] >= 0 else "inverse"
            c1.metric("LTP",        f"₹{q['ltp']:,.2f}", f"{q['change_pct']:+.2f}%", delta_color=chg_color)
            c2.metric("Day High",   f"₹{q['high']:,.2f}")
            c3.metric("Day Low",    f"₹{q['low']:,.2f}")
            c4.metric("Prev Close", f"₹{q['prev_close']:,.2f}")

            c5,c6,c7,c8 = st.columns(4)
            c5.metric("Volume",        f"{q.get('volume',0):,}")
            c6.metric("52W High",      f"₹{q.get('week_52_high',0):,.0f}")
            c7.metric("52W Low",       f"₹{q.get('week_52_low',0):,.0f}")
            c8.metric("Upper Circuit", f"₹{q.get('upper_circuit',0):,.0f}")

    st.divider()
    if st.button("📊 Load Today's Top Gainers & Losers"):
        with st.spinner("Fetching…"):
            gainers = get_top_gainers()
            losers  = get_top_losers()

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("### 🟢 Top Gainers")
            if gainers:
                gdf = pd.DataFrame(gainers)[["symbol","lastPrice","pChange"]].head(10)
                gdf.columns = ["Symbol","LTP","Change%"]
                st.dataframe(gdf, use_container_width=True, hide_index=True)
        with col2:
            st.markdown("### 🔴 Top Losers")
            if losers:
                ldf = pd.DataFrame(losers)[["symbol","lastPrice","pChange"]].head(10)
                ldf.columns = ["Symbol","LTP","Change%"]
                st.dataframe(ldf, use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════
# TAB 4 — OPTION CHAIN
# ════════════════════════════════════════════════════════
with tab_options:
    st.subheader("⚙️ Option Chain Analyser")

    from screener.options import OptionChain

    oc1, oc2 = st.columns(2)
    with oc1:
        oc_sym = st.text_input("Symbol", value="NIFTY",
                               help="Use NIFTY, BANKNIFTY for indices, or any NSE equity symbol")
    with oc2:
        st.markdown("<br>", unsafe_allow_html=True)
        load_oc = st.button("⚙️ Load Option Chain", type="primary")

    if load_oc:
        with st.spinner(f"Fetching option chain for {oc_sym}…"):
            oc = OptionChain(oc_sym.upper())
            oc.load()

        if oc.df.empty:
            st.error("Could not load option chain. NSE may be rate-limiting or market is closed.")
        else:
            summary = oc.summary()
            levels  = summary["key_levels"]
            pcr_info = summary["pcr"]

            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Spot Price",  f"₹{levels.get('spot',0):,.0f}")
            c2.metric("PCR",         pcr_info["pcr"], pcr_info["sentiment"])
            c3.metric("Max Pain",    f"₹{summary['max_pain']:,.0f}")
            c4.metric("Signal",      pcr_info["signal"])

            c5,c6 = st.columns(2)
            c5.metric("Support (Max PE OI)",    f"₹{levels.get('support',0):,.0f}")
            c6.metric("Resistance (Max CE OI)", f"₹{levels.get('resistance',0):,.0f}")

            st.divider()
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("### 📊 OI Buildup — Top Strikes")
                oi_df = oc.oi_buildup(top_n=12)
                if not oi_df.empty:
                    fig_oi = go.Figure()
                    fig_oi.add_bar(x=oi_df["strike"].astype(str), y=oi_df["ce_oi"],
                                   name="Call OI", marker_color="#D85A30")
                    fig_oi.add_bar(x=oi_df["strike"].astype(str), y=oi_df["pe_oi"],
                                   name="Put OI",  marker_color="#1D9E75")
                    fig_oi.update_layout(barmode="group", height=360,
                                         xaxis_title="Strike", yaxis_title="Open Interest",
                                         margin=dict(l=0,r=0,t=0,b=0))
                    st.plotly_chart(fig_oi, use_container_width=True)

            with col2:
                st.markdown("### 📈 IV Skew")
                iv_df = oc.iv_surface()
                if not iv_df.empty:
                    fig_iv = go.Figure()
                    fig_iv.add_trace(go.Scatter(x=iv_df["strike"], y=iv_df["ce_iv"],
                                                name="Call IV", line=dict(color="#D85A30")))
                    fig_iv.add_trace(go.Scatter(x=iv_df["strike"], y=iv_df["pe_iv"],
                                                name="Put IV",  line=dict(color="#1D9E75")))
                    fig_iv.update_layout(height=360, xaxis_title="Strike",
                                          yaxis_title="Implied Volatility (%)",
                                          margin=dict(l=0,r=0,t=0,b=0))
                    st.plotly_chart(fig_iv, use_container_width=True)

            st.markdown("### Full Option Chain")
            st.dataframe(oc.df, use_container_width=True, height=360)


# ════════════════════════════════════════════════════════
# TAB 5 — SECTOR ROTATION
# ════════════════════════════════════════════════════════
with tab_sectors:
    st.subheader("🔄 Sector Rotation Tracker")

    if st.button("📊 Load Sector Data"):
        with st.spinner("Downloading all NSE sector indices…"):
            from screener.sectors import SectorTracker
            st_tracker = SectorTracker(period="1y")
            perf_df    = st_tracker.performance_table()
            rotation   = st_tracker.rotation_signals()

        # Heatmap
        st.markdown("### Performance Heatmap")
        hmap = st_tracker.heatmap_data()
        fig_heat = px.imshow(
            hmap.T,
            color_continuous_scale="RdYlGn",
            color_continuous_midpoint=0,
            aspect="auto",
            title="Sector Returns (%) — Green = positive, Red = negative",
            labels={"color": "Return %"},
        )
        fig_heat.update_layout(height=320, margin=dict(l=0,r=0,t=40,b=0))
        st.plotly_chart(fig_heat, use_container_width=True)

        # Rotation signals
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("### 🟢 Leading Sectors (above Nifty 50)")
            if rotation["leading"]:
                ldf = pd.DataFrame(rotation["leading"])
                st.dataframe(ldf, use_container_width=True, hide_index=True)
            st.markdown("### 🚀 Gaining Momentum")
            if rotation["improving"]:
                idf = pd.DataFrame(rotation["improving"])
                st.dataframe(idf, use_container_width=True, hide_index=True)
        with col2:
            st.markdown("### 🔴 Lagging Sectors")
            if rotation["lagging"]:
                ldf2 = pd.DataFrame(rotation["lagging"])
                st.dataframe(ldf2, use_container_width=True, hide_index=True)
            st.markdown("### 📉 Losing Momentum")
            if rotation["deteriorating"]:
                ddf = pd.DataFrame(rotation["deteriorating"])
                st.dataframe(ddf, use_container_width=True, hide_index=True)

        st.markdown("### Full Performance Table")
        st.dataframe(perf_df, use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════
# TAB 6 — CANDLESTICK PATTERNS
# ════════════════════════════════════════════════════════
with tab_candles:
    st.subheader("🕯 Candlestick Pattern Detection")

    cp1, cp2 = st.columns(2)
    with cp1:
        cdl_sym = st.text_input("NSE Symbol", value="RELIANCE", key="cdl_sym")
    with cp2:
        cdl_per = st.selectbox("Period", ["3mo","6mo","1y"], index=1, key="cdl_per")

    if st.button("🔍 Detect Patterns"):
        with st.spinner(f"Fetching data and detecting patterns for {cdl_sym}…"):
            from screener.fetch      import fetch_ohlcv
            from screener.technicals import compute_all
            from screener.patterns   import detect_all_patterns, PatternSummary

            df_cdl = fetch_ohlcv(cdl_sym.upper(), period=cdl_per)
            if df_cdl.empty:
                st.error("Could not fetch data. Check the symbol.")
            else:
                df_cdl = compute_all(df_cdl)
                df_cdl = detect_all_patterns(df_cdl)
                ps     = PatternSummary(df_cdl)
                latest = ps.latest()
                hist   = ps.history(days=30)

                # Latest pattern summary
                c1,c2,c3 = st.columns(3)
                c1.metric("Bullish Patterns", len(latest["bullish"]))
                c2.metric("Bearish Patterns", len(latest["bearish"]))
                c3.metric("Pattern Signal",   latest["summary"])

                if latest["bullish"]:
                    st.success("🟢 Bullish: " + ", ".join(latest["bullish"]))
                if latest["bearish"]:
                    st.error("🔴 Bearish: " + ", ".join(latest["bearish"]))
                if latest["neutral"]:
                    st.info("⚪ Neutral: " + ", ".join(latest["neutral"]))

                # Chart
                st.plotly_chart(candlestick_chart(df_cdl.tail(90), title=cdl_sym), use_container_width=True)

                # Pattern history
                if hist:
                    st.markdown("### Pattern History (last 30 days)")
                    rows = []
                    for day in hist:
                        for p in day["patterns"]:
                            rows.append({
                                "Date": day["date"],
                                "Pattern": p["pattern"],
                                "Direction": p["direction"].title(),
                                "Signal": "🟢 Bullish" if p["signal"] > 0 else "🔴 Bearish",
                            })
                    if rows:
                        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════
# TAB 7 — BACKTEST
# ════════════════════════════════════════════════════════
with tab_bt:
    st.subheader("⏪ Strategy Backtester")

    from backtest.strategies import STRATEGIES

    bc1, bc2 = st.columns(2)
    with bc1:
        bt_strat   = st.selectbox("Strategy",   list(STRATEGIES.keys()))
        bt_index   = st.selectbox("Universe",   ["nifty50","sensex"], key="bt_idx")
        bt_capital = st.number_input("Capital (₹)", value=100_000, step=10_000)
    with bc2:
        bt_period  = st.selectbox("Period",     ["1y","2y","5y"], index=1, key="bt_per")
        bt_pos     = st.slider("Position size %", 5, 25, 10) / 100
        bt_maxpos  = st.slider("Max positions",   3, 20, 8)

    if st.button("▶ Run Backtest", type="primary"):
        with st.spinner(f"Backtesting {bt_strat} on {bt_index.upper()}…"):
            from screener.fetch       import fetch_bulk, get_index_symbols
            from backtest.engine      import BacktestEngine
            from backtest.strategies  import get_strategy

            symbols = get_index_symbols(bt_index)
            data    = fetch_bulk(symbols, period=bt_period)
            engine  = BacktestEngine(
                strategy       = get_strategy(bt_strat),
                data           = data,
                initial_capital= bt_capital,
                position_size  = bt_pos,
                max_positions  = bt_maxpos,
            )
            result = engine.run()

        s = result.summary()
        cols = st.columns(4)
        metrics = [
            ("Total Return", s["total_return"], "📈"),
            ("CAGR",         s["cagr"],         "📊"),
            ("Sharpe",       s["sharpe"],        "⚖️"),
            ("Max Drawdown", s["max_drawdown"],  "📉"),
            ("Win Rate",     s["win_rate"],      "🎯"),
            ("Profit Factor",s["profit_factor"], "💰"),
            ("Avg Win",      s["avg_win"],       "✅"),
            ("Total Trades", s["total_trades"],  "🔄"),
        ]
        for i, (lbl, val, ico) in enumerate(metrics):
            cols[i%4].metric(f"{ico} {lbl}", val)

        # Equity curve
        if not result.equity_curve.empty:
            eq_df = pd.DataFrame({
                "Portfolio":  result.equity_curve,
                "Benchmark":  result.benchmark.reindex(result.equity_curve.index),
            }).dropna()
            fig_eq = px.line(eq_df, title="Portfolio vs Benchmark")
            fig_eq.update_layout(height=360)
            st.plotly_chart(fig_eq, use_container_width=True)

        # Trades table
        if result.trades:
            tdf = pd.DataFrame([{
                "Symbol":    t.symbol,
                "Entry":     t.entry_date.date() if t.entry_date else "",
                "Exit":      t.exit_date.date()  if t.exit_date  else "Open",
                "Entry ₹":   f"₹{t.entry_price:.2f}",
                "Exit ₹":    f"₹{t.exit_price:.2f}" if t.exit_price else "—",
                "P&L":       f"₹{t.pnl:+,.0f}",
                "Return %":  f"{t.pnl_pct:+.1f}%",
                "Win":       "✅" if t.is_winner else "❌",
            } for t in result.trades])
            st.dataframe(tdf, use_container_width=True, height=350)


# ════════════════════════════════════════════════════════
# TAB 8 — PORTFOLIO
# ════════════════════════════════════════════════════════
with tab_port:
    st.subheader("💼 Portfolio Tracker")
    st.markdown("Track your NSE holdings with live P&L, XIRR, and current screener signals.")

    if "portfolio" not in st.session_state:
        from portfolio.tracker import Portfolio
        st.session_state.portfolio = Portfolio("My Portfolio")

    port = st.session_state.portfolio

    with st.expander("➕ Add Holding"):
        pa, pb, pc, pd_ = st.columns(4)
        with pa: p_sym   = st.text_input("Symbol",     "RELIANCE", key="p_sym")
        with pb: p_qty   = st.number_input("Qty",      1, step=1,   key="p_qty")
        with pc: p_price = st.number_input("Buy Price",100.0,        key="p_price")
        with pd_:p_date  = st.date_input("Buy Date",                 key="p_date")
        if st.button("Add"):
            port.add(p_sym.upper(), int(p_qty), float(p_price), p_date)
            st.success(f"Added {p_sym.upper()}")

    with st.expander("📂 Load from CSV"):
        uploaded = st.file_uploader("Upload portfolio CSV", type="csv")
        if uploaded:
            import io, tempfile, os
            from portfolio.tracker import Portfolio as P2
            with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
                tmp.write(uploaded.read())
                tmp_path = tmp.name
            st.session_state.portfolio = P2.from_csv(tmp_path, "Loaded Portfolio")
            os.unlink(tmp_path)
            st.success("Portfolio loaded!")
            port = st.session_state.portfolio

    if port.holdings:
        if st.button("🔄 Refresh P&L"):
            with st.spinner("Fetching live prices…"):
                summary = port.summary()

            c1,c2,c3,c4,c5 = st.columns(5)
            pnl_delta = f"{summary['total_pnl_pct']:+.1f}%"
            c1.metric("Invested",    f"₹{summary['total_invested']:,.0f}")
            c2.metric("Current",     f"₹{summary['current_value']:,.0f}")
            c3.metric("Total P&L",   f"₹{summary['total_pnl']:+,.0f}", pnl_delta)
            c4.metric("XIRR",        f"{summary['xirr']:+.1f}%")
            c5.metric("Positions",   summary["open_positions"])

            hdf = pd.DataFrame(summary["holdings"])
            if not hdf.empty:
                fig_port = px.bar(
                    hdf.sort_values("pnl_pct"),
                    x="symbol", y="pnl_pct",
                    color="pnl_pct",
                    color_continuous_scale="RdYlGn",
                    title="P&L % by Holding",
                    labels={"pnl_pct":"P&L %","symbol":"Symbol"},
                )
                fig_port.update_layout(height=320, showlegend=False)
                st.plotly_chart(fig_port, use_container_width=True)
                st.dataframe(hdf, use_container_width=True, hide_index=True)

        if st.button("📊 Check Screener Signals on Holdings"):
            with st.spinner("Running screener on your holdings…"):
                sig_df = port.screener_signals()
            if not sig_df.empty:
                st.dataframe(sig_df, use_container_width=True, hide_index=True)

        buf = port.to_csv if callable(getattr(port, "to_csv", None)) else None
        import io as _io
        csv_buf = _io.StringIO()
        # Simple CSV export
        rows = []
        for h in port.holdings:
            rows.append({
                "symbol":h.symbol,"qty":h.qty,"buy_price":h.buy_price,
                "buy_date":str(h.buy_date),"sell_price":h.sell_price or "",
                "sell_date":str(h.sell_date) if h.sell_date else "","notes":h.notes,
            })
        if rows:
            port_csv = pd.DataFrame(rows).to_csv(index=False)
            st.download_button("⬇️ Download Portfolio CSV", port_csv,
                               file_name="my_portfolio.csv", mime="text/csv")
    else:
        st.info("Add holdings using the form above, or upload a CSV.")


# ════════════════════════════════════════════════════════
# TAB 9 — FII / DII
# ════════════════════════════════════════════════════════
with tab_fii:
    st.subheader("🏦 FII / DII Institutional Flow")

    if st.button("📥 Load FII/DII Data (last 30 days)"):
        with st.spinner("Fetching from NSE…"):
            from screener.fetch import fetch_fii_dii
            fii_df = fetch_fii_dii(days=30)

        if fii_df.empty:
            st.warning("NSE may be rate-limiting. Try again in a few seconds.")
        else:
            c1, c2 = st.columns(2)
            fii_net = fii_df["fii_net"].sum()
            dii_net = fii_df["dii_net"].sum()
            c1.metric("FII Net 30D (₹Cr)", f"₹{fii_net:+,.0f}",
                      "Buying" if fii_net>0 else "Selling")
            c2.metric("DII Net 30D (₹Cr)", f"₹{dii_net:+,.0f}",
                      "Buying" if dii_net>0 else "Selling")

            fig_fii = go.Figure()
            fig_fii.add_bar(x=fii_df["date"], y=fii_df["fii_net"],
                            name="FII Net", marker_color=["#1D9E75" if v>0 else "#D85A30"
                                                          for v in fii_df["fii_net"]])
            fig_fii.add_bar(x=fii_df["date"], y=fii_df["dii_net"],
                            name="DII Net", marker_color=["#534AB7" if v>0 else "#FAC775"
                                                          for v in fii_df["dii_net"]])
            fig_fii.update_layout(barmode="group", height=360,
                                   title="FII vs DII Daily Net Flow (₹ Crore)")
            st.plotly_chart(fig_fii, use_container_width=True)
            st.dataframe(fii_df, use_container_width=True)


# ════════════════════════════════════════════════════════
# TAB 10 — ABOUT
# ════════════════════════════════════════════════════════
with tab_about:
    st.markdown("""
## 🇮🇳 IndiaScreen v0.2 — Complete Setup Guide

### 1. Install
```bash
pip install indiascreen[all]
```

### 2. Run the dashboard
```bash
streamlit run ui/app.py
```

### 3. CLI usage
```bash
# Basic screen
indiascreen run --index nifty50 --rsi-max 50 --pe-max 25 --above-200dma

# With output file
indiascreen run --index nifty500 --preset value --output signals.csv

# Telegram test
export TELEGRAM_TOKEN=your_token
export TELEGRAM_CHAT_ID=your_chat_id
python -m alerts.telegram_bot --test
```

### 4. Python API
```python
from indiascreen import Screener, NLScreener, Portfolio

# Standard screen
s = Screener(index="nifty50")
results = s.run(rsi_max=50, pe_max=25, above_200dma=True)

# NL screen
nls = NLScreener()  # set ANTHROPIC_API_KEY for full AI mode
results = nls.run("oversold banking stocks with low P/E")

# Portfolio
p = Portfolio()
p.add("RELIANCE", qty=10, buy_price=2400, buy_date="2024-01-15")
summary = p.summary()
print(summary["xirr"])
```

### 5. What's new in v0.2
| Feature | Description |
|---------|-------------|
| ⚡ Live quotes | Real-time NSE prices via NSE website API |
| ⚙️ Option chain | PCR, max pain, OI buildup, IV skew |
| 🕯 Pattern detection | 15 candlestick patterns incl. Morning Star, Engulfing |
| 🤖 NL screener | Plain English → structured filters (uses Claude API) |
| 🔄 Sector rotation | Heatmap + leading/lagging sectors |
| 💼 Portfolio tracker | P&L, XIRR, Nifty benchmark, screener signals overlay |
| 📰 News sentiment | Google News RSS sentiment scoring |
| 👑 Promoter tracker | Shareholding change + pledge level alerts |
| 💾 SQLite cache | Instant repeat runs, historical signal database |
| 🤖 Telegram bot | Daily alerts via GitHub Actions |
| 📦 PyPI ready | pip install indiascreen |

### 6. Telegram bot setup
1. Message `@BotFather` on Telegram → `/newbot` → copy token
2. Open your bot and send any message
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` → find your chat_id
4. Add `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` to GitHub repo secrets
5. Push to main branch — GitHub Actions will run daily at 4 PM IST

**License:** MIT · **GitHub:** github.com/yourusername/indiascreen
""")
