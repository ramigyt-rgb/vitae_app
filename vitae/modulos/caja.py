"""Caja VM y Caja VMR: la planilla mensual de efectivo.

Se ve como la planilla que se completa a mano: por mes, saldo inicial,
cada movimiento con su fecha, descripción, entrada o salida y de quién es,
los colores de Alquileres y Caja gastos comunes, el TOTAL y el SALDO; debajo,
la caja en dólares.

Trabaja sobre la pestaña existente (``caja_vm`` / ``caja_vmr``) sin
reescribir filas que no se tocan: editar cambia sólo las celdas modificadas
y cargar agrega filas al final. Suma dos columnas si faltan: ``rubro``
(Alquileres, Caja gastos comunes o General) y ``moneda`` (ARS / USD). Los
movimientos en dólares cargados antes con la marca "[USD]" se siguen
reconociendo.

Saldo inicial: si un mes tiene una fila "SALDO INICIAL", ese es su saldo
inicial (como en la planilla) y no se cuenta como entrada; si no, es el saldo
con que cerró el mes anterior.

Cajas internas (Caja VMR): Efectivo (la caja de siempre), Mercado Pago y
Medicación. Cada una tiene sus propios movimientos, saldo inicial y saldo;
el total de la caja es la suma. Una fila sin caja interna es de Efectivo.

- Transferencia entre cajas internas (categoría «Transferencia»): dos filas,
  sale de una y entra en la otra. Mueve saldo entre cajas, pero no es una
  entrada ni una salida del total.
- Frascos (categoría «Frasco»): apartan plata dentro de una caja sin que salga
  (p. ej. «Reserva» en Mercado Pago). Entrada = apartar, salida = devolver al
  disponible. No cambian el saldo de la caja ni el total: sólo cómo se reparte
  (Disponible + frascos). Un gasto hecho «desde un frasco» es un movimiento
  normal con el frasco anotado: baja la caja y el frasco.
"""
from __future__ import annotations

import html
import io
import re
from datetime import date, datetime
from typing import Any, Callable

import pandas as pd
import streamlit as st

from vitae.modulos.pagos_pendientes import MESES_ES, _sin_acentos, _texto, nombre_mes, numero, pesos

try:
    from vitae.core.helpers import leer_fechas_ar
except Exception:  # pragma: no cover
    def leer_fechas_ar(valores):
        return pd.to_datetime(pd.Series(valores), dayfirst=True, errors="coerce")

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


GENERAL = "General"
ALQUILERES = "Alquileres"
GASTOS_COMUNES = "Caja gastos comunes"
RUBROS = [GENERAL, ALQUILERES, GASTOS_COMUNES]
EFECTIVO = "Efectivo"
MERCADO_PAGO = "Mercado Pago"
MEDICACION = "Medicación"
CAJAS_INTERNAS = {"caja_vmr": [EFECTIVO, MERCADO_PAGO, MEDICACION]}
FRASCOS_BASE = {"caja_vmr": {MERCADO_PAGO: ["Reserva"]}}
TRANSFERENCIA = "Transferencia"
FRASCO = "Frasco"
FONDO_RUBRO = {ALQUILERES: "#E4F1D8", GASTOS_COMUNES: "#FBE4D8", GENERAL: "transparent"}
BORDE_RUBRO = {ALQUILERES: "#9CC47A", GASTOS_COMUNES: "#E9A383", GENERAL: "#D9D2D6"}
MONEDAS = ["ARS", "USD"]
SECCIONES = ["Planilla", "Cargar movimiento", "Editar", "Historial", "Importar"]
SIN_FECHA = "Sin fecha"

_TAG = re.compile(r"^\s*\[(USD|ARS)\]\s*", re.I)


def dinero(valor: Any, moneda: str = "ARS") -> str:
    texto = pesos(valor)
    return texto.replace("$", "US$ ", 1) if moneda == "USD" else texto


def _iso(valor: Any) -> str:
    if isinstance(valor, (datetime, pd.Timestamp)):
        return valor.strftime("%Y-%m-%d")
    if isinstance(valor, date):
        return valor.isoformat()
    f = leer_fechas_ar(pd.Series([valor])).iloc[0]
    return "" if pd.isna(f) else f.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Columnas de la pestaña
# ---------------------------------------------------------------------------
_NOMBRES = {
    "fecha": ["fecha", "date", "fecha_movimiento", "dia"],
    "concepto": ["concepto", "descripcion", "detalle"],
    "ingreso": ["ingreso", "entrada", "credito", "haber"],
    "egreso": ["egreso", "salida", "debito", "debe"],
    "moneda": ["moneda", "currency"],
    "rubro": ["rubro"],
    "quien": ["responsable", "quien", "referencia"],
    "observaciones": ["observaciones", "observacion", "notas", "nota"],
    "categoria": ["categoria", "tipo"],
    "medio": ["medio", "medio_de_pago"],
    "cuenta": ["caja_interna", "cuenta", "subcaja"],
    "frasco": ["frasco"],
}
_OPCIONALES = {"cuenta", "frasco"}  # sólo se crean cuando hacen falta (Caja VM no las necesita)
_CREAR = {"fecha": "fecha", "concepto": "concepto", "ingreso": "ingreso", "egreso": "egreso",
          "moneda": "moneda", "rubro": "rubro", "quien": "responsable", "observaciones": "observaciones",
          "categoria": "categoria", "medio": "medio", "cuenta": "caja_interna", "frasco": "frasco"}


def _clave(nombre: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _sin_acentos(nombre)).strip("_")


def columnas(raw: pd.DataFrame) -> dict[str, str | None]:
    mapa = {_clave(c): c for c in raw.columns}
    return {k: next((mapa[n] for n in nombres if n in mapa), None) for k, nombres in _NOMBRES.items()}


def asegurar_columnas(raw: pd.DataFrame, extra: tuple[str, ...] = ()) -> tuple[pd.DataFrame, dict[str, str]]:
    """Agrega las columnas que falten (al final) sin tocar las existentes. Las de cajas internas y
    frascos sólo se agregan si se piden en ``extra``."""
    out = raw.copy().astype(object)
    cols = columnas(out)
    for k, c in cols.items():
        if c is None and (k not in _OPCIONALES or k in extra):
            out[_CREAR[k]] = ""
            cols[k] = _CREAR[k]
    return out, cols  # type: ignore[return-value]


def normalizar_rubro(valor: Any) -> str:
    t = _sin_acentos(valor)
    if not t:
        return ""
    if "alquil" in t:
        return ALQUILERES
    if "gastos comunes" in t or "gasto comun" in t or t in {"gc", "caja gc"}:
        return GASTOS_COMUNES
    if t == "general":
        return GENERAL
    return ""


def cajas_internas(tabla: str) -> list[str]:
    return list(CAJAS_INTERNAS.get(tabla, [EFECTIVO]))


def normalizar_cuenta(valor: Any) -> str:
    t = _sin_acentos(valor).replace(" ", "").replace(".", "")
    if not t or t in {"efectivo", "caja", "cajaefectivo", "general"}:
        return EFECTIVO
    if t in {"mp", "mpago"} or t.startswith("mercado"):
        return MERCADO_PAGO
    if t.startswith("medic"):
        return MEDICACION
    return EFECTIVO


def normalizar_frasco(valor: Any) -> str:
    t = re.sub(r"\s+", " ", _texto(valor).strip())
    t = re.sub(r"^frasco\s+(de\s+)?", "", t, flags=re.I)
    return t[:1].upper() + t[1:] if t else ""


def _rubro_inferido(concepto: str) -> str:
    t = _sin_acentos(concepto)
    if t.startswith("alquiler") or " alquiler" in t:
        return ALQUILERES
    if t.startswith("gastos comunes") or t.startswith("gasto comun"):
        return GASTOS_COMUNES
    return GENERAL


# ---------------------------------------------------------------------------
# Lectura de la caja
# ---------------------------------------------------------------------------
def preparar(raw: pd.DataFrame | None, *, dolar_por_texto: bool = False) -> pd.DataFrame:
    """Una fila por fila de la pestaña (mismo índice), con los valores ya leídos."""
    if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
        return pd.DataFrame(columns=["_fecha", "_mes", "_concepto", "_entrada", "_salida", "_moneda", "_rubro",
                                     "_quien", "_obs", "_medio", "_inicial", "_orden", "_cuenta", "_frasco",
                                     "_transferencia", "_es_frasco"])
    cols = columnas(raw)

    def serie(k: str) -> pd.Series:
        c = cols.get(k)
        return raw[c].map(_texto) if c else pd.Series("", index=raw.index, dtype="object")

    d = pd.DataFrame(index=raw.index)
    fechas = leer_fechas_ar(raw[cols["fecha"]]) if cols["fecha"] else pd.Series(pd.NaT, index=raw.index)
    d["_fecha"] = pd.to_datetime(pd.Series(fechas).values, errors="coerce")
    d["_mes"] = [str(pd.Period(f, freq="M")) if pd.notna(f) else SIN_FECHA for f in d["_fecha"]]
    concepto, obs, moneda_col = serie("concepto"), serie("observaciones"), serie("moneda")

    monedas = []
    for m, c, o in zip(moneda_col, concepto, obs):
        mt = m.upper().replace("U$S", "USD").replace("US$", "USD")
        if "USD" in mt or "DOLAR" in _sin_acentos(m).upper():
            monedas.append("USD")
        elif "ARS" in mt or "PESO" in _sin_acentos(m).upper():
            monedas.append("ARS")
        else:
            texto = f"{c} {o}"
            if re.search(r"\[USD\]", texto, re.I):
                monedas.append("USD")
            elif re.search(r"\[ARS\]", texto, re.I):
                monedas.append("ARS")
            elif re.search(r"\bUSD\b|U\$S|US\$", texto, re.I) or (
                    dolar_por_texto and re.search(r"D[ÓO]LAR", texto, re.I)):
                monedas.append("USD")
            else:
                monedas.append("ARS")
    d["_moneda"] = monedas
    d["_concepto"] = concepto.map(lambda t: _TAG.sub("", t))
    d["_obs"] = obs.map(lambda t: _TAG.sub("", t))

    entrada = pd.Series([numero(v) or 0.0 for v in (raw[cols["ingreso"]] if cols["ingreso"] else [0] * len(raw))],
                        index=raw.index, dtype=float)
    salida = pd.Series([numero(v) or 0.0 for v in (raw[cols["egreso"]] if cols["egreso"] else [0] * len(raw))],
                       index=raw.index, dtype=float)
    neg_e, neg_s = entrada < 0, salida < 0
    salida = salida + entrada.where(neg_e, 0).abs()
    entrada = entrada.where(~neg_e, 0.0)
    entrada = entrada + salida.where(neg_s, 0).abs()
    salida = salida.where(~neg_s, 0.0)
    d["_entrada"], d["_salida"] = entrada.round(2), salida.round(2)

    rub = serie("rubro").map(normalizar_rubro)
    d["_rubro"] = [r or _rubro_inferido(c) for r, c in zip(rub, d["_concepto"])]
    d["_quien"] = serie("quien")
    d["_medio"] = serie("medio")
    cat = serie("categoria").map(_sin_acentos)
    d["_es_frasco"] = cat.str.startswith("frasco").astype(bool)
    d["_transferencia"] = cat.str.startswith("transferencia").astype(bool)
    d["_inicial"] = [bool((_sin_acentos(c).startswith("saldo inicial") or k == "saldo inicial") and not f)
                     for c, k, f in zip(d["_concepto"], cat, d["_es_frasco"])]
    d["_cuenta"] = serie("cuenta").map(normalizar_cuenta)
    d["_frasco"] = serie("frasco").map(normalizar_frasco)
    d["_orden"] = range(len(d))
    return d


