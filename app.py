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

VERDE, ROJO, TINTA, GRIS = "#1F6F5C", "#B5483B", "#1D2B36", "#8A97A0"


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
    return tr, mi, avisos_mi, pos_mi, instrumentos, fiscal_manual


@st.cache_data(ttl=3600, show_spinner="Descargando históricos de 5 años…")
def historico(tickers: tuple[str, ...]):
    return mercado.descargar_historico(list(tickers), "5y")


@st.cache_data(ttl=300, show_spinner="Actualizando precios…")
def ultimos(tickers: tuple[str, ...]):
    return mercado.descargar_ultimos(list(tickers))


def marca_datos() -> float:
    ficheros = list((RAIZ / "data").rglob("*")) + [RAIZ / "config" / "instrumentos.csv"]
    return max((f.stat().st_mtime for f in ficheros if f.is_file()), default=0)


# --------------------------------------------------------------------------- #
# Barra lateral: subida de ficheros y ajustes
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("Vitillonario")
    objetivo = st.number_input("Objetivo (€)", value=100_000, step=5_000)
    incluir_plan = st.toggle("Contar el plan de pensiones", value=True)
    incluir_efectivo = st.toggle("Contar el efectivo", value=True)
    refresco = st.selectbox("Refrescar precios", ["Manual", "Cada 5 min", "Cada 15 min"], index=1)
    if st.button("Actualizar ahora", width="stretch"):
        ultimos.clear()

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

tr, mi, avisos_mi, pos_mi, instrumentos, fiscal_manual = cargar(marca_datos())
ventas_tr, abiertas_tr, avisos_fifo = cartera.fifo_tr(tr) if not tr.empty else (pd.DataFrame(), pd.DataFrame(), [])
pos = cartera.posiciones(abiertas_tr, pos_mi, mi, instrumentos)

tickers_mercado = set(instrumentos.ticker.dropna()) | set(instrumentos.proxy.dropna()) | set(mercado.FX.values())
hist = historico(tuple(sorted(tickers_mercado)))


def pintar():
    ult = ultimos(tuple(sorted(tickers_mercado)))
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

    t_mercado, t_cartera, t_aport, t_fiscal, t_datos = st.tabs(
        ["Mercado y señales", "Cartera", "Aportaciones y objetivo", "Fiscal", "Datos"])

    with t_mercado:
        pestaña_mercado(val, ult)
    with t_cartera:
        pestaña_cartera(val, efectivo_tr, efectivo_mi)
    with t_aport:
        pestaña_aportaciones(patrimonio)
    with t_fiscal:
        pestaña_fiscal()
    with t_datos:
        pestaña_datos(val)


