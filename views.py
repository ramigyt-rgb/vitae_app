# =========================================================
# VISTAS
# ========================================================
import pandas as pd
import plotly.express as px
import streamlit as st
from pathlib import Path
from datetime import date, timedelta
from typing import Any, Dict
from config import APP_TITLE
from modules import MODULES
from database import *
from helpers import *
from importers import render_importer
from assistant import preguntar_ia
from assistant import preguntar_dashboard
from director_ia import (
    generar_resumen_ejecutivo,
    generar_briefing_automatico,
)
from textwrap import dedent
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
        if tabla not in data:
            data[tabla] = add_balance_columns(get_df(tabla))
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
def render_dashboard() -> None:
    render_header()
    
    all_data = {}
    for name, cfg in MODULES.items():
        table = cfg["table"]
        if table not in all_data:
            try:
                all_data[table] = add_balance_columns(get_df(table))
            except Exception:
                all_data[table] = pd.DataFrame()
    dfs = {
        name: all_data[cfg["table"]]
        for name, cfg in MODULES.items()
    }
    render_briefing_automatico_vitae(dfs)
    st.divider()
    render_resumen_empresas_pro(dfs)
    st.divider()

    st.subheader("🤖 Director IA de Vitae")
    
    pregunta = st.text_input(
    
        "Preguntale al Director IA sobre todo el sistema",
    
        key="dashboard_ia"
    
    )
    
    if st.button("Consultar", key="consultar_dashboard"):
    
        if pregunta.strip():
    
            with st.spinner("Analizando todo Vitae..."):
    
                respuesta = preguntar_dashboard(
    
                    dfs,
    
                    pregunta
    
                )
    
            st.success(respuesta)
    def total_mod(nombre):
        df = dfs.get(nombre, pd.DataFrame())
        if df.empty:
            return 0.0
        if "saldo" in df.columns:
            return df["saldo"].apply(money).sum()
        if "saldo_movimiento" in df.columns:
            return df["saldo_movimiento"].apply(money).sum()
        if "importe" in df.columns:
            return df["importe"].apply(money).sum()
        if "valor_pesos" in df.columns:
            return df["valor_pesos"].apply(money).sum()
        if "monto" in df.columns:
            return df["monto"].apply(money).sum()
        return 0.0
    caja_vmr = total_mod("Caja VMR")
    banco_vmr = total_mod("Banco Macro VMR")
    caja_vm = total_mod("Caja VM")
    banco_vm = total_mod("Banco Galicia VM")
    gine_vitae = total_mod("Gine Vitae")
    pagos_pendientes = total_mod("Pagos pendientes Vitae")
    planes_pago = total_mod("Planes de pagos y préstamos")
    honorarios = total_mod("Honorarios médicos")
    deuda_imp_vmr = total_mod("Deudas Impositivas VMR")
    deuda_imp_vm = total_mod("Deudas Impositivas VM")
    liquidez_total = caja_vmr + banco_vmr + caja_vm + banco_vm + gine_vitae
    deuda_total_global = pagos_pendientes + planes_pago + honorarios + deuda_imp_vmr + deuda_imp_vm
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
    medicos_activos = set()
    hoy = pd.Timestamp.today().normalize()
    inicio_mes = hoy.replace(day=1)
    fin_mes = inicio_mes + pd.offsets.MonthEnd(0)
    estados_cerrados = ["pagado", "cobrado", "completo", "realizado", "finalizada", "finalizado", "anulado", "cancelado"]
    for name, df in dfs.items():
        if df.empty:
            continue
        if name in ["Facturación VMR", "Facturación VM"] and "mes" in df.columns:
            fechas = pd.to_datetime(
                safe_col(df, "mes"),
                format="%Y-%m-%d",
                errors="coerce"
            )
        elif "fecha" in df.columns:
            fechas = pd.to_datetime(df["fecha"], errors="coerce")
        elif "fecha_factura" in df.columns:
            fechas = pd.to_datetime(df["fecha_factura"], errors="coerce")
        else:
            fechas = pd.Series([pd.NaT] * len(df), index=df.index)
        es_mes = fechas.notna() & (fechas >= inicio_mes) & (fechas <= fin_mes)
        if name in ["Caja VMR", "Caja VM", "Banco Macro VMR", "Banco Galicia VM"]:
            ingresos = df["ingreso"].apply(money).sum() if "ingreso" in df.columns else 0
            egresos = df["egreso"].apply(money).sum() if "egreso" in df.columns else 0
            caja_bancos += ingresos - egresos
            if "ingreso" in df.columns:
                ingresos_mes += sum_money_col(df.loc[es_mes, "ingreso"])
            if "egreso" in df.columns:
                egresos_mes += sum_money_col(df.loc[es_mes, "egreso"])
        if name in ["Facturación VMR", "Facturación VM"]:
            if "valor_pesos" in df.columns:
                total_facturado = df["valor_pesos"].apply(money).sum()
                facturacion_mes += df.loc[es_mes, "valor_pesos"].apply(money).sum()
                estado = df["estado"].astype(str).str.lower().str.strip() if "estado" in df.columns else pd.Series([""] * len(df), index=df.index)
                cobrado = df[estado.isin(["completo", "cobrado", "pagado"])]["valor_pesos"].apply(money).sum()
                cobrado_mes += df.loc[es_mes & estado.isin(["completo", "cobrado", "pagado"]), "valor_pesos"].apply(money).sum()
                a_cobrar += max(0, total_facturado - cobrado)
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
                tipo = df["tipo"].astype(str).str.lower()
                pagado = df["pagado"].apply(money) if "pagado" in df.columns else 0
                saldo = df["importe"].apply(money) - pagado
                a_cobrar += saldo[tipo.eq("a cobrar")].sum()
                a_pagar += saldo[tipo.eq("a pagar")].sum()
        if name in ["Deudas Impositivas VMR", "Deudas Impositivas VM", "Planes de pagos y préstamos", "Pagos pendientes Vitae", "Deuda total", "Honorarios médicos"]:
            if "saldo" in df.columns:
                deuda_total += df["saldo"].apply(money).sum()
            elif "importe" in df.columns:
                pagado = df["pagado"].apply(money) if "pagado" in df.columns else 0
                deuda_total += max(0, df["importe"].apply(money).sum() - pagado.sum())
        if "vencimiento" in df.columns:
            venc = pd.to_datetime(df["vencimiento"], errors="coerce")
            estado = df["estado"].astype(str).str.lower().str.strip() if "estado" in df.columns else pd.Series([""] * len(df), index=df.index)
            vencidos += int((venc.notna() & (venc < hoy) & (~estado.isin(estados_cerrados))).sum())
        if name == "Tareas Pendientes" and "estado" in df.columns:
            tareas_pend += int(df[~df["estado"].isin(["Finalizada", "Cancelada"])].shape[0])
    resultado_mes = ingresos_mes + cobrado_mes - egresos_mes
    pendiente_cobro = a_cobrar
    promedio_facturacion = facturacion_mes / pacientes_mes if pacientes_mes > 0 else 0
    cuenta_corriente_vmr = deuda_mod("Cuenta Corriente VMR", dfs)
    cuenta_corriente_vm = deuda_mod("Cuenta Corriente VM", dfs)
    # =====================================================

    # PANEL EJECUTIVO

    # =====================================================

    st.subheader("📊 Estado General de Vitae")

    c1, c2, c3, c4, c5 = st.columns(5)

    c1.metric(

        "💰 Disponible total",

        fmt_money(liquidez_total)

    )

    c2.metric(

        "💵 Caja",

        fmt_money(caja_vmr + caja_vm)

    )

    c3.metric(

        "🏦 Bancos",

        fmt_money(banco_vmr + banco_vm)

    )

    c4.metric(

        "📈 Facturado",

        fmt_money(facturacion_mes)

    )

    c5.metric(

        "✅ Cobrado",

        fmt_money(cobrado_mes)

    )

    c6, c7, c8, c9, c10 = st.columns(5)

    c6.metric(

        "⏳ Pendiente de cobro",

        fmt_money(pendiente_cobro)

    )

    c7.metric(

        "💸 Pendiente de pago",

        fmt_money(a_pagar)

    )

    c8.metric(

        "📊 Resultado del mes",

        fmt_money(resultado_mes)

    )

    c9.metric(

        "📈 Resultado anual",

        fmt_money(resultado_mes)   # luego lo cambiaremos por el acumulado real

    )

    porcentaje_cobrado = (

        cobrado_mes / facturacion_mes * 100

        if facturacion_mes > 0 else 0

    )

    c10.metric(

        "🎯 Cobranza",

        f"{porcentaje_cobrado:.1f}%"

    )

    c11, c12, c13, c14 = st.columns(4)

    c11.metric(

        "👥 Pacientes del mes",

        pacientes_mes

    )

    c12.metric(

        "⚠️ Vencidos",

        vencidos

    )

    c13.metric(

        "📝 Tareas",

        tareas_pend

    )

    c14.metric(

        "💸 Deuda total",

        fmt_money(deuda_total)

    )
    st.divider()
    render_analisis_global_vitae(dfs)

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
    st.dataframe(tabla, use_container_width=True, hide_index=True)
    

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
def render_caja_pro_panel(
    df: pd.DataFrame,
    module_name: str,
    df_total: pd.DataFrame | None = None,
) -> None:
    """Panel profesional compartido por Caja VM y Caja VMR."""
    import re
    import unicodedata

    import pandas as pd
    import plotly.express as px
    import streamlit as st

    nombre_caja = str(module_name or "Caja").strip()
    clave_widget = re.sub(r"[^a-z0-9]+", "_", nombre_caja.lower()).strip("_") or "caja"

    st.markdown(f"## 💵 Centro de Control · {nombre_caja}")
    st.caption(
        "Ingresos, egresos, saldo real, evolución, categorías, responsables "
        "y controles de calidad de la caja."
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
        if valor is None or (not isinstance(valor, str) and pd.isna(valor)):
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
            if len(partes[-1]) in (1, 2):
                texto = texto.replace(".", "").replace(",", ".")
            else:
                texto = texto.replace(",", "")
        elif texto.count(".") > 1:
            partes = texto.split(".")
            if len(partes[-1]) in (1, 2):
                texto = "".join(partes[:-1]) + "." + partes[-1]
            else:
                texto = "".join(partes)

        try:
            resultado = float(texto)
            return -abs(resultado) if negativo_parentesis else resultado
        except Exception:
            return 0.0

    def moneda(valor: float) -> str:
        valor = float(valor or 0)
        texto = f"{abs(valor):,.2f}"
        texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
        signo = "-" if valor < 0 else ""
        return f"{signo}$ {texto}"

    def porcentaje(valor: float) -> str:
        if pd.isna(valor):
            valor = 0.0
        texto = f"{float(valor):,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{texto}%"

    def preparar(origen: pd.DataFrame | None) -> tuple[pd.DataFrame, dict[str, str | None]]:
        if origen is None or origen.empty:
            return pd.DataFrame(), {}

        base = origen.copy()
        columnas = {normalizar_texto(col): col for col in base.columns}

        def buscar(*opciones: str) -> str | None:
            for opcion in opciones:
                encontrada = columnas.get(normalizar_texto(opcion))
                if encontrada is not None:
                    return encontrada
            return None

        cols = {
            "fecha": buscar("fecha", "fecha_movimiento", "dia", "mes"),
            "concepto": buscar("concepto", "descripcion", "detalle", "movimiento", "motivo"),
            "categoria": buscar("categoria", "rubro", "tipo", "clasificacion"),
            "medio": buscar("medio", "medio_pago", "forma_pago", "metodo_pago", "canal"),
            "ingreso": buscar("ingreso", "ingresos", "entrada", "entradas", "haber", "credito", "monto_ingreso"),
            "egreso": buscar("egreso", "egresos", "salida", "salidas", "debe", "debito", "monto_egreso"),
            "monto": buscar("monto", "importe", "valor", "valor_pesos", "total"),
            "responsable": buscar("responsable", "usuario", "cargado_por", "operador"),
            "observaciones": buscar("observaciones", "observacion", "notas", "nota", "comentario"),
            "saldo": buscar("saldo", "balance", "saldo_acumulado", "saldo_actual"),
        }

        resultado = pd.DataFrame(index=base.index)

        if cols["fecha"]:
            serie_fecha = base[cols["fecha"]]
            fecha = pd.to_datetime(serie_fecha, errors="coerce", dayfirst=True)
            if fecha.isna().any():
                fecha_iso = pd.to_datetime(serie_fecha, format="%Y-%m-%d", errors="coerce")
                fecha = fecha.fillna(fecha_iso)
            resultado["_fecha"] = fecha
        else:
            resultado["_fecha"] = pd.NaT

        def texto_col(nombre: str, defecto: str) -> pd.Series:
            col = cols.get(nombre)
            if col:
                serie = base[col].fillna("").astype(str).str.strip()
                return serie.replace("", defecto)
            return pd.Series(defecto, index=base.index, dtype="object")

        resultado["_concepto"] = texto_col("concepto", "Sin concepto")
        resultado["_categoria"] = texto_col("categoria", "Sin categoría")
        resultado["_medio"] = texto_col("medio", "Sin medio")
        resultado["_responsable"] = texto_col("responsable", "Sin responsable")
        resultado["_observaciones"] = texto_col("observaciones", "")

        if cols["ingreso"]:
            resultado["_ingreso"] = base[cols["ingreso"]].map(numero)
        else:
            resultado["_ingreso"] = 0.0

        if cols["egreso"]:
            resultado["_egreso"] = base[cols["egreso"]].map(numero)
        else:
            resultado["_egreso"] = 0.0

        resultado["_sin_clasificar"] = 0.0

        if not cols["ingreso"] and not cols["egreso"] and cols["monto"]:
            montos = base[cols["monto"]].map(numero)
            clasificador = (
                resultado["_categoria"].astype(str) + " " + resultado["_concepto"].astype(str)
            ).map(normalizar_texto)

            palabras_ingreso = (
                r"ingreso|entrada|cobro|cobrado|venta|aporte|deposito|recibido|"
                r"reintegro|devolucion_a_favor|transferencia_recibida"
            )
            palabras_egreso = (
                r"egreso|salida|pago|pagado|gasto|compra|retiro|honorario|"
                r"impuesto|servicio|proveedor|transferencia_enviada"
            )

            es_ingreso = clasificador.str.contains(palabras_ingreso, regex=True, na=False)
            es_egreso = clasificador.str.contains(palabras_egreso, regex=True, na=False)
            resultado.loc[es_ingreso, "_ingreso"] = montos[es_ingreso].abs()
            resultado.loc[es_egreso, "_egreso"] = montos[es_egreso].abs()
            resultado.loc[~es_ingreso & ~es_egreso, "_sin_clasificar"] = montos[~es_ingreso & ~es_egreso]

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
        resultado.loc[
            (resultado["_ingreso"] > 0) & (resultado["_egreso"] > 0),
            "_tipo",
        ] = "Mixto"

        if cols["saldo"]:
            resultado["_saldo_origen"] = base[cols["saldo"]].map(numero)
        else:
            resultado["_saldo_origen"] = pd.NA

        resultado["_orden_original"] = range(len(resultado))
        resultado = resultado.sort_values(
            ["_fecha", "_orden_original"],
            ascending=[True, True],
            na_position="last",
        )
        resultado["_saldo_periodo"] = resultado["_neto"].cumsum()
        return resultado, cols

    data, columnas_detectadas = preparar(df)
    total_data, _ = preparar(df_total if df_total is not None else df)

    if data.empty:
        st.info("No hay movimientos válidos para analizar.")
        return

    faltantes_criticos = []
    if not columnas_detectadas.get("fecha"):
        faltantes_criticos.append("fecha")
    if not columnas_detectadas.get("ingreso") and not columnas_detectadas.get("egreso") and not columnas_detectadas.get("monto"):
        faltantes_criticos.append("ingreso/egreso")

    if faltantes_criticos:
        st.error(
            "No se pudieron identificar estas columnas: " + ", ".join(faltantes_criticos) + "."
        )
        st.caption(
            "La función reconoce nombres como fecha, concepto, categoría, medio, "
            "ingreso, egreso, responsable y observaciones."
        )
        return

    ingresos = float(data["_ingreso"].sum())
    egresos = float(data["_egreso"].sum())
    flujo_neto = ingresos - egresos
    movimientos = int(len(data))
    movimientos_ingreso = int((data["_ingreso"] > 0).sum())
    movimientos_egreso = int((data["_egreso"] > 0).sum())
    ingreso_promedio = ingresos / movimientos_ingreso if movimientos_ingreso else 0.0
    egreso_promedio = egresos / movimientos_egreso if movimientos_egreso else 0.0
    cobertura = ingresos / egresos * 100 if egresos else (100.0 if ingresos else 0.0)

    saldo_calculado_total = float(total_data["_neto"].sum()) if not total_data.empty else flujo_neto
    saldo_origen = pd.to_numeric(total_data.get("_saldo_origen"), errors="coerce") if "_saldo_origen" in total_data else pd.Series(dtype=float)
    saldo_origen = saldo_origen.dropna()
    caja_actual = float(saldo_origen.iloc[-1]) if not saldo_origen.empty else saldo_calculado_total
    saldo_inicio_periodo = caja_actual - flujo_neto if df_total is not None else 0.0

    fechas_validas = data["_fecha"].dropna()
    if not fechas_validas.empty:
        desde = fechas_validas.min().normalize()
        hasta = fechas_validas.max().normalize()
        periodo_texto = f"{desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}"
    else:
        desde = hasta = None
        periodo_texto = "sin fechas válidas"

    delta_ingresos = None
    delta_egresos = None
    delta_neto = None
    if desde is not None and hasta is not None and not total_data.empty:
        dias = max((hasta - desde).days + 1, 1)
        prev_hasta = desde - pd.Timedelta(days=1)
        prev_desde = prev_hasta - pd.Timedelta(days=dias - 1)
        anterior = total_data[
            total_data["_fecha"].between(prev_desde, prev_hasta, inclusive="both")
        ]
        if not anterior.empty:
            ant_ingresos = float(anterior["_ingreso"].sum())
            ant_egresos = float(anterior["_egreso"].sum())
            ant_neto = ant_ingresos - ant_egresos
            delta_ingresos = ingresos - ant_ingresos
            delta_egresos = egresos - ant_egresos
            delta_neto = flujo_neto - ant_neto

    st.caption(f"Período analizado: {periodo_texto}")

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("💼 Caja actual", moneda(caja_actual))
    k2.metric(
        "📥 Ingresos",
        moneda(ingresos),
        delta=moneda(delta_ingresos) if delta_ingresos is not None else None,
    )
    k3.metric(
        "📤 Egresos",
        moneda(egresos),
        delta=moneda(delta_egresos) if delta_egresos is not None else None,
        delta_color="inverse",
    )
    k4.metric(
        "⚖️ Flujo neto",
        moneda(flujo_neto),
        delta=moneda(delta_neto) if delta_neto is not None else None,
    )
    k5.metric("🧾 Movimientos", f"{movimientos:,}".replace(",", "."))

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Ingreso promedio", moneda(ingreso_promedio))
    s2.metric("Egreso promedio", moneda(egreso_promedio))
    s3.metric("Cobertura ingresos/egresos", porcentaje(cobertura))
    s4.metric(
        "Saldo al inicio del período",
        moneda(saldo_inicio_periodo),
        help="Se calcula con la caja total menos el flujo del período filtrado.",
    )

    if flujo_neto > 0:
        st.success(f"🟢 La caja generó un flujo positivo de {moneda(flujo_neto)} en el período.")
    elif flujo_neto < 0:
        st.error(f"🔴 Los egresos superaron a los ingresos en {moneda(abs(flujo_neto))}.")
    else:
        st.info("⚪ Ingresos y egresos quedaron equilibrados en el período.")

    sin_clasificar = float(data["_sin_clasificar"].abs().sum())
    if sin_clasificar:
        st.warning(
            f"Hay {moneda(sin_clasificar)} sin poder clasificar como ingreso o egreso. "
            "Revisá la categoría o el concepto de esos registros."
        )

    tab_resumen, tab_evolucion, tab_movimientos, tab_control = st.tabs(
        ["📊 Resumen", "📈 Evolución", "🧾 Movimientos", "🚨 Control"]
    )

    with tab_resumen:
        izquierda, derecha = st.columns(2)

        resumen_categoria = (
            data.groupby("_categoria", dropna=False)
            .agg(
                ingresos=("_ingreso", "sum"),
                egresos=("_egreso", "sum"),
                movimientos=("_neto", "size"),
            )
            .reset_index()
            .rename(columns={"_categoria": "categoría"})
        )
        resumen_categoria["neto"] = resumen_categoria["ingresos"] - resumen_categoria["egresos"]
        resumen_categoria = resumen_categoria.sort_values("egresos", ascending=False)

        with izquierda:
            st.markdown("### Egresos por categoría")
            categorias_egreso = resumen_categoria[resumen_categoria["egresos"] > 0]
            if categorias_egreso.empty:
                st.info("No hay egresos en el período.")
            else:
                fig = px.bar(
                    categorias_egreso.sort_values("egresos", ascending=True).tail(12),
                    x="egresos",
                    y="categoría",
                    orientation="h",
                    text_auto=".2s",
                )
                fig.update_layout(
                    xaxis_title="Egresos",
                    yaxis_title="",
                    height=420,
                    margin=dict(l=10, r=10, t=10, b=10),
                )
                st.plotly_chart(fig, use_container_width=True)

        with derecha:
            st.markdown("### Ingresos por categoría")
            categorias_ingreso = resumen_categoria[resumen_categoria["ingresos"] > 0]
            if categorias_ingreso.empty:
                st.info("No hay ingresos en el período.")
            else:
                fig = px.bar(
                    categorias_ingreso.sort_values("ingresos", ascending=True).tail(12),
                    x="ingresos",
                    y="categoría",
                    orientation="h",
                    text_auto=".2s",
                )
                fig.update_layout(
                    xaxis_title="Ingresos",
                    yaxis_title="",
                    height=420,
                    margin=dict(l=10, r=10, t=10, b=10),
                )
                st.plotly_chart(fig, use_container_width=True)

        st.markdown("### Resultado por categoría")
        tabla_categoria = resumen_categoria.copy()
        for col in ["ingresos", "egresos", "neto"]:
            tabla_categoria[col] = tabla_categoria[col].map(moneda)
        st.dataframe(tabla_categoria, use_container_width=True, hide_index=True)

        resumen_medio = (
            data.groupby("_medio", dropna=False)
            .agg(
                ingresos=("_ingreso", "sum"),
                egresos=("_egreso", "sum"),
                movimientos=("_neto", "size"),
            )
            .reset_index()
            .rename(columns={"_medio": "medio"})
        )
        resumen_medio["neto"] = resumen_medio["ingresos"] - resumen_medio["egresos"]
        resumen_medio = resumen_medio.sort_values("movimientos", ascending=False)

        st.markdown("### Movimiento por medio de pago")
        tabla_medio = resumen_medio.copy()
        for col in ["ingresos", "egresos", "neto"]:
            tabla_medio[col] = tabla_medio[col].map(moneda)
        st.dataframe(tabla_medio, use_container_width=True, hide_index=True)

        resumen_responsable = (
            data.groupby("_responsable", dropna=False)
            .agg(
                ingresos=("_ingreso", "sum"),
                egresos=("_egreso", "sum"),
                movimientos=("_neto", "size"),
            )
            .reset_index()
            .rename(columns={"_responsable": "responsable"})
        )
        resumen_responsable["neto"] = (
            resumen_responsable["ingresos"] - resumen_responsable["egresos"]
        )
        resumen_responsable = resumen_responsable.sort_values("movimientos", ascending=False)

        with st.expander("👤 Ver control por responsable", expanded=False):
            tabla_responsable = resumen_responsable.copy()
            for col in ["ingresos", "egresos", "neto"]:
                tabla_responsable[col] = tabla_responsable[col].map(moneda)
            st.dataframe(tabla_responsable, use_container_width=True, hide_index=True)

    with tab_evolucion:
        validas = data.dropna(subset=["_fecha"]).copy()
        if validas.empty:
            st.info("No hay fechas válidas para construir la evolución.")
        else:
            diario = (
                validas.assign(día=validas["_fecha"].dt.normalize())
                .groupby("día")
                .agg(ingresos=("_ingreso", "sum"), egresos=("_egreso", "sum"))
                .reset_index()
                .sort_values("día")
            )
            diario["neto"] = diario["ingresos"] - diario["egresos"]
            diario["saldo_acumulado_periodo"] = diario["neto"].cumsum()

            fig = px.line(
                diario,
                x="día",
                y="saldo_acumulado_periodo",
                markers=True,
                title="Saldo acumulado dentro del período filtrado",
            )
            fig.update_layout(
                xaxis_title="Fecha",
                yaxis_title="Saldo acumulado",
                height=400,
            )
            st.plotly_chart(fig, use_container_width=True)

            diario_largo = diario.melt(
                id_vars="día",
                value_vars=["ingresos", "egresos"],
                var_name="tipo",
                value_name="importe",
            )
            fig = px.bar(
                diario_largo,
                x="día",
                y="importe",
                color="tipo",
                barmode="group",
                title="Ingresos y egresos por día",
            )
            fig.update_layout(
                xaxis_title="Fecha",
                yaxis_title="Importe",
                height=420,
            )
            st.plotly_chart(fig, use_container_width=True)

            mensual = validas.copy()
            mensual["mes"] = mensual["_fecha"].dt.to_period("M").astype(str)
            mensual = (
                mensual.groupby("mes")
                .agg(ingresos=("_ingreso", "sum"), egresos=("_egreso", "sum"))
                .reset_index()
            )
            mensual["neto"] = mensual["ingresos"] - mensual["egresos"]

            st.markdown("### Cierre mensual")
            tabla_mensual = mensual.copy()
            for col in ["ingresos", "egresos", "neto"]:
                tabla_mensual[col] = tabla_mensual[col].map(moneda)
            st.dataframe(tabla_mensual, use_container_width=True, hide_index=True)

            st.markdown("### Cierre diario")
            tabla_diaria = diario.sort_values("día", ascending=False).copy()
            tabla_diaria["día"] = tabla_diaria["día"].dt.strftime("%d/%m/%Y")
            for col in ["ingresos", "egresos", "neto", "saldo_acumulado_periodo"]:
                tabla_diaria[col] = tabla_diaria[col].map(moneda)
            st.dataframe(tabla_diaria.head(31), use_container_width=True, hide_index=True)

    with tab_movimientos:
        st.markdown("### Buscador de movimientos")
        c_buscar, c_tipo, c_categoria = st.columns([2, 1, 1])
        with c_buscar:
            buscar_texto = st.text_input(
                "Buscar concepto, responsable u observación",
                key=f"buscar_movimientos_{clave_widget}",
            )
        with c_tipo:
            opciones_tipo = ["Todos", "Ingreso", "Egreso", "Mixto", "Sin movimiento"]
            filtro_tipo = st.selectbox(
                "Tipo",
                opciones_tipo,
                key=f"tipo_movimientos_{clave_widget}",
            )
        with c_categoria:
            opciones_categoria = ["Todas"] + sorted(data["_categoria"].dropna().astype(str).unique().tolist())
            filtro_categoria = st.selectbox(
                "Categoría",
                opciones_categoria,
                key=f"categoria_movimientos_{clave_widget}",
            )

        detalle = data.copy()
        if buscar_texto.strip():
            patron = re.escape(buscar_texto.strip())
            universo = (
                detalle["_concepto"].astype(str)
                + " "
                + detalle["_responsable"].astype(str)
                + " "
                + detalle["_observaciones"].astype(str)
            )
            detalle = detalle[universo.str.contains(patron, case=False, regex=True, na=False)]
        if filtro_tipo != "Todos":
            detalle = detalle[detalle["_tipo"] == filtro_tipo]
        if filtro_categoria != "Todas":
            detalle = detalle[detalle["_categoria"] == filtro_categoria]

        detalle = detalle.sort_values(
            ["_fecha", "_orden_original"],
            ascending=[False, False],
            na_position="last",
        )
        tabla_detalle = pd.DataFrame(
            {
                "fecha": detalle["_fecha"].dt.strftime("%d/%m/%Y").fillna(""),
                "concepto": detalle["_concepto"],
                "categoría": detalle["_categoria"],
                "medio": detalle["_medio"],
                "ingreso": detalle["_ingreso"],
                "egreso": detalle["_egreso"],
                "neto": detalle["_neto"],
                "responsable": detalle["_responsable"],
                "observaciones": detalle["_observaciones"],
            }
        )

        st.dataframe(
            tabla_detalle,
            use_container_width=True,
            hide_index=True,
            column_config={
                "ingreso": st.column_config.NumberColumn("Ingreso", format="$ %.2f"),
                "egreso": st.column_config.NumberColumn("Egreso", format="$ %.2f"),
                "neto": st.column_config.NumberColumn("Neto", format="$ %.2f"),
            },
        )
        st.caption(f"Movimientos mostrados: {len(tabla_detalle)}")

        exportar = tabla_detalle.copy()
        csv = exportar.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
        st.download_button(
            "📥 Descargar movimientos analizados",
            data=csv,
            file_name=f"{clave_widget}_movimientos.csv",
            mime="text/csv",
            key=f"descargar_movimientos_{clave_widget}",
        )

    with tab_control:
        sin_fecha = int(data["_fecha"].isna().sum())
        sin_concepto = int((data["_concepto"] == "Sin concepto").sum())
        sin_categoria = int((data["_categoria"] == "Sin categoría").sum())
        en_cero = int(((data["_ingreso"] == 0) & (data["_egreso"] == 0)).sum())
        ambos = int(((data["_ingreso"] > 0) & (data["_egreso"] > 0)).sum())

        columnas_dup = ["_fecha", "_concepto", "_ingreso", "_egreso"]
        duplicados_mask = data.duplicated(subset=columnas_dup, keep=False)
        duplicados = int(duplicados_mask.sum())

        q1, q2, q3, q4, q5 = st.columns(5)
        q1.metric("Sin fecha", sin_fecha)
        q2.metric("Sin concepto", sin_concepto)
        q3.metric("Sin categoría", sin_categoria)
        q4.metric("Importe en cero", en_cero)
        q5.metric("Posibles duplicados", duplicados)

        if not any([sin_fecha, sin_concepto, sin_categoria, en_cero, duplicados, ambos]):
            st.success("✅ No se detectaron problemas evidentes en los movimientos filtrados.")
        else:
            if sin_fecha:
                st.warning(f"Hay {sin_fecha} movimientos sin una fecha válida.")
            if sin_concepto:
                st.warning(f"Hay {sin_concepto} movimientos sin concepto.")
            if sin_categoria:
                st.warning(f"Hay {sin_categoria} movimientos sin categoría.")
            if en_cero:
                st.warning(f"Hay {en_cero} movimientos sin ingreso ni egreso.")
            if ambos:
                st.warning(f"Hay {ambos} filas que tienen ingreso y egreso simultáneamente.")
            if duplicados:
                st.error(f"Se detectaron {duplicados} filas que podrían estar duplicadas.")
                duplicados_df = data[duplicados_mask].copy().sort_values("_fecha", ascending=False)
                tabla_duplicados = pd.DataFrame(
                    {
                        "fecha": duplicados_df["_fecha"].dt.strftime("%d/%m/%Y").fillna(""),
                        "concepto": duplicados_df["_concepto"],
                        "categoría": duplicados_df["_categoria"],
                        "ingreso": duplicados_df["_ingreso"],
                        "egreso": duplicados_df["_egreso"],
                        "responsable": duplicados_df["_responsable"],
                    }
                )
                st.dataframe(tabla_duplicados, use_container_width=True, hide_index=True)

        egresos_validos = data[data["_egreso"] > 0]
        if not egresos_validos.empty and egresos > 0:
            top_categoria = (
                egresos_validos.groupby("_categoria")["_egreso"].sum().sort_values(ascending=False)
            )
            categoria_principal = str(top_categoria.index[0])
            importe_principal = float(top_categoria.iloc[0])
            concentracion = importe_principal / egresos * 100
            st.markdown("### Concentración del gasto")
            st.write(
                f"La categoría con mayor egreso es **{categoria_principal}**, con "
                f"{moneda(importe_principal)} ({porcentaje(concentracion)} del total de egresos)."
            )

        st.markdown("### Columnas detectadas")
        columnas_mostrar = {
            nombre: (columna if columna else "No encontrada")
            for nombre, columna in columnas_detectadas.items()
        }
        st.dataframe(
            pd.DataFrame(
                columnas_mostrar.items(),
                columns=["dato", "columna utilizada"],
            ),
            use_container_width=True,
            hide_index=True,
        )
def render_facturacion_pro(module_name: str, cfg: Dict[str, Any]) -> None:
    table = cfg["table"]
    try:
        df_base = get_df(table)
    except Exception as e:
        st.error(f"No se pudo leer Google Sheets para {table}: {e}")
        df_base = pd.DataFrame()
    render_header()
    st.header(module_name)
    st.caption(cfg.get("descripcion", ""))
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
        if df_panel.empty:
            st.warning("No hay registros cargados.")
        else:
            filtered = apply_filters(df_panel, module_name)
            if table in ["caja_vm", "caja_vmr"]:
                safe_panel("render_caja_pro_panel", filtered, module_name)
            if table in ["banco_galicia_vm", "banco_macro_vmr"]:
                render_banco_pro_panel(filtered, module_name)
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
            if table in ["facturacion_vm", "facturacion_vmr"]:
            
            
                render_analisis_anual_2026(df_base)
            
                render_analisis_mensual_2026(filtered)
            
                render_graficos_facturacion(filtered)
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
    st.markdown("### 📋 Resumen por módulo")
    st.dataframe(
        resumen_modulos.sort_values(
            "Facturado",
            ascending=False
        ),
        use_container_width=True,
        hide_index=True
    )

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

                df_mes_caja["ingreso"].apply(money).sum()

                if "ingreso" in df_mes_caja.columns

                else 0.0

            )

            egresos_caja = (

                df_mes_caja["egreso"].apply(money).sum()

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
