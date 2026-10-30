"""Precios (yfinance), conversión a euros y señales de compra."""
from __future__ import annotations

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:  # el dashboard sigue funcionando con los últimos precios conocidos
    yf = None

FX = {"USD": "EURUSD=X", "JPY": "EURJPY=X", "GBP": "EURGBP=X"}
# Ventanas por tiempo (meses), no por número de filas: la cripto cotiza también en fin de semana
VENTANAS = {"3 m": 3, "6 m": 6, "1 a": 12, "2 a": 24, "3 a": 36, "5 a": 60}


def recortar(serie: pd.Series, meses: int) -> pd.Series:
    """Último tramo de `meses` meses de una serie."""
    s = serie.dropna()
    if s.empty:
        return s
    return s[s.index >= s.index[-1] - pd.DateOffset(months=meses)]


def cubre(serie: pd.Series, meses: int) -> bool:
    """¿Hay histórico suficiente para ese plazo? (con un pequeño margen)"""
    s = serie.dropna()
    return len(s) > 1 and s.index[0] <= s.index[-1] - pd.DateOffset(months=meses) + pd.Timedelta(days=10)


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
    """Cierres cada 15 minutos de los últimos 5 días, en hora de Madrid.
    Los fondos (tickers 0P… de Morningstar) no tienen precios intradía y se omiten."""
    tickers = sorted({t for t in tickers if isinstance(t, str) and t and not t.startswith("0P")})
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
    idx = idx.tz_localize("UTC") if idx.tz is None else idx
    # Se mantiene la zona horaria: al quitarla, el cambio de hora de octubre
    # duplicaría la franja de 2:00 a 3:00 y rompería los cálculos.
    cierres.index = idx.tz_convert("Europe/Madrid")
    return cierres[~cierres.index.duplicated(keep="last")].sort_index()


def ultimos_de(intradia: pd.DataFrame) -> pd.Series:
    """Último precio disponible de cada ticker (retraso habitual de 15 min en bolsas europeas)."""
    if intradia.empty:
        return pd.Series(dtype=float)
    return intradia.ffill().iloc[-1].dropna()


def descargar_ultimos(tickers: list[str]) -> pd.Series:
    return ultimos_de(descargar_intradia(tickers))


# Tickers cuyo precio no cuadra con el de tus extractos (otra clase del fondo, otra moneda…)
TICKERS_DESCARTADOS: dict[str, tuple[str, float]] = {}


def ticker_coherente(ticker: str, moneda, precio_ref, fecha_ref, historico: pd.DataFrame,
                     tolerancia: float = 0.15) -> tuple[bool, float]:
    """Compara el precio del ticker en la fecha de tu foto/última operación con el precio real de entonces.
    Si se separan más de un 15 %, el ticker no es el mismo producto (clase, moneda o unidad distinta)."""
    if pd.isna(precio_ref) or pd.isna(fecha_ref) or not precio_ref:
        return True, np.nan
    serie = serie_eur(ticker, moneda, historico)
    if serie is None:
        return True, np.nan
    fecha = pd.Timestamp(fecha_ref).tz_localize(None) if pd.Timestamp(fecha_ref).tzinfo else pd.Timestamp(fecha_ref)
    antes = serie[serie.index <= fecha + pd.Timedelta(days=1)]
    if antes.empty or (fecha - antes.index[-1]).days > 10:
        return True, np.nan
    ratio = float(antes.iloc[-1]) / float(precio_ref)
    return abs(ratio - 1) <= tolerancia, ratio


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

    TICKERS_DESCARTADOS.clear()
    for r in pos.drop_duplicates("clave").itertuples():
        if r.clave not in cat.index:
            continue
        inst = cat.loc[r.clave]
        if isinstance(inst.ticker, str) and inst.ticker:
            ok, ratio = ticker_coherente(inst.ticker, inst.moneda_ticker, r.precio_ref, r.fecha_ref, historico)
            if not ok:
                TICKERS_DESCARTADOS[inst.ticker] = (r.clave, ratio)
                p = np.nan
            else:
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
    d = {"precio": p, "historia_años": (s.index[-1] - s.index[0]).days / 365.25}
    for etiqueta, meses in VENTANAS.items():
        d[f"min {etiqueta}"] = float(recortar(s, meses).min()) if cubre(s, meses) else np.nan
        d[f"vs min {etiqueta}"] = p / d[f"min {etiqueta}"] - 1 if not np.isnan(d[f"min {etiqueta}"]) else np.nan
    año = recortar(s, 12)
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

    # Señales de venta / recoger beneficios: el espejo de las de compra
    pv, mv = 0, []
    maximos = {etq: float(recortar(s, m).max()) for etq, m in [("1 a", 12), ("3 a", 36), ("5 a", 60)] if cubre(s, m)}
    for etq, maximo in maximos.items():
        d[f"vs max {etq}"] = p / maximo - 1
    if "1 a" in maximos and p >= maximos["1 a"] * 0.98:
        pv += 1; mv.append("en máximos del último año")
    if "3 a" in maximos and p >= maximos["3 a"] * 0.98:
        pv += 1; mv.append("en máximos de 3 años")
    if not np.isnan(d["vs media 200"]):
        if d["vs media 200"] >= 0.25:
            pv += 2; mv.append("más de un 25 % sobre la media de 200 sesiones")
        elif d["vs media 200"] >= 0.15:
            pv += 1; mv.append("más de un 15 % sobre la media de 200 sesiones")
    if d["RSI 14"] > 75:
        pv += 2; mv.append("RSI en sobrecompra (> 75)")
    elif d["RSI 14"] > 70:
        pv += 1; mv.append("RSI alto (> 70)")
    if d["z 1 a"] > 2:
        pv += 2; mv.append("precio muy por encima de su media anual")
    elif d["z 1 a"] > 1.5:
        pv += 1; mv.append("precio por encima de lo normal en el año")
    d["puntos venta"] = pv
    d["motivos venta"] = mv
    return d


