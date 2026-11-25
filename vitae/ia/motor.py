"""Motor de IA de Vitae con herramientas de consulta.

En lugar de mandarle a Gemini una muestra de filas, le damos herramientas para
que consulte por su cuenta TODOS los registros de todos los módulos:

    ver_modulos        qué módulos hay, columnas, cantidad de filas y fechas
    ver_columna        valores posibles de una columna (médicos, obras sociales…)
    consultar          filtrar + agrupar + contar/sumar/promediar (por mes, etc.)
    listar_registros   ver filas concretas
    buscar_en_todo     buscar un texto (paciente, proveedor…) en todos los módulos
    conocimiento_empresa  lo que la dirección cargó sobre la empresa

Los números los calcula Python sobre el 100% de las filas; Gemini decide qué
consultar e interpreta. Si algo falla, el Copiloto vuelve al motor anterior.
"""
import functools
import json
import re
import unicodedata
from typing import Any, Callable

import pandas as pd

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None

from vitae.core.tablas import MODULES

try:
    from vitae.core.database import get_df
except Exception:  # pragma: no cover
    get_df = None

try:
    from vitae.core.helpers import first_available_date_col, leer_fechas_ar, money, nombre_mes_es
except Exception:  # pragma: no cover
    first_available_date_col = leer_fechas_ar = money = nombre_mes_es = None

TABLA_CONOCIMIENTO = "ia_conocimiento"
# Pestañas que la IA NUNCA lee (datos de acceso de usuarios).
_TABLAS_PROHIBIDAS = {"usuarios_vitae"}
# Pestañas adicionales útiles fuera de MODULES.
_TABLAS_EXTRA = {"Farmacia · movimientos": "farmacia_movimientos", "Gine Vitae · formularios web": "gine_vitae_formularios"}
_MODULO_DE_EXTRA = {"Farmacia · movimientos": "Farmacia", "Gine Vitae · formularios web": "Gine Vitae"}
_MAX_FILAS_RESPUESTA = 200
_MAX_TEXTO_CELDA = 160

TEMAS_SUGERIDOS = [
    ("Empresa", "Quiénes somos: VMR (Vitae Medicina Reproductiva) y VM (Vitae Medical), dirección, sedes, horarios."),
    ("Equipo", "Médicos, anestesistas, administrativos: nombre, función, especialidad, a quién se le paga honorarios y cómo."),
    ("Servicios y prácticas", "Prácticas que se hacen (FIV, ICSI, TEC, CRIO…), qué incluye cada una y valores de referencia."),
    ("Obras sociales y convenios", "Con qué obras sociales se trabaja, particularidades, plazos de pago habituales."),
    ("Circuito de facturación", "Cómo se factura, quién lo hace, plazos, cómo se registra el cobro."),
    ("Proveedores y gastos fijos", "Proveedores principales, alquileres, servicios, vencimientos habituales."),
    ("Bancos y cajas", "Qué cuenta se usa para qué, quién maneja cada caja."),
    ("Reglas internas", "Políticas, prioridades de la dirección, objetivos del año."),
]


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _norm(valor: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or "").strip().lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", texto).strip("_")


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _celda(valor: Any) -> Any:
    try:
        if pd.isna(valor):
            return ""
    except Exception:
        pass
    if isinstance(valor, float):
        return round(valor, 2)
    texto = str(valor)
    return texto if len(texto) <= _MAX_TEXTO_CELDA else texto[: _MAX_TEXTO_CELDA - 1] + "…"


def _numeros(serie: pd.Series) -> pd.Series:
    if money is None:
        return pd.to_numeric(serie, errors="coerce").fillna(0.0)
    return serie.map(money).astype(float)


def _fechas(serie: pd.Series) -> pd.Series:
    if leer_fechas_ar is None:
        return pd.to_datetime(serie, errors="coerce", dayfirst=True)
    return leer_fechas_ar(serie)


