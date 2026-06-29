# =========================================================
# BASE DE DATOS - SOLO GOOGLE SHEETS
# =========================================================
import json
import gspread
import pandas as pd
import streamlit as st
from typing import Any, Dict, List
from google.oauth2.service_account import Credentials
from config import SHEET_ID
from modules import MODULES
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
def get_or_create_worksheet(table: str):
    sh = get_spreadsheet()
    try:
        return sh.worksheet(table)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=table, rows=1000, cols=40)
@st.cache_data(ttl=300, show_spinner=False)
def get_df(table: str) -> pd.DataFrame:
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