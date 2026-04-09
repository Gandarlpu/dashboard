"""
대시보드 — 코인봇 / 국장봇 / 미장봇 통합 모니터링
"""
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta

import pytz
from flask import Flask, jsonify, render_template

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
    # 폴백: 로그 파일 최근 수정 시간 (3분 이내면 실행 중)
    log_path = f"/home/park722117/{BOT_DIR[bot]}/bot.log"
    try:
        return (time.time() - os.path.getmtime(log_path)) < 180
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
    rows = query(bot, f"SELECT {tc}, avg_buy_price, volume, bought_at, buy_amount FROM open_positions")
    return [{"ticker": r[0], "avg_buy_price": r[1], "volume": r[2],
             "bought_at": r[3], "buy_amount": round(r[4], 2)} for r in rows]


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
            f"SELECT {tc}, pnl, pnl_rate, exit_at, exit_reason, result "
            f"FROM trade_history ORDER BY exit_at DESC LIMIT ?",
            (limit,)
        )
        for r in rows:
            all_trades.append({
                "bot": BOT_NAMES[bot],
                "ticker": r[0],
                "pnl": round(r[1], 2),
                "pnl_rate": round(r[2], 2),
                "exit_at": r[3],
                "exit_reason": r[4],
                "result": r[5],
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
