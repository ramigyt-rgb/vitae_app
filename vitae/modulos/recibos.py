"""Recibo de pago en PDF para una operación de Caja (para entregar a la persona que pagó).

Hoja A4 con ORIGINAL arriba y DUPLICADO abajo (línea de corte): logo de Vitae, empresa, N° de recibo,
fecha, «Recibimos de», la suma en letras y en números, el concepto, la forma de pago y la firma.
"""
from __future__ import annotations

import io
from datetime import date
from typing import Any

import pandas as pd

BORDO = "#8C2853"
BORDO_OSCURO = "#5E1A39"
GRIS = "#6B5F66"
EMPRESAS = {"caja_vmr": ("VMR", "Vitae Medicina Reproductiva"), "caja_vm": ("VM", "Vitae Medical")}

_UNIDADES = ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce",
             "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte",
             "veintiuno", "veintidós", "veintitrés", "veinticuatro", "veinticinco", "veintiséis", "veintisiete",
             "veintiocho", "veintinueve"]
_DECENAS = ["", "", "", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"]
_CENTENAS = ["", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos", "seiscientos", "setecientos",
             "ochocientos", "novecientos"]


def _menos_de_mil(n: int) -> str:
    if n == 0:
        return ""
    if n == 100:
        return "cien"
    c, r = divmod(n, 100)
    partes = [_CENTENAS[c]] if c else []
    if r < 30:
        partes.append(_UNIDADES[r])
    else:
        d, u = divmod(r, 10)
        partes.append(_DECENAS[d] + (f" y {_UNIDADES[u]}" if u else ""))
    return " ".join(p for p in partes if p)


def _apocope(t: str) -> str:
    """«veintiuno mil» → «veintiún mil», «uno millón» → «un millón»."""
    if t.endswith("veintiuno"):
        return t[:-9] + "veintiún"
    if t.endswith("uno"):
        return t[:-3] + "un"
    return t


def numero_a_letras(n: int) -> str:
    """Entero en letras (castellano): 1.250.300 → «un millón doscientos cincuenta mil trescientos»."""
    n = int(n)
    if n == 0:
        return "cero"
    if n < 0:
        return "menos " + numero_a_letras(-n)
    partes = []
    millones, resto = divmod(n, 1_000_000)
    if millones:
        partes.append("un millón" if millones == 1 else f"{_apocope(numero_a_letras(millones))} millones")
    miles, unidades = divmod(resto, 1000)
    if miles:
        partes.append("mil" if miles == 1 else f"{_apocope(_menos_de_mil(miles))} mil")
    if unidades:
        partes.append(_menos_de_mil(unidades))
    return " ".join(partes)


def importe_en_letras(importe: float, moneda: str = "ARS") -> str:
    total = round(abs(float(importe or 0)), 2)
    enteros = int(total)
    centavos = int(round((total - enteros) * 100))
    if centavos == 100:
        enteros, centavos = enteros + 1, 0
    unidad = "dólares estadounidenses" if moneda == "USD" else "pesos"
    letras = numero_a_letras(enteros)
    return f"{unidad} {letras} con {centavos:02d}/100".capitalize()


def dinero(v: float, moneda: str = "ARS") -> str:
    s = f"{abs(float(v or 0)):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("US$ " if moneda == "USD" else "$ ") + s


def numero_recibo(tabla: str, fecha: Any, fila: int) -> str:
    """N° estable: empresa, año y la fila de la planilla (fila 1 = encabezado)."""
    pref = EMPRESAS.get(tabla, ("VITAE", "Vitae"))[0]
    f = pd.Timestamp(fecha) if fecha is not None and pd.notna(fecha) else pd.Timestamp(date.today())
    return f"{pref}-{f.year}-{int(fila) + 2:05d}"


def datos_recibo(r: pd.Series, *, tabla: str, fila: int, quien_es_pagador: bool = True) -> dict[str, Any]:
    """Los datos que propone el recibo para una fila preparada de Caja (``caja.preparar``)."""
    empresa = EMPRESAS.get(tabla, ("VITAE", "Vitae"))[1]
    fecha = r.get("_fecha")
    fecha = pd.Timestamp(fecha).date() if fecha is not None and pd.notna(fecha) else date.today()
    forma = str(r.get("_medio") or "").strip() or (str(r.get("_cuenta") or "").strip()
                                                    if str(r.get("_cuenta") or "").strip() not in ("", "Medicación")
                                                    else "") or "Efectivo"
    return {"empresa": empresa, "numero": numero_recibo(tabla, fecha, fila), "fecha": fecha,
            "recibimos_de": str(r.get("_quien") or "").strip() if quien_es_pagador else "", "documento": "",
            "concepto": str(r.get("_concepto") or "").strip(), "importe": float(r.get("_entrada") or 0.0),
            "moneda": str(r.get("_moneda") or "ARS"), "forma_pago": forma, "observaciones": ""}


def pdf_recibo(datos: dict[str, Any], logo: Any = None) -> bytes:
    """El PDF (A4: original y duplicado)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader, simpleSplit
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    ancho, alto = A4
    c.setTitle(f"Recibo {datos.get('numero', '')}")
    c.setAuthor(str(datos.get("empresa") or "Vitae"))
    img = None
    if logo is not None:
        try:
            img = ImageReader(str(logo) if not isinstance(logo, (bytes, bytearray)) else io.BytesIO(logo))
        except Exception:
            img = None
    bordo, oscuro, gris = colors.HexColor(BORDO), colors.HexColor(BORDO_OSCURO), colors.HexColor(GRIS)
    moneda = str(datos.get("moneda") or "ARS")
    fecha = datos.get("fecha")
    fecha_txt = pd.Timestamp(fecha).strftime("%d/%m/%Y") if fecha is not None and pd.notna(fecha) else ""
    margen = 14 * mm

    def recibo(y0: float, copia: str) -> None:
        h = alto / 2 - 2 * margen + 4 * mm
        x0, w = margen, ancho - 2 * margen
        top = y0 + h
        c.setStrokeColor(bordo)
        c.setLineWidth(1.2)
        c.roundRect(x0, y0, w, h, 4 * mm, stroke=1, fill=0)
        c.setFillColor(bordo)
        c.rect(x0 + 4 * mm, top - 1.6 * mm, w - 8 * mm, 1.6 * mm, stroke=0, fill=1)
        # Logo y empresa
        ly = top - 26 * mm
        if img is not None:
            iw, ih = img.getSize()
            lh = 19 * mm
            c.drawImage(img, x0 + 7 * mm, ly, width=lh * iw / ih, height=lh, mask="auto")
        else:
            c.setFont("Helvetica-Bold", 20)
            c.setFillColor(bordo)
            c.drawString(x0 + 7 * mm, ly + 6 * mm, "Vitae")
        c.setFont("Helvetica", 9)
        c.setFillColor(gris)
        c.drawString(x0 + 7 * mm, ly - 5 * mm, str(datos.get("empresa") or ""))
        # Título, número y fecha
        xr = x0 + w - 7 * mm
        c.setFillColor(oscuro)
        c.setFont("Helvetica-Bold", 17)
        c.drawRightString(xr, top - 13 * mm, "RECIBO DE PAGO")
        c.setFont("Helvetica", 9.5)
        c.setFillColor(gris)
        c.drawRightString(xr, top - 19 * mm, f"N° {datos.get('numero', '')}")
        c.drawRightString(xr, top - 24 * mm, f"Fecha: {fecha_txt}")
        c.setFont("Helvetica-Bold", 7.5)
        c.setFillColor(bordo)
        c.drawRightString(xr, top - 29 * mm, copia)
        # Importe destacado
        caja_w, caja_h = 62 * mm, 13 * mm
        cy = top - 49 * mm
        c.setFillColor(colors.HexColor("#F7EFF3"))
        c.setStrokeColor(colors.HexColor("#EADDE4"))
        c.roundRect(xr - caja_w, cy, caja_w, caja_h, 2.5 * mm, stroke=1, fill=1)
        c.setFillColor(oscuro)
        c.setFont("Helvetica-Bold", 15)
        c.drawCentredString(xr - caja_w / 2, cy + 4.3 * mm, dinero(datos.get("importe", 0), moneda))
        # Renglones
        y = top - 42 * mm
        renglones = [("Recibimos de", str(datos.get("recibimos_de") or ""))]
        if str(datos.get("documento") or "").strip():
            renglones.append(("DNI / CUIT", str(datos["documento"]).strip()))
        renglones += [("La suma de", importe_en_letras(datos.get("importe", 0), moneda)),
                      ("En concepto de", str(datos.get("concepto") or "")),
                      ("Forma de pago", str(datos.get("forma_pago") or ""))]
        if str(datos.get("observaciones") or "").strip():
            renglones.append(("Observaciones", str(datos["observaciones"]).strip()))
        ancho_valor = w - 14 * mm - 33 * mm - (caja_w + 6 * mm)
        for i, (etq, valor) in enumerate(renglones):
            disponible = ancho_valor if i < 2 else (w - 14 * mm - 33 * mm)
            lineas = simpleSplit(valor or " ", "Helvetica", 10.5, disponible)[:3]
            c.setFont("Helvetica-Bold", 8.5)
            c.setFillColor(gris)
            c.drawString(x0 + 7 * mm, y, etq.upper())
            c.setFont("Helvetica", 10.5)
            c.setFillColor(colors.HexColor("#1F1A1D"))
            for j, linea in enumerate(lineas):
                c.drawString(x0 + 40 * mm, y - j * 4.6 * mm, linea)
            fondo = y - (len(lineas) - 1) * 4.6 * mm - 1.8 * mm
            c.setStrokeColor(colors.HexColor("#E2D8DD"))
            c.setLineWidth(0.5)
            c.line(x0 + 40 * mm, fondo, x0 + 40 * mm + disponible, fondo)
            y = fondo - 6.2 * mm
            if i == 1:
                y = min(y, cy - 6 * mm)
        # Firma y pie
        fy = y0 + 17 * mm
        c.setStrokeColor(colors.HexColor("#9A8D93"))
        c.setLineWidth(0.6)
        c.line(xr - 62 * mm, fy, xr, fy)
        c.setFont("Helvetica", 8)
        c.setFillColor(gris)
        c.drawCentredString(xr - 31 * mm, fy - 4 * mm, f"Firma y aclaración · {datos.get('empresa') or 'Vitae'}")
        c.setFont("Helvetica-Oblique", 7.5)
        c.drawString(x0 + 7 * mm, y0 + 6 * mm, "Comprobante de pago. No válido como factura.")

    recibo(alto / 2 + margen - 4 * mm, "ORIGINAL")
    recibo(margen, "DUPLICADO")
    c.setDash(3, 3)
    c.setStrokeColor(colors.HexColor("#B9AEB4"))
    c.setLineWidth(0.6)
    c.line(8 * mm, alto / 2, ancho - 8 * mm, alto / 2)
    c.setDash()
    c.setFont("Helvetica", 6.5)
    c.setFillColor(colors.HexColor("#B9AEB4"))
    c.drawCentredString(ancho / 2, alto / 2 + 1.2 * mm, "cortar por la línea")
    c.showPage()
    c.save()
    return buf.getvalue()
