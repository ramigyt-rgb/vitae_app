"""Facturación VMR / VM con el estilo de Deudas Impositivas: planillas por bloque, sin tarjetas.

Cada bloque es una obra social (o «Todas»): barra de título con el resumen, encabezado de columnas,
un renglón por paciente y la fila TOTAL. Las facturas conjuntas (varios pacientes con el mismo N°)
quedan agrupadas: el número aparece una sola vez, en el primer renglón, como los F931 por período.

Para actuar hay una sola barra arriba de la planilla: se elige el paciente o la factura (el selector
busca al escribir) y aparecen los botones de lo que corresponde (Facturar, Registrar cobro, Débito,
Editar, Documentación; para una factura conjunta: Registrar cobro y Datos de la factura). Los
formularios son los mismos de siempre (``facturacion_fichas.dialogo`` y ``dialogo_factura``).
"""
from __future__ import annotations

import html
from datetime import date
from typing import Any, Callable

import pandas as pd

import vitae.modulos.control_facturacion as cf
import vitae.modulos.facturacion_fichas as ff
from vitae.modulos.pagos_pendientes import _texto, pesos

VISTAS = ["Por obra social", "Todo junto"]
MAX_FILAS = 300

COLOR = {"rojo": "#B14E5E", "ambar": "#9A6200", "verde": "#2F7A4A", "azul": "#3D5F8C", "gris": "#8A7F85"}

CSS = """<style>
.fz-bloque{border:1px solid #E8E3E6;border-radius:12px;overflow:hidden;background:#fff;margin-bottom:1rem}
.fz-tit{display:flex;justify-content:space-between;align-items:center;gap:.6rem;padding:.6rem .9rem;background:#F7EFF3;border-bottom:1px solid #EADDE4}
.fz-tit b{font-size:.8rem;letter-spacing:.05em;text-transform:uppercase;color:#5E1A39}
.fz-tit span{font-size:.8rem;color:#6E6268;font-variant-numeric:tabular-nums;text-align:right}
.fz-cols,.fz-row{display:grid;gap:.5rem;align-items:center;padding:.4rem .9rem}
.fz-cols{font-size:.68rem;font-weight:600;color:#8A7F85;text-transform:uppercase;letter-spacing:.04em;padding-top:.45rem;padding-bottom:.25rem}
.fz-row{border-top:1px solid #F1ECEF;font-size:.86rem}
.fz-g{font-weight:650;font-size:.76rem;color:#5E1A39;letter-spacing:.02em;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.fz-g small{display:block;font-weight:500;color:#8A7F85;font-size:.7rem;letter-spacing:0}
.fz-n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.fz-c{text-align:center;font-variant-numeric:tabular-nums;color:#5D5258}
.fz-p{min-width:0;overflow-wrap:anywhere;color:#1F1A1D}
.fz-p small{display:block;color:#8A7F85;font-size:.74rem}
.fz-e{font-size:.78rem;font-weight:600;line-height:1.2}
.fz-dt{font-weight:650}
.fz-venc{color:#B14E5E}
.fz-ok{opacity:.55}
.fz-tot{background:#EFE7EB;font-weight:700;border-top:1px solid #DCCFD6}
.fz-grp{border-top:2px solid #E6DCE1}
.fz-meta{display:none}
.fz-vacio{background:#EEF8F0;border:1px solid #CFE8D5;border-radius:12px;padding:.7rem 1rem;color:#1F5130;font-weight:600;margin:.3rem 0 .6rem}
.fz-ayuda{font-size:.8rem;color:#8A7F85;margin:-.2rem 0 .5rem}
@media (max-width: 760px){
 .fz-cols{display:none}
 .fz-row{grid-template-columns:minmax(0,1fr) auto !important;gap:.1rem .6rem;padding:.5rem .7rem}
 .fz-row .fz-g,.fz-row .fz-c,.fz-row .fz-x{display:none}
 .fz-meta{display:block;grid-column:1 / -1;font-size:.74rem;color:#8A7F85}
 .fz-tit{flex-direction:column;align-items:flex-start;gap:.1rem}
}
</style>"""

# Factura/Obra social · Paciente · Prestación · Vence · Importe · Saldo · Estado
_GRID = "128px minmax(0,1fr) 84px 84px 108px 108px 150px"


