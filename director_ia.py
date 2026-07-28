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

    df["Periodo"] = df["Fecha"].dt.to_period("M")

    hoy = pd.Timestamp.today().normalize()

    periodo_actual_real = hoy.to_period("M")

    # Solo se analizan meses ya transcurridos o el mes actual.

    # Esto evita que meses futuros vacíos sean interpretados como

    # una caída o interrupción de actividad.

    df = df[df["Periodo"] <= periodo_actual_real].copy()

    if df.empty:

        raise ValueError(

            "No hay datos válidos hasta la fecha actual para analizar."

        )

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

# ANALIZADOR UNIVERSAL DE MÓDULOS

# =========================================================

def _briefing_categoria_modulo(

    nombre_modulo: str,

) -> str:

    """

    Clasifica automáticamente cualquier módulo de Vitae.

    """

    nombre = str(nombre_modulo).lower().strip()

    if "factur" in nombre:

        return "facturacion"

    if "caja" in nombre or "banco" in nombre:

        return "tesoreria"

    if "cuenta corriente" in nombre:

        return "cuenta_corriente"

    if any(

        palabra in nombre

        for palabra in [

            "deuda",

            "pago pendiente",

            "pagos pendientes",

            "préstamo",

            "prestamo",

            "honorario",

            "impositiva",

            "impositivo",

        ]

    ):

        return "obligaciones"

    if "tarea" in nombre:

        return "tareas"

    if "contrato" in nombre:

        return "contratos"

    if "vencimiento" in nombre:

        return "vencimientos"

    if "agenda" in nombre or "quirófano" in nombre or "quirofano" in nombre:

        return "agenda"

    if "gine" in nombre:

        return "gine_vitae"

    if "alquiler" in nombre:

        return "alquileres"

    return "otros"

def _briefing_empresa_modulo(

    nombre_modulo: str,

) -> str:

    """

    Detecta a qué empresa pertenece el módulo.

    """

    nombre = str(nombre_modulo).lower().strip()

    if (

        "vmr" in nombre

        or "macro" in nombre

        or "medicina reproductiva" in nombre

    ):

        return "VMR"

    if (

        nombre.endswith(" vm")

        or "galicia vm" in nombre

        or "vitae medical" in nombre

    ):

        return "VM"

    return "VITAE"

def _briefing_serie_columna_monetaria(

    df: pd.DataFrame,

    columna: str,

) -> pd.Series:

    """

    Devuelve una columna monetaria convertida de forma segura.

    """

    if columna not in df.columns:

        return pd.Series(

            [0.0] * len(df),

            index=df.index,

            dtype="float64",

        )

    return df[columna].apply(

        _briefing_numero_monetario

    )

def _briefing_variacion(

    actual: float,

    anterior: float,

) -> float | None:

    """

    Calcula variación porcentual entre dos períodos.

    """

    actual = float(actual or 0)

    anterior = float(anterior or 0)

    if anterior == 0:

        return None

    return (

        (actual - anterior)

        / abs(anterior)

        * 100

    )
def _briefing_serie_fechas(

    df: pd.DataFrame,

) -> pd.Series:

    """

    Detecta y normaliza la fecha principal de cualquier módulo.

    Devuelve una Serie de fechas compatible con el índice original

    del DataFrame. Si no encuentra fechas válidas, devuelve NaT.

    """

    if df is None or df.empty:

        return pd.Series(

            pd.NaT,

            index=getattr(df, "index", None),

            dtype="datetime64[ns]",

        )

    columnas_fecha = [

        "mes",

        "fecha",

        "fecha_factura",

        "fecha_movimiento",

        "fecha_pago",

        "fecha_cobro",

        "fecha_programada",

        "fecha_inicio",

        "fecha_fin",

        "created_at",

        "fecha_creacion",

        "vencimiento",

        "fecha_vencimiento",

    ]

    for columna in columnas_fecha:

        if columna not in df.columns:

            continue

        fechas = pd.to_datetime(

            df[columna],

            errors="coerce",

            dayfirst=True,

            utc=True,

        )

        if fechas.notna().any():

            # Quita la zona horaria para poder comparar

            # las fechas con pd.Timestamp.today().

            return fechas.dt.tz_convert(None)

    return pd.Series(

        pd.NaT,

        index=df.index,

        dtype="datetime64[ns]",

    )
