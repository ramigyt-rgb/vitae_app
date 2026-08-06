# =========================================================
# VISTAS
# ========================================================
import pandas as pd
import plotly.express as px
import streamlit as st
import re
from pathlib import Path
from datetime import date, timedelta
from typing import Any, Dict
from config import APP_TITLE
from modules import MODULES
from database import *
from helpers import *
from farmacia_pro import render_farmacia_pro
from importers import render_importer
import agenda_quirofano_ultra_pro as _agenda_quirofano_modulo
from assistant import preguntar_ia
from assistant import preguntar_dashboard
from director_ia import (
    generar_resumen_ejecutivo,
    generar_briefing_automatico,
)


# =========================================================
# HOTFIX AGENDA QUIRÓFANO
# =========================================================
# Google Sheets puede devolver columnas numéricas (int/float) aunque luego
# la agenda necesite guardar texto como "Programado", "Sí" u observaciones.
# En versiones recientes de pandas, escribir un texto dentro de una columna
# numérica puede lanzar TypeError. Este parche convierte SOLO la columna que
# se está editando a tipo object antes de guardar el valor y también tolera
# encabezados repetidos sin modificar el resto de la aplicación.
def _agenda_asignar_escalar_seguro(
    dataframe: pd.DataFrame,
    posicion: int,
    columna: Any,
    valor: Any,
) -> None:
    try:
        fila_pos = int(posicion)
    except (TypeError, ValueError) as error:
        raise TypeError(f"Posición de fila inválida en Agenda Quirófano: {posicion!r}") from error

    if fila_pos < 0 or fila_pos >= len(dataframe):
        raise IndexError(
            f"Fila fuera de rango en Agenda Quirófano: {fila_pos} "
            f"(total de filas: {len(dataframe)})"
        )

    posiciones_columna = [
        indice
        for indice, nombre in enumerate(dataframe.columns)
        if nombre == columna
    ]
    if not posiciones_columna:
        return

    # El mapa de la agenda utiliza la primera coincidencia real del encabezado.
    columna_pos = posiciones_columna[0]

    # isetitem trabaja por posición y evita problemas cuando hay encabezados
    # duplicados. La conversión a object permite guardar texto, fechas, horas,
    # números o valores vacíos sin TypeError de pandas.
    serie_segura = dataframe.iloc[:, columna_pos].astype("object")
    dataframe.isetitem(columna_pos, serie_segura)
    dataframe.iat[fila_pos, columna_pos] = valor


def _agenda_actualizar_campo_seguro(
    original: pd.DataFrame,
    mapa: dict[str, str],
    posicion: int,
    campo: str,
    valor: Any,
) -> None:
    columna = mapa.get(campo)
    if columna is None:
        return
    _agenda_asignar_escalar_seguro(original, posicion, columna, valor)


def _agenda_actualizar_fila_segura(
    original: pd.DataFrame,
    mapa: dict[str, str],
    posicion: int,
    datos: dict[str, Any],
) -> pd.DataFrame:
    resultado = original.copy()

    for campo, valor in datos.items():
        if campo in mapa:
            _agenda_actualizar_campo_seguro(
                resultado,
                mapa,
                posicion,
                campo,
                valor,
            )

    if "updated_at" in resultado.columns:
        _agenda_asignar_escalar_seguro(
            resultado,
            posicion,
            "updated_at",
            pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    return resultado


# Reemplaza únicamente las dos rutinas internas que provocaban el TypeError.
# Todo el diseño, filtros, carga, edición y guardado del módulo se conserva.
_agenda_quirofano_modulo._actualizar_campo = _agenda_actualizar_campo_seguro
_agenda_quirofano_modulo._actualizar_fila = _agenda_actualizar_fila_segura
render_agenda_quirofano_ultra_pro = (
    _agenda_quirofano_modulo.render_agenda_quirofano_ultra_pro
)
from textwrap import dedent

# Módulo corporativo de convenios. Se registra aquí para que aparezca en el
# menú sin obligar a modificar modules.py ni tocar los demás módulos.
MODULES.setdefault(
    "Convenios",
    {
        "table": "convenios",
        "empresa": "VITAE",
        "tipo": "Gestión quirúrgica",
        "descripcion": (
            "Centro corporativo de nomencladores, valores, vigencias, reglas de "
            "facturación y padrón de prestadoras."
        ),
        "fields": [],
    },
)

def safe_panel(func_name, *args, **kwargs):
    func = globals().get(func_name)
    if callable(func):
        return func(*args, **kwargs)
    return None
@st.cache_data(ttl=300)
def load_all_data():
    data = {}
    for cfg in MODULES.values():
        tabla = cfg["table"]
        if tabla in data:
            continue
        if tabla == "convenios":
            # El módulo Convenios reúne varias pestañas y no necesita una hoja maestra.
            data[tabla] = pd.DataFrame()
            continue
        try:
            data[tabla] = add_balance_columns(get_df(tabla))
        except Exception:
            data[tabla] = pd.DataFrame()
    return data
def render_header() -> None:
    col1, col2 = st.columns([6.5, 1.2])
    with col1:
        st.markdown(
            '<div class="main-title">🏥 Sistema de Gestión | VITAE </div>',
            unsafe_allow_html=True
        )
        st.markdown(
            '<div class="subtitle">VMR · Vitae Medicina Reproductiva | VM · Vitae Medical</div>',
            unsafe_allow_html=True
        )
    with col2:
        logo_path = Path("logo_vitae.png")
        if logo_path.exists():
            st.markdown(
                """
                <style>
                .vitae-logo img {
                    width: 170px !important;
                    max-width: 170px !important;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )
            st.markdown('<div class="vitae-logo">', unsafe_allow_html=True)
            st.image(str(logo_path))
            st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.warning("Logo no encontrado")
DEFAULT_FACT_LABELS = {
    "mes": "Mes",
    "afiliado": "Paciente / Afiliado",
    "obra_social": "Obra social",
    "procedimiento": "Procedimiento",
    "medico_responsable": "Médico",
    "fecha_factura": "Fecha factura",
    "numero_factura": "N° factura",
    "vencimiento": "Vencimiento",
    "fecha_pago": "Fecha pago",
    "valor_pesos": "Valor facturado",
    "valor_usd": "Valor USD",
    "estado": "Estado",
    "observaciones": "Observaciones",
}
def sum_money_col(series):
    return pd.to_numeric(series.apply(money), errors="coerce").fillna(0).sum()
def deuda_mod(nombre, dfs):
    df = dfs.get(nombre, pd.DataFrame())
    if df.empty:
        return 0.0
    col_monto = next((c for c in ["saldo", "importe", "monto", "valor_pesos", "valor"] if c in df.columns), None)
    if not col_monto:
        return 0.0
    monto = df[col_monto].apply(money)
    if "pagado" in df.columns:
        pagado = df["pagado"].apply(money)
        return max(0.0, (monto - pagado).sum())
    if "estado" in df.columns:
        estados_deuda = ["pendiente", "a pagar", "adeudado", "deuda", "vencido"]
        mask = df["estado"].astype(str).str.lower().str.strip().isin(estados_deuda)
        return monto[mask].sum()
    return monto.sum()
def safe_col(df, col):

    if col not in df.columns:

        return pd.Series([""] * len(df), index=df.index)

    s = df.loc[:, col]

    if isinstance(s, pd.DataFrame):

        s = s.iloc[:, 0]

    return s.astype(str).apply(lambda x: x.strip())

# =========================================================
# DASHBOARD GLOBAL — CENTRO EJECUTIVO
# =========================================================

_DG_ESTADOS_CERRADOS = {
    "pagado", "cobrado", "completo", "completado", "realizado",
    "realizada", "finalizada", "finalizado", "cerrado", "cerrada",
    "cancelado", "cancelada", "anulado", "anulada",
}
_DG_ESTADOS_CANCELADOS = {
    "cancelado", "cancelada", "anulado", "anulada", "suspendido",
    "suspendida",
}
_DG_ESTADOS_PAGADOS = {
    "pagado", "cobrado", "completo", "completado", "realizado",
    "realizada", "finalizada", "finalizado", "cerrado", "cerrada",
}


def _dg_norm(value: Any) -> str:
    """Normaliza textos para clasificar módulos y estados sin depender de tildes."""
    text = str(value or "").strip().casefold()
    text = text.translate(str.maketrans({
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "ü": "u", "ñ": "n",
    }))
    text = text.replace("_", " ").replace("-", " ").replace("/", " ")
    return " ".join(text.split())


def _dg_series(df: pd.DataFrame, column: str, default: Any = "") -> pd.Series:
    if column not in df.columns:
        return pd.Series([default] * len(df), index=df.index)
    series = df.loc[:, column]
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    return series


def _dg_first_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    columns = {_dg_norm(col): col for col in df.columns}
    for candidate in candidates:
        found = columns.get(_dg_norm(candidate))
        if found is not None:
            return found
    return None


def _dg_text_series(df: pd.DataFrame, candidates: list[str]) -> pd.Series:
    column = _dg_first_column(df, candidates)
    if column is None:
        return pd.Series([""] * len(df), index=df.index, dtype="object")
    return _dg_series(df, column).fillna("").astype(str).str.strip()


def _dg_money_series(df: pd.DataFrame, candidates: list[str]) -> pd.Series:
    column = _dg_first_column(df, candidates)
    if column is None:
        return pd.Series([0.0] * len(df), index=df.index, dtype="float64")
    return pd.to_numeric(
        _dg_series(df, column).apply(money),
        errors="coerce",
    ).fillna(0.0)


def _dg_date_series(df: pd.DataFrame, candidates: list[str]) -> pd.Series:
    column = _dg_first_column(df, candidates)
    if column is None:
        return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    raw = _dg_series(df, column).replace("", pd.NA)
    try:
        parsed = pd.to_datetime(raw, errors="coerce", format="mixed", dayfirst=True)
    except (TypeError, ValueError):
        parsed = pd.to_datetime(raw, errors="coerce", dayfirst=True)
    try:
        if getattr(parsed.dt, "tz", None) is not None:
            parsed = parsed.dt.tz_localize(None)
    except Exception:
        pass
    return parsed


def _dg_period_mask(dates: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    normalized = dates.dt.normalize()
    return normalized.notna() & normalized.between(
        start.normalize(), end.normalize(), inclusive="both"
    )


def _dg_category(module_name: str, cfg: Dict[str, Any]) -> str:
    identity = _dg_norm(
        f"{module_name} {cfg.get('table', '')} {cfg.get('tipo', '')}"
    )
    detail = _dg_norm(cfg.get("descripcion", ""))
    if "farmacia" in identity:
        return "Excluir"
    if "convenio" in identity or "nomenclador" in identity:
        return "Convenios"
    if "cuenta corriente" in identity:
        return "Cuentas corrientes"
    if "deuda imposit" in identity:
        return "Impuestos"
    if "deuda total" in identity:
        return "Control de deuda"
    if "honorario" in identity:
        return "Honorarios"
    if "pago pendiente" in identity:
        return "Obligaciones"
    if "plan" in identity and ("prestamo" in identity or "pago" in identity):
        return "Financiación"
    if "caja" in identity or "banco" in identity:
        return "Tesorería"
    if "factur" in identity:
        return "Facturación"
    if "tarea" in identity:
        return "Tareas"
    if "contrato" in identity:
        return "Contratos"
    if "vencimiento" in identity:
        return "Vencimientos"
    if "alquiler" in identity:
        return "Alquileres"
    if "agenda" in identity or "quirof" in identity:
        return "Operaciones"
    if "gine" in identity:
        return "Unidad de negocio"
    if "gasto" in identity:
        return "Gastos"
    # Fallback sólo para configuraciones cuyo nombre y tabla no describen el área.
    if "factur" in detail:
        return "Facturación"
    if "vencimiento" in detail:
        return "Vencimientos"
    return "Gestión"

def _dg_company(module_name: str, cfg: Dict[str, Any]) -> str:
    company = str(cfg.get("empresa", "") or "").strip().upper()
    if company in {"VM", "VMR", "VITAE"}:
        return company
    text = f" {_dg_norm(module_name)} "
    if " vmr " in text or text.strip().endswith("vmr"):
        return "VMR"
    if " vm " in text or text.strip().endswith("vm"):
        return "VM"
    return "VITAE"


def _dg_primary_dates(df: pd.DataFrame, category: str) -> pd.Series:
    candidates = {
        "Facturación": ["mes", "fecha_factura", "fecha", "created_at"],
        "Tesorería": ["fecha", "mes", "created_at"],
        "Operaciones": [
            "fecha_cirugia", "fecha_procedimiento", "fecha", "dia", "mes",
            "created_at",
        ],
        "Tareas": ["vencimiento", "fecha_limite", "fecha", "created_at"],
        "Contratos": ["fecha_fin", "vencimiento", "hasta", "fecha", "created_at"],
        "Vencimientos": ["vencimiento", "fecha", "mes", "created_at"],
    }.get(
        category,
        ["fecha", "mes", "vencimiento", "fecha_factura", "created_at"],
    )
    return _dg_date_series(df, candidates)


def _dg_last_date(df: pd.DataFrame) -> Any:
    date_candidates = [
        "updated_at", "fecha_actualizacion", "fecha", "mes", "fecha_factura",
        "fecha_pago", "vencimiento", "fecha_fin", "created_at",
    ]
    found: list[pd.Timestamp] = []
    for candidate in date_candidates:
        if _dg_first_column(df, [candidate]) is None:
            continue
        series = _dg_date_series(df, [candidate]).dropna()
        if not series.empty:
            found.append(series.max())
    return max(found) if found else pd.NaT


def _dg_data_quality(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    ignored = {
        _dg_norm(value)
        for value in {
            "id", "created_at", "updated_at", "saldo_movimiento",
            "saldo_calculado", "_orden", "fila_origen",
        }
    }
    columns = [col for col in df.columns if _dg_norm(col) not in ignored]
    if not columns:
        return 100.0
    sample = df[columns].copy()

    def clean_value(value: Any) -> Any:
        try:
            missing = pd.isna(value)
            if isinstance(missing, bool) and missing:
                return pd.NA
        except Exception:
            pass
        return pd.NA if str(value).strip().casefold() in {"", "nan", "none", "nat"} else value

    for col in sample.columns:
        sample[col] = sample[col].apply(clean_value)
    return float(sample.notna().mean().mean() * 100)


def _dg_row_balance(df: pd.DataFrame, category: str) -> pd.Series:
    direct_balance = _dg_money_series(
        df,
        [
            "saldo", "saldo_pendiente", "saldo_actual", "saldo_ars",
            "saldo_capital", "saldo_deuda", "capital_pendiente",
            "pendiente", "deuda", "restante",
        ],
    )
    amount = _dg_money_series(
        df,
        [
            "importe", "importe_total", "monto", "monto_total", "valor_pesos",
            "valor", "total", "monto a pagar", "honorario_total", "honorario",
            "honorarios", "importe honorario", "monto honorario",
            "valor honorario", "a_pagar", "capital",
        ],
    )
    paid_candidates = [
        "pagado", "monto_pagado", "importe_pagado", "abonado",
        "honorario pagado", "pago acumulado", "total pagado",
    ]
    paid = _dg_money_series(df, paid_candidates)
    paid_col = _dg_first_column(df, paid_candidates)
    if paid_col is not None:
        paid_flag = _dg_series(df, paid_col).fillna("").astype(str).map(_dg_norm).isin(
            {"si", "true", "pagado", "cobrado", "completo", "completado"}
        )
        paid = paid.where(~paid_flag, amount)
    has_direct = any(
        _dg_first_column(df, [candidate]) is not None
        for candidate in [
            "saldo", "saldo_pendiente", "saldo_actual", "saldo_ars",
            "saldo_capital", "saldo_deuda", "capital_pendiente",
            "pendiente", "deuda", "restante",
        ]
    )
    balance = direct_balance if has_direct else (amount - paid).clip(lower=0)
    status = _dg_text_series(df, ["estado", "situacion", "status"]).map(_dg_norm)
    balance = balance.where(~status.isin(_DG_ESTADOS_CANCELADOS), 0.0)
    if not has_direct:
        balance = balance.where(~status.isin(_DG_ESTADOS_PAGADOS), 0.0)
    return balance.fillna(0.0).clip(lower=0)


def _dg_concept_series(df: pd.DataFrame) -> pd.Series:
    return _dg_text_series(
        df,
        [
            "proveedor", "entidad", "concepto", "descripcion", "detalle",
            "obra_social", "afiliado", "paciente", "nombre", "impuesto",
            "medico", "medico_responsable",
        ],
    ).replace("", "Sin descripción")


def _dg_patient_keys(df: pd.DataFrame) -> pd.Series:
    parts = []
    for candidates in [
        ["afiliado", "paciente", "nombre_paciente"],
        ["procedimiento", "practica"],
        ["medico_responsable", "medico"],
        ["mes", "fecha_factura", "fecha"],
    ]:
        text = _dg_text_series(df, candidates).map(_dg_norm)
        if text.ne("").any():
            parts.append(text)
    if not parts:
        return pd.Series(df.index.astype(str), index=df.index)
    key = parts[0]
    for part in parts[1:]:
        key = key + "|" + part
    return key


def _dg_financial_status(score: float) -> tuple[str, str]:
    if score >= 85:
        return "Sólido", "dg-status-good"
    if score >= 65:
        return "Controlado", "dg-status-watch"
    if score >= 45:
        return "En atención", "dg-status-alert"
    return "Crítico", "dg-status-bad"


def _dg_build_model(
    dfs: dict[str, pd.DataFrame],
    start: pd.Timestamp,
    end: pd.Timestamp,
    today: pd.Timestamp,
) -> dict[str, Any]:
    module_rows: list[dict[str, Any]] = []
    billing_frames: list[pd.DataFrame] = []
    treasury_rows: list[dict[str, Any]] = []
    obligation_frames: list[pd.DataFrame] = []
    due_frames: list[pd.DataFrame] = []
    task_frames: list[pd.DataFrame] = []
    contract_frames: list[pd.DataFrame] = []
    operation_frames: list[pd.DataFrame] = []
    convention_rows = 0
    convention_names: set[str] = set()
    convention_valued = 0
    convention_without_value = 0
    convention_without_validity = 0
    convention_duplicates = 0

    cash_income_period = 0.0
    cash_expense_period = 0.0
    account_receivables = 0.0
    account_payables = 0.0
    debt_control = 0.0
    registered_expenses_period = 0.0

    for module_name, cfg in MODULES.items():
        category = _dg_category(module_name, cfg)
        if category == "Excluir":
            continue
        company = _dg_company(module_name, cfg)
        df = dfs.get(module_name, pd.DataFrame()).copy()
        if df is None:
            df = pd.DataFrame()
        primary_dates = _dg_primary_dates(df, category) if not df.empty else pd.Series(dtype="datetime64[ns]")
        period_mask = (
            _dg_period_mask(primary_dates, start, end)
            if not df.empty
            else pd.Series(dtype="bool")
        )
        quality = _dg_data_quality(df)
        last_date = _dg_last_date(df) if not df.empty else pd.NaT
        if df.empty:
            coverage = "Sin datos"
        elif quality >= 85:
            coverage = "Alta"
        elif quality >= 65:
            coverage = "Media"
        else:
            coverage = "Baja"

        module_rows.append({
            "Módulo": module_name,
            "Empresa": company,
            "Área": category,
            "Registros": int(len(df)),
            "En período": int(period_mask.sum()) if len(period_mask) else 0,
            "Último dato": last_date,
            "Calidad %": round(quality, 1),
            "Cobertura": coverage,
        })
        if df.empty:
            continue

        status_text = _dg_text_series(df, ["estado", "situacion", "status"])
        status_norm = status_text.map(_dg_norm)

        if category == "Facturación":
            invoice_date = _dg_date_series(df, ["mes", "fecha_factura", "fecha", "created_at"])
            payment_date = _dg_date_series(df, ["fecha_pago", "fecha_cobro", "cobrado_el"])
            due_date = _dg_date_series(df, ["vencimiento", "fecha_vencimiento"])
            amount = _dg_money_series(
                df,
                ["valor_pesos", "importe", "monto", "valor", "total"],
            ).clip(lower=0)
            paid_numeric = _dg_money_series(
                df,
                ["importe_cobrado", "monto_cobrado", "cobrado", "pagado"],
            ).clip(lower=0)
            has_paid_numeric = any(
                _dg_first_column(df, [candidate]) is not None
                for candidate in ["importe_cobrado", "monto_cobrado", "cobrado", "pagado"]
            ) and paid_numeric.gt(0).any()
            paid = paid_numeric.clip(upper=amount) if has_paid_numeric else pd.Series(0.0, index=df.index)
            paid_col = _dg_first_column(
                df,
                ["importe_cobrado", "monto_cobrado", "cobrado", "pagado"],
            )
            if paid_col is not None:
                paid_flag = _dg_series(df, paid_col).fillna("").astype(str).map(_dg_norm).isin(
                    {"si", "true", "pagado", "cobrado", "completo", "completado"}
                )
                paid = paid.where(~paid_flag, amount)
            paid = paid.where(~status_norm.isin(_DG_ESTADOS_PAGADOS), amount)
            paid = paid.where(payment_date.isna() | paid.gt(0), amount)
            paid = paid.where(~status_norm.isin(_DG_ESTADOS_CANCELADOS), 0.0)
            amount = amount.where(~status_norm.isin(_DG_ESTADOS_CANCELADOS), 0.0)
            pending = (amount - paid).clip(lower=0)

            effective_payment_date = payment_date.where(payment_date.notna(), invoice_date)
            payer = _dg_text_series(df, ["obra_social", "financiador", "cliente", "convenio"])
            procedure = _dg_text_series(df, ["procedimiento", "practica", "prestacion"])
            doctor = _dg_text_series(df, ["medico_responsable", "medico", "profesional"])
            patient = _dg_text_series(df, ["afiliado", "paciente", "nombre_paciente"])
            patient_key = _dg_patient_keys(df)

            billing_frames.append(pd.DataFrame({
                "Módulo": module_name,
                "Empresa": company,
                "Fecha factura": invoice_date,
                "Fecha cobro": effective_payment_date,
                "Vencimiento": due_date,
                "Monto": amount,
                "Cobrado": paid,
                "Pendiente": pending,
                "Estado": status_text,
                "Obra social": payer.replace("", "Sin especificar"),
                "Procedimiento": procedure.replace("", "Sin especificar"),
                "Médico": doctor.replace("", "Sin especificar"),
                "Paciente": patient.replace("", "Sin especificar"),
                "Clave paciente": patient_key,
            }))

        elif category == "Tesorería":
            movement_date = _dg_date_series(df, ["fecha", "mes", "created_at"])
            income = _dg_money_series(df, ["ingreso", "ingresos", "credito", "haber"])
            expense = _dg_money_series(df, ["egreso", "egresos", "debito", "debe"])
            movement_mask = _dg_period_mask(movement_date, start, end)
            cash_income_period += float(income.loc[movement_mask].sum())
            cash_expense_period += float(expense.loc[movement_mask].sum())

            balance_col = _dg_first_column(
                df,
                ["saldo_movimiento", "saldo_actual", "saldo", "balance"],
            )
            if balance_col is not None:
                balance_series = pd.to_numeric(
                    _dg_series(df, balance_col).apply(money),
                    errors="coerce",
                )
                valid = balance_series.notna()
                if movement_date.notna().any():
                    order = movement_date.where(valid).sort_values().index
                    balance = float(balance_series.loc[order].dropna().iloc[-1]) if valid.any() else 0.0
                else:
                    balance = float(balance_series.dropna().iloc[-1]) if valid.any() else 0.0
            else:
                balance = float(income.sum() - expense.sum())
            treasury_rows.append({
                "Módulo": module_name,
                "Empresa": company,
                "Disponible": balance,
                "Ingresos período": float(income.loc[movement_mask].sum()),
                "Egresos período": float(expense.loc[movement_mask].sum()),
            })

        elif category == "Cuentas corrientes":
            balance = _dg_row_balance(df, category)
            type_norm = _dg_text_series(df, ["tipo", "naturaleza", "clase"]).map(_dg_norm)
            receivable_mask = type_norm.str.contains("cobrar|cliente|favor", regex=True, na=False)
            account_receivables += float(balance.loc[receivable_mask].sum())
            account_payables += float(balance.loc[~receivable_mask].sum())
            obligation_frames.append(pd.DataFrame({
                "Módulo": module_name,
                "Empresa": company,
                "Área": category,
                "Concepto": _dg_concept_series(df),
                "Saldo": balance.where(~receivable_mask, 0.0),
                "A cobrar": balance.where(receivable_mask, 0.0),
                "Vencimiento": _dg_date_series(df, ["vencimiento", "fecha_vencimiento"]),
                "Estado": status_text,
            }))

        elif category in {
            "Impuestos", "Honorarios", "Obligaciones", "Financiación",
        }:
            balance = _dg_row_balance(df, category)
            amount = _dg_money_series(
                df,
                [
                    "importe", "importe_total", "monto", "monto_total",
                    "valor_pesos", "valor", "total", "monto a pagar",
                    "honorario_total", "honorario", "honorarios",
                    "importe honorario", "monto honorario", "valor honorario",
                    "a_pagar", "capital",
                ],
            )
            due_date = _dg_date_series(
                df,
                ["vencimiento", "fecha_vencimiento", "fecha_pago", "fecha_limite"],
            )
            obligation_frames.append(pd.DataFrame({
                "Módulo": module_name,
                "Empresa": company,
                "Área": category,
                "Concepto": _dg_concept_series(df),
                "Saldo": balance,
                "A cobrar": 0.0,
                "Vencimiento": due_date,
                "Estado": status_text,
            }))

        elif category == "Gastos":
            expense_amount = _dg_money_series(
                df,
                ["importe", "importe_total", "monto", "monto_total", "valor_pesos", "valor", "total"],
            )
            expense_dates = _dg_primary_dates(df, category)
            registered_expenses_period += float(
                expense_amount.loc[_dg_period_mask(expense_dates, start, end)].sum()
            )
            # Sólo se considera obligación si la planilla posee un saldo explícito.
            if _dg_first_column(
                df,
                ["saldo", "saldo_pendiente", "saldo_actual", "pendiente"],
            ) is not None:
                balance = _dg_row_balance(df, category)
                obligation_frames.append(pd.DataFrame({
                    "Módulo": module_name,
                    "Empresa": company,
                    "Área": category,
                    "Concepto": _dg_concept_series(df),
                    "Saldo": balance,
                    "A cobrar": 0.0,
                    "Vencimiento": _dg_date_series(df, ["vencimiento", "fecha_vencimiento"]),
                    "Estado": status_text,
                }))

        elif category == "Control de deuda":
            debt_control += float(_dg_row_balance(df, category).sum())

        elif category == "Tareas":
            due_date = _dg_date_series(df, ["vencimiento", "fecha_limite", "fecha"])
            closed = status_norm.isin(_DG_ESTADOS_CERRADOS)
            task_frames.append(pd.DataFrame({
                "Módulo": module_name,
                "Empresa": company,
                "Tarea": _dg_concept_series(df),
                "Vencimiento": due_date,
                "Estado": status_text.replace("", "Pendiente"),
                "Prioridad": _dg_text_series(df, ["prioridad", "urgencia"]).replace("", "Normal"),
                "Abierta": ~closed,
            }))

        elif category == "Contratos":
            end_date = _dg_date_series(df, ["fecha_fin", "vencimiento", "hasta", "fin"])
            closed = status_norm.isin(_DG_ESTADOS_CERRADOS)
            contract_frames.append(pd.DataFrame({
                "Módulo": module_name,
                "Empresa": company,
                "Contrato": _dg_concept_series(df),
                "Vencimiento": end_date,
                "Estado": status_text.replace("", "Vigente"),
                "Activo": ~closed,
            }))

        elif category == "Convenios":
            convenio = _dg_text_series(df, ["convenio", "obra_social", "prestadora"])
            convention_names.update(convenio[convenio.ne("")].tolist())
            code = _dg_text_series(df, ["codigo", "código", "cod_practica"])
            record_type = _dg_text_series(df, ["tipo_registro", "tipo"]).map(_dg_norm)
            practice_mask = code.ne("") | ~record_type.str.contains("directorio", na=False)
            convention_rows += int(practice_mask.sum())
            value_col = _dg_first_column(df, ["valor", "valor_pesos", "arancel"])
            if value_col is not None:
                values = pd.to_numeric(_dg_series(df, value_col).apply(money), errors="coerce")
                convention_valued += int((values.fillna(0).gt(0) & practice_mask).sum())
                convention_without_value += int((values.fillna(0).le(0) & practice_mask).sum())
            else:
                convention_without_value += int(practice_mask.sum())
            validity = _dg_date_series(df, ["vigencia", "fecha_vigencia", "actualizado"] )
            convention_without_validity += int((validity.isna() & practice_mask).sum())
            if code.ne("").any():
                duplicate_base = pd.DataFrame({"Convenio": convenio, "Código": code})
                valid_codes = duplicate_base[duplicate_base["Código"].ne("")]
                convention_duplicates += int(valid_codes.duplicated(["Convenio", "Código"], keep=False).sum())

        elif category == "Operaciones":
            operation_date = _dg_primary_dates(df, category)
            operation_frames.append(pd.DataFrame({
                "Módulo": module_name,
                "Empresa": company,
                "Fecha": operation_date,
                "Estado": status_text.replace("", "Sin estado"),
                "Estado normalizado": status_norm,
                "Procedimiento": _dg_text_series(
                    df,
                    ["procedimiento", "practica", "cirugia", "tipo_procedimiento"],
                ).replace("", "Sin especificar"),
                "Médico": _dg_text_series(
                    df,
                    ["medico", "medico_responsable", "cirujano"],
                ).replace("", "Sin especificar"),
                "Paciente": _dg_text_series(
                    df,
                    ["paciente", "afiliado", "nombre_paciente"],
                ).replace("", "Sin especificar"),
            }))

        # Agenda común de vencimientos para todo módulo financiero.
        if category not in {"Facturación", "Tesorería", "Tareas", "Contratos", "Operaciones"}:
            due_date = _dg_date_series(df, ["vencimiento", "fecha_vencimiento", "fecha_limite"])
            if due_date.notna().any():
                balance = _dg_row_balance(df, category)
                nature = pd.Series("A pagar", index=df.index, dtype="object")
                if category == "Cuentas corrientes":
                    type_due = _dg_text_series(df, ["tipo", "naturaleza", "clase"]).map(_dg_norm)
                    nature = nature.where(
                        ~type_due.str.contains("cobrar|cliente|favor", regex=True, na=False),
                        "A cobrar",
                    )
                elif category == "Vencimientos":
                    nature = pd.Series("Control", index=df.index, dtype="object")
                due_frames.append(pd.DataFrame({
                    "Módulo": module_name,
                    "Empresa": company,
                    "Naturaleza": nature,
                    "Concepto": _dg_concept_series(df),
                    "Vencimiento": due_date,
                    "Saldo": balance,
                    "Estado": status_text,
                }))

    modules = pd.DataFrame(module_rows)
    billing = pd.concat(billing_frames, ignore_index=True) if billing_frames else pd.DataFrame()
    treasury = pd.DataFrame(treasury_rows)
    obligations = pd.concat(obligation_frames, ignore_index=True) if obligation_frames else pd.DataFrame()
    dues = pd.concat(due_frames, ignore_index=True) if due_frames else pd.DataFrame()
    tasks = pd.concat(task_frames, ignore_index=True) if task_frames else pd.DataFrame()
    contracts = pd.concat(contract_frames, ignore_index=True) if contract_frames else pd.DataFrame()
    operations = pd.concat(operation_frames, ignore_index=True) if operation_frames else pd.DataFrame()

    if not billing.empty:
        invoice_mask = _dg_period_mask(billing["Fecha factura"], start, end)
        collection_mask = _dg_period_mask(billing["Fecha cobro"], start, end) & billing["Cobrado"].gt(0)
        billed_period = float(billing.loc[invoice_mask, "Monto"].sum())
        collected_period = float(billing.loc[collection_mask, "Cobrado"].sum())
        billing_pending = float(billing["Pendiente"].sum())
        patients_period = int(billing.loc[invoice_mask, "Clave paciente"].nunique())
        doctors_period = int(
            billing.loc[invoice_mask & billing["Médico"].ne("Sin especificar"), "Médico"].nunique()
        )
        procedures_period = int(
            billing.loc[invoice_mask & billing["Procedimiento"].ne("Sin especificar"), "Procedimiento"].nunique()
        )
    else:
        invoice_mask = pd.Series(dtype="bool")
        collection_mask = pd.Series(dtype="bool")
        billed_period = collected_period = billing_pending = 0.0
        patients_period = doctors_period = procedures_period = 0

    liquidity = float(treasury["Disponible"].sum()) if not treasury.empty else 0.0
    obligation_balance = float(obligations["Saldo"].sum()) if not obligations.empty else 0.0
    # Las cuentas corrientes a pagar ya están incluidas en obligations["Saldo"].
    # No se vuelven a sumar para evitar duplicar la deuda.
    total_payables = obligation_balance
    total_receivables = billing_pending + account_receivables

    if not billing.empty:
        pending_billing = billing[billing["Pendiente"].gt(0)].copy()
        pending_billing["Días vencido"] = (today - pending_billing["Vencimiento"]).dt.days
        pending_billing["Vencido"] = (
            pending_billing["Vencimiento"].notna()
            & pending_billing["Vencimiento"].lt(today)
        )
    else:
        pending_billing = pd.DataFrame()

    if not dues.empty:
        active_dues = dues[dues["Saldo"].gt(0)].copy()
    else:
        active_dues = pd.DataFrame(columns=["Vencimiento", "Saldo"])

    overdue_due = active_dues[
        active_dues["Vencimiento"].notna() & active_dues["Vencimiento"].lt(today)
    ].copy() if not active_dues.empty else active_dues.copy()
    upcoming_due = active_dues[
        active_dues["Vencimiento"].between(today, today + pd.Timedelta(days=30), inclusive="both")
    ].copy() if not active_dues.empty else active_dues.copy()

    overdue_billing = pending_billing[pending_billing.get("Vencido", False)].copy() if not pending_billing.empty else pending_billing.copy()
    overdue_amount = float(overdue_due["Saldo"].sum()) + (
        float(overdue_billing["Pendiente"].sum()) if not overdue_billing.empty else 0.0
    )
    overdue_count = int(len(overdue_due) + len(overdue_billing))
    upcoming_amount = float(upcoming_due["Saldo"].sum()) if not upcoming_due.empty else 0.0

    open_tasks = tasks[tasks["Abierta"]].copy() if not tasks.empty else tasks.copy()
    overdue_tasks = open_tasks[
        open_tasks["Vencimiento"].notna() & open_tasks["Vencimiento"].lt(today)
    ].copy() if not open_tasks.empty else open_tasks.copy()
    expiring_contracts = contracts[
        contracts["Activo"]
        & contracts["Vencimiento"].between(today, today + pd.Timedelta(days=60), inclusive="both")
    ].copy() if not contracts.empty else contracts.copy()

    if not operations.empty:
        operation_mask = _dg_period_mask(operations["Fecha"], start, end)
        operations_period = operations.loc[operation_mask].copy()
    else:
        operations_period = operations.copy()

    collection_rate = (collected_period / billed_period * 100) if billed_period > 0 else 0.0
    coverage_ratio = (liquidity / total_payables) if total_payables > 0 else None
    cash_flow = cash_income_period - cash_expense_period
    ticket = billed_period / patients_period if patients_period else 0.0
    quality_average = float(modules["Calidad %"].mean()) if not modules.empty else 0.0

    return {
        "modules": modules,
        "billing": billing,
        "treasury": treasury,
        "obligations": obligations,
        "dues": dues,
        "tasks": tasks,
        "contracts": contracts,
        "operations": operations,
        "operations_period": operations_period,
        "pending_billing": pending_billing,
        "overdue_billing": overdue_billing,
        "overdue_due": overdue_due,
        "upcoming_due": upcoming_due,
        "open_tasks": open_tasks,
        "overdue_tasks": overdue_tasks,
        "expiring_contracts": expiring_contracts,
        "billed_period": billed_period,
        "collected_period": collected_period,
        "billing_pending": billing_pending,
        "account_receivables": account_receivables,
        "account_payables": account_payables,
        "total_receivables": total_receivables,
        "total_payables": total_payables,
        "debt_control": debt_control,
        "liquidity": liquidity,
        "cash_income_period": cash_income_period,
        "cash_expense_period": cash_expense_period,
        "registered_expenses_period": registered_expenses_period,
        "cash_flow": cash_flow,
        "patients_period": patients_period,
        "doctors_period": doctors_period,
        "procedures_period": procedures_period,
        "ticket": ticket,
        "collection_rate": collection_rate,
        "coverage_ratio": coverage_ratio,
        "overdue_amount": overdue_amount,
        "overdue_count": overdue_count,
        "upcoming_amount": upcoming_amount,
        "quality_average": quality_average,
        "convention_rows": convention_rows,
        "convention_count": len(convention_names),
        "convention_valued": convention_valued,
        "convention_without_value": convention_without_value,
        "convention_without_validity": convention_without_validity,
        "convention_duplicates": convention_duplicates,
    }


def _dg_render_css() -> None:
    st.markdown(
        """
        <style>
        .dg-hero {
            border: 1px solid rgba(49, 58, 79, .12);
            border-radius: 22px;
            padding: 1.25rem 1.4rem;
            margin: .15rem 0 1rem 0;
            background: linear-gradient(135deg, rgba(248,250,253,.98), rgba(237,241,247,.96));
            box-shadow: 0 12px 34px rgba(24, 32, 48, .08);
        }
        .dg-kicker {font-size:.72rem; letter-spacing:.14em; text-transform:uppercase; color:#697386; font-weight:800;}
        .dg-title {font-size:2rem; line-height:1.08; color:#202633; font-weight:850; margin:.2rem 0 .35rem 0;}
        .dg-subtitle {font-size:.94rem; color:#667085; max-width:920px;}
        .dg-period {display:inline-block; margin-top:.75rem; padding:.35rem .65rem; border-radius:999px; background:#fff; border:1px solid rgba(49,58,79,.12); color:#344054; font-size:.8rem; font-weight:700;}
        .dg-section-title {font-size:1.08rem; font-weight:820; color:#242b38; margin:.3rem 0 .15rem 0;}
        .dg-section-copy {font-size:.84rem; color:#737b8c; margin-bottom:.75rem;}
        .dg-callout {border-radius:16px; padding:.9rem 1rem; border:1px solid rgba(49,58,79,.1); background:#fafbfc; margin:.35rem 0; min-height:92px;}
        .dg-callout strong {display:block; color:#2b3240; font-size:.88rem; margin-bottom:.18rem;}
        .dg-callout span {color:#697386; font-size:.82rem; line-height:1.35;}
        .dg-status-good {background:#eaf7ef; color:#176b3a;}
        .dg-status-watch {background:#fff7df; color:#775d00;}
        .dg-status-alert {background:#fff0df; color:#965000;}
        .dg-status-bad {background:#fdeaea; color:#a12626;}
        .dg-pill {display:inline-block; border-radius:999px; padding:.34rem .62rem; font-weight:800; font-size:.78rem;}
        div[data-testid="stMetric"] {
            border:1px solid rgba(49,58,79,.11);
            border-radius:17px;
            padding:.78rem .9rem;
            background:rgba(255,255,255,.92);
            box-shadow:0 7px 18px rgba(22,31,49,.045);
            min-height:116px;
        }
        div[data-testid="stMetricLabel"] {font-weight:700; color:#697386;}
        div[data-testid="stMetricValue"] {font-weight:820; color:#242b38; font-variant-numeric:tabular-nums;}
        div[data-testid="stTabs"] button {font-weight:750;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _dg_render_company_cards(model: dict[str, Any], start: pd.Timestamp, end: pd.Timestamp) -> None:
    billing = model["billing"]
    treasury = model["treasury"]
    companies = ["VMR", "VM"]
    columns = st.columns(2)
    for column, company in zip(columns, companies):
        with column:
            company_billing = billing[billing["Empresa"].eq(company)].copy() if not billing.empty else pd.DataFrame()
            if not company_billing.empty:
                invoice_mask = _dg_period_mask(company_billing["Fecha factura"], start, end)
                collection_mask = _dg_period_mask(company_billing["Fecha cobro"], start, end) & company_billing["Cobrado"].gt(0)
                billed = float(company_billing.loc[invoice_mask, "Monto"].sum())
                collected = float(company_billing.loc[collection_mask, "Cobrado"].sum())
                pending = float(company_billing["Pendiente"].sum())
                patients = int(company_billing.loc[invoice_mask, "Clave paciente"].nunique())
            else:
                billed = collected = pending = 0.0
                patients = 0
            available = (
                float(treasury.loc[treasury["Empresa"].eq(company), "Disponible"].sum())
                if not treasury.empty else 0.0
            )
            rate = (collected / billed * 100) if billed > 0 else 0.0
            st.markdown(f"#### {company}")
            a, b = st.columns(2)
            a.metric("Facturado", fmt_money(billed))
            b.metric("Cobrado", fmt_money(collected), delta=f"{rate:.1f}% sobre facturado")
            c, d = st.columns(2)
            c.metric("Pendiente", fmt_money(pending))
            d.metric("Pacientes", patients)
            st.caption(f"Disponibilidad registrada: {fmt_money(available)}")


def _dg_render_alerts(model: dict[str, Any]) -> None:
    alerts: list[tuple[str, str]] = []
    strengths: list[str] = []

    if model["overdue_count"] > 0:
        alerts.append((
            "error",
            f"Hay {model['overdue_count']} compromisos o saldos vencidos por {fmt_money(model['overdue_amount'])}.",
        ))
    else:
        strengths.append("No se detectaron compromisos vencidos con saldo en la información disponible.")

    if model["billed_period"] > 0 and model["collection_rate"] < 75:
        alerts.append((
            "warning",
            f"La cobranza del período equivale al {model['collection_rate']:.1f}% de lo facturado; requiere seguimiento de recupero.",
        ))
    elif model["billed_period"] > 0:
        strengths.append(f"La relación cobrado/facturado del período alcanza {model['collection_rate']:.1f}%.")

    coverage = model["coverage_ratio"]
    if coverage is not None and coverage < 1:
        alerts.append((
            "error",
            f"La liquidez cubre {coverage:.2f} veces las obligaciones registradas; existe una brecha de cobertura.",
        ))
    elif coverage is not None:
        strengths.append(f"La liquidez cubre {coverage:.2f} veces las obligaciones registradas.")

    if len(model["overdue_tasks"]) > 0:
        alerts.append(("warning", f"Hay {len(model['overdue_tasks'])} tareas vencidas todavía abiertas."))
    if len(model["expiring_contracts"]) > 0:
        alerts.append(("warning", f"Hay {len(model['expiring_contracts'])} contratos con vencimiento dentro de 60 días."))

    if model.get("convention_without_value", 0) > 0:
        alerts.append((
            "info",
            f"Convenios registra {model['convention_without_value']} prácticas sin valor cargado.",
        ))
    if model.get("convention_duplicates", 0) > 0:
        alerts.append((
            "warning",
            f"Se detectaron {model['convention_duplicates']} registros duplicados por convenio y código.",
        ))

    if model.get("debt_control", 0) > 0 and model.get("total_payables", 0) > 0:
        debt_difference = model["debt_control"] - model["total_payables"]
        tolerance = max(model["debt_control"], model["total_payables"]) * 0.05
        if abs(debt_difference) > tolerance:
            direction = "por encima" if debt_difference > 0 else "por debajo"
            alerts.append((
                "info",
                f"El control Deuda total está {fmt_money(abs(debt_difference))} {direction} "
                "de la suma de obligaciones del tablero; conviene conciliar los componentes.",
            ))

    modules_without_data = int((model["modules"]["Registros"] == 0).sum()) if not model["modules"].empty else 0
    if modules_without_data:
        alerts.append(("info", f"Hay {modules_without_data} módulos sin registros visibles en la fuente estándar del tablero."))

    billing = model["billing"]
    if not billing.empty:
        current_mask = billing["Monto"].gt(0)
        payer = billing.loc[current_mask].groupby("Obra social")["Monto"].sum().sort_values(ascending=False)
        if payer.sum() > 0 and not payer.empty:
            concentration = payer.iloc[0] / payer.sum() * 100
            if concentration >= 40:
                alerts.append((
                    "warning",
                    f"La principal obra social concentra {concentration:.1f}% de la facturación histórica registrada.",
                ))

    if not alerts:
        st.success("No se detectaron alertas ejecutivas con los criterios actuales.")
    else:
        for severity, message in alerts:
            getattr(st, severity)(message)

    if strengths:
        with st.expander("Fortalezas detectadas", expanded=False):
            for item in strengths:
                st.markdown(f"- {item}")


def _dg_render_priority_actions(model: dict[str, Any]) -> None:
    actions: list[tuple[str, str]] = []
    if model["overdue_amount"] > 0:
        actions.append((
            "Recupero y regularización",
            f"Ordenar los vencidos por monto y fecha. Prioridad económica actual: {fmt_money(model['overdue_amount'])}.",
        ))
    if model["coverage_ratio"] is not None and model["coverage_ratio"] < 1:
        gap = max(model["total_payables"] - model["liquidity"], 0)
        actions.append((
            "Cobertura financiera",
            f"Definir fuentes y fechas para cubrir una brecha estimada de {fmt_money(gap)}.",
        ))
    if len(model["overdue_tasks"]) > 0:
        actions.append((
            "Ejecución operativa",
            f"Asignar responsable y fecha de cierre a {len(model['overdue_tasks'])} tareas vencidas.",
        ))
    if len(model["expiring_contracts"]) > 0:
        actions.append((
            "Continuidad contractual",
            f"Revisar renovación, condiciones y responsables de {len(model['expiring_contracts'])} contratos próximos a vencer.",
        ))
    low_quality = model["modules"][model["modules"]["Cobertura"].isin(["Baja", "Sin datos"])] if not model["modules"].empty else pd.DataFrame()
    if not low_quality.empty:
        actions.append((
            "Calidad de información",
            f"Completar o normalizar {len(low_quality)} módulos para mejorar la confiabilidad de la lectura directiva.",
        ))
    if not actions:
        actions.append((
            "Consolidación",
            "Mantener el seguimiento mensual y validar que cobranzas, obligaciones y vencimientos permanezcan actualizados.",
        ))

    columns = st.columns(min(3, len(actions)))
    for index, (title, detail) in enumerate(actions[:6]):
        with columns[index % len(columns)]:
            st.markdown(
                f'<div class="dg-callout"><strong>{index + 1}. {title}</strong><span>{detail}</span></div>',
                unsafe_allow_html=True,
            )


def _dg_render_overview(model: dict[str, Any], start: pd.Timestamp, end: pd.Timestamp) -> None:
    st.markdown('<div class="dg-section-title">Lectura ejecutiva</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="dg-section-copy">Indicadores consolidados para decidir sobre liquidez, actividad, cobranza, deuda y ejecución.</div>',
        unsafe_allow_html=True,
    )

    collection_component = min(max(model["collection_rate"], 0), 100) if model["billed_period"] > 0 else 70
    coverage_component = min(max((model["coverage_ratio"] or 0) * 100, 0), 100) if model["coverage_ratio"] is not None else 70
    overdue_component = max(0, 100 - min(model["overdue_count"] * 7, 100))
    quality_component = min(max(model["quality_average"], 0), 100)
    executive_score = (
        collection_component * .30
        + coverage_component * .30
        + overdue_component * .20
        + quality_component * .20
    )
    label, css_class = _dg_financial_status(executive_score)
    st.markdown(
        f'<span class="dg-pill {css_class}">Semáforo ejecutivo: {label} · {executive_score:.0f}/100</span>',
        unsafe_allow_html=True,
    )
    st.caption("Índice orientativo: cobranza 30%, cobertura de obligaciones 30%, vencidos 20% y calidad de datos 20%.")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Liquidez disponible", fmt_money(model["liquidity"]))
    k2.metric("Facturado en período", fmt_money(model["billed_period"]))
    k3.metric(
        "Cobrado en período",
        fmt_money(model["collected_period"]),
        delta=f"{model['collection_rate']:.1f}% de lo facturado" if model["billed_period"] > 0 else None,
    )
    k4.metric("Flujo neto de fondos", fmt_money(model["cash_flow"]))

    k5, k6, k7, k8 = st.columns(4)
    k5.metric("Pendiente de cobro", fmt_money(model["total_receivables"]))
    k6.metric("Obligaciones registradas", fmt_money(model["total_payables"]))
    k7.metric("Vencido", fmt_money(model["overdue_amount"]), delta=f"{model['overdue_count']} registros")
    coverage_text = f"{model['coverage_ratio']:.2f}x" if model["coverage_ratio"] is not None else "Sin deuda"
    k8.metric("Cobertura de obligaciones", coverage_text)

    k9, k10, k11, k12 = st.columns(4)
    k9.metric("Pacientes del período", model["patients_period"])
    k10.metric("Ticket promedio", fmt_money(model["ticket"]))
    k11.metric("Médicos activos", model["doctors_period"])
    k12.metric("Procedimientos activos", model["procedures_period"])

    st.divider()
    st.markdown("### Resumen por empresa")
    _dg_render_company_cards(model, start, end)

    st.divider()
    left, right = st.columns([1.25, 1])
    with left:
        st.markdown("### Evolución de facturación y cobranza")
        billing = model["billing"]
        if billing.empty:
            st.info("No hay registros de facturación para construir la evolución.")
        else:
            chart_start = (end - pd.DateOffset(months=11)).replace(day=1)
            months = pd.period_range(chart_start.to_period("M"), end.to_period("M"), freq="M")
            fact = (
                billing[_dg_period_mask(billing["Fecha factura"], chart_start, end)]
                .assign(Mes=lambda x: x["Fecha factura"].dt.to_period("M").astype(str))
                .groupby("Mes", as_index=False)["Monto"].sum()
                .rename(columns={"Monto": "Facturado"})
            )
            coll = (
                billing[_dg_period_mask(billing["Fecha cobro"], chart_start, end)]
                .assign(Mes=lambda x: x["Fecha cobro"].dt.to_period("M").astype(str))
                .groupby("Mes", as_index=False)["Cobrado"].sum()
            )
            monthly = pd.DataFrame({"Mes": months.astype(str)}).merge(fact, on="Mes", how="left").merge(coll, on="Mes", how="left").fillna(0)
            long = monthly.melt("Mes", var_name="Indicador", value_name="Monto")
            fig = px.line(long, x="Mes", y="Monto", color="Indicador", markers=True)
            fig.update_layout(height=360, margin=dict(l=10, r=10, t=20, b=10), legend_title_text="")
            st.plotly_chart(fig, use_container_width=True)
    with right:
        st.markdown("### Composición de disponibilidad")
        treasury = model["treasury"]
        if treasury.empty or treasury["Disponible"].abs().sum() == 0:
            st.info("No hay saldos de caja o bancos disponibles.")
        else:
            fig = px.bar(
                treasury.sort_values("Disponible"),
                x="Disponible",
                y="Módulo",
                color="Empresa",
                orientation="h",
                text_auto=".2s",
            )
            fig.update_layout(height=360, margin=dict(l=10, r=10, t=20, b=10), legend_title_text="")
            st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.markdown("### Alertas y decisiones prioritarias")
    alert_col, decision_col = st.columns([1, 1.2])
    with alert_col:
        _dg_render_alerts(model)
    with decision_col:
        _dg_render_priority_actions(model)


def _dg_render_finance(model: dict[str, Any], start: pd.Timestamp, end: pd.Timestamp, today: pd.Timestamp) -> None:
    st.markdown("### Finanzas, cobranza y obligaciones")
    st.caption("Lectura consolidada sin sumar el módulo de control de deuda para evitar duplicaciones.")

    f1, f2, f3, f4 = st.columns(4)
    f1.metric("Ingresos de fondos", fmt_money(model["cash_income_period"]))
    f2.metric("Egresos de fondos", fmt_money(model["cash_expense_period"]))
    f3.metric("Cuentas por cobrar", fmt_money(model["account_receivables"]))
    f4.metric("Cuentas por pagar", fmt_money(model["account_payables"]))
    if model.get("registered_expenses_period", 0) > 0:
        st.caption(
            f"Gastos comunes registrados en el período: "
            f"{fmt_money(model['registered_expenses_period'])}. "
            "Se informan por separado para no duplicar egresos ya asentados en caja o bancos."
        )

    if model["debt_control"] > 0:
        st.info(
            f"El módulo Deuda total informa {fmt_money(model['debt_control'])}. "
            "Se presenta como control independiente y no se suma nuevamente a las obligaciones."
        )

    left, right = st.columns(2)
    with left:
        st.markdown("#### Obligaciones por área")
        obligations = model["obligations"]
        if obligations.empty or obligations["Saldo"].sum() <= 0:
            st.success("No hay obligaciones con saldo en los módulos analizados.")
        else:
            grouped = obligations.groupby("Área", as_index=False)["Saldo"].sum()
            if model.get("account_payables", 0) > 0:
                grouped = pd.concat([
                    grouped,
                    pd.DataFrame([{
                        "Área": "Cuentas corrientes",
                        "Saldo": model["account_payables"],
                    }]),
                ], ignore_index=True)
                grouped = grouped.groupby("Área", as_index=False)["Saldo"].sum()
            grouped = grouped.sort_values("Saldo", ascending=False)
            fig = px.bar(grouped, x="Área", y="Saldo", text_auto=".2s")
            fig.update_layout(height=350, margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(fig, use_container_width=True)
    with right:
        st.markdown("#### Antigüedad de saldos a cobrar")
        pending = model["pending_billing"].copy()
        if pending.empty:
            st.success("No hay saldos de facturación pendientes.")
        else:
            days = (today - pending["Vencimiento"]).dt.days
            pending["Antigüedad"] = pd.cut(
                days,
                bins=[-10**9, 0, 30, 60, 90, 10**9],
                labels=["No vencido", "1-30 días", "31-60 días", "61-90 días", "+90 días"],
            ).astype("object")
            pending.loc[pending["Vencimiento"].isna(), "Antigüedad"] = "Sin vencimiento"
            aging = pending.groupby("Antigüedad", as_index=False)["Pendiente"].sum()
            fig = px.bar(aging, x="Antigüedad", y="Pendiente", text_auto=".2s")
            fig.update_layout(height=350, margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### Concentración de facturación")
    billing = model["billing"]
    if billing.empty:
        st.info("No hay información para analizar financiadores.")
    else:
        period_billing = billing[_dg_period_mask(billing["Fecha factura"], start, end)].copy()
        payer = period_billing.groupby("Obra social", as_index=False)["Monto"].sum().sort_values("Monto", ascending=False).head(12)
        if payer.empty or payer["Monto"].sum() <= 0:
            st.info("No hay facturación del período para mostrar por obra social.")
        else:
            fig = px.bar(payer.sort_values("Monto"), x="Monto", y="Obra social", orientation="h", text_auto=".2s")
            fig.update_layout(height=430, margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### Próximos vencimientos monetarios")
    upcoming = model["upcoming_due"].copy()
    if upcoming.empty:
        st.success("No hay vencimientos monetarios dentro de 30 días.")
    else:
        upcoming["Días"] = (upcoming["Vencimiento"] - today).dt.days
        upcoming = upcoming.sort_values(["Vencimiento", "Saldo"], ascending=[True, False]).head(30)
        st.dataframe(
            upcoming[["Vencimiento", "Naturaleza", "Empresa", "Módulo", "Concepto", "Saldo", "Días"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "Vencimiento": st.column_config.DateColumn(format="DD/MM/YYYY"),
                "Saldo": st.column_config.NumberColumn(format="$ %.2f"),
            },
        )


def _dg_render_operations(model: dict[str, Any], start: pd.Timestamp, end: pd.Timestamp) -> None:
    st.markdown("### Actividad y desempeño operativo")
    operations = model["operations_period"]
    billing = model["billing"]
    period_billing = billing[_dg_period_mask(billing["Fecha factura"], start, end)].copy() if not billing.empty else pd.DataFrame()

    completed = 0
    cancelled = 0
    pending = 0
    if not operations.empty:
        completed = int(operations["Estado normalizado"].isin(_DG_ESTADOS_PAGADOS).sum())
        cancelled = int(operations["Estado normalizado"].isin(_DG_ESTADOS_CANCELADOS).sum())
        pending = int(len(operations) - completed - cancelled)

    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Registros de agenda", len(operations))
    o2.metric("Realizados", completed)
    o3.metric("Pendientes / programados", pending)
    o4.metric("Cancelados", cancelled)

    left, right = st.columns(2)
    with left:
        st.markdown("#### Procedimientos con mayor actividad")
        source = operations if not operations.empty else period_billing.rename(columns={"Fecha factura": "Fecha"})
        if source.empty or "Procedimiento" not in source.columns:
            st.info("No hay procedimientos registrados en el período.")
        else:
            top = source["Procedimiento"].value_counts().head(12).rename_axis("Procedimiento").reset_index(name="Casos")
            fig = px.bar(top.sort_values("Casos"), x="Casos", y="Procedimiento", orientation="h", text="Casos")
            fig.update_layout(height=410, margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(fig, use_container_width=True)
    with right:
        st.markdown("#### Actividad médica")
        source = operations if not operations.empty else period_billing
        if source.empty or "Médico" not in source.columns:
            st.info("No hay profesionales registrados en el período.")
        else:
            top = source[source["Médico"].ne("Sin especificar")]["Médico"].value_counts().head(12).rename_axis("Médico").reset_index(name="Casos")
            if top.empty:
                st.info("No hay profesionales identificados en los registros.")
            else:
                fig = px.bar(top.sort_values("Casos"), x="Casos", y="Médico", orientation="h", text="Casos")
                fig.update_layout(height=410, margin=dict(l=10, r=10, t=20, b=10))
                st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### Matriz operativa por empresa")
    company_rows = []
    for company in ["VMR", "VM", "VITAE"]:
        bill = period_billing[period_billing["Empresa"].eq(company)] if not period_billing.empty else pd.DataFrame()
        ops = operations[operations["Empresa"].eq(company)] if not operations.empty else pd.DataFrame()
        company_rows.append({
            "Empresa": company,
            "Pacientes / registros facturados": int(bill["Clave paciente"].nunique()) if not bill.empty else 0,
            "Facturación": float(bill["Monto"].sum()) if not bill.empty else 0.0,
            "Agenda": int(len(ops)),
            "Realizados": int(ops["Estado normalizado"].isin(_DG_ESTADOS_PAGADOS).sum()) if not ops.empty else 0,
            "Cancelados": int(ops["Estado normalizado"].isin(_DG_ESTADOS_CANCELADOS).sum()) if not ops.empty else 0,
        })
    company_table = pd.DataFrame(company_rows)
    st.dataframe(
        company_table,
        use_container_width=True,
        hide_index=True,
        column_config={"Facturación": st.column_config.NumberColumn(format="$ %.2f")},
    )


def _dg_render_risks(model: dict[str, Any], today: pd.Timestamp) -> None:
    st.markdown("### Riesgos, vencimientos y continuidad")
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Registros vencidos", model["overdue_count"])
    r2.metric("Monto vencido", fmt_money(model["overdue_amount"]))
    r3.metric("Tareas abiertas", len(model["open_tasks"]))
    r4.metric("Contratos próximos", len(model["expiring_contracts"]))

    st.markdown("#### Saldos vencidos")
    overdue_parts = []
    if not model["overdue_billing"].empty:
        bill = model["overdue_billing"].copy()
        bill = bill.rename(columns={"Pendiente": "Saldo", "Obra social": "Concepto"})
        bill["Naturaleza"] = "A cobrar"
        overdue_parts.append(bill[["Vencimiento", "Naturaleza", "Empresa", "Módulo", "Concepto", "Saldo", "Estado"]])
    if not model["overdue_due"].empty:
        overdue_parts.append(model["overdue_due"][["Vencimiento", "Naturaleza", "Empresa", "Módulo", "Concepto", "Saldo", "Estado"]])
    if overdue_parts:
        overdue = pd.concat(overdue_parts, ignore_index=True)
        overdue["Días vencido"] = (today - overdue["Vencimiento"]).dt.days
        overdue = overdue.sort_values(["Saldo", "Días vencido"], ascending=[False, False]).head(50)
        st.dataframe(
            overdue,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Vencimiento": st.column_config.DateColumn(format="DD/MM/YYYY"),
                "Saldo": st.column_config.NumberColumn(format="$ %.2f"),
            },
        )
    else:
        st.success("No se detectaron saldos vencidos.")

    left, right = st.columns(2)
    with left:
        st.markdown("#### Tareas abiertas")
        tasks = model["open_tasks"].copy()
        if tasks.empty:
            st.success("No hay tareas abiertas.")
        else:
            tasks["Días"] = (tasks["Vencimiento"] - today).dt.days
            st.dataframe(
                tasks.sort_values("Vencimiento", na_position="last")[["Vencimiento", "Prioridad", "Tarea", "Estado", "Días"]].head(30),
                use_container_width=True,
                hide_index=True,
                column_config={"Vencimiento": st.column_config.DateColumn(format="DD/MM/YYYY")},
            )
    with right:
        st.markdown("#### Contratos próximos a vencer")
        contracts = model["expiring_contracts"].copy()
        if contracts.empty:
            st.success("No hay contratos con vencimiento dentro de 60 días.")
        else:
            contracts["Días"] = (contracts["Vencimiento"] - today).dt.days
            st.dataframe(
                contracts.sort_values("Vencimiento")[["Vencimiento", "Empresa", "Contrato", "Estado", "Días"]].head(30),
                use_container_width=True,
                hide_index=True,
                column_config={"Vencimiento": st.column_config.DateColumn(format="DD/MM/YYYY")},
            )


def _dg_render_modules(model: dict[str, Any]) -> None:
    st.markdown("### Mapa integral de módulos")
    st.caption("Control de cobertura, volumen, uso en el período y consistencia de la información. Farmacia está excluida.")
    modules = model["modules"].copy()
    if modules.empty:
        st.warning("No se encontraron módulos para analizar.")
        return

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Módulos analizados", len(modules))
    m2.metric("Con datos", int(modules["Registros"].gt(0).sum()))
    m3.metric("Sin datos", int(modules["Registros"].eq(0).sum()))
    m4.metric("Calidad promedio", f"{model['quality_average']:.1f}%")

    if model.get("convention_rows", 0) > 0:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Convenios detectados", model["convention_count"])
        c2.metric("Prácticas de convenio", model["convention_rows"])
        coverage = model["convention_valued"] / model["convention_rows"] * 100 if model["convention_rows"] else 0
        c3.metric("Cobertura de valores", f"{coverage:.1f}%")
        c4.metric("Sin vigencia", model["convention_without_validity"])

    area = modules.groupby("Área", as_index=False).agg(
        Módulos=("Módulo", "count"),
        Registros=("Registros", "sum"),
        En_período=("En período", "sum"),
        Calidad=("Calidad %", "mean"),
    ).sort_values("Registros", ascending=False)
    left, right = st.columns([1, 1.2])
    with left:
        fig = px.bar(area, x="Área", y="Registros", text="Módulos")
        fig.update_layout(height=390, margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(fig, use_container_width=True)
    with right:
        company = modules.groupby("Empresa", as_index=False).agg(
            Módulos=("Módulo", "count"),
            Registros=("Registros", "sum"),
            Calidad=("Calidad %", "mean"),
        )
        st.dataframe(
            company,
            use_container_width=True,
            hide_index=True,
            column_config={"Calidad": st.column_config.NumberColumn(format="%.1f %%")},
        )

    show = modules.sort_values(["Cobertura", "Registros", "Módulo"], ascending=[True, False, True]).copy()
    st.dataframe(
        show,
        use_container_width=True,
        hide_index=True,
        height=560,
        column_config={
            "Último dato": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Calidad %": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f%%"),
        },
    )

    report = modules.copy()
    report["Último dato"] = pd.to_datetime(report["Último dato"], errors="coerce").dt.strftime("%d/%m/%Y")
    st.download_button(
        "Descargar control de módulos",
        data=report.to_csv(index=False).encode("utf-8-sig"),
        file_name="dashboard_global_control_modulos.csv",
        mime="text/csv",
        use_container_width=True,
    )


def _dg_ai_clean_frame(
    df: pd.DataFrame,
    columns: list[str] | None = None,
    max_rows: int = 100,
) -> pd.DataFrame:
    """Reduce y limpia tablas antes de enviarlas al modelo de IA."""
    if df is None or df.empty:
        return pd.DataFrame()

    show = df.copy()
    if columns:
        available = [column for column in columns if column in show.columns]
        show = show[available] if available else pd.DataFrame(index=show.index)
    show = show.head(max_rows).copy()

    for column in show.columns:
        if pd.api.types.is_datetime64_any_dtype(show[column]):
            show[column] = pd.to_datetime(show[column], errors="coerce").dt.strftime("%d/%m/%Y")
        elif show[column].dtype == "object":
            show[column] = (
                show[column]
                .fillna("")
                .astype(str)
                .str.replace(r"\s+", " ", regex=True)
                .str.strip()
                .str.slice(0, 140)
            )
    return show.fillna("")


def _dg_prepare_ai_context(
    model: dict[str, Any],
    start: pd.Timestamp,
    end: pd.Timestamp,
    today: pd.Timestamp,
) -> dict[str, pd.DataFrame]:
    """
    Crea un paquete ejecutivo pequeño y exacto.

    Evita enviar miles de filas de Google Sheets a Gemini, protege datos
    identificatorios innecesarios y conserva los totales que necesita dirección.
    """
    context: dict[str, pd.DataFrame] = {}

    context["Resumen ejecutivo"] = pd.DataFrame([{
        "Corte": today.strftime("%d/%m/%Y"),
        "Período desde": start.strftime("%d/%m/%Y"),
        "Período hasta": end.strftime("%d/%m/%Y"),
        "Liquidez": model["liquidity"],
        "Facturado período": model["billed_period"],
        "Cobrado período": model["collected_period"],
        "Tasa de cobranza %": round(model["collection_rate"], 2),
        "Facturación pendiente": model["billing_pending"],
        "Otras cuentas a cobrar": model["account_receivables"],
        "Total a cobrar": model["total_receivables"],
        "Total a pagar": model["total_payables"],
        "Importe vencido global": model["overdue_amount"],
        "Cantidad de vencidos": model["overdue_count"],
        "Vence próximos 30 días": model["upcoming_amount"],
        "Flujo de caja período": model["cash_flow"],
        "Pacientes período": model["patients_period"],
        "Médicos activos": model["doctors_period"],
        "Procedimientos": model["procedures_period"],
        "Tareas abiertas": len(model["open_tasks"]),
        "Tareas vencidas": len(model["overdue_tasks"]),
        "Contratos próximos a vencer": len(model["expiring_contracts"]),
        "Calidad promedio %": round(model["quality_average"], 2),
    }])

    pending = model["pending_billing"].copy()
    if not pending.empty:
        pending_group = (
            pending.groupby(["Empresa", "Módulo", "Obra social"], dropna=False)
            .agg(
                Comprobantes=("Pendiente", "size"),
                Pendiente=("Pendiente", "sum"),
                Vencidos=("Vencido", "sum"),
                Vencimiento_más_antiguo=("Vencimiento", "min"),
            )
            .reset_index()
            .sort_values("Pendiente", ascending=False)
        )
        context["Cobros pendientes agrupados"] = _dg_ai_clean_frame(
            pending_group,
            max_rows=80,
        )
        pending_detail = pending.sort_values(
            ["Pendiente", "Vencimiento"], ascending=[False, True]
        )
        context["Mayores comprobantes pendientes"] = _dg_ai_clean_frame(
            pending_detail,
            columns=[
                "Empresa", "Módulo", "Obra social", "Procedimiento", "Médico",
                "Vencimiento", "Pendiente", "Estado", "Días vencido", "Vencido",
            ],
            max_rows=100,
        )

    obligations = model["obligations"].copy()
    if not obligations.empty:
        receivables = obligations[obligations["A cobrar"].gt(0)].copy()
        if not receivables.empty:
            receivable_group = (
                receivables.groupby(["Empresa", "Módulo", "Concepto"], dropna=False)
                .agg(Registros=("A cobrar", "size"), Total=("A cobrar", "sum"))
                .reset_index()
                .sort_values("Total", ascending=False)
            )
            context["Otras cuentas a cobrar"] = _dg_ai_clean_frame(
                receivable_group,
                max_rows=80,
            )

        payables = obligations[obligations["Saldo"].gt(0)].copy()
        if not payables.empty:
            payable_group = (
                payables.groupby(["Empresa", "Área", "Módulo"], dropna=False)
                .agg(Registros=("Saldo", "size"), Total=("Saldo", "sum"))
                .reset_index()
                .sort_values("Total", ascending=False)
            )
            context["Obligaciones agrupadas"] = _dg_ai_clean_frame(
                payable_group,
                max_rows=80,
            )
            context["Mayores obligaciones"] = _dg_ai_clean_frame(
                payables.sort_values("Saldo", ascending=False),
                columns=[
                    "Empresa", "Área", "Módulo", "Concepto", "Saldo",
                    "Vencimiento", "Estado",
                ],
                max_rows=100,
            )

    treasury = model["treasury"].copy()
    if not treasury.empty:
        context["Tesorería"] = _dg_ai_clean_frame(treasury, max_rows=30)

    dues = model["dues"].copy()
    if not dues.empty:
        active_dues = dues[dues["Saldo"].gt(0)].copy()
        active_dues = active_dues.sort_values(
            ["Vencimiento", "Saldo"], ascending=[True, False], na_position="last"
        )
        context["Agenda de vencimientos"] = _dg_ai_clean_frame(
            active_dues,
            columns=[
                "Empresa", "Naturaleza", "Módulo", "Concepto",
                "Vencimiento", "Saldo", "Estado",
            ],
            max_rows=100,
        )

    tasks = model["open_tasks"].copy()
    if not tasks.empty:
        if "Vencimiento" in tasks.columns:
            tasks = tasks.sort_values("Vencimiento", na_position="last")
        context["Tareas abiertas"] = _dg_ai_clean_frame(tasks, max_rows=80)

    contracts = model["expiring_contracts"].copy()
    if not contracts.empty:
        if "Vencimiento" in contracts.columns:
            contracts = contracts.sort_values("Vencimiento")
        context["Contratos próximos a vencer"] = _dg_ai_clean_frame(
            contracts,
            max_rows=50,
        )

    operations = model["operations_period"].copy()
    if not operations.empty:
        operation_group = (
            operations.groupby(
                ["Empresa", "Módulo", "Estado", "Procedimiento"],
                dropna=False,
            )
            .size()
            .reset_index(name="Cantidad")
            .sort_values("Cantidad", ascending=False)
        )
        context["Actividad operativa agrupada"] = _dg_ai_clean_frame(
            operation_group,
            max_rows=100,
        )

    modules = model["modules"].copy()
    if not modules.empty:
        context["Mapa de módulos"] = _dg_ai_clean_frame(modules, max_rows=60)

    context["Control de convenios"] = pd.DataFrame([{
        "Convenios": model["convention_count"],
        "Prácticas": model["convention_rows"],
        "Prácticas valorizadas": model["convention_valued"],
        "Sin valor": model["convention_without_value"],
        "Sin vigencia": model["convention_without_validity"],
        "Duplicados": model["convention_duplicates"],
    }])

    return {name: frame for name, frame in context.items() if not frame.empty}


def _dg_friendly_ai_error(exc: Exception) -> str:
    """Convierte errores técnicos extensos en mensajes útiles para el usuario."""
    detail = str(exc)
    normalized = detail.lower()
    if "429" in normalized or "resource_exhausted" in normalized or "quota" in normalized:
        seconds = 60
        match = re.search(r"retry(?:delay| in)?[^0-9]*(\d+(?:\.\d+)?)", normalized)
        if match:
            seconds = max(10, int(float(match.group(1))) + 1)
        return (
            "Gemini alcanzó su límite temporal de uso. "
            f"Esperá aproximadamente {seconds} segundos y volvé a consultar. "
            "El Dashboard ya prepara un contexto reducido para que no vuelva a enviar toda la base."
        )
    if "api key" in normalized or "api_key" in normalized or "unauthorized" in normalized:
        return "No se pudo validar la clave de Gemini configurada en Secrets."
    if "timeout" in normalized or "deadline" in normalized:
        return "La consulta demoró más de lo permitido. Volvé a intentar en unos segundos."
    return "No se pudo completar la consulta. Revisá la conexión y volvé a intentar."


def _dg_local_briefing(
    model: dict[str, Any],
    start: pd.Timestamp,
    end: pd.Timestamp,
    today: pd.Timestamp,
) -> dict[str, Any]:
    """Informe ejecutivo calculado localmente, sin consumir cuota de Gemini."""
    coverage = model["coverage_ratio"]
    coverage_text = "sin obligaciones registradas"
    if coverage is not None:
        coverage_text = f"{coverage:.2f} veces"

    priorities: list[str] = []
    if model["overdue_amount"] > 0:
        priorities.append(
            f"Regularizar {fmt_money(model['overdue_amount'])} vencidos en "
            f"{model['overdue_count']} registros."
        )
    if model["total_receivables"] > 0:
        priorities.append(
            f"Gestionar la cobranza de {fmt_money(model['total_receivables'])}."
        )
    if model["total_payables"] > model["liquidity"]:
        gap = model["total_payables"] - model["liquidity"]
        priorities.append(
            f"Cubrir una brecha de liquidez de {fmt_money(gap)} frente a obligaciones."
        )
    if len(model["overdue_tasks"]) > 0:
        priorities.append(
            f"Resolver {len(model['overdue_tasks'])} tareas vencidas."
        )
    if not priorities:
        priorities.append("Mantener el seguimiento semanal de caja, cobranza y vencimientos.")

    content = (
        f"**Corte ejecutivo:** {today.strftime('%d/%m/%Y')}  \n"
        f"**Período analizado:** {start.strftime('%d/%m/%Y')} al {end.strftime('%d/%m/%Y')}\n\n"
        f"- Liquidez consolidada: **{fmt_money(model['liquidity'])}**.\n"
        f"- Facturado en el período: **{fmt_money(model['billed_period'])}**.\n"
        f"- Cobrado en el período: **{fmt_money(model['collected_period'])}** "
        f"({model['collection_rate']:.1f}% de cobranza).\n"
        f"- Total pendiente de cobro: **{fmt_money(model['total_receivables'])}**.\n"
        f"- Obligaciones registradas: **{fmt_money(model['total_payables'])}**.\n"
        f"- Cobertura de obligaciones: **{coverage_text}**.\n"
        f"- Flujo de caja del período: **{fmt_money(model['cash_flow'])}**.\n"
        f"- Tareas abiertas: **{len(model['open_tasks'])}**; vencidas: "
        f"**{len(model['overdue_tasks'])}**.\n\n"
        "**Prioridades inmediatas**\n" +
        "\n".join(f"{index}. {item}" for index, item in enumerate(priorities[:5], 1))
    )

    return {
        "saludo": "Informe ejecutivo de Dashboard Global",
        "contenido": content,
        "actualizado": today.strftime("%d/%m/%Y %H:%M"),
        "modulos_con_datos": int(model["modules"]["Registros"].gt(0).sum())
        if not model["modules"].empty else 0,
    }


def _dg_local_question_answer(
    model: dict[str, Any],
    question: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    today: pd.Timestamp,
) -> str | None:
    """Responde consultas ejecutivas frecuentes sin usar la API."""
    query = _dg_norm(question)

    collection_terms = [
        "que tengo para cobrar", "qué tengo para cobrar", "a cobrar",
        "cuentas por cobrar", "pendiente de cobro", "me deben", "cobrar",
    ]
    if any(term in query for term in collection_terms):
        pending = model["pending_billing"].copy()
        obligations = model["obligations"].copy()
        other_receivables = (
            obligations[obligations["A cobrar"].gt(0)].copy()
            if not obligations.empty else pd.DataFrame()
        )

        overdue_billing = 0.0
        if not pending.empty and "Vencido" in pending.columns:
            overdue_billing = float(pending.loc[pending["Vencido"], "Pendiente"].sum())
        overdue_other = 0.0
        if not other_receivables.empty:
            mask = (
                other_receivables["Vencimiento"].notna()
                & other_receivables["Vencimiento"].lt(today)
            )
            overdue_other = float(other_receivables.loc[mask, "A cobrar"].sum())

        lines = [
            f"## Total pendiente de cobro: {fmt_money(model['total_receivables'])}",
            f"- Facturación pendiente: **{fmt_money(model['billing_pending'])}**.",
            f"- Otras cuentas a cobrar: **{fmt_money(model['account_receivables'])}**.",
            f"- Vencido estimado: **{fmt_money(overdue_billing + overdue_other)}**.",
        ]

        if not pending.empty:
            top = (
                pending.groupby(["Empresa", "Obra social"], dropna=False)
                .agg(Casos=("Pendiente", "size"), Total=("Pendiente", "sum"))
                .reset_index()
                .sort_values("Total", ascending=False)
                .head(8)
            )
            lines.append("\n### Principales cobros pendientes")
            for _, row in top.iterrows():
                lines.append(
                    f"- **{row['Empresa']} · {row['Obra social']}**: "
                    f"{fmt_money(row['Total'])} en {int(row['Casos'])} registro(s)."
                )

        if not other_receivables.empty:
            top_other = (
                other_receivables.groupby(["Empresa", "Módulo"], dropna=False)["A cobrar"]
                .sum()
                .reset_index()
                .sort_values("A cobrar", ascending=False)
                .head(6)
            )
            lines.append("\n### Otras cuentas a cobrar")
            for _, row in top_other.iterrows():
                lines.append(
                    f"- **{row['Empresa']} · {row['Módulo']}**: "
                    f"{fmt_money(row['A cobrar'])}."
                )
        lines.append(f"\n_Corte: {today.strftime('%d/%m/%Y')}._")
        return "\n".join(lines)

    payment_terms = ["que tengo para pagar", "qué tengo para pagar", "a pagar", "cuentas por pagar", "debo", "obligaciones"]
    if any(term in query for term in payment_terms):
        obligations = model["obligations"].copy()
        payables = obligations[obligations["Saldo"].gt(0)].copy() if not obligations.empty else pd.DataFrame()
        lines = [
            f"## Total a pagar: {fmt_money(model['total_payables'])}",
            f"- Liquidez disponible: **{fmt_money(model['liquidity'])}**.",
        ]
        gap = model["liquidity"] - model["total_payables"]
        if gap >= 0:
            lines.append(f"- Cobertura remanente: **{fmt_money(gap)}**.")
        else:
            lines.append(f"- Brecha a cubrir: **{fmt_money(abs(gap))}**.")
        if not payables.empty:
            top = (
                payables.groupby(["Empresa", "Área", "Módulo"], dropna=False)["Saldo"]
                .sum()
                .reset_index()
                .sort_values("Saldo", ascending=False)
                .head(10)
            )
            lines.append("\n### Principales obligaciones")
            for _, row in top.iterrows():
                lines.append(
                    f"- **{row['Empresa']} · {row['Módulo']}** ({row['Área']}): "
                    f"{fmt_money(row['Saldo'])}."
                )
        return "\n".join(lines)

    if any(term in query for term in ["liquidez", "caja", "bancos", "disponible"]):
        lines = [f"## Liquidez consolidada: {fmt_money(model['liquidity'])}"]
        treasury = model["treasury"].copy()
        if not treasury.empty:
            for _, row in treasury.sort_values("Disponible", ascending=False).iterrows():
                lines.append(
                    f"- **{row['Empresa']} · {row['Módulo']}**: {fmt_money(row['Disponible'])}."
                )
        lines.append(
            f"\nFlujo de caja del período {start.strftime('%d/%m/%Y')}–"
            f"{end.strftime('%d/%m/%Y')}: **{fmt_money(model['cash_flow'])}**."
        )
        return "\n".join(lines)

    if any(term in query for term in ["facturacion", "facturación", "cobranza", "cobrado", "facturado"]):
        return (
            f"## Desempeño de facturación\n"
            f"- Facturado entre {start.strftime('%d/%m/%Y')} y {end.strftime('%d/%m/%Y')}: "
            f"**{fmt_money(model['billed_period'])}**.\n"
            f"- Cobrado: **{fmt_money(model['collected_period'])}**.\n"
            f"- Tasa de cobranza: **{model['collection_rate']:.1f}%**.\n"
            f"- Pendiente histórico de facturación: **{fmt_money(model['billing_pending'])}**.\n"
            f"- Ticket promedio del período: **{fmt_money(model['ticket'])}**."
        )

    if any(term in query for term in ["vencido", "vencimiento", "urgente", "riesgo"]):
        return (
            f"## Riesgos y vencimientos\n"
            f"- Registros vencidos: **{model['overdue_count']}**.\n"
            f"- Importe vencido global: **{fmt_money(model['overdue_amount'])}**.\n"
            f"- Vence en los próximos 30 días: **{fmt_money(model['upcoming_amount'])}**.\n"
            f"- Tareas vencidas: **{len(model['overdue_tasks'])}**.\n"
            f"- Contratos que vencen dentro de 60 días: **{len(model['expiring_contracts'])}**."
        )

    if any(term in query for term in ["tarea", "pendientes de gestion", "pendientes de gestión"]):
        return (
            f"## Gestión de tareas\n"
            f"- Tareas abiertas: **{len(model['open_tasks'])}**.\n"
            f"- Tareas vencidas: **{len(model['overdue_tasks'])}**."
        )

    if any(term in query for term in ["paciente", "procedimiento", "medico", "médico", "actividad"]):
        return (
            f"## Actividad del período\n"
            f"- Pacientes: **{model['patients_period']}**.\n"
            f"- Médicos activos: **{model['doctors_period']}**.\n"
            f"- Procedimientos distintos: **{model['procedures_period']}**.\n"
            f"- Ticket promedio: **{fmt_money(model['ticket'])}**."
        )

    return None


def _dg_render_ai(
    dfs: dict[str, pd.DataFrame],
    model: dict[str, Any],
    start: pd.Timestamp,
    end: pd.Timestamp,
    today: pd.Timestamp,
) -> None:
    st.markdown("### Director IA")
    st.caption(
        "Las consultas financieras directas se calculan localmente. "
        "Gemini se utiliza sólo cuando hace falta interpretación adicional y recibe un resumen reducido."
    )

    if st.button("Generar informe ejecutivo automático", type="primary", key="dg_generate_briefing"):
        st.session_state["dg_briefing"] = _dg_local_briefing(model, start, end, today)
        st.session_state.pop("dg_briefing_error", None)

    briefing = st.session_state.get("dg_briefing")
    if briefing:
        with st.container(border=True):
            st.markdown(f"#### {briefing.get('saludo', 'Informe ejecutivo')}")
            st.markdown(str(briefing.get("contenido", "Sin contenido disponible.")))
            st.caption(
                f"Actualizado: {briefing.get('actualizado', '-')} · "
                f"{briefing.get('modulos_con_datos', 0)} módulos con datos."
            )

    st.divider()
    question = st.text_area(
        "Pregunta ejecutiva",
        placeholder="Ej.: ¿Qué tengo para cobrar?",
        key="dashboard_ia",
        height=110,
    )
    if st.button("Consultar", key="consultar_dashboard", use_container_width=True):
        if not question.strip():
            st.warning("Escribí una pregunta para realizar el análisis.")
        else:
            st.session_state.pop("dg_ai_error", None)
            local_answer = _dg_local_question_answer(
                model=model,
                question=question,
                start=start,
                end=end,
                today=today,
            )
            if local_answer is not None:
                st.session_state["dg_ai_answer"] = local_answer
            else:
                with st.spinner("Analizando Dashboard Global..."):
                    try:
                        compact_context = _dg_prepare_ai_context(model, start, end, today)
                        st.session_state["dg_ai_answer"] = preguntar_dashboard(
                            compact_context,
                            question,
                        )
                    except Exception as exc:
                        st.session_state.pop("dg_ai_answer", None)
                        st.session_state["dg_ai_error"] = _dg_friendly_ai_error(exc)

    if st.session_state.get("dg_ai_answer"):
        with st.container(border=True):
            st.markdown(st.session_state["dg_ai_answer"])
    if st.session_state.get("dg_ai_error"):
        st.warning(st.session_state["dg_ai_error"])


@st.cache_data(ttl=300)
def _dg_load_dashboard_data() -> dict[str, pd.DataFrame]:
    data: dict[str, pd.DataFrame] = {}
    for module_name, cfg in MODULES.items():
        if _dg_category(module_name, cfg) == "Excluir":
            continue
        table = cfg["table"]
        if table in data or _dg_category(module_name, cfg) == "Convenios":
            continue
        try:
            data[table] = add_balance_columns(get_df(table))
        except Exception:
            data[table] = pd.DataFrame()
    return data


@st.cache_data(ttl=300)
def _dg_load_convention_snapshot() -> pd.DataFrame:
    loader = globals().get("_convenios_cargar_google")
    parser = globals().get("_convenios_preparar_datos")
    if not callable(loader) or not callable(parser):
        return pd.DataFrame()
    try:
        matrices, _ = loader()
        practices, directory, _ = parser(matrices, "Google Sheets")
        frames = []
        if not practices.empty:
            practice_snapshot = practices.copy()
            practice_snapshot["tipo_registro"] = "Práctica"
            frames.append(practice_snapshot)
        if not directory.empty:
            directory_snapshot = pd.DataFrame({
                "convenio": directory.get("convenio", pd.Series(dtype=str)),
                "codigo": "",
                "descripcion": directory.get("obra_social", pd.Series(dtype=str)),
                "valor": pd.NA,
                "vigencia": pd.NaT,
                "tipo_registro": "Directorio",
            })
            frames.append(directory_snapshot)
        return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def render_dashboard() -> None:
    """Dashboard Global consolidado para dirección general."""
    _dg_render_css()

    try:
        today = pd.Timestamp.now(tz="America/Argentina/Salta").normalize().tz_localize(None)
    except Exception:
        today = pd.Timestamp.today().normalize()

    all_data = _dg_load_dashboard_data()
    dfs: dict[str, pd.DataFrame] = {}
    for module_name, cfg in MODULES.items():
        if _dg_category(module_name, cfg) == "Excluir":
            continue
        table = cfg["table"]
        dfs[module_name] = all_data.get(table, pd.DataFrame()).copy()

    # Convenios utiliza dos pestañas y una estructura especial; se normaliza
    # sólo para lectura ejecutiva, sin modificar las planillas originales.
    convention_data = _dg_load_convention_snapshot()
    for module_name, cfg in MODULES.items():
        if _dg_category(module_name, cfg) == "Convenios":
            dfs[module_name] = convention_data.copy()

    top_left, top_right = st.columns([5.8, 1.2])
    with top_left:
        st.markdown(
            """
            <div class="dg-hero">
                <div class="dg-kicker">Dirección general</div>
                <div class="dg-title">Dashboard Global</div>
                <div class="dg-subtitle">
                    Visión consolidada de VMR, VM y áreas corporativas: desempeño financiero,
                    actividad, cobranza, obligaciones, vencimientos, riesgos y calidad de gestión.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with top_right:
        logo_path = Path("logo_vitae.png")
        if logo_path.exists():
            st.image(str(logo_path), use_container_width=True)
        if st.button("Actualizar", key="dg_refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    filter_col, custom_col, export_col = st.columns([2.2, 2.4, 1.1])
    with filter_col:
        period_option = st.selectbox(
            "Período de análisis",
            [
                "Mes actual", "Mes anterior", "Año actual",
                "Últimos 12 meses", "Histórico", "Personalizado",
            ],
            key="dg_period_option",
        )

    current_month_start = today.replace(day=1)
    if period_option == "Mes actual":
        start = current_month_start
        end = today
    elif period_option == "Mes anterior":
        end = current_month_start - pd.Timedelta(days=1)
        start = end.replace(day=1)
    elif period_option == "Año actual":
        start = today.replace(month=1, day=1)
        end = today
    elif period_option == "Últimos 12 meses":
        start = (today - pd.DateOffset(months=11)).replace(day=1)
        end = today
    elif period_option == "Histórico":
        start = pd.Timestamp("2000-01-01")
        end = today
    else:
        with custom_col:
            selected = st.date_input(
                "Rango personalizado",
                value=(current_month_start.date(), today.date()),
                max_value=today.date(),
                key="dg_custom_dates",
            )
        if isinstance(selected, (tuple, list)):
            if len(selected) >= 2:
                start, end = pd.Timestamp(selected[0]), pd.Timestamp(selected[1])
            elif len(selected) == 1:
                start = end = pd.Timestamp(selected[0])
            else:
                start, end = current_month_start, today
        else:
            start = end = pd.Timestamp(selected)
    if period_option != "Personalizado":
        with custom_col:
            st.markdown(
                f'<div class="dg-period">{start.strftime("%d/%m/%Y")} — {end.strftime("%d/%m/%Y")}</div>',
                unsafe_allow_html=True,
            )

    model = _dg_build_model(dfs, start, end, today)
    total_records = int(model["modules"]["Registros"].sum()) if not model["modules"].empty else 0
    modules_with_data = int(model["modules"]["Registros"].gt(0).sum()) if not model["modules"].empty else 0
    st.caption(
        f"Corte al {today.strftime('%d/%m/%Y')} · "
        f"{modules_with_data} módulos con datos · {total_records:,} registros analizados"
    )

    with export_col:
        snapshot = pd.DataFrame([{
            "Desde": start.strftime("%d/%m/%Y"),
            "Hasta": end.strftime("%d/%m/%Y"),
            "Liquidez": model["liquidity"],
            "Facturado": model["billed_period"],
            "Cobrado": model["collected_period"],
            "Pendiente de cobro": model["total_receivables"],
            "Obligaciones": model["total_payables"],
            "Vencido": model["overdue_amount"],
            "Pacientes": model["patients_period"],
            "Tareas abiertas": len(model["open_tasks"]),
        }])
        st.download_button(
            "Exportar resumen",
            data=snapshot.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"dashboard_global_{end.strftime('%Y%m%d')}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    tabs = st.tabs([
        "Resumen ejecutivo",
        "Finanzas y cobranza",
        "Operaciones",
        "Riesgos y vencimientos",
        "Mapa de módulos",
        "Director IA",
    ])
    with tabs[0]:
        _dg_render_overview(model, start, end)
    with tabs[1]:
        _dg_render_finance(model, start, end, today)
    with tabs[2]:
        _dg_render_operations(model, start, end)
    with tabs[3]:
        _dg_render_risks(model, today)
    with tabs[4]:
        _dg_render_modules(model)
    with tabs[5]:
        _dg_render_ai(dfs, model, start, end, today)

def get_fact_labels(module_name: str, cfg: Dict[str, Any]) -> Dict[str, str]:
    labels = DEFAULT_FACT_LABELS.copy()
    return labels
def rename_fact_df(df: pd.DataFrame, labels: Dict[str, str]) -> pd.DataFrame:
    return df.rename(columns={c: labels.get(c, c.replace("_", " ").title()) for c in df.columns})
def format_facturacion_table(df: pd.DataFrame, labels: Dict[str, str]) -> pd.DataFrame:
    if df.empty:
        return df
    show = df.copy()
    if "mes" in show.columns:
        show["mes"] = pd.to_datetime(
            show["mes"],
            errors="coerce"            
        ).dt.strftime("%d/%m/%Y")
    show = show.drop(
        columns=[
            "id",
            "created_at",
            "updated_at"
        ],
        errors="ignore"
    )
    for col in ["fecha_factura", "vencimiento", "fecha_pago"]:
        if col in show.columns:
            show[col] = pd.to_datetime(show[col], errors="coerce").dt.strftime("%d/%m/%Y")
            show[col] = show[col].fillna("")
    for col in ["valor_pesos"]:
        if col in show.columns:
            show[col] = show[col].apply(fmt_money)
    if "valor_usd" in show.columns:
        show["valor_usd"] = show["valor_usd"].apply(lambda x: f"USD {money(x):,.2f}")
    show = show.rename(columns={c: labels.get(c, c.replace("_", " ").title()) for c in show.columns})
    return show
def render_analisis_anual_2026(df: pd.DataFrame):

    st.subheader("📈 Análisis anual 2026")

    if df.empty or "mes" not in df.columns:

        st.info("No hay datos suficientes para analizar.")

        return

    data = df.copy()

    data["mes"] = pd.to_datetime(

        data["mes"],

        errors="coerce"

    )

    data = data.dropna(subset=["mes"])

    monto_col = None

    for col in [

        "valor_pesos",

        "importe",

        "monto",

        "facturado",

        "total"

    ]:

        if col in data.columns:

            monto_col = col

            break

    if not monto_col:

        st.warning(

            "No encontré columna de monto para calcular facturación."

        )

        return

    data[monto_col] = (

        data[monto_col]

        .apply(money)

        .fillna(0)

    )

    hoy = pd.Timestamp.today().normalize()

    inicio_2026 = pd.Timestamp("2026-01-01")

    fin_2026 = min(

        hoy,

        pd.Timestamp("2026-12-31")

    )

    # Solamente toma datos desde enero de 2026 hasta hoy.

    data = data[

        (data["mes"] >= inicio_2026)

        & (data["mes"] <= fin_2026)

    ].copy()

    if data.empty:

        st.info("No hay registros de 2026.")

        return

    nombres_meses = {

        1: "enero",

        2: "febrero",

        3: "marzo",

        4: "abril",

        5: "mayo",

        6: "junio",

        7: "julio",

        8: "agosto",

        9: "septiembre",

        10: "octubre",

        11: "noviembre",

        12: "diciembre",

    }

    data["numero_mes"] = data["mes"].dt.month

    data["mes_nombre"] = data["numero_mes"].map(

        nombres_meses

    )

    mensual = (

        data.groupby(

            ["numero_mes", "mes_nombre"],

            as_index=False

        )[monto_col]

        .sum()

        .rename(columns={monto_col: "facturacion"})

        .sort_values("numero_mes")

    )

    facturacion_acumulada = mensual["facturacion"].sum()

    meses_transcurridos = fin_2026.month

    promedio_mensual = (

        facturacion_acumulada / meses_transcurridos

        if meses_transcurridos > 0

        else 0

    )

    mejor_mes = mensual.loc[

        mensual["facturacion"].idxmax()

    ]

    proyeccion_anual = promedio_mensual * 12

    nombre_mes_actual = nombres_meses.get(

        fin_2026.month,

        str(fin_2026.month)

    )

    col1, col2, col3, col4 = st.columns(4)

    col1.metric(

        f"Facturación enero a {nombre_mes_actual}",

        fmt_money(facturacion_acumulada)

    )

    col2.metric(

        "Promedio mensual",

        fmt_money(promedio_mensual)

    )

    col3.metric(

        "Mejor mes",

        mejor_mes["mes_nombre"].capitalize()

    )

    col4.metric(

        "Proyección cierre 2026",

        fmt_money(proyeccion_anual)

    )

    resumen_anual = pd.DataFrame({

        "Concepto": [

            "Acumulado actual",

            "Proyección cierre 2026"

        ],

        "Facturación": [

            facturacion_acumulada,

            proyeccion_anual

        ]

    })

    fig = px.bar(

        resumen_anual,

        x="Concepto",

        y="Facturación",

        title=(

            f"Facturación anual 2026: "

            f"enero a {nombre_mes_actual}"

        ),

        text_auto=".2s"

    )

    fig.update_layout(

        xaxis_title="",

        yaxis_title="Facturación",

        height=420

    )

    st.plotly_chart(

        fig,

        use_container_width=True

    )

    porcentaje_anio = (

        meses_transcurridos / 12

    ) * 100

    st.caption(

        f"Período analizado: 01/01/2026 al "

        f"{fin_2026.strftime('%d/%m/%Y')} · "

        f"{porcentaje_anio:.0f}% del año transcurrido."

    )
def render_analisis_mensual_2026(df: pd.DataFrame):
    st.subheader("📈 Análisis mensual 2026")
    if df.empty or "mes" not in df.columns:
        st.info("No hay datos suficientes para analizar.")
        return
    data = df.copy()
    data["mes"] = pd.to_datetime(data["mes"], errors="coerce")
    data = data[data["mes"].dt.year == 2026]
    if data.empty:
        st.info("No hay registros de 2026.")
        return
    monto_col = None
    for col in [
        "valor_pesos",
        "importe",
        "monto",
        "facturado",
        "total"
    ]:
        if col in data.columns:
            monto_col = col
            break
    if not monto_col:
        st.warning("No encontré columna de monto para calcular facturación.")
        return
    data[monto_col] = data[monto_col].apply(money)
    data["mes_nombre"] = data["mes"].dt.strftime("%Y-%m")
    mensual = (
        data.groupby("mes_nombre")[monto_col]
        .sum()
        .reset_index()
        .rename(columns={monto_col: "facturacion"})
    )
    acumulado = mensual["facturacion"].sum()
    promedio = mensual["facturacion"].mean()
    mejor_mes = mensual.loc[mensual["facturacion"].idxmax()]
    proyeccion = promedio * 12
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Facturación 2026", fmt_money(acumulado))
    col2.metric("Promedio mensual", fmt_money(promedio))
    col3.metric("Mejor mes", mejor_mes["mes_nombre"])
    col4.metric("Proyección anual", fmt_money(proyeccion))
    fig = px.bar(
        mensual,
        x="mes_nombre",
        y="facturacion",
        title="Facturación mensual 2026",
        text_auto=".2s",
    )
    fig.update_layout(
        xaxis_title="Mes",
        yaxis_title="Facturación",
        height=420,
    )
    st.plotly_chart(fig, use_container_width=True)
    mensual["acumulado"] = mensual["facturacion"].cumsum()
    fig2 = px.line(
        mensual,
        x="mes_nombre",
        y="acumulado",
        markers=True,
        title="Evolución acumulada 2026",
    )
    fig2.update_layout(
        xaxis_title="Mes",
        yaxis_title="Acumulado",
        height=380,
    )
    st.plotly_chart(fig2, use_container_width=True)
def _money_sum(df, col):
    if col not in df.columns:
        return 0
    return df[col].apply(money).sum()
def _money_usd_sum(df, col):
    if col not in df.columns:
        return 0
    return pd.to_numeric(df[col], errors="coerce").fillna(0).sum()
def _first_money_col(df):
    for c in ["valor_pesos", "importe", "monto", "saldo", "valor"]:
        if c in df.columns:
            return c
    return None
def render_tabla_limpia_panel(filtered: pd.DataFrame) -> None:
    st.divider()
    st.markdown("### Tabla limpia")
    tabla = filtered.copy()
    tabla = tabla.drop(
        columns=["id", "created_at", "updated_at", "responsable", "observaciones"],
        errors="ignore"
    )
    if "mes" in tabla.columns:
        orden = parse_mes(tabla["mes"])
        tabla = tabla.assign(_orden=orden)
        tabla = tabla.sort_values("_orden", ascending=False, na_position="last")
        tabla["mes"] = tabla["_orden"].dt.strftime("%d-%m-%Y")
        tabla["mes"] = tabla["mes"].fillna("")
        tabla = tabla.drop(columns=["_orden"], errors="ignore")
    st.dataframe(

        tabla,
    
        use_container_width=True,
    
        hide_index=True,
    
        column_config={
    
            "mes": st.column_config.Column(
    
                "Fecha",
    
                width=105,
    
            ),
    
            "afiliado": st.column_config.Column(
    
                "Afiliado",
    
                width=230,
    
            ),
    
            "obra_social": st.column_config.Column(
    
                "Obra social",
    
                width=100,
    
            ),
    
            "procedimiento": st.column_config.Column(
    
                "Procedimiento",
    
                width=170,
    
            ),
    
            "medico_responsable": st.column_config.Column(
    
                "Médico",
    
                width=190,
    
            ),
    
            "fecha_factura": st.column_config.Column(
    
                "F. factura",
    
                width=105,
    
            ),
    
            "numero_factura": st.column_config.Column(
    
                "N.º factura",
    
                width=105,
    
            ),
    
            "vencimiento": st.column_config.Column(
    
                "Vencimiento",
    
                width=110,
    
            ),
    
            "fecha_pago": st.column_config.Column(
    
                "F. pago",
    
                width=105,
    
            ),
    
            "valor_pesos": st.column_config.Column(
    
                "Valor",
    
                width=120,
    
            ),
    
        },
    )
        
    

    st.divider()
    
    st.subheader("🤖 Asistente IA")
    
    pregunta = st.text_input(
    
        "Preguntale algo sobre esta tabla",
    
        key="pregunta_ia"
    
    )
    
    if st.button("Consultar IA", key="btn_ia"):
    
        with st.spinner("Analizando información..."):
    
            respuesta = preguntar_ia(
    
                modulo="Tabla",
    
                df=filtered,
    
                pregunta=pregunta
    
            )
    
        st.success(respuesta)
def _safe_float(value) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except Exception:
        return 0.0
def render_metricas_panel(filtered: pd.DataFrame, table: str) -> None:

    df = filtered.copy()

    col_monto = _first_money_col(df)

    total = _money_sum(df, col_monto)

    if "pagado" in df.columns:

        cobrado = _money_sum(df, "pagado")

    elif "estado" in df.columns and col_monto:

        estados_ok = ["completo", "pagado", "cobrado", "realizado", "finalizado"]

        cobrado_df = df[

            df["estado"].astype(str).str.lower().str.strip().isin(estados_ok)

        ]

        cobrado = _money_sum(cobrado_df, col_monto)

    else:

        cobrado = 0.0

    total = _safe_float(total)

    cobrado = _safe_float(cobrado)

    pendiente = total - cobrado

    registros = len(df)

    total_usd = _money_usd_sum(df, "importe_usd")

    pagado_usd = _money_usd_sum(df, "pagado_usd")

    pendiente_usd = _money_usd_sum(df, "saldo_usd")
    if table == "Contratos":

        st.metric("👥 Registros", registros)

    elif table == "cuenta_corriente_vm":

        c1, c2, c3 = st.columns(3)

        c1.metric("💰 Total Facturas", fmt_money(total))

        c2.metric("💸 Total Pagado", fmt_money(cobrado))

        c3.metric("⏳ Deuda Total", fmt_money(pendiente))

    elif table == "cuenta_corriente_vmr":

        c1, c2, c3, c4, c5, c6 = st.columns(6)

        c1.metric("🛒 Compras ARS", fmt_money(total))

        c2.metric("🛒 Compras USD", f"USD {total_usd:,.2f}")

        c3.metric("💵 Pagado ARS", fmt_money(cobrado))

        c4.metric("💵 Pagado USD", f"USD {pagado_usd:,.2f}")

        c5.metric("📌 A Pagar ARS", fmt_money(pendiente))

        c6.metric("📌 A Pagar USD", f"USD {pendiente_usd:,.2f}")

    else:

        c1, c2, c3, c4 = st.columns(4)

        c1.metric("💰 Facturado", fmt_money(total))

        c2.metric("✅ Cobrado", fmt_money(cobrado))

        c3.metric("⏳ Pendiente", fmt_money(pendiente))

        c4.metric("👥 Registros", registros)
def render_dashboard_proveedores_vm(filtered: pd.DataFrame) -> None:
    if not {"importe", "pagado", "persona_entidad"}.issubset(filtered.columns):
        return
    st.divider()
    st.markdown("## 📊 Dashboard Financiero Proveedores VM")
    graf = filtered.copy()
    graf["Deuda"] = (
        pd.to_numeric(graf["importe"], errors="coerce").fillna(0)
        - pd.to_numeric(graf["pagado"], errors="coerce").fillna(0)
    )
    graf_deuda = (
        graf[graf["Deuda"] > 0]
        .groupby("persona_entidad", as_index=False)["Deuda"]
        .sum()
        .rename(columns={"persona_entidad": "Proveedor"})
        .sort_values("Deuda", ascending=False)
    )
    if not graf_deuda.empty:
        fig = px.bar(
            graf_deuda,
            x="Deuda",
            y="Proveedor",
            orientation="h",
            text="Deuda",
            title="💰 Ranking de deuda por proveedor",
        )
        fig.update_layout(height=500, yaxis=dict(categoryorder="total ascending"))
        st.plotly_chart(fig, use_container_width=True)
    if "vencimiento" in filtered.columns:
        venc = filtered.copy()
        venc["vencimiento"] = pd.to_datetime(venc["vencimiento"], dayfirst=True, errors="coerce")
        venc["saldo"] = (
            pd.to_numeric(venc["importe"], errors="coerce").fillna(0)
            - pd.to_numeric(venc["pagado"], errors="coerce").fillna(0)
        )
        venc = venc[venc["saldo"] > 0]
        venc_resumen = (
            venc.groupby("vencimiento")["saldo"]
            .sum()
            .reset_index()
            .sort_values("vencimiento")
        )
        if not venc_resumen.empty:
            fig = px.bar(
                venc_resumen,
                x="vencimiento",
                y="saldo",
                text="saldo",
                title="📅 Calendario de vencimientos",
            )
            fig.update_layout(height=400)
            st.plotly_chart(fig, use_container_width=True)
    pagado_total = pd.to_numeric(filtered["pagado"], errors="coerce").fillna(0).sum()
    pendiente_total = pd.to_numeric(filtered["importe"], errors="coerce").fillna(0).sum() - pagado_total
    pie_df = pd.DataFrame({
        "Estado": ["Pagado", "Pendiente"],
        "Monto": [pagado_total, pendiente_total],
    })
    fig = px.pie(pie_df, names="Estado", values="Monto", hole=0.55, title="💳 Pagado vs Pendiente")
    fig.update_layout(height=450)
    st.plotly_chart(fig, use_container_width=True)
    st.divider()
    st.markdown("### 🚨 Facturas más importantes pendientes")
    top = filtered.copy()
    top["saldo"] = (
        pd.to_numeric(top["importe"], errors="coerce").fillna(0)
        - pd.to_numeric(top["pagado"], errors="coerce").fillna(0)
    )
    top = top[top["saldo"] > 0].sort_values("saldo", ascending=False)
    cols = [c for c in ["persona_entidad", "comprobante", "fecha", "vencimiento", "saldo"] if c in top.columns]
    if not top.empty:
        st.dataframe(top[cols].head(15), use_container_width=True, hide_index=True)
def render_graficos_facturacion(filtered: pd.DataFrame) -> None:
    st.divider()
    st.markdown("### Gráficos útiles")
    g1, g2 = st.columns(2)
    if "valor_pesos" in filtered.columns:
        graph = filtered.copy()
        fecha_col = None
        if "fecha_factura" in graph.columns:
            tmp = pd.to_datetime(graph["fecha_factura"], errors="coerce")
            if tmp.notna().sum() > 0:
                fecha_col = "fecha_factura"
        if fecha_col is None and "mes" in graph.columns:
            tmp = pd.to_datetime(graph["mes"], errors="coerce", dayfirst=True)
            if tmp.notna().sum() > 0:
                fecha_col = "mes"
        if fecha_col:
            graph[fecha_col] = pd.to_datetime(graph[fecha_col], errors="coerce", dayfirst=True)
            graph = graph[graph[fecha_col].notna()]
            graph["Mes"] = graph[fecha_col].dt.to_period("M").astype(str)
            chart = (
                graph.groupby("Mes")["valor_pesos"]
                .apply(lambda x: x.apply(money).sum())
                .reset_index()
            )
            fig = px.bar(chart, x="Mes", y="valor_pesos", title="Facturación por mes")
            g1.plotly_chart(fig, use_container_width=True)
    if "obra_social" in filtered.columns and "valor_pesos" in filtered.columns:
        chart = (
            filtered.groupby("obra_social")["valor_pesos"]
            .apply(lambda x: x.apply(money).sum())
            .reset_index()
            .sort_values("valor_pesos", ascending=False)
            .head(10)
        )
        fig = px.bar(chart, x="obra_social", y="valor_pesos", title="Facturación por obra social")
        g2.plotly_chart(fig, use_container_width=True)
    g3, g4 = st.columns(2)
    if "medico_responsable" in filtered.columns and "valor_pesos" in filtered.columns:
        chart = (
            filtered.groupby("medico_responsable")["valor_pesos"]
            .apply(lambda x: x.apply(money).sum())
            .reset_index()
            .sort_values("valor_pesos", ascending=False)
            .head(10)
        )
        fig = px.bar(chart, x="medico_responsable", y="valor_pesos", title="Facturación por médico")
        g3.plotly_chart(fig, use_container_width=True)
    if "procedimiento" in filtered.columns and "valor_pesos" in filtered.columns:
        chart = (
            filtered.groupby("procedimiento")["valor_pesos"]
            .apply(lambda x: x.apply(money).sum())
            .reset_index()
            .sort_values("valor_pesos", ascending=False)
            .head(10)
        )
        fig = px.bar(chart, x="procedimiento", y="valor_pesos", title="Facturación por procedimiento")
        g4.plotly_chart(fig, use_container_width=True)
# =========================================================

# AGENDA QUIRÓFANO PRO

# =========================================================

def render_agenda_quirofano_pro(

    df_original: pd.DataFrame,

    guardar_callback=None,

) -> pd.DataFrame:

    """

    Agenda quirúrgica profesional.

    Parámetros

    ----------

    df_original:

        DataFrame leído desde Google Sheets.

    guardar_callback:

        Función existente que recibe el DataFrame completo actualizado

        y lo guarda nuevamente en Google Sheets.

        Ejemplo:

        guardar_callback(df_actualizado)

    Retorna

    -------

    pd.DataFrame:

        DataFrame actualizado.

    """

    import calendar

    import html

    import unicodedata

    from datetime import date, datetime, timedelta

    import pandas as pd

    import plotly.express as px

    import streamlit as st

    # -----------------------------------------------------

    # CONFIGURACIÓN

    # -----------------------------------------------------

    ESTADOS_AGENDA = [

        "Consulta",

        "Pendiente de autorización",

        "Autorizado",

        "Programado",

        "Confirmado",

        "En quirófano",

        "Realizado",

        "Reprogramado",

        "Suspendido",

        "Cancelado",

    ]

    ESTADOS_KANBAN = [

        "Consulta",

        "Pendiente de autorización",

        "Autorizado",

        "Programado",

        "Confirmado",

        "Realizado",

    ]

    CAMPOS_BASE = {

        "fecha": "",

        "hora_inicio": "",

        "hora_fin": "",

        "duracion_min": 60,

        "sala": "",

        "paciente": "",

        "procedimiento": "",

        "medico": "",

        "estado": "Consulta",

        "anestesista": "",

        "obra_social": "",

        "numero_afiliado": "",

        "autorizacion": "",

        "telefono": "",

        "observaciones": "",

    }

    # -----------------------------------------------------

    # FUNCIONES INTERNAS

    # -----------------------------------------------------

    def normalizar_nombre_columna(valor) -> str:

        texto = str(valor or "").strip().lower()

        texto = unicodedata.normalize("NFKD", texto)

        texto = "".join(

            caracter

            for caracter in texto

            if not unicodedata.combining(caracter)

        )

        reemplazos = {

            " ": "_",

            "-": "_",

            "/": "_",

            ".": "",

            "(": "",

            ")": "",

        }

        for anterior, nuevo in reemplazos.items():

            texto = texto.replace(anterior, nuevo)

        while "__" in texto:

            texto = texto.replace("__", "_")

        return texto.strip("_")

    def texto_limpio(valor) -> str:

        if pd.isna(valor):

            return ""

        texto = str(valor).strip()

        if texto.lower() in {"nan", "none", "nat"}:

            return ""

        return texto

    def convertir_fecha(valor):

        if pd.isna(valor) or texto_limpio(valor) == "":

            return pd.NaT

        fecha_convertida = pd.to_datetime(

            valor,

            errors="coerce",

            dayfirst=True,

        )

        return fecha_convertida

    def normalizar_hora(valor, hora_default="08:00") -> str:

        texto = texto_limpio(valor)

        if not texto:

            return hora_default

        try:

            if ":" in texto:

                partes = texto.split(":")

                hora = int(float(partes[0]))

                minuto = int(float(partes[1]))

                return f"{hora:02d}:{minuto:02d}"

            numero = float(texto)

            # Hora guardada como fracción de día en Sheets/Excel.

            if 0 <= numero < 1:

                minutos_totales = round(numero * 24 * 60)

                hora = minutos_totales // 60

                minuto = minutos_totales % 60

                return f"{hora:02d}:{minuto:02d}"

            hora = int(numero)

            return f"{hora:02d}:00"

        except Exception:

            return hora_default

    def calcular_hora_fin(fila) -> str:

        hora_fin = normalizar_hora(fila.get("hora_fin"), "")

        if hora_fin:

            return hora_fin

        hora_inicio = normalizar_hora(

            fila.get("hora_inicio"),

            "08:00",

        )

        try:

            duracion = int(float(fila.get("duracion_min", 60) or 60))

        except Exception:

            duracion = 60

        try:

            inicio = datetime.strptime(hora_inicio, "%H:%M")

            fin = inicio + timedelta(minutes=duracion)

            return fin.strftime("%H:%M")

        except Exception:

            return hora_inicio

    def guardar_df(df_nuevo: pd.DataFrame) -> bool:

        if guardar_callback is None:

            st.warning(

                "El cambio quedó preparado, pero todavía no se configuró "

                "la función que guarda en Google Sheets."

            )

            return False

        try:

            guardar_callback(df_nuevo.copy())

            st.cache_data.clear()

            st.success("Cambio guardado correctamente en Google Sheets.")

            return True

        except Exception as error:

            st.error(

                "No se pudo guardar el cambio en Google Sheets.\n\n"

                f"Detalle: {error}"

            )

            return False

    def crear_fecha_hora(fila, columna_hora):

        fecha = fila.get("_fecha_dt")

        if pd.isna(fecha):

            return pd.NaT

        hora = normalizar_hora(

            fila.get(columna_hora),

            "08:00",

        )

        try:

            return pd.to_datetime(

                f"{fecha.strftime('%Y-%m-%d')} {hora}"

            )

        except Exception:

            return pd.NaT

    def badge_estado(estado: str) -> str:

        estilos = {

            "Consulta": ("#E8F0FE", "#174EA6"),

            "Pendiente de autorización": ("#FFF4E5", "#A15C00"),

            "Autorizado": ("#E6F4EA", "#137333"),

            "Programado": ("#E8EAED", "#3C4043"),

            "Confirmado": ("#E6F4EA", "#0D652D"),

            "En quirófano": ("#FCE8E6", "#B31412"),

            "Realizado": ("#D7F8E2", "#086B34"),

            "Reprogramado": ("#F3E8FD", "#681DA8"),

            "Suspendido": ("#FDE7E9", "#A50E0E"),

            "Cancelado": ("#F1F3F4", "#5F6368"),

        }

        fondo, texto = estilos.get(

            estado,

            ("#F1F3F4", "#3C4043"),

        )

        return (

            f"<span style='"

            f"background:{fondo};"

            f"color:{texto};"

            "padding:4px 9px;"

            "border-radius:999px;"

            "font-size:12px;"

            "font-weight:700;"

            "white-space:nowrap;"

            f"'>{html.escape(estado)}</span>"

        )

    # -----------------------------------------------------

    # CSS EXCLUSIVO DE LA AGENDA

    # -----------------------------------------------------

    st.markdown(

        """

        <style>

        .agenda-hero {{

            padding: 22px 24px;

            border: 1px solid rgba(120, 120, 120, 0.18);

            border-radius: 18px;

            margin-bottom: 18px;

            background:

                linear-gradient(

                    135deg,

                    rgba(255, 75, 75, 0.07),

                    rgba(70, 120, 255, 0.04)

                );

        }}

        .agenda-hero-title {{

            font-size: 31px;

            line-height: 1.15;

            font-weight: 800;

            margin-bottom: 5px;

        }}

        .agenda-hero-subtitle {{

            color: rgba(100, 100, 110, 0.95);

            font-size: 15px;

        }}

        .agenda-card {{

            border: 1px solid rgba(120, 120, 120, 0.20);

            border-radius: 14px;

            padding: 13px 14px;

            margin-bottom: 10px;

            background: rgba(255, 255, 255, 0.62);

            box-shadow: 0 2px 12px rgba(0, 0, 0, 0.035);

        }}

        .agenda-card-title {{

            font-size: 15px;

            font-weight: 750;

            margin-bottom: 5px;

        }}

        .agenda-card-detail {{

            color: #6B7280;

            font-size: 13px;

            line-height: 1.5;

        }}

        .agenda-kanban-header {{

            font-size: 15px;

            font-weight: 800;

            padding: 9px 10px;

            border-radius: 10px;

            text-align: center;

            background: rgba(120, 120, 120, 0.10);

            margin-bottom: 10px;

        }}

        .agenda-month {{

            width: 100%;

            border-collapse: separate;

            border-spacing: 6px;

        }}

        .agenda-month th {{

            text-align: center;

            font-size: 12px;

            color: #6B7280;

            padding: 5px;

        }}

        .agenda-month td {{

            width: 14.28%;

            height: 92px;

            vertical-align: top;

            border: 1px solid rgba(120, 120, 120, 0.17);

            border-radius: 10px;

            padding: 7px;

            font-size: 12px;

        }}

        .agenda-month-day {{

            font-weight: 800;

            font-size: 13px;

            margin-bottom: 7px;

        }}

        .agenda-month-count {{

            display: inline-block;

            background: rgba(255, 75, 75, 0.13);

            border-radius: 999px;

            padding: 3px 7px;

            font-weight: 700;

        }}

        .agenda-empty {{

            padding: 28px 15px;

            text-align: center;

            border: 1px dashed rgba(120, 120, 120, 0.30);

            border-radius: 13px;

            color: #7A7A84;

        }}

        </style>

        """,

        unsafe_allow_html=True,

    )

    # -----------------------------------------------------

    # PREPARAR DATOS SIN MODIFICAR EL ORIGINAL

    # -----------------------------------------------------

    if df_original is None:

        df_original = pd.DataFrame()

    df = df_original.copy()

    # Normaliza nombres para que la agenda trabaje correctamente.

    columnas_originales = list(df.columns)

    mapa_columnas = {

        columna: normalizar_nombre_columna(columna)

        for columna in columnas_originales

    }

    df = df.rename(columns=mapa_columnas)

    alias_columnas = {

        "fecha_cirugia": "fecha",

        "fecha_procedimiento": "fecha",

        "fecha_turno": "fecha",

        "hora": "hora_inicio",

        "inicio": "hora_inicio",

        "fin": "hora_fin",

        "nombre_paciente": "paciente",

        "apellido_y_nombre": "paciente",

        "apellido_nombre": "paciente",

        "medico_responsable": "medico",

        "profesional": "medico",

        "cirujano": "medico",

        "practica": "procedimiento",

        "cirugia": "procedimiento",

        "obra_social_prepaga": "obra_social",

        "cobertura": "obra_social",

        "n_afiliado": "numero_afiliado",

        "numero_de_afiliado": "numero_afiliado",

        "nro_afiliado": "numero_afiliado",

        "estado_agenda": "estado",

    }

    for columna_actual, columna_estandar in alias_columnas.items():

        if (

            columna_actual in df.columns

            and columna_estandar not in df.columns

        ):

            df = df.rename(

                columns={columna_actual: columna_estandar}

            )

    # Agrega solamente en memoria campos faltantes.

    for columna, valor_default in CAMPOS_BASE.items():

        if columna not in df.columns:

            df[columna] = valor_default

    df["_fila_original"] = range(len(df))

    df["_fecha_dt"] = df["fecha"].apply(convertir_fecha)
    df["_fecha_dt"] = pd.to_datetime(df["_fecha_dt"], errors="coerce")

    df["estado"] = (

        df["estado"]

        .apply(texto_limpio)

        .replace("", "Consulta")

    )

    df["paciente"] = df["paciente"].apply(texto_limpio)

    df["procedimiento"] = df["procedimiento"].apply(texto_limpio)

    df["medico"] = df["medico"].apply(texto_limpio)

    df["sala"] = df["sala"].apply(texto_limpio)

    df["obra_social"] = df["obra_social"].apply(texto_limpio)

    df["observaciones"] = df["observaciones"].apply(texto_limpio)

    df["_hora_inicio_normalizada"] = df["hora_inicio"].apply(

        lambda valor: normalizar_hora(valor, "08:00")

    )

    df["_hora_fin_normalizada"] = df.apply(

        calcular_hora_fin,

        axis=1,

    )

    df["_inicio_dt"] = df.apply(

        lambda fila: crear_fecha_hora(

            fila,
            "_hora_inicio_normalizada",
        ),
        axis=1,
    )
    df["_fin_dt"] = df.apply(
        lambda fila: crear_fecha_hora(
            fila,
            "_hora_fin_normalizada",
        ),
        axis=1,
    )
    # -----------------------------------------------------
    # ENCABEZADO
    # -----------------------------------------------------
    st.markdown(
        f"""
        <div class="agenda-hero">
            <div class="agenda-hero-title">
                🏥 Agenda Quirúrgica
            </div>
            <div class="agenda-hero-subtitle">
                Programación, autorizaciones, consultas y seguimiento
                integral de pacientes quirúrgicos.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    # -----------------------------------------------------

    # FILTROS PROPIOS DE QUIRÓFANO

    # -----------------------------------------------------

    with st.container(border=True):

        st.markdown("### 🔎 Filtros de agenda")

        fila_filtros_1 = st.columns([1.7, 1.3, 1.3, 1.3])

        with fila_filtros_1[0]:

            buscar = st.text_input(

                "Buscar paciente, médico o procedimiento",

                key="agenda_buscar_general",

                placeholder="Ej.: María Pérez, histeroscopia...",

            )

        estados_disponibles = sorted(

            set(ESTADOS_AGENDA + df["estado"].dropna().tolist())

        )

        with fila_filtros_1[1]:

            estados_seleccionados = st.multiselect(

                "Estado",

                options=estados_disponibles,

                default=[],

                key="agenda_filtro_estado",

                placeholder="Todos",

            )

        procedimientos = sorted(

            [

                valor

                for valor in df["procedimiento"].dropna().unique()

                if texto_limpio(valor)

            ]

        )

        with fila_filtros_1[2]:

            procedimientos_seleccionados = st.multiselect(

                "Procedimiento",

                options=procedimientos,

                default=[],

                key="agenda_filtro_procedimiento",

                placeholder="Todos",

            )

        medicos = sorted(

            [

                valor

                for valor in df["medico"].dropna().unique()

                if texto_limpio(valor)

            ]

        )

        with fila_filtros_1[3]:

            medicos_seleccionados = st.multiselect(

                "Médico",

                options=medicos,

                default=[],

                key="agenda_filtro_medico",

                placeholder="Todos",

            )

        fila_filtros_2 = st.columns([1.2, 1.2, 1.2, 1.2])

        hoy = date.today()

        fechas_validas = df["_fecha_dt"].dropna()

        fecha_minima = (

            fechas_validas.min().date()

            if not fechas_validas.empty

            else hoy - timedelta(days=30)

        )

        fecha_maxima = (

            fechas_validas.max().date()

            if not fechas_validas.empty

            else hoy + timedelta(days=90)

        )

        with fila_filtros_2[0]:

            fecha_desde = st.date_input(

                "Desde",

                value=min(hoy, fecha_minima),

                key="agenda_fecha_desde",

            )

        with fila_filtros_2[1]:

            fecha_hasta = st.date_input(

                "Hasta",

                value=max(hoy + timedelta(days=30), fecha_maxima),

                key="agenda_fecha_hasta",

            )

        salas = sorted(

            [

                valor

                for valor in df["sala"].dropna().unique()

                if texto_limpio(valor)

            ]

        )

        with fila_filtros_2[2]:

            salas_seleccionadas = st.multiselect(

                "Sala",

                options=salas,

                default=[],

                key="agenda_filtro_sala",

                placeholder="Todas",

            )

        obras_sociales = sorted(

            [

                valor

                for valor in df["obra_social"].dropna().unique()

                if texto_limpio(valor)

            ]

        )

        with fila_filtros_2[3]:

            obras_seleccionadas = st.multiselect(

                "Obra social",

                options=obras_sociales,

                default=[],

                key="agenda_filtro_obra_social",

                placeholder="Todas",

            )

    # -----------------------------------------------------

    # APLICAR FILTROS

    # -----------------------------------------------------

    df_filtrado = df.copy()

    if buscar.strip():

        texto_busqueda = buscar.strip().lower()

        mascara_busqueda = (

            df_filtrado["paciente"]

            .astype(str)

            .str.lower()

            .str.contains(texto_busqueda, na=False)

            |

            df_filtrado["procedimiento"]

            .astype(str)

            .str.lower()

            .str.contains(texto_busqueda, na=False)

            |

            df_filtrado["medico"]

            .astype(str)

            .str.lower()

            .str.contains(texto_busqueda, na=False)

            |

            df_filtrado["obra_social"]

            .astype(str)

            .str.lower()

            .str.contains(texto_busqueda, na=False)

        )

        df_filtrado = df_filtrado[mascara_busqueda]

    if estados_seleccionados:

        df_filtrado = df_filtrado[

            df_filtrado["estado"].isin(estados_seleccionados)

        ]

    if procedimientos_seleccionados:

        df_filtrado = df_filtrado[

            df_filtrado["procedimiento"].isin(

                procedimientos_seleccionados

            )

        ]

    if medicos_seleccionados:

        df_filtrado = df_filtrado[

            df_filtrado["medico"].isin(medicos_seleccionados)

        ]

    if salas_seleccionadas:

        df_filtrado = df_filtrado[

            df_filtrado["sala"].isin(salas_seleccionadas)

        ]

    if obras_seleccionadas:

        df_filtrado = df_filtrado[

            df_filtrado["obra_social"].isin(

                obras_seleccionadas

            )

        ]

    if fecha_desde:

        df_filtrado = df_filtrado[

            df_filtrado["_fecha_dt"].isna()

            | (

                df_filtrado["_fecha_dt"].dt.date

                >= fecha_desde

            )

        ]

    if fecha_hasta:

        df_filtrado = df_filtrado[

            df_filtrado["_fecha_dt"].isna()

            | (

                df_filtrado["_fecha_dt"].dt.date

                <= fecha_hasta

            )

        ]

    # -----------------------------------------------------

    # INDICADORES QUIRÚRGICOS

    # -----------------------------------------------------

    total_consultas = int(

        df_filtrado["estado"].eq("Consulta").sum()

    )

    total_pendientes = int(

        df_filtrado["estado"]

        .eq("Pendiente de autorización")

        .sum()

    )

    total_programados = int(

        df_filtrado["estado"]

        .isin(["Programado", "Confirmado", "En quirófano"])

        .sum()

    )

    total_realizados = int(

        df_filtrado["estado"].eq("Realizado").sum()

    )

    total_hoy = int(

        (

            df_filtrado["_fecha_dt"].dt.date

            == hoy

        ).fillna(False).sum()

    )

    metricas = st.columns(5)

    metricas[0].metric(

        "💬 Consultas",

        total_consultas,

    )

    metricas[1].metric(

        "⏳ Pendientes autorización",

        total_pendientes,

    )

    metricas[2].metric(

        "📅 Programados",

        total_programados,

    )

    metricas[3].metric(

        "✅ Realizados",

        total_realizados,

    )

    metricas[4].metric(

        "🏥 Cirugías de hoy",

        total_hoy,

    )

    st.markdown("")

    # -----------------------------------------------------

    # VISTAS PRINCIPALES

    # -----------------------------------------------------

    (

        tab_kanban,

        tab_diaria,

        tab_semanal,

        tab_mensual,

        tab_anual,

        tab_listado,

    ) = st.tabs(

        [

            "▦ Flujo de pacientes",

            "📆 Día",

            "🗓️ Semana",

            "📅 Mes",

            "📊 Año",

            "📋 Listado",

        ]

    )
    # ==========================================================

    # TABLERO TIPO TRELLO

    # ==========================================================

    st.markdown("---")

    st.subheader("📋 Gestión Visual de Pacientes")

    # Si no existe la columna

    if "estado" not in df.columns:

        df["estado"] = "Programado"

    columnas = [

        "Consulta",

        "Pendiente Autorización",

        "Programado",

        "En Quirófano",

        "Finalizado",

        "Suspendido"

    ]

    trello = st.columns(len(columnas))

    for i, estado in enumerate(columnas):

        with trello[i]:

            color = {

                "Consulta":"#4FA3FF",

                "Pendiente Autorización":"orange",

                "Programado":"green",

                "En Quirófano":"red",

                "Finalizado":"gray",

                "Suspendido":"black"

            }.get(estado,"gray")

            st.markdown(
                f"""
                <div style="
                    background:{color};
                    color:white;
                    padding:8px;
                    border-radius:8px;
                    text-align:center;
                    font-weight:bold;">
                    {estado}
                </div>
                """,
                unsafe_allow_html=True
            )
            datos = df[df["estado"] == estado]
            if len(datos)==0:
                st.info("Sin pacientes")

            else:

                for idx,row in datos.iterrows():

                    paciente = row.get("paciente","")

                    proc = row.get("procedimiento","")

                    medico = row.get("medico","")

                    sala = row.get("sala","")

                    hora = row.get("hora_inicio","")

                    with st.container(border=True):

                        st.markdown(f"### 👤 {paciente}")

                        st.caption(proc)

                        st.write("🕒",hora)

                        st.write("👨‍⚕️",medico)

                        st.write("🏥",sala)

                        nuevo_estado = st.selectbox(

                            "Mover",

                            columnas,

                            index=columnas.index(estado),

                            key=f"{idx}_estado"

                        )

                        if nuevo_estado != estado:

                            df.loc[idx,"estado"] = nuevo_estado

                            save_table(cfg["table"],df)

                            st.success("Paciente actualizado")

                            st.rerun()

    # ==========================================================

    # PACIENTES DEL DÍA

    # ==========================================================

    st.markdown("---")

    st.subheader("📅 Agenda del Día")

    hoy = pd.Timestamp.today().date()

    if "fecha" in df.columns:

        agenda_hoy = df[

            pd.to_datetime(df["fecha"],errors="coerce").dt.date == hoy

        ].sort_values("hora_inicio")

        if len(agenda_hoy):

            for _,r in agenda_hoy.iterrows():

                with st.container(border=True):

                    c1,c2,c3,c4 = st.columns([1,3,2,2])

                    c1.metric("Hora",r["hora_inicio"])

                    c2.write("### "+str(r["paciente"]))

                    c2.caption(r["procedimiento"])

                    c3.write("👨‍⚕️")

                    c3.write(r["medico"])

                    c4.write("🏥")

                    c4.write(r["sala"])

        else:

            st.info("No hay procedimientos para hoy.")

    # ==========================================================

    # ESTADÍSTICAS

    # ==========================================================

    st.markdown("---")

    st.subheader("📊 Indicadores")

    c1,c2,c3,c4,c5,c6 = st.columns(6)

    c1.metric(

        "Consultas",

        len(df[df["estado"]=="Consulta"])

    )

    c2.metric(

        "Pendientes",

        len(df[df["estado"]=="Pendiente Autorización"])

    )

    c3.metric(

        "Programados",

        len(df[df["estado"]=="Programado"])

    )

    c4.metric(

        "En Quirófano",

        len(df[df["estado"]=="En Quirófano"])

    )

    c5.metric(

        "Finalizados",

        len(df[df["estado"]=="Finalizado"])

    )

    c6.metric(

        "Suspendidos",

        len(df[df["estado"]=="Suspendido"])

    )

    # ==========================================================

    # PRÓXIMAS CIRUGÍAS

    # ==========================================================

    st.markdown("---")

    st.subheader("⏰ Próximas Cirugías")

    if "fecha" in df.columns:

        proximas = (

            df.sort_values("fecha")

            .head(10)

        )

        st.dataframe(

            proximas,

            use_container_width=True,

            hide_index=True

        )

    # ==========================================================

    # CALENDARIO DE OCUPACIÓN

    # ==========================================================

    st.markdown("---")

    st.subheader("📆 Ocupación del Quirófano")

    if "fecha" in df.columns:

        ocupacion = (

            df.groupby("fecha")

            .size()

            .reset_index(name="Cirugías")

        )

        st.bar_chart(

            ocupacion.set_index("fecha")

        )

    # ==========================================================

    # ALERTAS

    # ==========================================================

    st.markdown("---")

    st.subheader("🚨 Alertas")

    if "estado" in df.columns:

        pendientes = len(df[df["estado"]=="Pendiente Autorización"])

        if pendientes:

            st.warning(

                f"Hay {pendientes} pacientes pendientes de autorización."

            )

    if "fecha" in df.columns:

        manana = hoy + pd.Timedelta(days=1)

        manana_df = df[

            pd.to_datetime(df["fecha"],errors="coerce").dt.date == manana

        ]

        if len(manana_df):

            st.info(

                f"Mañana hay {len(manana_df)} procedimientos programados."

            )

    st.success("Agenda cargada correctamente.")
def parse_mes(series):
    s = series.astype(str).str.strip()
    fecha_ymd = pd.to_datetime(
        s,
        format="%Y-%m-%d",
        errors="coerce"
    )
    fecha_dmy = pd.to_datetime(
        s,
        format="%d/%m/%Y",
        errors="coerce"
    )
    return fecha_ymd.fillna(fecha_dmy)
def render_honorarios_medicos_pro(df: pd.DataFrame) -> None:
    """Panel ejecutivo exclusivo para el módulo Honorarios médicos."""
    import re
    import unicodedata

    st.markdown("## 🩺 Centro de Control de Honorarios Médicos")
    st.caption(
        "Facturación asociada, honorarios generados, pagos, saldos pendientes "
        "y productividad por médico."
    )

    if df is None or df.empty:
        st.info("No hay registros de honorarios para analizar.")
        return

    data = df.copy()

    # ---------------------------------------------------------
    # NORMALIZACIÓN DE COLUMNAS Y VALORES
    # ---------------------------------------------------------
    def normalizar_nombre(valor: object) -> str:
        texto = unicodedata.normalize("NFKD", str(valor))
        texto = "".join(c for c in texto if not unicodedata.combining(c))
        texto = texto.strip().lower()
        texto = re.sub(r"[^a-z0-9]+", "_", texto)
        return texto.strip("_")

    columnas = {normalizar_nombre(col): col for col in data.columns}

    def buscar_columna(*opciones: str):
        for opcion in opciones:
            encontrada = columnas.get(normalizar_nombre(opcion))
            if encontrada is not None:
                return encontrada
        return None

    def numero(valor: object) -> float:
        if valor is None or pd.isna(valor):
            return 0.0
        if isinstance(valor, bool):
            return float(valor)
        if isinstance(valor, (int, float)):
            try:
                return float(valor)
            except Exception:
                return 0.0

        texto = str(valor).strip()
        if not texto:
            return 0.0

        texto = re.sub(r"[^0-9,.-]", "", texto)
        if not texto or texto in {"-", ".", ","}:
            return 0.0

        if "," in texto and "." in texto:
            if texto.rfind(",") > texto.rfind("."):
                texto = texto.replace(".", "").replace(",", ".")
            else:
                texto = texto.replace(",", "")
        elif "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        elif texto.count(".") > 1:
            texto = texto.replace(".", "")

        try:
            return float(texto)
        except Exception:
            return 0.0

    def fmt_ars(valor: object) -> str:
        valor_num = numero(valor)
        formato = f"{valor_num:,.2f}"
        formato = formato.replace(",", "X").replace(".", ",").replace("X", ".")
        return f"$ {formato}"

    fecha_col = buscar_columna("fecha", "mes", "fecha procedimiento", "fecha atención")
    empresa_col = buscar_columna("empresa", "sociedad")
    medico_col = buscar_columna("medico", "médico", "profesional")
    paciente_col = buscar_columna("paciente", "nombre paciente")
    procedimiento_col = buscar_columna("procedimiento", "prestacion", "prestación", "practica", "práctica")
    facturado_col = buscar_columna(
        "importe facturado",
        "monto facturado",
        "facturado",
        "facturación asociada",
        "importe_facturado",
    )
    honorario_col = buscar_columna(
        "monto a pagar",
        "honorario",
        "honorarios",
        "importe honorario",
        "monto honorario",
        "valor honorario",
    )
    pagado_col = buscar_columna(
        "pagado",
        "monto pagado",
        "importe pagado",
        "honorario pagado",
    )
    fecha_pago_col = buscar_columna("fecha pago", "fecha de pago", "fecha_pago")
    estado_col = buscar_columna("estado", "estado pago", "estado de pago")

    if honorario_col is None:
        st.error(
            "No encuentro la columna del honorario. Debe llamarse “Monto a pagar”, "
            "“Honorario” o “Importe honorario”."
        )
        st.caption("Columnas disponibles: " + ", ".join(map(str, data.columns)))
        return

    data["_fecha"] = (
        pd.to_datetime(data[fecha_col], errors="coerce", dayfirst=True)
        if fecha_col
        else pd.NaT
    )
    data["_empresa"] = (
        data[empresa_col].fillna("").astype(str).str.strip()
        if empresa_col
        else "SIN EMPRESA"
    )
    data["_medico"] = (
        data[medico_col].fillna("").astype(str).str.strip()
        if medico_col
        else "SIN MÉDICO"
    )
    data["_paciente"] = (
        data[paciente_col].fillna("").astype(str).str.strip()
        if paciente_col
        else ""
    )
    data["_procedimiento"] = (
        data[procedimiento_col].fillna("").astype(str).str.strip()
        if procedimiento_col
        else "SIN PROCEDIMIENTO"
    )
    data["_facturado"] = (
        data[facturado_col].apply(numero)
        if facturado_col
        else 0.0
    )
    data["_honorario"] = data[honorario_col].apply(numero)
    data["_pagado"] = (
        data[pagado_col].apply(numero)
        if pagado_col
        else 0.0
    )
    data["_fecha_pago"] = (
        pd.to_datetime(data[fecha_pago_col], errors="coerce", dayfirst=True)
        if fecha_pago_col
        else pd.NaT
    )
    data["_estado_original"] = (
        data[estado_col].fillna("").astype(str).str.strip().str.lower()
        if estado_col
        else ""
    )

    data["_empresa"] = data["_empresa"].replace("", "SIN EMPRESA")
    data["_medico"] = data["_medico"].replace("", "SIN MÉDICO")
    data["_procedimiento"] = data["_procedimiento"].replace("", "SIN PROCEDIMIENTO")

    # Si "pagado" contiene Sí/True o el estado dice Pagado, toma el honorario completo.
    if pagado_col:
        marca_pagado = (
            data[pagado_col]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"si", "sí", "true", "pagado", "abonado", "cancelado", "completo"})
        )
    else:
        marca_pagado = pd.Series(False, index=data.index)

    estado_pagado = data["_estado_original"].isin(
        {"pagado", "abonado", "cancelado", "completo", "finalizado"}
    )
    data.loc[(marca_pagado | estado_pagado) & (data["_pagado"] <= 0), "_pagado"] = data["_honorario"]

    data["_saldo"] = (data["_honorario"] - data["_pagado"]).clip(lower=0)
    data["_estado_calculado"] = "Pendiente"
    data.loc[data["_honorario"] <= 0, "_estado_calculado"] = "Sin honorario cargado"
    data.loc[
        (data["_honorario"] > 0)
        & (data["_pagado"] > 0)
        & (data["_saldo"] > 0),
        "_estado_calculado",
    ] = "Pago parcial"
    data.loc[
        (data["_honorario"] > 0) & (data["_saldo"] <= 0),
        "_estado_calculado",
    ] = "Pagado"

    hoy = pd.Timestamp.today().normalize()
    data["_dias_pendiente"] = (hoy - data["_fecha"]).dt.days
    data["_pendiente_30"] = (
        (data["_saldo"] > 0)
        & data["_dias_pendiente"].gt(30)
    )

    # ---------------------------------------------------------
    # KPIs EJECUTIVOS
    # ---------------------------------------------------------
    registros = len(data)
    medicos = data.loc[data["_medico"] != "SIN MÉDICO", "_medico"].nunique()
    pacientes = data.loc[data["_paciente"] != "", "_paciente"].nunique()
    facturacion_total = float(data["_facturado"].sum())
    honorarios_total = float(data["_honorario"].sum())
    pagado_total = float(data["_pagado"].sum())
    pendiente_total = float(data["_saldo"].sum())
    porcentaje_pagado = (
        pagado_total / honorarios_total * 100
        if honorarios_total > 0
        else 0.0
    )
    promedio_honorario = (
        honorarios_total / registros
        if registros > 0
        else 0.0
    )
    relacion_honorarios = (
        honorarios_total / facturacion_total * 100
        if facturacion_total > 0
        else 0.0
    )
    sin_honorario = int((data["_honorario"] <= 0).sum())
    pendientes_30 = int(data["_pendiente_30"].sum())

    fila_1 = st.columns(4)
    fila_1[0].metric("💵 Facturación asociada", fmt_ars(facturacion_total))
    fila_1[1].metric("🩺 Honorarios generados", fmt_ars(honorarios_total))
    fila_1[2].metric("✅ Honorarios pagados", fmt_ars(pagado_total))
    fila_1[3].metric("⏳ Saldo pendiente", fmt_ars(pendiente_total))

    fila_2 = st.columns(4)
    fila_2[0].metric("🎯 Porcentaje pagado", f"{porcentaje_pagado:,.1f}%".replace(".", ","))
    fila_2[1].metric("👨‍⚕️ Médicos", medicos)
    fila_2[2].metric("👥 Pacientes", pacientes)
    fila_2[3].metric("📋 Prestaciones", registros)

    st.caption(
        f"Promedio por prestación: **{fmt_ars(promedio_honorario)}** · "
        f"Honorarios sobre facturación asociada: **{relacion_honorarios:,.1f}%**".replace(".", ",")
    )

    if pendiente_total <= 0 and honorarios_total > 0:
        st.success("✅ No existen honorarios pendientes dentro del período filtrado.")
    elif pendientes_30 > 0:
        st.warning(
            f"⚠️ Hay {pendientes_30} prestaciones con saldo pendiente desde hace más de 30 días, "
            f"por un total de {fmt_ars(data.loc[data['_pendiente_30'], '_saldo'].sum())}."
        )
    elif pendiente_total > 0:
        st.info(f"ℹ️ El saldo pendiente del período es {fmt_ars(pendiente_total)}.")

    if sin_honorario > 0:
        st.warning(
            f"🧾 Hay {sin_honorario} registros sin monto de honorario cargado. "
            "No se incluyen como deuda hasta completar el importe."
        )

    # ---------------------------------------------------------
    # RESÚMENES
    # ---------------------------------------------------------
    resumen_medicos = (
        data.groupby("_medico", dropna=False)
        .agg(
            Prestaciones=("_medico", "size"),
            Pacientes=("_paciente", lambda s: s.replace("", pd.NA).nunique()),
            Facturacion=("_facturado", "sum"),
            Honorarios=("_honorario", "sum"),
            Pagado=("_pagado", "sum"),
            Pendiente=("_saldo", "sum"),
        )
        .reset_index()
        .rename(columns={"_medico": "Médico"})
    )
    resumen_medicos["% pagado"] = resumen_medicos.apply(
        lambda row: row["Pagado"] / row["Honorarios"] * 100
        if row["Honorarios"] > 0
        else 0.0,
        axis=1,
    )
    resumen_medicos = resumen_medicos.sort_values(
        ["Pendiente", "Honorarios"],
        ascending=[False, False],
    )

    resumen_procedimientos = (
        data.groupby("_procedimiento", dropna=False)
        .agg(
            Prestaciones=("_procedimiento", "size"),
            Facturacion=("_facturado", "sum"),
            Honorarios=("_honorario", "sum"),
            Pagado=("_pagado", "sum"),
            Pendiente=("_saldo", "sum"),
        )
        .reset_index()
        .rename(columns={"_procedimiento": "Procedimiento"})
        .sort_values("Honorarios", ascending=False)
    )

    resumen_empresas = (
        data.groupby("_empresa", dropna=False)
        .agg(
            Prestaciones=("_empresa", "size"),
            Facturacion=("_facturado", "sum"),
            Honorarios=("_honorario", "sum"),
            Pagado=("_pagado", "sum"),
            Pendiente=("_saldo", "sum"),
        )
        .reset_index()
        .rename(columns={"_empresa": "Empresa"})
        .sort_values("Honorarios", ascending=False)
    )

    tab_medicos, tab_evolucion, tab_procedimientos, tab_control = st.tabs(
        [
            "👨‍⚕️ Médicos",
            "📈 Evolución",
            "🩺 Procedimientos y empresas",
            "🚨 Control y detalle",
        ]
    )

    # ---------------------------------------------------------
    # MÉDICOS
    # ---------------------------------------------------------
    with tab_medicos:
        st.subheader("Resumen por médico")

        tabla_medicos = resumen_medicos.copy()
        for col in ["Facturacion", "Honorarios", "Pagado", "Pendiente"]:
            tabla_medicos[col] = tabla_medicos[col].apply(fmt_ars)
        tabla_medicos["% pagado"] = tabla_medicos["% pagado"].apply(
            lambda x: f"{x:,.1f}%".replace(".", ",")
        )
        tabla_medicos = tabla_medicos.rename(
            columns={"Facturacion": "Facturación asociada"}
        )
        st.dataframe(tabla_medicos, use_container_width=True, hide_index=True)

        top = resumen_medicos.head(12).sort_values("Honorarios", ascending=True)
        if not top.empty:
            fig_medicos = px.bar(
                top,
                x="Honorarios",
                y="Médico",
                orientation="h",
                title="Honorarios generados por médico",
                hover_data=["Prestaciones", "Pagado", "Pendiente"],
            )
            fig_medicos.update_layout(
                xaxis_title="Honorarios",
                yaxis_title="",
                legend_title_text="",
            )
            st.plotly_chart(fig_medicos, use_container_width=True)

        opciones_medicos = [
            medico
            for medico in resumen_medicos["Médico"].astype(str).tolist()
            if medico != "SIN MÉDICO"
        ]
        if opciones_medicos:
            st.markdown("### Ficha individual")
            medico_elegido = st.selectbox(
                "Seleccionar médico",
                opciones_medicos,
                key="honorarios_medico_detalle",
            )
            ficha = data[data["_medico"] == medico_elegido].copy()
            f1, f2, f3, f4 = st.columns(4)
            f1.metric("Prestaciones", len(ficha))
            f2.metric("Honorarios", fmt_ars(ficha["_honorario"].sum()))
            f3.metric("Pagado", fmt_ars(ficha["_pagado"].sum()))
            f4.metric("Pendiente", fmt_ars(ficha["_saldo"].sum()))

            ficha_tabla = pd.DataFrame(
                {
                    "Fecha": ficha["_fecha"].dt.strftime("%d/%m/%Y").fillna(""),
                    "Paciente": ficha["_paciente"],
                    "Procedimiento": ficha["_procedimiento"],
                    "Empresa": ficha["_empresa"],
                    "Facturación asociada": ficha["_facturado"].apply(fmt_ars),
                    "Honorario": ficha["_honorario"].apply(fmt_ars),
                    "Pagado": ficha["_pagado"].apply(fmt_ars),
                    "Pendiente": ficha["_saldo"].apply(fmt_ars),
                    "Estado": ficha["_estado_calculado"],
                }
            )
            st.dataframe(ficha_tabla, use_container_width=True, hide_index=True)

    # ---------------------------------------------------------
    # EVOLUCIÓN MENSUAL
    # ---------------------------------------------------------
    with tab_evolucion:
        st.subheader("Evolución mensual")
        con_fecha = data[data["_fecha"].notna()].copy()
        if con_fecha.empty:
            st.info("No hay fechas válidas para construir la evolución mensual.")
        else:
            con_fecha["Mes"] = con_fecha["_fecha"].dt.to_period("M").astype(str)
            mensual = (
                con_fecha.groupby("Mes", as_index=False)[
                    ["_facturado", "_honorario", "_pagado", "_saldo"]
                ]
                .sum()
                .rename(
                    columns={
                        "_facturado": "Facturación asociada",
                        "_honorario": "Honorarios",
                        "_pagado": "Pagado",
                        "_saldo": "Pendiente",
                    }
                )
                .sort_values("Mes")
            )

            fig_mensual = px.line(
                mensual,
                x="Mes",
                y=["Honorarios", "Pagado", "Pendiente"],
                markers=True,
                title="Honorarios, pagos y saldos por mes",
            )
            fig_mensual.update_layout(
                yaxis_title="Importe",
                xaxis_title="Mes",
                legend_title_text="",
            )
            st.plotly_chart(fig_mensual, use_container_width=True)

            mensual_tabla = mensual.copy()
            for col in ["Facturación asociada", "Honorarios", "Pagado", "Pendiente"]:
                mensual_tabla[col] = mensual_tabla[col].apply(fmt_ars)
            st.dataframe(mensual_tabla, use_container_width=True, hide_index=True)

    # ---------------------------------------------------------
    # PROCEDIMIENTOS Y EMPRESAS
    # ---------------------------------------------------------
    with tab_procedimientos:
        col_proc, col_empresa = st.columns(2)

        with col_proc:
            st.subheader("Por procedimiento")
            tabla_proc = resumen_procedimientos.copy()
            for col in ["Facturacion", "Honorarios", "Pagado", "Pendiente"]:
                tabla_proc[col] = tabla_proc[col].apply(fmt_ars)
            tabla_proc = tabla_proc.rename(
                columns={"Facturacion": "Facturación asociada"}
            )
            st.dataframe(tabla_proc, use_container_width=True, hide_index=True)

        with col_empresa:
            st.subheader("Por empresa")
            tabla_emp = resumen_empresas.copy()
            for col in ["Facturacion", "Honorarios", "Pagado", "Pendiente"]:
                tabla_emp[col] = tabla_emp[col].apply(fmt_ars)
            tabla_emp = tabla_emp.rename(
                columns={"Facturacion": "Facturación asociada"}
            )
            st.dataframe(tabla_emp, use_container_width=True, hide_index=True)

        top_proc = resumen_procedimientos.head(12).sort_values("Honorarios", ascending=True)
        if not top_proc.empty:
            fig_proc = px.bar(
                top_proc,
                x="Honorarios",
                y="Procedimiento",
                orientation="h",
                title="Honorarios por procedimiento",
                hover_data=["Prestaciones", "Pagado", "Pendiente"],
            )
            fig_proc.update_layout(xaxis_title="Honorarios", yaxis_title="")
            st.plotly_chart(fig_proc, use_container_width=True)

    # ---------------------------------------------------------
    # CONTROL, ALERTAS Y DETALLE
    # ---------------------------------------------------------
    with tab_control:
        st.subheader("Control de pagos y calidad de datos")

        campos_duplicado = [
            col
            for col in ["_fecha", "_medico", "_paciente", "_procedimiento"]
            if col in data.columns
        ]
        duplicados = (
            data.duplicated(subset=campos_duplicado, keep=False)
            if campos_duplicado
            else pd.Series(False, index=data.index)
        )
        pagos_excedidos = data["_pagado"] > data["_honorario"]
        sin_medico = data["_medico"].eq("SIN MÉDICO")
        facturacion_cero = (data["_facturado"] <= 0) & (data["_honorario"] > 0)

        controles = st.columns(4)
        controles[0].metric("Sin honorario", int((data["_honorario"] <= 0).sum()))
        controles[1].metric("Pendientes +30 días", pendientes_30)
        controles[2].metric("Posibles duplicados", int(duplicados.sum()))
        controles[3].metric("Pagos mayores al honorario", int(pagos_excedidos.sum()))

        if duplicados.any():
            st.warning(
                "Se detectaron posibles duplicados con igual fecha, médico, paciente y procedimiento."
            )
        if pagos_excedidos.any():
            st.error("Hay registros donde el monto pagado supera el honorario cargado.")
        if sin_medico.any():
            st.warning(f"Hay {int(sin_medico.sum())} registros sin médico identificado.")
        if facturacion_cero.any():
            st.info(
                f"Hay {int(facturacion_cero.sum())} honorarios con facturación asociada en $ 0,00."
            )

        estados = ["Todos", "Pendiente", "Pago parcial", "Pagado", "Sin honorario cargado"]
        estado_elegido = st.selectbox(
            "Mostrar estado",
            estados,
            key="honorarios_estado_detalle",
        )
        detalle = data.copy()
        if estado_elegido != "Todos":
            detalle = detalle[detalle["_estado_calculado"] == estado_elegido]

        detalle = detalle.sort_values(
            ["_saldo", "_fecha"],
            ascending=[False, False],
            na_position="last",
        )
        detalle_tabla = pd.DataFrame(
            {
                "Fecha": detalle["_fecha"].dt.strftime("%d/%m/%Y").fillna(""),
                "Empresa": detalle["_empresa"],
                "Médico": detalle["_medico"],
                "Paciente": detalle["_paciente"],
                "Procedimiento": detalle["_procedimiento"],
                "Facturación asociada": detalle["_facturado"].apply(fmt_ars),
                "Honorario": detalle["_honorario"].apply(fmt_ars),
                "Pagado": detalle["_pagado"].apply(fmt_ars),
                "Pendiente": detalle["_saldo"].apply(fmt_ars),
                "Estado": detalle["_estado_calculado"],
                "Fecha de pago": detalle["_fecha_pago"].dt.strftime("%d/%m/%Y").fillna(""),
            }
        )
        st.dataframe(detalle_tabla, use_container_width=True, hide_index=True)

        exportar = pd.DataFrame(
            {
                "fecha": data["_fecha"].dt.strftime("%Y-%m-%d").fillna(""),
                "empresa": data["_empresa"],
                "medico": data["_medico"],
                "paciente": data["_paciente"],
                "procedimiento": data["_procedimiento"],
                "importe_facturado": data["_facturado"],
                "honorario": data["_honorario"],
                "pagado": data["_pagado"],
                "pendiente": data["_saldo"],
                "estado_calculado": data["_estado_calculado"],
                "fecha_pago": data["_fecha_pago"].dt.strftime("%Y-%m-%d").fillna(""),
            }
        )
        csv = exportar.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
        st.download_button(
            "📥 Descargar análisis de honorarios",
            data=csv,
            file_name="honorarios_medicos_analizados.csv",
            mime="text/csv",
            key="descargar_honorarios_analizados",
        )
def render_banco_pro_panel(
    df: pd.DataFrame,
    module_name: str,
    df_total: pd.DataFrame | None = None,
) -> None:
    """
    Centro bancario profesional para Banco Macro VMR y Banco Galicia VM.

    La función acepta tanto la vista filtrada (``df``) como el historial completo
    (``df_total``). El historial solo se utiliza para reconstruir saldos reales;
    no se modifica, reordena ni escribe ninguna celda de Google Sheets.
    """
    import re
    import unicodedata

    import pandas as pd
    import plotly.express as px
    import plotly.graph_objects as go
    import streamlit as st

    nombre_banco = str(module_name or "Banco").strip()
    es_macro = "macro" in nombre_banco.casefold() or "vmr" in nombre_banco.casefold()
    empresa = "VMR" if es_macro else "VM"
    banco = "Banco Macro" if es_macro else "Banco Galicia"
    clave = re.sub(r"[^a-z0-9]+", "_", nombre_banco.casefold()).strip("_") or "banco"
    hoy = pd.Timestamp.today().normalize()

    st.markdown(
        """
        <style>
        .banco-pro-hero {
            padding: 1.15rem 1.35rem;
            border: 1px solid rgba(120, 130, 150, .24);
            border-radius: 19px;
            background:
                radial-gradient(circle at 92% 10%, rgba(90, 160, 220, .22), transparent 31%),
                linear-gradient(135deg, rgba(14, 27, 48, .98), rgba(24, 52, 79, .94));
            box-shadow: 0 14px 34px rgba(0, 0, 0, .14);
            margin: .15rem 0 1rem 0;
        }
        .banco-pro-kicker {
            color: #a7ccec;
            font-size: .76rem;
            font-weight: 750;
            letter-spacing: .16em;
            text-transform: uppercase;
            margin-bottom: .28rem;
        }
        .banco-pro-title {
            color: white;
            font-size: 1.58rem;
            font-weight: 790;
            line-height: 1.15;
        }
        .banco-pro-subtitle {
            color: rgba(255, 255, 255, .74);
            font-size: .92rem;
            margin-top: .45rem;
        }
        .banco-pro-pill {
            display: inline-block;
            padding: .22rem .56rem;
            border-radius: 999px;
            border: 1px solid rgba(255,255,255,.18);
            background: rgba(255,255,255,.08);
            color: rgba(255,255,255,.82);
            font-size: .74rem;
            margin-top: .7rem;
            margin-right: .35rem;
        }
        .banco-pro-callout {
            border: 1px solid rgba(120, 130, 150, .23);
            border-radius: 14px;
            background: rgba(120, 130, 150, .07);
            padding: .78rem .95rem;
            margin: .45rem 0 .8rem 0;
        }
        .banco-pro-muted { opacity: .76; font-size: .83rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="banco-pro-hero">
            <div class="banco-pro-kicker">Tesorería bancaria · {empresa}</div>
            <div class="banco-pro-title">Centro Bancario · {banco}</div>
            <div class="banco-pro-subtitle">
                Posición real, conciliación, liquidez, evolución, cierres y auditoría automática.
            </div>
            <span class="banco-pro-pill">Lectura segura del Sheet</span>
            <span class="banco-pro-pill">Saldos históricos</span>
            <span class="banco-pro-pill">Control de conciliación</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if df is None or df.empty:
        st.info("No hay movimientos bancarios para analizar con los filtros actuales.")
        return

    def normalizar(valor: object) -> str:
        texto = unicodedata.normalize("NFKD", str(valor or ""))
        texto = "".join(c for c in texto if not unicodedata.combining(c))
        texto = texto.strip().casefold()
        return re.sub(r"[^a-z0-9]+", "_", texto).strip("_")

    def numero(valor: object) -> float:
        if valor is None or isinstance(valor, bool):
            return float(valor or 0)
        if isinstance(valor, (int, float)):
            try:
                return 0.0 if pd.isna(valor) else float(valor)
            except Exception:
                return 0.0
        texto = str(valor).strip()
        if not texto or texto.casefold() in {"nan", "none", "nat", "-"}:
            return 0.0
        negativo_parentesis = texto.startswith("(") and texto.endswith(")")
        texto = re.sub(r"[^0-9,.-]", "", texto)
        if texto in {"", "-", ".", ","}:
            return 0.0
        if "," in texto and "." in texto:
            if texto.rfind(",") > texto.rfind("."):
                texto = texto.replace(".", "").replace(",", ".")
            else:
                texto = texto.replace(",", "")
        elif "," in texto:
            ultima = texto.rsplit(",", 1)[-1]
            texto = texto.replace(".", "")
            texto = texto.replace(",", ".") if len(ultima) in (1, 2) else texto.replace(",", "")
        elif texto.count(".") > 1:
            ultima = texto.rsplit(".", 1)[-1]
            texto = texto.replace(".", "") if len(ultima) not in (1, 2) else texto.rsplit(".", 1)[0].replace(".", "") + "." + ultima
        try:
            resultado = float(texto)
            return -abs(resultado) if negativo_parentesis else resultado
        except Exception:
            return 0.0

    def moneda(valor: float, decimales: int = 2) -> str:
        valor = float(valor or 0.0)
        texto = f"{abs(valor):,.{decimales}f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{'-' if valor < 0 else ''}$ {texto}"

    def porcentaje(valor: float) -> str:
        valor = float(valor or 0.0)
        return f"{valor:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".") + "%"

    def variacion(actual: float, anterior: float) -> float | None:
        return None if anterior == 0 else (actual - anterior) / abs(anterior) * 100

    def serie_unica(base: pd.DataFrame, columna: str | None, defecto: object = "") -> pd.Series:
        if columna is None or columna not in base.columns:
            return pd.Series([defecto] * len(base), index=base.index)
        serie = base.loc[:, columna]
        if isinstance(serie, pd.DataFrame):
            serie = serie.iloc[:, 0]
        return serie

    def fecha_segura(serie: pd.Series) -> pd.Series:
        resultado = pd.to_datetime(serie, format="%Y-%m-%d", errors="coerce")
        pendientes = resultado.isna() & serie.notna() & serie.astype(str).str.strip().ne("")
        if pendientes.any():
            resultado.loc[pendientes] = pd.to_datetime(
                serie.loc[pendientes], errors="coerce", dayfirst=True
            )
        return resultado

    def estado_conciliado(valor: object) -> bool | None:
        if valor is None or (not isinstance(valor, str) and pd.isna(valor)):
            return None
        if isinstance(valor, bool):
            return valor
        if isinstance(valor, (int, float)):
            return bool(valor)
        texto = normalizar(valor)
        if texto in {"1", "si", "s", "yes", "true", "ok", "conciliado", "conciliada", "confirmado", "confirmada"}:
            return True
        if texto in {"0", "no", "n", "false", "pendiente", "sin_conciliar", "no_conciliado", "no_conciliada"}:
            return False
        return None

    def categoria_inferida(concepto: str, categoria_original: str) -> str:
        original = str(categoria_original or "").strip()
        if original and normalizar(original) not in {"nan", "none", "sin_categoria"}:
            return original
        texto = normalizar(concepto)
        reglas = [
            (("comision", "mantenimiento", "gasto_bancario"), "Comisiones bancarias"),
            (("impuesto", "iva", "iibb", "ganancias", "afip", "arca", "debito_ley"), "Impuestos"),
            (("sueldo", "haberes", "nomina"), "Sueldos y cargas"),
            (("proveedor", "factura", "pago_op"), "Proveedores"),
            (("honorario", "medico", "anestesia"), "Honorarios"),
            (("transferencia", "transf", "trf"), "Transferencias"),
            (("tarjeta", "visa", "master", "amex"), "Tarjetas"),
            (("cheque", "echeq"), "Cheques"),
            (("deposito", "cobro", "acreditacion", "liquidacion"), "Cobranzas"),
            (("servicio", "luz", "agua", "gas", "internet", "telefono"), "Servicios"),
        ]
        for palabras, nombre in reglas:
            if any(palabra in texto for palabra in palabras):
                return nombre
        return "Sin categoría"

    def preparar(origen: pd.DataFrame | None) -> tuple[pd.DataFrame, dict[str, str | None]]:
        if origen is None or origen.empty:
            return pd.DataFrame(), {}
        base = origen.copy()
        mapa: dict[str, str] = {}
        for columna in base.columns:
            mapa.setdefault(normalizar(columna), columna)

        def buscar(*opciones: str) -> str | None:
            for opcion in opciones:
                encontrada = mapa.get(normalizar(opcion))
                if encontrada is not None:
                    return encontrada
            return None

        cols = {
            "fecha": buscar("fecha", "fecha_movimiento", "fecha_operacion", "fecha_valor", "dia", "mes"),
            "concepto": buscar("concepto", "descripcion", "detalle", "movimiento", "motivo"),
            "categoria": buscar("categoria", "rubro", "clasificacion"),
            "tipo": buscar("tipo_movimiento", "tipo", "clase", "debito_credito", "movimiento_tipo"),
            "referencia": buscar("referencia", "comprobante", "nro_comprobante", "numero_comprobante", "operacion", "id_operacion"),
            "ingreso": buscar("ingreso", "ingresos", "credito", "creditos", "haber", "acreditacion", "entrada"),
            "egreso": buscar("egreso", "egresos", "debito", "debitos", "debe", "salida"),
            "importe": buscar("importe", "monto", "valor", "valor_pesos", "total"),
            "saldo": buscar("saldo", "saldo_acumulado", "balance", "saldo_actual", "saldo_cuenta"),
            "conciliado": buscar("conciliado", "conciliada", "estado_conciliacion", "conciliacion", "verificado"),
            "cuenta": buscar("cuenta", "numero_cuenta", "nro_cuenta", "cbu", "alias"),
            "responsable": buscar("responsable", "usuario", "operador", "cargado_por"),
            "observaciones": buscar("observaciones", "observacion", "nota", "notas", "comentario"),
        }

        resultado = pd.DataFrame(index=base.index)
        resultado["_orden"] = range(len(base))
        resultado["_indice_origen"] = base.index.astype(str)
        resultado["_fecha"] = fecha_segura(serie_unica(base, cols["fecha"], "")) if cols["fecha"] else pd.NaT
        resultado["_concepto"] = serie_unica(base, cols["concepto"], "Sin concepto").fillna("").astype(str).str.strip().replace("", "Sin concepto")
        resultado["_categoria_original"] = serie_unica(base, cols["categoria"], "").fillna("").astype(str).str.strip()
        resultado["_tipo_origen"] = serie_unica(base, cols["tipo"], "").fillna("").astype(str).str.strip()
        resultado["_referencia"] = serie_unica(base, cols["referencia"], "").fillna("").astype(str).str.strip()
        resultado["_cuenta"] = serie_unica(base, cols["cuenta"], "").fillna("").astype(str).str.strip()
        resultado["_responsable"] = serie_unica(base, cols["responsable"], "").fillna("").astype(str).str.strip()
        resultado["_observaciones"] = serie_unica(base, cols["observaciones"], "").fillna("").astype(str).str.strip()

        ingreso = serie_unica(base, cols["ingreso"], 0).map(numero) if cols["ingreso"] else pd.Series(0.0, index=base.index)
        egreso = serie_unica(base, cols["egreso"], 0).map(numero) if cols["egreso"] else pd.Series(0.0, index=base.index)
        importe = serie_unica(base, cols["importe"], 0).map(numero) if cols["importe"] else pd.Series(0.0, index=base.index)
        tipo_norm = resultado["_tipo_origen"].map(normalizar)

        sin_movimiento = ingreso.eq(0) & egreso.eq(0) & importe.ne(0)
        tipo_ingreso = tipo_norm.str.contains(r"credito|ingreso|entrada|acreditacion|haber|deposito|cobro", regex=True, na=False)
        tipo_egreso = tipo_norm.str.contains(r"debito|egreso|salida|pago|extraccion|debe", regex=True, na=False)
        ingreso.loc[sin_movimiento & (tipo_ingreso | (~tipo_egreso & importe.gt(0)))] = importe.loc[sin_movimiento & (tipo_ingreso | (~tipo_egreso & importe.gt(0)))].abs()
        egreso.loc[sin_movimiento & (tipo_egreso | (~tipo_ingreso & importe.lt(0)))] = importe.loc[sin_movimiento & (tipo_egreso | (~tipo_ingreso & importe.lt(0)))].abs()

        ingreso_negativo = ingreso.lt(0)
        egreso_negativo = egreso.lt(0)
        egreso.loc[ingreso_negativo] = egreso.loc[ingreso_negativo] + ingreso.loc[ingreso_negativo].abs()
        ingreso.loc[ingreso_negativo] = 0.0
        ingreso.loc[egreso_negativo] = ingreso.loc[egreso_negativo] + egreso.loc[egreso_negativo].abs()
        egreso.loc[egreso_negativo] = 0.0

        resultado["_ingreso"] = pd.to_numeric(ingreso, errors="coerce").fillna(0.0)
        resultado["_egreso"] = pd.to_numeric(egreso, errors="coerce").fillna(0.0)
        resultado["_neto"] = resultado["_ingreso"] - resultado["_egreso"]
        resultado["_tipo"] = "Sin movimiento"
        resultado.loc[resultado["_ingreso"].gt(0), "_tipo"] = "Crédito"
        resultado.loc[resultado["_egreso"].gt(0), "_tipo"] = "Débito"
        resultado.loc[resultado["_ingreso"].gt(0) & resultado["_egreso"].gt(0), "_tipo"] = "Mixto"
        resultado["_categoria"] = [
            categoria_inferida(concepto, categoria)
            for concepto, categoria in zip(resultado["_concepto"], resultado["_categoria_original"])
        ]

        if cols["saldo"]:
            saldo_bruto = serie_unica(base, cols["saldo"], "")
            saldo = saldo_bruto.map(numero)
            saldo_valido = saldo_bruto.notna() & saldo_bruto.astype(str).str.strip().ne("")
            resultado["_saldo_origen"] = saldo.where(saldo_valido, pd.NA)
        else:
            resultado["_saldo_origen"] = pd.NA

        if cols["conciliado"]:
            resultado["_conciliado"] = serie_unica(base, cols["conciliado"], "").map(estado_conciliado)
        else:
            resultado["_conciliado"] = pd.Series([None] * len(base), index=base.index, dtype="object")
        resultado["_estado_conciliacion"] = "Sin estado"
        resultado.loc[resultado["_conciliado"].eq(True), "_estado_conciliacion"] = "Conciliado"
        resultado.loc[resultado["_conciliado"].eq(False), "_estado_conciliacion"] = "Pendiente"

        resultado = resultado.sort_values(["_fecha", "_orden"], ascending=[True, True], na_position="last").reset_index(drop=True)
        return resultado, cols

    data, columnas = preparar(df)
    historial, columnas_historial = preparar(df_total if df_total is not None else df)
    if data.empty:
        st.info("No hay movimientos bancarios válidos para analizar.")
        return

    faltantes = []
    if not columnas.get("fecha"):
        faltantes.append("fecha")
    if not columnas.get("ingreso") and not columnas.get("egreso") and not columnas.get("importe"):
        faltantes.append("ingreso/egreso o importe")
    if faltantes:
        st.error("No se pudieron identificar las columnas esenciales: " + ", ".join(faltantes) + ".")
        st.caption("El panel reconoce automáticamente fecha, concepto, referencia, ingreso, egreso, importe, saldo y conciliado.")
        return

    fechas = data["_fecha"].dropna()
    desde = fechas.min().normalize() if not fechas.empty else None
    hasta = fechas.max().normalize() if not fechas.empty else None
    periodo_texto = f"{desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}" if desde is not None and hasta is not None else "sin fechas válidas"

    def ultimo_saldo(origen: pd.DataFrame) -> float | None:
        if origen.empty or "_saldo_origen" not in origen.columns:
            return None
        serie = pd.to_numeric(origen["_saldo_origen"], errors="coerce").dropna()
        return float(serie.iloc[-1]) if not serie.empty else None

    anteriores = historial.iloc[0:0].copy()
    hasta_periodo = historial.copy()
    periodo_historial = data.copy()
    if desde is not None and hasta is not None and not historial.empty:
        anteriores = historial[historial["_fecha"].notna() & historial["_fecha"].lt(desde)].copy()
        hasta_periodo = historial[historial["_fecha"].notna() & historial["_fecha"].le(hasta)].copy()
        periodo_historial = historial[historial["_fecha"].between(desde, hasta, inclusive="both")].copy()

    saldo_previo = ultimo_saldo(anteriores)
    if saldo_previo is not None:
        saldo_inicial = saldo_previo
    else:
        saldo_inicial = float(anteriores["_neto"].sum()) if not anteriores.empty else 0.0
        primeros_saldos = pd.to_numeric(periodo_historial.get("_saldo_origen", pd.Series(dtype=float)), errors="coerce").dropna()
        if anteriores.empty and not primeros_saldos.empty:
            fila_primera = periodo_historial.loc[pd.to_numeric(periodo_historial["_saldo_origen"], errors="coerce").notna()].iloc[0]
            saldo_inicial = float(primeros_saldos.iloc[0]) - float(fila_primera["_neto"])

    ingresos = float(data["_ingreso"].sum())
    egresos = float(data["_egreso"].sum())
    flujo = ingresos - egresos
    flujo_contable = float(periodo_historial["_neto"].sum()) if not periodo_historial.empty else flujo
    saldo_cierre_fuente = ultimo_saldo(hasta_periodo)
    saldo_cierre = saldo_cierre_fuente if saldo_cierre_fuente is not None else saldo_inicial + flujo_contable

    historial_hoy = historial[historial["_fecha"].notna() & historial["_fecha"].le(hoy)].copy()
    if historial_hoy.empty:
        historial_hoy = historial.copy()
    saldo_actual_fuente = ultimo_saldo(historial_hoy)
    saldo_actual = saldo_actual_fuente if saldo_actual_fuente is not None else float(historial_hoy["_neto"].sum())

    conciliados_mask = data["_conciliado"].eq(True)
    pendientes_mask = data["_conciliado"].eq(False)
    sin_estado_mask = data["_conciliado"].isna()
    conciliados = int(conciliados_mask.sum())
    pendientes = int(pendientes_mask.sum())
    sin_estado = int(sin_estado_mask.sum())
    importe_pendiente = float(data.loc[pendientes_mask, "_neto"].abs().sum())
    conciliacion_pct = conciliados / (conciliados + pendientes) * 100 if conciliados + pendientes else 0.0

    cantidad_ingresos = int(data["_ingreso"].gt(0).sum())
    cantidad_egresos = int(data["_egreso"].gt(0).sum())
    ticket_ingreso = ingresos / cantidad_ingresos if cantidad_ingresos else 0.0
    ticket_egreso = egresos / cantidad_egresos if cantidad_egresos else 0.0
    cobertura = ingresos / egresos * 100 if egresos else (100.0 if ingresos else 0.0)

    anterior = pd.DataFrame()
    if desde is not None and hasta is not None and not historial.empty:
        dias = max((hasta - desde).days + 1, 1)
        fin_anterior = desde - pd.Timedelta(days=1)
        inicio_anterior = fin_anterior - pd.Timedelta(days=dias - 1)
        anterior = historial[historial["_fecha"].between(inicio_anterior, fin_anterior, inclusive="both")]
    ingresos_ant = float(anterior["_ingreso"].sum()) if not anterior.empty else 0.0
    egresos_ant = float(anterior["_egreso"].sum()) if not anterior.empty else 0.0
    flujo_ant = ingresos_ant - egresos_ant
    var_ingresos = variacion(ingresos, ingresos_ant) if not anterior.empty else None
    var_egresos = variacion(egresos, egresos_ant) if not anterior.empty else None
    var_flujo = flujo - flujo_ant if not anterior.empty else None

    sin_fecha = int(data["_fecha"].isna().sum())
    sin_concepto = int(data["_concepto"].eq("Sin concepto").sum())
    sin_referencia = int(data["_referencia"].eq("").sum())
    sin_movimiento = int(data["_ingreso"].eq(0).mul(data["_egreso"].eq(0)).sum())
    mixtos = int(data["_ingreso"].gt(0).mul(data["_egreso"].gt(0)).sum())
    futuros = int(data["_fecha"].gt(hoy).sum())
    duplicados_mask = data.duplicated(subset=["_fecha", "_concepto", "_ingreso", "_egreso", "_referencia"], keep=False)
    duplicados = int(duplicados_mask.sum())

    data_saldo = data.copy()
    data_saldo["_saldo_calculado"] = saldo_inicial + data_saldo["_neto"].cumsum()
    data_saldo["_saldo_informado_num"] = pd.to_numeric(data_saldo["_saldo_origen"], errors="coerce")
    data_saldo["_diferencia_saldo"] = data_saldo["_saldo_informado_num"] - data_saldo["_saldo_calculado"]
    diferencias_saldo = int(data_saldo["_diferencia_saldo"].abs().gt(1.0).fillna(False).sum())

    alertas = sin_fecha + sin_concepto + sin_movimiento + mixtos + futuros + duplicados + diferencias_saldo
    calidad = max(0.0, 100.0 - alertas / max(len(data) * 4, 1) * 100)
    score = 0.0
    score += 25 if saldo_actual >= 0 else max(0.0, 25 + saldo_actual / max(egresos, 1) * 25)
    score += min(max(cobertura, 0), 120) / 120 * 25
    score += conciliacion_pct / 100 * 25 if conciliados + pendientes else 12.5
    score += calidad / 100 * 15
    score += 10 if flujo >= 0 else max(0.0, 10 - abs(flujo) / max(egresos, 1) * 10)
    score = round(min(100.0, max(0.0, score)))
    if score >= 82:
        estado, icono = "Sólido", "🟢"
    elif score >= 65:
        estado, icono = "Controlado", "🟡"
    else:
        estado, icono = "Requiere atención", "🔴"

    st.caption(
        f"Período analizado: {periodo_texto} · Historial para saldos: {len(historial):,} movimientos".replace(",", ".")
    )

    k1, k2, k3 = st.columns(3)
    k1.metric("🏦 Saldo bancario actual", moneda(saldo_actual), help="Último saldo informado hasta hoy; si no existe, se reconstruye con todo el historial.")
    k2.metric("⏮️ Saldo inicial", moneda(saldo_inicial), help="Saldo anterior al primer día incluido en los filtros.")
    k3.metric("⏭️ Saldo al cierre", moneda(saldo_cierre), delta=moneda(saldo_cierre - saldo_inicial))

    k4, k5, k6 = st.columns(3)
    k4.metric("📥 Créditos", moneda(ingresos), delta=f"{var_ingresos:+.1f}% vs. período anterior" if var_ingresos is not None else None)
    k5.metric("📤 Débitos", moneda(egresos), delta=f"{var_egresos:+.1f}% vs. período anterior" if var_egresos is not None else None, delta_color="inverse")
    k6.metric("💹 Flujo neto", moneda(flujo), delta=moneda(var_flujo) + " vs. período anterior" if var_flujo is not None else None)

    k7, k8, k9 = st.columns(3)
    k7.metric("✅ Conciliación", porcentaje(conciliacion_pct), f"{conciliados} conciliados")
    k8.metric("⏳ Pendiente de conciliar", moneda(importe_pendiente), f"{pendientes} movimientos")
    k9.metric("🧾 Movimientos", f"{len(data):,}".replace(",", "."), f"{cantidad_ingresos} créditos · {cantidad_egresos} débitos")

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Índice bancario", f"{score}/100", estado)
    s2.metric("Cobertura", porcentaje(cobertura), "Créditos / débitos")
    s3.metric("Ticket crédito", moneda(ticket_ingreso))
    s4.metric("Ticket débito", moneda(ticket_egreso))

    resumen_estado = (
        f"{icono} **Banco {estado.casefold()}.** El período tuvo un flujo de {moneda(flujo)}, "
        f"con saldo actual de {moneda(saldo_actual)} y conciliación de {porcentaje(conciliacion_pct)}."
    )
    if score >= 82:
        st.success(resumen_estado)
    elif score >= 65:
        st.warning(resumen_estado)
    else:
        st.error(resumen_estado)

    if sin_estado and not columnas.get("conciliado"):
        st.info("La hoja no tiene una columna de conciliación. El resto del panel funciona normalmente; para controlar extracto contra sistema podés agregar `conciliado` con valores Sí/No.")

    tabs = st.tabs([
        "🏛️ Resumen ejecutivo",
        "📈 Evolución y cierres",
        "✅ Conciliación",
        "🔭 Proyección",
        "🧾 Movimientos",
        "🚨 Auditoría",
    ])

    with tabs[0]:
        izquierda, derecha = st.columns([1.25, 1])
        with izquierda:
            st.markdown("### Puente de saldo")
            fig = go.Figure(go.Waterfall(
                orientation="v",
                measure=["absolute", "relative", "relative", "total"],
                x=["Saldo inicial", "Créditos", "Débitos", "Saldo final"],
                y=[saldo_inicial, ingresos, -egresos, saldo_cierre],
                text=[moneda(saldo_inicial, 0), moneda(ingresos, 0), moneda(-egresos, 0), moneda(saldo_cierre, 0)],
                textposition="outside",
                connector={"line": {"width": 1}},
            ))
            fig.update_layout(height=410, margin=dict(l=10, r=10, t=15, b=10), yaxis_title="Importe")
            st.plotly_chart(fig, use_container_width=True)
        with derecha:
            st.markdown("### Lectura ejecutiva")
            categorias = data.groupby("_categoria").agg(Créditos=("_ingreso", "sum"), Débitos=("_egreso", "sum"), Movimientos=("_neto", "size")).reset_index().rename(columns={"_categoria": "Categoría"})
            top_debito = categorias.sort_values("Débitos", ascending=False).iloc[0] if not categorias.empty else None
            puntos = [
                f"**Resultado:** {'superávit' if flujo >= 0 else 'déficit'} de {moneda(abs(flujo))}.",
                f"**Liquidez:** saldo actual {moneda(saldo_actual)}; cobertura {porcentaje(cobertura)}.",
                f"**Conciliación:** {conciliados} conciliados, {pendientes} pendientes y {sin_estado} sin estado.",
                f"**Calidad:** {porcentaje(calidad)} con {alertas} alertas automáticas.",
            ]
            if top_debito is not None and float(top_debito["Débitos"]) > 0:
                puntos.append(f"**Principal salida:** {top_debito['Categoría']} por {moneda(float(top_debito['Débitos']))}.")
            for punto in puntos:
                st.markdown(f"- {punto}")
            if saldo_actual < 0:
                st.error("La cuenta presenta saldo negativo. Revisá sobregiros, movimientos omitidos y el último saldo informado.")
            elif flujo < 0:
                st.warning("El banco conserva saldo, pero el período consumió liquidez.")
            elif pendientes > 0:
                st.info("La posición es positiva; quedan movimientos pendientes de conciliación.")
            else:
                st.success("La cuenta muestra flujo positivo y control bancario sin pendientes detectados.")

        categorias = data.groupby("_categoria").agg(Créditos=("_ingreso", "sum"), Débitos=("_egreso", "sum"), Movimientos=("_neto", "size")).reset_index().rename(columns={"_categoria": "Categoría"})
        categorias["Neto"] = categorias["Créditos"] - categorias["Débitos"]
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("### Débitos por categoría")
            deb = categorias[categorias["Débitos"].gt(0)].sort_values("Débitos", ascending=True).tail(12)
            if deb.empty:
                st.info("No hay débitos en el período.")
            else:
                fig = px.bar(deb, x="Débitos", y="Categoría", orientation="h", text_auto=".2s")
                fig.update_layout(height=405, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="")
                st.plotly_chart(fig, use_container_width=True)
        with c2:
            st.markdown("### Créditos por categoría")
            cre = categorias[categorias["Créditos"].gt(0)].sort_values("Créditos", ascending=True).tail(12)
            if cre.empty:
                st.info("No hay créditos en el período.")
            else:
                fig = px.bar(cre, x="Créditos", y="Categoría", orientation="h", text_auto=".2s")
                fig.update_layout(height=405, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="")
                st.plotly_chart(fig, use_container_width=True)

        tabla_cat = categorias.sort_values("Movimientos", ascending=False).copy()
        for col in ["Créditos", "Débitos", "Neto"]:
            tabla_cat[col] = tabla_cat[col].map(moneda)
        st.markdown("### Composición de la cuenta")
        st.dataframe(tabla_cat, use_container_width=True, hide_index=True)

    with tabs[1]:
        validas = data.dropna(subset=["_fecha"]).copy()
        if validas.empty:
            st.info("No hay fechas válidas para construir la evolución bancaria.")
        else:
            diario = validas.assign(Fecha=validas["_fecha"].dt.normalize()).groupby("Fecha").agg(Créditos=("_ingreso", "sum"), Débitos=("_egreso", "sum"), Movimientos=("_neto", "size")).reset_index().sort_values("Fecha")
            diario["Flujo neto"] = diario["Créditos"] - diario["Débitos"]
            diario["Saldo calculado"] = saldo_inicial + diario["Flujo neto"].cumsum()
            diario["Tendencia 7 días"] = diario["Flujo neto"].rolling(7, min_periods=1).mean()

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=diario["Fecha"], y=diario["Saldo calculado"], mode="lines+markers", name="Saldo"))
            fig.add_trace(go.Scatter(x=diario["Fecha"], y=diario["Tendencia 7 días"], mode="lines", name="Tendencia neta 7 días", yaxis="y2"))
            fig.update_layout(height=430, title="Evolución del saldo bancario", margin=dict(l=10, r=10, t=55, b=10), yaxis=dict(title="Saldo"), yaxis2=dict(title="Flujo promedio", overlaying="y", side="right", showgrid=False), legend=dict(orientation="h", y=1.08))
            st.plotly_chart(fig, use_container_width=True)

            largo = diario.melt(id_vars="Fecha", value_vars=["Créditos", "Débitos"], var_name="Tipo", value_name="Importe")
            fig = px.bar(largo, x="Fecha", y="Importe", color="Tipo", barmode="group", title="Créditos y débitos por día")
            fig.update_layout(height=395, margin=dict(l=10, r=10, t=55, b=10))
            st.plotly_chart(fig, use_container_width=True)

            mensual = validas.assign(Mes=validas["_fecha"].dt.to_period("M").astype(str)).groupby("Mes").agg(Créditos=("_ingreso", "sum"), Débitos=("_egreso", "sum"), Movimientos=("_neto", "size")).reset_index()
            mensual["Flujo neto"] = mensual["Créditos"] - mensual["Débitos"]
            mensual["Cobertura"] = mensual.apply(lambda fila: fila["Créditos"] / fila["Débitos"] * 100 if fila["Débitos"] else (100.0 if fila["Créditos"] else 0.0), axis=1)
            tabla_mensual = mensual.sort_values("Mes", ascending=False).copy()
            for col in ["Créditos", "Débitos", "Flujo neto"]:
                tabla_mensual[col] = tabla_mensual[col].map(moneda)
            tabla_mensual["Cobertura"] = tabla_mensual["Cobertura"].map(porcentaje)
            st.markdown("### Cierre mensual")
            st.dataframe(tabla_mensual, use_container_width=True, hide_index=True)

            tabla_diaria = diario.sort_values("Fecha", ascending=False).head(45).copy()
            tabla_diaria["Fecha"] = tabla_diaria["Fecha"].dt.strftime("%d/%m/%Y")
            for col in ["Créditos", "Débitos", "Flujo neto", "Saldo calculado", "Tendencia 7 días"]:
                tabla_diaria[col] = tabla_diaria[col].map(moneda)
            st.markdown("### Últimos cierres diarios")
            st.dataframe(tabla_diaria, use_container_width=True, hide_index=True)

    with tabs[2]:
        st.markdown("### Control de conciliación bancaria")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Conciliados", conciliados)
        c2.metric("Pendientes", pendientes)
        c3.metric("Sin estado", sin_estado)
        c4.metric("Importe pendiente", moneda(importe_pendiente))

        estado_resumen = data.groupby("_estado_conciliacion").agg(Movimientos=("_neto", "size"), Importe=("_neto", lambda s: s.abs().sum()), Neto=("_neto", "sum")).reset_index().rename(columns={"_estado_conciliacion": "Estado"})
        fig = px.bar(estado_resumen, x="Estado", y="Importe", text_auto=".2s", title="Importe controlado por estado")
        fig.update_layout(height=370, margin=dict(l=10, r=10, t=55, b=10))
        st.plotly_chart(fig, use_container_width=True)

        pendientes_df = data[pendientes_mask].copy()
        if pendientes_df.empty:
            st.success("No hay movimientos marcados como pendientes de conciliación.")
        else:
            pendientes_df["_antiguedad"] = (hoy - pendientes_df["_fecha"].dt.normalize()).dt.days
            vencidos_30 = int(pendientes_df["_antiguedad"].gt(30).fillna(False).sum())
            vencidos_60 = int(pendientes_df["_antiguedad"].gt(60).fillna(False).sum())
            a1, a2, a3 = st.columns(3)
            a1.metric("Pendientes +30 días", vencidos_30)
            a2.metric("Pendientes +60 días", vencidos_60)
            a3.metric("Pendiente más antiguo", f"{int(pendientes_df['_antiguedad'].max())} días" if pendientes_df["_antiguedad"].notna().any() else "Sin fecha")

            detalle = pd.DataFrame({
                "Fecha": pendientes_df["_fecha"].dt.strftime("%d/%m/%Y").fillna(""),
                "Antigüedad": pendientes_df["_antiguedad"].fillna(0).astype(int),
                "Tipo": pendientes_df["_tipo"],
                "Concepto": pendientes_df["_concepto"],
                "Referencia": pendientes_df["_referencia"],
                "Categoría": pendientes_df["_categoria"],
                "Importe": pendientes_df["_neto"].abs().map(moneda),
            }).sort_values("Antigüedad", ascending=False)
            st.dataframe(detalle, use_container_width=True, hide_index=True)

        estado_tabla = estado_resumen.copy()
        for col in ["Importe", "Neto"]:
            estado_tabla[col] = estado_tabla[col].map(moneda)
        st.markdown("### Resumen de conciliación")
        st.dataframe(estado_tabla, use_container_width=True, hide_index=True)

    with tabs[3]:
        st.markdown("### Proyección simple de liquidez")
        validas = data.dropna(subset=["_fecha"]).copy()
        if validas.empty or desde is None or hasta is None:
            st.info("No hay fechas suficientes para proyectar.")
        else:
            dias_periodo = max((hasta - desde).days + 1, 1)
            ingreso_dia = ingresos / dias_periodo
            egreso_dia = egresos / dias_periodo
            neto_dia = flujo / dias_periodo
            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Crédito diario", moneda(ingreso_dia))
            p2.metric("Débito diario", moneda(egreso_dia))
            p3.metric("Neto diario", moneda(neto_dia))
            p4.metric("Días analizados", dias_periodo)

            horizonte = st.slider("Horizonte de proyección", 7, 120, 30, key=f"{clave}_horizonte")
            dias_futuros = list(range(horizonte + 1))
            escenarios = pd.DataFrame({
                "Día": dias_futuros * 3,
                "Escenario": ["Base"] * len(dias_futuros) + ["Prudente"] * len(dias_futuros) + ["Exigente"] * len(dias_futuros),
                "Saldo proyectado": (
                    [saldo_actual + neto_dia * d for d in dias_futuros]
                    + [saldo_actual + (ingreso_dia * .9 - egreso_dia * 1.05) * d for d in dias_futuros]
                    + [saldo_actual + (ingreso_dia * .8 - egreso_dia * 1.15) * d for d in dias_futuros]
                ),
            })
            fig = px.line(escenarios, x="Día", y="Saldo proyectado", color="Escenario", title=f"Saldo proyectado a {horizonte} días")
            fig.add_hline(y=0, line_dash="dash")
            fig.update_layout(height=420, margin=dict(l=10, r=10, t=55, b=10))
            st.plotly_chart(fig, use_container_width=True)

            cierre_base = saldo_actual + neto_dia * horizonte
            cierre_prudente = saldo_actual + (ingreso_dia * .9 - egreso_dia * 1.05) * horizonte
            cierre_exigente = saldo_actual + (ingreso_dia * .8 - egreso_dia * 1.15) * horizonte
            f1, f2, f3 = st.columns(3)
            f1.metric("Escenario base", moneda(cierre_base))
            f2.metric("Escenario prudente", moneda(cierre_prudente))
            f3.metric("Escenario exigente", moneda(cierre_exigente))
            st.caption("La proyección es operativa y utiliza el ritmo promedio del período filtrado; no reemplaza una programación de pagos y cobranzas futuras.")

    with tabs[4]:
        st.markdown("### Explorador de movimientos")
        f1, f2, f3 = st.columns([1.45, 1, 1])
        buscar_texto = f1.text_input("Buscar", placeholder="Concepto, referencia, categoría…", key=f"{clave}_buscar")
        tipos = f2.multiselect("Tipo", ["Crédito", "Débito", "Mixto", "Sin movimiento"], key=f"{clave}_tipos")
        estados = f3.multiselect("Conciliación", ["Conciliado", "Pendiente", "Sin estado"], key=f"{clave}_estados")
        categorias_disponibles = sorted(data["_categoria"].dropna().astype(str).unique().tolist())
        categorias_sel = st.multiselect("Categorías", categorias_disponibles, key=f"{clave}_categorias")

        detalle = data.copy()
        if buscar_texto.strip():
            patron = re.escape(buscar_texto.strip())
            mascara = (
                detalle["_concepto"].str.contains(patron, case=False, na=False)
                | detalle["_referencia"].str.contains(patron, case=False, na=False)
                | detalle["_categoria"].str.contains(patron, case=False, na=False)
                | detalle["_observaciones"].str.contains(patron, case=False, na=False)
            )
            detalle = detalle[mascara]
        if tipos:
            detalle = detalle[detalle["_tipo"].isin(tipos)]
        if estados:
            detalle = detalle[detalle["_estado_conciliacion"].isin(estados)]
        if categorias_sel:
            detalle = detalle[detalle["_categoria"].isin(categorias_sel)]

        st.caption(f"{len(detalle):,} movimientos visibles".replace(",", "."))
        detalle = detalle.sort_values(["_fecha", "_orden"], ascending=[False, False], na_position="last")
        tabla = pd.DataFrame({
            "Fecha": detalle["_fecha"].dt.strftime("%d/%m/%Y").fillna(""),
            "Tipo": detalle["_tipo"],
            "Concepto": detalle["_concepto"],
            "Categoría": detalle["_categoria"],
            "Referencia": detalle["_referencia"],
            "Crédito": detalle["_ingreso"],
            "Débito": detalle["_egreso"],
            "Neto": detalle["_neto"],
            "Conciliación": detalle["_estado_conciliacion"],
            "Responsable": detalle["_responsable"],
            "Observaciones": detalle["_observaciones"],
        })
        st.dataframe(
            tabla,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Crédito": st.column_config.NumberColumn("Crédito", format="$ %.2f"),
                "Débito": st.column_config.NumberColumn("Débito", format="$ %.2f"),
                "Neto": st.column_config.NumberColumn("Neto", format="$ %.2f"),
            },
        )
        exportable = tabla.copy()
        csv = exportable.to_csv(index=False, sep=";", decimal=",", encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button("📥 Descargar movimientos filtrados", data=csv, file_name=f"{clave}_movimientos.csv", mime="text/csv", key=f"{clave}_descarga_movimientos")

    with tabs[5]:
        st.markdown("### Auditoría automática")
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Calidad de datos", porcentaje(calidad))
        a2.metric("Posibles duplicados", duplicados)
        a3.metric("Diferencias de saldo", diferencias_saldo)
        a4.metric("Fechas futuras", futuros)
        a5, a6, a7, a8 = st.columns(4)
        a5.metric("Sin fecha", sin_fecha)
        a6.metric("Sin concepto", sin_concepto)
        a7.metric("Sin referencia", sin_referencia)
        a8.metric("Sin movimiento / mixtos", sin_movimiento + mixtos)

        problemas_lista = []
        if duplicados:
            problemas_lista.append(f"{duplicados} filas forman parte de posibles movimientos duplicados.")
        if diferencias_saldo:
            problemas_lista.append(f"{diferencias_saldo} saldos informados difieren del saldo reconstruido en más de $ 1.")
        if futuros:
            problemas_lista.append(f"{futuros} movimientos tienen una fecha posterior a hoy.")
        if sin_fecha:
            problemas_lista.append(f"{sin_fecha} movimientos no tienen una fecha válida.")
        if sin_movimiento:
            problemas_lista.append(f"{sin_movimiento} filas no contienen crédito ni débito.")
        if mixtos:
            problemas_lista.append(f"{mixtos} filas contienen crédito y débito simultáneamente.")
        if pendientes:
            problemas_lista.append(f"{pendientes} movimientos siguen pendientes de conciliación.")
        if not problemas_lista:
            st.success("No se detectaron inconsistencias relevantes en el período.")
        else:
            for problema in problemas_lista:
                st.warning(problema)

        auditoria = data_saldo.copy()
        auditoria["_alerta"] = ""
        auditoria.loc[duplicados_mask.reindex(auditoria.index, fill_value=False), "_alerta"] += "Posible duplicado · "
        auditoria.loc[auditoria["_fecha"].isna(), "_alerta"] += "Fecha inválida · "
        auditoria.loc[auditoria["_fecha"].gt(hoy), "_alerta"] += "Fecha futura · "
        auditoria.loc[auditoria["_concepto"].eq("Sin concepto"), "_alerta"] += "Sin concepto · "
        auditoria.loc[auditoria["_ingreso"].eq(0) & auditoria["_egreso"].eq(0), "_alerta"] += "Sin movimiento · "
        auditoria.loc[auditoria["_ingreso"].gt(0) & auditoria["_egreso"].gt(0), "_alerta"] += "Crédito y débito · "
        auditoria.loc[auditoria["_diferencia_saldo"].abs().gt(1.0).fillna(False), "_alerta"] += "Diferencia de saldo · "
        auditoria.loc[auditoria["_conciliado"].eq(False), "_alerta"] += "Pendiente de conciliación · "
        auditoria["_alerta"] = auditoria["_alerta"].str.rstrip(" ·")
        auditoria = auditoria[auditoria["_alerta"].ne("")].sort_values(["_fecha", "_orden"], ascending=[False, False], na_position="last")
        if not auditoria.empty:
            tabla_aud = pd.DataFrame({
                "Alerta": auditoria["_alerta"],
                "Fecha": auditoria["_fecha"].dt.strftime("%d/%m/%Y").fillna(""),
                "Concepto": auditoria["_concepto"],
                "Referencia": auditoria["_referencia"],
                "Crédito": auditoria["_ingreso"].map(moneda),
                "Débito": auditoria["_egreso"].map(moneda),
                "Saldo informado": auditoria["_saldo_informado_num"].map(lambda x: moneda(x) if pd.notna(x) else ""),
                "Saldo calculado": auditoria["_saldo_calculado"].map(moneda),
                "Diferencia": auditoria["_diferencia_saldo"].map(lambda x: moneda(x) if pd.notna(x) else ""),
            })
            st.dataframe(tabla_aud, use_container_width=True, hide_index=True)

        columnas_reconocidas = pd.DataFrame({
            "Dato": ["Fecha", "Concepto", "Categoría", "Tipo", "Referencia", "Crédito", "Débito", "Importe único", "Saldo", "Conciliado", "Cuenta", "Responsable", "Observaciones"],
            "Columna detectada": [columnas.get("fecha"), columnas.get("concepto"), columnas.get("categoria"), columnas.get("tipo"), columnas.get("referencia"), columnas.get("ingreso"), columnas.get("egreso"), columnas.get("importe"), columnas.get("saldo"), columnas.get("conciliado"), columnas.get("cuenta"), columnas.get("responsable"), columnas.get("observaciones")],
        }).fillna("No encontrada")
        with st.expander("Ver diagnóstico de columnas del Sheet"):
            st.dataframe(columnas_reconocidas, use_container_width=True, hide_index=True)
            st.caption("El diagnóstico es informativo: el panel no renombra ni modifica encabezados del Google Sheet.")

def render_caja_pro_panel(
    df: pd.DataFrame,
    module_name: str,
    df_total: pd.DataFrame | None = None,
) -> None:
    """
    Centro de control profesional para Caja VM y Caja VMR.

    - ``df`` contiene la vista filtrada elegida por el usuario.
    - ``df_total`` contiene el historial completo de la misma hoja y se utiliza
      exclusivamente para calcular saldos de apertura/cierre reales.
    - La función es de solo lectura: no modifica ni reordena Google Sheets.
    """
    import re
    import unicodedata

    import pandas as pd
    import plotly.express as px
    import plotly.graph_objects as go
    import streamlit as st

    nombre_caja = str(module_name or "Caja").strip()
    empresa = "VMR" if "vmr" in nombre_caja.lower() else "VM"
    clave_widget = re.sub(r"[^a-z0-9]+", "_", nombre_caja.lower()).strip("_") or "caja"
    hoy = pd.Timestamp.today().normalize()

    st.markdown(
        """
        <style>
        .caja-pro-hero {
            padding: 1.15rem 1.3rem;
            border: 1px solid rgba(120, 130, 150, .22);
            border-radius: 18px;
            background: linear-gradient(135deg, rgba(19,31,52,.96), rgba(28,51,78,.90));
            box-shadow: 0 12px 30px rgba(0,0,0,.12);
            margin: .15rem 0 1rem 0;
        }
        .caja-pro-kicker {
            font-size: .76rem;
            letter-spacing: .16em;
            text-transform: uppercase;
            color: #9fc6ee;
            font-weight: 700;
            margin-bottom: .3rem;
        }
        .caja-pro-title {
            font-size: 1.55rem;
            line-height: 1.15;
            color: #ffffff;
            font-weight: 780;
            margin: 0;
        }
        .caja-pro-subtitle {
            color: rgba(255,255,255,.72);
            margin-top: .45rem;
            font-size: .92rem;
        }
        .caja-pro-note {
            padding: .8rem 1rem;
            border-radius: 13px;
            border: 1px solid rgba(120,130,150,.22);
            background: rgba(120,130,150,.07);
            margin: .45rem 0 .8rem 0;
        }
        .caja-pro-small {
            font-size: .82rem;
            opacity: .78;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="caja-pro-hero">
            <div class="caja-pro-kicker">Tesorería y control financiero · {empresa}</div>
            <div class="caja-pro-title">Centro de Control · {nombre_caja}</div>
            <div class="caja-pro-subtitle">
                Posición real, flujo operativo, cierres, proyección, conciliación y auditoría de movimientos.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if df is None or df.empty:
        st.info("No hay movimientos de caja para analizar con los filtros actuales.")
        return

    def normalizar_texto(valor: object) -> str:
        texto = unicodedata.normalize("NFKD", str(valor or ""))
        texto = "".join(c for c in texto if not unicodedata.combining(c))
        texto = texto.strip().lower()
        return re.sub(r"[^a-z0-9]+", "_", texto).strip("_")

    def numero(valor: object) -> float:
        if valor is None:
            return 0.0
        if isinstance(valor, bool):
            return float(valor)
        if isinstance(valor, (int, float)):
            try:
                return 0.0 if pd.isna(valor) else float(valor)
            except Exception:
                return 0.0

        texto = str(valor).strip()
        if not texto or texto.lower() in {"nan", "none", "nat"}:
            return 0.0

        negativo_parentesis = texto.startswith("(") and texto.endswith(")")
        texto = re.sub(r"[^0-9,.-]", "", texto)
        if texto in {"", "-", ".", ","}:
            return 0.0

        if "," in texto and "." in texto:
            if texto.rfind(",") > texto.rfind("."):
                texto = texto.replace(".", "").replace(",", ".")
            else:
                texto = texto.replace(",", "")
        elif "," in texto:
            partes = texto.split(",")
            texto = texto.replace(".", "")
            texto = texto.replace(",", ".") if len(partes[-1]) in (1, 2) else texto.replace(",", "")
        elif texto.count(".") > 1:
            partes = texto.split(".")
            texto = "".join(partes[:-1]) + "." + partes[-1] if len(partes[-1]) in (1, 2) else "".join(partes)

        try:
            resultado = float(texto)
            return -abs(resultado) if negativo_parentesis else resultado
        except Exception:
            return 0.0

    def moneda(valor: float, decimales: int = 2) -> str:
        valor = float(valor or 0.0)
        texto = f"{abs(valor):,.{decimales}f}"
        texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{'-' if valor < 0 else ''}$ {texto}"

    def porcentaje(valor: float) -> str:
        valor = 0.0 if pd.isna(valor) else float(valor)
        texto = f"{valor:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{texto}%"

    def variacion(actual: float, anterior: float) -> float | None:
        if anterior == 0:
            return None
        return (actual - anterior) / abs(anterior) * 100

    def preparar(origen: pd.DataFrame | None) -> tuple[pd.DataFrame, dict[str, str | None]]:
        if origen is None or origen.empty:
            return pd.DataFrame(), {}

        base = origen.copy()
        columnas_normalizadas: dict[str, str] = {}
        for col in base.columns:
            columnas_normalizadas.setdefault(normalizar_texto(col), col)

        def buscar(*opciones: str) -> str | None:
            for opcion in opciones:
                encontrada = columnas_normalizadas.get(normalizar_texto(opcion))
                if encontrada is not None:
                    return encontrada
            return None

        cols = {
            "fecha": buscar("fecha", "fecha_movimiento", "fecha_operacion", "dia", "mes"),
            "concepto": buscar("concepto", "descripcion", "detalle", "movimiento", "motivo"),
            "categoria": buscar("categoria", "rubro", "clasificacion"),
            "tipo": buscar("tipo_movimiento", "tipo", "clase_movimiento"),
            "medio": buscar("medio", "medio_pago", "forma_pago", "metodo_pago", "canal"),
            "ingreso": buscar("ingreso", "ingresos", "entrada", "entradas", "haber", "credito", "monto_ingreso"),
            "egreso": buscar("egreso", "egresos", "salida", "salidas", "debe", "debito", "monto_egreso"),
            "monto": buscar("monto", "importe", "valor", "valor_pesos", "total"),
            "responsable": buscar("responsable", "usuario", "cargado_por", "operador"),
            "observaciones": buscar("observaciones", "observacion", "notas", "nota", "comentario"),
            "comprobante": buscar("comprobante", "nro_comprobante", "numero_comprobante", "referencia", "recibo"),
            "saldo": buscar("saldo", "balance", "saldo_acumulado", "saldo_actual", "caja_actual"),
        }

        resultado = pd.DataFrame(index=base.index)
        resultado["_indice_origen"] = base.index.astype(str)
        resultado["_orden_original"] = range(len(base))

        if cols["fecha"]:
            serie_fecha = base[cols["fecha"]]
            # Primero resuelve el formato ISO usado por Google Sheets y luego
            # aplica dayfirst únicamente a los valores restantes.
            fecha = pd.to_datetime(serie_fecha, format="%Y-%m-%d", errors="coerce")
            pendientes_fecha = fecha.isna() & serie_fecha.notna()
            if pendientes_fecha.any():
                fecha.loc[pendientes_fecha] = pd.to_datetime(
                    serie_fecha.loc[pendientes_fecha],
                    errors="coerce",
                    dayfirst=True,
                )
            resultado["_fecha"] = fecha
        else:
            resultado["_fecha"] = pd.NaT

        def texto_col(nombre: str, defecto: str) -> pd.Series:
            col = cols.get(nombre)
            if col:
                serie = base[col].fillna("").astype(str).str.strip()
                return serie.mask(serie.eq(""), defecto)
            return pd.Series(defecto, index=base.index, dtype="object")

        resultado["_concepto"] = texto_col("concepto", "Sin concepto")
        resultado["_categoria"] = texto_col("categoria", "Sin categoría")
        resultado["_tipo_origen"] = texto_col("tipo", "")
        resultado["_medio"] = texto_col("medio", "Sin medio")
        resultado["_responsable"] = texto_col("responsable", "Sin responsable")
        resultado["_observaciones"] = texto_col("observaciones", "")
        resultado["_comprobante"] = texto_col("comprobante", "")

        resultado["_ingreso"] = base[cols["ingreso"]].map(numero) if cols["ingreso"] else 0.0
        resultado["_egreso"] = base[cols["egreso"]].map(numero) if cols["egreso"] else 0.0
        resultado["_sin_clasificar"] = 0.0

        if not cols["ingreso"] and not cols["egreso"] and cols["monto"]:
            montos = base[cols["monto"]].map(numero)
            clasificador = (
                resultado["_tipo_origen"].astype(str)
                + " " + resultado["_categoria"].astype(str)
                + " " + resultado["_concepto"].astype(str)
            ).map(normalizar_texto)

            patron_ingreso = r"ingreso|entrada|cobro|venta|aporte|deposito|recibido|reintegro|devolucion_a_favor|transferencia_recibida"
            patron_egreso = r"egreso|salida|pago|gasto|compra|retiro|honorario|impuesto|servicio|proveedor|transferencia_enviada"
            es_ingreso = clasificador.str.contains(patron_ingreso, regex=True, na=False)
            es_egreso = clasificador.str.contains(patron_egreso, regex=True, na=False)
            por_signo_ingreso = (~es_ingreso & ~es_egreso) & montos.gt(0)
            por_signo_egreso = (~es_ingreso & ~es_egreso) & montos.lt(0)

            resultado.loc[es_ingreso | por_signo_ingreso, "_ingreso"] = montos[es_ingreso | por_signo_ingreso].abs()
            resultado.loc[es_egreso | por_signo_egreso, "_egreso"] = montos[es_egreso | por_signo_egreso].abs()
            sin_clase = ~(es_ingreso | es_egreso | por_signo_ingreso | por_signo_egreso)
            resultado.loc[sin_clase, "_sin_clasificar"] = montos[sin_clase]

        ingreso_negativo = resultado["_ingreso"] < 0
        egreso_negativo = resultado["_egreso"] < 0
        resultado.loc[ingreso_negativo, "_egreso"] += resultado.loc[ingreso_negativo, "_ingreso"].abs()
        resultado.loc[ingreso_negativo, "_ingreso"] = 0.0
        resultado.loc[egreso_negativo, "_ingreso"] += resultado.loc[egreso_negativo, "_egreso"].abs()
        resultado.loc[egreso_negativo, "_egreso"] = 0.0

        resultado["_ingreso"] = pd.to_numeric(resultado["_ingreso"], errors="coerce").fillna(0.0)
        resultado["_egreso"] = pd.to_numeric(resultado["_egreso"], errors="coerce").fillna(0.0)
        resultado["_neto"] = resultado["_ingreso"] - resultado["_egreso"]
        resultado["_tipo"] = "Sin movimiento"
        resultado.loc[resultado["_ingreso"] > 0, "_tipo"] = "Ingreso"
        resultado.loc[resultado["_egreso"] > 0, "_tipo"] = "Egreso"
        resultado.loc[(resultado["_ingreso"] > 0) & (resultado["_egreso"] > 0), "_tipo"] = "Mixto"

        if cols["saldo"]:
            saldo_bruto = base[cols["saldo"]]
            saldo = saldo_bruto.map(numero)
            saldo_valido = saldo_bruto.notna() & saldo_bruto.astype(str).str.strip().ne("")
            resultado["_saldo_origen"] = saldo.where(saldo_valido, pd.NA)
        else:
            resultado["_saldo_origen"] = pd.NA

        resultado = resultado.sort_values(
            ["_fecha", "_orden_original"],
            ascending=[True, True],
            na_position="last",
        ).reset_index(drop=True)
        return resultado, cols

    data, columnas_detectadas = preparar(df)
    historial, _ = preparar(df_total if df_total is not None else df)

    if data.empty:
        st.info("No hay movimientos válidos para analizar.")
        return

    faltantes = []
    if not columnas_detectadas.get("fecha"):
        faltantes.append("fecha")
    if not columnas_detectadas.get("ingreso") and not columnas_detectadas.get("egreso") and not columnas_detectadas.get("monto"):
        faltantes.append("ingreso/egreso")
    if faltantes:
        st.error("No se pudieron identificar las columnas necesarias: " + ", ".join(faltantes) + ".")
        st.caption("Se reconocen nombres como fecha, concepto, categoría, medio, ingreso, egreso, responsable, comprobante y observaciones.")
        return

    fechas_visibles = data["_fecha"].dropna()
    desde = fechas_visibles.min().normalize() if not fechas_visibles.empty else None
    hasta = fechas_visibles.max().normalize() if not fechas_visibles.empty else None

    if desde is not None and hasta is not None and not historial.empty:
        periodo_contable = historial[historial["_fecha"].between(desde, hasta, inclusive="both")].copy()
        anteriores = historial[historial["_fecha"] < desde].copy()
        hasta_cierre = historial[historial["_fecha"] <= hasta].copy()
    else:
        periodo_contable = data.copy()
        anteriores = historial.iloc[0:0].copy()
        hasta_cierre = historial.copy()

    def ultimo_saldo(origen: pd.DataFrame) -> float | None:
        if origen.empty or "_saldo_origen" not in origen.columns:
            return None
        serie = pd.to_numeric(origen["_saldo_origen"], errors="coerce").dropna()
        return float(serie.iloc[-1]) if not serie.empty else None

    saldo_antes_fuente = ultimo_saldo(anteriores)
    if saldo_antes_fuente is not None:
        saldo_inicial = saldo_antes_fuente
    else:
        saldo_inicial = float(anteriores["_neto"].sum()) if not anteriores.empty else 0.0
        if anteriores.empty and not periodo_contable.empty:
            primer_saldo = pd.to_numeric(periodo_contable["_saldo_origen"], errors="coerce").dropna()
            if not primer_saldo.empty:
                primera_fila_saldo = periodo_contable.loc[pd.to_numeric(periodo_contable["_saldo_origen"], errors="coerce").notna()].iloc[0]
                saldo_inicial = float(primer_saldo.iloc[0]) - float(primera_fila_saldo["_neto"])

    ingresos = float(data["_ingreso"].sum())
    egresos = float(data["_egreso"].sum())
    flujo_visible = ingresos - egresos
    flujo_contable = float(periodo_contable["_neto"].sum()) if not periodo_contable.empty else flujo_visible

    saldo_cierre_fuente = ultimo_saldo(hasta_cierre)
    saldo_cierre = saldo_cierre_fuente if saldo_cierre_fuente is not None else saldo_inicial + flujo_contable

    # "Actual" significa hasta la fecha de hoy. Los movimientos futuros quedan
    # visibles en Auditoría, pero no inflan ni reducen la disponibilidad actual.
    historial_hasta_hoy = historial[
        historial["_fecha"].notna() & (historial["_fecha"] <= hoy)
    ].copy()
    if historial_hasta_hoy.empty:
        historial_hasta_hoy = historial.copy()
    saldo_actual_fuente = ultimo_saldo(historial_hasta_hoy)
    saldo_actual = (
        saldo_actual_fuente
        if saldo_actual_fuente is not None
        else float(historial_hasta_hoy["_neto"].sum())
    )

    movimientos = int(len(data))
    cantidad_ingresos = int((data["_ingreso"] > 0).sum())
    cantidad_egresos = int((data["_egreso"] > 0).sum())
    ingreso_promedio = ingresos / cantidad_ingresos if cantidad_ingresos else 0.0
    egreso_promedio = egresos / cantidad_egresos if cantidad_egresos else 0.0
    cobertura = ingresos / egresos * 100 if egresos else (100.0 if ingresos else 0.0)

    periodo_texto = (
        f"{desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}"
        if desde is not None and hasta is not None
        else "sin fechas válidas"
    )

    anterior = pd.DataFrame()
    if desde is not None and hasta is not None and not historial.empty:
        dias_periodo = max((hasta - desde).days + 1, 1)
        fin_anterior = desde - pd.Timedelta(days=1)
        inicio_anterior = fin_anterior - pd.Timedelta(days=dias_periodo - 1)
        anterior = historial[historial["_fecha"].between(inicio_anterior, fin_anterior, inclusive="both")]
    ingresos_anterior = float(anterior["_ingreso"].sum()) if not anterior.empty else 0.0
    egresos_anterior = float(anterior["_egreso"].sum()) if not anterior.empty else 0.0
    flujo_anterior = ingresos_anterior - egresos_anterior

    delta_ingresos = variacion(ingresos, ingresos_anterior) if not anterior.empty else None
    delta_egresos = variacion(egresos, egresos_anterior) if not anterior.empty else None
    delta_flujo = flujo_visible - flujo_anterior if not anterior.empty else None

    data_validas = data.dropna(subset=["_fecha"]).copy()
    dias_operativos = int(data_validas["_fecha"].dt.normalize().nunique()) if not data_validas.empty else 0
    egreso_diario = egresos / dias_operativos if dias_operativos else 0.0
    runway_dias = saldo_actual / egreso_diario if egreso_diario > 0 and saldo_actual > 0 else 0.0

    resumen_categoria = (
        data.groupby("_categoria", dropna=False)
        .agg(ingresos=("_ingreso", "sum"), egresos=("_egreso", "sum"), movimientos=("_neto", "size"))
        .reset_index()
        .rename(columns={"_categoria": "Categoría"})
    )
    resumen_categoria["Neto"] = resumen_categoria["ingresos"] - resumen_categoria["egresos"]
    resumen_categoria = resumen_categoria.sort_values("egresos", ascending=False)
    top_egreso_importe = float(resumen_categoria["egresos"].max()) if not resumen_categoria.empty else 0.0
    concentracion = top_egreso_importe / egresos * 100 if egresos else 0.0

    sin_fecha = int(data["_fecha"].isna().sum())
    sin_concepto = int(data["_concepto"].eq("Sin concepto").sum())
    sin_categoria = int(data["_categoria"].eq("Sin categoría").sum())
    en_cero = int(((data["_ingreso"] == 0) & (data["_egreso"] == 0)).sum())
    ambos = int(((data["_ingreso"] > 0) & (data["_egreso"] > 0)).sum())
    duplicados_mask = data.duplicated(subset=["_fecha", "_concepto", "_ingreso", "_egreso"], keep=False)
    duplicados = int(duplicados_mask.sum())
    futuras = int((data["_fecha"] > hoy).sum())
    problemas = sin_fecha + sin_concepto + sin_categoria + en_cero + ambos + duplicados + futuras
    calidad = max(0.0, 100.0 - problemas / max(len(data) * 4, 1) * 100)

    score = 0.0
    score += 25.0 if saldo_actual >= 0 else max(0.0, 25.0 + saldo_actual / max(abs(egresos), 1) * 25.0)
    score += min(max(cobertura, 0.0), 120.0) / 120.0 * 25.0
    score += max(0.0, 20.0 - max(concentracion - 35.0, 0.0) * 0.35)
    score += calidad * 0.20
    score += 10.0 if flujo_visible >= 0 else max(0.0, 10.0 - abs(flujo_visible) / max(egresos, 1) * 10.0)
    score = round(min(100.0, max(0.0, score)))

    if score >= 80:
        estado, icono = "Sólida", "🟢"
    elif score >= 60:
        estado, icono = "Controlada", "🟡"
    else:
        estado, icono = "Requiere atención", "🔴"

    st.caption(f"Período analizado: {periodo_texto} · Historial utilizado para saldos: {len(historial):,} movimientos".replace(",", "."))

    k1, k2, k3 = st.columns(3)
    k1.metric(
        "💼 Saldo actual real",
        moneda(saldo_actual),
        help="Último saldo histórico disponible. Si la hoja no posee saldo acumulado, se calcula como ingresos históricos menos egresos históricos.",
    )
    k2.metric(
        "⏮️ Saldo inicial del período",
        moneda(saldo_inicial),
        help="Saldo existente antes del primer día incluido en el filtro.",
    )
    k3.metric(
        "⏭️ Saldo al cierre del período",
        moneda(saldo_cierre),
        delta=moneda(saldo_cierre - saldo_inicial),
        help="Saldo inicial más el flujo contable completo del período.",
    )

    k4, k5, k6 = st.columns(3)
    k4.metric(
        "📥 Ingresos",
        moneda(ingresos),
        delta=f"{delta_ingresos:+.1f}% vs. período anterior" if delta_ingresos is not None else None,
    )
    k5.metric(
        "📤 Egresos",
        moneda(egresos),
        delta=f"{delta_egresos:+.1f}% vs. período anterior" if delta_egresos is not None else None,
        delta_color="inverse",
    )
    k6.metric(
        "⚖️ Flujo neto",
        moneda(flujo_visible),
        delta=moneda(delta_flujo) if delta_flujo is not None else None,
    )

    if abs(flujo_contable - flujo_visible) > 0.01:
        st.info(
            "La vista contiene filtros adicionales. Los indicadores de movimientos usan lo visible, "
            f"mientras que el saldo de cierre incorpora el flujo contable completo del rango ({moneda(flujo_contable)})."
        )

    e1, e2, e3, e4, e5 = st.columns(5)
    e1.metric("Índice interno", f"{score}/100", estado)
    e2.metric("Cobertura", porcentaje(cobertura), "Ingresos / egresos")
    e3.metric("Egreso diario", moneda(egreso_diario), f"{dias_operativos} días operativos")
    e4.metric("Autonomía estimada", f"{runway_dias:.1f} días" if runway_dias > 0 else "Sin cobertura")
    e5.metric("Calidad de datos", porcentaje(calidad), f"{problemas} alertas")

    mensaje_estado = (
        f"{icono} **Caja {estado.lower()}.** El período produjo un flujo de {moneda(flujo_visible)}; "
        f"los ingresos cubrieron {porcentaje(cobertura)} de los egresos y el saldo actual es {moneda(saldo_actual)}."
    )
    if score >= 80:
        st.success(mensaje_estado)
    elif score >= 60:
        st.warning(mensaje_estado)
    else:
        st.error(mensaje_estado)

    tabs = st.tabs([
        "🏦 Resumen ejecutivo",
        "📈 Evolución y cierres",
        "🔭 Proyección",
        "🧮 Conciliación",
        "🧾 Movimientos",
        "🚨 Auditoría",
    ])

    with tabs[0]:
        c1, c2 = st.columns([1.25, 1])
        with c1:
            st.markdown("### Puente de caja del período")
            fig_puente = go.Figure(
                go.Waterfall(
                    orientation="v",
                    measure=["absolute", "relative", "relative", "total"],
                    x=["Saldo inicial", "Ingresos", "Egresos", "Saldo final"],
                    y=[saldo_inicial, ingresos, -egresos, saldo_cierre],
                    text=[moneda(saldo_inicial, 0), moneda(ingresos, 0), moneda(-egresos, 0), moneda(saldo_cierre, 0)],
                    textposition="outside",
                    connector={"line": {"width": 1}},
                )
            )
            fig_puente.update_layout(height=405, margin=dict(l=10, r=10, t=20, b=10), yaxis_title="Importe")
            st.plotly_chart(fig_puente, use_container_width=True)

        with c2:
            st.markdown("### Lectura ejecutiva")
            categoria_top = "Sin egresos"
            if not resumen_categoria.empty and top_egreso_importe > 0:
                categoria_top = str(resumen_categoria.iloc[0]["Categoría"])
            lectura = [
                f"**Resultado:** {'superávit' if flujo_visible >= 0 else 'déficit'} de {moneda(abs(flujo_visible))}.",
                f"**Mayor rubro de salida:** {categoria_top}, con {moneda(top_egreso_importe)} ({porcentaje(concentracion)}).",
                f"**Ticket promedio:** ingreso {moneda(ingreso_promedio)} · egreso {moneda(egreso_promedio)}.",
                f"**Actividad:** {movimientos} movimientos distribuidos en {dias_operativos} días operativos.",
            ]
            for linea in lectura:
                st.markdown(f"- {linea}")

            if saldo_actual < 0:
                st.error("El saldo histórico actual es negativo. Conviene revisar retiros, anticipos, saldos iniciales y movimientos omitidos.")
            elif flujo_visible < 0 and saldo_actual > 0:
                st.warning("La caja conserva saldo positivo, pero el período consumió fondos. Revisá las categorías de egreso con mayor peso.")
            elif cobertura >= 110:
                st.success("Los ingresos superan holgadamente a los egresos del período.")
            else:
                st.info("La caja está equilibrada, aunque con margen limitado frente a nuevas salidas.")

        izquierda, derecha = st.columns(2)
        with izquierda:
            st.markdown("### Egresos por categoría")
            categorias_egreso = resumen_categoria[resumen_categoria["egresos"] > 0].copy()
            if categorias_egreso.empty:
                st.info("No hay egresos en el período.")
            else:
                fig = px.bar(
                    categorias_egreso.sort_values("egresos", ascending=True).tail(12),
                    x="egresos", y="Categoría", orientation="h", text_auto=".2s",
                )
                fig.update_layout(height=410, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="Egresos", yaxis_title="")
                st.plotly_chart(fig, use_container_width=True)

        with derecha:
            st.markdown("### Ingresos por categoría")
            categorias_ingreso = resumen_categoria[resumen_categoria["ingresos"] > 0].copy()
            if categorias_ingreso.empty:
                st.info("No hay ingresos en el período.")
            else:
                fig = px.bar(
                    categorias_ingreso.sort_values("ingresos", ascending=True).tail(12),
                    x="ingresos", y="Categoría", orientation="h", text_auto=".2s",
                )
                fig.update_layout(height=410, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="Ingresos", yaxis_title="")
                st.plotly_chart(fig, use_container_width=True)

        resumen_medio = (
            data.groupby("_medio", dropna=False)
            .agg(Ingresos=("_ingreso", "sum"), Egresos=("_egreso", "sum"), Movimientos=("_neto", "size"))
            .reset_index()
            .rename(columns={"_medio": "Medio"})
        )
        resumen_medio["Neto"] = resumen_medio["Ingresos"] - resumen_medio["Egresos"]
        st.markdown("### Composición por medio de pago")
        tabla_medio = resumen_medio.sort_values("Movimientos", ascending=False).copy()
        for col in ["Ingresos", "Egresos", "Neto"]:
            tabla_medio[col] = tabla_medio[col].map(moneda)
        st.dataframe(tabla_medio, use_container_width=True, hide_index=True)

    with tabs[1]:
        if data_validas.empty:
            st.info("No hay fechas válidas para construir la evolución.")
        else:
            diario = (
                data_validas.assign(Fecha=data_validas["_fecha"].dt.normalize())
                .groupby("Fecha")
                .agg(Ingresos=("_ingreso", "sum"), Egresos=("_egreso", "sum"), Movimientos=("_neto", "size"))
                .reset_index()
                .sort_values("Fecha")
            )
            diario["Flujo neto"] = diario["Ingresos"] - diario["Egresos"]
            diario["Saldo de cierre"] = saldo_inicial + diario["Flujo neto"].cumsum()
            diario["Promedio móvil 7 días"] = diario["Flujo neto"].rolling(7, min_periods=1).mean()

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=diario["Fecha"], y=diario["Saldo de cierre"], mode="lines+markers", name="Saldo de cierre"))
            fig.add_trace(go.Scatter(x=diario["Fecha"], y=diario["Promedio móvil 7 días"], mode="lines", name="Promedio flujo 7 días", yaxis="y2"))
            fig.update_layout(
                title="Evolución del saldo y tendencia operativa",
                height=430,
                margin=dict(l=10, r=10, t=55, b=10),
                yaxis=dict(title="Saldo"),
                yaxis2=dict(title="Flujo promedio", overlaying="y", side="right", showgrid=False),
                legend=dict(orientation="h", y=1.08),
            )
            st.plotly_chart(fig, use_container_width=True)

            diario_largo = diario.melt(id_vars="Fecha", value_vars=["Ingresos", "Egresos"], var_name="Tipo", value_name="Importe")
            fig = px.bar(diario_largo, x="Fecha", y="Importe", color="Tipo", barmode="group", title="Ingresos y egresos por día")
            fig.update_layout(height=400, margin=dict(l=10, r=10, t=55, b=10))
            st.plotly_chart(fig, use_container_width=True)

            mensual = data_validas.copy()
            mensual["Mes"] = mensual["_fecha"].dt.to_period("M").astype(str)
            mensual = mensual.groupby("Mes").agg(Ingresos=("_ingreso", "sum"), Egresos=("_egreso", "sum"), Movimientos=("_neto", "size")).reset_index()
            mensual["Flujo neto"] = mensual["Ingresos"] - mensual["Egresos"]
            mensual["Cobertura"] = mensual.apply(lambda fila: fila["Ingresos"] / fila["Egresos"] * 100 if fila["Egresos"] else (100.0 if fila["Ingresos"] else 0.0), axis=1)

            st.markdown("### Cierre mensual")
            tabla_mensual = mensual.sort_values("Mes", ascending=False).copy()
            for col in ["Ingresos", "Egresos", "Flujo neto"]:
                tabla_mensual[col] = tabla_mensual[col].map(moneda)
            tabla_mensual["Cobertura"] = tabla_mensual["Cobertura"].map(porcentaje)
            st.dataframe(tabla_mensual, use_container_width=True, hide_index=True)

            st.markdown("### Últimos cierres diarios")
            tabla_diaria = diario.sort_values("Fecha", ascending=False).head(45).copy()
            tabla_diaria["Fecha"] = tabla_diaria["Fecha"].dt.strftime("%d/%m/%Y")
            for col in ["Ingresos", "Egresos", "Flujo neto", "Saldo de cierre", "Promedio móvil 7 días"]:
                tabla_diaria[col] = tabla_diaria[col].map(moneda)
            st.dataframe(tabla_diaria, use_container_width=True, hide_index=True)

    with tabs[2]:
        st.markdown("### Proyección de disponibilidad")
        if data_validas.empty or desde is None or hasta is None:
            st.info("No hay información fechada suficiente para proyectar.")
        else:
            dias_calendario = max((hasta - desde).days + 1, 1)
            promedio_ingreso_dia = ingresos / dias_calendario
            promedio_egreso_dia = egresos / dias_calendario
            promedio_neto_dia = flujo_visible / dias_calendario

            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Ingreso diario", moneda(promedio_ingreso_dia))
            p2.metric("Egreso diario", moneda(promedio_egreso_dia))
            p3.metric("Neto diario", moneda(promedio_neto_dia))
            p4.metric("Días analizados", dias_calendario)

            horizonte = st.slider(
                "Horizonte de proyección (días)",
                min_value=7,
                max_value=120,
                value=30,
                step=1,
                key=f"horizonte_caja_{clave_widget}",
            )
            ajuste_ingreso = st.slider(
                "Variación esperada de ingresos",
                min_value=-50,
                max_value=100,
                value=0,
                step=5,
                format="%d%%",
                key=f"ajuste_ingresos_{clave_widget}",
            )
            ajuste_egreso = st.slider(
                "Variación esperada de egresos",
                min_value=-30,
                max_value=100,
                value=0,
                step=5,
                format="%d%%",
                key=f"ajuste_egresos_{clave_widget}",
            )

            ingreso_proyectado_dia = promedio_ingreso_dia * (1 + ajuste_ingreso / 100)
            egreso_proyectado_dia = promedio_egreso_dia * (1 + ajuste_egreso / 100)
            neto_proyectado_dia = ingreso_proyectado_dia - egreso_proyectado_dia
            saldo_proyectado = saldo_actual + neto_proyectado_dia * horizonte

            escenarios = pd.DataFrame({
                "Escenario": ["Conservador", "Base", "Expansivo"],
                "Ingreso diario": [ingreso_proyectado_dia * 0.85, ingreso_proyectado_dia, ingreso_proyectado_dia * 1.15],
                "Egreso diario": [egreso_proyectado_dia * 1.10, egreso_proyectado_dia, egreso_proyectado_dia * 0.95],
            })
            escenarios["Neto diario"] = escenarios["Ingreso diario"] - escenarios["Egreso diario"]
            escenarios["Saldo proyectado"] = saldo_actual + escenarios["Neto diario"] * horizonte

            r1, r2, r3 = st.columns(3)
            r1.metric("Saldo proyectado base", moneda(saldo_proyectado), moneda(saldo_proyectado - saldo_actual))
            r2.metric("Ingresos proyectados", moneda(ingreso_proyectado_dia * horizonte))
            r3.metric("Egresos proyectados", moneda(egreso_proyectado_dia * horizonte))

            fig = px.bar(escenarios, x="Escenario", y="Saldo proyectado", text_auto=".2s", title=f"Saldo estimado a {horizonte} días")
            fig.update_layout(height=390, margin=dict(l=10, r=10, t=55, b=10))
            st.plotly_chart(fig, use_container_width=True)

            tabla_escenarios = escenarios.copy()
            for col in ["Ingreso diario", "Egreso diario", "Neto diario", "Saldo proyectado"]:
                tabla_escenarios[col] = tabla_escenarios[col].map(moneda)
            st.dataframe(tabla_escenarios, use_container_width=True, hide_index=True)
            st.caption("La proyección es matemática y utiliza el ritmo del período filtrado; no reemplaza una previsión de cobranzas y pagos comprometidos.")

    with tabs[3]:
        st.markdown("### Conciliación operativa de caja")
        st.caption("Esta herramienta compara el saldo teórico con el arqueo informado. No escribe ni modifica el Google Sheet.")

        saldo_objetivo = saldo_actual
        c1, c2, c3 = st.columns(3)
        with c1:
            efectivo_contado = st.number_input(
                "Efectivo contado",
                value=0.0,
                step=1000.0,
                format="%.2f",
                key=f"efectivo_contado_{clave_widget}",
            )
        with c2:
            valores_pendientes = st.number_input(
                "Valores / comprobantes a incorporar",
                value=0.0,
                step=1000.0,
                format="%.2f",
                key=f"valores_pendientes_{clave_widget}",
            )
        with c3:
            retiros_no_registrados = st.number_input(
                "Retiros pendientes de registrar",
                value=0.0,
                step=1000.0,
                format="%.2f",
                key=f"retiros_pendientes_{clave_widget}",
            )

        arqueo_ajustado = efectivo_contado + valores_pendientes - retiros_no_registrados
        diferencia = arqueo_ajustado - saldo_objetivo
        tolerancia = st.number_input(
            "Tolerancia admitida",
            min_value=0.0,
            value=100.0,
            step=100.0,
            format="%.2f",
            key=f"tolerancia_caja_{clave_widget}",
        )

        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Saldo teórico", moneda(saldo_objetivo))
        a2.metric("Arqueo ajustado", moneda(arqueo_ajustado))
        a3.metric("Diferencia", moneda(diferencia), delta_color="inverse" if abs(diferencia) > tolerancia else "normal")
        a4.metric("Estado", "Conciliada" if abs(diferencia) <= tolerancia else "Con diferencia")

        if abs(diferencia) <= tolerancia:
            st.success(f"✅ Caja conciliada dentro de la tolerancia de {moneda(tolerancia)}.")
        else:
            st.error(f"⚠️ Existe una diferencia de {moneda(diferencia)}. Revisá movimientos omitidos, duplicados, retiros y comprobantes pendientes.")

        observacion_cierre = st.text_area(
            "Observación del cierre",
            placeholder="Ej.: pendiente comprobante de transferencia, retiro de dirección, diferencia en efectivo...",
            key=f"observacion_cierre_{clave_widget}",
        )
        reporte_cierre = pd.DataFrame([{
            "fecha_reporte": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
            "caja": nombre_caja,
            "periodo_analizado": periodo_texto,
            "saldo_teorico": saldo_objetivo,
            "efectivo_contado": efectivo_contado,
            "valores_pendientes": valores_pendientes,
            "retiros_no_registrados": retiros_no_registrados,
            "arqueo_ajustado": arqueo_ajustado,
            "diferencia": diferencia,
            "tolerancia": tolerancia,
            "estado": "Conciliada" if abs(diferencia) <= tolerancia else "Con diferencia",
            "observacion": observacion_cierre,
        }])
        st.download_button(
            "📥 Descargar acta de conciliación",
            data=reporte_cierre.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
            file_name=f"conciliacion_{clave_widget}_{hoy.strftime('%Y%m%d')}.csv",
            mime="text/csv",
            key=f"descargar_conciliacion_{clave_widget}",
            use_container_width=True,
        )

    with tabs[4]:
        st.markdown("### Explorador de movimientos")
        f1, f2, f3, f4 = st.columns([2.2, 1, 1, 1])
        with f1:
            busqueda = st.text_input(
                "Buscar concepto, responsable, comprobante u observación",
                key=f"buscar_movimientos_{clave_widget}",
            )
        with f2:
            tipo_elegido = st.selectbox("Tipo", ["Todos", "Ingreso", "Egreso", "Mixto", "Sin movimiento"], key=f"tipo_movimientos_{clave_widget}")
        with f3:
            categorias = ["Todas"] + sorted(data["_categoria"].dropna().astype(str).unique().tolist())
            categoria_elegida = st.selectbox("Categoría", categorias, key=f"categoria_movimientos_{clave_widget}")
        with f4:
            medios = ["Todos"] + sorted(data["_medio"].dropna().astype(str).unique().tolist())
            medio_elegido = st.selectbox("Medio", medios, key=f"medio_movimientos_{clave_widget}")

        detalle = data.copy()
        if busqueda.strip():
            patron = re.escape(busqueda.strip())
            universo = (
                detalle["_concepto"].astype(str) + " " + detalle["_responsable"].astype(str)
                + " " + detalle["_comprobante"].astype(str) + " " + detalle["_observaciones"].astype(str)
            )
            detalle = detalle[universo.str.contains(patron, case=False, regex=True, na=False)]
        if tipo_elegido != "Todos":
            detalle = detalle[detalle["_tipo"] == tipo_elegido]
        if categoria_elegida != "Todas":
            detalle = detalle[detalle["_categoria"] == categoria_elegida]
        if medio_elegido != "Todos":
            detalle = detalle[detalle["_medio"] == medio_elegido]

        detalle = detalle.sort_values(["_fecha", "_orden_original"], ascending=[False, False], na_position="last")
        tabla_detalle = pd.DataFrame({
            "Fecha": detalle["_fecha"].dt.strftime("%d/%m/%Y").fillna(""),
            "Concepto": detalle["_concepto"],
            "Categoría": detalle["_categoria"],
            "Medio": detalle["_medio"],
            "Comprobante": detalle["_comprobante"],
            "Ingreso": detalle["_ingreso"],
            "Egreso": detalle["_egreso"],
            "Neto": detalle["_neto"],
            "Responsable": detalle["_responsable"],
            "Observaciones": detalle["_observaciones"],
        })
        st.dataframe(
            tabla_detalle,
            use_container_width=True,
            hide_index=True,
            height=520,
            column_config={
                "Concepto": st.column_config.TextColumn(width="large"),
                "Observaciones": st.column_config.TextColumn(width="large"),
                "Ingreso": st.column_config.NumberColumn(format="$ %.2f"),
                "Egreso": st.column_config.NumberColumn(format="$ %.2f"),
                "Neto": st.column_config.NumberColumn(format="$ %.2f"),
            },
        )
        st.caption(f"Movimientos mostrados: {len(tabla_detalle):,} · Neto visible: {moneda(float(detalle['_neto'].sum()))}".replace(",", "."))
        st.download_button(
            "📥 Descargar movimientos filtrados",
            data=tabla_detalle.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
            file_name=f"{clave_widget}_movimientos_{hoy.strftime('%Y%m%d')}.csv",
            mime="text/csv",
            key=f"descargar_movimientos_{clave_widget}",
        )

    with tabs[5]:
        st.markdown("### Auditoría automática de integridad")
        q1, q2, q3, q4, q5, q6 = st.columns(6)
        q1.metric("Sin fecha", sin_fecha)
        q2.metric("Sin concepto", sin_concepto)
        q3.metric("Sin categoría", sin_categoria)
        q4.metric("Importe cero", en_cero)
        q5.metric("Duplicados", duplicados)
        q6.metric("Fechas futuras", futuras)

        if problemas == 0:
            st.success("✅ No se detectaron inconsistencias evidentes en la vista analizada.")
        else:
            if sin_fecha:
                st.warning(f"Hay {sin_fecha} movimientos sin fecha válida.")
            if sin_concepto:
                st.warning(f"Hay {sin_concepto} movimientos sin concepto.")
            if sin_categoria:
                st.warning(f"Hay {sin_categoria} movimientos sin categoría.")
            if en_cero:
                st.warning(f"Hay {en_cero} movimientos sin ingreso ni egreso.")
            if ambos:
                st.warning(f"Hay {ambos} filas con ingreso y egreso simultáneos.")
            if futuras:
                st.error(f"Hay {futuras} movimientos con fecha posterior a hoy.")
            if duplicados:
                st.error(f"Se detectaron {duplicados} filas potencialmente duplicadas.")
                duplicados_df = data[duplicados_mask].copy().sort_values("_fecha", ascending=False)
                tabla_duplicados = pd.DataFrame({
                    "Fecha": duplicados_df["_fecha"].dt.strftime("%d/%m/%Y").fillna(""),
                    "Concepto": duplicados_df["_concepto"],
                    "Categoría": duplicados_df["_categoria"],
                    "Ingreso": duplicados_df["_ingreso"],
                    "Egreso": duplicados_df["_egreso"],
                    "Responsable": duplicados_df["_responsable"],
                })
                st.dataframe(
                    tabla_duplicados,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Ingreso": st.column_config.NumberColumn(format="$ %.2f"),
                        "Egreso": st.column_config.NumberColumn(format="$ %.2f"),
                    },
                )

        importes = data[["_ingreso", "_egreso"]].max(axis=1)
        positivos = importes[importes > 0]
        atipicos_mask = pd.Series(False, index=data.index)
        if len(positivos) >= 8:
            q_1 = positivos.quantile(0.25)
            q_3 = positivos.quantile(0.75)
            limite = q_3 + 1.5 * (q_3 - q_1)
            atipicos_mask = importes > limite
        atipicos = int(atipicos_mask.sum())
        st.markdown("### Movimientos atípicos")
        if atipicos == 0:
            st.info("No se detectaron importes estadísticamente atípicos con el criterio IQR.")
        else:
            st.warning(f"Se detectaron {atipicos} movimientos de importe inusualmente alto para revisar.")
            revision = data[atipicos_mask].sort_values("_neto", key=lambda s: s.abs(), ascending=False)
            tabla_revision = pd.DataFrame({
                "Fecha": revision["_fecha"].dt.strftime("%d/%m/%Y").fillna(""),
                "Concepto": revision["_concepto"],
                "Categoría": revision["_categoria"],
                "Ingreso": revision["_ingreso"],
                "Egreso": revision["_egreso"],
                "Responsable": revision["_responsable"],
            })
            st.dataframe(tabla_revision, use_container_width=True, hide_index=True)

        st.markdown("### Columnas reconocidas")
        st.dataframe(
            pd.DataFrame(
                [(dato, columna if columna else "No encontrada") for dato, columna in columnas_detectadas.items()],
                columns=["Dato requerido", "Columna utilizada"],
            ),
            use_container_width=True,
            hide_index=True,
        )
def render_lectura_ejecutiva_facturacion_pro(
    activos: pd.DataFrame,
    filtrado: pd.DataFrame,
    facturado: float,
    cobrado: float,
    pendiente: float,
    vencido: float,
    tasa_cobro: float,
    mora: float,
    dso: float,
    hoy: pd.Timestamp,
    periodo: str,
) -> None:
    """
    Lectura ejecutiva automática y prospectiva de facturación.

    Solo analiza copias ya preparadas por el panel industrial.
    No escribe, actualiza ni elimina datos del Google Sheet.
    """
    import calendar

    if activos is None or activos.empty:
        st.info("No hay datos suficientes para construir la lectura ejecutiva.")
        return

    def ranking(columna: str, valor: str = "_monto") -> pd.Series:
        if columna not in activos.columns or valor not in activos.columns:
            return pd.Series(dtype="float64")
        base = activos[[columna, valor]].copy()
        base[columna] = base[columna].fillna("").astype(str).str.strip()
        base = base[base[columna].ne("")]
        if base.empty:
            return pd.Series(dtype="float64")
        return (
            base.groupby(columna, dropna=False)[valor]
            .sum()
            .sort_values(ascending=False)
        )

    def porcentaje(parte: float, total: float) -> float:
        return parte / total * 100 if total else 0.0

    def nombre_top(serie: pd.Series, defecto: str = "Sin dato") -> str:
        return str(serie.index[0]) if not serie.empty else defecto

    def valor_top(serie: pd.Series) -> float:
        return float(serie.iloc[0]) if not serie.empty else 0.0

    top_obra = ranking("_obra_social")
    top_proc = ranking("_procedimiento")
    top_med = ranking("_medico")
    deuda_obra = ranking("_obra_social", "_pendiente")

    conc_obra = porcentaje(valor_top(top_obra), facturado)
    conc_proc = porcentaje(valor_top(top_proc), facturado)
    conc_med = porcentaje(valor_top(top_med), facturado)

    # Tendencia mensual y ritmo anualizado.
    mensual = activos.loc[activos["_fecha_base"].notna()].copy()
    evolucion = pd.DataFrame()
    crecimiento_mensual = None
    ultimo_mes = "Sin dato"
    facturacion_ultimo_mes = 0.0
    promedio_mensual = 0.0

    if not mensual.empty:
        mensual["_periodo_exec"] = mensual["_fecha_base"].dt.to_period("M").astype(str)
        evolucion = (
            mensual.groupby("_periodo_exec", as_index=False)
            .agg(
                Facturado=("_monto", "sum"),
                Cobrado=("_cobrado", "sum"),
                Pendiente=("_pendiente", "sum"),
                Registros=("_monto", "size"),
            )
            .sort_values("_periodo_exec")
        )
        if not evolucion.empty:
            ultimo_mes = str(evolucion.iloc[-1]["_periodo_exec"])
            facturacion_ultimo_mes = float(evolucion.iloc[-1]["Facturado"])
            promedio_mensual = float(evolucion["Facturado"].mean())
        if len(evolucion) >= 2:
            anterior = float(evolucion.iloc[-2]["Facturado"])
            actual = float(evolucion.iloc[-1]["Facturado"])
            crecimiento_mensual = porcentaje(actual - anterior, anterior) if anterior else None

    if periodo == "Año actual":
        dias_anio = 366 if calendar.isleap(int(hoy.year)) else 365
        proyeccion_base = facturado / max(int(hoy.dayofyear), 1) * dias_anio
    else:
        proyeccion_base = promedio_mensual * 12 if promedio_mensual else facturado

    proyeccion_conservadora = proyeccion_base * 0.90
    proyeccion_expansiva = proyeccion_base * 1.10

    # Caja potencial recuperable.
    recupero_30 = vencido * 0.30
    recupero_50 = vencido * 0.50
    recupero_70 = vencido * 0.70

    # Calidad de datos relevante para gestión.
    registros_activos = max(int(len(activos)), 1)
    pendientes_df = activos.loc[activos["_pendiente"].gt(0)].copy()
    sin_fecha = int(activos["_fecha_base"].isna().sum())
    sin_factura = int(
        activos.get("_numero_factura", pd.Series("", index=activos.index))
        .fillna("")
        .astype(str)
        .str.strip()
        .eq("")
        .sum()
    )
    sin_vencimiento = int(
        pendientes_df["_vencimiento"].isna().sum()
    ) if not pendientes_df.empty else 0
    completitud = max(
        0.0,
        100.0
        - porcentaje(sin_fecha + sin_factura + sin_vencimiento, registros_activos * 3),
    )

    # Score interno sugerido, no es un estándar externo.
    puntaje_cobro = min(max(tasa_cobro, 0.0), 100.0) * 0.40
    puntaje_mora = max(0.0, 100.0 - min(max(mora, 0.0), 100.0)) * 0.25
    if dso <= 0:
        puntaje_dso = 8.0
    elif dso <= 45:
        puntaje_dso = 15.0
    elif dso <= 75:
        puntaje_dso = 10.0
    elif dso <= 120:
        puntaje_dso = 6.0
    else:
        puntaje_dso = 2.0
    puntaje_concentracion = max(0.0, 10.0 - max(conc_obra - 25.0, 0.0) * 0.20)
    puntaje_calidad = completitud * 0.10
    score = round(min(100.0, puntaje_cobro + puntaje_mora + puntaje_dso + puntaje_concentracion + puntaje_calidad))

    if score >= 80:
        estado = "Sólido"
        icono = "🟢"
    elif score >= 60:
        estado = "Bajo control, con puntos a corregir"
        icono = "🟡"
    else:
        estado = "Requiere intervención prioritaria"
        icono = "🔴"

    st.markdown("### 🧠 Lectura ejecutiva PRO")
    e1, e2, e3, e4 = st.columns(4)
    e1.metric("Índice ejecutivo interno", f"{score}/100", estado)
    e2.metric(
        "Tendencia último mes",
        "Sin comparación" if crecimiento_mensual is None else f"{crecimiento_mensual:+.1f}%",
        ultimo_mes,
    )
    e3.metric("Riesgo vencido", f"{mora:.1f}%", fmt_money(vencido))
    e4.metric("Caja recuperable al 50%", fmt_money(recupero_50))

    resumen = (
        f"{icono} **Estado ejecutivo: {estado}.** "
        f"La vista seleccionada registra {fmt_money(facturado)} facturados, "
        f"{fmt_money(cobrado)} cobrados y {fmt_money(pendiente)} pendientes. "
        f"La cobranza representa {tasa_cobro:.1f}% del facturado"
    )
    if vencido > 0:
        resumen += f" y {fmt_money(vencido)} ya están vencidos"
    resumen += "."

    if score >= 80:
        st.success(resumen)
    elif score >= 60:
        st.warning(resumen)
    else:
        st.error(resumen)

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("#### Qué está impulsando el resultado")
        impulso = []
        if not top_obra.empty:
            impulso.append(
                f"**Financiador principal:** {nombre_top(top_obra)} aporta "
                f"{fmt_money(valor_top(top_obra))} ({conc_obra:.1f}% del total)."
            )
        if not top_proc.empty:
            impulso.append(
                f"**Procedimiento principal:** {nombre_top(top_proc)} genera "
                f"{fmt_money(valor_top(top_proc))} ({conc_proc:.1f}% del total)."
            )
        if not top_med.empty:
            impulso.append(
                f"**Médico principal:** {nombre_top(top_med)} concentra "
                f"{fmt_money(valor_top(top_med))} ({conc_med:.1f}% del total)."
            )
        if crecimiento_mensual is not None:
            direccion = "creció" if crecimiento_mensual >= 0 else "cayó"
            impulso.append(
                f"**Ritmo reciente:** el último mes {direccion} "
                f"{abs(crecimiento_mensual):.1f}% frente al mes anterior."
            )
        impulso.append(
            f"**Ticket y cobranza:** la tasa de cobro es {tasa_cobro:.1f}%"
            + (f" y el plazo promedio observado es {dso:.1f} días." if dso else ".")
        )
        for texto in impulso:
            st.markdown(f"- {texto}")

    with col_b:
        st.markdown("#### Riesgos que requieren atención")
        riesgos = []
        if mora >= 30:
            riesgos.append(
                f"**Mora alta:** {mora:.1f}% de la cartera pendiente ya está vencida."
            )
        elif mora >= 15:
            riesgos.append(
                f"**Mora moderada:** {mora:.1f}% de la cartera pendiente está vencida."
            )
        else:
            riesgos.append(
                f"**Mora contenida:** {mora:.1f}% de la cartera pendiente está vencida."
            )
        if conc_obra >= 40:
            riesgos.append(
                f"**Dependencia comercial:** {nombre_top(top_obra)} representa "
                f"{conc_obra:.1f}% de la facturación."
            )
        if conc_med >= 45:
            riesgos.append(
                f"**Dependencia profesional:** {nombre_top(top_med)} concentra "
                f"{conc_med:.1f}% del volumen económico."
            )
        if dso > 75:
            riesgos.append(
                f"**Ciclo de cobro largo:** el promedio observado es {dso:.1f} días."
            )
        if sin_vencimiento:
            riesgos.append(
                f"**Control incompleto:** {sin_vencimiento} saldos pendientes no tienen vencimiento."
            )
        if sin_factura:
            riesgos.append(
                f"**Trazabilidad:** {sin_factura} registros activos no tienen número de factura."
            )
        if not riesgos:
            riesgos.append("No se detectan alertas críticas con los datos actualmente visibles.")
        for texto in riesgos:
            st.markdown(f"- {texto}")

    st.markdown("#### 🔭 Proyección y escenarios futuros")
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Escenario conservador", fmt_money(proyeccion_conservadora), "90% del ritmo base")
    p2.metric("Escenario base", fmt_money(proyeccion_base), "Ritmo anualizado")
    p3.metric("Escenario expansivo", fmt_money(proyeccion_expansiva), "+10% sobre el ritmo base")
    p4.metric("Último mes observado", fmt_money(facturacion_ultimo_mes), ultimo_mes)

    st.markdown("#### 💡 Oportunidades concretas")
    oportunidades = []
    if vencido > 0:
        oportunidades.append(
            f"Un operativo de cobranza que recupere 30%, 50% o 70% de la deuda vencida "
            f"liberaría aproximadamente {fmt_money(recupero_30)}, {fmt_money(recupero_50)} "
            f"o {fmt_money(recupero_70)} de caja."
        )
    if not deuda_obra.empty and valor_top(deuda_obra) > 0:
        oportunidades.append(
            f"La primera mesa de seguimiento debería enfocarse en **{nombre_top(deuda_obra)}**, "
            f"que concentra {fmt_money(valor_top(deuda_obra))} del saldo pendiente."
        )
    if not top_proc.empty:
        oportunidades.append(
            f"El crecimiento comercial puede apoyarse en **{nombre_top(top_proc)}**: "
            f"es la práctica con mayor peso económico y conviene medir demanda, margen, "
            f"capacidad disponible y velocidad de cobro antes de expandirla."
        )
    if conc_obra >= 35:
        oportunidades.append(
            "Conviene diversificar financiadores para reducir dependencia y evitar que una demora "
            "individual afecte de manera desproporcionada la caja."
        )
    else:
        oportunidades.append(
            "La cartera de financiadores presenta una concentración manejable; el siguiente paso "
            "es comparar rentabilidad y plazo de cobro por obra social."
        )
    oportunidades.append(
        f"Meta operativa sugerida para el próximo ciclo: llevar la cobranza a "
        f"{min(95.0, max(85.0, tasa_cobro + 5.0)):.1f}% y reducir el plazo medio "
        f"{('en al menos 10 días' if dso > 10 else 'manteniéndolo por debajo de 45 días')}."
    )
    for texto in oportunidades:
        st.markdown(f"- {texto}")

    st.markdown("#### 🗺️ Plan de acción 7–30–90 días")
    acciones = [
        {
            "Horizonte": "Próximos 7 días",
            "Prioridad": "Caja",
            "Acción": (
                f"Contactar y conciliar los mayores saldos vencidos, comenzando por "
                f"{nombre_top(deuda_obra)}."
                if not deuda_obra.empty and valor_top(deuda_obra) > 0
                else "Validar que todos los pendientes tengan factura, vencimiento y responsable."
            ),
            "Resultado esperado": f"Recuperar al menos {fmt_money(recupero_30)}" if vencido > 0 else "Cartera totalmente trazable",
        },
        {
            "Horizonte": "Próximos 30 días",
            "Prioridad": "Cobranza",
            "Acción": "Implementar seguimiento semanal por obra social, tramo de mora y responsable.",
            "Resultado esperado": f"Cobranza ≥ {min(95.0, max(85.0, tasa_cobro + 5.0)):.1f}%",
        },
        {
            "Horizonte": "Próximos 60 días",
            "Prioridad": "Crecimiento",
            "Acción": (
                f"Diseñar un plan de crecimiento para {nombre_top(top_proc)} y las dos prácticas siguientes, "
                "validando margen, demanda, insumos y disponibilidad quirúrgica."
            ),
            "Resultado esperado": "Aumentar volumen sin deteriorar margen ni plazo de cobro",
        },
        {
            "Horizonte": "Próximos 90 días",
            "Prioridad": "Estrategia",
            "Acción": "Definir metas mensuales por facturación, cobranza, mora, DSO y concentración.",
            "Resultado esperado": f"Acercarse al escenario base de {fmt_money(proyeccion_base)}",
        },
    ]
    st.dataframe(pd.DataFrame(acciones), use_container_width=True, hide_index=True)

    with st.expander("📌 Metas internas sugeridas para el próximo período", expanded=False):
        metas = pd.DataFrame(
            [
                ["Tasa de cobro", f"{tasa_cobro:.1f}%", f"≥ {min(95.0, max(85.0, tasa_cobro + 5.0)):.1f}%"],
                ["Mora sobre pendiente", f"{mora:.1f}%", f"≤ {max(10.0, mora - 10.0):.1f}%"],
                ["Días promedio de cobro", f"{dso:.1f}" if dso else "Sin dato", "≤ 45 días"],
                ["Completitud de datos", f"{completitud:.1f}%", "≥ 98%"],
                ["Concentración obra principal", f"{conc_obra:.1f}%", "≤ 35% cuando sea comercialmente posible"],
            ],
            columns=["Indicador", "Situación actual", "Meta sugerida"],
        )
        st.dataframe(metas, use_container_width=True, hide_index=True)
        st.caption(
            "Estas metas son internas y orientativas. El sistema las calcula para facilitar la gestión; "
            "no representan un estándar contable, médico ni contractual externo."
        )


def render_facturacion_industrial(
    df_original: pd.DataFrame,
    module_name: str,
    table: str,
) -> None:
    """
    Panel ejecutivo de facturación VM / VMR.

    IMPORTANTE:
    - Trabaja únicamente sobre una copia del DataFrame.
    - No escribe, no renombra y no reordena columnas en Google Sheets.
    - Las pestañas Cargar / Editar / Importar siguen usando el flujo existente.
    """
    import calendar
    import re
    import unicodedata

    # ---------------------------------------------------------
    # 1. SEGURIDAD Y NORMALIZACIÓN LOCAL (SOLO LECTURA)
    # ---------------------------------------------------------
    if df_original is None or df_original.empty:
        st.warning("No hay registros cargados en esta planilla.")
        return

    data = df_original.copy(deep=True)
    key = "fact_ind_" + re.sub(r"[^a-z0-9]+", "_", str(table).lower()).strip("_")
    empresa = "VMR" if str(table).lower().endswith("_vmr") else "VM"

    def texto_limpio(valor: Any) -> str:
        if pd.isna(valor):
            return ""
        return str(valor).strip()

    def normalizar_texto(valor: Any) -> str:
        txt = texto_limpio(valor).lower()
        txt = unicodedata.normalize("NFKD", txt)
        txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt

    def nombre_normalizado(valor: Any) -> str:
        txt = normalizar_texto(valor)
        return re.sub(r"[^a-z0-9]+", "_", txt).strip("_")

    columnas_normalizadas = {
        nombre_normalizado(columna): columna
        for columna in data.columns
    }

    def detectar_columna(*candidatas: str):
        for candidata in candidatas:
            encontrada = columnas_normalizadas.get(nombre_normalizado(candidata))
            if encontrada is not None:
                return encontrada
        return None

    def serie_texto(columna):
        if columna is None or columna not in data.columns:
            return pd.Series([""] * len(data), index=data.index, dtype="object")
        serie = data.loc[:, columna]
        if isinstance(serie, pd.DataFrame):
            serie = serie.iloc[:, 0]
        return serie.map(texto_limpio)

    def serie_fecha(columna):
        if columna is None or columna not in data.columns:
            return pd.Series(pd.NaT, index=data.index, dtype="datetime64[ns]")

        original = data.loc[:, columna]
        if isinstance(original, pd.DataFrame):
            original = original.iloc[:, 0]

        raw = original.astype(str).str.strip()
        fechas = pd.to_datetime(raw, format="%Y-%m-%d", errors="coerce")

        faltantes = fechas.isna()
        if faltantes.any():
            fechas.loc[faltantes] = pd.to_datetime(
                raw.loc[faltantes],
                dayfirst=True,
                errors="coerce",
            )

        # También admite fechas serializadas de Excel / Google Sheets.
        numeros = pd.to_numeric(original, errors="coerce")
        seriales = fechas.isna() & numeros.between(20000, 80000, inclusive="both")
        if seriales.any():
            fechas.loc[seriales] = (
                pd.Timestamp("1899-12-30")
                + pd.to_timedelta(numeros.loc[seriales], unit="D")
            )

        return fechas.dt.normalize()

    def lista_opciones(serie: pd.Series):
        valores = (
            serie.fillna("")
            .astype(str)
            .str.strip()
        )
        valores = valores[valores.ne("")]
        return sorted(valores.unique().tolist(), key=lambda x: x.casefold())

    col_mes = detectar_columna("mes", "fecha", "fecha procedimiento", "fecha práctica")
    col_afiliado = detectar_columna("afiliado", "paciente", "nombre paciente")
    col_obra_social = detectar_columna("obra_social", "obra social", "financiador")
    col_procedimiento = detectar_columna("procedimiento", "práctica", "practica")
    col_medico = detectar_columna(
        "medico_responsable",
        "médico responsable",
        "medico responsable",
        "médico",
        "medico",
    )
    col_fecha_factura = detectar_columna("fecha_factura", "fecha factura")
    col_numero_factura = detectar_columna(
        "numero_factura",
        "número factura",
        "numero factura",
        "n° factura",
        "factura",
    )
    col_vencimiento = detectar_columna("vencimiento", "fecha vencimiento")
    col_fecha_pago = detectar_columna("fecha_pago", "fecha pago", "fecha cobro")
    col_monto = detectar_columna(
        "valor_pesos",
        "valor pesos",
        "importe",
        "monto",
        "facturado",
        "total",
    )
    col_estado = detectar_columna("estado", "estado factura", "situación", "situacion")
    col_observaciones = detectar_columna("observaciones", "observación", "observacion")

    if col_monto is None:
        st.error(
            "No encontré la columna de importe. El panel espera una columna como "
            "'valor_pesos', 'importe', 'monto', 'facturado' o 'total'."
        )
        return

    data["_fecha_servicio"] = serie_fecha(col_mes)
    data["_fecha_factura"] = serie_fecha(col_fecha_factura)
    data["_vencimiento"] = serie_fecha(col_vencimiento)
    data["_fecha_pago"] = serie_fecha(col_fecha_pago)
    data["_fecha_base"] = data["_fecha_servicio"].combine_first(data["_fecha_factura"])
    data["_fecha_base"] = data["_fecha_base"].combine_first(data["_vencimiento"])

    data["_afiliado"] = serie_texto(col_afiliado)
    data["_obra_social"] = serie_texto(col_obra_social)
    data["_procedimiento"] = serie_texto(col_procedimiento)
    data["_medico"] = serie_texto(col_medico)
    data["_numero_factura"] = serie_texto(col_numero_factura)
    data["_estado_original"] = serie_texto(col_estado)
    data["_estado_norm"] = data["_estado_original"].map(normalizar_texto)
    data["_observaciones"] = serie_texto(col_observaciones)
    data["_monto"] = pd.to_numeric(
        data[col_monto].apply(money),
        errors="coerce",
    ).fillna(0.0)

    estados_cobrados = (
        "cobrado",
        "pagado",
        "completo",
        "completado",
        "finalizado",
        "cancelado pago",
    )
    estados_anulados = (
        "anulado",
        "anulada",
        "cancelado",
        "cancelada",
        "baja",
    )

    data["_es_anulado"] = data["_estado_norm"].apply(
        lambda x: any(palabra in x for palabra in estados_anulados)
    )
    data["_es_cobrado"] = data["_estado_norm"].apply(
        lambda x: any(palabra in x for palabra in estados_cobrados)
    ) | data["_fecha_pago"].notna()
    data.loc[data["_es_anulado"], "_es_cobrado"] = False

    data["_cobrado"] = data["_monto"].where(data["_es_cobrado"], 0.0)
    data["_pendiente"] = data["_monto"].where(
        ~data["_es_cobrado"] & ~data["_es_anulado"],
        0.0,
    )
    data["_anulado"] = data["_monto"].where(data["_es_anulado"], 0.0)

    hoy = pd.Timestamp.today().normalize()
    data["_dias_vencido"] = (hoy - data["_vencimiento"]).dt.days
    data["_esta_vencido"] = (
        data["_pendiente"].gt(0)
        & data["_vencimiento"].notna()
        & data["_vencimiento"].lt(hoy)
    )

    def estado_ejecutivo(fila):
        if bool(fila["_es_anulado"]):
            return "Anulado"
        if bool(fila["_es_cobrado"]):
            return "Cobrado"
        if bool(fila["_esta_vencido"]):
            return "Vencido"
        if fila["_pendiente"] > 0:
            return "Pendiente"
        return "Sin importe"

    data["_estado_ejecutivo"] = data.apply(estado_ejecutivo, axis=1)

    # ---------------------------------------------------------
    # 2. CABECERA Y FILTROS EJECUTIVOS
    # ---------------------------------------------------------
    st.markdown(
        f"""
        <div style="padding:16px 18px;border:1px solid rgba(128,128,128,.22);border-radius:14px;margin-bottom:12px;">
            <div style="font-size:1.35rem;font-weight:750;">🏥 Centro de Control de Facturación {empresa}</div>
            <div style="opacity:.72;margin-top:4px;">Producción, cobranza, vencimientos, concentración y calidad de datos en una sola vista.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    fecha_min = data["_fecha_base"].min()
    fecha_max = data["_fecha_base"].max()

    # El selector de período queda separado para que, en celular,
    # el rango personalizado aparezca inmediatamente debajo y no quede oculto
    # después de los demás filtros.
    periodo = st.selectbox(
        "Período",
        [
            "Año actual",
            "Mes actual",
            "Mes anterior",
            "Últimos 30 días",
            "Últimos 90 días",
            "Todo el historial",
            "Personalizado",
        ],
        index=0,
        key=f"{key}_periodo",
    )

    desde = None
    hasta = None

    # Guarda el período anterior para detectar el momento exacto en que
    # el usuario cambia a "Personalizado". Así se carga automáticamente
    # el último mes, pero luego permite modificar las fechas sin resetearlas.
    periodo_anterior = st.session_state.get(f"{key}_periodo_anterior")

    if periodo == "Personalizado":
        valor_hasta = hoy.date()
        valor_desde = (hoy - pd.DateOffset(months=1)).date()

        # Al entrar en Personalizado, reemplaza cualquier rango antiguo
        # (por ejemplo, fechas de 2015) por: hoy y un mes hacia atrás.
        if periodo_anterior != "Personalizado":
            st.session_state[f"{key}_desde"] = valor_desde
            st.session_state[f"{key}_hasta"] = valor_hasta

        st.markdown("**📅 Rango personalizado**")
        p1, p2 = st.columns(2)
        with p1:
            desde_fecha = st.date_input(
                "Desde",
                value=st.session_state.get(f"{key}_desde", valor_desde),
                key=f"{key}_desde",
            )
        with p2:
            hasta_fecha = st.date_input(
                "Hasta",
                value=st.session_state.get(f"{key}_hasta", valor_hasta),
                key=f"{key}_hasta",
            )

        desde = pd.Timestamp(desde_fecha).normalize()
        hasta = pd.Timestamp(hasta_fecha).normalize()

        if desde > hasta:
            st.error("La fecha 'Desde' no puede ser posterior a la fecha 'Hasta'.")
            return

        st.caption(
            f"Filtro activo: {desde.strftime('%d/%m/%Y')} al "
            f"{hasta.strftime('%d/%m/%Y')}."
        )
    elif periodo == "Mes actual":
        desde = hoy.replace(day=1)
        hasta = hoy
    elif periodo == "Mes anterior":
        # Mes calendario completo anterior al actual.
        primer_dia_mes_actual = hoy.replace(day=1)
        hasta = primer_dia_mes_actual - pd.Timedelta(days=1)
        desde = hasta.replace(day=1)
    elif periodo == "Últimos 30 días":
        desde = hoy - pd.Timedelta(days=29)
        hasta = hoy
    elif periodo == "Últimos 90 días":
        desde = hoy - pd.Timedelta(days=89)
        hasta = hoy
    elif periodo == "Año actual":
        desde = pd.Timestamp(year=hoy.year, month=1, day=1)
        hasta = hoy

    # Se actualiza después de procesar el período para no pisar las fechas
    # mientras el usuario las está modificando.
    st.session_state[f"{key}_periodo_anterior"] = periodo

    f2, f3 = st.columns([1.8, 1.0])
    with f2:
        buscar = st.text_input(
            "Buscar",
            placeholder="Paciente, factura, obra social, procedimiento, médico...",
            key=f"{key}_buscar",
        )
    with f3:
        solo_con_importe = st.checkbox(
            "Excluir importes en $ 0",
            value=True,
            key=f"{key}_solo_importe",
        )

    filtros_1 = st.columns(4)
    with filtros_1[0]:
        estados_seleccionados = st.multiselect(
            "Estado ejecutivo",
            ["Cobrado", "Pendiente", "Vencido", "Anulado", "Sin importe"],
            default=[],
            placeholder="Todos",
            key=f"{key}_estados",
        )
    with filtros_1[1]:
        obras_seleccionadas = st.multiselect(
            "Obra social",
            lista_opciones(data["_obra_social"]),
            default=[],
            placeholder="Todas",
            key=f"{key}_obras",
        )
    with filtros_1[2]:
        procedimientos_seleccionados = st.multiselect(
            "Procedimiento",
            lista_opciones(data["_procedimiento"]),
            default=[],
            placeholder="Todos",
            key=f"{key}_procedimientos",
        )
    with filtros_1[3]:
        medicos_seleccionados = st.multiselect(
            "Médico",
            lista_opciones(data["_medico"]),
            default=[],
            placeholder="Todos",
            key=f"{key}_medicos",
        )

    filtrado = data.copy()

    if desde is not None and hasta is not None:
        filtrado = filtrado[
            filtrado["_fecha_base"].between(desde, hasta, inclusive="both")
        ]
    if solo_con_importe:
        filtrado = filtrado[filtrado["_monto"].ne(0)]
    if estados_seleccionados:
        filtrado = filtrado[
            filtrado["_estado_ejecutivo"].isin(estados_seleccionados)
        ]
    if obras_seleccionadas:
        filtrado = filtrado[filtrado["_obra_social"].isin(obras_seleccionadas)]
    if procedimientos_seleccionados:
        filtrado = filtrado[
            filtrado["_procedimiento"].isin(procedimientos_seleccionados)
        ]
    if medicos_seleccionados:
        filtrado = filtrado[filtrado["_medico"].isin(medicos_seleccionados)]

    if buscar.strip():
        termino = normalizar_texto(buscar)
        buscador = (
            filtrado["_afiliado"].map(normalizar_texto)
            + " " + filtrado["_numero_factura"].map(normalizar_texto)
            + " " + filtrado["_obra_social"].map(normalizar_texto)
            + " " + filtrado["_procedimiento"].map(normalizar_texto)
            + " " + filtrado["_medico"].map(normalizar_texto)
            + " " + filtrado["_observaciones"].map(normalizar_texto)
        )
        filtrado = filtrado[buscador.str.contains(re.escape(termino), na=False)]

    if filtrado.empty:
        st.warning(
            "Los filtros actuales no devuelven registros. Cambiá el período o quitá algún filtro."
        )
        if pd.notna(fecha_min) and pd.notna(fecha_max):
            st.caption(
                f"La planilla contiene fechas desde {fecha_min.strftime('%d/%m/%Y')} "
                f"hasta {fecha_max.strftime('%d/%m/%Y')}."
            )
        return

    # ---------------------------------------------------------
    # 3. MÉTRICAS INDUSTRIALES
    # ---------------------------------------------------------
    activos = filtrado[~filtrado["_es_anulado"]].copy()
    facturado = float(activos["_monto"].sum())
    cobrado = float(activos["_cobrado"].sum())
    pendiente = float(activos["_pendiente"].sum())
    vencido = float(activos.loc[activos["_esta_vencido"], "_pendiente"].sum())
    anulado = float(filtrado["_anulado"].sum())
    registros = int(len(activos))
    pacientes = int(activos["_afiliado"].replace("", pd.NA).nunique())
    ticket = facturado / registros if registros else 0.0
    tasa_cobro = cobrado / facturado * 100 if facturado > 0 else 0.0
    mora = vencido / pendiente * 100 if pendiente > 0 else 0.0

    dias_cobro = (
        activos.loc[
            activos["_es_cobrado"]
            & activos["_fecha_pago"].notna()
            & activos["_fecha_factura"].notna(),
            "_fecha_pago",
        ]
        - activos.loc[
            activos["_es_cobrado"]
            & activos["_fecha_pago"].notna()
            & activos["_fecha_factura"].notna(),
            "_fecha_factura",
        ]
    ).dt.days
    dias_cobro = dias_cobro[dias_cobro.ge(0)]
    dso = float(dias_cobro.mean()) if not dias_cobro.empty else 0.0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("💰 Facturado activo", fmt_money(facturado))
    k2.metric("✅ Cobrado", fmt_money(cobrado), f"{tasa_cobro:.1f}% del facturado")
    k3.metric("⏳ Pendiente", fmt_money(pendiente))
    k4.metric("🚨 Vencido", fmt_money(vencido), f"{mora:.1f}% de la cartera")

    k5, k6, k7, k8 = st.columns(4)
    k5.metric("📄 Registros", f"{registros:,}".replace(",", "."))
    k6.metric("👥 Pacientes", f"{pacientes:,}".replace(",", "."))
    k7.metric("🎫 Ticket promedio", fmt_money(ticket))
    k8.metric("⏱️ Días promedio de cobro", f"{dso:.1f}" if dso else "Sin dato")

    if anulado > 0:
        st.caption(f"Importe anulado dentro del filtro: {fmt_money(anulado)}")

    # ---------------------------------------------------------
    # 4. PESTAÑAS DE CONTROL
    # ---------------------------------------------------------
    tab_resumen, tab_cobranza, tab_produccion, tab_calidad, tab_detalle = st.tabs(
        [
            "📊 Resumen ejecutivo",
            "💳 Cobranza y vencimientos",
            "🩺 Producción médica",
            "🛡️ Calidad de datos",
            "📋 Detalle",
        ]
    )

    with tab_resumen:
        mensual = activos[activos["_fecha_base"].notna()].copy()
        if not mensual.empty:
            mensual["_periodo"] = mensual["_fecha_base"].dt.to_period("M").astype(str)
            evolucion = (
                mensual.groupby("_periodo", as_index=False)
                .agg(
                    Facturado=("_monto", "sum"),
                    Cobrado=("_cobrado", "sum"),
                    Pendiente=("_pendiente", "sum"),
                    Registros=("_monto", "size"),
                )
                .sort_values("_periodo")
            )

            mejor_fila = evolucion.loc[evolucion["Facturado"].idxmax()]
            meses_observados = max(int(evolucion["_periodo"].nunique()), 1)
            promedio_mensual = facturado / meses_observados

            proyeccion = 0.0
            if periodo == "Año actual":
                dias_anio = 366 if calendar.isleap(hoy.year) else 365
                proyeccion = facturado / max(hoy.dayofyear, 1) * dias_anio
            elif meses_observados > 0:
                proyeccion = promedio_mensual * 12

            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Mejor mes", str(mejor_fila["_periodo"]))
            r2.metric("Facturación mejor mes", fmt_money(mejor_fila["Facturado"]))
            r3.metric("Promedio mensual observado", fmt_money(promedio_mensual))
            r4.metric("Proyección anual", fmt_money(proyeccion))

            evolucion_larga = evolucion.melt(
                id_vars=["_periodo"],
                value_vars=["Facturado", "Cobrado", "Pendiente"],
                var_name="Serie",
                value_name="Importe",
            )
            fig_evolucion = px.bar(
                evolucion_larga,
                x="_periodo",
                y="Importe",
                color="Serie",
                barmode="group",
                title="Evolución mensual: facturado, cobrado y pendiente",
                labels={"_periodo": "Mes"},
            )
            fig_evolucion.update_layout(height=430, legend_title_text="")
            st.plotly_chart(fig_evolucion, use_container_width=True)

        c_obra, c_estado = st.columns(2)
        with c_obra:
            ranking_obra = (
                activos.assign(
                    _obra=activos["_obra_social"].replace("", "Sin obra social")
                )
                .groupby("_obra", as_index=False)
                .agg(Facturado=("_monto", "sum"), Pendiente=("_pendiente", "sum"))
                .sort_values("Facturado", ascending=False)
                .head(12)
            )
            if not ranking_obra.empty:
                fig_obra = px.bar(
                    ranking_obra.sort_values("Facturado"),
                    x="Facturado",
                    y="_obra",
                    orientation="h",
                    title="Top obras sociales por facturación",
                    labels={"_obra": "Obra social"},
                )
                fig_obra.update_layout(height=430)
                st.plotly_chart(fig_obra, use_container_width=True)

        with c_estado:
            composicion = (
                filtrado.groupby("_estado_ejecutivo", as_index=False)["_monto"]
                .sum()
                .rename(columns={"_estado_ejecutivo": "Estado", "_monto": "Importe"})
            )
            if not composicion.empty and composicion["Importe"].sum() > 0:
                fig_estado = px.pie(
                    composicion,
                    names="Estado",
                    values="Importe",
                    hole=0.58,
                    title="Composición de la cartera",
                )
                fig_estado.update_layout(height=430)
                st.plotly_chart(fig_estado, use_container_width=True)

        render_lectura_ejecutiva_facturacion_pro(
            activos=activos,
            filtrado=filtrado,
            facturado=facturado,
            cobrado=cobrado,
            pendiente=pendiente,
            vencido=vencido,
            tasa_cobro=tasa_cobro,
            mora=mora,
            dso=dso,
            hoy=hoy,
            periodo=periodo,
        )

    with tab_cobranza:
        pendientes = activos[activos["_pendiente"].gt(0)].copy()

        if pendientes.empty:
            st.success("No hay saldos pendientes dentro del filtro seleccionado.")
        else:
            condiciones = [
                pendientes["_vencimiento"].isna(),
                pendientes["_vencimiento"].ge(hoy),
                pendientes["_dias_vencido"].between(1, 30, inclusive="both"),
                pendientes["_dias_vencido"].between(31, 60, inclusive="both"),
                pendientes["_dias_vencido"].between(61, 90, inclusive="both"),
                pendientes["_dias_vencido"].gt(90),
            ]
            etiquetas = [
                "Sin vencimiento",
                "A vencer",
                "1-30 días",
                "31-60 días",
                "61-90 días",
                "Más de 90 días",
            ]
            pendientes["_tramo_mora"] = "Sin clasificar"
            for condicion, etiqueta in zip(condiciones, etiquetas):
                pendientes.loc[condicion, "_tramo_mora"] = etiqueta

            orden_tramos = etiquetas + ["Sin clasificar"]
            aging = (
                pendientes.groupby("_tramo_mora", as_index=False)
                .agg(Importe=("_pendiente", "sum"), Registros=("_pendiente", "size"))
            )
            aging["_orden"] = aging["_tramo_mora"].map(
                {nombre: numero for numero, nombre in enumerate(orden_tramos)}
            )
            aging = aging.sort_values("_orden")

            a1, a2 = st.columns([1.05, 1.45])
            with a1:
                fig_aging = px.bar(
                    aging,
                    x="_tramo_mora",
                    y="Importe",
                    title="Antigüedad de saldos pendientes",
                    labels={"_tramo_mora": "Tramo"},
                    text_auto=".2s",
                )
                fig_aging.update_layout(height=430, xaxis_tickangle=-25)
                st.plotly_chart(fig_aging, use_container_width=True)
            with a2:
                cartera_obra = (
                    pendientes.assign(
                        _obra=pendientes["_obra_social"].replace("", "Sin obra social")
                    )
                    .groupby("_obra", as_index=False)
                    .agg(Pendiente=("_pendiente", "sum"), Vencido=("_esta_vencido", "sum"))
                    .sort_values("Pendiente", ascending=False)
                    .head(15)
                )
                fig_cartera = px.bar(
                    cartera_obra.sort_values("Pendiente"),
                    x="Pendiente",
                    y="_obra",
                    orientation="h",
                    title="Cartera pendiente por obra social",
                    labels={"_obra": "Obra social"},
                )
                fig_cartera.update_layout(height=430)
                st.plotly_chart(fig_cartera, use_container_width=True)

            proximos_15 = pendientes[
                pendientes["_vencimiento"].between(
                    hoy,
                    hoy + pd.Timedelta(days=15),
                    inclusive="both",
                )
            ]
            vencidos_df = pendientes[pendientes["_esta_vencido"]].copy()

            c1, c2, c3 = st.columns(3)
            c1.metric("Vencidos", f"{len(vencidos_df)} registros")
            c2.metric("Monto vencido", fmt_money(vencidos_df["_pendiente"].sum()))
            c3.metric("A vencer en 15 días", fmt_money(proximos_15["_pendiente"].sum()))

            st.markdown("### Prioridad de cobranza")
            prioridad = pendientes.copy()
            prioridad["_prioridad"] = (
                prioridad["_esta_vencido"].astype(int) * 1_000_000_000_000
                + prioridad["_pendiente"]
            )
            prioridad = prioridad.sort_values("_prioridad", ascending=False).head(30)
            tabla_prioridad = pd.DataFrame(
                {
                    "Paciente": prioridad["_afiliado"],
                    "Obra social": prioridad["_obra_social"],
                    "Procedimiento": prioridad["_procedimiento"],
                    "Factura": prioridad["_numero_factura"],
                    "Vencimiento": prioridad["_vencimiento"].dt.strftime("%d/%m/%Y").fillna(""),
                    "Días vencido": prioridad["_dias_vencido"].where(
                        prioridad["_esta_vencido"], 0
                    ).fillna(0).astype(int),
                    "Pendiente": prioridad["_pendiente"].apply(fmt_money),
                    "Estado": prioridad["_estado_ejecutivo"],
                }
            )
            st.dataframe(tabla_prioridad, use_container_width=True, hide_index=True)

    with tab_produccion:
        p1, p2 = st.columns(2)
        with p1:
            por_medico = (
                activos.assign(_medico_label=activos["_medico"].replace("", "Sin médico"))
                .groupby("_medico_label", as_index=False)
                .agg(
                    Facturado=("_monto", "sum"),
                    Pacientes=("_afiliado", lambda s: s.replace("", pd.NA).nunique()),
                    Procedimientos=("_monto", "size"),
                )
                .sort_values("Facturado", ascending=False)
                .head(15)
            )
            fig_medico = px.bar(
                por_medico.sort_values("Facturado"),
                x="Facturado",
                y="_medico_label",
                orientation="h",
                title="Facturación por médico",
                labels={"_medico_label": "Médico"},
            )
            fig_medico.update_layout(height=500)
            st.plotly_chart(fig_medico, use_container_width=True)

        with p2:
            por_procedimiento = (
                activos.assign(
                    _procedimiento_label=activos["_procedimiento"].replace(
                        "", "Sin procedimiento"
                    )
                )
                .groupby("_procedimiento_label", as_index=False)
                .agg(Facturado=("_monto", "sum"), Casos=("_monto", "size"))
                .sort_values("Facturado", ascending=False)
                .head(15)
            )
            fig_proc = px.bar(
                por_procedimiento.sort_values("Facturado"),
                x="Facturado",
                y="_procedimiento_label",
                orientation="h",
                title="Facturación por procedimiento",
                labels={"_procedimiento_label": "Procedimiento"},
            )
            fig_proc.update_layout(height=500)
            st.plotly_chart(fig_proc, use_container_width=True)

        st.markdown("### Matriz médico × procedimiento")
        matriz_base = activos.copy()
        matriz_base["_medico"] = matriz_base["_medico"].replace("", "Sin médico")
        matriz_base["_procedimiento"] = matriz_base["_procedimiento"].replace(
            "", "Sin procedimiento"
        )
        matriz = pd.pivot_table(
            matriz_base,
            index="_medico",
            columns="_procedimiento",
            values="_monto",
            aggfunc="sum",
            fill_value=0,
        )
        if not matriz.empty:
            matriz["TOTAL"] = matriz.sum(axis=1)
            matriz = matriz.sort_values("TOTAL", ascending=False)
            # Compatible con todas las versiones de pandas:
            # DataFrame.apply + Series.map, sin usar DataFrame.map/applymap.
            matriz_mostrar = pd.DataFrame(
                [
                    [fmt_money(valor) for valor in fila]
                    for fila in matriz.values.tolist()
                ],
                index=matriz.index,
                columns=matriz.columns,
            )
            st.dataframe(matriz_mostrar, use_container_width=True)

    with tab_calidad:
        sin_fecha = int(data["_fecha_base"].isna().sum())
        sin_paciente = int(data["_afiliado"].eq("").sum())
        sin_obra = int(data["_obra_social"].eq("").sum())
        sin_procedimiento = int(data["_procedimiento"].eq("").sum())
        sin_medico = int(data["_medico"].eq("").sum())
        importe_cero = int(data["_monto"].eq(0).sum())
        sin_factura = int(
            data["_numero_factura"].eq("").sum()
        ) if col_numero_factura else len(data)

        columnas_dup = ["_fecha_servicio", "_afiliado", "_procedimiento", "_monto"]
        duplicados_mask = data.duplicated(subset=columnas_dup, keep=False)
        duplicados = int(duplicados_mask.sum())

        q1, q2, q3, q4 = st.columns(4)
        q1.metric("Sin fecha", sin_fecha)
        q2.metric("Sin paciente", sin_paciente)
        q3.metric("Importe en cero", importe_cero)
        q4.metric("Posibles duplicados", duplicados)

        q5, q6, q7, q8 = st.columns(4)
        q5.metric("Sin obra social", sin_obra)
        q6.metric("Sin procedimiento", sin_procedimiento)
        q7.metric("Sin médico", sin_medico)
        q8.metric("Sin N.º factura", sin_factura)

        problemas = sum(
            [
                sin_fecha,
                sin_paciente,
                sin_obra,
                sin_procedimiento,
                sin_medico,
                importe_cero,
                duplicados,
            ]
        )
        if problemas == 0:
            st.success("✅ No se detectaron problemas evidentes en la planilla.")
        else:
            st.warning(
                "Este control es informativo: no modifica ni elimina ninguna fila del Sheet."
            )

        if duplicados:
            st.markdown("### Filas posiblemente duplicadas")
            dup = data[duplicados_mask].sort_values("_fecha_servicio", ascending=False)
            tabla_dup = pd.DataFrame(
                {
                    "Fecha": dup["_fecha_servicio"].dt.strftime("%d/%m/%Y").fillna(""),
                    "Paciente": dup["_afiliado"],
                    "Procedimiento": dup["_procedimiento"],
                    "Médico": dup["_medico"],
                    "Importe": dup["_monto"].apply(fmt_money),
                }
            )
            st.dataframe(tabla_dup, use_container_width=True, hide_index=True)

        columnas_detectadas = pd.DataFrame(
            {
                "Dato requerido": [
                    "Fecha del procedimiento",
                    "Paciente / afiliado",
                    "Obra social",
                    "Procedimiento",
                    "Médico responsable",
                    "Fecha factura",
                    "N.º factura",
                    "Vencimiento",
                    "Fecha pago",
                    "Importe",
                    "Estado",
                ],
                "Columna detectada en Sheet": [
                    col_mes or "No encontrada",
                    col_afiliado or "No encontrada",
                    col_obra_social or "No encontrada",
                    col_procedimiento or "No encontrada",
                    col_medico or "No encontrada",
                    col_fecha_factura or "No encontrada",
                    col_numero_factura or "No encontrada",
                    col_vencimiento or "No encontrada",
                    col_fecha_pago or "No encontrada",
                    col_monto or "No encontrada",
                    col_estado or "No encontrada",
                ],
            }
        )
        st.markdown("### Mapa de lectura del Sheet")
        st.dataframe(columnas_detectadas, use_container_width=True, hide_index=True)

    with tab_detalle:
        detalle = filtrado.copy().sort_values("_fecha_base", ascending=False)
        tabla_detalle = pd.DataFrame(
            {
                "Fecha": detalle["_fecha_servicio"].dt.strftime("%d/%m/%Y").fillna(""),
                "Paciente / afiliado": detalle["_afiliado"],
                "Obra social": detalle["_obra_social"],
                "Procedimiento": detalle["_procedimiento"],
                "Médico": detalle["_medico"],
                "Fecha factura": detalle["_fecha_factura"].dt.strftime("%d/%m/%Y").fillna(""),
                "N.º factura": detalle["_numero_factura"],
                "Vencimiento": detalle["_vencimiento"].dt.strftime("%d/%m/%Y").fillna(""),
                "Fecha pago": detalle["_fecha_pago"].dt.strftime("%d/%m/%Y").fillna(""),
                "Facturado": detalle["_monto"].apply(fmt_money),
                "Cobrado": detalle["_cobrado"].apply(fmt_money),
                "Pendiente": detalle["_pendiente"].apply(fmt_money),
                "Estado": detalle["_estado_ejecutivo"],
                "Estado original": detalle["_estado_original"],
            }
        )
        st.dataframe(tabla_detalle, use_container_width=True, hide_index=True, height=620)

        csv = tabla_detalle.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "📥 Descargar vista filtrada en CSV",
            data=csv,
            file_name=f"{table}_vista_filtrada.csv",
            mime="text/csv",
            key=f"{key}_descargar_csv",
        )

    # ---------------------------------------------------------
    # 5. ASISTENTE IA DEL MÓDULO
    # ---------------------------------------------------------
    st.divider()
    with st.expander("🤖 Asistente IA de facturación", expanded=False):
        pregunta = st.text_input(
            "Consultá sobre los registros filtrados",
            placeholder="Ej.: ¿Qué obra social concentra más deuda vencida?",
            key=f"{key}_pregunta_ia",
        )
        if st.button("Analizar con IA", key=f"{key}_btn_ia"):
            if not pregunta.strip():
                st.warning("Escribí una pregunta antes de consultar.")
            else:
                columnas_ia = [
                    c for c in data.columns
                    if not str(c).startswith("_")
                ]
                df_ia = filtrado[columnas_ia].copy()
                with st.spinner("Analizando la facturación filtrada..."):
                    respuesta = preguntar_ia(
                        modulo=module_name,
                        df=df_ia,
                        pregunta=pregunta,
                    )
                st.success(respuesta)



# =========================================================
# CONVENIOS QUIRÚRGICOS · CENTRO DE INTELIGENCIA PRO
# =========================================================
def _convenios_texto(valor: Any) -> str:
    """Convierte cualquier celda a texto limpio sin romper saltos útiles."""
    import math

    if valor is None:
        return ""
    try:
        if isinstance(valor, float) and math.isnan(valor):
            return ""
    except Exception:
        pass
    texto = str(valor).replace("\xa0", " ").replace("\r", "\n")
    lineas = [" ".join(linea.split()) for linea in texto.split("\n")]
    return "\n".join([linea for linea in lineas if linea]).strip()


def _convenios_normalizar_texto(valor: Any) -> str:
    import re
    import unicodedata

    texto = _convenios_texto(valor).lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9@.+%/-]+", " ", texto)
    return " ".join(texto.split())


def _convenios_numero(valor: Any):
    """Interpreta importes argentinos, números puros y celdas con símbolo $."""
    import re
    import pandas as pd

    if valor is None:
        return None
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        try:
            if pd.isna(valor):
                return None
        except Exception:
            pass
        return float(valor)

    texto = _convenios_texto(valor)
    if not texto:
        return None

    texto = texto.replace("$", "").replace("ARS", "").replace("ars", "")
    texto = texto.replace("\u200b", "").strip()
    texto = re.sub(r"[^0-9,.-]", "", texto)
    if not texto or texto in {"-", ".", ","}:
        return None

    try:
        if "," in texto and "." in texto:
            if texto.rfind(",") > texto.rfind("."):
                texto = texto.replace(".", "").replace(",", ".")
            else:
                texto = texto.replace(",", "")
        elif "," in texto:
            parte_decimal = texto.rsplit(",", 1)[-1]
            if len(parte_decimal) in (1, 2, 3):
                texto = texto.replace(".", "").replace(",", ".")
            else:
                texto = texto.replace(",", "")
        elif "." in texto:
            partes = texto.split(".")
            if len(partes) > 2 or (len(partes[-1]) == 3 and all(p.isdigit() for p in partes)):
                texto = "".join(partes)
        return float(texto)
    except Exception:
        return None


def _convenios_codigo(valor: Any) -> str:
    import re

    texto = _convenios_texto(valor)
    if not texto:
        return ""
    if texto.endswith(".0"):
        texto = texto[:-2]
    coincidencias = re.findall(r"\b\d{4,8}(?:[/.-]\d{1,4})?\b", texto)
    if coincidencias:
        return coincidencias[0].replace(" ", "")
    texto_limpio = re.sub(r"\s+", "", texto)
    if re.fullmatch(r"\d{3,10}", texto_limpio):
        return texto_limpio
    return ""


def _convenios_fecha_en_texto(valor: Any):
    import re
    import pandas as pd

    texto = _convenios_texto(valor)
    if not texto:
        return pd.NaT
    patrones = [
        r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
        r"\b\d{1,2}-\d{1,2}-\d{2,4}\b",
        r"\b\d{4}-\d{1,2}-\d{1,2}\b",
    ]
    for patron in patrones:
        match = re.search(patron, texto)
        if match:
            fecha = pd.to_datetime(match.group(0), dayfirst=True, errors="coerce")
            if pd.notna(fecha):
                return fecha
    return pd.NaT


def _convenios_df_a_matriz(df: pd.DataFrame) -> list[list[Any]]:
    """Reconstruye una matriz cruda, incluyendo los encabezados que get_df pudo consumir."""
    if df is None:
        return []
    data = df.copy()
    if data.empty and len(data.columns) == 0:
        return []

    columnas = [_convenios_texto(c) for c in data.columns.tolist()]
    filas = [columnas]
    if not data.empty:
        for fila in data.astype(object).where(pd.notna(data), "").values.tolist():
            filas.append([_convenios_texto(v) for v in fila])

    ancho = max((len(f) for f in filas), default=0)
    return [f + [""] * (ancho - len(f)) for f in filas]


def _convenios_limpiar_matriz(matriz: list[list[Any]]) -> list[list[str]]:
    if not matriz:
        return []
    limpia = [[_convenios_texto(v) for v in fila] for fila in matriz]
    # Elimina filas y columnas completamente vacías, preservando el orden original.
    limpia = [fila for fila in limpia if any(celda for celda in fila)]
    if not limpia:
        return []
    ancho = max(len(fila) for fila in limpia)
    limpia = [fila + [""] * (ancho - len(fila)) for fila in limpia]
    columnas_utiles = [
        i for i in range(ancho)
        if any(_convenios_texto(fila[i]) for fila in limpia)
    ]
    return [[fila[i] for i in columnas_utiles] for fila in limpia]


def _convenios_detectar_encabezado(matriz: list[list[str]], modo: str = "practicas") -> int:
    if not matriz:
        return 0

    if modo == "directorio":
        grupos = {
            "obra": ["obra social", "nombre de la obra", "prestadora"],
            "cuit": ["cuit"],
            "modalidad": ["modalidad", "facturacion"],
            "valores": ["valores", "plan"],
            "kairos": ["kairos"],
            "mail": ["mail", "correo", "email"],
            "liquidador": ["liquidador"],
        }
    else:
        grupos = {
            "codigo": ["codigo", "cod.", "cod ", "cód"],
            "descripcion": ["descripcion", "practica", "prestacion", "nomenclador"],
            "valor": ["valor", "importe", "arancel", "vigencia", "1/", "2/", "3/", "4/", "5/", "6/", "7/", "8/", "9/"],
            "observaciones": ["observacion", "tipo"],
        }

    mejor_indice = 0
    mejor_puntaje = -1
    limite = min(len(matriz), 35)
    for i in range(limite):
        fila = [_convenios_normalizar_texto(v) for v in matriz[i]]
        unidos = " | ".join(fila)
        puntaje = 0
        for nombre, sinonimos in grupos.items():
            encontrado = any(s in unidos for s in sinonimos)
            if encontrado:
                puntaje += 4 if nombre in {"codigo", "descripcion", "obra"} else 2
        puntaje += min(sum(bool(v) for v in fila), 6) * 0.15
        if puntaje > mejor_puntaje:
            mejor_puntaje = puntaje
            mejor_indice = i
    return mejor_indice


def _convenios_columna_por_alias(encabezados: list[str], aliases: list[str], excluir: set[int] | None = None):
    excluir = excluir or set()
    normalizados = [_convenios_normalizar_texto(v) for v in encabezados]
    for alias in aliases:
        alias_n = _convenios_normalizar_texto(alias)
        for i, encabezado in enumerate(normalizados):
            if i in excluir:
                continue
            if alias_n and (encabezado == alias_n or alias_n in encabezado):
                return i
    return None


def _convenios_extraer_metadata(matriz: list[list[str]]) -> dict[str, Any]:
    import re
    import pandas as pd

    primeras = matriz[:30]
    texto = "\n".join(" | ".join(fila) for fila in primeras if any(fila))
    texto_n = _convenios_normalizar_texto(texto)

    emails = sorted(set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", texto)))
    porcentajes = sorted(set(re.findall(r"(?:ips|kairos|medicamentos?)?\s*[+-]?\s*\d{1,3}\s*%", texto, flags=re.I)))
    fechas = []
    for patron in [r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", r"\b\d{4}-\d{1,2}-\d{1,2}\b"]:
        for valor in re.findall(patron, texto):
            fecha = pd.to_datetime(valor, dayfirst=True, errors="coerce")
            if pd.notna(fecha):
                fechas.append(fecha)

    notas_clave = []
    for fila in primeras:
        linea = " | ".join([c for c in fila if c])
        linea_n = _convenios_normalizar_texto(linea)
        if any(p in linea_n for p in [
            "ips", "kairos", "actualizacion", "mensual", "resolver expediente",
            "presentacion", "facturacion", "rehabilitado", "mail", "convenio",
        ]):
            notas_clave.append(linea)

    return {
        "emails": ", ".join(emails),
        "reglas_porcentuales": " · ".join(porcentajes),
        "ultima_fecha_detectada": max(fechas) if fechas else pd.NaT,
        "notas": "\n".join(dict.fromkeys(notas_clave[:12])),
        "menciona_ips": "ips" in texto_n,
        "menciona_kairos": "kairos" in texto_n,
    }


def _convenios_parsear_practicas(convenio: str, matriz_original: list[list[Any]], origen: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    import re
    import pandas as pd

    matriz = _convenios_limpiar_matriz(matriz_original)
    metadata = _convenios_extraer_metadata(matriz)
    if not matriz:
        return pd.DataFrame(), metadata

    idx_header = _convenios_detectar_encabezado(matriz, "practicas")
    encabezados = matriz[idx_header]
    encabezados_n = [_convenios_normalizar_texto(v) for v in encabezados]

    idx_codigo = _convenios_columna_por_alias(
        encabezados,
        ["codigo", "código", "cod. avalian", "cod avalian", "cod."],
    )
    idx_descripcion = _convenios_columna_por_alias(
        encabezados,
        ["descripcion de la practica", "descripcion", "descripción", "practica", "prestacion"],
        {idx_codigo} if idx_codigo is not None else set(),
    )
    idx_tipo = _convenios_columna_por_alias(encabezados, ["tipo"])
    idx_observaciones = _convenios_columna_por_alias(encabezados, ["observaciones", "observacion"])

    candidatos_valor = []
    for i, encabezado in enumerate(encabezados_n):
        if i in {idx_codigo, idx_descripcion, idx_tipo, idx_observaciones}:
            continue
        fecha = _convenios_fecha_en_texto(encabezados[i])
        if pd.notna(fecha):
            candidatos_valor.append((i, 100, fecha))
        elif any(p in encabezado for p in ["valor", "importe", "arancel", "precio"]):
            candidatos_valor.append((i, 80, pd.NaT))
        elif encabezado and re.search(r"\d", encabezado):
            candidatos_valor.append((i, 20, pd.NaT))

    if candidatos_valor:
        candidatos_valor.sort(key=lambda x: x[1], reverse=True)
        idx_valor = candidatos_valor[0][0]
        vigencia_header = candidatos_valor[0][2]
    else:
        idx_valor = None
        vigencia_header = pd.NaT

    # Fallback estructural para planillas con encabezados fusionados o consumidos por get_df.
    if idx_codigo is None or idx_descripcion is None:
        mejor = None
        for i in range(min(len(matriz), 40)):
            fila = matriz[i]
            for c in range(len(fila)):
                if _convenios_codigo(fila[c]):
                    for d in range(c + 1, min(c + 4, len(fila))):
                        texto_d = _convenios_texto(fila[d])
                        if len(texto_d) >= 12 and not _convenios_codigo(texto_d):
                            mejor = (i - 1 if i > 0 else 0, c, d)
                            break
                if mejor:
                    break
            if mejor:
                break
        if mejor:
            idx_header, idx_codigo, idx_descripcion = mejor
            encabezados = matriz[idx_header]
            if idx_valor is None:
                for c in range(idx_descripcion + 1, len(encabezados)):
                    valores_numericos = sum(
                        _convenios_numero(fila[c]) is not None
                        for fila in matriz[idx_header + 1: idx_header + 25]
                        if c < len(fila)
                    )
                    if valores_numericos >= 2:
                        idx_valor = c
                        break

    registros = []
    vacias_consecutivas = 0
    for numero_fila, fila in enumerate(matriz[idx_header + 1:], start=idx_header + 2):
        if not any(fila):
            vacias_consecutivas += 1
            if vacias_consecutivas >= 5:
                break
            continue
        vacias_consecutivas = 0

        codigo = _convenios_codigo(fila[idx_codigo]) if idx_codigo is not None and idx_codigo < len(fila) else ""
        descripcion = _convenios_texto(fila[idx_descripcion]) if idx_descripcion is not None and idx_descripcion < len(fila) else ""

        if not codigo:
            # Algunas hojas tienen una columna identificadora antes del código real.
            for c in range(min(3, len(fila))):
                codigo = _convenios_codigo(fila[c])
                if codigo:
                    if not descripcion:
                        for d in range(c + 1, min(c + 4, len(fila))):
                            if len(_convenios_texto(fila[d])) >= 10:
                                descripcion = _convenios_texto(fila[d])
                                break
                    break

        if not codigo or len(descripcion) < 4:
            continue

        # Evita volver a tomar encabezados internos.
        descripcion_n = _convenios_normalizar_texto(descripcion)
        if descripcion_n in {"descripcion", "descripcion de la practica", "practica"}:
            continue

        valor_raw = fila[idx_valor] if idx_valor is not None and idx_valor < len(fila) else ""
        valor = _convenios_numero(valor_raw)
        tipo = fila[idx_tipo] if idx_tipo is not None and idx_tipo < len(fila) else ""
        observaciones = fila[idx_observaciones] if idx_observaciones is not None and idx_observaciones < len(fila) else ""

        if not observaciones:
            extras = []
            for c, celda in enumerate(fila):
                if c in {idx_codigo, idx_descripcion, idx_valor, idx_tipo}:
                    continue
                texto_celda = _convenios_texto(celda)
                if texto_celda and not _convenios_codigo(texto_celda):
                    extras.append(texto_celda)
            observaciones = " · ".join(dict.fromkeys(extras[:3]))

        vigencia = vigencia_header
        if pd.isna(vigencia):
            vigencia = metadata.get("ultima_fecha_detectada", pd.NaT)

        registros.append({
            "convenio": convenio,
            "codigo": codigo,
            "descripcion": descripcion,
            "valor": valor,
            "vigencia": vigencia,
            "tipo": _convenios_texto(tipo),
            "observaciones": _convenios_texto(observaciones),
            "origen": origen,
            "fila_origen": numero_fila,
        })

    df = pd.DataFrame(registros)
    if df.empty:
        return df, metadata

    df["descripcion"] = df["descripcion"].str.replace(r"\s+", " ", regex=True).str.strip()
    df["codigo"] = df["codigo"].astype(str).str.strip()
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    df["vigencia"] = pd.to_datetime(df["vigencia"], errors="coerce")
    df["estado_valor"] = df["valor"].apply(lambda x: "Valorizada" if pd.notna(x) and x > 0 else "Sin valor")
    df["clave_practica"] = df["codigo"].astype(str) + " | " + df["descripcion"].astype(str)
    df = df.drop_duplicates(subset=["convenio", "codigo", "descripcion"], keep="last")
    return df.reset_index(drop=True), metadata


def _convenios_parsear_directorio(convenio: str, matriz_original: list[list[Any]], origen: str) -> pd.DataFrame:
    import re
    import pandas as pd

    matriz = _convenios_limpiar_matriz(matriz_original)
    if not matriz:
        return pd.DataFrame()
    idx_header = _convenios_detectar_encabezado(matriz, "directorio")
    encabezados = matriz[idx_header]

    # Solo activa el parser de directorio cuando la hoja realmente contiene
    # señales de padrón/prestadoras. Evita confundir descripciones quirúrgicas
    # largas con nombres de obras sociales.
    texto_encabezado = _convenios_normalizar_texto(" | ".join(encabezados))
    texto_superior = _convenios_normalizar_texto(
        " | ".join(" | ".join(fila) for fila in matriz[: min(len(matriz), 12)])
    )
    senales_directorio = [
        "obra social", "nombre de la obra", "prestadora", "cuit",
        "modalidad facturacion", "liquidador", "valores contemplados",
    ]
    cantidad_senales = sum(
        1 for señal in senales_directorio
        if señal in texto_encabezado or señal in texto_superior
    )
    if cantidad_senales < 2:
        return pd.DataFrame()

    idx_obra = _convenios_columna_por_alias(encabezados, ["nombre de la obra social", "obra social", "prestadora"])
    idx_cuit = _convenios_columna_por_alias(encabezados, ["cuit"])
    idx_modalidad = _convenios_columna_por_alias(encabezados, ["modalidad facturacion", "modalidad", "facturacion"])
    idx_valores = _convenios_columna_por_alias(encabezados, ["valores contemplados", "valores", "plan"])
    idx_kairos = _convenios_columna_por_alias(encabezados, ["kairos"])
    idx_mail = _convenios_columna_por_alias(encabezados, ["mails", "mail", "correo", "email"])
    idx_liquidador = _convenios_columna_por_alias(encabezados, ["liquidador"])

    registros = []
    for numero_fila, fila in enumerate(matriz[idx_header + 1:], start=idx_header + 2):
        if not any(fila):
            continue
        obra = fila[idx_obra] if idx_obra is not None and idx_obra < len(fila) else ""
        cuit = fila[idx_cuit] if idx_cuit is not None and idx_cuit < len(fila) else ""

        if not obra:
            # En algunas exportaciones queda un código interno antes del nombre.
            candidatos = [
                _convenios_texto(v) for v in fila[:4]
                if len(_convenios_texto(v)) >= 5
                and not re.fullmatch(r"[\d.,/-]+", _convenios_texto(v))
            ]
            obra = max(candidatos, key=len) if candidatos else ""
        if not obra:
            continue

        fila_unida = " | ".join(_convenios_texto(v) for v in fila if _convenios_texto(v))
        if not cuit:
            cuit_match = re.search(r"\b\d{2}-?\d{8}-?\d\b", fila_unida)
            cuit = cuit_match.group(0) if cuit_match else ""

        mail = fila[idx_mail] if idx_mail is not None and idx_mail < len(fila) else ""
        if not mail:
            emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", fila_unida)
            mail = ", ".join(emails)

        registros.append({
            "convenio": convenio,
            "obra_social": _convenios_texto(obra),
            "cuit": _convenios_texto(cuit),
            "modalidad_facturacion": _convenios_texto(fila[idx_modalidad]) if idx_modalidad is not None and idx_modalidad < len(fila) else "",
            "valores_planes": _convenios_texto(fila[idx_valores]) if idx_valores is not None and idx_valores < len(fila) else "",
            "kairos": _convenios_texto(fila[idx_kairos]) if idx_kairos is not None and idx_kairos < len(fila) else "",
            "mails": _convenios_texto(mail),
            "liquidador": _convenios_texto(fila[idx_liquidador]) if idx_liquidador is not None and idx_liquidador < len(fila) else "",
            "origen": origen,
            "fila_origen": numero_fila,
        })

    df = pd.DataFrame(registros)
    if not df.empty:
        df = df.drop_duplicates(subset=["obra_social", "cuit", "mails"], keep="last").reset_index(drop=True)
    return df



# =========================================================
# CONVENIOS · GUARDADO PERMANENTE EN GOOGLE SHEETS (VM / VMR)
# =========================================================
_CONVENIOS_SHEETS = {
    "VM": "convenios_vm",
    "VMR": "convenios_vmr",
}

_CONVENIOS_SHEET_COLUMNS = [
    "registro_id",
    "empresa",
    "tipo_registro",
    "obra_social",
    "plan",
    "codigo",
    "descripcion",
    "especialidad",
    "categoria",
    "modulo",
    "modelo_valor",
    "valor_ips",
    "porcentaje_ips",
    "valor_propio",
    "vigencia_desde",
    "vigencia_hasta",
    "activo",
    "requiere_autorizacion",
    "modalidad_facturacion",
    "fuente",
    "correo",
    "contacto",
    "observaciones",
    "actualizado_en",
]


def _convenios_id_registro(*partes: Any) -> str:
    """Crea un identificador estable para poder reemplazar la carga sin duplicados."""
    import hashlib

    texto = "|".join(_convenios_texto(p) for p in partes)
    return "cv_" + hashlib.sha1(texto.encode("utf-8")).hexdigest()[:20]


def _convenios_fecha_iso(valor: Any) -> str:
    import pandas as pd

    fecha = pd.to_datetime(valor, errors="coerce", dayfirst=True)
    if pd.isna(fecha):
        return ""
    return fecha.strftime("%Y-%m-%d")


def _convenios_porcentaje_texto(valor: Any) -> float:
    import re

    texto = _convenios_texto(valor)
    match = re.search(r"([+-]?\d+(?:[.,]\d+)?)\s*%", texto)
    if not match:
        return 0.0
    try:
        return float(match.group(1).replace(",", "."))
    except Exception:
        return 0.0


def _convenios_nombre_base(nombre: Any) -> str:
    """Devuelve el nombre del convenio sin el prefijo VM / VMR."""
    texto = _convenios_texto(nombre)
    if "·" in texto:
        texto = texto.split("·", 1)[1].strip()
    normalizado = _convenios_normalizar_texto(texto)
    if normalizado.startswith("vmr "):
        texto = texto[3:].lstrip(" -|·")
    elif normalizado.startswith("vm "):
        texto = texto[2:].lstrip(" -|·")
    return _convenios_texto(texto)


def _convenios_modelo_sugerido(convenio: str, sub: pd.DataFrame, meta: pd.DataFrame) -> tuple[str, float]:
    base = _convenios_nombre_base(convenio)
    texto_meta = ""
    menciona_ips = base.upper() == "IPS"
    if not meta.empty:
        fila = meta.iloc[0]
        texto_meta = " | ".join([
            _convenios_texto(fila.get("reglas_porcentuales", "")),
            _convenios_texto(fila.get("notas", "")),
        ])
        menciona_ips = menciona_ips or bool(fila.get("menciona_ips", False))

    porcentaje = _convenios_porcentaje_texto(texto_meta)
    if base.upper() == "IPS":
        return "IPS PURO", 0.0
    if menciona_ips and porcentaje != 0:
        return "IPS + %", porcentaje
    if menciona_ips:
        return "IPS PURO", 0.0
    if not sub.empty and "valor" in sub.columns and sub["valor"].notna().any():
        return "VALOR PROPIO", 0.0
    return "A CONVENIR", 0.0


def _convenios_construir_base_sheet(
    matrices: dict[str, list[list[Any]]],
    empresa: str,
    fuente: str,
) -> pd.DataFrame:
    """Transforma el Excel heterogéneo en una tabla fija para convenios_vm/vmr."""
    import json
    from datetime import datetime
    import pandas as pd

    empresa = _convenios_texto(empresa).upper()
    if empresa not in _CONVENIOS_SHEETS:
        raise ValueError("Empresa inválida. Debe ser VM o VMR.")

    practicas, directorio, metadata = _convenios_preparar_datos(matrices, fuente)
    actualizado = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    filas: list[dict[str, Any]] = []

    convenios = sorted(
        set(_convenios_nombre_base(c) for c in matrices.keys())
        | set(practicas.get("convenio", pd.Series(dtype=str)).map(_convenios_nombre_base).tolist())
        | set(directorio.get("convenio", pd.Series(dtype=str)).map(_convenios_nombre_base).tolist())
    )
    convenios = [c for c in convenios if _convenios_texto(c)]

    for convenio in convenios:
        sub = practicas[
            practicas["convenio"].map(_convenios_nombre_base) == convenio
        ].copy() if not practicas.empty else pd.DataFrame()
        sub_dir = directorio[
            directorio["convenio"].map(_convenios_nombre_base) == convenio
        ].copy() if not directorio.empty else pd.DataFrame()
        sub_meta = metadata[
            metadata["convenio"].map(_convenios_nombre_base) == convenio
        ].copy() if not metadata.empty else pd.DataFrame()

        modelo, porcentaje = _convenios_modelo_sugerido(convenio, sub, sub_meta)
        correo_meta = ""
        notas_meta = ""
        vigencia_general = ""
        if not sub_meta.empty:
            correo_meta = _convenios_texto(sub_meta.iloc[0].get("emails", ""))
            notas_meta = _convenios_texto(sub_meta.iloc[0].get("notas", ""))
            vigencia_general = _convenios_fecha_iso(
                sub_meta.iloc[0].get("ultima_fecha_detectada", "")
            )

        filas.append({
            "registro_id": _convenios_id_registro(empresa, "REGLA", convenio),
            "empresa": empresa,
            "tipo_registro": "REGLA",
            "obra_social": convenio,
            "plan": "General",
            "codigo": "",
            "descripcion": f"Regla general de {convenio}",
            "especialidad": "",
            "categoria": "Convenio",
            "modulo": empresa,
            "modelo_valor": modelo,
            "valor_ips": "",
            "porcentaje_ips": porcentaje if porcentaje != 0 else "",
            "valor_propio": "",
            "vigencia_desde": vigencia_general,
            "vigencia_hasta": "",
            "activo": "SI",
            "requiere_autorizacion": "NO",
            "modalidad_facturacion": "",
            "fuente": fuente,
            "correo": correo_meta,
            "contacto": "",
            "observaciones": notas_meta,
            "actualizado_en": actualizado,
        })

        if not sub.empty:
            for numero, (_, fila) in enumerate(sub.iterrows(), start=1):
                valor = _convenios_numero(fila.get("valor"))
                es_ips = convenio.upper() == "IPS"
                filas.append({
                    "registro_id": _convenios_id_registro(
                        empresa, "PRACTICA", convenio, fila.get("codigo", ""),
                        fila.get("vigencia", ""), numero,
                    ),
                    "empresa": empresa,
                    "tipo_registro": "PRACTICA",
                    "obra_social": convenio,
                    "plan": "General",
                    "codigo": _convenios_texto(fila.get("codigo", "")),
                    "descripcion": _convenios_texto(fila.get("descripcion", "")),
                    "especialidad": "",
                    "categoria": _convenios_texto(fila.get("tipo", "")),
                    "modulo": empresa,
                    "modelo_valor": modelo,
                    "valor_ips": valor if es_ips and valor is not None else "",
                    "porcentaje_ips": porcentaje if porcentaje != 0 else "",
                    "valor_propio": "",
                    "vigencia_desde": _convenios_fecha_iso(fila.get("vigencia", "")),
                    "vigencia_hasta": "",
                    "activo": "SI",
                    "requiere_autorizacion": "NO",
                    "modalidad_facturacion": "",
                    "fuente": fuente,
                    "correo": correo_meta,
                    "contacto": "",
                    "observaciones": _convenios_texto(fila.get("observaciones", "")),
                    "actualizado_en": actualizado,
                })
                # El valor propio corresponde a los convenios que no son la tabla IPS.
                if not es_ips and valor is not None:
                    filas[-1]["valor_propio"] = valor

        if not sub_dir.empty:
            for numero, (_, fila) in enumerate(sub_dir.iterrows(), start=1):
                detalle_dir = " | ".join(
                    parte for parte in [
                        f"CUIT: {_convenios_texto(fila.get('cuit', ''))}" if _convenios_texto(fila.get("cuit", "")) else "",
                        f"Valores/planes: {_convenios_texto(fila.get('valores_planes', ''))}" if _convenios_texto(fila.get("valores_planes", "")) else "",
                        f"Kairos: {_convenios_texto(fila.get('kairos', ''))}" if _convenios_texto(fila.get("kairos", "")) else "",
                        f"Liquidador: {_convenios_texto(fila.get('liquidador', ''))}" if _convenios_texto(fila.get("liquidador", "")) else "",
                    ] if parte
                )
                filas.append({
                    "registro_id": _convenios_id_registro(
                        empresa, "DIRECTORIO", convenio,
                        fila.get("obra_social", ""), numero,
                    ),
                    "empresa": empresa,
                    "tipo_registro": "DIRECTORIO",
                    "obra_social": convenio,
                    "plan": _convenios_texto(fila.get("valores_planes", "")),
                    "codigo": "",
                    "descripcion": _convenios_texto(fila.get("obra_social", convenio)),
                    "especialidad": "",
                    "categoria": "Directorio",
                    "modulo": empresa,
                    "modelo_valor": modelo,
                    "valor_ips": "",
                    "porcentaje_ips": porcentaje if porcentaje != 0 else "",
                    "valor_propio": "",
                    "vigencia_desde": vigencia_general,
                    "vigencia_hasta": "",
                    "activo": "SI",
                    "requiere_autorizacion": "NO",
                    "modalidad_facturacion": _convenios_texto(fila.get("modalidad_facturacion", "")),
                    "fuente": fuente,
                    "correo": _convenios_texto(fila.get("mails", "")) or correo_meta,
                    "contacto": _convenios_texto(fila.get("liquidador", "")),
                    "observaciones": detalle_dir,
                    "actualizado_en": actualizado,
                })

        # Conserva cada fila original para auditoría, sin depender nuevamente del Excel.
        matriz_original = next(
            (m for nombre, m in matrices.items() if _convenios_nombre_base(nombre) == convenio),
            [],
        )
        for numero_fila, fila_original in enumerate(matriz_original, start=1):
            if not any(_convenios_texto(v) for v in fila_original):
                continue
            filas.append({
                "registro_id": _convenios_id_registro(
                    empresa, "ORIGINAL", convenio, numero_fila,
                ),
                "empresa": empresa,
                "tipo_registro": "ORIGINAL",
                "obra_social": convenio,
                "plan": "",
                "codigo": "",
                "descripcion": f"Fila original {numero_fila}",
                "especialidad": "",
                "categoria": "Respaldo crudo",
                "modulo": empresa,
                "modelo_valor": modelo,
                "valor_ips": "",
                "porcentaje_ips": porcentaje if porcentaje != 0 else "",
                "valor_propio": "",
                "vigencia_desde": "",
                "vigencia_hasta": "",
                "activo": "SI",
                "requiere_autorizacion": "NO",
                "modalidad_facturacion": "",
                "fuente": fuente,
                "correo": "",
                "contacto": "",
                "observaciones": json.dumps(
                    [_convenios_texto(v) for v in fila_original],
                    ensure_ascii=False,
                ),
                "actualizado_en": actualizado,
            })

    base = pd.DataFrame(filas)
    for columna in _CONVENIOS_SHEET_COLUMNS:
        if columna not in base.columns:
            base[columna] = ""
    base = base[_CONVENIOS_SHEET_COLUMNS].copy()
    base = base.astype(object).where(pd.notna(base), "")
    return base


def _convenios_guardar_excel_en_sheets(archivo, destinos: list[str]) -> dict[str, int]:
    """Guarda el Excel en convenios_vm y/o convenios_vmr usando la conexión ya existente."""
    if archivo is None:
        raise ValueError("Primero cargá el Excel de convenios.")

    archivo.seek(0)
    matrices, _ = _convenios_cargar_excel(archivo)
    if not matrices:
        raise ValueError("El Excel no contiene pestañas utilizables.")

    resultado: dict[str, int] = {}
    nombre_fuente = _convenios_texto(getattr(archivo, "name", "Excel de convenios"))
    for empresa in destinos:
        empresa = _convenios_texto(empresa).upper()
        hoja = _CONVENIOS_SHEETS[empresa]
        base = _convenios_construir_base_sheet(matrices, empresa, nombre_fuente)
        sync_df_to_sheet(hoja, base)
        resultado[hoja] = len(base)

    archivo.seek(0)
    st.cache_data.clear()
    return resultado


def _convenios_matrices_desde_base_permanente(
    df: pd.DataFrame,
    empresa: str,
) -> dict[str, list[list[Any]]]:
    """Reconstruye matrices legibles por el motor actual desde convenios_vm/vmr."""
    import re
    import pandas as pd

    if df is None or df.empty:
        return {}

    data = df.copy()
    data.columns = [_convenios_normalizar_texto(c).replace(" ", "_") for c in data.columns]
    for columna in _CONVENIOS_SHEET_COLUMNS:
        if columna not in data.columns:
            data[columna] = ""

    data["tipo_registro"] = data["tipo_registro"].astype(str).str.upper().str.strip()
    data["obra_social"] = data["obra_social"].astype(str).str.strip()
    data = data[data["obra_social"].ne("")].copy()
    if data.empty:
        return {}

    matrices: dict[str, list[list[Any]]] = {}
    for obra_social in sorted(data["obra_social"].dropna().astype(str).unique().tolist()):
        sub = data[data["obra_social"] == obra_social].copy()
        regla = sub[sub["tipo_registro"] == "REGLA"]
        practicas = sub[sub["tipo_registro"] == "PRACTICA"]
        directorio = sub[sub["tipo_registro"] == "DIRECTORIO"]

        matriz: list[list[Any]] = []
        if not regla.empty:
            fila_regla = regla.iloc[-1]
            modelo = _convenios_texto(fila_regla.get("modelo_valor", ""))
            porcentaje = _convenios_numero(fila_regla.get("porcentaje_ips"))
            correo = _convenios_texto(fila_regla.get("correo", ""))
            observaciones = _convenios_texto(fila_regla.get("observaciones", ""))
            if modelo:
                matriz.append([f"Modelo de valorización: {modelo}"])
            if porcentaje is not None and porcentaje != 0:
                matriz.append([f"Regla: IPS {porcentaje:+g}%"])
            if correo:
                matriz.append([f"Correo: {correo}"])
            if observaciones:
                matriz.append([observaciones])

        if not practicas.empty:
            matriz.append(["Código", "Descripción", "Valor", "Vigencia", "Tipo", "Observaciones"])
            for _, fila in practicas.iterrows():
                valor = fila.get("valor_propio", "")
                if _convenios_numero(valor) is None:
                    valor = fila.get("valor_ips", "")
                matriz.append([
                    _convenios_texto(fila.get("codigo", "")),
                    _convenios_texto(fila.get("descripcion", "")),
                    valor,
                    _convenios_texto(fila.get("vigencia_desde", "")),
                    _convenios_texto(fila.get("categoria", "")),
                    _convenios_texto(fila.get("observaciones", "")),
                ])

        if not directorio.empty:
            if matriz:
                matriz.append([])
            matriz.append([
                "Nombre de la obra social", "CUIT", "Modalidad facturación",
                "Valores contemplados", "Mails", "Liquidador",
            ])
            for _, fila in directorio.iterrows():
                observaciones = _convenios_texto(fila.get("observaciones", ""))
                cuit_match = re.search(r"\b\d{2}-?\d{8}-?\d\b", observaciones)
                matriz.append([
                    _convenios_texto(fila.get("descripcion", obra_social)),
                    cuit_match.group(0) if cuit_match else "",
                    _convenios_texto(fila.get("modalidad_facturacion", "")),
                    _convenios_texto(fila.get("plan", "")),
                    _convenios_texto(fila.get("correo", "")),
                    _convenios_texto(fila.get("contacto", "")),
                ])

        if matriz:
            nombre = f"{empresa} · {_convenios_nombre_canonico(obra_social)}"
            matrices[nombre] = matriz

    return matrices


def _convenios_cargar_google() -> tuple[dict[str, list[list[Any]]], pd.DataFrame]:
    """Lee exclusivamente las dos pestañas oficiales: convenios_vm y convenios_vmr."""
    import pandas as pd

    matrices: dict[str, list[list[Any]]] = {}
    estados: list[dict[str, Any]] = []

    for empresa, pestaña in _CONVENIOS_SHEETS.items():
        try:
            df = get_df(pestaña)
            matrices_empresa = _convenios_matrices_desde_base_permanente(df, empresa)
            if matrices_empresa:
                matrices.update(matrices_empresa)
                estados.extend([
                    {
                        "convenio": convenio,
                        "pestaña": pestaña,
                        "estado": "Conectada",
                        "detalle": f"{len(matriz)} filas reconstruidas · {empresa}",
                    }
                    for convenio, matriz in matrices_empresa.items()
                ])
            else:
                estados.append({
                    "convenio": empresa,
                    "pestaña": pestaña,
                    "estado": "Sin datos",
                    "detalle": "La pestaña existe pero todavía no tiene convenios guardados.",
                })
        except Exception as exc:
            estados.append({
                "convenio": empresa,
                "pestaña": pestaña,
                "estado": "No encontrada",
                "detalle": str(exc)[:180],
            })

    return matrices, pd.DataFrame(estados)

def _convenios_cargar_excel(archivo) -> tuple[dict[str, list[list[Any]]], pd.DataFrame]:
    import pandas as pd

    matrices = {}
    estados = []
    if archivo is None:
        return matrices, pd.DataFrame()

    archivo.seek(0)
    libro = pd.ExcelFile(archivo)
    for hoja in libro.sheet_names:
        df = pd.read_excel(libro, sheet_name=hoja, header=None, dtype=object)
        matriz = df.astype(object).where(pd.notna(df), "").values.tolist()
        matrices[_convenios_nombre_canonico(hoja)] = matriz
        estados.append({
            "convenio": _convenios_nombre_canonico(hoja),
            "pestaña": hoja,
            "estado": "Cargada desde Excel",
            "detalle": f"{len(df)} filas crudas",
        })
    return matrices, pd.DataFrame(estados)


def _convenios_nombre_canonico(nombre: str) -> str:
    original = _convenios_texto(nombre)
    normalizado_original = _convenios_normalizar_texto(original)

    empresa = ""
    base_original = original
    if "·" in original:
        posible_empresa, base_original = original.split("·", 1)
        posible_empresa = _convenios_normalizar_texto(posible_empresa)
        if posible_empresa == "vmr":
            empresa = "VMR"
        elif posible_empresa == "vm":
            empresa = "VM"
    elif normalizado_original.startswith("vmr "):
        empresa = "VMR"
        base_original = original[3:].lstrip(" -|·")
    elif normalizado_original.startswith("vm "):
        empresa = "VM"
        base_original = original[2:].lstrip(" -|·")

    texto = _convenios_normalizar_texto(base_original)
    if "prevencion" in texto:
        base = "PREVENCION SALUD"
    elif "circulo" in texto:
        base = "CIRCULO MEDICO"
    elif texto in {"ips", "ipss", "ipssalta", "ips salta"}:
        base = "IPS"
    else:
        base = ""
        for canonico in ["AVALIAN", "OSDE", "OSPE", "OSSEG", "BOREAL", "ACLISASA"]:
            if canonico.lower() in texto:
                base = canonico
                break
        if not base:
            base = _convenios_texto(base_original).upper() or "SIN NOMBRE"

    return f"{empresa} · {base}" if empresa else base


def _convenios_preparar_datos(matrices: dict[str, list[list[Any]]], origen: str):
    import pandas as pd

    practicas = []
    directorios = []
    metadata_filas = []

    for convenio, matriz in matrices.items():
        convenio_canonico = _convenios_nombre_canonico(convenio)
        df_practicas, metadata = _convenios_parsear_practicas(convenio_canonico, matriz, origen)
        df_directorio = _convenios_parsear_directorio(convenio_canonico, matriz, origen)

        # ACLISASA suele ser padrón/directorio, no nomenclador de prácticas.
        if convenio_canonico.endswith("ACLISASA") and not df_directorio.empty:
            df_practicas = pd.DataFrame()

        if not df_practicas.empty:
            practicas.append(df_practicas)
        if not df_directorio.empty:
            directorios.append(df_directorio)

        metadata_filas.append({
            "convenio": convenio_canonico,
            "emails": metadata.get("emails", ""),
            "reglas_porcentuales": metadata.get("reglas_porcentuales", ""),
            "ultima_fecha_detectada": metadata.get("ultima_fecha_detectada", pd.NaT),
            "notas": metadata.get("notas", ""),
            "menciona_ips": metadata.get("menciona_ips", False),
            "menciona_kairos": metadata.get("menciona_kairos", False),
            "origen": origen,
        })

    df_practicas_total = pd.concat(practicas, ignore_index=True) if practicas else pd.DataFrame(columns=[
        "convenio", "codigo", "descripcion", "valor", "vigencia", "tipo",
        "observaciones", "origen", "fila_origen", "estado_valor", "clave_practica",
    ])
    df_directorio_total = pd.concat(directorios, ignore_index=True) if directorios else pd.DataFrame(columns=[
        "convenio", "obra_social", "cuit", "modalidad_facturacion", "valores_planes",
        "kairos", "mails", "liquidador", "origen", "fila_origen",
    ])
    df_metadata = pd.DataFrame(metadata_filas)
    return df_practicas_total, df_directorio_total, df_metadata


def _convenios_estado_calidad(df: pd.DataFrame, metadata: pd.DataFrame, estados_fuente: pd.DataFrame) -> pd.DataFrame:
    import pandas as pd

    convenios = sorted(set(estados_fuente.get("convenio", pd.Series(dtype=str)).dropna().astype(str).tolist()) |
                       set(df.get("convenio", pd.Series(dtype=str)).dropna().astype(str).tolist()))
    hoy = pd.Timestamp.today().normalize()
    filas = []
    for convenio in convenios:
        sub = df[df["convenio"] == convenio].copy() if not df.empty and "convenio" in df.columns else pd.DataFrame()
        fuente = estados_fuente[estados_fuente["convenio"] == convenio] if not estados_fuente.empty else pd.DataFrame()
        meta = metadata[metadata["convenio"] == convenio] if not metadata.empty else pd.DataFrame()

        total = len(sub)
        valorizadas = int(sub["valor"].notna().sum()) if total and "valor" in sub.columns else 0
        cobertura = (valorizadas / total * 100) if total else 0
        ultima = sub["vigencia"].dropna().max() if total and "vigencia" in sub.columns else pd.NaT
        if pd.isna(ultima) and not meta.empty:
            ultima = pd.to_datetime(meta["ultima_fecha_detectada"], errors="coerce").max()
        dias = (hoy - ultima.normalize()).days if pd.notna(ultima) else None
        duplicados = int(sub.duplicated(subset=["codigo"], keep=False).sum()) if total and "codigo" in sub.columns else 0

        conectado = not fuente.empty and fuente.iloc[0].get("estado") in {"Conectada", "Cargada desde Excel"}
        if not conectado:
            semaforo = "🔴 Fuente no disponible"
        elif total == 0:
            semaforo = "🟠 Sin prácticas detectadas"
        elif cobertura < 40:
            semaforo = "🔴 Cobertura crítica"
        elif cobertura < 85:
            semaforo = "🟠 Cobertura parcial"
        elif dias is not None and dias > 90:
            semaforo = "🟠 Valores desactualizados"
        else:
            semaforo = "🟢 Operativo"

        filas.append({
            "Convenio": convenio,
            "Estado": semaforo,
            "Prácticas": total,
            "Valorizadas": valorizadas,
            "Cobertura %": round(cobertura, 1),
            "Última vigencia": ultima,
            "Días desde vigencia": dias,
            "Códigos duplicados": duplicados,
            "Fuente": fuente.iloc[0].get("pestaña", "") if not fuente.empty else "",
        })
    return pd.DataFrame(filas)


def _convenios_formatear_tabla(df: pd.DataFrame) -> pd.DataFrame:
    import pandas as pd

    salida = df.copy()
    if "valor" in salida.columns:
        salida["valor"] = pd.to_numeric(salida["valor"], errors="coerce")
    if "vigencia" in salida.columns:
        fechas = pd.to_datetime(salida["vigencia"], errors="coerce")
        salida["vigencia"] = fechas.dt.strftime("%d/%m/%Y").fillna("")
    renombres = {
        "convenio": "Convenio",
        "codigo": "Código",
        "descripcion": "Descripción de la práctica",
        "valor": "Valor",
        "vigencia": "Vigencia",
        "tipo": "Tipo",
        "observaciones": "Observaciones",
        "origen": "Origen",
        "fila_origen": "Fila fuente",
        "estado_valor": "Estado valor",
    }
    return salida.rename(columns=renombres)


def _convenios_excel_exportacion(
    practicas: pd.DataFrame,
    directorio: pd.DataFrame,
    calidad: pd.DataFrame,
    fuentes: pd.DataFrame,
    metadata: pd.DataFrame,
) -> bytes:
    from io import BytesIO
    import pandas as pd

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        _convenios_formatear_tabla(practicas).to_excel(writer, sheet_name="Practicas", index=False)
        directorio.to_excel(writer, sheet_name="Directorio", index=False)
        calidad.to_excel(writer, sheet_name="Calidad", index=False)
        fuentes.to_excel(writer, sheet_name="Fuentes", index=False)
        metadata.to_excel(writer, sheet_name="Reglas_detectadas", index=False)

        for nombre_hoja, worksheet in writer.sheets.items():
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for columna in worksheet.columns:
                letra = columna[0].column_letter
                ancho = min(max(len(str(celda.value or "")) for celda in columna) + 2, 60)
                worksheet.column_dimensions[letra].width = max(ancho, 12)
    return buffer.getvalue()


def render_convenios_pro() -> None:
    """
    Centro de inteligencia de convenios.

    Lee pestañas heterogéneas sin reescribirlas, normaliza códigos, prácticas,
    valores, vigencias, reglas de facturación y directorios. El diseño original
    de cada planilla queda intacto.
    """
    import pandas as pd
    import plotly.express as px

    st.markdown(
        """
        <style>
        .cv-hero {
            padding: 1.35rem 1.55rem;
            border: 1px solid rgba(15,118,110,.20);
            border-radius: 22px;
            background: linear-gradient(135deg, rgba(15,118,110,.14), rgba(14,165,233,.07));
            box-shadow: 0 16px 38px rgba(15,23,42,.08);
            margin-bottom: 1rem;
        }
        .cv-hero h2 { margin: 0; font-size: 1.72rem; letter-spacing: -.025em; }
        .cv-hero p { margin: .45rem 0 0; opacity: .78; }
        .cv-pill {
            display:inline-block; padding:.27rem .62rem; margin:.18rem .25rem .18rem 0;
            border-radius:999px; font-size:.78rem; font-weight:700;
            border:1px solid rgba(15,118,110,.22); background:rgba(15,118,110,.08);
        }
        .cv-note {
            padding: .85rem 1rem; border-radius: 14px;
            border-left: 4px solid #0f766e; background: rgba(15,118,110,.07);
        }
        </style>
        <div class="cv-hero">
          <span class="cv-pill">LECTURA SEGURA</span>
          <span class="cv-pill">NORMALIZACIÓN AUTOMÁTICA</span>
          <span class="cv-pill">AUDITORÍA DE ARANCELES</span>
          <h2>🏥 Centro Corporativo de Convenios Quirúrgicos</h2>
          <p>Buscador único de nomencladores, comparación de valores, vigencias, reglas de facturación y control documental.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col_fuente, col_archivo, col_destino, col_guardar, col_actualizar = st.columns(
        [1.25, 2.05, 1.25, 1.05, .85]
    )
    with col_fuente:
        modo_fuente = st.selectbox(
            "Fuente de información",
            ["Google Sheets", "Excel temporal", "Combinar ambas"],
            key="convenios_modo_fuente",
        )
    with col_archivo:
        archivo = st.file_uploader(
            "Excel de convenios (opcional)",
            type=["xlsx", "xlsm"],
            key="convenios_excel",
            help="Podés auditarlo temporalmente o guardarlo de forma permanente en Google Sheets.",
        )
    with col_destino:
        destino_guardado = st.selectbox(
            "Guardar en",
            ["VM", "VMR", "VM y VMR"],
            key="convenios_destino_guardado",
            help="VM = Vitae Medical · VMR = Vitae Medicina Reproductiva.",
        )
    with col_guardar:
        st.write("")
        st.write("")
        guardar_sheet = st.button(
            "💾 GUARDAR EN SHEET",
            type="primary",
            use_container_width=True,
            key="convenios_guardar_sheet",
            disabled=archivo is None,
        )
    with col_actualizar:
        st.write("")
        st.write("")
        if st.button("🔄 Actualizar", use_container_width=True, key="convenios_actualizar"):
            st.cache_data.clear()
            st.rerun()

    if guardar_sheet:
        destinos = ["VM", "VMR"] if destino_guardado == "VM y VMR" else [destino_guardado]
        try:
            with st.spinner("Guardando la planilla de convenios en Google Sheets..."):
                guardados = _convenios_guardar_excel_en_sheets(archivo, destinos)
            detalle = " · ".join(
                f"{hoja}: {cantidad:,} registros".replace(",", ".")
                for hoja, cantidad in guardados.items()
            )
            st.success(
                "Planilla guardada correctamente y de forma permanente. " + detalle
            )
            st.info(
                "Ahora elegí Fuente de información → Google Sheets y presioná Actualizar. "
                "El Excel ya no será necesario para consultar esos datos."
            )
        except Exception as exc:
            st.error("No se pudo guardar la planilla en Google Sheets.")
            st.exception(exc)

    matrices = {}
    estados_partes = []

    if modo_fuente in {"Google Sheets", "Combinar ambas"}:
        with st.spinner("Leyendo pestañas de convenios desde Google Sheets..."):
            matrices_google, estados_google = _convenios_cargar_google()
        matrices.update(matrices_google)
        if not estados_google.empty:
            estados_partes.append(estados_google)

    if modo_fuente in {"Excel temporal", "Combinar ambas"}:
        if archivo is None:
            st.info("Cargá el archivo Excel para utilizar este modo.")
        else:
            with st.spinner("Analizando estructura del Excel..."):
                matrices_excel, estados_excel = _convenios_cargar_excel(archivo)
            # En modo combinado, el Excel prevalece para las hojas que también estén en Google.
            matrices.update(matrices_excel)
            if not estados_excel.empty:
                estados_partes.append(estados_excel)

    estados_fuente = pd.concat(estados_partes, ignore_index=True) if estados_partes else pd.DataFrame(
        columns=["convenio", "pestaña", "estado", "detalle"]
    )
    if not estados_fuente.empty:
        # En modo combinado, conserva el diagnóstico de la fuente que prevalece.
        estados_fuente = estados_fuente.drop_duplicates(subset=["convenio"], keep="last")

    if not matrices:
        st.error(
            "No se detectaron convenios. Cargá el Excel y usá Guardar en Sheet, o verificá "
            "las pestañas convenios_vm y convenios_vmr en Google Sheets."
        )
        with st.expander("Ver diagnóstico de conexión", expanded=True):
            st.dataframe(estados_fuente, use_container_width=True, hide_index=True)
        return

    with st.spinner("Normalizando nomencladores y reglas..."):
        practicas, directorio, metadata = _convenios_preparar_datos(
            matrices,
            "Google Sheets" if modo_fuente == "Google Sheets" else modo_fuente,
        )
        calidad = _convenios_estado_calidad(practicas, metadata, estados_fuente)

    total_convenios = int(calidad["Convenio"].nunique()) if not calidad.empty else len(matrices)
    total_practicas = len(practicas)
    total_valorizadas = int(practicas["valor"].notna().sum()) if not practicas.empty else 0
    cobertura = total_valorizadas / total_practicas * 100 if total_practicas else 0
    convenios_operativos = int(calidad["Estado"].str.contains("Operativo", na=False).sum()) if not calidad.empty else 0
    alertas = int((~calidad["Estado"].str.contains("Operativo", na=False)).sum()) if not calidad.empty else 0

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Convenios detectados", f"{total_convenios}")
    k2.metric("Nomenclador total", f"{total_practicas:,}".replace(",", "."))
    k3.metric("Prácticas valorizadas", f"{total_valorizadas:,}".replace(",", "."))
    k4.metric("Cobertura de valores", f"{cobertura:,.1f}%".replace(",", "X").replace(".", ",").replace("X", "."))
    k5.metric("Convenios operativos", f"{convenios_operativos}")
    k6.metric("Alertas activas", f"{alertas}")

    tabs = st.tabs([
        "📡 Centro de control",
        "🔎 Buscador universal",
        "⚖️ Comparador",
        "🏥 Ficha por convenio",
        "📋 Reglas y directorio",
        "🧪 Auditoría de calidad",
        "🤖 Analista IA",
        "📤 Exportar",
    ])

    with tabs[0]:
        st.subheader("Estado ejecutivo del ecosistema de convenios")
        if calidad.empty:
            st.warning("No fue posible construir indicadores de calidad.")
        else:
            tabla_calidad = calidad.copy()
            tabla_calidad["Última vigencia"] = pd.to_datetime(
                tabla_calidad["Última vigencia"], errors="coerce"
            ).dt.strftime("%d/%m/%Y").fillna("Sin fecha")
            st.dataframe(
                tabla_calidad,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Cobertura %": st.column_config.ProgressColumn(
                        "Cobertura %", min_value=0, max_value=100, format="%.1f%%"
                    ),
                    "Prácticas": st.column_config.NumberColumn(format="%d"),
                    "Valorizadas": st.column_config.NumberColumn(format="%d"),
                },
            )

            grafico = calidad[calidad["Prácticas"] > 0].copy()
            if not grafico.empty:
                fig = px.bar(
                    grafico.sort_values("Cobertura %", ascending=True),
                    x="Cobertura %",
                    y="Convenio",
                    orientation="h",
                    text="Cobertura %",
                    title="Cobertura arancelaria por convenio",
                    hover_data=["Prácticas", "Valorizadas", "Última vigencia"],
                )
                fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
                fig.update_layout(height=max(380, len(grafico) * 48), xaxis_range=[0, 108])
                st.plotly_chart(fig, use_container_width=True)

            criticos = calidad[~calidad["Estado"].str.contains("Operativo", na=False)]
            if criticos.empty:
                st.success("Todos los convenios conectados se encuentran en condición operativa.")
            else:
                st.markdown("#### Prioridades de gestión")
                for _, fila in criticos.iterrows():
                    st.warning(
                        f"{fila['Convenio']}: {fila['Estado']} · "
                        f"{fila['Prácticas']} prácticas · cobertura {fila['Cobertura %']:.1f}%"
                    )

        with st.expander("📶 Diagnóstico de fuentes", expanded=False):
            st.dataframe(estados_fuente, use_container_width=True, hide_index=True)

    with tabs[1]:
        st.subheader("Buscador universal de códigos, prácticas y observaciones")
        if practicas.empty:
            st.info("No se detectaron prácticas normalizables en las hojas conectadas.")
        else:
            f1, f2, f3, f4 = st.columns([2.4, 1.3, 1.3, 1.1])
            with f1:
                consulta = st.text_input(
                    "Buscar",
                    placeholder="Ej.: histeroscopia, 110501, biopsia, laparoscopia...",
                    key="convenios_busqueda_global",
                )
            with f2:
                opciones_convenio = ["Todos"] + sorted(practicas["convenio"].dropna().unique().tolist())
                filtro_convenio = st.selectbox("Convenio", opciones_convenio, key="convenios_filtro_convenio")
            with f3:
                filtro_valor = st.selectbox(
                    "Estado del valor",
                    ["Todos", "Valorizada", "Sin valor"],
                    key="convenios_filtro_valor",
                )
            with f4:
                solo_observaciones = st.checkbox("Con observaciones", key="convenios_obs")

            filtrado = practicas.copy()
            if filtro_convenio != "Todos":
                filtrado = filtrado[filtrado["convenio"] == filtro_convenio]
            if filtro_valor != "Todos":
                filtrado = filtrado[filtrado["estado_valor"] == filtro_valor]
            if solo_observaciones:
                filtrado = filtrado[filtrado["observaciones"].fillna("").str.strip().ne("")]
            if consulta.strip():
                q = _convenios_normalizar_texto(consulta)
                bolsa = (
                    filtrado["codigo"].fillna("").astype(str) + " " +
                    filtrado["descripcion"].fillna("").astype(str) + " " +
                    filtrado["observaciones"].fillna("").astype(str) + " " +
                    filtrado["tipo"].fillna("").astype(str)
                ).apply(_convenios_normalizar_texto)
                tokens = [t for t in q.split() if t]
                mascara = pd.Series(True, index=filtrado.index)
                for token in tokens:
                    mascara &= bolsa.str.contains(token, regex=False)
                filtrado = filtrado[mascara]

            st.caption(f"{len(filtrado):,} resultados".replace(",", "."))
            vista = _convenios_formatear_tabla(
                filtrado[[
                    "convenio", "codigo", "descripcion", "valor", "vigencia",
                    "tipo", "observaciones", "estado_valor",
                ]].sort_values(["convenio", "codigo"])
            )
            st.dataframe(
                vista,
                use_container_width=True,
                hide_index=True,
                height=610,
                column_config={
                    "Valor": st.column_config.NumberColumn("Valor", format="$ %.2f"),
                    "Descripción de la práctica": st.column_config.TextColumn(width="large"),
                    "Observaciones": st.column_config.TextColumn(width="large"),
                },
            )

            csv = vista.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "📥 Descargar resultados",
                data=csv,
                file_name="convenios_busqueda.csv",
                mime="text/csv",
                key="convenios_descargar_busqueda",
            )

    with tabs[2]:
        st.subheader("Comparador transversal de una práctica")
        if practicas.empty:
            st.info("No hay prácticas disponibles para comparar.")
        else:
            c1, c2 = st.columns([2.1, 1])
            with c1:
                texto_comparar = st.text_input(
                    "Código o descripción",
                    placeholder="Ej.: 110501 o histeroscopia diagnóstica",
                    key="convenios_texto_comparar",
                )
            with c2:
                cantidad = st.number_input("Cantidad", min_value=1, max_value=100, value=1, step=1)

            candidatos = practicas.copy()
            if texto_comparar.strip():
                q = _convenios_normalizar_texto(texto_comparar)
                bolsa = (
                    candidatos["codigo"].astype(str) + " " + candidatos["descripcion"].astype(str)
                ).apply(_convenios_normalizar_texto)
                candidatos = candidatos[bolsa.str.contains(q, regex=False)]

            opciones = (
                candidatos[["codigo", "descripcion"]]
                .drop_duplicates()
                .assign(opcion=lambda x: x["codigo"].astype(str) + " — " + x["descripcion"].astype(str))
                .head(250)
            )
            if opciones.empty:
                st.info("Escribí parte del código o de la descripción para encontrar coincidencias.")
            else:
                seleccion = st.selectbox(
                    "Práctica de referencia",
                    opciones["opcion"].tolist(),
                    key="convenios_practica_referencia",
                )
                codigo_ref = seleccion.split(" — ", 1)[0].strip()
                descripcion_ref = seleccion.split(" — ", 1)[1].strip() if " — " in seleccion else ""

                comparacion = practicas[practicas["codigo"] == codigo_ref].copy()
                if comparacion.empty:
                    palabras = [p for p in _convenios_normalizar_texto(descripcion_ref).split() if len(p) >= 4][:4]
                    bolsa = practicas["descripcion"].apply(_convenios_normalizar_texto)
                    mascara = pd.Series(True, index=practicas.index)
                    for palabra in palabras:
                        mascara &= bolsa.str.contains(palabra, regex=False)
                    comparacion = practicas[mascara].copy()

                comparacion["Total"] = pd.to_numeric(comparacion["valor"], errors="coerce") * int(cantidad)
                valores = comparacion["valor"].dropna()
                if not valores.empty:
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Menor valor", f"$ {valores.min():,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
                    m2.metric("Mayor valor", f"$ {valores.max():,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
                    m3.metric("Promedio", f"$ {valores.mean():,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
                    brecha = ((valores.max() / valores.min()) - 1) * 100 if valores.min() > 0 else 0
                    m4.metric("Brecha", f"{brecha:.1f}%")

                tabla_comp = comparacion[[
                    "convenio", "codigo", "descripcion", "valor", "Total", "vigencia", "observaciones"
                ]].sort_values("valor", na_position="last")
                tabla_comp["vigencia"] = pd.to_datetime(tabla_comp["vigencia"], errors="coerce").dt.strftime("%d/%m/%Y").fillna("")
                tabla_comp = tabla_comp.rename(columns={
                    "convenio": "Convenio", "codigo": "Código", "descripcion": "Descripción",
                    "valor": "Valor unitario", "vigencia": "Vigencia", "observaciones": "Observaciones",
                })
                st.dataframe(
                    tabla_comp,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Valor unitario": st.column_config.NumberColumn(format="$ %.2f"),
                        "Total": st.column_config.NumberColumn(format="$ %.2f"),
                        "Descripción": st.column_config.TextColumn(width="large"),
                    },
                )

                chart = comparacion.dropna(subset=["valor"]).copy()
                if not chart.empty:
                    fig = px.bar(
                        chart.sort_values("valor"),
                        x="convenio",
                        y="valor",
                        text="valor",
                        title=f"Comparación arancelaria · Código {codigo_ref}",
                    )
                    fig.update_traces(texttemplate="$ %{text:,.0f}", textposition="outside")
                    fig.update_layout(yaxis_title="Valor", xaxis_title="Convenio")
                    st.plotly_chart(fig, use_container_width=True)

    with tabs[3]:
        st.subheader("Ficha operativa por convenio")
        opciones_ficha = sorted(set(matrices.keys()) | set(practicas.get("convenio", pd.Series(dtype=str)).unique()))
        seleccionado = st.selectbox("Seleccionar convenio", opciones_ficha, key="convenios_ficha")
        seleccionado = _convenios_nombre_canonico(seleccionado)
        sub = practicas[practicas["convenio"] == seleccionado].copy() if not practicas.empty else pd.DataFrame()
        meta = metadata[metadata["convenio"] == seleccionado] if not metadata.empty else pd.DataFrame()
        calidad_sub = calidad[calidad["Convenio"] == seleccionado] if not calidad.empty else pd.DataFrame()

        if not calidad_sub.empty:
            fila = calidad_sub.iloc[0]
            a1, a2, a3, a4 = st.columns(4)
            a1.metric("Estado", fila["Estado"])
            a2.metric("Prácticas", int(fila["Prácticas"]))
            a3.metric("Cobertura", f"{fila['Cobertura %']:.1f}%")
            ultima_txt = pd.to_datetime(fila["Última vigencia"], errors="coerce")
            a4.metric("Última vigencia", ultima_txt.strftime("%d/%m/%Y") if pd.notna(ultima_txt) else "Sin fecha")

        if not meta.empty:
            regla = meta.iloc[0]
            st.markdown("#### Reglas y señales detectadas")
            regla_cols = st.columns(3)
            regla_cols[0].info(f"**Reglas porcentuales**\n\n{regla.get('reglas_porcentuales') or 'No detectadas'}")
            regla_cols[1].info(f"**Actualización / contacto**\n\n{regla.get('emails') or 'Sin correo detectado'}")
            regla_cols[2].info(
                "**Referencias**\n\n" +
                ("IPS · " if regla.get("menciona_ips") else "") +
                ("Kairos" if regla.get("menciona_kairos") else "") or "Sin referencias detectadas"
            )
            if regla.get("notas"):
                with st.expander("Notas originales detectadas", expanded=False):
                    st.text(regla.get("notas"))

        if sub.empty:
            st.info("Esta hoja parece ser un directorio/padrón o no contiene una tabla de prácticas reconocible.")
        else:
            f1, f2 = st.columns([2, 1])
            with f1:
                buscar_ficha = st.text_input("Filtrar ficha", key="convenios_buscar_ficha")
            with f2:
                ordenar_por = st.selectbox("Ordenar por", ["Código", "Descripción", "Valor mayor", "Valor menor"])
            sub_f = sub.copy()
            if buscar_ficha:
                q = _convenios_normalizar_texto(buscar_ficha)
                bolsa = (sub_f["codigo"] + " " + sub_f["descripcion"]).apply(_convenios_normalizar_texto)
                sub_f = sub_f[bolsa.str.contains(q, regex=False)]
            if ordenar_por == "Código":
                sub_f = sub_f.sort_values("codigo")
            elif ordenar_por == "Descripción":
                sub_f = sub_f.sort_values("descripcion")
            elif ordenar_por == "Valor mayor":
                sub_f = sub_f.sort_values("valor", ascending=False, na_position="last")
            else:
                sub_f = sub_f.sort_values("valor", ascending=True, na_position="last")

            st.dataframe(
                _convenios_formatear_tabla(sub_f[[
                    "codigo", "descripcion", "valor", "vigencia", "tipo", "observaciones", "estado_valor"
                ]]),
                use_container_width=True,
                hide_index=True,
                height=600,
                column_config={
                    "Valor": st.column_config.NumberColumn(format="$ %.2f"),
                    "Descripción de la práctica": st.column_config.TextColumn(width="large"),
                },
            )

    with tabs[4]:
        st.subheader("Reglas de convenio, contactos y padrón de prestadoras")
        if not metadata.empty:
            meta_vista = metadata.copy()
            meta_vista["ultima_fecha_detectada"] = pd.to_datetime(
                meta_vista["ultima_fecha_detectada"], errors="coerce"
            ).dt.strftime("%d/%m/%Y").fillna("")
            meta_vista = meta_vista.rename(columns={
                "convenio": "Convenio",
                "emails": "Correos detectados",
                "reglas_porcentuales": "Reglas porcentuales",
                "ultima_fecha_detectada": "Última fecha detectada",
                "notas": "Notas de la hoja",
                "menciona_ips": "Referencia IPS",
                "menciona_kairos": "Referencia Kairos",
                "origen": "Origen",
            })
            st.dataframe(meta_vista, use_container_width=True, hide_index=True, height=330)

        st.markdown("#### Directorio / padrón")
        if directorio.empty:
            st.info("No se detectó una hoja con estructura de directorio.")
        else:
            d1, d2 = st.columns([2.2, 1])
            with d1:
                buscar_directorio = st.text_input(
                    "Buscar obra social, CUIT, plan o correo",
                    key="convenios_buscar_directorio",
                )
            with d2:
                filtro_dir = st.selectbox(
                    "Hoja",
                    ["Todas"] + sorted(directorio["convenio"].unique().tolist()),
                    key="convenios_filtro_directorio",
                )
            dir_f = directorio.copy()
            if filtro_dir != "Todas":
                dir_f = dir_f[dir_f["convenio"] == filtro_dir]
            if buscar_directorio:
                q = _convenios_normalizar_texto(buscar_directorio)
                bolsa = dir_f.fillna("").astype(str).agg(" ".join, axis=1).apply(_convenios_normalizar_texto)
                dir_f = dir_f[bolsa.str.contains(q, regex=False)]

            st.dataframe(
                dir_f.rename(columns={
                    "convenio": "Hoja",
                    "obra_social": "Obra social / Prestadora",
                    "cuit": "CUIT",
                    "modalidad_facturacion": "Modalidad de facturación",
                    "valores_planes": "Valores / Planes",
                    "kairos": "Kairos",
                    "mails": "Correos",
                    "liquidador": "Liquidador",
                    "origen": "Origen",
                    "fila_origen": "Fila fuente",
                }),
                use_container_width=True,
                hide_index=True,
                height=560,
                column_config={
                    "Obra social / Prestadora": st.column_config.TextColumn(width="large"),
                    "Valores / Planes": st.column_config.TextColumn(width="large"),
                    "Correos": st.column_config.TextColumn(width="medium"),
                },
            )

    with tabs[5]:
        st.subheader("Auditoría automática de integridad")
        if practicas.empty:
            st.info("No hay nomencladores para auditar.")
        else:
            duplicados = practicas[practicas.duplicated(subset=["convenio", "codigo"], keep=False)].copy()
            sin_valor = practicas[practicas["valor"].isna()].copy()
            sin_vigencia = practicas[practicas["vigencia"].isna()].copy()
            descripcion_corta = practicas[practicas["descripcion"].fillna("").str.len() < 10].copy()

            q1, q2, q3, q4 = st.columns(4)
            q1.metric("Códigos duplicados", len(duplicados))
            q2.metric("Sin valor", len(sin_valor))
            q3.metric("Sin vigencia", len(sin_vigencia))
            q4.metric("Descripción dudosa", len(descripcion_corta))

            problema = st.selectbox(
                "Revisar incidencia",
                ["Sin valor", "Sin vigencia", "Códigos duplicados", "Descripción dudosa"],
                key="convenios_tipo_incidencia",
            )
            mapa = {
                "Sin valor": sin_valor,
                "Sin vigencia": sin_vigencia,
                "Códigos duplicados": duplicados,
                "Descripción dudosa": descripcion_corta,
            }
            revision = mapa[problema]
            if revision.empty:
                st.success(f"No se detectaron casos de: {problema}.")
            else:
                st.dataframe(
                    _convenios_formatear_tabla(revision[[
                        "convenio", "codigo", "descripcion", "valor", "vigencia",
                        "observaciones", "origen", "fila_origen",
                    ]]),
                    use_container_width=True,
                    hide_index=True,
                    height=520,
                    column_config={"Valor": st.column_config.NumberColumn(format="$ %.2f")},
                )
                st.caption(
                    "La columna Fila fuente permite ubicar el registro exacto en la pestaña original. "
                    "Este módulo no reescribe los nomencladores y por eso preserva su diseño."
                )

    with tabs[6]:
        st.subheader("Analista IA de convenios")
        st.caption("La IA recibe únicamente la vista normalizada de convenios, sin modificar las planillas.")
        pregunta = st.text_area(
            "Pregunta",
            placeholder=(
                "Ej.: ¿Qué convenios tienen valores de histeroscopia? "
                "¿Dónde faltan aranceles? ¿Qué hoja necesita actualización primero?"
            ),
            key="convenios_pregunta_ia",
            height=110,
        )
        if st.button("Analizar convenios", type="primary", key="convenios_btn_ia"):
            if not pregunta.strip():
                st.warning("Escribí una pregunta.")
            else:
                df_ia = practicas.copy()
                if len(df_ia) > 2500:
                    df_ia = df_ia.head(2500)
                with st.spinner("Analizando nomencladores, valores y vigencias..."):
                    try:
                        respuesta = preguntar_ia(
                            modulo="Convenios quirúrgicos",
                            df=df_ia,
                            pregunta=pregunta,
                        )
                        st.success(respuesta)
                    except Exception as exc:
                        st.error(f"No se pudo consultar la IA: {exc}")

    with tabs[7]:
        st.subheader("Exportación corporativa normalizada")
        st.markdown(
            """
            <div class="cv-note">
            El archivo exportado reúne todas las pestañas en un formato uniforme, agrega control de calidad
            y conserva la referencia de la fila original. No altera el Excel ni Google Sheets.
            </div>
            """,
            unsafe_allow_html=True,
        )
        excel_bytes = _convenios_excel_exportacion(
            practicas=practicas,
            directorio=directorio,
            calidad=calidad,
            fuentes=estados_fuente,
            metadata=metadata,
        )
        e1, e2 = st.columns(2)
        with e1:
            st.download_button(
                "📗 Descargar Excel corporativo",
                data=excel_bytes,
                file_name=f"convenios_vitae_{date.today().isoformat()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="convenios_exportar_excel",
            )
        with e2:
            csv_bytes = _convenios_formatear_tabla(practicas).to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "📄 Descargar nomenclador CSV",
                data=csv_bytes,
                file_name=f"convenios_vitae_{date.today().isoformat()}.csv",
                mime="text/csv",
                use_container_width=True,
                key="convenios_exportar_csv",
            )

        st.markdown("#### Alcance de esta implementación")
        st.success(
            "Lectura multiformato, búsqueda global, comparación por código, control de vigencia, "
            "auditoría de faltantes, padrón ACLISASA, asistente IA y exportación ejecutiva."
        )
        st.info(
            "Las planillas originales quedan en modo lectura para evitar que una tabla normalizada "
            "rompa celdas combinadas, títulos, reglas y formatos propios de cada convenio."
        )


# =========================================================
# DEUDA TOTAL — CENTRO CORPORATIVO ULTRA PRO
# =========================================================
# Este módulo trabaja con una fila por obligación y por corte mensual. Puede
# leer la estructura actual del Sheet, normalizar columnas antiguas, migrar la
# planilla histórica horizontal y guardar todo nuevamente en Google Sheets.

_DEUDA_TOTAL_COLUMNS = [
    "id_registro",
    "fecha_corte",
    "periodo",
    "empresa",
    "categoria",
    "acreedor",
    "concepto",
    "importe_ars",
    "importe_usd",
    "pagado_ars",
    "pagado_usd",
    "saldo_ars",
    "saldo_usd",
    "vencimiento",
    "estado",
    "prioridad",
    "tasa_mensual",
    "cuota_actual",
    "cuotas_totales",
    "observaciones",
    "fuente",
    "created_at",
    "updated_at",
]

_DEUDA_TOTAL_CATEGORIAS = [
    "ARCA",
    "DGR",
    "Sindicatos",
    "Municipalidad",
    "Préstamos bancarios",
    "Proveedores",
    "Gastos comunes",
    "Inmuebles",
    "Honorarios",
    "Planes de pago",
    "Otros",
]

_DEUDA_TOTAL_ESTADOS = [
    "Pendiente",
    "En plan de pagos",
    "Parcial",
    "Vencido",
    "Pagado",
    "Observado",
]

_DEUDA_TOTAL_PRIORIDADES = ["Crítica", "Alta", "Media", "Baja"]


def _dt_norm(value: Any) -> str:
    import unicodedata

    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")
    return text


def _dt_text(value: Any, default: str = "") -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null", "nat"}:
        return default
    return text


def _dt_number(value: Any) -> float:
    """Convierte números argentinos, USD y porcentajes sin romper decimales."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "-", "s/d"}:
        return 0.0

    negative = text.startswith("(") and text.endswith(")")
    text = (
        text.replace("AR$", "")
        .replace("US$", "")
        .replace("USD", "")
        .replace("$", "")
        .replace("%", "")
        .replace(" ", "")
        .replace("\u00a0", "")
    )

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        right = text.split(",")[-1]
        text = text.replace(".", "")
        text = text.replace(",", "." if len(right) <= 2 else "")
    elif text.count(".") > 1:
        parts = text.split(".")
        text = "".join(parts[:-1]) + "." + parts[-1]
    elif text.count(".") == 1:
        left, right = text.split(".")
        if len(right) == 3 and len(left.replace("-", "")) >= 1:
            text = left + right

    text = re.sub(r"[^0-9.\-]", "", text)
    try:
        result = float(text)
        return -result if negative else result
    except (TypeError, ValueError):
        return 0.0


def _dt_date(value: Any) -> pd.Timestamp:
    if value is None or _dt_text(value) == "":
        return pd.NaT
    try:
        parsed = pd.to_datetime(value, errors="coerce", dayfirst=True, format="mixed")
    except (TypeError, ValueError):
        parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if isinstance(parsed, pd.DatetimeIndex):
        return parsed[0] if len(parsed) else pd.NaT
    return parsed


def _dt_period_start(value: Any) -> pd.Timestamp:
    """Reconoce 2026-03, 03/26, marzo 26, diciembre 2025 y fechas completas."""
    if not isinstance(value, str):
        parsed_value = _dt_date(value)
        if pd.notna(parsed_value):
            return pd.Timestamp(parsed_value).replace(day=1).normalize()

    text = _dt_text(value)
    if not text:
        return pd.NaT

    iso_period = re.search(r"(?<!\d)((?:19|20)\d{2})[\-/](\d{1,2})(?!\d)", text)
    if iso_period:
        year = int(iso_period.group(1))
        month = int(iso_period.group(2))
        if 1 <= month <= 12:
            return pd.Timestamp(year=year, month=month, day=1)

    normalized = _dt_norm(text)
    months = {
        "enero": 1,
        "febrero": 2,
        "marzo": 3,
        "abril": 4,
        "mayo": 5,
        "junio": 6,
        "julio": 7,
        "agosto": 8,
        "septiembre": 9,
        "setiembre": 9,
        "octubre": 10,
        "noviembre": 11,
        "diciembre": 12,
    }
    for name, month in months.items():
        if name in normalized:
            years = re.findall(r"(?:19|20)?\d{2}", normalized)
            if years:
                year = int(years[-1])
                if year < 100:
                    year += 2000
                return pd.Timestamp(year=year, month=month, day=1)

    compact = re.search(r"(?<!\d)(\d{1,2})[\-/](\d{2,4})(?!\d)", text)
    if compact:
        month = int(compact.group(1))
        year = int(compact.group(2))
        if year < 100:
            year += 2000
        if 1 <= month <= 12:
            return pd.Timestamp(year=year, month=month, day=1)

    parsed = _dt_date(value)
    if pd.notna(parsed):
        return pd.Timestamp(parsed).replace(day=1).normalize()
    return pd.NaT


def _dt_company(value: Any) -> str:
    text = _dt_norm(value)
    if not text:
        return "VITAE"
    if any(token in text for token in ["vmr", "reproductiva", "medicina_reproductiva"]):
        return "VMR"
    if text in {"vm", "vitae_medical", "medical"} or "vitae_medical" in text:
        return "VM"
    if "vitae" in text and "reproduct" not in text:
        return "VM"
    return _dt_text(value).upper()


def _dt_category(value: Any, creditor: Any = "", concept: Any = "") -> str:
    combined = _dt_norm(f"{value} {creditor} {concept}")
    if any(token in combined for token in ["arca", "afip", "f_931", "f931", "iva"]):
        return "ARCA"
    if any(token in combined for token in ["dgr", "actividades_economicas", "ingresos_brutos"]):
        return "DGR"
    if any(token in combined for token in ["sindicato", "atsa", "fatsa"]):
        return "Sindicatos"
    if any(token in combined for token in ["municipal", "tish", "comercio_municipal"]):
        return "Municipalidad"
    if any(token in combined for token in ["prestamo", "banco_macro", "banco_galicia", "credito"]):
        return "Préstamos bancarios"
    if "proveedor" in combined:
        return "Proveedores"
    if any(token in combined for token in ["gastos_comunes", "expensas", "edificio"]):
        return "Gastos comunes"
    if any(token in combined for token in ["compra_edificio", "inmueble", "luis_ortiz", "jose_del_campo"]):
        return "Inmuebles"
    if "honorario" in combined:
        return "Honorarios"
    if "plan" in combined and "pago" in combined:
        return "Planes de pago"
    cleaned = _dt_text(value)
    return cleaned if cleaned else "Otros"


def _dt_unique_columns(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result.columns = [str(column).strip() for column in result.columns]
    return result.loc[:, ~result.columns.duplicated()].copy()


def _dt_pick_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    # Coincidencia exacta normalizada. Evita confundir, por ejemplo,
    # "importe" con "importe_usd" y duplicar el mismo valor en dos monedas.
    normalized = {_dt_norm(column): column for column in df.columns}
    for candidate in candidates:
        key = _dt_norm(candidate)
        if key in normalized:
            return normalized[key]
    return None


def _dt_series(df: pd.DataFrame, candidates: list[str], default: Any = "") -> pd.Series:
    column = _dt_pick_column(df, candidates)
    if column is None:
        return pd.Series([default] * len(df), index=df.index, dtype="object")
    result = df.loc[:, column]
    if isinstance(result, pd.DataFrame):
        result = result.iloc[:, 0]
    return result


def _dt_make_id(row: pd.Series, index: int) -> str:
    import hashlib

    seed = "|".join(
        [
            _dt_text(row.get("periodo")),
            _dt_text(row.get("empresa")),
            _dt_text(row.get("categoria")),
            _dt_text(row.get("acreedor")),
            _dt_text(row.get("concepto")),
            f"{_dt_number(row.get('importe_ars')):.2f}",
            f"{_dt_number(row.get('importe_usd')):.2f}",
            str(index),
        ]
    )
    return "DT-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12].upper()


def _dt_prepare(df: pd.DataFrame | None, source: str = "Google Sheets") -> pd.DataFrame:
    """Adapta hojas antiguas o nuevas al esquema profesional sin perder datos."""
    if df is None or df.empty:
        return pd.DataFrame(columns=_DEUDA_TOTAL_COLUMNS)

    raw = _dt_unique_columns(df)
    result = pd.DataFrame(index=raw.index)

    result["id_registro"] = _dt_series(
        raw, ["id_registro", "id", "codigo", "registro_id"], ""
    ).apply(_dt_text)

    raw_cut = _dt_series(
        raw,
        ["fecha_corte", "fecha", "mes", "periodo", "fecha_origen", "actualizado_en"],
        pd.NaT,
    )
    result["fecha_corte"] = raw_cut.apply(_dt_date)

    raw_period = _dt_series(raw, ["periodo", "mes", "corte", "fecha_corte"], "")
    result["periodo"] = raw_period.apply(_dt_period_start)
    result["periodo"] = result["periodo"].fillna(
        result["fecha_corte"].apply(
            lambda value: value.replace(day=1) if pd.notna(value) else pd.NaT
        )
    )
    result["fecha_corte"] = result["fecha_corte"].fillna(
        result["periodo"].apply(
            lambda value: value + pd.offsets.MonthEnd(0) if pd.notna(value) else pd.NaT
        )
    )

    result["empresa"] = _dt_series(
        raw, ["empresa", "sociedad", "unidad", "compania"], "VITAE"
    ).apply(_dt_company)

    creditor = _dt_series(
        raw,
        ["acreedor", "entidad", "organismo", "proveedor", "persona_entidad", "detalle"],
        "",
    ).apply(_dt_text)
    concept = _dt_series(
        raw,
        ["concepto", "tributo", "descripcion", "tipo_deuda", "obligacion", "detalle"],
        "",
    ).apply(_dt_text)
    category = _dt_series(raw, ["categoria", "rubro", "grupo", "tipo"], "")

    result["acreedor"] = creditor
    result["concepto"] = concept
    result["categoria"] = [
        _dt_category(category.loc[index], creditor.loc[index], concept.loc[index])
        for index in raw.index
    ]

    generic_amount = _dt_series(
        raw,
        ["importe", "monto", "valor", "deuda_total", "saldo", "valor_pesos", "capital"],
        0,
    ).apply(_dt_number)
    currency = _dt_series(raw, ["moneda", "currency"], "ARS").apply(_dt_norm)

    result["importe_ars"] = _dt_series(
        raw,
        [
            "importe_ars", "monto_ars", "deuda_ars", "saldo_ars", "total_ars",
            "valor_pesos", "importe_pesos",
        ],
        0,
    ).apply(_dt_number)
    result["importe_usd"] = _dt_series(
        raw,
        [
            "importe_usd", "monto_usd", "deuda_usd", "saldo_usd", "total_usd",
            "valor_usd", "importe_dolares",
        ],
        0,
    ).apply(_dt_number)

    generic_usd = currency.str.contains("usd|dolar|us", regex=True, na=False)
    missing_ars = result["importe_ars"].abs().le(0.0001)
    missing_usd = result["importe_usd"].abs().le(0.0001)
    result.loc[missing_ars & ~generic_usd, "importe_ars"] = generic_amount
    result.loc[missing_usd & generic_usd, "importe_usd"] = generic_amount

    result["pagado_ars"] = _dt_series(
        raw, ["pagado_ars", "pago_ars", "pagado", "abonado_ars"], 0
    ).apply(_dt_number)
    result["pagado_usd"] = _dt_series(
        raw, ["pagado_usd", "pago_usd", "abonado_usd"], 0
    ).apply(_dt_number)

    supplied_balance_ars = _dt_series(
        raw, ["saldo_ars", "pendiente_ars", "saldo", "deuda_pendiente_ars"], 0
    ).apply(_dt_number)
    supplied_balance_usd = _dt_series(
        raw, ["saldo_usd", "pendiente_usd", "deuda_pendiente_usd"], 0
    ).apply(_dt_number)

    calculated_ars = (result["importe_ars"] - result["pagado_ars"]).clip(lower=0)
    calculated_usd = (result["importe_usd"] - result["pagado_usd"]).clip(lower=0)
    result["saldo_ars"] = supplied_balance_ars.where(
        supplied_balance_ars.abs().gt(0.0001), calculated_ars
    ).clip(lower=0)
    result["saldo_usd"] = supplied_balance_usd.where(
        supplied_balance_usd.abs().gt(0.0001), calculated_usd
    ).clip(lower=0)

    result["vencimiento"] = _dt_series(
        raw, ["vencimiento", "fecha_vencimiento", "vence", "proximo_vencimiento"], pd.NaT
    ).apply(_dt_date)
    result["estado"] = _dt_series(raw, ["estado", "situacion", "status"], "Pendiente").apply(_dt_text)
    result["prioridad"] = _dt_series(raw, ["prioridad", "criticidad", "riesgo"], "Media").apply(_dt_text)
    result["tasa_mensual"] = _dt_series(
        raw, ["tasa_mensual", "tasa", "interes_mensual", "porcentaje"], 0
    ).apply(_dt_number)
    result["cuota_actual"] = _dt_series(
        raw, ["cuota_actual", "nro_cuota", "cuota"], 0
    ).apply(_dt_number).round().astype(int)
    result["cuotas_totales"] = _dt_series(
        raw, ["cuotas_totales", "total_cuotas", "cantidad_cuotas"], 0
    ).apply(_dt_number).round().astype(int)
    result["observaciones"] = _dt_series(
        raw, ["observaciones", "notas", "comentarios", "detalle_adicional"], ""
    ).apply(_dt_text)
    result["fuente"] = _dt_series(raw, ["fuente", "origen"], source).apply(_dt_text)
    result["created_at"] = _dt_series(raw, ["created_at", "creado_en"], "").apply(_dt_text)
    result["updated_at"] = _dt_series(raw, ["updated_at", "actualizado_en"], "").apply(_dt_text)

    today = pd.Timestamp.today().normalize()
    for index in result.index:
        if not result.at[index, "id_registro"]:
            result.at[index, "id_registro"] = _dt_make_id(result.loc[index], int(index))
        if not result.at[index, "periodo"] or pd.isna(result.at[index, "periodo"]):
            result.at[index, "periodo"] = today.replace(day=1)
        if pd.isna(result.at[index, "fecha_corte"]):
            result.at[index, "fecha_corte"] = result.at[index, "periodo"] + pd.offsets.MonthEnd(0)
        if not result.at[index, "acreedor"]:
            result.at[index, "acreedor"] = result.at[index, "concepto"] or result.at[index, "categoria"]
        if not result.at[index, "concepto"]:
            result.at[index, "concepto"] = result.at[index, "acreedor"]

        balance = float(result.at[index, "saldo_ars"] + result.at[index, "saldo_usd"])
        due = result.at[index, "vencimiento"]
        state_norm = _dt_norm(result.at[index, "estado"])
        if state_norm in {"pagado", "cancelado", "cancelada", "saldado"}:
            result.at[index, "pagado_ars"] = max(
                result.at[index, "pagado_ars"], result.at[index, "importe_ars"]
            )
            result.at[index, "pagado_usd"] = max(
                result.at[index, "pagado_usd"], result.at[index, "importe_usd"]
            )
            result.at[index, "saldo_ars"] = 0.0
            result.at[index, "saldo_usd"] = 0.0
            result.at[index, "estado"] = "Pagado"
        elif balance <= 0.01 and (
            result.at[index, "importe_ars"] > 0.01 or result.at[index, "importe_usd"] > 0.01
        ):
            result.at[index, "estado"] = "Pagado"
        elif pd.notna(due) and due.normalize() < today and state_norm not in {"pagado", "cancelado"}:
            result.at[index, "estado"] = "Vencido"
        elif state_norm in {"", "pendiente", "a_pagar", "adeudado", "deuda"}:
            result.at[index, "estado"] = "Pendiente"
        elif "plan" in state_norm:
            result.at[index, "estado"] = "En plan de pagos"
        elif "parcial" in state_norm:
            result.at[index, "estado"] = "Parcial"

        priority_norm = _dt_norm(result.at[index, "prioridad"])
        if result.at[index, "estado"] == "Vencido":
            result.at[index, "prioridad"] = "Crítica"
        elif priority_norm not in {_dt_norm(value) for value in _DEUDA_TOTAL_PRIORIDADES}:
            if pd.notna(due) and 0 <= (due.normalize() - today).days <= 7:
                result.at[index, "prioridad"] = "Alta"
            else:
                result.at[index, "prioridad"] = "Media"
        else:
            result.at[index, "prioridad"] = next(
                value for value in _DEUDA_TOTAL_PRIORIDADES if _dt_norm(value) == priority_norm
            )

    meaningful = (
        result["acreedor"].astype(str).str.strip().ne("")
        | result["concepto"].astype(str).str.strip().ne("")
        | result["importe_ars"].abs().gt(0.001)
        | result["importe_usd"].abs().gt(0.001)
    )
    result = result[meaningful].copy()

    total_mask = (
        result["acreedor"].apply(_dt_norm).str.startswith("total")
        | result["concepto"].apply(_dt_norm).str.startswith("total")
    )
    result = result[~total_mask].copy()

    result["periodo"] = pd.to_datetime(result["periodo"], errors="coerce").dt.to_period("M").dt.to_timestamp()
    result["fecha_corte"] = pd.to_datetime(result["fecha_corte"], errors="coerce")
    result["vencimiento"] = pd.to_datetime(result["vencimiento"], errors="coerce")
    result = result[_DEUDA_TOTAL_COLUMNS].reset_index(drop=True)
    return result


def _dt_prepare_sheet(df: pd.DataFrame) -> pd.DataFrame:
    clean = _dt_prepare(df, source="Sistema VITAE").copy()
    now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    clean["updated_at"] = now
    clean["created_at"] = clean["created_at"].replace("", now)
    clean["fecha_corte"] = pd.to_datetime(clean["fecha_corte"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    clean["periodo"] = pd.to_datetime(clean["periodo"], errors="coerce").dt.strftime("%Y-%m").fillna("")
    clean["vencimiento"] = pd.to_datetime(clean["vencimiento"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    for column in [
        "importe_ars", "importe_usd", "pagado_ars", "pagado_usd",
        "saldo_ars", "saldo_usd", "tasa_mensual",
    ]:
        clean[column] = pd.to_numeric(clean[column], errors="coerce").fillna(0.0).round(2)
    for column in ["cuota_actual", "cuotas_totales"]:
        clean[column] = pd.to_numeric(clean[column], errors="coerce").fillna(0).round().astype(int)
    return clean[_DEUDA_TOTAL_COLUMNS]


def _dt_period_label(value: Any) -> str:
    timestamp = _dt_period_start(value)
    if pd.isna(timestamp):
        return "Sin período"
    months = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    ]
    return f"{months[timestamp.month - 1]} {timestamp.year}"


def _dt_money(value: Any, symbol: str = "$") -> str:
    number = _dt_number(value)
    text = f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{symbol} {text}"


def _dt_percentage(value: float) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value:,.1f}%".replace(",", "X").replace(".", ",").replace("X", ".")


def _dt_enrich(df: pd.DataFrame) -> pd.DataFrame:
    data = _dt_prepare(df).copy()
    if data.empty:
        return data
    today = pd.Timestamp.today().normalize()
    data["_periodo"] = pd.to_datetime(data["periodo"], errors="coerce").dt.to_period("M").dt.to_timestamp()
    data["_fecha_corte"] = pd.to_datetime(data["fecha_corte"], errors="coerce")
    data["_vencimiento"] = pd.to_datetime(data["vencimiento"], errors="coerce")
    data["_saldo_ars"] = pd.to_numeric(data["saldo_ars"], errors="coerce").fillna(0).clip(lower=0)
    data["_saldo_usd"] = pd.to_numeric(data["saldo_usd"], errors="coerce").fillna(0).clip(lower=0)
    data["_dias"] = (data["_vencimiento"].dt.normalize() - today).dt.days
    data["_situacion"] = "En término"
    data.loc[data["_vencimiento"].isna(), "_situacion"] = "Sin vencimiento"
    data.loc[data["_dias"].between(8, 30, inclusive="both"), "_situacion"] = "Vence en 30 días"
    data.loc[data["_dias"].between(0, 7, inclusive="both"), "_situacion"] = "Vence en 7 días"
    data.loc[data["_dias"].lt(0), "_situacion"] = "Vencido"
    paid = data["_saldo_ars"].le(0.01) & data["_saldo_usd"].le(0.01)
    data.loc[paid, "_situacion"] = "Pagado"
    return data


def _dt_latest_period(df: pd.DataFrame) -> pd.Timestamp:
    if df is None or df.empty:
        return pd.Timestamp.today().replace(day=1).normalize()
    periods = pd.to_datetime(df.get("_periodo", df.get("periodo")), errors="coerce").dropna()
    return periods.max() if not periods.empty else pd.Timestamp.today().replace(day=1).normalize()


def _dt_merge(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    old = _dt_prepare(existing)
    new = _dt_prepare(incoming, source="Importación histórica")
    combined = pd.concat([old, new], ignore_index=True, sort=False)
    if combined.empty:
        return combined
    key_columns = [
        "periodo", "empresa", "categoria", "acreedor", "concepto",
        "importe_ars", "importe_usd",
    ]
    combined["_period_key"] = pd.to_datetime(combined["periodo"], errors="coerce").dt.strftime("%Y-%m")
    combined["_dedupe"] = combined.apply(
        lambda row: "|".join(
            [
                _dt_text(row.get("_period_key")),
                _dt_norm(row.get("empresa")),
                _dt_norm(row.get("categoria")),
                _dt_norm(row.get("acreedor")),
                _dt_norm(row.get("concepto")),
                f"{_dt_number(row.get('importe_ars')):.2f}",
                f"{_dt_number(row.get('importe_usd')):.2f}",
            ]
        ),
        axis=1,
    )
    combined = combined.drop_duplicates("_dedupe", keep="last")
    combined = combined.drop(columns=["_period_key", "_dedupe"], errors="ignore")
    return _dt_prepare(combined).sort_values(
        ["periodo", "empresa", "categoria", "acreedor"],
        ascending=[False, True, True, True],
    ).reset_index(drop=True)


def _dt_parse_legacy_matrix(matrix: pd.DataFrame, sheet_name: str = "") -> pd.DataFrame:
    """Lee los bloques horizontales de la planilla mostrada por el usuario."""
    if matrix is None or matrix.empty:
        return pd.DataFrame(columns=_DEUDA_TOTAL_COLUMNS)

    raw = matrix.copy().fillna("")
    records: list[dict[str, Any]] = []
    title_cells: list[tuple[int, int, str]] = []

    for row_index in range(len(raw)):
        for column_index in range(len(raw.columns)):
            value = _dt_text(raw.iat[row_index, column_index])
            normalized = _dt_norm(value)
            if "deuda_impositiva_vitae_medicina_reproductiva" in normalized:
                title_cells.append((row_index, column_index, "VMR"))
            elif "deuda_impositiva_vitae_medical" in normalized:
                title_cells.append((row_index, column_index, "VM"))

    if not title_cells:
        return pd.DataFrame(columns=_DEUDA_TOTAL_COLUMNS)

    for title_row, title_col, company in title_cells:
        nearby: list[Any] = []
        # El período suele estar una o varias filas por encima del bloque y se
        # comparte entre las secciones VMR y VM. Se buscan sólo textos con forma
        # explícita de período para no interpretar F 931 o importes como fechas.
        for r in range(max(0, title_row - 15), min(len(raw), title_row + 4)):
            for c in range(max(0, title_col - 1), min(len(raw.columns), title_col + 7)):
                nearby.append(raw.iat[r, c])
        period = pd.NaT
        month_tokens = [
            "enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "setiembre", "octubre",
            "noviembre", "diciembre",
        ]
        for candidate in nearby:
            candidate_text = _dt_text(candidate)
            candidate_norm = _dt_norm(candidate_text)
            looks_like_period = (
                any(token in candidate_norm for token in month_tokens)
                or bool(re.search(r"(?<!\d)\d{1,2}[\-/]\d{2,4}(?!\d)", candidate_text))
                or ("hasta" in candidate_norm and bool(re.search(r"\d", candidate_text)))
            )
            if not looks_like_period:
                continue
            candidate_period = _dt_period_start(candidate_text)
            if pd.notna(candidate_period) and 2000 <= candidate_period.year <= 2100:
                period = candidate_period
                break
        if pd.isna(period):
            period = pd.Timestamp.today().replace(day=1).normalize()

        next_title_rows = [
            r for r, c, _ in title_cells if c == title_col and r > title_row
        ]
        section_end = min(next_title_rows) if next_title_rows else min(len(raw), title_row + 26)
        current_group = "Otros"

        for row_index in range(title_row + 1, section_end):
            row_values = [raw.iat[row_index, c] for c in range(title_col, min(len(raw.columns), title_col + 7))]
            texts = [_dt_text(value) for value in row_values]
            nonempty_texts = [text for text in texts if text]
            if not nonempty_texts:
                continue

            leading = nonempty_texts[0]
            leading_norm = _dt_norm(leading)
            if leading_norm.startswith("total") or "saldo" == leading_norm:
                continue
            if "deuda_impositiva" in leading_norm:
                continue

            numeric_candidates: list[tuple[int, float, str]] = []
            for offset, value in enumerate(row_values[1:], 1):
                number = _dt_number(value)
                text = _dt_text(value)
                if abs(number) > 0.0001:
                    numeric_candidates.append((offset, number, text))

            if not numeric_candidates:
                if len(leading) <= 70:
                    current_group = _dt_category(leading, leading, leading)
                continue

            amount_offset, amount, amount_text = numeric_candidates[0]
            usd = "usd" in _dt_norm(f"{leading} {amount_text}")
            direct_category = _dt_category("", leading, " ".join(nonempty_texts[1:]))
            category = direct_category if direct_category != "Otros" else current_group
            notes = " · ".join(
                text
                for idx, text in enumerate(texts[1:], 1)
                if text and idx != amount_offset and abs(_dt_number(text)) <= 0.0001
            )
            record = {
                "fecha_corte": period + pd.offsets.MonthEnd(0),
                "periodo": period,
                "empresa": company,
                "categoria": category,
                "acreedor": leading,
                "concepto": leading,
                "importe_ars": 0.0 if usd else amount,
                "importe_usd": amount if usd else 0.0,
                "pagado_ars": 0.0,
                "pagado_usd": 0.0,
                "saldo_ars": 0.0 if usd else amount,
                "saldo_usd": amount if usd else 0.0,
                "estado": "Pendiente",
                "prioridad": "Media",
                "observaciones": notes,
                "fuente": f"Importación histórica · {sheet_name}".strip(" ·"),
            }
            records.append(record)

    return _dt_prepare(pd.DataFrame(records), source=f"Importación histórica · {sheet_name}")


def _dt_import_file(uploaded_file: Any) -> tuple[pd.DataFrame, pd.DataFrame]:
    if uploaded_file is None:
        return pd.DataFrame(columns=_DEUDA_TOTAL_COLUMNS), pd.DataFrame()

    filename = _dt_text(getattr(uploaded_file, "name", "archivo")).lower()
    imported_frames: list[pd.DataFrame] = []
    status_rows: list[dict[str, Any]] = []

    try:
        if filename.endswith(".csv"):
            try:
                frame = pd.read_csv(uploaded_file, sep=None, engine="python")
            except UnicodeDecodeError:
                uploaded_file.seek(0)
                frame = pd.read_csv(uploaded_file, sep=None, engine="python", encoding="latin-1")
            normalized = _dt_prepare(frame, source="CSV importado")
            if normalized.empty:
                legacy = _dt_parse_legacy_matrix(pd.read_csv(uploaded_file, header=None), "CSV")
                normalized = legacy
            if not normalized.empty:
                imported_frames.append(normalized)
            status_rows.append({
                "Hoja": "CSV",
                "Registros": len(normalized),
                "Resultado": "Detectada" if not normalized.empty else "Sin filas reconocidas",
            })
        else:
            excel = pd.ExcelFile(uploaded_file)
            preferred = [name for name in excel.sheet_names if "deuda" in _dt_norm(name)]
            sheet_names = preferred + [name for name in excel.sheet_names if name not in preferred]
            for sheet_name in sheet_names:
                try:
                    tabular = pd.read_excel(excel, sheet_name=sheet_name)
                    normalized = _dt_prepare(tabular, source=f"Excel · {sheet_name}")
                    # Una hoja histórica con encabezados vacíos suele parecer tabular,
                    # pero los títulos quedan en celdas; en ese caso usa lectura cruda.
                    legacy_raw = pd.read_excel(excel, sheet_name=sheet_name, header=None)
                    legacy = _dt_parse_legacy_matrix(legacy_raw, sheet_name)
                    if not legacy.empty:
                        normalized = legacy
                    if not normalized.empty:
                        imported_frames.append(normalized)
                    status_rows.append({
                        "Hoja": sheet_name,
                        "Registros": len(normalized),
                        "Resultado": "Detectada" if not normalized.empty else "Sin estructura de deuda",
                    })
                except Exception as error:
                    status_rows.append({
                        "Hoja": sheet_name,
                        "Registros": 0,
                        "Resultado": f"Error: {str(error)[:100]}",
                    })
    except Exception as error:
        status_rows.append({"Hoja": filename, "Registros": 0, "Resultado": f"Error: {error}"})

    imported = (
        _dt_merge(pd.DataFrame(columns=_DEUDA_TOTAL_COLUMNS), pd.concat(imported_frames, ignore_index=True))
        if imported_frames else pd.DataFrame(columns=_DEUDA_TOTAL_COLUMNS)
    )
    return imported, pd.DataFrame(status_rows)


def _dt_priority_table(snapshot: pd.DataFrame, exchange_rate: float = 0.0) -> pd.DataFrame:
    if snapshot.empty:
        return pd.DataFrame()
    data = snapshot.copy()
    data["Equivalente ARS"] = data["_saldo_ars"] + data["_saldo_usd"] * max(exchange_rate, 0.0)
    priority_rank = {"Crítica": 0, "Alta": 1, "Media": 2, "Baja": 3}
    situation_rank = {"Vencido": 0, "Vence en 7 días": 1, "Vence en 30 días": 2, "Sin vencimiento": 3, "En término": 4, "Pagado": 5}
    data["_priority_rank"] = data["prioridad"].map(priority_rank).fillna(3)
    data["_situation_rank"] = data["_situacion"].map(situation_rank).fillna(4)
    data = data[(data["_saldo_ars"] > 0.01) | (data["_saldo_usd"] > 0.01)].sort_values(
        ["_priority_rank", "_situation_rank", "Equivalente ARS"],
        ascending=[True, True, False],
    )
    return data


def _dt_simulate_plan(
    total_debt: float,
    initial_cash: float,
    monthly_budget: float,
    monthly_rate: float,
    max_months: int = 120,
) -> pd.DataFrame:
    balance = max(0.0, float(total_debt) - max(0.0, float(initial_cash)))
    budget = max(0.0, float(monthly_budget))
    rate = max(0.0, float(monthly_rate)) / 100.0
    rows = []
    current = pd.Timestamp.today().replace(day=1).normalize()

    for month_number in range(1, max_months + 1):
        if balance <= 0.01:
            break
        interest = balance * rate
        opening = balance
        balance += interest
        payment = min(balance, budget)
        balance = max(0.0, balance - payment)
        rows.append({
            "Mes": current + pd.DateOffset(months=month_number - 1),
            "Saldo inicial": opening,
            "Interés estimado": interest,
            "Pago planificado": payment,
            "Saldo final": balance,
        })
        if budget <= interest + 0.01 and balance > 0:
            # El pago no cubre siquiera los intereses: evita una simulación engañosa.
            break
    return pd.DataFrame(rows)


def _dt_css() -> None:
    st.markdown(
        """
        <style>
        .dt-hero {
            background: linear-gradient(135deg, #2c1728 0%, #6f3156 55%, #b3658e 100%);
            color: white; border-radius: 20px; padding: 25px 28px; margin: 4px 0 18px 0;
            box-shadow: 0 14px 34px rgba(54, 24, 48, .18);
        }
        .dt-kicker {font-size: .78rem; letter-spacing: .13em; text-transform: uppercase; opacity: .82; font-weight: 700;}
        .dt-title {font-size: 2rem; line-height: 1.08; font-weight: 800; margin-top: 5px;}
        .dt-subtitle {font-size: .98rem; opacity: .9; max-width: 930px; margin-top: 8px;}
        .dt-card {
            background: rgba(255,255,255,.96); border: 1px solid rgba(111,49,86,.14);
            border-radius: 16px; padding: 16px 18px; min-height: 132px;
            box-shadow: 0 8px 22px rgba(57,35,50,.07); margin-bottom: 10px;
        }
        .dt-card-label {font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; color: #765c6e; font-weight: 700;}
        .dt-card-value {font-size: 1.55rem; color: #2e1c2a; font-weight: 800; margin-top: 5px;}
        .dt-card-note {font-size: .82rem; color: #786a74; margin-top: 7px;}
        .dt-alert {border-left: 5px solid #a5466c; background: #fff6fa; border-radius: 12px; padding: 13px 15px; margin: 7px 0;}
        .dt-ok {border-left-color: #2d8d66; background: #f3fbf7;}
        .dt-section-title {font-size: 1.15rem; font-weight: 800; color: #3b2535; margin: 12px 0 5px 0;}
        div[data-testid="stMetric"] {border: 1px solid rgba(111,49,86,.12); padding: 12px 14px; border-radius: 14px; background: rgba(255,255,255,.92);}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _dt_metric_card(label: str, value: str, note: str = "") -> None:
    st.markdown(
        f"""
        <div class="dt-card">
          <div class="dt-card-label">{label}</div>
          <div class="dt-card-value">{value}</div>
          <div class="dt-card-note">{note}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_deuda_total_pro(
    df_original: pd.DataFrame,
    table: str = "deuda_total",
    module_name: str = "Deuda Total",
) -> None:
    """Centro corporativo integral de deuda VMR + VM, conectado a Google Sheets."""
    _dt_css()
    data = _dt_enrich(df_original)
    today = pd.Timestamp.today().normalize()

    hero_left, hero_right = st.columns([5.5, 1.1])
    with hero_left:
        st.markdown(
            """
            <div class="dt-hero">
                <div class="dt-kicker">Dirección financiera · Control corporativo</div>
                <div class="dt-title">Deuda Total</div>
                <div class="dt-subtitle">
                    Consolidación mensual de VMR y VM: obligaciones impositivas, bancarias,
                    sindicales, proveedores, inmuebles y gastos corporativos en ARS y USD.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with hero_right:
        st.write("")
        st.write("")
        if st.button("🔄 Actualizar", key="dt_refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.caption(f"Corte del sistema: {today.strftime('%d/%m/%Y')}")

    tabs = st.tabs(
        [
            "📊 Centro ejecutivo",
            "➕ Cargar / pagos",
            "✏️ Gestionar",
            "📥 Migrar histórico",
            "🧮 Plan de cancelación",
            "🤖 Analista IA",
            "📤 Exportar",
        ]
    )

    # ------------------------------------------------------------------
    # TAB 1 · CENTRO EJECUTIVO
    # ------------------------------------------------------------------
    with tabs[0]:
        if data.empty:
            st.info(
                "La hoja de Deuda Total todavía no tiene registros. Podés cargar el primer corte "
                "manualmente o importar la planilla histórica desde la pestaña Migrar histórico."
            )
        else:
            latest_period = _dt_latest_period(data)
            available_periods = sorted(data["_periodo"].dropna().unique(), reverse=True)
            period_options = ["Último corte", "Todos los cortes"] + [
                _dt_period_label(value) for value in available_periods
            ]
            period_lookup = {
                _dt_period_label(value): pd.Timestamp(value) for value in available_periods
            }

            f1, f2, f3, f4 = st.columns([1.55, 1.35, 1.65, 1.3])
            with f1:
                period_choice = st.selectbox(
                    "Período",
                    period_options,
                    index=0,
                    key="dt_filter_period",
                )
            with f2:
                companies = sorted(data["empresa"].dropna().astype(str).unique().tolist())
                company_filter = st.multiselect(
                    "Empresa",
                    companies,
                    default=companies,
                    key="dt_filter_company",
                )
            with f3:
                categories = sorted(data["categoria"].dropna().astype(str).unique().tolist())
                category_filter = st.multiselect(
                    "Categoría",
                    categories,
                    default=categories,
                    key="dt_filter_category",
                )
            with f4:
                exchange_rate = st.number_input(
                    "USD → ARS para consolidar",
                    min_value=0.0,
                    value=0.0,
                    step=50.0,
                    key="dt_exchange_rate",
                    help="Opcional. No modifica el Sheet; sirve para calcular exposición consolidada.",
                )

            filtered_base = data.copy()
            if company_filter:
                filtered_base = filtered_base[filtered_base["empresa"].isin(company_filter)]
            if category_filter:
                filtered_base = filtered_base[filtered_base["categoria"].isin(category_filter)]

            if period_choice == "Todos los cortes":
                snapshot = filtered_base[filtered_base["_periodo"].eq(latest_period)].copy()
                detail_scope = filtered_base.copy()
                st.caption(
                    "Las métricas usan el último corte para no sumar la misma deuda varias veces. "
                    "Los gráficos muestran toda la historia."
                )
            elif period_choice == "Último corte":
                snapshot = filtered_base[filtered_base["_periodo"].eq(latest_period)].copy()
                detail_scope = snapshot.copy()
            else:
                chosen_period = period_lookup.get(period_choice, latest_period)
                snapshot = filtered_base[filtered_base["_periodo"].eq(chosen_period)].copy()
                detail_scope = snapshot.copy()

            active = snapshot[
                (snapshot["_saldo_ars"] > 0.01) | (snapshot["_saldo_usd"] > 0.01)
            ].copy()
            selected_period = _dt_latest_period(snapshot) if not snapshot.empty else latest_period
            prior_periods = sorted(
                filtered_base.loc[filtered_base["_periodo"].lt(selected_period), "_periodo"].dropna().unique(),
                reverse=True,
            )
            previous = (
                filtered_base[filtered_base["_periodo"].eq(pd.Timestamp(prior_periods[0]))].copy()
                if prior_periods else pd.DataFrame()
            )

            total_ars = active["_saldo_ars"].sum()
            total_usd = active["_saldo_usd"].sum()
            previous_ars = previous["_saldo_ars"].sum() if not previous.empty else 0.0
            variation = ((total_ars - previous_ars) / previous_ars * 100) if previous_ars > 0 else None
            equivalent = total_ars + total_usd * exchange_rate if exchange_rate > 0 else None
            overdue = active[active["_situacion"].eq("Vencido")]
            due_7 = active[active["_situacion"].eq("Vence en 7 días")]
            no_due = active[active["_situacion"].eq("Sin vencimiento")]
            vmr_ars = active.loc[active["empresa"].eq("VMR"), "_saldo_ars"].sum()
            vm_ars = active.loc[active["empresa"].eq("VM"), "_saldo_ars"].sum()

            st.markdown(
                f"<div class='dt-section-title'>Situación al {_dt_period_label(selected_period)}</div>",
                unsafe_allow_html=True,
            )
            k1, k2, k3, k4 = st.columns(4)
            with k1:
                _dt_metric_card(
                    "Deuda consolidada ARS",
                    _dt_money(total_ars),
                    f"{len(active)} obligaciones activas",
                )
            with k2:
                _dt_metric_card(
                    "Deuda consolidada USD",
                    _dt_money(total_usd, "USD"),
                    "Se mantiene separada para evitar conversiones engañosas",
                )
            with k3:
                _dt_metric_card(
                    "Variación vs. corte anterior",
                    _dt_percentage(variation) if variation is not None else "Sin comparación",
                    (_dt_money(total_ars - previous_ars) if previous_ars > 0 else "No existe un corte anterior comparable"),
                )
            with k4:
                _dt_metric_card(
                    "Exposición equivalente",
                    _dt_money(equivalent) if equivalent is not None else "Definí cotización",
                    (f"Conversión a {_dt_money(exchange_rate, '$/USD')}" if exchange_rate > 0 else "Ingresá el tipo de cambio arriba"),
                )

            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("VMR · ARS", _dt_money(vmr_ars))
            m2.metric("VM · ARS", _dt_money(vm_ars))
            m3.metric("Vencido", _dt_money(overdue["_saldo_ars"].sum()), delta=f"{len(overdue)} ítems", delta_color="inverse")
            m4.metric("Vence en 7 días", _dt_money(due_7["_saldo_ars"].sum()), delta=f"{len(due_7)} ítems", delta_color="inverse")
            m5.metric("Sin vencimiento", len(no_due), delta="Requieren completar fecha", delta_color="inverse")

            alerts = []
            if not overdue.empty:
                alerts.append(
                    f"Prioridad crítica: {len(overdue)} obligaciones vencidas por "
                    f"{_dt_money(overdue['_saldo_ars'].sum())} y {_dt_money(overdue['_saldo_usd'].sum(), 'USD')}."
                )
            if not no_due.empty:
                alerts.append(
                    f"Calidad de datos: {len(no_due)} obligaciones activas no tienen vencimiento informado."
                )
            if variation is not None and variation > 10:
                alerts.append(
                    f"La deuda ARS aumentó {_dt_percentage(variation)} respecto del corte anterior."
                )
            if alerts:
                for alert in alerts:
                    st.markdown(f"<div class='dt-alert'>⚠️ {alert}</div>", unsafe_allow_html=True)
            else:
                st.markdown(
                    "<div class='dt-alert dt-ok'>✅ No se detectan alertas críticas en el corte seleccionado.</div>",
                    unsafe_allow_html=True,
                )

            chart_left, chart_right = st.columns([1.35, 1])
            with chart_left:
                history = filtered_base.copy()
                history_active = history[
                    (history["_saldo_ars"] > 0.01) | (history["_saldo_usd"] > 0.01)
                ]
                history_summary = history_active.groupby(
                    ["_periodo", "empresa"], as_index=False
                ).agg(Deuda_ARS=("_saldo_ars", "sum"), Deuda_USD=("_saldo_usd", "sum"))
                if not history_summary.empty:
                    fig_history = px.line(
                        history_summary,
                        x="_periodo",
                        y="Deuda_ARS",
                        color="empresa",
                        markers=True,
                        title="Evolución mensual de deuda ARS",
                        labels={"_periodo": "Período", "Deuda_ARS": "Deuda ARS", "empresa": "Empresa"},
                    )
                    fig_history.update_layout(height=390, margin=dict(l=10, r=10, t=55, b=10), legend_title_text="")
                    fig_history.update_yaxes(tickprefix="$ ", separatethousands=True)
                    st.plotly_chart(fig_history, use_container_width=True)
                else:
                    st.info("No hay historia suficiente para graficar.")
            with chart_right:
                composition = active.groupby("categoria", as_index=False)["_saldo_ars"].sum()
                composition = composition[composition["_saldo_ars"] > 0].sort_values("_saldo_ars", ascending=False)
                if not composition.empty:
                    fig_composition = px.pie(
                        composition,
                        names="categoria",
                        values="_saldo_ars",
                        hole=0.58,
                        title="Composición por categoría",
                    )
                    fig_composition.update_layout(height=390, margin=dict(l=10, r=10, t=55, b=10), legend_title_text="")
                    st.plotly_chart(fig_composition, use_container_width=True)
                else:
                    st.info("No hay saldos ARS para componer.")

            rank_left, rank_right = st.columns([1.25, 1])
            with rank_left:
                creditors = active.groupby(["acreedor", "empresa"], as_index=False).agg(
                    Saldo_ARS=("_saldo_ars", "sum"), Saldo_USD=("_saldo_usd", "sum")
                )
                creditors = creditors.sort_values("Saldo_ARS", ascending=False).head(12)
                if not creditors.empty and creditors["Saldo_ARS"].sum() > 0:
                    fig_creditors = px.bar(
                        creditors.sort_values("Saldo_ARS"),
                        x="Saldo_ARS",
                        y="acreedor",
                        color="empresa",
                        orientation="h",
                        title="Principales acreedores en ARS",
                        labels={"Saldo_ARS": "Saldo ARS", "acreedor": "Acreedor", "empresa": "Empresa"},
                    )
                    fig_creditors.update_layout(height=440, margin=dict(l=10, r=10, t=55, b=10), legend_title_text="")
                    fig_creditors.update_xaxes(tickprefix="$ ", separatethousands=True)
                    st.plotly_chart(fig_creditors, use_container_width=True)
                else:
                    st.info("No hay acreedores con saldo ARS.")
            with rank_right:
                risk = active.groupby("_situacion", as_index=False).agg(
                    Obligaciones=("id_registro", "count"), Saldo_ARS=("_saldo_ars", "sum")
                ).sort_values("Saldo_ARS", ascending=False)
                st.markdown("#### Matriz de riesgo")
                if risk.empty:
                    st.info("No existen obligaciones activas.")
                else:
                    risk_view = risk.rename(columns={"_situacion": "Situación", "Saldo_ARS": "Saldo ARS"})
                    st.dataframe(
                        risk_view,
                        use_container_width=True,
                        hide_index=True,
                        column_config={"Saldo ARS": st.column_config.NumberColumn(format="$ %.2f")},
                    )

                    top_three = creditors.nlargest(3, "Saldo_ARS")["Saldo_ARS"].sum() if not creditors.empty else 0
                    concentration = top_three / total_ars * 100 if total_ars > 0 else 0
                    st.metric("Concentración top 3 acreedores", _dt_percentage(concentration))
                    if concentration >= 60:
                        st.warning("La deuda está altamente concentrada: una negociación puntual puede cambiar mucho la posición total.")
                    else:
                        st.caption("La exposición se encuentra relativamente diversificada entre acreedores.")

            st.markdown("#### Prioridades inmediatas")
            priorities = _dt_priority_table(active, exchange_rate).head(15)
            if priorities.empty:
                st.success("No hay obligaciones pendientes en el alcance seleccionado.")
            else:
                priority_view = pd.DataFrame({
                    "Prioridad": priorities["prioridad"],
                    "Situación": priorities["_situacion"],
                    "Empresa": priorities["empresa"],
                    "Categoría": priorities["categoria"],
                    "Acreedor": priorities["acreedor"],
                    "Vencimiento": priorities["_vencimiento"],
                    "Saldo ARS": priorities["_saldo_ars"],
                    "Saldo USD": priorities["_saldo_usd"],
                    "Observaciones": priorities["observaciones"],
                })
                st.dataframe(
                    priority_view,
                    use_container_width=True,
                    hide_index=True,
                    height=460,
                    column_config={
                        "Vencimiento": st.column_config.DateColumn(format="DD/MM/YYYY"),
                        "Saldo ARS": st.column_config.NumberColumn(format="$ %.2f"),
                        "Saldo USD": st.column_config.NumberColumn(format="USD %.2f"),
                    },
                )

    # ------------------------------------------------------------------
    # TAB 2 · CARGAR Y REGISTRAR PAGOS
    # ------------------------------------------------------------------
    with tabs[1]:
        st.subheader("Nueva obligación")
        with st.form("dt_new_debt_form", clear_on_submit=True):
            n1, n2, n3 = st.columns(3)
            with n1:
                cut_date = st.date_input("Fecha de corte", value=today.date(), key="dt_new_cut")
                company = st.selectbox("Empresa", ["VMR", "VM"], key="dt_new_company")
                category = st.selectbox("Categoría", _DEUDA_TOTAL_CATEGORIAS, key="dt_new_category")
                creditor = st.text_input("Acreedor / organismo", key="dt_new_creditor")
            with n2:
                concept = st.text_input("Concepto / tributo", key="dt_new_concept")
                amount_ars = st.number_input("Importe ARS", min_value=0.0, step=1000.0, key="dt_new_ars")
                amount_usd = st.number_input("Importe USD", min_value=0.0, step=100.0, key="dt_new_usd")
                has_due = st.checkbox("Tiene vencimiento", value=True, key="dt_new_has_due")
                due_date = st.date_input("Vencimiento", value=today.date(), disabled=not has_due, key="dt_new_due")
            with n3:
                state = st.selectbox("Estado", _DEUDA_TOTAL_ESTADOS, key="dt_new_state")
                priority = st.selectbox("Prioridad", _DEUDA_TOTAL_PRIORIDADES, index=2, key="dt_new_priority")
                monthly_rate = st.number_input("Tasa mensual %", min_value=0.0, step=0.1, key="dt_new_rate")
                installment_now = st.number_input("Cuota actual", min_value=0, step=1, key="dt_new_installment")
                installments = st.number_input("Cuotas totales", min_value=0, step=1, key="dt_new_installments")
            notes = st.text_area("Observaciones", key="dt_new_notes")
            submit_new = st.form_submit_button("💾 Guardar obligación", type="primary", use_container_width=True)

        if submit_new:
            if not creditor.strip() and not concept.strip():
                st.error("Completá al menos el acreedor o el concepto.")
            elif amount_ars <= 0 and amount_usd <= 0:
                st.error("Ingresá un importe en ARS o USD.")
            else:
                timestamp = pd.Timestamp.now()
                new_row = pd.DataFrame([{
                    "id_registro": f"DT-{timestamp.strftime('%Y%m%d%H%M%S%f')}",
                    "fecha_corte": pd.Timestamp(cut_date),
                    "periodo": pd.Timestamp(cut_date).replace(day=1),
                    "empresa": company,
                    "categoria": category,
                    "acreedor": creditor.strip() or concept.strip(),
                    "concepto": concept.strip() or creditor.strip(),
                    "importe_ars": amount_ars,
                    "importe_usd": amount_usd,
                    "pagado_ars": 0.0,
                    "pagado_usd": 0.0,
                    "saldo_ars": amount_ars,
                    "saldo_usd": amount_usd,
                    "vencimiento": pd.Timestamp(due_date) if has_due else pd.NaT,
                    "estado": state,
                    "prioridad": priority,
                    "tasa_mensual": monthly_rate,
                    "cuota_actual": installment_now,
                    "cuotas_totales": installments,
                    "observaciones": notes,
                    "fuente": "Carga manual",
                    "created_at": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "updated_at": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                }])
                try:
                    destination = _dt_merge(data[_DEUDA_TOTAL_COLUMNS] if not data.empty else data, new_row)
                    sync_df_to_sheet(table, _dt_prepare_sheet(destination))
                    st.cache_data.clear()
                    st.success("Obligación guardada en Google Sheets.")
                    st.rerun()
                except Exception as error:
                    st.error("No se pudo guardar la obligación.")
                    st.exception(error)

        st.divider()
        st.subheader("Registrar pago rápido")
        if data.empty:
            st.info("Primero cargá una obligación.")
        else:
            latest = _dt_latest_period(data)
            open_debts = data[
                data["_periodo"].eq(latest)
                & ((data["_saldo_ars"] > 0.01) | (data["_saldo_usd"] > 0.01))
            ].copy()
            if open_debts.empty:
                st.success("El último corte no tiene deudas abiertas.")
            else:
                open_debts["_label"] = open_debts.apply(
                    lambda row: (
                        f"{row['empresa']} · {row['acreedor']} · "
                        f"{_dt_money(row['_saldo_ars'])} · {_dt_money(row['_saldo_usd'], 'USD')}"
                    ),
                    axis=1,
                )
                selected_id = st.selectbox(
                    "Obligación",
                    open_debts["id_registro"].tolist(),
                    format_func=lambda value: open_debts.set_index("id_registro").loc[value, "_label"],
                    key="dt_quick_payment_debt",
                )
                selected_row = open_debts[open_debts["id_registro"].eq(selected_id)].iloc[0]
                p1, p2, p3 = st.columns(3)
                with p1:
                    pay_ars = st.number_input(
                        "Pago ARS",
                        min_value=0.0,
                        max_value=float(selected_row["_saldo_ars"]),
                        step=1000.0,
                        key="dt_quick_pay_ars",
                    )
                with p2:
                    pay_usd = st.number_input(
                        "Pago USD",
                        min_value=0.0,
                        max_value=float(selected_row["_saldo_usd"]),
                        step=100.0,
                        key="dt_quick_pay_usd",
                    )
                with p3:
                    payment_date = st.date_input("Fecha del pago", value=today.date(), key="dt_quick_pay_date")
                payment_notes = st.text_input("Referencia / comprobante", key="dt_quick_pay_notes")
                confirm_payment = st.checkbox("Confirmo que el pago fue realizado", key="dt_quick_payment_confirm")
                if st.button(
                    "✅ Aplicar pago",
                    type="primary",
                    disabled=not confirm_payment,
                    key="dt_quick_payment_button",
                    use_container_width=True,
                ):
                    if pay_ars <= 0 and pay_usd <= 0:
                        st.warning("Ingresá un importe pagado.")
                    else:
                        updated = _dt_prepare(data[_DEUDA_TOTAL_COLUMNS]).copy()
                        mask = updated["id_registro"].eq(selected_id)
                        updated.loc[mask, "pagado_ars"] = (
                            pd.to_numeric(updated.loc[mask, "pagado_ars"], errors="coerce").fillna(0) + pay_ars
                        )
                        updated.loc[mask, "pagado_usd"] = (
                            pd.to_numeric(updated.loc[mask, "pagado_usd"], errors="coerce").fillna(0) + pay_usd
                        )
                        updated.loc[mask, "saldo_ars"] = (
                            pd.to_numeric(updated.loc[mask, "importe_ars"], errors="coerce").fillna(0)
                            - pd.to_numeric(updated.loc[mask, "pagado_ars"], errors="coerce").fillna(0)
                        ).clip(lower=0)
                        updated.loc[mask, "saldo_usd"] = (
                            pd.to_numeric(updated.loc[mask, "importe_usd"], errors="coerce").fillna(0)
                            - pd.to_numeric(updated.loc[mask, "pagado_usd"], errors="coerce").fillna(0)
                        ).clip(lower=0)
                        fully_paid = (
                            float(updated.loc[mask, "saldo_ars"].iloc[0]) <= 0.01
                            and float(updated.loc[mask, "saldo_usd"].iloc[0]) <= 0.01
                        )
                        updated.loc[mask, "estado"] = "Pagado" if fully_paid else "Parcial"
                        previous_notes = _dt_text(updated.loc[mask, "observaciones"].iloc[0])
                        log = f"Pago {pd.Timestamp(payment_date).strftime('%d/%m/%Y')}: {_dt_money(pay_ars)} / {_dt_money(pay_usd, 'USD')}"
                        if payment_notes.strip():
                            log += f" · {payment_notes.strip()}"
                        updated.loc[mask, "observaciones"] = (previous_notes + " | " + log).strip(" |")
                        try:
                            sync_df_to_sheet(table, _dt_prepare_sheet(updated))
                            st.cache_data.clear()
                            st.success("Pago aplicado y saldo actualizado.")
                            st.rerun()
                        except Exception as error:
                            st.error("No se pudo registrar el pago.")
                            st.exception(error)

        st.divider()
        st.subheader("Crear un nuevo corte mensual")
        if data.empty:
            st.info("No hay un corte anterior para copiar.")
        else:
            source_period = _dt_latest_period(data)
            source_snapshot = data[data["_periodo"].eq(source_period)].copy()
            c1, c2 = st.columns(2)
            with c1:
                new_cut_date = st.date_input(
                    "Fecha del nuevo corte",
                    value=(source_period + pd.offsets.MonthBegin(1) + pd.offsets.MonthEnd(0)).date(),
                    key="dt_new_snapshot_date",
                )
            with c2:
                carry_only_open = st.checkbox(
                    "Copiar solamente saldos pendientes",
                    value=True,
                    key="dt_carry_open",
                )
            st.caption(
                f"Origen: {_dt_period_label(source_period)} · {len(source_snapshot)} obligaciones. "
                "El nuevo corte conserva el historial y crea filas nuevas."
            )
            confirm_cut = st.checkbox("Confirmo la creación del nuevo corte", key="dt_confirm_cut")
            if st.button(
                "📆 Generar corte mensual",
                type="primary",
                disabled=not confirm_cut,
                key="dt_generate_cut",
                use_container_width=True,
            ):
                target_period = pd.Timestamp(new_cut_date).replace(day=1)
                if data["_periodo"].eq(target_period).any():
                    st.error("Ese período ya existe. Editalo desde Gestionar para evitar duplicados.")
                else:
                    carried = source_snapshot.copy()
                    if carry_only_open:
                        carried = carried[(carried["_saldo_ars"] > 0.01) | (carried["_saldo_usd"] > 0.01)].copy()
                    carried["fecha_corte"] = pd.Timestamp(new_cut_date)
                    carried["periodo"] = target_period
                    carried["importe_ars"] = carried["_saldo_ars"]
                    carried["importe_usd"] = carried["_saldo_usd"]
                    carried["pagado_ars"] = 0.0
                    carried["pagado_usd"] = 0.0
                    carried["saldo_ars"] = carried["_saldo_ars"]
                    carried["saldo_usd"] = carried["_saldo_usd"]
                    carried["estado"] = carried.apply(
                        lambda row: "Vencido" if pd.notna(row["_vencimiento"]) and row["_vencimiento"].normalize() < pd.Timestamp(new_cut_date) else "Pendiente",
                        axis=1,
                    )
                    carried["id_registro"] = [
                        f"DT-{pd.Timestamp.now().strftime('%Y%m%d%H%M%S')}-{index:04d}"
                        for index in range(len(carried))
                    ]
                    carried["fuente"] = f"Arrastre de {_dt_period_label(source_period)}"
                    carried["created_at"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
                    carried["updated_at"] = carried["created_at"]
                    try:
                        destination = pd.concat(
                            [data[_DEUDA_TOTAL_COLUMNS], carried[_DEUDA_TOTAL_COLUMNS]],
                            ignore_index=True,
                            sort=False,
                        )
                        sync_df_to_sheet(table, _dt_prepare_sheet(destination))
                        st.cache_data.clear()
                        st.success(f"Nuevo corte creado con {len(carried)} obligaciones.")
                        st.rerun()
                    except Exception as error:
                        st.error("No se pudo crear el nuevo corte.")
                        st.exception(error)

    # ------------------------------------------------------------------
    # TAB 3 · GESTIÓN / EDICIÓN
    # ------------------------------------------------------------------
    with tabs[2]:
        st.subheader("Gestión integral de registros")
        if data.empty:
            st.info("No hay registros para editar.")
        else:
            g1, g2, g3 = st.columns(3)
            periods = sorted(data["_periodo"].dropna().unique(), reverse=True)
            with g1:
                manage_period_label = st.selectbox(
                    "Corte a editar",
                    [_dt_period_label(value) for value in periods],
                    key="dt_manage_period",
                )
                manage_period = next(
                    pd.Timestamp(value) for value in periods if _dt_period_label(value) == manage_period_label
                )
            with g2:
                manage_company = st.multiselect(
                    "Empresa",
                    sorted(data["empresa"].unique().tolist()),
                    default=sorted(data["empresa"].unique().tolist()),
                    key="dt_manage_company",
                )
            with g3:
                search = st.text_input(
                    "Buscar",
                    placeholder="Acreedor, concepto, categoría...",
                    key="dt_manage_search",
                )

            edit_mask = data["_periodo"].eq(manage_period)
            if manage_company:
                edit_mask &= data["empresa"].isin(manage_company)
            if search.strip():
                query = _dt_norm(search)
                haystack = data[["categoria", "acreedor", "concepto", "observaciones"]].fillna("").astype(str).agg(" ".join, axis=1).apply(_dt_norm)
                edit_mask &= haystack.str.contains(query, regex=False)

            subset = data.loc[edit_mask, _DEUDA_TOTAL_COLUMNS].copy()
            display_columns = [
                "id_registro", "fecha_corte", "periodo", "empresa", "categoria",
                "acreedor", "concepto", "importe_ars", "importe_usd", "pagado_ars",
                "pagado_usd", "saldo_ars", "saldo_usd", "vencimiento", "estado",
                "prioridad", "tasa_mensual", "cuota_actual", "cuotas_totales",
                "observaciones", "fuente",
            ]
            subset["fecha_corte"] = pd.to_datetime(subset["fecha_corte"], errors="coerce")
            subset["periodo"] = pd.to_datetime(subset["periodo"], errors="coerce")
            subset["vencimiento"] = pd.to_datetime(subset["vencimiento"], errors="coerce")

            edited = st.data_editor(
                subset[display_columns],
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                height=600,
                key="dt_professional_editor",
                disabled=["id_registro", "saldo_ars", "saldo_usd", "fuente"],
                column_config={
                    "id_registro": st.column_config.TextColumn("ID", width="small"),
                    "fecha_corte": st.column_config.DateColumn("Fecha corte", format="DD/MM/YYYY"),
                    "periodo": st.column_config.DateColumn("Período", format="MM/YYYY"),
                    "empresa": st.column_config.SelectboxColumn("Empresa", options=["VMR", "VM"], required=True),
                    "categoria": st.column_config.SelectboxColumn("Categoría", options=_DEUDA_TOTAL_CATEGORIAS, required=True),
                    "importe_ars": st.column_config.NumberColumn("Importe ARS", min_value=0.0, format="$ %.2f"),
                    "importe_usd": st.column_config.NumberColumn("Importe USD", min_value=0.0, format="USD %.2f"),
                    "pagado_ars": st.column_config.NumberColumn("Pagado ARS", min_value=0.0, format="$ %.2f"),
                    "pagado_usd": st.column_config.NumberColumn("Pagado USD", min_value=0.0, format="USD %.2f"),
                    "saldo_ars": st.column_config.NumberColumn("Saldo ARS", format="$ %.2f"),
                    "saldo_usd": st.column_config.NumberColumn("Saldo USD", format="USD %.2f"),
                    "vencimiento": st.column_config.DateColumn("Vencimiento", format="DD/MM/YYYY"),
                    "estado": st.column_config.SelectboxColumn("Estado", options=_DEUDA_TOTAL_ESTADOS, required=True),
                    "prioridad": st.column_config.SelectboxColumn("Prioridad", options=_DEUDA_TOTAL_PRIORIDADES, required=True),
                    "tasa_mensual": st.column_config.NumberColumn("Tasa mensual %", min_value=0.0, format="%.2f %%"),
                },
            )
            st.caption(
                "El saldo se recalcula automáticamente como importe menos pagado. "
                "Las filas eliminadas del editor se eliminarán solamente dentro del filtro actual."
            )
            confirm_save = st.checkbox("Confirmo guardar estos cambios", key="dt_confirm_editor_save")
            if st.button(
                "💾 Guardar cambios en Google Sheets",
                type="primary",
                disabled=not confirm_save,
                key="dt_save_editor",
                use_container_width=True,
            ):
                try:
                    edited_clean = edited.copy()
                    now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
                    for index in edited_clean.index:
                        if not _dt_text(edited_clean.at[index, "id_registro"]):
                            edited_clean.at[index, "id_registro"] = f"DT-{pd.Timestamp.now().strftime('%Y%m%d%H%M%S%f')}-{index}"
                    edited_clean["saldo_ars"] = (
                        pd.to_numeric(edited_clean["importe_ars"], errors="coerce").fillna(0)
                        - pd.to_numeric(edited_clean["pagado_ars"], errors="coerce").fillna(0)
                    ).clip(lower=0)
                    edited_clean["saldo_usd"] = (
                        pd.to_numeric(edited_clean["importe_usd"], errors="coerce").fillna(0)
                        - pd.to_numeric(edited_clean["pagado_usd"], errors="coerce").fillna(0)
                    ).clip(lower=0)
                    edited_clean["created_at"] = now
                    edited_clean["updated_at"] = now
                    edited_clean["fuente"] = edited_clean["fuente"].replace("", "Editor profesional")

                    untouched = data.loc[~edit_mask, _DEUDA_TOTAL_COLUMNS].copy()
                    destination = pd.concat([untouched, edited_clean], ignore_index=True, sort=False)
                    sync_df_to_sheet(table, _dt_prepare_sheet(destination))
                    st.cache_data.clear()
                    st.success(f"Cambios guardados. La hoja contiene {len(destination)} registros.")
                    st.rerun()
                except Exception as error:
                    st.error("No se pudieron guardar los cambios.")
                    st.exception(error)

    # ------------------------------------------------------------------
    # TAB 4 · MIGRACIÓN DE HISTÓRICO
    # ------------------------------------------------------------------
    with tabs[3]:
        st.subheader("Migración automática de la planilla histórica")
        st.info(
            "Subí el Excel que contiene la pestaña DEUDA TOTAL. El importador reconoce tanto "
            "tablas verticales como los bloques horizontales de VMR y VM mostrados en la planilla original."
        )
        uploaded = st.file_uploader(
            "Archivo histórico",
            type=["xlsx", "xls", "csv"],
            key="dt_historical_upload",
        )
        imported, import_status = _dt_import_file(uploaded)
        if uploaded is not None:
            if not import_status.empty:
                st.dataframe(import_status, use_container_width=True, hide_index=True)
            if imported.empty:
                st.error("No se detectaron obligaciones. Revisá que el archivo tenga títulos o columnas de deuda reconocibles.")
            else:
                i1, i2, i3, i4 = st.columns(4)
                i1.metric("Registros detectados", len(imported))
                i2.metric("Períodos", imported["periodo"].nunique())
                i3.metric("Total ARS histórico", _dt_money(imported["saldo_ars"].sum()))
                i4.metric("Total USD histórico", _dt_money(imported["saldo_usd"].sum(), "USD"))
                preview = imported[
                    [
                        "periodo", "empresa", "categoria", "acreedor", "concepto",
                        "saldo_ars", "saldo_usd", "observaciones", "fuente",
                    ]
                ].copy()
                st.dataframe(
                    preview,
                    use_container_width=True,
                    hide_index=True,
                    height=500,
                    column_config={
                        "periodo": st.column_config.DateColumn("Período", format="MM/YYYY"),
                        "saldo_ars": st.column_config.NumberColumn("Saldo ARS", format="$ %.2f"),
                        "saldo_usd": st.column_config.NumberColumn("Saldo USD", format="USD %.2f"),
                    },
                )
                mode = st.radio(
                    "Cómo guardar",
                    [
                        "Fusionar con la hoja actual sin duplicar",
                        "Reemplazar la hoja actual con esta migración",
                    ],
                    key="dt_import_mode",
                )
                confirm_import = st.checkbox("Confirmo que revisé la vista previa", key="dt_confirm_import")
                if st.button(
                    "💾 Guardar migración en Google Sheets",
                    type="primary",
                    disabled=not confirm_import,
                    key="dt_save_import",
                    use_container_width=True,
                ):
                    try:
                        destination = (
                            _dt_merge(data[_DEUDA_TOTAL_COLUMNS] if not data.empty else data, imported)
                            if mode.startswith("Fusionar")
                            else _dt_prepare(imported)
                        )
                        sync_df_to_sheet(table, _dt_prepare_sheet(destination))
                        st.cache_data.clear()
                        st.success(f"Migración terminada: {len(destination)} registros guardados.")
                        st.rerun()
                    except Exception as error:
                        st.error("No se pudo guardar la migración.")
                        st.exception(error)

    # ------------------------------------------------------------------
    # TAB 5 · PLAN DE CANCELACIÓN
    # ------------------------------------------------------------------
    with tabs[4]:
        st.subheader("Simulador de cancelación y cobertura")
        if data.empty:
            st.info("Cargá deudas para utilizar el simulador.")
        else:
            latest_period = _dt_latest_period(data)
            plan_scope = data[
                data["_periodo"].eq(latest_period)
                & ((data["_saldo_ars"] > 0.01) | (data["_saldo_usd"] > 0.01))
            ].copy()
            s1, s2, s3, s4 = st.columns(4)
            with s1:
                plan_exchange = st.number_input("Tipo de cambio USD", min_value=0.0, value=0.0, step=50.0, key="dt_plan_fx")
            with s2:
                initial_cash = st.number_input("Caja inicial disponible", min_value=0.0, step=100000.0, key="dt_plan_cash")
            with s3:
                monthly_budget = st.number_input("Pago mensual posible", min_value=0.0, step=100000.0, key="dt_plan_budget")
            with s4:
                monthly_interest = st.number_input("Interés mensual promedio %", min_value=0.0, step=0.1, key="dt_plan_interest")

            debt_ars = plan_scope["_saldo_ars"].sum()
            debt_usd = plan_scope["_saldo_usd"].sum()
            debt_equivalent = debt_ars + debt_usd * plan_exchange
            coverage = initial_cash / debt_equivalent * 100 if debt_equivalent > 0 else 100
            remaining = max(0.0, debt_equivalent - initial_cash)

            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Deuda equivalente", _dt_money(debt_equivalent) if plan_exchange > 0 or debt_usd <= 0 else "Falta tipo de cambio")
            p2.metric("Cobertura inmediata", _dt_percentage(coverage))
            p3.metric("Saldo luego de caja inicial", _dt_money(remaining))
            p4.metric("USD pendiente", _dt_money(debt_usd, "USD"))

            if debt_usd > 0 and plan_exchange <= 0:
                st.warning("Ingresá un tipo de cambio para incluir la deuda USD en la simulación.")
            elif monthly_budget <= 0 and remaining > 0:
                st.info("Ingresá un pago mensual posible para generar el cronograma.")
            else:
                schedule = _dt_simulate_plan(
                    total_debt=debt_equivalent,
                    initial_cash=initial_cash,
                    monthly_budget=monthly_budget,
                    monthly_rate=monthly_interest,
                )
                if schedule.empty:
                    st.success("La caja inicial alcanza para cubrir la deuda seleccionada.")
                else:
                    cannot_amortize = (
                        monthly_budget <= schedule.iloc[0]["Interés estimado"] + 0.01
                        and schedule.iloc[-1]["Saldo final"] > 0.01
                    )
                    if cannot_amortize:
                        st.error("El pago mensual no cubre el interés estimado. El saldo no se amortiza.")
                    else:
                        months_needed = len(schedule)
                        final_date = pd.Timestamp(schedule.iloc[-1]["Mes"])
                        interest_total = schedule["Interés estimado"].sum()
                        q1, q2, q3 = st.columns(3)
                        q1.metric("Meses estimados", months_needed)
                        q2.metric("Fecha estimada de cancelación", final_date.strftime("%m/%Y"))
                        q3.metric("Interés acumulado estimado", _dt_money(interest_total))
                        fig_schedule = px.area(
                            schedule,
                            x="Mes",
                            y="Saldo final",
                            title="Trayectoria estimada del saldo",
                            labels={"Saldo final": "Saldo ARS equivalente"},
                        )
                        fig_schedule.update_layout(height=390, margin=dict(l=10, r=10, t=55, b=10))
                        fig_schedule.update_yaxes(tickprefix="$ ", separatethousands=True)
                        st.plotly_chart(fig_schedule, use_container_width=True)
                        with st.expander("Ver cronograma mensual"):
                            st.dataframe(
                                schedule,
                                use_container_width=True,
                                hide_index=True,
                                column_config={
                                    "Mes": st.column_config.DateColumn(format="MM/YYYY"),
                                    "Saldo inicial": st.column_config.NumberColumn(format="$ %.2f"),
                                    "Interés estimado": st.column_config.NumberColumn(format="$ %.2f"),
                                    "Pago planificado": st.column_config.NumberColumn(format="$ %.2f"),
                                    "Saldo final": st.column_config.NumberColumn(format="$ %.2f"),
                                },
                            )

            st.markdown("#### Orden sugerido de negociación y cancelación")
            priority_plan = _dt_priority_table(plan_scope, plan_exchange).head(20)
            if not priority_plan.empty:
                plan_view = pd.DataFrame({
                    "Orden": range(1, len(priority_plan) + 1),
                    "Prioridad": priority_plan["prioridad"].values,
                    "Situación": priority_plan["_situacion"].values,
                    "Empresa": priority_plan["empresa"].values,
                    "Acreedor": priority_plan["acreedor"].values,
                    "Categoría": priority_plan["categoria"].values,
                    "Saldo ARS": priority_plan["_saldo_ars"].values,
                    "Saldo USD": priority_plan["_saldo_usd"].values,
                    "Equivalente ARS": priority_plan["Equivalente ARS"].values,
                })
                st.dataframe(
                    plan_view,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Saldo ARS": st.column_config.NumberColumn(format="$ %.2f"),
                        "Saldo USD": st.column_config.NumberColumn(format="USD %.2f"),
                        "Equivalente ARS": st.column_config.NumberColumn(format="$ %.2f"),
                    },
                )

    # ------------------------------------------------------------------
    # TAB 6 · IA
    # ------------------------------------------------------------------
    with tabs[5]:
        st.subheader("Analista IA de deuda corporativa")
        st.caption(
            "La IA analiza la información del módulo y puede responder sobre evolución, "
            "concentración, prioridades, vencimientos y escenarios de pago."
        )
        question = st.text_area(
            "Pregunta ejecutiva",
            placeholder=(
                "Ej.: ¿Qué tres acreedores debería negociar primero? "
                "¿Por qué subió la deuda? ¿Qué obligaciones vencen antes?"
            ),
            height=120,
            key="dt_ai_question",
        )
        ai_period = st.selectbox(
            "Alcance",
            ["Último corte", "Todo el histórico"],
            key="dt_ai_scope",
        )
        if st.button("🧠 Analizar deuda", type="primary", key="dt_ai_button", use_container_width=True):
            if not question.strip():
                st.warning("Escribí una pregunta.")
            elif data.empty:
                st.info("No hay datos para analizar.")
            else:
                ai_data = data.copy()
                if ai_period == "Último corte":
                    ai_data = ai_data[ai_data["_periodo"].eq(_dt_latest_period(ai_data))]
                ai_columns = [column for column in _DEUDA_TOTAL_COLUMNS if column not in {"created_at", "updated_at"}]
                ai_data = ai_data[ai_columns].head(2500)
                with st.spinner("Analizando la estructura completa de deuda..."):
                    try:
                        answer = preguntar_ia(
                            modulo=module_name,
                            df=ai_data,
                            pregunta=question,
                        )
                        st.success(answer)
                    except Exception as error:
                        st.error("No se pudo completar el análisis de IA.")
                        st.exception(error)

    # ------------------------------------------------------------------
    # TAB 7 · EXPORTACIÓN
    # ------------------------------------------------------------------
    with tabs[6]:
        import io

        st.subheader("Exportación y respaldo")
        if data.empty:
            st.info("No hay datos para exportar.")
        else:
            export_data = _dt_prepare_sheet(data[_DEUDA_TOTAL_COLUMNS])
            latest_period = _dt_latest_period(data)
            latest_export = export_data[
                pd.to_datetime(export_data["periodo"], errors="coerce").dt.to_period("M").dt.to_timestamp().eq(latest_period)
            ].copy()
            e1, e2, e3 = st.columns(3)
            with e1:
                st.download_button(
                    "⬇️ CSV completo",
                    data=export_data.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"deuda_total_historico_{today.strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key="dt_export_csv_all",
                )
            with e2:
                st.download_button(
                    "⬇️ CSV último corte",
                    data=latest_export.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"deuda_total_{latest_period.strftime('%Y_%m')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key="dt_export_csv_latest",
                )
            with e3:
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                    export_data.to_excel(writer, sheet_name="Historico", index=False)
                    latest_export.to_excel(writer, sheet_name="Ultimo corte", index=False)
                    summary = data[data["_periodo"].eq(latest_period)].groupby(
                        ["empresa", "categoria"], as_index=False
                    ).agg(Saldo_ARS=("_saldo_ars", "sum"), Saldo_USD=("_saldo_usd", "sum"))
                    summary.to_excel(writer, sheet_name="Resumen", index=False)
                st.download_button(
                    "⬇️ Excel ejecutivo",
                    data=buffer.getvalue(),
                    file_name=f"deuda_total_ejecutivo_{today.strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="dt_export_excel",
                )

            st.markdown("#### Resumen exportable del último corte")
            summary_view = data[data["_periodo"].eq(latest_period)].groupby(
                ["empresa", "categoria"], as_index=False
            ).agg(
                Obligaciones=("id_registro", "count"),
                Saldo_ARS=("_saldo_ars", "sum"),
                Saldo_USD=("_saldo_usd", "sum"),
            )
            st.dataframe(
                summary_view,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Saldo_ARS": st.column_config.NumberColumn("Saldo ARS", format="$ %.2f"),
                    "Saldo_USD": st.column_config.NumberColumn("Saldo USD", format="USD %.2f"),
                },
            )



def render_facturacion_pro(module_name: str, cfg: Dict[str, Any]) -> None:
    table = cfg["table"]

    # CONVENIOS: lectura especializada de todas las pestañas heterogéneas.
    # Se resuelve antes de get_df("convenios") porque esa hoja maestra no es necesaria.
    if table == "convenios" or "convenio" in str(module_name).lower():
        render_header()
        st.header(module_name)
        st.caption(cfg.get("descripcion", ""))
        render_convenios_pro()
        return

    try:
        df_base = get_df(table)
    except Exception as e:
        st.error(f"No se pudo leer Google Sheets para {table}: {e}")
        df_base = pd.DataFrame()
    render_header()
    st.header(module_name)
    st.caption(cfg.get("descripcion", ""))
    if table == "agenda_quirofano":

        render_agenda_quirofano_ultra_pro(
    
            df_original=df_base.copy(),
    
            guardar_callback=lambda df_nuevo: sync_df_to_sheet(
    
                "agenda_quirofano",
    
                df_nuevo,
    
            ),
    
        )
    
        return
    # DEUDA TOTAL: centro corporativo especializado, con gestión, migración,
    # evolución, vencimientos, pagos, simulador, IA y exportación.
    if "deuda_total" in str(table).lower() or "deuda_total" in _dt_norm(module_name):
        render_deuda_total_pro(
            df_original=df_base.copy(),
            table=table,
            module_name=module_name,
        )
        return

    labels = get_fact_labels(module_name, cfg)
    tab_panel, tab_cargar, tab_importar, tab_editar, tab_columnas, tab_exportar = st.tabs([
        "📊 Panel PRO",
        "➕ Cargar",
        "📥 Importar",
        "✏️ Editar tabla",
        "🏷️ Editar columnas",
        "📤 Exportar",
    ])
    with tab_panel:
        df_panel = add_balance_columns(df_base.copy())
        # FARMACIA Y STOCK CLÍNICO PRO

        # =====================================================

        if table == "farmacia":

            try:

                df_movimientos_farmacia = get_df(

                    "farmacia_movimientos"

                )

            except Exception:

                df_movimientos_farmacia = pd.DataFrame()

            render_farmacia_pro(

                df_stock=df_base,

                df_movimientos=df_movimientos_farmacia,

                guardar_stock_callback=lambda df_nuevo: (

                    sync_df_to_sheet(

                        "farmacia",

                        df_nuevo,

                    )

                ),

                guardar_movimientos_callback=lambda df_nuevo: (

                    sync_df_to_sheet(

                        "farmacia_movimientos",

                        df_nuevo,

                    )

                ),

            )

            return
        
        # FACTURACIÓN VM / VMR: panel industrial de solo lectura.
        # No modifica el Sheet y conserva las pestañas Cargar, Importar, Editar y Exportar.
        if table in ["facturacion_vm", "facturacion_vmr"]:
            render_facturacion_industrial(
                df_original=df_base.copy(),
                module_name=module_name,
                table=table,
            )
        elif df_panel.empty:
            st.warning("No hay registros cargados.")
        else:
            filtered = apply_filters(df_panel, module_name)

            # Caja VM / VMR utiliza su panel integral y el historial completo
            # para calcular correctamente saldo inicial, cierre y saldo actual.
            # No se ejecuta debajo el panel genérico para evitar métricas y tablas duplicadas.
            if table in ["caja_vm", "caja_vmr"]:
                render_caja_pro_panel(
                    df=filtered,
                    module_name=module_name,
                    df_total=df_panel,
                )
            elif table in ["banco_galicia_vm", "banco_macro_vmr"]:
                # Banco VM / VMR usa un centro bancario integral. Se entrega
                # también el historial completo para reconstruir saldo inicial,
                # saldo de cierre y posición actual sin depender de los filtros.
                render_banco_pro_panel(
                    df=filtered,
                    module_name=module_name,
                    df_total=df_panel,
                )
            else:
                if table == "cuenta_corriente_vm":
                    filtered = filtered.drop(columns=["importe_usd", "pagado_usd"], errors="ignore")
                if table == "honorarios_medicos" or "honorarios" in module_name.lower():
                    render_honorarios_medicos_pro(filtered)
                else:
                    render_metricas_panel(filtered, table)
                if table == "cuenta_corriente_vm":
                    render_dashboard_proveedores_vm(filtered)
                if table in ["cuenta_corriente_vm", "cuenta_corriente_vmr"]:
                    render_cuenta_corriente_pro(filtered, table)
                render_tabla_limpia_panel(filtered)
    with tab_cargar:
        st.subheader("Nuevo registro")
        with st.form(f"form_add_{table}", clear_on_submit=False):
            data: Dict[str, Any] = {}
            cols = st.columns(2)
            for i, field in enumerate(cfg["fields"]):
                with cols[i % 2]:
                    raw = input_field(field, f"add_{table}")
                    data[field[0]] = clean_for_db(raw, field[1])
            submitted = st.form_submit_button("Guardar registro", type="primary")
            if submitted:
                errors = validate_required(cfg, data)
                if errors:
                    st.error("Faltan completar campos obligatorios: " + ", ".join(errors))
                    st.write("DEBUG DATA:", data)
                else:
                    try:
                        insert_row(table, data)
                        st.success("Registro guardado correctamente.")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error("Error al guardar el registro")
                        st.exception(e)
    with tab_importar:
        render_importer(module_name, cfg)
    with tab_editar:
        st.subheader("Editar registros cargados")
        df = add_balance_columns(df_base.copy())
        if df.empty:
            st.warning("No hay registros para editar.")
        else:
            df_edit = df.copy()
            if table == "cuenta_corriente_vm":
                df_edit = df_edit.drop(columns=["importe_usd", "pagado_usd"], errors="ignore")
            df_edit = df_edit.drop(columns=["created_at", "updated_at"], errors="ignore")
            if "mes" in df_edit.columns:
                orden = parse_mes(df_edit["mes"])
                df_edit = df_edit.assign(_orden=orden)
                df_edit = df_edit.sort_values("_orden", ascending=False, na_position="last")
                df_edit["mes"] = df_edit["_orden"].dt.strftime("%d/%m/%Y")
                df_edit["mes"] = df_edit["mes"].fillna("")
                df_edit = df_edit.drop(columns=["_orden"], errors="ignore")
            edited_df = st.data_editor(
                df_edit,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key=f"editor_{table}",
            )
            if st.button("Guardar cambios", type="primary", key=f"guardar_editor_{table}"):
                try:
                    limpio = edited_df.copy()
                    limpio = limpio.drop(
                        columns=["saldo", "saldo_usd", "saldo_movimiento"],
                        errors="ignore"
                    )
                    if "mes" in limpio.columns:
                        fechas = parse_mes(limpio["mes"])
                        limpio["mes"] = fechas.dt.strftime("%Y-%m-%d")
                        limpio["mes"] = limpio["mes"].replace("NaT", "")
                        limpio["mes"] = limpio["mes"].fillna("")
                    sync_df_to_sheet(table, limpio)
                    st.success(f"Cambios guardados correctamente. Registros procesados: {len(limpio)}")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error("ERROR AL GUARDAR")
                    st.exception(e)
    with tab_columnas:
        st.info("Editor de columnas pendiente.")
    with tab_exportar:
        df = add_balance_columns(df_base.copy())
        if table == "cuenta_corriente_vm":
            df = df.drop(columns=["importe_usd", "pagado_usd"], errors="ignore")
        if df.empty:
            st.info("No hay datos para exportar.")
        else:
            export_df = format_facturacion_table(df, labels)
            csv = export_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "Descargar CSV",
                data=csv,
                file_name=f"{table}.csv",
                mime="text/csv"
            )
            xlsx_path = Path(f"{table}.xlsx")
            export_df.to_excel(xlsx_path, index=False)
            with open(xlsx_path, "rb") as f:
                st.download_button(
                    "Descargar Excel",
                    data=f,
                    file_name=f"{table}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

def render_cuenta_corriente_pro(df, table=""):
    import re
    import unicodedata
    import numpy as np
    import pandas as pd
    import plotly.express as px
    import streamlit as st

    # 1. Trabajamos sobre una copia: la planilla no se toca.
    if df is None or df.empty:
        st.info("Todavía no hay registros para analizar.")
        return

    d = df.copy()
    empresa = "VMR" if str(table).lower().endswith("_vmr") else "VM"
    key = f"cc_pro_{empresa.lower()}"

    def nombre_limpio(valor):
        valor = unicodedata.normalize("NFKD", str(valor))
        valor = valor.encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^a-zA-Z0-9]+", "_", valor.lower()).strip("_")

    def a_numero(valor):
        if pd.isna(valor):
            return 0.0
        if isinstance(valor, (int, float, np.integer, np.floating)):
            return float(valor)

        texto = str(valor).strip()
        if not texto or texto.lower() in {"nan", "none", "null", "-"}:
            return 0.0

        texto = (
            texto.replace("US$", "")
            .replace("USD", "")
            .replace("$", "")
            .replace(" ", "")
        )

        if "," in texto and "." in texto:
            if texto.rfind(",") > texto.rfind("."):
                texto = texto.replace(".", "").replace(",", ".")
            else:
                texto = texto.replace(",", "")
        elif "," in texto:
            texto = texto.replace(".", "").replace(",", ".")

        texto = re.sub(r"[^0-9.\-]", "", texto)

        try:
            return float(texto)
        except ValueError:
            return 0.0

    def columna(nombre, defecto=""):
        if nombre in d.columns:
            return d[nombre]
        return pd.Series(defecto, index=d.index)

    def moneda(valor, simbolo="$"):
        texto = f"{float(valor or 0):,.2f}"
        texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{simbolo} {texto}"

    d.columns = [nombre_limpio(c) for c in d.columns]
    d = d.loc[:, ~d.columns.duplicated()].copy()

    d["_fecha"] = pd.to_datetime(columna("fecha", pd.NaT), errors="coerce")
    d["_vencimiento"] = pd.to_datetime(
        columna("vencimiento", pd.NaT),
        errors="coerce",
    )
    d["_observaciones"] = columna("observaciones", "")

    # 2. Adaptamos las dos planillas diferentes al mismo formato.
    if empresa == "VMR":
        d["_entidad"] = columna("entidad", "Sin entidad")
        d["_documento"] = columna("factura", "")
        d["_tipo"] = columna("estado", "A pagar")
        d["_estado_planilla"] = ""

        # En VMR, según tu planilla:
        # pagado = importe original
        # saldo = importe todavía pendiente
        importe_original = columna("pagado", 0).apply(a_numero)
        saldo_pendiente = columna("saldo", 0).apply(a_numero).clip(lower=0)

        d["_importe"] = pd.concat(
            [importe_original, saldo_pendiente],
            axis=1,
        ).max(axis=1)
        d["_saldo"] = saldo_pendiente
        d["_pagado"] = (d["_importe"] - d["_saldo"]).clip(lower=0)

    else:
        d["_entidad"] = columna("persona_entidad", "Sin entidad")
        d["_documento"] = columna("concepto", "")
        d["_tipo"] = columna("tipo", "A pagar")
        d["_estado_planilla"] = columna("estado", "")

        d["_importe"] = columna("importe", 0).apply(a_numero).clip(lower=0)
        d["_pagado"] = columna("pagado", 0).apply(a_numero).clip(lower=0)
        d["_saldo"] = (d["_importe"] - d["_pagado"]).clip(lower=0)

    d["_pagado_usd"] = columna("pagado_usd", 0).apply(a_numero).clip(lower=0)
    d["_saldo_usd"] = columna("saldo_usd", 0).apply(a_numero).clip(lower=0)

    for c in [
        "_entidad",
        "_documento",
        "_tipo",
        "_estado_planilla",
        "_observaciones",
    ]:
        d[c] = d[c].fillna("").astype(str).str.strip()

    d["_entidad"] = d["_entidad"].replace("", "Sin entidad")
    d["_tipo"] = d["_tipo"].replace("", "A pagar")

    # 3. Calculamos la situación real de cada comprobante.
    hoy = pd.Timestamp.today().normalize()
    d["_situacion"] = "Pendiente"
    d.loc[d["_saldo"] <= 0.01, "_situacion"] = "Pagado"

    es_vencido = (
        (d["_saldo"] > 0.01)
        & d["_vencimiento"].notna()
        & (d["_vencimiento"] < hoy)
    )
    d.loc[es_vencido, "_situacion"] = "Vencido"

    vence_7 = (
        (d["_saldo"] > 0.01)
        & d["_vencimiento"].between(
            hoy,
            hoy + pd.Timedelta(days=7),
            inclusive="both",
        )
    )
    d.loc[vence_7, "_situacion"] = "Vence en 7 días"

    vence_30 = (
        (d["_saldo"] > 0.01)
        & d["_vencimiento"].between(
            hoy + pd.Timedelta(days=8),
            hoy + pd.Timedelta(days=30),
            inclusive="both",
        )
    )
    d.loc[vence_30, "_situacion"] = "Vence en 30 días"

    d["_es_cobrar"] = d["_tipo"].str.lower().str.contains("cobrar", na=False)

    # 4. Panel y filtros.
    st.markdown(f"## 💳 Cuenta Corriente {empresa} · Panel PRO")
    st.caption(
        "Saldos, pagos, vencimientos, entidades y dólares en un solo lugar."
    )

    f1, f2, f3 = st.columns(3)

    with f1:
        situacion = st.selectbox(
            "Situación",
            [
                "Todos",
                "Pendiente",
                "Pagado",
                "Vencido",
                "Vence en 7 días",
                "Vence en 30 días",
            ],
            key=f"{key}_situacion",
        )

    with f2:
        entidades = st.multiselect(
            "Entidad",
            sorted(d["_entidad"].unique().tolist()),
            placeholder="Todas",
            key=f"{key}_entidades",
        )

    with f3:
        buscar = st.text_input(
            "Buscar",
            placeholder="Entidad, factura o concepto",
            key=f"{key}_buscar",
        ).strip().lower()

    x = d.copy()

    if situacion != "Todos":
        x = x[x["_situacion"].eq(situacion)]

    if entidades:
        x = x[x["_entidad"].isin(entidades)]

    if buscar:
        texto = (
            x["_entidad"]
            + " "
            + x["_documento"]
            + " "
            + x["_tipo"]
            + " "
            + x["_estado_planilla"]
            + " "
            + x["_observaciones"]
        ).str.lower()
        x = x[texto.str.contains(buscar, regex=False, na=False)]

    if x.empty:
        st.warning("No hay registros para esos filtros.")
        return

    total = x["_importe"].sum()
    pagado = x["_pagado"].sum()
    saldo = x["_saldo"].sum()
    vencido = x.loc[x["_situacion"].eq("Vencido"), "_saldo"].sum()
    a_cobrar = x.loc[x["_es_cobrar"], "_saldo"].sum()
    a_pagar = x.loc[~x["_es_cobrar"], "_saldo"].sum()
    saldo_usd = x["_saldo_usd"].sum()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total registrado", moneda(total))
    m2.metric("Pagado / regularizado", moneda(pagado))
    m3.metric("Saldo pendiente", moneda(saldo))
    m4.metric("Saldo vencido", moneda(vencido))

    m5, m6, m7 = st.columns(3)
    m5.metric("A pagar", moneda(a_pagar))
    m6.metric("A cobrar", moneda(a_cobrar))
    m7.metric("Saldo pendiente USD", moneda(saldo_usd, "US$"))

    avance = pagado / total if total > 0 else 0
    st.progress(min(max(avance, 0.0), 1.0))
    st.caption(f"Regularización total: {avance * 100:.1f}%")

    tab1, tab2, tab3 = st.tabs(
        ["📊 Resumen", "⏰ Vencimientos", "📋 Detalle"]
    )

    with tab1:
        izquierda, derecha = st.columns(2)

        estados = (
            x.groupby("_situacion", as_index=False)["_saldo"]
            .sum()
            .rename(columns={"_situacion": "Situación", "_saldo": "Saldo"})
        )

        with izquierda:
            st.markdown("#### Saldo por situación")
            if estados["Saldo"].sum() > 0:
                fig = px.pie(
                    estados,
                    names="Situación",
                    values="Saldo",
                    hole=0.55,
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.success("No hay saldo pendiente.")

        top = (
            x.groupby("_entidad", as_index=False)["_saldo"]
            .sum()
            .sort_values("_saldo", ascending=False)
            .head(10)
            .rename(columns={"_entidad": "Entidad", "_saldo": "Saldo"})
        )

        with derecha:
            st.markdown("#### Top 10 saldos pendientes")
            fig = px.bar(
                top.sort_values("Saldo"),
                x="Saldo",
                y="Entidad",
                orientation="h",
                text_auto=".2s",
            )
            st.plotly_chart(fig, use_container_width=True)

        resumen = (
            x.groupby("_entidad", as_index=False)
            .agg(
                Comprobantes=("_documento", "count"),
                Importe=("_importe", "sum"),
                Pagado=("_pagado", "sum"),
                Saldo=("_saldo", "sum"),
                Saldo_USD=("_saldo_usd", "sum"),
                Proximo_vencimiento=("_vencimiento", "min"),
            )
            .rename(columns={"_entidad": "Entidad"})
            .sort_values("Saldo", ascending=False)
        )

        st.markdown("#### Resumen por entidad")
        st.dataframe(resumen, use_container_width=True, hide_index=True)

    with tab2:
        cantidad_vencidos = int(x["_situacion"].eq("Vencido").sum())
        cantidad_7 = int(x["_situacion"].eq("Vence en 7 días").sum())
        sin_fecha = int(
            ((x["_saldo"] > 0.01) & x["_vencimiento"].isna()).sum()
        )

        a1, a2, a3 = st.columns(3)
        a1.metric("Vencidos", cantidad_vencidos)
        a2.metric("Vencen en 7 días", cantidad_7)
        a3.metric("Sin vencimiento", sin_fecha)

        if cantidad_vencidos:
            st.error(
                f"Hay {cantidad_vencidos} comprobantes vencidos por "
                f"{moneda(vencido)}."
            )

        pendientes = x[x["_saldo"] > 0.01].copy()
        pendientes["Días"] = (pendientes["_vencimiento"] - hoy).dt.days

        pendientes = pendientes[
            [
                "_vencimiento",
                "_entidad",
                "_documento",
                "_tipo",
                "_situacion",
                "Días",
                "_saldo",
                "_saldo_usd",
            ]
        ].rename(
            columns={
                "_vencimiento": "Vencimiento",
                "_entidad": "Entidad",
                "_documento": "Factura / concepto",
                "_tipo": "Tipo",
                "_situacion": "Situación",
                "_saldo": "Saldo ARS",
                "_saldo_usd": "Saldo USD",
            }
        ).sort_values(
            ["Vencimiento", "Saldo ARS"],
            ascending=[True, False],
            na_position="last",
        )

        st.dataframe(pendientes, use_container_width=True, hide_index=True)

    with tab3:
        detalle = x[
            [
                "_fecha",
                "_vencimiento",
                "_entidad",
                "_documento",
                "_tipo",
                "_estado_planilla",
                "_situacion",
                "_importe",
                "_pagado",
                "_saldo",
                "_pagado_usd",
                "_saldo_usd",
                "_observaciones",
            ]
        ].rename(
            columns={
                "_fecha": "Fecha",
                "_vencimiento": "Vencimiento",
                "_entidad": "Entidad",
                "_documento": "Factura / concepto",
                "_tipo": "Tipo",
                "_estado_planilla": "Estado planilla",
                "_situacion": "Situación calculada",
                "_importe": "Importe ARS",
                "_pagado": "Pagado ARS",
                "_saldo": "Saldo ARS",
                "_pagado_usd": "Pagado USD",
                "_saldo_usd": "Saldo USD",
                "_observaciones": "Observaciones",
            }
        )

        st.dataframe(detalle, use_container_width=True, hide_index=True)

        st.download_button(
            "⬇️ Descargar detalle filtrado",
            data=detalle.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"cuenta_corriente_{empresa.lower()}.csv",
            mime="text/csv",
            key=f"{key}_descarga",
        )
def total_mod(nombre, dfs):
    df = dfs.get(nombre, pd.DataFrame())
    if df.empty:
        return 0.0
    for col in ["saldo", "saldo_movimiento", "importe", "valor_pesos", "monto", "ingreso"]:
        if col in df.columns:
            return sum_money_col(df[col])
    return 0.0
def render_analisis_global_vitae(dfs):
    ANIO_ANALISIS = 2026
    st.divider()
    st.markdown(f"## 📊 Análisis Global VITAE {ANIO_ANALISIS}")
    rows = []
    for module_name, cfg in MODULES.items():
        df = dfs.get(module_name, pd.DataFrame()).copy()
        if df.empty:
            continue
        empresa = cfg.get("empresa", "VITAE")
        for _, row in df.iterrows():
            fecha = (
                row.get("fecha")
                or row.get("vencimiento")
                or row.get("created_at")
            )
            fecha = pd.to_datetime(fecha, errors="coerce")
            if pd.isna(fecha):
                continue
            if fecha.year != ANIO_ANALISIS:
                continue
            ingreso = money(row.get("ingreso", 0))
            egreso = money(row.get("egreso", 0))
            valor = (
                money(row.get("valor_pesos", 0))
                or money(row.get("importe", 0))
                or money(row.get("monto", 0))
                or money(row.get("valor", 0))
            )
            estado = str(row.get("estado", "")).lower()
            facturado = valor if valor else ingreso
            cobrado = (
                valor
                if estado in [
                    "cobrado",
                    "pagado",
                    "realizado",
                    "completo",
                    "finalizado"
                ]
                else ingreso
            )
            pendiente = (
                valor
                if estado in [
                    "pendiente",
                    "a cobrar",
                    "adeudado",
                    "deuda"
                ]
                else 0
            )
            rows.append({
                "Fecha": fecha,
                "Mes": fecha.strftime("%Y-%m"),
                "Empresa": empresa,
                "Módulo": module_name,
                "Facturado": facturado,
                "Cobrado": cobrado,
                "Pendiente": pendiente,
                "Egreso": egreso,
                "Resultado": cobrado - egreso,
            })
    if not rows:
        st.info("No hay datos de 2026 para analizar.")
        return
    global_df = pd.DataFrame(rows)
    facturado_total = global_df["Facturado"].sum()
    cobrado_total = global_df["Cobrado"].sum()
    pendiente_total = global_df["Pendiente"].sum()
    egreso_total = global_df["Egreso"].sum()
    resultado_total = global_df["Resultado"].sum()
    # =====================================================
    # DIRECTOR IA - ANÁLISIS EJECUTIVO AUTOMÁTICO
    # =====================================================
    st.markdown("---")
    with st.spinner("🧠 El Director IA está analizando Vitae..."):
        resumen_director = generar_resumen_ejecutivo(
            global_df=global_df,
            anio=ANIO_ANALISIS,
        )
    st.markdown(resumen_director)
    st.markdown("---")
    
    mensual = global_df.groupby(
        "Mes",
        as_index=False
    )[
        [
            "Facturado",
            "Cobrado",
            "Pendiente",
            "Egreso",
            "Resultado"
        ]
    ].sum()
    st.markdown("### 📅 Resumen mensual 2026")
    fig = px.bar(
        mensual,
        x="Mes",
        y=[
            "Facturado",
            "Cobrado",
            "Pendiente",
            "Egreso"
        ],
        barmode="group",
        title="Movimientos mensuales"
    )
    fig.update_layout(height=450)
    st.plotly_chart(
        fig,
        use_container_width=True,
        key="global_mensual_2026"
    )
    mensual["Acumulado"] = mensual["Resultado"].cumsum()
    st.markdown("### 📈 Evolución acumulada")
    fig2 = px.line(
        mensual,
        x="Mes",
        y="Acumulado",
        markers=True,
        title="Resultado acumulado 2026"
    )
    fig2.update_layout(height=400)
    st.plotly_chart(
        fig2,
        use_container_width=True,
        key="global_acumulado_2026"
    )
    resumen_modulos = global_df.groupby(
        ["Módulo", "Empresa"],
        as_index=False
    )[
        [
            "Facturado",
            "Cobrado",
            "Pendiente",
            "Egreso",
            "Resultado"
        ]
    ].sum()
    

    def deuda_mod(nombre):
        df = dfs.get(nombre, pd.DataFrame())
        if df.empty:
            return 0.0
        col_monto = None
        for c in ["valor_pesos", "importe", "monto", "saldo", "valor"]:
            if c in df.columns:
                col_monto = c
                break
        if not col_monto:
            return 0.0
        if "estado" not in df.columns:
            return df[col_monto].apply(money).sum()
        estados_deuda = ["pendiente", "a pagar", "adeudado", "deuda"]
        deuda = df[
            df["estado"].astype(str).str.lower().isin(estados_deuda)
        ]
        return deuda[col_monto].apply(money).sum()
    caja_vmr = total_mod("Caja VMR", dfs)
    banco_vmr = total_mod("Banco Macro VMR", dfs)
    caja_vm = total_mod("Caja VM", dfs)
    banco_vm = total_mod("Banco Galicia VM", dfs)
    gine_vitae = total_mod("Gine Vitae", dfs)
    pagos_pendientes = total_mod("Pagos pendientes Vitae", dfs)
    planes_pago = total_mod("Planes de pagos y préstamos", dfs)
    honorarios = total_mod("Honorarios médicos", dfs)
    deuda_imp_vmr = total_mod("Deudas Impositivas VMR", dfs)
    deuda_imp_vm = total_mod("Deudas Impositivas VM", dfs)
    liquidez_total = caja_vmr + banco_vmr + caja_vm + banco_vm + gine_vitae
    deuda_total_global = pagos_pendientes + planes_pago + honorarios + deuda_imp_vmr + deuda_imp_vm   
def render_resumen_empresa(titulo, empresa, dfs):
        for name, cfg in MODULES.items():
            df = dfs.get(name, pd.DataFrame()).copy()
            if df.empty:
                continue
        mods = {
            name: dfs.get(name, pd.DataFrame())
            for name, cfg in MODULES.items()
            if cfg.get("empresa") == empresa
        }
        liquidez = 0
        facturacion = 0
        cobrado = 0
        a_cobrar = 0
        a_pagar_emp = 0
        deuda_emp = 0
        vencidos_emp = 0
        tareas_emp = 0
        pacientes = 0
        for name, df in mods.items():
            if df.empty:
                continue    
            tipo = MODULES[name].get("tipo", "")
            if tipo in ["caja", "banco"]:
                liquidez += total_mod(name)
            if "valor_pesos" in df.columns:
                facturacion += df["valor_pesos"].apply(money).sum()
                pacientes += len(df)
                if "estado" in df.columns:
                    cobrado += df[df["estado"].astype(str).str.lower().isin(["completo", "cobrado", "pagado"])]["valor_pesos"].apply(money).sum()
                    a_cobrar += df[df["estado"].astype(str).str.lower().isin(["pendiente", "parcial", "vencido"])]["valor_pesos"].apply(money).sum()
            if "monto" in df.columns and "estado" in df.columns:
                a_pagar_emp += df[df["estado"].astype(str).str.lower().isin(["pendiente", "vencido"])]["monto"].apply(money).sum()
            if "vencimiento" in df.columns:
                vencidos_emp += len(df)
            if name == "Tareas Pendientes" and "estado" in df.columns:
                tareas_emp += len(df[~df["estado"].astype(str).str.lower().isin(["finalizada", "cancelada"])])
        resultado = cobrado - a_pagar_emp
        promedio = facturacion / pacientes if pacientes > 0 else 0
        st.divider()
        st.markdown(f"### {titulo}")
        r1, r2, r3, r4, r5 = st.columns(5)
        r1.metric("Liquidez actual", fmt_money(liquidez))
        r2.metric("Facturación mes", fmt_money(facturacion))
        r3.metric("Cobrado mes", fmt_money(cobrado))
        r4.metric("A cobrar", fmt_money(a_cobrar))
        r5.metric("Resultado mes", fmt_money(resultado))
        r6, r7, r8, r9, r10 = st.columns(5)
        r6.metric("A pagar", fmt_money(a_pagar_emp))
        r7.metric("Deuda total", fmt_money(deuda_emp))
        r8.metric("Vencidos / críticos", vencidos_emp)
        r9.metric("Tareas pendientes", tareas_emp)
        r10.metric("Promedio por paciente", fmt_money(promedio))
        rows = []
        for name, cfg in MODULES.items():
            df = dfs.get(name, pd.DataFrame())
            if df.empty:
                continue
            if "valor_pesos" in df.columns:
                total = df["valor_pesos"].apply(money).sum()
            elif "importe" in df.columns:
                total = df["importe"].apply(money).sum()
            else:
                total = 0
            if total > 0:
                rows.append({
                "Módulo": name,
                "Empresa": MODULES[name]["empresa"],
                "Total": total,
                "Registros": len(df),
            })
        resumen = pd.DataFrame(rows)           
        st.divider()                                

def render_configuracion() -> None:
    render_header()
    st.header("Configuración")
    tab1, tab2, tab3, tab4 = st.tabs([
        "👤 Usuarios",
        "🔐 Permisos",
        "🏢 Empresas",
        "⚙️ Sistema"
    ])
    with tab1:
        st.subheader("Usuarios")
        st.info("Acá irá la gestión de usuarios.")
    with tab2:
        st.subheader("Permisos")
        st.info("Acá irá la gestión de permisos.")
    with tab3:
        st.subheader("Empresas")
        st.info("Acá irá la gestión de empresas.")
    with tab4:
        st.subheader("Sistema")
        st.info("Acá irá la configuración general del sistema.")
        st.markdown("### 🗑️ Borrar base de un módulo")
        modulo_borrar = st.selectbox(
            "Módulo a borrar",
            list(MODULES.keys()),
            key="modulo_borrar_db"
        )
        confirmar = st.checkbox(
            f"Confirmo borrar todos los datos de {modulo_borrar}",
            key="confirmar_borrar_db"
        )
        st.markdown("### 🗑️ Borrar base de un módulo")
        modulo_borrar = st.selectbox(
            "Módulo a borrar",
            list(MODULES.keys()),
            key="modulo_borrar_db_2"
        )
        confirmar = st.checkbox(
            f"Confirmo borrar todos los datos de {modulo_borrar}",
            key="confirmar_borrar_db_2"
        )
    st.markdown("### Sincronización Google Sheets")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("⬆️ Subir datos actuales a Google Sheets"):
            try:
                result = sync_all_to_sheets()
                st.success("Sincronización ejecutada.")
                st.write(result)
            except Exception as e:
                st.error("No se pudo subir a Google Sheets.")
                st.exception(e)
    with col2:
        if st.button("⬇️ Leer datos desde Google Sheets"):
            try:
                restore_all_from_sheets()
                st.success("Datos restaurados desde Google Sheets.")
                st.rerun()
            except Exception as e:
                st.error(f"No se pudo leer desde Google Sheets: {e}")
def seed_examples() -> None:
    examples = [
        ("caja_vmr", {"fecha": date.today().strftime(DATE_FMT), "concepto": "Ingreso muestra fertilidad", "categoria": "Ingreso", "medio": "Efectivo", "ingreso": 150000, "egreso": 0, "responsable": "Administración", "observaciones": "Ejemplo"}),
        ("banco_galicia_vm", {"fecha": date.today().strftime(DATE_FMT), "concepto": "Pago proveedor quirófano", "tipo_movimiento": "Débito", "referencia": "OP-001", "ingreso": 0, "egreso": 80000, "conciliado": 1, "observaciones": "Ejemplo"}),
        ("pagos_pendientes_vitae", {"fecha": date.today().strftime(DATE_FMT), "empresa": "VITAE", "proveedor": "Proveedor insumos", "concepto": "Insumos médicos", "importe": 120000, "pagado": 0, "vencimiento": (date.today() + timedelta(days=7)).strftime(DATE_FMT), "prioridad": "Alta", "estado": "Pendiente", "observaciones": "Ejemplo"}),
        ("tareas_pendientes", {"fecha": date.today().strftime(DATE_FMT), "empresa": "VM", "tarea": "Revisar stock quirófano", "responsable": "Enfermería", "prioridad": "Alta", "vencimiento": (date.today() + timedelta(days=3)).strftime(DATE_FMT), "estado": "Pendiente", "observaciones": "Ejemplo"}),
    ]
    for table, data in examples:
        insert_row(table, data)
        st.cache_data.clear()
# =========================================================

# RESUMEN EJECUTIVO PROFESIONAL VMR + VM

# =========================================================

def render_resumen_empresas_pro(

    dfs: dict[str, pd.DataFrame],

) -> None:

    """

    Muestra un resumen ejecutivo profesional y comparable de:

    - VMR: Vitae Medicina Reproductiva

    - VM: Vitae Medical

    Los cálculos se realizan solamente con los DataFrames ya cargados

    en el dashboard. Esta función no modifica datos ni escribe en Sheets.

    """

    hoy = pd.Timestamp.today().normalize()

    inicio_mes = hoy.replace(day=1)

    fin_mes = inicio_mes + pd.offsets.MonthEnd(0)

    estados_cobrados = {

        "cobrado",

        "pagado",

        "completo",

        "realizado",

        "finalizada",

        "finalizado",

    }

    estados_cancelados = {

        "anulado",

        "anulada",

        "cancelado",

        "cancelada",

    }

    estados_cerrados = estados_cobrados | estados_cancelados

    configuracion_empresas = {

        "VMR": {

            "nombre": "Vitae Medicina Reproductiva",

            "icono": "🟣",

            "caja": "Caja VMR",

            "banco": "Banco Macro VMR",

            "facturacion": "Facturación VMR",

        },

        "VM": {

            "nombre": "Vitae Medical",

            "icono": "🔵",

            "caja": "Caja VM",

            "banco": "Banco Galicia VM",

            "facturacion": "Facturación VM",

        },

    }

    # -----------------------------------------------------

    # FUNCIONES INTERNAS SEGURAS

    # -----------------------------------------------------

    def obtener_df(nombre_modulo: str) -> pd.DataFrame:

        """

        Devuelve una copia segura del módulo solicitado.

        """

        df = dfs.get(nombre_modulo, pd.DataFrame())

        if df is None or df.empty:

            return pd.DataFrame()

        return df.copy()

    def normalizar_estado(df: pd.DataFrame) -> pd.Series:

        """

        Devuelve la columna estado normalizada.

        """

        if "estado" not in df.columns:

            return pd.Series(

                [""] * len(df),

                index=df.index,

                dtype="object",

            )

        return (

            df["estado"]

            .fillna("")

            .astype(str)

            .str.lower()

            .str.strip()

        )

    def obtener_fechas(df: pd.DataFrame) -> pd.Series:

        """

        Busca la primera columna de fecha disponible.

        """

        if df.empty:

            return pd.Series(dtype="datetime64[ns]")

        columnas_fecha = [

            "mes",

            "fecha",

            "fecha_factura",

            "created_at",

            "vencimiento",

        ]

        for columna in columnas_fecha:

            if columna not in df.columns:

                continue

            fechas = pd.to_datetime(

                df[columna],

                errors="coerce",

            )

            if fechas.notna().any():

                return fechas

        return pd.Series(

            [pd.NaT] * len(df),

            index=df.index,

            dtype="datetime64[ns]",

        )

    def obtener_columna_monto(df: pd.DataFrame) -> str | None:

        """

        Detecta la columna monetaria principal de un módulo.

        """

        columnas_monto = [

            "valor_pesos",

            "importe",

            "monto",

            "valor",

            "saldo",

        ]

        for columna in columnas_monto:

            if columna in df.columns:

                return columna

        return None

    def saldo_actual_modulo(nombre_modulo: str) -> float:

        """

        Obtiene el saldo actual de una caja o banco.

        Si existe una columna de saldo acumulado, toma el último saldo.

        Si no existe, calcula ingresos menos egresos.

        """

        df = obtener_df(nombre_modulo)

        if df.empty:

            return 0.0
        # Las cajas del resumen mensual deben usar exactamente

        # los ingresos y egresos correspondientes al mes actual.

        if nombre_modulo in ["Caja VM", "Caja VMR"]:

            fechas_caja = obtener_fechas(df)

            es_mes_actual = (

                fechas_caja.notna()

                & (fechas_caja >= inicio_mes)

                & (fechas_caja <= fin_mes)

            )

            df_mes_caja = df.loc[es_mes_actual].copy()

            ingresos_caja = (

                sum_money_col(df["ingreso"].fillna(0))

                if "ingreso" in df_mes_caja.columns

                else 0.0

            )

            egresos_caja = (

                sum_money_col(df["egreso"].fillna(0))

                if "egreso" in df_mes_caja.columns

                else 0.0

            )

            return float(ingresos_caja - egresos_caja)
        fechas = obtener_fechas(df)

        if fechas.notna().any():

            df = df.assign(_fecha_orden=fechas)

            df = df.sort_values(

                "_fecha_orden",

                na_position="first",

            )

        for columna_saldo in ["saldo", "saldo_movimiento"]:

            if columna_saldo not in df.columns:

                continue

            saldos = df[columna_saldo].apply(money)

            saldos_validos = saldos.dropna()

            if not saldos_validos.empty:

                return float(saldos_validos.iloc[-1])

        ingresos = (

            df["ingreso"].apply(money).sum()

            if "ingreso" in df.columns

            else 0.0

        )

        egresos = (

            df["egreso"].apply(money).sum()

            if "egreso" in df.columns

            else 0.0

        )

        return float(ingresos - egresos)

    def egresos_del_mes(

        modulo_caja: str,

        modulo_banco: str,

    ) -> float:

        """

        Suma de manera segura los egresos del mes

        correspondientes a caja y banco.

        """

        total = 0.0

        for nombre_modulo in [

            modulo_caja,

            modulo_banco,

        ]:

            df = obtener_df(nombre_modulo)

            if df.empty:

                continue

            if "egreso" not in df.columns:

                continue

            fechas = obtener_fechas(df)

            es_mes = (

                fechas.notna()

                & (fechas >= inicio_mes)

                & (fechas <= fin_mes)

            )

            egresos_filtrados = df.loc[

                es_mes,

                "egreso",

            ]

            valor_mes = sum_money_col(

                egresos_filtrados

            )

            if pd.isna(valor_mes):

                valor_mes = 0.0

            total += float(valor_mes)

        return float(total)

    def contar_vencidos_empresa(codigo_empresa: str) -> int:

        """

        Cuenta obligaciones vencidas de los módulos pertenecientes

        a la empresa indicada.

        """

        total_vencidos = 0

        for nombre_modulo, df_original in dfs.items():

            cfg = MODULES.get(nombre_modulo, {})

            empresa_modulo = str(

                cfg.get("empresa", "")

            ).upper().strip()

            nombre_mayusculas = str(nombre_modulo).upper()

            pertenece_empresa = (

                empresa_modulo == codigo_empresa

                or nombre_mayusculas.endswith(

                    f" {codigo_empresa}"

                )

            )

            if not pertenece_empresa:

                continue

            if df_original is None or df_original.empty:

                continue

            df = df_original.copy()

            if "vencimiento" not in df.columns:

                continue

            vencimientos = pd.to_datetime(

                df["vencimiento"],

                errors="coerce",

            )

            estados = normalizar_estado(df)

            mascara_vencidos = (

                vencimientos.notna()

                & (vencimientos < hoy)

                & (~estados.isin(estados_cerrados))

            )

            total_vencidos += int(

                mascara_vencidos.sum()

            )

        return total_vencidos

    def calcular_empresa(

        codigo_empresa: str,

        configuracion: dict,

    ) -> dict:

        """

        Calcula todos los indicadores ejecutivos de una empresa.

        """

        df_facturacion = obtener_df(

            configuracion["facturacion"]

        )

        caja_actual = saldo_actual_modulo(

            configuracion["caja"]

        )

        banco_actual = saldo_actual_modulo(

            configuracion["banco"]

        )

        disponible = caja_actual + banco_actual

        facturado_mes = 0.0

        cobrado_mes = 0.0

        pendiente_mes = 0.0

        pacientes_mes = 0

        if not df_facturacion.empty:

            fechas = obtener_fechas(df_facturacion)

            estados = normalizar_estado(df_facturacion)

            columna_monto = obtener_columna_monto(

                df_facturacion

            )

            es_mes = (

                fechas.notna()

                & (fechas >= inicio_mes)

                & (fechas <= fin_mes)

            )

            no_cancelado = ~estados.isin(

                estados_cancelados

            )

            registros_mes = es_mes & no_cancelado

            pacientes_mes = int(

                registros_mes.sum()

            )

            if columna_monto:

                montos = df_facturacion[

                    columna_monto

                ].apply(money)

                facturado_mes = float(

                    montos[registros_mes].sum()

                )

                es_cobrado = estados.isin(

                    estados_cobrados

                )

                cobrado_mes = float(

                    montos[

                        registros_mes & es_cobrado

                    ].sum()

                )

                pendiente_mes = max(

                    0.0,

                    facturado_mes - cobrado_mes,

                )

        egresos_mes = egresos_del_mes(

            configuracion["caja"],

            configuracion["banco"],

        )

        resultado_mes = cobrado_mes - egresos_mes

        cobranza_pct = (

            cobrado_mes / facturado_mes * 100

            if facturado_mes > 0

            else 0.0

        )

        vencidos = contar_vencidos_empresa(

            codigo_empresa

        )

        # -------------------------------------------------

        # PUNTAJE Y ESTADO AUTOMÁTICO

        # -------------------------------------------------

        puntaje = 0

        if cobranza_pct >= 90:

            puntaje += 45

        elif cobranza_pct >= 80:

            puntaje += 38

        elif cobranza_pct >= 70:

            puntaje += 30

        elif cobranza_pct >= 50:

            puntaje += 20

        else:

            puntaje += 8

        if resultado_mes > 0:

            puntaje += 30

        elif resultado_mes == 0:

            puntaje += 15

        else:

            puntaje += 0

        if disponible > 0:

            puntaje += 15

        if vencidos == 0:

            puntaje += 10

        elif vencidos <= 10:

            puntaje += 7

        elif vencidos <= 30:

            puntaje += 4

        puntaje = min(100, max(0, puntaje))

        if puntaje >= 85:

            estado = "Excelente"

            emoji_estado = "🟢"

            clase_estado = "estado-excelente"

        elif puntaje >= 70:

            estado = "Bueno"

            emoji_estado = "🟡"

            clase_estado = "estado-bueno"

        elif puntaje >= 50:

            estado = "Atención"

            emoji_estado = "🟠"

            clase_estado = "estado-atencion"

        else:

            estado = "Crítico"

            emoji_estado = "🔴"

            clase_estado = "estado-critico"

        return {

            "codigo": codigo_empresa,

            "nombre": configuracion["nombre"],

            "icono": configuracion["icono"],

            "caja": caja_actual,

            "banco": banco_actual,

            "disponible": disponible,

            "facturado_mes": facturado_mes,

            "cobrado_mes": cobrado_mes,

            "pendiente_mes": pendiente_mes,

            "egresos_mes": egresos_mes,

            "resultado_mes": resultado_mes,

            "pacientes_mes": pacientes_mes,

            "cobranza_pct": cobranza_pct,

            "vencidos": vencidos,

            "puntaje": puntaje,

            "estado": estado,

            "emoji_estado": emoji_estado,

            "clase_estado": clase_estado,

        }

    def clase_resultado(valor: float) -> str:

        if valor > 0:

            return "valor-positivo"

        if valor < 0:

            return "valor-negativo"

        return ""

    def tarjeta_empresa(

        datos: dict,

        participacion: float,

    ) -> str:

        """

        Construye la tarjeta HTML de una empresa.

        """

        clase_resultado_mes = clase_resultado(

            datos["resultado_mes"]

        )

        return dedent(f"""

        <div class="empresa-card">

            <div class="empresa-header">

                <div>

                    <div class="empresa-codigo">

                        {datos["icono"]} {datos["codigo"]}

                    </div>

                    <div class="empresa-nombre">

                        {datos["nombre"]}

                    </div>

                </div>

                <div class="empresa-estado {datos["clase_estado"]}">

                    {datos["emoji_estado"]}

                    {datos["estado"]}

                    <span>{datos["puntaje"]}/100</span>

                </div>

            </div>

            <div class="empresa-participacion">

                Participación en la facturación total del mes:

                <strong>{participacion:.1f}%</strong>

            </div>

            <div class="metricas-grid">

                <div class="metrica-box metrica-principal">

                    <div class="metrica-label">

                        💰 Disponible

                    </div>

                    <div class="metrica-valor">

                        {fmt_money(datos["disponible"])}

                    </div>

                    <div class="metrica-detalle">

                        Caja {fmt_money(datos["caja"])}

                        · Banco {fmt_money(datos["banco"])}

                    </div>

                </div>

                <div class="metrica-box">

                    <div class="metrica-label">

                        📈 Facturado del mes

                    </div>

                    <div class="metrica-valor">

                        {fmt_money(datos["facturado_mes"])}

                    </div>

                </div>

                <div class="metrica-box">

                    <div class="metrica-label">

                        ✅ Cobrado del mes

                    </div>

                    <div class="metrica-valor valor-positivo">

                        {fmt_money(datos["cobrado_mes"])}

                    </div>

                </div>

                <div class="metrica-box">

                    <div class="metrica-label">

                        ⏳ Pendiente de cobro

                    </div>

                    <div class="metrica-valor">

                        {fmt_money(datos["pendiente_mes"])}

                    </div>

                </div>

                <div class="metrica-box">

                    <div class="metrica-label">

                        📤 Egresos del mes

                    </div>

                    <div class="metrica-valor">

                        {fmt_money(datos["egresos_mes"])}

                    </div>

                </div>

                <div class="metrica-box">

                    <div class="metrica-label">

                        📊 Resultado del mes

                    </div>

                    <div class="metrica-valor {clase_resultado_mes}">

                        {fmt_money(datos["resultado_mes"])}

                    </div>

                </div>

                <div class="metrica-box">

                    <div class="metrica-label">

                        👥 Pacientes del mes

                    </div>

                    <div class="metrica-valor">

                        {datos["pacientes_mes"]}

                    </div>

                </div>

                <div class="metrica-box">

                    <div class="metrica-label">

                        🎯 Cobranza

                    </div>

                    <div class="metrica-valor">

                        {datos["cobranza_pct"]:.1f}%

                    </div>

                </div>

                <div class="metrica-box">

                    <div class="metrica-label">

                        ⚠️ Vencidos / críticos

                    </div>

                    <div class="metrica-valor">

                        {datos["vencidos"]}

                    </div>

                </div>

            </div>

        </div>

        """).strip()

    # -----------------------------------------------------

    # CÁLCULO DE AMBAS EMPRESAS

    # -----------------------------------------------------

    datos_vmr = calcular_empresa(

        "VMR",

        configuracion_empresas["VMR"],

    )

    datos_vm = calcular_empresa(

        "VM",

        configuracion_empresas["VM"],

    )

    facturacion_conjunta = (

        datos_vmr["facturado_mes"]

        + datos_vm["facturado_mes"]

    )

    participacion_vmr = (

        datos_vmr["facturado_mes"]

        / facturacion_conjunta

        * 100

        if facturacion_conjunta > 0

        else 0.0

    )

    participacion_vm = (

        datos_vm["facturado_mes"]

        / facturacion_conjunta

        * 100

        if facturacion_conjunta > 0

        else 0.0

    )

    resultado_conjunto = (

        datos_vmr["resultado_mes"]

        + datos_vm["resultado_mes"]

    )

    pacientes_conjuntos = (

        datos_vmr["pacientes_mes"]

        + datos_vm["pacientes_mes"]

    )

    cobranza_conjunta = (

        (

            datos_vmr["cobrado_mes"]

            + datos_vm["cobrado_mes"]

        )

        / facturacion_conjunta

        * 100

        if facturacion_conjunta > 0

        else 0.0

    )

    # -----------------------------------------------------

    # ESTILOS

    # -----------------------------------------------------

    st.html(

        dedent("""

        <style>

            .empresas-section {

                margin-top: 0.5rem;

                margin-bottom: 1rem;

            }

            .empresas-title {

                font-size: 1.85rem;

                font-weight: 750;

                color: #252938;

                margin-bottom: 0.15rem;

            }

            .empresas-subtitle {

                color: #707789;

                font-size: 0.95rem;

                margin-bottom: 1.25rem;

            }

            .empresa-card {

                background: linear-gradient(

                    145deg,

                    #ffffff 0%,

                    #f8f9fc 100%

                );

                border: 1px solid rgba(48, 55, 75, 0.12);

                border-radius: 22px;

                padding: 1.35rem;

                margin-bottom: 1rem;

                box-shadow:

                    0 8px 24px rgba(25, 33, 50, 0.06);

                min-height: 100%;

            }

            .empresa-header {

                display: flex;

                justify-content: space-between;

                align-items: flex-start;

                gap: 1rem;

                margin-bottom: 0.75rem;

            }

            .empresa-codigo {

                font-size: 1.65rem;

                font-weight: 800;

                color: #252938;

                line-height: 1.15;

            }

            .empresa-nombre {

                color: #747b8d;

                font-size: 0.92rem;

                margin-top: 0.2rem;

            }

            .empresa-estado {

                display: flex;

                align-items: center;

                gap: 0.35rem;

                border-radius: 999px;

                padding: 0.45rem 0.7rem;

                font-size: 0.8rem;

                font-weight: 700;

                white-space: nowrap;

            }

            .empresa-estado span {

                opacity: 0.68;

                font-weight: 600;

            }

            .estado-excelente {

                background: #e8f7ee;

                color: #176a3a;

            }

            .estado-bueno {

                background: #fff7dc;

                color: #806400;

            }

            .estado-atencion {

                background: #fff0df;

                color: #9a4e00;

            }

            .estado-critico {

                background: #fee9e9;

                color: #a12727;

            }

            .empresa-participacion {

                color: #6b7280;

                font-size: 0.86rem;

                padding-bottom: 0.9rem;

                margin-bottom: 0.9rem;

                border-bottom: 1px solid rgba(

                    48,

                    55,

                    75,

                    0.1

                );

            }

            .metricas-grid {

                display: grid;

                grid-template-columns:

                    repeat(3, minmax(0, 1fr));

                gap: 0.7rem;

            }

            .metrica-box {

                background: rgba(255, 255, 255, 0.82);

                border: 1px solid rgba(48, 55, 75, 0.09);

                border-radius: 14px;

                padding: 0.8rem;

                min-height: 92px;

            }

            .metrica-principal {

                grid-column: span 3;

                background: #f1f4fa;

            }

            .metrica-label {

                color: #72798b;

                font-size: 0.76rem;

                font-weight: 650;

                margin-bottom: 0.28rem;

            }

            .metrica-valor {

                color: #252938;
            
                font-size: 1.08rem;
            
                font-weight: 760;
            
                line-height: 1.25;
            
                white-space: nowrap;
            
                word-break: normal;
            
                overflow-wrap: normal;
            
                font-variant-numeric: tabular-nums;

            }

            .metrica-detalle {

                color: #858b99;

                font-size: 0.72rem;

                margin-top: 0.3rem;

            }

            .valor-positivo {

                color: #177245;

            }

            .valor-negativo {

                color: #b02a37;

            }

            .comparacion-card {

                background: #252938;

                color: white;

                border-radius: 18px;

                padding: 1rem 1.2rem;

                margin-top: 0.3rem;

                margin-bottom: 1rem;

                display: grid;

                grid-template-columns:

                    repeat(4, minmax(0, 1fr));

                gap: 1rem;

            }

            .comparacion-item-label {

                font-size: 0.76rem;

                opacity: 0.68;

                margin-bottom: 0.25rem;

            }

            .comparacion-item-valor {

                font-size: 1.1rem;

                font-weight: 750;

            }

            @media (max-width: 900px) {

                .metricas-grid {

                    grid-template-columns:

                        repeat(2, minmax(0, 1fr));

                }

                .metrica-principal {

                    grid-column: span 2;

                }

                .comparacion-card {

                    grid-template-columns:

                        repeat(2, minmax(0, 1fr));

                }

            }

            @media (max-width: 600px) {

                .metricas-grid {

                    grid-template-columns: 1fr;

                }

                .metrica-principal {

                    grid-column: span 1;

                }

                .empresa-header {

                    flex-direction: column;

                }

                .comparacion-card {

                    grid-template-columns: 1fr;

                }

            }

        </style>

        """).strip(),

    )

    # -----------------------------------------------------

    # RENDER

    # -----------------------------------------------------

    st.html(

        dedent("""

        <div class="empresas-section">

            <div class="empresas-title">

                🏥 Resumen Ejecutivo por Empresa

            </div>

            <div class="empresas-subtitle">

                Situación mensual de Vitae Medicina Reproductiva

                y Vitae Medical.

            </div>

        </div>

        """).strip(),

    )

    columna_vmr, columna_vm = st.columns(2)

    with columna_vmr:

        st.html(

            tarjeta_empresa(

                datos_vmr,

                participacion_vmr,

            ),



        )

    with columna_vm:

        st.html(

            tarjeta_empresa(

                datos_vm,

                participacion_vm,

            ),



        )

    clase_resultado_global = (

        "valor-positivo"

        if resultado_conjunto >= 0

        else "valor-negativo"

    )

    st.html(

        dedent(f"""

        <div class="comparacion-card">

            <div>

                <div class="comparacion-item-label">

                    Facturación conjunta

                </div>

                <div class="comparacion-item-valor">

                    {fmt_money(facturacion_conjunta)}

                </div>

            </div>

            <div>

                <div class="comparacion-item-label">

                    Resultado conjunto

                </div>

                <div class="comparacion-item-valor {clase_resultado_global}">

                    {fmt_money(resultado_conjunto)}

                </div>

            </div>

            <div>

                <div class="comparacion-item-label">

                    Pacientes del mes

                </div>

                <div class="comparacion-item-valor">

                    {pacientes_conjuntos}

                </div>

            </div>

            <div>

                <div class="comparacion-item-label">

                    Cobranza conjunta

                </div>

                <div class="comparacion-item-valor">

                    {cobranza_conjunta:.1f}%

                </div>

            </div>

        </div>

        """).strip(),

    )
# =========================================================

# INFORME AUTOMÁTICO INTEGRAL DE VITAE

# =========================================================

def render_briefing_automatico_vitae(

    dfs: dict[str, pd.DataFrame],

) -> None:

    """

    Muestra automáticamente el análisis integral

    de todos los módulos cargados en el Dashboard.

    """

    with st.spinner(

        "🧠 Analizando toda la información de Vitae..."

    ):

        briefing = generar_briefing_automatico(

            dfs=dfs,

        )

    st.markdown("## 🧠 Informe automático de Vitae")

    with st.container(border=True):

        encabezado, estado = st.columns(

            [3.5, 1.5]

        )

        with encabezado:

            st.markdown(

                f"### {briefing.get('saludo', 'Bienvenidos')}."

            )

            st.caption(

                "Diagnóstico automático de todos los módulos "

                "del sistema."

            )

        with estado:

            st.markdown(

                f"**{briefing.get('emoji_nivel', '⚪')} "

                f"{briefing.get('nivel', 'Información disponible')}**"

            )

        contenido = str(

            briefing.get(

                "contenido",

                "",

            )

        ).strip()

        if contenido:

            st.markdown(contenido)

        else:

            st.warning(

                "El análisis se ejecutó, pero no generó "

                "conclusiones visibles."

            )

        st.caption(

            "Actualizado: "

            f"{briefing.get('actualizado', '-')} · "

            f"{briefing.get('modulos_con_datos', 0)} módulos "

            "con datos de "

            f"{briefing.get('modulos_analizados', 0)} analizados."

        )

        nombres_modulos = briefing.get(

            "nombres_modulos",

            [],

        )

        if nombres_modulos:

            with st.expander(

                "🔍 Ver módulos incluidos en el análisis"

            ):

                for nombre_modulo in nombres_modulos:

                    st.markdown(

                        f"- {nombre_modulo}"

                    )
            errores_modulos = briefing.get(

            "modulos_con_error",

            [],

        )

        if errores_modulos:

            with st.expander(

                "🛠️ Ver errores técnicos detectados"

            ):

                for error_modulo in errores_modulos:

                    nombre_modulo = error_modulo.get(

                        "modulo",

                        "Módulo desconocido",

                    )

                    detalle_error = error_modulo.get(

                        "error",

                        "Sin detalle disponible",

                    )

                    st.markdown(

                        f"**{nombre_modulo}**"

                    )

                    st.code(

                        str(detalle_error),

                        language="text",

                    )
