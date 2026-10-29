"""Vitillonario · Dashboard de inversiones (Trade Republic + MyInvestor)."""
from __future__ import annotations

import hmac
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from core import almacen, cartera, mercado, parsers

RAIZ = Path(__file__).resolve().parent
st.set_page_config(page_title="Vitillonario", page_icon="📈", layout="wide")

# Colores legibles tanto en tema claro como oscuro
VERDE, ROJO, TINTA, GRIS = "#2BB673", "#E5534B", "#4C9EEB", "#9AA5B1"
ORO, MORADO, TURQUESA, NARANJA = "#D4A72C", "#A371F7", "#3CC8C8", "#F0883E"
PALETA = [TINTA, VERDE, ORO, MORADO, NARANJA, TURQUESA, ROJO, GRIS]


# --------------------------------------------------------------------------- #
# Acceso: si hay contraseña en secrets, se pide antes de mostrar nada
# --------------------------------------------------------------------------- #
def acceso() -> bool:
    try:
        clave = st.secrets["password"]
    except Exception:
        clave = None
    if not clave or st.session_state.get("ok"):
        return True
    pwd = st.text_input("Contraseña", type="password")
    if pwd and hmac.compare_digest(pwd, clave):
        st.session_state["ok"] = True
        st.rerun()
    elif pwd:
        st.error("Contraseña incorrecta.")
    return False


if not acceso():
    st.stop()


def eur(x, dec=0):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x:,.{dec}f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(x, dec=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x * 100:+.{dec}f} %".replace(".", ",")


# --------------------------------------------------------------------------- #
# Carga de datos
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def cargar(_marca: float):
    tr = parsers.leer_tr()
    mi, avisos_mi = parsers.leer_mi()
    pos_mi = parsers.leer_posiciones_mi()
    instrumentos = pd.read_csv(RAIZ / "config" / "instrumentos.csv")
    f_manual = RAIZ / "config" / "fiscal_manual.csv"
    fiscal_manual = pd.read_csv(f_manual) if f_manual.exists() else pd.DataFrame(columns=["año", "casilla", "concepto", "importe"])
    perfiles, avisos_perfiles = cartera.leer_perfiles(RAIZ / "config" / "perfiles.csv")
    f_seg = RAIZ / "config" / "seguimiento.csv"
    seguimiento = pd.read_csv(f_seg) if f_seg.exists() else pd.DataFrame(columns=["ticker", "nombre", "moneda"])
    return tr, mi, avisos_mi + avisos_perfiles, pos_mi, instrumentos, fiscal_manual, perfiles, seguimiento


@st.cache_data(ttl=3600, show_spinner="Descargando históricos de 5 años…")
def historico(tickers: tuple[str, ...]):
    return mercado.descargar_historico(list(tickers), "5y")


@st.cache_data(ttl=6 * 3600, show_spinner="Consultando a los analistas…")
def analistas(tickers: tuple[str, ...]):
    return mercado.objetivos_analistas(list(tickers))


@st.cache_data(ttl=300, show_spinner="Actualizando precios…")
def intradia(tickers: tuple[str, ...]):
    return mercado.descargar_intradia(list(tickers))


def marca_datos() -> float:
    ficheros = list((RAIZ / "data").rglob("*")) + list((RAIZ / "config").glob("*.csv"))
    return max((f.stat().st_mtime for f in ficheros if f.is_file()), default=0)


# --------------------------------------------------------------------------- #
# Barra lateral: subida de ficheros y ajustes
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("Vitillonario")
    objetivo = st.number_input("Objetivo (€)", value=100_000, step=5_000)
    incluir_plan = st.toggle("Contar el plan de pensiones", value=True)
    incluir_efectivo = st.toggle("Contar el efectivo", value=False)
    refresco = st.selectbox("Refrescar precios", ["Manual", "Cada 5 min", "Cada 15 min"], index=1)
    if st.button("Actualizar ahora", width="stretch"):
        intradia.clear()

    st.divider()
    st.subheader("Actualizar datos")
    st.caption("Puedes subir exportaciones solapadas: los duplicados se eliminan solos.")
    subida_tr = st.file_uploader("Trade Republic (.csv)", type=["csv"], accept_multiple_files=True)
    subida_mi = st.file_uploader("MyInvestor · extracto de cuenta (.xls)", type=["xls", "html"], accept_multiple_files=True)
    subida_pos = st.file_uploader("MyInvestor · foto de posiciones (.csv)", type=["csv"])
    if st.button("Guardar ficheros", width="stretch", type="primary"):
        pendientes = []
        hoy = pd.Timestamp.today()
        for f in subida_tr or []:
            pendientes.append((f"tr_{hoy:%Y%m%d}_{f.name}", f.getvalue(), parsers.TR_DIR, "data/tr"))
        for f in subida_mi or []:
            pendientes.append((f"mi_{hoy:%Y%m%d}_{f.name}", f.getvalue(), parsers.MI_DIR, "data/myinvestor"))
        if subida_pos:
            nombre = subida_pos.name if subida_pos.name.startswith("posiciones_") else f"posiciones_{hoy:%Y-%m-%d}.csv"
            pendientes.append((nombre, subida_pos.getvalue(), parsers.MI_DIR, "data/myinvestor"))
        if not pendientes:
            st.info("No has seleccionado ningún fichero.")
        en_github = almacen.configurado(st.secrets)
        for nombre, contenido, carpeta, carpeta_repo in pendientes:
            parsers.guardar_subida(nombre, contenido, carpeta)
            if en_github:
                ok, detalle = almacen.subir(st.secrets, f"{carpeta_repo}/{nombre}", contenido, f"Datos: {nombre}")
                if not ok:
                    st.error(f"{nombre} se ha cargado, pero no se ha guardado en GitHub. {detalle}")
        if pendientes:
            st.success(f"{len(pendientes)} fichero(s) cargados" + (" y guardados en GitHub." if en_github else
                       ". Sin GitHub configurado, se perderán si la app se reinicia."))
        cargar.clear()

tr, mi, avisos_mi, pos_mi, instrumentos, fiscal_manual, perfiles, seguimiento = cargar(marca_datos())
ventas_tr, abiertas_tr, avisos_fifo = cartera.fifo_tr(tr) if not tr.empty else (pd.DataFrame(), pd.DataFrame(), [])
pos = cartera.posiciones(abiertas_tr, pos_mi, mi, instrumentos)

tickers_mercado = (set(instrumentos.ticker.dropna()) | set(instrumentos.proxy.dropna()) | set(mercado.FX.values())
                   | set(seguimiento.ticker.dropna()))
hist = historico(tuple(sorted(tickers_mercado)))


