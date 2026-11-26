"""Luz edita cualquier dato cargado: alta, modificación y baja en cualquier módulo.

Igual que el resto de las acciones de Luz (vitae/ia/acciones.py):
- Las herramientas sólo PROPONEN: arman una tarjeta con «antes → después».
- Estas acciones piden además la CLAVE de Luz (vitae/ia/clave.py) para confirmarse.
- Al confirmar se vuelve a leer la pestaña, se ubica cada fila por su huella (si alguien la cambió
  mientras tanto, no se toca) y se escribe respetando el formato de cada columna (fechas, importes).
- Nunca toca usuarios_vitae ni las pestañas internas de Luz, y sólo módulos que el usuario puede abrir.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Callable

import pandas as pd

from vitae.ia.acciones import (_escribir_fecha, _escribir_importe, _escribir_mes, _huella, _json, _norm,
                               _texto)

MAX_FILAS = 100            # una sola acción no toca más de esto
_PROHIBIDAS = {"usuarios_vitae", "luz_memoria", "luz_chat", "ia_conocimiento", "pacientes", "pacientes_accesos"}
TIPOS = ("datos_alta", "datos_cambio", "datos_baja")

_PISTAS_FECHA = ("fecha", "vencim", "vto", "nacim", "ingreso", "egreso", "desde", "hasta")
_PISTAS_IMPORTE = ("importe", "monto", "total", "saldo", "entrada", "salida", "precio", "valor", "costo",
                   "pagado", "debe", "haber", "honorario", "cobrado", "neto", "iva", "deuda")
_PISTAS_DESCRIBIR = ("paciente", "afiliado", "nombre", "proveedor", "concepto", "descripcion", "detalle",
                     "medico", "obra_social", "comprobante", "factura", "fecha", "mes", "importe", "monto", "total")


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def leer_campos(texto: Any) -> dict[str, str]:
    """«{"paciente": "Ana", "importe": "1000"}» o «paciente: Ana; importe = 1000» → dict."""
    if isinstance(texto, dict):
        return {str(k).strip(): _texto(v) for k, v in texto.items() if str(k).strip()}
    t = str(texto or "").strip()
    if not t:
        return {}
    if t.startswith("{"):
        try:
            return {str(k).strip(): _texto(v) for k, v in json.loads(t).items() if str(k).strip()}
        except Exception:
            pass
    out: dict[str, str] = {}
    for parte in re.split(r"[;\n]", t):
        m = re.match(r"\s*([^:=]+?)\s*[:=]\s*(.*)$", parte)
        if m:
            out[m[1].strip()] = m[2].strip().strip("\"'")
    return out


def _col(df: pd.DataFrame, nombre: str) -> str | None:
    objetivo = _norm(nombre)
    if not objetivo:
        return None
    for c in df.columns:
        if _norm(c) == objetivo:
            return c
    parecidas = [c for c in df.columns if objetivo in _norm(c) or (_norm(c) and _norm(c) in objetivo)]
    return parecidas[0] if len(parecidas) == 1 else None


def tipo_columna(nombre: str, serie: pd.Series) -> str:
    n = _norm(nombre)
    if n in {"mes", "periodo", "mes_factura", "mes_prestacion"}:
        return "mes"
    if any(p in n for p in _PISTAS_FECHA):
        return "fecha"
    if any(p in n.split("_") or n.startswith(p) or n.endswith(p) for p in _PISTAS_IMPORTE):
        return "importe"
    return "texto"


def valor_para(col: str, serie: pd.Series, valor: Any, hoy: date | None = None) -> tuple[str, str] | str:
    """(lo que se escribe, lo que se muestra) o un texto de error."""
    t = _texto(valor)
    if t == "" or _norm(t) in {"vacio", "borrar", "nada"}:
        return "", "vacío"
    tipo = tipo_columna(col, serie)
    if tipo == "fecha":
        escrito, f = _escribir_fecha(t, serie, hoy)
        return (escrito, f"{f.day}/{f.month}/{f.year}") if escrito else (t, t)
    if tipo == "mes":
        escrito, f = _escribir_mes(t, serie, hoy)
        return (escrito, escrito) if escrito else (t, t)
    if tipo == "importe":
        escrito, n = _escribir_importe(t, serie)
        if n is None:
            return (t, t)            # p. ej. «USD» o «-»: se respeta lo que dijeron
        return escrito, escrito
    return t, t


def describir(fila: pd.Series, maximo: int = 4) -> str:
    partes: list[str] = []
    usadas: set[str] = set()
    for pista in _PISTAS_DESCRIBIR:
        for c in fila.index:
            if c in usadas or pista not in _norm(c):
                continue
            v = _texto(fila[c])
            if v:
                partes.append(v[:40])
                usadas.add(c)
                break
        if len(partes) >= maximo:
            break
    if len(partes) < 2:
        partes += [_texto(v)[:40] for c, v in fila.items() if c not in usadas and _texto(v)][: maximo - len(partes)]
    return " · ".join(partes) or "(fila vacía)"


def buscar_filas(raw: pd.DataFrame, buscar: Any) -> pd.DataFrame:
    """buscar: filtros JSON como en consultar ([{"columna","op","valor"}]), «columna: valor; …»
    o texto libre (todas las palabras tienen que aparecer en la fila)."""
    from vitae.ia.motor import _aplicar_filtros

    t = str(buscar or "").strip()
    if not t:
        raise ValueError("Decime cómo encuentro el registro (paciente, fecha, importe, N°…).")
    if t.startswith("[") or t.startswith('{"columna'):
        return _aplicar_filtros(raw, t)
    campos = leer_campos(t) if re.search(r"[:=]", t) else {}
    if campos and all(_col(raw, k) for k in campos):
        out = raw
        for k, v in campos.items():
            c = _col(raw, k)
            col_n = out[c].map(_norm)
            v_n = _norm(v)
            exacto = col_n.eq(v_n)
            out = out[exacto] if exacto.any() else out[col_n.str.contains(re.escape(v_n), na=False)]
        return out
    palabras = [p for p in _norm(t).split("_") if p]
    pajar = raw.astype(str).apply(lambda c: c.map(_norm)).agg("_".join, axis=1)
    mask = pd.Series(True, index=raw.index)
    for p in palabras:
        mask &= pajar.str.contains(re.escape(p), na=False)
    return raw[mask]


def _candidatas(x: pd.DataFrame) -> list[dict[str, Any]]:
    return [{"fila": int(i) + 2, "registro": describir(r)} for i, r in x.head(8).iterrows()]


def _base(modulo: str, nombre_visible: str, tabla: str, tipo: str, titulo: str, icono: str) -> dict[str, Any]:
    return {"tipo": tipo, "modulo": modulo, "modulo_visible": nombre_visible, "tabla": tabla, "icono": icono,
            "titulo": titulo, "requiere_clave": True, "estado": "pendiente", "avisos": []}


def _tabla_ok(tabla: str) -> bool:
    return bool(tabla) and tabla not in _PROHIBIDAS


# ---------------------------------------------------------------------------
# Propuestas
# ---------------------------------------------------------------------------
def alta_datos(raw: pd.DataFrame, modulo: str, tabla: str, campos: Any, *, nombre_visible: str = "",
               hoy: date | None = None) -> dict[str, Any]:
    nombre_visible = nombre_visible or modulo
    if not _tabla_ok(tabla):
        return {"ok": False, "error": "En esa pestaña no puedo cargar."}
    raw = raw.reset_index(drop=True) if isinstance(raw, pd.DataFrame) else pd.DataFrame()
    pedidos = leer_campos(campos)
    if not pedidos:
        return {"ok": False, "error": "Decime qué datos cargo (columna: valor)."}
    if raw.columns.empty:
        return {"ok": False, "error": f"{nombre_visible} no tiene columnas todavía; cargalo desde el módulo."}
    valores: dict[str, str] = {}
    detalle: list[list[str]] = []
    no_existen: list[str] = []
    for k, v in pedidos.items():
        c = _col(raw, k)
        if c is None:
            no_existen.append(k)
            continue
        r = valor_para(c, raw[c], v, hoy)
        if isinstance(r, str):
            return {"ok": False, "error": r}
        valores[c], mostrado = r
        if valores[c]:
            detalle.append([str(c), mostrado])
    if no_existen:
        return {"ok": False, "error": f"No existen las columnas: {', '.join(no_existen)}.",
                "columnas": [str(c) for c in raw.columns]}
    if not any(valores.values()):
        return {"ok": False, "error": "La fila quedaría vacía."}
    col_id = next((c for c in raw.columns if _norm(c) == "id"), None)
    if col_id is not None and col_id not in valores:
        ids = pd.to_numeric(raw[col_id], errors="coerce")
        if len(raw) and ids.notna().mean() > 0.8:
            valores[col_id] = str(int(ids.max()) + 1)
        else:
            import uuid

            valores[col_id] = uuid.uuid4().hex[:10]
    avisos: list[str] = []
    if len(raw):
        iguales = pd.Series(True, index=raw.index)
        for c, v in valores.items():
            if c != col_id and v:
                iguales &= raw[c].map(_texto).map(_norm) == _norm(v)
        if iguales.any():
            avisos.append(f"Ya hay {int(iguales.sum())} fila(s) con estos mismos datos: ¿no está repetida?")
    a = _base(modulo, nombre_visible, tabla, "datos_alta", f"Cargar en {nombre_visible}", "➕")
    a.update({"valores": valores, "filas": [[k, v] for k, v in detalle], "cambios": [], "avisos": avisos,
              "resumen": f"nueva fila en {nombre_visible}: " + ", ".join(f"{k} {v}" for k, v in detalle[:4])})
    return {"ok": True, "accion": a}


def cambio_datos(raw: pd.DataFrame, modulo: str, tabla: str, buscar: Any, cambios: Any, *,
                 aplicar_a_todas: bool = False, nombre_visible: str = "", hoy: date | None = None) -> dict[str, Any]:
    nombre_visible = nombre_visible or modulo
    if not _tabla_ok(tabla):
        return {"ok": False, "error": "Esa pestaña no se puede modificar."}
    raw = raw.reset_index(drop=True) if isinstance(raw, pd.DataFrame) else pd.DataFrame()
    pedidos = leer_campos(cambios)
    if not pedidos:
        return {"ok": False, "error": "No me dijiste qué cambiar (columna: valor nuevo)."}
    try:
        x = buscar_filas(raw, buscar)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if x.empty:
        return {"ok": False, "error": "No encontré ese registro."}
    if len(x) > 1 and not aplicar_a_todas:
        return {"ok": False, "varias": int(len(x)), "candidatas": _candidatas(x),
                "pista": "Preguntá cuál, o usá aplicar_a_todas=true si lo pidió para todas."}
    if len(x) > MAX_FILAS:
        return {"ok": False, "error": f"Son {len(x)} filas: de a {MAX_FILAS} como mucho, por seguridad."}
    valores: dict[str, str] = {}
    detalle: list[list[str]] = []
    for k, v in pedidos.items():
        c = _col(raw, k)
        if c is None:
            return {"ok": False, "error": f"La columna «{k}» no existe.", "columnas": [str(c) for c in raw.columns]}
        r = valor_para(c, raw[c], v, hoy)
        if isinstance(r, str):
            return {"ok": False, "error": r}
        valores[c], mostrado = r
        antes = {_texto(a) for a in x[c]}
        detalle.append([str(c), next(iter(antes)) if len(antes) == 1 else "(varios)", mostrado])
    if all(_texto(x[c].iloc[0]) == v for c, v in valores.items()) and len(x) == 1:
        return {"ok": False, "error": "Ya tiene esos valores: no hay nada que cambiar."}
    pos = [int(i) for i in x.index]
    filas = ([["Registro", describir(x.iloc[0])]] if len(x) == 1
             else [["Registros", f"{len(x)} filas"]] + [["", describir(r)] for _, r in x.head(3).iterrows()]
             + ([["", f"… y {len(x) - 3} más"]] if len(x) > 3 else []))
    a = _base(modulo, nombre_visible, tabla, "datos_cambio", f"Modificar en {nombre_visible}", "✏️")
    a.update({"pos": pos, "huellas": [_huella(raw.iloc[p]) for p in pos], "valores": valores, "filas": filas,
              "cambios": detalle,
              "resumen": (describir(x.iloc[0]) if len(x) == 1 else f"{len(x)} filas") + ": "
                         + ", ".join(f"{k} → {d}" for k, _, d in detalle)})
    return {"ok": True, "accion": a}


def baja_datos(raw: pd.DataFrame, modulo: str, tabla: str, buscar: Any, *, aplicar_a_todas: bool = False,
               nombre_visible: str = "") -> dict[str, Any]:
    nombre_visible = nombre_visible or modulo
    if not _tabla_ok(tabla):
        return {"ok": False, "error": "En esa pestaña no se puede borrar."}
    raw = raw.reset_index(drop=True) if isinstance(raw, pd.DataFrame) else pd.DataFrame()
    try:
        x = buscar_filas(raw, buscar)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if x.empty:
        return {"ok": False, "error": "No encontré ese registro."}
    if len(x) > 1 and not aplicar_a_todas:
        return {"ok": False, "varias": int(len(x)), "candidatas": _candidatas(x),
                "pista": "Preguntá cuál, o usá aplicar_a_todas=true si lo pidió para todas."}
    if len(x) > MAX_FILAS:
        return {"ok": False, "error": f"Son {len(x)} filas: de a {MAX_FILAS} como mucho, por seguridad."}
    pos = [int(i) for i in x.index]
    filas = [["Se borra" if len(x) == 1 else f"Se borran {len(x)}", describir(x.iloc[0])]] + \
            [["", describir(r)] for _, r in x.iloc[1:4].iterrows()] + \
            ([["", f"… y {len(x) - 4} más"]] if len(x) > 4 else [])
    a = _base(modulo, nombre_visible, tabla, "datos_baja", f"Borrar de {nombre_visible}", "🗑️")
    a.update({"pos": pos, "huellas": [_huella(raw.iloc[p]) for p in pos], "filas": filas, "cambios": [],
              "avisos": ["Se borra de la planilla. Si fue un error, pedime que la vuelva a cargar."],
              "resumen": (describir(x.iloc[0]) if len(x) == 1 else f"{len(x)} filas") + " (borrado)"})
    return {"ok": True, "accion": a}


# ---------------------------------------------------------------------------
# Ejecución (después de Confirmar + clave)
# ---------------------------------------------------------------------------
def _ubicar(raw: pd.DataFrame, pos: list[int], huellas: list[str]) -> list[int]:
    todas = [_huella(raw.iloc[i]) for i in range(len(raw))]
    out: list[int] = []
    for p, h in zip(pos, huellas):
        if p < len(todas) and todas[p] == h and p not in out:
            out.append(p)
            continue
        iguales = [i for i, x in enumerate(todas) if x == h and i not in out]
        if not iguales:
            raise RuntimeError("Ese registro cambió en la planilla mientras tanto. Pedímelo de nuevo.")
        out.append(iguales[0])
    return out


def ejecutar(accion: dict[str, Any], leer: Callable[[str], pd.DataFrame],
             guardar: Callable[[str, pd.DataFrame], Any]) -> str:
    tabla = accion.get("tabla", "")
    if not _tabla_ok(tabla):
        raise RuntimeError("Esa pestaña no se puede tocar.")
    raw = leer(tabla)
    raw = raw.copy().reset_index(drop=True) if isinstance(raw, pd.DataFrame) else pd.DataFrame()
    nombre = accion.get("modulo_visible") or accion.get("modulo")
    tipo = accion.get("tipo")
    if tipo == "datos_alta":
        faltan = [c for c in accion["valores"] if c not in raw.columns]
        if faltan:
            raise RuntimeError(f"La planilla ya no tiene las columnas {', '.join(faltan)}.")
        nueva = {c: accion["valores"].get(c, "") for c in raw.columns}
        nuevo = pd.concat([raw.astype(object), pd.DataFrame([nueva], columns=raw.columns).astype(object)],
                          ignore_index=True)
        guardar(tabla, nuevo)
        return f"Listo ✅ Cargué en {nombre}: {accion['resumen'].split(': ', 1)[-1]}."
    pos = _ubicar(raw, accion["pos"], accion["huellas"])
    if tipo == "datos_baja":
        guardar(tabla, raw.drop(index=pos).reset_index(drop=True))
        return f"Listo ✅ Borré de {nombre}: {accion['resumen'].replace(' (borrado)', '')}."
    if tipo == "datos_cambio":
        nuevo = raw.astype(object)
        for c, v in accion["valores"].items():
            if c not in nuevo.columns:
                raise RuntimeError(f"La planilla ya no tiene la columna {c}.")
            nuevo.loc[pos, c] = v
        guardar(tabla, nuevo)
        return f"Listo ✅ Modifiqué en {nombre}: {accion['resumen']}."
    raise RuntimeError("No sé hacer esa acción.")


# ---------------------------------------------------------------------------
# Herramientas para la IA
# ---------------------------------------------------------------------------
INSTRUCCIONES = """- CUALQUIER DATO (alta / modificación / baja en cualquier módulo que el usuario puede abrir):
  proponer_alta_registro, proponer_modificacion_registro y proponer_baja_registro. Usalas para todo lo que no
  tenga una herramienta propia (para Agenda de Quirófano, Facturación y Caja preferí las específicas, que
  conocen mejor esas planillas). Antes, si no sabés los nombres de las columnas, mirá listar_registros.
  campos / cambios: JSON {"columna": "valor"} o «columna: valor; columna: valor». Fechas como AAAA-MM-DD;
  se escriben solas en el formato de la planilla. buscar: «columna: valor; …», filtros JSON o texto libre.
  Si hay varias filas candidatas te las devuelve: preguntá cuál (o aplicar_a_todas=true si lo pidió así).