def _e(t: Any) -> str:
    return html.escape(str(t if t is not None else ""))


def _f(v: Any) -> str:
    return ff._f(v)


def _estado(r: pd.Series, hoy: date, forzar_incompleta: bool, nota: str) -> tuple[str, str]:
    if forzar_incompleta and ff.incompleta(r):
        falta = [t for t, ok in (("obra social", r["_obra_social"] == "SIN OBRA SOCIAL"),
                                 ("procedimiento", r["_procedimiento"] == "SIN PROCEDIMIENTO"),
                                 ("médico", r["_medico"] == "SIN MÉDICO"), ("importe", r["_monto"] <= 0)) if ok]
        return "falta " + ", ".join(falta), "ambar"
    texto, tono = ff.aviso(r, hoy)
    return texto, tono


def _saldo(r: pd.Series) -> float | None:
    if r["_cancelado"] or not r["_facturada"]:
        return None
    return float(r["_pendiente"])


def _fila(r: pd.Series, *, grupo: str, sub: str, primera: bool, hoy: date, columna_os: bool,
          forzar_incompleta: bool, nota: str) -> str:
    texto, tono = _estado(r, hoy, forzar_incompleta, nota)
    cobrada = ff.etapa(r) in ("cobrado", "anulado") and not r["_debito_abierto"]
    detalle = " · ".join(x for x in (
        "" if r["_procedimiento"] == "SIN PROCEDIMIENTO" else r["_procedimiento"],
        "" if r["_medico"] == "SIN MÉDICO" else r["_medico"],
        f"Fact. {r['_numero_factura']}" if columna_os and _texto(r["_numero_factura"]) else "") if x)
    if nota:
        detalle = " · ".join(x for x in (detalle, nota) if x)
    v = r.get("_vencimiento")
    venc = _f(v) if r["_facturada"] else ""
    es_venc = bool(r["_vencido"]) and not cobrada
    if not r["_facturada"] and not r["_cancelado"]:
        dias = r.get("_dias_plazo")
        if dias is not None and pd.notna(dias):
            limite = pd.Timestamp(hoy) + pd.Timedelta(days=int(dias))
            venc = _f(limite)
            es_venc = int(dias) < 0
    saldo = _saldo(r)
    meta = " · ".join(x for x in (r["_obra_social"] if columna_os else (f"Fact. {grupo}" if grupo and primera else ""),
                                   _f(r.get("_prestacion")), texto) if x)
    return (f'<div class="fz-row{" fz-grp" if primera else ""}{" fz-ok" if cobrada else ""}" '
            f'style="grid-template-columns:{_GRID}">'
            f'<div class="fz-g">{(_e(grupo) + (f"<small>{_e(sub)}</small>" if sub else "")) if primera else ""}</div>'
            f'<div class="fz-p">{_e(r["_paciente"] or "Sin nombre")}'
            f'{f"<small>{_e(detalle)}</small>" if detalle else ""}</div>'
            f'<div class="fz-c">{_e(_f(r.get("_prestacion")))}</div>'
            f'<div class="fz-c{" fz-venc" if es_venc else ""}">{_e(venc)}</div>'
            f'<div class="fz-n fz-dt">{pesos(r["_monto"]) if r["_monto"] else "—"}</div>'
            f'<div class="fz-n fz-x">{pesos(saldo) if saldo is not None else ""}</div>'
            f'<div class="fz-e fz-x" style="color:{COLOR[tono]}">{_e(texto)}</div>'
            f'<div class="fz-meta">{_e(meta)}</div></div>')


def _encabezado(primera: str, vence: str) -> str:
    return (f'<div class="fz-cols" style="grid-template-columns:{_GRID}"><div>{_e(primera)}</div><div>Paciente</div>'
            f'<div style="text-align:center">Prestación</div><div style="text-align:center">{_e(vence)}</div>'
            '<div style="text-align:right">Importe</div><div style="text-align:right">Saldo</div><div>Estado</div></div>')


