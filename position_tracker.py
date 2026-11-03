import os
import json
import re
import time
from datetime import datetime
import requests
import yfinance as yf
import pandas as pd

# --- 1. Load Configurations ---
def load_config(config_path="config.json") -> dict:
    if not os.path.exists(config_path):
        return {"ai": {"base_url": "http://localhost:8000/v1", "api_key": "", "model": "qwen35-think"}}
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()
AI_CONFIG = CONFIG.get("ai", {})

def load_and_enrich_my_stocks(json_path="my_stocks.json") -> list:
    """อ่าน my_stocks.json และคำนวณค่า SL/TP ตามกฎ Swing Trade อัตโนมัติ"""
    if not os.path.exists(json_path):
        print(f"⚠️ ไม่พบไฟล์ {json_path} กรุณาสร้างไฟล์ข้อมูลหุ้นก่อนครับ")
        return []
    
    with open(json_path, "r", encoding="utf-8") as f:
        raw_stocks = json.load(f)
        
    enriched_stocks = []
    for stock in raw_stocks:
        buy_p = float(stock["buy_price"])
        
        initial_sl = stock.get("initial_sl", round(buy_p * 0.98, 2))       # Stop Loss -2%
        tp1 = stock.get("tp1", round(buy_p * 1.05, 2))                    # Take Profit 1 +5%
        tp2_lock_sl = stock.get("tp2_lock_sl", round(buy_p * 1.03, 2))    # Lock Profit SL +3%
        tp1_hit = stock.get("tp1_hit", False)                             # สถานะขายไม้แรก
        
        enriched_stocks.append({
            "symbol": stock["symbol"],
            "buy_price": buy_p,
            "buy_date": stock.get("buy_date", ""),
            "shares": stock.get("shares", 0),
            "initial_sl": initial_sl,
            "tp1": tp1,
            "tp2_lock_sl": tp2_lock_sl,
            "tp1_hit": tp1_hit
        })
        
    return enriched_stocks

