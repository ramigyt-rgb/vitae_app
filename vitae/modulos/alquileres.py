"""Alquileres de consultorios: lo que paga cada médico por mes, como la planilla.

La planilla: una fila por médico (SANCHEZ WILDE, SAJAMA (MAMOGRAFO)…) y una columna por mes con
el valor del alquiler, o «SIN CARGO», «BAJA», «A CTA INFORMES». Abajo, el servicio de WhatsApp
que se suma («MAS $48000 SERV WPP»).

Se guarda una fila por médico y mes en la pestaña ``valores_alquileres``:
    medico        como figura en la planilla (dos con el mismo nombre → «SALMORAL (2)»)
    periodo       AAAA-MM
    condicion     Con cargo / Sin cargo / A cuenta (p. ej. a cuenta de informes) / Baja
    alquiler      valor del mes
    servicio      servicio WPP del mes (va aparte: no es parte del alquiler)
    estado        Pendiente / Cobrado / Parcial / Compensado (a cuenta)
    cobrado, fecha_cobro, medio, observaciones, orden (orden de la planilla)

A cobrar = el alquiler de los que tienen cargo (o a cuenta). El servicio WPP va aparte: se muestra
como en la planilla (fila amarilla) y con su propio total, pero no se suma al alquiler. Lo que no se cobró de meses
anteriores es deuda. Los cobros se pueden tomar de las cajas (rubro Alquileres o «alquiler» en la
descripción, con el apellido del médico). El formato anterior de la pestaña (inmueble_area,
valor_mensual, periodo…) se sigue leyendo.
"""
from __future__ import annotations

import html
import io
import re
import uuid
from datetime import date, datetime
from typing import Any, Callable

import pandas as pd

from vitae.modulos.pagos_pendientes import _sin_acentos, _texto, nombre_mes, numero, pesos
from vitae.modulos.planes import etiqueta_mes, mes_actual, mes_de

TABLA = "valores_alquileres"
COLUMNAS = ["id_registro", "medico", "periodo", "condicion", "alquiler", "servicio", "estado", "cobrado",
            "fecha_cobro", "medio", "observaciones", "orden", "updated_at"]
CARGO, SIN_CARGO, A_CUENTA, BAJA = "Con cargo", "Sin cargo", "A cuenta", "Baja"
CONDICIONES = [CARGO, SIN_CARGO, A_CUENTA, BAJA]
PENDIENTE, COBRADO, PARCIAL, COMPENSADO = "Pendiente", "Cobrado", "Parcial", "Compensado"
ESTADOS = [PENDIENTE, COBRADO, PARCIAL, COMPENSADO]
MEDIOS = ["", "Efectivo", "Transferencia", "Mercado Pago", "Cheque", "A cuenta"]
SECCIONES = ["Planilla", "Cobros", "Nuevo mes", "Médicos", "Análisis", "Importar"]
CAJAS = {"caja_vm": "Caja VM", "caja_vmr": "Caja VMR", "banco_galicia_vm": "Banco Galicia VM"}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _n(v: Any) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return 0.0 if pd.isna(v) else float(v)
    x = numero(v)
    return float(x) if x is not None else 0.0


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _mes_mas(mes: str, n: int) -> str:
    return str(pd.Period(mes, freq="M") + n)


def mes_largo(periodo: str) -> str:
    return nombre_mes(periodo).lower() if periodo else ""


def nombre_medico(v: Any) -> str:
    return re.sub(r"\s+", " ", _texto(v)).strip()


def leer_celda(v: Any) -> tuple[str, float, str] | None:
    """Una celda de la planilla → (condición, alquiler, nota). None si está vacía."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (CARGO, float(v), "")
    t = _texto(v)
    if not t:
        return None
    n = _sin_acentos(t)
    if "sin cargo" in n or n in {"s/c", "sc", "bonificado"}:
        return (SIN_CARGO, 0.0, "")
    if n.startswith("baja"):
        return (BAJA, 0.0, "")
    if re.search(r"\ba\s*(cta|cuenta)\b", n):
        x = numero(re.sub(r"[^\d.,$-]", " ", t).strip().split(" ")[0]) if re.search(r"\d", t) else None
        return (A_CUENTA, float(x or 0.0), t)
    x = numero(t)
    if x is not None:
        return (CARGO, float(x), "")
    return (CARGO, 0.0, t)


def texto_celda(condicion: str, alquiler: float, nota: str = "") -> str:
    """Al revés: cómo se ve (y se edita) la celda."""
    if condicion == SIN_CARGO:
        return "SIN CARGO"
    if condicion == BAJA:
        return "BAJA"
    if condicion == A_CUENTA:
        return nota or ("A CUENTA " + pesos(alquiler) if alquiler else "A CUENTA")
    if not alquiler and nota:
        return nota
    return pesos(alquiler)


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------
def _col(df: pd.DataFrame, *nombres: str) -> str | None:
    mapa = {re.sub(r"[^a-z0-9]+", "_", _sin_acentos(c)).strip("_"): c for c in df.columns}
    return next((mapa[n] for n in nombres if n in mapa), None)


def _condicion(v: Any) -> str:
    n = _sin_acentos(v)
    if not n:
        return ""
    if "sin" in n:
        return SIN_CARGO
    if "cuenta" in n or "cta" in n:
        return A_CUENTA
    if "baja" in n or "finaliz" in n:
        return BAJA
    return CARGO


def _estado(v: Any) -> str:
    n = _sin_acentos(v)
    if n.startswith("cobr") or n.startswith("pag"):
        return COBRADO
    if "parcial" in n:
        return PARCIAL
    if "compens" in n or "cuenta" in n:
        return COMPENSADO
    if n.startswith("pend") or n.startswith("impag") or n.startswith("debe"):
        return PENDIENTE
    return ""


def normalizar(df: pd.DataFrame | None) -> pd.DataFrame:
    """La pestaña (formato nuevo o el anterior) → una fila por médico y mes, con tipos limpios."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=COLUMNAS)

    def serie(*nombres: str) -> pd.Series:
        c = _col(df, *nombres)
        return df[c].map(_texto) if c else pd.Series("", index=df.index, dtype="object")

    d = pd.DataFrame(index=df.index)
    d["id_registro"] = [x or _id() for x in serie("id_registro", "id")]
    d["medico"] = serie("medico", "profesional", "inquilino", "inmueble_area", "locador").map(nombre_medico)
    per = serie("periodo", "mes")
    fecha = serie("fecha", "fecha_desde")
    d["periodo"] = [mes_de(p) or mes_de(f) for p, f in zip(per, fecha)]
    alq_c = _col(df, "alquiler", "valor_mensual", "importe", "valor")
    d["alquiler"] = df[alq_c].map(_n) if alq_c else 0.0
    srv_c = _col(df, "servicio", "servicio_wpp", "adicional")
    d["servicio"] = df[srv_c].map(_n) if srv_c else 0.0
    cond = serie("condicion")
    est = serie("estado")
    d["condicion"] = [_condicion(c) or (BAJA if _sin_acentos(e).startswith("finaliz") else CARGO)
                      for c, e in zip(cond, est)]
    d["estado"] = [_estado(e) for e in est]
    cob_c = _col(df, "cobrado")
    d["cobrado"] = df[cob_c].map(_n) if cob_c else 0.0
    d["fecha_cobro"] = serie("fecha_cobro", "fecha_pago")
    d["medio"] = serie("medio", "medio_de_pago")
    d["observaciones"] = serie("observaciones", "nota", "notas")
    ord_c = _col(df, "orden")
    d["orden"] = df[ord_c].map(_n).astype(int) if ord_c else range(len(df))
    d["updated_at"] = serie("updated_at")
    d = d[(d["medico"] != "") & (d["periodo"] != "")]
    sin_cargo = d["condicion"].isin([SIN_CARGO, BAJA])
    d.loc[sin_cargo, "estado"] = ""
    d.loc[sin_cargo, ["alquiler", "servicio"]] = 0.0
    d.loc[~sin_cargo & (d["estado"] == ""), "estado"] = [COMPENSADO if c == A_CUENTA else PENDIENTE
                                                         for c in d.loc[~sin_cargo & (d["estado"] == ""), "condicion"]]
    # Un médico y mes, una sola fila (la última manda).
    d = d.drop_duplicates(["medico", "periodo"], keep="last")
    return d[COLUMNAS].reset_index(drop=True)


def para_guardar(d: pd.DataFrame) -> pd.DataFrame:
    out = d[COLUMNAS].copy()
    for c in ("alquiler", "servicio", "cobrado"):
        out[c] = out[c].map(lambda v: f"{float(v):.2f}")
    out["orden"] = out["orden"].map(lambda v: str(int(v)))
    out["updated_at"] = out["updated_at"].replace("", _ahora())
    return out.sort_values(["orden", "medico", "periodo"], kind="stable").reset_index(drop=True)


def a_cobrar(d: pd.DataFrame) -> pd.Series:
    """El alquiler a cobrar (el servicio WPP va aparte)."""
    return d["alquiler"].where(d["condicion"].isin([CARGO, A_CUENTA]), 0.0)


def servicio_total(d: pd.DataFrame, mes: str) -> float:
    """El servicio WPP del mes, aparte del alquiler."""
    x = d[(d["periodo"] == mes) & d["condicion"].isin([CARGO, A_CUENTA])]
    return float(x["servicio"].sum())


