"""Facturación VMR / VM paso a paso.

    Registros → 1 Cargar → 2 Facturar → 3 Cobrar → 4 Débitos → 5 Cierre → Mes   (aparte: Análisis · Tabla…)

Arriba, siempre los mismos indicadores (para facturar, por cobrar, vencido, débitos). Cada paso dice
qué hacer, muestra la lista para resolver ahí mismo y termina con «Siguiente paso».

- Mes: el mes elegido (por defecto el actual) en calendario del 1 al último día, la situación del mes
  (qué falta y dónde se resuelve), por obra social y sus registros por obra social o por paciente
  (``facturacion_mes``).

REGLA PRINCIPAL (a muerte): VMR (fertilidad) y VM (quirófano) son dos facturaciones distintas; cada módulo
sólo lee y controla su propia planilla. Son de VMR: FIV, FIV OVO, FIV OVO SDA, FIV SDA, TE, TEC, aspiración
folicular, crio, criopreservación de semen e IIU; todo lo demás, de VM. De la Agenda, cada módulo ve como
solicitud sólo las cirugías realizadas de su empresa.
- Cargar: solicitudes de la Agenda (de esta empresa), datos incompletos o repetidos, nuevo registro y la
  calidad de la planilla (para ver o editar
  cualquier registro: Registros).
- Facturar: lo que falta facturar, por obra social (presentaciones) o todo junto, por el plazo de 60 días.
- Cobrar: vencidas, por vencer y sin vencimiento; antigüedad, por obra social y a quién cobrar primero.
- Débitos: a reclamar, reclamados, recuperados y aceptados; cobros parciales sin el débito.
- Cierre: los meses del año con su estado; el control de cada mes; cerrar (se guarda en
  ``facturacion_cierres``) y el PDF del mes y del año.
- Análisis: lo de antes (resumen, cobranza, producción, calidad) con filtros.
- Registros: todos los registros en planilla por obra social, con el estilo de Deudas Impositivas
  (filtros, búsqueda, barra para actuar, nuevo registro).
- Tabla (la planilla tipo Excel, por si hace falta), Importar y Exportar.
"""
from __future__ import annotations

import html
from datetime import date
from typing import Any, Callable

import pandas as pd

import vitae.modulos.cierre_facturacion as cfx
import vitae.modulos.control_facturacion as cf
import vitae.modulos.facturacion as fx
import vitae.modulos.facturacion_fichas as ff
import vitae.modulos.facturacion_mes as fm
import vitae.modulos.facturacion_planilla as fz
from vitae.modulos.pagos_pendientes import pesos

# Registros primero (donde estaba Mes); Mes al final, después del Cierre.
PASOS = ["fichas", "cargar", "facturar", "cobrar", "debitos", "cierre", "hoy"]
EXTRAS = ["analisis", "tabla", "importar", "exportar"]
INICIO = "fichas"
NOMBRES = {"hoy": "Mes", "cargar": "1 · Cargar", "facturar": "2 · Facturar", "cobrar": "3 · Cobrar",
           "debitos": "4 · Débitos", "cierre": "5 · Cierre", "fichas": "Registros", "analisis": "Análisis",
           "tabla": "Tabla", "importar": "Importar", "exportar": "Exportar"}
LEGACY = {"tabla": "Editar tabla", "importar": "Importar"}
ANALISIS = ["Resumen", "Cobranza", "Producción", "Calidad"]

_CSS = """<style>
.fp-paso{display:flex;align-items:baseline;gap:.6rem;margin:.6rem 0 .1rem}
.fp-num{background:#8C2853;color:#fff;border-radius:999px;font-weight:800;font-size:.85rem;padding:.12rem .55rem}
.fp-tit{font-weight:800;font-size:1.15rem;color:#2B2226}
.fp-que{color:#6B5F66;font-size:.88rem;margin:.1rem 0 .6rem}
.fp-ok{background:#EEF8F0;border:1px solid #CFE8D5;border-radius:12px;padding:.7rem 1rem;color:#1F5130;font-weight:600;margin:.3rem 0 .6rem}
.fp-box{background:#FBF8F9;border:1px solid #EFE7EB;border-radius:12px;padding:.7rem .9rem;margin:.3rem 0 .7rem}
.fp-chip{display:inline-block;border-radius:999px;padding:.12rem .6rem;font-size:.78rem;font-weight:700;margin:0 .3rem .3rem 0}
.fp-etapa{display:flex;gap:.6rem;align-items:center;border-bottom:1px solid #F0ECEE;padding:.35rem 0}
.fp-etapa .n{background:#F4F1F3;color:#5E1A39;font-weight:800;border-radius:8px;padding:.05rem .45rem;min-width:1.6rem;text-align:center}
.fp-sub{color:#8A7F85;font-size:.8rem}
.fp-ck{display:flex;justify-content:space-between;gap:.8rem;border:1px solid #EFE7EB;border-radius:10px;padding:.45rem .75rem;margin:.22rem 0;background:#fff}
.fp-ck.mal{background:#FFF5F5;border-color:#F3D0D0}.fp-ck.info{background:#FFFAF0;border-color:#F1E2C4}
</style>"""


# ---------------------------------------------------------------------------
# Cuentas para la navegación y los indicadores
# ---------------------------------------------------------------------------
def cuentas(ctx: dict[str, Any]) -> dict[str, int]:
    c, b = ctx["c"], ctx["b"]

    def n(*claves: str) -> int:
        idx: set[Any] = set()
        for k in claves:
            if len(c.get(k, [])):
                idx |= set(c[k].index)
        return len(idx)

    hoy_ = ctx["hoy"]
    del_mes = fm.registros(ctx["d"], f"{hoy_.year:04d}-{hoy_.month:02d}")
    return {"cargar": int(len(c["agenda"])) + n("incompletos", "repetidas", "fecha_sin_numero"),
            "facturar": n("sin_facturar"), "cobrar": n("vencidas", "por_vencer", "sin_vencimiento"),
            "debitos": n("debitos", "parcial_sin_debito")}


