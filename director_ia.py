from __future__ import annotations

import json

from typing import Any

import pandas as pd

import streamlit as st

from google import genai

from config import GEMINI_API_KEY

# =========================================================

# CONFIGURACIÓN

# =========================================================

MODELO_DIRECTOR = "gemini-3.5-flash-lite"

client = genai.Client(api_key=GEMINI_API_KEY)

# =========================================================

# FUNCIONES INTERNAS

# =========================================================

def _numero(valor: Any) -> float:

    """

    Convierte cualquier valor numérico válido a float.

    Evita errores por None, NaN, strings o valores inválidos.

    """

    try:

        numero = float(valor)

        if pd.isna(numero):

            return 0.0

        return numero

    except (TypeError, ValueError):

        return 0.0

def _division_segura(numerador: float, denominador: float) -> float:

    """

    Realiza una división sin producir división por cero.

    """

    if denominador == 0:

        return 0.0

    return numerador / denominador

def _fmt_money(valor: float) -> str:

    """

    Formatea valores monetarios al estilo argentino.

    Ejemplo: $ 1.250.430,50

    """

    numero = _numero(valor)

    formateado = f"{numero:,.2f}"

    formateado = (

        formateado

        .replace(",", "TEMP")

        .replace(".", ",")

        .replace("TEMP", ".")

    )

    return f"$ {formateado}"

def _fmt_porcentaje(valor: float) -> str:

    """

    Formatea porcentajes.

    """

    return f"{_numero(valor):.1f}%"

def _nombre_mes(periodo: pd.Period | None) -> str:

    """

    Convierte un período mensual a un nombre legible.

    """

    if periodo is None:

        return "Sin datos"

    meses = {

        1: "enero",

        2: "febrero",

        3: "marzo",

        4: "abril",

        5: "mayo",

        6: "junio",

        7: "julio",

        8: "agosto",

        9: "septiembre",

        10: "octubre",

        11: "noviembre",

        12: "diciembre",

    }

    return f"{meses.get(periodo.month, periodo.month)} {periodo.year}"

def _variacion_porcentual(actual: float, anterior: float) -> float | None:

    """

    Calcula la variación porcentual entre dos períodos.

    Devuelve None cuando el período anterior es cero porque no existe

    una base válida de comparación.

    """

    actual = _numero(actual)

    anterior = _numero(anterior)

    if anterior == 0:

        return None

    return ((actual - anterior) / abs(anterior)) * 100

def _texto_variacion(valor: float | None) -> str:

    """

    Convierte una variación porcentual en texto.

    """

    if valor is None:

        return "sin base suficiente para comparar"

    if abs(valor) < 0.1:

        return "sin cambios relevantes"

    if valor > 0:

        return f"aumentó {_fmt_porcentaje(abs(valor))}"

    return f"disminuyó {_fmt_porcentaje(abs(valor))}"

# =========================================================

# PREPARACIÓN DEL CONTEXTO EMPRESARIAL

# =========================================================

