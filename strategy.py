"""Estratégia de tendência (long-only, spot): cruzamento de EMAs + filtro de tendência longa.

Regra (avaliada no FECHAMENTO de cada candle):
  - Comprado quando EMA_rapida > EMA_lenta E preço > EMA_filtro (se filtro ativo)
  - Fora (em USDC) caso contrário
Sem alavancagem, sem venda a descoberto.
"""
from dataclasses import dataclass
import pandas as pd


@dataclass(frozen=True)
class Params:
    fast: int = 20
    slow: int = 50
    trend: int = 0  # 0 = sem filtro; ex. 200 = só compra acima da EMA200

    def min_bars(self) -> int:
        return max(self.slow, self.trend)


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def signals(close: pd.Series, p: Params) -> pd.Series:
    """Retorna série 0/1 (1 = deve estar comprado) para cada candle fechado."""
    f, s = ema(close, p.fast), ema(close, p.slow)
    sig = (f > s)
    if p.trend:
        sig &= close > ema(close, p.trend)
    sig = sig.astype(int)
    sig.iloc[: p.min_bars() if len(sig) > p.min_bars() else 0] = 0  # aquecimento
    return sig
