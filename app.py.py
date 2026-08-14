# vitae_gestion_app.py
# Ejecutar en VS Code / terminal:
# pip install streamlit pandas plotly openpyxl
# streamlit run vitae_gestion_app.py
# app.py
# Ejecutar:
# streamlit run app.py
import streamlit as st
from config import APP_TITLE
from modules import MODULES
from views import (
    render_dashboard,
    render_facturacion_pro as render_facturacion_actual,
    render_configuracion,
    require_vitae_login,
)
from facturacion_ultra_pro import render_facturacion_ultra_pro
st.set_page_config(
    page_title=APP_TITLE,
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(
    """
    <style>
    .main-title {
        font-size: 2.1rem;
        font-weight: 800;
        margin-bottom: 0.1rem;
    }
    .subtitle {
        color: #6b7280;
        margin-bottom: 1.2rem;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.35rem !important;
    }
    .small-muted {
        color: #6b7280;
        font-size: 0.88rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
def main() -> None:
    # LOGIN GLOBAL: nada de la aplicación se construye antes de autenticar.
    require_vitae_login()

    st.sidebar.title("VITAE")
    st.sidebar.caption("Sistema interno de gestión")
    page = st.sidebar.radio(
        "Navegación",
        ["Dashboard Global", "Módulos", "Administración", "Configuración"]
    )
    if page == "Dashboard Global":
        render_dashboard()
    elif page == "Módulos":
        empresas = ["Todos", "VMR", "VM", "VITAE"]
        empresa_filter = st.sidebar.selectbox("Empresa", empresas)
        module_names = list(MODULES.keys())
        if empresa_filter != "Todos":
            module_names = [
                m for m in module_names
                if MODULES[m]["empresa"] == empresa_filter
                or MODULES[m]["empresa"] == "VITAE"
            ]
        module_name = st.sidebar.selectbox("Módulo", module_names)
        cfg = MODULES[module_name]
        table = str(cfg.get("table", "") or "").lower()
        identity = f"{module_name} {table}".lower()
        if "factur" in identity and ("vmr" in identity or " vm" in f" {identity}"):
            render_facturacion_ultra_pro(
                module_name,
                cfg,
                legacy_renderer=render_facturacion_actual,
            )
        else:
            render_facturacion_actual(module_name, cfg)
    elif page == "Administración":
        st.title("Administración")
        st.subheader("Panel Administrativo")
    elif page == "Configuración":
        render_configuracion()
    st.sidebar.divider()
    st.sidebar.markdown("**Módulos incluidos**")
    st.sidebar.caption(f"{len(MODULES)} módulos activos")
if __name__ == "__main__":
    main()
