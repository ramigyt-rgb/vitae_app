"""Planes de pagos y préstamos: como la planilla «DEUDAS ASUMIDAS MES A MES».

Cada plan (PP ARCA, PP DGR, Crédito Macro…) es una fila con su número, agrupada por quién lo
asumió (MEDICAL, ADRIAN EN CTA DE MEDICAL → VM; REPRODUCTIVA → VMR), y cada mes es una columna
con la cuota. Verde = pagada, rojo = impaga, blanco = pendiente. TOTALES por mes y lo que queda
por pagar.

Se guarda una fila por cuota en la pestaña planes_pagos_prestamos (columnas compatibles con el
formato anterior, que se sigue leyendo):
    registro_clase   «Cuota mensual» o «Adelanto de cuotas»
    empresa          VM / VMR
    unidad           MEDICAL, ADRIAN EN CTA DE MEDICAL, REPRODUCTIVA…
    acreedor         PP ARCA, PP DGR, Credito Macro…
    identificador    N° de plan o préstamo
    periodo          AAAA-MM
    importe_cuota, estado (Pagada / Pendiente / Impaga), fecha_pago, observaciones

Importar: se lee el Excel bajado del borrador respetando lo oculto (filas ocultas = planes
terminados, se omiten; columnas ocultas = meses anteriores, opcional), los colores, las celdas
combinadas y las fórmulas pegadas; y se controla contra los TOTALES de la planilla.
"""
from __future__ import annotations

import html
import io
import re
import uuid
from datetime import date, datetime
from typing import Any, Callable

import pandas as pd
import streamlit as st

from vitae.core.ui import tarjetas_kpi
from vitae.modulos.pagos_pendientes import _sin_acentos, _texto, nombre_mes, numero, pesos

TABLA = "planes_pagos_prestamos"
COLUMNAS = ["id_registro", "registro_clase", "empresa", "unidad", "acreedor", "identificador", "tipo_financiacion",
            "periodo", "importe_cuota", "estado", "fecha_pago", "observaciones", "fuente", "updated_at"]
CUOTA = "Cuota mensual"
ADELANTO = "Adelanto de cuotas"
PAGADA, PENDIENTE, IMPAGA = "Pagada", "Pendiente", "Impaga"
ESTADOS = [PENDIENTE, PAGADA, IMPAGA]
EMPRESAS = ["VM", "VMR"]
NOMBRE_EMPRESA = {"VM": "Medical (VM)", "VMR": "Reproductiva (VMR)"}
ACREEDORES = ["PP ARCA", "PP DGR", "Credito Macro", "Otro"]
_MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9, "set": 9,
          "oct": 10, "nov": 11, "dic": 12, "jan": 1, "apr": 4, "aug": 8, "dec": 12}
VERDE, ROJO, BLANCO = "#DDEFD5", "#F6C9C9", "#FFFFFF"


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _n(v: Any) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return 0.0 if pd.isna(v) else float(v)
    x = numero(v)
    return float(x) if x is not None else 0.0


def mes_de(v: Any) -> str:
    """'ago-26', 'agosto 2026', 08/2026, 2026-08, una fecha… → '2026-08' ('' si no es un mes)."""
    if v is None:
        return ""
    if isinstance(v, (datetime, date, pd.Timestamp)):
        return "" if pd.isna(v) else f"{v.year:04d}-{v.month:02d}"
    t = _sin_acentos(v).replace(".", "").strip()
    if not t:
        return ""
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(-\d{1,2})?( .*)?", t)
    if m and 1 <= int(m[2]) <= 12:
        return f"{int(m[1]):04d}-{int(m[2]):02d}"
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})( .*)?", t)
    if m and 1 <= int(m[2]) <= 12:
        a = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        return f"{a:04d}-{int(m[2]):02d}"
    m = re.fullmatch(r"(\d{1,2})[/-](\d{4}|\d{2})", t)
    if m and 1 <= int(m[1]) <= 12:
        a = int(m[2]) + (2000 if len(m[2]) == 2 else 0)
        return f"{a:04d}-{int(m[1]):02d}"
    m = re.fullmatch(r"([a-z]{3})[a-z]*[\s/-]*(\d{4}|\d{2})", t)
    if m and m[1] in _MESES:
        a = int(m[2]) + (2000 if len(m[2]) == 2 else 0)
        return f"{a:04d}-{_MESES[m[1]]:02d}"
    return ""


def etiqueta_mes(periodo: str) -> str:
    if not periodo:
        return ""
    a, m = periodo.split("-")
    return f"{['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'][int(m) - 1]}-{a[2:]}"


def empresa_de(*textos: Any) -> str:
    t = " ".join(_sin_acentos(x) for x in textos)
    if "reproduct" in t or re.search(r"\bvmr\b", t):
        return "VMR"
    if "medical" in t or re.search(r"\bvm\b", t):
        return "VM"
    return ""


def tipo_de(acreedor: Any) -> str:
    t = _sin_acentos(acreedor)
    return "Préstamo" if ("credito" in t or "prestamo" in t or "banco" in t) else "Plan de pago"


