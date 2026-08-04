# -*- coding: utf-8 -*-
"""
Agenda Quirófano ULTRA PRO para VITAE.

Integración recomendada desde views.py:

    from agenda_quirofano_ultra_pro import render_agenda_quirofano_ultra_pro

    if table == "agenda_quirofano":
        render_agenda_quirofano_ultra_pro(
            df_original=df_base,
            guardar_callback=lambda df_nuevo: save_table(table, df_nuevo),
        )
        return

No requiere librerías nuevas: usa pandas, plotly y streamlit, que ya forman
parte del proyecto.
"""

from __future__ import annotations

import calendar
import html
import unicodedata
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Optional

import pandas as pd
import plotly.express as px
import streamlit as st


GuardarCallback = Callable[[pd.DataFrame], Any]

ESTADOS = [
    "Consulta",
    "Pendiente de autorización",
    "Autorizado",
    "Programado",
    "Confirmado",
    "En quirófano",
    "Realizado",
    "Reprogramado",
    "Suspendido",
    "Cancelado",
]

ESTADOS_CERRADOS = {"Realizado", "Suspendido", "Cancelado"}
ESTADOS_NO_OCUPAN = {"Suspendido", "Cancelado"}

GRUPOS_KANBAN = {
    "Consulta": {"Consulta"},
    "Autorización": {"Pendiente de autorización", "Autorizado"},
    "Programación": {"Programado", "Confirmado", "Reprogramado"},
    "En quirófano": {"En quirófano"},
    "Finalizado": {"Realizado"},
    "Suspendido": {"Suspendido", "Cancelado"},
}

ESTADO_ALIAS = {
    "consulta": "Consulta",
    "pendiente": "Pendiente de autorización",
    "pendiente autorizacion": "Pendiente de autorización",
    "pendiente de autorizacion": "Pendiente de autorización",
    "autorizada": "Autorizado",
    "autorizado": "Autorizado",
    "programada": "Programado",
    "programado": "Programado",
    "confirmada": "Confirmado",
    "confirmado": "Confirmado",
    "en quirofano": "En quirófano",
    "realizada": "Realizado",
    "realizado": "Realizado",
    "finalizada": "Realizado",
    "finalizado": "Realizado",
    "reprogramada": "Reprogramado",
    "reprogramado": "Reprogramado",
    "suspendida": "Suspendido",
    "suspendido": "Suspendido",
    "cancelada": "Cancelado",
    "cancelado": "Cancelado",
}

CAMPOS = {
    "fecha": "",
    "hora_inicio": "",
    "hora_fin": "",
    "duracion_min": 60,
    "sala": "",
    "paciente": "",
    "procedimiento": "",
    "medico": "",
    "estado": "Consulta",
    "anestesista": "",
    "tipo_anestesia": "",
    "obra_social": "",
    "numero_afiliado": "",
    "autorizacion": "",
    "telefono": "",
    "prioridad": "Normal",
    "ayuno_confirmado": "No",
    "consentimiento_firmado": "No",
    "prequirurgico_completo": "No",
    "material_confirmado": "No",
    "observaciones": "",
}

ALIAS_COLUMNAS = {
    "fecha": "fecha",
    "fecha_cirugia": "fecha",
    "fecha_procedimiento": "fecha",
    "fecha_turno": "fecha",
    "dia": "fecha",
    "hora": "hora_inicio",
    "inicio": "hora_inicio",
    "hora_inicio": "hora_inicio",
    "fin": "hora_fin",
    "hora_fin": "hora_fin",
    "duracion": "duracion_min",
    "duracion_minutos": "duracion_min",
    "duracion_min": "duracion_min",
    "quirofano": "sala",
    "sala": "sala",
    "nombre_paciente": "paciente",
    "apellido_y_nombre": "paciente",
    "apellido_nombre": "paciente",
    "afiliado": "paciente",
    "paciente": "paciente",
    "practica": "procedimiento",
    "cirugia": "procedimiento",
    "procedimiento": "procedimiento",
    "medico_responsable": "medico",
    "profesional": "medico",
    "cirujano": "medico",
    "medico": "medico",
    "estado_agenda": "estado",
    "estado": "estado",
    "anestesiologo": "anestesista",
    "anestesista": "anestesista",
    "tipo_de_anestesia": "tipo_anestesia",
    "tipo_anestesia": "tipo_anestesia",
    "obra_social_prepaga": "obra_social",
    "cobertura": "obra_social",
    "obra_social": "obra_social",
    "n_afiliado": "numero_afiliado",
    "numero_de_afiliado": "numero_afiliado",
    "nro_afiliado": "numero_afiliado",
    "numero_afiliado": "numero_afiliado",
    "autorizacion": "autorizacion",
    "numero_autorizacion": "autorizacion",
    "telefono": "telefono",
    "celular": "telefono",
    "prioridad": "prioridad",
    "ayuno": "ayuno_confirmado",
    "ayuno_confirmado": "ayuno_confirmado",
    "consentimiento": "consentimiento_firmado",
    "consentimiento_firmado": "consentimiento_firmado",
    "prequirurgico": "prequirurgico_completo",
    "prequirurgico_completo": "prequirurgico_completo",
    "material": "material_confirmado",
    "material_confirmado": "material_confirmado",
    "observacion": "observaciones",
    "observaciones": "observaciones",
}

MESES = {
    1: "Enero",
    2: "Febrero",
    3: "Marzo",
    4: "Abril",
    5: "Mayo",
    6: "Junio",
    7: "Julio",
    8: "Agosto",
    9: "Septiembre",
    10: "Octubre",
    11: "Noviembre",
    12: "Diciembre",
}

DIAS = {
    "Monday": "Lunes",
    "Tuesday": "Martes",
    "Wednesday": "Miércoles",
    "Thursday": "Jueves",
    "Friday": "Viernes",
    "Saturday": "Sábado",
    "Sunday": "Domingo",
}


