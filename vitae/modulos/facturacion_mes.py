"""Facturación VMR / VM · Mes: la guía de cómo viene un mes (por fecha de prestación).

    Mes (selector, por defecto el actual) → resumen del mes → calendario del 1 al último día →
    situación del mes (qué falta, con «Ir» al paso que lo resuelve) → por obra social → registros
    (por obra social o por paciente; todo el mes o sólo lo pendiente; un día o el mes entero).

Todo con el estilo de Deudas Impositivas (``facturacion_planilla``). Lo pendiente de pago, arriba.
"""
from __future__ import annotations

import calendar
import html
from datetime import date
from typing import Any, Callable

import pandas as pd

import vitae.modulos.cierre_facturacion as cfx
import vitae.modulos.control_facturacion as cf
import vitae.modulos.facturacion_fichas as ff
import vitae.modulos.facturacion_planilla as fz
from vitae.modulos.pagos_pendientes import pesos

DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MOSTRAR = ["Todo el mes", "Sólo lo pendiente"]

CSS = """<style>
.fm-cab{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:.3rem .9rem;margin:.2rem 0 .6rem}
.fm-cab b{font-size:1.02rem;letter-spacing:.03em;color:#1F1A1D;text-transform:uppercase}
.fm-cab span{font-size:.84rem;color:#6E6268;font-variant-numeric:tabular-nums}
.fm-cal{border:1px solid #E8E3E6;border-radius:12px;overflow:hidden;background:#fff;margin-bottom:1rem}
.fm-sem{display:grid;grid-template-columns:repeat(7,minmax(0,1fr))}
.fm-dn{font-size:.68rem;font-weight:600;color:#8A7F85;text-transform:uppercase;letter-spacing:.04em;padding:.45rem .55rem;
 background:#F7EFF3;border-bottom:1px solid #EADDE4}
.fm-d{min-height:84px;padding:.4rem .5rem;border-top:1px solid #F1ECEF;border-left:1px solid #F1ECEF;font-size:.76rem;
 display:flex;flex-direction:column;gap:.12rem;font-variant-numeric:tabular-nums}
.fm-sem .fm-d:first-child{border-left:none}
.fm-d .n{font-weight:700;color:#3A2E34;font-size:.84rem}
.fm-d .t{color:#1F1A1D;font-weight:650}
.fm-d .i{color:#6E6268}
.fm-d .b{display:inline-block;border-radius:999px;padding:.02rem .42rem;font-size:.68rem;font-weight:700;margin-right:.15rem}
.fm-fuera{background:#FAF8F9}
.fm-vacio .n{color:#B9AFB4;font-weight:600}
.fm-fin{background:#FCFAFB}
.fm-pend{box-shadow:inset 3px 0 0 #D9A441}
.fm-alerta{box-shadow:inset 3px 0 0 #B14E5E}
.fm-ok{box-shadow:inset 3px 0 0 #5E9C76}
.fm-hoy .n{background:#8C2853;color:#fff;border-radius:999px;padding:0 .42rem;align-self:flex-start}
.fm-ley{display:flex;gap:.9rem;flex-wrap:wrap;font-size:.76rem;color:#6E6268;margin:-.5rem 0 .9rem}
.fm-ley i{display:inline-block;width:3px;height:12px;margin-right:.35rem;vertical-align:-2px}
@media (max-width: 760px){
 .fm-d{min-height:58px;padding:.3rem .3rem;font-size:.68rem}
 .fm-d .i,.fm-d .b{display:none}
 .fm-dn{padding:.35rem .3rem}
}
</style>"""


def _mes(s: pd.Series) -> pd.Series:
    return s.map(lambda f: f"{f.year:04d}-{f.month:02d}" if pd.notna(f) else "")


def registros(d: pd.DataFrame, mes: str) -> pd.DataFrame:
    """Todo lo registrado en el mes (por fecha de prestación), anuladas incluidas (van al final)."""
    if d is None or d.empty:
        return pd.DataFrame() if d is None else d
    return d[_mes(d["_prestacion"]) == mes]


def meses(d: pd.DataFrame, hoy: date) -> list[str]:
    actual = f"{hoy.year:04d}-{hoy.month:02d}"
    ms = set(_mes(d["_prestacion"])) - {""} if len(d) else set()
    return sorted(ms | {actual}, reverse=True)


