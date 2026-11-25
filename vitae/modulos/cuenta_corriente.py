"""Cuenta corriente de proveedores (VMR; VM con el mismo modelo).

Es la planilla «PROVEEDORES DE VITAE MEDICINA REPRODUCTIVA» tal cual se usa:

- Arriba, «Proveedores a pagar»: el saldo de cada proveedor en pesos y, aparte,
  los proveedores en dólares (nunca se suman pesos con dólares).
- Abajo, el detalle de facturas: FECHA · PROVEEDOR · COMPROBANTE · MONTO ·
  PAGADO · SALDO · OBSERVACIÓN (en la observación suele ir la fecha de pago).

Como en el Excel, las filas se pueden ocultar (por ejemplo, las facturas ya
pagadas de meses anteriores): la planilla muestra sólo las visibles y las
ocultas quedan guardadas como historial. Al importar el Excel se toman sólo
las filas y columnas visibles; si una fila se ocultó en el Excel, se oculta
también en la app.

Se carga mes a mes (factura nueva, registrar pagos) y se sincroniza con el
Excel borrador: las facturas se reconocen por proveedor + número de
comprobante + moneda, así que volver a subir el mismo Excel no duplica nada;
sólo agrega las nuevas y actualiza pagos y observaciones.

Los saldos de este módulo son la única fuente para el Panel general y Luz.
"""
from __future__ import annotations

import hashlib
import html
import io
import re
from datetime import date, datetime
from typing import Any, Callable

import pandas as pd
import streamlit as st

from vitae.modulos.pagos_pendientes import _sin_acentos, _texto, nombre_mes, numero, pesos

try:
    from vitae.core.ui import tarjetas_kpi
except Exception:  # pragma: no cover
    def tarjetas_kpi(tarjetas):
        for col, (t, v, p, _) in zip(st.columns(len(tarjetas)), tarjetas):
            col.metric(t, v, p)


EMPRESA = {
    "cuenta_corriente_vmr": "VITAE MEDICINA REPRODUCTIVA",
    "cuenta_corriente_vm": "VITAE MEDICAL",
}
MONEDAS = ["ARS", "USD"]
TIPOS = ["A pagar", "A cobrar"]
COLUMNAS = ["id", "fecha", "proveedor", "comprobante", "moneda", "monto", "pagado", "saldo",
            "fecha_pago", "vencimiento", "tipo", "observacion", "oculta"]
# Nombres de la versión anterior del módulo: se leen y se pasan al formato nuevo.
_ANTERIORES = {"persona_entidad", "entidad", "acreedor", "cliente", "concepto", "factura", "numero_factura",
               "nro_factura", "documento", "importe", "importe_ars", "importe_usd", "pagado_ars", "pagado_usd",
               "saldo_ars", "saldo_usd", "estado", "observaciones", "id_registro", "fecha_factura"}
PANTALLAS = ["Planilla", "Cargar", "Pagar", "Editar", "Importar"]

_MESES_CORTOS = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9,
                 "set": 9, "oct": 10, "nov": 11, "dic": 12}


# ---------------------------------------------------------------------------
# Lectura de valores
# ---------------------------------------------------------------------------
def usd(valor: Any, decimales: int = 2) -> str:
    return "US" + pesos(valor, decimales)


def importe(valor: Any, moneda: str, decimales: int = 2) -> str:
    return usd(valor, decimales) if moneda == "USD" else pesos(valor, decimales)