def construir_contexto_ejecutivo(

    global_df: pd.DataFrame,

    anio: int,

) -> dict[str, Any]:

    """

    Convierte el DataFrame global en información ejecutiva.

    No incluye nombres de pacientes ni registros individuales.

    Solo envía a Gemini información consolidada.

    """

    columnas_requeridas = {

        "Fecha",

        "Mes",

        "Empresa",

        "Módulo",

        "Facturado",

        "Cobrado",

        "Pendiente",

        "Egreso",

        "Resultado",

    }

    columnas_faltantes = columnas_requeridas.difference(global_df.columns)

    if columnas_faltantes:

        faltantes = ", ".join(sorted(columnas_faltantes))

        raise ValueError(

            f"Faltan columnas necesarias para el Director IA: {faltantes}"

        )

    df = global_df.copy()

    df["Fecha"] = pd.to_datetime(

        df["Fecha"],

        errors="coerce",

    )

    df = df[df["Fecha"].notna()].copy()

    if df.empty:

        raise ValueError(

            "No hay fechas válidas para generar el análisis ejecutivo."

        )

    columnas_numericas = [

        "Facturado",

        "Cobrado",

        "Pendiente",

        "Egreso",

        "Resultado",

    ]

    for columna in columnas_numericas:

        df[columna] = pd.to_numeric(

            df[columna],

            errors="coerce",

        ).fillna(0.0)

    # -----------------------------------------------------

    # TOTALES ANUALES

    # -----------------------------------------------------

    facturado_total = _numero(df["Facturado"].sum())

    cobrado_total = _numero(df["Cobrado"].sum())

    pendiente_total = _numero(df["Pendiente"].sum())

    egreso_total = _numero(df["Egreso"].sum())

    resultado_total = _numero(df["Resultado"].sum())

    cobranza_pct = (

        _division_segura(cobrado_total, facturado_total) * 100

    )

    pendiente_pct = (

        _division_segura(pendiente_total, facturado_total) * 100

    )

    egresos_sobre_facturacion_pct = (

        _division_segura(egreso_total, facturado_total) * 100

    )

    margen_sobre_cobrado_pct = (

        _division_segura(resultado_total, cobrado_total) * 100

    )

    # -----------------------------------------------------

    # ANÁLISIS MENSUAL

    # -----------------------------------------------------

    df["Periodo"] = df["Fecha"].dt.to_period("M")

    mensual = (

        df.groupby("Periodo", as_index=False)[columnas_numericas]

        .sum()

        .sort_values("Periodo")

    )

    periodos_con_actividad = mensual[

        mensual[columnas_numericas].abs().sum(axis=1) > 0

    ].copy()

    periodo_actual: pd.Period | None = None

    periodo_anterior: pd.Period | None = None

    actual = {

        "Facturado": 0.0,

        "Cobrado": 0.0,

        "Pendiente": 0.0,

        "Egreso": 0.0,

        "Resultado": 0.0,

    }

    anterior = actual.copy()

    if not periodos_con_actividad.empty:

        periodo_actual = periodos_con_actividad.iloc[-1]["Periodo"]

        fila_actual = periodos_con_actividad.iloc[-1]

        actual = {

            columna: _numero(fila_actual[columna])

            for columna in columnas_numericas

        }

        if len(periodos_con_actividad) >= 2:

            periodo_anterior = periodos_con_actividad.iloc[-2]["Periodo"]

            fila_anterior = periodos_con_actividad.iloc[-2]

            anterior = {

                columna: _numero(fila_anterior[columna])

                for columna in columnas_numericas

            }

    variaciones = {

        columna: _variacion_porcentual(

            actual[columna],

            anterior[columna],

        )

        for columna in columnas_numericas

    }

    # -----------------------------------------------------

    # RESUMEN POR MÓDULO

    # -----------------------------------------------------

    por_modulo = (

        df.groupby("Módulo", as_index=False)[columnas_numericas]

        .sum()

    )

    por_modulo["ParticipacionFacturacionPct"] = (

        por_modulo["Facturado"]

        .apply(

            lambda valor: (

                _division_segura(valor, facturado_total) * 100

            )

        )

    )

    ranking_facturacion = por_modulo.sort_values(

        "Facturado",

        ascending=False,

    )

    ranking_egresos = por_modulo.sort_values(

        "Egreso",

        ascending=False,

    )

    ranking_resultados = por_modulo.sort_values(

        "Resultado",

        ascending=False,

    )

    ranking_resultados_negativos = por_modulo[

        por_modulo["Resultado"] < 0

    ].sort_values(

        "Resultado",

        ascending=True,

    )

    modulo_mayor_facturacion = None

    modulo_mayor_egreso = None

    modulo_mejor_resultado = None

    if not ranking_facturacion.empty:

        fila = ranking_facturacion.iloc[0]

        modulo_mayor_facturacion = {

            "modulo": str(fila["Módulo"]),

            "facturado": _numero(fila["Facturado"]),

            "participacion_pct": _numero(

                fila["ParticipacionFacturacionPct"]

            ),

        }

    if not ranking_egresos.empty:

        fila = ranking_egresos.iloc[0]

        modulo_mayor_egreso = {

            "modulo": str(fila["Módulo"]),

            "egreso": _numero(fila["Egreso"]),

        }

    if not ranking_resultados.empty:

        fila = ranking_resultados.iloc[0]

        modulo_mejor_resultado = {

            "modulo": str(fila["Módulo"]),

            "resultado": _numero(fila["Resultado"]),

        }

    modulos_resultado_negativo = []

    for _, fila in ranking_resultados_negativos.head(5).iterrows():

        modulos_resultado_negativo.append({

            "modulo": str(fila["Módulo"]),

            "resultado": _numero(fila["Resultado"]),

        })

    # Gemini no necesita recibir todos los módulos.

    # Enviamos solamente los principales.

    principales_modulos = []

    for _, fila in ranking_facturacion.head(10).iterrows():

        principales_modulos.append({

            "modulo": str(fila["Módulo"]),

            "facturado": _numero(fila["Facturado"]),

            "cobrado": _numero(fila["Cobrado"]),

            "pendiente": _numero(fila["Pendiente"]),

            "egreso": _numero(fila["Egreso"]),

            "resultado": _numero(fila["Resultado"]),

            "participacion_facturacion_pct": _numero(

                fila["ParticipacionFacturacionPct"]

            ),

        })

    # -----------------------------------------------------

    # RESUMEN POR EMPRESA

    # -----------------------------------------------------

    por_empresa = (

        df.groupby("Empresa", as_index=False)[columnas_numericas]

        .sum()

        .sort_values("Facturado", ascending=False)

    )

    empresas = []

    for _, fila in por_empresa.iterrows():

        empresas.append({

            "empresa": str(fila["Empresa"]),

            "facturado": _numero(fila["Facturado"]),

            "cobrado": _numero(fila["Cobrado"]),

            "pendiente": _numero(fila["Pendiente"]),

            "egreso": _numero(fila["Egreso"]),

            "resultado": _numero(fila["Resultado"]),

        })

    # -----------------------------------------------------

    # SERIE MENSUAL PARA TENDENCIAS

    # -----------------------------------------------------

    evolucion_mensual = []

    for _, fila in mensual.tail(12).iterrows():

        periodo = fila["Periodo"]

        evolucion_mensual.append({

            "mes": str(periodo),

            "facturado": _numero(fila["Facturado"]),

            "cobrado": _numero(fila["Cobrado"]),

            "pendiente": _numero(fila["Pendiente"]),

            "egreso": _numero(fila["Egreso"]),

            "resultado": _numero(fila["Resultado"]),

        })

    meses_con_actividad = len(periodos_con_actividad)

    promedio_mensual_facturado = (

        facturado_total / meses_con_actividad

        if meses_con_actividad > 0

        else 0.0

    )

    promedio_mensual_resultado = (

        resultado_total / meses_con_actividad

        if meses_con_actividad > 0

        else 0.0

    )

    # -----------------------------------------------------

    # CONTEXTO FINAL

    # -----------------------------------------------------

    return {

        "anio": anio,

        "periodo_analizado": {

            "desde": str(df["Fecha"].min().date()),

            "hasta": str(df["Fecha"].max().date()),

            "meses_con_actividad": meses_con_actividad,

        },

        "totales": {

            "facturado": facturado_total,

            "cobrado": cobrado_total,

            "pendiente": pendiente_total,

            "egresos": egreso_total,

            "resultado": resultado_total,

        },

        "indicadores": {

            "cobranza_pct": cobranza_pct,

            "pendiente_sobre_facturacion_pct": pendiente_pct,

            "egresos_sobre_facturacion_pct": (

                egresos_sobre_facturacion_pct

            ),

            "margen_sobre_cobrado_pct": margen_sobre_cobrado_pct,

            "promedio_mensual_facturado": promedio_mensual_facturado,

            "promedio_mensual_resultado": promedio_mensual_resultado,

        },

        "ultimo_mes_con_actividad": {

            "nombre": _nombre_mes(periodo_actual),

            "valores": actual,

        },

        "mes_anterior": {

            "nombre": _nombre_mes(periodo_anterior),

            "valores": anterior,

        },

        "variaciones_ultimo_mes": {

            columna: {

                "porcentaje": variaciones[columna],

                "descripcion": _texto_variacion(

                    variaciones[columna]

                ),

            }

            for columna in columnas_numericas

        },

        "modulos_destacados": {

            "mayor_facturacion": modulo_mayor_facturacion,

            "mayor_egreso": modulo_mayor_egreso,

            "mejor_resultado": modulo_mejor_resultado,

            "resultados_negativos": modulos_resultado_negativo,

        },

        "principales_modulos": principales_modulos,

        "empresas": empresas,

        "evolucion_mensual": evolucion_mensual,

    }

