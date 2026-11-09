import sys
import os
import time
import json
import re
import warnings
import pandas as pd
import yfinance as yf
import requests

warnings.filterwarnings("ignore")

CONFIG_PATH = "config.json"
INPUT_MD_PATH = "daily_macro_watchlist.md"
MY_STOCKS_PATH = "my_stocks.json"
TRADE_HISTORY_PATH = "trade_history.json"

RISK_CAPITAL_PER_TRADE = 500.0  # จำนวนเงินดอลลาร์ที่ยอมเสียได้ต่อ 1 เทรด (Risk Capital)

# --- 1. LOAD CONFIG & READ MARKDOWN ---
def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {"ai": {"base_url": "http://localhost:11434/v1", "api_key": "ollama", "model": "qwen2.5:7b"}}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def parse_markdown_watchlist() -> dict:
    if not os.path.exists(INPUT_MD_PATH):
        print(f"❌ ไม่พบไฟล์ '{INPUT_MD_PATH}' กรุณารัน Script 1 ช่วงกลางวันก่อน")
        return {"allow_trading": False, "symbols": []}
        
    with open(INPUT_MD_PATH, "r", encoding="utf-8") as f:
        content = f.read()
        
    allow_trading = "ALLOW_BUY" in content and "NO_TRADE_EXECUTION" not in content
    symbols = re.findall(r'### 📌 ([A-Z]+)', content)
    
    return {"allow_trading": allow_trading, "symbols": symbols, "raw_md": content}

def load_my_stocks() -> list:
    if not os.path.exists(MY_STOCKS_PATH):
        return []
    with open(MY_STOCKS_PATH, "r", encoding="utf-8") as f:
        try: return json.load(f)
        except Exception: return []

def save_my_stocks(stocks: list):
    with open(MY_STOCKS_PATH, "w", encoding="utf-8") as f:
        json.dump(stocks, f, indent=2, ensure_ascii=False)

def log_closed_trade(trade: dict, reason: str):
    history = []
    if os.path.exists(TRADE_HISTORY_PATH):
        with open(TRADE_HISTORY_PATH, "r", encoding="utf-8") as f:
            try: history = json.load(f)
            except Exception: history = []
            
    trade_record = {**trade, "closed_at": time.strftime("%Y-%m-%d %H:%M:%S"), "close_reason": reason}
    history.append(trade_record)
    with open(TRADE_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

# --- 2. MARTIN LUK STEP 3: PRE-MARKET & INTRADAY EXECUTION ---
def check_premarket_execution(symbols: list):
    print("=" * 90)
    print("⚡ [MARTIN LUK STEP 3] PRE-MARKET & INTRADAY BREAKOUT EXECUTION")
    print("=" * 90)
    
    if not symbols:
        print("ℹ️ ไม่พบรายชื่อหุ้นแนะนำใน Watchlist ประจำวันนี้")
        return
        
    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            df = ticker.history(period="5d", interval="5m")
            if df.empty: continue
            
            curr_price = float(df['Close'].iloc[-1])
            day_low = float(df['Low'].iloc[-12:].min()) # Low ช่วงเปิดตลาด 1 ชม. แรก
            
            # คำนวณ Risk Per Share & Position Size
            risk_per_share = curr_price - day_low
            if risk_per_share <= 0: risk_per_share = curr_price * 0.02
            
            risk_pct = (risk_per_share / curr_price) * 100
            shares_to_buy = int(RISK_CAPITAL_PER_TRADE / risk_per_share) if risk_per_share > 0 else 0
            
            print(f"📌 หุ้นเฝ้าระวัง: {sym}")
            print(f"   • ราคาปัจจุบัน/Pre-market : ${curr_price:.2f}")
            print(f"   • Stop Loss (Low ของวัน)  : ${day_low:.2f} (ความเสี่ยง {risk_pct:.2f}%)")
            print(f"   • คำนวณ Position Size   : {shares_to_buy} หุ้น (ยอมรับความเสี่ยงสูงสุด ${RISK_CAPITAL_PER_TRADE})")
            print(f"   💡 คำแนะนำ: หากราคาทะลุ High ช่วง 5 นาทีแรก ให้เข้าซื้อ {shares_to_buy} หุ้น\n")
        except Exception as e:
            print(f"⚠️ ไม่สามารถดึงข้อมูล {sym}: {e}")

# --- 3. MARTIN LUK STEP 4: DYNAMIC EXIT PORTFOLIO TRACKER ---
def track_portfolio_positions(my_stocks: list):
    print("=" * 90)
    print("🛡️ [MARTIN LUK STEP 4] PORTFOLIO DYNAMIC EXIT TRACKER")
    print("=" * 90)
    
    if not my_stocks:
        print("ℹ️ ไม่มีหุ้นถือครองในพอร์ต (`my_stocks.json`)")
        return

    updated_portfolio = []
    for stock in my_stocks:
        sym = stock["symbol"]
        entry_p = float(stock["buy_price"])
        
        try:
            df = yf.Ticker(sym).history(period="1mo")
            if df.empty: continue
            
            df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
            curr_p = float(df['Close'].iloc[-1])
            ema9 = float(df['EMA9'].iloc[-1])
            prev_close = float(df['Close'].iloc[-2])
            
            pnl_pct = ((curr_p - entry_p) / entry_p) * 100
            initial_sl = stock.get("initial_sl", entry_p * 0.98)
            
            action = "HOLD"
            # Dynamic Exit Conditions ( Martin Luk Style )
            if curr_p <= initial_sl:
                action = "🔴 CUT LOSS (Sell All)"
                log_closed_trade(stock, action)
            elif curr_p < ema9 and curr_p < prev_close:
                action = "🔵 DYNAMIC EXIT (เห็นแท่งกลับตัวใกล้ EMA9 / หมดแรง ให้ขายล็อกกำไร)"
                log_closed_trade(stock, action)
            elif pnl_pct >= 15.0:
                action = "🟢 TAKE PROFIT (กำไรวิ่งถึงเป้า 7R - ทยอยขายล็อกกำไร)"
                updated_portfolio.append(stock)
            else:
                updated_portfolio.append(stock)
                
            print(f"📦 {sym} | Entry: ${entry_p:.2f} | Current: ${curr_p:.2f} | P&L: {pnl_pct:+.2f}% | EMA9: ${ema9:.2f}")
            print(f"   └─ Action: {action}\n")
        except Exception as e:
            print(f"⚠️ Error tracking {sym}: {e}")
            updated_portfolio.append(stock)
            
    save_my_stocks(updated_portfolio)

# --- 4. MAIN EXECUTION ---
def main():
    print("🌙 [SCRIPT 2] INTRADAY EXECUTION & TRACKER (รันบน Mac)")
    md_data = parse_markdown_watchlist()
    
    if not md_data["allow_trading"]:
        print("🔴 [NO TRADE WARN]: สภาพตลาดมหภาคใน `daily_macro_watchlist.md` สั่งระงับการเข้าซื้อ!")
        print("   ระบบจะทำการติดตามหุ้นในพอร์ตอย่างเดียว โดยไม่สแกนซื้อหุ้นเพิ่ม\n")
    else:
        print("🟢 [MARKET SAFE]: ตลาดมหภาคอนุญาตให้สแกนซื้อ (ALLOW_BUY)\n")
        check_premarket_execution(md_data["symbols"])
        
    my_stocks = load_my_stocks()
    track_portfolio_positions(my_stocks)
    
    print("=" * 90)
    print("🎉 [COMPLETE] สคริปต์ตัวที่ 2 ทำงานเสร็จสิ้น!")

if __name__ == "__main__":
    main()