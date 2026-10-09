"""米国株の売買ルール実験場（架空のお金）。

毎日1回動かす。やることは3つ:
1. 本物の株価（終値）を取得して prices.json にためる
2. 各ルールで最初の日から売買をやり直して、資産の動きを計算する
3. 画面用の data.json を書き出す
"""
import json
import os
import urllib.request
from datetime import datetime, timezone

SYMBOL = "SPY"          # 対象（S&P500に連動するETF）
START_CASH = 200.0      # 架空の元金（ドル）。約3万円
WARMUP_DAYS = 20        # 平均を計算するために、最初の20日は売買しない
PRICES_FILE = "prices.json"
DATA_FILE = "data.json"


# ---------- 株価 ----------
def fetch_prices():
    """Alpha Vantage から直近約100日の終値を取る。戻り値は {日付: 終値}"""
    key = os.environ["ALPHAVANTAGE_KEY"]
    url = ("https://www.alphavantage.co/query?function=TIME_SERIES_DAILY"
           f"&symbol={SYMBOL}&outputsize=compact&apikey={key}")
    with urllib.request.urlopen(url, timeout=30) as response:
        body = json.load(response)
    if "Time Series (Daily)" not in body:
        raise SystemExit(f"株価を取得できませんでした: {body}")
    days = body["Time Series (Daily)"]
    return {date: float(day["4. close"]) for date, day in days.items()}


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, value):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)


# ---------- ルール ----------
# 各ルールは「買う」「売る」「何もしない」を返す。
#   closes: 終値のリスト、i: 今日の番号
#   position: 持っていなければ None、持っていれば {"price": 買値, "day": 買った日の番号}
def average(closes, i, days):
    return sum(closes[i - days + 1:i + 1]) / days


def rule_hold(closes, i, position):
    """買って持つだけ（比べるための基準）"""
    return "buy" if position is None else None


def rule_trend(closes, i, position):
    """上り調子に乗る: 5日平均が20日平均より上なら持つ、下なら手放す"""
    rising = average(closes, i, 5) > average(closes, i, 20)
    if rising and position is None:
        return "buy"
    if not rising and position is not None:
        return "sell"
    return None


def rule_dip(closes, i, position):
    """下がったら買う: 直近10日の高値から3%下がったら買い、買値から3%上がるか10日たったら売る"""
    if position is None:
        recent_high = max(closes[i - 9:i + 1])
        return "buy" if closes[i] <= recent_high * 0.97 else None
    gained = closes[i] >= position["price"] * 1.03
    waited = i - position["day"] >= 10
    return "sell" if gained or waited else None


RULES = [
    {"name": "買って持つだけ", "note": "最初の日に全額で買い、売らない。ほかのルールと比べる基準。", "rule": rule_hold},
    {"name": "上り調子に乗る", "note": "5日平均が20日平均より上なら持つ。下なら手放す。", "rule": rule_trend},
    {"name": "下がったら買う", "note": "直近10日の高値から3%下がったら買う。3%上がるか10日たったら売る。", "rule": rule_dip},
]


# ---------- 売買の再現 ----------
def simulate(rule, dates, closes):
    """最初の日から売買をやり直す。毎回、全額で買うか全部売るかのどちらか。手数料は0円とする。"""
    cash = START_CASH
    shares = 0.0
    position = None
    values = []
    trades = []
    for i in range(WARMUP_DAYS, len(closes)):
        action = rule(closes, i, position)
        if action == "buy":
            shares = cash / closes[i]
            cash = 0.0
            position = {"price": closes[i], "day": i}
        if action == "sell":
            cash = shares * closes[i]
            shares = 0.0
            position = None
        if action:
            trades.append({"date": dates[i], "action": action, "price": closes[i]})
        values.append(round(cash + shares * closes[i], 2))
    return {"values": values, "trades": trades, "holding": position is not None}


def main():
    prices = load_json(PRICES_FILE, {})
    prices.update(fetch_prices())
    save_json(PRICES_FILE, prices)

    dates = sorted(prices)
    closes = [prices[date] for date in dates]
    if len(dates) <= WARMUP_DAYS:
        raise SystemExit("株価の日数が足りません")

    results = []
    for entry in RULES:
        result = simulate(entry["rule"], dates, closes)
        result["name"] = entry["name"]
        result["note"] = entry["note"]
        results.append(result)

    save_json(DATA_FILE, {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "symbol": SYMBOL,
        "start_cash": START_CASH,
        "dates": dates[WARMUP_DAYS:],
        "closes": closes[WARMUP_DAYS:],
        "rules": results,
    })


if __name__ == "__main__":
    main()
