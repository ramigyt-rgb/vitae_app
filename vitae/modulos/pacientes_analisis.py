"""Inteligencia de Pacientes: análisis de la base INSTITUCIONAL (y sólo de ella).

No se cruza con Facturación, Quirófano, Cajas ni ninguna otra área: todo sale de las personas
de la base (documento, sexo, nacimiento, estado civil, historia clínica, origen del registro).

Además de la fecha de nacimiento, usa el NÚMERO DE DNI: en Argentina se asigna en orden
cronológico, así que para quien no tiene fecha de nacimiento se puede ESTIMAR el año con un
modelo que se ajusta solo con las personas que sí la tienen (y se mide cuánto se equivoca). Las
edades estimadas siempre se marcan como tales. Los DNI desde 92 millones son de residentes
extranjeros (dato útil en sí, pero ahí la estimación no sirve y no se usa).

Todo se calcula dentro de la app: nada va a la IA.
"""
from __future__ import annotations

import io
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from vitae.modulos.pacientes import RANGOS, edades, rango_edad

TRAMO_DNI = 500_000
DNI_EXTRANJEROS = 92_000_000
_ORDEN_RANGOS = [r for _, _, r in RANGOS] + ["Sin dato"]

GENERACIONES = [(1900, 1945, "Silenciosa (≤1945)"), (1946, 1964, "Baby boomers (1946-64)"),
                (1965, 1980, "Generación X (1965-80)"), (1981, 1996, "Millennials (1981-96)"),
                (1997, 2012, "Generación Z (1997-2012)"), (2013, 2100, "Alfa (2013+)")]
FERTILIDAD = [(18, 29, "18-29"), (30, 34, "30-34"), (35, 37, "35-37"), (38, 40, "38-40"), (41, 42, "41-42"),
              (43, 44, "43-44"), (45, 49, "45-49")]
PEDIATRIA = [(0, 1, "0-1 año"), (2, 5, "2-5"), (6, 12, "6-12"), (13, 17, "13-17")]
DIGITAL = {"Alta por turnos online", "Actualizado por turnos online", "Alta por turnos", "Actualizado por turnos"}


# ---------------------------------------------------------------------------
# Edad: real (fecha de nacimiento) o estimada por número de DNI
# ---------------------------------------------------------------------------
def _dni_numerico(df: pd.DataFrame) -> pd.Series:
    ok = df["clave"].str.startswith("DOC:") & df["tipo_doc"].isin(["DNI", "DU"])
    return pd.to_numeric(df["nro_doc"].where(ok), errors="coerce")


def _ano_decimal(fechas_iso: pd.Series) -> pd.Series:
    f = pd.to_datetime(fechas_iso.where(fechas_iso.astype(str).str.len() >= 10), format="%Y-%m-%d", errors="coerce")
    return f.dt.year + (f.dt.dayofyear - 1) / 365.25