def una_fecha(valor: Any, anio: int | None = None) -> pd.Timestamp:
    """31/3/2026, 2026-03-31, 8-oct, fechas de Excel… → Timestamp (o NaT)."""
    if valor is None:
        return pd.NaT
    try:
        if pd.isna(valor):
            return pd.NaT
    except (TypeError, ValueError):
        pass
    if isinstance(valor, pd.Timestamp):
        return valor.normalize() if pd.notna(valor) else pd.NaT
    if isinstance(valor, (datetime, date)):
        return pd.Timestamp(valor).normalize()
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        if pd.isna(valor) or not 30000 < float(valor) < 80000:
            return pd.NaT
        return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(valor))).normalize()
    t = str(valor).strip().lower()
    if not t or t in {"nan", "nat", "none", "-"}:
        return pd.NaT
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ t].*)?", t)
    if m:
        return pd.Timestamp(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", t)
    if m:
        a = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        dia, mes = int(m[1]), int(m[2])
        if mes > 12 and dia <= 12:  # vino como mes/día/año
            dia, mes = mes, dia
        try:
            return pd.Timestamp(a, mes, dia)
        except ValueError:
            return pd.NaT
    m = re.fullmatch(r"(\d{1,2})[\s/.-]*([a-z]{3})[a-z]*\.?(?:[\s/.-]*(\d{2,4}))?", _sin_acentos(t))
    if m and m[2] in _MESES_CORTOS:
        a = int(m[3]) + (2000 if m[3] and len(m[3]) == 2 else 0) if m[3] else (anio or date.today().year)
        try:
            return pd.Timestamp(a, _MESES_CORTOS[m[2]], int(m[1]))
        except ValueError:
            return pd.NaT
    return pd.NaT


def _fechas(serie: pd.Series) -> pd.Series:
    return pd.Series([una_fecha(v) for v in serie], index=serie.index, dtype="datetime64[ns]")


_ERRORES_EXCEL = ("#ref", "#value", "#valor", "#n/a", "#div/0", "#name", "#nombre", "#num", "#null", "#nulo", "#error",
                  "#spill", "#calc", "#¡")


def es_error(v: Any) -> bool:
    return isinstance(v, str) and v.strip().lower().startswith(_ERRORES_EXCEL)


def importe_celda(v: Any) -> float | None:
    """Importe de una celda: número, «$ 1.150.795,69», «1,150,795.69», «$ -» (= 0).
    Los errores de Excel/Sheets (#REF!, #VALUE!…) no son importes."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        t = v.strip()
        if not t or es_error(t):
            return None
        if re.fullmatch(r"\(?\s*(us)?\$?\s*-+\s*\)?", t.lower()):
            return 0.0
        limpio = re.sub(r"[^\d.,()-]", "", t)
        if re.fullmatch(r"\(?-?\d{1,3}(,\d{3})+\)?", limpio):  # 1,150,795 (miles con coma, sin decimales)
            return numero(limpio.replace(",", ""))
    return numero(v)


def _monto(serie: pd.Series) -> pd.Series:
    return pd.Series([importe_celda(v) or 0.0 for v in serie], index=serie.index, dtype="float64")


def _moneda(valor: Any) -> str:
    t = _sin_acentos(valor).replace(" ", "")
    return "USD" if any(x in t for x in ("usd", "u$s", "us$", "dolar", "u$d")) else "ARS"


def clave(proveedor: Any, comprobante: Any, moneda: Any) -> str:
    """Identifica una factura: «FA-0005- 10493» y «FA-0005-00010493» son la misma."""
    prov = re.sub(r"[^a-z0-9]", "", _sin_acentos(proveedor))
    grupos = re.findall(r"\d+", str(comprobante or ""))
    comp = "-".join(str(int(g)) for g in grupos) if grupos else re.sub(r"[^a-z0-9]", "", _sin_acentos(comprobante))
    return f"{prov}|{comp}|{_moneda(moneda)}"


def _id_fijo(base: str, n: int = 0) -> str:
    return "cc-" + hashlib.sha1(f"{base}#{n}".encode("utf-8")).hexdigest()[:12]


def _norm_col(nombre: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _sin_acentos(nombre)).strip("_")


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------
def _vacio() -> pd.DataFrame:
    d = pd.DataFrame({c: pd.Series(dtype="object") for c in COLUMNAS})
    for c in ("monto", "pagado", "saldo"):
        d[c] = d[c].astype("float64")
    for c in ("_fecha", "_pago", "_venc"):
        d[c] = pd.Series(dtype="datetime64[ns]")
    d["_clave"] = pd.Series(dtype="object")
    d["_mes"] = pd.Series(dtype="object")
    return d


def normalizar(df: pd.DataFrame | None) -> pd.DataFrame:
    """Planilla del Sheet (formato nuevo o el anterior) → modelo de trabajo."""
    if df is None or len(df) == 0:
        return _vacio()
    raw = df.copy()
    raw.columns = [str(c) for c in raw.columns]
    cols = {_norm_col(c): c for c in raw.columns}

    def col(*nombres: str) -> pd.Series:
        for n in nombres:
            if n in cols:
                return raw[cols[n]]
        return pd.Series([""] * len(raw), index=raw.index, dtype="object")

    def hay(*nombres: str) -> bool:
        return any(n in cols for n in nombres)

    d = pd.DataFrame(index=raw.index)
    d["proveedor"] = col("proveedor", "persona_entidad", "entidad", "acreedor", "cliente").map(_texto).str.strip()
    d["comprobante"] = col("comprobante", "factura", "numero_factura", "nro_factura", "documento", "concepto").map(_texto).str.strip()
    monto_ars = _monto(col("monto", "importe", "importe_ars", "total"))
    pagado_ars = _monto(col("pagado", "pagado_ars", "pago"))
    monto_usd = _monto(col("importe_usd")) if hay("importe_usd") else pd.Series(0.0, index=raw.index)
    pagado_usd = _monto(col("pagado_usd")) if hay("pagado_usd") else pd.Series(0.0, index=raw.index)
    if hay("moneda"):
        moneda = col("moneda").map(lambda v: _moneda(v) if _texto(v).strip() else "")
    else:
        moneda = pd.Series([""] * len(raw), index=raw.index)
    moneda = moneda.where(moneda.ne(""), pd.Series(["USD" if u > 0 and a == 0 else "ARS"
                                                    for u, a in zip(monto_usd, monto_ars)], index=raw.index))
    d["moneda"] = moneda
    es_usd = moneda.eq("USD")
    d["monto"] = monto_ars.where(~(es_usd & monto_ars.eq(0)), monto_usd).round(2)
    d["pagado"] = pagado_ars.where(~(es_usd & pagado_ars.eq(0)), pagado_usd).round(2)
    estado = col("estado").map(_sin_acentos)
    pagada = estado.str.startswith("pagad") & d["pagado"].eq(0)
    d.loc[pagada, "pagado"] = d.loc[pagada, "monto"]
    d["saldo"] = (d["monto"] - d["pagado"]).clip(lower=0).round(2)
    d["_fecha"] = _fechas(col("fecha", "fecha_factura", "mes"))
    d["_pago"] = _fechas(col("fecha_pago"))
    obs_crudas = col("observacion", "observaciones", "nota", "notas")
    obs_fecha = pd.Series([una_fecha(v, f.year if pd.notna(f) else None) if re.fullmatch(
        r"\s*\d{1,2}[/.-]\d{1,2}([/.-]\d{2,4})?\s*|\s*\d{4}-\d{2}-\d{2}.*|\s*\d{1,2}[\s/.-]*[a-zA-Z]{3,}\.?\s*",
        _texto(v)) else pd.NaT for v, f in zip(obs_crudas, d["_fecha"])], index=raw.index, dtype="datetime64[ns]")
    mover = d["_pago"].isna() & obs_fecha.notna()
    d.loc[mover, "_pago"] = obs_fecha[mover]
    d["_venc"] = _fechas(col("vencimiento", "fecha_vencimiento"))
    tipo = col("tipo").map(_sin_acentos)
    d["tipo"] = ["A cobrar" if "cobrar" in t else "A pagar" for t in tipo]
    d["observacion"] = obs_crudas.map(_texto).str.strip().where(~mover, "")
    d["oculta"] = col("oculta", "oculto", "archivada").map(
        lambda v: _sin_acentos(v) in {"si", "true", "1", "x", "oculta", "oculto", "verdadero"}).astype(bool)
    d = d[(d["proveedor"].ne("")) | (d["comprobante"].ne("")) | (d["monto"].abs() > 0.004)].copy()
    d["_clave"] = [clave(p, c, m) for p, c, m in zip(d["proveedor"], d["comprobante"], d["moneda"])]
    ids = col("id", "id_registro").loc[d.index].map(_texto).str.strip()
    vez = d.groupby("_clave").cumcount()
    d["id"] = [i if i else _id_fijo(k, n) for i, k, n in zip(ids, d["_clave"], vez)]
    d["fecha"] = d["_fecha"].dt.strftime("%Y-%m-%d").fillna("")
    d["fecha_pago"] = d["_pago"].dt.strftime("%Y-%m-%d").fillna("")
    d["vencimiento"] = d["_venc"].dt.strftime("%Y-%m-%d").fillna("")
    d["_mes"] = d["_fecha"].dt.strftime("%Y-%m").fillna("")
    extras = [c for c in raw.columns if _norm_col(c) not in set(COLUMNAS) | _ANTERIORES
              and _norm_col(c) not in {"mes", "fecha_vencimiento", "nota", "notas", "pago", "total"}]
    for c in extras:
        d[c] = raw.loc[d.index, c]
    d = d.sort_values(["moneda", "_fecha"], kind="stable", na_position="last")
    return d.reset_index(drop=True)[COLUMNAS + ["_fecha", "_pago", "_venc", "_clave", "_mes"] + extras]


def para_guardar(d: pd.DataFrame) -> pd.DataFrame:
    """Modelo → filas para Google Sheets (texto, montos con punto decimal)."""
    d = normalizar(d) if "_clave" not in d.columns else d.copy()
    salida = pd.DataFrame(index=d.index)
    for c in COLUMNAS:
        salida[c] = d[c] if c in d.columns else ""
    for c in ("monto", "pagado"):
        salida[c] = d[c].astype(float).round(2)
    salida["saldo"] = (salida["monto"] - salida["pagado"]).clip(lower=0).round(2)
    for c in ("monto", "pagado", "saldo"):
        salida[c] = salida[c].map(lambda v: f"{v:.2f}")
    salida["oculta"] = d["oculta"].map(lambda b: "SI" if bool(b) else "") if "oculta" in d.columns else ""
    salida["fecha"] = d["_fecha"].dt.strftime("%Y-%m-%d").fillna("") if "_fecha" in d.columns else d["fecha"]
    salida["fecha_pago"] = d["_pago"].dt.strftime("%Y-%m-%d").fillna("") if "_pago" in d.columns else d["fecha_pago"]
    salida["vencimiento"] = d["_venc"].dt.strftime("%Y-%m-%d").fillna("") if "_venc" in d.columns else d["vencimiento"]
    extras = [c for c in d.columns if c not in COLUMNAS and not str(c).startswith("_")]
    for c in extras:
        salida[c] = d[c].map(_texto)
    return salida.fillna("").astype(str).reset_index(drop=True)


def fila(fecha: Any, proveedor: str, comprobante: str, moneda: str, monto: float, pagado: float = 0.0,
         fecha_pago: Any = None, observacion: str = "", tipo: str = "A pagar", oculta: bool = False) -> dict[str, Any]:
    f, fp = una_fecha(fecha), una_fecha(fecha_pago)
    return {"fecha": f.strftime("%Y-%m-%d") if pd.notna(f) else "", "proveedor": proveedor.strip(),
            "comprobante": comprobante.strip(), "moneda": moneda, "monto": round(float(monto), 2),
            "pagado": round(float(pagado), 2), "fecha_pago": fp.strftime("%Y-%m-%d") if pd.notna(fp) else "",
            "tipo": tipo, "observacion": observacion.strip(), "oculta": "SI" if oculta else ""}


def agregar(d: pd.DataFrame, filas: list[dict[str, Any]]) -> pd.DataFrame:
    base = para_guardar(d) if len(d) else pd.DataFrame(columns=COLUMNAS)
    return normalizar(pd.concat([base, pd.DataFrame(filas)], ignore_index=True).fillna(""))


# ---------------------------------------------------------------------------
# Cálculos
# ---------------------------------------------------------------------------
def a_pagar(d: pd.DataFrame) -> pd.DataFrame:
    return d[d["tipo"].ne("A cobrar")]


def resumen_proveedores(d: pd.DataFrame, moneda: str) -> pd.DataFrame:
    """«Proveedores a pagar»: saldo por proveedor (también los que están en cero)."""
    x = a_pagar(d)
    x = x[x["moneda"].eq(moneda) & x["proveedor"].ne("")]
    if x.empty:
        return pd.DataFrame(columns=["proveedor", "saldo", "pendientes"])
    g = x.groupby("proveedor", sort=False).agg(saldo=("saldo", "sum"), pendientes=("saldo", lambda s: int((s > 0.004).sum())))
    return g.reset_index().sort_values(["saldo", "proveedor"], ascending=[False, True], kind="stable").reset_index(drop=True)


def totales(d: pd.DataFrame, hoy: pd.Timestamp | None = None) -> dict[str, Any]:
    hoy = (hoy or pd.Timestamp.today()).normalize()
    p = a_pagar(d)
    pend = p[p["saldo"] > 0.004]
    cobrar = d[d["tipo"].eq("A cobrar") & (d["saldo"] > 0.004)]
    mes = hoy.strftime("%Y-%m")
    vencido = pend[pend["_venc"].notna() & (pend["_venc"] < hoy)]
    viejas = pend[pend["_fecha"].notna()].sort_values("_fecha")
    del_mes = p[p["_mes"].eq(mes)]
    pagos_mes = p[p["_pago"].notna() & p["_pago"].dt.strftime("%Y-%m").eq(mes)]
    out: dict[str, Any] = {"mes": mes}
    for m in MONEDAS:
        out[m] = float(pend.loc[pend["moneda"].eq(m), "saldo"].sum())
        out[f"{m}_n"] = int(pend["moneda"].eq(m).sum())
        out[f"{m}_cobrar"] = float(cobrar.loc[cobrar["moneda"].eq(m), "saldo"].sum())
        out[f"{m}_vencido"] = float(vencido.loc[vencido["moneda"].eq(m), "saldo"].sum())
        out[f"{m}_mes"] = float(del_mes.loc[del_mes["moneda"].eq(m), "monto"].sum())
        out[f"{m}_mes_n"] = int(del_mes["moneda"].eq(m).sum())
        out[f"{m}_pagado_mes"] = float(pagos_mes.loc[pagos_mes["moneda"].eq(m), "pagado"].sum())
    out["mas_vieja"] = viejas.iloc[0].to_dict() if len(viejas) else None
    return out


def visibles(d: pd.DataFrame) -> pd.DataFrame:
    return d[~d["oculta"].astype(bool)]


def ocultar_pagadas(d: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Oculta las facturas saldadas (como se hace en el Excel a fin de mes)."""
    sel = (~d["oculta"].astype(bool)) & (d["saldo"] <= 0.004) & (d["monto"] > 0.004)
    x = d.copy()
    x.loc[sel, "oculta"] = True
    return x, int(sel.sum())


def filtrar(d: pd.DataFrame, *, mes: str = "", proveedor: str = "", estado: str = "Todas", buscar: str = "",
            con_ocultas: bool = True) -> pd.DataFrame:
    x = d if con_ocultas else visibles(d)
    if mes:
        x = x[x["_mes"].eq(mes)]
    if proveedor:
        x = x[x["proveedor"].eq(proveedor)]
    if estado == "Con saldo":
        x = x[x["saldo"] > 0.004]
    elif estado == "Pagadas":
        x = x[x["saldo"] <= 0.004]
    t = _sin_acentos(buscar)
    if t:
        texto = (x["proveedor"] + " " + x["comprobante"] + " " + x["observacion"]).map(_sin_acentos)
        x = x[texto.str.contains(re.escape(t), na=False)]
    return x


# ---------------------------------------------------------------------------
# Planilla de noviembre 2026 (fotos de la planilla de VMR)
# ---------------------------------------------------------------------------
_PLANILLA_VMR = [
    # fecha, proveedor, comprobante, monto, pagado, observación (fecha de pago u otra)
    ("ARS", "31/3/2026", "AIRLIQUIDE", "255-7598", 169983.76, 169983.76, "15/10/2026"),
    ("ARS", "31/3/2026", "AIRLIQUIDE", "255-7611", 1150795.69, 1150795.69, "15/10/2026"),
    ("ARS", "30/4/2026", "AIRLIQUIDE", "255-7687", 175763.22, 175763.22, "15/10/2026"),
    ("ARS", "29/5/2026", "AIRLIQUIDE", "255-7778", 180333.06, 180333.06, "15/10/2026"),
    ("ARS", "31/5/2026", "AIRLIQUIDE", "255-7790", 371818.42, 371818.42, "15/10/2026"),
    ("ARS", "25/6/2026", "EUROFARMA", "0008A00017932", 6645561.30, 6645561.30, ""),
    ("ARS", "25/6/2026", "EUROFARMA", "0008A00017931", 1792381.22, 1792381.22, "15/10/2026"),
    ("ARS", "28/6/2026", "EUROFARMA", "0008A00017948", 2215968.52, 2215968.52, "15/10/2026"),
    ("ARS", "30/6/2026", "AIRLIQUIDE", "255-7871", 184120.05, 184120.05, "15/10/2026"),
    ("ARS", "30/6/2026", "MERCK", "FAC A 0096-00137712", 9351741.27, 9351741.27, "13/11/2026"),
    ("ARS", "31/7/2026", "AIRLIQUIDE", "255-7988", 187618.31, 187618.31, "15/10/2026"),
    ("ARS", "31/8/2026", "AIRLIQUIDE", "255-8088", 191558.32, 191558.32, "15/10/2026"),
    ("ARS", "31/8/2026", "AIRLIQUIDE", "255-8098", 2289528.93, 0.0, ""),
    ("ARS", "28/9/2026", "CAMPO NITRO", "FAC A 5-18742", 1314096.30, 0.0, ""),
    ("ARS", "30/9/2026", "AIRLIQUIDE", "255-8189", 194814.80, 0.0, ""),
    ("ARS", "5/10/2026", "FEDERALMED", "FAC A 7-767", 437899.00, 437899.00, ""),
    ("ARS", "6/10/2026", "EUROFARMA", "FAC A 0008-18577", 3018338.03, 0.0, ""),
    ("ARS", "6/10/2026", "EUROFARMA", "FAC A 0008-18578", 3769544.91, 0.0, ""),
    ("ARS", "17/11/2026", "CAMPO NITRO", "FAC A 5-19222", 1122880.00, 0.0, ""),
    ("USD", "13/3/2026", "DIVILAB", "FA-0005- 10493", 646.04, 646.04, ""),
    ("USD", "17/4/2026", "DIVILAB", "FA-0005- 10623", 1152.79, 1152.79, ""),
    ("USD", "17/4/2026", "DIVILAB", "FA-0005- 10646", 554.79, 554.79, ""),
    ("USD", "20/5/2026", "DIVILAB", "FA-0005- 10736", 1089.72, 1089.72, ""),
    ("USD", "5/6/2026", "DIVILAB", "FA-0005- 10827", 1136.38, 1136.38, ""),
    ("USD", "5/6/2026", "DIVILAB", "FA-0005- 10836", 977.03, 977.03, ""),
    ("USD", "3/7/2026", "DIVILAB", "FA-0005- 10909", 1067.74, 1067.74, ""),
    ("USD", "7/7/2026", "VITAGEN", "FAC A 9026", 579.57, 579.57, ""),
    ("USD", "5/8/2026", "DIVILAB", "FA-0005- 11033", 260.51, 260.51, ""),
    ("USD", "7/8/2026", "DIVILAB", "FA-0005- 11056", 1091.25, 1091.25, ""),
    ("USD", "20/8/2026", "DIVILAB", "FA-0005- 11090", 1067.55, 0.0, ""),
    ("USD", "26/8/2026", "DIVILAB", "FA-0005- 11123", 417.89, 0.0, ""),
    ("USD", "28/9/2026", "DIVILAB", "FA-0005- 11230", 1074.08, 0.0, ""),
    ("USD", "8/10/2026", "VITAGEN", "FAC A 9403", 721.58, 721.58, "8-oct"),
    ("USD", "13/10/2026", "DIVILAB", "FA-0005-00011271", 1063.42, 0.0, ""),
    ("USD", "29/10/2026", "DIVILAB", "FA-0005-00011350", 1069.82, 0.0, ""),
    ("USD", "17/11/2026", "VITAGEN", "FAC A 9560", 879.25, 0.0, ""),
    ("USD", "17/11/2026", "VITAGEN", "FAC A 9562", 577.64, 0.0, ""),
    ("USD", "18/11/2026", "DIVILAB", "FA-0005-00011422", 1563.16, 1563.16, "CA-0005-00000998"),
    ("USD", "18/11/2026", "DIVILAB", "FA-0005-00011424", 1012.85, 0.0, ""),
]


def _observacion(valor: Any, anio: int | None) -> tuple[str, str]:
    """La columna OBSERVACIÓN: si es una fecha, es la fecha de pago."""
    f = una_fecha(valor, anio)
    if pd.notna(f):
        return f.strftime("%Y-%m-%d"), ""
    return "", _texto(valor).strip()


def planilla_vmr() -> pd.DataFrame:
    filas = []
    for moneda, fecha, prov, comp, monto, pagado, obs in _PLANILLA_VMR:
        f = una_fecha(fecha)
        pago, nota = _observacion(obs, f.year)
        filas.append(fila(f, prov, comp, moneda, monto, pagado, pago, nota))
    return normalizar(pd.DataFrame(filas))


# ---------------------------------------------------------------------------
# Excel borrador: lectura y sincronización
# ---------------------------------------------------------------------------
def leer_archivo(archivo: Any) -> dict[str, pd.DataFrame]:
    """Hojas visibles del archivo como matrices (sin encabezado).

    En un .xlsx se respeta lo que está oculto: las columnas ocultas se ignoran
    y las filas ocultas (a mano, agrupadas o por un filtro) quedan anotadas en
    ``matriz.attrs["ocultas"]`` (posiciones de fila)."""
    nombre = str(getattr(archivo, "name", "archivo")).lower()
    datos = archivo.getvalue() if hasattr(archivo, "getvalue") else archivo.read()
    if nombre.endswith(".csv"):
        texto = datos.decode("latin-1")
        for cod in ("utf-8-sig", "latin-1"):
            try:
                texto = datos.decode(cod)
                break
            except UnicodeDecodeError:
                continue
        return {"CSV": pd.read_csv(io.StringIO(texto), header=None, dtype=object, sep=None, engine="python")}
    try:
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(datos), data_only=True)
        wb_f = load_workbook(io.BytesIO(datos), data_only=False)
    except Exception:  # .xls viejo u otro formato: sin datos de visibilidad
        return pd.read_excel(io.BytesIO(datos), sheet_name=None, header=None, dtype=object)
    libro = _Libro(wb, wb_f)
    hojas: dict[str, pd.DataFrame] = {}
    for ws in wb.worksheets:
        if getattr(ws, "sheet_state", "visible") != "visible":
            continue
        alto, ancho = min(ws.max_row or 0, 20000), min(ws.max_column or 0, 60)
        filas = [[libro.valor(ws.title, r, c) for c in range(1, ancho + 1)] for r in range(1, alto + 1)]
        mat = pd.DataFrame(filas, dtype=object) if filas else pd.DataFrame()
        for dim in ws.column_dimensions.values():
            if getattr(dim, "hidden", False) and dim.min:
                for j in range(int(dim.min) - 1, int(dim.max or dim.min)):
                    if j < mat.shape[1]:
                        mat[j] = None
        # la fila r de Excel es la posición r - 1
        mat.attrs["ocultas"] = {r - 1 for r, dim in ws.row_dimensions.items() if getattr(dim, "hidden", False)}
        mat.attrs["recalculadas"] = libro.recalculadas.get(ws.title, 0)
        hojas[ws.title] = mat
    return hojas


