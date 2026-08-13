import os
import time
import json
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')
from datetime import datetime, timedelta
from telegram import Bot
from telegram.error import TelegramError
import jdatetime
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, SMAIndicator, EMAIndicator
import io

# ==================== تنظیمات ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # -1004370207580
HISTORY_FILE = "gold_history.json"
CHECK_INTERVAL = 15 * 60          # هر ۱۵ دقیقه
MIN_CHANGE_TOMAN = 5000           # حداقل تغییر برای نوتیف (تومان)

bot = Bot(token=BOT_TOKEN)

# ==================== توابع کمکی ====================
def get_current_gold18():
    """دریافت قیمت لحظه‌ای طلای ۱۸ عیار از tgju"""
    try:
        # روش ۱: از جدول خلاصه
        url = "https://api.tgju.org/v1/market/indicator/summary-table-data/geram18"
        r = requests.get(url, timeout=15)
        data = r.json()
        if data.get("data"):
            # آخرین رکورد (جدیدترین)
            latest = data["data"][0]
            price_str = latest[3].replace(",", "")  # قیمت پایانی
            return int(price_str)
    except Exception as e:
        print(f"Error method 1: {e}")

    try:
        # روش جایگزین: صفحه اصلی
        r = requests.get("https://www.tgju.org/", timeout=15)
        # جستجوی ساده برای geram18
        import re
        match = re.search(r'data-market-nameslug="geram18".*?data-price="(\d+)"', r.text)
        if match:
            return int(match.group(1))
    except Exception as e:
        print(f"Error method 2: {e}")

    return None


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    return []


def save_history(history):
    # فقط ۷ روز آخر نگه می‌داریم
    cutoff = (datetime.now() - timedelta(days=7)).timestamp()
    history = [h for h in history if h["timestamp"] > cutoff]
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f)


def calculate_indicators(df):
    """محاسبه اندیکاتورهای مهم"""
    if len(df) < 30:
        return {}

    rsi = RSIIndicator(close=df["price"], window=14).rsi().iloc[-1]
    adx = ADXIndicator(high=df["price"], low=df["price"], close=df["price"], window=14).adx().iloc[-1]
    sma20 = SMAIndicator(close=df["price"], window=20).sma_indicator().iloc[-1]
    sma50 = SMAIndicator(close=df["price"], window=50).sma_indicator().iloc[-1] if len(df) >= 50 else None
    ema12 = EMAIndicator(close=df["price"], window=12).ema_indicator().iloc[-1]

    return {
        "RSI": round(rsi, 1),
        "ADX": round(adx, 1),
        "SMA20": int(sma20),
        "SMA50": int(sma50) if sma50 else None,
        "EMA12": int(ema12)
    }


def create_chart(df, indicators):
    """ساخت نمودار"""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), gridspec_kw={'height_ratios': [3, 1]})

    # نمودار قیمت
    ax1.plot(df.index, df["price"], label="قیمت طلای ۱۸ عیار", color="#d4af37", linewidth=2)
    if "SMA20" in indicators:
        ax1.axhline(y=indicators["SMA20"], color="blue", linestyle="--", alpha=0.7, label=f"SMA20: {indicators['SMA20']:,}")
    ax1.set_title("نمودار قیمت طلای ۱۸ عیار (تومان)", fontsize=14, fontweight="bold")
    ax1.set_ylabel("قیمت (تومان)")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{int(x):,}'))

    # RSI
    if len(df) >= 14:
        rsi_values = RSIIndicator(close=df["price"], window=14).rsi()
        ax2.plot(df.index, rsi_values, color="purple", label="RSI(14)")
        ax2.axhline(70, color="red", linestyle="--", alpha=0.5)
        ax2.axhline(30, color="green", linestyle="--", alpha=0.5)
        ax2.set_ylim(0, 100)
        ax2.set_ylabel("RSI")
        ax2.legend(loc="upper left")
        ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    buf.seek(0)
    plt.close()
    return buf


def format_message(price, change, indicators, jdate):
    change_emoji = "🟢" if change > 0 else "🔴" if change < 0 else "⚪"
    change_text = f"{change_emoji} {change:+,} تومان"

    text = f"""
🏅 **طلای ۱۸ عیار**
📅 {jdate}
💰 قیمت فعلی: **{price:,} تومان**
📊 تغییر: {change_text}

📈 **اندیکاتورها:**
• RSI(14): `{indicators.get('RSI', '—')}`
• ADX(14): `{indicators.get('ADX', '—')}`
• SMA20: `{indicators.get('SMA20', '—'):,}`
• EMA12: `{indicators.get('EMA12', '—'):,}`
"""
    if indicators.get("SMA50"):
        text += f"• SMA50: `{indicators['SMA50']:,}`\n"

    # تفسیر ساده
    rsi = indicators.get("RSI")
    if rsi:
        if rsi > 70:
            text += "\n⚠️ RSI در ناحیه اشباع خرید"
        elif rsi < 30:
            text += "\n✅ RSI در ناحیه اشباع فروش"

    return text.strip()


# ==================== حلقه اصلی ====================
def main():
    print("Agent started...")
    history = load_history()
    last_sent_price = history[-1]["price"] if history else None

    while True:
        try:
            price = get_current_gold18()
            if price is None:
                print("نتونستم قیمت رو بگیرم")
                time.sleep(60)
                continue

            now = datetime.now()
            history.append({
                "timestamp": now.timestamp(),
                "price": price,
                "datetime": now.isoformat()
            })
            save_history(history)

            # ساخت دیتافریم
            df = pd.DataFrame(history)
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.set_index("datetime").sort_index()

            # محاسبه تغییر
            change = 0
            if last_sent_price:
                change = price - last_sent_price

            # فقط اگر تغییر معنادار بود نوتیف بده
            if last_sent_price is None or abs(change) >= MIN_CHANGE_TOMAN:
                indicators = calculate_indicators(df)
                chart = create_chart(df.tail(100), indicators)  # ۱۰۰ نقطه آخر
                jdate = jdatetime.datetime.now().strftime("%Y/%m/%d - %H:%M")

                message = format_message(price, change, indicators, jdate)

                # ارسال به کانال
                bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=chart,
                    caption=message,
                    parse_mode="Markdown"
                )
                print(f"نوتیف ارسال شد | قیمت: {price:,}")
                last_sent_price = price
            else:
                print(f"قیمت تغییر نکرده معنادار | فعلی: {price:,}")

        except Exception as e:
            print(f"خطا: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()