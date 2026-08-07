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
# IA Vitae: importación tolerante a versiones para que la app no se caiga
# si GitHub/Streamlit queda momentáneamente con assistant.py y views.py desincronizados.
import assistant as _vitae_assistant

preguntar_ia = getattr(_vitae_assistant, "preguntar_ia")
preguntar_dashboard = getattr(_vitae_assistant, "preguntar_dashboard")

def _copiloto_no_disponible(*args, **kwargs):
    return (
        "El Copiloto Vitae todavía no está disponible en esta versión de assistant.py. "
        "Actualizá assistant.py con la versión incluida junto a este views.py. "
        "El resto de la plataforma puede seguir funcionando normalmente."
    )

def _historial_copiloto_vacio():
    return []

def _limpiar_historial_copiloto_fallback():
    try:
        st.session_state["vitae_copiloto_historial"] = []
    except Exception:
        pass

preguntar_copiloto = getattr(_vitae_assistant, "preguntar_copiloto", _copiloto_no_disponible)
obtener_historial_copiloto = getattr(_vitae_assistant, "obtener_historial_copiloto", _historial_copiloto_vacio)
limpiar_historial_copiloto = getattr(_vitae_assistant, "limpiar_historial_copiloto", _limpiar_historial_copiloto_fallback)
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

# Módulo corporativo de convenios.
# Se registra de forma OBLIGATORIA para evitar que una configuración vieja,
# incompleta o vacía de modules.py haga desaparecer el acceso del menú.
_CONVENIOS_MODULE_CONFIG = {
    "table": "convenios",
    "empresa": "VITAE",
    "tipo": "Gestión quirúrgica",
    "descripcion": (
        "Centro corporativo de nomencladores, valores, vigencias, reglas de "
        "facturación y padrón de prestadoras."
    ),
    "fields": [],
}
MODULES["Convenios"] = _CONVENIOS_MODULE_CONFIG.copy()

# Módulo obligatorio de Planes de pagos y préstamos. Conserva la tabla ya
# configurada en modules.py cuando existe y completa solamente los datos que
# faltan. Así el acceso no desaparece aunque modules.py sea una versión vieja.
_PLANES_MODULE_NAME = "Planes de pagos y préstamos"
_PLANES_DEFAULT_CONFIG = {
    "table": "planes_pagos_prestamos",
    "empresa": "VITAE",
    "tipo": "Financiación",
    "descripcion": (
        "Centro financiero de planes ARCA, préstamos, créditos, cuotas y "
        "proyección mensual de VMR y VM."
    ),
    "fields": [],
}
_PLANES_EQUIVALENT_KEY = next(
    (
        key
        for key in list(MODULES.keys())
        if str(key).strip().casefold().replace("é", "e")
        in {
            "planes de pagos y prestamos",
            "planes de pago y prestamos",
            "planes de pagos y préstamo",
        }
    ),
    None,
)
if _PLANES_EQUIVALENT_KEY and _PLANES_EQUIVALENT_KEY != _PLANES_MODULE_NAME:
    _existing_planes_cfg = MODULES.pop(_PLANES_EQUIVALENT_KEY)
    MODULES[_PLANES_MODULE_NAME] = _existing_planes_cfg
else:
    MODULES.setdefault(_PLANES_MODULE_NAME, {})
for _key, _value in _PLANES_DEFAULT_CONFIG.items():
    MODULES[_PLANES_MODULE_NAME].setdefault(_key, _value)

# Módulo obligatorio de Gine Vitae. Se conserva cualquier configuración previa
# de modules.py y se completa únicamente lo necesario para que el acceso no
# desaparezca en instalaciones antiguas.
_GINE_MODULE_NAME = "Gine Vitae"
_GINE_DEFAULT_CONFIG = {
    "table": "gine_vitae",
    "empresa": "VITAE",
    "tipo": "Programa preventivo",
    "descripcion": (
        "Centro integral de planes, pacientes, cobros, autorizaciones, "
        "entregas, facturación y caja de Gine Vitae."
    ),
    "fields": [],
}
_GINE_EQUIVALENT_KEY = next(
    (
        key
        for key in list(MODULES.keys())
        if str(key).strip().casefold().replace("é", "e")
        in {
            "gine vitae",
            "ginevitae",
            "planes gine vitae",
            "planes de gine vitae",
        }
    ),
    None,
)
if _GINE_EQUIVALENT_KEY and _GINE_EQUIVALENT_KEY != _GINE_MODULE_NAME:
    _existing_gine_cfg = MODULES.pop(_GINE_EQUIVALENT_KEY)
    MODULES[_GINE_MODULE_NAME] = _existing_gine_cfg
else:
    MODULES.setdefault(_GINE_MODULE_NAME, {})
for _key, _value in _GINE_DEFAULT_CONFIG.items():
    MODULES[_GINE_MODULE_NAME].setdefault(_key, _value)

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
def _render_vitae_app_shell() -> None:
    """Capa visual corporativa global. No modifica datos ni lógica funcional."""
    st.markdown(
        """
        <style>
        :root {
            --vitae-navy: #10243E;
            --vitae-blue: #1C4E80;
            --vitae-cyan: #2E8FA3;
            --vitae-mint: #52B8A5;
            --vitae-ice: #F3F8FA;
            --vitae-paper: #FFFFFF;
            --vitae-text: #172033;
            --vitae-muted: #687386;
            --vitae-line: rgba(16, 36, 62, .11);
            --vitae-shadow: 0 14px 38px rgba(20, 43, 70, .08);
        }

        html, body, [class*="css"] {
            font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        }
        .stApp {
            background:
                radial-gradient(circle at 88% 4%, rgba(82,184,165,.10), transparent 24rem),
                radial-gradient(circle at 7% 18%, rgba(46,143,163,.08), transparent 28rem),
                linear-gradient(180deg, #F8FBFC 0%, #F3F7F9 100%);
            color: var(--vitae-text);
        }
        .block-container {
            max-width: 1600px;
            padding-top: 1.25rem;
            padding-bottom: 3.5rem;
        }
        header[data-testid="stHeader"] {
            background: rgba(248,251,252,.82);
            backdrop-filter: blur(18px);
            border-bottom: 1px solid rgba(16,36,62,.06);
        }
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #10243E 0%, #173B60 58%, #1F6174 100%);
            border-right: 1px solid rgba(255,255,255,.08);
        }
        section[data-testid="stSidebar"] > div { padding-top: .75rem; }
        section[data-testid="stSidebar"] * { color: rgba(255,255,255,.92); }
        section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
        section[data-testid="stSidebar"] label { color: rgba(255,255,255,.78) !important; }
        section[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,.12); }
        /* Selectores del sidebar: fondo claro + texto oscuro para máxima legibilidad. */
        section[data-testid="stSidebar"] div[data-baseweb="select"] > div {
            background: #FFFFFF !important;
            border: 1px solid rgba(255,255,255,.72) !important;
            box-shadow: 0 7px 18px rgba(3,18,36,.18) !important;
            min-height: 3rem;
        }
        section[data-testid="stSidebar"] div[data-baseweb="select"] *,
        section[data-testid="stSidebar"] div[data-baseweb="select"] span,
        section[data-testid="stSidebar"] div[data-baseweb="select"] input,
        section[data-testid="stSidebar"] div[data-baseweb="select"] [data-testid="stSelectboxVirtualDropdown"] {
            color: #10243E !important;
            -webkit-text-fill-color: #10243E !important;
            opacity: 1 !important;
        }
        section[data-testid="stSidebar"] div[data-baseweb="select"] svg {
            fill: #1C4E80 !important;
            color: #1C4E80 !important;
        }
        section[data-testid="stSidebar"] .stTextInput input,
        section[data-testid="stSidebar"] .stNumberInput input,
        section[data-testid="stSidebar"] .stDateInput input {
            background: #FFFFFF !important;
            border-color: rgba(255,255,255,.72) !important;
            color: #10243E !important;
            -webkit-text-fill-color: #10243E !important;
        }
        /* El menú desplegable se renderiza fuera del sidebar. */
        div[data-baseweb="popover"] [role="listbox"],
        div[data-baseweb="menu"] {
            background: #FFFFFF !important;
            color: #10243E !important;
        }
        div[data-baseweb="popover"] [role="option"],
        div[data-baseweb="popover"] [role="option"] *,
        li[role="option"], li[role="option"] * {
            color: #10243E !important;
            -webkit-text-fill-color: #10243E !important;
        }
        section[data-testid="stSidebar"] button {
            border-color: rgba(255,255,255,.18) !important;
            background: rgba(255,255,255,.08) !important;
            color: white !important;
        }
        section[data-testid="stSidebar"] button:hover {
            background: rgba(255,255,255,.16) !important;
            border-color: rgba(255,255,255,.32) !important;
        }

        .vitae-app-header {
            position: relative;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1.5rem;
            padding: 1.35rem 1.55rem;
            margin: 0 0 1.15rem 0;
            border: 1px solid rgba(16,36,62,.10);
            border-radius: 24px;
            background: linear-gradient(125deg, rgba(255,255,255,.98), rgba(238,248,248,.96));
            box-shadow: var(--vitae-shadow);
        }
        .vitae-app-header::after {
            content: "";
            position: absolute;
            width: 250px;
            height: 250px;
            right: -95px;
            top: -145px;
            border-radius: 50%;
            background: linear-gradient(135deg, rgba(46,143,163,.22), rgba(82,184,165,.08));
        }
        .vitae-eyebrow {
            font-size: .69rem;
            letter-spacing: .16em;
            text-transform: uppercase;
            font-weight: 850;
            color: var(--vitae-cyan);
            margin-bottom: .25rem;
        }
        .vitae-title {
            font-size: clamp(1.65rem, 2.4vw, 2.35rem);
            line-height: 1.05;
            font-weight: 880;
            letter-spacing: -.035em;
            color: var(--vitae-navy);
        }
        .vitae-subtitle {
            margin-top: .5rem;
            font-size: .91rem;
            color: var(--vitae-muted);
            max-width: 820px;
        }
        .vitae-live {
            position: relative;
            z-index: 1;
            display: inline-flex;
            align-items: center;
            gap: .45rem;
            white-space: nowrap;
            padding: .48rem .72rem;
            border-radius: 999px;
            background: rgba(255,255,255,.78);
            border: 1px solid rgba(16,36,62,.10);
            color: #285668;
            font-size: .73rem;
            font-weight: 800;
        }
        .vitae-live::before {
            content: "";
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #3DBA8C;
            box-shadow: 0 0 0 4px rgba(61,186,140,.13);
        }

        h1, h2, h3, h4 { color: var(--vitae-navy); letter-spacing: -.018em; }
        h1 { font-weight: 860 !important; }
        h2, h3 { font-weight: 820 !important; }
        p, li { line-height: 1.55; }
        [data-testid="stCaptionContainer"] { color: var(--vitae-muted); }

        div[data-testid="stMetric"] {
            border: 1px solid var(--vitae-line);
            border-radius: 19px;
            padding: .9rem 1rem;
            background: rgba(255,255,255,.92);
            box-shadow: 0 8px 24px rgba(20,43,70,.055);
            min-height: 118px;
            transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
        }
        div[data-testid="stMetric"]:hover {
            transform: translateY(-2px);
            border-color: rgba(46,143,163,.26);
            box-shadow: 0 14px 30px rgba(20,43,70,.09);
        }
        div[data-testid="stMetricLabel"] { color: var(--vitae-muted); font-weight: 720; }
        div[data-testid="stMetricValue"] { color: var(--vitae-navy); font-weight: 860; font-variant-numeric: tabular-nums; }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-color: var(--vitae-line) !important;
            border-radius: 20px !important;
            background: rgba(255,255,255,.82);
            box-shadow: 0 8px 28px rgba(20,43,70,.045);
        }
        div[data-testid="stExpander"] {
            border: 1px solid var(--vitae-line);
            border-radius: 16px;
            background: rgba(255,255,255,.80);
            overflow: hidden;
        }
        div[data-testid="stExpander"] summary { font-weight: 760; color: var(--vitae-navy); }

        .stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
            min-height: 2.65rem;
            border-radius: 12px;
            border: 1px solid rgba(28,78,128,.16);
            background: linear-gradient(135deg, #1C4E80, #28758C);
            color: white;
            font-weight: 760;
            box-shadow: 0 6px 16px rgba(28,78,128,.15);
            transition: transform .16s ease, box-shadow .16s ease, filter .16s ease;
        }
        .stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {
            transform: translateY(-1px);
            filter: brightness(1.04);
            box-shadow: 0 10px 22px rgba(28,78,128,.22);
            color: white;
        }
        .stButton > button:active { transform: translateY(0); }

        div[data-baseweb="select"] > div,
        div[data-baseweb="base-input"],
        .stDateInput > div > div,
        .stTextArea textarea,
        .stTextInput input,
        .stNumberInput input {
            border-radius: 12px !important;
            border-color: rgba(16,36,62,.14) !important;
            background: rgba(255,255,255,.94) !important;
        }
        div[data-baseweb="select"] > div:focus-within,
        div[data-baseweb="base-input"]:focus-within,
        .stTextArea textarea:focus,
        .stTextInput input:focus {
            border-color: rgba(46,143,163,.65) !important;
            box-shadow: 0 0 0 3px rgba(46,143,163,.10) !important;
        }
        div[data-testid="stDataFrame"], div[data-testid="stTable"] {
            border: 1px solid var(--vitae-line);
            border-radius: 16px;
            overflow: hidden;
            background: white;
            box-shadow: 0 7px 22px rgba(20,43,70,.045);
        }
        div[data-testid="stPlotlyChart"] {
            border: 1px solid var(--vitae-line);
            border-radius: 18px;
            background: rgba(255,255,255,.88);
            padding: .25rem;
            box-shadow: 0 8px 24px rgba(20,43,70,.045);
        }
        div[data-testid="stTabs"] [data-baseweb="tab-list"] {
            gap: .35rem;
            background: rgba(229,238,242,.72);
            padding: .3rem;
            border-radius: 14px;
        }
        div[data-testid="stTabs"] button {
            border-radius: 10px;
            font-weight: 760;
            padding-left: .85rem;
            padding-right: .85rem;
        }
        div[data-testid="stTabs"] button[aria-selected="true"] {
            background: white;
            color: var(--vitae-blue);
            box-shadow: 0 4px 12px rgba(20,43,70,.08);
        }
        div[data-testid="stAlert"] { border-radius: 15px; }
        hr { border-color: rgba(16,36,62,.09); }

        @media (max-width: 800px) {
            .block-container { padding-left: .8rem; padding-right: .8rem; padding-top: .7rem; }
            .vitae-app-header { padding: 1.05rem; border-radius: 19px; align-items: flex-start; }
            .vitae-title { font-size: 1.65rem; }
            .vitae-live { display: none; }
            div[data-testid="stMetric"] { min-height: 104px; }
        }

        /* FIX DEFINITIVO: selectbox del sidebar compatible con Streamlit clásico y moderno. */
        section[data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-baseweb="select"],
        section[data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
        section[data-testid="stSidebar"] [data-testid="stSelectbox"] [role="combobox"],
        section[data-testid="stSidebar"] [data-testid="stSelectbox"] button[role="combobox"] {
            background-color: #FFFFFF !important;
            border-color: rgba(255,255,255,.80) !important;
            color: #10243E !important;
            -webkit-text-fill-color: #10243E !important;
            opacity: 1 !important;
        }
        section[data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-baseweb="select"] *,
        section[data-testid="stSidebar"] [data-testid="stSelectbox"] [role="combobox"] *,
        section[data-testid="stSidebar"] [data-testid="stSelectbox"] button[role="combobox"] *,
        section[data-testid="stSidebar"] [data-testid="stSelectbox"] span,
        section[data-testid="stSidebar"] [data-testid="stSelectbox"] p,
        section[data-testid="stSidebar"] [data-testid="stSelectbox"] input {
            color: #10243E !important;
            -webkit-text-fill-color: #10243E !important;
            text-shadow: none !important;
            opacity: 1 !important;
            visibility: visible !important;
        }
        section[data-testid="stSidebar"] [data-testid="stSelectbox"] svg {
            color: #1C4E80 !important;
            fill: #1C4E80 !important;
            opacity: 1 !important;
        }
        /* Opciones abiertas del desplegable. */
        body > div[data-baseweb="popover"] [role="option"],
        body > div[data-baseweb="popover"] [role="option"] *,
        [data-baseweb="popover"] [role="listbox"] [role="option"],
        [data-baseweb="popover"] [role="listbox"] [role="option"] * {
            color: #10243E !important;
            -webkit-text-fill-color: #10243E !important;
            background-color: #FFFFFF !important;
            opacity: 1 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )



def render_vitae_copilot(modulo_actual: str | None = None) -> None:
    """Copiloto persistente y discreto: no ocupa el sidebar ni deforma la app."""
    actual = str(
        modulo_actual
        or st.session_state.get("vitae_current_module", "")
        or "Inicio"
    ).strip()
    st.session_state["vitae_current_module"] = actual

    # Barra mínima: el contenido completo vive dentro de un popover.
    # Así el Dashboard y los módulos conservan todo su ancho.
    _, copilot_col = st.columns([8.6, 1.4])
    with copilot_col:
        with st.popover("✦ Copiloto Vitae", use_container_width=True):
            st.markdown("### ✦ Copiloto Vitae")
            st.caption(f"Contexto activo · {actual}")
            st.markdown(
                "Preguntame sobre esta pantalla o sobre cualquier área de Vitae. "
                "Mantengo el contexto mientras recorrés la plataforma."
            )

            q1, q2, q3 = st.columns(3)
            quick_question = None
            if q1.button("Explicame", key=f"copilot_help_{actual}", use_container_width=True):
                quick_question = "Explicame esta pantalla, para qué sirve y qué debería mirar primero."
            if q2.button("Analizar", key=f"copilot_analyze_{actual}", use_container_width=True):
                quick_question = "Analizá el módulo en el que estoy y decime qué es lo más importante que debería saber ahora."
            if q3.button("Prioridades", key=f"copilot_priority_{actual}", use_container_width=True):
                quick_question = "Según toda la información disponible de Vitae, ¿qué requiere mi atención primero y por qué?"

            history = obtener_historial_copiloto()
            if history:
                st.markdown("---")
                for item in history[-5:]:
                    pregunta = str(item.get("pregunta", "")).strip()
                    respuesta = str(item.get("respuesta", "")).strip()
                    if pregunta:
                        st.markdown(f"**Vos** · {pregunta}")
                    if respuesta:
                        st.markdown(respuesta)

            st.markdown("---")
            question = st.text_area(
                "Consulta",
                key="vitae_copilot_question",
                placeholder="Preguntame cualquier cosa sobre Vitae…",
                height=90,
                label_visibility="collapsed",
            )

            c_send, c_clear = st.columns([4, 1])
            send = c_send.button("Enviar", key="vitae_copilot_send", type="primary", use_container_width=True)
            clear = c_clear.button("↺", key="vitae_copilot_clear", help="Nueva conversación", use_container_width=True)

            if clear:
                limpiar_historial_copiloto()
                st.session_state["vitae_copilot_last_answer"] = ""
                st.session_state["vitae_copilot_last_question"] = ""
                st.rerun()

            final_question = quick_question or (
                question.strip()
                if send and question.strip()
                else ""
            )

            if final_question:
                try:
                    with st.spinner("Analizando Vitae…"):
                        answer = preguntar_copiloto(
                            final_question,
                            modulo_actual=actual,
                            ruta_actual=actual,
                        )

                    st.session_state["vitae_copilot_last_answer"] = str(answer or "").strip()
                    st.session_state["vitae_copilot_last_question"] = final_question

                except Exception as error:
                    st.session_state["vitae_copilot_last_answer"] = (
                        "No pude completar la consulta.\n\n"
                        f"**Detalle técnico:** `{error}`"
                    )
                    st.session_state["vitae_copilot_last_question"] = final_question

            last_question = str(
                st.session_state.get("vitae_copilot_last_question", "") or ""
            ).strip()
            last_answer = str(
                st.session_state.get("vitae_copilot_last_answer", "") or ""
            ).strip()

            if last_answer:
                st.markdown("---")
                if last_question:
                    st.markdown(f"**Vos** · {last_question}")
                st.markdown("**✦ Copiloto Vitae**")
                st.markdown(last_answer)


def render_header() -> None:
    _render_vitae_app_shell()
    render_vitae_copilot()
    st.markdown(
        """
        <div class="vitae-app-header">
            <div>
                <div class="vitae-eyebrow">Ecosistema de gestión clínica</div>
                <div class="vitae-title">VITAE</div>
                <div class="vitae-subtitle">
                    Gestión integral de Vitae Medicina Reproductiva y Vitae Medical ·
                    información operativa, financiera y ejecutiva en un solo lugar.
                </div>
            </div>
            <div class="vitae-live">Sistema operativo</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

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
    "pagado", "pagada", "cobrado", "cobrada", "abonado", "abonada",
    "completo", "completa", "completado", "completada", "realizado",
    "realizada", "finalizada", "finalizado", "cerrado", "cerrada",
    "cancelado", "cancelada", "anulado", "anulada",
}
_DG_ESTADOS_CANCELADOS = {
    "cancelado", "cancelada", "anulado", "anulada", "suspendido",
    "suspendida",
}
# IMPORTANTE: estados estrictamente financieros. "Realizado", "finalizado" o
# "cerrado" NO significan cobrado y jamás deben cancelar un saldo económico.
_DG_ESTADOS_PAGADOS = {
    "pagado", "pagada", "cobrado", "cobrada", "abonado", "abonada",
    "pago", "cobro", "saldado", "saldada",
}
_DG_ESTADOS_REALIZADOS = {
    "realizado", "realizada", "finalizado", "finalizada", "completado",
    "completada", "completo", "completa", "cerrado", "cerrada",
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

    original = _dg_series(df, column).copy()
    raw = original.astype("string").str.strip().replace("", pd.NA)

    # Una columna llamada "mes" puede contener simplemente 1..12. Eso no es
    # una fecha completa y no debe convertirse en 1970 por accidente.
    if _dg_norm(column) == "mes":
        month_only = raw.str.fullmatch(r"(?:0?[1-9]|1[0-2])", na=False)
        raw = raw.mask(month_only, pd.NA)

    # Misma estrategia que usa Facturación VM/VMR: primero ISO, luego formato
    # local día/mes/año y finalmente serial Excel / Google Sheets.
    parsed = pd.to_datetime(raw, format="%Y-%m-%d", errors="coerce")
    missing = parsed.isna()
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(
            raw.loc[missing],
            dayfirst=True,
            errors="coerce",
        )

    numeric = pd.to_numeric(original, errors="coerce")
    serial_mask = parsed.isna() & numeric.between(20000, 80000, inclusive="both")
    if serial_mask.any():
        parsed.loc[serial_mask] = (
            pd.Timestamp("1899-12-30")
            + pd.to_timedelta(numeric.loc[serial_mask], unit="D")
        )

    try:
        if getattr(parsed.dt, "tz", None) is not None:
            parsed = parsed.dt.tz_localize(None)
    except Exception:
        pass
    return parsed.dt.normalize()


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
        "Facturación": ["fecha_factura", "fecha", "mes", "created_at"],
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
    account_receivables_usd = 0.0
    account_payables_usd = 0.0
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
            # REGLA ÚNICA: el Dashboard reproduce exactamente la lectura usada
            # por render_facturacion_industrial(). El período de Facturación no
            # se define por fecha_factura solamente: usa fecha de servicio/mes,
            # luego fecha_factura y finalmente vencimiento. De esta forma VM y
            # VMR deben coincidir con sus módulos de origen para el mismo filtro.
            service_date = _dg_date_series(
                df,
                ["mes", "fecha", "fecha_procedimiento", "fecha práctica", "fecha_practica"],
            )
            invoice_date = _dg_date_series(df, ["fecha_factura", "fecha factura"])
            payment_date = _dg_date_series(df, ["fecha_pago", "fecha pago", "fecha_cobro", "cobrado_el"])
            due_date = _dg_date_series(df, ["vencimiento", "fecha_vencimiento", "fecha vencimiento"])
            base_date = service_date.combine_first(invoice_date).combine_first(due_date)

            amount = _dg_money_series(
                df,
                ["valor_pesos", "valor pesos", "importe", "monto", "facturado", "total"],
            ).fillna(0.0)

            # Misma clasificación del Centro de Control de Facturación.
            # Allí el cobro es por estado/fecha de pago; no se infiere por una
            # fórmula distinta dentro del Dashboard.
            status_source = _dg_text_series(
                df, ["estado", "estado factura", "situacion", "situación", "status"]
            ).map(_dg_norm)
            paid_words = ("cobrado", "pagado", "completo", "completado", "finalizado", "cancelado pago")
            cancelled_words = ("anulado", "anulada", "cancelado", "cancelada", "baja")
            is_cancelled = status_source.apply(
                lambda x: any(word in x for word in cancelled_words)
            )
            is_paid = status_source.apply(
                lambda x: any(word in x for word in paid_words)
            ) | payment_date.notna()
            is_paid = is_paid & ~is_cancelled

            paid = amount.where(is_paid, 0.0)
            pending = amount.where(~is_paid & ~is_cancelled, 0.0)
            active_amount = amount.where(~is_cancelled, 0.0)

            payer = _dg_text_series(df, ["obra_social", "obra social", "financiador", "cliente", "convenio"])
            procedure = _dg_text_series(df, ["procedimiento", "practica", "práctica", "prestacion"])
            doctor = _dg_text_series(df, ["medico_responsable", "médico responsable", "medico", "médico", "profesional"])
            patient = _dg_text_series(df, ["afiliado", "paciente", "nombre_paciente", "nombre paciente"])

            billing_frames.append(pd.DataFrame({
                "Módulo": module_name,
                "Empresa": company,
                "Fecha base": base_date,
                "Fecha servicio": service_date,
                "Fecha factura": invoice_date,
                "Fecha cobro": payment_date,
                "Vencimiento": due_date,
                "Monto": active_amount,
                "Cobrado": paid,
                "Pendiente": pending,
                "Anulado": amount.where(is_cancelled, 0.0),
                "Es anulado": is_cancelled,
                "Estado": status_text,
                "Obra social": payer.replace("", "Sin especificar"),
                "Procedimiento": procedure.replace("", "Sin especificar"),
                "Médico": doctor.replace("", "Sin especificar"),
                "Paciente": patient.replace("", "Sin especificar"),
            }))

        elif category == "Tesorería":
            movement_date = _dg_date_series(df, ["fecha", "fecha_movimiento", "fecha_operacion", "mes", "created_at"])
            income_candidates = ["ingreso", "ingresos", "credito", "creditos", "haber", "entrada", "acreditacion"]
            expense_candidates = ["egreso", "egresos", "debito", "debitos", "debe", "salida"]
            income = _dg_money_series(df, income_candidates)
            expense = _dg_money_series(df, expense_candidates)

            # Caja/Bancos también pueden guardar un único "monto/importe" y
            # clasificarlo por tipo. Reproducimos esa lógica para que el flujo
            # global coincida con el módulo de origen.
            has_income = _dg_first_column(df, income_candidates) is not None
            has_expense = _dg_first_column(df, expense_candidates) is not None
            if not has_income and not has_expense:
                amount = _dg_money_series(df, ["monto", "importe", "valor", "valor_pesos", "total"])
                movement_type = _dg_text_series(
                    df,
                    ["tipo_movimiento", "tipo", "clase", "debito_credito", "movimiento_tipo"],
                ).map(_dg_norm)
                is_income = movement_type.str.contains(
                    r"credito|ingreso|entrada|acreditacion|haber|deposito|cobro",
                    regex=True,
                    na=False,
                )
                is_expense = movement_type.str.contains(
                    r"debito|egreso|salida|pago|extraccion|debe",
                    regex=True,
                    na=False,
                )
                income = amount.where(is_income & ~is_expense, 0.0)
                expense = amount.where(is_expense & ~is_income, 0.0)

            movement_mask = _dg_period_mask(movement_date, start, end)
            cash_income_period += float(income.loc[movement_mask].sum())
            cash_expense_period += float(expense.loc[movement_mask].sum())

            balance_col = _dg_first_column(
                df,
                ["saldo_movimiento", "saldo_actual", "saldo", "balance", "saldo_calculado"],
            )
            if balance_col is not None:
                balance_series = pd.to_numeric(
                    _dg_series(df, balance_col).apply(money),
                    errors="coerce",
                )
                # La disponibilidad debe respetar el cierre seleccionado.
                # Nunca usar un saldo posterior a ``end`` cuando se consulta
                # un mes/año anterior.
                dated_valid = (
                    balance_series.notna()
                    & movement_date.notna()
                    & movement_date.le(end.normalize())
                )
                if dated_valid.any():
                    latest_index = movement_date.loc[dated_valid].idxmax()
                    balance = float(balance_series.loc[latest_index])
                else:
                    # Sin saldo fechado hasta el cierre seleccionado no se
                    # infiere una disponibilidad con filas futuras/sin fecha.
                    balance = 0.0
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

            # USD se mantiene separado. No se convierte ni se suma a ARS sin
            # un tipo de cambio explícito, porque hacerlo falsearía el total.
            balance_usd = _dg_money_series(
                df,
                ["saldo_usd", "saldo pendiente usd", "saldo usd", "saldo_dolares"],
            ).clip(lower=0)
            account_receivables_usd += float(balance_usd.loc[receivable_mask].sum())
            account_payables_usd += float(balance_usd.loc[~receivable_mask].sum())
            obligation_frames.append(pd.DataFrame({
                "Módulo": module_name,
                "Empresa": company,
                "Área": category,
                "Concepto": _dg_concept_series(df),
                "Saldo": balance.where(~receivable_mask, 0.0),
                "A cobrar": balance.where(receivable_mask, 0.0),
                "Fecha referencia": primary_dates,
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
                "Fecha referencia": primary_dates,
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
                    "Fecha referencia": primary_dates,
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
        # MISMO FILTRO que Facturación VM / VMR: fecha base de la prestación.
        billing_period_mask = _dg_period_mask(billing["Fecha base"], start, end)
        collection_date_mask = _dg_period_mask(billing["Fecha cobro"], start, end) & billing["Cobrado"].gt(0)

        billed_period = float(billing.loc[billing_period_mask, "Monto"].sum())
        # Para que el Dashboard coincida con la tarjeta "Cobrado" del módulo de
        # Facturación, este importe pertenece a los REGISTROS del período
        # seleccionado, aunque el pago se haya registrado en otra fecha.
        collected_period = float(billing.loc[billing_period_mask, "Cobrado"].sum())
        cohort_collected_period = collected_period
        # Se conserva aparte el flujo efectivamente cobrado por fecha de pago.
        cash_collected_period = float(billing.loc[collection_date_mask, "Cobrado"].sum())

        billing_pending_period = float(billing.loc[billing_period_mask, "Pendiente"].sum())
        billing_pending = float(billing["Pendiente"].sum())
        collected_without_date = float(
            billing.loc[billing["Cobrado"].gt(0) & billing["Fecha cobro"].isna(), "Cobrado"].sum()
        )
        collected_without_date_count = int(
            (billing["Cobrado"].gt(0) & billing["Fecha cobro"].isna()).sum()
        )

        period_rows = billing.loc[billing_period_mask]
        records_period = int(len(period_rows))
        patients_period = int(
            period_rows.loc[period_rows["Paciente"].ne("Sin especificar"), "Paciente"].nunique()
        )
        doctors_period = int(
            period_rows.loc[period_rows["Médico"].ne("Sin especificar"), "Médico"].nunique()
        )
        procedures_period = int(
            period_rows.loc[period_rows["Procedimiento"].ne("Sin especificar"), "Procedimiento"].nunique()
        )
    else:
        billing_period_mask = pd.Series(dtype="bool")
        collection_date_mask = pd.Series(dtype="bool")
        billed_period = collected_period = cohort_collected_period = cash_collected_period = billing_pending_period = billing_pending = 0.0
        collected_without_date = 0.0
        collected_without_date_count = 0
        records_period = patients_period = doctors_period = procedures_period = 0

    liquidity = float(treasury["Disponible"].sum()) if not treasury.empty else 0.0

    # Toda cifra rotulada como "del período" usa exactamente el mismo rango
    # seleccionado en el encabezado. Conservamos además el universo actual /
    # histórico por separado sólo para vistas explícitamente identificadas.
    if not obligations.empty:
        obligation_balance = float(obligations["Saldo"].sum())
        obligation_period_mask = _dg_period_mask(obligations["Fecha referencia"], start, end)
        obligations_period = obligations.loc[obligation_period_mask].copy()
        total_payables_period = float(obligations_period["Saldo"].sum())
    else:
        obligation_balance = 0.0
        obligations_period = obligations.copy()
        total_payables_period = 0.0

    # Las cuentas corrientes a pagar ya están incluidas en obligations["Saldo"].
    total_payables = obligation_balance
    # Dos universos distintos, deliberadamente separados:
    # 1) pendiente del período: sólo saldo de facturas emitidas en el rango;
    # 2) cartera histórica total: facturación histórica + otras cuentas a cobrar.
    # Mezclarlos en un único KPI fue la causa de cifras confusas en el resumen.
    total_receivables_period = billing_pending_period
    total_receivables = billing_pending + account_receivables

    cutoff = min(end.normalize(), today)

    if not billing.empty:
        pending_billing = billing[billing["Pendiente"].gt(0)].copy()
        pending_billing["Días vencido"] = (cutoff - pending_billing["Vencimiento"]).dt.days
        pending_billing["Vencido"] = (
            pending_billing["Vencimiento"].notna()
            & pending_billing["Vencimiento"].le(cutoff)
        )
        pending_billing_period = pending_billing.loc[
            _dg_period_mask(pending_billing["Fecha base"], start, end)
        ].copy()
    else:
        pending_billing = pd.DataFrame()
        pending_billing_period = pd.DataFrame()

    if not dues.empty:
        active_dues = dues[dues["Saldo"].gt(0)].copy()
        # En Riesgos/Vencidos el filtro se aplica a la FECHA DE VENCIMIENTO:
        # elegir "Mes actual" muestra vencimientos de ese mes, no toda la deuda histórica.
        active_dues_period = active_dues.loc[
            _dg_period_mask(active_dues["Vencimiento"], start, end)
        ].copy()
    else:
        active_dues = pd.DataFrame(columns=["Vencimiento", "Saldo"])
        active_dues_period = active_dues.copy()

    overdue_due = active_dues_period[
        active_dues_period["Vencimiento"].notna()
        & active_dues_period["Vencimiento"].le(cutoff)
    ].copy() if not active_dues_period.empty else active_dues_period.copy()

    upcoming_start = max(today, start.normalize())
    upcoming_end = min(today + pd.Timedelta(days=30), end.normalize())
    if upcoming_start <= upcoming_end and not active_dues_period.empty:
        upcoming_due = active_dues_period[
            active_dues_period["Vencimiento"].between(upcoming_start, upcoming_end, inclusive="both")
        ].copy()
    else:
        upcoming_due = active_dues_period.iloc[0:0].copy()

    overdue_billing = (
        pending_billing_period[pending_billing_period.get("Vencido", False)].copy()
        if not pending_billing_period.empty else pending_billing_period.copy()
    )
    overdue_amount = float(overdue_due["Saldo"].sum()) + (
        float(overdue_billing["Pendiente"].sum()) if not overdue_billing.empty else 0.0
    )
    overdue_count = int(len(overdue_due) + len(overdue_billing))
    upcoming_amount = float(upcoming_due["Saldo"].sum()) if not upcoming_due.empty else 0.0

    open_tasks = tasks[tasks["Abierta"]].copy() if not tasks.empty else tasks.copy()
    open_tasks_period = (
        open_tasks.loc[_dg_period_mask(open_tasks["Vencimiento"], start, end)].copy()
        if not open_tasks.empty else open_tasks.copy()
    )
    overdue_tasks = open_tasks_period[
        open_tasks_period["Vencimiento"].notna() & open_tasks_period["Vencimiento"].le(cutoff)
    ].copy() if not open_tasks_period.empty else open_tasks_period.copy()

    if not contracts.empty:
        contracts_period = contracts.loc[_dg_period_mask(contracts["Vencimiento"], start, end)].copy()
        expiring_contracts = contracts_period[contracts_period["Activo"]].copy()
    else:
        contracts_period = contracts.copy()
        expiring_contracts = contracts.copy()

    if not operations.empty:
        operation_mask = _dg_period_mask(operations["Fecha"], start, end)
        operations_period = operations.loc[operation_mask].copy()
    else:
        operations_period = operations.copy()

    collection_rate = (cohort_collected_period / billed_period * 100) if billed_period > 0 else 0.0
    coverage_ratio = (liquidity / total_payables_period) if total_payables_period > 0 else None
    cash_flow = cash_income_period - cash_expense_period
    ticket = billed_period / records_period if records_period else 0.0
    quality_average = float(modules["Calidad %"].mean()) if not modules.empty else 0.0

    return {
        "period_start": start,
        "period_end": end,
        "modules": modules,
        "billing": billing,
        "treasury": treasury,
        "obligations": obligations,
        "obligations_period": obligations_period,
        "dues": dues,
        "tasks": tasks,
        "contracts": contracts,
        "operations": operations,
        "operations_period": operations_period,
        "pending_billing": pending_billing,
        "pending_billing_period": pending_billing_period,
        "overdue_billing": overdue_billing,
        "overdue_due": overdue_due,
        "upcoming_due": upcoming_due,
        "open_tasks": open_tasks,
        "open_tasks_period": open_tasks_period,
        "overdue_tasks": overdue_tasks,
        "expiring_contracts": expiring_contracts,
        "billed_period": billed_period,
        "collected_period": collected_period,
        "cohort_collected_period": cohort_collected_period,
        "cash_collected_period": cash_collected_period,
        "collected_without_date": collected_without_date,
        "collected_without_date_count": collected_without_date_count,
        "billing_pending_period": billing_pending_period,
        "billing_pending": billing_pending,
        "account_receivables": account_receivables,
        "account_payables": account_payables,
        "account_receivables_usd": account_receivables_usd,
        "account_payables_usd": account_payables_usd,
        "total_receivables_period": total_receivables_period,
        "total_receivables": total_receivables,
        "total_payables": total_payables,
        "total_payables_period": total_payables_period,
        "debt_control": debt_control,
        "liquidity": liquidity,
        "cash_income_period": cash_income_period,
        "cash_expense_period": cash_expense_period,
        "registered_expenses_period": registered_expenses_period,
        "cash_flow": cash_flow,
        "records_period": records_period,
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
                period_mask = _dg_period_mask(company_billing["Fecha base"], start, end)
                period_rows = company_billing.loc[period_mask]
                billed = float(period_rows["Monto"].sum())
                collected = float(period_rows["Cobrado"].sum())
                cohort_collected = collected
                pending = float(period_rows["Pendiente"].sum())
                patients = int(
                    period_rows.loc[period_rows["Paciente"].ne("Sin especificar"), "Paciente"].nunique()
                )
            else:
                billed = collected = cohort_collected = pending = 0.0
                patients = 0
            available = (
                float(treasury.loc[treasury["Empresa"].eq(company), "Disponible"].sum())
                if not treasury.empty else 0.0
            )
            rate = (cohort_collected / billed * 100) if billed > 0 else 0.0
            st.markdown(f"#### {company}")
            a, b = st.columns(2)
            a.metric("Facturado", fmt_money(billed))
            b.metric("Cobrado · facturas del período", fmt_money(collected), delta=f"{rate:.1f}% del facturado")
            c, d = st.columns(2)
            c.metric("Pendiente del período", fmt_money(pending))
            d.metric("Pacientes", patients)
            st.caption(f"Disponibilidad registrada: {fmt_money(available)}")


def _dg_render_alerts(model: dict[str, Any]) -> None:
    alerts: list[tuple[str, str]] = []
    strengths: list[str] = []

    if model["overdue_count"] > 0:
        alerts.append((
            "error",
            f"En el período seleccionado hay {model['overdue_count']} compromisos o saldos vencidos por {fmt_money(model['overdue_amount'])}.",
        ))
    else:
        strengths.append("No se detectaron compromisos vencidos con saldo en la información disponible.")

    if model["billed_period"] > 0 and model["collection_rate"] < 75:
        alerts.append((
            "warning",
            f"De la facturación emitida en el período, figura cobrado el {model['collection_rate']:.1f}% al cierre seleccionado; requiere seguimiento de recupero.",
        ))
    elif model["billed_period"] > 0:
        strengths.append(f"La facturación del período registra una cobranza del {model['collection_rate']:.1f}% al cierre seleccionado.")

    coverage = model["coverage_ratio"]
    if coverage is not None and coverage < 1:
        alerts.append((
            "error",
            f"La liquidez al cierre cubre {coverage:.2f} veces las obligaciones del período; existe una brecha de cobertura.",
        ))
    elif coverage is not None:
        strengths.append(f"La liquidez al cierre cubre {coverage:.2f} veces las obligaciones del período.")

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

    modules_without_data = int((model["modules"]["Registros"] == 0).sum()) if not model["modules"].empty else 0
    if modules_without_data:
        alerts.append(("info", f"Hay {modules_without_data} módulos sin registros visibles en la fuente estándar del tablero."))

    billing = model["billing"]
    if not billing.empty:
        period_billing_mask = _dg_period_mask(billing["Fecha base"], model["period_start"], model["period_end"])
        current_mask = billing["Monto"].gt(0) & period_billing_mask
        payer = billing.loc[current_mask].groupby("Obra social")["Monto"].sum().sort_values(ascending=False)
        if payer.sum() > 0 and not payer.empty:
            concentration = payer.iloc[0] / payer.sum() * 100
            if concentration >= 40:
                alerts.append((
                    "warning",
                    f"La principal obra social concentra {concentration:.1f}% de la facturación del período seleccionado.",
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
        gap = max(model["total_payables_period"] - model["liquidity"], 0)
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
        "Cobrado · facturas del período",
        fmt_money(model["collected_period"]),
        delta=f"{model['collection_rate']:.1f}% del facturado" if model["billed_period"] > 0 else None,
        help="Usa exactamente los mismos registros filtrados por fecha base que Facturación VM/VMR.",
    )
    k4.metric("Flujo neto de fondos", fmt_money(model["cash_flow"]))

    k5, k6, k7, k8 = st.columns(4)
    k5.metric(
        "Pendiente facturas del período",
        fmt_money(model["billing_pending_period"]),
        help=(
            "Saldo todavía no cobrado de las facturas emitidas entre "
            f"{start.strftime('%d/%m/%Y')} y {end.strftime('%d/%m/%Y')}. "
            "No incluye cartera histórica ni cuentas corrientes."
        ),
    )
    k6.metric("Obligaciones del período", fmt_money(model["total_payables_period"]))
    k7.metric("Vencido en período", fmt_money(model["overdue_amount"]), delta=f"{model['overdue_count']} registros")
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
            chart_start = start.replace(day=1)
            months = pd.period_range(chart_start.to_period("M"), end.to_period("M"), freq="M")
            fact = (
                billing[_dg_period_mask(billing["Fecha base"], chart_start, end)]
                .assign(Mes=lambda x: x["Fecha base"].dt.to_period("M").astype(str))
                .groupby("Mes", as_index=False)["Monto"].sum()
                .rename(columns={"Monto": "Facturado"})
            )
            coll = (
                billing[_dg_period_mask(billing["Fecha base"], chart_start, end)]
                .assign(Mes=lambda x: x["Fecha base"].dt.to_period("M").astype(str))
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
    f1.metric("Ingresos de fondos · período", fmt_money(model["cash_income_period"]))
    f2.metric("Egresos de fondos · período", fmt_money(model["cash_expense_period"]))
    f3.metric("Pendiente facturas · período", fmt_money(model["billing_pending_period"]))
    f4.metric("Obligaciones · período", fmt_money(model["total_payables_period"]))
    st.caption(
        "Cobros con fecha de pago dentro del período: "
        f"{fmt_money(model.get('cash_collected_period', 0.0))}. "
        "Este dato es flujo por fecha de cobro y se mantiene separado del KPI de Facturación, "
        "que replica exactamente el filtro del módulo VM/VMR."
    )

    with st.expander("Cartera histórica / saldos actuales (fuera del filtro temporal)", expanded=False):
        c1, c2, c3 = st.columns(3)
        c1.metric(
            "Facturación pendiente · histórica",
            fmt_money(model["billing_pending"]),
            help="Saldo pendiente de todas las facturas cargadas, sin limitarlo al período seleccionado.",
        )
        c2.metric(
            "Otras cuentas a cobrar · actuales",
            fmt_money(model["account_receivables"]),
            help="Saldos identificados explícitamente como A cobrar en los módulos de Cuenta Corriente.",
        )
        c3.metric(
            "Cartera total a cobrar · histórica",
            fmt_money(model["total_receivables"]),
            help="Facturación pendiente histórica + otras cuentas a cobrar. No representa el período seleccionado.",
        )
    if model.get("account_receivables_usd", 0) > 0 or model.get("account_payables_usd", 0) > 0:
        st.caption(
            "Cuenta corriente en USD (sin convertir ni mezclar con ARS): "
            f"a cobrar USD {model['account_receivables_usd']:,.2f} · "
            f"a pagar USD {model['account_payables_usd']:,.2f}."
        )
    if model.get("collected_without_date_count", 0) > 0:
        st.warning(
            f"Hay {model['collected_without_date_count']} registros cobrados por "
            f"{fmt_money(model['collected_without_date'])} sin fecha de cobro. "
            "El saldo está conciliado, pero esos importes no se imputan a ningún período."
        )
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
        obligations = model["obligations_period"]
        if obligations.empty or obligations["Saldo"].sum() <= 0:
            st.success("No hay obligaciones con saldo en los módulos analizados.")
        else:
            # Cuentas corrientes ya forma parte de obligations. No volver a
            # agregar account_payables: hacerlo duplicaba esa deuda en el gráfico.
            grouped = obligations.groupby("Área", as_index=False)["Saldo"].sum()
            grouped = grouped.sort_values("Saldo", ascending=False)
            fig = px.bar(grouped, x="Área", y="Saldo", text_auto=".2s")
            fig.update_layout(height=350, margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(fig, use_container_width=True)
    with right:
        st.markdown("#### Antigüedad de saldos a cobrar")
        pending = model["pending_billing_period"].copy()
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
        period_billing = billing[_dg_period_mask(billing["Fecha base"], start, end)].copy()
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
    period_billing = billing[_dg_period_mask(billing["Fecha base"], start, end)].copy() if not billing.empty else pd.DataFrame()

    completed = 0
    cancelled = 0
    pending = 0
    if not operations.empty:
        completed = int(operations["Estado normalizado"].isin(_DG_ESTADOS_REALIZADOS).sum())
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
        source = operations if not operations.empty else period_billing.rename(columns={"Fecha base": "Fecha"})
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
            "Pacientes / registros facturados": int(
                bill.loc[bill["Paciente"].ne("Sin especificar"), "Paciente"].nunique()
            ) if not bill.empty else 0,
            "Facturación": float(bill["Monto"].sum()) if not bill.empty else 0.0,
            "Agenda": int(len(ops)),
            "Realizados": int(ops["Estado normalizado"].isin(_DG_ESTADOS_REALIZADOS).sum()) if not ops.empty else 0,
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
    st.markdown("### Riesgos, vencimientos y continuidad · período seleccionado")
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Vencidos en período", model["overdue_count"])
    r2.metric("Monto vencido · período", fmt_money(model["overdue_amount"]))
    r3.metric("Tareas del período", len(model["open_tasks_period"]))
    r4.metric("Contratos con vencimiento · período", len(model["expiring_contracts"]))

    st.markdown("#### Saldos con vencimiento dentro del período")
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
        cutoff = min(model["period_end"].normalize(), today)
        overdue["Días vencido"] = (cutoff - overdue["Vencimiento"]).dt.days
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
        st.success("No se detectaron saldos vencidos dentro del período seleccionado.")

    left, right = st.columns(2)
    with left:
        st.markdown("#### Tareas con vencimiento en el período")
        tasks = model["open_tasks_period"].copy()
        if tasks.empty:
            st.success("No hay tareas con vencimiento dentro del período seleccionado.")
        else:
            tasks["Días"] = (tasks["Vencimiento"] - min(model["period_end"].normalize(), today)).dt.days
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
            st.success("No hay contratos con vencimiento dentro del período seleccionado.")
        else:
            contracts["Días"] = (contracts["Vencimiento"] - min(model["period_end"].normalize(), today)).dt.days
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
        "Pendiente facturas del período": model["billing_pending_period"],
        "Facturación pendiente histórica": model["billing_pending"],
        "Otras cuentas a cobrar actuales": model["account_receivables"],
        "Cartera total a cobrar histórica": model["total_receivables"],
        "Obligaciones del período": model["total_payables_period"],
        "Importe vencido del período": model["overdue_amount"],
        "Cantidad de vencidos": model["overdue_count"],
        "Vence próximos 30 días": model["upcoming_amount"],
        "Flujo de caja período": model["cash_flow"],
        "Pacientes período": model["patients_period"],
        "Médicos activos": model["doctors_period"],
        "Procedimientos": model["procedures_period"],
        "Tareas del período": len(model["open_tasks_period"]),
        "Tareas vencidas": len(model["overdue_tasks"]),
        "Contratos próximos a vencer": len(model["expiring_contracts"]),
        "Calidad promedio %": round(model["quality_average"], 2),
    }])

    pending = model["pending_billing_period"].copy()
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

    obligations = model["obligations_period"].copy()
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
        active_dues = dues[
            dues["Saldo"].gt(0)
            & _dg_period_mask(dues["Vencimiento"], start, end)
        ].copy()
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

    tasks = model["open_tasks_period"].copy()
    if not tasks.empty:
        if "Vencimiento" in tasks.columns:
            tasks = tasks.sort_values("Vencimiento", na_position="last")
        context["Tareas del período"] = _dg_ai_clean_frame(tasks, max_rows=80)

    contracts = model["expiring_contracts"].copy()
    if not contracts.empty:
        if "Vencimiento" in contracts.columns:
            contracts = contracts.sort_values("Vencimiento")
        context["Contratos con vencimiento en el período"] = _dg_ai_clean_frame(
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
    if model["billing_pending_period"] > 0:
        priorities.append(
            f"Gestionar {fmt_money(model['billing_pending_period'])} pendientes de facturas emitidas en el período."
        )
    elif model["total_receivables"] > 0:
        priorities.append(
            f"Revisar la cartera histórica a cobrar de {fmt_money(model['total_receivables'])}."
        )
    if model["total_payables_period"] > model["liquidity"]:
        gap = model["total_payables_period"] - model["liquidity"]
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
        f"- Tareas del período: **{len(model['open_tasks_period'])}**; vencidas: "
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
        pending = model["pending_billing_period"].copy()
        obligations = model["obligations_period"].copy()
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
                & other_receivables["Vencimiento"].le(min(end.normalize(), today))
            )
            overdue_other = float(other_receivables.loc[mask, "A cobrar"].sum())

        lines = [
            f"## Cobranza del período: {fmt_money(model['billing_pending_period'])} pendiente",
            f"- Pendiente de facturas emitidas en el período: **{fmt_money(model['billing_pending_period'])}**.",
            f"- Facturación pendiente histórica: **{fmt_money(model['billing_pending'])}**.",
            f"- Otras cuentas a cobrar actuales: **{fmt_money(model['account_receivables'])}**.",
            f"- Cartera total a cobrar histórica: **{fmt_money(model['total_receivables'])}**.",
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
        obligations = model["obligations_period"].copy()
        payables = obligations[obligations["Saldo"].gt(0)].copy() if not obligations.empty else pd.DataFrame()
        lines = [
            f"## Obligaciones del período: {fmt_money(model['total_payables_period'])}",
            f"- Liquidez disponible: **{fmt_money(model['liquidity'])}**.",
        ]
        gap = model["liquidity"] - model["total_payables_period"]
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
            f"- Contratos con vencimiento en el período: **{len(model['expiring_contracts'])}**."
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
    st.session_state["vitae_current_module"] = "Dashboard Global"
    _render_vitae_app_shell()
    render_vitae_copilot("Dashboard Global")
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
        f"{modules_with_data} módulos con datos · {total_records:,} registros fuente"
    )
    st.caption(
        f"Filtro activo en indicadores temporales: {start.strftime('%d/%m/%Y')} — {end.strftime('%d/%m/%Y')}. "
        "La disponibilidad muestra el último saldo existente hasta la fecha de cierre seleccionada."
    )

    with export_col:
        snapshot = pd.DataFrame([{
            "Desde": start.strftime("%d/%m/%Y"),
            "Hasta": end.strftime("%d/%m/%Y"),
            "Liquidez": model["liquidity"],
            "Facturado": model["billed_period"],
            "Cobrado de facturas del período": model["collected_period"],
            "Cobros por fecha de pago en período": model.get("cash_collected_period", 0.0),
            "Cobrado sin fecha": model["collected_without_date"],
            "Pendiente facturas del período": model["billing_pending_period"],
            "Facturación pendiente histórica": model["billing_pending"],
            "Otras cuentas a cobrar actuales": model["account_receivables"],
            "Cartera total a cobrar histórica": model["total_receivables"],
            "Obligaciones del período": model["total_payables_period"],
            "Vencido en período": model["overdue_amount"],
            "Pacientes": model["patients_period"],
            "Tareas del período": len(model["open_tasks_period"]),
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




# =========================================================
# DEUDAS IMPOSITIVAS VM · CENTRO DE CONTROL PROFESIONAL
# =========================================================
_DI_VM_COLUMNS = [
    "id_registro",
    "organismo",
    "impuesto",
    "periodo",
    "fecha_vencimiento",
    "capital",
    "intereses",
    "cuota_sindical",
    "cuota_solidaridad",
    "contribucion_convencional",
    "deuda_total",
    "importe_pagado",
    "saldo",
    "estado",
    "fecha_pago",
    "plan_pago",
    "cuota_plan",
    "observaciones",
    "hoja_origen",
    "fila_origen",
    "archivo_origen",
    "created_at",
    "updated_at",
]

_DI_VM_MANUAL_COLUMNS = {
    "importe_pagado",
    "fecha_pago",
    "estado",
    "plan_pago",
    "cuota_plan",
    "observaciones",
}


def _di_vm_entity_label(table: str = "", module_name: str = "") -> str:
    """Devuelve VM o VMR sin depender del nombre exacto configurado en MODULES."""
    identity = _di_vm_norm(f"{table} {module_name}")
    return "VMR" if re.search(r"\bvmr\b", identity) else "VM"


def _di_vm_entity_name(table: str = "", module_name: str = "") -> str:
    return (
        "Vitae Medicina Reproductiva"
        if _di_vm_entity_label(table, module_name) == "VMR"
        else "Vitae Medical"
    )


def _di_vm_export_slug(table: str = "", module_name: str = "") -> str:
    return f"deudas_impositivas_{_di_vm_entity_label(table, module_name).lower()}"


def _di_vm_norm(value: Any) -> str:
    import unicodedata

    text = "" if value is None or pd.isna(value) else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().strip().replace("_", " ").replace("-", " ")
    return " ".join(text.split())


def _di_vm_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "nat"} else text


def _di_vm_number(value: Any) -> float:
    """Convierte números argentinos, valores Excel y textos monetarios."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        try:
            return 0.0 if pd.isna(value) else float(value)
        except (TypeError, ValueError):
            return 0.0

    text = _di_vm_text(value)
    if not text:
        return 0.0
    negative = text.startswith("(") and text.endswith(")")
    text = text.replace("(", "").replace(")", "")
    text = re.sub(r"[^0-9,\.\-]", "", text)
    if not text or text in {"-", ".", ","}:
        return 0.0

    if "," in text and "." in text:
        # El último separador es el decimal; el otro es de miles.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts) > 2:
            text = "".join(parts[:-1]) + "." + parts[-1]
        else:
            text = text.replace(",", ".")
    elif text.count(".") > 1:
        parts = text.split(".")
        text = "".join(parts[:-1]) + "." + parts[-1]
    elif text.count(".") == 1:
        left, right = text.split(".")
        # 97.791 suele significar miles; 669.36 suele ser decimal.
        if len(right) == 3 and len(left.replace("-", "")) >= 1:
            text = left + right

    try:
        number = float(text)
    except (TypeError, ValueError):
        return 0.0
    return -abs(number) if negative else number


def _di_vm_period(value: Any) -> pd.Timestamp:
    if value is None:
        return pd.NaT
    if isinstance(value, (pd.Timestamp, date)):
        parsed = pd.Timestamp(value)
        return parsed.to_period("M").to_timestamp()

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            if pd.isna(value):
                return pd.NaT
            integer = int(float(value))
            if 190001 <= integer <= 219912:
                year, month = divmod(integer, 100)
                if 1 <= month <= 12:
                    return pd.Timestamp(year=year, month=month, day=1)
        except (TypeError, ValueError, OverflowError):
            pass

    text = _di_vm_text(value)
    if not text:
        return pd.NaT
    compact = re.sub(r"\D", "", text)
    if len(compact) == 6:
        integer = int(compact)
        year, month = divmod(integer, 100)
        if 1900 <= year <= 2199 and 1 <= month <= 12:
            return pd.Timestamp(year=year, month=month, day=1)

    month_map = {
        "ene": 1, "enero": 1, "jan": 1, "january": 1,
        "feb": 2, "febrero": 2, "february": 2,
        "mar": 3, "marzo": 3, "march": 3,
        "abr": 4, "abril": 4, "apr": 4, "april": 4,
        "may": 5, "mayo": 5,
        "jun": 6, "junio": 6, "june": 6,
        "jul": 7, "julio": 7, "july": 7,
        "ago": 8, "agosto": 8, "aug": 8, "august": 8,
        "sep": 9, "sept": 9, "septiembre": 9, "september": 9,
        "oct": 10, "octubre": 10, "october": 10,
        "nov": 11, "noviembre": 11, "november": 11,
        "dic": 12, "diciembre": 12, "dec": 12, "december": 12,
    }
    normalized = _di_vm_norm(text)
    match = re.search(r"([a-z]+)\s*[\-/ ]\s*(\d{2,4})", normalized)
    if match and match.group(1) in month_map:
        year = int(match.group(2))
        year += 2000 if year < 100 else 0
        return pd.Timestamp(year=year, month=month_map[match.group(1)], day=1)

    # También acepta MM/AAAA, fechas completas y seriales Excel ya convertidos.
    try:
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    except (TypeError, ValueError):
        parsed = pd.NaT
    if pd.isna(parsed):
        return pd.NaT
    return pd.Timestamp(parsed).to_period("M").to_timestamp()


def _di_vm_date(value: Any) -> pd.Timestamp:
    if value is None or _di_vm_text(value) == "":
        return pd.NaT
    try:
        parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    except (TypeError, ValueError):
        parsed = pd.NaT
    return pd.Timestamp(parsed).normalize() if pd.notna(parsed) else pd.NaT


def _di_vm_find_column(df: pd.DataFrame, aliases: list[str]) -> str | None:
    normalized = {_di_vm_norm(column): column for column in df.columns}
    for alias in aliases:
        alias_norm = _di_vm_norm(alias)
        if alias_norm in normalized:
            return normalized[alias_norm]
    # Segundo pase: coincidencia parcial controlada.
    for alias in aliases:
        alias_norm = _di_vm_norm(alias)
        for norm, original in normalized.items():
            if alias_norm and (norm.startswith(alias_norm) or alias_norm in norm):
                return original
    return None


def _di_vm_series(df: pd.DataFrame, aliases: list[str], default: Any = "") -> pd.Series:
    column = _di_vm_find_column(df, aliases)
    if column is None:
        return pd.Series([default] * len(df), index=df.index, dtype="object")
    series = df.loc[:, column]
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    return series


def _di_vm_identifier(row: pd.Series) -> str:
    import hashlib

    key = "|".join([
        _di_vm_norm(row.get("organismo", "")),
        _di_vm_norm(row.get("impuesto", "")),
        _di_vm_text(row.get("periodo", ""))[:10],
        _di_vm_text(row.get("fecha_vencimiento", ""))[:10],
        _di_vm_norm(row.get("hoja_origen", "")),
    ])
    return "DI-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:14].upper()


def _di_vm_prepare(df: pd.DataFrame, source: str = "Google Sheet") -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=_DI_VM_COLUMNS)

    result = pd.DataFrame(index=df.index)
    result["id_registro"] = _di_vm_series(df, ["id_registro", "id", "codigo", "registro"])
    result["organismo"] = _di_vm_series(
        df,
        ["organismo", "jurisdiccion", "ente", "sindicato", "reparticion", "categoria"],
    )
    result["impuesto"] = _di_vm_series(
        df,
        ["impuesto", "concepto", "tributo", "obligacion", "detalle", "descripcion"],
    )
    result["periodo"] = _di_vm_series(df, ["periodo", "mes", "periodo fiscal"])
    result["fecha_vencimiento"] = _di_vm_series(
        df,
        ["fecha_vencimiento", "fecha de vto", "fecha vto", "vencimiento", "fecha venc."],
    )
    result["capital"] = _di_vm_series(df, ["capital", "importe capital", "monto capital"], 0.0)
    result["intereses"] = _di_vm_series(df, ["intereses", "interes", "resarcitorios"], 0.0)
    result["cuota_sindical"] = _di_vm_series(df, ["cuota_sindical", "cuota sindical"], 0.0)
    result["cuota_solidaridad"] = _di_vm_series(
        df,
        ["cuota_solidaridad", "cuota solidaridad", "cuota solidaridad extraordinaria", "cuota solidaria"],
        0.0,
    )
    result["contribucion_convencional"] = _di_vm_series(
        df,
        ["contribucion_convencional", "contribucion convencional", "contribucion"],
        0.0,
    )
    result["deuda_total"] = _di_vm_series(
        df,
        ["deuda_total", "deuda total", "total deuda", "importe total", "total"],
        0.0,
    )
    result["importe_pagado"] = _di_vm_series(
        df,
        ["importe_pagado", "pagado", "monto pagado"],
        0.0,
    )
    result["saldo"] = _di_vm_series(df, ["saldo", "saldo pendiente"], 0.0)
    result["estado"] = _di_vm_series(df, ["estado", "situacion"])
    result["fecha_pago"] = _di_vm_series(df, ["fecha_pago", "fecha de pago"])
    result["plan_pago"] = _di_vm_series(df, ["plan_pago", "plan de pago", "en plan"])
    result["cuota_plan"] = _di_vm_series(df, ["cuota_plan", "cuota plan", "cuota"])
    result["observaciones"] = _di_vm_series(df, ["observaciones", "notas", "comentarios"])
    result["hoja_origen"] = _di_vm_series(df, ["hoja_origen", "hoja", "sheet"], source)
    result["fila_origen"] = _di_vm_series(df, ["fila_origen", "fila", "row"], 0)
    result["archivo_origen"] = _di_vm_series(df, ["archivo_origen", "archivo", "fuente"], source)
    result["created_at"] = _di_vm_series(df, ["created_at", "creado", "fecha_carga"])
    result["updated_at"] = _di_vm_series(df, ["updated_at", "actualizado", "fecha_actualizacion"])

    for column in [
        "capital", "intereses", "cuota_sindical", "cuota_solidaridad",
        "contribucion_convencional", "deuda_total", "importe_pagado", "saldo",
    ]:
        result[column] = result[column].apply(_di_vm_number).astype(float)

    result["periodo"] = result["periodo"].apply(_di_vm_period)
    result["fecha_vencimiento"] = result["fecha_vencimiento"].apply(_di_vm_date)
    result["fecha_pago"] = result["fecha_pago"].apply(_di_vm_date)
    result["fila_origen"] = pd.to_numeric(result["fila_origen"], errors="coerce").fillna(0).astype(int)

    for column in [
        "id_registro", "organismo", "impuesto", "estado", "plan_pago",
        "cuota_plan", "observaciones", "hoja_origen", "archivo_origen",
        "created_at", "updated_at",
    ]:
        result[column] = result[column].fillna("").astype(str).replace({"nan": "", "NaT": ""}).str.strip()

    components = result[
        ["capital", "intereses", "cuota_sindical", "cuota_solidaridad", "contribucion_convencional"]
    ].sum(axis=1)
    result.loc[result["deuda_total"].abs().le(0.0001), "deuda_total"] = components

    # Si el saldo no estaba guardado, se reconstruye. Nunca se fuerza a cero un saldo negativo
    # porque puede representar un pago excedente o nota de crédito real.
    missing_balance = result["saldo"].abs().le(0.0001) & result["deuda_total"].abs().gt(0.0001)
    result.loc[missing_balance, "saldo"] = (
        result.loc[missing_balance, "deuda_total"]
        - result.loc[missing_balance, "importe_pagado"]
    )

    meaningful = (
        result["organismo"].ne("")
        | result["impuesto"].ne("")
        | result["deuda_total"].abs().gt(0.0001)
        | result["capital"].abs().gt(0.0001)
        | result["intereses"].abs().gt(0.0001)
    )
    result = result[meaningful].copy()
    total_rows = (
        result["organismo"].apply(_di_vm_norm).str.startswith("total")
        | result["impuesto"].apply(_di_vm_norm).str.startswith("total")
    )
    result = result[~total_rows].copy()

    now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    result["created_at"] = result["created_at"].replace("", now)
    result["updated_at"] = now
    result["hoja_origen"] = result["hoja_origen"].replace("", source)
    result["archivo_origen"] = result["archivo_origen"].replace("", source)

    for index in result.index:
        if not _di_vm_text(result.at[index, "id_registro"]):
            result.at[index, "id_registro"] = _di_vm_identifier(result.loc[index])

    return _di_vm_refresh_status(result[_DI_VM_COLUMNS].reset_index(drop=True))


def _di_vm_refresh_status(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=_DI_VM_COLUMNS)
    result = df.copy()
    today = pd.Timestamp.today().normalize()
    result["deuda_total"] = pd.to_numeric(result["deuda_total"], errors="coerce").fillna(0.0)
    result["importe_pagado"] = pd.to_numeric(result["importe_pagado"], errors="coerce").fillna(0.0)
    result["saldo"] = result["deuda_total"] - result["importe_pagado"]
    result.loc[result["saldo"].abs().le(0.005), "saldo"] = 0.0
    due = pd.to_datetime(result["fecha_vencimiento"], errors="coerce")
    days = (due.dt.normalize() - today).dt.days
    current_state = result["estado"].fillna("").astype(str).apply(_di_vm_norm)

    protected = current_state.isin({
        "en plan de pagos", "plan de pagos", "judicializada", "discutida",
        "condonada", "prescripta",
    })
    automatic = pd.Series("Pendiente", index=result.index, dtype="object")
    automatic.loc[due.isna()] = "Sin vencimiento"
    automatic.loc[days.between(0, 30, inclusive="both")] = "Próxima a vencer"
    automatic.loc[days.lt(0)] = "Vencida"
    automatic.loc[result["saldo"].le(0.005)] = "Pagada"
    result.loc[~protected, "estado"] = automatic.loc[~protected]
    result.loc[protected & result["saldo"].le(0.005), "estado"] = "Pagada"
    return result


def _di_vm_header_kind(value: Any) -> str | None:
    norm = _di_vm_norm(value)
    if not norm:
        return None
    if "fecha" in norm and any(token in norm for token in ["vto", "venc", "vtc"]):
        return "fecha_vencimiento"
    if norm == "capital" or "importe capital" in norm:
        return "capital"
    if "interes" in norm:
        return "intereses"
    if "cuota sindical" in norm:
        return "cuota_sindical"
    # Algunas planillas históricas usan una única columna llamada
    # "Solidaridad / Extra contribución convenio". Se conserva completa
    # dentro de contribución convencional para no duplicar ni dividir importes.
    contribution_token = any(
        token in norm
        for token in ["contrib", "tribucion", "extratrib", "extrantrib"]
    )
    agreement_token = any(token in norm for token in ["convenc", "convenio", "extra"])
    if "solidar" in norm and contribution_token:
        return "contribucion_convencional"
    if contribution_token and agreement_token:
        return "contribucion_convencional"
    if "solidar" in norm or "extraordinaria" in norm:
        return "cuota_solidaridad"
    if norm in {"total", "deuda total", "total deuda", "importe total"} or ("deuda" in norm and "total" in norm):
        return "deuda_total"
    return None


def _di_vm_inferred_tax(kinds: dict[str, int]) -> str:
    labels = []
    if "cuota_sindical" in kinds:
        labels.append("Cuota sindical")
    if "cuota_solidaridad" in kinds:
        labels.append("Cuota solidaridad / extraordinaria")
    if "contribucion_convencional" in kinds:
        labels.append("Contribución convencional")
    if labels:
        return " + ".join(labels)
    if "capital" in kinds and "intereses" in kinds:
        return "Obligación impositiva"
    if "capital" in kinds:
        return "Capital adeudado"
    return "Deuda impositiva"


def _di_vm_parse_matrix(
    matrix: pd.DataFrame,
    sheet_name: str = "Hoja 1",
    filename: str = "Excel importado",
) -> pd.DataFrame:
    """Lee cuadros con celdas combinadas y varias tablas laterales sin alterar sus importes."""
    if matrix is None or matrix.empty:
        return pd.DataFrame(columns=_DI_VM_COLUMNS)

    raw = matrix.copy().reset_index(drop=True)
    raw.columns = list(range(len(raw.columns)))
    records: list[dict[str, Any]] = []
    nrows, ncols = raw.shape

    header_rows: list[tuple[int, list[int]]] = []
    for row_index in range(nrows):
        norms = [_di_vm_norm(raw.iat[row_index, col]) for col in range(ncols)]
        period_columns = [
            col for col, norm in enumerate(norms)
            if norm == "periodo" or norm.startswith("periodo ") or norm == "period"
        ]
        recognized_amounts = sum(_di_vm_header_kind(value) is not None for value in raw.iloc[row_index])
        if period_columns and recognized_amounts >= 1:
            header_rows.append((row_index, period_columns))

    for header_position, (header_row, period_columns) in enumerate(header_rows):
        next_header_row = header_rows[header_position + 1][0] if header_position + 1 < len(header_rows) else nrows
        for period_index, period_col in enumerate(period_columns):
            next_period_col = period_columns[period_index + 1] if period_index + 1 < len(period_columns) else ncols
            left_limit = period_columns[period_index - 1] + 1 if period_index > 0 else 0

            descriptor_col = max(period_col - 1, 0)
            for col in range(period_col - 1, left_limit - 1, -1):
                norm = _di_vm_norm(raw.iat[header_row, col])
                if any(token in norm for token in ["impuesto", "sindicato", "concepto", "tributo", "obligacion"]):
                    descriptor_col = col
                    break
            group_col = descriptor_col - 1 if descriptor_col - 1 >= left_limit else None

            kinds: dict[str, int] = {}
            scan_end = min(next_period_col, period_col + 8, ncols)
            for col in range(period_col + 1, scan_end):
                kind = _di_vm_header_kind(raw.iat[header_row, col])
                if kind is not None and kind not in kinds:
                    kinds[kind] = col
            if not kinds:
                continue

            group_ffill = ""
            descriptor_ffill = ""
            blank_streak = 0
            for row_index in range(header_row + 1, min(next_header_row, nrows)):
                row_text = [_di_vm_text(raw.iat[row_index, col]) for col in range(left_limit, scan_end)]
                if not any(row_text):
                    blank_streak += 1
                    if blank_streak >= 4:
                        break
                    continue
                blank_streak = 0

                descriptor_value = _di_vm_text(raw.iat[row_index, descriptor_col])
                group_value = _di_vm_text(raw.iat[row_index, group_col]) if group_col is not None else ""
                if group_value and not _di_vm_norm(group_value).startswith("total"):
                    group_ffill = group_value
                if descriptor_value and not _di_vm_norm(descriptor_value).startswith("total"):
                    descriptor_ffill = descriptor_value

                period_value = raw.iat[row_index, period_col]
                period = _di_vm_period(period_value)
                if pd.isna(period):
                    # Evita leer subtítulos y totales como obligaciones.
                    continue

                numeric = {
                    kind: _di_vm_number(raw.iat[row_index, col])
                    for kind, col in kinds.items()
                }
                if not any(abs(value) > 0.0001 for value in numeric.values()):
                    continue

                organism = group_ffill.strip() or descriptor_ffill.strip() or sheet_name
                tax = descriptor_value.strip() or descriptor_ffill.strip()
                if (
                    not tax
                    or not group_ffill
                    or _di_vm_norm(tax) == _di_vm_norm(organism)
                ):
                    tax = _di_vm_inferred_tax(kinds)

                explicit_total = numeric.get("deuda_total", 0.0)
                calculated_total = sum(
                    numeric.get(key, 0.0)
                    for key in [
                        "capital", "intereses", "cuota_sindical",
                        "cuota_solidaridad", "contribucion_convencional",
                    ]
                )
                total = explicit_total if abs(explicit_total) > 0.0001 else calculated_total
                due = (
                    _di_vm_date(raw.iat[row_index, kinds["fecha_vencimiento"]])
                    if "fecha_vencimiento" in kinds else pd.NaT
                )

                record = {
                    "organismo": organism,
                    "impuesto": tax,
                    "periodo": period,
                    "fecha_vencimiento": due,
                    "capital": numeric.get("capital", 0.0),
                    "intereses": numeric.get("intereses", 0.0),
                    "cuota_sindical": numeric.get("cuota_sindical", 0.0),
                    "cuota_solidaridad": numeric.get("cuota_solidaridad", 0.0),
                    "contribucion_convencional": numeric.get("contribucion_convencional", 0.0),
                    "deuda_total": total,
                    "importe_pagado": 0.0,
                    "saldo": total,
                    "estado": "Pendiente",
                    "fecha_pago": pd.NaT,
                    "plan_pago": "No",
                    "cuota_plan": "",
                    "observaciones": "",
                    "hoja_origen": sheet_name,
                    "fila_origen": row_index + 1,
                    "archivo_origen": filename,
                }
                records.append(record)

    if not records:
        return pd.DataFrame(columns=_DI_VM_COLUMNS)
    return _di_vm_prepare(pd.DataFrame(records), source=sheet_name)


def _di_vm_from_sheet(df: pd.DataFrame, table: str) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=_DI_VM_COLUMNS)

    column_norms = {_di_vm_norm(column) for column in df.columns}
    normalized_markers = {
        "id registro", "hoja origen", "fila origen", "archivo origen",
        "importe pagado", "fecha pago", "plan pago", "created at", "updated at",
    }
    # Una base ya importada por el módulo se lee directamente. En cambio, una
    # hoja histórica con varios cuadros debe recorrerse como matriz completa,
    # aunque la primera tabla tenga encabezados tabulares válidos.
    if column_norms.intersection(normalized_markers):
        return _di_vm_prepare(df, source=table)

    header = pd.DataFrame([list(df.columns)])
    body = df.copy().reset_index(drop=True)
    body.columns = range(len(body.columns))
    header.columns = body.columns
    raw = pd.concat([header, body], ignore_index=True)
    parsed_matrix = _di_vm_parse_matrix(
        raw,
        sheet_name=table,
        filename="Google Sheet",
    )
    if not parsed_matrix.empty:
        return parsed_matrix

    # Último recurso para hojas simples que ya tienen columnas como Impuesto,
    # Período, Capital e Intereses, pero no incluyen metadatos del módulo.
    return _di_vm_prepare(df, source=table)


def _di_vm_for_sheet(df: pd.DataFrame) -> pd.DataFrame:
    result = _di_vm_prepare(df).copy()
    for column in ["periodo", "fecha_vencimiento", "fecha_pago"]:
        parsed = pd.to_datetime(result[column], errors="coerce")
        if column == "periodo":
            result[column] = parsed.dt.strftime("%Y-%m").fillna("")
        else:
            result[column] = parsed.dt.strftime("%Y-%m-%d").fillna("")
    for column in [
        "capital", "intereses", "cuota_sindical", "cuota_solidaridad",
        "contribucion_convencional", "deuda_total", "importe_pagado", "saldo",
    ]:
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0.0).round(2)
    return result[_DI_VM_COLUMNS]


def _di_vm_merge(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    old = _di_vm_prepare(existing)
    new = _di_vm_prepare(incoming)
    if old.empty:
        return new
    if new.empty:
        return old

    old_by_id = {str(row["id_registro"]): row.copy() for _, row in old.iterrows()}
    order = list(old_by_id.keys())
    for _, incoming_row in new.iterrows():
        record_id = str(incoming_row["id_registro"])
        if record_id in old_by_id:
            previous = old_by_id[record_id]
            merged = incoming_row.copy()
            for column in _DI_VM_MANUAL_COLUMNS:
                previous_value = previous.get(column, "")
                if column == "importe_pagado":
                    if abs(_di_vm_number(previous_value)) > 0.0001:
                        merged[column] = previous_value
                elif _di_vm_text(previous_value):
                    merged[column] = previous_value
            merged["created_at"] = previous.get("created_at", merged.get("created_at", ""))
            old_by_id[record_id] = merged
        else:
            old_by_id[record_id] = incoming_row.copy()
            order.append(record_id)
    return _di_vm_refresh_status(pd.DataFrame([old_by_id[key] for key in order])[_DI_VM_COLUMNS])


def _di_vm_money(value: Any) -> str:
    number = _di_vm_number(value)
    text = f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"$ {text}"


def _di_vm_period_label(value: Any) -> str:
    period = _di_vm_period(value)
    if pd.isna(period):
        return "Sin período"
    months = [
        "Ene", "Feb", "Mar", "Abr", "May", "Jun",
        "Jul", "Ago", "Sep", "Oct", "Nov", "Dic",
    ]
    return f"{months[period.month - 1]} {period.year}"


def _di_vm_filtered(data: pd.DataFrame, key_prefix: str) -> pd.DataFrame:
    if data.empty:
        return data
    enriched = _di_vm_refresh_status(data).copy()
    enriched["_periodo"] = pd.to_datetime(enriched["periodo"], errors="coerce")
    enriched["_vencimiento"] = pd.to_datetime(enriched["fecha_vencimiento"], errors="coerce")

    periods = enriched["_periodo"].dropna()
    min_year = int(periods.dt.year.min()) if not periods.empty else pd.Timestamp.today().year
    max_year = int(periods.dt.year.max()) if not periods.empty else pd.Timestamp.today().year
    year_options = list(range(min_year, max_year + 1))

    row1 = st.columns([1.1, 1.4, 1.4, 1.1])
    with row1[0]:
        years = st.multiselect(
            "Años",
            year_options,
            default=year_options,
            key=f"{key_prefix}_years",
        )
    with row1[1]:
        organisms = sorted(value for value in enriched["organismo"].dropna().astype(str).unique() if value)
        selected_organisms = st.multiselect(
            "Organismos / sindicatos",
            organisms,
            default=organisms,
            key=f"{key_prefix}_orgs",
        )
    with row1[2]:
        states = sorted(value for value in enriched["estado"].dropna().astype(str).unique() if value)
        selected_states = st.multiselect(
            "Estados",
            states,
            default=states,
            key=f"{key_prefix}_states",
        )
    with row1[3]:
        sheets = sorted(value for value in enriched["hoja_origen"].dropna().astype(str).unique() if value)
        selected_sheets = st.multiselect(
            "Hojas de origen",
            sheets,
            default=sheets,
            key=f"{key_prefix}_sheets",
        )

    search = st.text_input(
        "Buscar impuesto, organismo, período u observación",
        placeholder="Ej.: F931, IVA, TISSH, FATSA, 202606…",
        key=f"{key_prefix}_search",
    )

    mask = pd.Series(True, index=enriched.index)
    if years:
        mask &= enriched["_periodo"].dt.year.isin(years)
    if selected_organisms:
        mask &= enriched["organismo"].isin(selected_organisms)
    if selected_states:
        mask &= enriched["estado"].isin(selected_states)
    if selected_sheets:
        mask &= enriched["hoja_origen"].isin(selected_sheets)
    if search.strip():
        needle = _di_vm_norm(search)
        haystack = (
            enriched["organismo"].fillna("").astype(str) + " "
            + enriched["impuesto"].fillna("").astype(str) + " "
            + enriched["periodo"].astype(str) + " "
            + enriched["observaciones"].fillna("").astype(str)
        ).apply(_di_vm_norm)
        mask &= haystack.str.contains(re.escape(needle), regex=True, na=False)
    return enriched[mask].copy()


def _di_vm_render_dashboard(data: pd.DataFrame, table: str) -> None:
    if data.empty:
        st.info("La hoja todavía no tiene obligaciones reconocidas. Importá el Excel o cargá el primer registro.")
        return

    filtered = _di_vm_filtered(data, f"di_vm_filter_{table}")
    if filtered.empty:
        st.warning("Los filtros actuales no devuelven registros.")
        return

    today = pd.Timestamp.today().normalize()
    due = pd.to_datetime(filtered["fecha_vencimiento"], errors="coerce")
    balance = pd.to_numeric(filtered["saldo"], errors="coerce").fillna(0.0)
    total_balance = balance.sum()
    overdue = balance[(due.notna()) & (due.dt.normalize() < today) & (balance > 0)].sum()
    next_30 = balance[
        due.notna()
        & due.dt.normalize().between(today, today + pd.Timedelta(days=30), inclusive="both")
        & (balance > 0)
    ].sum()
    total_interest = pd.to_numeric(filtered["intereses"], errors="coerce").fillna(0.0).sum()
    paid = pd.to_numeric(filtered["importe_pagado"], errors="coerce").fillna(0.0).sum()

    metric_cols = st.columns(5)
    metric_cols[0].metric("Deuda pendiente", _di_vm_money(total_balance))
    metric_cols[1].metric("Vencida", _di_vm_money(overdue))
    metric_cols[2].metric("Próximos 30 días", _di_vm_money(next_30))
    metric_cols[3].metric("Intereses", _di_vm_money(total_interest))
    metric_cols[4].metric("Pagado registrado", _di_vm_money(paid))

    overdue_ratio = (overdue / total_balance * 100) if total_balance > 0 else 0.0
    interest_ratio = (
        total_interest / pd.to_numeric(filtered["deuda_total"], errors="coerce").fillna(0.0).sum() * 100
        if pd.to_numeric(filtered["deuda_total"], errors="coerce").fillna(0.0).sum() > 0 else 0.0
    )
    if overdue > 0:
        st.error(
            f"Atención ejecutiva: {_di_vm_money(overdue)} están vencidos "
            f"({overdue_ratio:.1f}% del saldo filtrado)."
        )
    elif next_30 > 0:
        st.warning(f"Próximos vencimientos por {_di_vm_money(next_30)} dentro de 30 días.")
    else:
        st.success("No hay deuda vencida ni vencimientos dentro de los próximos 30 días en el filtro actual.")

    chart_left, chart_right = st.columns(2)
    by_organism = (
        filtered.groupby("organismo", dropna=False, as_index=False)["saldo"]
        .sum()
        .sort_values("saldo", ascending=True)
    )
    with chart_left:
        st.markdown("#### Saldo por organismo")
        fig = px.bar(
            by_organism,
            x="saldo",
            y="organismo",
            orientation="h",
            text_auto=".3s",
            labels={"saldo": "Saldo pendiente", "organismo": "Organismo"},
        )
        fig.update_layout(height=max(330, len(by_organism) * 42), margin=dict(l=10, r=10, t=20, b=10))
        fig.update_xaxes(tickprefix="$ ", separatethousands=True)
        st.plotly_chart(fig, use_container_width=True, key=f"di_vm_org_chart_{table}")

    monthly = filtered.dropna(subset=["_periodo"]).copy()
    monthly["Período"] = monthly["_periodo"].dt.to_period("M").dt.to_timestamp()
    monthly = monthly.groupby("Período", as_index=False).agg(
        Deuda=("deuda_total", "sum"),
        Saldo=("saldo", "sum"),
    ).sort_values("Período")
    with chart_right:
        st.markdown("#### Evolución mensual")
        if monthly.empty:
            st.info("No hay períodos válidos para graficar.")
        else:
            long_monthly = monthly.melt(
                id_vars="Período",
                value_vars=["Deuda", "Saldo"],
                var_name="Serie",
                value_name="Importe",
            )
            fig = px.line(
                long_monthly,
                x="Período",
                y="Importe",
                color="Serie",
                markers=True,
                labels={"Importe": "Importe ARS"},
            )
            fig.update_layout(height=330, margin=dict(l=10, r=10, t=20, b=10), legend_title_text="")
            fig.update_yaxes(tickprefix="$ ", separatethousands=True)
            st.plotly_chart(fig, use_container_width=True, key=f"di_vm_month_chart_{table}")

    state_left, state_right = st.columns([1, 1.35])
    with state_left:
        st.markdown("#### Distribución del saldo")
        by_state = filtered.groupby("estado", as_index=False)["saldo"].sum()
        by_state = by_state[by_state["saldo"].gt(0.0001)]
        if by_state.empty:
            st.info("No hay saldo para distribuir.")
        else:
            fig = px.pie(by_state, names="estado", values="saldo", hole=0.58)
            fig.update_layout(height=350, margin=dict(l=10, r=10, t=15, b=10), legend_title_text="")
            st.plotly_chart(fig, use_container_width=True, key=f"di_vm_state_chart_{table}")
        st.caption(f"Incidencia de intereses sobre la deuda filtrada: {interest_ratio:.1f}%")

    with state_right:
        st.markdown("#### Agenda de vencimientos")
        agenda = filtered[filtered["saldo"].gt(0)].copy()
        agenda["Días"] = (
            pd.to_datetime(agenda["fecha_vencimiento"], errors="coerce").dt.normalize() - today
        ).dt.days
        agenda = agenda.sort_values(["fecha_vencimiento", "saldo"], ascending=[True, False]).head(15)
        st.dataframe(
            agenda[["organismo", "impuesto", "periodo", "fecha_vencimiento", "saldo", "estado", "Días"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "organismo": st.column_config.TextColumn("Organismo"),
                "impuesto": st.column_config.TextColumn("Impuesto / concepto", width="large"),
                "periodo": st.column_config.DateColumn("Período", format="MMM YYYY"),
                "fecha_vencimiento": st.column_config.DateColumn("Vencimiento", format="DD/MM/YYYY"),
                "saldo": st.column_config.NumberColumn("Saldo", format="$ %.2f"),
                "Días": st.column_config.NumberColumn("Días", format="%d"),
            },
        )

    st.markdown("#### Detalle filtrado")
    detail = filtered.sort_values(
        ["fecha_vencimiento", "periodo", "organismo"],
        ascending=[True, False, True],
        na_position="last",
    )
    st.dataframe(
        detail[[
            "organismo", "impuesto", "periodo", "fecha_vencimiento",
            "capital", "intereses", "cuota_sindical", "cuota_solidaridad",
            "contribucion_convencional", "deuda_total", "importe_pagado",
            "saldo", "estado", "hoja_origen",
        ]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "organismo": st.column_config.TextColumn("Organismo"),
            "impuesto": st.column_config.TextColumn("Impuesto / concepto", width="large"),
            "periodo": st.column_config.DateColumn("Período", format="MMM YYYY"),
            "fecha_vencimiento": st.column_config.DateColumn("Vencimiento", format="DD/MM/YYYY"),
            "capital": st.column_config.NumberColumn("Capital", format="$ %.2f"),
            "intereses": st.column_config.NumberColumn("Intereses", format="$ %.2f"),
            "cuota_sindical": st.column_config.NumberColumn("Cuota sindical", format="$ %.2f"),
            "cuota_solidaridad": st.column_config.NumberColumn("Cuota solidaria", format="$ %.2f"),
            "contribucion_convencional": st.column_config.NumberColumn("Contribución", format="$ %.2f"),
            "deuda_total": st.column_config.NumberColumn("Deuda total", format="$ %.2f"),
            "importe_pagado": st.column_config.NumberColumn("Pagado", format="$ %.2f"),
            "saldo": st.column_config.NumberColumn("Saldo", format="$ %.2f"),
            "estado": st.column_config.TextColumn("Estado"),
            "hoja_origen": st.column_config.TextColumn("Hoja origen"),
        },
    )


@st.cache_data(show_spinner=False)
def _di_vm_read_uploaded_bytes(
    filename: str,
    content: bytes,
) -> tuple[dict[str, pd.DataFrame], str]:
    """Abre el archivo una sola vez y reutiliza la lectura en cada rerun de Streamlit."""
    from io import BytesIO, StringIO

    lower = filename.lower()
    if lower.endswith(".csv"):
        text = None
        for encoding in ["utf-8-sig", "utf-8", "latin-1"]:
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("No se pudo interpretar la codificación del CSV.")
        frame = pd.read_csv(
            StringIO(text),
            header=None,
            sep=None,
            engine="python",
            dtype=object,
        )
        return {"CSV": frame}, filename

    excel = pd.ExcelFile(BytesIO(content))
    matrices = {
        sheet_name: pd.read_excel(
            BytesIO(content),
            sheet_name=sheet_name,
            header=None,
            dtype=object,
        )
        for sheet_name in excel.sheet_names
    }
    return matrices, filename


def _di_vm_read_uploaded(uploaded_file: Any) -> tuple[dict[str, pd.DataFrame], str]:
    if uploaded_file is None:
        return {}, ""
    filename = _di_vm_text(getattr(uploaded_file, "name", "archivo")) or "archivo"
    return _di_vm_read_uploaded_bytes(filename, uploaded_file.getvalue())


def _di_vm_render_import(data: pd.DataFrame, table: str, module_name: str = "") -> None:
    entity_label = _di_vm_entity_label(table, module_name)
    st.markdown("### Importador inteligente de Excel")
    st.caption(
        "Selecciona una o varias hojas. El importador conserva organismo, impuesto, período, "
        "vencimiento, capital, intereses, cuotas, contribuciones, total y procedencia de cada fila."
    )
    uploaded = st.file_uploader(
        "Cargar planilla Excel o CSV",
        type=["xlsx", "xls", "xlsm", "csv"],
        key=f"di_vm_upload_{table}",
    )
    if uploaded is None:
        st.info("Cargá la planilla. Después vas a poder elegir exactamente qué hoja leer antes de guardar.")
        return

    try:
        matrices, filename = _di_vm_read_uploaded(uploaded)
    except Exception as error:
        st.error("No se pudo abrir el archivo. Si es .xls antiguo, guardalo como .xlsx y volvé a cargarlo.")
        st.exception(error)
        return

    sheet_names = list(matrices.keys())
    preferred = [
        name for name in sheet_names
        if any(token in _di_vm_norm(name) for token in ["deuda", "impuesto", "afip", "municip", "sindicato"])
    ]
    defaults = preferred[:1] or sheet_names[:1]
    selected = st.multiselect(
        "Hojas a procesar",
        sheet_names,
        default=defaults,
        key=f"di_vm_selected_sheets_{table}",
    )
    if not selected:
        st.warning("Seleccioná al menos una hoja.")
        return

    parsed_frames: list[pd.DataFrame] = []
    diagnostic_rows: list[dict[str, Any]] = []
    preview_tabs = st.tabs([f"📄 {name}" for name in selected])
    for tab, sheet_name in zip(preview_tabs, selected):
        raw = matrices[sheet_name]
        parsed = _di_vm_parse_matrix(raw, sheet_name=sheet_name, filename=filename)
        if parsed.empty:
            # También admite hojas que ya vienen normalizadas con encabezados en la primera fila.
            try:
                tabular = raw.copy()
                tabular.columns = [_di_vm_text(value) or f"col_{index}" for index, value in enumerate(raw.iloc[0])]
                tabular = tabular.iloc[1:].reset_index(drop=True)
                parsed = _di_vm_prepare(tabular, source=sheet_name)
                if not parsed.empty:
                    parsed["hoja_origen"] = sheet_name
                    parsed["archivo_origen"] = filename
            except Exception:
                parsed = pd.DataFrame(columns=_DI_VM_COLUMNS)
        if not parsed.empty:
            parsed_frames.append(parsed)
        diagnostic_rows.append({
            "Hoja": sheet_name,
            "Filas del cuadro": len(raw),
            "Obligaciones detectadas": len(parsed),
            "Total detectado": float(pd.to_numeric(parsed.get("deuda_total", 0), errors="coerce").fillna(0).sum()) if not parsed.empty else 0.0,
        })
        with tab:
            st.markdown("**Vista original de la hoja**")
            st.dataframe(raw.head(120), use_container_width=True, hide_index=True)
            st.markdown("**Lectura estructurada que se guardará**")
            if parsed.empty:
                st.warning("No se reconocieron filas. Revisá que la hoja elegida contenga PERIODO y alguna columna de importes.")
            else:
                st.dataframe(
                    parsed[[
                        "organismo", "impuesto", "periodo", "fecha_vencimiento",
                        "capital", "intereses", "cuota_sindical", "cuota_solidaridad",
                        "contribucion_convencional", "deuda_total", "hoja_origen", "fila_origen",
                    ]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "periodo": st.column_config.DateColumn("Período", format="MMM YYYY"),
                        "fecha_vencimiento": st.column_config.DateColumn("Vencimiento", format="DD/MM/YYYY"),
                        "capital": st.column_config.NumberColumn("Capital", format="$ %.2f"),
                        "intereses": st.column_config.NumberColumn("Intereses", format="$ %.2f"),
                        "cuota_sindical": st.column_config.NumberColumn("Cuota sindical", format="$ %.2f"),
                        "cuota_solidaridad": st.column_config.NumberColumn("Cuota solidaria", format="$ %.2f"),
                        "contribucion_convencional": st.column_config.NumberColumn("Contribución", format="$ %.2f"),
                        "deuda_total": st.column_config.NumberColumn("Deuda total", format="$ %.2f"),
                    },
                )

    diagnostics = pd.DataFrame(diagnostic_rows)
    st.markdown("#### Control de lectura")
    st.dataframe(
        diagnostics,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Total detectado": st.column_config.NumberColumn("Total detectado", format="$ %.2f"),
        },
    )

    if not parsed_frames:
        st.error("Ninguna de las hojas seleccionadas produjo obligaciones válidas. No se modificó Google Sheets.")
        return
    incoming = _di_vm_prepare(pd.concat(parsed_frames, ignore_index=True), source=filename)

    mode = st.radio(
        "Cómo guardar en Google Sheets",
        [
            "Agregar o actualizar sin duplicar",
            "Reemplazar solamente las hojas seleccionadas",
            f"Reemplazar toda la base de Deudas Impositivas {entity_label}",
        ],
        horizontal=True,
        key=f"di_vm_import_mode_{table}",
    )
    confirm = st.checkbox(
        f"Confirmo guardar {len(incoming)} obligaciones detectadas en la hoja {table}",
        key=f"di_vm_confirm_import_{table}",
    )
    if st.button(
        "Guardar planilla en Google Sheets",
        type="primary",
        use_container_width=True,
        disabled=not confirm,
        key=f"di_vm_save_import_{table}",
    ):
        try:
            if mode.startswith("Agregar"):
                destination = _di_vm_merge(data, incoming)
            elif mode.startswith("Reemplazar solamente"):
                selected_norm = {_di_vm_norm(name) for name in selected}
                keep = data[~data["hoja_origen"].apply(_di_vm_norm).isin(selected_norm)].copy()
                destination = _di_vm_merge(keep, incoming)
            else:
                destination = incoming
            sync_df_to_sheet(table, _di_vm_for_sheet(destination))
            st.cache_data.clear()
            st.success(
                f"Planilla guardada correctamente: {len(destination)} obligaciones disponibles "
                f"en Google Sheets, sin perder la hoja y fila de origen."
            )
            st.rerun()
        except Exception as error:
            st.error("No se pudo guardar en Google Sheets. La base anterior no fue reemplazada.")
            st.exception(error)


def _di_vm_render_editor(data: pd.DataFrame, table: str) -> None:
    st.markdown("### Base maestra en Google Sheets")
    st.caption(
        "Esta tabla es la fuente real del módulo. Podés corregir datos, agregar filas o borrar registros; "
        "los cambios se aplican únicamente al presionar Guardar cambios."
    )
    if data.empty:
        st.info("No hay filas para editar.")
        return

    editable_columns = [
        "organismo", "impuesto", "periodo", "fecha_vencimiento", "capital", "intereses",
        "cuota_sindical", "cuota_solidaridad", "contribucion_convencional", "deuda_total",
        "importe_pagado", "estado", "fecha_pago", "plan_pago", "cuota_plan", "observaciones",
        "hoja_origen", "fila_origen", "archivo_origen", "id_registro", "created_at", "updated_at",
    ]
    editor_data = data[editable_columns].copy()
    edited = st.data_editor(
        editor_data,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key=f"di_vm_editor_{table}",
        disabled=["id_registro", "created_at", "updated_at"],
        column_config={
            "organismo": st.column_config.TextColumn("Organismo", required=True),
            "impuesto": st.column_config.TextColumn("Impuesto / concepto", required=True, width="large"),
            "periodo": st.column_config.DateColumn("Período", format="MMM YYYY"),
            "fecha_vencimiento": st.column_config.DateColumn("Vencimiento", format="DD/MM/YYYY"),
            "capital": st.column_config.NumberColumn("Capital", min_value=0.0, format="$ %.2f"),
            "intereses": st.column_config.NumberColumn("Intereses", min_value=0.0, format="$ %.2f"),
            "cuota_sindical": st.column_config.NumberColumn("Cuota sindical", min_value=0.0, format="$ %.2f"),
            "cuota_solidaridad": st.column_config.NumberColumn("Cuota solidaria", min_value=0.0, format="$ %.2f"),
            "contribucion_convencional": st.column_config.NumberColumn("Contribución", min_value=0.0, format="$ %.2f"),
            "deuda_total": st.column_config.NumberColumn("Deuda total", min_value=0.0, format="$ %.2f"),
            "importe_pagado": st.column_config.NumberColumn("Pagado", min_value=0.0, format="$ %.2f"),
            "estado": st.column_config.SelectboxColumn(
                "Estado",
                options=[
                    "Pendiente", "Próxima a vencer", "Vencida", "Pagada",
                    "En plan de pagos", "Judicializada", "Discutida",
                    "Condonada", "Prescripta", "Sin vencimiento",
                ],
            ),
            "fecha_pago": st.column_config.DateColumn("Fecha de pago", format="DD/MM/YYYY"),
            "plan_pago": st.column_config.SelectboxColumn("Plan de pago", options=["No", "Sí"]),
            "cuota_plan": st.column_config.TextColumn("Cuota / plan"),
            "observaciones": st.column_config.TextColumn("Observaciones", width="large"),
            "fila_origen": st.column_config.NumberColumn("Fila origen", format="%d"),
        },
    )
    button_left, button_right = st.columns([1, 1])
    with button_left:
        save = st.button(
            "Guardar cambios en Google Sheets",
            type="primary",
            use_container_width=True,
            key=f"di_vm_save_editor_{table}",
        )
    with button_right:
        refresh = st.button(
            "Volver a leer Google Sheets",
            use_container_width=True,
            key=f"di_vm_refresh_{table}",
        )
    if refresh:
        st.cache_data.clear()
        st.rerun()
    if save:
        try:
            prepared = _di_vm_prepare(edited, source=table)
            sync_df_to_sheet(table, _di_vm_for_sheet(prepared))
            st.cache_data.clear()
            st.success(f"Cambios guardados. Registros procesados: {len(prepared)}")
            st.rerun()
        except Exception as error:
            st.error("No se pudieron guardar los cambios.")
            st.exception(error)


def _di_vm_render_actions(data: pd.DataFrame, table: str) -> None:
    add_tab, payment_tab = st.tabs(["➕ Nueva obligación", "💳 Registrar pago / plan"])
    with add_tab:
        with st.form(f"di_vm_add_form_{table}", clear_on_submit=True):
            row1 = st.columns(3)
            organismo = row1[0].text_input("Organismo / sindicato *")
            impuesto = row1[1].text_input("Impuesto / concepto *")
            periodo = row1[2].date_input("Período", value=pd.Timestamp.today().replace(day=1).date())
            row2 = st.columns(3)
            vencimiento = row2[0].date_input("Fecha de vencimiento", value=None)
            capital = row2[1].number_input("Capital", min_value=0.0, step=1000.0, format="%.2f")
            intereses = row2[2].number_input("Intereses", min_value=0.0, step=1000.0, format="%.2f")
            row3 = st.columns(3)
            cuota_sindical = row3[0].number_input("Cuota sindical", min_value=0.0, step=1000.0, format="%.2f")
            cuota_solidaria = row3[1].number_input("Cuota solidaridad / extraordinaria", min_value=0.0, step=1000.0, format="%.2f")
            contribucion = row3[2].number_input("Contribución convencional", min_value=0.0, step=1000.0, format="%.2f")
            deuda_total = st.number_input(
                "Deuda total (dejá 0 para calcularla automáticamente)",
                min_value=0.0,
                step=1000.0,
                format="%.2f",
            )
            observations = st.text_area("Observaciones")
            submitted = st.form_submit_button("Guardar obligación", type="primary", use_container_width=True)
        if submitted:
            if not organismo.strip() or not impuesto.strip():
                st.error("Completá organismo e impuesto / concepto.")
            else:
                record = pd.DataFrame([{
                    "organismo": organismo,
                    "impuesto": impuesto,
                    "periodo": periodo,
                    "fecha_vencimiento": vencimiento,
                    "capital": capital,
                    "intereses": intereses,
                    "cuota_sindical": cuota_sindical,
                    "cuota_solidaridad": cuota_solidaria,
                    "contribucion_convencional": contribucion,
                    "deuda_total": deuda_total,
                    "importe_pagado": 0.0,
                    "estado": "Pendiente",
                    "plan_pago": "No",
                    "observaciones": observations,
                    "hoja_origen": "Carga manual",
                    "fila_origen": 0,
                    "archivo_origen": "Sistema VITAE",
                }])
                try:
                    destination = _di_vm_merge(data, record)
                    sync_df_to_sheet(table, _di_vm_for_sheet(destination))
                    st.cache_data.clear()
                    st.success("Obligación guardada correctamente.")
                    st.rerun()
                except Exception as error:
                    st.error("No se pudo guardar la obligación.")
                    st.exception(error)

    with payment_tab:
        pending = data[pd.to_numeric(data.get("saldo", 0), errors="coerce").fillna(0).gt(0.005)].copy()
        if pending.empty:
            st.success("No hay obligaciones con saldo pendiente.")
            return
        pending = pending.sort_values(["fecha_vencimiento", "periodo"], na_position="last")
        labels = {
            f"{row['organismo']} · {row['impuesto']} · {_di_vm_period_label(row['periodo'])} · saldo {_di_vm_money(row['saldo'])}": row["id_registro"]
            for _, row in pending.iterrows()
        }
        with st.form(f"di_vm_payment_form_{table}"):
            selected_label = st.selectbox("Obligación", list(labels.keys()))
            selected_id = labels[selected_label]
            selected_row = pending[pending["id_registro"].eq(selected_id)].iloc[0]
            payment_cols = st.columns(3)
            amount = payment_cols[0].number_input(
                "Importe a registrar",
                min_value=0.0,
                max_value=float(max(selected_row["saldo"], 0.0)),
                value=float(max(selected_row["saldo"], 0.0)),
                step=1000.0,
                format="%.2f",
            )
            payment_date = payment_cols[1].date_input("Fecha del pago", value=date.today())
            in_plan = payment_cols[2].selectbox("Plan de pagos", ["No", "Sí"])
            plan_installment = st.text_input("Cuota / identificación del plan", disabled=in_plan == "No")
            observations = st.text_area("Observaciones del pago")
            paid_submit = st.form_submit_button("Registrar pago", type="primary", use_container_width=True)
        if paid_submit:
            try:
                updated = data.copy()
                mask = updated["id_registro"].eq(selected_id)
                updated.loc[mask, "importe_pagado"] = (
                    pd.to_numeric(updated.loc[mask, "importe_pagado"], errors="coerce").fillna(0.0)
                    + float(amount)
                )
                updated.loc[mask, "fecha_pago"] = pd.Timestamp(payment_date)
                updated.loc[mask, "plan_pago"] = in_plan
                if in_plan == "Sí":
                    updated.loc[mask, "estado"] = "En plan de pagos"
                    updated.loc[mask, "cuota_plan"] = plan_installment
                old_note = _di_vm_text(updated.loc[mask, "observaciones"].iloc[0])
                new_note = _di_vm_text(observations)
                if new_note:
                    updated.loc[mask, "observaciones"] = (old_note + " | " + new_note).strip(" |")
                updated = _di_vm_refresh_status(updated)
                sync_df_to_sheet(table, _di_vm_for_sheet(updated))
                st.cache_data.clear()
                st.success("Pago registrado y saldo actualizado.")
                st.rerun()
            except Exception as error:
                st.error("No se pudo registrar el pago.")
                st.exception(error)


def _di_vm_render_export(data: pd.DataFrame, table: str, module_name: str = "") -> None:
    from io import BytesIO

    export_slug = _di_vm_export_slug(table, module_name)
    if data.empty:
        st.info("No hay datos para exportar.")
        return
    filtered = _di_vm_filtered(data, f"di_vm_export_filter_{table}")
    export = _di_vm_for_sheet(filtered.drop(columns=["_periodo", "_vencimiento"], errors="ignore"))
    csv = export.to_csv(index=False).encode("utf-8-sig")
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "Descargar CSV filtrado",
            data=csv,
            file_name=f"{export_slug}_{date.today().isoformat()}.csv",
            mime="text/csv",
            use_container_width=True,
            key=f"di_vm_csv_{table}",
        )
    buffer = BytesIO()
    summary = export.groupby(["organismo", "estado"], as_index=False).agg(
        Obligaciones=("id_registro", "count"),
        Deuda_total=("deuda_total", "sum"),
        Pagado=("importe_pagado", "sum"),
        Saldo=("saldo", "sum"),
    )
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        export.to_excel(writer, sheet_name="Base completa", index=False)
        summary.to_excel(writer, sheet_name="Resumen ejecutivo", index=False)
        for sheet_name, subset in export.groupby("hoja_origen", dropna=False):
            safe_name = re.sub(r"[\\/*?:\[\]]", "_", _di_vm_text(sheet_name) or "Sin origen")[:31]
            if safe_name in {"Base completa", "Resumen ejecutivo"}:
                safe_name = (safe_name[:26] + " origen")[:31]
            subset.to_excel(writer, sheet_name=safe_name, index=False)
    with col2:
        st.download_button(
            "Descargar Excel ejecutivo",
            data=buffer.getvalue(),
            file_name=f"{export_slug}_{date.today().isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key=f"di_vm_xlsx_{table}",
        )
    st.dataframe(
        summary,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Deuda_total": st.column_config.NumberColumn("Deuda total", format="$ %.2f"),
            "Pagado": st.column_config.NumberColumn("Pagado", format="$ %.2f"),
            "Saldo": st.column_config.NumberColumn("Saldo", format="$ %.2f"),
        },
    )


def render_deudas_impositivas_pro(
    df_original: pd.DataFrame,
    table: str,
    module_name: str,
) -> None:
    """Centro integral de deuda tributaria y sindical para VM o VMR."""
    entity_label = _di_vm_entity_label(table, module_name)
    entity_name = _di_vm_entity_name(table, module_name)
    title = f"Centro Fiscal y Sindical · {entity_label}"

    st.markdown(
        f"""
        <style>
        .di-vm-hero {{
            padding: 1.30rem 1.40rem;
            border: 1px solid rgba(80, 115, 160, .22);
            border-radius: 20px;
            background:
                radial-gradient(circle at 92% 10%, rgba(112, 226, 204, .23), transparent 30%),
                linear-gradient(135deg, rgba(11, 39, 69, .99), rgba(12, 94, 111, .94));
            color: white;
            margin: .25rem 0 1rem 0;
            box-shadow: 0 16px 42px rgba(9, 30, 55, .18);
        }}
        .di-vm-hero h2 {{ margin: 0 0 .30rem 0; font-size: 1.48rem; letter-spacing: -.01em; }}
        .di-vm-hero p {{ margin: 0; opacity: .90; max-width: 920px; }}
        .di-vm-pill {{
            display: inline-block;
            padding: .27rem .68rem;
            margin-top: .75rem;
            border-radius: 999px;
            background: rgba(255,255,255,.13);
            border: 1px solid rgba(255,255,255,.20);
            font-size: .82rem;
        }}
        </style>
        <div class="di-vm-hero">
            <h2>{title}</h2>
            <p>Centro de control de {entity_name}: ARCA/AFIP, Rentas, IVA, F.931, anticipos, FATSA, ATSA y demás obligaciones tributarias y sindicales.</p>
            <span class="di-vm-pill">Google Sheets como fuente única · Excel por hoja · Lectura de cuadros combinados · Pagos y vencimientos</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    data = _di_vm_from_sheet(df_original, table)
    if not data.empty:
        data = _di_vm_refresh_status(data)

    tabs = st.tabs([
        "📊 Centro ejecutivo",
        "📋 Base en Sheets",
        "📥 Importar Excel",
        "➕ Cargar / pagar",
        "📤 Exportar",
    ])
    with tabs[0]:
        _di_vm_render_dashboard(data, table)
    with tabs[1]:
        _di_vm_render_editor(data, table)
    with tabs[2]:
        _di_vm_render_import(data, table, module_name)
    with tabs[3]:
        _di_vm_render_actions(data, table)
    with tabs[4]:
        _di_vm_render_export(data, table, module_name)


def render_deudas_impositivas_vm_pro(
    df_original: pd.DataFrame,
    table: str = "deudas_impositivas_vm",
    module_name: str = "Deudas Impositivas VM",
) -> None:
    render_deudas_impositivas_pro(df_original, table, module_name)


def render_deudas_impositivas_vmr_pro(
    df_original: pd.DataFrame,
    table: str = "deudas_impositivas_vmr",
    module_name: str = "Deudas Impositivas VMR",
) -> None:
    render_deudas_impositivas_pro(df_original, table, module_name)




# =========================================================
# PLANES DE PAGOS Y PRÉSTAMOS — CENTRO FINANCIERO PRO
# =========================================================
# Lee el cronograma normalizado desde Google Sheets y también interpreta
# planillas históricas horizontales como la utilizada por VITAE. Al importar
# un Excel conserva una copia matricial de cada hoja para poder visualizar el
# cuadro original desde la aplicación, pero guarda además una base normalizada
# que permite filtrar, proyectar, registrar pagos y analizar vencimientos.

_PLANES_COLUMNS = [
    "id_registro",
    "hoja_origen",
    "seccion",
    "registro_clase",
    "empresa",
    "unidad",
    "tipo_financiacion",
    "acreedor",
    "identificador",
    "periodo",
    "vencimiento",
    "cuota_numero",
    "cuotas_totales",
    "importe_cuota",
    "pagado",
    "saldo",
    "moneda",
    "tasa_mensual",
    "capital_original",
    "cuotas_adeudadas_monto",
    "monto_adelanto",
    "intereses_ahorrados",
    "total_plan_declarado",
    "estado",
    "observaciones",
    "fila_origen",
    "columna_origen",
    "fuente",
    "created_at",
    "updated_at",
]

_PLANES_REGISTRO_CLASES = [
    "Cuota mensual",
    "Resumen de planes",
    "Adelanto de cuotas",
    "Resumen de préstamos",
]

_PLANES_ESTADOS = [
    "Programada",
    "Pendiente",
    "Parcial",
    "Pagada",
    "Vencida",
    "A conciliar",
    "Cancelada",
]

_PLANES_TIPOS = [
    "Plan de pagos",
    "Préstamo bancario",
    "Crédito / tarjeta",
    "Adelanto de cuotas",
    "Financiación de proveedor",
    "Otro",
]

_PLANES_MONEDAS = ["ARS", "USD"]


def _pp_norm(value: Any) -> str:
    import unicodedata

    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text.lower()).strip()
    return " ".join(text.split())


def _pp_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null", "nat"}:
        return default
    return text


def _pp_number(value: Any) -> float:
    """Convierte importes argentinos, fórmulas cacheadas y celdas monetarias."""
    if value is None:
        return 0.0
    try:
        if pd.isna(value):
            return 0.0
    except Exception:
        pass
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "-", "s/d", "n/a"}:
        return 0.0

    negative = text.startswith("(") and text.endswith(")")
    text = (
        text.replace("AR$", "")
        .replace("US$", "")
        .replace("USD", "")
        .replace("$", "")
        .replace("%", "")
        .replace("\u00a0", "")
        .replace(" ", "")
    )
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        decimals = text.split(",")[-1]
        text = text.replace(".", "")
        text = text.replace(",", "." if len(decimals) <= 2 else "")
    elif text.count(".") > 1:
        parts = text.split(".")
        text = "".join(parts[:-1]) + "." + parts[-1]
    elif text.count(".") == 1:
        left, right = text.split(".", 1)
        if len(right) == 3 and left.replace("-", "").isdigit():
            text = left + right
    text = re.sub(r"[^0-9.\-]", "", text)
    try:
        number = float(text)
        return -number if negative else number
    except (TypeError, ValueError):
        return 0.0


def _pp_date(value: Any) -> pd.Timestamp:
    if value is None or _pp_text(value) == "":
        return pd.NaT
    try:
        parsed = pd.to_datetime(value, errors="coerce", dayfirst=True, format="mixed")
    except (TypeError, ValueError):
        parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if isinstance(parsed, pd.DatetimeIndex):
        return parsed[0] if len(parsed) else pd.NaT
    return parsed


def _pp_month(value: Any) -> pd.Timestamp:
    """Reconoce fechas, jun-26, junio 2026, 06/26 y 2026-06."""
    if value is None:
        return pd.NaT
    try:
        if pd.isna(value):
            return pd.NaT
    except Exception:
        pass
    if isinstance(value, (pd.Timestamp, date)):
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return pd.NaT
        return parsed.replace(day=1).normalize()
    if isinstance(value, float) and 20000 <= value <= 80000:
        # Fecha serial de Excel.
        try:
            parsed = pd.Timestamp("1899-12-30") + pd.to_timedelta(value, unit="D")
            return parsed.replace(day=1).normalize()
        except Exception:
            return pd.NaT

    text = _pp_text(value)
    if not text:
        return pd.NaT
    normalized = _pp_norm(text)
    month_map = {
        "ene": 1, "enero": 1,
        "feb": 2, "febrero": 2,
        "mar": 3, "marzo": 3,
        "abr": 4, "abril": 4,
        "may": 5, "mayo": 5,
        "jun": 6, "junio": 6,
        "jul": 7, "julio": 7,
        "ago": 8, "agosto": 8,
        "sep": 9, "sept": 9, "septiembre": 9, "set": 9, "setiembre": 9,
        "oct": 10, "octubre": 10,
        "nov": 11, "noviembre": 11,
        "dic": 12, "diciembre": 12,
    }
    match = re.fullmatch(
        r"(ene|enero|feb|febrero|mar|marzo|abr|abril|may|mayo|jun|junio|jul|julio|ago|agosto|sep|sept|septiembre|set|setiembre|oct|octubre|nov|noviembre|dic|diciembre)\s*(\d{2,4})",
        normalized,
    )
    if match:
        year = int(match.group(2))
        if year < 100:
            year += 2000
        if 2000 <= year <= 2100:
            return pd.Timestamp(year=year, month=month_map[match.group(1)], day=1)

    numeric = re.fullmatch(r"(\d{1,2})\s+(\d{2,4})", normalized)
    if numeric:
        month = int(numeric.group(1))
        year = int(numeric.group(2))
        if year < 100:
            year += 2000
        if 1 <= month <= 12 and 2000 <= year <= 2100:
            return pd.Timestamp(year=year, month=month, day=1)

    iso = re.fullmatch(r"(20\d{2})\s+(\d{1,2})", normalized)
    if iso:
        year, month = int(iso.group(1)), int(iso.group(2))
        if 1 <= month <= 12:
            return pd.Timestamp(year=year, month=month, day=1)

    parsed = _pp_date(text)
    if pd.notna(parsed) and 2000 <= parsed.year <= 2100:
        return pd.Timestamp(parsed).replace(day=1).normalize()
    return pd.NaT


def _pp_excel_column(number: int) -> str:
    result = ""
    current = int(number) + 1
    while current:
        current, remainder = divmod(current - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _pp_unique_columns(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    names: list[str] = []
    counts: dict[str, int] = {}
    for index, column in enumerate(result.columns):
        base = _pp_norm(column).replace(" ", "_") or f"columna_{index + 1}"
        counts[base] = counts.get(base, 0) + 1
        names.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    result.columns = names
    return result


def _pp_pick_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    columns = {_pp_norm(column).replace(" ", "_"): column for column in df.columns}
    for candidate in candidates:
        match = columns.get(_pp_norm(candidate).replace(" ", "_"))
        if match is not None:
            return match
    return None


def _pp_series(df: pd.DataFrame, candidates: list[str], default: Any = "") -> pd.Series:
    column = _pp_pick_column(df, candidates)
    if column is None:
        return pd.Series([default] * len(df), index=df.index)
    series = df.loc[:, column]
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    return series


def _pp_company(value: Any, unit: Any = "", sheet: Any = "") -> str:
    # La unidad tiene prioridad sobre el nombre de la hoja porque una misma
    # planilla puede contener simultáneamente bloques de VM y VMR.
    unit_text = _pp_norm(unit)
    value_text = _pp_norm(value)
    sheet_text = _pp_norm(sheet)
    if "reproductiva" in unit_text or "vmr" in unit_text:
        return "VMR"
    if "medical" in unit_text or re.search(r"\bvm\b", unit_text):
        return "VM"
    if "vmr" in value_text or "reproductiva" in value_text:
        return "VMR"
    if re.search(r"\bvm\b", value_text) or "medical" in value_text:
        return "VM"
    if "vmr" in sheet_text or "reproductiva" in sheet_text:
        return "VMR"
    if re.search(r"\bvm\b", sheet_text) or "medical" in sheet_text:
        return "VM"
    return "VITAE"


def _pp_type(value: Any, creditor: Any = "", section: Any = "") -> str:
    text = _pp_norm(f"{value} {creditor} {section}")
    if "adelanto" in text and "cuota" in text:
        return "Adelanto de cuotas"
    if any(token in text for token in ["credito", "tarjeta", "visa", "master", "amex"]):
        return "Crédito / tarjeta"
    if any(token in text for token in ["prestamo", "banco", "macro", "galicia"]):
        return "Préstamo bancario"
    if any(token in text for token in ["proveedor", "financiacion"]):
        return "Financiación de proveedor"
    if "plan" in text or "pp arca" in text or "arca" in text:
        return "Plan de pagos"
    return "Otro"


def _pp_make_id(row: pd.Series, index: int = 0) -> str:
    import hashlib

    seed = "|".join(
        [
            _pp_text(row.get("hoja_origen")),
            _pp_text(row.get("seccion")),
            _pp_text(row.get("registro_clase")),
            _pp_text(row.get("empresa")),
            _pp_text(row.get("acreedor")),
            _pp_text(row.get("identificador")),
            _pp_text(row.get("periodo")),
            _pp_text(row.get("fila_origen")),
            _pp_text(row.get("columna_origen")),
            str(index),
        ]
    )
    return "PP-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:14].upper()


def _pp_is_canonical(df: pd.DataFrame | None) -> bool:
    if df is None or df.empty:
        return False
    columns = {_pp_norm(column).replace(" ", "_") for column in df.columns}
    required_signals = {
        "identificador",
        "periodo",
        "importe_cuota",
        "acreedor",
        "tipo_financiacion",
    }
    return len(columns.intersection(required_signals)) >= 3


def _pp_prepare(df: pd.DataFrame | None, source: str = "Google Sheets") -> pd.DataFrame:
    """Normaliza una base tabular sin mezclar cuotas con resúmenes del cuadro."""
    if df is None or df.empty:
        return pd.DataFrame(columns=_PLANES_COLUMNS)

    raw = _pp_unique_columns(df)
    result = pd.DataFrame(index=raw.index)
    result["id_registro"] = _pp_series(raw, ["id_registro", "id", "registro_id"], "").apply(_pp_text)
    result["hoja_origen"] = _pp_series(raw, ["hoja_origen", "hoja", "sheet", "pestana"], "Principal").apply(_pp_text)
    result["seccion"] = _pp_series(raw, ["seccion", "grupo", "bloque"], "Deudas asumidas mes a mes").apply(_pp_text)
    result["registro_clase"] = _pp_series(
        raw,
        ["registro_clase", "clase_registro", "clase", "naturaleza_registro"],
        "",
    ).apply(_pp_text)

    unit = _pp_series(raw, ["unidad", "area", "sector", "empresa_detalle"], "").apply(_pp_text)
    company_raw = _pp_series(raw, ["empresa", "sociedad", "compania"], "")
    result["empresa"] = [
        _pp_company(company_raw.loc[index], unit.loc[index], result.at[index, "hoja_origen"])
        for index in raw.index
    ]
    result["unidad"] = unit
    creditor = _pp_series(raw, ["acreedor", "entidad", "organismo", "banco", "proveedor"], "").apply(_pp_text)
    result["acreedor"] = creditor
    result["tipo_financiacion"] = [
        _pp_type(
            _pp_series(raw, ["tipo_financiacion", "tipo", "categoria"], "").loc[index],
            creditor.loc[index],
            result.at[index, "seccion"],
        )
        for index in raw.index
    ]
    result["identificador"] = _pp_series(
        raw,
        ["identificador", "numero_plan", "nro_plan", "plan", "codigo", "referencia", "prestamo"],
        "",
    ).apply(_pp_text)
    result["periodo"] = _pp_series(raw, ["periodo", "mes", "fecha_cuota", "fecha"], pd.NaT).apply(_pp_month)
    result["vencimiento"] = _pp_series(
        raw, ["vencimiento", "fecha_vencimiento", "vence"], pd.NaT
    ).apply(_pp_date)
    result["vencimiento"] = result["vencimiento"].fillna(
        result["periodo"].apply(lambda value: value + pd.offsets.MonthEnd(0) if pd.notna(value) else pd.NaT)
    )
    result["cuota_numero"] = _pp_series(raw, ["cuota_numero", "nro_cuota", "cuota_actual", "cuota"], 0).apply(_pp_number).round().astype(int)
    result["cuotas_totales"] = _pp_series(raw, ["cuotas_totales", "total_cuotas", "cantidad_cuotas"], 0).apply(_pp_number).round().astype(int)
    result["importe_cuota"] = _pp_series(
        raw,
        ["importe_cuota", "importe", "monto", "valor", "cuota_importe", "valor_pesos"],
        0,
    ).apply(_pp_number)
    result["pagado"] = _pp_series(raw, ["pagado", "importe_pagado", "abonado"], 0).apply(_pp_number)
    supplied_balance = _pp_series(raw, ["saldo", "pendiente", "saldo_pendiente"], 0).apply(_pp_number)
    calculated_balance = (result["importe_cuota"] - result["pagado"]).clip(lower=0)
    result["saldo"] = supplied_balance.where(supplied_balance.abs().gt(0.0001), calculated_balance).clip(lower=0)
    result["moneda"] = _pp_series(raw, ["moneda", "currency"], "ARS").apply(
        lambda value: "USD" if "usd" in _pp_norm(value) or "dolar" in _pp_norm(value) else "ARS"
    )
    result["tasa_mensual"] = _pp_series(raw, ["tasa_mensual", "tasa", "interes"], 0).apply(_pp_number)
    result["capital_original"] = _pp_series(raw, ["capital_original", "capital", "monto_original"], 0).apply(_pp_number)
    result["cuotas_adeudadas_monto"] = _pp_series(
        raw, ["cuotas_adeudadas_monto", "cuotas_adeudadas", "deuda_cancelacion"], 0
    ).apply(_pp_number)
    result["monto_adelanto"] = _pp_series(
        raw, ["monto_adelanto", "adelanto", "cancelacion_anticipada"], 0
    ).apply(_pp_number)
    result["intereses_ahorrados"] = _pp_series(
        raw, ["intereses_ahorrados", "ahorro_intereses", "interes_ahorrado"], 0
    ).apply(_pp_number)
    result["total_plan_declarado"] = _pp_series(
        raw, ["total_plan_declarado", "total_plan", "total", "monto_total"], 0
    ).apply(_pp_number)
    result["estado"] = _pp_series(raw, ["estado", "situacion", "status"], "Programada").apply(_pp_text)
    result["observaciones"] = _pp_series(raw, ["observaciones", "notas", "comentarios"], "").apply(_pp_text)
    result["fila_origen"] = _pp_series(raw, ["fila_origen", "fila"], 0).apply(_pp_number).round().astype(int)
    result["columna_origen"] = _pp_series(raw, ["columna_origen", "columna"], "").apply(_pp_text)
    result["fuente"] = _pp_series(raw, ["fuente", "origen"], source).apply(_pp_text)
    result["created_at"] = _pp_series(raw, ["created_at", "creado_en"], "").apply(_pp_text)
    result["updated_at"] = _pp_series(raw, ["updated_at", "actualizado_en"], "").apply(_pp_text)

    current_month = pd.Timestamp.today().replace(day=1).normalize()
    allowed_states = {_pp_norm(state): state for state in _PLANES_ESTADOS}
    allowed_classes = {_pp_norm(value): value for value in _PLANES_REGISTRO_CLASES}

    for position, index in enumerate(result.index):
        section_norm = _pp_norm(result.at[index, "seccion"])
        class_norm = _pp_norm(result.at[index, "registro_clase"])
        if class_norm in allowed_classes:
            result.at[index, "registro_clase"] = allowed_classes[class_norm]
        elif "adelanto" in section_norm:
            result.at[index, "registro_clase"] = "Adelanto de cuotas"
        elif "prestamo" in section_norm and pd.isna(result.at[index, "periodo"]):
            result.at[index, "registro_clase"] = "Resumen de préstamos"
        elif "resumen" in section_norm and "plan" in section_norm:
            result.at[index, "registro_clase"] = "Resumen de planes"
        else:
            result.at[index, "registro_clase"] = "Cuota mensual"

        if not result.at[index, "acreedor"]:
            result.at[index, "acreedor"] = "Sin acreedor"
        if not result.at[index, "identificador"]:
            result.at[index, "identificador"] = f"SIN-ID-{int(result.at[index, 'fila_origen'] or position + 1)}"
        if not result.at[index, "unidad"]:
            result.at[index, "unidad"] = "Reproductiva" if result.at[index, "empresa"] == "VMR" else "Medical" if result.at[index, "empresa"] == "VM" else "Corporativo"
        if not result.at[index, "hoja_origen"]:
            result.at[index, "hoja_origen"] = "Principal"
        if not result.at[index, "seccion"]:
            result.at[index, "seccion"] = "Deudas asumidas mes a mes"
        if not result.at[index, "tipo_financiacion"] or result.at[index, "tipo_financiacion"] == "Otro":
            result.at[index, "tipo_financiacion"] = _pp_type("", result.at[index, "acreedor"], result.at[index, "seccion"])

        state_norm = _pp_norm(result.at[index, "estado"])
        amount = float(result.at[index, "importe_cuota"])
        paid = max(0.0, float(result.at[index, "pagado"]))
        balance = max(0.0, amount - paid)
        if supplied_balance.loc[index] > 0:
            balance = max(0.0, float(supplied_balance.loc[index]))
        result.at[index, "pagado"] = min(max(paid, 0.0), max(amount, paid))
        result.at[index, "saldo"] = balance

        if state_norm in {"pagada", "pagado", "cancelada", "cancelado", "saldada", "saldado"} or (amount > 0 and balance <= 0.01):
            result.at[index, "estado"] = "Pagada" if "cancel" not in state_norm else "Cancelada"
            result.at[index, "pagado"] = max(amount, paid)
            result.at[index, "saldo"] = 0.0
        elif paid > 0 and balance > 0:
            result.at[index, "estado"] = "Parcial"
        elif state_norm in allowed_states:
            result.at[index, "estado"] = allowed_states[state_norm]
        elif "venc" in state_norm:
            result.at[index, "estado"] = "Vencida"
        elif "concili" in state_norm:
            result.at[index, "estado"] = "A conciliar"
        elif result.at[index, "registro_clase"] != "Cuota mensual":
            result.at[index, "estado"] = "Pendiente" if balance > 0 else "A conciliar"
        elif pd.notna(result.at[index, "periodo"]) and result.at[index, "periodo"] < current_month:
            result.at[index, "estado"] = "A conciliar"
        else:
            result.at[index, "estado"] = "Programada"

        if not result.at[index, "id_registro"]:
            result.at[index, "id_registro"] = _pp_make_id(result.loc[index], position)

    has_numeric_content = (
        result["importe_cuota"].abs().gt(0.0001)
        | result["cuotas_adeudadas_monto"].abs().gt(0.0001)
        | result["monto_adelanto"].abs().gt(0.0001)
        | result["intereses_ahorrados"].abs().gt(0.0001)
        | result["total_plan_declarado"].abs().gt(0.0001)
    )
    is_named_summary = (
        result["registro_clase"].ne("Cuota mensual")
        & result["identificador"].astype(str).str.strip().ne("")
    )
    result = result[has_numeric_content | is_named_summary].copy()
    result["periodo"] = pd.to_datetime(result["periodo"], errors="coerce").dt.to_period("M").dt.to_timestamp()
    result["vencimiento"] = pd.to_datetime(result["vencimiento"], errors="coerce")

    if not result.empty:
        monthly_mask = result["registro_clase"].eq("Cuota mensual")
        monthly = result.loc[monthly_mask].copy()
        if not monthly.empty:
            grouping = ["hoja_origen", "empresa", "acreedor", "identificador"]
            monthly = monthly.sort_values(grouping + ["periodo", "fila_origen", "columna_origen"], na_position="last")
            calculated_number = monthly.groupby(grouping, dropna=False).cumcount() + 1
            calculated_total = monthly.groupby(grouping, dropna=False)["id_registro"].transform("size")
            monthly["cuota_numero"] = monthly["cuota_numero"].where(monthly["cuota_numero"] > 0, calculated_number)
            monthly["cuotas_totales"] = monthly["cuotas_totales"].where(monthly["cuotas_totales"] > 0, calculated_total)
            result.loc[monthly.index, "cuota_numero"] = monthly["cuota_numero"]
            result.loc[monthly.index, "cuotas_totales"] = monthly["cuotas_totales"]
        result.loc[~monthly_mask, ["cuota_numero", "cuotas_totales"]] = 0

    return result[_PLANES_COLUMNS].reset_index(drop=True)


def _pp_matrix_from_dataframe(df: pd.DataFrame | None) -> pd.DataFrame:
    """Recupera la primera fila usada como encabezado por get_df()."""
    if df is None or (df.empty and len(df.columns) == 0):
        return pd.DataFrame()
    header = pd.DataFrame([list(df.columns)])
    body = df.copy()
    body.columns = range(len(body.columns))
    header.columns = range(len(header.columns))
    return pd.concat([header, body], ignore_index=True).fillna("")


def _pp_detect_identifier(values: list[str]) -> tuple[str, str, str]:
    creditor = ""
    identifier = ""
    unit = ""
    for raw_text in values:
        text = _pp_text(raw_text)
        normalized = _pp_norm(text)
        if not normalized:
            continue

        if any(token in normalized for token in ["medical", "reproductiva", "adrian", "corporativo", "vitae"]):
            unit = unit or text
            continue

        is_creditor = (
            normalized.startswith("pp ")
            or normalized.startswith("credito ")
            or normalized.startswith("prestamo ")
            or normalized.startswith("banco ")
            or normalized in {"arca", "dgr", "credito macro", "credito galicia"}
        )
        if is_creditor:
            creditor = creditor or text
            continue

        compact = re.sub(r"\s+", "", text).strip()
        if re.fullmatch(r"\d+\.0", compact):
            compact = compact[:-2]
        identifier_like = bool(re.fullmatch(r"[A-Za-z]{0,4}\d{5,15}", compact))
        if identifier_like:
            identifier = identifier or compact

    return creditor, identifier, unit


def _pp_legacy_header_month(value: Any) -> pd.Timestamp:
    """
    Interpreta los encabezados especiales del archivo original.

    La planilla usa fechas como 25/01/2025 con formato visual ``ene-26``:
    el mes real es enero y el día 26 representa el año 2026. Esta función
    reconoce esa convención sin confundir importes monetarios con fechas.
    """
    if value is None:
        return pd.NaT
    try:
        if pd.isna(value):
            return pd.NaT
    except Exception:
        pass

    def normalize_datetime(parsed: pd.Timestamp) -> pd.Timestamp:
        if pd.isna(parsed):
            return pd.NaT
        parsed = pd.Timestamp(parsed)
        if 2020 <= parsed.year <= 2035 and 20 <= parsed.day <= 50:
            intended_year = 2000 + int(parsed.day)
            if 2020 <= intended_year <= 2100:
                return pd.Timestamp(year=intended_year, month=int(parsed.month), day=1)
        if 2000 <= parsed.year <= 2100:
            return parsed.replace(day=1).normalize()
        return pd.NaT

    if isinstance(value, (pd.Timestamp, date)):
        return normalize_datetime(pd.Timestamp(value))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        # Los seriales de fecha de Excel son enteros. Los importes del cuadro
        # tienen decimales; no deben convertirse jamás en meses.
        if not number.is_integer() or not (20000 <= number <= 80000):
            return pd.NaT
        parsed = pd.Timestamp("1899-12-30") + pd.to_timedelta(int(number), unit="D")
        # Acepta seriales normales de fecha y la codificación mes-día/año.
        if 2020 <= parsed.year <= 2035:
            return normalize_datetime(parsed)
        return pd.NaT

    text = _pp_text(value)
    if not text:
        return pd.NaT
    normalized = _pp_norm(text)

    month_name_pattern = (
        r"(?:ene|enero|feb|febrero|mar|marzo|abr|abril|may|mayo|jun|junio|"
        r"jul|julio|ago|agosto|sep|sept|septiembre|set|setiembre|oct|octubre|"
        r"nov|noviembre|dic|diciembre)\s*\d{2,4}"
    )
    if re.fullmatch(month_name_pattern, normalized):
        return _pp_month(text)

    # Matrices guardadas en la hoja auxiliar conservan los Timestamp como
    # dd/mm/yyyy. También acepta ISO con o sin hora.
    looks_like_date = bool(
        re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", text.strip())
        or re.fullmatch(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}(?:\s+.*)?", text.strip())
    )
    if looks_like_date:
        parsed = _pp_date(text)
        return normalize_datetime(parsed)

    # 06/26, 2026-06 y variantes explícitas de mes/año.
    if re.fullmatch(r"\d{1,2}[/-]\d{2,4}", text.strip()) or re.fullmatch(r"20\d{2}[/-]\d{1,2}", text.strip()):
        return _pp_month(text)
    return pd.NaT


def _pp_valid_month_header(months: dict[int, pd.Timestamp]) -> bool:
    if len(months) < 2:
        return False
    ordered = sorted(months.items())
    columns = [column for column, _ in ordered]
    periods = [pd.Timestamp(period) for _, period in ordered]
    if any(pd.isna(period) or period.year < 2020 or period.year > 2100 for period in periods):
        return False
    if len({(period.year, period.month) for period in periods}) != len(periods):
        return False

    good_steps = 0
    comparable_steps = 0
    for (column_a, period_a), (column_b, period_b) in zip(ordered, ordered[1:]):
        if column_b - column_a > 2:
            continue
        comparable_steps += 1
        ordinal_a = period_a.year * 12 + period_a.month
        ordinal_b = period_b.year * 12 + period_b.month
        if ordinal_b - ordinal_a == 1:
            good_steps += 1

    if comparable_steps == 0:
        return False
    continuity = good_steps / comparable_steps
    compact_span = (max(columns) - min(columns) + 1) <= len(columns) + 3
    if len(months) == 2:
        return continuity == 1 and compact_span
    return continuity >= 0.75 and compact_span


def _pp_nearest_amount_left(raw: pd.DataFrame, row_index: int, column_index: int) -> tuple[float, int | None]:
    for candidate in range(column_index - 1, max(-1, column_index - 8), -1):
        value = _pp_number(raw.iat[row_index, candidate])
        if abs(value) > 0.0001:
            return value, candidate
    return 0.0, None


def _pp_parse_legacy_matrix(matrix: pd.DataFrame, sheet_name: str = "Principal") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Lee fielmente cronogramas, adelantos y resúmenes del cuadro original."""
    if matrix is None or matrix.empty:
        return pd.DataFrame(columns=_PLANES_COLUMNS), pd.DataFrame()

    raw = matrix.copy().reset_index(drop=True)
    raw.columns = range(len(raw.columns))
    raw = raw.fillna("")
    row_count = len(raw)
    column_count = len(raw.columns)

    def row_text(row_index: int) -> str:
        return " ".join(_pp_text(value) for value in raw.iloc[row_index].tolist() if _pp_text(value))

    row_norms = [_pp_norm(row_text(row_index)) for row_index in range(row_count)]

    # 1) Encabezados mensuales verdaderos. No se aceptan importes como seriales.
    header_rows: list[dict[str, Any]] = []
    global_total_columns: list[int] = []
    for row_index in range(row_count):
        months: dict[int, pd.Timestamp] = {}
        total_column = None
        for column_index in range(column_count):
            value = raw.iat[row_index, column_index]
            month = _pp_legacy_header_month(value)
            if pd.notna(month):
                months[column_index] = month
            if _pp_norm(value) in {"total", "total plan", "monto total"}:
                total_column = column_index
                global_total_columns.append(column_index)
        if _pp_valid_month_header(months):
            descriptor_values = [
                _pp_text(raw.iat[row_index, column])
                for column in range(0, min(months))
                if _pp_text(raw.iat[row_index, column])
            ]
            _, possible_identifier, _ = _pp_detect_identifier(descriptor_values)
            if possible_identifier and len(months) < 12:
                continue
            header_rows.append({
                "row": row_index,
                "months": months,
                "first_month_col": min(months),
                "last_month_col": max(months),
                "total_col": total_column,
            })

    if not header_rows:
        return pd.DataFrame(columns=_PLANES_COLUMNS), pd.DataFrame([
            {"Hoja": sheet_name, "Control": "Estructura", "Resultado": "No se detectaron encabezados mensuales válidos"}
        ])

    common_total_column = None
    if global_total_columns:
        common_total_column = max(set(global_total_columns), key=global_total_columns.count)

    adelanto_rows = [index for index, norm in enumerate(row_norms) if "adelanto de cuotas" in norm]
    prestamo_marker_rows = []
    for row_index in range(row_count):
        if any(_pp_norm(raw.iat[row_index, col]) == "prestamos" for col in range(column_count)):
            prestamo_marker_rows.append(row_index)

    records: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []

    # 2) Cronogramas mensuales VM y VMR.
    for block_position, header in enumerate(header_rows):
        start_row = int(header["row"]) + 1
        natural_end = header_rows[block_position + 1]["row"] if block_position + 1 < len(header_rows) else row_count
        boundaries = [value for value in adelanto_rows + prestamo_marker_rows if start_row <= value < natural_end]
        end_row = min(boundaries) if boundaries else natural_end
        month_columns: dict[int, pd.Timestamp] = header["months"]
        first_month_col = int(header["first_month_col"])
        total_column = header.get("total_col")
        if total_column is None:
            total_column = common_total_column

        block_values = []
        for row_index in range(max(0, int(header["row"])), end_row):
            block_values.extend(_pp_text(value) for value in raw.iloc[row_index].tolist() if _pp_text(value))
        block_norm = _pp_norm(" ".join(block_values))
        if "planes de pagos vmr" in block_norm or "reproductiva" in block_norm or re.search(r"\bvmr\b", block_norm):
            block_company = "VMR"
        elif "planes de pagos vm" in block_norm or "medical" in block_norm or re.search(r"\bvm\b", block_norm):
            block_company = "VM"
        else:
            block_company = "VITAE"
        carried_unit = "Reproductiva" if block_company == "VMR" else "Medical" if block_company == "VM" else "Corporativo"

        for row_index in range(start_row, end_row):
            descriptor_values = [
                _pp_text(raw.iat[row_index, column_index])
                for column_index in range(0, first_month_col)
                if _pp_text(raw.iat[row_index, column_index])
            ]
            descriptor_norm = _pp_norm(" ".join(descriptor_values))
            if descriptor_norm.startswith("total") or "totales" in descriptor_norm:
                declared_month_sum = sum(_pp_number(raw.iat[row_index, column]) for column in month_columns)
                validations.append({
                    "Hoja": sheet_name,
                    "Control": f"Total mensual de bloque · fila {row_index + 1}",
                    "Resultado": "Detectado",
                    "Importe": declared_month_sum,
                    "Diferencia": 0.0,
                })
                continue
            if any(token in descriptor_norm for token in ["planes de pagos vm", "planes de pagos vmr"]):
                continue

            creditor, identifier, detected_unit = _pp_detect_identifier(descriptor_values)
            if detected_unit:
                carried_unit = detected_unit
            if not identifier:
                continue
            if not creditor:
                creditor = "Plan / préstamo"

            company = block_company if block_company in {"VM", "VMR"} else _pp_company("", carried_unit, sheet_name)
            unit = carried_unit or ("Reproductiva" if company == "VMR" else "Medical" if company == "VM" else "Corporativo")
            financing_type = _pp_type("", creditor, "Deudas asumidas mes a mes")
            row_total = _pp_number(raw.iat[row_index, total_column]) if total_column is not None and total_column < column_count else 0.0
            row_record_indexes: list[int] = []
            monthly_pairs: list[tuple[pd.Timestamp, float]] = []

            for column_index, period in sorted(month_columns.items()):
                amount = _pp_number(raw.iat[row_index, column_index])
                if abs(amount) <= 0.0001:
                    continue
                monthly_pairs.append((period, amount))
                records.append({
                    "hoja_origen": sheet_name,
                    "seccion": "Deudas asumidas mes a mes",
                    "registro_clase": "Cuota mensual",
                    "empresa": company,
                    "unidad": unit,
                    "tipo_financiacion": financing_type,
                    "acreedor": creditor,
                    "identificador": identifier,
                    "periodo": period,
                    "vencimiento": period + pd.offsets.MonthEnd(0),
                    "importe_cuota": amount,
                    "pagado": 0.0,
                    "saldo": amount,
                    "moneda": "USD" if "usd" in descriptor_norm or "dolar" in descriptor_norm else "ARS",
                    "tasa_mensual": 0.0,
                    "capital_original": 0.0,
                    "cuotas_adeudadas_monto": 0.0,
                    "monto_adelanto": 0.0,
                    "intereses_ahorrados": 0.0,
                    "total_plan_declarado": row_total,
                    "estado": "A conciliar" if period < pd.Timestamp.today().replace(day=1).normalize() else "Programada",
                    "observaciones": "Cuota leída del cronograma mensual original.",
                    "fila_origen": row_index + 1,
                    "columna_origen": _pp_excel_column(column_index),
                    "fuente": f"Excel / matriz histórica · {sheet_name}",
                    "created_at": "",
                    "updated_at": "",
                })
                row_record_indexes.append(len(records) - 1)

            if row_record_indexes:
                total_installments = len(row_record_indexes)
                for installment_number, record_index in enumerate(row_record_indexes, 1):
                    records[record_index]["cuota_numero"] = installment_number
                    records[record_index]["cuotas_totales"] = total_installments

                monthly_sum = sum(amount for _, amount in monthly_pairs)
                result_label = "Sin total declarado"
                difference = 0.0
                matched_from = pd.NaT
                if row_total:
                    suffixes = []
                    for position in range(len(monthly_pairs)):
                        suffixes.append((monthly_pairs[position][0], sum(amount for _, amount in monthly_pairs[position:])))
                    matched_from, closest_sum = min(suffixes, key=lambda item: abs(row_total - item[1]))
                    difference = row_total - closest_sum
                    result_label = (
                        f"Coincide desde {_pp_month_label(matched_from)}"
                        if abs(difference) <= 1.0
                        else "Total declarado informativo"
                    )
                validations.append({
                    "Hoja": sheet_name,
                    "Control": f"{company} · {creditor} · {identifier}",
                    "Resultado": result_label,
                    "Importe": monthly_sum,
                    "Total declarado": row_total,
                    "Diferencia": difference,
                })

    # Mapa de empresa por identificador para leer los bloques sin rótulo repetido.
    identifier_company: dict[str, str] = {}
    for record in records:
        if record.get("registro_clase") == "Cuota mensual":
            identifier_company[_pp_norm(record.get("identificador"))] = _pp_text(record.get("empresa"))

    # 3) Totales exactos declarados: PLANES DE PAGOS VM / VMR.
    for row_index in range(row_count):
        for column_index in range(column_count):
            label_norm = _pp_norm(raw.iat[row_index, column_index])
            if label_norm not in {"planes de pagos vm", "planes de pagos vmr"}:
                continue
            amount, amount_column = _pp_nearest_amount_left(raw, row_index, column_index)
            if abs(amount) <= 0.0001:
                continue
            company = "VMR" if label_norm.endswith("vmr") else "VM"
            records.append({
                "hoja_origen": sheet_name,
                "seccion": "Resumen de planes de pagos",
                "registro_clase": "Resumen de planes",
                "empresa": company,
                "unidad": "Reproductiva" if company == "VMR" else "Medical",
                "tipo_financiacion": "Plan de pagos",
                "acreedor": "Planes de pagos consolidados",
                "identificador": f"PLANES-DE-PAGOS-{company}",
                "periodo": pd.NaT,
                "vencimiento": pd.NaT,
                "cuota_numero": 0,
                "cuotas_totales": 0,
                "importe_cuota": amount,
                "pagado": 0.0,
                "saldo": amount,
                "moneda": "ARS",
                "tasa_mensual": 0.0,
                "capital_original": amount,
                "cuotas_adeudadas_monto": 0.0,
                "monto_adelanto": 0.0,
                "intereses_ahorrados": 0.0,
                "total_plan_declarado": amount,
                "estado": "A conciliar",
                "observaciones": "Total exacto declarado en el cuadro original; no se suma al flujo mensual para evitar duplicación.",
                "fila_origen": row_index + 1,
                "columna_origen": _pp_excel_column(amount_column if amount_column is not None else column_index),
                "fuente": f"Resumen declarado · {sheet_name}",
                "created_at": "",
                "updated_at": "",
            })

    # 4) Simulaciones de adelanto: deuda, adelanto e intereses ahorrados.
    advance_headers: list[dict[str, Any]] = []
    for row_index in range(row_count):
        found: dict[str, int] = {}
        for column_index in range(column_count):
            norm = _pp_norm(raw.iat[row_index, column_index])
            if norm in {"cuotas adeudadas", "adelanto", "intereses ahorrados"}:
                found[norm] = column_index
        if {"cuotas adeudadas", "adelanto", "intereses ahorrados"}.issubset(found):
            advance_headers.append({"row": row_index, "columns": found})

    for header_position, header in enumerate(advance_headers):
        start_row = header["row"] + 1
        end_row = advance_headers[header_position + 1]["row"] if header_position + 1 < len(advance_headers) else row_count
        debt_col = header["columns"]["cuotas adeudadas"]
        advance_col = header["columns"]["adelanto"]
        savings_col = header["columns"]["intereses ahorrados"]
        first_value_col = min(debt_col, advance_col, savings_col)

        candidate_companies = []
        for row_index in range(start_row, end_row):
            descriptor_values = [
                _pp_text(raw.iat[row_index, column])
                for column in range(first_value_col)
                if _pp_text(raw.iat[row_index, column])
            ]
            _, identifier, _ = _pp_detect_identifier(descriptor_values)
            company = identifier_company.get(_pp_norm(identifier))
            if company:
                candidate_companies.append(company)
        if candidate_companies and len(set(candidate_companies)) == 1:
            block_company = candidate_companies[0]
        else:
            block_company = "VM" if header_position == 0 else "VMR" if header_position == 1 else "VITAE"
        carried_unit = "Medical" if block_company == "VM" else "Reproductiva" if block_company == "VMR" else "Corporativo"

        for row_index in range(start_row, end_row):
            descriptor_values = [
                _pp_text(raw.iat[row_index, column])
                for column in range(first_value_col)
                if _pp_text(raw.iat[row_index, column])
            ]
            descriptor_norm = _pp_norm(" ".join(descriptor_values))
            debt = _pp_number(raw.iat[row_index, debt_col])
            advance_amount = _pp_number(raw.iat[row_index, advance_col])
            savings = _pp_number(raw.iat[row_index, savings_col])
            if descriptor_norm.startswith("total") or "totales" in descriptor_norm:
                validations.append({
                    "Hoja": sheet_name,
                    "Control": f"Total adelantos · fila {row_index + 1}",
                    "Resultado": "Detectado",
                    "Cuotas adeudadas": debt,
                    "Adelanto": advance_amount,
                    "Intereses ahorrados": savings,
                })
                break

            creditor, identifier, detected_unit = _pp_detect_identifier(descriptor_values)
            if detected_unit:
                carried_unit = detected_unit
            if not identifier:
                if abs(debt) <= 0.0001 and abs(advance_amount) <= 0.0001 and abs(savings) <= 0.0001:
                    continue
                continue
            company = identifier_company.get(_pp_norm(identifier), block_company)
            if company not in {"VM", "VMR"}:
                company = _pp_company("", carried_unit, sheet_name)
            unit = carried_unit or ("Reproductiva" if company == "VMR" else "Medical" if company == "VM" else "Corporativo")
            note_values = [
                _pp_text(raw.iat[row_index, column])
                for column in range(max(debt_col, advance_col, savings_col) + 1, column_count)
                if _pp_text(raw.iat[row_index, column])
            ]
            notes = " | ".join(note_values)
            records.append({
                "hoja_origen": sheet_name,
                "seccion": "Adelanto de cuotas",
                "registro_clase": "Adelanto de cuotas",
                "empresa": company,
                "unidad": unit,
                "tipo_financiacion": "Adelanto de cuotas",
                "acreedor": creditor or "PP ARCA",
                "identificador": identifier,
                "periodo": pd.NaT,
                "vencimiento": pd.NaT,
                "cuota_numero": 0,
                "cuotas_totales": 0,
                "importe_cuota": advance_amount,
                "pagado": 0.0,
                "saldo": advance_amount,
                "moneda": "ARS",
                "tasa_mensual": 0.0,
                "capital_original": debt,
                "cuotas_adeudadas_monto": debt,
                "monto_adelanto": advance_amount,
                "intereses_ahorrados": savings,
                "total_plan_declarado": debt,
                "estado": "Pendiente" if advance_amount > 0 else "A conciliar",
                "observaciones": notes or "Simulación de cancelación anticipada leída del cuadro original.",
                "fila_origen": row_index + 1,
                "columna_origen": _pp_excel_column(debt_col),
                "fuente": f"Adelantos · {sheet_name}",
                "created_at": "",
                "updated_at": "",
            })

    # 5) Resumen de préstamos VM/VMR ubicado en la columna lateral.
    processed_loan_markers: set[tuple[int, int]] = set()
    for row_index in range(row_count):
        for column_index in range(column_count):
            if _pp_norm(raw.iat[row_index, column_index]) != "prestamos":
                continue
            marker = (row_index, column_index)
            if marker in processed_loan_markers:
                continue
            processed_loan_markers.add(marker)
            values: list[tuple[int, float]] = []
            for candidate_row in range(row_index + 1, min(row_count, row_index + 8)):
                amount = _pp_number(raw.iat[candidate_row, column_index])
                if abs(amount) > 0.0001:
                    values.append((candidate_row, amount))
                if "adelanto de cuotas" in row_norms[candidate_row] and values:
                    break
            if not values:
                continue

            detail_values = values
            declared_total = 0.0
            if len(values) >= 3 and abs(values[-1][1] - sum(amount for _, amount in values[:-1])) <= 1.0:
                detail_values = values[:-1]
                declared_total = values[-1][1]
            companies = ["VM", "VMR"] if len(detail_values) == 2 else ["VITAE"] * len(detail_values)
            for position, ((value_row, amount), company) in enumerate(zip(detail_values, companies)):
                records.append({
                    "hoja_origen": sheet_name,
                    "seccion": "Préstamos",
                    "registro_clase": "Resumen de préstamos",
                    "empresa": company,
                    "unidad": "Medical" if company == "VM" else "Reproductiva" if company == "VMR" else "Corporativo",
                    "tipo_financiacion": "Préstamo bancario",
                    "acreedor": "Préstamos consolidados",
                    "identificador": f"PRESTAMOS-{company}-{position + 1}" if company == "VITAE" else f"PRESTAMOS-{company}",
                    "periodo": pd.NaT,
                    "vencimiento": pd.NaT,
                    "cuota_numero": 0,
                    "cuotas_totales": 0,
                    "importe_cuota": amount,
                    "pagado": 0.0,
                    "saldo": amount,
                    "moneda": "ARS",
                    "tasa_mensual": 0.0,
                    "capital_original": amount,
                    "cuotas_adeudadas_monto": 0.0,
                    "monto_adelanto": 0.0,
                    "intereses_ahorrados": 0.0,
                    "total_plan_declarado": amount,
                    "estado": "A conciliar",
                    "observaciones": "Saldo consolidado declarado en el cuadro; no se suma al flujo mensual.",
                    "fila_origen": value_row + 1,
                    "columna_origen": _pp_excel_column(column_index),
                    "fuente": f"Resumen de préstamos · {sheet_name}",
                    "created_at": "",
                    "updated_at": "",
                })
            validations.append({
                "Hoja": sheet_name,
                "Control": "Resumen de préstamos",
                "Resultado": "Coincide" if declared_total and abs(declared_total - sum(amount for _, amount in detail_values)) <= 1.0 else "Detectado",
                "Importe": sum(amount for _, amount in detail_values),
                "Total declarado": declared_total,
                "Diferencia": declared_total - sum(amount for _, amount in detail_values) if declared_total else 0.0,
            })

    prepared = _pp_prepare(pd.DataFrame(records), source=f"Matriz histórica · {sheet_name}")
    return prepared, pd.DataFrame(validations)


def _pp_prepare_sheet(df: pd.DataFrame) -> pd.DataFrame:
    clean = _pp_prepare(df, source="Sistema VITAE").copy()
    now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    clean["updated_at"] = now
    clean["created_at"] = clean["created_at"].replace("", now)
    clean["periodo"] = pd.to_datetime(clean["periodo"], errors="coerce").dt.strftime("%Y-%m").fillna("")
    clean["vencimiento"] = pd.to_datetime(clean["vencimiento"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    numeric_columns = [
        "cuota_numero", "cuotas_totales", "importe_cuota", "pagado", "saldo",
        "tasa_mensual", "capital_original", "cuotas_adeudadas_monto",
        "monto_adelanto", "intereses_ahorrados", "total_plan_declarado", "fila_origen",
    ]
    for column in numeric_columns:
        clean[column] = pd.to_numeric(clean[column], errors="coerce").fillna(0)
    return clean[_PLANES_COLUMNS]


def _pp_enrich(df: pd.DataFrame) -> pd.DataFrame:
    data = _pp_prepare(df).copy()
    if data.empty:
        for column in [
            "_periodo", "_vencimiento", "_importe", "_pagado", "_saldo",
            "_situacion", "_es_cuota_mensual",
        ]:
            data[column] = pd.Series(dtype="object")
        return data
    data["_periodo"] = pd.to_datetime(data["periodo"], errors="coerce").dt.to_period("M").dt.to_timestamp()
    data["_vencimiento"] = pd.to_datetime(data["vencimiento"], errors="coerce")
    data["_importe"] = pd.to_numeric(data["importe_cuota"], errors="coerce").fillna(0.0)
    data["_pagado"] = pd.to_numeric(data["pagado"], errors="coerce").fillna(0.0)
    data["_saldo"] = pd.to_numeric(data["saldo"], errors="coerce").fillna(0.0).clip(lower=0)
    data["_es_cuota_mensual"] = data["registro_clase"].eq("Cuota mensual")
    current_month = pd.Timestamp.today().replace(day=1).normalize()

    def situation(row: pd.Series) -> str:
        record_class = _pp_text(row.get("registro_clase"))
        state = _pp_norm(row.get("estado"))
        period = row.get("_periodo")
        if record_class == "Resumen de planes":
            return "Total declarado"
        if record_class == "Resumen de préstamos":
            return "Saldo declarado"
        if record_class == "Adelanto de cuotas":
            return "Simulación de adelanto"
        if state in {"pagada", "cancelada"} or float(row.get("_saldo", 0)) <= 0.01:
            return "Pagada"
        if state == "parcial":
            return "Parcial"
        if pd.isna(period):
            return "Sin período"
        if period < current_month:
            return "Histórica sin conciliar" if state == "a conciliar" else "Vencida"
        if period == current_month:
            return "Mes actual"
        if period <= current_month + pd.DateOffset(months=3):
            return "Próximos 90 días"
        return "Futura"

    data["_situacion"] = data.apply(situation, axis=1)
    return data


def _pp_plan_summary(data: pd.DataFrame) -> pd.DataFrame:
    if data is None or data.empty:
        return pd.DataFrame()
    grouping = [
        "hoja_origen", "seccion", "registro_clase", "empresa", "unidad",
        "tipo_financiacion", "acreedor", "identificador", "moneda",
    ]
    summary = data.groupby(grouping, dropna=False, as_index=False).agg(
        Cuotas=("id_registro", "size"),
        Desde=("_periodo", "min"),
        Hasta=("_periodo", "max"),
        Total_cronograma=("_importe", "sum"),
        Pagado=("_pagado", "sum"),
        Saldo=("_saldo", "sum"),
        Total_declarado=("total_plan_declarado", "max"),
        Cuotas_adeudadas=("cuotas_adeudadas_monto", "max"),
        Monto_adelanto=("monto_adelanto", "max"),
        Intereses_ahorrados=("intereses_ahorrados", "max"),
    )
    current_month = pd.Timestamp.today().replace(day=1).normalize()
    pending_source = data[
        (data["_saldo"] > 0.01)
        & (
            data["registro_clase"].ne("Cuota mensual")
            | (data["_periodo"] >= current_month)
        )
    ]
    remaining = (
        pending_source.groupby(grouping, dropna=False)["id_registro"]
        .size()
        .rename("Cuotas_pendientes")
        .reset_index()
    )
    summary = summary.merge(remaining, on=grouping, how="left")
    summary["Cuotas_pendientes"] = summary["Cuotas_pendientes"].fillna(0).astype(int)
    summary["Diferencia_control"] = (
        summary["Total_declarado"].where(summary["Total_declarado"] > 0, summary["Total_cronograma"])
        - summary["Total_cronograma"]
    )
    summary.loc[summary["registro_clase"].ne("Cuota mensual"), "Diferencia_control"] = 0.0
    summary["Avance_%"] = (
        summary["Pagado"] / summary["Total_cronograma"].replace(0, pd.NA) * 100
    ).fillna(0).clip(0, 100)
    return summary.sort_values(
        ["registro_clase", "Saldo", "Hasta"], ascending=[True, False, True], na_position="last"
    ).reset_index(drop=True)


def _pp_merge(existing: pd.DataFrame, incoming: pd.DataFrame, replace_sheets: list[str] | None = None) -> pd.DataFrame:
    base = _pp_prepare(existing)
    new = _pp_prepare(incoming)
    if replace_sheets:
        normalized_sheets = {_pp_norm(value) for value in replace_sheets}
        base = base[~base["hoja_origen"].apply(_pp_norm).isin(normalized_sheets)].copy()
    combined = pd.concat([base, new], ignore_index=True)
    if combined.empty:
        return pd.DataFrame(columns=_PLANES_COLUMNS)
    # Mismo tipo de registro, plan, mes y celda: la última importación reemplaza la anterior.
    combined["_period_key"] = pd.to_datetime(combined["periodo"], errors="coerce").dt.strftime("%Y-%m")
    dedupe_keys = [
        "hoja_origen", "registro_clase", "empresa", "acreedor", "identificador",
        "_period_key", "fila_origen", "columna_origen",
    ]
    combined = combined.drop_duplicates(subset=dedupe_keys, keep="last").drop(columns="_period_key")
    return _pp_prepare(combined)


def _pp_raw_long(matrix: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
    if matrix is None:
        return pd.DataFrame(columns=["hoja_origen", "fila", "columna", "valor"])
    raw = matrix.copy()
    raw.columns = range(len(raw.columns))
    rows: list[dict[str, Any]] = []
    for row_index in range(len(raw)):
        for column_index in range(len(raw.columns)):
            value = raw.iat[row_index, column_index]
            if isinstance(value, pd.Timestamp):
                text = value.strftime("%d/%m/%Y")
            else:
                text = _pp_text(value)
            rows.append({
                "hoja_origen": sheet_name,
                "fila": row_index + 1,
                "columna": column_index + 1,
                "valor": text,
            })
    return pd.DataFrame(rows, columns=["hoja_origen", "fila", "columna", "valor"])


def _pp_raw_matrix(raw_long: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
    if raw_long is None or raw_long.empty:
        return pd.DataFrame()
    data = raw_long[raw_long["hoja_origen"].astype(str).eq(str(sheet_name))].copy()
    if data.empty:
        return pd.DataFrame()
    data["fila"] = pd.to_numeric(data["fila"], errors="coerce").fillna(0).astype(int)
    data["columna"] = pd.to_numeric(data["columna"], errors="coerce").fillna(0).astype(int)
    max_row = int(data["fila"].max())
    max_col = int(data["columna"].max())
    matrix = pd.DataFrame("", index=range(max_row), columns=range(max_col))
    for _, row in data.iterrows():
        r, c = int(row["fila"]) - 1, int(row["columna"]) - 1
        if r >= 0 and c >= 0:
            matrix.iat[r, c] = _pp_text(row["valor"])
    matrix.columns = [_pp_excel_column(index) for index in range(max_col)]
    matrix.index = range(1, max_row + 1)
    return matrix


def _pp_aux_table(table: str, suffix: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_]+", "_", str(table)).strip("_") or "planes_pagos_prestamos"
    return f"{clean[:80]}_{suffix}"[:98]


def _pp_load_raw_sheet(table: str) -> pd.DataFrame:
    raw_table = _pp_aux_table(table, "matriz")
    try:
        frame = get_df(raw_table)
    except Exception:
        return pd.DataFrame(columns=["hoja_origen", "fila", "columna", "valor"])
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["hoja_origen", "fila", "columna", "valor"])
    columns = {_pp_norm(column).replace(" ", "_"): column for column in frame.columns}
    required = [columns.get("hoja_origen"), columns.get("fila"), columns.get("columna"), columns.get("valor")]
    if any(column is None for column in required):
        return pd.DataFrame(columns=["hoja_origen", "fila", "columna", "valor"])
    result = frame[required].copy()
    result.columns = ["hoja_origen", "fila", "columna", "valor"]
    return result


def _pp_needs_repair(df: pd.DataFrame | None) -> bool:
    """Detecta la lectura antigua que convertía importes en fechas falsas."""
    prepared = _pp_prepare(df)
    if prepared.empty:
        return False
    monthly = prepared[prepared["registro_clase"].eq("Cuota mensual")].copy()
    periods = pd.to_datetime(monthly["periodo"], errors="coerce")
    if periods.notna().any():
        years = periods.dropna().dt.year
        if years.lt(2020).any() or years.gt(2040).any():
            return True
        duplicate_periods = monthly.duplicated(
            subset=["hoja_origen", "empresa", "acreedor", "identificador", "periodo"],
            keep=False,
        )
        if duplicate_periods.any():
            return True
    malformed_summaries = prepared[
        prepared["registro_clase"].isin(["Adelanto de cuotas", "Resumen de planes", "Resumen de préstamos"])
        & pd.to_datetime(prepared["periodo"], errors="coerce").notna()
    ]
    return not malformed_summaries.empty


def _pp_reparse_raw_long(raw_long: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if raw_long is None or raw_long.empty:
        return pd.DataFrame(columns=_PLANES_COLUMNS), pd.DataFrame()
    frames: list[pd.DataFrame] = []
    controls: list[pd.DataFrame] = []
    for sheet_name in raw_long["hoja_origen"].dropna().astype(str).unique().tolist():
        matrix = _pp_raw_matrix(raw_long, sheet_name)
        parsed, validation = _pp_parse_legacy_matrix(matrix, sheet_name)
        if not parsed.empty:
            frames.append(parsed)
        if not validation.empty:
            controls.append(validation)
    result = _pp_prepare(pd.concat(frames, ignore_index=True)) if frames else pd.DataFrame(columns=_PLANES_COLUMNS)
    validation = pd.concat(controls, ignore_index=True) if controls else pd.DataFrame()
    return result, validation


def _pp_money(value: Any, currency: str = "ARS") -> str:
    number = _pp_number(value)
    symbol = "USD" if str(currency).upper() == "USD" else "$"
    formatted = f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{symbol} {formatted}"


def _pp_month_label(value: Any) -> str:
    parsed = _pp_month(value)
    if pd.isna(parsed):
        return "Sin período"
    months = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    return f"{months[parsed.month - 1]}-{str(parsed.year)[-2:]}"


def _pp_css() -> None:
    st.markdown(
        """
        <style>
        .pp-hero {
            background: linear-gradient(135deg, #122335 0%, #1e4963 52%, #2f7f87 100%);
            color: white; border-radius: 22px; padding: 26px 28px; margin: 4px 0 18px 0;
            box-shadow: 0 16px 36px rgba(16, 43, 61, .22);
        }
        .pp-kicker {font-size: .76rem; letter-spacing: .14em; text-transform: uppercase; opacity: .82; font-weight: 750;}
        .pp-title {font-size: 2rem; font-weight: 850; line-height: 1.08; margin-top: 6px;}
        .pp-subtitle {font-size: .97rem; opacity: .91; max-width: 980px; margin-top: 9px;}
        .pp-card {
            background: rgba(255,255,255,.97); border: 1px solid rgba(30,73,99,.14);
            border-radius: 17px; padding: 16px 18px; min-height: 128px;
            box-shadow: 0 8px 24px rgba(20, 54, 73, .07); margin-bottom: 10px;
        }
        .pp-card-label {font-size: .76rem; color: #62717c; font-weight: 750; text-transform: uppercase; letter-spacing: .06em;}
        .pp-card-value {font-size: 1.52rem; color: #173247; font-weight: 850; margin-top: 5px; white-space: nowrap;}
        .pp-card-note {font-size: .80rem; color: #75828b; margin-top: 7px;}
        .pp-alert {border-left: 5px solid #e1a629; background: #fff9eb; border-radius: 12px; padding: 12px 15px; margin: 7px 0;}
        .pp-danger {border-left-color: #bd4b4b; background: #fff3f3;}
        .pp-ok {border-left-color: #318267; background: #f2fbf7;}
        .pp-note {background:#f5f8fa; border:1px solid #dfe8ed; border-radius:12px; padding:12px 14px; color:#50616d;}
        div[data-testid="stMetric"] {border: 1px solid rgba(30,73,99,.12); padding: 12px 14px; border-radius: 14px; background: rgba(255,255,255,.94);}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _pp_metric_card(label: str, value: str, note: str = "") -> None:
    st.markdown(
        f"""
        <div class="pp-card">
            <div class="pp-card-label">{label}</div>
            <div class="pp-card-value">{value}</div>
            <div class="pp-card-note">{note}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _pp_export_excel(data: pd.DataFrame, raw_long: pd.DataFrame) -> bytes:
    from io import BytesIO

    output = BytesIO()
    enriched = _pp_enrich(data)
    summary = _pp_plan_summary(enriched)
    monthly = pd.DataFrame()
    if not enriched.empty:
        monthly = enriched.groupby(["_periodo", "empresa", "moneda"], as_index=False).agg(
            Importe=("_importe", "sum"), Pagado=("_pagado", "sum"), Saldo=("_saldo", "sum")
        )
        monthly = monthly.rename(columns={"_periodo": "Periodo"})
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        _pp_prepare_sheet(data).to_excel(writer, sheet_name="Cronograma normalizado", index=False)
        if not summary.empty:
            summary.to_excel(writer, sheet_name="Resumen de planes", index=False)
        if not monthly.empty:
            monthly.to_excel(writer, sheet_name="Flujo mensual", index=False)
        if raw_long is not None and not raw_long.empty:
            for sheet_name in raw_long["hoja_origen"].dropna().astype(str).unique().tolist():
                matrix = _pp_raw_matrix(raw_long, sheet_name)
                safe_name = re.sub(r"[\\/*?:\[\]]", "_", sheet_name)[:27] or "Original"
                matrix.to_excel(writer, sheet_name=f"ORG {safe_name}"[:31], index=True)
    output.seek(0)
    return output.getvalue()


def render_planes_pagos_prestamos_pro(
    df_original: pd.DataFrame,
    table: str = "planes_pagos_prestamos",
    module_name: str = "Planes de pagos y préstamos",
) -> None:
    """Centro financiero integral conectado a Google Sheets."""
    _pp_css()
    today = pd.Timestamp.today().normalize()
    current_month = today.replace(day=1)

    # La hoja puede estar normalizada, conservar el cuadro horizontal o venir
    # de una importación anterior con fechas mal interpretadas. La matriz
    # original guardada en Sheets permite una reparación automática y segura.
    fallback_matrix = pd.DataFrame()
    validation_from_sheet = pd.DataFrame()
    raw_long = _pp_load_raw_sheet(table)
    repaired_from_raw = False

    if _pp_is_canonical(df_original):
        candidate = _pp_prepare(df_original, source="Google Sheets")
        if _pp_needs_repair(candidate) and raw_long is not None and not raw_long.empty:
            repaired, repaired_controls = _pp_reparse_raw_long(raw_long)
            if not repaired.empty:
                base = repaired
                validation_from_sheet = repaired_controls
                repaired_from_raw = True
            else:
                base = candidate
        else:
            base = candidate
    else:
        fallback_matrix = _pp_matrix_from_dataframe(df_original)
        base, validation_from_sheet = _pp_parse_legacy_matrix(fallback_matrix, "Hoja principal")
        if base.empty and raw_long is not None and not raw_long.empty:
            base, validation_from_sheet = _pp_reparse_raw_long(raw_long)

    data = _pp_enrich(base)
    plan_summary = _pp_plan_summary(data)

    hero_left, hero_right = st.columns([5.5, 1.15])
    with hero_left:
        st.markdown(
            """
            <div class="pp-hero">
                <div class="pp-kicker">Dirección financiera · VMR + VM</div>
                <div class="pp-title">Planes de pagos y préstamos</div>
                <div class="pp-subtitle">
                    Cronograma unificado de cuotas, préstamos bancarios, planes ARCA y adelantos.
                    Lee Google Sheets, conserva el cuadro original por hoja y proyecta el impacto mensual.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with hero_right:
        st.write("")
        st.write("")
        if st.button("🔄 Actualizar", key=f"pp_refresh_{table}", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.caption(f"Actualizado: {today.strftime('%d/%m/%Y')}")

    if repaired_from_raw:
        st.warning(
            "Detecté registros creados por la lectura anterior —importes convertidos en fechas y años incorrectos—. "
            "La pantalla ya está mostrando la reconstrucción correcta desde la matriz original guardada."
        )
        repair_left, repair_right = st.columns([4.2, 1.3])
        with repair_left:
            st.caption(
                "La reparación conserva el respaldo y reemplaza únicamente la hoja normalizada. "
                "No modifica la matriz original ni el Excel fuente."
            )
        with repair_right:
            if st.button(
                "🛠️ Guardar reparación",
                type="primary",
                use_container_width=True,
                key=f"pp_save_auto_repair_{table}",
            ):
                try:
                    if df_original is not None and (not df_original.empty or len(df_original.columns) > 0):
                        try:
                            sync_df_to_sheet(_pp_aux_table(table, "backup"), df_original.copy())
                        except Exception:
                            pass
                    sync_df_to_sheet(table, _pp_prepare_sheet(base))
                    st.cache_data.clear()
                    st.success("Lectura corregida y guardada en Google Sheets.")
                    st.rerun()
                except Exception as error:
                    st.error("No se pudo guardar la reparación en Google Sheets.")
                    st.exception(error)

    tabs = st.tabs([
        "📊 Centro ejecutivo",
        "🗓️ Cronograma",
        "💳 Cargar / pagar",
        "📥 Importar Excel",
        "✏️ Gestionar",
        "🧾 Vista original",
        "🤖 Analista IA",
        "📤 Exportar",
    ])

    # ---------------------------------------------------------------
    # CENTRO EJECUTIVO
    # ---------------------------------------------------------------
    with tabs[0]:
        if data.empty:
            st.info(
                "La hoja todavía no contiene cuotas reconocibles. Importá el Excel desde “Importar Excel” "
                "o cargá el primer plan manualmente."
            )
        else:
            # Totales generales: las cuotas mensuales se separan de los
            # resúmenes declarados para no sumar dos veces los mismos compromisos.
            monthly_data = data[data["registro_clase"].eq("Cuota mensual")].copy()
            plan_declared = data[data["registro_clase"].eq("Resumen de planes")].copy()
            loan_declared = data[data["registro_clase"].eq("Resumen de préstamos")].copy()
            advance_data = data[data["registro_clase"].eq("Adelanto de cuotas")].copy()

            ars = monthly_data[monthly_data["moneda"].eq("ARS")]
            usd = monthly_data[monthly_data["moneda"].eq("USD")]
            general_total_ars = ars["_importe"].sum()
            future_balance_ars = ars[(ars["_periodo"] >= current_month) & (ars["_saldo"] > 0.01)]["_saldo"].sum()
            month_ars = ars[ars["_periodo"].eq(current_month)]["_saldo"].sum()
            next_90_ars = ars[
                ars["_periodo"].between(current_month, current_month + pd.DateOffset(months=2), inclusive="both")
            ]["_saldo"].sum()
            future_balance_usd = usd[(usd["_periodo"] >= current_month) & (usd["_saldo"] > 0.01)]["_saldo"].sum()
            monthly_summary = _pp_plan_summary(monthly_data)
            active_plans = int(monthly_summary[monthly_summary["Saldo"] > 0.01].shape[0]) if not monthly_summary.empty else 0
            final_month = monthly_data.loc[
                (monthly_data["_saldo"] > 0.01) & monthly_data["_periodo"].notna(), "_periodo"
            ].max()

            st.markdown("#### Totales generales del cronograma mensual")
            c1, c2, c3, c4, c5 = st.columns(5)
            with c1:
                _pp_metric_card("Cronograma total ARS", _pp_money(general_total_ars), f"{len(ars)} cuotas mensuales")
            with c2:
                _pp_metric_card("Pendiente desde hoy", _pp_money(future_balance_ars), "No duplica saldos consolidados")
            with c3:
                _pp_metric_card("Cuotas del mes", _pp_money(month_ars), _pp_month_label(current_month))
            with c4:
                _pp_metric_card("Próximos 90 días", _pp_money(next_90_ars), "Exigencia financiera inmediata")
            with c5:
                _pp_metric_card("Planes activos", str(active_plans), f"Fin estimado: {_pp_month_label(final_month)}")
            if future_balance_usd > 0:
                st.metric("Pendiente programado USD", _pp_money(future_balance_usd, "USD"))

            if not plan_declared.empty or not loan_declared.empty:
                st.markdown("#### Totales exactos declarados en la planilla")
                declared_columns = st.columns(4)
                declared_specs = [
                    ("Planes de pagos VM", plan_declared[plan_declared["empresa"].eq("VM")]["_importe"].sum()),
                    ("Planes de pagos VMR", plan_declared[plan_declared["empresa"].eq("VMR")]["_importe"].sum()),
                    ("Préstamos VM", loan_declared[loan_declared["empresa"].eq("VM")]["_importe"].sum()),
                    ("Préstamos VMR", loan_declared[loan_declared["empresa"].eq("VMR")]["_importe"].sum()),
                ]
                for column, (label, amount) in zip(declared_columns, declared_specs):
                    with column:
                        _pp_metric_card(label, _pp_money(amount), "Valor leído de la celda original")

            if not advance_data.empty:
                st.markdown("#### Cancelación anticipada")
                a1, a2, a3, a4 = st.columns(4)
                a1.metric("Cuotas adeudadas", _pp_money(advance_data["cuotas_adeudadas_monto"].sum()))
                a2.metric("Adelanto simulado", _pp_money(advance_data["monto_adelanto"].sum()))
                a3.metric("Intereses ahorrables", _pp_money(advance_data["intereses_ahorrados"].sum()))
                a4.metric("Planes evaluados", int(advance_data["identificador"].nunique()))

            st.divider()
            st.markdown("#### Filtros de análisis")
            f1, f2, f3, f4 = st.columns(4)
            with f1:
                sheet_options = sorted(monthly_data["hoja_origen"].dropna().astype(str).unique().tolist())
                selected_sheets = st.multiselect(
                    "Hoja de origen", sheet_options, default=sheet_options, key=f"pp_filter_sheet_{table}"
                )
            with f2:
                company_options = sorted(monthly_data["empresa"].dropna().astype(str).unique().tolist())
                selected_companies = st.multiselect(
                    "Empresa", company_options, default=company_options, key=f"pp_filter_company_{table}"
                )
            with f3:
                type_options = sorted(monthly_data["tipo_financiacion"].dropna().astype(str).unique().tolist())
                selected_types = st.multiselect(
                    "Tipo", type_options, default=type_options, key=f"pp_filter_type_{table}"
                )
            with f4:
                creditor_options = sorted(monthly_data["acreedor"].dropna().astype(str).unique().tolist())
                selected_creditors = st.multiselect(
                    "Acreedor", creditor_options, default=creditor_options, key=f"pp_filter_creditor_{table}"
                )

            available_periods = monthly_data["_periodo"].dropna()
            min_period = available_periods.min().date() if not available_periods.empty else current_month.date()
            max_period = available_periods.max().date() if not available_periods.empty else current_month.date()
            range_value = st.date_input(
                "Rango del cronograma",
                value=(min_period, max_period),
                min_value=min_period,
                max_value=max_period,
                key=f"pp_filter_dates_{table}",
            )
            if isinstance(range_value, (tuple, list)) and len(range_value) == 2:
                start_period = pd.Timestamp(range_value[0]).replace(day=1)
                end_period = pd.Timestamp(range_value[1]).replace(day=1)
            else:
                start_period = pd.Timestamp(range_value).replace(day=1)
                end_period = start_period

            filtered = monthly_data.copy()
            if selected_sheets:
                filtered = filtered[filtered["hoja_origen"].isin(selected_sheets)]
            if selected_companies:
                filtered = filtered[filtered["empresa"].isin(selected_companies)]
            if selected_types:
                filtered = filtered[filtered["tipo_financiacion"].isin(selected_types)]
            if selected_creditors:
                filtered = filtered[filtered["acreedor"].isin(selected_creditors)]
            filtered = filtered[filtered["_periodo"].between(start_period, end_period, inclusive="both")]

            if filtered.empty:
                st.warning("Los filtros no dejaron cuotas visibles. Los totales generales permanecen arriba.")
            else:
                filtered_ars = filtered[filtered["moneda"].eq("ARS")]
                filtered_usd = filtered[filtered["moneda"].eq("USD")]
                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("Total filtrado ARS", _pp_money(filtered_ars["_importe"].sum()))
                m2.metric("Saldo filtrado ARS", _pp_money(filtered_ars["_saldo"].sum()))
                m3.metric("Pagado ARS", _pp_money(filtered_ars["_pagado"].sum()))
                m4.metric("Saldo USD", _pp_money(filtered_usd["_saldo"].sum(), "USD"))
                m5.metric("Cuotas visibles", len(filtered))

                historical_unreconciled = filtered[
                    filtered["_situacion"].eq("Histórica sin conciliar") & (filtered["_saldo"] > 0.01)
                ]
                current_due = filtered[filtered["_situacion"].eq("Mes actual") & (filtered["_saldo"] > 0.01)]
                if not historical_unreconciled.empty:
                    st.markdown(
                        f"<div class='pp-alert pp-danger'>⚠️ Hay {len(historical_unreconciled)} cuotas de meses anteriores sin conciliación. "
                        "No se consideran automáticamente impagas: deben marcarse como Pagada, Parcial o Vencida.</div>",
                        unsafe_allow_html=True,
                    )
                if not current_due.empty:
                    st.markdown(
                        f"<div class='pp-alert'>📅 Este mes concentra {_pp_money(current_due[current_due['moneda'].eq('ARS')]['_saldo'].sum())} "
                        f"en {len(current_due)} cuotas.</div>",
                        unsafe_allow_html=True,
                    )
                if historical_unreconciled.empty and current_due.empty:
                    st.markdown(
                        "<div class='pp-alert pp-ok'>✅ No hay alertas de conciliación ni cuotas del mes dentro de la vista seleccionada.</div>",
                        unsafe_allow_html=True,
                    )

                left, right = st.columns([1.55, 1])
                with left:
                    monthly = filtered.groupby(["_periodo", "empresa"], as_index=False).agg(Saldo=("_saldo", "sum"))
                    fig_monthly = px.bar(
                        monthly,
                        x="_periodo",
                        y="Saldo",
                        color="empresa",
                        barmode="stack",
                        title="Compromiso mensual por empresa",
                        labels={"_periodo": "Mes", "Saldo": "Saldo programado"},
                    )
                    fig_monthly.update_layout(height=410, legend_title_text="Empresa", hovermode="x unified")
                    st.plotly_chart(fig_monthly, use_container_width=True, key=f"pp_monthly_chart_{table}")
                with right:
                    creditor = filtered.groupby("acreedor", as_index=False).agg(Saldo=("_saldo", "sum"))
                    creditor = creditor.sort_values("Saldo", ascending=False).head(10)
                    fig_creditor = px.bar(
                        creditor.sort_values("Saldo"),
                        x="Saldo",
                        y="acreedor",
                        orientation="h",
                        title="Exposición por acreedor",
                        labels={"acreedor": "Acreedor"},
                    )
                    fig_creditor.update_layout(height=410, showlegend=False)
                    st.plotly_chart(fig_creditor, use_container_width=True, key=f"pp_creditor_chart_{table}")

                st.markdown("#### Próximos 12 meses")
                horizon_end = current_month + pd.DateOffset(months=11)
                projection = filtered[
                    filtered["_periodo"].between(current_month, horizon_end, inclusive="both")
                ].groupby(["_periodo", "empresa"], as_index=False).agg(
                    Cuotas=("id_registro", "size"),
                    Importe=("_importe", "sum"),
                    Pagado=("_pagado", "sum"),
                    Saldo=("_saldo", "sum"),
                )
                if projection.empty:
                    st.info("No hay cuotas dentro de los próximos 12 meses para la selección actual.")
                else:
                    projection["Mes"] = projection["_periodo"].apply(_pp_month_label)
                    projection = projection[["Mes", "empresa", "Cuotas", "Importe", "Pagado", "Saldo"]]
                    st.dataframe(
                        projection,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "empresa": "Empresa",
                            "Importe": st.column_config.NumberColumn("Importe", format="$ %.2f"),
                            "Pagado": st.column_config.NumberColumn("Pagado", format="$ %.2f"),
                            "Saldo": st.column_config.NumberColumn("Saldo", format="$ %.2f"),
                        },
                    )

                st.markdown("#### Resumen por plan / préstamo")
                filtered_summary = _pp_plan_summary(filtered)
                if not filtered_summary.empty:
                    view = filtered_summary.rename(columns={
                        "hoja_origen": "Hoja",
                        "seccion": "Sección",
                        "registro_clase": "Clase",
                        "empresa": "Empresa",
                        "unidad": "Unidad",
                        "tipo_financiacion": "Tipo",
                        "acreedor": "Acreedor",
                        "identificador": "Identificador",
                        "moneda": "Moneda",
                    })
                    st.dataframe(
                        view,
                        use_container_width=True,
                        hide_index=True,
                        height=430,
                        column_config={
                            "Desde": st.column_config.DateColumn(format="MMM YYYY"),
                            "Hasta": st.column_config.DateColumn(format="MMM YYYY"),
                            "Total_cronograma": st.column_config.NumberColumn("Total cronograma", format="$ %.2f"),
                            "Pagado": st.column_config.NumberColumn(format="$ %.2f"),
                            "Saldo": st.column_config.NumberColumn(format="$ %.2f"),
                            "Total_declarado": st.column_config.NumberColumn("Total declarado", format="$ %.2f"),
                            "Cuotas_adeudadas": st.column_config.NumberColumn("Cuotas adeudadas", format="$ %.2f"),
                            "Monto_adelanto": st.column_config.NumberColumn("Adelanto", format="$ %.2f"),
                            "Intereses_ahorrados": st.column_config.NumberColumn("Intereses ahorrados", format="$ %.2f"),
                            "Diferencia_control": st.column_config.NumberColumn("Diferencia", format="$ %.2f"),
                            "Avance_%": st.column_config.ProgressColumn("Avance", min_value=0, max_value=100, format="%.1f%%"),
                        },
                    )

    # ---------------------------------------------------------------
    # CRONOGRAMA
    # ---------------------------------------------------------------
    with tabs[1]:
        if data.empty:
            st.info("No hay cronograma cargado.")
        else:
            q1, q2, q3 = st.columns([1.4, 1.4, 1.2])
            with q1:
                search = st.text_input(
                    "Buscar plan, acreedor o unidad",
                    placeholder="Ej.: ARCA, Macro, U941648, Reproductiva",
                    key=f"pp_schedule_search_{table}",
                )
            with q2:
                situation_options = sorted(data["_situacion"].dropna().unique().tolist())
                selected_situations = st.multiselect(
                    "Situación", situation_options, default=situation_options, key=f"pp_schedule_state_{table}"
                )
            with q3:
                only_balance = st.toggle("Sólo con saldo", value=False, key=f"pp_schedule_balance_{table}")

            class_options = [
                value for value in _PLANES_REGISTRO_CLASES
                if value in data["registro_clase"].dropna().astype(str).unique().tolist()
            ]
            selected_classes = st.multiselect(
                "Contenido a mostrar",
                class_options,
                default=class_options,
                key=f"pp_schedule_class_{table}",
                help="Cuota mensual, totales declarados, adelantos y saldos consolidados se mantienen separados.",
            )

            schedule = data.copy()
            if search.strip():
                needle = _pp_norm(search)
                text = schedule[["acreedor", "identificador", "unidad", "empresa", "hoja_origen"]].fillna("").astype(str).agg(" ".join, axis=1).apply(_pp_norm)
                schedule = schedule[text.str.contains(re.escape(needle), regex=True, na=False)]
            if selected_situations:
                schedule = schedule[schedule["_situacion"].isin(selected_situations)]
            if selected_classes:
                schedule = schedule[schedule["registro_clase"].isin(selected_classes)]
            if only_balance:
                schedule = schedule[schedule["_saldo"] > 0.01]

            schedule = schedule.sort_values(["_periodo", "empresa", "acreedor", "identificador"])
            view = schedule[[
                "hoja_origen", "registro_clase", "seccion", "empresa", "unidad",
                "tipo_financiacion", "acreedor", "identificador", "_periodo",
                "cuota_numero", "cuotas_totales", "_importe", "_pagado", "_saldo",
                "cuotas_adeudadas_monto", "monto_adelanto", "intereses_ahorrados",
                "moneda", "estado", "_situacion",
            ]].rename(columns={
                "hoja_origen": "Hoja", "registro_clase": "Clase", "seccion": "Sección",
                "empresa": "Empresa", "unidad": "Unidad", "tipo_financiacion": "Tipo",
                "acreedor": "Acreedor", "identificador": "Identificador", "_periodo": "Período",
                "cuota_numero": "Cuota", "cuotas_totales": "Total cuotas", "_importe": "Importe",
                "_pagado": "Pagado", "_saldo": "Saldo", "cuotas_adeudadas_monto": "Cuotas adeudadas",
                "monto_adelanto": "Adelanto", "intereses_ahorrados": "Intereses ahorrados",
                "moneda": "Moneda", "estado": "Estado", "_situacion": "Situación",
            })
            monthly_visible = schedule[schedule["registro_clase"].eq("Cuota mensual")]
            st.caption(
                f"{len(view)} registros visibles · {len(monthly_visible)} cuotas mensuales · "
                f"Saldo mensual ARS: {_pp_money(monthly_visible[monthly_visible['moneda'].eq('ARS')]['_saldo'].sum())}"
            )
            st.dataframe(
                view,
                use_container_width=True,
                hide_index=True,
                height=610,
                column_config={
                    "Período": st.column_config.DateColumn(format="MMM YYYY"),
                    "Importe": st.column_config.NumberColumn(format="$ %.2f"),
                    "Pagado": st.column_config.NumberColumn(format="$ %.2f"),
                    "Saldo": st.column_config.NumberColumn(format="$ %.2f"),
                    "Cuotas adeudadas": st.column_config.NumberColumn(format="$ %.2f"),
                    "Adelanto": st.column_config.NumberColumn(format="$ %.2f"),
                    "Intereses ahorrados": st.column_config.NumberColumn(format="$ %.2f"),
                },
            )

    # ---------------------------------------------------------------
    # CARGAR / PAGAR
    # ---------------------------------------------------------------
    with tabs[2]:
        st.subheader("Crear un plan o préstamo completo")
        with st.form(f"pp_new_plan_{table}", clear_on_submit=True):
            a1, a2, a3 = st.columns(3)
            with a1:
                company = st.selectbox("Empresa", ["VM", "VMR", "VITAE"], key=f"pp_new_company_{table}")
                unit = st.text_input("Unidad", value="Medical", key=f"pp_new_unit_{table}")
                financing_type = st.selectbox("Tipo", _PLANES_TIPOS, key=f"pp_new_type_{table}")
                creditor = st.text_input("Acreedor / banco / organismo", key=f"pp_new_creditor_{table}")
            with a2:
                identifier = st.text_input("N° de plan / préstamo", key=f"pp_new_identifier_{table}")
                currency = st.selectbox("Moneda", _PLANES_MONEDAS, key=f"pp_new_currency_{table}")
                capital = st.number_input("Capital / importe financiado", min_value=0.0, step=10000.0, key=f"pp_new_capital_{table}")
                installments = st.number_input("Cantidad de cuotas", min_value=1, max_value=240, value=12, step=1, key=f"pp_new_installments_{table}")
            with a3:
                monthly_rate = st.number_input("Tasa mensual %", min_value=0.0, value=0.0, step=0.1, key=f"pp_new_rate_{table}")
                first_month = st.date_input("Primer vencimiento", value=today.date(), key=f"pp_new_first_{table}")
                custom_installment = st.number_input(
                    "Cuota fija manual (opcional)", min_value=0.0, step=1000.0, key=f"pp_new_custom_{table}",
                    help="Si queda en cero, se calcula con el capital, la tasa y la cantidad de cuotas.",
                )
                section = st.selectbox(
                    "Sección", ["Deudas asumidas mes a mes", "Adelanto de cuotas", "Préstamos"], key=f"pp_new_section_{table}"
                )
            notes = st.text_area("Observaciones", key=f"pp_new_notes_{table}")
            create_plan = st.form_submit_button("💾 Crear cronograma y guardar", type="primary", use_container_width=True)

        if create_plan:
            if not creditor.strip() or not identifier.strip():
                st.error("Completá el acreedor y el número identificador del plan.")
            elif capital <= 0 and custom_installment <= 0:
                st.error("Ingresá el capital o una cuota fija manual.")
            else:
                n = int(installments)
                rate = float(monthly_rate) / 100.0
                if custom_installment > 0:
                    installment_value = float(custom_installment)
                    declared_total = installment_value * n
                elif rate > 0:
                    installment_value = float(capital) * rate * (1 + rate) ** n / ((1 + rate) ** n - 1)
                    declared_total = installment_value * n
                else:
                    installment_value = float(capital) / n
                    declared_total = float(capital)
                start = pd.Timestamp(first_month).replace(day=1)
                timestamp = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
                rows = []
                for number in range(1, n + 1):
                    period = start + pd.DateOffset(months=number - 1)
                    rows.append({
                        "hoja_origen": "Carga manual",
                        "seccion": section,
                        "registro_clase": "Cuota mensual",
                        "empresa": company,
                        "unidad": unit.strip() or company,
                        "tipo_financiacion": financing_type,
                        "acreedor": creditor.strip(),
                        "identificador": identifier.strip(),
                        "periodo": period,
                        "vencimiento": period + pd.offsets.MonthEnd(0),
                        "cuota_numero": number,
                        "cuotas_totales": n,
                        "importe_cuota": installment_value,
                        "pagado": 0.0,
                        "saldo": installment_value,
                        "moneda": currency,
                        "tasa_mensual": monthly_rate,
                        "capital_original": capital,
                        "cuotas_adeudadas_monto": 0.0,
                        "monto_adelanto": 0.0,
                        "intereses_ahorrados": 0.0,
                        "total_plan_declarado": declared_total,
                        "estado": "Programada",
                        "observaciones": notes,
                        "fila_origen": 0,
                        "columna_origen": "",
                        "fuente": "Carga manual",
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    })
                new_plan = _pp_prepare(pd.DataFrame(rows), source="Carga manual")
                try:
                    destination = _pp_merge(base, new_plan)
                    sync_df_to_sheet(table, _pp_prepare_sheet(destination))
                    st.cache_data.clear()
                    st.success(f"Plan creado correctamente: {n} cuotas de {_pp_money(installment_value, currency)}.")
                    st.rerun()
                except Exception as error:
                    st.error("No se pudo guardar el plan en Google Sheets.")
                    st.exception(error)

        st.divider()
        st.subheader("Registrar pago de una cuota")
        open_rows = data[
            data["registro_clase"].eq("Cuota mensual") & (data["_saldo"] > 0.01)
        ].copy() if not data.empty else pd.DataFrame()
        if open_rows.empty:
            st.info("No hay cuotas abiertas para registrar pagos.")
        else:
            open_rows["_label"] = open_rows.apply(
                lambda row: (
                    f"{row['empresa']} · {row['acreedor']} · {row['identificador']} · "
                    f"{_pp_month_label(row['_periodo'])} · {_pp_money(row['_saldo'], row['moneda'])}"
                ),
                axis=1,
            )
            selected_id = st.selectbox(
                "Cuota", open_rows["id_registro"].tolist(),
                format_func=lambda value: open_rows.set_index("id_registro").loc[value, "_label"],
                key=f"pp_payment_select_{table}",
            )
            selected = open_rows[open_rows["id_registro"].eq(selected_id)].iloc[0]
            p1, p2, p3 = st.columns(3)
            with p1:
                payment = st.number_input(
                    f"Importe pagado ({selected['moneda']})",
                    min_value=0.0,
                    max_value=float(selected["_saldo"]),
                    value=float(selected["_saldo"]),
                    step=1000.0 if selected["moneda"] == "ARS" else 10.0,
                    key=f"pp_payment_amount_{table}",
                )
            with p2:
                payment_date = st.date_input("Fecha del pago", value=today.date(), key=f"pp_payment_date_{table}")
            with p3:
                reference = st.text_input("Comprobante / referencia", key=f"pp_payment_reference_{table}")
            confirm = st.checkbox("Confirmo que el pago fue realizado", key=f"pp_payment_confirm_{table}")
            if st.button(
                "✅ Aplicar pago",
                type="primary",
                disabled=not confirm,
                use_container_width=True,
                key=f"pp_payment_button_{table}",
            ):
                if payment <= 0:
                    st.warning("Ingresá un importe mayor que cero.")
                else:
                    updated = _pp_prepare(base).copy()
                    mask = updated["id_registro"].eq(selected_id)
                    updated.loc[mask, "pagado"] = (
                        pd.to_numeric(updated.loc[mask, "pagado"], errors="coerce").fillna(0) + payment
                    )
                    updated.loc[mask, "saldo"] = (
                        pd.to_numeric(updated.loc[mask, "importe_cuota"], errors="coerce").fillna(0)
                        - pd.to_numeric(updated.loc[mask, "pagado"], errors="coerce").fillna(0)
                    ).clip(lower=0)
                    is_paid = float(updated.loc[mask, "saldo"].iloc[0]) <= 0.01
                    updated.loc[mask, "estado"] = "Pagada" if is_paid else "Parcial"
                    previous_notes = _pp_text(updated.loc[mask, "observaciones"].iloc[0])
                    log = f"Pago {pd.Timestamp(payment_date).strftime('%d/%m/%Y')}: {_pp_money(payment, selected['moneda'])}"
                    if reference.strip():
                        log += f" · {reference.strip()}"
                    updated.loc[mask, "observaciones"] = (previous_notes + " | " + log).strip(" |")
                    try:
                        sync_df_to_sheet(table, _pp_prepare_sheet(updated))
                        st.cache_data.clear()
                        st.success("Pago aplicado y saldo actualizado en Google Sheets.")
                        st.rerun()
                    except Exception as error:
                        st.error("No se pudo registrar el pago.")
                        st.exception(error)

    # ---------------------------------------------------------------
    # IMPORTAR EXCEL
    # ---------------------------------------------------------------
    with tabs[3]:
        st.subheader("Importar la planilla histórica sin perder su estructura")
        st.markdown(
            """
            <div class="pp-note">
            1) Elegí el Excel. 2) Seleccioná las hojas que querés procesar. 3) Revisá el cuadro original y los controles.
            4) Guardá: el cronograma normalizado queda en la hoja principal y la matriz original en una hoja auxiliar.
            </div>
            """,
            unsafe_allow_html=True,
        )
        uploaded = st.file_uploader(
            "Archivo Excel o CSV",
            type=["xlsx", "xlsm", "xls", "csv"],
            key=f"pp_uploader_{table}",
        )
        if uploaded is not None:
            from io import BytesIO

            file_bytes = uploaded.getvalue()
            filename = _pp_text(getattr(uploaded, "name", "archivo")).lower()
            matrices: dict[str, pd.DataFrame] = {}
            read_errors: list[str] = []
            if filename.endswith(".csv"):
                try:
                    try:
                        matrix = pd.read_csv(BytesIO(file_bytes), header=None, dtype=object, sep=None, engine="python")
                    except UnicodeDecodeError:
                        matrix = pd.read_csv(BytesIO(file_bytes), header=None, dtype=object, sep=None, engine="python", encoding="latin-1")
                    matrices["CSV"] = matrix
                except Exception as error:
                    read_errors.append(f"CSV: {error}")
            else:
                try:
                    excel = pd.ExcelFile(BytesIO(file_bytes))
                    for sheet_name in excel.sheet_names:
                        try:
                            matrices[sheet_name] = pd.read_excel(
                                BytesIO(file_bytes), sheet_name=sheet_name, header=None, dtype=object
                            ).fillna("")
                        except Exception as error:
                            read_errors.append(f"{sheet_name}: {error}")
                except Exception as error:
                    read_errors.append(str(error))

            if read_errors:
                for error in read_errors:
                    st.warning(f"No se pudo leer {error}")
            if matrices:
                all_sheets = list(matrices.keys())
                selected_import_sheets = st.multiselect(
                    "Hojas a importar",
                    all_sheets,
                    default=all_sheets,
                    key=f"pp_import_sheets_{table}",
                    help="Podés importar una sola hoja para aislar sus números o todas para consolidar.",
                )
                if selected_import_sheets:
                    preview_sheet = st.selectbox(
                        "Hoja para previsualizar", selected_import_sheets, key=f"pp_preview_sheet_{table}"
                    )
                    preview_matrix = matrices[preview_sheet].copy()
                    preview_matrix.columns = [_pp_excel_column(index) for index in range(len(preview_matrix.columns))]
                    preview_matrix.index = range(1, len(preview_matrix) + 1)
                    st.markdown(f"##### Vista original · {preview_sheet}")
                    st.dataframe(preview_matrix, use_container_width=True, height=390)

                    imported_frames: list[pd.DataFrame] = []
                    raw_frames: list[pd.DataFrame] = []
                    validation_frames: list[pd.DataFrame] = []
                    status_rows: list[dict[str, Any]] = []
                    for sheet_name in selected_import_sheets:
                        parsed, validation = _pp_parse_legacy_matrix(matrices[sheet_name], sheet_name)
                        imported_frames.append(parsed)
                        raw_frames.append(_pp_raw_long(matrices[sheet_name], sheet_name))
                        if not validation.empty:
                            validation_frames.append(validation)
                        parsed_monthly = parsed[parsed["registro_clase"].eq("Cuota mensual")] if not parsed.empty else pd.DataFrame()
                        status_rows.append({
                            "Hoja": sheet_name,
                            "Filas": len(matrices[sheet_name]),
                            "Columnas": len(matrices[sheet_name].columns),
                            "Cuotas detectadas": len(parsed_monthly),
                            "Planes detectados": parsed_monthly["identificador"].nunique() if not parsed_monthly.empty else 0,
                            "Resúmenes / adelantos": len(parsed) - len(parsed_monthly),
                            "Total mensual ARS": parsed_monthly.loc[parsed_monthly["moneda"].eq("ARS"), "importe_cuota"].sum() if not parsed_monthly.empty else 0.0,
                        })

                    imported = _pp_prepare(pd.concat(imported_frames, ignore_index=True)) if imported_frames else pd.DataFrame(columns=_PLANES_COLUMNS)
                    imported_raw = pd.concat(raw_frames, ignore_index=True) if raw_frames else pd.DataFrame()
                    validations = pd.concat(validation_frames, ignore_index=True) if validation_frames else pd.DataFrame()
                    st.markdown("##### Resultado de lectura")
                    st.dataframe(
                        pd.DataFrame(status_rows),
                        use_container_width=True,
                        hide_index=True,
                        column_config={"Total mensual ARS": st.column_config.NumberColumn(format="$ %.2f")},
                    )
                    if imported.empty:
                        st.error("No se detectaron cuotas mensuales. Revisá que la hoja tenga encabezados como jun-26, jul-26, etc.")
                    else:
                        imported_monthly = imported[imported["registro_clase"].eq("Cuota mensual")].copy()
                        i1, i2, i3, i4 = st.columns(4)
                        i1.metric("Cuotas mensuales", len(imported_monthly))
                        i2.metric("Planes con cronograma", imported_monthly["identificador"].nunique())
                        i3.metric("VM mensual", _pp_money(imported_monthly.loc[imported_monthly["empresa"].eq("VM") & imported_monthly["moneda"].eq("ARS"), "importe_cuota"].sum()))
                        i4.metric("VMR mensual", _pp_money(imported_monthly.loc[imported_monthly["empresa"].eq("VMR") & imported_monthly["moneda"].eq("ARS"), "importe_cuota"].sum()))
                        st.caption(
                            f"Además se detectaron {len(imported) - len(imported_monthly)} registros de control: "
                            "totales declarados, préstamos consolidados y simulaciones de adelanto."
                        )

                        if not validations.empty:
                            with st.expander("🔎 Control contra totales declarados", expanded=False):
                                st.dataframe(
                                    validations,
                                    use_container_width=True,
                                    hide_index=True,
                                    column_config={
                                        "Importe": st.column_config.NumberColumn(format="$ %.2f"),
                                        "Total declarado": st.column_config.NumberColumn(format="$ %.2f"),
                                        "Diferencia": st.column_config.NumberColumn(format="$ %.2f"),
                                    },
                                )

                        mode = st.radio(
                            "Modo de guardado",
                            ["Reemplazar únicamente las hojas seleccionadas", "Agregar / actualizar sin borrar otras hojas"],
                            key=f"pp_import_mode_{table}",
                        )
                        confirm_import = st.checkbox(
                            f"Confirmo guardar {len(imported)} registros ({len(imported_monthly)} cuotas mensuales) en Google Sheets",
                            key=f"pp_import_confirm_{table}",
                        )
                        if st.button(
                            "💾 Guardar Excel en Google Sheets",
                            type="primary",
                            disabled=not confirm_import,
                            use_container_width=True,
                            key=f"pp_import_save_{table}",
                        ):
                            try:
                                # Backup automático de la hoja principal anterior.
                                if df_original is not None and (not df_original.empty or len(df_original.columns) > 0):
                                    try:
                                        sync_df_to_sheet(_pp_aux_table(table, "backup"), df_original.copy())
                                    except Exception:
                                        pass

                                replace = selected_import_sheets if mode.startswith("Reemplazar") else None
                                destination = _pp_merge(base, imported, replace_sheets=replace)
                                sync_df_to_sheet(table, _pp_prepare_sheet(destination))

                                raw_table = _pp_aux_table(table, "matriz")
                                existing_raw = raw_long.copy()
                                if replace and not existing_raw.empty:
                                    existing_raw = existing_raw[
                                        ~existing_raw["hoja_origen"].astype(str).isin(selected_import_sheets)
                                    ]
                                combined_raw = pd.concat([existing_raw, imported_raw], ignore_index=True)
                                combined_raw = combined_raw.drop_duplicates(
                                    subset=["hoja_origen", "fila", "columna"], keep="last"
                                )
                                sync_df_to_sheet(raw_table, combined_raw)
                                st.cache_data.clear()
                                st.success(
                                    "Importación completa: se guardó el cronograma normalizado y la vista original por hoja."
                                )
                                st.rerun()
                            except Exception as error:
                                st.error("No se pudo guardar la importación en Google Sheets.")
                                st.exception(error)

    # ---------------------------------------------------------------
    # GESTIONAR
    # ---------------------------------------------------------------
    with tabs[4]:
        if data.empty:
            st.info("No hay registros para editar.")
        else:
            st.subheader("Editar cronograma normalizado")
            st.caption("Los cambios se guardan en la hoja principal. La matriz original queda intacta como respaldo visual.")
            editable = _pp_prepare(base).copy()
            editable["periodo"] = pd.to_datetime(editable["periodo"], errors="coerce").dt.date
            editable["vencimiento"] = pd.to_datetime(editable["vencimiento"], errors="coerce").dt.date
            edited = st.data_editor(
                editable,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                height=610,
                key=f"pp_editor_{table}",
                column_config={
                    "id_registro": st.column_config.TextColumn("ID", disabled=True),
                    "registro_clase": st.column_config.SelectboxColumn("Clase", options=_PLANES_REGISTRO_CLASES),
                    "empresa": st.column_config.SelectboxColumn("Empresa", options=["VM", "VMR", "VITAE"]),
                    "tipo_financiacion": st.column_config.SelectboxColumn("Tipo", options=_PLANES_TIPOS),
                    "moneda": st.column_config.SelectboxColumn("Moneda", options=_PLANES_MONEDAS),
                    "estado": st.column_config.SelectboxColumn("Estado", options=_PLANES_ESTADOS),
                    "periodo": st.column_config.DateColumn("Período", format="MMM YYYY"),
                    "vencimiento": st.column_config.DateColumn("Vencimiento", format="DD/MM/YYYY"),
                    "importe_cuota": st.column_config.NumberColumn("Importe cuota", format="$ %.2f"),
                    "pagado": st.column_config.NumberColumn("Pagado", format="$ %.2f"),
                    "saldo": st.column_config.NumberColumn("Saldo", format="$ %.2f"),
                    "cuotas_adeudadas_monto": st.column_config.NumberColumn("Cuotas adeudadas", format="$ %.2f"),
                    "monto_adelanto": st.column_config.NumberColumn("Adelanto", format="$ %.2f"),
                    "intereses_ahorrados": st.column_config.NumberColumn("Intereses ahorrados", format="$ %.2f"),
                },
            )
            confirm_edit = st.checkbox("Confirmo guardar la edición", key=f"pp_edit_confirm_{table}")
            if st.button(
                "💾 Guardar cambios",
                type="primary",
                disabled=not confirm_edit,
                use_container_width=True,
                key=f"pp_edit_save_{table}",
            ):
                try:
                    sync_df_to_sheet(table, _pp_prepare_sheet(edited))
                    st.cache_data.clear()
                    st.success(f"Cambios guardados. Registros procesados: {len(edited)}")
                    st.rerun()
                except Exception as error:
                    st.error("No se pudieron guardar los cambios.")
                    st.exception(error)

    # ---------------------------------------------------------------
    # VISTA ORIGINAL
    # ---------------------------------------------------------------
    with tabs[5]:
        st.subheader("Cuadro original por hoja")
        if raw_long is not None and not raw_long.empty:
            original_sheets = sorted(raw_long["hoja_origen"].dropna().astype(str).unique().tolist())
            chosen_original = st.selectbox(
                "Hoja", original_sheets, key=f"pp_original_sheet_{table}"
            )
            original_matrix = _pp_raw_matrix(raw_long, chosen_original)
            st.caption(
                "Esta vista se reconstruye desde la hoja auxiliar de Google Sheets y conserva la posición de cada celda importada."
            )
            st.dataframe(original_matrix, use_container_width=True, height=650)
        elif not fallback_matrix.empty:
            display_fallback = fallback_matrix.copy()
            display_fallback.columns = [_pp_excel_column(index) for index in range(len(display_fallback.columns))]
            display_fallback.index = range(1, len(display_fallback) + 1)
            st.warning(
                "La hoja principal todavía está en formato horizontal. Importala desde la pestaña Importar Excel para guardar también la matriz original permanente."
            )
            st.dataframe(display_fallback, use_container_width=True, height=650)
            if not validation_from_sheet.empty:
                with st.expander("Control de lectura"):
                    st.dataframe(validation_from_sheet, use_container_width=True, hide_index=True)
        else:
            st.info("Todavía no hay una matriz original guardada. Importá el Excel para habilitar esta vista.")

    # ---------------------------------------------------------------
    # IA
    # ---------------------------------------------------------------
    with tabs[6]:
        st.subheader("Analista financiero IA")
        st.caption("La IA analiza únicamente el cronograma visible; no modifica Google Sheets.")
        question = st.text_area(
            "Pregunta",
            placeholder=(
                "Ej.: ¿Cuánto debemos pagar en los próximos 6 meses? "
                "¿Qué acreedor concentra más deuda? ¿Cuándo termina cada plan?"
            ),
            height=110,
            key=f"pp_ai_question_{table}",
        )
        if st.button("🧠 Analizar", type="primary", key=f"pp_ai_button_{table}"):
            if not question.strip():
                st.warning("Escribí una pregunta.")
            elif data.empty:
                st.warning("No hay datos para analizar.")
            else:
                ai_frame = data[_PLANES_COLUMNS].copy()
                ai_frame["periodo"] = pd.to_datetime(ai_frame["periodo"], errors="coerce").dt.strftime("%Y-%m")
                ai_frame["vencimiento"] = pd.to_datetime(ai_frame["vencimiento"], errors="coerce").dt.strftime("%Y-%m-%d")
                if len(ai_frame) > 3000:
                    ai_frame = ai_frame.head(3000)
                with st.spinner("Analizando cuotas, saldos y vencimientos..."):
                    try:
                        answer = preguntar_ia(modulo=module_name, df=ai_frame, pregunta=question)
                        st.success(answer)
                    except Exception as error:
                        st.error(f"No se pudo consultar la IA: {error}")

    # ---------------------------------------------------------------
    # EXPORTAR
    # ---------------------------------------------------------------
    with tabs[7]:
        st.subheader("Exportación ejecutiva")
        if data.empty:
            st.info("No hay datos para exportar.")
        else:
            excel_bytes = _pp_export_excel(base, raw_long)
            csv_bytes = _pp_prepare_sheet(base).to_csv(index=False).encode("utf-8-sig")
            e1, e2 = st.columns(2)
            with e1:
                st.download_button(
                    "📗 Descargar Excel completo",
                    data=excel_bytes,
                    file_name=f"planes_pagos_prestamos_{date.today().isoformat()}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key=f"pp_export_xlsx_{table}",
                )
            with e2:
                st.download_button(
                    "📄 Descargar CSV normalizado",
                    data=csv_bytes,
                    file_name=f"planes_pagos_prestamos_{date.today().isoformat()}.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key=f"pp_export_csv_{table}",
                )
            st.success(
                "El Excel incluye cronograma normalizado, resumen por plan, flujo mensual y las hojas originales disponibles."
            )



# =========================================================
# GINE VITAE · PLANES + CAJA · MÓDULO PRO
# =========================================================
_GV_PATIENT_COLUMNS = [
    "id_registro", "numero", "paciente", "efectivo", "mercado_pago",
    "total_cobrado", "factura", "fecha_plan", "autorizado", "entregado",
    "observaciones", "estado_operativo", "hoja_origen", "fila_origen",
    "archivo_origen", "saldo_movimiento",
]
_GV_CASH_COLUMNS = [
    "id_movimiento", "fecha", "ingreso", "egreso", "concepto", "neto",
    "hoja_origen", "fila_origen", "archivo_origen",
]
_GV_SUMMARY_COLUMNS = [
    "hoja_origen", "caja_declarada", "cuenta_declarada", "ingresos",
    "egresos", "saldo_movimientos", "total_planes", "pacientes",
    "archivo_origen",
]
_GV_RAW_COLUMNS = ["hoja_origen", "fila", "columna", "valor"]


def _gv_norm(value: Any) -> str:
    import unicodedata

    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii").lower().strip()
    text = text.replace("º", "").replace("°", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _gv_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    if isinstance(value, pd.Timestamp):
        return value.strftime("%d/%m/%Y")
    text = str(value).strip()
    return default if text.lower() in {"nan", "none", "nat", "null"} else text


def _gv_number(value: Any) -> float:
    import numbers

    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, numbers.Number):
        try:
            if pd.isna(value):
                return 0.0
        except Exception:
            pass
        return float(value)
    text = _gv_text(value)
    if not text or text in {"-", "—", "–"}:
        return 0.0
    negative = text.startswith("(") and text.endswith(")")
    text = (
        text.replace("ARS", "")
        .replace("US$", "")
        .replace("USD", "")
        .replace("$", "")
        .replace("%", "")
        .replace(" ", "")
        .replace("(", "")
        .replace(")", "")
    )
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        decimals = len(text.rsplit(",", 1)[-1])
        text = text.replace(".", "")
        text = text.replace(",", "." if decimals <= 2 else "")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    text = re.sub(r"[^0-9.\-]", "", text)
    try:
        number = float(text)
        return -number if negative else number
    except (TypeError, ValueError):
        return 0.0


def _gv_date(value: Any) -> pd.Timestamp:
    """Convierte fechas de Excel, Sheets e ingreso manual sin invertir mes/día.

    Google Sheets guarda las fechas normalizadas como AAAA-MM-DD. Esas fechas
    deben interpretarse con año primero. Los textos argentinos DD/MM/AAAA se
    interpretan recién después con ``dayfirst=True``.
    """
    if value is None or _gv_text(value) in {"", "-", "—", "–"}:
        return pd.NaT
    if isinstance(value, (pd.Timestamp, date)):
        return pd.Timestamp(value).normalize()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if 20000 <= number <= 80000:
            try:
                return (pd.Timestamp("1899-12-30") + pd.to_timedelta(number, unit="D")).normalize()
            except Exception:
                pass

    text = _gv_text(value)

    # Formato persistido por el módulo/Google Sheets: AAAA-MM-DD.
    # Nunca debe procesarse con dayfirst=True porque 2026-05-09 terminaría
    # convertido incorrectamente en 05/09/2026.
    if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:$|[ T])", text):
        parsed = pd.to_datetime(text, errors="coerce", yearfirst=True, dayfirst=False)
    else:
        # Formato visible y habitual en Argentina: DD/MM/AAAA.
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)

    if pd.isna(parsed):
        return pd.NaT
    return pd.Timestamp(parsed).normalize()


def _gv_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        if float(value) == 1:
            return True
        if float(value) == 0:
            return False
    text = _gv_norm(value)
    if text in {"si", "s", "true", "verdadero", "ok", "x", "hecho", "entregado", "autorizado", "facturado", "1"}:
        return True
    if text in {"no", "n", "false", "falso", "pendiente", "0"}:
        return False
    return None


def _gv_excel_column(index: int) -> str:
    number = int(index) + 1
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _gv_trim_matrix(matrix: pd.DataFrame | None) -> pd.DataFrame:
    if matrix is None or matrix.empty:
        return pd.DataFrame()
    raw = matrix.copy()
    # Recorta únicamente el exceso final generado por formato de Excel. Mantiene
    # filas y columnas vacías internas para conservar coordenadas exactas.
    row_mask = raw.notna().any(axis=1)
    col_mask = raw.notna().any(axis=0)
    if not row_mask.any() or not col_mask.any():
        return pd.DataFrame()
    last_row = max(position for position, present in enumerate(row_mask.tolist()) if present)
    last_col = max(position for position, present in enumerate(col_mask.tolist()) if present)
    return raw.iloc[: last_row + 1, : last_col + 1].reset_index(drop=True)


def _gv_find_header(raw: pd.DataFrame, kind: str) -> int | None:
    if raw is None or raw.empty:
        return None
    limit = min(len(raw), 120)
    best_index, best_score = None, -1
    for row_index in range(limit):
        values = [_gv_norm(value) for value in raw.iloc[row_index].tolist()]
        tokens = set(values)
        if kind == "patients":
            score = 0
            score += 6 if any(value in {"paciente", "pacientes"} for value in values) else 0
            score += 2 if "efectivo" in tokens else 0
            score += 2 if any(value in {"mp", "mercado pago"} for value in values) else 0
            score += 2 if "autorizado" in tokens else 0
            score += 2 if "entregado" in tokens else 0
            score += 1 if "factura" in tokens else 0
        else:
            score = 0
            score += 4 if "ingreso" in tokens else 0
            score += 4 if "egreso" in tokens else 0
            score += 3 if "concepto" in tokens else 0
            score += 1 if "fecha" in tokens else 0
            score += 1 if "total" in tokens else 0
        if score > best_score:
            best_index, best_score = row_index, score
    threshold = 6 if kind == "patients" else 8
    return best_index if best_score >= threshold else None


def _gv_find_column(headers: list[str], aliases: list[str], start: int = 0, end: int | None = None) -> int | None:
    if end is None:
        end = len(headers)
    alias_norms = [_gv_norm(alias) for alias in aliases]
    for index in range(max(0, start), min(len(headers), end)):
        if headers[index] in alias_norms:
            return index
    for index in range(max(0, start), min(len(headers), end)):
        if any(alias and alias in headers[index] for alias in alias_norms):
            return index
    return None


def _gv_row_value(raw: pd.DataFrame, row_index: int, col_index: int | None) -> Any:
    if col_index is None or row_index < 0 or row_index >= len(raw) or col_index >= len(raw.columns):
        return None
    return raw.iat[row_index, col_index]


def _gv_make_id(*parts: Any, prefix: str = "GV") -> str:
    import hashlib

    payload = "|".join(_gv_norm(part) or _gv_text(part) for part in parts)
    digest = hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:14]
    return f"{prefix}-{digest}"


def _gv_status(row: pd.Series) -> str:
    observation = _gv_norm(row.get("observaciones", ""))
    if any(token in observation for token in ["ausente", "no asistio", "no se realizo"]):
        return "Ausente / no realizado"
    if any(token in observation for token in ["cancelado", "cancelada", "anulado", "anulada"]):
        return "Cancelado"
    authorized = _gv_bool(row.get("autorizado")) is True
    delivered = _gv_bool(row.get("entregado")) is True
    invoiced = _gv_bool(row.get("factura")) is True
    if authorized and delivered and invoiced:
        return "Completo"
    if not authorized:
        return "Pendiente autorización"
    if authorized and not delivered:
        return "Autorizado · pendiente entrega"
    if delivered and not invoiced:
        return "Entregado · pendiente factura"
    return "En gestión"


def _gv_prepare_patients(df: pd.DataFrame | None, source: str = "Google Sheets") -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=_GV_PATIENT_COLUMNS)
    work = df.copy()
    work = work.loc[:, ~work.columns.duplicated()].copy()
    normalized = {_gv_norm(column).replace(" ", "_"): column for column in work.columns}

    def series(names: list[str], default: Any = "") -> pd.Series:
        for name in names:
            key = _gv_norm(name).replace(" ", "_")
            if key in normalized:
                return work[normalized[key]]
        return pd.Series(default, index=work.index)

    patient_series = series(["paciente", "pacientes", "nombre", "nombre_paciente"])
    if patient_series.apply(_gv_text).eq("").all():
        return pd.DataFrame(columns=_GV_PATIENT_COLUMNS)

    result = pd.DataFrame(index=work.index)
    result["numero"] = pd.to_numeric(series(["numero", "n", "nro", "n_paciente"]), errors="coerce")
    result["paciente"] = patient_series.apply(_gv_text)
    result["efectivo"] = series(["efectivo", "pago_efectivo"]).apply(_gv_number)
    result["mercado_pago"] = series(["mercado_pago", "mp", "transferencia", "pago_mp"]).apply(_gv_number)
    explicit_total = series(["total_cobrado", "total", "importe", "ingreso"]).apply(_gv_number)
    calculated_total = result["efectivo"] + result["mercado_pago"]
    result["total_cobrado"] = calculated_total.where(calculated_total.abs() > 0.001, explicit_total)
    result["factura"] = series(["factura", "facturado"]).apply(_gv_bool)
    result["fecha_plan"] = series(["fecha_plan", "fecha", "fecha_campana", "fecha_jornada"]).apply(_gv_date)
    result["autorizado"] = series(["autorizado", "autorizacion"]).apply(_gv_bool)
    result["entregado"] = series(["entregado", "entrega"]).apply(_gv_bool)
    result["observaciones"] = series(["observaciones", "observacion", "notas"]).apply(_gv_text)
    result["hoja_origen"] = series(["hoja_origen", "hoja", "sheet"], source).apply(lambda value: _gv_text(value, source))
    result["fila_origen"] = pd.to_numeric(series(["fila_origen", "fila"]), errors="coerce")
    result["archivo_origen"] = series(["archivo_origen", "archivo"], source).apply(lambda value: _gv_text(value, source))
    result["saldo_movimiento"] = series(["saldo_movimiento", "posicion_financiera"], 0).apply(_gv_number)
    existing_ids = series(["id_registro", "id", "registro_id"]).apply(_gv_text)
    result["id_registro"] = [
        existing_ids.iloc[pos] or _gv_make_id(
            result.iloc[pos]["hoja_origen"],
            result.iloc[pos]["fila_origen"],
            result.iloc[pos]["numero"],
            result.iloc[pos]["paciente"],
            result.iloc[pos]["fecha_plan"],
            prefix="GVP",
        )
        for pos in range(len(result))
    ]
    result = result[result["paciente"].ne("")].copy()
    result["numero"] = result["numero"].where(result["numero"].notna(), range(1, len(result) + 1))
    result["numero"] = pd.to_numeric(result["numero"], errors="coerce").fillna(0).astype(int)
    result["estado_operativo"] = result.apply(_gv_status, axis=1)
    result = result.drop_duplicates(subset=["id_registro"], keep="last").reset_index(drop=True)
    for column in _GV_PATIENT_COLUMNS:
        if column not in result.columns:
            result[column] = ""
    return result[_GV_PATIENT_COLUMNS]


def _gv_prepare_cash(df: pd.DataFrame | None, source: str = "Google Sheets") -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=_GV_CASH_COLUMNS)
    work = df.copy()
    work = work.loc[:, ~work.columns.duplicated()].copy()
    normalized = {_gv_norm(column).replace(" ", "_"): column for column in work.columns}

    def series(names: list[str], default: Any = "") -> pd.Series:
        for name in names:
            key = _gv_norm(name).replace(" ", "_")
            if key in normalized:
                return work[normalized[key]]
        return pd.Series(default, index=work.index)

    result = pd.DataFrame(index=work.index)
    result["fecha"] = series(["fecha", "date"]).apply(_gv_date)
    result["ingreso"] = series(["ingreso", "entradas"]).apply(_gv_number)
    result["egreso"] = series(["egreso", "salidas"]).apply(_gv_number)
    result["concepto"] = series(["concepto", "detalle", "descripcion"]).apply(_gv_text)
    result["neto"] = result["ingreso"] - result["egreso"]
    result["hoja_origen"] = series(["hoja_origen", "hoja", "sheet"], source).apply(lambda value: _gv_text(value, source))
    result["fila_origen"] = pd.to_numeric(series(["fila_origen", "fila"]), errors="coerce")
    result["archivo_origen"] = series(["archivo_origen", "archivo"], source).apply(lambda value: _gv_text(value, source))
    existing_ids = series(["id_movimiento", "id", "registro_id"]).apply(_gv_text)
    result["id_movimiento"] = [
        existing_ids.iloc[pos] or _gv_make_id(
            result.iloc[pos]["hoja_origen"],
            result.iloc[pos]["fila_origen"],
            result.iloc[pos]["fecha"],
            result.iloc[pos]["concepto"],
            result.iloc[pos]["ingreso"],
            result.iloc[pos]["egreso"],
            prefix="GVC",
        )
        for pos in range(len(result))
    ]
    useful = (
        result["fecha"].notna()
        | result["concepto"].ne("")
        | result["ingreso"].abs().gt(0.001)
        | result["egreso"].abs().gt(0.001)
    )
    result = result[useful].copy()
    result = result.drop_duplicates(subset=["id_movimiento"], keep="last").reset_index(drop=True)
    for column in _GV_CASH_COLUMNS:
        if column not in result.columns:
            result[column] = ""
    return result[_GV_CASH_COLUMNS]


def _gv_prepare_summary(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=_GV_SUMMARY_COLUMNS)
    work = df.copy()
    work = work.loc[:, ~work.columns.duplicated()].copy()
    normalized = {_gv_norm(column).replace(" ", "_"): column for column in work.columns}

    def series(names: list[str], default: Any = "") -> pd.Series:
        for name in names:
            key = _gv_norm(name).replace(" ", "_")
            if key in normalized:
                return work[normalized[key]]
        return pd.Series(default, index=work.index)

    result = pd.DataFrame(index=work.index)
    result["hoja_origen"] = series(["hoja_origen", "hoja", "sheet"], "GineVitae").apply(lambda value: _gv_text(value, "GineVitae"))
    for column, aliases in {
        "caja_declarada": ["caja_declarada", "caja"],
        "cuenta_declarada": ["cuenta_declarada", "cuenta"],
        "ingresos": ["ingresos", "total_ingresos"],
        "egresos": ["egresos", "total_egresos"],
        "saldo_movimientos": ["saldo_movimientos", "saldo", "total"],
        "total_planes": ["total_planes", "planes", "total_cobrado"],
        "pacientes": ["pacientes", "cantidad_pacientes"],
    }.items():
        result[column] = series(aliases, 0).apply(_gv_number)
    result["archivo_origen"] = series(["archivo_origen", "archivo"], "Google Sheets").apply(_gv_text)
    result = result.drop_duplicates(subset=["hoja_origen"], keep="last").reset_index(drop=True)
    for column in _GV_SUMMARY_COLUMNS:
        if column not in result.columns:
            result[column] = ""
    return result[_GV_SUMMARY_COLUMNS]


def _gv_parse_matrix(
    matrix: pd.DataFrame | None,
    sheet_name: str = "GineVitae",
    filename: str = "Excel",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    raw = _gv_trim_matrix(matrix)
    if raw.empty:
        return (
            pd.DataFrame(columns=_GV_PATIENT_COLUMNS),
            pd.DataFrame(columns=_GV_CASH_COLUMNS),
            pd.DataFrame(columns=_GV_SUMMARY_COLUMNS),
        )

    patient_header = _gv_find_header(raw, "patients")
    cash_header = _gv_find_header(raw, "cash")
    patient_rows: list[dict[str, Any]] = []
    cash_rows: list[dict[str, Any]] = []

    patient_columns: dict[str, int | None] = {}
    last_patient_row = None
    if patient_header is not None:
        headers = [_gv_norm(value) for value in raw.iloc[patient_header].tolist()]
        patient_col = _gv_find_column(headers, ["paciente", "pacientes"])
        patient_columns = {
            "numero": _gv_find_column(headers, ["n", "nro", "numero", "n paciente"], 0, patient_col if patient_col is not None else len(headers)),
            "paciente": patient_col,
            "efectivo": _gv_find_column(headers, ["efectivo"], (patient_col or 0) + 1),
            "mercado_pago": _gv_find_column(headers, ["mp", "mercado pago", "mercadopago"], (patient_col or 0) + 1),
            "factura": _gv_find_column(headers, ["factura", "facturado"], (patient_col or 0) + 1),
            "fecha_plan": _gv_find_column(headers, ["fecha", "fecha plan", "fecha jornada", "fecha campana"], (patient_col or 0) + 1),
            "autorizado": _gv_find_column(headers, ["autorizado", "autorizacion"], (patient_col or 0) + 1),
            "entregado": _gv_find_column(headers, ["entregado", "entrega"], (patient_col or 0) + 1),
            "observaciones": _gv_find_column(headers, ["observaciones", "observacion", "notas"], (patient_col or 0) + 1),
        }
        for row_index in range(patient_header + 1, len(raw)):
            patient = _gv_text(_gv_row_value(raw, row_index, patient_columns["paciente"]))
            if not patient:
                continue
            last_patient_row = row_index
            effective = _gv_number(_gv_row_value(raw, row_index, patient_columns["efectivo"]))
            mp = _gv_number(_gv_row_value(raw, row_index, patient_columns["mercado_pago"]))
            row = {
                "numero": _gv_number(_gv_row_value(raw, row_index, patient_columns["numero"])),
                "paciente": patient,
                "efectivo": effective,
                "mercado_pago": mp,
                "total_cobrado": effective + mp,
                "factura": _gv_bool(_gv_row_value(raw, row_index, patient_columns["factura"])),
                "fecha_plan": _gv_date(_gv_row_value(raw, row_index, patient_columns["fecha_plan"])),
                "autorizado": _gv_bool(_gv_row_value(raw, row_index, patient_columns["autorizado"])),
                "entregado": _gv_bool(_gv_row_value(raw, row_index, patient_columns["entregado"])),
                "observaciones": _gv_text(_gv_row_value(raw, row_index, patient_columns["observaciones"])),
                "hoja_origen": sheet_name,
                "fila_origen": row_index + 1,
                "archivo_origen": filename,
                "saldo_movimiento": 0.0,
            }
            row["id_registro"] = _gv_make_id(
                sheet_name, row_index + 1, row["numero"], patient, row["fecha_plan"], prefix="GVP"
            )
            row["estado_operativo"] = _gv_status(pd.Series(row))
            patient_rows.append(row)

    if cash_header is not None:
        headers = [_gv_norm(value) for value in raw.iloc[cash_header].tolist()]
        ingress_col = _gv_find_column(headers, ["ingreso"])
        cash_columns = {
            "fecha": _gv_find_column(headers, ["fecha"], 0, ingress_col if ingress_col is not None else len(headers)),
            "ingreso": ingress_col,
            "egreso": _gv_find_column(headers, ["egreso"]),
            "concepto": _gv_find_column(headers, ["concepto", "detalle", "descripcion"]),
            "total": _gv_find_column(headers, ["total"]),
        }
        for row_index in range(cash_header + 1, len(raw)):
            movement_date = _gv_date(_gv_row_value(raw, row_index, cash_columns["fecha"]))
            income = _gv_number(_gv_row_value(raw, row_index, cash_columns["ingreso"]))
            expense = _gv_number(_gv_row_value(raw, row_index, cash_columns["egreso"]))
            concept = _gv_text(_gv_row_value(raw, row_index, cash_columns["concepto"]))
            if pd.isna(movement_date) and not concept and abs(income) <= 0.001 and abs(expense) <= 0.001:
                continue
            row = {
                "fecha": movement_date,
                "ingreso": income,
                "egreso": expense,
                "concepto": concept,
                "neto": income - expense,
                "hoja_origen": sheet_name,
                "fila_origen": row_index + 1,
                "archivo_origen": filename,
            }
            row["id_movimiento"] = _gv_make_id(
                sheet_name, row_index + 1, movement_date, concept, income, expense, prefix="GVC"
            )
            cash_rows.append(row)

    patients = _gv_prepare_patients(pd.DataFrame(patient_rows), source=sheet_name)
    cash = _gv_prepare_cash(pd.DataFrame(cash_rows), source=sheet_name)

    def label_value(label: str) -> float:
        for row_index in range(min(len(raw), 20)):
            for col_index in range(len(raw.columns)):
                if _gv_norm(raw.iat[row_index, col_index]) == _gv_norm(label):
                    for offset in range(1, 4):
                        if col_index + offset < len(raw.columns):
                            candidate = raw.iat[row_index, col_index + offset]
                            if _gv_text(candidate) not in {"", "-", "—", "–"}:
                                return _gv_number(candidate)
        return 0.0

    total_income = float(cash["ingreso"].sum()) if not cash.empty else 0.0
    total_expense = float(cash["egreso"].sum()) if not cash.empty else 0.0
    net = total_income - total_expense
    declared_cash = label_value("CAJA")
    declared_account = label_value("CUENTA")
    if abs(declared_account) <= 0.001 and (abs(net) > 0.001 or abs(declared_cash) > 0.001):
        declared_account = net - declared_cash

    declared_plan_total = 0.0
    if patient_header is not None and patient_columns:
        cash_cols = [patient_columns.get("efectivo"), patient_columns.get("mercado_pago")]
        start_row = (last_patient_row + 1) if last_patient_row is not None else patient_header + 1
        candidates: list[float] = []
        for row_index in range(start_row, len(raw)):
            patient = _gv_text(_gv_row_value(raw, row_index, patient_columns.get("paciente")))
            if patient:
                continue
            for col_index in cash_cols:
                value = _gv_number(_gv_row_value(raw, row_index, col_index))
                if abs(value) > 0.001:
                    candidates.append(value)
        if candidates:
            declared_plan_total = max(candidates, key=abs)
    calculated_plan_total = float(patients["total_cobrado"].sum()) if not patients.empty else 0.0
    if abs(declared_plan_total) <= 0.001:
        declared_plan_total = calculated_plan_total

    summary = pd.DataFrame([{
        "hoja_origen": sheet_name,
        "caja_declarada": declared_cash,
        "cuenta_declarada": declared_account,
        "ingresos": total_income,
        "egresos": total_expense,
        "saldo_movimientos": net,
        "total_planes": declared_plan_total,
        "pacientes": len(patients),
        "archivo_origen": filename,
    }], columns=_GV_SUMMARY_COLUMNS)
    return patients, cash, _gv_prepare_summary(summary)


def _gv_matrix_from_dataframe(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or (df.empty and len(df.columns) == 0):
        return pd.DataFrame()
    frame = df.copy()
    header = pd.DataFrame([list(frame.columns)], columns=range(len(frame.columns)))
    frame.columns = range(len(frame.columns))
    return _gv_trim_matrix(pd.concat([header, frame], ignore_index=True))


def _gv_raw_long(matrix: pd.DataFrame | None, sheet_name: str) -> pd.DataFrame:
    raw = _gv_trim_matrix(matrix)
    if raw.empty:
        return pd.DataFrame(columns=_GV_RAW_COLUMNS)
    raw.columns = range(len(raw.columns))
    rows: list[dict[str, Any]] = []
    for row_index in range(len(raw)):
        for col_index in range(len(raw.columns)):
            value = raw.iat[row_index, col_index]
            if value is None:
                continue
            try:
                if pd.isna(value):
                    continue
            except Exception:
                pass
            if isinstance(value, (pd.Timestamp, date)):
                text = pd.Timestamp(value).strftime("%d/%m/%Y")
            elif isinstance(value, bool):
                text = "VERDADERO" if value else "FALSO"
            else:
                text = _gv_text(value)
            if text == "":
                continue
            rows.append({
                "hoja_origen": sheet_name,
                "fila": row_index + 1,
                "columna": col_index + 1,
                "valor": text,
            })
    return pd.DataFrame(rows, columns=_GV_RAW_COLUMNS)


def _gv_raw_matrix(raw_long: pd.DataFrame | None, sheet_name: str) -> pd.DataFrame:
    if raw_long is None or raw_long.empty:
        return pd.DataFrame()
    data = raw_long[raw_long["hoja_origen"].astype(str).eq(str(sheet_name))].copy()
    if data.empty:
        return pd.DataFrame()
    data["fila"] = pd.to_numeric(data["fila"], errors="coerce").fillna(0).astype(int)
    data["columna"] = pd.to_numeric(data["columna"], errors="coerce").fillna(0).astype(int)
    max_row = int(data["fila"].max())
    max_col = int(data["columna"].max())
    matrix = pd.DataFrame("", index=range(max_row), columns=range(max_col))
    for _, row in data.iterrows():
        r, c = int(row["fila"]) - 1, int(row["columna"]) - 1
        if 0 <= r < max_row and 0 <= c < max_col:
            matrix.iat[r, c] = _gv_text(row["valor"])
    matrix.columns = [_gv_excel_column(index) for index in range(max_col)]
    matrix.index = range(1, max_row + 1)
    return matrix


def _gv_aux_table(table: str, suffix: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_]+", "_", str(table)).strip("_") or "gine_vitae"
    return f"{clean[:80]}_{suffix}"[:98]


def _gv_load_aux(table: str, suffix: str, columns: list[str]) -> pd.DataFrame:
    try:
        frame = get_df(_gv_aux_table(table, suffix))
    except Exception:
        return pd.DataFrame(columns=columns)
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    return frame


def _gv_merge_patients(existing: pd.DataFrame, incoming: pd.DataFrame, replace_sheets: list[str] | None = None) -> pd.DataFrame:
    old = _gv_prepare_patients(existing)
    new = _gv_prepare_patients(incoming)
    if replace_sheets:
        selected = {_gv_norm(value) for value in replace_sheets}
        old = old[~old["hoja_origen"].apply(_gv_norm).isin(selected)].copy()
    combined = pd.concat([old, new], ignore_index=True)
    if combined.empty:
        return pd.DataFrame(columns=_GV_PATIENT_COLUMNS)
    combined = combined.drop_duplicates(subset=["id_registro"], keep="last")
    natural = combined.assign(
        _sheet=combined["hoja_origen"].apply(_gv_norm),
        _patient=combined["paciente"].apply(_gv_norm),
        _date=pd.to_datetime(combined["fecha_plan"], errors="coerce").dt.strftime("%Y-%m-%d").fillna(""),
    )
    natural = natural.drop_duplicates(subset=["_sheet", "numero", "_patient", "_date"], keep="last")
    return _gv_prepare_patients(natural.drop(columns=["_sheet", "_patient", "_date"]))


def _gv_merge_cash(existing: pd.DataFrame, incoming: pd.DataFrame, replace_sheets: list[str] | None = None) -> pd.DataFrame:
    old = _gv_prepare_cash(existing)
    new = _gv_prepare_cash(incoming)
    if replace_sheets:
        selected = {_gv_norm(value) for value in replace_sheets}
        old = old[~old["hoja_origen"].apply(_gv_norm).isin(selected)].copy()
    combined = pd.concat([old, new], ignore_index=True)
    if combined.empty:
        return pd.DataFrame(columns=_GV_CASH_COLUMNS)
    combined = combined.drop_duplicates(subset=["id_movimiento"], keep="last")
    return _gv_prepare_cash(combined)


def _gv_merge_summary(existing: pd.DataFrame, incoming: pd.DataFrame, replace_sheets: list[str] | None = None) -> pd.DataFrame:
    old = _gv_prepare_summary(existing)
    new = _gv_prepare_summary(incoming)
    if replace_sheets:
        selected = {_gv_norm(value) for value in replace_sheets}
        old = old[~old["hoja_origen"].apply(_gv_norm).isin(selected)].copy()
    combined = pd.concat([old, new], ignore_index=True)
    return _gv_prepare_summary(combined)


def _gv_rebuild_summary(summary: pd.DataFrame, cash: pd.DataFrame, patients: pd.DataFrame) -> pd.DataFrame:
    base = _gv_prepare_summary(summary)
    cash_data = _gv_prepare_cash(cash)
    patient_data = _gv_prepare_patients(patients)
    sheets = sorted(set(
        base.get("hoja_origen", pd.Series(dtype=str)).dropna().astype(str).tolist()
        + cash_data.get("hoja_origen", pd.Series(dtype=str)).dropna().astype(str).tolist()
        + patient_data.get("hoja_origen", pd.Series(dtype=str)).dropna().astype(str).tolist()
    ))
    if not sheets:
        sheets = ["GineVitae"]
    rows = []
    for sheet in sheets:
        current = base[base["hoja_origen"].astype(str).eq(sheet)]
        current_row = current.iloc[-1] if not current.empty else pd.Series(dtype=object)
        cash_sheet = cash_data[cash_data["hoja_origen"].astype(str).eq(sheet)]
        patient_sheet = patient_data[patient_data["hoja_origen"].astype(str).eq(sheet)]
        income = float(cash_sheet["ingreso"].sum()) if not cash_sheet.empty else _gv_number(current_row.get("ingresos", 0))
        expense = float(cash_sheet["egreso"].sum()) if not cash_sheet.empty else _gv_number(current_row.get("egresos", 0))
        net = income - expense
        declared_cash = _gv_number(current_row.get("caja_declarada", 0))
        rows.append({
            "hoja_origen": sheet,
            "caja_declarada": declared_cash,
            "cuenta_declarada": net - declared_cash,
            "ingresos": income,
            "egresos": expense,
            "saldo_movimientos": net,
            "total_planes": float(patient_sheet["total_cobrado"].sum()) if not patient_sheet.empty else _gv_number(current_row.get("total_planes", 0)),
            "pacientes": len(patient_sheet),
            "archivo_origen": _gv_text(current_row.get("archivo_origen", "Gestión manual"), "Gestión manual"),
        })
    return _gv_prepare_summary(pd.DataFrame(rows))


def _gv_attach_position(patients: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    data = _gv_prepare_patients(patients)
    if data.empty:
        return data
    total_position = float(_gv_prepare_summary(summary)["saldo_movimientos"].sum()) if summary is not None and not summary.empty else 0.0
    data["saldo_movimiento"] = 0.0
    data.loc[data.index[0], "saldo_movimiento"] = total_position
    return data


def _gv_patients_for_sheet(df: pd.DataFrame) -> pd.DataFrame:
    data = _gv_prepare_patients(df).copy()
    if data.empty:
        return pd.DataFrame(columns=_GV_PATIENT_COLUMNS)
    data["fecha_plan"] = pd.to_datetime(data["fecha_plan"], errors="coerce").dt.strftime("%Y-%m-%d").replace("NaT", "")
    for column in ["factura", "autorizado", "entregado"]:
        data[column] = data[column].apply(lambda value: "" if _gv_bool(value) is None else bool(_gv_bool(value)))
    data["numero"] = pd.to_numeric(data["numero"], errors="coerce").fillna(0).astype(int)
    data["fila_origen"] = pd.to_numeric(data["fila_origen"], errors="coerce").fillna(0).astype(int)
    return data[_GV_PATIENT_COLUMNS]


def _gv_cash_for_sheet(df: pd.DataFrame) -> pd.DataFrame:
    data = _gv_prepare_cash(df).copy()
    if data.empty:
        return pd.DataFrame(columns=_GV_CASH_COLUMNS)
    data["fecha"] = pd.to_datetime(data["fecha"], errors="coerce").dt.strftime("%Y-%m-%d").replace("NaT", "")
    data["fila_origen"] = pd.to_numeric(data["fila_origen"], errors="coerce").fillna(0).astype(int)
    return data[_GV_CASH_COLUMNS]


@st.cache_data(show_spinner=False)
def _gv_read_uploaded_bytes(filename: str, content: bytes) -> tuple[dict[str, pd.DataFrame], str]:
    from io import BytesIO, StringIO

    lower = filename.lower()
    if lower.endswith(".csv"):
        text = None
        for encoding in ["utf-8-sig", "utf-8", "latin-1"]:
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("No se pudo interpretar la codificación del CSV.")
        return {
            "CSV": pd.read_csv(StringIO(text), header=None, sep=None, engine="python", dtype=object)
        }, filename
    excel = pd.ExcelFile(BytesIO(content))
    matrices = {
        sheet_name: pd.read_excel(BytesIO(content), sheet_name=sheet_name, header=None, dtype=object)
        for sheet_name in excel.sheet_names
    }
    return matrices, filename


def _gv_money(value: Any) -> str:
    number = _gv_number(value)
    formatted = f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"$ {formatted}"


def _gv_bool_label(value: Any) -> str:
    parsed = _gv_bool(value)
    if parsed is True:
        return "Sí"
    if parsed is False:
        return "No"
    return "Sin definir"


def _gv_css() -> None:
    st.markdown(
        """
        <style>
        .gv-hero {
            background: linear-gradient(135deg, #173b46 0%, #176d70 48%, #2f9585 100%);
            color: white; border-radius: 24px; padding: 27px 30px; margin: 4px 0 18px 0;
            box-shadow: 0 18px 42px rgba(19, 74, 77, .24);
        }
        .gv-kicker {font-size:.76rem; letter-spacing:.15em; text-transform:uppercase; opacity:.82; font-weight:800;}
        .gv-title {font-size:2.08rem; font-weight:900; line-height:1.06; margin-top:7px;}
        .gv-subtitle {font-size:.98rem; opacity:.92; max-width:980px; margin-top:9px;}
        .gv-card {
            background:rgba(255,255,255,.98); border:1px solid rgba(23,109,112,.14);
            border-radius:18px; padding:16px 18px; min-height:126px;
            box-shadow:0 9px 26px rgba(21,69,73,.07); margin-bottom:10px;
        }
        .gv-card-label {font-size:.74rem; color:#68777c; font-weight:800; letter-spacing:.055em; text-transform:uppercase;}
        .gv-card-value {font-size:1.50rem; color:#153d45; font-weight:900; margin-top:6px; white-space:nowrap;}
        .gv-card-note {font-size:.80rem; color:#7b888c; margin-top:7px;}
        .gv-alert {border-left:5px solid #d8a22d; background:#fff9e9; border-radius:13px; padding:12px 15px; margin:8px 0;}
        .gv-danger {border-left-color:#bd4b4b; background:#fff3f3;}
        .gv-ok {border-left-color:#2d8669; background:#f2fbf7;}
        .gv-info {border-left-color:#3f7faa; background:#f2f8fc;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _gv_card(label: str, value: str, note: str = "") -> None:
    st.markdown(
        f"""
        <div class="gv-card">
            <div class="gv-card-label">{label}</div>
            <div class="gv-card-value">{value}</div>
            <div class="gv-card-note">{note}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _gv_enrich(patients: pd.DataFrame) -> pd.DataFrame:
    data = _gv_prepare_patients(patients).copy()
    if data.empty:
        return data
    data["_fecha"] = pd.to_datetime(data["fecha_plan"], errors="coerce")
    data["_efectivo"] = pd.to_numeric(data["efectivo"], errors="coerce").fillna(0.0)
    data["_mp"] = pd.to_numeric(data["mercado_pago"], errors="coerce").fillna(0.0)
    data["_total"] = data["_efectivo"] + data["_mp"]
    data["_factura"] = data["factura"].apply(_gv_bool).eq(True)
    data["_autorizado"] = data["autorizado"].apply(_gv_bool).eq(True)
    data["_entregado"] = data["entregado"].apply(_gv_bool).eq(True)
    data["_estado"] = data.apply(_gv_status, axis=1)
    data["estado_operativo"] = data["_estado"]
    data["_ausente"] = data["_estado"].eq("Ausente / no realizado")
    data["_mes"] = data["_fecha"].dt.to_period("M").astype(str).replace("NaT", "Sin fecha")
    return data


def _gv_export_excel(patients: pd.DataFrame, cash: pd.DataFrame, summary: pd.DataFrame, raw: pd.DataFrame) -> bytes:
    from io import BytesIO

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        _gv_patients_for_sheet(patients).to_excel(writer, sheet_name="Planes y pacientes", index=False)
        _gv_cash_for_sheet(cash).to_excel(writer, sheet_name="Caja", index=False)
        _gv_prepare_summary(summary).to_excel(writer, sheet_name="Resumen", index=False)
        if raw is not None and not raw.empty:
            for sheet_name in raw["hoja_origen"].dropna().astype(str).unique().tolist():
                matrix = _gv_raw_matrix(raw, sheet_name)
                safe_name = re.sub(r"[\\/*?:\[\]]", "_", sheet_name)[:24] or "Original"
                matrix.to_excel(writer, sheet_name=f"ORG {safe_name}"[:31], index=True)
    output.seek(0)
    return output.getvalue()


def render_gine_vitae_pro(
    df_original: pd.DataFrame,
    table: str = "gine_vitae",
    module_name: str = "Gine Vitae",
) -> None:
    """Centro integral de planes, pacientes, operación y caja de Gine Vitae."""
    _gv_css()
    today = pd.Timestamp.today().normalize()

    cash_aux_raw = _gv_load_aux(table, "caja", _GV_CASH_COLUMNS)
    summary_aux_raw = _gv_load_aux(table, "resumen", _GV_SUMMARY_COLUMNS)
    raw_long = _gv_load_aux(table, "matriz", _GV_RAW_COLUMNS)
    raw_long = raw_long if set(_GV_RAW_COLUMNS).issubset(raw_long.columns) else pd.DataFrame(columns=_GV_RAW_COLUMNS)

    patients = _gv_prepare_patients(df_original, source="Google Sheets")
    cash = _gv_prepare_cash(cash_aux_raw, source="Google Sheets")
    summary = _gv_prepare_summary(summary_aux_raw)
    fallback_matrix = pd.DataFrame()

    # Compatibilidad con hojas antiguas: puede existir el cuadro completo en la
    # hoja principal o solamente la caja normalizada por el importador genérico.
    if patients.empty:
        fallback_matrix = _gv_matrix_from_dataframe(df_original)
        parsed_patients, parsed_cash, parsed_summary = _gv_parse_matrix(
            fallback_matrix, "Hoja principal", "Google Sheets"
        )
        if not parsed_patients.empty:
            patients = parsed_patients
        if cash.empty and not parsed_cash.empty:
            cash = parsed_cash
        if summary.empty and not parsed_summary.empty:
            summary = parsed_summary
    if cash.empty and df_original is not None and not df_original.empty:
        possible_cash = _gv_prepare_cash(df_original, source="Google Sheets")
        if not possible_cash.empty:
            cash = possible_cash

    # Si existe la matriz original permanente, es la fuente de reparación más confiable.
    if not raw_long.empty and (patients.empty or cash.empty or summary.empty):
        parsed_patient_frames: list[pd.DataFrame] = []
        parsed_cash_frames: list[pd.DataFrame] = []
        parsed_summary_frames: list[pd.DataFrame] = []
        for sheet_name in raw_long["hoja_origen"].dropna().astype(str).unique().tolist():
            matrix = _gv_raw_matrix(raw_long, sheet_name)
            p, c, s = _gv_parse_matrix(matrix, sheet_name, "Matriz guardada")
            if not p.empty:
                parsed_patient_frames.append(p)
            if not c.empty:
                parsed_cash_frames.append(c)
            if not s.empty:
                parsed_summary_frames.append(s)
        if patients.empty and parsed_patient_frames:
            patients = _gv_prepare_patients(pd.concat(parsed_patient_frames, ignore_index=True))
        if cash.empty and parsed_cash_frames:
            cash = _gv_prepare_cash(pd.concat(parsed_cash_frames, ignore_index=True))
        if summary.empty and parsed_summary_frames:
            summary = _gv_prepare_summary(pd.concat(parsed_summary_frames, ignore_index=True))

    summary = _gv_rebuild_summary(summary, cash, patients)
    patients = _gv_attach_position(patients, summary)
    data = _gv_enrich(patients)

    hero_left, hero_right = st.columns([5.7, 1.1])
    with hero_left:
        st.markdown(
            """
            <div class="gv-hero">
                <div class="gv-kicker">Programa preventivo · Gestión integral</div>
                <div class="gv-title">Planes Gine Vitae</div>
                <div class="gv-subtitle">
                    Pacientes, cobros, facturación, autorizaciones, entregas y caja en una sola pantalla.
                    Lee Google Sheets, importa el Excel por hoja y conserva el cuadro original como respaldo permanente.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with hero_right:
        st.write("")
        st.write("")
        if st.button("🔄 Actualizar", key=f"gv_refresh_{table}", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.caption(f"Corte: {today.strftime('%d/%m/%Y')}")

    tabs = st.tabs([
        "📊 Centro ejecutivo",
        "👩‍⚕️ Planes y pacientes",
        "✅ Operación",
        "💰 Caja",
        "➕ Gestión",
        "📥 Importar Excel",
        "🧾 Vista original",
        "🤖 IA y exportación",
    ])

    with tabs[0]:
        if data.empty:
            st.info("Todavía no hay pacientes reconocidos. Importá el Excel desde la pestaña “Importar Excel”.")
        else:
            sheet_options = sorted(data["hoja_origen"].dropna().astype(str).unique().tolist())
            status_options = sorted(data["_estado"].dropna().astype(str).unique().tolist())
            f1, f2, f3 = st.columns([1.3, 1.5, 1.2])
            with f1:
                selected_sheets = st.multiselect(
                    "Hoja de origen", sheet_options, default=sheet_options, key=f"gv_exec_sheets_{table}"
                )
            with f2:
                selected_status = st.multiselect(
                    "Estado operativo", status_options, default=status_options, key=f"gv_exec_status_{table}"
                )
            with f3:
                period_options = ["Todos"] + sorted(
                    [value for value in data["_mes"].dropna().astype(str).unique().tolist() if value != "Sin fecha"],
                    reverse=True,
                ) + (["Sin fecha"] if "Sin fecha" in data["_mes"].values else [])
                selected_period = st.selectbox("Período", period_options, key=f"gv_exec_period_{table}")

            visible = data.copy()
            if selected_sheets:
                visible = visible[visible["hoja_origen"].isin(selected_sheets)]
            if selected_status:
                visible = visible[visible["_estado"].isin(selected_status)]
            if selected_period != "Todos":
                visible = visible[visible["_mes"].eq(selected_period)]

            total_patients = len(visible)
            total_collected = float(visible["_total"].sum())
            total_cash = float(visible["_efectivo"].sum())
            total_mp = float(visible["_mp"].sum())
            authorized = int(visible["_autorizado"].sum())
            delivered = int(visible["_entregado"].sum())
            invoiced = int(visible["_factura"].sum())
            absent = int(visible["_ausente"].sum())

            r1 = st.columns(5)
            with r1[0]:
                _gv_card("Pacientes", str(total_patients), f"{absent} ausentes / no realizados")
            with r1[1]:
                _gv_card("Cobrado total", _gv_money(total_collected), "Efectivo + MP")
            with r1[2]:
                _gv_card("Efectivo", _gv_money(total_cash), f"{(total_cash / total_collected * 100) if total_collected else 0:.1f}% del total")
            with r1[3]:
                _gv_card("Mercado Pago", _gv_money(total_mp), f"{(total_mp / total_collected * 100) if total_collected else 0:.1f}% del total")
            with r1[4]:
                average = total_collected / total_patients if total_patients else 0
                _gv_card("Promedio por paciente", _gv_money(average), "Ingreso medio registrado")

            r2 = st.columns(3)
            with r2[0]:
                _gv_card("Autorizados", f"{authorized}/{total_patients}", f"{(authorized / total_patients * 100) if total_patients else 0:.1f}%")
            with r2[1]:
                _gv_card("Entregados", f"{delivered}/{total_patients}", f"{(delivered / total_patients * 100) if total_patients else 0:.1f}%")
            with r2[2]:
                _gv_card("Facturados", f"{invoiced}/{total_patients}", f"{(invoiced / total_patients * 100) if total_patients else 0:.1f}%")

            # El cuadro original muestra tres conceptos distintos y no deben
            # confundirse: CAJA, CUENTA y el TOTAL de movimientos. Antes se
            # mostraba únicamente $68.000 como “Posición de caja”, aunque ese
            # importe es el consolidado ($345.000 + -$277.000), no la caja.
            selected_summary = summary.copy()
            if selected_sheets and not selected_summary.empty:
                selected_summary = selected_summary[
                    selected_summary["hoja_origen"].astype(str).isin(selected_sheets)
                ]
            cash_declared = float(selected_summary["caja_declarada"].sum()) if not selected_summary.empty else 0.0
            account_declared = float(selected_summary["cuenta_declarada"].sum()) if not selected_summary.empty else 0.0
            movement_balance = float(selected_summary["saldo_movimientos"].sum()) if not selected_summary.empty else 0.0
            consolidated_position = cash_declared + account_declared
            if abs(cash_declared) <= 0.001 and abs(account_declared) <= 0.001:
                consolidated_position = movement_balance

            r3 = st.columns(3)
            with r3[0]:
                _gv_card("Caja declarada", _gv_money(cash_declared), "Valor exacto de la celda CAJA")
            with r3[1]:
                _gv_card("Cuenta declarada", _gv_money(account_declared), "Valor exacto de la celda CUENTA")
            with r3[2]:
                _gv_card("Posición consolidada", _gv_money(consolidated_position), "Caja + cuenta")

            pending_authorization = int((~visible["_autorizado"] & ~visible["_ausente"]).sum())
            pending_delivery = int((visible["_autorizado"] & ~visible["_entregado"] & ~visible["_ausente"]).sum())
            pending_invoice = int((visible["_entregado"] & ~visible["_factura"] & ~visible["_ausente"]).sum())
            if pending_authorization or pending_delivery or pending_invoice:
                st.markdown(
                    f'<div class="gv-alert"><b>Cola operativa:</b> {pending_authorization} pendientes de autorización · '
                    f'{pending_delivery} autorizados pendientes de entrega · {pending_invoice} entregados pendientes de factura.</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown('<div class="gv-alert gv-ok"><b>Operación al día:</b> no hay pendientes en la selección actual.</div>', unsafe_allow_html=True)

            c1, c2 = st.columns(2)
            with c1:
                payment = pd.DataFrame({
                    "Medio": ["Efectivo", "Mercado Pago"],
                    "Importe": [total_cash, total_mp],
                })
                st.markdown("#### Composición de cobros")
                if payment["Importe"].sum() > 0:
                    fig = px.pie(payment, names="Medio", values="Importe", hole=.56)
                    fig.update_layout(margin=dict(l=10, r=10, t=20, b=10), legend_title_text="")
                    st.plotly_chart(fig, use_container_width=True, key=f"gv_payment_{table}")
                else:
                    st.info("No hay importes para graficar.")
            with c2:
                funnel = pd.DataFrame({
                    "Etapa": ["Registrados", "Autorizados", "Entregados", "Facturados"],
                    "Pacientes": [total_patients, authorized, delivered, invoiced],
                })
                st.markdown("#### Embudo operativo")
                fig = px.bar(funnel, x="Etapa", y="Pacientes", text="Pacientes")
                fig.update_traces(textposition="outside")
                fig.update_layout(margin=dict(l=10, r=10, t=20, b=10), yaxis_title="Pacientes")
                st.plotly_chart(fig, use_container_width=True, key=f"gv_funnel_{table}")

            monthly = visible[visible["_fecha"].notna()].copy()
            if not monthly.empty:
                monthly["Mes"] = monthly["_fecha"].dt.to_period("M").astype(str)
                monthly_summary = monthly.groupby("Mes", as_index=False).agg(
                    Pacientes=("id_registro", "count"),
                    Cobrado=("_total", "sum"),
                ).sort_values("Mes")
                st.markdown("#### Evolución mensual")
                fig = px.bar(monthly_summary, x="Mes", y="Cobrado", text="Pacientes", hover_data=["Pacientes"])
                fig.update_layout(margin=dict(l=10, r=10, t=20, b=10), yaxis_title="Cobrado")
                st.plotly_chart(fig, use_container_width=True, key=f"gv_monthly_{table}")

            campaign = visible.copy()
            campaign["Fecha"] = campaign["_fecha"].dt.strftime("%d/%m/%Y").fillna("Sin fecha")
            campaign_summary = campaign.groupby("Fecha", as_index=False).agg(
                Pacientes=("id_registro", "count"),
                Cobrado=("_total", "sum"),
                Autorizados=("_autorizado", "sum"),
                Entregados=("_entregado", "sum"),
                Facturados=("_factura", "sum"),
            ).sort_values("Cobrado", ascending=False)
            st.markdown("#### Rendimiento por jornada / fecha")
            st.dataframe(
                campaign_summary,
                use_container_width=True,
                hide_index=True,
                column_config={"Cobrado": st.column_config.NumberColumn(format="$ %.2f")},
            )

    with tabs[1]:
        if data.empty:
            st.info("No hay pacientes cargados.")
        else:
            p1, p2, p3, p4 = st.columns([1.1, 1.2, 1.2, 1.8])
            with p1:
                sheet_filter = st.selectbox(
                    "Hoja", ["Todas"] + sorted(data["hoja_origen"].unique().tolist()), key=f"gv_patient_sheet_{table}"
                )
            with p2:
                status_filter = st.selectbox(
                    "Estado", ["Todos"] + sorted(data["_estado"].unique().tolist()), key=f"gv_patient_status_{table}"
                )
            with p3:
                payment_filter = st.selectbox(
                    "Medio de pago", ["Todos", "Efectivo", "Mercado Pago", "Mixto", "Sin pago"], key=f"gv_patient_payment_{table}"
                )
            with p4:
                search = st.text_input("Buscar paciente", placeholder="Nombre u observación", key=f"gv_patient_search_{table}").strip().lower()
            visible = data.copy()
            if sheet_filter != "Todas":
                visible = visible[visible["hoja_origen"].eq(sheet_filter)]
            if status_filter != "Todos":
                visible = visible[visible["_estado"].eq(status_filter)]
            if payment_filter == "Efectivo":
                visible = visible[(visible["_efectivo"] > 0) & (visible["_mp"] <= 0)]
            elif payment_filter == "Mercado Pago":
                visible = visible[(visible["_mp"] > 0) & (visible["_efectivo"] <= 0)]
            elif payment_filter == "Mixto":
                visible = visible[(visible["_mp"] > 0) & (visible["_efectivo"] > 0)]
            elif payment_filter == "Sin pago":
                visible = visible[visible["_total"] <= 0]
            if search:
                haystack = (visible["paciente"] + " " + visible["observaciones"] + " " + visible["_estado"]).str.lower()
                visible = visible[haystack.str.contains(search, regex=False, na=False)]

            st.caption(f"{len(visible)} pacientes visibles · Total {_gv_money(visible['_total'].sum())}")
            detail = visible[[
                "numero", "paciente", "fecha_plan", "efectivo", "mercado_pago", "total_cobrado",
                "factura", "autorizado", "entregado", "estado_operativo", "observaciones", "hoja_origen",
            ]].copy()
            st.dataframe(
                detail,
                use_container_width=True,
                hide_index=True,
                height=610,
                column_config={
                    "numero": st.column_config.NumberColumn("N°", format="%d"),
                    "paciente": st.column_config.TextColumn("Paciente", width="large"),
                    "fecha_plan": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
                    "efectivo": st.column_config.NumberColumn("Efectivo", format="$ %.2f"),
                    "mercado_pago": st.column_config.NumberColumn("MP", format="$ %.2f"),
                    "total_cobrado": st.column_config.NumberColumn("Total", format="$ %.2f"),
                    "factura": st.column_config.CheckboxColumn("Factura"),
                    "autorizado": st.column_config.CheckboxColumn("Autorizado"),
                    "entregado": st.column_config.CheckboxColumn("Entregado"),
                    "estado_operativo": st.column_config.TextColumn("Estado", width="large"),
                    "observaciones": st.column_config.TextColumn("Observaciones", width="large"),
                    "hoja_origen": st.column_config.TextColumn("Hoja"),
                },
            )

    with tabs[2]:
        if data.empty:
            st.info("No hay pacientes para gestionar.")
        else:
            st.markdown("### Cola operativa priorizada")
            queue = data[~data["_estado"].isin(["Completo", "Cancelado"])].copy()
            priority_map = {
                "Pendiente autorización": 1,
                "Autorizado · pendiente entrega": 2,
                "Entregado · pendiente factura": 3,
                "En gestión": 4,
                "Ausente / no realizado": 5,
            }
            queue["_prioridad"] = queue["_estado"].map(priority_map).fillna(9)
            queue = queue.sort_values(["_prioridad", "_fecha", "numero"], na_position="last")
            q1, q2, q3, q4 = st.columns(4)
            q1.metric("Pendiente autorización", int((queue["_estado"] == "Pendiente autorización").sum()))
            q2.metric("Pendiente entrega", int((queue["_estado"] == "Autorizado · pendiente entrega").sum()))
            q3.metric("Pendiente factura", int((queue["_estado"] == "Entregado · pendiente factura").sum()))
            q4.metric("Ausentes / no realizados", int((queue["_estado"] == "Ausente / no realizado").sum()))
            st.dataframe(
                queue[["paciente", "fecha_plan", "estado_operativo", "factura", "autorizado", "entregado", "observaciones", "hoja_origen"]],
                use_container_width=True,
                hide_index=True,
                height=370,
            )

            st.markdown("### Actualización rápida")
            operational = patients[[
                "id_registro", "numero", "paciente", "fecha_plan", "factura", "autorizado", "entregado", "observaciones", "hoja_origen"
            ]].copy()
            operational["fecha_plan"] = pd.to_datetime(operational["fecha_plan"], errors="coerce").dt.date
            edited_operational = st.data_editor(
                operational,
                use_container_width=True,
                hide_index=True,
                height=470,
                key=f"gv_operational_editor_{table}",
                column_config={
                    "id_registro": st.column_config.TextColumn("ID", disabled=True),
                    "numero": st.column_config.NumberColumn("N°", disabled=True),
                    "paciente": st.column_config.TextColumn("Paciente", disabled=True, width="large"),
                    "fecha_plan": st.column_config.DateColumn("Fecha", disabled=True, format="DD/MM/YYYY"),
                    "factura": st.column_config.CheckboxColumn("Factura"),
                    "autorizado": st.column_config.CheckboxColumn("Autorizado"),
                    "entregado": st.column_config.CheckboxColumn("Entregado"),
                    "observaciones": st.column_config.TextColumn("Observaciones", width="large"),
                    "hoja_origen": st.column_config.TextColumn("Hoja", disabled=True),
                },
            )
            confirm_ops = st.checkbox("Confirmo guardar la actualización operativa", key=f"gv_ops_confirm_{table}")
            if st.button("💾 Guardar operación", type="primary", use_container_width=True, disabled=not confirm_ops, key=f"gv_ops_save_{table}"):
                try:
                    updated = patients.copy().set_index("id_registro")
                    edit_index = edited_operational.copy().set_index("id_registro")
                    for column in ["factura", "autorizado", "entregado", "observaciones"]:
                        updated.loc[edit_index.index, column] = edit_index[column]
                    updated = _gv_prepare_patients(updated.reset_index())
                    updated = _gv_attach_position(updated, summary)
                    sync_df_to_sheet(table, _gv_patients_for_sheet(updated))
                    st.cache_data.clear()
                    st.success("Operación actualizada correctamente en Google Sheets.")
                    st.rerun()
                except Exception as error:
                    st.error("No se pudo guardar la actualización operativa.")
                    st.exception(error)

    with tabs[3]:
        st.markdown("### Caja Gine Vitae")
        selected_cash_sheet = st.selectbox(
            "Hoja de caja",
            ["Todas"] + sorted(set(
                cash.get("hoja_origen", pd.Series(dtype=str)).dropna().astype(str).tolist()
                + summary.get("hoja_origen", pd.Series(dtype=str)).dropna().astype(str).tolist()
            )),
            key=f"gv_cash_sheet_{table}",
        )
        visible_cash = cash.copy()
        visible_summary = summary.copy()
        if selected_cash_sheet != "Todas":
            visible_cash = visible_cash[visible_cash["hoja_origen"].astype(str).eq(selected_cash_sheet)]
            visible_summary = visible_summary[visible_summary["hoja_origen"].astype(str).eq(selected_cash_sheet)]
        income = float(visible_cash["ingreso"].sum()) if not visible_cash.empty else float(visible_summary["ingresos"].sum())
        expense = float(visible_cash["egreso"].sum()) if not visible_cash.empty else float(visible_summary["egresos"].sum())
        net = income - expense
        cash_declared = float(visible_summary["caja_declarada"].sum()) if not visible_summary.empty else 0.0
        account_declared = float(visible_summary["cuenta_declarada"].sum()) if not visible_summary.empty else net - cash_declared
        cm = st.columns(5)
        with cm[0]: _gv_card("Ingresos", _gv_money(income), f"{len(visible_cash[visible_cash['ingreso'] > 0]) if not visible_cash.empty else 0} movimientos")
        with cm[1]: _gv_card("Egresos", _gv_money(expense), f"{len(visible_cash[visible_cash['egreso'] > 0]) if not visible_cash.empty else 0} movimientos")
        with cm[2]: _gv_card("Saldo movimientos", _gv_money(net), "Ingresos − egresos")
        with cm[3]: _gv_card("Caja declarada", _gv_money(cash_declared), "Valor leído del cuadro")
        with cm[4]: _gv_card("Cuenta declarada", _gv_money(account_declared), "Valor leído del cuadro")

        if not visible_cash.empty:
            chart_cash = visible_cash.copy()
            chart_cash["_fecha"] = pd.to_datetime(chart_cash["fecha"], errors="coerce")
            chart_cash = chart_cash[chart_cash["_fecha"].notna()]
            if not chart_cash.empty:
                chart_cash["Mes"] = chart_cash["_fecha"].dt.to_period("M").astype(str)
                monthly_cash = chart_cash.groupby("Mes", as_index=False).agg(Ingresos=("ingreso", "sum"), Egresos=("egreso", "sum"))
                monthly_long = monthly_cash.melt(id_vars="Mes", value_vars=["Ingresos", "Egresos"], var_name="Tipo", value_name="Importe")
                fig = px.bar(monthly_long, x="Mes", y="Importe", color="Tipo", barmode="group")
                fig.update_layout(margin=dict(l=10, r=10, t=20, b=10), legend_title_text="")
                st.plotly_chart(fig, use_container_width=True, key=f"gv_cash_chart_{table}")

            display_cash = visible_cash.sort_values(
                ["fecha", "fila_origen"], na_position="last"
            ).copy()
            display_cash["saldo_acumulado"] = display_cash["neto"].cumsum()
            st.dataframe(
                display_cash[["fecha", "concepto", "ingreso", "egreso", "neto", "saldo_acumulado", "hoja_origen"]],
                use_container_width=True,
                hide_index=True,
                height=440,
                column_config={
                    "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
                    "concepto": st.column_config.TextColumn("Concepto", width="large"),
                    "ingreso": st.column_config.NumberColumn("Ingreso", format="$ %.2f"),
                    "egreso": st.column_config.NumberColumn("Egreso", format="$ %.2f"),
                    "neto": st.column_config.NumberColumn("Neto", format="$ %.2f"),
                    "saldo_acumulado": st.column_config.NumberColumn("Saldo acumulado", format="$ %.2f"),
                    "hoja_origen": st.column_config.TextColumn("Hoja"),
                },
            )
        else:
            st.info("No hay movimientos de caja normalizados.")

        st.markdown("#### Registrar movimiento")
        source_options = sorted(set(
            patients.get("hoja_origen", pd.Series(dtype=str)).dropna().astype(str).tolist()
            + summary.get("hoja_origen", pd.Series(dtype=str)).dropna().astype(str).tolist()
        )) or ["GineVitae"]
        with st.form(f"gv_cash_form_{table}", clear_on_submit=True):
            fc1, fc2, fc3, fc4 = st.columns([1.0, 1.0, 1.8, 1.2])
            with fc1:
                movement_date = st.date_input("Fecha", value=date.today(), key=f"gv_cash_date_{table}")
            with fc2:
                movement_type = st.selectbox("Tipo", ["Ingreso", "Egreso"], key=f"gv_cash_type_{table}")
            with fc3:
                concept = st.text_input("Concepto", key=f"gv_cash_concept_{table}")
            with fc4:
                amount = st.number_input("Importe", min_value=0.0, step=1000.0, key=f"gv_cash_amount_{table}")
            source_sheet = st.selectbox("Hoja de origen", source_options, key=f"gv_cash_source_{table}")
            save_movement = st.form_submit_button("Guardar movimiento", type="primary", use_container_width=True)
        if save_movement:
            if not concept.strip() or amount <= 0:
                st.warning("Completá concepto e importe.")
            else:
                try:
                    new_row = pd.DataFrame([{
                        "id_movimiento": _gv_make_id(source_sheet, movement_date, concept, amount, len(cash) + 1, prefix="GVC"),
                        "fecha": movement_date,
                        "ingreso": amount if movement_type == "Ingreso" else 0.0,
                        "egreso": amount if movement_type == "Egreso" else 0.0,
                        "concepto": concept,
                        "neto": amount if movement_type == "Ingreso" else -amount,
                        "hoja_origen": source_sheet,
                        "fila_origen": 0,
                        "archivo_origen": "Carga manual",
                    }])
                    new_cash = _gv_merge_cash(cash, new_row)
                    new_summary = _gv_rebuild_summary(summary, new_cash, patients)
                    new_patients = _gv_attach_position(patients, new_summary)
                    sync_df_to_sheet(_gv_aux_table(table, "caja"), _gv_cash_for_sheet(new_cash))
                    sync_df_to_sheet(_gv_aux_table(table, "resumen"), new_summary)
                    sync_df_to_sheet(table, _gv_patients_for_sheet(new_patients))
                    st.cache_data.clear()
                    st.success("Movimiento guardado y posición actualizada.")
                    st.rerun()
                except Exception as error:
                    st.error("No se pudo guardar el movimiento.")
                    st.exception(error)

    with tabs[4]:
        st.markdown("### Alta de paciente / plan")
        source_options = sorted(set(
            patients.get("hoja_origen", pd.Series(dtype=str)).dropna().astype(str).tolist()
            + summary.get("hoja_origen", pd.Series(dtype=str)).dropna().astype(str).tolist()
        )) or ["GineVitae"]
        next_number = int(pd.to_numeric(patients.get("numero", pd.Series(dtype=float)), errors="coerce").max() or 0) + 1 if not patients.empty else 1
        with st.form(f"gv_patient_form_{table}", clear_on_submit=True):
            g1, g2, g3 = st.columns([.7, 2.0, 1.2])
            with g1:
                patient_number = st.number_input("N°", min_value=1, value=next_number, step=1, key=f"gv_add_number_{table}")
            with g2:
                patient_name = st.text_input("Paciente", key=f"gv_add_patient_{table}")
            with g3:
                plan_date = st.date_input("Fecha del plan", value=date.today(), key=f"gv_add_date_{table}")
            g4, g5, g6 = st.columns(3)
            with g4:
                effective = st.number_input("Efectivo", min_value=0.0, step=5000.0, key=f"gv_add_cash_{table}")
            with g5:
                mp_amount = st.number_input("Mercado Pago", min_value=0.0, step=5000.0, key=f"gv_add_mp_{table}")
            with g6:
                patient_sheet = st.selectbox("Hoja de origen", source_options, key=f"gv_add_sheet_{table}")
            g7, g8, g9 = st.columns(3)
            with g7: invoiced = st.checkbox("Factura", key=f"gv_add_invoice_{table}")
            with g8: authorized = st.checkbox("Autorizado", key=f"gv_add_authorized_{table}")
            with g9: delivered = st.checkbox("Entregado", key=f"gv_add_delivered_{table}")
            observations = st.text_area("Observaciones", key=f"gv_add_notes_{table}")
            add_patient = st.form_submit_button("Guardar paciente", type="primary", use_container_width=True)
        if add_patient:
            if not patient_name.strip():
                st.warning("Ingresá el nombre del paciente.")
            else:
                try:
                    new_patient = pd.DataFrame([{
                        "id_registro": _gv_make_id(patient_sheet, patient_number, patient_name, plan_date, len(patients) + 1, prefix="GVP"),
                        "numero": int(patient_number),
                        "paciente": patient_name,
                        "efectivo": effective,
                        "mercado_pago": mp_amount,
                        "total_cobrado": effective + mp_amount,
                        "factura": invoiced,
                        "fecha_plan": plan_date,
                        "autorizado": authorized,
                        "entregado": delivered,
                        "observaciones": observations,
                        "estado_operativo": "",
                        "hoja_origen": patient_sheet,
                        "fila_origen": 0,
                        "archivo_origen": "Carga manual",
                        "saldo_movimiento": 0.0,
                    }])
                    destination = _gv_merge_patients(patients, new_patient)
                    new_summary = _gv_rebuild_summary(summary, cash, destination)
                    destination = _gv_attach_position(destination, new_summary)
                    sync_df_to_sheet(table, _gv_patients_for_sheet(destination))
                    sync_df_to_sheet(_gv_aux_table(table, "resumen"), new_summary)
                    st.cache_data.clear()
                    st.success("Paciente guardado correctamente.")
                    st.rerun()
                except Exception as error:
                    st.error("No se pudo guardar el paciente.")
                    st.exception(error)

        st.divider()
        st.markdown("### Editor completo de pacientes")
        if patients.empty:
            st.info("No hay registros para editar.")
        else:
            editable = patients.drop(columns=["estado_operativo", "saldo_movimiento"], errors="ignore").copy()
            editable["fecha_plan"] = pd.to_datetime(editable["fecha_plan"], errors="coerce").dt.date
            edited = st.data_editor(
                editable,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                height=560,
                key=f"gv_full_editor_{table}",
                column_config={
                    "id_registro": st.column_config.TextColumn("ID", disabled=True),
                    "numero": st.column_config.NumberColumn("N°", format="%d"),
                    "paciente": st.column_config.TextColumn("Paciente", width="large"),
                    "efectivo": st.column_config.NumberColumn("Efectivo", format="$ %.2f"),
                    "mercado_pago": st.column_config.NumberColumn("MP", format="$ %.2f"),
                    "total_cobrado": st.column_config.NumberColumn("Total", disabled=True, format="$ %.2f"),
                    "factura": st.column_config.CheckboxColumn("Factura"),
                    "fecha_plan": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
                    "autorizado": st.column_config.CheckboxColumn("Autorizado"),
                    "entregado": st.column_config.CheckboxColumn("Entregado"),
                    "observaciones": st.column_config.TextColumn("Observaciones", width="large"),
                    "hoja_origen": st.column_config.TextColumn("Hoja"),
                    "fila_origen": st.column_config.NumberColumn("Fila original", disabled=True),
                    "archivo_origen": st.column_config.TextColumn("Archivo", disabled=True),
                },
            )
            confirm_edit = st.checkbox("Confirmo guardar el editor completo", key=f"gv_full_confirm_{table}")
            if st.button("💾 Guardar tabla de pacientes", type="primary", use_container_width=True, disabled=not confirm_edit, key=f"gv_full_save_{table}"):
                try:
                    destination = _gv_prepare_patients(edited)
                    new_summary = _gv_rebuild_summary(summary, cash, destination)
                    destination = _gv_attach_position(destination, new_summary)
                    sync_df_to_sheet(table, _gv_patients_for_sheet(destination))
                    sync_df_to_sheet(_gv_aux_table(table, "resumen"), new_summary)
                    st.cache_data.clear()
                    st.success(f"Tabla guardada: {len(destination)} pacientes.")
                    st.rerun()
                except Exception as error:
                    st.error("No se pudo guardar la tabla.")
                    st.exception(error)

        st.divider()
        st.markdown("### Editor de movimientos de caja")
        if cash.empty:
            st.info("No hay movimientos para editar.")
        else:
            editable_cash = cash.copy()
            editable_cash["fecha"] = pd.to_datetime(editable_cash["fecha"], errors="coerce").dt.date
            edited_cash = st.data_editor(
                editable_cash,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                height=450,
                key=f"gv_cash_editor_{table}",
                column_config={
                    "id_movimiento": st.column_config.TextColumn("ID", disabled=True),
                    "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
                    "ingreso": st.column_config.NumberColumn("Ingreso", format="$ %.2f"),
                    "egreso": st.column_config.NumberColumn("Egreso", format="$ %.2f"),
                    "concepto": st.column_config.TextColumn("Concepto", width="large"),
                    "neto": st.column_config.NumberColumn("Neto", disabled=True, format="$ %.2f"),
                    "hoja_origen": st.column_config.TextColumn("Hoja"),
                    "fila_origen": st.column_config.NumberColumn("Fila original", disabled=True),
                    "archivo_origen": st.column_config.TextColumn("Archivo", disabled=True),
                },
            )
            confirm_cash_edit = st.checkbox("Confirmo guardar el editor de caja", key=f"gv_cash_edit_confirm_{table}")
            if st.button("💾 Guardar caja", type="primary", use_container_width=True, disabled=not confirm_cash_edit, key=f"gv_cash_edit_save_{table}"):
                try:
                    new_cash = _gv_prepare_cash(edited_cash)
                    new_summary = _gv_rebuild_summary(summary, new_cash, patients)
                    new_patients = _gv_attach_position(patients, new_summary)
                    sync_df_to_sheet(_gv_aux_table(table, "caja"), _gv_cash_for_sheet(new_cash))
                    sync_df_to_sheet(_gv_aux_table(table, "resumen"), new_summary)
                    sync_df_to_sheet(table, _gv_patients_for_sheet(new_patients))
                    st.cache_data.clear()
                    st.success("Caja guardada y posición actualizada.")
                    st.rerun()
                except Exception as error:
                    st.error("No se pudo guardar la caja.")
                    st.exception(error)

    with tabs[5]:
        st.markdown("### Importador inteligente de Planes Gine Vitae")
        st.caption(
            "Cargá el Excel, elegí una o varias hojas y verificá la lectura antes de guardarla. "
            "El sistema separa pacientes y caja aunque estén dentro del mismo cuadro."
        )
        uploaded = st.file_uploader(
            "Cargar Excel o CSV", type=["xlsx", "xls", "xlsm", "csv"], key=f"gv_upload_{table}"
        )
        if uploaded is None:
            st.info("Cargá la planilla. Después podrás seleccionar la hoja exacta que querés procesar.")
        else:
            try:
                matrices, filename = _gv_read_uploaded_bytes(_gv_text(uploaded.name, "archivo"), uploaded.getvalue())
                sheet_names = list(matrices.keys())
                preferred = [name for name in sheet_names if "gine" in _gv_norm(name) or "plan" in _gv_norm(name)]
                selected_import_sheets = st.multiselect(
                    "Hojas a procesar",
                    sheet_names,
                    default=preferred[:1] or sheet_names[:1],
                    key=f"gv_import_sheets_{table}",
                )
                if not selected_import_sheets:
                    st.warning("Seleccioná al menos una hoja.")
                else:
                    imported_patients_frames: list[pd.DataFrame] = []
                    imported_cash_frames: list[pd.DataFrame] = []
                    imported_summary_frames: list[pd.DataFrame] = []
                    imported_raw_frames: list[pd.DataFrame] = []
                    diagnostics: list[dict[str, Any]] = []
                    preview_tabs = st.tabs([f"📄 {name}" for name in selected_import_sheets])
                    for preview_tab, sheet_name in zip(preview_tabs, selected_import_sheets):
                        matrix = matrices[sheet_name]
                        parsed_patients, parsed_cash, parsed_summary = _gv_parse_matrix(matrix, sheet_name, filename)
                        if not parsed_patients.empty:
                            imported_patients_frames.append(parsed_patients)
                        if not parsed_cash.empty:
                            imported_cash_frames.append(parsed_cash)
                        if not parsed_summary.empty:
                            imported_summary_frames.append(parsed_summary)
                        imported_raw_frames.append(_gv_raw_long(matrix, sheet_name))
                        diagnostics.append({
                            "Hoja": sheet_name,
                            "Filas del cuadro": len(_gv_trim_matrix(matrix)),
                            "Pacientes": len(parsed_patients),
                            "Movimientos de caja": len(parsed_cash),
                            "Cobrado en planes": float(parsed_patients["total_cobrado"].sum()) if not parsed_patients.empty else 0.0,
                            "Saldo de caja": float(parsed_summary["saldo_movimientos"].sum()) if not parsed_summary.empty else 0.0,
                        })
                        with preview_tab:
                            st.markdown("**Vista original**")
                            preview_matrix = _gv_trim_matrix(matrix).copy()
                            preview_matrix.columns = [_gv_excel_column(index) for index in range(len(preview_matrix.columns))]
                            preview_matrix.index = range(1, len(preview_matrix) + 1)
                            st.dataframe(preview_matrix, use_container_width=True, height=380)
                            pc1, pc2 = st.columns(2)
                            with pc1:
                                st.markdown("**Pacientes detectados**")
                                if parsed_patients.empty:
                                    st.warning("No se encontró el bloque PACIENTES.")
                                else:
                                    st.dataframe(
                                        parsed_patients[["numero", "paciente", "efectivo", "mercado_pago", "fecha_plan", "factura", "autorizado", "entregado", "observaciones"]],
                                        use_container_width=True,
                                        hide_index=True,
                                        height=360,
                                    )
                            with pc2:
                                st.markdown("**Caja detectada**")
                                if parsed_cash.empty:
                                    st.warning("No se encontró el bloque INGRESO / EGRESO.")
                                else:
                                    st.dataframe(
                                        parsed_cash[["fecha", "ingreso", "egreso", "concepto"]],
                                        use_container_width=True,
                                        hide_index=True,
                                        height=360,
                                    )

                    diagnostics_df = pd.DataFrame(diagnostics)
                    st.markdown("#### Control de lectura")
                    st.dataframe(
                        diagnostics_df,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Cobrado en planes": st.column_config.NumberColumn(format="$ %.2f"),
                            "Saldo de caja": st.column_config.NumberColumn(format="$ %.2f"),
                        },
                    )
                    imported_patients = _gv_prepare_patients(pd.concat(imported_patients_frames, ignore_index=True)) if imported_patients_frames else pd.DataFrame(columns=_GV_PATIENT_COLUMNS)
                    imported_cash = _gv_prepare_cash(pd.concat(imported_cash_frames, ignore_index=True)) if imported_cash_frames else pd.DataFrame(columns=_GV_CASH_COLUMNS)
                    imported_summary = _gv_prepare_summary(pd.concat(imported_summary_frames, ignore_index=True)) if imported_summary_frames else pd.DataFrame(columns=_GV_SUMMARY_COLUMNS)
                    imported_raw = pd.concat(imported_raw_frames, ignore_index=True) if imported_raw_frames else pd.DataFrame(columns=_GV_RAW_COLUMNS)

                    if imported_patients.empty and imported_cash.empty:
                        st.error("No se detectaron pacientes ni movimientos. Revisá la hoja seleccionada.")
                    else:
                        mode = st.radio(
                            "Modo de guardado",
                            [
                                "Reemplazar solamente las hojas seleccionadas",
                                "Agregar / actualizar sin borrar otras hojas",
                                "Reemplazar toda la base de Gine Vitae",
                            ],
                            key=f"gv_import_mode_{table}",
                        )
                        confirm_import = st.checkbox(
                            f"Confirmo guardar {len(imported_patients)} pacientes y {len(imported_cash)} movimientos en Google Sheets",
                            key=f"gv_import_confirm_{table}",
                        )
                        if st.button(
                            "💾 Guardar planilla en Google Sheets",
                            type="primary",
                            use_container_width=True,
                            disabled=not confirm_import,
                            key=f"gv_import_save_{table}",
                        ):
                            try:
                                # Backups automáticos: la importación nunca borra sin dejar copia.
                                backup_targets = [
                                    (table, df_original),
                                    (_gv_aux_table(table, "caja"), cash_aux_raw),
                                    (_gv_aux_table(table, "resumen"), summary_aux_raw),
                                    (_gv_aux_table(table, "matriz"), raw_long),
                                ]
                                for backup_name, backup_df in backup_targets:
                                    if backup_df is not None and (not backup_df.empty or len(backup_df.columns) > 0):
                                        try:
                                            sync_df_to_sheet(_gv_aux_table(backup_name, "backup"), backup_df.copy())
                                        except Exception:
                                            pass

                                if mode.startswith("Reemplazar solamente"):
                                    replace = selected_import_sheets
                                    destination_patients = _gv_merge_patients(patients, imported_patients, replace)
                                    destination_cash = _gv_merge_cash(cash, imported_cash, replace)
                                    destination_summary = _gv_merge_summary(summary, imported_summary, replace)
                                    existing_raw = raw_long[~raw_long["hoja_origen"].astype(str).isin(selected_import_sheets)].copy() if not raw_long.empty else pd.DataFrame(columns=_GV_RAW_COLUMNS)
                                    destination_raw = pd.concat([existing_raw, imported_raw], ignore_index=True)
                                elif mode.startswith("Agregar"):
                                    destination_patients = _gv_merge_patients(patients, imported_patients)
                                    destination_cash = _gv_merge_cash(cash, imported_cash)
                                    destination_summary = _gv_merge_summary(summary, imported_summary)
                                    destination_raw = pd.concat([raw_long, imported_raw], ignore_index=True)
                                else:
                                    destination_patients = imported_patients
                                    destination_cash = imported_cash
                                    destination_summary = imported_summary
                                    destination_raw = imported_raw

                                destination_summary = _gv_rebuild_summary(destination_summary, destination_cash, destination_patients)
                                destination_patients = _gv_attach_position(destination_patients, destination_summary)
                                if not destination_raw.empty:
                                    destination_raw = destination_raw.drop_duplicates(
                                        subset=["hoja_origen", "fila", "columna"], keep="last"
                                    )
                                sync_df_to_sheet(table, _gv_patients_for_sheet(destination_patients))
                                sync_df_to_sheet(_gv_aux_table(table, "caja"), _gv_cash_for_sheet(destination_cash))
                                sync_df_to_sheet(_gv_aux_table(table, "resumen"), destination_summary)
                                sync_df_to_sheet(_gv_aux_table(table, "matriz"), destination_raw)
                                st.cache_data.clear()
                                st.success(
                                    f"Importación completa: {len(destination_patients)} pacientes y "
                                    f"{len(destination_cash)} movimientos disponibles en Google Sheets."
                                )
                                st.rerun()
                            except Exception as error:
                                st.error("No se pudo guardar la importación. La base anterior quedó respaldada.")
                                st.exception(error)
            except Exception as error:
                st.error("No se pudo abrir el archivo. Si es .xls antiguo, guardalo como .xlsx.")
                st.exception(error)

    with tabs[6]:
        st.markdown("### Cuadro original por hoja")
        if not raw_long.empty:
            source_sheets = sorted(raw_long["hoja_origen"].dropna().astype(str).unique().tolist())
            selected_original = st.selectbox("Hoja", source_sheets, key=f"gv_original_sheet_{table}")
            matrix = _gv_raw_matrix(raw_long, selected_original)
            st.caption("Vista reconstruida celda por celda desde Google Sheets. No altera los datos normalizados.")
            st.dataframe(matrix, use_container_width=True, height=680)
        elif not fallback_matrix.empty:
            display_fallback = fallback_matrix.copy()
            display_fallback.columns = [_gv_excel_column(index) for index in range(len(display_fallback.columns))]
            display_fallback.index = range(1, len(display_fallback) + 1)
            st.warning("La hoja principal todavía conserva un formato antiguo. Importá el Excel para guardar el respaldo matricial permanente.")
            st.dataframe(display_fallback, use_container_width=True, height=680)
        else:
            st.info("Todavía no hay una matriz original guardada.")

    with tabs[7]:
        ai_col, export_col = st.columns([1.3, 1.0])
        with ai_col:
            st.markdown("### Analista IA de Gine Vitae")
            question = st.text_area(
                "Pregunta",
                placeholder="Ej.: ¿Qué pacientes requieren acción inmediata? ¿Cuánto se cobró por jornada?",
                height=120,
                key=f"gv_ai_question_{table}",
            )
            if st.button("🧠 Analizar", type="primary", use_container_width=True, key=f"gv_ai_button_{table}"):
                if not question.strip():
                    st.warning("Escribí una pregunta.")
                elif data.empty:
                    st.warning("No hay pacientes para analizar.")
                else:
                    ai_data = data[[
                        "paciente", "fecha_plan", "efectivo", "mercado_pago", "total_cobrado",
                        "factura", "autorizado", "entregado", "estado_operativo", "observaciones", "hoja_origen",
                    ]].copy().head(1500)
                    ai_data["fecha_plan"] = pd.to_datetime(ai_data["fecha_plan"], errors="coerce").dt.strftime("%Y-%m-%d")
                    with st.spinner("Analizando pacientes, cobros y pendientes..."):
                        try:
                            answer = preguntar_ia(modulo=module_name, df=ai_data, pregunta=question)
                            st.success(answer)
                        except Exception as error:
                            st.error(f"No se pudo consultar la IA: {error}")
        with export_col:
            st.markdown("### Exportación ejecutiva")
            excel_bytes = _gv_export_excel(patients, cash, summary, raw_long)
            st.download_button(
                "📗 Descargar Excel completo",
                data=excel_bytes,
                file_name=f"gine_vitae_{date.today().isoformat()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"gv_export_xlsx_{table}",
            )
            st.download_button(
                "📄 Descargar pacientes CSV",
                data=_gv_patients_for_sheet(patients).to_csv(index=False).encode("utf-8-sig"),
                file_name=f"gine_vitae_pacientes_{date.today().isoformat()}.csv",
                mime="text/csv",
                use_container_width=True,
                key=f"gv_export_csv_{table}",
            )
            st.info(
                "El Excel incluye pacientes, caja, resumen financiero y cada hoja original importada."
            )


def render_facturacion_pro(module_name: str, cfg: Dict[str, Any]) -> None:
    st.session_state["vitae_current_module"] = str(module_name)
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

    # DEUDAS IMPOSITIVAS VM / VMR: lector especializado de Google Sheets e
    # importador Excel con selección de hojas, cuadros laterales, celdas
    # combinadas, vencimientos, edición y registro de pagos.
    identity_deuda_impositiva = _di_vm_norm(f"{table} {module_name}")
    is_deuda_impositiva = (
        str(table).lower() in {"deudas_impositivas_vm", "deudas_impositivas_vmr"}
        or "impositiv" in identity_deuda_impositiva
    )
    if is_deuda_impositiva and re.search(r"\bvmr\b", identity_deuda_impositiva):
        render_deudas_impositivas_vmr_pro(
            df_original=df_base.copy(),
            table=table,
            module_name=module_name,
        )
        return
    if is_deuda_impositiva and re.search(r"\bvm\b", identity_deuda_impositiva):
        render_deudas_impositivas_vm_pro(
            df_original=df_base.copy(),
            table=table,
            module_name=module_name,
        )
        return


    # GINE VITAE: lector especializado del cuadro doble (planes/pacientes + caja),
    # importación por hoja y conservación permanente de la matriz original.
    identity_gine = _gv_norm(f"{table} {module_name}")
    is_gine_module = (
        str(table).lower() in {"gine_vitae", "ginevitae", "planes_gine_vitae"}
        or "gine vitae" in identity_gine
        or "ginevitae" in identity_gine.replace(" ", "")
    )
    if is_gine_module:
        render_gine_vitae_pro(
            df_original=df_base.copy(),
            table=table,
            module_name=module_name,
        )
        return

    # PLANES DE PAGOS Y PRÉSTAMOS: cronograma mensual especializado,
    # importación multihija y conservación del cuadro original.
    identity_planes = _pp_norm(f"{table} {module_name}")
    is_planes_module = (
        str(table).lower() in {
            "planes_pagos_prestamos",
            "planes_de_pagos_prestamos",
            "planes_pago_prestamos",
        }
        or ("plan" in identity_planes and ("pago" in identity_planes or "prestamo" in identity_planes))
    )
    if is_planes_module:
        render_planes_pagos_prestamos_pro(
            df_original=df_base.copy(),
            table=table,
            module_name=module_name,
        )
        return

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
            # Cuenta Corriente VMR posee filtros propios para ARS y USD.
            # Se entrega el historial completo y se evita ejecutar apply_filters(),
            # porque ese filtro genérico duplicaba controles encima del panel.
            if table == "cuenta_corriente_vmr":
                render_cuenta_corriente_pro(df_panel.copy(), table)
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

def _render_cuenta_corriente_pro_legacy(df, table=""):
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


def render_cuenta_corriente_vmr_pro(df):
    """Centro financiero VMR para compromisos y créditos en ARS y USD."""
    import io
    import re
    import unicodedata

    import numpy as np
    import pandas as pd
    import plotly.express as px
    import streamlit as st

    if df is None or df.empty:
        st.info("Todavía no hay registros cargados en Cuenta Corriente VMR.")
        return

    key = "cc_vmr_hyper_pro"
    raw = df.copy()

    def _norm(value):
        text = unicodedata.normalize("NFKD", str(value or ""))
        text = text.encode("ascii", "ignore").decode("ascii").lower()
        return re.sub(r"[^a-z0-9]+", "_", text).strip("_")

    def _clean_text(value):
        if pd.isna(value):
            return ""
        return str(value).strip()

    def _parse_number(value):
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return np.nan
        if isinstance(value, (int, float, np.integer, np.floating)):
            return float(value)
        text = str(value).strip()
        if not text or text.lower() in {"nan", "none", "null", "-", "s/d"}:
            return np.nan
        negative = text.startswith("(") and text.endswith(")")
        text = (
            text.replace("US$", "")
            .replace("U$S", "")
            .replace("USD", "")
            .replace("ARS", "")
            .replace("$", "")
            .replace(" ", "")
            .replace("(", "")
            .replace(")", "")
        )
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            pieces = text.split(",")
            if len(pieces[-1]) in {1, 2}:
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif text.count(".") > 1:
            text = text.replace(".", "")
        text = re.sub(r"[^0-9.\-]", "", text)
        try:
            number = float(text)
            return -number if negative else number
        except (TypeError, ValueError):
            return np.nan

    def _money(value, symbol="$", decimals=2):
        number = float(value or 0)
        text = f"{number:,.{decimals}f}"
        text = text.replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{symbol} {text}"

    def _pct(value):
        return f"{max(0.0, min(float(value or 0), 1.0)) * 100:.1f}%"

    raw.columns = [_norm(column) for column in raw.columns]
    raw = raw.loc[:, ~raw.columns.duplicated()].copy()

    column_map = {_norm(column): column for column in raw.columns}

    def _first_column(candidates):
        for candidate in candidates:
            found = column_map.get(_norm(candidate))
            if found is not None:
                return found
        return None

    def _text_series(candidates, default=""):
        column = _first_column(candidates)
        if column is None:
            return pd.Series([default] * len(raw), index=raw.index, dtype="object")
        series = raw.loc[:, column]
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        return series.apply(_clean_text)

    def _number_series(candidates):
        column = _first_column(candidates)
        if column is None:
            return pd.Series(np.nan, index=raw.index, dtype="float64")
        series = raw.loc[:, column]
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        return series.apply(_parse_number).astype("float64")

    def _date_series(candidates):
        column = _first_column(candidates)
        if column is None:
            return pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]")
        series = raw.loc[:, column]
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        series = series.replace("", pd.NA)
        try:
            result = pd.to_datetime(series, errors="coerce", format="mixed", dayfirst=True)
        except (TypeError, ValueError):
            result = pd.to_datetime(series, errors="coerce", dayfirst=True)
        try:
            if getattr(result.dt, "tz", None) is not None:
                result = result.dt.tz_localize(None)
        except Exception:
            pass
        return result

    def _reconstruct_currency(import_candidates, paid_candidates, balance_candidates):
        explicit_column = _first_column(import_candidates)
        paid_column = _first_column(paid_candidates)
        balance_column = _first_column(balance_candidates)

        explicit = _number_series(import_candidates)
        raw_paid = _number_series(paid_candidates)
        raw_balance = _number_series(balance_candidates)

        if explicit_column is not None:
            amount = explicit.fillna(0).clip(lower=0)
            paid = raw_paid.fillna(0).clip(lower=0)
            calculated = (amount - paid).clip(lower=0)
            balance = raw_balance.where(raw_balance.notna(), calculated).fillna(0).clip(lower=0)
            amount = pd.concat([amount, paid + balance], axis=1).max(axis=1)
            paid = (amount - balance).clip(lower=0)
            return amount, paid, balance, "columnas explícitas"

        paid_values = raw_paid.fillna(0).clip(lower=0)
        balance_values = raw_balance.fillna(0).clip(lower=0)
        comparable = (paid_values > 0.01) & (balance_values > 0.01)
        if comparable.any():
            legacy_ratio = (balance_values[comparable] <= paid_values[comparable] + 0.01).mean()
            legacy_mode = bool(legacy_ratio >= 0.60)
        else:
            legacy_mode = True

        if legacy_mode:
            amount = pd.concat([paid_values, balance_values], axis=1).max(axis=1)
            balance = balance_values
            paid = (amount - balance).clip(lower=0)
            mode = "formato histórico VMR"
        else:
            amount = paid_values + balance_values
            paid = paid_values
            balance = balance_values
            mode = "pago más saldo"

        if paid_column is None and balance_column is None:
            amount = pd.Series(0.0, index=raw.index)
            paid = pd.Series(0.0, index=raw.index)
            balance = pd.Series(0.0, index=raw.index)
            mode = "sin columnas"

        return amount, paid, balance, mode

    data = pd.DataFrame(index=raw.index)
    data["_fecha"] = _date_series(["fecha", "mes", "fecha_factura", "created_at"])
    data["_vencimiento"] = _date_series(
        ["vencimiento", "fecha_vencimiento", "vence", "fecha_limite"]
    )
    data["_entidad"] = _text_series(
        ["entidad", "proveedor", "acreedor", "persona_entidad", "cliente"],
        "Sin entidad",
    ).replace("", "Sin entidad")
    data["_documento"] = _text_series(
        ["factura", "numero_factura", "comprobante", "concepto", "detalle", "documento"]
    )
    data["_tipo"] = _text_series(["tipo", "naturaleza", "movimiento"], "")
    data["_estado_planilla"] = _text_series(["estado", "situacion"], "")
    data["_observaciones"] = _text_series(["observaciones", "nota", "notas", "comentario"])
    data["_responsable"] = _text_series(["responsable", "usuario", "cargado_por"])

    amount_ars, paid_ars, balance_ars, mode_ars = _reconstruct_currency(
        ["importe_ars", "importe", "monto_ars", "monto", "total_ars", "total", "deuda_ars"],
        ["pagado_ars", "pago_ars", "abonado_ars", "pagado", "pago"],
        ["saldo_ars", "saldo", "pendiente_ars", "deuda_pendiente_ars", "a_pagar_ars"],
    )
    amount_usd, paid_usd, balance_usd, mode_usd = _reconstruct_currency(
        ["importe_usd", "monto_usd", "total_usd", "deuda_usd"],
        ["pagado_usd", "pago_usd", "abonado_usd"],
        ["saldo_usd", "pendiente_usd", "deuda_pendiente_usd", "a_pagar_usd"],
    )

    data["_importe_ars"] = amount_ars
    data["_pagado_ars"] = paid_ars
    data["_saldo_ars"] = balance_ars
    data["_importe_usd"] = amount_usd
    data["_pagado_usd"] = paid_usd
    data["_saldo_usd"] = balance_usd

    data["_tipo_norm"] = (data["_tipo"] + " " + data["_estado_planilla"]).map(_norm)
    data["_es_cobrar"] = data["_tipo_norm"].str.contains(
        r"cobrar|credito_cliente|cuenta_por_cobrar|a_favor", regex=True, na=False
    )
    data["_es_anulado"] = data["_tipo_norm"].str.contains(
        r"anulad|cancelad|baja", regex=True, na=False
    )

    data["_saldo_ars_activo"] = data["_saldo_ars"].where(~data["_es_anulado"], 0.0)
    data["_saldo_usd_activo"] = data["_saldo_usd"].where(~data["_es_anulado"], 0.0)
    data["_pendiente"] = (
        (data["_saldo_ars_activo"] > 0.01) | (data["_saldo_usd_activo"] > 0.01)
    )

    has_ars = (
        data[["_importe_ars", "_pagado_ars", "_saldo_ars"]].abs().max(axis=1) > 0.01
    )
    has_usd = (
        data[["_importe_usd", "_pagado_usd", "_saldo_usd"]].abs().max(axis=1) > 0.01
    )
    data["_moneda"] = np.select(
        [has_ars & has_usd, has_ars, has_usd],
        ["Mixta", "ARS", "USD"],
        default="Sin importe",
    )

    today = pd.Timestamp.today().normalize()
    data["_dias"] = (data["_vencimiento"] - today).dt.days
    data["_situacion"] = "Pendiente"
    data.loc[data["_es_anulado"], "_situacion"] = "Anulado"
    data.loc[~data["_pendiente"] & ~data["_es_anulado"], "_situacion"] = "Pagado"
    data.loc[
        data["_pendiente"] & data["_vencimiento"].isna(), "_situacion"
    ] = "Sin vencimiento"
    data.loc[
        data["_pendiente"] & data["_vencimiento"].notna() & (data["_dias"] < 0),
        "_situacion",
    ] = "Vencido"
    data.loc[
        data["_pendiente"] & data["_dias"].between(0, 7, inclusive="both"),
        "_situacion",
    ] = "Vence en 7 días"
    data.loc[
        data["_pendiente"] & data["_dias"].between(8, 30, inclusive="both"),
        "_situacion",
    ] = "Vence en 30 días"

    data["_antiguedad"] = "Sin vencimiento"
    data.loc[~data["_pendiente"], "_antiguedad"] = "Pagado"
    data.loc[data["_pendiente"] & data["_dias"].between(0, 30), "_antiguedad"] = "Vence 0–30 días"
    data.loc[data["_pendiente"] & (data["_dias"] > 30), "_antiguedad"] = "Vence a más de 30 días"
    data.loc[data["_pendiente"] & data["_dias"].between(-30, -1), "_antiguedad"] = "Vencida 1–30 días"
    data.loc[data["_pendiente"] & data["_dias"].between(-60, -31), "_antiguedad"] = "Vencida 31–60 días"
    data.loc[data["_pendiente"] & data["_dias"].between(-90, -61), "_antiguedad"] = "Vencida 61–90 días"
    data.loc[data["_pendiente"] & (data["_dias"] < -90), "_antiguedad"] = "Vencida +90 días"

    data["_prioridad"] = "Baja"
    data.loc[data["_situacion"].eq("Sin vencimiento"), "_prioridad"] = "Media"
    data.loc[data["_situacion"].eq("Vence en 30 días"), "_prioridad"] = "Media"
    data.loc[data["_situacion"].eq("Vence en 7 días"), "_prioridad"] = "Alta"
    data.loc[data["_situacion"].eq("Vencido"), "_prioridad"] = "Alta"
    data.loc[data["_pendiente"] & (data["_dias"] < -30), "_prioridad"] = "Crítica"
    data.loc[~data["_pendiente"], "_prioridad"] = "Cerrada"
    data.loc[data["_es_anulado"], "_prioridad"] = "Anulada"

    rate_series = _number_series(
        ["tipo_cambio", "cotizacion", "cotizacion_usd", "dolar", "tc"]
    ).dropna()
    suggested_rate = float(rate_series[rate_series > 0].median()) if (rate_series > 0).any() else 0.0

    st.markdown(
        """
        <style>
        .ccvmr-hero, .ccvmr-hero * {
            box-sizing: border-box !important;
        }
        .ccvmr-hero {
            width: 100%;
            padding: 1.15rem 1.35rem;
            border-radius: 18px;
            background: linear-gradient(120deg, #0f172a 0%, #17233a 55%, #24324a 100%);
            border: 1px solid rgba(148,163,184,.24);
            box-shadow: 0 12px 30px rgba(15,23,42,.16);
            margin: .2rem 0 1rem 0;
            color: #f8fafc !important;
        }
        .ccvmr-hero .ccvmr-kicker {
            color: #93c5fd !important;
            font-size: .72rem !important;
            font-weight: 750 !important;
            letter-spacing: .16em !important;
            line-height: 1.35 !important;
            text-transform: uppercase !important;
            opacity: 1 !important;
        }
        .ccvmr-hero .ccvmr-title {
            color: #ffffff !important;
            font-size: 1.55rem !important;
            font-weight: 800 !important;
            line-height: 1.18 !important;
            margin: .28rem 0 .25rem 0 !important;
        }
        .ccvmr-hero .ccvmr-sub {
            color: #cbd5e1 !important;
            font-size: .93rem !important;
            line-height: 1.45 !important;
            opacity: 1 !important;
            margin: 0 !important;
            max-width: 820px;
        }
        .ccvmr-note, .ccvmr-note * {
            color: #172033 !important;
        }
        .ccvmr-note {
            padding: .85rem 1rem;
            border-radius: 14px;
            background: #f8fafc;
            border: 1px solid #dbe3ee;
            box-shadow: 0 4px 14px rgba(15,23,42,.05);
            margin: .55rem 0 .9rem 0;
            line-height: 1.55;
        }
        @media (max-width: 900px) {
            .ccvmr-hero {padding: 1rem 1.05rem; border-radius: 16px;}
            .ccvmr-hero .ccvmr-title {font-size: 1.3rem !important;}
            .ccvmr-hero .ccvmr-sub {font-size: .88rem !important;}
        }
        </style>
        <div class="ccvmr-hero">
            <div class="ccvmr-kicker">CENTRO FINANCIERO · ARS + USD</div>
            <div class="ccvmr-title">Posición financiera en pesos y dólares</div>
            <p class="ccvmr-sub">Compromisos, pagos, vencimientos y exposición de VMR, sin mezclar monedas.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    control1, control2, control3 = st.columns([1.0, 1.65, 1.0])
    with control1:
        situations = [
            "Todos", "Pendiente", "Vencido", "Vence en 7 días",
            "Vence en 30 días", "Sin vencimiento", "Pagado", "Anulado",
        ]
        selected_situation = st.selectbox("Situación", situations, key=f"{key}_situacion")
    with control2:
        entities = st.multiselect(
            "Entidad",
            sorted(data["_entidad"].dropna().unique().tolist()),
            placeholder="Todas las entidades",
            key=f"{key}_entidad",
        )
    with control3:
        selected_currency = st.selectbox(
            "Moneda", ["Todas", "ARS", "USD", "Mixta"], key=f"{key}_moneda"
        )

    control4, control5, control6 = st.columns([1.55, 1.05, 1.4])
    with control4:
        search = st.text_input(
            "Buscar", placeholder="Entidad, factura o concepto", key=f"{key}_buscar"
        ).strip().lower()
    with control5:
        horizon = st.selectbox(
            "Período",
            ["Todos", "Mes actual", "Mes anterior", "Últimos 90 días", "Año actual"],
            key=f"{key}_horizonte",
        )
    with control6:
        exchange_rate = st.number_input(
            "Cotización USD para consolidar",
            min_value=0.0,
            value=max(suggested_rate, 0.0),
            step=10.0,
            format="%.2f",
            key=f"{key}_tc",
            help="Opcional. No modifica el Sheet; solo calcula la exposición consolidada del panel.",
        )

    filtered = data.copy()
    if selected_situation == "Pendiente":
        filtered = filtered[filtered["_pendiente"] & ~filtered["_es_anulado"]]
    elif selected_situation != "Todos":
        filtered = filtered[filtered["_situacion"].eq(selected_situation)]
    if entities:
        filtered = filtered[filtered["_entidad"].isin(entities)]
    if selected_currency != "Todas":
        filtered = filtered[filtered["_moneda"].eq(selected_currency)]
    if search:
        searchable = (
            filtered["_entidad"] + " " + filtered["_documento"] + " "
            + filtered["_tipo"] + " " + filtered["_estado_planilla"] + " "
            + filtered["_observaciones"]
        ).str.lower()
        filtered = filtered[searchable.str.contains(search, regex=False, na=False)]

    reference_date = filtered["_fecha"].where(filtered["_fecha"].notna(), filtered["_vencimiento"])
    month_start = today.replace(day=1)
    previous_end = month_start - pd.Timedelta(days=1)
    previous_start = previous_end.replace(day=1)
    if horizon == "Mes actual":
        filtered = filtered[reference_date.between(month_start, today, inclusive="both")]
    elif horizon == "Mes anterior":
        filtered = filtered[reference_date.between(previous_start, previous_end, inclusive="both")]
    elif horizon == "Últimos 90 días":
        filtered = filtered[reference_date.between(today - pd.Timedelta(days=90), today, inclusive="both")]
    elif horizon == "Año actual":
        filtered = filtered[reference_date.dt.year.eq(today.year)]

    if filtered.empty:
        st.warning("No hay registros que coincidan con los filtros seleccionados.")
        return

    active = filtered[~filtered["_es_anulado"]].copy()
    pending = active[active["_pendiente"]].copy()
    overdue = pending[pending["_situacion"].eq("Vencido")].copy()
    due_7 = pending[pending["_situacion"].eq("Vence en 7 días")].copy()
    no_due = pending[pending["_situacion"].eq("Sin vencimiento")].copy()

    payable = pending[~pending["_es_cobrar"]]
    receivable = pending[pending["_es_cobrar"]]

    payable_ars = payable["_saldo_ars_activo"].sum()
    payable_usd = payable["_saldo_usd_activo"].sum()
    receivable_ars = receivable["_saldo_ars_activo"].sum()
    receivable_usd = receivable["_saldo_usd_activo"].sum()
    overdue_ars = overdue["_saldo_ars_activo"].sum()
    overdue_usd = overdue["_saldo_usd_activo"].sum()
    total_pending_ars = pending["_saldo_ars_activo"].sum()
    total_pending_usd = pending["_saldo_usd_activo"].sum()

    consolidated = total_pending_ars + total_pending_usd * exchange_rate
    total_original_ars = active["_importe_ars"].sum()
    total_original_usd = active["_importe_usd"].sum()
    total_paid_ars = active["_pagado_ars"].sum()
    total_paid_usd = active["_pagado_usd"].sum()
    progress_ars = total_paid_ars / total_original_ars if total_original_ars > 0 else 0.0
    progress_usd = total_paid_usd / total_original_usd if total_original_usd > 0 else 0.0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("A pagar ARS", _money(payable_ars))
    m2.metric("A pagar USD", _money(payable_usd, "USD"))
    m3.metric("A cobrar ARS", _money(receivable_ars))
    m4.metric("A cobrar USD", _money(receivable_usd, "USD"))

    m5, m6, m7, m8 = st.columns(4)
    m5.metric("Vencido ARS", _money(overdue_ars), delta=f"{len(overdue)} comprobantes", delta_color="inverse")
    m6.metric("Vencido USD", _money(overdue_usd, "USD"), delta=f"{len(overdue)} comprobantes", delta_color="inverse")
    m7.metric("Vencen en 7 días", len(due_7))
    if exchange_rate > 0:
        m8.metric("Exposición consolidada", _money(consolidated))
    else:
        m8.metric("Entidades con saldo", pending["_entidad"].nunique())

    if exchange_rate <= 0 and total_pending_usd > 0:
        st.caption("Ingresá un tipo de cambio para ver la exposición total consolidada en pesos.")

    concentration = 0.0
    top_entity = ""
    if not pending.empty:
        pending["_equiv_ars"] = pending["_saldo_ars_activo"] + pending["_saldo_usd_activo"] * exchange_rate
        concentration_base = pending["_equiv_ars"].sum() if exchange_rate > 0 else pending["_saldo_ars_activo"].sum()
        entity_concentration = pending.groupby("_entidad")["_equiv_ars"].sum().sort_values(ascending=False)
        if concentration_base > 0 and not entity_concentration.empty:
            concentration = float(entity_concentration.iloc[0] / concentration_base)
            top_entity = str(entity_concentration.index[0])

    alerts = []
    if len(overdue):
        alerts.append(
            f"🔴 {len(overdue)} comprobantes vencidos por {_money(overdue_ars)} y {_money(overdue_usd, 'USD')}."
        )
    if len(due_7):
        alerts.append(f"🟠 {len(due_7)} compromisos vencen dentro de los próximos 7 días.")
    if len(no_due):
        alerts.append(f"🟡 {len(no_due)} registros pendientes no tienen fecha de vencimiento.")
    if concentration >= 0.40 and top_entity:
        alerts.append(f"⚠️ {top_entity} concentra {concentration * 100:.1f}% de la exposición consolidada.")
    if not alerts:
        alerts.append("🟢 No se detectan alertas críticas con los filtros actuales.")

    st.markdown('<div class="ccvmr-note"><b>Lectura ejecutiva</b><br>' + "<br>".join(alerts) + "</div>", unsafe_allow_html=True)

    tab_summary, tab_due, tab_entities, tab_evolution, tab_detail = st.tabs(
        ["📊 Panorama", "⏰ Vencimientos", "🏢 Entidades", "📈 Evolución", "📋 Detalle"]
    )

    with tab_summary:
        p1, p2 = st.columns(2)
        with p1:
            st.markdown("#### Regularización en pesos")
            st.progress(min(max(progress_ars, 0.0), 1.0))
            st.caption(
                f"{_pct(progress_ars)} regularizado · {_money(total_paid_ars)} pagado sobre {_money(total_original_ars)} registrado."
            )
        with p2:
            st.markdown("#### Regularización en dólares")
            st.progress(min(max(progress_usd, 0.0), 1.0))
            st.caption(
                f"{_pct(progress_usd)} regularizado · {_money(total_paid_usd, 'USD')} pagado sobre {_money(total_original_usd, 'USD')} registrado."
            )

        c1, c2 = st.columns(2)
        status_counts = (
            active.groupby("_situacion", as_index=False)
            .size()
            .rename(columns={"_situacion": "Situación", "size": "Registros"})
        )
        with c1:
            st.markdown("#### Estado de la cartera")
            fig = px.pie(status_counts, names="Situación", values="Registros", hole=0.58)
            fig.update_layout(margin=dict(l=10, r=10, t=20, b=10), legend_title_text="")
            st.plotly_chart(fig, use_container_width=True, key=f"{key}_pie_status")

        exposure = pd.DataFrame(
            {
                "Moneda": ["ARS", "USD"],
                "Registrado": [total_original_ars, total_original_usd],
                "Pagado": [total_paid_ars, total_paid_usd],
                "Pendiente": [total_pending_ars, total_pending_usd],
            }
        ).melt(id_vars="Moneda", var_name="Concepto", value_name="Importe")
        with c2:
            st.markdown("#### Composición por moneda")
            fig = px.bar(exposure, x="Concepto", y="Importe", color="Moneda", barmode="group")
            fig.update_layout(margin=dict(l=10, r=10, t=20, b=10), legend_title_text="")
            st.plotly_chart(fig, use_container_width=True, key=f"{key}_bar_exposure")

        summary_rows = pd.DataFrame(
            [
                ["Cartera activa", len(active), active["_entidad"].nunique(), _money(total_pending_ars), _money(total_pending_usd, "USD")],
                ["Vencida", len(overdue), overdue["_entidad"].nunique(), _money(overdue_ars), _money(overdue_usd, "USD")],
                ["Próximos 7 días", len(due_7), due_7["_entidad"].nunique(), _money(due_7["_saldo_ars_activo"].sum()), _money(due_7["_saldo_usd_activo"].sum(), "USD")],
                ["Sin vencimiento", len(no_due), no_due["_entidad"].nunique(), _money(no_due["_saldo_ars_activo"].sum()), _money(no_due["_saldo_usd_activo"].sum(), "USD")],
            ],
            columns=["Segmento", "Registros", "Entidades", "Saldo ARS", "Saldo USD"],
        )
        st.markdown("#### Resumen de control")
        st.dataframe(summary_rows, use_container_width=True, hide_index=True)
        st.caption(f"Motor de lectura: ARS = {mode_ars}; USD = {mode_usd}. El Sheet no fue modificado.")

    with tab_due:
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Críticos", int(pending["_prioridad"].eq("Crítica").sum()))
        d2.metric("Alta prioridad", int(pending["_prioridad"].eq("Alta").sum()))
        d3.metric("Sin fecha", len(no_due))
        d4.metric("Próximos 30 días", int(pending["_dias"].between(0, 30).sum()))

        aging_order = [
            "Vencida +90 días", "Vencida 61–90 días", "Vencida 31–60 días",
            "Vencida 1–30 días", "Vence 0–30 días", "Vence a más de 30 días",
            "Sin vencimiento",
        ]
        aging = (
            pending.groupby("_antiguedad", as_index=False)
            .agg(Registros=("_entidad", "size"), Saldo_ARS=("_saldo_ars_activo", "sum"), Saldo_USD=("_saldo_usd_activo", "sum"))
        )
        aging["_orden"] = aging["_antiguedad"].map({name: i for i, name in enumerate(aging_order)}).fillna(99)
        aging = aging.sort_values("_orden").drop(columns="_orden").rename(columns={"_antiguedad": "Antigüedad"})
        st.markdown("#### Antigüedad de saldos")
        st.dataframe(aging, use_container_width=True, hide_index=True)

        schedule = pending.copy()
        schedule["_mes_vencimiento"] = schedule["_vencimiento"].dt.to_period("M").astype(str)
        schedule.loc[schedule["_vencimiento"].isna(), "_mes_vencimiento"] = "Sin fecha"
        monthly_due = (
            schedule.groupby("_mes_vencimiento", as_index=False)
            .agg(Saldo_ARS=("_saldo_ars_activo", "sum"), Saldo_USD=("_saldo_usd_activo", "sum"), Registros=("_entidad", "size"))
            .rename(columns={"_mes_vencimiento": "Mes"})
        )
        st.markdown("#### Calendario financiero")
        st.dataframe(monthly_due, use_container_width=True, hide_index=True)

        priority_rank = {"Crítica": 0, "Alta": 1, "Media": 2, "Baja": 3}
        priority = pending.copy()
        priority["_rank"] = priority["_prioridad"].map(priority_rank).fillna(9)
        priority = priority.sort_values(["_rank", "_vencimiento", "_saldo_ars_activo"], ascending=[True, True, False], na_position="last")
        priority_view = priority[
            ["_prioridad", "_vencimiento", "_dias", "_entidad", "_documento", "_moneda", "_saldo_ars_activo", "_saldo_usd_activo", "_situacion"]
        ].rename(columns={
            "_prioridad": "Prioridad", "_vencimiento": "Vencimiento", "_dias": "Días",
            "_entidad": "Entidad", "_documento": "Factura / concepto", "_moneda": "Moneda",
            "_saldo_ars_activo": "Saldo ARS", "_saldo_usd_activo": "Saldo USD", "_situacion": "Situación",
        })
        st.markdown("#### Cola de acción")
        st.dataframe(priority_view, use_container_width=True, hide_index=True, height=430)

    with tab_entities:
        entity_summary = (
            active.groupby("_entidad", as_index=False)
            .agg(
                Registros=("_documento", "size"),
                Importe_ARS=("_importe_ars", "sum"),
                Pagado_ARS=("_pagado_ars", "sum"),
                Saldo_ARS=("_saldo_ars_activo", "sum"),
                Importe_USD=("_importe_usd", "sum"),
                Pagado_USD=("_pagado_usd", "sum"),
                Saldo_USD=("_saldo_usd_activo", "sum"),
                Proximo_vencimiento=("_vencimiento", "min"),
            )
        )
        overdue_by_entity = overdue.groupby("_entidad").agg(
            Vencido_ARS=("_saldo_ars_activo", "sum"),
            Vencido_USD=("_saldo_usd_activo", "sum"),
        )
        entity_summary = entity_summary.join(overdue_by_entity, on="_entidad").fillna({"Vencido_ARS": 0.0, "Vencido_USD": 0.0})
        entity_summary["Equivalente_ARS"] = entity_summary["Saldo_ARS"] + entity_summary["Saldo_USD"] * exchange_rate
        entity_summary = entity_summary.rename(columns={"_entidad": "Entidad"}).sort_values(
            ["Equivalente_ARS", "Saldo_ARS", "Saldo_USD"], ascending=False
        )
        if exchange_rate > 0 and entity_summary["Equivalente_ARS"].sum() > 0:
            entity_summary["Participación"] = entity_summary["Equivalente_ARS"] / entity_summary["Equivalente_ARS"].sum()
        else:
            entity_summary["Participación"] = 0.0

        e1, e2 = st.columns(2)
        with e1:
            top_ars = entity_summary.nlargest(10, "Saldo_ARS").sort_values("Saldo_ARS")
            st.markdown("#### Principales saldos ARS")
            if top_ars["Saldo_ARS"].sum() > 0:
                fig = px.bar(top_ars, x="Saldo_ARS", y="Entidad", orientation="h")
                fig.update_layout(margin=dict(l=10, r=10, t=20, b=10))
                st.plotly_chart(fig, use_container_width=True, key=f"{key}_top_ars")
            else:
                st.info("No hay saldos pendientes en pesos.")
        with e2:
            top_usd = entity_summary.nlargest(10, "Saldo_USD").sort_values("Saldo_USD")
            st.markdown("#### Principales saldos USD")
            if top_usd["Saldo_USD"].sum() > 0:
                fig = px.bar(top_usd, x="Saldo_USD", y="Entidad", orientation="h")
                fig.update_layout(margin=dict(l=10, r=10, t=20, b=10))
                st.plotly_chart(fig, use_container_width=True, key=f"{key}_top_usd")
            else:
                st.info("No hay saldos pendientes en dólares.")

        entity_view = entity_summary.rename(columns={
            "Importe_ARS": "Importe ARS", "Pagado_ARS": "Pagado ARS", "Saldo_ARS": "Saldo ARS",
            "Importe_USD": "Importe USD", "Pagado_USD": "Pagado USD", "Saldo_USD": "Saldo USD",
            "Vencido_ARS": "Vencido ARS", "Vencido_USD": "Vencido USD",
            "Proximo_vencimiento": "Próximo vencimiento", "Equivalente_ARS": "Equivalente ARS",
        })
        st.markdown("#### Ficha consolidada por entidad")
        st.dataframe(entity_view, use_container_width=True, hide_index=True, height=430)

    with tab_evolution:
        evolution = active.copy()
        evolution["_periodo"] = evolution["_fecha"].where(evolution["_fecha"].notna(), evolution["_vencimiento"])
        evolution = evolution[evolution["_periodo"].notna()].copy()
        if evolution.empty:
            st.info("No hay fechas válidas para construir la evolución mensual.")
        else:
            evolution["Mes"] = evolution["_periodo"].dt.to_period("M").astype(str)
            monthly = evolution.groupby("Mes", as_index=False).agg(
                Importe_ARS=("_importe_ars", "sum"),
                Pagado_ARS=("_pagado_ars", "sum"),
                Saldo_ARS=("_saldo_ars_activo", "sum"),
                Importe_USD=("_importe_usd", "sum"),
                Pagado_USD=("_pagado_usd", "sum"),
                Saldo_USD=("_saldo_usd_activo", "sum"),
            ).sort_values("Mes")

            ev1, ev2 = st.columns(2)
            with ev1:
                ars_long = monthly.melt(
                    id_vars="Mes", value_vars=["Importe_ARS", "Pagado_ARS", "Saldo_ARS"],
                    var_name="Concepto", value_name="ARS",
                )
                ars_long["Concepto"] = ars_long["Concepto"].str.replace("_ARS", "", regex=False)
                st.markdown("#### Evolución ARS")
                fig = px.line(ars_long, x="Mes", y="ARS", color="Concepto", markers=True)
                fig.update_layout(margin=dict(l=10, r=10, t=20, b=10), legend_title_text="")
                st.plotly_chart(fig, use_container_width=True, key=f"{key}_evo_ars")
            with ev2:
                usd_long = monthly.melt(
                    id_vars="Mes", value_vars=["Importe_USD", "Pagado_USD", "Saldo_USD"],
                    var_name="Concepto", value_name="USD",
                )
                usd_long["Concepto"] = usd_long["Concepto"].str.replace("_USD", "", regex=False)
                st.markdown("#### Evolución USD")
                fig = px.line(usd_long, x="Mes", y="USD", color="Concepto", markers=True)
                fig.update_layout(margin=dict(l=10, r=10, t=20, b=10), legend_title_text="")
                st.plotly_chart(fig, use_container_width=True, key=f"{key}_evo_usd")

            st.markdown("#### Serie mensual")
            st.dataframe(monthly, use_container_width=True, hide_index=True)

    with tab_detail:
        detail = filtered[
            [
                "_fecha", "_vencimiento", "_dias", "_prioridad", "_entidad", "_documento",
                "_tipo", "_estado_planilla", "_situacion", "_moneda",
                "_importe_ars", "_pagado_ars", "_saldo_ars",
                "_importe_usd", "_pagado_usd", "_saldo_usd",
                "_responsable", "_observaciones",
            ]
        ].copy().rename(columns={
            "_fecha": "Fecha", "_vencimiento": "Vencimiento", "_dias": "Días",
            "_prioridad": "Prioridad", "_entidad": "Entidad", "_documento": "Factura / concepto",
            "_tipo": "Tipo", "_estado_planilla": "Estado planilla", "_situacion": "Situación calculada",
            "_moneda": "Moneda", "_importe_ars": "Importe ARS", "_pagado_ars": "Pagado ARS",
            "_saldo_ars": "Saldo ARS", "_importe_usd": "Importe USD", "_pagado_usd": "Pagado USD",
            "_saldo_usd": "Saldo USD", "_responsable": "Responsable", "_observaciones": "Observaciones",
        })
        priority_order = {"Crítica": 0, "Alta": 1, "Media": 2, "Baja": 3, "Cerrada": 4, "Anulada": 5}
        detail["_orden"] = detail["Prioridad"].map(priority_order).fillna(9)
        detail = detail.sort_values(["_orden", "Vencimiento", "Entidad"], na_position="last").drop(columns="_orden")

        q1, q2, q3, q4 = st.columns(4)
        q1.metric("Registros filtrados", len(detail))
        q2.metric("Con entidad", int(detail["Entidad"].ne("Sin entidad").sum()))
        q3.metric("Con vencimiento", int(detail["Vencimiento"].notna().sum()))
        q4.metric("Doble moneda", int(detail["Moneda"].eq("Mixta").sum()))

        st.dataframe(detail, use_container_width=True, hide_index=True, height=520)

        csv_bytes = detail.to_csv(index=False).encode("utf-8-sig")
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            detail.to_excel(writer, sheet_name="Detalle", index=False)
            entity_view.to_excel(writer, sheet_name="Entidades", index=False)
            aging.to_excel(writer, sheet_name="Vencimientos", index=False)
            summary_rows.to_excel(writer, sheet_name="Resumen", index=False)
        excel_buffer.seek(0)

        dl1, dl2 = st.columns(2)
        with dl1:
            st.download_button(
                "⬇️ Descargar detalle CSV",
                data=csv_bytes,
                file_name="cuenta_corriente_vmr_filtrada.csv",
                mime="text/csv",
                use_container_width=True,
                key=f"{key}_csv",
            )
        with dl2:
            st.download_button(
                "⬇️ Descargar informe Excel",
                data=excel_buffer.getvalue(),
                file_name="cuenta_corriente_vmr_informe.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"{key}_xlsx",
            )


def render_cuenta_corriente_pro(df, table=""):
    """Despachador seguro: VMR usa el centro doble moneda; VM conserva su panel anterior."""
    if str(table).lower().endswith("_vmr"):
        return render_cuenta_corriente_vmr_pro(df)
    return _render_cuenta_corriente_pro_legacy(df, table)


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
    st.session_state["vitae_current_module"] = "Configuración"
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
