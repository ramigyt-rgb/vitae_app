"""Presentaciones por obra social (facturar de a varias) y documentación de cada ficha.

Presentación (pestaña ``facturacion_presentaciones``)
    Un lote de prestaciones de una misma obra social que se factura junto: se eligen las fichas (por
    defecto, las que tienen la documentación completa), el período, si va una sola factura para todo el
    lote o una por paciente (números correlativos), N° (el siguiente de la serie), fecha y vencimiento.
    Cada ficha queda facturada y con el código de la presentación (columna ``presentacion``). Después se
    sigue el lote entero: cuánto se cobró, si está vencido, el detalle en Excel y «registrar cobro del
    lote».

Documentación (columna ``documentacion`` de la planilla, p. ej. «Autorización | Orden médica»)
    Lo que pide cada obra social (pestaña ``facturacion_requisitos``; si una obra social no tiene nada
    cargado, se usa la lista de «Todas»). Una ficha está «lista para presentar» cuando tiene todo.
"""
from __future__ import annotations

import io
import re
import uuid
from datetime import date, datetime
from typing import Any

import pandas as pd

from vitae.modulos.pagos_pendientes import _sin_acentos, _texto, pesos

TABLA_PRES = "facturacion_presentaciones"
TABLA_REQ = "facturacion_requisitos"
COLS_PRES = ["id", "empresa", "obra_social", "periodo", "fecha", "modo", "numero", "vencimiento", "cantidad",
             "importe", "creado_por", "observaciones"]
COLS_REQ = ["empresa", "obra_social", "documentos"]
TODAS = "Todas"
DOCS_DEFECTO = ["Autorización", "Orden médica", "Consentimiento"]
UNA, CORRELATIVAS = "Una factura para todo el lote", "Una factura por paciente (números correlativos)"
SEP = " | "


# ---------------------------------------------------------------------------
# Documentación
# ---------------------------------------------------------------------------
def lista_docs(texto: Any) -> list[str]:
    return [x.strip() for x in re.split(r"\s*[|;,\n]\s*", _texto(texto)) if x.strip()]


def normalizar_req(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=COLS_REQ)
    out = df.copy()
    for c in COLS_REQ:
        if c not in out.columns:
            out[c] = ""
    out = out[COLS_REQ].astype(str).replace({"nan": ""})
    out["empresa"] = out["empresa"].str.upper()
    return out[out["obra_social"].str.strip() != ""].reset_index(drop=True)


def requisitos(req: pd.DataFrame | None, empresa: str, obra_social: str) -> list[str]:
    """Lo que pide la obra social; si no tiene nada cargado, lo de «Todas»; si tampoco, la lista de siempre."""
    r = normalizar_req(req)
    r = r[r["empresa"].isin([str(empresa).upper(), ""])]
    os_n = _sin_acentos(obra_social)
    propia = r[r["obra_social"].map(_sin_acentos) == os_n]
    if len(propia):
        return lista_docs(propia.iloc[-1]["documentos"])
    todas = r[r["obra_social"].map(_sin_acentos) == _sin_acentos(TODAS)]
    if len(todas):
        return lista_docs(todas.iloc[-1]["documentos"])
    return list(DOCS_DEFECTO)


def faltan(docs: Any, pedidos: list[str]) -> list[str]:
    tiene = {_sin_acentos(x) for x in (docs if isinstance(docs, list) else lista_docs(docs))}
    return [p for p in pedidos if _sin_acentos(p) not in tiene]


def nota_docs(r: pd.Series, req: pd.DataFrame | None, empresa: str) -> str:
    pedidos = requisitos(req, empresa, r["_obra_social"])
    if not pedidos:
        return ""
    f = faltan(r.get("_documentacion", ""), pedidos)
    return "Documentación completa ✓" if not f else "Falta documentación: " + ", ".join(f)


def tabla_req(req: pd.DataFrame | None, empresa: str, obras: list[str]) -> pd.DataFrame:
    """Para editar: una fila por obra social (con «Todas» arriba)."""
    r = normalizar_req(req)
    r = r[r["empresa"].isin([str(empresa).upper(), ""])]
    filas = [{"Obra social": TODAS, "Documentos": SEP.join(requisitos(req, empresa, TODAS))}]
    vistas = {TODAS}
    for os_ in list(r["obra_social"]) + obras:
        if os_ in vistas or not os_ or os_ == "SIN OBRA SOCIAL":
            continue
        vistas.add(os_)
        propia = r[r["obra_social"] == os_]
        filas.append({"Obra social": os_, "Documentos": propia.iloc[-1]["documentos"] if len(propia) else ""})
    return pd.DataFrame(filas)


