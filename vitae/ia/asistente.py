"""Asistente Vitae: un único chat de IA en el menú lateral.

Reemplaza al Copiloto y a las consultas de IA que había dentro de cada
módulo. Acompaña al usuario en todas las pantallas (la conversación se
mantiene al cambiar de módulo), sabe en qué pantalla está y consulta los
datos con el motor de herramientas (vitae/ia/motor.py), limitado a los
módulos que el usuario tiene permitidos.
"""
from __future__ import annotations

from typing import Any, Callable

import streamlit as st

CLAVE = "asistente_vitae_mensajes"
MAX_MENSAJES = 40

SUGERENCIAS = [
    "¿Qué requiere mi atención hoy?",
    "¿Cuánto se facturó este mes en VMR y VM?",
    "¿Qué impuestos están vencidos?",
]

_CSS = """
<style>
section[data-testid="stSidebar"] .asis-titulo{display:flex;align-items:center;gap:.45rem;font-weight:650;font-size:.95rem;
  color:#5E1A39;margin:.2rem 0 .1rem}
section[data-testid="stSidebar"] .asis-sub{font-size:.75rem;color:#8A7F85;margin-bottom:.35rem}
section[data-testid="stSidebar"] [data-testid="stChatMessage"]{padding:.35rem .4rem;gap:.5rem}
section[data-testid="stSidebar"] [data-testid="stChatMessage"] p{font-size:.86rem;line-height:1.4}
</style>
"""


def mensajes() -> list[dict[str, str]]:
    return st.session_state.setdefault(CLAVE, [])


def _historial_para_motor(lista: list[dict[str, str]]) -> list[dict[str, str]]:
    """Pares pregunta/respuesta para dar contexto a la IA."""
    pares, pregunta = [], None
    for m in lista:
        if m["rol"] == "user":
            pregunta = m["texto"]
        elif pregunta is not None:
            pares.append({"pregunta": pregunta, "respuesta": m["texto"]})
            pregunta = None
    return pares[-6:]


def responder(pregunta: str, modulo_actual: str, historial: list[dict[str, str]],
              permitidos: set[str] | None) -> str:
    """Motor con herramientas; si no está disponible, el motor anterior."""
    from vitae.ia import copiloto

    if copiloto._motor_disponible():
        try:
            from vitae.ia.motor import responder as motor_responder

            return motor_responder(pregunta, modulo_actual, historial=historial, modulos_permitidos=permitidos)
        except Exception as exc:  # cuota, modelo, red: se informa y se prueba el respaldo
            ultimo = exc
        else:  # pragma: no cover
            ultimo = None
    else:
        ultimo = None
    if permitidos is not None:
        # El motor anterior lee todos los módulos sin mirar permisos: sólo se
        # usa como respaldo para quien puede ver todo.
        raise ultimo or RuntimeError("La IA no está disponible en este momento.")
    try:
        return copiloto._preguntar_copiloto_base(pregunta, modulo_actual)
    except Exception as exc:
        raise ultimo or exc


def _limpiar() -> None:
    st.session_state[CLAVE] = []


def _sugerir(texto: str) -> None:
    st.session_state["asistente_vitae_pendiente"] = texto


def render_asistente(
    modulo_actual: str,
    *,
    permitidos: set[str] | None = None,
    responder_fn: Callable[[str, str, list[dict[str, str]], set[str] | None], str] | None = None,
    alto: int = 380,
) -> None:
    """Chat en el menú lateral (en el celular, dentro del menú desplegable)."""
    responder_fn = responder_fn or responder
    lista = mensajes()
    with st.sidebar:
        st.markdown(_CSS, unsafe_allow_html=True)
        st.divider()
        st.markdown('<div class="asis-titulo">✦ Asistente Vitae</div>'
                    f'<div class="asis-sub">Estás en: {modulo_actual}</div>', unsafe_allow_html=True)
        caja = st.container(height=alto, border=False)
        with caja:
            if not lista:
                st.caption("Preguntame lo que necesites sobre Vitae: facturación, cajas, deudas, pacientes, "
                           "agenda… Consulto los datos al momento.")
                for i, s in enumerate(SUGERENCIAS):
                    st.button(s, key=f"asistente_sug_{i}", on_click=_sugerir, args=(s,), use_container_width=True)
            for m in lista:
                with st.chat_message("user" if m["rol"] == "user" else "assistant",
                                     avatar=":material/person:" if m["rol"] == "user" else ":material/auto_awesome:"):
                    st.markdown(m["texto"])
        pregunta = st.chat_input("Escribile al asistente…", key="asistente_vitae_input")
        pregunta = pregunta or st.session_state.pop("asistente_vitae_pendiente", None)
        if pregunta:
            historial = _historial_para_motor(lista)
            lista.append({"rol": "user", "texto": pregunta})
            with caja:
                with st.chat_message("user", avatar=":material/person:"):
                    st.markdown(pregunta)
                with st.chat_message("assistant", avatar=":material/auto_awesome:"):
                    with st.spinner("Consultando los datos…"):
                        try:
                            texto = responder_fn(pregunta, modulo_actual, historial, permitidos)
                        except Exception as exc:
                            texto = ("No pude responder en este momento "
                                     f"({type(exc).__name__}). Probá de nuevo en unos segundos.")
                    st.markdown(texto)
            lista.append({"rol": "assistant", "texto": texto})
            del lista[:-MAX_MENSAJES]
        if lista:
            st.button("Nueva conversación", key="asistente_vitae_nueva", on_click=_limpiar,
                      icon=":material/add_comment:", use_container_width=True)
