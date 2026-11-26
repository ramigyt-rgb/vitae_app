"""Facturación VMR / VM: plan para ponerse al día, avance diario y cierre mensual.

Plan para ponerse al día
    Todo lo pendiente (la bandeja del control) ordenado por dónde conviene empezar: primero cargar las
    cirugías que faltan (si no están cargadas no se facturan), después facturar lo que está por perder
    el plazo de 60 días, lo que ya lo pasó y el resto; después cobrar lo vencido, los débitos y, al
    final, completar datos. Cada día se toma una «tanda» de esa lista.

Avance diario (pestaña ``facturacion_avance``)
    Una fila por día y empresa con cuánto quedaba pendiente, para ver cómo baja.

Cierre mensual (pestaña ``facturacion_cierres``)
    Por mes de la prestación: un control con cada punto en verde o rojo. Se puede cerrar cuando todo lo
    que depende de nosotros está bien (cargado, facturado, datos completos, sin repetidas, con
    vencimiento y débitos anotados); lo que falta cobrar se informa. Al cerrar se guardan los números
    del mes y una «huella»; si después cambia algo de ese mes, avisa. El PDF sale de acá.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any

import pandas as pd

import vitae.modulos.control_facturacion as cf
import vitae.modulos.facturacion as fx
from vitae.modulos.pagos_pendientes import _texto, pesos

TABLA_CIERRES = "facturacion_cierres"
TABLA_AVANCE = "facturacion_avance"
COLS_CIERRES = ["empresa", "mes", "cerrado_el", "usuario", "prestaciones", "facturado", "cobrado", "por_cobrar",
                "debitos", "huella", "observaciones"]
COLS_AVANCE = ["fecha", "empresa", "pendientes", "cargar", "facturar", "plazo_vencido", "cobrar", "debitos",
               "datos", "importe_sin_facturar", "importe_vencido"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]

# Orden para ponerse al día: (tarea de la bandeja, etapa, título de la etapa, paso donde se resuelve, por qué)
ETAPAS = [
    ("Cargar en facturación", 1, "Cargar las cirugías realizadas que faltan", "cargar",
     "Si no están cargadas, no se facturan."),
    ("Facturar (vence el plazo)", 2, "Facturar lo que está por perder el plazo", "facturar",
     "Todavía estás a tiempo: quedan 15 días o menos de los 60."),
    ("Facturar (plazo vencido)", 3, "Facturar lo que ya pasó los 60 días", "facturar",
     "Presentalas igual cuanto antes, empezando por las que vencieron hace menos."),
    ("Facturar", 4, "Facturar el resto", "facturar", "Con plazo, pero mejor no dejarlas para el final."),
    ("Reclamar factura vencida", 5, "Reclamar las facturas vencidas", "cobrar", "De la más grande a la más chica."),
    ("Factura por vencer", 6, "Seguir las facturas que vencen esta semana", "cobrar", "Para que no se venzan."),
    ("Reclamar débito", 7, "Reclamar los débitos", "debitos", "Lo que la obra social pagó de menos."),
    ("Anotar el débito", 8, "Anotar los débitos de cobros parciales", "debitos", "Cuánto debitaron y por qué."),
    ("Cargar vencimiento", 9, "Cargar los vencimientos que faltan", "cobrar",
     "Sin vencimiento no se puede seguir la cobranza."),
    ("Completar datos", 10, "Completar datos", "cargar", "Obra social, procedimiento o importe."),
    ("Factura repetida", 11, "Revisar números de factura repetidos", "facturar", "Error de carga o duplicada."),
]
_ETAPA = {t: (n, titulo, paso, por_que) for t, n, titulo, paso, por_que in ETAPAS}


# ---------------------------------------------------------------------------
# Plan para ponerse al día
# ---------------------------------------------------------------------------
def plan(b: pd.DataFrame) -> pd.DataFrame:
    """Las tareas de la bandeja en el orden para ponerse al día (columna «Etapa» 1, 2, 3…)."""
    if b is None or b.empty:
        return pd.DataFrame(columns=list(getattr(b, "columns", [])) + ["Etapa", "Qué hacer", "Paso"])
    x = b.copy()
    x["Etapa"] = x["Tarea"].map(lambda t: _ETAPA.get(t, (99,))[0])
    x["Qué hacer"] = x["Tarea"].map(lambda t: _ETAPA.get(t, (0, t))[1])
    x["Paso"] = x["Tarea"].map(lambda t: _ETAPA.get(t, (0, "", "hoy"))[2])
    return x.sort_values(["Etapa", "Importe"], ascending=[True, False], kind="stable").reset_index(drop=True)


def etapas(p: pd.DataFrame) -> pd.DataFrame:
    """Cuántas tareas e importe por etapa, en orden."""
    if p.empty:
        return pd.DataFrame(columns=["Etapa", "Qué hacer", "Tareas", "Importe", "Paso", "Por qué"])
    g = p.groupby(["Etapa", "Qué hacer", "Paso"]).agg(Tareas=("Tarea", "size"), Importe=("Importe", "sum")).reset_index()
    g["Por qué"] = g["Etapa"].map({n: pq for _, n, _, _, pq in ETAPAS})
    return g.sort_values("Etapa").reset_index(drop=True)


def dias_para_terminar(pendientes: int, por_dia: int) -> int:
    return 0 if pendientes <= 0 else -(-pendientes // max(int(por_dia), 1))


# ---------------------------------------------------------------------------
# Avance diario
# ---------------------------------------------------------------------------
def foto(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cuánto queda pendiente hoy (una fila de ``facturacion_avance``)."""
    c, b = ctx["c"], ctx["b"]
    p = plan(b)
    cuenta = p["Paso"].value_counts() if len(p) else pd.Series(dtype=int)
    return {
        "fecha": pd.Timestamp(ctx["hoy"]).strftime("%Y-%m-%d"), "empresa": ctx["empresa"], "pendientes": int(len(p)),
        "cargar": int(cuenta.get("cargar", 0)), "facturar": int(cuenta.get("facturar", 0)),
        "plazo_vencido": int(len(c["sin_facturar_vencido"])), "cobrar": int(cuenta.get("cobrar", 0)),
        "debitos": int(cuenta.get("debitos", 0)),
        "datos": int((p["Tarea"].isin(["Completar datos", "Factura repetida"])).sum()) if len(p) else 0,
        "importe_sin_facturar": round(float(c["sin_facturar"]["_monto"].sum()) if len(c["sin_facturar"]) else 0.0, 2),
        "importe_vencido": round(float(c["vencidas"]["_pendiente"].sum()) if len(c["vencidas"]) else 0.0, 2),
    }


