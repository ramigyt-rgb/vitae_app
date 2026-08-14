from __future__ import annotations

"""
Parche no destructivo para VITAE INTELLIGENCE.

Objetivo:
- conservar el assistant.py existente;
- seguir calculando perfiles/totales sobre el 100% de las filas;
- cuando el usuario pide un LISTADO NOMINAL o detalle completo, recuperar todas
  las filas relevantes necesarias para responder (hasta un límite de seguridad);
- evitar enviar toda la empresa a Gemini cuando la pregunta no lo necesita.

Este archivo no escribe en Google Sheets ni modifica ningún DataFrame fuente.
"""

from typing import Any
import re
import pandas as pd


_MAX_LIST_ROWS_PER_MODULE = 500
_MAX_NORMAL_ROWS_PER_MODULE = 90
_MAX_ENTERPRISE_LIST_MODULES = 4
_MAX_ENTERPRISE_NORMAL_MODULES = 12


def _norm_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.casefold()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_listing_question(question: str) -> bool:
    q = _norm_text(question)
    signals = (
        "listado", "lista ", "listar", "listame", "listá", "mostrame",
        "mostrarme", "quienes", "quiénes", "cuales son", "cuáles son",
        "todos los", "todas las", "detalle completo", "detalle exacto",
        "uno por uno", "nominal", "nombres", "pacientes pendientes",
        "facturas pendientes", "registros pendientes",
    )
    return any(signal in q for signal in signals)


def _smart_limit(question: str, df: pd.DataFrame, fallback: int = _MAX_NORMAL_ROWS_PER_MODULE) -> int:
    if df is None or df.empty:
        return 0
    if _is_listing_question(question):
        return min(len(df), _MAX_LIST_ROWS_PER_MODULE)
    return min(len(df), max(1, fallback))


def aplicar_parche_acceso_total() -> bool:
    """
    Aplica el parche sobre el módulo assistant ya instalado.
    Puede llamarse varias veces sin duplicar cambios.
    """
    import assistant as a

    if getattr(a, "_VITAE_FULL_ACCESS_PATCH_APPLIED", False):
        return True

    required = (
        "_select_relevant_rows",
        "_records",
        "_profile_dataframe",
        "_module_metadata",
        "_question_module_score",
        "_norm",
    )
    if not all(hasattr(a, name) for name in required):
        # Si assistant.py cambia de arquitectura en el futuro, no rompemos la app.
        return False

    def _build_module_packet_full(
        module_name: str,
        df: pd.DataFrame,
        question: str,
        detail_rows: int | None = None,
    ) -> dict[str, Any]:
        local_df = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame(df)

        # Si el llamador pidió un límite explícito grande, se respeta.
        # Para llamadas antiguas que heredaban 55, se usa el límite inteligente.
        requested = int(detail_rows or 0)
        if _is_listing_question(question):
            limit = _smart_limit(question, local_df)
        elif requested > 55:
            limit = min(len(local_df), requested)
        else:
            limit = _smart_limit(question, local_df)

        relevant = a._select_relevant_rows(local_df, question, limit)
        sent = int(min(len(relevant), limit))
        complete_for_query = bool(
            _is_listing_question(question)
            and sent == len(relevant)
            and len(relevant) <= limit
        )

        return {
            "modulo": module_name,
            "definicion": a._module_metadata(module_name),
            "perfil_total": a._profile_dataframe(local_df),
            "detalle_relevante": a._records(relevant, limit),
            "filas_detalle_enviadas": sent,
            "detalle_completo_para_consulta": complete_for_query,
            "limite_inteligente_aplicado": int(limit),
        }

    def _build_enterprise_packet_full(
        data: dict[str, pd.DataFrame],
        question: str,
        current_module: str | None = None,
    ) -> dict[str, Any]:
        profiles: list[dict[str, Any]] = []
        scored: list[tuple[int, str, pd.DataFrame]] = []
        total_rows = 0
        with_data = 0

        for module_name, df in (data or {}).items():
            if not isinstance(df, pd.DataFrame):
                continue

            total_rows += int(len(df))
            if not df.empty:
                with_data += 1

            profiles.append({
                "modulo": module_name,
                "definicion": a._module_metadata(module_name),
                "perfil_total": a._profile_dataframe(df),
            })

            score = a._question_module_score(module_name, df, question)
            if current_module and a._norm(module_name) == a._norm(current_module):
                score += 50
            scored.append((score, module_name, df))

        scored.sort(key=lambda item: (item[0], len(item[2])), reverse=True)

        if _is_listing_question(question):
            selected = [item for item in scored if item[0] > 0][:_MAX_ENTERPRISE_LIST_MODULES]
            if not selected and current_module:
                selected = [
                    item for item in scored
                    if a._norm(item[1]) == a._norm(current_module)
                ][:1]
            if not selected:
                selected = [item for item in scored if not item[2].empty][:2]
        else:
            selected = [item for item in scored if item[0] > 0][:_MAX_ENTERPRISE_NORMAL_MODULES]
            if not selected:
                selected = [item for item in scored if not item[2].empty][:6]

        details: list[dict[str, Any]] = []
        for _, module_name, df in selected:
            if _is_listing_question(question):
                limit = _smart_limit(question, df)
            else:
                # Mantiene el comportamiento compacto para preguntas ejecutivas.
                limit = min(len(df), max(8, 90 // max(len(selected), 1)))

            relevant = a._select_relevant_rows(df, question, limit)
            sent = int(min(len(relevant), limit))

            details.append({
                "modulo": module_name,
                "filas": a._records(relevant, limit),
                "filas_detalle_enviadas": sent,
                "detalle_completo_para_consulta": bool(
                    _is_listing_question(question)
                    and sent == len(relevant)
                    and len(relevant) <= limit
                ),
            })

        return {
            "cobertura": {
                "modulos_conocidos": int(len(data or {})),
                "modulos_con_datos": int(with_data),
                "filas_totales_analizadas_por_python": int(total_rows),
                "nota": (
                    "Los perfiles y totales se calcularon sobre todas las filas. "
                    "Cuando la consulta pide un listado nominal, el detalle relevante "
                    "se amplía dinámicamente para incluir todos los registros necesarios "
                    "dentro del límite de seguridad."
                ),
            },
            "mapa_modulos": profiles,
            "detalle_relevante_multimodulo": details,
        }

    a._build_module_packet = _build_module_packet_full
    a._build_enterprise_packet = _build_enterprise_packet_full

    # Reglas extra para que el modelo no siga interpretando un listado completo
    # como si fuera una muestra parcial.
    extra_rules = """
18. Si `detalle_completo_para_consulta` es true, ese detalle contiene el conjunto
    completo recuperado para responder la consulta nominal. En ese caso NO digas
    que sólo recibiste una muestra: respondé usando todos esos registros.
19. Si el usuario pide nombres, facturas, pacientes o registros "uno por uno",
    listalos de manera completa cuando `detalle_completo_para_consulta` sea true.
20. No pidas al operador exportar datos si el contexto ya contiene el detalle
    completo necesario para responder.
"""
    try:
        rules = str(getattr(a, "_SYSTEM_RULES", ""))
        if "detalle_completo_para_consulta" not in rules:
            a._SYSTEM_RULES = rules.rstrip() + "\n" + extra_rules.strip() + "\n"
    except Exception:
        pass

    a._VITAE_FULL_ACCESS_PATCH_APPLIED = True
    return True
