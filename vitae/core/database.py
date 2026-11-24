# =========================================================
# BASE DE DATOS - SOLO GOOGLE SHEETS
# =========================================================
import json
import re
import threading
import uuid
import gspread
try:
    from gspread.utils import rowcol_to_a1
except Exception:  # respaldo: misma función, por si cambia la librería
    def rowcol_to_a1(row: int, col: int) -> str:
        letras = ""
        while col:
            col, resto = divmod(col - 1, 26)
            letras = chr(65 + resto) + letras
        return f"{letras}{row}"
import pandas as pd
import streamlit as st
from typing import Any, Dict, List
from google.oauth2.service_account import Credentials
from vitae.core.config import SHEET_ID
from vitae.core.tablas import MODULES
@st.cache_resource

def get_gs_client():

    scopes = [

        "https://www.googleapis.com/auth/spreadsheets",

        "https://www.googleapis.com/auth/drive",

    ]

    raw_json = st.secrets.get("GOOGLE_SERVICE_ACCOUNT_JSON", None)

    if raw_json is None:

        raise RuntimeError("Falta GOOGLE_SERVICE_ACCOUNT_JSON en Secrets.")

    raw_json = str(raw_json).strip()

    # Limpia si viene con espacios raros

    raw_json = raw_json.replace("\r\n", "\n").replace("\r", "\n")

    try:

        service_account_info = json.loads(raw_json)

    except json.JSONDecodeError:

        import re

        # Arregla SOLO el private_key, no todo el JSON

        def fix_private_key(match):

            key = match.group(1)

            key = key.replace("\n", "\\n")

            return f'"private_key": "{key}"'

        fixed_json = re.sub(

            r'"private_key"\s*:\s*"([\s\S]*?)"',

            fix_private_key,

            raw_json,

            count=1

        )

        service_account_info = json.loads(fixed_json)

    credentials = Credentials.from_service_account_info(

        service_account_info,

        scopes=scopes,

    )

    return gspread.authorize(credentials)


@st.cache_resource
def get_spreadsheet():
    if not SHEET_ID:
        raise RuntimeError("Falta GOOGLE_SHEET_ID en Secrets.")
    return get_gs_client().open_by_key(SHEET_ID)


# ---------------------------------------------------------
# CACHE DE LECTURA (velocidad)
# ---------------------------------------------------------
# Antes cada tabla se leía por separado y cada lectura hacía 2 llamadas a
# Google (buscar la pestaña + leer valores). Con ~22 tablas eso eran ~44
# llamadas seguidas al abrir el dashboard, y además chocaba con el límite
# de Google de ~60 lecturas por minuto.
#
# Ahora:
#   - Las pestañas se buscan una sola vez y se recuerdan.
#   - Cuando falta una tabla en memoria, se leen JUNTAS todas las tablas
#     de MODULES en UNA sola llamada (values_batch_get).
#   - El cache se invalida exactamente igual que antes: cuando vence (5 min)
#     o cuando cualquier parte de la app llama a st.cache_data.clear()
#     (al guardar, al tocar "Actualizar", etc.). Así los datos nunca quedan
#     más viejos que con el sistema anterior.

_CACHE_TTL_SEGUNDOS = 300
_cache_lock = threading.RLock()
_cache_tablas: Dict[str, pd.DataFrame] = {}
_cache_generacion: str | None = None
_cache_hojas: Dict[str, Any] = {}

# Tablas que se precargan juntas. "convenios" se excluye porque esa hoja
# maestra no se usa (el módulo lee convenios_vm / convenios_vmr).
_TABLAS_PRECARGA: List[str] = list(dict.fromkeys(
    cfg["table"] for cfg in MODULES.values()
    if cfg.get("table") and cfg.get("table") != "convenios"
))


@st.cache_data(ttl=_CACHE_TTL_SEGUNDOS, show_spinner=False)
def _generacion_cache() -> str:
    """Token que cambia cada vez que se ejecuta st.cache_data.clear() o vence el TTL."""
    return uuid.uuid4().hex


def _hojas_por_nombre(refrescar: bool = False) -> Dict[str, Any]:
    global _cache_hojas
    with _cache_lock:
        if refrescar or not _cache_hojas:
            _cache_hojas = {ws.title: ws for ws in get_spreadsheet().worksheets()}
        return _cache_hojas


