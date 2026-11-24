"""Estructura del menú: grupos de módulos, íconos y nombres de empresas.

Lo usan el menú lateral (app.py) y el encabezado de cada módulo, para que
ambos muestren siempre el mismo grupo e ícono.
"""
from __future__ import annotations

EMPRESAS = {
    "VMR": "Vitae Medicina Reproductiva",
    "VM": "Vitae Medical",
    "VITAE": "Vitae",
}

GRUPOS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Facturación y cobros", [
        ("Facturación VMR", ":material/receipt_long:"),
        ("Facturación VM", ":material/receipt_long:"),
        ("Cuenta Corriente VMR", ":material/account_balance_wallet:"),
        ("Cuenta Corriente VM", ":material/account_balance_wallet:"),
        ("Honorarios médicos", ":material/medical_information:"),
        ("Convenios", ":material/handshake:"),
    ]),
    ("Cajas y bancos", [
        ("Caja VMR", ":material/point_of_sale:"),
        ("Banco Macro VMR", ":material/account_balance:"),
        ("Caja VM", ":material/point_of_sale:"),
        ("Banco Galicia VM", ":material/account_balance:"),
    ]),
    ("Deudas y pagos", [
        ("Deuda total", ":material/monitoring:"),
        ("Deudas Impositivas VMR", ":material/gavel:"),
        ("Deudas Impositivas VM", ":material/gavel:"),
        ("Planes de pagos y préstamos", ":material/credit_score:"),
        ("Pagos pendientes Vitae", ":material/pending_actions:"),
        ("Gastos comunes", ":material/shopping_cart:"),
        ("Vencimientos", ":material/event_upcoming:"),
    ]),
    ("Operación clínica", [
        ("Agenda Quirófano", ":material/calendar_month:"),
        ("Farmacia", ":material/medication:"),
        ("Gine Vitae", ":material/female:"),
    ]),
    ("Contratos y tareas", [
        ("Contratos", ":material/contract:"),
        ("Valores Alquileres", ":material/real_estate_agent:"),
        ("Tareas Pendientes", ":material/task_alt:"),
    ]),
]

ICONO_POR_DEFECTO = ":material/folder:"


def grupo_de(modulo: str) -> str | None:
    for grupo, modulos in GRUPOS:
        if any(nombre == modulo for nombre, _ in modulos):
            return grupo
    return None


def icono_de(modulo: str) -> str:
    for _, modulos in GRUPOS:
        for nombre, icono in modulos:
            if nombre == modulo:
                return icono
    return ICONO_POR_DEFECTO
