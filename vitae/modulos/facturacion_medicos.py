"""Facturación Médicos: la facturación que Ramiro le hace a cada profesional (CMS, facturación propia, OSDE, OSPE).

- Sólo lo ve el usuario ``rchar`` (vitae/vistas.py → render_facturacion_medicos_pagina).
- Información del profesional, aparte de Vitae: sus pestañas (``fm_*``) no forman parte de los módulos de Vitae,
  ni del Panel general, ni de la IA, ni de los permisos de los demás usuarios.
- Por profesional: total facturado (por canal y por mes), honorarios del facturador (5 % de lo facturado,
  o el porcentaje del profesional), pagos del profesional al facturador y saldo.
- Cada factura guarda su porcentaje: si después se cambia el del profesional, lo ya facturado no se recalcula.
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import date, datetime
from io import BytesIO
from typing import Any, Callable

import pandas as pd

from vitae.core.helpers import leer_fechas_ar, money, nombre_mes_es

TABLA_FACTURAS = "fm_facturas"
TABLA_PAGOS = "fm_pagos"
TABLA_PROFESIONALES = "fm_profesionales"
TABLAS = (TABLA_FACTURAS, TABLA_PAGOS, TABLA_PROFESIONALES)

CANALES = ["CMS", "Facturación propia", "OSDE", "OSPE"]
ESTADOS = ["Pendiente de cobro", "Cobrada", "Anulada"]
MEDIOS = ["Transferencia", "Efectivo", "Cheque", "Otro"]
PORCENTAJE_DEFECTO = 5.0
PROFESIONALES_INICIALES = ["Dr. Adrián Char", "Dra. Graciela Soler"]

COL_FACTURAS = ["id", "profesional", "canal", "periodo", "fecha", "numero", "detalle", "importe", "porcentaje",
                "estado", "fecha_cobro", "observaciones", "creado_en", "codigo", "obra_social", "valor", "cantidad"]
CMS = "CMS"
COL_PAGOS = ["id", "profesional", "fecha", "importe", "medio", "periodo", "comprobante", "observaciones", "creado_en"]
COL_PROFESIONALES = ["id", "nombre", "cuit", "porcentaje", "activo", "observaciones"]
COLUMNAS = {TABLA_FACTURAS: COL_FACTURAS, TABLA_PAGOS: COL_PAGOS, TABLA_PROFESIONALES: COL_PROFESIONALES}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _txt(v: Any) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    t = str(v).strip()
    return "" if t.lower() in {"nan", "none", "nat"} else t


def nuevo_id(prefijo: str) -> str:
    return f"{prefijo}-{uuid.uuid4().hex[:10].upper()}"


def pesos(v: Any) -> str:
    x = float(v or 0)
    s = f"{abs(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-$ {s}" if x < 0 else f"$ {s}"


def _pct(v: Any) -> float:
    t = _txt(v).replace("%", "").replace(",", ".")
    try:
        return float(t) if t else PORCENTAJE_DEFECTO
    except ValueError:
        return PORCENTAJE_DEFECTO


def _activo(v: Any) -> bool:
    t = _txt(v).lower()
    return t not in {"false", "0", "no", "inactivo"}


def _fecha_iso(v: Any) -> str:
    try:
        if v is None or pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(v, (date, datetime, pd.Timestamp)):
        return pd.Timestamp(v).strftime("%Y-%m-%d")
    f = leer_fechas_ar(pd.Series([v])).iloc[0]
    return "" if pd.isna(f) else f.strftime("%Y-%m-%d")


def _periodo(v: Any, fecha: Any = None) -> str:
    t = _txt(v)
    if t:
        m = re.match(r"^(\d{4})[-/](\d{1,2})(?!\d)", t) or None
        if m and 1 <= int(m[2]) <= 12:
            return f"{m[1]}-{int(m[2]):02d}"
        m = re.match(r"^(\d{1,2})[-/](\d{4})$", t)
        if m and 1 <= int(m[1]) <= 12:
            return f"{m[2]}-{int(m[1]):02d}"
        f = leer_fechas_ar(pd.Series([t])).iloc[0]
        if pd.notna(f):
            return str(f.to_period("M"))
    f = leer_fechas_ar(pd.Series([fecha])).iloc[0] if _txt(fecha) else pd.NaT
    return "" if pd.isna(f) else str(f.to_period("M"))


def normalizar(df: pd.DataFrame | None, tabla: str) -> pd.DataFrame:
    """La pestaña con sus columnas (las que falten, vacías; las de más, se conservan al final)."""
    cols = COLUMNAS[tabla]
    x = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
    x = x.rename(columns={c: str(c).strip().lower() for c in x.columns})
    if len(x.columns) == 1 and str(x.columns[0]).lower() == "sin datos":
        x = pd.DataFrame()
    for c in cols:
        if c not in x.columns:
            x[c] = ""
    extra = [c for c in x.columns if c not in cols]
    x = x[cols + extra].copy()
    x = x.astype(object).where(pd.notnull(x), "")
    for c in x.columns:
        x[c] = x[c].map(_txt)
    clave = "nombre" if tabla == TABLA_PROFESIONALES else "profesional"
    x = x[(x[clave] != "") | (x["id"] != "")].reset_index(drop=True)
    sin_id = x["id"] == ""
    if sin_id.any():
        pref = {TABLA_FACTURAS: "FM", TABLA_PAGOS: "PG", TABLA_PROFESIONALES: "PR"}[tabla]
        x.loc[sin_id, "id"] = [nuevo_id(pref) for _ in range(int(sin_id.sum()))]
    return x


def profesionales(df: pd.DataFrame | None) -> pd.DataFrame:
    x = normalizar(df, TABLA_PROFESIONALES)
    if x.empty:
        x = pd.DataFrame([{"id": nuevo_id("PR"), "nombre": n, "cuit": "", "porcentaje": str(PORCENTAJE_DEFECTO),
                           "activo": "true", "observaciones": ""} for n in PROFESIONALES_INICIALES])
        x = normalizar(x, TABLA_PROFESIONALES)
    return x


def nombres_activos(prof: pd.DataFrame) -> list[str]:
    return [n for n, a in zip(prof["nombre"], prof["activo"]) if n and _activo(a)]


def porcentaje_de(prof: pd.DataFrame, nombre: str) -> float:
    m = prof[prof["nombre"] == nombre]
    return _pct(m.iloc[0]["porcentaje"]) if len(m) else PORCENTAJE_DEFECTO


# ---------------------------------------------------------------------------
# Cálculos
# ---------------------------------------------------------------------------
def preparar_facturas(df: pd.DataFrame | None) -> pd.DataFrame:
    x = normalizar(df, TABLA_FACTURAS)
    x["_valor"] = x["valor"].map(money).astype(float)
    x["_cantidad"] = x["cantidad"].map(money).astype(float)
    x["_importe"] = x["importe"].map(money).astype(float)
    # CMS: el importe es valor × cantidad (si el renglón los tiene).
    por_linea = (x["_valor"] > 0) & (x["_cantidad"] > 0)
    x.loc[por_linea, "_importe"] = (x.loc[por_linea, "_valor"] * x.loc[por_linea, "_cantidad"]).round(2)
    x["_pct"] = x["porcentaje"].map(_pct).astype(float)
    x["_fecha"] = leer_fechas_ar(x["fecha"])
    x["_periodo"] = [_periodo(p, f) for p, f in zip(x["periodo"], x["fecha"])]
    x["_anulada"] = x["estado"].str.lower().str.startswith("anul")
    x["_cobrada"] = x["estado"].str.lower().str.startswith("cobrad") & ~x["_anulada"]
    x["_facturado"] = x["_importe"].where(~x["_anulada"], 0.0)
    x["_honorarios"] = (x["_facturado"] * x["_pct"] / 100).round(2)
    return x


def preparar_pagos(df: pd.DataFrame | None) -> pd.DataFrame:
    x = normalizar(df, TABLA_PAGOS)
    x["_importe"] = x["importe"].map(money).astype(float)
    x["_fecha"] = leer_fechas_ar(x["fecha"])
    x["_periodo"] = [_periodo(p, f) for p, f in zip(x["periodo"], x["fecha"])]
    return x


def filtrar(x: pd.DataFrame, *, profesional: str = "", anio: str = "", mes: str = "") -> pd.DataFrame:
    m = pd.Series(True, index=x.index)
    if profesional:
        m &= x["profesional"] == profesional
    if anio:
        m &= x["_periodo"].str[:4] == str(anio)
    if mes:
        m &= x["_periodo"] == mes
    return x[m]


def resumen(fac: pd.DataFrame, pag: pd.DataFrame, nombres: list[str]) -> pd.DataFrame:
    """Por profesional: facturado (y por canal), honorarios del facturador, pagado y saldo."""
    todos = list(dict.fromkeys(list(nombres) + [n for n in fac["profesional"] if n] + [n for n in pag["profesional"] if n]))
    filas = []
    for n in todos:
        f = fac[fac["profesional"] == n]
        p = pag[pag["profesional"] == n]
        fila: dict[str, Any] = {"Profesional": n, "Facturas": int((~f["_anulada"]).sum()),
                                "Facturado": float(f["_facturado"].sum())}
        for c in CANALES:
            fila[c] = float(f.loc[f["canal"] == c, "_facturado"].sum())
        otros = float(f.loc[~f["canal"].isin(CANALES), "_facturado"].sum())
        if otros:
            fila["Otros"] = otros
        fila["Pendiente de cobro"] = float(f.loc[~f["_cobrada"], "_facturado"].sum())
        fila["Honorarios facturador"] = float(f["_honorarios"].sum())
        fila["Pagado"] = float(p["_importe"].sum())
        fila["Saldo"] = round(fila["Honorarios facturador"] - fila["Pagado"], 2)
        filas.append(fila)
    out = pd.DataFrame(filas)
    if not out.empty and "Otros" in out.columns:
        out["Otros"] = out["Otros"].fillna(0.0)
    return out


def por_mes(fac: pd.DataFrame, pag: pd.DataFrame, profesional: str) -> pd.DataFrame:
    """Cuenta corriente mensual del profesional: lo facturado, los honorarios, lo pagado y el saldo acumulado."""
    f = fac[fac["profesional"] == profesional]
    p = pag[pag["profesional"] == profesional]
    meses = sorted({m for m in list(f["_periodo"]) + list(p["_periodo"]) if m})
    filas, acumulado = [], 0.0
    for m in meses:
        fm = f[f["_periodo"] == m]
        hon = float(fm["_honorarios"].sum())
        pagado = float(p.loc[p["_periodo"] == m, "_importe"].sum())
        acumulado = round(acumulado + hon - pagado, 2)
        filas.append({"Mes": nombre_mes_es(m), "_periodo": m, "Facturado": float(fm["_facturado"].sum()),
                      "Honorarios facturador": hon, "Pagado": pagado, "Saldo del mes": round(hon - pagado, 2),
                      "Saldo acumulado": acumulado})
    return pd.DataFrame(filas)


def movimientos(fac: pd.DataFrame, pag: pd.DataFrame, profesional: str) -> pd.DataFrame:
    """Debe (honorarios de cada mes) y haber (cada pago), en orden, con el saldo."""
    filas = []
    for _, r in por_mes(fac, pag, profesional).iterrows():
        if r["Honorarios facturador"]:
            fin = pd.Period(r["_periodo"], freq="M").end_time.normalize()
            filas.append({"_f": fin, "_o": 0, "Fecha": fin.strftime("%d/%m/%Y"),
                          "Concepto": f"Honorarios {r['Mes']} ({pesos(r['Facturado'])} facturado)",
                          "Debe": r["Honorarios facturador"], "Haber": 0.0})
    p = pag[pag["profesional"] == profesional]
    for _, r in p.iterrows():
        f = r["_fecha"]
        concepto = "Pago" + (f" · {r['medio']}" if r["medio"] else "") + (f" · {r['comprobante']}" if r["comprobante"] else "")
        filas.append({"_f": f if pd.notna(f) else pd.Timestamp.max.normalize(), "_o": 1,
                      "Fecha": f.strftime("%d/%m/%Y") if pd.notna(f) else "", "Concepto": concepto,
                      "Debe": 0.0, "Haber": float(r["_importe"])})
    out = pd.DataFrame(filas, columns=["_f", "_o", "Fecha", "Concepto", "Debe", "Haber"])
    if out.empty:
        return out.drop(columns=["_f", "_o"]).assign(Saldo=pd.Series(dtype=float))
    out = out.sort_values(["_f", "_o"], kind="mergesort").reset_index(drop=True)
    out["Saldo"] = (out["Debe"] - out["Haber"]).cumsum().round(2)
    return out.drop(columns=["_f", "_o"])


# ---------------------------------------------------------------------------
# Altas, cambios y bajas (devuelven la pestaña completa para guardar)
# ---------------------------------------------------------------------------
def agregar_factura(raw: pd.DataFrame | None, *, profesional: str, canal: str, fecha: Any, importe: Any,
                    periodo: str = "", numero: str = "", detalle: str = "", porcentaje: float | None = None,
                    estado: str = "Pendiente de cobro", observaciones: str = "") -> tuple[pd.DataFrame | None, str]:
    """(pestaña nueva, error)."""
    x = normalizar(raw, TABLA_FACTURAS)
    imp = money(importe)
    if not _txt(profesional):
        return None, "Elegí el profesional."
    if not _txt(canal):
        return None, "Elegí el canal (CMS, facturación propia, OSDE u OSPE)."
    if imp <= 0:
        return None, "El importe tiene que ser mayor a cero."
    f = _fecha_iso(fecha)
    if not f:
        return None, "Falta la fecha de la factura."
    num = _txt(numero)
    if num:
        rep = x[(x["profesional"] == profesional) & (x["canal"] == canal) & (x["numero"].str.upper() == num.upper())
                & ~x["estado"].str.lower().str.startswith("anul")]
        if len(rep):
            return None, f"La factura {num} de {profesional} ({canal}) ya está cargada."
    fila = {"id": nuevo_id("FM"), "profesional": profesional, "canal": canal, "periodo": _periodo(periodo, f),
            "fecha": f, "numero": num, "detalle": _txt(detalle), "importe": f"{imp:.2f}",
            "porcentaje": f"{(PORCENTAJE_DEFECTO if porcentaje is None else float(porcentaje)):g}",
            "estado": estado or ESTADOS[0], "fecha_cobro": "", "observaciones": _txt(observaciones),
            "creado_en": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    return pd.concat([x, pd.DataFrame([fila])], ignore_index=True), ""


def agregar_cms(raw: pd.DataFrame | None, *, profesional: str, codigo: str, valor: Any, cantidad: Any,
                obra_social: str, fecha: Any, porcentaje: float | None = None,
                estado: str = "Pendiente de cobro") -> tuple[pd.DataFrame | None, str]:
    """Un renglón de liquidación de CMS: código, valor, cantidad, obra social y fecha (importe = valor × cantidad)."""
    x = normalizar(raw, TABLA_FACTURAS)
    val, cant = money(valor), money(cantidad)
    if not _txt(profesional):
        return None, "Elegí el profesional."
    if not _txt(codigo):
        return None, "Falta el código."
    if val <= 0:
        return None, "El valor tiene que ser mayor a cero."
    if cant <= 0:
        return None, "La cantidad tiene que ser mayor a cero."
    if not _txt(obra_social):
        return None, "Falta la obra social."
    f = _fecha_iso(fecha)
    if not f:
        return None, "Falta la fecha."
    fila = {c: "" for c in COL_FACTURAS}
    fila.update({"id": nuevo_id("FM"), "profesional": profesional, "canal": CMS, "periodo": _periodo("", f),
                 "fecha": f, "codigo": _txt(codigo), "obra_social": _txt(obra_social).upper(), "valor": f"{val:.2f}",
                 "cantidad": f"{cant:g}", "importe": f"{val * cant:.2f}",
                 "porcentaje": f"{(PORCENTAJE_DEFECTO if porcentaje is None else float(porcentaje)):g}",
                 "estado": estado or ESTADOS[0], "creado_en": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    return pd.concat([x, pd.DataFrame([fila])], ignore_index=True), ""


def cms_por_obra_social(fac: pd.DataFrame) -> pd.DataFrame:
    """CMS por obra social y por mes (importe liquidado), con total por obra social y por mes."""
    c = fac[(fac["canal"] == CMS) & ~fac["_anulada"] & (fac["_periodo"] != "")]
    if c.empty:
        return pd.DataFrame()
    os_ = c["obra_social"].where(c["obra_social"] != "", "SIN OBRA SOCIAL")
    t = c.assign(_os=os_).pivot_table(index="_os", columns="_periodo", values="_facturado", aggfunc="sum",
                                      fill_value=0.0)
    t = t[sorted(t.columns)]
    t.columns = [nombre_mes_es(m) for m in t.columns]
    t["Total"] = t.sum(axis=1)
    t = t.sort_values("Total", ascending=False)
    t.loc["TOTAL"] = t.sum(axis=0)
    return t.reset_index().rename(columns={"_os": "Obra social"})


def agregar_pago(raw: pd.DataFrame | None, *, profesional: str, fecha: Any, importe: Any, medio: str = "",
                 periodo: str = "", comprobante: str = "", observaciones: str = "") -> tuple[pd.DataFrame | None, str]:
    x = normalizar(raw, TABLA_PAGOS)
    imp = money(importe)
    if not _txt(profesional):
        return None, "Elegí el profesional."
    if imp <= 0:
        return None, "El importe tiene que ser mayor a cero."
    f = _fecha_iso(fecha)
    if not f:
        return None, "Falta la fecha del pago."
    fila = {"id": nuevo_id("PG"), "profesional": profesional, "fecha": f, "importe": f"{imp:.2f}",
            "medio": _txt(medio), "periodo": _periodo(periodo, f), "comprobante": _txt(comprobante),
            "observaciones": _txt(observaciones), "creado_en": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    return pd.concat([x, pd.DataFrame([fila])], ignore_index=True), ""


def aplicar_edicion(raw: pd.DataFrame | None, tabla: str, editada: pd.DataFrame) -> pd.DataFrame:
    """Aplica una tabla editada (columna ``id`` + columnas de la pestaña + ``Borrar``) a la pestaña completa."""
    x = normalizar(raw, tabla)
    if editada is None or editada.empty:
        return x
    e = editada.copy()
    borrar = set(e.loc[e.get("Borrar", pd.Series(False, index=e.index)).fillna(False).astype(bool), "id"])
    x = x[~x["id"].isin(borrar)].reset_index(drop=True)
    pos = {i: k for k, i in enumerate(x["id"])}
    for _, r in e.iterrows():
        k = pos.get(r["id"])
        if k is None:
            continue
        for c in COLUMNAS[tabla]:
            if c in ("id", "creado_en") or c not in e.columns:
                continue
            v = r[c]
            if c in ("fecha", "fecha_cobro"):
                v = _fecha_iso(v)
            elif c in ("importe", "valor"):
                v = f"{money(v):.2f}" if _txt(v) or c == "importe" else ""
            elif c == "cantidad":
                v = f"{money(v):g}" if _txt(v) else ""
            elif c == "porcentaje":
                v = f"{_pct(v):g}"
            elif c == "activo":
                v = "true" if bool(v) and _txt(v).lower() not in {"false", "0", "no"} else "false"
            elif c == "periodo":
                v = _periodo(v, r["fecha"] if "fecha" in e.columns else x.at[k, "fecha"])
            else:
                v = _txt(v)
            x.at[k, c] = v
    if tabla == TABLA_FACTURAS:
        # CMS: con valor y cantidad, el importe es valor × cantidad.
        val, cant = x["valor"].map(money), x["cantidad"].map(money)
        linea = (val > 0) & (cant > 0)
        x.loc[linea, "importe"] = (val[linea] * cant[linea]).map(lambda v: f"{v:.2f}")
        x["obra_social"] = x["obra_social"].str.upper()
        # Cobrada sin fecha de cobro: hoy. Vuelve a pendiente: sin fecha de cobro.
        cob = x["estado"].str.lower().str.startswith("cobrad")
        x.loc[cob & (x["fecha_cobro"] == ""), "fecha_cobro"] = date.today().strftime("%Y-%m-%d")
        x.loc[~cob, "fecha_cobro"] = ""
    return x


def liquidacion_excel(fac: pd.DataFrame, pag: pd.DataFrame, profesional: str, mes: str = "") -> bytes:
    """Excel con la liquidación del profesional (de un mes o de todo): facturas, pagos y resumen."""
    f = filtrar(fac, profesional=profesional, mes=mes)
    p = filtrar(pag, profesional=profesional, mes=mes)
    r = resumen(f, p, [profesional])
    facturas = pd.DataFrame({
        "Fecha": f["_fecha"].dt.strftime("%d/%m/%Y"), "Mes": f["_periodo"], "Canal": f["canal"], "N° factura": f["numero"],
        "Código": f["codigo"], "Obra social": f["obra_social"], "Valor": f["_valor"].where(f["_valor"] > 0),
        "Cantidad": f["_cantidad"].where(f["_cantidad"] > 0),
        "Detalle": f["detalle"], "Estado": f["estado"], "Importe": f["_importe"], "%": f["_pct"],
        "Honorarios facturador": f["_honorarios"]})
    pagos = pd.DataFrame({"Fecha": p["_fecha"].dt.strftime("%d/%m/%Y"), "Mes": p["_periodo"], "Medio": p["medio"],
                          "Comprobante": p["comprobante"], "Importe": p["_importe"], "Observaciones": p["observaciones"]})
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        r.to_excel(w, sheet_name="Resumen", index=False)
        facturas.to_excel(w, sheet_name="Facturas", index=False)
        cms = cms_por_obra_social(f)
        if not cms.empty:
            cms.to_excel(w, sheet_name="CMS por obra social", index=False)
        pagos.to_excel(w, sheet_name="Pagos", index=False)
        if not mes:
            por_mes(fac, pag, profesional).drop(columns=["_periodo"], errors="ignore").to_excel(
                w, sheet_name="Por mes", index=False)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Almacenamiento: planilla propia (secreto FACTURACION_MEDICOS_SHEET_ID) o, si no, pestañas fm_* de la principal
# ---------------------------------------------------------------------------
class Almacen:
    def __init__(self, sheet_id: str = "", leer: Callable[[str], pd.DataFrame] | None = None,
                 guardar: Callable[[str, pd.DataFrame], Any] | None = None):
        self.sheet_id = sheet_id
        self._leer, self._guardar = leer, guardar
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}

    def _hoja(self, tabla: str):
        import gspread

        from vitae.core.database import get_gs_client

        archivo = get_gs_client().open_by_key(self.sheet_id)
        try:
            return archivo.worksheet(tabla)
        except gspread.WorksheetNotFound:
            ws = archivo.add_worksheet(title=tabla, rows=500, cols=len(COLUMNAS[tabla]))
            ws.update([COLUMNAS[tabla]], "A1", value_input_option="RAW")
            return ws

    def leer(self, tabla: str) -> pd.DataFrame:
        c = self._cache.get(tabla)
        if c and time.time() - c[0] < 30:
            return c[1].copy()
        if self.sheet_id:
            valores = self._hoja(tabla).get_all_values()
            df = (pd.DataFrame([v + [""] * (len(valores[0]) - len(v)) for v in valores[1:]], columns=valores[0])
                  if len(valores) > 1 else pd.DataFrame(columns=valores[0] if valores else COLUMNAS[tabla]))
        else:
            df = self._leer(tabla) if self._leer else pd.DataFrame()
        df = normalizar(df, tabla)
        self._cache[tabla] = (time.time(), df)
        return df.copy()

    def guardar(self, tabla: str, df: pd.DataFrame) -> None:
        df = normalizar(df, tabla)
        if self.sheet_id:
            ws = self._hoja(tabla)
            datos = [list(df.columns)] + df.astype(str).values.tolist()
            ws.clear()
            ws.update(datos, "A1", value_input_option="RAW")
        elif self._guardar:
            self._guardar(tabla, df)
        self._cache[tabla] = (time.time(), df)


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------
SECCIONES = ["Resumen", "CMS", "Facturas", "Pagos", "Cuenta corriente", "Profesionales"]


def _filtro_periodo(st: Any, fac: pd.DataFrame, pag: pd.DataFrame, key: str) -> tuple[str, str]:
    periodos = sorted({m for m in list(fac["_periodo"]) + list(pag["_periodo"]) if m}, reverse=True)
    anios = sorted({m[:4] for m in periodos}, reverse=True)
    c1, c2 = st.columns(2)
    anio = c1.selectbox("Año", ["Todos"] + anios, key=f"{key}_anio")
    anio = "" if anio == "Todos" else anio
    meses = [m for m in periodos if not anio or m.startswith(anio)]
    mes = c2.selectbox("Mes", ["Todos"] + meses, key=f"{key}_mes",
                       format_func=lambda m: m if m == "Todos" else nombre_mes_es(m))
    return anio, ("" if mes == "Todos" else mes)


def _tabla_pesos(st: Any, df: pd.DataFrame, columnas_pesos: list[str], **k: Any) -> None:
    cfg = {c: st.column_config.NumberColumn(c, format="$ %.2f") for c in columnas_pesos if c in df.columns}
    st.dataframe(df, hide_index=True, use_container_width=True, column_config=cfg, **k)


def _guardar(st: Any, almacen: Almacen, tabla: str, df: pd.DataFrame, ok: str) -> None:
    try:
        almacen.guardar(tabla, df)
    except Exception as exc:
        st.error(f"No se pudo guardar ({type(exc).__name__}). No se cambió nada.")
        return
    st.session_state["fm_aviso"] = ok
    st.rerun()


def _meses_para_elegir(hoy: date) -> list[str]:
    actual = pd.Period(hoy, freq="M")
    return [str(actual + 1)] + [str(actual - i) for i in range(0, 24)]


def render(st: Any, almacen: Almacen, hoy: date | None = None) -> None:
    hoy = hoy or date.today()
    aviso = st.session_state.pop("fm_aviso", None)
    if aviso:
        st.success(aviso)
    prof = profesionales(almacen.leer(TABLA_PROFESIONALES))
    fac = preparar_facturas(almacen.leer(TABLA_FACTURAS))
    pag = preparar_pagos(almacen.leer(TABLA_PAGOS))
    nombres = nombres_activos(prof)
    seccion = st.segmented_control("Sección", SECCIONES, default="Resumen", key="fm_seccion",
                                   label_visibility="collapsed") or "Resumen"
    if seccion == "Resumen":
        _render_resumen(st, fac, pag, nombres)
    elif seccion == "CMS":
        _render_cms(st, almacen, fac, prof, nombres, hoy)
    elif seccion == "Facturas":
        _render_facturas(st, almacen, fac[fac["canal"] != CMS], prof, nombres, hoy)
    elif seccion == "Pagos":
        _render_pagos(st, almacen, fac, pag, nombres, hoy)
    elif seccion == "Cuenta corriente":
        _render_cuenta(st, fac, pag, nombres)
    else:
        _render_profesionales(st, almacen, prof)


def _render_resumen(st: Any, fac: pd.DataFrame, pag: pd.DataFrame, nombres: list[str]) -> None:
    anio, mes = _filtro_periodo(st, fac, pag, "fm_res")
    f, p = filtrar(fac, anio=anio, mes=mes), filtrar(pag, anio=anio, mes=mes)
    r = resumen(f, p, nombres)
    tot = {k: float(r[k].sum()) if k in r.columns else 0.0
           for k in ("Facturado", "Honorarios facturador", "Pagado", "Saldo", "Pendiente de cobro")}
    c = st.columns(4)
    c[0].metric("Total facturado", pesos(tot["Facturado"]))
    c[1].metric("Honorarios facturador", pesos(tot["Honorarios facturador"]))
    c[2].metric("Pagado por los profesionales", pesos(tot["Pagado"]))
    c[3].metric("Saldo a cobrar", pesos(tot["Saldo"]))
    for _, fila in r.iterrows():
        with st.container(border=True):
            st.markdown(f"#### {fila['Profesional']}")
            k = st.columns(4)
            k[0].metric("Facturado", pesos(fila["Facturado"]), f"{int(fila['Facturas'])} facturas", delta_color="off")
            k[1].metric("Honorarios facturador", pesos(fila["Honorarios facturador"]))
            k[2].metric("Pagado", pesos(fila["Pagado"]))
            k[3].metric("Saldo", pesos(fila["Saldo"]))
            canales = pd.DataFrame([{"Canal": cn, "Facturado": float(fila.get(cn, 0.0) or 0.0)}
                                    for cn in CANALES + (["Otros"] if "Otros" in r.columns else [])])
            _tabla_pesos(st, canales, ["Facturado"])
            if fila["Pendiente de cobro"]:
                st.caption(f"Pendiente de cobro: {pesos(fila['Pendiente de cobro'])}")


def _render_cms(st: Any, almacen: Almacen, fac: pd.DataFrame, prof: pd.DataFrame, nombres: list[str],
                hoy: date) -> None:
    cms = fac[fac["canal"] == CMS]
    obras = sorted({o for o in cms["obra_social"] if o})
    with st.expander("Cargar liquidación CMS", expanded=cms.empty):
        with st.form("fm_nuevo_cms", clear_on_submit=True):
            a, b, c = st.columns(3)
            profesional = a.selectbox("Profesional", nombres, key="fm_cms_prof")
            codigo = b.text_input("Código", key="fm_cms_codigo")
            fecha = c.date_input("Fecha", value=hoy, format="DD/MM/YYYY", key="fm_cms_fecha")
            d, e, g = st.columns(3)
            valor = d.number_input("Valor", min_value=0.0, step=100.0, format="%.2f", key="fm_cms_valor")
            cantidad = e.number_input("Cantidad", min_value=0.0, value=1.0, step=1.0, format="%g", key="fm_cms_cantidad")
            obra = g.selectbox("Obra social", obras + ["Otra…"], key="fm_cms_os")
            otra = st.text_input("Obra social (si es otra)", key="fm_cms_os_otra")
            enviar = st.form_submit_button("Guardar", type="primary", use_container_width=True)
        if enviar:
            nuevo, error = agregar_cms(almacen.leer(TABLA_FACTURAS), profesional=profesional or "", codigo=codigo,
                                       valor=valor, cantidad=cantidad,
                                       obra_social=otra if (obra == "Otra…" or _txt(otra)) else obra, fecha=fecha,
                                       porcentaje=porcentaje_de(prof, profesional or ""))
            if error:
                st.error(error)
            else:
                _guardar(st, almacen, TABLA_FACTURAS, nuevo, "Liquidación CMS guardada.")
    f1, f2 = st.columns(2)
    quien = f1.selectbox("Profesional", ["Todos"] + nombres, key="fm_cms_f_prof")
    os_f = f2.selectbox("Obra social", ["Todas"] + obras, key="fm_cms_f_os")
    anio, mes = _filtro_periodo(st, cms, cms.iloc[0:0], "fm_cms_f")
    x = filtrar(cms, profesional="" if quien == "Todos" else quien, anio=anio, mes=mes)
    if os_f != "Todas":
        x = x[x["obra_social"] == os_f]
    if x.empty:
        st.info("No hay liquidaciones de CMS con estos filtros.")
        return
    c = st.columns(3)
    c[0].metric("Liquidado CMS", pesos(x["_facturado"].sum()))
    c[1].metric("Honorarios facturador", pesos(x["_honorarios"].sum()))
    c[2].metric("Prestaciones", f"{x.loc[~x['_anulada'], '_cantidad'].sum():g}")
    st.markdown("##### Por obra social y mes")
    tabla = cms_por_obra_social(x)
    _tabla_pesos(st, tabla, [col for col in tabla.columns if col != "Obra social"])
    st.markdown("##### Registros")
    x = x.sort_values("_fecha", ascending=False, na_position="last", kind="mergesort")
    vista = pd.DataFrame({
        "id": x["id"], "codigo": x["codigo"], "valor": x["_valor"], "cantidad": x["_cantidad"],
        "obra_social": x["obra_social"], "fecha": x["_fecha"].dt.date, "importe": x["_importe"],
        "profesional": x["profesional"], "estado": x["estado"], "Borrar": False})
    cc = st.column_config
    editada = st.data_editor(
        vista, key="fm_cms_editor", hide_index=True, use_container_width=True, disabled=["id", "importe"],
        column_order=[k for k in vista.columns if k != "id"],
        column_config={
            "codigo": cc.TextColumn("Código"), "valor": cc.NumberColumn("Valor", format="$ %.2f", min_value=0.0),
            "cantidad": cc.NumberColumn("Cantidad", min_value=0.0, format="%g"),
            "obra_social": cc.TextColumn("Obra social"), "fecha": cc.DateColumn("Fecha", format="DD/MM/YYYY"),
            "importe": cc.NumberColumn("Importe", format="$ %.2f"),
            "profesional": cc.SelectboxColumn("Profesional", options=list(dict.fromkeys(nombres + list(x["profesional"])))),
            "estado": cc.SelectboxColumn("Estado", options=ESTADOS), "Borrar": cc.CheckboxColumn("Borrar")})
    if st.button("Guardar cambios", type="primary", key="fm_cms_guardar"):
        e = editada.copy()
        e["periodo"] = ""  # el mes sale de la fecha
        _guardar(st, almacen, TABLA_FACTURAS, aplicar_edicion(almacen.leer(TABLA_FACTURAS), TABLA_FACTURAS, e),
                 "Cambios guardados.")


def _render_facturas(st: Any, almacen: Almacen, fac: pd.DataFrame, prof: pd.DataFrame, nombres: list[str],
                     hoy: date) -> None:
    with st.expander("Cargar factura", expanded=fac.empty):
        with st.form("fm_nueva_factura", clear_on_submit=True):
            a, b, c = st.columns(3)
            profesional = a.selectbox("Profesional", nombres, key="fm_nf_prof")
            canal = b.selectbox("Canal", [c for c in CANALES if c != CMS], key="fm_nf_canal")
            fecha = c.date_input("Fecha de la factura", value=hoy, format="DD/MM/YYYY", key="fm_nf_fecha")
            d, e, g = st.columns(3)
            numero = d.text_input("N° de factura", key="fm_nf_numero")
            importe = e.number_input("Importe", min_value=0.0, step=1000.0, format="%.2f", key="fm_nf_importe")
            periodo = g.selectbox("Mes de la prestación", _meses_para_elegir(hoy), index=1, key="fm_nf_periodo",
                                  format_func=nombre_mes_es)
            h, i = st.columns([2, 1])
            detalle = h.text_input("Detalle", key="fm_nf_detalle")
            estado = i.selectbox("Estado", ESTADOS, key="fm_nf_estado")
            obs = st.text_input("Observaciones", key="fm_nf_obs")
            enviar = st.form_submit_button("Guardar factura", type="primary", use_container_width=True)
        if enviar:
            nuevo, error = agregar_factura(almacen.leer(TABLA_FACTURAS), profesional=profesional or "", canal=canal,
                                           fecha=fecha, importe=importe, periodo=periodo, numero=numero,
                                           detalle=detalle, porcentaje=porcentaje_de(prof, profesional or ""),
                                           estado=estado, observaciones=obs)
            if error:
                st.error(error)
            else:
                _guardar(st, almacen, TABLA_FACTURAS, nuevo, "Factura guardada.")
    f1, f2 = st.columns(2)
    quien = f1.selectbox("Profesional", ["Todos"] + nombres, key="fm_fac_prof")
    canal_f = f2.selectbox("Canal", ["Todos"] + [c for c in CANALES if c != CMS], key="fm_fac_canal")
    anio, mes = _filtro_periodo(st, fac, fac.iloc[0:0], "fm_fac")
    x = filtrar(fac, profesional="" if quien == "Todos" else quien, anio=anio, mes=mes)
    if canal_f != "Todos":
        x = x[x["canal"] == canal_f]
    x = x.sort_values("_fecha", ascending=False, na_position="last", kind="mergesort")
    if x.empty:
        st.info("No hay facturas con estos filtros.")
        return
    c = st.columns(3)
    c[0].metric("Facturado", pesos(x["_facturado"].sum()))
    c[1].metric("Honorarios facturador", pesos(x["_honorarios"].sum()))
    c[2].metric("Pendiente de cobro", pesos(x.loc[~x["_cobrada"], "_facturado"].sum()))
    vista = pd.DataFrame({
        "id": x["id"], "fecha": x["_fecha"].dt.date, "profesional": x["profesional"], "canal": x["canal"],
        "periodo": x["_periodo"], "numero": x["numero"], "detalle": x["detalle"], "importe": x["_importe"],
        "porcentaje": x["_pct"], "honorarios": x["_honorarios"], "estado": x["estado"],
        "fecha_cobro": leer_fechas_ar(x["fecha_cobro"]).dt.date, "observaciones": x["observaciones"], "Borrar": False})
    cc = st.column_config
    editada = st.data_editor(
        vista, key="fm_fac_editor", hide_index=True, use_container_width=True, disabled=["id", "honorarios"],
        column_order=[k for k in vista.columns if k != "id"],
        column_config={
            "fecha": cc.DateColumn("Fecha", format="DD/MM/YYYY"), "profesional": cc.SelectboxColumn(
                "Profesional", options=list(dict.fromkeys(nombres + list(x["profesional"])))),
            "canal": cc.SelectboxColumn("Canal", options=list(dict.fromkeys(CANALES + list(x["canal"])))),
            "periodo": cc.TextColumn("Mes (AAAA-MM)"), "numero": cc.TextColumn("N° factura"),
            "detalle": cc.TextColumn("Detalle"), "importe": cc.NumberColumn("Importe", format="$ %.2f", min_value=0.0),
            "porcentaje": cc.NumberColumn("%", format="%.2f", min_value=0.0, max_value=100.0),
            "honorarios": cc.NumberColumn("Honorarios", format="$ %.2f"),
            "estado": cc.SelectboxColumn("Estado", options=ESTADOS),
            "fecha_cobro": cc.DateColumn("Cobrada el", format="DD/MM/YYYY"),
            "observaciones": cc.TextColumn("Observaciones"), "Borrar": cc.CheckboxColumn("Borrar")})
    if st.button("Guardar cambios", type="primary", key="fm_fac_guardar"):
        _guardar(st, almacen, TABLA_FACTURAS,
                 aplicar_edicion(almacen.leer(TABLA_FACTURAS), TABLA_FACTURAS, editada), "Cambios guardados.")


def _render_pagos(st: Any, almacen: Almacen, fac: pd.DataFrame, pag: pd.DataFrame, nombres: list[str],
                  hoy: date) -> None:
    r = resumen(fac, pag, nombres)
    if not r.empty:
        c = st.columns(max(len(r), 1))
        for col, (_, fila) in zip(c, r.iterrows()):
            col.metric(f"Saldo {fila['Profesional']}", pesos(fila["Saldo"]),
                       f"honorarios {pesos(fila['Honorarios facturador'])} · pagado {pesos(fila['Pagado'])}",
                       delta_color="off")
    with st.expander("Registrar pago del profesional", expanded=pag.empty):
        with st.form("fm_nuevo_pago", clear_on_submit=True):
            a, b, c = st.columns(3)
            profesional = a.selectbox("Profesional", nombres, key="fm_np_prof")
            fecha = b.date_input("Fecha del pago", value=hoy, format="DD/MM/YYYY", key="fm_np_fecha")
            importe = c.number_input("Importe", min_value=0.0, step=1000.0, format="%.2f", key="fm_np_importe")
            d, e, g = st.columns(3)
            medio = d.selectbox("Medio", MEDIOS, key="fm_np_medio")
            periodo = e.selectbox("Corresponde al mes", _meses_para_elegir(hoy), index=1, key="fm_np_periodo",
                                  format_func=nombre_mes_es)
            comprobante = g.text_input("Comprobante", key="fm_np_comp")
            obs = st.text_input("Observaciones", key="fm_np_obs")
            enviar = st.form_submit_button("Guardar pago", type="primary", use_container_width=True)
        if enviar:
            nuevo, error = agregar_pago(almacen.leer(TABLA_PAGOS), profesional=profesional or "", fecha=fecha,
                                        importe=importe, medio=medio, periodo=periodo, comprobante=comprobante,
                                        observaciones=obs)
            if error:
                st.error(error)
            else:
                _guardar(st, almacen, TABLA_PAGOS, nuevo, "Pago registrado.")
    quien = st.selectbox("Profesional", ["Todos"] + nombres, key="fm_pag_prof")
    x = filtrar(pag, profesional="" if quien == "Todos" else quien)
    x = x.sort_values("_fecha", ascending=False, na_position="last", kind="mergesort")
    if x.empty:
        st.info("Todavía no hay pagos registrados.")
        return
    vista = pd.DataFrame({"id": x["id"], "fecha": x["_fecha"].dt.date, "profesional": x["profesional"],
                          "importe": x["_importe"], "medio": x["medio"], "periodo": x["_periodo"],
                          "comprobante": x["comprobante"], "observaciones": x["observaciones"], "Borrar": False})
    cc = st.column_config
    editada = st.data_editor(
        vista, key="fm_pag_editor", hide_index=True, use_container_width=True, disabled=["id"],
        column_order=[k for k in vista.columns if k != "id"],
        column_config={
            "fecha": cc.DateColumn("Fecha", format="DD/MM/YYYY"),
            "profesional": cc.SelectboxColumn("Profesional", options=list(dict.fromkeys(nombres + list(x["profesional"])))),
            "importe": cc.NumberColumn("Importe", format="$ %.2f", min_value=0.0),
            "medio": cc.SelectboxColumn("Medio", options=list(dict.fromkeys(MEDIOS + list(x["medio"])))),
            "periodo": cc.TextColumn("Mes (AAAA-MM)"), "comprobante": cc.TextColumn("Comprobante"),
            "observaciones": cc.TextColumn("Observaciones"), "Borrar": cc.CheckboxColumn("Borrar")})
    if st.button("Guardar cambios", type="primary", key="fm_pag_guardar"):
        _guardar(st, almacen, TABLA_PAGOS, aplicar_edicion(almacen.leer(TABLA_PAGOS), TABLA_PAGOS, editada),
                 "Cambios guardados.")


def _render_cuenta(st: Any, fac: pd.DataFrame, pag: pd.DataFrame, nombres: list[str]) -> None:
    todos = list(dict.fromkeys(nombres + [n for n in fac["profesional"] if n] + [n for n in pag["profesional"] if n]))
    if not todos:
        st.info("Todavía no hay profesionales.")
        return
    quien = st.selectbox("Profesional", todos, key="fm_cc_prof")
    pm = por_mes(fac, pag, quien)
    saldo = float(pm["Saldo acumulado"].iloc[-1]) if len(pm) else 0.0
    st.metric("Saldo a cobrarle", pesos(saldo))
    if pm.empty:
        st.info("Sin facturas ni pagos todavía.")
        return
    st.markdown("##### Por mes")
    _tabla_pesos(st, pm.drop(columns=["_periodo"]),
                 ["Facturado", "Honorarios facturador", "Pagado", "Saldo del mes", "Saldo acumulado"])
    st.markdown("##### Movimientos")
    _tabla_pesos(st, movimientos(fac, pag, quien), ["Debe", "Haber", "Saldo"])
    meses = list(pm["_periodo"])[::-1]
    mes = st.selectbox("Liquidación", ["Todo"] + meses, key="fm_cc_liq",
                       format_func=lambda m: "Todo" if m == "Todo" else nombre_mes_es(m))
    sufijo = "todo" if mes == "Todo" else mes
    st.download_button("Descargar liquidación (Excel)", liquidacion_excel(fac, pag, quien, "" if mes == "Todo" else mes),
                       file_name=f"liquidacion_{quien.replace(' ', '_').replace('.', '')}_{sufijo}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="fm_cc_descarga")


def _render_profesionales(st: Any, almacen: Almacen, prof: pd.DataFrame) -> None:
    vista = pd.DataFrame({"id": prof["id"], "nombre": prof["nombre"], "cuit": prof["cuit"],
                          "porcentaje": prof["porcentaje"].map(_pct), "activo": prof["activo"].map(_activo),
                          "observaciones": prof["observaciones"]})
    cc = st.column_config
    editada = st.data_editor(
        vista, key="fm_prof_editor", hide_index=True, use_container_width=True, num_rows="dynamic",
        column_order=["nombre", "cuit", "porcentaje", "activo", "observaciones"],
        column_config={"nombre": cc.TextColumn("Profesional", required=True), "cuit": cc.TextColumn("CUIT"),
                       "porcentaje": cc.NumberColumn("% facturador", min_value=0.0, max_value=100.0, format="%.2f"),
                       "activo": cc.CheckboxColumn("Activo"), "observaciones": cc.TextColumn("Observaciones")})
    if st.button("Guardar profesionales", type="primary", key="fm_prof_guardar"):
        e = editada.copy()
        e["id"] = [i if _txt(i) else nuevo_id("PR") for i in e.get("id", pd.Series([""] * len(e)))]
        e["porcentaje"] = e["porcentaje"].map(lambda v: f"{_pct(v):g}")
        e["activo"] = e["activo"].map(lambda v: "true" if bool(v) else "false")
        e = e[e["nombre"].map(_txt) != ""]
        _guardar(st, almacen, TABLA_PROFESIONALES, e, "Profesionales guardados.")
