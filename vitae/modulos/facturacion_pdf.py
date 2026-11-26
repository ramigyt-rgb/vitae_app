"""PDF del cierre de Facturación (VMR / VM): un mes o el año entero.

Mes: estado del cierre (quién y cuándo), números (prestaciones, facturado, cobrado, por cobrar,
débitos), el control punto por punto y el detalle por obra social, médico y procedimiento.
Año: un renglón por mes con su estado y números, los totales y las obras sociales del año.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any

import pandas as pd

from vitae.modulos.cierre_facturacion import mes_txt
from vitae.modulos.pagos_pendientes import pesos

BORDO = "#8C2853"
GRIS = "#6B5F66"


def _estilos():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold", fontSize=17,
                                 textColor=colors.HexColor(BORDO), alignment=0, spaceAfter=2),
        "sub": ParagraphStyle("s", parent=base["Normal"], fontName="Helvetica", fontSize=9.5,
                              textColor=colors.HexColor(GRIS), spaceAfter=8),
        "h": ParagraphStyle("h", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11.5,
                            textColor=colors.HexColor("#5E1A39"), spaceBefore=10, spaceAfter=4),
        "n": ParagraphStyle("n", parent=base["Normal"], fontName="Helvetica", fontSize=9, leading=12),
        "nota": ParagraphStyle("nota", parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=8,
                               textColor=colors.HexColor(GRIS), leading=10),
    }


def _tabla(filas: list[list[Any]], anchos: list[float], *, encabezado: bool = True, total: bool = False,
           derecha_desde: int = 1, fondos: dict[int, str] | None = None):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    t = Table(filas, colWidths=anchos, repeatRows=1 if encabezado else 0)
    estilo = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 8.3),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ALIGN", (derecha_desde, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#E8E3E6")),
    ]
    if encabezado:
        estilo += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F4F1F3")),
                   ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#5E1A39")),
                   ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    if total:
        estilo += [("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                   ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.HexColor("#CFC6CB"))]
    for fila, color in (fondos or {}).items():
        estilo.append(("BACKGROUND", (0, fila), (-1, fila), colors.HexColor(color)))
    t.setStyle(TableStyle(estilo))
    return t


def _kpis(nums: dict[str, Any]) -> list[list[str]]:
    return [["Prestaciones", "Facturado", "Cobrado", "Por cobrar", "Débitos"],
            [str(nums["prestaciones"]), pesos(nums["facturado"]), pesos(nums["cobrado"]), pesos(nums["por_cobrar"]),
             pesos(nums["debitos"])]]


def _detalle(t: pd.DataFrame, titulo: str, n: int = 12) -> list[list[str]]:
    filas = [[titulo, "Prest.", "Facturado", "Cobrado", "Por cobrar", "Débitos"]]
    for _, r in t.head(n).iterrows():
        filas.append([str(r[titulo])[:38], str(int(r["Prestaciones"])), pesos(r["Facturado"]), pesos(r["Cobrado"]),
                      pesos(r["Por cobrar"]), pesos(r["Débitos"]) if r["Débitos"] else ""])
    if len(t) > n:
        resto = t.iloc[n:]
        filas.append([f"Otros ({len(resto)})", str(int(resto["Prestaciones"].sum())), pesos(resto["Facturado"].sum()),
                      pesos(resto["Cobrado"].sum()), pesos(resto["Por cobrar"].sum()),
                      pesos(resto["Débitos"].sum()) if resto["Débitos"].sum() else ""])
    return filas


def _documento(contenido: list[Any], titulo: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate

    buf = io.BytesIO()

    def pie(canvas, doc):  # noqa: ANN001
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColorRGB(0.42, 0.37, 0.40)
        canvas.drawString(1.6 * cm, 1.0 * cm, f"Vitae · {titulo} · generado el {datetime.now():%d/%m/%Y %H:%M}")
        canvas.drawRightString(A4[0] - 1.6 * cm, 1.0 * cm, f"Página {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.4 * cm,
                            bottomMargin=1.6 * cm, title=titulo, author="Vitae")
    doc.build(contenido, onFirstPage=pie, onLaterPages=pie)
    return buf.getvalue()


def pdf_mes(empresa: str, mes: str, nums: dict[str, Any], lista: list[dict[str, Any]],
            cierre: dict[str, Any] | None, cambio: bool = False) -> bytes:
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, Spacer

    e = _estilos()
    titulo = f"Facturación {empresa} · {mes_txt(mes).capitalize()}"
    if cierre:
        estado = f"Mes cerrado el {cierre.get('cerrado_el', '')}" + (f" por {cierre['usuario']}" if cierre.get("usuario") else "")
        if cambio:
            estado += ". ATENCIÓN: hubo cambios en este mes después del cierre."
    else:
        estado = "Mes sin cerrar (borrador)."
    c: list[Any] = [Paragraph(titulo, e["titulo"]),
                    Paragraph(f"Resumen del mes por fecha de prestación. {estado}", e["sub"])]
    c.append(_tabla(_kpis(nums), [3.4 * cm] * 5, derecha_desde=0))
    extra = []
    if nums["sin_facturar"]:
        extra.append(f"{nums['sin_facturar']} prestación(es) sin facturar")
    if nums["vencido"] > 0.5:
        extra.append(f"vencido sin cobrar {pesos(nums['vencido'])}")
    if nums["debitos"] > 0.5:
        extra.append(f"débitos {pesos(nums['debitos'])} (abiertos {pesos(nums['debitos_abiertos'])}, recuperado "
                     f"{pesos(nums['recuperado'])}, aceptado {pesos(nums['aceptado'])})")
    if extra:
        c += [Spacer(1, 4), Paragraph("; ".join(extra).capitalize() + ".", e["n"])]

    c.append(Paragraph("Control del cierre", e["h"]))
    filas = [["", "Punto", "Casos", "Para cerrar"]]
    fondos = {}
    for i, x in enumerate(lista, start=1):
        filas.append(["OK" if x["ok"] else "FALTA", x["punto"], str(x["casos"]) if x["casos"] else "—",
                      "obligatorio" if x["traba"] else "informativo"])
        if not x["ok"]:
            fondos[i] = "#FBE4E4" if x["traba"] else "#FDF0DC"
    c.append(_tabla(filas, [1.5 * cm, 10.2 * cm, 1.6 * cm, 3.7 * cm], derecha_desde=2, fondos=fondos))

    for clave, titulo_t, n in (("por_obra_social", "Obra social", 15), ("por_medico", "Médico", 10),
                               ("por_procedimiento", "Procedimiento", 10)):
        t = nums.get(clave)
        if isinstance(t, pd.DataFrame) and len(t):
            c.append(Paragraph(f"Por {titulo_t.lower()}", e["h"]))
            c.append(_tabla(_detalle(t, titulo_t, n), [6.2 * cm, 1.4 * cm, 2.4 * cm, 2.4 * cm, 2.4 * cm, 2.2 * cm]))
    c += [Spacer(1, 8), Paragraph("Facturado: valor de las prestaciones del mes. Cobrado y por cobrar: según lo anotado "
                                  "en la planilla a la fecha de este informe. Plazo de presentación: 60 días desde la "
                                  "prestación.", e["nota"])]
    return _documento(c, titulo)


def pdf_anio(empresa: str, anio: int, tabla_anio: pd.DataFrame, nums_anio: dict[str, Any]) -> bytes:
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, Spacer

    e = _estilos()
    titulo = f"Facturación {empresa} · Año {anio}"
    cerrados = int(tabla_anio["Estado"].str.contains("Cerrado").sum()) if len(tabla_anio) else 0
    c: list[Any] = [Paragraph(titulo, e["titulo"]),
                    Paragraph(f"Mes a mes por fecha de prestación. Meses cerrados: {cerrados} de {len(tabla_anio)}.", e["sub"])]
    c.append(_tabla(_kpis(nums_anio), [3.4 * cm] * 5, derecha_desde=0))
    c.append(Paragraph("Mes a mes", e["h"]))
    filas = [["Mes", "Estado", "Prest.", "Sin fact.", "Facturado", "Cobrado", "Por cobrar"]]
    fondos = {}
    for i, (_, r) in enumerate(tabla_anio.iterrows(), start=1):
        est = str(r["Estado"]).replace("🔒 ", "").replace("✅ ", "").replace("🔴 ", "").replace("⏳ ", "") \
            .replace("⚠️ ", "").replace("— ", "")
        filas.append([r["Mes"], est, str(int(r["Prestaciones"])), str(int(r["Sin facturar"])) if r["Sin facturar"] else "",
                      pesos(r["Facturado"]), pesos(r["Cobrado"]), pesos(r["Por cobrar"])])
        if "Falta" in est or "Cambió" in est:
            fondos[i] = "#FBE4E4"
        elif "Cerrado" in est:
            fondos[i] = "#EEF8F0"
    filas.append(["Total", "", str(int(tabla_anio["Prestaciones"].sum())), str(int(tabla_anio["Sin facturar"].sum())),
                  pesos(tabla_anio["Facturado"].sum()), pesos(tabla_anio["Cobrado"].sum()),
                  pesos(tabla_anio["Por cobrar"].sum())])
    c.append(_tabla(filas, [2.8 * cm, 4.2 * cm, 1.3 * cm, 1.5 * cm, 2.6 * cm, 2.6 * cm, 2.6 * cm], total=True,
                    derecha_desde=2, fondos=fondos))
    t = nums_anio.get("por_obra_social")
    if isinstance(t, pd.DataFrame) and len(t):
        c.append(Paragraph("Obras sociales del año", e["h"]))
        c.append(_tabla(_detalle(t, "Obra social", 20), [6.2 * cm, 1.4 * cm, 2.4 * cm, 2.4 * cm, 2.4 * cm, 2.2 * cm]))
    t = nums_anio.get("por_medico")
    if isinstance(t, pd.DataFrame) and len(t):
        c.append(Paragraph("Médicos del año", e["h"]))
        c.append(_tabla(_detalle(t, "Médico", 12), [6.2 * cm, 1.4 * cm, 2.4 * cm, 2.4 * cm, 2.4 * cm, 2.2 * cm]))
    c += [Spacer(1, 8), Paragraph("Cerrado: el mes se controló y se cerró en Vitae. Falta resolver: hay cirugías sin "
                                  "cargar, prestaciones sin facturar o datos para completar.", e["nota"])]
    return _documento(c, titulo)