class _Datos:
    """Carga perezosa de todos los módulos (una vez por pregunta)."""

    def __init__(self, extra: dict[str, pd.DataFrame] | None = None, permitidos: set[str] | None = None):
        self._cache: dict[str, pd.DataFrame] = {}
        self._extra = extra or {}
        self._permitidos = permitidos
        self.consultas: list[str] = []

    def modulos(self) -> dict[str, str]:
        nombres = {n: c.get("table", "") for n, c in MODULES.items()
                   if c.get("table") and c.get("table") not in _TABLAS_PROHIBIDAS and c.get("table") != "convenios"}
        nombres.update(_TABLAS_EXTRA)
        if self._permitidos is not None:
            # Sólo lo que el usuario puede ver en la app (las pestañas extra
            # siguen el permiso de su módulo).
            nombres = {n: t for n, t in nombres.items()
                       if (_MODULO_DE_EXTRA.get(n) or n) in self._permitidos}
        for n in self._extra:
            nombres.setdefault(n, "")
        return nombres

    def resolver(self, modulo: str) -> str | None:
        objetivo = _norm(modulo)
        nombres = self.modulos()
        for n, tabla in nombres.items():
            if objetivo in (_norm(n), _norm(tabla)):
                return n
        candidatos = [n for n in nombres if objetivo and (objetivo in _norm(n) or _norm(n) in objetivo)]
        return candidatos[0] if len(candidatos) == 1 else None

    def df(self, modulo: str) -> pd.DataFrame:
        nombre = self.resolver(modulo)
        if nombre is None:
            raise ValueError(f"No existe el módulo «{modulo}». Usá ver_modulos para ver los nombres.")
        if nombre not in self._cache:
            if nombre in self._extra:
                datos = self._extra[nombre]
            else:
                tabla = self.modulos()[nombre]
                datos = get_df(tabla) if get_df is not None else pd.DataFrame()
            datos = datos.copy() if isinstance(datos, pd.DataFrame) else pd.DataFrame()
            datos.columns = [str(c) for c in datos.columns]
            self._cache[nombre] = datos
        return self._cache[nombre]

    def nombre(self, modulo: str) -> str:
        return self.resolver(modulo) or modulo


def _columna(df: pd.DataFrame, nombre: str) -> str:
    objetivo = _norm(nombre)
    for c in df.columns:
        if _norm(c) == objetivo:
            return c
    parecidas = [c for c in df.columns if objetivo and (objetivo in _norm(c) or _norm(c) in objetivo)]
    if len(parecidas) == 1:
        return parecidas[0]
    raise ValueError(f"La columna «{nombre}» no existe. Columnas: {', '.join(map(str, df.columns))}")


def _columna_fecha(df: pd.DataFrame, modulo: str) -> str | None:
    if first_available_date_col is not None:
        col = first_available_date_col(df, modulo)
        if col:
            return col
    for c in df.columns:
        if any(p in _norm(c) for p in ("fecha", "mes", "periodo", "vencimiento")):
            return c
    return None


def _aplicar_filtros(df: pd.DataFrame, filtros: str) -> pd.DataFrame:
    """filtros: JSON con una lista de {"columna","op","valor"}."""
    if not str(filtros or "").strip():
        return df
    try:
        lista = json.loads(filtros)
    except Exception as exc:
        raise ValueError('Los filtros tienen que ser JSON: [{"columna":"estado","op":"=","valor":"Pagado"}]') from exc
    if isinstance(lista, dict):
        lista = [lista]
    out = df
    for f in lista:
        col = _columna(out, f.get("columna", ""))
        op = str(f.get("op", "=")).strip().lower()
        valor = f.get("valor", "")
        texto = out[col].astype(str).str.strip()
        if op in ("=", "==", "igual"):
            mask = texto.str.lower() == str(valor).strip().lower()
        elif op in ("!=", "distinto"):
            mask = texto.str.lower() != str(valor).strip().lower()
        elif op in ("contiene", "~"):
            mask = texto.map(_norm).str.contains(re.escape(_norm(valor)), na=False)
        elif op in ("no contiene", "!~"):
            mask = ~texto.map(_norm).str.contains(re.escape(_norm(valor)), na=False)
        elif op in ("en", "in"):
            opciones = {str(v).strip().lower() for v in (valor if isinstance(valor, list) else str(valor).split(","))}
            mask = texto.str.lower().isin(opciones)
        elif op in ("vacio", "vacío"):
            mask = texto.isin(["", "nan", "None"])
        elif op in ("no vacio", "no vacío"):
            mask = ~texto.isin(["", "nan", "None"])
        elif op in (">", "<", ">=", "<="):
            numeros = _numeros(out[col])
            limite = float(money(valor) if money else valor)
            mask = {">": numeros > limite, "<": numeros < limite, ">=": numeros >= limite, "<=": numeros <= limite}[op]
        else:
            raise ValueError(f"Operador «{op}» no reconocido.")
        out = out[mask]
    return out