def indicadores(ctx: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    c, d = ctx["c"], ctx["d"]
    a = d[~d["_cancelado"]] if len(d) else d
    fact = a[a["_facturada"]] if len(a) else a
    pend = fact[fact["_pendiente"] > 0.004] if len(fact) else fact
    sf = c["sin_facturar"]
    ven = c["vencidas"]
    deb = c["debitos"]
    return [
        ("Para facturar", f"{len(sf)}", f"{pesos(float(sf['_monto'].sum()) if len(sf) else 0, 0)}"
         + (f" · {len(c['sin_facturar_vencido'])} con el plazo vencido" if len(c["sin_facturar_vencido"]) else ""), "#8C2853"),
        ("Por cobrar", pesos(float(pend["_pendiente"].sum()) if len(pend) else 0, 0), f"{len(pend)} factura(s)", "#4F7CAC"),
        ("Vencido", pesos(float(ven["_pendiente"].sum()) if len(ven) else 0, 0), f"{len(ven)} factura(s) para reclamar",
         "#BD4B4B" if len(ven) else "#5E9C76"),
        ("Débitos abiertos", pesos(float(deb["_debito"].sum()) if len(deb) else 0, 0),
         f"{len(deb)} para reclamar · {len(c['parcial_sin_debito'])} parciales sin anotar", "#C9895B"),
    ]


def _titulo(numero: str, titulo: str, que: str) -> str:
    num = f'<span class="fp-num">{html.escape(numero)}</span>' if numero else ""
    return (f'<div class="fp-paso">{num}<span class="fp-tit">{html.escape(titulo)}</span></div>'
            + (f'<div class="fp-que">{html.escape(que)}</div>' if que else ""))


def _chips(items: list[tuple[str, str, str]]) -> str:
    return "".join(f'<span class="fp-chip" style="background:{f};color:{t}">{html.escape(x)}</span>' for x, f, t in items)


def _dias_txt(dias: Any, futuro: str, pasado: str) -> str:
    if dias is None or pd.isna(dias):
        return ""
    d = int(dias)
    return futuro.format(d) if d >= 0 else pasado.format(-d)


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------
def render_pasos(*, module_name: str, cfg: dict[str, Any], table: str, empresa: str, key: str,
                 raw: pd.DataFrame, guardar: Callable[[pd.DataFrame], Any],
                 leer_tabla: Callable[[str], pd.DataFrame | None], guardar_tabla: Callable[[str, pd.DataFrame], Any],
                 legacy_renderer: Callable[..., None] | None = None, usuario: str = "",
                 hoy: date | None = None, movil: bool = False, puede_editar: bool = False) -> None:
    import streamlit as st

    from vitae.core.ui import tarjetas_kpi

    st.markdown(cf._CSS + _CSS, unsafe_allow_html=True)
    hoy_d = hoy or date.today()
    # REGLA PRINCIPAL: VMR (fertilidad) y VM (quirófano) son distintas y no se mezclan: cada una se controla
    # sólo con su planilla (no se lee la de la otra empresa). La Agenda era un respaldo: no se compara.
    descartadas = leer_tabla(cf.TABLA_DESCARTADAS)
    ctx = cf.contexto(raw, empresa=empresa, agenda_raw=leer_tabla("agenda_quirofano"), hoy=hoy_d,
                      descartadas=descartadas)
    from vitae.modulos import presentaciones as pr

    ctx["requisitos"] = leer_tabla(pr.TABLA_REQ)
    ctx["puede_editar"] = bool(puede_editar)  # sólo rchar
    n = cuentas(ctx)
    cierres = leer_tabla(cfx.TABLA_CIERRES)
    actual = f"{hoy_d.year:04d}-{hoy_d.month:02d}"
    ci_ = cfx.normalizar_cierres(cierres)
    cerrados = set(ci_.loc[ci_["empresa"] == empresa, "mes"])
    sin_cerrar = [m for m in cfx.meses_con_datos(ctx["d"]) if m < actual and m[:4] == actual[:4] and m not in cerrados]
    n["cierre"] = len(sin_cerrar)

    tarjetas_kpi(indicadores(ctx))

    # ---- Navegación: pasos numerados y, aparte, Análisis y Planilla -------------------
    k_v, k_p, k_e = f"{key}_vista", f"{key}_pasos", f"{key}_extras"
    if k_v not in st.session_state:
        st.session_state[k_v] = INICIO
    vista = st.session_state[k_v]

    def ir(destino: str) -> Callable[[], None]:
        def _cb() -> None:
            st.session_state[k_v] = destino
            st.session_state[k_p] = destino if destino in PASOS else None
            st.session_state[k_e] = destino if destino in EXTRAS else None
            st.session_state[f"{key}_movil"] = destino
        return _cb

    def etiqueta(v: str) -> str:
        cant = n.get(v)
        return f"{NOMBRES[v]} ({cant})" if cant else NOMBRES[v]

    if movil:
        todas = PASOS + EXTRAS
        if f"{key}_movil" not in st.session_state:
            st.session_state[f"{key}_movil"] = vista
        elegido = st.selectbox("Paso", todas, key=f"{key}_movil", label_visibility="collapsed",
                               format_func=lambda v: etiqueta(v) if v in PASOS else f"Más · {NOMBRES[v]}")
        vista = elegido or INICIO
        st.session_state[k_v] = vista
    else:
        if k_p not in st.session_state:
            st.session_state[k_p] = vista if vista in PASOS else None
        if k_e not in st.session_state:
            st.session_state[k_e] = vista if vista in EXTRAS else None

        def _al_paso() -> None:
            v = st.session_state.get(k_p)
            if v:
                st.session_state[k_v] = v
                st.session_state[k_e] = None
            elif st.session_state.get(k_v) in PASOS:
                st.session_state[k_p] = st.session_state[k_v]

        def _al_extra() -> None:
            v = st.session_state.get(k_e)
            if v:
                st.session_state[k_v] = v
                st.session_state[k_p] = None
            elif st.session_state.get(k_v) in EXTRAS:
                st.session_state[k_e] = st.session_state[k_v]

        c1, c2 = st.columns([3.2, 1.6])
        with c1:
            st.segmented_control("Paso a paso", PASOS, key=k_p, format_func=etiqueta, on_change=_al_paso,
                                 label_visibility="collapsed")
        with c2:
            st.segmented_control("Más", EXTRAS, key=k_e, format_func=lambda v: NOMBRES[v], on_change=_al_extra,
                                 label_visibility="collapsed")
        vista = st.session_state.get(k_v, INICIO)

    def siguiente(actual_: str) -> None:
        i = PASOS.index(actual_)
        if i + 1 < len(PASOS):
            dest = PASOS[i + 1]
            st.button(f"Siguiente paso: {NOMBRES[dest]} →", key=f"{key}_sig_{actual_}", on_click=ir(dest),
                      icon=":material/arrow_forward:")

    # ---- Planilla: el flujo de gestión de siempre ------------------------------------
    if vista in LEGACY:
        if legacy_renderer is None:
            st.info("La planilla no está disponible en esta pantalla.")
            return
        legacy_renderer(module_name, cfg, workspace=LEGACY[vista], embebido=True)
        return

    data, cols = fx._prepare_facturacion(raw)
    data = fx._ajustar_estado_manual(data)

    if vista == "exportar":
        st.markdown(_titulo("", "Exportar", "Elegí el período con los filtros y descargá; abajo, la planilla completa."),
                    unsafe_allow_html=True)
        filtrado, _ = fx._panel_filtros(data, key)
        fx._seccion_exportar(filtrado, fx._metrics(filtrado), module_name, cfg, table, key, legacy_renderer)
        return

    if vista == "analisis":
        st.markdown(_titulo("", "Análisis", "Cómo viene la facturación: evolución, cobranza, producción y calidad. "
                            "Usá los filtros para elegir el período."), unsafe_allow_html=True)
        sub = st.pills("Ver", ANALISIS, default="Resumen", key=f"{key}_an") or "Resumen"
        filtrado, detalle = fx._panel_filtros(data, key)
        if filtrado.empty:
            st.info("Ningún registro coincide con los filtros. Probá ampliar el período o tocar «Limpiar».")
            return
        metrics = fx._metrics(filtrado)
        fx._tarjetas(metrics)
        st.caption(detalle)
        if sub == "Resumen":
            fx._seccion_resumen(filtrado, metrics, module_name, key)
            fx._registros_del_periodo(filtrado, detalle)
        elif sub == "Cobranza":
            fx._seccion_cobranza(filtrado, metrics, key)
        elif sub == "Producción":
            fx._seccion_produccion(filtrado)
        else:
            fx._seccion_calidad(filtrado, cols, key)
        return

    # ---- Registros: todos, en planilla ------------------------------------------------------
    if vista == "fichas":
        d = ctx["d"]
        st.markdown(_titulo("", "Registros", ""), unsafe_allow_html=True)
        d, periodo = filtro_periodo(st, d, ctx["hoy"], key=f"{key}_reg")
        modo = "Ver y avanzar"
        if ctx.get("puede_editar"):  # «Editar en tabla» sólo para rchar
            modo = st.segmented_control("Modo", ["Ver y avanzar", "Editar en tabla"], default="Ver y avanzar",
                                        key=f"{key}_reg_modo", label_visibility="collapsed") or "Ver y avanzar"
        cnt = ff.cuentas_filtros(d)
        c1, c2 = st.columns([4, 1.3])
        with c1:
            filtro = st.pills("Mostrar", ff.FILTROS, default="Todas", key=f"{key}_ff_filtro",
                              format_func=lambda f_: f"{f_} {cnt.get(f_, 0)}", label_visibility="collapsed") or "Todas"
        with c2:
            if st.button("Nuevo registro", key=f"{key}_nuevo", type="primary", use_container_width=True,
                         icon=":material/add:"):
                ff.dialogo_nuevo(st, ctx, guardar, key)
        x = ff.filtrar(d, filtro)
        if modo == "Editar en tabla":
            fz.render_edicion(st, ctx, x, guardar=guardar, key=key)
            return
        fz.render_registros(st, ctx, x, guardar=guardar, key=key, lista=f"todas_{filtro}_{periodo}",
                            vacio=f"No hay registros en este grupo ({periodo}).")
        return

    # ---- Hoy ---------------------------------------------------------------------------
    if vista == "hoy":
        st.markdown(_titulo("", "Mes", "Cómo viene el mes: todo lo registrado día por día, qué falta y dónde se "
                            "resuelve. Elegí otro mes para ver qué quedó pendiente."), unsafe_allow_html=True)
        fm.render_mes(st, ctx, key=key, ir=ir, guardar=guardar, nombres=NOMBRES)
        return

    # ---- 1 · Cargar -------------------------------------------------------------------------
    if vista == "cargar":
        st.markdown(_titulo("1", "Cargar", "Cada prestación cargada con sus datos completos (obra social, "
                            "procedimiento e importe). Lo que no está cargado no se factura."), unsafe_allow_html=True)
        c = ctx["c"]
        if not n["cargar"]:
            st.markdown('<div class="fp-ok">✅ Sin solicitudes de la Agenda y con los datos de todo lo cargado '
                        'completos.</div>', unsafe_allow_html=True)
        fz.render_solicitudes(st, ctx, guardar=guardar, guardar_tabla=guardar_tabla, descartadas=descartadas, key=key,
                              usuario=usuario)
        _fechas_sin_numero(st, ctx, guardar=guardar, key=key)
        x, _, _ = cf.grupo(c, "Completar datos")
        if len(x):
            rep_ = c["repetidas"].index if len(c["repetidas"]) else []
            notas = {i: f"N° {x.loc[i, '_numero_factura']} repetido" for i in x.index if i in rep_}
            fz.render_lista(st, ctx, x, guardar=guardar, key=key, lista="completar", forzar_incompleta=True,
                            notas=notas, vista="Por paciente", titulo="Datos para completar")
        if st.button("Nuevo registro", key=f"{key}_nuevo_cargar", icon=":material/add_circle:"):
            ff.dialogo_nuevo(st, ctx, guardar, key)
        st.button("Ver o editar todos en Registros", key=f"{key}_ir_registros", on_click=ir("fichas"),
                  icon=":material/table_rows:")
        with st.expander("Calidad de la planilla (qué columnas se leen y qué falta)", icon=":material/verified:"):
            fx._seccion_calidad(data, cols, f"{key}_cal")
        siguiente("cargar")
        return

    # ---- 2 · Facturar -------------------------------------------------------------------------
    if vista == "facturar":
        c = ctx["c"]
        st.markdown(_titulo("2", "Facturar", f"Todo lo cargado tiene que facturarse dentro de los "
                            f"{cf.PLAZO_PRESENTACION} días de la prestación. Cargá el número y la fecha de factura."),
                    unsafe_allow_html=True)
        resto = len(c["sin_facturar"]) - len(c["sin_facturar_vencido"]) - len(c["sin_facturar_proximo"])
        st.markdown(_chips([(f"Plazo vencido: {len(c['sin_facturar_vencido'])}", "#FBE4E4", "#8A1F1F"),
                            (f"Vence en {cf.AVISO_PRESENTACION} días o menos: {len(c['sin_facturar_proximo'])}",
                             "#FDF0DC", "#7A4B00"),
                            (f"Con plazo: {resto}", "#F4F1F3", "#5E1A39"),
                            (f"Total sin facturar: {pesos(float(c['sin_facturar']['_monto'].sum()) if len(c['sin_facturar']) else 0, 0)}",
                             "#EEF4FB", "#2F4F7A")]), unsafe_allow_html=True)
        modo = st.pills("Facturar", ["Por obra social", "Por paciente"], default="Por obra social", key=f"{key}_fmodo",
                        label_visibility="collapsed") or "Por obra social"
        if modo == "Por obra social":
            _presentaciones(st, ctx, key=key, guardar=guardar, leer_tabla=leer_tabla, guardar_tabla=guardar_tabla,
                            usuario=usuario)
        elif c["sin_facturar"].empty:
            st.markdown('<div class="fp-ok">✅ No queda nada sin facturar.</div>', unsafe_allow_html=True)
        else:
            x, _, _ = cf.grupo(c, "Facturar")
            fz.render_lista(st, ctx, x, guardar=guardar, key=key, lista="facturar", vista="Por paciente",
                            titulo="Sin facturar")
        siguiente("facturar")
        return

    # ---- 3 · Cobrar -------------------------------------------------------------------------
    if vista == "cobrar":
        c = ctx["c"]
        st.markdown(_titulo("3", "Cobrar", "Seguir cada factura hasta cobrarla: primero las vencidas (de la más "
                            "grande a la más chica), después las que vencen esta semana."), unsafe_allow_html=True)
        ven = c["vencidas"]
        st.markdown(_chips([(f"Vencidas: {len(ven)} · {pesos(float(ven['_pendiente'].sum()) if len(ven) else 0, 0)}",
                             "#FBE4E4", "#8A1F1F"),
                            (f"Vencen en {cf.POR_VENCER} días: {len(c['por_vencer'])}", "#FDF0DC", "#7A4B00"),
                            (f"Sin vencimiento: {len(c['sin_vencimiento'])}", "#F4F1F3", "#5E1A39")]),
                    unsafe_allow_html=True)
        x, _, _ = cf.grupo(c, "Cobrar")
        if x.empty:
            st.markdown('<div class="fp-ok">✅ Nada vencido ni por vencer esta semana.</div>', unsafe_allow_html=True)
        else:
            fz.render_lista(st, ctx, x, guardar=guardar, key=key, lista="cobrar", titulo="Para cobrar")
        st.markdown('<div class="cf-tit">Seguimiento de la cobranza</div>', unsafe_allow_html=True)
        fx._seccion_cobranza(data, fx._metrics(data), f"{key}_cob")
        siguiente("cobrar")
        return

    # ---- 4 · Débitos -------------------------------------------------------------------------
    if vista == "debitos":
        c, d = ctx["c"], ctx["d"]
        st.markdown(_titulo("4", "Débitos", "Lo que la obra social pagó de menos: anotar cuánto y por qué, reclamarlo "
                            "y registrar si se recuperó o se aceptó."), unsafe_allow_html=True)
        todos = d[(~d["_cancelado"]) & (d["_debito"] > 0.004)] if len(d) else d
        est = todos["_debito_estado"].astype(str).str.lower() if len(todos) else pd.Series(dtype=str)
        st.markdown(_chips([
            (f"Abiertos: {pesos(float(c['debitos']['_debito'].sum()) if len(c['debitos']) else 0, 0)}", "#FBE4E4", "#8A1F1F"),
            (f"Recuperado: {pesos(float(todos.loc[est.str.contains('recuper', na=False), '_debito'].sum()) if len(todos) else 0, 0)}",
             "#EEF8F0", "#1F5130"),
            (f"Aceptado: {pesos(float(todos.loc[est.str.contains('acept', na=False), '_debito'].sum()) if len(todos) else 0, 0)}",
             "#F4F1F3", "#5E1A39"),
            (f"Parciales sin anotar: {len(c['parcial_sin_debito'])}", "#FDF0DC", "#7A4B00")]), unsafe_allow_html=True)
        x, _, _ = cf.grupo(c, "Débitos")
        if x.empty:
            st.markdown('<div class="fp-ok">✅ No hay débitos para reclamar.</div>', unsafe_allow_html=True)
        else:
            fz.render_lista(st, ctx, x, guardar=guardar, key=key, lista="debitos", titulo="Débitos")
        if len(todos):
            st.markdown('<div class="cf-tit">Débitos por obra social</div>', unsafe_allow_html=True)
            t = todos.assign(_e=todos["_debito_estado"].replace("", "A reclamar")).pivot_table(
                index="_obra_social", columns="_e", values="_debito", aggfunc="sum", fill_value=0.0)
            t["Total"] = t.sum(axis=1)
            t = t.sort_values("Total", ascending=False).reset_index().rename(columns={"_obra_social": "Obra social"})
            st.dataframe(t.assign(**{c_: t[c_].map(lambda v: pesos(v, 0) if v else "") for c_ in t.columns
                                     if c_ != "Obra social"}), hide_index=True, use_container_width=True)
        siguiente("debitos")
        return

    # ---- 5 · Cierre -------------------------------------------------------------------------
    if vista == "cierre":
        _cierre(st, ctx, cierres, key=key, ir=ir, guardar_tabla=guardar_tabla, usuario=usuario)
        return


def _cierre(st: Any, ctx: dict[str, Any], cierres: pd.DataFrame | None, *, key: str,
            ir: Callable[[str], Callable[[], None]], guardar_tabla: Callable[[str, pd.DataFrame], Any],
            usuario: str) -> None:
    from vitae.core.ui import tarjetas_kpi
    from vitae.modulos import facturacion_pdf as pdf

    empresa, d, hoy_d = ctx["empresa"], ctx["d"], ctx["hoy"]
    st.markdown(_titulo("5", "Cierre del mes", "Cada mes (por fecha de prestación) se controla y se cierra: todo "
                        "cargado, facturado y con los datos completos. Al cerrarlo quedan guardados los números y "
                        "sale el PDF."), unsafe_allow_html=True)
    actual = f"{hoy_d.year:04d}-{hoy_d.month:02d}"
    anios = sorted({int(m[:4]) for m in cfx.meses_con_datos(d)} | {hoy_d.year}, reverse=True)
    c1, c2 = st.columns([1, 3])
    anio_ = c1.selectbox("Año", anios, index=0, key=f"{key}_ci_anio")
    tabla = cfx.anio(ctx, cierres, int(anio_))
    vista = tabla.drop(columns="_mes").assign(**{k: tabla[k].map(lambda v: pesos(v, 0)) for k in
                                                ("Facturado", "Cobrado", "Por cobrar", "Débitos")})
    st.dataframe(vista, hide_index=True, use_container_width=True, height=min(38 + 35 * len(vista), 470))
    nums_anio = cfx.numeros(d, anio=int(anio_))
    with c2:
        st.download_button(f"PDF del año {anio_}", data=pdf.pdf_anio(empresa, int(anio_), tabla, nums_anio),
                           file_name=f"facturacion_{empresa.lower()}_{anio_}.pdf", mime="application/pdf",
                           key=f"{key}_pdf_anio", icon=":material/picture_as_pdf:")

    # Mes elegido: por defecto, el primero que falta cerrar
    meses = list(tabla["_mes"])
    pendientes = [m for m, e in zip(tabla["_mes"], tabla["Estado"]) if m < actual and "Cerrado" not in e
                  and "Sin prestaciones" not in e]
    defecto = pendientes[0] if pendientes else (meses[-1] if meses else actual)
    mes = st.selectbox("Mes", meses, index=meses.index(defecto) if defecto in meses else 0, key=f"{key}_ci_mes_{anio_}",
                       format_func=lambda m: f"{cfx.mes_txt(m).capitalize()} · "
                                             f"{tabla.set_index('_mes').loc[m, 'Estado']}")
    e = cfx.estado_mes(ctx, cierres, mes)
    nums = cfx.numeros(d, mes)
    tarjetas_kpi([
        ("Prestaciones", str(nums["prestaciones"]), f"{nums['sin_facturar']} sin facturar", "#8C2853"),
        ("Facturado", pesos(nums["facturado"], 0), f"Cobrado {pesos(nums['cobrado'], 0)}", "#4F7CAC"),
        ("Por cobrar", pesos(nums["por_cobrar"], 0), f"Vencido {pesos(nums['vencido'], 0)}",
         "#BD4B4B" if nums["vencido"] > 0.5 else "#5E9C76"),
        ("Débitos", pesos(nums["debitos"], 0), f"Abiertos {pesos(nums['debitos_abiertos'], 0)}", "#C9895B"),
    ])

    st.markdown('<div class="cf-tit">Control del mes</div>', unsafe_allow_html=True)
    for i, x in enumerate(e["lista"]):
        clase = "" if x["ok"] else ("mal" if x["traba"] else "info")
        icono = "✅" if x["ok"] else ("⛔" if x["traba"] else "⚠️")
        c1, c2 = st.columns([6, 1.2])
        c1.markdown(f'<div class="fp-ck {clase}"><div>{icono} <b>{html.escape(x["punto"])}</b>'
                    f'<div class="fp-sub">{html.escape(x["detalle"])}</div></div>'
                    f'<div style="white-space:nowrap"><b>{x["casos"] or ""}</b></div></div>', unsafe_allow_html=True)
        if not x["ok"]:
            c2.button(f"Ir a {NOMBRES[x['paso']]}", key=f"{key}_ci_ir_{i}", on_click=ir(x["paso"]),
                      use_container_width=True)

    cier = e["cierre"]
    if cier:
        txt = f"🔒 Cerrado el {cier.get('cerrado_el', '')}" + (f" por {cier['usuario']}" if cier.get("usuario") else "")
        if cier.get("observaciones"):
            txt += f" · {cier['observaciones']}"
        st.markdown(f'<div class="fp-box">{html.escape(txt)}</div>', unsafe_allow_html=True)
        if e["cambio"]:
            antes = {k: float(cier.get(k) or 0) for k in ("facturado", "cobrado", "por_cobrar", "debitos")}
            st.warning("Hubo cambios en este mes después del cierre. Al cerrar: facturado "
                       f"{pesos(antes['facturado'], 0)}, cobrado {pesos(antes['cobrado'], 0)}, por cobrar "
                       f"{pesos(antes['por_cobrar'], 0)}. Ahora: facturado {pesos(nums['facturado'], 0)}, cobrado "
                       f"{pesos(nums['cobrado'], 0)}, por cobrar {pesos(nums['por_cobrar'], 0)}. Si los cambios están "
                       "bien (por ejemplo, cobros nuevos), volvé a cerrarlo.")

    b1, b2, b3 = st.columns(3)
    listo = cfx.listo_para_cerrar(e["lista"])
    if mes >= actual:
        b1.info("El mes todavía no terminó.")
    elif not cier or e["cambio"]:
        obs = st.text_input("Observaciones del cierre (opcional)", key=f"{key}_ci_obs_{mes}")
        if b1.button("Volver a cerrar" if cier else f"Cerrar {cfx.mes_txt(mes)}", type="primary", disabled=not listo,
                     key=f"{key}_ci_ok_{mes}", icon=":material/lock:", use_container_width=True,
                     help=None if listo else "Primero resolvé los puntos en rojo (⛔)."):
            nuevo = cfx.cerrar(cierres, empresa, mes, nums, cfx.huella(d, mes), usuario, obs)
            try:
                guardar_tabla(cfx.TABLA_CIERRES, nuevo)
            except Exception as exc:
                st.error(f"No se pudo guardar el cierre: {exc}")
            else:
                st.toast(f"{cfx.mes_txt(mes).capitalize()} cerrado", icon=":material/lock:")
                st.rerun()
    if cier and b2.button("Reabrir el mes", key=f"{key}_ci_reabrir_{mes}", icon=":material/lock_open:",
                          use_container_width=True):
        try:
            guardar_tabla(cfx.TABLA_CIERRES, cfx.reabrir(cierres, empresa, mes))
        except Exception as exc:
            st.error(f"No se pudo reabrir: {exc}")
        else:
            st.rerun()
    b3.download_button(f"PDF de {cfx.mes_txt(mes)}", data=pdf.pdf_mes(empresa, mes, nums, e["lista"], cier, e["cambio"]),
                       file_name=f"facturacion_{empresa.lower()}_{mes}.pdf", mime="application/pdf",
                       key=f"{key}_pdf_mes", icon=":material/picture_as_pdf:", use_container_width=True)
    if len(nums["por_obra_social"]):
        st.markdown('<div class="cf-tit">Por obra social</div>', unsafe_allow_html=True)
        t = nums["por_obra_social"]
        st.dataframe(t.assign(**{c_: t[c_].map(lambda v: pesos(v, 0)) for c_ in ("Facturado", "Cobrado", "Por cobrar",
                                                                               "Débitos")}),
                     hide_index=True, use_container_width=True)


def _presentaciones(st: Any, ctx: dict[str, Any], *, key: str, guardar: Callable[[pd.DataFrame], Any],
                    leer_tabla: Callable[[str], pd.DataFrame | None], guardar_tabla: Callable[[str, pd.DataFrame], Any],
                    usuario: str) -> None:
    """Facturar por obra social: armar la presentación (lote), seguir las ya presentadas y la documentación."""
    from vitae.modulos import presentaciones as pr

    d, empresa, req = ctx["d"], ctx["empresa"], ctx.get("requisitos")
    pres = leer_tabla(pr.TABLA_PRES)
    rev = int(st.session_state.get(f"{key}_cf_rev", 0))

    def _ok(aviso: str) -> None:
        st.session_state[f"{key}_cf_rev"] = rev + 1
        st.toast(aviso, icon=":material/check_circle:")
        st.rerun()

    def armar_dialogo(os_: str) -> None:
        x = pr.sin_facturar(d)
        x = x[x["_obra_social"] == os_].sort_values(["_prestacion", "_paciente"], na_position="last")
        pedidos = pr.requisitos(req, empresa, os_)
        falta = [pr.faltan(r["_documentacion"], pedidos) for _, r in x.iterrows()]
        tabla = pd.DataFrame({"fila": x.index, "Incluir": [not f for f in falta], "Paciente": x["_paciente"],
                              "Prestación": x["_prestacion"].map(lambda v: pr.fecha_corta(v)),
                              "Procedimiento": x["_procedimiento"], "Importe": x["_monto"],
                              "Documentación": ["completa ✓" if not f else "falta " + ", ".join(f) for f in falta]})
        if not tabla["Incluir"].any():
            tabla["Incluir"] = True
        st.caption("Tildá las prestaciones que van en esta presentación (por defecto, las que tienen la documentación "
                   "completa).")
        ed = st.data_editor(tabla.reset_index(drop=True), hide_index=True, use_container_width=True,
                            key=f"{key}_pr_ed_{os_}_{rev}", disabled=["Paciente", "Prestación", "Procedimiento", "Importe",
                                                                      "Documentación"],
                            column_config={"fila": None, "Importe": st.column_config.NumberColumn(format="localized")})
        elegidas = [int(f_) for f_, inc in zip(ed["fila"], ed["Incluir"]) if inc]
        c1, c2 = st.columns([1.3, 1])
        periodo = c1.text_input("Período", value=pr.periodo_sugerido(x.loc[elegidas] if elegidas else x), key=f"{key}_pr_per")
        modo = st.radio("Facturas", [pr.UNA, pr.CORRELATIVAS], horizontal=False, key=f"{key}_pr_modo")
        c1, c2, c3 = st.columns(3)
        numero = c1.text_input("N° de factura" if modo == pr.UNA else "Primer N°", value=ff.sugerir_numero(d),
                               key=f"{key}_pr_num")
        fecha = c2.date_input("Fecha", value=ctx["hoy"], format="DD/MM/YYYY", key=f"{key}_pr_f")
        dias = ff.dias_vencimiento(d, os_)
        venc = c3.date_input("Vencimiento", value=pd.Timestamp(fecha or ctx["hoy"]).date() + pd.Timedelta(days=dias),
                             format="DD/MM/YYYY", key=f"{key}_pr_v", help=f"Sugerido: {dias} días.")
        total = float(x.loc[elegidas, "_monto"].sum()) if elegidas else 0.0
        if modo == pr.CORRELATIVAS and elegidas and _txt(numero):
            nums = pr.correlativos(numero, len(elegidas))
            st.caption(f"Números: {nums[0]} a {nums[-1]}.")
        if st.button(f"Facturar {len(elegidas)} prestación(es) · {pesos(total, 0)}", type="primary",
                     key=f"{key}_pr_ok", icon=":material/receipt_long:", use_container_width=True):
            try:
                raw, p, pid = pr.armar(ctx, elegidas, obra_social=os_, periodo=periodo, modo=modo, numero=numero,
                                       fecha=fecha, vencimiento=venc, pres=pres, usuario=usuario)
                guardar(raw)
                guardar_tabla(pr.TABLA_PRES, p)
            except Exception as exc:
                st.error(str(exc))
                return
            _ok(f"Presentación {os_} facturada: {len(elegidas)} prestación(es)")

    def cobro_dialogo(info: dict[str, Any]) -> None:
        st.markdown(f'<div class="ff-sub">{_esc(info["obra_social"])} · {_esc(info["periodo"])} · saldo '
                    f'{pesos(info["pendiente"], 0)}</div>', unsafe_allow_html=True)
        fecha = st.date_input("Fecha de pago", value=ctx["hoy"], format="DD/MM/YYYY", key=f"{key}_pc_f_{info['id']}")
        st.caption("Marca como cobradas todas las prestaciones del lote que siguen pendientes. Si a alguna le pagaron de "
                   "menos, registralo después en 3 · Cobrar (cobro de una parte).")
        if st.button("Registrar cobro del lote", type="primary", key=f"{key}_pc_ok_{info['id']}", use_container_width=True):
            cambios = pr.cambios_cobro_lote(d, info["id"], fecha)
            if not cambios:
                st.info("No queda nada pendiente en este lote.")
                return
            guardar(cf.aplicar(ctx["raw"], ctx["cols"], cambios, ctx["hoy"]))
            _ok(f"Lote {info['obra_social']} cobrado")

    # Para presentar
    t = pr.por_obra_social(d, req, empresa)
    st.markdown('<div class="cf-tit">Para presentar</div><div class="fp-sub">Una presentación por obra social: '
                'elegís las prestaciones, el N° y la fecha una sola vez y quedan todas facturadas.</div>',
                unsafe_allow_html=True)
    if t.empty:
        st.markdown('<div class="fp-ok">✅ No queda nada sin facturar.</div>', unsafe_allow_html=True)
    else:
        with st.container(border=True):
            c1, c2 = st.columns([3.4, 1.6], vertical_alignment="bottom")
            os_sel = c1.selectbox("Armar la presentación de", list(t["Obra social"]), index=None, key=f"{key}_pr_sel",
                                  placeholder="Elegí la obra social…",
                                  format_func=lambda o: f"{o} · {int(t.set_index('Obra social').loc[o, 'Prestaciones'])} "
                                                        f"prestación(es) · {pesos(t.set_index('Obra social').loc[o, 'Importe'], 0)}")
            if c2.button("Armar presentación", key=f"{key}_pr_armar", type="primary", disabled=os_sel is None,
                         use_container_width=True, icon=":material/inventory_2:"):
                st.dialog(f"Presentación · {os_sel}", width="large")(armar_dialogo)(os_sel)
        st.markdown(fz.html_para_presentar(t), unsafe_allow_html=True)

    # Ya presentadas
    hechas = pr.presentaciones(pres, d, empresa)
    if len(hechas):
        c1, c2 = st.columns([3, 1.4], vertical_alignment="bottom")
        c1.markdown('<div class="cf-tit">Presentaciones hechas</div>', unsafe_allow_html=True)
        ver = c2.segmented_control("Ver", ["Por cobrar", "Todas"], default="Por cobrar", key=f"{key}_pr_ver",
                                   label_visibility="collapsed") or "Por cobrar"
        if ver == "Por cobrar":
            hechas = hechas[hechas["pendiente"] > 0.5]
        hechas = hechas.head(60).reset_index(drop=True)
        if hechas.empty:
            st.markdown('<div class="fp-ok">✅ Todas las presentaciones están cobradas.</div>', unsafe_allow_html=True)
        else:
            with st.container(border=True):
                c1, c2, c3 = st.columns([3.4, 1.2, 1.4], vertical_alignment="bottom")
                j = c1.selectbox("Presentación", list(hechas.index), index=None, key=f"{key}_pr_hsel",
                                 placeholder="Elegí la presentación para cobrarla o bajar el detalle…",
                                 format_func=lambda j_: f"{hechas.loc[j_, 'obra_social']} · {hechas.loc[j_, 'periodo']} · "
                                                        f"Fact. {hechas.loc[j_, 'numero']} · saldo "
                                                        f"{pesos(hechas.loc[j_, 'pendiente'], 0)}")
                if j is not None:
                    h = hechas.loc[j]
                    c2.download_button("Detalle", data=pr.excel_lote(d, h.to_dict()), key=f"{key}_pr_x_{h['id']}",
                                       file_name=f"presentacion_{h['obra_social']}_{str(h['fecha']).replace('/', '-')}.xlsx",
                                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                       icon=":material/download:", use_container_width=True)
                    if h["pendiente"] > 0.5 and c3.button("Registrar cobro", key=f"{key}_pr_c_{h['id']}", type="primary",
                                                          use_container_width=True, icon=":material/payments:"):
                        st.dialog(f"Cobro del lote · {h['obra_social']}")(cobro_dialogo)(h.to_dict())
            st.markdown(fz.html_presentadas(hechas), unsafe_allow_html=True)

    # Documentación que pide cada obra social
    with st.expander("Documentación que pide cada obra social", icon=":material/fact_check:"):
        st.caption("Una línea por obra social; los documentos separados por «|». «Todas» vale para las que no tienen nada "
                   "cargado.")
        obras = ff.opciones(d, "_obra_social", "SIN OBRA SOCIAL")
        base = pr.tabla_req(req, empresa, obras)
        ed = st.data_editor(base, hide_index=True, use_container_width=True, num_rows="dynamic", key=f"{key}_req_{rev}")
        if st.button("Guardar documentación", key=f"{key}_req_ok", icon=":material/save:"):
            guardar_tabla(pr.TABLA_REQ, pr.guardar_req(req, empresa, ed))
            _ok("Documentación por obra social guardada")


def cambios_fechas_sin_numero(x: pd.DataFrame) -> list[dict[str, Any]]:
    """Borra la fecha de factura y el vencimiento de las filas sin N° (no son facturas)."""
    out: list[dict[str, Any]] = []
    for i in x.index:
        if pd.notna(x.loc[i, "_fecha_factura"]):
            out.append({"fila": int(i), "campo": "fecha_factura", "valor": ""})
        if pd.notna(x.loc[i, "_vencimiento"]):
            out.append({"fila": int(i), "campo": "vencimiento", "valor": ""})
    return out


def _fechas_sin_numero(st: Any, ctx: dict[str, Any], *, guardar: Callable[[pd.DataFrame], Any], key: str) -> None:
    x = ctx["c"].get("fecha_sin_numero")
    if x is None or x.empty:
        return
    st.markdown(f'<div class="cf-tit">Fecha de factura sin N° · {len(x)}</div>', unsafe_allow_html=True)
    c1, c2 = st.columns([3, 1.6], vertical_alignment="center")
    c1.markdown('<div class="fp-sub">Tienen fecha de factura o vencimiento pero no N° de factura: no cuentan como '
                'facturadas. Si se facturaron, poné el N° en Registros; si no, borrá esas fechas.</div>',
                unsafe_allow_html=True)
    if c2.button(f"Borrar esas fechas ({len(x)})", key=f"{key}_fsn_borrar", use_container_width=True,
                 icon=":material/event_busy:"):
        try:
            guardar(cf.aplicar(ctx["raw"], ctx["cols"], cambios_fechas_sin_numero(x), ctx["hoy"]))
        except Exception as exc:
            st.error(f"No se pudo guardar: {exc}")
            return
        st.toast(f"Fechas borradas en {len(x)} registro(s)", icon=":material/check_circle:")
        st.rerun()
    st.markdown(fz.html_planilla(x, ctx["hoy"], "Por paciente", titulo="Fecha de factura sin N°"),
                unsafe_allow_html=True)


TODOS_ANIOS = "Todos los años"
TODO_EL_ANIO = "Todo el año"


def filtro_periodo(st: Any, d: pd.DataFrame, hoy: date, *, key: str) -> tuple[pd.DataFrame, str]:
    """Año y mes (por fecha de prestación). Abre en el mes actual. Devuelve (filas, texto del período)."""
    import vitae.modulos.cierre_facturacion as cfx_

    f = d["_prestacion"] if len(d) else pd.Series(dtype="datetime64[ns]")
    anios = sorted({int(a) for a in f.dropna().dt.year} | {hoy.year}, reverse=True)
    c1, c2, _ = st.columns([1, 1.4, 2.6], vertical_alignment="bottom")
    k_a, k_m = f"{key}_anio", f"{key}_mes"
    if st.session_state.get(k_a) not in [TODOS_ANIOS] + anios:
        st.session_state[k_a] = hoy.year
    anio = c1.selectbox("Año", [TODOS_ANIOS] + anios, key=k_a)
    if anio == TODOS_ANIOS:
        return d, "todos los años"
    del_anio = d[f.dt.year == anio] if len(d) else d
    meses = list(range(12, 0, -1))
    if st.session_state.get(k_m) not in [0] + meses:
        st.session_state[k_m] = hoy.month if anio == hoy.year else 0
    cuenta = del_anio["_prestacion"].dt.month.value_counts() if len(del_anio) else pd.Series(dtype=int)
    mes = c2.selectbox("Mes", [0] + meses, key=k_m,
                       format_func=lambda m: TODO_EL_ANIO if not m else
                       f"{cfx_.MESES[m - 1].capitalize()} ({int(cuenta.get(m, 0))})")
    if not mes:
        return del_anio, f"año {anio}"
    return del_anio[del_anio["_prestacion"].dt.month == mes], f"{cfx_.MESES[mes - 1]} {anio}"


def _esc(t: Any) -> str:
    return html.escape(str(t if t is not None else ""))


def _txt(v: Any) -> str:
    return str(v or "").strip()
