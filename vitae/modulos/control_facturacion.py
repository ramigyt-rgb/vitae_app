"""Control de facturación (VMR / VM): que no se pase nada.

Circuito de cada registro de Facturación (pacientes que ingresaron a quirófano):

    Cirugía realizada (Agenda de Quirófano) → Para facturar → Facturada → Por cobrar → Cobrada
                                                                                    ↘ Débito

Controles, cada uno con cantidad e importe (en cero = todo bien):
- Cirugías realizadas en la Agenda que no están en ninguna facturación (VMR ni VM). Cada una va a la
  empresa de su procedimiento (``empresa_procedimiento``): en VMR sólo las de VMR; el resto, en VM.
- Para facturar: sin N° de factura. Plazo de presentación: 60 días desde la prestación
  (``PLAZO_PRESENTACION``); avisa antes de que venza.
- Por cobrar: vencidas (30/60/90+) y por vencer en 7 días; facturas sin vencimiento.
- Débitos: lo que la obra social pagó de menos (columnas ``debito``, ``debito_estado``,
  ``debito_motivo``), y cobros parciales sin el débito anotado.
- Facturas repetidas y números salteados (por punto de venta, sólo para revisar).
- Datos incompletos: sin obra social, procedimiento, médico o importe.

La bandeja de hoy junta todo por urgencia, y cada grupo se resuelve ahí mismo (se escribe en la
misma planilla de Facturación, respetando el formato de cada columna). El cierre de mes da un
semáforo por mes y obra social. El Panel general y Luz usan ``resumen``.
"""
from __future__ import annotations

import html
import re
from datetime import date
from typing import Any, Callable

import pandas as pd

import vitae.modulos.facturacion as fx
from vitae.modulos.pagos_pendientes import _sin_acentos, _texto, numero, pesos

PLAZO_PRESENTACION = 60      # días desde la prestación para facturar/presentar (todas las obras sociales)
AVISO_PRESENTACION = 15      # avisar cuando faltan estos días
POR_VENCER = 7               # facturas que vencen en esta cantidad de días
VENTANA_AGENDA = 120         # cirugías realizadas de los últimos N días que se controlan
COLS_DEBITO = {"debito": "debito", "estado": "debito_estado", "motivo": "debito_motivo"}
ESTADOS_DEBITO = ["A reclamar", "Reclamado", "Recuperado", "Aceptado"]
URGENTE, SEMANA, HACER, REVISAR = "1 · Urgente", "2 · Esta semana", "3 · Para hacer", "4 · Revisar"


# ---------------------------------------------------------------------------
# Preparación
# ---------------------------------------------------------------------------
def _hoy(hoy: date | None) -> pd.Timestamp:
    return pd.Timestamp(hoy or date.today()).normalize()


def _col_raw(raw: pd.DataFrame, *nombres: str) -> str | None:
    mapa = {re.sub(r"[^a-z0-9]+", "_", _sin_acentos(c)).strip("_"): c for c in raw.columns}
    return next((mapa[n] for n in nombres if n in mapa), None)


def fecha_prestacion(raw: pd.DataFrame, cols: dict[str, str | None]) -> pd.Series:
    col = cols.get("mes")
    if not col or col not in raw.columns:
        return pd.Series(pd.NaT, index=raw.index)

    def una(v: Any) -> pd.Timestamp:
        if isinstance(v, (pd.Timestamp, date)) and not pd.isna(v):
            return pd.Timestamp(v)
        t = _texto(v)
        if not t:
            return pd.NaT
        if re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}.*", t):
            return pd.to_datetime(t.split(" ")[0], dayfirst=True, errors="coerce")
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}.*", t):
            return pd.to_datetime(t[:10], errors="coerce")
        m = fx._parse_month_series(pd.Series([t])).iloc[0]
        return m + pd.offsets.MonthEnd(0) if pd.notna(m) else pd.NaT

    return raw[col].map(una)


def preparar(raw: pd.DataFrame | None, hoy: date | None = None) -> tuple[pd.DataFrame, dict[str, str | None]]:
    """La planilla de Facturación con todo lo necesario para controlar (índice = fila de la planilla)."""
    if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
        return pd.DataFrame(), {}
    data, cols = fx._prepare_facturacion(raw)
    data = fx._ajustar_estado_manual(data)
    h = _hoy(hoy)
    # Fecha de la prestación (ingreso a quirófano): la de la columna «mes» con su día si lo tiene
    # (dd/mm/aaaa); si sólo dice el mes, el último día de ese mes. Una columna de fecha de servicio
    # propia, si existe (no la de factura), tiene prioridad.
    data["_prestacion"] = fecha_prestacion(raw, cols)
    servicio_propio = cols.get("fecha_servicio") and cols.get("fecha_servicio") != cols.get("fecha_factura")
    if servicio_propio:
        data["_prestacion"] = data["_fecha_servicio"].combine_first(data["_prestacion"])
    data["_plazo"] = data["_prestacion"] + pd.Timedelta(days=PLAZO_PRESENTACION)
    data["_dias_plazo"] = (data["_plazo"] - h).dt.days
    data["_facturada"] = (data["_numero_factura"].astype(str).str.strip() != "") | data["_fecha_factura"].notna()
    for clave, nombre in COLS_DEBITO.items():
        c = _col_raw(raw, nombre)
        cols[f"debito_{clave}" if clave != "debito" else "debito"] = c
    deb_c = cols.get("debito")
    data["_debito"] = raw[deb_c].map(lambda v: float(numero(v) or 0.0)) if deb_c else 0.0
    est_c = cols.get("debito_estado")
    data["_debito_estado"] = raw[est_c].map(_texto) if est_c else ""
    mot_c = cols.get("debito_motivo")
    data["_debito_motivo"] = raw[mot_c].map(_texto) if mot_c else ""
    data["_debito_abierto"] = (data["_debito"] > 0.004) & ~data["_debito_estado"].map(_sin_acentos).str.contains(
        "recuper|acept", regex=True, na=False)
    data["_parcial"] = data["_estado_norm"].str.contains("parcial", na=False)
    return data, cols