def señal_venta(puntos: int) -> str:
    return "Recoger beneficios" if puntos >= 5 else "Caliente" if puntos >= 3 else "—"


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
            try:
                obj = tk.analyst_price_targets or {}
            except Exception:
                obj = {}
            if not obj.get("mean"):
                # Alternativa: los mismos datos vienen también en la ficha general del valor
                info = tk.get_info() or {}
                obj = dict(current=info.get("currentPrice") or info.get("regularMarketPrice"),
                           mean=info.get("targetMeanPrice"), median=info.get("targetMedianPrice"),
                           low=info.get("targetLowPrice"), high=info.get("targetHighPrice"))
            fila = dict(ticker=t, actual=obj.get("current"), objetivo_medio=obj.get("mean"),
                        objetivo_mediano=obj.get("median"), objetivo_min=obj.get("low"),
                        objetivo_max=obj.get("high"))
            try:
                rec = tk.recommendations_summary
            except Exception:
                rec = None
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
    s = s[~s.index.duplicated(keep="last")]
    if moneda and moneda != "EUR" and not pd.isna(moneda):
        fx = historico.get(FX.get(moneda, ""))
        if fx is None:
            return None
        fx = fx.dropna()
        fx = fx[~fx.index.duplicated(keep="last")]
        s = (s / fx.reindex(s.index, method="ffill")).dropna()
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
    """Valor diario estimado de la cartera desde la primera aportación hasta hoy.

    Cada día: valor = valor_anterior × (1 + rentabilidad del día + ajuste) + aportación neta del día.
    El índice usa los pesos de hoy, así que no acierta exactamente; el «ajuste» es una corrección diaria
    constante que se calcula para que el valor final coincida con el valor real de hoy. Así la ganancia
    total cuadra con valor actual − dinero aportado, y se reparte en el tiempo según el mercado.
    """
    if indice.empty:
        return pd.DataFrame()
    fechas = indice.index
    r = indice.pct_change().fillna(0).values
    f = np.zeros(len(fechas))
    if not flujos.empty:
        idx_flujos = pd.to_datetime(flujos.index)
        pos = np.clip(fechas.searchsorted(idx_flujos), 0, len(fechas) - 1)
        for p, importe in zip(pos, flujos.values):
            f[p] += importe

    def simular(ajuste: float) -> np.ndarray:
        v = np.zeros(len(fechas))
        for t in range(1, len(fechas)):
            v[t] = max(0.0, v[t - 1] * (1 + r[t] + ajuste) + f[t])
        v[0] = max(0.0, f[0])
        return v

    bajo, alto = -0.01, 0.01
    for _ in range(60):  # bisección sobre el ajuste diario
        medio = (bajo + alto) / 2
        if simular(medio)[-1] < valor_hoy:
            bajo = medio
        else:
            alto = medio
    v = simular((bajo + alto) / 2)
    resultado = np.concatenate([[0.0], v[1:] - v[:-1] - f[1:]])
    return pd.DataFrame({"valor": v, "aportacion": f, "resultado": resultado}, index=fechas)


# --------------------------------------------------------------------------- #
# Ranking de rentabilidad y estabilidad
# --------------------------------------------------------------------------- #
def metricas_periodo(serie: pd.Series, meses: int) -> dict | None:
    """Rentabilidad, volatilidad y peor caída de una serie en los últimos `meses`.
    None si no hay histórico suficiente para ese plazo."""
    s = serie.dropna()
    if not cubre(s, meses):
        return None
    t = recortar(s, meses)
    if len(t) < 20:
        return None
    total = float(t.iloc[-1] / t.iloc[0] - 1)
    años = meses / 12
    dias = max((t.index[-1] - t.index[0]).days, 1)
    obs_año = len(t) / (dias / 365.25)  # 252 en bolsa, 365 en cripto
    vol = float(t.pct_change().dropna().std() * np.sqrt(obs_año))
    caida = float((t / t.cummax() - 1).min())
    anual = (1 + total) ** (1 / años) - 1 if total > -1 else -1.0
    return dict(rentabilidad=total, anualizada=anual, volatilidad=vol, peor_caida=caida,
                rent_riesgo=anual / vol if vol > 0 else np.nan)
