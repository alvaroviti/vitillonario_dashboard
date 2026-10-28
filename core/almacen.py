"""Guarda los ficheros subidos en el repositorio privado de GitHub.

En Streamlit Cloud el disco se borra al reiniciar la app. Si en los secrets hay
`github_token` y `github_repo`, cada fichero subido se guarda también como commit
en el repo, y en el siguiente arranque ya forma parte de los datos.
"""
from __future__ import annotations

import base64

import requests

API = "https://api.github.com"


def configurado(secrets) -> bool:
    try:
        return bool(secrets["github_token"] and secrets["github_repo"])
    except Exception:
        return False


def subir(secrets, ruta_repo: str, contenido: bytes, mensaje: str) -> tuple[bool, str]:
    """Crea o actualiza `ruta_repo` (p. ej. 'data/tr/fichero.csv'). Devuelve (ok, detalle)."""
    repo = secrets["github_repo"]
    rama = secrets.get("github_branch", "main")
    cabeceras = {"Authorization": f"Bearer {secrets['github_token']}",
                 "Accept": "application/vnd.github+json"}
    url = f"{API}/repos/{repo}/contents/{ruta_repo}"

    actual = requests.get(url, headers=cabeceras, params={"ref": rama}, timeout=20)
    cuerpo = {"message": mensaje, "branch": rama,
              "content": base64.b64encode(contenido).decode()}
    if actual.status_code == 200:
        cuerpo["sha"] = actual.json()["sha"]
    elif actual.status_code != 404:
        return False, f"GitHub respondió {actual.status_code} al consultar {ruta_repo}"

    r = requests.put(url, headers=cabeceras, json=cuerpo, timeout=30)
    if r.status_code in (200, 201):
        return True, ruta_repo
    return False, f"GitHub respondió {r.status_code}: {r.json().get('message', '')}"