def guardar_req(req: pd.DataFrame | None, empresa: str, editada: pd.DataFrame) -> pd.DataFrame:
    """Lo editado → la pestaña (las filas vacías de cada obra social usan «Todas»)."""
    r = normalizar_req(req)
    r = r[r["empresa"] != str(empresa).upper()]
    nuevas = [{"empresa": str(empresa).upper(), "obra_social": _texto(f["Obra social"]),
               "documentos": SEP.join(lista_docs(f["Documentos"]))}
              for _, f in editada.iterrows() if _texto(f.get("Obra social")) and _texto(f.get("Documentos"))]
    return pd.concat([r, pd.DataFrame(nuevas, columns=COLS_REQ)], ignore_index=True)


# ---------------------------------------------------------------------------
# Para presentar
# ---------------------------------------------------------------------------
def sin_facturar(d: pd.DataFrame) -> pd.DataFrame:
    if d.empty:
        return d
    # La misma definición que 2 · Facturar y Registros («sin_facturar» del control).
    return d[~d["_cancelado"] & ~d["_facturada"] & ~d["_sin_cargo"] & ~d["_incluida"]]


def por_obra_social(d: pd.DataFrame, req: pd.DataFrame | None, empresa: str) -> pd.DataFrame:
    """Lo que falta facturar, por obra social: cantidad, importe, cuántas tienen la documentación y el plazo más
    cercano."""
    x = sin_facturar(d)
    if x.empty:
        return pd.DataFrame(columns=["Obra social", "Prestaciones", "Importe", "Listas", "Plazo", "Desde", "Hasta"])
    filas = []
    for os_, g in x.groupby("_obra_social"):
        pedidos = requisitos(req, empresa, os_)
        listas = sum(1 for _, r in g.iterrows() if not faltan(r["_documentacion"], pedidos))
        filas.append({"Obra social": os_, "Prestaciones": len(g), "Importe": float(g["_monto"].sum()), "Listas": listas,
                      "Plazo": int(g["_dias_plazo"].min()) if g["_dias_plazo"].notna().any() else None,
                      "Desde": g["_prestacion"].min(), "Hasta": g["_prestacion"].max()})
    t = pd.DataFrame(filas)
    return t.assign(_p=t["Plazo"].fillna(9999)).sort_values(["_p", "Importe"], ascending=[True, False]) \
        .drop(columns="_p").reset_index(drop=True)


def periodo_sugerido(x: pd.DataFrame) -> str:
    from vitae.modulos.cierre_facturacion import mes_txt

    f = x["_prestacion"].dropna()
    if f.empty:
        return ""
    a, b = f.min(), f.max()
    ma, mb = f"{a.year:04d}-{a.month:02d}", f"{b.year:04d}-{b.month:02d}"
    return mes_txt(ma) if ma == mb else f"{mes_txt(ma)} a {mes_txt(mb)}"


def correlativos(numero: str, n: int) -> list[str]:
    """«0001-00001235», 3 → 1235, 1236, 1237 con el mismo formato."""
    m = re.fullmatch(r"(.*?)(\d+)(\D*)", _texto(numero))
    if not m:
        return [_texto(numero)] * n
    pre, num, suf = m.groups()
    return [f"{pre}{str(int(num) + i).zfill(len(num))}{suf}" for i in range(n)]


def _txt_fecha(v: Any) -> str:
    t = pd.Timestamp(v) if v is not None else pd.NaT
    return "" if pd.isna(t) else t.strftime("%d/%m/%Y")


def normalizar_pres(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=COLS_PRES)
    out = df.copy()
    for c in COLS_PRES:
        if c not in out.columns:
            out[c] = ""
    out = out[COLS_PRES].astype(str).replace({"nan": ""})
    out["empresa"] = out["empresa"].str.upper()
    return out[out["id"].str.strip() != ""].reset_index(drop=True)