def _briefing_resumir_modulo(

    nombre_modulo: str,

    df_original: pd.DataFrame,

    hoy: pd.Timestamp,

    inicio_mes: pd.Timestamp,

    fecha_corte: pd.Timestamp,

    inicio_mes_anterior: pd.Timestamp,

    fin_mes_anterior: pd.Timestamp,

) -> dict[str, Any]:

    """

    Analiza cualquier módulo de Vitae sin conocer previamente

    su estructura exacta.

    Devuelve únicamente información consolidada.

    """

    categoria = _briefing_categoria_modulo(

        nombre_modulo

    )

    empresa = _briefing_empresa_modulo(

        nombre_modulo

    )

    resumen_base = {

        "modulo": str(nombre_modulo),

        "empresa": empresa,

        "categoria": categoria,

        "tiene_datos": False,

        "filas_total": 0,

        "filas_mes": 0,

        "filas_mes_anterior": 0,

        "monto_mes": 0.0,

        "monto_mes_anterior": 0.0,

        "variacion_monto_pct": None,

        "ingresos_mes": 0.0,

        "egresos_mes": 0.0,

        "flujo_mes": 0.0,

        "saldo_actual": 0.0,

        "facturado_mes": 0.0,

        "facturado_mes_anterior": 0.0,

        "cobrado_mes": 0.0,

        "pendiente_cobro_mes": 0.0,

        "pendiente_cobro_total": 0.0,

        "a_cobrar": 0.0,

        "a_pagar": 0.0,

        "deuda_pendiente": 0.0,

        "registros_pendientes": 0,

        "vencidos": 0,

        "monto_vencido": 0.0,

        "proximos_7_dias": 0,

        "monto_proximos_7_dias": 0.0,

        "proximos_30_dias": 0,

        "monto_proximos_30_dias": 0.0,

        "tareas_abiertas": 0,

        "tareas_criticas": 0,

        "tareas_vencidas": 0,

        "pacientes_mes": 0,

        "error_lectura": None,

    }

    if (

        df_original is None

        or df_original.empty

    ):

        return resumen_base

    try:

        df = df_original.copy()

        resumen_base["tiene_datos"] = True

        resumen_base["filas_total"] = int(

            len(df)

        )

        fechas = _briefing_serie_fechas(df)

        vencimientos = _briefing_serie_vencimientos(df)

        estados = _briefing_estados(df)

        montos = _briefing_serie_montos(df)

        ingresos = _briefing_serie_columna_monetaria(

            df,

            "ingreso",

        )

        egresos = _briefing_serie_columna_monetaria(

            df,

            "egreso",

        )

        pagado = _briefing_serie_columna_monetaria(

            df,

            "pagado",

        )

        estados_cobrados = {

            "cobrado",

            "pagado",

            "completo",

            "completa",

            "realizado",

            "realizada",

            "finalizado",

            "finalizada",

        }

        estados_cancelados = {

            "cancelado",

            "cancelada",

            "anulado",

            "anulada",

        }

        estados_cerrados = (

            estados_cobrados

            | estados_cancelados

        )

        es_cobrado = estados.isin(

            estados_cobrados

        )

        es_cancelado = estados.isin(

            estados_cancelados

        )

        esta_abierto = ~estados.isin(

            estados_cerrados

        )

        es_mes = (

            fechas.notna()

            & (fechas >= inicio_mes)

            & (fechas <= fecha_corte)

            & (~es_cancelado)

        )

        es_mes_anterior = (

            fechas.notna()

            & (fechas >= inicio_mes_anterior)

            & (fechas <= fin_mes_anterior)

            & (~es_cancelado)

        )

        resumen_base["filas_mes"] = int(

            es_mes.sum()

        )

        resumen_base["filas_mes_anterior"] = int(

            es_mes_anterior.sum()

        )

        resumen_base["monto_mes"] = float(

            montos[es_mes].sum()

        )

        resumen_base["monto_mes_anterior"] = float(

            montos[es_mes_anterior].sum()

        )

        resumen_base["variacion_monto_pct"] = (

            _briefing_variacion(

                resumen_base["monto_mes"],

                resumen_base["monto_mes_anterior"],

            )

        )

        # -------------------------------------------------

        # TESORERÍA: CAJAS Y BANCOS

        # -------------------------------------------------

        if categoria == "tesoreria":

            resumen_base["ingresos_mes"] = float(

                ingresos[es_mes].sum()

            )

            resumen_base["egresos_mes"] = float(

                egresos[es_mes].sum()

            )

            resumen_base["flujo_mes"] = (

                resumen_base["ingresos_mes"]

                - resumen_base["egresos_mes"]

            )

            columna_saldo = None

            for posible in [

                "saldo",

                "saldo_movimiento",

            ]:

                if posible in df.columns:

                    columna_saldo = posible

                    break

            if columna_saldo:

                saldos = (

                    df[columna_saldo]

                    .apply(

                        _briefing_numero_monetario

                    )

                )

                if fechas.notna().any():

                    orden = (

                        pd.DataFrame({

                            "_fecha": fechas,

                            "_saldo": saldos,

                        })

                        .loc[

                            lambda tabla: (

                                tabla["_fecha"].notna()

                                & (

                                    tabla["_fecha"]

                                    <= fecha_corte

                                )

                            )

                        ]

                        .sort_values("_fecha")

                    )

                    if not orden.empty:

                        resumen_base["saldo_actual"] = float(

                            orden.iloc[-1]["_saldo"]

                        )

                elif not saldos.empty:

                    resumen_base["saldo_actual"] = float(

                        saldos.iloc[-1]

                    )

            else:

                ingresos_totales = float(

                    ingresos.sum()

                )

                egresos_totales = float(

                    egresos.sum()

                )

                resumen_base["saldo_actual"] = (

                    ingresos_totales

                    - egresos_totales

                )

        # -------------------------------------------------

        # FACTURACIÓN

        # -------------------------------------------------

        if categoria == "facturacion":

            no_cancelado = ~es_cancelado

            resumen_base["facturado_mes"] = float(

                montos[es_mes].sum()

            )

            resumen_base[

                "facturado_mes_anterior"

            ] = float(

                montos[es_mes_anterior].sum()

            )

            resumen_base["cobrado_mes"] = float(

                montos[

                    es_mes

                    & es_cobrado

                ].sum()

            )

            resumen_base[

                "pendiente_cobro_mes"

            ] = max(

                0.0,

                resumen_base["facturado_mes"]

                - resumen_base["cobrado_mes"],

            )

            total_facturado = float(

                montos[no_cancelado].sum()

            )

            total_cobrado = float(

                montos[

                    no_cancelado

                    & es_cobrado

                ].sum()

            )

            resumen_base[

                "pendiente_cobro_total"

            ] = max(

                0.0,

                total_facturado - total_cobrado,

            )

            resumen_base["pacientes_mes"] = int(

                es_mes.sum()

            )

        # -------------------------------------------------

        # CUENTAS CORRIENTES

        # -------------------------------------------------

        if categoria == "cuenta_corriente":

            if "tipo" in df.columns:

                tipos = (

                    df["tipo"]

                    .fillna("")

                    .astype(str)

                    .str.lower()

                    .str.strip()

                )

            else:

                tipos = pd.Series(

                    [""] * len(df),

                    index=df.index,

                )

            saldos_cc = (

                montos - pagado

            ).clip(lower=0)

            resumen_base["a_cobrar"] = float(

                saldos_cc[

                    tipos.eq("a cobrar")

                ].sum()

            )

            resumen_base["a_pagar"] = float(

                saldos_cc[

                    tipos.eq("a pagar")

                ].sum()

            )

        # -------------------------------------------------

        # DEUDAS, HONORARIOS, PRÉSTAMOS Y PAGOS

        # -------------------------------------------------

        if categoria == "obligaciones":

            if "saldo" in df.columns:

                saldos_pendientes = (

                    df["saldo"]

                    .apply(

                        _briefing_numero_monetario

                    )

                    .clip(lower=0)

                )

            elif "pagado" in df.columns:

                saldos_pendientes = (

                    montos - pagado

                ).clip(lower=0)

            else:

                saldos_pendientes = (

                    montos.where(

                        esta_abierto,

                        0.0,

                    )

                    .clip(lower=0)

                )

            resumen_base["deuda_pendiente"] = float(

                saldos_pendientes.sum()

            )

        # -------------------------------------------------

        # ESTADOS PENDIENTES GENERALES

        # -------------------------------------------------

        estados_pendientes = estados.str.contains(

            (

                "pendiente|a cobrar|a pagar|"

                "adeudado|deuda|autorización|"

                "autorizacion|consultando|"

                "en proceso"

            ),

            regex=True,

            na=False,

        )

        resumen_base[

            "registros_pendientes"

        ] = int(

            estados_pendientes.sum()

        )

        # -------------------------------------------------

        # VENCIMIENTOS

        # -------------------------------------------------

        vencidos = (

            vencimientos.notna()

            & (vencimientos < hoy)

            & esta_abierto

            & (~es_cancelado)

        )

        resumen_base["vencidos"] = int(

            vencidos.sum()

        )

        resumen_base["monto_vencido"] = float(

            montos[vencidos].sum()

        )

        limite_7_dias = (

            hoy + pd.Timedelta(days=7)

        )

        limite_30_dias = (

            hoy + pd.Timedelta(days=30)

        )

        proximos_7 = (

            vencimientos.notna()

            & (vencimientos >= hoy)

            & (vencimientos <= limite_7_dias)

            & esta_abierto

            & (~es_cancelado)

        )

        proximos_30 = (

            vencimientos.notna()

            & (vencimientos >= hoy)

            & (vencimientos <= limite_30_dias)

            & esta_abierto

            & (~es_cancelado)

        )

        resumen_base[

            "proximos_7_dias"

        ] = int(

            proximos_7.sum()

        )

        resumen_base[

            "monto_proximos_7_dias"

        ] = float(

            montos[proximos_7].sum()

        )

        resumen_base[

            "proximos_30_dias"

        ] = int(

            proximos_30.sum()

        )

        resumen_base[

            "monto_proximos_30_dias"

        ] = float(

            montos[proximos_30].sum()

        )

        # -------------------------------------------------

        # TAREAS

        # -------------------------------------------------

        if categoria == "tareas":

            resumen_base["tareas_abiertas"] = int(

                esta_abierto.sum()

            )

            prioridad = pd.Series(

                [""] * len(df),

                index=df.index,

                dtype="object",

            )

            for columna_prioridad in [

                "prioridad",

                "criticidad",

                "nivel",

            ]:

                if columna_prioridad in df.columns:

                    prioridad = (

                        df[columna_prioridad]

                        .fillna("")

                        .astype(str)

                        .str.lower()

                        .str.strip()

                    )

                    break

            es_critica = prioridad.str.contains(

                "crítica|critica|alta|urgente",

                regex=True,

                na=False,

            )

            tareas_vencidas = (

                vencimientos.notna()

                & (vencimientos < hoy)

                & esta_abierto

            )

            resumen_base[

                "tareas_vencidas"

            ] = int(

                tareas_vencidas.sum()

            )

            resumen_base[

                "tareas_criticas"

            ] = int(

                (

                    esta_abierto

                    & (

                        es_critica

                        | tareas_vencidas

                    )

                ).sum()

            )

        return resumen_base

    except Exception as error:

        resumen_base["error_lectura"] = str(

            error

        )

        return resumen_base




