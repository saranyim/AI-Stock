import os
import json
import time
import re
import random
import requests
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# --- 1. Page Configuration & Custom CSS ---
st.set_page_config(
    page_title="AI Swing Trade Terminal",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- 2. Load Configuration ---
@st.cache_data(ttl=3600)
def load_config(config_path="config.json") -> dict:
    if not os.path.exists(config_path):
        return {
            "ai": {
                "base_url": "http://localhost:11434/v1",
                "api_key": "ollama",
                "model": "qwen2.5:7b"
            }
        }
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()
AI_CONFIG = CONFIG.get("ai", {})

# Quick News Bookmarks
NEWS_SOURCES = [
    {"name": "Yahoo Finance", "url": "https://finance.yahoo.com"},
    {"name": "Reuters Business", "url": "https://www.reuters.com/business"},
    {"name": "CNBC Markets", "url": "https://www.cnbc.com/markets"},
    {"name": "MarketWatch", "url": "https://www.marketwatch.com"},
    {"name": "Investing.com", "url": "https://www.investing.com/news"},
    {"name": "Bloomberg 🔒", "url": "https://www.bloomberg.com/markets"},
    {"name": "Seeking Alpha 🔒", "url": "https://seekingalpha.com"}
]

# --- 3. Helper Functions: Portfolio & Trade History ---
def load_my_stocks(json_path="my_stocks.json") -> list:
    if not os.path.exists(json_path):
        return []
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_my_stocks(stocks_data: list, json_path="my_stocks.json"):
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(stocks_data, f, indent=2, ensure_ascii=False)

def log_trade_history(stock_info: dict, action_type: str, json_path="trade_history.json"):
    history = []
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []
    
    stock_info["close_date"] = time.strftime("%Y-%m-%d %H:%M:%S")
    stock_info["close_action"] = action_type
    history.append(stock_info)
    
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

# --- 4. Market Data Engine (Safe Fetching with Caching) ---
@st.cache_data(ttl=900)
def check_market_context() -> dict:
    try:
        qqq = yf.download("QQQ", period="1y", interval="1d", auto_adjust=True, progress=False)
        if isinstance(qqq.columns, pd.MultiIndex):
            qqq.columns = qqq.columns.get_level_values(0)
        qqq["EMA50"] = qqq["Close"].ewm(span=50, adjust=False).mean()
        close_p = float(qqq["Close"].iloc[-1])
        ema50_p = float(qqq["EMA50"].iloc[-1])
        return {"status": close_p > ema50_p, "close": close_p, "ema50": ema50_p}
    except Exception:
        return {"status": False, "close": 0.0, "ema50": 0.0}

@st.cache_data(ttl=1800)
def load_sp500_list(csv_path="sp500_companies.csv") -> list:
    if not os.path.exists(csv_path):
        return ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "AMD", "NXPI", "PYPL", "QCOM", "CNC", "BAX", "HCA", "IQV"]
    try:
        df = pd.read_csv(csv_path)
        col = "Symbol" if "Symbol" in df.columns else "Ticker"
        return df[col].dropna().astype(str).str.replace(".", "-", regex=False).tolist()
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"]

# --- 5. Targeted News Fetcher with Links ---
def fetch_ticker_news_with_links(symbol: str, limit: int = 5) -> list:
    cleaned_news = []
    try:
        ticker = yf.Ticker(symbol)
        yf_news = ticker.news
        if yf_news:
            for item in yf_news[:limit]:
                title = item.get("title") or item.get("headline") or "No Title"
                summary = (item.get("summary") or item.get("text") or "")[:500]
                url = item.get("link") or item.get("url") or f"https://finance.yahoo.com/quote/{symbol}/news"
                provider = item.get("provider", {}).get("displayName") or "Yahoo Finance"
                
                cleaned_news.append({
                    "title": title,
                    "summary": summary,
                    "url": url,
                    "publisher": provider
                })
    except Exception:
        pass
    return cleaned_news

