"""Bot de tendência BTC-USDC — roda 24h.

MODO PADRÃO = SIMULAÇÃO (paper). Nenhuma ordem real é enviada.
Para operar de verdade é preciso, AO MESMO TEMPO:
  LIVE=1, CONFIRM_LIVE=SIM_EU_ENTENDO_O_RISCO, COINBASE_API_KEY e COINBASE_API_SECRET.

Variáveis de ambiente (todas opcionais):
  PRODUCT=BTC-USDC  TF=4h  FAST=50  SLOW=150  TREND=0  (backtest 10/11/2026)
  PAPER_CAPITAL=1000      capital simulado em USDC
  LIVE_MAX_USDC=0         teto de USDC que o bot pode usar no modo real
  STATE_DIR=/data         onde salvar estado e histórico (monte um volume no Easypanel)
  TELEGRAM_TOKEN / TELEGRAM_CHAT_ID   avisos no Telegram (opcional)
  PANEL_PASSWORD=...      liga o painel web (usuário "admin"); sem senha, o painel fica desligado
  PORT=8080               porta do painel
"""
import csv
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

from data_source import fetch_candles, drop_open_candle
from strategy import Params, ema, signals

FEE, SLIPPAGE = 0.001, 0.0005
TF_SECS = {"1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "1d": 86400}

env = os.environ.get
PRODUCT = env("PRODUCT", "BTC-USDC")
TF = env("TF", "4h")
P = Params(int(env("FAST", 50)), int(env("SLOW", 150)), int(env("TREND", 0)))
STATE_DIR = Path(env("STATE_DIR", "/data"))
LIVE = env("LIVE") == "1" and env("CONFIRM_LIVE") == "SIM_EU_ENTENDO_O_RISCO"
LIVE_MAX_USDC = float(env("LIVE_MAX_USDC", 0))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("bot")
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_F, TRADES_F = STATE_DIR / "state.json", STATE_DIR / "trades.csv"
EQUITY_F = STATE_DIR / "equity.csv"  # uma linha por candle: usado pelo painel


def notify(msg: str):
    log.info(msg)
    tok, chat = env("TELEGRAM_TOKEN"), env("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          json={"chat_id": chat, "text": msg}, timeout=10)
        except requests.RequestException as e:
            log.warning("Telegram falhou: %s", e)


def load_state():
    if STATE_F.exists():
        s = json.loads(STATE_F.read_text())
        mode = s.get("mode", "paper")
        if mode != ("live" if LIVE else "paper"):
            sys.exit(f"O state.json é do modo '{mode}' e o bot está em '{'live' if LIVE else 'paper'}'. "
                     f"Apague {STATE_F} antes de trocar de modo (evita herdar posição errada).")
        return s
    if LIVE:
        _, usdc = _live_balances(_client())
        cap = min(usdc, LIVE_MAX_USDC)
        if cap < 5:
            sys.exit(f"Modo real: USDC disponível ({usdc:.2f}) ou LIVE_MAX_USDC baixo demais.")
        log.info("Modo real: o bot vai controlar %.2f USDC (saldo %.2f, teto %.2f)", cap, usdc, LIVE_MAX_USDC)
        return {"mode": "live", "usdc": cap, "btc": 0.0, "position": 0,
                "last_candle": None, "start_equity": cap}
    cap = float(env("PAPER_CAPITAL", 1000))
    return {"mode": "paper", "usdc": cap, "btc": 0.0, "position": 0,
            "last_candle": None, "start_equity": cap}


def save_state(s):
    tmp = STATE_F.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2))
    tmp.replace(STATE_F)


def record(row: dict, path: Path = TRADES_F):
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


def spot_price() -> float:
    r = requests.get(f"https://api.coinbase.com/api/v3/brokerage/market/products/{PRODUCT}", timeout=15)
    r.raise_for_status()
    return float(r.json()["price"])


# ---------- execução ----------
# No modo real o bot só mexe no que ELE comprou: s["usdc"] e s["btc"] são a "subconta" do bot.
# O resto do saldo da conta nunca é vendido nem usado.
def _client():
    from coinbase.rest import RESTClient
    return RESTClient(api_key=env("COINBASE_API_KEY"), api_secret=env("COINBASE_API_SECRET"))


