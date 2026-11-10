"""Backtest com validação fora da amostra.

Uso:
  python backtest.py                     # baixa 4 anos de BTC-USDC 4h da Coinbase
  python backtest.py --tf 1d --days 2000
  python backtest.py --csv dados.csv     # CSV com colunas time,close (ou só closes, um por linha)

Metodologia (para não se enganar):
  1. Divide o histórico: primeiros 70% = treino, últimos 30% = teste.
  2. Escolhe os melhores parâmetros SÓ no treino.
  3. Reporta o resultado no teste (dados que a escolha nunca viu) vs. buy & hold.
  4. Inclui taxa (taker) + slippage em cada operação.
"""
import argparse
import itertools
import numpy as np
import pandas as pd
from strategy import Params, signals

FEE = 0.001       # taker da sua conta (tier Intro) = 0,10%
SLIPPAGE = 0.0005  # 0,05% estimado para ordem a mercado em BTC-USDC
BARS_PER_YEAR = {"1h": 8760, "2h": 4380, "4h": 2190, "6h": 1460, "1d": 365}


def run(close: pd.Series, p: Params, cost=FEE + SLIPPAGE) -> dict:
    pos = signals(close, p).shift(1).fillna(0)  # decide no fechamento t, ganha retorno t->t+1
    ret = close.pct_change().fillna(0)
    trades = pos.diff().abs().fillna(0)
    strat = pos * ret - trades * cost
    equity = (1 + strat).cumprod()
    return {"equity": equity, "trades": int(trades.sum()),
            "exposure": float(pos.mean())}


def stats(equity: pd.Series, bpy: int) -> dict:
    total = equity.iloc[-1] / equity.iloc[0] - 1
    r = equity.pct_change().dropna()
    sharpe = (r.mean() / r.std() * np.sqrt(bpy)) if r.std() > 0 else 0.0
    dd = (equity / equity.cummax() - 1).min()
    return {"retorno": total, "sharpe": sharpe, "max_dd": dd}


def grid():
    for f, s, t in itertools.product([10, 20, 30, 50], [50, 100, 150, 200], [0, 200]):
        if f < s:
            yield Params(f, s, t)


def evaluate(close: pd.Series, tf: str, split=0.7, verbose=True):
    bpy = BARS_PER_YEAR[tf]
    cut = int(len(close) * split)
    train, test_full = close.iloc[:cut], close  # teste usa histórico p/ aquecer EMAs
    rows = []
    for p in grid():
        r = run(train, p)
        st = stats(r["equity"], bpy)
        rows.append((p, st, r["trades"]))
    rows = [x for x in rows if x[2] >= 6]  # ignora combinações que quase não operaram
    if not rows:
        raise SystemExit("Histórico curto demais: nenhuma combinação operou ≥6 vezes no treino. "
                         "Use mais dados (ex.: --days 1460).")
    rows.sort(key=lambda x: x[1]["sharpe"], reverse=True)
    best = rows[0][0]

    r = run(test_full, best)
    eq_test = r["equity"].iloc[cut:]
    eq_test = eq_test / eq_test.iloc[0]
    bh = close.iloc[cut:] / close.iloc[cut]
    trades_test = int(signals(test_full, best).diff().abs().iloc[cut:].sum())
    res = {"best": best, "train": rows[0][1], "test": stats(eq_test, bpy),
           "bh_test": stats(bh, bpy), "trades_test": trades_test,
           "periodo_teste": (close.index[cut], close.index[-1]), "top5": rows[:5]}
    if verbose:
        report(res, len(close), tf)
    return res


def report(res, n, tf):
    pct = lambda x: f"{x*100:+.1f}%"
    print(f"\nCandles: {n} ({tf})  |  custo/operação: {(FEE+SLIPPAGE)*100:.2f}%")
    print("\nTop 5 no TREINO (por Sharpe):")
    for p, st, tr in res["top5"]:
        print(f"  EMA {p.fast}/{p.slow} filtro {p.trend or '-':>3}: ret {pct(st['retorno'])}, "
              f"sharpe {st['sharpe']:.2f}, DD {pct(st['max_dd'])}, {tr} trades")
    b = res["best"]
    a, z = res["periodo_teste"]
    print(f"\nEscolhido: EMA {b.fast}/{b.slow} filtro {b.trend or 'nenhum'}")
    print(f"TESTE fora da amostra ({a:%Y-%m-%d} → {z:%Y-%m-%d}):")
    t, h = res["test"], res["bh_test"]
    print(f"  Estratégia : ret {pct(t['retorno'])}, sharpe {t['sharpe']:.2f}, DD máx {pct(t['max_dd'])}, "
          f"{res['trades_test']} trocas de posição")
    print(f"  Buy & hold : ret {pct(h['retorno'])}, sharpe {h['sharpe']:.2f}, DD máx {pct(h['max_dd'])}")
    tr = res["train"]
    checks = {
        "Sharpe no treino > 0.5 (tem vantagem de verdade)": tr["sharpe"] > 0.5,
        "Sharpe no teste > 0 (continuou funcionando)": t["sharpe"] > 0,
        "Sharpe no teste > buy&hold": t["sharpe"] > h["sharpe"],
        "Drawdown no teste menor que buy&hold": t["max_dd"] > h["max_dd"],
        ">= 6 trocas no teste (amostra minima)": res["trades_test"] >= 6,
    }
    print()
    for k, ok in checks.items():
        print(f"  [{'OK' if ok else 'X '}] {k}")
    print(f"\nVeredito: {'PASSOU' if all(checks.values()) else 'NÃO PASSOU'}")


def load_csv(path):
    df = pd.read_csv(path, header=None if open(path).read(1).isdigit() else 0)
    if df.shape[1] == 1:
        vals = df.iloc[:, 0] if df.shape[0] > 1 else pd.Series(open(path).read().split(), dtype=float)
        return pd.Series(vals.values.astype(float),
                         index=pd.date_range("2000-01-01", periods=len(vals), freq="D", tz="UTC"))
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.set_index("time")["close"].astype(float)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", default="BTC-USDC")
    ap.add_argument("--tf", default="4h", choices=list(BARS_PER_YEAR))
    ap.add_argument("--days", type=int, default=365 * 4)
    ap.add_argument("--csv")
    a = ap.parse_args()
    if a.csv:
        close = load_csv(a.csv)
    else:
        from data_source import fetch_candles, drop_open_candle
        df = drop_open_candle(fetch_candles(a.pid, a.tf, a.days), a.tf)
        df.to_csv(f"candles_{a.pid}_{a.tf}.csv")
        close = df["close"]
    evaluate(close, a.tf)
