"""Mamografía: unidad de negocio propia. Factura por el Círculo Médico de Salta (CMS) y maneja sus
propios ingresos y gastos.

Lo que se registra (una sola pestaña ``mamografia``, una fila por registro; ``registro`` dice qué es):
    Factura CMS    lo que se presenta por el CMS, por mes de facturación (Mesfac): N° de facturación,
                   presentación, tipo, formulario, obra social, período de la prestación, cons, importe.
    OSDE           prácticas de OSDE facturadas por Vitae: paciente, importe, fecha de prestación y de
                   facturación.
    Presentación   presentaciones al CMS: fecha, cantidad y tipo (NACIONALES, IPS…).
    Ingreso        lo que entra: CMS, OSDE, caja (particulares)… con medio (efectivo, banco).
    Gasto          lo que se paga: alquiler, sistema, sueldos, honorarios, service… con medio y mes al
                   que corresponde.

El resumen junta todo: resultado por mes (ingresos − gastos), facturado contra cobrado (CMS y OSDE),
gastos por categoría y obras sociales. El Panel general usa ``resumen``. Hay nombres de pacientes
(datos de salud): Luz no lee esta pestaña.
"""
from __future__ import annotations

import html
import io
import re
import uuid
from datetime import date, datetime
from typing import Any, Callable

import pandas as pd

from vitae.modulos.pagos_pendientes import _sin_acentos, _texto, nombre_mes, numero, pesos
from vitae.modulos.planes import mes_de
from vitae.modulos.vencimientos import fecha_txt, leer_fecha

TABLA = "mamografia"
COLUMNAS = ["id_registro", "registro", "fecha", "detalle", "obra_social", "numero", "periodo", "mes_facturacion",
            "tipo_doc", "formulario", "cons", "cantidad", "importe", "medio", "categoria", "fecha_facturacion",
            "observaciones", "updated_at"]
CMS, OSDE, PRESENTACION, INGRESO, GASTO = "Factura CMS", "OSDE", "Presentación", "Ingreso", "Gasto"
REGISTROS = [CMS, OSDE, PRESENTACION, INGRESO, GASTO]
CAT_INGRESO = ["CMS", "OSDE", "Caja (particulares)", "Otros"]
CAT_GASTO = ["Alquiler", "Sistema", "Sueldos", "Honorarios médicos", "Service y mantenimiento", "Insumos",
             "Impuestos", "Otros"]
MEDIOS = ["", "Efectivo", "Banco", "Banco Galicia", "Banco Macro", "Mercado Pago", "Transferencia"]
SECCIONES = ["Resumen", "Facturación CMS", "OSDE", "Ingresos y gastos", "Presentaciones", "Importar"]
_MESES_TXT = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
              "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _id() -> str:
    return uuid.uuid4().hex[:12]


def _ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _n(v: Any) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return 0.0 if pd.isna(v) else float(v)
    x = numero(v)
    return float(x) if x is not None else 0.0


def periodo_de(v: Any) -> str:
    """082026, 08/2026, 2026-08, «agosto 2026», una fecha… → '2026-08'."""
    if v is None:
        return ""
    if isinstance(v, float) and not pd.isna(v) and v.is_integer():
        v = int(v)
    t = _texto(v).strip()
    if re.fullmatch(r"\d{5,6}", t):
        t = t.zfill(6)
        m, a = int(t[:2]), int(t[2:])
        return f"{a:04d}-{m:02d}" if 1 <= m <= 12 and a > 1900 else ""
    return mes_de(v)


def periodo_en_texto(texto: Any) -> str:
    """«SISTEMA PARA MAMOGRAFO. SEPTIEMBRE 2026» → '2026-09'."""
    n = _sin_acentos(texto)
    m = re.search(r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|"
                  r"diciembre)\s*(?:de\s*)?(\d{4})", n)
    return f"{int(m[2]):04d}-{_MESES_TXT[m[1]]:02d}" if m else ""


def periodo_txt(p: str) -> str:
    return f"{p[5:7]}/{p[:4]}" if p else ""


def mes_largo(p: str) -> str:
    return nombre_mes(p).lower() if p else ""


def categoria_ingreso(detalle: Any, medio: Any = "") -> str:
    n = _sin_acentos(detalle)
    if "cms" in n or "circulo" in n:
        return "CMS"
    if "osde" in n:
        return "OSDE"
    if "caja" in n or "particular" in n or "efectivo" in _sin_acentos(medio) and "cms" not in n:
        return "Caja (particulares)"
    return "Otros"


def categoria_gasto(detalle: Any) -> str:
    n = _sin_acentos(detalle)
    for cat, palabras in (("Alquiler", ("alquiler",)), ("Sistema", ("sistema", "software", "licencia")),
                          ("Sueldos", ("sueldo", "aguinaldo", "sac ", "cargas sociales", "f931", "vacaciones")),
                          ("Honorarios médicos", ("honorario", "dra ", "dr ")),
                          ("Service y mantenimiento", ("service", "mantenimiento", "reparacion", "impresora", "tecnico")),
                          ("Insumos", ("insumo", "placa", "papel", "toner", "film")),
                          ("Impuestos", ("impuesto", "afip", "arca", "dgr", "iibb", "tasa"))):
        if any(p in f" {n} " for p in palabras):
            return cat
    return "Otros"


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------
def _col(df: pd.DataFrame, *nombres: str) -> str | None:
    mapa = {re.sub(r"[^a-z0-9]+", "_", _sin_acentos(c)).strip("_"): c for c in df.columns}
    return next((mapa[n] for n in nombres if n in mapa), None)


def _registro(v: Any) -> str:
    n = _sin_acentos(v)
    if "cms" in n or "factura" in n:
        return CMS
    if "osde" in n:
        return OSDE
    if "present" in n:
        return PRESENTACION
    if "ingreso" in n or "cobro" in n:
        return INGRESO
    if "gasto" in n or "pago" in n or "egreso" in n:
        return GASTO
    return ""


