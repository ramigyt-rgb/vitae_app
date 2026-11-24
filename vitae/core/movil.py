"""Ajustes para celulares.

Dos capas:

1. CSS responsive (``css_movil``): se aplica solo en pantallas angostas
   (≤ 640 px) y vale para todos los módulos: márgenes, tamaños de títulos,
   indicadores de a dos por fila, pestañas deslizables, campos que no hacen
   zoom en iPhone, botones cómodos para el dedo.

2. Detección del dispositivo (``es_movil``) por User-Agent, para lo que el CSS
   no puede resolver: gráficos más bajos, sin zoom por arrastre (que en el
   celular "secuestra" el scroll) y tablas menos altas. Se instala una sola
   vez con ``instalar_ajustes_moviles()`` desde app.py.

Para probar la vista de celular desde la computadora: agregar ``?vista=movil``
a la dirección de la app.
"""
from __future__ import annotations

import re
from typing import Any

import streamlit as st

_PATRON_MOVIL = re.compile(r"Mobi|Android|iPhone|iPod|Windows Phone", re.I)

ALTO_GRAFICO_MOVIL = 300
ALTO_TABLA_MOVIL = 360


def es_movil() -> bool:
    """True si la sesión viene de un celular (o si se forzó con ?vista=movil)."""
    try:
        forzado = str(st.query_params.get("vista", "")).lower()
        if forzado in {"movil", "mobile"}:
            return True
        if forzado in {"escritorio", "desktop"}:
            return False
    except Exception:
        pass
    try:
        agente = st.context.headers.get("User-Agent", "") or ""
        # Las tablets (iPad, Android sin "Mobile") se tratan como escritorio.
        return isinstance(agente, str) and bool(_PATRON_MOVIL.search(agente))
    except Exception:
        return False


def ajustar_figura_movil(fig: Any) -> Any:
    """Gráfico Plotly más compacto y fácil de leer en el celular."""
    if not hasattr(fig, "update_layout"):
        return fig
    alto = getattr(fig.layout, "height", None)
    fig.update_layout(
        height=min(int(alto), ALTO_GRAFICO_MOVIL) if alto else ALTO_GRAFICO_MOVIL,
        margin=dict(l=4, r=4, t=36, b=4),
        font=dict(size=11),
        dragmode=False,  # deslizar el dedo mueve la página, no el gráfico
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=10)),
        title=dict(font=dict(size=13)),
    )
    try:
        fig.update_xaxes(automargin=True, tickfont=dict(size=10), fixedrange=True)
        fig.update_yaxes(automargin=True, tickfont=dict(size=10), fixedrange=True)
    except Exception:
        pass
    return fig


def instalar_ajustes_moviles() -> None:
    """Envuelve st.plotly_chart y st.dataframe para adaptarlos en celulares.

    En escritorio no cambia nada. Ante cualquier error se usa la función
    original, así que nunca rompe una pantalla.
    """
    try:
        from streamlit.delta_generator import DeltaGenerator
    except Exception:
        return  # versión de Streamlit distinta: la app sigue igual, solo sin este ajuste

    if getattr(DeltaGenerator, "_vitae_movil", False):
        return

    plotly_original = DeltaGenerator.plotly_chart
    dataframe_original = DeltaGenerator.dataframe

    def plotly_chart(self, figure_or_data=None, *args, **kwargs):
        if es_movil():
            try:
                figure_or_data = ajustar_figura_movil(figure_or_data)
                config = dict(kwargs.get("config") or {})
                config.update(displayModeBar=False, scrollZoom=False, responsive=True)
                kwargs["config"] = config
            except Exception:
                pass
        return plotly_original(self, figure_or_data, *args, **kwargs)

    def dataframe(self, data=None, *args, **kwargs):
        if es_movil():
            alto = kwargs.get("height")
            if isinstance(alto, (int, float)) and alto > ALTO_TABLA_MOVIL:
                kwargs["height"] = ALTO_TABLA_MOVIL
        return dataframe_original(self, data, *args, **kwargs)

    DeltaGenerator.plotly_chart = plotly_chart
    DeltaGenerator.dataframe = dataframe
    DeltaGenerator._vitae_movil = True

    # st.plotly_chart / st.dataframe son métodos ya enlazados al contenedor
    # principal: se vuelven a enlazar para que también usen la versión nueva.
    principal = getattr(st, "_main", None)
    if principal is not None:
        st.plotly_chart = plotly_chart.__get__(principal, DeltaGenerator)
        st.dataframe = dataframe.__get__(principal, DeltaGenerator)