def pintar():
    intr = intradia(tuple(sorted(tickers_mercado)))
    ult = mercado.ultimos_de(intr)
    precios = mercado.precios_actuales(pos, instrumentos, hist, ult)
    val = cartera.valorar(pos, precios, instrumentos)

    efectivo_tr = parsers.saldo_efectivo_tr(tr)
    efectivo_mi = float(mi["saldo"].iloc[-1]) if not mi.empty else 0.0
    es_plan = val.plataforma.eq("MyInvestor (plan)")
    invertido = val.loc[~es_plan, "valor"].sum()
    plan = val.loc[es_plan, "valor"].sum()
    patrimonio = invertido + (plan if incluir_plan else 0) + ((efectivo_tr + efectivo_mi) if incluir_efectivo else 0)
    resultado_latente = val["resultado"].sum()

    sin_mercado = not len(ult) and hist.empty
    st.title("Vitillonario")
    if sin_mercado:
        st.warning("No hay conexión con Yahoo Finance: se muestran los últimos precios conocidos "
                   "(foto de MyInvestor y última operación en Trade Republic).")
    else:
        st.caption(f"Precios actualizados a las {pd.Timestamp.now():%H:%M}. "
                   "Las bolsas europeas suelen llegar con unos 15 minutos de retraso; "
                   "los fondos se estiman con un ETF equivalente hasta la siguiente foto de MyInvestor.")

    c = st.columns(4)
    c[0].metric("Patrimonio", eur(patrimonio))
    c[1].metric("Resultado latente", eur(resultado_latente), pct(resultado_latente / val["coste"].sum()))
    c[2].metric("Hasta el objetivo", eur(max(0, objetivo - patrimonio)), f"{patrimonio / objetivo:.0%} conseguido".replace(".", ","), delta_color="off")
    c[3].metric("Efectivo", eur(efectivo_tr + efectivo_mi), "TR " + eur(efectivo_tr) + " · MI " + eur(efectivo_mi), delta_color="off")

    t_hoy, t_mercado, t_cartera, t_aport, t_fiscal, t_datos = st.tabs(
        ["Hoy", "Mercado y señales", "Cartera", "Aportaciones y objetivo", "Fiscal", "Datos"])

    with t_hoy:
        pestaña_hoy(val, intr)
    with t_mercado:
        pestaña_mercado(val, ult, intr, efectivo_tr + efectivo_mi)
    with t_cartera:
        pestaña_cartera(val, efectivo_tr, efectivo_mi, intr)
    with t_aport:
        pestaña_aportaciones(patrimonio)
    with t_fiscal:
        pestaña_fiscal()
    with t_datos:
        pestaña_datos(val)


# --------------------------------------------------------------------------- #
# Pestañas
# --------------------------------------------------------------------------- #
def variacion_activo(ref: str, moneda: str | None, intr: pd.DataFrame) -> dict:
    """Precio en euros y variación del día (frente al cierre anterior) y de la semana."""
    out = dict(precio=np.nan, var_dia=np.nan, var_semana=np.nan, sesion=None)
    s_i = mercado.serie_eur(ref, moneda, intr) if not intr.empty else None
    s_d = mercado.serie_eur(ref, moneda, hist) if not hist.empty else None
    if s_i is not None and len(s_i):
        ultimo, momento = float(s_i.iloc[-1]), s_i.index[-1]
        dia = momento.date()
        antes = s_i[s_i.index.date < dia]
        cierre_ant = float(antes.iloc[-1]) if len(antes) else np.nan
        if np.isnan(cierre_ant) and s_d is not None:
            previos = s_d[s_d.index.date < dia]
            cierre_ant = float(previos.iloc[-1]) if len(previos) else np.nan
        out.update(precio=ultimo, var_dia=ultimo / cierre_ant - 1 if cierre_ant else np.nan, sesion=momento)
    elif s_d is not None and len(s_d) > 1:
        out.update(precio=float(s_d.iloc[-1]), var_dia=float(s_d.iloc[-1] / s_d.iloc[-2] - 1), sesion=s_d.index[-1])
    if s_d is not None and len(s_d) and not np.isnan(out["precio"]):
        hace_semana = pd.Timestamp(out["sesion"]).tz_localize(None) - pd.Timedelta(days=7) if out["sesion"] is not None else None
        base = s_d[:hace_semana] if hace_semana is not None else s_d.iloc[:-5]
        if len(base):
            out["var_semana"] = out["precio"] / float(base.iloc[-1]) - 1
    return out


def pestaña_hoy(val: pd.DataFrame, intr: pd.DataFrame):
    if intr.empty and hist.empty:
        st.info("Sin conexión con el mercado: aquí verás las subidas y bajadas del día en cuanto haya datos.")
        return
    cat = instrumentos.set_index("clave")
    base = val if incluir_plan else val[val.plataforma != "MyInvestor (plan)"]
    pos = base.groupby("clave", as_index=False).agg(activo=("nombre_corto", "first"), coste=("coste", "sum"),
                                                     valor=("valor", "sum"))
    pos = pos[pos.valor > 0.5]
    filas = []
    for r in pos.itertuples():
        ref, moneda, equivalente = referencia_de(r.clave, cat)
        v = variacion_activo(ref, moneda, intr) if ref else dict(var_dia=np.nan, var_semana=np.nan, sesion=None)
        filas.append(dict(activo=r.activo + (" *" if equivalente else ""), var_dia=v["var_dia"],
                          var_semana=v["var_semana"], invertido=r.coste, valor=r.valor,
                          ganancia=r.valor - r.coste, ganancia_pct=(r.valor - r.coste) / r.coste if r.coste else np.nan))
    df = pd.DataFrame(filas)
    df["peso"] = df.valor / df.valor.sum()
    df["hoy_eur"] = df.valor * (1 - 1 / (1 + df.var_dia.fillna(0)))
    df["semana_eur"] = df.valor * (1 - 1 / (1 + df.var_semana.fillna(0)))
    total = df.valor.sum()

    c = st.columns(4)
    hoy_eur, sem_eur = df.hoy_eur.sum(), df.semana_eur.sum()
    c[0].metric("Valor de la cartera", eur(total))
    c[1].metric("Hoy", eur(hoy_eur), pct(hoy_eur / (total - hoy_eur)))
    c[2].metric("Esta semana", eur(sem_eur), pct(sem_eur / (total - sem_eur)))
    c[3].metric("Ganancia total", eur(df.ganancia.sum()), pct(df.ganancia.sum() / df.invertido.sum()))

    orden = st.segmented_control("Ordenar por", ["% cartera", "Hoy %", "Semana %", "Ganancia"], default="% cartera",
                                 label_visibility="collapsed") or "% cartera"
    clave_orden = {"% cartera": "peso", "Hoy %": "var_dia", "Semana %": "var_semana", "Ganancia": "ganancia"}[orden]
    df = df.sort_values(clave_orden, ascending=False, na_position="last")

    def flecha(x):
        if pd.isna(x):
            return "—"
        return ("▲ " if x > 0 else "▼ " if x < 0 else "● ") + f"{abs(x) * 100:.2f} %".replace(".", ",")

    def color(x):
        if pd.isna(x) or abs(x) < 1e-9:
            return f"color: {GRIS}"
        return f"color: {VERDE}; font-weight: 600" if x > 0 else f"color: {ROJO}; font-weight: 600"

    vista = df[["activo", "var_dia", "var_semana", "peso", "invertido", "valor", "ganancia", "ganancia_pct", "hoy_eur"]]
    estilo = (vista.style
              .format({"var_dia": flecha, "var_semana": flecha, "ganancia_pct": flecha,
                       "peso": lambda x: f"{x * 100:.1f} %".replace(".", ","),
                       "invertido": lambda x: eur(x), "valor": lambda x: eur(x),
                       "ganancia": lambda x: eur(x), "hoy_eur": lambda x: eur(x)}, na_rep="—")
              .map(color, subset=["var_dia", "var_semana", "ganancia", "ganancia_pct", "hoy_eur"]))
    st.dataframe(estilo, hide_index=True, width="stretch", height=min(40 + 35 * len(vista), 640), column_config={
        "var_dia": st.column_config.Column("hoy"), "var_semana": st.column_config.Column("semana"),
        "peso": st.column_config.Column("% cartera"), "ganancia_pct": st.column_config.Column("ganancia %"),
        "hoy_eur": st.column_config.Column("hoy €"),
    })

    movidos = df.dropna(subset=["var_dia"])
    movidos = movidos[movidos.var_dia.abs() > 1e-9].sort_values("var_dia")
    if len(movidos):
        st.markdown("**Movimiento de hoy**")
        fig = go.Figure(go.Bar(x=movidos.var_dia, y=movidos.activo, orientation="h",
                               marker_color=np.where(movidos.var_dia >= 0, VERDE, ROJO),
                               text=[flecha(x) for x in movidos.var_dia], textposition="outside",
                               customdata=movidos.hoy_eur,
                               hovertemplate="%{y}: %{x:.2%} (%{customdata:.0f} €)<extra></extra>"))
        fig.update_layout(height=max(260, 28 * len(movidos)), margin=dict(l=10, r=60, t=10, b=10),
                          xaxis=dict(tickformat=".1%", title=None), yaxis=dict(title=None),
                          plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, width="stretch")

    st.caption("«Hoy» compara con el cierre de la sesión anterior; si la bolsa está cerrada, muestra la última sesión. "
               "Precios cada 15 minutos (con retraso en bolsas europeas). * = fondo estimado con un ETF equivalente. "
               "Monetarios, bono y Private Equity no cotizan a diario y aparecen con «—». Variaciones en euros.")