def normalizar(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=COLUMNAS)
    from vitae.modulos.alquileres import sin_columnas_repetidas

    df = sin_columnas_repetidas(df.reset_index(drop=True))

    def serie(*nombres: str) -> pd.Series:
        c = _col(df, *nombres)
        return df[c].map(_texto) if c else pd.Series("", index=df.index, dtype="object")

    d = pd.DataFrame(index=df.index)
    d["id_registro"] = [x or _id() for x in serie("id_registro")]
    d["registro"] = serie("registro", "tipo_registro").map(_registro)
    d["fecha"] = serie("fecha").map(fecha_txt)
    for c in ("detalle", "obra_social", "numero", "tipo_doc", "formulario", "cons", "medio", "categoria",
              "observaciones", "updated_at"):
        d[c] = serie(c)
    d["periodo"] = serie("periodo").map(periodo_de)
    d["mes_facturacion"] = serie("mes_facturacion").map(periodo_de)
    d["fecha_facturacion"] = serie("fecha_facturacion").map(fecha_txt)
    for c in ("cantidad", "importe"):
        col = _col(df, c)
        d[c] = df[col].map(_n) if col else 0.0
    d = d[d["registro"] != ""].copy()
    # Completar lo que se deduce
    vacia = d["categoria"] == ""
    cat_i = pd.Series([categoria_ingreso(a, b) for a, b in zip(d["detalle"], d["medio"])], index=d.index, dtype="object")
    cat_g = d["detalle"].map(categoria_gasto)
    d["categoria"] = d["categoria"].where(~(vacia & (d["registro"] == INGRESO)), cat_i)
    d["categoria"] = d["categoria"].where(~(vacia & (d["registro"] == GASTO)), cat_g)
    mes_f = d["fecha"].map(lambda f: mes_de(leer_fecha(f)))
    d["mes_facturacion"] = d["mes_facturacion"].where(~((d["registro"] == CMS) & (d["mes_facturacion"] == "")), mes_f)
    d["obra_social"] = d["obra_social"].where(~((d["registro"] == OSDE) & (d["obra_social"] == "")), "OSDE")
    return d[COLUMNAS].reset_index(drop=True)


def para_guardar(d: pd.DataFrame) -> pd.DataFrame:
    out = d[COLUMNAS].copy()
    out["importe"] = out["importe"].map(lambda v: f"{float(v or 0):.2f}")
    out["cantidad"] = out["cantidad"].map(lambda v: f"{float(v):g}" if float(v or 0) else "")
    out["updated_at"] = out["updated_at"].replace("", _ahora())
    orden = {r: i for i, r in enumerate(REGISTROS)}
    out = out.assign(_o=out["registro"].map(orden), _f=[leer_fecha(f) for f in out["fecha"]])
    return out.sort_values(["_o", "_f"], kind="stable").drop(columns=["_o", "_f"]).reset_index(drop=True)


def de(d: pd.DataFrame, registro: str) -> pd.DataFrame:
    x = d[d["registro"] == registro].copy()
    x["_fecha"] = pd.to_datetime([leer_fecha(f) for f in x["fecha"]], errors="coerce") if len(x) else \
        pd.Series(dtype="datetime64[ns]")
    x["_mes"] = [mes_de(f) if pd.notna(f) else "" for f in x["_fecha"]]
    return x


def mes_gasto(x: pd.DataFrame) -> pd.Series:
    """El mes al que corresponde cada gasto: el que dice el concepto, si no, el de la fecha."""
    return pd.Series([p or periodo_en_texto(d_) or m for p, d_, m in zip(x["periodo"], x["detalle"], x["_mes"])],
                     index=x.index, dtype="object")


def por_mes(d: pd.DataFrame) -> pd.DataFrame:
    """Por mes: facturado (CMS por mes de facturación, OSDE por fecha de facturación), cobrado por
    origen, gastos y resultado de caja (ingresos − gastos)."""
    cms, osde, ing, gas = de(d, CMS), de(d, OSDE), de(d, INGRESO), de(d, GASTO)
    osde_mes = pd.Series([mes_de(leer_fecha(f)) or m for f, m in zip(osde["fecha_facturacion"], osde["_mes"])],
                         index=osde.index, dtype="object")
    meses = sorted({m for m in list(cms["mes_facturacion"]) + list(osde_mes) + list(ing["_mes"]) + list(gas["_mes"]) if m})
    filas = []
    for m in meses:
        i = ing[ing["_mes"] == m]
        g = gas[gas["_mes"] == m]
        fila = {"_mes": m, "Mes": mes_largo(m),
                "Facturado CMS": float(cms.loc[cms["mes_facturacion"] == m, "importe"].sum()),
                "Facturado OSDE": float(osde.loc[osde_mes == m, "importe"].sum()),
                "Prácticas OSDE": int((osde_mes == m).sum()),
                "Cobrado CMS": float(i.loc[i["categoria"] == "CMS", "importe"].sum()),
                "Cobrado OSDE": float(i.loc[i["categoria"] == "OSDE", "importe"].sum()),
                "Otros ingresos": float(i.loc[~i["categoria"].isin(["CMS", "OSDE"]), "importe"].sum()),
                "Ingresos": float(i["importe"].sum()), "Gastos": float(g["importe"].sum())}
        fila["Resultado"] = fila["Ingresos"] - fila["Gastos"]
        filas.append(fila)
    return pd.DataFrame(filas)