# ---------------------------------------------------------------------------
# Controles
# ---------------------------------------------------------------------------
def _tokens(nombre: Any) -> frozenset[str]:
    from vitae.modulos.pacientes import tokens_nombre

    return frozenset(tokens_nombre(nombre))


# Procedimientos propios de reproducción (VMR). Sólo se usan si el procedimiento nunca se facturó:
# lo que ya está cargado en Facturación VMR o VM manda.
_CLAVES_VMR = (
    # palabras completas (siglas)
    "fiv", "icsi", "iiu", "pgt", "pgd", "pgta", "tec", "tesa", "tese", "crio", "ovo",
    # partes de palabras
    "in vitro", "fecundacion", "fertilizacion", "puncion ovar", "puncion folic", "puncion transvag",
    "aspiracion folic", "aspiracion de ovoc", "ovocit", "ovulo", "transferencia", "ovodon", "ovulodon",
    "criopreserv", "vitrific", "desvitrif", "insemin", "embrion", "biopsia testic", "microtese", "micro tese",
    "recuperacion espermat", "espermatoz", "reproduc",
)


def _proc_norm(v: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", _sin_acentos(_texto(v)))).strip()


def historial_procedimientos(facturaciones: dict[str, pd.DataFrame | None]) -> dict[str, str]:
    """Procedimiento (normalizado) → empresa donde más se facturó. Ej.: {"VMR": raw_vmr, "VM": raw_vm}."""
    cuenta: dict[str, dict[str, int]] = {}
    for emp, df in facturaciones.items():
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        d, cols = fx._prepare_facturacion(df)
        if not cols.get("procedimiento"):
            continue
        for p in d.loc[~d["_cancelado"], "_procedimiento"]:
            n = _proc_norm(p)
            if n and n != "sin procedimiento":
                cuenta.setdefault(n, {}).setdefault(emp, 0)
                cuenta[n][emp] += 1
    return {p: max(c.items(), key=lambda kv: (kv[1], kv[0] == "VMR"))[0] for p, c in cuenta.items()}


def empresa_procedimiento(procedimiento: Any, historial: dict[str, str] | None = None) -> str:
    """De qué empresa es una cirugía: lo que ya se facturó manda; si es nuevo, VMR sólo si es de
    reproducción; el resto, VM."""
    n = _proc_norm(procedimiento)
    hist = historial or {}
    if n in hist:
        return hist[n]
    palabras = set(n.split())
    if any((k in palabras) if " " not in k and len(k) <= 5 else (k in n) for k in _CLAVES_VMR):
        return "VMR"
    return "VM"


def agenda_sin_facturar(agenda_raw: pd.DataFrame | None, facturaciones: list[pd.DataFrame],
                        hoy: date | None = None, empresa: str | None = None,
                        historial: dict[str, str] | None = None) -> pd.DataFrame:
    """Cirugías «Realizado» de la Agenda (últimos ``VENTANA_AGENDA`` días) que no aparecen en ninguna
    facturación: mismo paciente (nombre en cualquier orden) y fecha a menos de 60 días.

    Cada una lleva su «Empresa» (``empresa_procedimiento``). Con ``empresa`` sólo quedan las de esa
    empresa: en Facturación VMR no aparecen las cirugías que no son procedimientos de VMR."""
    if agenda_raw is None or not isinstance(agenda_raw, pd.DataFrame) or agenda_raw.empty:
        return pd.DataFrame()
    from vitae.modulos import quirofano as qx

    _, ag, _ = qx._preparar_dataframe(agenda_raw)
    h = _hoy(hoy)
    ag = ag[(ag["estado"] == "Realizado") & ag["_fecha_dt"].notna() & (ag["_fecha_dt"] <= h)
            & (ag["_fecha_dt"] >= h - pd.Timedelta(days=VENTANA_AGENDA)) & (ag["paciente"].str.strip() != "")]
    if ag.empty:
        return pd.DataFrame()
    registros: list[tuple[frozenset[str], pd.Timestamp]] = []
    for df in facturaciones:
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        d, cols_f = fx._prepare_facturacion(df)
        fechas = fecha_prestacion(df, cols_f).combine_first(d["_fecha_mes"]).combine_first(d["_fecha_factura"])
        fechas = fechas[~d["_cancelado"]]
        d = d[~d["_cancelado"]]
        registros += [(_tokens(p), f) for p, f in zip(d["_paciente"], fechas)]
    faltan = []
    for _, c in ag.iterrows():
        t = _tokens(c["paciente"])
        if len(t) < 2:
            continue
        esta = any(len(t & r) >= 2 and (pd.isna(f) or abs((f - c["_fecha_dt"]).days) <= 62) for r, f in registros)
        if not esta:
            faltan.append({"Fecha": c["_fecha_dt"], "Paciente": c["paciente"], "Procedimiento": c["procedimiento"],
                           "Médico": c["medico"], "Obra social": c["obra_social"],
                           "Empresa": empresa_procedimiento(c["procedimiento"], historial)})
    out = pd.DataFrame(faltan)
    if empresa and not out.empty:
        out = out[out["Empresa"] == str(empresa).upper()].reset_index(drop=True)
    return out


def numeros_salteados(d: pd.DataFrame, maximo_hueco: int = 15) -> pd.DataFrame:
    """Huecos en la numeración (PPPP-NNNNNNNN) por punto de venta. Sólo para revisar: la misma
    numeración puede usarse para otras facturas que no son de quirófano."""
    if d.empty:
        return pd.DataFrame()
    n = d.loc[~d["_cancelado"], "_numero_factura"].astype(str).str.strip()
    partes = n.str.extract(r"^\D*(\d{1,5})\D+(\d{1,8})\D*$")
    partes = partes.dropna()
    if partes.empty or len(partes) < max(3, int(0.6 * (n != "").sum())):
        return pd.DataFrame()
    filas = []
    for pv, g in partes.groupby(0):
        nums = sorted(set(int(x) for x in g[1]))
        for a, b in zip(nums, nums[1:]):
            if 1 < b - a <= maximo_hueco + 1:
                filas.append({"Punto de venta": pv.zfill(4), "Entre": f"{a} y {b}", "Faltan": b - a - 1})
    return pd.DataFrame(filas)


def controles(d: pd.DataFrame, agenda: pd.DataFrame, hoy: date | None = None) -> dict[str, pd.DataFrame]:
    """Cada control → las filas que lo incumplen (índice = fila de la planilla)."""
    if d.empty:
        vacio = pd.DataFrame()
        return {k: vacio for k in ("sin_facturar_vencido", "sin_facturar_proximo", "sin_facturar", "vencidas",
                                   "por_vencer", "sin_vencimiento", "debitos", "parcial_sin_debito", "repetidas",
                                   "incompletos")} | {"agenda": agenda, "salteados": vacio}
    h = _hoy(hoy)
    a = d[~d["_cancelado"]]
    sin_f = a[~a["_facturada"] & (a["_monto"] > 0)]
    fact = a[a["_facturada"]]
    pend = fact[fact["_pendiente"] > 0.004]
    rep = a["_numero_factura"].astype(str).str.strip()
    return {
        "agenda": agenda,
        "sin_facturar_vencido": sin_f[sin_f["_dias_plazo"] < 0],
        "sin_facturar_proximo": sin_f[sin_f["_dias_plazo"].between(0, AVISO_PRESENTACION)],
        "sin_facturar": sin_f,
        "vencidas": pend[pend["_vencido"]],
        "por_vencer": pend[pend["_vencimiento"].between(h, h + pd.Timedelta(days=POR_VENCER)) & ~pend["_vencido"]],
        "sin_vencimiento": pend[pend["_vencimiento"].isna() & ~pend["_vencido"]],
        "debitos": a[a["_debito_abierto"]],
        "parcial_sin_debito": a[a["_parcial"] & (a["_debito"] <= 0.004)],
        "repetidas": a[(rep != "") & rep.duplicated(keep=False)],
        "incompletos": a[(a["_obra_social"] == "SIN OBRA SOCIAL") | (a["_procedimiento"] == "SIN PROCEDIMIENTO")
                         | (a["_medico"] == "SIN MÉDICO") | (a["_monto"] <= 0)],
        "salteados": numeros_salteados(d),
    }


CONTROLES = [
    # clave, título, por qué, nivel si hay casos
    ("agenda", "Cirugías realizadas sin cargar en facturación", "En la Agenda figuran como realizadas, son procedimientos "
     "de esta empresa y no están ni en VMR ni en VM.", "alta"),
    ("sin_facturar_vencido", f"Sin facturar con el plazo de {PLAZO_PRESENTACION} días vencido",
     "Pasaron más de 60 días desde la prestación.", "alta"),
    ("sin_facturar_proximo", f"Sin facturar: vence el plazo en {AVISO_PRESENTACION} días o menos",
     "Facturalas antes de que se pase la fecha de presentación.", "media"),
    ("vencidas", "Facturas vencidas sin cobrar", "Reclamar a la obra social.", "alta"),
    ("debitos", "Débitos para reclamar", "La obra social pagó de menos y todavía no se resolvió.", "alta"),
    ("parcial_sin_debito", "Cobros parciales sin el débito anotado", "Anotá cuánto debitaron y por qué.", "media"),
    ("repetidas", "Números de factura repetidos", "Puede ser un error de carga o una factura duplicada.", "media"),
    ("por_vencer", f"Facturas que vencen en {POR_VENCER} días", "Seguimiento preventivo.", "baja"),
    ("sin_vencimiento", "Facturas sin fecha de vencimiento", "Sin vencimiento no se puede controlar la cobranza.", "media"),
    ("incompletos", "Registros con datos incompletos", "Falta obra social, procedimiento, médico o importe.", "media"),
    ("salteados", "Números de factura salteados", "Revisá si son de otra facturación (no de quirófano) o se perdieron.",
     "baja"),
]


def tabla_controles(c: dict[str, pd.DataFrame]) -> pd.DataFrame:
    filas = []
    for clave, titulo, por_que, nivel in CONTROLES:
        x = c.get(clave, pd.DataFrame())
        n = int(x["Faltan"].sum()) if clave == "salteados" and not x.empty else len(x)
        imp = 0.0
        if not x.empty and clave not in ("agenda", "salteados"):
            imp = float(x["_debito"].sum()) if clave == "debitos" else (
                float(x["_pendiente"].sum()) if clave in ("vencidas", "por_vencer", "sin_vencimiento") else
                float(x["_monto"].sum()))
        estado = "✅" if n == 0 else {"alta": "⛔", "media": "⚠️", "baja": "👀"}[nivel]
        filas.append({"": estado, "Control": titulo, "Casos": n, "Importe": imp, "Qué hacer": por_que, "_clave": clave,
                      "_nivel": nivel})
    return pd.DataFrame(filas)


def _fecha_txt(f: Any) -> str:
    return "" if pd.isna(f) else f"{f.day:02d}/{f.month:02d}/{f.year}"


def bandeja(c: dict[str, pd.DataFrame], hoy: date | None = None) -> pd.DataFrame:
    """Todo lo que hay que hacer, ordenado por urgencia."""
    h = _hoy(hoy)
    filas: list[dict[str, Any]] = []

    def add(prioridad: str, tarea: str, x: pd.DataFrame, detalle: Callable[[pd.Series], str], monto: str = "_monto"):
        for i, r in x.iterrows():
            filas.append({"Prioridad": prioridad, "Tarea": tarea, "Paciente": r["_paciente"],
                          "Obra social": r["_obra_social"], "Procedimiento": r["_procedimiento"],
                          "Importe": float(r[monto]), "Detalle": detalle(r), "_fila": i})

    for _, r in c["agenda"].iterrows() if not c["agenda"].empty else []:
        filas.append({"Prioridad": URGENTE, "Tarea": "Cargar en facturación", "Paciente": r["Paciente"],
                      "Obra social": r["Obra social"], "Procedimiento": r["Procedimiento"], "Importe": 0.0,
                      "Detalle": f"Cirugía realizada el {_fecha_txt(r['Fecha'])}", "_fila": -1})
    add(URGENTE, "Facturar (plazo vencido)", c["sin_facturar_vencido"],
        lambda r: f"Prestación {_fecha_txt(r['_prestacion'])} · venció hace {-int(r['_dias_plazo'])} días")
    add(URGENTE, "Reclamar factura vencida", c["vencidas"],
        lambda r: f"Factura {r['_numero_factura'] or 's/n'} · vencida hace "
                  f"{int((h - r['_vencimiento']).days) if pd.notna(r['_vencimiento']) else '?'} días", "_pendiente")
    add(URGENTE, "Reclamar débito", c["debitos"],
        lambda r: f"Débito {pesos(r['_debito'])}" + (f" · {r['_debito_motivo']}" if r["_debito_motivo"] else ""),
        "_debito")
    add(SEMANA, "Facturar (vence el plazo)", c["sin_facturar_proximo"],
        lambda r: f"Quedan {int(r['_dias_plazo'])} días (hasta el {_fecha_txt(r['_plazo'])})")
    add(SEMANA, "Factura por vencer", c["por_vencer"],
        lambda r: f"Factura {r['_numero_factura'] or 's/n'} · vence el {_fecha_txt(r['_vencimiento'])}", "_pendiente")
    resto = c["sin_facturar"].drop(index=list(c["sin_facturar_vencido"].index) + list(c["sin_facturar_proximo"].index),
                                   errors="ignore")
    add(HACER, "Facturar", resto, lambda r: f"Quedan {int(r['_dias_plazo'])} días" if pd.notna(r["_dias_plazo"])
        else "Sin fecha de prestación")
    add(HACER, "Anotar el débito", c["parcial_sin_debito"], lambda r: "Cobro parcial: falta anotar cuánto y por qué")
    add(HACER, "Cargar vencimiento", c["sin_vencimiento"],
        lambda r: f"Factura {r['_numero_factura'] or 's/n'} sin vencimiento", "_pendiente")
    add(REVISAR, "Factura repetida", c["repetidas"], lambda r: f"N° {r['_numero_factura']} aparece más de una vez")
    add(REVISAR, "Completar datos", c["incompletos"], lambda r: "Falta: " + ", ".join(
        t for t, ok in (("obra social", r["_obra_social"] == "SIN OBRA SOCIAL"),
                        ("procedimiento", r["_procedimiento"] == "SIN PROCEDIMIENTO"),
                        ("médico", r["_medico"] == "SIN MÉDICO"), ("importe", r["_monto"] <= 0)) if ok))
    if not filas:
        return pd.DataFrame(columns=["Prioridad", "Tarea", "Paciente", "Obra social", "Procedimiento", "Importe",
                                     "Detalle", "_fila"])
    return pd.DataFrame(filas).sort_values(["Prioridad", "Importe"], ascending=[True, False], kind="stable") \
        .reset_index(drop=True)


def cierre_mensual(d: pd.DataFrame, hoy: date | None = None, meses: int = 6) -> pd.DataFrame:
    """Semáforo por mes de prestación: facturado, cobrado, débitos y qué falta."""
    if d.empty:
        return pd.DataFrame()
    a = d[~d["_cancelado"] & d["_prestacion"].notna()].copy()
    a["_mes_p"] = a["_prestacion"].dt.to_period("M")
    desde = pd.Period(_hoy(hoy), "M") - (meses - 1)
    a = a[a["_mes_p"] >= desde]
    if a.empty:
        return pd.DataFrame()
    filas = []
    for mes, g in a.groupby("_mes_p"):
        sin_f = g[~g["_facturada"]]
        plazo_vencido = int((sin_f["_dias_plazo"] < 0).sum())
        pend = float(g.loc[g["_facturada"], "_pendiente"].sum())
        deb = float(g.loc[g["_debito_abierto"], "_debito"].sum())
        if len(sin_f) == 0 and pend <= 0.5 and deb <= 0.5:
            sem = "🟢 Cerrado"
        elif plazo_vencido or g["_vencido"].any() or deb > 0.5:
            sem = "🔴 Atrasado"
        elif len(sin_f):
            sem = "🟡 Falta facturar"
        else:
            sem = "🟡 Falta cobrar"
        filas.append({"Mes": fx._etiqueta_mes(mes.to_timestamp()), "Estado": sem, "Prestaciones": len(g),
                      "Sin facturar": len(sin_f), "Facturado": float(g["_monto"].sum()),
                      "Cobrado": float(g["_cobrado"].sum()), "Por cobrar": pend, "Débitos abiertos": deb,
                      "_mes": str(mes)})
    return pd.DataFrame(filas).sort_values("_mes", ascending=False).reset_index(drop=True)


def _historial(raw: pd.DataFrame | None, otras: list[pd.DataFrame] | None, empresa: str | None) -> dict[str, str]:
    otra = "VM" if str(empresa or "").upper() == "VMR" else "VMR"
    return historial_procedimientos({str(empresa or "").upper(): raw, otra: (list(otras or []) or [None])[0]})


def resumen(raw: pd.DataFrame | None, agenda_raw: pd.DataFrame | None = None,
            otras_facturaciones: list[pd.DataFrame] | None = None, hoy: date | None = None,
            empresa: str | None = None) -> dict[str, Any]:
    """Para el Panel general y Luz. Con ``empresa`` ("VMR"/"VM") las cirugías sin cargar son sólo
    las de esa empresa; ``otras_facturaciones[0]`` es la facturación de la otra empresa."""
    d, _ = preparar(raw, hoy)
    ag = agenda_sin_facturar(agenda_raw, [raw] + list(otras_facturaciones or []), hoy, empresa,
                             _historial(raw, otras_facturaciones, empresa)) if agenda_raw is not None \
        else pd.DataFrame()
    c = controles(d, ag, hoy)
    t = tabla_controles(c)
    b = bandeja(c, hoy)
    return {"controles": t, "bandeja": b,
            "urgentes": int((b["Prioridad"] == URGENTE).sum()) if len(b) else 0,
            "esta_semana": int((b["Prioridad"] == SEMANA).sum()) if len(b) else 0,
            "sin_facturar": len(c["sin_facturar"]), "sin_facturar_vencido": len(c["sin_facturar_vencido"]),
            "agenda_sin_cargar": len(ag), "debitos": float(c["debitos"]["_debito"].sum()) if len(c["debitos"]) else 0.0,
            "vencidas": len(c["vencidas"])}


# ---------------------------------------------------------------------------
# Escribir en la planilla (respetando el formato de cada columna)
# ---------------------------------------------------------------------------
def _escribir(raw: pd.DataFrame, fila: int, col: str, valor: Any, tipo: str, hoy: date | None) -> None:
    from vitae.ia.acciones import _escribir_fecha, _escribir_importe

    serie = raw[col] if col in raw.columns else pd.Series(dtype=object)
    if tipo == "fecha":
        texto = _escribir_fecha(valor, serie, hoy)[0] if _texto(valor) else ""
    elif tipo == "importe":
        texto = _escribir_importe(valor, serie)[0] if _texto(valor) else ""
    else:
        texto = _texto(valor)
    raw.loc[fila, col] = texto


def aplicar(raw: pd.DataFrame, cols: dict[str, str | None], cambios: list[dict[str, Any]],
            hoy: date | None = None) -> pd.DataFrame:
    """cambios: [{"fila": índice, "campo": numero_factura|fecha_factura|vencimiento|fecha_pago|estado|
    obra_social|procedimiento|medico|monto|debito|debito_estado|debito_motivo|paciente|mes|observaciones,
    "valor": ...}]"""
    out = raw.copy().astype(object)
    nombres = {"numero_factura": "numero_factura", "fecha_factura": "fecha_factura", "vencimiento": "vencimiento",
               "fecha_pago": "fecha_pago", "estado": "estado", "obra_social": "obra_social",
               "procedimiento": "procedimiento", "medico": "medico_responsable", "monto": "valor_pesos",
               "debito": "debito", "debito_estado": "debito_estado", "debito_motivo": "debito_motivo",
               "paciente": "afiliado", "mes": "mes", "observaciones": "observaciones"}
    tipos = {"fecha_factura": "fecha", "vencimiento": "fecha", "fecha_pago": "fecha", "monto": "importe",
             "debito": "importe", "mes": "fecha"}
    for c in cambios:
        campo = c["campo"]
        col = cols.get(campo) or nombres[campo]
        if col not in out.columns:
            out[col] = ""
        _escribir(out, c["fila"], col, c["valor"], tipos.get(campo, "texto"), hoy)
    return out


def nueva_fila(raw: pd.DataFrame, cols: dict[str, str | None], cirugia: dict[str, Any], importe: Any,
               hoy: date | None = None) -> pd.DataFrame:
    """Una cirugía de la Agenda → fila nueva de Facturación (estado Pendiente)."""
    from vitae.ia.acciones import _escribir_fecha, _escribir_importe

    out = raw.copy().astype(object)
    fila = {c: "" for c in out.columns}

    def poner(campo: str, defecto: str, valor: str) -> None:
        col = cols.get(campo) or defecto
        if col not in out.columns:
            out[col] = ""
            fila[col] = ""
        fila[col] = valor

    col_mes = cols.get("mes") or "mes"
    f = cirugia.get("Fecha")
    poner("mes", "mes", _escribir_fecha(f, out[col_mes] if col_mes in out.columns else pd.Series(dtype=object), hoy)[0]
          if pd.notna(f) else "")
    poner("paciente", "afiliado", _texto(cirugia.get("Paciente")).upper())
    poner("obra_social", "obra_social", _texto(cirugia.get("Obra social")))
    poner("procedimiento", "procedimiento", _texto(cirugia.get("Procedimiento")))
    poner("medico", "medico_responsable", _texto(cirugia.get("Médico")))
    col_m = cols.get("monto") or "valor_pesos"
    poner("monto", "valor_pesos", _escribir_importe(importe, out[col_m] if col_m in out.columns else pd.Series(dtype=object))[0]
          if _texto(importe) else "")
    poner("estado", "estado", "Pendiente")
    return pd.concat([out, pd.DataFrame([fila])[out.columns]], ignore_index=True)


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------
_CSS = """<style>
.cf-ok{background:#EEF8F0;border:1px solid #CFE8D5;border-radius:12px;padding:.7rem 1rem;color:#1F5130;font-weight:600}
.cf-tit{font-weight:800;color:#5E1A39;margin:.8rem 0 .2rem}
.cf-sub{color:#8A7F85;font-size:.82rem;margin-bottom:.3rem}
</style>"""


def contexto(raw: pd.DataFrame | None, *, empresa: str, agenda_raw: pd.DataFrame | None = None,
             otras_facturaciones: list[pd.DataFrame] | None = None, hoy: date | None = None) -> dict[str, Any]:
    """Todo lo que necesitan los pasos (Hoy, Cargar, Facturar, Cobrar, Débitos, Cierre), calculado una vez."""
    hoy_d = hoy or date.today()
    raw = raw.reset_index(drop=True) if isinstance(raw, pd.DataFrame) else pd.DataFrame()
    d, cols = preparar(raw, hoy_d)
    ag = agenda_sin_facturar(agenda_raw, [raw] + list(otras_facturaciones or []), hoy_d, empresa,
                             _historial(raw, otras_facturaciones, empresa))
    c = controles(d, ag, hoy_d)
    return {"raw": raw, "d": d, "cols": cols, "c": c, "b": bandeja(c, hoy_d), "t": tabla_controles(c),
            "hoy": hoy_d, "empresa": empresa, "agenda_raw": agenda_raw}


def _sin_dup(x: pd.DataFrame) -> pd.DataFrame:
    return x.loc[~x.index.duplicated()] if len(x) else x


def grupo(c: dict[str, pd.DataFrame], nombre: str) -> tuple[pd.DataFrame, dict[str, str], str]:
    """Las filas de cada grupo para resolver, sus columnas editables y la ayuda."""
    if nombre == "Facturar":
        resto = c["sin_facturar"].drop(index=list(c["sin_facturar_vencido"].index) + list(c["sin_facturar_proximo"].index),
                                       errors="ignore")
        x = pd.concat([c["sin_facturar_proximo"].sort_values("_dias_plazo"),
                       c["sin_facturar_vencido"].sort_values("_dias_plazo", ascending=False),
                       resto.sort_values("_dias_plazo")])
        return (_sin_dup(x), {"N° factura": "numero_factura", "Fecha factura": "fecha_factura", "Vencimiento": "vencimiento"},
                "Cargá el número y la fecha de factura (y el vencimiento si lo sabés). Arriba, lo que está por perder el "
                "plazo; después, lo que ya lo pasó; al final, el resto.")
    if nombre == "Cobrar":
        x = pd.concat([c["vencidas"].sort_values("_pendiente", ascending=False), c["por_vencer"], c["sin_vencimiento"]])
        return (_sin_dup(x), {"Vencimiento": "vencimiento", "Fecha de pago": "fecha_pago", "Estado": "estado",
                              "Débito": "debito", "Motivo del débito": "debito_motivo"},
                "Cuando cobres, poné la fecha de pago y el estado (Completo o Parcial). Si pagaron de menos, anotá el débito.")
    if nombre == "Débitos":
        x = pd.concat([c["debitos"].sort_values("_debito", ascending=False), c["parcial_sin_debito"]])
        return (_sin_dup(x), {"Débito": "debito", "Estado del débito": "debito_estado", "Motivo del débito": "debito_motivo"},
                "Estado del débito: A reclamar, Reclamado, Recuperado o Aceptado (no se recupera).")
    if nombre == "Completar datos":
        x = pd.concat([c["incompletos"], c["repetidas"]])
        return (_sin_dup(x), {"Obra social": "obra_social", "Procedimiento": "procedimiento", "Médico": "medico",
                              "Importe": "monto", "N° factura": "numero_factura"},
                "Completá lo que falta o corregí el número repetido.")
    raise KeyError(nombre)


_ACTUALES = {"numero_factura": "_numero_factura", "fecha_factura": "_fecha_factura", "vencimiento": "_vencimiento",
             "fecha_pago": "_fecha_pago", "estado": "_estado_original", "debito": "_debito",
             "debito_estado": "_debito_estado", "debito_motivo": "_debito_motivo", "obra_social": "_obra_social",
             "procedimiento": "_procedimiento", "medico": "_medico", "monto": "_monto"}


def _valor_actual(r: pd.Series, campo: str) -> str:
    v = r[_ACTUALES[campo]]
    if isinstance(v, pd.Timestamp):
        return _fecha_txt(v)
    if campo in ("debito", "monto"):
        return f"{float(v):.2f}".replace(".", ",") if float(v or 0) else ""
    t_ = _texto(v)
    return "" if t_.startswith("SIN ") else t_


def _guardador(st: Any, key: str, guardar: Callable[[pd.DataFrame], Any]) -> tuple[int, Callable[[pd.DataFrame, str], None]]:
    rev = int(st.session_state.get(f"{key}_rev", 0))

    def _guardar(nuevo: pd.DataFrame, aviso: str) -> None:
        try:
            guardar(nuevo)
        except Exception as exc:
            st.error(f"No se pudo guardar: {exc}")
            return
        st.session_state[f"{key}_rev"] = rev + 1
        st.toast(aviso, icon=":material/check_circle:")
        st.rerun()

    return rev, _guardar


def render_agenda(ctx: dict[str, Any], *, guardar: Callable[[pd.DataFrame], Any], key: str = "cf") -> None:
    """Cirugías realizadas en la Agenda que faltan cargar: se agregan con el importe."""
    import streamlit as st

    c, raw, cols, empresa, hoy_d = ctx["c"], ctx["raw"], ctx["cols"], ctx["empresa"], ctx["hoy"]
    if c["agenda"].empty:
        return
    rev, _guardar = _guardador(st, key, guardar)
    st.markdown('<div class="cf-tit">Cirugías realizadas sin cargar en facturación</div>'
                f'<div class="cf-sub">Sólo las de procedimientos de {html.escape(empresa)} (según lo que ya facturaste). '
                'Tildá las que agregás, poné el importe y tocá «Agregar».</div>', unsafe_allow_html=True)
    tab = c["agenda"].assign(Agregar=False, Importe="", Fecha=c["agenda"]["Fecha"].map(_fecha_txt))
    ed = st.data_editor(tab[["Agregar", "Fecha", "Paciente", "Procedimiento", "Médico", "Obra social", "Importe"]],
                        hide_index=True, use_container_width=True, key=f"{key}_ag_{rev}",
                        disabled=["Fecha", "Paciente", "Procedimiento", "Médico"])
    if st.button(f"Agregar a Facturación {empresa}", key=f"{key}_ag_ok", icon=":material/add:"):
        nuevo = raw
        for i, r in ed[ed["Agregar"]].iterrows():
            cir = c["agenda"].iloc[i].to_dict() | {"Obra social": r["Obra social"]}
            nuevo = nueva_fila(nuevo, cols, cir, r["Importe"], hoy_d)
        if len(nuevo) > len(raw):
            _guardar(nuevo, f"{len(nuevo) - len(raw)} cirugía(s) agregada(s)")
        else:
            st.info("Tildá al menos una cirugía.")


def render_grupo(ctx: dict[str, Any], nombre: str, *, guardar: Callable[[pd.DataFrame], Any], key: str = "cf",
                 en_expander: bool = True, extra: dict[str, pd.Series] | None = None) -> None:
    """La lista de un grupo (Facturar, Cobrar, Débitos, Completar datos) para resolver ahí mismo."""
    import streamlit as st

    x, campos, ayuda = grupo(ctx["c"], nombre)
    if x.empty:
        return
    raw, cols, hoy_d = ctx["raw"], ctx["cols"], ctx["hoy"]
    rev, _guardar = _guardador(st, key, guardar)

    def cuerpo() -> None:
        st.caption(ayuda)
        base = pd.DataFrame({"fila": x.index, "Paciente": x["_paciente"], "Obra social ": x["_obra_social"],
                             "Procedimiento ": x["_procedimiento"], "Mes": x["_prestacion"].map(_fecha_txt),
                             "Importe ": x["_monto"].map(lambda v: pesos(v) if v else "")})
        for etiqueta, serie in (extra or {}).items():
            base[etiqueta] = [serie.get(i, "") for i in x.index]
        for etiqueta, campo in campos.items():
            base[etiqueta] = [_valor_actual(r, campo) for _, r in x.iterrows()]
        conf: dict[str, Any] = {"fila": None}
        if "Estado" in campos:
            conf["Estado"] = st.column_config.SelectboxColumn(options=["Pendiente", "Completo", "Parcial", "Vencido",
                                                                       "Anulado"])
        if "Estado del débito" in campos:
            conf["Estado del débito"] = st.column_config.SelectboxColumn(options=ESTADOS_DEBITO)
        fijas = ["Paciente", "Obra social ", "Procedimiento ", "Mes", "Importe "] + list(extra or {})
        ed = st.data_editor(base.reset_index(drop=True), hide_index=True, use_container_width=True,
                            key=f"{key}_{nombre}_{rev}", disabled=fijas, column_config=conf)
        if st.button("Guardar", key=f"{key}_{nombre}_ok", type="primary", icon=":material/save:"):
            cambios = []
            for (_, nue), (_, ori) in zip(ed.iterrows(), base.reset_index(drop=True).iterrows()):
                for etiqueta, campo in campos.items():
                    if _texto(nue[etiqueta]) != _texto(ori[etiqueta]):
                        cambios.append({"fila": int(nue["fila"]), "campo": campo, "valor": nue[etiqueta]})
                        if campo == "debito" and _texto(nue[etiqueta]) and "Estado del débito" not in campos:
                            cambios.append({"fila": int(nue["fila"]), "campo": "debito_estado", "valor": "A reclamar"})
            if cambios:
                _guardar(aplicar(raw, cols, cambios, hoy_d), f"{len(cambios)} dato(s) guardado(s)")
            else:
                st.info("No hay cambios para guardar.")

    if en_expander:
        with st.expander(f"{nombre} · {len(x)}", icon=":material/edit_note:"):
            cuerpo()
    else:
        cuerpo()


def render_control(raw: pd.DataFrame, *, empresa: str, guardar: Callable[[pd.DataFrame], Any],
                   agenda_raw: pd.DataFrame | None = None, otras_facturaciones: list[pd.DataFrame] | None = None,
                   key: str = "cf", hoy: date | None = None) -> None:
    import streamlit as st

    from vitae.core.ui import tarjetas_kpi

    hoy_d = hoy or date.today()
    st.markdown(_CSS, unsafe_allow_html=True)
    raw = raw.reset_index(drop=True) if isinstance(raw, pd.DataFrame) else pd.DataFrame()
    d, cols = preparar(raw, hoy_d)
    ag = agenda_sin_facturar(agenda_raw, [raw] + list(otras_facturaciones or []), hoy_d, empresa,
                             _historial(raw, otras_facturaciones, empresa))
    c = controles(d, ag, hoy_d)
    b = bandeja(c, hoy_d)
    t = tabla_controles(c)
    rev = int(st.session_state.get(f"{key}_rev", 0))

    def _guardar(nuevo: pd.DataFrame, aviso: str) -> None:
        try:
            guardar(nuevo)
        except Exception as exc:
            st.error(f"No se pudo guardar: {exc}")
            return
        st.session_state[f"{key}_rev"] = rev + 1
        st.toast(aviso, icon=":material/check_circle:")
        st.rerun()

    urg = int((b["Prioridad"] == URGENTE).sum()) if len(b) else 0
    sem = int((b["Prioridad"] == SEMANA).sum()) if len(b) else 0
    tarjetas_kpi([
        ("Urgente", str(urg), "Plazo vencido, vencidas, débitos, cirugías sin cargar", "#BD4B4B"),
        ("Esta semana", str(sem), "Por vencer el plazo o la factura", "#C9895B"),
        ("Para facturar", str(len(c["sin_facturar"])), pesos(float(c["sin_facturar"]["_monto"].sum()) if len(c["sin_facturar"]) else 0, 0),
         "#8C2853"),
        ("Débitos abiertos", pesos(float(c["debitos"]["_debito"].sum()) if len(c["debitos"]) else 0, 0),
         f"{len(c['debitos'])} para reclamar", "#4F7CAC"),
    ])
    vista = st.segmented_control("Vista", ["Bandeja de hoy", "Controles", "Cierre de mes"], default="Bandeja de hoy",
                                 key=f"{key}_vista", label_visibility="collapsed") or "Bandeja de hoy"

    if vista == "Controles":
        st.dataframe(t.drop(columns=["_clave", "_nivel"]).assign(Importe=lambda x: x["Importe"].map(
            lambda v: pesos(v) if v else "")), hide_index=True, use_container_width=True)
        st.caption(f"Plazo de presentación: {PLAZO_PRESENTACION} días desde la prestación (si sólo está el mes, desde "
                   "el último día del mes).")
        if not c["salteados"].empty:
            with st.expander("Números salteados"):
                st.dataframe(c["salteados"], hide_index=True, use_container_width=True)
        return

    if vista == "Cierre de mes":
        cm = cierre_mensual(d, hoy_d)
        if cm.empty:
            st.info("No hay prestaciones en los últimos 6 meses.")
            return
        st.dataframe(cm.drop(columns="_mes").assign(**{k: cm[k].map(lambda v: pesos(v, 0)) for k in
                                                       ("Facturado", "Cobrado", "Por cobrar", "Débitos abiertos")}),
                     hide_index=True, use_container_width=True)
        st.caption("🟢 todo facturado y cobrado · 🟡 falta facturar o cobrar · 🔴 plazo vencido, facturas vencidas o "
                   "débitos sin resolver. Por mes de la prestación.")
        return

    # ---- Bandeja de hoy ----------------------------------------------------
    if b.empty:
        st.markdown('<div class="cf-ok">✅ Todo al día: nada para facturar fuera de plazo, ni vencido, ni débitos '
                    'pendientes.</div>', unsafe_allow_html=True)
    else:
        vista_b = b.drop(columns="_fila").assign(Importe=b["Importe"].map(lambda v: pesos(v) if v else ""))
        fondos = {URGENTE: "#FBE4E4", SEMANA: "#FDF0DC", REVISAR: "#F4F1F3"}
        try:
            vista_b = vista_b.style.apply(lambda fila: [f"background-color: {fondos.get(fila['Prioridad'], '')}"] *
                                          len(fila), axis=1)
        except Exception:
            pass
        st.dataframe(vista_b, hide_index=True, use_container_width=True, height=min(38 + 35 * len(b), 420))

    # Resolver por grupo
    ctx = {"raw": raw, "d": d, "cols": cols, "c": c, "b": b, "t": t, "hoy": hoy_d, "empresa": empresa}
    render_agenda(ctx, guardar=guardar, key=key)
    for nombre in ("Facturar", "Cobrar", "Débitos", "Completar datos"):
        render_grupo(ctx, nombre, guardar=guardar, key=key)
