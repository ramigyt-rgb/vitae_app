# core/config.py
from __future__ import annotations
from pathlib import Path
import streamlit as st
APP_TITLE = "Sistema de Gestión"
DATE_FMT = "%Y-%m-%d"
TECH_COLUMNS = [
    "id",
    "created_at",
    "updated_at",
]
LOGO_PATH = Path("logo_vitae.png")
SHEET_ID = st.secrets.get("GOOGLE_SHEET_ID", "")
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]