"""Vencimientos: documentos, habilitaciones, pólizas, mantenimientos y dominios con su fecha.

Como la planilla (DOCUMENTO · VENCIMIENTO), con lo que hace falta para que no se pase ninguno:
cuántos días faltan, semáforo (vencido, esta semana, por vencer, vigente, sin fecha), categoría,
cada cuánto se renueva, cuántos días antes avisar, responsable y notas. «Renovar» pone la fecha
nueva (sugerida según la renovación) y guarda la anterior. «En trámite» marca lo que ya se está
gestionando; «Archivado» lo saca de las alertas sin borrarlo.

Pestaña ``vencimientos`` (el formato anterior —detalle, tipo_vencimiento, vencimiento, estado,
importe…— se sigue leyendo). El Panel general avisa con ``resumen``.
"""
from __future__ import annotations

import html
import io
import re
import uuid
from datetime import date, datetime
from typing import Any, Callable

import pandas as pd

from vitae.modulos.pagos_pendientes import _sin_acentos, _texto, numero, pesos

TABLA = "vencimientos"
COLUMNAS = ["id_registro", "documento", "vencimiento", "categoria", "renovacion", "aviso_dias", "responsable",
            "estado", "importe", "renovado_el", "vencimiento_anterior", "observaciones", "orden", "updated_at"]
AVISO = 30                      # días antes para avisar, si no se indica otro
EN_TRAMITE, ARCHIVADO = "En trámite", "Archivado"
ESTADOS = ["", EN_TRAMITE, ARCHIVADO]
VENCIDO, SEMANA, POR_VENCER, PROXIMO, VIGENTE, SIN_FECHA = (
    "Vencido", "Vence esta semana", "Por vencer", "Próximos 3 meses", "Vigente", "Sin fecha")
SITUACIONES = [VENCIDO, SEMANA, POR_VENCER, PROXIMO, VIGENTE, SIN_FECHA]
RENOVACIONES = {"": None, "Mensual": 1, "Bimestral": 2, "Trimestral": 3, "Cuatrimestral": 4, "Semestral": 6,
                "Anual": 12, "Cada 2 años": 24, "Cada 3 años": 36, "Cada 5 años": 60, "Única": None}
CATEGORIAS = ["Mantenimiento y seguridad", "Habilitaciones", "Seguros", "Web y dominios", "Convenios", "Personal",
              "Otros"]
SECCIONES = ["Planilla", "Editar", "Calendario", "Importar"]
COLOR = {VENCIDO: ("#F9E1D7", "#B4452F"), SEMANA: ("#FDE7C9", "#A35E12"), POR_VENCER: ("#FFF6D1", "#7A5B00"),
         PROXIMO: ("#EEF4FB", "#2F4F7A"), VIGENTE: ("#FFFFFF", "#1F5130"), SIN_FECHA: ("#F4F1F3", "#6B5F66"),
         EN_TRAMITE: ("#E7EEF9", "#2F4F7A")}
_PALABRAS_CATEGORIA = [
    ("Seguros", ("poliza", "seguro", "art ")),
    ("Web y dominios", ("dominio", ".com", "certificado de seguridad", "certificados de seguridad", "ssl", "hosting")),
    ("Convenios", ("convenio", "contrato")),
    ("Personal", ("legajo", "personal", "libreta", "matricula")),
    ("Habilitaciones", ("habilitacion", "bombero", "municipal", "caam", "sss", "residuos", "proficcssa",
                        "inscripcion", "ministerio", "registro", "establecimiento")),
    ("Mantenimiento y seguridad", ("mantenimiento", "hidrante", "electrogeno", "fumigacion", "analisis agua",
                                   "agua", "electrico", "matafuego", "tanque", "ascensor", "limpieza", "desinfec")),
]


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _id() -> str:
    return uuid.uuid4().hex[:12]


def _ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _hoy(hoy: date | None) -> pd.Timestamp:
    return pd.Timestamp(hoy or date.today()).normalize()


def leer_fecha(v: Any) -> pd.Timestamp:
    """1/9/2026, 01/09/26, 2026-09-01, una fecha de Excel… → fecha (día primero)."""
    if v is None:
        return pd.NaT
    if isinstance(v, (pd.Timestamp, datetime, date)):
        return pd.NaT if pd.isna(v) else pd.Timestamp(v).normalize()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if pd.isna(v) or not 20000 < float(v) < 80000:
            return pd.NaT
        return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(v))).normalize()  # número de serie de Excel
    t = _texto(v).strip()
    if not t:
        return pd.NaT
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}.*", t):
        return pd.to_datetime(t[:10], errors="coerce")
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})(\s.*)?", t)
    if m:
        a = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        try:
            return pd.Timestamp(year=a, month=int(m[2]), day=int(m[1]))
        except ValueError:
            return pd.NaT
    return pd.NaT


def fecha_txt(f: Any) -> str:
    f = leer_fecha(f)
    return "" if pd.isna(f) else f.strftime("%d/%m/%Y")


def categoria_de(documento: Any) -> str:
    n = f" {_sin_acentos(documento)} "
    for cat, palabras in _PALABRAS_CATEGORIA:
        if any(p in n for p in palabras):
            return cat
    return "Otros"