def por_dia(x: pd.DataFrame) -> pd.DataFrame:
    """Un renglón por día con algo registrado: pacientes, importe, pendientes, alertas y cobrados."""
    cols = ["dia", "pacientes", "importe", "pendientes", "alertas", "cobrados", "no_factura"]
    if x.empty:
        return pd.DataFrame(columns=cols)
    a = x[~x["_cancelado"] & x["_prestacion"].notna()]
    if a.empty:
        return pd.DataFrame(columns=cols)
    pp = ff.pendiente_pago(a)
    alerta = pp & ((~a["_facturada"] & (a["_dias_plazo"] < 0)) | a["_vencido"] | a["_debito_abierto"])
    cobr = (a["_facturada"] & (a["_pendiente"] <= 0.004) & ~a["_debito_abierto"] & ~a["_sin_cargo"])
    t = pd.DataFrame({"dia": a["_prestacion"].dt.day, "importe": a["_monto"], "pend": pp, "alerta": alerta,
                      "cobr": cobr, "nf": a["_sin_cargo"]})
    g = t.groupby("dia").agg(pacientes=("importe", "size"), importe=("importe", "sum"), pendientes=("pend", "sum"),
                             alertas=("alerta", "sum"), cobrados=("cobr", "sum"), no_factura=("nf", "sum"))
    return g.reset_index()[cols].astype({"pendientes": int, "alertas": int, "cobrados": int, "no_factura": int})


def html_calendario(x: pd.DataFrame, mes: str, hoy: date) -> str:
    anio, m = int(mes[:4]), int(mes[5:7])
    pd_ = por_dia(x).set_index("dia") if len(x) else pd.DataFrame()
    semanas = calendar.Calendar(firstweekday=0).monthdayscalendar(anio, m)
    partes = [CSS, '<div class="fm-cal"><div class="fm-sem">'
              + "".join(f'<div class="fm-dn">{d_}</div>' for d_ in DIAS) + "</div>"]
    for semana in semanas:
        celdas = []
        for k, dia in enumerate(semana):
            clases = ["fm-d"]
            if k >= 5:
                clases.append("fm-fin")
            if dia == 0:
                celdas.append(f'<div class="{" ".join(clases + ["fm-fuera"])}"></div>')
                continue
            if (anio, m, dia) == (hoy.year, hoy.month, hoy.day):
                clases.append("fm-hoy")
            cont = ""
            if dia in pd_.index:
                r = pd_.loc[dia]
                if r["alertas"]:
                    clases.append("fm-alerta")
                elif r["pendientes"]:
                    clases.append("fm-pend")
                else:
                    clases.append("fm-ok")
                insignias = ""
                if r["pendientes"]:
                    fondo, color = ff.TONOS["rojo" if r["alertas"] else "ambar"]
                    insignias += f'<span class="b" style="background:{fondo};color:{color}">{int(r["pendientes"])} pend.</span>'
                if r["cobrados"]:
                    fondo, color = ff.TONOS["verde"]
                    insignias += f'<span class="b" style="background:{fondo};color:{color}">{int(r["cobrados"])} cobr.</span>'
                cont = (f'<span class="t">{int(r["pacientes"])} pac.</span>'
                        f'<span class="i">{pesos(float(r["importe"]), 0) if r["importe"] else ""}</span>'
                        f'<span>{insignias}</span>')
            else:
                clases.append("fm-vacio")
            celdas.append(f'<div class="{" ".join(clases)}"><span class="n">{dia}</span>{cont}</div>')
        partes.append('<div class="fm-sem">' + "".join(celdas) + "</div>")
    partes.append("</div>")
    partes.append('<div class="fm-ley"><span><i style="background:#B14E5E"></i>Con algo vencido (plazo o factura) o '
                  'débito</span><span><i style="background:#D9A441"></i>Con algo pendiente</span>'
                  '<span><i style="background:#5E9C76"></i>Todo cobrado</span></div>')
    return "".join(partes)


