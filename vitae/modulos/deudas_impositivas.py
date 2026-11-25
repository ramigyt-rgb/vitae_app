"""Deudas impositivas VMR / VM: la planilla de deudas, prolija y al día.

Muestra la información igual que la planilla que se usa:

- RENTAS (provincia): impuesto, período, vencimiento, capital, intereses,
  deuda total, con su TOTAL.
- ARCA (AFIP): F931 agrupado por período (seguro de vida, aportes y
  contribuciones, ART…), Anticipos de Ganancias, IVA y otros, con su TOTAL.
- Sindicatos (ATSA, FATSA…): período, cuota sindical / solidaridad /
  contribución convencional y total, con su TOTAL.

Las filas pueden llevar una marca de color (amarillo / salmón) como en la
planilla. Se guarda en la pestaña existente (deudas_impositivas_vmr / _vm)
con las mismas columnas de siempre, más «seccion» y «marca».
"""
from __future__ import annotations

import html
import io
import re
import uuid
from datetime import date
from typing import Any, Callable

import pandas as pd
import streamlit as st

from vitae.modulos.pagos_pendientes import _sin_acentos, _texto, numero, pesos

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

try:
    from vitae.core.helpers import leer_fechas_ar
except Exception:  # pragma: no cover
    def leer_fechas_ar(valores):
        return pd.to_datetime(pd.Series(valores), dayfirst=True, errors="coerce")


EMPRESA = {
    "deudas_impositivas_vmr": ("VITAE MEDICINA REPRODUCTIVA SRL", "30-71250000-6"),
    "deudas_impositivas_vm": ("VITAE MEDICAL SRL", ""),
}

RENTAS, F931, ANTICIPOS, GANANCIAS, IVA, OTROS, SINDICATO = (
    "Rentas", "F931", "Anticipos de Ganancias", "Ganancias", "IVA", "Otros impuestos", "Sindicato")
SECCIONES_ARCA = [F931, ANTICIPOS, GANANCIAS, IVA, OTROS]
SECCIONES = [RENTAS] + SECCIONES_ARCA + [SINDICATO]
BLOQUES = ["Rentas", "ARCA (AFIP)", "Sindicatos"]
MARCAS = ["", "Amarillo", "Salmón"]
FONDO_MARCA = {"Amarillo": "#FFF3A6", "Salmón": "#FBE1D3"}

CODIGOS_F931 = [
    "28 - SEGURO DE VIDA COLECTIVO", "301 - APORTES SEG SOCIAL", "302 - APORTES OBRAS SOCIALES",
    "312 - ASEG.RIESGO DE TRABAJO L 24557", "351 - CONTRIBUCIONES SEG SOCIAL", "352 - CONTRIBUCIONES OBRA SOCIAL",
]
IMPUESTO_POR_DEFECTO = {RENTAS: "Activ. Economicas | DDJJ Mensual", ANTICIPOS: "10 - GANANCIAS Anticipos",
                        IVA: "DDJJ Mensual", GANANCIAS: "Ganancias DDJJ", OTROS: ""}

COLUMNAS = [
    "id_registro", "seccion", "organismo", "impuesto", "periodo", "fecha_vencimiento", "capital", "intereses",
    "cuota_sindical", "cuota_solidaridad", "contribucion_convencional", "deuda_total", "importe_pagado", "saldo",
    "estado", "marca", "fecha_pago", "plan_pago", "observaciones",
]
_MONTOS = ["capital", "intereses", "cuota_sindical", "cuota_solidaridad", "contribucion_convencional",
           "deuda_total", "importe_pagado"]
_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------
def leer_periodo(valor: Any) -> str:
    """'feb-25', 'sept-25', '202601', '2026-01', '01/2026', fechas → 'AAAA-MM' ('' si no se entiende)."""
    if isinstance(valor, (pd.Timestamp, date)) and not pd.isna(valor):
        return f"{valor.year:04d}-{valor.month:02d}"
    if isinstance(valor, (int, float)) and not pd.isna(valor) and 190001 <= int(valor) <= 210012:
        valor = str(int(valor))
    t = _sin_acentos(_texto(valor)).replace(" ", "")
    if not t:
        return ""
    m = re.fullmatch(r"(\d{4})[-/]?(\d{2})", t)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-\d{1,2}.*", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(\d{1,2})[-/](\d{4})", t)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.fullmatch(r"([a-z]{3,10})\.?[-/ ]?(\d{2}|\d{4})", t)
    if m:
        nombre = m.group(1)[:3]
        if nombre in _MESES:
            anio = int(m.group(2))
            anio += 2000 if anio < 100 else 0
            return f"{anio:04d}-{_MESES.index(nombre) + 1:02d}"
    return ""


def periodo_planilla(periodo: str, seccion: str) -> str:
    """Como en la planilla: F931/Ganancias '202601'; Rentas y sindicatos 'feb-25'."""
    if not re.fullmatch(r"\d{4}-\d{2}", str(periodo or "")):
        return str(periodo or "")
    anio, mes = int(periodo[:4]), int(periodo[5:])
    if seccion in (F931, ANTICIPOS, GANANCIAS, IVA, OTROS):
        return f"{anio}{mes:02d}"
    return f"{_MESES[mes - 1]}-{anio % 100:02d}"


def seccion_de(organismo: str, impuesto: str, sindical: bool = False) -> str:
    o, i = _sin_acentos(organismo), _sin_acentos(impuesto)
    t = f"{o} {i}"
    if sindical or any(x in o for x in ("atsa", "fatsa", "sindic", "uocra", "uatre", "osecac", "faecys")):
        return SINDICATO
    if any(x in t for x in ("renta", "dgr", "ingresos brutos", "activ. econ", "actividades econ")):
        return RENTAS
    if "931" in t or re.match(r"^(28|301|302|312|351|352)\s*-", i) or any(
            x in i for x in ("seguridad social", "seg social", "obra social", "obras sociales", "seguro de vida",
                             "riesgo de trabajo")):
        return F931
    if "ganancia" in t:
        return ANTICIPOS if "anticip" in t else GANANCIAS
    if re.search(r"\biva\b", t):
        return IVA
    return OTROS


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _id_fijo(fila: pd.Series, posicion: int) -> str:
    """Identificador estable para filas que no lo tienen en la pestaña: el
    mismo en cada lectura (si cambiara, el editor perdería lo editado)."""
    import hashlib

    clave = "|".join(_texto(fila.get(c, "")) for c in ("organismo", "impuesto", "periodo", "fecha_vencimiento",
                                                      "capital", "intereses", "deuda_total")) + f"|{posicion}"
    return "DI-" + hashlib.sha1(clave.encode("utf-8")).hexdigest()[:12].upper()


