# =========================================================
# IMPORTADOR EXCEL / CSV
# =========================================================
import pandas as pd
import streamlit as st
from helpers import show_business_table
from typing import Any, Dict, List, Tuple
from helpers import parse_date
from database import *
from modules import MODULES
import numpy as np
def clean_tabular_sheet(df_raw: pd.DataFrame) -> pd.DataFrame:
    if df_raw.empty:
        return df_raw
    raw = df_raw.copy().dropna(how="all").dropna(axis=1, how="all")
    if raw.empty:
        return raw

    header_keywords = {
        "mes", "afiliado", "obra social", "procedimiento", "medico", "médico",
        "fecha factura", "factura", "vencimiento", "fecha pago", "valor", "estado",
        "cliente", "paciente", "importe", "concepto", "comprobante"
    }

    best_idx = raw.index[0]
    best_score = -1.0
    for idx, row in raw.iterrows():
        values = [str(x).strip().lower() for x in row.tolist() if pd.notna(x) and str(x).strip() != ""]
        if not values:
            continue
        joined = " | ".join(values)
        score = sum(1 for kw in header_keywords if kw in joined) + min(len(values), 10) * 0.05
        if score > best_score:
            best_score = score
            best_idx = idx

    header_values = raw.loc[best_idx].tolist()
    columns: List[str] = []
    used: Dict[str, int] = {}
    for i, value in enumerate(header_values):
        name = str(value).strip() if pd.notna(value) and str(value).strip() else f"Columna_{i + 1}"
        name = name.replace("\n", " ").replace("  ", " ").strip()
        if name in used:
            used[name] += 1
            name = f"{name}_{used[name]}"
        else:
            used[name] = 1
        columns.append(name)

    cleaned = raw.loc[raw.index > best_idx].copy()
    cleaned.columns = columns
    cleaned = cleaned.dropna(how="all")
    cleaned = cleaned.loc[:, [not str(c).lower().startswith("columna_") or not cleaned[c].isna().all() for c in cleaned.columns]]
    return cleaned.reset_index(drop=True)
def read_uploaded_sheet(uploaded_file: Any) -> Dict[str, pd.DataFrame]:
    filename = uploaded_file.name.lower()
    if filename.endswith(".csv"):
        try:
            raw = pd.read_csv(uploaded_file, sep=None, engine="python", header=None)
        except Exception:
            uploaded_file.seek(0)
            raw = pd.read_csv(uploaded_file, header=None)
        return {"CSV": clean_tabular_sheet(raw)}
    raw_sheets = pd.read_excel(uploaded_file, sheet_name=None, header=None)
    return {name: clean_tabular_sheet(df) for name, df in raw_sheets.items()}
def field_label(field: Tuple) -> str:
    name, _ftype, required = field[0], field[1], field[2]
    return f"{name.replace('_', ' ').title()}{' *' if required else ''}"