# Títulos grandes de los distintos módulos (≥ 24 px en escritorio).
_TITULOS = [
    "gv-title", "dt-title", "pp-title", "dg-title", "agenda-hero-title", "farmacia-titulo",
    "fx-head-title", "vitae-page-title", "empresas-title", "qx-title", "fx-title", "empresa-codigo",
    "banco-pro-title", "caja-pro-title", "ccvmr-title",
]
_VALORES = ["dt-card-value", "pp-card-value", "gv-card-value", "fx-value"]
_HEROES = [
    "agenda-hero", "banco-pro-hero", "caja-pro-hero", "ccvmr-hero", "cv-hero", "dg-hero", "di-vm-hero",
    "dt-hero", "farmacia-hero", "fx-hero", "gv-hero", "pp-hero", "qx-hero", "empresa-header",
]


def css_movil() -> str:
    titulos = ", ".join(f".{c}" for c in _TITULOS)
    valores = ", ".join(f".{c}" for c in _VALORES)
    heroes = ", ".join(f".{c}" for c in _HEROES)
    bloques = [
        '[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [data-testid="stMetric"])',
        '[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .fx-card)',
        '[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .vitae-kpi)',
        '[data-testid="stHorizontalBlock"]:has(> [data-testid="column"] [data-testid="stMetric"])',
    ]
    fila_kpi = ", ".join(bloques)
    columnas_kpi = ", ".join(
        f'{b} > [data-testid="{c}"]' for b in bloques for c in ("stColumn", "column")
    )
    return f"""
    @media (max-width: 640px) {{
        .block-container {{ padding: 3.6rem .85rem 4.5rem .85rem !important; }}
        h1 {{ font-size: 1.4rem !important; }}
        h2 {{ font-size: 1.2rem !important; }}
        h3 {{ font-size: 1.05rem !important; }}
        {titulos} {{ font-size: 1.35rem !important; line-height: 1.15 !important; }}
        {valores} {{ font-size: 1.15rem !important; }}
        {heroes} {{ padding: 14px 16px !important; border-radius: 12px !important; margin-bottom: .75rem !important; }}

        /* Indicadores de a dos por fila en lugar de uno debajo del otro */
        {fila_kpi} {{ flex-wrap: wrap !important; gap: .5rem !important; }}
        {columnas_kpi} {{
            min-width: calc(50% - .25rem) !important; width: calc(50% - .25rem) !important;
            flex: 1 1 calc(50% - .25rem) !important;
        }}
        div[data-testid="stMetric"] {{ padding: .6rem .7rem !important; }}
        div[data-testid="stMetricValue"] {{ font-size: 1.15rem !important; }}
        div[data-testid="stMetricLabel"] p {{ font-size: .78rem !important; }}
        .fx-card, .vitae-kpi {{ padding: 10px 12px !important; }}
        .vitae-kpi-value {{ font-size: 1.15rem !important; }}
        .fx-card .fx-foot {{ font-size: .7rem !important; }}

        /* Pestañas: se deslizan de costado en lugar de amontonarse */
        div[data-testid="stTabs"] [data-baseweb="tab-list"] {{
            overflow-x: auto !important; flex-wrap: nowrap !important; scrollbar-width: none;
        }}
        div[data-testid="stTabs"] [data-baseweb="tab-list"]::-webkit-scrollbar {{ display: none; }}
        div[data-testid="stTabs"] button {{ white-space: nowrap !important; padding: .5rem .65rem !important; }}

        /* Botones cómodos para el dedo (mínimo 44 px) */
        .stButton > button, .stDownloadButton > button, .stFormSubmitButton > button,
        [data-testid="stPopover"] button {{ min-height: 2.75rem !important; }}

        /* iPhone hace zoom si el texto de un campo es menor a 16 px */
        input, textarea, select, div[data-baseweb="select"] input,
        div[data-baseweb="select"] div {{ font-size: 16px !important; }}

        div[data-testid="stPlotlyChart"] {{ padding: 0 !important; }}
        div[data-testid="stExpander"] summary {{ padding-top: .6rem; padding-bottom: .6rem; }}
        .vitae-login-wrap {{ margin-top: 2vh !important; }}
    }}
    """