def resumen(x: pd.DataFrame) -> dict[str, Any]:
    a = x[~x["_cancelado"]] if len(x) else x
    if a.empty:
        return {"prestaciones": 0, "pacientes": 0, "importe": 0.0, "sin_facturar": 0, "importe_sin_facturar": 0.0,
                "facturado": 0.0, "cobrado": 0.0, "saldo": 0.0, "vencido": 0.0, "debitos": 0.0, "no_factura": 0,
                "pendientes": 0}
    sf = a[~a["_facturada"] & ~a["_sin_cargo"]]
    fact = a[a["_facturada"]]
    return {"prestaciones": int(len(a)), "pacientes": int(a["_paciente"].nunique()),
            "importe": float(a["_monto"].sum()), "sin_facturar": int(len(sf)), "importe_sin_facturar": float(sf["_monto"].sum()),
            "facturado": float(fact["_monto"].sum()), "cobrado": float(fact["_cobrado"].sum()),
            "saldo": float(fact["_pendiente"].sum()), "vencido": float(fact.loc[fact["_vencido"], "_pendiente"].sum()),
            "debitos": float(a.loc[a["_debito_abierto"], "_debito"].sum()), "no_factura": int(a["_sin_cargo"].sum()),
            "pendientes": int(ff.pendiente_pago(a).sum())}


def situacion(ctx: dict[str, Any], mes: str) -> list[dict[str, Any]]:
    """Qué falta en el mes, en el orden en que conviene resolverlo, con el paso donde se resuelve."""
    c = ctx["c"]

    def en(k: str) -> pd.DataFrame:
        return cfx._en_mes(c.get(k), mes)

    ag = c["agenda"]
    ag_mes = ag[_mes(pd.to_datetime(ag["Fecha"], errors="coerce")) == mes] if not ag.empty else ag
    sf, sfv = en("sin_facturar"), en("sin_facturar_vencido")
    ven = en("vencidas")
    pv = pd.concat([en("por_vencer"), en("sin_vencimiento")])
    deb, inc = en("debitos"), pd.concat([en("incompletos"), en("repetidas")])
    inc = inc[~inc.index.duplicated()] if len(inc) else inc

    def imp(x: pd.DataFrame, col: str) -> float:
        return float(x[col].sum()) if len(x) and col in x.columns else 0.0

    return [
        {"punto": "Cirugías de la Agenda sin cargar", "casos": len(ag_mes), "importe": 0.0, "paso": "cargar",
         "detalle": "Realizadas en quirófano y todavía no están en Facturación.", "grave": True},
        {"punto": "Datos para completar", "casos": len(inc), "importe": 0.0, "paso": "cargar",
         "detalle": "Falta obra social, procedimiento o importe, o el N° de factura está repetido.", "grave": False},
        {"punto": "Sin facturar", "casos": len(sf), "importe": imp(sf, "_monto"), "paso": "facturar",
         "detalle": (f"{len(sfv)} con el plazo de {cf.PLAZO_PRESENTACION} días vencido." if len(sfv) else
                     f"Dentro del plazo de {cf.PLAZO_PRESENTACION} días."), "grave": bool(len(sfv))},
        {"punto": "Facturas vencidas sin cobrar", "casos": len(ven), "importe": imp(ven, "_pendiente"), "paso": "cobrar",
         "detalle": "Hay que reclamarlas.", "grave": True},
        {"punto": "Facturas por cobrar (sin vencer)", "casos": len(pv), "importe": imp(pv, "_pendiente"), "paso": "cobrar",
         "detalle": "Seguirlas hasta el cobro.", "grave": False},
        {"punto": "Débitos abiertos", "casos": len(deb), "importe": imp(deb, "_debito"), "paso": "debitos",
         "detalle": "Lo que la obra social pagó de menos: reclamar o aceptar.", "grave": False},
    ]