# =========================================================

# RESPUESTA LOCAL DE SEGURIDAD

# =========================================================

def generar_resumen_local(

    contexto: dict[str, Any],

) -> str:

    """

    Genera un resumen sin Gemini.

    Se utiliza automáticamente si la API falla, no responde

    o no existe conexión.

    """

    totales = contexto["totales"]

    indicadores = contexto["indicadores"]

    destacados = contexto["modulos_destacados"]

    ultimo_mes = contexto["ultimo_mes_con_actividad"]

    variaciones = contexto["variaciones_ultimo_mes"]

    lineas = [

        "### 🧠 Director IA",

        "",

        "#### Diagnóstico ejecutivo",

        "",

        (

            f"Vitae registra una facturación acumulada de "

            f"**{_fmt_money(totales['facturado'])}**, con "

            f"**{_fmt_money(totales['cobrado'])}** efectivamente "

            f"cobrados."

        ),

        (

            f"La eficiencia de cobranza es del "

            f"**{_fmt_porcentaje(indicadores['cobranza_pct'])}** y "

            f"quedan **{_fmt_money(totales['pendiente'])}** "

            f"pendientes de cobro."

        ),

        (

            f"Los egresos acumulados alcanzan "

            f"**{_fmt_money(totales['egresos'])}** y el resultado "

            f"global es **{_fmt_money(totales['resultado'])}**."

        ),

    ]

    mayor_facturacion = destacados.get("mayor_facturacion")

    if mayor_facturacion:

        lineas.append(

            (

                f"El principal generador de facturación es "

                f"**{mayor_facturacion['modulo']}**, con una "

                f"participación del "

                f"**{_fmt_porcentaje(mayor_facturacion['participacion_pct'])}**."

            )

        )

    mayor_egreso = destacados.get("mayor_egreso")

    if mayor_egreso:

        lineas.append(

            (

                f"El módulo con mayor salida de dinero es "

                f"**{mayor_egreso['modulo']}**, con "

                f"**{_fmt_money(mayor_egreso['egreso'])}**."

            )

        )

    lineas.extend([

        "",

        f"#### Último período: {ultimo_mes['nombre'].capitalize()}",

        "",

        (

            f"- La facturación "

            f"{variaciones['Facturado']['descripcion']} respecto del "

            f"período anterior."

        ),

        (

            f"- La cobranza "

            f"{variaciones['Cobrado']['descripcion']} respecto del "

            f"período anterior."

        ),

        (

            f"- Los egresos "

            f"{variaciones['Egreso']['descripcion']} respecto del "

            f"período anterior."

        ),

        (

            f"- El resultado "

            f"{variaciones['Resultado']['descripcion']} respecto del "

            f"período anterior."

        ),

        "",

        "#### Prioridades recomendadas",

        "",

    ])

    if indicadores["cobranza_pct"] < 70:

        lineas.append(

            "- Priorizar la recuperación de facturas y cuentas "

            "corrientes pendientes."

        )

    else:

        lineas.append(

            "- Mantener el seguimiento de cobranza y reducir el monto "

            "pendiente más antiguo."

        )

    if indicadores["egresos_sobre_facturacion_pct"] > 70:

        lineas.append(

            "- Revisar los módulos con mayores egresos para detectar "

            "costos recurrentes o extraordinarios."

        )

    else:

        lineas.append(

            "- Continuar controlando los egresos para conservar el "

            "resultado positivo."

        )

    if totales["resultado"] < 0:

        lineas.append(

            "- Aplicar medidas inmediatas de contención porque el "

            "resultado acumulado es negativo."

        )

    else:

        lineas.append(

            "- Proteger el resultado acumulado positivo y evitar "

            "incrementar gastos sin respaldo de cobranza."

        )

    return "\n".join(lineas)

