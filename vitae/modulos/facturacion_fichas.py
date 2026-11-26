"""Facturación VMR / VM en fichas: una tarjeta por paciente en lugar de la tabla tipo Excel.

Cada ficha muestra quién es (paciente, obra social, procedimiento, médico, fecha), el importe, en qué
etapa está (Cargado → Facturado → Cobrado) con un aviso (plazo, vencimiento, débito) y el botón de lo
que sigue. El botón abre un formulario corto con los datos sugeridos:

    Facturar          N° (el siguiente de la serie), fecha (hoy) y vencimiento (los días típicos de esa
                      obra social).
    Registrar cobro   fecha de pago; todo o una parte (lo que falta queda como débito a reclamar).
    Débito            importe, estado (A reclamar, Reclamado, Recuperado, Aceptado) y motivo.
    Editar            todos los datos, o anular.
    Nuevo / Agregar   un registro nuevo (o una cirugía de la Agenda) con el importe sugerido.

«Guardar y siguiente» guarda y abre la ficha que sigue en la lista. Todo se escribe en la misma
planilla de Facturación con el formato de cada columna (``control_facturacion.aplicar``).
"""
from __future__ import annotations

import html
from datetime import date
from typing import Any, Callable

import pandas as pd

import vitae.modulos.control_facturacion as cf
from vitae.modulos.pagos_pendientes import _texto, pesos

POR_PAGINA = 10
FILTROS = ["Para facturar", "Por cobrar", "Vencidas", "Débitos", "Cobradas", "Anuladas", "Todas"]
ORDENES = ["Más urgentes", "Más recientes", "Mayor importe", "Paciente (A-Z)"]
ESTADOS = ["Pendiente", "Completo", "Parcial", "Vencido", "Anulado"]

_CSS = """<style>
.ff-top{display:flex;justify-content:space-between;gap:.8rem;align-items:flex-start}
.ff-nom{font-weight:800;font-size:1.02rem;color:#2B2226;line-height:1.25}
.ff-sub{color:#7A6E74;font-size:.82rem;margin-top:.1rem}
.ff-imp{font-weight:800;font-size:1.02rem;color:#2B2226;text-align:right;white-space:nowrap}
.ff-bd{display:inline-block;border-radius:999px;padding:.08rem .55rem;font-size:.74rem;font-weight:700;margin-top:.2rem;white-space:nowrap}
.ff-trk{display:flex;align-items:center;gap:.4rem;margin:.65rem 0 .2rem;font-size:.78rem;color:#7A6E74}
.ff-dot{width:.7rem;height:.7rem;border-radius:50%;border:2px solid #CFC6CB;display:inline-block;vertical-align:-1px;margin-right:.25rem}
.ff-dot.ok{background:#5E9C76;border-color:#5E9C76}.ff-dot.mal{background:#BD4B4B;border-color:#BD4B4B}
.ff-ln{flex:1;height:2px;background:#E8E3E6;min-width:.8rem}.ff-ln.ok{background:#5E9C76}
.ff-et{font-weight:700;color:#2B2226}
.ff-nota{color:#8A7F85;font-size:.78rem;margin-top:.15rem}
.ff-pag{color:#8A7F85;font-size:.82rem;text-align:center;padding-top:.45rem}
</style>"""
TONOS = {"rojo": ("#FBE4E4", "#8A1F1F"), "ambar": ("#FDF0DC", "#7A4B00"), "gris": ("#F1EEF0", "#5E5258"),
         "verde": ("#E4F3E8", "#1F5130"), "azul": ("#E6EEF8", "#2F4F7A")}


# ---------------------------------------------------------------------------
# Estado de cada ficha
# ---------------------------------------------------------------------------
def etapa(r: pd.Series) -> str:
    if r["_cancelado"]:
        return "anulado"
    if not r["_facturada"]:
        return "cargado"
    return "facturado" if r["_pendiente"] > 0.004 else "cobrado"


def _f(v: Any) -> str:
    return cf._fecha_txt(v) if isinstance(v, pd.Timestamp) else ""


def aviso(r: pd.Series, hoy: date | None = None) -> tuple[str, str]:
    """El aviso de la ficha: (texto, tono)."""
    h = pd.Timestamp(hoy or date.today()).normalize()
    e = etapa(r)
    if e == "anulado":
        return "anulado", "gris"
    if e == "cargado":
        dias = r.get("_dias_plazo")
        if dias is None or pd.isna(dias):
            return "sin fecha de prestación", "gris"
        d = int(dias)
        if d < 0:
            return f"plazo vencido hace {-d} días", "rojo"
        return (f"quedan {d} días para facturar", "ambar" if d <= cf.AVISO_PRESENTACION else "gris")
    if r["_debito_abierto"]:
        return f"débito {pesos(r['_debito'], 0)} · {(r['_debito_estado'] or 'a reclamar').lower()}", "rojo"
    if e == "cobrado":
        return (f"cobrada el {_f(r['_fecha_pago'])}" if pd.notna(r.get("_fecha_pago")) else "cobrada"), "verde"
    v = r.get("_vencimiento")
    if r["_vencido"]:
        return (f"vencida hace {int((h - v).days)} días" if pd.notna(v) else "vencida"), "rojo"
    if pd.isna(v):
        return "sin vencimiento", "ambar"
    dv = int((v - h).days)
    return f"vence el {_f(v)}", ("ambar" if dv <= cf.POR_VENCER else "azul")


def accion(r: pd.Series, incompleta: bool = False) -> tuple[str, str]:
    """La acción principal: (clave, texto del botón)."""
    if incompleta:
        return "editar", "Completar datos"
    e = etapa(r)
    if e == "cargado":
        return "facturar", "Facturar"
    if e == "facturado":
        return ("debito", "Débito") if r["_debito_abierto"] and r["_pendiente"] <= r["_debito"] + 0.5 else (
            "cobro", "Registrar cobro")
    if e == "cobrado" and r["_debito_abierto"]:
        return "debito", "Débito"
    return "editar", "Editar"


def incompleta(r: pd.Series) -> bool:
    return (r["_obra_social"] == "SIN OBRA SOCIAL" or r["_procedimiento"] == "SIN PROCEDIMIENTO"
            or r["_medico"] == "SIN MÉDICO" or r["_monto"] <= 0)


