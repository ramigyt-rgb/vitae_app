# =========================================================
# BASE DE DATOS - SOLO GOOGLE SHEETS
# =========================================================
import json
import threading
import uuid
import gspread
import pandas as pd
import streamlit as st
from typing import Any, Dict, List
from google.oauth2.service_account import Credentials
from vitae.core.config import SHEET_ID
from vitae.core.tablas import MODULES
@st.cache_resource

def get_gs_client():

    scopes = [

        "https://www.googleapis.com/auth/spreadsheets",

        "https://www.googleapis.com/auth/drive",

    ]

    raw_json = st.secrets.get("GOOGLE_SERVICE_ACCOUNT_JSON", None)

    if raw_json is None:

        raise RuntimeError("Falta GOOGLE_SERVICE_ACCOUNT_JSON en Secrets.")

    raw_json = str(raw_json).strip()

    # Limpia si viene con espacios raros

    raw_json = raw_json.replace("\r\n", "\n").replace("\r", "\n")

    try:

        service_account_info = json.loads(raw_json)

    except json.JSONDecodeError:

        import re

        # Arregla SOLO el private_key, no todo el JSON

        def fix_private_key(match):

            key = match.group(1)

            key = key.replace("\n", "\\n")

            return f'"private_key": "{key}"'

        fixed_json = re.sub(

            r'"private_key"\s*:\s*"([\s\S]*?)"',

            fix_private_key,

            raw_json,

            count=1

        )

        service_account_info = json.loads(fixed_json)

    credentials = Credentials.from_service_account_info(

        service_account_info,

        scopes=scopes,

    )

    return gspread.authorize(credentials)


@st.cache_resource
def get_spreadsheet():
    if not SHEET_ID:
        raise RuntimeError("Falta GOOGLE_SHEET_ID en Secrets.")
    return get_gs_client().open_by_key(SHEET_ID)


# ---------------------------------------------------------
# CACHE DE LECTURA (velocidad)
# ---------------------------------------------------------
# Antes cada tabla se leía por separado y cada lectura hacía 2 llamadas a
# Google (buscar la pestaña + leer valores). Con ~22 tablas eso eran ~44
# llamadas seguidas al abrir el dashboard, y además chocaba con el límite
# de Google de ~60 lecturas por minuto.
#
# Ahora:
#   - Las pestañas se buscan una sola vez y se recuerdan.
#   - Cuando falta una tabla en memoria, se leen JUNTAS todas las tablas
#     de MODULES en UNA sola llamada (values_batch_get).
#   - El cache se invalida exactamente igual que antes: cuando vence (5 min)
#     o cuando cualquier parte de la app llama a st.cache_data.clear()
#     (al guardar, al tocar "Actualizar", etc.). Así los datos nunca quedan
#     más viejos que con el sistema anterior.

_CACHE_TTL_SEGUNDOS = 300
_cache_lock = threading.RLock()
_cache_tablas: Dict[str, pd.DataFrame] = {}
_cache_generacion: str | None = None
_cache_hojas: Dict[str, Any] = {}

# Tablas que se precargan juntas. "convenios" se excluye porque esa hoja
# maestra no se usa (el módulo lee convenios_vm / convenios_vmr).
_TABLAS_PRECARGA: List[str] = list(dict.fromkeys(
    cfg["table"] for cfg in MODULES.values()
    if cfg.get("table") and cfg.get("table") != "convenios"
))


@st.cache_data(ttl=_CACHE_TTL_SEGUNDOS, show_spinner=False)
def _generacion_cache() -> str:
    """Token que cambia cada vez que se ejecuta st.cache_data.clear() o vence el TTL."""
    return uuid.uuid4().hex


def _hojas_por_nombre(refrescar: bool = False) -> Dict[str, Any]:
    global _cache_hojas
    with _cache_lock:
        if refrescar or not _cache_hojas:
            _cache_hojas = {ws.title: ws for ws in get_spreadsheet().worksheets()}
        return _cache_hojas


def get_or_create_worksheet(table: str):
    hojas = _hojas_por_nombre()
    if table not in hojas:
        hojas = _hojas_por_nombre(refrescar=True)
    if table in hojas:
        return hojas[table]
    sh = get_spreadsheet()
    try:
        ws = sh.worksheet(table)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=table, rows=1000, cols=40)
    with _cache_lock:
        _cache_hojas[table] = ws
    return ws