def _movs(d: pd.DataFrame) -> pd.DataFrame:
    """Movimientos que cambian el saldo (sin los de frascos, que sólo reparten)."""
    return d[~d["_es_frasco"]] if (not d.empty and "_es_frasco" in d.columns) else d


def meses(d: pd.DataFrame) -> list[str]:
    if d.empty:
        return []
    m = sorted({x for x in d["_mes"] if x != SIN_FECHA}, reverse=True)
    return m + ([SIN_FECHA] if (d["_mes"] == SIN_FECHA).any() else [])


def libro(d: pd.DataFrame, moneda: str = "ARS", hasta: pd.Timestamp | None = None) -> pd.DataFrame:
    """Mes a mes: saldo inicial (cargado o calculado), entradas, salidas y saldo."""
    d = _movs(d)
    x = d[(d["_moneda"] == moneda) & (d["_mes"] != SIN_FECHA)] if not d.empty else d
    if hasta is not None and not x.empty:
        x = x[x["_fecha"] <= hasta]
    filas = []
    anterior = 0.0
    hubo = False
    for mes in sorted(set(x["_mes"])) if not x.empty else []:
        m = x[x["_mes"] == mes]
        ini = m[m["_inicial"]]
        mov = m[~m["_inicial"]]
        cargado = float((ini["_entrada"] - ini["_salida"]).sum()) if not ini.empty else None
        inicial = cargado if cargado is not None else anterior
        entradas, salidas = float(mov["_entrada"].sum()), float(mov["_salida"].sum())
        saldo = round(inicial + entradas - salidas, 2)
        filas.append({
            "mes": mes, "inicial": round(inicial, 2), "inicial_cargado": cargado, "inicial_calculado": round(anterior, 2),
            "diferencia": round(cargado - anterior, 2) if (cargado is not None and hubo) else 0.0,
            "entradas": round(entradas, 2), "salidas": round(salidas, 2), "saldo": saldo, "movimientos": int(len(mov)),
        })
        anterior, hubo = saldo, True
    return pd.DataFrame(filas, columns=["mes", "inicial", "inicial_cargado", "inicial_calculado", "diferencia",
                                        "entradas", "salidas", "saldo", "movimientos"])


def saldo_inicial(d: pd.DataFrame, mes: str, moneda: str = "ARS") -> float:
    lb = libro(d, moneda)
    if lb.empty:
        return 0.0
    if mes in set(lb["mes"]):
        return float(lb.loc[lb["mes"] == mes, "inicial"].iloc[0])
    previos = lb[lb["mes"] < mes]
    return float(previos["saldo"].iloc[-1]) if not previos.empty else 0.0


def resumen_mes(d: pd.DataFrame, mes: str, moneda: str = "ARS") -> dict[str, float]:
    ini = saldo_inicial(d, mes, moneda)
    d = _movs(d)
    m = d[(d["_mes"] == mes) & (d["_moneda"] == moneda) & (~d["_inicial"])] if not d.empty else d
    entradas = float(m["_entrada"].sum()) if not m.empty else 0.0
    salidas = float(m["_salida"].sum()) if not m.empty else 0.0
    return {"inicial": ini, "entradas": entradas, "salidas": salidas, "saldo": round(ini + entradas - salidas, 2),
            "movimientos": int(len(m))}


def saldo_actual(raw: pd.DataFrame | None, moneda: str = "ARS", *, dolar_por_texto: bool = False) -> float:
    """Saldo de la caja hoy (para el Panel general): último mes hasta hoy, más
    los movimientos sin fecha."""
    d = preparar(raw, dolar_por_texto=dolar_por_texto)
    if d.empty:
        return 0.0
    return round(sum(saldo_hoy(de_caja(d, c), moneda) for c in sorted(set(d["_cuenta"]))), 2)


def saldo_hoy(d: pd.DataFrame, moneda: str = "ARS") -> float:
    """Saldo de hoy de UNA caja (interna): último mes hasta hoy, más los movimientos sin fecha."""
    if d.empty:
        return 0.0
    hoy = pd.Timestamp.today().normalize() + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    lb = libro(d, moneda, hasta=hoy)
    base = float(lb["saldo"].iloc[-1]) if not lb.empty else 0.0
    m = _movs(d)
    sf = m[(m["_mes"] == SIN_FECHA) & (m["_moneda"] == moneda) & (~m["_inicial"])]
    return round(base + float(sf["_entrada"].sum() - sf["_salida"].sum()), 2)


# ---------------------------------------------------------------------------
# Cajas internas y frascos
# ---------------------------------------------------------------------------
def de_caja(d: pd.DataFrame, caja: str) -> pd.DataFrame:
    return d[d["_cuenta"] == caja] if not d.empty else d


def saldo_al_cierre(d: pd.DataFrame, mes: str, moneda: str = "ARS") -> float:
    """Saldo al cierre de un mes, sumando cada caja interna por separado."""
    total = 0.0
    for c in (sorted(set(d["_cuenta"])) if not d.empty else []):
        lb = libro(de_caja(d, c), moneda)
        lb = lb[lb["mes"] <= mes]
        total += float(lb["saldo"].iloc[-1]) if not lb.empty else 0.0
    return round(total, 2)


def resumen_caja(d: pd.DataFrame, mes: str, moneda: str, caja: str) -> dict[str, float]:
    """Resumen de una caja interna: entradas y salidas propias y, aparte, las transferencias con otras cajas."""
    x = de_caja(d, caja)
    r = dict(resumen_mes(x, mes, moneda))
    t = _movs(x)
    t = t[(t["_mes"] == mes) & (t["_moneda"] == moneda) & (~t["_inicial"]) & t["_transferencia"]] if not t.empty else t
    te = float(t["_entrada"].sum()) if not t.empty else 0.0
    ts = float(t["_salida"].sum()) if not t.empty else 0.0
    r["transf_entrada"], r["transf_salida"] = round(te, 2), round(ts, 2)
    r["entradas_propias"], r["salidas_propias"] = round(r["entradas"] - te, 2), round(r["salidas"] - ts, 2)
    return r


def resumen_total(d: pd.DataFrame, mes: str, moneda: str, cajas: list[str]) -> dict[str, Any]:
    """Total de la caja: suma de las cajas internas. Las transferencias entre ellas no son entradas ni salidas."""
    por = {c: resumen_caja(d, mes, moneda, c) for c in cajas}
    return {"inicial": round(sum(r["inicial"] for r in por.values()), 2),
            "entradas": round(sum(r["entradas_propias"] for r in por.values()), 2),
            "salidas": round(sum(r["salidas_propias"] for r in por.values()), 2),
            "saldo": round(sum(r["saldo"] for r in por.values()), 2),
            "movimientos": sum(r["movimientos"] for r in por.values()), "por_caja": por}


def frascos(d: pd.DataFrame, caja: str, tabla: str = "", hasta: pd.Timestamp | None = None,
            moneda: str = "ARS") -> dict[str, float]:
    """Saldo de cada frasco de una caja interna (lo apartado menos lo devuelto o gastado desde el frasco)."""
    base = {f: 0.0 for f in FRASCOS_BASE.get(tabla, {}).get(caja, [])}
    x = de_caja(d, caja)
    if x.empty:
        return base
    x = x[(x["_frasco"] != "") & (x["_moneda"] == moneda)]
    if hasta is not None:
        x = x[x["_fecha"].isna() | (x["_fecha"] <= hasta)]
    for f, g in x.groupby("_frasco", sort=False):
        base[f] = round(base.get(f, 0.0) + float((g["_entrada"] - g["_salida"]).sum()), 2)
    return base


def libro_total(d: pd.DataFrame, moneda: str, cajas: list[str]) -> pd.DataFrame:
    """Libro mes a mes del total (suma de cajas internas; sin contar las transferencias entre ellas)."""
    libros = {c: libro(de_caja(d, c), moneda) for c in cajas}
    meses_t = sorted({m for lb in libros.values() for m in lb["mes"]})
    filas = []
    for mes in meses_t:
        fila: dict[str, Any] = {"mes": mes, "inicial": 0.0, "entradas": 0.0, "salidas": 0.0, "saldo": 0.0,
                                "movimientos": 0, "diferencia": 0.0}
        for c in cajas:
            r = resumen_caja(d, mes, moneda, c)
            fila["inicial"] += r["inicial"]
            fila["entradas"] += r["entradas_propias"]
            fila["salidas"] += r["salidas_propias"]
            fila["saldo"] += r["saldo"]
            fila["movimientos"] += r["movimientos"]
            lb = libros[c]
            dif = lb.loc[lb["mes"] == mes, "diferencia"]
            fila["diferencia"] += float(dif.iloc[0]) if not dif.empty else 0.0
        filas.append({k: (round(v, 2) if isinstance(v, float) else v) for k, v in fila.items()})
    return pd.DataFrame(filas, columns=["mes", "inicial", "entradas", "salidas", "saldo", "movimientos", "diferencia"])


def por_rubro(d: pd.DataFrame, mes: str, moneda: str = "ARS") -> dict[str, dict[str, float]]:
    d = _movs(d)
    m = d[(d["_mes"] == mes) & (d["_moneda"] == moneda) & (~d["_inicial"])] if not d.empty else d
    out = {}
    for r in RUBROS:
        x = m[m["_rubro"] == r] if not m.empty else m
        out[r] = {"entradas": float(x["_entrada"].sum()) if not x.empty else 0.0,
                  "salidas": float(x["_salida"].sum()) if not x.empty else 0.0, "n": int(len(x))}
    return out


def frecuentes(d: pd.DataFrame, n: int = 25) -> list[dict[str, Any]]:
    """Movimientos que más se repiten (para cargar rápido)."""
    if d.empty:
        return []
    x = d[~d["_inicial"] & (d["_concepto"] != "") & ~d["_es_frasco"] & ~d["_transferencia"]].copy()
    x["_tipo"] = ["Entrada" if e > 0 else "Salida" for e in x["_entrada"]]
    x["_clave"] = x["_concepto"].map(lambda t: re.sub(r"\s+", " ", t.strip().upper()))
    x = x.sort_values("_fecha", na_position="first")
    grupos = x.groupby("_clave", sort=False)
    filas = []
    for clave, g in grupos:
        ult = g.iloc[-1]
        filas.append({"concepto": ult["_concepto"], "tipo": ult["_tipo"], "rubro": ult["_rubro"],
                      "quien": ult["_quien"], "moneda": ult["_moneda"], "veces": len(g),
                      "importe": float(ult["_entrada"] or ult["_salida"])})
    filas.sort(key=lambda f: (-f["veces"], f["concepto"]))
    return filas[:n]


# ---------------------------------------------------------------------------
# Escritura (sólo lo que cambia)
# ---------------------------------------------------------------------------
def _monto_texto(valor: Any) -> str:
    """Importe con punto decimal y sin miles ("25000.00"): se lee igual en todo el sistema."""
    v = round(float(valor or 0.0), 2)
    return f"{v:.2f}" if v else ""