# =========================================================

# CONSULTA A GEMINI CON CACHE

# =========================================================

@st.cache_data(

    ttl=900,

    show_spinner=False,

)

def _consultar_director_gemini(

    contexto_json: str,

) -> str:

    """

    Consulta Gemini.

    El cache evita repetir la misma consulta durante 15 minutos

    cuando Streamlit vuelve a ejecutar la página.

    """

    prompt = f"""

Sos el Director Ejecutivo y Financiero del sistema de gestión de

Vitae Medical y Vitae Medicina Reproductiva.

Tu función es analizar información consolidada de la empresa y producir

un diagnóstico ejecutivo útil para la toma de decisiones.

REGLAS OBLIGATORIAS:

1. Usá exclusivamente la información incluida en DATOS CONSOLIDADOS.

2. No inventes cifras, causas, pacientes, fechas ni explicaciones.

3. No confundas facturación con dinero efectivamente cobrado.

4. No confundas pendiente de cobro con deuda a pagar.

5. Diferenciá claramente ingresos, egresos, cobranza y resultado.

6. Cuando no exista una base válida para comparar, decilo.

7. No afirmes que existe una pérdida estructural solo por un mes negativo.

8. No afirmes que existe rentabilidad real si los datos no lo demuestran.

9. Detectá riesgos, oportunidades, concentraciones y cambios de tendencia.

10. Priorizá recomendaciones concretas y aplicables.

11. Escribí en español argentino, profesional, claro y directo.

12. No uses lenguaje exagerado, publicitario ni genérico.

13. Los importes deben escribirse con signo $ y formato argentino.

14. Máximo 500 palabras.

15. La salida debe estar en Markdown.

ESTRUCTURA OBLIGATORIA:

### 🧠 Director IA

#### Diagnóstico ejecutivo

Escribí un análisis de 2 a 4 párrafos cortos.

#### Indicadores clave

Incluí entre 4 y 7 puntos con los datos más importantes.

#### Alertas y riesgos

Incluí únicamente alertas respaldadas por los datos.

Si no hay alertas relevantes, indicá que no se detectan alertas críticas

con la información disponible.

#### Prioridades recomendadas

Incluí exactamente 3 acciones concretas y ordenadas por prioridad.

#### Conclusión

Cerrá con una conclusión ejecutiva breve.

DATOS CONSOLIDADOS:

{contexto_json}

"""

    respuesta = client.models.generate_content(

        model=MODELO_DIRECTOR,

        contents=prompt,

    )

    texto = getattr(respuesta, "text", None)

    if not texto or not str(texto).strip():

        raise RuntimeError(

            "Gemini devolvió una respuesta vacía."

        )

    return str(texto).strip()

