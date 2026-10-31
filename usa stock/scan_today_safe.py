import sys
import time
import pandas as pd
import yfinance as yf

# บังคับให้ Output Stream รองรับภาษาไทยบน Windows Task Scheduler
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# ตั้งค่า Delay สำหรับการดึงข้อมูลรายตัวเพื่อป้องกัน Rate Limit/IP Block (วินาที)
DELAY_BETWEEN_REQUESTS = 0.3

print("[INFO] กำลังโหลดรายชื่อหุ้น S&P 500 จากไฟล์ Local...")
try:
    df_sp500 = pd.read_csv("sp500_companies.csv")
    col_name = "Symbol" if "Symbol" in df_sp500.columns else "Ticker"
    sp500_tickers = (
        df_sp500[col_name].astype(str).str.replace(".", "-", regex=False).tolist()
    )
    print(
        f"[SUCCESS] โหลดรายชื่อสำเร็จทั้งหมด {len(sp500_tickers)} หุ้นจากไฟล์"
        " sp500_companies.csv"
    )
except FileNotFoundError:
    print(
        "[WARNING] ไม่พบไฟล์ 'sp500_companies.csv' ในเครื่อง ใช้รายชื่อสำรอง..."
    )
    sp500_tickers = [
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "GOOGL",
        "META",
        "TSLA",
        "AVGO",
        "COST",
        "AMD",
    ]
except Exception as e:
    print(f"[ERROR] เกิดข้อผิดพลาดในการอ่านไฟล์: {e}")
    sys.exit(1)

print(
    "\n[SAFE MODE] เริ่มสแกนประจำวัน (เปิดระบบ Delay ชะลอเวลาการดึงข้อมูล)..."
)

# 1. เช็ก Market Filter (QQQ > EMA50)
try:
    qqq = yf.download(
        "QQQ", period="1y", interval="1d", auto_adjust=True, progress=False
    )
    if isinstance(qqq.columns, pd.MultiIndex):
        qqq.columns = qqq.columns.get_level_values(0)
    qqq["EMA50"] = qqq["Close"].ewm(span=50, adjust=False).mean()

    qqq_close = float(qqq["Close"].iloc[-1])
    qqq_ema50 = float(qqq["EMA50"].iloc[-1])
    market_ok = qqq_close > qqq_ema50
except Exception as e:
    print(f"[ERROR] ไม่สามารถดึงข้อมูล QQQ ได้: {e}")
    market_ok = False
    qqq_close, qqq_ema50 = 0.0, 0.0

if not market_ok:
    print(
        f"\n[MARKET WARNING] QQQ ({qqq_close:.2f}) อยู่ต่ำกว่า EMA50"
        f" ({qqq_ema50:.2f})"
    )
    print(
        "[INFO] สภาพตลาดภาพรวมเป็นขาลง/พักฐาน แต่ระบบจะทำการสแกนหาหุ้นเกรด A+"
        " ให้ตามปกติ..."
    )
else:
    print(
        f"\n[MARKET FILTER PASSED] QQQ ({qqq_close:.2f}) ยืนเหนือ EMA50"
        f" ({qqq_ema50:.2f})"
    )

print("กำลังสแกนหาหุ้นเกรด A+ จากทั้ง 500 ตัว (ใช้เวลาประมาณ 3-4 นาที)...\n")

matched_stocks = []

for idx, ticker in enumerate(sp500_tickers, 1):
    if idx % 50 == 0:
        print(f"   ...สแกนผ่านไปแล้ว {idx}/{len(sp500_tickers)} หุ้น")

    try:
        df = yf.download(
            ticker,
            period="1y",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )

        time.sleep(DELAY_BETWEEN_REQUESTS)

        if df.empty or len(df) < 200:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
        df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
        df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()
        df["Vol_SMA20"] = df["Volume"].rolling(20).mean()

        close = float(df["Close"].iloc[-1])
        open_p = float(df["Open"].iloc[-1])
        high = float(df["High"].iloc[-1])
        low = float(df["Low"].iloc[-1])
        vol = float(df["Volume"].iloc[-1])
        vol_sma = float(df["Vol_SMA20"].iloc[-1])

        ema20 = float(df["EMA20"].iloc[-1])
        ema50 = float(df["EMA50"].iloc[-1])
        ema200 = float(df["EMA200"].iloc[-1])

        # 2. เงื่อนไข Quality Filter A+ Setup
        cond1 = close > (ema200 * 1.03)
        cond2 = ema20 > ema50
        cond3 = (
            (low <= (ema20 * 1.008) or low <= (ema50 * 1.008))
            and (close > open_p * 1.008)
            and (close >= ema20)
        )
        cond4 = vol >= (vol_sma * 1.20)

        if cond1 and cond2 and cond3 and cond4:
            rvol = vol / vol_sma if vol_sma > 0 else 1.0

            news_headlines = []
            try:
                t_obj = yf.Ticker(ticker)
                news_data = t_obj.news
                if news_data:
                    for item in news_data[:3]:
                        title = item.get("title", "")
                        if title:
                            news_headlines.append(title)
            except Exception:
                pass

            matched_stocks.append({
                "ticker": ticker,
                "close": close,
                "open": open_p,
                "high": high,
                "low": low,
                "ema20": ema20,
                "ema50": ema50,
                "ema200": ema200,
                "rvol": rvol,
                "tp1": close * 1.05,
                "sl_initial": close * 0.98,
                "sl_step2": close * 1.03,
                "news": news_headlines,
            })
    except Exception:
        continue

