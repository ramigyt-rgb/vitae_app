
# HELPERS
# =========================================================
import re
from vitae.core.config import DATE_FMT, TECH_COLUMNS
from datetime import date, timedelta
from typing import Any, Dict, List, Tuple
import pandas as pd
import streamlit as st
from vitae.core.tablas import MODULES
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
    elif re.fullmatch(r"-?[1-9]\d{0,2}(\.\d{3})+", text):
        # Punto de miles sin decimales (150.000 · 1.500.000): antes se leía 150 o 0.
        text = text.replace(".", "")
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
    module = str(module_name).lower()
    if "facturación" in module or "facturacion" in module:
        if "mes" in df.columns:
            return "mes"
    for col in ["mes", "fecha", "fecha_factura", "vencimiento", "fecha_pago"]:
        if col in df.columns:
            return col
    return None
MESES_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
            "Septiembre", "Octubre", "Noviembre", "Diciembre"]
_TODO_EL_HISTORIAL = "Todo el historial"
_RANGO = "Rango personalizado"


def leer_fechas_ar(valores: Any) -> pd.Series:
    """Lee fechas tal como vienen de Google Sheets, sin invertir día y mes.

    Acepta, en este orden: ISO (2026-11-05, con o sin hora), 2026/11/05,
    formato argentino 05/11/2026 o 05-11-2026 (con o sin hora), 05/11/26,
    y número de serie de Excel / Sheets. Lo que no se reconoce queda vacío.
    """
    if isinstance(valores, pd.DataFrame):
        valores = valores.iloc[:, 0]
    original = pd.Series(valores)
    if pd.api.types.is_datetime64_any_dtype(original):
        fechas = pd.to_datetime(original, errors="coerce")
        return fechas.dt.tz_convert(None) if fechas.dt.tz is not None else fechas
    raw = original.astype("string").str.strip()
    fechas = pd.Series(pd.NaT, index=original.index, dtype="datetime64[ns]")
    formatos = [
        (r"^(\d{4}-\d{1,2}-\d{1,2})", "%Y-%m-%d"),
        (r"^(\d{4}/\d{1,2}/\d{1,2})", "%Y/%m/%d"),
        (r"^(\d{1,2}/\d{1,2}/\d{4})", "%d/%m/%Y"),
        (r"^(\d{1,2}-\d{1,2}-\d{4})", "%d-%m-%Y"),
        (r"^(\d{1,2}\.\d{1,2}\.\d{4})", "%d.%m.%Y"),
        (r"^(\d{1,2}/\d{1,2}/\d{2})(?!\d)", "%d/%m/%y"),
    ]
    for patron, formato in formatos:
        texto = raw.str.extract(patron, expand=False)
        faltan = fechas.isna() & texto.notna()
        if faltan.any():
            fechas.loc[faltan] = pd.to_datetime(texto.loc[faltan], format=formato, errors="coerce")
    numeros = pd.to_numeric(original, errors="coerce")
    seriales = fechas.isna() & numeros.between(20000, 80000, inclusive="both")
    if seriales.any():
        fechas.loc[seriales] = pd.Timestamp("1899-12-30") + pd.to_timedelta(numeros.loc[seriales].round(), unit="D")
    return fechas


def nombre_mes_es(periodo: Any) -> str:
    p = pd.Period(periodo, freq="M")
    return f"{MESES_ES[p.month - 1]} {p.year}"


def _filtro_select(df: pd.DataFrame, columna: str, etiqueta: str, key: str) -> str:
    opciones = sorted({str(x).strip() for x in df[columna].dropna().astype(str) if str(x).strip()}, key=str.casefold)
    return st.selectbox(etiqueta, ["Todos"] + opciones, key=key)


