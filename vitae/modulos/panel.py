"""Panel general: los datos importantes de cada módulo, en una sola pantalla.

Regla de oro: cada número sale de la MISMA cuenta que hace su módulo
(Facturación, Cajas, Pagos pendientes, Honorarios, Deudas impositivas,
Deuda total, Gine Vitae...), para que el Panel y el módulo nunca digan cosas
distintas. No se suman montos de módulos distintos salvo donde es la misma
cosa (efectivo de cajas y bancos; facturación de VMR y VM), y los dólares
nunca se suman a los pesos.

Saldos y deudas se muestran a hoy; facturación y operación, del mes elegido.
"""
from __future__ import annotations

import html
import io
import re
from typing import Any, Callable

import pandas as pd
import streamlit as st

from vitae.modulos import caja as cj
from vitae.modulos import cuenta_corriente as ccm
from vitae.modulos import gine_formularios as gf
from vitae.modulos import pagos_pendientes as pp
from vitae.modulos.pagos_pendientes import _sin_acentos, _texto, nombre_mes, numero, pesos

try:
    # Se llama siempre a través del módulo (fx.<función>): la app ajusta
    # _prepare_facturacion al arrancar (fecha del mes igual que el editor) y el
    # Panel tiene que usar exactamente la misma versión que la pantalla.
    import vitae.modulos.facturacion as fx
except Exception:  # pragma: no cover
    fx = None

try:
    from vitae.modulos import quirofano as qx
except Exception:  # pragma: no cover
    qx = None

try:
    from vitae.core.helpers import leer_fechas_ar
except Exception:  # pragma: no cover
    def leer_fechas_ar(valores):
        return pd.to_datetime(pd.Series(valores), dayfirst=True, errors="coerce")

try:
    from vitae.core.ui import tarjetas_kpi
except Exception:  # pragma: no cover
    def tarjetas_kpi(tarjetas):
        for col, (t, v, p, _) in zip(st.columns(len(tarjetas)), tarjetas):
            col.metric(t, v, p)


BORDO, VERDE, AMBAR, ROJO, AZUL, GRIS = "#8C2853", "#2F7D4F", "#C58A32", "#B14E5E", "#2F6F8F", "#8A7F85"

TABLAS = {
    "caja_vmr": "Caja VMR", "banco_macro_vmr": "Banco Macro VMR", "caja_vm": "Caja VM",
    "banco_galicia_vm": "Banco Galicia VM", "facturacion_vmr": "Facturación VMR", "facturacion_vm": "Facturación VM",
    "pagos_pendientes_vitae": "Pagos pendientes Vitae", "honorarios_medicos": "Honorarios médicos",
    "deudas_impositivas_vmr": "Deudas Impositivas VMR", "deudas_impositivas_vm": "Deudas Impositivas VM",
    "planes_pagos_prestamos": "Planes de pagos y préstamos", "cuenta_corriente_vmr": "Cuenta Corriente VMR",
    "cuenta_corriente_vm": "Cuenta Corriente VM", "deuda_total_manual": "Deuda total",
    "gastos_comunes": "Gastos comunes", "agenda_quirofano": "Agenda Quirófano", "gine_vitae": "Gine Vitae",
    gf.TABLA: "Gine Vitae · Formularios", "tareas_pendientes": "Tareas Pendientes", "vencimientos": "Vencimientos",
    "contratos": "Contratos", "valores_alquileres": "Valores Alquileres",
}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _col(df: pd.DataFrame, *nombres: str) -> str | None:
    mapa = {re.sub(r"[^a-z0-9]+", "_", _sin_acentos(c)).strip("_"): c for c in df.columns}
    for n in nombres:
        c = mapa.get(re.sub(r"[^a-z0-9]+", "_", _sin_acentos(n)).strip("_"))
        if c is not None:
            return c
    return None


def _num(df: pd.DataFrame, *nombres: str) -> pd.Series:
    c = _col(df, *nombres)
    if c is None:
        return pd.Series(0.0, index=df.index)
    return pd.Series([numero(v) or 0.0 for v in df[c]], index=df.index, dtype=float)


def _txt(df: pd.DataFrame, *nombres: str) -> pd.Series:
    c = _col(df, *nombres)
    if c is None:
        return pd.Series("", index=df.index, dtype="object")
    return df[c].map(_texto)


def _fecha(df: pd.DataFrame, *nombres: str) -> pd.Series:
    c = _col(df, *nombres)
    if c is None:
        return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    return pd.Series(pd.to_datetime(leer_fechas_ar(df[c].map(_texto)).values, errors="coerce"), index=df.index)


def _vacio(df: Any) -> bool:
    return not isinstance(df, pd.DataFrame) or df.empty