def normalizar_avance(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=COLS_AVANCE)
    out = df.copy()
    for c in COLS_AVANCE:
        if c not in out.columns:
            out[c] = ""
    out = out[COLS_AVANCE].copy()
    for c in COLS_AVANCE[2:]:
        out[c] = pd.to_numeric(out[c].astype(str).str.replace(",", ".", regex=False), errors="coerce").fillna(0)
    out["fecha"] = out["fecha"].astype(str).str[:10]
    out["empresa"] = out["empresa"].astype(str).str.upper()
    return out[out["fecha"].str.match(r"\d{4}-\d{2}-\d{2}")].reset_index(drop=True)


def registrar_avance(avance: pd.DataFrame | None, f: dict[str, Any]) -> tuple[pd.DataFrame, bool]:
    """Pone (o actualiza) la fila de hoy. Devuelve (tabla, cambió)."""
    a = normalizar_avance(avance)
    m = (a["fecha"] == f["fecha"]) & (a["empresa"] == f["empresa"])
    if m.any():
        fila = a[m].iloc[0]
        if all(float(fila[c]) == float(f[c]) for c in COLS_AVANCE[2:]):
            return a, False
        a = a[~m]
    a = pd.concat([a, pd.DataFrame([f])[COLS_AVANCE]], ignore_index=True)
    return a.sort_values(["empresa", "fecha"]).reset_index(drop=True), True


