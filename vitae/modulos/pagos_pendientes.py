"""Pagos pendientes Vitae: la planilla mensual de lo que hay que pagar.

Replica la planilla que se completa todos los meses (concepto, período,
monto, pagado, saldo, agrupado por categoría) y le suma:

- qué caja se hace cargo de cada gasto (VMR, VM o Gine Vitae);
- cuánto tiene disponible cada caja en el mes y hasta dónde le alcanza;
- registrar pagos desde el teléfono;
- crear el mes siguiente a partir del anterior;
- importar la planilla desde Excel y descargarla con el mismo formato.

Se guarda en la pestaña ``pagos_pendientes_vitae`` (las columnas que ya
tuviera se conservan) y lo disponible por caja en ``pagos_pendientes_cajas``.
"""
from __future__ import annotations

import html
import io
import re
import unicodedata
import uuid
from datetime import date, datetime
from typing import Any, Callable

import pandas as pd
import streamlit as st

try:
    from vitae.core.movil import es_movil
except Exception:  # pragma: no cover
    def es_movil() -> bool:
        return False

try:
    from vitae.core.ui import tarjetas_kpi
except Exception:  # pragma: no cover
    def tarjetas_kpi(tarjetas):
        for col, (t, v, p, _) in zip(st.columns(len(tarjetas)), tarjetas):
            col.metric(t, v, p)


TABLA = "pagos_pendientes_vitae"
TABLA_CAJAS = "pagos_pendientes_cajas"

CAJAS = ["VMR", "VM", "Gine Vitae"]
SIN_ASIGNAR = "Sin asignar"
NOMBRE_CAJA = {
    "VMR": "Vitae Medicina Reproductiva",
    "VM": "Vitae Medical",
    "Gine Vitae": "Gine Vitae",
    SIN_ASIGNAR: "Sin caja asignada",
}
COLOR_CAJA = {"VMR": "#8C2853", "VM": "#2F6F8F", "Gine Vitae": "#B0608A", SIN_ASIGNAR: "#9A8F95"}

CATEGORIA_REFERENCIAS = "Referencias"
CATEGORIA_SIN = "Sin categoría"

COLUMNAS = [
    "id", "mes", "orden", "categoria", "concepto", "periodo", "importe", "pagado",
    "saldo", "cubre", "estado", "observaciones", "fecha", "empresa",
]

MESES_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
            "Septiembre", "Octubre", "Noviembre", "Diciembre"]

SECCIONES = ["Planilla", "Qué caja paga", "Registrar pago", "Editar", "Nuevo mes", "Importar"]


# ---------------------------------------------------------------------------
# Números, textos y meses
# ---------------------------------------------------------------------------
def numero(valor: Any) -> float | None:
    """Lee importes argentinos: "$1.635.154,44", "150.000", "1635154.44", 12.5.

    Devuelve None si la celda no tiene un número (vacía, "FALTA DETALLE"...).
    """
    if valor is None:
        return None
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        try:
            if pd.isna(valor):
                return None
        except Exception:
            pass
        return float(valor)
    texto = str(valor).strip()
    if not texto or texto.lower() in {"nan", "none", "nat", "-"}:
        return None
    negativo = texto.startswith("-") or (texto.startswith("(") and texto.endswith(")"))
    texto = re.sub(r"[^\d.,]", "", texto)
    if not texto or not re.search(r"\d", texto):
        return None
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", texto):
        texto = texto.replace(".", "")
    try:
        n = float(texto)
    except ValueError:
        return None
    return -n if negativo else n


def pesos(valor: Any, decimales: int = 2) -> str:
    n = numero(valor) or 0.0
    signo = "-" if n < 0 else ""
    texto = f"{abs(n):,.{decimales}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{signo}${texto}"


def _sin_acentos(texto: Any) -> str:
    t = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in t if not unicodedata.combining(c)).lower().strip()


def _texto(valor: Any) -> str:
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except Exception:
        pass
    t = str(valor).strip()
    return "" if t.lower() in {"nan", "none", "nat"} else t


def nombre_mes(mes: str) -> str:
    try:
        p = pd.Period(mes, freq="M")
        return f"{MESES_ES[p.month - 1]} {p.year}"
    except Exception:
        return str(mes or "Sin mes")


def mes_siguiente(mes: str) -> str:
    return str(pd.Period(mes, freq="M") + 1)


def mes_actual() -> str:
    return str(pd.Period(date.today(), freq="M"))


def _mes_de_fecha(valor: Any) -> str:
    t = _texto(valor)
    if not t:
        return ""
    try:
        if re.match(r"^\d{4}-\d{1,2}", t):
            f = pd.to_datetime(t[:10], errors="coerce")
        else:
            f = pd.to_datetime(t, dayfirst=True, errors="coerce")
        return "" if pd.isna(f) else str(pd.Period(f, freq="M"))
    except Exception:
        return ""


def _normalizar_mes(valor: Any) -> str:
    t = _texto(valor)
    if not t:
        return ""
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", t)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    return _mes_de_fecha(t)


def avanzar_periodo(periodo: Any) -> str:
    """"10/26" → "11/26"; "12/26" → "1/27"; "10/2026" → "11/2026"; otro texto queda igual."""
    t = _texto(periodo)
    m = re.fullmatch(r"(\d{1,2})/(\d{2}|\d{4})", t)
    if not m:
        return t
    mes, anio = int(m.group(1)), m.group(2)
    if not 1 <= mes <= 12:
        return t
    y = int(anio)
    mes += 1
    if mes == 13:
        mes, y = 1, y + 1
    return f"{mes}/{y:02d}" if len(anio) == 2 else f"{mes}/{y:04d}"


def normalizar_caja(valor: Any) -> str:
    t = _sin_acentos(valor)
    if not t:
        return ""
    if "gine" in t:
        return "Gine Vitae"
    if t in {"vmr", "vitae medicina reproductiva"} or "reproductiva" in t:
        return "VMR"
    if t in {"vm", "vitae medical", "medical"} or "vitae medical" in t:
        return "VM"
    return ""


def sugerir_caja(*textos: Any) -> str:
    """Caja sugerida según la empresa que aparece en el concepto o la categoría."""
    t = _sin_acentos(" ".join(_texto(x) for x in textos))
    if "gine" in t:
        return "Gine Vitae"
    if "medicina reproductiva" in t:
        return "VMR"
    if "vitae medical" in t:
        return "VM"
    return ""


