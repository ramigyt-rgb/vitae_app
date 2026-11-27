# app.py
# Ejecutar:
#   streamlit run app.py

import re
import unicodedata

import streamlit as st

from vitae.core.config import LOGO_PATH
from vitae.core.movil import instalar_ajustes_moviles
from vitae.core.navegacion import GRUPOS, ICONO_POR_DEFECTO
from vitae.core.ui import logo_data_uri
from vitae.core.tablas import MODULES

# IMPORTANTE: el parche se aplica antes de importar las vistas, para que el
# Copiloto Vitae y todos los módulos usen la recuperación inteligente completa.
try:
    from vitae.ia.copiloto_acceso_total import aplicar_parche_acceso_total
    aplicar_parche_acceso_total()
except Exception:
    # Nunca bloquea la aplicación si el asistente cambia en una futura versión.
    pass

from vitae.vistas import (
    render_dashboard,
    render_header,
    render_facturacion_pro as render_modulo,
    render_configuracion,
    render_pacientes_pagina,
    render_facturacion_medicos_pagina,
    require_vitae_login,
    _auth_can_access,
    _auth_ve_pacientes,
    _auth_ve_facturacion_medicos,
)
from vitae.modulos.facturacion import render_facturacion_ultra_pro

st.set_page_config(
    page_title="Vitae",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "🏥",
    layout="wide",
    initial_sidebar_state="auto",  # en el celular el menú arranca cerrado
)
instalar_ajustes_moviles()


# Menú lateral: módulos agrupados por tipo, con íconos (vitae/core/navegacion.py).
# Un módulo nuevo en MODULES que no figure en GRUPOS aparece solo en "Otros".
def _slug(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def _es_facturacion(module_name: str, cfg: dict) -> bool:
    identity = f"{module_name} {cfg.get('table', '')}".lower()
    return "factur" in identity and ("vmr" in identity or " vm" in f" {identity}")


def _pagina_modulo(module_name: str):
    def pagina() -> None:
        cfg = MODULES[module_name]
        if _es_facturacion(module_name, cfg):
            render_facturacion_ultra_pro(
                module_name,
                cfg,
                legacy_renderer=render_modulo,
                header_renderer=render_header,
            )
        else:
            render_modulo(module_name, cfg)

    pagina.__name__ = f"pagina_{_slug(module_name).replace('-', '_')}"
    return pagina


def _puede_ver_modulo(module_name: str) -> bool:
    empresa = str(MODULES.get(module_name, {}).get("empresa", "") or "").strip().upper() or None
    try:
        return _auth_can_access(f"mod::{module_name}", empresa)
    except Exception:
        return True  # ante cualquier duda se muestra; el módulo valida el acceso al abrirse


def _armar_menu() -> dict[str, list]:
    menu: dict[str, list] = {}
    inicio = []
    if _auth_can_access("dashboard_global"):
        inicio.append(st.Page(render_dashboard, title="Panel general", icon=":material/space_dashboard:",
                              url_path="inicio", default=True))
    if inicio:
        menu["Inicio"] = inicio

    falta_inicio = not inicio  # sin Panel general, la primera página visible será la de inicio
    # Pacientes: datos sensibles, sólo para quienes figuran en PACIENTES_USUARIOS (fuera de MODULES).
    try:
        ve_pacientes = _auth_ve_pacientes()
    except Exception:
        ve_pacientes = False
    if ve_pacientes:
        menu["Pacientes"] = [st.Page(render_pacientes_pagina, title="Pacientes", icon=":material/groups:",
                                     url_path="pacientes", default=falta_inicio)]
        falta_inicio = False
    # Facturación Médicos: información de los profesionales, aparte de Vitae (sólo rchar; fuera de MODULES).
    try:
        ve_fm = _auth_ve_facturacion_medicos()
    except Exception:
        ve_fm = False
    if ve_fm:
        menu["Profesionales"] = [st.Page(render_facturacion_medicos_pagina, title="Facturación Médicos",
                                         icon=":material/stethoscope:", url_path="facturacion-medicos",
                                         default=falta_inicio)]
        falta_inicio = False
    ubicados: set[str] = set()
    for grupo, modulos in GRUPOS:
        paginas = []
        for nombre, icono in modulos:
            if nombre not in MODULES:
                continue
            ubicados.add(nombre)
            if _puede_ver_modulo(nombre):
                paginas.append(st.Page(_pagina_modulo(nombre), title=nombre, icon=icono,
                                       url_path=_slug(nombre), default=falta_inicio))
                falta_inicio = False
        if paginas:
            menu[grupo] = paginas

    otros = [n for n in MODULES if n not in ubicados and _puede_ver_modulo(n)]
    if otros:
        menu["Otros"] = []
        for n in otros:
            menu["Otros"].append(st.Page(_pagina_modulo(n), title=n, icon=ICONO_POR_DEFECTO,
                                         url_path=_slug(n), default=falta_inicio))
            falta_inicio = False

    if _auth_can_access("configuracion"):
        menu["Sistema"] = [st.Page(render_configuracion, title="Configuración", icon=":material/settings:",
                                   url_path="configuracion", default=falta_inicio)]
    return menu


def main() -> None:
    # LOGIN GLOBAL: nada de la aplicación se construye antes de autenticar.
    require_vitae_login()

    logo = logo_data_uri()
    if logo:
        try:
            st.logo(logo, size="large")  # incrustado: no depende de imágenes temporales
        except Exception:
            st.logo(str(LOGO_PATH), size="large")

    menu = _armar_menu()
    if not menu:
        render_header()
        st.warning("Tu usuario todavía no tiene módulos asignados. Pedile acceso a un administrador.")
        return

    pagina = st.navigation(menu, position="sidebar")
    pagina.run()


if __name__ == "__main__":
    main()