def faltan_txt(dias: Any) -> str:
    if dias is None or pd.isna(dias):
        return ""
    d = int(dias)
    if d < 0:
        d = -d
        return "venció ayer" if d == 1 else (f"venció hace {d} días" if d < 60 else
                                             f"venció hace {round(d / 30.4)} meses")
    if d == 0:
        return "vence hoy"
    if d == 1:
        return "vence mañana"
    if d < 60:
        return f"en {d} días"
    if d < 730:
        return f"en {round(d / 30.4)} meses"
    return f"en {d / 365.25:.1f} años".replace(".", ",")


def sumar_meses(f: pd.Timestamp, meses: int) -> pd.Timestamp:
    return (f + pd.DateOffset(months=meses)).normalize()


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------
def _col(df: pd.DataFrame, *nombres: str) -> str | None:
    mapa = {re.sub(r"[^a-z0-9]+", "_", _sin_acentos(c)).strip("_"): c for c in df.columns}
    return next((mapa[n] for n in nombres if n in mapa), None)


def _sin_repetidas(df: pd.DataFrame) -> pd.DataFrame:
    claves = [re.sub(r"[^a-z0-9]+", "_", _sin_acentos(c)).strip("_") for c in df.columns]
    if len(set(claves)) == len(claves):
        return df
    out: dict[str, pd.Series] = {}
    for j, k in enumerate(claves):
        col = df.iloc[:, j].map(_texto)
        out[k] = col if k not in out else out[k].where(out[k] != "", col)
    return pd.DataFrame(out, index=df.index)


def _estado(v: Any) -> str:
    n = _sin_acentos(v)
    if "tramit" in n or "reprogram" in n or "gestion" in n:
        return EN_TRAMITE
    if n.startswith(("archiv", "realiz", "pagad", "cancel", "finaliz", "renovad", "baja")):
        return ARCHIVADO
    return ""


def _renovacion(v: Any) -> str:
    n = _sin_acentos(v)
    for k in RENOVACIONES:
        if k and _sin_acentos(k) == n:
            return k
    if not n:
        return ""
    if "mens" in n:
        return "Mensual"
    if "trimes" in n:
        return "Trimestral"
    if "semes" in n:
        return "Semestral"
    if "anual" in n or n in {"1 ano", "12 meses"}:
        return "Anual"
    return ""


