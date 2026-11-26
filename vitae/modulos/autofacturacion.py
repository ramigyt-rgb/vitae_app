"""Agenda de Quirófano → Facturación, sola.

Cuando en la Agenda una cirugía pasa a «Realizado» (o se carga directamente como realizada), se crea
su ficha en la facturación de la empresa que corresponde a su procedimiento (VMR o VM, igual que el
control): paciente, fecha, obra social, procedimiento, médico, importe sugerido (lo último de ese
procedimiento y obra social), estado Pendiente y la documentación que ya figura en la Agenda
(autorización, consentimiento firmado). Si ya está en la facturación de su empresa, no se duplica.
Regla principal: reproducción → VMR; todo lo demás → VM (``control_facturacion.empresa_procedimiento``).

Sólo las recién realizadas (las que pasan a «Realizado» al guardar): lo viejo de la Agenda no se revisa,
porque la Agenda era un respaldo. No se duplica lo que ya está en la facturación de su empresa.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable

import pandas as pd

import vitae.modulos.control_facturacion as cf
from vitae.modulos.pagos_pendientes import _sin_acentos, _texto

TABLAS = {"VMR": "facturacion_vmr", "VM": "facturacion_vm"}


def _clave(r: pd.Series) -> str:
    f = r.get("_fecha_dt")
    fecha = f.strftime("%Y-%m-%d") if isinstance(f, pd.Timestamp) and pd.notna(f) else ""
    nombre = " ".join(sorted(re.findall(r"[a-z]+", _sin_acentos(r.get("paciente", "")))))
    return f"{nombre}|{fecha}|{_sin_acentos(r.get('procedimiento', ''))}"


def recien_realizadas(antes: pd.DataFrame | None, despues: pd.DataFrame | None) -> pd.DataFrame:
    """Las filas de la Agenda que quedaron «Realizado» en este guardado (índice = fila de ``despues``)."""
    from vitae.modulos import quirofano as qx

    if despues is None or not isinstance(despues, pd.DataFrame) or despues.empty:
        return pd.DataFrame()
    _, ag_d, _ = qx._preparar_dataframe(despues)
    ag_d = ag_d[(ag_d["estado"] == "Realizado") & (ag_d["paciente"].str.strip() != "")]
    if ag_d.empty:
        return ag_d
    ya = set()
    if isinstance(antes, pd.DataFrame) and not antes.empty:
        _, ag_a, _ = qx._preparar_dataframe(antes)
        ya = {_clave(r) for _, r in ag_a[ag_a["estado"] == "Realizado"].iterrows()}
    return ag_d[[_clave(r) not in ya for _, r in ag_d.iterrows()]]


def documentos_de_agenda(r: pd.Series) -> list[str]:
    """Lo que la Agenda ya dice: autorización cargada y consentimiento firmado."""
    docs = []
    if _texto(r.get("autorizacion")) and _sin_acentos(r.get("autorizacion")) not in {"no", "pendiente", "-"}:
        docs.append("Autorización")
    if _sin_acentos(r.get("consentimiento_firmado")) in {"si", "sí", "true", "1"}:
        docs.append("Consentimiento")
    return docs


def a_facturacion(cirugias: pd.DataFrame, facturaciones: dict[str, pd.DataFrame | None], hoy: date | None = None,
                  agenda_raw: pd.DataFrame | None = None) -> dict[str, tuple[pd.DataFrame, list[str]]]:
    """Agrega las cirugías a la facturación de su empresa. Devuelve {empresa: (planilla nueva, pacientes)}
    sólo para las empresas que cambiaron."""
    from vitae.modulos import facturacion_fichas as ff

    if cirugias is None or cirugias.empty:
        return {}
    hoy_d = hoy or date.today()
    raws = {e: (df.reset_index(drop=True) if isinstance(df, pd.DataFrame) else pd.DataFrame())
            for e, df in facturaciones.items()}
    # ¿Ya está en alguna facturación? Mismo control que «cirugías sin cargar», sobre estas filas.
    base_agenda = agenda_raw.loc[cirugias.index] if isinstance(agenda_raw, pd.DataFrame) else None
    out: dict[str, tuple[pd.DataFrame, list[str]]] = {}
    for empresa, tabla in TABLAS.items():
        raw = raws.get(empresa, pd.DataFrame())
        if base_agenda is not None:
            # Sólo contra la facturación de su empresa: VMR y VM no se mezclan.
            faltan = cf.agenda_sin_facturar(base_agenda, [raw], hoy_d, empresa)
            faltan_claves = {(_texto(p), pd.Timestamp(f).strftime("%Y-%m-%d")) for p, f in zip(faltan.get("Paciente", []),
                                                                                         faltan.get("Fecha", []))}
        else:
            faltan_claves = None
        d, cols = cf.preparar(raw, hoy_d) if not raw.empty else (pd.DataFrame(), {})
        nuevo, pacientes = raw, []
        for _, r in cirugias.iterrows():
            if cf.empresa_procedimiento(r["procedimiento"]) != empresa:
                continue
            f = r["_fecha_dt"]
            if faltan_claves is not None and (_texto(r["paciente"]), f.strftime("%Y-%m-%d") if pd.notna(f) else "") \
                    not in faltan_claves:
                continue  # ya está cargada
            imp = ff.importe_sugerido(d, r["procedimiento"], r["obra_social"]) if len(d) else None
            cir = {"Fecha": f, "Paciente": r["paciente"], "Obra social": r["obra_social"],
                   "Procedimiento": r["procedimiento"], "Médico": r["medico"]}
            nuevo = cf.nueva_fila(nuevo if len(nuevo.columns) else _vacia(), cols, cir,
                                  f"{imp:.2f}" if imp else "", hoy_d,
                                  documentacion=documentos_de_agenda(r),
                                  observaciones="Cargada sola desde la Agenda de Quirófano")
            pacientes.append(_texto(r["paciente"]))
        if pacientes:
            out[empresa] = (nuevo, pacientes)
    return out


def _vacia() -> pd.DataFrame:
    return pd.DataFrame(columns=["mes", "afiliado", "obra_social", "procedimiento", "medico_responsable",
                                 "fecha_factura", "numero_factura", "vencimiento", "fecha_pago", "valor_pesos",
                                 "estado", "observaciones"])


def al_guardar_agenda(antes: pd.DataFrame | None, despues: pd.DataFrame, leer: Callable[[str], pd.DataFrame | None],
                      guardar: Callable[[str, pd.DataFrame], Any], hoy: date | None = None) -> list[str]:
    """Después de guardar la Agenda: carga en Facturación lo que se acaba de realizar. Devuelve los avisos."""
    nuevas = recien_realizadas(antes, despues)
    if nuevas.empty:
        return []
    facts = {e: leer(t) for e, t in TABLAS.items()}
    cambios = a_facturacion(nuevas, facts, hoy, agenda_raw=despues)
    avisos = []
    for empresa, (df, pacientes) in cambios.items():
        guardar(TABLAS[empresa], df)
        avisos.append(f"Cargada en Facturación {empresa}: " + ", ".join(pacientes))
    return avisos
