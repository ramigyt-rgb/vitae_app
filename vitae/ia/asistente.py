"""Asistente Vitae: «Luz», un chat de IA estilo Messenger en todas las pantallas.

Minimizada es una burbujita redonda con el logo de Vitae (como los «chat
heads» de Messenger); al tocarla se abre una ventanita flotante con esquinas
redondeadas. Burbuja y ventana se pueden arrastrar adonde le quede cómodo a
cada uno, y la posición se recuerda en ese navegador. Los mensajes van en
globitos (los tuyos a la derecha, los de Luz a la izquierda), con «En línea»
y «escribiendo…» mientras busca la respuesta. Luz contesta corto y cálido,
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

NOMBRE = "Luz"
CLAVE = "asistente_vitae_mensajes"
ABIERTO = "asis_abierto"
PENDIENTE = "asistente_vitae_pendiente"
NUEVAS_ACCIONES = "asis_acciones_nuevas"   # propuestas de la última respuesta (vitae/ia/acciones.py)
REFRESCAR = "asis_refrescar"               # después de una acción: recargar la pantalla de abajo
MAX_MENSAJES = 40

# Respuestas rápidas (chips arriba del cuadro de texto): etiqueta → pregunta.
SUGERENCIAS = [
    ("📌 Hoy", "¿Qué requiere mi atención hoy?"),
    ("💰 Facturación", "¿Cuánto se facturó este mes?"),
    ("🧾 Vencidos", "¿Qué impuestos están vencidos?"),
    ("🏦 Cajas", "¿Cuánto hay hoy en las cajas?"),
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

            propuestas: list[dict[str, Any]] = []
            texto = motor_responder(pregunta, modulo_actual, historial=historial,
                                    modulos_permitidos=permitidos, estilo="chat", acciones=propuestas)
            st.session_state[NUEVAS_ACCIONES] = propuestas
            return texto
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


def _agregar(rol: str, texto: str, accion: dict[str, Any] | None = None) -> None:
    lista = mensajes()
    m: dict[str, Any] = {"rol": rol, "texto": texto, "hora": _hora()}
    if accion:
        m["accion"] = accion
    lista.append(m)
    del lista[:-MAX_MENSAJES]


# ---------------------------------------------------------------- acciones

def accion_pendiente() -> tuple[int, dict[str, Any]] | None:
    for i in range(len(mensajes()) - 1, -1, -1):
        a = mensajes()[i].get("accion")
        if a and a.get("estado") == "pendiente":
            return i, a
    return None


def _descartar_pendientes() -> None:
    for m in mensajes():
        if m.get("accion", {}).get("estado") == "pendiente":
            m["accion"]["estado"] = "descartada"


def _confirmar(i: int, ejecutar_fn: Callable[[dict[str, Any]], str] | None, permitidos: set[str] | None) -> None:
    lista = mensajes()
    accion = lista[i].get("accion") if i < len(lista) else None
    if not accion or accion.get("estado") != "pendiente":
        return
    if permitidos is not None and accion.get("modulo") not in permitidos:
        accion["estado"] = "rechazada"
        _agregar("assistant", f"No tenés permiso para cambiar {accion.get('modulo')}, así que no lo hice.")
        return
    if ejecutar_fn is None:
        accion["estado"] = "error"
        _agregar("assistant", "Desde acá no puedo guardar cambios. Cargalo desde el módulo, porfa.")
        return
    try:
        texto = ejecutar_fn(accion)
        accion["estado"] = "hecha"
        st.session_state[REFRESCAR] = True
    except Exception as exc:
        accion["estado"] = "error"
        texto = f"Uy, no pude guardarlo: {exc}"
    _agregar("assistant", texto)


def _cancelar(i: int) -> None:
    lista = mensajes()
    accion = lista[i].get("accion") if i < len(lista) else None
    if accion and accion.get("estado") == "pendiente":
        accion["estado"] = "cancelada"
        _agregar("assistant", "Dale, no cargué nada. Si querés cambiar algún dato, decime y la armo de nuevo.")


def _tarjeta_accion(lugar: Any, ejecutar_fn: Callable[[dict[str, Any]], str] | None,
                    permitidos: set[str] | None) -> None:
    pend = accion_pendiente()
    if not pend:
        lugar.empty()
        return
    i, accion = pend
    from vitae.ia.acciones import html_tarjeta

    with lugar.container(key=f"asis_accion_{i}"):
        st.markdown(html_tarjeta(accion), unsafe_allow_html=True)
        with _fila(f"asis_accion_botones_{i}"):
            _boton("✅ Confirmar", key=f"asis_acc_ok_{i}", type="primary", on_click=_confirmar,
                   args=(i, ejecutar_fn, permitidos))
            _boton("Cancelar", key=f"asis_acc_no_{i}", on_click=_cancelar, args=(i,))


def _error_humano(exc: Exception) -> str:
    return (f"Uy, no pude consultar eso ahora ({type(exc).__name__}). "
            "¿Me lo volvés a preguntar en un ratito?")


_LOGO: str | None = None


_LOGOS: dict[str, str] = {}


def logo_icono(tamano: str = "chico") -> str:
    """Isotipo de Vitae incrustado (data URI), como el logo del inicio de sesión.
    tamano: «chico» (96 px, cabecera y burbuja) o «mini» (64 px, al lado de cada mensaje)."""
    if tamano not in _LOGOS:
        try:
            import base64

            from vitae.core.config import BASE_DIR

            datos = (BASE_DIR / "static" / f"luz_logo_{tamano}.png").read_bytes()
            _LOGOS[tamano] = "data:image/png;base64," + base64.b64encode(datos).decode("ascii")
        except Exception:
            _LOGOS[tamano] = ""
    return _LOGOS[tamano]


def foto(clase: str, tamano: str = "chico") -> str:
    """La foto de Luz: una imagen real (no un fondo de CSS)."""
    src = logo_icono(tamano)
    contenido = f'<img src="{src}" alt="Luz">' if src else "L"
    return f'<div class="{clase}">{contenido}</div>'


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


def _inicial(nombre: str | None) -> str:
    n = str(nombre or "").strip()
    return html.escape(n[:1].upper()) if n else "Yo"


def _globito(m: dict[str, str], inicial: str = "Yo") -> str:
    yo = m.get("rol") == "user"
    hora = html.escape(m.get("hora", ""))
    burbuja = f'<div class="asis-burbuja">{texto_a_html(m.get("texto", ""))}</div>'
    if yo:
        meta = f'<div class="asis-meta">{hora} <span class="asis-visto">✓✓</span></div>' if hora else ""
        return (f'<div class="asis-fila asis-yo"><div class="asis-col">{burbuja}{meta}</div>'
                f'<div class="asis-yo-mini">{inicial}</div></div>')
    meta = f'<div class="asis-meta">{hora}</div>' if hora else ""
    return f'<div class="asis-fila asis-ella">{foto("asis-mini", "mini")}<div class="asis-col">{burbuja}{meta}</div></div>'


def html_conversacion(lista: list[dict[str, str]], *, escribiendo: bool = False, saludo: str = "",
                      inicial: str = "Yo") -> str:
    """Los globitos, en una caja que siempre muestra lo último."""
    filas = []
    if saludo:
        filas.append(_globito({"rol": "assistant", "texto": saludo}))
    filas += [_globito(m, inicial) for m in lista]
    if escribiendo:
        filas.append(f'<div class="asis-fila asis-ella">{foto("asis-mini", "mini")}<div class="asis-col">'
                     '<div class="asis-burbuja asis-escribiendo"><span></span><span></span><span></span></div>'
                     f'<div class="asis-meta">{NOMBRE} está escribiendo…</div></div></div>')
    # column-reverse: el scroll arranca abajo, como en un chat
    return f'<div class="asis-mensajes"><div class="asis-pila">{"".join(filas)}</div></div>'


def _saludo(nombre_usuario: str | None) -> str:
    nombre = f" {nombre_usuario}" if nombre_usuario else ""
    return (f"¡Hola{nombre}! Soy {NOMBRE} 👋 ¿En qué te doy una mano? "
            "Puedo mirarte facturación, cajas, deudas, pagos, pacientes o la agenda.")


# ---------------------------------------------------------------- estilos
# Posición por defecto: abajo a la derecha, por encima del «Manage app» de
# Streamlit Cloud (que sólo ve el dueño de la app). Después, cada uno la mueve.

_CSS = """<style>
.st-key-asis_chat_cerrado,.st-key-asis_chat_abierto{position:fixed;right:22px;bottom:64px;z-index:999990;gap:0!important}
.st-key-asis_chat_cerrado *,.st-key-asis_chat_abierto *{box-sizing:border-box}
.st-key-asis_chat_cerrado{width:62px!important;height:62px}
.st-key-asis_chat_cerrado:after{content:"";position:absolute;right:3px;bottom:3px;width:14px;height:14px;border-radius:50%;
  background:#31C46B;border:2px solid #fff;pointer-events:none;z-index:5}
.st-key-asis_chat_cerrado button{width:62px!important;height:62px!important;min-height:0!important;padding:0!important;
  border-radius:50%!important;border:3px solid #8C2853!important;background-color:#fff!important;
  box-shadow:0 8px 24px rgba(94,26,57,.35)!important;cursor:grab;touch-action:none;transition:transform .15s}
.st-key-asis_chat_cerrado button:hover{transform:scale(1.07)}
.st-key-asis_chat_cerrado button p{display:none}
.st-key-asis_chat_cerrado [data-testid="stElementContainer"]:has(.asis-burbuja-logo){position:absolute!important;inset:0!important;
  margin:0!important;pointer-events:none;z-index:3}
.st-key-asis_chat_cerrado .asis-burbuja-logo{position:absolute;left:8px;top:8px;width:46px;height:46px;border-radius:50%;
  pointer-events:none;background-size:100%!important}
.st-key-asis_chat_abierto{width:330px!important;background:#fff;border-radius:20px;overflow:hidden;
  box-shadow:0 14px 44px rgba(30,10,20,.28);border:1px solid #ECE3E7}
.st-key-asis_chat_abierto>div{width:100%}
.st-key-asis_cabecera{background:linear-gradient(135deg,#8C2853,#6F1F42);padding:.55rem .55rem .55rem .8rem;
  gap:.3rem!important;cursor:grab;touch-action:none;user-select:none}
.st-key-asis_cabecera button{background:transparent!important;border:1.5px solid rgba(255,255,255,.75)!important;
  border-radius:50%!important;width:28px;height:28px;min-height:0!important;padding:0!important;box-shadow:none!important}
.st-key-asis_cabecera button p{color:#fff!important;font-size:.9rem!important;font-weight:700!important;line-height:1}
.st-key-asis_cabecera button:hover{background:rgba(255,255,255,.18)!important}
.asis-quien{display:flex;align-items:center;gap:.6rem;margin:0}
.asis-avatar{position:relative;width:40px;height:40px;border-radius:50%;flex:none;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.15)}
.asis-nombre{color:#fff;font-weight:700;font-size:1.02rem;line-height:1.1}
.asis-online{color:#F3DCE6;font-size:.72rem;line-height:1.3;display:flex;align-items:center;gap:.3rem}
.asis-online:before{content:"";width:7px;height:7px;border-radius:50%;background:#31C46B;display:inline-block}
.asis-mensajes{height:290px;overflow-y:auto;display:flex;flex-direction:column-reverse;padding:.75rem .65rem .5rem;
  background:#F7F3F5}
.asis-pila{display:flex;flex-direction:column;gap:.55rem}
.asis-fila{display:flex;align-items:flex-end;gap:.4rem}
.asis-yo{justify-content:flex-end}
.asis-col{display:flex;flex-direction:column;max-width:78%;min-width:0}
.asis-yo .asis-col{align-items:flex-end}
.asis-mini{width:30px;height:30px;border-radius:50%;flex:none;border:1px solid #E7DDE2;margin-bottom:1.05rem}
.asis-yo-mini{width:28px;height:28px;border-radius:50%;flex:none;background:#E9D3DD;color:#6F1F42;font-size:.72rem;
  font-weight:800;display:flex;align-items:center;justify-content:center;margin-bottom:1.05rem}
.asis-burbuja{padding:.5rem .75rem;border-radius:14px;font-size:.84rem;line-height:1.4;word-wrap:break-word;
  overflow-wrap:anywhere;max-width:100%}
.asis-burbuja p{margin:0 0 .3rem}.asis-burbuja p:last-of-type{margin-bottom:0}
.asis-burbuja ul,.asis-burbuja ol{margin:.15rem 0 .3rem 1.05rem;padding:0}.asis-burbuja li{margin:0}
.asis-burbuja code{font-size:.8rem;background:rgba(0,0,0,.06);padding:0 .2rem;border-radius:4px}
.asis-yo .asis-burbuja{background:#8C2853;color:#fff;border-bottom-right-radius:4px}
.asis-ella .asis-burbuja{background:#E9E4E7;color:#2B2226;border-bottom-left-radius:4px}
.asis-meta{font-size:.64rem;color:#9A8D93;margin:.18rem .2rem 0}
.asis-visto{color:#8C2853;letter-spacing:-2px}
.asis-tabla{overflow-x:auto;margin:.2rem 0}
.asis-tabla table{border-collapse:collapse;font-size:.76rem}
.asis-tabla th,.asis-tabla td{border-bottom:1px solid rgba(0,0,0,.1);padding:.15rem .4rem;text-align:left;white-space:nowrap}
.asis-escribiendo{display:flex;gap:4px;padding:.65rem .8rem}
.asis-escribiendo span{width:7px;height:7px;border-radius:50%;background:#9A8D93;animation:asis-pt 1.2s infinite ease-in-out}
.asis-escribiendo span:nth-child(2){animation-delay:.15s}.asis-escribiendo span:nth-child(3){animation-delay:.3s}
@keyframes asis-pt{0%,60%,100%{transform:translateY(0);opacity:.5}30%{transform:translateY(-4px);opacity:1}}
.asis-acc{background:#fff;border:1px solid #E4D7DD;border-left:4px solid #8C2853;border-radius:12px;padding:.55rem .7rem;
  margin:.1rem .6rem .35rem;font-size:.8rem}
.asis-acc-tit{display:flex;justify-content:space-between;align-items:center;font-weight:700;color:#5E1A39;margin-bottom:.35rem}
.asis-acc-tit span{font-weight:500;font-size:.68rem;color:#9A8D93}
.asis-acc-grid{display:grid;grid-template-columns:auto 1fr;gap:.12rem .6rem}
.asis-acc-k{color:#8A7F85}.asis-acc-v{color:#2B2226;font-weight:600;overflow-wrap:anywhere}
.asis-acc-aviso{margin-top:.35rem;color:#9A3A1E;background:#FFF3EC;border-radius:8px;padding:.25rem .45rem}
[class*="st-key-asis_accion_botones"]{padding:0 .6rem .45rem;gap:.4rem!important}
[class*="st-key-asis_accion_botones"] button{min-height:0!important;padding:.25rem .8rem!important;border-radius:999px!important}
[class*="st-key-asis_accion_"]{background:#F7F3F5}
.st-key-asis_chat_abierto:has([class*="st-key-asis_accion_"]) .asis-mensajes{height:165px}
.asis-avatar,.asis-mini,.asis-burbuja-logo{background:#fff;overflow:hidden;box-sizing:border-box;padding:4px;
  display:flex;align-items:center;justify-content:center;color:#8C2853;font-weight:800}
.asis-mini{padding:3px}.st-key-asis_chat_cerrado .asis-burbuja-logo{padding:2px}
.asis-avatar img,.asis-mini img,.asis-burbuja-logo img{width:100%;height:100%;object-fit:contain;display:block;
  margin:0!important;max-width:none!important}
.st-key-asis_pie{background:#fff;padding:.5rem .6rem .6rem;gap:.45rem!important;border-top:1px solid #EFE6EA}
.st-key-asis_sugerencias{gap:.35rem!important;flex-wrap:nowrap!important;overflow-x:auto;scrollbar-width:none}
.st-key-asis_sugerencias::-webkit-scrollbar{display:none}
.st-key-asis_sugerencias button{border-radius:10px!important;border:1px solid #E4D7DD!important;background:#FBF8F9!important;
  min-height:0!important;padding:.22rem .6rem!important;white-space:nowrap;box-shadow:none!important}
.st-key-asis_sugerencias button:hover{border-color:#8C2853!important}
.st-key-asis_sugerencias button p{font-size:.74rem!important;color:#4A3B42!important;font-weight:600!important}
.st-key-asis_chat_abierto [data-testid="stForm"]{padding:0;border:none;border-radius:0}
.st-key-asis_escribir{background:#F1ECEF;border-radius:14px;padding:.2rem .25rem .2rem .2rem;gap:.1rem!important}
.st-key-asis_escribir input{background:transparent!important;font-size:.88rem!important}
.st-key-asis_escribir [data-testid="stTextInputRootElement"],.st-key-asis_escribir [data-baseweb="input"],
.st-key-asis_escribir [data-baseweb="base-input"]{background:transparent!important;border:none!important}
.st-key-asis_escribir button{border:none!important;background:transparent!important;width:34px;height:34px;
  min-height:0!important;padding:0!important;box-shadow:none!important}
.st-key-asis_escribir button p{color:#8C2853!important;font-size:1.15rem!important}
.st-key-asis_js{position:absolute!important;width:0!important;height:0!important;overflow:hidden}
@media (max-width: 640px){
  html body .st-key-asis_chat_abierto{left:8px!important;right:8px!important;bottom:8px!important;top:auto!important;
    width:auto!important}
  .st-key-asis_chat_abierto .asis-mensajes{height:48vh}
}
</style>"""


def _css() -> str:
    return _CSS


# ---------------------------------------------------------------- arrastrar
# Streamlit no ejecuta JavaScript en st.markdown: un componente mínimo agrega
# (una sola vez) un script a la página que permite arrastrar la burbuja (toda)
# y la ventana (desde la cabecera). La posición se guarda en el navegador.

_JS_ARRASTRE = r"""
(function () {
  var w = window, d = document;
  if (w.__luzArrastre) return;
  w.__luzArrastre = true;
  var CLAVE = "vitae_luz_posicion";
  var pos = {r: 22, b: 64};
  try {
    var g = JSON.parse(w.localStorage.getItem(CLAVE) || "null");
    if (g && isFinite(g.r) && isFinite(g.b)) pos = g;
  } catch (e) {}
  var estilo = d.getElementById("luz-posicion");
  if (!estilo) { estilo = d.createElement("style"); estilo.id = "luz-posicion"; d.head.appendChild(estilo); }
  function aplicar() {
    var r = Math.round(pos.r), b = Math.round(pos.b);
    estilo.textContent =
      ".st-key-asis_chat_cerrado{right:clamp(6px," + r + "px,calc(100vw - 68px))!important;" +
      "bottom:clamp(6px," + b + "px,calc(100vh - 68px))!important}" +
      ".st-key-asis_chat_abierto{right:clamp(6px," + r + "px,max(6px,calc(100vw - 336px)))!important;" +
      "bottom:clamp(6px," + b + "px,max(6px,calc(100vh - 500px)))!important}";
  }
  aplicar();
  function asa(t) {
    if (!t || !t.closest) return null;
    var burbuja = t.closest(".st-key-asis_chat_cerrado");
    if (burbuja) return burbuja;
    var cab = t.closest(".st-key-asis_cabecera");
    if (cab && !t.closest("button")) return cab.closest(".st-key-asis_chat_abierto");
    return null;
  }
  var arrastre = null, movio = false, frenarClick = false;
  d.addEventListener("pointerdown", function (e) {
    if (e.button > 0) return;
    var el = asa(e.target);
    if (!el) return;
    var rc = el.getBoundingClientRect();
    arrastre = {x: e.clientX, y: e.clientY, r: w.innerWidth - rc.right, b: w.innerHeight - rc.bottom};
    movio = false;
  }, true);
  d.addEventListener("pointermove", function (e) {
    if (!arrastre) return;
    var dx = e.clientX - arrastre.x, dy = e.clientY - arrastre.y;
    if (!movio && Math.abs(dx) + Math.abs(dy) < 6) return;
    movio = true;
    e.preventDefault();
    pos = {r: arrastre.r - dx, b: arrastre.b - dy};
    aplicar();
  }, true);
  function soltar() {
    if (!arrastre) return;
    if (movio) {
      frenarClick = true;
      setTimeout(function () { frenarClick = false; }, 400);
      try { w.localStorage.setItem(CLAVE, JSON.stringify(pos)); } catch (e) {}
    }
    arrastre = null;
  }
  d.addEventListener("pointerup", soltar, true);
  d.addEventListener("pointercancel", soltar, true);
  // Si se arrastró, el «click» al soltar no abre ni cierra el chat.
  w.addEventListener("click", function (e) {
    if (frenarClick) { e.stopPropagation(); e.preventDefault(); frenarClick = false; }
  }, true);
})();
"""


def codigo_arrastre() -> str:
    """HTML del componente: inyecta el script en la página de la app (no en el iframe)."""
    import json

    return ("<script>(function(){var p=window.parent;try{if(p.__luzArrastre)return;"
            "var s=p.document.createElement('script');s.textContent=" + json.dumps(_JS_ARRASTRE) +
            ";p.document.head.appendChild(s);}catch(e){}})();</script>")


def _arrastre() -> None:
    try:
        import streamlit.components.v1 as components
    except Exception:  # sin componentes (pruebas): el chat funciona igual, sin arrastrar
        return
    try:
        with st.container(key="asis_js"):
            components.html(codigo_arrastre(), height=0)
    except Exception:
        pass


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
            f'<div class="asis-quien">{foto("asis-avatar")}<div>'
            f'<div class="asis-nombre">{NOMBRE}</div>'
            f'<div class="asis-online">En línea · {html.escape(str(modulo_actual))}</div></div></div>',
            unsafe_allow_html=True,
        )
        _boton("✎", key="asis_nueva", help="Conversación nueva", on_click=_limpiar)
        _boton("–", key="asis_minimizar", help="Minimizar", on_click=_minimizar)


def _conversacion(modulo_actual: str, permitidos: set[str] | None, responder_fn: Responder,
                  nombre_usuario: str | None, ejecutar_fn: Callable[[dict[str, Any]], str] | None = None) -> None:
    lista = mensajes()
    saludo = _saludo(nombre_usuario)
    inicial = _inicial(nombre_usuario)
    caja = st.empty()
    caja.markdown(html_conversacion(lista, saludo=saludo, inicial=inicial), unsafe_allow_html=True)
    lugar_accion = st.empty()
    _tarjeta_accion(lugar_accion, ejecutar_fn, permitidos)
    with st.container(key="asis_pie"):
        with _fila("asis_sugerencias"):
            for i, (etiqueta, pregunta_sug) in enumerate(SUGERENCIAS):
                _boton(etiqueta, key=f"asistente_sug_{i}", on_click=_sugerir, args=(pregunta_sug,),
                       help=pregunta_sug)
        with st.form("asistente_vitae_form", clear_on_submit=True, border=False):
            with _fila("asis_escribir"):
                texto = st.text_input(f"Mensaje para {NOMBRE}", key="asistente_vitae_texto",
                                      placeholder="Escribí tu mensaje…", label_visibility="collapsed")
                enviar = st.form_submit_button("➤", help="Enviar")
    pregunta = (texto or "").strip() if enviar else ""
    pregunta = pregunta or st.session_state.pop(PENDIENTE, None)
    if not pregunta:
        return
    historial = _historial_para_motor(lista)
    _descartar_pendientes()
    lugar_accion.empty()
    _agregar("user", pregunta)
    caja.markdown(html_conversacion(mensajes(), escribiendo=True, saludo=saludo, inicial=inicial),
                  unsafe_allow_html=True)
    st.session_state.pop(NUEVAS_ACCIONES, None)
    try:
        respuesta = responder_fn(pregunta, modulo_actual, historial, permitidos)
    except Exception as exc:
        respuesta = _error_humano(exc)
    propuestas = st.session_state.pop(NUEVAS_ACCIONES, None) or []
    _agregar("assistant", respuesta or "Mmm, no me salió nada para eso. ¿Me lo preguntás de otra forma?",
             accion=propuestas[-1] if propuestas else None)
    caja.markdown(html_conversacion(mensajes(), saludo=saludo, inicial=inicial), unsafe_allow_html=True)
    _tarjeta_accion(lugar_accion, ejecutar_fn, permitidos)


def _ventana(modulo_actual: str, permitidos: set[str] | None, responder_fn: Responder,
             nombre_usuario: str | None, ejecutar_fn: Callable[[dict[str, Any]], str] | None = None) -> None:
    if st.session_state.pop(REFRESCAR, False):
        st.rerun()  # Luz cambió datos: se recarga toda la pantalla para verlos
    st.markdown(_css(), unsafe_allow_html=True)
    if not st.session_state.get(ABIERTO, False) and not st.session_state.get(PENDIENTE):
        with st.container(key="asis_chat_cerrado"):
            _boton(NOMBRE, key="asis_abrir", on_click=_abrir, help=f"Chatear con {NOMBRE}")
            st.markdown(foto("asis-burbuja-logo"), unsafe_allow_html=True)
        return
    st.session_state[ABIERTO] = True
    with st.container(key="asis_chat_abierto"):
        _cabecera(modulo_actual)
        _conversacion(modulo_actual, permitidos, responder_fn, nombre_usuario, ejecutar_fn)


_fragmento = getattr(st, "fragment", None)
_ventana_fragmento = _fragmento(_ventana) if callable(_fragmento) else _ventana


def chat_flotante(
    modulo_actual: str,
    *,
    permitidos: set[str] | None = None,
    responder_fn: Responder | None = None,
    nombre_usuario: str | None = None,
    ejecutar_fn: Callable[[dict[str, Any]], str] | None = None,
) -> None:
    """Luz: burbuja flotante que se abre en una ventanita; las dos se pueden arrastrar.

    ``ejecutar_fn`` guarda una acción confirmada (por ejemplo, una cirugía en la agenda)."""
    _arrastre()
    _ventana_fragmento(modulo_actual, permitidos, responder_fn or responder, nombre_usuario, ejecutar_fn)


def render_asistente(
    modulo_actual: str,
    *,
    permitidos: set[str] | None = None,
    responder_fn: Responder | None = None,
    nombre_usuario: str | None = None,
) -> None:
    """La conversación sola (sin ventana flotante), por ejemplo para pruebas."""
    st.markdown(_css(), unsafe_allow_html=True)
    _conversacion(modulo_actual, permitidos, responder_fn or responder, nombre_usuario)
