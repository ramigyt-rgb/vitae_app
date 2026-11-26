"""Pacientes: la base de personas de Vitae (todas las que pasaron por el edificio).

Datos sensibles (Ley 25.326, art. 2 y 8; Ley 26.529): por eso este módulo
- vive FUERA de MODULES: no entra en la precarga general, ni en el Panel, ni en la IA
  (Luz y el Copiloto no pueden leerlo: ver _TABLAS_PROHIBIDAS en vitae/ia/motor.py);
- sólo lo ven los usuarios de PACIENTES_USUARIOS (secret; por defecto, el usuario rchar);
- guarda sólo datos administrativos: identidad, documento, sexo, nacimiento, estado civil,
  N° de historia clínica y origen. Las observaciones libres del sistema de origen (que pueden
  tener datos clínicos) NO se importan;
- no muestra listados completos: se busca de a una persona (mínimo 3 letras o 4 números);
- registra quién buscó, abrió, importó o exportó (pestaña pacientes_accesos);
- exporta para investigación sólo datos anonimizados (sin nombre, documento ni HC);
- puede vivir en un archivo de Google Sheets aparte (secret PACIENTES_SHEET_ID), con
  permisos propios en Drive; si no está, usa una pestaña del archivo principal.

El Excel de origen (p. ej. «GP_Personas_ABM») se puede volver a importar cuando quieran:
se actualiza sin duplicar (clave = documento; si no hay documento válido, historia clínica).
"""
from __future__ import annotations

import difflib
import hashlib
import io
import re
import threading
import time
import unicodedata
from datetime import date, datetime
from typing import Any, Callable, Iterable

import pandas as pd

TABLA = "pacientes"
TABLA_ACCESOS = "pacientes_accesos"
COLUMNAS = ["clave", "tipo_doc", "nro_doc", "apellido_nombre", "sexo", "fecha_nac", "estado_civil",
            "historia_clinica", "origen", "revisar", "primera_carga", "actualizado"]
CAMPOS_DATOS = ["tipo_doc", "nro_doc", "apellido_nombre", "sexo", "fecha_nac", "estado_civil",
                "historia_clinica", "origen"]
COLUMNAS_ACCESOS = ["fecha_hora", "usuario", "accion", "detalle"]

_NACIONALES = {"DNI", "DU", "LC", "LE"}
_TIPOS = {"DNI": "DNI", "DNI DUP": "DNI", "DU": "DU", "LC": "LC", "LE": "LE", "CI": "CI",
          "PASAPORTE": "Pasaporte", "PAS": "Pasaporte", "DOC EXT": "Doc. extranjero", "NN": "NN", "RN": "RN"}
_PARTICULAS = {"de", "del", "la", "las", "los", "y", "da", "di", "do", "dos", "van", "von", "e"}
_SEXOS = {"femenino": "Femenino", "f": "Femenino", "mujer": "Femenino", "masculino": "Masculino",
          "m": "Masculino", "varon": "Masculino", "hombre": "Masculino", "indefinido": "Indefinido",
          "x": "Indefinido"}
_ESTADOS = {"casado": "Casado/a", "casada": "Casado/a", "soltero": "Soltero/a", "soltera": "Soltero/a",
            "union estable": "Unión estable", "union convivencial": "Unión estable", "viudo": "Viudo/a",
            "viuda": "Viudo/a", "divorciado": "Divorciado/a", "divorciada": "Divorciado/a",
            "separado": "Separado/a", "separada": "Separado/a"}
_ORIGENES = [("registro agregado a traves de turnos online", "Alta por turnos online"),
             ("registro actualizado a traves de turnos online", "Actualizado por turnos online"),
             ("registro agregado a traves de turnos", "Alta por turnos"),
             ("registro actualizado a traves de turnos", "Actualizado por turnos"),
             ("migracion laboratorio vitae", "Migración Laboratorio Vitae")]
FECHA_MINIMA = date(1900, 1, 1)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _sin_acentos(texto: Any) -> str:
    t = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in t if not unicodedata.combining(c))


def norm(texto: Any) -> str:
    """minúsculas, sin acentos, sólo letras/números separados por un espacio."""
    return " ".join(re.findall(r"[a-z0-9]+", _sin_acentos(texto).lower()))


def _texto(valor: Any) -> str:
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return re.sub(r"\s+", " ", str(valor)).strip()


def nombre_prolijo(texto: Any) -> str:
    t = _texto(texto)
    if not t:
        return ""
    t = re.sub(r"\s*,\s*", ", ", t)
    salida = []
    for i, palabra in enumerate(t.split(" ")):
        baja = palabra.lower()
        if i > 0 and baja in _PARTICULAS:
            salida.append(baja)
            continue
        partes = re.split(r"([-'’])", baja)
        salida.append("".join(p[:1].upper() + p[1:] if p not in "-'’" else p for p in partes))
    return " ".join(salida)


def tokens_nombre(texto: Any) -> list[str]:
    return [p for p in norm(texto).split() if len(p) >= 3 and p not in _PARTICULAS]


def parecido(a: str, b: str) -> float:
    a2, b2 = " ".join(sorted(tokens_nombre(a))), " ".join(sorted(tokens_nombre(b)))
    if not a2 or not b2:
        return 0.0
    return difflib.SequenceMatcher(None, a2, b2).ratio()


