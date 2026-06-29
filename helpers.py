# =========================================================
# HELPERS
# =========================================================
import re
from config import DATE_FMT, TECH_COLUMNS
from datetime import date, datetime
from typing import Any, Dict, List, Tuple
import pandas as pd
import streamlit as st
from modules import MODULES
import numpy as np
def normalize_money_string(value: Any) -> str:
    if value is None:
        return "0"
    try:
        if pd.isna(value):
            return "0"
    except Exception:
        pass
    text = str(value).strip()
    if text == "":
        return "0"
    text = text.replace("$", "").replace("ARS", "").replace("USD", "")
    text = text.replace(" ", "").replace("\u00a0", "")
    text = text.replace("(", "-").replace(")", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    return text
def money(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        if pd.isna(value) or value == "":
            return 0.0
        if isinstance(value, str):
            value = normalize_money_string(value)
        return float(value)
    except Exception:
        return 0.0
def fmt_money(value: Any) -> str:
    return f"$ {money(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, str) and value.strip() == "":
        return None
    try:
        parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()
    except Exception:
        return None
def clean_for_db(value: Any, ftype: str) -> Any:
    if ftype == "date":
        parsed = parse_date(value)
        return parsed.strftime(DATE_FMT) if parsed else ""
    if isinstance(value, date):
        return value.strftime(DATE_FMT)
    if ftype == "bool":
        return 1 if value else 0
    if ftype in {"money", "number"}:
        return float(value or 0)
    if ftype == "int":
        return int(value or 0)
    return value or ""
def default_value(ftype: str, options: List[str] | None = None) -> Any:
    if ftype == "date":
        return date.today()
    if ftype in {"money", "number"}:
        return 0.0
    if ftype == "int":
        return 0
    if ftype == "bool":
        return False
    if ftype == "select":
        return options[0] if options else ""
    return ""
def input_field(field: Tuple, prefix: str, existing: Dict[str, Any] | None = None) -> Any:
    name, ftype, required = field[0], field[1], field[2]
    options = field[3] if len(field) > 3 else None
    label = name.replace("_", " ").title() + (" *" if required else "")
    key = f"{prefix}_{name}"
    old = existing.get(name) if existing else None

    if ftype == "date":
        value = parse_date(old) if old else date.today()
        if value is None:
            value = date.today()
        return st.date_input(label, value=value, key=key)
    if ftype == "money":
        return st.number_input(label, min_value=0.0, step=1000.0, value=money(old), key=key)
    if ftype == "number":
        return st.number_input(label, step=1.0, value=float(money(old)), key=key)
    if ftype == "int":
        return st.number_input(label, min_value=0, step=1, value=int(money(old)), key=key)
    if ftype == "bool":
        return st.checkbox(label, value=bool(old), key=key)
    if ftype == "select":
        idx = 0
        if options and old in options:
            idx = options.index(old)
        return st.selectbox(label, options or [], index=idx, key=key)
    if ftype == "textarea":
        return st.text_area(label, value=str(old or ""), key=key)
    return st.text_input(label, value=str(old or ""), key=key)
def validate_required(cfg: Dict[str, Any], data: Dict[str, Any]) -> List[str]:
    errors = []
    for field in cfg["fields"]:
        name, ftype, required = field[0], field[1], field[2]
        if required and ftype not in {"money", "number", "int", "bool"} and not data.get(name):
            errors.append(name.replace("_", " ").title())
        if required and ftype in {"money", "number"} and money(data.get(name)) <= 0:
            errors.append(name.replace("_", " ").title())
    return errors
def get_field_names(cfg: Dict[str, Any]) -> List[str]:
    return [field[0] for field in cfg["fields"]]
def business_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    cols = [c for c in df.columns if c not in TECH_COLUMNS]
    return df[cols].copy()
def module_business_df(df: pd.DataFrame, cfg: Dict[str, Any]) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    field_names = get_field_names(cfg)
    calc_cols = ["saldo", "saldo_movimiento"]
    cols = [c for c in field_names + calc_cols if c in df.columns]
    return df[cols].copy()
def show_business_table(df: pd.DataFrame, height: int | None = None, **kwargs: Any) -> None:
    if height is not None:
        st.dataframe(business_df(df), use_container_width=True, hide_index=True, height=height, **kwargs)
    else:
        st.dataframe(business_df(df), use_container_width=True, hide_index=True, **kwargs)
def show_module_table(df: pd.DataFrame, cfg: Dict[str, Any], **kwargs: Any) -> None:
    st.dataframe(module_business_df(df, cfg), use_container_width=True, hide_index=True, **kwargs)
def add_balance_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    if "ingreso" in df.columns and "egreso" in df.columns:
        df["saldo_movimiento"] = df["ingreso"].apply(money) - df["egreso"].apply(money)
    if "importe" in df.columns and "pagado" in df.columns:
        if "tipo" in df.columns:
            df["saldo"] = df["importe"].apply(money) - df["pagado"].apply(money)
        elif "saldo" not in df.columns:
            df["saldo"] = df["importe"].apply(money) - df["pagado"].apply(money)
    if "valor_pesos" in df.columns:
        df["valor_pesos"] = df["valor_pesos"].apply(money)
    if "valor_usd" in df.columns:
        df["valor_usd"] = df["valor_usd"].apply(money)
    if "importe_total" in df.columns and "saldo" in df.columns:
        df["saldo"] = df["saldo"].apply(money)
    if "importe_original" in df.columns and "saldo" in df.columns:
        df["saldo"] = df["saldo"].apply(money)
    return df
def first_available_date_col(df: pd.DataFrame, module_name: str) -> str | None:
    if module_name in ["Facturación VMR", "Facturación VM"] and "fecha_factura" in df.columns:
        return "fecha_factura"
    for candidate in ["fecha", "vencimiento", "fecha_pago", "fecha_cobro", "proximo_vencimiento", "fecha_desde", "fecha_hasta"]:
        if candidate in df.columns:
            return candidate
    return None
def apply_filters(df: pd.DataFrame, module_name: str) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    st.subheader("Filtros")
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)

    with c1:
        search = st.text_input("Buscar texto", key=f"search_{module_name}")
    with c2:
        estado = "Todos"
        if "estado" in df.columns:
            estados = [str(x).strip() for x in df["estado"].dropna().unique().tolist() if str(x).strip() != ""]
            estado = st.selectbox("Estado", ["Todos"] + sorted(estados), key=f"estado_{module_name}")
    with c3:
        obra_social = "Todos"
        if "obra_social" in df.columns:
            obra_social = st.selectbox(
                "Obra Social",
                ["Todos"] + sorted(df["obra_social"].dropna().astype(str).unique().tolist())
            )
    with c4:
        procedimiento = "Todos"
        if "procedimiento" in df.columns:
            procedimiento = st.selectbox(
                "Procedimiento",
                ["Todos"] + sorted(df["procedimiento"].dropna().astype(str).unique().tolist())
            )
    with c5:
        medico = "Todos"
        if "medico_responsable" in df.columns:
            medico = st.selectbox(
                "Médico",
                ["Todos"] + sorted(df["medico_responsable"].dropna().astype(str).unique().tolist())
            )
    with c6:
        fecha_desde = st.date_input("Desde", value=date.today() - timedelta(days=3650), key=f"desde_{module_name}")
    with c7:
        fecha_hasta = st.date_input("Hasta", value=date.today() + timedelta(days=3650), key=f"hasta_{module_name}")
    if search:
        mask = df.astype(str).apply(lambda col: col.str.contains(search, case=False, na=False)).any(axis=1)
        df = df[mask]
    if "estado" in df.columns and estado != "Todos":
        df = df[df["estado"].astype(str).str.strip() == estado]
    if obra_social != "Todos":
        df = df[df["obra_social"].astype(str) == obra_social]
    if procedimiento != "Todos":
        df = df[df["procedimiento"].astype(str) == procedimiento]
    if medico != "Todos":
        df = df[df["medico_responsable"].astype(str) == medico]    
    fecha_col = first_available_date_col(df, module_name)
    st.warning(f"Fecha usada para filtrar: {fecha_col}")
    st.info(f"Filas antes del filtro fecha: {len(df)}")
    if fecha_col:
        fechas = pd.to_datetime(df[fecha_col], errors="coerce")
        desde_ts = pd.Timestamp(fecha_desde)
        hasta_ts = pd.Timestamp(fecha_hasta)
        # Conserva filas sin fecha para que no desaparezcan registros importados con fecha_factura vacía.
        df = df[fechas.isna() | ((fechas >= desde_ts) & (fechas <= hasta_ts))]
        st.info(f"Filas despues del filtro fecha: {len(df)}")
    return df