def apply_filters(df: pd.DataFrame, module_name: str) -> pd.DataFrame:
    """Filtros comunes de los módulos: mes, búsqueda, estado, obra social,
    procedimiento y médico.

    El mes se elige de una lista armada con los meses que realmente tienen
    registros. Las fechas se leen en formato argentino (día/mes/año), igual
    que en el editor: antes se leían como mes/día y los registros del mes
    elegido no aparecían o aparecían en otro mes.
    """
    if df.empty:
        return df
    df = df.copy()
    fecha_col = first_available_date_col(df, module_name)
    fechas = leer_fechas_ar(df[fecha_col]) if fecha_col else pd.Series(pd.NaT, index=df.index)
    periodos = fechas.dt.to_period("M")
    meses = sorted(periodos.dropna().unique(), reverse=True)
    hoy = pd.Timestamp.today().normalize()
    mes_actual = hoy.to_period("M")
    if mes_actual not in meses:
        meses = sorted(set(meses) | {mes_actual}, reverse=True)

    medico_col = "medico" if module_name == "Honorarios médicos" and "medico" in df.columns else (
        "medico_responsable" if "medico_responsable" in df.columns else None
    )
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.1, 1.6, 1.0])
        with c1:
            opciones_mes = ([str(m) for m in meses] + [_TODO_EL_HISTORIAL, _RANGO]) if fecha_col else [_TODO_EL_HISTORIAL]
            mes_sel = st.selectbox(
                "Mes",
                opciones_mes,
                index=opciones_mes.index(str(mes_actual)) if str(mes_actual) in opciones_mes else 0,
                format_func=lambda o: o if o in (_TODO_EL_HISTORIAL, _RANGO) else nombre_mes_es(o),
                key=f"mes_{module_name}",
                help="Muestra los registros del mes elegido. Los meses de la lista son los que tienen datos.",
            )
        with c2:
            search = st.text_input("Buscar texto", key=f"search_{module_name}", placeholder="Nombre, concepto, comprobante…")
        with c3:
            estado = _filtro_select(df, "estado", "Estado", f"estado_{module_name}") if "estado" in df.columns else "Todos"

        if mes_sel == _RANGO:
            r1, r2, _ = st.columns([1, 1, 2])
            fecha_desde = r1.date_input("Desde", value=mes_actual.start_time.date(), format="DD/MM/YYYY", key=f"desde_{module_name}")
            fecha_hasta = r2.date_input("Hasta", value=hoy.date(), format="DD/MM/YYYY", key=f"hasta_{module_name}")
        extra = [c for c in ("obra_social", "procedimiento") if c in df.columns] + ([medico_col] if medico_col else [])
        obra_social = procedimiento = medico = "Todos"
        if extra:
            cols = st.columns(len(extra))
            for col_ui, campo in zip(cols, extra):
                with col_ui:
                    if campo == "obra_social":
                        obra_social = _filtro_select(df, campo, "Obra Social", f"os_{module_name}")
                    elif campo == "procedimiento":
                        procedimiento = _filtro_select(df, campo, "Procedimiento", f"proc_{module_name}")
                    else:
                        medico = _filtro_select(df, campo, "Médico", f"medico_{module_name}")

    mascara = pd.Series(True, index=df.index)
    if search:
        mascara &= df.astype(str).apply(lambda col: col.str.contains(search, case=False, na=False, regex=False)).any(axis=1)

    def norm(s):
        return s.astype(str).str.strip().str.lower()

    if "estado" in df.columns and estado != "Todos":
        mascara &= norm(df["estado"]) == str(estado).strip().lower()
    if "obra_social" in df.columns and obra_social != "Todos":
        mascara &= norm(df["obra_social"]) == str(obra_social).strip().lower()
    if "procedimiento" in df.columns and procedimiento != "Todos":
        mascara &= norm(df["procedimiento"]) == str(procedimiento).strip().lower()
    if medico_col and medico != "Todos":
        mascara &= norm(df[medico_col]) == str(medico).strip().lower()

    sin_fecha = int((fechas.isna() & mascara).sum()) if fecha_col and mes_sel != _TODO_EL_HISTORIAL else 0
    if fecha_col and mes_sel == _RANGO:
        desde_ts, hasta_ts = pd.Timestamp(fecha_desde), pd.Timestamp(fecha_hasta)
        if desde_ts > hasta_ts:
            desde_ts, hasta_ts = hasta_ts, desde_ts
        mascara &= fechas.between(desde_ts, hasta_ts + pd.Timedelta(hours=23, minutes=59, seconds=59))
        texto_periodo = f"del {desde_ts.strftime('%d/%m/%Y')} al {hasta_ts.strftime('%d/%m/%Y')}"
    elif fecha_col and mes_sel != _TODO_EL_HISTORIAL:
        mascara &= periodos == pd.Period(mes_sel, freq="M")
        texto_periodo = f"de {nombre_mes_es(mes_sel)}"
    else:
        texto_periodo = "de todo el historial"

    resultado = df[mascara]
    st.session_state[f"periodo_texto_{module_name}"] = texto_periodo
    aviso = f"{len(resultado):,} registros {texto_periodo}".replace(",", ".")
    if sin_fecha:
        aviso += f" · {sin_fecha} registros sin fecha válida no se incluyen (se ven en «Todo el historial»)"
    st.caption(aviso)
    return resultado
