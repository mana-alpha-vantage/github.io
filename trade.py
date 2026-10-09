"""米国株の売買ルール実験場（架空のお金）。

毎日1回動かす。やることは4つ:
1. 本物の株価を取得する（日ごとの終値と、25年以上ある月ごとの終値）
2. 記録の開始日から、各ルールで売買を再現する（これが本番の記録）
3. 同じルールを過去の月ごとの株価にも当てて、成績を確かめる（過去の検証）
4. 画面用の data.json を書き出す
"""
import json
import os
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

SYMBOL = "SPY"                 # 対象（S&P500に連動するETF）
START_CASH = 200.0             # 架空の元金（ドル）。約3万円
LIVE_START = "2026-10-09"      # 記録を始める日。この日以降の最初の取引日から売買する
PARTS = 10                     # 積立で元金を何回に分けるか
AVERAGE_MONTHS = 10            # 移動平均に使う月数
PRICES_FILE = "prices.json"
DATA_FILE = "data.json"


# ---------- 株価 ----------
def fetch_closes(query):
    """Alpha Vantage から終値を取る。query は「function=...&symbol=...」の部分。戻り値は {日付: 終値}"""
    key = os.environ["ALPHAVANTAGE_KEY"]
    url = f"https://www.alphavantage.co/query?{query}&apikey={key}"
    with urllib.request.urlopen(url, timeout=30) as response:
        body = json.load(response)
    series = [value for name, value in body.items() if "Time Series" in name]
    if not series:
        raise SystemExit(f"データを取得できませんでした（{query}）: {body}")
    return {date: float(day["4. close"]) for date, day in series[0].items()}


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, value):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)


# ---------- ルール ----------
# 各ルールは、その日の状況 day を見て、次のどれかを返す。
#   ("buy", 金額, 理由)  その金額ぶん買う
#   ("sell", 0, 理由)    持っている株を全部売る
#   None                 何もしない
# day の中身:
#   first      記録の最初の日か
#   new_month  月が変わって最初の取引日か
#   months     その時点でわかっている、過去の月末の終値（古い順）
#   cash       現金、shares 持っている株数、buys これまでに買った回数
def rule_hold(day):
    """買って持つだけ: 最初の日に全額で買い、売らない"""
    if day["first"]:
        return ("buy", day["cash"], "記録の最初の日なので、全額で買った。このあとは売らない。")
    return None


def rule_average(day):
    """10か月移動平均: 月に1回、月末の終値が過去10か月の平均より上なら持ち、下なら現金にする"""
    if not day["new_month"] or len(day["months"]) < AVERAGE_MONTHS:
        return None
    average = sum(day["months"][-AVERAGE_MONTHS:]) / AVERAGE_MONTHS
    last = day["months"][-1]
    above = last > average
    basis = f"直近の月末の終値 {last:.2f} が、過去10か月の平均 {average:.2f} より"
    if above and day["shares"] == 0:
        return ("buy", day["cash"], basis + "上。上り調子と判断して、全額で買った。")
    if not above and day["shares"] > 0:
        return ("sell", 0, basis + "下。下り調子と判断して、全部売って現金にした。")
    return None


def rule_monthly(day):
    """毎月の積立: 元金を10回に分け、毎月1回、同じ金額ずつ買う"""
    if day["new_month"] and day["buys"] < PARTS:
        count = day["buys"] + 1
        return ("buy", START_CASH / PARTS, f"月が変わったので、決まった金額を買った（{count}回目／{PARTS}回）。株価は見ていない。")
    return None


RULES = [
    {"name": "買って持つだけ", "rule": rule_hold,
     "note": "最初の日に全額で買い、売らない。インデックス投資の基本で、ほかのルールと比べる基準。"},
    {"name": "10か月移動平均", "rule": rule_average,
     "note": "月に1回だけ判断する。月末の終値が過去10か月の平均より上なら持ち、下なら現金にする。大きな下落を避けることをねらう。"},
    {"name": "毎月の積立", "rule": rule_monthly,
     "note": "元金を10回に分け、毎月1回、同じ金額ずつ買う。買う時期を分散する。"},
]


# ---------- 売買の再現 ----------
def simulate(rule, dates, closes, known_months):
    """dates の最初の日から売買を再現する。

    known_months(i) は、i番目の日にわかっている月末の終値のリストを返す関数。
    手数料は0円、その日の終値で売買できたものとする。
    """
    cash = START_CASH
    shares = 0.0
    buys = 0
    values = []
    trades = []
    for i in range(len(dates)):
        day = {
            "first": i == 0,
            "new_month": i == 0 or dates[i][:7] != dates[i - 1][:7],
            "months": known_months(i),
            "cash": cash, "shares": shares, "buys": buys,
        }
        action = rule(day)
        if action and action[0] == "buy":
            amount = min(action[1], cash)
            shares += amount / closes[i]
            cash -= amount
            buys += 1
            trades.append({"date": dates[i], "action": "buy", "price": closes[i], "amount": round(amount, 2), "reason": action[2]})
        if action and action[0] == "sell":
            amount = shares * closes[i]
            cash += amount
            shares = 0.0
            trades.append({"date": dates[i], "action": "sell", "price": closes[i], "amount": round(amount, 2), "reason": action[2]})
        values.append(round(cash + shares * closes[i], 2))
    return {"values": values, "trades": trades, "holding": shares > 0}


def biggest_drop(values):
    """それまでの最高額からの、いちばん大きい下落率（%）"""
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = max(worst, (peak - value) / peak * 100)
    return round(worst, 1)


