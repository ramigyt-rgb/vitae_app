"""Acciones de Luz: cosas que la asistente puede HACER en la app, no sólo consultar.

Cómo funciona (igual para todas las acciones):

1. Luz entiende el pedido y llama a una herramienta ``proponer_…`` con los
   datos. La herramienta valida, completa lo que puede (por ejemplo, escribe el
   médico o el procedimiento igual que ya están cargados) y revisa conflictos.
   NO escribe nada: deja la acción «pendiente».
2. El chat muestra una tarjeta con lo que se va a cargar y los botones
   «Confirmar» / «Cancelar».
3. Sólo al tocar «Confirmar» se ejecuta (se vuelven a leer los datos y se
   vuelve a controlar todo antes de guardar).

Cada acción respeta los permisos: Luz sólo puede proponerla si el usuario
tiene acceso al módulo. Para sumar una acción nueva: una función
``proponer_x`` en ``herramientas()`` y su ``_ejecutar_x`` en ``EJECUTORES``.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Callable

import pandas as pd

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _norm(valor: Any) -> str:
    t = unicodedata.normalize("NFKD", str(valor if valor is not None else "").strip().lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


def _texto(valor: Any) -> str:
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    t = str(valor).strip()
    return "" if t.lower() in {"nan", "none", "nat"} else t


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def fecha_larga(ts: pd.Timestamp) -> str:
    return f"{_DIAS[ts.weekday()]} {ts.day}/{ts.month}/{ts.year}"


def leer_fecha(valor: Any, hoy: date | None = None) -> pd.Timestamp:
    """AAAA-MM-DD, DD/MM/AAAA, DD/MM (año actual o siguiente), «hoy», «mañana»."""
    hoy = hoy or date.today()
    t = _texto(valor).lower()
    if not t:
        return pd.NaT
    if t in {"hoy"}:
        return pd.Timestamp(hoy)
    if t in {"manana", "mañana"}:
        return pd.Timestamp(hoy + timedelta(days=1))
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ t].*)?", t)
    if m:
        return pd.Timestamp(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", t)
    if m:
        anio = int(m[3]) + (2000 if m[3] and len(m[3]) == 2 else 0) if m[3] else hoy.year
        try:
            f = pd.Timestamp(anio, int(m[2]), int(m[1]))
        except ValueError:
            return pd.NaT
        if not m[3] and f.date() < hoy:
            f = f.replace(year=anio + 1)
        return f
    return pd.NaT


def leer_hora(valor: Any) -> str:
    t = _texto(valor).lower().replace("hs", "").replace("h", ":").strip()
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?:?", t)
    if not m:
        return ""
    hh, mm = int(m[1]), int(m[2] or 0)
    return f"{hh:02d}:{mm:02d}" if 0 <= hh < 24 and 0 <= mm < 60 else ""


def _minutos(hhmm: str) -> int | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", _texto(hhmm))
    return int(m[1]) * 60 + int(m[2]) if m else None


def _como_esta_cargado(valor: str, existentes: list[str]) -> str:
    """Escribe el nombre igual que ya está en la planilla («perez» → «Dr. Pérez»)."""
    v = _norm(valor)
    if not v:
        return valor
    unicos = list(dict.fromkeys(e for e in existentes if _texto(e)))
    exactos = [e for e in unicos if _norm(e) == v]
    if exactos:
        return exactos[0]
    parecidos = [e for e in unicos if v in _norm(e) or (len(_norm(e)) > 3 and _norm(e) in v)]
    return parecidos[0] if len(parecidos) == 1 else valor.strip()


# ---------------------------------------------------------------------------
# Agenda de quirófano (mismo mapeo de columnas que la pantalla de la agenda)
# ---------------------------------------------------------------------------
MODULO_AGENDA = "Agenda Quirófano"
TABLA_AGENDA = "agenda_quirofano"
_ALIAS_AGENDA = {
    "fecha": ["fecha", "fecha_cirugia", "fecha_procedimiento", "fecha_turno", "dia"],
    "hora_inicio": ["hora_inicio", "hora", "inicio", "horario", "hora_desde"],
    "hora_fin": ["hora_fin", "fin", "hora_hasta"],
    "duracion_min": ["duracion_min", "duracion", "duracion_minutos", "minutos"],
    "sala": ["sala", "quirofano", "quirofano_sala", "sala_quirofano"],
    "paciente": ["paciente", "nombre_paciente", "apellido_y_nombre", "apellido_nombre", "afiliado"],
    "procedimiento": ["procedimiento", "practica", "cirugia", "prestacion"],
    "medico": ["medico", "medico_responsable", "profesional", "cirujano"],
    "estado": ["estado", "estado_agenda", "situacion"],
    "anestesista": ["anestesista", "anestesiologo", "anestesia"],
    "obra_social": ["obra_social", "obra_social_prepaga", "cobertura", "financiador"],
    "telefono": ["telefono", "celular", "contacto", "whatsapp"],
    "observaciones": ["observaciones", "observacion", "notas", "nota"],
    "created_at": ["created_at", "creado", "fecha_creacion"],
    "updated_at": ["updated_at", "actualizado", "fecha_actualizacion"],
}
_ESTADOS_OCUPAN = {"autorizado", "programado", "confirmado", "en_quirofano", "reprogramado"}
ESTADOS_AGENDA = ["Consulta", "Pendiente de autorización", "Autorizado", "Programado", "Confirmado", "Reprogramado"]


def _posiciones(df: pd.DataFrame) -> dict[str, int | None]:
    cols = [_norm(c) for c in df.columns]
    return {campo: next((i for i, c in enumerate(cols) if c in {_norm(a) for a in alias + [campo]}), None)
            for campo, alias in _ALIAS_AGENDA.items()}


def _agenda(df: pd.DataFrame | None) -> pd.DataFrame:
    """Vista canónica de la agenda (una columna por campo)."""
    df = df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    pos = _posiciones(df)
    out = pd.DataFrame(index=range(len(df)))
    for campo, p in pos.items():
        out[campo] = df.iloc[:, p].map(_texto).values if p is not None else ""
    crudas = df.iloc[:, pos["fecha"]].tolist() if pos.get("fecha") is not None else [""] * len(df)
    out["_fecha"] = [pd.Timestamp(v).normalize() if isinstance(v, (datetime, date)) and pd.notna(v) else leer_fecha(v)
                     for v in crudas]
    ini = [_minutos(leer_hora(v)) for v in out["hora_inicio"]]
    dur = pd.to_numeric(out["duracion_min"], errors="coerce").fillna(60).tolist()
    fin = [_minutos(leer_hora(v)) for v in out["hora_fin"]]
    out["_ini"] = pd.Series(ini, dtype="object").values
    out["_fin"] = pd.Series([f if f is not None else (i + int(d) if i is not None else None)
                             for f, i, d in zip(fin, ini, dur)], dtype="object").values
    return out


def _choques(agenda: pd.DataFrame, fecha: pd.Timestamp, sala: str, medico: str, ini: int, fin: int) -> list[str]:
    dia = agenda[agenda["_fecha"].notna() & agenda["_fecha"].eq(fecha) & agenda["estado"].map(_norm).isin(_ESTADOS_OCUPAN)]
    avisos = []
    for _, r in dia.iterrows():
        if r["_ini"] is None or r["_fin"] is None or not (r["_ini"] < fin and ini < r["_fin"]):
            continue
        horario = f"{r['hora_inicio']}–{r['hora_fin'] or '?'}"
        if sala and _norm(r["sala"]) == _norm(sala):
            avisos.append(f"{r['sala']} ya está ocupado {horario} ({r['paciente'] or 'sin nombre'})")
        if medico and _norm(r["medico"]) == _norm(medico):
            avisos.append(f"{r['medico']} ya opera {horario} ({r['paciente'] or 'sin nombre'})")
    return list(dict.fromkeys(avisos))


def proponer_cirugia_datos(agenda_raw: pd.DataFrame, *, paciente: str, fecha: str, hora: str, medico: str,
                           procedimiento: str, duracion_min: int = 60, quirofano: str = "", anestesista: str = "",
                           obra_social: str = "", telefono: str = "", observaciones: str = "",
                           estado: str = "Programado", hoy: date | None = None) -> dict[str, Any]:
    """Valida y arma la cirugía. Devuelve {"ok": bool, "faltan": [...], "accion": {...}}."""
    hoy = hoy or date.today()
    agenda = _agenda(agenda_raw)
    f = leer_fecha(fecha, hoy)
    h = leer_hora(hora)
    faltan = [n for n, v in (("paciente", _texto(paciente)), ("fecha", pd.notna(f)), ("hora", h),
                             ("médico", _texto(medico)), ("procedimiento", _texto(procedimiento))) if not v]
    if faltan:
        return {"ok": False, "faltan": faltan}
    salas = [s for s in agenda["sala"] if s]
    sala = _como_esta_cargado(quirofano, salas) if _texto(quirofano) else (
        pd.Series(salas).value_counts().index[0] if salas else "Quirófano 1")
    medico = _como_esta_cargado(medico, list(agenda["medico"]))
    procedimiento = _como_esta_cargado(procedimiento, list(agenda["procedimiento"]))
    anestesista = _como_esta_cargado(anestesista, list(agenda["anestesista"])) if _texto(anestesista) else ""
    obra_social = _como_esta_cargado(obra_social, list(agenda["obra_social"])) if _texto(obra_social) else ""
    dur = max(5, min(int(duracion_min or 60), 24 * 60))
    ini = _minutos(h)
    fin = ini + dur
    avisos = []
    if fin > 24 * 60:
        return {"ok": False, "faltan": [], "error": "La cirugía terminaría después de medianoche."}
    if f.date() < hoy:
        avisos.append(f"La fecha ({f.day}/{f.month}/{f.year}) ya pasó.")
    avisos += _choques(agenda, f, sala, medico, ini, fin)
    est = next((e for e in ESTADOS_AGENDA if _norm(e) == _norm(estado)), "Programado")
    datos = {
        "fecha": f.strftime("%d/%m/%Y"), "hora_inicio": h, "hora_fin": f"{fin // 60:02d}:{fin % 60:02d}",
        "duracion_min": dur, "sala": sala, "paciente": _texto(paciente), "procedimiento": procedimiento,
        "medico": medico, "estado": est, "anestesista": anestesista, "obra_social": obra_social,
        "telefono": _texto(telefono), "observaciones": _texto(observaciones),
    }
    resumen = (f"{datos['paciente']} · {procedimiento} con {medico} · {fecha_larga(f)} "
               f"{h}–{datos['hora_fin']} · {sala}")
    return {"ok": True, "faltan": [], "accion": {
        "tipo": "agendar_cirugia", "modulo": MODULO_AGENDA, "tabla": TABLA_AGENDA, "titulo": "Programar cirugía",
        "datos": datos, "resumen": resumen, "avisos": avisos, "estado": "pendiente"}}


def _ejecutar_agendar_cirugia(accion: dict[str, Any], leer: Callable[[str], pd.DataFrame],
                              guardar: Callable[[str, pd.DataFrame], Any]) -> str:
    original = leer(TABLA_AGENDA)
    original = original.copy() if isinstance(original, pd.DataFrame) else pd.DataFrame()
    original = original.reset_index(drop=True)
    d = dict(accion["datos"])
    # Se vuelve a controlar con los datos de este momento.
    agenda = _agenda(original)
    f = leer_fecha(d["fecha"])
    nuevos = [a for a in _choques(agenda, f, d["sala"], d["medico"], _minutos(d["hora_inicio"]),
                                  _minutos(d["hora_fin"])) if a not in accion.get("avisos", [])]
    if nuevos:
        raise RuntimeError("Mientras tanto se ocupó ese horario: " + "; ".join(nuevos)
                           + ". Pedime de nuevo la cirugía con otro horario.")
    pos = _posiciones(original)
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if pos.get("created_at") is not None:
        d["created_at"] = ahora
    if pos.get("updated_at") is not None:
        d["updated_at"] = ahora
    d = {c: v for c, v in d.items() if pos.get(c) is not None or _texto(v)}  # no crea columnas para datos vacíos
    for campo in d:
        if pos.get(campo) is None:
            original[campo] = ""
            pos[campo] = len(original.columns) - 1
    for i in range(original.shape[1]):
        original.isetitem(i, original.iloc[:, i].astype("object"))
    fila = [""] * original.shape[1]
    for campo, valor in d.items():
        fila[pos[campo]] = valor
    original.loc[len(original)] = fila
    guardar(TABLA_AGENDA, original)
    return (f"Listo ✅ Agendé a {d['paciente']} el {fecha_larga(f)} de {d['hora_inicio']} a {d['hora_fin']} "
            f"en {d['sala']} ({d['procedimiento']} con {d['medico']}).")


# ---------------------------------------------------------------------------
# Facturación VMR / VM: modificar filas y cargar facturación nueva
# ---------------------------------------------------------------------------
FACTURACION = {"VMR": ("Facturación VMR", "facturacion_vmr"), "VM": ("Facturación VM", "facturacion_vm")}
# campo → (clave en las columnas que detecta el módulo, nombre de columna por defecto, etiqueta)
_CAMPOS_FX = {
    "mes": ("mes", "mes", "Mes"),
    "paciente": ("paciente", "afiliado", "Paciente / afiliado"),
    "obra_social": ("obra_social", "obra_social", "Obra social"),
    "procedimiento": ("procedimiento", "procedimiento", "Procedimiento"),
    "medico": ("medico", "medico_responsable", "Médico"),
    "numero_factura": ("numero_factura", "numero_factura", "N° de factura"),
    "fecha_factura": ("fecha_factura", "fecha_factura", "Fecha de factura"),
    "vencimiento": ("vencimiento", "vencimiento", "Vencimiento"),
    "fecha_pago": ("fecha_pago", "fecha_pago", "Fecha de pago"),
    "importe": ("monto", "valor_pesos", "Importe"),
    "estado": ("estado", "estado", "Estado"),
    "observaciones": (None, "observaciones", "Observaciones"),
}
_FECHAS_FX = {"fecha_factura", "vencimiento", "fecha_pago"}
ESTADOS_FACTURACION = ["Pendiente", "Completo", "Parcial", "Vencido", "Anulado"]


def _empresa_fx(empresa: str) -> str | None:
    e = re.sub(r"[^a-z]", "", _norm(empresa))
    if e in {"vmr", "reproductiva", "medicinareproductiva", "vitaemedicinareproductiva"}:
        return "VMR"
    if e in {"vm", "medical", "vitaemedical"}:
        return "VM"
    return None


def _preparar_fx(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    import vitae.modulos.facturacion as fx  # por el módulo: vistas.py le ajusta la fecha

    data, cols = fx._prepare_facturacion(df)
    return fx._ajustar_estado_manual(data), dict(cols or {})


def _columna_fx(df: pd.DataFrame, cols: dict[str, Any], campo: str) -> str | None:
    clave_m, defecto, _ = _CAMPOS_FX[campo]
    col = cols.get(clave_m) if clave_m else None
    if col and col in df.columns:
        return col
    candidatos = {defecto, campo} | ({"observacion", "notas", "nota"} if campo == "observaciones" else set())
    return next((c for c in df.columns if _norm(c) in {_norm(x) for x in candidatos}), None)


def _estilo_fecha(serie: pd.Series) -> str:
    vals = [_texto(v) for v in serie if _texto(v)][-200:]
    iso = sum(bool(re.match(r"\d{4}-\d{2}-\d{2}", v)) for v in vals)
    return "iso" if vals and iso > len(vals) / 2 else "ar"


def _escribir_fecha(valor: Any, serie: pd.Series, hoy: date | None = None) -> tuple[str, pd.Timestamp]:
    f = leer_fecha(valor, hoy)
    if pd.isna(f):
        return "", f
    return (f.strftime("%Y-%m-%d") if _estilo_fecha(serie) == "iso" else f.strftime("%d/%m/%Y")), f


def _escribir_mes(valor: Any, serie: pd.Series, hoy: date | None = None) -> tuple[str, pd.Timestamp]:
    t = _texto(valor)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", t) or None
    if m:
        f = pd.Timestamp(int(m[1]), int(m[2]), 1)
    else:
        m2 = re.fullmatch(r"(\d{1,2})[/-](\d{4})", t)
        f = pd.Timestamp(int(m2[2]), int(m2[1]), 1) if m2 else leer_fecha(t, hoy)
    if pd.isna(f):
        return "", f
    f = f.replace(day=1)
    vals = [_texto(v) for v in serie if _texto(v)][-200:]

    def mayoria(patron: str) -> bool:
        return bool(vals) and sum(bool(re.fullmatch(patron, v)) for v in vals) > len(vals) / 2

    if mayoria(r"\d{4}-\d{2}"):
        return f.strftime("%Y-%m"), f
    if mayoria(r"\d{4}-\d{2}-\d{2}.*"):
        return f.strftime("%Y-%m-%d"), f
    if mayoria(r"\d{1,2}/\d{4}"):
        return f.strftime("%m/%Y"), f
    return f.strftime("%d/%m/%Y"), f


def _escribir_importe(valor: Any, serie: pd.Series) -> tuple[str, float | None]:
    from vitae.modulos.pagos_pendientes import numero, pesos

    n = numero(valor)
    if n is None:
        return "", None
    vals = [_texto(v) for v in serie if _texto(v)][-200:]
    if vals and sum("$" in v for v in vals) > len(vals) / 2:
        return pesos(n).replace("$", "$ "), n
    if vals and sum(bool(re.search(r",\d{1,2}$", v)) for v in vals) > len(vals) / 2:
        return f"{n:.2f}".replace(".", ","), n
    return (f"{n:.0f}" if float(n).is_integer() else f"{n:.2f}"), n


def _huella(fila: pd.Series) -> str:
    return "|".join(_texto(v) for v in fila.tolist())


def _valor_nuevo(df: pd.DataFrame, cols: dict[str, Any], campo: str, valor: Any, hoy: date | None) -> tuple[str, str]:
    """(texto a escribir, texto para mostrar). Error → ("", motivo con «!» adelante)."""
    col = _columna_fx(df, cols, campo)
    serie = df[col] if col else pd.Series(dtype=object)
    if campo in _FECHAS_FX:
        t, f = _escribir_fecha(valor, serie, hoy)
        return (t, f"{f.day}/{f.month}/{f.year}") if t else ("", f"!No entendí la fecha «{valor}»")
    if campo == "mes":
        t, f = _escribir_mes(valor, serie, hoy)
        return (t, f"{f.month:02d}/{f.year}") if t else ("", f"!No entendí el mes «{valor}»")
    if campo == "importe":
        t, n = _escribir_importe(valor, serie)
        return (t, "$ " + f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")) if t else (
            "", f"!No entendí el importe «{valor}»")
    if campo == "estado":
        existentes = list(serie) + ESTADOS_FACTURACION
        e = _como_esta_cargado(_texto(valor), existentes)
        return e, e
    if campo in {"obra_social", "procedimiento", "medico", "paciente"}:
        e = _como_esta_cargado(_texto(valor), list(serie))
        return e, e
    return _texto(valor), _texto(valor)


def buscar_facturas(data: pd.DataFrame, *, paciente: str = "", numero_factura: str = "", mes: str = "",
                    obra_social: str = "", procedimiento: str = "") -> pd.DataFrame:
    x = data
    for col, valor in (("_paciente", paciente), ("_numero_factura", numero_factura), ("_obra_social", obra_social),
                       ("_procedimiento", procedimiento)):
        v = _norm(valor)
        if v:
            n = x[col].map(_norm)
            exacto = n.eq(v)
            x = x[exacto] if exacto.any() else x[n.str.contains(re.escape(v), na=False)]
    if _texto(mes):
        t = _texto(mes)
        m = re.fullmatch(r"(\d{4})-(\d{1,2})", t) or re.fullmatch(r"(\d{1,2})[/-](\d{4})", t)
        if m:
            anio, mm = (int(m[1]), int(m[2])) if len(m[1]) == 4 else (int(m[2]), int(m[1]))
            x = x[x["_fecha_base"].dt.year.eq(anio) & x["_fecha_base"].dt.month.eq(mm)]
    return x


def _resumen_fila(f: pd.Series) -> dict[str, Any]:
    fb = f["_fecha_base"]
    return {"mes": f"{fb.month:02d}/{fb.year}" if pd.notna(fb) else "", "paciente": f["_paciente"],
            "obra_social": f["_obra_social"], "procedimiento": f["_procedimiento"],
            "factura": f["_numero_factura"], "importe": round(float(f["_monto"]), 2),
            "estado": f["_estado_ejecutivo"]}


def proponer_cambio_facturacion_datos(df: pd.DataFrame, empresa: str, *, buscar: dict[str, str],
                                      cambios: dict[str, Any], aplicar_a_todas: bool = False,
                                      hoy: date | None = None) -> dict[str, Any]:
    emp = _empresa_fx(empresa)
    if emp is None:
        return {"ok": False, "error": "Decime si es VMR o VM."}
    cambios = {k: v for k, v in cambios.items() if _texto(v)}
    if not cambios:
        return {"ok": False, "error": "No me dijiste qué dato cambiar."}
    if not any(_texto(v) for v in buscar.values()):
        return {"ok": False, "error": "Decime de qué paciente, factura, mes u obra social se trata."}
    df = df.reset_index(drop=True) if isinstance(df, pd.DataFrame) else pd.DataFrame()
    data, cols = _preparar_fx(df)
    hallados = buscar_facturas(data, **buscar)
    if hallados.empty:
        return {"ok": False, "error": "No encontré esa facturación.", "buscado": buscar}
    if len(hallados) > 1 and not aplicar_a_todas:
        return {"ok": False, "varias": int(len(hallados)),
                "candidatas": [_resumen_fila(f) for _, f in hallados.head(8).iterrows()]}
    if len(hallados) > 60:
        return {"ok": False, "error": f"Son {len(hallados)} filas: es demasiado para un solo cambio. Acotalo."}
    escribir, mostrar = {}, {}
    for campo, valor in cambios.items():
        t, m = _valor_nuevo(df, cols, campo, valor, hoy)
        if m.startswith("!"):
            return {"ok": False, "error": m[1:]}
        escribir[campo], mostrar[campo] = t, m
    objetivos, filas_tarjeta = [], []
    for pos, f in hallados.iterrows():
        objetivos.append({"pos": int(pos), "huella": _huella(df.iloc[int(pos)])})
    primera = hallados.iloc[0]
    detalle = []
    for campo in escribir:
        col = _columna_fx(df, cols, campo)
        antes = _texto(df.iloc[int(hallados.index[0])][col]) if col else ""
        detalle.append([_CAMPOS_FX[campo][2], antes if len(hallados) == 1 else "(varias)", mostrar[campo]])
    if len(hallados) == 1:
        r = _resumen_fila(primera)
        filas_tarjeta = [["Paciente", r["paciente"]], ["Mes", r["mes"]], ["Obra social", r["obra_social"]],
                         ["Procedimiento", r["procedimiento"]], ["Factura", r["factura"] or "—"]]
        quien = f"{r['paciente']} ({r['mes']})"
    else:
        filas_tarjeta = [["Filas", f"{len(hallados)} facturas"]] + [
            ["", f"{_resumen_fila(f)['paciente']} · {_resumen_fila(f)['mes']}"] for _, f in hallados.head(5).iterrows()]
        quien = f"{len(hallados)} facturas"
    nombre, tabla = FACTURACION[emp]
    return {"ok": True, "accion": {
        "tipo": "facturacion_cambio", "modulo": nombre, "tabla": tabla, "titulo": f"Modificar {nombre}",
        "icono": "🧾", "objetivos": objetivos, "escribir": escribir, "filas": filas_tarjeta, "cambios": detalle,
        "resumen": f"{quien}: " + ", ".join(f"{d[0]} → {d[2]}" for d in detalle), "avisos": [],
        "estado": "pendiente"}}


def proponer_nueva_facturacion_datos(df: pd.DataFrame, empresa: str, datos_fila: dict[str, Any],
                                     hoy: date | None = None) -> dict[str, Any]:
    emp = _empresa_fx(empresa)
    if emp is None:
        return {"ok": False, "error": "Decime si es VMR o VM."}
    datos_fila = {k: v for k, v in datos_fila.items() if _texto(v)}
    faltan = [_CAMPOS_FX[c][2] for c in ("mes", "paciente", "obra_social", "procedimiento") if c not in datos_fila]
    if faltan:
        return {"ok": False, "faltan": faltan}
    df = df.reset_index(drop=True) if isinstance(df, pd.DataFrame) else pd.DataFrame()
    data, cols = _preparar_fx(df)
    escribir, filas_tarjeta = {}, []
    for campo in _CAMPOS_FX:
        if campo not in datos_fila:
            continue
        t, m = _valor_nuevo(df, cols, campo, datos_fila[campo], hoy)
        if m.startswith("!"):
            return {"ok": False, "error": m[1:]}
        escribir[campo] = t
        filas_tarjeta.append([_CAMPOS_FX[campo][2], m])
    avisos = []
    if len(data):
        mes_t = escribir.get("mes", "")
        _, fmes = _escribir_mes(datos_fila["mes"], pd.Series(dtype=object), hoy)
        repetidas = buscar_facturas(data, paciente=escribir.get("paciente", ""),
                                    procedimiento=escribir.get("procedimiento", ""),
                                    mes=f"{fmes.year}-{fmes.month:02d}" if pd.notna(fmes) else "")
        if mes_t and len(repetidas):
            avisos.append(f"Ya hay {len(repetidas)} fila(s) de {escribir.get('paciente')} con ese procedimiento ese mes.")
        if escribir.get("numero_factura") and len(buscar_facturas(data, numero_factura=escribir["numero_factura"])):
            avisos.append(f"El N° de factura {escribir['numero_factura']} ya está cargado en otra fila.")
    nombre, tabla = FACTURACION[emp]
    return {"ok": True, "accion": {
        "tipo": "facturacion_nueva", "modulo": nombre, "tabla": tabla, "titulo": f"Cargar en {nombre}",
        "icono": "🧾", "escribir": escribir, "filas": filas_tarjeta, "avisos": avisos,
        "resumen": f"{escribir.get('paciente')} · {escribir.get('procedimiento')} · {escribir.get('mes')}",
        "estado": "pendiente"}}


def _ejecutar_facturacion_cambio(accion: dict[str, Any], leer: Callable[[str], pd.DataFrame],
                                 guardar: Callable[[str, pd.DataFrame], Any]) -> str:
    df = leer(accion["tabla"])
    df = df.copy().reset_index(drop=True) if isinstance(df, pd.DataFrame) else pd.DataFrame()
    _, cols = _preparar_fx(df)
    huellas = [_huella(df.iloc[i]) for i in range(len(df))]
    posiciones = []
    for obj in accion["objetivos"]:
        pos = obj["pos"]
        if pos < len(df) and huellas[pos] == obj["huella"]:
            posiciones.append(pos)
            continue
        iguales = [i for i, h in enumerate(huellas) if h == obj["huella"] and i not in posiciones]
        if len(iguales) != 1:
            raise RuntimeError("Esa fila cambió en la planilla mientras tanto. Pedímelo de nuevo.")
        posiciones.append(iguales[0])
    for campo, valor in accion["escribir"].items():
        col = _columna_fx(df, cols, campo) or _CAMPOS_FX[campo][1]
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].astype("object")
        for pos in posiciones:
            df.at[pos, col] = valor
    if "updated_at" in df.columns:
        df["updated_at"] = df["updated_at"].astype("object")
        for pos in posiciones:
            df.at[pos, "updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    guardar(accion["tabla"], df)
    return f"Listo ✅ Lo cargué en {accion['modulo']}: {accion['resumen']}."


def _ejecutar_facturacion_nueva(accion: dict[str, Any], leer: Callable[[str], pd.DataFrame],
                                guardar: Callable[[str, pd.DataFrame], Any]) -> str:
    df = leer(accion["tabla"])
    df = df.copy().reset_index(drop=True) if isinstance(df, pd.DataFrame) else pd.DataFrame()
    _, cols = _preparar_fx(df)
    fila: dict[str, Any] = {c: "" for c in df.columns}
    for campo, valor in accion["escribir"].items():
        col = _columna_fx(df, cols, campo) or _CAMPOS_FX[campo][1]
        if col not in df.columns:
            df[col] = ""
            fila[col] = ""
        fila[col] = valor
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for c in ("created_at", "updated_at"):
        if c in df.columns:
            fila[c] = ahora
    if "id" in df.columns:
        ids = pd.to_numeric(df["id"], errors="coerce")
        fila["id"] = str(int(ids.max()) + 1) if ids.notna().any() else datetime.now().strftime("FX%Y%m%d%H%M%S")
    df = pd.concat([df, pd.DataFrame([fila], columns=df.columns)], ignore_index=True)
    guardar(accion["tabla"], df)
    return f"Listo ✅ Cargué en {accion['modulo']}: {accion['resumen']}."


EJECUTORES: dict[str, Callable[..., str]] = {
    "agendar_cirugia": _ejecutar_agendar_cirugia,
    "facturacion_cambio": _ejecutar_facturacion_cambio,
    "facturacion_nueva": _ejecutar_facturacion_nueva,
}


def ejecutar(accion: dict[str, Any], leer: Callable[[str], pd.DataFrame],
             guardar: Callable[[str, pd.DataFrame], Any]) -> str:
    funcion = EJECUTORES.get(accion.get("tipo", ""))
    if funcion is None:
        raise RuntimeError("No sé hacer esa acción.")
    return funcion(accion, leer, guardar)


# ---------------------------------------------------------------------------
# Herramientas para la IA (sólo proponen)
# ---------------------------------------------------------------------------
INSTRUCCIONES_FACTURACION = """- Facturación VMR / VM: con proponer_cambio_facturacion registrás o corregís datos de una fila existente
  (N° de factura, fecha de factura, vencimiento, fecha de pago, importe, estado, obra social, procedimiento,
  médico, observaciones). La fila se busca por paciente, N° de factura, mes (AAAA-MM), obra social o
  procedimiento. Si hay varias candidatas te las devuelve: preguntá cuál (o, si el usuario lo pidió para
  todas, usá aplicar_a_todas=true).