def auto_guess_column(target_name: str, source_columns: List[str]) -> str:
    norm_target = target_name.lower().replace("_", " ")
    aliases = {
        "fecha": ["fecha", "dia", "día", "date"],
        "mes": ["mes", "periodo", "período"],
        "afiliado": ["afiliado", "paciente", "cliente", "nombre", "apellido y nombre"],
        "obra_social": ["obra social", "os", "prepaga"],
        "procedimiento": ["procedimiento", "practica", "práctica", "prestacion", "prestación"],
        "medico_responsable": ["medico responsable", "médico responsable", "medico", "médico", "doctor", "profesional", "responsable"],
        "fecha_factura": ["fecha factura", "fecha de factura", "fecha", "factura fecha"],
        "numero_factura": ["n° factura", "nº factura", "n factura", "numero factura", "número factura", "factura", "comprobante"],
        "fecha_pago": ["fecha pago", "fecha de pago", "pago fecha"],
        "valor_pesos": ["valor $", "valor pesos", "valor ars", "importe", "monto", "total", "valor"],
        "valor_usd": ["valor usd", "usd", "dolares", "dólares"],
        "cliente": ["cliente", "paciente", "nombre", "razon social", "razón social"],
        "concepto": ["concepto", "detalle", "descripcion", "descripción", "movimiento", "observacion"],
        "detalle": ["detalle", "concepto", "descripcion", "descripción"],
        "persona_entidad": ["persona", "entidad", "cliente", "proveedor", "paciente", "nombre"],
        "proveedor": ["proveedor", "acreedor", "contraparte", "entidad"],
        "acreedor": ["acreedor", "proveedor", "banco", "entidad"],
        "contraparte": ["contraparte", "proveedor", "profesional", "locador"],
        "medico": ["medico", "médico", "doctor", "profesional"],
        "importe": ["importe", "monto", "total", "valor", "debe", "saldo"],
        "importe_total": ["importe total", "total", "monto", "importe"],
        "importe_original": ["importe original", "deuda", "total", "importe", "monto"],
        "valor": ["valor", "importe", "monto", "total"],
        "valor_mensual": ["valor mensual", "alquiler", "importe", "monto", "total"],
        "ingreso": ["ingreso", "entradas", "haber", "credito", "crédito", "cobro"],
        "egreso": ["egreso", "salidas", "debe", "debito", "débito", "pago"],
        "pagado": ["pagado", "pago", "abonado", "cancelado"],
        "cobrado": ["cobrado", "cobro", "pagado", "abonado"],
        "saldo": ["saldo", "pendiente", "resta", "deuda"],
        "estado": ["estado", "situacion", "situación", "status"],
        "vencimiento": ["vencimiento", "vence", "fecha vencimiento"],
        "proximo_vencimiento": ["proximo vencimiento", "próximo vencimiento", "vencimiento", "vence"],
        "observaciones": ["observaciones", "observacion", "obs", "nota", "comentario"],
        "responsable": ["responsable", "usuario", "encargado"],
        "dni": ["dni", "documento"],
        "telefono": ["telefono", "teléfono", "celular", "whatsapp"],
        "practica": ["practica", "práctica", "prestacion", "prestación", "procedimiento"],
        "periodo": ["periodo", "período", "mes"],
        "comprobante": ["comprobante", "factura", "n factura", "n° factura", "nº factura"],
    }
    candidates = aliases.get(target_name, [norm_target])
    normalized_sources = {str(col).lower().replace("_", " ").strip(): col for col in source_columns}
    for cand in candidates:
        cand = cand.lower().strip()
        if cand in normalized_sources:
            return normalized_sources[cand]
    for cand in candidates:
        cand = cand.lower().strip()
        for src_norm, original in normalized_sources.items():
            if cand in src_norm or src_norm in cand:
                return original
    return "No usar"
def normalize_select_value(value: Any, options: List[str]) -> str:
    if value is None:
        return options[0] if options else ""
    try:
        if pd.isna(value):
            return options[0] if options else ""
    except Exception:
        pass
    text = str(value).strip()
    if text == "":
        return options[0] if options else ""
    for opt in options:
        if text.lower() == opt.lower():
            return opt
    aliases = {
        "cobrado": "Cobrado", "pagado": "Pagado", "pendiente": "Pendiente", "vencido": "Vencido",
        "parcial": "Parcial", "completo": "Completo", "completa": "Completo",
        "realizado": "Realizado", "finalizada": "Finalizada", "finalizado": "Finalizado",
        "alta": "Alta", "media": "Media", "baja": "Baja",
        "credito": "Crédito", "crédito": "Crédito", "debito": "Débito", "débito": "Débito",
    }
    wanted = aliases.get(text.lower())
    if wanted and wanted in options:
        return wanted
    return options[0] if options else text
