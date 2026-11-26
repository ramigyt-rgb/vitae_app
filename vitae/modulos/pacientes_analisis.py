"""Inteligencia de pacientes: todo lo que Vitae sabe de cada persona, cruzado y analizado.

Une la base de Pacientes con las prestaciones del resto de la app (Facturación VMR y VM,
Agenda de Quirófano, Gine Vitae, Farmacia y los formularios web de Gine) y calcula:
resumen ejecutivo con hallazgos, prestaciones y estudios, recorrido del paciente (embudo,
recurrencia, cohortes, secuencias), obras sociales, médicos, valor del paciente,
oportunidades de crecimiento y datos anonimizados para investigación.

Todo se calcula DENTRO de la app (nada va a la IA). Mismas reglas de privacidad que
vitae/modulos/pacientes.py.
"""
from __future__ import annotations

import functools
import io
import re
from datetime import date
from typing import Any

import pandas as pd

from vitae.modulos.pacientes import (RANGOS, _texto, edad, edades, norm, rango_edad, tokens_nombre)

try:
    from vitae.core.helpers import leer_fechas_ar, money
except Exception:  # pragma: no cover
    leer_fechas_ar = money = None

# ---------------------------------------------------------------------------
# Clasificación de prácticas (medicina reproductiva + ginecología)
# ---------------------------------------------------------------------------
CATEGORIAS: list[tuple[str, tuple[str, ...]]] = [
    ("Ovodonación", ("ovodon", "donacion de ovul", "donacion de ovocit", "receptora", "donante")),
    ("Genética embrionaria (PGT)", ("pgt", "pgs", "pgd", "genetic", "biopsia embrion", "cariotip", "nipt")),
    ("FIV / ICSI", ("fiv", "icsi", "in vitro", "fecundacion", "aspiracion folic", "puncion ovar", "puncion folic",
                    "aspiracion ovoc", "alta complejidad")),
    ("Transferencia embrionaria", ("transfer", "tec", "descongel")),
    ("Criopreservación", ("crio", "vitrif", "congel", "preservacion de fertilidad", "mantenimiento", "guarda de",
                          "almacenamiento")),
    ("Inseminación (IIU)", ("insemin", "iiu", "iac", "iad", "baja complejidad")),
    ("Andrología", ("esperm", "andrologi", "tesa", "tese", "biopsia testic", "capacitacion", "semen", "mesa")),
    ("Cirugía ginecológica", ("histeroscop", "laparoscop", "miomect", "conizac", "legrado", "polipect", "cirugi",
                              "quirurg", "ligadura", "salping", "quistect", "endometriosis", "biopsia")),
    ("Obstetricia", ("parto", "cesare", "embarazo", "obstet", "prenatal")),
    ("Estimulación y monitoreo", ("estimul", "monitoreo", "foliculom", "seguimiento folic", "induccion")),
    ("Ecografía e imágenes", ("ecograf", "eco", "doppler", "mamograf", "histerosalping", "hsg", "imagen", "resonan")),
    ("Laboratorio y estudios", ("laborat", "analisis", "hormon", "amh", "fsh", "beta", "hcg", "serolog", "pap", "papanicol", "citolog",
                                "colpo", "cultivo", "estudio", "perfil", "test", "panel")),
    ("Consulta", ("consulta", "control", "visita", "entrevista", "teleconsulta", "primera vez", "orientacion")),
]
TRATAMIENTOS = {"FIV / ICSI", "Ovodonación", "Inseminación (IIU)"}
ESTUDIOS = {"Laboratorio y estudios", "Ecografía e imágenes", "Andrología", "Estimulación y monitoreo",
            "Genética embrionaria (PGT)"}
OTROS = "Otras prácticas"


@functools.lru_cache(maxsize=20000)
def categoria(procedimiento: str) -> str:
    """Agrupa una práctica escrita a mano en una categoría (palabras cortas: palabra entera)."""
    t = norm(procedimiento)
    if not t or t in {"sin procedimiento", "sin dato"}:
        return OTROS
    for cat, claves in CATEGORIAS:
        for c in claves:
            if (re.search(rf"\b{re.escape(c)}\b", t) if len(c) <= 4 else c in t):
                return cat
    return OTROS


# ---------------------------------------------------------------------------
# Eventos: una fila por prestación, venga de donde venga
# ---------------------------------------------------------------------------
COLUMNAS_EVENTO = ["fuente", "empresa", "fecha", "nombre", "dni", "procedimiento", "categoria", "obra_social",
                   "medico", "importe", "cobrado", "pendiente", "fecha_factura", "fecha_pago", "estado"]


def _col(df: pd.DataFrame, *alias: str) -> str | None:
    nombres = {norm(c).replace(" ", "_"): c for c in df.columns}
    for a in alias:
        if a in nombres:
            return nombres[a]
    for a in alias:
        for n, c in nombres.items():
            if len(a) > 3 and a in n:
                return c
    return None


def _serie(df: pd.DataFrame, col: str | None) -> pd.Series:
    if col is None:
        return pd.Series([""] * len(df), index=df.index, dtype=object)
    return df[col].map(_texto)


def _fechas(serie: pd.Series) -> pd.Series:
    if leer_fechas_ar is not None:
        try:
            return leer_fechas_ar(serie)
        except Exception:
            pass
    return pd.to_datetime(serie, errors="coerce", dayfirst=True)


def _importes(serie: pd.Series) -> pd.Series:
    if money is not None:
        return serie.map(money).astype(float)
    return pd.to_numeric(serie, errors="coerce").fillna(0.0)


def _de_facturacion(df: pd.DataFrame, fuente: str, empresa: str) -> pd.DataFrame:
    import vitae.modulos.facturacion as fx

    data, _cols = fx._prepare_facturacion(df)
    data = data[~data["_cancelado"]] if "_cancelado" in data.columns else data
    return pd.DataFrame({
        "fuente": fuente, "empresa": empresa, "fecha": data["_fecha_base"], "nombre": data["_paciente"],
        "dni": "", "procedimiento": data["_procedimiento"].replace("SIN PROCEDIMIENTO", ""),
        "obra_social": data["_obra_social"].replace("SIN OBRA SOCIAL", ""),
        "medico": data["_medico"].replace("SIN MÉDICO", ""), "importe": data["_monto"].astype(float),
        "cobrado": data["_cobrado"].astype(float), "pendiente": data["_pendiente"].astype(float),
        "fecha_factura": data["_fecha_factura"], "fecha_pago": data["_fecha_pago"],
        "estado": data.get("_estado_original", ""),
    })


def _generico(df: pd.DataFrame, fuente: str, empresa: str, *, procedimiento_por_defecto: str = "",
              filtro_tipo: str = "") -> pd.DataFrame:
    c_nombre = _col(df, "paciente", "afiliado", "paciente_afiliado", "nombre_paciente", "nombre")
    if c_nombre is None:
        return pd.DataFrame(columns=COLUMNAS_EVENTO)
    if filtro_tipo:
        c_tipo = _col(df, "tipo", "movimiento")
        if c_tipo is not None:
            df = df[df[c_tipo].map(norm).str.contains(filtro_tipo, na=False)]
    c_ape = _col(df, "apellido", "apellidos")
    nombre = _serie(df, c_nombre)
    if c_ape is not None and c_ape != c_nombre:
        nombre = (_serie(df, c_ape) + " " + nombre).str.strip()
    proc = _serie(df, _col(df, "procedimiento", "practica", "prestacion", "tratamiento", "articulo", "detalle"))
    if procedimiento_por_defecto:
        proc = proc.where(proc != "", procedimiento_por_defecto)
    imp = _importes(_serie(df, _col(df, "importe", "monto", "valor_pesos", "total")))
    cob_col = _col(df, "cobrado", "pagado")
    cob = _importes(_serie(df, cob_col)) if cob_col else pd.Series(0.0, index=df.index)
    estado = _serie(df, _col(df, "estado"))
    cancelado = estado.map(norm).str.contains("cancel|anulad|suspend|no asist|ausente", regex=True, na=False)
    out = pd.DataFrame({
        "fuente": fuente, "empresa": empresa, "fecha": _fechas(_serie(df, _col(df, "fecha", "fecha_turno",
                                                                                  "fecha_cirugia"))),
        "nombre": nombre, "dni": _serie(df, _col(df, "dni", "documento", "nro_doc")).map(lambda v: re.sub(r"\D", "", v).lstrip("0")),
        "procedimiento": proc, "obra_social": _serie(df, _col(df, "obra_social", "cobertura", "prepaga")),
        "medico": _serie(df, _col(df, "medico", "medico_responsable", "profesional", "cirujano")),
        "importe": imp, "cobrado": cob.clip(upper=imp.where(imp > 0, cob)), "pendiente": (imp - cob).clip(lower=0),
        "fecha_factura": pd.NaT, "fecha_pago": pd.NaT, "estado": estado,
    })
    return out[~cancelado & (out["nombre"] != "")]