def _valores_a_df(values: List[List[Any]]) -> pd.DataFrame:
    """Convierte la matriz de valores de una pestaña en DataFrame (misma lógica de siempre)."""
    if not values or len(values) < 2:
        return pd.DataFrame()
    # Igual que gspread.get_all_values(): todas las filas al mismo ancho.
    ancho = max(len(fila) for fila in values)
    values = [list(fila) + [""] * (ancho - len(fila)) for fila in values]
    headers = [str(h).strip() for h in values[0]]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)
    df = df.dropna(how="all")
    df = df.loc[:, [c for c in df.columns if str(c).strip() != ""]]
    if df.empty:
        return pd.DataFrame()
    if len(df.columns) == 1 and str(df.iloc[0, 0]).strip().lower() == "sin datos":
        return pd.DataFrame()
    return df


def _rango_hoja(nombre: str) -> str:
    return "'" + nombre.replace("'", "''") + "'"


def _leer_tabla_individual(table: str) -> pd.DataFrame:
    ws = get_or_create_worksheet(table)
    return _valores_a_df(ws.get_all_values())


def _leer_tablas_en_lote(tablas: List[str]) -> Dict[str, pd.DataFrame]:
    """Lee varias pestañas existentes en una sola llamada a la API."""
    if not tablas:
        return {}
    respuesta = get_spreadsheet().values_batch_get([_rango_hoja(t) for t in tablas])
    rangos = respuesta.get("valueRanges", [])
    if len(rangos) != len(tablas):
        raise RuntimeError("Respuesta incompleta de Google Sheets en lectura por lote.")
    return {t: _valores_a_df(r.get("values", [])) for t, r in zip(tablas, rangos)}


def get_df(table: str) -> pd.DataFrame:
    global _cache_generacion
    generacion = _generacion_cache()
    with _cache_lock:
        if _cache_generacion != generacion:
            _cache_tablas.clear()
            _cache_generacion = generacion
        if table in _cache_tablas:
            return _cache_tablas[table].copy()

        hojas = _hojas_por_nombre()
        if table not in hojas:
            # Pestaña nueva o creada recién: se busca/crea como antes.
            df = _leer_tabla_individual(table)
            _cache_tablas[table] = df
            return df.copy()

        pendientes = [table] + [
            t for t in _TABLAS_PRECARGA
            if t != table and t in hojas and t not in _cache_tablas
        ]
        try:
            _cache_tablas.update(_leer_tablas_en_lote(pendientes))
        except Exception:
            # Si la lectura por lote falla, se vuelve al método anterior
            # solo para la tabla pedida (y sus errores se propagan igual).
            _cache_tablas[table] = _leer_tabla_individual(table)
        return _cache_tablas[table].copy()


def sync_df_to_sheet(table: str, df: pd.DataFrame) -> int:
    ws = get_or_create_worksheet(table)
    df = df.copy()
    df = df.where(pd.notnull(df), "")
    df = df.astype(str)
    ws.clear()
    if df.empty:
        ws.update([["Sin datos"]])
        st.cache_data.clear()
        return 0
    data = [df.columns.tolist()] + df.values.tolist()
    ws.update(data)
    st.cache_data.clear()
    return len(df)
def insert_row(table: str, data: Dict[str, Any]) -> None:
    df = get_df(table)
    new_row = pd.DataFrame([data])
    if df.empty:
        final_df = new_row
    else:
        final_df = pd.concat([df, new_row], ignore_index=True)
    sync_df_to_sheet(table, final_df)
def bulk_insert_rows(table: str, rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    df = get_df(table)
    new_df = pd.DataFrame(rows)
    if df.empty:
        final_df = new_df
    else:
        final_df = pd.concat([df, new_df], ignore_index=True)
    return sync_df_to_sheet(table, final_df)
def replace_table_rows(table: str, rows: List[Dict[str, Any]]) -> int:
    df = pd.DataFrame(rows)
    return sync_df_to_sheet(table, df)
def update_row(table: str, row_id: int, data: Dict[str, Any]) -> None:
    df = get_df(table)
    if df.empty:
        return
    if "id" in df.columns:
        df.loc[df["id"].astype(str) == str(row_id), list(data.keys())] = list(data.values())
    sync_df_to_sheet(table, df)
def delete_row(table: str, row_id: int) -> None:
    df = get_df(table)
    if df.empty:
        return
    if "id" in df.columns:
        df = df[df["id"].astype(str) != str(row_id)]
    sync_df_to_sheet(table, df)
def sync_all_to_sheets() -> Dict[str, int]:
    return {}
def restore_all_from_sheets() -> Dict[str, int]:
    return {}
def restore_table_from_sheet(table: str) -> int:
    df = get_df(table)
    return len(df)