def _identificador(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return _texto(v)


def _nuevo_id() -> str:
    return uuid.uuid4().hex[:12]


def mes_actual(hoy: date | None = None) -> str:
    h = hoy or date.today()
    return f"{h.year:04d}-{h.month:02d}"


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------
def _col(df: pd.DataFrame, *nombres: str) -> str | None:
    mapa = {re.sub(r"[^a-z0-9]+", "_", _sin_acentos(c)).strip("_"): c for c in df.columns}
    return next((mapa[n] for n in nombres if n in mapa), None)


def normalizar(df: pd.DataFrame | None) -> pd.DataFrame:
    """La pestaña (formato nuevo o el anterior) → una fila por cuota o adelanto, con tipos limpios."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=COLUMNAS)

    def serie(*nombres: str, defecto: Any = "") -> pd.Series:
        c = _col(df, *nombres)
        return df[c] if c else pd.Series([defecto] * len(df), index=df.index, dtype=object)

    if _col(df, "registro_clase", "clase") is None and _col(df, "periodo", "importe_cuota") is None and \
            (_col(df, "saldo") or _col(df, "importe_total")):
        return _desde_resumen(df, serie)
    clase = serie("registro_clase", "clase").map(_texto)
    unidad = serie("unidad", "area", "sector").map(_texto)
    acreedor = serie("acreedor", "entidad", "organismo").map(_texto)
    out = pd.DataFrame({
        "id_registro": serie("id_registro", "id").map(_texto),
        "registro_clase": clase.where(clase != "", CUOTA),
        "empresa": serie("empresa").map(_texto).str.upper(),
        "unidad": unidad,
        "acreedor": acreedor,
        "identificador": serie("identificador", "numero_plan", "nro_plan", "plan").map(_identificador),
        "tipo_financiacion": serie("tipo_financiacion", "tipo").map(_texto),
        "periodo": serie("periodo", "mes", "fecha_cuota", "fecha").map(mes_de),
        "importe_cuota": serie("importe_cuota", "importe", "monto", "cuota").map(_n),
        "estado": serie("estado").map(_texto),
        "fecha_pago": serie("fecha_pago").map(_texto),
        "observaciones": serie("observaciones", "detalle").map(_texto),
        "fuente": serie("fuente").map(_texto),
        "updated_at": serie("updated_at").map(_texto),
    }, index=df.index)
    # Formato anterior: sólo cuotas y adelantos (los «resúmenes» del cuadro se calculan solos).
    out = out[out["registro_clase"].map(_sin_acentos).isin({"cuota mensual", "adelanto de cuotas"})].copy()
    out["registro_clase"] = out["registro_clase"].map(lambda c: ADELANTO if "adelanto" in _sin_acentos(c) else CUOTA)
    pagado = serie("pagado").reindex(out.index).map(_n)
    est = out["estado"].map(_sin_acentos)
    out["estado"] = [
        PAGADA if (e.startswith("pagad") or e in {"pago", "cancelada", "abonada"} or (p > 0 and p >= i - 0.005))
        else IMPAGA if (e.startswith("impag") or "mora" in e or "vencid" in e)
        else PENDIENTE
        for e, p, i in zip(est, pagado, out["importe_cuota"])]
    falta = out["empresa"].isin(["", "VITAE"])
    out.loc[falta, "empresa"] = [empresa_de(u, a) or "VM" for u, a in zip(out.loc[falta, "unidad"], out.loc[falta, "acreedor"])]
    out["tipo_financiacion"] = [t if t in ("Plan de pago", "Préstamo") else tipo_de(a)
                                for t, a in zip(out["tipo_financiacion"], out["acreedor"])]
    sin_id = out["id_registro"] == ""
    out.loc[sin_id, "id_registro"] = [_nuevo_id() for _ in range(int(sin_id.sum()))]
    out = out[(out["registro_clase"] == ADELANTO) | ((out["periodo"] != "") & (out["importe_cuota"].abs() > 0.004))]
    return out[COLUMNAS].reset_index(drop=True)


def _desde_resumen(df: pd.DataFrame, serie: Callable[..., pd.Series]) -> pd.DataFrame:
    """Formato simple (un renglón por plan con saldo y próximo vencimiento): cada plan con saldo queda
    como una cuota pendiente por ese saldo, para no perder la deuda hasta que se cargue el detalle."""
    est = serie("estado").map(_sin_acentos)
    saldo = serie("saldo").map(_n)
    total = serie("importe_total").map(_n)
    filas = []
    for i in df.index:
        if est[i].startswith("finaliz") or est[i].startswith("cancel"):
            continue
        imp = saldo[i] if saldo[i] > 0.004 else (total[i] if not _col(df, "saldo") else 0.0)
        if imp <= 0.004:
            continue
        per = mes_de(serie("proximo_vencimiento", "vencimiento")[i]) or mes_de(serie("fecha")[i]) or mes_actual()
        acr = _texto(serie("acreedor", "entidad")[i])
        uni = _texto(serie("unidad", "detalle")[i])
        filas.append({"id_registro": _nuevo_id(), "registro_clase": CUOTA,
                      "empresa": (_texto(serie("empresa")[i]).upper() if _texto(serie("empresa")[i]).upper() in EMPRESAS
                                  else (empresa_de(uni, acr) or "VM")),
                      "unidad": uni.upper(), "acreedor": acr, "identificador": _texto(serie("identificador", "detalle")[i]),
                      "tipo_financiacion": tipo_de(acr), "periodo": per, "importe_cuota": imp,
                      "estado": IMPAGA if "mora" in est[i] else PENDIENTE, "fecha_pago": "",
                      "observaciones": "Saldo del formato anterior (sin detalle de cuotas)",
                      "fuente": "Formato anterior", "updated_at": ""})
    return pd.DataFrame(filas, columns=COLUMNAS)


def para_guardar(d: pd.DataFrame) -> pd.DataFrame:
    out = d[COLUMNAS].copy()
    out["importe_cuota"] = out["importe_cuota"].map(lambda v: f"{float(v):.2f}")
    out["updated_at"] = out["updated_at"].replace("", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    orden = out.assign(_o=out["registro_clase"].eq(ADELANTO)).sort_values(
        ["_o", "empresa", "unidad", "acreedor", "identificador", "periodo"], kind="stable")
    return orden.drop(columns="_o").reset_index(drop=True)


def cuotas(d: pd.DataFrame) -> pd.DataFrame:
    return d[d["registro_clase"] == CUOTA]


def clave_plan(f: Any) -> str:
    return " | ".join([f["empresa"], f["unidad"], f["acreedor"], f["identificador"]])


def claves(df: pd.DataFrame) -> pd.Series:
    """Clave de plan de cada fila (seguro con tablas vacías)."""
    return pd.Series([clave_plan(f) for _, f in df.iterrows()], index=df.index, dtype=object)


def estado_vista(estado: str, periodo: str, hoy: date | None = None) -> str:
    """Pendiente de un mes que ya pasó = Atrasada."""
    if estado == PENDIENTE and periodo and periodo < mes_actual(hoy):
        return "Atrasada"
    return estado


def planes(d: pd.DataFrame, hoy: date | None = None) -> pd.DataFrame:
    """Un renglón por plan: cuotas, pagado, lo que falta, próxima cuota y cuándo termina."""
    c = cuotas(d)
    cols = ["empresa", "unidad", "acreedor", "identificador", "tipo", "cuotas", "pagadas", "pendientes",
            "atrasadas", "total", "pagado", "pendiente", "atrasado", "cuota_mes", "desde", "hasta", "proxima", "clave"]
    if c.empty:
        return pd.DataFrame(columns=cols)
    ma = mes_actual(hoy)
    x = c.assign(vista=[estado_vista(e, p, hoy) for e, p in zip(c["estado"], c["periodo"])])
    filas = []
    for (emp, uni, acr, ide), g in x.groupby(["empresa", "unidad", "acreedor", "identificador"], sort=False):
        pend = g[g["estado"] != PAGADA]
        atr = g[g["vista"].isin(["Atrasada", IMPAGA])]
        prox = pend[pend["periodo"] >= ma].sort_values("periodo")
        filas.append({
            "empresa": emp, "unidad": uni, "acreedor": acr, "identificador": ide, "tipo": tipo_de(acr),
            "cuotas": len(g), "pagadas": int((g["estado"] == PAGADA).sum()), "pendientes": len(pend),
            "atrasadas": len(atr), "total": float(g["importe_cuota"].sum()),
            "pagado": float(g.loc[g["estado"] == PAGADA, "importe_cuota"].sum()),
            "pendiente": float(pend["importe_cuota"].sum()), "atrasado": float(atr["importe_cuota"].sum()),
            "cuota_mes": float(g.loc[g["periodo"] == ma, "importe_cuota"].sum()),
            "desde": g["periodo"].min(), "hasta": g["periodo"].max(),
            "proxima": prox["periodo"].iloc[0] if len(prox) else "",
            "clave": " | ".join([emp, uni, acr, ide]),
        })
    return pd.DataFrame(filas, columns=cols)


def resumen(d: pd.DataFrame, hoy: date | None = None) -> dict[str, Any]:
    """Para la pantalla y el Panel general."""
    p = planes(d, hoy)
    c = cuotas(d)
    ma = mes_actual(hoy)
    del_mes = c[c["periodo"] == ma]
    out: dict[str, Any] = {
        "hay": not c.empty, "pendiente": float(p["pendiente"].sum()) if len(p) else 0.0,
        "atrasado": float(p["atrasado"].sum()) if len(p) else 0.0,
        "activos": int((p["pendiente"] > 0.004).sum()) if len(p) else 0,
        "con_atraso": int((p["atrasadas"] > 0).sum()) if len(p) else 0,
        "mes": ma, "cuota_mes": float(del_mes["importe_cuota"].sum()),
        "cuota_mes_pagada": float(del_mes.loc[del_mes["estado"] == PAGADA, "importe_cuota"].sum()),
        "por_empresa": {}, "por_tipo": {},
    }
    for e in EMPRESAS:
        pe = p[p["empresa"] == e]
        out["por_empresa"][e] = {"pendiente": float(pe["pendiente"].sum()), "planes": int((pe["pendiente"] > 0.004).sum()),
                                 "cuota_mes": float(pe["cuota_mes"].sum())}
    for t in ("Plan de pago", "Préstamo"):
        out["por_tipo"][t] = float(p.loc[p["tipo"] == t, "pendiente"].sum()) if len(p) else 0.0
    ultimo = c.loc[c["estado"] != PAGADA, "periodo"].max() if len(c) else ""
    out["termina"] = ultimo if isinstance(ultimo, str) else ""
    return out


def grilla(d: pd.DataFrame, empresa: str, meses_v: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(importes, estados) por plan × mes, como la planilla."""
    c = cuotas(d)
    c = c[c["empresa"] == empresa]
    if c.empty:
        return pd.DataFrame(), pd.DataFrame()
    c = c.assign(plan=[clave_plan(f) for _, f in c.iterrows()])
    imp = c.pivot_table(index="plan", columns="periodo", values="importe_cuota", aggfunc="sum")
    est = c.pivot_table(index="plan", columns="periodo", values="estado", aggfunc="first")
    orden = list(dict.fromkeys(c["plan"]))
    imp = imp.reindex(index=orden, columns=meses_v)
    est = est.reindex(index=orden, columns=meses_v)
    return imp, est


# ---------------------------------------------------------------------------
# Altas y cambios
# ---------------------------------------------------------------------------
def cuotas_plan(*, empresa: str, unidad: str, acreedor: str, identificador: str, desde: str, cantidad: int,
                importe: float, variacion: float = 0.0, importes: list[float] | None = None,
                pagadas_hasta: str = "") -> pd.DataFrame:
    """Las cuotas de un plan nuevo: importe fijo (con variación mensual opcional) o una lista de importes."""
    lista = list(importes) if importes else [round(importe + variacion * i, 2) for i in range(max(int(cantidad), 0))]
    p0 = pd.Period(desde, freq="M")
    filas = []
    for i, imp in enumerate(lista):
        per = str(p0 + i)
        filas.append({"id_registro": _nuevo_id(), "registro_clase": CUOTA, "empresa": empresa,
                      "unidad": unidad.strip().upper(), "acreedor": acreedor.strip(), "identificador": identificador.strip(),
                      "tipo_financiacion": tipo_de(acreedor), "periodo": per, "importe_cuota": float(imp),
                      "estado": PAGADA if (pagadas_hasta and per <= pagadas_hasta) else PENDIENTE,
                      "fecha_pago": "", "observaciones": "", "fuente": "Vitae", "updated_at": ""})
    return pd.DataFrame(filas, columns=COLUMNAS)


def agregar(d: pd.DataFrame, nuevas: pd.DataFrame) -> pd.DataFrame:
    return pd.concat([d, nuevas[COLUMNAS]], ignore_index=True) if len(nuevas) else d


def marcar(d: pd.DataFrame, ids: list[str], estado: str, fecha_pago: str = "") -> pd.DataFrame:
    out = d.copy()
    m = out["id_registro"].isin(ids)
    out.loc[m, "estado"] = estado
    out.loc[m, "fecha_pago"] = fecha_pago if estado == PAGADA else ""
    out.loc[m, "updated_at"] = ""
    return out


def renombrar_plan(d: pd.DataFrame, clave: str, **cambios: str) -> pd.DataFrame:
    out = d.copy()
    m = (out["registro_clase"] == CUOTA) & (claves(out) == clave)
    for k, v in cambios.items():
        if v is not None and k in ("empresa", "unidad", "acreedor", "identificador"):
            out.loc[m, k] = v.strip().upper() if k == "unidad" else v.strip()
    if "acreedor" in cambios:
        out.loc[m, "tipo_financiacion"] = tipo_de(cambios["acreedor"])
    return out


def borrar_plan(d: pd.DataFrame, clave: str) -> pd.DataFrame:
    m = (d["registro_clase"] == CUOTA) & (claves(d) == clave)
    return d[~m].reset_index(drop=True)


def aplicar_edicion(d: pd.DataFrame, clave: str, editada: pd.DataFrame) -> pd.DataFrame:
    """Reemplaza las cuotas de un plan por las de la tabla editada (agregar, cambiar o borrar filas)."""
    base = d[(d["registro_clase"] == CUOTA) & (claves(d) == clave)]
    if base.empty:
        return d
    f0 = base.iloc[0]
    nuevas = []
    for _, r in editada.iterrows():
        per = mes_de(r.get("Mes"))
        imp = _n(r.get("Cuota"))
        if not per or abs(imp) < 0.005:
            continue
        nuevas.append({"id_registro": _texto(r.get("id")) or _nuevo_id(), "registro_clase": CUOTA,
                       "empresa": f0["empresa"], "unidad": f0["unidad"], "acreedor": f0["acreedor"],
                       "identificador": f0["identificador"], "tipo_financiacion": f0["tipo_financiacion"],
                       "periodo": per, "importe_cuota": imp,
                       "estado": _texto(r.get("Estado")) if _texto(r.get("Estado")) in ESTADOS else PENDIENTE,
                       "fecha_pago": _texto(r.get("Fecha de pago")), "observaciones": _texto(r.get("Notas")),
                       "fuente": f0["fuente"] or "Vitae", "updated_at": ""})
    resto = d.drop(index=base.index)
    return pd.concat([resto, pd.DataFrame(nuevas, columns=COLUMNAS)], ignore_index=True)


# ---------------------------------------------------------------------------
# Importar la planilla del borrador
# ---------------------------------------------------------------------------
def _color(celda: Any) -> str:
    try:
        f = celda.fill
        if f is None or f.fill_type != "solid" or f.fgColor is None or f.fgColor.type != "rgb":
            return ""
        rgb = str(f.fgColor.rgb or "").upper()[-6:]
        return "" if rgb in {"FFFFFF", "000000", ""} else rgb
    except Exception:
        return ""


def estado_por_color(rgb: str) -> str:
    rgb = (rgb or "").upper()[-6:]
    if not re.fullmatch(r"[0-9A-F]{6}", rgb):
        return ""
    r, g, b = int(rgb[:2], 16), int(rgb[2:4], 16), int(rgb[4:], 16)
    if r > 180 and g < 120 and b < 120:
        return IMPAGA
    if g > r + 8 and g > b + 8:
        return PAGADA
    return ""


def leer_excel(contenido: bytes, hoja: str | None = None) -> tuple[dict[str, Any], list[str]]:
    """Matriz de valores, colores, filas y columnas ocultas (celdas combinadas ya completadas)."""
    from openpyxl import load_workbook

    from vitae.modulos.cuenta_corriente import _Libro

    wb = load_workbook(io.BytesIO(contenido), data_only=True)
    wb_f = load_workbook(io.BytesIO(contenido), data_only=False)
    libro = _Libro(wb, wb_f)
    hojas = [w.title for w in wb.worksheets if getattr(w, "sheet_state", "visible") == "visible"]
    ws = wb[hoja] if hoja in wb.sheetnames else wb[hojas[0]]
    alto, ancho = min(ws.max_row or 0, 2000), min(ws.max_column or 0, 200)
    valores = [[libro.valor(ws.title, r, c) for c in range(1, ancho + 1)] for r in range(1, alto + 1)]
    colores = [[_color(ws.cell(r, c)) for c in range(1, ancho + 1)] for r in range(1, alto + 1)]
    for rango in ws.merged_cells.ranges:
        v = valores[rango.min_row - 1][rango.min_col - 1] if rango.min_row <= alto and rango.min_col <= ancho else None
        for r in range(rango.min_row, min(rango.max_row, alto) + 1):
            for c in range(rango.min_col, min(rango.max_col, ancho) + 1):
                valores[r - 1][c - 1] = v
    filas_ocultas = {r - 1 for r, dim in ws.row_dimensions.items() if getattr(dim, "hidden", False)}
    cols_ocultas: set[int] = set()
    for dim in ws.column_dimensions.values():
        if getattr(dim, "hidden", False) and dim.min:
            cols_ocultas |= set(range(int(dim.min) - 1, int(dim.max or dim.min)))
    return {"valores": valores, "colores": colores, "filas_ocultas": filas_ocultas,
            "cols_ocultas": cols_ocultas, "hoja": ws.title}, hojas


def leer_planilla(m: dict[str, Any], *, incluir_filas_ocultas: bool = False, incluir_meses_ocultos: bool = True,
                  pasados_sin_color_pagados: bool = True, hoy: date | None = None) -> dict[str, Any]:
    """La planilla → cuotas, adelantos y controles (TOTALES por mes y total por empresa)."""
    valores, colores = m["valores"], m["colores"]
    f_ocultas, c_ocultas = m["filas_ocultas"], m["cols_ocultas"]
    ma = mes_actual(hoy)
    cuotas_l: list[dict[str, Any]] = []
    adelantos: list[dict[str, Any]] = []
    controles: list[dict[str, Any]] = []
    totales_emp: list[dict[str, Any]] = []
    omitidas = 0
    r = 0
    alto = len(valores)

    def es_encabezado(fila: list[Any]) -> dict[int, str]:
        meses_f = {j: mes_de(v) for j, v in enumerate(fila) if mes_de(v)}
        return meses_f if len(meses_f) >= 3 else {}

    while r < alto:
        fila = valores[r]
        textos = [_sin_acentos(v) for v in fila]
        if any(t.startswith("adelanto de cuota") for t in textos):
            r = _leer_adelantos(valores, r + 1, f_ocultas, incluir_filas_ocultas, adelantos)
            continue
        meses_f = es_encabezado(fila)
        if not meses_f:
            r += 1
            continue
        primera = min(meses_f)
        ultima_mes = max(meses_f)
        etiquetas = list(range(primera))
        bloque: list[dict[str, Any]] = []
        ocultas_suma: dict[str, float] = {}
        unidad_actual = ""
        r += 1
        while r < alto:
            fila = valores[r]
            txt = [_sin_acentos(v) for v in fila[:primera]]
            if es_encabezado(fila):
                break
            if any(t.startswith("totales") or t == "total" for t in txt):
                tot = {meses_f[j]: _n(fila[j]) for j in meses_f if j < len(fila) and fila[j] not in (None, "")}
                derecha = [(j, v) for j, v in enumerate(fila) if j > ultima_mes]
                etiqueta = next((_texto(v) for j, v in derecha if isinstance(v, str) and "plan" in _sin_acentos(v)), "")
                monto = next((_n(v) for j, v in derecha if isinstance(v, (int, float)) or numero(v) is not None), None)
                controles.append({"filas": bloque, "totales": tot, "etiqueta": etiqueta, "total_derecha": monto,
                                  "ocultas": ocultas_suma,
                                  "meses_visibles": [meses_f[j] for j in meses_f if j not in c_ocultas]})
                r += 1
                break
            lab = [_texto(_identificador(fila[j])) if j < len(fila) and fila[j] is not None else "" for j in etiquetas]
            if lab and lab[0]:
                unidad_actual = lab[0]  # se sigue aunque la fila esté oculta (celdas combinadas)
            if r in f_ocultas and not incluir_filas_ocultas:
                hubo = False
                for j, per in meses_f.items():
                    v = fila[j] if j < len(fila) else None
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) > 0.004:
                        ocultas_suma[per] = ocultas_suma.get(per, 0.0) + float(v)
                        hubo = True
                omitidas += int(hubo)
                r += 1
                continue
            acreedor = lab[1] if len(lab) > 1 else ""
            ident = lab[2] if len(lab) > 2 else ""
            if not (acreedor or ident):
                r += 1
                continue
            empresa = empresa_de(unidad_actual)
            for j, per in meses_f.items():
                if j in c_ocultas and not incluir_meses_ocultos:
                    continue
                v = fila[j] if j < len(fila) else None
                if v in (None, "") or not (isinstance(v, (int, float)) or numero(v) is not None):
                    continue
                imp = _n(v)
                if abs(imp) < 0.005:
                    continue
                est = estado_por_color(colores[r][j] if j < len(colores[r]) else "")
                if not est:
                    est = PAGADA if (pasados_sin_color_pagados and per < ma) else PENDIENTE
                cuotas_l.append({"id_registro": _nuevo_id(), "registro_clase": CUOTA, "empresa": empresa,
                                 "unidad": unidad_actual.upper(), "acreedor": acreedor, "identificador": ident,
                                 "tipo_financiacion": tipo_de(acreedor), "periodo": per, "importe_cuota": round(imp, 2),
                                 "estado": est, "fecha_pago": "", "observaciones": "", "fuente": "Planilla",
                                 "updated_at": "", "_fila": r})
            bloque.append({"fila": r, "unidad": unidad_actual, "empresa": empresa})
            r += 1
    df = pd.DataFrame(cuotas_l, columns=COLUMNAS + ["_fila"])
    # Empresa del bloque: por la unidad, o por la etiqueta «PLANES DE PAGOS VM/VMR» de los totales.
    for ctl in controles:
        filas_b = {b["fila"] for b in ctl["filas"]}
        emp_lbl = empresa_de(ctl["etiqueta"])
        if emp_lbl:
            m_ = df["_fila"].isin(filas_b) & (df["empresa"] == "")
            df.loc[m_, "empresa"] = emp_lbl
        ctl["empresa"] = emp_lbl or next((b["empresa"] for b in ctl["filas"] if b["empresa"]), "")
    df.loc[df["empresa"] == "", "empresa"] = "VM"
    # Controles contra la planilla
    control_rows = []
    for ctl in controles:
        filas_b = {b["fila"] for b in ctl["filas"]}
        x = df[df["_fila"].isin(filas_b)]
        for per in ctl["meses_visibles"]:
            if per not in ctl["totales"]:
                continue
            app = round(float(x.loc[x["periodo"] == per, "importe_cuota"].sum()), 2)
            oc = round(ctl["ocultas"].get(per, 0.0), 2)
            control_rows.append({"Empresa": ctl["empresa"], "Mes": etiqueta_mes(per), "_per": per,
                                 "Planilla": ctl["totales"][per], "Vitae": app, "En filas ocultas": oc,
                                 "Coincide": abs(app + oc - ctl["totales"][per]) < 0.02})
        if ctl["total_derecha"] is not None:
            totales_emp.append({"Empresa": ctl["empresa"], "Etiqueta": ctl["etiqueta"],
                                "Planilla": ctl["total_derecha"], "Vitae": round(float(x["importe_cuota"].sum()), 2),
                                "En filas ocultas": round(sum(ctl["ocultas"].values()), 2)})
    return {"cuotas": df.drop(columns="_fila").reset_index(drop=True), "adelantos": pd.DataFrame(adelantos, columns=COLUMNAS),
            "control": pd.DataFrame(control_rows), "totales": pd.DataFrame(totales_emp), "filas_ocultas_omitidas": omitidas}


