"""Facturación VMR / VM: estado de cada registro y los formularios para avanzar.

La planilla (estilo Deudas Impositivas) está en ``facturacion_planilla``. Acá: en qué etapa está cada
registro (Cargado → Facturado → Cobrado), su aviso (plazo, vencimiento, débito), la acción que sigue
y los formularios cortos con los datos sugeridos:

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
FILTROS = ["Para facturar", "Plazo vencido", "Por cobrar", "Vencidas", "Débitos", "Cobradas", "Anuladas", "Todas"]
ORDENES = ["Más urgentes", "Más recientes", "Mayor importe", "Paciente (A-Z)"]
# Estados de la planilla: «Completo» = pagado; «Pendiente» = sin cobro; «Anulado».
ESTADOS = ["Pendiente", "Completo", "Anulado"]

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
    if r.get("_sin_cargo", False):
        return "sin_cargo"
    if r.get("_incluida", False):
        return "incluida"
    if not r["_facturada"]:
        return "cargado"
    # Cobrada sólo si tiene importe y no queda saldo: sin importe no hay nada cobrado.
    if r["_monto"] <= 0.004:
        return "facturado"
    return "facturado" if r["_pendiente"] > 0.004 else "cobrado"


def _f(v: Any) -> str:
    return cf._fecha_txt(v) if isinstance(v, pd.Timestamp) else ""


def aviso(r: pd.Series, hoy: date | None = None) -> tuple[str, str]:
    """El aviso de la ficha: (texto, tono)."""
    h = pd.Timestamp(hoy or date.today()).normalize()
    e = etapa(r)
    if e == "anulado":
        return "anulado", "gris"
    if e == "sin_cargo":
        return "no se factura", "gris"
    if e == "incluida":
        if r.get("_te_aspiracion", False):
            return "va en la aspiración folicular", "gris"
        return "importe en la factura conjunta", "gris"
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
    if r["_monto"] <= 0.004:
        # Sin importe: si la planilla dice «Completo», está pagada pero falta el importe; si no, es «Pendiente»
        # = sin registro de cobro (regla de la planilla).
        if r.get("_completo", False):
            return "completa · sin importe", "ambar"
        return "sin registro de cobro", "ambar"
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
        if r["_monto"] <= 0.004:
            return "editar", "Completar importe"
        return ("debito", "Débito") if r["_debito_abierto"] and r["_pendiente"] <= r["_debito"] + 0.5 else (
            "cobro", "Registrar cobro")
    if e == "cobrado" and r["_debito_abierto"]:
        return "debito", "Débito"
    return "editar", "Editar"


def incompleta(r: pd.Series) -> bool:
    return (r["_obra_social"] == "SIN OBRA SOCIAL" or r["_procedimiento"] == "SIN PROCEDIMIENTO"
            or (r["_monto"] <= 0 and not r.get("_sin_cargo", False) and not r.get("_incluida", False)))


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


# Obras sociales que se facturan con los valores de otra (regla interna). En VMR, AVALIAN usa los de IPS:
# se toma el importe del procedimiento similar de IPS con la fecha más cercana, a 45 días como máximo.
VALORES_DE = {"VMR": {"AVALIAN": "IPS"}}
DIAS_REFERENCIA = 45


def _compacto(v: Any) -> str:
    from vitae.modulos.pagos_pendientes import _sin_acentos

    return "".join(ch for ch in _sin_acentos(_texto(v)).upper() if ch.isalnum())


def referencia_valores(empresa: str | None, obra_social: Any) -> str | None:
    """La obra social cuyos valores se usan para ``obra_social`` (ej.: AVALIAN → IPS en VMR), o None."""
    c = _compacto(obra_social)
    for os_, ref in VALORES_DE.get(str(empresa or "").upper(), {}).items():
        if os_ in c:
            return ref
    return None


def _proc_tokens(v: Any) -> frozenset[str]:
    from vitae.modulos.pagos_pendientes import _sin_acentos

    return frozenset(t for t in "".join(ch if ch.isalnum() else " " for ch in _sin_acentos(_texto(v)).lower()).split() if t)


def importe_referencia(d: pd.DataFrame, procedimiento: str, obra_ref: str, fecha: Any = None,
                       dias: int = DIAS_REFERENCIA) -> tuple[float | None, pd.Timestamp | None]:
    """El importe de ``obra_ref`` para el procedimiento similar con la fecha de prestación más cercana a
    ``fecha`` (a ``dias`` días como máximo). Devuelve (importe, fecha de esa prestación) o (None, None)."""
    if d is None or d.empty or not _texto(procedimiento):
        return None, None
    f0 = pd.Timestamp(fecha) if fecha is not None and pd.notna(pd.Timestamp(fecha)) else pd.Timestamp(date.today())
    ref = _compacto(obra_ref)
    a = d[~d["_cancelado"] & (d["_monto"] > 0) & d["_prestacion"].notna()
          & d["_obra_social"].map(lambda o: _compacto(o).startswith(ref))]
    if a.empty:
        return None, None
    t = _proc_tokens(procedimiento)
    toks = a["_procedimiento"].map(_proc_tokens)
    igual = toks == t
    similar = toks.map(lambda x: bool(x) and bool(t) and (x <= t or t <= x))
    for m in (igual, similar):
        x = a[m]
        if x.empty:
            continue
        dif = (x["_prestacion"] - f0).abs().dt.days
        x = x.assign(_dif=dif)[dif <= dias]
        if x.empty:
            continue
        x = x.sort_values(["_dif", "_prestacion"], ascending=[True, False])
        return float(x["_monto"].iloc[0]), x["_prestacion"].iloc[0]
    return None, None


def te_sin_valor(d: pd.DataFrame, procedimiento: Any, obra_social: Any, paciente: Any, fecha: Any = None) -> bool:
    """IPS / AVALIAN: la TE de una paciente con aspiración folicular (±45 días) no lleva valor."""
    import vitae.modulos.facturacion as fx

    if d is None or d.empty or not _texto(paciente) or not fx._es_te(procedimiento):
        return False
    os_n = fx._norm(obra_social)
    if not any(os_n == g or g in os_n.split("_") for g in fx.OS_TE_EN_ASPIRACION):
        return False
    clave = " ".join(sorted(t for t in fx._norm(paciente).split("_") if t))
    pac = d["_paciente"].map(lambda p: " ".join(sorted(t for t in fx._norm(p).split("_") if t)))
    asp = d[(pac == clave) & ~d["_cancelado"] & d["_procedimiento"].map(fx._es_aspiracion)]
    if asp.empty:
        return False
    if fecha is None:
        return True
    f0 = pd.Timestamp(fecha)
    return any(pd.isna(x) or abs((x - f0).days) <= fx.DIAS_TE_ASPIRACION for x in asp["_prestacion"])


def importe_sugerido(d: pd.DataFrame, procedimiento: str, obra_social: str, *, empresa: str | None = None,
                     fecha: Any = None, paciente: Any = None) -> float | None:
    """El importe más reciente para ese procedimiento y obra social (o sólo el procedimiento). Si la obra
    social usa los valores de otra (``VALORES_DE``), el de esa otra a ±45 días; si no hay, ninguno. En IPS /
    AVALIAN, la TE de una paciente con aspiración folicular no lleva valor (0)."""
    if te_sin_valor(d, procedimiento, obra_social, paciente, fecha):
        return 0.0
    ref = referencia_valores(empresa, obra_social)
    if ref:
        return importe_referencia(d, procedimiento, ref, fecha)[0]
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


def ayuda_importe(d: pd.DataFrame, procedimiento: str, obra_social: str, *, empresa: str | None = None,
                  fecha: Any = None, paciente: Any = None) -> str | None:
    """El texto de ayuda del importe sugerido."""
    if te_sin_valor(d, procedimiento, obra_social, paciente, fecha):
        return "Sin valor: el importe va en la aspiración folicular de esta paciente."
    ref = referencia_valores(empresa, obra_social)
    if ref:
        v, f = importe_referencia(d, procedimiento, ref, fecha)
        if v is None:
            return f"Sin valor de {ref} para este procedimiento a {DIAS_REFERENCIA} días o menos."
        return f"Valor de {ref}: {pesos(v, 0)} (prestación del {_f(f)})."
    sug = importe_sugerido(d, procedimiento, obra_social)
    return f"Sugerido: {pesos(sug, 0)} (lo último de este procedimiento)." if sug else None


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
    if filtro in ("Para facturar", "Plazo vencido"):
        # La misma definición que 2 · Facturar (control_facturacion.controles → «sin_facturar»)
        sf = d[~d["_cancelado"] & ~d["_facturada"] & ~d["_sin_cargo"] & ~d["_incluida"]]
        return sf if filtro == "Para facturar" else sf[sf["_dias_plazo"] < 0]
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


def pendiente_pago(x: pd.DataFrame) -> pd.Series:
    """Regla de Facturación: lo que todavía no se cobró (sin facturar, facturado con saldo o con débito abierto),
    sin importar lo viejo que sea. Siempre va arriba de lo cobrado y lo anulado."""
    if x.empty:
        return pd.Series(dtype=bool)
    m = (x["_pendiente"] > 0.004) | x["_debito_abierto"]
    if "_facturada" in x.columns:
        m = m | ~x["_facturada"]
        # facturada sin importe: falta completarla (no está cobrada)
        sin_imp = x["_facturada"] & (x["_monto"] <= 0.004)
        if "_incluida" in x.columns:
            sin_imp = sin_imp & ~x["_incluida"]
        m = m | sin_imp
    if "_sin_cargo" in x.columns:
        m = m & ~x["_sin_cargo"]
    if "_incluida" in x.columns:
        m = m & ~x["_incluida"]
    return m & ~x["_cancelado"]


def ordenar(x: pd.DataFrame, orden: str) -> pd.DataFrame:
    """Ordena siempre con lo pendiente de pago primero; dentro de cada parte, por el orden pedido."""
    if x.empty:
        return x
    pp = (~pendiente_pago(x)).astype(int)
    if orden == "Más recientes":
        return x.assign(_pp=pp).sort_values(["_pp", "_prestacion"], ascending=[True, False], na_position="last",
                                            kind="stable").drop(columns="_pp")
    if orden == "Mayor importe":
        return x.assign(_pp=pp).sort_values(["_pp", "_monto"], ascending=[True, False], kind="stable").drop(columns="_pp")
    if orden == "Paciente (A-Z)":
        return x.assign(_pp=pp).sort_values(["_pp", "_paciente"], kind="stable").drop(columns="_pp")
    # Más urgentes: plazo vencido / vencidas / débitos primero, después lo que vence antes
    urg = pd.Series(3, index=x.index)
    urg[(~x["_facturada"]) & (x["_dias_plazo"] < 0)] = 0
    urg[x["_vencido"] | x["_debito_abierto"]] = 1
    urg[(~x["_facturada"]) & x["_dias_plazo"].between(0, cf.AVISO_PRESENTACION)] = 2
    clave = x["_dias_plazo"].where(~x["_facturada"], (x["_vencimiento"] - pd.Timestamp.today()).dt.days)
    return x.assign(_pp=pp, _u=urg, _c=clave.fillna(9999)).sort_values(
        ["_pp", "_u", "_c", "_monto"], ascending=[True, True, True, False], kind="stable").drop(columns=["_pp", "_u", "_c"])


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
    """Registrar el cobro: estado «Completo» (= pagado; regla de la planilla). Si pagaron menos, se anota el
    débito por la diferencia, a reclamar (el débito no cambia lo cobrado: sólo queda para reclamarlo)."""
    c = [{"fila": fila, "campo": "fecha_pago", "valor": _txt_fecha(fecha)},
         {"fila": fila, "campo": "estado", "valor": "Completo"}]
    monto = float(r["_monto"])
    if cobrado is None or cobrado >= monto - 0.5:
        return c
    debito = max(monto - float(cobrado), 0.0)
    c += [{"fila": fila, "campo": "debito", "valor": _txt_imp(debito)},
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
        med = c1.selectbox("Médico (opcional)", _con(opciones(d, "_medico", "SIN MÉDICO"), r["_medico"], "SIN MÉDICO"),
                           key=f"{k}_md", accept_new_options=True,
                           help="Criopreservaciones y mantenimientos no llevan médico: dejalo en «SIN MÉDICO».")
        sug = importe_sugerido(d, proc or "", os_ or "", empresa=ctx.get("empresa"), fecha=prest, paciente=pac)
        imp = c2.number_input("Importe", min_value=0.0, value=float(r["_monto"]) or float(sug or 0), step=1000.0,
                              format="%.2f", key=f"{k}_im",
                              help=ayuda_importe(d, proc or "", os_ or "", empresa=ctx.get("empresa"), fecha=prest,
                                                 paciente=pac))
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
        med = c1.selectbox("Médico (opcional)", _con(md_l, _texto(cir.get("Médico")), ""), key=f"{k}_md",
                           accept_new_options=True, index=0 if cir.get("Médico") else None,
                           placeholder="Ninguno (criopreservación, mantenimiento…)")
        sug = importe_sugerido(d, proc or "", os_ or "", empresa=ctx.get("empresa"), fecha=prest, paciente=pac)
        imp = c2.number_input("Importe", min_value=0.0, value=float(sug or 0), step=1000.0, format="%.2f",
                              key=f"{k}_im_{proc}_{os_}_{prest}",
                              help=ayuda_importe(d, proc or "", os_ or "", empresa=ctx.get("empresa"), fecha=prest,
                                                 paciente=pac))
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


# ---------------------------------------------------------------------------
# Agrupado por obra social y por factura (facturas conjuntas); la planilla está en facturacion_planilla
# ---------------------------------------------------------------------------
def _urgencia(x: pd.DataFrame) -> pd.Series:
    """0 = plazo vencido sin facturar / factura vencida / débito; 1 = por vencer; 2 = resto."""
    u = pd.Series(2, index=x.index)
    sin_f = ~x["_facturada"] & ~x["_sin_cargo"] if "_sin_cargo" in x.columns else ~x["_facturada"]
    u[sin_f & x["_dias_plazo"].between(0, cf.AVISO_PRESENTACION)] = 1
    u[(sin_f & (x["_dias_plazo"] < 0)) | x["_vencido"] | x["_debito_abierto"]] = 0
    return u


def resumen_obras(x: pd.DataFrame) -> pd.DataFrame:
    """Una fila por obra social: cantidad, importe, para facturar, pendiente, vencidas, débitos, facturas conjuntas."""
    if x.empty:
        return pd.DataFrame(columns=["Obra social", "Prestaciones", "Importe", "Para facturar", "Pendiente", "Vencidas",
                                     "Débitos", "Conjuntas", "Urgencia"])
    conj = cf.facturas_conjuntas(x)
    u = _urgencia(x)
    pp = pendiente_pago(x)
    filas = []
    for os_, g in x.groupby("_obra_social"):
        num = g["_numero_factura"].astype(str).str.strip()
        filas.append({"Obra social": os_, "Prestaciones": len(g), "Importe": float(g["_monto"].sum()),
                      "Para facturar": int((~g["_facturada"] & ~g["_cancelado"] & ~g["_sin_cargo"]).sum()),
                      "Pendiente": float(g.loc[g["_facturada"], "_pendiente"].sum()),
                      "Vencidas": int(g["_vencido"].sum()), "Débitos": float(g.loc[g["_debito_abierto"], "_debito"].sum()),
                      "Conjuntas": int(num[num.isin(conj.index)].nunique()), "Urgencia": int(u[g.index].min()),
                      "_pp": 0 if bool(pp[g.index].any()) else 1})
    return pd.DataFrame(filas).sort_values(["_pp", "Urgencia", "Importe"], ascending=[True, True, False],
                                           kind="stable").drop(columns="_pp").reset_index(drop=True)


def grupos_factura(x: pd.DataFrame) -> list[tuple[str, list[int]]]:
    """[(N° de factura, filas)]: primero lo pendiente de pago (por urgencia), después lo cobrado y lo anulado.
    Las filas sin N° van con clave ''."""
    if x.empty:
        return []
    num = x["_numero_factura"].astype(str).str.strip()
    u = _urgencia(x)
    pp = pendiente_pago(x)
    grupos = []
    for n_, g in x[num != ""].groupby(num[num != ""]):
        grupos.append((n_, list(g.index), 0 if bool(pp[g.index].any()) else 1, int(u[g.index].min()),
                       float(g["_monto"].sum())))
    sin = x[num == ""]
    if len(sin):
        # Sin N°: lo pendiente (falta facturar) entra con lo pendiente; lo anulado va al final.
        vivos, resto = sin[pp[sin.index]], sin[~pp[sin.index]]
        if len(vivos):
            grupos.append(("", list(vivos.index), 0, int(u[vivos.index].min()), float(vivos["_monto"].sum())))
        sin_cargo = resto[~resto["_cancelado"]]  # donantes, particulares y S/C sin importe
        if len(sin_cargo):
            grupos.append(("", list(sin_cargo.index), 1, 9, 0.0))
        anulados = resto[resto["_cancelado"]]
        if len(anulados):
            grupos.append(("", list(anulados.index), 2, 9, 0.0))
    grupos.sort(key=lambda t: (t[2], t[3], -t[4]))
    return [(n_, idx) for n_, idx, _, _, _ in grupos]


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