def _id_nuevo() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------
def normalizar(df: pd.DataFrame | None) -> pd.DataFrame:
    """Tabla lista para usar: importes numéricos, saldo, estado y caja.

    Conserva todas las columnas que tenga la pestaña (también las del formato
    anterior: proveedor, vencimiento, prioridad...).
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=COLUMNAS)
    d = df.copy().reset_index(drop=True)
    d.columns = [str(c).strip() for c in d.columns]
    for col in COLUMNAS:
        if col not in d.columns:
            d[col] = ""

    # Formato anterior del módulo: el monto estaba en "importe" y el concepto
    # podía estar en "proveedor".
    if "proveedor" in d.columns:
        sin_concepto = d["concepto"].map(_texto).eq("")
        d.loc[sin_concepto, "concepto"] = d.loc[sin_concepto, "proveedor"]

    d["id"] = [(_texto(v) or _id_nuevo()) for v in d["id"]]
    meses = [_normalizar_mes(m) or _mes_de_fecha(f) or (_mes_de_fecha(v) if "vencimiento" in d.columns else "")
             for m, f, v in zip(d["mes"], d["fecha"], d.get("vencimiento", d["fecha"]))]
    d["mes"] = [m or "Sin mes" for m in meses]
    d["categoria"] = [(_texto(c) or CATEGORIA_SIN) for c in d["categoria"]]
    d["concepto"] = d["concepto"].map(_texto)
    d["periodo"] = d["periodo"].map(_texto)
    d["observaciones"] = d["observaciones"].map(_texto)
    d["importe"] = [numero(v) or 0.0 for v in d["importe"]]
    d["pagado"] = [numero(v) or 0.0 for v in d["pagado"]]
    d["saldo"] = (d["importe"] - d["pagado"]).clip(lower=0).round(2)

    cajas = []
    for cubre, empresa in zip(d["cubre"], d["empresa"]):
        c = normalizar_caja(cubre)
        if not c and not _texto(cubre):
            c = normalizar_caja(empresa) if _texto(empresa).upper() in {"VM", "VMR"} else ""
        cajas.append(c)
    d["cubre"] = cajas

    orden = pd.to_numeric(d["orden"], errors="coerce")
    d["orden"] = orden.where(orden.notna(), pd.Series(range(1, len(d) + 1), index=d.index) + 10_000).astype(float)
    d["estado"] = [estado_de(i, p, c) for i, p, c in zip(d["importe"], d["pagado"], d["categoria"])]
    return d


def estado_de(importe: float, pagado: float, categoria: str = "") -> str:
    if categoria == CATEGORIA_REFERENCIAS:
        return "Referencia"
    if (importe or 0) <= 0:
        return "Sin monto"
    if (pagado or 0) >= importe - 0.004:
        return "Pagado"
    if (pagado or 0) > 0:
        return "Parcial"
    return "Pendiente"


def para_guardar(d: pd.DataFrame) -> pd.DataFrame:
    """Tabla con el formato de la pestaña: columnas fijas primero y luego el resto."""
    if d is None or d.empty:
        return pd.DataFrame(columns=COLUMNAS)
    out = d.copy()
    for col in COLUMNAS:
        if col not in out.columns:
            out[col] = ""
    out["importe"] = [round(numero(v) or 0.0, 2) for v in out["importe"]]
    out["pagado"] = [round(numero(v) or 0.0, 2) for v in out["pagado"]]
    out["saldo"] = (out["importe"] - out["pagado"]).clip(lower=0).round(2)
    out["estado"] = [estado_de(i, p, c) for i, p, c in zip(out["importe"], out["pagado"], out["categoria"])]
    out["cubre"] = out["cubre"].map(normalizar_caja)
    out["empresa"] = [c if c in {"VM", "VMR"} else (_texto(e) or "VITAE") for c, e in zip(out["cubre"], out["empresa"])]
    out["fecha"] = [(_texto(f) or (f"{m}-01" if re.fullmatch(r"\d{4}-\d{2}", str(m)) else "")) for f, m in zip(out["fecha"], out["mes"])]
    out["mes"] = out["mes"].replace("Sin mes", "")
    out["orden"] = pd.to_numeric(out["orden"], errors="coerce").fillna(0).astype(int)
    # Montos con punto decimal y sin separador de miles: Google Sheets y la app
    # los leen igual en cualquier configuración regional.
    for col in ("importe", "pagado", "saldo"):
        out[col] = out[col].map(lambda v: f"{v:.2f}")
    extras = [c for c in out.columns if c not in COLUMNAS and not str(c).startswith("_")]
    return out[COLUMNAS + extras]


def meses_disponibles(d: pd.DataFrame) -> list[str]:
    if d is None or d.empty:
        return []
    return sorted({m for m in d["mes"] if m and m != "Sin mes"}, reverse=True) + (
        ["Sin mes"] if (d["mes"] == "Sin mes").any() else []
    )


def del_mes(d: pd.DataFrame, mes: str) -> pd.DataFrame:
    if d is None or d.empty:
        return pd.DataFrame(columns=COLUMNAS)
    return d[d["mes"] == mes].sort_values("orden", kind="stable")


def renumerar(d: pd.DataFrame, mes: str) -> pd.DataFrame:
    """Orden 1, 2, 3... dentro del mes, respetando el orden actual."""
    out = d.copy()
    mascara = out["mes"] == mes
    orden = out.loc[mascara].sort_values("orden", kind="stable").index
    out.loc[orden, "orden"] = range(1, len(orden) + 1)
    return out


def categorias_en_orden(df_mes: pd.DataFrame) -> list[str]:
    if df_mes.empty:
        return []
    primeras = df_mes.groupby("categoria", sort=False)["orden"].min().sort_values(kind="stable")
    cats = list(primeras.index)
    if CATEGORIA_REFERENCIAS in cats:  # las referencias siempre al final
        cats.remove(CATEGORIA_REFERENCIAS)
        cats.append(CATEGORIA_REFERENCIAS)
    return cats


def totales(df_mes: pd.DataFrame) -> dict[str, float]:
    d = df_mes[df_mes["categoria"] != CATEGORIA_REFERENCIAS] if not df_mes.empty else df_mes
    if d.empty:
        return {"importe": 0.0, "pagado": 0.0, "saldo": 0.0, "conceptos": 0, "con_saldo": 0,
                "sin_asignar": 0.0, "n_sin_asignar": 0, "sin_monto": 0, "avance": 0.0}
    con_saldo = d["saldo"] > 0.004
    sin_caja = con_saldo & (d["cubre"] == "")
    importe = float(d["importe"].sum())
    pagado = float(d["pagado"].sum())
    return {
        "importe": importe,
        "pagado": pagado,
        "saldo": float(d["saldo"].sum()),
        "conceptos": int(len(d)),
        "con_saldo": int(con_saldo.sum()),
        "sin_asignar": float(d.loc[sin_caja, "saldo"].sum()),
        "n_sin_asignar": int(sin_caja.sum()),
        "sin_monto": int((d["importe"] <= 0).sum()),
        "avance": (100 * pagado / importe) if importe else 0.0,
    }


def saldo_mes_vigente(df: pd.DataFrame | None) -> float:
    """Saldo pendiente de la planilla más reciente (para Deuda total y el Panel).

    Cada mes repite lo que sigue impago del anterior, así que sumar todos los
    meses contaría la misma deuda varias veces.
    """
    d = normalizar(df)
    meses = [m for m in meses_disponibles(d) if m != "Sin mes"]
    if not meses:
        return float(d["saldo"].sum()) if not d.empty else 0.0
    return totales(del_mes(d, meses[0]))["saldo"]


def cobertura(df_mes: pd.DataFrame, disponibles: dict[str, float]) -> list[dict[str, Any]]:
    """Por caja: disponible, saldo asignado y qué conceptos alcanza a cubrir (en orden)."""
    d = df_mes[(df_mes["categoria"] != CATEGORIA_REFERENCIAS) & (df_mes["saldo"] > 0.004)]
    d = d.sort_values("orden", kind="stable")
    salida = []
    for caja in CAJAS + [SIN_ASIGNAR]:
        filas = d[d["cubre"] == ("" if caja == SIN_ASIGNAR else caja)]
        disponible = float(disponibles.get(caja, 0.0) or 0.0) if caja != SIN_ASIGNAR else 0.0
        acumulado = 0.0
        items = []
        for _, f in filas.iterrows():
            acumulado += float(f["saldo"])
            items.append({
                "id": f["id"], "concepto": f["concepto"], "periodo": f["periodo"],
                "categoria": f["categoria"], "saldo": float(f["saldo"]), "acumulado": acumulado,
                "alcanza": caja != SIN_ASIGNAR and acumulado <= disponible + 0.004,
            })
        asignado = float(filas["saldo"].sum())
        salida.append({
            "caja": caja, "disponible": disponible, "asignado": asignado,
            "margen": disponible - asignado, "items": items,
            "cubiertos": sum(1 for i in items if i["alcanza"]),
        })
    return salida


def crear_mes(d: pd.DataFrame, origen: str, destino: str, *, pasar_saldos: bool = True,
              repetir_pagados: bool = True, copiar_cajas: bool = True) -> pd.DataFrame:
    """Filas del mes nuevo a partir de otro mes.

    - Lo que quedó con saldo pasa igual (monto y pagado), para seguir su deuda.
    - Lo que ya se pagó se repite con el período siguiente, en cero pagado,
      como base para completar el mes (los montos se revisan a mano).
    """
    base = del_mes(d, origen)
    nuevas = []
    for _, f in base.iterrows():
        referencia = f["categoria"] == CATEGORIA_REFERENCIAS
        con_saldo = f["saldo"] > 0.004 or f["importe"] <= 0
        if referencia or (con_saldo and pasar_saldos):
            fila = f.to_dict()
        elif not con_saldo and repetir_pagados:
            fila = f.to_dict()
            fila["periodo"] = avanzar_periodo(f["periodo"])
            fila["pagado"] = 0.0
            fila["observaciones"] = ""
        else:
            continue
        fila["id"] = _id_nuevo()
        fila["mes"] = destino
        fila["fecha"] = f"{destino}-01"
        if not copiar_cajas:
            fila["cubre"] = ""
        nuevas.append(fila)
    nuevo = pd.DataFrame(nuevas, columns=d.columns if not d.empty else COLUMNAS)
    if nuevo.empty:
        return nuevo
    nuevo["importe"] = nuevo["importe"].astype(float)
    nuevo["pagado"] = nuevo["pagado"].astype(float)
    nuevo["saldo"] = (nuevo["importe"] - nuevo["pagado"]).clip(lower=0).round(2)
    nuevo["estado"] = [estado_de(i, p, c) for i, p, c in zip(nuevo["importe"], nuevo["pagado"], nuevo["categoria"])]
    return nuevo


def registrar_pago(d: pd.DataFrame, fila_id: str, monto: float, caja: str, cuando: date | None = None) -> pd.DataFrame:
    out = d.copy()
    mascara = out["id"] == fila_id
    if not mascara.any() or monto <= 0:
        return out
    i = out.index[mascara][0]
    out.at[i, "pagado"] = round(float(out.at[i, "pagado"]) + float(monto), 2)
    if caja in CAJAS and not out.at[i, "cubre"]:
        out.at[i, "cubre"] = caja
    nota = f"{(cuando or date.today()):%d/%m/%Y}: pago {pesos(monto)}" + (f" ({caja})" if caja in CAJAS else "")
    previa = _texto(out.at[i, "observaciones"])
    out.at[i, "observaciones"] = f"{previa} · {nota}" if previa else nota
    out.at[i, "saldo"] = max(round(float(out.at[i, "importe"]) - float(out.at[i, "pagado"]), 2), 0.0)
    out.at[i, "estado"] = estado_de(out.at[i, "importe"], out.at[i, "pagado"], out.at[i, "categoria"])
    return out


# ---------------------------------------------------------------------------
# Planilla de noviembre 2026 (la que se completaba a mano)
# ---------------------------------------------------------------------------
_CAT_SUELDOS_VMR = "Sueldos con recibos de Vitae Medicina Reproductiva S.R.L."
_CAT_SUELDOS_VM = "Sueldos con recibos de Vitae Medical S.R.L."
_CAT_SIN_RECIBO = "Sueldos sin recibo"
_CAT_GASTOS = "Gastos generales"
_CAT_PROVEEDORES = "Proveedores y honorarios"
_CAT_PLANES = "Planes de pagos y préstamos"

_NOVIEMBRE_2026 = [
    (_CAT_SUELDOS_VMR, "ALFREDO GUAYMAS", "10/26", 1635154.44, 1635154.44, ""),
    (_CAT_SUELDOS_VMR, "PATRICIA PLATA", "10/26", 1559905.04, 1559905.04, ""),
    (_CAT_SUELDOS_VMR, "NICOLAS CHAR", "10/26", 1257685.75, 262000.00, ""),
    (_CAT_SUELDOS_VMR, "KARINA CRUZ", "10/26", 1432857.16, 1432857.16, ""),
    (_CAT_SUELDOS_VMR, "PAOLA ALEGRE", "10/26", 1304013.62, 1304013.62, ""),
    (_CAT_SUELDOS_VMR, "CONSTANZA ROBLES (LIQUIDACION FINAL)", "1/10", 1200000.00, 1200000.00, "Liquidación final"),
    (_CAT_SUELDOS_VM, "GERMAN VALDEZ", "10/26", 735126.61, 735126.61, ""),
    (_CAT_SUELDOS_VM, "SILVINA MARTINEZ", "10/26", 607256.29, 607256.29, ""),
    (_CAT_SIN_RECIBO, "RAMIRO CHAR", "10/26", 1662000.00, 1662000.00, ""),
    (_CAT_SIN_RECIBO, "AGUSTIN CHAR", "10/26", 1662000.00, 1662000.00, ""),
    (_CAT_SIN_RECIBO, "ROMINA GARCIA (MONOTRIBUTO)", "10/26", 1531000.00, 1531000.00, ""),
    (_CAT_SIN_RECIBO, "GERMAN VALDEZ", "9/26", 936873.39, 936873.39, ""),
    (_CAT_SIN_RECIBO, "GERMAN VALDEZ", "10/26", 926873.39, 926873.39, ""),
    (_CAT_SIN_RECIBO, "NICOLAS CHAR", "10/26", 404314.25, 0.0, ""),
    (_CAT_SIN_RECIBO, "PAULA GALLINA (GINEVITAE + CAPACITACION)", "10/26", 609800.00, 609800.00, ""),
    (_CAT_SIN_RECIBO, "SILVINA MARTINEZ. HORAS EXTRAS", "10/26", 65000.00, 65000.00, ""),
    (_CAT_GASTOS, "GASTOS COMUNES EDIFICIO - 20 ESPACIOS", "9/26", 5365361.03, 4936214.11, ""),
    (_CAT_GASTOS, "GASTOS COMUNES EDIFICIO - 20 ESPACIOS", "10/26", 5901673.34, 0.0, ""),
    (_CAT_GASTOS, "GASTOS POR MEDICOS", "10/26", 816000.00, 816000.00, ""),
    (_CAT_GASTOS, "TARJETA DE CREDITO VITAE MEDICAL S.R.L.", "10/26", 674751.23, 674751.23, ""),
    (_CAT_GASTOS, "TARJETA DE CREDITO VITAE MEDICINA REPRODUCTIVA S.R.L.", "10/26", 0.0, 0.0, "Falta detalle"),
    (_CAT_PROVEEDORES, "ISABEL ALONSO", "10/26", 747500.00, 0.0, ""),
    (_CAT_PROVEEDORES, "CELESTE LOPEZ", "10/26", 550000.00, 0.0, ""),
    (_CAT_PROVEEDORES, "CONTADORA JULIETA MARTIN", "10/26", 1493000.00, 1493000.00, ""),
    (_CAT_PROVEEDORES, "IMPRENTA PARA CHARLA RIO TINTO. NATALIA RESTON", "10/26", 480000.00, 480000.00, ""),
    (_CAT_PROVEEDORES, "ESTERILIZACION DE MATERIALES. SENYP", "2026", 1035000.00, 0.0, ""),
    (_CAT_PROVEEDORES, "ABOGADO LUCAS MURUA", "10/26", 430000.00, 430000.00, ""),
    (_CAT_PROVEEDORES, "ACLISASA APORTE MENSUAL", "6/26", 1665765.77, 1665765.77, ""),
    (_CAT_PROVEEDORES, "ACLISASA APORTE MENSUAL", "7/26", 1665765.77, 4257.74, ""),
    (_CAT_PROVEEDORES, "ACLISASA APORTE MENSUAL", "8/26", 1665765.77, 0.0, ""),
    (_CAT_PROVEEDORES, "ACLISASA APORTE MENSUAL", "9/26", 1665765.77, 0.0, ""),
    (_CAT_PROVEEDORES, "ACLISASA APORTE MENSUAL", "10/26", 1665765.77, 0.0, ""),
    (_CAT_PROVEEDORES, "ACLISASA APORTE MENSUAL", "11/26", 1665765.77, 0.0, ""),
    (_CAT_PROVEEDORES, "HONORARIOS CONTADORES POR BALANCES", "5/26", 12600000.00, 7015000.00, ""),
    (_CAT_PLANES, "PLANES DE PAGOS. VITAE MEDICAL", "8/26", 617971.53, 617971.53, ""),
    (_CAT_PLANES, "PLANES DE PAGOS Y PRESTAMOS. VITAE MEDICINA REPRODUCTIVA", "10/26", 0.0, 0.0, "Detalle contadora"),
    (CATEGORIA_REFERENCIAS, "IMPUESTOS", "", 0.0, 0.0, "Planilla de contadora"),
    (CATEGORIA_REFERENCIAS, "PROVEEDORES", "", 0.0, 0.0, "Planillas de VM y VMR"),
]


def planilla_noviembre_2026() -> pd.DataFrame:
    filas = []
    for orden, (cat, concepto, periodo, importe, pagado, obs) in enumerate(_NOVIEMBRE_2026, start=1):
        filas.append({
            "id": _id_nuevo(), "mes": "2026-11", "orden": orden, "categoria": cat, "concepto": concepto,
            "periodo": periodo, "importe": importe, "pagado": pagado, "cubre": "",
            "observaciones": obs, "fecha": "2026-11-01", "empresa": "",
        })
    return normalizar(pd.DataFrame(filas))


# ---------------------------------------------------------------------------
# Importar la planilla desde Excel / CSV
# ---------------------------------------------------------------------------
_PALABRAS_GASTO_SUELTO = ("gastos ", "gasto ", "tarjeta", "planes de pago", "plan de pago", "impuesto", "alquiler",
                          "expensas", "servicios")


def _categoria_linda(texto: str) -> str:
    t = re.sub(r"\s+", " ", _texto(texto)).strip(" .:-")
    if not t:
        return CATEGORIA_SIN
    t = t[:1].upper() + t[1:].lower()
    for nombre in ("Vitae Medicina Reproductiva", "Vitae Medical", "Gine Vitae"):
        t = re.sub(re.escape(nombre), nombre, t, flags=re.I)
    t = re.sub(r"\bs\.?r\.?l\.?", "S.R.L.", t, flags=re.I)
    return t


def _categoria_gasto_suelto(concepto: str) -> str:
    t = _sin_acentos(concepto)
    if t.startswith("plan") or "prestamo" in t:
        return _CAT_PLANES
    return _CAT_GASTOS


def _mes_del_titulo(texto: str) -> str:
    t = _sin_acentos(texto)
    for i, nombre in enumerate(MESES_ES, start=1):
        m = re.search(rf"\b{_sin_acentos(nombre)}\b\D*(\d{{4}})", t)
        if m:
            return f"{int(m.group(1)):04d}-{i:02d}"
    return ""


def _celda_texto(v: Any) -> str:
    if isinstance(v, (datetime, date)):
        return f"{v.month}/{v.strftime('%y')}"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return _texto(v)


def leer_planilla(filas: list[list[Any]], resaltadas: list[bool] | None = None) -> tuple[str, pd.DataFrame]:
    """Convierte la planilla (lista de filas) en registros.

    Reconoce el título "PAGOS PENDIENTES <MES> <AÑO>", la fila de encabezados
    (CONCEPTO, PERIODO, MONTO, PAGADO, SALDO), las filas de categoría (sin
    importes), los gastos sueltos resaltados (con importe), el TOTAL y las
    referencias que hay debajo.
    """
    resaltadas = resaltadas or [False] * len(filas)
    mes = ""
    cab = None
    for i, fila in enumerate(filas[:15]):
        celdas = [_sin_acentos(_celda_texto(v)) for v in fila]
        if not mes:
            for c in celdas:
                if "pagos pendientes" in c or _mes_del_titulo(c):
                    mes = _mes_del_titulo(c) or mes
        if "concepto" in celdas and any("monto" in c or "importe" in c for c in celdas):
            cab = i
            break
    if cab is None:
        raise ValueError("No encontré la fila de encabezados (CONCEPTO, PERIODO, MONTO, PAGADO, SALDO).")
    encabezados = [_sin_acentos(_celda_texto(v)) for v in filas[cab]]

    def col(*nombres: str, exacto: bool = False) -> int | None:
        for n in nombres:
            for j, h in enumerate(encabezados):
                if h == n or (not exacto and h.startswith(n)):
                    return j
        return None

    c_con = col("concepto")
    c_per = col("periodo", "período")
    c_mon = col("monto", "importe")
    c_pag = col("pagado")
    c_caja = col("caja que paga", "caja", "paga", "cubre", exacto=True)
    c_obs = col("observ")

    def valor(fila: list[Any], j: int | None) -> Any:
        return fila[j] if j is not None and j < len(fila) else None

    registros = []
    categoria = ""
    referencias = False
    for i in range(cab + 1, len(filas)):
        fila = filas[i]
        concepto = _celda_texto(valor(fila, c_con))
        if not concepto:
            continue
        if _sin_acentos(concepto).startswith("total"):
            referencias = True
            continue
        otros = [_celda_texto(v) for j, v in enumerate(fila) if j != c_con and _celda_texto(v)]
        if referencias:
            registros.append({"categoria": CATEGORIA_REFERENCIAS, "concepto": concepto, "periodo": "",
                              "importe": 0.0, "pagado": 0.0, "observaciones": " ".join(otros)[:200].capitalize(),
                              "cubre": ""})
            continue
        periodo = _celda_texto(valor(fila, c_per))
        monto_crudo = valor(fila, c_mon)
        pagado_crudo = valor(fila, c_pag)
        monto = numero(monto_crudo)
        pagado = numero(pagado_crudo)
        nota = ""
        if monto is None and _celda_texto(monto_crudo):
            nota = _celda_texto(monto_crudo).capitalize()
        tiene_datos = bool(periodo) or monto is not None or pagado is not None or bool(nota)
        if not tiene_datos:
            categoria = _categoria_linda(concepto)
            continue
        suelto = resaltadas[i] if i < len(resaltadas) else False
        suelto = suelto or _sin_acentos(concepto).startswith(_PALABRAS_GASTO_SUELTO)
        obs = _celda_texto(valor(fila, c_obs))
        registros.append({
            "categoria": _categoria_gasto_suelto(concepto) if suelto else (categoria or _CAT_GASTOS),
            "concepto": concepto,
            "periodo": periodo,
            "importe": monto or 0.0,
            "pagado": pagado or 0.0,
            "observaciones": " · ".join(x for x in (nota, obs) if x),
            "cubre": normalizar_caja(valor(fila, c_caja)),
        })
    df = pd.DataFrame(registros)
    if not df.empty:
        df.insert(0, "orden", range(1, len(df) + 1))
    return mes, df


def leer_archivo(nombre: str, contenido: bytes, hoja: str | None = None) -> tuple[str, pd.DataFrame, list[str]]:
    """Lee un .xlsx (con colores de fila) o .csv con el formato de la planilla."""
    if nombre.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        libro = load_workbook(io.BytesIO(contenido), data_only=True)
        hojas = libro.sheetnames
        ws = libro[hoja] if hoja in hojas else libro.worksheets[0]
        filas, resaltadas = [], []
        for fila in ws.iter_rows():
            filas.append([c.value for c in fila])
            primera = fila[0] if fila else None
            color = ""
            try:
                if primera is not None and primera.fill is not None and primera.fill.fill_type == "solid":
                    color = str(primera.fill.fgColor.rgb or "")
                    if primera.fill.fgColor.type == "theme":
                        color = "theme"
            except Exception:
                color = ""
            resaltadas.append(bool(color) and color.upper() not in {"FFFFFFFF", "00000000", "00FFFFFF"})
        mes, df = leer_planilla(filas, resaltadas)
        return mes, df, hojas
    crudo = pd.read_csv(io.BytesIO(contenido), header=None, dtype=str, sep=None, engine="python")
    filas = crudo.where(crudo.notna(), None).values.tolist()
    mes, df = leer_planilla(filas)
    return mes, df, []


# ---------------------------------------------------------------------------
# Excel con el formato de la planilla
# ---------------------------------------------------------------------------
def excel_planilla(df_mes: pd.DataFrame, mes: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = Workbook()
    ws = wb.active
    ws.title = nombre_mes(mes)[:31]
    rosa = PatternFill("solid", fgColor="D5A6BD")
    verde = PatternFill("solid", fgColor="A9C47F")
    fino = Side(style="thin", color="999999")
    borde = Border(left=fino, right=fino, top=fino, bottom=fino)
    formato = '"$"#,##0.00'

    ws.append([f"PAGOS PENDIENTES {nombre_mes(mes).upper()}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["CONCEPTO", "PERIODO", "MONTO", "PAGADO", "SALDO", "CAJA QUE PAGA", "OBSERVACIONES"])
    for c in ws[2]:
        c.fill, c.font, c.border = rosa, Font(bold=True), borde
        c.alignment = Alignment(horizontal="center")

    datos = df_mes[df_mes["categoria"] != CATEGORIA_REFERENCIAS]
    for cat in categorias_en_orden(datos):
        ws.append([cat.upper()])
        fila = ws.max_row
        for c in ws[fila][:7]:
            c.fill, c.border = verde, borde
        ws.cell(fila, 1).font = Font(bold=True)
        for _, f in datos[datos["categoria"] == cat].iterrows():
            ws.append([
                f["concepto"], f["periodo"],
                float(f["importe"]) if f["importe"] else None,
                float(f["pagado"]) if f["pagado"] else None,
                float(f["saldo"]) if f["importe"] else None,
                f["cubre"] or "", f["observaciones"],
            ])
            for c in ws[ws.max_row][:7]:
                c.border = borde
            for j in (3, 4, 5):
                ws.cell(ws.max_row, j).number_format = formato
            ws.cell(ws.max_row, 2).alignment = Alignment(horizontal="center")
    t = totales(df_mes)
    ws.append(["TOTAL", "", t["importe"], t["pagado"], t["saldo"], "", ""])
    for c in ws[ws.max_row][:7]:
        c.fill, c.font, c.border = rosa, Font(bold=True), borde
    for j in (3, 4, 5):
        ws.cell(ws.max_row, j).number_format = formato

    refs = df_mes[df_mes["categoria"] == CATEGORIA_REFERENCIAS]
    if not refs.empty:
        ws.append([])
        for _, f in refs.iterrows():
            ws.append([f["concepto"], f["observaciones"]])
            ws.cell(ws.max_row, 1).fill = verde
            ws.cell(ws.max_row, 1).font = Font(bold=True)
    for letra, ancho in zip("ABCDEFG", (58, 10, 17, 17, 17, 16, 34)):
        ws.column_dimensions[letra].width = ancho
    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()


# ---------------------------------------------------------------------------
# Vista
# ---------------------------------------------------------------------------
_CSS = """
<style>
.pp2-cat{margin:1.1rem 0 .35rem;border:1px solid var(--vitae-line,#E8E3E6);border-radius:12px;overflow:hidden;background:#fff}
.pp2-cat-head{display:flex;justify-content:space-between;align-items:center;gap:.75rem;padding:.6rem .9rem;background:#F7EFF3;border-bottom:1px solid #EADDE4}
.pp2-cat-title{font-weight:650;font-size:.8rem;letter-spacing:.04em;text-transform:uppercase;color:#5E1A39}
.pp2-cat-sum{font-size:.78rem;color:#6E6268;white-space:nowrap;font-variant-numeric:tabular-nums}
.pp2-cat-sum b{color:#B14E5E;font-weight:650}
.pp2-cols,.pp2-row{display:grid;grid-template-columns:minmax(0,1fr) 64px 132px 132px 132px 92px;gap:.5rem;align-items:center;padding:.48rem .9rem}
.pp2-cols{font-size:.7rem;font-weight:600;color:#8A7F85;text-transform:uppercase;letter-spacing:.04em;padding-top:.4rem;padding-bottom:.25rem}
.pp2-row{border-top:1px solid #F2EEF0;font-size:.88rem}
.pp2-row:hover{background:#FCFAFB}
.pp2-con{min-width:0;color:#1F1A1D;font-weight:500;overflow-wrap:anywhere}
.pp2-con small{display:block;color:#8A7F85;font-weight:400;font-size:.76rem;margin-top:1px}
.pp2-per{text-align:center;color:#5D5258;font-variant-numeric:tabular-nums}
.pp2-num{text-align:right;font-variant-numeric:tabular-nums;color:#3A3236;white-space:nowrap}
.pp2-saldo{font-weight:650;color:#B14E5E}
.pp2-saldo.ok{color:#5F9D78;font-weight:500}
.pp2-saldo.na{color:#B9AEB4;font-weight:400}
.pp2-meta{display:none}
.pp2-chip{display:inline-block;padding:.12rem .5rem;border-radius:999px;font-size:.72rem;font-weight:600;color:#fff;white-space:nowrap}
.pp2-chip.none{background:#fff;color:#9A8F95;border:1px dashed #CFC6CB;font-weight:500}
.pp2-tag{display:inline-block;margin-left:.35rem;padding:.05rem .42rem;border-radius:6px;font-size:.68rem;font-weight:600;vertical-align:middle}
.pp2-tag.parcial{background:#FBF1E2;color:#9A6A1F}
.pp2-tag.falta{background:#EEF0F3;color:#5C6470}
.pp2-total{display:grid;grid-template-columns:minmax(0,1fr) 64px 132px 132px 132px 92px;gap:.5rem;padding:.75rem .9rem;margin-top:.9rem;border-radius:12px;background:#5E1A39;color:#fff;font-weight:650;font-variant-numeric:tabular-nums}
.pp2-total .pp2-num{color:#fff}
.pp2-refs{margin-top:.8rem;font-size:.85rem;color:#5D5258}
.pp2-refs span{display:inline-block;margin:.2rem .5rem .2rem 0;padding:.3rem .65rem;border-radius:8px;background:#F4F7EE;border:1px solid #DCE6CC}
.pp2-caja{border:1px solid var(--vitae-line,#E8E3E6);border-radius:12px;padding:14px 16px;background:#fff;height:100%}
.pp2-caja h4{margin:0 0 .1rem;font-size:1rem;font-weight:650;color:#1F1A1D}
.pp2-caja .sub{font-size:.76rem;color:#8A7F85;margin-bottom:.6rem}
.pp2-caja .linea{display:flex;justify-content:space-between;font-size:.86rem;padding:.16rem 0;font-variant-numeric:tabular-nums}
.pp2-caja .linea b{font-weight:650}
.pp2-bar{height:8px;border-radius:99px;background:#F1ECEF;overflow:hidden;margin:.55rem 0 .35rem}
.pp2-bar > div{height:100%;border-radius:99px}
.pp2-margen{font-size:.86rem;font-weight:650}
.pp2-margen.ok{color:#5F9D78}.pp2-margen.mal{color:#B14E5E}
.pp2-item{display:flex;justify-content:space-between;gap:.6rem;font-size:.82rem;padding:.3rem 0;border-top:1px solid #F2EEF0}
.pp2-item .c{min-width:0;overflow-wrap:anywhere}
.pp2-item .m{white-space:nowrap;font-variant-numeric:tabular-nums}
.pp2-item.si .c::before{content:"✓ ";color:#5F9D78;font-weight:700}
.pp2-item.no .c::before{content:"✗ ";color:#B14E5E;font-weight:700}
.pp2-vacio{border:1px dashed #D9CFD4;border-radius:12px;padding:1.4rem;text-align:center;color:#6E6268;background:#FCFAFB}
@media (max-width: 640px){
 .pp2-cols{display:none}
 .pp2-row,.pp2-total{grid-template-columns:minmax(0,1fr) auto;gap:.15rem .6rem;padding:.55rem .75rem}
 .pp2-row .pp2-per,.pp2-row .pp2-monto,.pp2-row .pp2-pag,.pp2-row .pp2-caja-col{display:none}
 .pp2-meta{display:block;grid-column:1 / -1;font-size:.75rem;color:#8A7F85;font-variant-numeric:tabular-nums}
 .pp2-total .pp2-per,.pp2-total .pp2-monto,.pp2-total .pp2-pag,.pp2-total .pp2-caja-col{display:none}
 .pp2-cat-head{flex-direction:column;align-items:flex-start;gap:.15rem;padding:.55rem .75rem}
 .pp2-cat-sum{white-space:normal}
}
</style>
"""


def _chip(caja: str) -> str:
    if not caja:
        return '<span class="pp2-chip none">Sin asignar</span>'
    return f'<span class="pp2-chip" style="background:{COLOR_CAJA.get(caja, "#777")}">{html.escape(caja)}</span>'


def _fila_html(f: pd.Series) -> str:
    e = html.escape
    sin_monto = f["importe"] <= 0
    tag = ""
    if sin_monto:
        tag = '<span class="pp2-tag falta">Falta monto</span>'
    elif f["estado"] == "Parcial":
        tag = '<span class="pp2-tag parcial">Parcial</span>'
    obs = f'<small>{e(f["observaciones"])}</small>' if f["observaciones"] else ""
    if sin_monto:
        saldo = '<div class="pp2-num pp2-saldo na">—</div>'
    elif f["saldo"] <= 0.004:
        saldo = '<div class="pp2-num pp2-saldo ok">Pagado</div>'
    else:
        saldo = f'<div class="pp2-num pp2-saldo">{pesos(f["saldo"])}</div>'
    meta = " · ".join(x for x in (
        f["periodo"],
        f"Monto {pesos(f['importe'])}" if not sin_monto else "",
        f"Pagado {pesos(f['pagado'])}" if f["pagado"] else "",
        f["cubre"] or "Sin caja",
    ) if x)
    return (
        '<div class="pp2-row">'
        f'<div class="pp2-con">{e(f["concepto"] or "(sin concepto)")}{tag}{obs}</div>'
        f'<div class="pp2-per">{e(f["periodo"])}</div>'
        f'<div class="pp2-num pp2-monto">{"" if sin_monto else pesos(f["importe"])}</div>'
        f'<div class="pp2-num pp2-pag">{pesos(f["pagado"]) if f["pagado"] else ""}</div>'
        f'{saldo}'
        f'<div class="pp2-caja-col" style="text-align:right">{_chip(f["cubre"])}</div>'
        f'<div class="pp2-meta">{e(meta)}</div>'
        '</div>'
    )


def planilla_html(df_mes: pd.DataFrame) -> str:
    partes = [_CSS]
    datos = df_mes[df_mes["categoria"] != CATEGORIA_REFERENCIAS]
    for cat in categorias_en_orden(datos):
        filas = datos[datos["categoria"] == cat]
        saldo = float(filas["saldo"].sum())
        resumen = f"Monto {pesos(filas['importe'].sum())} · Saldo <b>{pesos(saldo)}</b>" if saldo > 0.004 else \
            f"Monto {pesos(filas['importe'].sum())} · <span style='color:#5F9D78'>todo pagado</span>"
        partes.append(
            '<div class="pp2-cat">'
            f'<div class="pp2-cat-head"><div class="pp2-cat-title">{html.escape(cat)}</div>'
            f'<div class="pp2-cat-sum">{resumen}</div></div>'
            '<div class="pp2-cols"><div>Concepto</div><div style="text-align:center">Período</div>'
            '<div style="text-align:right">Monto</div><div style="text-align:right">Pagado</div>'
            '<div style="text-align:right">Saldo</div><div style="text-align:right">Caja</div></div>'
            + "".join(_fila_html(f) for _, f in filas.iterrows())
            + "</div>"
        )
    t = totales(datos)
    partes.append(
        '<div class="pp2-total"><div>TOTAL</div><div class="pp2-per"></div>'
        f'<div class="pp2-num pp2-monto">{pesos(t["importe"])}</div>'
        f'<div class="pp2-num pp2-pag">{pesos(t["pagado"])}</div>'
        f'<div class="pp2-num">{pesos(t["saldo"])}</div><div class="pp2-caja-col"></div>'
        f'<div class="pp2-meta" style="color:#F1DCE6">Monto {pesos(t["importe"])} · Pagado {pesos(t["pagado"])}</div></div>'
    )
    refs = df_mes[df_mes["categoria"] == CATEGORIA_REFERENCIAS]
    if not refs.empty:
        chips = "".join(
            f"<span><b>{html.escape(f['concepto'])}</b>{' → ' + html.escape(f['observaciones']) if f['observaciones'] else ''}</span>"
            for _, f in refs.iterrows()
        )
        partes.append(f'<div class="pp2-refs">{chips}</div>')
    return "".join(partes)


def _caja_html(c: dict[str, Any]) -> str:
    caja = c["caja"]
    color = COLOR_CAJA.get(caja, "#777")
    e = html.escape
    if caja == SIN_ASIGNAR:
        cabeza = (f'<h4><span class="pp2-chip none">Sin asignar</span></h4>'
                  '<div class="sub">Saldo que todavía no tiene una caja que lo pague</div>'
                  f'<div class="linea"><span>Saldo sin caja</span><b>{pesos(c["asignado"])}</b></div>')
        barra = ""
    else:
        usado = (c["asignado"] / c["disponible"] * 100) if c["disponible"] > 0 else (100 if c["asignado"] > 0 else 0)
        margen_txt = (f'<div class="pp2-margen ok">Le sobran {pesos(c["margen"])}</div>' if c["margen"] >= -0.004
                      else f'<div class="pp2-margen mal">Le faltan {pesos(-c["margen"])}</div>')
        if c["disponible"] <= 0 and c["asignado"] > 0:
            margen_txt = '<div class="pp2-margen mal">Cargá cuánto tiene disponible</div>'
        cabeza = (f'<h4><span class="pp2-chip" style="background:{color}">{e(caja)}</span> '
                  f'<span style="font-weight:500;color:#6E6268;font-size:.85rem">{e(NOMBRE_CAJA[caja])}</span></h4>'
                  '<div class="sub">Lo que tiene para pagar este mes y lo que se le asignó</div>'
                  f'<div class="linea"><span>Disponible</span><b>{pesos(c["disponible"])}</b></div>'
                  f'<div class="linea"><span>Asignado pendiente</span><b>{pesos(c["asignado"])}</b></div>')
        barra = (f'<div class="pp2-bar"><div style="width:{min(usado, 100):.0f}%;background:'
                 f'{"#B14E5E" if usado > 100 else color}"></div></div>{margen_txt}')
    items = ""
    if c["items"]:
        lista = []
        for it in c["items"]:
            clase = "" if caja == SIN_ASIGNAR else (" si" if it["alcanza"] else " no")
            lista.append(f'<div class="pp2-item{clase}"><div class="c">{e(it["concepto"])}'
                         f'{" · " + e(it["periodo"]) if it["periodo"] else ""}</div>'
                         f'<div class="m">{pesos(it["saldo"])}</div></div>')
        items = '<div style="margin-top:.6rem">' + "".join(lista) + "</div>"
    else:
        items = '<div class="sub" style="margin-top:.6rem">Sin conceptos pendientes asignados.</div>'
    return f'<div class="pp2-caja">{cabeza}{barra}{items}</div>'


def _clave_rev() -> str:
    return "_pp2_rev"


def _rev() -> int:
    return int(st.session_state.get(_clave_rev(), 0))


def _subir_rev() -> None:
    st.session_state[_clave_rev()] = _rev() + 1


def render_pagos_pendientes(
    df_original: pd.DataFrame | None,
    *,
    guardar: Callable[[pd.DataFrame], Any],
    leer_cajas: Callable[[], pd.DataFrame] | None = None,
    guardar_cajas: Callable[[pd.DataFrame], Any] | None = None,
    selector: Callable[..., str] | None = None,
    mostrar_error: Callable[[str, Exception], None] | None = None,
) -> None:
    datos = normalizar(df_original)

    def _error(msg: str, exc: Exception) -> None:
        if mostrar_error:
            mostrar_error(msg, exc)
        else:
            st.error(f"{msg} {exc}")

    def _guardar(nuevo: pd.DataFrame, aviso: str) -> None:
        try:
            guardar(para_guardar(nuevo))
        except Exception as exc:
            _error("No se pudieron guardar los cambios en Google Sheets.", exc)
            return
        _subir_rev()
        st.toast(aviso, icon=":material/check_circle:")
        st.rerun()

    st.markdown(_CSS, unsafe_allow_html=True)

    meses = meses_disponibles(datos)
    if not meses:
        st.markdown(
            '<div class="pp2-vacio"><b>Todavía no hay planillas de pagos pendientes.</b><br>'
            "Podés cargar la de noviembre 2026 (la que venían completando) o importar una desde Excel en "
            "<b>Importar</b>.</div>",
            unsafe_allow_html=True,
        )
        if st.button("Cargar la planilla de noviembre 2026", type="primary", key="pp2_semilla_vacia",
                     icon=":material/playlist_add:"):
            _guardar(planilla_noviembre_2026(), "Planilla de noviembre 2026 cargada")
        meses = []

    # ---- Mes ---------------------------------------------------------------
    clave_mes = "pp2_mes"
    pendiente = st.session_state.pop("pp2_mes_pendiente", None)
    if pendiente in meses:  # mes recién creado o importado
        st.session_state[clave_mes] = pendiente
    if meses:
        if st.session_state.get(clave_mes) not in meses:
            st.session_state[clave_mes] = mes_actual() if mes_actual() in meses else meses[0]
        c_mes, c_desc = st.columns([1.2, 1])
        with c_mes:
            mes = st.selectbox("Planilla del mes", meses, key=clave_mes, format_func=nombre_mes)
        df_mes = del_mes(datos, mes)
        t = totales(df_mes)
        with c_desc:
            if not df_mes.empty:
                st.download_button(
                    "Descargar Excel", data=excel_planilla(df_mes, mes),
                    file_name=f"pagos_pendientes_{mes}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"pp2_excel_{mes}", icon=":material/download:", use_container_width=True,
                )
        tarjetas_kpi([
            ("Total del mes", pesos(t["importe"]), f"{t['conceptos']} conceptos", "#8C2853"),
            ("Pagado", pesos(t["pagado"]), f"{t['avance']:.0f}% del total", "#5F9D78"),
            ("Saldo pendiente", pesos(t["saldo"]), f"{t['con_saldo']} con saldo", "#B14E5E"),
            ("Sin caja asignada", pesos(t["sin_asignar"]), f"{t['n_sin_asignar']} conceptos", "#9A8F95"),
        ])
        if t["sin_monto"]:
            st.caption(f":material/info: {t['sin_monto']} concepto(s) sin monto cargado (por ejemplo «Falta detalle»).")
    else:
        mes = mes_actual()
        df_mes = del_mes(datos, mes)

    if selector:
        seccion = selector("Sección", SECCIONES, key="pp2_seccion")
    else:
        seccion = st.segmented_control("Sección", SECCIONES, default=SECCIONES[0], key="pp2_seccion",
                                       label_visibility="collapsed") or SECCIONES[0]

    # ---- Planilla ----------------------------------------------------------
    if seccion == "Planilla":
        if df_mes.empty:
            st.info("Este mes no tiene conceptos cargados.")
            return
        f1, f2, f3 = st.columns([1.3, 1, 1.2])
        with f1:
            filtro_caja = st.pills("Caja", ["Todas"] + CAJAS + [SIN_ASIGNAR], default="Todas",
                                   key="pp2_filtro_caja") or "Todas"
        with f2:
            filtro_ver = st.segmented_control("Mostrar", ["Todo", "Con saldo", "Pagado"], default="Todo",
                                              key="pp2_filtro_ver") or "Todo"
        with f3:
            buscar = st.text_input("Buscar", key="pp2_buscar", placeholder="Concepto u observación",
                                   label_visibility="visible")
        vista = df_mes
        if filtro_caja != "Todas":
            vista = vista[(vista["cubre"] == ("" if filtro_caja == SIN_ASIGNAR else filtro_caja))
                          | (vista["categoria"] == CATEGORIA_REFERENCIAS)]
        if filtro_ver == "Con saldo":
            vista = vista[(vista["saldo"] > 0.004) | (vista["importe"] <= 0)]
        elif filtro_ver == "Pagado":
            vista = vista[(vista["importe"] > 0) & (vista["saldo"] <= 0.004)]
        if buscar.strip():
            b = _sin_acentos(buscar)
            vista = vista[vista.apply(lambda f: b in _sin_acentos(f"{f['concepto']} {f['observaciones']} {f['categoria']}"),
                                      axis=1)]
        filtrado = filtro_caja != "Todas" or filtro_ver != "Todo" or bool(buscar.strip())
        if vista[vista["categoria"] != CATEGORIA_REFERENCIAS].empty:
            st.info("No hay conceptos con esos filtros.")
            return
        if filtrado:
            st.caption("El total de abajo corresponde a lo filtrado.")
        st.markdown(planilla_html(vista), unsafe_allow_html=True)
        return

    # ---- Qué caja paga -----------------------------------------------------
    if seccion == "Qué caja paga":
        if df_mes.empty:
            st.info("Este mes no tiene conceptos cargados.")
            return
        cajas_df = pd.DataFrame()
        if leer_cajas:
            try:
                cajas_df = leer_cajas()
            except Exception:
                cajas_df = pd.DataFrame()
        guardados: dict[str, float] = {}
        if not cajas_df.empty and {"mes", "caja", "disponible"} <= set(cajas_df.columns):
            for _, f in cajas_df[cajas_df["mes"].astype(str).str.strip() == mes].iterrows():
                caja = normalizar_caja(f["caja"])
                if caja:
                    guardados[caja] = numero(f["disponible"]) or 0.0

        st.markdown(f"**¿Cuánto tiene cada caja para pagar en {nombre_mes(mes).lower()}?**")
        st.caption("Lo que hay o lo que va a entrar. Con eso se ve qué conceptos alcanza a cubrir cada una, "
                   "en el orden de la planilla.")
        disp_cols = st.columns(len(CAJAS))
        disponibles: dict[str, float] = {}
        for col, caja in zip(disp_cols, CAJAS):
            with col:
                disponibles[caja] = float(st.number_input(
                    f"{caja} · {NOMBRE_CAJA[caja]}", min_value=0.0, step=10000.0, format="%.2f",
                    value=float(guardados.get(caja, 0.0)), key=f"pp2_disp_{mes}_{caja}_{_rev()}",
                ))
        cambio = any(abs(disponibles[c] - guardados.get(c, 0.0)) > 0.004 for c in CAJAS)
        if guardar_cajas and st.button("Guardar disponibles", key=f"pp2_guardar_disp_{mes}", disabled=not cambio,
                                       icon=":material/save:"):
            try:
                resto = cajas_df[cajas_df["mes"].astype(str).str.strip() != mes] if not cajas_df.empty and "mes" in cajas_df.columns else pd.DataFrame()
                nuevas = pd.DataFrame([
                    {"mes": mes, "caja": c, "disponible": f"{disponibles[c]:.2f}",
                     "actualizado": datetime.now().strftime("%Y-%m-%d %H:%M")}
                    for c in CAJAS
                ])
                guardar_cajas(pd.concat([resto, nuevas], ignore_index=True))
                _subir_rev()
                st.toast("Disponibles guardados", icon=":material/check_circle:")
                st.rerun()
            except Exception as exc:
                _error("No se pudo guardar lo disponible por caja.", exc)

        resumen = cobertura(df_mes, disponibles)
        tarjetas = [c for c in resumen if c["caja"] != SIN_ASIGNAR]
        cols = st.columns(len(tarjetas))
        for col, c in zip(cols, tarjetas):
            with col:
                st.markdown(_caja_html(c), unsafe_allow_html=True)
        sin = next(c for c in resumen if c["caja"] == SIN_ASIGNAR)
        if sin["items"]:
            st.markdown(" ")
            st.markdown(_caja_html(sin), unsafe_allow_html=True)

        st.markdown("#### Asignar caja a cada concepto")
        st.caption("Elegí en la columna **Paga** qué caja se hace cargo. Se muestran los conceptos con saldo.")
        pendientes = df_mes[(df_mes["categoria"] != CATEGORIA_REFERENCIAS)
                            & ((df_mes["saldo"] > 0.004) | (df_mes["importe"] <= 0))]
        sugeridas = {r["id"]: sugerir_caja(r["concepto"], r["categoria"]) for _, r in pendientes.iterrows()
                     if not r["cubre"] and sugerir_caja(r["concepto"], r["categoria"])}
        if sugeridas and st.button(
            f"Asignar según la empresa del concepto ({len(sugeridas)})", key=f"pp2_sugerir_{mes}",
            icon=":material/auto_fix_high:",
            help="Por ejemplo: «Sueldos con recibos de Vitae Medical» → VM; «Tarjeta … Medicina Reproductiva» → VMR. "
                 "Sólo completa los que están sin asignar.",
        ):
            nuevo = datos.copy()
            for fid, caja in sugeridas.items():
                nuevo.loc[nuevo["id"] == fid, "cubre"] = caja
            _guardar(nuevo, f"{len(sugeridas)} concepto(s) asignados")
        if pendientes.empty:
            st.success("No quedan conceptos con saldo este mes.")
            return
        tabla = pendientes[["id", "categoria", "concepto", "periodo", "saldo", "cubre"]].copy()
        tabla["cubre"] = tabla["cubre"].replace("", SIN_ASIGNAR)
        tabla = tabla.set_index("id")
        editada = st.data_editor(
            tabla,
            hide_index=True,
            use_container_width=True,
            disabled=["categoria", "concepto", "periodo", "saldo"],
            column_order=["concepto", "periodo", "saldo", "cubre", "categoria"],
            column_config={
                "concepto": st.column_config.TextColumn("Concepto", width="large"),
                "periodo": st.column_config.TextColumn("Período", width="small"),
                "saldo": st.column_config.NumberColumn("Saldo", format="localized"),
                "cubre": st.column_config.SelectboxColumn("Paga", options=CAJAS + [SIN_ASIGNAR], required=True),
                "categoria": st.column_config.TextColumn("Categoría"),
            },
            key=f"pp2_asignar_{mes}_{_rev()}",
        )
        cambios = {fid: ("" if v == SIN_ASIGNAR else v) for fid, v in editada["cubre"].items()
                   if ("" if v == SIN_ASIGNAR else v) != tabla.at[fid, "cubre"].replace(SIN_ASIGNAR, "")}
        if st.button(f"Guardar asignaciones ({len(cambios)})", type="primary", disabled=not cambios,
                     key=f"pp2_guardar_asig_{mes}", icon=":material/save:"):
            nuevo = datos.copy()
            for fid, caja in cambios.items():
                nuevo.loc[nuevo["id"] == fid, "cubre"] = caja
            _guardar(nuevo, "Asignaciones guardadas")
        return

    # ---- Registrar pago ----------------------------------------------------
    if seccion == "Registrar pago":
        pendientes = df_mes[(df_mes["categoria"] != CATEGORIA_REFERENCIAS) & (df_mes["saldo"] > 0.004)]
        if pendientes.empty:
            st.success("No quedan saldos por pagar en este mes.")
            return
        etiquetas = {r["id"]: f"{r['concepto']} · {r['periodo']} · saldo {pesos(r['saldo'])}"
                     for _, r in pendientes.iterrows()}
        fila_id = st.selectbox("Concepto", list(etiquetas), format_func=lambda i: etiquetas[i],
                               key=f"pp2_pago_concepto_{mes}")
        fila = pendientes[pendientes["id"] == fila_id].iloc[0]
        c1, c2 = st.columns(2)
        with c1:
            monto = st.number_input("Monto pagado", min_value=0.0, step=1000.0, format="%.2f",
                                    value=float(fila["saldo"]), key=f"pp2_pago_monto_{fila_id}_{_rev()}")
        with c2:
            cuando = st.date_input("Fecha", value=date.today(), format="DD/MM/YYYY", key=f"pp2_pago_fecha_{_rev()}")
        caja = st.segmented_control("Pagó la caja", CAJAS, default=fila["cubre"] or None,
                                    key=f"pp2_pago_caja_{fila_id}_{_rev()}")
        if monto > fila["saldo"] + 0.004:
            st.warning(f"El monto supera el saldo ({pesos(fila['saldo'])}).")
        if st.button("Registrar pago", type="primary", disabled=monto <= 0, key=f"pp2_pago_ok_{mes}",
                     icon=":material/payments:", use_container_width=es_movil()):
            nuevo = registrar_pago(datos, fila_id, float(monto), caja or "", cuando)
            _guardar(nuevo, f"Pago de {pesos(monto)} registrado")
        return

    # ---- Editar ------------------------------------------------------------
    if seccion == "Editar":
        cats = categorias_en_orden(df_mes)
        with st.expander("Agregar un concepto", expanded=df_mes.empty, icon=":material/add_circle:"):
            with st.form(f"pp2_alta_{mes}", clear_on_submit=True):
                a1, a2 = st.columns([1.4, 1])
                with a1:
                    opciones_cat = (cats or []) + ["Nueva categoría…"]
                    cat_sel = st.selectbox("Categoría", opciones_cat)
                    cat_nueva = st.text_input("Nombre de la categoría nueva", placeholder="Sólo si elegiste «Nueva categoría…»")
                    concepto = st.text_input("Concepto")
                with a2:
                    previo = str(pd.Period(mes, freq="M") - 1) if re.fullmatch(r"\d{4}-\d{2}", mes) else ""
                    periodo = st.text_input("Período", value=f"{int(previo[5:])}/{previo[2:4]}" if previo else "")
                    imp = st.number_input("Monto", min_value=0.0, step=1000.0, format="%.2f")
                    pag = st.number_input("Pagado", min_value=0.0, step=1000.0, format="%.2f")
                caja_alta = st.segmented_control("Caja que lo paga", CAJAS, default=None)
                obs = st.text_input("Observaciones")
                if st.form_submit_button("Agregar", type="primary"):
                    categoria = cat_nueva.strip() if cat_sel == "Nueva categoría…" else cat_sel
                    if not concepto.strip() or not categoria:
                        st.error("Completá la categoría y el concepto.")
                    else:
                        misma = df_mes[df_mes["categoria"] == categoria]
                        orden = (float(misma["orden"].max()) + 0.5) if not misma.empty else (
                            float(df_mes["orden"].max()) + 1 if not df_mes.empty else 1.0)
                        fila_nueva = {
                            "id": _id_nuevo(), "mes": mes, "orden": orden, "categoria": categoria,
                            "concepto": concepto.strip(), "periodo": periodo.strip(), "importe": float(imp),
                            "pagado": float(pag), "cubre": caja_alta or "", "observaciones": obs.strip(),
                            "fecha": f"{mes}-01", "empresa": "",
                        }
                        nuevo = pd.concat([datos, pd.DataFrame([fila_nueva])], ignore_index=True)
                        _guardar(renumerar(normalizar(nuevo), mes), "Concepto agregado")

        if df_mes.empty:
            return
        st.caption("Tocá una celda para cambiarla. Para borrar, marcá 🗑️ y guardá. "
                   "El saldo y el estado se calculan solos.")
        tabla = df_mes[["id", "categoria", "concepto", "periodo", "importe", "pagado", "saldo", "cubre",
                        "observaciones"]].copy()
        tabla["cubre"] = tabla["cubre"].replace("", SIN_ASIGNAR)
        tabla.insert(1, "borrar", False)
        tabla = tabla.set_index("id")
        editada = st.data_editor(
            tabla,
            hide_index=True,
            use_container_width=True,
            num_rows="fixed",
            height=min(80 + 36 * len(tabla), 720),
            disabled=["saldo"],
            column_config={
                "borrar": st.column_config.CheckboxColumn("🗑️", width="small", help="Marcar para borrar"),
                "categoria": st.column_config.SelectboxColumn("Categoría", options=cats, required=True, width="medium"),
                "concepto": st.column_config.TextColumn("Concepto", width="large", required=True),
                "periodo": st.column_config.TextColumn("Período", width="small"),
                "importe": st.column_config.NumberColumn("Monto", min_value=0.0, format="localized"),
                "pagado": st.column_config.NumberColumn("Pagado", min_value=0.0, format="localized"),
                "saldo": st.column_config.NumberColumn("Saldo", format="localized"),
                "cubre": st.column_config.SelectboxColumn("Paga", options=CAJAS + [SIN_ASIGNAR], required=True),
                "observaciones": st.column_config.TextColumn("Observaciones", width="medium"),
            },
            key=f"pp2_editor_{mes}_{_rev()}",
        )
        borrar = [i for i, v in editada["borrar"].items() if bool(v)]
        b1, b2 = st.columns(2)
        with b1:
            guardar_ok = st.button(
                "Guardar cambios" + (f" y borrar {len(borrar)}" if borrar else ""), type="primary",
                key=f"pp2_editor_guardar_{mes}", icon=":material/save:", use_container_width=True,
            )
        with b2:
            if st.button("Descartar cambios", key=f"pp2_editor_descartar_{mes}", use_container_width=True):
                _subir_rev()
                st.rerun()
        if guardar_ok:
            nuevo = datos.set_index("id")
            for fid, f in editada.iterrows():
                if fid not in nuevo.index:
                    continue
                for col in ("categoria", "concepto", "periodo", "observaciones"):
                    nuevo.at[fid, col] = _texto(f[col])
                nuevo.at[fid, "importe"] = float(numero(f["importe"]) or 0.0)
                nuevo.at[fid, "pagado"] = float(numero(f["pagado"]) or 0.0)
                nuevo.at[fid, "cubre"] = "" if f["cubre"] == SIN_ASIGNAR else normalizar_caja(f["cubre"])
            nuevo = nuevo.drop(index=[b for b in borrar if b in nuevo.index]).reset_index()
            _guardar(renumerar(normalizar(nuevo), mes), "Cambios guardados")
        return

    # ---- Nuevo mes ---------------------------------------------------------
    if seccion == "Nuevo mes":
        if df_mes.empty:
            st.info("Elegí arriba un mes con conceptos para usarlo de base.")
            return
        destino = mes_siguiente(mes)
        st.markdown(f"Crear **{nombre_mes(destino)}** a partir de **{nombre_mes(mes)}**.")
        if destino in meses:
            st.info(f"{nombre_mes(destino)} ya existe. Elegilo arriba para verlo o editarlo.")
            return
        pasar = st.checkbox("Pasar lo que quedó con saldo (con lo ya pagado a cuenta)", value=True, key="pp2_nm_saldos")
        repetir = st.checkbox("Repetir lo que ya se pagó, con el período siguiente y pagado en cero "
                              "(sueldos, aportes y gastos fijos: después revisás los montos)", value=True,
                              key="pp2_nm_repetir")
        copiar = st.checkbox("Mantener la caja que paga cada concepto", value=True, key="pp2_nm_cajas")
        nuevo_mes = crear_mes(datos, mes, destino, pasar_saldos=pasar, repetir_pagados=repetir, copiar_cajas=copiar)
        if nuevo_mes.empty:
            st.warning("Con esas opciones el mes nuevo quedaría vacío.")
            return
        tn = totales(nuevo_mes)
        st.caption(f"Va a tener {tn['conceptos']} conceptos · Monto {pesos(tn['importe'])} · "
                   f"Saldo {pesos(tn['saldo'])}.")
        if st.button(f"Crear {nombre_mes(destino)}", type="primary", key="pp2_nm_crear",
                     icon=":material/calendar_add_on:"):
            st.session_state["pp2_mes_pendiente"] = destino
            _guardar(pd.concat([datos, nuevo_mes], ignore_index=True), f"{nombre_mes(destino)} creado")
        return

    # ---- Importar ----------------------------------------------------------
    if seccion == "Importar":
        if "2026-11" not in meses:
            st.markdown("**Planilla de noviembre 2026**")
            st.caption("Carga los 36 conceptos de la planilla que venían completando a mano "
                       "(total $58.235.711,69 · pagado $34.262.820,32 · saldo $23.972.891,37).")
            if st.button("Cargar noviembre 2026", key="pp2_semilla", icon=":material/playlist_add:"):
                _guardar(pd.concat([datos, planilla_noviembre_2026()], ignore_index=True),
                         "Planilla de noviembre 2026 cargada")
            st.divider()
        st.markdown("**Importar desde Excel**")
        st.caption("Con el mismo formato de la planilla: título «PAGOS PENDIENTES MES AÑO», columnas CONCEPTO, "
                   "PERIODO, MONTO, PAGADO, SALDO y las categorías en filas propias. Mejor .xlsx (respeta los colores).")
        archivo = st.file_uploader("Archivo", type=["xlsx", "csv"], key="pp2_archivo", label_visibility="collapsed")
        if archivo is None:
            return
        contenido = archivo.getvalue()
        hoja = None
        try:
            mes_det, importado, hojas = leer_archivo(archivo.name, contenido)
            if len(hojas) > 1:
                hoja = st.selectbox("Hoja", hojas, key="pp2_hoja")
                mes_det, importado, _ = leer_archivo(archivo.name, contenido, hoja)
        except Exception as exc:
            _error("No pude leer el archivo.", exc)
            return
        if importado.empty:
            st.warning("No encontré conceptos en el archivo.")
            return
        opciones = sorted(set([mes_det] if mes_det else []) | {mes_actual(), mes} | set(meses) - {"Sin mes"},
                          reverse=True)
        destino = st.selectbox("Mes de la planilla", opciones,
                               index=opciones.index(mes_det) if mes_det in opciones else 0,
                               format_func=nombre_mes, key="pp2_imp_mes")
        prev = normalizar(importado.assign(mes=destino))
        tp = totales(prev)
        st.caption(f"{tp['conceptos']} conceptos en {len(categorias_en_orden(prev))} categorías · "
                   f"Monto {pesos(tp['importe'])} · Pagado {pesos(tp['pagado'])} · Saldo {pesos(tp['saldo'])}")
        st.markdown(planilla_html(prev), unsafe_allow_html=True)
        existe = destino in meses
        reemplazar = True
        if existe:
            reemplazar = st.checkbox(f"Reemplazar lo que ya hay en {nombre_mes(destino)}", value=False,
                                     key="pp2_imp_reemplazar")
        if st.button(f"Importar a {nombre_mes(destino)}", type="primary", disabled=existe and not reemplazar,
                     key="pp2_imp_ok", icon=":material/upload_file:"):
            prev["id"] = [_id_nuevo() for _ in range(len(prev))]
            prev["fecha"] = f"{destino}-01"
            resto = datos[datos["mes"] != destino] if existe else datos
            st.session_state["pp2_mes_pendiente"] = destino
            _guardar(pd.concat([resto, prev], ignore_index=True), f"Planilla de {nombre_mes(destino)} importada")
        return
