from __future__ import annotations

"""
Motor de Inteligencia Vitae.

Objetivos de diseño:
- analizar el 100% de las filas para los cálculos, no sólo ``head(1000)``;
- recuperar únicamente el detalle relevante para cada pregunta;
- conocer el mapa completo de módulos de Vitae;
- poder cruzar módulos cuando la consulta lo requiera;
- priorizar cifras calculadas por Python y evitar que el LLM invente números;
- mantener compatibilidad con las llamadas existentes de views.py:
    preguntar_ia(modulo, df, pregunta)
    preguntar_dashboard(data, pregunta)
"""

import json
import math
import re
import unicodedata
from datetime import date, datetime
from typing import Any, Iterable

import pandas as pd

try:
    import streamlit as st
except Exception:  # pragma: no cover - permite importar fuera de Streamlit
    st = None

from google import genai

try:
    from google.genai import types as genai_types
except Exception:  # pragma: no cover - SDK viejo
    genai_types = None

from config import GEMINI_API_KEY, GEMINI_MODEL
from modules import MODULES

try:
    # Se importa de forma opcional para que el asistente siga funcionando
    # aunque se ejecute aislado en tests.
    from database import add_balance_columns, get_df
except Exception:  # pragma: no cover
    add_balance_columns = None
    get_df = None


# =========================================================
# CONFIGURACIÓN
# =========================================================

_MAX_MODULE_DETAIL_ROWS = 55
_MAX_GLOBAL_DETAIL_ROWS = 90
_MAX_CATEGORICAL_VALUES = 8
_MAX_TEXT_LENGTH = 180
_MAX_HISTORY_ITEMS = 6
_MAX_ENTERPRISE_MODULES_IN_DETAIL = 12

_STOPWORDS = {
    "que", "qué", "como", "cómo", "cual", "cuál", "cuales", "cuáles",
    "para", "por", "con", "sin", "del", "las", "los", "una", "uno",
    "unos", "unas", "este", "esta", "estos", "estas", "ese", "esa",
    "hay", "tengo", "tiene", "tienen", "sobre", "entre", "desde", "hasta",
    "donde", "dónde", "cuanto", "cuánto", "cuanta", "cuánta", "total",
    "vitae", "modulo", "módulo", "datos", "informacion", "información",
}

_DATE_HINTS = (
    "fecha", "mes", "periodo", "período", "venc", "pago", "desde", "hasta",
    "created", "updated", "emision", "emisión", "factura", "turno",
)

_MONEY_HINTS = (
    "monto", "importe", "valor", "saldo", "ingreso", "egreso", "factur",
    "cobrado", "pagado", "pendiente", "deuda", "precio", "costo", "cuota",
    "honorario", "disponible", "capital", "interes", "interés", "usd", "peso",
)

_ID_HINTS = (
    "id", "dni", "cuit", "cuil", "telefono", "teléfono", "mail", "email",
    "domicilio", "direccion", "dirección", "afiliado", "nro_afiliado",
)


if not GEMINI_API_KEY:
    client = None
else:
    client = genai.Client(api_key=GEMINI_API_KEY)


# =========================================================
# UTILIDADES SEGURAS
# =========================================================

def _norm(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokens(text: str) -> list[str]:
    raw = re.findall(r"[a-zA-ZáéíóúÁÉÍÓÚñÑ0-9_./-]+", str(text))
    result: list[str] = []
    for token in raw:
        token = _norm(token).strip("_./-")
        if len(token) < 3 or token in _STOPWORDS:
            continue
        result.append(token)
    return list(dict.fromkeys(result))


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except Exception:
            return None

    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"[^0-9,.-]", "", text)
    if not text or text in {"-", ".", ","}:
        return None

    try:
        # Formato AR: 1.234.567,89
        if "," in text and "." in text and text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        elif "," in text and "." not in text:
            text = text.replace(",", ".")
        elif text.count(".") > 1:
            text = text.replace(".", "")
        number = float(text)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _round_number(value: Any) -> float | None:
    number = _safe_float(value)
    if number is None:
        return None
    return round(number, 2)


def _is_date_column(name: Any, series: pd.Series) -> bool:
    n = _norm(name)
    if any(h in n for h in _DATE_HINTS):
        return True
    return pd.api.types.is_datetime64_any_dtype(series)


