"""Cálculos de cartera: FIFO, posiciones consolidadas, aportaciones, fiscalidad y objetivo."""
from __future__ import annotations

from collections import defaultdict, deque

import numpy as np
import pandas as pd

LIMITE_PLAN_PENSIONES = 1500.0  # límite general de aportación deducible (revisar cada año)

# --------------------------------------------------------------------------- #
# Trade Republic: FIFO
# --------------------------------------------------------------------------- #
def _coste_compra(r) -> float:
    if pd.notna(r.amount):
        return -r.amount - (r.fee if pd.notna(r.fee) else 0.0)
    return r.shares * r.price  # compras financiadas con Saveback / regalos


def fifo_tr(tr: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Devuelve (ventas_realizadas, posiciones_abiertas, avisos)."""
    lotes: dict[str, deque] = defaultdict(deque)
    ventas, avisos, fusion = [], [], None
    ultimo_precio, nombres, clases = {}, {}, {}

    for r in tr.itertuples():
        s = r.symbol
        if isinstance(s, str):
            nombres[s] = r.name
            clases[s] = r.asset_class
            if pd.notna(r.price):
                ultimo_precio[s] = (r.price, r.date)

        if r.type == "BUY" and pd.notna(r.shares):
            lotes[s].append([r.shares, _coste_compra(r) / r.shares, r.date])
        elif r.type == "FREE_RECEIPT" and pd.notna(r.shares):
            lotes[s].append([r.shares, r.price, r.date])  # staking: coste = valor recibido
        elif r.type == "SELL":
            q = -r.shares
            ingreso = r.amount + (r.fee if pd.notna(r.fee) else 0.0)
            coste, fechas = 0.0, []
            while q > 1e-9 and lotes[s]:
                lote = lotes[s][0]
                toma = min(q, lote[0])
                coste += toma * lote[1]
                fechas.append(lote[2])
                lote[0] -= toma
                q -= toma
                if lote[0] < 1e-9:
                    lotes[s].popleft()
            if q > 1e-6:
                avisos.append(f"Venta de {r.name} el {r.date:%d/%m/%Y} sin compras suficientes en el histórico.")
            ventas.append(dict(fecha=r.date, año=r.date.year, plataforma="Trade Republic", clave=s,
                               nombre=r.name, clase=r.asset_class, titulos=-r.shares,
                               transmision=ingreso, adquisicion=coste, resultado=ingreso - coste,
                               compra_mas_antigua=min(fechas) if fechas else pd.NaT))
        elif r.type == "SPLIT" and pd.notna(r.shares):
            total = sum(l[0] for l in lotes[s])
            if total > 0:
                f = (total + r.shares) / total
                for l in lotes[s]:
                    l[0] *= f
                    l[1] /= f
                if s in ultimo_precio:
                    precio, fecha = ultimo_precio[s]
                    ultimo_precio[s] = (precio / f, fecha)
        elif r.type == "MERGER":
            if r.shares < 0:
                fusion = list(lotes[s])
                lotes[s] = deque()
            elif fusion:
                total = sum(l[0] for l in fusion)
                f = r.shares / total
                for l in fusion:
                    lotes[s].append([l[0] * f, l[1] / f, l[2]])
                fusion = None
        # MIGRATION llega en pares +/- idénticos: no cambia nada

    ventas = pd.DataFrame(ventas)
    if not ventas.empty:
        ventas["regla_2_meses"] = ventas.apply(lambda v: _regla_dos_meses(v, tr), axis=1)

    filas = []
    for s, ls in lotes.items():
        tit = sum(l[0] for l in ls)
        if tit > 1e-6:
            precio, fecha = ultimo_precio.get(s, (np.nan, pd.NaT))
            filas.append(dict(plataforma="Trade Republic", clave=s, nombre=nombres.get(s, s),
                              clase=clases.get(s), titulos=tit, coste=sum(l[0] * l[1] for l in ls),
                              precio_ref=precio, fecha_ref=fecha))
    return ventas, pd.DataFrame(filas), avisos


def _regla_dos_meses(venta, tr) -> bool:
    """Marca pérdidas con recompras del mismo valor dos meses antes o después."""
    if venta.resultado >= 0:
        return False
    ventana = tr[(tr.symbol == venta.clave) & (tr.type == "BUY")
                 & (tr.date >= venta.fecha - pd.DateOffset(months=2))
                 & (tr.date <= venta.fecha + pd.DateOffset(months=2))]
    return not ventana.empty


# --------------------------------------------------------------------------- #
# Posiciones consolidadas
# --------------------------------------------------------------------------- #
def posiciones(tr_abiertas: pd.DataFrame, pos_mi: pd.DataFrame, mi: pd.DataFrame,
               instrumentos: pd.DataFrame) -> pd.DataFrame:
    partes = []
    if not tr_abiertas.empty:
        partes.append(tr_abiertas)
    if not pos_mi.empty:
        p = pos_mi.rename(columns={"precio": "precio_ref", "fecha": "fecha_ref"})
        p["plataforma"] = np.where(p["cuenta"] == "pensiones", "MyInvestor (plan)", "MyInvestor")
        partes.append(p[["plataforma", "clave", "nombre", "titulos", "coste", "precio_ref", "fecha_ref"]])
        # Compras posteriores a la foto: se valoran a coste hasta subir una foto nueva
        fecha_foto = pos_mi["fecha"].max()
        nuevas = mi[(mi.fecha > fecha_foto) & mi.tipo.isin(
            ["SUSCRIPCION IIC", "COMPRA RV CONTADO", "APORTACION PLANES"])] if not mi.empty else mi
        for r in nuevas.itertuples():
            partes.append(pd.DataFrame([dict(plataforma="MyInvestor", clave=f"PEND_{r.concepto}",
                                             nombre=f"{r.concepto} (pendiente de foto)", titulos=np.nan,
                                             coste=-r.importe, precio_ref=np.nan, fecha_ref=r.fecha)]))
    if not partes:
        return pd.DataFrame()
    df = pd.concat(partes, ignore_index=True)
    cat = instrumentos.set_index("clave")
    df["tipo"] = df["clave"].map(cat["tipo"]).fillna(df.get("clase", pd.Series(dtype=str)).str.lower())
    df["nombre_corto"] = df["clave"].map(cat["nombre"]).fillna(df["nombre"])
    return df


def valorar(pos: pd.DataFrame, precios: dict, instrumentos: pd.DataFrame) -> pd.DataFrame:
    """precios: {clave: (precio_eur, fuente)} calculado en mercado.py."""
    df = pos.copy()
    df["precio"] = df.apply(lambda r: precios.get(r.clave, (r.precio_ref, "último dato"))[0], axis=1)
    df["fuente"] = df.apply(lambda r: precios.get(r.clave, (None, "último dato"))[1], axis=1)
    df["valor"] = np.where(df["titulos"].notna(), df["titulos"] * df["precio"], df["coste"])
    df.loc[df["titulos"].isna(), "fuente"] = "a coste"
    df["resultado"] = df["valor"] - df["coste"]
    df["resultado_pct"] = df["resultado"] / df["coste"]
    return df


# --------------------------------------------------------------------------- #
# Aportaciones
# --------------------------------------------------------------------------- #
MI_COMPRAS = ["SUSCRIPCION IIC", "COMPRA RV CONTADO", "APORTACION PLANES"]
MI_VENTAS = ["REEMBOLSO IIC", "VENTA RV CONTADO"]


def aportaciones_mensuales(tr: pd.DataFrame, mi: pd.DataFrame) -> pd.DataFrame:
    filas = []
    if not tr.empty:
        b = tr[(tr.type == "BUY") & tr.amount.notna()]
        s = tr[tr.type == "SELL"]
        filas += [dict(fecha=r.date, plataforma="Trade Republic", compras=-r.amount, ventas=0.0) for r in b.itertuples()]
        filas += [dict(fecha=r.date, plataforma="Trade Republic", compras=0.0, ventas=r.amount) for r in s.itertuples()]
    if not mi.empty:
        c = mi[mi.tipo.isin(MI_COMPRAS)]
        v = mi[mi.tipo.isin(MI_VENTAS)]
        filas += [dict(fecha=r.fecha, plataforma="MyInvestor", compras=-r.importe, ventas=0.0) for r in c.itertuples()]
        filas += [dict(fecha=r.fecha, plataforma="MyInvestor", compras=0.0, ventas=r.importe) for r in v.itertuples()]
    df = pd.DataFrame(filas)
    if df.empty:
        return df
    df["mes"] = df["fecha"].dt.to_period("M")
    m = df.groupby("mes")[["compras", "ventas"]].sum()
    m = m.reindex(pd.period_range(m.index.min(), pd.Timestamp.today().to_period("M"), freq="M"), fill_value=0)
    m["neto"] = m["compras"] - m["ventas"]
    m["acumulado"] = m["neto"].cumsum()
    return m


def medias_aportacion(m: pd.DataFrame) -> pd.DataFrame:
    """Medias de los últimos N meses completos (el mes en curso se excluye)."""
    completos = m.iloc[:-1] if len(m) > 1 else m
    filas = []
    for etiqueta, n in [("Último mes", 1), ("3 meses", 3), ("6 meses", 6), ("12 meses", 12),
                        ("24 meses", 24), ("Todo el histórico", len(completos))]:
        tramo = completos.tail(n)
        filas.append(dict(periodo=etiqueta, meses=len(tramo),
                          compras_mes=tramo["compras"].mean(), neto_mes=tramo["neto"].mean(),
                          total_neto=tramo["neto"].sum()))
    return pd.DataFrame(filas)


# --------------------------------------------------------------------------- #
# Objetivo
# --------------------------------------------------------------------------- #
def proyeccion(valor_actual: float, aportacion_mes: float, rentabilidad_anual: float,
               objetivo: float, max_meses: int = 600) -> pd.DataFrame:
    r = (1 + rentabilidad_anual) ** (1 / 12) - 1
    v, filas = valor_actual, []
    hoy = pd.Timestamp.today().normalize()
    for mes in range(max_meses + 1):
        filas.append(dict(fecha=hoy + pd.DateOffset(months=mes), valor=v))
        if v >= objetivo:
            break
        v = v * (1 + r) + aportacion_mes
    return pd.DataFrame(filas)


def aportacion_necesaria(valor_actual: float, rentabilidad_anual: float, objetivo: float, meses: int) -> float:
    r = (1 + rentabilidad_anual) ** (1 / 12) - 1
    crecimiento = (1 + r) ** meses
    if r == 0:
        return max(0.0, (objetivo - valor_actual) / meses)
    return max(0.0, (objetivo - valor_actual * crecimiento) * r / (crecimiento - 1))


# --------------------------------------------------------------------------- #
# Fiscalidad (estimación orientativa para revisar contra el borrador)
# --------------------------------------------------------------------------- #
def resumen_fiscal(tr: pd.DataFrame, mi: pd.DataFrame, ventas_tr: pd.DataFrame,
                   fiscal_manual: pd.DataFrame) -> dict[int, dict]:
    años = set()
    if not tr.empty:
        años |= set(tr.date.dt.year)
    if not mi.empty:
        años |= set(mi.fecha.dt.year)
    res = {}
    for a in sorted(años):
        d = {}
        # Ganancias y pérdidas patrimoniales
        v = ventas_tr[ventas_tr["año"] == a] if not ventas_tr.empty else ventas_tr
        d["ventas_tr"] = v
        mi_reemb = mi[(mi.fecha.dt.year == a) & mi.tipo.isin(MI_VENTAS)] if not mi.empty else mi
        d["reembolsos_mi"] = mi_reemb
        man = fiscal_manual[fiscal_manual["año"] == a] if not fiscal_manual.empty else fiscal_manual
        d["manual"] = man
        d["gp_tr"] = float(v["resultado"].sum()) if not v.empty else 0.0
        d["gp_manual"] = float(man.loc[man.casilla == "ganancia_patrimonial", "importe"].sum()) if not man.empty else 0.0

        # Rendimientos del capital mobiliario
        filas = []
        if not tr.empty:
            t = tr[tr.date.dt.year == a]
            for tipo, etiqueta in [("INTEREST_PAYMENT", "Intereses Trade Republic"),
                                   ("DIVIDEND", "Dividendos Trade Republic")]:
                x = t[t.type == tipo]
                filas.append(dict(concepto=etiqueta, bruto=x.amount.sum(), retencion=-x.tax.fillna(0).sum()))
        if not mi.empty:
            m = mi[mi.fecha.dt.year == a]
            filas.append(dict(concepto="Intereses MyInvestor",
                              bruto=m[m.tipo.isin(["LIQUIDAC. INTERESES", "INTERESES S/F"])].importe.sum(),
                              retencion=-m[m.tipo == "RETE.LIQUIDA"].importe.sum()))
            filas.append(dict(concepto="Promociones MyInvestor",
                              bruto=m[m.tipo == "ABONO PROMOCION"].importe.sum(),
                              retencion=-m[m.tipo == "CARGO RETENCION A CUENTA"].importe.sum()))
        rcm = pd.DataFrame(filas)
        d["rcm"] = rcm[(rcm.bruto.abs() > 0.004) | (rcm.retencion.abs() > 0.004)] if not rcm.empty else rcm

        # Partidas a revisar con el borrador
        otros = []
        if not tr.empty:
            t = tr[tr.date.dt.year == a]
            sb = t[t.type == "BENEFITS_SAVEBACK"]
            otros.append(dict(concepto="Saveback Trade Republic", importe=sb.amount.sum(), retencion=-sb.tax.fillna(0).sum()))
            sp = t[t.type == "STOCKPERK"]
            otros.append(dict(concepto="Acciones regalo (Stockperk)", importe=sp.amount.sum(), retencion=0.0))
            st = t[t.type == "FREE_RECEIPT"]
            otros.append(dict(concepto="Staking de cripto (valor recibido)", importe=(st.shares * st.price).sum(), retencion=0.0))
        ot = pd.DataFrame(otros)
        d["otros"] = ot[ot.importe.abs() > 0.004] if not ot.empty else ot

        # Plan de pensiones
        pp = -mi[(mi.fecha.dt.year == a) & (mi.tipo == "APORTACION PLANES")].importe.sum() if not mi.empty else 0.0
        d["plan_pensiones"] = float(pp)
        d["plan_margen"] = max(0.0, LIMITE_PLAN_PENSIONES - pp)
        res[a] = d
    return res


# --------------------------------------------------------------------------- #
# Desglose por tipo de activo y región
# --------------------------------------------------------------------------- #
def leer_perfiles(ruta) -> tuple[pd.DataFrame, list[str]]:
    perfiles = pd.read_csv(ruta, comment="#")
    avisos = []
    sumas = perfiles.groupby(["perfil", "dimension"])["peso"].sum()
    for (perfil, dim), s in sumas.items():
        if abs(s - 1) > 0.005:
            avisos.append(f"El perfil {perfil} suma {s:.3f} en «{dim}» en vez de 1.")
    return perfiles, avisos


def desglose(val: pd.DataFrame, instrumentos: pd.DataFrame, perfiles: pd.DataFrame,
             efectivo: float = 0.0) -> tuple[pd.DataFrame, list[str]]:
    """Reparte el valor de cada posición según su perfil. Devuelve filas
    (clave, nombre, dimension, categoria, valor) y la lista de activos sin perfil."""
    mapa = instrumentos.set_index("clave")["perfil"]
    base = val.groupby("clave", as_index=False).agg(nombre=("nombre_corto", "first"), valor=("valor", "sum"))
    base["perfil"] = base["clave"].map(mapa)
    # Compras pendientes de foto: sin perfil conocido
    sin_perfil = base[base.perfil.isna() | ~base.perfil.isin(perfiles.perfil)]
    base.loc[sin_perfil.index, "perfil"] = "_SIN_PERFIL"

    extra = pd.DataFrame([
        dict(perfil="_SIN_PERFIL", dimension="clase", categoria="Sin clasificar", peso=1),
        dict(perfil="_SIN_PERFIL", dimension="region", categoria="Sin desglose", peso=1),
        dict(perfil="_EFECTIVO", dimension="clase", categoria="Monetario y efectivo", peso=1),
        dict(perfil="_EFECTIVO", dimension="region", categoria="Sin región", peso=1),
    ])
    perfiles = pd.concat([perfiles, extra], ignore_index=True)
    if efectivo:
        base = pd.concat([base, pd.DataFrame([dict(clave="EFECTIVO", nombre="Efectivo en cuentas",
                                                   valor=efectivo, perfil="_EFECTIVO")])], ignore_index=True)
    filas = base.merge(perfiles, on="perfil", how="left")
    filas["valor"] = filas["valor"] * filas["peso"]
    return filas[["clave", "nombre", "dimension", "categoria", "valor"]], sin_perfil["nombre"].tolist()


def flujos_diarios(tr: pd.DataFrame, mi: pd.DataFrame, incluir_plan: bool = True) -> pd.Series:
    """Dinero neto metido en inversiones cada día (compras − ventas)."""
    partes = []
    if not tr.empty:
        b = tr[(tr.type == "BUY") & tr.amount.notna()]
        s = tr[tr.type == "SELL"]
        partes += [pd.Series(-b.amount.values, index=b.date), pd.Series(-s.amount.values, index=s.date)]
    if not mi.empty:
        tipos = MI_COMPRAS if incluir_plan else [t for t in MI_COMPRAS if t != "APORTACION PLANES"]
        m = mi[mi.tipo.isin(tipos + MI_VENTAS)]
        partes.append(pd.Series(-m.importe.values, index=m.fecha))
    if not partes:
        return pd.Series(dtype=float)
    return pd.concat(partes).groupby(level=0).sum().sort_index()


# --------------------------------------------------------------------------- #
# Listado de movimientos de inversión (aportaciones operación a operación)
# --------------------------------------------------------------------------- #
def _nombre_mi(concepto: str) -> str:
    return concepto.split(" @")[0].strip().title() if isinstance(concepto, str) else ""


def movimientos_inversion(tr: pd.DataFrame, mi: pd.DataFrame) -> pd.DataFrame:
    """Una fila por compra, venta, suscripción o aportación. importe > 0 = dinero invertido."""
    filas = []
    if not tr.empty:
        for r in tr[tr.type.isin(["BUY", "SELL"])].itertuples():
            desc = r.description if isinstance(r.description, str) else ""
            if r.type == "SELL":
                tipo, importe = "Venta", -r.amount
            elif pd.isna(r.amount):
                tipo, importe = "Saveback / regalo", r.shares * r.price
            elif desc.startswith("Savings plan"):
                tipo, importe = "Plan de inversión", -r.amount
            else:
                tipo, importe = "Compra", -r.amount
            filas.append(dict(fecha=r.date, plataforma="Trade Republic", tipo=tipo, activo=r.name,
                              titulos=abs(r.shares), precio=r.price, importe=importe,
                              cuenta_como_aportacion=tipo != "Saveback / regalo"))
    if not mi.empty:
        tipos = {"SUSCRIPCION IIC": "Suscripción fondo", "COMPRA RV CONTADO": "Compra",
                 "APORTACION PLANES": "Aportación plan de pensiones", "REEMBOLSO IIC": "Reembolso fondo",
                 "VENTA RV CONTADO": "Venta"}
        for r in mi[mi.tipo.isin(tipos)].itertuples():
            filas.append(dict(fecha=r.fecha, plataforma="MyInvestor", tipo=tipos[r.tipo], activo=_nombre_mi(r.concepto),
                              titulos=np.nan, precio=np.nan, importe=-r.importe, cuenta_como_aportacion=True))
    df = pd.DataFrame(filas)
    return df.sort_values("fecha", ascending=False).reset_index(drop=True) if not df.empty else df


# --------------------------------------------------------------------------- #
# Estimación del efecto de las inversiones en la declaración de la renta
# --------------------------------------------------------------------------- #
ESCALA_AHORRO = [(6_000, 0.19), (50_000, 0.21), (200_000, 0.23), (300_000, 0.27), (float("inf"), 0.30)]


def cuota_ahorro(base: float) -> float:
    cuota, desde = 0.0, 0.0
    for hasta, tipo in ESCALA_AHORRO:
        if base <= desde:
            break
        cuota += (min(base, hasta) - desde) * tipo
        desde = hasta
    return cuota


def estimar_renta(d: dict, tipo_marginal: float, incluir_otros: bool = True,
                  perdidas_previas: float = 0.0) -> dict:
    """d: el diccionario de un año de resumen_fiscal()."""
    gp = d["gp_tr"] + d["gp_manual"]
    rcm = d["rcm"]["bruto"].sum() if not d["rcm"].empty else 0.0
    ret = d["rcm"]["retencion"].sum() if not d["rcm"].empty else 0.0
    if incluir_otros and not d["otros"].empty:
        rcm += d["otros"]["importe"].sum()
        ret += d["otros"]["retencion"].sum()

    # Pérdidas de años anteriores (hasta 4 años) contra ganancias de este año
    usadas = min(max(gp, 0.0), perdidas_previas)
    gp_neto = gp - usadas
    # Compensación cruzada entre ganancias y rendimientos: hasta el 25 %
    if gp_neto < 0 and rcm > 0:
        comp = min(-gp_neto, 0.25 * rcm)
        gp_neto, rcm = gp_neto + comp, rcm - comp
    elif rcm < 0 and gp_neto > 0:
        comp = min(-rcm, 0.25 * gp_neto)
        rcm, gp_neto = rcm + comp, gp_neto - comp
    base = max(gp_neto, 0.0) + max(rcm, 0.0)
    cuota = cuota_ahorro(base)

    reduccion_plan = min(d["plan_pensiones"], LIMITE_PLAN_PENSIONES)
    ahorro_plan = reduccion_plan * tipo_marginal
    return dict(ganancias=gp, perdidas_compensadas=usadas, rendimientos=rcm, base_ahorro=base, cuota_ahorro=cuota,
                retenciones=ret, reduccion_plan=reduccion_plan, ahorro_plan=ahorro_plan,
                efecto_neto=cuota - ret - ahorro_plan,
                perdida_pendiente=min(gp_neto, 0.0) * -1 + (perdidas_previas - usadas))