def _empresa_de(modulo: str, defecto: str) -> str:
    try:
        from vitae.core.tablas import MODULES

        return str(MODULES.get(modulo, {}).get("empresa") or defecto).upper()
    except Exception:  # pragma: no cover
        return defecto


def eventos_desde(tablas: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Todas las prestaciones de Vitae en un solo formato."""
    partes = []
    for nombre, df in (tablas or {}).items():
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        n = norm(nombre)
        try:
            if "factur" in n and n.endswith("vmr"):
                partes.append(_de_facturacion(df, "Facturación VMR", "VMR"))
            elif "factur" in n and n.endswith("vm"):
                partes.append(_de_facturacion(df, "Facturación VM", "VM"))
            elif "quirofano" in n or "agenda" in n:
                partes.append(_generico(df, "Quirófano", _empresa_de("Agenda Quirófano", "VITAE"),
                                        procedimiento_por_defecto="Cirugía"))
            elif "formularios" in n:
                continue  # son consultas web (leads), van aparte
            elif "gine" in n:
                partes.append(_generico(df, "Gine Vitae", _empresa_de("Gine Vitae", "VM")))
            elif "farmacia" in n and "movimiento" in n:
                partes.append(_generico(df, "Farmacia", _empresa_de("Farmacia", "VITAE"),
                                        procedimiento_por_defecto="Insumos de farmacia",
                                        filtro_tipo="paciente"))
        except Exception:
            continue
    if not partes:
        return pd.DataFrame(columns=COLUMNAS_EVENTO)
    ev = pd.concat(partes, ignore_index=True)
    ev["fecha"] = pd.to_datetime(ev["fecha"], errors="coerce")
    ev["procedimiento"] = ev["procedimiento"].map(_texto)
    ev["categoria"] = ev["procedimiento"].map(categoria)
    ev.loc[ev["fuente"] == "Farmacia", "categoria"] = "Insumos de farmacia"
    for c in ("obra_social", "medico", "nombre"):
        ev[c] = ev[c].map(_texto)
    return ev[COLUMNAS_EVENTO].reset_index(drop=True)


def leads_desde(tablas: dict[str, pd.DataFrame]) -> pd.DataFrame:
    for nombre, df in (tablas or {}).items():
        if "formularios" in norm(nombre) and isinstance(df, pd.DataFrame) and not df.empty:
            nom = (_serie(df, _col(df, "apellido")) + " " + _serie(df, _col(df, "nombre"))).str.strip()
            return pd.DataFrame({"fecha": _fechas(_serie(df, _col(df, "fecha"))), "nombre": nom,
                                 "edad": pd.to_numeric(_serie(df, _col(df, "edad")), errors="coerce"),
                                 "obra_social": _serie(df, _col(df, "obra_social")),
                                 "estado": _serie(df, _col(df, "estado"))})
    return pd.DataFrame(columns=["fecha", "nombre", "edad", "obra_social", "estado"])


# ---------------------------------------------------------------------------
# Vinculación con la base de pacientes
# ---------------------------------------------------------------------------
class Indice:
    """Encuentra a la persona de la base por DNI o por nombre (en cualquier orden)."""

    def __init__(self, personas: pd.DataFrame):
        self.por_doc: dict[str, str] = {}
        self.por_clave_nombre: dict[str, list[str]] = {}
        self.por_token: dict[str, set[str]] = {}
        self.tokens: dict[str, frozenset[str]] = {}
        for f in personas[["clave", "nro_doc", "apellido_nombre"]].itertuples(index=False):
            clave, doc, nombre = f
            if doc and clave.startswith("DOC:"):
                self.por_doc[doc] = clave
            toks = frozenset(tokens_nombre(nombre))
            if len(toks) < 2:
                continue
            self.tokens[clave] = toks
            self.por_clave_nombre.setdefault(" ".join(sorted(toks)), []).append(clave)
            for t in toks:
                self.por_token.setdefault(t, set()).add(clave)
        self._memo: dict[str, tuple[str, str]] = {}

    def buscar(self, nombre: str, dni: str = "") -> tuple[str, str]:
        """(clave o "", cómo: dni / nombre / nombre parcial / ambiguo / no está)."""
        if dni and dni in self.por_doc:
            return self.por_doc[dni], "dni"
        toks = tokens_nombre(nombre)
        if len(toks) < 2:
            return "", "no está"
        llave = " ".join(sorted(set(toks)))
        if llave in self._memo:
            return self._memo[llave]
        exactos = self.por_clave_nombre.get(llave, [])
        if len(exactos) == 1:
            r = (exactos[0], "nombre")
        elif len(exactos) > 1:
            r = ("", "ambiguo")
        else:
            conjuntos = sorted((self.por_token.get(t, set()) for t in set(toks)), key=len)
            if not conjuntos or not conjuntos[0]:
                r = ("", "no está")
            else:
                cand = set(conjuntos[0])
                for c in conjuntos[1:]:
                    cand &= c
                    if not cand:
                        break
                r = (next(iter(cand)), "nombre parcial") if len(cand) == 1 else (("", "ambiguo") if cand else ("", "no está"))
        self._memo[llave] = r
        return r


def vincular(personas: pd.DataFrame, eventos: pd.DataFrame) -> pd.DataFrame:
    """Agrega a cada evento: clave (de la base), vinculo y persona (clave, o EXT:<nombre> si no está en la base)."""
    ev = eventos.copy()
    if ev.empty:
        for c in ("clave", "vinculo", "persona"):
            ev[c] = pd.Series(dtype=object)
        return ev
    idx = Indice(personas)
    res = [idx.buscar(n, d) for n, d in zip(ev["nombre"], ev["dni"])]
    ev["clave"] = [r[0] for r in res]
    ev["vinculo"] = [r[1] for r in res]
    ev["persona"] = [c if c else "EXT:" + " ".join(sorted(set(tokens_nombre(n)))) for c, n in zip(ev["clave"], ev["nombre"])]
    ev.loc[ev["persona"] == "EXT:", "persona"] = ""
    return ev


# ---------------------------------------------------------------------------
# Perfil por persona
# ---------------------------------------------------------------------------
def perfiles(personas: pd.DataFrame, ev: pd.DataFrame, hoy: date) -> pd.DataFrame:
    """Una fila por persona con prestaciones: demografía (si está en la base), actividad y valor."""
    x = ev[ev["persona"] != ""]
    if x.empty:
        return pd.DataFrame(columns=["persona", "clave", "eventos", "primera", "ultima", "facturado", "cobrado",
                                     "pendiente", "categorias", "obra_social", "medico", "empresas", "sexo",
                                     "edad", "rango", "origen", "en_base"])
    g = x.groupby("persona")

    def principal(col: str) -> pd.Series:
        y = x[x[col] != ""]
        if y.empty:
            return pd.Series(dtype=object)
        c = y.groupby(["persona", col]).size().rename("n").reset_index()
        return c.sort_values(["persona", "n"], ascending=[True, False]).drop_duplicates("persona").set_index("persona")[col]

    p = pd.DataFrame({
        "eventos": g.size(), "primera": g["fecha"].min(), "ultima": g["fecha"].max(),
        "facturado": g["importe"].sum(), "cobrado": g["cobrado"].sum(), "pendiente": g["pendiente"].sum(),
        "categorias": g["categoria"].unique().map(frozenset),
        "empresas": g["empresa"].unique().map(lambda a: " + ".join(sorted(a))),
    })
    p["obra_social"] = principal("obra_social").reindex(p.index).fillna("")
    p["medico"] = principal("medico").reindex(p.index).fillna("")
    p = p.reset_index()
    p["clave"] = p["persona"].where(~p["persona"].str.startswith("EXT:"), "")
    base = personas.set_index("clave")[["sexo", "fecha_nac", "origen"]] if len(personas) else None
    if base is not None:
        p = p.join(base, on="clave")
    for c in ("sexo", "fecha_nac", "origen"):
        if c not in p.columns:
            p[c] = ""
        p[c] = p[c].fillna("")
    eds = edades(p["fecha_nac"], hoy)
    p["edad"] = eds.astype(float)
    p["rango"] = eds.map(lambda e: rango_edad(None if pd.isna(e) else int(e)))
    p["en_base"] = p["clave"] != ""
    return p.drop(columns=["fecha_nac"])


# ---------------------------------------------------------------------------
# Análisis
# ---------------------------------------------------------------------------
def _pesos(x: float) -> str:
    return "$ " + f"{x:,.0f}".replace(",", ".")


def _pct(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


def vinculacion(ev: pd.DataFrame) -> pd.DataFrame:
    if ev.empty:
        return pd.DataFrame(columns=["Fuente", "Prestaciones", "Vinculadas a la base", "%"])
    t = ev.groupby("fuente").agg(Prestaciones=("fuente", "size"), vinc=("clave", lambda s: (s != "").sum()))
    t["%"] = t["vinc"] / t["Prestaciones"]
    return t.rename(columns={"vinc": "Vinculadas a la base"}).reset_index().rename(columns={"fuente": "Fuente"})


def por_categoria(ev: pd.DataFrame, perf: pd.DataFrame) -> pd.DataFrame:
    if ev.empty:
        return pd.DataFrame()
    eda = perf.set_index("persona")["edad"] if len(perf) else pd.Series(dtype=float)
    sexos = perf.set_index("persona")["sexo"] if len(perf) else pd.Series(dtype=object)
    x = ev.assign(edad=ev["persona"].map(eda), sexo=ev["persona"].map(sexos))
    g = x.groupby("categoria")
    t = pd.DataFrame({
        "Pacientes": g["persona"].nunique(), "Prestaciones": g.size(), "Facturado": g["importe"].sum(),
        "Edad promedio": g["edad"].mean().round(1), "% mujeres": g["sexo"].agg(lambda s: (s == "Femenino").mean()
                                                                              if s.notna().any() else None),
    })
    t["Ticket promedio"] = t["Facturado"] / t["Prestaciones"].where(t["Prestaciones"] > 0)
    return t.sort_values("Pacientes", ascending=False).reset_index().rename(columns={"categoria": "Categoría"})


def por_procedimiento(ev: pd.DataFrame, limite: int = 40) -> pd.DataFrame:
    if ev.empty:
        return pd.DataFrame()
    x = ev[ev["procedimiento"] != ""]
    g = x.groupby(["procedimiento", "categoria"])
    t = pd.DataFrame({"Pacientes": g["persona"].nunique(), "Prestaciones": g.size(), "Facturado": g["importe"].sum()})
    return t.sort_values("Prestaciones", ascending=False).head(limite).reset_index().rename(
        columns={"procedimiento": "Práctica", "categoria": "Categoría"})


def mensual(ev: pd.DataFrame, hoy: date, meses: int = 24) -> pd.DataFrame:
    """Por mes: pacientes atendidos, pacientes nuevos (primera prestación), prestaciones y facturado."""
    x = ev[ev["fecha"].notna() & (ev["persona"] != "")].copy()
    if x.empty:
        return pd.DataFrame()
    x["mes"] = x["fecha"].dt.to_period("M")
    primera = x.groupby("persona")["mes"].min()
    desde = pd.Period(hoy, "M") - (meses - 1)
    g = x[x["mes"] >= desde].groupby("mes")
    t = pd.DataFrame({"Pacientes atendidos": g["persona"].nunique(), "Prestaciones": g.size(),
                      "Facturado": g["importe"].sum()})
    nuevos = primera[primera >= desde].value_counts()
    t["Pacientes nuevos"] = nuevos.reindex(t.index).fillna(0).astype(int)
    rango = pd.period_range(desde, pd.Period(hoy, "M"), freq="M")
    t = t.reindex(rango, fill_value=0)
    t.index = t.index.astype(str)
    return t


def crecimiento(ev: pd.DataFrame, hoy: date) -> dict[str, Any]:
    x = ev[ev["fecha"].notna() & (ev["persona"] != "")]
    fin = pd.Timestamp(hoy)
    a = x[(x["fecha"] > fin - pd.DateOffset(months=12)) & (x["fecha"] <= fin)]
    b = x[(x["fecha"] > fin - pd.DateOffset(months=24)) & (x["fecha"] <= fin - pd.DateOffset(months=12))]
    primera = x.groupby("persona")["fecha"].min()
    nuevos_a = int(((primera > fin - pd.DateOffset(months=12)) & (primera <= fin)).sum())
    nuevos_b = int(((primera > fin - pd.DateOffset(months=24)) & (primera <= fin - pd.DateOffset(months=12))).sum())

    def var(n, m):
        return (n - m) / m if m else None

    return {"pacientes_12m": int(a["persona"].nunique()), "pacientes_12m_ant": int(b["persona"].nunique()),
            "facturado_12m": float(a["importe"].sum()), "facturado_12m_ant": float(b["importe"].sum()),
            "nuevos_12m": nuevos_a, "nuevos_12m_ant": nuevos_b,
            "var_pacientes": var(a["persona"].nunique(), b["persona"].nunique()),
            "var_facturado": var(a["importe"].sum(), b["importe"].sum()), "var_nuevos": var(nuevos_a, nuevos_b)}


def recurrencia(ev: pd.DataFrame, perf: pd.DataFrame) -> dict[str, Any]:
    if perf.empty:
        return {}
    x = ev[ev["fecha"].notna() & (ev["persona"] != "")][["persona", "fecha"]].drop_duplicates()
    x = x.sort_values(["persona", "fecha"])
    dias = x.groupby("persona")["fecha"].diff().dt.days.dropna()
    visitas = x.groupby("persona").size()
    return {"pacientes": int(len(perf)), "con_mas_de_una": int((visitas >= 2).sum()),
            "tasa_retorno": _pct((visitas >= 2).sum(), len(visitas)),
            "dias_entre_visitas": float(dias.median()) if len(dias) else None,
            "visitas_promedio": float(visitas.mean()) if len(visitas) else 0.0,
            "distribucion": visitas.clip(upper=10).value_counts().sort_index().rename(
                lambda v: "10 o más" if v >= 10 else str(v))}


def secuencias(ev: pd.DataFrame, limite: int = 12) -> pd.DataFrame:
    """Qué viene después de qué: pasos más comunes entre categorías (en orden de fecha)."""
    x = ev[ev["fecha"].notna() & (ev["persona"] != "") & (ev["categoria"] != "Insumos de farmacia")]
    x = x.sort_values(["persona", "fecha"])[["persona", "categoria"]]
    sig = x.groupby("persona")["categoria"].shift(-1)
    pares = pd.DataFrame({"Desde": x["categoria"], "Hacia": sig})
    pares = pares[pares["Hacia"].notna() & (pares["Desde"] != pares["Hacia"])]
    if pares.empty:
        return pd.DataFrame(columns=["Desde", "Hacia", "Veces"])
    return pares.value_counts().head(limite).rename("Veces").reset_index()


def embudo(perf: pd.DataFrame) -> pd.DataFrame:
    """Recorrido de fertilidad: consulta → estudios → tratamiento → transferencia."""
    if perf.empty:
        return pd.DataFrame(columns=["Etapa", "Pacientes", "% del paso anterior"])
    cats = perf["categorias"]
    pasos = [("Llegaron (cualquier prestación)", cats.map(lambda c: True)),
             ("Consulta", cats.map(lambda c: "Consulta" in c)),
             ("Estudios", cats.map(lambda c: bool(c & ESTUDIOS))),
             ("Tratamiento (IIU, FIV/ICSI, ovodonación)", cats.map(lambda c: bool(c & TRATAMIENTOS))),
             ("Transferencia embrionaria", cats.map(lambda c: "Transferencia embrionaria" in c))]
    filas, previo = [], None
    for etapa, m in pasos:
        n = int(m.sum())
        filas.append({"Etapa": etapa, "Pacientes": n, "% del paso anterior": _pct(n, previo) if previo else 1.0})
        previo = n or previo
    return pd.DataFrame(filas)


def cohortes(ev: pd.DataFrame, hoy: date, trimestres: int = 8) -> pd.DataFrame:
    """% de pacientes de cada trimestre de llegada que volvió en los trimestres siguientes."""
    x = ev[ev["fecha"].notna() & (ev["persona"] != "")].copy()
    if x.empty:
        return pd.DataFrame()
    x["t"] = x["fecha"].dt.to_period("Q")
    primera = x.groupby("persona")["t"].min().rename("cohorte")
    x = x.join(primera, on="persona")
    x["n"] = (x["t"] - x["cohorte"]).map(lambda d: d.n)
    desde = pd.Period(hoy, "Q") - (trimestres - 1)
    x = x[x["cohorte"] >= desde]
    if x.empty:
        return pd.DataFrame()
    tam = x.groupby("cohorte")["persona"].nunique()
    t = x[x["n"] > 0].groupby(["cohorte", "n"])["persona"].nunique().unstack(fill_value=0)
    t = t.reindex(tam.index, fill_value=0).div(tam, axis=0)
    t.columns = [f"+{c} trim." for c in t.columns]
    t.insert(0, "Pacientes nuevos", tam)
    t.index = t.index.astype(str)
    return t.iloc[:, : 1 + min(trimestres - 1, max(0, t.shape[1] - 1))]


def obras_sociales(ev: pd.DataFrame, limite: int = 30) -> pd.DataFrame:
    x = ev[ev["obra_social"] != ""]
    if x.empty:
        return pd.DataFrame()
    dias = (x["fecha_pago"] - x["fecha_factura"]).dt.days if "fecha_pago" in x else pd.Series(dtype=float)
    x = x.assign(dias=dias.where(dias >= 0))
    g = x.groupby("obra_social")
    t = pd.DataFrame({"Pacientes": g["persona"].nunique(), "Prestaciones": g.size(), "Facturado": g["importe"].sum(),
                      "Cobrado": g["cobrado"].sum(), "Pendiente": g["pendiente"].sum(),
                      "Días hasta el cobro (mediana)": g["dias"].median().round(0),
                      "Práctica principal": g["categoria"].agg(lambda s: s.mode().iat[0] if len(s) else "")})
    t["% cobrado"] = t["Cobrado"] / t["Facturado"].where(t["Facturado"] > 0)
    t["Ticket promedio"] = t["Facturado"] / t["Prestaciones"]
    return t.sort_values("Pacientes", ascending=False).head(limite).reset_index().rename(
        columns={"obra_social": "Obra social"})


def medicos(ev: pd.DataFrame, perf: pd.DataFrame, limite: int = 30) -> pd.DataFrame:
    x = ev[(ev["medico"] != "") & (ev["persona"] != "") & ev["fecha"].notna()].sort_values("fecha")
    if x.empty:
        return pd.DataFrame()
    primero = x.groupby("persona").head(1).groupby("medico")["persona"].nunique()
    eda = perf.set_index("persona")["edad"] if len(perf) else pd.Series(dtype=float)
    g = x.groupby("medico")
    t = pd.DataFrame({"Pacientes": g["persona"].nunique(), "Prestaciones": g.size(), "Facturado": g["importe"].sum(),
                      "Pacientes que llegaron con este médico": primero,
                      "Edad promedio de sus pacientes": g["persona"].agg(lambda s: eda.reindex(s.unique()).mean()).round(1),
                      "Práctica principal": g["categoria"].agg(lambda s: s.mode().iat[0] if len(s) else "")})
    t["Pacientes que llegaron con este médico"] = t["Pacientes que llegaron con este médico"].fillna(0).astype(int)
    return t.sort_values("Pacientes", ascending=False).head(limite).reset_index().rename(columns={"medico": "Médico"})


def valor(perf: pd.DataFrame) -> dict[str, Any]:
    v = perf[perf["facturado"] > 0]["facturado"].sort_values(ascending=False)
    if v.empty:
        return {}
    top = v.head(max(1, int(round(len(v) * 0.2))))

    def tabla(col: str, orden: list[str] | None = None) -> pd.DataFrame:
        x = perf[(perf["facturado"] > 0) & (perf[col] != "")]
        if x.empty:
            return pd.DataFrame()
        g = x.groupby(col)
        t = pd.DataFrame({"Pacientes": g.size(), "Valor promedio": g["facturado"].mean(),
                          "Valor total": g["facturado"].sum()})
        if orden:
            t = t.reindex([o for o in orden if o in t.index])
        else:
            t = t.sort_values("Valor total", ascending=False).head(15)
        return t.reset_index()

    return {"pacientes_con_valor": int(len(v)), "promedio": float(v.mean()), "mediana": float(v.median()),
            "concentracion_top20": _pct(top.sum(), v.sum()),
            "por_rango": tabla("rango", [r for _, _, r in RANGOS] + ["Sin dato"]),
            "por_obra_social": tabla("obra_social"), "por_origen": tabla("origen"),
            "por_sexo": tabla("sexo")}


def oportunidades(ev: pd.DataFrame, perf: pd.DataFrame, leads: pd.DataFrame, personas_idx: Any,
                  hoy: date) -> list[dict[str, Any]]:
    """Listas accionables. Cada una: título, por qué importa, cantidad y las personas (para seguimiento)."""
    out: list[dict[str, Any]] = []
    if not perf.empty:
        fin = pd.Timestamp(hoy)
        cats = perf["categorias"]
        consulta_sin = perf[cats.map(lambda c: ("Consulta" in c or bool(c & ESTUDIOS)) and not (c & TRATAMIENTOS)
                                     and bool(c & ({"Consulta"} | ESTUDIOS)))
                            & (perf["empresas"].str.contains("VMR"))]
        out.append({"titulo": "Consultaron o se estudiaron en VMR y no iniciaron tratamiento",
                    "por_que": "Es el paso del embudo donde más pacientes se pierden: seguimiento médico, "
                               "financiación o segunda consulta.", "personas": consulta_sin})
        crio = ev[(ev["categoria"] == "Criopreservación") & (ev["persona"] != "")].groupby("persona")["fecha"].max()
        trans = ev[(ev["categoria"] == "Transferencia embrionaria") & (ev["persona"] != "")].groupby("persona")["fecha"].max()
        sin_trans = [p for p, f in crio.items() if not (p in trans.index and pd.notna(f) and trans[p] >= f)]
        out.append({"titulo": "Tienen material criopreservado y no hay transferencia posterior",
                    "por_que": "Seguimiento del material guardado (renovaciones, decisión de uso) y posible "
                               "próximo tratamiento.", "personas": perf[perf["persona"].isin(sin_trans)]})
        dormidos = perf[(perf["ultima"] < fin - pd.DateOffset(months=12)) &
                        (perf["ultima"] >= fin - pd.DateOffset(months=36))]
        out.append({"titulo": "No volvieron en más de 12 meses (activos en los últimos 3 años)",
                    "por_que": "Controles anuales y continuidad de cuidado: la forma más barata de crecer es que "
                               "vuelva quien ya confía en Vitae.", "personas": dormidos})
        deben = perf[perf["pendiente"] > 0].sort_values("pendiente", ascending=False)
        out.append({"titulo": "Prestaciones facturadas pendientes de cobro",
                    "por_que": "Plata ya trabajada que todavía no entró.", "personas": deben})
        unica = perf[(perf["eventos"] == 1) & (perf["ultima"] < fin - pd.DateOffset(months=3))]
        out.append({"titulo": "Vinieron una sola vez (hace más de 3 meses)",
                    "por_que": "Mide la primera experiencia: si es alto, revisar atención, turnos y derivaciones.",
                    "personas": unica})
    if leads is not None and not leads.empty and personas_idx is not None:
        idx = Indice(personas_idx)
        con_eventos = set(ev["persona"]) if not ev.empty else set()
        conv = []
        for nombre in leads["nombre"]:
            c, _ = idx.buscar(nombre)
            conv.append(bool(c) and c in con_eventos)
        l2 = leads.assign(convirtio=conv)
        no = l2[~l2["convirtio"]]
        out.append({"titulo": f"Consultas web de Gine sin prestación registrada ({_pct(len(no), len(l2)):.0%} de "
                              f"{len(l2)})", "por_que": "Conversión de la web en pacientes: llamar a tiempo "
                              "multiplica los turnos.", "personas": None, "leads": no,
                    "tasa_conversion": _pct(l2["convirtio"].sum(), len(l2))})
    return out


def hallazgos(personas: pd.DataFrame, ev: pd.DataFrame, perf: pd.DataFrame, hoy: date) -> list[str]:
    """Frases con los datos más importantes (se calculan, no se inventan)."""
    h: list[str] = []
    total = len(personas)
    if total:
        muj = (personas["sexo"] == "Femenino").mean()
        h.append(f"La base tiene {total:,} personas; {muj:.0%} son mujeres.".replace(",", "."))
        eds = edades(personas["fecha_nac"], hoy).dropna()
        if len(eds):
            fert = int(((eds >= 30) & (eds <= 44)).sum())
            h.append(f"{fert:,} personas con fecha de nacimiento tienen entre 30 y 44 años (edad reproductiva "
                     f"de mayor demanda), el {fert / len(eds):.0%} de las que tienen el dato.".replace(",", "."))
        sin = (personas["fecha_nac"] == "").mean()
        if sin > 0.2:
            h.append(f"Al {sin:.0%} le falta la fecha de nacimiento: completarla en el sistema de turnos mejora "
                     "todos los análisis por edad.")
    if ev.empty:
        h.append("Todavía no hay prestaciones para cruzar (Facturación, Quirófano, Gine): cuando las haya, "
                 "aparecen el recorrido, el valor y las oportunidades.")
        return h
    vinc = (ev["clave"] != "").mean()
    h.append(f"{len(ev):,} prestaciones registradas en Vitae; el {vinc:.0%} se pudo vincular a una persona de la "
             "base (por DNI o por nombre).".replace(",", "."))
    cr = crecimiento(ev, hoy)
    if cr["var_pacientes"] is not None:
        h.append(f"Pacientes atendidos en los últimos 12 meses: {cr['pacientes_12m']:,} "
                 f"({cr['var_pacientes']:+.0%} contra los 12 anteriores).".replace(",", "."))
    if cr["var_nuevos"] is not None:
        h.append(f"Pacientes nuevos en 12 meses: {cr['nuevos_12m']:,} ({cr['var_nuevos']:+.0%}).".replace(",", "."))
    rec = recurrencia(ev, perf)
    if rec:
        h.append(f"El {rec['tasa_retorno']:.0%} de los pacientes volvió al menos una vez"
                 + (f"; entre una visita y la siguiente pasan {rec['dias_entre_visitas']:.0f} días (mediana)."
                    if rec.get("dias_entre_visitas") is not None else "."))
    cat = por_categoria(ev, perf)
    if not cat.empty:
        top = cat.iloc[0]
        h.append(f"La práctica con más pacientes es {top['Categoría']} ({int(top['Pacientes']):,})."
                 .replace(",", "."))
        trat = cat[cat["Categoría"].isin(TRATAMIENTOS) & cat["Edad promedio"].notna()]
        for _, f in trat.iterrows():
            h.append(f"Edad promedio en {f['Categoría']}: {f['Edad promedio']:.1f} años.")
    v = valor(perf)
    if v:
        h.append(f"El 20% de los pacientes con facturación concentra el {v['concentracion_top20']:.0%} de lo "
                 f"facturado; valor promedio por paciente {_pesos(v['promedio'])}.")
    os_ = obras_sociales(ev)
    if not os_.empty:
        f = os_.iloc[0]
        h.append(f"La cobertura con más pacientes es {f['Obra social']} ({int(f['Pacientes']):,}).".replace(",", "."))
        lentas = os_[(os_["Prestaciones"] >= 10) & os_["Días hasta el cobro (mediana)"].notna()]
        if not lentas.empty:
            l = lentas.sort_values("Días hasta el cobro (mediana)", ascending=False).iloc[0]
            h.append(f"La que más tarda en pagar es {l['Obra social']}: {l['Días hasta el cobro (mediana)']:.0f} días "
                     "de la factura al cobro (mediana).")
    return h


# ---------------------------------------------------------------------------
# Investigación: dataset anonimizado enriquecido
# ---------------------------------------------------------------------------
def dataset_investigacion(perf: pd.DataFrame, ev: pd.DataFrame, hoy: date, minimo: int = 5,
                          semilla: int | None = None) -> bytes:
    """Excel sin datos identificatorios: personas y prestaciones con un id anónimo al azar.

    - Edad en rangos, fechas al mes, sin nombre/DNI/HC/médico.
    - Prácticas con menos de ``minimo`` pacientes se reemplazan por su categoría (evita reidentificar).
    """
    import random

    gen = random.Random(semilla)
    personas = list(perf["persona"])
    ids = [f"A{i + 1:06d}" for i in range(len(personas))]
    gen.shuffle(ids)
    mapa = dict(zip(personas, ids))
    x = ev[ev["persona"].isin(mapa)].copy()
    raros = x.groupby("procedimiento")["persona"].nunique()
    raros = set(raros[raros < minimo].index)
    x["practica"] = [c if p in raros or not p else p for p, c in zip(x["procedimiento"], x["categoria"])]
    eda = perf.set_index("persona")["edad"]
    nac_edad = x["persona"].map(eda)
    anos = ((pd.Timestamp(hoy) - x["fecha"]).dt.days / 365.25).fillna(0)
    edad_evento = (nac_edad - anos).map(lambda e: rango_edad(int(e)) if pd.notna(e) and e >= 0 else "Sin dato")
    eventos = pd.DataFrame({"id_anonimo": x["persona"].map(mapa), "mes": x["fecha"].dt.strftime("%Y-%m"),
                            "empresa": x["empresa"], "categoria": x["categoria"], "practica": x["practica"],
                            "cobertura": x["obra_social"].where(x["obra_social"] != "", "Sin dato"),
                            "rango_edad_en_la_prestacion": edad_evento, "importe": x["importe"].round(0)})
    eventos = eventos.sample(frac=1, random_state=semilla).sort_values(["id_anonimo", "mes"]).reset_index(drop=True)
    pers = pd.DataFrame({
        "id_anonimo": perf["persona"].map(mapa), "sexo": perf["sexo"].replace("", "Sin dato"),
        "rango_edad_hoy": perf["rango"], "origen": perf["origen"].replace("", "Sin dato"),
        "prestaciones": perf["eventos"], "categorias": perf["categorias"].map(lambda c: ", ".join(sorted(c))),
        "primer_mes": perf["primera"].dt.strftime("%Y-%m"), "ultimo_mes": perf["ultima"].dt.strftime("%Y-%m"),
        "valor_total": perf["facturado"].round(0), "en_base_de_pacientes": perf["en_base"].map({True: "Sí", False: "No"}),
    }).sort_values("id_anonimo").reset_index(drop=True)
    diccionario = pd.DataFrame([
        ("id_anonimo", "Identificador al azar, distinto en cada exportación. No permite volver a la persona."),
        ("rango_edad_*", "Edad en rangos (no fecha de nacimiento)."),
        ("mes", "Mes de la prestación (sin día)."),
        ("practica", f"Práctica tal como se factura; si la hicieron menos de {minimo} personas se reemplaza por la "
                     "categoría."),
        ("categoria", "Agrupación automática de prácticas (fertilidad, estudios, cirugía, etc.)."),
        ("cobertura", "Obra social o prepaga."),
        ("valor_total / importe", "Pesos facturados (sin anulados)."),
    ], columns=["Campo", "Qué es"])
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as xl:
        pd.DataFrame({"Vitae · investigación (datos anonimizados)": [
            f"Generado el {hoy:%d/%m/%Y}. Sin nombre, documento, historia clínica, médico ni fechas exactas.",
            "Uso interno para investigación y estadística. Prohibido intentar reidentificar a las personas.",
            "Antes de publicar, consultar al comité de ética que corresponda.",
        ]}).to_excel(xl, sheet_name="Leeme", index=False)
        pers.to_excel(xl, sheet_name="Pacientes", index=False)
        eventos.to_excel(xl, sheet_name="Prestaciones", index=False)
        diccionario.to_excel(xl, sheet_name="Diccionario", index=False)
    return buffer.getvalue()


def edad_por_tratamiento_y_ano(ev: pd.DataFrame, perf: pd.DataFrame, hoy: date) -> pd.DataFrame:
    """Para investigación: edad promedio al momento de cada tratamiento, por año."""
    x = ev[ev["categoria"].isin(TRATAMIENTOS | {"Transferencia embrionaria", "Criopreservación"}) & ev["fecha"].notna()]
    if x.empty or perf.empty:
        return pd.DataFrame()
    eda = perf.set_index("persona")["edad"]
    anos = (pd.Timestamp(hoy) - x["fecha"]).dt.days / 365.25
    x = x.assign(edad_ev=x["persona"].map(eda) - anos, ano=x["fecha"].dt.year)
    x = x[x["edad_ev"].notna() & (x["edad_ev"] > 0)]
    if x.empty:
        return pd.DataFrame()
    return x.pivot_table(index="ano", columns="categoria", values="edad_ev", aggfunc="mean").round(1)


# ---------------------------------------------------------------------------
# Todo junto (se calcula una vez y se guarda en la sesión)
# ---------------------------------------------------------------------------
def analizar(personas: pd.DataFrame, tablas: dict[str, pd.DataFrame], hoy: date) -> dict[str, Any]:
    ev = vincular(personas, eventos_desde(tablas))
    perf = perfiles(personas, ev, hoy)
    leads = leads_desde(tablas)
    return {"eventos": ev, "perfiles": perf, "leads": leads, "hoy": hoy,
            "hallazgos": hallazgos(personas, ev, perf, hoy)}


# ---------------------------------------------------------------------------
# Pantalla «Inteligencia»
# ---------------------------------------------------------------------------
SECCIONES = ["Resumen", "Prestaciones", "Recorrido", "Obras sociales", "Médicos", "Valor", "Oportunidades",
             "Investigación"]
_VINO = "#8C2853"
_PALETA = ["#8C2853", "#4F7CAC", "#C9895B", "#5E9C76", "#8E6FB1", "#D1A93F", "#5BA4A4", "#B5577A", "#7A8B99",
           "#A0522D", "#6B8E23", "#9A8D93", "#3A6B8C", "#C06C84"]

_CSS = """<style>
.pin-hallazgos{background:#fff;border:1px solid #E8D9E0;border-radius:14px;padding:.8rem 1.1rem;margin:.3rem 0 1rem}
.pin-hallazgos li{margin:.28rem 0;color:#2B2226}
.pin-tit{font-weight:700;color:#5E1A39;margin-bottom:.3rem}
.pin-op{background:#fff;border:1px solid #E8D9E0;border-left:4px solid #8C2853;border-radius:12px;padding:.6rem .9rem;margin:.4rem 0}
.pin-op b{color:#3A1426}.pin-op span{color:#8A7F85;font-size:.86rem}
.pin-nota{color:#8A7F85;font-size:.82rem}
</style>"""


def _fmt_pesos(df: pd.DataFrame, columnas: list[str]) -> pd.DataFrame:
    out = df.copy()
    for c in columnas:
        if c in out.columns:
            out[c] = out[c].map(lambda v: _pesos(v) if pd.notna(v) else "")
    return out


def _fmt_pct(df: pd.DataFrame, columnas: list[str]) -> pd.DataFrame:
    out = df.copy()
    for c in columnas:
        if c in out.columns:
            out[c] = out[c].map(lambda v: f"{v:.0%}" if pd.notna(v) else "")
    return out


def _num(n: Any) -> str:
    return f"{int(n):,}".replace(",", ".")


def _var(v: float | None) -> str | None:
    return None if v is None else f"{v:+.0%}"


def _grafico_barras(st: Any, df: pd.DataFrame, x: str, ys: list[str], *, apilado: bool = False, alto: int = 320,
                    horizontal: bool = False) -> None:
    if df.empty:
        return
    try:
        import plotly.graph_objects as go

        fig = go.Figure()
        for i, y in enumerate(ys):
            if horizontal:
                fig.add_bar(y=list(df[x]), x=list(df[y]), name=y, orientation="h",
                            marker_color=_PALETA[i % len(_PALETA)])
            else:
                fig.add_bar(x=list(df[x]), y=list(df[y]), name=y, marker_color=_PALETA[i % len(_PALETA)])
        fig.update_layout(barmode="stack" if apilado else "group", height=alto, margin=dict(l=10, r=10, t=10, b=10),
                          legend=dict(orientation="h", y=1.12), plot_bgcolor="rgba(0,0,0,0)",
                          paper_bgcolor="rgba(0,0,0,0)", showlegend=len(ys) > 1)
        if horizontal:
            fig.update_yaxes(autorange="reversed")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    except Exception:
        st.bar_chart(df.set_index(x)[ys])


def _grafico_lineas(st: Any, df: pd.DataFrame, ys: list[str], alto: int = 300) -> None:
    if df.empty:
        return
    try:
        import plotly.graph_objects as go

        fig = go.Figure()
        for i, y in enumerate(ys):
            fig.add_scatter(x=list(df.index), y=list(df[y]), name=y, mode="lines+markers",
                            line=dict(color=_PALETA[i % len(_PALETA)], width=2.5))
        fig.update_layout(height=alto, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.15),
                          plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    except Exception:
        st.line_chart(df[ys])


def _tabla(st: Any, df: pd.DataFrame, **k: Any) -> None:
    if df is None or df.empty:
        st.caption("Sin datos para mostrar.")
        return
    st.dataframe(df, hide_index=True, use_container_width=True, **k)


def _lista_personas(personas: pd.DataFrame, perf: pd.DataFrame, ev: pd.DataFrame, hoy: date) -> pd.DataFrame:
    """Personas de una oportunidad, con lo justo para el seguimiento."""
    if perf is None or perf.empty:
        return pd.DataFrame()
    base = personas.set_index("clave")
    ultima_cat = (ev[ev["persona"] != ""].sort_values("fecha").groupby("persona")["categoria"].last()
                  if not ev.empty else pd.Series(dtype=object))
    nombres_ev = (ev[ev["persona"] != ""].groupby("persona")["nombre"].first() if not ev.empty
                  else pd.Series(dtype=object))
    filas = []
    for f in perf.itertuples(index=False):
        en = f.clave in base.index if f.clave else False
        b = base.loc[f.clave] if en else None
        filas.append({
            "Apellido y nombre": (b["apellido_nombre"] if en else nombres_ev.get(f.persona, "")),
            "Documento": (f"{b['tipo_doc']} {b['nro_doc']}".strip() if en else "No está en la base"),
            "HC": (b["historia_clinica"] if en else ""),
            "Edad": (f"{int(f.edad)}" if f.edad is not None and pd.notna(f.edad) else ""),
            "Última prestación": f.ultima.strftime("%d/%m/%Y") if pd.notna(f.ultima) else "",
            "Último paso": ultima_cat.get(f.persona, ""),
            "Cobertura": f.obra_social, "Médico": f.medico,
            "Pendiente": _pesos(f.pendiente) if f.pendiente else "",
        })
    return pd.DataFrame(filas)


def render_inteligencia(st: Any, almacen: Any, personas: pd.DataFrame,
                        leer_otras: Any, hoy: date) -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    tablas = {}
    if leer_otras is not None:
        try:
            tablas = leer_otras() or {}
        except Exception as exc:
            st.caption(f"No pude leer los otros módulos ({type(exc).__name__}): se analiza sólo la base.")
    firma = (len(personas), tuple(sorted((k, len(v)) for k, v in tablas.items() if isinstance(v, pd.DataFrame))),
             hoy.isoformat())
    if st.session_state.get("pin_firma") != firma or "pin_res" not in st.session_state:
        with st.spinner("Cruzando pacientes con todas las prestaciones de Vitae…"):
            st.session_state["pin_res"] = analizar(personas, tablas, hoy)
            st.session_state["pin_firma"] = firma
    r = st.session_state["pin_res"]
    ev, perf, leads = r["eventos"], r["perfiles"], r["leads"]
    seccion = st.pills("Sección", SECCIONES, key="pin_seccion", default="Resumen", label_visibility="collapsed") \
        or "Resumen"
    from vitae.modulos.pacientes import _registrar_una_vez

    _registrar_una_vez(almacen, "inteligencia", seccion)

    if seccion == "Resumen":
        cr = crecimiento(ev, hoy) if not ev.empty else {}
        rec = recurrencia(ev, perf) if not perf.empty else {}
        c = st.columns(4)
        c[0].metric("Personas en la base", _num(len(personas)))
        c[1].metric("Pacientes atendidos (12 meses)", _num(cr.get("pacientes_12m", 0)),
                    _var(cr.get("var_pacientes")), help="Con al menos una prestación; contra los 12 meses anteriores.")
        c[2].metric("Pacientes nuevos (12 meses)", _num(cr.get("nuevos_12m", 0)), _var(cr.get("var_nuevos")))
        c[3].metric("Facturado (12 meses)", _pesos(cr.get("facturado_12m", 0)), _var(cr.get("var_facturado")))
        c = st.columns(4)
        c[0].metric("Volvieron al menos una vez", f"{rec.get('tasa_retorno', 0):.0%}")
        c[1].metric("Visitas por paciente", f"{rec.get('visitas_promedio', 0):.1f}".replace(".", ","))
        c[2].metric("Días entre visitas (mediana)",
                    f"{rec['dias_entre_visitas']:.0f}" if rec.get("dias_entre_visitas") is not None else "—")
        c[3].metric("Prestaciones vinculadas", f"{(ev['clave'] != '').mean():.0%}" if not ev.empty else "—",
                    help="Prestaciones de Facturación, Quirófano, Gine y Farmacia que se pudieron unir a una persona "
                         "de la base (por DNI o nombre).")
        items = "".join(f"<li>{h}</li>" for h in r["hallazgos"])
        st.markdown(f'<div class="pin-hallazgos"><div class="pin-tit">💡 Lo más importante</div><ul>{items}</ul></div>',
                    unsafe_allow_html=True)
        m = mensual(ev, hoy)
        if not m.empty:
            st.markdown("##### Evolución mensual (24 meses)")
            _grafico_lineas(st, m, ["Pacientes atendidos", "Pacientes nuevos"])
        if not ev.empty:
            with st.expander("Cómo se vinculó cada fuente"):
                _tabla(st, _fmt_pct(vinculacion(ev), ["%"]))
                st.caption("Se une por DNI cuando la fuente lo tiene; si no, por nombre completo en cualquier orden. "
                           "Los nombres repetidos en la base (homónimos) no se asignan, para no mezclar personas.")

    elif seccion == "Prestaciones":
        if ev.empty:
            st.info("Todavía no hay prestaciones para analizar.")
            return
        cat = por_categoria(ev, perf)
        st.markdown("##### Por tipo de práctica")
        _grafico_barras(st, cat.head(14), "Categoría", ["Pacientes"], horizontal=True, alto=380)
        _tabla(st, _fmt_pct(_fmt_pesos(cat, ["Facturado", "Ticket promedio"]), ["% mujeres"]))
        x = ev[ev["fecha"].notna()].copy()
        if not x.empty:
            x["mes"] = x["fecha"].dt.to_period("M")
            desde = pd.Period(hoy, "M") - 23
            top = list(cat["Categoría"].head(6))
            t = (x[(x["mes"] >= desde) & x["categoria"].isin(top)].groupby(["mes", "categoria"])["persona"]
                 .nunique().unstack(fill_value=0))
            if not t.empty:
                t.index = t.index.astype(str)
                st.markdown("##### Pacientes por mes en las prácticas principales")
                _grafico_lineas(st, t, [c for c in top if c in t.columns])
        st.markdown("##### Prácticas más frecuentes")
        _tabla(st, _fmt_pesos(por_procedimiento(ev), ["Facturado"]))
        with st.expander("Cómo se clasificó cada práctica"):
            cl = ev.groupby(["procedimiento", "categoria"]).size().rename("Veces").reset_index() \
                .sort_values("Veces", ascending=False).rename(columns={"procedimiento": "Práctica", "categoria": "Categoría"})
            _tabla(st, cl.head(300))
            otros = (ev["categoria"] == OTROS).mean()
            st.caption(f"{otros:.0%} quedó en «{OTROS}». Si alguna está mal agrupada, avisame y ajusto la regla.")

    elif seccion == "Recorrido":
        if perf.empty:
            st.info("Todavía no hay prestaciones para armar el recorrido.")
            return
        st.markdown("##### Embudo de fertilidad")
        emb = embudo(perf)
        _grafico_barras(st, emb, "Etapa", ["Pacientes"], horizontal=True, alto=260)
        _tabla(st, _fmt_pct(emb, ["% del paso anterior"]))
        st.caption("Cada etapa cuenta pacientes que alguna vez tuvieron esa práctica.")
        izq, der = st.columns(2)
        with izq:
            st.markdown("##### Visitas por paciente")
            rec = recurrencia(ev, perf)
            dist = rec["distribucion"].rename_axis("Visitas").reset_index(name="Pacientes")
            _grafico_barras(st, dist, "Visitas", ["Pacientes"], alto=260)
        with der:
            st.markdown("##### Qué viene después de qué")
            _tabla(st, secuencias(ev))
        co = cohortes(ev, hoy)
        if not co.empty:
            st.markdown("##### Retorno por trimestre de llegada")
            pct = [c for c in co.columns if c.startswith("+")]
            _tabla(st, _fmt_pct(co.reset_index().rename(columns={"cohorte": "Llegaron en"}), pct))
            st.caption("Ejemplo: de los que llegaron en ese trimestre, qué % volvió 1, 2, 3… trimestres después.")

    elif seccion == "Obras sociales":
        os_ = obras_sociales(ev)
        if os_.empty:
            st.info("No hay coberturas cargadas en las prestaciones.")
            return
        _grafico_barras(st, os_.head(12), "Obra social", ["Pacientes"], horizontal=True, alto=360)
        _tabla(st, _fmt_pct(_fmt_pesos(os_, ["Facturado", "Cobrado", "Pendiente", "Ticket promedio"]), ["% cobrado"]))
        st.caption("Días hasta el cobro: desde la fecha de factura a la fecha de pago (sólo Facturación).")

    elif seccion == "Médicos":
        md = medicos(ev, perf)
        if md.empty:
            st.info("No hay médicos cargados en las prestaciones.")
            return
        _grafico_barras(st, md.head(12), "Médico", ["Pacientes", "Pacientes que llegaron con este médico"],
                        horizontal=True, alto=380)
        _tabla(st, _fmt_pesos(md, ["Facturado"]))
        st.caption("«Llegaron con este médico»: su primera prestación en Vitae fue con él o ella (capacidad de "
                   "atraer pacientes nuevos).")

    elif seccion == "Valor":
        v = valor(perf)
        if not v:
            st.info("Todavía no hay facturación vinculada a pacientes.")
            return
        c = st.columns(4)
        c[0].metric("Pacientes con facturación", _num(v["pacientes_con_valor"]))
        c[1].metric("Valor promedio por paciente", _pesos(v["promedio"]))
        c[2].metric("Valor mediano", _pesos(v["mediana"]))
        c[3].metric("Lo que aporta el 20% de mayor valor", f"{v['concentracion_top20']:.0%}")
        izq, der = st.columns(2)
        with izq:
            st.markdown("##### Por edad")
            t = v["por_rango"]
            _grafico_barras(st, t, "rango", ["Valor promedio"], alto=260)
            _tabla(st, _fmt_pesos(t.rename(columns={"rango": "Edad"}), ["Valor promedio", "Valor total"]))
        with der:
            st.markdown("##### Por cobertura")
            _tabla(st, _fmt_pesos(v["por_obra_social"].rename(columns={"obra_social": "Obra social"}),
                                  ["Valor promedio", "Valor total"]))
            st.markdown("##### Por origen del registro")
            _tabla(st, _fmt_pesos(v["por_origen"].rename(columns={"origen": "Origen"}),
                                  ["Valor promedio", "Valor total"]))
        st.caption("Valor = lo facturado (sin anulados) a cada paciente en toda su historia en Vitae.")

    elif seccion == "Oportunidades":
        ops = oportunidades(ev, perf, leads, personas, hoy)
        if not ops:
            st.info("Todavía no hay prestaciones para detectar oportunidades.")
            return
        st.caption("Listas para seguimiento médico y administrativo. Para contactar con fines comerciales hace "
                   "falta el consentimiento de cada paciente (Ley 25.326).")
        for i, op in enumerate(ops):
            n = len(op["personas"]) if op.get("personas") is not None else len(op.get("leads", []))
            st.markdown(f'<div class="pin-op"><b>{op["titulo"]}: {_num(n)}</b><br><span>{op["por_que"]}</span></div>',
                        unsafe_allow_html=True)
            if n == 0:
                continue
            with st.expander("Ver la lista"):
                if op.get("personas") is not None:
                    lista = _lista_personas(personas, op["personas"].head(300), ev, hoy)
                else:
                    lista = op["leads"].assign(fecha=op["leads"]["fecha"].dt.strftime("%d/%m/%Y")).head(300) \
                        .rename(columns={"fecha": "Fecha", "nombre": "Nombre", "edad": "Edad",
                                         "obra_social": "Cobertura", "estado": "Estado"})
                _registrar_una_vez(almacen, "ver_oportunidad", f"{op['titulo'][:60]}: {n}")
                _tabla(st, lista)
                if n > 300:
                    st.caption(f"Muestro 300 de {_num(n)}.")
                if st.button("Preparar Excel de esta lista", key=f"pin_op_xl_{i}", icon=":material/download:"):
                    buf = io.BytesIO()
                    lista.to_excel(buf, index=False)
                    st.session_state[f"pin_op_bytes_{i}"] = buf.getvalue()
                    almacen.registrar("exportar_lista", f"{op['titulo'][:60]}: {len(lista)}")
                if st.session_state.get(f"pin_op_bytes_{i}"):
                    st.download_button("Descargar", st.session_state[f"pin_op_bytes_{i}"],
                                       file_name=f"vitae_oportunidad_{i + 1}_{hoy:%Y%m%d}.xlsx", key=f"pin_op_dl_{i}")

    else:  # Investigación
        st.markdown("##### Datos para investigación")
        st.caption("Excel anonimizado con pacientes y prestaciones: sin nombre, documento, historia clínica, médico "
                   "ni fechas exactas; edad en rangos; las prácticas que hicieron menos de 5 personas se agrupan por "
                   "categoría. Antes de publicar, pasar por el comité de ética que corresponda.")
        c = st.columns(3)
        c[0].metric("Pacientes con prestaciones", _num(len(perf)))
        c[1].metric("Prestaciones", _num(len(ev)))
        c[2].metric("Años de historia", _num(ev["fecha"].dt.year.nunique()) if not ev.empty else "0")
        if not perf.empty and st.button("Preparar Excel para investigación", key="pin_inv_xl", icon=":material/science:"):
            st.session_state["pin_inv_bytes"] = dataset_investigacion(perf, ev, hoy)
            almacen.registrar("exportar_investigacion", f"{len(perf)} pacientes, {len(ev)} prestaciones")
        if st.session_state.get("pin_inv_bytes"):
            st.download_button("Descargar Excel para investigación", st.session_state["pin_inv_bytes"],
                               file_name=f"vitae_investigacion_{hoy:%Y%m%d}.xlsx", key="pin_inv_dl",
                               icon=":material/download:")
        t = edad_por_tratamiento_y_ano(ev, perf, hoy)
        if not t.empty:
            st.markdown("##### Edad promedio al momento del tratamiento, por año")
            _tabla(st, t.reset_index().rename(columns={"ano": "Año"}))
        st.markdown("##### Preguntas que estos datos permiten estudiar")
        st.markdown(
            "- ¿Cambió la edad de las pacientes que inician tratamientos de fertilidad con los años?\n"
            "- ¿Cuántas pacientes pasan de la consulta al tratamiento y cuánto tardan?\n"
            "- ¿Qué proporción usa su material criopreservado y después de cuánto tiempo?\n"
            "- ¿Difieren las prácticas según la cobertura (acceso a tratamientos)?\n"
            "- ¿Cuántas pacientes vuelven para un segundo ciclo o una transferencia adicional?\n"
            "- Demanda por edad y sexo para planificar consultorios, quirófano y laboratorio."
        )
        st.markdown('<div class="pin-nota">Los resultados clínicos (embarazo, nacido vivo) no están en Vitae todavía: '
                    'si se registran, se pueden sumar tasas de éxito por edad y tratamiento.</div>',
                    unsafe_allow_html=True)