# --- 2. Technical & Exit Strategy Checker (คำนวณ RVOL, EMA Position, Tech Score) ---
def analyze_position_technical(stock_info: dict) -> dict:
    # 1. แปลง Symbol ให้เป็นตัวพิมพ์ใหญ่อัตโนมัติ (ป้องกัน nvda -> NVDA)
    symbol = str(stock_info["symbol"]).upper().strip()
    buy_price = float(stock_info["buy_price"])
    initial_sl = stock_info["initial_sl"]
    tp1 = stock_info["tp1"]
    tp2_lock_sl = stock_info.get("tp2_lock_sl", round(buy_price * 1.03, 2))
    tp1_hit = stock_info.get("tp1_hit", False)

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="3mo")
        
        if df.empty or len(df) < 20:
            return {"symbol": symbol, "status": "Error: Insufficient Data"}

        # 2. จัดการโครงสร้าง MultiIndex Column ของ yfinance
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # 3. ตรวจสอบและดึงราคาปิด (ป้องกันค่า NaN)
        close_series = df['Close'].dropna()
        if close_series.empty:
            return {"symbol": symbol, "status": "Error: Price Data NaN"}
            
        current_price = float(close_series.iloc[-1])
        
        # คำนวณ Indicators
        df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
        df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
        df['Vol_SMA20'] = df['Volume'].rolling(window=20).mean()

        ema20 = float(df['EMA20'].iloc[-1])
        ema50 = float(df['EMA50'].iloc[-1])
        prev_close = float(close_series.iloc[-2]) if len(close_series) >= 2 else current_price

        # คำนวณ RVOL
        current_vol = float(df['Volume'].iloc[-1])
        avg_vol20 = float(df['Vol_SMA20'].iloc[-1])
        rvol = round(current_vol / avg_vol20, 2) if avg_vol20 > 0 else 1.0

        # ตำแหน่ง EMA Position
        if current_price >= ema20:
            ema_pos = "> EMA20"
        elif ema20 > current_price >= ema50:
            ema_pos = "Between 20-50"
        else:
            ema_pos = "< EMA50"

        # คำนวณ Base Score (0 - 100)
        tech_score = 50
        if current_price >= ema20:
            tech_score += 25
        elif current_price >= ema50:
            tech_score += 10
        else:
            tech_score -= 20

        if rvol >= 1.5:
            tech_score += 25
        elif rvol >= 1.0:
            tech_score += 15
        else:
            tech_score += 5

        # คำนวณ % P&L
        pnl_pct = ((current_price - buy_price) / buy_price) * 100

        # Logic ตัดสินใจ Action
        action = "HOLD"
        action_color = "⚪"
        reason = "ราคาอยู่ในกรอบปกติ ถือครองตามแผน"

        if not tp1_hit and current_price <= initial_sl:
            action = "CUT LOSS (Sell All)"
            action_color = "🔴"
            reason = f"ราคาปิด (${current_price:.2f}) หลุด SL แรกเริ่ม ที่ ${initial_sl:.2f}"
        elif not tp1_hit and current_price >= tp1:
            action = "TAKE PROFIT 1 (Sell 50%)"
            action_color = "🟢"
            reason = f"ราคาถึงเป้า TP1 ที่ ${tp1:.2f} ขายล็อกกำไร 50%"
        elif tp1_hit:
            current_sl = max(tp2_lock_sl, ema20)
            if current_price <= current_sl:
                action = "TAKE PROFIT 2 (Sell Remaining 50%)"
                action_color = "🔵"
                reason = f"ราคาปิด (${current_price:.2f}) หลุด Trailing Stop (${current_sl:.2f})"
            else:
                action = "LET PROFIT RUN"
                action_color = "🟢"
                reason = f"ถือครองต่อตาม Trailing Stop EMA20 (${ema20:.2f})"
        elif current_price < ema20 and current_price < prev_close:
            action_color = "🟡"
            reason += f" | ⚠️ เตือน: ราคาหลุด EMA20 (${ema20:.2f})"

        return {
            "symbol": symbol,
            "buy_price": buy_price,
            "current_price": round(current_price, 2),
            "pnl_pct": round(pnl_pct, 2),
            "ema20": round(ema20, 2),
            "ema50": round(ema50, 2),
            "rvol": rvol,
            "ema_pos": ema_pos,
            "tech_score": tech_score,
            "initial_sl": initial_sl,
            "tp1": tp1,
            "action": f"{action_color} {action}",
            "tech_reason": reason,
            "tp1_hit": tp1_hit
        }
    except Exception as e:
        return {"symbol": symbol, "status": f"Error: {e}"}

# --- 3. AI Emergency News Risk Checker & Final Score Adjustment ---
def fetch_ticker_news(symbol: str) -> list:
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news or []
        cleaned = []
        for n in news[:3]:
            cleaned.append({
                "title": n.get("title") or n.get("headline") or "",
                "summary": (n.get("summary") or n.get("text") or "")[:400]
            })
        return cleaned
    except:
        return []