def resumen(d: pd.DataFrame, hoy: date | None = None) -> dict[str, Any]:
    """Para la pantalla, el Panel general."""
    if d.empty:
        return {"hay": False}
    pm = por_mes(d)
    h = pd.Timestamp(hoy or date.today())
    ma = f"{h.year:04d}-{h.month:02d}"
    cms, osde, ing, gas = de(d, CMS), de(d, OSDE), de(d, INGRESO), de(d, GASTO)
    fila = pm[pm["_mes"] == ma].iloc[0].to_dict() if (pm["_mes"] == ma).any() else {}
    ant = pd.Period(ma, freq="M") - 1
    fila_ant = pm[pm["_mes"] == str(ant)].iloc[0].to_dict() if (pm["_mes"] == str(ant)).any() else {}
    fact_cms, cob_cms = float(cms["importe"].sum()), float(ing.loc[ing["categoria"] == "CMS", "importe"].sum())
    fact_osde, cob_osde = float(osde["importe"].sum()), float(ing.loc[ing["categoria"] == "OSDE", "importe"].sum())
    pres = de(d, PRESENTACION)
    ult_pres = pres.sort_values("_fecha").iloc[-1] if len(pres) and pres["_fecha"].notna().any() else None
    ult_mesfac = max([m for m in cms["mes_facturacion"] if m], default="")
    return {
        "hay": True, "mes": ma,
        "ingresos_mes": float(fila.get("Ingresos", 0.0)), "gastos_mes": float(fila.get("Gastos", 0.0)),
        "resultado_mes": float(fila.get("Resultado", 0.0)),
        "resultado_mes_anterior": float(fila_ant.get("Resultado", 0.0)) if fila_ant else None,
        "facturado_mes": float(fila.get("Facturado CMS", 0.0) + fila.get("Facturado OSDE", 0.0)),
        "facturado_cms": fact_cms, "cobrado_cms": cob_cms, "pendiente_cms": max(fact_cms - cob_cms, 0.0),
        "facturado_osde": fact_osde, "cobrado_osde": cob_osde, "pendiente_osde": max(fact_osde - cob_osde, 0.0),
        "practicas_osde": int(len(osde)),
        "ingresos_total": float(ing["importe"].sum()), "gastos_total": float(gas["importe"].sum()),
        "resultado_total": float(ing["importe"].sum() - gas["importe"].sum()),
        "ultima_presentacion": (f"{ult_pres['fecha']} · {int(ult_pres['cantidad'])} {ult_pres['detalle']}".strip()
                                if ult_pres is not None else ""),
        "ultimo_mesfac": ult_mesfac, "por_mes": pm,
    }


def gastos_por_categoria(d: pd.DataFrame, meses_sel: list[str] | None = None) -> pd.DataFrame:
    g = de(d, GASTO)
    if meses_sel:
        g = g[g["_mes"].isin(meses_sel)]
    if g.empty:
        return pd.DataFrame(columns=["Categoría", "Importe", "%"])
    t = g.groupby("categoria")["importe"].sum().sort_values(ascending=False)
    return pd.DataFrame({"Categoría": t.index, "Importe": t.values, "%": (t.values / t.sum() * 100).round(1)})


def obras_sociales(d: pd.DataFrame) -> pd.DataFrame:
    c = de(d, CMS)
    if c.empty:
        return pd.DataFrame(columns=["Obra social", "Facturas", "Importe", "%"])
    t = c.groupby("obra_social").agg(Facturas=("importe", "size"), Importe=("importe", "sum")).sort_values(
        "Importe", ascending=False)
    t["%"] = (t["Importe"] / t["Importe"].sum() * 100).round(1)
    return t.reset_index().rename(columns={"obra_social": "Obra social"})


def hallazgos(d: pd.DataFrame, hoy: date | None = None) -> list[str]:
    r = resumen(d, hoy)
    if not r.get("hay"):
        return []
    out = []
    pm = r["por_mes"]
    cerrados = pm[pm["_mes"] < r["mes"]]
    if len(cerrados):
        u = cerrados.iloc[-1]
        out.append(f"{u['Mes'].capitalize()}: entraron {pesos(u['Ingresos'], 0)}, se gastaron {pesos(u['Gastos'], 0)} "
                   f"→ resultado {pesos(u['Resultado'], 0)}.")
    if r["pendiente_cms"] > 0.5:
        out.append(f"CMS: facturado {pesos(r['facturado_cms'], 0)}, cobrado {pesos(r['cobrado_cms'], 0)} → falta "
                   f"cobrar {pesos(r['pendiente_cms'], 0)} (incluye lo que retiene el CMS).")
    if r["pendiente_osde"] > 0.5:
        out.append(f"OSDE: {r['practicas_osde']} prácticas facturadas por {pesos(r['facturado_osde'], 0)}; cobrado "
                   f"{pesos(r['cobrado_osde'], 0)} → falta {pesos(r['pendiente_osde'], 0)}.")
    gc = gastos_por_categoria(d)
    if len(gc):
        out.append("Gastos: " + ", ".join(f"{c} {p:.0f}%" for c, p in zip(gc["Categoría"].head(3), gc["%"].head(3))) + ".")
    os_ = obras_sociales(d)
    if len(os_):
        out.append(f"La obra social que más factura por el CMS: {os_.iloc[0]['Obra social']} "
                   f"({os_.iloc[0]['%']:.0f}%).")
    return out