PERIODOS = {"1 d": 0, "1 m": 21, "6 m": 126, "1 a": 252, "2 a": 504, "3 a": 756, "5 a": 1260}


def referencia_de(clave: str, cat: pd.DataFrame) -> tuple[str | None, str | None, bool]:
    """(ticker de referencia, moneda, es_equivalente) de una posición."""
    if clave not in cat.index:
        return None, None, False
    inst = cat.loc[clave]
    if isinstance(inst.ticker, str) and inst.ticker:
        return inst.ticker, inst.moneda_ticker, False
    if isinstance(inst.proxy, str) and inst.proxy:
        return inst.proxy, "EUR", True
    return None, None, False


def pesos_cartera(val: pd.DataFrame, con_efectivo: float = 0.0) -> pd.Series:
    base = val if incluir_plan else val[val.plataforma != "MyInvestor (plan)"]
    pesos = base.groupby("clave")["valor"].sum()
    pesos = pesos[pesos > 0]
    if con_efectivo:
        pesos["EFECTIVO"] = con_efectivo
    return pesos


def indice_de(pesos: pd.Series, marco: pd.DataFrame) -> tuple[pd.Series, float]:
    """Índice de la cartera sobre un marco de precios (diario o intradía) y parte del valor cubierta."""
    cat = instrumentos.set_index("clave")
    refs = {}
    for clave in pesos.index:
        t, moneda, _ = referencia_de(clave, cat)
        if t:
            serie = mercado.serie_eur(t, moneda, marco)
            if serie is not None:
                refs[clave] = serie
    cubierto = pesos[list(refs)].sum() / pesos.sum() if refs else 0.0
    return mercado.indice_cartera(pesos, refs), cubierto


@st.cache_data(ttl=300, show_spinner=False)
def reconstruccion(pesos: pd.Series, _marca: float, con_plan: bool):
    indice, _ = indice_de(pesos, hist)
    flujos = cartera.flujos_diarios(tr, mi, con_plan)
    return mercado.reconstruir_valor(indice, float(pesos.sum()), flujos)


def resultado_periodo(pesos_inv: pd.Series, periodo: str, intr: pd.DataFrame) -> float:
    """Ganado o perdido por la cartera invertida en el periodo, sin contar aportaciones."""
    if periodo == "1 d":
        ind, _ = indice_de(pesos_inv, intr)
        ind = mercado.ultimas_24h(ind)
        return float(pesos_inv.sum() * (1 - ind.iloc[0] / ind.iloc[-1])) if len(ind) > 1 else np.nan
    rec = reconstruccion(pesos_inv, marca_datos(), incluir_plan)
    if rec.empty:
        return np.nan
    return float(rec["resultado"].tail(PERIODOS[periodo]).sum())