def fila_nueva(raw: pd.DataFrame, *, fecha: Any, concepto: str, entrada: float = 0.0, salida: float = 0.0,
               moneda: str = "ARS", rubro: str = GENERAL, quien: str = "", medio: str = "Efectivo",
               observaciones: str = "", inicial: bool = False, caja: str = EFECTIVO, frasco: str = "",
               categoria: str = "") -> pd.DataFrame:
    caja = normalizar_cuenta(caja)
    extra = tuple(k for k, usar in (("cuenta", caja != EFECTIVO), ("frasco", bool(frasco))) if usar)
    out, cols = asegurar_columnas(raw, extra)
    fila = {c: "" for c in out.columns}
    fila[cols["fecha"]] = _iso(fecha)
    fila[cols["concepto"]] = concepto.strip()
    fila[cols["ingreso"]] = _monto_texto(entrada)
    fila[cols["egreso"]] = _monto_texto(salida)
    fila[cols["moneda"]] = moneda
    fila[cols["rubro"]] = "" if rubro == GENERAL else rubro
    fila[cols["quien"]] = quien.strip()
    fila[cols["observaciones"]] = observaciones.strip()
    fila[cols["categoria"]] = categoria or ("Saldo inicial" if inicial else ("Ingreso" if entrada else "Egreso"))
    fila[cols["medio"]] = medio or "Efectivo"
    if cols.get("cuenta"):
        fila[cols["cuenta"]] = "" if caja == EFECTIVO else caja
    if cols.get("frasco"):
        fila[cols["frasco"]] = normalizar_frasco(frasco)
    return pd.concat([out, pd.DataFrame([fila], columns=out.columns)], ignore_index=True)


_EDITABLES = ["fecha", "descripcion", "entrada", "salida", "moneda", "rubro", "quien", "medio", "observaciones"]


def transferencia(raw: pd.DataFrame, *, fecha: Any, desde: str, hacia: str, importe: float, moneda: str = "ARS",
                  observaciones: str = "", quien: str = "") -> pd.DataFrame:
    """Pase de plata entre dos cajas internas: sale de una y entra en la otra (no es entrada ni salida del total)."""
    desde, hacia = normalizar_cuenta(desde), normalizar_cuenta(hacia)
    if desde == hacia:
        raise ValueError("La caja de origen y la de destino son la misma.")
    out = fila_nueva(raw, fecha=fecha, concepto=f"TRANSFERENCIA A {hacia.upper()}", salida=importe, moneda=moneda,
                     quien=quien, medio="Transferencia", observaciones=observaciones, caja=desde,
                     categoria=TRANSFERENCIA)
    return fila_nueva(out, fecha=fecha, concepto=f"TRANSFERENCIA DESDE {desde.upper()}", entrada=importe,
                      moneda=moneda, quien=quien, medio="Transferencia", observaciones=observaciones, caja=hacia,
                      categoria=TRANSFERENCIA)


def mover_frasco(raw: pd.DataFrame, *, fecha: Any, caja: str, frasco: str, importe: float, apartar: bool = True,
                 moneda: str = "ARS", observaciones: str = "") -> pd.DataFrame:
    """Aparta plata en un frasco (o la devuelve al disponible). No cambia el saldo de la caja."""
    frasco = normalizar_frasco(frasco)
    if not frasco:
        raise ValueError("Falta el nombre del frasco.")
    concepto = f"APARTADO A FRASCO {frasco.upper()}" if apartar else f"DEVUELTO DE FRASCO {frasco.upper()}"
    return fila_nueva(raw, fecha=fecha, concepto=concepto, entrada=importe if apartar else 0.0,
                      salida=0.0 if apartar else importe, moneda=moneda, medio="Interno", observaciones=observaciones,
                      caja=caja, frasco=frasco, categoria=FRASCO)


def tabla_editable(d: pd.DataFrame, mes: str) -> pd.DataFrame:
    m = d[d["_mes"] == mes].sort_values(["_fecha", "_orden"], na_position="last")
    t = pd.DataFrame({
        "fecha": m["_fecha"].dt.date,
        "descripcion": m["_concepto"],
        "entrada": m["_entrada"].astype(float),
        "salida": m["_salida"].astype(float),
        "moneda": m["_moneda"],
        "rubro": m["_rubro"],
        "quien": m["_quien"],
        "medio": m["_medio"],
        "observaciones": m["_obs"],
        "caja": m["_cuenta"] if "_cuenta" in m.columns else EFECTIVO,
        "frasco": m["_frasco"] if "_frasco" in m.columns else "",
    }, index=m.index)
    return t


def aplicar_edicion(raw: pd.DataFrame, original: pd.DataFrame, editada: pd.DataFrame,
                    borrar: list[Any] | None = None) -> tuple[pd.DataFrame, int]:
    """Escribe en la pestaña sólo las celdas que cambiaron; borra las filas marcadas."""
    extra = tuple(k for k, col in (("cuenta", "caja"), ("frasco", "frasco"))
                  if col in editada.columns and col in original.columns and any(
                      _texto(a) != _texto(b) for a, b in zip(editada[col], original[col])))
    out, cols = asegurar_columnas(raw, extra)
    cambios = 0
    for idx, nueva in editada.iterrows():
        if idx not in out.index or idx not in original.index:
            continue
        vieja = original.loc[idx]
        fila_cambio = False

        def distinto(a: Any, b: Any) -> bool:
            if isinstance(a, float) or isinstance(b, float):
                return abs((numero(a) or 0.0) - (numero(b) or 0.0)) > 0.004
            return _texto(a) != _texto(b)

        if distinto(_iso(nueva["fecha"]) if _texto(nueva["fecha"]) else "", _iso(vieja["fecha"]) if _texto(vieja["fecha"]) else ""):
            out.at[idx, cols["fecha"]] = _iso(nueva["fecha"]) if _texto(nueva["fecha"]) else ""
            fila_cambio = True
        if distinto(nueva["descripcion"], vieja["descripcion"]):
            out.at[idx, cols["concepto"]] = _texto(nueva["descripcion"])
            fila_cambio = True
        montos = False
        for k, c in (("entrada", "ingreso"), ("salida", "egreso")):
            if distinto(float(numero(nueva[k]) or 0.0), float(vieja[k] or 0.0)):
                out.at[idx, cols[c]] = _monto_texto(numero(nueva[k]))
                fila_cambio = montos = True
        for k, c in (("rubro", "rubro"), ("quien", "quien"), ("medio", "medio"), ("observaciones", "observaciones")):
            if distinto(nueva[k], vieja[k]):
                v = _texto(nueva[k])
                out.at[idx, cols[c]] = "" if (k == "rubro" and v == GENERAL) else v
                fila_cambio = True
        for k, c in (("caja", "cuenta"), ("frasco", "frasco")):
            if k in editada.columns and k in original.columns and distinto(nueva[k], vieja[k]):
                v = normalizar_cuenta(nueva[k]) if k == "caja" else normalizar_frasco(nueva[k])
                out.at[idx, cols[c]] = "" if (k == "caja" and v == EFECTIVO) else v
                fila_cambio = True
        if fila_cambio or distinto(nueva["moneda"], vieja["moneda"]):
            # La moneda queda explícita: ya no depende de la marca [USD] del texto.
            out.at[idx, cols["moneda"]] = _texto(nueva["moneda"]) or "ARS"
            for c in ("concepto", "observaciones"):
                out.at[idx, cols[c]] = _TAG.sub("", _texto(out.at[idx, cols[c]]))
            fila_cambio = True
        if montos:
            cat = _sin_acentos(out.at[idx, cols["categoria"]])
            if cat in {"", "ingreso", "egreso"}:
                e = numero(out.at[idx, cols["ingreso"]]) or 0.0
                out.at[idx, cols["categoria"]] = "Ingreso" if e > 0 else "Egreso"
        cambios += int(fila_cambio)
    borrar = [b for b in (borrar or []) if b in out.index]
    if borrar:
        out = out.drop(index=borrar)
    return out.reset_index(drop=True), cambios + len(borrar)


# ---------------------------------------------------------------------------
# Importar la planilla (bloques mensuales apilados)
# ---------------------------------------------------------------------------
def _fecha_celda(v: Any) -> pd.Timestamp | None:
    if isinstance(v, (datetime, pd.Timestamp)):
        return pd.Timestamp(v).normalize()
    if isinstance(v, date):
        return pd.Timestamp(v)
    t = _texto(v)
    if not re.search(r"\d{1,4}[/-]\d{1,2}([/-]\d{2,4})?", t):
        return None
    f = leer_fechas_ar(pd.Series([t])).iloc[0]
    return None if pd.isna(f) else pd.Timestamp(f).normalize()


def _tono(rgb: str) -> str:
    """Rubro según el color de la fila (si la planilla no tiene referencias)."""
    rgb = (rgb or "").upper()[-6:]
    if not re.fullmatch(r"[0-9A-F]{6}", rgb) or rgb in {"FFFFFF", "000000"}:
        return ""
    r, g, b = int(rgb[:2], 16), int(rgb[2:4], 16), int(rgb[4:], 16)
    if g > r + 8 and g > b + 8:
        return ALQUILERES
    if r > g + 12 and r > b + 20 and g > b:
        return GASTOS_COMUNES
    return ""


def leer_planilla(filas: list[list[Any]], colores: list[list[str]] | None = None) -> dict[str, Any]:
    """Lee la planilla de caja: uno o varios meses apilados.

    Cada bloque: encabezado FECHA / DESCRIPCION / ENTRADA / SALIDA, fila SALDO
    INICIAL, movimientos, TOTAL y SALDO; luego "CAJA EN DOLARES" con su saldo
    inicial y movimientos. El color de la fila indica el rubro (las referencias
    ALQUILERES / CAJA GASTOS COMUNES de la planilla dicen qué color es cuál).
    """
    colores = colores or [[""] * len(f) for f in filas]
    leyenda: dict[str, str] = {}
    for r, fila in enumerate(filas):
        for c, v in enumerate(fila):
            t = _sin_acentos(v)
            col = colores[r][c] if r < len(colores) and c < len(colores[r]) else ""
            if not col:
                continue
            if t in {"alquileres", "alquiler"}:
                leyenda[col] = ALQUILERES
            elif "gastos comunes" in t and t.startswith("caja"):
                leyenda[col] = GASTOS_COMUNES
    textos_leyenda = {"alquileres", "alquiler", "caja gastos comunes"}

    movimientos: list[dict[str, Any]] = []
    iniciales: list[dict[str, Any]] = []
    saldos_planilla: list[dict[str, Any]] = []
    cab = None
    moneda = "ARS"
    bloque = None
    for r, fila in enumerate(filas):
        t = [_sin_acentos(v if not isinstance(v, (datetime, date)) else "") for v in fila]
        if "fecha" in t and any(x in t for x in ("descripcion", "detalle", "concepto")) and \
                any(x in t for x in ("entrada", "salida", "ingreso", "egreso")):
            def idx(*nombres: str) -> int | None:
                return next((t.index(n) for n in nombres if n in t), None)
            cab = {"fecha": idx("fecha"), "desc": idx("descripcion", "detalle", "concepto"),
                   "ent": idx("entrada", "ingreso"), "sal": idx("salida", "egreso")}
            base = max(v for v in cab.values() if v is not None)
            cab["quien"] = base + 1
            moneda, bloque = "ARS", None
            continue
        if cab is None:
            continue
        if any("caja en dolares" in x or x in {"dolares", "caja usd", "caja en usd"} for x in t):
            moneda = "USD"
            continue

        def celda(k: str) -> Any:
            j = cab.get(k)
            return fila[j] if j is not None and j < len(fila) else None

        desc = _texto(celda("desc"))
        f = _fecha_celda(celda("fecha"))
        ent, sal = numero(celda("ent")), numero(celda("sal"))
        if f is None:
            if _sin_acentos(desc) == "saldo" and bloque:
                saldos_planilla.append({"mes": bloque, "moneda": moneda, "saldo": (ent or 0.0) - (sal or 0.0)})
            continue
        if not desc:
            continue
        mes = str(pd.Period(f, freq="M"))
        if _sin_acentos(desc).startswith("saldo inicial"):
            bloque = bloque or mes
            iniciales.append({"mes": bloque if moneda == "USD" else mes, "moneda": moneda,
                              "importe": (ent or 0.0) - (sal or 0.0), "fecha": f})
            if moneda == "ARS":
                bloque = mes
            continue
        if not ent and not sal:
            continue
        bloque = bloque or mes
        jd = cab["desc"] or 0
        color = colores[r][jd] if r < len(colores) and jd < len(colores[r]) else ""
        if not color and cab["fecha"] is not None and r < len(colores) and cab["fecha"] < len(colores[r]):
            color = colores[r][cab["fecha"]]
        rubro = leyenda.get(color) or _tono(color) or _rubro_inferido(desc)
        quien = _texto(celda("quien"))
        if _sin_acentos(quien) in textos_leyenda:
            quien = ""
        movimientos.append({"fecha": f, "concepto": desc, "entrada": float(ent or 0.0), "salida": float(sal or 0.0),
                            "moneda": moneda, "rubro": rubro, "quien": quien, "mes": mes})
    return {"movimientos": pd.DataFrame(movimientos), "iniciales": pd.DataFrame(iniciales),
            "saldos": pd.DataFrame(saldos_planilla), "leyenda": bool(leyenda)}


