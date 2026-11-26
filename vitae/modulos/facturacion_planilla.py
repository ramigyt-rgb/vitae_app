"""Facturación VMR / VM con el estilo de Deudas Impositivas: planillas por bloque, sin tarjetas.

Cada bloque es una obra social (o «Todas»): barra de título con el resumen, encabezado de columnas,
un renglón por paciente y la fila TOTAL. Regla: lo pendiente de pago (sin cobrar, aunque sea viejo)
siempre va arriba; después lo cobrado y lo anulado (``facturacion_fichas.pendiente_pago``). Las facturas conjuntas (varios pacientes con el mismo N°)
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

VISTAS = ["Por obra social", "Por paciente"]
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
                                 ("importe", r["_monto"] <= 0 and not r["_sin_cargo"] and not r["_incluida"])) if ok]
        return "falta " + ", ".join(falta), "ambar"
    texto, tono = ff.aviso(r, hoy)
    if not r["_facturada"] and not r["_cancelado"] and not r["_sin_cargo"] and r["_monto"] <= 0.004:
        texto += " · falta importe"
        tono = "rojo" if tono == "rojo" else "ambar"
    return texto, tono


def pendiente_fila(r: pd.Series) -> float:
    """Lo que falta cobrar de la fila: sin facturar, todo el importe; facturada, su saldo."""
    if r["_cancelado"] or r["_sin_cargo"]:
        return 0.0
    return float(r["_monto"]) if not r["_facturada"] else float(r["_pendiente"])


def pendiente_total(x: pd.DataFrame) -> float:
    vivos = x[~x["_cancelado"] & ~x["_sin_cargo"]]
    return float(vivos.loc[~vivos["_facturada"], "_monto"].sum() + vivos.loc[vivos["_facturada"], "_pendiente"].sum())


def _saldo(r: pd.Series) -> float | None:
    if r["_cancelado"] or r["_sin_cargo"] or r.get("_incluida", False):
        return None
    return pendiente_fila(r)


def _fila(r: pd.Series, *, grupo: str, sub: str, primera: bool, hoy: date, columna_os: bool,
          forzar_incompleta: bool, nota: str) -> str:
    texto, tono = _estado(r, hoy, forzar_incompleta, nota)
    cobrada = ff.etapa(r) in ("cobrado", "anulado", "sin_cargo", "incluida") and not r["_debito_abierto"]
    detalle = " · ".join(x for x in (
        "" if r["_procedimiento"] == "SIN PROCEDIMIENTO" else r["_procedimiento"],
        "" if r["_medico"] == "SIN MÉDICO" else r["_medico"],
        f"Fact. {r['_numero_factura']}" if columna_os and _texto(r["_numero_factura"]) else "") if x)
    if nota:
        detalle = " · ".join(x for x in (detalle, nota) if x)
    v = r.get("_vencimiento")
    venc = _f(v) if r["_facturada"] else ""
    es_venc = bool(r["_vencido"]) and not cobrada
    if not r["_facturada"] and not r["_cancelado"] and not r["_sin_cargo"]:
        dias = r.get("_dias_plazo")
        if dias is not None and pd.notna(dias):
            limite = pd.Timestamp(hoy) + pd.Timedelta(days=int(dias))
            venc = _f(limite)
            es_venc = int(dias) < 0
    saldo = _saldo(r)
    estilo_saldo = ' style="color:#9A6200"' if saldo is not None and not r["_facturada"] else ""
    meta = " · ".join(x for x in (r["_obra_social"] if columna_os else (f"Fact. {grupo}" if grupo and primera else ""),
                                   _f(r.get("_prestacion")), texto) if x)
    return (f'<div class="fz-row{" fz-grp" if primera else ""}{" fz-ok" if cobrada else ""}" '
            f'style="grid-template-columns:{_GRID}">'
            f'<div class="fz-g">{(_e(grupo) + (f"<small>{_e(sub)}</small>" if sub else "")) if primera else ""}</div>'
            f'<div class="fz-p">{_e(r["_paciente"] or "Sin nombre")}'
            f'{f"<small>{_e(detalle)}</small>" if detalle else ""}</div>'
            f'<div class="fz-c">{_e(_f(r.get("_prestacion")))}</div>'
            f'<div class="fz-c{" fz-venc" if es_venc else ""}">{_e(venc)}</div>'
            f'<div class="fz-n fz-dt">{pesos(r["_monto"]) if r["_monto"] else ("en el total" if r["_incluida"] else "—")}</div>'
            f'<div class="fz-n fz-x"{estilo_saldo}>{pesos(saldo) if saldo is not None else ""}</div>'
            f'<div class="fz-e fz-x" style="color:{COLOR[tono]}">{_e(texto)}</div>'
            f'<div class="fz-meta">{_e(meta)}</div></div>')


def _encabezado(primera: str, vence: str) -> str:
    return (f'<div class="fz-cols" style="grid-template-columns:{_GRID}"><div>{_e(primera)}</div><div>Paciente</div>'
            f'<div style="text-align:center">Prestación</div><div style="text-align:center">{_e(vence)}</div>'
            '<div style="text-align:right">Importe</div><div style="text-align:right">Pendiente</div><div>Estado</div></div>')


def _total(x: pd.DataFrame) -> str:
    vivos = x[~x["_cancelado"]]
    imp = float(vivos["_monto"].sum())
    sal = pendiente_total(x)
    return (f'<div class="fz-row fz-tot" style="grid-template-columns:{_GRID}"><div class="fz-g">TOTAL</div><div></div>'
            f'<div></div><div></div><div class="fz-n">{pesos(imp)}</div><div class="fz-n fz-x">{pesos(sal)}</div><div></div>'
            f'<div class="fz-meta">Importe {pesos(imp)} · pendiente {pesos(sal)}</div></div>')


def resumen_txt(x: pd.DataFrame) -> str:
    vivos = x[~x["_cancelado"]]
    partes = [f"{len(vivos)} paciente(s)"]
    sf = vivos[~vivos["_facturada"] & ~vivos["_sin_cargo"] & ~vivos["_incluida"]]
    if len(sf):
        partes.append(f"{len(sf)} sin facturar ({pesos(float(sf['_monto'].sum()), 0)})")
        pv = int((sf["_dias_plazo"] < 0).sum())
        if pv:
            partes.append(f"{pv} con el plazo vencido")
        si = int((sf["_monto"] <= 0.004).sum())
        if si:
            partes.append(f"{si} sin importe")
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
                partes.append(_fila(x.loc[i], grupo="ANULADAS" if bool(g["_cancelado"].all()) else (
                                    "NO SE FACTURA" if bool(g["_sin_cargo"].all()) else "SIN FACTURA"),
                                    sub=f"{len(g)}" if len(g) > 1 else "", primera=k == 0,
                                    hoy=hoy, columna_os=False, forzar_incompleta=forzar_incompleta,
                                    nota=(notas or {}).get(i, "")))
    partes.append(_total(x))
    partes.append("</div>")
    return "".join(partes)


def html_todo_junto(x: pd.DataFrame, titulo: str, hoy: date, *, forzar_incompleta: bool = False,
                    notas: dict[Any, str] | None = None, orden: str = "Más urgentes") -> str:
    """Un solo bloque, de lo más urgente a lo menos, con la obra social en la primera columna."""
    if x.empty:
        return ""
    sin_factura = bool((~x["_facturada"]).all())
    partes = [f'<div class="fz-bloque"><div class="fz-tit"><b>{_e(titulo)}</b><span>{_e(resumen_txt(x))}</span></div>',
              _encabezado("Obra social", "Plazo" if sin_factura else "Vence")]
    for i in ff.ordenar(x, orden).index:
        r = x.loc[i]
        partes.append(_fila(r, grupo=r["_obra_social"], sub="", primera=True, hoy=hoy, columna_os=True,
                            forzar_incompleta=forzar_incompleta, nota=(notas or {}).get(i, "")))
    partes.append(_total(x))
    partes.append("</div>")
    return "".join(partes)


def html_planilla(x: pd.DataFrame, hoy: date, vista: str = "Por obra social", *, titulo: str = "Todas",
                  forzar_incompleta: bool = False, notas: dict[Any, str] | None = None,
                  orden: str = "Más urgentes") -> str:
    if x.empty:
        return ""
    if vista == "Por paciente":
        return CSS + html_todo_junto(x, titulo, hoy, forzar_incompleta=forzar_incompleta, notas=notas, orden=orden)
    ro = ff.resumen_obras(x)
    return CSS + "".join(html_obra_social(x[x["_obra_social"] == os_], os_, hoy, forzar_incompleta=forzar_incompleta,
                                          notas=notas) for os_ in ro["Obra social"])


# ---------------------------------------------------------------------------
# Barra de acción: elegir paciente o factura y actuar
# ---------------------------------------------------------------------------
def opciones_accion(x: pd.DataFrame, orden: str = "Más urgentes") -> list[str]:
    """Las facturas conjuntas primero (``F|número``) y cada paciente (``R|fila``), en el orden pedido."""
    if x.empty:
        return []
    out: list[str] = []
    for numero, filas in ff.grupos_factura(ff.ordenar(x, "Más urgentes")):
        if numero and len(filas) > 1:
            out.append(f"F|{numero}")
    out += [f"R|{int(i)}" for i in ff.ordenar(x, orden).index]
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
           forzar_incompleta: bool, solo_editar: bool = False, orden: str = "Más urgentes") -> None:
    ops = opciones_accion(x, orden)
    edita = bool(ctx.get("puede_editar", True))
    sel_key = f"{k}_sel"
    if st.session_state.get(sel_key) not in ops:
        st.session_state.pop(sel_key, None)
    with st.container(border=True):
        c = st.columns([3.4, 1.25, 1.25, 1.25], vertical_alignment="bottom")
        op = c[0].selectbox("Para editar, elegí un paciente o una factura" if solo_editar else
                            "Para actuar, elegí un paciente o una factura", ops, index=None, key=sel_key,
                            format_func=lambda o: etiqueta_accion(o, x),
                            placeholder="Escribí el paciente, la obra social o el N° de factura…")
        if not op:
            c[3].button("Elegí arriba", key=f"{k}_nada", disabled=True, use_container_width=True)
            return
        tipo, val = op.split("|", 1)
        if tipo == "F":
            filas = [int(i) for i in ctx["d"].index[ctx["d"]["_numero_factura"].astype(str).str.strip() == val]]
            g = ctx["d"].loc[filas]
            if solo_editar:
                if edita and c[3].button("Datos de la factura", key=f"{k}_fd", type="primary", use_container_width=True,
                               icon=":material/receipt_long:"):
                    ff.dialogo_factura(st, ctx, filas, "datos", guardar, key)
                return
            if float(g["_pendiente"].sum()) > 0.5 and c[3].button("Registrar cobro", key=f"{k}_fc", type="primary",
                                                                  use_container_width=True, icon=":material/payments:"):
                ff.dialogo_factura(st, ctx, filas, "cobro", guardar, key)
            if edita and c[2].button("Datos de la factura", key=f"{k}_fd", use_container_width=True,
                                     icon=":material/receipt_long:"):
                ff.dialogo_factura(st, ctx, filas, "datos", guardar, key)
            return
        i = int(val)
        r = ctx["d"].loc[i]
        inc = forzar_incompleta and ff.incompleta(r)
        acc, texto = ("editar", "Editar") if solo_editar else ff.accion(r, inc)
        if acc != "editar":
            if c[3].button(texto, key=f"{k}_a", type="primary", use_container_width=True,
                           icon={"facturar": ":material/receipt_long:", "cobro": ":material/payments:",
                                 "debito": ":material/report:"}.get(acc)):
                ff.dialogo(st, acc, i, ctx, guardar, key)
            if edita and c[2].button("Editar", key=f"{k}_e", use_container_width=True, icon=":material/edit:"):
                ff.dialogo(st, "editar", i, ctx, guardar, key)
        elif edita and c[3].button(texto, key=f"{k}_a", type="primary" if inc or solo_editar else "secondary", use_container_width=True,
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
                 forzar_incompleta: bool = False, vista: str = "Por obra social", titulo: str = "Todas",
                 solo_editar: bool = False, orden: str = "Más urgentes") -> None:
    """Filtros, barra de acción y la planilla. ``solo_editar``: la barra sólo edita (para 1 · Cargar)."""
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
    filas = [int(i) for i in ff.ordenar(x, orden).index]
    st.session_state[f"{key}_orden"] = filas
    for acc in ("facturar", "cobro", "debito", "editar"):
        st.session_state[f"{key}_orden_{acc}"] = [
            i for i in filas if ff.accion(ctx["d"].loc[i], forzar_incompleta and ff.incompleta(ctx["d"].loc[i]))[0] == acc]
    _barra(st, ctx, x, k=k, key=key, guardar=guardar, forzar_incompleta=forzar_incompleta, solo_editar=solo_editar,
           orden=orden)
    mostrar = x
    if len(x) > MAX_FILAS:
        mostrar = x.loc[filas[:MAX_FILAS]]
        st.caption(f"Se muestran {MAX_FILAS} de {len(x)} ({orden.lower()}). Usá los filtros o el buscador para ver el "
                   "resto; en la barra de arriba están todos.")
    st.markdown(html_planilla(mostrar, ctx["hoy"], v, titulo=titulo, forzar_incompleta=forzar_incompleta, notas=notas,
                              orden=orden), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Solicitudes de la Agenda (sólo las de esta empresa, por la regla del procedimiento)
# ---------------------------------------------------------------------------
_GRID_AG = "minmax(0,1.2fr) minmax(0,1fr) minmax(0,1fr) minmax(0,1fr) 90px"


def html_solicitudes(ag: pd.DataFrame, empresa: str) -> str:
    partes = [CSS, f'<div class="fz-bloque"><div class="fz-tit"><b>Solicitudes de la Agenda</b>'
              f'<span>{len(ag)} realizada(s) · procedimientos de {_e(empresa)}</span></div>'
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


def filas_solicitudes(ctx: dict[str, Any], ag: pd.DataFrame) -> pd.DataFrame:
    """Todas las solicitudes → la planilla con sus filas nuevas (importe sugerido, estado Pendiente)."""
    nuevo, d, hoy = ctx["raw"], ctx["d"], ctx["hoy"]
    for _, r in ag.iterrows():
        imp = ff.importe_sugerido(d, _texto(r["Procedimiento"]), _texto(r["Obra social"]), empresa=ctx["empresa"],
                                  fecha=r.get("Fecha"), paciente=r.get("Paciente")) if len(d) else None
        nuevo = cf.nueva_fila(nuevo, ctx["cols"], r.to_dict(), ff._txt_imp(imp), hoy,
                              observaciones="Cargada desde la Agenda de Quirófano")
    return nuevo


def render_solicitudes(st: Any, ctx: dict[str, Any], *, guardar: Callable[[pd.DataFrame], Any],
                       guardar_tabla: Callable[[str, pd.DataFrame], Any] | None, descartadas: pd.DataFrame | None,
                       key: str, usuario: str = "") -> None:
    """Cirugías realizadas en la Agenda que son de esta empresa y todavía no están: agregar o descartar."""
    ag = ctx["c"].get("agenda")
    if ag is None or ag.empty:
        return
    ag = ag.reset_index(drop=True)
    st.markdown('<div class="cf-tit">Solicitudes de la Agenda</div><div class="fp-sub">Realizadas en quirófano con '
                f'procedimientos de {_e(ctx["empresa"])} que todavía no están cargadas. Agregalas (revisás el importe) o '
                'descartalas si ya están o no se facturan.</div>', unsafe_allow_html=True)

    def ok(aviso: str) -> None:
        st.session_state[f"{key}_cf_rev"] = int(st.session_state.get(f"{key}_cf_rev", 0)) + 1
        st.toast(aviso, icon=":material/check_circle:")
        st.rerun()

    with st.container(border=True):
        c = st.columns([3.2, 1.1, 1.1, 1.4], vertical_alignment="bottom")
        i = c[0].selectbox("Solicitud", list(ag.index), index=None, key=f"{key}_fz_sol",
                           format_func=lambda j: " · ".join(_texto(v) for v in (
                               ag.loc[j, "Paciente"], ag.loc[j, "Obra social"], ag.loc[j, "Procedimiento"],
                               cf._fecha_txt(ag.loc[j, "Fecha"])) if _texto(v)),
                           placeholder="Elegí la cirugía para agregarla o descartarla…")
        if c[1].button("Agregar", key=f"{key}_fz_sol_add", type="primary", disabled=i is None,
                       use_container_width=True, icon=":material/add:"):
            ff.dialogo_nuevo(st, ctx, guardar, key, ag.loc[i].to_dict())
        if c[2].button("Descartar", key=f"{key}_fz_sol_desc", disabled=i is None or guardar_tabla is None,
                       use_container_width=True, icon=":material/block:",
                       help="Ya está cargada o no se factura: no vuelve a aparecer."):
            guardar_tabla(cf.TABLA_DESCARTADAS, cf.descartar(descartadas, [ag.loc[i].to_dict()], ctx["empresa"],
                                                             "Descartada", usuario, ctx["hoy"]))
            ok(f"Solicitud de {_texto(ag.loc[i, 'Paciente'])} descartada")
        if len(ag) > 1 and c[3].button(f"Agregar las {len(ag)}", key=f"{key}_fz_sol_todas", use_container_width=True,
                                       icon=":material/playlist_add:", help="Con el importe sugerido."):
            try:
                guardar(filas_solicitudes(ctx, ag))
            except Exception as exc:
                st.error(f"No se pudo guardar: {exc}")
                return
            ok(f"{len(ag)} cirugía(s) agregada(s)")
    st.markdown(html_solicitudes(ag, ctx["empresa"]), unsafe_allow_html=True)


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


# ---------------------------------------------------------------------------
# Editar en tabla (como «Editar» de Deudas Impositivas): cambiar celdas y guardar todo junto
# ---------------------------------------------------------------------------
# (campo de control_facturacion.aplicar, título, tipo)
EDICION = [("paciente", "Paciente", "texto"), ("mes", "Prestación", "fecha"), ("obra_social", "Obra social", "texto"),
           ("procedimiento", "Procedimiento", "texto"), ("medico", "Médico", "texto"), ("monto", "Importe", "num"),
           ("numero_factura", "N° factura", "texto"), ("fecha_factura", "Fecha factura", "fecha"),
           ("vencimiento", "Vencimiento", "fecha"), ("fecha_pago", "Fecha de pago", "fecha"),
           ("estado", "Estado", "texto"), ("debito", "Débito", "num"), ("observaciones", "Observaciones", "texto")]
MAX_EDICION = 300


def _fecha_o_none(v: Any) -> Any:
    return v.date() if isinstance(v, pd.Timestamp) and pd.notna(v) else None


def tabla_edicion(ctx: dict[str, Any], x: pd.DataFrame) -> pd.DataFrame:
    """Una fila por registro (``fila`` = fila de la planilla) con los datos editables tal como están."""
    raw, cols = ctx["raw"], ctx["cols"]
    col_obs = cols.get("observaciones") or "observaciones"
    filas = []
    for i in x.index:
        r = x.loc[i]
        filas.append({
            "fila": int(i), "Paciente": r["_paciente"], "Prestación": _fecha_o_none(r.get("_prestacion")),
            "Obra social": "" if r["_obra_social"] == "SIN OBRA SOCIAL" else r["_obra_social"],
            "Procedimiento": "" if r["_procedimiento"] == "SIN PROCEDIMIENTO" else r["_procedimiento"],
            "Médico": "" if r["_medico"] == "SIN MÉDICO" else r["_medico"],
            "Importe": float(r["_monto"]) if r["_monto"] else None, "N° factura": r["_numero_factura"],
            "Fecha factura": _fecha_o_none(r.get("_fecha_factura")), "Vencimiento": _fecha_o_none(r.get("_vencimiento")),
            "Fecha de pago": _fecha_o_none(r.get("_fecha_pago")), "Estado": _texto(r.get("_estado_original")),
            "Débito": float(r["_debito"]) if r["_debito"] else None,
            "Observaciones": _texto(raw.loc[i].get(col_obs, "")) if i in raw.index else ""})
    return pd.DataFrame(filas, columns=["fila"] + [t for _, t, _ in EDICION])


def _norm_edicion(v: Any, tipo: str) -> str:
    if tipo == "fecha":
        return ff._txt_fecha(v) if v is not None and not (isinstance(v, float) and pd.isna(v)) else ""
    if tipo == "num":
        try:
            return "" if v is None or pd.isna(v) else ff._txt_imp(float(v))
        except (TypeError, ValueError):
            return ""
    return _texto(v)


def cambios_edicion(original: pd.DataFrame, editada: pd.DataFrame) -> list[dict[str, Any]]:
    """Sólo las celdas que cambiaron → cambios para ``control_facturacion.aplicar``."""
    base = original.set_index("fila")
    out: list[dict[str, Any]] = []
    for _, r in editada.iterrows():
        try:
            fila = int(r["fila"])
        except (TypeError, ValueError):
            continue
        if fila not in base.index:
            continue
        for campo, titulo, tipo in EDICION:
            nuevo, antes = _norm_edicion(r[titulo], tipo), _norm_edicion(base.loc[fila, titulo], tipo)
            if nuevo != antes:
                out.append({"fila": fila, "campo": campo, "valor": nuevo.upper() if campo == "paciente" else nuevo})
    return out


def render_edicion(st: Any, ctx: dict[str, Any], x: pd.DataFrame, *, guardar: Callable[[pd.DataFrame], Any],
                   key: str) -> None:
    """Tabla para modificar los registros (los del filtro elegido) y guardar todo junto."""
    k = f"{key}_fz_ed"
    if not ctx.get("puede_editar", True):
        return
    rev = int(st.session_state.get(f"{k}_rev", 0))
    if x is None or x.empty:
        st.markdown('<div class="fz-vacio">✅ No hay registros para editar con estos filtros.</div>', unsafe_allow_html=True)
        return
    t = st.text_input("Buscar", key=f"{k}_q", label_visibility="collapsed",
                      placeholder="Buscar paciente, obra social, procedimiento o N° de factura")
    x = ff.buscar(x, t)
    if x.empty:
        st.info("Nada coincide con la búsqueda.")
        return
    x = ff.ordenar(x, "Más urgentes")
    if len(x) > MAX_EDICION:
        st.caption(f"Se muestran {MAX_EDICION} de {len(x)} (lo pendiente de pago primero). Elegí un mes o buscá para "
                   "ver el resto.")
        x = x.head(MAX_EDICION)
    st.caption(f"{len(x)} registro(s)")
    base = tabla_edicion(ctx, x)
    cc = st.column_config
    obras = ff.opciones(ctx["d"], "_obra_social", "SIN OBRA SOCIAL")
    cfg = {"fila": None,
           "Paciente": cc.TextColumn("Paciente", width="medium"),
           "Prestación": cc.DateColumn("Prestación", format="DD/MM/YYYY"),
           "Obra social": cc.SelectboxColumn("Obra social", options=sorted(set(obras) | set(base["Obra social"]) - {""})),
           "Procedimiento": cc.TextColumn("Procedimiento"),
           "Médico": cc.TextColumn("Médico"),
           "Importe": cc.NumberColumn("Importe", format="localized", min_value=0.0),
           "N° factura": cc.TextColumn("N° factura"),
           "Fecha factura": cc.DateColumn("Fecha factura", format="DD/MM/YYYY"),
           "Vencimiento": cc.DateColumn("Vencimiento", format="DD/MM/YYYY"),
           "Fecha de pago": cc.DateColumn("Fecha de pago", format="DD/MM/YYYY"),
           "Estado": cc.SelectboxColumn("Estado", options=sorted(set(ff.ESTADOS) | set(base["Estado"]) - {""})),
           "Débito": cc.NumberColumn("Débito", format="localized", min_value=0.0),
           "Observaciones": cc.TextColumn("Observaciones", width="medium")}
    with st.form(f"{k}_form_{rev}", border=False):
        editada = st.data_editor(base, hide_index=True, use_container_width=True, num_rows="fixed", column_config=cfg,
                                 height=min(110 + 36 * len(base), 680), key=f"{k}_tabla_{rev}")
        b1, b2 = st.columns(2)
        ok = b1.form_submit_button("Guardar cambios", type="primary", icon=":material/save:", use_container_width=True)
        no = b2.form_submit_button("Descartar cambios", use_container_width=True)
    if no:
        st.session_state[f"{k}_rev"] = rev + 1
        st.rerun()
    if ok:
        cambios = cambios_edicion(base, editada)
        if not cambios:
            st.info("No hay cambios para guardar.")
            return
        try:
            guardar(cf.aplicar(ctx["raw"], ctx["cols"], cambios, ctx["hoy"]))
        except Exception as exc:
            st.error(f"No se pudo guardar: {exc}")
            return
        st.session_state[f"{k}_rev"] = rev + 1
        st.session_state[f"{key}_cf_rev"] = int(st.session_state.get(f"{key}_cf_rev", 0)) + 1
        n = len({c["fila"] for c in cambios})
        st.toast(f"{len(cambios)} cambio(s) guardado(s) en {n} registro(s)", icon=":material/check_circle:")
        st.rerun()


# ---------------------------------------------------------------------------
# Registros: una fila por paciente con su botón «Editar» (sólo quien puede editar: rchar)
# ---------------------------------------------------------------------------
REG_COLUMNAS = ["Paciente", "Fecha prestación", "N° factura", "Fecha factura", "Vto. factura", "Importe", "Saldo",
                "Estado"]
REG_ANCHOS = [2.6, 1.15, 1.35, 1.1, 1.1, 1.2, 1.2, 1.9]
REG_ANCHO_EDITAR = 0.42
REG_POR_TANDA = 60

CSS_REG = """<style>
.fr-h{font-size:.6rem;font-weight:600;color:#8A7F85;text-transform:uppercase;letter-spacing:.04em;padding:0}
.fr-c{font-size:.78rem;color:#1F1A1D;font-variant-numeric:tabular-nums;line-height:1.2;padding:.05rem 0;overflow-wrap:anywhere}
.fr-c small{display:block;color:#8A7F85;font-size:.68rem}
div[class*="st-key-frblk_"] div[data-testid="stVerticalBlock"]{gap:.05rem}
div[class*="st-key-frblk_"] div[data-testid="stHorizontalBlock"]{gap:.45rem;border-top:1px solid #F1ECEF;padding:.12rem 0;
 align-items:center}
div[class*="st-key-frblk_"] div[data-testid="stMarkdownContainer"] p{margin:0}
div[class*="st-key-fred_"] button{min-height:1.5rem;height:1.5rem;padding:0 .3rem;border-radius:6px;color:#8C2853}
div[class*="st-key-fred_"] button p{font-size:.82rem;line-height:1}
div[class*="st-key-fred_"] button:hover{background:#F7E9EF}
.fr-n{text-align:right;white-space:nowrap}
.fr-ok{opacity:.55}
.fr-tot{background:#EFE7EB;border-radius:8px;padding:.35rem .6rem;font-weight:700;font-size:.84rem;display:flex;
 justify-content:space-between;margin-top:.3rem}
</style>"""


def _celda(t: str, *, num: bool = False, color: str = "", tenue: bool = False, chico: str = "") -> str:
    estilo = f' style="color:{color}"' if color else ""
    clases = "fr-c" + (" fr-n" if num else "") + (" fr-ok" if tenue else "")
    return f'<div class="{clases}"{estilo}>{_e(t)}{f"<small>{_e(chico)}</small>" if chico else ""}</div>'


def _clave_css(t: Any) -> str:
    """Una clave segura para usar como clase CSS (sólo letras, números y _)."""
    import re as _re

    return _re.sub(r"[^A-Za-z0-9_]+", "_", str(t))[:60]


def celdas_registro(r: pd.Series, hoy: date) -> list[tuple[str, dict[str, Any]]]:
    """Las celdas de un renglón de Registros: (texto, opciones)."""
    texto, tono = _estado(r, hoy, False, "")
    tenue = ff.etapa(r) in ("cobrado", "anulado", "sin_cargo", "incluida") and not r["_debito_abierto"]
    saldo = _saldo(r)
    detalle = " · ".join(x for x in ("" if r["_procedimiento"] == "SIN PROCEDIMIENTO" else r["_procedimiento"],
                                     "" if r["_medico"] == "SIN MÉDICO" else r["_medico"]) if x)
    venc_rojo = bool(r["_vencido"]) and not tenue
    return [
        (r["_paciente"] or "Sin nombre", {"chico": detalle, "tenue": tenue}),
        (_f(r.get("_prestacion")), {"tenue": tenue}),
        (_texto(r["_numero_factura"]) or "—", {"tenue": tenue}),
        (_f(r.get("_fecha_factura")) if r["_facturada"] else "", {"tenue": tenue}),
        (_f(r.get("_vencimiento")) if r["_facturada"] else "", {"tenue": tenue, "color": COLOR["rojo"] if venc_rojo else ""}),
        (pesos(r["_monto"]) if r["_monto"] else ("en la aspiración" if r.get("_te_aspiracion", False) else
                                                 "en el total" if r.get("_incluida", False) else "—"),
         {"num": True, "tenue": tenue}),
        (pesos(saldo) if saldo is not None else "", {"num": True, "tenue": tenue,
                                                    "color": "#9A6200" if saldo is not None and not r["_facturada"] else ""}),
        (texto, {"color": COLOR[tono]}),
    ]


def render_registros(st: Any, ctx: dict[str, Any], x: pd.DataFrame, *, guardar: Callable[[pd.DataFrame], Any], key: str,
                     lista: str, vacio: str = "No hay registros.") -> None:
    """Registros: filtros, barra para avanzar y una planilla por obra social con «Editar» en cada paciente."""
    st.markdown(CSS + ff._CSS + CSS_REG, unsafe_allow_html=True)
    k = f"{key}_fr_{lista}"
    edita = bool(ctx.get("puede_editar", True))
    pend = st.session_state.pop(f"{key}_abrir", None)
    if pend:
        acc, fila = pend
        if acc == "nuevo":
            ff.dialogo_nuevo(st, ctx, guardar, key)
        elif acc != "editar" or edita:
            ff.dialogo(st, acc, fila, ctx, guardar, key)
    if x is None or x.empty:
        st.markdown(f'<div class="fz-vacio">✅ {html.escape(vacio)}</div>', unsafe_allow_html=True)
        return
    c1, c2 = st.columns([1.6, 2.4], vertical_alignment="bottom")
    obras = list(ff.resumen_obras(x)["Obra social"])
    elegidas = c1.multiselect("Obra social", obras, key=f"{k}_os", placeholder="Todas las obras sociales",
                              label_visibility="collapsed") if len(obras) > 1 else []
    t = c2.text_input("Buscar", key=f"{k}_q", label_visibility="collapsed",
                      placeholder="Buscar paciente, procedimiento o N° de factura")
    if elegidas:
        x = x[x["_obra_social"].isin(elegidas)]
    x = ff.buscar(x, t)
    if x.empty:
        st.info("Nada coincide con los filtros.")
        return
    # En orden: obras sociales A-Z (las que tienen algo pendiente primero) y, en cada una, por fecha de
    # prestación (lo pendiente de pago primero).
    pend_os = set(x.loc[ff.pendiente_pago(x), "_obra_social"])
    obras_orden = sorted(x["_obra_social"].unique(), key=lambda o: (o not in pend_os, str(o).upper()))
    orden = [int(i) for os_ in obras_orden for i in ff.ordenar(x[x["_obra_social"] == os_], "Por fecha").index]
    st.session_state[f"{key}_orden"] = orden
    for acc in ("facturar", "cobro", "debito", "editar"):
        st.session_state[f"{key}_orden_{acc}"] = [i for i in orden if ff.accion(ctx["d"].loc[i])[0] == acc]
    _barra(st, ctx, x, k=k, key=key, guardar=guardar, forzar_incompleta=False, orden="Por fecha")

    limite = int(st.session_state.get(f"{k}_limite", REG_POR_TANDA))
    mostradas = set(orden[:limite])
    anchos = REG_ANCHOS + ([REG_ANCHO_EDITAR] if edita else [])
    for os_ in obras_orden:
        g = x[x["_obra_social"] == os_]
        filas = [int(j) for j in ff.ordenar(g, "Por fecha").index if int(j) in mostradas]
        if not filas:
            continue
        with st.container(border=True, key=f"frblk_{_clave_css(k)}_{_clave_css(os_)}"):
            st.markdown(f'<div class="fz-tit" style="margin:-.2rem -.2rem .3rem;border-radius:8px"><b>{_e(os_)}</b>'
                        f'<span>{_e(resumen_txt(g))}</span></div>', unsafe_allow_html=True)
            cab = st.columns(anchos, vertical_alignment="bottom")
            for j, titulo in enumerate(REG_COLUMNAS + ([""] if edita else [])):
                derecha = titulo in ("Importe", "Saldo")
                cab[j].markdown(f'<div class="fr-h{" fr-n" if derecha else ""}">{_e(titulo)}</div>',
                                unsafe_allow_html=True)
            for i in filas:
                r = ctx["d"].loc[i]
                cols = st.columns(anchos, vertical_alignment="center")
                for j, (texto, op) in enumerate(celdas_registro(r, ctx["hoy"])):
                    cols[j].markdown(_celda(texto, **op), unsafe_allow_html=True)
                if edita and cols[-1].button("✎", key=f"fred_{_clave_css(k)}_{i}", type="tertiary",
                                             help="Editar los datos de este paciente"):
                    ff.dialogo(st, "editar", i, ctx, guardar, key)
            vivos = g[~g["_cancelado"]]
            st.markdown(f'<div class="fr-tot"><span>TOTAL · {len(g)} paciente(s)</span><span>Importe '
                        f'{pesos(float(vivos["_monto"].sum()))} · Saldo {pesos(pendiente_total(g))}</span></div>',
                        unsafe_allow_html=True)
    if len(orden) > limite:
        if st.button(f"Mostrar más ({len(orden) - limite} restantes)", key=f"{k}_mas", icon=":material/expand_more:"):
            st.session_state[f"{k}_limite"] = limite + REG_POR_TANDA
            st.rerun()