# 3. เจนไฟล์ Prompt ผลลัพธ์
if matched_stocks:
    prompt_text = (
        "==================================================\n PROMPT สำหรับ"
        " COPY ส่งต่อให้ CHAT AI / LOCAL LLM\n==================================================\n\n"
    )

    prompt_text += (
        "คุณคือผู้เชี่ยวชาญด้านการลงทุนสไตล์ Swing Trade (Quantitative & News"
        " Sentiment Analyst)\n"
    )
    prompt_text += (
        "โปรดวิเคราะห์ข้อมูลหุ้น S&P 500 ที่ผ่านการคัดกรองสัญญาณเทคนิคเกรด"
        " A+ ด้านล่างนี้ เพื่อประเมินความเสี่ยงจากข่าวสารและจัดลำดับในการเข้าซื้อ\n\n"
    )

    prompt_text += "### บริบทตลาดภาพรวม (Market Context):\n"
    if market_ok:
        prompt_text += (
            f"- ดัชนี QQQ (NASDAQ 100): อยู่ในแนวโน้มขาขึ้น (ราคาปิด"
            f" ${qqq_close:.2f} > EMA50 ${qqq_ema50:.2f})\n\n"
        )
    else:
        prompt_text += (
            f"- [คำเตือนตลาด] ดัชนี QQQ (NASDAQ 100): ต่ำกว่า EMA50 (ราคาปิด"
            f" ${qqq_close:.2f} < EMA50 ${qqq_ema50:.2f}) "
            "ตลาดภาพรวมอยู่ในช่วงพักฐาน/ขาลง ควรเพิ่มความระมัดระวังในการเข้าซื้อ\n\n"
        )

    prompt_text += (
        f"### รายชื่อหุ้น S&P 500 ที่ผ่านเกณฑ์สแกนวันนี้ (พบ {len(matched_stocks)}"
        " ตัว):\n"
    )
    for idx, s in enumerate(matched_stocks, 1):
        prompt_text += f"\n{idx}. หุ้น: **{s['ticker']}**\n"
        prompt_text += (
            f"   - ราคาปิดล่าสุด: ${s['close']:.2f} (Low: ${s['low']:.2f},"
            f" High: ${s['high']:.2f})\n"
        )
        prompt_text += (
            f"   - เส้นค่าเฉลี่ย: EMA20 = ${s['ema20']:.2f} | EMA50 ="
            f" ${s['ema50']:.2f} | EMA200 = ${s['ema200']:.2f}\n"
        )
        prompt_text += (
            f"   - โวลุ่มเปรียบเทียบ (RVOL): {s['rvol']:.2f}x (มากกว่าปกติ"
            f" {(s['rvol']-1)*100:.0f}%)\n"
        )
        prompt_text += (
            "   - แผนการเทรด (กลยุทธ์แบ่งขาย 2 ไม้ + ล็อกกำไร +3%):\n"
        )
        prompt_text += (
            "     * Stop Loss แรกเริ่ม: ตั้งที่"
            f" ${s['sl_initial']:.2f} (-2%)\n"
        )
        prompt_text += (
            f"     * ไม้ที่ 1 (50%): ตั้ง Take Profit ที่ ${s['tp1']:.2f}"
            " (+5%)\n"
        )
        prompt_text += (
            "     * ไม้ที่ 2 (50%): เมื่อไม้ 1 ชนเป้าแล้ว ปรับ Stop Loss"
            f" มาล็อกกำไรที่ ${s['sl_step2']:.2f} (+3%) แล้วเลื่อนขายตาม EMA"
            " 20\n"
        )

        if s["news"]:
            prompt_text += "   - หัวข้อข่าวล่าสุดในระบบ:\n"
            for n in s["news"]:
                prompt_text += f"     * {n}\n"
        else:
            prompt_text += "   - หัวข้อข่าวล่าสุด: (ไม่มีข่าวผิดปกติ)\n"

    prompt_text += "\n" + "=" * 45 + "\n"
    prompt_text += "คำสั่งที่ต้องการให้ Chat AI ช่วยสรุปและตัดสินใจ:\n"
    prompt_text += (
        "1. ตรวจสอบว่าข่าวสารของหุ้นข้างต้นมีปัจจัยลบแรง (เช่น คดีความ,"
        " โดนปรับลดคำแนะนำ, งบแย่กว่าคาด) หรือไม่?\n"
    )
    prompt_text += (
        "2. จัดลำดับ (Rank) ความน่าสนใจกรณีมีหุ้นสแกนติดหลายตัว"
        " โดยพิจารณาจากน้ำหนักข่าวประกอบกับสัญญาณกราฟ\n"
    )
    prompt_text += (
        "3. สรุปคำแนะนำสั้นๆ 2-3 บรรทัด ว่าควรเปิดคำสั่งซื้อหุ้นตัวไหนในคืนนี้"
        " ที่ราคาเท่าไหร่ พร้อมจุด SL และ TP ไม้แรก"
        " (หากตลาดภาพรวมไม่เป็นใจ ให้ประเมินด้วยว่าคุ้มเสี่ยงที่จะเข้าซื้อหรือไม่)\n"
    )
    prompt_text += "==================================================\n"

    print(prompt_text)

    with open("prompt_result.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)
    print("\n[COMPLETE] บันทึก Prompt ลงไฟล์ 'prompt_result.txt' เรียบร้อยแล้ว!")

else:
    print(
        "\n[NO MATCH] สแกนครบทั้ง 503 ตัวแล้ว ไม่พบหุ้นใน S&P 500"
        " ที่เข้าสูตร Quality Filter A+ ในวันนี้"
    )
    print("[ACTION] พักการเทรด (No Trade) รอโอกาสรอบถัดไป")