def fecha_de(valor: Any, hoy: date | None = None) -> date | None:
    """Fecha de un Excel (date, datetime, número de serie o texto dd/mm/aaaa)."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, pd.Timestamp):
        return None if pd.isna(valor) else valor.date()
    if isinstance(valor, (int, float)):
        if not 1 <= float(valor) < 2958466:
            return None
        return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=float(valor))).date()
    t = _texto(valor)
    if len(t) == 10 and t[4] == "-" and t[7] == "-":
        try:
            return date.fromisoformat(t)
        except ValueError:
            pass
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", t):
        f = pd.to_datetime(t[:10], errors="coerce")
    else:
        f = pd.to_datetime(t, errors="coerce", dayfirst=True)
    return None if pd.isna(f) else f.date()


def edad(fecha_nac: Any, hoy: date | None = None) -> int | None:
    f = fecha_de(fecha_nac)
    if f is None:
        return None
    hoy = hoy or date.today()
    return hoy.year - f.year - ((hoy.month, hoy.day) < (f.month, f.day))


def edades(fechas_iso: pd.Series, hoy: date | None = None) -> pd.Series:
    """Edad (años cumplidos) de toda una columna de fechas AAAA-MM-DD a la vez (NaN si no hay)."""
    hoy = hoy or date.today()
    f = pd.to_datetime(fechas_iso.where(fechas_iso.astype(str).str.len() >= 10), format="%Y-%m-%d", errors="coerce")
    cumplio = (f.dt.month < hoy.month) | ((f.dt.month == hoy.month) & (f.dt.day <= hoy.day))
    return (hoy.year - f.dt.year - (~cumplio).astype(int)).where(f.notna())


def texto_edad(fecha_nac: Any, hoy: date | None = None) -> str:
    f = fecha_de(fecha_nac)
    if f is None:
        return ""
    hoy = hoy or date.today()
    e = edad(f, hoy)
    if e is not None and e >= 2:
        return f"{e} años"
    meses = (hoy.year - f.year) * 12 + hoy.month - f.month - (hoy.day < f.day)
    return f"{max(meses, 0)} meses"


RANGOS = [(0, 17, "0-17"), (18, 24, "18-24"), (25, 29, "25-29"), (30, 34, "30-34"), (35, 39, "35-39"),
          (40, 44, "40-44"), (45, 49, "45-49"), (50, 59, "50-59"), (60, 69, "60-69"), (70, 200, "70+")]


def rango_edad(e: int | None) -> str:
    if e is None:
        return "Sin dato"
    return next((r for d, h, r in RANGOS if d <= e <= h), "Sin dato")


# ---------------------------------------------------------------------------
# Documento y clave única
# ---------------------------------------------------------------------------
def limpiar_documento(tipo: Any, numero: Any) -> tuple[str, str, str]:
    """(tipo prolijo, número prolijo, problema). problema vacío = documento válido para identificar."""
    tipo_t = _texto(tipo).upper()
    tipo_n = _TIPOS.get(tipo_t, tipo_t.title() if tipo_t else "")
    crudo = _texto(numero)
    if tipo_n in ("NN", "RN") or (not crudo and not tipo_n):
        return tipo_n or "NN", re.sub(r"\D", "", crudo) if crudo else "", "sin documento"
    if tipo_n in _NACIONALES or (not tipo_n and re.fullmatch(r"[\d.\- ]+", crudo)):
        tipo_n = tipo_n or "DNI"
        digitos = re.sub(r"\D", "", crudo).lstrip("0")
        if not digitos:
            return tipo_n, "", "sin documento"
        if re.fullmatch(r"(\d)\1+", digitos):
            return tipo_n, digitos, "documento inválido"
        minimo = 6 if tipo_n in ("LC", "LE") else 7
        if len(digitos) > 8:
            return tipo_n, digitos, "documento con dígitos de más"
        if len(digitos) < minimo:
            return tipo_n, digitos, "documento incompleto"
        return tipo_n, digitos, ""
    numero_n = re.sub(r"[^0-9A-Za-z]", "", crudo).upper()
    if len(numero_n) < 5:
        return tipo_n, numero_n, "documento incompleto" if numero_n else "sin documento"
    return tipo_n, numero_n, ""


def clave_de(tipo: str, numero: str, problema: str, historia_clinica: str, nombre: str, fecha_nac: str,
             crudo: str = "") -> str:
    if not problema and numero:
        prefijo = "DOC" if tipo in _NACIONALES else {"CI": "CI", "Pasaporte": "PAS"}.get(tipo, "EXT")
        return f"{prefijo}:{numero}"
    if historia_clinica:
        return f"HC:{historia_clinica}"
    base = f"{norm(nombre)}|{fecha_nac}|{crudo}"
    return "SD:" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]


def es_clave_documento(clave: str) -> bool:
    return clave.split(":", 1)[0] in {"DOC", "CI", "PAS", "EXT"}


def origen_de(observacion: Any) -> tuple[str, bool]:
    """(origen, tenía nota libre). Las notas libres no se importan (pueden ser clínicas)."""
    o = norm(observacion)
    if not o:
        return "", False
    for pista, origen in _ORIGENES:
        if o.startswith(pista):
            return origen, len(o) > len(pista) + 1  # lo que sigue (mail, teléfono, notas) no se importa
    return "", True


# ---------------------------------------------------------------------------
# Lectura del archivo
# ---------------------------------------------------------------------------
_ENCABEZADOS = {
    "tipo_doc": ["tipo doc", "tipo de documento", "tipo documento", "tipodoc"],
    "nro_doc": ["nro doc", "numero de documento", "nro documento", "documento", "dni", "n doc", "num doc"],
    "apellido_nombre": ["apellido y nombre", "apellido nombre", "nombre y apellido", "paciente", "nombre"],
    "sexo": ["sexo", "genero"],
    "estado_civil": ["estado civil"],
    "fecha_nac": ["fecha nac", "fecha de nacimiento", "nacimiento", "f nac", "fec nac"],
    "historia_clinica": ["historia clinica", "hc", "nro hc", "n hc", "historia"],
    "observaciones": ["observaciones", "observacion", "obs"],
}


def _mapear_encabezados(fila: list[Any]) -> dict[str, int]:
    nombres = [norm(c) for c in fila]
    mapa: dict[str, int] = {}
    for campo, opciones in _ENCABEZADOS.items():
        for op in opciones:
            if op in nombres and nombres.index(op) not in mapa.values():
                mapa[campo] = nombres.index(op)
                break
    return mapa


def hojas_del_archivo(contenido: bytes, nombre: str) -> list[str]:
    n = nombre.lower()
    if n.endswith(".csv"):
        return []
    if n.endswith(".xls"):
        import xlrd

        return xlrd.open_workbook(file_contents=contenido, on_demand=True).sheet_names()
    from openpyxl import load_workbook

    return load_workbook(io.BytesIO(contenido), read_only=True).sheetnames


def leer_archivo(contenido: bytes, nombre: str, hoja: str | int = 0) -> pd.DataFrame:
    """Excel (.xls/.xlsx) o .csv del sistema de turnos → columnas estándar (sin limpiar)."""
    n = nombre.lower()
    if n.endswith(".csv"):
        texto = contenido.decode("utf-8-sig", errors="replace")
        sep = ";" if texto.count(";") > texto.count(",") else ","
        matriz = pd.read_csv(io.StringIO(texto), sep=sep, header=None, dtype=str).fillna("").values.tolist()
    elif n.endswith(".xls"):
        import xlrd

        libro = xlrd.open_workbook(file_contents=contenido)
        idx = libro.sheet_names().index(hoja) if isinstance(hoja, str) and hoja else int(hoja or 0)
        matriz = _matriz_xls_hoja(libro, idx)
    else:
        matriz = pd.read_excel(io.BytesIO(contenido), sheet_name=hoja or 0, header=None,
                               dtype=object).fillna("").values.tolist()
    for i, fila in enumerate(matriz[:40]):
        mapa = _mapear_encabezados(list(fila))
        if "nro_doc" in mapa and "apellido_nombre" in mapa:
            filas = [list(f) for f in matriz[i + 1:]]
            datos = {campo: [f[c] if c < len(f) else "" for f in filas] for campo, c in mapa.items()}
            df = pd.DataFrame(datos)
            for campo in _ENCABEZADOS:
                if campo not in df.columns:
                    df[campo] = ""
            vacia = df.apply(lambda r: all(_texto(v) == "" for v in r), axis=1)
            return df[~vacia].reset_index(drop=True)[list(_ENCABEZADOS)]
    raise ValueError("No encontré los encabezados (Nro Doc., Apellido y Nombre…) en las primeras filas del archivo.")


def _matriz_xls_hoja(libro: Any, idx: int) -> list[list[Any]]:
    import xlrd

    hoja = libro.sheet_by_index(idx)
    out = []
    for r in range(hoja.nrows):
        fila = []
        for c in range(hoja.ncols):
            celda = hoja.cell(r, c)
            fila.append(xlrd.xldate_as_datetime(celda.value, libro.datemode)
                        if celda.ctype == xlrd.XL_CELL_DATE else celda.value)
        out.append(fila)
    return out


# ---------------------------------------------------------------------------
# Limpieza
# ---------------------------------------------------------------------------
def normalizar(crudo: pd.DataFrame, hoy: date | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Filas del archivo → pacientes limpios (una fila por persona) + informe."""
    hoy = hoy or date.today()
    informe = {"filas": int(len(crudo)), "sin_documento_valido": 0, "fechas_descartadas": 0,
               "notas_no_importadas": 0, "unidos": 0, "nombres_distintos": 0}
    registros: list[dict[str, str]] = []
    for r in crudo.to_dict("records"):
        nombre = nombre_prolijo(r.get("apellido_nombre"))
        if not nombre and not _texto(r.get("nro_doc")):
            continue
        tipo, numero, problema = limpiar_documento(r.get("tipo_doc"), r.get("nro_doc"))
        motivos = [problema] if problema else []
        f = fecha_de(r.get("fecha_nac"))
        fecha_txt = ""
        if f is not None:
            if FECHA_MINIMA <= f <= hoy:
                fecha_txt = f.isoformat()
            else:
                motivos.append(f"fecha de nacimiento imposible ({f.day:02d}/{f.month:02d}/{f.year})")
                informe["fechas_descartadas"] += 1
        origen, tenia_nota = origen_de(r.get("observaciones"))
        informe["notas_no_importadas"] += int(tenia_nota)
        hc = _texto(r.get("historia_clinica"))
        sexo = _SEXOS.get(norm(r.get("sexo")), _texto(r.get("sexo")).capitalize())
        estado = _ESTADOS.get(norm(r.get("estado_civil")), "")
        if problema:
            informe["sin_documento_valido"] += 1
        registros.append({
            "clave": clave_de(tipo, numero, problema, hc, nombre, fecha_txt, _texto(r.get("nro_doc"))),
            "tipo_doc": tipo, "nro_doc": numero, "apellido_nombre": nombre, "sexo": sexo,
            "fecha_nac": fecha_txt, "estado_civil": estado, "historia_clinica": hc, "origen": origen,
            "revisar": "; ".join(motivos),
        })
    # Una fila por clave: se unen los repetidos (gana la fila más completa; los huecos se completan).
    por_clave: dict[str, dict[str, str]] = {}
    for reg in registros:
        previo = por_clave.get(reg["clave"])
        if previo is None:
            por_clave[reg["clave"]] = reg
            continue
        informe["unidos"] += 1
        a, b = (previo, reg) if _completo(previo) >= _completo(reg) else (reg, previo)
        unido = dict(a)
        for c in CAMPOS_DATOS:
            if not unido[c] and b[c]:
                unido[c] = b[c]
        motivos = [m for m in (a["revisar"].split("; ") + b["revisar"].split("; ")) if m]
        if parecido(a["apellido_nombre"], b["apellido_nombre"]) < 0.75:
            motivos.append(f"mismo documento con otro nombre: {b['apellido_nombre']}")
            informe["nombres_distintos"] += 1
        unido["revisar"] = "; ".join(dict.fromkeys(motivos))
        por_clave[reg["clave"]] = unido
    df = pd.DataFrame(list(por_clave.values()), columns=[c for c in COLUMNAS if c not in ("primera_carga", "actualizado")])
    informe["pacientes"] = int(len(df))
    informe["para_revisar"] = int((df["revisar"] != "").sum()) if len(df) else 0
    return df, informe