def leer_archivo(nombre: str, contenido: bytes, hoja: str | None = None) -> tuple[dict[str, Any], list[str]]:
    if nombre.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        libro_x = load_workbook(io.BytesIO(contenido), data_only=True)
        hojas = libro_x.sheetnames
        ws = libro_x[hoja] if hoja in hojas else libro_x.worksheets[0]
        filas, colores = [], []
        for fila in ws.iter_rows():
            filas.append([c.value for c in fila])
            cs = []
            for c in fila:
                rgb = ""
                try:
                    if c.fill is not None and c.fill.fill_type == "solid" and c.fill.fgColor.type == "rgb":
                        rgb = str(c.fill.fgColor.rgb or "").upper()
                        if rgb[-6:] in {"FFFFFF", "000000"}:
                            rgb = ""
                except Exception:
                    rgb = ""
                cs.append(rgb)
            colores.append(cs)
        return leer_planilla(filas, colores), hojas
    crudo = pd.read_csv(io.BytesIO(contenido), header=None, dtype=str, sep=None, engine="python")
    return leer_planilla(crudo.where(crudo.notna(), None).values.tolist()), []


def plan_importacion(raw: pd.DataFrame, leido: dict[str, Any], meses_sel: list[str], reemplazar: bool,
                     *, dolar_por_texto: bool = False, usar_inicial_planilla: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Tabla resultante y control de saldos contra la planilla."""
    mov = leido["movimientos"]
    mov = mov[mov["mes"].isin(meses_sel)] if not mov.empty else mov
    base = raw.copy()
    if reemplazar and not base.empty:
        # La planilla importada es la de Efectivo: no se tocan Mercado Pago, Medicación ni los frascos.
        d0 = preparar(base, dolar_por_texto=dolar_por_texto)
        base = base.loc[~(d0["_mes"].isin(meses_sel) & (d0["_cuenta"] == EFECTIVO) & ~d0["_es_frasco"])]
    d_base = de_caja(preparar(base, dolar_por_texto=dolar_por_texto), EFECTIVO)
    nuevo = base.reset_index(drop=True)

    ini = leido["iniciales"]
    for moneda in MONEDAS:
        m_mon = mov[mov["moneda"] == moneda] if not mov.empty else mov
        i_mon = ini[(ini["moneda"] == moneda) & ini["mes"].isin(meses_sel)] if not ini.empty else ini
        meses_mon = sorted(set(m_mon["mes"]) | set(i_mon["mes"])) if (not m_mon.empty or not i_mon.empty) else []
        if not meses_mon:
            continue
        primero = meses_mon[0]
        hay_antes = (not d_base.empty) and ((d_base["_moneda"] == moneda) & (d_base["_mes"] != SIN_FECHA)
                                             & (d_base["_mes"] < primero)).any()
        fila_ini = i_mon[i_mon["mes"] == primero] if not i_mon.empty else i_mon
        if not hay_antes and not fila_ini.empty and abs(float(fila_ini["importe"].iloc[0])) > 0.004:
            imp = float(fila_ini["importe"].iloc[0])
            nuevo = fila_nueva(nuevo, fecha=pd.Period(primero, freq="M").start_time, concepto="SALDO INICIAL",
                               entrada=imp if imp > 0 else 0.0, salida=-imp if imp < 0 else 0.0,
                               moneda=moneda, inicial=True)
    for _, m in (mov.sort_values("fecha", kind="stable").iterrows() if not mov.empty else []):
        nuevo = fila_nueva(nuevo, fecha=m["fecha"], concepto=m["concepto"], entrada=m["entrada"],
                           salida=m["salida"], moneda=m["moneda"], rubro=m["rubro"], quien=m["quien"])

    if usar_inicial_planilla and not ini.empty:
        # Donde el saldo inicial de la planilla no coincide con lo calculado,
        # se registra el de la planilla (como fila SALDO INICIAL del mes).
        for mes in sorted(meses_sel):
            for moneda in MONEDAS:
                i_pl = ini[(ini["mes"] == mes) & (ini["moneda"] == moneda)]
                if i_pl.empty:
                    continue
                d_tmp = de_caja(preparar(nuevo, dolar_por_texto=dolar_por_texto), EFECTIVO)
                ya = (not d_tmp.empty) and bool(((d_tmp["_mes"] == mes) & (d_tmp["_moneda"] == moneda) & d_tmp["_inicial"]).any())
                imp = float(i_pl["importe"].iloc[0])
                if not ya and abs(saldo_inicial(d_tmp, mes, moneda) - imp) > 0.009:
                    nuevo = fila_nueva(nuevo, fecha=pd.Period(mes, freq="M").start_time, concepto="SALDO INICIAL",
                                       entrada=imp if imp > 0 else 0.0, salida=-imp if imp < 0 else 0.0,
                                       moneda=moneda, inicial=True)

    d = de_caja(preparar(nuevo, dolar_por_texto=dolar_por_texto), EFECTIVO)
    control = []
    for mes in meses_sel:
        for moneda in MONEDAS:
            r = resumen_mes(d, mes, moneda)
            i_pl = ini[(ini["mes"] == mes) & (ini["moneda"] == moneda)] if not ini.empty else ini
            s_pl = leido["saldos"]
            s_pl = s_pl[(s_pl["mes"] == mes) & (s_pl["moneda"] == moneda)] if not s_pl.empty else s_pl
            if i_pl.empty and r["movimientos"] == 0:
                continue
            control.append({
                "mes": mes, "moneda": moneda,
                "inicial_planilla": float(i_pl["importe"].iloc[0]) if not i_pl.empty else None,
                "inicial_app": r["inicial"], "entradas": r["entradas"], "salidas": r["salidas"],
                "saldo_planilla": float(s_pl["saldo"].iloc[0]) if not s_pl.empty else None,
                "saldo_app": r["saldo"], "movimientos": r["movimientos"],
            })
    return nuevo, pd.DataFrame(control)


# ---------------------------------------------------------------------------
# Excel con el formato de la planilla
# ---------------------------------------------------------------------------
def excel_caja(d: pd.DataFrame, meses_sel: list[str], titulo: str,
               partes: list[tuple[str, pd.DataFrame]] | None = None) -> bytes:
    """Excel como la planilla. Con ``partes`` hace una hoja por caja interna."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    partes = partes or [(titulo, d)]
    for n_hoja, (titulo_h, d_h) in enumerate(partes):
        ws = wb.active if n_hoja == 0 else wb.create_sheet()
        ws.title = re.sub(r"[\\/*?:\[\]]", "", titulo_h)[:31] or f"Hoja {n_hoja + 1}"
        d = d_h
        azul = PatternFill("solid", fgColor="6D9EEB")
        gris = PatternFill("solid", fgColor="CCCCCC")
        rell = {ALQUILERES: PatternFill("solid", fgColor="D9EAD3"), GASTOS_COMUNES: PatternFill("solid", fgColor="FCE5CD")}
        formato = '"$" #,##0.00'
        ws.append(["", "", "", "", "ALQUILERES"])
        ws.cell(1, 5).fill = rell[ALQUILERES]
        ws.append(["", "", "", "", "CAJA GASTOS COMUNES"])
        ws.cell(2, 5).fill = rell[GASTOS_COMUNES]
        for mes in sorted(meses_sel):
            ws.append([])
            ws.append([nombre_mes(mes).lower()])
            ws.cell(ws.max_row, 1).font = Font(bold=True)
            ws.append([""] * 5)
            for c in ws[ws.max_row][:5]:
                c.fill = azul
            ws.append(["FECHA", "DESCRIPCION", "ENTRADA", "SALIDA", "QUIEN"])
            for c in ws[ws.max_row][:5]:
                c.fill, c.font = gris, Font(bold=True)
            for moneda in MONEDAS:
                r = resumen_mes(d, mes, moneda)
                m_ = _movs(d)
                filas = m_[(m_["_mes"] == mes) & (m_["_moneda"] == moneda) & (~m_["_inicial"])].sort_values(
                    ["_fecha", "_orden"], na_position="last")
                if moneda == "USD":
                    if filas.empty and abs(r["inicial"]) < 0.005:
                        continue
                    ws.append([])
                    ws.append(["CAJA EN DOLARES"])
                    ws.cell(ws.max_row, 1).font = Font(bold=True)
                ws.append([pd.Period(mes, freq="M").start_time.to_pydatetime(), "SALDO INICIAL", r["inicial"], None, ""])
                ws.cell(ws.max_row, 2).font = Font(bold=True)
                ws.cell(ws.max_row, 3).font = Font(bold=True)
                for _, f in filas.iterrows():
                    ws.append([f["_fecha"].to_pydatetime() if pd.notna(f["_fecha"]) else None, f["_concepto"],
                               f["_entrada"] or None, f["_salida"] or None, f["_quien"]])
                    if f["_rubro"] in rell:
                        for c in ws[ws.max_row][:4]:
                            c.fill = rell[f["_rubro"]]
                ws.append([])
                ws.append(["", "TOTAL", r["inicial"] + r["entradas"], r["salidas"], ""])
                for c in ws[ws.max_row][1:4]:
                    c.font = Font(bold=True)
                ws.append(["", "SALDO", r["saldo"], None, ""])
                for c in ws[ws.max_row][1:3]:
                    c.fill, c.font = gris, Font(bold=True)
            for fila in ws.iter_rows(min_row=1, max_row=ws.max_row):
                if isinstance(fila[0].value, datetime):
                    fila[0].number_format = "d/m/yyyy"
                    fila[0].alignment = Alignment(horizontal="right")
                for c in fila[2:4]:
                    if isinstance(c.value, (int, float)):
                        c.number_format = formato if "DOLARES" not in str(ws.cell(c.row, 1).value or "") else formato
        for letra, ancho in zip("ABCDE", (13, 62, 17, 17, 24)):
            ws.column_dimensions[letra].width = ancho
    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()


# ---------------------------------------------------------------------------
# Vista
# ---------------------------------------------------------------------------
COLOR_CAJA = {EFECTIVO: "#8C2853", MERCADO_PAGO: "#009EE3", MEDICACION: "#3E8E5E"}


def _hasta_mes(mes: str) -> pd.Timestamp:
    """Fin del mes elegido (o hoy, si es el mes en curso)."""
    hoy = pd.Timestamp.today().normalize()
    fin = pd.Period(mes, freq="M").end_time.normalize()
    return min(hoy, fin) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)