_REF = re.compile(
    r"(?<![A-Za-z0-9_.\"])(?:(?:'(?P<h1>[^']+)'|(?P<h2>[A-Za-z0-9_.]+))!)?"
    r"\$?(?P<c1>[A-Za-z]{1,3})\$?(?P<f1>\d+)(?::\$?(?P<c2>[A-Za-z]{1,3})\$?(?P<f2>\d+))?(?![A-Za-z0-9_(])")
_FUNCIONES = {"SUM": "_sum", "SUMA": "_sum", "ROUND": "_round", "REDONDEAR": "_round", "ABS": "abs",
              "MAX": "_max", "MIN": "_min", "IFERROR": "_iferror", "SI.ERROR": "_iferror", "SIERROR": "_iferror"}


def _col_num(letras: str) -> int:
    n = 0
    for ch in letras.upper():
        n = n * 26 + ord(ch) - 64
    return n


def _plano(xs: Any) -> list[float]:
    out: list[float] = []
    for x in xs if isinstance(xs, list) else [xs]:
        if isinstance(x, list):
            out += _plano(x)
        elif isinstance(x, (int, float)):
            out.append(float(x))
    return out


class _Libro:
    """Valores de un Excel. Si una celda con fórmula viene sin valor calculado
    o con error (#REF!, #VALUE!…, típico al copiar entre Excel y Sheets), se
    calcula acá: referencias a celdas (también de otras hojas), + - * / ^,
    SUMA/SUM, REDONDEAR/ROUND, ABS, MAX, MIN y SI.ERROR."""

    def __init__(self, wb_valores: Any, wb_formulas: Any):
        self.v, self.f = wb_valores, wb_formulas
        self.cache: dict[tuple[str, int, int], Any] = {}
        self.recalculadas: dict[str, int] = {}

    def valor(self, hoja: str, fila: int, col: int, prof: int = 0) -> Any:
        clave_c = (hoja, fila, col)
        if clave_c in self.cache:
            return self.cache[clave_c]
        if hoja not in self.v.sheetnames:
            return None
        v = self.v[hoja].cell(fila, col).value
        f = self.f[hoja].cell(fila, col).value if hoja in self.f.sheetnames else None
        if isinstance(f, str) and f.startswith("=") and (v is None or es_error(v)) and prof < 40:
            self.cache[clave_c] = None  # corta referencias circulares
            calc = self._evaluar(f[1:], hoja, prof + 1)
            if calc is not None:
                v = calc
                self.recalculadas[hoja] = self.recalculadas.get(hoja, 0) + 1
        self.cache[clave_c] = v
        return v

    def _numero(self, hoja: str, fila: int, col: int, prof: int) -> float:
        x = self.valor(hoja, fila, col, prof)
        if isinstance(x, (datetime, date)):
            return float((pd.Timestamp(x) - pd.Timestamp("1899-12-30")).days)
        n = importe_celda(x)
        return float(n) if n is not None else 0.0

    def _evaluar(self, formula: str, hoja: str, prof: int) -> float | None:
        import ast

        if '"' in formula:
            return None

        def ref(m: re.Match) -> str:
            h = m["h1"] or m["h2"] or hoja
            c1, f1 = _col_num(m["c1"]), int(m["f1"])
            if not m["c2"]:
                return repr(self._numero(h, f1, c1, prof))
            c2, f2 = _col_num(m["c2"]), int(m["f2"])
            if (abs(f2 - f1) + 1) * (abs(c2 - c1) + 1) > 20000:
                raise ValueError("rango muy grande")
            vals = [repr(self._numero(h, r, c, prof)) for r in range(min(f1, f2), max(f1, f2) + 1)
                    for c in range(min(c1, c2), max(c1, c2) + 1)]
            return "[" + ",".join(vals) + "]"

        try:
            expr = formula.strip().lstrip("+")
            for nombre, py in sorted(_FUNCIONES.items(), key=lambda x: -len(x[0])):
                expr = re.sub(rf"(?<![A-Za-z_.]){re.escape(nombre)}\s*\(", py + "(", expr, flags=re.IGNORECASE)
            expr = _REF.sub(ref, expr).replace(";", ",").replace("^", "**")
            arbol = ast.parse(expr, mode="eval")
            permitidos = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.List, ast.Call, ast.Name, ast.Load,
                          ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd)
            funciones = {"_sum": lambda *a: sum(_plano(list(a))), "_round": lambda x, n=0: round(float(x), int(n)),
                         "abs": lambda x: abs(float(x)), "_max": lambda *a: max(_plano(list(a)) or [0.0]),
                         "_min": lambda *a: min(_plano(list(a)) or [0.0]), "_iferror": lambda x, *a: x}
            for nodo in ast.walk(arbol):
                if not isinstance(nodo, permitidos):
                    return None
                if isinstance(nodo, ast.Name) and nodo.id not in funciones:
                    return None
                if isinstance(nodo, ast.Constant) and not isinstance(nodo.value, (int, float)):
                    return None
            r = eval(compile(arbol, "<formula>", "eval"), {"__builtins__": {}}, funciones)  # noqa: S307 (árbol validado)
            return round(float(r), 6) if isinstance(r, (int, float)) else None
        except Exception:
            return None