def _is_id_like(name: Any) -> bool:
    n = _norm(name).replace(" ", "_")
    return any(n == hint or n.endswith(f"_{hint}") for hint in _ID_HINTS)


def _is_money_like(name: Any) -> bool:
    n = _norm(name)
    return any(h in n for h in _MONEY_HINTS)


def _clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        try:
            return pd.Timestamp(value).strftime("%Y-%m-%d")
        except Exception:
            return str(value)
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _round_number(value)
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:_MAX_TEXT_LENGTH]


def _records(df: pd.DataFrame, max_rows: int) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    show = df.head(max_rows).copy()
    result: list[dict[str, Any]] = []
    for _, row in show.iterrows():
        item: dict[str, Any] = {}
        for col in show.columns:
            value = _clean_scalar(row.get(col))
            if value not in (None, ""):
                item[str(col)] = value
        result.append(item)
    return result


def _series_to_numeric(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    return series.map(_safe_float).astype("float64")


def _date_range(series: pd.Series) -> tuple[str | None, str | None, int]:
    parsed = pd.to_datetime(series, errors="coerce", dayfirst=True)
    valid = parsed.dropna()
    if valid.empty:
        return None, None, 0
    return (
        valid.min().strftime("%Y-%m-%d"),
        valid.max().strftime("%Y-%m-%d"),
        int(valid.size),
    )


def _meaningful_categorical(series: pd.Series) -> bool:
    non_null = series.dropna()
    if non_null.empty:
        return False
    nunique = non_null.astype(str).nunique(dropna=True)
    return 1 <= nunique <= min(80, max(12, int(len(non_null) * 0.35)))


# =========================================================
# PERFIL DETERMINÍSTICO DEL 100% DE LOS DATOS
# =========================================================

def _profile_dataframe(df: pd.DataFrame) -> dict[str, Any]:
    """Calcula métricas usando TODAS las filas del DataFrame."""
    if df is None:
        df = pd.DataFrame()

    profile: dict[str, Any] = {
        "filas": int(len(df)),
        "columnas": [str(c) for c in df.columns],
        "columnas_total": int(len(df.columns)),
        "filas_duplicadas_exactas": 0,
        "fechas": {},
        "numericos": {},
        "categorias": {},
        "calidad": {},
    }

    if df.empty:
        return profile

    try:
        profile["filas_duplicadas_exactas"] = int(df.duplicated().sum())
    except Exception:
        pass

    # Calidad por columna: se informa sólo lo relevante.
    quality_rows: list[dict[str, Any]] = []
    for col in df.columns:
        s = df[col]
        nulls = int(s.isna().sum())
        blanks = 0
        if s.dtype == "object":
            try:
                blanks = int(s.fillna("").astype(str).str.strip().eq("").sum())
            except Exception:
                blanks = 0
        missing = min(len(s), nulls + blanks)
        if missing > 0:
            quality_rows.append({
                "columna": str(col),
                "faltantes": int(missing),
                "pct": round((missing / max(len(s), 1)) * 100, 1),
            })

    profile["calidad"] = {
        "columnas_con_faltantes": sorted(
            quality_rows, key=lambda x: x["pct"], reverse=True
        )[:12]
    }

    for col in df.columns:
        s = df[col]
        col_name = str(col)

        # Fechas
        if _is_date_column(col, s):
            start, end, valid = _date_range(s)
            if valid:
                profile["fechas"][col_name] = {
                    "desde": start,
                    "hasta": end,
                    "validas": valid,
                }

        # Numéricos: tipos nativos o columnas con semántica monetaria.
        numeric = None
        if pd.api.types.is_numeric_dtype(s):
            numeric = pd.to_numeric(s, errors="coerce")
        elif _is_money_like(col):
            numeric = _series_to_numeric(s)

        if numeric is not None:
            valid = numeric.dropna()
            if not valid.empty:
                # IDs numéricos no deben sumarse.
                info: dict[str, Any] = {
                    "cantidad": int(valid.size),
                    "promedio": round(float(valid.mean()), 2),
                    "min": round(float(valid.min()), 2),
                    "max": round(float(valid.max()), 2),
                }
                if not _is_id_like(col):
                    info["suma"] = round(float(valid.sum()), 2)
                profile["numericos"][col_name] = info

        # Distribuciones categóricas útiles.
        if s.dtype == "object" or pd.api.types.is_bool_dtype(s):
            try:
                if _meaningful_categorical(s):
                    clean = s.fillna("").astype(str).str.strip()
                    clean = clean[clean.ne("")]
                    counts = clean.value_counts(dropna=False).head(_MAX_CATEGORICAL_VALUES)
                    if not counts.empty:
                        profile["categorias"][col_name] = [
                            {"valor": str(idx)[:120], "cantidad": int(count)}
                            for idx, count in counts.items()
                        ]
            except Exception:
                continue

    return profile


def _module_metadata(module_name: str) -> dict[str, Any]:
    cfg = MODULES.get(module_name, {})
    if not cfg:
        # También permite que se pase el nombre físico de la tabla.
        cfg = next(
            (value for value in MODULES.values() if value.get("table") == module_name),
            {},
        )
    fields = []
    for field in cfg.get("fields", []) or []:
        if not field:
            continue
        fields.append({
            "campo": str(field[0]),
            "tipo": str(field[1]) if len(field) > 1 else "",
            "obligatorio": bool(field[2]) if len(field) > 2 else False,
        })
    return {
        "empresa": cfg.get("empresa"),
        "tipo": cfg.get("tipo"),
        "tabla": cfg.get("table"),
        "descripcion": cfg.get("descripcion"),
        "campos_configurados": fields,
    }


# =========================================================
# RECUPERACIÓN DE DETALLE RELEVANTE
# =========================================================

def _select_relevant_rows(
    df: pd.DataFrame,
    question: str,
    max_rows: int,
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=[] if df is None else df.columns)

    tokens = _tokens(question)
    work = df.copy()

    # Se evita convertir dataframes gigantes completos a una sola cadena cuando
    # no hay términos concretos en la pregunta.
    if not tokens:
        date_candidates = [
            c for c in work.columns if _is_date_column(c, work[c])
        ]
        if date_candidates:
            c = date_candidates[0]
            parsed = pd.to_datetime(work[c], errors="coerce", dayfirst=True)
            return work.assign(_sort_date=parsed).sort_values(
                "_sort_date", ascending=False, na_position="last"
            ).drop(columns=["_sort_date"]).head(max_rows)
        return work.head(max_rows)

    # Sólo se usan columnas de texto/categoría/fecha para la búsqueda lexical.
    search_cols = []
    for c in work.columns:
        s = work[c]
        if s.dtype == "object" or _is_date_column(c, s):
            search_cols.append(c)
    if not search_cols:
        return work.head(max_rows)

    scores = pd.Series(0, index=work.index, dtype="int64")
    for c in search_cols:
        try:
            text = work[c].fillna("").astype(str).map(_norm)
        except Exception:
            continue
        for token in tokens:
            scores = scores.add(text.str.contains(re.escape(token), regex=True).astype(int), fill_value=0)

    matched = scores[scores.gt(0)].sort_values(ascending=False)
    if matched.empty:
        return work.head(min(max_rows, len(work)))

    selected = work.loc[matched.index].copy()
    selected.insert(0, "_relevancia", matched.astype(int).values)
    return selected.head(max_rows)


def _question_module_score(module_name: str, df: pd.DataFrame, question: str) -> int:
    q = _norm(question)
    tokens = _tokens(question)
    meta = _module_metadata(module_name)
    haystack = " ".join([
        _norm(module_name),
        _norm(meta.get("empresa")),
        _norm(meta.get("tipo")),
        _norm(meta.get("descripcion")),
        " ".join(_norm(c) for c in df.columns),
    ])
    score = 0
    if _norm(module_name) and _norm(module_name) in q:
        score += 20
    for token in tokens:
        if token in haystack:
            score += 3
    return score


# =========================================================
# CARGA DEL MAPA EMPRESARIAL COMPLETO
# =========================================================

def _load_enterprise_data_uncached() -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    if get_df is None:
        return result

    table_cache: dict[str, pd.DataFrame] = {}
    for module_name, cfg in MODULES.items():
        table = str(cfg.get("table", "")).strip()
        if not table:
            continue
        if table not in table_cache:
            try:
                frame = get_df(table)
                if frame is None:
                    frame = pd.DataFrame()
                if callable(add_balance_columns):
                    try:
                        frame = add_balance_columns(frame)
                    except Exception:
                        pass
                table_cache[table] = frame.copy()
            except Exception:
                table_cache[table] = pd.DataFrame()
        result[module_name] = table_cache[table].copy()
    return result


if st is not None:
    _load_enterprise_data = st.cache_data(ttl=120, show_spinner=False)(
        _load_enterprise_data_uncached
    )
else:
    _load_enterprise_data = _load_enterprise_data_uncached


def _merge_data_sources(
    supplied: dict[str, Any] | None,
    include_database: bool = True,
) -> dict[str, pd.DataFrame]:
    merged: dict[str, pd.DataFrame] = {}

    if include_database:
        try:
            merged.update(_load_enterprise_data())
        except Exception:
            pass

    for name, value in (supplied or {}).items():
        if isinstance(value, pd.DataFrame):
            merged[str(name)] = value.copy()
        elif isinstance(value, pd.Series):
            merged[str(name)] = value.to_frame().copy()
        elif isinstance(value, dict):
            # El Dashboard ya entrega algunos paquetes compactos; se convierten
            # en un DataFrame de una fila para conservar compatibilidad.
            try:
                merged[str(name)] = pd.DataFrame([value])
            except Exception:
                continue
        elif isinstance(value, (list, tuple)):
            try:
                merged[str(name)] = pd.DataFrame(value)
            except Exception:
                continue

    return merged


# =========================================================
# CONTEXTO EMPRESARIAL
# =========================================================

def _build_module_packet(
    module_name: str,
    df: pd.DataFrame,
    question: str,
    detail_rows: int = _MAX_MODULE_DETAIL_ROWS,
) -> dict[str, Any]:
    relevant = _select_relevant_rows(df, question, detail_rows)
    return {
        "modulo": module_name,
        "definicion": _module_metadata(module_name),
        "perfil_total": _profile_dataframe(df),
        "detalle_relevante": _records(relevant, detail_rows),
        "filas_detalle_enviadas": int(min(len(relevant), detail_rows)),
    }


def _build_enterprise_packet(
    data: dict[str, pd.DataFrame],
    question: str,
    current_module: str | None = None,
) -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    scored: list[tuple[int, str, pd.DataFrame]] = []
    total_rows = 0
    with_data = 0

    for module_name, df in data.items():
        if not isinstance(df, pd.DataFrame):
            continue
        total_rows += int(len(df))
        if not df.empty:
            with_data += 1
        profile = _profile_dataframe(df)
        profiles.append({
            "modulo": module_name,
            "definicion": _module_metadata(module_name),
            "perfil_total": profile,
        })
        score = _question_module_score(module_name, df, question)
        if current_module and _norm(module_name) == _norm(current_module):
            score += 50
        scored.append((score, module_name, df))

    # Si no hay coincidencias semánticas, se priorizan módulos con datos.
    scored.sort(key=lambda item: (item[0], len(item[2])), reverse=True)
    selected = [item for item in scored if item[0] > 0][: _MAX_ENTERPRISE_MODULES_IN_DETAIL]
    if not selected:
        selected = [item for item in scored if not item[2].empty][:6]

    details: list[dict[str, Any]] = []
    per_module = max(8, _MAX_GLOBAL_DETAIL_ROWS // max(len(selected), 1))
    for _, module_name, df in selected:
        relevant = _select_relevant_rows(df, question, per_module)
        details.append({
            "modulo": module_name,
            "filas": _records(relevant, per_module),
        })

    return {
        "cobertura": {
            "modulos_conocidos": int(len(data)),
            "modulos_con_datos": int(with_data),
            "filas_totales_analizadas_por_python": int(total_rows),
            "nota": (
                "Los perfiles y totales se calcularon sobre todas las filas. "
                "El detalle enviado al modelo fue recuperado según la pregunta."
            ),
        },
        "mapa_modulos": profiles,
        "detalle_relevante_multimodulo": details,
    }


# =========================================================
# MEMORIA DE CONVERSACIÓN CORTA
# =========================================================

def _history_key(scope: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", _norm(scope))[:80]
    return f"vitae_ai_history_{safe}"


def _get_history(scope: str) -> list[dict[str, str]]:
    if st is None:
        return []
    try:
        history = st.session_state.get(_history_key(scope), [])
        return list(history)[-_MAX_HISTORY_ITEMS:]
    except Exception:
        return []


def _append_history(scope: str, question: str, answer: str) -> None:
    if st is None:
        return
    try:
        key = _history_key(scope)
        history = list(st.session_state.get(key, []))
        history.append({
            "pregunta": str(question)[:500],
            "respuesta": str(answer)[:1800],
        })
        st.session_state[key] = history[-_MAX_HISTORY_ITEMS:]
    except Exception:
        pass


# =========================================================
# LLAMADA A GEMINI
# =========================================================

def _generate(prompt: str) -> str:
    if client is None:
        raise RuntimeError(
            "No está configurada GEMINI_API_KEY. Configurala en variables de entorno o st.secrets."
        )

    kwargs: dict[str, Any] = {
        "model": GEMINI_MODEL,
        "contents": prompt,
    }

    if genai_types is not None:
        try:
            kwargs["config"] = genai_types.GenerateContentConfig(
                temperature=0.15,
                max_output_tokens=3200,
            )
        except Exception:
            pass

    response = client.models.generate_content(**kwargs)
    text = getattr(response, "text", None)
    if not text or not str(text).strip():
        raise RuntimeError("Gemini devolvió una respuesta vacía.")
    return str(text).strip().replace("```markdown", "").replace("```md", "").replace("```", "").strip()


def _json_context(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":"))


_SYSTEM_RULES = """
Sos VITAE INTELLIGENCE, el cerebro analítico interno del sistema de gestión Vitae.
Tu trabajo es responder como un analista senior operativo, financiero y directivo que conoce el mapa completo de la empresa.

REGLAS DE EXACTITUD OBLIGATORIAS:
1. Usá exclusivamente la información incluida en CONTEXTO VITAE.
2. Los campos dentro de `perfil_total` fueron calculados por Python sobre el 100% de las filas y son la fuente autoritativa para sumas, conteos, promedios, mínimos y máximos.
3. `detalle_relevante` es sólo una muestra recuperada para explicar o identificar registros; nunca lo uses para reemplazar un total de `perfil_total`.
4. No inventes cifras, causas, estados, personas, fechas, relaciones ni conclusiones.
5. No confundas facturado, cobrado, ingreso, saldo disponible, pendiente de cobro, deuda a pagar, egreso ni resultado.
6. No sumes columnas distintas sólo porque ambas contienen importes. Primero respetá su significado.
7. Si el dato puede tener más de una interpretación, explicá la ambigüedad y no afirmes una cifra dudosa.
8. Si faltan datos, decilo expresamente y señalá qué campo o módulo falta.
9. No interpretes ausencia de meses futuros como caída de actividad.
10. Cuando una pregunta pide un registro puntual, apoyate en el detalle recuperado y mencioná el módulo de origen.
11. Cuando la pregunta cruza áreas, relacioná módulos y señalá de dónde sale cada conclusión.
12. Separá HECHOS de RECOMENDACIONES. Una recomendación nunca debe presentarse como un hecho observado.
13. Si detectás una inconsistencia de calidad (faltantes, duplicados, fechas inválidas o columnas contradictorias), informala.
14. Nunca digas que analizaste una fila que no aparece en el contexto; sí podés afirmar que los perfiles agregados cubren todas las filas cuando `cobertura` así lo indica.
15. Respondé en español argentino, claro, profesional y directo.
16. Para importes, usá formato argentino cuando sea posible: $ 1.250.000,00. Para USD, indicá USD explícitamente.
17. No uses lenguaje publicitario ni relleno.

MODO DE RESPUESTA:
- Empezá por la respuesta concreta.
- Después explicá los datos que la respaldan.
- Si corresponde, agregá alertas, inconsistencias y acciones recomendadas.
- Para preguntas simples, sé breve. Para preguntas ejecutivas o diagnósticos, profundizá.
"""


# =========================================================
# API PÚBLICA - COMPATIBLE CON views.py
# =========================================================

def preguntar_ia(modulo: str, df: pd.DataFrame, pregunta: str) -> str:
    question = str(pregunta or "").strip()
    if not question:
        return "Escribí una pregunta para poder analizar la información."

    local_df = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame(df)

    # El módulo visible tiene prioridad, pero el motor incorpora el mapa global
    # para poder responder cruces y detectar relaciones con otras áreas.
    enterprise = _merge_data_sources(None, include_database=True)
    if modulo:
        enterprise[str(modulo)] = local_df.copy()

    local_packet = _build_module_packet(str(modulo or "Módulo"), local_df, question)
    enterprise_packet = _build_enterprise_packet(
        enterprise,
        question,
        current_module=str(modulo or "Módulo"),
    ) if enterprise else None

    context = {
        "alcance_consulta": "módulo + conocimiento empresarial",
        "modulo_visible": local_packet,
        "empresa": enterprise_packet,
        "historial_conversacion": _get_history(f"modulo::{modulo}"),
    }

    prompt = f"""
{_SYSTEM_RULES}

CONTEXTO VITAE:
{_json_context(context)}

PREGUNTA DEL USUARIO:
{question}

Respondé la pregunta usando primero el módulo visible. Si la pregunta requiere relacionar información, utilizá el contexto empresarial. Indicá con claridad cuando una conclusión proviene de otro módulo.
"""

    answer = _generate(prompt)
    _append_history(f"modulo::{modulo}", question, answer)
    return answer


def preguntar_dashboard(data: dict, pregunta: str) -> str:
    question = str(pregunta or "").strip()
    if not question:
        return "Escribí una pregunta ejecutiva para poder analizar el Dashboard."

    # Une el paquete exacto que ya construyó el Dashboard con el conocimiento
    # de todos los módulos disponibles en Google Sheets/database.py.
    merged = _merge_data_sources(data or {}, include_database=True)
    enterprise_packet = _build_enterprise_packet(merged, question)

    # Conserva explícitamente el contexto compacto suministrado por views.py,
    # porque contiene métricas ejecutivas que ya fueron calculadas localmente.
    supplied_packet: dict[str, Any] = {}
    for name, value in (data or {}).items():
        if isinstance(value, pd.DataFrame):
            supplied_packet[str(name)] = {
                "perfil_total": _profile_dataframe(value),
                "filas": _records(value, min(len(value), 100)),
            }
        elif isinstance(value, (dict, list, tuple)):
            supplied_packet[str(name)] = value

    context = {
        "alcance_consulta": "dirección general + todos los módulos",
        "contexto_ejecutivo_calculado_por_dashboard": supplied_packet,
        "inteligencia_empresarial": enterprise_packet,
        "historial_conversacion": _get_history("dashboard_global"),
    }

    prompt = f"""
{_SYSTEM_RULES}

Además actuás como Director de Inteligencia Empresarial de Vitae. Tenés que conectar finanzas, cobranzas, obligaciones, actividad, tareas, vencimientos, contratos, operaciones clínicas y cualquier otro módulo disponible, sin mezclar conceptos contables ni duplicar operaciones.

CONTEXTO VITAE:
{_json_context(context)}

PREGUNTA EJECUTIVA:
{question}

Si existe una respuesta exacta ya calculada en `contexto_ejecutivo_calculado_por_dashboard`, priorizala. Para ampliar el diagnóstico, usá `inteligencia_empresarial`. Cuando cites una cifra relevante, dejá claro el concepto y el módulo o fuente lógica que la respalda.
"""

    answer = _generate(prompt)
    _append_history("dashboard_global", question, answer)
    return answer


# =========================================================
# COPILOTO VITAE - API PÚBLICA
# Compatible con render_vitae_copilot() de views.py
# =========================================================

_COPILOT_HISTORY_KEY = "vitae_copiloto_historial"
_COPILOT_MAX_HISTORY = 8


def obtener_historial_copiloto() -> list[dict[str, str]]:
    """Devuelve el historial visible del Copiloto Vitae."""
    if st is None:
        return []
    try:
        history = st.session_state.get(_COPILOT_HISTORY_KEY, [])
        if not isinstance(history, list):
            return []
        return list(history)[-_COPILOT_MAX_HISTORY:]
    except Exception:
        return []


def limpiar_historial_copiloto() -> None:
    """Reinicia la conversación del Copiloto sin afectar el resto de la app."""
    if st is None:
        return
    try:
        st.session_state[_COPILOT_HISTORY_KEY] = []
        st.session_state.pop("vitae_copilot_last_answer", None)
        st.session_state.pop("vitae_copilot_last_question", None)
    except Exception:
        pass


def _guardar_historial_copiloto(pregunta: str, respuesta: str) -> None:
    if st is None:
        return
    try:
        history = list(st.session_state.get(_COPILOT_HISTORY_KEY, []))
        history.append({
            "pregunta": str(pregunta or "")[:800],
            "respuesta": str(respuesta or "")[:5000],
        })
        st.session_state[_COPILOT_HISTORY_KEY] = history[-_COPILOT_MAX_HISTORY:]
    except Exception:
        pass


def preguntar_copiloto(
    pregunta: str,
    modulo_actual: str | None = None,
    ruta_actual: str | None = None,
) -> str:
    """
    Copiloto persistente de toda la plataforma.

    - conoce todos los módulos configurados en MODULES;
    - lee los datos disponibles a través de database.get_df();
    - prioriza el módulo/pantalla actual;
    - puede cruzar información de distintas áreas;
    - usa perfiles calculados por Python sobre el 100% de las filas;
    - conserva historial corto entre pantallas.
    """
    question = str(pregunta or "").strip()
    if not question:
        return "Escribí una pregunta para poder analizar Vitae."

    actual = str(modulo_actual or ruta_actual or "Inicio").strip() or "Inicio"

    enterprise = _merge_data_sources(None, include_database=True)

    if not enterprise:
        raise RuntimeError(
            "No pude leer los módulos de Vitae desde database.py / Google Sheets. "
            "Revisá la conexión y la función get_df()."
        )

    enterprise_packet = _build_enterprise_packet(
        enterprise,
        question,
        current_module=actual,
    )

    # Si encontramos el módulo actual exactamente o por nombre físico,
    # agregamos un paquete local más profundo para que el Copiloto entienda
    # mejor la pantalla en la que está parado el usuario.
    current_packet = None
    for module_name, frame in enterprise.items():
        same_name = _norm(module_name) == _norm(actual)
        meta = _module_metadata(module_name)
        same_table = _norm(meta.get("tabla")) == _norm(actual)
        if same_name or same_table:
            current_packet = _build_module_packet(
                module_name,
                frame,
                question,
                detail_rows=_MAX_MODULE_DETAIL_ROWS,
            )
            break

    context = {
        "contexto_actual": {
            "modulo": actual,
            "ruta": str(ruta_actual or actual),
        },
        "pantalla_actual": current_packet,
        "empresa_completa": enterprise_packet,
        "historial_copiloto": obtener_historial_copiloto(),
    }

    prompt = f"""
{_SYSTEM_RULES}

Además sos el COPILOTO VITAE: acompañás al usuario mientras recorre toda la plataforma.
Tenés conocimiento transversal de los módulos disponibles y debés responder según
el contexto de la pantalla actual, pero podés cruzar otras áreas cuando la pregunta
lo requiera.

REGLAS ESPECÍFICAS DEL COPILOTO:
1. Si preguntan "qué se debe", "qué hay que pagar", "deudas", "pendientes" o similares,
   buscá y separá claramente obligaciones, deudas, pagos pendientes, vencimientos,
   préstamos, impuestos y cualquier otro concepto equivalente disponible.
2. No mezcles deuda con facturación ni pendiente de cobro.
3. Si existen varias empresas/unidades, separá los resultados por módulo o empresa.
4. Si una cifra no puede calcularse con certeza desde el contexto, no la inventes.
5. Si la pregunta es sobre la pantalla actual, priorizá `pantalla_actual`.
6. Si la consulta es global, usá `empresa_completa`.
7. Mencioná de qué módulo sale cada cifra importante.
8. Contestá de forma ejecutiva y útil: primero la respuesta concreta, luego el detalle.
9. No digas que "no conocés la empresa" si el contexto contiene módulos disponibles.
10. Si detectás datos contradictorios o faltantes, advertílo claramente.

CONTEXTO VITAE:
{_json_context(context)}

PREGUNTA:
{question}
"""

    answer = _generate(prompt)
    _guardar_historial_copiloto(question, answer)
    return answer

