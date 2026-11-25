"""Asistente Vitae: «Vita», un chat de IA estilo Messenger en todas las pantallas.

Una ventanita fija abajo a la derecha, como el chat de Facebook de antes:
se minimiza a una barrita y se vuelve a abrir tocándola. Los mensajes van en
globitos (los tuyos a la derecha, los de Vita a la izquierda), con «En línea»
y «escribiendo…» mientras busca la respuesta. Vita contesta corto y cálido,
como alguien del equipo, y consulta los datos con el motor de herramientas
(vitae/ia/motor.py), limitado a los módulos que el usuario tiene permitidos.

La ventana es un fragmento de Streamlit: escribir, minimizar o abrir el chat
no recarga la pantalla que está debajo. La conversación se mantiene al cambiar
de módulo.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any, Callable

import streamlit as st

NOMBRE = "Vita"
CLAVE = "asistente_vitae_mensajes"
ABIERTO = "asis_abierto"
PENDIENTE = "asistente_vitae_pendiente"
MAX_MENSAJES = 40

SUGERENCIAS = [
    "¿Qué requiere mi atención hoy?",
    "¿Cuánto se facturó este mes?",
    "¿Qué impuestos están vencidos?",
]

Responder = Callable[[str, str, list[dict[str, str]], "set[str] | None"], str]


# ---------------------------------------------------------------- conversación

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
    """Motor con herramientas (en tono de chat); si no está, el motor anterior."""
    from vitae.ia import copiloto

    ultimo: Exception | None = None
    if copiloto._motor_disponible():
        try:
            from vitae.ia.motor import responder as motor_responder

            return motor_responder(pregunta, modulo_actual, historial=historial,
                                   modulos_permitidos=permitidos, estilo="chat")
        except Exception as exc:  # cuota, modelo, red: se informa y se prueba el respaldo
            ultimo = exc
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
    st.session_state[PENDIENTE] = texto


def _abrir() -> None:
    st.session_state[ABIERTO] = True


def _minimizar() -> None:
    st.session_state[ABIERTO] = False


def _hora() -> str:
    return datetime.now().strftime("%H:%M")


def _agregar(rol: str, texto: str) -> None:
    lista = mensajes()
    lista.append({"rol": rol, "texto": texto, "hora": _hora()})
    del lista[:-MAX_MENSAJES]


def _error_humano(exc: Exception) -> str:
    return (f"Uy, no pude consultar eso ahora ({type(exc).__name__}). "
            "¿Me lo volvés a preguntar en un ratito?")


# ---------------------------------------------------------------- texto → globito

def _inline(texto: str) -> str:
    t = html.escape(texto, quote=False).replace("$ ", "$\u00a0").replace("$", "&#36;")
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
    return t


def _celdas(linea: str) -> list[str]:
    return [c.strip() for c in linea.strip().strip("|").split("|")]


def texto_a_html(texto: str) -> str:
    """Markdown sencillo (negrita, cursiva, listas, tablas) a HTML seguro."""
    partes: list[str] = []
    lista: str | None = None
    tabla: list[list[str]] = []
    parrafo: list[str] = []

    def cerrar_parrafo() -> None:
        if parrafo:
            partes.append("<p>" + "<br>".join(parrafo) + "</p>")
            parrafo.clear()

    def cerrar_lista() -> None:
        nonlocal lista
        if lista:
            partes.append(f"</{lista}>")
            lista = None

    def cerrar_tabla() -> None:
        if tabla:
            filas = [f for f in tabla if not all(re.fullmatch(r":?-{2,}:?", c or "-") for c in f)]
            if filas:
                cab = "".join(f"<th>{_inline(c)}</th>" for c in filas[0])
                cuerpo = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in f) + "</tr>" for f in filas[1:])
                partes.append(f'<div class="asis-tabla"><table><tr>{cab}</tr>{cuerpo}</table></div>')
            tabla.clear()

    for cruda in str(texto or "").strip().splitlines():
        linea = cruda.strip()
        if linea.startswith("|") and linea.count("|") >= 2:
            cerrar_parrafo()
            cerrar_lista()
            tabla.append(_celdas(linea))
            continue
        cerrar_tabla()
        viñeta = re.match(r"^([-*•·])\s+(.*)", linea)
        numero = re.match(r"^(\d+)[.)]\s+(.*)", linea)
        if viñeta or numero:
            cerrar_parrafo()
            tipo = "ul" if viñeta else "ol"
            if lista != tipo:
                cerrar_lista()
                partes.append(f"<{tipo}>")
                lista = tipo
            partes.append(f"<li>{_inline((viñeta or numero).group(2))}</li>")
            continue
        cerrar_lista()
        if not linea:
            cerrar_parrafo()
            continue
        titulo = re.match(r"^#{1,6}\s+(.*)", linea)
        parrafo.append(f"<b>{_inline(titulo.group(1))}</b>" if titulo else _inline(linea))
    cerrar_tabla()
    cerrar_lista()
    cerrar_parrafo()
    return "".join(partes)


def _globito(m: dict[str, str]) -> str:
    yo = m.get("rol") == "user"
    hora = f'<span class="asis-hora">{html.escape(m.get("hora", ""))}</span>' if m.get("hora") else ""
    burbuja = f'<div class="asis-burbuja">{texto_a_html(m.get("texto", ""))}{hora}</div>'
    if yo:
        return f'<div class="asis-fila asis-yo">{burbuja}</div>'
    return f'<div class="asis-fila asis-ella"><div class="asis-mini">V</div>{burbuja}</div>'


def html_conversacion(lista: list[dict[str, str]], *, escribiendo: bool = False, saludo: str = "") -> str:
    """Los globitos, en una caja que siempre muestra lo último."""
    filas = []
    if saludo:
        filas.append(_globito({"rol": "assistant", "texto": saludo}))
    filas += [_globito(m) for m in lista]
    if escribiendo:
        filas.append('<div class="asis-fila asis-ella"><div class="asis-mini">V</div>'
                     '<div class="asis-burbuja asis-escribiendo"><span></span><span></span><span></span></div></div>'
                     f'<div class="asis-estado">{NOMBRE} está escribiendo…</div>')
    # column-reverse: el scroll arranca abajo, como en un chat
    return f'<div class="asis-mensajes"><div class="asis-pila">{"".join(filas)}</div></div>'


def _saludo(nombre_usuario: str | None) -> str:
    nombre = f" {nombre_usuario}" if nombre_usuario else ""
    return (f"¡Hola{nombre}! Soy {NOMBRE} 👋 ¿En qué te doy una mano? "
            "Puedo mirarte facturación, cajas, deudas, pagos, pacientes o la agenda.")


# ---------------------------------------------------------------- estilos

_CSS = """<style>
.st-key-asis_chat_cerrado{position:fixed;right:24px;bottom:0;z-index:999990;width:270px!important;gap:0}
.st-key-asis_chat_cerrado button{width:100%;justify-content:flex-start;border-radius:12px 12px 0 0!important;
  background:#8C2853!important;border:none!important;padding:.6rem 1rem!important;
  box-shadow:0 -4px 18px rgba(94,26,57,.28)!important}