def html_ficha(r: pd.Series, hoy: date | None = None, nota: str = "") -> str:
    e = html.escape
    texto, tono = aviso(r, hoy)
    fondo, color = TONOS[tono]
    et = etapa(r)
    sub = " · ".join(x for x in (
        "" if r["_obra_social"] == "SIN OBRA SOCIAL" else r["_obra_social"],
        "" if r["_procedimiento"] == "SIN PROCEDIMIENTO" else r["_procedimiento"],
        "" if r["_medico"] == "SIN MÉDICO" else r["_medico"], _f(r.get("_prestacion"))) if x)
    if r.get("_numero_factura"):
        sub += f" · Fact. {r['_numero_factura']}"
    pasos = ["Cargado", "Facturado", "Cobrado"]
    hecho = {"cargado": 1, "facturado": 2, "cobrado": 3, "anulado": 0}[et]
    trk = []
    for i, p in enumerate(pasos, start=1):
        clase = "ok" if i <= hecho else ""
        actual = ' class="ff-et"' if i == hecho + 1 and et != "anulado" else ""
        trk.append(f'<span><span class="ff-dot {clase}"></span><span{actual}>{p}</span></span>')
        if i < 3:
            trk.append(f'<span class="ff-ln {"ok" if i < hecho else ""}"></span>')
    nom = e(r["_paciente"] or "Sin nombre")
    falta = ""
    if incompleta(r):
        falta = "Falta: " + ", ".join(t for t, ok in (
            ("obra social", r["_obra_social"] == "SIN OBRA SOCIAL"), ("procedimiento", r["_procedimiento"] == "SIN PROCEDIMIENTO"),
            ("médico", r["_medico"] == "SIN MÉDICO"), ("importe", r["_monto"] <= 0)) if ok)
    notas = " · ".join(x for x in (falta, nota) if x)
    pend = (f'<div class="ff-nota">saldo {pesos(r["_pendiente"], 0)}</div>'
            if et == "facturado" and 0.5 < r["_pendiente"] < r["_monto"] - 0.5 else "")
    return (f'<div class="ff-top"><div><div class="ff-nom">{nom}</div><div class="ff-sub">{e(sub) or "&nbsp;"}</div></div>'
            f'<div><div class="ff-imp">{pesos(r["_monto"], 0) if r["_monto"] else "—"}</div>{pend}'
            f'<div style="text-align:right"><span class="ff-bd" style="background:{fondo};color:{color}">{e(texto)}</span>'
            f'</div></div></div><div class="ff-trk">{"".join(trk)}</div>'
            + (f'<div class="ff-nota">{e(notas)}</div>' if notas else ""))


# ---------------------------------------------------------------------------
# Sugerencias
# ---------------------------------------------------------------------------
def sugerir_numero(d: pd.DataFrame) -> str:
    """El siguiente número de la serie más usada (PPPP-NNNNNNNN)."""
    if d.empty:
        return ""
    a = d[~d["_cancelado"] & d["_facturada"]]
    n = a.sort_values("_fecha_factura", na_position="first")["_numero_factura"].astype(str).str.strip()
    partes = n.str.extract(r"^(\D*)(\d{1,5})(\D+)(\d{1,8})\D*$").dropna()
    if partes.empty:
        return ""
    ultimas = partes.tail(30)
    pv = ultimas[1].mode().iloc[0]
    serie = partes[partes[1] == pv]
    fila = serie.iloc[-1]
    mayor = max(int(x) for x in serie[3])
    return f"{fila[0]}{pv}{fila[2]}{str(mayor + 1).zfill(len(fila[3]))}"


def dias_vencimiento(d: pd.DataFrame, obra_social: str) -> int:
    """Los días típicos entre la factura y el vencimiento (de esa obra social, si hay datos)."""
    if d.empty:
        return 30
    a = d[d["_fecha_factura"].notna() & d["_vencimiento"].notna()]
    dif = (a["_vencimiento"] - a["_fecha_factura"]).dt.days
    a = a.assign(_dif=dif)[(dif >= 0) & (dif <= 365)]
    os_ = a[a["_obra_social"] == obra_social]
    if len(os_) >= 3:
        return int(os_["_dif"].median())
    return int(a["_dif"].median()) if len(a) >= 3 else 30


def importe_sugerido(d: pd.DataFrame, procedimiento: str, obra_social: str) -> float | None:
    """El importe más reciente para ese procedimiento y obra social (o sólo el procedimiento)."""
    if d.empty or not procedimiento:
        return None
    a = d[~d["_cancelado"] & (d["_monto"] > 0)]
    for m in ((a["_procedimiento"] == procedimiento) & (a["_obra_social"] == obra_social),
              a["_procedimiento"] == procedimiento):
        x = a[m]
        if len(x):
            x = x.sort_values("_prestacion", na_position="first")
            return float(x["_monto"].tail(5).median())
    return None


def opciones(d: pd.DataFrame, col: str, vacio: str) -> list[str]:
    if d.empty:
        return []
    s = d.loc[d[col] != vacio, col].astype(str)
    return list(s.value_counts().index)


# ---------------------------------------------------------------------------
# Listas
# ---------------------------------------------------------------------------
def filtrar(d: pd.DataFrame, filtro: str) -> pd.DataFrame:
    if d.empty:
        return d
    et = d.apply(etapa, axis=1)
    if filtro == "Para facturar":
        return d[et == "cargado"]
    if filtro == "Por cobrar":
        return d[et == "facturado"]
    if filtro == "Vencidas":
        return d[(et == "facturado") & d["_vencido"]]
    if filtro == "Débitos":
        return d[d["_debito_abierto"] & ~d["_cancelado"]]
    if filtro == "Cobradas":
        return d[et == "cobrado"]
    if filtro == "Anuladas":
        return d[et == "anulado"]
    return d


def cuentas_filtros(d: pd.DataFrame) -> dict[str, int]:
    return {f_: len(filtrar(d, f_)) for f_ in FILTROS}


def buscar(x: pd.DataFrame, texto: str) -> pd.DataFrame:
    t = _texto(texto).lower().strip()
    if not t or x.empty:
        return x
    blob = (x["_paciente"].astype(str) + " " + x["_obra_social"].astype(str) + " " + x["_procedimiento"].astype(str)
            + " " + x["_medico"].astype(str) + " " + x["_numero_factura"].astype(str)).str.lower()
    return x[blob.str.contains(t, regex=False, na=False)]


def ordenar(x: pd.DataFrame, orden: str) -> pd.DataFrame:
    if x.empty:
        return x
    if orden == "Más recientes":
        return x.sort_values("_prestacion", ascending=False, na_position="last", kind="stable")
    if orden == "Mayor importe":
        return x.sort_values("_monto", ascending=False, kind="stable")
    if orden == "Paciente (A-Z)":
        return x.sort_values("_paciente", kind="stable")
    # Más urgentes: plazo vencido / vencidas / débitos primero, después lo que vence antes
    urg = pd.Series(3, index=x.index)
    urg[(~x["_facturada"]) & (x["_dias_plazo"] < 0)] = 0
    urg[x["_vencido"] | x["_debito_abierto"]] = 1
    urg[(~x["_facturada"]) & x["_dias_plazo"].between(0, cf.AVISO_PRESENTACION)] = 2
    clave = x["_dias_plazo"].where(~x["_facturada"], (x["_vencimiento"] - pd.Timestamp.today()).dt.days)
    return x.assign(_u=urg, _c=clave.fillna(9999)).sort_values(["_u", "_c", "_monto"], ascending=[True, True, False],
                                                                kind="stable").drop(columns=["_u", "_c"])


