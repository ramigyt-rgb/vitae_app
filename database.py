# =========================================================

# BASE DE DATOS - SOLO GOOGLE SHEETS

# =========================================================

import json

import re

from typing import Any, Dict, List

import gspread

import pandas as pd

import streamlit as st

from google.oauth2.service_account import Credentials

from config import SHEET_ID

@st.cache_resource

def get_gs_client():

    scopes = [

        "https://www.googleapis.com/auth/spreadsheets",

        "https://www.googleapis.com/auth/drive",

    ]

    raw_json = st.secrets.get("GOOGLE_SERVICE_ACCOUNT_JSON", None)

    if raw_json is None:

        raise RuntimeError("Falta GOOGLE_SERVICE_ACCOUNT_JSON en Secrets.")

    if isinstance(raw_json, dict):

        service_account_info = dict(raw_json)

    else:

        raw_json = str(raw_json).strip()

        raw_json = raw_json.replace("\r\n", "\n").replace("\r", "\n")

        try:

            service_account_info = json.loads(raw_json)

        except json.JSONDecodeError:

            def fix_private_key(match):

                key = match.group(1).replace("\n", "\\n")

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

    return get_gs_client().open_by_key(SHEET_ID)

@st.cache_resource

def get_worksheet(table: str):

    sh = get_spreadsheet()

    try:

        return sh.worksheet(table)

    except gspread.WorksheetNotFound:

        return sh.add_worksheet(title=table, rows=1000, cols=40)

@st.cache_data(ttl=300, show_spinner=False)

def get_df(table: str) -> pd.DataFrame:

    ws = get_worksheet(table)

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

    ws = get_worksheet(table)

    df = df.copy()

    df = df.where(pd.notnull(df), "")

    df = df.astype(str)

    if df.empty:

        data = [["Sin datos"]]

        total = 0

    else:

        data = [df.columns.tolist()] + df.values.tolist()

        total = len(df)

    ws.clear()

    ws.update("A1", data)

    get_df.clear()

    return total

def insert_row(table: str, data: Dict[str, Any]) -> None:

    df = get_df(table)

    new_row = pd.DataFrame([data])

    final_df = new_row if df.empty else pd.concat([df, new_row], ignore_index=True)

    sync_df_to_sheet(table, final_df)

def bulk_insert_rows(table: str, rows: List[Dict[str, Any]]) -> int:

    if not rows:

        return 0

    df = get_df(table)

    new_df = pd.DataFrame(rows)

    final_df = new_df if df.empty else pd.concat([df, new_df], ignore_index=True)

    return sync_df_to_sheet(table, final_df)

def replace_table_rows(table: str, rows: List[Dict[str, Any]]) -> int:

    df = pd.DataFrame(rows)

    return sync_df_to_sheet(table, df)

def update_row(table: str, row_id: int, data: Dict[str, Any]) -> None:

    df = get_df(table)

    if df.empty or "id" not in df.columns:

        return

    df.loc[df["id"].astype(str) == str(row_id), list(data.keys())] = list(data.values())

    sync_df_to_sheet(table, df)

def delete_row(table: str, row_id: int) -> None:

    df = get_df(table)

    if df.empty or "id" not in df.columns:

        return

    df = df[df["id"].astype(str) != str(row_id)]

    sync_df_to_sheet(table, df)

def sync_all_to_sheets() -> Dict[str, int]:

    return {}

def restore_all_from_sheets() -> Dict[str, int]:

    return {}

def restore_table_from_sheet(table: str) -> int:

    df = get_df(table)

    return len(df)
