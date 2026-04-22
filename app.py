"""
대시보드 — 코인봇 / 국장봇 / 미장봇 통합 모니터링
"""
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta

import pytz
import requests as _req
from flask import Flask, jsonify, render_template

# 한국 주식 종목명 캐시 (코드 → 이름)
_krx_name_cache: dict = {}

def _get_krx_name(code: str) -> str:
    """한국 주식 코드 → 종목명 (pykrx, 캐시)"""
    if code in _krx_name_cache:
        return _krx_name_cache[code]
    try:
        from pykrx import stock as krx
        name = krx.get_market_ticker_name(code)
        if name:
            _krx_name_cache[code] = name
            return name
    except Exception:
        pass
    return code

def _resolve_name(bot: str, ticker: str) -> str:
    if bot == "coin":
        return ticker.replace("KRW-", "")   # KRW-BTC → BTC
    if bot == "us":
        return ticker.split(":")[-1]         # NAS:NVDA → NVDA
    if bot == "stock":
        return _get_krx_name(ticker)         # 005930 → 삼성전자
    return ticker

_price_cache: dict = {}
_PRICE_TTL = 60  # 1분 캐시

def _fetch_current_price(bot: str, ticker: str):
    key = (bot, ticker)
    cached = _price_cache.get(key)
    if cached and time.time() - cached[1] < _PRICE_TTL:
        return cached[0]
    price = None
    try:
        if bot == "coin":
            r = _req.get(f"https://api.upbit.com/v1/ticker?markets={ticker}", timeout=3)
            data = r.json()
            price = data[0]["trade_price"] if data else None
        elif bot == "us":
            symbol = ticker.split(":")[-1]
            r = _req.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=4
            )
            price = r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
        elif bot == "stock":
            r = _req.get(
                f"https://m.stock.naver.com/api/stock/{ticker}/basic",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=3
            )
            raw = r.json().get("closePrice") or r.json().get("stockEndPrice") or ""
            price = float(str(raw).replace(",", "")) if raw else None
    except Exception:
        pass
    if price is not None:
        _price_cache[key] = (price, time.time())
    return price

app = Flask(__name__)

KST = pytz.timezone("Asia/Seoul")

DB = {
    "coin":  "/home/park722117/coin-autotrader/data/trades.db",
    "stock": "/home/park722117/stock-autotrader/data/trades.db",
    "us":    "/home/park722117/us-autotrader/data/trades.db",
}

PID = {
    "coin":  "/home/park722117/coin-autotrader/bot.pid",
    "stock": "/home/park722117/stock-autotrader/bot.pid",
    "us":    "/home/park722117/us-autotrader/bot.pid",
}

BOT_DIR = {
    "coin":  "coin-autotrader",
    "stock": "stock-autotrader",
    "us":    "us-autotrader",
}

BOT_NAMES   = {"coin": "코인봇", "stock": "국장봇", "us": "미장봇"}
TICKER_COL  = {"coin": "ticker", "stock": "stock_code", "us": "stock_code"}


# ── 유틸 ──────────────────────────────────────────────────────────

def is_running(bot: str) -> bool:
    # PID 파일로 확인
    try:
        with open(PID[bot]) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        pass
    # 폴백: 로그 파일 최근 수정 시간 (15분 이내면 실행 중)
    log_path = f"/home/park722117/{BOT_DIR[bot]}/bot.log"
    try:
        return (time.time() - os.path.getmtime(log_path)) < 900
    except Exception:
        return False


def query(bot: str, sql: str, params=()):
    try:
        conn = sqlite3.connect(DB[bot])
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


# ── 데이터 함수 ───────────────────────────────────────────────────

def get_stats(bot: str) -> dict:
    rows = query(bot, "SELECT result, COUNT(*), SUM(pnl) FROM trade_history GROUP BY result")
    win = loss = 0
    total_pnl = 0.0
    for r in rows:
        total_pnl += r[2] or 0
        if r[0] == "win":
            win = r[1]
        else:
            loss = r[1]
    total = win + loss
    return {
        "win": win, "loss": loss, "total": total,
        "win_rate": round(win / total * 100, 1) if total else 0,
        "total_pnl": round(total_pnl, 2),
    }