def get_or_create_worksheet(table: str):
    hojas = _hojas_por_nombre()
    if table not in hojas:
        hojas = _hojas_por_nombre(refrescar=True)
    if table in hojas:
        return hojas[table]
    sh = get_spreadsheet()
    try:
        ws = sh.worksheet(table)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=table, rows=1000, cols=40)
    with _cache_lock:
        _cache_hojas[table] = ws
    return ws


def _valores_a_df(values: List[List[Any]]) -> pd.DataFrame:
    """Convierte la matriz de valores de una pestaña en DataFrame (misma lógica de siempre)."""
    if not values or len(values) < 2:
        return pd.DataFrame()
    # Igual que gspread.get_all_values(): todas las filas al mismo ancho.
    ancho = max(len(fila) for fila in values)
    values = [list(fila) + [""] * (ancho - len(fila)) for fila in values]
    headers = [str(h).strip() for h in values[0]]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)
    df = df.dropna(how="all")
    df = df.loc[:, [c for c in df.columns if str(c).strip() != ""]]
    if df.empty:
        return pd.DataFrame()
    if len(df.columns) == 1 and str(df.iloc[0, 0]).strip().lower() == "sin datos":
        return pd.DataFrame()
    return df


def _rango_hoja(nombre: str) -> str:
    return "'" + nombre.replace("'", "''") + "'"


def _leer_tabla_individual(table: str) -> pd.DataFrame:
    ws = get_or_create_worksheet(table)
    return _valores_a_df(ws.get_all_values())


def _leer_tablas_en_lote(tablas: List[str]) -> Dict[str, pd.DataFrame]:
    """Lee varias pestañas existentes en una sola llamada a la API."""
    if not tablas:
        return {}
    respuesta = get_spreadsheet().values_batch_get([_rango_hoja(t) for t in tablas])
    rangos = respuesta.get("valueRanges", [])
    if len(rangos) != len(tablas):
        raise RuntimeError("Respuesta incompleta de Google Sheets en lectura por lote.")
    return {t: _valores_a_df(r.get("values", [])) for t, r in zip(tablas, rangos)}


def get_df(table: str) -> pd.DataFrame:
    global _cache_generacion
    generacion = _generacion_cache()
    with _cache_lock:
        if _cache_generacion != generacion:
            _cache_tablas.clear()
            _cache_generacion = generacion
        if table in _cache_tablas:
            return _cache_tablas[table].copy()

        hojas = _hojas_por_nombre()
        if table not in hojas:
            # Pestaña nueva o creada recién: se busca/crea como antes.
            df = _leer_tabla_individual(table)
            _cache_tablas[table] = df
            return df.copy()

        pendientes = [table] + [
            t for t in _TABLAS_PRECARGA
            if t != table and t in hojas and t not in _cache_tablas
        ]
        try:
            _cache_tablas.update(_leer_tablas_en_lote(pendientes))
        except Exception:
            # Si la lectura por lote falla, se vuelve al método anterior
            # solo para la tabla pedida (y sus errores se propagan igual).
            _cache_tablas[table] = _leer_tabla_individual(table)
        return _cache_tablas[table].copy()


def _limpiar_cache_tablas() -> None:
    """Descarta las tablas en memoria (la próxima lectura va a Google Sheets)
    sin borrar el resto de los cálculos cacheados de la app."""
    with _cache_lock:
        _cache_tablas.clear()


# Compatibilidad: el editor de Facturación llama a get_df.clear() para forzar
# una lectura fresca antes de editar.
get_df.clear = _limpiar_cache_tablas  # type: ignore[attr-defined]


class ErrorGuardadoSheets(Exception):
    """Error de Google Sheets explicado en castellano (se muestra tal cual)."""


def _email_cuenta_servicio() -> str:
    try:
        raw = str(st.secrets.get("GOOGLE_SERVICE_ACCOUNT_JSON", "") or "")
        encontrado = re.search(r'"client_email"\s*:\s*"([^"]+)"', raw)
        return encontrado.group(1) if encontrado else ""
    except Exception:
        return ""