# --- 6. Local AI Risk Filter ---
def analyze_news_risk_with_ai(symbol: str, news_list: list) -> dict:
    if not news_list:
        return {
            "risk_status": "PASS",
            "reasoning": "ไม่พบข่าวใหม่ล่าสุด - ใช้องค์ประกอบทางเทคนิคเป็นหลัก",
            "has_immediate_red_flags": False
        }
    
    news_text = "\n".join([f"- [{n['publisher']}] {n['title']}: {n['summary']}" for n in news_list])
    prompt = f"""
คุณคือระบบกรองความเสี่ยงจากข่าวสาร (News Risk Filter) สำหรับ Swing Trade (ถือ 3-10 วัน)

วิเคราะห์ข่าวของหุ้น: **{symbol}** ต่อไปนี้:
{news_text}

โจทย์ประเมิน (เน้นระยะสั้น 1-7 วันเท่านั้น):
1. มี Red Flags ลบแรงทันทีหรือไม่? (เช่น งบแย่กว่าคาดมาก, โดน Downgrade, โดนฟ้องร้อง, ผู้บริหารลาออก)
2. มี Catalyst บวกระยะสั้นหรือไม่? (เช่น งบดีกว่าคาดมาก, ปรับเพิ่มเป้า, โดน Upgrade)

ตอบเป็น JSON เท่านั้น:
{{
    "risk_status": "PASS", 
    "reasoning": "อธิบายสั้นๆ 1-2 ประโยค",
    "has_immediate_red_flags": false
}}
*คำอธิบาย risk_status: "PASS" (ไม่มีข่าวลบ), "WARNING" (ข่าวปนกัน), "REJECT" (มีข่าวลบแรง)*
"""
    try:
        resp = requests.post(
            f"{AI_CONFIG.get('base_url')}/chat/completions",
            headers={
                "Authorization": f"Bearer {AI_CONFIG.get('api_key', 'ollama')}",
                "Content-Type": "application/json"
            },
            json={
                "model": AI_CONFIG.get("model", "qwen2.5:7b"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 500
            },
            timeout=90
        )
        if resp.status_code == 200:
            raw_content = resp.json()["choices"][0]["message"]["content"]
            raw_content = re.sub(r'<think>.*?</think>', '', raw_content, flags=re.DOTALL).strip()
            match = re.search(r'\{.*\}', raw_content, re.DOTALL)
            if match:
                return json.loads(match.group())
    except Exception:
        pass
    return {"risk_status": "WARNING", "reasoning": "AI Scan Timeout / Parsing Error", "has_immediate_red_flags": False}

# --- 7. Plotly 5-Day Interactive Candlestick Chart ---
def render_5day_chart(symbol: str, buy_p: float = None, sl_p: float = None, tp_p: float = None):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="3mo", interval="1d")
        if df.empty or len(df) < 5:
            st.error(f"ไม่สามารถโหลดข้อมูลกราฟสำหรับ {symbol}")
            return

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
        df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
        df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()

        # ตัดข้อมูลเฉพาะ 5 วันทำการล่าสุด
        df_5d = df.tail(5)

        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            vertical_spacing=0.08, subplot_titles=(f"{symbol} - 5-Day Candlestick & EMA", "Volume"),
            row_width=[0.3, 0.7]
        )

        # Candlestick
        fig.add_trace(go.Candlestick(
            x=df_5d.index.strftime('%Y-%m-%d'),
            open=df_5d['Open'], high=df_5d['High'],
            low=df_5d['Low'], close=df_5d['Close'],
            name="Price"
        ), row=1, col=1)

        # EMAs
        fig.add_trace(go.Scatter(x=df_5d.index.strftime('%Y-%m-%d'), y=df_5d['EMA20'], mode='lines', name='EMA20', line=dict(color='orange', width=1.5)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df_5d.index.strftime('%Y-%m-%d'), y=df_5d['EMA50'], mode='lines', name='EMA50', line=dict(color='blue', width=1.5)), row=1, col=1)

        # Draw Target Lines if provided
        if buy_p:
            fig.add_hline(y=buy_p, line_dash="dash", line_color="cyan", annotation_text=f"Buy: ${buy_p:.2f}", row=1, col=1)
        if sl_p:
            fig.add_hline(y=sl_p, line_dash="dash", line_color="red", annotation_text=f"SL: ${sl_p:.2f}", row=1, col=1)
        if tp_p:
            fig.add_hline(y=tp_p, line_dash="dash", line_color="green", annotation_text=f"TP1: ${tp_p:.2f}", row=1, col=1)

        # Volume
        colors = ['green' if c >= o else 'red' for c, o in zip(df_5d['Close'], df_5d['Open'])]
        fig.add_trace(go.Bar(x=df_5d.index.strftime('%Y-%m-%d'), y=df_5d['Volume'], marker_color=colors, name="Volume"), row=2, col=1)

        fig.update_layout(height=420, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, use_container_width=True)

    except Exception as e:
        st.error(f"Error rendering chart: {e}")