# ---------------------------------------------------------------------------
# Tablas editables (cada registro con sus columnas)
# ---------------------------------------------------------------------------
VISTAS: dict[str, list[tuple[str, str, str]]] = {
    # (título en pantalla, campo, tipo)
    CMS: [("N° facturación", "numero", "texto"), ("Presentación", "fecha", "fecha"), ("Tipo", "tipo_doc", "texto"),
          ("Formulario", "formulario", "texto"), ("Obra social", "obra_social", "texto"),
          ("Período", "periodo", "periodo"), ("Cons", "cons", "texto"), ("Importe", "importe", "importe")],
    OSDE: [("Paciente", "detalle", "texto"), ("Facturado por Vitae", "importe", "importe"),
           ("Fecha de prestación", "fecha", "fecha"), ("Fecha de facturación", "fecha_facturacion", "fecha")],
    PRESENTACION: [("Fecha", "fecha", "fecha"), ("Cantidad", "cantidad", "numero"), ("Tipo", "detalle", "texto"),
                   ("Notas", "observaciones", "texto")],
    INGRESO: [("Fecha", "fecha", "fecha"), ("Concepto", "detalle", "texto"), ("Origen", "categoria", "cat_ingreso"),
              ("Importe", "importe", "importe"), ("Medio", "medio", "medio")],
    GASTO: [("Fecha", "fecha", "fecha"), ("Concepto", "detalle", "texto"), ("Categoría", "categoria", "cat_gasto"),
            ("Mes que corresponde", "periodo", "periodo"), ("Importe", "importe", "importe"), ("Medio", "medio", "medio")],
}


def tabla(d: pd.DataFrame, registro: str, filtro: Callable[[pd.DataFrame], pd.Series] | None = None) -> pd.DataFrame:
    x = de(d, registro)
    if filtro is not None and len(x):
        x = x[filtro(x)]
    x = x.sort_values("_fecha", kind="stable")
    out = {"id": list(x["id_registro"])}
    for titulo, campo, tipo in VISTAS[registro]:
        if tipo == "fecha":
            out[titulo] = [f.date() if pd.notna(f := leer_fecha(v)) else None for v in x[campo]]
        elif tipo == "periodo":
            out[titulo] = [periodo_txt(p) for p in x[campo]]
        elif tipo in ("importe", "numero"):
            out[titulo] = [float(v) for v in x[campo]]
        else:
            out[titulo] = list(x[campo])
    return pd.DataFrame(out)


def aplicar(d: pd.DataFrame, registro: str, original: pd.DataFrame, editada: pd.DataFrame,
            fijos: dict[str, Any] | None = None) -> tuple[pd.DataFrame, int]:
    """La tabla editada de un registro (cambios, filas nuevas y borradas) → los datos."""
    nuevo = d.copy()
    previos = set(original["id"]) if len(original) else set()
    vistos, cambios, filas_nuevas = set(), 0, []
    orig = original.set_index("id") if len(original) else None
    for _, f in editada.iterrows():
        i = _texto(f.get("id"))
        valores: dict[str, Any] = {}
        for titulo, campo, tipo in VISTAS[registro]:
            v = f.get(titulo)
            if tipo == "fecha":
                valores[campo] = fecha_txt(v)
            elif tipo == "periodo":
                valores[campo] = periodo_de(v)
            elif tipo in ("importe", "numero"):
                valores[campo] = _n(v)
            else:
                valores[campo] = _texto(v)
        if not any(valores[c] for c in valores if c not in ("importe", "cantidad")) and not valores.get("importe"):
            continue
        if registro == INGRESO and not valores.get("categoria"):
            valores["categoria"] = categoria_ingreso(valores.get("detalle"), valores.get("medio"))
        if registro == GASTO and not valores.get("categoria"):
            valores["categoria"] = categoria_gasto(valores.get("detalle"))
        if i and i in previos:
            vistos.add(i)
            antes = orig.loc[i]
            if any(str(f.get(t)) != str(antes.get(t)) for t, _, _ in VISTAS[registro]):
                m = nuevo["id_registro"] == i
                for c, v in valores.items():
                    nuevo.loc[m, c] = v
                nuevo.loc[m, "updated_at"] = _ahora()
                cambios += 1
        else:
            fila = {c: "" for c in COLUMNAS} | {"id_registro": _id(), "registro": registro, "importe": 0.0,
                                                 "cantidad": 0.0, "updated_at": _ahora()} | (fijos or {}) | valores
            filas_nuevas.append(fila)
            cambios += 1
    borrados = previos - vistos
    if borrados:
        nuevo = nuevo[~nuevo["id_registro"].isin(borrados)]
        cambios += len(borrados)
    if filas_nuevas:
        nuevo = pd.concat([nuevo, pd.DataFrame(filas_nuevas, columns=COLUMNAS)], ignore_index=True)
    return nuevo.reset_index(drop=True), cambios


# ---------------------------------------------------------------------------
# Importar las planillas (Excel o pegado): reconoce cada bloque
# ---------------------------------------------------------------------------
def _celdas(fila: list[Any]) -> list[Any]:
    return [c for c in fila if _texto(c) != ""]