def _normalizar_nombre(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    for viejo, nuevo in {
        " ": "_",
        "-": "_",
        "/": "_",
        ".": "",
        "(": "",
        ")": "",
        "°": "",
        "º": "",
    }.items():
        texto = texto.replace(viejo, nuevo)
    while "__" in texto:
        texto = texto.replace("__", "_")
    return texto.strip("_")


def _texto(valor: Any) -> str:
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except Exception:
        pass
    texto = str(valor).strip()
    return "" if texto.lower() in {"nan", "none", "nat"} else texto


def _escapar(valor: Any) -> str:
    return html.escape(_texto(valor))


def _fecha(valor: Any) -> pd.Timestamp:
    if not _texto(valor):
        return pd.NaT

    # Google Sheets suele devolver YYYY-MM-DD. Con dayfirst=True, fechas como
    # 2026-08-02 pueden interpretarse erróneamente como 8 de febrero. Primero
    # se intenta el formato ISO exacto y después formatos habituales argentinos.
    if isinstance(valor, (pd.Timestamp, datetime, date)):
        return pd.to_datetime(valor, errors="coerce")

    texto = _texto(valor)
    for formato in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return pd.Timestamp(datetime.strptime(texto[:10], formato))
        except (TypeError, ValueError):
            continue
    return pd.to_datetime(valor, errors="coerce", dayfirst=True)


def _hora_texto(valor: Any, por_defecto: str = "08:00") -> str:
    texto = _texto(valor)
    if not texto:
        return por_defecto

    try:
        if isinstance(valor, time):
            return valor.strftime("%H:%M")

        if ":" in texto:
            partes = texto.split(":")
            hora = max(0, min(23, int(float(partes[0]))))
            minuto = max(0, min(59, int(float(partes[1]))))
            return f"{hora:02d}:{minuto:02d}"

        numero = float(texto.replace(",", "."))
        if 0 <= numero < 1:
            minutos = round(numero * 24 * 60)
            return f"{(minutos // 60) % 24:02d}:{minutos % 60:02d}"
        return f"{max(0, min(23, int(numero))):02d}:00"
    except Exception:
        return por_defecto


def _hora_objeto(valor: Any, por_defecto: str = "08:00") -> time:
    texto = _hora_texto(valor, por_defecto)
    try:
        return datetime.strptime(texto, "%H:%M").time()
    except Exception:
        return time(8, 0)


def _numero_entero(valor: Any, por_defecto: int = 60) -> int:
    try:
        numero = int(float(str(valor).replace(",", ".")))
        return max(5, min(1440, numero))
    except Exception:
        return por_defecto


def _si_no(valor: Any) -> str:
    texto = _normalizar_nombre(valor).replace("_", " ")
    if texto in {"si", "s", "true", "1", "ok", "confirmado", "completo"}:
        return "Sí"
    return "No"


def _booleano(valor: Any) -> bool:
    return _si_no(valor) == "Sí"


def _estado(valor: Any) -> str:
    texto = _texto(valor)
    if not texto:
        return "Consulta"
    clave = _normalizar_nombre(texto).replace("_", " ")
    return ESTADO_ALIAS.get(clave, texto if texto in ESTADOS else "Consulta")


def _fin_desde_inicio(inicio: str, duracion: int) -> str:
    try:
        base = datetime.strptime(inicio, "%H:%M")
        return (base + timedelta(minutes=duracion)).strftime("%H:%M")
    except Exception:
        return inicio


def _combinar(fecha_valor: Any, hora_valor: Any) -> pd.Timestamp:
    fecha_dt = _fecha(fecha_valor)
    if pd.isna(fecha_dt):
        return pd.NaT
    hora = _hora_texto(hora_valor, "08:00")
    return pd.to_datetime(
        f"{fecha_dt.strftime('%Y-%m-%d')} {hora}",
        errors="coerce",
    )


def _resolver_columnas(
    df_original: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, str]]:
    original = df_original.copy().reset_index(drop=True)
    mapa: dict[str, str] = {}

    for columna in original.columns:
        normalizada = _normalizar_nombre(columna)
        campo = ALIAS_COLUMNAS.get(normalizada)
        if campo and campo not in mapa:
            mapa[campo] = columna

    # Las columnas faltantes se agregan con nombre estándar. No se toca ningún
    # dato existente y se mantiene el orden original de la hoja.
    for campo in CAMPOS:
        if campo not in mapa:
            original[campo] = ""
            mapa[campo] = campo

    return original, mapa


