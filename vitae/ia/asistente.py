"""Asistente Vitae: un único chat de IA, a mano en todas las pantallas.

Un botón «🤖 Asistente» fijo abajo a la derecha abre el chat en una ventana.
Reemplaza al Copiloto y a las consultas de IA que había dentro de cada
módulo. La conversación se mantiene al cambiar de módulo, sabe en qué
pantalla está el usuario y consulta los datos con el motor de herramientas
(vitae/ia/motor.py), limitado a los módulos que el usuario tiene permitidos.
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


_CSS_BOTON = """
<style>
.st-key-asistente_flotante{position:fixed;right:24px;bottom:24px;z-index:999990;width:auto!important}
.st-key-asistente_flotante button{border-radius:999px!important;padding:.55rem 1.15rem!important;
  background:#8C2853!important;color:#fff!important;border:none!important;
  box-shadow:0 10px 26px rgba(94,26,57,.32)!important;font-weight:650!important}
.st-key-asistente_flotante button p{color:#fff!important;font-size:1rem!important}
.st-key-asistente_flotante button:hover{background:#6F1F42!important;transform:translateY(-1px)}
@media (max-width: 640px){.st-key-asistente_flotante{right:14px;bottom:14px}}
</style>
"""


def _chat(modulo_actual: str, permitidos: set[str] | None,
          responder_fn: Callable[[str, str, list[dict[str, str]], set[str] | None], str], alto: int) -> None:
    """La conversación: mensajes, sugerencias y cuadro para escribir."""
    lista = mensajes()
    st.markdown(_CSS, unsafe_allow_html=True)
    st.caption(f"Estás en: {modulo_actual} · consulto los datos de Vitae en el momento.")
    caja = st.container(height=alto, border=True)
    with caja:
        if not lista:
            st.markdown("Hola 👋 Preguntame lo que necesites: facturación, cajas, deudas, pacientes, agenda…")
            for i, sug in enumerate(SUGERENCIAS):
                st.button(sug, key=f"asistente_sug_{i}", on_click=_sugerir, args=(sug,), use_container_width=True)
        for m in lista:
            with st.chat_message("user" if m["rol"] == "user" else "assistant",
                                 avatar=":material/person:" if m["rol"] == "user" else "🤖"):
                st.markdown(m["texto"])
    # Formulario propio: el mensaje se envía con «Enviar» en un solo paso.
    with st.form("asistente_vitae_form", clear_on_submit=True, border=False):
        texto = st.text_area("Mensaje para el asistente", key="asistente_vitae_texto", height=80,
                             placeholder="Escribí tu pregunta…", label_visibility="collapsed")
        c1, c2 = st.columns([3, 1])
        enviar = c1.form_submit_button("Enviar", type="primary", icon=":material/send:", use_container_width=True)
        nueva = c2.form_submit_button("Nueva", icon=":material/add_comment:", use_container_width=True,
                                      help="Empezar una conversación nueva")
    if nueva:
        _limpiar()
        st.rerun(scope="fragment") if _en_dialogo() else st.rerun()
    pregunta = (texto or "").strip() if enviar else ""
    pregunta = pregunta or st.session_state.pop("asistente_vitae_pendiente", None)
    if pregunta:
        historial = _historial_para_motor(lista)
        lista.append({"rol": "user", "texto": pregunta})
        with caja:
            with st.chat_message("user", avatar=":material/person:"):
                st.markdown(pregunta)
            with st.chat_message("assistant", avatar="🤖"):
                with st.spinner("Consultando los datos…"):
                    try:
                        respuesta = responder_fn(pregunta, modulo_actual, historial, permitidos)
                    except Exception as exc:
                        respuesta = ("No pude responder en este momento "
                                     f"({type(exc).__name__}). Probá de nuevo en unos segundos.")
                st.markdown(respuesta)
        lista.append({"rol": "assistant", "texto": respuesta})
        del lista[:-MAX_MENSAJES]


def _en_dialogo() -> bool:
    return bool(st.session_state.get("_asistente_en_dialogo"))


def _abrir_dialogo(modulo_actual: str, permitidos: set[str] | None,
                   responder_fn: Callable[[str, str, list[dict[str, str]], set[str] | None], str]) -> None:
    def contenido() -> None:
        st.session_state["_asistente_en_dialogo"] = True
        _chat(modulo_actual, permitidos, responder_fn, 420)

    try:
        dialogo = st.dialog("🤖 Asistente Vitae", width="large")(contenido)
    except Exception:  # sin ventanas emergentes: se muestra en la página
        dialogo = None
    if callable(dialogo):
        dialogo()
    else:
        with st.container(border=True):
            st.markdown("#### 🤖 Asistente Vitae")
            contenido()


def boton_asistente(
    modulo_actual: str,
    *,
    permitidos: set[str] | None = None,
    responder_fn: Callable[[str, str, list[dict[str, str]], set[str] | None], str] | None = None,
) -> None:
    """Botón «🤖 Asistente» fijo abajo a la derecha; abre el chat en una ventana."""
    responder_fn = responder_fn or responder
    st.session_state["_asistente_en_dialogo"] = False
    st.markdown(_CSS_BOTON, unsafe_allow_html=True)
    with st.container(key="asistente_flotante"):
        abrir = st.button("🤖 Asistente", key="asistente_abrir", help="Asistente Vitae: preguntale lo que necesites")
    if abrir:
        _abrir_dialogo(modulo_actual, permitidos, responder_fn)


def render_asistente(
    modulo_actual: str,
    *,
    permitidos: set[str] | None = None,
    responder_fn: Callable[[str, str, list[dict[str, str]], set[str] | None], str] | None = None,
    alto: int = 380,
) -> None:
    """Chat directo (sin botón), por ejemplo para pruebas o una página propia."""
    _chat(modulo_actual, permitidos, responder_fn or responder, alto)