- Un cobro se registra con set_fecha_pago y set_estado «Completo» (o «Parcial» si fue parcial).
- Con proponer_nueva_facturacion cargás una fila nueva (mes, paciente, obra social y procedimiento son
  obligatorios; médico, importe, N° de factura, fechas, estado y observaciones, si los dicen).
- Para que todo quede completo: con facturacion_calidad ves qué falta (N° de factura, vencimientos, fechas de
  pago…) y ofrecé completarlo con proponer_cambio_facturacion."""

INSTRUCCIONES_GENERALES = """ACCIONES (podés HACER cosas, siempre con confirmación)
- Las herramientas proponer_* NO guardan: dejan el cambio listo para que el usuario toque «Confirmar» en la
  tarjeta del chat. Nunca digas que ya quedó hecho; decí algo como «Te lo dejé listo, confirmalo abajo 👇».
- Pasá las fechas como AAAA-MM-DD calculándolas desde HOY («el jueves», «mañana», «el 3») y las horas como HH:MM.
- Si falta un dato obligatorio, preguntalo en una línea antes de proponer.
- Si la herramienta avisa algo (superposiciones, filas repetidas, fechas pasadas), contalo en una frase.
- Si te piden algo que todavía no sabés hacer, decí que por ahora no podés y que se puede agregar."""

INSTRUCCIONES_CIRUGIA = """- Agenda de Quirófano: proponer_cirugia programa una cirugía. Obligatorios: paciente, fecha, hora, médico y
  procedimiento. Si no lo dicen: 60 minutos y el quirófano que más se usa."""

INSTRUCCIONES = INSTRUCCIONES_GENERALES + "\n" + INSTRUCCIONES_CIRUGIA


def instrucciones(herramientas_ofrecidas: list[Callable[..., str]]) -> str:
    nombres = {h.__name__ for h in herramientas_ofrecidas}
    texto = INSTRUCCIONES_GENERALES
    if "proponer_cirugia" in nombres:
        texto += "\n" + INSTRUCCIONES_CIRUGIA
    if nombres & {"proponer_cambio_facturacion", "proponer_nueva_facturacion"}:
        texto += "\n" + INSTRUCCIONES_FACTURACION
    return texto


def herramientas(datos: Any, acciones: list[dict[str, Any]], hoy: date | None = None) -> list[Callable[..., str]]:
    """Herramientas de acción según lo que el usuario puede usar."""
    disponibles = datos.modulos()
    out: list[Callable[..., str]] = []
    if MODULO_AGENDA in disponibles:
        def proponer_cirugia(paciente: str, fecha: str, hora: str, medico: str, procedimiento: str,
                             duracion_min: int = 60, quirofano: str = "", anestesista: str = "",
                             obra_social: str = "", telefono: str = "", observaciones: str = "",
                             estado: str = "Programado") -> str:
            """Prepara una cirugía para la Agenda de Quirófano (NO la guarda: el usuario la confirma en el chat).
            fecha: AAAA-MM-DD. hora: HH:MM de inicio. duracion_min: minutos (60 si no lo dicen).
            quirofano: sala (vacío = la habitual). estado: Programado (por defecto), Confirmado, Pendiente de
            autorización, Autorizado o Consulta."""
            try:
                datos.consultas.append(f"proponer_cirugia({paciente}, {fecha} {hora})")
            except Exception:
                pass
            try:
                agenda = datos.df(MODULO_AGENDA)
            except Exception:
                agenda = pd.DataFrame()
            r = proponer_cirugia_datos(agenda, paciente=paciente, fecha=fecha, hora=hora, medico=medico,
                                       procedimiento=procedimiento, duracion_min=duracion_min, quirofano=quirofano,
                                       anestesista=anestesista, obra_social=obra_social, telefono=telefono,
                                       observaciones=observaciones, estado=estado, hoy=hoy)
            if not r["ok"]:
                return _json({"resultado": "faltan datos", "faltan": r.get("faltan"), "error": r.get("error", "")})
            # Una sola propuesta pendiente por respuesta: la última reemplaza a las anteriores.
            acciones[:] = [a for a in acciones if a.get("tipo") != "agendar_cirugia"] + [r["accion"]]
            return _json({"resultado": "lista para confirmar (todavía NO está guardada)",
                          "cirugia": r["accion"]["datos"], "avisos": r["accion"]["avisos"]})

        out.append(proponer_cirugia)

    empresas_fx = [e for e, (nombre, _) in FACTURACION.items() if nombre in disponibles]
    if empresas_fx:
        def _df_fx(empresa: str) -> tuple[str | None, pd.DataFrame]:
            emp = _empresa_fx(empresa)
            if emp not in empresas_fx:
                return None, pd.DataFrame()
            try:
                return emp, datos.df(FACTURACION[emp][0])
            except Exception:
                return emp, pd.DataFrame()

        def _anotar(r: dict[str, Any]) -> str:
            if not r["ok"]:
                return _json({"resultado": "no se armó", **{k: v for k, v in r.items() if k != "ok"}})
            acciones[:] = [a for a in acciones if not a.get("tipo", "").startswith("facturacion_")] + [r["accion"]]
            a = r["accion"]
            return _json({"resultado": "lista para confirmar (todavía NO está guardada)", "resumen": a["resumen"],
                          "cambios": a.get("cambios"), "avisos": a.get("avisos")})

        def proponer_cambio_facturacion(empresa: str, paciente: str = "", numero_factura: str = "", mes: str = "",
                                        obra_social: str = "", procedimiento: str = "", set_numero_factura: str = "",
                                        set_fecha_factura: str = "", set_vencimiento: str = "",
                                        set_fecha_pago: str = "", set_importe: str = "", set_estado: str = "",
                                        set_obra_social: str = "", set_procedimiento: str = "",
                                        set_medico: str = "", set_mes: str = "", set_observaciones: str = "",
                                        aplicar_a_todas: bool = False) -> str:
            """Prepara un cambio en Facturación VMR o VM (NO lo guarda: el usuario confirma en el chat).
            empresa: VMR o VM. Para encontrar la fila: paciente, numero_factura, mes (AAAA-MM), obra_social,
            procedimiento. Lo que cambia va en los set_*: fechas en AAAA-MM-DD, importe en número, estado
            (Pendiente, Completo, Parcial, Vencido, Anulado). Si hay varias filas devuelve las candidatas.
            aplicar_a_todas=true aplica el cambio a todas las que coinciden (sólo si el usuario lo pidió)."""
            try:
                datos.consultas.append(f"proponer_cambio_facturacion({empresa}, {paciente or numero_factura})")
            except Exception:
                pass
            emp, df = _df_fx(empresa)
            if emp is None:
                return _json({"resultado": "no se armó", "error": "Decime VMR o VM (con acceso del usuario)."})
            cambios = {"numero_factura": set_numero_factura, "fecha_factura": set_fecha_factura,
                       "vencimiento": set_vencimiento, "fecha_pago": set_fecha_pago, "importe": set_importe,
                       "estado": set_estado, "obra_social": set_obra_social, "procedimiento": set_procedimiento,
                       "medico": set_medico, "mes": set_mes, "observaciones": set_observaciones}
            return _anotar(proponer_cambio_facturacion_datos(
                df, emp, buscar={"paciente": paciente, "numero_factura": numero_factura, "mes": mes,
                                 "obra_social": obra_social, "procedimiento": procedimiento},
                cambios=cambios, aplicar_a_todas=aplicar_a_todas, hoy=hoy))

        def proponer_nueva_facturacion(empresa: str, mes: str, paciente: str, obra_social: str, procedimiento: str,
                                       medico: str = "", importe: str = "", numero_factura: str = "",
                                       fecha_factura: str = "", vencimiento: str = "", fecha_pago: str = "",
                                       estado: str = "Pendiente", observaciones: str = "") -> str:
            """Prepara una fila nueva en Facturación VMR o VM (NO la guarda: el usuario confirma en el chat).
            mes: AAAA-MM. Fechas: AAAA-MM-DD. importe: número. estado: Pendiente (por defecto), Completo,
            Parcial, Vencido o Anulado."""
            try:
                datos.consultas.append(f"proponer_nueva_facturacion({empresa}, {paciente})")
            except Exception:
                pass
            emp, df = _df_fx(empresa)
            if emp is None:
                return _json({"resultado": "no se armó", "error": "Decime VMR o VM (con acceso del usuario)."})
            return _anotar(proponer_nueva_facturacion_datos(df, emp, {
                "mes": mes, "paciente": paciente, "obra_social": obra_social, "procedimiento": procedimiento,
                "medico": medico, "importe": importe, "numero_factura": numero_factura,
                "fecha_factura": fecha_factura, "vencimiento": vencimiento, "fecha_pago": fecha_pago,
                "estado": estado, "observaciones": observaciones}, hoy=hoy))

        out += [proponer_cambio_facturacion, proponer_nueva_facturacion]
    return out


# ---------------------------------------------------------------------------
# Tarjeta para el chat
# ---------------------------------------------------------------------------
_ETIQUETAS = [("paciente", "Paciente"), ("fecha", "Fecha"), ("hora_inicio", "Hora"), ("sala", "Quirófano"),
              ("procedimiento", "Procedimiento"), ("medico", "Médico"), ("anestesista", "Anestesista"),
              ("obra_social", "Cobertura"), ("telefono", "Teléfono"), ("estado", "Estado"),
              ("observaciones", "Notas")]


def html_tarjeta(accion: dict[str, Any]) -> str:
    if accion.get("filas") is not None:
        filas = [f'<div class="asis-acc-k">{html.escape(_texto(k))}</div><div class="asis-acc-v">{html.escape(_texto(v))}</div>'
                 for k, v in accion["filas"]]
        for etiqueta, antes, despues in accion.get("cambios") or []:
            antes_t = (f'<s style="color:#9A8D93;font-weight:400">{html.escape(_texto(antes))}</s> → ' if _texto(antes)
                       else '<span style="color:#9A8D93;font-weight:400">vacío</span> → ')
            filas.append(f'<div class="asis-acc-k">{html.escape(etiqueta)}</div>'
                         f'<div class="asis-acc-v">{antes_t}{html.escape(_texto(despues))}</div>')
        avisos = "".join(f'<div class="asis-acc-aviso">⚠️ {html.escape(a)}</div>' for a in accion.get("avisos", []))
        return (f'<div class="asis-acc"><div class="asis-acc-tit">{accion.get("icono", "📝")} '
                f'{html.escape(accion.get("titulo", "Acción"))}<span>esperando tu confirmación</span></div>'
                f'<div class="asis-acc-grid">{"".join(filas)}</div>{avisos}</div>')
    d = accion.get("datos", {})
    filas = []
    for campo, etiqueta in _ETIQUETAS:
        v = _texto(d.get(campo))
        if not v:
            continue
        if campo == "hora_inicio":
            v = f"{v}–{d.get('hora_fin', '')} ({d.get('duracion_min', '')} min)"
        if campo == "fecha":
            f = leer_fecha(v)
            v = fecha_larga(f) if pd.notna(f) else v
        filas.append(f'<div class="asis-acc-k">{etiqueta}</div><div class="asis-acc-v">{html.escape(v)}</div>')
    avisos = "".join(f'<div class="asis-acc-aviso">⚠️ {html.escape(a)}</div>' for a in accion.get("avisos", []))
    return (f'<div class="asis-acc"><div class="asis-acc-tit">🗓️ {html.escape(accion.get("titulo", "Acción"))}'
            f'<span>esperando tu confirmación</span></div><div class="asis-acc-grid">{"".join(filas)}</div>{avisos}</div>')