def frascos_html(d: pd.DataFrame, caja: str, tabla: str, mes: str, saldo_caja: float) -> str:
    fr = frascos(d, caja, tabla, _hasta_mes(mes))
    if not fr:
        return ""
    apartado = sum(fr.values())
    partes = [f'<div class="cj-fr disp"><span>Disponible</span><b>{pesos(saldo_caja - apartado)}</b></div>']
    partes += [f'<div class="cj-fr"><span>🫙 Frasco {html.escape(f)}</span><b>{pesos(v)}</b></div>' for f, v in fr.items()]
    return f'<div class="cj-frascos">{"".join(partes)}</div>'


def distribucion_html(d: pd.DataFrame, mes: str, tabla: str, cajas: list[str], titulo: str = "Total") -> str:
    """Cómo se reparte el total entre las cajas internas (y, dentro de cada una, entre disponible y frascos)."""
    tot = resumen_total(d, mes, "ARS", cajas)
    hasta = _hasta_mes(mes)
    positivo = sum(max(r["saldo"], 0.0) for r in tot["por_caja"].values()) or 1.0
    barra = "".join(f'<i style="width:{max(r["saldo"], 0.0) / positivo * 100:.2f}%;background:{COLOR_CAJA.get(c, "#999")}" '
                    f'title="{html.escape(c)}"></i>' for c, r in tot["por_caja"].items())
    tarjetas = []
    for c in cajas:
        r = tot["por_caja"][c]
        color = COLOR_CAJA.get(c, "#999")
        pct = (r["saldo"] / tot["saldo"] * 100) if abs(tot["saldo"]) > 0.004 else 0.0
        lineas = [f'<div><span>Entradas</span><b class="e">{pesos(r["entradas_propias"])}</b></div>',
                  f'<div><span>Salidas</span><b class="s">{pesos(r["salidas_propias"])}</b></div>']
        if r["transf_entrada"] or r["transf_salida"]:
            neto = r["transf_entrada"] - r["transf_salida"]
            lineas.append(f'<div><span>Transferencias</span><b>{"+" if neto >= 0 else "−"}{pesos(abs(neto))}</b></div>')
        fr = frascos(d, c, tabla, hasta)
        if fr:
            apartado = sum(fr.values())
            lineas.append(f'<div class="sep"><span>Disponible</span><b>{pesos(r["saldo"] - apartado)}</b></div>')
            lineas += [f'<div><span>🫙 {html.escape(f)}</span><b>{pesos(v)}</b></div>' for f, v in fr.items()]
        tarjetas.append(
            f'<div class="cj-caja" style="border-top-color:{color}"><div class="n"><i style="background:{color}"></i>'
            f'{html.escape(c)}<em>{pct:.0f}%</em></div><div class="v">{pesos(r["saldo"])}</div>'
            f'<div class="d">{"".join(lineas)}</div></div>')
    return (f'<div class="cj-dist"><div class="cj-dist-cab"><b>{html.escape(titulo)} · {pesos(tot["saldo"])}</b>'
            f'<span>{"hasta hoy" if mes >= str(pd.Period(date.today(), freq="M")) else "al cierre de " + nombre_mes(mes).lower()}'
            f'</span></div><div class="cj-barra">{barra}</div><div class="cj-cajas">{"".join(tarjetas)}</div></div>')


_CSS = """
<style>
.cj-dist{border:1px solid #E8E3E6;border-radius:14px;background:#fff;padding:.75rem .9rem .9rem;margin:.2rem 0 .9rem}
.cj-dist-cab{display:flex;justify-content:space-between;align-items:baseline;gap:.6rem;margin-bottom:.45rem}
.cj-dist-cab b{font-size:.95rem;color:#3A1426}.cj-dist-cab span{font-size:.76rem;color:#8A7F85}
.cj-barra{display:flex;height:10px;border-radius:999px;overflow:hidden;background:#F1ECEF;margin-bottom:.7rem}
.cj-barra i{display:block;height:100%}
.cj-cajas{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:.6rem}
.cj-caja{border:1px solid #EEE7EA;border-top:4px solid #999;border-radius:12px;padding:.6rem .75rem;background:#FCFAFB}
.cj-caja .n{display:flex;align-items:center;gap:.4rem;font-size:.76rem;font-weight:700;letter-spacing:.04em;
  text-transform:uppercase;color:#5D5258}
.cj-caja .n i{width:9px;height:9px;border-radius:50%;display:inline-block}
.cj-caja .n em{margin-left:auto;font-style:normal;font-weight:600;color:#8A7F85;letter-spacing:0}
.cj-caja .v{font-size:1.35rem;font-weight:750;color:#1F1A1D;margin:.15rem 0 .35rem;font-variant-numeric:tabular-nums}
.cj-caja .d div{display:flex;justify-content:space-between;gap:.5rem;font-size:.8rem;color:#6E6268;padding:.08rem 0}
.cj-caja .d b{font-weight:600;color:#3A3236;font-variant-numeric:tabular-nums}
.cj-caja .d b.e{color:#2F7D4F}.cj-caja .d b.s{color:#A33B4C}
.cj-caja .d .sep{border-top:1px dashed #E2D8DD;margin-top:.25rem;padding-top:.3rem}
.cj-frascos{display:flex;flex-wrap:wrap;gap:.5rem;margin:.1rem 0 .7rem}
.cj-fr{display:flex;flex-direction:column;padding:.45rem .75rem;border-radius:10px;border:1px solid #CDE7F5;
  background:#F3FAFE;min-width:170px}
.cj-fr.disp{border-color:#E8E3E6;background:#fff}
.cj-fr span{font-size:.74rem;color:#5D5258;font-weight:600}.cj-fr b{font-size:1rem;font-variant-numeric:tabular-nums}
.cj-leyenda{display:flex;flex-wrap:wrap;gap:.5rem;margin:.2rem 0 .7rem}
.cj-rub{display:flex;flex-direction:column;gap:.1rem;padding:.5rem .75rem;border-radius:10px;border:1px solid #E8E3E6;min-width:200px;flex:1;background:#fff}
.cj-rub .t{font-size:.74rem;font-weight:650;letter-spacing:.03em;text-transform:uppercase;color:#5D5258;display:flex;align-items:center;gap:.4rem}
.cj-rub .t i{display:inline-block;width:12px;height:12px;border-radius:3px}
.cj-rub .v{font-size:.84rem;color:#3A3236;font-variant-numeric:tabular-nums}
.cj-tabla{border:1px solid #E8E3E6;border-radius:12px;overflow:hidden;background:#fff;margin-bottom:.9rem}
.cj-titulo{display:flex;justify-content:space-between;align-items:center;gap:.6rem;padding:.6rem .9rem;background:#F7EFF3;border-bottom:1px solid #EADDE4}
.cj-titulo b{font-size:.8rem;letter-spacing:.05em;text-transform:uppercase;color:#5E1A39}
.cj-titulo span{font-size:.78rem;color:#6E6268}
.cj-cols,.cj-row{display:grid;grid-template-columns:86px minmax(0,1fr) 128px 128px 128px 150px;gap:.5rem;align-items:center;padding:.45rem .9rem}
.cj-cols{font-size:.7rem;font-weight:600;color:#8A7F85;text-transform:uppercase;letter-spacing:.04em;padding-top:.4rem;padding-bottom:.25rem}
.cj-row{border-top:1px solid #F1ECEF;font-size:.87rem;border-left:4px solid transparent}
.cj-fecha{color:#5D5258;font-variant-numeric:tabular-nums;white-space:nowrap}
.cj-desc{min-width:0;color:#1F1A1D;overflow-wrap:anywhere}
.cj-desc small{display:block;color:#8A7F85;font-size:.75rem}
.cj-num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.cj-ent{color:#2F7D4F}.cj-sal{color:#A33B4C}.cj-sdo{color:#6E6268;font-size:.82rem}
.cj-quien{color:#5D5258;font-size:.8rem;text-align:right;overflow-wrap:anywhere}
.cj-ini{background:#FBF8FA;font-weight:650}
.cj-tot{background:#F3EEF1;font-weight:650;border-top:1px solid #E2D8DD}
.cj-saldo{background:#5E1A39;color:#fff;font-weight:700}
.cj-saldo .cj-num,.cj-saldo .cj-desc{color:#fff}
.cj-meta{display:none}
.cj-aviso{padding:.55rem .8rem;border-radius:10px;background:#FFF6E8;border:1px solid #F1D9AE;color:#7A5A1E;font-size:.84rem;margin:.2rem 0 .7rem}
.cj-usd-vacio{font-size:.84rem;color:#6E6268;padding:.55rem .9rem;border:1px dashed #DDD3D8;border-radius:10px}
@media (max-width: 640px){
 .cj-cols{display:none}
 .cj-row{grid-template-columns:minmax(0,1fr) auto;gap:.1rem .6rem;padding:.5rem .7rem}
 .cj-row .cj-fecha,.cj-row .cj-quien,.cj-row .cj-sdo,.cj-row .cj-col-ent,.cj-row .cj-col-sal{display:none}
 .cj-row .cj-movil{display:block}
 .cj-meta{display:block;grid-column:1 / -1;font-size:.74rem;color:#8A7F85}
 .cj-saldo .cj-meta,.cj-tot .cj-meta{color:inherit;opacity:.85}
 .cj-titulo{flex-direction:column;align-items:flex-start;gap:.1rem;padding:.55rem .7rem}
}
.cj-movil{display:none}
</style>
"""


def _fila(fecha: str, desc: str, ent: str, sal: str, sdo: str, quien: str, *, clase: str = "",
          estilo: str = "", movil: str = "", meta: str = "", nota: str = "") -> str:
    e = html.escape
    nota_html = f"<small>{e(nota)}</small>" if nota else ""
    return (f'<div class="cj-row {clase}" style="{estilo}">'
            f'<div class="cj-fecha">{e(fecha)}</div><div class="cj-desc">{e(desc)}{nota_html}</div>'
            f'<div class="cj-num cj-ent cj-col-ent">{ent}</div><div class="cj-num cj-sal cj-col-sal">{sal}</div>'
            f'<div class="cj-num cj-sdo">{sdo}</div><div class="cj-quien">{e(quien)}</div>'
            f'<div class="cj-num cj-movil">{movil}</div><div class="cj-meta">{e(meta)}</div></div>')


