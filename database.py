# =========================================================
# BASE DE DATOS - GOOGLE SHEETS
# =========================================================
import json
import re
from datetime import datetime
from typing import Any, Dict, List
import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials
from config import SHEET_ID
from modules import MODULES
@st.cache_resource
def get_gs_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    raw_json = st.secrets.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw_json:
        raise RuntimeError("Falta GOOGLE_SERVICE_ACCOUNT_JSON en Secrets.")
    try:
        service_account_info = json.loads(raw_json)
    except json.JSONDecodeError:
        import re
        def fix_private_key(match):
            key = match.group(1)
            key = key.replace("\n", "\\n")
            return f'"private_key": "{key}"'
        fixed_json = re.sub(
            r'"private_key"\s*:\s*"([\s\S]*?)"',
            fix_private_key,
            raw_json,
            count=1,
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
    gc = get_gs_client()
    return gc.open_by_key(SHEET_ID)
def get_or_create_worksheet(table: str):
    sh = get_spreadsheet()
    try:
        return sh.worksheet(table)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=table, rows=1000, cols=40)
def get_df(table: str) -> pd.DataFrame:
    try:
        ws = get_or_create_worksheet(table)
        values = ws.get_all_values()
        if not values or len(values) < 2:
            return pd.DataFrame()
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
    except Exception as e:
        st.error(f"ERROR leyendo Google Sheets en tabla: {table}")
        st.exception(e)
        return pd.DataFrame()
def sync_df_to_sheet(table: str, df: pd.DataFrame) -> int:
    ws = get_or_create_worksheet(table)
    df = df.copy()
    df = df.where(pd.notnull(df), "")
    df = df.astype(str)
    ws.clear()
    if df.empty:
        ws.update([["Sin datos"]])
        return 0
    data = [df.columns.tolist()] + df.values.tolist()
    ws.update(data)
    return len(df)
def insert_row(table: str, data: Dict[str, Any]) -> None:
    df = get_df(table)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = {**data, "created_at": now, "updated_at": now}
    new_df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    sync_df_to_sheet(table, new_df)
    st.cache_data.clear()
def bulk_insert_rows(table: str, rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    df = get_df(table)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clean_rows = [

        {**row, "created_at": now, "updated_at": now}

        for row in rows
    ]
    new_df = pd.concat([df, pd.DataFrame(clean_rows)], ignore_index=True)
    sync_df_to_sheet(table, new_df)
    st.cache_data.clear()
    return len(clean_rows)
def replace_table_rows(table: str, rows: List[Dict[str, Any]]) -> int:
    if not rows:
        sync_df_to_sheet(table, pd.DataFrame())
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clean_rows = [
        {**row, "created_at": now, "updated_at": now}
        for row in rows
    ]
    df = pd.DataFrame(clean_rows)
    sync_df_to_sheet(table, df)
    st.cache_data.clear()
    return len(clean_rows)
def update_row(table: str, row_id: int, data: Dict[str, Any]) -> None:
    df = get_df(table)
    if df.empty or "id" not in df.columns:
        return
    df["id"] = pd.to_numeric(df["id"], errors="coerce")
    mask = df["id"] == row_id
    for key, value in data.items():
        df.loc[mask, key] = value
    df.loc[mask, "updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sync_df_to_sheet(table, df)
    st.cache_data.clear()
def delete_row(table: str, row_id: int) -> None:
    df = get_df(table)
    if df.empty or "id" not in df.columns:
        return
    df["id"] = pd.to_numeric(df["id"], errors="coerce")
    df = df[df["id"] != row_id]
    sync_df_to_sheet(table, df)
    st.cache_data.clear()
def sync_all_to_sheets() -> Dict[str, int]:
    result = {}
    for cfg in MODULES.values():
        table = cfg["table"]
        df = get_df(table)
        result[table] = sync_df_to_sheet(table, df)
    return result
def restore_table_from_sheet(table: str) -> int:
    df = get_df(table)
    return len(df)
def restore_all_from_sheets() -> Dict[str, int]:
    result = {}
    for cfg in MODULES.values():
        table = cfg["table"]
        result[table] = restore_table_from_sheet(table)
    return result
