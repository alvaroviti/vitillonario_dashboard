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


def descargar_intradia(tickers: list[str]) -> pd.DataFrame:
    """Cierres cada 15 minutos de los últimos 5 días, en hora de Madrid."""
    tickers = sorted({t for t in tickers if isinstance(t, str) and t})
    if yf is None or not tickers:
        return pd.DataFrame()
    try:
        datos = yf.download(tickers, period="5d", interval="15m", auto_adjust=True,
                            progress=False, group_by="column", threads=True)
    except Exception:
        return pd.DataFrame()
    if datos.empty:
        return pd.DataFrame()
    cierres = datos["Close"]
    if isinstance(cierres, pd.Series):
        cierres = cierres.to_frame(tickers[0])
    idx = pd.to_datetime(cierres.index)
    idx = idx.tz_convert("Europe/Madrid") if idx.tz is not None else idx.tz_localize("UTC").tz_convert("Europe/Madrid")
    cierres.index = idx.tz_localize(None)
    return cierres.sort_index()


def ultimos_de(intradia: pd.DataFrame) -> pd.Series:
    """Último precio disponible de cada ticker (retraso habitual de 15 min en bolsas europeas)."""
    if intradia.empty:
        return pd.Series(dtype=float)
    return intradia.ffill().iloc[-1].dropna()


def descargar_ultimos(tickers: list[str]) -> pd.Series:
    return ultimos_de(descargar_intradia(tickers))


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


# --------------------------------------------------------------------------- #
# Consenso de analistas (solo acciones individuales)
# --------------------------------------------------------------------------- #
def objetivos_analistas(tickers: list[str]) -> pd.DataFrame:
    """Precio objetivo y recomendaciones de Yahoo Finance, en la moneda del ticker."""
    if yf is None:
        return pd.DataFrame()
    filas = []
    for t in sorted(set(tickers)):
        try:
            tk = yf.Ticker(t)
            obj = tk.analyst_price_targets or {}
            fila = dict(ticker=t, actual=obj.get("current"), objetivo_medio=obj.get("mean"),
                        objetivo_mediano=obj.get("median"), objetivo_min=obj.get("low"),
                        objetivo_max=obj.get("high"))
            rec = tk.recommendations_summary
            if rec is not None and not rec.empty:
                r = rec[rec["period"] == "0m"].iloc[0] if "0m" in set(rec["period"]) else rec.iloc[0]
                compra = int(r.get("strongBuy", 0) + r.get("buy", 0))
                mantener = int(r.get("hold", 0))
                venta = int(r.get("sell", 0) + r.get("strongSell", 0))
                fila.update(analistas=compra + mantener + venta, comprar=compra, mantener=mantener, vender=venta)
            filas.append(fila)
        except Exception:
            continue
    df = pd.DataFrame(filas)
    if df.empty or "objetivo_medio" not in df:
        return pd.DataFrame()
    df = df.dropna(subset=["objetivo_medio"])
    df["potencial"] = df["objetivo_medio"] / df["actual"] - 1
    return df


# --------------------------------------------------------------------------- #
# Índice de la cartera completa (composición actual)
# --------------------------------------------------------------------------- #
def serie_eur(ticker: str, moneda: str | None, historico: pd.DataFrame) -> pd.Series | None:
    if ticker not in historico.columns:
        return None
    s = historico[ticker].dropna()
    if moneda and moneda != "EUR" and not pd.isna(moneda):
        fx = historico.get(FX.get(moneda, ""))
        if fx is None:
            return None
        s = (s / fx.reindex(s.index).ffill()).dropna()
    return s if len(s) > 30 else None


def indice_cartera(pesos: pd.Series, referencias: dict[str, pd.Series], base: float = 100.0) -> pd.Series:
    """Evolución que habría tenido la cartera con la composición de hoy.

    pesos: valor actual por activo. referencias: serie de precios en EUR por activo
    (los activos sin serie, como monetarios o el efectivo, se toman como planos).
    Los días en que un activo todavía no cotizaba, su peso se reparte entre el resto.
    """
    if not referencias:
        return pd.Series(dtype=float)
    precios = pd.DataFrame(referencias).sort_index().ffill()
    rend = precios.pct_change()
    w = pesos.reindex(precios.columns).fillna(0)
    peso_plano = pesos.drop(labels=list(referencias), errors="ignore").sum()
    disponible = rend.notna().mul(w, axis=1)
    total = disponible.sum(axis=1) + peso_plano
    rend_cartera = rend.fillna(0).mul(w, axis=1).sum(axis=1) / total.replace(0, np.nan)
    rend_cartera = rend_cartera.iloc[1:].fillna(0)
    return base * (1 + rend_cartera).cumprod()


def ultimas_24h(serie: pd.Series) -> pd.Series:
    s = serie.dropna()
    if s.empty:
        return s
    return s[s.index >= s.index[-1] - pd.Timedelta(hours=24)]


def reconstruir_valor(indice: pd.Series, valor_hoy: float, flujos: pd.Series) -> pd.DataFrame:
    """Valor diario estimado de la cartera hacia atrás desde hoy.

    Cada día: valor = valor_anterior × (1 + rentabilidad del día) + aportación neta del día.
    El resultado del día es lo que se ha ganado o perdido sin contar las aportaciones.
    """
    if indice.empty:
        return pd.DataFrame()
    fechas = indice.index
    r = indice.pct_change().fillna(0).values
    f = pd.Series(0.0, index=fechas)
    if not flujos.empty:
        pos = np.clip(fechas.searchsorted(pd.to_datetime(flujos.index)), 0, len(fechas) - 1)
        for p, importe in zip(pos, flujos.values):
            f.iloc[p] += importe
    v = np.zeros(len(fechas))
    v[-1] = valor_hoy
    for t in range(len(fechas) - 1, 0, -1):
        v[t - 1] = max(0.0, (v[t] - f.iloc[t]) / (1 + r[t]))
    resultado = np.concatenate([[0.0], v[1:] - v[:-1] - f.values[1:]])
    return pd.DataFrame({"valor": v, "aportacion": f.values, "resultado": resultado}, index=fechas)
