# =========================================================
# BASE DE DATOS
# =========================================================
import json
import gspread
import pandas as pd
import streamlit as st
from typing import Any, Dict, List
from google.oauth2.service_account import Credentials
from config import SHEET_ID
from modules import MODULES
def get_gs_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    raw_json = st.secrets.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw_json:
        raise RuntimeError("Falta GOOGLE_SERVICE_ACCOUNT_JSON en Secrets.")
    service_account_info = json.loads(raw_json)
    credentials = Credentials.from_service_account_info(
        service_account_info,
        scopes=scopes,
    )
    return gspread.authorize(credentials)
def get_spreadsheet():
    if not SHEET_ID:
        raise RuntimeError("Falta GOOGLE_SHEET_ID en Secrets.")
    gc = get_gs_client()
    return gc.open_by_key(SHEET_ID)
def get_or_create_worksheet(table: str):
    sh = get_spreadsheet()
    try:
        return sh.worksheet(table)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(
            title=table,
            rows=1000,
            cols=40,
        )
def get_df(table: str) -> pd.DataFrame:
    with connect() as conn:
        try:
            return pd.read_sql_query(f"SELECT * FROM {table} ORDER BY id DESC", conn)
        except Exception:
            return pd.DataFrame()               
def insert_row(table: str, data: Dict[str, Any]) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = {**data, "created_at": now, "updated_at": now}
    cols = list(data.keys())
    placeholders = ", ".join(["?"] * len(cols))
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    with connect() as conn:
        conn.execute(sql, [data[c] for c in cols])
        conn.commit()
        sync_table_to_sheet(table)
def bulk_insert_rows(table: str, rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clean_rows = [{**row, "created_at": now, "updated_at": now} for row in rows]
    cols = list(clean_rows[0].keys())
    placeholders = ", ".join(["?"] * len(cols))
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    values = [[row.get(c, "") for c in cols] for row in clean_rows]
    with connect() as conn:
        conn.executemany(sql, values)
        conn.commit()
    return len(clean_rows)
def update_row(table: str, row_id: int, data: Dict[str, Any]) -> None:
    data = {**data, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    sets = ", ".join([f"{k} = ?" for k in data.keys()])
    sql = f"UPDATE {table} SET {sets} WHERE id = ?"
    with connect() as conn:
        conn.execute(sql, [*data.values(), row_id])
        conn.commit()
def delete_row(table: str, row_id: int) -> None:
    with connect() as conn:
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
        conn.commit()
        sync_table_to_sheet(table)
def replace_table_rows(table: str, rows: List[Dict[str, Any]]) -> int:
    with connect() as conn:
        conn.execute(f"DELETE FROM {table}")
        conn.commit()
    result = bulk_insert_rows(table, rows)
    try:
        sync_table_to_sheet(table)
    except Exception as e:
        st.warning(f"No se pudo sincronizar Google Sheets: {e}")
    return result
    return bulk_insert_rows(table, rows)
        sync_table_to_sheet(table)