def detalle_error_google(exc: Exception) -> str:
    """Texto exacto que devuelve Google (código, estado, motivo)."""
    try:
        err = exc.response.json().get("error", {})
        motivos = [d.get("reason") for d in err.get("details", []) if isinstance(d, dict) and d.get("reason")]
        texto = f"{err.get('code', '')} {err.get('status', '')}: {err.get('message', '')}".strip()
        return texto + (f" (motivo: {', '.join(motivos)})" if motivos else "")
    except Exception:
        return str(exc)


def _explicar_error_sheets(exc: Exception, table: str, paso: str = "") -> Exception:
    estado = getattr(getattr(exc, "response", None), "status_code", None)
    if estado == 403:
        cuenta = _email_cuenta_servicio()
        quien = f"la cuenta {cuenta}" if cuenta else "la cuenta de servicio de la app"
        try:
            detectado = explicar_diagnostico(diagnosticar_permisos(table), table)
        except Exception:
            detectado = ""
        return ErrorGuardadoSheets(
            f"Google Sheets no dio permiso para guardar en «{table}»"
            + (f" (falló al {paso})" if paso else "") + ". "
            + (f"Diagnóstico: {detectado} " if detectado else "")
            + f"Respuesta de Google: {detalle_error_google(exc)}. "
            + f"Para solucionarlo: abrí la planilla → Compartir → agregá {quien} como Editor; si ya figura, "
            f"revisá Datos → Proteger hojas y rangos. No se modificó nada de la planilla."
        )
    if estado == 429:
        return ErrorGuardadoSheets(
            "Google Sheets recibió demasiadas operaciones seguidas. Esperá un minuto y volvé a guardar. "
            "No se modificó nada de la planilla."
        )
    return exc


def diagnosticar_permisos(table: str | None = None) -> Dict[str, Any]:
    """Pregunta a Google qué puede hacer la app con la planilla (solo lectura).

    Devuelve: cuenta de servicio, nombre del archivo, si Google la considera
    editora (Drive «canEdit»), su rol en el archivo y qué pestañas tienen
    rangos protegidos (y si la cuenta figura entre sus editores).
    """
    info: Dict[str, Any] = {
        "cuenta": _email_cuenta_servicio(), "archivo": None, "puede_editar": None, "rol": None,
        "puede_modificar_contenido": None, "puede_desbloquear": None, "bloqueado": None, "tipo_archivo": None,
        "pestanas_protegidas": {}, "notas": [],
    }
    try:
        sh = get_spreadsheet()
        info["archivo"] = getattr(sh, "title", None)
    except Exception as exc:
        info["notas"].append(f"No se pudo abrir la planilla: {exc}")
        return info
    # Protecciones por pestaña
    try:
        meta = sh.fetch_sheet_metadata({
            "fields": "sheets(properties(title),protectedRanges(description,warningOnly,range,editors(users,groups,domainUsersCanEdit)))"
        })
        cuenta = (info["cuenta"] or "").lower()
        for hoja in meta.get("sheets", []):
            titulo = hoja.get("properties", {}).get("title", "")
            bloqueos = [p for p in hoja.get("protectedRanges", []) if not p.get("warningOnly")]
            if bloqueos:
                editores = sorted({u.lower() for p in bloqueos for u in p.get("editors", {}).get("users", [])})
                info["pestanas_protegidas"][titulo] = {
                    "rangos": len(bloqueos),
                    "cuenta_puede": bool(cuenta) and cuenta in editores,
                    "editores": editores,
                }
    except Exception as exc:
        info["notas"].append(f"No se pudieron leer las protecciones: {exc}")
    # Capacidad de edición según Google Drive
    try:
        cliente = sh.client
        http = getattr(cliente, "http_client", None) or cliente
        url = f"https://www.googleapis.com/drive/v3/files/{SHEET_ID}"
        respuesta = http.request("get", url, params={
            "fields": "name,mimeType,capabilities(canEdit,canModifyContent,canModifyContentRestriction),"
                      "contentRestrictions(readOnly,reason,type,restrictionTime,restrictingUser(emailAddress,displayName)),"
                      "permissions(emailAddress,role,type)",
            "supportsAllDrives": "true",
        })
        datos = respuesta.json()
        capacidades = datos.get("capabilities", {})
        info["puede_editar"] = capacidades.get("canEdit")
        info["puede_modificar_contenido"] = capacidades.get("canModifyContent")
        info["puede_desbloquear"] = capacidades.get("canModifyContentRestriction")
        info["tipo_archivo"] = datos.get("mimeType")
        bloqueos = [r for r in datos.get("contentRestrictions", []) or [] if r.get("readOnly")]
        if bloqueos:
            r = bloqueos[0]
            quien = (r.get("restrictingUser") or {}).get("displayName") or (r.get("restrictingUser") or {}).get("emailAddress") or ""
            info["bloqueado"] = {"motivo": r.get("reason") or "", "por": quien,
                                 "desde": str(r.get("restrictionTime") or "")[:10], "tipo": r.get("type") or ""}
        cuenta = (info["cuenta"] or "").lower()
        for permiso in datos.get("permissions", []) or []:
            if str(permiso.get("emailAddress", "")).lower() == cuenta:
                info["rol"] = permiso.get("role")
        if info["rol"] is None and datos.get("permissions") is not None:
            info["notas"].append("La cuenta de la app no figura con su email entre los permisos del archivo "
                                 "(puede estar leyendo por el enlace público o por un grupo).")
    except Exception as exc:
        info["notas"].append(f"No se pudo consultar el permiso en Google Drive: {exc}")
    return info