# =========================================================

# RESPUESTA LOCAL DE SEGURIDAD

# =========================================================
def construir_contexto_briefing_vitae(

    dfs: dict[str, pd.DataFrame],

) -> dict[str, Any]:

    """

    Analiza absolutamente todos los módulos cargados

    en el Dashboard de Vitae.

    No envía registros individuales ni información personal.

    """

    hoy = pd.Timestamp.today().normalize()

    inicio_mes = hoy.replace(day=1)

    fecha_corte = hoy

    fin_mes_anterior = (

        inicio_mes - pd.Timedelta(days=1)

    )

    inicio_mes_anterior = (

        fin_mes_anterior.replace(day=1)

    )

    resumenes_modulos = []

    for nombre_modulo, df in dfs.items():

        resumen = _briefing_resumir_modulo(

            nombre_modulo=nombre_modulo,

            df_original=df,

            hoy=hoy,

            inicio_mes=inicio_mes,

            fecha_corte=fecha_corte,

            inicio_mes_anterior=inicio_mes_anterior,

            fin_mes_anterior=fin_mes_anterior,

        )

        resumenes_modulos.append(

            resumen

        )

    modulos_con_datos = [

        modulo

        for modulo in resumenes_modulos

        if modulo["tiene_datos"]

    ]

    modulos_sin_datos = [

        modulo["modulo"]

        for modulo in resumenes_modulos

        if not modulo["tiene_datos"]

    ]

    modulos_con_error = [

        {

            "modulo": modulo["modulo"],

            "error": modulo["error_lectura"],

        }

        for modulo in resumenes_modulos

        if modulo["error_lectura"]

    ]

    facturacion = [

        modulo

        for modulo in modulos_con_datos

        if modulo["categoria"] == "facturacion"

    ]

    tesoreria = [

        modulo

        for modulo in modulos_con_datos

        if modulo["categoria"] == "tesoreria"

    ]

    obligaciones = [

        modulo

        for modulo in modulos_con_datos

        if modulo["categoria"] in {

            "obligaciones",

            "cuenta_corriente",

        }

    ]

    tareas = [

        modulo

        for modulo in modulos_con_datos

        if modulo["categoria"] == "tareas"

    ]

    contratos = [

        modulo

        for modulo in modulos_con_datos

        if modulo["categoria"] == "contratos"

    ]

    # -----------------------------------------------------

    # FACTURACIÓN GLOBAL

    # -----------------------------------------------------

    facturado_mes = sum(

        modulo["facturado_mes"]

        for modulo in facturacion

    )

    facturado_mes_anterior = sum(

        modulo["facturado_mes_anterior"]

        for modulo in facturacion

    )

    cobrado_mes = sum(

        modulo["cobrado_mes"]

        for modulo in facturacion

    )

    pendiente_cobro_mes = sum(

        modulo["pendiente_cobro_mes"]

        for modulo in facturacion

    )

    pendiente_cobro_total = sum(

        modulo["pendiente_cobro_total"]

        for modulo in facturacion

    )

    pacientes_mes = sum(

        modulo["pacientes_mes"]

        for modulo in facturacion

    )

    variacion_facturacion_pct = (

        _briefing_variacion(

            facturado_mes,

            facturado_mes_anterior,

        )

    )

    cobranza_pct = (

        cobrado_mes

        / facturado_mes

        * 100

        if facturado_mes > 0

        else 0.0

    )

    # -----------------------------------------------------

    # TESORERÍA GLOBAL

    # -----------------------------------------------------

    ingresos_tesoreria_mes = sum(

        modulo["ingresos_mes"]

        for modulo in tesoreria

    )

    egresos_tesoreria_mes = sum(

        modulo["egresos_mes"]

        for modulo in tesoreria

    )

    flujo_tesoreria_mes = (

        ingresos_tesoreria_mes

        - egresos_tesoreria_mes

    )

    disponible_total = sum(

        modulo["saldo_actual"]

        for modulo in tesoreria

    )

    # -----------------------------------------------------

    # DEUDAS Y CUENTAS CORRIENTES

    # -----------------------------------------------------

    deuda_pendiente_total = sum(

        modulo["deuda_pendiente"]

        for modulo in obligaciones

    )

    a_cobrar_cuentas_corrientes = sum(

        modulo["a_cobrar"]

        for modulo in obligaciones

    )

    a_pagar_cuentas_corrientes = sum(

        modulo["a_pagar"]

        for modulo in obligaciones

    )

    # -----------------------------------------------------

    # VENCIMIENTOS GENERALES

    # -----------------------------------------------------

    vencidos_total = sum(

        modulo["vencidos"]

        for modulo in modulos_con_datos

    )

    monto_vencido_total = sum(

        modulo["monto_vencido"]

        for modulo in modulos_con_datos

    )

    vencimientos_proximos_7 = sum(

        modulo["proximos_7_dias"]

        for modulo in modulos_con_datos

    )

    monto_proximos_7 = sum(

        modulo["monto_proximos_7_dias"]

        for modulo in modulos_con_datos

    )

    vencimientos_proximos_30 = sum(

        modulo["proximos_30_dias"]

        for modulo in modulos_con_datos

    )

    monto_proximos_30 = sum(

        modulo["monto_proximos_30_dias"]

        for modulo in modulos_con_datos

    )

    cobros_proximos_7 = sum(

        modulo["monto_proximos_7_dias"]

        for modulo in facturacion

    )

    pagos_proximos_7 = sum(

        modulo["monto_proximos_7_dias"]

        for modulo in obligaciones

    )

    # -----------------------------------------------------

    # TAREAS

    # -----------------------------------------------------

    tareas_abiertas = sum(

        modulo["tareas_abiertas"]

        for modulo in tareas

    )

    tareas_criticas = sum(

        modulo["tareas_criticas"]

        for modulo in tareas

    )

    tareas_vencidas = sum(

        modulo["tareas_vencidas"]

        for modulo in tareas

    )

    contratos_proximos_30 = sum(

        modulo["proximos_30_dias"]

        for modulo in contratos

    )

    # -----------------------------------------------------

    # RANKINGS

    # -----------------------------------------------------

    ranking_facturacion = sorted(

        facturacion,

        key=lambda modulo: modulo["facturado_mes"],

        reverse=True,

    )

    ranking_egresos = sorted(

        tesoreria,

        key=lambda modulo: modulo["egresos_mes"],

        reverse=True,

    )

    ranking_deuda = sorted(

        obligaciones,

        key=lambda modulo: (

            modulo["deuda_pendiente"]

            + modulo["a_pagar"]

        ),

        reverse=True,

    )

    ranking_vencidos = sorted(

        modulos_con_datos,

        key=lambda modulo: modulo["vencidos"],

        reverse=True,

    )

    ranking_actividad = sorted(

        modulos_con_datos,

        key=lambda modulo: modulo["filas_mes"],

        reverse=True,

    )

    def primer_elemento(

        ranking: list[dict[str, Any]],

    ) -> dict[str, Any] | None:

        return ranking[0] if ranking else None

    # -----------------------------------------------------

    # ALERTAS GENERADAS POR EL SISTEMA

    # -----------------------------------------------------

    alertas = []

    if vencidos_total > 0:

        alertas.append({

            "tipo": "vencimientos",

            "nivel": "alto" if vencidos_total >= 10 else "medio",

            "detalle": (

                f"Hay {vencidos_total} registros vencidos "

                f"por {_fmt_money(monto_vencido_total)}."

            ),

        })

    if tareas_criticas > 0:

        alertas.append({

            "tipo": "tareas",

            "nivel": "alto" if tareas_criticas >= 5 else "medio",

            "detalle": (

                f"Hay {tareas_criticas} tareas críticas "

                "o vencidas."

            ),

        })

    if facturado_mes > 0 and cobranza_pct < 50:

        alertas.append({

            "tipo": "cobranza",

            "nivel": "alto",

            "detalle": (

                f"La cobranza del mes es del "

                f"{cobranza_pct:.1f}%."

            ),

        })

    if disponible_total < 0:

        alertas.append({

            "tipo": "liquidez",

            "nivel": "alto",

            "detalle": (

                "El disponible consolidado de cajas y "

                "bancos es negativo."

            ),

        })

    if deuda_pendiente_total > disponible_total:

        alertas.append({

            "tipo": "obligaciones",

            "nivel": "medio",

            "detalle": (

                "Las obligaciones pendientes superan "

                "el disponible consolidado."

            ),

        })

    if modulos_con_error:

        alertas.append({

            "tipo": "lectura",

            "nivel": "medio",

            "detalle": (

                f"No fue posible interpretar completamente "

                f"{len(modulos_con_error)} módulos."

            ),

        })

    # -----------------------------------------------------

    # NIVEL GENERAL

    # -----------------------------------------------------

    alertas_altas = sum(

        1

        for alerta in alertas

        if alerta["nivel"] == "alto"

    )

    if alertas_altas >= 2:

        nivel = "Atención prioritaria"

        emoji_nivel = "🔴"

    elif alertas:

        nivel = "Seguimiento recomendado"

        emoji_nivel = "🟠"

    else:

        nivel = "Situación estable"

        emoji_nivel = "🟢"

    return {

        "fecha_actual": str(hoy.date()),

        "periodo_actual": inicio_mes.strftime("%Y-%m"),

        "periodo_anterior": inicio_mes_anterior.strftime("%Y-%m"),

        "nivel": nivel,

        "emoji_nivel": emoji_nivel,

        "cobertura": {

            "modulos_totales": len(resumenes_modulos),

            "modulos_con_datos": len(modulos_con_datos),

            "modulos_sin_datos": modulos_sin_datos,

            "modulos_con_error": modulos_con_error,

        },

        "resumen_global": {

            "facturado_mes": facturado_mes,

            "facturado_mes_anterior": facturado_mes_anterior,

            "variacion_facturacion_pct": variacion_facturacion_pct,

            "cobrado_mes": cobrado_mes,

            "pendiente_cobro_mes": pendiente_cobro_mes,

            "pendiente_cobro_total": pendiente_cobro_total,

            "cobranza_pct": cobranza_pct,

            "pacientes_mes": pacientes_mes,

            "ingresos_tesoreria_mes": ingresos_tesoreria_mes,

            "egresos_tesoreria_mes": egresos_tesoreria_mes,

            "flujo_tesoreria_mes": flujo_tesoreria_mes,

            "disponible_total": disponible_total,

            "deuda_pendiente_total": deuda_pendiente_total,

            "a_cobrar_cuentas_corrientes": (

                a_cobrar_cuentas_corrientes

            ),

            "a_pagar_cuentas_corrientes": (

                a_pagar_cuentas_corrientes

            ),

            "vencidos_total": vencidos_total,

            "monto_vencido_total": monto_vencido_total,

            "vencimientos_proximos_7": (

                vencimientos_proximos_7

            ),

            "monto_proximos_7": monto_proximos_7,

            "vencimientos_proximos_30": (

                vencimientos_proximos_30

            ),

            "monto_proximos_30": monto_proximos_30,

            "cobros_proximos_7": cobros_proximos_7,

            "pagos_proximos_7": pagos_proximos_7,

            "tareas_abiertas": tareas_abiertas,

            "tareas_criticas": tareas_criticas,

            "tareas_vencidas": tareas_vencidas,

            "contratos_proximos_30": (

                contratos_proximos_30

            ),

        },

        "destacados": {

            "mayor_facturacion": primer_elemento(

                ranking_facturacion

            ),

            "mayor_egreso": primer_elemento(

                ranking_egresos

            ),

            "mayor_deuda": primer_elemento(

                ranking_deuda

            ),

            "mayor_cantidad_vencidos": primer_elemento(

                ranking_vencidos

            ),

            "mayor_actividad_mes": primer_elemento(

                ranking_actividad

            ),

        },

        "alertas": alertas,

        "modulos": resumenes_modulos,

    }