# ---------------------------------------------------------------------------
# Formularios (diálogos)
# ---------------------------------------------------------------------------
def _txt_fecha(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    t = pd.Timestamp(v)
    return "" if pd.isna(t) else t.strftime("%d/%m/%Y")


def _txt_imp(v: Any) -> str:
    try:
        return "" if v is None or float(v) <= 0 else f"{float(v):.2f}"
    except (TypeError, ValueError):
        return ""


def cambios_facturar(fila: int, numero: str, fecha: Any, vencimiento: Any) -> list[dict[str, Any]]:
    c = [{"fila": fila, "campo": "numero_factura", "valor": _texto(numero)},
         {"fila": fila, "campo": "fecha_factura", "valor": _txt_fecha(fecha)}]
    if vencimiento:
        c.append({"fila": fila, "campo": "vencimiento", "valor": _txt_fecha(vencimiento)})
    return c


def cambios_cobro(r: pd.Series, fila: int, fecha: Any, cobrado: float | None, motivo: str = "") -> list[dict[str, Any]]:
    """Todo (cobrado None o ≥ saldo) → Completo. Una parte → Parcial y lo que falta queda como débito a reclamar."""
    c = [{"fila": fila, "campo": "fecha_pago", "valor": _txt_fecha(fecha)}]
    saldo = float(r["_pendiente"]) if r["_pendiente"] > 0.004 else float(r["_monto"])
    ya = float(r["_monto"]) - saldo
    if cobrado is None or cobrado >= saldo - 0.5:
        c.append({"fila": fila, "campo": "estado", "valor": "Completo"})
        return c
    debito = max(float(r["_monto"]) - ya - float(cobrado), 0.0)
    c += [{"fila": fila, "campo": "estado", "valor": "Parcial"},
          {"fila": fila, "campo": "debito", "valor": _txt_imp(debito)},
          {"fila": fila, "campo": "debito_estado", "valor": "A reclamar"}]
    if motivo:
        c.append({"fila": fila, "campo": "debito_motivo", "valor": motivo})
    return c


def _abrir_siguiente(st: Any, key: str, accion_: str, fila: int) -> None:
    orden = st.session_state.get(f"{key}_orden_{accion_}") or st.session_state.get(f"{key}_orden") or []
    if fila in orden:
        resto = orden[orden.index(fila) + 1:]
        if resto:
            st.session_state[f"{key}_abrir"] = (accion_, int(resto[0]))


def _guardar_y(st: Any, ctx: dict[str, Any], guardar: Callable[[pd.DataFrame], Any], cambios: list[dict[str, Any]],
               aviso_: str, key: str, siguiente: tuple[str, int] | None = None) -> None:
    try:
        guardar(cf.aplicar(ctx["raw"], ctx["cols"], cambios, ctx["hoy"]))
    except Exception as exc:
        st.error(f"No se pudo guardar: {exc}")
        return
    if siguiente:
        _abrir_siguiente(st, key, *siguiente)
    st.session_state[f"{key}_cf_rev"] = int(st.session_state.get(f"{key}_cf_rev", 0)) + 1
    st.toast(aviso_, icon=":material/check_circle:")
    st.rerun()


def _botones(st: Any, k: str) -> tuple[bool, bool]:
    c1, c2 = st.columns(2)
    g = c1.button("Guardar", key=f"{k}_g", use_container_width=True, icon=":material/save:")
    s = c2.button("Guardar y siguiente", key=f"{k}_s", type="primary", use_container_width=True,
                  icon=":material/arrow_forward:")
    return g, s


def _cabecera(st: Any, r: pd.Series) -> None:
    sub = " · ".join(x for x in (r["_obra_social"], r["_procedimiento"], pesos(r["_monto"], 0)) if x)
    st.markdown(f'<div class="ff-sub" style="margin:-.4rem 0 .6rem">{html.escape(sub)}</div>', unsafe_allow_html=True)


def dialogo(st: Any, accion_: str, fila: int, ctx: dict[str, Any], guardar: Callable[[pd.DataFrame], Any], key: str) -> None:
    """Abre el formulario de la acción para la fila (índice de la planilla)."""
    d, hoy = ctx["d"], ctx["hoy"]
    if fila not in d.index:
        return
    r = d.loc[fila]
    titulos = {"facturar": "Facturar", "cobro": "Registrar cobro", "debito": "Débito", "editar": "Editar",
               "documentos": "Documentación"}
    k = f"{key}_dlg_{accion_}_{fila}"

    def facturar() -> None:
        _cabecera(st, r)
        numero = st.text_input("N° de factura", value=r["_numero_factura"] or sugerir_numero(d), key=f"{k}_n")
        c1, c2 = st.columns(2)
        fecha = c1.date_input("Fecha de factura", value=hoy, format="DD/MM/YYYY", key=f"{k}_f")
        dias = dias_vencimiento(d, r["_obra_social"])
        venc = c2.date_input("Vencimiento", value=pd.Timestamp(fecha or hoy).date() + pd.Timedelta(days=dias),
                             format="DD/MM/YYYY", key=f"{k}_v", help=f"Sugerido: {dias} días (lo habitual de esta obra social).")
        g, s = _botones(st, k)
        if g or s:
            if not _texto(numero):
                st.error("Poné el número de factura.")
                return
            _guardar_y(st, ctx, guardar, cambios_facturar(fila, numero, fecha, venc), f"{r['_paciente']}: facturada",
                       key, ("facturar", fila) if s else None)

    def cobro() -> None:
        _cabecera(st, r)
        saldo = float(r["_pendiente"]) if r["_pendiente"] > 0.004 else float(r["_monto"])
        fecha = st.date_input("Fecha de pago", value=hoy, format="DD/MM/YYYY", key=f"{k}_f")
        modo = st.radio("¿Cuánto cobraste?", [f"Todo ({pesos(saldo, 0)})", "Una parte"], horizontal=True, key=f"{k}_m")
        cobrado, motivo = None, ""
        if modo == "Una parte":
            cobrado = st.number_input("Importe cobrado", min_value=0.0, max_value=float(saldo), step=1000.0, format="%.2f",
                                      key=f"{k}_i")
            st.caption(f"Lo que falta ({pesos(max(saldo - cobrado, 0), 0)}) queda como débito para reclamar.")
            motivo = st.text_input("Motivo del débito (opcional)", key=f"{k}_mot")
        g, s = _botones(st, k)
        if g or s:
            _guardar_y(st, ctx, guardar, cambios_cobro(r, fila, fecha, cobrado, motivo), f"{r['_paciente']}: cobro anotado",
                       key, ("cobro", fila) if s else None)

    def debito() -> None:
        _cabecera(st, r)
        base = float(r["_debito"]) if r["_debito"] > 0.004 else float(max(r["_monto"] - r["_cobrado"], 0))
        imp = st.number_input("Importe del débito", min_value=0.0, value=base, step=1000.0, format="%.2f", key=f"{k}_i")
        actual = r["_debito_estado"] if r["_debito_estado"] in cf.ESTADOS_DEBITO else "A reclamar"
        est = st.radio("Estado", cf.ESTADOS_DEBITO, index=cf.ESTADOS_DEBITO.index(actual), horizontal=True, key=f"{k}_e",
                       help="Recuperado: lo pagaron después. Aceptado: no se recupera.")
        motivo = st.text_input("Motivo", value=r["_debito_motivo"], key=f"{k}_mot")
        g, s = _botones(st, k)
        if g or s:
            cambios = [{"fila": fila, "campo": "debito", "valor": _txt_imp(imp)},
                       {"fila": fila, "campo": "debito_estado", "valor": est if imp > 0 else ""},
                       {"fila": fila, "campo": "debito_motivo", "valor": motivo}]
            _guardar_y(st, ctx, guardar, cambios, f"{r['_paciente']}: débito {est.lower()}", key,
                       ("debito", fila) if s else None)

    def editar() -> None:
        c1, c2 = st.columns([1.6, 1])
        pac = c1.text_input("Paciente", value=r["_paciente"], key=f"{k}_p")
        prest = c2.date_input("Fecha de prestación", value=r["_prestacion"].date() if pd.notna(r["_prestacion"]) else None,
                              format="DD/MM/YYYY", key=f"{k}_pr")
        c1, c2 = st.columns(2)
        os_ = c1.selectbox("Obra social", _con(opciones(d, "_obra_social", "SIN OBRA SOCIAL"), r["_obra_social"], "SIN OBRA SOCIAL"),
                           key=f"{k}_os", accept_new_options=True)
        proc = c2.selectbox("Procedimiento", _con(opciones(d, "_procedimiento", "SIN PROCEDIMIENTO"), r["_procedimiento"],
                                                  "SIN PROCEDIMIENTO"), key=f"{k}_pc", accept_new_options=True)
        c1, c2 = st.columns(2)
        med = c1.selectbox("Médico", _con(opciones(d, "_medico", "SIN MÉDICO"), r["_medico"], "SIN MÉDICO"), key=f"{k}_md",
                           accept_new_options=True)
        sug = importe_sugerido(d, proc or "", os_ or "")
        imp = c2.number_input("Importe", min_value=0.0, value=float(r["_monto"]) or float(sug or 0), step=1000.0,
                              format="%.2f", key=f"{k}_im",
                              help=f"Sugerido: {pesos(sug, 0)} (lo último de este procedimiento)." if sug else None)
        with st.expander("Factura, cobro y débito", expanded=bool(r["_facturada"])):
            c1, c2, c3 = st.columns(3)
            num = c1.text_input("N° de factura", value=r["_numero_factura"], key=f"{k}_n")
            ff = c2.date_input("Fecha de factura", value=r["_fecha_factura"].date() if pd.notna(r["_fecha_factura"]) else None,
                               format="DD/MM/YYYY", key=f"{k}_ff")
            ve = c3.date_input("Vencimiento", value=r["_vencimiento"].date() if pd.notna(r["_vencimiento"]) else None,
                               format="DD/MM/YYYY", key=f"{k}_ve")
            c1, c2, c3 = st.columns(3)
            fp = c1.date_input("Fecha de pago", value=r["_fecha_pago"].date() if pd.notna(r["_fecha_pago"]) else None,
                               format="DD/MM/YYYY", key=f"{k}_fp")
            est_actual = r["_estado_original"] if r["_estado_original"] in ESTADOS else None
            est = c2.selectbox("Estado", ESTADOS, index=ESTADOS.index(est_actual) if est_actual else 0, key=f"{k}_es",
                               accept_new_options=True)
            deb = c3.number_input("Débito", min_value=0.0, value=float(r["_debito"]), step=1000.0, format="%.2f", key=f"{k}_db")
        obs_actual = _texto(ctx["raw"].loc[fila].get(ctx["cols"].get("observaciones") or "observaciones", ""))
        obs = st.text_area("Observaciones", value=obs_actual, key=f"{k}_ob", height=70)
        g, s = _botones(st, k)
        if g or s:
            nuevos = {"paciente": pac.upper(), "mes": _txt_fecha(prest), "obra_social": os_ if os_ != "SIN OBRA SOCIAL" else "",
                      "procedimiento": proc if proc != "SIN PROCEDIMIENTO" else "", "medico": med if med != "SIN MÉDICO" else "",
                      "monto": _txt_imp(imp), "numero_factura": num, "fecha_factura": _txt_fecha(ff),
                      "vencimiento": _txt_fecha(ve), "fecha_pago": _txt_fecha(fp), "estado": est, "debito": _txt_imp(deb),
                      "observaciones": obs}
            antes = {"paciente": r["_paciente"].upper(), "mes": _txt_fecha(r["_prestacion"]),
                     "obra_social": "" if r["_obra_social"] == "SIN OBRA SOCIAL" else r["_obra_social"],
                     "procedimiento": "" if r["_procedimiento"] == "SIN PROCEDIMIENTO" else r["_procedimiento"],
                     "medico": "" if r["_medico"] == "SIN MÉDICO" else r["_medico"], "monto": _txt_imp(r["_monto"]),
                     "numero_factura": r["_numero_factura"], "fecha_factura": _txt_fecha(r["_fecha_factura"]),
                     "vencimiento": _txt_fecha(r["_vencimiento"]), "fecha_pago": _txt_fecha(r["_fecha_pago"]),
                     "estado": est_actual or ESTADOS[0], "debito": _txt_imp(r["_debito"]),
                     "observaciones": obs_actual}
            cambios = [{"fila": fila, "campo": c, "valor": v} for c, v in nuevos.items() if _texto(v) != _texto(antes[c])]
            if not cambios:
                st.info("No hay cambios.")
                return
            _guardar_y(st, ctx, guardar, cambios, f"{pac}: guardado", key, ("editar", fila) if s else None)

    def documentos() -> None:
        from vitae.modulos import presentaciones as pr

        _cabecera(st, r)
        pedidos = pr.requisitos(ctx.get("requisitos"), ctx["empresa"], r["_obra_social"])
        tiene = pr.lista_docs(r.get("_documentacion", ""))
        extras = [x for x in tiene if pr._sin_acentos(x) not in {pr._sin_acentos(p_) for p_ in pedidos}]
        st.caption(f"Lo que pide {r['_obra_social']}. Tildá lo que ya tenés; con todo tildado queda lista para presentar.")
        marcados = []
        for j, doc in enumerate(pedidos + extras):
            if st.checkbox(doc, value=pr._sin_acentos(doc) in {pr._sin_acentos(x) for x in tiene}, key=f"{k}_c{j}"):
                marcados.append(doc)
        otro = st.text_input("Otro documento (opcional)", key=f"{k}_otro", placeholder="Ej.: Informe de anatomía patológica")
        if _texto(otro):
            marcados.append(_texto(otro))
        g, s = _botones(st, k)
        if g or s:
            _guardar_y(st, ctx, guardar, [{"fila": fila, "campo": "documentacion", "valor": pr.SEP.join(marcados)}],
                       f"{r['_paciente']}: documentación guardada", key, ("documentos", fila) if s else None)

    cuerpo = {"facturar": facturar, "cobro": cobro, "debito": debito, "editar": editar, "documentos": documentos}[accion_]
    titulo = f"{titulos[accion_]} · {r['_paciente'] or 'registro'}"
    st.dialog(titulo, width="large" if accion_ == "editar" else "medium")(cuerpo)()


def _con(lista: list[str], actual: str, vacio: str = "") -> list[str]:
    """Las opciones con la actual primera (si hay)."""
    out = [o for o in lista if o != actual]
    if actual:
        out.insert(0, actual)
    return out


def dialogo_nuevo(st: Any, ctx: dict[str, Any], guardar: Callable[[pd.DataFrame], Any], key: str,
                  cirugia: dict[str, Any] | None = None) -> None:
    """Un registro nuevo (o una cirugía de la Agenda que falta cargar), con el importe sugerido."""
    d, hoy = ctx["d"], ctx["hoy"]
    cir = cirugia or {}
    k = f"{key}_dlg_nuevo_{abs(hash(str(cir.get('Paciente', '')) + str(cir.get('Fecha', '')))) % 10**8}"

    def cuerpo() -> None:
        c1, c2 = st.columns([1.6, 1])
        pac = c1.text_input("Paciente", value=_texto(cir.get("Paciente")).upper(), key=f"{k}_p",
                            placeholder="APELLIDO NOMBRE")
        f0 = cir.get("Fecha")
        prest = c2.date_input("Fecha de prestación", value=pd.Timestamp(f0).date() if f0 is not None and pd.notna(f0) else hoy,
                              format="DD/MM/YYYY", key=f"{k}_pr")
        c1, c2 = st.columns(2)
        os_l = opciones(d, "_obra_social", "SIN OBRA SOCIAL")
        os_ = c1.selectbox("Obra social", _con(os_l, _texto(cir.get("Obra social")), ""), key=f"{k}_os",
                           accept_new_options=True, index=0 if cir.get("Obra social") else None,
                           placeholder="Elegí o escribí")
        pr_l = opciones(d, "_procedimiento", "SIN PROCEDIMIENTO")
        proc = c2.selectbox("Procedimiento", _con(pr_l, _texto(cir.get("Procedimiento")), ""), key=f"{k}_pc",
                            accept_new_options=True, index=0 if cir.get("Procedimiento") else None,
                            placeholder="Elegí o escribí")
        c1, c2 = st.columns(2)
        md_l = opciones(d, "_medico", "SIN MÉDICO")
        med = c1.selectbox("Médico", _con(md_l, _texto(cir.get("Médico")), ""), key=f"{k}_md", accept_new_options=True,
                           index=0 if cir.get("Médico") else None, placeholder="Elegí o escribí")
        sug = importe_sugerido(d, proc or "", os_ or "")
        imp = c2.number_input("Importe", min_value=0.0, value=float(sug or 0), step=1000.0, format="%.2f",
                              key=f"{k}_im_{proc}_{os_}",
                              help=f"Sugerido: {pesos(sug, 0)} (lo último de este procedimiento)." if sug else None)
        g, s = _botones(st, k)
        if g or s:
            if not _texto(pac):
                st.error("Poné el nombre del paciente.")
                return
            nuevo = cf.nueva_fila(ctx["raw"], ctx["cols"], {"Fecha": pd.Timestamp(prest) if prest else pd.NaT,
                                                            "Paciente": pac, "Obra social": os_ or "",
                                                            "Procedimiento": proc or "", "Médico": med or ""},
                                  _txt_imp(imp), hoy)
            try:
                guardar(nuevo)
            except Exception as exc:
                st.error(f"No se pudo guardar: {exc}")
                return
            if s:
                st.session_state[f"{key}_abrir"] = ("nuevo", -1)
            st.session_state[f"{key}_cf_rev"] = int(st.session_state.get(f"{key}_cf_rev", 0)) + 1
            st.toast(f"{pac.upper()}: cargado", icon=":material/check_circle:")
            st.rerun()

    titulo = "Agregar cirugía a facturación" if cirugia else "Nuevo registro"
    st.dialog(titulo, width="medium")(cuerpo)()


def render_fichas(st: Any, ctx: dict[str, Any], x: pd.DataFrame, *, guardar: Callable[[pd.DataFrame], Any], key: str,
                  vacio: str = "No hay nada acá.", notas: dict[Any, str] | None = None, buscar_txt: bool = True,
                  forzar_incompleta: bool = False, lista: str = "") -> None:
    """Las fichas de ``x`` (filas de ``ctx['d']``) con búsqueda, páginas y el botón de cada una."""
    st.markdown(_CSS, unsafe_allow_html=True)
    k = f"{key}_ff{('_' + lista) if lista else ''}"
    # Formulario pendiente de abrir («Guardar y siguiente»)
    pend = st.session_state.pop(f"{key}_abrir", None)
    if pend:
        acc, fila = pend
        if acc == "nuevo":
            dialogo_nuevo(st, ctx, guardar, key)
        else:
            dialogo(st, acc, fila, ctx, guardar, key)
    if x is None or x.empty:
        st.markdown(f'<div class="fp-ok">✅ {html.escape(vacio)}</div>', unsafe_allow_html=True)
        return
    if buscar_txt and len(x) > 6:
        t = st.text_input("Buscar", key=f"{k}_q", placeholder="Buscar paciente, obra social, procedimiento o N° de factura",
                          label_visibility="collapsed")
        x = buscar(x, t)
        if x.empty:
            st.info("Ninguna ficha coincide con la búsqueda.")
            return
    filas = [int(i) for i in x.index]
    st.session_state[f"{key}_orden"] = filas
    st.session_state[f"{key}_orden_documentos"] = [i for i in filas if etapa(ctx["d"].loc[i]) == "cargado"]
    for acc in ("facturar", "cobro", "debito", "editar"):
        st.session_state[f"{key}_orden_{acc}"] = [i for i in filas
                                                  if accion(ctx["d"].loc[i], forzar_incompleta or incompleta(ctx["d"].loc[i]))[0] == acc]
    paginas = max(1, -(-len(filas) // POR_PAGINA))
    pag = min(int(st.session_state.get(f"{k}_pag", 0)), paginas - 1)
    for i in filas[pag * POR_PAGINA:(pag + 1) * POR_PAGINA]:
        _ficha(st, ctx, i, k=k, key=key, guardar=guardar, forzar_incompleta=forzar_incompleta,
               nota=(notas or {}).get(i, ""))
    if paginas > 1:
        c1, c2, c3 = st.columns([1, 2, 1])
        if c1.button("← Anteriores", key=f"{k}_prev", disabled=pag == 0, use_container_width=True):
            st.session_state[f"{k}_pag"] = pag - 1
            st.rerun()
        c2.markdown(f'<div class="ff-pag">{pag * POR_PAGINA + 1}–{min((pag + 1) * POR_PAGINA, len(filas))} de '
                    f'{len(filas)}</div>', unsafe_allow_html=True)
        if c3.button("Siguientes →", key=f"{k}_next", disabled=pag >= paginas - 1, use_container_width=True):
            st.session_state[f"{k}_pag"] = pag + 1
            st.rerun()


def render_agenda_fichas(st: Any, ctx: dict[str, Any], *, guardar: Callable[[pd.DataFrame], Any], key: str) -> None:
    """Cirugías realizadas que faltan cargar, como fichas con «Agregar»."""
    ag = ctx["c"]["agenda"]
    if ag.empty:
        return
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="cf-tit">Cirugías realizadas sin cargar · {len(ag)}</div>'
                f'<div class="cf-sub">Sólo las de procedimientos de {html.escape(ctx["empresa"])}. Tocá «Agregar», revisá '
                'el importe sugerido y guardá.</div>', unsafe_allow_html=True)
    if len(ag) > 1 and st.button(f"Agregar las {len(ag)} con el importe sugerido", key=f"{key}_ag_todas",
                                 icon=":material/playlist_add:", help="Después podés revisar cada una en 2 · Facturar."):
        agregar_todas(st, ctx, guardar, key)
    for i, r in ag.reset_index(drop=True).iterrows():
        with st.container(border=True):
            c1, c2 = st.columns([4.1, 1.5])
            sub = " · ".join(_texto(v) for v in (r["Obra social"], r["Procedimiento"], r["Médico"], cf._fecha_txt(r["Fecha"]))
                             if _texto(v))
            c1.markdown(f'<div class="ff-nom">{html.escape(_texto(r["Paciente"]))}</div><div class="ff-sub">'
                        f'{html.escape(sub)}</div><span class="ff-bd" style="background:{TONOS["rojo"][0]};'
                        f'color:{TONOS["rojo"][1]}">realizada, sin cargar</span>', unsafe_allow_html=True)
            if c2.button("Agregar", key=f"{key}_ag_add_{i}", type="primary", use_container_width=True, icon=":material/add:"):
                dialogo_nuevo(st, ctx, guardar, key, r.to_dict())


def filas_agenda(ctx: dict[str, Any]) -> pd.DataFrame:
    """Todas las cirugías sin cargar → la planilla con sus filas nuevas (importe sugerido)."""
    nuevo, d, hoy = ctx["raw"], ctx["d"], ctx["hoy"]
    for _, r in ctx["c"]["agenda"].iterrows():
        imp = importe_sugerido(d, _texto(r["Procedimiento"]), _texto(r["Obra social"]))
        nuevo = cf.nueva_fila(nuevo, ctx["cols"], r.to_dict(), _txt_imp(imp), hoy,
                              observaciones="Cargada desde la Agenda de Quirófano")
    return nuevo


def agregar_todas(st: Any, ctx: dict[str, Any], guardar: Callable[[pd.DataFrame], Any], key: str) -> None:
    n = len(ctx["c"]["agenda"])
    try:
        guardar(filas_agenda(ctx))
    except Exception as exc:
        st.error(f"No se pudo guardar: {exc}")
        return
    st.session_state[f"{key}_cf_rev"] = int(st.session_state.get(f"{key}_cf_rev", 0)) + 1
    st.toast(f"{n} cirugía(s) agregada(s)", icon=":material/check_circle:")
    st.rerun()


# ---------------------------------------------------------------------------
# Vistas agrupadas: por obra social y por factura (facturas conjuntas)
# ---------------------------------------------------------------------------
VISTAS = ["Por obra social", "Por factura", "Por paciente"]
_CSS_GRUPOS = """<style>
.fg-os{font-weight:800;font-size:1.08rem;color:#2B2226}
.fg-chips{margin-top:.35rem}
.fg-fac{font-weight:800;color:#2B2226}
.fg-pac{display:flex;justify-content:space-between;gap:.6rem;align-items:center}
.fg-pac b{color:#2B2226}
</style>"""


def _urgencia(x: pd.DataFrame) -> pd.Series:
    """0 = plazo vencido sin facturar / factura vencida / débito; 1 = por vencer; 2 = resto."""
    u = pd.Series(2, index=x.index)
    u[(~x["_facturada"]) & x["_dias_plazo"].between(0, cf.AVISO_PRESENTACION)] = 1
    u[((~x["_facturada"]) & (x["_dias_plazo"] < 0)) | x["_vencido"] | x["_debito_abierto"]] = 0
    return u


def resumen_obras(x: pd.DataFrame) -> pd.DataFrame:
    """Una fila por obra social: cantidad, importe, para facturar, pendiente, vencidas, débitos, facturas conjuntas."""
    if x.empty:
        return pd.DataFrame(columns=["Obra social", "Prestaciones", "Importe", "Para facturar", "Pendiente", "Vencidas",
                                     "Débitos", "Conjuntas", "Urgencia"])
    conj = cf.facturas_conjuntas(x)
    u = _urgencia(x)
    filas = []
    for os_, g in x.groupby("_obra_social"):
        num = g["_numero_factura"].astype(str).str.strip()
        filas.append({"Obra social": os_, "Prestaciones": len(g), "Importe": float(g["_monto"].sum()),
                      "Para facturar": int((~g["_facturada"] & ~g["_cancelado"]).sum()),
                      "Pendiente": float(g.loc[g["_facturada"], "_pendiente"].sum()),
                      "Vencidas": int(g["_vencido"].sum()), "Débitos": float(g.loc[g["_debito_abierto"], "_debito"].sum()),
                      "Conjuntas": int(num[num.isin(conj.index)].nunique()), "Urgencia": int(u[g.index].min())})
    return pd.DataFrame(filas).sort_values(["Urgencia", "Importe"], ascending=[True, False]).reset_index(drop=True)


def grupos_factura(x: pd.DataFrame) -> list[tuple[str, list[int]]]:
    """[(N° de factura, filas)] en orden de urgencia; las filas sin N° van aparte con clave ''."""
    if x.empty:
        return []
    num = x["_numero_factura"].astype(str).str.strip()
    u = _urgencia(x)
    grupos = []
    for n_, g in x[num != ""].groupby(num[num != ""]):
        grupos.append((n_, list(g.index), int(u[g.index].min()), float(g["_monto"].sum())))
    grupos.sort(key=lambda t: (t[2], -t[3]))
    out = [(n_, idx) for n_, idx, _, _ in grupos]
    sin = list(x[num == ""].index)
    if sin:
        out.append(("", sin))
    return out


def aviso_factura(g: pd.DataFrame, hoy: date | None = None) -> tuple[str, str]:
    h = pd.Timestamp(hoy or date.today()).normalize()
    pend = float(g["_pendiente"].sum())
    if g["_cancelado"].all():
        return "anulada", "gris"
    if pend <= 0.5:
        return "cobrada", "verde"
    if g["_debito_abierto"].any():
        return f"débito {pesos(float(g.loc[g['_debito_abierto'], '_debito'].sum()), 0)}", "rojo"
    v = g["_vencimiento"].dropna()
    if g["_vencido"].any() and len(v):
        return f"vencida hace {int((h - v.min()).days)} días", "rojo"
    if float(g["_cobrado"].sum()) > 0.5:
        return "cobrada en parte", "azul"
    if len(v):
        dv = int((v.min() - h).days)
        return f"vence el {_f(v.min())}", "ambar" if dv <= cf.POR_VENCER else "azul"
    return "sin vencimiento", "ambar"


def _chip(texto: str, tono: str) -> str:
    fondo, color = TONOS[tono]
    return f'<span class="ff-bd" style="background:{fondo};color:{color};margin-right:.3rem">{html.escape(texto)}</span>'


def _ficha(st: Any, ctx: dict[str, Any], i: int, *, k: str, key: str, guardar: Callable[[pd.DataFrame], Any],
           forzar_incompleta: bool = False, nota: str = "") -> None:
    """Una ficha completa (tarjeta) con sus botones."""
    r = ctx["d"].loc[i]
    inc = forzar_incompleta or incompleta(r)
    acc, etiqueta = accion(r, inc and forzar_incompleta)
    docs_btn = "requisitos" in ctx and etapa(r) == "cargado" and not (inc and forzar_incompleta)
    if docs_btn:
        from vitae.modulos import presentaciones as pr

        nd = pr.nota_docs(r, ctx.get("requisitos"), ctx["empresa"])
        nota = " · ".join(x_ for x_ in (nota, nd) if x_)
    with st.container(border=True):
        st.markdown(html_ficha(r, ctx["hoy"], nota), unsafe_allow_html=True)
        if docs_btn:
            b = st.columns([1.9, 1.4, 1.1, 1.5])
            if b[1].button("Documentación", key=f"{k}_d_{i}", use_container_width=True, icon=":material/fact_check:"):
                dialogo(st, "documentos", i, ctx, guardar, key)
            b = [b[0], b[2], b[3]]
        else:
            b = st.columns([3, 1.1, 1.5]) if acc != "editar" else st.columns([4.1, 1.5])
        if acc != "editar":
            if b[1].button("Editar", key=f"{k}_e_{i}", use_container_width=True, icon=":material/edit:"):
                dialogo(st, "editar", i, ctx, guardar, key)
            if b[2].button(etiqueta, key=f"{k}_a_{i}", type="primary", use_container_width=True,
                           icon={"facturar": ":material/receipt_long:", "cobro": ":material/payments:",
                                 "debito": ":material/report:"}.get(acc)):
                dialogo(st, acc, i, ctx, guardar, key)
        elif b[1].button(etiqueta, key=f"{k}_a_{i}", use_container_width=True, type="primary" if inc else "secondary",
                         icon=":material/edit:"):
            dialogo(st, "editar", i, ctx, guardar, key)


def _factura(st: Any, ctx: dict[str, Any], numero: str, filas: list[int], *, k: str, key: str,
             guardar: Callable[[pd.DataFrame], Any]) -> None:
    """Una factura con varios pacientes (factura conjunta): sus datos, lo cobrado y cada paciente."""
    d = ctx["d"]
    g = d.loc[filas]
    texto, tono = aviso_factura(g, ctx["hoy"])
    ff_ = g["_fecha_factura"].dropna()
    vv = g["_vencimiento"].dropna()
    sub = " · ".join(x_ for x_ in (g["_obra_social"].iloc[0], f"{len(g)} pacientes",
                                    f"del {_f(ff_.min())}" if len(ff_) else "",
                                    f"vence {_f(vv.min())}" if len(vv) else "") if x_)
    with st.container(border=True):
        st.markdown(f'<div class="ff-top"><div><div class="fg-fac">Factura {html.escape(numero)}</div>'
                    f'<div class="ff-sub">{html.escape(sub)}</div></div><div><div class="ff-imp">'
                    f'{pesos(float(g["_monto"].sum()), 0)}</div><div style="text-align:right">{_chip(texto, tono)}</div>'
                    f'</div></div>', unsafe_allow_html=True)
        fact, cob = float(g["_monto"].sum()), float(g["_cobrado"].sum())
        if fact > 0:
            st.progress(min(cob / fact, 1.0), text=f"Cobrado {pesos(cob, 0)} · falta {pesos(float(g['_pendiente'].sum()), 0)}")
        for i in filas:
            r = d.loc[i]
            t_, to_ = aviso(r, ctx["hoy"])
            c1, c2, c3 = st.columns([4.2, 1.2, 1.1])
            c1.markdown(f'<div class="fg-pac"><div><b>{html.escape(r["_paciente"])}</b> <span class="ff-sub">'
                        f'{html.escape(" · ".join(x_ for x_ in (r["_procedimiento"], _f(r["_prestacion"])) if x_))}</span>'
                        f'</div><div>{_chip(t_, to_) if to_ in ("rojo", "verde") else ""}</div></div>',
                        unsafe_allow_html=True)
            c2.markdown(f'<div class="ff-imp" style="font-size:.92rem;padding-top:.3rem">{pesos(r["_monto"], 0)}</div>',
                        unsafe_allow_html=True)
            if c3.button("Abrir", key=f"{k}_fe_{i}", use_container_width=True, icon=":material/edit:"):
                dialogo(st, "editar", i, ctx, guardar, key)
        b1, b2, b3 = st.columns([2.4, 1.5, 1.6])
        if b2.button("Datos de la factura", key=f"{k}_fd_{numero}", use_container_width=True, icon=":material/receipt_long:"):
            dialogo_factura(st, ctx, filas, "datos", guardar, key)
        if float(g["_pendiente"].sum()) > 0.5 and b3.button("Registrar cobro", key=f"{k}_fc_{numero}", type="primary",
                                                            use_container_width=True, icon=":material/payments:"):
            dialogo_factura(st, ctx, filas, "cobro", guardar, key)


def cambios_factura_cobro(d: pd.DataFrame, filas: list[int], fecha: Any, cobrados: dict[int, float]) -> list[dict[str, Any]]:
    """Cobro de una factura conjunta: cada paciente con lo que pagaron (si es menos, la diferencia es débito)."""
    out = []
    for i in filas:
        r = d.loc[i]
        if r["_cancelado"] or r["_pendiente"] <= 0.004:
            continue
        saldo = float(r["_pendiente"])
        c = cobrados.get(i, saldo)
        out += cambios_cobro(r, i, fecha, None if c >= saldo - 0.5 else float(c))
    return out


def dialogo_factura(st: Any, ctx: dict[str, Any], filas: list[int], accion_: str, guardar: Callable[[pd.DataFrame], Any],
                    key: str) -> None:
    d, hoy = ctx["d"], ctx["hoy"]
    g = d.loc[filas]
    numero = _texto(g["_numero_factura"].iloc[0])
    k = f"{key}_dlgf_{accion_}_{numero}"

    def cobro() -> None:
        fecha = st.date_input("Fecha de pago", value=hoy, format="DD/MM/YYYY", key=f"{k}_f")
        pend = g[(g["_pendiente"] > 0.004) & ~g["_cancelado"]]
        tabla = pd.DataFrame({"fila": pend.index, "Paciente": pend["_paciente"], "Saldo": pend["_pendiente"],
                              "Cobrado": pend["_pendiente"]}).reset_index(drop=True)
        st.caption("Si a algún paciente le pagaron menos, cambiá lo cobrado: la diferencia queda como débito a reclamar.")
        ed = st.data_editor(tabla, hide_index=True, use_container_width=True, key=f"{k}_t", disabled=["Paciente", "Saldo"],
                            column_config={"fila": None, "Saldo": st.column_config.NumberColumn(format="localized"),
                                           "Cobrado": st.column_config.NumberColumn(format="localized", min_value=0.0)})
        if st.button(f"Registrar cobro de la factura {numero}", type="primary", key=f"{k}_ok", use_container_width=True):
            cobrados = {int(f_): float(c or 0) for f_, c in zip(ed["fila"], ed["Cobrado"])}
            cambios = cambios_factura_cobro(d, list(pend.index), fecha, cobrados)
            _guardar_y(st, ctx, guardar, cambios, f"Factura {numero}: cobro anotado", key)

    def datos() -> None:
        st.caption(f"Se cambia en los {len(g)} pacientes de la factura.")
        num = st.text_input("N° de factura", value=numero, key=f"{k}_n")
        c1, c2 = st.columns(2)
        ff_ = g["_fecha_factura"].dropna()
        vv = g["_vencimiento"].dropna()
        fecha = c1.date_input("Fecha de factura", value=ff_.min().date() if len(ff_) else hoy, format="DD/MM/YYYY",
                              key=f"{k}_f")
        venc = c2.date_input("Vencimiento", value=vv.min().date() if len(vv) else None, format="DD/MM/YYYY", key=f"{k}_v")
        if st.button("Guardar", type="primary", key=f"{k}_ok", use_container_width=True, icon=":material/save:"):
            if not _texto(num):
                st.error("Poné el número de factura.")
                return
            cambios = []
            for i in filas:
                cambios += cambios_facturar(i, num, fecha, venc)
            _guardar_y(st, ctx, guardar, cambios, f"Factura {num}: datos guardados", key)

    titulo = {"cobro": "Cobro de la factura", "datos": "Datos de la factura"}[accion_] + f" · {numero}"
    st.dialog(titulo, width="large" if accion_ == "cobro" else "medium")({"cobro": cobro, "datos": datos}[accion_])()


def _contenido(st: Any, ctx: dict[str, Any], x: pd.DataFrame, *, k: str, key: str, guardar: Callable[[pd.DataFrame], Any],
               forzar_incompleta: bool, notas: dict[Any, str] | None, por_pagina: int = 15) -> None:
    """Las fichas de un grupo: las facturas conjuntas juntas, el resto de a una; con páginas."""
    items: list[tuple[str, Any]] = []
    for numero, filas in grupos_factura(ordenar(x, "Más urgentes")):
        if numero and len(filas) > 1:
            items.append(("factura", (numero, filas)))
        else:
            items += [("ficha", i) for i in filas]
    paginas = max(1, -(-len(items) // por_pagina))
    pag = min(int(st.session_state.get(f"{k}_pag", 0)), paginas - 1)
    for tipo, val in items[pag * por_pagina:(pag + 1) * por_pagina]:
        if tipo == "factura":
            _factura(st, ctx, val[0], val[1], k=k, key=key, guardar=guardar)
        else:
            _ficha(st, ctx, val, k=k, key=key, guardar=guardar, forzar_incompleta=forzar_incompleta,
                   nota=(notas or {}).get(val, ""))
    if paginas > 1:
        c1, c2, c3 = st.columns([1, 2, 1])
        if c1.button("← Anteriores", key=f"{k}_prev", disabled=pag == 0, use_container_width=True):
            st.session_state[f"{k}_pag"] = pag - 1
            st.rerun()
        c2.markdown(f'<div class="ff-pag">{pag * por_pagina + 1}–{min((pag + 1) * por_pagina, len(items))} de '
                    f'{len(items)}</div>', unsafe_allow_html=True)
        if c3.button("Siguientes →", key=f"{k}_next", disabled=pag >= paginas - 1, use_container_width=True):
            st.session_state[f"{k}_pag"] = pag + 1
            st.rerun()


def render_lista(st: Any, ctx: dict[str, Any], x: pd.DataFrame, *, guardar: Callable[[pd.DataFrame], Any], key: str,
                 lista: str, vacio: str = "No hay nada acá.", notas: dict[Any, str] | None = None,
                 forzar_incompleta: bool = False, vista: str = "Por obra social") -> None:
    """La lista de fichas con la vista a elegir: por obra social (grupos con sus totales), por factura (las
    conjuntas juntas) o por paciente (de a una)."""
    st.markdown(_CSS + _CSS_GRUPOS, unsafe_allow_html=True)
    k = f"{key}_fl_{lista}"
    pend = st.session_state.pop(f"{key}_abrir", None)
    if pend:
        acc, fila = pend
        if acc == "nuevo":
            dialogo_nuevo(st, ctx, guardar, key)
        else:
            dialogo(st, acc, fila, ctx, guardar, key)
    if x is None or x.empty:
        st.markdown(f'<div class="fp-ok">✅ {html.escape(vacio)}</div>', unsafe_allow_html=True)
        return
    c1, c2 = st.columns([1.5, 2.5])
    with c1:
        v = st.pills("Ver", VISTAS, default=vista, key=f"{k}_vista", label_visibility="collapsed") or vista
    with c2:
        t = st.text_input("Buscar", key=f"{k}_q", label_visibility="collapsed",
                          placeholder="Buscar paciente, obra social, procedimiento o N° de factura")
    x = buscar(x, t)
    if x.empty:
        st.info("Nada coincide con la búsqueda.")
        return
    filas = [int(i) for i in ordenar(x, "Más urgentes").index]
    st.session_state[f"{key}_orden"] = filas
    for acc in ("facturar", "cobro", "debito", "editar"):
        st.session_state[f"{key}_orden_{acc}"] = [i for i in filas if accion(ctx["d"].loc[i], forzar_incompleta or
                                                                            incompleta(ctx["d"].loc[i]))[0] == acc]
    if v == "Por paciente":
        render_fichas(st, ctx, ordenar(x, "Más urgentes"), guardar=guardar, key=key, lista=lista, buscar_txt=False,
                      notas=notas, forzar_incompleta=forzar_incompleta, vacio=vacio)
        return
    if v == "Por factura":
        _contenido(st, ctx, x, k=f"{k}_fac", key=key, guardar=guardar, forzar_incompleta=forzar_incompleta, notas=notas)
        return
    # Por obra social: un grupo por obra social con sus totales; se abre para ver sus fichas.
    abiertas = set(st.session_state.get(f"{k}_abiertas", set()))
    ro = resumen_obras(x)
    if len(ro) == 1:
        abiertas.add(ro.iloc[0]["Obra social"])
    for _, r in ro.iterrows():
        os_ = r["Obra social"]
        chips = []
        if r["Para facturar"]:
            chips.append(_chip(f"{r['Para facturar']} para facturar", "ambar"))
        if r["Pendiente"] > 0.5:
            chips.append(_chip(f"por cobrar {pesos(r['Pendiente'], 0)}", "azul"))
        if r["Vencidas"]:
            chips.append(_chip(f"{r['Vencidas']} vencida(s)", "rojo"))
        if r["Débitos"] > 0.5:
            chips.append(_chip(f"débitos {pesos(r['Débitos'], 0)}", "rojo"))
        sub = f"{int(r['Prestaciones'])} paciente(s)" + (f" · {int(r['Conjuntas'])} factura(s) conjunta(s)"
                                                         if r["Conjuntas"] else "")
        abierta = os_ in abiertas
        with st.container(border=True):
            c1, c2 = st.columns([4.2, 1.3])
            c1.markdown(f'<div class="ff-top"><div><div class="fg-os">{html.escape(os_)}</div><div class="ff-sub">{sub}</div>'
                        f'<div class="fg-chips">{"".join(chips)}</div></div><div class="ff-imp">{pesos(r["Importe"], 0)}'
                        f'</div></div>', unsafe_allow_html=True)
            if c2.button("Ocultar" if abierta else "Ver", key=f"{k}_os_{os_}", use_container_width=True,
                         icon=":material/expand_less:" if abierta else ":material/expand_more:"):
                abiertas.symmetric_difference_update({os_})
                st.session_state[f"{k}_abiertas"] = abiertas
                st.rerun()
            if abierta:
                _contenido(st, ctx, x[x["_obra_social"] == os_], k=f"{k}_{os_}", key=key, guardar=guardar,
                           forzar_incompleta=forzar_incompleta, notas=notas)