def _total(x: pd.DataFrame) -> str:
    vivos = x[~x["_cancelado"]]
    imp = float(vivos["_monto"].sum())
    sal = float(vivos.loc[vivos["_facturada"], "_pendiente"].sum())
    return (f'<div class="fz-row fz-tot" style="grid-template-columns:{_GRID}"><div class="fz-g">TOTAL</div><div></div>'
            f'<div></div><div></div><div class="fz-n">{pesos(imp)}</div><div class="fz-n fz-x">{pesos(sal)}</div><div></div>'
            f'<div class="fz-meta">Importe {pesos(imp)} · saldo {pesos(sal)}</div></div>')


def resumen_txt(x: pd.DataFrame) -> str:
    vivos = x[~x["_cancelado"]]
    partes = [f"{len(vivos)} paciente(s)"]
    sin = int((~vivos["_facturada"]).sum())
    if sin:
        partes.append(f"{sin} sin facturar")
    pend = float(vivos.loc[vivos["_facturada"], "_pendiente"].sum())
    if pend > 0.5:
        partes.append(f"por cobrar {pesos(pend, 0)}")
    ven = int(vivos["_vencido"].sum())
    if ven:
        partes.append(f"{ven} vencida(s)")
    deb = float(vivos.loc[vivos["_debito_abierto"], "_debito"].sum())
    if deb > 0.5:
        partes.append(f"débitos {pesos(deb, 0)}")
    return " · ".join(partes)


def html_obra_social(x: pd.DataFrame, titulo: str, hoy: date, *, forzar_incompleta: bool = False,
                     notas: dict[Any, str] | None = None) -> str:
    """Un bloque: las filas agrupadas por factura (las conjuntas juntas, el N° una sola vez)."""
    if x.empty:
        return ""
    sin_factura = bool((~x["_facturada"]).all())
    partes = [f'<div class="fz-bloque"><div class="fz-tit"><b>{_e(titulo)}</b><span>{_e(resumen_txt(x))}</span></div>',
              _encabezado("Factura", "Plazo" if sin_factura else "Vence")]
    for numero, filas in ff.grupos_factura(ff.ordenar(x, "Más urgentes")):
        g = x.loc[filas]
        if numero:
            sub = f"conjunta · {len(filas)}" if len(filas) > 1 else ""
            for k, i in enumerate(filas):
                partes.append(_fila(x.loc[i], grupo=numero, sub=sub, primera=k == 0, hoy=hoy, columna_os=False,
                                    forzar_incompleta=forzar_incompleta, nota=(notas or {}).get(i, "")))
        else:
            for k, i in enumerate(filas):
                partes.append(_fila(x.loc[i], grupo="SIN FACTURA", sub=f"{len(g)}" if len(g) > 1 else "", primera=k == 0,
                                    hoy=hoy, columna_os=False, forzar_incompleta=forzar_incompleta,
                                    nota=(notas or {}).get(i, "")))
    partes.append(_total(x))
    partes.append("</div>")
    return "".join(partes)


def html_todo_junto(x: pd.DataFrame, titulo: str, hoy: date, *, forzar_incompleta: bool = False,
                    notas: dict[Any, str] | None = None) -> str:
    """Un solo bloque, de lo más urgente a lo menos, con la obra social en la primera columna."""
    if x.empty:
        return ""
    sin_factura = bool((~x["_facturada"]).all())
    partes = [f'<div class="fz-bloque"><div class="fz-tit"><b>{_e(titulo)}</b><span>{_e(resumen_txt(x))}</span></div>',
              _encabezado("Obra social", "Plazo" if sin_factura else "Vence")]
    for i in ff.ordenar(x, "Más urgentes").index:
        r = x.loc[i]
        partes.append(_fila(r, grupo=r["_obra_social"], sub="", primera=True, hoy=hoy, columna_os=True,
                            forzar_incompleta=forzar_incompleta, nota=(notas or {}).get(i, "")))
    partes.append(_total(x))
    partes.append("</div>")
    return "".join(partes)


def html_planilla(x: pd.DataFrame, hoy: date, vista: str = "Por obra social", *, titulo: str = "Todas",
                  forzar_incompleta: bool = False, notas: dict[Any, str] | None = None) -> str:
    if x.empty:
        return ""
    if vista == "Todo junto":
        return CSS + html_todo_junto(x, titulo, hoy, forzar_incompleta=forzar_incompleta, notas=notas)
    ro = ff.resumen_obras(x)
    return CSS + "".join(html_obra_social(x[x["_obra_social"] == os_], os_, hoy, forzar_incompleta=forzar_incompleta,
                                          notas=notas) for os_ in ro["Obra social"])


