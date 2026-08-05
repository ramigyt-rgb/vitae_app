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
from farmacia_pro import render_farmacia_pro
from importers import render_importer
from agenda_quirofano_ultra_pro import render_agenda_quirofano_ultra_pro
from assistant import preguntar_ia
from assistant import preguntar_dashboard
from director_ia import (
    generar_resumen_ejecutivo,
    generar_briefing_automatico,
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
            <div style="font-size:1.35rem;font-weight:750;">🏭 Centro de Control de Facturación {empresa}</div>
            <div style="opacity:.72;margin-top:4px;">Producción, cobranza, vencimientos, concentración y calidad de datos en una sola vista.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    fecha_min = data["_fecha_base"].min()
    fecha_max = data["_fecha_base"].max()
    periodo_default = "Año actual"

    f1, f2, f3 = st.columns([1.15, 1.7, 1.15])
    with f1:
        periodo = st.selectbox(
            "Período",
            [
                "Año actual",
                "Mes actual",
                "Últimos 30 días",
                "Últimos 90 días",
                "Todo el historial",
                "Personalizado",
            ],
            index=0,
            key=f"{key}_periodo",
        )
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

    if periodo == "Personalizado":
        valor_desde = fecha_min.date() if pd.notna(fecha_min) else hoy.date()
        valor_hasta = fecha_max.date() if pd.notna(fecha_max) else hoy.date()
        p1, p2 = st.columns(2)
        with p1:
            desde = pd.Timestamp(
                st.date_input(
                    "Desde",
                    value=valor_desde,
                    key=f"{key}_desde",
                )
            )
        with p2:
            hasta = pd.Timestamp(
                st.date_input(
                    "Hasta",
                    value=valor_hasta,
                    key=f"{key}_hasta",
                )
            )
    elif periodo == "Mes actual":
        desde = hoy.replace(day=1)
        hasta = hoy
    elif periodo == "Últimos 30 días":
        desde = hoy - pd.Timedelta(days=29)
        hasta = hoy
    elif periodo == "Últimos 90 días":
        desde = hoy - pd.Timedelta(days=89)
        hasta = hoy
    elif periodo == "Año actual":
        desde = pd.Timestamp(year=hoy.year, month=1, day=1)
        hasta = hoy
    else:
        desde = None
        hasta = None

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


def _convenios_cargar_google() -> tuple[dict[str, list[list[Any]]], pd.DataFrame]:
    """Lee cada pestaña sin exigir que todas tengan el mismo diseño."""
    import pandas as pd

    aliases = {
        "AVALIAN": ["AVALIAN", "Avalian", "avalían"],
        "OSDE": ["OSDE", "osde"],
        "OSPE": ["OSPE", "ospe"],
        "OSSEG": ["OSSEG", "osseg"],
        "BOREAL": ["BOREAL", "Boreal", "boreal"],
        "IPS": ["IPS", "IPSS", "IPSSALTA", "IPS SALTA"],
        "PREVENCION SALUD": ["PREVENCION S", "PREVENCION SALUD", "PREVENCIÓN SALUD", "PREVENCION"],
        "ACLISASA": ["ACLISASA", "aclisasa"],
        "CIRCULO MEDICO": ["CIRCULO MED", "CIRCULO MEDICO", "CÍRCULO MEDICO", "CIRCULO"],
    }

    matrices: dict[str, list[list[Any]]] = {}
    estados = []
    for convenio, candidatos in aliases.items():
        ultimo_error = ""
        encontrado = False
        for pestaña in candidatos:
            try:
                df = get_df(pestaña)
                matriz = _convenios_df_a_matriz(df)
                if matriz and any(any(_convenios_texto(v) for v in fila) for fila in matriz):
                    matrices[convenio] = matriz
                    estados.append({
                        "convenio": convenio,
                        "pestaña": pestaña,
                        "estado": "Conectada",
                        "detalle": f"{max(len(matriz)-1, 0)} filas crudas",
                    })
                    encontrado = True
                    break
            except Exception as exc:
                ultimo_error = str(exc)
        if not encontrado:
            estados.append({
                "convenio": convenio,
                "pestaña": " / ".join(candidatos[:2]),
                "estado": "No encontrada",
                "detalle": ultimo_error[:180] if ultimo_error else "Sin datos o pestaña inexistente",
            })
    return matrices, pd.DataFrame(estados)


def _convenios_buscar_archivo_fijo():
    """Localiza la planilla permanente dentro del repositorio de la aplicación."""
    from pathlib import Path

    base = Path(__file__).resolve().parent
    nombres = [
        "QUIROFANO CONVENIOS.xlsx",
        "QUIROFANO_CONVENIOS.xlsx",
        "quirofano convenios.xlsx",
        "quirofano_convenios.xlsx",
    ]
    carpetas = [
        base / "data",
        base / "datos",
        base / "documentos",
        base / "assets",
        base,
        Path.cwd() / "data",
        Path.cwd(),
    ]

    revisados = set()
    for carpeta in carpetas:
        try:
            carpeta = carpeta.resolve()
        except Exception:
            continue
        if carpeta in revisados:
            continue
        revisados.add(carpeta)
        for nombre in nombres:
            candidato = carpeta / nombre
            if candidato.is_file():
                return candidato
    return None


def _convenios_cargar_excel(archivo) -> tuple[dict[str, list[list[Any]]], pd.DataFrame]:
    """Lee un Excel cargado, un BytesIO o una ruta fija del repositorio."""
    import pandas as pd

    matrices = {}
    estados = []
    if archivo is None:
        return matrices, pd.DataFrame()

    if hasattr(archivo, "seek"):
        archivo.seek(0)

    libro = pd.ExcelFile(archivo, engine="openpyxl")
    for hoja in libro.sheet_names:
        df = pd.read_excel(libro, sheet_name=hoja, header=None, dtype=object)
        matriz = df.astype(object).where(pd.notna(df), "").values.tolist()
        canonico = _convenios_nombre_canonico(hoja)
        matrices[canonico] = matriz
        estados.append({
            "convenio": canonico,
            "pestaña": hoja,
            "estado": "Planilla fija conectada",
            "detalle": f"{len(df)} filas crudas",
        })
    return matrices, pd.DataFrame(estados)


def _convenios_nombre_canonico(nombre: str) -> str:
    texto = _convenios_normalizar_texto(nombre)
    if "prevencion" in texto:
        return "PREVENCION SALUD"
    if "circulo" in texto:
        return "CIRCULO MEDICO"
    if texto in {"ips", "ipss", "ipssalta", "ips salta"}:
        return "IPS"
    for canonico in ["AVALIAN", "OSDE", "OSPE", "OSSEG", "BOREAL", "ACLISASA"]:
        if canonico.lower() in texto:
            return canonico
    return _convenios_texto(nombre).upper() or "SIN NOMBRE"


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
        if convenio_canonico == "ACLISASA" and not df_directorio.empty:
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


# =========================================================
# CONVENIOS VITAE · BASE MAESTRA PERMANENTE Y MOTOR ARANCELARIO
# =========================================================
_CV_TABLAS = {
    "nomenclador": {
        "sheet": "convenios_nomenclador",
        "columns": [
            "registro_id", "codigo", "descripcion", "especialidad", "categoria",
            "tipo_practica", "activo", "vigencia_desde", "vigencia_hasta",
            "fuente", "observaciones",
        ],
    },
    "ips": {
        "sheet": "convenios_valores_ips",
        "columns": [
            "registro_id", "codigo", "valor_ips", "vigencia_desde",
            "vigencia_hasta", "fuente", "observaciones",
        ],
    },
    "entidades": {
        "sheet": "convenios_entidades",
        "columns": [
            "registro_id", "convenio_id", "empresa", "obra_social", "plan",
            "modelo_valor", "porcentaje_sobre_ips", "valor_fijo_general",
            "vigencia_desde", "vigencia_hasta", "activo", "requiere_autorizacion",
            "modalidad_facturacion", "correo", "contacto", "observaciones",
        ],
    },
    "codigos": {
        "sheet": "convenios_codigos",
        "columns": [
            "registro_id", "convenio_id", "empresa", "obra_social", "plan",
            "codigo", "habilitado", "modelo_valor_override", "porcentaje_override",
            "valor_propio", "vigencia_desde", "vigencia_hasta",
            "requiere_autorizacion", "modulo", "observaciones",
        ],
    },
}

_CV_EMPRESAS = ["VM", "VMR"]
_CV_MODELOS = ["IPS PURO", "IPS + %", "VALOR PROPIO", "VALOR FIJO GENERAL", "A CONVENIR"]
_CV_SI_NO = ["SI", "NO"]


def _cv_df_vacio(tipo: str) -> pd.DataFrame:
    return pd.DataFrame(columns=_CV_TABLAS[tipo]["columns"])


def _cv_numero(valor: Any):
    numero = _convenios_numero(valor)
    return numero if numero is not None else None


def _cv_texto(valor: Any) -> str:
    return _convenios_texto(valor)


def _cv_si(valor: Any) -> bool:
    texto = _convenios_normalizar_texto(valor)
    return texto in {"si", "s", "yes", "true", "1", "activo", "vigente", "habilitado"}


def _cv_fecha(valor: Any):
    if valor is None or _cv_texto(valor) == "":
        return pd.NaT
    return pd.to_datetime(valor, dayfirst=True, errors="coerce")


def _cv_fecha_iso(valor: Any) -> str:
    fecha = _cv_fecha(valor)
    return "" if pd.isna(fecha) else fecha.strftime("%Y-%m-%d")


def _cv_slug(valor: Any) -> str:
    import re
    texto = _convenios_normalizar_texto(valor)
    texto = re.sub(r"[^a-z0-9]+", "_", texto).strip("_")
    return texto[:60]


def _cv_id_deterministico(prefijo: str, contenido: str) -> str:
    import hashlib
    digest = hashlib.sha1(contenido.encode("utf-8", errors="ignore")).hexdigest()[:14]
    return f"{prefijo}_{digest}"


def _cv_nuevo_id(prefijo: str) -> str:
    import uuid
    return f"{prefijo}_{uuid.uuid4().hex[:14]}"


def _cv_normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    salida = df.copy()
    renombres = {}
    for columna in salida.columns:
        nombre = _cv_slug(columna)
        renombres[columna] = nombre
    return salida.rename(columns=renombres)


def _cv_normalizar_tabla(tipo: str, df: pd.DataFrame | None) -> pd.DataFrame:
    columnas = _CV_TABLAS[tipo]["columns"]
    if df is None:
        return _cv_df_vacio(tipo)

    salida = _cv_normalizar_columnas(df)
    aliases = {
        "id": "registro_id",
        "id_registro": "registro_id",
        "cod": "codigo",
        "codigo_practica": "codigo",
        "practica": "descripcion",
        "descripcion_practica": "descripcion",
        "valor": "valor_ips" if tipo == "ips" else "valor_propio",
        "vigencia": "vigencia_desde",
        "desde": "vigencia_desde",
        "hasta": "vigencia_hasta",
        "obra": "obra_social",
        "os": "obra_social",
        "porcentaje": "porcentaje_sobre_ips" if tipo == "entidades" else "porcentaje_override",
    }
    salida = salida.rename(columns={c: aliases[c] for c in salida.columns if c in aliases})

    for columna in columnas:
        if columna not in salida.columns:
            salida[columna] = ""
    salida = salida[columnas].copy()

    # Quita filas completamente vacías, pero conserva filas que tengan un ID o código.
    columnas_contenido = [c for c in columnas if c not in {"registro_id", "convenio_id"}]
    mascara = salida[columnas_contenido].apply(
        lambda fila: any(_cv_texto(v) != "" for v in fila), axis=1
    )
    salida = salida[mascara].reset_index(drop=True)

    for columna in salida.columns:
        if columna in {"valor_ips", "porcentaje_sobre_ips", "valor_fijo_general", "porcentaje_override", "valor_propio"}:
            salida[columna] = salida[columna].apply(_cv_numero)
        elif columna in {"vigencia_desde", "vigencia_hasta"}:
            salida[columna] = salida[columna].apply(_cv_fecha_iso)
        else:
            salida[columna] = salida[columna].apply(_cv_texto)

    if tipo == "nomenclador":
        salida["codigo"] = salida["codigo"].apply(_convenios_codigo)
        salida["activo"] = salida["activo"].apply(lambda v: "SI" if _cv_si(v) or _cv_texto(v) == "" else "NO")
        prefijo = "nom"
    elif tipo == "ips":
        salida["codigo"] = salida["codigo"].apply(_convenios_codigo)
        prefijo = "ips"
    elif tipo == "entidades":
        salida["empresa"] = salida["empresa"].str.upper().replace({"MEDICAL": "VM", "REPRODUCTIVA": "VMR"})
        salida["activo"] = salida["activo"].apply(lambda v: "SI" if _cv_si(v) or _cv_texto(v) == "" else "NO")
        salida["requiere_autorizacion"] = salida["requiere_autorizacion"].apply(
            lambda v: "SI" if _cv_si(v) else "NO"
        )
        salida["modelo_valor"] = salida["modelo_valor"].str.upper()
        prefijo = "ent"
    else:
        salida["empresa"] = salida["empresa"].str.upper().replace({"MEDICAL": "VM", "REPRODUCTIVA": "VMR"})
        salida["codigo"] = salida["codigo"].apply(_convenios_codigo)
        salida["habilitado"] = salida["habilitado"].apply(lambda v: "SI" if _cv_si(v) or _cv_texto(v) == "" else "NO")
        salida["requiere_autorizacion"] = salida["requiere_autorizacion"].apply(
            lambda v: "SI" if _cv_si(v) else "NO"
        )
        salida["modelo_valor_override"] = salida["modelo_valor_override"].str.upper()
        prefijo = "cod"

    # IDs persistentes: si una fila antigua no tiene ID, se le asigna uno estable.
    usados = set()
    ids = []
    for indice, fila in salida.iterrows():
        existente = _cv_texto(fila.get("registro_id", ""))
        base_id = existente or _cv_id_deterministico(
            prefijo,
            f"{tipo}|{indice}|" + "|".join(_cv_texto(fila.get(c, "")) for c in columnas_contenido),
        )
        candidato = base_id
        contador = 2
        while candidato in usados:
            candidato = f"{base_id}_{contador}"
            contador += 1
        usados.add(candidato)
        ids.append(candidato)
    salida["registro_id"] = ids

    if tipo == "entidades":
        convenios = []
        usados_conv = set()
        for _, fila in salida.iterrows():
            existente = _cv_texto(fila.get("convenio_id", ""))
            base = existente or _cv_id_deterministico(
                "cv",
                f"{fila.get('empresa','')}|{fila.get('obra_social','')}|{fila.get('plan','')}",
            )
            candidato = base
            contador = 2
            while candidato in usados_conv:
                candidato = f"{base}_{contador}"
                contador += 1
            usados_conv.add(candidato)
            convenios.append(candidato)
        salida["convenio_id"] = convenios

    if tipo == "codigos":
        salida["convenio_id"] = salida.apply(
            lambda fila: _cv_texto(fila.get("convenio_id", "")) or _cv_id_deterministico(
                "cv",
                f"{fila.get('empresa','')}|{fila.get('obra_social','')}|{fila.get('plan','')}",
            ),
            axis=1,
        )

    return salida.reset_index(drop=True)


def _cv_preparar_guardado(tipo: str, df: pd.DataFrame) -> pd.DataFrame:
    salida = _cv_normalizar_tabla(tipo, df)
    for columna in salida.columns:
        if columna in {"valor_ips", "porcentaje_sobre_ips", "valor_fijo_general", "porcentaje_override", "valor_propio"}:
            salida[columna] = pd.to_numeric(salida[columna], errors="coerce")
        else:
            salida[columna] = salida[columna].fillna("").astype(str)
    return salida


def _cv_cargar_base() -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    base = {}
    errores = {}
    for tipo, config in _CV_TABLAS.items():
        try:
            base[tipo] = _cv_normalizar_tabla(tipo, get_df(config["sheet"]))
        except Exception as exc:
            base[tipo] = _cv_df_vacio(tipo)
            errores[tipo] = str(exc)
    return base, errores


def _cv_guardar_tabla(tipo: str, df: pd.DataFrame) -> pd.DataFrame:
    preparado = _cv_preparar_guardado(tipo, df)
    sync_df_to_sheet(_CV_TABLAS[tipo]["sheet"], preparado)
    st.cache_data.clear()
    return preparado


def _cv_inicializar_faltantes(tipos: list[str]) -> None:
    for tipo in tipos:
        sync_df_to_sheet(_CV_TABLAS[tipo]["sheet"], _cv_df_vacio(tipo))
    st.cache_data.clear()


def _cv_reemplazar_vista(
    tipo: str,
    original: pd.DataFrame,
    vista_original: pd.DataFrame,
    editado: pd.DataFrame,
) -> pd.DataFrame:
    original = _cv_normalizar_tabla(tipo, original)
    vista_original = _cv_normalizar_tabla(tipo, vista_original)
    editado = _cv_normalizar_tabla(tipo, editado)

    ids_vista = set(vista_original.get("registro_id", pd.Series(dtype=str)).astype(str))
    restante = original[~original["registro_id"].astype(str).isin(ids_vista)].copy()
    combinado = pd.concat([restante, editado], ignore_index=True)
    return _cv_normalizar_tabla(tipo, combinado)


def _cv_en_periodo(desde: Any, hasta: Any, fecha: pd.Timestamp) -> bool:
    d = _cv_fecha(desde)
    h = _cv_fecha(hasta)
    if pd.notna(d) and fecha.normalize() < d.normalize():
        return False
    if pd.notna(h) and fecha.normalize() > h.normalize():
        return False
    return True


def _cv_ultima_version_por_codigo(df: pd.DataFrame, fecha: pd.Timestamp, activo_col: str | None = None) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    trabajo = df.copy()
    trabajo["_desde"] = pd.to_datetime(trabajo.get("vigencia_desde", ""), errors="coerce")
    trabajo["_hasta"] = pd.to_datetime(trabajo.get("vigencia_hasta", ""), errors="coerce")
    mascara = (trabajo["_desde"].isna() | (trabajo["_desde"].dt.normalize() <= fecha.normalize()))
    mascara &= (trabajo["_hasta"].isna() | (trabajo["_hasta"].dt.normalize() >= fecha.normalize()))
    if activo_col and activo_col in trabajo.columns:
        mascara &= trabajo[activo_col].apply(_cv_si)
    trabajo = trabajo[mascara].copy()
    if trabajo.empty:
        return trabajo
    trabajo = trabajo.sort_values(["codigo", "_desde"], ascending=[True, False], na_position="last")
    return trabajo.drop_duplicates("codigo", keep="first").drop(columns=["_desde", "_hasta"], errors="ignore")


def _cv_calcular_catalogo(base: dict[str, pd.DataFrame], fecha_consulta: Any) -> pd.DataFrame:
    fecha = pd.to_datetime(fecha_consulta, errors="coerce")
    if pd.isna(fecha):
        fecha = pd.Timestamp.today().normalize()

    codigos = _cv_normalizar_tabla("codigos", base.get("codigos"))
    entidades = _cv_normalizar_tabla("entidades", base.get("entidades"))
    nomenclador = _cv_normalizar_tabla("nomenclador", base.get("nomenclador"))
    ips = _cv_normalizar_tabla("ips", base.get("ips"))

    columnas_salida = [
        "registro_id", "convenio_id", "empresa", "obra_social", "plan", "codigo",
        "descripcion", "especialidad", "categoria", "modelo_valor", "valor_ips",
        "porcentaje", "valor_propio", "valor_final", "vigencia_desde",
        "vigencia_hasta", "estado", "requiere_autorizacion", "modulo", "observaciones",
    ]
    if codigos.empty:
        return pd.DataFrame(columns=columnas_salida)

    entidades_join = entidades.rename(columns={
        "empresa": "empresa_entidad",
        "obra_social": "obra_social_entidad",
        "plan": "plan_entidad",
        "vigencia_desde": "vigencia_desde_entidad",
        "vigencia_hasta": "vigencia_hasta_entidad",
        "activo": "activo_entidad",
        "requiere_autorizacion": "autorizacion_entidad",
        "observaciones": "observaciones_entidad",
    })
    columnas_entidad = [
        "convenio_id", "empresa_entidad", "obra_social_entidad", "plan_entidad",
        "modelo_valor", "porcentaje_sobre_ips", "valor_fijo_general",
        "vigencia_desde_entidad", "vigencia_hasta_entidad", "activo_entidad",
        "autorizacion_entidad", "observaciones_entidad",
    ]
    resultado = codigos.merge(
        entidades_join[columnas_entidad], on="convenio_id", how="left"
    )
    resultado["empresa"] = resultado["empresa"].where(resultado["empresa"].str.strip().ne(""), resultado["empresa_entidad"])
    resultado["obra_social"] = resultado["obra_social"].where(resultado["obra_social"].str.strip().ne(""), resultado["obra_social_entidad"])
    resultado["plan"] = resultado["plan"].where(resultado["plan"].str.strip().ne(""), resultado["plan_entidad"])

    nom_actual = _cv_ultima_version_por_codigo(nomenclador, fecha, "activo")
    if not nom_actual.empty:
        resultado = resultado.merge(
            nom_actual[["codigo", "descripcion", "especialidad", "categoria"]],
            on="codigo", how="left",
        )
    else:
        resultado["descripcion"] = ""
        resultado["especialidad"] = ""
        resultado["categoria"] = ""

    ips_actual = _cv_ultima_version_por_codigo(ips, fecha)
    if not ips_actual.empty:
        resultado = resultado.merge(
            ips_actual[["codigo", "valor_ips"]], on="codigo", how="left"
        )
    else:
        resultado["valor_ips"] = None

    resultado["modelo_valor"] = resultado.apply(
        lambda fila: _cv_texto(fila.get("modelo_valor_override", "")).upper()
        or _cv_texto(fila.get("modelo_valor", "")).upper()
        or "IPS PURO",
        axis=1,
    )
    resultado["porcentaje"] = resultado.apply(
        lambda fila: (
            _cv_numero(fila.get("porcentaje_override"))
            if _cv_numero(fila.get("porcentaje_override")) is not None
            else (_cv_numero(fila.get("porcentaje_sobre_ips")) or 0.0)
        ),
        axis=1,
    )
    resultado["valor_ips"] = pd.to_numeric(resultado["valor_ips"], errors="coerce")
    resultado["valor_propio"] = pd.to_numeric(resultado["valor_propio"], errors="coerce")
    resultado["valor_fijo_general"] = pd.to_numeric(resultado["valor_fijo_general"], errors="coerce")

    def calcular_valor(fila):
        modelo = fila["modelo_valor"]
        ips_valor = fila.get("valor_ips")
        propio = fila.get("valor_propio")
        fijo = fila.get("valor_fijo_general")
        porcentaje = fila.get("porcentaje") or 0.0
        if modelo == "IPS PURO":
            return ips_valor
        if modelo == "IPS + %":
            return ips_valor * (1 + porcentaje / 100) if pd.notna(ips_valor) else None
        if modelo == "VALOR PROPIO":
            return propio if pd.notna(propio) else fijo
        if modelo == "VALOR FIJO GENERAL":
            return fijo
        return None

    resultado["valor_final"] = resultado.apply(calcular_valor, axis=1)

    def estado_fila(fila):
        if not _cv_si(fila.get("activo_entidad", "SI")):
            return "Convenio inactivo"
        if not _cv_en_periodo(fila.get("vigencia_desde_entidad"), fila.get("vigencia_hasta_entidad"), fecha):
            return "Convenio fuera de vigencia"
        if not _cv_si(fila.get("habilitado", "SI")):
            return "Código inactivo"
        if not _cv_en_periodo(fila.get("vigencia_desde"), fila.get("vigencia_hasta"), fecha):
            return "Código fuera de vigencia"
        if not _cv_texto(fila.get("descripcion", "")):
            return "No existe en nomenclador"
        if fila.get("modelo_valor") in {"IPS PURO", "IPS + %"} and pd.isna(fila.get("valor_ips")):
            return "Falta valor IPS"
        if fila.get("modelo_valor") == "VALOR PROPIO" and pd.isna(fila.get("valor_propio")) and pd.isna(fila.get("valor_fijo_general")):
            return "Falta valor propio"
        if fila.get("modelo_valor") == "VALOR FIJO GENERAL" and pd.isna(fila.get("valor_fijo_general")):
            return "Falta valor fijo"
        if fila.get("modelo_valor") == "A CONVENIR":
            return "A convenir"
        return "Vigente"

    resultado["estado"] = resultado.apply(estado_fila, axis=1)
    resultado.loc[resultado["estado"] != "Vigente", "valor_final"] = None
    resultado["requiere_autorizacion"] = resultado.apply(
        lambda fila: "SI" if _cv_si(fila.get("requiere_autorizacion", "")) or _cv_si(fila.get("autorizacion_entidad", "")) else "NO",
        axis=1,
    )
    resultado["observaciones"] = resultado.apply(
        lambda fila: " · ".join(
            [v for v in [_cv_texto(fila.get("observaciones", "")), _cv_texto(fila.get("observaciones_entidad", ""))] if v]
        ),
        axis=1,
    )
    return resultado.reindex(columns=columnas_salida).sort_values(
        ["empresa", "obra_social", "plan", "codigo"], na_position="last"
    ).reset_index(drop=True)


def _cv_formato_moneda(valor: Any) -> str:
    numero = _cv_numero(valor)
    if numero is None:
        return "—"
    texto = f"{numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"$ {texto}"


def _cv_combinar_por_clave(tipo: str, actual: pd.DataFrame, nuevo: pd.DataFrame, claves: list[str]) -> pd.DataFrame:
    actual = _cv_normalizar_tabla(tipo, actual)
    nuevo = _cv_normalizar_tabla(tipo, nuevo)
    if actual.empty:
        return nuevo
    if nuevo.empty:
        return actual
    combinado = pd.concat([actual, nuevo], ignore_index=True)
    claves_validas = [c for c in claves if c in combinado.columns]
    if claves_validas:
        combinado = combinado.drop_duplicates(subset=claves_validas, keep="last")
    return _cv_normalizar_tabla(tipo, combinado)


def _cv_leer_archivo_tabular(archivo, hoja: str | None = None) -> pd.DataFrame:
    nombre = getattr(archivo, "name", "").lower()
    if hasattr(archivo, "seek"):
        archivo.seek(0)
    if nombre.endswith(".csv"):
        try:
            return pd.read_csv(archivo, sep=None, engine="python", dtype=object)
        except UnicodeDecodeError:
            archivo.seek(0)
            return pd.read_csv(archivo, sep=None, engine="python", dtype=object, encoding="latin-1")
    return pd.read_excel(archivo, sheet_name=hoja or 0, dtype=object, engine="openpyxl")


def _cv_columna_sugerida(columnas: list[str], palabras: list[str], defecto: str = "— No usar —") -> str:
    normalizadas = {c: _convenios_normalizar_texto(c) for c in columnas}
    for palabra in palabras:
        p = _convenios_normalizar_texto(palabra)
        for original, normalizada in normalizadas.items():
            if p == normalizada or p in normalizada:
                return original
    return defecto


def _cv_porcentaje_texto(texto: str):
    import re
    coincidencias = re.findall(r"([+-]?\d+(?:[.,]\d+)?)\s*%", _cv_texto(texto))
    if not coincidencias:
        return 0.0
    try:
        return float(coincidencias[0].replace(",", "."))
    except Exception:
        return 0.0


def _cv_previsualizar_excel_legacy(archivo) -> tuple[dict[str, list[list[Any]]], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    matrices, _ = _convenios_cargar_excel(archivo)
    practicas, directorio, metadata = _convenios_preparar_datos(matrices, "Importación inicial")
    return matrices, practicas, directorio, metadata


def _cv_importar_excel_legacy(
    matrices: dict[str, list[list[Any]]],
    reglas: pd.DataFrame,
    base: dict[str, pd.DataFrame],
    reemplazar: bool = False,
) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    practicas, _, metadata = _convenios_preparar_datos(matrices, "Excel histórico")
    reglas = reglas.copy()
    reglas["convenio"] = reglas["convenio"].astype(str)
    mapa_reglas = {fila["convenio"]: fila for _, fila in reglas.iterrows()}

    # Nomenclador maestro: toma la descripción más completa encontrada para cada código.
    if practicas.empty:
        nuevo_nom = _cv_df_vacio("nomenclador")
    else:
        nom = practicas[["codigo", "descripcion"]].copy()
        nom = nom[nom["codigo"].astype(str).str.strip().ne("")]
        nom["_largo"] = nom["descripcion"].fillna("").astype(str).str.len()
        nom = nom.sort_values(["codigo", "_largo"], ascending=[True, False]).drop_duplicates("codigo")
        nuevo_nom = pd.DataFrame({
            "codigo": nom["codigo"],
            "descripcion": nom["descripcion"],
            "especialidad": "",
            "categoria": "Nomenclador nacional",
            "tipo_practica": "",
            "activo": "SI",
            "vigencia_desde": "",
            "vigencia_hasta": "",
            "fuente": "QUIROFANO CONVENIOS.xlsx",
            "observaciones": "Importado desde planilla histórica",
        })
        nuevo_nom = _cv_normalizar_tabla("nomenclador", nuevo_nom)

    # Valores IPS, solo cuando la hoja IPS realmente contiene importes.
    ips_src = practicas[(practicas["convenio"] == "IPS") & practicas["valor"].notna()].copy() if not practicas.empty else pd.DataFrame()
    if ips_src.empty:
        nuevo_ips = _cv_df_vacio("ips")
    else:
        nuevo_ips = pd.DataFrame({
            "codigo": ips_src["codigo"],
            "valor_ips": ips_src["valor"],
            "vigencia_desde": ips_src["vigencia"].apply(_cv_fecha_iso),
            "vigencia_hasta": "",
            "fuente": "IPS · QUIROFANO CONVENIOS.xlsx",
            "observaciones": "Importado desde hoja IPS",
        })
        nuevo_ips = _cv_normalizar_tabla("ips", nuevo_ips)

    entidades_nuevas = []
    codigos_nuevos = []
    convenios = sorted(set(matrices.keys()) | set(practicas.get("convenio", pd.Series(dtype=str)).astype(str)))
    for convenio in convenios:
        regla = mapa_reglas.get(convenio)
        if regla is None or not bool(regla.get("importar", True)):
            continue
        destino = _cv_texto(regla.get("destino", "VM")).upper()
        empresas = ["VM", "VMR"] if destino == "AMBAS" else [destino]
        empresas = [e for e in empresas if e in _CV_EMPRESAS]
        if not empresas:
            continue

        modelo = _cv_texto(regla.get("modelo_valor", "IPS PURO")).upper() or "IPS PURO"
        porcentaje = _cv_numero(regla.get("porcentaje")) or 0.0
        sub = practicas[practicas["convenio"] == convenio].copy() if not practicas.empty else pd.DataFrame()
        meta = metadata[metadata["convenio"] == convenio] if not metadata.empty else pd.DataFrame()
        vigencia_general = ""
        if not sub.empty and sub["vigencia"].notna().any():
            vigencia_general = _cv_fecha_iso(sub["vigencia"].max())
        notas_meta = _cv_texto(meta.iloc[0].get("notas", "")) if not meta.empty else ""
        correo_meta = _cv_texto(meta.iloc[0].get("emails", "")) if not meta.empty else ""

        for empresa in empresas:
            convenio_id = _cv_id_deterministico("cv", f"{empresa}|{convenio}|")
            entidades_nuevas.append({
                "convenio_id": convenio_id,
                "empresa": empresa,
                "obra_social": convenio,
                "plan": "",
                "modelo_valor": modelo,
                "porcentaje_sobre_ips": porcentaje,
                "valor_fijo_general": None,
                "vigencia_desde": vigencia_general,
                "vigencia_hasta": "",
                "activo": "SI",
                "requiere_autorizacion": "NO",
                "modalidad_facturacion": "",
                "correo": correo_meta,
                "contacto": "",
                "observaciones": notas_meta,
            })

            for _, fila in sub.iterrows():
                valor_propio = fila.get("valor") if modelo in {"VALOR PROPIO", "VALOR FIJO GENERAL"} else None
                codigos_nuevos.append({
                    "convenio_id": convenio_id,
                    "empresa": empresa,
                    "obra_social": convenio,
                    "plan": "",
                    "codigo": fila.get("codigo", ""),
                    "habilitado": "SI",
                    "modelo_valor_override": "",
                    "porcentaje_override": None,
                    "valor_propio": valor_propio,
                    "vigencia_desde": _cv_fecha_iso(fila.get("vigencia")),
                    "vigencia_hasta": "",
                    "requiere_autorizacion": "NO",
                    "modulo": empresa,
                    "observaciones": _cv_texto(fila.get("observaciones", "")),
                })

    nuevas_entidades = _cv_normalizar_tabla("entidades", pd.DataFrame(entidades_nuevas))
    nuevos_codigos = _cv_normalizar_tabla("codigos", pd.DataFrame(codigos_nuevos))

    if reemplazar:
        resultado = {
            "nomenclador": nuevo_nom,
            "ips": nuevo_ips,
            "entidades": nuevas_entidades,
            "codigos": nuevos_codigos,
        }
    else:
        resultado = {
            "nomenclador": _cv_combinar_por_clave(
                "nomenclador", base["nomenclador"], nuevo_nom,
                ["codigo", "vigencia_desde", "vigencia_hasta"],
            ),
            "ips": _cv_combinar_por_clave(
                "ips", base["ips"], nuevo_ips,
                ["codigo", "vigencia_desde", "vigencia_hasta"],
            ),
            "entidades": _cv_combinar_por_clave(
                "entidades", base["entidades"], nuevas_entidades,
                ["empresa", "obra_social", "plan"],
            ),
            "codigos": _cv_combinar_por_clave(
                "codigos", base["codigos"], nuevos_codigos,
                ["empresa", "obra_social", "plan", "codigo", "vigencia_desde", "vigencia_hasta"],
            ),
        }
    conteos = {tipo: len(df) for tipo, df in resultado.items()}
    return resultado, conteos


def _cv_exportar_base(base: dict[str, pd.DataFrame], catalogo: pd.DataFrame, auditoria: dict[str, pd.DataFrame]) -> bytes:
    from io import BytesIO
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        base["nomenclador"].to_excel(writer, sheet_name="Nomenclador_Nacional", index=False)
        base["ips"].to_excel(writer, sheet_name="Valores_IPS", index=False)
        base["entidades"].to_excel(writer, sheet_name="Obras_Sociales", index=False)
        base["codigos"].to_excel(writer, sheet_name="Codigos_Convenidos", index=False)
        catalogo.to_excel(writer, sheet_name="Catalogo_Calculado", index=False)
        for nombre, df in auditoria.items():
            df.to_excel(writer, sheet_name=nombre[:31], index=False)
        for ws in writer.sheets.values():
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for columna in ws.columns:
                letra = columna[0].column_letter
                ancho = min(max(len(str(c.value or "")) for c in columna) + 2, 55)
                ws.column_dimensions[letra].width = max(12, ancho)
    return buffer.getvalue()


def _cv_auditar(base: dict[str, pd.DataFrame], catalogo: pd.DataFrame) -> dict[str, pd.DataFrame]:
    codigos = base["codigos"].copy()
    nomenclador = base["nomenclador"].copy()
    ips = base["ips"].copy()

    cod_nom = set(nomenclador["codigo"].dropna().astype(str))
    sin_nom = codigos[~codigos["codigo"].astype(str).isin(cod_nom)].copy() if not codigos.empty else codigos.copy()

    dependientes_ips = catalogo[catalogo["modelo_valor"].isin(["IPS PURO", "IPS + %"])] if not catalogo.empty else catalogo.copy()
    sin_ips = dependientes_ips[dependientes_ips["valor_ips"].isna()].copy() if not dependientes_ips.empty else dependientes_ips.copy()

    sin_valor_propio = catalogo[
        catalogo["estado"].isin(["Falta valor propio", "Falta valor fijo"])
    ].copy() if not catalogo.empty else catalogo.copy()

    duplicados_cod = codigos[codigos.duplicated(
        subset=["empresa", "obra_social", "plan", "codigo", "vigencia_desde", "vigencia_hasta"],
        keep=False,
    )].copy() if not codigos.empty else codigos.copy()

    duplicados_ips = ips[ips.duplicated(
        subset=["codigo", "vigencia_desde", "vigencia_hasta"], keep=False
    )].copy() if not ips.empty else ips.copy()

    fuera_vigencia = catalogo[catalogo["estado"].str.contains("fuera de vigencia|inactivo", case=False, na=False)].copy() if not catalogo.empty else catalogo.copy()

    return {
        "Sin_nomenclador": sin_nom,
        "Sin_valor_IPS": sin_ips,
        "Sin_valor_propio": sin_valor_propio,
        "Duplicados_codigos": duplicados_cod,
        "Duplicados_IPS": duplicados_ips,
        "Fuera_de_vigencia": fuera_vigencia,
    }


def render_convenios_pro() -> None:
    """Gestión integral y permanente de nomencladores, reglas y aranceles."""
    import plotly.express as px

    st.markdown(
        """
        <style>
        .cv2-hero{padding:1.35rem 1.55rem;border:1px solid rgba(15,118,110,.22);border-radius:22px;
        background:linear-gradient(135deg,rgba(15,118,110,.15),rgba(14,165,233,.07));
        box-shadow:0 16px 38px rgba(15,23,42,.08);margin-bottom:1rem}
        .cv2-hero h2{margin:.35rem 0 0;font-size:1.72rem;letter-spacing:-.025em}
        .cv2-hero p{margin:.45rem 0 0;opacity:.8}.cv2-pill{display:inline-block;padding:.27rem .62rem;
        margin:.12rem .25rem .12rem 0;border-radius:999px;font-size:.77rem;font-weight:750;
        border:1px solid rgba(15,118,110,.24);background:rgba(15,118,110,.08)}
        .cv2-note{padding:.9rem 1rem;border-radius:14px;border-left:4px solid #0f766e;background:rgba(15,118,110,.07)}
        .cv2-card{padding:1rem;border:1px solid rgba(100,116,139,.2);border-radius:16px;background:rgba(255,255,255,.55)}
        </style>
        <div class="cv2-hero">
          <span class="cv2-pill">VERSIÓN 3.1 · GUARDADO PERMANENTE</span>
          <span class="cv2-pill">BASE PERMANENTE EN GOOGLE SHEETS</span>
          <span class="cv2-pill">VM · VITAE MEDICAL</span>
          <span class="cv2-pill">VMR · MEDICINA REPRODUCTIVA</span>
          <span class="cv2-pill">MOTOR IPS Y VALORES PROPIOS</span>
          <h2>🏥 Gestión Corporativa de Convenios · Maestro Permanente</h2>
          <p>Nomenclador nacional, valores IPS históricos, reglas por obra social, códigos habilitados, vigencias y cálculo automático del arancel aplicable.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    base, errores = _cv_cargar_base()
    faltantes = list(errores.keys())

    # Primera apertura: crea automáticamente las cuatro pestañas normalizadas.
    if faltantes and not st.session_state.get("cv_inicializacion_intentada", False):
        st.session_state["cv_inicializacion_intentada"] = True
        try:
            _cv_inicializar_faltantes(faltantes)
            st.rerun()
        except Exception:
            pass

    estado_cols = st.columns([4.5, 1.2, 1.2])
    with estado_cols[0]:
        if not errores:
            st.success(
                "✅ Base permanente conectada. Todo lo que guardes queda fijo en Google Sheets; "
                "no necesitás volver a cargar el Excel en cada ingreso."
            )
        else:
            st.warning(f"Faltan inicializar {len(errores)} pestañas de la base permanente.")
    with estado_cols[1]:
        if st.button("🧱 Reparar base", use_container_width=True, key="cv_reparar_base"):
            try:
                _cv_inicializar_faltantes(list(errores.keys()) or list(_CV_TABLAS.keys()))
                st.session_state["cv_inicializacion_intentada"] = False
                st.success("Base creada correctamente.")
                st.rerun()
            except Exception as exc:
                st.error(f"No se pudo crear la base: {exc}")
    with estado_cols[2]:
        if st.button("🔄 Actualizar", use_container_width=True, key="cv_actualizar_v2"):
            st.cache_data.clear()
            st.rerun()

    if errores:
        with st.expander("Ver diagnóstico técnico", expanded=False):
            diagnostico = pd.DataFrame([
                {
                    "pestaña": _CV_TABLAS[tipo]["sheet"],
                    "estado": "Pendiente",
                    "detalle": detalle[:300],
                }
                for tipo, detalle in errores.items()
            ])
            st.dataframe(diagnostico, use_container_width=True, hide_index=True)

    fecha_consulta = st.date_input(
        "Fecha de cálculo y vigencia",
        value=date.today(),
        help="El sistema busca la versión del nomenclador, el valor IPS y el convenio que estaban vigentes en esta fecha.",
        key="cv_fecha_global",
    )
    catalogo = _cv_calcular_catalogo(base, fecha_consulta)
    auditoria = _cv_auditar(base, catalogo)

    vigentes = catalogo[catalogo["estado"] == "Vigente"] if not catalogo.empty else catalogo
    total_os = base["entidades"]["convenio_id"].nunique() if not base["entidades"].empty else 0
    total_codigos = len(base["codigos"])
    total_nom = base["nomenclador"]["codigo"].nunique() if not base["nomenclador"].empty else 0
    total_ips = base["ips"]["codigo"].nunique() if not base["ips"].empty else 0
    alertas = sum(len(df) for df in auditoria.values())

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Nomenclador nacional", f"{total_nom:,}".replace(",", "."))
    m2.metric("Códigos con IPS", f"{total_ips:,}".replace(",", "."))
    m3.metric("Convenios / planes", f"{total_os:,}".replace(",", "."))
    m4.metric("Códigos convenidos", f"{total_codigos:,}".replace(",", "."))
    m5.metric("Vigentes hoy", f"{len(vigentes):,}".replace(",", "."))
    m6.metric("Alertas de datos", f"{alertas:,}".replace(",", "."))

    tabs = st.tabs([
        "📊 Tablero ejecutivo",
        "🔎 Consulta y cálculo",
        "📚 Nomenclador nacional",
        "💲 Valores IPS",
        "🏥 Obras sociales y reglas",
        "🧾 Códigos convenidos",
        "📥 Carga inicial / masiva",
        "🧪 Auditoría y exportación",
    ])

    # ---------------------------------------------------------
    # 1. TABLERO EJECUTIVO
    # ---------------------------------------------------------
    with tabs[0]:
        st.subheader("Estado operativo por empresa")
        if catalogo.empty:
            st.info("Todavía no hay códigos convenidos. Cargá primero el nomenclador, las obras sociales y sus códigos.")
        else:
            resumen = (
                catalogo.groupby(["empresa", "obra_social", "estado"], dropna=False)
                .size().reset_index(name="cantidad")
            )
            resumen_total = (
                catalogo.groupby(["empresa", "obra_social"], dropna=False)
                .agg(
                    Codigos=("codigo", "count"),
                    Vigentes=("estado", lambda s: int((s == "Vigente").sum())),
                    Valor_promedio=("valor_final", "mean"),
                ).reset_index()
            )
            resumen_total["Cobertura %"] = (
                resumen_total["Vigentes"] / resumen_total["Codigos"].replace(0, pd.NA) * 100
            ).fillna(0)
            st.dataframe(
                resumen_total.rename(columns={
                    "empresa": "Empresa", "obra_social": "Obra social",
                    "Valor_promedio": "Valor promedio",
                }),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Cobertura %": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f%%"),
                    "Valor promedio": st.column_config.NumberColumn(format="$ %.2f"),
                },
            )
            graf = resumen_total[resumen_total["Codigos"] > 0].copy()
            if not graf.empty:
                fig = px.bar(
                    graf.sort_values("Cobertura %"), x="Cobertura %", y="obra_social",
                    color="empresa", orientation="h", barmode="group",
                    hover_data=["Codigos", "Vigentes"],
                    title="Cobertura vigente de códigos por obra social",
                )
                fig.update_layout(height=max(420, len(graf) * 38), xaxis_range=[0, 105])
                st.plotly_chart(fig, use_container_width=True)

            problemas = catalogo[catalogo["estado"] != "Vigente"]
            if problemas.empty:
                st.success("No hay alertas para la fecha seleccionada.")
            else:
                st.markdown("#### Prioridades de corrección")
                prioridades = problemas.groupby(["empresa", "obra_social", "estado"]).size().reset_index(name="Casos")
                st.dataframe(prioridades, use_container_width=True, hide_index=True)

    # ---------------------------------------------------------
    # 2. CONSULTA Y CÁLCULO
    # ---------------------------------------------------------
    with tabs[1]:
        st.subheader("Buscador corporativo y calculador de arancel")
        if catalogo.empty:
            st.info("No hay catálogo calculado todavía.")
        else:
            c1, c2, c3, c4 = st.columns([1, 1.8, 1.4, 2.2])
            with c1:
                empresa_f = st.selectbox("Empresa", ["Todas"] + _CV_EMPRESAS, key="cv_cons_empresa")
            datos_empresa = catalogo if empresa_f == "Todas" else catalogo[catalogo["empresa"] == empresa_f]
            obras = sorted(datos_empresa["obra_social"].dropna().astype(str).unique().tolist())
            with c2:
                obra_f = st.selectbox("Obra social", ["Todas"] + obras, key="cv_cons_obra")
            datos_obra = datos_empresa if obra_f == "Todas" else datos_empresa[datos_empresa["obra_social"] == obra_f]
            planes = sorted([p for p in datos_obra["plan"].dropna().astype(str).unique().tolist() if p])
            with c3:
                plan_f = st.selectbox("Plan", ["Todos"] + planes, key="cv_cons_plan")
            with c4:
                buscar = st.text_input("Código o práctica", placeholder="Ej.: 110501, histeroscopia, biopsia...", key="cv_cons_buscar")

            vista = datos_obra.copy()
            if plan_f != "Todos":
                vista = vista[vista["plan"] == plan_f]
            if buscar.strip():
                q = _convenios_normalizar_texto(buscar)
                bolsa = (
                    vista["codigo"].fillna("").astype(str) + " " +
                    vista["descripcion"].fillna("").astype(str) + " " +
                    vista["especialidad"].fillna("").astype(str)
                ).apply(_convenios_normalizar_texto)
                mascara = pd.Series(True, index=vista.index)
                for token in q.split():
                    mascara &= bolsa.str.contains(token, regex=False)
                vista = vista[mascara]

            st.caption(f"{len(vista):,} resultados para {pd.to_datetime(fecha_consulta).strftime('%d/%m/%Y')}".replace(",", "."))
            st.dataframe(
                vista.rename(columns={
                    "empresa": "Empresa", "obra_social": "Obra social", "plan": "Plan",
                    "codigo": "Código", "descripcion": "Práctica", "modelo_valor": "Regla",
                    "valor_ips": "IPS base", "porcentaje": "%", "valor_propio": "Valor propio",
                    "valor_final": "Valor calculado", "estado": "Estado",
                    "requiere_autorizacion": "Autorización", "modulo": "Módulo",
                })[[
                    "Empresa", "Obra social", "Plan", "Código", "Práctica", "Regla",
                    "IPS base", "%", "Valor propio", "Valor calculado", "Estado",
                    "Autorización", "Módulo",
                ]],
                use_container_width=True,
                hide_index=True,
                height=560,
                column_config={
                    "Práctica": st.column_config.TextColumn(width="large"),
                    "IPS base": st.column_config.NumberColumn(format="$ %.2f"),
                    "Valor propio": st.column_config.NumberColumn(format="$ %.2f"),
                    "Valor calculado": st.column_config.NumberColumn(format="$ %.2f"),
                },
            )

            opciones = vista["registro_id"].tolist()
            if opciones:
                etiquetas = {
                    fila["registro_id"]: f"{fila['empresa']} · {fila['obra_social']} · {fila['codigo']} · {fila['descripcion'][:70]}"
                    for _, fila in vista.iterrows()
                }
                seleccionado = st.selectbox(
                    "Abrir ficha de cálculo",
                    opciones,
                    format_func=lambda x: etiquetas.get(x, x),
                    key="cv_ficha_calculo",
                )
                fila = vista[vista["registro_id"] == seleccionado].iloc[0]
                f1, f2, f3, f4 = st.columns(4)
                f1.metric("Valor IPS vigente", _cv_formato_moneda(fila["valor_ips"]))
                f2.metric("Regla aplicada", fila["modelo_valor"])
                f3.metric("Porcentaje", f"{fila['porcentaje']:.2f}%")
                f4.metric("Arancel final", _cv_formato_moneda(fila["valor_final"]))
                st.info(
                    f"**{fila['codigo']} — {fila['descripcion']}**  \n"
                    f"Estado: **{fila['estado']}** · Empresa: **{fila['empresa']}** · "
                    f"Obra social: **{fila['obra_social']}** · Plan: **{fila['plan'] or 'General'}**"
                )

    # ---------------------------------------------------------
    # 3. NOMENCLADOR NACIONAL
    # ---------------------------------------------------------
    with tabs[2]:
        st.subheader("Maestro del nomenclador nacional")
        st.caption("Cada código puede conservar versiones históricas mediante Vigencia desde / hasta.")
        n1, n2, n3 = st.columns([2.4, 1.2, 1.2])
        with n1:
            q_nom = st.text_input("Buscar código o descripción", key="cv_nom_buscar")
        with n2:
            solo_activos = st.checkbox("Solo activos", value=False, key="cv_nom_activos")
        with n3:
            st.metric("Registros", len(base["nomenclador"]))
        vista_nom = base["nomenclador"].copy()
        if solo_activos:
            vista_nom = vista_nom[vista_nom["activo"].apply(_cv_si)]
        if q_nom.strip():
            q = _convenios_normalizar_texto(q_nom)
            bolsa = (vista_nom["codigo"] + " " + vista_nom["descripcion"] + " " + vista_nom["especialidad"]).apply(_convenios_normalizar_texto)
            vista_nom = vista_nom[bolsa.str.contains(q, regex=False)]

        editor_nom = vista_nom.copy()
        for c in ["vigencia_desde", "vigencia_hasta"]:
            editor_nom[c] = pd.to_datetime(editor_nom[c], errors="coerce").dt.date
        editado_nom = st.data_editor(
            editor_nom,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            height=520,
            key="cv_editor_nomenclador",
            disabled=["registro_id"],
            column_config={
                "registro_id": st.column_config.TextColumn("ID", width="small"),
                "codigo": st.column_config.TextColumn("Código", required=True),
                "descripcion": st.column_config.TextColumn("Descripción", width="large", required=True),
                "especialidad": st.column_config.TextColumn("Especialidad"),
                "categoria": st.column_config.TextColumn("Categoría"),
                "tipo_practica": st.column_config.TextColumn("Tipo"),
                "activo": st.column_config.SelectboxColumn("Activo", options=_CV_SI_NO, required=True),
                "vigencia_desde": st.column_config.DateColumn("Vigencia desde", format="DD/MM/YYYY"),
                "vigencia_hasta": st.column_config.DateColumn("Vigencia hasta", format="DD/MM/YYYY"),
                "fuente": st.column_config.TextColumn("Fuente"),
                "observaciones": st.column_config.TextColumn("Observaciones", width="large"),
            },
        )
        if st.button("💾 Guardar nomenclador", type="primary", key="cv_guardar_nom"):
            try:
                combinado = _cv_reemplazar_vista("nomenclador", base["nomenclador"], vista_nom, editado_nom)
                _cv_guardar_tabla("nomenclador", combinado)
                st.success("Nomenclador guardado en la base permanente.")
                st.rerun()
            except Exception as exc:
                st.error(f"No se pudo guardar: {exc}")

        with st.expander("📥 Importar nomenclador nacional desde Excel o CSV", expanded=False):
            archivo_nom = st.file_uploader("Archivo del nomenclador", type=["xlsx", "xls", "csv"], key="cv_upload_nom")
            if archivo_nom is not None:
                hojas_nom = [""]
                if getattr(archivo_nom, "name", "").lower().endswith((".xlsx", ".xls")):
                    archivo_nom.seek(0)
                    hojas_nom = pd.ExcelFile(archivo_nom, engine="openpyxl").sheet_names
                    hoja_nom = st.selectbox("Hoja", hojas_nom, key="cv_hoja_nom")
                else:
                    hoja_nom = None
                raw_nom = _cv_leer_archivo_tabular(archivo_nom, hoja_nom)
                columnas = raw_nom.columns.astype(str).tolist()
                opciones = ["— No usar —"] + columnas
                c1, c2, c3, c4 = st.columns(4)
                col_codigo = c1.selectbox("Columna código", opciones, index=opciones.index(_cv_columna_sugerida(columnas, ["codigo", "cod"])), key="cv_map_nom_cod")
                col_desc = c2.selectbox("Columna descripción", opciones, index=opciones.index(_cv_columna_sugerida(columnas, ["descripcion", "practica"])), key="cv_map_nom_desc")
                col_esp = c3.selectbox("Especialidad", opciones, index=opciones.index(_cv_columna_sugerida(columnas, ["especialidad"])), key="cv_map_nom_esp")
                col_vig = c4.selectbox("Vigencia desde", opciones, index=opciones.index(_cv_columna_sugerida(columnas, ["vigencia", "fecha"])), key="cv_map_nom_vig")
                st.dataframe(raw_nom.head(20), use_container_width=True, hide_index=True)
                if st.button("Incorporar al nomenclador", type="primary", key="cv_importar_nom"):
                    if col_codigo == "— No usar —" or col_desc == "— No usar —":
                        st.error("Seleccioná las columnas Código y Descripción.")
                    else:
                        nuevo = pd.DataFrame({
                            "codigo": raw_nom[col_codigo],
                            "descripcion": raw_nom[col_desc],
                            "especialidad": raw_nom[col_esp] if col_esp != "— No usar —" else "",
                            "categoria": "Nomenclador nacional",
                            "tipo_practica": "",
                            "activo": "SI",
                            "vigencia_desde": raw_nom[col_vig] if col_vig != "— No usar —" else "",
                            "vigencia_hasta": "",
                            "fuente": getattr(archivo_nom, "name", "Importación"),
                            "observaciones": "",
                        })
                        combinado = _cv_combinar_por_clave(
                            "nomenclador", base["nomenclador"], nuevo,
                            ["codigo", "vigencia_desde", "vigencia_hasta"],
                        )
                        _cv_guardar_tabla("nomenclador", combinado)
                        st.success(f"Se incorporaron {len(_cv_normalizar_tabla('nomenclador', nuevo))} registros.")
                        st.rerun()

    # ---------------------------------------------------------
    # 4. VALORES IPS
    # ---------------------------------------------------------
    with tabs[3]:
        st.subheader("Historial maestro de valores IPS")
        st.caption("Para actualizar IPS, agregá una nueva fila con una nueva vigencia. No borres el valor anterior: así conservás la historia.")
        i1, i2 = st.columns([3, 1])
        with i1:
            q_ips = st.text_input("Buscar código", key="cv_ips_buscar")
        with i2:
            st.metric("Registros históricos", len(base["ips"]))
        vista_ips = base["ips"].copy()
        if q_ips.strip():
            vista_ips = vista_ips[vista_ips["codigo"].astype(str).str.contains(q_ips.strip(), case=False, regex=False)]
        editor_ips = vista_ips.copy()
        for c in ["vigencia_desde", "vigencia_hasta"]:
            editor_ips[c] = pd.to_datetime(editor_ips[c], errors="coerce").dt.date
        editado_ips = st.data_editor(
            editor_ips,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            height=520,
            key="cv_editor_ips",
            disabled=["registro_id"],
            column_config={
                "registro_id": st.column_config.TextColumn("ID", width="small"),
                "codigo": st.column_config.TextColumn("Código", required=True),
                "valor_ips": st.column_config.NumberColumn("Valor IPS", format="$ %.2f", min_value=0.0, required=True),
                "vigencia_desde": st.column_config.DateColumn("Vigencia desde", format="DD/MM/YYYY", required=True),
                "vigencia_hasta": st.column_config.DateColumn("Vigencia hasta", format="DD/MM/YYYY"),
                "fuente": st.column_config.TextColumn("Fuente"),
                "observaciones": st.column_config.TextColumn("Observaciones", width="large"),
            },
        )
        if st.button("💾 Guardar valores IPS", type="primary", key="cv_guardar_ips"):
            try:
                combinado = _cv_reemplazar_vista("ips", base["ips"], vista_ips, editado_ips)
                _cv_guardar_tabla("ips", combinado)
                st.success("Valores IPS guardados.")
                st.rerun()
            except Exception as exc:
                st.error(f"No se pudo guardar: {exc}")

        with st.expander("📥 Importar valores IPS desde Excel o CSV", expanded=False):
            archivo_ips = st.file_uploader("Archivo de valores IPS", type=["xlsx", "xls", "csv"], key="cv_upload_ips")
            if archivo_ips is not None:
                if getattr(archivo_ips, "name", "").lower().endswith((".xlsx", ".xls")):
                    archivo_ips.seek(0)
                    hojas_ips = pd.ExcelFile(archivo_ips, engine="openpyxl").sheet_names
                    hoja_ips = st.selectbox("Hoja", hojas_ips, key="cv_hoja_ips")
                else:
                    hoja_ips = None
                raw_ips = _cv_leer_archivo_tabular(archivo_ips, hoja_ips)
                columnas = raw_ips.columns.astype(str).tolist()
                opciones = ["— No usar —"] + columnas
                c1, c2, c3, c4 = st.columns(4)
                col_codigo = c1.selectbox("Columna código", opciones, index=opciones.index(_cv_columna_sugerida(columnas, ["codigo", "cod"])), key="cv_map_ips_cod")
                col_valor = c2.selectbox("Columna valor", opciones, index=opciones.index(_cv_columna_sugerida(columnas, ["valor ips", "valor", "importe"])), key="cv_map_ips_val")
                col_desde = c3.selectbox("Vigencia desde", opciones, index=opciones.index(_cv_columna_sugerida(columnas, ["vigencia", "fecha", "desde"])), key="cv_map_ips_desde")
                col_hasta = c4.selectbox("Vigencia hasta", opciones, index=opciones.index(_cv_columna_sugerida(columnas, ["hasta"])), key="cv_map_ips_hasta")
                st.dataframe(raw_ips.head(20), use_container_width=True, hide_index=True)
                if st.button("Incorporar valores IPS", type="primary", key="cv_importar_ips"):
                    if col_codigo == "— No usar —" or col_valor == "— No usar —":
                        st.error("Seleccioná Código y Valor.")
                    else:
                        nuevo = pd.DataFrame({
                            "codigo": raw_ips[col_codigo],
                            "valor_ips": raw_ips[col_valor],
                            "vigencia_desde": raw_ips[col_desde] if col_desde != "— No usar —" else date.today(),
                            "vigencia_hasta": raw_ips[col_hasta] if col_hasta != "— No usar —" else "",
                            "fuente": getattr(archivo_ips, "name", "Importación IPS"),
                            "observaciones": "",
                        })
                        combinado = _cv_combinar_por_clave(
                            "ips", base["ips"], nuevo,
                            ["codigo", "vigencia_desde", "vigencia_hasta"],
                        )
                        _cv_guardar_tabla("ips", combinado)
                        st.success(f"Se incorporaron {len(_cv_normalizar_tabla('ips', nuevo))} valores.")
                        st.rerun()

    # ---------------------------------------------------------
    # 5. OBRAS SOCIALES Y REGLAS
    # ---------------------------------------------------------
    with tabs[4]:
        st.subheader("Configuración de obras sociales, planes y reglas de valorización")
        st.markdown(
            """
            <div class="cv2-note"><b>Cómo funciona:</b> definís una regla general por obra social y plan.
            Puede ser IPS puro, IPS más o menos un porcentaje, valor propio, valor fijo general o a convenir.
            Luego, en un código puntual, podés crear una excepción sin cambiar el resto del convenio.</div>
            """,
            unsafe_allow_html=True,
        )
        e1, e2, e3 = st.columns([1.1, 1.8, 2.2])
        with e1:
            emp_ent = st.selectbox("Empresa", ["Todas"] + _CV_EMPRESAS, key="cv_ent_empresa")
        entidades_vista = base["entidades"].copy()
        if emp_ent != "Todas":
            entidades_vista = entidades_vista[entidades_vista["empresa"] == emp_ent]
        obras_ent = sorted(entidades_vista["obra_social"].dropna().astype(str).unique().tolist())
        with e2:
            os_ent = st.selectbox("Obra social", ["Todas"] + obras_ent, key="cv_ent_os")
        if os_ent != "Todas":
            entidades_vista = entidades_vista[entidades_vista["obra_social"] == os_ent]
        with e3:
            st.caption("Ejemplo: Avalian VM = IPS + 15%; OSDE VM = Valor propio.")

        editor_ent = entidades_vista.copy()
        for c in ["vigencia_desde", "vigencia_hasta"]:
            editor_ent[c] = pd.to_datetime(editor_ent[c], errors="coerce").dt.date
        editado_ent = st.data_editor(
            editor_ent,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            height=520,
            key="cv_editor_entidades",
            disabled=["registro_id", "convenio_id"],
            column_config={
                "registro_id": st.column_config.TextColumn("ID registro", width="small"),
                "convenio_id": st.column_config.TextColumn("ID convenio", width="small"),
                "empresa": st.column_config.SelectboxColumn("Empresa", options=_CV_EMPRESAS, required=True),
                "obra_social": st.column_config.TextColumn("Obra social", required=True),
                "plan": st.column_config.TextColumn("Plan"),
                "modelo_valor": st.column_config.SelectboxColumn("Regla de valor", options=_CV_MODELOS, required=True),
                "porcentaje_sobre_ips": st.column_config.NumberColumn("% sobre IPS", format="%.2f"),
                "valor_fijo_general": st.column_config.NumberColumn("Valor fijo general", format="$ %.2f"),
                "vigencia_desde": st.column_config.DateColumn("Vigencia desde", format="DD/MM/YYYY"),
                "vigencia_hasta": st.column_config.DateColumn("Vigencia hasta", format="DD/MM/YYYY"),
                "activo": st.column_config.SelectboxColumn("Activo", options=_CV_SI_NO, required=True),
                "requiere_autorizacion": st.column_config.SelectboxColumn("Autorización", options=_CV_SI_NO),
                "modalidad_facturacion": st.column_config.TextColumn("Facturación", width="medium"),
                "correo": st.column_config.TextColumn("Correo"),
                "contacto": st.column_config.TextColumn("Contacto"),
                "observaciones": st.column_config.TextColumn("Observaciones", width="large"),
            },
        )
        if st.button("💾 Guardar obras sociales y reglas", type="primary", key="cv_guardar_entidades"):
            try:
                combinado = _cv_reemplazar_vista("entidades", base["entidades"], entidades_vista, editado_ent)
                guardado = _cv_guardar_tabla("entidades", combinado)
                # Sincroniza los datos descriptivos de cada código con su convenio_id.
                mapa = guardado.set_index("convenio_id")[["empresa", "obra_social", "plan"]].to_dict("index") if not guardado.empty else {}
                cod_sync = base["codigos"].copy()
                for idx, fila in cod_sync.iterrows():
                    datos = mapa.get(fila["convenio_id"])
                    if datos:
                        for campo in ["empresa", "obra_social", "plan"]:
                            cod_sync.at[idx, campo] = datos[campo]
                if not cod_sync.empty:
                    _cv_guardar_tabla("codigos", cod_sync)
                st.success("Configuración guardada.")
                st.rerun()
            except Exception as exc:
                st.error(f"No se pudo guardar: {exc}")

    # ---------------------------------------------------------
    # 6. CÓDIGOS CONVENIDOS
    # ---------------------------------------------------------
    with tabs[5]:
        st.subheader("Códigos habilitados por obra social, empresa y plan")
        if base["entidades"].empty:
            st.warning("Primero creá al menos una obra social en la pestaña anterior.")
        else:
            c1, c2, c3, c4 = st.columns([1, 1.7, 1.3, 2.2])
            empresa_cod = c1.selectbox("Empresa", ["Todas"] + _CV_EMPRESAS, key="cv_cod_empresa")
            cod_vista = base["codigos"].copy()
            if empresa_cod != "Todas":
                cod_vista = cod_vista[cod_vista["empresa"] == empresa_cod]
            obras_cod = sorted(cod_vista["obra_social"].dropna().astype(str).unique().tolist())
            obra_cod = c2.selectbox("Obra social", ["Todas"] + obras_cod, key="cv_cod_os")
            if obra_cod != "Todas":
                cod_vista = cod_vista[cod_vista["obra_social"] == obra_cod]
            plan_cod = c3.text_input("Plan contiene", key="cv_cod_plan")
            if plan_cod.strip():
                cod_vista = cod_vista[cod_vista["plan"].astype(str).str.contains(plan_cod, case=False, regex=False)]
            buscar_cod = c4.text_input("Código o nota", key="cv_cod_buscar")
            if buscar_cod.strip():
                q = _convenios_normalizar_texto(buscar_cod)
                bolsa = (cod_vista["codigo"] + " " + cod_vista["observaciones"]).apply(_convenios_normalizar_texto)
                cod_vista = cod_vista[bolsa.str.contains(q, regex=False)]

            editor_cod = cod_vista.copy()
            for c in ["vigencia_desde", "vigencia_hasta"]:
                editor_cod[c] = pd.to_datetime(editor_cod[c], errors="coerce").dt.date
            editado_cod = st.data_editor(
                editor_cod,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                height=520,
                key="cv_editor_codigos",
                disabled=["registro_id", "convenio_id"],
                column_config={
                    "registro_id": st.column_config.TextColumn("ID", width="small"),
                    "convenio_id": st.column_config.TextColumn("Convenio ID", width="small"),
                    "empresa": st.column_config.SelectboxColumn("Empresa", options=_CV_EMPRESAS, required=True),
                    "obra_social": st.column_config.TextColumn("Obra social", required=True),
                    "plan": st.column_config.TextColumn("Plan"),
                    "codigo": st.column_config.TextColumn("Código", required=True),
                    "habilitado": st.column_config.SelectboxColumn("Habilitado", options=_CV_SI_NO, required=True),
                    "modelo_valor_override": st.column_config.SelectboxColumn("Excepción de regla", options=[""] + _CV_MODELOS),
                    "porcentaje_override": st.column_config.NumberColumn("% excepción", format="%.2f"),
                    "valor_propio": st.column_config.NumberColumn("Valor propio", format="$ %.2f"),
                    "vigencia_desde": st.column_config.DateColumn("Vigencia desde", format="DD/MM/YYYY"),
                    "vigencia_hasta": st.column_config.DateColumn("Vigencia hasta", format="DD/MM/YYYY"),
                    "requiere_autorizacion": st.column_config.SelectboxColumn("Autorización", options=_CV_SI_NO),
                    "modulo": st.column_config.TextColumn("Módulo / práctica"),
                    "observaciones": st.column_config.TextColumn("Observaciones", width="large"),
                },
            )
            if st.button("💾 Guardar códigos convenidos", type="primary", key="cv_guardar_codigos"):
                try:
                    combinado = _cv_reemplazar_vista("codigos", base["codigos"], cod_vista, editado_cod)
                    _cv_guardar_tabla("codigos", combinado)
                    st.success("Códigos convenidos guardados.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"No se pudo guardar: {exc}")

            with st.expander("📥 Cargar masivamente los códigos de una obra social", expanded=False):
                entidades_op = base["entidades"].copy()
                opciones_ent = entidades_op["convenio_id"].tolist()
                etiquetas_ent = {
                    fila["convenio_id"]: f"{fila['empresa']} · {fila['obra_social']} · {fila['plan'] or 'General'} · {fila['modelo_valor']}"
                    for _, fila in entidades_op.iterrows()
                }
                convenio_elegido = st.selectbox(
                    "Destino",
                    opciones_ent,
                    format_func=lambda x: etiquetas_ent.get(x, x),
                    key="cv_cod_destino",
                )
                archivo_cod = st.file_uploader("Excel o CSV con los códigos", type=["xlsx", "xls", "csv"], key="cv_upload_codigos")
                if archivo_cod is not None and convenio_elegido:
                    if getattr(archivo_cod, "name", "").lower().endswith((".xlsx", ".xls")):
                        archivo_cod.seek(0)
                        hojas_cod = pd.ExcelFile(archivo_cod, engine="openpyxl").sheet_names
                        hoja_cod = st.selectbox("Hoja", hojas_cod, key="cv_hoja_codigos")
                    else:
                        hoja_cod = None
                    raw_cod = _cv_leer_archivo_tabular(archivo_cod, hoja_cod)
                    columnas = raw_cod.columns.astype(str).tolist()
                    opciones = ["— No usar —"] + columnas
                    c1, c2, c3, c4 = st.columns(4)
                    col_codigo = c1.selectbox("Código", opciones, index=opciones.index(_cv_columna_sugerida(columnas, ["codigo", "cod"])), key="cv_map_cod_cod")
                    col_valor = c2.selectbox("Valor propio", opciones, index=opciones.index(_cv_columna_sugerida(columnas, ["valor", "importe"])), key="cv_map_cod_val")
                    col_desde = c3.selectbox("Vigencia desde", opciones, index=opciones.index(_cv_columna_sugerida(columnas, ["vigencia", "fecha", "desde"])), key="cv_map_cod_desde")
                    col_obs = c4.selectbox("Observaciones", opciones, index=opciones.index(_cv_columna_sugerida(columnas, ["observaciones", "nota"])), key="cv_map_cod_obs")
                    st.dataframe(raw_cod.head(20), use_container_width=True, hide_index=True)
                    if st.button("Incorporar códigos al convenio", type="primary", key="cv_importar_codigos"):
                        if col_codigo == "— No usar —":
                            st.error("Seleccioná la columna Código.")
                        else:
                            ent = entidades_op[entidades_op["convenio_id"] == convenio_elegido].iloc[0]
                            nuevo = pd.DataFrame({
                                "convenio_id": convenio_elegido,
                                "empresa": ent["empresa"],
                                "obra_social": ent["obra_social"],
                                "plan": ent["plan"],
                                "codigo": raw_cod[col_codigo],
                                "habilitado": "SI",
                                "modelo_valor_override": "",
                                "porcentaje_override": None,
                                "valor_propio": raw_cod[col_valor] if col_valor != "— No usar —" else None,
                                "vigencia_desde": raw_cod[col_desde] if col_desde != "— No usar —" else ent["vigencia_desde"],
                                "vigencia_hasta": "",
                                "requiere_autorizacion": ent["requiere_autorizacion"],
                                "modulo": ent["empresa"],
                                "observaciones": raw_cod[col_obs] if col_obs != "— No usar —" else "",
                            })
                            combinado = _cv_combinar_por_clave(
                                "codigos", base["codigos"], nuevo,
                                ["empresa", "obra_social", "plan", "codigo", "vigencia_desde", "vigencia_hasta"],
                            )
                            _cv_guardar_tabla("codigos", combinado)
                            st.success(f"Se incorporaron {len(_cv_normalizar_tabla('codigos', nuevo))} códigos.")
                            st.rerun()

    # ---------------------------------------------------------
    # 7. CARGA INICIAL / MIGRACIÓN DEL EXCEL HISTÓRICO
    # ---------------------------------------------------------
    with tabs[6]:
        st.subheader("Carga inicial del archivo QUIROFANO CONVENIOS.xlsx")
        st.markdown(
            """
            <div class="cv2-note"><b>Esta carga se hace una sola vez.</b> El sistema transforma las pestañas antiguas
            en cuatro tablas maestras y las guarda en Google Sheets. Después, el Excel deja de ser necesario para operar.</div>
            """,
            unsafe_allow_html=True,
        )
        archivo_fijo = _convenios_buscar_archivo_fijo()
        fuente_importacion = st.radio(
            "Origen",
            ["Archivo fijo del repositorio", "Subir archivo ahora"],
            horizontal=True,
            key="cv_origen_import",
        )
        archivo_legacy = None
        if fuente_importacion == "Archivo fijo del repositorio":
            if archivo_fijo is None:
                st.warning("No se encontró data/QUIROFANO CONVENIOS.xlsx. Podés elegir Subir archivo ahora.")
            else:
                st.success(f"Archivo detectado: {archivo_fijo.name}")
                archivo_legacy = archivo_fijo
        else:
            archivo_legacy = st.file_uploader(
                "Seleccioná QUIROFANO CONVENIOS.xlsx",
                type=["xlsx"],
                key="cv_upload_legacy",
            )

        if archivo_legacy is not None:
            try:
                matrices_legacy, practicas_legacy, _, metadata_legacy = _cv_previsualizar_excel_legacy(archivo_legacy)
                filas_reglas = []
                for convenio in sorted(matrices_legacy.keys()):
                    sub = practicas_legacy[practicas_legacy["convenio"] == convenio] if not practicas_legacy.empty else pd.DataFrame()
                    meta = metadata_legacy[metadata_legacy["convenio"] == convenio] if not metadata_legacy.empty else pd.DataFrame()
                    texto_meta = " ".join([
                        _cv_texto(meta.iloc[0].get("notas", "")) if not meta.empty else "",
                        _cv_texto(meta.iloc[0].get("reglas_porcentuales", "")) if not meta.empty else "",
                    ])
                    porcentaje = _cv_porcentaje_texto(texto_meta)
                    menciona_ips = (not meta.empty and bool(meta.iloc[0].get("menciona_ips", False))) or convenio == "IPS"
                    if convenio == "IPS":
                        modelo = "IPS PURO"
                    elif menciona_ips and porcentaje != 0:
                        modelo = "IPS + %"
                    elif menciona_ips:
                        modelo = "IPS PURO"
                    elif not sub.empty and sub["valor"].notna().any():
                        modelo = "VALOR PROPIO"
                    else:
                        modelo = "IPS PURO"
                    filas_reglas.append({
                        "importar": convenio != "ACLISASA",
                        "convenio": convenio,
                        "destino": "VM",
                        "modelo_valor": modelo,
                        "porcentaje": porcentaje,
                        "practicas_detectadas": len(sub),
                    })
                reglas_df = pd.DataFrame(filas_reglas)
                st.markdown("#### Definí a qué empresa pertenece cada pestaña")
                reglas_editadas = st.data_editor(
                    reglas_df,
                    use_container_width=True,
                    hide_index=True,
                    key="cv_reglas_import_legacy",
                    disabled=["convenio", "practicas_detectadas"],
                    column_config={
                        "importar": st.column_config.CheckboxColumn("Importar"),
                        "convenio": st.column_config.TextColumn("Hoja / convenio"),
                        "destino": st.column_config.SelectboxColumn("Destino", options=["VM", "VMR", "AMBAS", "OMITIR"]),
                        "modelo_valor": st.column_config.SelectboxColumn("Regla sugerida", options=_CV_MODELOS),
                        "porcentaje": st.column_config.NumberColumn("% IPS", format="%.2f"),
                        "practicas_detectadas": st.column_config.NumberColumn("Prácticas"),
                    },
                )
                reemplazar = st.checkbox(
                    "Reemplazar toda la base actual por esta importación",
                    value=False,
                    help="Dejalo desmarcado para agregar y actualizar sin borrar lo ya cargado.",
                    key="cv_reemplazar_legacy",
                )
                st.caption(f"Se detectaron {len(practicas_legacy):,} filas de prácticas en {len(matrices_legacy)} pestañas.".replace(",", "."))
                if st.button("🚀 Migrar y guardar permanentemente", type="primary", key="cv_migrar_legacy"):
                    reglas_editadas["importar"] = reglas_editadas.apply(
                        lambda fila: bool(fila["importar"]) and fila["destino"] != "OMITIR", axis=1
                    )
                    resultado, conteos = _cv_importar_excel_legacy(
                        matrices_legacy, reglas_editadas, base, reemplazar=reemplazar
                    )
                    for tipo in ["nomenclador", "ips", "entidades", "codigos"]:
                        _cv_guardar_tabla(tipo, resultado[tipo])
                    st.success(
                        "Migración terminada: "
                        f"{conteos['nomenclador']} nomenclador · {conteos['ips']} valores IPS · "
                        f"{conteos['entidades']} convenios/planes · {conteos['codigos']} códigos convenidos."
                    )
                    st.rerun()
            except Exception as exc:
                st.error(f"No se pudo interpretar el Excel: {exc}")

    # ---------------------------------------------------------
    # 8. AUDITORÍA Y EXPORTACIÓN
    # ---------------------------------------------------------
    with tabs[7]:
        st.subheader("Auditoría de integridad y respaldo corporativo")
        a1, a2, a3, a4, a5, a6 = st.columns(6)
        a1.metric("Sin nomenclador", len(auditoria["Sin_nomenclador"]))
        a2.metric("Sin valor IPS", len(auditoria["Sin_valor_IPS"]))
        a3.metric("Sin valor propio", len(auditoria["Sin_valor_propio"]))
        a4.metric("Duplicados códigos", len(auditoria["Duplicados_codigos"]))
        a5.metric("Duplicados IPS", len(auditoria["Duplicados_IPS"]))
        a6.metric("Fuera de vigencia", len(auditoria["Fuera_de_vigencia"]))

        tipo_revision = st.selectbox("Revisar", list(auditoria.keys()), key="cv_auditoria_tipo")
        revision = auditoria[tipo_revision]
        if revision.empty:
            st.success("No se detectaron casos en esta categoría.")
        else:
            st.dataframe(revision, use_container_width=True, hide_index=True, height=500)

        st.markdown("#### Exportar respaldo completo")
        excel = _cv_exportar_base(base, catalogo, auditoria)
        x1, x2 = st.columns(2)
        x1.download_button(
            "📗 Descargar base completa Excel",
            data=excel,
            file_name=f"respaldo_convenios_vitae_{date.today().isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="cv_export_excel_v2",
        )
        x2.download_button(
            "📄 Descargar catálogo calculado CSV",
            data=catalogo.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"catalogo_convenios_{date.today().isoformat()}.csv",
            mime="text/csv",
            use_container_width=True,
            key="cv_export_csv_v2",
        )
        st.info(
            "Las cuatro pestañas maestras son la fuente oficial. El cálculo final nunca se guarda como un número fijo: "
            "se vuelve a calcular según la fecha, la regla del convenio, el IPS vigente y las excepciones por código."
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