def html_obras(x: pd.DataFrame) -> str:
    """Resumen del mes por obra social (estilo Deudas): prestaciones, importe, sin facturar, cobrado y saldo."""
    a = x[~x["_cancelado"]] if len(x) else x
    if a.empty:
        return ""
    grid = "minmax(0,1.4fr) 70px 110px 110px 110px 110px 110px"
    filas = []
    for os_, g in a.groupby("_obra_social"):
        fact = g[g["_facturada"]]
        sf = g[~g["_facturada"] & ~g["_sin_cargo"]]
        filas.append({"os": os_, "n": len(g), "imp": float(g["_monto"].sum()), "sf": float(sf["_monto"].sum()),
                      "nsf": len(sf), "fac": float(fact["_monto"].sum()), "cob": float(fact["_cobrado"].sum()),
                      "sal": float(fact["_pendiente"].sum()), "ven": bool(fact["_vencido"].any()),
                      "pp": bool(ff.pendiente_pago(g).any())})
    filas.sort(key=lambda f: (not f["pp"], -(f["sal"] + f["sf"]), -f["imp"]))
    e = html.escape
    partes = [fz.CSS, f'<div class="fz-bloque"><div class="fz-tit"><b>Por obra social</b><span>{len(filas)} obra(s) '
              f'social(es)</span></div><div class="fz-cols" style="grid-template-columns:{grid}"><div>Obra social</div>'
              '<div style="text-align:center">Pac.</div><div style="text-align:right">Importe</div>'
              '<div style="text-align:right">Sin facturar</div><div style="text-align:right">Facturado</div>'
              '<div style="text-align:right">Cobrado</div><div style="text-align:right">Saldo</div></div>']
    for f in filas:
        ok = not f["pp"]
        partes.append(f'<div class="fz-row{" fz-ok" if ok else ""}" style="grid-template-columns:{grid}">'
                      f'<div class="fz-g" style="font-size:.84rem">{e(f["os"])}</div><div class="fz-c">{f["n"]}</div>'
                      f'<div class="fz-n fz-dt">{pesos(f["imp"])}</div>'
                      f'<div class="fz-n fz-x" style="color:{fz.COLOR["ambar"] if f["nsf"] else "inherit"}">'
                      f'{pesos(f["sf"]) if f["nsf"] else ""}</div><div class="fz-n fz-x">{pesos(f["fac"]) if f["fac"] else ""}</div>'
                      f'<div class="fz-n fz-x">{pesos(f["cob"]) if f["cob"] else ""}</div>'
                      f'<div class="fz-n{" fz-venc" if f["ven"] else ""}">{pesos(f["sal"]) if f["sal"] > 0.004 else ""}</div>'
                      f'<div class="fz-meta">{f["n"]} pac. · importe {pesos(f["imp"])} · saldo {pesos(f["sal"])}</div></div>')
    t = {k: sum(f[k] for f in filas) for k in ("n", "imp", "sf", "fac", "cob", "sal")}
    partes.append(f'<div class="fz-row fz-tot" style="grid-template-columns:{grid}"><div class="fz-g">TOTAL</div>'
                  f'<div class="fz-c">{t["n"]}</div><div class="fz-n">{pesos(t["imp"])}</div>'
                  f'<div class="fz-n fz-x">{pesos(t["sf"])}</div><div class="fz-n fz-x">{pesos(t["fac"])}</div>'
                  f'<div class="fz-n fz-x">{pesos(t["cob"])}</div><div class="fz-n">{pesos(t["sal"])}</div></div></div>')
    return "".join(partes)