def _filtrar_meses(df: pd.DataFrame, modulo: str, desde: str, hasta: str) -> tuple[pd.DataFrame, str | None]:
    col = _columna_fecha(df, modulo)
    if not (desde or hasta):
        return df, col
    if col is None:
        raise ValueError("Este módulo no tiene una columna de fecha para filtrar por mes.")
    periodos = _fechas(df[col]).dt.to_period("M")
    mask = periodos.notna()
    if desde:
        mask &= periodos >= pd.Period(desde, freq="M")
    if hasta:
        mask &= periodos <= pd.Period(hasta, freq="M")
    return df[mask], col


# ---------------------------------------------------------------------------
# Herramientas (Gemini las llama solas)
# ---------------------------------------------------------------------------
def crear_herramientas(datos: _Datos) -> list[Callable[..., str]]:
    def registrar(texto: str) -> None:
        datos.consultas.append(texto)

    def ver_modulos() -> str:
        """Lista los módulos de Vitae con su empresa, descripción, cantidad de registros, columnas y rango de fechas.
        Usala primero si no sabés en qué módulo está un dato."""
        registrar("ver_modulos")
        salida = []
        for nombre in datos.modulos():
            try:
                df = datos.df(nombre)
            except Exception:
                continue
            col_fecha = _columna_fecha(df, nombre)
            rango = ""
            if col_fecha and not df.empty:
                f = _fechas(df[col_fecha]).dropna()
                if not f.empty:
                    rango = f"{f.min():%d/%m/%Y} a {f.max():%d/%m/%Y}"
            cfg = MODULES.get(nombre, {})
            salida.append({
                "modulo": nombre, "empresa": cfg.get("empresa", ""), "descripcion": " ".join(x for x in (cfg.get("descripcion", ""), cfg.get("notas_ia", "")) if x),
                "registros": int(len(df)), "columnas": list(df.columns)[:40],
                "columna_fecha": col_fecha, "rango_fechas": rango,
            })
        return _json(salida)

    def ver_columna(modulo: str, columna: str, limite: int = 40) -> str:
        """Muestra los valores distintos de una columna con cuántas veces aparece cada uno (o estadísticas si es un
        importe). Sirve para saber cómo están escritos los médicos, obras sociales, estados, proveedores, etc."""
        registrar(f"ver_columna({datos.nombre(modulo)}, {columna})")
        df = datos.df(modulo)
        col = _columna(df, columna)
        texto = df[col].astype(str).str.strip()
        texto = texto[~texto.isin(["", "nan", "None"])]
        conteo = texto.value_counts().head(max(1, min(int(limite), 200)))
        numeros = _numeros(df[col])
        es_importe = numeros.ne(0).mean() > 0.6 if len(df) else False
        res: dict[str, Any] = {"modulo": datos.nombre(modulo), "columna": col, "valores_distintos": int(texto.nunique()),
                               "valores": [{"valor": k, "cantidad": int(v)} for k, v in conteo.items()]}
        if es_importe:
            res["como_importe"] = {"suma": round(float(numeros.sum()), 2), "promedio": round(float(numeros.mean()), 2),
                                   "minimo": round(float(numeros.min()), 2), "maximo": round(float(numeros.max()), 2)}
        return _json(res)

    def consultar(modulo: str, filtros: str = "", mes_desde: str = "", mes_hasta: str = "", agrupar_por: str = "",
                  operacion: str = "contar", columna: str = "", limite: int = 50) -> str:
        """Calcula sobre TODOS los registros de un módulo.
        - filtros: JSON, lista de {"columna","op","valor"}. op: "=", "!=", "contiene", "no contiene", "en", ">", "<",
          ">=", "<=", "vacio", "no vacio". Ej: [{"columna":"obra_social","op":"contiene","valor":"osde"}]
        - mes_desde / mes_hasta: "AAAA-MM" (filtra por la columna de fecha del módulo).
        - agrupar_por: columnas separadas por coma; usá "mes" para agrupar por mes.
        - operacion: "contar", "suma", "promedio", "minimo", "maximo" o "contar_distintos".
        - columna: columna del importe o dato a calcular (obligatoria salvo para "contar").
        Devuelve el resultado, el total general y cuántas filas se consideraron."""
        nombre = datos.nombre(modulo)
        registrar(f"consultar({nombre}; filtros={filtros or '-'}; meses={mes_desde or '…'}→{mes_hasta or '…'}; "
                  f"agrupar={agrupar_por or '-'}; {operacion}{'(' + columna + ')' if columna else ''})")
        df = _aplicar_filtros(datos.df(modulo), filtros)
        df, col_fecha = _filtrar_meses(df, nombre, mes_desde, mes_hasta)
        operacion = _norm(operacion) or "contar"
        trabajo = pd.DataFrame(index=df.index)
        if operacion != "contar":
            if not columna:
                raise ValueError("Indicá la columna a calcular.")
            col_val = _columna(df, columna)
            trabajo["_valor"] = _numeros(df[col_val]) if operacion != "contar_distintos" else df[col_val].astype(str)
        claves = []
        for g in [x.strip() for x in str(agrupar_por or "").split(",") if x.strip()]:
            if _norm(g) == "mes":
                if col_fecha is None:
                    col_fecha = _columna_fecha(df, nombre)
                if col_fecha is None:
                    raise ValueError("Este módulo no tiene columna de fecha para agrupar por mes.")
                trabajo["mes"] = _fechas(df[col_fecha]).dt.to_period("M").astype(str).replace("NaT", "Sin fecha")
                claves.append("mes")
            else:
                c = _columna(df, g)
                trabajo[c] = df[c].astype(str).str.strip().replace({"": "(vacío)", "nan": "(vacío)"})
                claves.append(c)

        def calcular(serie_valor: pd.Series | None, n: int) -> float:
            if operacion == "contar":
                return n
            if operacion == "suma":
                return float(serie_valor.sum())
            if operacion == "promedio":
                return float(serie_valor.mean()) if n else 0.0
            if operacion == "minimo":
                return float(serie_valor.min()) if n else 0.0
            if operacion == "maximo":
                return float(serie_valor.max()) if n else 0.0
            if operacion == "contar_distintos":
                return int(serie_valor.nunique())
            raise ValueError(f"Operación «{operacion}» no reconocida.")

        total = calcular(trabajo.get("_valor"), len(trabajo))
        res: dict[str, Any] = {"modulo": nombre, "filas_consideradas": int(len(df)), "operacion": operacion,
                               "columna": columna, "total": round(total, 2) if isinstance(total, float) else total}
        if claves and len(trabajo):
            filas = []
            for clave, grupo in trabajo.groupby(claves, dropna=False):
                clave = clave if isinstance(clave, tuple) else (clave,)
                fila = dict(zip(claves, clave))
                fila["resultado"] = calcular(grupo.get("_valor"), len(grupo))
                fila["filas"] = int(len(grupo))
                filas.append(fila)
            orden = "mes" if claves == ["mes"] else "resultado"
            filas.sort(key=lambda f: f[orden], reverse=orden != "mes")
            res["grupos_totales"] = len(filas)
            res["grupos"] = [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in f.items()}
                             for f in filas[: max(1, min(int(limite), _MAX_FILAS_RESPUESTA))]]
        return _json(res)

    def listar_registros(modulo: str, filtros: str = "", mes_desde: str = "", mes_hasta: str = "",
                         columnas: str = "", limite: int = 30) -> str:
        """Devuelve registros concretos de un módulo (los más recientes primero), con los mismos filtros que
        consultar. columnas: lista separada por comas para mostrar solo algunas."""
        nombre = datos.nombre(modulo)
        registrar(f"listar_registros({nombre}; filtros={filtros or '-'}; meses={mes_desde or '…'}→{mes_hasta or '…'})")
        df = _aplicar_filtros(datos.df(modulo), filtros)
        df, col_fecha = _filtrar_meses(df, nombre, mes_desde, mes_hasta)
        if col_fecha and col_fecha in df.columns:
            df = df.assign(_o=_fechas(df[col_fecha])).sort_values("_o", ascending=False, na_position="last").drop(columns="_o")
        if columnas.strip():
            df = df[[_columna(df, c.strip()) for c in columnas.split(",") if c.strip()]]
        limite = max(1, min(int(limite), _MAX_FILAS_RESPUESTA))
        filas = [{k: _celda(v) for k, v in fila.items()} for fila in df.head(limite).to_dict("records")]
        return _json({"modulo": nombre, "coinciden": int(len(df)), "mostrados": len(filas), "registros": filas})

    def buscar_en_todo(texto: str, limite: int = 40) -> str:
        """Busca un texto (paciente, médico, proveedor, N° de factura, concepto…) en TODOS los módulos a la vez y
        devuelve dónde aparece y las filas encontradas."""
        registrar(f"buscar_en_todo({texto})")
        aguja = _norm(texto)
        if not aguja:
            return _json({"error": "Texto vacío."})
        encontrados, total = [], 0
        for nombre in datos.modulos():
            try:
                df = datos.df(nombre)
            except Exception:
                continue
            if df.empty:
                continue
            pajar = df.astype(str).apply(lambda c: c.map(_norm)).agg(" ".join, axis=1)
            hits = df[pajar.str.contains(re.escape(aguja), na=False)]
            if hits.empty:
                continue
            total += len(hits)
            encontrados.append({"modulo": nombre, "coincidencias": int(len(hits)),
                                "registros": [{k: _celda(v) for k, v in r.items()}
                                              for r in hits.head(max(1, int(limite) // 4)).to_dict("records")]})
        return _json({"texto": texto, "total_coincidencias": total, "por_modulo": encontrados})

    def conocimiento_empresa() -> str:
        """Información sobre la empresa cargada por la dirección (equipo, servicios, convenios, circuitos, reglas)."""
        registrar("conocimiento_empresa")
        return leer_conocimiento() or "Todavía no se cargó información de la empresa."

    def seguro(funcion: Callable[..., str]) -> Callable[..., str]:
        """Si una consulta falla, la IA recibe el error y puede corregirse (no se corta la respuesta)."""
        @functools.wraps(funcion)
        def envoltura(*args, **kwargs):
            try:
                return funcion(*args, **kwargs)
            except Exception as exc:
                return _json({"error": str(exc)})
        return envoltura

    return [seguro(f) for f in (ver_modulos, ver_columna, consultar, listar_registros, buscar_en_todo,
                                conocimiento_empresa)]


# ---------------------------------------------------------------------------
# Base de conocimiento
# ---------------------------------------------------------------------------
def leer_conocimiento() -> str:
    if get_df is None:
        return ""
    try:
        df = get_df(TABLA_CONOCIMIENTO)
    except Exception:
        return ""
    if df is None or df.empty:
        return ""
    cols = {_norm(c): c for c in df.columns}
    tema = cols.get("tema")
    contenido = cols.get("contenido")
    if not contenido:
        return ""
    partes = []
    for _, fila in df.iterrows():
        texto = str(fila.get(contenido, "") or "").strip()
        if texto:
            titulo = str(fila.get(tema, "") or "").strip() if tema else ""
            partes.append(f"## {titulo}\n{texto}" if titulo else texto)
    return "\n\n".join(partes)


# ---------------------------------------------------------------------------
# Respuesta
# ---------------------------------------------------------------------------
_TRABAJO = """CÓMO TRABAJAR
- Para CUALQUIER número, lista o dato concreto, usá las herramientas: consultar, listar_registros, ver_columna,
  buscar_en_todo. Nunca inventes ni estimes una cifra que podés calcular.
- Si no sabés dónde está un dato, empezá por ver_modulos; si no sabés cómo está escrito un nombre (médico, obra
  social, estado), usá ver_columna antes de filtrar.
- Si la pregunta es sobre una persona o un texto puntual, usá buscar_en_todo.
- Podés hacer varias consultas y cruzar módulos (por ejemplo, facturación con honorarios o con la agenda).
- Respetá el significado de cada importe: facturado ≠ cobrado ≠ pendiente ≠ deuda ≠ egreso.
- Si un dato no existe o es ambiguo, decilo y explicá qué falta."""

_RESPONDER_ANALISTA = """CÓMO RESPONDER
- En español rioplatense, claro y directo. Primero la respuesta, después el respaldo.
- Montos en formato argentino: $ 1.234.567 (USD aclarado). Meses con nombre (Noviembre 2026).
- Tablas cortas en markdown cuando ayuden. Separá hechos de recomendaciones.
- Al final, una línea breve «Fuente:» con los módulos y filtros usados."""

_PRESENTACION = {
    "analista": ("Sos el asistente de Vitae, el analista interno de la empresa (VMR · Vitae Medicina Reproductiva y\n"
                 "VM · Vitae Medical). Conocés todos sus módulos y los consultás con herramientas."),
    "chat": ("Sos Luz, la asistente virtual de Vitae (VMR · Vitae Medicina Reproductiva y VM · Vitae Medical).\n"
             "Charlás por un chat chiquito, como alguien más del equipo que conoce todos los módulos y los consulta\n"
             "con herramientas."),
}

_RESPONDER_CHAT = """CÓMO RESPONDER (es un chat, no un informe)
- Hablá como una compañera de trabajo: cálida, cercana, en voseo rioplatense («fijate», «te paso», «dale»).
- Corto: 1 a 4 oraciones. Arrancá con el dato que te pidieron, sin preámbulos ni «¡Claro!» de relleno.
- Si hacen falta varios datos, una lista breve (máx. 5 ítems) o una tabla mínima; nada de títulos ni secciones.
- Montos en formato argentino: $ 1.234.567 (USD aclarado). Meses con nombre (Noviembre 2026).
- Si algo merece atención (vencido, saldo negativo, pendiente grande), mencionalo en una frase.
- No pongas «Fuente:» salvo que te lo pidan; si preguntan de dónde sale un número, explicalo simple.
- Si no entendiste o falta un dato (mes, empresa, persona), preguntá en una línea.
- Podés cerrar ofreciendo un paso siguiente concreto («¿Querés que te lo separe por obra social?»), no siempre.
- Saludos y charla casual: respondé natural y breve, sin consultar datos.
- Si te preguntan si sos una persona, decí con simpatía que sos la asistente virtual (IA) de Vitae."""


def _instrucciones(estilo: str = "analista") -> str:
    if estilo == "chat":
        return f"{_PRESENTACION['chat']}\n\n{_TRABAJO}\n\n{_RESPONDER_CHAT}"
    return f"{_PRESENTACION['analista']}\n\n{_TRABAJO}\n\n{_RESPONDER_ANALISTA}"


_INSTRUCCIONES = _instrucciones()


def _cliente_y_modelos():
    from vitae.core.config import GEMINI_API_KEY, GEMINI_MODEL, _secret

    if not GEMINI_API_KEY:
        raise RuntimeError("No está configurada GEMINI_API_KEY.")
    from google import genai

    avanzado = _secret("GEMINI_MODEL_AVANZADO", "")
    modelos = [m for m in dict.fromkeys([avanzado, GEMINI_MODEL]) if m]
    return genai.Client(api_key=GEMINI_API_KEY), modelos


def responder(pregunta: str, modulo_actual: str | None = None, historial: list[dict[str, str]] | None = None,
              datos_visibles: dict[str, pd.DataFrame] | None = None, cliente=None, modelos: list[str] | None = None,
              modulos_permitidos: set[str] | None = None, estilo: str = "analista",
              acciones: list[dict[str, Any]] | None = None) -> str:
    """Responde una pregunta usando herramientas de consulta sobre todos los datos.

    ``estilo``: "analista" (respuestas completas, con fuente) o "chat" (Luz:
    corto, cálido y conversado, para el chat flotante de Luz).

    ``acciones``: si se pasa una lista, Luz además puede PROPONER acciones
    (por ejemplo, programar una cirugía); las propuestas quedan en esa lista y
    sólo se ejecutan cuando el usuario las confirma (vitae/ia/acciones.py).

    ``modulos_permitidos``: si se indica, la IA sólo consulta esos módulos
    (los que el usuario puede abrir en la app).
    """
    from google.genai import types

    if cliente is None or modelos is None:
        cliente, modelos = _cliente_y_modelos()
    datos = _Datos(extra=datos_visibles, permitidos=modulos_permitidos)
    herramientas = crear_herramientas(datos)
    from vitae.ia import experta_facturacion as _experta_fx

    expertas = _experta_fx.herramientas(datos)
    herramientas = herramientas + expertas
    de_accion: list[Callable[..., str]] = []
    if acciones is not None:
        from vitae.ia import acciones as _acciones

        de_accion = _acciones.herramientas(datos, acciones)
        herramientas = herramientas + de_accion

    hoy = pd.Timestamp.today().normalize()
    mes = nombre_mes_es(hoy.to_period("M")) if nombre_mes_es else hoy.strftime("%m/%Y")
    resumen_modulos = "; ".join(
        f"{n} ({MODULES.get(n, {}).get('empresa', '')})" for n in datos.modulos()
    )
    conocimiento = leer_conocimiento()
    sistema = (
        f"{_instrucciones(estilo)}\n\n"
        + (f"{_experta_fx.INSTRUCCIONES}\n\n" if expertas else "")
        + (f"{_acciones.instrucciones(de_accion)}\n\n" if de_accion else "")
        + f"HOY: {['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'][hoy.weekday()]} "
          f"{hoy:%d/%m/%Y} (mes actual: {mes}).\n"
        f"PANTALLA ACTUAL DEL USUARIO: {modulo_actual or 'Inicio'}.\n"
        f"MÓDULOS DISPONIBLES: {resumen_modulos}.\n"
        + (f"\nCONOCIMIENTO DE LA EMPRESA (cargado por la dirección):\n{conocimiento[:20000]}\n" if conocimiento else "")
    )

    contenidos = []
    for turno in (historial or [])[-6:]:
        if turno.get("pregunta"):
            contenidos.append(types.Content(role="user", parts=[types.Part(text=str(turno["pregunta"])[:1500])]))
        if turno.get("respuesta"):
            contenidos.append(types.Content(role="model", parts=[types.Part(text=str(turno["respuesta"])[:3000])]))
    contenidos.append(types.Content(role="user", parts=[types.Part(text=str(pregunta))]))

    config = types.GenerateContentConfig(
        system_instruction=sistema,
        tools=herramientas,
        temperature=0.4 if estilo == "chat" else 0.1,
        max_output_tokens=4000,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=15),
    )
    ultimo_error: Exception | None = None
    for modelo in modelos:
        try:
            respuesta = cliente.models.generate_content(model=modelo, contents=contenidos, config=config)
            texto = (getattr(respuesta, "text", None) or "").strip()
            if texto:
                return texto
            ultimo_error = RuntimeError("Gemini no devolvió texto.")
        except Exception as exc:  # modelo inexistente, cuota, etc.: se prueba el siguiente
            ultimo_error = exc
    raise ultimo_error or RuntimeError("No se pudo obtener respuesta de la IA.")