# ---------- 本番の記録 ----------
def run_live(prices, month_dates, month_closes):
    dates = [date for date in sorted(prices) if date >= LIVE_START]
    closes = [prices[date] for date in dates]

    def known_months(i):
        # その日より前の月の、月末の終値だけを使う
        return [close for date, close in zip(month_dates, month_closes) if date[:7] < dates[i][:7]]

    results = []
    for entry in RULES:
        result = simulate(entry["rule"], dates, closes, known_months)
        result["name"] = entry["name"]
        result["note"] = entry["note"]
        results.append(result)
    return dates, closes, results


# ---------- 過去の検証 ----------
def run_backtest(month_dates, month_closes):
    """同じルールを、月ごとの株価に最初から当てる。1か月を1日として扱う。"""
    skip = AVERAGE_MONTHS - 1          # 平均を計算できる月から始める
    dates = month_dates[skip:]
    closes = month_closes[skip:]

    def known_months(i):
        # 月末に判断するので、その月の終値までわかっている
        return month_closes[:skip + i + 1]

    years = len(dates) / 12
    rows = []
    for entry in RULES:
        result = simulate(entry["rule"], dates, closes, known_months)
        final = result["values"][-1]
        rows.append({
            "name": entry["name"],
            "final": final,
            "yearly": round(((final / START_CASH) ** (1 / years) - 1) * 100, 1),
            "drop": biggest_drop(result["values"]),
            "trades": len(result["trades"]),
            "values": result["values"],      # グラフ用: 月ごとの資産
        })
    return {"from": dates[0], "to": dates[-1], "years": round(years, 1),
            "dates": dates, "closes": closes, "rows": rows}


# ---------- 「あのとき200ドル買っていたら」用のデータ ----------
def shift_months(day_text, months):
    """日付を months か月前にずらす（日は28日までに丸める）"""
    day = date.fromisoformat(day_text)
    total = day.year * 12 + (day.month - 1) - months
    return date(total // 12, total % 12 + 1, min(day.day, 28)).isoformat()


def last_on_or_before(series, target):
    """target 以前でいちばん新しい日付を返す。なければ None"""
    found = [day for day in series if day <= target]
    return max(found) if found else None


def build_lookback(all_prices, all_rates, daily_dates):
    """過去のいろいろな時点の株価と為替を集める。

    3日前までは毎日、1か月以内は1週間ごと、10か月前までは2か月ごと、それより前は1年ごと。
    all_prices と all_rates は {日付: 値}（日ごとと月ごとを合わせたもの）。
    """
    latest = daily_dates[-1]
    latest_day = date.fromisoformat(latest)

    targets = []
    for n in (1, 2, 3):
        if len(daily_dates) > n:
            targets.append((f"{n}取引日前", daily_dates[-1 - n]))
    for n in (1, 2, 3, 4):
        targets.append((f"{n}週間前", (latest_day - timedelta(days=7 * n)).isoformat()))
    for n in (2, 4, 6, 8, 10):
        targets.append((f"{n}か月前", shift_months(latest, n)))
    for n in range(1, 41):
        targets.append((f"{n}年前", shift_months(latest, 12 * n)))

    def rate_on(day):
        found = last_on_or_before(all_rates, day)
        return all_rates[found] if found else None

    rows = []
    for label, target in targets:
        day = last_on_or_before(all_prices, target)
        if day is None:
            continue   # そこまで古いデータがない
        rows.append({"label": label, "date": day, "close": all_prices[day], "rate": rate_on(day)})
    return {"date": latest, "close": all_prices[latest], "rate": rate_on(latest), "rows": rows}


def fetch_rates():
    """ドル円の為替（日ごとと月ごと）を取る。取れなければ空にして、株価の計算は続ける。"""
    try:
        time.sleep(15)
        rates = fetch_closes("function=FX_MONTHLY&from_symbol=USD&to_symbol=JPY")
        time.sleep(15)
        rates.update(fetch_closes("function=FX_DAILY&from_symbol=USD&to_symbol=JPY"))
        return rates
    except SystemExit as error:
        print(error)
        return {}


def main():
    prices = load_json(PRICES_FILE, {})
    prices.update(fetch_closes(f"function=TIME_SERIES_DAILY&symbol={SYMBOL}&outputsize=compact"))
    save_json(PRICES_FILE, prices)

    time.sleep(15)   # 無料キーは続けて呼ぶと断られるので、少し待つ
    monthly = fetch_closes(f"function=TIME_SERIES_MONTHLY&symbol={SYMBOL}")

    # 今月ぶんは月の途中なので、終わった月だけを使う
    this_month = max(prices)[:7]
    month_dates = [date for date in sorted(monthly) if date[:7] < this_month]
    month_closes = [monthly[date] for date in month_dates]

    dates, closes, results = run_live(prices, month_dates, month_closes)

    # 月ごとの古い株価と、日ごとの新しい株価を1つにまとめる
    all_prices = dict(zip(month_dates, month_closes))
    all_prices.update(prices)
    lookback = build_lookback(all_prices, fetch_rates(), sorted(prices))

    save_json(DATA_FILE, {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "symbol": SYMBOL,
        "start_cash": START_CASH,
        "live_start": LIVE_START,
        "dates": dates,
        "closes": closes,
        "rules": results,
        "backtest": run_backtest(month_dates, month_closes),
        "lookback": lookback,
    })


if __name__ == "__main__":
    main()