def leer_tabla(valores: list[list[Any]]) -> dict[str, Any]:
    """Lee las planillas de registro de Mamografía, en uno o varios bloques:
    - «Año: 2026, Mesfac: 9» + N° facturación · Presentación · Tipo · Formulario · Obra Social · Periodo · Cons · Importe
    - PACIENTES · FACTURADO POR VITAE · FECHA DE PRESTACIÓN · FECHA DE FACTURACIÓN (OSDE)
    - Presentaciones CMS: FECHA · CANT · tipo
    - INGRESOS / GASTOS: fecha · concepto · importe · medio
    """
    regs: list[dict[str, Any]] = []
    modo, cols, mesfac, anio = None, {}, "", None
    for fila in valores:
        textos = [_sin_acentos(c) for c in fila]
        unido = " ".join(t for t in textos if t)
        if not unido:
            continue
        m = re.search(r"(\d{4})?\D{0,12}mes\s*fac\D{0,3}(\d{1,2})", unido)
        if m and "mesfac" in unido.replace(" ", ""):
            anio = int(m[1]) if m[1] else (anio or None)
            mesfac = f"{anio or 0:04d}-{int(m[2]):02d}" if anio else f"????-{int(m[2]):02d}"
            modo = None
            continue
        if any("presentacion" in t for t in textos) and any("obra social" in t for t in textos):
            modo, cols = CMS, {}
            for j, t in enumerate(textos):
                for clave, patron in (("numero", r"facturacion|^n"), ("fecha", r"presentacion"), ("tipo_doc", r"^tipo"),
                                      ("formulario", r"formulario"), ("obra_social", r"obra social"),
                                      ("periodo", r"periodo"), ("cons", r"^cons"), ("importe", r"importe")):
                    if re.search(patron, t) and clave not in cols:
                        cols[clave] = j
                        break
            continue
        if any(t.startswith("pacientes") for t in textos) and any("factur" in t for t in textos):
            modo, cols = OSDE, {}
            for j, t in enumerate(textos):
                if t.startswith("paciente"):
                    cols["detalle"] = j
                elif "facturado" in t:
                    cols["importe"] = j
                elif "prestac" in t:
                    cols["fecha"] = j
                elif "fecha" in t and "factur" in t:
                    cols["fecha_facturacion"] = j
            continue
        if unido.startswith("presentaciones"):
            modo, cols = PRESENTACION, {}
            continue
        if unido in {"ingresos", "ingreso"}:
            modo = INGRESO
            continue
        if unido in {"gastos", "gasto", "egresos"}:
            modo = GASTO
            continue
        if modo == PRESENTACION and "fecha" in textos and any(t.startswith("cant") for t in textos):
            continue
        if modo is None:
            continue
        if modo == CMS:
            def cel(k: str) -> Any:
                j = cols.get(k)
                return fila[j] if j is not None and j < len(fila) else None
            num = _texto(cel("numero"))
            if not re.search(r"\d", num) or pd.isna(leer_fecha(cel("fecha"))):
                if _n(cel("importe")) and not num:
                    continue  # fila de total
                continue
            per = periodo_de(cel("periodo"))
            mf = mesfac
            if mf.startswith("????"):
                f_ = leer_fecha(cel("fecha"))
                mf = f"{f_.year:04d}{mf[4:]}" if pd.notna(f_) else ""
            regs.append({"registro": CMS, "numero": re.sub(r"\.0$", "", num), "fecha": fecha_txt(cel("fecha")),
                         "tipo_doc": _texto(cel("tipo_doc")), "formulario": _texto(cel("formulario")),
                         "obra_social": _texto(cel("obra_social")), "periodo": per,
                         "cons": "" if _texto(cel("cons")) in ("-", "") else _texto(cel("cons")),
                         "importe": _n(cel("importe")), "mes_facturacion": mf or mes_de(leer_fecha(cel("fecha")))})
            continue
        if modo == OSDE:
            pac = _texto(fila[cols["detalle"]]) if cols.get("detalle") is not None and cols["detalle"] < len(fila) else ""
            if not pac or _sin_acentos(pac).startswith("total"):
                continue
            g = lambda k: fila[cols[k]] if cols.get(k) is not None and cols[k] < len(fila) else None  # noqa: E731
            regs.append({"registro": OSDE, "detalle": pac, "importe": _n(g("importe")), "fecha": fecha_txt(g("fecha")),
                         "fecha_facturacion": fecha_txt(g("fecha_facturacion")), "obra_social": "OSDE"})
            continue
        celdas = _celdas(fila)
        if not celdas or pd.isna(leer_fecha(celdas[0])):
            continue
        fecha = fecha_txt(celdas[0])
        resto = celdas[1:]
        if modo == PRESENTACION:
            cant = next((_n(c) for c in resto if re.fullmatch(r"\d+([.,]0+)?", _texto(c))), 0.0)
            tipo = " ".join(_texto(c) for c in resto if not re.fullmatch(r"\d+([.,]0+)?", _texto(c)))
            m2 = re.match(r"(\d+)\s+(.+)", tipo)
            if not cant and m2:
                cant, tipo = float(m2[1]), m2[2]
            regs.append({"registro": PRESENTACION, "fecha": fecha, "cantidad": cant, "detalle": tipo.strip()})
            continue
        importe_j = next((j for j in range(len(resto) - 1, -1, -1) if re.search(r"\d", _texto(resto[j]))
                          and numero(resto[j]) is not None and not re.fullmatch(r"\d{4}", _texto(resto[j]))), None)
        if importe_j is None:
            continue
        concepto = " ".join(_texto(c) for c in resto[:importe_j])
        medio = " ".join(_texto(c) for c in resto[importe_j + 1:])
        reg = {"registro": modo, "fecha": fecha, "detalle": concepto.strip(), "importe": _n(resto[importe_j]),
               "medio": medio.strip().title() if medio.isupper() else medio.strip()}
        if modo == INGRESO:
            reg["categoria"] = categoria_ingreso(concepto, medio)
        else:
            reg["categoria"] = categoria_gasto(concepto)
            reg["periodo"] = periodo_en_texto(concepto)
        regs.append(reg)
    cuenta = {r: sum(1 for x in regs if x["registro"] == r) for r in REGISTROS}
    return {"registros": regs, "cuenta": cuenta}


def _clave(r: dict[str, Any] | pd.Series) -> str:
    reg = r["registro"]
    if reg == CMS:
        return f"{reg}|{_texto(r['numero'])}"
    if reg == OSDE:
        return f"{reg}|{_sin_acentos(r['detalle'])}|{r['fecha']}"
    if reg == PRESENTACION:
        return f"{reg}|{r['fecha']}|{_sin_acentos(r['detalle'])}"
    return f"{reg}|{r['fecha']}|{_sin_acentos(r['detalle'])}|{float(r['importe']):.2f}"


def leer_pegado(texto: str) -> list[list[Any]]:
    filas = [ln.split("\t") for ln in (texto or "").splitlines()]
    return [[c.strip() for c in f] for f in filas]


