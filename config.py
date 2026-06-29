# =========================================================
# CONFIG GENERAL
# =========================================================
from pathlib import Path
import streamlit as st

APP_TITLE = "Sistema de Gestión"
DB_PATH = Path("vitae_gestion.db")
SHEET_ID = str(st.secrets.get("GOOGLE_SHEET_ID", "")).strip().replace('"', "")
DATE_FMT = "%Y-%m-%d"
TECH_COLUMNS = ["id", "created_at", "updated_at"]