def cobrado_efectivo(d: pd.DataFrame) -> pd.Series:
    """Lo cobrado: Cobrado/Compensado = todo lo que había que cobrar; Parcial = lo anotado."""
    ac = a_cobrar(d)
    full = d["estado"].isin([COBRADO, COMPENSADO])
    return ac.where(full, d["cobrado"].clip(lower=0).where(d["estado"] == PARCIAL, 0.0))


def saldo(d: pd.DataFrame) -> pd.Series:
    return (a_cobrar(d) - cobrado_efectivo(d)).clip(lower=0.0)


def meses(d: pd.DataFrame) -> list[str]:
    return sorted(set(d["periodo"])) if not d.empty else []


def medicos(d: pd.DataFrame) -> list[str]:
    """En el orden de la planilla."""
    if d.empty:
        return []
    o = d.groupby("medico")["orden"].min().sort_values(kind="stable")
    return list(o.index)


def mes_referencia(d: pd.DataFrame, hoy: date | None = None) -> str:
    ma = mes_actual(hoy)
    ms = meses(d)
    if not ms:
        return ma
    if ma in ms:
        return ma
    anteriores = [m for m in ms if m <= ma]
    return anteriores[-1] if anteriores else ms[0]


def aumento(d: pd.DataFrame, mes: str) -> float | None:
    """Aumento típico (%) de los que pagan, contra el mes anterior: la mediana, para que un cambio
    puntual (un consultorio más grande, un descuento) no lo distorsione."""
    ant = _mes_mas(mes, -1)
    x = d[(d["condicion"] == CARGO) & (d["alquiler"] > 0)]
    a = x[x["periodo"] == mes].set_index("medico")["alquiler"]
    b = x[x["periodo"] == ant].set_index("medico")["alquiler"]
    comunes = a.index.intersection(b.index)
    if not len(comunes):
        return None
    return float(((a[comunes] / b[comunes]) - 1).median() * 100)


def servicio_del_mes(d: pd.DataFrame, mes: str) -> float:
    s = d.loc[(d["periodo"] == mes) & (d["servicio"] > 0), "servicio"]
    return float(s.mode().iloc[0]) if len(s) else 0.0


def resumen(d: pd.DataFrame, hoy: date | None = None) -> dict[str, Any]:
    """Para la pantalla, el Panel general y Luz."""
    if d.empty:
        return {"hay": False}
    mes = mes_referencia(d, hoy)
    x = d[d["periodo"] == mes]
    ac, ce, sa = a_cobrar(d), cobrado_efectivo(d), saldo(d)
    antes = d[(d["periodo"] < mes) & (sa > 0.5)]
    deudores = (antes.assign(_s=sa[antes.index]).groupby("medico")
                .agg(deuda=("_s", "sum"), meses=("periodo", lambda p: ", ".join(etiqueta_mes(m) for m in sorted(p))))
                .sort_values("deuda", ascending=False).reset_index()) if len(antes) else \
        pd.DataFrame(columns=["medico", "deuda", "meses"])
    ms = meses(d)
    ma = mes_actual(hoy)
    return {
        "hay": True, "mes": mes, "ultimo_mes": ms[-1],
        "a_cobrar": float(ac[x.index].sum()), "cobrado": float(ce[x.index].sum()),
        "pendiente": float(sa[x.index].sum()),
        "alquileres": float(x.loc[x["condicion"].isin([CARGO, A_CUENTA]), "alquiler"].sum()),
        "servicio": servicio_total(d, mes), "con_servicio": int(((x["servicio"] > 0)
                                                                   & x["condicion"].isin([CARGO, A_CUENTA])).sum()),
        "servicio_valor": servicio_del_mes(d, mes),
        "con_cargo": int((x["condicion"] == CARGO).sum()), "sin_cargo": int((x["condicion"] == SIN_CARGO).sum()),
        "a_cuenta": int((x["condicion"] == A_CUENTA).sum()), "bajas": int((x["condicion"] == BAJA).sum()),
        "por_cobrar_mes": int(((sa[x.index] > 0.5)).sum()),
        "adeudado": float(deudores["deuda"].sum()) if len(deudores) else 0.0,
        "deudores": deudores, "aumento": aumento(d, mes),
        "falta_mes": bool(ms and ms[-1] < ma), "proximo": _mes_mas(ms[-1], 1),
    }


# ---------------------------------------------------------------------------
# Planilla: grilla, edición, nuevo mes, altas y bajas
# ---------------------------------------------------------------------------
def tabla_planilla(d: pd.DataFrame, meses_v: list[str], mostrar_bajas: bool = True) -> pd.DataFrame:
    """Médico × mes con el texto de cada celda (lo que se ve y se edita)."""
    filas = []
    for med in medicos(d):
        x = d[(d["medico"] == med) & d["periodo"].isin(meses_v)].set_index("periodo")
        if x.empty:
            continue
        if not mostrar_bajas:
            ultimo = d[d["medico"] == med].sort_values("periodo").iloc[-1]
            if ultimo["condicion"] == BAJA and ultimo["periodo"] < meses_v[-1]:
                continue  # se fue antes del último mes que se ve
        fila = {"Médico": med}
        for m in meses_v:
            fila[m] = texto_celda(x.loc[m, "condicion"], x.loc[m, "alquiler"], x.loc[m, "observaciones"]) \
                if m in x.index else ""
        filas.append(fila)
    return pd.DataFrame(filas, columns=["Médico"] + meses_v)


def _nuevo_orden(d: pd.DataFrame) -> int:
    return int(d["orden"].max()) + 1 if len(d) else 0


def poner(d: pd.DataFrame, medico: str, periodo: str, condicion: str, alquiler: float, nota: str = "",
          servicio: float | None = None) -> pd.DataFrame:
    """Crea o cambia la celda (médico, mes) sin perder lo cobrado."""
    d = d.copy()
    m = (d["medico"] == medico) & (d["periodo"] == periodo)
    orden = int(d.loc[d["medico"] == medico, "orden"].min()) if (d["medico"] == medico).any() else _nuevo_orden(d)
    if servicio is None:
        prev = d[m]
        if len(prev):
            servicio = float(prev["servicio"].iloc[0])
        else:
            servicio = servicio_del_mes(d, periodo) if condicion == CARGO else 0.0
    if condicion in (SIN_CARGO, BAJA):
        alquiler, servicio = 0.0, 0.0
    if m.any():
        i = d.index[m][0]
        antes = d.loc[i, "condicion"]
        d.loc[i, ["condicion", "alquiler", "servicio", "updated_at"]] = [condicion, float(alquiler), float(servicio),
                                                                          _ahora()]
        if nota:
            d.loc[i, "observaciones"] = nota
        elif antes == A_CUENTA and condicion != A_CUENTA:
            d.loc[i, "observaciones"] = ""  # era el texto de la celda («A CTA INFORMES»)
        if condicion in (SIN_CARGO, BAJA):
            d.loc[i, ["estado", "cobrado"]] = ["", 0.0]
        elif d.loc[i, "estado"] == "":
            d.loc[i, "estado"] = COMPENSADO if condicion == A_CUENTA else PENDIENTE
        return d
    fila = {"id_registro": _id(), "medico": medico, "periodo": periodo, "condicion": condicion,
            "alquiler": float(alquiler), "servicio": float(servicio),
            "estado": "" if condicion in (SIN_CARGO, BAJA) else (COMPENSADO if condicion == A_CUENTA else PENDIENTE),
            "cobrado": 0.0, "fecha_cobro": "", "medio": "", "observaciones": nota, "orden": orden,
            "updated_at": _ahora()}
    return pd.concat([d, pd.DataFrame([fila], columns=COLUMNAS)], ignore_index=True)


def aplicar_planilla(d: pd.DataFrame, original: pd.DataFrame, editada: pd.DataFrame,
                     meses_v: list[str]) -> tuple[pd.DataFrame, int]:
    """Los cambios hechos en la grilla editable (texto de cada celda) → los datos. Devuelve (datos, cambios)."""
    nuevo = d.copy()
    cambios = 0
    orig = original.set_index("Médico") if len(original) else pd.DataFrame(columns=meses_v)
    for _, fila in editada.iterrows():
        med_ed = nombre_medico(fila.get("Médico"))
        if not med_ed:
            continue
        for m in meses_v:
            v = _texto(fila.get(m))
            antes = _texto(orig.loc[med_ed, m]) if med_ed in orig.index and m in orig.columns else ""
            if v == antes:
                continue
            cambios += 1
            celda = leer_celda(v)
            if celda is None:
                nuevo = nuevo[~((nuevo["medico"] == med_ed) & (nuevo["periodo"] == m))]
                continue
            nuevo = poner(nuevo, med_ed, m, *celda)
    # Filas borradas de la grilla: se borran sus meses visibles.
    quedan = {nombre_medico(x) for x in editada["Médico"]} if len(editada) else set()
    for med in orig.index:
        if med not in quedan:
            m_ = (nuevo["medico"] == med) & nuevo["periodo"].isin(meses_v)
            cambios += int(m_.sum())
            nuevo = nuevo[~m_]
    return nuevo.reset_index(drop=True), cambios