def _completo(reg: dict[str, str]) -> int:
    return sum(bool(reg.get(c)) for c in CAMPOS_DATOS)


# ---------------------------------------------------------------------------
# Importación sin duplicar
# ---------------------------------------------------------------------------
def preparar(actual: pd.DataFrame | None) -> pd.DataFrame:
    if not isinstance(actual, pd.DataFrame) or actual.empty:
        return pd.DataFrame(columns=COLUMNAS)
    df = actual.copy()
    for c in COLUMNAS:
        if c not in df.columns:
            df[c] = ""
    return df[COLUMNAS].fillna("").astype(str).reset_index(drop=True)


def plan_importacion(actual: pd.DataFrame | None, nuevo: pd.DataFrame, hoy: date | None = None) -> dict[str, Any]:
    """Qué cambia: pacientes nuevos, actualizados (dato a dato) y sin cambios.

    Nunca borra a nadie que no venga en el archivo y nunca vacía un dato que ya estaba.
    """
    hoy_t = (hoy or date.today()).isoformat()
    actual = preparar(actual)
    filas = actual.to_dict("records")
    por_clave = {f["clave"]: i for i, f in enumerate(filas)}
    por_hc = {}
    for i, f in enumerate(filas):
        if f["historia_clinica"]:
            por_hc.setdefault(f["historia_clinica"], i)
    nuevos: list[dict[str, str]] = []
    cambios: dict[int, dict[str, str]] = {}
    campos_cambiados: dict[str, int] = {}
    sin_cambios = reclaves = 0
    for reg in nuevo.to_dict("records"):
        pos = por_clave.get(reg["clave"])
        if pos is None and reg.get("historia_clinica") and reg["historia_clinica"] in por_hc:
            candidato = por_hc[reg["historia_clinica"]]
            # Misma HC: es la misma persona si el nombre se parece (p. ej. ahora trae DNI).
            if parecido(filas[candidato]["apellido_nombre"], reg["apellido_nombre"]) >= 0.75:
                pos = candidato
        if pos is None:
            fila = {c: reg.get(c, "") for c in COLUMNAS}
            fila.update(primera_carga=hoy_t, actualizado=hoy_t)
            nuevos.append(fila)
            por_clave[fila["clave"]] = -1
            continue
        if pos < 0:
            continue  # repetido dentro del mismo archivo (ya se unió en normalizar)
        base = dict(cambios.get(pos) or filas[pos])
        cambio = False
        if reg["clave"] != base["clave"] and es_clave_documento(reg["clave"]) and not es_clave_documento(base["clave"]):
            base["clave"] = reg["clave"]
            reclaves += 1
            cambio = True
        for c in CAMPOS_DATOS:
            v = reg.get(c, "")
            if v and v != base[c]:
                base[c] = v
                campos_cambiados[c] = campos_cambiados.get(c, 0) + 1
                cambio = True
        if reg.get("revisar", "") != base["revisar"]:
            base["revisar"] = reg.get("revisar", "")
            cambio = True
        if cambio:
            base["actualizado"] = hoy_t
            cambios[pos] = base
        else:
            sin_cambios += 1
    return {"nuevos": pd.DataFrame(nuevos, columns=COLUMNAS), "cambios": cambios, "sin_cambios": sin_cambios,
            "campos": campos_cambiados, "reclaves": reclaves, "total_actual": len(filas)}