def pestaña_mercado(val: pd.DataFrame, ult: pd.Series, intr: pd.DataFrame, efectivo: float = 0.0):
    if hist.empty:
        st.info("Sin históricos de mercado. En cuanto haya conexión verás mínimos de 3 meses a 5 años y las señales.")
        return
    cat = instrumentos.set_index("clave")
    agrupado = val.groupby("clave", as_index=False).agg(nombre=("nombre_corto", "first"), valor=("valor", "sum"))
    total = agrupado.valor.sum()
    filas = []
    for r in agrupado.itertuples():
        ref, _, equivalente = referencia_de(r.clave, cat)
        if not ref or ref not in hist.columns:
            continue
        precio = float(ult[ref]) if ref in ult.index else None
        sen = mercado.señales(hist[ref], precio)
        if sen:
            filas.append(dict(clave=r.clave, activo=r.nombre, referencia=ref + (" (equivalente)" if equivalente else ""),
                              en_cartera=r.valor, **sen))
    for r in seguimiento.itertuples():
        if r.ticker in hist.columns:
            sen = mercado.señales(hist[r.ticker], float(ult[r.ticker]) if r.ticker in ult.index else None)
            if sen:
                filas.append(dict(clave=f"SEG_{r.ticker}", activo=f"{r.nombre} (seguimiento)", referencia=r.ticker,
                                  en_cartera=0.0, **sen))
    if not filas:
        st.info("Ninguna posición tiene ticker o equivalente con histórico disponible.")
        return
    df = pd.DataFrame(filas)
    numericas = [c for c in df.columns if c.startswith(("vs ", "min ", "caída", "media", "RSI", "z "))]
    df[numericas] = df[numericas].apply(pd.to_numeric, errors="coerce")
    # Varios fondos con el mismo equivalente comparten fila: se suma lo que tienes en ellos
    df = df.sort_values("en_cartera", ascending=False)
    unidos = df.groupby("referencia", sort=False).agg(
        activo=("activo", lambda x: x.iloc[0] + (f" y {len(x) - 1} más" if len(x) > 1 else "")),
        en_cartera=("en_cartera", "sum"))
    df = df.drop_duplicates("referencia").drop(columns=["activo", "en_cartera"]).merge(unidos, on="referencia")
    df["peso"] = df.en_cartera / total
    df = df.sort_values("en_cartera", ascending=False)

    zona = df[df["señal"] == "Zona de compra"]
    if not zona.empty:
        st.success("En zona de compra según los indicadores: " + ", ".join(zona.activo))

    pesos_inv = pesos_cartera(val)
    indice, cubierto = indice_de(pesos_cartera(val, efectivo if incluir_efectivo else 0.0), hist)
    s_cartera = mercado.señales(indice) if len(indice) > 30 else {}
    if s_cartera:
        caida = s_cartera["caída desde máx 1 a"]
        detalle = (f"está un {abs(caida) * 100:.1f} % por debajo de su máximo anual".replace(".", ",")
                   if caida < -0.001 else "está en máximos del último año")
        st.markdown(f"**Cartera completa:** {s_cartera['señal'].lower()} ({s_cartera['puntos']} puntos); {detalle}.")
    st.caption("Ordenado por lo que tienes en cada activo; pulsa en una columna para ordenar por ella. "
               "La puntuación suma caída desde máximos, cercanía a mínimos, media de 200 sesiones, RSI y "
               "desviación respecto a la media anual. Es una ayuda para decidir, no una recomendación.")

    porcentajes = ["caída desde máx 1 a", "vs min 3 m", "vs min 6 m", "vs min 1 a", "vs min 2 a", "vs min 3 a",
                   "vs min 5 a", "vs media 200"]
    vista = df[["activo", "en_cartera", "peso", "señal", "puntos"] + porcentajes + ["RSI 14", "referencia", "motivos"]]
    formato_pct = lambda x: f"{x * 100:.1f} %".replace(".", ",")
    formatos = {c: formato_pct for c in porcentajes + ["peso"]}
    formatos.update({"en_cartera": lambda x: eur(x), "RSI 14": "{:.0f}"})
    estilo = vista.style.format(formatos, na_rep="—")  # una sola llamada: una segunda borraría la primera
    st.dataframe(estilo, hide_index=True, width="stretch", column_config={
        "en_cartera": st.column_config.Column("en cartera"),
        "peso": st.column_config.Column("% cartera"),
        "puntos": st.column_config.ProgressColumn(min_value=0, max_value=9, format="%d"),
    })
    st.caption("«—» significa que el activo no tiene histórico suficiente para ese plazo.")

    # ------------------------------------------------------------------ gráfico
    st.markdown("**Gráfico**")
    c1, c2 = st.columns([2, 3])
    opciones = (["Cartera completa"] if s_cartera else []) + df["activo"].tolist()
    elegido = c1.selectbox("Activo", opciones, label_visibility="collapsed")
    periodo = c2.segmented_control("Periodo", list(PERIODOS), default="1 a", label_visibility="collapsed") or "1 a"
    es_cartera = elegido == "Cartera completa"

    if es_cartera:
        nombre = "Cartera (base 100)"
        if periodo == "1 d":
            base_intr, _ = indice_de(pesos_cartera(val, efectivo if incluir_efectivo else 0.0), intr)
            serie_total = mercado.ultimas_24h(base_intr)
        else:
            serie_total = indice
    else:
        fila = df[df.activo == elegido].iloc[0]
        ref = fila.referencia.replace(" (equivalente)", "")
        nombre = ref
        serie_total = mercado.ultimas_24h(intr[ref]) if periodo == "1 d" and ref in intr else hist[ref].dropna()

    if periodo == "1 d":
        serie, media = serie_total, None
    else:
        media_completa = serie_total.rolling(200).mean()
        serie = serie_total.tail(PERIODOS[periodo])
        media = media_completa.reindex(serie.index)
    if es_cartera and len(serie):
        factor = 100 / serie.iloc[0]
        serie = serie * factor
        media = media * factor if media is not None else None

    if len(serie) < 2:
        st.info("No hay datos de ese periodo para este activo (puede que el mercado esté cerrado).")
    else:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=serie.index, y=serie, name=nombre,
                                 line=dict(color=VERDE if es_cartera else TINTA, width=1.8)))
        if media is not None and media.notna().any():
            fig.add_trace(go.Scatter(x=media.index, y=media, name="Media 200", line=dict(color=GRIS, width=1, dash="dot")))
        fig.add_hline(y=serie.min(), line=dict(color=ROJO, width=1, dash="dash"),
                      annotation_text=f"Mín {periodo}", annotation_position="bottom right")
        fig.add_hline(y=serie.max(), line=dict(color=VERDE, width=1, dash="dash"),
                      annotation_text=f"Máx {periodo}", annotation_position="top right")
        fig.update_layout(height=420, margin=dict(l=10, r=10, t=20, b=10), legend=dict(orientation="h"),
                          plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, width="stretch")

    # ------------------------------------------------------------------ resultado del periodo
    variacion = serie.iloc[-1] / serie.iloc[0] - 1 if len(serie) > 1 else np.nan
    m = st.columns(3)
    m[0].metric(f"Variación {periodo}", pct(variacion))
    res_cartera = resultado_periodo(pesos_inv, periodo, intr)
    m[1].metric(f"Resultado de la cartera en {periodo}", eur(res_cartera), delta_color="off")
    if not es_cartera:
        valor_pos = float(df[df.activo == elegido].en_cartera.iloc[0])
        m[2].metric(f"Resultado de esta posición en {periodo}",
                    eur(valor_pos * (1 - 1 / (1 + variacion)) if not np.isnan(variacion) else np.nan))

    textos = []
    if periodo != "1 d":
        años_disp = len(serie_total) / 252
        if años_disp < PERIODOS[periodo] / 252 * 0.95:
            textos.append(f"Solo hay {años_disp:.1f} años de histórico.".replace(".", ",", 1))
    textos.append("El resultado de la cartera es lo ganado o perdido sin contar lo que has aportado, estimado día a día "
                  "con tus movimientos y los precios de mercado; no incluye el efectivo.")
    if not es_cartera:
        textos.append("El de la posición usa lo que tienes hoy en ella.")
    if es_cartera:
        textos.append(f"La línea simula tu cartera con los pesos de hoy. Cubre con precios de mercado el "
                      f"{cubierto * 100:.0f} % del valor; monetarios, bono y efectivo se toman como planos.")
    st.caption(" ".join(textos))

    seccion_comparador(df, indice)
    seccion_seguimiento()
    seccion_analistas(val)


def nombre_ticker(t: str) -> str:
    try:
        import yfinance as yf
        info = yf.Ticker(t).get_info()
        return info.get("shortName") or info.get("longName") or t
    except Exception:
        return t