def get_today_pnl(bot: str) -> float:
    today = datetime.now(KST).strftime("%Y-%m-%d")
    rows = query(bot, "SELECT SUM(pnl) FROM trade_history WHERE exit_at LIKE ?", (f"{today}%",))
    return round(rows[0][0] or 0, 2) if rows else 0.0


def get_positions(bot: str) -> list:
    tc = TICKER_COL[bot]
    rows = query(
        bot,
        f"SELECT {tc}, avg_buy_price, volume, bought_at, buy_amount, "
        f"stop_loss_price, take_profit_price FROM open_positions"
    )
    result = []
    for r in rows:
        ticker, avg_buy = r[0], r[1]
        current = _fetch_current_price(bot, ticker)
        pnl_rate = round((current - avg_buy) / avg_buy * 100, 2) if current and avg_buy else None
        result.append({
            "ticker": ticker,
            "name": _resolve_name(bot, ticker),
            "avg_buy_price": avg_buy,
            "volume": r[2],
            "bought_at": r[3],
            "buy_amount": round(r[4], 2),
            "stop_loss_price": r[5],
            "take_profit_price": r[6],
            "current_price": current,
            "pnl_rate": pnl_rate,
        })
    return result


def get_cumulative(bot: str) -> list:
    rows = query(bot, "SELECT DATE(exit_at), SUM(pnl) FROM trade_history GROUP BY DATE(exit_at) ORDER BY DATE(exit_at)")
    cum, result = 0.0, []
    for r in rows:
        cum += r[1] or 0
        result.append({"date": r[0], "pnl": round(cum, 2)})
    return result


def get_yesterday(bot: str) -> list:
    yesterday = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
    rows = query(
        bot,
        "SELECT STRFTIME('%H', exit_at), SUM(pnl) FROM trade_history "
        "WHERE exit_at LIKE ? GROUP BY STRFTIME('%H', exit_at) ORDER BY 1",
        (f"{yesterday}%",)
    )
    return [{"hour": r[0], "pnl": round(r[1] or 0, 2)} for r in rows]


def get_recent_trades(limit: int = 20) -> list:
    all_trades = []
    for bot in ["coin", "stock", "us"]:
        tc = TICKER_COL[bot]
        rows = query(
            bot,
            f"SELECT {tc}, pnl, pnl_rate, exit_at, exit_reason, result, COALESCE(theme, '') "
            f"FROM trade_history ORDER BY exit_at DESC LIMIT ?",
            (limit,)
        )
        if not rows:
            # theme 컬럼 없는 구버전 DB 폴백
            rows = query(
                bot,
                f"SELECT {tc}, pnl, pnl_rate, exit_at, exit_reason, result "
                f"FROM trade_history ORDER BY exit_at DESC LIMIT ?",
                (limit,)
            )
            for r in rows:
                all_trades.append({
                    "bot": BOT_NAMES[bot],
                    "ticker": r[0],
                    "name": _resolve_name(bot, r[0]),
                    "pnl": round(r[1], 2),
                    "pnl_rate": round(r[2], 2),
                    "exit_at": r[3],
                    "exit_reason": r[4],
                    "result": r[5],
                    "theme": "",
                })
            continue
        for r in rows:
            all_trades.append({
                "bot": BOT_NAMES[bot],
                "ticker": r[0],
                "name": _resolve_name(bot, r[0]),
                "pnl": round(r[1], 2),
                "pnl_rate": round(r[2], 2),
                "exit_at": r[3],
                "exit_reason": r[4],
                "result": r[5],
                "theme": r[6],
            })
    all_trades.sort(key=lambda x: x["exit_at"], reverse=True)
    return all_trades[:limit]


# ── 라우트 ────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def api_data():
    data = {"bots": {}, "recent_trades": get_recent_trades(20)}
    for bot in ["coin", "stock", "us"]:
        data["bots"][bot] = {
            "name":       BOT_NAMES[bot],
            "running":    is_running(bot),
            "stats":      get_stats(bot),
            "today_pnl":  get_today_pnl(bot),
            "positions":  get_positions(bot),
            "cumulative": get_cumulative(bot),
            "yesterday":  get_yesterday(bot),
        }
    return jsonify(data)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
