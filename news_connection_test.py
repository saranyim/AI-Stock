import sys
import time
import random
import requests
import feedparser
from bs4 import BeautifulSoup

# บังคับให้ Output Stream รองรับภาษาไทยบน Terminal / Task Scheduler
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# --- CONFIG & USER-AGENT ROTATION (ANTI-FLOOD) ---
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Safari/605.1.15"
]

TARGET_SOURCES = {
    # --- 7 สำนักข่าวเดิม ---
    "Yahoo Finance": {
        "url": "https://finance.yahoo.com",
        "rss": "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
        "paywall": False
    },
    "Reuters Business": {
        "url": "https://www.reuters.com/business",
        "rss": "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
        "paywall": False
    },
    "CNBC Markets": {
        "url": "https://www.cnbc.com/markets",
        "rss": "https://search.cnbc.com/rs/search/combined Garfield/rss.xml",
        "paywall": False
    },
    "MarketWatch": {
        "url": "https://www.marketwatch.com",
        "rss": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "paywall": False
    },
    "Investing.com": {
        "url": "https://www.investing.com/news",
        "rss": "https://www.investing.com/rss/news.rss",
        "paywall": False
    },
    "Bloomberg": {
        "url": "https://www.bloomberg.com/markets",
        "rss": "https://feeds.bloomberg.com/markets/news.rss",
        "paywall": True
    },
    "Seeking Alpha": {
        "url": "https://www.seekingalpha.com",
        "rss": "https://seekingalpha.com/feed.xml",
        "paywall": True
    },

    # --- 6 แหล่งข่าวเพิ่มเติมใหม่ ---
    "Fed Policy": {
        "url": "https://www.federalreserve.gov",
        "rss": "https://www.federalreserve.gov/feeds/press_all.xml",
        "paywall": False
    },
    "WSJ Business": {
        "url": "https://www.wsj.com",
        "rss": "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
        "paywall": True
    },
    "Benzinga": {
        "url": "https://www.benzinga.com",
        "rss": "https://www.benzinga.com/feed",
        "paywall": False
    },
    "Zacks": {
        "url": "https://www.zacks.com",
        "rss": "https://www.zacks.com/rss/news/",
        "paywall": False
    },
    "OilPrice": {
        "url": "https://oilprice.com",
        "rss": "https://oilprice.com/rss/main",
        "paywall": False
    },
    "CoinDesk": {
        "url": "https://www.coindesk.com",
        "rss": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "paywall": False
    }
}

def get_safe_headers():
    """สร้าง HTTP Headers แบบสุ่มเพื่อป้องกัน Anti-Bot / Rate Limit"""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive"
    }

def safe_delay(min_sec=0.6, max_sec=1.5):
    """หน่วงเวลาแบบ Random Jitter ชะลอความเร็วการยิง Request"""
    time.sleep(random.uniform(min_sec, max_sec))

def test_single_source(source_name: str, config: dict, test_ticker: str = "NVDA"):
    safe_delay()  # Anti-flood delay
    start_time = time.time()
    
    headers = get_safe_headers()
    rss_url = config.get("rss", "").format(ticker=test_ticker)
    main_url = config.get("url")
    
    result = {
        "source": source_name,
        "url": main_url,
        "status": "🔴 Failed",
        "response_time": "0.00s",
        "http_code": "N/A",
        "articles_found": 0,
        "sample_headline": "-",
        "is_paywall": config.get("paywall", False),
        "error_msg": ""
    }

    try:
        session = requests.Session()
        resp = session.get(main_url, headers=headers, timeout=8)
        elapsed = time.time() - start_time
        result["response_time"] = f"{elapsed:.2f}s"
        result["http_code"] = str(resp.status_code)

        if resp.status_code == 200:
            result["status"] = "🟢 Connected"
        elif resp.status_code in [403, 429]:
            result["status"] = "🟡 Blocked/Rate Limited"
            result["error_msg"] = f"HTTP {resp.status_code} - Detected Bot/Anti-Scrape"
        else:
            result["status"] = f"🟡 HTTP {resp.status_code}"

        # ดึงและนับจำนวนข่าวผ่าน RSS Feed
        if rss_url:
            feed = feedparser.parse(rss_url, request_headers=headers)
            if feed.entries:
                result["articles_found"] = len(feed.entries)
                first_entry = feed.entries[0]
                result["sample_headline"] = first_entry.get("title", "No Title Available")
            else:
                if resp.status_code == 200 and not config["paywall"]:
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    headlines = [h.get_text(strip=True) for h in soup.find_all(['h1', 'h2', 'h3']) if len(h.get_text(strip=True)) > 20]
                    if headlines:
                        result["articles_found"] = len(headlines)
                        result["sample_headline"] = headlines[0]

    except requests.exceptions.Timeout:
        result["status"] = "🔴 Timeout"
        result["error_msg"] = "การเชื่อมต่อใช้เวลานานเกินกำหนด (>8s)"
    except Exception as e:
        result["status"] = "🔴 Error"
        result["error_msg"] = str(e)[:100]

    return result

def main():
    test_ticker = "NVDA"
    print("=" * 95)
    print(f"📡  GLOBAL NEWS CONNECTIVITY & ANTI-FLOOD TESTER (Text Output Mode)")
    print(f"🎯  Testing Ticker: {test_ticker} | Total Sources: {len(TARGET_SOURCES)}")
    print("=" * 95)
    
    results = []
    total = len(TARGET_SOURCES)
    
    for idx, (name, cfg) in enumerate(TARGET_SOURCES.items(), 1):
        print(f"[{idx}/{total}] ⏳ Testing connection: {name:<16} (Anti-Flood Delay Active)...", end="", flush=True)
        res = test_single_source(name, cfg, test_ticker)
        results.append(res)
        print(f"\r[{idx}/{total}] Status: {res['status']:<22} | Time: {res['response_time']} | HTTP: {res['http_code']} | Count: {res['articles_found']} articles")

    print("\n" + "=" * 95)
    print("📋 SUMMARY REPORT: TEST RESULTS FOR ALL 7 SOURCES")
    print("=" * 95)
    
    print(f"{'Source':<18} | {'Status':<22} | {'HTTP':<5} | {'Time':<7} | {'Articles':<9} | {'Access Type':<16} | {'Sample Headline'}")
    print("-" * 95)
    
    successful_connections = 0
    total_articles = 0
    
    for r in results:
        if "Connected" in r["status"]:
            successful_connections += 1
        total_articles += r["articles_found"]
        
        access_type = "🔒 Paywall/Login" if r["is_paywall"] else "🟢 Free Access"
        sample_title = r["sample_headline"][:35] + "..." if len(r["sample_headline"]) > 35 else r["sample_headline"]
        
        print(f"{r['source']:<18} | {r['status']:<22} | {r['http_code']:<5} | {r['response_time']:<7} | {r['articles_found']:<9} | {access_type:<16} | {sample_title}")

    print("=" * 95)
    print(f"📊 Summary Statistics:")
    print(f"   - Connected Sources : {successful_connections} / {len(results)}")
    print(f"   - Total Articles    : {total_articles} articles fetched across all sources")
    print("=" * 95)

if __name__ == "__main__":
    main()