def _briefing_texto_local(

    contexto: dict[str, Any],

) -> str:

    """

    Genera el informe integral sin Gemini.

    """

    globales = contexto["resumen_global"]

    cobertura = contexto["cobertura"]

    destacados = contexto["destacados"]

    lineas = []

    lineas.append(

        "🧠 El sistema analizó "

        f"**{cobertura['modulos_con_datos']} módulos con datos** "

        f"de un total de **{cobertura['modulos_totales']} módulos**."

    )

    variacion = globales[

        "variacion_facturacion_pct"

    ]

    if variacion is not None:

        if variacion > 0:

            lineas.append(

                "📈 La facturación aumentó "

                f"**{abs(variacion):.1f}%** "

                "respecto del mes anterior."

            )

        elif variacion < 0:

            lineas.append(

                "📉 La facturación disminuyó "

                f"**{abs(variacion):.1f}%** "

                "respecto del mes anterior."

            )

    lineas.append(

        "💰 El disponible consolidado de cajas y bancos es "

        f"**{_fmt_money(globales['disponible_total'])}**. "

        "El flujo de tesorería del mes es "

        f"**{_fmt_money(globales['flujo_tesoreria_mes'])}**."

    )

    lineas.append(

        "🎯 La cobranza mensual alcanza el "

        f"**{globales['cobranza_pct']:.1f}%**, con "

        f"**{_fmt_money(globales['pendiente_cobro_total'])}** "

        "pendientes de cobro acumulados."

    )

    if globales["vencidos_total"] > 0:

        lineas.append(

            "⚠️ Se detectaron "

            f"**{globales['vencidos_total']} vencimientos** "

            "por un total de "

            f"**{_fmt_money(globales['monto_vencido_total'])}**."

        )

    if (

        globales["deuda_pendiente_total"] > 0

        or globales["a_pagar_cuentas_corrientes"] > 0

    ):

        obligaciones = (

            globales["deuda_pendiente_total"]

            + globales["a_pagar_cuentas_corrientes"]

        )

        lineas.append(

            "💸 Las obligaciones y cuentas por pagar "

            "identificadas ascienden a "

            f"**{_fmt_money(obligaciones)}**."

        )

    if globales["tareas_criticas"] > 0:

        lineas.append(

            "📋 Hay "

            f"**{globales['tareas_criticas']} tareas críticas "

            "o vencidas** que requieren seguimiento."

        )

    if globales["contratos_proximos_30"] > 0:

        lineas.append(

            "📄 Hay "

            f"**{globales['contratos_proximos_30']} contratos** "

            "con vencimiento durante los próximos 30 días."

        )

    mayor_facturacion = destacados.get(

        "mayor_facturacion"

    )

    if mayor_facturacion:

        lineas.append(

            "🏥 El módulo con mayor facturación del mes es "

            f"**{mayor_facturacion['modulo']}**, con "

            f"**{_fmt_money(mayor_facturacion['facturado_mes'])}**."

        )

    mayor_egreso = destacados.get(

        "mayor_egreso"

    )

    if (

        mayor_egreso

        and mayor_egreso["egresos_mes"] > 0

    ):

        lineas.append(

            "📤 La mayor salida de fondos del mes se concentra en "

            f"**{mayor_egreso['modulo']}**, con "

            f"**{_fmt_money(mayor_egreso['egresos_mes'])}**."

        )

    return "\n\n".join(

        f"- {linea}"

        for linea in lineas[:10]

    )