def generar_mes(d: pd.DataFrame, mes: str, *, aumento_pct: float = 0.0, redondeo: int = 0,
                servicio: float | None = None) -> pd.DataFrame:
    """Las filas del mes nuevo a partir del último mes cargado antes: mismo médico y condición,
    alquiler + aumento (redondeado), servicio del mes. Los de baja no pasan."""
    previos = [m for m in meses(d) if m < mes]
    if not previos:
        return pd.DataFrame(columns=COLUMNAS)
    base = d[d["periodo"] == previos[-1]]
    base = base[base["condicion"] != BAJA].copy()
    srv = servicio_del_mes(d, previos[-1]) if servicio is None else float(servicio)
    filas = []
    for _, r in base.sort_values("orden").iterrows():
        alq = float(r["alquiler"])
        if r["condicion"] == CARGO and alq > 0:
            alq = alq * (1 + aumento_pct / 100.0)
            if redondeo:
                alq = round(alq / redondeo) * redondeo
            alq = round(alq, 2)
        con_srv = r["condicion"] == CARGO and (r["servicio"] > 0 or srv > 0)
        filas.append({"id_registro": _id(), "medico": r["medico"], "periodo": mes, "condicion": r["condicion"],
                      "alquiler": alq, "servicio": srv if con_srv else 0.0,
                      "estado": "" if r["condicion"] == SIN_CARGO else (COMPENSADO if r["condicion"] == A_CUENTA
                                                                         else PENDIENTE),
                      "cobrado": 0.0, "fecha_cobro": "", "medio": "",
                      "observaciones": r["observaciones"] if r["condicion"] == A_CUENTA else "",
                      "orden": int(r["orden"]), "updated_at": _ahora()})
    return pd.DataFrame(filas, columns=COLUMNAS)


def guardar_mes(d: pd.DataFrame, filas: pd.DataFrame) -> pd.DataFrame:
    """Pone las filas de un mes (las que ya existen se actualizan sin perder lo cobrado)."""
    nuevo = d.copy()
    for _, r in filas.iterrows():
        nuevo = poner(nuevo, r["medico"], r["periodo"], r["condicion"], float(r["alquiler"]),
                      _texto(r.get("observaciones")), float(r["servicio"]))
    return nuevo


def alta(d: pd.DataFrame, medico: str, mes: str, condicion: str, alquiler: float, servicio: float) -> pd.DataFrame:
    return poner(d, nombre_medico(medico), mes, condicion, alquiler, "", servicio)


def baja(d: pd.DataFrame, medico: str, mes: str) -> pd.DataFrame:
    """Desde ese mes deja de alquilar: BAJA en el mes y se borran los meses siguientes."""
    nuevo = d[~((d["medico"] == medico) & (d["periodo"] > mes))]
    return poner(nuevo, medico, mes, BAJA, 0.0)


def renombrar(d: pd.DataFrame, viejo: str, nuevo_nombre: str) -> pd.DataFrame:
    nuevo = d.copy()
    nuevo.loc[nuevo["medico"] == viejo, "medico"] = nombre_medico(nuevo_nombre)
    return nuevo.drop_duplicates(["medico", "periodo"], keep="last").reset_index(drop=True)


def borrar_medico(d: pd.DataFrame, medico: str) -> pd.DataFrame:
    return d[d["medico"] != medico].reset_index(drop=True)


def marcar(d: pd.DataFrame, ids: list[str], estado: str, *, cobrado: float | None = None,
           fecha: str = "", medio: str = "") -> pd.DataFrame:
    nuevo = d.copy()
    m = nuevo["id_registro"].isin(ids)
    nuevo.loc[m, "estado"] = estado
    if cobrado is not None:
        nuevo.loc[m, "cobrado"] = float(cobrado)
    elif estado in (COBRADO, COMPENSADO):
        nuevo.loc[m, "cobrado"] = a_cobrar(nuevo[m])
    elif estado == PENDIENTE:
        nuevo.loc[m, "cobrado"] = 0.0
    nuevo.loc[m, "fecha_cobro"] = fecha if estado != PENDIENTE else ""
    if medio or estado == PENDIENTE:
        nuevo.loc[m, "medio"] = medio
    nuevo.loc[m, "updated_at"] = _ahora()
    return nuevo


# ---------------------------------------------------------------------------
# Cobros vistos en las cajas
# ---------------------------------------------------------------------------
_GENERICAS = {"dr", "dra", "de", "del", "la", "las", "los", "el", "y", "consultorio", "mamografo", "alquiler",
              "alquileres", "cta", "sr", "sra", "servicio", "wpp", "informes"}


def tokens_medico(nombre: str) -> set[str]:
    t = re.sub(r"\(\s*\d+\s*\)", " ", _sin_acentos(nombre))
    t = re.sub(r"\([^)]*\)", " ", t)
    return {w for w in re.findall(r"[a-z]+", t) if w not in _GENERICAS and len(w) >= 2}


def _menciona(texto: str, tokens: set[str]) -> bool:
    palabras = set(re.findall(r"[a-z]+", texto))
    largos = {t for t in tokens if len(t) >= 3}
    if largos:
        return bool(largos & palabras)
    return bool(tokens & palabras)


def cobros_en_caja(d: pd.DataFrame, mes: str, cajas: dict[str, pd.DataFrame | None]) -> dict[str, list[dict[str, Any]]]:
    """Entradas de las cajas que parecen el alquiler de cada médico en ese mes: rubro Alquileres o
    «alquil» en la descripción, y el apellido del médico. También las del mes siguiente que nombran
    el mes («ALQUILER OCTUBRE SANCHEZ»)."""
    from vitae.modulos import caja as cj

    movs = []
    sig = _mes_mas(mes, 1)
    nombre_m = _sin_acentos(nombre_mes(mes).split()[0])
    for tabla, raw in (cajas or {}).items():
        if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
            continue
        c = cj.preparar(raw)
        c = c[(c["_entrada"] > 0) & (c["_moneda"] == "ARS") & c["_mes"].isin([mes, sig])]
        for _, r in c.iterrows():
            texto = _sin_acentos(f"{r['_concepto']} {r['_quien']} {r['_obs']}")
            if not (r["_rubro"] == cj.ALQUILERES or "alquil" in texto):
                continue
            if r["_mes"] == sig and nombre_m not in texto:
                continue
            movs.append({"caja": CAJAS.get(tabla, tabla), "fecha": r["_fecha"], "importe": float(r["_entrada"]),
                         "detalle": r["_concepto"] or r["_quien"], "_texto": texto})
    out: dict[str, list[dict[str, Any]]] = {}
    if not movs:
        return out
    for med in d.loc[d["periodo"] == mes, "medico"].unique():
        tk = tokens_medico(med)
        if not tk:
            continue
        hallados = [{k: v for k, v in m.items() if k != "_texto"} for m in movs if _menciona(m["_texto"], tk)]
        if hallados:
            out[med] = hallados
    return out


