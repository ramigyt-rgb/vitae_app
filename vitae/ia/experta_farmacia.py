"""Luz, asistente de Farmacia: stock interno (Farmacia) y stock de Quirófano.

Consultas (para quien puede abrir Farmacia): existencias de cada artículo en los dos stocks, mínimos, lotes,
vencimientos, ubicaciones, alertas (sin stock, bajo el mínimo, vencidos o por vencer) y movimientos.

Acciones (sólo cuando lo pide el usuario ``rchar``; siempre con confirmación en el chat):
- un movimiento: ingreso / compra, consumo / salida, ajuste positivo o negativo y transferencia al otro stock
  (igual que el formulario del módulo: actualiza el stock y deja el movimiento en el historial);
- alta de un artículo nuevo o cambios de un artículo (mínimos, lote, vencimiento, ubicación, proveedor, costo…).
  Si cambian la cantidad en stock, se registra como ajuste (queda en el historial).

Los movimientos nunca devuelven datos de pacientes (paciente, documento, historia clínica).
"""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable

import pandas as pd

MODULO = "Farmacia"
TABLA_STOCK = "farmacia"
TABLA_MOVIMIENTOS = "farmacia_movimientos"
MODULO_MOVIMIENTOS = "Farmacia · movimientos"
EDITORES = {"rchar"}
SECTORES = {"quirofano": "Quirófano", "farmacia": "Farmacia"}
TIPOS = ["Ingreso / compra", "Consumo / salida", "Ajuste positivo", "Ajuste negativo", "Transferencia al otro stock"]
_SUMAN = {"Ingreso / compra", "Ajuste positivo"}
_SIN_PACIENTES = {"paciente", "documento", "historia_clinica", "dni", "afiliado"}

_ALIAS = {
    "id": "id_articulo", "producto": "articulo", "insumo": "articulo", "medicamento": "articulo", "nombre": "articulo",
    "stock_qx": "stock_quirofano", "stock_quirofano_actual": "stock_quirofano", "minimo_qx": "stock_minimo_quirofano",
    "stock_minimo_qx": "stock_minimo_quirofano", "stock_farm": "stock_farmacia", "minimo_farmacia": "stock_minimo_farmacia",
    "precio": "costo_unitario", "costo": "costo_unitario", "frio": "requiere_frio", "controlado": "medicamento_controlado",
}
CAMPOS_ARTICULO = ["codigo", "principio_activo", "categoria", "presentacion", "unidad", "proveedor", "costo_unitario",
                   "requiere_frio", "medicamento_controlado", "activo", "stock_minimo_quirofano", "ubicacion_quirofano",
                   "lote_quirofano", "vencimiento_quirofano", "stock_minimo_farmacia", "ubicacion_farmacia",
                   "lote_farmacia", "vencimiento_farmacia", "observaciones"]
_CAMPOS_MOV = ["id_movimiento", "id_provision", "fecha", "hora", "sector", "tipo", "id_articulo", "articulo", "cantidad",
               "stock_anterior", "stock_nuevo", "sector_destino", "stock_destino_anterior", "stock_destino_nuevo", "lote",
               "vencimiento", "paciente", "documento", "historia_clinica", "procedimiento", "cirujano", "anestesista",
               "responsable", "motivo", "observaciones"]


