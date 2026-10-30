"""Alertas de Vitillonario por Telegram.

Lo ejecuta GitHub Actions (ver .github/workflows/alertas.yml). Lee los mismos datos y usa el mismo
código que el dashboard, y solo envía mensaje si hay algo que contar:

- Un activo de tu cartera ENTRA hoy en «Zona de compra» o en «Recoger beneficios» (no repite el aviso
  los días siguientes: compara la señal de hoy con la de la sesión anterior).
- La cartera cae más de UMBRAL_CAIDA_SEMANA % en la última semana.
- Resumen semanal el día RESUMEN_DIA (0 = lunes … 4 = viernes).
- En diciembre, el margen que queda en el plan de pensiones.

Variables de entorno:
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID   obligatorias para enviar
  DRY_RUN=1                          muestra el mensaje por pantalla y no envía nada
  UMBRAL_CAIDA_SEMANA=5              en %
  RESUMEN_DIA=4                      día de la semana del resumen; -1 para desactivarlo
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
from core import cartera, mercado, parsers  # noqa: E402

DRY_RUN = os.environ.get("DRY_RUN", "0").lower() in ("1", "true", "si", "sí", "yes")
UMBRAL_CAIDA = float(os.environ.get("UMBRAL_CAIDA_SEMANA", "5")) / 100
RESUMEN_DIA = int(os.environ.get("RESUMEN_DIA", "4"))
SIN_SEÑALES = {"monetario", "renta_fija", "bono", "privado"}


def h(texto: str) -> str:
    """Escapa el texto para el formato HTML de Telegram (nombres con & o < no rompen el mensaje)."""
    return str(texto).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def eur(x: float) -> str:
    return f"{x:,.0f} €".replace(",", ".")


def pct(x: float) -> str:
    return f"{x * 100:+.1f} %".replace(".", ",")


def referencia(clave: str, cat: pd.DataFrame, hist: pd.DataFrame) -> tuple[str | None, str | None]:
    if clave not in cat.index:
        return None, None
    inst = cat.loc[clave]
    t = inst.ticker if isinstance(inst.ticker, str) else None
    if t and t not in mercado.TICKERS_DESCARTADOS and t in hist.columns and hist[t].notna().sum() > 30:
        return t, inst.moneda_ticker
    if isinstance(inst.proxy, str) and inst.proxy:
        return inst.proxy, "EUR"
    return None, None


def main() -> None:
    # ------------------------------------------------------------- datos (igual que el dashboard)
    tr = parsers.leer_tr()
    mi, _ = parsers.leer_mi()
    pos_mi = parsers.leer_posiciones_mi()
    instrumentos = pd.read_csv(RAIZ / "config" / "instrumentos.csv")
    mapeo = pd.read_csv(RAIZ / "config" / "mapeo_myinvestor.csv") if (RAIZ / "config" / "mapeo_myinvestor.csv").exists() \
        else pd.DataFrame(columns=["patron", "clave"])
    cat = instrumentos.set_index("clave")

    _, abiertas, _ = cartera.fifo_tr(tr) if not tr.empty else (None, pd.DataFrame(), None)
    base = cartera.posiciones(abiertas, pos_mi, mi, instrumentos)
    tickers = set(instrumentos.ticker.dropna()) | set(instrumentos.proxy.dropna()) | set(mercado.FX.values())
    hist = mercado.descargar_historico(sorted(tickers), "5y")
    if hist.empty:
        print("Sin datos de Yahoo Finance: no se envía nada.")
        return

    def precio_en_fecha(clave, fecha):
        ref, moneda = referencia(clave, cat, hist)
        serie = mercado.serie_eur(ref, moneda, hist) if ref else None
        if serie is None:
            return None, "sin precio"
        hasta = serie[serie.index <= pd.Timestamp(fecha) + pd.Timedelta(days=1)]
        if hasta.empty:
            return None, "sin precio"
        if ref == cat.loc[clave, "ticker"]:
            return float(hasta.iloc[-1]), "valor liquidativo"
        foto = pos_mi[pos_mi.clave == clave]
        base_p = serie[serie.index <= foto.fecha.iloc[0] + pd.Timedelta(days=1)] if not foto.empty else serie.iloc[0:0]
        if foto.empty or base_p.empty:
            return None, "sin foto"
        return float(foto.precio.iloc[0] * hasta.iloc[-1] / base_p.iloc[-1]), "equivalente"

    movs = cartera.movimientos_tras_foto(pos_mi, mi, mapeo, precio_en_fecha)
    pos = cartera.completar_nombres(cartera.aplicar_movimientos(base, movs), instrumentos)
    ult = mercado.descargar_ultimos(sorted(tickers))
    precios = mercado.precios_actuales(pos, instrumentos, hist, ult)
    val = cartera.valorar(pos, precios, instrumentos)
    val = val[val.valor > 0.5]

    # ------------------------------------------------------------- señales que cambian hoy
    avisos_compra, avisos_venta = [], []
    pesos = val.groupby("clave").valor.sum()
    vistas = set()
    for clave in pesos.sort_values(ascending=False).index:
        # Monetarios y renta fija suben casi en línea recta: siempre parecerían «en máximos»
        if clave in cat.index and cat.loc[clave, "tipo"] in SIN_SEÑALES:
            continue
        ref, _ = referencia(clave, cat, hist)
        if not ref or ref in vistas or ref not in hist.columns:
            continue
        vistas.add(ref)
        serie = hist[ref].dropna()
        if len(serie) < 60:
            continue
        hoy = mercado.señales(serie, float(ult[ref]) if ref in ult.index else None)
        ayer = mercado.señales(serie.iloc[:-1])
        if not hoy or not ayer:
            continue
        nombre = cat.loc[clave, "nombre"] if clave in cat.index else clave
        if hoy["señal"] == "Zona de compra" and ayer["señal"] != "Zona de compra":
            avisos_compra.append(f"• {h(nombre)}: {h(hoy['motivos'])}")
        venta_hoy = mercado.señal_venta(hoy["puntos venta"])
        venta_ayer = mercado.señal_venta(ayer["puntos venta"])
        if venta_hoy == "Recoger beneficios" and venta_ayer != "Recoger beneficios":
            avisos_venta.append(f"• {h(nombre)}: {h(', '.join(hoy['motivos venta']))}")

    # ------------------------------------------------------------- evolución de la cartera
    refs = {}
    for clave in pesos.index:
        ref, moneda = referencia(clave, cat, hist)
        s = mercado.serie_eur(ref, moneda, hist) if ref else None
        if s is not None:
            refs[clave] = s
    indice = mercado.indice_cartera(pesos, refs)
    total = float(pesos.sum())
    semana = mes = np.nan
    if len(indice) > 30:
        ultimo = indice.iloc[-1]
        hace_semana = indice[indice.index <= indice.index[-1] - pd.Timedelta(days=7)]
        inicio_mes = indice[indice.index < indice.index[-1].replace(day=1)]
        semana = ultimo / hace_semana.iloc[-1] - 1 if len(hace_semana) else np.nan
        mes = ultimo / inicio_mes.iloc[-1] - 1 if len(inicio_mes) else np.nan

    # ------------------------------------------------------------- mensaje
    hoy_fecha = pd.Timestamp.today()
    partes = []
    if avisos_compra:
        partes.append("🟢 <b>Entran en zona de compra</b>\n" + "\n".join(avisos_compra))
    if avisos_venta:
        partes.append("🟠 <b>Entran en zona de recoger beneficios</b>\n" + "\n".join(avisos_venta))
    if not np.isnan(semana) and semana <= -UMBRAL_CAIDA:
        partes.append(f"🔻 <b>La cartera cae {pct(semana)} en la última semana</b> "
                      f"(unos {eur(total * (1 - 1 / (1 + semana)))}).")
    if hoy_fecha.month == 12 and not mi.empty:
        aportado = -mi[(mi.fecha.dt.year == hoy_fecha.year) & (mi.tipo == "APORTACION PLANES")].importe.sum()
        margen = cartera.LIMITE_PLAN_PENSIONES - aportado
        if margen > 0:
            partes.append(f"🗓️ <b>Plan de pensiones:</b> te quedan {eur(margen)} de margen deducible este año.")
    if hoy_fecha.weekday() == RESUMEN_DIA:
        linea = f"📊 <b>Resumen semanal</b>\nCartera: {eur(total)}"
        if not np.isnan(semana):
            linea += f"\nSemana: {pct(semana)}"
        if not np.isnan(mes):
            linea += f"\nMes: {pct(mes)}"
        partes.append(linea)

    if not partes:
        print("Nada que avisar hoy.")
        return
    mensaje = "<b>Vitillonario</b>\n\n" + "\n\n".join(partes)
    enviar(mensaje)


def enviar(texto: str) -> None:
    if DRY_RUN:
        print("DRY_RUN: no se envía. Mensaje:\n" + texto)
        return
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        sys.exit("Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en los secrets del repositorio.")
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": texto, "parse_mode": "HTML"}, timeout=30)
    if r.status_code != 200:
        sys.exit(f"Telegram respondió {r.status_code}: {r.text[:200]}")
    print("Mensaje enviado.")


if __name__ == "__main__":
    main()