# =========================================================

# FUNCIÓN PRINCIPAL

# =========================================================

def generar_resumen_ejecutivo(

    global_df: pd.DataFrame,

    anio: int = 2026,

) -> str:

    """

    Genera el informe del Director IA.

    Primero prepara datos consolidados.

    Luego consulta Gemini.

    Si Gemini falla, devuelve automáticamente un resumen local.

    """

    if global_df is None or global_df.empty:

        return (

            "### 🧠 Director IA\n\n"

            "No hay información suficiente para generar el análisis "

            "ejecutivo."

        )

    try:

        contexto = construir_contexto_ejecutivo(

            global_df=global_df,

            anio=anio,

        )

    except Exception as error:

        return (

            "### 🧠 Director IA\n\n"

            "No fue posible preparar los datos para el análisis.\n\n"

            f"**Detalle técnico:** `{error}`"

        )

    contexto_json = json.dumps(

        contexto,

        ensure_ascii=False,

        sort_keys=True,

        separators=(",", ":"),

    )

    try:

        return _consultar_director_gemini(

            contexto_json=contexto_json,

        )

    except Exception:

        # La aplicación continúa funcionando aunque Gemini falle.

        resumen_local = generar_resumen_local(contexto)

        return (

            f"{resumen_local}\n\n"

            "> ℹ️ El análisis se generó localmente porque el servicio "

            "de IA no estuvo disponible en este momento."

        )