# --------------------------------------------------------------------------- #
# Pestañas
# --------------------------------------------------------------------------- #
def pestaña_mercado(val: pd.DataFrame, ult: pd.Series):
    if hist.empty:
        st.info("Sin históricos de mercado. En cuanto haya conexión verás mínimos de 3 meses a 5 años y las señales.")
        return
    cat = instrumentos.set_index("clave")
    agrupado = val.groupby("clave", as_index=False).agg(nombre=("nombre_corto", "first"), valor=("valor", "sum"))
    filas = []
    for r in agrupado.itertuples():
        if r.clave not in cat.index:
            continue
        inst = cat.loc[r.clave]
        ref = inst.ticker if isinstance(inst.ticker, str) else inst.proxy if isinstance(inst.proxy, str) else None
        if not ref or ref not in hist.columns:
            continue
        precio = float(ult[ref]) if ref in ult.index else None
        s = mercado.señales(hist[ref], precio)
        if not s:
            continue
        filas.append(dict(clave=r.clave, activo=r.nombre, referencia=ref + ("" if ref == inst.ticker else " (equivalente)"),
                          peso=r.valor, **s))
    if not filas:
        st.info("Ninguna posición tiene ticker o equivalente con histórico disponible.")
        return
    df = pd.DataFrame(filas).sort_values(["puntos", "peso"], ascending=[False, False])
    df = df.drop_duplicates("referencia")

    zona = df[df["señal"] == "Zona de compra"]
    if not zona.empty:
        st.success("En zona de compra según los indicadores: " + ", ".join(zona.activo))
    st.caption("La puntuación suma caída desde máximos, cercanía a mínimos, media de 200 sesiones, RSI y "
               "desviación respecto a la media anual. Es una ayuda para decidir, no una recomendación.")

    vista = df[["activo", "referencia", "señal", "puntos", "caída desde máx 1 a", "vs min 3 m", "vs min 6 m",
                "vs min 1 a", "vs min 3 a", "vs min 5 a", "vs media 200", "RSI 14", "motivos"]]
    st.dataframe(vista, hide_index=True, width="stretch", column_config={
        **{c: st.column_config.NumberColumn(c, format="percent") for c in
           ["caída desde máx 1 a", "vs min 3 m", "vs min 6 m", "vs min 1 a", "vs min 3 a", "vs min 5 a", "vs media 200"]},
        "RSI 14": st.column_config.NumberColumn(format="%.0f"),
        "puntos": st.column_config.ProgressColumn(min_value=0, max_value=9, format="%d"),
    })

    elegido = st.selectbox("Ver gráfico", df["activo"].tolist())
    fila = df[df.activo == elegido].iloc[0]
    ref = fila.referencia.replace(" (equivalente)", "")
    serie = hist[ref].dropna()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=serie.index, y=serie, name=ref, line=dict(color=TINTA, width=1.6)))
    fig.add_trace(go.Scatter(x=serie.index, y=serie.rolling(200).mean(), name="Media 200",
                             line=dict(color=GRIS, width=1, dash="dot")))
    for etiqueta, n, color in [("Mín 6 m", 126, "#C9A227"), ("Mín 1 a", 252, ROJO), ("Mín 5 a", 1260, "#6B2D26")]:
        if len(serie) >= n * 0.9:
            fig.add_hline(y=serie.tail(n).min(), line=dict(color=color, width=1, dash="dash"),
                          annotation_text=etiqueta, annotation_position="bottom right")
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=20, b=10), legend=dict(orientation="h"),
                      plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, width="stretch")
    if fila.historia_años < 4.9:
        st.caption(f"Solo hay {fila.historia_años:.1f} años de histórico para {ref}.".replace(".", ","))


def pestaña_cartera(val: pd.DataFrame, efectivo_tr: float, efectivo_mi: float):
    por_plat = val.groupby("plataforma")[["coste", "valor", "resultado"]].sum()
    por_plat.loc["Efectivo"] = [efectivo_tr + efectivo_mi, efectivo_tr + efectivo_mi, 0]
    c1, c2 = st.columns([3, 2])
    with c1:
        st.subheader("Por plataforma")
        st.dataframe(por_plat.style.format(lambda x: eur(x)), width="stretch")
    with c2:
        tipos = val.groupby("tipo")["valor"].sum().reset_index()
        fig = px.pie(tipos, names="tipo", values="valor", hole=0.55,
                     color_discrete_sequence=[VERDE, TINTA, "#C9A227", ROJO, GRIS, "#5B8C85", "#3F5866", "#D7B45A"])
        fig.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0), showlegend=True)
        st.plotly_chart(fig, width="stretch")

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

    df = m.reset_index().rename(columns={"index": "mes"})
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
    for (nombre, r), color in zip(escenarios.items(), [GRIS, VERDE, TINTA]):
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


def pestaña_fiscal():
    st.caption("Estimación para preparar la declaración. Compárala siempre con el borrador y los informes fiscales "
               "de cada plataforma; no sustituye a un asesor.")
    res = cartera.resumen_fiscal(tr, mi, ventas_tr, fiscal_manual)
    if not res:
        return
    año = st.selectbox("Ejercicio", sorted(res, reverse=True))
    d = res[año]
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
