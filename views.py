# =========================================================
# VISTAS
# =========================================================
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import plotly.express as px
import streamlit as st

from config import APP_TITLE
from modules import MODULES
from database import *
from helpers import *
from importers import render_importer


# =========================================================
# CACHE / CARGA ÚNICA DE DATOS
# =========================================================
@st.cache_data(ttl=300, show_spinner=False)
def load_all_data() -> Dict[str, pd.DataFrame]:
    """
    Carga cada tabla una sola vez por ejecución/cache.
    Evita llamadas repetidas a get_df() y reduce errores 429 en Google Sheets.
    """
    data: Dict[str, pd.DataFrame] = {}

    for cfg in MODULES.values():
        table = cfg["table"]
        if table in data:
            continue

        try:
            df = get_df(table)
            data[table] = add_balance_columns(df) if isinstance(df, pd.DataFrame) else pd.DataFrame()
        except Exception as exc:
            st.warning(f"No se pudo cargar la tabla {table}: {exc}")
            data[table] = pd.DataFrame()

    return data


def get_module_df(module_name: str, dfs: Optional[Dict[str, pd.DataFrame]] = None) -> pd.DataFrame:
    """Devuelve el DataFrame de un módulo usando la carga única."""
    cfg = MODULES.get(module_name)
    if not cfg:
        return pd.DataFrame()

    dfs = dfs if dfs is not None else build_module_dfs()
    return dfs.get(module_name, pd.DataFrame()).copy()


def build_module_dfs() -> Dict[str, pd.DataFrame]:
    """Mapea nombre de módulo -> DataFrame, sin repetir get_df()."""
    all_data = load_all_data()
    return {
        name: all_data.get(cfg["table"], pd.DataFrame()).copy()
        for name, cfg in MODULES.items()
    }


# =========================================================
# HELPERS INTERNOS
# =========================================================
def money_sum(df: pd.DataFrame, col: str) -> float:
    if df.empty or col not in df.columns:
        return 0.0
    return pd.to_numeric(df[col].apply(money), errors="coerce").fillna(0).sum()


def sum_money_col(series: pd.Series) -> float:
    return pd.to_numeric(series.apply(money), errors="coerce").fillna(0).sum()