def leer_archivo(nombre: str, contenido: bytes, hoja: str | None = None) -> tuple[list[list[Any]], list[str]]:
    n = (nombre or "").lower()
    if n.endswith((".csv", ".txt")):
        import csv

        texto = contenido.decode("utf-8-sig", errors="replace")
        sep = "\t" if "\t" in texto.splitlines()[0] else (";" if texto.count(";") > texto.count(",") else ",")
        return [f for f in csv.reader(io.StringIO(texto), delimiter=sep)], []
    xl = pd.ExcelFile(io.BytesIO(contenido))
    hojas = xl.sheet_names
    df = xl.parse(hoja if hoja in hojas else hojas[0], header=None)
    return df.astype(object).where(df.notna(), None).values.tolist(), hojas


def plan_importacion(actual: pd.DataFrame, leido: dict[str, Any]) -> tuple[pd.DataFrame, int, int]:
    """Agrega lo nuevo; lo que ya está (mismo N° de facturación, mismo paciente y fecha, mismo
    movimiento) no se duplica. Devuelve (datos, nuevos, repetidos)."""
    act = actual.copy() if set(COLUMNAS) <= set(actual.columns) else normalizar(actual)
    ya = {_clave(r) for _, r in act.iterrows()}
    nuevos, repetidos = [], 0
    for r in leido.get("registros", []):
        fila = {c: "" for c in COLUMNAS} | {"id_registro": _id(), "importe": 0.0, "cantidad": 0.0,
                                             "updated_at": _ahora()} | r
        k = _clave(fila)
        if k in ya:
            repetidos += 1
            continue
        ya.add(k)
        nuevos.append(fila)
    out = pd.concat([act, pd.DataFrame(nuevos, columns=COLUMNAS)], ignore_index=True) if nuevos else act
    return out, len(nuevos), repetidos


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
def excel(d: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as w:
        pm = por_mes(d)
        if len(pm):
            pm.drop(columns="_mes").to_excel(w, sheet_name="Resumen por mes", index=False)
        for reg, hoja in ((CMS, "Facturación CMS"), (OSDE, "OSDE"), (INGRESO, "Ingresos"), (GASTO, "Gastos"),
                          (PRESENTACION, "Presentaciones")):
            t = tabla(d, reg).drop(columns="id")
            if reg == CMS and len(t):
                x = de(d, CMS).sort_values("_fecha", kind="stable")
                t.insert(0, "Mes facturación", [mes_largo(m) for m in x["mes_facturacion"]])
            t.to_excel(w, sheet_name=hoja, index=False)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------
_CSS = """<style>
.mg-h{font-weight:800;color:#5E1A39;margin:.7rem 0 .2rem;font-size:1rem}
.mg-sub{color:#8A7F85;font-size:.8rem;margin-bottom:.3rem}
.mg-hall{background:#FBF8F9;border:1px solid #EFE7EB;border-radius:12px;padding:.6rem .9rem;margin:.2rem 0 .8rem}
.mg-hall li{margin:.15rem 0;color:#3A1426}
.mg-tot{display:flex;justify-content:flex-end;gap:1.2rem;font-weight:800;color:#3A1426;margin:-.2rem 0 .6rem;font-size:.92rem}
</style>"""


def _config(st: Any, registro: str) -> dict[str, Any]:
    cfg: dict[str, Any] = {"id": None}
    for titulo, _, tipo in VISTAS[registro]:
        if tipo == "fecha":
            cfg[titulo] = st.column_config.DateColumn(format="DD/MM/YYYY")
        elif tipo == "importe":
            cfg[titulo] = st.column_config.NumberColumn(format="localized", min_value=0.0)
        elif tipo == "numero":
            cfg[titulo] = st.column_config.NumberColumn(format="%d", min_value=0, step=1)
        elif tipo == "periodo":
            cfg[titulo] = st.column_config.TextColumn(help="MM/AAAA (ej.: 08/2026)")
        elif tipo == "cat_ingreso":
            cfg[titulo] = st.column_config.SelectboxColumn(options=CAT_INGRESO)
        elif tipo == "cat_gasto":
            cfg[titulo] = st.column_config.SelectboxColumn(options=CAT_GASTO)
        elif tipo == "medio":
            cfg[titulo] = st.column_config.SelectboxColumn(options=MEDIOS)
    return cfg


def render_mamografia(raw: pd.DataFrame | None, *, guardar: Callable[[pd.DataFrame], Any],
                      selector: Callable[..., str] | None = None,
                      mostrar_error: Callable[[str, Exception], None] | None = None,
                      hoy: date | None = None) -> None:
    import streamlit as st

    from vitae.core.ui import tarjetas_kpi

    d = normalizar(raw)
    hoy = hoy or date.today()
    k = "mg_"
    st.markdown(_CSS, unsafe_allow_html=True)
    rev = int(st.session_state.get(f"{k}rev", 0))

    def _guardar(nuevo: pd.DataFrame, aviso: str) -> None:
        try:
            guardar(para_guardar(nuevo))
        except Exception as exc:
            (mostrar_error or (lambda m, e: st.error(f"{m} {e}")))("No se pudieron guardar los cambios.", exc)
            return
        st.session_state[f"{k}rev"] = rev + 1
        st.toast(aviso, icon=":material/check_circle:")
        st.rerun()

    def _editor(registro: str, filtro: Callable[[pd.DataFrame], pd.Series] | None, clave: str,
                fijos: dict[str, Any] | None = None, ayuda: str = "") -> pd.DataFrame:
        original = tabla(d, registro, filtro)
        if ayuda:
            st.caption(ayuda)
        ed = st.data_editor(original, hide_index=True, use_container_width=True, num_rows="dynamic",
                            key=f"{k}{clave}_{rev}", column_config=_config(st, registro))
        if st.button("Guardar cambios", type="primary", key=f"{k}{clave}_ok", icon=":material/save:"):
            nuevo, n = aplicar(d, registro, original, ed, fijos)
            if n:
                _guardar(nuevo, f"{n} cambio(s) guardado(s)")
            else:
                st.info("No hay cambios.")
        return ed

    r = resumen(d, hoy)
    if r.get("hay"):
        mt = mes_largo(r["mes"])
        tarjetas_kpi([
            (f"Resultado de {mt}", pesos(r["resultado_mes"], 0),
             f"Entró {pesos(r['ingresos_mes'], 0)} · se gastó {pesos(r['gastos_mes'], 0)}",
             "#5E9C76" if r["resultado_mes"] >= 0 else "#BD4B4B"),
            ("CMS por cobrar", pesos(r["pendiente_cms"], 0),
             f"Facturado {pesos(r['facturado_cms'], 0)} · cobrado {pesos(r['cobrado_cms'], 0)}", "#8C2853"),
            ("OSDE por cobrar", pesos(r["pendiente_osde"], 0),
             f"{r['practicas_osde']} prácticas · cobrado {pesos(r['cobrado_osde'], 0)}", "#4F7CAC"),
            ("Resultado acumulado", pesos(r["resultado_total"], 0),
             f"Última presentación: {r['ultima_presentacion'] or '—'}", "#C9895B"),
        ])

    if selector:
        seccion = selector("Sección", SECCIONES, key=f"{k}seccion")
    else:
        seccion = st.segmented_control("Sección", SECCIONES, default=SECCIONES[0], key=f"{k}seccion",
                                       label_visibility="collapsed") or SECCIONES[0]

    # ---- Resumen -----------------------------------------------------------------
    if seccion == "Resumen":
        if d.empty:
            st.info("Todavía no hay registros. Traé las planillas en «Importar» (se pueden pegar todas juntas) "
                    "o cargá directamente en cada sección.")
            return
        hl = hallazgos(d, hoy)
        if hl:
            st.markdown('<div class="mg-hall"><ul style="margin:0;padding-left:1.1rem">' +
                        "".join(f"<li>{html.escape(h_)}</li>" for h_ in hl) + "</ul></div>", unsafe_allow_html=True)
        pm = r["por_mes"]
        if len(pm):
            st.markdown('<div class="mg-h">Mes a mes</div>', unsafe_allow_html=True)
            st.bar_chart(pm.set_index("Mes")[["Ingresos", "Gastos"]], stack=False, color=["#5E9C76", "#BD4B4B"])
            money = [c for c in pm.columns if c not in ("_mes", "Mes", "Prácticas OSDE")]
            st.dataframe(pm.drop(columns="_mes").assign(**{c: pm[c].map(lambda v: pesos(v, 0)) for c in money}),
                         hide_index=True, use_container_width=True)
            st.caption("Facturado: CMS por mes de facturación (Mesfac) y OSDE por fecha de facturación. Ingresos y "
                       "gastos por la fecha en que entró o salió la plata.")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown('<div class="mg-h">Gastos por categoría</div>', unsafe_allow_html=True)
            gc = gastos_por_categoria(d)
            st.dataframe(gc.assign(Importe=gc["Importe"].map(lambda v: pesos(v, 0)),
                                   **{"%": gc["%"].map(lambda v: f"{v:.0f}%")}), hide_index=True,
                         use_container_width=True)
        with c2:
            st.markdown('<div class="mg-h">Obras sociales (CMS)</div>', unsafe_allow_html=True)
            os_ = obras_sociales(d)
            st.dataframe(os_.assign(Importe=os_["Importe"].map(lambda v: pesos(v, 0)),
                                    **{"%": os_["%"].map(lambda v: f"{v:.0f}%")}), hide_index=True,
                         use_container_width=True)
        st.download_button("Descargar Excel", data=excel(d), file_name=f"mamografia_{hoy:%Y%m%d}.xlsx", key=f"{k}xls",
                           icon=":material/download:",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        return

    # ---- Facturación CMS ---------------------------------------------------------
    if seccion == "Facturación CMS":
        cms = de(d, CMS)
        meses_f = sorted({m for m in cms["mes_facturacion"] if m}, reverse=True)
        ma = f"{hoy.year:04d}-{hoy.month:02d}"
        opciones = sorted(set(meses_f) | {ma}, reverse=True)
        mes = st.selectbox("Mes de facturación (Mesfac)", opciones, index=0, key=f"{k}cms_mes", format_func=mes_largo)
        x = cms[cms["mes_facturacion"] == mes]
        st.markdown(f'<div class="mg-tot"><span>{len(x)} facturas</span><span>Total {pesos(x["importe"].sum())}</span></div>',
                    unsafe_allow_html=True)
        _editor(CMS, lambda t: t["mes_facturacion"] == mes, f"cms_{mes}", {"mes_facturacion": mes},
                "Como la planilla del CMS. Agregá filas abajo (quedan en el mes elegido) o cambiá cualquier dato.")
        if len(meses_f) > 1:
            st.markdown('<div class="mg-h">Total por mes de facturación</div>', unsafe_allow_html=True)
            t = cms.groupby("mes_facturacion").agg(Facturas=("importe", "size"), Total=("importe", "sum")).reset_index()
            t = t.sort_values("mes_facturacion", ascending=False)
            st.dataframe(pd.DataFrame({"Mes": t["mes_facturacion"].map(mes_largo), "Facturas": t["Facturas"],
                                       "Total": t["Total"].map(pesos)}), hide_index=True, use_container_width=True)
        return

    # ---- OSDE ----------------------------------------------------------------------
    if seccion == "OSDE":
        o = de(d, OSDE)
        st.markdown(f'<div class="mg-tot"><span>{len(o)} prácticas</span><span>Total {pesos(o["importe"].sum())}</span>'
                    f'<span>Cobrado {pesos(r.get("cobrado_osde", 0.0))}</span></div>', unsafe_allow_html=True)
        _editor(OSDE, None, "osde", {"obra_social": "OSDE"},
                "Prácticas de OSDE facturadas por Vitae. Agregá cada paciente con lo facturado y las fechas.")
        if len(o):
            o_f = pd.Series([mes_de(leer_fecha(f)) for f in o["fecha_facturacion"]], index=o.index)
            t = o.assign(_mf=o_f).groupby("_mf").agg(Prácticas=("importe", "size"), Total=("importe", "sum")).reset_index()
            st.markdown('<div class="mg-h">Por mes de facturación</div>', unsafe_allow_html=True)
            st.dataframe(pd.DataFrame({"Mes": t["_mf"].map(lambda m: mes_largo(m) or "sin fecha"),
                                       "Prácticas": t["Prácticas"], "Total": t["Total"].map(pesos)}),
                         hide_index=True, use_container_width=True)
        return

    # ---- Ingresos y gastos ---------------------------------------------------------
    if seccion == "Ingresos y gastos":
        ing, gas = de(d, INGRESO), de(d, GASTO)
        meses_m = sorted({m for m in list(ing["_mes"]) + list(gas["_mes"]) if m}, reverse=True)
        opciones = ["Todos"] + meses_m
        mes = st.selectbox("Mes", opciones, index=0, key=f"{k}ig_mes",
                           format_func=lambda m: "Todos los meses" if m == "Todos" else mes_largo(m))
        filtro = None if mes == "Todos" else (lambda t, m=mes: t["_mes"] == m)
        i_v = ing if mes == "Todos" else ing[ing["_mes"] == mes]
        g_v = gas if mes == "Todos" else gas[gas["_mes"] == mes]
        st.markdown(f'<div class="mg-tot"><span>Ingresos {pesos(i_v["importe"].sum())}</span>'
                    f'<span>Gastos {pesos(g_v["importe"].sum())}</span>'
                    f'<span>Resultado {pesos(i_v["importe"].sum() - g_v["importe"].sum())}</span></div>',
                    unsafe_allow_html=True)
        t1, t2 = st.tabs(["Ingresos", "Gastos"])
        with t1:
            _editor(INGRESO, filtro, f"ing_{mes}", None,
                    "Lo que entra (CMS, OSDE, caja de particulares…). El origen se completa solo si lo dejás vacío.")
        with t2:
            _editor(GASTO, filtro, f"gas_{mes}", None,
                    "Lo que se paga. La categoría y el mes que corresponde se completan solos si el concepto lo dice "
                    "(«ALQUILER PARA MAMOGRAFO. SEPTIEMBRE 2026»).")
        return

    # ---- Presentaciones -------------------------------------------------------------
    if seccion == "Presentaciones":
        p = de(d, PRESENTACION)
        st.markdown(f'<div class="mg-tot"><span>{len(p)} presentaciones</span>'
                    f'<span>{int(p["cantidad"].sum())} prestaciones presentadas</span></div>', unsafe_allow_html=True)
        _editor(PRESENTACION, None, "pres", None, "Presentaciones al CMS: fecha, cantidad y tipo (NACIONALES, IPS…).")
        return

    # ---- Importar -------------------------------------------------------------------
    if seccion == "Importar":
        st.caption("Copiá en Google Sheets los bloques de la planilla (facturación CMS con su «Año: 2026, Mesfac: 9», "
                   "pacientes de OSDE, presentaciones, INGRESOS y GASTOS) y pegalos acá: podés pegar todo junto. "
                   "También podés subir el Excel. Lo que ya está cargado no se duplica.")
        modo = st.pills("Desde", ["Pegar", "Archivo"], default="Pegar", key=f"{k}i_modo") or "Pegar"
        valores = None
        if modo == "Archivo":
            arch = st.file_uploader("Planilla de Mamografía", type=["xlsx", "xls", "csv"], key=f"{k}i_arch_{rev}")
            if arch is not None:
                try:
                    valores, hojas = leer_archivo(arch.name, arch.getvalue())
                    if len(hojas) > 1:
                        hoja = st.selectbox("Hoja", hojas, key=f"{k}i_hoja")
                        valores, _ = leer_archivo(arch.name, arch.getvalue(), hoja)
                except Exception as exc:
                    (mostrar_error or (lambda m, e: st.error(f"{m} {e}")))("No pude leer el archivo.", exc)
                    return
        else:
            texto = st.text_area("Pegá acá", height=240, key=f"{k}i_pegar_{rev}")
            if texto.strip():
                valores = leer_pegado(texto)
        if not valores:
            return
        leido = leer_tabla(valores)
        if not leido["registros"]:
            st.warning("No reconocí ningún bloque. Incluí los títulos (N° facturación / Presentación / Obra Social…, "
                       "PACIENTES, Presentaciones CMS, INGRESOS, GASTOS).")
            return
        nuevo, n_nuevos, n_rep = plan_importacion(d, leido)
        c = leido["cuenta"]
        st.markdown("**Encontré:** " + " · ".join(f"{v} {k_.lower() if k_ != OSDE else 'OSDE'}" for k_, v in c.items() if v)
                    + (f" — {n_rep} ya estaban" if n_rep else ""))
        prev = pd.DataFrame(leido["registros"])
        for reg in REGISTROS:
            x = prev[prev["registro"] == reg]
            if len(x):
                with st.expander(f"{reg} · {len(x)}", expanded=False):
                    st.dataframe(tabla(normalizar(x.assign(id_registro="")), reg).drop(columns="id"),
                                 hide_index=True, use_container_width=True)
        cms = prev[prev["registro"] == CMS]
        if len(cms) and cms["mes_facturacion"].str.startswith("????").any():
            st.warning("Falta el año del «Mesfac»: incluí la línea «Año: 2026, Mesfac: 9» al copiar.")
        if st.button(f"Guardar {n_nuevos} registro(s)", type="primary", key=f"{k}i_ok", icon=":material/upload:",
                     disabled=not n_nuevos):
            _guardar(nuevo, f"{n_nuevos} registro(s) importado(s)")