@st.cache_data(

    ttl=600,

    show_spinner=False,

)
def _consultar_briefing_gemini(

    contexto_json: str,

) -> str:

    """

    Envía a Gemini la información consolidada de todos los módulos

    de Vitae y devuelve un informe ejecutivo breve.

    La respuesta queda en caché durante 10 minutos para evitar

    consultas repetidas cada vez que Streamlit recarga la página.

    """

    prompt = f"""

Sos el gerente virtual integral del sistema de gestión Vitae.

Analizaste información consolidada de todos los módulos disponibles

en el Dashboard.

Los módulos pueden incluir:

- Facturación VMR y VM.

- Cajas.

- Bancos.

- Cuentas corrientes.

- Deudas impositivas.

- Pagos pendientes.

- Honorarios médicos.

- Planes de pago y préstamos.

- Tareas.

- Contratos.

- Vencimientos.

- Agenda quirúrgica.

- Gine Vitae.

- Alquileres.

- Gastos.

- Autorizaciones.

- Cualquier otro módulo incorporado al sistema.

OBJETIVO:

Generar un informe automático integral que permita comprender, al abrir

el Dashboard, qué está pasando en todo Vitae, cuáles son los principales

riesgos y qué situaciones requieren seguimiento.

REGLAS OBLIGATORIAS:

1. Usá exclusivamente los DATOS CONSOLIDADOS proporcionados.

2. No inventes cifras, fechas, causas, estados ni explicaciones.

3. No menciones nombres de usuarios.

4. No saludes dentro de la respuesta.

5. No menciones pacientes individuales.

6. No expongas información personal ni sensible.

7. No confundas facturado con cobrado.

8. No confundas pendiente de cobro con deuda a pagar.

9. No confundas saldo disponible con resultado económico.

10. No sumes conceptos que puedan representar la misma operación.

11. No interpretes meses futuros.

12. Un módulo vacío no representa una caída ni un cese de actividad.

13. Indicá cuántos módulos fueron analizados cuando ese dato esté disponible.

14. Analizá todas las áreas, pero priorizá solamente los hallazgos relevantes.

15. Prestá especial atención a:

    - liquidez;

    - cajas y bancos;

    - facturación;

    - cobranza;

    - pendientes de cobro;

    - egresos;

    - deudas;

    - cuentas corrientes;

    - pagos pendientes;

    - vencimientos;

    - tareas;

    - contratos;

    - actividad operativa;

    - concentraciones por empresa o módulo.

16. Diferenciá claramente hechos de recomendaciones.

17. Los cobros próximos son expectativas y no dinero garantizado.

18. No afirmes que existe una crisis o pérdida estructural sin evidencia.

19. Si faltan datos para una conclusión, indicalo claramente.

20. Escribí en español argentino profesional, claro y directo.

21. Devolvé entre 7 y 10 viñetas.

22. Cada viñeta debe comenzar con "- " y un emoji.

23. No agregues título, saludo, introducción ni conclusión.

24. Máximo 280 palabras.

25. Los importes deben utilizar formato argentino:

    $ 1.250.000,50.

26. Priorizá primero las alertas, luego los cambios relevantes y finalmente

    las oportunidades o recomendaciones.

27. No repitas el mismo dato en distintas viñetas.

DATOS CONSOLIDADOS DE TODOS LOS MÓDULOS:

{contexto_json}

"""

    respuesta = client.models.generate_content(

        model=MODELO_DIRECTOR,

        contents=prompt,

    )

    texto = getattr(

        respuesta,

        "text",

        None,

    )

    if not texto or not str(texto).strip():

        raise RuntimeError(

            "Gemini devolvió una respuesta vacía."

        )

    texto_limpio = (

        str(texto)

        .replace("```markdown", "")

        .replace("```md", "")

        .replace("```", "")

        .strip()

    )

    if not texto_limpio:

        raise RuntimeError(

            "Gemini no devolvió contenido utilizable."

        )

    return texto_limpio