def _tramos(dni: pd.Series, ano: pd.Series, minimo: int = 15, iqr_max: float = 3.0) -> pd.DataFrame:
    x = pd.DataFrame({"dni": dni, "ano": ano}).dropna()
    x = x[x["dni"] < DNI_EXTRANJEROS]
    if x.empty:
        return pd.DataFrame(columns=["tramo", "centro", "ano", "n", "iqr"])
    x["tramo"] = (x["dni"] // TRAMO_DNI).astype(int)
    g = x.groupby("tramo")["ano"]
    t = pd.DataFrame({"ano": g.median(), "n": g.size(), "iqr": g.quantile(0.75) - g.quantile(0.25)})
    t = t[(t["n"] >= minimo) & (t["iqr"] <= iqr_max)].reset_index()
    t["centro"] = t["tramo"] * TRAMO_DNI + TRAMO_DNI / 2
    # Debe crecer con el número: se descartan tramos que van para atrás (DNI reasignados, LE/LC viejas).
    t = t.sort_values("centro")
    t = t[t["ano"] >= t["ano"].cummax() - 1.0]
    return t[["tramo", "centro", "ano", "n", "iqr"]].reset_index(drop=True)


def _aplicar(tramos: pd.DataFrame, dni: pd.Series) -> pd.Series:
    est = pd.Series(np.nan, index=dni.index)
    if tramos.empty or dni.empty:
        return est
    tramo = dni // TRAMO_DNI
    usable = dni.notna() & tramo.isin(set(tramos["tramo"]))
    if usable.any():
        est[usable] = np.interp(dni[usable].astype(float), tramos["centro"].to_numpy(dtype=float),
                                tramos["ano"].to_numpy(dtype=float))
    return est


def modelo_edad(df: pd.DataFrame, semilla: int = 0) -> dict[str, Any]:
    """Ajusta DNI → año de nacimiento con quienes tienen ambos y mide el error en un 20% apartado."""
    dni = _dni_numerico(df)
    ano = _ano_decimal(df["fecha_nac"])
    conocidos = dni.notna() & ano.notna()
    tramos = _tramos(dni[conocidos], ano[conocidos])
    error: dict[str, Any] = {"mediana_anos": None, "dentro_2_anos": None, "probados": 0}
    idx = df.index[conocidos]
    if len(idx) >= 200:
        rng = np.random.default_rng(semilla)
        prueba = rng.random(len(idx)) < 0.2
        t_ent = _tramos(dni[idx[~prueba]], ano[idx[~prueba]])
        est = _aplicar(t_ent, dni[idx[prueba]])
        dif = (est - ano[idx[prueba]]).abs().dropna()
        if len(dif):
            error = {"mediana_anos": float(dif.median()), "dentro_2_anos": float((dif <= 2).mean()),
                     "probados": int(len(dif))}
    return {"tramos": tramos, "error": error}


def edades_completas(df: pd.DataFrame, hoy: date, modelo: dict[str, Any] | None = None) -> pd.DataFrame:
    """edad (real o estimada) y estimada (bool) para cada persona."""
    real = edades(df["fecha_nac"], hoy).astype(float)
    modelo = modelo or modelo_edad(df)
    faltan = real.isna()
    ano_est = _aplicar(modelo["tramos"], _dni_numerico(df)[faltan])
    hoy_dec = hoy.year + (pd.Timestamp(hoy).dayofyear - 1) / 365.25
    est = np.floor(hoy_dec - ano_est)
    est = est.where(est >= 0)
    edad = real.copy()
    edad[faltan] = est
    return pd.DataFrame({"edad": edad, "estimada": faltan & edad.notna()}, index=df.index)


# ---------------------------------------------------------------------------
# Análisis
# ---------------------------------------------------------------------------
def _pct(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


def _rango(e: Any) -> str:
    return rango_edad(None if pd.isna(e) else int(e))


def _bandas(edad: pd.Series, bandas: list[tuple[int, int, str]]) -> pd.Series:
    out = pd.Series(None, index=edad.index, dtype=object)
    for d, h, nombre in bandas:
        out[(edad >= d) & (edad <= h)] = nombre
    return out


def piramide(df: pd.DataFrame, ed: pd.DataFrame, incluir_estimadas: bool = True) -> pd.DataFrame:
    edad = ed["edad"].where(incluir_estimadas | ~ed["estimada"])
    x = pd.DataFrame({"rango": edad.map(_rango), "sexo": df["sexo"].replace("", "Sin dato")})
    t = x.groupby(["rango", "sexo"]).size().unstack(fill_value=0)
    return t.reindex([r for r in _ORDEN_RANGOS if r in t.index])


def generaciones(df: pd.DataFrame, ed: pd.DataFrame, hoy: date) -> pd.DataFrame:
    g = _bandas(hoy.year - ed["edad"], GENERACIONES).fillna("Sin dato")
    t = pd.DataFrame({"gen": g, "sexo": df["sexo"]}).groupby("gen").agg(
        Personas=("sexo", "size"), Mujeres=("sexo", lambda s: (s == "Femenino").mean()))
    orden = [n for *_, n in GENERACIONES] + ["Sin dato"]
    return t.reindex([o for o in orden if o in t.index]).reset_index().rename(columns={"gen": "Generación"})


def segmentos(df: pd.DataFrame, ed: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Personas hoy en cada etapa de vida (con edad real o estimada): demanda potencial por servicio."""
    edad = ed["edad"]
    muj = df["sexo"] == "Femenino"
    hom = df["sexo"] == "Masculino"

    def tabla(mask: pd.Series, bandas: list[tuple[int, int, str]]) -> pd.DataFrame:
        b = _bandas(edad[mask], bandas)
        t = pd.DataFrame({"b": b, "est": ed["estimada"][mask]}).dropna(subset=["b"]).groupby("b").agg(
            Personas=("est", "size"), estimadas=("est", "sum"))
        t = t.reindex([n for *_, n in bandas]).fillna(0).astype(int)
        t["De ellas, con edad estimada"] = t.pop("estimadas")
        return t.reset_index().rename(columns={"b": "Edad"})

    return {
        "fertilidad": tabla(muj, FERTILIDAD),
        "andrologia": tabla(hom, [(18, 29, "18-29"), (30, 39, "30-39"), (40, 49, "40-49"), (50, 59, "50-59")]),
        "menopausia": tabla(muj, [(40, 44, "40-44 (perimenopausia)"), (45, 50, "45-50"), (51, 55, "51-55"),
                                  (56, 65, "56-65 (posmenopausia)")]),
        "pediatria": tabla(edad.notna(), PEDIATRIA),
        "mayores": tabla(edad.notna(), [(65, 74, "65-74"), (75, 84, "75-84"), (85, 120, "85 o más")]),
    }


def canales(df: pd.DataFrame, ed: pd.DataFrame) -> dict[str, Any]:
    origen = df["origen"].replace("", "Sin dato (carga en el sistema)")
    total = origen.value_counts().rename_axis("Origen").reset_index(name="Personas")
    total["%"] = total["Personas"] / max(len(df), 1)
    digital = df["origen"].isin(DIGITAL)
    x = pd.DataFrame({"rango": ed["edad"].map(_rango), "digital": digital, "sexo": df["sexo"]})
    por_edad = (x[x["rango"] != "Sin dato"].groupby("rango")["digital"].mean()
                .reindex([r for r in _ORDEN_RANGOS if r != "Sin dato"]).dropna()
                .rename("% turnos online").rename_axis("Edad").reset_index())
    por_sexo = x[x["sexo"].isin(["Femenino", "Masculino"])].groupby("sexo")["digital"].mean()
    return {"origen": total, "digital": float(digital.mean()) if len(df) else 0.0, "por_edad": por_edad,
            "por_sexo": por_sexo}


def documentacion(df: pd.DataFrame, ed: pd.DataFrame) -> dict[str, Any]:
    dni = _dni_numerico(df)
    extranjero_res = dni >= DNI_EXTRANJEROS
    doc_ext = df["tipo_doc"].isin(["CI", "Pasaporte", "Doc. extranjero"])
    tipos = df["tipo_doc"].replace("", "Sin dato").value_counts().rename_axis("Tipo").reset_index(name="Personas")
    hc = df["historia_clinica"]
    formato = hc.map(lambda h: "Sin HC" if not h else (f"{len(h)} dígitos" if h.isdigit() else "Con letras"))
    hc_origen = pd.crosstab(formato.rename("Historia clínica"), df["origen"].replace("", "Sin dato")).reset_index()
    hc_origen.columns.name = None
    return {"tipos": tipos, "extranjeros_residentes": int(extranjero_res.sum()),
            "documento_extranjero": int(doc_ext.sum()),
            "sin_documento": int(df["tipo_doc"].isin(["NN", "RN"]).sum()),
            "con_hc": _pct((hc != "").sum(), len(df)), "hc_por_origen": hc_origen}


def crecimiento_base(df: pd.DataFrame) -> pd.DataFrame:
    """Personas nuevas por mes desde que la base se importa (primera_carga)."""
    f = pd.to_datetime(df["primera_carga"], errors="coerce")
    if f.dropna().dt.to_period("M").nunique() < 2:
        return pd.DataFrame()
    t = f.dt.to_period("M").value_counts().sort_index()
    return pd.DataFrame({"Mes": t.index.astype(str), "Personas nuevas": t.to_numpy()})


def hallazgos(df: pd.DataFrame, ed: pd.DataFrame, modelo: dict[str, Any], hoy: date) -> list[str]:
    n = len(df)
    if not n:
        return ["La base está vacía: importá el Excel del sistema de turnos."]

    def fmt(v: Any) -> str:
        return f"{int(v):,}".replace(",", ".")

    h: list[str] = []
    muj = df["sexo"] == "Femenino"
    h.append(f"La base institucional tiene {fmt(n)} personas: {muj.mean():.0%} mujeres y "
             f"{(df['sexo'] == 'Masculino').mean():.0%} varones.")
    real = ed["edad"].notna() & ~ed["estimada"]
    est = ed["estimada"]
    if est.any():
        err = modelo["error"]
        precision = ""
        if err["mediana_anos"] is not None:
            mediana = f"{err['mediana_anos']:.1f}".replace(".", ",")
            precision = (f" (en la mitad de los casos se equivoca {mediana} años o menos; "
                         f"el {err['dentro_2_anos']:.0%} queda a menos de 2 años)")
        h.append(f"Edad: {fmt(real.sum())} personas la tienen por fecha de nacimiento y a {fmt(est.sum())} más se la "
                 f"estimé por su número de DNI{precision}. Total con edad: {ed['edad'].notna().mean():.0%}.")
    edad = ed["edad"]
    if edad.notna().any():
        h.append(f"Edad mediana: {edad[muj].median():.0f} años las mujeres y "
                 f"{edad[df['sexo'] == 'Masculino'].median():.0f} los varones.")
        h.append(f"{fmt((muj & edad.between(30, 44)).sum())} mujeres tienen entre 30 y 44 años (la franja de mayor "
                 f"demanda en reproducción); {fmt((muj & edad.between(35, 44)).sum())} de ellas tienen 35 o más.")
        h.append(f"{fmt((muj & edad.between(45, 55)).sum())} mujeres están entre 45 y 55 años "
                 "(perimenopausia y menopausia).")
        ped = int(edad.between(0, 17).sum())
        if ped:
            h.append(f"{fmt(ped)} pacientes son menores de 18 ({_pct(ped, edad.notna().sum()):.0%} de quienes tienen "
                     f"edad); {fmt(edad.between(0, 5).sum())} tienen 5 años o menos.")
        h.append(f"{fmt((edad >= 65).sum())} tienen 65 años o más.")
    c = canales(df, ed)
    h.append(f"El {c['digital']:.0%} de la base usó los turnos online (alta o actualización de datos).")
    if len(c["por_edad"]) >= 2:
        orden = c["por_edad"].sort_values("% turnos online")
        h.append(f"Por edad, quienes más usan los turnos online tienen {orden.iloc[-1]['Edad']} años "
                 f"({orden.iloc[-1]['% turnos online']:.0%}); quienes menos, {orden.iloc[0]['Edad']} "
                 f"({orden.iloc[0]['% turnos online']:.0%}).")
    d = documentacion(df, ed)
    ext = d["extranjeros_residentes"] + d["documento_extranjero"]
    if ext:
        h.append(f"{fmt(ext)} personas son extranjeras ({fmt(d['extranjeros_residentes'])} con DNI de residente y "
                 f"{fmt(d['documento_extranjero'])} con pasaporte, cédula o documento de otro país).")
    sin = (df["fecha_nac"] == "").mean()
    if sin > 0.2:
        h.append(f"Al {sin:.0%} le falta la fecha de nacimiento: pedirla al dar el turno haría exactas todas las "
                 "edades.")
    return h


def analizar(df: pd.DataFrame, hoy: date) -> dict[str, Any]:
    modelo = modelo_edad(df)
    ed = edades_completas(df, hoy, modelo)
    return {"modelo": modelo, "edades": ed, "hallazgos": hallazgos(df, ed, modelo, hoy)}


def dataset_investigacion(df: pd.DataFrame, ed: pd.DataFrame, hoy: date, semilla: int | None = None) -> bytes:
    """Excel anonimizado: sin nombre, documento ni HC; edad en rangos (marcando si es estimada); orden al azar."""
    x = pd.DataFrame({
        "sexo": df["sexo"].replace("", "Sin dato"),
        "rango_edad": ed["edad"].map(_rango),
        "edad_estimada_por_dni": ed["estimada"].map({True: "Sí", False: "No"}).where(ed["edad"].notna(), ""),
        "generacion": _bandas(hoy.year - ed["edad"], GENERACIONES).fillna("Sin dato"),
        "estado_civil": df["estado_civil"].replace("", "Sin dato"),
        "origen": df["origen"].replace("", "Sin dato"),
        "extranjero": ((_dni_numerico(df) >= DNI_EXTRANJEROS) |
                       df["tipo_doc"].isin(["CI", "Pasaporte", "Doc. extranjero"])).map({True: "Sí", False: "No"}),
    })
    x = x.sample(frac=1, random_state=semilla).reset_index(drop=True)
    x.insert(0, "id_anonimo", [f"A{i + 1:06d}" for i in range(len(x))])
    resumen = x.groupby(["rango_edad", "sexo"]).size().unstack(fill_value=0)
    resumen = resumen.reindex([r for r in _ORDEN_RANGOS if r in resumen.index])
    diccionario = pd.DataFrame([
        ("id_anonimo", "Número al azar, distinto en cada exportación. No permite volver a la persona."),
        ("rango_edad", "Edad hoy en rangos. Si no había fecha de nacimiento, se estimó por el número de DNI."),
        ("edad_estimada_por_dni", "Sí = la edad es estimada."),
        ("generacion", "Según el año de nacimiento (real o estimado)."),
        ("origen", "Cómo se registró la persona (turnos online, migración de laboratorio, carga en el sistema)."),
        ("extranjero", "DNI de residente extranjero (92 millones o más) o documento de otro país."),
    ], columns=["Campo", "Qué es"])
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as xl:
        pd.DataFrame({"Vitae · base institucional de pacientes (anonimizada)": [
            f"Generado el {hoy:%d/%m/%Y}. Sin nombre, documento, historia clínica ni fechas.",
            "Uso interno para investigación y estadística. Prohibido intentar reidentificar a las personas.",
            "Antes de publicar, consultar al comité de ética que corresponda.",
        ]}).to_excel(xl, sheet_name="Leeme", index=False)
        x.to_excel(xl, sheet_name="Pacientes", index=False)
        resumen.to_excel(xl, sheet_name="Edad x sexo")
        diccionario.to_excel(xl, sheet_name="Diccionario", index=False)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Pantalla «Inteligencia»
# ---------------------------------------------------------------------------
SECCIONES = ["Resumen", "Demografía", "Etapas de vida", "Canales", "Documentación", "Edad estimada",
             "Investigación"]
_PALETA = {"Femenino": "#8C2853", "Masculino": "#4F7CAC"}
_SERIES = ["#8C2853", "#4F7CAC", "#C9895B", "#5E9C76", "#8E6FB1", "#D1A93F"]

_CSS = """<style>
.pin-hallazgos{background:#fff;border:1px solid #E8D9E0;border-radius:14px;padding:.8rem 1.1rem;margin:.3rem 0 1rem}
.pin-hallazgos li{margin:.3rem 0;color:#2B2226}
.pin-tit{font-weight:700;color:#5E1A39;margin-bottom:.3rem}
.pin-nota{color:#8A7F85;font-size:.82rem}
</style>"""


def _num(n: Any) -> str:
    return f"{int(n):,}".replace(",", ".")


def _fmt_pct(df: pd.DataFrame, columnas: list[str]) -> pd.DataFrame:
    out = df.copy()
    for c in columnas:
        if c in out.columns:
            out[c] = out[c].map(lambda v: f"{v:.0%}" if pd.notna(v) else "")
    return out


def _tabla(st: Any, df: pd.DataFrame | None) -> None:
    if df is None or df.empty:
        st.caption("Sin datos para mostrar.")
        return
    st.dataframe(df, hide_index=True, use_container_width=True)


def _barras(st: Any, df: pd.DataFrame, x: str, ys: list[str], *, horizontal: bool = False, alto: int = 300,
            color: str | None = None, porcentaje: bool = False) -> None:
    if df is None or df.empty:
        return
    try:
        import plotly.graph_objects as go

        fig = go.Figure()
        for i, y in enumerate(ys):
            c = color or _SERIES[i % len(_SERIES)]
            if horizontal:
                fig.add_bar(y=list(df[x]), x=list(df[y]), name=y, orientation="h", marker_color=c)
            else:
                fig.add_bar(x=list(df[x]), y=list(df[y]), name=y, marker_color=c)
        fig.update_layout(height=alto, margin=dict(l=10, r=10, t=10, b=10), plot_bgcolor="rgba(0,0,0,0)",
                          paper_bgcolor="rgba(0,0,0,0)", showlegend=len(ys) > 1, legend=dict(orientation="h", y=1.12))
        if horizontal:
            fig.update_yaxes(autorange="reversed")
        if porcentaje:
            (fig.update_xaxes if horizontal else fig.update_yaxes)(tickformat=".0%")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    except Exception:
        st.bar_chart(df.set_index(x)[ys])


def _piramide_grafico(st: Any, t: pd.DataFrame) -> None:
    con = t.drop(index="Sin dato", errors="ignore")
    if con.empty:
        return
    try:
        import plotly.graph_objects as go

        muj = con["Femenino"] if "Femenino" in con.columns else pd.Series(0, index=con.index)
        var = con["Masculino"] if "Masculino" in con.columns else pd.Series(0, index=con.index)
        fig = go.Figure()
        fig.add_bar(y=list(con.index), x=list(-muj), name="Mujeres", orientation="h", marker_color=_PALETA["Femenino"],
                    customdata=list(muj), hovertemplate="%{y}: %{customdata} mujeres<extra></extra>")
        fig.add_bar(y=list(con.index), x=list(var), name="Varones", orientation="h", marker_color=_PALETA["Masculino"],
                    hovertemplate="%{y}: %{x} varones<extra></extra>")
        tope = max(int(muj.max()), int(var.max()), 1)
        paso = max(1, int(round(tope / 3, -2)) or 1)
        marcas = list(range(0, tope + 1, paso))
        fig.update_layout(barmode="overlay", bargap=0.08, height=400, margin=dict(l=10, r=10, t=10, b=10),
                          legend=dict(orientation="h", y=1.08), plot_bgcolor="rgba(0,0,0,0)",
                          paper_bgcolor="rgba(0,0,0,0)",
                          xaxis=dict(tickvals=[-m for m in marcas[::-1]] + marcas[1:],
                                     ticktext=[_num(m) for m in marcas[::-1]] + [_num(m) for m in marcas[1:]]))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    except Exception:
        st.bar_chart(con)


def render_inteligencia(st: Any, almacen: Any, df: pd.DataFrame, hoy: date) -> None:
    from vitae.modulos.pacientes import _registrar_una_vez, completitud

    st.markdown(_CSS, unsafe_allow_html=True)
    if df.empty:
        st.info("La base está vacía: importá el Excel del sistema de turnos para ver el análisis.")
        return
    firma = (len(df), str(df["actualizado"].max()) if "actualizado" in df.columns else "", hoy.isoformat())
    if st.session_state.get("pin_firma") != firma or "pin_res" not in st.session_state:
        with st.spinner("Analizando la base de pacientes…"):
            st.session_state["pin_res"] = analizar(df, hoy)
            st.session_state["pin_firma"] = firma
    r = st.session_state["pin_res"]
    ed, modelo = r["edades"], r["modelo"]
    seccion = st.pills("Sección", SECCIONES, key="pin_seccion", default="Resumen",
                       label_visibility="collapsed") or "Resumen"
    _registrar_una_vez(almacen, "inteligencia", seccion)
    muj = df["sexo"] == "Femenino"

    if seccion == "Resumen":
        d = documentacion(df, ed)
        c = st.columns(4)
        c[0].metric("Personas", _num(len(df)))
        c[1].metric("Mujeres", f"{muj.mean():.0%}")
        c[2].metric("Con edad", f"{ed['edad'].notna().mean():.0%}",
                    help="Por fecha de nacimiento o estimada por el número de DNI.")
        c[3].metric("Edad mediana", f"{ed['edad'].median():.0f} años" if ed["edad"].notna().any() else "—")
        c = st.columns(4)
        c[0].metric("Mujeres de 30 a 44 años", _num((muj & ed["edad"].between(30, 44)).sum()))
        c[1].metric("Menores de 18", _num(ed["edad"].between(0, 17).sum()))
        c[2].metric("Usaron turnos online", f"{df['origen'].isin(DIGITAL).mean():.0%}")
        c[3].metric("Extranjeros", _num(d["extranjeros_residentes"] + d["documento_extranjero"]))
        items = "".join(f"<li>{h}</li>" for h in r["hallazgos"])
        st.markdown(f'<div class="pin-hallazgos"><div class="pin-tit">💡 Lo más importante de la base</div>'
                    f'<ul>{items}</ul></div>', unsafe_allow_html=True)
        cb = crecimiento_base(df)
        if not cb.empty:
            st.markdown("##### Crecimiento de la base (personas nuevas por mes)")
            _barras(st, cb, "Mes", ["Personas nuevas"], alto=260)
        else:
            st.caption("Desde la próxima importación vas a ver acá cuántas personas nuevas suma la base cada mes.")

    elif seccion == "Demografía":
        incluir = st.toggle("Incluir edades estimadas por DNI", value=True, key="pin_estimadas",
                            help="Si lo apagás, sólo cuentan las personas con fecha de nacimiento.")
        t = piramide(df, ed, incluir)
        st.markdown("##### Pirámide de edad")
        _piramide_grafico(st, t)
        sin = int(t.loc["Sin dato"].sum()) if "Sin dato" in t.index else 0
        st.caption(f"{_num(sin)} personas sin edad" + (" (ni fecha de nacimiento ni DNI que permita estimarla)."
                                                      if incluir else " por fecha de nacimiento."))
        izq, der = st.columns(2)
        with izq:
            st.markdown("##### Generaciones")
            g = generaciones(df, ed, hoy)
            _barras(st, g[g["Generación"] != "Sin dato"], "Generación", ["Personas"], horizontal=True, alto=280)
            _tabla(st, _fmt_pct(g, ["Mujeres"]))
        with der:
            st.markdown("##### Sexo")
            sx = df["sexo"].replace("", "Sin dato").value_counts().rename_axis("Sexo").reset_index(name="Personas")
            sx["%"] = sx["Personas"] / len(df)
            _tabla(st, _fmt_pct(sx, ["%"]))
            st.markdown("##### Estado civil")
            ec = df["estado_civil"][df["estado_civil"] != ""].value_counts().rename_axis("Estado civil") \
                .reset_index(name="Personas")
            _tabla(st, ec)
            st.caption(f"Sólo el {(df['estado_civil'] != '').mean():.0%} de la base tiene este dato.")

    elif seccion == "Etapas de vida":
        s = segmentos(df, ed)
        st.caption("Cuántas personas de la base están hoy en cada etapa de la vida: dimensiona la demanda "
                   "potencial de cada servicio.")
        izq, der = st.columns(2)
        with izq:
            st.markdown("##### Mujeres en edad reproductiva")
            _barras(st, s["fertilidad"], "Edad", ["Personas"], alto=260, color=_PALETA["Femenino"])
            _tabla(st, s["fertilidad"])
            st.markdown("##### Mujeres: perimenopausia y menopausia")
            _tabla(st, s["menopausia"])
        with der:
            st.markdown("##### Varones (salud reproductiva masculina)")
            _barras(st, s["andrologia"], "Edad", ["Personas"], alto=260, color=_PALETA["Masculino"])
            _tabla(st, s["andrologia"])
            st.markdown("##### Niñas, niños y adolescentes")
            _tabla(st, s["pediatria"])
            st.markdown("##### Mayores de 65")
            _tabla(st, s["mayores"])

    elif seccion == "Canales":
        c = canales(df, ed)
        k = st.columns(3)
        k[0].metric("Usaron turnos online", f"{c['digital']:.0%}")
        if "Femenino" in c["por_sexo"].index:
            k[1].metric("Mujeres", f"{c['por_sexo']['Femenino']:.0%}")
        if "Masculino" in c["por_sexo"].index:
            k[2].metric("Varones", f"{c['por_sexo']['Masculino']:.0%}")
        st.markdown("##### Cómo se registró cada persona")
        _tabla(st, _fmt_pct(c["origen"], ["%"]))
        st.markdown("##### Uso de turnos online por edad")
        _barras(st, c["por_edad"], "Edad", ["% turnos online"], alto=280, porcentaje=True)
        st.caption("Ayuda a decidir por dónde comunicarse con cada grupo de edad (web, WhatsApp, teléfono, recepción).")

    elif seccion == "Documentación":
        d = documentacion(df, ed)
        k = st.columns(4)
        k[0].metric("DNI de residente extranjero", _num(d["extranjeros_residentes"]),
                    help="DNI argentinos desde 92 millones: se otorgan a personas extranjeras residentes.")
        k[1].metric("Pasaporte, cédula o doc. de otro país", _num(d["documento_extranjero"]))
        k[2].metric("Sin documento (NN / recién nacidos)", _num(d["sin_documento"]))
        k[3].metric("Con historia clínica", f"{d['con_hc']:.0%}")
        izq, der = st.columns(2)
        with izq:
            st.markdown("##### Tipos de documento")
            _tabla(st, d["tipos"])
        with der:
            st.markdown("##### Historia clínica según el origen del registro")
            _tabla(st, d["hc_por_origen"])
            st.caption("Los distintos largos de número suelen venir de sistemas distintos (p. ej. la migración del "
                       "laboratorio).")
        st.markdown("##### Datos completos")
        for k_, v in completitud(df).items():
            st.progress(min(max(v, 0.0), 1.0), text=f"{k_}: {v:.0%}")

    elif seccion == "Edad estimada":
        err = modelo["error"]
        con_fecha = int((ed["edad"].notna() & ~ed["estimada"]).sum())
        k = st.columns(4)
        k[0].metric("Con fecha de nacimiento", _num(con_fecha))
        k[1].metric("Edad estimada por DNI", _num(ed["estimada"].sum()))
        k[2].metric("Error típico", f"{err['mediana_anos']:.1f} años".replace(".", ",") if err["mediana_anos"] is not None
                    else "—",
                    help="Mediana de la diferencia entre la edad estimada y la real, medida en un 20% de personas "
                         "con fecha de nacimiento que se apartaron para probar.")
        k[3].metric("A menos de 2 años", f"{err['dentro_2_anos']:.0%}" if err["dentro_2_anos"] is not None else "—")
        st.markdown(
            "En Argentina el número de DNI se asigna en orden: cuanto más alto, más joven la persona. Con las "
            f"{_num(con_fecha)} personas que tienen fecha de nacimiento, Vitae aprende qué año corresponde a cada "
            "tramo de números y lo usa para estimar la edad de quienes no la tienen. Sólo se usan los tramos donde "
            "la relación es firme; los DNI de extranjeros (92 millones o más), las LE/LC viejas y los documentos "
            "dudosos quedan sin estimar.")
        tr = modelo["tramos"]
        if not tr.empty:
            t = pd.DataFrame({"DNI desde": (tr["tramo"] * TRAMO_DNI).map(_num),
                              "Año de nacimiento típico": tr["ano"].round(0).astype(int),
                              "Personas para aprender": tr["n"].astype(int)})
            with st.expander("Ver la tabla DNI → año de nacimiento"):
                _tabla(st, t)
        st.markdown('<div class="pin-nota">Las edades estimadas nunca se escriben en la base: sólo se usan en estos '
                    'análisis y se marcan en las exportaciones.</div>', unsafe_allow_html=True)

    else:  # Investigación
        st.markdown("##### Datos para investigación")
        st.caption("Excel anonimizado de toda la base: sin nombre, documento, historia clínica ni fechas; edad en "
                   "rangos (marcando las estimadas), generación, estado civil, origen y si es extranjero. Antes de "
                   "publicar, pasar por el comité de ética que corresponda.")
        if st.button("Preparar Excel para investigación", key="pin_inv_xl", icon=":material/science:"):
            st.session_state["pin_inv_bytes"] = dataset_investigacion(df, ed, hoy)
            almacen.registrar("exportar_investigacion", f"{len(df)} personas")
        if st.session_state.get("pin_inv_bytes"):
            st.download_button("Descargar Excel para investigación", st.session_state["pin_inv_bytes"],
                               file_name=f"vitae_pacientes_investigacion_{hoy:%Y%m%d}.xlsx", key="pin_inv_dl",
                               icon=":material/download:")
        st.markdown("##### Preguntas que la base permite estudiar")
        st.markdown(
            "- Perfil de edad y sexo de quienes se atienden en Vitae, y cómo cambia con cada importación.\n"
            "- Tamaño de cada población (edad reproductiva, menopausia, pediatría, mayores) para planificar "
            "servicios y equipos.\n"
            "- Adopción de turnos online por edad y sexo.\n"
            "- Peso de pacientes extranjeros.\n"
            "- Calidad del registro (datos faltantes, documentos dudosos) y su evolución."
        )