def _leer_adelantos(valores: list[list[Any]], r: int, f_ocultas: set[int], incluir_ocultas: bool,
                    salida: list[dict[str, Any]]) -> int:
    """Bloque ADELANTO DE CUOTAS: cada fila con plan y número; los importes se guardan con su encabezado."""
    encabezados: dict[int, str] = {}
    unidad = ""
    vacias = 0
    alto = len(valores)
    while r < alto and vacias < 4:
        fila = valores[r]
        if not any(_texto(v) for v in fila):
            vacias += 1
            r += 1
            continue
        vacias = 0
        textos = [_sin_acentos(v) for v in fila]
        if any(t.startswith("deudas asumidas") or t.startswith("totales") for t in textos[:3]) and not \
                any(isinstance(v, (int, float)) for v in fila[3:]):
            pass
        numeros = {j: v for j, v in enumerate(fila) if j >= 3 and (isinstance(v, (int, float)) or numero(v) is not None)
                   and not isinstance(v, (datetime, date))}
        if not numeros and sum(1 for v in fila if isinstance(v, str) and v.strip()) >= 2:
            encabezados = {j: _texto(v) for j, v in enumerate(fila) if isinstance(v, str) and v.strip()}
            r += 1
            continue
        if r in f_ocultas and not incluir_ocultas:
            r += 1
            continue
        if _texto(fila[0]) if fila else "":
            unidad = _texto(fila[0])
        acreedor = _texto(fila[1]) if len(fila) > 1 else ""
        ident = _identificador(fila[2]) if len(fila) > 2 and fila[2] is not None else ""
        if (acreedor or ident) and numeros:
            partes = [f"{encabezados.get(j, f'col {j + 1}')}: {pesos(_n(v))}" for j, v in numeros.items()]
            salida.append({"id_registro": _nuevo_id(), "registro_clase": ADELANTO, "empresa": empresa_de(unidad) or "VM",
                           "unidad": unidad.upper(), "acreedor": acreedor, "identificador": ident,
                           "tipo_financiacion": tipo_de(acreedor), "periodo": "",
                           "importe_cuota": round(_n(next(iter(numeros.values()))), 2), "estado": "",
                           "fecha_pago": "", "observaciones": " · ".join(partes), "fuente": "Planilla",
                           "updated_at": ""})
        r += 1
    return r