def _preparar_dataframe(
    df_original: Optional[pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    entrada = df_original.copy() if isinstance(df_original, pd.DataFrame) else pd.DataFrame()
    original, mapa = _resolver_columnas(entrada)

    preparado = pd.DataFrame(index=original.index)
    for campo, por_defecto in CAMPOS.items():
        serie = original[mapa[campo]] if mapa[campo] in original.columns else por_defecto
        preparado[campo] = serie

    preparado["_row_position"] = range(len(preparado))
    preparado["_fecha_dt"] = preparado["fecha"].apply(_fecha)
    preparado["estado"] = preparado["estado"].apply(_estado)
    preparado["hora_inicio"] = preparado["hora_inicio"].apply(
        lambda v: _hora_texto(v, "08:00") if _texto(v) else ""
    )
    preparado["duracion_min"] = preparado["duracion_min"].apply(_numero_entero)

    hora_fin_existente = preparado["hora_fin"].apply(
        lambda v: _hora_texto(v, "") if _texto(v) else ""
    )
    preparado["hora_fin"] = [
        fin if fin else _fin_desde_inicio(inicio or "08:00", duracion)
        for fin, inicio, duracion in zip(
            hora_fin_existente,
            preparado["hora_inicio"],
            preparado["duracion_min"],
        )
    ]

    columnas_texto = [
        "sala",
        "paciente",
        "procedimiento",
        "medico",
        "anestesista",
        "tipo_anestesia",
        "obra_social",
        "numero_afiliado",
        "autorizacion",
        "telefono",
        "prioridad",
        "observaciones",
    ]
    for columna in columnas_texto:
        preparado[columna] = preparado[columna].apply(_texto)

    for columna in [
        "ayuno_confirmado",
        "consentimiento_firmado",
        "prequirurgico_completo",
        "material_confirmado",
    ]:
        preparado[columna] = preparado[columna].apply(_si_no)

    preparado["_inicio_dt"] = preparado.apply(
        lambda fila: _combinar(fila["_fecha_dt"], fila["hora_inicio"]), axis=1
    )
    preparado["_fin_dt"] = preparado.apply(
        lambda fila: _combinar(fila["_fecha_dt"], fila["hora_fin"]), axis=1
    )
    preparado["_es_vacio"] = preparado.apply(
        lambda fila: not any(
            [
                _texto(fila["fecha"]),
                _texto(fila["paciente"]),
                _texto(fila["procedimiento"]),
                _texto(fila["medico"]),
                _texto(fila["sala"]),
                _texto(fila["observaciones"]),
            ]
        ),
        axis=1,
    )

    checklist = [
        "ayuno_confirmado",
        "consentimiento_firmado",
        "prequirurgico_completo",
        "material_confirmado",
    ]
    preparado["_checklist_ok"] = preparado[checklist].apply(
        lambda fila: sum(_booleano(valor) for valor in fila), axis=1
    )
    preparado["_preparacion_pct"] = (preparado["_checklist_ok"] / len(checklist) * 100).round(0)

    return original, preparado, mapa


def _actualizar_campo(
    original: pd.DataFrame,
    mapa: dict[str, str],
    posicion: int,
    campo: str,
    valor: Any,
) -> None:
    columna = mapa[campo]
    original.at[posicion, columna] = valor


def _actualizar_fila(
    original: pd.DataFrame,
    mapa: dict[str, str],
    posicion: int,
    datos: dict[str, Any],
) -> pd.DataFrame:
    resultado = original.copy()
    for campo, valor in datos.items():
        if campo in mapa:
            _actualizar_campo(resultado, mapa, posicion, campo, valor)

    if "updated_at" in resultado.columns:
        resultado.at[posicion, "updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return resultado


def _siguiente_id(df: pd.DataFrame, columna: str) -> Any:
    serie = pd.to_numeric(df[columna], errors="coerce")
    if serie.notna().any():
        return int(serie.max()) + 1
    return f"AG-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"


def _agregar_fila(
    original: pd.DataFrame,
    mapa: dict[str, str],
    datos: dict[str, Any],
) -> pd.DataFrame:
    nueva = {columna: "" for columna in original.columns}
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for campo, valor in datos.items():
        if campo in mapa:
            nueva[mapa[campo]] = valor

    if "id" in original.columns:
        nueva["id"] = _siguiente_id(original, "id")
    if "created_at" in original.columns:
        nueva["created_at"] = ahora
    if "updated_at" in original.columns:
        nueva["updated_at"] = ahora

    return pd.concat([original, pd.DataFrame([nueva])], ignore_index=True)


def _limpiar_para_guardar(df: pd.DataFrame, mapa: dict[str, str]) -> pd.DataFrame:
    resultado = df.copy()

    # Quita solamente filas completamente vacías. Una fila con fecha, paciente,
    # procedimiento o cualquier otro dato operativo se conserva.
    columnas_operativas = [mapa[campo] for campo in CAMPOS if mapa[campo] in resultado.columns]
    if columnas_operativas:
        mascara_vacia = resultado[columnas_operativas].apply(
            lambda fila: not any(_texto(valor) for valor in fila), axis=1
        )
        resultado = resultado.loc[~mascara_vacia].copy()

    return resultado.reset_index(drop=True)


def _guardar(
    df: pd.DataFrame,
    mapa: dict[str, str],
    guardar_callback: Optional[GuardarCallback],
    mensaje: str,
) -> bool:
    if not callable(guardar_callback):
        st.error(
            "La agenda está abierta, pero no tiene conectado el guardado. "
            "En views.py debe enviarse guardar_callback=lambda df: save_table(table, df)."
        )
        return False

    try:
        guardar_callback(_limpiar_para_guardar(df, mapa))
        st.cache_data.clear()
        st.success(mensaje)
        return True
    except Exception as error:
        st.error("No se pudieron guardar los cambios de la agenda en Google Sheets.")
        st.exception(error)
        return False


def _conflictos(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    activos = df[
        df["_fecha_dt"].notna()
        & df["_inicio_dt"].notna()
        & df["_fin_dt"].notna()
        & ~df["estado"].isin(ESTADOS_NO_OCUPAN)
        & ~df["_es_vacio"]
    ].copy()

    filas: list[dict[str, Any]] = []

    def revisar_recurso(columna: str, tipo: str) -> None:
        validos = activos[activos[columna].astype(str).str.strip().ne("")]
        for (fecha_valor, recurso), grupo in validos.groupby(["_fecha_dt", columna]):
            grupo = grupo.sort_values("_inicio_dt")
            registros = list(grupo.to_dict("records"))
            for i, primero in enumerate(registros):
                for segundo in registros[i + 1 :]:
                    if segundo["_inicio_dt"] >= primero["_fin_dt"]:
                        break
                    if (
                        primero["_inicio_dt"] < segundo["_fin_dt"]
                        and segundo["_inicio_dt"] < primero["_fin_dt"]
                    ):
                        filas.append(
                            {
                                "Tipo": tipo,
                                "Fecha": pd.Timestamp(fecha_valor).strftime("%d/%m/%Y"),
                                "Recurso": recurso,
                                "Paciente A": primero["paciente"] or "Sin nombre",
                                "Horario A": f"{primero['hora_inicio']}–{primero['hora_fin']}",
                                "Paciente B": segundo["paciente"] or "Sin nombre",
                                "Horario B": f"{segundo['hora_inicio']}–{segundo['hora_fin']}",
                                "_fila_a": primero["_row_position"],
                                "_fila_b": segundo["_row_position"],
                            }
                        )

    revisar_recurso("sala", "Superposición de sala")
    revisar_recurso("medico", "Superposición de médico")
    return pd.DataFrame(filas)


def _tiene_superposicion(
    df: pd.DataFrame,
    datos: dict[str, Any],
    excluir_posicion: Optional[int] = None,
) -> list[str]:
    fecha_dt = _fecha(datos.get("fecha"))
    inicio_dt = _combinar(fecha_dt, datos.get("hora_inicio"))
    fin_dt = _combinar(fecha_dt, datos.get("hora_fin"))
    if pd.isna(fecha_dt) or pd.isna(inicio_dt) or pd.isna(fin_dt):
        return []

    candidatos = df[
        df["_fecha_dt"].dt.date.eq(fecha_dt.date())
        & ~df["estado"].isin(ESTADOS_NO_OCUPAN)
        & df["_inicio_dt"].notna()
        & df["_fin_dt"].notna()
    ].copy()
    if excluir_posicion is not None:
        candidatos = candidatos[candidatos["_row_position"] != excluir_posicion]

    mensajes: list[str] = []
    for _, fila in candidatos.iterrows():
        solapa = inicio_dt < fila["_fin_dt"] and fila["_inicio_dt"] < fin_dt
        if not solapa:
            continue
        if _texto(datos.get("sala")) and _texto(datos.get("sala")) == fila["sala"]:
            mensajes.append(
                f"Sala {fila['sala']}: {fila['paciente'] or 'Paciente sin nombre'} "
                f"({fila['hora_inicio']}–{fila['hora_fin']})"
            )
        if _texto(datos.get("medico")) and _texto(datos.get("medico")) == fila["medico"]:
            mensajes.append(
                f"Médico {fila['medico']}: {fila['paciente'] or 'Paciente sin nombre'} "
                f"({fila['hora_inicio']}–{fila['hora_fin']})"
            )
    return sorted(set(mensajes))


def _alertas(df: pd.DataFrame) -> pd.DataFrame:
    hoy = pd.Timestamp.today().normalize()
    filas: list[dict[str, str]] = []

    for _, fila in df[~df["_es_vacio"]].iterrows():
        fecha_dt = fila["_fecha_dt"]
        paciente = fila["paciente"] or "Paciente sin nombre"
        fecha_txt = fecha_dt.strftime("%d/%m/%Y") if pd.notna(fecha_dt) else "Sin fecha"

        faltantes = []
        for campo, nombre in [
            ("fecha", "fecha"),
            ("paciente", "paciente"),
            ("procedimiento", "procedimiento"),
            ("medico", "médico"),
            ("sala", "sala"),
            ("hora_inicio", "hora de inicio"),
        ]:
            if not _texto(fila[campo]):
                faltantes.append(nombre)
        if faltantes:
            filas.append(
                {
                    "Severidad": "🔴 Alta",
                    "Tipo": "Datos incompletos",
                    "Fecha": fecha_txt,
                    "Paciente": paciente,
                    "Detalle": "Falta: " + ", ".join(faltantes),
                }
            )

        if pd.notna(fecha_dt) and fecha_dt.normalize() >= hoy:
            dias = (fecha_dt.normalize() - hoy).days
            if dias <= 7 and fila["estado"] in {
                "Pendiente de autorización",
                "Programado",
                "Confirmado",
            } and not fila["autorizacion"]:
                filas.append(
                    {
                        "Severidad": "🔴 Alta" if dias <= 2 else "🟠 Media",
                        "Tipo": "Autorización pendiente",
                        "Fecha": fecha_txt,
                        "Paciente": paciente,
                        "Detalle": f"Procedimiento dentro de {dias} día(s) sin número de autorización.",
                    }
                )

            if dias <= 2 and fila["estado"] in {"Programado", "Confirmado"}:
                pendientes = 4 - int(fila["_checklist_ok"])
                if pendientes > 0:
                    filas.append(
                        {
                            "Severidad": "🔴 Alta",
                            "Tipo": "Checklist incompleto",
                            "Fecha": fecha_txt,
                            "Paciente": paciente,
                            "Detalle": f"Faltan {pendientes} control(es): ayuno, consentimiento, prequirúrgico o material.",
                        }
                    )
                if not fila["anestesista"]:
                    filas.append(
                        {
                            "Severidad": "🟠 Media",
                            "Tipo": "Anestesista sin definir",
                            "Fecha": fecha_txt,
                            "Paciente": paciente,
                            "Detalle": "Procedimiento próximo sin anestesista registrado.",
                        }
                    )

        if (
            pd.notna(fecha_dt)
            and fecha_dt.normalize() < hoy
            and fila["estado"] not in ESTADOS_CERRADOS
        ):
            filas.append(
                {
                    "Severidad": "🟠 Media",
                    "Tipo": "Estado desactualizado",
                    "Fecha": fecha_txt,
                    "Paciente": paciente,
                    "Detalle": f"La fecha ya pasó y continúa como «{fila['estado']}».",
                }
            )

    duplicados = df[
        ~df["_es_vacio"]
        & df["_fecha_dt"].notna()
        & df["paciente"].str.strip().ne("")
        & df["procedimiento"].str.strip().ne("")
    ].copy()
    if not duplicados.empty:
        mascara = duplicados.duplicated(
            subset=["_fecha_dt", "paciente", "procedimiento"], keep=False
        )
        for _, fila in duplicados[mascara].iterrows():
            filas.append(
                {
                    "Severidad": "🟠 Media",
                    "Tipo": "Posible duplicado",
                    "Fecha": fila["_fecha_dt"].strftime("%d/%m/%Y"),
                    "Paciente": fila["paciente"],
                    "Detalle": fila["procedimiento"],
                }
            )

    alertas = pd.DataFrame(filas)
    if not alertas.empty:
        orden = {"🔴 Alta": 0, "🟠 Media": 1, "🟡 Baja": 2}
        alertas["_orden"] = alertas["Severidad"].map(orden).fillna(9)
        alertas = alertas.sort_values(["_orden", "Fecha", "Paciente"]).drop(columns="_orden")
    return alertas


def _inyectar_css() -> None:
    st.markdown(
        """
        <style>
        .agenda-hero {
            padding: 22px 24px;
            border-radius: 18px;
            background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 55%, #0f766e 100%);
            color: white;
            margin-bottom: 18px;
            box-shadow: 0 12px 28px rgba(15, 23, 42, .16);
        }
        .agenda-hero-title {font-size: 1.75rem; font-weight: 800; line-height: 1.15;}
        .agenda-hero-subtitle {opacity: .9; margin-top: 7px; font-size: .98rem;}
        .agenda-card {
            border: 1px solid rgba(148,163,184,.35);
            border-radius: 14px;
            padding: 12px 13px;
            margin: 7px 0;
            background: rgba(255,255,255,.78);
            box-shadow: 0 4px 13px rgba(15,23,42,.06);
        }
        .agenda-card-title {font-weight: 800; color:#0f172a; font-size:1rem;}
        .agenda-card-detail {color:#475569; font-size:.88rem; line-height:1.55; margin-top:5px;}
        .agenda-badge {
            display:inline-block; padding:4px 9px; border-radius:999px;
            font-size:.74rem; font-weight:800; white-space:nowrap;
        }
        .agenda-kanban-head {
            color:white; border-radius:12px; padding:9px 8px; text-align:center;
            font-weight:800; margin-bottom:8px; min-height:42px;
            display:flex; align-items:center; justify-content:center;
        }
        .agenda-ready {
            border-left:5px solid #16a34a; background:#f0fdf4;
            border-radius:12px; padding:10px 12px; margin:8px 0;
        }
        .agenda-warning {
            border-left:5px solid #f59e0b; background:#fffbeb;
            border-radius:12px; padding:10px 12px; margin:8px 0;
        }
        .agenda-danger {
            border-left:5px solid #dc2626; background:#fef2f2;
            border-radius:12px; padding:10px 12px; margin:8px 0;
        }
        div[data-testid="stMetric"] {
            border:1px solid rgba(148,163,184,.25);
            border-radius:14px; padding:10px 12px;
            background:rgba(255,255,255,.72);
        }
        @media (max-width: 900px) {
            .agenda-hero-title {font-size:1.35rem;}
            .agenda-hero {padding:18px;}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _badge(estado: str) -> str:
    estilos = {
        "Consulta": ("#dbeafe", "#1d4ed8"),
        "Pendiente de autorización": ("#ffedd5", "#c2410c"),
        "Autorizado": ("#dcfce7", "#15803d"),
        "Programado": ("#e0e7ff", "#4338ca"),
        "Confirmado": ("#ccfbf1", "#0f766e"),
        "En quirófano": ("#fee2e2", "#b91c1c"),
        "Realizado": ("#dcfce7", "#166534"),
        "Reprogramado": ("#f3e8ff", "#7e22ce"),
        "Suspendido": ("#f1f5f9", "#334155"),
        "Cancelado": ("#e2e8f0", "#475569"),
    }
    fondo, letra = estilos.get(estado, ("#f1f5f9", "#334155"))
    return (
        f'<span class="agenda-badge" style="background:{fondo};color:{letra};">'
        f"{_escapar(estado)}</span>"
    )


def _opciones(df: pd.DataFrame, columna: str) -> list[str]:
    if columna not in df.columns or df.empty:
        return []
    valores = {_texto(v) for v in df[columna]}
    valores.discard("")
    return sorted(valores)


def _render_filtros(df: pd.DataFrame) -> pd.DataFrame:
    hoy = date.today()
    fechas_validas = df["_fecha_dt"].dropna() if "_fecha_dt" in df.columns else pd.Series(dtype="datetime64[ns]")
    inicio_default = hoy - timedelta(days=30)
    fin_default = hoy + timedelta(days=180)

    st.markdown("### 🔎 Filtros operativos")
    fila1 = st.columns([1.7, 1, 1, 1])
    buscar = fila1[0].text_input(
        "Buscar paciente, médico o procedimiento",
        placeholder="Ej.: María Pérez, histeroscopia...",
        key="agenda_ultra_buscar",
    )
    estado_sel = fila1[1].selectbox(
        "Estado",
        ["Todos"] + ESTADOS,
        key="agenda_ultra_estado",
    )
    procedimiento_sel = fila1[2].selectbox(
        "Procedimiento",
        ["Todos"] + _opciones(df, "procedimiento"),
        key="agenda_ultra_procedimiento",
    )
    medico_sel = fila1[3].selectbox(
        "Médico",
        ["Todos"] + _opciones(df, "medico"),
        key="agenda_ultra_medico",
    )

    fila2 = st.columns([1, 1, 1, 1, .7])
    desde = fila2[0].date_input(
        "Desde",
        value=inicio_default,
        key="agenda_ultra_desde",
    )
    hasta = fila2[1].date_input(
        "Hasta",
        value=fin_default,
        key="agenda_ultra_hasta",
    )
    sala_sel = fila2[2].selectbox(
        "Sala",
        ["Todas"] + _opciones(df, "sala"),
        key="agenda_ultra_sala",
    )
    obra_sel = fila2[3].selectbox(
        "Obra social",
        ["Todas"] + _opciones(df, "obra_social"),
        key="agenda_ultra_obra",
    )
    with fila2[4]:
        st.write("")
        st.write("")
        if st.button("↺ Limpiar", use_container_width=True, key="agenda_ultra_limpiar"):
            for clave in [
                "agenda_ultra_buscar",
                "agenda_ultra_estado",
                "agenda_ultra_procedimiento",
                "agenda_ultra_medico",
                "agenda_ultra_desde",
                "agenda_ultra_hasta",
                "agenda_ultra_sala",
                "agenda_ultra_obra",
            ]:
                st.session_state.pop(clave, None)
            st.rerun()

    if desde > hasta:
        desde, hasta = hasta, desde

    filtrado = df.copy()
    filtrado = filtrado[
        filtrado["_fecha_dt"].isna()
        | (
            (filtrado["_fecha_dt"].dt.date >= desde)
            & (filtrado["_fecha_dt"].dt.date <= hasta)
        )
    ]

    if buscar.strip():
        patron = buscar.strip().lower()
        mascara = pd.Series(False, index=filtrado.index)
        for columna in ["paciente", "medico", "procedimiento", "obra_social", "sala"]:
            mascara |= filtrado[columna].astype(str).str.lower().str.contains(
                patron, regex=False, na=False
            )
        filtrado = filtrado[mascara]
    if estado_sel != "Todos":
        filtrado = filtrado[filtrado["estado"] == estado_sel]
    if procedimiento_sel != "Todos":
        filtrado = filtrado[filtrado["procedimiento"] == procedimiento_sel]
    if medico_sel != "Todos":
        filtrado = filtrado[filtrado["medico"] == medico_sel]
    if sala_sel != "Todas":
        filtrado = filtrado[filtrado["sala"] == sala_sel]
    if obra_sel != "Todas":
        filtrado = filtrado[filtrado["obra_social"] == obra_sel]

    st.caption(
        f"Mostrando {len(filtrado)} registro(s). "
        f"Período: {desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}."
    )
    return filtrado


def _render_metricas(df_total: pd.DataFrame, df_filtrado: pd.DataFrame) -> None:
    hoy = pd.Timestamp.today().normalize()
    proximos_7 = hoy + pd.Timedelta(days=7)

    no_cancelados = df_total[~df_total["estado"].isin(ESTADOS_NO_OCUPAN)]
    cirugias_hoy = int(
        (
            no_cancelados["_fecha_dt"].notna()
            & no_cancelados["_fecha_dt"].dt.normalize().eq(hoy)
        ).sum()
    )
    pendientes_aut = int(
        (
            df_total["estado"].eq("Pendiente de autorización")
            & df_total["_fecha_dt"].notna()
            & (df_total["_fecha_dt"].dt.normalize() >= hoy)
        ).sum()
    )
    programados = int(
        (
            df_filtrado["estado"].isin({"Programado", "Confirmado", "Reprogramado"})
            & df_filtrado["_fecha_dt"].notna()
            & (df_filtrado["_fecha_dt"].dt.normalize() >= hoy)
        ).sum()
    )
    en_quirofano = int(df_total["estado"].eq("En quirófano").sum())
    realizados_mes = int(
        (
            df_total["estado"].eq("Realizado")
            & df_total["_fecha_dt"].notna()
            & df_total["_fecha_dt"].dt.month.eq(hoy.month)
            & df_total["_fecha_dt"].dt.year.eq(hoy.year)
        ).sum()
    )
    semana = df_total[
        df_total["_fecha_dt"].notna()
        & (df_total["_fecha_dt"].dt.normalize() >= hoy)
        & (df_total["_fecha_dt"].dt.normalize() <= proximos_7)
        & ~df_total["estado"].isin(ESTADOS_NO_OCUPAN)
    ]
    preparacion = float(semana["_preparacion_pct"].mean()) if not semana.empty else 0.0

    metricas = st.columns(6)
    metricas[0].metric("🏥 Cirugías de hoy", cirugias_hoy)
    metricas[1].metric("⏳ Pendientes autorización", pendientes_aut)
    metricas[2].metric("📅 Programados filtrados", programados)
    metricas[3].metric("🔴 En quirófano", en_quirofano)
    metricas[4].metric("✅ Realizados este mes", realizados_mes)
    metricas[5].metric("🛡️ Preparación próximos 7 días", f"{preparacion:.0f}%")


def _datos_formulario(
    fecha_seleccionada: date,
    hora_inicio: time,
    duracion: int,
    sala: str,
    paciente: str,
    procedimiento: str,
    medico: str,
    estado: str,
    anestesista: str,
    tipo_anestesia: str,
    obra_social: str,
    numero_afiliado: str,
    autorizacion: str,
    telefono: str,
    prioridad: str,
    ayuno: bool,
    consentimiento: bool,
    prequirurgico: bool,
    material: bool,
    observaciones: str,
) -> dict[str, Any]:
    inicio_txt = hora_inicio.strftime("%H:%M")
    fin_txt = _fin_desde_inicio(inicio_txt, int(duracion))
    return {
        "fecha": fecha_seleccionada.strftime("%Y-%m-%d"),
        "hora_inicio": inicio_txt,
        "hora_fin": fin_txt,
        "duracion_min": int(duracion),
        "sala": sala.strip(),
        "paciente": paciente.strip(),
        "procedimiento": procedimiento.strip(),
        "medico": medico.strip(),
        "estado": estado,
        "anestesista": anestesista.strip(),
        "tipo_anestesia": tipo_anestesia.strip(),
        "obra_social": obra_social.strip(),
        "numero_afiliado": numero_afiliado.strip(),
        "autorizacion": autorizacion.strip(),
        "telefono": telefono.strip(),
        "prioridad": prioridad,
        "ayuno_confirmado": "Sí" if ayuno else "No",
        "consentimiento_firmado": "Sí" if consentimiento else "No",
        "prequirurgico_completo": "Sí" if prequirurgico else "No",
        "material_confirmado": "Sí" if material else "No",
        "observaciones": observaciones.strip(),
    }


def _validar_datos(datos: dict[str, Any]) -> list[str]:
    errores = []
    for campo, nombre in [
        ("fecha", "fecha"),
        ("paciente", "paciente"),
        ("procedimiento", "procedimiento"),
        ("medico", "médico"),
        ("sala", "sala"),
        ("hora_inicio", "hora de inicio"),
    ]:
        if not _texto(datos.get(campo)):
            errores.append(nombre)
    return errores


def _render_tarjeta(fila: pd.Series, compacta: bool = False) -> None:
    paciente = fila["paciente"] or "Paciente sin nombre"
    procedimiento = fila["procedimiento"] or "Procedimiento sin definir"
    medico = fila["medico"] or "Médico sin definir"
    sala = fila["sala"] or "Sala sin definir"
    fecha_txt = fila["_fecha_dt"].strftime("%d/%m/%Y") if pd.notna(fila["_fecha_dt"]) else "Sin fecha"
    checklist = int(fila["_checklist_ok"])
    detalle_extra = "" if compacta else (
        f"<br>🛡️ Checklist {checklist}/4 · "
        f"🏦 {_escapar(fila['obra_social'] or 'Sin cobertura')}"
    )
    st.markdown(
        f"""
        <div class="agenda-card">
            <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start;">
                <div>
                    <div class="agenda-card-title">👤 {_escapar(paciente)}</div>
                    <div class="agenda-card-detail">
                        📅 {_escapar(fecha_txt)} · 🕒 {_escapar(fila['hora_inicio'])}–{_escapar(fila['hora_fin'])}<br>
                        🩺 {_escapar(procedimiento)}<br>
                        👨‍⚕️ {_escapar(medico)} · 🚪 {_escapar(sala)}{detalle_extra}
                    </div>
                </div>
                <div>{_badge(fila['estado'])}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_operacion_hoy(
    df: pd.DataFrame,
    original: pd.DataFrame,
    mapa: dict[str, str],
    guardar_callback: Optional[GuardarCallback],
) -> None:
    controles = st.columns([1.2, 1, 1, 1])
    dia = controles[0].date_input("Día operativo", value=date.today(), key="agenda_ultra_dia_operativo")
    agenda = df[
        df["_fecha_dt"].notna()
        & df["_fecha_dt"].dt.date.eq(dia)
        & ~df["estado"].isin(ESTADOS_NO_OCUPAN)
    ].sort_values(["hora_inicio", "sala", "paciente"])

    minutos = agenda["duracion_min"].sum() if not agenda.empty else 0
    salas = agenda["sala"].replace("", pd.NA).dropna().nunique() if not agenda.empty else 0
    preparados = int((agenda["_checklist_ok"] == 4).sum()) if not agenda.empty else 0
    controles[1].metric("Procedimientos", len(agenda))
    controles[2].metric("Tiempo previsto", f"{minutos / 60:.1f} h")
    controles[3].metric("Listos / salas", f"{preparados} / {salas}")

    if agenda.empty:
        st.info("No hay procedimientos activos para el día seleccionado.")
        return

    for _, fila in agenda.iterrows():
        _render_tarjeta(fila)
        posicion = int(fila["_row_position"])
        with st.expander(f"⚙️ Gestionar · {fila['paciente'] or 'Paciente sin nombre'}", expanded=False):
            with st.form(f"agenda_hoy_gestion_{posicion}"):
                columnas = st.columns([1.2, 1, 1, 1])
                nuevo_estado = columnas[0].selectbox(
                    "Estado",
                    ESTADOS,
                    index=ESTADOS.index(fila["estado"]),
                )
                ayuno = columnas[1].checkbox("Ayuno", value=_booleano(fila["ayuno_confirmado"]))
                consentimiento = columnas[2].checkbox(
                    "Consentimiento", value=_booleano(fila["consentimiento_firmado"])
                )
                prequirurgico = columnas[3].checkbox(
                    "Prequirúrgico", value=_booleano(fila["prequirurgico_completo"])
                )
                columnas2 = st.columns([1, 1, 2])
                material = columnas2[0].checkbox(
                    "Material", value=_booleano(fila["material_confirmado"])
                )
                autorizacion = columnas2[1].text_input(
                    "Autorización", value=fila["autorizacion"]
                )
                observaciones = columnas2[2].text_input(
                    "Observación operativa", value=fila["observaciones"]
                )
                guardar = st.form_submit_button(
                    "💾 Guardar actualización", type="primary", use_container_width=True
                )

            if guardar:
                datos = {
                    "estado": nuevo_estado,
                    "ayuno_confirmado": "Sí" if ayuno else "No",
                    "consentimiento_firmado": "Sí" if consentimiento else "No",
                    "prequirurgico_completo": "Sí" if prequirurgico else "No",
                    "material_confirmado": "Sí" if material else "No",
                    "autorizacion": autorizacion.strip(),
                    "observaciones": observaciones.strip(),
                }
                actualizado = _actualizar_fila(original, mapa, posicion, datos)
                if _guardar(actualizado, mapa, guardar_callback, "Paciente actualizado correctamente."):
                    st.rerun()


def _render_kanban(
    df: pd.DataFrame,
    original: pd.DataFrame,
    mapa: dict[str, str],
    guardar_callback: Optional[GuardarCallback],
) -> None:
    st.markdown("### 🧭 Gestión visual del circuito quirúrgico")
    colores = {
        "Consulta": "#3b82f6",
        "Autorización": "#f59e0b",
        "Programación": "#4f46e5",
        "En quirófano": "#dc2626",
        "Finalizado": "#16a34a",
        "Suspendido": "#475569",
    }
    columnas = st.columns(len(GRUPOS_KANBAN))

    for columna_ui, (grupo, estados_grupo) in zip(columnas, GRUPOS_KANBAN.items()):
        with columna_ui:
            cantidad = int(df["estado"].isin(estados_grupo).sum())
            st.markdown(
                f'<div class="agenda-kanban-head" style="background:{colores[grupo]};">'
                f"{_escapar(grupo)} · {cantidad}</div>",
                unsafe_allow_html=True,
            )
            datos = df[df["estado"].isin(estados_grupo)].sort_values(
                ["_fecha_dt", "hora_inicio"], na_position="last"
            )
            if datos.empty:
                st.caption("Sin pacientes")
                continue

            for _, fila in datos.iterrows():
                _render_tarjeta(fila, compacta=True)
                posicion = int(fila["_row_position"])
                with st.expander("Mover / actualizar", expanded=False):
                    with st.form(f"agenda_kanban_{posicion}"):
                        nuevo_estado = st.selectbox(
                            "Nuevo estado",
                            ESTADOS,
                            index=ESTADOS.index(fila["estado"]),
                        )
                        nota = st.text_area(
                            "Observaciones",
                            value=fila["observaciones"],
                            height=75,
                        )
                        guardar = st.form_submit_button(
                            "Guardar", type="primary", use_container_width=True
                        )
                    if guardar:
                        actualizado = _actualizar_fila(
                            original,
                            mapa,
                            posicion,
                            {"estado": nuevo_estado, "observaciones": nota.strip()},
                        )
                        if _guardar(
                            actualizado,
                            mapa,
                            guardar_callback,
                            f"{fila['paciente'] or 'Paciente'} movido a {nuevo_estado}.",
                        ):
                            st.rerun()


def _render_calendario(df: pd.DataFrame) -> None:
    tab_dia, tab_semana, tab_mes, tab_anio = st.tabs(
        ["📆 Día", "🗓️ Semana", "📅 Mes", "📊 Año"]
    )

    with tab_dia:
        dia = st.date_input("Fecha", value=date.today(), key="agenda_ultra_cal_dia")
        datos = df[df["_fecha_dt"].notna() & df["_fecha_dt"].dt.date.eq(dia)].sort_values(
            ["hora_inicio", "sala"]
        )
        if datos.empty:
            st.info("No hay registros para esa fecha.")
        else:
            for _, fila in datos.iterrows():
                _render_tarjeta(fila)

    with tab_semana:
        referencia = st.date_input(
            "Semana que contiene", value=date.today(), key="agenda_ultra_cal_semana"
        )
        inicio = referencia - timedelta(days=referencia.weekday())
        fin = inicio + timedelta(days=6)
        semana = df[
            df["_fecha_dt"].notna()
            & (df["_fecha_dt"].dt.date >= inicio)
            & (df["_fecha_dt"].dt.date <= fin)
            & ~df["estado"].isin(ESTADOS_NO_OCUPAN)
        ].copy()
        st.caption(f"Del {inicio.strftime('%d/%m/%Y')} al {fin.strftime('%d/%m/%Y')}")
        timeline = semana.dropna(subset=["_inicio_dt", "_fin_dt"]).copy()
        if timeline.empty:
            st.info("No hay procedimientos programados en esa semana.")
        else:
            timeline["Paciente"] = timeline["paciente"].replace("", "Sin nombre")
            timeline["Sala"] = timeline["sala"].replace("", "Sin sala")
            timeline["Médico"] = timeline["medico"].replace("", "Sin médico")
            timeline["Procedimiento"] = timeline["procedimiento"].replace("", "Sin procedimiento")
            figura = px.timeline(
                timeline,
                x_start="_inicio_dt",
                x_end="_fin_dt",
                y="Sala",
                color="estado",
                hover_name="Paciente",
                hover_data=["Procedimiento", "Médico"],
                title="Ocupación semanal por sala",
            )
            figura.update_yaxes(autorange="reversed")
            figura.update_layout(height=max(420, 70 * timeline["Sala"].nunique()))
            st.plotly_chart(figura, use_container_width=True, key="agenda_ultra_timeline_semana")

    with tab_mes:
        referencia = st.date_input(
            "Mes a visualizar", value=date.today().replace(day=1), key="agenda_ultra_cal_mes"
        )
        anio, mes = referencia.year, referencia.month
        calendario = calendar.Calendar(firstweekday=0).monthdatescalendar(anio, mes)
        conteo = (
            df[df["_fecha_dt"].notna()]
            .assign(_dia=df["_fecha_dt"].dt.date)
            .groupby("_dia")
            .size()
            .to_dict()
        )
        st.markdown(f"#### {MESES[mes]} {anio}")
        encabezado = st.columns(7)
        for col, nombre in zip(encabezado, ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]):
            col.markdown(f"**{nombre}**")
        for semana in calendario:
            columnas = st.columns(7)
            for col, dia in zip(columnas, semana):
                cantidad = int(conteo.get(dia, 0))
                fuera = dia.month != mes
                texto = f"{dia.day}"
                if cantidad:
                    texto += f"  ·  **{cantidad}**"
                if fuera:
                    col.caption(texto)
                else:
                    col.markdown(
                        f"<div style='min-height:58px;padding:8px;border:1px solid #e2e8f0;"
                        f"border-radius:10px;background:#fff;'>{texto}</div>",
                        unsafe_allow_html=True,
                    )

    with tab_anio:
        anio = st.number_input(
            "Año", min_value=2020, max_value=2100, value=date.today().year, step=1,
            key="agenda_ultra_cal_anio",
        )
        datos = df[df["_fecha_dt"].notna() & df["_fecha_dt"].dt.year.eq(int(anio))].copy()
        if datos.empty:
            st.info("No hay datos para el año seleccionado.")
        else:
            datos["Mes"] = datos["_fecha_dt"].dt.month.map(MESES)
            datos["_mes_num"] = datos["_fecha_dt"].dt.month
            resumen = (
                datos.groupby(["_mes_num", "Mes"], as_index=False)
                .agg(Procedimientos=("paciente", "size"), Horas=("duracion_min", lambda s: s.sum() / 60))
                .sort_values("_mes_num")
            )
            figura = px.bar(
                resumen,
                x="Mes",
                y="Procedimientos",
                text_auto=True,
                title=f"Actividad quirúrgica {int(anio)}",
            )
            st.plotly_chart(figura, use_container_width=True, key="agenda_ultra_anual")
            st.dataframe(
                resumen[["Mes", "Procedimientos", "Horas"]],
                use_container_width=True,
                hide_index=True,
            )


def _render_alertas(df: pd.DataFrame) -> None:
    alertas = _alertas(df)
    conflictos = _conflictos(df)

    m1, m2, m3 = st.columns(3)
    altas = int(alertas["Severidad"].eq("🔴 Alta").sum()) if not alertas.empty else 0
    medias = int(alertas["Severidad"].eq("🟠 Media").sum()) if not alertas.empty else 0
    m1.metric("Alertas altas", altas)
    m2.metric("Alertas medias", medias)
    m3.metric("Conflictos de agenda", len(conflictos))

    if alertas.empty and conflictos.empty:
        st.success("✅ No se detectaron problemas operativos evidentes.")
        return

    if not conflictos.empty:
        st.markdown("### 🔴 Superposiciones detectadas")
        st.dataframe(
            conflictos.drop(columns=["_fila_a", "_fila_b"], errors="ignore"),
            use_container_width=True,
            hide_index=True,
        )

    if not alertas.empty:
        st.markdown("### 🚨 Alertas de seguridad y gestión")
        st.dataframe(alertas, use_container_width=True, hide_index=True, height=420)
        st.download_button(
            "⬇️ Descargar alertas",
            data=alertas.to_csv(index=False, sep=";", encoding="utf-8-sig"),
            file_name=f"alertas_agenda_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            use_container_width=True,
        )


def _render_nuevo(
    df: pd.DataFrame,
    original: pd.DataFrame,
    mapa: dict[str, str],
    guardar_callback: Optional[GuardarCallback],
) -> None:
    st.markdown("### ➕ Registrar nuevo paciente / procedimiento")
    st.caption("Los campos con * son obligatorios. El sistema controla superposición de sala y médico.")

    with st.form("agenda_ultra_nuevo_registro", clear_on_submit=False):
        c1, c2, c3 = st.columns(3)
        paciente = c1.text_input("Paciente *")
        fecha_sel = c2.date_input("Fecha *", value=date.today())
        hora_inicio = c3.time_input("Hora de inicio *", value=time(8, 0), step=300)

        c4, c5, c6 = st.columns(3)
        procedimiento = c4.text_input("Procedimiento *")
        medico = c5.text_input("Médico *")
        sala = c6.text_input("Sala *", value="Quirófano 1")

        c7, c8, c9 = st.columns(3)
        duracion = c7.number_input(
            "Duración estimada (min)", min_value=5, max_value=1440, value=60, step=5
        )
        estado_sel = c8.selectbox("Estado", ESTADOS, index=ESTADOS.index("Programado"))
        prioridad = c9.selectbox("Prioridad", ["Baja", "Normal", "Alta", "Urgente"], index=1)

        c10, c11, c12 = st.columns(3)
        anestesista = c10.text_input("Anestesista")
        tipo_anestesia = c11.text_input("Tipo de anestesia")
        telefono = c12.text_input("Teléfono")

        c13, c14, c15 = st.columns(3)
        obra_social = c13.text_input("Obra social / prepaga")
        numero_afiliado = c14.text_input("N.º afiliado")
        autorizacion = c15.text_input("N.º autorización")

        st.markdown("**Checklist prequirúrgico**")
        checks = st.columns(4)
        ayuno = checks[0].checkbox("Ayuno confirmado")
        consentimiento = checks[1].checkbox("Consentimiento firmado")
        prequirurgico = checks[2].checkbox("Prequirúrgico completo")
        material = checks[3].checkbox("Material confirmado")

        observaciones = st.text_area("Observaciones", height=90)
        permitir = st.checkbox(
            "Permitir guardar aunque exista una superposición confirmada",
            help="Usar solamente si la superposición es intencional o corresponde a salas/recursos distintos.",
        )
        enviar = st.form_submit_button(
            "💾 Guardar nuevo registro", type="primary", use_container_width=True
        )

    if enviar:
        datos = _datos_formulario(
            fecha_sel,
            hora_inicio,
            int(duracion),
            sala,
            paciente,
            procedimiento,
            medico,
            estado_sel,
            anestesista,
            tipo_anestesia,
            obra_social,
            numero_afiliado,
            autorizacion,
            telefono,
            prioridad,
            ayuno,
            consentimiento,
            prequirurgico,
            material,
            observaciones,
        )
        errores = _validar_datos(datos)
        if errores:
            st.error("Falta completar: " + ", ".join(errores) + ".")
            return

        superposiciones = _tiene_superposicion(df, datos)
        if superposiciones and not permitir:
            st.error("No se guardó porque existe una superposición:")
            for conflicto in superposiciones:
                st.write("• " + conflicto)
            st.info("Revisá el horario o marcá la casilla de autorización excepcional.")
            return

        nuevo = _agregar_fila(original, mapa, datos)
        if _guardar(nuevo, mapa, guardar_callback, "Nuevo procedimiento guardado correctamente."):
            st.rerun()


def _etiqueta_registro(fila: pd.Series) -> str:
    fecha_txt = fila["_fecha_dt"].strftime("%d/%m/%Y") if pd.notna(fila["_fecha_dt"]) else "Sin fecha"
    return (
        f"{fecha_txt} · {fila['hora_inicio'] or '--:--'} · "
        f"{fila['paciente'] or 'Sin nombre'} · {fila['procedimiento'] or 'Sin procedimiento'}"
    )


def _render_editar(
    df: pd.DataFrame,
    original: pd.DataFrame,
    mapa: dict[str, str],
    guardar_callback: Optional[GuardarCallback],
) -> None:
    st.markdown("### ✏️ Editar un registro")
    if df.empty:
        st.info("No hay registros para editar con los filtros actuales.")
        return

    opciones = df.sort_values(["_fecha_dt", "hora_inicio"], na_position="last")["_row_position"].tolist()
    seleccion = st.selectbox(
        "Elegí el registro",
        opciones,
        format_func=lambda pos: _etiqueta_registro(df[df["_row_position"] == pos].iloc[0]),
        key="agenda_ultra_editar_selector",
    )
    fila = df[df["_row_position"] == seleccion].iloc[0]
    posicion = int(seleccion)

    fecha_inicial = fila["_fecha_dt"].date() if pd.notna(fila["_fecha_dt"]) else date.today()
    with st.form(f"agenda_ultra_editar_{posicion}"):
        c1, c2, c3 = st.columns(3)
        paciente = c1.text_input("Paciente *", value=fila["paciente"])
        fecha_sel = c2.date_input("Fecha *", value=fecha_inicial)
        hora_inicio = c3.time_input(
            "Hora de inicio *", value=_hora_objeto(fila["hora_inicio"]), step=300
        )

        c4, c5, c6 = st.columns(3)
        procedimiento = c4.text_input("Procedimiento *", value=fila["procedimiento"])
        medico = c5.text_input("Médico *", value=fila["medico"])
        sala = c6.text_input("Sala *", value=fila["sala"])

        c7, c8, c9 = st.columns(3)
        duracion = c7.number_input(
            "Duración estimada (min)",
            min_value=5,
            max_value=1440,
            value=_numero_entero(fila["duracion_min"]),
            step=5,
        )
        estado_sel = c8.selectbox(
            "Estado", ESTADOS, index=ESTADOS.index(fila["estado"])
        )
        prioridades = ["Baja", "Normal", "Alta", "Urgente"]
        prioridad_actual = fila["prioridad"] if fila["prioridad"] in prioridades else "Normal"
        prioridad = c9.selectbox(
            "Prioridad", prioridades, index=prioridades.index(prioridad_actual)
        )

        c10, c11, c12 = st.columns(3)
        anestesista = c10.text_input("Anestesista", value=fila["anestesista"])
        tipo_anestesia = c11.text_input("Tipo de anestesia", value=fila["tipo_anestesia"])
        telefono = c12.text_input("Teléfono", value=fila["telefono"])

        c13, c14, c15 = st.columns(3)
        obra_social = c13.text_input("Obra social / prepaga", value=fila["obra_social"])
        numero_afiliado = c14.text_input("N.º afiliado", value=fila["numero_afiliado"])
        autorizacion = c15.text_input("N.º autorización", value=fila["autorizacion"])

        st.markdown("**Checklist prequirúrgico**")
        checks = st.columns(4)
        ayuno = checks[0].checkbox(
            "Ayuno confirmado", value=_booleano(fila["ayuno_confirmado"])
        )
        consentimiento = checks[1].checkbox(
            "Consentimiento firmado", value=_booleano(fila["consentimiento_firmado"])
        )
        prequirurgico = checks[2].checkbox(
            "Prequirúrgico completo", value=_booleano(fila["prequirurgico_completo"])
        )
        material = checks[3].checkbox(
            "Material confirmado", value=_booleano(fila["material_confirmado"])
        )

        observaciones = st.text_area("Observaciones", value=fila["observaciones"], height=100)
        permitir = st.checkbox("Permitir superposición excepcional")
        guardar = st.form_submit_button(
            "💾 Guardar cambios", type="primary", use_container_width=True
        )

    if guardar:
        datos = _datos_formulario(
            fecha_sel,
            hora_inicio,
            int(duracion),
            sala,
            paciente,
            procedimiento,
            medico,
            estado_sel,
            anestesista,
            tipo_anestesia,
            obra_social,
            numero_afiliado,
            autorizacion,
            telefono,
            prioridad,
            ayuno,
            consentimiento,
            prequirurgico,
            material,
            observaciones,
        )
        errores = _validar_datos(datos)
        if errores:
            st.error("Falta completar: " + ", ".join(errores) + ".")
            return
        superposiciones = _tiene_superposicion(df, datos, excluir_posicion=posicion)
        if superposiciones and not permitir:
            st.error("No se guardó porque existe una superposición:")
            for conflicto in superposiciones:
                st.write("• " + conflicto)
            return

        actualizado = _actualizar_fila(original, mapa, posicion, datos)
        if _guardar(actualizado, mapa, guardar_callback, "Registro actualizado correctamente."):
            st.rerun()

    st.divider()
    with st.expander("🗑️ Eliminar este registro", expanded=False):
        st.warning("Esta acción elimina la fila completa de Google Sheets.")
        confirmar = st.checkbox(
            f"Confirmo eliminar a {fila['paciente'] or 'este registro'}",
            key=f"agenda_ultra_confirmar_borrar_{posicion}",
        )
        if st.button(
            "Eliminar definitivamente",
            type="secondary",
            disabled=not confirmar,
            key=f"agenda_ultra_borrar_{posicion}",
        ):
            eliminado = original.drop(index=posicion).reset_index(drop=True)
            if _guardar(eliminado, mapa, guardar_callback, "Registro eliminado correctamente."):
                st.rerun()


def _render_listado(df: pd.DataFrame) -> None:
    columnas = [
        "_fecha_dt",
        "hora_inicio",
        "hora_fin",
        "paciente",
        "procedimiento",
        "medico",
        "anestesista",
        "tipo_anestesia",
        "sala",
        "estado",
        "prioridad",
        "obra_social",
        "numero_afiliado",
        "autorizacion",
        "telefono",
        "_preparacion_pct",
        "observaciones",
    ]
    listado = df[columnas].copy() if not df.empty else pd.DataFrame(columns=columnas)
    listado["_fecha_dt"] = listado["_fecha_dt"].apply(
        lambda v: v.strftime("%d/%m/%Y") if pd.notna(v) else ""
    )
    listado.columns = [
        "Fecha",
        "Inicio",
        "Fin",
        "Paciente",
        "Procedimiento",
        "Médico",
        "Anestesista",
        "Tipo anestesia",
        "Sala",
        "Estado",
        "Prioridad",
        "Obra social",
        "N.º afiliado",
        "Autorización",
        "Teléfono",
        "Preparación %",
        "Observaciones",
    ]

    st.dataframe(listado, use_container_width=True, hide_index=True, height=520)
    st.download_button(
        "⬇️ Exportar agenda filtrada",
        data=listado.to_csv(index=False, sep=";", encoding="utf-8-sig"),
        file_name=f"agenda_quirofano_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    if df.empty:
        return
    st.divider()
    g1, g2 = st.columns(2)
    por_medico = (
        df.assign(Médico=df["medico"].replace("", "Sin médico"))
        .groupby("Médico")
        .size()
        .reset_index(name="Procedimientos")
        .sort_values("Procedimientos", ascending=False)
        .head(12)
    )
    por_procedimiento = (
        df.assign(Procedimiento=df["procedimiento"].replace("", "Sin procedimiento"))
        .groupby("Procedimiento")
        .size()
        .reset_index(name="Cantidad")
        .sort_values("Cantidad", ascending=False)
        .head(12)
    )
    g1.plotly_chart(
        px.bar(
            por_medico,
            x="Médico",
            y="Procedimientos",
            title="Procedimientos por médico",
            text_auto=True,
        ),
        use_container_width=True,
        key="agenda_ultra_grafico_medico",
    )
    g2.plotly_chart(
        px.bar(
            por_procedimiento,
            x="Procedimiento",
            y="Cantidad",
            title="Procedimientos más frecuentes",
            text_auto=True,
        ),
        use_container_width=True,
        key="agenda_ultra_grafico_procedimiento",
    )


def render_agenda_quirofano_ultra_pro(
    df_original: Optional[pd.DataFrame],
    guardar_callback: Optional[GuardarCallback] = None,
) -> pd.DataFrame:
    """
    Renderiza y gestiona la Agenda Quirófano ULTRA PRO.

    Parámetros
    ----------
    df_original:
        DataFrame completo leído desde la hoja ``agenda_quirofano``.
        Debe enviarse el DataFrame completo, no uno previamente filtrado.
    guardar_callback:
        Función que recibe el DataFrame completo actualizado y lo guarda.
        Ejemplo: ``lambda df: save_table("agenda_quirofano", df)``.

    Retorna
    -------
    pandas.DataFrame
        Copia del DataFrame original preparado para guardar.
    """
    _inyectar_css()
    original, preparado, mapa = _preparar_dataframe(df_original)
    datos = preparado[~preparado["_es_vacio"]].copy()

    st.markdown(
        """
        <div class="agenda-hero">
            <div class="agenda-hero-title">🏥 Agenda Quirófano · Centro de Operaciones</div>
            <div class="agenda-hero-subtitle">
                Programación quirúrgica, autorizaciones, seguridad preoperatoria,
                ocupación de salas, alertas y seguimiento integral de pacientes.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not callable(guardar_callback):
        st.warning(
            "⚠️ Modo lectura: la agenda puede verse, pero los cambios no se guardarán "
            "hasta conectar el callback de Google Sheets."
        )

    if datos.empty:
        st.info(
            "La hoja Agenda Quirófano todavía no tiene registros válidos. "
            "Podés crear el primero desde la pestaña «Cargar»."
        )

    filtrado = _render_filtros(datos)
    _render_metricas(datos, filtrado)

    st.divider()
    (
        tab_operacion,
        tab_flujo,
        tab_calendario,
        tab_alertas,
        tab_nuevo,
        tab_editar,
        tab_listado,
    ) = st.tabs(
        [
            "🏥 Operación",
            "🧭 Flujo de pacientes",
            "📆 Calendario",
            "🚨 Alertas",
            "➕ Cargar",
            "✏️ Editar",
            "📋 Listado",
        ]
    )

    with tab_operacion:
        _render_operacion_hoy(datos, original, mapa, guardar_callback)

    with tab_flujo:
        _render_kanban(filtrado, original, mapa, guardar_callback)

    with tab_calendario:
        _render_calendario(filtrado)

    with tab_alertas:
        _render_alertas(datos)

    with tab_nuevo:
        _render_nuevo(datos, original, mapa, guardar_callback)

    with tab_editar:
        _render_editar(filtrado, original, mapa, guardar_callback)

    with tab_listado:
        _render_listado(filtrado)

    return original