def sugerencias_caja(d: pd.DataFrame, mes: str, encontrados: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Para cada médico pendiente del mes con cobros en caja: estado sugerido (Cobrado si alcanza, si no Parcial)."""
    x = d[(d["periodo"] == mes) & d["estado"].isin([PENDIENTE, PARCIAL])]
    ac = a_cobrar(x)
    sug = []
    for i, r in x.iterrows():
        movs = encontrados.get(r["medico"], [])
        tot = sum(m["importe"] for m in movs)
        if tot <= 0 or ac[i] <= 0:
            continue
        ult = max((m["fecha"] for m in movs if pd.notna(m["fecha"])), default=pd.NaT)
        sug.append({"id": r["id_registro"], "medico": r["medico"], "estado": COBRADO if tot >= ac[i] - 1 else PARCIAL,
                    "cobrado": min(tot, float(ac[i])), "fecha": ult.strftime("%d/%m/%Y") if pd.notna(ult) else "",
                    "caja": ", ".join(sorted({m["caja"] for m in movs}))})
    return sug


# ---------------------------------------------------------------------------
# Médicos y análisis
# ---------------------------------------------------------------------------
def resumen_medicos(d: pd.DataFrame, hoy: date | None = None) -> pd.DataFrame:
    """Una fila por médico: situación, valor actual, último aumento, deuda y cobrado del año."""
    if d.empty:
        return pd.DataFrame()
    mes = mes_referencia(d, hoy)
    sa, ce = saldo(d), cobrado_efectivo(d)
    filas = []
    for med in medicos(d):
        x = d[d["medico"] == med].sort_values("periodo")
        hasta = x[x["periodo"] <= mes]
        u = (hasta if len(hasta) else x).iloc[-1]
        pagos = x[(x["condicion"] == CARGO) & (x["alquiler"] > 0)]
        ult_aum, ult_aum_mes, meses_sin = None, "", None
        vals = list(zip(pagos["periodo"], pagos["alquiler"]))
        for (m0, v0), (m1, v1) in zip(vals, vals[1:]):
            if v1 > v0 + 0.5:
                ult_aum, ult_aum_mes = (v1 / v0 - 1) * 100, m1
        desde_aum = ult_aum_mes or (pagos["periodo"].iloc[0] if len(pagos) else "")
        if desde_aum and desde_aum <= mes:
            meses_sin = (pd.Period(mes, freq="M") - pd.Period(desde_aum, freq="M")).n
        hace12 = pagos[pagos["periodo"] <= _mes_mas(mes, -12)]
        base12 = hace12.iloc[-1]["alquiler"] if len(hace12) else (pagos.iloc[0]["alquiler"] if len(pagos) else None)
        filas.append({
            "Médico": med, "Situación": u["condicion"], "Desde": etiqueta_mes(x["periodo"].iloc[0]),
            "Último mes": etiqueta_mes(x["periodo"].iloc[-1]),
            "Alquiler": float(u["alquiler"]) if u["condicion"] in (CARGO, A_CUENTA) else 0.0,
            "Servicio": float(u["servicio"]),
            "Último aumento": f"{ult_aum:+.1f}% ({etiqueta_mes(ult_aum_mes)})" if ult_aum is not None else "",
            "Meses sin aumento": meses_sin,
            "Subió desde el inicio": (f"{(u['alquiler'] / base12 - 1) * 100:+.0f}%"
                                      if base12 and u["condicion"] == CARGO and u["alquiler"] > 0 else ""),
            "Debe": float(sa[x.index][x["periodo"] < mes].sum()),
            "Cobrado en el año": float(ce[x.index][x["periodo"].str[:4] == mes[:4]].sum()),
        })
    return pd.DataFrame(filas)


def evolucion(d: pd.DataFrame) -> pd.DataFrame:
    """Por mes: alquileres a cobrar, cobrado, pendiente, médicos que pagan, servicio WPP (aparte) y aumento."""
    if d.empty:
        return pd.DataFrame()
    ac, ce = a_cobrar(d), cobrado_efectivo(d)
    filas = []
    for m in meses(d):
        x = d[d["periodo"] == m]
        a = aumento(d, m)
        filas.append({"Mes": etiqueta_mes(m), "_mes": m, "A cobrar": float(ac[x.index].sum()),
                      "Cobrado": float(ce[x.index].sum()), "Pendiente": float((ac - ce)[x.index].clip(lower=0).sum()),
                      "Con cargo": int((x["condicion"] == CARGO).sum()),
                      "Sin cargo": int((x["condicion"] == SIN_CARGO).sum()),
                      "Servicio WPP c/u": servicio_del_mes(d, m), "Servicio WPP total": servicio_total(d, m),
                      "Aumento típico": round(a, 1) if a is not None else None})
    return pd.DataFrame(filas)


def hallazgos(d: pd.DataFrame, hoy: date | None = None) -> list[str]:
    """Lo importante para quien administra los alquileres, en frases."""
    r = resumen(d, hoy)
    if not r.get("hay"):
        return []
    out = []
    mt = mes_largo(r["mes"])
    if r["falta_mes"]:
        out.append(f"Falta cargar {mes_largo(r['proximo'])}: usá «Nuevo mes» (copia el anterior con el aumento).")
    if r["por_cobrar_mes"]:
        out.append(f"De {mt} faltan cobrar {r['por_cobrar_mes']} médico(s): {pesos(r['pendiente'], 0)}.")
    if r["adeudado"] > 0.5:
        top = r["deudores"].head(3)
        out.append("Deben meses anteriores: " + ", ".join(f"{m} {pesos(v, 0)}" for m, v in zip(top["medico"], top["deuda"]))
                   + (f" y {len(r['deudores']) - 3} más" if len(r["deudores"]) > 3 else "") + ".")
    if r["aumento"] is not None:
        out.append(f"Aumento típico de {mt}: {r['aumento']:+.1f}% contra el mes anterior.")
    rm = resumen_medicos(d, hoy)
    if len(rm):
        quietos = rm[(rm["Situación"] == CARGO) & (rm["Meses sin aumento"].fillna(0) >= 3)]
        if len(quietos):
            out.append("Sin aumento hace 3 meses o más: " + ", ".join(quietos["Médico"].head(5)) + ".")
        sc = rm[rm["Situación"] == SIN_CARGO]
        if len(sc):
            out.append(f"{len(sc)} consultorio(s) sin cargo: " + ", ".join(sc["Médico"].head(6)) + ".")
    x = d[(d["periodo"] == r["mes"]) & (d["condicion"] == CARGO) & (d["alquiler"] > 0)]
    if len(x) >= 3:
        v = x.set_index("medico")["alquiler"]
        out.append(f"Alquiler de {mt}: de {pesos(v.min(), 0)} ({v.idxmin()}) a {pesos(v.max(), 0)} ({v.idxmax()}); "
                   f"promedio {pesos(v.mean(), 0)}.")
    return out


# ---------------------------------------------------------------------------
# Importar la planilla (Excel o pegado)
# ---------------------------------------------------------------------------
def _es_servicio(texto: Any) -> bool:
    n = _sin_acentos(texto)
    return bool(n) and ("serv" in n or "wpp" in n or "whats" in n)


def _monto_en_texto(texto: Any) -> float:
    if isinstance(texto, (int, float)) and not isinstance(texto, bool):
        return 0.0 if pd.isna(texto) else float(texto)
    m = re.search(r"\$?\s*(\d[\d.,]*)", _texto(texto))
    return float(numero(m.group(1)) or 0.0) if m else 0.0


def leer_tabla(valores: list[list[Any]], *, filas_ocultas: set[int] | None = None,
               cols_ocultas: set[int] | None = None, incluir_filas_ocultas: bool = True,
               incluir_meses_ocultos: bool = True) -> dict[str, Any]:
    """La planilla (matriz de celdas) → registros médico/mes, servicio WPP por mes y controles."""
    filas_ocultas, cols_ocultas = filas_ocultas or set(), cols_ocultas or set()
    cab_r, meses_c, col_nom = None, {}, 0
    for r, fila in enumerate(valores[:30]):
        mc = {c: mes_de(v) for c, v in enumerate(fila)
              if not (isinstance(v, (int, float)) and not isinstance(v, bool))}
        mc = {c: m for c, m in mc.items() if m}
        if len(mc) >= 1 and (len(mc) >= 2 or any("medic" in _sin_acentos(v) for v in fila)):
            cab_r, meses_c = r, mc
            nom = [c for c, v in enumerate(fila) if re.search(r"medic|profes|nombre|inquil", _sin_acentos(v))]
            col_nom = nom[0] if nom else min(mc) - 1 if min(mc) > 0 else 0
            break
    if cab_r is None:
        return {"error": "No encontré la fila de los meses (ej.: MEDICO · marzo 2026 · abril 2026…)."}
    if not incluir_meses_ocultos:
        meses_c = {c: m for c, m in meses_c.items() if c not in cols_ocultas}
    registros, servicio, nombres_vistos = [], {}, {}
    ocultas_omitidas = 0
    for r in range(cab_r + 1, len(valores)):
        fila = valores[r]
        nombre = nombre_medico(fila[col_nom]) if col_nom < len(fila) else ""
        celdas = {m: (fila[c] if c < len(fila) else None) for c, m in meses_c.items()}
        if any(_es_servicio(v) for v in celdas.values()) or _es_servicio(nombre):
            for m, v in celdas.items():
                if _es_servicio(v) or (_es_servicio(nombre) and _monto_en_texto(v)):
                    servicio[m] = _monto_en_texto(v)
            continue
        if not nombre or _sin_acentos(nombre).startswith(("total", "a cobrar", "cobrado")):
            continue
        if r in filas_ocultas and not incluir_filas_ocultas:
            ocultas_omitidas += 1
            continue
        k = nombres_vistos.get(nombre, 0) + 1
        nombres_vistos[nombre] = k
        etiqueta = nombre if k == 1 else f"{nombre} ({k})"
        for m, v in celdas.items():
            celda = leer_celda(v)
            if celda is None:
                continue
            cond, alq, nota = celda
            registros.append({"medico": etiqueta, "periodo": m, "condicion": cond, "alquiler": alq,
                              "observaciones": nota, "orden": r, "_oculta": r in filas_ocultas})
    for reg in registros:
        reg["servicio"] = servicio.get(reg["periodo"], 0.0) if reg["condicion"] == CARGO else 0.0
    meses_l = sorted(set(meses_c.values()))
    totales = {}
    for m in meses_l:
        rs = [x for x in registros if x["periodo"] == m and x["condicion"] in (CARGO, A_CUENTA)]
        totales[m] = {"alquileres": sum(x["alquiler"] for x in rs), "servicio": servicio.get(m, 0.0),
                      "con_cargo": sum(1 for x in rs if x["condicion"] == CARGO)}
    return {"registros": registros, "meses": meses_l, "servicio": servicio, "totales": totales,
            "medicos": list(dict.fromkeys(x["medico"] for x in registros)),
            "repetidos": [n for n, k in nombres_vistos.items() if k > 1], "ocultas_omitidas": ocultas_omitidas}


def leer_pegado(texto: str) -> list[list[Any]]:
    """Lo copiado de Google Sheets o Excel (columnas separadas por tabulador)."""
    filas = [ln.split("\t") for ln in (texto or "").splitlines()]
    return [[c.strip() for c in f] for f in filas if any(c.strip() for c in f)]


def leer_archivo(nombre: str, contenido: bytes, hoja: str | None = None) -> tuple[dict[str, Any], list[str]]:
    """Excel (con filas y columnas ocultas) o CSV → la matriz para ``leer_tabla``."""
    n = (nombre or "").lower()
    if n.endswith(".csv") or n.endswith(".txt"):
        texto = contenido.decode("utf-8-sig", errors="replace")
        sep = "\t" if "\t" in texto.splitlines()[0] else (";" if texto.count(";") > texto.count(",") else ",")
        import csv

        valores = [f for f in csv.reader(io.StringIO(texto), delimiter=sep)]
        return {"valores": valores, "filas_ocultas": set(), "cols_ocultas": set(), "hoja": ""}, []
    if n.endswith(".xls"):
        xl = pd.ExcelFile(io.BytesIO(contenido))
        hojas = xl.sheet_names
        df = xl.parse(hoja if hoja in hojas else hojas[0], header=None)
        return {"valores": df.astype(object).where(df.notna(), None).values.tolist(), "filas_ocultas": set(),
                "cols_ocultas": set(), "hoja": hoja or hojas[0]}, hojas
    from vitae.modulos.planes import leer_excel

    m, hojas = leer_excel(contenido, hoja)
    return m, hojas


def plan_importacion(actual: pd.DataFrame, leido: dict[str, Any], *, cobrados_hasta: str = "") -> pd.DataFrame:
    """Reemplaza los meses que trae la planilla. Lo ya cobrado de cada médico y mes se conserva; lo
    nuevo de meses anteriores a ``cobrados_hasta`` queda como cobrado."""
    act = normalizar(actual) if not set(COLUMNAS) <= set(getattr(actual, "columns", [])) else actual.copy()
    regs = pd.DataFrame(leido.get("registros", []))
    if regs.empty:
        return act
    meses_imp = set(regs["periodo"])
    previas = act[act["periodo"].isin(meses_imp)].set_index(["medico", "periodo"])
    filas = []
    for _, r in regs.iterrows():
        k = (r["medico"], r["periodo"])
        p = previas.loc[k] if k in previas.index else None
        if r["condicion"] in (SIN_CARGO, BAJA):
            est = ""
        elif p is not None and p["estado"]:
            est = p["estado"]
        elif r["condicion"] == A_CUENTA:
            est = COMPENSADO
        else:
            est = COBRADO if cobrados_hasta and r["periodo"] < cobrados_hasta else PENDIENTE
        ac = r["alquiler"] if r["condicion"] in (CARGO, A_CUENTA) else 0.0
        filas.append({
            "id_registro": p["id_registro"] if p is not None else _id(), "medico": r["medico"], "periodo": r["periodo"],
            "condicion": r["condicion"], "alquiler": float(r["alquiler"]), "servicio": float(r["servicio"]),
            "estado": est,
            "cobrado": (float(p["cobrado"]) if p is not None and p["estado"] == est and est else
                        (ac if est in (COBRADO, COMPENSADO) else 0.0)),
            "fecha_cobro": p["fecha_cobro"] if p is not None and est else "",
            "medio": p["medio"] if p is not None and est else "",
            "observaciones": r["observaciones"] or (p["observaciones"] if p is not None else ""),
            "orden": int(r["orden"]), "updated_at": _ahora()})
    resto = act[~act["periodo"].isin(meses_imp)].copy()
    orden_nuevo = {f["medico"]: f["orden"] for f in filas}
    resto["orden"] = [orden_nuevo.get(m, o) for m, o in zip(resto["medico"], resto["orden"])]
    return pd.concat([resto, pd.DataFrame(filas, columns=COLUMNAS)], ignore_index=True)


# ---------------------------------------------------------------------------
# Excel como la planilla
# ---------------------------------------------------------------------------
def excel(d: pd.DataFrame, meses_v: list[str]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Alquileres"
    negrita = Font(bold=True)
    linea = Side(style="thin", color="999999")
    borde = Border(left=linea, right=linea, top=linea, bottom=linea)
    verde = PatternFill("solid", fgColor="D9EAD3")
    amarillo = PatternFill("solid", fgColor="FFFF00")
    ws.append(["MEDICO"] + [nombre_mes(m).lower() for m in meses_v])
    for c in ws[1]:
        c.font, c.border = negrita, borde
        c.alignment = Alignment(horizontal="center")
    for med in medicos(d):
        x = d[(d["medico"] == med) & d["periodo"].isin(meses_v)].set_index("periodo")
        if x.empty:
            continue
        fila = [med]
        for m in meses_v:
            if m not in x.index:
                fila.append(None)
            elif x.loc[m, "condicion"] == CARGO and x.loc[m, "alquiler"]:
                fila.append(float(x.loc[m, "alquiler"]))
            else:
                fila.append(texto_celda(x.loc[m, "condicion"], x.loc[m, "alquiler"], x.loc[m, "observaciones"]))
        ws.append(fila)
        for j, m in enumerate(meses_v):
            c = ws.cell(ws.max_row, 2 + j)
            c.border = borde
            if isinstance(c.value, float):
                c.number_format = '"$ "#,##0.00'
            if m in x.index and x.loc[m, "estado"] == COBRADO:
                c.fill = verde
        ws.cell(ws.max_row, 1).border = borde
    ws.append([""] + [f"MAS ${servicio_del_mes(d, m):,.0f} SERV WPP".replace(",", ".") if servicio_del_mes(d, m)
                      else "" for m in meses_v])
    for j in range(len(meses_v)):
        if ws.cell(ws.max_row, 2 + j).value:
            ws.cell(ws.max_row, 2 + j).fill = amarillo
    ac = a_cobrar(d)
    ws.append(["TOTAL ALQUILERES"] + [float(ac[d["periodo"] == m].sum()) for m in meses_v])
    for c in ws[ws.max_row]:
        c.font = negrita
        if isinstance(c.value, float):
            c.number_format = '"$ "#,##0.00'
    ws.column_dimensions["A"].width = 26
    for j in range(len(meses_v)):
        ws.column_dimensions[ws.cell(1, 2 + j).column_letter].width = 16
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------
_CSS = """<style>
.al-wrap{overflow-x:auto;border:1px solid #E8E3E6;border-radius:12px;background:#fff;margin:.3rem 0 .6rem}
.al-tab{border-collapse:collapse;font-size:.82rem;width:max-content;min-width:100%}
.al-tab th,.al-tab td{padding:.32rem .6rem;border-bottom:1px solid #F0ECEE;white-space:nowrap;text-align:right}
.al-tab th{background:#F4F1F3;color:#5E1A39;font-weight:700;position:sticky;top:0}
.al-tab th.l,.al-tab td.l{text-align:left}
.al-tab td.fijo,.al-tab th.fijo{position:sticky;left:0;background:#fff;z-index:1;border-right:1px solid #E8E3E6;
 font-weight:700;color:#3A1426}
.al-tab th.fijo{background:#F4F1F3;z-index:2}
.al-tab td.cob{background:#DDEFD5}.al-tab td.par{background:#FFF1C9}.al-tab td.atr{background:#F6C9C9;color:#8A1F1F;font-weight:700}
.al-tab td.cmp{background:#E3ECF7;color:#2F4F7A}.al-tab td.sc{color:#8A7F85;font-style:italic}
.al-tab td.baja{color:#BD4B4B;font-weight:700}.al-tab td.act{outline:2px solid #8C2853;outline-offset:-2px}
.al-tab td.debe{font-weight:700;color:#8C2853}
.al-tab tr.srv td{background:#FFF8B8;color:#5E4B00;font-weight:600}
.al-tab tr.tot td{font-weight:800;border-top:2px solid #CFC6CB;background:#FAF7F8}
.al-ley{display:flex;gap:1rem;flex-wrap:wrap;font-size:.78rem;color:#6B5F66;margin:.2rem 0 .4rem}
.al-ley i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:4px;vertical-align:-2px;border:1px solid #ddd}
.al-hall{background:#FBF8F9;border:1px solid #EFE7EB;border-radius:12px;padding:.6rem .9rem;margin:.2rem 0 .8rem}
.al-hall li{margin:.15rem 0;color:#3A1426}
.al-sub{color:#8A7F85;font-size:.8rem}
</style>"""

LEYENDA = ('<div class="al-ley"><span><i style="background:#DDEFD5"></i>Cobrado</span>'
           '<span><i style="background:#FFF1C9"></i>Cobrado en parte</span>'
           '<span><i style="background:#F6C9C9"></i>Debe (mes que ya pasó)</span>'
           '<span><i style="background:#E3ECF7"></i>A cuenta</span><span><i style="background:#fff"></i>Por cobrar</span>'
           '<span><i style="background:#fff;outline:2px solid #8C2853"></i>Mes actual</span></div>')


def html_planilla(d: pd.DataFrame, meses_v: list[str], hoy: date | None = None, mostrar_bajas: bool = False) -> str:
    """La grilla como la planilla, con el estado de cobro de cada celda, el servicio WPP y los totales."""
    t = tabla_planilla(d, meses_v, mostrar_bajas)
    if t.empty:
        return '<div class="al-sub">No hay alquileres en esos meses.</div>'
    e = html.escape
    ma = mes_actual(hoy)
    ref = mes_referencia(d, hoy)
    sa = saldo(d)
    cab = ('<tr><th class="l fijo">Médico</th>' + "".join(f"<th>{e(mes_largo(m))}</th>" for m in meses_v) +
           "<th>Debe</th></tr>")
    cuerpo = []
    idx = d.set_index(["medico", "periodo"])
    for _, fila in t.iterrows():
        med = fila["Médico"]
        celdas = []
        for m in meses_v:
            txt = fila[m]
            if not txt:
                celdas.append("<td></td>")
                continue
            r = idx.loc[(med, m)]
            clase = ""
            if r["condicion"] == SIN_CARGO:
                clase = "sc"
            elif r["condicion"] == BAJA:
                clase = "baja"
            elif r["estado"] == COBRADO:
                clase = "cob"
            elif r["estado"] == COMPENSADO:
                clase = "cmp"
            elif r["estado"] == PARCIAL:
                clase = "par" if m >= ref else "atr"
            elif m < ref:
                clase = "atr"
            if m == ma:
                clase += " act"
            tip = r["estado"] or r["condicion"]
            if r["servicio"] and r["condicion"] in (CARGO, A_CUENTA):
                tip += f" · servicio WPP aparte {pesos(r['servicio'], 0)}"
            if r["fecha_cobro"]:
                tip += f" · cobrado {r['fecha_cobro']}"
            celdas.append(f'<td class="{clase}" title="{e(tip)}">{e(txt)}</td>')
        x = d[d["medico"] == med]
        debe = float(sa[x.index][x["periodo"] < ref].sum())
        cuerpo.append(f'<tr><td class="l fijo">{e(med)}</td>{"".join(celdas)}'
                      f'<td class="debe">{pesos(debe, 0) if debe > 0.5 else ""}</td></tr>')
    srv = [servicio_del_mes(d, m) for m in meses_v]
    if any(srv):
        cuerpo.append('<tr class="srv"><td class="l fijo">Servicio WPP (aparte)</td>' +
                      "".join(f"<td>{pesos(v, 0) + ' c/u' if v else ''}</td>" for v in srv) + "<td></td></tr>")
    ac, ce = a_cobrar(d), cobrado_efectivo(d)
    tot = [float(ac[d["periodo"] == m].sum()) for m in meses_v]
    cob = [float(ce[d["periodo"] == m].sum()) for m in meses_v]
    deuda = float(sa[d["periodo"] < ref].sum())
    cuerpo.append('<tr class="tot"><td class="l fijo">Alquileres a cobrar</td>' + "".join(f"<td>{pesos(v)}</td>" for v in tot) +
                  f'<td class="debe">{pesos(deuda, 0) if deuda > 0.5 else ""}</td></tr>')
    cuerpo.append('<tr><td class="l fijo">Cobrado</td>' + "".join(f"<td>{pesos(v)}</td>" for v in cob) + "<td></td></tr>")
    return f'<div class="al-wrap"><table class="al-tab">{cab}{"".join(cuerpo)}</table></div>'


def render_alquileres(raw: pd.DataFrame | None, *, guardar: Callable[[pd.DataFrame], Any],
                      leer_cajas: Callable[[], dict[str, pd.DataFrame | None]] | None = None,
                      selector: Callable[..., str] | None = None,
                      mostrar_error: Callable[[str, Exception], None] | None = None,
                      hoy: date | None = None) -> None:
    import streamlit as st

    from vitae.core.ui import tarjetas_kpi

    d = normalizar(raw)
    hoy = hoy or date.today()
    ma = mes_actual(hoy)
    k = "alq_"
    st.markdown(_CSS, unsafe_allow_html=True)
    rev = int(st.session_state.get(f"{k}rev", 0))

    def _guardar(nuevo: pd.DataFrame, aviso: str) -> None:
        try:
            guardar(para_guardar(nuevo))
        except Exception as exc:
            (mostrar_error or (lambda m, e: st.error(f"{m} {e}")))("No se pudieron guardar los cambios.", exc)
            return
        st.session_state[f"{k}rev"] = rev + 1
        st.toast(aviso, icon=":material/check_circle:")
        st.rerun()

    r = resumen(d, hoy)
    if r.get("hay"):
        mt = mes_largo(r["mes"])
        tarjetas_kpi([
            (f"Alquileres a cobrar {mt}", pesos(r["a_cobrar"], 0),
             f"Servicio WPP aparte: {pesos(r['servicio'], 0)}"
             + (f" ({r['con_servicio']} × {pesos(r['servicio_valor'], 0)})" if r["servicio_valor"] else ""), "#8C2853"),
            ("Cobrado", pesos(r["cobrado"], 0), f"Falta {pesos(r['pendiente'], 0)} · {r['por_cobrar_mes']} médico(s)",
             "#5E9C76"),
            ("Deben meses anteriores", pesos(r["adeudado"], 0), f"{len(r['deudores'])} médico(s)",
             "#BD4B4B" if r["adeudado"] > 0.5 else "#8A7F85"),
            ("Consultorios", f"{r['con_cargo']} con cargo",
             f"{r['sin_cargo']} sin cargo · {r['a_cuenta']} a cuenta"
             + (f" · aumento {r['aumento']:+.1f}%" if r["aumento"] is not None else ""), "#4F7CAC"),
        ])
        if r["falta_mes"]:
            st.markdown(f'<div style="background:#FFF7EC;border-left:4px solid #C9895B;border-radius:10px;padding:.5rem .8rem;'
                        f'margin:.2rem 0 .6rem">📅 Falta cargar <b>{html.escape(mes_largo(r["proximo"]))}</b>. En «Nuevo mes» '
                        f'se copia el anterior con el aumento.</div>', unsafe_allow_html=True)

    if selector:
        seccion = selector("Sección", SECCIONES, key=f"{k}seccion")
    else:
        seccion = st.segmented_control("Sección", SECCIONES, default=SECCIONES[0], key=f"{k}seccion",
                                       label_visibility="collapsed") or SECCIONES[0]
    todos = meses(d)

    # ---- Planilla -----------------------------------------------------------
    if seccion == "Planilla":
        if not todos:
            st.info("Todavía no hay alquileres. Traé la planilla en «Importar» o cargá los médicos en «Médicos».")
            return
        ref = mes_referencia(d, hoy)
        opciones = {"Últimos 6 meses": _mes_mas(ref, -5), "Este año": f"{ref[:4]}-01", "Últimos 12 meses":
                    _mes_mas(ref, -11), "Todo": todos[0]}
        c1, c2 = st.columns([1.4, 1])
        with c1:
            rango = st.pills("Meses", list(opciones), default="Últimos 6 meses", key=f"{k}rango") or "Últimos 6 meses"
        meses_v = [m for m in todos if m >= opciones[rango]]
        with c2:
            st.download_button("Descargar Excel (como la planilla)", data=excel(d, todos),
                               file_name=f"alquileres_{ma}.xlsx", key=f"{k}xls", icon=":material/download:",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True)
        bajas_v = st.toggle("Mostrar los que se dieron de baja", value=False, key=f"{k}bajas")
        st.markdown(LEYENDA, unsafe_allow_html=True)
        st.markdown(html_planilla(d, meses_v, hoy, bajas_v), unsafe_allow_html=True)
        hl = hallazgos(d, hoy)
        if hl:
            st.markdown('<div class="al-hall"><ul style="margin:0;padding-left:1.1rem">' +
                        "".join(f"<li>{html.escape(h)}</li>" for h in hl) + "</ul></div>", unsafe_allow_html=True)
        with st.expander("Editar la planilla", icon=":material/edit:"):
            st.caption("Escribí como en la planilla: un importe ($ 584.000), SIN CARGO, BAJA o A CTA INFORMES. "
                       "Vacío = no figura ese mes. Agregá un médico en la última fila. Lo cobrado no se pierde.")
            original = tabla_planilla(d, meses_v, True)
            vista = original.rename(columns={m: mes_largo(m) for m in meses_v})
            ed = st.data_editor(vista, hide_index=True, use_container_width=True, num_rows="dynamic",
                                key=f"{k}ed_{rango}_{rev}",
                                column_config={"Médico": st.column_config.TextColumn(required=True, width="medium")})
            if st.button("Guardar la planilla", type="primary", key=f"{k}ed_ok", icon=":material/save:"):
                ed = ed.rename(columns={mes_largo(m): m for m in meses_v})
                nuevo, n = aplicar_planilla(d, original, ed, meses_v)
                if n:
                    _guardar(nuevo, f"{n} celda(s) actualizada(s)")
                else:
                    st.info("No hay cambios.")
        return

    # ---- Cobros --------------------------------------------------------------
    if seccion == "Cobros":
        if not todos:
            st.info("Todavía no hay alquileres.")
            return
        ref = mes_referencia(d, hoy)
        opciones_m = sorted(set(todos), reverse=True)
        mes = st.selectbox("Mes", opciones_m, index=opciones_m.index(ref), key=f"{k}c_mes", format_func=mes_largo)
        sa = saldo(d)
        x = d[(d["periodo"] == mes) & d["condicion"].isin([CARGO, A_CUENTA])]
        atras = d[(d["periodo"] < mes) & (sa > 0.5)]
        encontrados: dict[str, list[dict[str, Any]]] = {}
        if leer_cajas:
            try:
                encontrados = cobros_en_caja(d, mes, leer_cajas())
            except Exception:
                encontrados = {}
        z = pd.concat([atras.assign(_atr=True), x.assign(_atr=False)])
        if z.empty:
            st.info("No hay nada para cobrar en ese mes.")
            return
        ac = a_cobrar(z)
        tabla = pd.DataFrame({
            "id": z["id_registro"], "Médico": z["medico"], "Mes": z["periodo"].map(etiqueta_mes),
            "A cobrar": ac,
            "Estado": z["estado"], "Cobrado": z["cobrado"].where(z["estado"] == PARCIAL, ac.where(
                z["estado"].isin([COBRADO, COMPENSADO]), 0.0)),
            "Fecha": z["fecha_cobro"], "Medio": z["medio"], "Notas": z["observaciones"],
            "En caja": [" · ".join(f"{m_['caja']} {pesos(m_['importe'], 0)}" for m_ in encontrados.get(med, []))
                        if not atr else "" for med, atr in zip(z["medico"], z["_atr"])],
        }).reset_index(drop=True)
        st.caption("Cambiá el estado de cada uno y guardá (Cobrado = todo; Parcial = poné cuánto). Arriba figuran "
                   "los meses anteriores que siguen sin cobrar. «En caja» muestra las entradas de alquiler que "
                   "encontré en las cajas con ese apellido.")
        if not leer_cajas or not any(tabla["En caja"]):
            tabla = tabla.drop(columns="En caja")
        ed = st.data_editor(tabla, hide_index=True, use_container_width=True, key=f"{k}c_ed_{mes}_{rev}",
                            disabled=["id", "Médico", "Mes", "A cobrar", "En caja"],
                            column_config={
                                "id": None,
                                "A cobrar": st.column_config.NumberColumn(format="localized", help="Alquiler (sin el servicio WPP)"),
                                "Cobrado": st.column_config.NumberColumn(format="localized", min_value=0.0),
                                "Estado": st.column_config.SelectboxColumn(options=ESTADOS, required=True),
                                "Medio": st.column_config.SelectboxColumn(options=MEDIOS),
                                "Fecha": st.column_config.TextColumn(help="dd/mm/aaaa")})
        tot = float(tabla["A cobrar"].sum())
        st.caption(f"Total {pesos(tot)} · cobrado {pesos(float(ed['Cobrado'].sum()))}")
        sug = sugerencias_caja(d, mes, encontrados)
        b1, b2, b3 = st.columns(3)
        if b1.button("Guardar cobros", type="primary", key=f"{k}c_ok", icon=":material/save:", use_container_width=True):
            nuevo = d.copy()
            hoy_t = hoy.strftime("%d/%m/%Y")
            for (_, f), (_, o) in zip(ed.iterrows(), tabla.iterrows()):
                if (f["Estado"], float(f["Cobrado"] or 0), _texto(f["Fecha"]), _texto(f["Medio"]), _texto(f["Notas"])) == \
                        (o["Estado"], float(o["Cobrado"] or 0), _texto(o["Fecha"]), _texto(o["Medio"]), _texto(o["Notas"])):
                    continue
                est = f["Estado"]
                cob = float(f["Cobrado"] or 0)
                if est == PENDIENTE and cob > 0.5 and abs(cob - float(o["Cobrado"] or 0)) > 0.5:
                    est = PARCIAL  # anotó un importe: cobrado en parte
                fecha = _texto(f["Fecha"]) or (hoy_t if est != PENDIENTE else "")
                nuevo = marcar(nuevo, [f["id"]], est, cobrado=cob if est == PARCIAL else None, fecha=fecha,
                               medio=_texto(f["Medio"]))
                nuevo.loc[nuevo["id_registro"] == f["id"], "observaciones"] = _texto(f["Notas"])
            _guardar(nuevo, "Cobros guardados")
        if b2.button(f"Todo {mes_largo(mes).split()[0]} cobrado", key=f"{k}c_todos", icon=":material/done_all:",
                     use_container_width=True):
            ids = list(x.loc[x["estado"].isin([PENDIENTE, PARCIAL]), "id_registro"])
            _guardar(marcar(d, ids, COBRADO, fecha=hoy.strftime("%d/%m/%Y")), "Mes cobrado")
        if b3.button(f"Tomar de las cajas ({len(sug)})", key=f"{k}c_caja", icon=":material/account_balance_wallet:",
                     disabled=not sug, use_container_width=True,
                     help="Marca como cobrados los que tienen la entrada en la caja (o parcial si es menos)."):
            nuevo = d.copy()
            for s in sug:
                nuevo = marcar(nuevo, [s["id"]], s["estado"], cobrado=s["cobrado"] if s["estado"] == PARCIAL else None,
                               fecha=s["fecha"], medio=s["caja"])
            _guardar(nuevo, f"{len(sug)} cobro(s) tomados de las cajas")
        if len(r.get("deudores", [])) and mes == ref:
            st.markdown("##### Quién debe")
            dd = r["deudores"].rename(columns={"medico": "Médico", "deuda": "Debe", "meses": "Meses"})
            st.dataframe(dd.assign(Debe=dd["Debe"].map(pesos)), hide_index=True, use_container_width=True)
        return

    # ---- Nuevo mes -------------------------------------------------------------
    if seccion == "Nuevo mes":
        if not todos:
            st.info("Primero traé la planilla en «Importar» o cargá los médicos en «Médicos».")
            return
        sig = _mes_mas(todos[-1], 1)
        opciones_m = sorted({sig, ma, _mes_mas(ma, 1)} | set(todos[-3:]))
        c1, c2, c3, c4 = st.columns([1.2, 1, 1, 1])
        mes = c1.selectbox("Mes", opciones_m, index=opciones_m.index(sig), key=f"{k}n_mes", format_func=mes_largo)
        ult = aumento(d, todos[-1])
        pct = c2.number_input("Aumento %", value=0.0, step=0.5, format="%.2f", key=f"{k}n_pct",
                              help=f"El último mes el aumento típico fue {ult:+.1f}%." if ult is not None else None)
        red = c3.selectbox("Redondear a", [1000, 500, 100, 0], key=f"{k}n_red",
                           format_func=lambda v: f"$ {v:,.0f}".replace(",", ".") if v else "Sin redondeo")
        srv = c4.number_input("Servicio WPP", value=float(servicio_del_mes(d, todos[-1])), step=1000.0, format="%.2f",
                              key=f"{k}n_srv")
        base = generar_mes(d, mes, aumento_pct=float(pct), redondeo=int(red), servicio=float(srv))
        if base.empty:
            st.info("No hay un mes anterior para copiar.")
            return
        prev = [m for m in todos if m < mes][-1]
        anterior = d[d["periodo"] == prev].set_index("medico")["alquiler"]
        tabla = pd.DataFrame({"Médico": base["medico"], "Condición": base["condicion"],
                              f"{mes_largo(prev)}": [float(anterior.get(m, 0.0)) for m in base["medico"]],
                              "Alquiler": base["alquiler"], "Servicio": base["servicio"],
                              "Nota": base["observaciones"]}).reset_index(drop=True)
        if mes in todos:
            st.warning(f"{mes_largo(mes).capitalize()} ya está cargado: se actualizan los valores (lo cobrado se conserva).")
        st.caption("Revisá y cambiá lo que haga falta. Se copian los médicos del último mes (menos las bajas). "
                   "Agregá alguno nuevo en la última fila.")
        ed = st.data_editor(tabla, hide_index=True, use_container_width=True, num_rows="dynamic",
                            key=f"{k}n_ed_{mes}_{pct}_{red}_{srv}_{rev}", disabled=[f"{mes_largo(prev)}"],
                            column_config={
                                "Médico": st.column_config.TextColumn(required=True),
                                "Condición": st.column_config.SelectboxColumn(options=CONDICIONES, required=True),
                                f"{mes_largo(prev)}": st.column_config.NumberColumn(format="localized"),
                                "Alquiler": st.column_config.NumberColumn(format="localized", min_value=0.0),
                                "Servicio": st.column_config.NumberColumn(format="localized", min_value=0.0)})
        ed = ed[ed["Médico"].map(nombre_medico) != ""]
        con = ed["Condición"].isin([CARGO, A_CUENTA])
        tot = float(ed["Alquiler"].fillna(0)[con].sum())
        st.caption(f"{len(ed)} médicos · alquileres a cobrar {pesos(tot)} · servicio WPP aparte "
                   f"{pesos(float(ed['Servicio'].fillna(0)[con].sum()), 0)}")
        if st.button(f"Guardar {mes_largo(mes)}", type="primary", key=f"{k}n_ok", icon=":material/save:"):
            filas = pd.DataFrame({"medico": ed["Médico"].map(nombre_medico), "periodo": mes,
                                  "condicion": ed["Condición"].fillna(CARGO), "alquiler": ed["Alquiler"].fillna(0.0),
                                  "servicio": ed["Servicio"].fillna(0.0), "observaciones": ed["Nota"].fillna("")})
            _guardar(guardar_mes(d, filas), f"{mes_largo(mes).capitalize()} cargado")
        return

    # ---- Médicos ---------------------------------------------------------------
    if seccion == "Médicos":
        rm = resumen_medicos(d, hoy)
        if len(rm):
            filtro = st.pills("Mostrar", ["Activos", "Todos", "Bajas"], default="Activos", key=f"{k}m_filtro") or "Activos"
            v = rm if filtro == "Todos" else (rm[rm["Situación"] == BAJA] if filtro == "Bajas" else rm[rm["Situación"] != BAJA])
            st.dataframe(v.assign(**{c: v[c].map(lambda n: pesos(n, 0) if n else "") for c in
                                     ("Alquiler", "Servicio", "Debe", "Cobrado en el año")}),
                         hide_index=True, use_container_width=True,
                         column_config={"Meses sin aumento": st.column_config.NumberColumn(format="%d")})
        ref = mes_referencia(d, hoy) if todos else ma
        meses_op = sorted(set(todos) | {ma, _mes_mas(ma, 1)})
        t1, t2, t3 = st.tabs(["Alta", "Baja", "Cambiar nombre o borrar"])
        with t1:
            c1, c2 = st.columns([1.6, 1])
            nom = c1.text_input("Médico", key=f"{k}a_nom_{rev}", placeholder="Ej.: PEREZ (CONSULTORIO 3)")
            mes_a = c2.selectbox("Desde", meses_op, index=meses_op.index(ref) if ref in meses_op else 0,
                                 key=f"{k}a_mes_{rev}", format_func=mes_largo)
            c3, c4, c5 = st.columns(3)
            cond = c3.selectbox("Condición", CONDICIONES[:3], key=f"{k}a_cond_{rev}")
            alq = c4.number_input("Alquiler", min_value=0.0, step=1000.0, format="%.2f", key=f"{k}a_alq_{rev}",
                                  disabled=cond == SIN_CARGO)
            srv = c5.number_input("Servicio WPP", min_value=0.0, step=1000.0, format="%.2f",
                                  value=float(servicio_del_mes(d, mes_a)) if cond == CARGO else 0.0,
                                  key=f"{k}a_srv_{rev}_{cond}", disabled=cond == SIN_CARGO)
            existe = nombre_medico(nom) in set(d["medico"])
            if existe:
                st.warning("Ya hay un médico con ese nombre: se le carga ese mes.")
            if st.button("Dar de alta", type="primary", key=f"{k}a_ok", icon=":material/person_add:",
                         disabled=not nombre_medico(nom)):
                _guardar(alta(d, nom, mes_a, cond, float(alq), float(srv)), f"{nombre_medico(nom)} dado de alta")
        lista = medicos(d)
        with t2:
            if not lista:
                st.info("No hay médicos.")
            else:
                c1, c2 = st.columns([1.6, 1])
                med = c1.selectbox("Médico", lista, key=f"{k}b_med")
                mes_b = c2.selectbox("Mes de la baja", meses_op, index=meses_op.index(ref) if ref in meses_op else 0,
                                     key=f"{k}b_mes", format_func=mes_largo)
                st.caption("Ese mes figura como BAJA y se borran los meses siguientes que estén cargados.")
                if st.button("Dar de baja", key=f"{k}b_ok", icon=":material/person_remove:"):
                    _guardar(baja(d, med, mes_b), f"{med}: baja desde {mes_largo(mes_b)}")
        with t3:
            if lista:
                c1, c2 = st.columns(2)
                med = c1.selectbox("Médico", lista, key=f"{k}r_med")
                nuevo_n = c2.text_input("Nuevo nombre", value=med, key=f"{k}r_nom_{med}")
                b1, b2 = st.columns(2)
                if b1.button("Guardar nombre", key=f"{k}r_ok", icon=":material/save:", use_container_width=True,
                             disabled=nombre_medico(nuevo_n) in ("", med)):
                    _guardar(renombrar(d, med, nuevo_n), "Nombre cambiado")
                conf = st.session_state.get(f"{k}r_conf") == med
                if b2.button("Confirmar: borrar todo" if conf else "Borrar el médico y su historia", key=f"{k}r_del",
                             icon=":material/delete:", use_container_width=True):
                    if conf:
                        st.session_state.pop(f"{k}r_conf", None)
                        _guardar(borrar_medico(d, med), f"{med} borrado")
                    else:
                        st.session_state[f"{k}r_conf"] = med
                        st.rerun()
                if conf:
                    st.warning("Tocá de nuevo para borrar todos sus meses. Para que deje de alquilar, mejor usá «Baja».")
        return

    # ---- Análisis ---------------------------------------------------------------
    if seccion == "Análisis":
        ev = evolucion(d)
        if ev.empty:
            st.info("Todavía no hay alquileres.")
            return
        hl = hallazgos(d, hoy)
        if hl:
            st.markdown('<div class="al-hall"><ul style="margin:0;padding-left:1.1rem">' +
                        "".join(f"<li>{html.escape(h)}</li>" for h in hl) + "</ul></div>", unsafe_allow_html=True)
        st.markdown("##### A cobrar y cobrado por mes")
        st.bar_chart(ev.set_index("Mes")[["Cobrado", "Pendiente"]], stack=True, color=["#5E9C76", "#BD4B4B"])
        st.dataframe(ev.drop(columns="_mes").assign(**{c: ev[c].map(lambda v: pesos(v, 0)) for c in
                                                       ("A cobrar", "Cobrado", "Pendiente", "Servicio WPP c/u",
                                                        "Servicio WPP total")},
                                                    **{"Aumento típico": ev["Aumento típico"].map(
                                                        lambda v: f"{v:+.1f}%" if pd.notna(v) else "")}),
                     hide_index=True, use_container_width=True)
        ref = mes_referencia(d, hoy)
        x = d[(d["periodo"] == ref) & (d["condicion"] == CARGO) & (d["alquiler"] > 0)]
        if len(x):
            st.markdown(f"##### Alquiler de cada médico ({mes_largo(ref)})")
            st.bar_chart(x.set_index("medico")["alquiler"].sort_values(ascending=False), horizontal=True,
                         color="#8C2853")
        meds = medicos(d)
        if meds:
            st.markdown("##### Historia de un médico")
            med = st.selectbox("Médico", meds, key=f"{k}h_med")
            h = d[d["medico"] == med].sort_values("periodo")
            sa_h = saldo(d)[h.index]
            st.dataframe(pd.DataFrame({
                "Mes": h["periodo"].map(mes_largo), "Condición": h["condicion"],
                "Alquiler": h["alquiler"].map(lambda v: pesos(v, 0) if v else ""),
                "Servicio": h["servicio"].map(lambda v: pesos(v, 0) if v else ""),
                "Estado": h["estado"], "Cobrado el": h["fecha_cobro"], "Debe": sa_h.map(lambda v: pesos(v, 0) if v > 0.5 else ""),
                "Notas": h["observaciones"]}), hide_index=True, use_container_width=True)
        return

    # ---- Importar ------------------------------------------------------------------
    if seccion == "Importar":
        st.caption("Traé la planilla de alquileres: subí el Excel (respeta filas y columnas ocultas) o copiá las celdas "
                   "en Google Sheets / Excel (desde «MEDICO» y los meses) y pegalas acá.")
        modo = st.pills("Desde", ["Archivo", "Pegar"], default="Archivo", key=f"{k}i_modo") or "Archivo"
        m_ = None
        if modo == "Archivo":
            arch = st.file_uploader("Planilla de alquileres", type=["xlsx", "xls", "csv"], key=f"{k}i_arch_{rev}")
            if arch is not None:
                try:
                    m_, hojas = leer_archivo(arch.name, arch.getvalue())
                    if len(hojas) > 1:
                        hoja = st.selectbox("Hoja", hojas, index=hojas.index(m_["hoja"]) if m_["hoja"] in hojas else 0,
                                            key=f"{k}i_hoja")
                        if hoja != m_["hoja"]:
                            m_, _ = leer_archivo(arch.name, arch.getvalue(), hoja)
                except Exception as exc:
                    (mostrar_error or (lambda m, e: st.error(f"{m} {e}")))("No pude leer el archivo.", exc)
                    return
        else:
            texto = st.text_area("Pegá acá", height=200, key=f"{k}i_pegar_{rev}",
                                 placeholder="MEDICO\tmarzo 2026\tabril 2026\nSANCHEZ WILDE\t$ 458.000,00\t$ 486.000,00")
            if texto.strip():
                m_ = {"valores": leer_pegado(texto), "filas_ocultas": set(), "cols_ocultas": set()}
        if not m_:
            return
        c1, c2 = st.columns(2)
        inc_f = c1.toggle("Incluir filas ocultas", value=True, key=f"{k}i_filas",
                          disabled=not m_.get("filas_ocultas"))
        inc_c = c2.toggle("Incluir meses ocultos", value=True, key=f"{k}i_cols", disabled=not m_.get("cols_ocultas"))
        leido = leer_tabla(m_["valores"], filas_ocultas=m_.get("filas_ocultas"), cols_ocultas=m_.get("cols_ocultas"),
                           incluir_filas_ocultas=inc_f, incluir_meses_ocultos=inc_c)
        if leido.get("error"):
            st.error(leido["error"])
            return
        if not leido["registros"]:
            st.warning("No encontré médicos con valores.")
            return
        meses_l = leido["meses"]
        st.markdown(f"**{len(leido['medicos'])} médicos · {len(meses_l)} meses** "
                    f"({mes_largo(meses_l[0])} a {mes_largo(meses_l[-1])})")
        if leido["repetidos"]:
            st.caption("Repetidos en la planilla (se guardan como dos): " +
                       ", ".join(f"{n} y {n} (2)" for n in leido["repetidos"]))
        ctrl = pd.DataFrame([{"Mes": mes_largo(m), "Con cargo": t["con_cargo"], "Alquileres": pesos(t["alquileres"]),
                              "Servicio WPP c/u (aparte)": pesos(t["servicio"], 0) if t["servicio"] else ""}
                             for m, t in leido["totales"].items()])
        st.dataframe(ctrl, hide_index=True, use_container_width=True)
        cob_op = ["Ninguno"] + [m for m in meses_l if m <= ma]
        defecto = ma if ma in cob_op else cob_op[-1]
        cob = st.selectbox("¿Hasta qué mes ya está todo cobrado?", cob_op, index=cob_op.index(defecto),
                           key=f"{k}i_cob", format_func=lambda m: m if m == "Ninguno" else f"Cobrado todo antes de {mes_largo(m)}",
                           help="Los meses anteriores quedan como cobrados (si ya anotaste cobros en Vitae, se conservan).")
        prev = plan_importacion(pd.DataFrame(columns=COLUMNAS), leido, cobrados_hasta="" if cob == "Ninguno" else cob)
        st.markdown(html_planilla(prev, meses_l[-6:], hoy, True), unsafe_allow_html=True)
        st.caption("Se reemplazan los meses que trae la planilla; los demás meses no se tocan.")
        if st.button("Guardar la planilla", type="primary", key=f"{k}i_ok", icon=":material/upload:"):
            nuevo = plan_importacion(d, leido, cobrados_hasta="" if cob == "Ninguno" else cob)
            _guardar(nuevo, f"Planilla importada: {len(leido['medicos'])} médicos, {len(meses_l)} meses")
