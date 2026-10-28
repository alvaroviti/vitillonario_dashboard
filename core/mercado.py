"""Precios (yfinance), conversión a euros y señales de compra."""
from __future__ import annotations

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:  # el dashboard sigue funcionando con los últimos precios conocidos
    yf = None

FX = {"USD": "EURUSD=X", "JPY": "EURJPY=X", "GBP": "EURGBP=X"}
VENTANAS = {"3 m": 63, "6 m": 126, "1 a": 252, "3 a": 756, "5 a": 1260}


def descargar_historico(tickers: list[str], periodo: str = "5y") -> pd.DataFrame:
    """Cierres diarios, una columna por ticker. Vacío si no hay conexión."""
    tickers = sorted({t for t in tickers if isinstance(t, str) and t})
    if yf is None or not tickers:
        return pd.DataFrame()
    try:
        datos = yf.download(tickers, period=periodo, interval="1d", auto_adjust=True,
                            progress=False, group_by="column", threads=True)
    except Exception:
        return pd.DataFrame()
    if datos.empty:
        return pd.DataFrame()
    cierres = datos["Close"] if isinstance(datos.columns, pd.MultiIndex) else datos[["Close"]]
    if not isinstance(cierres, pd.DataFrame):
        cierres = cierres.to_frame()
    if cierres.shape[1] == 1 and len(tickers) == 1:
        cierres.columns = tickers
    cierres.index = pd.to_datetime(cierres.index).tz_localize(None)
    return cierres.dropna(how="all")


def descargar_ultimos(tickers: list[str]) -> pd.Series:
    """Último precio intradía disponible (retraso habitual de 15 min en bolsas europeas)."""
    tickers = sorted({t for t in tickers if isinstance(t, str) and t})
    if yf is None or not tickers:
        return pd.Series(dtype=float)
    try:
        datos = yf.download(tickers, period="5d", interval="15m", auto_adjust=True,
                            progress=False, group_by="column", threads=True)
    except Exception:
        return pd.Series(dtype=float)
    if datos.empty:
        return pd.Series(dtype=float)
    cierres = datos["Close"]
    if isinstance(cierres, pd.Series):
        cierres = cierres.to_frame(tickers[0])
    return cierres.ffill().iloc[-1].dropna()


def precios_actuales(pos: pd.DataFrame, instrumentos: pd.DataFrame,
                     historico: pd.DataFrame, ultimos: pd.Series) -> dict:
    """Devuelve {clave: (precio_eur, fuente)} para cada posición con dato de mercado."""
    cat = instrumentos.set_index("clave")
    out = {}

    def ultimo(t):
        if t in ultimos.index:
            return float(ultimos[t])
        if t in historico.columns and historico[t].notna().any():
            return float(historico[t].dropna().iloc[-1])
        return np.nan

    def a_eur(precio, moneda):
        if moneda in (None, "", "EUR") or pd.isna(moneda):
            return precio
        fx = ultimo(FX.get(moneda, ""))
        return precio / fx if fx and not np.isnan(fx) else np.nan

    for r in pos.drop_duplicates("clave").itertuples():
        if r.clave not in cat.index:
            continue
        inst = cat.loc[r.clave]
        if isinstance(inst.ticker, str) and inst.ticker:
            p = a_eur(ultimo(inst.ticker), inst.moneda_ticker)
            if not np.isnan(p):
                out[r.clave] = (p, f"mercado ({inst.ticker})")
                continue
        if isinstance(inst.proxy, str) and inst.proxy and pd.notna(r.precio_ref) and pd.notna(r.fecha_ref):
            serie = historico.get(inst.proxy)
            if serie is not None and serie.notna().any():
                base = serie[:pd.Timestamp(r.fecha_ref)].dropna()
                ahora = ultimo(inst.proxy)
                if not base.empty and not np.isnan(ahora):
                    out[r.clave] = (r.precio_ref * ahora / base.iloc[-1], f"estimado con {inst.proxy}")
    return out


# --------------------------------------------------------------------------- #
# Señales de compra
# --------------------------------------------------------------------------- #
def _rsi(serie: pd.Series, n: int = 14) -> float:
    delta = serie.diff()
    subida = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    bajada = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = subida / bajada.replace(0, np.nan)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def señales(serie: pd.Series, precio_actual: float | None = None) -> dict:
    s = serie.dropna()
    if len(s) < 30:
        return {}
    p = precio_actual if precio_actual and not np.isnan(precio_actual) else float(s.iloc[-1])
    d = {"precio": p, "historia_años": len(s) / 252}
    for etiqueta, n in VENTANAS.items():
        tramo = s.tail(n)
        d[f"min {etiqueta}"] = float(tramo.min()) if len(s) >= n * 0.9 else np.nan
        d[f"vs min {etiqueta}"] = p / d[f"min {etiqueta}"] - 1 if not np.isnan(d[f"min {etiqueta}"]) else np.nan
    año = s.tail(252)
    d["caída desde máx 1 a"] = p / año.max() - 1
    d["media 200"] = float(s.tail(200).mean()) if len(s) >= 200 else np.nan
    d["vs media 200"] = p / d["media 200"] - 1 if not np.isnan(d["media 200"]) else np.nan
    d["RSI 14"] = _rsi(pd.concat([s, pd.Series([p])], ignore_index=True))
    log = np.log(año)
    d["z 1 a"] = (np.log(p) - log.mean()) / log.std() if log.std() > 0 else 0.0

    puntos, motivos = 0, []
    if d["caída desde máx 1 a"] <= -0.20:
        puntos += 2; motivos.append("más de un 20 % bajo el máximo anual")
    elif d["caída desde máx 1 a"] <= -0.10:
        puntos += 1; motivos.append("más de un 10 % bajo el máximo anual")
    for etiqueta in ["6 m", "1 a", "3 a"]:
        v = d.get(f"vs min {etiqueta}")
        if v is not None and not np.isnan(v) and v <= 0.03:
            puntos += 1; motivos.append(f"cerca del mínimo de {etiqueta}")
    if not np.isnan(d["vs media 200"]) and d["vs media 200"] < 0:
        puntos += 1; motivos.append("bajo la media de 200 sesiones")
    if d["RSI 14"] < 30:
        puntos += 2; motivos.append("RSI en sobreventa (< 30)")
    elif d["RSI 14"] < 35:
        puntos += 1; motivos.append("RSI bajo (< 35)")
    if d["z 1 a"] < -1.5:
        puntos += 1; motivos.append("precio muy por debajo de su media anual")

    d["puntos"] = puntos
    d["señal"] = "Zona de compra" if puntos >= 4 else "Vigilar" if puntos >= 2 else "Sin señal"
    d["motivos"] = ", ".join(motivos) if motivos else "—"
    return d