def normalize_estado(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().str.strip()


def get_estado(df: pd.DataFrame) -> pd.Series:
    if "estado" in df.columns:
        return normalize_estado(df["estado"])
    return pd.Series([""] * len(df), index=df.index)


def get_fecha_series(df: pd.DataFrame) -> pd.Series:
    for col in ["fecha", "fecha_factura", "mes", "vencimiento", "created_at"]:
        if col in df.columns:
            return pd.to_datetime(df[col], errors="coerce")
    return pd.Series([pd.NaT] * len(df), index=df.index)


def first_existing_col(df: pd.DataFrame, cols: list[str]) -> Optional[str]:
    for col in cols:
        if col in df.columns:
            return col
    return None


def total_mod(nombre: str, dfs: Optional[Dict[str, pd.DataFrame]] = None) -> float:
    """Total inteligente por módulo."""
    df = get_module_df(nombre, dfs)
    if df.empty:
        return 0.0

    if "saldo" in df.columns:
        return money_sum(df, "saldo")
    if "saldo_movimiento" in df.columns:
        return money_sum(df, "saldo_movimiento")
    if "importe" in df.columns:
        return money_sum(df, "importe")
    if "valor_pesos" in df.columns:
        return money_sum(df, "valor_pesos")
    if "monto" in df.columns:
        return money_sum(df, "monto")
    if "ingreso" in df.columns or "egreso" in df.columns:
        return money_sum(df, "ingreso") - money_sum(df, "egreso")

    return 0.0


def deuda_mod(nombre: str, dfs: Optional[Dict[str, pd.DataFrame]] = None) -> float:
    """Calcula deuda / saldo pendiente de un módulo."""
    df = get_module_df(nombre, dfs)
    if df.empty:
        return 0.0

    if "tipo" in df.columns and "importe" in df.columns:
        tipo = normalize_estado(df["tipo"])
        pagado = df["pagado"].apply(money) if "pagado" in df.columns else 0
        saldo = df["importe"].apply(money) - pagado
        return float(saldo[tipo.eq("a pagar")].sum())

    col_monto = first_existing_col(df, ["saldo", "importe", "monto", "valor_pesos", "valor"])
    if not col_monto:
        return 0.0

    if "estado" not in df.columns:
        if col_monto == "importe" and "pagado" in df.columns:
            return max(0.0, money_sum(df, "importe") - money_sum(df, "pagado"))
        return money_sum(df, col_monto)

    estados_deuda = ["pendiente", "a pagar", "adeudado", "deuda", "vencido", "parcial"]
    estado = get_estado(df)
    deuda = df[estado.isin(estados_deuda)]

    if deuda.empty and col_monto == "importe" and "pagado" in df.columns:
        return max(0.0, money_sum(df, "importe") - money_sum(df, "pagado"))

    return money_sum(deuda, col_monto)


def is_closed_estado(df: pd.DataFrame) -> pd.Series:
    estados_cerrados = [
        "pagado", "cobrado", "completo", "realizado", "finalizada",
        "finalizado", "anulado", "cancelado"
    ]
    return get_estado(df).isin(estados_cerrados)


# =========================================================
# HEADER
# =========================================================
def render_header() -> None:
    col1, col2 = st.columns([6.5, 1.2])

    with col1:
        st.markdown(
            '<div class="main-title">🏥 Sistema de Gestión | VITAE </div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="subtitle">VMR · Vitae Medicina Reproductiva | VM · Vitae Medical</div>',
            unsafe_allow_html=True,
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
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.warning("Logo no encontrado")


# =========================================================
# LABELS FACTURACIÓN
# =========================================================
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


def get_fact_labels(module_name: str, cfg: Dict[str, Any]) -> Dict[str, str]:
    return DEFAULT_FACT_LABELS.copy()


def rename_fact_df(df: pd.DataFrame, labels: Dict[str, str]) -> pd.DataFrame:
    return df.rename(columns={c: labels.get(c, c.replace("_", " ").title()) for c in df.columns})


def format_facturacion_table(df: pd.DataFrame, labels: Dict[str, str]) -> pd.DataFrame:
    if df.empty:
        return df

    show = df.copy()
    show = show.drop(columns=["id", "created_at", "updated_at"], errors="ignore")

    for col in ["mes", "fecha", "fecha_factura", "vencimiento", "fecha_pago"]:
        if col in show.columns:
            show[col] = pd.to_datetime(show[col], errors="coerce").dt.strftime("%d/%m/%Y")
            show[col] = show[col].fillna("")

    for col in ["valor_pesos", "importe", "monto", "saldo", "ingreso", "egreso", "pagado"]:
        if col in show.columns:
            show[col] = show[col].apply(fmt_money)

    if "valor_usd" in show.columns:
        show["valor_usd"] = show["valor_usd"].apply(lambda x: f"USD {money(x):,.2f}")

    return rename_fact_df(show, labels)


# =========================================================
# DASHBOARD
# =========================================================
def calcular_metricas_globales(dfs: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    caja_bancos = 0.0
    ingresos_mes = 0.0
    egresos_mes = 0.0
    facturacion_mes = 0.0
    cobrado_mes = 0.0
    a_cobrar = 0.0
    a_pagar = 0.0
    deuda_total = 0.0
    vencidos = 0
    tareas_pend = 0
    pacientes_mes = 0
    medicos_activos: set[str] = set()

    hoy = pd.Timestamp.today().normalize()
    inicio_mes = hoy.to_period("M").to_timestamp()
    fin_mes = inicio_mes + pd.offsets.MonthEnd(0)

    for name, df in dfs.items():
        if df.empty:
            continue

        fechas = get_fecha_series(df)
        es_mes = fechas.notna() & (fechas >= inicio_mes) & (fechas <= fin_mes)
        estado = get_estado(df)

        if name in ["Caja VMR", "Caja VM", "Banco Macro VMR", "Banco Galicia VM"]:
            ingresos = money_sum(df, "ingreso")
            egresos = money_sum(df, "egreso")
            caja_bancos += ingresos - egresos
            if "ingreso" in df.columns:
                ingresos_mes += sum_money_col(df.loc[es_mes, "ingreso"])
            if "egreso" in df.columns:
                egresos_mes += sum_money_col(df.loc[es_mes, "egreso"])

        if name in ["Facturación VMR", "Facturación VM"] and "valor_pesos" in df.columns:
            total_facturado = money_sum(df, "valor_pesos")
            facturacion_mes += sum_money_col(df.loc[es_mes, "valor_pesos"])

            mask_cobrado = estado.isin(["completo", "cobrado", "pagado"])
            cobrado_total = sum_money_col(df.loc[mask_cobrado, "valor_pesos"])
            cobrado_mes += sum_money_col(df.loc[es_mes & mask_cobrado, "valor_pesos"])
            a_cobrar += max(0.0, total_facturado - cobrado_total)

            pacientes_mes += int(es_mes.sum())
            if "medico_responsable" in df.columns:
                medicos_activos.update(
                    df.loc[es_mes, "medico_responsable"]
                    .dropna()
                    .astype(str)
                    .str.strip()
                    .replace("", pd.NA)
                    .dropna()
                    .tolist()
                )

        if name in ["Cuenta Corriente VMR", "Cuenta Corriente VM"]:
            if "tipo" in df.columns and "importe" in df.columns:
                tipo = normalize_estado(df["tipo"])
                pagado = df["pagado"].apply(money) if "pagado" in df.columns else 0
                saldo = df["importe"].apply(money) - pagado
                a_cobrar += float(saldo[tipo.eq("a cobrar")].sum())
                a_pagar += float(saldo[tipo.eq("a pagar")].sum())

        if name in [
            "Deudas Impositivas VMR",
            "Deudas Impositivas VM",
            "Planes de pagos y préstamos",
            "Pagos pendientes Vitae",
            "Deuda total",
            "Honorarios médicos",
        ]:
            deuda_total += deuda_mod(name, dfs)

        if "vencimiento" in df.columns:
            venc = pd.to_datetime(df["vencimiento"], errors="coerce")
            vencidos += int((venc.notna() & (venc < hoy) & (~is_closed_estado(df))).sum())

        if name == "Tareas Pendientes" and "estado" in df.columns:
            tareas_pend += int((~estado.isin(["finalizada", "cancelada"])).sum())

    resultado_mes = ingresos_mes + cobrado_mes - egresos_mes
    promedio_facturacion = facturacion_mes / pacientes_mes if pacientes_mes > 0 else 0

    return {
        "caja_bancos": caja_bancos,
        "facturacion_mes": facturacion_mes,
        "cobrado_mes": cobrado_mes,
        "a_cobrar": a_cobrar,
        "resultado_mes": resultado_mes,
        "a_pagar": a_pagar,
        "deuda_total": deuda_total,
        "vencidos": vencidos,
        "tareas_pend": tareas_pend,
        "promedio_facturacion": promedio_facturacion,
        "medicos_activos": medicos_activos,
    }


def render_dashboard() -> None:
    render_header()
    st.markdown("### Resumen General")

    dfs = build_module_dfs()
    m = calcular_metricas_globales(dfs)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Liquidez actual", fmt_money(m["caja_bancos"]))
    c2.metric("Facturación mes", fmt_money(m["facturacion_mes"]))
    c3.metric("Cobrado mes", fmt_money(m["cobrado_mes"]))
    c4.metric("A cobrar", fmt_money(m["a_cobrar"]))
    c5.metric("Resultado mes", fmt_money(m["resultado_mes"]))

    c6, c7, c8, c9, c10 = st.columns(5)
    c6.metric("A pagar", fmt_money(m["a_pagar"]))
    c7.metric("Deuda total", fmt_money(m["deuda_total"]))
    c8.metric("Vencidos / críticos", m["vencidos"])
    c9.metric("Tareas pendientes", m["tareas_pend"])
    c10.metric("Promedio por paciente", fmt_money(m["promedio_facturacion"]))

    c11, c12 = st.columns(2)
    c11.metric("💸 Deuda Proveedores VMR", fmt_money(deuda_mod("Cuenta Corriente VMR", dfs)))
    c12.metric("💸 Deuda Proveedores VM", fmt_money(deuda_mod("Cuenta Corriente VM", dfs)))

    st.divider()
    render_resumen_empresa("Resumen VMR", "VMR", dfs)
    render_resumen_empresa("Resumen VM", "VM", dfs)
    render_analisis_global_vitae(dfs)
    render_grafico_modulos(dfs)


def render_grafico_modulos(dfs: Dict[str, pd.DataFrame]) -> None:
    rows_global = []

    for name, cfg in MODULES.items():
        df = dfs.get(name, pd.DataFrame())
        if df.empty:
            continue

        if "valor_pesos" in df.columns:
            total = money_sum(df, "valor_pesos")
        elif "importe" in df.columns:
            total = money_sum(df, "importe")
        elif "monto" in df.columns:
            total = money_sum(df, "monto")
        elif "saldo" in df.columns:
            total = money_sum(df, "saldo")
        else:
            total = 0.0

        if total > 0:
            rows_global.append({"Módulo": name, "Empresa": cfg.get("empresa", "VITAE"), "Total": total})

    resumen_global = pd.DataFrame(rows_global)
    if resumen_global.empty:
        return

    fig = px.bar(
        resumen_global,
        x="Módulo",
        y="Total",
        color="Empresa",
        title="Importes registrados por módulo",
    )
    fig.update_layout(xaxis_tickangle=-35)
    st.plotly_chart(fig, use_container_width=True, key="grafico_modulos_unico")


# =========================================================
# RESUMEN POR EMPRESA
# =========================================================
def render_resumen_empresa(titulo: str, empresa: str, dfs: Dict[str, pd.DataFrame]) -> None:
    mods = {
        name: dfs.get(name, pd.DataFrame())
        for name, cfg in MODULES.items()
        if cfg.get("empresa") == empresa
    }

    liquidez = 0.0
    facturacion = 0.0
    cobrado = 0.0
    a_cobrar = 0.0
    a_pagar_emp = 0.0
    deuda_emp = 0.0
    vencidos_emp = 0
    tareas_emp = 0
    pacientes = 0
    hoy = pd.Timestamp.today().normalize()

    for name, df in mods.items():
        if df.empty:
            continue

        tipo_modulo = MODULES[name].get("tipo", "")
        estado = get_estado(df)

        if tipo_modulo in ["caja", "banco"] or name in ["Caja VMR", "Caja VM", "Banco Macro VMR", "Banco Galicia VM"]:
            liquidez += money_sum(df, "ingreso") - money_sum(df, "egreso")

        if "valor_pesos" in df.columns:
            facturacion += money_sum(df, "valor_pesos")
            pacientes += len(df)
            cobrado += sum_money_col(df.loc[estado.isin(["completo", "cobrado", "pagado"]), "valor_pesos"])
            a_cobrar += sum_money_col(df.loc[estado.isin(["pendiente", "parcial", "vencido", "a cobrar"]), "valor_pesos"])

        if name in ["Cuenta Corriente VMR", "Cuenta Corriente VM"] and "tipo" in df.columns and "importe" in df.columns:
            tipo = normalize_estado(df["tipo"])
            pagado = df["pagado"].apply(money) if "pagado" in df.columns else 0
            saldo = df["importe"].apply(money) - pagado
            a_pagar_emp += float(saldo[tipo.eq("a pagar")].sum())
            a_cobrar += float(saldo[tipo.eq("a cobrar")].sum())

        if name in ["Deudas Impositivas VMR", "Deudas Impositivas VM", "Pagos pendientes Vitae", "Planes de pagos y préstamos", "Honorarios médicos", "Deuda total"]:
            deuda_emp += deuda_mod(name, dfs)
            a_pagar_emp += deuda_mod(name, dfs)

        if "monto" in df.columns and "estado" in df.columns:
            a_pagar_emp += sum_money_col(df.loc[estado.isin(["pendiente", "vencido", "a pagar"]), "monto"])

        if "vencimiento" in df.columns:
            venc = pd.to_datetime(df["vencimiento"], errors="coerce")
            vencidos_emp += int((venc.notna() & (venc < hoy) & (~is_closed_estado(df))).sum())

        if name == "Tareas Pendientes" and "estado" in df.columns:
            tareas_emp += int((~estado.isin(["finalizada", "cancelada"])).sum())

    resultado = cobrado - a_pagar_emp
    promedio = facturacion / pacientes if pacientes > 0 else 0

    st.divider()
    st.markdown(f"### {titulo}")

    r1, r2, r3, r4, r5 = st.columns(5)
    r1.metric("Liquidez actual", fmt_money(liquidez))
    r2.metric("Facturación", fmt_money(facturacion))
    r3.metric("Cobrado", fmt_money(cobrado))
    r4.metric("A cobrar", fmt_money(a_cobrar))
    r5.metric("Resultado", fmt_money(resultado))

    r6, r7, r8, r9, r10 = st.columns(5)
    r6.metric("A pagar", fmt_money(a_pagar_emp))
    r7.metric("Deuda total", fmt_money(deuda_emp))
    r8.metric("Vencidos / críticos", vencidos_emp)
    r9.metric("Tareas pendientes", tareas_emp)
    r10.metric("Promedio por paciente", fmt_money(promedio))


# =========================================================
# ANÁLISIS GLOBAL
# =========================================================
def render_analisis_global_vitae(dfs: Dict[str, pd.DataFrame]) -> None:
    anio_analisis = 2026
    st.divider()
    st.markdown(f"## 📊 Análisis Global VITAE {anio_analisis}")

    rows = []
    estados_cobrados = ["cobrado", "pagado", "realizado", "completo", "finalizado", "finalizada"]
    estados_pendientes = ["pendiente", "a cobrar", "adeudado", "deuda", "vencido", "parcial"]

    for module_name, cfg in MODULES.items():
        df = dfs.get(module_name, pd.DataFrame())
        if df.empty:
            continue

        empresa = cfg.get("empresa", "VITAE")

        for _, row in df.iterrows():
            fecha = row.get("fecha") or row.get("fecha_factura") or row.get("mes") or row.get("vencimiento") or row.get("created_at")
            fecha = pd.to_datetime(fecha, errors="coerce")
            if pd.isna(fecha) or fecha.year != anio_analisis:
                continue

            ingreso = money(row.get("ingreso", 0))
            egreso = money(row.get("egreso", 0))
            valor = (
                money(row.get("valor_pesos", 0))
                or money(row.get("importe", 0))
                or money(row.get("monto", 0))
                or money(row.get("saldo", 0))
                or money(row.get("valor", 0))
            )
            estado = str(row.get("estado", "")).lower().strip()

            facturado = valor if valor else ingreso
            cobrado = valor if estado in estados_cobrados else ingreso
            pendiente = valor if estado in estados_pendientes else 0.0

            rows.append(
                {
                    "Fecha": fecha,
                    "Mes": fecha.strftime("%Y-%m"),
                    "Empresa": empresa,
                    "Módulo": module_name,
                    "Facturado": facturado,
                    "Cobrado": cobrado,
                    "Pendiente": pendiente,
                    "Egreso": egreso,
                    "Resultado": cobrado - egreso,
                }
            )

    if not rows:
        st.info("No hay datos de 2026 para analizar.")
        return

    global_df = pd.DataFrame(rows)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("💰 Facturado", fmt_money(global_df["Facturado"].sum()))
    c2.metric("✅ Cobrado", fmt_money(global_df["Cobrado"].sum()))
    c3.metric("⏳ Pendiente", fmt_money(global_df["Pendiente"].sum()))
    c4.metric("📤 Egresos", fmt_money(global_df["Egreso"].sum()))
    c5.metric("📈 Resultado", fmt_money(global_df["Resultado"].sum()))

    mensual = global_df.groupby("Mes", as_index=False)[["Facturado", "Cobrado", "Pendiente", "Egreso", "Resultado"]].sum()

    st.markdown("### 📅 Resumen mensual 2026")
    fig = px.bar(
        mensual,
        x="Mes",
        y=["Facturado", "Cobrado", "Pendiente", "Egreso"],
        barmode="group",
        title="Movimientos mensuales",
    )
    fig.update_layout(height=450)
    st.plotly_chart(fig, use_container_width=True, key="global_mensual_2026")

    mensual["Acumulado"] = mensual["Resultado"].cumsum()
    st.markdown("### 📈 Evolución acumulada")
    fig2 = px.line(mensual, x="Mes", y="Acumulado", markers=True, title="Resultado acumulado 2026")
    fig2.update_layout(height=400)
    st.plotly_chart(fig2, use_container_width=True, key="global_acumulado_2026")

    resumen_modulos = global_df.groupby(["Módulo", "Empresa"], as_index=False)[
        ["Facturado", "Cobrado", "Pendiente", "Egreso", "Resultado"]
    ].sum()

    st.markdown("### 📋 Resumen por módulo")
    st.dataframe(
        resumen_modulos.sort_values("Facturado", ascending=False),
        use_container_width=True,
        hide_index=True,
    )


# =========================================================
# ANÁLISIS MENSUAL 2026
# =========================================================
def render_analisis_mensual_2026(df: pd.DataFrame) -> None:
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

    monto_col = first_existing_col(data, ["valor_pesos", "importe", "monto", "facturado", "total"])
    if not monto_col:
        st.warning("No encontré columna de monto para calcular facturación.")
        return

    data[monto_col] = data[monto_col].apply(money)
    data["mes_nombre"] = data["mes"].dt.strftime("%Y-%m")

    mensual = data.groupby("mes_nombre", as_index=False)[monto_col].sum().rename(columns={monto_col: "facturacion"})
    if mensual.empty:
        return

    acumulado = mensual["facturacion"].sum()
    promedio = mensual["facturacion"].mean()
    mejor_mes = mensual.loc[mensual["facturacion"].idxmax()]
    proyeccion = promedio * 12

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Facturación 2026", fmt_money(acumulado))
    col2.metric("Promedio mensual", fmt_money(promedio))
    col3.metric("Mejor mes", mejor_mes["mes_nombre"])
    col4.metric("Proyección anual", fmt_money(proyeccion))

    fig = px.bar(mensual, x="mes_nombre", y="facturacion", title="Facturación mensual 2026", text_auto=".2s")
    fig.update_layout(xaxis_title="Mes", yaxis_title="Facturación", height=420)
    st.plotly_chart(fig, use_container_width=True, key="facturacion_mensual_2026")

    mensual["acumulado"] = mensual["facturacion"].cumsum()
    fig2 = px.line(mensual, x="mes_nombre", y="acumulado", markers=True, title="Evolución acumulada 2026")
    fig2.update_layout(xaxis_title="Mes", yaxis_title="Acumulado", height=380)
    st.plotly_chart(fig2, use_container_width=True, key="facturacion_acumulada_2026")


# =========================================================
# VISTA PRO DE MÓDULO
# =========================================================
def render_facturacion_pro(module_name: str, cfg: Dict[str, Any]) -> None:
    table = cfg["table"]
    render_header()
    st.header(module_name)
    st.caption(cfg.get("descripcion", ""))

    labels = get_fact_labels(module_name, cfg)

    tab_panel, tab_cargar, tab_importar, tab_editar, tab_columnas, tab_exportar = st.tabs(
        ["📊 Panel PRO", "➕ Cargar", "📥 Importar", "✏️ Editar tabla", "🏷️ Editar columnas", "📤 Exportar"]
    )

    with tab_panel:
        df = load_all_data().get(table, pd.DataFrame()).copy()
        filtered = df.copy()

        if df.empty:
            st.warning("No hay registros cargados.")
        else:
            filtered = apply_filters(df, module_name)
            cols_monto = [c for c in ["valor_pesos", "importe", "monto", "saldo"] if c in filtered.columns]
            total_facturado = sum(money_sum(filtered, c) for c in cols_monto)

            mask_cobrado = pd.Series([False] * len(filtered), index=filtered.index)
            if "estado" in filtered.columns:
                mask_cobrado = get_estado(filtered).isin(["cobrado", "pagado", "completo", "realizado", "finalizado", "finalizada"])

            cobrado = sum(sum_money_col(filtered.loc[mask_cobrado, c]) for c in cols_monto)
            pendiente = max(0.0, total_facturado - cobrado)
            pacientes = len(filtered)
            ticket_promedio = total_facturado / pacientes if pacientes > 0 else 0

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("💰 Facturado", fmt_money(total_facturado))
            if module_name in ["Cuenta Corriente VMR", "Cuenta Corriente VM"]:
                c2.metric("💸 Pagado", fmt_money(cobrado))
                c3.metric("💰 A pagar", fmt_money(pendiente))
                c4.metric("📊 Promedio", fmt_money(ticket_promedio))
            else:
                c2.metric("✅ Cobrado", fmt_money(cobrado))
                c3.metric("⏳ Pendiente", fmt_money(pendiente))
                c4.metric("👥 Registros", pacientes)

            render_analisis_mensual_2026(filtered)

        st.divider()

        if not filtered.empty:
            if "mes" in filtered.columns:
                filtered = filtered.sort_values(by="mes", ascending=False, na_position="last")

            columnas_a_mostrar = get_field_names(cfg)
            visible_cols = [c for c in columnas_a_mostrar if c in filtered.columns]

            table_view = filtered.copy()
            for col_fecha in ["mes", "fecha", "fecha_factura", "vencimiento", "fecha_pago"]:
                if col_fecha in table_view.columns:
                    table_view[col_fecha] = pd.to_datetime(table_view[col_fecha], errors="coerce").dt.strftime("%d/%m/%Y")

            st.dataframe(
                table_view[visible_cols] if visible_cols else table_view,
                use_container_width=True,
                hide_index=True,
            )

        render_graficos_facturacion(filtered)

    with tab_cargar:
        render_form_cargar(table, cfg)

    with tab_importar:
        render_importer(module_name, cfg)

    with tab_editar:
        render_editor_tabla(table)

    with tab_columnas:
        st.info("Editor de columnas reservado para próxima etapa.")

    with tab_exportar:
        render_exportar(table, labels)


def render_graficos_facturacion(filtered: pd.DataFrame) -> None:
    if filtered.empty:
        return

    st.divider()
    st.markdown("### Gráficos útiles")

    g1, g2 = st.columns(2)

    if "fecha_factura" in filtered.columns and "valor_pesos" in filtered.columns:
        graph = filtered.copy()
        graph["fecha_factura"] = pd.to_datetime(graph["fecha_factura"], errors="coerce")
        graph = graph[graph["fecha_factura"].notna()]
        if not graph.empty:
            graph["Mes"] = graph["fecha_factura"].dt.to_period("M").astype(str)
            chart = graph.groupby("Mes", as_index=False)["valor_pesos"].sum()
            fig = px.bar(chart, x="Mes", y="valor_pesos", title="Facturación por mes")
            g1.plotly_chart(fig, use_container_width=True, key="chart_facturacion_mes")

    if "obra_social" in filtered.columns and "valor_pesos" in filtered.columns:
        chart = filtered.groupby("obra_social")["valor_pesos"].apply(lambda x: x.apply(money).sum()).reset_index()
        chart = chart.sort_values("valor_pesos", ascending=False).head(10)
        fig = px.bar(chart, x="obra_social", y="valor_pesos", title="Facturación por obra social")
        g2.plotly_chart(fig, use_container_width=True, key="chart_facturacion_obra_social")

    g3, g4 = st.columns(2)

    if "medico_responsable" in filtered.columns and "valor_pesos" in filtered.columns:
        chart = filtered.groupby("medico_responsable")["valor_pesos"].apply(lambda x: x.apply(money).sum()).reset_index()
        chart = chart.sort_values("valor_pesos", ascending=False).head(10)
        fig = px.bar(chart, x="medico_responsable", y="valor_pesos", title="Facturación por médico")
        g3.plotly_chart(fig, use_container_width=True, key="chart_facturacion_medico")

    if "procedimiento" in filtered.columns and "valor_pesos" in filtered.columns:
        chart = filtered.groupby("procedimiento")["valor_pesos"].apply(lambda x: x.apply(money).sum()).reset_index()
        chart = chart.sort_values("valor_pesos", ascending=False).head(10)
        fig = px.bar(chart, x="procedimiento", y="valor_pesos", title="Facturación por procedimiento")
        g4.plotly_chart(fig, use_container_width=True, key="chart_facturacion_procedimiento")


def render_form_cargar(table: str, cfg: Dict[str, Any]) -> None:
    st.subheader("Nuevo registro")

    with st.form(f"form_add_{table}", clear_on_submit=False):
        data: Dict[str, Any] = {}
        cols = st.columns(2)

        for i, field in enumerate(cfg["fields"]):
            with cols[i % 2]:
                raw = input_field(field, f"add_{table}")
                data[field[0]] = clean_for_db(raw, field[1])

        submitted = st.form_submit_button("Guardar registro", type="primary")

    if not submitted:
        return

    errors = validate_required(cfg, data)
    if errors:
        st.error("Faltan completar campos obligatorios: " + ", ".join(errors))
        return

    try:
        df_actual = load_all_data().get(table, pd.DataFrame()).copy()
        duplicado = False

        if not df_actual.empty:
            cols_check = [c for c in data.keys() if c in df_actual.columns]
            if cols_check:
                left = df_actual[cols_check].fillna("").astype(str)
                right = pd.Series(data)[cols_check].fillna("").astype(str)
                duplicado = (left == right).all(axis=1).any()

        if duplicado:
            st.warning("Este registro ya existe. No se volvió a cargar.")
        else:
            insert_row(table, data)
            st.success("Registro guardado correctamente.")
            st.cache_data.clear()
            st.rerun()

    except Exception as e:
        st.error("Error al guardar el registro")
        st.exception(e)


def render_editor_tabla(table: str) -> None:
    st.subheader("Editar registros cargados")

    df = load_all_data().get(table, pd.DataFrame()).copy()
    if df.empty:
        st.warning("No hay registros para editar.")
        return

    df_edit = df.drop(columns=["created_at", "updated_at"], errors="ignore")

    if "mes" in df_edit.columns:
        df_edit["mes"] = pd.to_datetime(df_edit["mes"], errors="coerce")
        df_edit = df_edit.sort_values(by="mes", ascending=False)
        df_edit["mes"] = df_edit["mes"].dt.strftime("%Y-%m-%d")

    if "estado" in df_edit.columns:
        estados = sorted(df_edit["estado"].dropna().astype(str).unique().tolist())
        estado_editor = st.selectbox("Filtrar por estado", ["Todos"] + estados, key=f"estado_editor_{table}")
        if estado_editor != "Todos":
            df_edit = df_edit[df_edit["estado"].astype(str) == estado_editor]

    edited_df = st.data_editor(
        df_edit,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        disabled=["id"] if "id" in df_edit.columns else None,
        key=f"editor_{table}",
    )

    col1, col2 = st.columns(2)
    with col1:
        guardar = st.button("Guardar cambios", type="primary", key=f"guardar_editor_{table}")
    with col2:
        st.warning("Si borrás filas en la tabla y guardás, se eliminan de la base.")

    if guardar:
        try:
            replace_table_rows(table, edited_df.to_dict("records"))
            st.success("Cambios guardados correctamente.")
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error("ERROR AL GUARDAR")
            st.exception(e)


def render_exportar(table: str, labels: Dict[str, str]) -> None:
    df = load_all_data().get(table, pd.DataFrame()).copy()

    if df.empty:
        st.info("No hay datos para exportar.")
        return

    export_df = format_facturacion_table(df, labels)
    csv = export_df.to_csv(index=False).encode("utf-8-sig")

    st.download_button(
        "Descargar CSV",
        data=csv,
        file_name=f"{table}.csv",
        mime="text/csv",
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


# =========================================================
# CONFIGURACIÓN
# =========================================================
def render_configuracion() -> None:
    render_header()
    st.header("Configuración")

    tab1, tab2, tab3, tab4 = st.tabs(["👤 Usuarios", "🔐 Permisos", "🏢 Empresas", "⚙️ Sistema"])

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
        modulo_borrar = st.selectbox("Módulo a borrar", list(MODULES.keys()), key="modulo_borrar_db")
        confirmar = st.checkbox(f"Confirmo borrar todos los datos de {modulo_borrar}", key="confirmar_borrar_db")

        if st.button("Borrar módulo seleccionado", type="secondary", disabled=not confirmar):
            try:
                replace_table_rows(MODULES[modulo_borrar]["table"], [])
                st.cache_data.clear()
                st.success(f"Datos de {modulo_borrar} borrados correctamente.")
                st.rerun()
            except Exception as e:
                st.error("No se pudo borrar el módulo.")
                st.exception(e)

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
                    st.cache_data.clear()
                    st.success("Datos restaurados desde Google Sheets.")
                    st.rerun()
                except Exception as e:
                    st.error(f"No se pudo leer desde Google Sheets: {e}")


# =========================================================
# DATOS DE EJEMPLO
# =========================================================
def seed_examples() -> None:
    examples = [
        (
            "caja_vmr",
            {
                "fecha": date.today().strftime(DATE_FMT),
                "concepto": "Ingreso muestra fertilidad",
                "categoria": "Ingreso",
                "medio": "Efectivo",
                "ingreso": 150000,
                "egreso": 0,
                "responsable": "Administración",
                "observaciones": "Ejemplo",
            },
        ),
        (
            "banco_galicia_vm",
            {
                "fecha": date.today().strftime(DATE_FMT),
                "concepto": "Pago proveedor quirófano",
                "tipo_movimiento": "Débito",
                "referencia": "OP-001",
                "ingreso": 0,
                "egreso": 80000,
                "conciliado": 1,
                "observaciones": "Ejemplo",
            },
        ),
        (
            "pagos_pendientes_vitae",
            {
                "fecha": date.today().strftime(DATE_FMT),
                "empresa": "VITAE",
                "proveedor": "Proveedor insumos",
                "concepto": "Insumos médicos",
                "importe": 120000,
                "pagado": 0,
                "vencimiento": (date.today() + timedelta(days=7)).strftime(DATE_FMT),
                "prioridad": "Alta",
                "estado": "Pendiente",
                "observaciones": "Ejemplo",
            },
        ),
        (
            "tareas_pendientes",
            {
                "fecha": date.today().strftime(DATE_FMT),
                "empresa": "VM",
                "tarea": "Revisar stock quirófano",
                "responsable": "Enfermería",
                "prioridad": "Alta",
                "vencimiento": (date.today() + timedelta(days=3)).strftime(DATE_FMT),
                "estado": "Pendiente",
                "observaciones": "Ejemplo",
            },
        ),
    ]

    for table, data in examples:
        insert_row(table, data)

    st.cache_data.clear()