def normalizar(df: pd.DataFrame | None) -> pd.DataFrame:
    """Una fila por obligación, con sección, período AAAA-MM, importes y saldo.

    Lee tanto lo cargado con este módulo como lo importado antes (mismas
    columnas: organismo, impuesto, periodo, fecha_vencimiento, capital...).
    Conserva cualquier otra columna de la pestaña.
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=COLUMNAS + ["_vto", "_bloque", "_grupo"])
    d = df.copy().reset_index(drop=True)
    d.columns = [str(c).strip() for c in d.columns]
    for c in COLUMNAS:
        if c not in d.columns:
            d[c] = ""
    for c in _MONTOS:
        d[c] = [numero(v) or 0.0 for v in d[c]]
    d["id_registro"] = [(_texto(v) or _id_fijo(f, i)) for i, (v, (_, f)) in enumerate(zip(d["id_registro"], df.iterrows()))]
    # Si la pestaña tiene identificadores repetidos, se distinguen (si no, editar una fila tocaría otra).
    repetidos = d["id_registro"].duplicated(keep="first")
    if repetidos.any():
        d.loc[repetidos, "id_registro"] = [f"{v}-{i}" for i, v in zip(d.index[repetidos], d.loc[repetidos, "id_registro"])]
    d["organismo"] = d["organismo"].map(_texto)
    d["impuesto"] = d["impuesto"].map(_texto)
    sindical = (d["cuota_sindical"].abs() + d["cuota_solidaridad"].abs() + d["contribucion_convencional"].abs()) > 0
    d["seccion"] = [(_texto(s) if _texto(s) in SECCIONES else seccion_de(o, i, bool(si)))
                    for s, o, i, si in zip(d["seccion"], d["organismo"], d["impuesto"], sindical)]
    # Deuda total: la cargada, o la suma de sus componentes.
    componentes = d[["capital", "intereses", "cuota_sindical", "cuota_solidaridad", "contribucion_convencional"]].sum(axis=1)
    d["deuda_total"] = [t if abs(t) > 0.004 else c for t, c in zip(d["deuda_total"], componentes)]
    d["saldo"] = (d["deuda_total"] - d["importe_pagado"]).round(2)
    d["periodo"] = d["periodo"].map(leer_periodo)
    d["_vto"] = pd.to_datetime(leer_fechas_ar(d["fecha_vencimiento"].map(_texto)).values, errors="coerce")
    d["marca"] = [m if m in MARCAS else ("Amarillo" if "amar" in _sin_acentos(m) else
                                          "Salmón" if "salm" in _sin_acentos(m) or "naranj" in _sin_acentos(m) else "")
                  for m in d["marca"].map(_texto)]
    d["observaciones"] = d["observaciones"].map(_texto)
    d["estado"] = ["Pagada" if s <= 0.004 and t > 0.004 else (_texto(e) or "Pendiente")
                   for s, t, e in zip(d["saldo"], d["deuda_total"], d["estado"])]
    d["_bloque"] = ["Rentas" if s == RENTAS else "Sindicatos" if s == SINDICATO else "ARCA (AFIP)" for s in d["seccion"]]
    d["organismo"] = [o or ({RENTAS: "RENTAS", SINDICATO: "SINDICATO"}.get(s, s.upper())) for o, s in zip(d["organismo"], d["seccion"])]
    d["_grupo"] = [o.upper() if s == SINDICATO else s for o, s in zip(d["organismo"], d["seccion"])]
    d["_orden"] = range(len(d))
    return d


def para_guardar(d: pd.DataFrame) -> pd.DataFrame:
    out = d.copy()
    for c in COLUMNAS:
        if c not in out.columns:
            out[c] = ""
    out["fecha_vencimiento"] = [f.strftime("%Y-%m-%d") if pd.notna(f) else "" for f in
                                pd.to_datetime(out.get("_vto", out["fecha_vencimiento"]), errors="coerce")]
    componentes = out[["capital", "intereses", "cuota_sindical", "cuota_solidaridad", "contribucion_convencional"]].astype(float).sum(axis=1)
    out["deuda_total"] = [float(t) if abs(float(t)) > 0.004 else c for t, c in zip(out["deuda_total"], componentes)]
    out["saldo"] = (out["deuda_total"].astype(float) - out["importe_pagado"].astype(float)).round(2)
    out["estado"] = ["Pagada" if s <= 0.004 and t > 0.004 else ("Pendiente" if e in ("", "Pagada") else e)
                     for s, t, e in zip(out["saldo"], out["deuda_total"], out["estado"].map(_texto))]
    for c in _MONTOS + ["saldo"]:
        out[c] = [f"{float(v):.2f}" for v in out[c]]
    extras = [c for c in out.columns if c not in COLUMNAS and not str(c).startswith("_")]
    return out[COLUMNAS + extras]


_COMPONENTES = ("capital", "intereses", "cuota_sindical", "cuota_solidaridad", "contribucion_convencional")
COLS_EDICION = ["seccion", "organismo", "impuesto", "periodo", "_vto", "capital", "intereses", "cuota_sindical",
                "cuota_solidaridad", "contribucion_convencional", "deuda_total", "importe_pagado", "estado", "marca",
                "plan_pago", "observaciones"]


def tabla_edicion(x: pd.DataFrame) -> pd.DataFrame:
    """Todas las columnas de la deuda, editables (índice: id_registro)."""
    t = x[["id_registro"] + COLS_EDICION].copy()
    t["periodo"] = [periodo_planilla(p, sec) for p, sec in zip(x["periodo"], x["seccion"])]
    t["_vto"] = pd.to_datetime(t["_vto"], errors="coerce").dt.date
    t["plan_pago"] = t["plan_pago"].map(_texto)
    t["estado"] = [e if e in ESTADOS else ("Pendiente" if not e else e) for e in t["estado"].map(_texto)]
    t.insert(1, "borrar", False)
    return t.set_index("id_registro")


def _valor(c: str, v: Any, actual: Any = None) -> Any:
    if c == "periodo":
        return leer_periodo(v) or (actual if actual is not None else "")
    if c == "_vto":
        return pd.Timestamp(v) if v is not None and not pd.isna(v) and _texto(v) else pd.NaT
    if c in _MONTOS:
        return float(numero(v) or 0.0)
    return _texto(v)


def aplicar_edicion(d: pd.DataFrame, original: pd.DataFrame, editada: pd.DataFrame,
                    cols: list[str] | None = None) -> tuple[pd.DataFrame, int]:
    """Aplica sólo las celdas que cambiaron, agrega las filas nuevas y borra las marcadas.

    Deuda total: si se escribió a mano, se respeta; si cambió un componente
    (capital, intereses o importes sindicales), se recalcula. Estado «Pagada»
    con saldo pendiente registra el pago completo.
    """
    cols = cols or COLS_EDICION
    nuevo = d.set_index("id_registro")
    cambios = 0

    def distinto(a: Any, b: Any, c: str) -> bool:
        if c in _MONTOS:
            return abs(float(numero(a) or 0.0) - float(numero(b) or 0.0)) > 0.004
        if c == "_vto":
            va, vb = _valor(c, a), _valor(c, b)
            if pd.isna(va) and pd.isna(vb):
                return False
            return pd.isna(va) or pd.isna(vb) or va != vb
        if c == "periodo":
            return leer_periodo(a) != leer_periodo(b)
        return _texto(a) != _texto(b)

    filas_nuevas = []
    for i, f in editada.iterrows():
        if bool(f.get("borrar", False)):
            continue
        if i in original.index and i in nuevo.index:
            o = original.loc[i]
            cambiadas = [c for c in cols if c in f.index and distinto(f[c], o[c], c)]
            if not cambiadas:
                continue
            for c in cambiadas:
                nuevo.at[i, c] = _valor(c, f[c], nuevo.at[i, c] if c == "periodo" else None)
            if "deuda_total" not in cambiadas and any(c in _COMPONENTES for c in cambiadas):
                nuevo.at[i, "deuda_total"] = sum(float(nuevo.at[i, c]) for c in _COMPONENTES)
            if "estado" in cambiadas and _texto(f["estado"]) == "Pagada":
                nuevo.at[i, "importe_pagado"] = float(nuevo.at[i, "deuda_total"])
            cambios += 1
        elif any(_texto(f.get(c, "")) for c in ("impuesto", "organismo")) or any(
                float(numero(f.get(c)) or 0) for c in _COMPONENTES + ("deuda_total",)):
            fila = {c: _valor(c, f.get(c)) for c in cols}
            fila["id_registro"] = _id()
            fila["fecha_vencimiento"] = fila["_vto"]
            if abs(fila.get("deuda_total") or 0) <= 0.004:
                fila["deuda_total"] = sum(float(fila.get(c) or 0) for c in _COMPONENTES)
            if not fila.get("seccion"):
                fila["seccion"] = seccion_de(fila.get("organismo", ""), fila.get("impuesto", ""))
            filas_nuevas.append(fila)
    borrar = [i for i, f in editada.iterrows() if bool(f.get("borrar", False)) and i in nuevo.index]
    nuevo = nuevo.drop(index=borrar).reset_index()
    if filas_nuevas:
        nuevo = pd.concat([nuevo, pd.DataFrame(filas_nuevas)], ignore_index=True)
    nuevo["fecha_vencimiento"] = nuevo["_vto"]
    return normalizar(para_guardar(nuevo)), cambios + len(borrar) + len(filas_nuevas)


def vencido(d: pd.DataFrame, hoy: pd.Timestamp | None = None) -> pd.Series:
    hoy = (hoy or pd.Timestamp.today()).normalize()
    return (d["saldo"] > 0.004) & d["_vto"].notna() & (d["_vto"] < hoy)


def totales(d: pd.DataFrame, hoy: pd.Timestamp | None = None) -> dict[str, Any]:
    act = d[d["saldo"] > 0.004] if not d.empty else d
    out: dict[str, Any] = {"total": float(act["saldo"].sum()) if not act.empty else 0.0}
    for b in BLOQUES:
        x = act[act["_bloque"] == b] if not act.empty else act
        out[b] = float(x["saldo"].sum()) if not x.empty else 0.0
        out[f"{b}_vencido"] = float(x.loc[vencido(x, hoy), "saldo"].sum()) if not x.empty else 0.0
        out[f"{b}_n"] = int(len(x))
    out["vencido"] = float(act.loc[vencido(act, hoy), "saldo"].sum()) if not act.empty else 0.0
    return out


# ---------------------------------------------------------------------------
# Planilla de VMR (fotos de noviembre 2026)
# ---------------------------------------------------------------------------
def planilla_vmr() -> pd.DataFrame:
    filas: list[dict[str, Any]] = []

    def imp(seccion, organismo, impuesto, periodo, vto, capital, intereses, marca=""):
        filas.append({"seccion": seccion, "organismo": organismo, "impuesto": impuesto, "periodo": periodo,
                      "fecha_vencimiento": vto, "capital": capital, "intereses": intereses, "marca": marca})

    for per, vto, cap, inte in (("2025-02", "2025-03-19", 525210.10, 81538.73),
                                ("2024-07", "2024-08-19", 62770.68, 38866.53),
                                ("2024-01", "2024-02-19", 310978.60, 382764.11)):
        imp(RENTAS, "RENTAS", "Activ. Economicas | DDJJ Mensual", per, vto, cap, inte)
    SV, OS302, OS352 = "28 - SEGURO DE VIDA COLECTIVO", "302 - APORTES OBRAS SOCIALES", "352 - CONTRIBUCIONES OBRA SOCIAL"
    f931 = [
        ("2026-01", "2026-02-10", "Amarillo", [(SV, 2008.08)]),
        ("2026-02", "2026-03-10", "Amarillo", [(SV, 2008.08)]),
        ("2026-03", "2026-04-10", "Amarillo", [(SV, 2547.72)]),
        ("2026-04", "2026-05-10", "Amarillo", [(SV, 2547.72), (OS302, 223433.65), (OS352, 446867.28)]),
        ("2026-05", "2026-06-10", "Amarillo", [(SV, 2547.72), (OS302, 224633.06), (OS352, 449266.10)]),
        ("2026-06", "2026-07-10", "Amarillo", [(SV, 2547.72), (OS302, 352620.52), (OS352, 705241.03)]),
        ("2026-07", "2026-08-11", "Salmón", [(SV, 2547.72), (OS302, 245276.48), (OS352, 490553.00)]),
        ("2026-08", "2026-09-10", "Salmón", [(SV, 2547.72), (OS302, 243166.00), (OS352, 486331.98)]),
        ("2026-09", "2026-10-13", "Salmón", [(SV, 2547.72), (OS302, 226168.54), (OS352, 452337.09)]),
        ("2026-10", "2026-11-13", "", [(SV, 2123.10), (OS302, 212757.86), ("351 - CONTRIBUCIONES SEG SOCIAL", 1568807.87),
                                       ("312 - ASEG.RIESGO DE TRABAJO L 24557", 238058.92),
                                       ("301 - APORTES SEG SOCIAL", 1205627.86), (OS352, 425515.72)]),
    ]
    for per, vto, marca, lineas in f931:
        for impuesto, cap in lineas:
            imp(F931, "F931", impuesto, per, vto, cap, 0.0, marca)
    for per, vto, inte in (("2023-01", "2023-06-14", 213129.41), ("2023-02", "2023-07-14", 71014.73),
                           ("2023-03", "2023-08-15", 71014.73), ("2023-04", "2023-09-14", 71014.73),
                           ("2023-05", "2023-10-18", 71014.73), ("2023-06", "2023-11-14", 71014.73),
                           ("2023-08", "2024-01-16", 71014.70), ("2023-09", "2024-02-15", 46214.11),
                           ("2023-10", "2024-03-14", 2687.84)):
        imp(ANTICIPOS, "ANTICIPO GANANCIAS", "10 - GANANCIAS Anticipos", per, vto, 0.0, inte, "Salmón")
    imp(IVA, "IVA", "DDJJ Mensual", "", "", 0.0, 0.0)

    atsa = [("ago-17", 3469.70), ("nov-17", 3284.78), ("oct-18", 4086.44), ("nov-18", 4232.96), ("abr-19", 5618.02),
            ("may-19", 2814.37), ("jun-19", 3825.32), ("jul-19", 3438.38), ("sep-19", 3647.42), ("may-20", 4510.79),
            ("jun-20", 6766.18), ("jul-20", 4510.79), ("ago-20", 4510.79), ("sep-20", 4510.79), ("oct-20", 5100.24),
            ("nov-20", 5100.24), ("dic-20", 7700.37), ("ene-21", 5346.49), ("feb-21", 5344.16), ("mar-21", 5471.38),
            ("abr-21", 6159.24), ("may-21", 6199.05), ("jun-21", 9298.57), ("jul-21", 7128.91), ("ago-21", 7388.13),
            ("sep-21", 6269.04), ("feb-22", 7464.06), ("mar-22", 7614.68), ("abr-22", 7784.24), ("may-22", 6310.44),
            ("jun-22", 10091.02), ("jul-22", 6935.80), ("ago-22", 11764.79), ("sep-22", 11764.79), ("oct-22", 14054.34),
            ("nov-22", 15573.72), ("dic-22", 25639.67), ("ene-23", 17093.11), ("feb-23", 17757.84), ("mar-23", 18761.90),
            ("abr-23", 20450.47), ("may-23", 22198.84), ("jun-23", 36391.93), ("jul-23", 26635.86), ("ago-23", 27684.48),
            ("sep-23", 30597.76), ("oct-23", 28531.61), ("nov-23", 32142.12), ("dic-23", 61377.94), ("ene-24", 46056.44),
            ("feb-24", 58362.88), ("mar-24", 65463.65), ("abr-24", 65281.08), ("may-24", 72465.79), ("jun-24", 119701.74),
            ("jul-24", 86469.85), ("ago-24", 69529.26), ("sep-24", 71672.01), ("oct-24", 96256.86), ("nov-24", 99586.51),
            ("dic-24", 155591.06), ("ene-25", 108093.74), ("feb-25", 103593.35), ("mar-25", 108312.18),
            ("abr-25", 108957.29), ("jul-25", 92094.27), ("ago-25", 94359.12), ("sept-25", 99182.65),
            ("oct-25", 100769.57), ("nov-25", 102281.12)]
    for per, cuota in atsa:
        filas.append({"seccion": SINDICATO, "organismo": "ATSA", "impuesto": "Cuota sindical", "periodo": per,
                      "cuota_sindical": cuota})
    fatsa = [("feb-21", 1174.00, 5344.16), ("mar-21", 1174.00, 5471.38), ("abr-21", 1174.00, 6159.24),
             ("may-21", 1174.00, 6199.05), ("jun-21", 0.0, 9298.57), ("jul-21", 2420.00, 7128.91),
             ("ago-21", 2420.00, 7388.92), ("sep-21", 1936.00, 6269.04), ("feb-22", 1936.00, 7464.06),
             ("mar-22", 1936.00, 7614.68), ("abr-22", 1936.00, 7784.24), ("may-22", 2736.00, 6310.44),
             ("jun-22", 0.0, 12253.46), ("jul-22", 4560.00, 10179.47), ("ago-22", 4560.00, 11764.79),
             ("sep-22", 4560.00, 11764.79), ("oct-22", 4560.00, 14054.34), ("nov-22", 4560.00, 15573.72),
             ("dic-22", 0.0, 25639.67), ("ene-23", 4560.00, 17093.11), ("feb-23", 4560.00, 17757.84),
             ("mar-23", 4560.00, 18761.90), ("abr-23", 4560.00, 20450.47), ("may-23", 12500.00, 22198.84),
             ("jun-23", 0.0, 36391.93), ("jul-23", 12500.00, 26635.86), ("ago-23", 12500.00, 27684.48),
             ("sep-23", 12500.00, 30597.76), ("oct-23", 12500.00, 28531.61), ("nov-23", 17500.00, 32142.12),
             ("dic-23", 0.0, 61377.94), ("ene-24", 17500.00, 46056.44), ("feb-24", 17500.00, 58362.88),
             ("mar-24", 15000.00, 65463.65), ("abr-24", 15000.00, 65281.08), ("may-24", 27000.00, 72465.79),
             ("jun-24", 0.0, 119701.74), ("jul-24", 27000.00, 86469.85), ("ago-24", 23512.50, 69529.26),
             ("sep-24", 24412.50, 73127.10), ("oct-24", 30466.80, 96256.86), ("nov-24", 31580.04, 99586.51),
             ("dic-24", 0.0, 155591.06), ("ene-25", 32634.66, 108093.74), ("feb-25", 37008.00, 103593.35),
             ("mar-25", 37638.00, 108312.18), ("abr-25", 38238.00, 108957.29), ("jul-25", 64231.89, 140758.04),
             ("ago-25", 88086.33, 142551.79), ("sept-25", 90041.94, 151286.54), ("oct-25", 89054.06, 150897.68),
             ("nov-25", 95430.02, 153161.15)]
    for per, sol, contrib in fatsa:
        filas.append({"seccion": SINDICATO, "organismo": "FATSA", "impuesto": "Solidaridad + contribución convencional",
                      "periodo": per, "cuota_solidaridad": sol, "contribucion_convencional": contrib})
    df = pd.DataFrame(filas)
    df["fecha_vencimiento"] = df["fecha_vencimiento"].fillna("") if "fecha_vencimiento" in df else ""
    return normalizar(df)


# ---------------------------------------------------------------------------
# Vista: la planilla
# ---------------------------------------------------------------------------
_CSS = """
<style>
.di-emp{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:.3rem .8rem;margin:.1rem 0 .5rem}
.di-emp b{font-size:1.02rem;letter-spacing:.02em;color:#1F1A1D}.di-emp span{font-size:.82rem;color:#7A6F75}
.di-bloque{border:1px solid #E8E3E6;border-radius:12px;overflow:hidden;background:#fff;margin-bottom:1rem}
.di-tit{display:flex;justify-content:space-between;align-items:center;gap:.6rem;padding:.6rem .9rem;background:#F7EFF3;border-bottom:1px solid #EADDE4}
.di-tit b{font-size:.8rem;letter-spacing:.05em;text-transform:uppercase;color:#5E1A39}
.di-tit span{font-size:.8rem;color:#6E6268;font-variant-numeric:tabular-nums}
.di-cols,.di-row{display:grid;grid-template-columns:118px minmax(0,1fr) 74px 92px 118px 112px 122px;gap:.5rem;align-items:center;padding:.4rem .9rem}
.di-cols{font-size:.68rem;font-weight:600;color:#8A7F85;text-transform:uppercase;letter-spacing:.04em;padding-top:.45rem;padding-bottom:.25rem}
.di-row{border-top:1px solid #F1ECEF;font-size:.86rem}
.di-g{font-weight:650;font-size:.76rem;color:#5E1A39;text-transform:uppercase;letter-spacing:.03em}
.di-n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.di-c{text-align:center;font-variant-numeric:tabular-nums;color:#5D5258}
.di-imp{min-width:0;overflow-wrap:anywhere;color:#1F1A1D}
.di-imp small{display:block;color:#8A7F85;font-size:.74rem}
.di-dt{font-weight:650}
.di-venc{color:#B14E5E}
.di-pag{opacity:.55;text-decoration:line-through}
.di-tot{background:#EFE7EB;font-weight:700;border-top:1px solid #DCCFD6}
.di-grp{border-top:2px solid #E6DCE1}
.di-meta{display:none}
.di-sind{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.di-srow{display:grid;gap:.5rem;align-items:center;padding:.36rem .9rem;border-top:1px solid #F1ECEF;font-size:.86rem}
.di-scols{display:grid;gap:.5rem;padding:.45rem .9rem .25rem;font-size:.68rem;font-weight:600;color:#8A7F85;text-transform:uppercase;letter-spacing:.04em}
.di-ley{display:flex;gap:.8rem;flex-wrap:wrap;font-size:.78rem;color:#6E6268;margin:-.3rem 0 .7rem}
.di-ley i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:.3rem;vertical-align:-2px;border:1px solid #E4D9A0}
@media (max-width: 760px){
 .di-cols{display:none}
 .di-row{grid-template-columns:minmax(0,1fr) auto;gap:.1rem .6rem;padding:.5rem .7rem}
 .di-row .di-g,.di-row .di-c,.di-row .di-cap,.di-row .di-int{display:none}
 .di-meta{display:block;grid-column:1 / -1;font-size:.74rem;color:#8A7F85}
 .di-sind{grid-template-columns:minmax(0,1fr)}
 .di-tit{flex-direction:column;align-items:flex-start;gap:.1rem}
}
</style>
"""


def _fmt(v: float) -> str:
    return pesos(v) if abs(v) > 0.004 else ""


def _fila_impuesto(f: pd.Series, grupo: str, hoy: pd.Timestamp, primera: bool) -> str:
    e = html.escape
    fondo = FONDO_MARCA.get(f["marca"], "")
    estilo = f"background:{fondo}" if fondo else ""
    vto = f["_vto"].strftime("%d/%m/%Y") if pd.notna(f["_vto"]) else ""
    es_venc = bool(f["saldo"] > 0.004 and pd.notna(f["_vto"]) and f["_vto"] < hoy)
    pagada = f["saldo"] <= 0.004 and f["deuda_total"] > 0.004
    obs = f"<small>{e(f['observaciones'])}</small>" if f["observaciones"] else ""
    if f["importe_pagado"] > 0.004 and not pagada:
        obs += f"<small>Pagado {e(pesos(f['importe_pagado']))} · saldo {e(pesos(f['saldo']))}</small>"
    per = periodo_planilla(f["periodo"], f["seccion"])
    meta = " · ".join(x for x in (per, f"vence {vto}" if vto else "", f"capital {pesos(f['capital'])}" if f["capital"] else "",
                                  f"intereses {pesos(f['intereses'])}" if f["intereses"] else "") if x)
    return (f'<div class="di-row{" di-grp" if primera else ""}{" di-pag" if pagada else ""}" style="{estilo}">'
            f'<div class="di-g">{e(grupo) if primera else ""}</div>'
            f'<div class="di-imp">{e(f["impuesto"] or "—")}{obs}</div>'
            f'<div class="di-c">{e(per)}</div>'
            f'<div class="di-c{" di-venc" if es_venc else ""}">{e(vto)}</div>'
            f'<div class="di-n di-cap">{_fmt(f["capital"]) if f["capital"] or f["deuda_total"] == 0 else ""}'
            f'{"" if f["capital"] or f["deuda_total"] else "$0,00"}</div>'
            f'<div class="di-n di-int">{_fmt(f["intereses"])}</div>'
            f'<div class="di-n di-dt">{pesos(f["deuda_total"])}</div>'
            f'<div class="di-meta">{e(meta)}</div></div>')


def _orden_periodo(d: pd.DataFrame, descendente: bool = False) -> pd.DataFrame:
    return d.assign(_p=d["periodo"].replace("", "9999-99")).sort_values(["_p", "_orden"], ascending=[not descendente, True],
                                                                         kind="stable")


def html_bloque_impuestos(d: pd.DataFrame, bloque: str, hoy: pd.Timestamp) -> str:
    x = d[d["_bloque"] == bloque]
    if x.empty:
        return ""
    partes = [f'<div class="di-bloque"><div class="di-tit"><b>{html.escape(bloque)}</b>'
              f'<span>{len(x)} obligaciones · saldo {pesos(x["saldo"].clip(lower=0).sum())}</span></div>'
              '<div class="di-cols"><div></div><div>Impuesto</div><div style="text-align:center">Período</div>'
              '<div style="text-align:center">Fecha de vto</div><div style="text-align:right">Capital</div>'
              '<div style="text-align:right">Intereses</div><div style="text-align:right">Deuda total</div></div>']
    if bloque == "Rentas":
        grupos = [("RENTAS", _orden_periodo(x, descendente=True))]
    else:
        grupos = []
        for seccion in SECCIONES_ARCA:
            s = x[x["seccion"] == seccion]
            if s.empty:
                continue
            if seccion == F931:
                for per in sorted(s["periodo"].unique(), key=lambda p: p or "9999-99"):
                    grupos.append(("F931", s[s["periodo"] == per].sort_values("_orden", kind="stable")))
            else:
                nombre = {ANTICIPOS: "ANTICIPO GANANCIAS"}.get(seccion, seccion.upper())
                grupos.append((nombre, _orden_periodo(s)))
    for nombre, g in grupos:
        for k, (_, f) in enumerate(g.iterrows()):
            partes.append(_fila_impuesto(f, nombre, hoy, k == 0))
    activos = x[x["saldo"] > 0.004]
    partes.append(f'<div class="di-row di-tot"><div class="di-g">TOTAL</div><div></div><div></div><div></div>'
                  f'<div class="di-n di-cap">{pesos(activos["capital"].sum())}</div>'
                  f'<div class="di-n di-int">{pesos(activos["intereses"].sum())}</div>'
                  f'<div class="di-n di-dt">{pesos(activos["saldo"].sum())}</div>'
                  f'<div class="di-meta">Capital {pesos(activos["capital"].sum())} · intereses {pesos(activos["intereses"].sum())}</div></div>')
    partes.append("</div>")
    return "".join(partes)


_SIND_COLS = [("cuota_sindical", "Cuota sindical"), ("cuota_solidaridad", "Solidaridad / Extra"),
              ("contribucion_convencional", "Contribución convencional")]


def html_sindicato(x: pd.DataFrame, nombre: str, hoy: pd.Timestamp) -> str:
    e = html.escape
    cols = [(c, t) for c, t in _SIND_COLS if x[c].abs().sum() > 0.004]
    otros = (x["capital"].abs().sum() + x["intereses"].abs().sum()) > 0.004
    mostrar_total = len(cols) > 1 or otros or not cols
    plantilla = "70px " + " ".join(["minmax(0,1fr)"] * len(cols)) + (" minmax(0,1fr)" if mostrar_total else "")
    x = _orden_periodo(x)
    activos = x[x["saldo"] > 0.004]
    partes = [f'<div class="di-bloque"><div class="di-tit"><b>{e(nombre)}</b>'
              f'<span>{len(activos)} períodos · {pesos(activos["saldo"].sum())}</span></div>'
              f'<div class="di-scols" style="grid-template-columns:{plantilla}"><div>Período</div>'
              + "".join(f'<div style="text-align:right">{e(t)}</div>' for _, t in cols)
              + ('<div style="text-align:right">Total</div>' if mostrar_total else "") + "</div>"]
    for _, f in x.iterrows():
        fondo = FONDO_MARCA.get(f["marca"], "")
        pagada = f["saldo"] <= 0.004 and f["deuda_total"] > 0.004
        partes.append(f'<div class="di-srow{" di-pag" if pagada else ""}" style="grid-template-columns:{plantilla};'
                      f'{"background:" + fondo if fondo else ""}"><div class="di-c" style="text-align:left">'
                      f'{e(periodo_planilla(f["periodo"], SINDICATO))}</div>'
                      + "".join(f'<div class="di-n">{pesos(f[c]) if abs(f[c]) > 0.004 or c == "cuota_solidaridad" else ""}</div>'
                                for c, _ in cols)
                      + (f'<div class="di-n di-dt">{pesos(f["deuda_total"])}</div>' if mostrar_total else "") + "</div>")
    partes.append(f'<div class="di-srow di-tot" style="grid-template-columns:{plantilla}"><div>TOTAL</div>'
                  + "".join(f'<div class="di-n">{pesos(activos[c].sum())}</div>' for c, _ in cols)
                  + (f'<div class="di-n">{pesos(activos["saldo"].sum())}</div>' if mostrar_total else "") + "</div></div>")
    return "".join(partes)


def html_planilla(d: pd.DataFrame, hoy: pd.Timestamp, bloques: list[str] | None = None) -> str:
    bloques = bloques or BLOQUES
    partes = [_CSS]
    for b in ("Rentas", "ARCA (AFIP)"):
        if b in bloques:
            partes.append(html_bloque_impuestos(d, b, hoy))
    if "Sindicatos" in bloques:
        s = d[d["_bloque"] == "Sindicatos"]
        if not s.empty:
            nombres = sorted(s["_grupo"].unique())
            partes.append('<div class="di-sind">' + "".join(html_sindicato(s[s["_grupo"] == n], n, hoy) for n in nombres)
                          + "</div>")
    return "".join(partes)


# ---------------------------------------------------------------------------
# Excel con el formato de la planilla
# ---------------------------------------------------------------------------
def excel(d: pd.DataFrame, tabla: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    nombre, cuit = EMPRESA.get(tabla, ("", ""))
    gris = PatternFill("solid", fgColor="D9D9D9")
    rell = {"Amarillo": PatternFill("solid", fgColor="FFFF66"), "Salmón": PatternFill("solid", fgColor="FCE5CD")}
    fmt = '#,##0.00'
    wb = Workbook()
    ws = wb.active
    ws.title = "Impuestos"
    ws.append(["", f"DEUDAS {nombre}", "", "", cuit])
    ws.cell(1, 2).font = Font(bold=True)
    act = d[d["saldo"] > 0.004]
    for bloque in ("Rentas", "ARCA (AFIP)"):
        x = act[act["_bloque"] == bloque]
        if x.empty:
            continue
        ws.append([])
        ws.append(["", "IMPUESTO", "PERIODO", "FECHA DE VTO", "CAPITAL", "INTERESES", "DEUDA TOTAL"])
        for c in ws[ws.max_row][1:7]:
            c.fill, c.font = gris, Font(bold=True)
        secciones = [RENTAS] if bloque == "Rentas" else SECCIONES_ARCA
        for seccion in secciones:
            s = x[x["seccion"] == seccion]
            claves = sorted(s["periodo"].unique(), key=lambda p: p or "9999-99") if seccion == F931 else [None]
            for per in claves:
                g = s if per is None else s[s["periodo"] == per]
                g = _orden_periodo(g, descendente=seccion == RENTAS) if per is None else g
                for k, (_, f) in enumerate(g.iterrows()):
                    grupo = {RENTAS: "RENTAS", ANTICIPOS: "ANTICIPO GANANCIAS"}.get(seccion, seccion.upper()) if k == 0 else ""
                    ws.append([grupo, f["impuesto"], periodo_planilla(f["periodo"], seccion),
                               f["_vto"].to_pydatetime() if pd.notna(f["_vto"]) else None,
                               f["capital"] or None, f["intereses"] or None, f["saldo"]])
                    if f["marca"] in rell:
                        ws.cell(ws.max_row, 7).fill = rell[f["marca"]]
                    ws.cell(ws.max_row, 4).number_format = "dd/mm/yyyy"
                    for j in (5, 6, 7):
                        ws.cell(ws.max_row, j).number_format = fmt
        ws.append(["", "TOTAL", "", "", float(x["capital"].sum()), float(x["intereses"].sum()), float(x["saldo"].sum())])
        for j in range(2, 8):
            ws.cell(ws.max_row, j).fill, ws.cell(ws.max_row, j).font = gris, Font(bold=True)
            if j >= 5:
                ws.cell(ws.max_row, j).number_format = fmt
    for letra, ancho in zip("ABCDEFG", (22, 42, 10, 13, 15, 15, 16)):
        ws.column_dimensions[letra].width = ancho
    sind = act[act["_bloque"] == "Sindicatos"]
    for nombre_s in sorted(sind["_grupo"].unique()):
        x = _orden_periodo(sind[sind["_grupo"] == nombre_s])
        cols = [(c, t) for c, t in _SIND_COLS if x[c].abs().sum() > 0.004]
        hs = wb.create_sheet(nombre_s[:31])
        hs.append(["SINDICATO", "PERIODO"] + [t for _, t in cols] + ["TOTAL"])
        for c in hs[1]:
            c.fill, c.font = gris, Font(bold=True)
        for k, (_, f) in enumerate(x.iterrows()):
            hs.append([nombre_s if k == 0 else "", periodo_planilla(f["periodo"], SINDICATO)]
                      + [f[c] or None for c, _ in cols] + [f["saldo"]])
        hs.append(["TOTAL", ""] + [float(x[c].sum()) for c, _ in cols] + [float(x["saldo"].sum())])
        for c in hs[hs.max_row]:
            c.fill, c.font = gris, Font(bold=True)
        for fila in hs.iter_rows(min_row=2):
            for c in fila[2:]:
                c.number_format = fmt
        for letra, ancho in zip("ABCDE", (14, 10, 20, 24, 16)):
            hs.column_dimensions[letra].width = ancho
    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------
PANTALLAS = ["Planilla", "Cargar", "Editar", "Importar"]
ESTADOS = ["Pendiente", "En plan de pagos", "Judicializada", "Discutida", "Pagada"]


def _anio(periodo: str) -> str:
    return periodo[:4] if re.fullmatch(r"\d{4}-\d{2}", str(periodo or "")) else "Sin período"


def filtrar(d: pd.DataFrame, *, bloque: str = "Todo", organismos: list[str] | None = None,
            anios: list[str] | None = None, marca: str = "Todas", estado: str = "Pendientes",
            solo_vencidas: bool = False, buscar: str = "", hoy: pd.Timestamp | None = None) -> pd.DataFrame:
    """Filtros de Planilla y Editar."""
    x = d
    if x.empty:
        return x
    if bloque != "Todo":
        x = x[x["_bloque"] == bloque]
    if organismos:
        x = x[x["_grupo"].isin(organismos)]
    if anios:
        x = x[x["periodo"].map(_anio).isin(anios)]
    if marca == "Sin marca":
        x = x[x["marca"] == ""]
    elif marca != "Todas":
        x = x[x["marca"] == marca]
    pagada = (x["saldo"] <= 0.004) & (x["deuda_total"].abs() > 0.004)
    if estado == "Pendientes":
        x = x[~pagada]
    elif estado == "Pagadas":
        x = x[pagada]
    if solo_vencidas:
        x = x[vencido(x, hoy)]
    if buscar.strip():
        b = _sin_acentos(buscar)
        x = x[[b in _sin_acentos(f"{f['organismo']} {f['impuesto']} {f['observaciones']} {f['periodo']} {f['estado']} "
                                 f"{periodo_planilla(f['periodo'], f['seccion'])}") for _, f in x.iterrows()]]
    return x


def _widgets_filtro(d: pd.DataFrame, k: str, hoy: pd.Timestamp, *, con_estado: bool = True) -> tuple[pd.DataFrame, str, bool]:
    """Dibuja los filtros y devuelve (filas filtradas, bloque elegido, hay filtros activos)."""
    movil = es_movil()
    activos = sum(bool(st.session_state.get(f"{k}{x}")) for x in ("org", "anio", "vencidas", "buscar"))
    caja = (st.expander("Filtros" + (f" · {activos} activos" if activos else ""), icon=":material/filter_list:")
            if movil else st.container(border=True))
    with caja:
        bloque = st.pills("Bloque", ["Todo"] + BLOQUES, default="Todo", key=f"{k}bloque") or "Todo"
        base = d if bloque == "Todo" else d[d["_bloque"] == bloque]
        f1, f2, f3 = st.columns([1.4, 1, 1])
        organismos = f1.multiselect("Organismo / sindicato", sorted(base["_grupo"].unique()) if not base.empty else [],
                                    placeholder="Todos", key=f"{k}org")
        anios = f2.multiselect("Año del período", sorted(base["periodo"].map(_anio).unique(), reverse=True)
                               if not base.empty else [], placeholder="Todos", key=f"{k}anio")
        marca = f3.selectbox("Marca", ["Todas", "Amarillo", "Salmón", "Sin marca"], key=f"{k}marca_f")
        g1, g2, g3 = st.columns([1.2, 1, 1.6], vertical_alignment="bottom")
        estado = (g1.segmented_control("Estado", ["Pendientes", "Pagadas", "Todas"], default="Pendientes",
                                       key=f"{k}estado_f") or "Pendientes") if con_estado else "Todas"
        vencidas = g2.toggle("Sólo vencidas", key=f"{k}vencidas")
        buscar = g3.text_input("Buscar", key=f"{k}buscar", placeholder="Impuesto, período (202606, feb-25), nota…")
    x = filtrar(d, bloque=bloque, organismos=organismos, anios=anios, marca=marca, estado=estado,
                solo_vencidas=vencidas, buscar=buscar, hoy=hoy)
    hay = bool(organismos or anios or marca != "Todas" or estado != "Pendientes" or vencidas or buscar.strip())
    return x, bloque, hay


def render_deudas(
    df_original: pd.DataFrame | None,
    *,
    tabla: str,
    guardar: Callable[[pd.DataFrame], Any],
    leer_excel: Callable[..., pd.DataFrame] | None = None,
    listar_hojas: Callable[[Any], list[str]] | None = None,
    selector: Callable[..., str] | None = None,
    mostrar_error: Callable[[str, Exception], None] | None = None,
) -> None:
    hoy = pd.Timestamp.today().normalize()
    d = normalizar(df_original)
    k = f"di2_{tabla}_"
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

    nombre, cuit = EMPRESA.get(tabla, ("", ""))
    st.markdown(_CSS + f'<div class="di-emp"><b>DEUDAS {html.escape(nombre)}</b>'
                f'<span>{"CUIT " + html.escape(cuit) if cuit else ""}</span></div>', unsafe_allow_html=True)

    t = totales(d, hoy)
    tarjetas_kpi([
        ("Deuda total", pesos(t["total"], 0), f"{pesos(t['vencido'], 0)} vencido" if t["vencido"] > 0.5 else "Nada vencido",
         "#8C2853"),
        ("Rentas", pesos(t["Rentas"], 0), f"{t['Rentas_n']} obligaciones", "#2F6F8F"),
        ("ARCA (AFIP)", pesos(t["ARCA (AFIP)"], 0), f"{t['ARCA (AFIP)_n']} obligaciones", "#B14E5E"),
        ("Sindicatos", pesos(t["Sindicatos"], 0), f"{t['Sindicatos_n']} períodos", "#C58A32"),
    ])

    seccion = (selector("Sección", PANTALLAS, key=f"{k}seccion") if selector else
               st.segmented_control("Sección", PANTALLAS, default=PANTALLAS[0], key=f"{k}seccion",
                                    label_visibility="collapsed") or PANTALLAS[0])

    # ---- Planilla ----------------------------------------------------------
    if seccion == "Planilla":
        if d.empty:
            st.info("Todavía no hay deudas cargadas. Cargalas en «Cargar» o traé la planilla en «Importar».")
            return
        vista, ver, hay_filtros = _widgets_filtro(d, f"{k}pl_", hoy)
        # Filas en 0 (por ejemplo IVA sin deuda) se ven como en la planilla.
        if not hay_filtros:
            ceros = d[(d["deuda_total"].abs() <= 0.004) & ((d["_bloque"] == ver) if ver != "Todo" else True)]
            vista = pd.concat([vista, ceros[~ceros["id_registro"].isin(vista["id_registro"])]]).sort_values("_orden")
        if hay_filtros:
            tv = vista[vista["saldo"] > 0.004]
            st.caption(f"{len(vista)} deudas con estos filtros · saldo {pesos(tv['saldo'].sum())} · "
                       f"capital {pesos(tv['capital'].sum())} · intereses {pesos(tv['intereses'].sum())}")
        if (d["marca"] != "").any():
            st.markdown('<div class="di-ley"><span><i style="background:#FFF3A6"></i>Amarillo</span>'
                        '<span><i style="background:#FBE1D3;border-color:#EBC3AE"></i>Salmón</span>'
                        '<span>Marcas como en la planilla. Fecha en rojo: vencida.</span></div>', unsafe_allow_html=True)
        if vista.empty:
            st.info("No hay deudas con esos filtros.")
            return
        st.markdown(html_planilla(vista, hoy, None if ver == "Todo" else [ver]), unsafe_allow_html=True)
        st.download_button("Descargar Excel (como la planilla)", data=excel(d, tabla), file_name=f"{tabla}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key=f"{k}xls", icon=":material/download:")
        return

    # ---- Cargar ------------------------------------------------------------
    if seccion == "Cargar":
        tipo = st.segmented_control("Qué cargar", ["F931 de un período", "Rentas", "Ganancias / IVA / otro", "Sindicato"],
                                    default="F931 de un período", key=f"{k}tipo") or "F931 de un período"
        existentes = sorted(d.loc[d["seccion"] == SINDICATO, "_grupo"].unique()) if not d.empty else []
        secc = RENTAS
        if tipo == "Ganancias / IVA / otro":
            secc = st.segmented_control("Sección", [ANTICIPOS, GANANCIAS, IVA, OTROS], default=ANTICIPOS,
                                        key=f"{k}secc") or ANTICIPOS
        # Todo dentro de un formulario: se guarda en un solo paso al tocar «Guardar».
        with st.form(f"{k}alta_{tipo}_{rev}", border=False):
            c1, c2, c3 = st.columns(3)
            with c1:
                per_txt = st.text_input("Período", key=f"{k}per_{tipo}_{rev}",
                                        placeholder="202611 o nov-26",
                                        value=str(pd.Period(hoy, freq="M") - 1).replace("-", ""))
            with c2:
                vto = st.date_input("Fecha de vencimiento", value=None, format="DD/MM/YYYY", key=f"{k}vto_{tipo}_{rev}")
            with c3:
                marca = st.selectbox("Marca", MARCAS, format_func=lambda m: m or "Sin marca", key=f"{k}marca_alta_{tipo}_{rev}")
            if tipo == "F931 de un período":
                st.caption("Completá los importes de cada código; los que queden en 0 no se cargan.")
                base = pd.DataFrame({"impuesto": CODIGOS_F931, "capital": 0.0, "intereses": 0.0})
                tabla_f = st.data_editor(base, hide_index=True, use_container_width=True, num_rows="dynamic",
                                         key=f"{k}f931_{rev}", column_config={
                                             "impuesto": st.column_config.TextColumn("Impuesto", width="large"),
                                             "capital": st.column_config.NumberColumn("Capital", min_value=0.0, format="localized"),
                                             "intereses": st.column_config.NumberColumn("Intereses", min_value=0.0, format="localized")})
            elif tipo == "Sindicato":
                s1, s2 = st.columns(2)
                with s1:
                    sind = st.selectbox("Sindicato", existentes + ["Otro…"], key=f"{k}sind_{rev}")
                with s2:
                    otro = st.text_input("Si es otro, nombre", key=f"{k}sind_otro_{rev}", placeholder="ATSA, FATSA…")
                a, b, c = st.columns(3)
                cs = a.number_input("Cuota sindical", min_value=0.0, step=100.0, format="%.2f", key=f"{k}cs_{rev}")
                so = b.number_input("Solidaridad / Extra", min_value=0.0, step=100.0, format="%.2f", key=f"{k}so_{rev}")
                cc = c.number_input("Contribución convencional", min_value=0.0, step=100.0, format="%.2f", key=f"{k}cc_{rev}")
            else:
                impuesto = st.text_input("Impuesto", value=IMPUESTO_POR_DEFECTO.get(secc, ""), key=f"{k}imp_{secc}_{rev}",
                                         placeholder="Si no es el indicado, escribilo")
                a, b = st.columns(2)
                cap = a.number_input("Capital", min_value=0.0, step=1000.0, format="%.2f", key=f"{k}cap_{tipo}_{rev}")
                inte = b.number_input("Intereses", min_value=0.0, step=1000.0, format="%.2f", key=f"{k}int_{tipo}_{rev}")
            obs = st.text_input("Observaciones (opcional)", key=f"{k}obs_{tipo}_{rev}")
            enviar = st.form_submit_button("Guardar", type="primary", icon=":material/save:", use_container_width=es_movil())
        if not enviar:
            return
        periodo = leer_periodo(per_txt)
        nuevas: list[dict[str, Any]] = []
        if tipo == "F931 de un período":
            for _, f in tabla_f.iterrows():
                cap_f, int_f = float(numero(f["capital"]) or 0), float(numero(f["intereses"]) or 0)
                if cap_f + int_f > 0.004 and _texto(f["impuesto"]):
                    nuevas.append({"seccion": F931, "organismo": "F931", "impuesto": _texto(f["impuesto"]),
                                   "capital": cap_f, "intereses": int_f})
        elif tipo == "Sindicato":
            organismo = otro.strip().upper() if (sind == "Otro…" or otro.strip()) else sind
            if cs + so + cc > 0.004 and organismo:
                nuevas.append({"seccion": SINDICATO, "organismo": organismo,
                               "impuesto": " + ".join(t for v, t in ((cs, "Cuota sindical"), (so, "Solidaridad"),
                                                                     (cc, "Contribución convencional")) if v),
                               "cuota_sindical": cs, "cuota_solidaridad": so, "contribucion_convencional": cc})
        else:
            if cap + inte > 0.004 and impuesto.strip():
                organismo = {RENTAS: "RENTAS", ANTICIPOS: "ANTICIPO GANANCIAS"}.get(secc, secc.upper())
                nuevas.append({"seccion": secc, "organismo": organismo, "impuesto": impuesto.strip(),
                               "capital": cap, "intereses": inte})
        if not periodo:
            st.error("No entiendo el período. Escribilo como 202611 o nov-26.")
            return
        if not nuevas:
            st.error("Completá al menos un importe (y el nombre del sindicato o del impuesto).")
            return
        for n in nuevas:
            n.update({"id_registro": _id(), "periodo": periodo, "marca": marca, "observaciones": obs.strip(),
                      "fecha_vencimiento": vto.isoformat() if vto else ""})
        _guardar(pd.concat([d, normalizar(pd.DataFrame(nuevas))], ignore_index=True), f"{len(nuevas)} deuda(s) cargadas")
        return

    # ---- Editar ------------------------------------------------------------
    if seccion == "Editar":
        if d.empty:
            st.info("No hay deudas para editar.")
            return
        x, bloque, _ = _widgets_filtro(d, f"{k}ed_", hoy)
        st.caption(f"{len(x)} deudas en la tabla. Todas las celdas se pueden cambiar; la deuda total se recalcula "
                   "sola si cambiás capital, intereses o importes sindicales (o la escribís a mano). Para registrar un "
                   "pago completá «Pagado» o poné el estado «Pagada». Para borrar marcá 🗑️; para agregar usá la fila "
                   "vacía del final. Se guarda al tocar «Guardar cambios».")
        tabla_e = tabla_edicion(x)
        cfg = {
            "borrar": st.column_config.CheckboxColumn("🗑️", width="small", default=False),
            "seccion": st.column_config.SelectboxColumn("Sección", options=SECCIONES, width="medium",
                                                        default=SINDICATO if bloque == "Sindicatos" else
                                                        (RENTAS if bloque == "Rentas" else F931)),
            "organismo": st.column_config.TextColumn("Organismo / sindicato"),
            "impuesto": st.column_config.TextColumn("Impuesto / concepto", width="large"),
            "periodo": st.column_config.TextColumn("Período", width="small", help="202606, feb-25, 2026-06…"),
            "_vto": st.column_config.DateColumn("Fecha de vto", format="DD/MM/YYYY"),
            "capital": st.column_config.NumberColumn("Capital", format="localized", min_value=0.0),
            "intereses": st.column_config.NumberColumn("Intereses", format="localized", min_value=0.0),
            "cuota_sindical": st.column_config.NumberColumn("Cuota sindical", format="localized", min_value=0.0),
            "cuota_solidaridad": st.column_config.NumberColumn("Solidaridad / Extra", format="localized", min_value=0.0),
            "contribucion_convencional": st.column_config.NumberColumn("Contrib. convencional", format="localized", min_value=0.0),
            "deuda_total": st.column_config.NumberColumn("Deuda total", format="localized"),
            "importe_pagado": st.column_config.NumberColumn("Pagado", format="localized", min_value=0.0),
            "estado": st.column_config.SelectboxColumn("Estado", options=ESTADOS),
            "marca": st.column_config.SelectboxColumn("Marca", options=MARCAS),
            "plan_pago": st.column_config.TextColumn("Plan de pago"),
            "observaciones": st.column_config.TextColumn("Observaciones", width="medium"),
        }
        with st.form(f"{k}form_ed_{rev}", border=False):
            editada = st.data_editor(tabla_e, hide_index=True, use_container_width=True, num_rows="dynamic",
                                     height=min(110 + 36 * len(tabla_e), 680), column_config=cfg,
                                     key=f"{k}editor_{rev}")
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
    if seccion == "Importar":
        if tabla == "deudas_impositivas_vmr":
            ya = (not d.empty) and (d["organismo"].str.upper().isin({"ATSA", "FATSA"}).any())
            st.markdown("**Planilla de deudas de VMR (noviembre 2026)**")
            st.caption("Rentas (3), F931 por período, anticipos de Ganancias, IVA, ATSA y FATSA, con sus marcas de "
                       "color. Totales: Rentas $1.402.128,75 · ARCA $8.908.755,97 · ATSA $2.524.434,38 · "
                       "FATSA $3.641.181,51 (52 períodos). En la planilla, el TOTAL de FATSA ($3.594.464,17) no "
                       "suma feb-21 a jul-21.")
            reemplazar = st.toggle("Reemplazar lo que hay cargado", value=not d.empty, key=f"{k}seed_rep",
                                   help="Si lo apagás, se agrega a lo que ya está (puede duplicar).") if not d.empty else True
            if st.button("Cargar la planilla de VMR", key=f"{k}seed", icon=":material/playlist_add:",
                         type="primary" if d.empty else "secondary"):
                base = pd.DataFrame(columns=d.columns) if reemplazar else d
                _guardar(pd.concat([base, planilla_vmr()], ignore_index=True), "Planilla de VMR cargada")
            if ya:
                st.caption("Ya hay deudas de ATSA/FATSA cargadas.")
            st.divider()
        else:
            nombre_corto = "VM" if tabla == "deudas_impositivas_vm" else nombre
            st.markdown(f"**Planilla de deudas de {nombre_corto}**")
            st.caption(f"Para sincronizarla: bajá la planilla de {nombre_corto} como Excel (en Google Sheets: Archivo → "
                       "Descargar → Microsoft Excel) y subila acá abajo. Te muestro cómo queda —Rentas, ARCA y "
                       "sindicatos, con sus totales— y se guarda con un botón. Cada vez que la quieras actualizar, "
                       "repetí lo mismo con «Reemplazar lo que hay cargado» prendido.")
            st.divider()
        st.markdown("**Importar desde Excel**")
        st.caption("Planilla con columnas IMPUESTO · PERIODO · FECHA DE VTO · CAPITAL · INTERESES · DEUDA TOTAL, y las "
                   "de sindicatos (CUOTA SINDICAL, SOLIDARIDAD, CONTRIBUCIÓN CONVENCIONAL). Lee todas las hojas.")
        archivo = st.file_uploader("Archivo", type=["xlsx", "xls", "csv"], key=f"{k}archivo", label_visibility="collapsed")
        if archivo is None or leer_excel is None:
            return
        hojas: list[str] = []
        if listar_hojas is not None:
            try:
                hojas = list(listar_hojas(archivo))
            except Exception as exc:
                _error("No pude abrir el archivo.", exc)
                return
        elegidas: list[str] | None = None
        if len(hojas) > 1:
            todas = f"Todas las hojas ({len(hojas)})"
            hoja = st.selectbox("¿Qué hoja (página) del archivo importo?", [*hojas, todas], index=0,
                                key=f"{k}imp_hoja_{getattr(archivo, 'name', '')}",
                                help="Elegí la pestaña de la planilla que querés traer.")
            elegidas = None if hoja == todas else [hoja]
        elif hojas:
            st.caption(f"Hoja: {hojas[0]}")
        try:
            importado = normalizar(leer_excel(archivo, hojas=elegidas) if elegidas else leer_excel(archivo))
        except Exception as exc:
            _error("No pude leer el archivo.", exc)
            return
        if importado.empty:
            st.warning("No encontré deudas en el archivo.")
            return
        ti = totales(importado, hoy)
        st.caption(f"{len(importado)} deudas · Rentas {pesos(ti['Rentas'])} · ARCA {pesos(ti['ARCA (AFIP)'])} · "
                   f"Sindicatos {pesos(ti['Sindicatos'])}")
        st.markdown(html_planilla(importado, hoy), unsafe_allow_html=True)
        reemplazar = st.toggle("Reemplazar lo que hay cargado", value=True, key=f"{k}imp_rep")
        if st.button(f"Importar {len(importado)} deudas", type="primary", key=f"{k}imp_ok", icon=":material/upload_file:"):
            base = pd.DataFrame(columns=d.columns) if reemplazar else d
            _guardar(pd.concat([base, importado], ignore_index=True), "Planilla importada")
        return