def render_mes(st: Any, ctx: dict[str, Any], *, key: str, ir: Callable[[str], Callable[[], None]],
               guardar: Callable[[pd.DataFrame], Any], nombres: dict[str, str]) -> None:
    d, hoy = ctx["d"], ctx["hoy"]
    ms = meses(d, hoy)
    k_mes = f"{key}_mes_sel"
    if st.session_state.get(k_mes) not in ms:
        st.session_state[k_mes] = ms[ms.index(f"{hoy.year:04d}-{hoy.month:02d}")]

    def mover(paso: int) -> Callable[[], None]:
        def _cb() -> None:
            actual = st.session_state.get(k_mes, ms[0])
            i = ms.index(actual) - paso  # la lista va del más nuevo al más viejo
            if 0 <= i < len(ms):
                st.session_state[k_mes] = ms[i]
        return _cb

    c1, c2, c3 = st.columns([0.7, 3, 0.7], vertical_alignment="bottom")
    pos = ms.index(st.session_state[k_mes])
    c1.button("← Anterior", key=f"{key}_mes_ant", on_click=mover(-1), disabled=pos >= len(ms) - 1,
              use_container_width=True)
    mes = c2.selectbox("Mes", ms, key=k_mes, label_visibility="collapsed",
                       format_func=lambda m_: cfx.mes_txt(m_).capitalize()
                       + (" · este mes" if m_ == f"{hoy.year:04d}-{hoy.month:02d}" else ""))
    c3.button("Siguiente →", key=f"{key}_mes_sig", on_click=mover(1), disabled=pos == 0, use_container_width=True)

    x = registros(d, mes)
    r = resumen(x)
    partes = [f"{r['prestaciones']} prestación(es)", f"importe {pesos(r['importe'], 0)}"]
    if r["sin_facturar"]:
        partes.append(f"{r['sin_facturar']} sin facturar ({pesos(r['importe_sin_facturar'], 0)})")
    partes += [f"facturado {pesos(r['facturado'], 0)}", f"cobrado {pesos(r['cobrado'], 0)}"]
    if r["saldo"] > 0.5:
        partes.append(f"falta cobrar {pesos(r['saldo'], 0)}")
    if r["no_factura"]:
        partes.append(f"{r['no_factura']} no se factura(n)")
    st.markdown(CSS + f'<div class="fm-cab"><b>{html.escape(cfx.mes_txt(mes))}</b>'
                f'<span>{html.escape(" · ".join(partes))}</span></div>', unsafe_allow_html=True)

    # Calendario
    st.markdown(html_calendario(x, mes, hoy), unsafe_allow_html=True)

    # Situación del mes
    sit = situacion(ctx, mes)
    pend = [s for s in sit if s["casos"]]
    st.markdown('<div class="cf-tit">Situación del mes</div>', unsafe_allow_html=True)
    if not pend:
        if r["prestaciones"]:
            st.markdown('<div class="fp-ok">✅ El mes está al día: todo cargado, facturado y cobrado.</div>',
                        unsafe_allow_html=True)
        else:
            st.markdown('<div class="fp-box">No hay prestaciones registradas en este mes.</div>', unsafe_allow_html=True)
    for i, s in enumerate(pend):
        clase = "mal" if s["grave"] else "info"
        icono = "⛔" if s["grave"] else "⚠️"
        cifra = f'{s["casos"]}' + (f' · {pesos(s["importe"], 0)}' if s["importe"] else "")
        b1, b2 = st.columns([6, 1.3], vertical_alignment="center")
        b1.markdown(f'<div class="fp-ck {clase}"><div>{icono} <b>{html.escape(s["punto"])}</b>'
                    f'<div class="fp-sub">{html.escape(s["detalle"])}</div></div>'
                    f'<div style="white-space:nowrap"><b>{html.escape(cifra)}</b></div></div>', unsafe_allow_html=True)
        b2.button(f"Ir a {nombres.get(s['paso'], s['paso'])}", key=f"{key}_mes_ir_{i}", on_click=ir(s["paso"]),
                  use_container_width=True)

    if x.empty:
        return

    # Por obra social
    st.markdown(html_obras(x), unsafe_allow_html=True)

    # Registros del mes
    st.markdown('<div class="cf-tit">Registros del mes</div><div class="fp-sub">Lo pendiente de pago, arriba. Elegí un '
                'día para ver sólo ese, o el mes entero.</div>', unsafe_allow_html=True)
    dias = por_dia(x)
    opciones = [0] + [int(v) for v in dias["dia"]]
    anio, m = int(mes[:4]), int(mes[5:7])

    def dia_txt(v: int) -> str:
        if not v:
            return "Todo el mes"
        fila = dias[dias["dia"] == v].iloc[0]
        return (f"{DIAS[date(anio, m, v).weekday()]} {v} · {int(fila['pacientes'])} pac."
                + (f" · {int(fila['pendientes'])} pend." if fila["pendientes"] else ""))

    k_dia = f"{key}_mes_dia_{mes}"
    if st.session_state.get(k_dia) not in opciones:
        st.session_state.pop(k_dia, None)
    f1, f2 = st.columns([1.6, 1.4], vertical_alignment="bottom")
    dia = f1.selectbox("Día", opciones, key=k_dia, format_func=dia_txt, label_visibility="collapsed")
    mostrar = f2.segmented_control("Mostrar", MOSTRAR, default=MOSTRAR[0], key=f"{key}_mes_mostrar",
                                   label_visibility="collapsed") or MOSTRAR[0]
    y = x if not dia else x[x["_prestacion"].dt.day == dia]
    if mostrar == MOSTRAR[1]:
        y = y[ff.pendiente_pago(y)] if len(y) else y
    titulo = cfx.mes_txt(mes) if not dia else f"{dia_txt(dia).split(' · ')[0]} de {cfx.mes_txt(mes)}"
    st.markdown(f'<div class="fz-ayuda">Mostrando: <b>{html.escape(titulo)}</b> · {len(y)} registro(s)'
                f'{" · sólo lo pendiente" if mostrar == MOSTRAR[1] else ""}</div>', unsafe_allow_html=True)
    fz.render_lista(st, ctx, y, guardar=guardar, key=key, lista=f"mes_{mes}_{dia}_{mostrar[:4]}", titulo=titulo,
                    vacio="No queda nada pendiente." if mostrar == MOSTRAR[1] else "No hay registros.")
