"""Facturación VMR / VM paso a paso.

    Hoy → 1 Cargar → 2 Facturar → 3 Cobrar → 4 Débitos → 5 Cierre          (y aparte: Análisis · Planilla)

Arriba, siempre los mismos indicadores (para facturar, por cobrar, vencido, débitos). Cada paso dice
qué hacer, muestra la lista para resolver ahí mismo y termina con «Siguiente paso».

- Hoy: el plan para ponerse al día (por dónde empezar, en etapas), la tanda del día y cómo viene
  bajando lo pendiente (se anota un renglón por día en ``facturacion_avance``).
- Cargar: cirugías de la Agenda que faltan, datos incompletos o repetidos y la calidad de la planilla.
- Facturar: lo que falta facturar, ordenado por el plazo de 60 días; números salteados.
- Cobrar: vencidas, por vencer y sin vencimiento; antigüedad, por obra social y a quién cobrar primero.
- Débitos: a reclamar, reclamados, recuperados y aceptados; cobros parciales sin el débito.
- Cierre: los meses del año con su estado; el control de cada mes; cerrar (se guarda en
  ``facturacion_cierres``) y el PDF del mes y del año.
- Análisis: lo de antes (resumen, cobranza, producción, calidad) con filtros.
- Fichas: todos los registros como tarjetas (filtros, búsqueda, nuevo registro).
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
from vitae.modulos.pagos_pendientes import pesos

PASOS = ["hoy", "cargar", "facturar", "cobrar", "debitos", "cierre"]
EXTRAS = ["fichas", "analisis", "tabla", "importar", "exportar"]
NOMBRES = {"hoy": "Hoy", "cargar": "1 · Cargar", "facturar": "2 · Facturar", "cobrar": "3 · Cobrar",
           "debitos": "4 · Débitos", "cierre": "5 · Cierre", "fichas": "Fichas", "analisis": "Análisis",
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

    return {"hoy": int(len(b)), "cargar": int(len(c["agenda"])) + n("incompletos", "repetidas"),
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
            f'<div class="fp-que">{html.escape(que)}</div>')


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
                 hoy: date | None = None, movil: bool = False) -> None:
    import streamlit as st

    from vitae.core.ui import tarjetas_kpi

    st.markdown(cf._CSS + _CSS, unsafe_allow_html=True)
    hoy_d = hoy or date.today()
    otra = "facturacion_vm" if empresa == "VMR" else "facturacion_vmr"
    ctx = cf.contexto(raw, empresa=empresa, agenda_raw=leer_tabla("agenda_quirofano"),
                      otras_facturaciones=[leer_tabla(otra)], hoy=hoy_d)
    from vitae.modulos import presentaciones as pr

    ctx["requisitos"] = leer_tabla(pr.TABLA_REQ)
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
        st.session_state[k_v] = "hoy"
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
        vista = elegido or "hoy"
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
        vista = st.session_state.get(k_v, "hoy")

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

    # ---- Fichas: todos los registros -----------------------------------------------------
    if vista == "fichas":
        d = ctx["d"]
        st.markdown(_titulo("", "Fichas", "Todos los pacientes cargados, como tarjetas. Filtrá, buscá y tocá el botón "
                            "de cada uno para avanzar."), unsafe_allow_html=True)
        cnt = ff.cuentas_filtros(d)
        c1, c2 = st.columns([4, 1.3])
        with c1:
            filtro = st.pills("Mostrar", ff.FILTROS, default="Para facturar", key=f"{key}_ff_filtro",
                              format_func=lambda f_: f"{f_} {cnt.get(f_, 0)}", label_visibility="collapsed") or "Todas"
        with c2:
            if st.button("Nuevo registro", key=f"{key}_nuevo", type="primary", use_container_width=True,
                         icon=":material/add:"):
                ff.dialogo_nuevo(st, ctx, guardar, key)
        orden = st.selectbox("Orden", ff.ORDENES, key=f"{key}_ff_orden", label_visibility="collapsed")
        x = ff.ordenar(ff.filtrar(d, filtro), orden)
        ff.render_fichas(st, ctx, x, guardar=guardar, key=key, lista=f"todas_{filtro}",
                         vacio="No hay registros en este grupo.")
        return

    # ---- Hoy ---------------------------------------------------------------------------
    if vista == "hoy":
        _hoy(st, ctx, key=key, ir=ir, leer_tabla=leer_tabla, guardar_tabla=guardar_tabla)
        return

    # ---- 1 · Cargar -------------------------------------------------------------------------
    if vista == "cargar":
        st.markdown(_titulo("1", "Cargar", "Que esté todo lo que pasó por quirófano y con los datos completos. "
                            "Lo que no está cargado no se factura."), unsafe_allow_html=True)
        c = ctx["c"]
        if c["agenda"].empty and not n["cargar"]:
            st.markdown('<div class="fp-ok">✅ Todas las cirugías realizadas están cargadas y los datos están completos.'
                        '</div>', unsafe_allow_html=True)
        ff.render_agenda_fichas(st, ctx, guardar=guardar, key=key)
        x, _, _ = cf.grupo(c, "Completar datos")
        if len(x):
            st.markdown(f'<div class="cf-tit">Datos para completar · {len(x)}</div>', unsafe_allow_html=True)
            rep_ = c["repetidas"].index if len(c["repetidas"]) else []
            notas = {i: f"N° {x.loc[i, '_numero_factura']} repetido" for i in x.index if i in rep_}
            ff.render_fichas(st, ctx, x, guardar=guardar, key=key, lista="completar", forzar_incompleta=True,
                             notas=notas)
        if st.button("Nuevo registro", key=f"{key}_nuevo_cargar", icon=":material/add_circle:"):
            ff.dialogo_nuevo(st, ctx, guardar, key)
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
            ff.render_fichas(st, ctx, x, guardar=guardar, key=key, lista="facturar")
        if len(c["repetidas"]) or not c["salteados"].empty:
            with st.expander("Numeración: repetidos y salteados", icon=":material/pin:"):
                if len(c["repetidas"]):
                    st.caption("Números de factura que aparecen más de una vez (se corrigen en 1 · Cargar → Datos para "
                               "completar).")
                    st.dataframe(pd.DataFrame({"N° factura": c["repetidas"]["_numero_factura"],
                                               "Paciente": c["repetidas"]["_paciente"],
                                               "Obra social": c["repetidas"]["_obra_social"]}),
                                 hide_index=True, use_container_width=True)
                if not c["salteados"].empty:
                    st.caption("Huecos en la numeración: revisá si son de otra facturación o si se perdieron.")
                    st.dataframe(c["salteados"], hide_index=True, use_container_width=True)
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
            ff.render_fichas(st, ctx, x, guardar=guardar, key=key, lista="cobrar")
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
            ff.render_fichas(st, ctx, x, guardar=guardar, key=key, lista="debitos")
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


def _hoy(st: Any, ctx: dict[str, Any], *, key: str, ir: Callable[[str], Callable[[], None]],
         leer_tabla: Callable[[str], pd.DataFrame | None], guardar_tabla: Callable[[str, pd.DataFrame], Any]) -> None:
    hoy_d = ctx["hoy"]
    p = cfx.plan(ctx["b"])
    st.markdown(_titulo("", "Hoy", "Lo que tenés que hacer, en orden. Si venís atrasado, seguí el plan de arriba "
                        "hacia abajo: cada etapa se resuelve en su paso."), unsafe_allow_html=True)

    # Avance: un renglón por día (sólo la primera vez que se abre en el día, o si cambió)
    av_raw = leer_tabla(cfx.TABLA_AVANCE)
    f = cfx.foto(ctx)
    marca = f"{key}_avance_{f['fecha']}_{f['pendientes']}"
    av_tabla, cambio = cfx.registrar_avance(av_raw, f)
    if cambio and not st.session_state.get(marca):
        try:
            guardar_tabla(cfx.TABLA_AVANCE, av_tabla)
            st.session_state[marca] = True
        except Exception:
            pass
    av = cfx.avance_de(av_tabla, ctx["empresa"])
    comp = cfx.comparacion(av, hoy_d)

    if p.empty:
        st.markdown('<div class="fp-ok">✅ Estás al día: nada para cargar, facturar, cobrar ni reclamar. Para mantenerlo: '
                    'cada día revisá 1 · Cargar (las cirugías de ayer) y 2 · Facturar; una vez por semana, 3 · Cobrar y '
                    '4 · Débitos; a principio de mes, 5 · Cierre del mes anterior.</div>', unsafe_allow_html=True)
    else:
        et = cfx.etapas(p)
        st.markdown('<div class="cf-tit">Plan para ponerte al día</div>'
                    f'<div class="fp-sub">{len(p)} tarea(s) en total. Empezá por la etapa 1 y seguí en orden; tocá '
                    '«Ir» para resolverla en su paso.</div>', unsafe_allow_html=True)
        for _, r in et.iterrows():
            c1, c2, c3 = st.columns([5, 1.6, 1.1])
            c1.markdown(f'<div class="fp-etapa"><span class="n">{int(r["Etapa"])}</span><div><b>{html.escape(r["Qué hacer"])}'
                        f'</b><div class="fp-sub">{html.escape(str(r["Por qué"] or ""))}</div></div></div>',
                        unsafe_allow_html=True)
            c2.markdown(f'<div style="padding-top:.45rem;text-align:right"><b>{int(r["Tareas"])}</b>'
                        f'{" · " + pesos(r["Importe"], 0) if r["Importe"] else ""}</div>', unsafe_allow_html=True)
            c3.button("Ir", key=f"{key}_ir_{int(r['Etapa'])}", on_click=ir(r["Paso"]), use_container_width=True,
                      icon=":material/arrow_forward:")

        st.markdown('<div class="cf-tit">Tanda de hoy</div>', unsafe_allow_html=True)
        c1, c2 = st.columns([1, 2.4])
        por_dia = c1.selectbox("Tareas por día", [10, 15, 20, 30, 50], index=2, key=f"{key}_por_dia")
        dias = cfx.dias_para_terminar(len(p), por_dia)
        c2.markdown(f'<div class="fp-sub" style="padding-top:1.9rem">A {por_dia} por día, te ponés al día en '
                    f'<b>{dias} día(s) hábil(es)</b>. Después, el circuito diario es mucho más corto.</div>',
                    unsafe_allow_html=True)
        tanda = p.head(por_dia)
        vista_t = pd.DataFrame({"Etapa": tanda["Etapa"], "Qué hacer": tanda["Tarea"], "Paciente": tanda["Paciente"],
                                "Obra social": tanda["Obra social"], "Importe": tanda["Importe"].map(
                                    lambda v: pesos(v) if v else ""), "Detalle": tanda["Detalle"],
                                "Dónde": tanda["Paso"].map(lambda v: NOMBRES.get(v, v))})
        st.dataframe(vista_t, hide_index=True, use_container_width=True, height=min(38 + 35 * len(vista_t), 560))

    st.markdown('<div class="cf-tit">Cómo venís</div>', unsafe_allow_html=True)
    if comp.get("anterior") is not None and comp.get("hoy") is not None:
        dif = comp["hoy"] - comp["anterior"]
        txt = (f"Hoy quedan <b>{comp['hoy']}</b> tareas; el {pd.Timestamp(comp['anterior_fecha']):%d/%m} quedaban "
               f"{comp['anterior']} ({'−' if dif < 0 else '+'}{abs(dif)}).")
        if comp.get("inicio") is not None and comp["inicio"] != comp["hoy"]:
            txt += (f" Desde el {pd.Timestamp(comp['inicio_fecha']):%d/%m}: {comp['inicio']} → {comp['hoy']}.")
        st.markdown(f'<div class="fp-box">{txt}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="fp-box">Desde hoy se anota cuánto queda pendiente cada día, para que veas cómo baja.'
                    '</div>', unsafe_allow_html=True)
    if len(av) >= 2:
        g = av.assign(Día=pd.to_datetime(av["fecha"])).set_index("Día")[["pendientes", "facturar", "cobrar"]]
        st.line_chart(g.rename(columns={"pendientes": "Total", "facturar": "Para facturar", "cobrar": "Para cobrar"}),
                      color=["#8C2853", "#C9895B", "#4F7CAC"])


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
                   "menos, registralo después en su ficha (cobro de una parte).")
        if st.button("Registrar cobro del lote", type="primary", key=f"{key}_pc_ok_{info['id']}", use_container_width=True):
            cambios = pr.cambios_cobro_lote(d, info["id"], fecha)
            if not cambios:
                st.info("No queda nada pendiente en este lote.")
                return
            guardar(cf.aplicar(ctx["raw"], ctx["cols"], cambios, ctx["hoy"]))
            _ok(f"Lote {info['obra_social']} cobrado")

    # Para presentar
    st.markdown(ff._CSS, unsafe_allow_html=True)
    t = pr.por_obra_social(d, req, empresa)
    st.markdown('<div class="cf-tit">Para presentar, por obra social</div>'
                '<div class="fp-sub">Armá una presentación por obra social: elegís las prestaciones, el N° y la fecha una '
                'sola vez y quedan todas facturadas.</div>', unsafe_allow_html=True)
    if t.empty:
        st.markdown('<div class="fp-ok">✅ No queda nada sin facturar.</div>', unsafe_allow_html=True)
    for _, r in t.iterrows():
        tono = "rojo" if r["Plazo"] is not None and pd.notna(r["Plazo"]) and r["Plazo"] < 0 else (
            "ambar" if r["Plazo"] is not None and pd.notna(r["Plazo"]) and r["Plazo"] <= cf.AVISO_PRESENTACION else "gris")
        fondo, color = ff.TONOS[tono]
        listas = (f"{int(r['Listas'])} con la documentación completa" if r["Listas"] < r["Prestaciones"]
                  else "todas con la documentación completa")
        with st.container(border=True):
            c1, c2 = st.columns([4, 1.5])
            c1.markdown(f'<div class="ff-top"><div><div class="ff-nom">{_esc(r["Obra social"])}</div>'
                        f'<div class="ff-sub">{int(r["Prestaciones"])} prestación(es) · {listas} · del '
                        f'{pr.fecha_corta(r["Desde"])} al {pr.fecha_corta(r["Hasta"])}</div></div>'
                        f'<div><div class="ff-imp">{pesos(r["Importe"], 0)}</div><div style="text-align:right">'
                        f'<span class="ff-bd" style="background:{fondo};color:{color}">{_esc(pr.plazo_txt(r["Plazo"]))}'
                        f'</span></div></div></div>', unsafe_allow_html=True)
            if c2.button("Armar presentación", key=f"{key}_pr_{r['Obra social']}", type="primary",
                         use_container_width=True, icon=":material/inventory_2:"):
                st.dialog(f"Presentación · {r['Obra social']}", width="large")(armar_dialogo)(r["Obra social"])

    # Ya presentadas
    hechas = pr.presentaciones(pres, d, empresa)
    if len(hechas):
        st.markdown('<div class="cf-tit">Presentaciones hechas</div>', unsafe_allow_html=True)
        ver = st.pills("Ver", ["Por cobrar", "Todas"], default="Por cobrar", key=f"{key}_pr_ver",
                       label_visibility="collapsed") or "Por cobrar"
        if ver == "Por cobrar":
            hechas = hechas[hechas["pendiente"] > 0.5]
        for _, h in hechas.head(30).iterrows():
            tono = "verde" if h["estado"] == "Cobrada" else ("rojo" if h["vencido"] else "azul")
            fondo, color = ff.TONOS[tono]
            with st.container(border=True):
                st.markdown(f'<div class="ff-top"><div><div class="ff-nom">{_esc(h["obra_social"])} · {_esc(h["periodo"])}</div>'
                            f'<div class="ff-sub">Presentada el {_esc(h["fecha"])} · Fact. {_esc(h["numero"])} · vence '
                            f'{_esc(h["vencimiento"]) or "—"} · {int(h["fichas"])} prestación(es)</div></div>'
                            f'<div><div class="ff-imp">{pesos(h["facturado"], 0)}</div><div style="text-align:right">'
                            f'<span class="ff-bd" style="background:{fondo};color:{color}">{_esc(h["estado"])}</span>'
                            f'</div></div></div>', unsafe_allow_html=True)
                st.progress(min(h["cobrado"] / h["facturado"], 1.0) if h["facturado"] else 0.0,
                            text=f"Cobrado {pesos(h['cobrado'], 0)} · falta {pesos(h['pendiente'], 0)}")
                c1, c2, c3 = st.columns([2.6, 1.3, 1.5])
                c2.download_button("Detalle", data=pr.excel_lote(d, h.to_dict()), key=f"{key}_pr_x_{h['id']}",
                                   file_name=f"presentacion_{h['obra_social']}_{h['fecha'].replace('/', '-')}.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   icon=":material/download:", use_container_width=True)
                if h["pendiente"] > 0.5 and c3.button("Registrar cobro", key=f"{key}_pr_c_{h['id']}", type="primary",
                                                      use_container_width=True, icon=":material/payments:"):
                    st.dialog(f"Cobro del lote · {h['obra_social']}")(cobro_dialogo)(h.to_dict())

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


def _esc(t: Any) -> str:
    return html.escape(str(t if t is not None else ""))


def _txt(v: Any) -> str:
    return str(v or "").strip()
