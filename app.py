"""
Dashboard de Gastos — FastAPI
Deploy: Render.com (conecta GitHub → auto-deploy)
Local:  python app.py
"""

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import json, os, io, base64, httpx
from pathlib import Path
import pandas as pd

app = FastAPI(title="Dashboard Gastos")

# ─── Configuración ─────────────────────────────────────────────
DATA_FILE  = Path(__file__).parent / "data" / "gastos.json"
STATIC_DIR = Path(__file__).parent / "static"

# Variables de entorno para persistencia en GitHub (opcionales)
# Configúralas en Render → Environment → Add Environment Variable
GH_TOKEN  = os.getenv("GITHUB_TOKEN", "")      # Personal Access Token con permiso 'repo'
GH_REPO   = os.getenv("GITHUB_REPO",  "")      # "usuario/nombre-repo"
GH_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GH_PATH   = "data/gastos.json"                 # ruta dentro del repo


# ─── Mapeo de categorías ──────────────────────────────────────
CATEGORY_MAP = {
    "RESTAURACIÓN":                  "Bares, Restaurantes y ocio",
    "Bares , restaurantes y fiesta": "Bares, Restaurantes y ocio",
    "Bares, restaurantes y fiesta":  "Bares, Restaurantes y ocio",
    "Bares , restaurantes y ocio":   "Bares, Restaurantes y ocio",
    "Ocio, Restauración y fiesta":   "Bares, Restaurantes y ocio",
    "Bares, Restaurantes y Ocio":    "Bares, Restaurantes y ocio",
}

# ─── Persistencia: local + GitHub ─────────────────────────────

def load_data() -> list:
    if DATA_FILE.exists():
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def save_data(data: list):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

async def push_to_github(data: list) -> dict:
    """Hace commit del gastos.json actualizado al repo de GitHub."""
    if not GH_TOKEN or not GH_REPO:
        return {"ok": False, "reason": "GITHUB_TOKEN / GITHUB_REPO no configurados"}

    content_b64 = base64.b64encode(
        json.dumps(data, ensure_ascii=False, indent=2).encode()
    ).decode()

    api_url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    async with httpx.AsyncClient() as client:
        # Obtener SHA del archivo actual (necesario para actualizar)
        r = await client.get(api_url, headers=headers,
                             params={"ref": GH_BRANCH})
        sha = r.json().get("sha", "") if r.status_code == 200 else ""

        payload = {
            "message": f"chore: actualizar gastos.json ({len(data)} registros)",
            "content": content_b64,
            "branch":  GH_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        r2 = await client.put(api_url, headers=headers,
                              json=payload)
        if r2.status_code in (200, 201):
            return {"ok": True, "url": r2.json()["content"]["html_url"]}
        return {"ok": False, "reason": r2.text}

# ─── Procesamiento Excel ───────────────────────────────────────

def process_excel(file_bytes: bytes) -> list:
    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name="Gastos", header=1)
    df["Fecha y hora"] = pd.to_datetime(df["Fecha y hora"])
    df["Cantidad"] = pd.to_numeric(
        df["Cantidad en la divisa de la cuenta"], errors="coerce"
    ).abs()
    df = df.dropna(subset=["Cantidad"])
    df["mes"]       = df["Fecha y hora"].dt.to_period("M").astype(str)
    df["anyo"]      = df["Fecha y hora"].dt.year.astype(str)
    df["fecha"]     = df["Fecha y hora"].dt.strftime("%Y-%m-%d")
    df["categoria"] = df["Categoría"].fillna("Otros").replace(CATEGORY_MAP)
    return (
        df[["fecha", "categoria", "Cantidad", "mes", "anyo"]]
        .rename(columns={"Cantidad": "cantidad"})
        .to_dict("records")
    )

def merge_data(existing: list, new_records: list) -> list:
    # Reemplaza solo los meses presentes en el Excel, conserva el resto
    new_months = set(r["mes"] for r in new_records)
    kept = [r for r in existing if r["mes"] not in new_months]
    merged = kept + new_records
    merged.sort(key=lambda r: r["fecha"], reverse=True)
    return merged

# ─── Rutas ────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    html_path = STATIC_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(404, "dashboard no encontrado en static/index.html")
    return html_path.read_text(encoding="utf-8")

@app.get("/api/data")
async def get_data():
    return JSONResponse(content=load_data())

@app.post("/api/upload")
async def upload_excel(file: UploadFile = File(...)):
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Solo se aceptan archivos Excel (.xlsx / .xls)")
    try:
        new_records = process_excel(await file.read())
    except Exception as e:
        raise HTTPException(422, f"Error procesando el Excel: {e}")

    merged = merge_data(load_data(), new_records)
    save_data(merged)

    # Persistir en GitHub si está configurado
    gh = await push_to_github(merged)

    new_years = sorted(set(r["anyo"] for r in new_records))
    return {
        "ok":       True,
        "message":  f"✓ {len(new_records)} registros importados ({', '.join(new_years)}). Total: {len(merged)}.",
        "total":    len(merged),
        "new":      len(new_records),
        "years":    new_years,
        "fecha_max": merged[0]["fecha"] if merged else None,
        "github":   gh,
        "data":     merged,
    }

# ─── Arranque directo ─────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