def plan_importacion(actual: pd.DataFrame, leido: dict[str, Any], reemplazar: bool = True) -> pd.DataFrame:
    """Reemplazar: la planilla manda (se conservan las fechas de pago ya registradas en Vitae).
    Si no, sólo se suman los planes/meses que no estaban."""
    nuevo = pd.concat([leido["cuotas"], leido["adelantos"]], ignore_index=True)
    if actual.empty:
        return nuevo[COLUMNAS]
    llave = lambda df: df["empresa"] + "|" + df["unidad"] + "|" + df["acreedor"] + "|" + df["identificador"] + "|" + df["periodo"]  # noqa: E731
    previas = actual[actual["registro_clase"] == CUOTA]
    if reemplazar:
        fp = dict(zip(llave(previas), previas["fecha_pago"]))
        obs = dict(zip(llave(previas), previas["observaciones"]))
        k = llave(nuevo)
        nuevo["fecha_pago"] = [fp.get(x, "") if e == PAGADA else "" for x, e in zip(k, nuevo["estado"])]
        nuevo["observaciones"] = [o or obs.get(x, "") for x, o in zip(k, nuevo["observaciones"])]
        return nuevo[COLUMNAS].reset_index(drop=True)
    ya = set(llave(previas))
    sumar = nuevo[(nuevo["registro_clase"] == CUOTA) & ~llave(nuevo).isin(ya)]
    return pd.concat([actual, sumar[COLUMNAS]], ignore_index=True)