def _norm(v: Any) -> str:
    t = unicodedata.normalize("NFKD", str(v if v is not None else "").strip().lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


def _txt(v: Any) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    t = str(v).strip()
    return "" if t.lower() in {"nan", "none", "nat"} else t


def _num(v: Any) -> float:
    t = _txt(v).replace("$", "").replace(" ", "")
    if not t:
        return 0.0
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return 0.0


def _cant(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _fecha(v: Any) -> pd.Timestamp:
    t = _txt(v)
    if not t:
        return pd.NaT
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return pd.Timestamp(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", t)
    if m:
        anio = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        try:
            return pd.Timestamp(anio, int(m[2]), int(m[1]))
        except ValueError:
            return pd.NaT
    return pd.NaT


def sector_de(texto: Any) -> str | None:
    t = _norm(texto)
    if not t:
        return None
    if "quir" in t or t in {"qx", "q"}:
        return "quirofano"
    if "farm" in t or "intern" in t or t in {"f", "deposito"}:
        return "farmacia"
    return None


def tipo_de(texto: Any) -> str | None:
    t = _norm(texto)
    if not t:
        return None
    if "transf" in t or "pase" in t or "pasar" in t or "mover" in t:
        return "Transferencia al otro stock"
    if "ajuste" in t and ("neg" in t or "menos" in t or "baja" in t or "rest" in t):
        return "Ajuste negativo"
    if "ajuste" in t:
        return "Ajuste positivo"
    if any(w in t for w in ("ingres", "compra", "entrada", "recib", "llego", "repos")):
        return "Ingreso / compra"
    if any(w in t for w in ("consum", "salida", "uso", "usad", "gast", "dispens", "egreso")):
        return "Consumo / salida"
    return None


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------
def stock(raw: pd.DataFrame | None) -> tuple[pd.DataFrame, dict[str, str]]:
    """(stock con columnas estándar, estándar → columna original de la planilla)."""
    x = raw.copy() if isinstance(raw, pd.DataFrame) else pd.DataFrame()
    if len(x.columns) == 1 and _norm(x.columns[0]) == "sin_datos":
        x = pd.DataFrame()
    normas = {c: _norm(c) for c in x.columns}
    presentes = set(normas.values())
    original: dict[str, str] = {}
    nuevas: dict[Any, str] = {}
    for c, n in normas.items():
        alias = _ALIAS.get(n)
        if alias and alias not in presentes:
            n = alias
        if n in original:
            continue  # columna repetida: se deja como está
        original[n] = c
        nuevas[c] = n
    x = x.rename(columns=nuevas)
    for c in ["id_articulo", "articulo", "stock_quirofano", "stock_farmacia"] + CAMPOS_ARTICULO + ["ultima_actualizacion"]:
        if c not in x.columns:
            x[c] = ""
    return x.astype(object).reset_index(drop=True), original


def _estado(fila: pd.Series, sector: str) -> str:
    s, m = _num(fila.get(f"stock_{sector}")), _num(fila.get(f"stock_minimo_{sector}"))
    if s <= 0:
        return "sin stock"
    if m > 0 and s <= m:
        return "bajo el mínimo"
    return "normal"


def _vence(fila: pd.Series, sector: str, hoy: pd.Timestamp) -> tuple[str, int | None]:
    f = _fecha(fila.get(f"vencimiento_{sector}"))
    if pd.isna(f):
        return "", None
    return f.strftime("%d/%m/%Y"), int((f - hoy).days)


def ficha(fila: pd.Series, hoy: pd.Timestamp) -> dict[str, Any]:
    out: dict[str, Any] = {"articulo": _txt(fila["articulo"]), "codigo": _txt(fila.get("codigo")),
                           "categoria": _txt(fila.get("categoria")), "presentacion": _txt(fila.get("presentacion")),
                           "unidad": _txt(fila.get("unidad"))}
    for sec, nombre in SECTORES.items():
        v, d = _vence(fila, sec, hoy)
        out[nombre] = {"stock": _num(fila.get(f"stock_{sec}")), "minimo": _num(fila.get(f"stock_minimo_{sec}")),
                       "estado": _estado(fila, sec), "lote": _txt(fila.get(f"lote_{sec}")), "vence": v,
                       "dias_para_vencer": d, "ubicacion": _txt(fila.get(f"ubicacion_{sec}"))}
    if _txt(fila.get("proveedor")):
        out["proveedor"] = _txt(fila.get("proveedor"))
    if _num(fila.get("costo_unitario")):
        out["costo_unitario"] = _num(fila.get("costo_unitario"))
    return out


def buscar(x: pd.DataFrame, texto: str) -> pd.DataFrame:
    """Artículos que coinciden (nombre exacto primero; si no, los que contienen todas las palabras)."""
    t = _norm(texto)
    x = x[x["articulo"].map(_txt) != ""]
    if not t:
        return x
    nombres = x["articulo"].map(_norm)
    exacto = x[nombres == t]
    if len(exacto):
        return exacto
    cod = x[x["codigo"].map(_norm) == t]
    if len(cod):
        return cod
    palabras = [p for p in t.split("_") if p]
    otros = (x["principio_activo"].map(_norm) + "_" + x["codigo"].map(_norm))
    m = nombres.map(lambda n: all(p in n for p in palabras)) | otros.map(lambda n: all(p in n for p in palabras))
    return x[m]


def alertas(x: pd.DataFrame, hoy: pd.Timestamp, dias: int = 60) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"sin_stock": [], "bajo_minimo": [], "vencidos": [], "por_vencer": []}
    activos = x[x["activo"].map(lambda v: _norm(v) not in {"false", "0", "no"}) & (x["articulo"].map(_txt) != "")]
    for _, f in activos.iterrows():
        for sec, nombre in SECTORES.items():
            base = {"articulo": _txt(f["articulo"]), "sector": nombre, "stock": _num(f.get(f"stock_{sec}"))}
            e = _estado(f, sec)
            if e == "sin stock" and (_num(f.get(f"stock_minimo_{sec}")) > 0 or _txt(f.get(f"ubicacion_{sec}"))):
                out["sin_stock"].append(base)
            elif e == "bajo el mínimo":
                out["bajo_minimo"].append({**base, "minimo": _num(f.get(f"stock_minimo_{sec}"))})
            v, d = _vence(f, sec, hoy)
            if d is not None and base["stock"] > 0:
                if d < 0:
                    out["vencidos"].append({**base, "vencio": v, "lote": _txt(f.get(f"lote_{sec}"))})
                elif d <= dias:
                    out["por_vencer"].append({**base, "vence": v, "dias": d, "lote": _txt(f.get(f"lote_{sec}"))})
    out["por_vencer"].sort(key=lambda r: r["dias"])
    return out


def movimientos(raw: pd.DataFrame | None, *, articulo: str = "", sector: str = "", tipo: str = "", dias: int = 30,
                hoy: pd.Timestamp | None = None, maximo: int = 40) -> list[dict[str, Any]]:
    hoy = hoy if hoy is not None else pd.Timestamp.today().normalize()
    m = raw.copy() if isinstance(raw, pd.DataFrame) else pd.DataFrame()
    if m.empty:
        return []
    m = m.rename(columns={c: _norm(c) for c in m.columns})
    for c in ("fecha", "hora", "sector", "tipo", "articulo", "cantidad", "stock_anterior", "stock_nuevo",
              "sector_destino", "responsable", "motivo", "lote", "vencimiento", "procedimiento"):
        if c not in m.columns:
            m[c] = ""
    m["_f"] = m["fecha"].map(_fecha)
    if dias:
        m = m[m["_f"].isna() | (m["_f"] >= hoy - timedelta(days=int(dias)))]
    if articulo:
        palabras = [p for p in _norm(articulo).split("_") if p]
        m = m[m["articulo"].map(_norm).map(lambda n: all(p in n for p in palabras))]
    s = sector_de(sector) if sector else None
    if s:
        m = m[m["sector"].map(sector_de) == s]
    if tipo:
        t = tipo_de(tipo) or tipo
        m = m[m["tipo"].map(_norm).str.contains(_norm(t)[:6], na=False)]
    m = m.sort_values(["_f", "hora"], ascending=False, na_position="last", kind="mergesort").head(maximo)
    cols = [c for c in ("fecha", "hora", "sector", "tipo", "articulo", "cantidad", "stock_anterior", "stock_nuevo",
                        "sector_destino", "lote", "vencimiento", "procedimiento", "responsable", "motivo")
            if c not in _SIN_PACIENTES]
    return [{c: _txt(r[c]) for c in cols if _txt(r[c])} for _, r in m.iterrows()]


# ---------------------------------------------------------------------------
# Acciones (proponen; se ejecutan al confirmar)
# ---------------------------------------------------------------------------
def _uno(x: pd.DataFrame, articulo: str) -> tuple[int | None, dict[str, Any] | None]:
    c = buscar(x, articulo)
    if len(c) == 1:
        return int(c.index[0]), None
    if c.empty:
        return None, {"ok": False, "error": f"No encontré «{articulo}» en Farmacia.",
                      "sugerencias": list(buscar(x, _norm(articulo).split("_")[0])["articulo"].head(8))}
    return None, {"ok": False, "error": "Hay varios artículos con ese nombre: decime cuál.",
                  "candidatos": list(c["articulo"].head(10))}


def movimiento_datos(raw_stock: pd.DataFrame | None, *, articulo: str, sector: str, tipo: str, cantidad: Any,
                     motivo: str = "", lote: str = "", vencimiento: str = "", responsable: str = "",
                     procedimiento: str = "") -> dict[str, Any]:
    x, _ = stock(raw_stock)
    sec = sector_de(sector)
    if sec is None:
        return {"ok": False, "faltan": ["sector"], "error": "¿Stock de Quirófano o de Farmacia (interno)?"}
    tp = tipo_de(tipo) or (tipo if tipo in TIPOS else None)
    if tp is None:
        return {"ok": False, "faltan": ["tipo"], "error": "¿Es ingreso, consumo, ajuste o transferencia?"}
    cant = _num(cantidad)
    if cant <= 0:
        return {"ok": False, "faltan": ["cantidad"], "error": "La cantidad tiene que ser mayor a cero."}
    i, err = _uno(x, articulo)
    if err:
        return err
    f = x.loc[i]
    antes = _num(f.get(f"stock_{sec}"))
    if tp not in _SUMAN and cant > antes:
        return {"ok": False, "error": f"No hay stock suficiente en {SECTORES[sec]}: hay {_cant(antes)}."}
    despues = antes + cant if tp in _SUMAN else antes - cant
    otro = "farmacia" if sec == "quirofano" else "quirofano"
    filas = [("Artículo", _txt(f["articulo"])), ("Stock", SECTORES[sec]), ("Movimiento", tp),
             ("Cantidad", _cant(cant))]
    cambios = [(f"Stock {SECTORES[sec]}", _cant(antes), _cant(despues))]
    if tp == "Transferencia al otro stock":
        d0 = _num(f.get(f"stock_{otro}"))
        cambios.append((f"Stock {SECTORES[otro]}", _cant(d0), _cant(d0 + cant)))
    venc = _fecha(vencimiento)
    if _txt(lote):
        filas.append(("Lote", _txt(lote)))
    if pd.notna(venc):
        filas.append(("Vence", venc.strftime("%d/%m/%Y")))
    if _txt(motivo):
        filas.append(("Motivo", _txt(motivo)))
    avisos = []
    minimo = _num(f.get(f"stock_minimo_{sec}"))
    if tp not in _SUMAN and minimo > 0 and despues <= minimo:
        avisos.append(f"Queda en {_cant(despues)}: en o bajo el mínimo ({_cant(minimo)}).")
    return {"ok": True, "accion": {
        "tipo": "farmacia_movimiento", "modulo": MODULO, "icono": "💊", "titulo": f"{tp} · {_txt(f['articulo'])}",
        "filas": filas, "cambios": cambios, "avisos": avisos,
        "datos": {"id_articulo": _txt(f.get("id_articulo")), "articulo": _txt(f["articulo"]), "sector": sec,
                  "tipo": tp, "cantidad": cant, "motivo": _txt(motivo), "lote": _txt(lote),
                  "vencimiento": venc.strftime("%Y-%m-%d") if pd.notna(venc) else "",
                  "responsable": _txt(responsable), "procedimiento": _txt(procedimiento)}}}


def _valor_campo(campo: str, valor: Any) -> str:
    if campo.startswith("vencimiento_"):
        f = _fecha(valor)
        return f.strftime("%Y-%m-%d") if pd.notna(f) else ""
    if campo in ("costo_unitario", "stock_minimo_quirofano", "stock_minimo_farmacia"):
        return f"{_num(valor):g}"
    if campo in ("requiere_frio", "medicamento_controlado", "activo"):
        return "True" if _norm(valor) in {"si", "true", "1", "yes", "s"} else "False"
    return _txt(valor)


def articulo_datos(raw_stock: pd.DataFrame | None, *, articulo: str, nuevo: bool = False,
                   cambios: dict[str, Any] | None = None, stock_quirofano: Any = "", stock_farmacia: Any = "",
                   responsable: str = "") -> dict[str, Any]:
    x, _ = stock(raw_stock)
    pedidos = {k: v for k, v in (cambios or {}).items() if k in CAMPOS_ARTICULO and _txt(v)}
    stocks = {s: _num(v) for s, v in (("quirofano", stock_quirofano), ("farmacia", stock_farmacia)) if _txt(v)}
    if not _txt(articulo):
        return {"ok": False, "faltan": ["articulo"]}
    if nuevo:
        if len(x[x["articulo"].map(_norm) == _norm(articulo)]):
            return {"ok": False, "error": f"«{articulo}» ya está en Farmacia: decime qué querés cambiarle."}
        filas = [("Artículo nuevo", _txt(articulo))] + [(k.replace("_", " ").capitalize(), _valor_campo(k, v))
                                                         for k, v in pedidos.items()]
        filas += [(f"Stock inicial {SECTORES[s]}", _cant(v)) for s, v in stocks.items()]
        return {"ok": True, "accion": {
            "tipo": "farmacia_articulo", "modulo": MODULO, "icono": "💊", "titulo": f"Artículo nuevo · {_txt(articulo)}",
            "filas": filas, "cambios": [], "avisos": [],
            "datos": {"nuevo": True, "articulo": _txt(articulo), "campos": {k: _valor_campo(k, v) for k, v in pedidos.items()},
                      "stocks": stocks, "responsable": _txt(responsable)}}}
    i, err = _uno(x, articulo)
    if err:
        return err
    f = x.loc[i]
    lista = []
    for k, v in pedidos.items():
        nuevo_v = _valor_campo(k, v)
        if _txt(f.get(k)) != nuevo_v:
            lista.append((k.replace("_", " ").capitalize(), _txt(f.get(k)), nuevo_v))
    for s, v in stocks.items():
        if _num(f.get(f"stock_{s}")) != v:
            lista.append((f"Stock {SECTORES[s]} (ajuste)", _cant(_num(f.get(f"stock_{s}"))), _cant(v)))
    if not lista:
        return {"ok": False, "error": "No hay nada para cambiar: ya está así."}
    return {"ok": True, "accion": {
        "tipo": "farmacia_articulo", "modulo": MODULO, "icono": "💊", "titulo": f"Cambios · {_txt(f['articulo'])}",
        "filas": [("Artículo", _txt(f["articulo"]))], "cambios": lista, "avisos": [],
        "datos": {"nuevo": False, "id_articulo": _txt(f.get("id_articulo")), "articulo": _txt(f["articulo"]),
                  "campos": {k: _valor_campo(k, v) for k, v in pedidos.items()}, "stocks": stocks,
                  "responsable": _txt(responsable)}}}


def _de_vuelta(x: pd.DataFrame, original: dict[str, str]) -> pd.DataFrame:
    """Vuelve a los nombres de columna de la planilla (las columnas nuevas quedan con su nombre estándar)."""
    return x.rename(columns={k: v for k, v in original.items() if k in x.columns})


def _mov(datos: dict[str, Any]) -> dict[str, Any]:
    fila = {c: "" for c in _CAMPOS_MOV}
    fila.update(datos)
    fila["id_movimiento"] = f"MOV-{uuid.uuid4().hex[:10].upper()}"
    fila["fecha"] = date.today().strftime("%Y-%m-%d")
    fila["hora"] = datetime.now().strftime("%H:%M:%S")
    return fila


def _agregar_movs(raw_movs: pd.DataFrame | None, filas: list[dict[str, Any]]) -> pd.DataFrame:
    m = raw_movs.copy() if isinstance(raw_movs, pd.DataFrame) else pd.DataFrame()
    if len(m.columns) == 1 and _norm(m.columns[0]) == "sin_datos":
        m = pd.DataFrame()
    norm_a_orig = {_norm(c): c for c in m.columns}
    nuevas = pd.DataFrame([{norm_a_orig.get(k, k): v for k, v in f.items()} for f in filas])
    return pd.concat([m, nuevas], ignore_index=True)


def _responsable(datos: dict[str, Any], usuario: str) -> str:
    quien = _txt(datos.get("responsable"))
    return quien or f"Luz (pedido de {usuario or 'rchar'})"


def ejecutar_movimiento(accion: dict[str, Any], leer: Callable[[str], pd.DataFrame],
                        guardar: Callable[[str, pd.DataFrame], Any]) -> str:
    d = accion["datos"]
    raw = leer(TABLA_STOCK)
    r = movimiento_datos(raw, articulo=d["articulo"], sector=d["sector"], tipo=d["tipo"],
                         cantidad=d["cantidad"], motivo=d["motivo"], lote=d["lote"], vencimiento=d["vencimiento"],
                         responsable=d["responsable"], procedimiento=d.get("procedimiento", ""))
    if not r["ok"]:
        raise RuntimeError(r.get("error") or "Cambió el stock mientras tanto: pedímelo de nuevo.")
    x, original = stock(raw)
    i, _ = _uno(x, d["articulo"])
    sec, tp, cant = d["sector"], d["tipo"], float(d["cantidad"])
    otro = "farmacia" if sec == "quirofano" else "quirofano"
    antes = _num(x.at[i, f"stock_{sec}"])
    despues = antes + cant if tp in _SUMAN else antes - cant
    x.at[i, f"stock_{sec}"] = max(despues, 0.0)
    d0 = d1 = 0.0
    destino = ""
    if tp == "Transferencia al otro stock":
        d0 = _num(x.at[i, f"stock_{otro}"])
        d1 = d0 + cant
        x.at[i, f"stock_{otro}"] = d1
        destino = SECTORES[otro]
    if d["lote"]:
        x.at[i, f"lote_{sec}"] = d["lote"]
    if d["vencimiento"]:
        x.at[i, f"vencimiento_{sec}"] = d["vencimiento"]
    x.at[i, "ultima_actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mov = _mov({"sector": SECTORES[sec], "tipo": tp, "id_articulo": _txt(x.at[i, "id_articulo"]),
                "articulo": _txt(x.at[i, "articulo"]), "cantidad": cant, "stock_anterior": antes,
                "stock_nuevo": max(despues, 0.0), "sector_destino": destino, "stock_destino_anterior": d0,
                "stock_destino_nuevo": d1, "lote": d["lote"] or _txt(x.at[i, f"lote_{sec}"]),
                "vencimiento": d["vencimiento"] or _txt(x.at[i, f"vencimiento_{sec}"]),
                "procedimiento": d.get("procedimiento", ""), "responsable": _responsable(d, accion.get("usuario", "")),
                "motivo": d["motivo"] or "Cargado por Luz", "observaciones": ""})
    movs = _agregar_movs(leer(TABLA_MOVIMIENTOS), [mov])
    guardar(TABLA_STOCK, _de_vuelta(x, original))
    guardar(TABLA_MOVIMIENTOS, movs)
    texto = f"Listo ✅ {tp} de {_cant(cant)} · {_txt(x.at[i, 'articulo'])}: {SECTORES[sec]} quedó en {_cant(max(despues, 0.0))}"
    if destino:
        texto += f" y {destino} en {_cant(d1)}"
    return texto + "."


def ejecutar_articulo(accion: dict[str, Any], leer: Callable[[str], pd.DataFrame],
                      guardar: Callable[[str, pd.DataFrame], Any]) -> str:
    d = accion["datos"]
    raw = leer(TABLA_STOCK)
    x, original = stock(raw)
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    movs: list[dict[str, Any]] = []
    quien = _responsable(d, accion.get("usuario", ""))
    if d.get("nuevo"):
        if len(x[x["articulo"].map(_norm) == _norm(d["articulo"])]):
            raise RuntimeError(f"«{d['articulo']}» ya está cargado.")
        fila = {c: "" for c in x.columns}
        fila.update({"id_articulo": f"ART-{uuid.uuid4().hex[:8].upper()}", "articulo": d["articulo"],
                     "activo": "True", "stock_quirofano": 0.0, "stock_farmacia": 0.0, "ultima_actualizacion": ahora})
        fila.setdefault("categoria", "")
        if not _txt(fila.get("categoria")):
            fila["categoria"] = "Insumos generales"
        if not _txt(fila.get("unidad")):
            fila["unidad"] = "unidad"
        fila.update(d.get("campos") or {})
        x = pd.concat([x, pd.DataFrame([fila])], ignore_index=True)
        i = len(x) - 1
        antes_stock = {s: 0.0 for s in SECTORES}
    else:
        i, err = _uno(x, d["articulo"])
        if err:
            raise RuntimeError(err.get("error") or "No encontré el artículo.")
        antes_stock = {s: _num(x.at[i, f"stock_{s}"]) for s in SECTORES}
        for k, v in (d.get("campos") or {}).items():
            x.at[i, k] = v
        x.at[i, "ultima_actualizacion"] = ahora
    for s, v in (d.get("stocks") or {}).items():
        a = antes_stock[s]
        if v == a:
            continue
        x.at[i, f"stock_{s}"] = float(v)
        tp = "Ajuste positivo" if v > a else "Ajuste negativo"
        movs.append(_mov({"sector": SECTORES[s], "tipo": tp, "id_articulo": _txt(x.at[i, "id_articulo"]),
                          "articulo": _txt(x.at[i, "articulo"]), "cantidad": abs(float(v) - a), "stock_anterior": a,
                          "stock_nuevo": float(v), "lote": _txt(x.at[i, f"lote_{s}"]),
                          "vencimiento": _txt(x.at[i, f"vencimiento_{s}"]), "responsable": quien,
                          "motivo": "Stock inicial" if d.get("nuevo") else "Ajuste por conteo (Luz)"}))
    guardar(TABLA_STOCK, _de_vuelta(x, original))
    if movs:
        guardar(TABLA_MOVIMIENTOS, _agregar_movs(leer(TABLA_MOVIMIENTOS), movs))
    return (f"Listo ✅ Di de alta «{d['articulo']}» en Farmacia." if d.get("nuevo")
            else f"Listo ✅ Actualicé «{_txt(x.at[i, 'articulo'])}» en Farmacia.")


EJECUTORES = {"farmacia_movimiento": ejecutar_movimiento, "farmacia_articulo": ejecutar_articulo}

INSTRUCCIONES = """FARMACIA (también sos la asistente de farmacia de Vitae)
- Conocés el stock interno (Farmacia) y el stock de Quirófano: son dos existencias del mismo catálogo de artículos,
  cada una con su mínimo, lote, vencimiento y ubicación. Respondé siempre aclarando de qué stock hablás.
- farmacia_stock: existencias de uno o varios artículos (o de una categoría). farmacia_alertas: sin stock, bajo el
  mínimo, vencidos y por vencer. farmacia_movimientos: ingresos, consumos, ajustes y transferencias.
- Si te piden qué reponer, usá farmacia_alertas y proponé cantidades para llegar al mínimo.
- Nunca des datos de pacientes de los movimientos."""

INSTRUCCIONES_ACCIONES = """- Farmacia (sólo para Ramiro): proponer_movimiento_farmacia registra un ingreso / compra, un consumo / salida,
  un ajuste o una transferencia entre Quirófano y Farmacia (actualiza el stock y deja el movimiento en el historial).
  Obligatorios: artículo, stock (Quirófano o Farmacia), tipo y cantidad. Si te dictan varios, una llamada por cada uno.
- proponer_articulo_farmacia da de alta un artículo nuevo (nuevo=true) o le cambia datos (mínimos, lote, vencimiento,
  ubicación, proveedor, costo, categoría…). Si cambian la cantidad contada, pasala en stock_quirofano o
  stock_farmacia: queda como ajuste en el historial."""


def herramientas(datos: Any, hoy: date | None = None) -> list[Callable[..., str]]:
    """Consultas de Farmacia (si el usuario puede abrir el módulo)."""
    if MODULO not in datos.modulos():
        return []
    h = pd.Timestamp(hoy or date.today()).normalize()

    def _x() -> pd.DataFrame:
        try:
            x = stock(datos.df(MODULO))[0]
        except Exception:
            x = stock(None)[0]
        return x[x["articulo"].map(_txt) != ""]

    def farmacia_stock(articulo: str = "", categoria: str = "", sector: str = "") -> str:
        """Existencias de Farmacia y Quirófano. articulo: nombre (o parte), código o principio activo; vacío =
        todos. categoria: filtra por categoría. sector: Quirófano o Farmacia (vacío = los dos)."""
        try:
            datos.consultas.append(f"farmacia_stock({articulo}, {categoria}, {sector})")
        except Exception:
            pass
        x = _x()
        if articulo:
            x = buscar(x, articulo)
        if categoria:
            x = x[x["categoria"].map(_norm).str.contains(_norm(categoria), na=False)]
        fichas = [ficha(f, h) for _, f in x.head(60).iterrows()]
        s = sector_de(sector)
        if s:
            otro = SECTORES["farmacia" if s == "quirofano" else "quirofano"]
            for f in fichas:
                f.pop(otro, None)
        return _json({"articulos": fichas, "total": int(len(x)), "mostrados": len(fichas)})

    def farmacia_alertas(dias_vencimiento: int = 60) -> str:
        """Lo que hay que mirar en Farmacia y Quirófano: sin stock, bajo el mínimo, vencidos y por vencer
        (dentro de dias_vencimiento días)."""
        try:
            datos.consultas.append("farmacia_alertas()")
        except Exception:
            pass
        return _json(alertas(_x(), h, int(dias_vencimiento or 60)))

    def farmacia_movimientos(articulo: str = "", sector: str = "", tipo: str = "", dias: int = 30) -> str:
        """Movimientos de Farmacia/Quirófano de los últimos `dias` días (0 = todos): ingresos, consumos,
        ajustes, transferencias y controles. Filtros opcionales: articulo, sector, tipo."""
        try:
            datos.consultas.append(f"farmacia_movimientos({articulo}, {sector}, {tipo}, {dias})")
        except Exception:
            pass
        try:
            raw = datos.df(MODULO_MOVIMIENTOS)
        except Exception:
            raw = pd.DataFrame()
        return _json({"movimientos": movimientos(raw, articulo=articulo, sector=sector, tipo=tipo, dias=int(dias or 0),
                                                 hoy=h)})

    return [farmacia_stock, farmacia_alertas, farmacia_movimientos]


def herramientas_accion(datos: Any, acciones: list[dict[str, Any]]) -> list[Callable[..., str]]:
    """Cargar y editar en Farmacia: sólo si quien pide es rchar."""
    usuario = str(getattr(datos, "usuario", "") or "").strip().lower()
    if usuario not in EDITORES or MODULO not in datos.modulos():
        return []

    def _raw() -> pd.DataFrame:
        try:
            return datos.df(MODULO)
        except Exception:
            return pd.DataFrame()

    def _anotar(r: dict[str, Any]) -> str:
        if not r["ok"]:
            return _json({"resultado": "no se armó", **{k: v for k, v in r.items() if k != "ok"}})
        r["accion"]["usuario"] = usuario
        acciones.append(r["accion"])
        return _json({"resultado": "lista para confirmar (todavía NO está guardada)", "titulo": r["accion"]["titulo"],
                      "cambios": r["accion"]["cambios"], "avisos": r["accion"]["avisos"]})

    def proponer_movimiento_farmacia(articulo: str, sector: str, tipo: str, cantidad: float, motivo: str = "",
                                     lote: str = "", vencimiento: str = "", responsable: str = "",
                                     procedimiento: str = "") -> str:
        """Prepara un movimiento de Farmacia (NO lo guarda: el usuario confirma en el chat).
        sector: Quirófano o Farmacia (el stock interno). tipo: ingreso / compra, consumo / salida, ajuste positivo,
        ajuste negativo o transferencia (pasa la cantidad al otro stock). vencimiento: AAAA-MM-DD (opcional)."""
        try:
            datos.consultas.append(f"proponer_movimiento_farmacia({articulo}, {sector}, {tipo}, {cantidad})")
        except Exception:
            pass
        return _anotar(movimiento_datos(_raw(), articulo=articulo, sector=sector, tipo=tipo, cantidad=cantidad,
                                        motivo=motivo, lote=lote, vencimiento=vencimiento, responsable=responsable,
                                        procedimiento=procedimiento))

    def proponer_articulo_farmacia(articulo: str, nuevo: bool = False, codigo: str = "", principio_activo: str = "",
                                   categoria: str = "", presentacion: str = "", unidad: str = "", proveedor: str = "",
                                   costo_unitario: str = "", requiere_frio: str = "", medicamento_controlado: str = "",
                                   activo: str = "", stock_minimo_quirofano: str = "", ubicacion_quirofano: str = "",
                                   lote_quirofano: str = "", vencimiento_quirofano: str = "",
                                   stock_minimo_farmacia: str = "", ubicacion_farmacia: str = "",
                                   lote_farmacia: str = "", vencimiento_farmacia: str = "", observaciones: str = "",
                                   stock_quirofano: str = "", stock_farmacia: str = "") -> str:
        """Prepara el alta (nuevo=true) o los cambios de un artículo de Farmacia (NO lo guarda: el usuario
        confirma). Pasá sólo los datos que cambian. Vencimientos AAAA-MM-DD. stock_quirofano / stock_farmacia:
        la cantidad contada (se registra como ajuste)."""
        try:
            datos.consultas.append(f"proponer_articulo_farmacia({articulo}, nuevo={nuevo})")
        except Exception:
            pass
        loc = locals()
        cambios = {k: loc[k] for k in CAMPOS_ARTICULO}
        return _anotar(articulo_datos(_raw(), articulo=articulo, nuevo=bool(nuevo), cambios=cambios,
                                      stock_quirofano=stock_quirofano, stock_farmacia=stock_farmacia))

    return [proponer_movimiento_farmacia, proponer_articulo_farmacia]
