"""Lectura y consolidación de exportaciones de Trade Republic y MyInvestor.

Todos los ficheros de una carpeta se leen y se unen sin duplicados, así que
se pueden subir exportaciones solapadas sin miedo.
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
TR_DIR = DATA / "tr"
MI_DIR = DATA / "myinvestor"

# Columnas de TR que contienen datos personales y no se usan en el dashboard
TR_COLUMNAS_PRIVADAS = ["counterparty_name", "counterparty_iban", "payment_reference", "mcc_code"]


# --------------------------------------------------------------------------- #
# Trade Republic
# --------------------------------------------------------------------------- #
def leer_tr(carpeta: Path = TR_DIR) -> pd.DataFrame:
    ficheros = sorted(carpeta.glob("*.csv"))
    if not ficheros:
        return pd.DataFrame()
    df = pd.concat([pd.read_csv(f) for f in ficheros], ignore_index=True)
    df = df.drop_duplicates(subset="transaction_id", keep="last")
    df = df.drop(columns=[c for c in TR_COLUMNAS_PRIVADAS if c in df.columns])
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True, format="ISO8601")
    df["date"] = pd.to_datetime(df["date"])
    for c in ["shares", "price", "amount", "fee", "tax"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("datetime").reset_index(drop=True)


def saldo_efectivo_tr(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    return float((df["amount"].fillna(0) + df["fee"].fillna(0) + df["tax"].fillna(0)).sum())


# --------------------------------------------------------------------------- #
# MyInvestor - extracto de cuenta de efectivo (.xls que en realidad es HTML)
# --------------------------------------------------------------------------- #
def _num_es(serie: pd.Series) -> pd.Series:
    return (serie.astype(str).str.strip()
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .str.replace("+", "", regex=False)
            .pipe(pd.to_numeric, errors="coerce"))


def _leer_extracto_mi(contenido: str, origen: str) -> pd.DataFrame:
    t = pd.read_html(io.StringIO(contenido), thousands=None)[0]
    t.columns = ["fecha", "fecha_valor", "tipo", "concepto", "cargo_abono", "importe", "saldo"]
    t = t[~t["fecha_valor"].astype(str).str.contains("Saldo")].copy()
    t["fecha"] = pd.to_datetime(t["fecha"].astype(str).str.strip(), dayfirst=True)
    t["importe"] = _num_es(t["importe"])
    t["saldo"] = _num_es(t["saldo"])
    t["concepto"] = t["concepto"].fillna("").astype(str).str.strip()
    t["origen"] = origen
    t["orden"] = range(len(t))
    return t


def leer_mi(carpeta: Path = MI_DIR) -> tuple[pd.DataFrame, list[str]]:
    """Devuelve (movimientos, avisos). Valida que la cadena de saldos cuadre."""
    ficheros = sorted(carpeta.glob("*.xls"))
    if not ficheros:
        return pd.DataFrame(), []
    partes = [_leer_extracto_mi(f.read_text(encoding="latin-1"), f.name) for f in ficheros]
    df = pd.concat(partes, ignore_index=True)
    clave = ["fecha", "fecha_valor", "tipo", "concepto", "importe", "saldo"]
    df = df.drop_duplicates(subset=clave, keep="first")
    df = df.sort_values(["fecha", "origen", "orden"], kind="stable").reset_index(drop=True)
    df = _reordenar_por_saldo(df)

    avisos = []
    esperado = df["saldo"].shift(1) + df["importe"]
    saltos = df[(esperado - df["saldo"]).abs() > 0.011].iloc[1:] if len(df) > 1 else df.iloc[0:0]
    for _, r in saltos.iterrows():
        avisos.append(f"La cadena de saldos no cuadra el {r.fecha:%d/%m/%Y} ({r.tipo}). "
                      "Puede faltar un extracto de esas fechas.")
    # Si el fichero que contiene el último movimiento roza el límite de filas,
    # puede que la exportación esté cortada y falten los movimientos más recientes.
    ultimo = max(zip(ficheros, partes), key=lambda fp: fp[1]["fecha"].max())
    if len(ultimo[1]) >= 205:
        avisos.append(f"{ultimo[0].name} tiene {len(ultimo[1])} movimientos y es el más reciente: "
                      "MyInvestor suele cortar ahí. Exporta un tramo desde "
                      f"{ultimo[1]['fecha'].max():%d/%m/%Y} hasta hoy para comprobarlo.")
    return df, avisos


def _reordenar_por_saldo(df: pd.DataFrame) -> pd.DataFrame:
    """Dentro de un mismo día, ordena los movimientos para que el saldo encadene."""
    if df.empty:
        return df
    filas, pendientes = [], df.to_dict("records")
    actual = pendientes.pop(0)
    filas.append(actual)
    while pendientes:
        idx = next((i for i, r in enumerate(pendientes[:30])
                    if abs(round(actual["saldo"] + r["importe"], 2) - r["saldo"]) < 0.011), 0)
        actual = pendientes.pop(idx)
        filas.append(actual)
    return pd.DataFrame(filas)


# --------------------------------------------------------------------------- #
# MyInvestor - foto de posiciones (CSV mantenido a mano o desde capturas)
# --------------------------------------------------------------------------- #
def leer_posiciones_mi(carpeta: Path = MI_DIR) -> pd.DataFrame:
    ficheros = sorted(carpeta.glob("posiciones_*.csv"))
    if not ficheros:
        return pd.DataFrame()
    pos = pd.read_csv(ficheros[-1], parse_dates=["fecha"])  # la más reciente manda
    pos["valor"] = pos["titulos"] * pos["precio"]
    pos["coste"] = pos["titulos"] * pos["coste_medio"]
    return pos


def nombre_seguro(nombre: str) -> str:
    """Deja solo el nombre del fichero, sin carpetas ni caracteres raros, para que una subida
    nunca pueda escribir fuera de data/ (por ejemplo con «../app.py»)."""
    import re
    base = Path(str(nombre).replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "fichero"
    return base[:120]


def guardar_subida(nombre: str, contenido: bytes, destino: Path) -> Path:
    destino.mkdir(parents=True, exist_ok=True)
    ruta = (destino / nombre_seguro(nombre)).resolve()
    if destino.resolve() not in ruta.parents:
        raise ValueError("Nombre de fichero no permitido")
    ruta.write_bytes(contenido)
    return ruta