def _d(x) -> dict:
    return x.to_dict() if hasattr(x, "to_dict") else dict(x or {})


def _live_balances(c):
    base, quote = PRODUCT.split("-")
    bal = {base: 0.0, quote: 0.0}
    for a in _d(c.get_accounts(limit=250)).get("accounts", []):
        a = _d(a)
        if a.get("currency") in bal:
            bal[a["currency"]] = float(_d(a["available_balance"])["value"])
    return bal[base], bal[quote]


def _order_id(candle: str, side: str) -> str:
    # Mesmo id para o mesmo candle/lado: se o ciclo der erro e repetir, a Coinbase não duplica a ordem.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"btcbot/{PRODUCT}/{candle}/{side}"))


def _wait_fill(c, resp) -> dict:
    r = _d(resp)
    if not r.get("success"):
        raise RuntimeError(f"ordem recusada: {r.get('error_response') or r.get('failure_reason') or r}")
    oid = r.get("order_id") or _d(r.get("success_response")).get("order_id")
    for _ in range(30):
        o = _d(_d(c.get_order(oid)).get("order"))
        if o.get("status") in ("FILLED", "CANCELLED", "EXPIRED", "FAILED"):
            break
        time.sleep(1)
    filled = float(o.get("filled_size") or 0)
    if filled <= 0:
        raise RuntimeError(f"ordem {oid} sem execução (status {o.get('status')})")
    return {"id": oid, "status": o.get("status"), "base": filled,
            "value": float(o.get("filled_value") or 0), "fees": float(o.get("total_fees") or 0),
            "avg": float(o.get("average_filled_price") or 0)}


def _floor8(x: float) -> str:
    return f"{int(x * 1e8) / 1e8:.8f}"


def buy(s, price, candle=""):
    if LIVE:
        c = _client()
        _, avail = _live_balances(c)
        size = min(s["usdc"], avail, LIVE_MAX_USDC) * 0.995  # folga para a taxa
        if size < 5:
            notify(f"⚠️ COMPRA ignorada: USDC do bot/disponível/teto muito baixo ({size:.2f})")
            return
        f = _wait_fill(c, c.market_order_buy(client_order_id=_order_id(candle, "BUY"),
                                             product_id=PRODUCT, quote_size=f"{size:.2f}"))
        s["usdc"] = max(0.0, s["usdc"] - f["value"] - f["fees"])
        s["btc"] += f["base"]
        s["position"] = 1
        save_state(s)
        notify(f"🟢 COMPRA REAL {PRODUCT}: {f['base']:.8f} BTC a {f['avg']:,.2f} "
               f"({f['value']:.2f} USDC + taxa {f['fees']:.2f})")
        price = f["avg"] or price
    else:
        fill = price * (1 + SLIPPAGE)
        btc = s["usdc"] * (1 - FEE) / fill
        s.update(btc=btc, usdc=0.0, position=1)
        notify(f"🟢 [SIMULAÇÃO] COMPRA {btc:.6f} BTC a {fill:,.2f}")
    record({"time": datetime.now(timezone.utc).isoformat(), "side": "BUY", "price": price,
            "live": LIVE, "usdc": s["usdc"], "btc": s["btc"]})