def probar_escritura(table: str) -> tuple[bool, str]:
    """Reescribe la celda A1 de la pestaña con su MISMO valor (o fórmula).

    No cambia ningún dato; sirve para saber si Google acepta escrituras en esa
    pestaña y, si no, con qué motivo exacto.
    """
    try:
        sh = get_spreadsheet()
        rango = _rango_hoja(table) + "!A1"
        respuesta = sh.values_get(rango, params={"valueRenderOption": "FORMULA"})
        valores = respuesta.get("values") or [[""]]
        valor = valores[0][0] if valores and valores[0] else ""
        sh.values_update(rango, params={"valueInputOption": "USER_ENTERED"}, body={"values": [[valor]]})
        return True, "Google aceptó la escritura (A1 quedó con el mismo valor)."
    except gspread.exceptions.APIError as exc:
        return False, detalle_error_google(exc)
    except Exception as exc:  # pestaña inexistente u otro problema
        return False, str(exc)


def explicar_diagnostico(info: Dict[str, Any], table: str | None = None) -> str:
    """Resumen en castellano de diagnosticar_permisos()."""
    partes = []
    bloqueo = info.get("bloqueado")
    if bloqueo:
        detalle = " ".join(x for x in (f"por {bloqueo['por']}" if bloqueo.get("por") else "",
                                       f"el {bloqueo['desde']}" if bloqueo.get("desde") else "",
                                       f"(motivo: {bloqueo['motivo']})" if bloqueo.get("motivo") else "") if x)
        partes.append(
            f"EL ARCHIVO ESTÁ BLOQUEADO en Google Drive {detalle}. Mientras esté bloqueado nadie puede modificar su "
            "contenido, aunque sea Editor. Para desbloquearlo: en Google Drive, clic derecho sobre la planilla → "
            "Información del archivo → Desbloquear (si el bloqueo vino de una aprobación, hay que cancelarla)."
        )
    elif info.get("puede_modificar_contenido") is False:
        partes.append("Google indica que la cuenta NO puede modificar el contenido del archivo aunque figure como "
                      "Editor (puede haber un bloqueo o una regla de la organización de Google Workspace).")
    tipo = str(info.get("tipo_archivo") or "")
    if tipo and tipo != "application/vnd.google-apps.spreadsheet":
        partes.append(f"El archivo no es una planilla de Google Sheets sino «{tipo}» (por ejemplo, un Excel "
                      "subido a Drive). Hay que convertirlo: Archivo → Guardar como Hojas de cálculo de Google.")
    if info.get("puede_editar") is False:
        rol = {"reader": "Lector", "commenter": "Comentarista"}.get(str(info.get("rol")), info.get("rol"))
        partes.append(
            f"Google confirma que la cuenta {info.get('cuenta') or 'de la app'} NO puede editar el archivo "
            f"«{info.get('archivo') or ''}»" + (f" (su rol es {rol})" if rol else "") + ". Tiene que figurar como Editor."
        )
    protegidas = info.get("pestanas_protegidas", {})
    if table and table in protegidas and not protegidas[table]["cuenta_puede"]:
        partes.append(
            f"La pestaña «{table}» tiene {protegidas[table]['rangos']} protección(es) y la cuenta de la app no está "
            "entre quienes pueden editarla (Datos → Proteger hojas y rangos)."
        )
    if not partes and info.get("puede_editar") is True:
        partes.append("Google indica que la cuenta puede editar el archivo y no detecta bloqueos ni protecciones. "
                      "Usá Configuración → Sistema → «Probar escritura» para ver qué pestaña rechaza la escritura.")
    return " ".join(partes)