def planilla_html(d: pd.DataFrame, mes: str, moneda: str = "ARS", *, rubro: str | None = None,
                  buscar: str = "", titulo: str = "") -> str:
    r = resumen_mes(d, mes, moneda)
    m_ = _movs(d)
    filas = m_[(m_["_mes"] == mes) & (m_["_moneda"] == moneda) & (~m_["_inicial"])].sort_values(
        ["_fecha", "_orden"], na_position="last")
    filtrado = bool(rubro) or bool(buscar.strip())
    if rubro:
        filas = filas[filas["_rubro"] == rubro]
    if buscar.strip():
        b = _sin_acentos(buscar)
        mascara = [b in _sin_acentos(f"{c} {q} {o}") for c, q, o in zip(filas["_concepto"], filas["_quien"], filas["_obs"])]
        filas = filas[pd.Series(mascara, index=filas.index, dtype=bool)]  # lista vacía ≠ «ninguna columna»
    din = lambda v: dinero(v, moneda)  # noqa: E731
    partes = ['<div class="cj-tabla">']
    if titulo:
        partes.append(f'<div class="cj-titulo"><b>{html.escape(titulo)}</b>'
                      f'<span>{r["movimientos"]} movimientos · saldo {din(r["saldo"])}</span></div>')
    partes.append('<div class="cj-cols"><div>Fecha</div><div>Descripción</div><div style="text-align:right">Entrada</div>'
                  '<div style="text-align:right">Salida</div><div style="text-align:right">Saldo</div>'
                  '<div style="text-align:right">Quién</div></div>')
    inicio = pd.Period(mes, freq="M").start_time if mes != SIN_FECHA else None
    corrido = r["inicial"]
    if not filtrado and mes != SIN_FECHA:
        partes.append(_fila(f"{inicio.day}/{inicio.month}/{inicio.year}", "SALDO INICIAL", din(r["inicial"]), "", "", "",
                            clase="cj-ini", movil=din(r["inicial"]), meta=f"{inicio:%d/%m/%Y}"))
    for _, f in filas.iterrows():
        corrido += f["_entrada"] - f["_salida"]
        fecha = f"{f['_fecha'].day}/{f['_fecha'].month}/{f['_fecha'].year}" if pd.notna(f["_fecha"]) else "—"
        estilo = (f"background:{FONDO_RUBRO[f['_rubro']]};border-left-color:{BORDE_RUBRO[f['_rubro']]}"
                  if f["_rubro"] != GENERAL else "")
        movil = (f'<span class="cj-ent">+{din(f["_entrada"])}</span>' if f["_entrada"] else "") + \
                (f'<span class="cj-sal">−{din(f["_salida"])}</span>' if f["_salida"] else "")
        meta = " · ".join(x for x in (fecha, f["_quien"], f["_rubro"] if f["_rubro"] != GENERAL else "") if x)
        partes.append(_fila(fecha, f["_concepto"] or "(sin descripción)", din(f["_entrada"]) if f["_entrada"] else "",
                            din(f["_salida"]) if f["_salida"] else "", "" if filtrado else din(corrido), f["_quien"],
                            estilo=estilo, movil=movil, meta=meta, nota=f["_obs"]))
    if filtrado:
        ent, sal = float(filas["_entrada"].sum()), float(filas["_salida"].sum())
        partes.append(_fila("", f"TOTAL ({len(filas)})", din(ent), din(sal), "", "", clase="cj-tot",
                            movil=din(ent - sal), meta=f"Entradas {din(ent)} · Salidas {din(sal)}"))
    else:
        tot_e = r["inicial"] + r["entradas"]
        partes.append(_fila("", "TOTAL", din(tot_e), din(r["salidas"]), "", "", clase="cj-tot",
                            movil="", meta=f"Entradas {din(tot_e)} · Salidas {din(r['salidas'])}"))
        partes.append(_fila("", "SALDO", din(r["saldo"]), "", "", "", clase="cj-saldo", movil=din(r["saldo"])))
    partes.append("</div>")
    return "".join(partes)


def rubros_html(d: pd.DataFrame, mes: str) -> str:
    pr = por_rubro(d, mes, "ARS")
    partes = ['<div class="cj-leyenda">']
    for rubro in (ALQUILERES, GASTOS_COMUNES, GENERAL):
        x = pr[rubro]
        color = BORDE_RUBRO[rubro] if rubro != GENERAL else "#CFC6CB"
        if rubro == ALQUILERES:
            valor = f"Cobrado {pesos(x['entradas'])}" + (f" · Salidas {pesos(x['salidas'])}" if x["salidas"] else "")
        else:
            valor = f"Entró {pesos(x['entradas'])} · Salió {pesos(x['salidas'])} · Neto {pesos(x['entradas'] - x['salidas'])}"
        partes.append(f'<div class="cj-rub"><div class="t"><i style="background:{color}"></i>{html.escape(rubro)}'
                      f' <span style="font-weight:400;text-transform:none;letter-spacing:0">({x["n"]})</span></div>'
                      f'<div class="v">{valor}</div></div>')
    partes.append("</div>")
    return "".join(partes)