- CLAVE: estos cambios (y cualquier borrado) se confirman con la clave de Luz, que el usuario escribe en el
  casillero de la tarjeta. NUNCA la pidas escrita en el chat, nunca la repitas, nunca la recuerdes ni la
  guardes. Decí: «Te lo dejé listo: poné la clave y confirmá abajo 👇»."""


def herramientas(datos: Any, acciones: list[dict[str, Any]], hoy: date | None = None) -> list[Callable[..., str]]:
    try:
        from vitae.ia.motor import _MODULO_DE_EXTRA
    except Exception:  # pragma: no cover
        _MODULO_DE_EXTRA = {}

    def _modulo(modulo: str) -> tuple[str, str, str, pd.DataFrame] | str:
        nombre = datos.resolver(modulo)
        if nombre is None:
            return f"No existe el módulo «{modulo}» (o no tenés acceso). Mirá ver_modulos."
        tabla = datos.modulos().get(nombre, "")
        if not _tabla_ok(tabla):
            return f"{nombre} no se puede modificar desde el chat."
        try:
            # La planilla tal cual (no la vista de pantalla): así la huella coincide al guardar.
            raw = datos.crudo(nombre) if hasattr(datos, "crudo") else datos.df(nombre)
        except Exception as exc:
            return f"No pude leer {nombre}: {exc}"
        return _MODULO_DE_EXTRA.get(nombre, nombre), nombre, tabla, raw

    def _anotar(r: dict[str, Any]) -> str:
        if not r.get("ok"):
            return _json({"resultado": "no se armó", **{k: v for k, v in r.items() if k != "ok"}})
        acciones.append(r["accion"])
        del acciones[:-10]
        a = r["accion"]
        return _json({"resultado": "lista para confirmar con clave (todavía NO está guardada)",
                      "resumen": a["resumen"], "avisos": a.get("avisos")})

    def _log(texto: str) -> None:
        try:
            datos.consultas.append(texto)
        except Exception:
            pass

    def proponer_alta_registro(modulo: str, campos: str) -> str:
        """Prepara una fila NUEVA en cualquier módulo (NO la guarda: el usuario confirma con clave).
        campos: JSON {"columna": "valor"} o «columna: valor; columna: valor» con los nombres de columna de la planilla."""
        _log(f"proponer_alta_registro({modulo})")
        m = _modulo(modulo)
        if isinstance(m, str):
            return _json({"resultado": "no se armó", "error": m})
        permiso, nombre, tabla, raw = m
        return _anotar(alta_datos(raw, permiso, tabla, campos, nombre_visible=nombre, hoy=hoy))

    def proponer_modificacion_registro(modulo: str, buscar: str, cambios: str, aplicar_a_todas: bool = False) -> str:
        """Prepara la modificación de registros ya cargados en cualquier módulo (NO la guarda: se confirma con
        clave). buscar: «columna: valor; …», filtros JSON [{"columna","op","valor"}] o texto libre.
        cambios: JSON {"columna": "valor nuevo"} o «columna: valor». Valor vacío = dejar la celda vacía."""
        _log(f"proponer_modificacion_registro({modulo}; {buscar})")
        m = _modulo(modulo)
        if isinstance(m, str):
            return _json({"resultado": "no se armó", "error": m})
        permiso, nombre, tabla, raw = m
        return _anotar(cambio_datos(raw, permiso, tabla, buscar, cambios, aplicar_a_todas=aplicar_a_todas,
                                    nombre_visible=nombre, hoy=hoy))

    def proponer_baja_registro(modulo: str, buscar: str, aplicar_a_todas: bool = False) -> str:
        """Prepara el BORRADO de registros en cualquier módulo (NO lo hace: se confirma con clave).
        buscar: «columna: valor; …», filtros JSON o texto libre. Si hay varias, devuelve las candidatas."""
        _log(f"proponer_baja_registro({modulo}; {buscar})")
        m = _modulo(modulo)
        if isinstance(m, str):
            return _json({"resultado": "no se armó", "error": m})
        permiso, nombre, tabla, raw = m
        return _anotar(baja_datos(raw, permiso, tabla, buscar, aplicar_a_todas=aplicar_a_todas,
                                  nombre_visible=nombre))

    return [proponer_alta_registro, proponer_modificacion_registro, proponer_baja_registro]
