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


EJECUTORES: dict[str, Callable[..., str]] = {
    "agendar_cirugia": _ejecutar_agendar_cirugia,
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
INSTRUCCIONES = """ACCIONES (podés HACER cosas, siempre con confirmación)
- Podés programar cirugías en la Agenda de Quirófano con proponer_cirugia.
- Pasá la fecha como AAAA-MM-DD calculándola desde HOY («el jueves», «mañana», «el 3») y la hora como HH:MM.
- Si falta un dato obligatorio (paciente, fecha, hora, médico, procedimiento), preguntalo en una línea antes de proponer.
  La duración por defecto es 60 minutos y el quirófano, el que más se usa.
- proponer_cirugia NO guarda: deja la cirugía lista para que el usuario toque «Confirmar» en la tarjeta del chat.
  Nunca digas que ya quedó agendada; decí algo como «Te la dejé lista, confirmala abajo 👇».
- Si la herramienta avisa superposiciones o una fecha pasada, contalo en una frase para que decida.
- Si te piden algo que todavía no sabés hacer, decí que por ahora no podés y que se puede agregar."""


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
    return out


# ---------------------------------------------------------------------------
# Tarjeta para el chat
# ---------------------------------------------------------------------------
_ETIQUETAS = [("paciente", "Paciente"), ("fecha", "Fecha"), ("hora_inicio", "Hora"), ("sala", "Quirófano"),
              ("procedimiento", "Procedimiento"), ("medico", "Médico"), ("anestesista", "Anestesista"),
              ("obra_social", "Cobertura"), ("telefono", "Teléfono"), ("estado", "Estado"),
              ("observaciones", "Notas")]


def html_tarjeta(accion: dict[str, Any]) -> str:
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