def seccion_comparador(df: pd.DataFrame, indice: pd.Series):
    st.subheader("Comparador")
    opciones = {"Cartera completa": None} if len(indice) > 30 else {}
    for r in df.itertuples():
        opciones[r.activo] = r.referencia.replace(" (equivalente)", "")
    c1, c2 = st.columns([3, 2])
    elegidos = c1.multiselect("Activos de tu cartera o seguimiento", list(opciones),
                              default=list(opciones)[:2], placeholder="Elige uno o varios")
    extra = c2.text_input("Añadir tickers de Yahoo Finance", placeholder="p. ej. ^GSPC, AAPL, IWDA.AS",
                          help="Separados por comas. Índices con ^ (^GSPC S&P 500, ^STOXX50E Euro Stoxx 50, ^IBEX).")
    periodo = st.segmented_control("Periodo del comparador", [p for p in PERIODOS if p != "1 d"], default="1 a",
                                   label_visibility="collapsed") or "1 a"

    series = {}
    for nombre in elegidos:
        t = opciones[nombre]
        s_ = indice if t is None else hist[t].dropna() if t in hist.columns else None
        if s_ is not None:
            series[nombre] = s_
    nuevos = [t.strip().upper() if not t.strip().startswith("^") else t.strip() for t in extra.split(",") if t.strip()]
    if nuevos:
        h_extra = historico(tuple(sorted(nuevos)))
        for t in nuevos:
            if t in h_extra.columns and h_extra[t].notna().sum() > 30:
                series[t] = h_extra[t].dropna()
            else:
                st.warning(f"No he encontrado datos de «{t}» en Yahoo Finance. Revisa el ticker.")
    if not series:
        st.caption("Elige algún activo o escribe un ticker para compararlos.")
        return

    n = PERIODOS[periodo]
    fig, filas = go.Figure(), []
    for (nombre, s_), color in zip(series.items(), PALETA * 3):
        tramo = s_.tail(n)
        if len(tramo) < 2:
            continue
        norm = 100 * tramo / tramo.iloc[0]
        fig.add_trace(go.Scatter(x=norm.index, y=norm, name=nombre, line=dict(color=color, width=1.8)))
        rend = tramo.pct_change().dropna()
        filas.append(dict(activo=nombre, rentabilidad=norm.iloc[-1] / 100 - 1,
                          volatilidad=rend.std() * np.sqrt(252),
                          peor_caida=(tramo / tramo.cummax() - 1).min()))
    fig.add_hline(y=100, line=dict(color=GRIS, width=1, dash="dot"))
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=20, b=10), legend=dict(orientation="h"),
                      plot_bgcolor="rgba(0,0,0,0)", yaxis=dict(title="Base 100"))
    st.plotly_chart(fig, width="stretch")
    st.dataframe(pd.DataFrame(filas).style.format({c: lambda x: f"{x * 100:.1f} %".replace(".", ",")
                                                   for c in ["rentabilidad", "volatilidad", "peor_caida"]}),
                 hide_index=True, width="stretch",
                 column_config={"peor_caida": st.column_config.Column("peor caída"),
                                "volatilidad": st.column_config.Column("volatilidad anual")})
    st.caption("Todas las líneas parten de 100 al inicio del periodo. Los tickers en dólares se comparan en su "
               "moneda, sin convertir a euros.")


def seccion_seguimiento():
    with st.expander("Valores en seguimiento"):
        st.caption("Activos que no tienes pero quieres vigilar: aparecen en la tabla de señales y en el comparador.")
        tabla = st.data_editor(seguimiento, num_rows="dynamic", hide_index=True, width="stretch",
                               column_config={
                                   "ticker": st.column_config.TextColumn("ticker de Yahoo", required=True),
                                   "nombre": st.column_config.TextColumn("nombre (opcional)"),
                                   "moneda": st.column_config.SelectboxColumn(options=["EUR", "USD", "GBP", "JPY"], default="EUR"),
                               }, key="editor_seguimiento")
        if st.button("Guardar seguimiento"):
            tabla = tabla.dropna(subset=["ticker"])
            tabla["ticker"] = tabla["ticker"].str.strip()
            tabla["nombre"] = [n if isinstance(n, str) and n.strip() else nombre_ticker(t)
                               for t, n in zip(tabla.ticker, tabla.nombre)]
            contenido = tabla.to_csv(index=False).encode("utf-8")
            (RAIZ / "config" / "seguimiento.csv").write_bytes(contenido)
            if almacen.configurado(st.secrets):
                ok, detalle = almacen.subir(st.secrets, "config/seguimiento.csv", contenido, "Seguimiento actualizado")
                st.success("Guardado en GitHub.") if ok else st.error(f"No se ha guardado en GitHub. {detalle}")
            else:
                st.info("Guardado mientras la app siga encendida (sin GitHub configurado no es permanente).")
            cargar.clear()
            historico.clear()
            st.rerun()


def seccion_analistas(val: pd.DataFrame):
    st.subheader("Precio objetivo de los analistas")
    cat = instrumentos.set_index("clave")
    acciones = [c for c in val.clave.unique() if c in cat.index and cat.loc[c, "tipo"] == "accion"
                and isinstance(cat.loc[c, "ticker"], str)]
    if not acciones:
        st.caption("No tienes acciones individuales en cartera.")
        return
    mapa = {cat.loc[c, "ticker"]: cat.loc[c, "nombre"] for c in acciones}
    df = analistas(tuple(sorted(mapa)))
    if df.empty:
        st.info("Yahoo Finance no ha devuelto datos de analistas ahora mismo. Se vuelve a intentar cada pocas horas.")
        return
    df.insert(0, "activo", df.ticker.map(mapa))
    monedas = {cat.loc[c, "ticker"]: cat.loc[c, "moneda_ticker"] for c in acciones}
    df["moneda"] = df.ticker.map(monedas)
    cols = ["activo", "ticker", "moneda", "actual", "objetivo_medio", "potencial", "objetivo_min", "objetivo_max",
            "analistas", "comprar", "mantener", "vender"]
    st.dataframe(df[[c for c in cols if c in df.columns]], hide_index=True, width="stretch", column_config={
        **{c: st.column_config.NumberColumn(format="%.2f") for c in ["actual", "objetivo_medio", "objetivo_min", "objetivo_max"]},
        "potencial": st.column_config.NumberColumn("potencial al objetivo medio", format="percent"),
    })
    st.caption("Consenso de Yahoo Finance a 12 meses, en la moneda de cotización. Solo existe para acciones: "
               "los ETFs, fondos, oro y cripto no tienen precio objetivo. Los analistas fallan a menudo y suelen "
               "ir con retraso respecto al precio; úsalo como un dato más junto a las señales.")


def seccion_resultado_mensual(val: pd.DataFrame):
    st.subheader("Resultado por mes")
    if hist.empty:
        st.caption("Necesita precios de mercado para calcularse.")
        return
    rec = reconstruccion(pesos_cartera(val), marca_datos(), incluir_plan)
    if rec.empty:
        return
    mensual = rec["resultado"].groupby(rec.index.to_period("M")).sum()
    hoy = pd.Timestamp.today()
    c = st.columns(4)
    c[0].metric("Este mes", eur(mensual.iloc[-1]))
    c[1].metric("Últimos 3 meses", eur(mensual.tail(3).sum()))
    c[2].metric("Últimos 12 meses", eur(mensual.tail(12).sum()))
    c[3].metric(f"En {hoy.year}", eur(mensual[mensual.index.year == hoy.year].sum()))

    meses = st.segmented_control("Meses", ["12 meses", "24 meses", "Todo"], default="12 meses",
                                 label_visibility="collapsed") or "12 meses"
    datos = mensual if meses == "Todo" else mensual.tail(int(meses.split()[0]))
    datos = datos[datos.index >= rec.index[rec.valor > 1].min().to_period("M")] if (rec.valor > 1).any() else datos
    x = datos.index.to_timestamp()
    fig = go.Figure(go.Bar(x=x, y=datos.values, marker_color=np.where(datos.values >= 0, VERDE, ROJO),
                           text=[eur(v) for v in datos.values], textposition="outside"))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=20, b=10), plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(tickformat="%m/%y"))
    st.plotly_chart(fig, width="stretch")
    st.caption("Lo ganado o perdido cada mes sin contar tus aportaciones, sin efectivo. Es una estimación: se "
               "reconstruye el valor día a día desde tu primera compra con tus movimientos y los precios de mercado, "
               "ajustado para que cuadre con el valor de hoy.")

    mens = cartera.resumen_mensual(rec)
    anual = cartera.resumen_anual(mens)
    if anual.empty:
        return
    st.subheader("Resumen anual")
    pct_es = lambda x: f"{x * 100:.2f} %".replace(".", ",")
    vista = anual.rename(columns={"año": "Año", "aportado": "Inversión del año", "beneficio": "Ganancia",
                                  "rentabilidad_suma": "Rentabilidad (suma mensual)",
                                  "rentabilidad_encadenada": "Rentabilidad encadenada",
                                  "promedio_mes": "Promedio ganancia / mes", "valor_final": "Valor final"})
    st.dataframe(vista.style.format({
        "Año": "{:d}", "Inversión del año": lambda x: eur(x), "Ganancia": lambda x: eur(x),
        "Rentabilidad (suma mensual)": pct_es, "Rentabilidad encadenada": pct_es,
        "Promedio ganancia / mes": lambda x: eur(x, 2), "Valor final": lambda x: eur(x)}),
        hide_index=True, width="stretch")
    st.caption("«Promedio ganancia / mes» divide la ganancia del año entre 12, o entre el mes actual en el año en "
               "curso. La rentabilidad mensual es el beneficio del mes entre el valor al empezar el mes más lo "
               "aportado. «Suma mensual» suma esas rentabilidades, como tu Excel; «encadenada» las compone mes a "
               "mes, que es la rentabilidad real del año.")

    with st.expander("Detalle mes a mes"):
        det = mens.rename_axis("mes").reset_index()
        det["mes"] = det["mes"].astype(str)
        det = det[["mes", "valor", "aportacion", "aportado_acumulado", "rentabilidad", "beneficio", "beneficio_acumulado"]]
        det = det.rename(columns={"valor": "valor actual", "aportacion": "inversión", "aportado_acumulado": "inversión acumulada",
                                  "beneficio_acumulado": "beneficio acumulado"})
        st.dataframe(det.iloc[::-1].style.format({
            "valor actual": lambda x: eur(x), "inversión": lambda x: eur(x), "inversión acumulada": lambda x: eur(x),
            "rentabilidad": pct_es, "beneficio": lambda x: eur(x), "beneficio acumulado": lambda x: eur(x)}, na_rep="—"),
            hide_index=True, width="stretch", height=380)