# ---------------------------------------------------------------------------
# Excel como la planilla
# ---------------------------------------------------------------------------
def excel(d: pd.DataFrame, meses_v: list[str]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Planes de pagos y prestamos"
    negrita = Font(bold=True)
    gris = PatternFill("solid", fgColor="E8E8E8")
    relleno = {PAGADA: PatternFill("solid", fgColor="B7E1CD"), IMPAGA: PatternFill("solid", fgColor="FF0000")}
    linea = Side(style="thin", color="999999")
    ws.append(["DEUDAS ASUMIDAS MES A MES"])
    ws["A1"].font = negrita
    ws.append([])
    for emp in EMPRESAS:
        imp, est = grilla(d, emp, meses_v)
        if imp.empty:
            continue
        ws.append(["", "", ""] + [etiqueta_mes(m) for m in meses_v] + ["PENDIENTE"])
        for c in ws[ws.max_row]:
            c.fill, c.font = gris, negrita
        pend = planes(d)
        pend = dict(zip(pend["clave"], pend["pendiente"]))
        for plan in imp.index:
            _, uni, acr, ide = plan.split(" | ")
            ws.append([uni, acr, ide] + [None if pd.isna(v) else float(v) for v in imp.loc[plan]] +
                      [pend.get(plan, 0.0)])
            for j, m in enumerate(meses_v):
                e = est.loc[plan, m]
                if isinstance(e, str) and e in relleno:
                    ws.cell(ws.max_row, 4 + j).fill = relleno[e]
        tot = imp.sum(axis=0, min_count=1)
        ws.append(["TOTALES", "", ""] + [0.0 if pd.isna(v) else float(v) for v in tot] +
                  [sum(pend.get(p, 0.0) for p in imp.index)])
        for c in ws[ws.max_row]:
            c.font, c.border = negrita, Border(top=linea, bottom=linea)
        ws.cell(ws.max_row, 5 + len(meses_v)).value = f"PLANES DE PAGOS {emp}"
        ws.append([])
    for fila in ws.iter_rows(min_row=3):
        for c in fila[3:]:
            if isinstance(c.value, float):
                c.number_format = '"$"#,##0.00'
                c.alignment = Alignment(horizontal="right")
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 14
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------
_CSS = """<style>
.pl-wrap{overflow-x:auto;border:1px solid #E8E3E6;border-radius:12px;background:#fff;margin:.3rem 0 1rem}
.pl-tab{border-collapse:collapse;font-size:.82rem;width:max-content;min-width:100%}
.pl-tab th,.pl-tab td{padding:.32rem .55rem;border-bottom:1px solid #F0ECEE;white-space:nowrap}
.pl-tab th{background:#F4F1F3;color:#5E1A39;font-weight:700;text-align:right;position:sticky;top:0}
.pl-tab th.l,.pl-tab td.l{text-align:left}
.pl-tab td{text-align:right;color:#2B2226}
.pl-tab td.fijo,.pl-tab th.fijo{position:sticky;left:0;background:#fff;z-index:1;border-right:1px solid #E8E3E6}
.pl-tab th.fijo{background:#F4F1F3;z-index:2}
.pl-tab td.pag{background:#DDEFD5}.pl-tab td.imp{background:#F6C9C9;color:#8A1F1F;font-weight:700}
.pl-tab td.atr{background:#FDE7C9}.pl-tab td.act{outline:2px solid #8C2853;outline-offset:-2px}
.pl-tab tr.tot td{font-weight:800;border-top:2px solid #CFC6CB;background:#FAF7F8}
.pl-tab td.pend{font-weight:700;color:#8C2853}
.pl-uni{font-weight:700;color:#3A1426}.pl-sub{color:#8A7F85;font-size:.74rem}
.pl-ley{display:flex;gap:1rem;flex-wrap:wrap;font-size:.78rem;color:#6B5F66;margin:.2rem 0 .4rem}
.pl-ley i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:4px;vertical-align:-2px;border:1px solid #ddd}
.pl-h{font-weight:800;color:#5E1A39;margin:.6rem 0 .2rem;font-size:1rem}
</style>"""


def html_grilla(d: pd.DataFrame, empresa: str, meses_v: list[str], hoy: date | None = None) -> str:
    imp, est = grilla(d, empresa, meses_v)
    if imp.empty:
        return f'<div class="pl-sub">Sin planes de {html.escape(NOMBRE_EMPRESA[empresa])}.</div>'
    ma = mes_actual(hoy)
    pl = planes(d, hoy).set_index("clave")
    e = html.escape
    cab = ('<tr><th class="l fijo">Plan</th>' + "".join(f'<th>{e(etiqueta_mes(m))}</th>' for m in meses_v) +
           '<th>Pendiente</th></tr>')
    cuerpo = []
    for plan in imp.index:
        _, uni, acr, ide = plan.split(" | ")
        celdas = []
        for m in meses_v:
            v = imp.loc[plan, m]
            if pd.isna(v):
                celdas.append('<td></td>')
                continue
            es = est.loc[plan, m]
            vista = estado_vista(es, m, hoy)
            clase = {PAGADA: "pag", IMPAGA: "imp", "Atrasada": "atr"}.get(vista, "")
            if m == ma:
                clase += " act"
            celdas.append(f'<td class="{clase}" title="{e(vista)}">{pesos(v)}</td>')
        pend = float(pl.loc[plan, "pendiente"]) if plan in pl.index else 0.0
        cuerpo.append(f'<tr><td class="l fijo"><span class="pl-uni">{e(acr)} {e(ide)}</span>'
                      f'<div class="pl-sub">{e(uni)}</div></td>{"".join(celdas)}'
                      f'<td class="pend">{pesos(pend) if pend else "—"}</td></tr>')
    tot = imp.sum(axis=0, min_count=1)
    pend_tot = float(pl.loc[[p for p in imp.index if p in pl.index], "pendiente"].sum())
    cuerpo.append('<tr class="tot"><td class="l fijo">TOTALES</td>' +
                  "".join(f'<td>{pesos(v) if pd.notna(v) else ""}</td>' for v in tot) +
                  f'<td class="pend">{pesos(pend_tot)}</td></tr>')
    return f'<div class="pl-wrap"><table class="pl-tab">{cab}{"".join(cuerpo)}</table></div>'


LEYENDA = ('<div class="pl-ley"><span><i style="background:#DDEFD5"></i>Pagada</span>'
           '<span><i style="background:#F6C9C9"></i>Impaga</span><span><i style="background:#FDE7C9"></i>Atrasada '
           '(pendiente de un mes que ya pasó)</span><span><i style="background:#fff"></i>Pendiente</span>'
           '<span><i style="background:#fff;outline:2px solid #8C2853"></i>Mes actual</span></div>')

SECCIONES = ["Planilla", "Este mes", "Planes", "Cargar plan", "Editar cuotas", "Adelantos", "Importar"]


def render_planes(raw: pd.DataFrame | None, *, guardar: Callable[[pd.DataFrame], Any],
                  selector: Callable[..., str] | None = None,
                  mostrar_error: Callable[[str, Exception], None] | None = None) -> None:
    d = normalizar(raw)
    hoy = date.today()
    ma = mes_actual(hoy)
    k = "pl_"
    st.markdown(_CSS, unsafe_allow_html=True)

    def _guardar(nuevo: pd.DataFrame, aviso: str) -> None:
        try:
            guardar(para_guardar(nuevo))
        except Exception as exc:
            (mostrar_error or (lambda m, e: st.error(f"{m} {e}")))("No se pudieron guardar los cambios.", exc)
            return
        st.session_state[f"{k}rev"] = int(st.session_state.get(f"{k}rev", 0)) + 1
        st.toast(aviso, icon=":material/check_circle:")
        st.rerun()

    rev = int(st.session_state.get(f"{k}rev", 0))
    r = resumen(d, hoy)
    tarjetas_kpi([
        ("Falta pagar", pesos(r["pendiente"], 0), f"{r['activos']} planes activos", "#8C2853"),
        ("VM · Medical", pesos(r["por_empresa"]["VM"]["pendiente"], 0),
         f"{r['por_empresa']['VM']['planes']} planes", "#4F7CAC"),
        ("VMR · Reproductiva", pesos(r["por_empresa"]["VMR"]["pendiente"], 0),
         f"{r['por_empresa']['VMR']['planes']} planes", "#5E9C76"),
        (f"Cuotas de {nombre_mes(ma).split()[0].lower()}", pesos(r["cuota_mes"], 0),
         f"Pagado {pesos(r['cuota_mes_pagada'], 0)}", "#C9895B"),
    ])
    if r["atrasado"] > 0.5:
        st.markdown(f'<div style="background:#FFF3F3;border-left:4px solid #BD4B4B;border-radius:10px;padding:.5rem .8rem;'
                    f'margin:.2rem 0 .6rem">⚠️ {r["con_atraso"]} plan(es) con cuotas impagas o atrasadas: '
                    f'<b>{pesos(r["atrasado"])}</b>.</div>', unsafe_allow_html=True)
    if r["por_tipo"]["Préstamo"] > 0.5:
        st.caption(f"De lo que falta pagar: planes de pago {pesos(r['por_tipo']['Plan de pago'], 0)} · "
                   f"préstamos {pesos(r['por_tipo']['Préstamo'], 0)}"
                   + (f" · la última cuota pendiente es de {nombre_mes(r['termina']).lower()}." if r["termina"] else "."))

    if selector:
        seccion = selector("Sección", SECCIONES, key=f"{k}seccion")
    else:
        seccion = st.segmented_control("Sección", SECCIONES, default=SECCIONES[0], key=f"{k}seccion",
                                       label_visibility="collapsed") or SECCIONES[0]
    todos_meses = sorted(set(cuotas(d)["periodo"])) if not d.empty else []

    # ---- Planilla ----------------------------------------------------------
    if seccion == "Planilla":
        if not todos_meses:
            st.info("Todavía no hay planes. Cargalos en «Cargar plan» o traé la planilla en «Importar».")
            return
        p_ma = pd.Period(ma, freq="M")
        opciones = {"Desde 3 meses atrás": (str(p_ma - 3), None), "Desde este mes": (ma, None),
                    "Todo": (todos_meses[0], None), "Últimos 12 meses": (str(p_ma - 11), ma)}
        c1, c2 = st.columns([1.3, 1])
        with c1:
            rango = st.pills("Meses", list(opciones), default="Desde 3 meses atrás", key=f"{k}rango") or \
                "Desde 3 meses atrás"
        desde, hasta = opciones[rango]
        meses_v = [m for m in todos_meses if m >= desde and (hasta is None or m <= hasta)]
        with c2:
            st.download_button("Descargar Excel (como la planilla)", data=excel(d, todos_meses),
                               file_name=f"planes_de_pago_{ma}.xlsx", key=f"{k}xls", icon=":material/download:",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True)
        st.markdown(LEYENDA, unsafe_allow_html=True)
        for emp in EMPRESAS:
            st.markdown(f'<div class="pl-h">{html.escape(NOMBRE_EMPRESA[emp])} · falta pagar '
                        f'{pesos(r["por_empresa"][emp]["pendiente"])}</div>', unsafe_allow_html=True)
            st.markdown(html_grilla(d, emp, meses_v, hoy), unsafe_allow_html=True)
        return

    # ---- Este mes ----------------------------------------------------------
    if seccion == "Este mes":
        opciones_m = sorted(set(todos_meses) | {ma}, reverse=True)
        mes = st.selectbox("Mes", opciones_m, index=opciones_m.index(ma), key=f"{k}mes", format_func=nombre_mes)
        c = cuotas(d)
        x = c[c["periodo"] == mes].copy()
        atras = c[(c["periodo"] < mes) & (c["estado"] != PAGADA)]
        if x.empty and atras.empty:
            st.info("No hay cuotas en ese mes.")
            return
        st.caption("Tildá las cuotas pagadas y guardá. También aparecen las de meses anteriores que siguen sin pagar.")
        x = pd.concat([atras.assign(_atr=True), x.assign(_atr=False)])
        tabla = pd.DataFrame({
            "id": x["id_registro"], "Pagada": x["estado"] == PAGADA,
            "Empresa": x["empresa"], "Plan": x["acreedor"] + " " + x["identificador"], "Unidad": x["unidad"],
            "Mes": x["periodo"].map(etiqueta_mes), "Cuota": x["importe_cuota"],
            "Estado": [estado_vista(e, p, hoy) for e, p in zip(x["estado"], x["periodo"])],
            "Fecha de pago": x["fecha_pago"],
        }).reset_index(drop=True)
        ed = st.data_editor(tabla, hide_index=True, use_container_width=True, key=f"{k}ed_mes_{mes}_{rev}",
                            disabled=["id", "Empresa", "Plan", "Unidad", "Mes", "Cuota", "Estado"],
                            column_config={"id": None, "Cuota": st.column_config.NumberColumn(format="localized"),
                                           "Fecha de pago": st.column_config.TextColumn(help="dd/mm/aaaa")})
        tot = float(tabla["Cuota"].sum())
        st.caption(f"Total {pesos(tot)} · marcado como pagado {pesos(float(ed.loc[ed['Pagada'], 'Cuota'].sum()))}")
        c1, c2 = st.columns(2)
        if c1.button("Guardar", type="primary", key=f"{k}ok_mes", icon=":material/save:", use_container_width=True):
            nuevo = d.copy()
            hoy_t = hoy.strftime("%d/%m/%Y")
            for _, fila in ed.iterrows():
                m_ = nuevo["id_registro"] == fila["id"]
                ya = bool(nuevo.loc[m_, "estado"].eq(PAGADA).any())
                if fila["Pagada"] and not ya:
                    nuevo = marcar(nuevo, [fila["id"]], PAGADA, _texto(fila["Fecha de pago"]) or hoy_t)
                elif not fila["Pagada"] and ya:
                    nuevo = marcar(nuevo, [fila["id"]], PENDIENTE)
                elif fila["Pagada"]:
                    nuevo.loc[m_, "fecha_pago"] = _texto(fila["Fecha de pago"])
            _guardar(nuevo, "Cuotas actualizadas")
        if c2.button("Marcar todas como pagadas", key=f"{k}todas_mes", icon=":material/done_all:",
                     use_container_width=True):
            _guardar(marcar(d, list(x.loc[~x["_atr"], "id_registro"]), PAGADA, hoy.strftime("%d/%m/%Y")),
                     f"Cuotas de {nombre_mes(mes).lower()} pagadas")
        return

    # ---- Planes ------------------------------------------------------------
    if seccion == "Planes":
        p = planes(d, hoy)
        if p.empty:
            st.info("Todavía no hay planes.")
            return
        vista = pd.DataFrame({
            "Empresa": p["empresa"], "Unidad": p["unidad"], "Plan": p["acreedor"], "Número": p["identificador"],
            "Tipo": p["tipo"], "Cuotas": p["pagadas"].astype(str) + " de " + p["cuotas"].astype(str),
            "Cuota de este mes": p["cuota_mes"].map(lambda v: pesos(v) if v else ""),
            "Pagado": p["pagado"].map(pesos), "Falta pagar": p["pendiente"].map(pesos),
            "Atrasado": p["atrasado"].map(lambda v: pesos(v) if v else ""),
            "Desde": p["desde"].map(etiqueta_mes), "Termina": p["hasta"].map(etiqueta_mes),
        })
        filtro = st.pills("Mostrar", ["Activos", "Todos", "Terminados"], default="Activos", key=f"{k}filtro") or "Activos"
        if filtro == "Activos":
            vista = vista[p["pendiente"] > 0.004]
        elif filtro == "Terminados":
            vista = vista[p["pendiente"] <= 0.004]
        st.dataframe(vista, hide_index=True, use_container_width=True)
        st.markdown("##### Modificar un plan")
        elegido = st.selectbox("Plan", list(p["clave"]), key=f"{k}plan_mod",
                               format_func=lambda c_: c_.replace(" | ", " · "))
        f0 = p[p["clave"] == elegido].iloc[0]
        c1, c2, c3, c4 = st.columns([0.8, 1.4, 1, 1])
        emp = c1.selectbox("Empresa", EMPRESAS, index=EMPRESAS.index(f0["empresa"]) if f0["empresa"] in EMPRESAS else 0,
                           key=f"{k}m_emp_{elegido}")
        uni = c2.text_input("Unidad", value=f0["unidad"], key=f"{k}m_uni_{elegido}")
        acr = c3.text_input("Plan", value=f0["acreedor"], key=f"{k}m_acr_{elegido}")
        ide = c4.text_input("Número", value=f0["identificador"], key=f"{k}m_ide_{elegido}")
        b1, b2 = st.columns(2)
        if b1.button("Guardar cambios del plan", type="primary", key=f"{k}m_ok", icon=":material/save:",
                     use_container_width=True):
            _guardar(renombrar_plan(d, elegido, empresa=emp, unidad=uni, acreedor=acr, identificador=ide),
                     "Plan actualizado")
        confirmar = st.session_state.get(f"{k}borrar_conf") == elegido
        if b2.button("Confirmar: borrar el plan" if confirmar else "Borrar el plan", key=f"{k}m_del",
                     icon=":material/delete:", use_container_width=True):
            if confirmar:
                st.session_state.pop(f"{k}borrar_conf", None)
                _guardar(borrar_plan(d, elegido), "Plan borrado")
            else:
                st.session_state[f"{k}borrar_conf"] = elegido
                st.rerun()
        if confirmar:
            st.warning("Tocá de nuevo para borrar el plan y todas sus cuotas.")
        return

    # ---- Cargar plan -------------------------------------------------------
    if seccion == "Cargar plan":
        unidades = sorted(set(cuotas(d)["unidad"])) or ["MEDICAL", "REPRODUCTIVA"]
        c1, c2, c3, c4 = st.columns([0.8, 1.4, 1, 1])
        emp = c1.selectbox("Empresa", EMPRESAS, key=f"{k}n_emp_{rev}")
        uni_sel = c2.selectbox("Unidad", unidades + ["➕ Otra"], key=f"{k}n_uni_{rev}")
        uni = c2.text_input("Nueva unidad", key=f"{k}n_uni_txt_{rev}") if uni_sel == "➕ Otra" else uni_sel
        acr_sel = c3.selectbox("Plan", ACREEDORES, key=f"{k}n_acr_{rev}")
        acr = c3.text_input("¿Cuál?", key=f"{k}n_acr_txt_{rev}") if acr_sel == "Otro" else acr_sel
        ide = c4.text_input("Número", key=f"{k}n_ide_{rev}", placeholder="Ej.: V553108")
        c5, c6, c7, c8 = st.columns(4)
        opciones_m = [str(pd.Period(ma, freq="M") + i) for i in range(-24, 25)]
        desde = c5.selectbox("Primera cuota", opciones_m, index=24, key=f"{k}n_desde_{rev}", format_func=etiqueta_mes)
        cant = c6.number_input("Cantidad de cuotas", min_value=1, max_value=240, value=12, key=f"{k}n_cant_{rev}")
        imp = c7.number_input("Cuota", min_value=0.0, value=0.0, step=1000.0, format="%.2f", key=f"{k}n_imp_{rev}")
        var = c8.number_input("Cambia por mes", value=0.0, step=10.0, format="%.2f", key=f"{k}n_var_{rev}",
                              help="Si la cuota sube o baja un monto fijo cada mes (p. ej. +99,95).")
        with st.expander("O pegar los importes de cada cuota", icon=":material/content_paste:"):
            pegado = st.text_area("Un importe por línea (o separados por espacios)", key=f"{k}n_pegar_{rev}",
                                  placeholder="$13.083,09\n$12.806,90\n$12.530,71")
        lista = [x for x in (numero(t) for t in re.split(r"[\n\t;]+|\s{2,}", pegado or "") if t.strip()) if x]
        pagadas = st.selectbox("¿Hay cuotas ya pagadas?", ["Ninguna"] + [m for m in opciones_m if m >= desde][:int(cant)],
                               key=f"{k}n_pag_{rev}", format_func=lambda m: m if m == "Ninguna" else f"Pagadas hasta {etiqueta_mes(m)}")
        nuevas = cuotas_plan(empresa=emp, unidad=uni or "", acreedor=acr or "", identificador=ide or "", desde=desde,
                             cantidad=int(cant), importe=float(imp), variacion=float(var), importes=lista or None,
                             pagadas_hasta="" if pagadas == "Ninguna" else pagadas)
        if len(nuevas) and nuevas["importe_cuota"].abs().sum() > 0:
            st.caption(f"{len(nuevas)} cuotas de {etiqueta_mes(nuevas['periodo'].iloc[0])} a "
                       f"{etiqueta_mes(nuevas['periodo'].iloc[-1])} · total {pesos(nuevas['importe_cuota'].sum())}")
        listo = bool(uni and acr and ide and len(nuevas) and nuevas["importe_cuota"].abs().sum() > 0)
        existe = listo and bool((claves(cuotas(d)) == " | ".join([emp, (uni or "").upper(), acr, ide])).any())
        if existe:
            st.warning("Ese plan ya existe: las cuotas se suman a las que tiene (usá «Editar cuotas» para cambiarlas).")
        if st.button("Guardar plan", type="primary", disabled=not listo, key=f"{k}n_ok", icon=":material/add:"):
            _guardar(agregar(d, nuevas), f"Plan {acr} {ide} cargado")
        return

    # ---- Editar cuotas -----------------------------------------------------
    if seccion == "Editar cuotas":
        p = planes(d, hoy)
        if p.empty:
            st.info("Todavía no hay planes.")
            return
        elegido = st.selectbox("Plan", list(p["clave"]), key=f"{k}e_plan", format_func=lambda c_: c_.replace(" | ", " · "))
        base = cuotas(d)
        base = base[claves(base) == elegido].sort_values("periodo")
        tabla = pd.DataFrame({"id": base["id_registro"], "Mes": base["periodo"], "Cuota": base["importe_cuota"],
                              "Estado": base["estado"], "Fecha de pago": base["fecha_pago"],
                              "Notas": base["observaciones"]}).reset_index(drop=True)
        st.caption("Cambiá importes, estados o meses; agregá filas abajo (Mes como AAAA-MM) o borrá las que sobran.")
        ed = st.data_editor(tabla, hide_index=True, use_container_width=True, num_rows="dynamic",
                            key=f"{k}e_ed_{elegido}_{rev}",
                            column_config={"id": None,
                                           "Mes": st.column_config.TextColumn(help="AAAA-MM", required=True),
                                           "Cuota": st.column_config.NumberColumn(format="localized", required=True),
                                           "Estado": st.column_config.SelectboxColumn(options=ESTADOS, required=True),
                                           "Fecha de pago": st.column_config.TextColumn(help="dd/mm/aaaa")})
        if st.button("Guardar cuotas", type="primary", key=f"{k}e_ok", icon=":material/save:"):
            _guardar(aplicar_edicion(d, elegido, ed), "Cuotas guardadas")
        return

    # ---- Adelantos ---------------------------------------------------------
    if seccion == "Adelantos":
        a = d[d["registro_clase"] == ADELANTO]
        st.caption("El bloque ADELANTO DE CUOTAS de la planilla (se trae al importar).")
        if a.empty:
            st.info("No hay adelantos cargados.")
            return
        st.dataframe(pd.DataFrame({"Empresa": a["empresa"], "Unidad": a["unidad"], "Plan": a["acreedor"],
                                   "Número": a["identificador"], "Importe": a["importe_cuota"].map(pesos),
                                   "Detalle": a["observaciones"]}), hide_index=True, use_container_width=True)
        return

    # ---- Importar ----------------------------------------------------------
    st.caption("Subí la planilla bajada del borrador (Archivo → Descargar → Microsoft Excel .xlsx). Se leen los "
               "bloques de meses (MEDICAL / ADRIAN EN CTA DE MEDICAL → VM, REPRODUCTIVA → VMR), los colores "
               "(verde pagada, rojo impaga), las celdas combinadas, las fórmulas pegadas y el bloque ADELANTO DE "
               "CUOTAS. Se controla contra los TOTALES de la planilla antes de guardar.")
    archivo = st.file_uploader("Planilla", type=["xlsx", "xlsm"], key=f"{k}archivo", label_visibility="collapsed")
    if archivo is None:
        return
    contenido = archivo.getvalue()
    try:
        m0, hojas = leer_excel(contenido)
    except Exception as exc:
        st.error(f"No pude leer el archivo: {exc}")
        return
    hoja = st.selectbox("Hoja", hojas, index=next((i for i, h in enumerate(hojas) if "plan" in _sin_acentos(h)), 0),
                        key=f"{k}hoja") if len(hojas) > 1 else hojas[0]
    m = m0 if hoja == m0["hoja"] else leer_excel(contenido, hoja)[0]
    o1, o2, o3 = st.columns(3)
    filas_oc = o1.toggle("Incluir filas ocultas", value=False, key=f"{k}imp_filas",
                         help="Las filas ocultas suelen ser planes terminados.")
    meses_oc = o2.toggle("Incluir meses ocultos", value=True, key=f"{k}imp_meses",
                         help="Las columnas ocultas suelen ser meses anteriores: dan el historial de cada plan.")
    pasados = o3.toggle("Meses pasados sin color = pagados", value=True, key=f"{k}imp_pasados",
                        help="En la planilla, lo impago se marca en rojo.")
    leido = leer_planilla(m, incluir_filas_ocultas=filas_oc, incluir_meses_ocultos=meses_oc,
                          pasados_sin_color_pagados=pasados, hoy=hoy)
    cu = leido["cuotas"]
    if cu.empty:
        st.error("No encontré bloques de meses (una fila con ago-26, sep-26…) con planes debajo.")
        return
    np_ = cu.groupby(["empresa", "unidad", "acreedor", "identificador"]).ngroups
    st.success(f"{np_} planes · {len(cu)} cuotas · {len(leido['adelantos'])} adelantos"
               + (f" · {leido['filas_ocultas_omitidas']} filas ocultas omitidas" if leido["filas_ocultas_omitidas"] else ""))
    ctl = leido["control"]
    if not ctl.empty:
        malos = ctl[~ctl["Coincide"]]
        if malos.empty:
            oc_tot = float(ctl["En filas ocultas"].sum())
            st.markdown(f"✅ Los TOTALES de la planilla coinciden en los {len(ctl)} meses visibles"
                        + (f" (incluyen {pesos(oc_tot)} de filas ocultas: planes terminados que no se importan)."
                           if oc_tot > 0.5 else "."))
        else:
            st.warning(f"{len(malos)} mes(es) no coinciden con los TOTALES de la planilla.")
            st.dataframe(malos.drop(columns=["_per", "Coincide"]).assign(
                Planilla=lambda x: x["Planilla"].map(pesos), Vitae=lambda x: x["Vitae"].map(pesos),
                **{"En filas ocultas": lambda x: x["En filas ocultas"].map(pesos)}),
                hide_index=True, use_container_width=True)
    tot = leido["totales"]
    if not tot.empty:
        st.dataframe(tot.assign(Coincide=[("✓" if abs(a - b - c) < 0.02 else "≠")
                                          for a, b, c in zip(tot["Planilla"], tot["Vitae"], tot["En filas ocultas"])],
                                Planilla=tot["Planilla"].map(pesos), Vitae=tot["Vitae"].map(pesos),
                                **{"En filas ocultas": tot["En filas ocultas"].map(pesos)}),
                     hide_index=True, use_container_width=True)
        st.caption("Total de la derecha de la planilla contra todas las cuotas del bloque (importadas + filas ocultas). "
                   "Si no coincide, suele ser porque la planilla suma meses fuera de la grilla.")
    prev = normalizar(pd.concat([leido["cuotas"], leido["adelantos"]], ignore_index=True))
    meses_prev = sorted(set(cuotas(prev)["periodo"]))
    with st.expander("Vista previa", expanded=True, icon=":material/visibility:"):
        ventana = [x for x in meses_prev if x >= str(pd.Period(ma, freq="M") - 3)][:15]
        st.markdown(LEYENDA, unsafe_allow_html=True)
        for emp in EMPRESAS:
            st.markdown(f'<div class="pl-h">{html.escape(NOMBRE_EMPRESA[emp])}</div>', unsafe_allow_html=True)
            st.markdown(html_grilla(prev, emp, ventana, hoy), unsafe_allow_html=True)
    modo = st.radio("Cómo guardar", ["Reemplazar todo por la planilla", "Sólo sumar lo que falta"], horizontal=True,
                    key=f"{k}imp_modo")
    if st.button("Importar", type="primary", key=f"{k}imp_ok", icon=":material/upload_file:"):
        _guardar(plan_importacion(d, leido, reemplazar=modo.startswith("Reemplazar")), "Planilla importada")