# ---------------------------------------------------------------------------
# Búsqueda, ficha y calidad
# ---------------------------------------------------------------------------
def con_indices(df: pd.DataFrame) -> pd.DataFrame:
    """Columnas auxiliares para buscar rápido (se calculan una vez por lectura)."""
    df = preparar(df)
    df["_nombre"] = df["apellido_nombre"].map(norm)
    df["_tokens"] = df["apellido_nombre"].map(lambda t: " ".join(sorted(tokens_nombre(t))))
    return df


def buscar(df: pd.DataFrame, texto: str, limite: int = 50) -> pd.DataFrame:
    t = _texto(texto)
    if "_nombre" not in df.columns:
        df = con_indices(df)
    digitos = re.sub(r"\D", "", t)
    if digitos and len(digitos) >= 4 and len(digitos) >= len(re.sub(r"[\s.\-]", "", t)) - 1:
        d = digitos.lstrip("0") or digitos
        exacto = (df["nro_doc"] == d) | (df["historia_clinica"] == digitos) | (df["historia_clinica"] == d)
        if exacto.any() or len(d) >= 7:
            return df[exacto].head(limite)
        mask = df["nro_doc"].str.startswith(d) | df["historia_clinica"].str.startswith(digitos)
        return df[mask].head(limite)
    partes = norm(t).split()
    if not partes or sum(len(p) for p in partes) < 3:
        return df.iloc[0:0]
    mask = pd.Series(True, index=df.index)
    for p in partes:
        mask &= df["_nombre"].str.contains(rf"\b{re.escape(p)}", regex=True, na=False)
    return df[mask].sort_values("apellido_nombre").head(limite)


def posibles_duplicados(df: pd.DataFrame) -> pd.DataFrame:
    """Mismas palabras del nombre y misma fecha de nacimiento, con claves distintas."""
    if "_tokens" not in df.columns:
        df = con_indices(df)
    x = df[(df["fecha_nac"] != "") & (df["_tokens"] != "")]
    rep = x[x.duplicated(["_tokens", "fecha_nac"], keep=False)]
    if rep.empty:
        return rep
    rep = rep.assign(grupo=rep.groupby(["_tokens", "fecha_nac"]).ngroup() + 1)
    return rep.sort_values(["grupo", "apellido_nombre"])


def motivos_revisar(df: pd.DataFrame) -> pd.Series:
    serie = df["revisar"].str.split("; ").explode()
    serie = serie[serie.fillna("") != ""].map(lambda m: re.sub(r"\s*\(.*\)$|:.*$", "", m))
    return serie.value_counts()


def completitud(df: pd.DataFrame) -> dict[str, float]:
    if df.empty:
        return {}
    return {
        "Documento válido": float((~df["clave"].str.match(r"^(HC|SD):")).mean()),
        "Fecha de nacimiento": float((df["fecha_nac"] != "").mean()),
        "Historia clínica": float((df["historia_clinica"] != "").mean()),
        "Sexo": float(df["sexo"].isin(["Femenino", "Masculino"]).mean()),
        "Estado civil": float((df["estado_civil"] != "").mean()),
    }


def tabla_edades(df: pd.DataFrame, hoy: date | None = None) -> pd.DataFrame:
    hoy = hoy or date.today()
    eds = edades(df["fecha_nac"], hoy)
    x = pd.DataFrame({"rango": eds.map(lambda e: rango_edad(None if pd.isna(e) else int(e))),
                      "sexo": df["sexo"].replace("", "Sin dato")})
    orden = [r for _, _, r in RANGOS] + ["Sin dato"]
    t = x.groupby(["rango", "sexo"]).size().unstack(fill_value=0)
    return t.reindex([r for r in orden if r in t.index])


_COLUMNAS_PERSONA = ("paciente", "afiliado", "nombre", "titular", "cliente", "persona", "apellido")
_COLUMNAS_DOC = ("dni", "documento", "nro_doc", "doc")


def menciones(paciente: dict[str, Any], tablas: dict[str, pd.DataFrame], limite: int = 5) -> list[dict[str, Any]]:
    """Dónde aparece esta persona en el resto de Vitae (facturación, quirófano, gine, farmacia…)."""
    toks = tokens_nombre(paciente.get("apellido_nombre", ""))
    doc = _texto(paciente.get("nro_doc"))
    out = []
    for modulo, df in tablas.items():
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        cols_p = [c for c in df.columns if any(p in norm(c).replace(" ", "_") for p in _COLUMNAS_PERSONA)]
        cols_d = [c for c in df.columns if any(norm(c).replace(" ", "_") == p or norm(c).startswith(p + " ")
                                               or norm(c) == p for p in _COLUMNAS_DOC)]
        mask = pd.Series(False, index=df.index)
        if len(toks) >= 2:
            for c in cols_p:
                n = df[c].map(norm)
                m = pd.Series(True, index=df.index)
                for t in toks:
                    m &= n.str.contains(rf"\b{re.escape(t)}\b", regex=True, na=False)
                mask |= m
        if doc and len(doc) >= 7:
            for c in cols_d:
                mask |= df[c].map(lambda v: re.sub(r"\D", "", str(v)).lstrip("0")) == doc
        if mask.any():
            filas = df[mask]
            out.append({"modulo": modulo, "cantidad": int(mask.sum()), "filas": filas.head(limite)})
    return sorted(out, key=lambda m: -m["cantidad"])