def sync_df_to_sheet(table: str, df: pd.DataFrame) -> int:
    """Reemplaza el contenido de la pestaña por ``df``.

    Orden seguro: primero se ESCRIBE el contenido nuevo y recién después se
    limpia lo que sobra. Antes se borraba la pestaña y luego se escribía: si
    la escritura fallaba, la pestaña quedaba vacía.
    """
    df = df.copy()
    df = df.where(pd.notnull(df), "")
    df = df.astype(str)
    data = [df.columns.tolist()] + df.values.tolist() if not df.empty else [["Sin datos"]]
    filas = len(data)
    columnas = max(len(fila) for fila in data)
    paso = "leer la pestaña"
    try:
        # Lectura fresca de la pestaña para conocer su tamaño real.
        try:
            ws = get_spreadsheet().worksheet(table)
        except gspread.WorksheetNotFound:
            paso = "crear la pestaña"
            ws = get_or_create_worksheet(table)
        with _cache_lock:
            _cache_hojas[table] = ws
        if ws.row_count < filas or ws.col_count < columnas:
            paso = f"agrandar la pestaña a {max(ws.row_count, filas)} filas × {max(ws.col_count, columnas)} columnas"
            ws.resize(rows=max(ws.row_count, filas), cols=max(ws.col_count, columnas))
        paso = f"escribir {filas} filas × {columnas} columnas"
        ws.update(data)
        paso = "limpiar las filas sobrantes"
        sobrantes = []
        if ws.row_count > filas:
            sobrantes.append(f"{rowcol_to_a1(filas + 1, 1)}:{rowcol_to_a1(ws.row_count, ws.col_count)}")
        if ws.col_count > columnas:
            sobrantes.append(f"{rowcol_to_a1(1, columnas + 1)}:{rowcol_to_a1(filas, ws.col_count)}")
        if sobrantes:
            ws.batch_clear(sobrantes)
    except gspread.exceptions.APIError as exc:
        raise _explicar_error_sheets(exc, table, paso) from exc
    st.cache_data.clear()
    return 0 if df.empty else len(df)


def insert_row(table: str, data: Dict[str, Any]) -> None:
    df = get_df(table)
    new_row = pd.DataFrame([data])
    if df.empty:
        final_df = new_row
    else:
        final_df = pd.concat([df, new_row], ignore_index=True)
    sync_df_to_sheet(table, final_df)
def bulk_insert_rows(table: str, rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    df = get_df(table)
    new_df = pd.DataFrame(rows)
    if df.empty:
        final_df = new_df
    else:
        final_df = pd.concat([df, new_df], ignore_index=True)
    return sync_df_to_sheet(table, final_df)
def replace_table_rows(table: str, rows: List[Dict[str, Any]]) -> int:
    df = pd.DataFrame(rows)
    return sync_df_to_sheet(table, df)
def update_row(table: str, row_id: int, data: Dict[str, Any]) -> None:
    df = get_df(table)
    if df.empty:
        return
    if "id" in df.columns:
        df.loc[df["id"].astype(str) == str(row_id), list(data.keys())] = list(data.values())
    sync_df_to_sheet(table, df)
def delete_row(table: str, row_id: int) -> None:
    df = get_df(table)
    if df.empty:
        return
    if "id" in df.columns:
        df = df[df["id"].astype(str) != str(row_id)]
    sync_df_to_sheet(table, df)
def sync_all_to_sheets() -> Dict[str, int]:
    return {}
def restore_all_from_sheets() -> Dict[str, int]:
    return {}
def restore_table_from_sheet(table: str) -> int:
    df = get_df(table)
    return len(df)