"""Baixa candles públicos da Coinbase Advanced Trade (sem chave de API)."""
import time
import requests
import pandas as pd

BASE = "https://api.coinbase.com/api/v3/brokerage/market/products/{pid}/candles"
GRAN = {"1h": ("ONE_HOUR", 3600), "2h": ("TWO_HOUR", 7200), "4h": None,
        "6h": ("SIX_HOUR", 21600), "1d": ("ONE_DAY", 86400)}
# A API não tem 4h nativo: montamos 4h a partir de 1h.


def _fetch_raw(pid: str, gran: str, secs: int, start: int, end: int) -> list:
    out, cur = [], start
    step = secs * 300  # limite de 350 por chamada; 300 por segurança
    while cur < end:
        stop = min(cur + step, end)
        for attempt in range(5):
            try:
                r = requests.get(BASE.format(pid=pid), timeout=20,
                                 params={"start": cur, "end": stop, "granularity": gran})
                r.raise_for_status()
                out += r.json().get("candles", [])
                break
            except requests.RequestException:
                time.sleep(2 ** attempt)
        else:
            raise RuntimeError("Falha ao baixar candles da Coinbase")
        cur = stop
        time.sleep(0.15)
    return out


def fetch_candles(pid: str = "BTC-USDC", tf: str = "4h", days: int = 365 * 4) -> pd.DataFrame:
    end = int(time.time())
    start = end - days * 86400
    if tf == "4h":
        df = fetch_candles(pid, "1h", days)
        return (df.resample("4h", origin="epoch")
                  .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
                  .dropna())
    gran, secs = GRAN[tf]
    raw = _fetch_raw(pid, gran, secs, start, end)
    df = pd.DataFrame(raw)
    if df.empty:
        raise RuntimeError("Nenhum candle retornado")
    df["time"] = pd.to_datetime(df["start"].astype(int), unit="s", utc=True)
    df = df.drop(columns="start").set_index("time").astype(float)
    df = df[~df.index.duplicated()].sort_index()
    return df[["open", "high", "low", "close", "volume"]]


def drop_open_candle(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Remove o candle ainda em formação (só decidimos com candle fechado)."""
    secs = {"1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "1d": 86400}[tf]
    now = pd.Timestamp.now(tz="UTC")
    return df[df.index + pd.Timedelta(seconds=secs) <= now]