# ---------------------------------------------------------------------------
# Barra de acción: elegir paciente o factura y actuar
# ---------------------------------------------------------------------------
def opciones_accion(x: pd.DataFrame) -> list[str]:
    """Las facturas conjuntas primero (``F|número``) y cada paciente (``R|fila``), en orden de urgencia."""
    if x.empty:
        return []
    out: list[str] = []
    for numero, filas in ff.grupos_factura(ff.ordenar(x, "Más urgentes")):
        if numero and len(filas) > 1:
            out.append(f"F|{numero}")
    out += [f"R|{int(i)}" for i in ff.ordenar(x, "Más urgentes").index]
    return out


def etiqueta_accion(op: str, x: pd.DataFrame) -> str:
    tipo, val = op.split("|", 1)
    if tipo == "F":
        g = x[x["_numero_factura"].astype(str).str.strip() == val]
        return (f"Factura {val} · {g['_obra_social'].iloc[0] if len(g) else ''} · {len(g)} pacientes · "
                f"{pesos(float(g['_monto'].sum()), 0)}")
    i = int(val)
    if i not in x.index:
        return val
    r = x.loc[i]
    return " · ".join(t for t in (r["_paciente"] or "Sin nombre", r["_obra_social"],
                                  "" if r["_procedimiento"] == "SIN PROCEDIMIENTO" else r["_procedimiento"],
                                  _f(r.get("_prestacion")), pesos(r["_monto"], 0) if r["_monto"] else "",
                                  f"Fact. {r['_numero_factura']}" if _texto(r["_numero_factura"]) else "") if t)


def _barra(st: Any, ctx: dict[str, Any], x: pd.DataFrame, *, k: str, key: str, guardar: Callable[[pd.DataFrame], Any],
           forzar_incompleta: bool) -> None:
    ops = opciones_accion(x)
    sel_key = f"{k}_sel"
    if st.session_state.get(sel_key) not in ops:
        st.session_state.pop(sel_key, None)
    with st.container(border=True):
        c = st.columns([3.4, 1.25, 1.25, 1.25], vertical_alignment="bottom")
        op = c[0].selectbox("Para actuar, elegí un paciente o una factura", ops, index=None, key=sel_key,
                            format_func=lambda o: etiqueta_accion(o, x),
                            placeholder="Escribí el paciente, la obra social o el N° de factura…")
        if not op:
            c[3].button("Elegí arriba", key=f"{k}_nada", disabled=True, use_container_width=True)
            return
        tipo, val = op.split("|", 1)
        if tipo == "F":
            filas = [int(i) for i in ctx["d"].index[ctx["d"]["_numero_factura"].astype(str).str.strip() == val]]
            g = ctx["d"].loc[filas]
            if float(g["_pendiente"].sum()) > 0.5 and c[3].button("Registrar cobro", key=f"{k}_fc", type="primary",
                                                                  use_container_width=True, icon=":material/payments:"):
                ff.dialogo_factura(st, ctx, filas, "cobro", guardar, key)
            if c[2].button("Datos de la factura", key=f"{k}_fd", use_container_width=True, icon=":material/receipt_long:"):
                ff.dialogo_factura(st, ctx, filas, "datos", guardar, key)
            return
        i = int(val)
        r = ctx["d"].loc[i]
        inc = forzar_incompleta and ff.incompleta(r)
        acc, texto = ff.accion(r, inc)
        if acc != "editar":
            if c[3].button(texto, key=f"{k}_a", type="primary", use_container_width=True,
                           icon={"facturar": ":material/receipt_long:", "cobro": ":material/payments:",
                                 "debito": ":material/report:"}.get(acc)):
                ff.dialogo(st, acc, i, ctx, guardar, key)
            if c[2].button("Editar", key=f"{k}_e", use_container_width=True, icon=":material/edit:"):
                ff.dialogo(st, "editar", i, ctx, guardar, key)
        elif c[3].button(texto, key=f"{k}_a", type="primary" if inc else "secondary", use_container_width=True,
                         icon=":material/edit:"):
            ff.dialogo(st, "editar", i, ctx, guardar, key)
        if "requisitos" in ctx and ff.etapa(r) == "cargado" and not inc:
            if c[1].button("Documentación", key=f"{k}_d", use_container_width=True, icon=":material/fact_check:"):
                ff.dialogo(st, "documentos", i, ctx, guardar, key)