def render_caja(
    raw_original: pd.DataFrame | None,
    *,
    tabla: str,
    nombre: str,
    guardar: Callable[[pd.DataFrame], Any],
    selector: Callable[..., str] | None = None,
    mostrar_error: Callable[[str, Exception], None] | None = None,
) -> None:
    raw = raw_original.copy() if isinstance(raw_original, pd.DataFrame) else pd.DataFrame()
    dolar_txt = tabla == "caja_vmr"
    d = preparar(raw, dolar_por_texto=dolar_txt)
    k = f"cj_{tabla}_"
    rev_key = f"{k}rev"

    def rev() -> int:
        return int(st.session_state.get(rev_key, 0))

    def _error(msg: str, exc: Exception) -> None:
        (mostrar_error or (lambda m, e: st.error(f"{m} {e}")))(msg, exc)

    def _guardar(nuevo: pd.DataFrame, aviso: str, mes_ir: str | None = None) -> None:
        try:
            guardar(nuevo)
        except Exception as exc:
            _error("No se pudieron guardar los cambios en Google Sheets.", exc)
            return
        st.session_state[rev_key] = rev() + 1
        if mes_ir:
            st.session_state[f"{k}mes_pendiente"] = mes_ir
        st.toast(aviso, icon=":material/check_circle:")
        st.rerun()

    st.markdown(_CSS, unsafe_allow_html=True)

    cajas = cajas_internas(tabla)
    multi = len(cajas) > 1
    total_v = "Total " + nombre.replace("Caja ", "")
    vista = total_v
    if multi:
        vista = st.segmented_control("Caja interna", [total_v, *cajas], default=total_v, key=f"{k}vista",
                                     label_visibility="collapsed") or total_v
    es_total = multi and vista == total_v
    dv = d if (not multi or es_total) else de_caja(d, vista)

    lista = meses(d)
    hoy_mes = str(pd.Period(date.today(), freq="M"))
    opciones = sorted(set(lista) - {SIN_FECHA} | {hoy_mes}, reverse=True) + ([SIN_FECHA] if SIN_FECHA in lista else [])
    clave_mes = f"{k}mes"
    pendiente = st.session_state.pop(f"{k}mes_pendiente", None)
    if pendiente in opciones:
        st.session_state[clave_mes] = pendiente
    if st.session_state.get(clave_mes) not in opciones:
        # Siempre arranca en el mes actual (aunque todavía no tenga movimientos:
        # muestra el saldo que viene del mes anterior).
        st.session_state[clave_mes] = hoy_mes

    c_mes, c_xls = st.columns([1.2, 1])
    with c_mes:
        mes = st.selectbox("Mes", opciones, key=clave_mes,
                           format_func=lambda m: nombre_mes(m) if m != SIN_FECHA else "Sin fecha (revisar)")
    with c_xls:
        if not d.empty and mes != SIN_FECHA:
            partes_x = [(c, de_caja(d, c)) for c in cajas] if es_total else [(vista if multi else nombre, dv)]
            st.download_button("Descargar Excel del mes", data=excel_caja(dv, [mes], nombre, partes_x),
                               file_name=f"{tabla}_{mes}.xlsx", key=f"{k}xls_{mes}",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               icon=":material/download:", use_container_width=True)

    sf = int((d["_mes"] == SIN_FECHA).sum()) if not d.empty else 0

    def _resumen(moneda: str) -> dict[str, Any]:
        if es_total:
            return resumen_total(d, mes, moneda, cajas)
        if multi:
            rc = resumen_caja(d, mes, moneda, vista)
            return rc
        return resumen_mes(d, mes, moneda)

    if mes != SIN_FECHA:
        r = _resumen("ARS")
        tarjetas_kpi([
            ("Saldo inicial", pesos(r["inicial"]), f"1/{int(mes[5:])}/{mes[:4]}", "#8A7F85"),
            ("Entradas", pesos(r["entradas"]), nombre_mes(mes), "#2F7D4F"),
            ("Salidas", pesos(r["salidas"]), f"{r['movimientos']} movimientos", "#A33B4C"),
            ("Saldo", pesos(r["saldo"]), "Al cierre del mes" if mes < hoy_mes else "Hasta hoy", "#8C2853"),
        ])
        if multi and not es_total and (r.get("transf_entrada") or r.get("transf_salida")):
            st.caption(f":material/swap_horiz: Las entradas y salidas incluyen transferencias con otras cajas: "
                       f"entró {pesos(r['transf_entrada'])} · salió {pesos(r['transf_salida'])}.")
        ru = _resumen("USD")
        if ru["movimientos"] or abs(ru["inicial"]) > 0.004:
            st.caption(f":material/attach_money: Caja en dólares: saldo **{dinero(ru['saldo'], 'USD')}** "
                       f"(entró {dinero(ru['entradas'], 'USD')} · salió {dinero(ru['salidas'], 'USD')}).")
        for c_ in (cajas if es_total else [vista if multi else None]):
            lb = libro(de_caja(d, c_) if c_ else d, "ARS")
            dif = lb[(lb["mes"] == mes) & (lb["diferencia"].abs() > 0.009)]
            if not dif.empty:
                x = dif.iloc[0]
                quien_c = f" de {c_}" if (multi and c_) else ""
                st.markdown(f'<div class="cj-aviso">El saldo inicial{quien_c} cargado este mes ({pesos(x["inicial_cargado"])}) '
                            f'no coincide con el cierre del mes anterior ({pesos(x["inicial_calculado"])}): diferencia '
                            f'{pesos(x["diferencia"])}.</div>', unsafe_allow_html=True)
        if es_total:
            st.markdown(distribucion_html(d, mes, tabla, cajas, total_v), unsafe_allow_html=True)
        elif multi:
            st.markdown(frascos_html(d, vista, tabla, mes, r["saldo"]), unsafe_allow_html=True)
    if sf:
        st.markdown(f'<div class="cj-aviso">{sf} movimiento(s) sin fecha válida. Elegí «Sin fecha (revisar)» en el mes '
                    'y corregilos en Editar.</div>', unsafe_allow_html=True)

    clave_sec = f"{k}seccion"
    if selector:
        seccion = selector("Sección", SECCIONES, key=clave_sec)
    else:
        seccion = st.segmented_control("Sección", SECCIONES, default=SECCIONES[0], key=clave_sec,
                                       label_visibility="collapsed") or SECCIONES[0]

    # ---- Planilla ----------------------------------------------------------
    if seccion == "Planilla":
        if d.empty:
            st.info("Todavía no hay movimientos. Cargá el saldo inicial y los movimientos en «Cargar movimiento», "
                    "o traé la planilla en «Importar».")
            return
        if es_total and mes != SIN_FECHA:
            # Una planilla por caja interna, cada una con su saldo corrido.
            buscar_t = st.text_input("Buscar", key=f"{k}buscar_total", placeholder="Descripción, quién u observación")
            for c_ in cajas:
                dc = de_caja(d, c_)
                rc = resumen_mes(dc, mes, "ARS")
                if dc.empty or (rc["movimientos"] == 0 and abs(rc["inicial"]) < 0.005):
                    st.markdown(f'<div class="cj-usd-vacio"><b>{html.escape(c_)}</b> · sin movimientos este mes · '
                                f'saldo {pesos(rc["saldo"])}</div>', unsafe_allow_html=True)
                    continue
                st.markdown(planilla_html(dc, mes, "ARS", buscar=buscar_t, titulo=c_), unsafe_allow_html=True)
                ru_c = resumen_mes(dc, mes, "USD")
                if ru_c["movimientos"] or abs(ru_c["inicial"]) > 0.004:
                    st.markdown(planilla_html(dc, mes, "USD", buscar=buscar_t, titulo=f"{c_} · dólares"),
                                unsafe_allow_html=True)
            return
        d_full = d
        d = dv  # desde acá, la planilla de la caja interna elegida
        if mes == SIN_FECHA:
            st.info("Estos movimientos no tienen una fecha válida. Corregilos en «Editar».")
        elif not multi or vista == EFECTIVO:
            st.markdown(rubros_html(d, mes), unsafe_allow_html=True)
        f1, f2 = st.columns([1.4, 1])
        with f1:
            rubro = st.pills("Rubro", ["Todos"] + RUBROS, default="Todos", key=f"{k}rubro") or "Todos"
        with f2:
            buscar = st.text_input("Buscar", key=f"{k}buscar", placeholder="Descripción, quién u observación")
        st.markdown(planilla_html(d, mes, "ARS", rubro=None if rubro == "Todos" else rubro, buscar=buscar,
                                  titulo=f"{vista} · pesos" if multi else "Caja en pesos"), unsafe_allow_html=True)
        lb_usd = libro(d, "USD")
        ru = resumen_mes(d, mes, "USD")
        if ru["movimientos"] or abs(ru["inicial"]) > 0.004:
            st.markdown(planilla_html(d, mes, "USD", rubro=None if rubro == "Todos" else rubro, buscar=buscar,
                                      titulo=f"{vista} · dólares" if multi else "Caja en dólares"), unsafe_allow_html=True)
        elif mes != SIN_FECHA:
            ultimo = dinero(lb_usd["saldo"].iloc[-1], "USD") if not lb_usd.empty else "US$ 0,00"
            st.markdown(f'<div class="cj-usd-vacio"><b>Caja en dólares</b> · sin movimientos este mes · saldo {ultimo}</div>',
                        unsafe_allow_html=True)
        if multi:
            mov_fr = d_full[(d_full["_cuenta"] == vista) & d_full["_es_frasco"] & (d_full["_mes"] == mes)]
            if len(mov_fr):
                with st.expander(f"Movimientos de frascos ({len(mov_fr)})", icon=":material/savings:"):
                    st.dataframe(pd.DataFrame({
                        "Fecha": mov_fr["_fecha"].dt.strftime("%d/%m/%Y"), "Frasco": mov_fr["_frasco"],
                        "Apartado": mov_fr["_entrada"].where(mov_fr["_entrada"] > 0), "Devuelto": mov_fr["_salida"].where(mov_fr["_salida"] > 0),
                        "Detalle": mov_fr["_obs"]}), hide_index=True, use_container_width=True,
                        column_config={c: st.column_config.NumberColumn(c, format="localized") for c in ("Apartado", "Devuelto")})
        return

    # ---- Cargar movimiento -------------------------------------------------
    if seccion == "Cargar movimiento":
        frec = frecuentes(d)
        clave_modelo = f"{k}modelo"
        claves = {c: f"{k}alta_{c}_{rev()}" for c in ("tipo", "moneda", "desc", "imp", "rubro", "quien", "fecha",
                                                      "medio", "obs", "caja", "frasco")}

        def usar_modelo() -> None:
            sel = st.session_state.get(clave_modelo)
            f = next((x for x in frec if x["concepto"] == sel), None)
            if not f:
                return
            st.session_state[claves["tipo"]] = f["tipo"]
            st.session_state[claves["moneda"]] = f["moneda"]
            st.session_state[claves["desc"]] = f["concepto"]
            st.session_state[claves["rubro"]] = f["rubro"]
            st.session_state[claves["quien"]] = f["quien"]
            st.session_state[claves["imp"]] = float(f["importe"])

        if frec:
            st.selectbox("Partir de un movimiento frecuente (opcional)", [None] + [f["concepto"] for f in frec],
                         format_func=lambda c: "—" if c is None else
                         next(f"{c} · {x['tipo'].lower()} · {x['veces']} veces" for x in frec if x["concepto"] == c),
                         key=clave_modelo, on_change=usar_modelo)
        a1, a2 = st.columns(2)
        with a1:
            tipos = ["Entrada", "Salida", "Saldo inicial"] + (["Transferencia", "Frasco"] if multi else [])
            tipo = st.segmented_control("Tipo", tipos, default="Entrada", key=claves["tipo"]) or "Entrada"
        with a2:
            moneda = st.segmented_control("Moneda", MONEDAS, default="ARS", key=claves["moneda"]) or "ARS"
        caja_sel, frasco_sel = EFECTIVO, ""
        hoy_d = date.today()
        if multi and tipo == "Transferencia":
            st.caption("Pase de plata entre cajas internas: sale de una y entra en la otra. No cuenta como entrada ni "
                       "salida del total.")
            t1, t2 = st.columns(2)
            with t1:
                desde = st.selectbox("Desde", cajas, index=cajas.index(vista) if vista in cajas else 0, key=f"{k}tr_desde_{rev()}")
            with t2:
                hacia = st.selectbox("Hacia", [c for c in cajas if c != desde], key=f"{k}tr_hacia_{rev()}")
            t3, t4 = st.columns(2)
            with t3:
                fecha_t = st.date_input("Fecha", value=hoy_d, format="DD/MM/YYYY", key=f"{k}tr_fecha_{rev()}")
            with t4:
                imp_t = st.number_input(f"Importe ({'US$' if moneda == 'USD' else '$'})", min_value=0.0, step=1000.0,
                                        format="%.2f", key=f"{k}tr_imp_{rev()}")
            obs_t = st.text_input("Detalle (opcional)", key=f"{k}tr_obs_{rev()}")
            sd = saldo_hoy(de_caja(d, desde), moneda)
            if imp_t > sd + 0.004:
                st.warning(f"{desde} tiene {dinero(sd, moneda)}: con esta transferencia queda en negativo.")
            if st.button("Guardar transferencia", type="primary", disabled=imp_t <= 0, key=f"{k}tr_ok",
                         icon=":material/swap_horiz:", use_container_width=es_movil()):
                nuevo = transferencia(raw, fecha=fecha_t, desde=desde, hacia=hacia, importe=imp_t, moneda=moneda,
                                      observaciones=obs_t)
                _guardar(nuevo, f"Transferencia de {dinero(imp_t, moneda)}: {desde} → {hacia}",
                         str(pd.Period(fecha_t, freq="M")))
            return
        if multi and tipo == "Frasco":
            st.caption("Los frascos apartan plata dentro de una caja sin que salga: el saldo de la caja y el total no "
                       "cambian, sólo cuánto queda disponible.")
            f1_, f2_ = st.columns(2)
            with f1_:
                caja_f = st.selectbox("Caja", cajas, index=cajas.index(vista) if vista in cajas else
                                      (cajas.index(MERCADO_PAGO) if MERCADO_PAGO in cajas else 0), key=f"{k}fr_caja_{rev()}")
            existentes = frascos(d, caja_f, tabla)
            with f2_:
                elegido = st.selectbox("Frasco", [*existentes, "➕ Frasco nuevo"], key=f"{k}fr_nombre_{rev()}")
            nombre_f = elegido
            if elegido == "➕ Frasco nuevo":
                nombre_f = st.text_input("Nombre del frasco nuevo", key=f"{k}fr_nuevo_{rev()}", placeholder="Ej.: Aguinaldos")
            f3_, f4_, f5_ = st.columns([1.2, 1, 1])
            with f3_:
                accion_f = st.segmented_control("Movimiento", ["Apartar", "Devolver al disponible"], default="Apartar",
                                                key=f"{k}fr_accion_{rev()}") or "Apartar"
            with f4_:
                imp_f = st.number_input(f"Importe ({'US$' if moneda == 'USD' else '$'})", min_value=0.0, step=1000.0,
                                        format="%.2f", key=f"{k}fr_imp_{rev()}")
            with f5_:
                fecha_f = st.date_input("Fecha", value=hoy_d, format="DD/MM/YYYY", key=f"{k}fr_fecha_{rev()}")
            obs_f = st.text_input("Detalle (opcional)", key=f"{k}fr_obs_{rev()}")
            saldo_c = saldo_hoy(de_caja(d, caja_f), moneda)
            fr_act = frascos(d, caja_f, tabla, moneda=moneda)
            disp = saldo_c - sum(fr_act.values())
            en_frasco = fr_act.get(normalizar_frasco(nombre_f), 0.0)
            st.caption(f"{caja_f}: saldo {dinero(saldo_c, moneda)} · disponible {dinero(disp, moneda)}"
                       + (f" · en el frasco {dinero(en_frasco, moneda)}" if nombre_f and nombre_f in fr_act else ""))
            apartar = accion_f == "Apartar"
            if apartar and imp_f > disp + 0.004:
                st.warning("Es más de lo que hay disponible en esa caja.")
            if not apartar and imp_f > en_frasco + 0.004:
                st.warning("Es más de lo que hay en el frasco.")
            if st.button("Guardar", type="primary", disabled=imp_f <= 0 or not normalizar_frasco(nombre_f),
                         key=f"{k}fr_ok", icon=":material/savings:", use_container_width=es_movil()):
                nuevo = mover_frasco(raw, fecha=fecha_f, caja=caja_f, frasco=nombre_f, importe=imp_f, apartar=apartar,
                                     moneda=moneda, observaciones=obs_f)
                _guardar(nuevo, f"{'Apartado' if apartar else 'Devuelto'} {dinero(imp_f, moneda)} "
                                f"{'a' if apartar else 'de'} frasco {normalizar_frasco(nombre_f)}",
                         str(pd.Period(fecha_f, freq="M")))
            return
        if multi:
            c_a, c_b = st.columns([1.4, 1])
            with c_a:
                caja_sel = st.segmented_control("Caja", cajas, default=vista if vista in cajas else EFECTIVO,
                                                key=claves["caja"]) or EFECTIVO
            fr_caja = frascos(d, caja_sel, tabla) if tipo in ("Entrada", "Salida") else {}
            if fr_caja:
                with c_b:
                    op = st.selectbox("¿De un frasco?", ["No (disponible)", *fr_caja], key=claves["frasco"],
                                      help="Si el gasto se paga con plata apartada en un frasco, elegilo: baja la caja y "
                                           "el frasco.")
                    frasco_sel = "" if op.startswith("No") else op
        b1, b2 = st.columns([1, 1.4])
        with b1:
            hoy = date.today()
            defecto = hoy if mes in (SIN_FECHA, hoy_mes) else pd.Period(mes, freq="M").start_time.date()
            if tipo == "Saldo inicial":
                defecto = (pd.Period(mes, freq="M").start_time.date() if mes != SIN_FECHA else hoy.replace(day=1))
            fecha = st.date_input("Fecha", value=defecto, format="DD/MM/YYYY", key=claves["fecha"] + tipo)
        with b2:
            importe = st.number_input(f"Importe ({'US$' if moneda == 'USD' else '$'})", min_value=0.0, step=1000.0,
                                      format="%.2f", key=claves["imp"])
        desc = st.text_input("Descripción", key=claves["desc"], disabled=tipo == "Saldo inicial",
                             placeholder="Ej.: ALQUILER DE CONSULTORIO" if tipo != "Saldo inicial" else "SALDO INICIAL")
        if tipo != "Saldo inicial":
            c1, c2 = st.columns([1.4, 1])
            with c1:
                rubro = st.segmented_control("Rubro", RUBROS, default=GENERAL, key=claves["rubro"]) or GENERAL
            with c2:
                quien = st.text_input("Quién", key=claves["quien"], placeholder="Ej.: SOLER, AGUIRRE…")
            with st.expander("Más datos", icon=":material/more_horiz:"):
                medio = st.selectbox("Medio", ["Efectivo", "Transferencia", "Tarjeta", "Cheque", "Otro"], key=claves["medio"])
                obs = st.text_input("Observaciones", key=claves["obs"])
        else:
            rubro, quien, medio, obs = GENERAL, "", "Efectivo", ""
            st.caption("El saldo inicial es con lo que arranca la caja ese mes. No se cuenta como entrada; "
                       "los meses siguientes se calculan solos.")
        listo = importe > 0 and (tipo == "Saldo inicial" or desc.strip())
        etiqueta = {"Entrada": "Guardar entrada", "Salida": "Guardar salida", "Saldo inicial": "Guardar saldo inicial"}[tipo]
        if st.button(etiqueta, type="primary", disabled=not listo, key=f"{k}alta_ok", icon=":material/save:",
                     use_container_width=es_movil()):
            nuevo = fila_nueva(raw, fecha=fecha, concepto="SALDO INICIAL" if tipo == "Saldo inicial" else desc,
                               entrada=importe if tipo != "Salida" else 0.0, salida=importe if tipo == "Salida" else 0.0,
                               moneda=moneda, rubro=rubro, quien=quien, medio=medio, observaciones=obs,
                               inicial=tipo == "Saldo inicial", caja=caja_sel, frasco=frasco_sel)
            st.session_state.pop(clave_modelo, None)
            _guardar(nuevo, f"{tipo} de {dinero(importe, moneda)} guardada" + (f" en {caja_sel}" if multi else ""),
                     str(pd.Period(fecha, freq="M")))
        return

    # ---- Editar ------------------------------------------------------------
    if seccion == "Editar":
        original = tabla_editable(dv, mes)
        if not multi:
            original = original.drop(columns=["caja", "frasco"])
        if original.empty:
            st.info("Este mes no tiene movimientos.")
            return
        st.caption("Tocá una celda para cambiarla. Para borrar, marcá 🗑️ y guardá. "
                   "Sólo se modifican en Google Sheets las celdas que cambies.")
        vista = original.copy()
        vista.insert(0, "borrar", False)
        form_ed = st.form(f"{k}form_editor_{mes}_{rev()}", border=False)
        editada = form_ed.data_editor(
            vista, hide_index=True, use_container_width=True, num_rows="fixed",
            height=min(80 + 36 * len(vista), 720),
            column_config={
                "borrar": st.column_config.CheckboxColumn("🗑️", width="small"),
                "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
                "descripcion": st.column_config.TextColumn("Descripción", width="large"),
                "entrada": st.column_config.NumberColumn("Entrada", min_value=0.0, format="localized"),
                "salida": st.column_config.NumberColumn("Salida", min_value=0.0, format="localized"),
                "moneda": st.column_config.SelectboxColumn("Moneda", options=MONEDAS, required=True, width="small"),
                "rubro": st.column_config.SelectboxColumn("Rubro", options=RUBROS, required=True),
                "quien": st.column_config.TextColumn("Quién"),
                "medio": st.column_config.SelectboxColumn("Medio", options=["Efectivo", "Transferencia", "Tarjeta",
                                                                             "Cheque", "Otro", ""]),
                "observaciones": st.column_config.TextColumn("Observaciones"),
                "caja": st.column_config.SelectboxColumn("Caja", options=cajas, required=True),
                "frasco": st.column_config.TextColumn("Frasco", help="Vacío = disponible"),
            },
            key=f"{k}editor_{mes}_{rev()}_{vista}",
        )
        borrar = [i for i, v in editada["borrar"].items() if bool(v)]
        b1, b2 = form_ed.columns(2)
        with b1:
            ok = st.form_submit_button("Guardar cambios", type="primary", icon=":material/save:", use_container_width=True)
        with b2:
            if st.form_submit_button("Descartar cambios", use_container_width=True):
                st.session_state[rev_key] = rev() + 1
                st.rerun()
        if ok:
            nuevo, n = aplicar_edicion(raw, original, editada.drop(columns=["borrar"]), borrar)
            if n == 0:
                st.info("No hay cambios para guardar.")
            else:
                _guardar(nuevo, f"{n} movimiento(s) actualizados")
        return

    # ---- Historial ---------------------------------------------------------
    if seccion == "Historial":
        for moneda in MONEDAS:
            lb = libro_total(d, moneda, cajas) if es_total else libro(dv, moneda)
            if lb.empty:
                if moneda == "ARS":
                    st.info("Todavía no hay movimientos.")
                continue
            din = lambda v, m=moneda: dinero(v, m)  # noqa: E731
            st.markdown(f"**{(vista + ' · ') if multi else ''}{'Caja en pesos' if moneda == 'ARS' else 'Caja en dólares'}**")
            tabla_h = pd.DataFrame({
                "Mes": lb["mes"].map(nombre_mes),
                "Saldo inicial": lb["inicial"].map(din),
                "Entradas": lb["entradas"].map(din),
                "Salidas": lb["salidas"].map(din),
                "Saldo": lb["saldo"].map(din),
                "Movimientos": lb["movimientos"],
                "Control": [("✓" if abs(x) < 0.01 else f"Saldo inicial cargado difiere en {din(x)}")
                            for x in lb["diferencia"]],
            }).iloc[::-1]
            st.dataframe(tabla_h, hide_index=True, use_container_width=True,
                         height=min(60 + 35 * len(tabla_h), 520))
        if not d.empty:
            todos = [m for m in lista if m != SIN_FECHA]
            st.download_button("Descargar todos los meses en Excel (como la planilla)",
                               data=excel_caja(dv, todos, nombre,
                                               [(c, de_caja(d, c)) for c in cajas] if es_total else
                                               [(vista if multi else nombre, dv)]),
                               file_name=f"{tabla}_completa.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               key=f"{k}xls_todo", icon=":material/download:")
        return

    # ---- Importar ----------------------------------------------------------
    if seccion == "Importar":
        if multi:
            st.caption(":material/info: La planilla que se importa es la de **Efectivo**: Mercado Pago, Medicación y "
                       "los frascos no se tocan.")
        st.caption("Subí la planilla de caja (mejor .xlsx, que conserva los colores). Se reconocen los bloques de cada "
                   "mes: FECHA, DESCRIPCION, ENTRADA, SALIDA y la columna de quién, el SALDO INICIAL, el SALDO y la "
                   "caja en dólares. El color verde o salmón define Alquileres o Caja gastos comunes.")
        archivo = st.file_uploader("Planilla", type=["xlsx", "csv"], key=f"{k}archivo", label_visibility="collapsed")
        if archivo is None:
            return
        contenido = archivo.getvalue()
        try:
            leido, hojas = leer_archivo(archivo.name, contenido)
            if len(hojas) > 1:
                hoja = st.selectbox("Hoja", hojas, key=f"{k}hoja")
                leido, _ = leer_archivo(archivo.name, contenido, hoja)
        except Exception as exc:
            _error("No pude leer el archivo.", exc)
            return
        mov = leido["movimientos"]
        if mov.empty:
            st.warning("No encontré movimientos (filas con fecha y descripción debajo de FECHA / DESCRIPCION).")
            return
        detectados = sorted(set(mov["mes"]), reverse=True)
        sel = st.multiselect("Meses a importar", detectados, default=detectados[:1], format_func=nombre_mes,
                             key=f"{k}imp_meses")
        if not sel:
            return
        existentes = [m for m in sel if m in lista]
        reemplazar = True
        if existentes:
            reemplazar = st.toggle(f"Reemplazar lo que ya hay en {', '.join(nombre_mes(m) for m in existentes)}",
                                   value=True, key=f"{k}imp_reemplazar",
                                   help="Si lo apagás, los movimientos se agregan a los que ya están (pueden quedar duplicados).")
        nuevo, control = plan_importacion(raw, leido, sel, reemplazar, dolar_por_texto=dolar_txt)
        difiere = (not control.empty) and any(
            c["inicial_planilla"] is not None and not pd.isna(c["inicial_planilla"])
            and abs(c["inicial_planilla"] - c["inicial_app"]) > 0.009 for _, c in control.iterrows())
        if difiere and st.toggle("Usar el saldo inicial de la planilla donde no coincide", value=True,
                                 key=f"{k}imp_inicial",
                                 help="Se registra como fila SALDO INICIAL de ese mes. Si lo apagás, el saldo inicial "
                                      "se calcula con el cierre del mes anterior que ya tiene la app."):
            nuevo, control = plan_importacion(raw, leido, sel, reemplazar, dolar_por_texto=dolar_txt,
                                              usar_inicial_planilla=True)
        if not control.empty:
            st.markdown("**Control contra la planilla**")
            vista_c = []
            for _, c in control.iterrows():
                din = lambda v, m=c["moneda"]: dinero(v, m)  # noqa: E731
                ok_i = c["inicial_planilla"] is None or pd.isna(c["inicial_planilla"]) or abs(c["inicial_planilla"] - c["inicial_app"]) < 0.01
                ok_s = c["saldo_planilla"] is None or pd.isna(c["saldo_planilla"]) or abs(c["saldo_planilla"] - c["saldo_app"]) < 0.01
                vista_c.append({
                    "Mes": nombre_mes(c["mes"]), "Caja": "Pesos" if c["moneda"] == "ARS" else "Dólares",
                    "Movimientos": c["movimientos"], "Saldo inicial": din(c["inicial_app"]),
                    "Entradas": din(c["entradas"]), "Salidas": din(c["salidas"]), "Saldo": din(c["saldo_app"]),
                    "Coincide con la planilla": "✓" if (ok_i and ok_s) else (
                        ("Saldo inicial: planilla " + din(c["inicial_planilla"]) if not ok_i else "")
                        + (" · " if not ok_i and not ok_s else "")
                        + ("Saldo: planilla " + din(c["saldo_planilla"]) if not ok_s else "")),
                })
            st.dataframe(pd.DataFrame(vista_c), hide_index=True, use_container_width=True)
        dn = preparar(nuevo, dolar_por_texto=dolar_txt)
        with st.expander(f"Vista previa · {nombre_mes(sel[0])}", expanded=True, icon=":material/visibility:"):
            st.markdown(planilla_html(dn, sel[0], "ARS", titulo="Caja en pesos"), unsafe_allow_html=True)
        if st.button(f"Importar {len(sel)} mes(es)", type="primary", key=f"{k}imp_ok", icon=":material/upload_file:"):
            _guardar(nuevo, "Planilla importada", sel[0])
        return