def exportar_anonimo(df: pd.DataFrame, hoy: date | None = None, semilla: int | None = None) -> bytes:
    """Excel para investigación: SIN nombre, documento ni HC. Edad en rangos de 5 años."""
    hoy = hoy or date.today()
    base = preparar(df)
    eds = edades(base["fecha_nac"], hoy)
    anon = pd.DataFrame({
        "sexo": base["sexo"].replace("", "Sin dato"),
        "rango_edad": eds.map(lambda e: rango_edad(None if pd.isna(e) else int(e))),
        "origen": base["origen"].replace("", "Sin dato"),
        "año_primera_carga": base["primera_carga"].str[:4].replace("", "Sin dato"),
    })
    anon = anon.sample(frac=1, random_state=semilla).reset_index(drop=True)  # el orden no delata a nadie
    anon.insert(0, "id_anonimo", [f"A{i + 1:06d}" for i in range(len(anon))])
    resumen = anon.groupby(["rango_edad", "sexo"]).size().unstack(fill_value=0)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as xl:
        pd.DataFrame({"Vitae · datos anonimizados": [
            f"Generado el {hoy:%d/%m/%Y}. Sin nombre, documento ni historia clínica.",
            "Uso: investigación y estadística interna. No intentar reidentificar personas.",
        ]}).to_excel(xl, sheet_name="Leeme", index=False)
        anon.to_excel(xl, sheet_name="Pacientes", index=False)
        resumen.to_excel(xl, sheet_name="Edad x sexo")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Almacenamiento (Google Sheets, con caché propia)
# ---------------------------------------------------------------------------
_TANDA_FILAS = 5000
_TANDA_RANGOS = 1000