def sell(s, price, candle=""):
    if LIVE:
        c = _client()
        avail, _ = _live_balances(c)
        qty = min(s["btc"], avail)  # nunca vende mais do que o bot comprou
        if qty * price < 5:
            notify(f"⚠️ VENDA ignorada: BTC do bot muito pequeno ({qty:.8f}). Posição zerada.")
            s.update(btc=0.0, position=0)
            return
        f = _wait_fill(c, c.market_order_sell(client_order_id=_order_id(candle, "SELL"),
                                              product_id=PRODUCT, base_size=_floor8(qty)))
        s["btc"] = max(0.0, s["btc"] - f["base"])
        if s["btc"] * price < 1:  # sobra de arredondamento
            s["btc"] = 0.0
        s["usdc"] += f["value"] - f["fees"]
        s["position"] = 0
        save_state(s)
        notify(f"🔴 VENDA REAL {PRODUCT}: {f['base']:.8f} BTC a {f['avg']:,.2f} "
               f"→ {f['value'] - f['fees']:.2f} USDC (taxa {f['fees']:.2f})")
        price = f["avg"] or price
    else:
        fill = price * (1 - SLIPPAGE)
        usdc = s["btc"] * fill * (1 - FEE)
        s.update(usdc=usdc, btc=0.0, position=0)
        notify(f"🔴 [SIMULAÇÃO] VENDA a {fill:,.2f} → {usdc:,.2f} USDC")
    record({"time": datetime.now(timezone.utc).isoformat(), "side": "SELL", "price": price,
            "live": LIVE, "usdc": s["usdc"], "btc": s["btc"]})


# ---------- loop ----------
def tick(s):
    lookback_days = max(60, int(P.min_bars() * 3 * TF_SECS[TF] / 86400) + 5)
    df = drop_open_candle(fetch_candles(PRODUCT, TF, lookback_days), TF)
    last = str(df.index[-1])
    if s["last_candle"] == last:
        return
    close = df["close"]
    want = int(signals(close, P).iloc[-1])
    price = spot_price()
    s.setdefault("start_price", price)  # referência do "comprar e segurar"
    s.setdefault("start_time", datetime.now(timezone.utc).isoformat())
    if want == 1 and s["position"] == 0:
        buy(s, price, last)
    elif want == 0 and s["position"] == 1:
        sell(s, price, last)
    s["last_candle"] = last
    eq = s["usdc"] + s["btc"] * price
    bh = s["start_equity"] * price / s["start_price"]
    s.update(signal=want, price=price, equity=eq, bh_equity=bh,
             ema_fast=float(ema(close, P.fast).iloc[-1]), ema_slow=float(ema(close, P.slow).iloc[-1]),
             ema_trend=float(ema(close, P.trend).iloc[-1]) if P.trend else None,
             updated=datetime.now(timezone.utc).isoformat())
    record({"candle": last, "price": round(price, 2), "equity": round(eq, 2),
            "bh_equity": round(bh, 2), "position": s["position"]}, EQUITY_F)
    log.info("candle %s | sinal=%d pos=%d | preço %.2f | patrimônio do bot %.2f (%+.2f%%)",
             last, want, s["position"], price, eq, (eq / s["start_equity"] - 1) * 100)
    save_state(s)


def sleep_until_next_close():
    step = TF_SECS[TF]
    now = time.time()
    time.sleep(step - now % step + 90)  # 90s após o fechamento, para a API consolidar o candle


def main():
    if env("LIVE") == "1" and not LIVE:
        sys.exit("LIVE=1 sem CONFIRM_LIVE=SIM_EU_ENTENDO_O_RISCO. Abortando por segurança.")
    if LIVE and LIVE_MAX_USDC <= 0:
        sys.exit("Modo real exige LIVE_MAX_USDC > 0.")
    s = load_state()
    if env("PANEL_PASSWORD"):
        import panel
        panel.start(STATE_DIR, int(env("PORT", 8080)), env("PANEL_PASSWORD"), spot_price,
                    {"product": PRODUCT, "tf": TF, "fast": P.fast, "slow": P.slow,
                     "trend": P.trend, "live": LIVE, "tf_secs": TF_SECS[TF]})
    else:
        log.info("Painel web desligado (defina PANEL_PASSWORD para ligar).")
    notify(f"🤖 Bot iniciado | {'REAL' if LIVE else 'SIMULAÇÃO'} | {PRODUCT} {TF} | "
           f"EMA {P.fast}/{P.slow} filtro {P.trend or '-'}")
    while True:
        try:
            tick(s)
        except Exception as e:  # nunca morrer silenciosamente
            notify(f"⚠️ Erro no ciclo: {e!r}")
            time.sleep(300)
            continue
        if env("RUN_ONCE") == "1":
            break
        sleep_until_next_close()


if __name__ == "__main__":
    main()