# --- 8. UI App Main Layout & Navigation ---
st.title("📈 Quantitative & AI Swing Trade Terminal")

# Sidebar Status & Links
st.sidebar.header("🕹️ Control Panel")

# Market Context Banner
mkt = check_market_context()
if mkt["status"]:
    st.sidebar.success(f"🟢 QQQ (${mkt['close']:.2f}) > EMA50 (${mkt['ema50']:.2f})\n\nตลาดพร้อมสำหรับฝั่ง BUY")
else:
    st.sidebar.warning(f"🔴 QQQ (${mkt['close']:.2f}) < EMA50 (${mkt['ema50']:.2f})\n\nตลาดพักฐาน - แนะนำลด Position Size 50%")

st.sidebar.markdown("---")
st.sidebar.subheader("🌐 Financial News Bookmarks")
for src in NEWS_SOURCES:
    st.sidebar.markdown(f"🔗 [{src['name']}]({src['url']})")

# Navigation Tabs
tab1, tab2, tab3 = st.tabs(["🔍 Market Scanner (สแกนซื้อ)", "🛡️ Position Tracker (พอร์ตถือครอง)", "⚙️ Settings"])

# -----------------------------------------------------------------------------
# TAB 1: MARKET SCANNER
# -----------------------------------------------------------------------------
with tab1:
    st.subheader("🔍 สแกนคัดกรองหุ้นสเปกเกรด A+ (S&P 500)")
    
    col_sc1, col_sc2 = st.columns([2, 1])
    with col_sc1:
        scan_limit = st.slider("จำนวนหุ้นที่จะสแกน (จาก S&P 500):", min_value=10, max_value=500, value=50, step=10)
    with col_sc2:
        st.write("")
        st.write("")
        run_scan_btn = st.button("🚀 เริ่มรันระบบสแกนประจำวัน", type="primary")

    if run_scan_btn:
        sp_tickers = load_sp500_list()[:scan_limit]
        st.info(f"กำลังสแกนสัญญาณเทคนิค {len(sp_tickers)} ตัว (เปิดระบบ Anti-Flood Delay 0.3s)...")
        
        progress_bar = st.progress(0)
        matched_stocks = []

        for idx, ticker in enumerate(sp_tickers):
            progress_bar.progress((idx + 1) / len(sp_tickers))
            try:
                df = yf.download(ticker, period="1y", interval="1d", auto_adjust=True, progress=False)
                time.sleep(0.3)  # Anti-Flood Delay
                
                if df.empty or len(df) < 200: continue
                if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)

                df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
                df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
                df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()
                df["Vol_SMA20"] = df["Volume"].rolling(20).mean()

                close = float(df["Close"].iloc[-1])
                open_p = float(df["Open"].iloc[-1])
                low = float(df["Low"].iloc[-1])
                vol = float(df["Volume"].iloc[-1])
                vol_sma = float(df["Vol_SMA20"].iloc[-1])
                ema20 = float(df["EMA20"].iloc[-1])
                ema50 = float(df["EMA50"].iloc[-1])
                ema200 = float(df["EMA200"].iloc[-1])
                rvol = round(vol / vol_sma, 2) if vol_sma > 0 else 1.0

                # A+ Setup Filters
                if close > (ema200 * 1.03) and ema20 > ema50 and (low <= (ema20 * 1.008) or low <= (ema50 * 1.008)) and (close > open_p * 1.008) and (close >= ema20) and vol >= (vol_sma * 1.20):
                    matched_stocks.append({
                        "symbol": ticker, "close": round(close, 2), "rvol": rvol,
                        "ema20": round(ema20, 2), "ema50": round(ema50, 2),
                        "initial_sl": round(close * 0.98, 2), "tp1": round(close * 1.05, 2)
                    })
            except Exception:
                continue

        st.success(f"พบหุ้นเข้าเกณฑ์ A+ ทั้งหมด {len(matched_stocks)} ตัว!")

        if matched_stocks:
            st.write("---")
            st.subheader("🤖 ให้ AI วิเคราะห์ความเสี่ยงจากข่าวสาร")
            
            scanned_results = []
            for item in matched_stocks:
                sym = item["symbol"]
                news = fetch_ticker_news_with_links(sym, limit=4)
                ai_res = analyze_news_risk_with_ai(sym, news)
                
                status = ai_res.get("risk_status", "PASS")
                action = "🟢 STRONG BUY" if status == "PASS" and item["rvol"] >= 1.5 else ("🟡 BUY" if status == "PASS" else "🔴 REJECT")
                
                item_data = {**item, **ai_res, "news_items": news, "action": action}
                scanned_results.append(item_data)

            st.session_state["scan_results"] = scanned_results

    # Render Results if Session State has data
    if "scan_results" in st.session_state:
        st.write("---")
        results = st.session_state["scan_results"]
        
        for res in results:
            with st.expander(f"{res['action']} | **{res['symbol']}** (${res['close']}) | RVOL: {res['rvol']}x | Risk: {res['risk_status']}"):
                col_a, col_b = st.columns([1.2, 1])
                
                with col_a:
                    st.write(f"**เหตุผลจาก AI:** {res['reasoning']}")
                    st.write(f"**แผนการเทรด:** Entry: ${res['close']} | SL (-2%): ${res['initial_sl']} | TP1 (+5%): ${res['tp1']}")
                    
                    st.write("**หัวข้อข่าวสารล่าสุด (คลิกอ่านต้นฉบับได้):**")
                    for n in res.get("news_items", []):
                        st.markdown(f"- [{n['publisher']}] [{n['title']}]({n['url']})")
                    
                    # Buy Button Action
                    if st.button(f"➕ BUY & Add {res['symbol']} to Portfolio", key=f"buy_{res['symbol']}"):
                        current_my_stocks = load_my_stocks()
                        if not any(s['symbol'] == res['symbol'] for s in current_my_stocks):
                            new_entry = {
                                "symbol": res['symbol'],
                                "buy_price": res['close'],
                                "buy_date": time.strftime("%Y-%m-%d"),
                                "initial_sl": res['initial_sl'],
                                "tp1": res['tp1'],
                                "tp2_lock_sl": round(res['close'] * 1.03, 2),
                                "tp1_hit": False
                            }
                            current_my_stocks.append(new_entry)
                            save_my_stocks(current_my_stocks)
                            st.success(f"บันทึก {res['symbol']} ลงในพอร์ตเรียบร้อยแล้ว!")
                        else:
                            st.warning(f"หุ้น {res['symbol']} มีอยู่ในพอร์ตแล้ว")

                with col_b:
                    render_5day_chart(res['symbol'], res['close'], res['initial_sl'], res['tp1'])