def _mes_rango(mes: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    p = pd.Period(mes, freq="M")
    return p.start_time.normalize(), p.end_time.normalize()


def _seguro(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except Exception as exc:  # un módulo con datos raros no tira abajo el Panel
        return {"error": f"{type(exc).__name__}: {exc}"[:160]}


# ---------------------------------------------------------------------------
# Cálculos (cada uno igual que su módulo)
# ---------------------------------------------------------------------------
def disponible(dfs: dict[str, pd.DataFrame], mes: str, hoy: pd.Timestamp) -> dict[str, Any]:
    """Cajas y bancos: el SALDO de la planilla del mes, exactamente como lo muestra cada módulo
    (cj.saldo_planilla). El mes actual incluye lo ya cargado con fecha posterior a hoy; los
    movimientos sin fecha no se suman (el módulo los marca para corregir)."""
    actual = mes >= str(pd.Period(hoy, freq="M"))
    mes_calc = str(pd.Period(hoy, freq="M")) if actual else mes
    filas = []
    for tabla in ("caja_vmr", "banco_macro_vmr", "caja_vm", "banco_galicia_vm"):
        df = dfs.get(tabla)
        dolar_txt = tabla == "caja_vmr"
        fila = {"tabla": tabla, "nombre": TABLAS[tabla], "ars": 0.0, "usd": 0.0, "hay": not _vacio(df), "sin_fecha": 0}
        if not _vacio(df):
            d = cj.preparar(df, dolar_por_texto=dolar_txt)
            internas = cj.cajas_internas(tabla) if tabla.startswith("caja_") else []
            fila["ars"] = cj.saldo_planilla(d, mes_calc, "ARS")
            fila["usd"] = cj.saldo_planilla(d, mes_calc, "USD")
            fila["sin_fecha"] = cj.sin_fecha(d)
            if len(internas) > 1:
                fila["internas"] = {c: cj.saldo_planilla(cj.de_caja(d, c), mes_calc, "ARS") for c in internas}
        filas.append(fila)
    return {"filas": filas, "ars": round(sum(f["ars"] for f in filas), 2), "usd": round(sum(f["usd"] for f in filas), 2),
            "al": f"planilla de {nombre_mes(mes_calc).lower()}" if actual else f"cierre de {nombre_mes(mes).lower()}"}


def facturacion(df: pd.DataFrame | None, mes: str) -> dict[str, Any]:
    """Mismo criterio que la pantalla de Facturación (período por fecha base)."""
    if _vacio(df) or fx is None:
        return {"hay": False}
    data, _ = fx._prepare_facturacion(df)
    data = fx._ajustar_estado_manual(data)
    desde, hasta = _mes_rango(mes)
    del_mes = data[data["_fecha_base"].between(desde, hasta + pd.Timedelta(hours=23, minutes=59, seconds=59))]
    m = fx._metrics(del_mes) if not del_mes.empty else {"facturado": 0.0, "cobrado": 0.0, "pendiente": 0.0,
                                                    "vencido": 0.0, "tasa": 0.0, "registros": 0}
    total = fx._metrics(data)
    activos = data[~data["_cancelado"]]
    venc = activos[activos["_vencido"]]
    por_os = (venc.groupby("_obra_social")["_pendiente"].sum().sort_values(ascending=False)
              if not venc.empty else pd.Series(dtype=float))
    return {
        "hay": True, "facturado": m["facturado"], "cobrado": m["cobrado"], "pendiente": m["pendiente"],
        "tasa": m["tasa"], "registros": int(m["registros"]),
        "a_cobrar": total["pendiente"], "vencido": total["vencido"], "facturas_vencidas": int(len(venc)),
        "os_vencido": [(str(k), float(v)) for k, v in por_os.head(3).items()],
        "sin_importe": int((activos["_estado_ejecutivo"] == "Sin importe").sum()),
    }


def pagos_pendientes(df: pd.DataFrame | None) -> dict[str, Any]:
    """Saldo de la planilla más reciente (cada mes repite lo impago)."""
    d = pp.normalizar(df)
    meses = [m for m in pp.meses_disponibles(d) if m != "Sin mes"]
    if not meses:
        return {"hay": False}
    t = pp.totales(pp.del_mes(d, meses[0]))
    return {"hay": True, "mes": meses[0], "saldo": t["saldo"], "total": t["importe"], "pagado": t["pagado"],
            "con_saldo": t["con_saldo"], "sin_asignar": t["sin_asignar"], "n_sin_asignar": t["n_sin_asignar"],
            "avance": t["avance"]}


def honorarios(df: pd.DataFrame | None, preparar: Callable | None) -> dict[str, Any]:
    if _vacio(df) or preparar is None:
        return {"hay": False}
    data, _ = preparar(df)
    if data is None or data.empty:
        return {"hay": False}
    return {"hay": True, "pendiente": float(data["_saldo"].sum()),
            "atrasado": float(data.loc[data["_atrasado"], "_saldo"].sum()),
            "medicos": int(data.loc[data["_saldo"] > 0, "_medico"].nunique())}


def impuestos(df: pd.DataFrame | None, tabla: str, preparar: Callable | None, hoy: pd.Timestamp) -> dict[str, Any]:
    """Igual que el Centro Fiscal (deuda pendiente, vencida y próximos 30 días)."""
    if _vacio(df) or preparar is None:
        return {"hay": False}
    data = preparar(df, tabla)
    if data is None or data.empty:
        return {"hay": False}
    due = pd.to_datetime(data["fecha_vencimiento"], errors="coerce")
    saldo = pd.to_numeric(data["saldo"], errors="coerce").fillna(0.0)
    return {"hay": True, "saldo": float(saldo.sum()),
            "vencido": float(saldo[due.notna() & (due.dt.normalize() < hoy) & (saldo > 0)].sum()),
            "prox_30": float(saldo[due.notna() & due.dt.normalize().between(hoy, hoy + pd.Timedelta(days=30))
                                   & (saldo > 0)].sum())}


def planes(df: pd.DataFrame | None, hoy: pd.Timestamp) -> dict[str, Any]:
    """Mismo cálculo que el módulo Planes de pagos y préstamos (lo que falta pagar)."""
    from vitae.modulos import planes as pl

    if _vacio(df):
        return {"hay": False}
    r = pl.resumen(pl.normalizar(df), hoy.date())
    if not r["hay"]:
        return {"hay": False}
    return {"hay": True, "saldo": r["pendiente"], "activos": r["activos"], "mora": r["con_atraso"],
            "atrasado": r["atrasado"], "cuota_mes": r["cuota_mes"], "cuota_mes_pagada": r["cuota_mes_pagada"],
            "prox_7": 0}


def cuenta_corriente(df: pd.DataFrame | None, hoy: pd.Timestamp) -> dict[str, Any]:
    """Saldos de proveedores con el mismo cálculo del módulo Cuenta corriente
    (pesos y dólares por separado; lee también el formato anterior)."""
    if _vacio(df):
        return {"hay": False}
    t = ccm.totales(ccm.normalizar(df), hoy)
    return {"hay": True, "a_pagar": t["ARS"], "a_pagar_usd": t["USD"], "a_cobrar": t["ARS_cobrar"],
            "pagar_vencido": t["ARS_vencido"], "facturas": t["ARS_n"] + t["USD_n"]}


def deuda_total(df: pd.DataFrame | None, enriquecer: Callable | None) -> dict[str, Any]:
    """Último corte del módulo Deuda total (deuda consolidada ARS y USD)."""
    if _vacio(df) or enriquecer is None:
        return {"hay": False}
    data = enriquecer(df)
    if data is None or data.empty:
        return {"hay": False}
    ultimo = pd.to_datetime(data["_periodo"], errors="coerce").max()
    snap = data[data["_periodo"].eq(ultimo)]
    act = snap[(snap["_saldo_ars"] > 0.01) | (snap["_saldo_usd"] > 0.01)]
    return {"hay": True, "ars": float(act["_saldo_ars"].sum()), "usd": float(act["_saldo_usd"].sum()),
            "corte": nombre_mes(str(pd.Period(ultimo, freq="M"))) if pd.notna(ultimo) else "sin fecha",
            "vencido": float(act.loc[act["_situacion"].eq("Vencido"), "_saldo_ars"].sum())}


def gastos_comunes(df: pd.DataFrame | None, mes: str) -> dict[str, Any]:
    if _vacio(df):
        return {"hay": False}
    f = _fecha(df, "fecha")
    desde, hasta = _mes_rango(mes)
    en_mes = f.between(desde, hasta)
    imp = _num(df, "importe")
    pagado = _txt(df, "pagado").map(_sin_acentos).isin({"si", "true", "1", "pagado", "x", "yes"})
    return {"hay": True, "mes": float(imp[en_mes].sum()), "impago": float(imp[en_mes & ~pagado].sum()),
            "n": int(en_mes.sum())}


def quirofano(df: pd.DataFrame | None, mes: str, hoy: pd.Timestamp) -> dict[str, Any]:
    if _vacio(df) or qx is None:
        return {"hay": False}
    _, d, _ = qx._preparar_dataframe(df)
    d = d[~d["_es_vacio"]]
    f = pd.to_datetime(d["_fecha_dt"], errors="coerce").dt.normalize()
    ocupa = ~d["estado"].isin(qx.ESTADOS_NO_OCUPAN)
    desde, hasta = _mes_rango(mes)
    proximos = ocupa & f.between(hoy, hoy + pd.Timedelta(days=7))
    return {"hay": True, "hoy": int((ocupa & f.eq(hoy)).sum()), "prox_7": int(proximos.sum()),
            "realizadas_mes": int((d["estado"].eq("Realizado") & f.between(desde, hasta)).sum()),
            "programadas_mes": int((ocupa & f.between(desde, hasta)).sum()),
            "sin_autorizar_7": int((proximos & d["estado"].eq("Pendiente de autorización")).sum())}


def gine(df: pd.DataFrame | None, mes: str, cargar: Callable | None) -> dict[str, Any]:
    """Igual que Gine Vitae → Resumen, para el mes."""
    if cargar is None:
        return {"hay": False}
    data = cargar(df if isinstance(df, pd.DataFrame) else pd.DataFrame(), "gine_vitae")["data"]
    if data is None or data.empty:
        return {"hay": False}
    v = data[data["_mes"].eq(mes)]
    return {"hay": True, "pacientes": int(len(v)), "recaudado": float(v["_total"].sum()),
            "por_autorizar": int((~v["_autorizado"] & ~v["_ausente"]).sum()),
            "por_entregar": int((v["_autorizado"] & ~v["_entregado"] & ~v["_ausente"]).sum()),
            "por_facturar": int((v["_entregado"] & ~v["_factura"] & ~v["_ausente"]).sum())}


def formularios(df: pd.DataFrame | None, mes: str) -> dict[str, Any]:
    if _vacio(df):
        return {"hay": False}
    d = gf.preparar(df)
    return {"hay": True, "mes": int((d["_mes"] == mes).sum()), "sin_contactar": int((d["estado"] == "Nuevo").sum()),
            "total": int(len(d))}


def tareas(df: pd.DataFrame | None, hoy: pd.Timestamp) -> dict[str, Any]:
    if _vacio(df):
        return {"hay": False}
    estado = _txt(df, "estado").map(_sin_acentos)
    abierta = ~estado.isin({"finalizada", "finalizado", "cancelada", "cancelado", "realizada", "realizado", "cerrada"})
    v = _fecha(df, "vencimiento", "fecha_limite")
    alta = _txt(df, "prioridad").map(_sin_acentos).isin({"alta", "urgente"})
    return {"hay": True, "abiertas": int(abierta.sum()), "vencidas": int((abierta & v.notna() & v.lt(hoy)).sum()),
            "prox_7": int((abierta & v.between(hoy, hoy + pd.Timedelta(days=7))).sum()),
            "alta": int((abierta & alta).sum())}


def vencimientos(df: pd.DataFrame | None, hoy: pd.Timestamp) -> dict[str, Any]:
    if _vacio(df):
        return {"hay": False}
    estado = _txt(df, "estado").map(_sin_acentos)
    pendiente = ~estado.isin({"realizado", "realizada", "pagado", "pagada", "cancelado", "cancelada"})
    v = _fecha(df, "vencimiento", "fecha")
    imp = _num(df, "importe")
    vencido = pendiente & v.notna() & v.lt(hoy)
    prox = pendiente & v.between(hoy, hoy + pd.Timedelta(days=30))
    return {"hay": True, "vencidos": int(vencido.sum()), "vencidos_importe": float(imp[vencido].sum()),
            "prox_30": int(prox.sum()), "prox_30_importe": float(imp[prox].sum()),
            "prox_7": int((pendiente & v.between(hoy, hoy + pd.Timedelta(days=7))).sum())}


def alquileres(df: pd.DataFrame | None, hoy: pd.Timestamp) -> dict[str, Any]:
    """Mismo cálculo que el módulo Valores Alquileres (alquileres de consultorios)."""
    from vitae.modulos import alquileres as alq

    if _vacio(df):
        return {"hay": False}
    r = alq.resumen(alq.normalizar(df), hoy.date())
    if not r.get("hay"):
        return {"hay": False}
    return {"hay": True, "mes": r["mes"], "a_cobrar": r["a_cobrar"], "cobrado": r["cobrado"], "servicio": r["servicio"],
            "pendiente": r["pendiente"], "por_cobrar": r["por_cobrar_mes"], "adeudado": r["adeudado"],
            "deudores": len(r["deudores"]), "falta_mes": r["falta_mes"], "proximo": r["proximo"]}


def contratos(df_c: pd.DataFrame | None, df_a: pd.DataFrame | None, hoy: pd.Timestamp) -> dict[str, Any]:
    out: dict[str, Any] = {"hay": not (_vacio(df_c) and _vacio(df_a)), "vencen_60": 0, "vencidos": 0, "aumentos_30": 0}
    if not _vacio(df_c):
        estado = _txt(df_c, "estado").map(_sin_acentos)
        activo = ~estado.isin({"finalizado", "finalizada"})
        fin = _fecha(df_c, "fin", "fecha_fin", "vencimiento", "hasta")
        out["vencen_60"] = int((activo & fin.between(hoy, hoy + pd.Timedelta(days=60))).sum())
        out["vencidos"] = int((activo & estado.isin({"vigente", ""}) & fin.notna() & fin.lt(hoy)).sum())
    if not _vacio(df_a):
        estado = _txt(df_a, "estado").map(_sin_acentos)
        aum = _fecha(df_a, "proximo_aumento")
        out["aumentos_30"] = int((~estado.isin({"finalizado"}) & aum.between(hoy, hoy + pd.Timedelta(days=30))).sum())
    return out


def calcular(dfs: dict[str, pd.DataFrame], mes: str, hoy: pd.Timestamp | None = None,
             preparadores: dict[str, Callable] | None = None) -> dict[str, Any]:
    hoy = (hoy or pd.Timestamp.today()).normalize()
    p = preparadores or {}
    g = dfs.get
    m: dict[str, Any] = {"mes": mes, "hoy": hoy}
    m["disponible"] = _seguro(lambda: disponible(dfs, mes, hoy))
    m["fact"] = {e: _seguro(lambda t=t: facturacion(g(t), mes)) for e, t in (("VMR", "facturacion_vmr"), ("VM", "facturacion_vm"))}
    # Control de facturación (mismo cálculo que la sección Control de cada Facturación). Cada cirugía
    # realizada sin cargar va a la empresa de su procedimiento (VMR sólo los de VMR; el resto, VM).
    from vitae.modulos import control_facturacion as cf

    m["control_fact"] = {
        "VMR": _seguro(lambda: cf.resumen(g("facturacion_vmr"), g("agenda_quirofano"), [g("facturacion_vm")],
                                          hoy.date(), empresa="VMR")),
        "VM": _seguro(lambda: cf.resumen(g("facturacion_vm"), g("agenda_quirofano"), [g("facturacion_vmr")],
                                         hoy.date(), empresa="VM")),
    }
    m["pagos"] = _seguro(lambda: pagos_pendientes(g("pagos_pendientes_vitae")))
    m["honorarios"] = _seguro(lambda: honorarios(g("honorarios_medicos"), p.get("honorarios")))
    m["impuestos"] = {e: _seguro(lambda t=t: impuestos(g(t), t, p.get("impuestos"), hoy))
                      for e, t in (("VMR", "deudas_impositivas_vmr"), ("VM", "deudas_impositivas_vm"))}
    m["planes"] = _seguro(lambda: planes(g("planes_pagos_prestamos"), hoy))
    m["cc"] = {e: _seguro(lambda t=t: cuenta_corriente(g(t), hoy))
               for e, t in (("VMR", "cuenta_corriente_vmr"), ("VM", "cuenta_corriente_vm"))}
    m["deuda_total"] = _seguro(lambda: deuda_total(g("deuda_total_manual"), p.get("deuda_total")))
    m["gastos"] = _seguro(lambda: gastos_comunes(g("gastos_comunes"), mes))
    m["quirofano"] = _seguro(lambda: quirofano(g("agenda_quirofano"), mes, hoy))
    m["gine"] = _seguro(lambda: gine(g("gine_vitae"), mes, p.get("gine")))
    m["formularios"] = _seguro(lambda: formularios(g(gf.TABLA), mes))
    m["tareas"] = _seguro(lambda: tareas(g("tareas_pendientes"), hoy))
    m["vencimientos"] = _seguro(lambda: vencimientos(g("vencimientos"), hoy))
    m["contratos"] = _seguro(lambda: contratos(g("contratos"), g("valores_alquileres"), hoy))
    m["alquileres"] = _seguro(lambda: alquileres(g("valores_alquileres"), hoy))
    m["alertas"] = alertas(m)
    return m


def alertas(m: dict[str, Any]) -> list[dict[str, str]]:
    """Lo que requiere atención, de más a menos urgente."""
    out: list[dict[str, str]] = []

    def add(nivel: str, titulo: str, detalle: str, modulo: str) -> None:
        out.append({"nivel": nivel, "titulo": titulo, "detalle": detalle, "modulo": modulo})

    for f in m["disponible"].get("filas", []):
        if f["hay"] and f["ars"] < -0.5:
            add("alta", f"{f['nombre']} en negativo", f"Saldo {pesos(f['ars'])}", f["nombre"])
    q = m["quirofano"]
    if q.get("sin_autorizar_7"):
        add("alta", "Cirugías sin autorización", f"{q['sin_autorizar_7']} en los próximos 7 días", "Agenda Quirófano")
    for e, f in m["fact"].items():
        if f.get("vencido", 0) > 0.5:
            os_txt = ", ".join(n for n, _ in f.get("os_vencido", [])[:2])
            add("alta", f"Facturación {e}: cobros vencidos",
                f"{pesos(f['vencido'], 0)} en {f['facturas_vencidas']} facturas" + (f" · {os_txt}" if os_txt else ""),
                f"Facturación {e}")
    for e, cfe in m.get("control_fact", {}).items():
        if cfe.get("agenda_sin_cargar"):
            add("alta", f"Facturación {e}: cirugías realizadas sin cargar",
                f"{cfe['agenda_sin_cargar']} en la Agenda de Quirófano", f"Facturación {e}")
        if cfe.get("sin_facturar_vencido"):
            add("alta", f"Facturación {e}: sin facturar con el plazo de 60 días vencido",
                f"{cfe['sin_facturar_vencido']} registro(s)", f"Facturación {e}")
        if cfe.get("debitos", 0) > 0.5:
            add("media", f"Facturación {e}: débitos para reclamar", pesos(cfe["debitos"], 0), f"Facturación {e}")
        if cfe.get("esta_semana"):
            add("media", f"Facturación {e}: para hacer esta semana",
                f"{cfe['esta_semana']} (plazos y facturas por vencer)", f"Facturación {e}")
    for e, i in m["impuestos"].items():
        if i.get("vencido", 0) > 0.5:
            add("alta", f"Impuestos vencidos {e}", pesos(i["vencido"], 0), f"Deudas Impositivas {e}")
    v = m["vencimientos"]
    if v.get("vencidos"):
        add("alta", "Vencimientos pasados sin resolver",
            f"{v['vencidos']}" + (f" · {pesos(v['vencidos_importe'], 0)}" if v.get("vencidos_importe") else ""), "Vencimientos")
    pl = m["planes"]
    if pl.get("mora"):
        add("alta", "Planes de pago con cuotas impagas o atrasadas",
            f"{pl['mora']} plan(es) · {pesos(pl.get('atrasado', 0.0), 0)}", "Planes de pagos y préstamos")
    aq = m.get("alquileres", {})
    if aq.get("adeudado", 0) > 0.5:
        add("media", "Alquileres: médicos que deben meses anteriores",
            f"{aq['deudores']} médico(s) · {pesos(aq['adeudado'], 0)}", "Valores Alquileres")
    if aq.get("falta_mes"):
        add("baja", f"Alquileres: falta cargar {nombre_mes(aq['proximo']).lower()}", "Usá «Nuevo mes»",
            "Valores Alquileres")
    for e, c in m["cc"].items():
        if c.get("pagar_vencido", 0) > 0.5:
            add("media", f"Cuenta corriente {e}: pagos vencidos", pesos(c["pagar_vencido"], 0), f"Cuenta Corriente {e}")
    h = m["honorarios"]
    if h.get("atrasado", 0) > 0.5:
        add("media", "Honorarios con más de 30 días", pesos(h["atrasado"], 0), "Honorarios médicos")
    t = m["tareas"]
    if t.get("vencidas"):
        add("media", "Tareas vencidas", f"{t['vencidas']} abiertas con fecha pasada", "Tareas Pendientes")
    pg = m["pagos"]
    if pg.get("n_sin_asignar"):
        add("media", "Pagos pendientes sin caja asignada",
            f"{pesos(pg['sin_asignar'], 0)} en {pg['n_sin_asignar']} conceptos", "Pagos pendientes Vitae")
    if v.get("prox_7"):
        add("media", "Vencimientos esta semana", f"{v['prox_7']} en los próximos 7 días", "Vencimientos")
    c = m["contratos"]
    if c.get("vencidos"):
        add("media", "Contratos vencidos marcados como vigentes", f"{c['vencidos']}", "Contratos")
    fo = m["formularios"]
    if fo.get("sin_contactar"):
        add("baja", "Inscripciones sin contactar", f"{fo['sin_contactar']} en Gine Vitae · Formularios", "Gine Vitae")
    if c.get("vencen_60"):
        add("baja", "Contratos por vencer", f"{c['vencen_60']} en los próximos 60 días", "Contratos")
    orden = {"alta": 0, "media": 1, "baja": 2}
    return sorted(out, key=lambda a: orden.get(a["nivel"], 3))  # estable: dentro de cada nivel, mismo orden


# ---------------------------------------------------------------------------
# Vista
# ---------------------------------------------------------------------------
_CSS = """
<style>
.pg-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:.4rem}
.pg-card{border:1px solid #E8E3E6;border-radius:14px;background:#fff;padding:14px 16px 10px;min-width:0}
.pg-card h4{margin:0 0 .15rem;font-size:.95rem;font-weight:650;color:#1F1A1D;display:flex;align-items:center;gap:.45rem}
.pg-card h4 i{display:inline-block;width:9px;height:9px;border-radius:3px}
.pg-card .sub{font-size:.75rem;color:#8A7F85;margin-bottom:.45rem}
.pg-l{display:flex;justify-content:space-between;align-items:baseline;gap:.8rem;padding:.42rem 0;border-top:1px solid #F2EEF0}
.pg-l:first-of-type{border-top:none}
.pg-l .k{min-width:0;color:#3A3236;font-size:.87rem}
.pg-l .k small{display:block;color:#8A7F85;font-size:.74rem;margin-top:1px}
.pg-l .v{text-align:right;font-variant-numeric:tabular-nums;font-weight:650;color:#1F1A1D;white-space:nowrap;font-size:.92rem}
.pg-l .v small{display:block;font-weight:400;color:#8A7F85;font-size:.74rem}
.pg-l .v small.pg-mal{font-weight:600}
.pg-l.tot{border-top:1px solid #E2D8DD;margin-top:.2rem}
.pg-l.tot .k{font-weight:650}
.pg-na{color:#B9AEB4!important;font-weight:400!important}
.pg-mal{color:#B14E5E!important}.pg-bien{color:#2F7D4F!important}
.pg-tabla{width:100%;border-collapse:collapse;font-size:.86rem;font-variant-numeric:tabular-nums}
.pg-tabla th{font-size:.7rem;text-transform:uppercase;letter-spacing:.04em;color:#8A7F85;font-weight:600;text-align:right;padding:.2rem 0 .35rem}
.pg-tabla th:first-child,.pg-tabla td:first-child{text-align:left}
.pg-tabla td{padding:.38rem 0;border-top:1px solid #F2EEF0;text-align:right;white-space:nowrap}
.pg-tabla td:first-child{color:#3A3236;white-space:normal}
.pg-tabla tr.sep td{border-top:1px solid #E2D8DD}
.pg-alertas{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:.2rem 0 .3rem}
.pg-al{display:flex;gap:.6rem;align-items:flex-start;padding:.55rem .75rem;border-radius:10px;border:1px solid #EEE6EA;background:#fff}
.pg-al .d{flex:none;width:9px;height:9px;border-radius:50%;margin-top:.35rem}
.pg-al .t{font-size:.86rem;font-weight:600;color:#1F1A1D}
.pg-al .x{font-size:.78rem;color:#6E6268}
.pg-al .m{font-size:.7rem;color:#9A8F95;margin-top:1px}
.pg-ok{padding:.6rem .85rem;border-radius:10px;background:#EEF7F1;border:1px solid #CFE7D8;color:#2F6B45;font-size:.88rem}
.pg-h{font-size:.78rem;font-weight:650;letter-spacing:.06em;text-transform:uppercase;color:#5E1A39;margin:1.1rem 0 .35rem}
@media (max-width: 760px){
 .pg-grid,.pg-alertas{grid-template-columns:minmax(0,1fr)}
 .pg-card{padding:12px 13px 8px}
}
</style>
"""

_COLOR_NIVEL = {"alta": ROJO, "media": AMBAR, "baja": AZUL}


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _linea(k: str, v: str, *, sub_k: str = "", sub_v: str = "", clase_v: str = "", tot: bool = False,
           alerta_sub: bool = False) -> str:
    e = html.escape
    sk = f"<small>{e(sub_k)}</small>" if sub_k else ""
    sv = f'<small{" class=pg-mal" if alerta_sub else ""}>{e(sub_v)}</small>' if sub_v else ""
    return (f'<div class="pg-l{" tot" if tot else ""}"><div class="k">{e(k)}{sk}</div>'
            f'<div class="v {clase_v}">{e(v)}{sv}</div></div>')


def _sin_datos(k: str, bloque: dict[str, Any]) -> str:
    detalle = "No se pudo leer" if bloque.get("error") else "Sin datos"
    return _linea(k, detalle, clase_v="pg-na", sub_v=bloque.get("error", "")[:60] if bloque.get("error") else "")


def _tarjeta(titulo: str, sub: str, color: str, cuerpo: str) -> str:
    return (f'<div class="pg-card"><h4><i style="background:{color}"></i>{html.escape(titulo)}</h4>'
            f'<div class="sub">{html.escape(sub)}</div>{cuerpo}</div>')


def html_alertas(lista: list[dict[str, str]], maximo: int = 10) -> str:
    if not lista:
        return '<div class="pg-ok">✓ No hay alertas: nada vencido ni pendiente de atención inmediata.</div>'
    partes = ['<div class="pg-alertas">']
    for a in lista[:maximo]:
        partes.append(f'<div class="pg-al"><div class="d" style="background:{_COLOR_NIVEL[a["nivel"]]}"></div><div>'
                      f'<div class="t">{html.escape(a["titulo"])}</div><div class="x">{html.escape(a["detalle"])}</div>'
                      f'<div class="m">{html.escape(a["modulo"])}</div></div></div>')
    partes.append("</div>")
    return "".join(partes)


def html_tarjetas(m: dict[str, Any]) -> str:
    mes_txt = nombre_mes(m["mes"])
    tarjetas = []

    # Cajas y bancos
    d = m["disponible"]
    if d.get("error"):
        cuerpo = _sin_datos("Cajas y bancos", d)
    else:
        cuerpo = ""
        for f in d["filas"]:
            if not f["hay"]:
                cuerpo += _linea(f["nombre"], "Sin datos", clase_v="pg-na")
                continue
            detalle = " · ".join(f"{c} {pesos(v, 0)}" for c, v in f.get("internas", {}).items())
            if f.get("sin_fecha"):
                detalle = (detalle + " · " if detalle else "") + f"⚠ {f['sin_fecha']} sin fecha (no suman)"
            cuerpo += _linea(f["nombre"], pesos(f["ars"]), sub_v=(cj.dinero(f["usd"], "USD") if abs(f["usd"]) > 0.004 else ""),
                             sub_k=detalle, clase_v="pg-mal" if f["ars"] < -0.5 else "")
        cuerpo += _linea("Total en pesos", pesos(d["ars"]), tot=True,
                         sub_v=(f"+ {cj.dinero(d['usd'], 'USD')} en dólares" if abs(d["usd"]) > 0.004 else ""))
    tarjetas.append(_tarjeta("Cajas y bancos", f"Saldo de la {d.get('al', 'planilla')}" if str(d.get("al", "")).startswith("planilla")
                             else f"Saldo al {d.get('al', 'día')}", AZUL, cuerpo))

    # Facturación
    fv, fm = m["fact"].get("VMR", {}), m["fact"].get("VM", {})

    def celda(f: dict[str, Any], k: str, fmt: Callable[[float], str] = lambda x: pesos(x, 0)) -> str:
        return html.escape(fmt(f[k])) if f.get("hay") else '<span class="pg-na">—</span>'

    filas_f = [
        ("Facturado", "facturado"), ("Cobrado", "cobrado"), ("Pendiente del mes", "pendiente"),
    ]
    t = ('<table class="pg-tabla"><tr><th></th><th>VMR</th><th>VM</th></tr>'
         + "".join(f"<tr><td>{n}</td><td>{celda(fv, k)}</td><td>{celda(fm, k)}</td></tr>" for n, k in filas_f)
         + f'<tr><td>% cobrado</td><td>{celda(fv, "tasa", lambda x: f"{x:.0f}%")}</td><td>{celda(fm, "tasa", lambda x: f"{x:.0f}%")}</td></tr>'
         + f'<tr class="sep"><td>A cobrar (total)</td><td>{celda(fv, "a_cobrar")}</td><td>{celda(fm, "a_cobrar")}</td></tr>'
         + f'<tr><td>… vencido</td><td class="pg-mal">{celda(fv, "vencido")}</td><td class="pg-mal">{celda(fm, "vencido")}</td></tr>'
         + "</table>")
    aq = m.get("alquileres", {})
    if aq.get("hay"):
        t += _linea(f"Alquileres de consultorios ({nombre_mes(aq['mes']).lower()})", pesos(aq["a_cobrar"], 0),
                    sub_k=(f"WPP aparte {pesos(aq.get('servicio', 0.0), 0)} · pagaron {pesos(aq['cobrado'], 0)} · "
                           f"deben {pesos(aq['pendiente'], 0)}"),
                    sub_v=(f"{aq['deudores']} deben {pesos(aq['adeudado'], 0)} de meses anteriores"
                           if aq["adeudado"] > 0.5 else "nadie debe meses anteriores"),
                    alerta_sub=aq["adeudado"] > 0.5)
    tarjetas.append(_tarjeta("Facturación y cobranza", f"{mes_txt} · igual que Facturación VMR y VM", BORDO, t))

    # Obligaciones
    c = ""
    pg = m["pagos"]
    c += (_linea("Pagos pendientes", pesos(pg["saldo"], 0), sub_k=f"Planilla de {nombre_mes(pg['mes']).lower()}",
                 sub_v=(f"{pesos(pg['sin_asignar'], 0)} sin caja" if pg.get("sin_asignar") else "todo asignado"))
          if pg.get("hay") else _sin_datos("Pagos pendientes", pg))
    h = m["honorarios"]
    c += (_linea("Honorarios médicos", pesos(h["pendiente"], 0), sub_k=f"{h['medicos']} médicos con saldo",
                 sub_v=(f"{pesos(h['atrasado'], 0)} con +30 días" if h["atrasado"] > 0.5 else ""),
                 alerta_sub=h["atrasado"] > 0.5)
          if h.get("hay") else _sin_datos("Honorarios médicos", h))
    for e, i in m["impuestos"].items():
        c += (_linea(f"Deudas impositivas {e}", pesos(i["saldo"], 0),
                     sub_v=(f"{pesos(i['vencido'], 0)} vencido" if i["vencido"] > 0.5 else "nada vencido"),
                     alerta_sub=i["vencido"] > 0.5)
              if i.get("hay") else _sin_datos(f"Deudas impositivas {e}", i))
    pl = m["planes"]
    c += (_linea("Planes de pago y préstamos", pesos(pl["saldo"], 0), sub_k=_plural(pl["activos"], "activo"),
                 sub_v=(f"{pl['mora']} con atraso" if pl["mora"] else
                        f"Cuotas del mes {pesos(pl.get('cuota_mes', 0.0), 0)}"), alerta_sub=bool(pl["mora"]))
          if pl.get("hay") else _sin_datos("Planes de pago y préstamos", pl))
    for e, cc in m["cc"].items():
        c += (_linea(f"Cuenta corriente {e}", f"A pagar {pesos(cc['a_pagar'], 0)}",
                     sub_k=_plural(cc.get("facturas", 0), "factura") + " con saldo",
                     sub_v=" · ".join(x for x in (ccm.usd(cc['a_pagar_usd'], 0) if cc.get("a_pagar_usd") else "",
                                                   f"A cobrar {pesos(cc['a_cobrar'], 0)}" if cc.get("a_cobrar") else "") if x))
              if cc.get("hay") else _sin_datos(f"Cuenta corriente {e}", cc))
    dt = m["deuda_total"]
    c += (_linea("Deuda total (último corte)", pesos(dt["ars"], 0), sub_k=dt["corte"],
                 sub_v=(cj.dinero(dt["usd"], "USD") if dt["usd"] > 0.004 else ""))
          if dt.get("hay") else _sin_datos("Deuda total (último corte)", dt))
    gc = m["gastos"]
    if gc.get("hay"):
        c += _linea("Gastos comunes del mes", pesos(gc["mes"], 0),
                    sub_v=(f"{pesos(gc['impago'], 0)} sin pagar" if gc["impago"] > 0.5 else ""))
    tarjetas.append(_tarjeta("Lo que hay que pagar", "Saldos a hoy, cada uno como en su módulo (no se suman entre sí)",
                             ROJO, c))

    # Operación
    o = ""
    q = m["quirofano"]
    o += (_linea("Quirófano", f"{_plural(q['hoy'], 'cirugía')} hoy",
                 sub_k=f"{_plural(q['realizadas_mes'], 'realizada')} en {mes_txt.lower()}",
                 sub_v=f"{q['prox_7']} en los próximos 7 días")
          if q.get("hay") else _sin_datos("Quirófano", q))
    g = m["gine"]
    o += (_linea("Gine Vitae", _plural(g["pacientes"], "paciente"), sub_k=f"Por autorizar {g['por_autorizar']} · "
                 f"entregar {g['por_entregar']} · facturar {g['por_facturar']}",
                 sub_v=f"Recaudado {pesos(g['recaudado'], 0)}")
          if g.get("hay") else _sin_datos("Gine Vitae", g))
    fo = m["formularios"]
    if fo.get("hay"):
        o += _linea("Inscripciones web (Gine)", f"{fo['mes']} en el mes", sub_v=f"{fo['sin_contactar']} sin contactar")
    tr = m["tareas"]
    o += (_linea("Tareas pendientes", _plural(tr["abiertas"], "abierta"),
                 sub_v=(_plural(tr["vencidas"], "vencida") if tr["vencidas"] else "ninguna vencida"),
                 sub_k=(f"{tr['alta']} de prioridad alta" if tr["alta"] else ""), alerta_sub=bool(tr["vencidas"]))
          if tr.get("hay") else _sin_datos("Tareas pendientes", tr))
    v = m["vencimientos"]
    o += (_linea("Vencimientos", f"{v['prox_30']} en 30 días",
                 sub_k=(pesos(v["prox_30_importe"], 0) if v["prox_30_importe"] else ""),
                 sub_v=(f"{_plural(v['vencidos'], 'ya vencido', 'ya vencidos')}" if v["vencidos"] else "ninguno vencido"),
                 alerta_sub=bool(v["vencidos"]))
          if v.get("hay") else _sin_datos("Vencimientos", v))
    ct = m["contratos"]
    if ct.get("hay"):
        o += _linea("Contratos y alquileres", f"{ct['vencen_60']} vencen en 60 días",
                    sub_v=f"{ct['aumentos_30']} aumentos en 30 días")
    tarjetas.append(_tarjeta("Operación", f"{mes_txt} y próximos días", VERDE, o))
    return _CSS + '<div class="pg-grid">' + "".join(tarjetas) + "</div>"


def excel_resumen(m: dict[str, Any]) -> bytes:
    filas: list[tuple[str, str, Any]] = []
    d = m["disponible"]
    for f in d.get("filas", []):
        filas.append(("Cajas y bancos", f"{f['nombre']} (ARS)", round(f["ars"], 2)))
        if abs(f["usd"]) > 0.004:
            filas.append(("Cajas y bancos", f"{f['nombre']} (USD)", round(f["usd"], 2)))
    filas.append(("Cajas y bancos", "Total en pesos", round(d.get("ars", 0.0), 2)))
    for e, f in m["fact"].items():
        if f.get("hay"):
            for k, n in (("facturado", "Facturado del mes"), ("cobrado", "Cobrado del mes"),
                         ("pendiente", "Pendiente del mes"), ("a_cobrar", "A cobrar total"), ("vencido", "Vencido")):
                filas.append((f"Facturación {e}", n, round(f[k], 2)))
    if m["pagos"].get("hay"):
        filas.append(("Obligaciones", f"Pagos pendientes ({nombre_mes(m['pagos']['mes'])})", round(m["pagos"]["saldo"], 2)))
    if m["honorarios"].get("hay"):
        filas.append(("Obligaciones", "Honorarios médicos pendientes", round(m["honorarios"]["pendiente"], 2)))
    for e, i in m["impuestos"].items():
        if i.get("hay"):
            filas.append(("Obligaciones", f"Deudas impositivas {e}", round(i["saldo"], 2)))
    if m["planes"].get("hay"):
        filas.append(("Obligaciones", "Planes de pago y préstamos", round(m["planes"]["saldo"], 2)))
    for e, c in m["cc"].items():
        if c.get("hay"):
            filas.append(("Obligaciones", f"Cuenta corriente {e} a pagar", round(c["a_pagar"], 2)))
            filas.append(("Obligaciones", f"Cuenta corriente {e} a pagar (USD)", round(c.get("a_pagar_usd", 0.0), 2)))
            filas.append(("Obligaciones", f"Cuenta corriente {e} a cobrar", round(c["a_cobrar"], 2)))
    if m["deuda_total"].get("hay"):
        filas.append(("Obligaciones", f"Deuda total ARS ({m['deuda_total']['corte']})", round(m["deuda_total"]["ars"], 2)))
    aq = m.get("alquileres", {})
    if aq.get("hay"):
        filas.append(("Alquileres", f"A cobrar ({nombre_mes(aq['mes'])})", round(aq["a_cobrar"], 2)))
        filas.append(("Alquileres", "Cobrado", round(aq["cobrado"], 2)))
        filas.append(("Alquileres", "Deben meses anteriores", round(aq["adeudado"], 2)))
    for a in m["alertas"]:
        filas.append(("Alertas", a["titulo"], a["detalle"]))
    df = pd.DataFrame(filas, columns=["Sección", "Indicador", "Valor"])
    salida = io.BytesIO()
    df.to_excel(salida, index=False, sheet_name=f"Panel {m['mes']}")
    return salida.getvalue()


def render_panel(
    *,
    leer: Callable[[str], pd.DataFrame],
    preparadores: dict[str, Callable],
    encabezado: Callable[[], None] | None = None,
) -> None:
    hoy = pd.Timestamp.today().normalize()
    actual = str(pd.Period(hoy, freq="M"))
    if encabezado:
        encabezado()
    opciones = [str(pd.Period(hoy, freq="M") - i) for i in range(0, 18)]
    c1, c2 = st.columns([1.3, 1])
    with c1:
        mes = st.selectbox("Mes", opciones, key="pg_mes", format_func=nombre_mes)
    dfs: dict[str, pd.DataFrame] = {}
    for tabla in TABLAS:
        try:
            dfs[tabla] = leer(tabla)
        except Exception:
            dfs[tabla] = pd.DataFrame()
    m = calcular(dfs, mes, hoy, preparadores)
    with c2:
        st.download_button("Descargar resumen", data=excel_resumen(m), file_name=f"panel_general_{mes}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           icon=":material/download:", use_container_width=True, key="pg_xls")
    st.caption(f"Cajas y bancos: saldo de la planilla de {nombre_mes(mes).lower()} (igual que en cada módulo) · "
               f"deudas al {hoy:%d/%m/%Y}" if mes == actual else
               f"Cajas al cierre de {nombre_mes(mes).lower()} · deudas al {hoy:%d/%m/%Y}")

    d = m["disponible"]
    fv, fm = m["fact"].get("VMR", {}), m["fact"].get("VM", {})
    facturado = fv.get("facturado", 0.0) + fm.get("facturado", 0.0)
    cobrado = fv.get("cobrado", 0.0) + fm.get("cobrado", 0.0)
    a_cobrar = fv.get("a_cobrar", 0.0) + fm.get("a_cobrar", 0.0)
    vencido = fv.get("vencido", 0.0) + fm.get("vencido", 0.0)
    pg = m["pagos"]
    tarjetas_kpi([
        ("Disponible en pesos", pesos(d.get("ars", 0.0), 0),
         (f"+ {cj.dinero(d['usd'], 'USD')}" if abs(d.get("usd", 0.0)) > 0.004 else "Cajas y bancos"), AZUL),
        (f"Facturado en {nombre_mes(mes).split()[0].lower()}", pesos(facturado, 0),
         f"Cobrado {pesos(cobrado, 0)} ({(100 * cobrado / facturado) if facturado else 0:.0f}%)", BORDO),
        ("A cobrar", pesos(a_cobrar, 0), f"{pesos(vencido, 0)} vencido" if vencido > 0.5 else "Nada vencido",
         ROJO if vencido > 0.5 else VERDE),
        ("Pagos pendientes", pesos(pg.get("saldo", 0.0), 0) if pg.get("hay") else "—",
         (f"Planilla de {nombre_mes(pg['mes']).lower()}" if pg.get("hay") else "Sin planilla"), AMBAR),
    ])

    st.markdown('<div class="pg-h">Requiere atención</div>', unsafe_allow_html=True)
    st.markdown(_CSS + html_alertas(m["alertas"]), unsafe_allow_html=True)
    st.markdown('<div class="pg-h">Por área</div>', unsafe_allow_html=True)
    st.markdown(html_tarjetas(m), unsafe_allow_html=True)
    errores = [TABLAS.get(k, k) for k, v in (("disponible", m["disponible"]), ("pagos", m["pagos"])) if v.get("error")]
    if errores:
        st.caption("No se pudieron leer: " + ", ".join(errores))