def pestaña_cartera(val: pd.DataFrame, efectivo_tr: float, efectivo_mi: float, intr: pd.DataFrame):
    por_plat = val.groupby("plataforma")[["coste", "valor", "resultado"]].sum()
    por_plat.loc["Efectivo"] = [efectivo_tr + efectivo_mi, efectivo_tr + efectivo_mi, 0]
    st.subheader("Por plataforma")
    st.dataframe(por_plat.style.format(lambda x: eur(x)), width="stretch")

    seccion_resultado_mensual(val)

    st.subheader("Diversificación")
    base = val if incluir_plan else val[val.plataforma != "MyInvestor (plan)"]
    filas, sin_perfil = cartera.desglose(base, instrumentos, perfiles,
                                         (efectivo_tr + efectivo_mi) if incluir_efectivo else 0.0)
    clases = filas[filas.dimension == "clase"].groupby("categoria", as_index=False)["valor"].sum()
    clases = clases[clases.valor > 0.5].sort_values("valor", ascending=False)
    clases["peso"] = clases.valor / clases.valor.sum()

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Tipo de activo**")
        colores = {"Renta variable": VERDE, "Renta fija": TINTA, "Monetario y efectivo": GRIS,
                   "Oro": ORO, "Cripto": NARANJA, "Alternativos": MORADO, "Sin clasificar": "#6E7781"}
        fig = px.pie(clases, names="categoria", values="valor", hole=0.55, color="categoria",
                     color_discrete_map=colores)
        fig.update_traces(textinfo="percent", sort=False)
        fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), legend=dict(orientation="h"))
        st.plotly_chart(fig, width="stretch")
    with c2:
        st.markdown("**Región**")
        solo_rv = st.toggle("Solo renta variable", value=True,
                            help="El oro, la cripto y el efectivo no tienen país: con esto ves solo dónde están las empresas.")
        reg = filas[filas.dimension == "region"]
        if solo_rv:
            claves_rv = filas[(filas.dimension == "clase") & (filas.categoria == "Renta variable")].clave
            reg = reg[reg.clave.isin(claves_rv)]
        reg = reg.groupby("categoria", as_index=False)["valor"].sum()
        reg = reg[reg.valor > 0.5].sort_values("valor")
        reg["peso"] = reg.valor / reg.valor.sum()
        fig = px.bar(reg, x="peso", y="categoria", orientation="h", text=reg.peso.map(lambda x: f"{x:.0%}"),
                     color_discrete_sequence=[VERDE])
        fig.update_layout(height=300, margin=dict(l=0, r=10, t=10, b=0), xaxis=dict(tickformat=".0%", title=None),
                          yaxis=dict(title=None), plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, width="stretch")

    rv = clases.set_index("categoria")["peso"]
    defensivo = rv.get("Renta fija", 0) + rv.get("Monetario y efectivo", 0)
    st.caption(f"Renta variable {rv.get('Renta variable', 0):.0%}, renta fija y liquidez {defensivo:.0%}, "
               f"oro {rv.get('Oro', 0):.0%} y cripto {rv.get('Cripto', 0):.0%}. "
               "Los fondos indexados se reparten por país con los pesos aproximados de su índice "
               "(config/perfiles.csv).")
    if sin_perfil:
        st.caption("Sin clasificar todavía: " + ", ".join(sin_perfil))

    with st.expander("Qué activos forman cada parte"):
        dim = st.radio("Ver por", ["Tipo de activo", "Región"], horizontal=True)
        det = filas[filas.dimension == ("clase" if dim == "Tipo de activo" else "region")]
        det = det[det.valor > 0.5].groupby(["categoria", "nombre"], as_index=False)["valor"].sum()
        det = det.sort_values(["categoria", "valor"], ascending=[True, False])
        st.dataframe(det, hide_index=True, width="stretch",
                     column_config={"valor": st.column_config.NumberColumn(format="%.2f €")})

    st.subheader("Posiciones")
    consolidar = st.toggle("Agrupar el mismo activo en ambas plataformas", value=True)
    tabla = val.copy()
    if consolidar:
        tabla = tabla.groupby("clave", as_index=False).agg(
            nombre=("nombre_corto", "first"), tipo=("tipo", "first"),
            plataforma=("plataforma", lambda s: " + ".join(sorted(set(s)))),
            titulos=("titulos", "sum"), coste=("coste", "sum"), valor=("valor", "sum"),
            fuente=("fuente", "first"))
        tabla["resultado"] = tabla.valor - tabla.coste
        tabla["resultado_pct"] = tabla.resultado / tabla.coste
    else:
        tabla = tabla.rename(columns={"nombre_corto": "nombre"})
    tabla["peso"] = tabla.valor / tabla.valor.sum()
    st.dataframe(tabla.sort_values("valor", ascending=False)[
        ["nombre", "plataforma", "tipo", "titulos", "coste", "valor", "resultado", "resultado_pct", "peso", "fuente"]],
        hide_index=True, width="stretch", column_config={
            "titulos": st.column_config.NumberColumn(format="%.4f"),
            "coste": st.column_config.NumberColumn(format="%.2f €"),
            "valor": st.column_config.NumberColumn(format="%.2f €"),
            "resultado": st.column_config.NumberColumn(format="%.2f €"),
            "resultado_pct": st.column_config.NumberColumn("resultado %", format="percent"),
            "peso": st.column_config.NumberColumn(format="percent"),
        })

    st.subheader("Ventas realizadas")
    if ventas_tr.empty:
        st.caption("Todavía no hay ventas.")
    else:
        st.dataframe(ventas_tr[["fecha", "nombre", "titulos", "transmision", "adquisicion", "resultado", "regla_2_meses"]]
                     .sort_values("fecha", ascending=False), hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.2f €") for c in ["transmision", "adquisicion", "resultado"]})


def pestaña_aportaciones(patrimonio: float):
    m = cartera.aportaciones_mensuales(tr, mi)
    if m.empty:
        st.info("Sube tus movimientos para ver las aportaciones.")
        return
    medias = cartera.medias_aportacion(m)
    st.subheader("Aportación media al mes")
    st.caption("«Compras» suma todo lo invertido; «neto» resta las ventas. Una venta y su reinversión "
               "en otra plataforma meses después afean las medias cortas: mira las de 12 meses o más.")
    cols = st.columns(len(medias))
    for col, r in zip(cols, medias.itertuples()):
        col.metric(r.periodo, eur(r.neto_mes), f"compras {eur(r.compras_mes)}", delta_color="off")

    df = m.rename_axis("mes").reset_index()
    df["mes"] = df["mes"].dt.to_timestamp()
    fig = go.Figure()
    fig.add_bar(x=df.mes, y=df.neto, name="Neto", marker_color=np.where(df.neto >= 0, VERDE, ROJO))
    fig.add_scatter(x=df.mes, y=df.acumulado, name="Acumulado", yaxis="y2", line=dict(color=TINTA))
    fig.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h"),
                      yaxis2=dict(overlaying="y", side="right", showgrid=False), plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, width="stretch")

    st.subheader(f"Camino a {eur(objetivo)}")
    c1, c2, c3 = st.columns(3)
    base_aport = c1.selectbox("Aportación mensual", ["Media 12 meses", "Media 6 meses", "Media histórica", "Personalizada"])
    mapa = {"Media 12 meses": "12 meses", "Media 6 meses": "6 meses", "Media histórica": "Todo el histórico"}
    aport = float(medias.set_index("periodo").loc[mapa[base_aport], "neto_mes"]) if base_aport in mapa else 0.0
    if base_aport == "Personalizada":
        aport = c1.number_input("€/mes", value=1000, step=50)
    aport = c2.number_input("Aportación usada (€/mes)", value=round(max(aport, 0)), step=50)
    plazo = c3.number_input("¿En cuántos años quieres llegar?", value=5, min_value=1, max_value=40)

    escenarios = {"Prudente (4 %)": 0.04, "Base (7 %)": 0.07, "Optimista (9 %)": 0.09}
    fig = go.Figure()
    filas = []
    for (nombre, r), color in zip(escenarios.items(), [GRIS, VERDE, TINTA]):  # prudente, base, optimista
        p = cartera.proyeccion(patrimonio, aport, r, objetivo)
        alcanzado = p.valor.iloc[-1] >= objetivo
        filas.append(dict(escenario=nombre,
                          llegada=p.fecha.iloc[-1].strftime("%m/%Y") if alcanzado else "más de 50 años",
                          años=(len(p) - 1) / 12 if alcanzado else np.nan,
                          aportacion_para_plazo=cartera.aportacion_necesaria(patrimonio, r, objetivo, int(plazo * 12))))
        fig.add_scatter(x=p.fecha, y=p.valor, name=nombre, line=dict(color=color))
    fig.add_hline(y=objetivo, line=dict(color=ROJO, dash="dash"))
    fig.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h"), plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, width="stretch")
    st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch", column_config={
        "años": st.column_config.NumberColumn(format="%.1f"),
        "aportacion_para_plazo": st.column_config.NumberColumn(f"€/mes para llegar en {plazo} años", format="%.0f €"),
    })

    seccion_movimientos()


