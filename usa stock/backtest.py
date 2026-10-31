import time
import pandas as pd
import yfinance as yf

YEARS_BACKTEST = 1

print("🌐 กำลังดึงรายชื่อหุ้น S&P 500...")
try:
  url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
  tables = pd.read_html(url)
  sp500_tickers = (
      tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
  )
  print(f"✅ โหลดรายชื่อสำเร็จ {len(sp500_tickers)} หุ้น")
except Exception:
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

period_str = f"{YEARS_BACKTEST + 1}y"
start_time = time.time()

print(f"\n⚡ [FAST MODE] กำลังดาวน์โหลดข้อมูล Batch ทั้งหมด (S&P 500 + QQQ)...")

# ดึงข้อมูลทั้งหมดรวดเดียวใน 1-2 Request
all_tickers = sp500_tickers + ["QQQ"]
data = yf.download(
    all_tickers,
    period=period_str,
    interval="1d",
    auto_adjust=True,
    group_by="ticker",
    progress=True,
)

print(
    f"⏱️ ดาวน์โหลดข้อมูลสำเร็จใน {time.time() - start_time:.2f} วินาที!"
    " กำลังเริ่มคำนวณ Backtest..."
)

# ดึงข้อมูล QQQ
qqq = data["QQQ"].copy()
qqq["EMA50"] = qqq["Close"].ewm(span=50, adjust=False).mean()

trades = []

for ticker in sp500_tickers:
  try:
    if ticker not in data:
      continue
    df = data[ticker].dropna().copy()
    if len(df) < 200:
      continue

    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()
    df["Vol_SMA20"] = df["Volume"].rolling(20).mean()

    days_to_test = int(252 * YEARS_BACKTEST)
    start_idx = max(200, len(df) - days_to_test)
    end_idx = len(df) - 1
    cooldown_until = 0

    for i in range(start_idx, end_idx):
      if i < cooldown_until:
        continue

      date_val = df.index[i]
      if date_val in qqq.index:
        market_ok = float(qqq.loc[date_val, "Close"]) > float(
            qqq.loc[date_val, "EMA50"]
        )
      else:
        market_ok = True

      if not market_ok:
        continue

      close = float(df["Close"].iloc[i])
      open_p = float(df["Open"].iloc[i])
      low = float(df["Low"].iloc[i])
      ema20 = float(df["EMA20"].iloc[i])
      ema50 = float(df["EMA50"].iloc[i])
      ema200 = float(df["EMA200"].iloc[i])
      vol = float(df["Volume"].iloc[i])
      vol_sma = float(df["Vol_SMA20"].iloc[i])

      # Quality Filter A+ Setup
      cond1 = close > (ema200 * 1.03)
      cond2 = ema20 > ema50
      cond3 = (
          (low <= (ema20 * 1.008) or low <= (ema50 * 1.008))
          and (close > open_p * 1.008)
          and (close >= ema20)
      )
      cond4 = vol >= (vol_sma * 1.20)

      if cond1 and cond2 and cond3 and cond4:
        entry_idx = i + 1
        entry_date = df.index[entry_idx].strftime("%Y-%m-%d")
        entry_price = float(df["Open"].iloc[entry_idx])

        tp1_price = entry_price * 1.050
        sl_price = entry_price * 0.980

        half1_sold = False
        half1_pnl = 0.0
        half2_sold = False
        half2_pnl = 0.0

        exit_date = entry_date
        max_holding_days = min(30, len(df) - entry_idx)

        for h in range(max_holding_days):
          curr_idx = entry_idx + h
          high_curr = float(df["High"].iloc[curr_idx])
          low_curr = float(df["Low"].iloc[curr_idx])
          close_curr = float(df["Close"].iloc[curr_idx])
          ema20_curr = float(df["EMA20"].iloc[curr_idx])

          if not half1_sold:
            if low_curr <= sl_price:
              half1_sold = True
              half2_sold = True
              half1_pnl = -2.0
              half2_pnl = -2.0
              exit_date = df.index[curr_idx].strftime("%Y-%m-%d")
              cooldown_until = curr_idx + 1
              break
            elif high_curr >= tp1_price:
              half1_sold = True
              half1_pnl = +5.0
              sl_price = entry_price * 1.030  # ล็อกกำไรไม้สองที่ +3%

          if half1_sold and not half2_sold:
            if low_curr <= sl_price:
              half2_sold = True
              half2_pnl = +3.0
              exit_date = df.index[curr_idx].strftime("%Y-%m-%d")
              cooldown_until = curr_idx + 1
              break
            elif close_curr < ema20_curr:
              exit_pnl = round(
                  ((close_curr - entry_price) / entry_price) * 100, 2
              )
              half2_sold = True
              half2_pnl = max(3.0, exit_pnl)
              exit_date = df.index[curr_idx].strftime("%Y-%m-%d")
              cooldown_until = curr_idx + 1
              break
            elif h == max_holding_days - 1:
              exit_pnl = round(
                  ((close_curr - entry_price) / entry_price) * 100, 2
              )
              half2_sold = True
              half2_pnl = max(3.0, exit_pnl)
              exit_date = df.index[curr_idx].strftime("%Y-%m-%d")
              cooldown_until = curr_idx + 1
              break

        total_pnl = round((half1_pnl * 0.5) + (half2_pnl * 0.5), 2)
        result_status = (
            "WIN" if total_pnl > 0 else ("LOSS" if total_pnl < 0 else "EVEN")
        )

        trades.append({
            "Ticker": ticker,
            "Entry Date": entry_date,
            "Entry Price": round(entry_price, 2),
            "Exit Date": exit_date,
            "Half 1 PnL": f"{half1_pnl:+.1f}%",
            "Half 2 PnL": f"{half2_pnl:+.1f}%",
            "Result": result_status,
            "Total PnL (%)": total_pnl,
        })
  except Exception:
    continue

results_df = pd.DataFrame(trades)

if not results_df.empty:
  results_df = results_df.sort_values(by="Entry Date").reset_index(drop=True)

  win_count = len(results_df[results_df["Result"] == "WIN"])
  loss_count = len(results_df[results_df["Result"] == "LOSS"])
  total_trades = len(results_df)

  win_rate = (win_count / total_trades) * 100 if total_trades > 0 else 0
  total_pnl = results_df["Total PnL (%)"].sum()

  print("\n" + "=" * 65)
  print(
      f"=== ผลสรุป Backtest S&P 500 Fast Mode ย้อนหลัง {YEARS_BACKTEST} ปี ==="
  )
  print(f"จำนวนการเทรดทั้งหมด: {total_trades} ไม้")
  print(f"ชนะ (Total PnL > 0): {win_count} ไม้")
  print(f"แพ้ (Total PnL < 0): {loss_count} ไม้")
  print(f"Win Rate: {win_rate:.1f}%")
  print(f"ผลตอบแทนรวมสุทธิพอร์ต: {total_pnl:+.2f}%")
  print(f"เฉลี่ยเทรดเดือนละ: {round(total_trades / (YEARS_BACKTEST * 12), 1)} ไม้")
  print(f"เวลาประมวลผลทั้งหมด: {time.time() - start_time:.2f} วินาที")
  print("=" * 65)