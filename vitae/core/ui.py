"""Componentes visuales compartidos por todos los módulos."""
from __future__ import annotations

import html
import re
from typing import Any

import streamlit as st

from vitae.core.navegacion import EMPRESAS, grupo_de

_ICONO = re.compile(r":material/[a-z0-9_]+:\s*")


def sin_icono(texto: Any) -> str:
    """Quita los ':material/…:' de un texto (los desplegables no los muestran)."""
    return _ICONO.sub("", str(texto))


def encabezado_modulo(module_name: str, cfg: dict[str, Any] | None = None, descripcion: str | None = None) -> None:
    """Encabezado igual para todas las pantallas:

        GRUPO · EMPRESA
        Título del módulo
        Descripción breve
    """
    cfg = cfg or {}
    empresa = str(cfg.get("empresa", "") or "").strip().upper()
    partes = [p for p in (grupo_de(module_name), EMPRESAS.get(empresa, empresa) if empresa else None) if p]
    kicker = " · ".join(partes)
    texto = descripcion if descripcion is not None else str(cfg.get("descripcion", "") or "")
    st.markdown(
        f"""
        <div class="vitae-page-head">
          {f'<div class="vitae-page-kicker">{html.escape(kicker)}</div>' if kicker else ''}
          <div class="vitae-page-title">{html.escape(module_name)}</div>
          {f'<div class="vitae-page-sub">{html.escape(texto)}</div>' if texto else ''}
        </div>
        """,
        unsafe_allow_html=True,
    )