def seccion_movimientos():
    st.subheader("Movimientos de inversión")
    mov = cartera.movimientos_inversion(tr, mi)
    if mov.empty:
        st.caption("Todavía no hay movimientos.")
        return
    c1, c2, c3, c4 = st.columns([2, 2, 2, 3])
    desde_def = (pd.Timestamp.today() - pd.DateOffset(months=12)).date()
    rango = c1.date_input("Fechas", value=(max(desde_def, mov.fecha.min().date()), mov.fecha.max().date()),
                          min_value=mov.fecha.min().date(), max_value=pd.Timestamp.today().date(), format="DD/MM/YYYY")
    plataformas = c2.multiselect("Plataforma", sorted(mov.plataforma.unique()), placeholder="Todas")
    tipos = c3.multiselect("Tipo", sorted(mov.tipo.unique()), placeholder="Todos")
    texto = c4.text_input("Buscar activo", placeholder="p. ej. gold, msci, bitcoin")

    f = mov
    if isinstance(rango, (list, tuple)) and len(rango) == 2:
        f = f[(f.fecha.dt.date >= rango[0]) & (f.fecha.dt.date <= rango[1])]
    if plataformas:
        f = f[f.plataforma.isin(plataformas)]
    if tipos:
        f = f[f.tipo.isin(tipos)]
    if texto:
        f = f[f.activo.str.contains(texto, case=False, na=False)]

    aport = f[f.cuenta_como_aportacion]
    invertido = aport.loc[aport.importe > 0, "importe"].sum()
    retirado = -aport.loc[aport.importe < 0, "importe"].sum()
    m = st.columns(4)
    m[0].metric("Invertido", eur(invertido))
    m[1].metric("Vendido o reembolsado", eur(retirado))
    m[2].metric("Neto", eur(invertido - retirado))
    m[3].metric("Operaciones", f"{len(f):,}".replace(",", "."))

    vista = f[["fecha", "plataforma", "tipo", "activo", "titulos", "precio", "importe"]]
    st.dataframe(vista, hide_index=True, width="stretch", height=380, column_config={
        "fecha": st.column_config.DateColumn(format="DD/MM/YYYY"),
        "titulos": st.column_config.NumberColumn("títulos", format="%.4f"),
        "precio": st.column_config.NumberColumn(format="%.2f €"),
        "importe": st.column_config.NumberColumn(format="%.2f €"),
    })
    st.caption("Importe positivo = dinero invertido; negativo = ventas y reembolsos. En MyInvestor el extracto no trae "
               "títulos ni precio. Los Saveback y regalos se listan pero no cuentan como aportación.")

    with st.expander("Resumen por activo"):
        res = aport.groupby(["activo", "plataforma"], as_index=False).agg(
            operaciones=("importe", "size"), neto=("importe", "sum"), ultima=("fecha", "max"))
        st.dataframe(res.sort_values("neto", ascending=False), hide_index=True, width="stretch", column_config={
            "neto": st.column_config.NumberColumn("invertido neto", format="%.2f €"),
            "ultima": st.column_config.DateColumn("última operación", format="DD/MM/YYYY"),
        })
    st.download_button("Descargar CSV", f.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                       file_name="movimientos_inversion.csv", mime="text/csv")