def clean_import_value(value: Any, field: Tuple) -> Any:
    _name, ftype = field[0], field[1]
    options = field[3] if len(field) > 3 else None
    if ftype == "date":
        parsed = parse_date(value)
        return parsed.strftime(DATE_FMT) if parsed else ""
    if ftype in {"money", "number"}:
        num = pd.to_numeric(normalize_money_string(value), errors="coerce")
        return 0.0 if pd.isna(num) else float(num)
    if ftype == "int":
        num = pd.to_numeric(normalize_money_string(value), errors="coerce")
        return 0 if pd.isna(num) else int(num)
    if ftype == "bool":
        if value is None:
            return 0
        try:
            if pd.isna(value):
                return 0
        except Exception:
            pass
        return 1 if str(value).strip().lower() in ["1", "true", "si", "sí", "x", "ok", "pagado", "conciliado"] else 0
    if ftype == "select":
        return normalize_select_value(value, options or [])
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()
def render_importer(module_name: str, cfg: Dict[str, Any]) -> None:
    table = cfg["table"]
    st.subheader("Importar planilla Excel / CSV")
    st.caption("Subí una planilla, elegí la hoja, mapeá columnas y guardala dentro de este módulo.")
    uploaded_file = st.file_uploader("Subir archivo", type=["xlsx", "xls", "csv"], key=f"upload_{table}")
    if uploaded_file is None:
        st.info("Acepta Excel con varias hojas o CSV.")
        return
    try:
        sheets = read_uploaded_sheet(uploaded_file)
    except Exception as e:
        st.error(f"No pude leer el archivo. Detalle: {e}")
        return
    sheet_names = list(sheets.keys())
    selected_sheet = st.selectbox("Hoja a importar", sheet_names, key=f"sheet_{table}")
    df_original = sheets[selected_sheet].copy().dropna(how="all")
    df_original.columns = [str(c).strip() for c in df_original.columns]
    if df_original.empty:
        st.warning("La hoja seleccionada está vacía.")
        return
    st.markdown("#### Vista previa")
    show_business_table(
        df_original,
        height=700,
    )
    columnas = df_original.columns.tolist()
    st.markdown("#### Mapeo de columnas")
    mapping: Dict[str, str] = {}
    cols = st.columns(2)
    for i, field in enumerate(cfg["fields"]):
        name = field[0]
        guessed = auto_guess_column(name, columnas)
        options = ["No usar"] + columnas
        index = options.index(guessed) if guessed in options else 0
        with cols[i % 2]:
            mapping[name] = st.selectbox(field_label(field), options, index=index, key=f"map_{table}_{name}")
    with st.expander("Opciones avanzadas"):
        modo = st.radio("Modo de importación", ["Agregar a registros existentes", "Reemplazar módulo completo"], key=f"modo_import_{table}")
        saltar_filas_vacias = st.checkbox("Saltar filas completamente vacías", value=True, key=f"skip_empty_{table}")
        validar_obligatorios = st.checkbox("Validar campos obligatorios", value=False, key=f"valid_required_{table}")
    rows: List[Dict[str, Any]] = []
    rejected_rows: List[Dict[str, Any]] = []
    for idx, source_row in df_original.iterrows():
        if saltar_filas_vacias and source_row.isna().all():
            continue
        new_row: Dict[str, Any] = {}
        for field in cfg["fields"]:
            name = field[0]
            mapped_col = mapping.get(name, "No usar")
            if mapped_col == "No usar":
                new_row[name] = clean_for_db(default_value(field[1], field[3] if len(field) > 3 else None), field[1])
                if field[1] == "date" and not field[2]:
                    new_row[name] = ""
            else:
                new_row[name] = clean_import_value(source_row.get(mapped_col), field)
        errors = validate_required(cfg, new_row) if validar_obligatorios else []
        if errors:
            rejected_rows.append({"fila_excel": idx + 2, "motivo": ", ".join(errors), **new_row})
        else:
            rows.append(new_row)
    st.markdown("#### Previsualización final")
    preview_df = pd.DataFrame(rows)
    if preview_df.empty:
        st.warning("No hay filas válidas para importar con el mapeo actual.")
    else:
        show_business_table(preview_df.head(50))
        st.success(f"Filas listas para importar: {len(rows)}")
    if rejected_rows:
        with st.expander(f"Filas rechazadas: {len(rejected_rows)}"):
            show_business_table(pd.DataFrame(rejected_rows))
    col_a, col_b = st.columns([1, 2])
    with col_a:
        confirm_import = st.checkbox("Confirmo la importación", key=f"confirm_import_{table}")
    with col_b:
        st.caption("Si reemplazás el módulo completo, se borran los registros anteriores de este módulo.")
    if st.button("Importar planilla al módulo", type="primary", disabled=(not confirm_import or not rows), key=f"btn_import_{table}"):
        count = replace_table_rows(table, rows) if modo == "Reemplazar módulo completo" else bulk_insert_rows(table, rows)
        st.success(f"Importación completada. Registros importados en {module_name}: {count}")
        st.rerun()
