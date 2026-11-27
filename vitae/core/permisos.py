"""Permisos por función dentro de cada módulo: ver, cargar, editar y borrar.

- «Ver» es el acceso al módulo (``mod::<Módulo>``).
- Las demás funciones se guardan en la columna ``permisos`` del usuario como
  ``fn::<Módulo>::cargar,editar`` (vacío después de ``::`` = sólo ver).
- Si el usuario no tiene ninguna marca ``fn::`` para un módulo, puede hacer todo
  en él (así quedan los usuarios que ya existían).
- El control se hace al GUARDAR (``vitae.core.database.sync_df_to_sheet``): se
  compara la pestaña antes y después y se ve si se agregaron, cambiaron o
  borraron filas. Así vale para cualquier pantalla del módulo y para Luz.
- Los superusuarios (``rchar``) tienen siempre todas las funciones.
"""
from __future__ import annotations

import contextlib
import threading
from collections import Counter
from typing import Any, Iterable

import pandas as pd

FUNCIONES: list[tuple[str, str]] = [("cargar", "Cargar"), ("editar", "Editar"), ("borrar", "Borrar")]
CLAVES = [k for k, _ in FUNCIONES]
NOMBRE = dict(FUNCIONES)
SUPERUSUARIOS = {"rchar"}
_PREFIJO = "fn::"

_local = threading.local()


def es_superusuario(user: dict[str, Any] | None) -> bool:
    return bool(user) and str((user or {}).get("usuario") or "").strip().lower() in SUPERUSUARIOS


def token(modulo: str, funciones: Iterable[str]) -> str:
    fs = [f for f in CLAVES if f in set(funciones)]
    return f"{_PREFIJO}{modulo}::{','.join(fs)}"


def funciones_de(permisos: Iterable[str], modulo: str) -> set[str] | None:
    """Funciones habilitadas en el módulo (None = todas: no se limitaron)."""
    pref = f"{_PREFIJO}{modulo}::"
    marcas = [p for p in permisos if str(p).startswith(pref)]
    if not marcas:
        return None
    out: set[str] = set()
    for p in marcas:
        out |= {f.strip() for f in p[len(pref):].split(",") if f.strip() in CLAVES}
    return out


def sin_marcas(permisos: Iterable[str], modulos: Iterable[str] | None = None) -> list[str]:
    """Los permisos sin las marcas ``fn::`` (de esos módulos, o de todos)."""
    mods = set(modulos) if modulos is not None else None
    out = []
    for p in permisos:
        if str(p).startswith(_PREFIJO):
            mod = str(p)[len(_PREFIJO):].split("::", 1)[0]
            if mods is None or mod in mods:
                continue
        out.append(p)
    return out


def modulo_de_tabla(tabla: str, modulos: dict[str, dict[str, Any]], extras: dict[str, str] | None = None) -> str | None:
    t = str(tabla or "").strip().lower()
    for nombre, cfg in modulos.items():
        if str(cfg.get("table") or "").strip().lower() == t:
            return nombre
    return (extras or {}).get(t)


def _filas(df: pd.DataFrame, columnas: list[str]) -> Counter:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return Counter()
    x = df.reindex(columns=columnas).copy()
    x = x.where(pd.notnull(x), "").astype(str).apply(lambda s: s.str.strip())
    x = x[(x != "").any(axis=1)]
    return Counter(map(tuple, x.values.tolist()))


def cambios(antes: pd.DataFrame | None, despues: pd.DataFrame | None) -> set[str]:
    """Qué funciones hacen falta para pasar de ``antes`` a ``despues``."""
    antes = antes if isinstance(antes, pd.DataFrame) else pd.DataFrame()
    despues = despues if isinstance(despues, pd.DataFrame) else pd.DataFrame()
    cols = list(dict.fromkeys([str(c) for c in antes.columns] + [str(c) for c in despues.columns]))
    a = _filas(antes.rename(columns=str), cols)
    d = _filas(despues.rename(columns=str), cols)
    solo_d = sum((d - a).values())
    solo_a = sum((a - d).values())
    out: set[str] = set()
    pares = min(solo_d, solo_a)
    if pares:
        out.add("editar")
    if solo_d > pares:
        out.add("cargar")
    if solo_a > pares:
        out.add("borrar")
    return out


def faltantes(user: dict[str, Any] | None, permisos: Iterable[str], modulo: str,
              antes: pd.DataFrame | None, despues: pd.DataFrame | None) -> set[str]:
    if es_superusuario(user):
        return set()
    habilitadas = funciones_de(permisos, modulo)
    if habilitadas is None:
        return set()
    return cambios(antes, despues) - habilitadas


def mensaje(modulo: str, faltan: set[str]) -> str:
    acciones = " ni ".join(NOMBRE[f].lower() for f in CLAVES if f in faltan)
    return f"No tenés permiso para {acciones} registros en {modulo}. Pedíselo a Ramiro (Configuración → Permisos)."


@contextlib.contextmanager
def sistema():
    """Escrituras automáticas de la app (p. ej. la Agenda que carga Facturación sola): no se controlan."""
    previo = getattr(_local, "sistema", False)
    _local.sistema = True
    try:
        yield
    finally:
        _local.sistema = previo


def en_sistema() -> bool:
    return bool(getattr(_local, "sistema", False))