.st-key-asis_chat_cerrado button p{color:#fff!important;font-weight:650!important;font-size:.95rem!important}
.st-key-asis_chat_cerrado button:hover{background:#6F1F42!important}
.st-key-asis_chat_abierto{position:fixed;right:24px;bottom:0;z-index:999990;width:370px!important;gap:0!important;
  background:#fff;border-radius:14px 14px 0 0;box-shadow:0 8px 34px rgba(30,10,20,.30);overflow:hidden;
  border:1px solid #E7DDE2;border-bottom:none}
.st-key-asis_chat_abierto>div{width:100%}
.st-key-asis_chat_abierto *,.st-key-asis_chat_cerrado *{box-sizing:border-box}
.st-key-asis_cabecera{background:linear-gradient(135deg,#8C2853,#6F1F42);padding:.45rem .5rem .45rem .7rem;gap:.25rem!important;
  min-height:56px}
.st-key-asis_cabecera button{background:transparent!important;border:none!important;color:#fff!important;
  min-height:0!important;padding:.15rem .45rem!important;box-shadow:none!important}
.st-key-asis_cabecera button p{color:#fff!important;font-size:1.05rem!important;font-weight:700!important}
.st-key-asis_cabecera button:hover{background:rgba(255,255,255,.16)!important}
.asis-quien{display:flex;align-items:center;gap:.55rem;margin:0}
.asis-avatar{position:relative;width:36px;height:36px;border-radius:50%;background:#fff;color:#8C2853;
  display:flex;align-items:center;justify-content:center;font-weight:800;font-size:1rem;flex:none}
.asis-avatar:after{content:"";position:absolute;right:-1px;bottom:-1px;width:11px;height:11px;border-radius:50%;
  background:#31C46B;border:2px solid #7A2248}
.asis-nombre{color:#fff;font-weight:700;font-size:.95rem;line-height:1.1}
.asis-online{color:#F3DCE6;font-size:.72rem;line-height:1.2}
.asis-mensajes{height:340px;overflow-y:auto;display:flex;flex-direction:column-reverse;padding:.7rem .7rem .4rem;
  background:#FBF8F9}
.asis-pila{display:flex;flex-direction:column;gap:.35rem}
.asis-fila{display:flex;align-items:flex-end;gap:.4rem}
.asis-yo{justify-content:flex-end}
.asis-mini{width:24px;height:24px;border-radius:50%;background:#8C2853;color:#fff;font-size:.7rem;font-weight:800;
  display:flex;align-items:center;justify-content:center;flex:none}
.asis-burbuja{max-width:78%;padding:.45rem .7rem;border-radius:18px;font-size:.86rem;line-height:1.38;
  word-wrap:break-word;overflow-wrap:anywhere}
.asis-burbuja p{margin:0 0 .3rem}.asis-burbuja p:last-of-type{margin-bottom:0}
.asis-burbuja ul,.asis-burbuja ol{margin:.15rem 0 .3rem 1.05rem;padding:0}.asis-burbuja li{margin:0}
.asis-burbuja code{font-size:.8rem;background:rgba(0,0,0,.06);padding:0 .2rem;border-radius:4px}
.asis-yo .asis-burbuja{background:#8C2853;color:#fff;border-bottom-right-radius:5px}
.asis-ella .asis-burbuja{background:#ECE6E9;color:#2B2226;border-bottom-left-radius:5px}
.asis-hora{display:block;text-align:right;font-size:.63rem;opacity:.6;margin-top:.1rem}
.asis-tabla{overflow-x:auto;margin:.2rem 0}
.asis-tabla table{border-collapse:collapse;font-size:.76rem}
.asis-tabla th,.asis-tabla td{border-bottom:1px solid rgba(0,0,0,.1);padding:.15rem .4rem;text-align:left;white-space:nowrap}
.asis-escribiendo{display:flex;gap:4px;padding:.65rem .8rem}
.asis-escribiendo span{width:7px;height:7px;border-radius:50%;background:#9A8D93;animation:asis-pt 1.2s infinite ease-in-out}
.asis-escribiendo span:nth-child(2){animation-delay:.15s}.asis-escribiendo span:nth-child(3){animation-delay:.3s}
@keyframes asis-pt{0%,60%,100%{transform:translateY(0);opacity:.5}30%{transform:translateY(-4px);opacity:1}}
.asis-estado{font-size:.7rem;color:#8A7F85;margin-left:2rem}
.st-key-asis_sugerencias{padding:.1rem .6rem .35rem;background:#FBF8F9;gap:.3rem!important;flex-wrap:wrap}
.st-key-asis_sugerencias button{border-radius:999px!important;border:1px solid #D9B8C7!important;background:#fff!important;
  min-height:0!important;padding:.2rem .65rem!important}
.st-key-asis_sugerencias button p{font-size:.76rem!important;color:#8C2853!important}
.st-key-asis_chat_abierto [data-testid="stForm"]{padding:.45rem .55rem .55rem;border-top:1px solid #EFE6EA;border-radius:0}
.st-key-asis_escribir{gap:.35rem!important}
.st-key-asis_escribir input{border-radius:999px!important;font-size:.88rem!important}
.st-key-asis_escribir [data-testid="stTextInputRootElement"]{border-radius:999px!important}
.st-key-asis_escribir button{border-radius:50%!important;width:38px;height:38px;min-height:0!important;padding:0!important;
  background:#8C2853!important;border:none!important}
.st-key-asis_escribir button p{color:#fff!important;font-size:1rem!important}
section.main .block-container,[data-testid="stMainBlockContainer"]{padding-bottom:4.5rem}
@media (max-width: 640px){
  .st-key-asis_chat_abierto{left:0;right:0;width:auto!important;border-radius:14px 14px 0 0}
  .st-key-asis_chat_abierto .asis-mensajes{height:52vh}
  .st-key-asis_chat_cerrado{right:12px;width:220px!important}
}
</style>"""


# ---------------------------------------------------------------- ventana

def _fila(key: str):
    """Fila horizontal (con respaldo a un contenedor común)."""
    try:
        return st.container(key=key, horizontal=True, vertical_alignment="center")
    except TypeError:  # pragma: no cover - Streamlit viejo
        return st.container(key=key)


def _boton(label: str, *, key: str, **k: Any) -> bool:
    try:
        return st.button(label, key=key, width="content", **k)
    except TypeError:  # pragma: no cover - Streamlit viejo
        return st.button(label, key=key, **k)


def _cabecera(modulo_actual: str) -> None:
    with _fila("asis_cabecera"):
        st.markdown(
            '<div class="asis-quien"><div class="asis-avatar">V</div><div>'
            f'<div class="asis-nombre">{NOMBRE} · Asistente Vitae</div>'
            f'<div class="asis-online">En línea · {html.escape(str(modulo_actual))}</div></div></div>',
            unsafe_allow_html=True,
        )
        _boton("✎", key="asis_nueva", help="Empezar una conversación nueva", on_click=_limpiar)
        _boton("–", key="asis_minimizar", help="Minimizar", on_click=_minimizar)


def _conversacion(modulo_actual: str, permitidos: set[str] | None, responder_fn: Responder,
                  nombre_usuario: str | None) -> None:
    lista = mensajes()
    saludo = _saludo(nombre_usuario)
    caja = st.empty()
    caja.markdown(html_conversacion(lista, saludo=saludo), unsafe_allow_html=True)
    lugar_sug = st.empty()
    if not lista:
        with lugar_sug.container(key="asis_sugerencias", horizontal=True):
            for i, sug in enumerate(SUGERENCIAS):
                _boton(sug, key=f"asistente_sug_{i}", on_click=_sugerir, args=(sug,))
    with st.form("asistente_vitae_form", clear_on_submit=True, border=False):
        with _fila("asis_escribir"):
            texto = st.text_input("Mensaje para Vita", key="asistente_vitae_texto",
                                  placeholder=f"Escribile a {NOMBRE}…", label_visibility="collapsed")
            enviar = st.form_submit_button("➤", help="Enviar")
    pregunta = (texto or "").strip() if enviar else ""
    pregunta = pregunta or st.session_state.pop(PENDIENTE, None)
    if not pregunta:
        return
    historial = _historial_para_motor(lista)
    _agregar("user", pregunta)
    lugar_sug.empty()
    caja.markdown(html_conversacion(mensajes(), escribiendo=True, saludo=saludo), unsafe_allow_html=True)
    try:
        respuesta = responder_fn(pregunta, modulo_actual, historial, permitidos)
    except Exception as exc:
        respuesta = _error_humano(exc)
    _agregar("assistant", respuesta or "Mmm, no me salió nada para eso. ¿Me lo preguntás de otra forma?")
    caja.markdown(html_conversacion(mensajes(), saludo=saludo), unsafe_allow_html=True)


def _ventana(modulo_actual: str, permitidos: set[str] | None, responder_fn: Responder,
             nombre_usuario: str | None) -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    if not st.session_state.get(ABIERTO, False) and not st.session_state.get(PENDIENTE):
        with st.container(key="asis_chat_cerrado"):
            _boton(f"💬  {NOMBRE} · Asistente Vitae", key="asis_abrir", on_click=_abrir,
                   help="Abrir el chat con Vita")
        return
    st.session_state[ABIERTO] = True
    with st.container(key="asis_chat_abierto"):
        _cabecera(modulo_actual)
        _conversacion(modulo_actual, permitidos, responder_fn, nombre_usuario)


_fragmento = getattr(st, "fragment", None)
_ventana_fragmento = _fragmento(_ventana) if callable(_fragmento) else _ventana


def chat_flotante(
    modulo_actual: str,
    *,
    permitidos: set[str] | None = None,
    responder_fn: Responder | None = None,
    nombre_usuario: str | None = None,
) -> None:
    """Chat de Vita abajo a la derecha: minimizable, en globitos, en todas las pantallas."""
    _ventana_fragmento(modulo_actual, permitidos, responder_fn or responder, nombre_usuario)


def render_asistente(
    modulo_actual: str,
    *,
    permitidos: set[str] | None = None,
    responder_fn: Responder | None = None,
    nombre_usuario: str | None = None,
) -> None:
    """La conversación sola (sin ventana flotante), por ejemplo para pruebas."""
    st.markdown(_CSS, unsafe_allow_html=True)
    _conversacion(modulo_actual, permitidos, responder_fn or responder, nombre_usuario)