def _celda(v: Any) -> str:
    return _sin_acentos(v).replace(".", "").strip() if v is not None and not (isinstance(v, float) and pd.isna(v)) else ""


def _es_encabezado(celdas: list[str]) -> dict[str, int] | None:
    """Columnas de una fila de encabezado (FECHA · PROVEEDOR · COMPROBANTE · MONTO
    · PAGADO · SALDO · OBSERVACIÓN), o None si la fila no lo es."""
    def pos(*claves: str, evitar: tuple[str, ...] = ()) -> int | None:
        for exacto in (True, False):  # primero la coincidencia exacta, después «contiene»
            for j, c in enumerate(celdas):
                if not c or any(e in c for e in evitar):
                    continue
                if any((c == k or c.startswith(k + " ")) if exacto else k in c for k in claves):
                    return j
        return None

    j = {"proveedor": pos("proveedor", "razon social", "acreedor"),
         "comprobante": pos("comprobante", "factura", "nro", "numero", "n°"),
         "monto": pos("monto", "importe", "total factura")}
    if None in j.values() or len(set(j.values())) < 3:
        return None
    j["fecha"] = pos("fecha", evitar=("pago", "vto", "venc"))
    j["pagado"] = pos("pagado", "abonado", "pago", evitar=("fecha",))
    j["saldo"] = pos("saldo", "pendiente", "debe")
    j["observacion"] = pos("observ", "obs", "nota", "fecha pago", "fecha de pago")
    return j


def _es_cero(v: Any) -> bool:
    return isinstance(v, str) and re.fullmatch(r"\s*(us)?\$?\s*-+\s*", v.lower()) is not None