def seccion_estimacion_renta(año: int, d: dict):
    st.subheader(f"¿A pagar o a devolver? Renta {año}")
    c1, c2, c3 = st.columns(3)
    tipo = c1.number_input("Tu tipo marginal en la base general (%)", min_value=0.0, max_value=55.0, value=30.0,
                           step=1.0, help="El tramo más alto de IRPF que pagas por tu sueldo. Lo ves en tu declaración "
                           "anterior o en un simulador. Sirve para calcular lo que te ahorra el plan de pensiones.")
    previas = c2.number_input("Pérdidas pendientes de años anteriores (€)", min_value=0.0, value=0.0, step=50.0,
                              help="Pérdidas de los últimos 4 años que aún no hayas compensado. Salen en tu última declaración.")
    otros = c3.toggle("Incluir Saveback, regalos y staking", value=True,
                      help="Trade Republic ya les aplica retención, así que lo normal es que aparezcan en el borrador.")
    e = cartera.estimar_renta(d, tipo / 100, otros, previas)

    filas = [("Ganancias y pérdidas por ventas", e["ganancias"]),
             ("Intereses, dividendos y otros rendimientos", e["rendimientos"]),
             ("Base del ahorro", e["base_ahorro"]),
             ("Impuesto sobre el ahorro (19 % a 30 %)", e["cuota_ahorro"]),
             ("Retenciones ya pagadas", -e["retenciones"]),
             (f"Ahorro por el plan de pensiones ({eur(e['reduccion_plan'])} × {tipo:.0f} %)", -e["ahorro_plan"])]
    if e["perdidas_compensadas"]:
        filas.insert(1, ("Pérdidas de años anteriores compensadas", -e["perdidas_compensadas"]))
    tabla = pd.DataFrame(filas, columns=["concepto", "importe"])
    st.dataframe(tabla.style.format({"importe": lambda x: eur(x, 2)}), hide_index=True, width="stretch")

    neto = e["efecto_neto"]
    if neto > 0:
        st.warning(f"Tus inversiones te sumarían unos **{eur(neto)} a pagar** en la declaración.")
    else:
        st.success(f"Tus inversiones te restarían unos **{eur(-neto)}**: empujan la declaración hacia **a devolver**.")

    base_sin = st.number_input("Opcional: resultado de tu declaración sin inversiones (€, negativo = a devolver)",
                               value=0.0, step=50.0, help="Por ejemplo, lo que te sale en un simulador solo con tu nómina.")
    if base_sin:
        total = base_sin + neto
        st.metric("Resultado aproximado de la declaración", ("A pagar " if total > 0 else "A devolver ") + eur(abs(total)))

    margen = d["plan_margen"]
    if año == pd.Timestamp.today().year and margen > 0:
        st.info(f"Aún puedes aportar {eur(margen)} al plan de pensiones este año: te ahorraría unos "
                f"{eur(margen * tipo / 100)} más.")
    st.caption("Estimación orientativa. Que salga a pagar o a devolver depende sobre todo de las retenciones de tu "
               "nómina, que aquí no se conocen: esto calcula cuánto mueven ese resultado tus inversiones. "
               "No incluye la deducción por doble imposición internacional de los dividendos extranjeros, ni las "
               "ganancias de MyInvestor que no hayas copiado en config/fiscal_manual.csv. No sustituye al borrador "
               "ni a un asesor.")


def pestaña_fiscal():
    st.caption("Estimación para preparar la declaración. Compárala siempre con el borrador y los informes fiscales "
               "de cada plataforma; no sustituye a un asesor.")
    res = cartera.resumen_fiscal(tr, mi, ventas_tr, fiscal_manual)
    if not res:
        return
    año = st.selectbox("Ejercicio", sorted(res, reverse=True))
    d = res[año]
    seccion_estimacion_renta(año, d)
    st.divider()
    st.markdown("**Detalle**")
    c = st.columns(4)
    c[0].metric("Ganancias/pérdidas (TR)", eur(d["gp_tr"], 2))
    rcm = d["rcm"]
    c[1].metric("Intereses y dividendos", eur(rcm.bruto.sum() if not rcm.empty else 0, 2))
    c[2].metric("Retenciones", eur(rcm.retencion.sum() if not rcm.empty else 0, 2))
    c[3].metric("Plan de pensiones", eur(d["plan_pensiones"]), f"margen {eur(d['plan_margen'])}", delta_color="off")

    st.markdown("**Ganancias y pérdidas patrimoniales**")
    if d["ventas_tr"].empty:
        st.caption("Sin ventas en Trade Republic este año.")
    else:
        v = d["ventas_tr"]
        st.dataframe(v[["fecha", "nombre", "clase", "transmision", "adquisicion", "resultado", "regla_2_meses"]],
                     hide_index=True, width="stretch",
                     column_config={x: st.column_config.NumberColumn(format="%.2f €") for x in ["transmision", "adquisicion", "resultado"]})
        if v.regla_2_meses.any():
            st.warning("Hay pérdidas con recompras del mismo valor en dos meses: esa pérdida no se puede "
                       "compensar hasta que vendas los títulos recomprados.")
    if not d["reembolsos_mi"].empty:
        st.info("Reembolsos de fondos en MyInvestor: el extracto no trae el valor de adquisición. "
                "Copia la ganancia del informe fiscal de MyInvestor en config/fiscal_manual.csv.")
        st.dataframe(d["reembolsos_mi"][["fecha", "concepto", "importe"]], hide_index=True, width="stretch")
    if not d["manual"].empty:
        st.dataframe(d["manual"], hide_index=True, width="stretch")

    st.markdown("**Rendimientos del capital mobiliario**")
    st.dataframe(rcm, hide_index=True, width="stretch",
                 column_config={x: st.column_config.NumberColumn(format="%.2f €") for x in ["bruto", "retencion"]})
    if not d["otros"].empty:
        st.markdown("**Partidas a revisar**")
        st.caption("Saveback, acciones regalo y staking tienen un tratamiento discutido: revisa cómo aparecen en el borrador.")
        st.dataframe(d["otros"], hide_index=True, width="stretch",
                     column_config={x: st.column_config.NumberColumn(format="%.2f €") for x in ["importe", "retencion"]})


def pestaña_datos(val: pd.DataFrame):
    for a in avisos_mi + avisos_fifo:
        st.warning(a)
    if not avisos_mi:
        st.success("La cadena de saldos de MyInvestor cuadra de principio a fin.")
    c = st.columns(3)
    c[0].metric("Movimientos Trade Republic", len(tr), f"{tr.date.min():%m/%Y} – {tr.date.max():%m/%Y}" if not tr.empty else "", delta_color="off")
    c[1].metric("Movimientos MyInvestor", len(mi), f"{mi.fecha.min():%m/%Y} – {mi.fecha.max():%m/%Y}" if not mi.empty else "", delta_color="off")
    c[2].metric("Foto de posiciones MyInvestor", f"{pos_mi.fecha.max():%d/%m/%Y}" if not pos_mi.empty else "—")
    st.markdown("**Cómo se valora cada posición**")
    st.dataframe(val[["nombre_corto", "plataforma", "fuente", "precio"]].drop_duplicates(), hide_index=True, width="stretch")
    sin_cat = sorted(set(val.clave) - set(instrumentos.clave))
    sin_cat = [s for s in sin_cat if not str(s).startswith("PEND_")]
    if sin_cat:
        st.warning("Activos sin entrada en config/instrumentos.csv (se valoran con el último precio conocido): " + ", ".join(sin_cat))


segundos = {"Manual": None, "Cada 5 min": 300, "Cada 15 min": 900}[refresco]
st.fragment(run_every=segundos)(pintar)()