# -----------------------------------------------------------------------------
# TAB 2: POSITION TRACKER
# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# TAB 2: POSITION TRACKER (ปรับปรุงแก้ KeyError)
# -----------------------------------------------------------------------------
with tab2:
    st.subheader("🛡️ Portfolio Position Tracker & Exit Strategy")
    
    my_stocks = load_my_stocks()
    if not my_stocks:
        st.info("ยังไม่มีรายการหุ้นในพอร์ต (สามารถกดเพิ่มได้จากหน้า Market Scanner)")
    else:
        st.write(f"จำนวนหุ้นที่ถือครองอยู่: **{len(my_stocks)}** ตัว")
        
        portfolio_results = []
        for stock in my_stocks:
            sym = str(stock["symbol"]).upper()
            buy_p = float(stock["buy_price"])
            
            # ใช้ .get() เพื่อป้องกัน KeyError หากไฟล์ my_stocks.json ไม่มีค่านั้นอยู่
            sl_p = float(stock.get("initial_sl", round(buy_p * 0.98, 2)))
            tp1_p = float(stock.get("tp1", round(buy_p * 1.05, 2)))
            tp2_lock_p = float(stock.get("tp2_lock_sl", round(buy_p * 1.03, 2)))
            tp1_hit = stock.get("tp1_hit", False)

            try:
                ticker = yf.Ticker(sym)
                df = ticker.history(period="3mo")
                if df.empty or len(df) < 20:
                    continue

                if isinstance(df.columns, pd.MultiIndex): 
                    df.columns = df.columns.get_level_values(0)
                
                curr_p = float(df['Close'].iloc[-1])
                df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
                df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
                df['Vol_SMA20'] = df['Volume'].rolling(20).mean()
                
                ema20 = float(df['EMA20'].iloc[-1])
                ema50 = float(df['EMA50'].iloc[-1])
                avg_vol = float(df['Vol_SMA20'].iloc[-1])
                rvol = round(float(df['Volume'].iloc[-1]) / avg_vol, 2) if avg_vol > 0 else 1.0
                pnl = round(((curr_p - buy_p) / buy_p) * 100, 2)
                
                # Rule Engine Exit Strategy
                if not tp1_hit and curr_p <= sl_p:
                    action, color = "🔴 CUT LOSS (Sell All)", "red"
                elif not tp1_hit and curr_p >= tp1_p:
                    action, color = "🟢 TAKE PROFIT 1 (Sell 50%)", "green"
                elif tp1_hit and curr_p <= max(tp2_lock_p, ema20):
                    action, color = "🔵 TAKE PROFIT 2 (Sell 50%)", "blue"
                else:
                    action, color = "⚪ HOLD", "gray"
                
                portfolio_results.append({
                    **stock, "symbol": sym, "current_price": round(curr_p, 2),
                    "initial_sl": sl_p, "tp1": tp1_p,
                    "pnl_pct": pnl, "rvol": rvol, "ema20": round(ema20, 2),
                    "action": action, "action_color": color
                })
            except Exception as e:
                st.error(f"Error fetching data for {sym}: {e}")

        # Render Portfolio Cards / Table
        for p in portfolio_results:
            with st.container():
                st.markdown(f"### {p['symbol']} | P&L: **{p['pnl_pct']:+.2f}%** | Action: **{p['action']}**")
                col_p1, col_p2, col_p3 = st.columns([1, 1, 1])
                
                with col_p1:
                    st.write(f"- **Entry Price:** ${p['buy_price']}")
                    st.write(f"- **Current Price:** ${p['current_price']}")
                    st.write(f"- **Entry Date:** {p.get('buy_date', 'N/A')}")
                with col_p2:
                    st.write(f"- **Initial SL (-2%):** ${p['initial_sl']}")
                    st.write(f"- **Target TP1 (+5%):** ${p['tp1']}")
                    st.write(f"- **RVOL:** {p['rvol']}x | **EMA20:** ${p['ema20']}")
                with col_p3:
                    if st.button(f"❌ SELL & Close Position ({p['symbol']})", key=f"sell_{p['symbol']}"):
                        log_trade_history(p, action_type=p['action'])
                        updated_my_stocks = [s for s in my_stocks if str(s['symbol']).upper() != p['symbol']]
                        save_my_stocks(updated_my_stocks)
                        st.success(f"ปิดออเดอร์และลบ {p['symbol']} ออกจากพอร์ตแล้ว!")
                        st.rerun()
                st.write("---")
# -----------------------------------------------------------------------------
# TAB 3: SETTINGS
# -----------------------------------------------------------------------------
with tab3:
    st.subheader("⚙️ System Configuration")
    st.json(CONFIG)
    st.info("ตั้งค่า API และ Local AI Model สามารถอัปเดตได้ผ่านไฟล์ config.json")