def notas_documentacion(ctx: dict[str, Any], x: pd.DataFrame, notas: dict[Any, str] | None) -> dict[Any, str]:
    """Las notas de cada fila más, en lo que falta facturar, si la documentación está completa o qué falta."""
    out = dict(notas or {})
    if "requisitos" not in ctx or x.empty:
        return out
    from vitae.modulos import presentaciones as pr

    for i in x.index:
        r = x.loc[i]
        if ff.etapa(r) == "cargado":
            nd = pr.nota_docs(r, ctx.get("requisitos"), ctx["empresa"])
            if nd:
                out[i] = " · ".join(t for t in (out.get(i, ""), nd) if t)
    return out


def render_lista(st: Any, ctx: dict[str, Any], x: pd.DataFrame, *, guardar: Callable[[pd.DataFrame], Any], key: str,
                 lista: str, vacio: str = "No hay nada acá.", notas: dict[Any, str] | None = None,
                 forzar_incompleta: bool = False, vista: str = "Por obra social", titulo: str = "Todas") -> None:
    """Filtros, barra de acción y la planilla."""
    st.markdown(CSS + ff._CSS, unsafe_allow_html=True)
    k = f"{key}_fz_{lista}"
    pend = st.session_state.pop(f"{key}_abrir", None)
    if pend:
        acc, fila = pend
        if acc == "nuevo":
            ff.dialogo_nuevo(st, ctx, guardar, key)
        else:
            ff.dialogo(st, acc, fila, ctx, guardar, key)
    if x is None or x.empty:
        st.markdown(f'<div class="fz-vacio">✅ {html.escape(vacio)}</div>', unsafe_allow_html=True)
        return
    c1, c2, c3 = st.columns([1.35, 1.6, 2.2], vertical_alignment="bottom")
    v = c1.segmented_control("Ver", VISTAS, default=vista if vista in VISTAS else VISTAS[0], key=f"{k}_vista",
                             label_visibility="collapsed") or VISTAS[0]
    obras = list(ff.resumen_obras(x)["Obra social"])
    elegidas = c2.multiselect("Obra social", obras, key=f"{k}_os", placeholder="Todas las obras sociales",
                              label_visibility="collapsed") if len(obras) > 1 else []
    t = c3.text_input("Buscar", key=f"{k}_q", label_visibility="collapsed",
                      placeholder="Buscar paciente, procedimiento o N° de factura")
    if elegidas:
        x = x[x["_obra_social"].isin(elegidas)]
    x = ff.buscar(x, t)
    if x.empty:
        st.info("Nada coincide con los filtros.")
        return
    notas = notas_documentacion(ctx, x, notas)
    filas = [int(i) for i in ff.ordenar(x, "Más urgentes").index]
    st.session_state[f"{key}_orden"] = filas
    for acc in ("facturar", "cobro", "debito", "editar"):
        st.session_state[f"{key}_orden_{acc}"] = [
            i for i in filas if ff.accion(ctx["d"].loc[i], forzar_incompleta and ff.incompleta(ctx["d"].loc[i]))[0] == acc]
    _barra(st, ctx, x, k=k, key=key, guardar=guardar, forzar_incompleta=forzar_incompleta)
    mostrar = x
    if len(x) > MAX_FILAS:
        mostrar = x.loc[filas[:MAX_FILAS]]
        st.caption(f"Se muestran los {MAX_FILAS} más urgentes de {len(x)}. Usá los filtros para ver el resto.")
    st.markdown(html_planilla(mostrar, ctx["hoy"], v, titulo=titulo, forzar_incompleta=forzar_incompleta, notas=notas),
                unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Cirugías de la Agenda sin cargar
# ---------------------------------------------------------------------------
_GRID_AG = "minmax(0,1.2fr) minmax(0,1fr) minmax(0,1fr) minmax(0,1fr) 90px"


def html_agenda(ag: pd.DataFrame, empresa: str) -> str:
    partes = [CSS, f'<div class="fz-bloque"><div class="fz-tit"><b>Cirugías realizadas sin cargar</b>'
              f'<span>{len(ag)} · sólo procedimientos de {_e(empresa)}</span></div>'
              f'<div class="fz-cols" style="grid-template-columns:{_GRID_AG}"><div>Paciente</div><div>Obra social</div>'
              '<div>Procedimiento</div><div>Médico</div><div style="text-align:center">Fecha</div></div>']
    for _, r in ag.iterrows():
        partes.append(f'<div class="fz-row" style="grid-template-columns:{_GRID_AG}">'
                      f'<div class="fz-p">{_e(_texto(r["Paciente"]))}</div><div class="fz-p fz-x">{_e(_texto(r["Obra social"]))}</div>'
                      f'<div class="fz-p fz-x">{_e(_texto(r["Procedimiento"]))}</div><div class="fz-p fz-x">{_e(_texto(r["Médico"]))}</div>'
                      f'<div class="fz-c">{_e(cf._fecha_txt(r["Fecha"]))}</div>'
                      f'<div class="fz-meta">{_e(" · ".join(_texto(v) for v in (r["Obra social"], r["Procedimiento"], r["Médico"]) if _texto(v)))}'
                      '</div></div>')
    partes.append("</div>")
    return "".join(partes)


def render_agenda(st: Any, ctx: dict[str, Any], *, guardar: Callable[[pd.DataFrame], Any], key: str) -> None:
    ag = ctx["c"]["agenda"]
    if ag.empty:
        return
    ag = ag.reset_index(drop=True)
    with st.container(border=True):
        c1, c2, c3 = st.columns([3.4, 1.25, 1.6], vertical_alignment="bottom")
        i = c1.selectbox("Agregar una cirugía", list(ag.index), index=None, key=f"{key}_fz_ag_sel",
                         format_func=lambda j: " · ".join(_texto(v) for v in (ag.loc[j, "Paciente"], ag.loc[j, "Obra social"],
                                                                              ag.loc[j, "Procedimiento"],
                                                                              cf._fecha_txt(ag.loc[j, "Fecha"])) if _texto(v)),
                         placeholder="Elegí la cirugía para revisar el importe y agregarla…")
        if c2.button("Agregar", key=f"{key}_fz_ag_add", type="primary", disabled=i is None, use_container_width=True,
                     icon=":material/add:"):
            ff.dialogo_nuevo(st, ctx, guardar, key, ag.loc[i].to_dict())
        if len(ag) > 1 and c3.button(f"Agregar las {len(ag)}", key=f"{key}_ag_todas", use_container_width=True,
                                     icon=":material/playlist_add:",
                                     help="Con el importe sugerido. Después se revisan en 2 · Facturar."):
            ff.agregar_todas(st, ctx, guardar, key)
    st.markdown(html_agenda(ag, ctx["empresa"]), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Presentaciones (facturar por obra social)
# ---------------------------------------------------------------------------
_GRID_PR = "minmax(0,1.3fr) 90px minmax(0,1.2fr) 150px 120px 150px"
_GRID_HE = "minmax(0,1.4fr) 92px 128px 92px 112px 112px 140px"


def html_para_presentar(t: pd.DataFrame) -> str:
    from vitae.modulos import presentaciones as pr

    partes = [CSS, f'<div class="fz-bloque"><div class="fz-tit"><b>Para presentar, por obra social</b>'
              f'<span>{int(t["Prestaciones"].sum())} prestación(es) · {pesos(float(t["Importe"].sum()), 0)}</span></div>'
              f'<div class="fz-cols" style="grid-template-columns:{_GRID_PR}"><div>Obra social</div>'
              '<div style="text-align:center">Prestaciones</div><div>Documentación</div><div style="text-align:center">'
              'Prestaciones del</div><div style="text-align:right">Importe</div><div>Plazo</div></div>']
    for _, r in t.iterrows():
        plazo = r["Plazo"]
        tono = "rojo" if plazo is not None and pd.notna(plazo) and plazo < 0 else (
            "ambar" if plazo is not None and pd.notna(plazo) and plazo <= cf.AVISO_PRESENTACION else "gris")
        docs = "completa" if r["Listas"] >= r["Prestaciones"] else f"{int(r['Listas'])} de {int(r['Prestaciones'])} completas"
        rango = pr.fecha_corta(r["Desde"]) + ("" if r["Desde"] == r["Hasta"] else f" al {pr.fecha_corta(r['Hasta'])}")
        partes.append(f'<div class="fz-row" style="grid-template-columns:{_GRID_PR}">'
                      f'<div class="fz-g" style="font-size:.84rem">{_e(r["Obra social"])}</div>'
                      f'<div class="fz-c">{int(r["Prestaciones"])}</div>'
                      f'<div class="fz-p fz-x" style="color:{COLOR["verde"] if docs == "completa" else COLOR["ambar"]}">{_e(docs)}</div>'
                      f'<div class="fz-c fz-x">{_e(rango)}</div><div class="fz-n fz-dt">{pesos(r["Importe"])}</div>'
                      f'<div class="fz-e fz-x" style="color:{COLOR[tono]}">{_e(pr.plazo_txt(plazo))}</div>'
                      f'<div class="fz-meta">{int(r["Prestaciones"])} prestación(es) · {_e(docs)} · {_e(pr.plazo_txt(plazo))}</div></div>')
    partes.append(f'<div class="fz-row fz-tot" style="grid-template-columns:{_GRID_PR}"><div class="fz-g">TOTAL</div>'
                  f'<div class="fz-c">{int(t["Prestaciones"].sum())}</div><div></div><div></div>'
                  f'<div class="fz-n">{pesos(float(t["Importe"].sum()))}</div><div></div></div></div>')
    return "".join(partes)


def html_presentadas(h: pd.DataFrame) -> str:
    partes = [CSS, f'<div class="fz-bloque"><div class="fz-tit"><b>Presentaciones hechas</b>'
              f'<span>{len(h)} · falta cobrar {pesos(float(h["pendiente"].sum()), 0)}</span></div>'
              f'<div class="fz-cols" style="grid-template-columns:{_GRID_HE}"><div>Obra social · período</div>'
              '<div style="text-align:center">Presentada</div><div>Factura</div><div style="text-align:center">Vence</div>'
              '<div style="text-align:right">Facturado</div><div style="text-align:right">Saldo</div><div>Estado</div></div>']
    for _, r in h.iterrows():
        tono = "verde" if r["estado"] == "Cobrada" else ("rojo" if r["vencido"] else "azul")
        partes.append(f'<div class="fz-row{" fz-ok" if r["estado"] == "Cobrada" else ""}" style="grid-template-columns:{_GRID_HE}">'
                      f'<div class="fz-p">{_e(r["obra_social"])}<small>{_e(r["periodo"])} · {int(r["fichas"])} prestación(es)</small></div>'
                      f'<div class="fz-c fz-x">{_e(r["fecha"])}</div><div class="fz-g fz-x">{_e(r["numero"])}</div>'
                      f'<div class="fz-c{" fz-venc" if r["vencido"] and r["estado"] != "Cobrada" else ""}">{_e(r["vencimiento"]) or "—"}</div>'
                      f'<div class="fz-n fz-dt">{pesos(r["facturado"])}</div><div class="fz-n fz-x">{pesos(r["pendiente"])}</div>'
                      f'<div class="fz-e fz-x" style="color:{COLOR[tono]}">{_e(r["estado"])}</div>'
                      f'<div class="fz-meta">Fact. {_e(r["numero"])} · saldo {pesos(r["pendiente"])} · {_e(r["estado"])}</div></div>')
    partes.append(f'<div class="fz-row fz-tot" style="grid-template-columns:{_GRID_HE}"><div class="fz-g">TOTAL</div><div></div>'
                  f'<div></div><div></div><div class="fz-n">{pesos(float(h["facturado"].sum()))}</div>'
                  f'<div class="fz-n fz-x">{pesos(float(h["pendiente"].sum()))}</div><div></div></div></div>')
    return "".join(partes)