def check_emergency_news_with_ai(symbol: str, news_list: list) -> dict:
    if not news_list:
        return {"has_emergency_red_flag": False, "news_reason": "ไม่มีข่าวใหม่", "news_score_impact": 0}

    news_text = "\n".join([f"- {n['title']}: {n['summary']}" for n in news_list])
    prompt = f"""
คุณคือระบบเฝ้าระวังความเสี่ยงวิกฤต (Emergency Exit Filter) สำหรับหุ้นที่ถือครองอยู่: **{symbol}**

ข่าวล่าสุด:
{news_text}

ประเมินว่ามี "ข่าวร้ายวิกฤตทันที" ที่ต้อง **ขายทิ้งทันทีโดยไม่รอ Technical SL** หรือไม่? 
(เช่น โดนฟ้องร้องคดีอาญา, งบออกแย่กว่าคาดมหาศาล, โดน Downgrade แบบ panic, ผู้บริหารฉ้อโกง)

ตอบกลับเป็น JSON เท่านั้น:
{{
    "has_emergency_red_flag": false,
    "news_score_impact": 0,
    "news_reason": "อธิบายสั้นๆ 1 ประโยค"
}}

*คำอธิบาย news_score_impact: ถ้าข่าวเป็นบวกใส่ 10, ข่าวปกติใส่ 0, ข่าวลบปานกลางใส่ -20, ข่าวลบวิกฤตใส่ -50*
"""
    try:
        text_resp = requests.post(
            f"{AI_CONFIG.get('base_url')}/chat/completions",
            headers={"Authorization": f"Bearer {AI_CONFIG.get('api_key', '')}", "Content-Type": "application/json"},
            json={"model": AI_CONFIG.get("model", "qwen35-think"), "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            timeout=120
        ).json()["choices"][0]["message"]["content"]
        
        # Clean <think> tags
        text_resp = re.sub(r'<think>.*?</think>', '', text_resp, flags=re.DOTALL).strip()
        match = re.search(r'\{.*\}', text_resp, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return {"has_emergency_red_flag": False, "news_reason": "AI Scan Failed", "news_score_impact": 0}

# --- 4. Main Workflow ---
def main():
    print("=" * 110)
    print("🛡️ PORTFOLIO POSITION TRACKER & EXIT STRATEGY EVALUATOR")
    print("=" * 110)

    my_stocks = load_and_enrich_my_stocks("my_stocks.json")
    if not my_stocks:
        return

    print(f"📦 Tracking {len(my_stocks)} positions in portfolio...\n")
    
    tracking_results = []

    for stock in my_stocks:
        sym = stock["symbol"]
        print(f"🔎 Checking {sym} (Entry: ${stock['buy_price']})...")
        
        # 1. เช็ค Technical & Volume & EMA Position
        tech_eval = analyze_position_technical(stock)
        
        # 2. เช็ค Emergency News ด้วย Local AI
        news_list = fetch_ticker_news(sym)
        news_eval = check_emergency_news_with_ai(sym, news_list)

        # 3. รวม Score และการตัดสินใจ (Consolidation)
        base_score = tech_eval.get("tech_score", 50)
        news_impact = news_eval.get("news_score_impact", 0)
        final_score = max(0, min(100, base_score + news_impact))  # คำนวณ Score ให้อยู่ในระดับ 0 - 100

        final_action = tech_eval.get("action", "HOLD")
        reason = tech_eval.get("tech_reason", "")

        if news_eval.get("has_emergency_red_flag", False):
            final_action = "🚨 EMERGENCY EXIT (Sell All)"
            reason = f"ข่าวร้ายวิกฤต: {news_eval.get('news_reason')}"
            final_score = min(final_score, 20)

        tracking_results.append({
            "symbol": sym,
            "buy_price": stock['buy_price'],
            "current_price": tech_eval.get('current_price', 0),
            "score": f"{final_score}/100",
            "rvol": f"{tech_eval.get('rvol', 1.0)}x",
            "ema_pos": tech_eval.get('ema_pos', 'N/A'),
            "sl_price": tech_eval.get('initial_sl', 0),
            "tp1_price": tech_eval.get('tp1', 0),
            "pnl": f"{tech_eval.get('pnl_pct', 0):+.2f}%",
            "action": final_action,
            "reason": reason
        })
        time.sleep(0.5)

    # แสดงผลตารางติดตามพอร์ตแบบเพิ่มคอลัมน์ Score, RVOL, EMA Pos
    print("\n" + "=" * 110)
    print(f"{'Symbol':<7} | {'Entry':<8} | {'Current':<8} | {'Score':<8} | {'RVOL':<7} | {'EMA Pos':<14} | {'P&L (%)':<8} | {'Action':<28}")
    print("-" * 110)
    
    for r in tracking_results:
        print(f"{r['symbol']:<7} | ${r['buy_price']:<7} | ${r['current_price']:<7} | {r['score']:<8} | {r['rvol']:<7} | {r['ema_pos']:<14} | {r['pnl']:<8} | {r['action']}")
        print(f"  └─ Decision Reason: {r['reason']}\n")
        
    print("=" * 110)
    print("✅ Tracking completed. Execute sell orders accordingly if any actions triggered.")

if __name__ == "__main__":
    main()