def normalizar(df: pd.DataFrame | None) -> pd.DataFrame:
    """La pestaña (formato nuevo o el anterior) → una fila por documento, con tipos limpios."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=COLUMNAS)
    df = _sin_repetidas(df.reset_index(drop=True))

    def serie(*nombres: str) -> pd.Series:
        c = _col(df, *nombres)
        return df[c].map(_texto) if c else pd.Series("", index=df.index, dtype="object")

    d = pd.DataFrame(index=df.index)
    d["id_registro"] = [x or _id() for x in serie("id_registro")]
    d["documento"] = serie("documento", "detalle", "concepto", "descripcion").map(lambda t: re.sub(r"\s+", " ", t).strip())
    venc = serie("vencimiento", "vence", "fecha_vencimiento")
    fecha = serie("fecha")
    d["vencimiento"] = [fecha_txt(v) or fecha_txt(f) for v, f in zip(venc, fecha)]
    cat = serie("categoria", "tipo_vencimiento", "tipo")
    d["categoria"] = [c if c in CATEGORIAS else (categoria_de(doc) if not c or c in {"Otro", "Otros"} else
                                                 {"Seguro": "Seguros", "Habilitación": "Habilitaciones",
                                                  "Mantenimiento": "Mantenimiento y seguridad",
                                                  "Contrato": "Convenios"}.get(c, categoria_de(doc)))
                     for c, doc in zip(cat, d["documento"])]
    d["renovacion"] = serie("renovacion", "periodicidad", "frecuencia").map(_renovacion)
    av = serie("aviso_dias", "aviso", "avisar")
    d["aviso_dias"] = [int(numero(a)) if numero(a) not in (None,) and numero(a) >= 0 else AVISO for a in av]
    d["responsable"] = serie("responsable", "encargado", "empresa")
    d["estado"] = serie("estado").map(_estado)
    imp = _col(df, "importe", "costo", "monto")
    d["importe"] = df[imp].map(lambda v: float(numero(v) or 0.0)) if imp else 0.0
    d["renovado_el"] = serie("renovado_el").map(lambda v: fecha_txt(v) or "")
    d["vencimiento_anterior"] = serie("vencimiento_anterior").map(lambda v: fecha_txt(v) or "")
    d["observaciones"] = serie("observaciones", "notas", "nota")
    o = _col(df, "orden")
    d["orden"] = df[o].map(lambda v: int(numero(v) or 0)) if o else range(len(df))
    d["updated_at"] = serie("updated_at")
    d = d[d["documento"] != ""]
    return d[COLUMNAS].reset_index(drop=True)


def para_guardar(d: pd.DataFrame) -> pd.DataFrame:
    out = d[COLUMNAS].copy()
    out["importe"] = out["importe"].map(lambda v: f"{float(v or 0):.2f}" if float(v or 0) else "")
    out["aviso_dias"] = out["aviso_dias"].map(lambda v: str(int(v)))
    out["orden"] = range(len(out))
    out["orden"] = out["orden"].map(str)
    out["updated_at"] = out["updated_at"].replace("", _ahora())
    return out.reset_index(drop=True)


def preparar(d: pd.DataFrame, hoy: date | None = None) -> pd.DataFrame:
    """Agrega la fecha, los días que faltan y la situación (semáforo)."""
    h = _hoy(hoy)
    x = d.copy()
    x["_fecha"] = pd.to_datetime([leer_fecha(v) for v in x["vencimiento"]], errors="coerce") if len(x) else \
        pd.Series(dtype="datetime64[ns]")
    x["_dias"] = (x["_fecha"] - h).dt.days

    def sit(dias: Any, aviso: int) -> str:
        if dias is None or pd.isna(dias):
            return SIN_FECHA
        if dias < 0:
            return VENCIDO
        if dias <= 7:
            return SEMANA
        if dias <= max(int(aviso or AVISO), 7):
            return POR_VENCER
        if dias <= 90:
            return PROXIMO
        return VIGENTE

    x["_situacion"] = [sit(a, b) for a, b in zip(x["_dias"], x["aviso_dias"])]
    x["_activo"] = x["estado"] != ARCHIVADO
    orden_sit = {s: i for i, s in enumerate(SITUACIONES)}
    x["_orden_sit"] = x["_situacion"].map(orden_sit)
    return x


def ordenados(x: pd.DataFrame) -> pd.DataFrame:
    """Por fecha (los vencidos primero, los sin fecha al final)."""
    return (x.assign(_f=x["_fecha"].fillna(pd.Timestamp.max), _o=pd.to_numeric(x["orden"], errors="coerce").fillna(0))
            .sort_values(["_f", "_o"], kind="stable").drop(columns=["_f", "_o"]))


def resumen(d: pd.DataFrame, hoy: date | None = None) -> dict[str, Any]:
    """Para la pantalla, el Panel general y Luz."""
    if d.empty:
        return {"hay": False}
    x = preparar(d, hoy)
    a = ordenados(x[x["_activo"]])
    tram = a["estado"] == EN_TRAMITE

    def nombres(m: pd.Series) -> list[str]:
        return list(a.loc[m, "documento"])

    venc = (a["_situacion"] == VENCIDO) & ~tram
    sem = (a["_situacion"] == SEMANA) & ~tram
    d30 = a["_dias"].between(0, 30)
    por_v = (a["_situacion"] == POR_VENCER) & ~tram
    return {
        "hay": True, "total": len(a),
        "vencidos": int(venc.sum()), "vencidos_nombres": nombres(venc),
        "vencidos_importe": float(a.loc[venc, "importe"].sum()),
        "en_tramite": int(tram.sum()), "en_tramite_nombres": nombres(tram),
        "en_tramite_vencidos": int((tram & (a["_situacion"] == VENCIDO)).sum()),
        "prox_7": int(sem.sum()), "prox_7_nombres": nombres(sem),
        "por_vencer": int(por_v.sum()), "por_vencer_nombres": nombres(por_v),
        "prox_30": int((d30 & ~tram).sum()), "prox_30_importe": float(a.loc[d30 & ~tram, "importe"].sum()),
        "prox_90": int((a["_dias"].between(0, 90)).sum()),
        "sin_fecha": int((a["_situacion"] == SIN_FECHA).sum()), "sin_fecha_nombres": nombres(a["_situacion"] == SIN_FECHA),
        "vigentes": int((a["_situacion"] == VIGENTE).sum()),
        "proximo": (a.loc[a["_dias"] >= 0].iloc[0][["documento", "vencimiento", "_dias"]].to_dict()
                    if (a["_dias"] >= 0).any() else None),
        "archivados": int((~x["_activo"]).sum()),
    }


def lista_corta(nombres: list[str], n: int = 3) -> str:
    if not nombres:
        return ""
    t = ", ".join(nombres[:n])
    return t + (f" y {len(nombres) - n} más" if len(nombres) > n else "")


# ---------------------------------------------------------------------------
# Cambios
# ---------------------------------------------------------------------------
def sugerir_renovacion(fila: pd.Series | dict, hoy: date | None = None) -> pd.Timestamp:
    """La fecha nueva: la anterior + la renovación (o un año). Si eso ya pasó, desde hoy."""
    h = _hoy(hoy)
    meses = RENOVACIONES.get(fila.get("renovacion", ""), None) or 12
    f = leer_fecha(fila.get("vencimiento"))
    nueva = sumar_meses(f, meses) if pd.notna(f) else sumar_meses(h, meses)
    if nueva < h:
        nueva = sumar_meses(h, meses)
    return nueva


def renovar(d: pd.DataFrame, id_registro: str, nueva: Any, hoy: date | None = None) -> pd.DataFrame:
    nuevo = d.copy()
    m = nuevo["id_registro"] == id_registro
    if not m.any():
        return nuevo
    i = nuevo.index[m][0]
    nuevo.loc[i, "vencimiento_anterior"] = nuevo.loc[i, "vencimiento"]
    nuevo.loc[i, "vencimiento"] = fecha_txt(nueva)
    nuevo.loc[i, "renovado_el"] = _hoy(hoy).strftime("%d/%m/%Y")
    nuevo.loc[i, "estado"] = ""
    nuevo.loc[i, "updated_at"] = _ahora()
    return nuevo


def poner_estado(d: pd.DataFrame, id_registro: str, estado: str) -> pd.DataFrame:
    nuevo = d.copy()
    m = nuevo["id_registro"] == id_registro
    nuevo.loc[m, "estado"] = estado
    nuevo.loc[m, "updated_at"] = _ahora()
    return nuevo


def tabla_edicion(d: pd.DataFrame, hoy: date | None = None) -> pd.DataFrame:
    x = ordenados(preparar(d, hoy)) if len(d) else d.assign(_fecha=pd.NaT)
    return pd.DataFrame({
        "id": x["id_registro"], "Documento": x["documento"], "Vencimiento": x["_fecha"].dt.date if len(x) else [],
        "Categoría": x["categoria"], "Renovación": x["renovacion"], "Avisar (días antes)": x["aviso_dias"],
        "Responsable": x["responsable"], "Estado": x["estado"], "Costo": x["importe"], "Notas": x["observaciones"],
    }).reset_index(drop=True)


def aplicar_edicion(d: pd.DataFrame, editada: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """La tabla editada (con filas nuevas o borradas) → los datos. Devuelve (datos, cambios)."""
    previos = d.set_index("id_registro") if len(d) else pd.DataFrame(columns=COLUMNAS).set_index("id_registro")
    filas, cambios = [], 0
    vistos = set()
    for _, f in editada.iterrows():
        doc = re.sub(r"\s+", " ", _texto(f.get("Documento"))).strip()
        if not doc:
            continue
        i = _texto(f.get("id"))
        p = previos.loc[i] if i and i in previos.index else None
        aviso = numero(f.get("Avisar (días antes)"))
        nueva = {
            "id_registro": i if p is not None else _id(), "documento": doc,
            "vencimiento": fecha_txt(f.get("Vencimiento")),
            "categoria": _texto(f.get("Categoría")) or categoria_de(doc),
            "renovacion": _texto(f.get("Renovación")) if _texto(f.get("Renovación")) in RENOVACIONES else "",
            "aviso_dias": int(aviso) if aviso is not None and aviso >= 0 else AVISO,
            "responsable": _texto(f.get("Responsable")), "estado": _texto(f.get("Estado")) if _texto(f.get("Estado"))
            in ESTADOS else "", "importe": float(numero(f.get("Costo")) or 0.0), "observaciones": _texto(f.get("Notas")),
            "renovado_el": p["renovado_el"] if p is not None else "",
            "vencimiento_anterior": p["vencimiento_anterior"] if p is not None else "",
            "orden": 0, "updated_at": p["updated_at"] if p is not None else _ahora(),
        }
        if p is None:
            cambios += 1
        else:
            vistos.add(i)
            campos = ["documento", "vencimiento", "categoria", "renovacion", "aviso_dias", "responsable", "estado",
                      "importe", "observaciones"]
            if any(str(nueva[c]) != str(p[c]) for c in campos):
                cambios += 1
                if nueva["vencimiento"] != p["vencimiento"] and p["vencimiento"]:
                    nueva["vencimiento_anterior"] = p["vencimiento"]
                nueva["updated_at"] = _ahora()
        filas.append(nueva)
    cambios += len(set(previos.index) - vistos)
    return pd.DataFrame(filas, columns=COLUMNAS), cambios


# ---------------------------------------------------------------------------
# Importar la planilla (Excel o pegado)
# ---------------------------------------------------------------------------
def leer_tabla(valores: list[list[Any]]) -> dict[str, Any]:
    """La planilla (DOCUMENTO · VENCIMIENTO, más columnas opcionales) → filas."""
    cab, c_doc, c_ven = None, None, None
    for r, fila in enumerate(valores[:20]):
        n = [_sin_acentos(v) for v in fila]
        docs = [j for j, t in enumerate(n) if re.search(r"documento|detalle|concepto|descripcion|habilitacion|item", t)]
        vens = [j for j, t in enumerate(n) if re.search(r"vencimiento|vence|fecha", t)]
        if docs and vens:
            cab, c_doc, c_ven = r, docs[0], vens[0]
            break
    if cab is None:
        # Sin títulos: la primera columna con texto y la primera con fechas.
        for fila in valores[:5]:
            for j, v in enumerate(fila):
                if pd.notna(leer_fecha(v)) and j > 0:
                    cab, c_doc, c_ven = -1, j - 1, j
                    break
            if cab is not None:
                break
    if cab is None:
        return {"error": "No encontré las columnas DOCUMENTO y VENCIMIENTO."}
    extras = {}
    if cab >= 0:
        for j, t in enumerate(_sin_acentos(v) for v in valores[cab]):
            for clave, patron in (("categoria", r"categ|tipo"), ("renovacion", r"renov|periodic|frecuen"),
                                  ("responsable", r"respons|encarg"), ("observaciones", r"observ|nota"),
                                  ("importe", r"costo|importe|monto")):
                if j not in (c_doc, c_ven) and re.search(patron, t) and clave not in extras:
                    extras[clave] = j
    filas, sin_fecha_raras = [], []
    for fila in valores[cab + 1:]:
        doc = re.sub(r"\s+", " ", _texto(fila[c_doc] if c_doc < len(fila) else "")).strip()
        if not doc:
            continue
        v = fila[c_ven] if c_ven < len(fila) else None
        f = leer_fecha(v)
        if pd.isna(f) and _texto(v):
            sin_fecha_raras.append(f"{doc}: «{_texto(v)}»")
        reg = {"documento": doc, "vencimiento": fecha_txt(f)}
        for clave, j in extras.items():
            reg[clave] = fila[j] if j < len(fila) else ""
        filas.append(reg)
    return {"filas": filas, "raras": sin_fecha_raras}


def leer_pegado(texto: str) -> list[list[Any]]:
    filas = [ln.split("\t") for ln in (texto or "").splitlines()]
    return [[c.strip() for c in f] for f in filas if any(c.strip() for c in f)]


def leer_archivo(nombre: str, contenido: bytes) -> list[list[Any]]:
    n = (nombre or "").lower()
    if n.endswith((".csv", ".txt")):
        import csv

        texto = contenido.decode("utf-8-sig", errors="replace")
        sep = "\t" if "\t" in texto.splitlines()[0] else (";" if texto.count(";") > texto.count(",") else ",")
        return [f for f in csv.reader(io.StringIO(texto), delimiter=sep)]
    df = pd.read_excel(io.BytesIO(contenido), header=None)
    return df.astype(object).where(df.notna(), None).values.tolist()


def plan_importacion(actual: pd.DataFrame, leido: dict[str, Any], *, reemplazar: bool = False) -> pd.DataFrame:
    """Agrega o actualiza por nombre del documento (sin perder categoría, notas…). Con ``reemplazar``,
    la planilla pasa a ser exactamente la importada."""
    act = actual.copy() if set(COLUMNAS) <= set(actual.columns) else normalizar(actual)
    clave = lambda t: re.sub(r"[^a-z0-9]+", " ", _sin_acentos(t)).strip()  # noqa: E731
    previos = {clave(doc): i for i, doc in zip(act.index, act["documento"])}
    filas = []
    usados = set()
    for k, reg in enumerate(leido.get("filas", [])):
        i = previos.get(clave(reg["documento"]))
        if i is not None:
            base = act.loc[i].to_dict()
            usados.add(i)
            if reg["vencimiento"] and reg["vencimiento"] != base["vencimiento"]:
                base["vencimiento_anterior"] = base["vencimiento"]
                base["vencimiento"] = reg["vencimiento"]
                base["estado"] = "" if base["estado"] == EN_TRAMITE else base["estado"]
        else:
            base = {c: "" for c in COLUMNAS} | {"id_registro": _id(), "documento": reg["documento"],
                                                 "vencimiento": reg["vencimiento"],
                                                 "categoria": categoria_de(reg["documento"]),
                                                 "aviso_dias": AVISO, "importe": 0.0}
        if reg.get("categoria") and _texto(reg["categoria"]) in CATEGORIAS:
            base["categoria"] = _texto(reg["categoria"])
        if reg.get("renovacion"):
            base["renovacion"] = _renovacion(reg["renovacion"]) or base.get("renovacion", "")
        for c in ("responsable", "observaciones"):
            if _texto(reg.get(c)):
                base[c] = _texto(reg[c])
        if reg.get("importe") not in (None, ""):
            base["importe"] = float(numero(reg["importe"]) or 0.0)
        base["orden"] = k
        base["updated_at"] = _ahora()
        filas.append(base)
    nuevo = pd.DataFrame(filas, columns=COLUMNAS)
    if not reemplazar:
        resto = act.drop(index=list(usados))
        nuevo = pd.concat([nuevo, resto], ignore_index=True)
    return nuevo.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
def excel(d: pd.DataFrame, hoy: date | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    x = ordenados(preparar(d, hoy))
    x = x[x["_activo"]]
    wb = Workbook()
    ws = wb.active
    ws.title = "Vencimientos"
    linea = Side(style="thin", color="BBBBBB")
    borde = Border(left=linea, right=linea, top=linea, bottom=linea)
    ws.append(["DOCUMENTO", "VENCIMIENTO", "SITUACIÓN", "FALTAN", "CATEGORÍA", "RENOVACIÓN", "RESPONSABLE", "NOTAS"])
    for c in ws[1]:
        c.font, c.border = Font(bold=True), borde
    for _, r in x.iterrows():
        sit = EN_TRAMITE if r["estado"] == EN_TRAMITE else r["_situacion"]
        ws.append([r["documento"], r["_fecha"].to_pydatetime() if pd.notna(r["_fecha"]) else None, sit,
                   faltan_txt(r["_dias"]), r["categoria"], r["renovacion"], r["responsable"], r["observaciones"]])
        fondo = COLOR.get(sit, COLOR[VIGENTE])[0].lstrip("#")
        for c in ws[ws.max_row]:
            c.border = borde
            if fondo != "FFFFFF":
                c.fill = PatternFill("solid", fgColor=fondo)
        ws.cell(ws.max_row, 2).number_format = "D/M/YYYY"
        ws.cell(ws.max_row, 2).alignment = Alignment(horizontal="right")
    for col, ancho in zip("ABCDEFGH", (52, 14, 18, 20, 26, 14, 18, 40)):
        ws.column_dimensions[col].width = ancho
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------
_CSS = """<style>
.vc-wrap{overflow-x:auto;border:1px solid #E8E3E6;border-radius:12px;background:#fff;margin:.3rem 0 .8rem}
.vc-tab{border-collapse:collapse;font-size:.86rem;width:100%}
.vc-tab th,.vc-tab td{padding:.42rem .7rem;border-bottom:1px solid #F0ECEE;text-align:left;vertical-align:middle}
.vc-tab th{background:#F4F1F3;color:#5E1A39;font-weight:700;white-space:nowrap}
.vc-tab td.doc{font-weight:700;color:#2B2226}.vc-tab td.f{white-space:nowrap;text-align:right;font-variant-numeric:tabular-nums}
.vc-tab td.falta{white-space:nowrap;color:#6B5F66}
.vc-pill{display:inline-block;border-radius:999px;padding:.1rem .55rem;font-size:.74rem;font-weight:700;white-space:nowrap}
.vc-sub{color:#8A7F85;font-size:.76rem}
.vc-mes{font-weight:800;color:#5E1A39;margin:.7rem 0 .2rem;font-size:.95rem}
.vc-item{display:flex;justify-content:space-between;gap:.6rem;border:1px solid #EFE7EB;border-radius:10px;padding:.35rem .7rem;margin:.2rem 0;background:#fff}
</style>"""


def _pill(sit: str) -> str:
    fondo, texto = COLOR.get(sit, COLOR[VIGENTE])
    borde = "border:1px solid #E1D6DB;" if fondo == "#FFFFFF" else ""
    return f'<span class="vc-pill" style="background:{fondo};color:{texto};{borde}">{html.escape(sit)}</span>'


def html_tabla(x: pd.DataFrame) -> str:
    e = html.escape
    filas = []
    for _, r in x.iterrows():
        sit = EN_TRAMITE if r["estado"] == EN_TRAMITE else r["_situacion"]
        fondo = COLOR[VENCIDO][0] if (sit == VENCIDO) else ("#FFFAF0" if sit in (SEMANA, POR_VENCER) else "#fff")
        extra = []
        if r["estado"] == EN_TRAMITE and r["_situacion"] == VENCIDO:
            extra.append("vencido")
        if r["renovado_el"]:
            extra.append(f"renovado el {r['renovado_el']}")
        sub = " · ".join(x_ for x_ in [r["categoria"], r["responsable"]] if x_)
        filas.append(
            f'<tr style="background:{fondo}"><td class="doc">{e(r["documento"])}'
            f'<div class="vc-sub">{e(sub)}</div></td>'
            f'<td class="f">{e(r["vencimiento"]) or "—"}</td><td class="falta">{e(faltan_txt(r["_dias"]))}</td>'
            f'<td>{_pill(sit)}{"<div class=vc-sub>" + e(" · ".join(extra)) + "</div>" if extra else ""}</td>'
            f'<td>{e(r["renovacion"])}</td><td class="vc-sub">{e(r["observaciones"])}</td></tr>')
    return ('<div class="vc-wrap"><table class="vc-tab"><tr><th>Documento</th><th style="text-align:right">Vencimiento'
            '</th><th>Faltan</th><th>Situación</th><th>Renovación</th><th>Notas</th></tr>' + "".join(filas) +
            "</table></div>")


def render_vencimientos(raw: pd.DataFrame | None, *, guardar: Callable[[pd.DataFrame], Any],
                        selector: Callable[..., str] | None = None,
                        mostrar_error: Callable[[str, Exception], None] | None = None,
                        hoy: date | None = None) -> None:
    import streamlit as st

    from vitae.core.ui import tarjetas_kpi

    d = normalizar(raw)
    hoy = hoy or date.today()
    k = "vc_"
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
        px = r["proximo"]
        tarjetas_kpi([
            ("Vencidos", str(r["vencidos"]), lista_corta(r["vencidos_nombres"], 2) or "ninguno",
             "#BD4B4B" if r["vencidos"] else "#5E9C76"),
            ("Esta semana", str(r["prox_7"]), lista_corta(r["prox_7_nombres"], 2) or "nada", "#C9895B"),
            ("Próximos 30 días", str(r["prox_30"]),
             f"El próximo: {px['documento']} ({px['vencimiento']})" if px else "—", "#D4A72C"),
            ("En trámite", str(r["en_tramite"]), f"{r['vigentes']} vigentes · {r['sin_fecha']} sin fecha", "#4F7CAC"),
        ])

    if selector:
        seccion = selector("Sección", SECCIONES, key=f"{k}seccion")
    else:
        seccion = st.segmented_control("Sección", SECCIONES, default=SECCIONES[0], key=f"{k}seccion",
                                       label_visibility="collapsed") or SECCIONES[0]

    # ---- Planilla --------------------------------------------------------------
    if seccion == "Planilla":
        if d.empty:
            st.info("Todavía no hay vencimientos. Traé la planilla en «Importar» o cargalos en «Editar».")
            return
        x = ordenados(preparar(d, hoy))
        c1, c2, c3 = st.columns([1.6, 1.2, 1])
        with c1:
            filtro = st.pills("Mostrar", ["Todos", "Para atender", "Próximos 90 días", "Archivados"], default="Todos",
                              key=f"{k}filtro") or "Todos"
        with c2:
            cats = st.multiselect("Categoría", CATEGORIAS, key=f"{k}cat", placeholder="Todas las categorías",
                                  label_visibility="collapsed")
        with c3:
            st.download_button("Descargar Excel", data=excel(d, hoy), file_name=f"vencimientos_{hoy:%Y%m%d}.xlsx",
                               key=f"{k}xls", icon=":material/download:", use_container_width=True,
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        buscar = st.text_input("Buscar", key=f"{k}buscar", placeholder="Buscar documento, responsable o nota…",
                               label_visibility="collapsed")
        v = x[x["_activo"]] if filtro != "Archivados" else x[~x["_activo"]]
        if filtro == "Para atender":
            v = v[v["_situacion"].isin([VENCIDO, SEMANA, POR_VENCER]) | (v["estado"] == EN_TRAMITE)]
        elif filtro == "Próximos 90 días":
            v = v[v["_dias"].between(0, 90)]
        if cats:
            v = v[v["categoria"].isin(cats)]
        if buscar.strip():
            t = _sin_acentos(buscar)
            v = v[(v["documento"] + " " + v["responsable"] + " " + v["observaciones"]).map(_sin_acentos).str.contains(
                re.escape(t), na=False)]
        if v.empty:
            st.info("No hay vencimientos con ese filtro.")
        else:
            st.markdown(html_tabla(v), unsafe_allow_html=True)

        # Renovar o marcar en trámite, sin entrar a editar
        activos = ordenados(x[x["_activo"]])
        if len(activos):
            st.markdown("##### Renovar")
            opciones = list(activos["id_registro"])
            nombre = dict(zip(activos["id_registro"], activos["documento"] + " · " + activos["vencimiento"].replace("", "sin fecha")))
            c1, c2 = st.columns([1.8, 1])
            elegido = c1.selectbox("Documento", opciones, key=f"{k}r_doc", format_func=lambda i: nombre.get(i, i))
            fila = d[d["id_registro"] == elegido].iloc[0]
            sug = sugerir_renovacion(fila, hoy)
            nueva = c2.date_input("Nuevo vencimiento", value=sug.date(), format="DD/MM/YYYY", key=f"{k}r_fecha_{elegido}")
            if fila["renovacion"]:
                st.caption(f"Se renueva {fila['renovacion'].lower()}: te sugiero {sug:%d/%m/%Y}.")
            b1, b2, b3 = st.columns(3)
            if b1.button("Renovado", type="primary", key=f"{k}r_ok", icon=":material/event_available:",
                         use_container_width=True):
                _guardar(renovar(d, elegido, nueva, hoy), f"{fila['documento']}: vence el {fecha_txt(nueva)}")
            tram = fila["estado"] == EN_TRAMITE
            if b2.button("Quitar «en trámite»" if tram else "En trámite", key=f"{k}r_tram", icon=":material/pending:",
                         use_container_width=True):
                _guardar(poner_estado(d, elegido, "" if tram else EN_TRAMITE),
                         f"{fila['documento']}: {'sin trámite' if tram else 'en trámite'}")
            if b3.button("Archivar", key=f"{k}r_arch", icon=":material/archive:", use_container_width=True,
                         help="Lo saca de las alertas sin borrarlo (se ve en «Archivados»)."):
                _guardar(poner_estado(d, elegido, ARCHIVADO), f"{fila['documento']} archivado")
        return

    # ---- Editar ----------------------------------------------------------------
    if seccion == "Editar":
        st.caption("Cambiá lo que quieras como en una planilla: nombre, fecha (tocá la celda para el calendario), "
                   "categoría, cada cuánto se renueva, cuántos días antes avisar, responsable o notas. Agregá abajo "
                   "o borrá filas. «En trámite» = ya lo estás gestionando; «Archivado» = no avisar más.")
        tabla = tabla_edicion(d, hoy)
        ed = st.data_editor(
            tabla, hide_index=True, use_container_width=True, num_rows="dynamic", key=f"{k}ed_{rev}",
            column_config={
                "id": None,
                "Documento": st.column_config.TextColumn(required=True, width="large"),
                "Vencimiento": st.column_config.DateColumn(format="DD/MM/YYYY"),
                "Categoría": st.column_config.SelectboxColumn(options=CATEGORIAS),
                "Renovación": st.column_config.SelectboxColumn(options=list(RENOVACIONES)),
                "Avisar (días antes)": st.column_config.NumberColumn(min_value=0, max_value=365, step=1, format="%d"),
                "Estado": st.column_config.SelectboxColumn(options=ESTADOS),
                "Costo": st.column_config.NumberColumn(format="localized", min_value=0.0),
            })
        if st.button("Guardar cambios", type="primary", key=f"{k}ed_ok", icon=":material/save:"):
            nuevo, n = aplicar_edicion(d, ed)
            if n:
                _guardar(nuevo, f"{n} cambio(s) guardado(s)")
            else:
                st.info("No hay cambios.")
        return

    # ---- Calendario ------------------------------------------------------------
    if seccion == "Calendario":
        x = preparar(d, hoy)
        x = ordenados(x[x["_activo"] & x["_fecha"].notna()])
        if x.empty:
            st.info("No hay vencimientos con fecha.")
            return
        h = _hoy(hoy)
        venc = x[x["_fecha"] < h]
        if len(venc):
            st.markdown('<div class="vc-mes" style="color:#B4452F">Ya vencidos</div>', unsafe_allow_html=True)
            st.markdown("".join(_item(r_) for _, r_ in venc.iterrows()), unsafe_allow_html=True)
        prox = x[x["_fecha"] >= h]
        hasta = st.pills("Ver", ["12 meses", "2 años", "Todo"], default="12 meses", key=f"{k}cal") or "12 meses"
        if hasta != "Todo":
            prox = prox[prox["_fecha"] < h + pd.DateOffset(months=12 if hasta == "12 meses" else 24)]
        for per, g in prox.groupby(prox["_fecha"].dt.to_period("M")):
            from vitae.modulos.pagos_pendientes import nombre_mes

            st.markdown(f'<div class="vc-mes">{html.escape(nombre_mes(str(per)))} · {len(g)}</div>',
                        unsafe_allow_html=True)
            st.markdown("".join(_item(r_) for _, r_ in g.iterrows()), unsafe_allow_html=True)
        return

    # ---- Importar --------------------------------------------------------------
    if seccion == "Importar":
        st.caption("Subí la planilla (Excel o CSV) o copiá las celdas desde Google Sheets (DOCUMENTO y VENCIMIENTO; "
                   "si tiene categoría, renovación, responsable o notas, también se leen) y pegalas acá.")
        modo = st.pills("Desde", ["Pegar", "Archivo"], default="Pegar", key=f"{k}i_modo") or "Pegar"
        valores = None
        if modo == "Archivo":
            arch = st.file_uploader("Planilla de vencimientos", type=["xlsx", "xls", "csv"], key=f"{k}i_arch_{rev}")
            if arch is not None:
                try:
                    valores = leer_archivo(arch.name, arch.getvalue())
                except Exception as exc:
                    (mostrar_error or (lambda m, e: st.error(f"{m} {e}")))("No pude leer el archivo.", exc)
                    return
        else:
            texto = st.text_area("Pegá acá", height=220, key=f"{k}i_pegar_{rev}",
                                 placeholder="DOCUMENTO\tVENCIMIENTO\nMANTENIMIENTO HIDRANTE\t1/9/2026")
            if texto.strip():
                valores = leer_pegado(texto)
        if not valores:
            return
        leido = leer_tabla(valores)
        if leido.get("error"):
            st.error(leido["error"])
            return
        if not leido["filas"]:
            st.warning("No encontré documentos.")
            return
        reemplazar = st.toggle("Reemplazar toda la planilla", value=False, key=f"{k}i_reemp",
                               help="Apagado: agrega los nuevos y actualiza las fechas de los que ya están (por nombre), "
                                    "sin perder categoría, responsable ni notas.")
        nuevo = plan_importacion(d, leido, reemplazar=reemplazar)
        prev = ordenados(preparar(nuevo, hoy))
        existentes = {re.sub(r"[^a-z0-9]+", " ", _sin_acentos(x_)).strip() for x_ in d["documento"]}
        nuevos_n = sum(1 for f_ in leido["filas"] if re.sub(r"[^a-z0-9]+", " ", _sin_acentos(f_["documento"])).strip()
                       not in existentes)
        st.markdown(f"**{len(leido['filas'])} documentos** · {nuevos_n} nuevos · "
                    f"{len(leido['filas']) - nuevos_n} ya estaban")
        if leido["raras"]:
            st.warning("Estas fechas no las entendí (quedan sin fecha): " + "; ".join(leido["raras"]))
        st.markdown(html_tabla(prev[prev["_activo"]]), unsafe_allow_html=True)
        if st.button("Guardar", type="primary", key=f"{k}i_ok", icon=":material/upload:"):
            _guardar(nuevo, f"Vencimientos importados: {len(leido['filas'])}")


def _item(r: pd.Series) -> str:
    sit = EN_TRAMITE if r["estado"] == EN_TRAMITE else r["_situacion"]
    e = html.escape
    return (f'<div class="vc-item"><div><b>{e(r["documento"])}</b><div class="vc-sub">{e(r["categoria"])}</div></div>'
            f'<div style="text-align:right">{e(r["vencimiento"])} {_pill(sit)}'
            f'<div class="vc-sub">{e(faltan_txt(r["_dias"]))}</div></div></div>')