def avance_de(avance: pd.DataFrame | None, empresa: str) -> pd.DataFrame:
    a = normalizar_avance(avance)
    return a[a["empresa"] == str(empresa).upper()].sort_values("fecha").reset_index(drop=True)


def comparacion(av: pd.DataFrame, hoy: date) -> dict[str, Any]:
    """Pendientes hoy contra ayer (el último día anterior registrado) y contra hace una semana."""
    if av.empty:
        return {}
    h = pd.Timestamp(hoy).strftime("%Y-%m-%d")
    hoy_f = av[av["fecha"] == h]
    antes = av[av["fecha"] < h]
    out: dict[str, Any] = {"hoy": int(hoy_f["pendientes"].iloc[-1]) if len(hoy_f) else None}
    if len(antes):
        out["anterior"] = int(antes["pendientes"].iloc[-1])
        out["anterior_fecha"] = antes["fecha"].iloc[-1]
    semana = av[av["fecha"] <= (pd.Timestamp(hoy) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")]
    if len(semana):
        out["semana"] = int(semana["pendientes"].iloc[-1])
    if len(av):
        out["inicio"] = int(av["pendientes"].iloc[0])
        out["inicio_fecha"] = av["fecha"].iloc[0]
    return out


# ---------------------------------------------------------------------------
# Cierre mensual
# ---------------------------------------------------------------------------
def mes_txt(mes: str) -> str:
    return f"{MESES[int(mes[5:7]) - 1]} {mes[:4]}" if mes and len(mes) >= 7 else ""


def _mes_de_fecha(s: pd.Series) -> pd.Series:
    return s.map(lambda f: f"{f.year:04d}-{f.month:02d}" if pd.notna(f) else "")


def filas_mes(d: pd.DataFrame, mes: str) -> pd.DataFrame:
    """Prestaciones del mes (por fecha de prestación), sin las anuladas."""
    if d.empty:
        return d
    a = d[~d["_cancelado"]]
    return a[_mes_de_fecha(a["_prestacion"]) == mes]


def meses_con_datos(d: pd.DataFrame) -> list[str]:
    if d.empty:
        return []
    return sorted({m for m in _mes_de_fecha(d.loc[~d["_cancelado"], "_prestacion"]) if m})


def numeros(d: pd.DataFrame, mes: str | None = None, anio: int | None = None) -> dict[str, Any]:
    """Los números de un mes (o de un año entero)."""
    if mes:
        g = filas_mes(d, mes)
    else:
        a = d[~d["_cancelado"]] if len(d) else d
        g = a[_mes_de_fecha(a["_prestacion"]).str[:4] == str(anio)] if len(a) else a
    if g.empty:
        return {"prestaciones": 0, "facturadas": 0, "sin_facturar": 0, "facturado": 0.0, "cobrado": 0.0,
                "por_cobrar": 0.0, "vencido": 0.0, "debitos": 0.0, "debitos_abiertos": 0.0, "recuperado": 0.0,
                "aceptado": 0.0, "por_obra_social": pd.DataFrame(), "por_medico": pd.DataFrame(),
                "por_procedimiento": pd.DataFrame()}
    est = g["_debito_estado"].map(_texto).str.lower()

    def tabla(col: str, titulo: str) -> pd.DataFrame:
        t = g.groupby(col).agg(Prestaciones=("_monto", "size"), Facturado=("_monto", "sum"),
                               Cobrado=("_cobrado", "sum"), **{"Por cobrar": ("_pendiente", "sum")}, Débitos=("_debito", "sum"))
        t = t.sort_values("Facturado", ascending=False).reset_index().rename(columns={col: titulo})
        return t

    fact = g[g["_facturada"]]
    return {
        "prestaciones": int(len(g)), "facturadas": int(len(fact)), "sin_facturar": int((~g["_facturada"] & ~g["_sin_cargo"]).sum()),
        "facturado": float(g["_monto"].sum()), "cobrado": float(g["_cobrado"].sum()),
        "por_cobrar": float(fact["_pendiente"].sum()), "vencido": float(fact.loc[fact["_vencido"], "_pendiente"].sum()),
        "debitos": float(g["_debito"].sum()), "debitos_abiertos": float(g.loc[g["_debito_abierto"], "_debito"].sum()),
        "recuperado": float(g.loc[est.str.contains("recuper", na=False), "_debito"].sum()),
        "aceptado": float(g.loc[est.str.contains("acept", na=False), "_debito"].sum()),
        "por_obra_social": tabla("_obra_social", "Obra social"), "por_medico": tabla("_medico", "Médico"),
        "por_procedimiento": tabla("_procedimiento", "Procedimiento"),
    }


def _en_mes(x: pd.DataFrame, mes: str) -> pd.DataFrame:
    if x is None or x.empty or "_prestacion" not in x.columns:
        return pd.DataFrame()
    return x[_mes_de_fecha(x["_prestacion"]) == mes]


def checklist(ctx: dict[str, Any], mes: str) -> list[dict[str, Any]]:
    """Cada punto del cierre: ok o no, cuántos casos, si traba el cierre y en qué paso se arregla."""
    c = ctx["c"]
    puntos = [
        ("Todo facturado", len(_en_mes(c["sin_facturar"], mes)), True, "facturar", "Prestaciones sin N° de factura."),
        ("Datos completos", len(_en_mes(c["incompletos"], mes)), True, "cargar",
         "Falta obra social, procedimiento o importe."),
        ("Sin números de factura repetidos", len(_en_mes(c["repetidas"], mes)), True, "facturar",
         "Un mismo N° de factura en más de una fila."),
        ("Facturas con vencimiento", len(_en_mes(c["sin_vencimiento"], mes)), True, "cobrar",
         "Sin vencimiento no se puede seguir la cobranza."),
        ("Débitos anotados en los cobros parciales", len(_en_mes(c["parcial_sin_debito"], mes)), True, "debitos",
         "Cobro parcial sin cuánto debitaron y por qué."),
        ("Facturas vencidas sin cobrar", len(_en_mes(c["vencidas"], mes)), False, "cobrar",
         "No traba el cierre: se sigue reclamando."),
        ("Débitos sin resolver", len(_en_mes(c["debitos"], mes)), False, "debitos",
         "No traba el cierre: se sigue reclamando."),
    ]
    return [{"punto": p, "casos": int(n), "ok": n == 0, "traba": traba, "paso": paso, "detalle": det}
            for p, n, traba, paso, det in puntos]


def listo_para_cerrar(lista: list[dict[str, Any]]) -> bool:
    return all(x["ok"] for x in lista if x["traba"])


def huella(d: pd.DataFrame, mes: str) -> str:
    """Resumen de todas las filas del mes: si algo cambia después del cierre, cambia la huella."""
    g = filas_mes(d, mes)
    if g.empty:
        return ""
    partes = sorted(f"{r['_paciente']}|{r['_numero_factura']}|{r['_monto']:.2f}|{r['_cobrado']:.2f}|"
                    f"{r['_debito']:.2f}|{r['_estado_norm']}" for _, r in g.iterrows())
    return hashlib.sha1("\n".join(partes).encode("utf-8")).hexdigest()[:16]


def normalizar_cierres(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=COLS_CIERRES)
    out = df.copy()
    for c in COLS_CIERRES:
        if c not in out.columns:
            out[c] = ""
    out = out[COLS_CIERRES].astype(str).replace({"nan": ""})
    out["empresa"] = out["empresa"].str.upper()
    return out[out["mes"].str.match(r"\d{4}-\d{2}")].reset_index(drop=True)


def cierre_de(cierres: pd.DataFrame | None, empresa: str, mes: str) -> dict[str, Any] | None:
    c = normalizar_cierres(cierres)
    m = c[(c["empresa"] == str(empresa).upper()) & (c["mes"] == mes)]
    return m.iloc[-1].to_dict() if len(m) else None


def cerrar(cierres: pd.DataFrame | None, empresa: str, mes: str, nums: dict[str, Any], hue: str, usuario: str,
           observaciones: str = "") -> pd.DataFrame:
    c = normalizar_cierres(cierres)
    c = c[~((c["empresa"] == str(empresa).upper()) & (c["mes"] == mes))]
    fila = {"empresa": str(empresa).upper(), "mes": mes, "cerrado_el": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "usuario": usuario, "prestaciones": str(nums["prestaciones"]), "facturado": f"{nums['facturado']:.2f}",
            "cobrado": f"{nums['cobrado']:.2f}", "por_cobrar": f"{nums['por_cobrar']:.2f}",
            "debitos": f"{nums['debitos']:.2f}", "huella": hue, "observaciones": observaciones}
    return pd.concat([c, pd.DataFrame([fila])], ignore_index=True).sort_values(["empresa", "mes"]).reset_index(drop=True)


def reabrir(cierres: pd.DataFrame | None, empresa: str, mes: str) -> pd.DataFrame:
    c = normalizar_cierres(cierres)
    return c[~((c["empresa"] == str(empresa).upper()) & (c["mes"] == mes))].reset_index(drop=True)


def estado_mes(ctx: dict[str, Any], cierres: pd.DataFrame | None, mes: str) -> dict[str, Any]:
    """Estado del mes para la lista del año."""
    d, empresa = ctx["d"], ctx["empresa"]
    hoy = pd.Timestamp(ctx["hoy"])
    lista = checklist(ctx, mes)
    ci = cierre_de(cierres, empresa, mes)
    actual = f"{hoy.year:04d}-{hoy.month:02d}"
    if ci:
        cambio = ci.get("huella") and ci["huella"] != huella(d, mes)
        return {"estado": "⚠️ Cambió después del cierre" if cambio else "🔒 Cerrado", "cerrado": True, "cambio": bool(cambio),
                "cierre": ci, "lista": lista}
    if mes >= actual:
        return {"estado": "⏳ En curso", "cerrado": False, "cambio": False, "cierre": None, "lista": lista}
    if listo_para_cerrar(lista):
        return {"estado": "✅ Listo para cerrar", "cerrado": False, "cambio": False, "cierre": None, "lista": lista}
    falta = sum(x["casos"] for x in lista if x["traba"] and not x["ok"])
    return {"estado": f"🔴 Falta resolver ({falta})", "cerrado": False, "cambio": False, "cierre": None, "lista": lista}


def anio(ctx: dict[str, Any], cierres: pd.DataFrame | None, anio_: int) -> pd.DataFrame:
    """Los 12 meses del año (hasta el actual): estado y números."""
    hoy = pd.Timestamp(ctx["hoy"])
    hasta = 12 if anio_ < hoy.year else hoy.month
    filas = []
    for m in range(1, hasta + 1):
        mes = f"{anio_:04d}-{m:02d}"
        n = numeros(ctx["d"], mes)
        e = estado_mes(ctx, cierres, mes)
        if n["prestaciones"] == 0 and not e["cerrado"] and mes < f"{hoy.year:04d}-{hoy.month:02d}" and \
                not any(not x["ok"] for x in e["lista"]):
            estado = "— Sin prestaciones"
        else:
            estado = e["estado"]
        filas.append({"_mes": mes, "Mes": mes_txt(mes).capitalize(), "Estado": estado, "Prestaciones": n["prestaciones"],
                      "Sin facturar": n["sin_facturar"], "Facturado": n["facturado"], "Cobrado": n["cobrado"],
                      "Por cobrar": n["por_cobrar"], "Débitos": n["debitos"]})
    return pd.DataFrame(filas)


def fmt(v: float, dec: int = 0) -> str:
    return pesos(v, dec)
