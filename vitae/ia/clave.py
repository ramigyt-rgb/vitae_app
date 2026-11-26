"""Clave de Luz: la piden los cambios libres (alta / modificación / baja en cualquier tabla).

La clave NUNCA está escrita en el código (el repo es público): sólo su hash PBKDF2.
Para cambiarla sin tocar el código, en los secrets de Streamlit:

    LUZ_CLAVE = "la-nueva-clave"          # o, mejor,
    LUZ_CLAVE_HASH = "pbkdf2:<sal_hex>:<hash_hex>"

La clave se escribe en la tarjeta de confirmación (campo oculto), nunca en el chat: así no
viaja a la IA, no queda en el historial y no entra en la memoria.
"""
from __future__ import annotations

import hashlib
import hmac
import re

_ITERACIONES = 300_000
# Hash por defecto (PBKDF2-SHA256, 300.000 vueltas).
_HASH_DEFECTO = ("pbkdf2:2edacc67819dce94cb40f79c4f86b1e1:"
                 "204c5a6591a20f47dbb77131a08173ac11dba7babb54d8b306b96a847f11d647")
MAX_INTENTOS = 3


def _secreto(nombre: str) -> str:
    try:
        import streamlit as st

        return str(st.secrets.get(nombre, "") or "").strip()
    except Exception:
        return ""


def _hash(texto: str, sal_hex: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", texto.encode("utf-8"), bytes.fromhex(sal_hex), _ITERACIONES).hex()


def hash_nuevo(texto: str, sal_hex: str = "") -> str:
    """Para generar un LUZ_CLAVE_HASH: python -c "from vitae.ia.clave import hash_nuevo; print(hash_nuevo('x'))"."""
    import os

    sal = sal_hex or os.urandom(16).hex()
    return f"pbkdf2:{sal}:{_hash(texto, sal)}"


def es_correcta(texto: str | None) -> bool:
    texto = str(texto or "").strip()
    if not texto:
        return False
    plana = _secreto("LUZ_CLAVE")
    if plana:
        return hmac.compare_digest(texto, plana)
    guardado = _secreto("LUZ_CLAVE_HASH") or _HASH_DEFECTO
    try:
        _, sal, esperado = guardado.split(":", 2)
        return hmac.compare_digest(_hash(texto, sal), esperado)
    except Exception:
        return False


def tapar(texto: str) -> str:
    """Si alguien escribe la clave en el chat, se tapa antes de mandarla a la IA o guardarla."""
    if not texto:
        return texto
    salida = texto
    candidatas = {p.strip(".,;:!?¡¿\"'()") for p in re.findall(r"\S{8,}", texto)}
    # Las más largas primero y como mucho 6 (cada chequeo tarda ~0,06 s).
    for limpia in sorted((c for c in candidatas if len(c) >= 8), key=len, reverse=True)[:6]:
        if es_correcta(limpia):
            salida = salida.replace(limpia, "••••••")
    return salida