def armar(ctx: dict[str, Any], filas: list[int], *, obra_social: str, periodo: str, modo: str, numero: str,
          fecha: Any, vencimiento: Any, pres: pd.DataFrame | None, usuario: str = "",
          observaciones: str = "") -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Factura las fichas elegidas como una presentación. Devuelve (planilla, presentaciones, id)."""
    import vitae.modulos.control_facturacion as cf

    d = ctx["d"]
    filas = [int(f) for f in filas if int(f) in d.index]
    if not filas:
        raise ValueError("Elegí al menos una prestación.")
    if not _texto(numero):
        raise ValueError("Poné el número de factura.")
    f = pd.Timestamp(fecha) if fecha is not None else pd.Timestamp(ctx["hoy"])
    pid = f"{ctx['empresa']}-{f:%Y%m%d}-{uuid.uuid4().hex[:4].upper()}"
    nums = [numero] * len(filas) if modo == UNA else correlativos(numero, len(filas))
    orden = d.loc[filas].sort_values(["_prestacion", "_paciente"], na_position="last").index
    cambios = []
    for fila, num in zip(orden, nums):
        cambios += [{"fila": int(fila), "campo": "numero_factura", "valor": num},
                    {"fila": int(fila), "campo": "fecha_factura", "valor": _txt_fecha(f)},
                    {"fila": int(fila), "campo": "presentacion", "valor": pid}]
        if vencimiento is not None:
            cambios.append({"fila": int(fila), "campo": "vencimiento", "valor": _txt_fecha(vencimiento)})
    raw = cf.aplicar(ctx["raw"], ctx["cols"], cambios, ctx["hoy"])
    p = normalizar_pres(pres)
    fila_p = {"id": pid, "empresa": ctx["empresa"], "obra_social": obra_social, "periodo": periodo,
              "fecha": _txt_fecha(f), "modo": "una" if modo == UNA else "correlativas",
              "numero": nums[0] if modo == UNA else f"{nums[0]} a {nums[-1]}" if len(nums) > 1 else nums[0],
              "vencimiento": _txt_fecha(vencimiento), "cantidad": str(len(filas)),
              "importe": f"{float(d.loc[filas, '_monto'].sum()):.2f}", "creado_por": usuario,
              "observaciones": observaciones}
    p = pd.concat([p, pd.DataFrame([fila_p])], ignore_index=True)
    return raw, p, pid


def presentaciones(pres: pd.DataFrame | None, d: pd.DataFrame, empresa: str) -> pd.DataFrame:
    """Las presentaciones de la empresa con lo que está pasando ahora con sus fichas."""
    p = normalizar_pres(pres)
    p = p[p["empresa"] == str(empresa).upper()].copy()
    if p.empty:
        return p.assign(fichas=0, facturado=0.0, cobrado=0.0, pendiente=0.0, vencido=False, estado="")
    filas = []
    for _, r in p.iterrows():
        g = d[(d["_presentacion"] == r["id"]) & ~d["_cancelado"]] if len(d) else d
        fact, cob, pend = float(g["_monto"].sum()), float(g["_cobrado"].sum()), float(g["_pendiente"].sum())
        venc = bool(g["_vencido"].any()) if len(g) else False
        estado = "Cobrada" if len(g) and pend <= 0.5 else ("Cobrada en parte" if cob > 0.5 else "Presentada")
        if venc and estado != "Cobrada":
            estado += " · vencida"
        filas.append(r.to_dict() | {"fichas": len(g), "facturado": fact, "cobrado": cob, "pendiente": pend,
                                    "vencido": venc, "estado": estado})
    t = pd.DataFrame(filas)
    t["_f"] = pd.to_datetime(t["fecha"], dayfirst=True, errors="coerce")
    t["_pp"] = (t["pendiente"] <= 0.5).astype(int)  # lo que falta cobrar, arriba
    return t.sort_values(["_pp", "_f"], ascending=[True, False], kind="stable").drop(columns=["_f", "_pp"]) \
        .reset_index(drop=True)


def cambios_cobro_lote(d: pd.DataFrame, pid: str, fecha: Any) -> list[dict[str, Any]]:
    g = d[(d["_presentacion"] == pid) & ~d["_cancelado"] & (d["_pendiente"] > 0.004)]
    out = []
    for fila in g.index:
        out += [{"fila": int(fila), "campo": "fecha_pago", "valor": _txt_fecha(fecha)},
                {"fila": int(fila), "campo": "estado", "valor": "Completo"}]
    return out


def excel_lote(d: pd.DataFrame, info: dict[str, Any]) -> bytes:
    """El detalle de la presentación, para adjuntar o controlar."""
    g = d[d["_presentacion"] == info["id"]].sort_values(["_prestacion", "_paciente"], na_position="last")
    t = pd.DataFrame({"Paciente": g["_paciente"], "Prestación": g["_prestacion"].dt.strftime("%d/%m/%Y"),
                      "Procedimiento": g["_procedimiento"], "Médico": g["_medico"], "N° factura": g["_numero_factura"],
                      "Documentación": g["_documentacion"], "Importe": g["_monto"].round(2),
                      "Cobrado": g["_cobrado"].round(2), "Pendiente": g["_pendiente"].round(2)})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as w:
        cab = pd.DataFrame({"": ["Obra social", "Período", "Fecha", "N° de factura", "Vencimiento", "Prestaciones",
                                 "Importe"],
                            " ": [info.get("obra_social", ""), info.get("periodo", ""), info.get("fecha", ""),
                                  info.get("numero", ""), info.get("vencimiento", ""), len(t),
                                  round(float(t["Importe"].sum()), 2)]})
        cab.to_excel(w, sheet_name="Presentación", index=False)
        t.to_excel(w, sheet_name="Detalle", index=False)
    return buf.getvalue()


def plazo_txt(dias: Any) -> str:
    if dias is None or pd.isna(dias):
        return "sin fecha"
    d = int(dias)
    return f"plazo vencido hace {-d} días" if d < 0 else f"quedan {d} días"


def fecha_corta(f: Any) -> str:
    return "" if f is None or pd.isna(f) else pd.Timestamp(f).strftime("%d/%m")


def ahora() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")


__all__ = ["armar", "presentaciones", "por_obra_social", "requisitos", "faltan", "nota_docs", "pesos"]