def _saludo_institucional() -> str:

    """

    Genera un saludo institucional según la hora,

    sin mencionar nombres de usuarios.

    """

    hora_actual = pd.Timestamp.now().hour

    if hora_actual < 12:

        return "Buenos días"

    if hora_actual < 20:

        return "Buenas tardes"

    return "Buenas noches"
def generar_briefing_automatico(

    dfs: dict[str, pd.DataFrame],

) -> dict[str, Any]:

    """

    Genera el informe automático integral del Dashboard.

    Proceso:

    1. Analiza todos los módulos incluidos en `dfs`.

    2. Construye información consolidada y sin datos personales.

    3. Consulta Gemini.

    4. Si Gemini falla, genera un informe local.

    5. Siempre devuelve la misma estructura para evitar que

       el Dashboard se rompa.

    """

    saludo = _saludo_institucional()

    actualizado = pd.Timestamp.now().strftime(

        "%d/%m/%Y %H:%M"

    )

    # Estructura segura utilizada si ocurre algún error.

    respuesta_base: dict[str, Any] = {

        "saludo": saludo,

        "nivel": "Información parcial",

        "emoji_nivel": "⚪",

        "contenido": (

            "- No se pudo completar el análisis integral "

            "con la información disponible."

        ),

        "actualizado": actualizado,

        "modo": "local",

        "modulos_analizados": 0,

        "modulos_con_datos": 0,

        "modulos_sin_datos": [],

        "nombres_modulos": [],

    }

    if dfs is None or not isinstance(dfs, dict):

        respuesta_base["contenido"] = (

            "- No se recibió una estructura válida de módulos "

            "para realizar el análisis."

        )

        return respuesta_base

    try:

        contexto = construir_contexto_briefing_vitae(

            dfs=dfs,

        )

    except Exception as error:

        respuesta_base["contenido"] = (

            "- No fue posible preparar completamente los datos "

            "del Dashboard para el análisis.\n\n"

            f"- Detalle técnico: `{error}`"

        )

        return respuesta_base

    cobertura = contexto.get(

        "cobertura",

        {},

    )

    modulos = contexto.get(

        "modulos",

        [],

    )

    modulos_analizados = int(

        cobertura.get(

            "modulos_totales",

            len(modulos),

        )

        or 0

    )

    modulos_con_datos = int(

        cobertura.get(

            "modulos_con_datos",

            0,

        )

        or 0

    )

    modulos_sin_datos = cobertura.get(

        "modulos_sin_datos",

        [],

    )
    modulos_con_error = cobertura.get(

        "modulos_con_error",

        [],

    )

    if not isinstance(

        modulos_con_error,

        list,

    ):

        modulos_con_error = []
    if not isinstance(

        modulos_sin_datos,

        list,

    ):

        modulos_sin_datos = []

    nombres_modulos = []

    for modulo in modulos:

        if not isinstance(modulo, dict):

            continue

        nombre = str(

            modulo.get(

                "modulo",

                "",

            )

        ).strip()

        if nombre:

            nombres_modulos.append(nombre)

    contexto_json = json.dumps(

        contexto,

        ensure_ascii=False,

        sort_keys=True,

        separators=(",", ":"),

        default=str,

    )

    try:

        contenido = _consultar_briefing_gemini(

            contexto_json=contexto_json,

        )

        modo = "IA"

    except Exception:

        try:

            contenido = _briefing_texto_local(

                contexto=contexto,

            )

        except Exception as error_local:

            contenido = (

                "- El análisis automático no estuvo disponible "

                "en este momento.\n\n"

                f"- Detalle técnico: `{error_local}`"

            )

        modo = "local"

    if not contenido or not str(contenido).strip():

        contenido = (

            "- No se detectaron conclusiones suficientes "

            "con la información disponible."

        )

    return {

        "saludo": saludo,

        "nivel": contexto.get(

            "nivel",

            "Información disponible",

        ),

        "emoji_nivel": contexto.get(

            "emoji_nivel",

            "⚪",

        ),

        "contenido": str(contenido).strip(),

        "actualizado": actualizado,

        "modo": modo,

        "modulos_analizados": modulos_analizados,

        "modulos_con_datos": modulos_con_datos,

        "modulos_sin_datos": modulos_sin_datos,
        "modulos_con_error": modulos_con_error,
        "nombres_modulos": nombres_modulos,

    }






# =========================================================

# CONSULTA A GEMINI CON CACHE

# =========================================================



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
16. Nunca interpretes meses posteriores a la fecha actual.

17. La ausencia de datos en meses futuros no representa una caída, interrupción ni cese de actividad.

18. Solo podés analizar tendencias hasta el último mes real con datos y nunca proyectar meses futuros como hechos consumados.

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