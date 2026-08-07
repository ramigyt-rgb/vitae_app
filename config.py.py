# =========================================================
# CONFIG GENERAL
# =========================================================
from pathlib import Path
import os
import streamlit as st


def _secret(name: str, default: str = "") -> str:
    """Lee primero variable de entorno y luego Streamlit Secrets."""
    env_value = os.getenv(name)
    if env_value is not None and str(env_value).strip():
        return str(env_value).strip()
    try:
        value = st.secrets.get(name, default)
        return str(value).strip() if value is not None else default
    except Exception:
        return default


GEMINI_API_KEY = _secret("GEMINI_API_KEY", "")
# Conserva por defecto el modelo que ya usa la instalación actual.
# Se puede cambiar sin tocar código agregando GEMINI_MODEL a Secrets.
GEMINI_MODEL = _secret("GEMINI_MODEL", "gemini-3.5-flash-lite")

APP_TITLE = "Sistema de Gestión"
DB_PATH = Path("vitae_gestion.db")
SHEET_ID = _secret("GOOGLE_SHEET_ID", "").replace('"', "")
DATE_FMT = "%Y-%m-%d"
TECH_COLUMNS = ["id", "created_at", "updated_at"]
