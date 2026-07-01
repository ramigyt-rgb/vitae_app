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
    render_facturacion_pro,
    render_configuracion,
)
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
        render_facturacion_pro(module_name, MODULES[module_name])
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