def leer_planilla(hojas: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Busca en cada hoja las tablas FECHA · PROVEEDOR · COMPROBANTE · MONTO · PAGADO
    · SALDO · OBSERVACIÓN. Una tabla con «USD» / «EN USD» justo arriba (o en el
    nombre de la hoja) es en dólares.

    Cada tabla sigue hasta el próximo encabezado: las filas TOTAL, vacías o de
    subtotales viejos se saltean (no cortan la lectura). En ``attrs["tablas"]``
    queda qué se encontró, para mostrarlo."""
    filas: list[dict[str, Any]] = []
    tablas: list[dict[str, Any]] = []
    for nombre_hoja, m in hojas.items():
        if m is None or m.empty:
            continue
        ocultas = set(m.attrs.get("ocultas", set()))
        mat = m.reset_index(drop=True)
        mat.columns = range(mat.shape[1])
        textos_filas = [[_celda(v) for v in mat.iloc[r].tolist()] for r in range(len(mat))]
        encabezados = [(r, cols) for r, t in enumerate(textos_filas) if (cols := _es_encabezado(t))]
        for k, (r0, cols) in enumerate(encabezados):
            r1 = encabezados[k + 1][0] if k + 1 < len(encabezados) else len(mat)
            arriba = [c for r in range(max(0, r0 - 3), r0 + 1) for c in textos_filas[r]]
            marca_usd = any(re.fullmatch(r"(en\s+)?(usd|u\$s|us\$|dolares)(\s.*)?", c) for c in arriba)
            moneda = "USD" if marca_usd or re.search(r"\busd\b|dolar", _sin_acentos(nombre_hoja)) else "ARS"
            info = {"hoja": nombre_hoja, "fila": r0 + 1, "moneda": moneda, "visibles": 0, "ocultas": 0,
                    "recalculadas": int(m.attrs.get("recalculadas", 0)), "total_excel": None, "saldo": 0.0}
            for r in range(r0 + 1, r1):
                valores = mat.iloc[r].tolist()
                if any(t.startswith(("total", "subtotal", "proveedores a pagar")) for t in textos_filas[r]):
                    if r not in ocultas and info.get("total_excel") is None and cols.get("saldo") is not None:
                        tot = importe_celda(valores[cols["saldo"]]) if cols["saldo"] < len(valores) else None
                        if tot is not None:
                            info["total_excel"] = tot
                    continue
                v = lambda c: valores[cols[c]] if cols.get(c) is not None and cols[c] < len(valores) else None
                prov, comp = _texto(v("proveedor")).strip(), _texto(v("comprobante")).strip()
                monto = importe_celda(v("monto")) or 0.0
                if not prov or (not comp and not monto):
                    continue
                f = una_fecha(v("fecha"))
                pagado = importe_celda(v("pagado")) or 0.0
                if cols.get("pagado") is None and cols.get("saldo") is not None:
                    # Sin columna PAGADO, lo pagado sale del saldo. Si hay PAGADO, el SALDO no se usa:
                    # es la columna que más se desarma al pegar fórmulas entre Excel y Sheets.
                    saldo = importe_celda(v("saldo"))
                    if saldo is not None and monto - saldo > 0.004:
                        pagado = round(monto - saldo, 2)
                pago, nota = _observacion(v("observacion"), f.year if pd.notna(f) else None)
                oculta = r in ocultas
                info["ocultas" if oculta else "visibles"] += 1
                info["saldo"] = round(info.get("saldo", 0.0) + max(monto - pagado, 0.0), 2)
                filas.append(fila(f, prov, comp, moneda, monto, pagado, pago, nota, oculta=oculta))
            tablas.append(info)
    resultado = normalizar(pd.DataFrame(filas)) if filas else _vacio()
    resultado.attrs["tablas"] = tablas
    return resultado


_CAMPOS_SYNC = [("fecha", "fecha"), ("proveedor", "proveedor"), ("comprobante", "comprobante"), ("monto", "monto"),
                ("pagado", "pagado"), ("fecha_pago", "fecha de pago"), ("observacion", "observación")]


def sincronizar(actual: pd.DataFrame, excel: pd.DataFrame, *, quitar_faltantes: bool = False
                ) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Mezcla el Excel borrador con lo cargado: agrega las facturas nuevas y
    actualiza las que ya están (misma factura = proveedor + comprobante + moneda)."""
    a = actual.copy()
    e = excel.copy()
    a["_vez"] = a.groupby("_clave").cumcount() if len(a) else pd.Series(dtype=int)
    e["_vez"] = e.groupby("_clave").cumcount() if len(e) else pd.Series(dtype=int)
    idx_a = {(k, v): i for i, k, v in zip(a.index, a["_clave"], a["_vez"])}
    nuevas, cambios, iguales, vistos = [], [], 0, set()
    ignoradas, ocultadas = 0, 0
    for _, f in e.iterrows():
        llave = (f["_clave"], f["_vez"])
        if llave not in idx_a:
            if bool(f["oculta"]):
                ignoradas += 1  # fila oculta en el Excel que la app no tiene: no se trae
            else:
                nuevas.append(f)
            continue
        i = idx_a[llave]
        vistos.add(i)
        dif = []
        if bool(a.at[i, "oculta"]) != bool(f["oculta"]):
            dif.append("se oculta" if bool(f["oculta"]) else "vuelve a verse")
            ocultadas += int(bool(f["oculta"]))
            a.at[i, "oculta"] = bool(f["oculta"])
        for campo, nombre in _CAMPOS_SYNC:
            va, ve = a.at[i, campo], f[campo]
            if campo in ("monto", "pagado"):
                if abs(float(va) - float(ve)) > 0.004:
                    dif.append(nombre)
                    a.at[i, campo] = float(ve)
            elif campo in ("fecha_pago", "observacion") and not str(ve):
                continue  # el Excel no borra una fecha de pago u observación cargada en la app
            elif str(va) != str(ve) and str(ve):
                dif.append(nombre)
                a.at[i, campo] = ve
                if campo in ("fecha", "fecha_pago"):
                    a.at[i, "_fecha" if campo == "fecha" else "_pago"] = una_fecha(ve)
        if dif:
            cambios.append({"proveedor": f["proveedor"], "comprobante": f["comprobante"], "moneda": f["moneda"],
                            "cambios": ", ".join(dif)})
        else:
            iguales += 1
    faltan = [i for i in a.index if i not in vistos]
    if quitar_faltantes:
        a = a.loc[sorted(vistos)]
    base = para_guardar(a.drop(columns=["_vez"]))
    if nuevas:
        base = pd.concat([base, para_guardar(pd.DataFrame(nuevas).drop(columns=["_vez"]))], ignore_index=True)
    resultado = normalizar(base)
    return resultado, {"nuevas": len(nuevas), "actualizadas": len(cambios), "iguales": iguales,
                       "ocultadas": ocultadas, "ocultas_ignoradas": ignoradas,
                       "solo_app": len(faltan), "quitadas": len(faltan) if quitar_faltantes else 0,
                       "detalle_cambios": cambios,
                       "detalle_nuevas": pd.DataFrame(nuevas).drop(columns=["_vez"]) if nuevas else _vacio()}


# ---------------------------------------------------------------------------
# Vista tipo planilla
# ---------------------------------------------------------------------------
_CSS = """
<style>
.cc-emp{font-size:1.05rem;font-weight:750;letter-spacing:.02em;color:#1F1A1D;margin:.1rem 0 .6rem}
.cc-res{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-bottom:1rem}
.cc-caja{border:1px solid #E8E3E6;border-radius:12px;overflow:hidden;background:#fff}
.cc-tit{display:flex;justify-content:space-between;align-items:center;gap:.6rem;padding:.55rem .9rem;background:#F7EFF3;
  border-bottom:1px solid #EADDE4}
.cc-tit b{font-size:.78rem;letter-spacing:.05em;text-transform:uppercase;color:#5E1A39}
.cc-tit span{font-size:.8rem;color:#6E6268;font-variant-numeric:tabular-nums}
.cc-pr{display:grid;grid-template-columns:minmax(0,1fr) 150px;gap:.5rem;padding:.38rem .9rem;border-top:1px solid #F1ECEF;
  font-size:.88rem;align-items:center}
.cc-pr .cc-n{font-weight:600}
.cc-cero{color:#A79CA2}.cc-cero .cc-n{font-weight:400}
.cc-apagar{background:#F8D9C4;font-weight:750;border-top:1px solid #E9BFA3}
.cc-cols,.cc-row{display:grid;grid-template-columns:92px minmax(110px,1fr) minmax(130px,1.1fr) 128px 128px 124px minmax(110px,.9fr);
  gap:.5rem;align-items:center;padding:.38rem .9rem}
.cc-cols{font-size:.68rem;font-weight:600;color:#8A7F85;text-transform:uppercase;letter-spacing:.04em;padding-top:.45rem;padding-bottom:.25rem}
.cc-row{border-top:1px solid #F1ECEF;font-size:.86rem}
.cc-n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.cc-c{font-variant-numeric:tabular-nums;color:#5D5258;white-space:nowrap}
.cc-t{min-width:0;overflow-wrap:anywhere}
.cc-obs{font-size:.8rem;color:#6E6268;min-width:0;overflow-wrap:anywhere}
.cc-pagada{color:#8A7F85}
.cc-debe .cc-saldo{font-weight:700;color:#8C2853}
.cc-tot{background:#EFE7EB;font-weight:700;border-top:1px solid #DCCFD6}
.cc-oculta{background:repeating-linear-gradient(135deg,#FAF7F8 0 8px,#F4EFF2 8px 16px);font-style:italic}
@media (max-width:760px){
  .cc-res{grid-template-columns:1fr}
  .cc-cols{display:none}
  .cc-row{grid-template-columns:1fr 1fr;gap:.15rem .6rem}
  .cc-row>div:nth-child(1){grid-column:1}.cc-row>div:nth-child(2){grid-column:2;text-align:right;font-weight:650}
  .cc-row>div:nth-child(3){grid-column:1/3;color:#6E6268;font-size:.8rem}
  .cc-row>div:nth-child(4):before{content:"Monto ";color:#8A7F85;font-size:.72rem}
  .cc-row>div:nth-child(5):before{content:"Pagado ";color:#8A7F85;font-size:.72rem}
  .cc-row>div:nth-child(6):before{content:"Saldo ";color:#8A7F85;font-size:.72rem}
  .cc-row>div:nth-child(4),.cc-row>div:nth-child(5),.cc-row>div:nth-child(6){text-align:left}
  .cc-row>div:nth-child(7){grid-column:1/3}
  .cc-pr{grid-template-columns:minmax(0,1fr) auto}
}
</style>
"""


def _e(v: Any) -> str:
    return html.escape(_texto(v))


def _fecha_ar(ts: Any) -> str:
    return f"{ts.day}/{ts.month}/{ts.year}" if isinstance(ts, pd.Timestamp) and pd.notna(ts) else ""


def html_resumen(d: pd.DataFrame) -> str:
    partes = []
    for moneda, titulo, pie in (("ARS", "Proveedor", "Proveedores a pagar"), ("USD", "Proveedores en USD", "Proveedores a pagar")):
        r = resumen_proveedores(d, moneda)
        filas = "".join(
            f'<div class="cc-pr{" cc-cero" if s <= 0.004 else ""}"><div class="cc-t">{_e(p)}</div>'
            f'<div class="cc-n">{importe(s, moneda) if s > 0.004 else ("US$ -" if moneda == "USD" else "$ -")}</div></div>'
            for p, s in zip(r["proveedor"], r["saldo"]))
        total = float(r["saldo"].sum()) if len(r) else 0.0
        partes.append(
            f'<div class="cc-caja"><div class="cc-tit"><b>{titulo}</b><span>Monto</span></div>'
            f'{filas or "<div class=cc-pr><div class=cc-t>Sin proveedores</div><div></div></div>"}'
            f'<div class="cc-pr cc-apagar"><div>{pie.upper()}</div><div class="cc-n">{importe(total, moneda)}</div></div></div>')
    return f'<div class="cc-res">{"".join(partes)}</div>'


def html_detalle(x: pd.DataFrame, moneda: str, titulo: str) -> str:
    x = x[x["moneda"].eq(moneda)]
    if x.empty:
        return ""
    filas = []
    for _, f in x.iterrows():
        obs = " · ".join(t for t in (_fecha_ar(f["_pago"]), _texto(f["observacion"])) if t)
        clase = ("cc-debe" if f["saldo"] > 0.004 else "cc-pagada") + (" cc-oculta" if bool(f["oculta"]) else "")
        filas.append(
            f'<div class="cc-row {clase}"><div class="cc-c">{_fecha_ar(f["_fecha"])}</div><div class="cc-t">{_e(f["proveedor"])}</div>'
            f'<div class="cc-t">{_e(f["comprobante"])}{" <small>(a cobrar)</small>" if f["tipo"] == "A cobrar" else ""}</div>'
            f'<div class="cc-n">{importe(f["monto"], moneda)}</div>'
            f'<div class="cc-n">{importe(f["pagado"], moneda) if f["pagado"] > 0.004 else ""}</div>'
            f'<div class="cc-n cc-saldo">{importe(f["saldo"], moneda) if f["saldo"] > 0.004 else "-"}</div>'
            f'<div class="cc-obs">{html.escape(obs)}</div></div>')
    pend = int((x["saldo"] > 0.004).sum())
    return (f'<div class="cc-caja" style="margin-bottom:1rem"><div class="cc-tit"><b>{titulo}</b>'
            f'<span>{len(x)} facturas · {pend} con saldo</span></div>'
            '<div class="cc-cols"><div>Fecha</div><div>Proveedor</div><div>Comprobante</div><div class="cc-n">Monto</div>'
            '<div class="cc-n">Pagado</div><div class="cc-n">Saldo</div><div>Observación</div></div>'
            + "".join(filas) +
            f'<div class="cc-row cc-tot"><div></div><div>TOTAL</div><div></div><div class="cc-n">{importe(x["monto"].sum(), moneda)}</div>'
            f'<div class="cc-n">{importe(x["pagado"].sum(), moneda)}</div><div class="cc-n">{importe(x["saldo"].sum(), moneda)}</div>'
            '<div></div></div></div>')


def excel(d: pd.DataFrame, tabla: str) -> bytes:
    """Excel con el formato de la planilla (se puede volver a importar)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    salmon = PatternFill("solid", fgColor="F8CBAD")
    gris = PatternFill("solid", fgColor="E7E6E6")
    fmt = '"$" #,##0.00'
    wb = Workbook()
    ws = wb.active
    ws.title = "Cuenta corriente"
    ws.append(["", f"PROVEEDORES DE {EMPRESA.get(tabla, '')}"])
    ws.cell(1, 2).font = Font(bold=True, size=14)
    ws.append([])
    ra, ru = resumen_proveedores(d, "ARS"), resumen_proveedores(d, "USD")
    ws.append(["", "PROVEEDOR", "MONTO", "", "", "PROVEEDORES EN USD", "MONTO"])
    for j in (2, 3, 6, 7):
        ws.cell(ws.max_row, j).font = Font(bold=True, color="C00000")
    for k in range(max(len(ra), len(ru))):
        fila_x = ["", None, None, "", "", None, None]
        if k < len(ra):
            fila_x[1], fila_x[2] = ra.at[k, "proveedor"], float(ra.at[k, "saldo"])
        if k < len(ru):
            fila_x[5], fila_x[6] = ru.at[k, "proveedor"], float(ru.at[k, "saldo"])
        ws.append(fila_x)
        for j in (3, 7):
            ws.cell(ws.max_row, j).number_format = fmt
    ws.append(["", "PROVEEDORES A PAGAR", float(ra["saldo"].sum()) if len(ra) else 0.0, "", "",
               "PROVEEDORES A PAGAR", float(ru["saldo"].sum()) if len(ru) else 0.0])
    for j in (2, 3, 6, 7):
        ws.cell(ws.max_row, j).font = Font(bold=True)
    for j in (3, 7):
        ws.cell(ws.max_row, j).fill, ws.cell(ws.max_row, j).number_format = salmon, fmt
    for moneda in MONEDAS:
        x = d[d["moneda"].eq(moneda)]
        if x.empty:
            continue
        ws.append([])
        ws.append([])
        if moneda == "USD":
            ws.append(["EN USD"])
            ws.cell(ws.max_row, 1).font = Font(bold=True)
        ws.append(["FECHA", "PROVEEDOR", "COMPROBANTE", "MONTO", "PAGADO", "SALDO", "OBSERVACION"])
        for c in ws[ws.max_row][:7]:
            c.font, c.fill = Font(bold=True), gris
        for _, f in x.iterrows():
            obs = f["_pago"].to_pydatetime() if pd.notna(f["_pago"]) and not f["observacion"] else (f["observacion"] or None)
            ws.append([f["_fecha"].to_pydatetime() if pd.notna(f["_fecha"]) else None, f["proveedor"], f["comprobante"],
                       float(f["monto"]), float(f["pagado"]) or None, float(f["saldo"]), obs])
            ws.cell(ws.max_row, 1).number_format = "d/m/yyyy"
            if bool(f["oculta"]):
                ws.row_dimensions[ws.max_row].hidden = True
            if isinstance(obs, datetime):
                ws.cell(ws.max_row, 7).number_format = "d/m/yyyy"
            for j in (4, 5, 6):
                ws.cell(ws.max_row, j).number_format = fmt
        ws.append(["", "", "", "", "TOTAL", float(x["saldo"].sum())])
        ws.cell(ws.max_row, 6).font, ws.cell(ws.max_row, 6).number_format = Font(bold=True), fmt
    for letra, ancho in zip("ABCDEFG", (12, 24, 24, 16, 22, 18, 20)):
        ws.column_dimensions[letra].width = ancho
    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()


# ---------------------------------------------------------------------------
# Edición
# ---------------------------------------------------------------------------
_COLS_EDICION = ["borrar", "_fecha", "proveedor", "comprobante", "moneda", "monto", "pagado", "saldo", "_pago",
                 "observacion", "tipo", "oculta"]


def tabla_edicion(x: pd.DataFrame) -> pd.DataFrame:
    t = x.copy()
    t["borrar"] = False
    t["_fecha"] = t["_fecha"].dt.date
    t["_pago"] = t["_pago"].dt.date
    return t.set_index("id")[_COLS_EDICION]


def aplicar_edicion(d: pd.DataFrame, original: pd.DataFrame, editada: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Lo editado reemplaza a esas filas; lo demás queda igual. Devuelve (nuevo, cambios)."""
    ed = editada.copy()
    ed.index = [str(i) if str(i) not in ("", "nan", "None") else "" for i in ed.index]
    borrar = ed["borrar"].fillna(False).astype(bool)
    vivas = ed[~borrar]
    filas = []
    for i, f in vivas.iterrows():
        if not _texto(f.get("proveedor")).strip() and not _texto(f.get("comprobante")).strip() and not (numero(f.get("monto")) or 0):
            continue
        r = fila(f.get("_fecha"), _texto(f.get("proveedor")), _texto(f.get("comprobante")),
                 f.get("moneda") if f.get("moneda") in MONEDAS else "ARS", numero(f.get("monto")) or 0.0,
                 numero(f.get("pagado")) or 0.0, f.get("_pago"), _texto(f.get("observacion")),
                 f.get("tipo") if f.get("tipo") in TIPOS else "A pagar", oculta=_sin_acentos(f.get("oculta")) in {"true", "1", "si"})
        r["id"] = i
        filas.append(r)
    nuevas = normalizar(pd.DataFrame(filas)) if filas else _vacio()
    ids_originales = set(map(str, original.index))
    antes = para_guardar(d[d["id"].isin(ids_originales)]) if len(original) else pd.DataFrame(columns=COLUMNAS)
    despues = para_guardar(nuevas) if len(nuevas) else pd.DataFrame(columns=COLUMNAS)
    a = antes.set_index("id")[COLUMNAS[1:]] if len(antes) else pd.DataFrame(columns=COLUMNAS[1:])
    b = despues.set_index("id")[COLUMNAS[1:]] if len(despues) else pd.DataFrame(columns=COLUMNAS[1:])
    comunes = a.index.intersection(b.index)
    cambios = int(len(a.index.difference(b.index)) + len(b.index.difference(a.index))
                  + (a.loc[comunes] != b.loc[comunes]).any(axis=1).sum())
    if cambios == 0:
        return d, 0
    resto = d[~d["id"].isin(ids_originales)] if len(original) else d
    base = pd.concat([para_guardar(resto) if len(resto) else pd.DataFrame(columns=COLUMNAS), despues], ignore_index=True)
    return normalizar(base), cambios


def registrar_pagos(d: pd.DataFrame, pagos: dict[str, float], fecha_pago: Any) -> tuple[pd.DataFrame, int]:
    """Suma lo pagado a cada factura (id → importe). Si queda saldada, guarda la fecha de pago."""
    x = d.copy()
    fp = una_fecha(fecha_pago)
    n = 0
    for i, monto in pagos.items():
        sel = x["id"].eq(i)
        if not sel.any() or not monto or monto <= 0:
            continue
        j = x.index[sel][0]
        x.at[j, "pagado"] = round(min(float(x.at[j, "monto"]), float(x.at[j, "pagado"]) + float(monto)), 2)
        x.at[j, "saldo"] = round(max(float(x.at[j, "monto"]) - float(x.at[j, "pagado"]), 0.0), 2)
        if pd.notna(fp):
            x.at[j, "_pago"] = fp
        n += 1
    return normalizar(para_guardar(x)), n


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------
def _opciones_mes(d: pd.DataFrame) -> list[str]:
    return sorted({m for m in d["_mes"] if m}, reverse=True)


def render_cuenta_corriente(
    df_original: pd.DataFrame | None,
    *,
    tabla: str,
    guardar: Callable[[pd.DataFrame], Any],
    selector: Callable[..., str] | None = None,
    mostrar_error: Callable[[str, Exception], None] | None = None,
) -> None:
    hoy = pd.Timestamp.today().normalize()
    d = normalizar(df_original)
    k = f"cc2_{tabla}_"
    rev_key = f"{k}rev"
    rev = int(st.session_state.get(rev_key, 0))

    def _error(msg: str, exc: Exception) -> None:
        (mostrar_error or (lambda m, e: st.error(f"{m} {e}")))(msg, exc)

    def _guardar(nuevo: pd.DataFrame, aviso: str) -> None:
        try:
            guardar(para_guardar(nuevo))
        except Exception as exc:
            _error("No se pudieron guardar los cambios en Google Sheets.", exc)
            return
        st.session_state[rev_key] = rev + 1
        st.toast(aviso, icon=":material/check_circle:")
        st.rerun()

    st.markdown(_CSS + f'<div class="cc-emp">PROVEEDORES DE {html.escape(EMPRESA.get(tabla, ""))}</div>',
                unsafe_allow_html=True)
    t = totales(d, hoy)
    vieja = t["mas_vieja"]
    tarjetas_kpi([
        ("Proveedores a pagar", pesos(t["ARS"]), f"{t['ARS_n']} factura(s) con saldo", "#8C2853"),
        ("Proveedores en USD", usd(t["USD"]), f"{t['USD_n']} factura(s) con saldo", "#2F6F8F"),
        (f"Facturas de {nombre_mes(t['mes'])}", pesos(t["ARS_mes"], 0),
         f"{t['ARS_mes_n'] + t['USD_mes_n']} cargadas" + (f" · {usd(t['USD_mes'])}" if t["USD_mes"] else ""), "#C58A32"),
        ("Impaga más antigua", _fecha_ar(vieja["_fecha"]) if vieja else "—",
         f"{vieja['proveedor']} · {(hoy - vieja['_fecha']).days} días" if vieja else "No hay facturas impagas", "#B14E5E"),
    ])

    seccion = (selector("Sección", PANTALLAS, key=f"{k}seccion") if selector else
               st.segmented_control("Sección", PANTALLAS, default=PANTALLAS[0], key=f"{k}seccion",
                                    label_visibility="collapsed") or PANTALLAS[0])
    proveedores = sorted({p for p in d["proveedor"] if p})

    # ---- Planilla ----------------------------------------------------------
    if seccion == "Planilla":
        if d.empty:
            st.info("Todavía no hay facturas cargadas. Cargalas en «Cargar» o traé el Excel borrador en «Importar».")
            return
        st.markdown(html_resumen(d), unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns([1.1, 1.2, 1.3, 1.4])
        meses = _opciones_mes(d)
        mes = c1.selectbox("Mes de la factura", ["Todos", *meses], key=f"{k}pl_mes",
                           format_func=lambda m: "Todos los meses" if m == "Todos" else nombre_mes(m))
        prov = c2.selectbox("Proveedor", ["Todos", *proveedores], key=f"{k}pl_prov")
        with c3:
            estado = st.segmented_control("Facturas", ["Todas", "Con saldo", "Pagadas"], default="Todas",
                                          key=f"{k}pl_estado") or "Todas"
        buscar = c4.text_input("Buscar", key=f"{k}pl_buscar", placeholder="Comprobante, proveedor, observación…")
        n_ocultas = int(d["oculta"].sum())
        ver_ocultas = st.toggle(f"Ver también las filas ocultas ({n_ocultas})", value=False, key=f"{k}pl_ocultas",
                                disabled=n_ocultas == 0,
                                help="Como en el Excel: las ocultas (por ejemplo, ya pagadas de meses anteriores) "
                                     "no se muestran, pero quedan guardadas.") if n_ocultas else False
        x = filtrar(d, mes="" if mes == "Todos" else mes, proveedor="" if prov == "Todos" else prov,
                    estado=estado, buscar=buscar, con_ocultas=ver_ocultas)
        if x.empty:
            st.info("No hay facturas visibles con esos filtros.")
        st.markdown(html_detalle(x, "ARS", "Detalle en pesos") + html_detalle(x, "USD", "En USD"), unsafe_allow_html=True)
        _, n_pagadas = ocultar_pagadas(d)
        if n_pagadas and st.button(f"Ocultar las {n_pagadas} facturas ya pagadas", key=f"{k}ocultar_pag",
                                   icon=":material/visibility_off:",
                                   help="Como a fin de mes en el Excel. Quedan guardadas y se pueden volver a mostrar en «Editar»."):
            nuevo, n = ocultar_pagadas(d)
            _guardar(nuevo, f"{n} facturas pagadas ocultas")
        st.download_button("Descargar Excel (formato planilla)", data=excel(d, tabla), icon=":material/download:",
                           file_name=f"{tabla}_{hoy:%Y-%m-%d}.xlsx", key=f"{k}xls",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        return

    # ---- Cargar ------------------------------------------------------------
    if seccion == "Cargar":
        st.caption("Factura nueva del mes. Si ya está pagada, marcalo y queda con su fecha de pago.")
        moneda = st.segmented_control("Moneda", MONEDAS, default="ARS", key=f"{k}ca_moneda") or "ARS"
        de_moneda = sorted({p for p, m in zip(d["proveedor"], d["moneda"]) if p and m == moneda}) or proveedores
        with st.form(f"{k}form_ca_{rev}_{moneda}", clear_on_submit=False, border=True):
            a1, a2 = st.columns(2)
            fecha = a1.date_input("Fecha de la factura", value=hoy.date(), format="DD/MM/YYYY")
            prov_sel = a2.selectbox("Proveedor", [*de_moneda, "➕ Otro proveedor"] if de_moneda else ["➕ Otro proveedor"])
            prov_nuevo = a2.text_input("Nombre del proveedor nuevo", placeholder="Sólo si elegiste «Otro proveedor»")
            b1, b2 = st.columns(2)
            comp = b1.text_input("Comprobante", placeholder="FAC A 0008-18577")
            monto = b2.number_input(f"Monto ({'US$' if moneda == 'USD' else '$'})", min_value=0.0, step=1000.0 if moneda == "ARS" else 10.0,
                                    format="%.2f")
            c1, c2 = st.columns(2)
            ya_pagada = c1.checkbox("Ya está pagada")
            fecha_pago = c2.date_input("Fecha de pago", value=hoy.date(), format="DD/MM/YYYY")
            obs = st.text_input("Observación", placeholder="Opcional")
            tipo = st.radio("Tipo", TIPOS, horizontal=True, help="Casi siempre es una factura a pagar de un proveedor.")
            ok = st.form_submit_button("Guardar factura", type="primary", icon=":material/add:")
        if ok:
            proveedor = (prov_nuevo.strip() if prov_sel.startswith("➕") else prov_sel).upper()
            if not proveedor or not comp.strip() or monto <= 0:
                st.error("Completá proveedor, comprobante y un monto mayor a cero.")
            elif clave(proveedor, comp, moneda) in set(d["_clave"]):
                st.warning(f"La factura {comp} de {proveedor} ya está cargada. Si querés cambiarla, usá «Editar».")
            else:
                nueva = fila(fecha, proveedor, comp, moneda, monto, monto if ya_pagada else 0.0,
                             fecha_pago if ya_pagada else None, obs, tipo)
                _guardar(agregar(d, [nueva]), f"Factura {comp} de {proveedor} guardada")
        recientes = d[d["_mes"].eq(hoy.strftime("%Y-%m"))]
        if len(recientes):
            st.markdown(html_detalle(recientes, "ARS", f"Cargadas en {nombre_mes(hoy.strftime('%Y-%m'))}")
                        + html_detalle(recientes, "USD", "En USD"), unsafe_allow_html=True)
        return

    # ---- Pagar -------------------------------------------------------------
    if seccion == "Pagar":
        moneda = st.segmented_control("Moneda", MONEDAS, default="ARS", key=f"{k}pa_moneda") or "ARS"
        pend = a_pagar(d)
        pend = pend[pend["moneda"].eq(moneda) & (pend["saldo"] > 0.004)]
        if pend.empty:
            st.success(f"No hay facturas en {moneda} con saldo. 🎉")
            return
        prov = st.selectbox("Proveedor", ["Todos", *sorted(set(pend["proveedor"]))], key=f"{k}pa_prov")
        if prov != "Todos":
            pend = pend[pend["proveedor"].eq(prov)]
        st.caption("Tildá las facturas que se pagaron. Si fue un pago parcial, cambiá «A pagar». "
                   "Queda registrado el pago con la fecha que elijas.")
        t = pend.set_index("id")[["_fecha", "proveedor", "comprobante", "monto", "saldo"]].copy()
        t["_fecha"] = t["_fecha"].dt.date
        t.insert(0, "pagar", False)
        t["a_pagar"] = t["saldo"]
        with st.form(f"{k}form_pa_{rev}_{moneda}", border=False):
            editada = st.data_editor(
                t, hide_index=True, use_container_width=True, key=f"{k}pa_ed_{rev}_{moneda}",
                disabled=["_fecha", "proveedor", "comprobante", "monto", "saldo"],
                column_config={
                    "pagar": st.column_config.CheckboxColumn("Pagar", width="small"),
                    "_fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
                    "proveedor": "Proveedor", "comprobante": "Comprobante",
                    "monto": st.column_config.NumberColumn("Monto", format="localized"),
                    "saldo": st.column_config.NumberColumn("Saldo", format="localized"),
                    "a_pagar": st.column_config.NumberColumn("A pagar", format="localized", min_value=0.0),
                })
            fecha_pago = st.date_input("Fecha de pago", value=hoy.date(), format="DD/MM/YYYY")
            ok = st.form_submit_button("Registrar pagos", type="primary", icon=":material/payments:")
        if ok:
            sel = editada[editada["pagar"].fillna(False).astype(bool)]
            pagos = {str(i): min(float(numero(v) or 0), float(t.at[i, "saldo"])) for i, v in sel["a_pagar"].items()}
            nuevo, n = registrar_pagos(d, pagos, fecha_pago)
            if n == 0:
                st.info("Tildá al menos una factura (con importe a pagar mayor a cero).")
            else:
                total = sum(pagos.values())
                _guardar(nuevo, f"{n} pago(s) registrados · {importe(total, moneda)}")
        return

    # ---- Editar ------------------------------------------------------------
    if seccion == "Editar":
        c1, c2, c3 = st.columns(3)
        meses = _opciones_mes(d)
        mes = c1.selectbox("Mes", ["Todos", *meses], key=f"{k}ed_mes",
                           format_func=lambda m: "Todos los meses" if m == "Todos" else nombre_mes(m))
        prov = c2.selectbox("Proveedor", ["Todos", *proveedores], key=f"{k}ed_prov")
        moneda = c3.selectbox("Moneda", ["Todas", *MONEDAS], key=f"{k}ed_moneda")
        con_ocultas = st.toggle("Incluir las filas ocultas", value=False, key=f"{k}ed_ocultas")
        x = filtrar(d, mes="" if mes == "Todos" else mes, proveedor="" if prov == "Todos" else prov,
                    con_ocultas=con_ocultas)
        if moneda != "Todas":
            x = x[x["moneda"].eq(moneda)]
        st.caption(f"{len(x)} facturas. Todas las celdas se pueden cambiar (el saldo se calcula solo). Para borrar marcá 🗑️; "
                   "para agregar usá la fila vacía del final. Se guarda con «Guardar cambios».")
        tabla_e = tabla_edicion(x)
        cfg = {
            "borrar": st.column_config.CheckboxColumn("🗑️", width="small", default=False),
            "_fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
            "proveedor": st.column_config.TextColumn("Proveedor"),
            "comprobante": st.column_config.TextColumn("Comprobante"),
            "moneda": st.column_config.SelectboxColumn("Moneda", options=MONEDAS, default="ARS", width="small"),
            "monto": st.column_config.NumberColumn("Monto", format="localized", min_value=0.0),
            "pagado": st.column_config.NumberColumn("Pagado", format="localized", min_value=0.0),
            "saldo": st.column_config.NumberColumn("Saldo", format="localized", disabled=True),
            "_pago": st.column_config.DateColumn("Fecha de pago", format="DD/MM/YYYY"),
            "observacion": st.column_config.TextColumn("Observación", width="medium"),
            "tipo": st.column_config.SelectboxColumn("Tipo", options=TIPOS, default="A pagar", width="small"),
            "oculta": st.column_config.CheckboxColumn("Oculta", width="small", default=False,
                                                      help="Oculta en la planilla (queda guardada)"),
        }
        with st.form(f"{k}form_ed_{rev}", border=False):
            editada = st.data_editor(tabla_e, hide_index=True, use_container_width=True, num_rows="dynamic",
                                     height=min(110 + 36 * len(tabla_e), 680), column_config=cfg, key=f"{k}editor_{rev}")
            b1, b2 = st.columns(2)
            ok = b1.form_submit_button("Guardar cambios", type="primary", icon=":material/save:", use_container_width=True)
            no = b2.form_submit_button("Descartar cambios", use_container_width=True)
        if no:
            st.session_state[rev_key] = rev + 1
            st.rerun()
        if ok:
            nuevo, n = aplicar_edicion(d, tabla_e, editada)
            if n == 0:
                st.info("No hay cambios para guardar.")
            else:
                _guardar(nuevo, f"{n} cambio(s) guardados")
        return

    # ---- Importar ----------------------------------------------------------
    if tabla == "cuenta_corriente_vmr":
        st.markdown("**Planilla de VMR (noviembre 2026)**")
        st.caption("Las facturas de las fotos de la planilla: 19 en pesos (desde marzo 2026) y 20 en USD. "
                   "Proveedores a pagar $11.709.202,97 · USD 7.162,50. No duplica: si ya están, las deja como están.")
        if st.button("Cargar la planilla de VMR", key=f"{k}seed", icon=":material/playlist_add:",
                     type="primary" if d.empty else "secondary"):
            nuevo, res = sincronizar(d, planilla_vmr())
            if res["nuevas"] == 0 and res["actualizadas"] == 0:
                st.info("Ya estaba todo cargado.")
            else:
                _guardar(nuevo, f"Planilla de VMR: {res['nuevas']} facturas nuevas, {res['actualizadas']} actualizadas")
        st.divider()
    st.markdown("**Sincronizar con el Excel borrador**")
    st.caption("Subí el Excel que usan de borrador (o bajalo de Google Sheets: Archivo → Descargar → Microsoft Excel). "
               "Se toma sólo lo visible: las filas y columnas ocultas del Excel no se traen, y si una factura que ya "
               "está en la app aparece oculta en el Excel, se oculta también acá. "
               "Busco las tablas FECHA · PROVEEDOR · COMPROBANTE · MONTO · PAGADO · SALDO · OBSERVACIÓN; la que tiene "
               "«EN USD» arriba va en dólares. Cada factura se reconoce por proveedor + comprobante: las nuevas se "
               "agregan, a las que ya están se les actualizan pagos y observaciones. Subir el mismo Excel dos veces no duplica nada.")
    archivo = st.file_uploader("Excel borrador", type=["xlsx", "xls", "csv"], key=f"{k}archivo", label_visibility="collapsed")
    if archivo is None:
        return
    try:
        hojas = leer_archivo(archivo)
    except Exception as exc:
        _error("No pude abrir el archivo.", exc)
        return
    nombres = list(hojas)
    if len(nombres) > 1:
        todas = f"Todas las hojas ({len(nombres)})"
        hoja = st.selectbox("¿Qué hoja (página) del archivo importo?", [*nombres, todas], index=0,
                            key=f"{k}hoja_{getattr(archivo, 'name', '')}")
        if hoja != todas:
            hojas = {hoja: hojas[hoja]}
    elif nombres:
        st.caption(f"Hoja: {nombres[0]}")
    try:
        importado = leer_planilla(hojas)
    except Exception as exc:
        _error("No pude leer la planilla.", exc)
        return
    tablas = importado.attrs.get("tablas", [])
    if tablas:
        st.caption("Encontré: " + " · ".join(
            f"hoja «{t['hoja']}», tabla en la fila {t['fila']} ({'USD' if t['moneda'] == 'USD' else 'pesos'}): "
            f"{t['visibles']} visibles" + (f", {t['ocultas']} ocultas" if t["ocultas"] else "") for t in tablas))
    recalc = {t["hoja"]: t["recalculadas"] for t in tablas if t.get("recalculadas")}
    if recalc:
        st.caption("Calculé yo " + " · ".join(f"{n} celdas con fórmulas en «{h}»" for h, n in recalc.items())
                   + ": venían sin valor o con error (#REF!, #VALUE!), típico al pegar entre Excel y Sheets.")
    for t in tablas:
        if t.get("total_excel") is not None and abs(t["total_excel"] - t["saldo"]) > 1:
            fmt = usd if t["moneda"] == "USD" else pesos
            st.warning(f"En «{t['hoja']}» ({'USD' if t['moneda'] == 'USD' else 'pesos'}) el TOTAL del Excel dice "
                       f"{fmt(t['total_excel'])}, pero sumando monto − pagado de cada factura da {fmt(t['saldo'])}. "
                       "Puede haber fórmulas mezcladas en la columna SALDO del Excel: acá se usa monto − pagado.")
    if importado.empty:
        st.warning("No encontré facturas. Revisá que la hoja tenga una fila de encabezados con PROVEEDOR, "
                   "COMPROBANTE y MONTO (y elegí la hoja correcta arriba).")
        return
    with st.expander("Ver lo que leí del Excel (filas visibles)"):
        prev = visibles(importado)
        st.dataframe(
            pd.DataFrame({"Fecha": prev["_fecha"].dt.strftime("%d/%m/%Y"), "Proveedor": prev["proveedor"],
                          "Comprobante": prev["comprobante"], "Moneda": prev["moneda"], "Monto": prev["monto"],
                          "Pagado": prev["pagado"], "Saldo": prev["saldo"],
                          "Fecha de pago": prev["_pago"].dt.strftime("%d/%m/%Y"), "Observación": prev["observacion"]}),
            hide_index=True, use_container_width=True,
            column_config={c: st.column_config.NumberColumn(c, format="localized") for c in ("Monto", "Pagado", "Saldo")})
    vis = visibles(importado)
    ti = totales(vis, hoy)
    n_ocu = int(importado["oculta"].sum())
    st.caption(f"Visibles en el Excel: {int(vis['moneda'].eq('ARS').sum())} facturas en pesos (saldo {pesos(ti['ARS'])}) · "
               f"{int(vis['moneda'].eq('USD').sum())} en USD (saldo {usd(ti['USD'])})"
               + (f" · {n_ocu} filas ocultas (no se traen)." if n_ocu else "."))
    quitar = st.toggle("Borrar de la app las facturas que no estén en el Excel", value=False, key=f"{k}quitar",
                       help="Apagado (lo normal): sólo agrega y actualiza.")
    nuevo, res = sincronizar(d, importado, quitar_faltantes=quitar)
    tarjetas_kpi([
        ("Nuevas", str(res["nuevas"]), "se agregan", "#2E8B57"),
        ("Actualizadas", str(res["actualizadas"]), (f"{res['ocultadas']} se ocultan · " if res["ocultadas"] else "")
         + "cambian pagos / datos", "#C58A32"),
        ("Sin cambios", str(res["iguales"]), "ya estaban iguales", "#8A7F85"),
        ("Sólo en la app", str(res["solo_app"]), "se borran" if quitar else "quedan como están", "#B14E5E"),
    ])
    if res["detalle_cambios"]:
        with st.expander(f"Qué cambia en {res['actualizadas']} factura(s)"):
            st.dataframe(pd.DataFrame(res["detalle_cambios"]), hide_index=True, use_container_width=True)
    if res["nuevas"]:
        n_ = res["detalle_nuevas"]
        st.markdown(html_detalle(n_, "ARS", "Facturas nuevas en pesos") + html_detalle(n_, "USD", "Facturas nuevas en USD"),
                    unsafe_allow_html=True)
    tn = totales(nuevo, hoy)
    st.caption(f"Después de sincronizar: proveedores a pagar {pesos(tn['ARS'])} · USD {usd(tn['USD'])}.")
    hay_algo = res["nuevas"] or res["actualizadas"] or res["quitadas"]
    if st.button("Sincronizar", type="primary", key=f"{k}sync", icon=":material/sync:", disabled=not hay_algo):
        _guardar(nuevo, f"Sincronizado: {res['nuevas']} nuevas, {res['actualizadas']} actualizadas")
    if not hay_algo:
        st.success("La app ya está igual que el Excel.")
