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
def render_dashboard() -> None:
    render_header()
    st.markdown("### Resumen General")
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
        if "fecha" in df.columns:
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
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Liquidez actual", fmt_money(caja_bancos))
    c2.metric("Facturación mes", fmt_money(facturacion_mes))
    c3.metric("Cobrado mes", fmt_money(cobrado_mes))
    c4.metric("A cobrar", fmt_money(pendiente_cobro))
    c5.metric("Resultado mes", fmt_money(resultado_mes))
    c6, c7, c8, c9, c10 = st.columns(5)
    c6.metric("A pagar", fmt_money(a_pagar))
    c7.metric("Deuda total", fmt_money(deuda_total))
    c8.metric("Vencidos / críticos", vencidos)
    c9.metric("Tareas pendientes", tareas_pend)
    c10.metric("Promedio por paciente", fmt_money(promedio_facturacion))
    c11, c12 = st.columns(2)
    c11.metric(
        "💸 Deuda Proveedores VMR",
        fmt_money(cuenta_corriente_vmr)
    )
    c12.metric(
        "💸 Deuda Proveedores VM",
        fmt_money(cuenta_corriente_vm)
    )
    st.divider()
    render_resumen_empresa("Resumen VMR", "VMR", dfs)
    render_resumen_empresa("Resumen VM", "VM", dfs)
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
def render_facturacion_pro(module_name: str, cfg: Dict[str, Any]) -> None:
    table = cfg["table"]
    try:
        df_base = get_df(table)
    except Exception as e:
        st.error(f"No se pudo leer Google Sheets para {table}: {e}")
        df_base = pd.DataFrame()
    render_header()
    st.header(module_name)
    st.caption(cfg["descripcion"])
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
        filtered = df_panel.copy()
        if df_panel.empty:
            st.warning("No hay registros cargados.")
        else:
            filtered = apply_filters(df_panel, module_name)            
            if table in ["caja_vm", "caja_vmr"]:
                safe_panel("render_caja_pro_panel", filtered, module_name)
            if table == "cuenta_corriente_vm":
                filtered = filtered.drop(columns=["importe_usd", "pagado_usd"], errors="ignore")
                st.divider()
            if table in ["cuenta_corriente_vm", "cuenta_corriente_vmr"]:
                st.divider()
                st.markdown("#### Tabla limpia")
                tabla_general = module_business_df(
                    add_balance_columns(filtered),
                    cfg
                ).drop(
                    columns=["responsable", "observaciones"],
                    errors="ignore"
                )
                st.dataframe(
                    tabla_general,
                    use_container_width=True,
                    hide_index=True,
                )
                st.markdown("### Tabla limpia")
                tabla_caja = module_business_df(
                    add_balance_columns(filtered),
                    cfg
                ).drop(columns=["observaciones"], errors="ignore")
                tabla = module_business_df(
                    add_balance_columns(filtered),
                    cfg
                )
                tabla = tabla.drop(
                    columns=["responsable", "observaciones"],
                    errors="ignore"
                )
                st.dataframe(
                    tabla,
                    use_container_width=True,
                    hide_index=True,
                )
                # ==================================================
                # DASHBOARD FINANCIERO PROVEEDORES VM
                # ==================================================
                
                st.divider()
                st.markdown("## 📊 Dashboard Financiero Proveedores VM")
                # ---------------------------------------
                # Deuda por proveedor
                # ---------------------------------------
                graf_deuda = filtered.copy()
                graf_deuda["Deuda"] = (
                    pd.to_numeric(graf_deuda["importe"], errors="coerce").fillna(0)
                    -
                    pd.to_numeric(graf_deuda["pagado"], errors="coerce").fillna(0)
                )
                graf_deuda = (
                    graf_deuda[graf_deuda["Deuda"] > 0]
                    .groupby("persona_entidad", as_index=False)["Deuda"]
                    .sum()
                    .rename(columns={"persona_entidad": "Proveedor"})
                    .sort_values("Deuda", ascending=False)
                )
                if not graf_deuda.empty:
                    fig1 = px.bar(
                        graf_deuda,
                        x="Deuda",
                        y="Proveedor",
                        orientation="h",
                        text="Deuda",
                        title="💰 Ranking de deuda por proveedor"
                    )
                    fig1.update_layout(
                        height=500,
                        yaxis=dict(categoryorder="total ascending")
                    )
                    st.plotly_chart(
                        fig1,
                        use_container_width=True
                    )
                # ---------------------------------------
                # Vencimientos próximos
                # ---------------------------------------
                if "vencimiento" in filtered.columns:
                    venc_df = filtered.copy()
                    venc_df["vencimiento"] = pd.to_datetime(
                        venc_df["vencimiento"],
                        dayfirst=True,
                        errors="coerce"
                    )
                    venc_df["saldo"] = (
                        pd.to_numeric(
                            venc_df["importe"],
                            errors="coerce"
                        ).fillna(0)
                        -
                        pd.to_numeric(
                            venc_df["pagado"],
                            errors="coerce"
                        ).fillna(0)
                    )
                    venc_df = venc_df[
                        venc_df["saldo"] > 0
                    ]
                    venc_resumen = (
                        venc_df.groupby("vencimiento")["saldo"]
                        .sum()
                        .reset_index()
                        .sort_values("vencimiento")
                    )
                    if not venc_resumen.empty:
                        fig2 = px.bar(
                            venc_resumen,
                            x="vencimiento",
                            y="saldo",
                            text="saldo",
                            title="📅 Calendario de vencimientos"
                        )
                        fig2.update_layout(
                            height=400
                        )
                        st.plotly_chart(
                            fig2,
                            use_container_width=True
                        )
                # ---------------------------------------
                # Pagado vs pendiente
                # ---------------------------------------
                pagado_total = pd.to_numeric(filtered["pagado"], errors="coerce").fillna(0).sum()
                pendiente_total = (
                    pd.to_numeric(filtered["importe"], errors="coerce").fillna(0).sum()
                    -
                    pagado_total
                )
                pagado_total = pd.to_numeric(filtered["pagado"], errors="coerce").fillna(0).sum()
                pendiente_total = (
                    pd.to_numeric(filtered["importe"], errors="coerce").fillna(0).sum()
                    -
                    pagado_total
                )
                pie_df = pd.DataFrame({
                    "Estado": [
                        "Pagado",
                        "Pendiente"
                    ],
                    "Monto": [
                        pagado_total,
                        pendiente_total
                    ]
                })
                fig3 = px.pie(
                    pie_df,
                    names="Estado",
                    values="Monto",
                    hole=0.55,
                    title="💳 Pagado vs Pendiente"
                )
                fig3.update_layout(
                    height=450
                )
                st.plotly_chart(
                    fig3,
                    use_container_width=True
                )
                # ---------------------------------------
                # Top facturas pendientes
                # ---------------------------------------
                st.divider()
                st.markdown("### 🚨 Facturas más importantes pendientes")
                top_facturas = filtered.copy()
                top_facturas["saldo"] = (
                    pd.to_numeric(
                        top_facturas["importe"],
                        errors="coerce"
                    ).fillna(0)
                    -
                    pd.to_numeric(
                        top_facturas["pagado"],
                        errors="coerce"
                    ).fillna(0)
                )
                top_facturas = top_facturas[
                    top_facturas["saldo"] > 0
                ]
                top_facturas = top_facturas.sort_values(
                    "saldo",
                    ascending=False
                )
                cols = [
                    c for c in [
                        "persona_entidad",
                        "comprobante",
                        "fecha",
                        "vencimiento",
                        "saldo"
                    ]
                    if c in top_facturas.columns
                ]
                if not top_facturas.empty:
                    st.dataframe(
                        top_facturas[cols].head(15),
                        use_container_width=True,
                        hide_index=True
                    )                
            if table in ["banco_galicia_vm", "banco_macro_vmr"]:
                render_banco_pro_panel(filtered, module_name)
                st.divider()
                st.markdown("### Movimientos bancarios")
                tabla_banco = module_business_df(
                    add_balance_columns(filtered),
                    cfg
                ).drop(
                    columns=["responsable", "observaciones"],
                    errors="ignore"
                )
                st.dataframe(
                    tabla_banco,
                    use_container_width=True,
                    hide_index=True,
                )
                                
            col_monto = None
            for c in ["valor_pesos", "importe", "monto", "saldo", "valor"]:
                if c in filtered.columns:
                    col_monto = c
                    break
            total_facturado = filtered[col_monto].apply(money).sum() if col_monto else 0 
            if "estado" in filtered.columns:
                total_cobrado = filtered[filtered["estado"].astype(str).str.lower().isin(["completo", "pagado", "cobrado"])]
                cobrado = total_cobrado[col_monto].apply(money).sum() if col_monto else 0
            else:
                total_cobrado = 0
                cobrado = 0
            pendiente = total_facturado - cobrado
            pacientes = len(filtered)
            ticket_promedio = total_facturado / pacientes if pacientes > 0 else 0
            if table == "Cuenta Corriente VMR":
                c1, c2, c3, c4, c5, c6 = st.columns(6)
                c1.metric("💰 Facturado ARS", fmt_money(total_facturado))
                c2.metric("💵 Facturado USD", f"USD {total_usd:,.2f}")
                c3.metric("💸 Pagado ARS", fmt_money(cobrado))
                c4.metric("💵 Pagado USD", f"USD {pagado_usd:,.2f}")
                c5.metric("⏳ A pagar ARS", fmt_money(pendiente))
                c6.metric("💵 A pagar USD", f"USD {pendiente_usd:,.2f}")
            if table == "cuenta_corriente_vm":
                c1, c2, c3 = st.columns(3)
                c1.metric("💰 Total Facturas", fmt_money(total_facturado))
                c2.metric("💸 Total Pagado", fmt_money(cobrado))
                c3.metric("⏳ Deuda Total", fmt_money(pendiente))
                st.divider()
                st.markdown("### 🏥 Estado de proveedores")
                proveedores_vm = [
                    "DROGUERIA CAPDEVILLA",
                    "OXITESA",
                    "SALUZZI",
                    "DROGUERIA SALTA SALUD",
                    "DROGUERIA PLAZA OÑA",
                    "PHARMA LIGHT",
                    "FARMACORP",
                    "DISTRIMED",
                    "SALUS",
                    "DROGUERIA LARPOS",
                    "MEDICFARMA"
                ]
                resumen = []
                for proveedor in proveedores_vm:
                    df_prov = filtered[
                        filtered["persona_entidad"]
                        .astype(str)
                        .str.upper()
                        .str.strip()
                        == proveedor
                    ]
                    deuda = (
                        pd.to_numeric(df_prov["importe"], errors="coerce").fillna(0).sum()
                        -
                        pd.to_numeric(df_prov["pagado"], errors="coerce").fillna(0).sum()
                    )
                    if not df_prov.empty:
                        prox_vto = (
                            pd.to_datetime(
                                df_prov["vencimiento"],
                                dayfirst=True,
                                errors="coerce"
                            ).min()
                        )
                    else:
                        prox_vto = pd.NaT
                    resumen.append({
                        "Proveedor": proveedor,
                        "Deuda": deuda,
                        "Próximo vencimiento": prox_vto
                    })
                resumen_df = pd.DataFrame(resumen)
                resumen_df = resumen_df.sort_values(
                    by=["Próximo vencimiento", "Deuda"],
                    ascending=[True, False]
                )
                st.dataframe(
                    resumen_df,
                    use_container_width=True,
                    hide_index=True
                ) 
            if table != "Cuenta Corriente VM":
                total_usd = filtered["importe_usd"].apply(money_usd).sum() if "importe_usd" in filtered.columns else 0
                pagado_usd = filtered["pagado_usd"].apply(money_usd).sum() if "pagado_usd" in filtered.columns else 0
                pendiente_usd = total_usd - pagado_usd
            
            else:
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("💰 Facturado", fmt_money(total_facturado))
                c2.metric("✅ Cobrado", fmt_money(cobrado))
                c3.metric("⏳ Pendiente", fmt_money(pendiente))
                c4.metric("👥 Pacientes", pacientes)
        st.divider()
        if table == "cuenta_corriente_vm":
            filtered = df.drop(columns=["importe_usd", "pagado_usd"], errors="ignore")
        if "mes" in filtered.columns:
            filtered = filtered.sort_values(
                by="mes",
                ascending=False,
                na_position="last"
            )
        col_orden = "mes" if "mes" in filtered.columns else "Mes" if "Mes" in filtered.columns else None
        if col_orden:
            filtered[col_orden] = pd.to_datetime(
                filtered[col_orden],
                errors="coerce",
                dayfirst=True
            )
            filtered = filtered.sort_values(
                by=col_orden,
                ascending=False,
                na_position="last"
            )
        if table == "cuenta_corriente_vm":
            df = df.drop(columns=["importe_usd", "pagado_usd"], errors="ignore")
        elif table == "cuenta_corriente_vmr":
            c1, c2, c3, c4, c5, c6 = st.columns(6)
            c1.metric("🛒 Compras ARS", fmt_money(total_facturado))
            c2.metric("🛒 Compras USD", f"USD {total_usd:,.2f}")
            c3.metric("💵 Pagado ARS", fmt_money(cobrado))
            c4.metric("💵 Pagado USD", f"USD {pagado_usd:,.2f}")
            c5.metric("📌 A Pagar ARS", fmt_money(pendiente))
            c6.metric("📌 A Pagar USD", f"USD {pendiente_usd:,.2f}")
            st.divider()
            st.markdown("### 🏥 Estado de proveedores VMR")
            proveedores_vmr = [
                "DIVILAB",
                "EUROFARMA",
                "FERRING",
                "MEDICAL ENGINEERING",
                "MERCK",
                "VITAGEN",
                "CAMPO NITRO",
                "DANIEL CAMACHO",
            ]
            rows = []
            for proveedor in proveedores_vmr:
                df_prov = filtered[
                    filtered["persona_entidad"]
                    .astype(str)
                    .str.upper()
                    .str.strip()
                    .str.contains(proveedor, na=False)
                ]
                importe_ars = pd.to_numeric(df_prov["importe"], errors="coerce").fillna(0).sum()
                pagado_ars = pd.to_numeric(df_prov["pagado"], errors="coerce").fillna(0).sum()
                deuda_ars = importe_ars - pagado_ars
                importe_usd = pd.to_numeric(df_prov["importe_usd"], errors="coerce").fillna(0).sum() if "importe_usd" in df_prov.columns else 0
                pagado_usd_prov = pd.to_numeric(df_prov["pagado_usd"], errors="coerce").fillna(0).sum() if "pagado_usd" in df_prov.columns else 0
                deuda_usd = importe_usd - pagado_usd_prov
                if not df_prov.empty and "vencimiento" in df_prov.columns:
                    prox_vto = pd.to_datetime(
                        df_prov["vencimiento"],
                        dayfirst=True,
                        errors="coerce"
                    ).min()
                else:
                    prox_vto = pd.NaT
                rows.append({
                    "Proveedor": proveedor,
                    "Importe ARS": importe_ars,
                    "Pagado ARS": pagado_ars,
                    "Deuda ARS": deuda_ars,
                    "Importe USD": importe_usd,
                    "Pagado USD": pagado_usd_prov,
                    "Deuda USD": deuda_usd,
                    "Próximo vencimiento": prox_vto,
                    "Facturas": len(df_prov),
                })
            resumen_vmr = pd.DataFrame(rows)
            resumen_vmr = resumen_vmr.sort_values(
                by=["Deuda ARS", "Deuda USD", "Próximo vencimiento"],
                ascending=[False, False, True]
            )
            st.dataframe(
                resumen_vmr,
                use_container_width=True,
                hide_index=True
            )
            st.divider()
            st.markdown("## 📊 Dashboard Financiero Proveedores VMR")
            graf_ars = resumen_vmr[resumen_vmr["Deuda ARS"] > 0].copy()
            if not graf_ars.empty:
                fig1 = px.bar(
                    graf_ars,
                    x="Deuda ARS",
                    y="Proveedor",
                    orientation="h",
                    text="Deuda ARS",
                    title="💰 Ranking deuda ARS por proveedor",
                )
                fig1.update_layout(
                    height=500,
                    yaxis=dict(categoryorder="total ascending")
                )
                st.plotly_chart(fig1, use_container_width=True)
            graf_usd = resumen_vmr[resumen_vmr["Deuda USD"] > 0].copy()
            if not graf_usd.empty:
                fig2 = px.bar(
                    graf_usd,
                    x="Deuda USD",
                    y="Proveedor",
                    orientation="h",
                    text="Deuda USD",
                    title="💵 Ranking deuda USD por proveedor",
                )
                fig2.update_layout(
                    height=500,
                    yaxis=dict(categoryorder="total ascending")
                )
                st.plotly_chart(fig2, use_container_width=True)
            venc_df = filtered.copy()
            if "vencimiento" in venc_df.columns:
                venc_df["vencimiento"] = pd.to_datetime(
                    venc_df["vencimiento"],
                    dayfirst=True,
                    errors="coerce"
                )
                venc_df["saldo_ars"] = (
                    pd.to_numeric(venc_df["importe"], errors="coerce").fillna(0)
                    -
                    pd.to_numeric(venc_df["pagado"], errors="coerce").fillna(0)
                )
                venc_df["saldo_usd"] = (
                    pd.to_numeric(venc_df["importe_usd"], errors="coerce").fillna(0)
                    -
                    pd.to_numeric(venc_df["pagado_usd"], errors="coerce").fillna(0)
                ) if "importe_usd" in venc_df.columns and "pagado_usd" in venc_df.columns else 0
                venc_resumen = (
                    venc_df.groupby("vencimiento")[["saldo_ars", "saldo_usd"]]
                    .sum()
                    .reset_index()
                    .sort_values("vencimiento")
                )
                venc_resumen = venc_resumen[
                    (venc_resumen["saldo_ars"] > 0) |
                    (venc_resumen["saldo_usd"] > 0)
                ]
                if not venc_resumen.empty:
                    fig3 = px.bar(
                        venc_resumen,
                        x="vencimiento",
                        y=["saldo_ars", "saldo_usd"],
                        title="📅 Vencimientos próximos ARS / USD",
                        barmode="group"
                    )
                    fig3.update_layout(height=430)
                    st.plotly_chart(fig3, use_container_width=True)
            pie_ars = pd.DataFrame({
                "Estado": ["Pagado ARS", "Pendiente ARS"],
                "Monto": [cobrado, pendiente],
            })
            fig4 = px.pie(
                pie_ars,
                names="Estado",
                values="Monto",
                hole=0.55,
                title="💳 ARS Pagado vs Pendiente",
            )
            st.plotly_chart(fig4, use_container_width=True)
            pie_usd = pd.DataFrame({
                "Estado": ["Pagado USD", "Pendiente USD"],
                "Monto": [pagado_usd, pendiente_usd],
            })
            fig5 = px.pie(
                pie_usd,
                names="Estado",
                values="Monto",
                hole=0.55,
                title="💵 USD Pagado vs Pendiente",
            )
            st.plotly_chart(fig5, use_container_width=True)
    st.divider()
    st.markdown("#### Tabla limpia")
    try:
        tabla_limpia = filtered.copy()
    except Exception as e:
        tabla_limpia = pd.DataFrame()
        st.warning(f"No se pudo leer la tabla limpia desde Google Sheets: {e}")
    if tabla_limpia is None or tabla_limpia.empty:
        st.warning("No hay datos en Google Sheets para este módulo.")
    else:
        tabla_limpia = tabla_limpia.drop(
            columns=["id", "created_at", "updated_at"],
            errors="ignore"
        )
        fecha_col = "mes" if "mes" in tabla_limpia.columns else None
        if fecha_col:
            fechas = pd.to_datetime(
                tabla_limpia[fecha_col].astype(str).str.strip(),
                errors="coerce",
                dayfirst=False
            )
            tabla_limpia["_orden_fecha"] = fechas
            tabla_limpia = tabla_limpia.sort_values(
                by="_orden_fecha",
                ascending=False,
                na_position="last"
            )
            tabla_limpia[fecha_col] = tabla_limpia["_orden_fecha"].dt.strftime("%d-%m-%Y")
            tabla_limpia = tabla_limpia.drop(columns=["_orden_fecha"], errors="ignore")
        st.dataframe(
            tabla_limpia,
            use_container_width=True,
            hide_index=True,
        )
    st.markdown("### Gráficos útiles")
    g1, g2 = st.columns(2)
    if "valor_pesos" in filtered.columns:
        graph = filtered.copy()
        graph["valor_pesos"] = graph["valor_pesos"].apply(money)
        if "mes" in graph.columns:
            graph["mes"] = pd.to_datetime(
                graph["mes"],
                errors="coerce",
                dayfirst=True
            )
        if "fecha_factura" in graph.columns:
            tmp = pd.to_datetime(graph["fecha_factura"], errors="coerce")
            if tmp.notna().sum() > 0:
                fecha_grafico = "fecha_factura"
        fecha_grafico = None
        if fecha_grafico is None and "mes" in graph.columns:
            tmp = pd.to_datetime(graph["mes"], errors="coerce", dayfirst=True)
            if tmp.notna().sum() > 0:
                fecha_grafico = "mes"
        if fecha_grafico:
            graph[fecha_grafico] = pd.to_datetime(
                graph[fecha_grafico],
                errors="coerce",
                dayfirst=True
            )
            graph = graph[graph[fecha_grafico].notna()]
            if not graph.empty:
                graph["Mes"] = graph[fecha_grafico].dt.to_period("M").astype(str)
                chart = (
                    graph.groupby("Mes")["valor_pesos"]
                    .sum()
                    .reset_index()
                )
                fig = px.bar(
                    chart,
                    x="Mes",
                    y="valor_pesos",
                    title="Facturación por mes"
                )
                g1.plotly_chart(fig, use_container_width=True)
    if "obra_social" in filtered.columns and "valor_pesos" in filtered.columns:
        chart = filtered.copy()
        chart["valor_pesos"] = chart["valor_pesos"].apply(money)
        chart = (
            chart.groupby("obra_social")["valor_pesos"]
            .sum()
            .reset_index()
        )
        chart = chart.sort_values("valor_pesos", ascending=False).head(10)
        fig = px.bar(chart, x="obra_social", y="valor_pesos", title="Facturación por obra social")
        g2.plotly_chart(fig, use_container_width=True)
    g3, g4 = st.columns(2)
    if "medico_responsable" in filtered.columns and "valor_pesos" in filtered.columns:
        chart = filtered.copy()
        chart["valor_pesos"] = chart["valor_pesos"].apply(money)
        chart = (
            chart.groupby("medico_responsable")["valor_pesos"]
            .sum()
            .reset_index()
        )
        chart = chart.sort_values("valor_pesos", ascending=False).head(10)
        fig = px.bar(chart, x="medico_responsable", y="valor_pesos", title="Facturación por médico")
        g3.plotly_chart(fig, use_container_width=True)
    if "procedimiento" in filtered.columns and "valor_pesos" in filtered.columns:
        chart = filtered.copy()
        chart["valor_pesos"] = chart["valor_pesos"].apply(money)
        chart = (
            chart.groupby("procedimiento")["valor_pesos"]
            .sum()
            .reset_index()
        )
        chart = chart.sort_values("valor_pesos", ascending=False).head(10)
        fig = px.bar(chart, x="procedimiento", y="valor_pesos", title="Facturación por procedimiento")
        g4.plotly_chart(fig, use_container_width=True)    
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
                        df_actual = df_base.copy()     
                        duplicado = False     
                        if not df_actual.empty:
                            cols_check = [c for c in data.keys() if c in df_actual.columns]    
                            duplicado = (      
                                df_actual[cols_check].fillna("").astype(str)    
                                == pd.Series(data)[cols_check].fillna("").astype(str)      
                            ).all(axis=1).any()         
                        if duplicado:      
                            st.warning("Este registro ya existe. No se volvió a cargar.")        
                        else:       
                            insert_row(table, data)          
                            st.success("Registro guardado correctamente.")         
                        st.write("DEBUG GUARDADO EN TABLA:", table)            
                        st.write(data)            
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
            columnas_ocultas = ["created_at", "updated_at"]
            df_edit = df_edit.drop(columns=columnas_ocultas, errors="ignore")
        
            # Mostrar fechas como DD/MM/AAAA en el editor
            for col in ["mes", "fecha_factura", "fecha_pago", "fecha_cobro"]:
                # Ordenar por la columna mes (más reciente primero)
                if "mes" in df_edit.columns:
                    df_edit["_orden"] = pd.to_datetime(
                        df_edit["mes"],
                        format="%d/%m/%Y",
                        errors="coerce"
                    )
                    df_edit = (
                        df_edit
                        .sort_values("_orden", ascending=False)
                        .drop(columns="_orden")
                        .reset_index(drop=True)
                    )
                
                if col in df_edit.columns:
                    df_edit[col] = (
                        pd.to_datetime(df_edit[col], errors="coerce")
                        .dt.strftime("%d/%m/%Y")
                    )
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
                    for col in ["mes", "fecha", "fecha_factura", "fecha_pago", "fecha_cobro", "vencimiento"]:
                        if col in limpio.columns:
                            fechas = pd.to_datetime(limpio[col], dayfirst=True, errors="coerce")
                            limpio[col] = fechas.dt.strftime("%Y-%m-%d")
                            limpio[col] = limpio[col].fillna("")
                    limpio = limpio.drop(
                        columns=["saldo", "saldo_usd", "saldo_movimiento", "_orden"],
                        errors="ignore"
                    )
                    sync_df_to_sheet(table, limpio)
                    st.success("Cambios guardados correctamente.")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error("ERROR AL GUARDAR")
                    st.exception(e)
    with tab_exportar:
        df = add_balance_columns(df_base.copy())
        if table == "cuenta_corriente_vm":
            df = df.drop(columns=["importe_usd", "pagado_usd"], errors="ignore")
        if df.empty:
            st.info("No hay datos para exportar.")
        else:
            export_df = format_facturacion_table(df, labels)
            csv = export_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button("Descargar CSV", data=csv, file_name=f"{table}.csv", mime="text/csv")
            xlsx_path = Path(f"{table}.xlsx")
            export_df.to_excel(xlsx_path, index=False)
            with open(xlsx_path, "rb") as f:
                st.download_button(
                    "Descargar Excel",
                    data=f,
                    file_name=f"{table}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(
        "💰 Facturado",
        fmt_money(facturado_total)
    )
    c2.metric(
        "✅ Cobrado",
        fmt_money(cobrado_total)
    )
    c3.metric(
        "⏳ Pendiente",
        fmt_money(pendiente_total)
    )
    c4.metric(
        "📤 Egresos",
        fmt_money(egreso_total)
    )
    c5.metric(
        "📈 Resultado",
        fmt_money(resultado_total)
    )
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