def _letra(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class AlmacenSheets:
    """Lee y escribe la pestaña de pacientes sin pasar por la precarga general de la app.

    - Caché propia (compartida por todas las sesiones del servidor): leer 60.000 filas sólo cuando
      hace falta, no cada vez que alguien guarda algo en otro módulo.
    - Escribe sólo lo que cambió: filas nuevas al final (por tandas) y filas modificadas en su lugar.
    - value_input_option RAW: Google no convierte documentos ni fechas (no se pierden ceros).
    """

    _lock = threading.RLock()
    _cache: dict[str, Any] = {}
    TTL = 30 * 60

    def __init__(self, sheet_id: str = "", usuario: str = ""):
        self.sheet_id = sheet_id
        self.usuario = usuario

    def _archivo(self):
        from vitae.core.database import get_gs_client, get_spreadsheet

        return get_gs_client().open_by_key(self.sheet_id) if self.sheet_id else get_spreadsheet()

    def _hoja(self, nombre: str, columnas: list[str]):
        import gspread

        archivo = self._archivo()
        try:
            ws = archivo.worksheet(nombre)
        except gspread.WorksheetNotFound:
            ws = archivo.add_worksheet(title=nombre, rows=1000, cols=len(columnas))
            ws.update([columnas], "A1", value_input_option="RAW")
        return ws

    def leer(self, refrescar: bool = False) -> pd.DataFrame:
        with self._lock:
            c = self._cache.get(self.sheet_id or "-")
            if c is not None and not refrescar and time.time() - c["cuando"] < self.TTL:
                return c["df"]
            ws = self._hoja(TABLA, COLUMNAS)
            valores = ws.get_all_values()
            if len(valores) < 2:
                df = preparar(None)
            else:
                ancho = len(valores[0])
                df = pd.DataFrame([f + [""] * (ancho - len(f)) for f in valores[1:]], columns=valores[0])
                df = df[[c for c in df.columns if c]]
            df = con_indices(df)
            self._cache[self.sheet_id or "-"] = {"df": df, "cuando": time.time(), "encabezado": valores[:1]}
            return df

    def guardar_plan(self, plan: dict[str, Any], progreso: Callable[[float, str], None] | None = None) -> None:
        ws = self._hoja(TABLA, COLUMNAS)
        actual = self.leer(refrescar=True)
        if len(actual) != plan.get("total_actual", len(actual)):
            raise RuntimeError("La base de pacientes cambió mientras tanto (otra importación). Tocá «Releer» y "
                               "volvé a importar: no se modificó nada.")
        encabezado = ws.row_values(1)
        if encabezado != COLUMNAS:
            if any(encabezado):
                faltan = [c for c in COLUMNAS if c not in encabezado]
                if faltan or len(actual):
                    raise RuntimeError("La pestaña de pacientes tiene otras columnas. No la toco para no romperla.")
            ws.update([COLUMNAS], "A1", value_input_option="RAW")
        ultima = _letra(len(COLUMNAS))
        cambios = list(plan["cambios"].items())
        nuevos = plan["nuevos"][COLUMNAS].astype(str).values.tolist() if len(plan["nuevos"]) else []
        pasos = max(1, -(-len(cambios) // _TANDA_RANGOS) + -(-len(nuevos) // _TANDA_FILAS))
        hecho = 0
        for i in range(0, len(cambios), _TANDA_RANGOS):
            tanda = [{"range": f"A{pos + 2}:{ultima}{pos + 2}", "values": [[fila[c] for c in COLUMNAS]]}
                     for pos, fila in cambios[i:i + _TANDA_RANGOS]]
            ws.batch_update(tanda, value_input_option="RAW")
            hecho += 1
            if progreso:
                progreso(hecho / pasos, f"Actualizando pacientes ({min(i + _TANDA_RANGOS, len(cambios))}/{len(cambios)})")
        if nuevos:
            necesarias = len(actual) + 1 + len(nuevos)
            if ws.row_count < necesarias:
                ws.add_rows(necesarias - ws.row_count)
            inicio = len(actual) + 2
            for i in range(0, len(nuevos), _TANDA_FILAS):
                tanda = nuevos[i:i + _TANDA_FILAS]
                fila = inicio + i
                ws.update(tanda, f"A{fila}:{ultima}{fila + len(tanda) - 1}", value_input_option="RAW")
                hecho += 1
                if progreso:
                    progreso(hecho / pasos, f"Cargando pacientes nuevos ({min(i + _TANDA_FILAS, len(nuevos))}/{len(nuevos)})")
        with self._lock:
            self._cache.pop(self.sheet_id or "-", None)

    def registrar(self, accion: str, detalle: str = "") -> None:
        """Registro de accesos (quién, cuándo, qué). Nunca guarda el nombre del paciente: sólo su clave."""
        try:
            ws = self._hoja(TABLA_ACCESOS, COLUMNAS_ACCESOS)
            ws.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.usuario, accion, detalle[:300]],
                          value_input_option="RAW", table_range="A1")
        except Exception:
            pass  # el registro nunca bloquea la pantalla


class AlmacenMemoria:
    """Para pruebas: misma interfaz que AlmacenSheets, todo en memoria."""

    def __init__(self, df: pd.DataFrame | None = None, usuario: str = "prueba"):
        self.df = preparar(df)
        self.usuario = usuario
        self.accesos: list[list[str]] = []

    def leer(self, refrescar: bool = False) -> pd.DataFrame:
        return con_indices(self.df)

    def guardar_plan(self, plan: dict[str, Any], progreso: Callable[[float, str], None] | None = None) -> None:
        df = self.df.copy()
        for pos, fila in plan["cambios"].items():
            df.loc[pos, COLUMNAS] = [fila[c] for c in COLUMNAS]
        if len(plan["nuevos"]):
            df = pd.concat([df, plan["nuevos"][COLUMNAS]], ignore_index=True)
        self.df = preparar(df)
        if progreso:
            progreso(1.0, "Listo")

    def registrar(self, accion: str, detalle: str = "") -> None:
        self.accesos.append([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.usuario, accion, detalle])


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------
_CSS = """<style>
.pac-aviso{background:#F7F1F4;border:1px solid #E8D9E0;border-radius:12px;padding:.55rem .8rem;color:#5E1A39;
  font-size:.86rem;margin:.2rem 0 .8rem}
.pac-ficha{background:#fff;border:1px solid #E8D9E0;border-left:5px solid #8C2853;border-radius:14px;padding:.9rem 1.1rem}
.pac-nombre{font-size:1.35rem;font-weight:700;color:#3A1426;margin-bottom:.15rem}
.pac-sub{color:#8A7F85;font-size:.85rem;margin-bottom:.6rem}
.pac-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:.45rem .9rem}
.pac-k{font-size:.7rem;text-transform:uppercase;letter-spacing:.04em;color:#9A8D93}
.pac-v{font-weight:600;color:#2B2226}
.pac-revisar{margin-top:.6rem;background:#FFF3EC;color:#9A3A1E;border-radius:8px;padding:.35rem .6rem;font-size:.85rem}
</style>"""

AVISO = ("🔒 <b>Datos sensibles</b> (Ley 25.326 de Protección de Datos Personales). Uso interno de Vitae, sólo datos "
         "administrativos. Cada búsqueda, ficha abierta, importación y exportación queda registrada. "
         "La IA (Luz) no tiene acceso a este módulo.")


def _fecha_ar(iso: str) -> str:
    f = fecha_de(iso) if iso else None
    return f"{f.day:02d}/{f.month:02d}/{f.year}" if f else ""


def _documento(fila: dict[str, Any]) -> str:
    if not fila.get("nro_doc"):
        return fila.get("tipo_doc") or "Sin documento"
    n = fila["nro_doc"]
    if fila.get("tipo_doc") in _NACIONALES and n.isdigit():
        n = f"{int(n):,}".replace(",", ".")
    return f"{fila.get('tipo_doc', '')} {n}".strip()


def tabla_resultados(res: pd.DataFrame, hoy: date | None = None) -> pd.DataFrame:
    hoy = hoy or date.today()
    return pd.DataFrame({
        "Apellido y nombre": res["apellido_nombre"],
        "Documento": [_documento(f) for f in res.to_dict("records")],
        "Edad": res["fecha_nac"].map(lambda f: texto_edad(f, hoy)),
        "Sexo": res["sexo"],
        "HC": res["historia_clinica"],
    }).reset_index(drop=True)


def html_ficha(p: dict[str, Any], hoy: date | None = None) -> str:
    import html as _h

    hoy = hoy or date.today()
    nac = _fecha_ar(p.get("fecha_nac", ""))
    datos = [("Documento", _documento(p)),
             ("Nacimiento", f"{nac} · {texto_edad(p.get('fecha_nac'), hoy)}" if nac else "Sin dato"),
             ("Sexo", p.get("sexo") or "Sin dato"), ("Estado civil", p.get("estado_civil") or "Sin dato"),
             ("Historia clínica", p.get("historia_clinica") or "Sin dato"), ("Origen", p.get("origen") or "Sin dato"),
             ("En Vitae desde", _fecha_ar(p.get("primera_carga", "")) or "—"),
             ("Última actualización", _fecha_ar(p.get("actualizado", "")) or "—")]
    celdas = "".join(f'<div><div class="pac-k">{_h.escape(k)}</div><div class="pac-v">{_h.escape(str(v))}</div></div>'
                     for k, v in datos)
    revisar = (f'<div class="pac-revisar">⚠️ Para revisar: {_h.escape(p["revisar"])}</div>' if p.get("revisar") else "")
    return (f'<div class="pac-ficha"><div class="pac-nombre">{_h.escape(p.get("apellido_nombre") or "Sin nombre")}</div>'
            f'<div class="pac-sub">Clave interna {_h.escape(p.get("clave", ""))}</div>'
            f'<div class="pac-grid">{celdas}</div>{revisar}</div>')


def _registrar_una_vez(almacen: Any, accion: str, detalle: str) -> None:
    import streamlit as st

    vistos = st.session_state.setdefault("pac_registrados", set())
    marca = f"{accion}|{detalle}"
    if marca not in vistos:
        vistos.add(marca)
        almacen.registrar(accion, detalle)


def _vista_buscar(st: Any, almacen: Any, df: pd.DataFrame, leer_otras: Callable[[], dict[str, pd.DataFrame]] | None,
                  hoy: date) -> None:
    q = st.text_input("Buscar paciente", key="pac_buscar", placeholder="Apellido y nombre, DNI o N° de historia clínica",
                      label_visibility="collapsed")
    if not _texto(q):
        st.caption(f"{len(df):,} personas en la base. Escribí al menos 3 letras de un nombre o 4 números de un "
                   "documento o historia clínica.".replace(",", "."))
        return
    res = buscar(df, q, limite=50)
    _registrar_una_vez(almacen, "buscar", f"{len(res)} resultados")
    if res.empty:
        st.info("No encontré a nadie con eso. Probá con menos palabras, sólo el apellido o el número de documento.")
        return
    if len(res) >= 50:
        st.caption("Muestro las primeras 50 coincidencias: sumá el nombre o el documento para afinar.")
    tabla = tabla_resultados(res, hoy)
    elegido = None
    if len(res) == 1:
        elegido = 0
    else:
        try:
            ev = st.dataframe(tabla, hide_index=True, use_container_width=True, on_select="rerun",
                              selection_mode="single-row", key="pac_resultados",
                              height=min(38 + 35 * len(tabla), 380))
            filas = list(getattr(getattr(ev, "selection", None), "rows", []) or [])
            elegido = filas[0] if filas else None
        except TypeError:  # pragma: no cover - Streamlit viejo
            st.dataframe(tabla, hide_index=True, use_container_width=True)
        if elegido is None:
            st.caption("Tocá una fila para abrir la ficha.")
            return
    p = res.iloc[elegido].to_dict()
    _registrar_una_vez(almacen, "ver_ficha", p["clave"])
    st.markdown(html_ficha(p, hoy), unsafe_allow_html=True)
    if leer_otras is None:
        return
    st.markdown("##### En Vitae")
    try:
        encontrados = menciones(p, leer_otras())
    except Exception as exc:
        st.caption(f"No pude cruzar con los otros módulos ({type(exc).__name__}).")
        return
    if not encontrados:
        st.caption("No aparece en Facturación, Quirófano, Gine Vitae, Farmacia ni en el resto de los módulos "
                   "(se busca por nombre completo y por DNI).")
        return
    for m in encontrados:
        with st.expander(f"{m['modulo']} · {m['cantidad']} registro{'s' if m['cantidad'] != 1 else ''}"):
            cols = [c for c in m["filas"].columns if not str(c).startswith("_")][:10]
            st.dataframe(m["filas"][cols], hide_index=True, use_container_width=True)
            if m["cantidad"] > len(m["filas"]):
                st.caption(f"… y {m['cantidad'] - len(m['filas'])} más en {m['modulo']}.")


def _vista_tablero(st: Any, almacen: Any, df: pd.DataFrame, hoy: date) -> None:
    total = len(df)
    mujeres = (df["sexo"] == "Femenino").mean() if total else 0
    con_fecha = (df["fecha_nac"] != "").mean() if total else 0
    revisar = int((df["revisar"] != "").sum())
    c = st.columns(4)
    c[0].metric("Personas", f"{total:,}".replace(",", "."))
    c[1].metric("Mujeres", f"{mujeres:.0%}")
    c[2].metric("Con fecha de nacimiento", f"{con_fecha:.0%}")
    c[3].metric("Para revisar", f"{revisar:,}".replace(",", "."))
    edades = tabla_edades(df, hoy)
    sin_dato = int(edades.loc["Sin dato"].sum()) if "Sin dato" in edades.index else 0
    con = edades.drop(index="Sin dato", errors="ignore")
    st.markdown("##### Edad y sexo")
    if not con.empty:
        try:
            import plotly.graph_objects as go

            fig = go.Figure()
            colores = {"Femenino": "#8C2853", "Masculino": "#4F7CAC", "Indefinido": "#B9A9B1", "Sin dato": "#D9CFD4"}
            for s in [c_ for c_ in ["Femenino", "Masculino", "Indefinido", "Sin dato"] if c_ in con.columns]:
                fig.add_bar(x=list(con.index), y=list(con[s]), name=s, marker_color=colores[s])
            fig.update_layout(barmode="stack", height=320, margin=dict(l=10, r=10, t=10, b=10),
                              legend=dict(orientation="h", y=1.1), plot_bgcolor="rgba(0,0,0,0)",
                              paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        except Exception:
            st.bar_chart(con)
    st.caption(f"Edad calculada hoy desde la fecha de nacimiento. {sin_dato:,} personas sin fecha de nacimiento."
               .replace(",", "."))
    izq, der = st.columns(2)
    with izq:
        st.markdown("##### Origen del registro")
        org = df["origen"].replace("", "Sin dato").value_counts().rename_axis("Origen").reset_index(name="Personas")
        st.dataframe(org, hide_index=True, use_container_width=True)
    with der:
        st.markdown("##### Datos completos")
        for k, v in completitud(df).items():
            st.progress(min(max(v, 0.0), 1.0), text=f"{k}: {v:.0%}")
    st.markdown("##### Investigación")
    st.caption("Excel anonimizado: sin nombre, documento ni historia clínica, con la edad en rangos y el orden "
               "mezclado. Para estudios y estadística interna.")
    if st.button("Preparar Excel anonimizado", key="pac_prep_anon", icon=":material/science:"):
        st.session_state["pac_anon"] = exportar_anonimo(df, hoy)
        almacen.registrar("exportar_anonimo", f"{total} personas")
    if st.session_state.get("pac_anon"):
        st.download_button("Descargar Excel anonimizado", st.session_state["pac_anon"],
                           file_name=f"vitae_pacientes_anonimizado_{hoy:%Y%m%d}.xlsx", key="pac_desc_anon",
                           icon=":material/download:")


def _vista_calidad(st: Any, almacen: Any, df: pd.DataFrame, hoy: date) -> None:
    motivos = motivos_revisar(df)
    dup = posibles_duplicados(df)
    c = st.columns(3)
    c[0].metric("Para revisar", f"{int((df['revisar'] != '').sum()):,}".replace(",", "."))
    c[1].metric("Posibles duplicados", f"{int(dup['grupo'].nunique()) if len(dup) else 0:,}".replace(",", "."),
                help="Mismo nombre y misma fecha de nacimiento con distinto documento o historia clínica.")
    c[2].metric("Sin fecha de nacimiento", f"{int((df['fecha_nac'] == '').sum()):,}".replace(",", "."))
    opciones = ["Posibles duplicados"] + list(motivos.index)
    elegido = st.selectbox("Qué revisar", opciones, key="pac_calidad_motivo",
                           format_func=lambda o: (f"{o} ({int(dup['grupo'].nunique()) if len(dup) else 0} grupos)"
                                                  if o == "Posibles duplicados" else f"{o} ({int(motivos[o])})"))
    if elegido == "Posibles duplicados":
        x = dup
    else:
        x = df[df["revisar"].str.contains(re.escape(elegido), na=False)]
    if x.empty:
        st.success("Nada para revisar acá 🎉")
        return
    _registrar_una_vez(almacen, "ver_calidad", f"{elegido}: {len(x)}")
    t = tabla_resultados(x.head(200), hoy)
    if elegido == "Posibles duplicados":
        t.insert(0, "Grupo", list(x["grupo"].head(200)))
        t["Clave"] = list(x["clave"].head(200))
    else:
        t["Motivo"] = list(x["revisar"].head(200))
    st.dataframe(t, hide_index=True, use_container_width=True)
    if len(x) > 200:
        st.caption(f"Muestro 200 de {len(x)}. Se corrigen en el sistema de turnos y se vuelve a importar.")
    else:
        st.caption("Se corrigen en el sistema de turnos y se vuelve a importar el Excel: Vitae se actualiza solo.")


def _vista_importar(st: Any, almacen: Any, df: pd.DataFrame, hoy: date) -> None:
    st.caption("Subí el Excel de personas del sistema de turnos (.xls, .xlsx o .csv). Se puede repetir cuando "
               "quieras: suma a los nuevos y actualiza a los que cambiaron, sin duplicar ni borrar a nadie. "
               "El archivo no se guarda: se lee y se descarta.")
    archivo = st.file_uploader("Archivo de pacientes", type=["xls", "xlsx", "csv"], key="pac_archivo",
                               label_visibility="collapsed")
    if archivo is None:
        return
    contenido = archivo.getvalue()
    hojas = []
    try:
        hojas = hojas_del_archivo(contenido, archivo.name)
    except Exception:
        pass
    hoja = st.selectbox("Hoja del archivo", hojas, key="pac_hoja") if len(hojas) > 1 else (hojas[0] if hojas else 0)
    huella = hashlib.sha1(contenido).hexdigest() + f"|{hoja}"
    if st.session_state.get("pac_imp_huella") != huella:
        with st.spinner("Leyendo y ordenando el archivo…"):
            try:
                nuevo, informe = normalizar(leer_archivo(contenido, archivo.name, hoja), hoy)
            except Exception as exc:
                st.error(f"No pude leer el archivo: {exc}")
                return
        st.session_state["pac_imp_huella"] = huella
        st.session_state["pac_imp"] = (nuevo, informe)
    nuevo, informe = st.session_state["pac_imp"]
    plan = plan_importacion(df, nuevo, hoy)
    c = st.columns(4)
    c[0].metric("Filas leídas", f"{informe['filas']:,}".replace(",", "."))
    c[1].metric("Nuevos", f"{len(plan['nuevos']):,}".replace(",", "."))
    c[2].metric("Actualizados", f"{len(plan['cambios']):,}".replace(",", "."))
    c[3].metric("Sin cambios", f"{plan['sin_cambios']:,}".replace(",", "."))
    detalles = [
        f"{informe['pacientes']:,} personas distintas en el archivo".replace(",", "."),
        f"{informe['unidos']} filas repetidas se unieron en una sola persona" if informe["unidos"] else "",
        f"{informe['para_revisar']} quedan marcadas para revisar (documento dudoso, fecha imposible…)"
        if informe["para_revisar"] else "",
        f"{informe['notas_no_importadas']} observaciones libres NO se importan (pueden tener datos clínicos)"
        if informe["notas_no_importadas"] else "",
        f"{plan['reclaves']} personas ahora tienen documento (antes se identificaban por HC)" if plan["reclaves"] else "",
    ]
    st.markdown("\n".join(f"- {d}" for d in detalles if d))
    if plan["campos"]:
        st.caption("Datos que se actualizan: " + ", ".join(f"{k.replace('_', ' ')} ({v})" for k, v in plan["campos"].items()))
    if not len(plan["nuevos"]) and not plan["cambios"]:
        st.success("La base ya está al día con este archivo.")
        return
    if st.button(f"Importar: {len(plan['nuevos'])} nuevos y {len(plan['cambios'])} actualizados", type="primary",
                 key="pac_importar", icon=":material/upload:"):
        barra = st.progress(0.0, text="Guardando…")
        try:
            almacen.guardar_plan(plan, progreso=lambda f, t: barra.progress(min(f, 1.0), text=t))
        except Exception as exc:
            st.error(f"No se pudo guardar: {exc}")
            return
        almacen.registrar("importar", f"{archivo.name}: {len(plan['nuevos'])} nuevos, {len(plan['cambios'])} actualizados")
        for k in ("pac_imp", "pac_imp_huella", "pac_archivo"):
            st.session_state.pop(k, None)
        st.session_state["pac_vista"] = "Buscar"
        st.session_state["pac_ok"] = (f"Listo ✅ {len(plan['nuevos'])} pacientes nuevos y {len(plan['cambios'])} "
                                      "actualizados.")
        st.rerun()


def render_pacientes(almacen: Any, leer_otras: Callable[[], dict[str, pd.DataFrame]] | None = None,
                     hoy: date | None = None) -> None:
    import streamlit as st

    hoy = hoy or date.today()
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="pac-aviso">{AVISO}</div>', unsafe_allow_html=True)
    if st.session_state.get("pac_ok"):
        st.success(st.session_state.pop("pac_ok"))
    try:
        df = almacen.leer()
    except Exception as exc:
        st.error(f"No pude leer la base de pacientes: {exc}")
        return
    vistas = ["Inteligencia", "Buscar", "Tablero", "Calidad de datos", "Importar"]
    if df.empty:
        st.info("La base de pacientes está vacía. Empezá importando el Excel del sistema de turnos.")
        st.session_state.setdefault("pac_vista", "Importar")
    st.session_state.setdefault("pac_vista", "Inteligencia")
    izq, der = st.columns([5, 1], vertical_alignment="center")
    with izq:
        try:
            vista = st.segmented_control("Vista", vistas, key="pac_vista", label_visibility="collapsed")
        except Exception:  # pragma: no cover
            vista = st.radio("Vista", vistas, key="pac_vista", horizontal=True, label_visibility="collapsed")
    with der:
        if st.button("Releer", key="pac_releer", icon=":material/refresh:", help="Vuelve a leer la base desde Google Sheets"):
            df = almacen.leer(refrescar=True)
    vista = vista or "Inteligencia"
    if vista == "Inteligencia":
        from vitae.modulos.pacientes_analisis import render_inteligencia

        render_inteligencia(st, almacen, df, leer_otras, hoy)
    elif vista == "Buscar":
        _vista_buscar(st, almacen, df, leer_otras, hoy)
    elif vista == "Tablero":
        _vista_tablero(st, almacen, df, hoy)
    elif vista == "Calidad de datos":
        _vista_calidad(st, almacen, df, hoy)
    else:
        _vista_importar(st, almacen, df, hoy)
