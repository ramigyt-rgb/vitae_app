from __future__ import annotations

from io import BytesIO
from typing import Any, Callable, Dict, Iterable
import math
import re
import unicodedata

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

try:
    from vitae.core.ui import encabezado_modulo, sin_icono
except Exception:  # pragma: no cover
    encabezado_modulo = None

    def sin_icono(texto):
        return re.sub(r":material/[a-z0-9_]+:\s*", "", str(texto))

try:
    from vitae.core.movil import es_movil
except Exception:  # pragma: no cover
    def es_movil() -> bool:
        return False

try:
    from vitae.core.database import get_df
except Exception:  # pragma: no cover - solo para que el módulo pueda importarse aislado
    get_df = None


VITAE_COLORS = [
    "#B1567E",
    "#C56D92",
    "#8F3F66",
    "#D892B0",
    "#71324F",
    "#E8B8CC",
    "#9F4A70",
    "#F1D5E1",
]
VITAE_DARK = "#4A1830"
VITAE_PINK = "#B1567E"
VITAE_SOFT = "#F8EAF0"
VITAE_GREEN = "#5F9D78"
VITAE_AMBER = "#C58A32"
VITAE_RED = "#B14E5E"

px.defaults.color_discrete_sequence = VITAE_COLORS


# -----------------------------------------------------------------------------
# Utilidades de lectura: tolerantes a cambios de encabezados del Sheet
# -----------------------------------------------------------------------------
def _txt(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _norm(value: Any) -> str:
    txt = _txt(value).lower()
    txt = unicodedata.normalize("NFKD", txt)
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    txt = re.sub(r"[^a-z0-9]+", "_", txt).strip("_")
    return txt


def _detect_column(df: pd.DataFrame, *candidates: str) -> str | None:
    mapping = {_norm(c): c for c in df.columns}
    for candidate in candidates:
        col = mapping.get(_norm(candidate))
        if col is not None:
            return col
    # segunda pasada: coincidencia parcial segura para encabezados como "importe pesos"
    normalized_candidates = [_norm(c) for c in candidates]
    for normalized, original in mapping.items():
        if any(c and (normalized.startswith(c) or c.startswith(normalized)) for c in normalized_candidates):
            return original
    return None


def _series_text(df: pd.DataFrame, col: str | None) -> pd.Series:
    if col is None or col not in df.columns:
        return pd.Series([""] * len(df), index=df.index, dtype="object")
    s = df.loc[:, col]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    return s.map(_txt)


def _series_number(df: pd.DataFrame, col: str | None) -> pd.Series:
    if col is None or col not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index, dtype="float64")
    s = df.loc[:, col]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_numeric(s, errors="coerce").fillna(0.0).astype(float)

    raw = s.astype(str).str.strip()
    # AR: 1.234.567,89  |  US: 1234567.89  |  símbolos monetarios
    raw = raw.str.replace(r"[^0-9,\.\-]", "", regex=True)

    def parse_one(v: str) -> float:
        if not v or v in {"-", ".", ","}:
            return 0.0
        try:
            if "," in v and "." in v:
                # el último separador define los decimales
                if v.rfind(",") > v.rfind("."):
                    v = v.replace(".", "").replace(",", ".")
                else:
                    v = v.replace(",", "")
            elif "," in v:
                tail = v.split(",")[-1]
                v = v.replace(".", "")
                v = v.replace(",", ".") if len(tail) <= 2 else v.replace(",", "")
            elif re.fullmatch(r"-?[1-9]\d{0,2}(\.\d{3})+", v):
                # Punto de miles sin decimales (150.000 · 1.500.000): antes se leía 150 o 1.500.
                v = v.replace(".", "")
            elif v.count(".") > 1:
                parts = v.split(".")
                v = "".join(parts[:-1]) + "." + parts[-1]
            return float(v)
        except Exception:
            return 0.0

    return raw.map(parse_one).astype(float)


def _series_date(df: pd.DataFrame, col: str | None) -> pd.Series:
    if col is None or col not in df.columns:
        return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    s = df.loc[:, col]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    if pd.api.types.is_datetime64_any_dtype(s):
        return pd.to_datetime(s, errors="coerce")
    raw = s.astype(str).str.strip()
    out = pd.to_datetime(raw, format="%Y-%m-%d", errors="coerce")
    missing = out.isna()
    if missing.any():
        out.loc[missing] = pd.to_datetime(raw.loc[missing], dayfirst=True, errors="coerce")
    return out


def _parse_month_series(s: pd.Series) -> pd.Series:
    raw = s.astype(str).str.strip()
    out = pd.to_datetime(raw, format="%Y-%m", errors="coerce")
    missing = out.isna()
    if missing.any():
        out.loc[missing] = pd.to_datetime(raw.loc[missing], dayfirst=True, errors="coerce")
    return out


def _fmt_money(value: float | int | None) -> str:
    try:
        val = float(value or 0)
    except Exception:
        val = 0.0
    formatted = f"{val:,.0f}"
    formatted = formatted.replace(",", ".")
    return f"$ {formatted}"


def _fmt_pct(value: float | int | None) -> str:
    try:
        return f"{float(value):.1f}%".replace(".", ",")
    except Exception:
        return "0,0%"


def _weighted_average(values: pd.Series, weights: pd.Series) -> float:
    v = pd.to_numeric(values, errors="coerce")
    w = pd.to_numeric(weights, errors="coerce").fillna(0)
    mask = v.notna() & (w > 0)
    if not mask.any():
        return 0.0
    return float((v.loc[mask] * w.loc[mask]).sum() / w.loc[mask].sum())


def _safe_div(a: float, b: float) -> float:
    return float(a / b) if b else 0.0


def _is_facturacion(module_name: str, table: str) -> bool:
    ident = _norm(f"{module_name} {table}")
    return "facturacion" in ident and ("vmr" in ident or re.search(r"(^|_)vm($|_)", ident) is not None)


# -----------------------------------------------------------------------------
# Normalización de la cartera
# -----------------------------------------------------------------------------
def _prepare_facturacion(df_original: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str | None]]:
    data = df_original.copy(deep=True)

    cols = {
        "mes": _detect_column(data, "mes", "periodo", "mes_facturacion"),
        "paciente": _detect_column(data, "afiliado", "paciente", "paciente_afiliado", "nombre_paciente", "beneficiario"),
        "obra_social": _detect_column(data, "obra_social", "obra social", "financiador", "cobertura", "os"),
        "procedimiento": _detect_column(data, "procedimiento", "practica", "práctica", "prestacion", "prestación", "servicio"),
        "medico": _detect_column(data, "medico", "médico", "profesional", "medico_responsable", "médico responsable"),
        "fecha_servicio": _detect_column(data, "fecha", "fecha_servicio", "fecha_practica", "fecha_atencion", "fecha_ingreso", "fecha_procedimiento"),
        "fecha_factura": _detect_column(data, "fecha_factura", "fecha factura", "fecha_emision", "fecha emisión"),
        "numero_factura": _detect_column(data, "numero_factura", "nro_factura", "n° factura", "n.º factura", "factura", "comprobante"),
        "vencimiento": _detect_column(data, "vencimiento", "fecha_vencimiento", "fecha vencimiento", "vence"),
        "fecha_pago": _detect_column(data, "fecha_pago", "fecha pago", "fecha_cobro", "fecha cobro"),
        "monto": _detect_column(data, "valor_pesos", "importe", "monto", "facturado", "total", "valor", "importe_facturado"),
        "cobrado": _detect_column(data, "cobrado", "pagado", "importe_cobrado", "monto_cobrado", "abonado"),
        "saldo": _detect_column(data, "saldo", "pendiente", "saldo_pendiente", "deuda", "a_cobrar"),
        "estado": _detect_column(data, "estado", "estado_factura", "situacion", "situación"),
    }

    data["_paciente"] = _series_text(data, cols["paciente"])
    data["_obra_social"] = _series_text(data, cols["obra_social"]).replace("", "SIN OBRA SOCIAL")
    data["_procedimiento"] = _series_text(data, cols["procedimiento"]).replace("", "SIN PROCEDIMIENTO")
    data["_medico"] = _series_text(data, cols["medico"]).replace("", "SIN MÉDICO")
    data["_numero_factura"] = _series_text(data, cols["numero_factura"])
    data["_estado_original"] = _series_text(data, cols["estado"])
    data["_estado_norm"] = data["_estado_original"].map(_norm)

    data["_fecha_servicio"] = _series_date(data, cols["fecha_servicio"])
    data["_fecha_factura"] = _series_date(data, cols["fecha_factura"])
    data["_vencimiento"] = _series_date(data, cols["vencimiento"])
    data["_fecha_pago"] = _series_date(data, cols["fecha_pago"])

    if cols["mes"]:
        data["_fecha_mes"] = _parse_month_series(_series_text(data, cols["mes"]))
    else:
        data["_fecha_mes"] = pd.NaT

    data["_fecha_base"] = data["_fecha_factura"].combine_first(data["_fecha_servicio"]).combine_first(data["_fecha_mes"])
    data["_monto"] = _series_number(data, cols["monto"]).clip(lower=0)

    cancel_words = ("anulad", "cancelad", "rechazad", "baja")
    paid_words = ("cobrad", "pagad", "abonad", "cancelado_pago", "completo", "cerrad")
    cancelled = data["_estado_norm"].map(lambda x: any(w in x for w in cancel_words))
    paid_by_state = data["_estado_norm"].map(lambda x: any(w in x for w in paid_words))

    explicit_collected = _series_number(data, cols["cobrado"]).clip(lower=0) if cols["cobrado"] else pd.Series(0.0, index=data.index)
    explicit_balance = _series_number(data, cols["saldo"]).clip(lower=0) if cols["saldo"] else pd.Series(0.0, index=data.index)

    if cols["cobrado"]:
        collected = explicit_collected.clip(upper=data["_monto"])
    elif cols["saldo"]:
        plausible = (explicit_balance >= 0) & (explicit_balance <= data["_monto"])
        collected = (data["_monto"] - explicit_balance.where(plausible, data["_monto"])).clip(lower=0)
    else:
        collected = pd.Series(0.0, index=data.index)
        collected.loc[paid_by_state | data["_fecha_pago"].notna()] = data.loc[paid_by_state | data["_fecha_pago"].notna(), "_monto"]

    # Si el Sheet dice cobrado/pagado y no existe monto explícito cobrado, se considera cobro total.
    if not cols["cobrado"]:
        collected.loc[paid_by_state] = data.loc[paid_by_state, "_monto"]

    data["_cobrado"] = collected.clip(lower=0, upper=data["_monto"])
    data["_pendiente"] = (data["_monto"] - data["_cobrado"]).clip(lower=0)
    data.loc[cancelled, ["_monto", "_cobrado", "_pendiente"]] = 0.0
    data["_cancelado"] = cancelled

    today = pd.Timestamp.today().normalize()
    data["_dias_vencido"] = (today - data["_vencimiento"]).dt.days
    data["_dias_vencido"] = data["_dias_vencido"].where(data["_vencimiento"].notna(), pd.NA)
    data["_vencido"] = (data["_pendiente"] > 0) & data["_vencimiento"].notna() & (data["_vencimiento"] < today)
    data["_dias_cobro"] = (data["_fecha_pago"] - data["_fecha_factura"].combine_first(data["_fecha_servicio"])).dt.days
    data.loc[data["_dias_cobro"] < 0, "_dias_cobro"] = pd.NA

    def exec_state(row: pd.Series) -> str:
        if row["_cancelado"]:
            return "Anulado"
        if row["_pendiente"] <= 0 and row["_monto"] > 0:
            return "Cobrado"
        if row["_vencido"]:
            return "Vencido"
        if row["_pendiente"] > 0:
            return "Pendiente"
        return "Sin importe"

    data["_estado_ejecutivo"] = data.apply(exec_state, axis=1)
    data["_mes"] = data["_fecha_base"].dt.to_period("M").astype(str).replace("NaT", "Sin fecha")
    return data, cols


# -----------------------------------------------------------------------------
# UI helpers
# -----------------------------------------------------------------------------
def _css() -> None:
    st.markdown(
        """
        <style>
        /* ===== PALETA VITAE: misma línea visual que el resto de la app ===== */
        html, body, [data-testid="stAppViewContainer"], .stApp {
            background:
                #FFFFFF !important;
            color:#3F2430;
        }
        [data-testid="stHeader"]{background:rgba(237,211,222,.96) !important;border-bottom:1px solid rgba(177,86,126,.09)}
        [data-testid="stMainBlockContainer"], .block-container{background:transparent !important}

        /* Hero integrado: ya no es bordó oscuro; usa el mismo rosa suave de VITAE */
        .fx-hero{
            background:#FFFFFF;
            border:1px solid rgba(177,86,126,.17);
            padding:22px 26px;border-radius:24px;color:#4A1830;margin:.2rem 0 1rem 0;
            box-shadow:0 15px 42px rgba(92,38,63,.08);position:relative;overflow:hidden;backdrop-filter:blur(8px)
        }
        .fx-hero:after{content:"";position:absolute;width:285px;height:285px;border-radius:50%;right:-78px;top:-155px;background:rgba(177,86,126,.11)}
        .fx-hero:before{content:"";position:absolute;width:150px;height:150px;border-radius:50%;right:55px;bottom:-116px;background:rgba(255,255,255,.34)}
        .fx-kicker{font-size:.78rem;letter-spacing:.14em;text-transform:uppercase;font-weight:850;color:#A54470;opacity:1}
        .fx-title{font-size:1.8rem;font-weight:850;letter-spacing:-.03em;margin:.18rem 0 .1rem 0;color:#4A1830}
        .fx-sub{font-size:.95rem;color:#6F5864;opacity:1;max-width:960px;line-height:1.5}

        .fx-card{background:rgba(255,255,255,.82);border:1px solid rgba(177,86,126,.14);border-radius:18px;padding:15px 17px;min-height:110px;box-shadow:0 9px 28px rgba(74,24,48,.055);backdrop-filter:blur(5px)}
        .fx-label{font-size:.82rem;color:#6E5863;font-weight:650;margin-bottom:5px}
        .fx-value{font-size:1.43rem;color:#4A1830;font-weight:850;letter-spacing:-.02em}
        .fx-foot{font-size:.76rem;color:#8A707C;margin-top:6px}
        .fx-section{margin-top:.7rem;margin-bottom:.4rem;font-size:1.26rem;font-weight:850;color:#4A1830}
        .fx-chip{display:inline-block;padding:.22rem .55rem;border-radius:999px;background:#F8EAF0;color:#8F3F66;font-size:.76rem;font-weight:750;margin:.1rem .2rem .1rem 0}

        div[data-testid="stMetric"]{background:rgba(255,255,255,.82);border:1px solid rgba(177,86,126,.14);border-radius:18px;padding:13px 15px;box-shadow:0 9px 28px rgba(74,24,48,.05)}
        div[data-testid="stDataFrame"]{border-radius:16px;overflow:hidden}

        /* Controles: gris muy claro como el módulo original, acento Vitae */
        div[data-baseweb="select"] > div,
        div[data-testid="stTextInput"] input,
        div[data-testid="stNumberInput"] input,
        div[data-testid="stDateInput"] input{background:#F2F3F7 !important;border-color:rgba(116,86,100,.10) !important}
        div[data-testid="stRadio"] [data-baseweb="radio"] div:first-child{border-color:#C45F87 !important}
        div[data-testid="stRadio"] [aria-checked="true"] div:first-child{background:#D7587E !important;border-color:#D7587E !important}

        /* Tabs en la misma línea rosa de VITAE */
        button[data-baseweb="tab"]{color:#604550 !important}
        button[data-baseweb="tab"][aria-selected="true"]{color:#C45279 !important}
        div[data-baseweb="tab-highlight"]{background-color:#D85C81 !important}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _hero(company: str) -> None:
    st.markdown(
        f"""
        <div class="fx-hero">
          <div class="fx-kicker">VITAE · Revenue Intelligence</div>
          <div class="fx-title">Centro Inteligente de Facturación {company}</div>
          <div class="fx-sub">Facturación, cobranza, mora, velocidad de cobro, concentración, producción, calidad de datos, proyección y prioridades de gestión en una sola capa ejecutiva. Todo se calcula sobre la información ya cargada en Google Sheets; este centro no modifica la base.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _kpi(label: str, value: str, foot: str = "") -> None:
    st.markdown(
        f'<div class="fx-card"><div class="fx-label">{label}</div><div class="fx-value">{value}</div><div class="fx-foot">{foot}</div></div>',
        unsafe_allow_html=True,
    )


def _clean_plot(fig: go.Figure, height: int = 390) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=45, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#5F4B55"),
        legend_title_text="",
    )
    fig.update_xaxes(gridcolor="rgba(120,90,105,.10)")
    fig.update_yaxes(gridcolor="rgba(120,90,105,.10)")
    return fig


def _filter_panel(data: pd.DataFrame, key: str) -> pd.DataFrame:
    today = pd.Timestamp.today().normalize()
    date_values = data["_fecha_base"].dropna()
    min_date = date_values.min().date() if not date_values.empty else today.date()
    max_date = date_values.max().date() if not date_values.empty else today.date()

    c1, c2 = st.columns([1, 2])
    with c1:
        period = st.selectbox(
            "Período",
            ["Año actual", "Mes actual", "Últimos 3 meses", "Últimos 6 meses", "Últimos 12 meses", "Todo el historial", "Rango personalizado"],
            key=f"{key}_period",
        )
    with c2:
        search = st.text_input(
            "Buscar",
            placeholder="Paciente, factura, obra social, procedimiento, médico...",
            key=f"{key}_search",
        )

    start = pd.Timestamp(min_date)
    end = pd.Timestamp(max_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    if period == "Año actual":
        start = pd.Timestamp(year=today.year, month=1, day=1)
        end = pd.Timestamp(year=today.year, month=12, day=31, hour=23, minute=59, second=59)
    elif period == "Mes actual":
        start = today.replace(day=1)
        end = start + pd.offsets.MonthEnd(0) + pd.Timedelta(hours=23, minutes=59, seconds=59)
    elif period.startswith("Últimos"):
        months = int(re.search(r"\d+", period).group())
        start = (today - pd.DateOffset(months=months - 1)).replace(day=1)
        end = today + pd.Timedelta(hours=23, minutes=59, seconds=59)
    elif period == "Rango personalizado":
        r1, r2 = st.columns(2)
        start_d = r1.date_input("Desde", value=min_date, key=f"{key}_from")
        end_d = r2.date_input("Hasta", value=max_date, key=f"{key}_to")
        start = pd.Timestamp(start_d)
        end = pd.Timestamp(end_d) + pd.Timedelta(hours=23, minutes=59, seconds=59)

    filtered = data.copy()
    if period != "Todo el historial":
        filtered = filtered[filtered["_fecha_base"].between(start, end, inclusive="both")]

    f1, f2, f3, f4 = st.columns(4)
    obras = sorted(v for v in filtered["_obra_social"].dropna().astype(str).unique() if v)
    procs = sorted(v for v in filtered["_procedimiento"].dropna().astype(str).unique() if v)
    medicos = sorted(v for v in filtered["_medico"].dropna().astype(str).unique() if v)
    estados = ["Cobrado", "Pendiente", "Vencido", "Anulado", "Sin importe"]
    obra = f1.selectbox("Obra social", ["Todas"] + obras, key=f"{key}_os")
    proc = f2.selectbox("Procedimiento", ["Todos"] + procs, key=f"{key}_proc")
    med = f3.selectbox("Médico", ["Todos"] + medicos, key=f"{key}_med")
    estado = f4.selectbox("Estado", ["Todos"] + estados, key=f"{key}_state")

    if obra != "Todas":
        filtered = filtered[filtered["_obra_social"] == obra]
    if proc != "Todos":
        filtered = filtered[filtered["_procedimiento"] == proc]
    if med != "Todos":
        filtered = filtered[filtered["_medico"] == med]
    if estado != "Todos":
        filtered = filtered[filtered["_estado_ejecutivo"] == estado]

    if search.strip():
        needle = _norm(search)
        haystack_cols = ["_paciente", "_obra_social", "_procedimiento", "_medico", "_numero_factura", "_estado_original"]
        mask = pd.Series(False, index=filtered.index)
        for col in haystack_cols:
            mask |= filtered[col].astype(str).map(_norm).str.contains(re.escape(needle), na=False)
        filtered = filtered[mask]

    st.caption(f"Vista activa: {len(filtered):,} registros · fuente filtrada sin modificación del Sheet".replace(",", "."))
    return filtered


# -----------------------------------------------------------------------------
# Cálculos ejecutivos
# -----------------------------------------------------------------------------
def _metrics(df: pd.DataFrame) -> dict[str, float]:
    active = df[~df["_cancelado"]].copy()
    facturado = float(active["_monto"].sum())
    cobrado = float(active["_cobrado"].sum())
    pendiente = float(active["_pendiente"].sum())
    vencido = float(active.loc[active["_vencido"], "_pendiente"].sum())
    tasa = 100 * _safe_div(cobrado, facturado)
    mora = 100 * _safe_div(vencido, pendiente)
    ticket = _safe_div(facturado, int((active["_monto"] > 0).sum()))
    dso = _weighted_average(active["_dias_cobro"], active["_cobrado"])
    pacientes = int(active.loc[active["_paciente"] != "", "_paciente"].nunique())
    records = int(len(active))
    return {
        "facturado": facturado,
        "cobrado": cobrado,
        "pendiente": pendiente,
        "vencido": vencido,
        "tasa": tasa,
        "mora": mora,
        "ticket": ticket,
        "dso": dso,
        "pacientes": pacientes,
        "registros": records,
    }


def _monthly(df: pd.DataFrame) -> pd.DataFrame:
    active = df[(~df["_cancelado"]) & df["_fecha_base"].notna()].copy()
    if active.empty:
        return pd.DataFrame(columns=["mes", "Facturado", "Cobrado", "Pendiente", "Vencido"])
    active["mes"] = active["_fecha_base"].dt.to_period("M").dt.to_timestamp()
    grouped = active.groupby("mes", as_index=False).agg(
        Facturado=("_monto", "sum"),
        Cobrado=("_cobrado", "sum"),
        Pendiente=("_pendiente", "sum"),
    )
    venc = active.assign(_venc_amount=active["_pendiente"].where(active["_vencido"], 0.0)).groupby("mes", as_index=False)["_venc_amount"].sum()
    grouped = grouped.merge(venc, on="mes", how="left").rename(columns={"_venc_amount": "Vencido"})
    grouped["Etiqueta"] = grouped["mes"].dt.strftime("%b %Y")
    return grouped.sort_values("mes")


def _payer_table(df: pd.DataFrame) -> pd.DataFrame:
    active = df[(~df["_cancelado"]) & (df["_obra_social"] != "")].copy()
    if active.empty:
        return pd.DataFrame()
    active["_venc_amount"] = active["_pendiente"].where(active["_vencido"], 0.0)
    rows = []
    for payer, g in active.groupby("_obra_social"):
        fact = float(g["_monto"].sum())
        cob = float(g["_cobrado"].sum())
        pend = float(g["_pendiente"].sum())
        venc = float(g["_venc_amount"].sum())
        dso = _weighted_average(g["_dias_cobro"], g["_cobrado"])
        rate = 100 * _safe_div(cob, fact)
        overdue_ratio = 100 * _safe_div(venc, pend)
        if rate >= 95 and (dso <= 45 or dso == 0):
            risk = "Fuerte"
        elif rate >= 85 and (dso <= 65 or dso == 0):
            risk = "Sano"
        elif rate >= 70:
            risk = "Atención"
        else:
            risk = "Crítico"
        rows.append({
            "Obra social": payer,
            "Facturado": fact,
            "Cobrado": cob,
            "Pendiente": pend,
            "Vencido": venc,
            "Cobranza %": rate,
            "Mora %": overdue_ratio,
            "Días cobro": dso,
            "Registros": len(g),
            "Estado interno": risk,
        })
    out = pd.DataFrame(rows).sort_values("Facturado", ascending=False)
    total = out["Facturado"].sum()
    out["Participación %"] = 100 * out["Facturado"] / total if total else 0
    return out


def _group_table(df: pd.DataFrame, group_col: str, label: str) -> pd.DataFrame:
    active = df[~df["_cancelado"]].copy()
    if active.empty:
        return pd.DataFrame()
    active["_venc_amount"] = active["_pendiente"].where(active["_vencido"], 0.0)
    out = active.groupby(group_col, as_index=False).agg(
        Facturado=("_monto", "sum"),
        Cobrado=("_cobrado", "sum"),
        Pendiente=("_pendiente", "sum"),
        Vencido=("_venc_amount", "sum"),
        Registros=("_monto", "size"),
    ).rename(columns={group_col: label})
    out["Cobranza %"] = out.apply(lambda r: 100 * _safe_div(r["Cobrado"], r["Facturado"]), axis=1)
    total = out["Facturado"].sum()
    out["Participación %"] = 100 * out["Facturado"] / total if total else 0
    return out.sort_values("Facturado", ascending=False)


def _aging(df: pd.DataFrame) -> pd.DataFrame:
    pending = df[(~df["_cancelado"]) & (df["_pendiente"] > 0)].copy()
    if pending.empty:
        return pd.DataFrame(columns=["Tramo", "Pendiente", "Registros"])
    today = pd.Timestamp.today().normalize()
    pending["dias"] = (today - pending["_vencimiento"]).dt.days

    def bucket(row: pd.Series) -> str:
        if pd.isna(row["_vencimiento"]):
            return "Sin vencimiento"
        if row["dias"] <= 0:
            return "A vencer"
        if row["dias"] <= 30:
            return "1-30 días"
        if row["dias"] <= 60:
            return "31-60 días"
        if row["dias"] <= 90:
            return "61-90 días"
        return "+90 días"

    pending["Tramo"] = pending.apply(bucket, axis=1)
    order = ["A vencer", "1-30 días", "31-60 días", "61-90 días", "+90 días", "Sin vencimiento"]
    out = pending.groupby("Tramo", as_index=False).agg(Pendiente=("_pendiente", "sum"), Registros=("_pendiente", "size"))
    out["Tramo"] = pd.Categorical(out["Tramo"], categories=order, ordered=True)
    return out.sort_values("Tramo")


def _priority_queue(df: pd.DataFrame) -> pd.DataFrame:
    pending = df[(~df["_cancelado"]) & (df["_pendiente"] > 0)].copy()
    if pending.empty:
        return pd.DataFrame()
    today = pd.Timestamp.today().normalize()
    pending["Días vencido"] = (today - pending["_vencimiento"]).dt.days
    pending["Días vencido"] = pending["Días vencido"].fillna(0).clip(lower=0)
    max_amount = max(float(pending["_pendiente"].max()), 1.0)
    pending["_score"] = (pending["_pendiente"] / max_amount) * 60 + (pending["Días vencido"].clip(upper=120) / 120) * 40
    pending.loc[pending["_vencimiento"].isna(), "_score"] += 5

    def priority(row: pd.Series) -> str:
        if row["Días vencido"] >= 60 or row["_score"] >= 65:
            return "Crítica"
        if row["Días vencido"] >= 15 or row["_score"] >= 40:
            return "Alta"
        if row["Días vencido"] > 0:
            return "Media"
        return "Preventiva"

    pending["Prioridad"] = pending.apply(priority, axis=1)
    pending["Impacto"] = pending["_pendiente"] * (1 + pending["Días vencido"] / 90)
    out = pd.DataFrame({
        "Prioridad": pending["Prioridad"],
        "Obra social": pending["_obra_social"],
        "Paciente": pending["_paciente"],
        "Factura": pending["_numero_factura"],
        "Procedimiento": pending["_procedimiento"],
        "Vencimiento": pending["_vencimiento"],
        "Días vencido": pending["Días vencido"].astype(int),
        "Pendiente": pending["_pendiente"],
        "Score": pending["_score"].round(1),
        "Impacto": pending["Impacto"],
    })
    return out.sort_values(["Impacto", "Pendiente"], ascending=False)


def _quality(df: pd.DataFrame, cols: dict[str, str | None]) -> tuple[pd.DataFrame, float]:
    active = df[~df["_cancelado"]].copy()
    n = max(len(active), 1)
    invoice_nonempty = active["_numero_factura"].astype(str).str.strip() != ""
    duplicate_invoice = invoice_nonempty & active["_numero_factura"].duplicated(keep=False)
    checks = [
        ("Sin importe", int((active["_monto"] <= 0).sum()), "Revisar filas con importe cero o no reconocido."),
        ("Sin fecha base", int(active["_fecha_base"].isna().sum()), "Impide análisis temporal y proyecciones."),
        ("Sin número de factura", int((~invoice_nonempty & (active["_monto"] > 0)).sum()), "Reduce trazabilidad administrativa."),
        ("Factura repetida", int(duplicate_invoice.sum()), "Validar duplicados antes de consolidar totales."),
        ("Pendiente sin vencimiento", int(((active["_pendiente"] > 0) & active["_vencimiento"].isna()).sum()), "Impide priorizar correctamente la cobranza."),
        ("Cobrado sin fecha de pago", int(((active["_cobrado"] > 0) & active["_fecha_pago"].isna()).sum()), "Limita el cálculo de velocidad de cobro."),
        ("Sin obra social", int((active["_obra_social"] == "SIN OBRA SOCIAL").sum()), "Reduce lectura por financiador."),
        ("Sin procedimiento", int((active["_procedimiento"] == "SIN PROCEDIMIENTO").sum()), "Reduce análisis de producción."),
    ]
    table = pd.DataFrame(checks, columns=["Control", "Registros", "Impacto"])
    # Índice interno de completitud: cada registro puede fallar en varios controles, se limita a 0..100.
    penalty = min(1.0, table["Registros"].sum() / (n * 4))
    score = max(0.0, 100 * (1 - penalty))
    return table, score


def _forecast(monthly: pd.DataFrame) -> dict[str, float | str | None]:
    if monthly.empty:
        return {"run_rate": 0.0, "year_close": 0.0, "trend": None, "base_month": 0.0, "conservative": 0.0, "expansive": 0.0, "last_month": "Sin dato"}
    today = pd.Timestamp.today().normalize()
    hist = monthly.copy().sort_values("mes")
    # Evita penalizar el run-rate con un mes actual todavía incompleto.
    if not hist.empty and hist.iloc[-1]["mes"].year == today.year and hist.iloc[-1]["mes"].month == today.month and today.day < 25:
        hist_complete = hist.iloc[:-1].copy()
    else:
        hist_complete = hist.copy()
    if hist_complete.empty:
        hist_complete = hist.copy()
    recent = hist_complete.tail(min(6, len(hist_complete)))
    weights = pd.Series(range(1, len(recent) + 1), index=recent.index, dtype=float)
    base_month = _weighted_average(recent["Facturado"], weights)
    run_rate = base_month * 12

    ytd = hist[hist["mes"].dt.year == today.year]
    ytd_total = float(ytd["Facturado"].sum())
    max_observed_month = int(ytd["mes"].dt.month.max()) if not ytd.empty else today.month
    remaining = max(0, 12 - max_observed_month)
    year_close = ytd_total + base_month * remaining if ytd_total else run_rate

    trend = None
    if len(hist_complete) >= 2:
        prev = float(hist_complete.iloc[-2]["Facturado"])
        last = float(hist_complete.iloc[-1]["Facturado"])
        trend = 100 * (last - prev) / prev if prev else None

    volatility = float(recent["Facturado"].std(ddof=0)) if len(recent) > 1 else 0.0
    conservative = max(0.0, (base_month - 0.5 * volatility) * 12)
    expansive = (base_month + 0.5 * volatility) * 12
    return {
        "run_rate": run_rate,
        "year_close": year_close,
        "trend": trend,
        "base_month": base_month,
        "conservative": conservative,
        "expansive": expansive,
        "last_month": hist_complete.iloc[-1]["mes"].strftime("%Y-%m") if not hist_complete.empty else "Sin dato",
    }


def _health_dimensions(metrics: dict[str, float], payer: pd.DataFrame, quality_score: float) -> dict[str, float]:
    collection = max(0.0, min(100.0, metrics["tasa"]))
    overdue = max(0.0, 100.0 - max(0.0, min(100.0, metrics["mora"])))
    if metrics["dso"] <= 0:
        speed = 55.0
    else:
        speed = max(0.0, min(100.0, 120 - metrics["dso"]))
    top_share = float(payer.iloc[0]["Participación %"]) if not payer.empty else 0.0
    diversification = max(0.0, min(100.0, 110 - top_share * 1.5))
    return {
        "Cobranza": collection,
        "Mora": overdue,
        "Velocidad": speed,
        "Diversificación": diversification,
        "Calidad": quality_score,
    }


def _executive_brief(metrics: dict[str, float], monthly: pd.DataFrame, payer: pd.DataFrame, proc: pd.DataFrame, quality_score: float) -> list[str]:
    msgs: list[str] = []
    if metrics["facturado"] <= 0:
        return ["No hay facturación positiva en la vista actual."]
    msgs.append(f"La cobranza representa {_fmt_pct(metrics['tasa'])} de lo facturado y quedan {_fmt_money(metrics['pendiente'])} pendientes.")
    if metrics["vencido"] > 0:
        msgs.append(f"La cartera vencida es {_fmt_money(metrics['vencido'])}, equivalente a {_fmt_pct(metrics['mora'])} del saldo pendiente.")
    if not payer.empty:
        top = payer.iloc[0]
        msgs.append(f"{top['Obra social']} concentra {_fmt_pct(top['Participación %'])} de la facturación de la vista; su saldo pendiente es {_fmt_money(top['Pendiente'])}.")
    if not proc.empty:
        top = proc.iloc[0]
        msgs.append(f"{top['Procedimiento']} es la práctica de mayor peso económico con {_fmt_money(top['Facturado'])} y {_fmt_pct(top['Cobranza %'])} de cobranza.")
    if len(monthly) >= 2:
        prev = float(monthly.iloc[-2]["Facturado"])
        last = float(monthly.iloc[-1]["Facturado"])
        if prev:
            delta = 100 * (last - prev) / prev
            msgs.append(f"El último mes observado varió {delta:+.1f}% frente al mes anterior.")
    if quality_score < 90:
        msgs.append(f"La calidad operativa de datos queda en {quality_score:.0f}/100; conviene corregir faltantes antes de usar el histórico para decisiones finas.")
    return msgs


def _action_plan(metrics: dict[str, float], queue: pd.DataFrame, payer: pd.DataFrame, quality_score: float, forecast: dict[str, Any]) -> pd.DataFrame:
    top_payer = payer.iloc[0]["Obra social"] if not payer.empty else "los principales financiadores"
    first_recovery = min(metrics["vencido"] * 0.30, metrics["pendiente"])
    actions = [
        {
            "Horizonte": "Próximos 7 días",
            "Prioridad": "Caja",
            "Acción": f"Conciliar los saldos vencidos de mayor impacto, empezando por {top_payer}.",
            "Resultado esperado": f"Objetivo inicial de recupero: {_fmt_money(first_recovery)}" if first_recovery > 0 else "Mantener cartera sin vencimientos relevantes",
        },
        {
            "Horizonte": "Próximos 30 días",
            "Prioridad": "Cobranza",
            "Acción": "Implementar seguimiento semanal por obra social, factura, tramo de mora y responsable.",
            "Resultado esperado": f"Elevar cobranza hacia {min(95.0, max(85.0, metrics['tasa'] + 5.0)):.1f}%",
        },
        {
            "Horizonte": "Próximos 60 días",
            "Prioridad": "Control",
            "Acción": "Cerrar faltantes de factura, vencimiento y fecha de pago; estandarizar el circuito de carga.",
            "Resultado esperado": f"Llevar calidad de datos de {quality_score:.0f}% a ≥ 98%",
        },
        {
            "Horizonte": "Próximos 90 días",
            "Prioridad": "Dirección",
            "Acción": "Revisar mix de financiadores, prácticas, médicos, crecimiento y velocidad de cobro con metas mensuales.",
            "Resultado esperado": f"Acercarse a un run-rate anual de {_fmt_money(float(forecast['run_rate']))}",
        },
    ]
    return pd.DataFrame(actions)


def _excel_export(metrics: dict[str, float], payer: pd.DataFrame, proc: pd.DataFrame, med: pd.DataFrame, aging: pd.DataFrame, queue: pd.DataFrame, quality: pd.DataFrame, monthly: pd.DataFrame) -> bytes | None:
    try:
        output = BytesIO()
        summary = pd.DataFrame([
            ["Facturado", metrics["facturado"]],
            ["Cobrado", metrics["cobrado"]],
            ["Pendiente", metrics["pendiente"]],
            ["Vencido", metrics["vencido"]],
            ["Tasa de cobro %", metrics["tasa"]],
            ["Mora %", metrics["mora"]],
            ["Días promedio de cobro", metrics["dso"]],
            ["Ticket promedio", metrics["ticket"]],
        ], columns=["Indicador", "Valor"])
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            summary.to_excel(writer, sheet_name="Resumen", index=False)
            monthly.to_excel(writer, sheet_name="Evolucion", index=False)
            payer.to_excel(writer, sheet_name="Obras sociales", index=False)
            proc.to_excel(writer, sheet_name="Procedimientos", index=False)
            med.to_excel(writer, sheet_name="Medicos", index=False)
            aging.to_excel(writer, sheet_name="Aging", index=False)
            queue.head(500).to_excel(writer, sheet_name="Prioridades", index=False)
            quality.to_excel(writer, sheet_name="Calidad", index=False)
        return output.getvalue()
    except Exception:
        return None


# -----------------------------------------------------------------------------
# PANTALLA ÚNICA DE FACTURACIÓN VM / VMR
# -----------------------------------------------------------------------------
# Una sola navegación para todo el módulo:
#
#   Análisis  → Resumen · Cobranza · Producción · Calidad
#   Gestión   → Registros · Cargar · Importar · Exportar
#
# Las secciones de análisis comparten un único panel de filtros y los mismos
# 4 indicadores. Las secciones de gestión usan el flujo de carga/edición ya
# existente (``gestion_renderer``), que siempre trabaja sobre la planilla
# completa, nunca sobre la vista filtrada.

_EMPRESAS = {
    "VMR": "Vitae Medicina Reproductiva",
    "VM": "Vitae Medical",
}

_SECCIONES_ANALISIS = [":material/insights: Resumen", ":material/payments: Cobranza", ":material/stethoscope: Producción", ":material/verified: Calidad"]
_SECCIONES_GESTION = [":material/table_view: Registros", ":material/add_circle: Cargar", ":material/upload_file: Importar", ":material/download: Exportar"]

# Sección de la pantalla → área de trabajo del flujo de gestión existente.
_WORKSPACE_GESTION = {
    ":material/table_view: Registros": "Editar tabla",
    ":material/add_circle: Cargar": "Cargar",
    ":material/upload_file: Importar": "Importar",
}

_PERIODOS = [
    "Mes actual",
    "Mes anterior",
    "Últimos 3 meses",
    "Últimos 12 meses",
    "Año actual",
    "Año anterior",
    "Todo el historial",
    "Personalizado",
]

_ESTADOS = ["Cobrado", "Pendiente", "Vencido", "Anulado", "Sin importe"]

_COLOR_ESTADO = {
    "Cobrado": VITAE_GREEN,
    "Pendiente": VITAE_AMBER,
    "Vencido": VITAE_RED,
    "Facturado": VITAE_PINK,
}

_MESES_CORTOS = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
_MESES_LARGOS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
                 "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def _fmt_money0(value: float | int | None) -> str:
    """Importe sin decimales, formato argentino: $ 1.234.567."""
    try:
        val = float(value or 0)
    except Exception:
        val = 0.0
    return "$ " + f"{val:,.0f}".replace(",", ".")


def _etiqueta_mes(fecha: pd.Timestamp) -> str:
    return f"{_MESES_CORTOS[fecha.month - 1]} {fecha.year}"


def _ajustar_estado_manual(data: pd.DataFrame) -> pd.DataFrame:
    """Si la planilla marca la fila como 'Vencido', se considera vencida aunque
    no tenga fecha de vencimiento cargada (antes quedaba como 'Pendiente')."""
    marcado = data["_estado_norm"].astype(str).str.contains("vencid", na=False)
    aplicar = marcado & (data["_pendiente"] > 0) & ~data["_cancelado"]
    if aplicar.any():
        data.loc[aplicar, "_vencido"] = True
        data.loc[aplicar, "_estado_ejecutivo"] = "Vencido"
    return data


def _css_pantalla() -> None:
    st.markdown(
        """
        <style>
        .fx-head{display:flex;justify-content:space-between;align-items:flex-end;gap:1rem;margin:.1rem 0 .6rem 0}
        .fx-head-kicker{font-size:.74rem;letter-spacing:.14em;text-transform:uppercase;font-weight:800;color:#A54470}
        .fx-head-title{font-size:1.85rem;font-weight:850;letter-spacing:-.03em;color:#4A1830;line-height:1.15}
        .fx-head-sub{font-size:.9rem;color:#7A6370;margin-top:.15rem}
        .fx-card{min-height:0;background:#FFFFFF !important;border:1px solid #E8E3E6 !important;border-radius:12px !important;box-shadow:none !important;padding:14px 16px !important}
        .fx-card .fx-value{font-size:1.45rem;font-weight:650 !important;color:#2A0F1C !important}
        .fx-card .fx-label{font-weight:500 !important}
        .fx-dot{display:inline-block;width:.62rem;height:.62rem;border-radius:50%;margin-right:.4rem;vertical-align:middle}
        .fx-subtitle{font-size:1.08rem;font-weight:800;color:#4A1830;margin:1.1rem 0 .35rem 0}
        .fx-note{font-size:.82rem;color:#8A707C;margin:-.15rem 0 .5rem 0}
        .fx-brief li{margin-bottom:.28rem;color:#4A3440}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _encabezado(module_name: str, company: str, key: str) -> None:
    izq, der = st.columns([5, 1], vertical_alignment="bottom")
    with izq:
        st.markdown(
            f"""
            <div class="fx-head"><div>
              <div class="fx-head-kicker">Facturación · {company}</div>
              <div class="fx-head-title">{module_name}</div>
              <div class="fx-head-sub">{_EMPRESAS.get(company, company)} · datos de Google Sheets</div>
            </div></div>
            """,
            unsafe_allow_html=True,
        )
    with der:
        if st.button("Actualizar", icon=":material/refresh:", key=f"{key}_refresh", use_container_width=True, help="Volver a leer la planilla"):
            st.cache_data.clear()
            st.rerun()


def _rango_periodo(periodo: str, hoy: pd.Timestamp) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    if re.fullmatch(r"\d{4}-\d{2}", str(periodo)):
        mes = pd.Period(periodo, freq="M")
        return mes.start_time.normalize(), mes.end_time.normalize()
    inicio_mes = hoy.replace(day=1)
    if periodo == "Mes actual":
        return inicio_mes, inicio_mes + pd.offsets.MonthEnd(0)
    if periodo == "Mes anterior":
        fin = inicio_mes - pd.Timedelta(days=1)
        return fin.replace(day=1), fin
    if periodo == "Últimos 3 meses":
        return (inicio_mes - pd.DateOffset(months=2)), inicio_mes + pd.offsets.MonthEnd(0)
    if periodo == "Últimos 12 meses":
        return (inicio_mes - pd.DateOffset(months=11)), inicio_mes + pd.offsets.MonthEnd(0)
    if periodo == "Año actual":
        return pd.Timestamp(year=hoy.year, month=1, day=1), pd.Timestamp(year=hoy.year, month=12, day=31)
    if periodo == "Año anterior":
        return pd.Timestamp(year=hoy.year - 1, month=1, day=1), pd.Timestamp(year=hoy.year - 1, month=12, day=31)
    return None, None


def _opciones(serie: pd.Series) -> list[str]:
    valores = serie.dropna().astype(str).str.strip()
    return sorted(set(valores[valores != ""]), key=str.casefold)


def _limpiar_filtros(key: str) -> None:
    for sufijo in ("periodo", "os", "estado", "med", "proc", "buscar", "desde", "hasta"):
        st.session_state.pop(f"{key}_f_{sufijo}", None)


def _panel_filtros(data: pd.DataFrame, key: str) -> tuple[pd.DataFrame, str]:
    """Filtros compartidos por todas las secciones de análisis."""
    hoy = pd.Timestamp.today().normalize()
    movil = es_movil()
    activos_previos = sum(
        bool(st.session_state.get(f"{key}_f_{k}")) for k in ("os", "estado", "med", "proc", "buscar")
    )
    titulo = "Filtros" + (f" · {activos_previos} activos" if activos_previos else "")
    caja = st.expander(titulo, icon=":material/filter_list:", expanded=False) if movil else st.container(border=True)
    with caja:
        c1, c2, c3, c4 = st.columns([1.0, 1.4, 1.0, 1.4])
        # Períodos rápidos + cada mes que tiene registros (del más reciente al más viejo).
        meses_datos = [str(m) for m in sorted(data["_fecha_base"].dropna().dt.to_period("M").unique(), reverse=True)]
        opciones_periodo = _PERIODOS[:-1] + meses_datos + _PERIODOS[-1:]
        periodo = c1.selectbox(
            "Período", opciones_periodo, index=opciones_periodo.index("Año actual"), key=f"{key}_f_periodo",
            format_func=lambda o: o if o in _PERIODOS else _MESES_LARGOS[pd.Period(o, freq="M").month - 1] + f" {pd.Period(o, freq='M').year}",
        )
        obras = c2.multiselect("Obra social", _opciones(data["_obra_social"]), placeholder="Todas", key=f"{key}_f_os")
        estados = c3.multiselect("Estado", _ESTADOS, placeholder="Todos", key=f"{key}_f_estado")
        medicos = c4.multiselect("Médico", _opciones(data["_medico"]), placeholder="Todos", key=f"{key}_f_med")

        desde, hasta = _rango_periodo(periodo, hoy)
        if periodo == "Personalizado":
            fechas = data["_fecha_base"].dropna()
            minimo = fechas.min().date() if not fechas.empty else hoy.date()
            r1, r2, _ = st.columns([1, 1, 2])
            d = r1.date_input("Desde", value=minimo, format="DD/MM/YYYY", key=f"{key}_f_desde")
            h = r2.date_input("Hasta", value=hoy.date(), format="DD/MM/YYYY", key=f"{key}_f_hasta")
            desde, hasta = pd.Timestamp(d), pd.Timestamp(h)
            if desde > hasta:
                st.error("La fecha «Desde» es posterior a «Hasta».")
                desde, hasta = hasta, desde

        s1, s2, s3 = st.columns([2.4, 1.4, 0.8], vertical_alignment="bottom")
        buscar = s1.text_input("Buscar", placeholder="Paciente, N° de factura, obra social, práctica…", key=f"{key}_f_buscar")
        procs = s2.multiselect("Procedimiento", _opciones(data["_procedimiento"]), placeholder="Todos", key=f"{key}_f_proc")
        s3.button("Limpiar", key=f"{key}_f_limpiar", on_click=_limpiar_filtros, args=(key,), use_container_width=True)

    out = data
    if desde is not None and hasta is not None:
        out = out[out["_fecha_base"].between(desde, hasta + pd.Timedelta(hours=23, minutes=59, seconds=59))]
    if obras:
        out = out[out["_obra_social"].isin(obras)]
    if estados:
        out = out[out["_estado_ejecutivo"].isin(estados)]
    if medicos:
        out = out[out["_medico"].isin(medicos)]
    if procs:
        out = out[out["_procedimiento"].isin(procs)]
    if buscar.strip():
        aguja = re.escape(_norm(buscar))
        pajar = (
            out["_paciente"].astype(str) + " " + out["_numero_factura"].astype(str) + " "
            + out["_obra_social"].astype(str) + " " + out["_procedimiento"].astype(str) + " "
            + out["_medico"].astype(str) + " " + out["_estado_original"].astype(str)
        ).map(_norm)
        out = out[pajar.str.contains(aguja, na=False)]

    if desde is not None and hasta is not None:
        texto_periodo = f"{desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}"
    else:
        texto_periodo = "todo el historial"
    activos = [x for x in (obras + estados + medicos + procs) if x]
    if buscar.strip():
        activos.append(f"«{buscar.strip()}»")
    detalle = f"{len(out):,} registros · {texto_periodo}".replace(",", ".")
    if activos:
        detalle += " · " + ", ".join(activos[:4]) + (f" y {len(activos) - 4} más" if len(activos) > 4 else "")
    return out, detalle


def _tarjetas(metrics: dict[str, float]) -> None:
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        _kpi(f'<span class="fx-dot" style="background:{VITAE_PINK}"></span>Facturado', _fmt_money0(metrics["facturado"]),
             f"{metrics['registros']:,} registros".replace(",", "."))
    with k2:
        _kpi(f'<span class="fx-dot" style="background:{VITAE_GREEN}"></span>Cobrado', _fmt_money0(metrics["cobrado"]),
             f"{_fmt_pct(metrics['tasa'])} de lo facturado")
    with k3:
        _kpi(f'<span class="fx-dot" style="background:{VITAE_AMBER}"></span>Pendiente', _fmt_money0(metrics["pendiente"]),
             f"{_fmt_pct(100 - metrics['tasa'] if metrics['facturado'] else 0)} de lo facturado")
    with k4:
        _kpi(f'<span class="fx-dot" style="background:{VITAE_RED}"></span>Vencido', _fmt_money0(metrics["vencido"]),
             f"{_fmt_pct(metrics['mora'])} de lo pendiente")


def _subtitulo(texto: str, nota: str = "") -> None:
    st.markdown(f'<div class="fx-subtitle">{texto}</div>', unsafe_allow_html=True)
    if nota:
        st.markdown(f'<div class="fx-note">{nota}</div>', unsafe_allow_html=True)


def _tabla_montos(df: pd.DataFrame, columnas_dinero: Iterable[str], columnas_pct: Iterable[str] = (), **kwargs: Any) -> None:
    vista = df.copy()
    for c in columnas_dinero:
        if c in vista.columns:
            vista[c] = vista[c].map(_fmt_money0)
    for c in columnas_pct:
        if c in vista.columns:
            vista[c] = vista[c].map(_fmt_pct)
    st.dataframe(vista, use_container_width=True, hide_index=True, **kwargs)


def _grafico(fig: go.Figure, height: int = 360) -> None:
    fig = _clean_plot(fig, height)
    fig.update_layout(title=None, margin=dict(l=10, r=10, t=10, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    fig.update_layout(separators=",.")  # 1.234.567,89 (formato argentino)
    fig.update_yaxes(tickformat=",.0f")
    st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False, "displayModeBar": False})


def _seccion_resumen(f: pd.DataFrame, metrics: dict[str, float], module_name: str, key: str) -> None:
    activos = f[~f["_cancelado"]]
    payer = _payer_table(f)
    proc = _group_table(f, "_procedimiento", "Procedimiento")

    brief = _executive_brief(metrics, _monthly(f), payer, proc, 100.0)
    with st.container(border=True):
        st.markdown("**En pocas palabras**")
        st.markdown('<ul class="fx-brief">' + "".join(f"<li>{m}</li>" for m in brief) + "</ul>", unsafe_allow_html=True)

    _subtitulo("Evolución mensual", "Cada barra es lo facturado en el mes, dividido en lo que ya se cobró, lo pendiente y lo vencido.")
    mensual = activos[activos["_fecha_base"].notna()].copy()
    if mensual.empty:
        st.info("No hay registros con mes cargado en esta vista.")
    else:
        mensual["mes"] = mensual["_fecha_base"].dt.to_period("M").dt.to_timestamp()
        mensual["Vencido"] = mensual["_pendiente"].where(mensual["_vencido"], 0.0)
        mensual["Pendiente"] = mensual["_pendiente"] - mensual["Vencido"]
        g = mensual.groupby("mes", as_index=False).agg(Cobrado=("_cobrado", "sum"), Pendiente=("Pendiente", "sum"), Vencido=("Vencido", "sum"))
        g["Mes"] = g["mes"].map(_etiqueta_mes)
        largo = g.melt(id_vars=["mes", "Mes"], value_vars=["Cobrado", "Pendiente", "Vencido"], var_name="Estado", value_name="Importe")
        fig = px.bar(largo, x="Mes", y="Importe", color="Estado", color_discrete_map=_COLOR_ESTADO,
                     category_orders={"Mes": list(g["Mes"]), "Estado": ["Cobrado", "Pendiente", "Vencido"]})
        fig.update_layout(barmode="stack", xaxis_title=None, yaxis_title=None)
        _grafico(fig, 360)

    c1, c2 = st.columns([1.35, 1])
    with c1:
        _subtitulo("Principales obras sociales", "Facturado en la vista, las 8 de mayor peso.")
        if payer.empty:
            st.info("Sin datos de obra social.")
        else:
            top = payer.head(8).sort_values("Facturado")
            fig = px.bar(top, x="Facturado", y="Obra social", orientation="h", color_discrete_sequence=[VITAE_PINK],
                         hover_data={"Facturado": ":,.0f"})
            fig.update_layout(xaxis_title=None, yaxis_title=None)
            fig.update_xaxes(tickformat=",.0f")
            _grafico(fig, 330)
    with c2:
        _subtitulo("Estado de la cartera", "Cómo se reparte lo facturado.")
        comp = pd.DataFrame({
            "Estado": ["Cobrado", "Pendiente", "Vencido"],
            "Importe": [metrics["cobrado"], max(0.0, metrics["pendiente"] - metrics["vencido"]), metrics["vencido"]],
        })
        if comp["Importe"].sum() <= 0:
            st.info("Sin importes en la vista.")
        else:
            fig = px.pie(comp, names="Estado", values="Importe", hole=.62, color="Estado", color_discrete_map=_COLOR_ESTADO)
            fig.update_traces(textinfo="percent", sort=False)
            _grafico(fig, 330)

    with st.expander("Preguntarle a la IA sobre esta vista", icon=":material/auto_awesome:", expanded=False):
        st.caption("La IA responde usando solo los registros que estás viendo con los filtros actuales.")
        pregunta = st.text_input("Pregunta", placeholder="Ej.: ¿Qué obra social conviene priorizar esta semana y por qué?", key=f"{key}_ia_q")
        if st.button("Preguntar", key=f"{key}_ia_btn"):
            if not pregunta.strip():
                st.warning("Escribí una pregunta.")
            else:
                try:
                    from vitae.ia.copiloto import preguntar_ia
                    ia_df = f[["_fecha_base", "_paciente", "_obra_social", "_procedimiento", "_medico", "_numero_factura",
                               "_vencimiento", "_fecha_pago", "_monto", "_cobrado", "_pendiente", "_estado_ejecutivo"]].copy()
                    ia_df.columns = ["fecha", "paciente", "obra_social", "procedimiento", "medico", "factura",
                                     "vencimiento", "fecha_pago", "facturado", "cobrado", "pendiente", "estado"]
                    with st.spinner("Analizando…"):
                        st.markdown(preguntar_ia(module_name, ia_df, pregunta))
                except Exception as exc:
                    st.error(f"La IA no pudo responder: {exc}")


def _seccion_cobranza(f: pd.DataFrame, metrics: dict[str, float], key: str) -> None:
    pendientes = f[(~f["_cancelado"]) & (f["_pendiente"] > 0)]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Tasa de cobro", _fmt_pct(metrics["tasa"]), help="Cobrado ÷ facturado")
    m2.metric("Mora", _fmt_pct(metrics["mora"]), help="Vencido ÷ pendiente")
    m3.metric("Días promedio de cobro", f"{metrics['dso']:.0f}" if metrics["dso"] else "Sin dato",
              help="Desde la fecha de factura hasta la fecha de pago, ponderado por importe")
    m4.metric("Facturas por cobrar", f"{len(pendientes):,}".replace(",", "."))

    c1, c2 = st.columns([1, 1.25])
    with c1:
        _subtitulo("Antigüedad de lo pendiente", "Cuánto hace que venció cada saldo.")
        aging = _aging(f)
        if aging.empty:
            st.success("No hay saldos pendientes en esta vista.")
        else:
            colores = {"A vencer": VITAE_GREEN, "1-30 días": VITAE_AMBER, "31-60 días": "#C9703F",
                       "61-90 días": VITAE_RED, "+90 días": "#7E2F3F", "Sin vencimiento": "#B9A9B1"}
            aging = aging.assign(Tramo=aging["Tramo"].astype(str))
            fig = px.bar(aging, x="Tramo", y="Pendiente", color="Tramo", color_discrete_map=colores,
                         hover_data={"Registros": True, "Pendiente": ":,.0f"})
            fig.update_layout(showlegend=False, xaxis_title=None, yaxis_title=None)
            _grafico(fig, 320)
    with c2:
        _subtitulo("Pendiente por obra social", "Lo que falta cobrar a cada financiador, separado en vencido y no vencido.")
        payer = _payer_table(f)
        if payer.empty or payer["Pendiente"].sum() <= 0:
            st.success("Sin saldos pendientes.")
        else:
            p = payer[payer["Pendiente"] > 0].sort_values("Pendiente", ascending=False).head(10).copy()
            p["No vencido"] = p["Pendiente"] - p["Vencido"]
            largo = p.melt(id_vars=["Obra social"], value_vars=["No vencido", "Vencido"], var_name="Estado", value_name="Importe")
            fig = px.bar(largo, x="Importe", y="Obra social", color="Estado", orientation="h",
                         color_discrete_map={"No vencido": VITAE_AMBER, "Vencido": VITAE_RED},
                         category_orders={"Obra social": list(p["Obra social"])})
            fig.update_layout(barmode="stack", xaxis_title=None, yaxis_title=None)
            fig.update_xaxes(tickformat=",.0f")
            _grafico(fig, 320)

    _subtitulo("A quién cobrar primero", "Ordenado por importe y días de atraso. Prioridad crítica: más de 60 días vencido o saldo muy alto.")
    queue = _priority_queue(f)
    if queue.empty:
        st.success("No hay saldos pendientes en esta vista.")
    else:
        q = queue.drop(columns=["Impacto", "Score"]).head(50).copy()
        q["Vencimiento"] = pd.to_datetime(q["Vencimiento"], errors="coerce").dt.strftime("%d/%m/%Y").fillna("Sin fecha")
        if es_movil():
            q = q[["Prioridad", "Obra social", "Paciente", "Pendiente", "Días vencido"]]
        _tabla_montos(q, ["Pendiente"], height=min(42 + 35 * len(q), 460))
        if len(queue) > 50:
            st.caption(f"Se muestran las 50 de mayor prioridad de {len(queue)}. La lista completa está en Exportar, en el informe Excel.")

    _subtitulo("Resumen por obra social")
    if not payer.empty:
        columnas = ["Obra social", "Facturado", "Cobrado", "Pendiente", "Vencido", "Cobranza %", "Días cobro", "Registros"]
        if es_movil():
            columnas = ["Obra social", "Pendiente", "Vencido", "Cobranza %"]
        tabla = payer[columnas].copy()
        if "Días cobro" in tabla.columns:
            tabla["Días cobro"] = tabla["Días cobro"].map(lambda x: f"{x:.0f}" if x else "—")
        _tabla_montos(tabla, ["Facturado", "Cobrado", "Pendiente", "Vencido"], ["Cobranza %"], height=min(42 + 35 * len(tabla), 420))


def _seccion_produccion(f: pd.DataFrame) -> None:
    med = _group_table(f, "_medico", "Médico")
    proc = _group_table(f, "_procedimiento", "Procedimiento")
    if med.empty and proc.empty:
        st.info("No hay producción en esta vista.")
        return

    c1, c2 = st.columns(2)
    for col, tabla, etiqueta in ((c1, med, "Médico"), (c2, proc, "Procedimiento")):
        with col:
            _subtitulo(f"Por {etiqueta.lower()}", "Facturado en la vista, los 10 de mayor peso.")
            if tabla.empty:
                st.info("Sin datos.")
                continue
            top = tabla.head(10).sort_values("Facturado")
            fig = px.bar(top, x="Facturado", y=etiqueta, orientation="h", color_discrete_sequence=[VITAE_PINK],
                         hover_data={"Registros": True, "Facturado": ":,.0f"})
            fig.update_layout(xaxis_title=None, yaxis_title=None)
            fig.update_xaxes(tickformat=",.0f")
            _grafico(fig, 360)
            cols_tabla = [etiqueta, "Registros", "Facturado", "Cobrado", "Pendiente", "Participación %"]
            if es_movil():
                cols_tabla = [etiqueta, "Facturado", "Participación %"]
            _tabla_montos(tabla[cols_tabla],
                          ["Facturado", "Cobrado", "Pendiente"], ["Participación %"], height=min(42 + 35 * len(tabla), 380))

    if not proc.empty and proc["Facturado"].sum() > 0:
        acumulado = proc["Facturado"].cumsum() / proc["Facturado"].sum() * 100
        n80 = int((acumulado < 80).sum() + 1)
        st.info(f"**{n80}** de {len(proc)} prácticas explican el 80% de lo facturado en esta vista.")

    activos = f[~f["_cancelado"]]
    if not activos.empty:
        with st.expander("Cruce médico × procedimiento (cantidad de registros)", expanded=False):
            top_proc = proc.head(12)["Procedimiento"].tolist() if not proc.empty else []
            base = activos[activos["_procedimiento"].isin(top_proc)]
            if base.empty:
                st.info("Sin datos para cruzar.")
            else:
                matriz = pd.crosstab(base["_medico"], base["_procedimiento"])
                matriz = matriz.loc[matriz.sum(axis=1).sort_values(ascending=False).index, [p for p in top_proc if p in matriz.columns]]
                matriz.index.name = "Médico"
                st.dataframe(matriz, use_container_width=True)
                st.caption("Se muestran las 12 prácticas de mayor facturación.")


_DESCRIPCION_CONTROLES = {
    "Sin importe": "Filas con importe vacío, cero o que no se pudo leer.",
    "Sin fecha base": "Filas sin «mes»: no entran en ningún período ni gráfico mensual.",
    "Sin número de factura": "Filas con importe pero sin N° de factura.",
    "Factura repetida": "El mismo N° de factura aparece en más de una fila.",
    "Pendiente sin vencimiento": "Saldos pendientes sin fecha de vencimiento: no se puede saber si están vencidos.",
    "Cobrado sin fecha de pago": "Marcados como cobrados pero sin fecha de pago.",
    "Sin obra social": "Filas sin obra social.",
    "Sin procedimiento": "Filas sin procedimiento.",
}


def _mascaras_calidad(f: pd.DataFrame) -> dict[str, pd.Series]:
    a = f[~f["_cancelado"]]
    con_factura = a["_numero_factura"].astype(str).str.strip() != ""
    return {
        "Sin importe": a["_monto"] <= 0,
        "Sin fecha base": a["_fecha_base"].isna(),
        "Sin número de factura": ~con_factura & (a["_monto"] > 0),
        "Factura repetida": con_factura & a["_numero_factura"].duplicated(keep=False),
        "Pendiente sin vencimiento": (a["_pendiente"] > 0) & a["_vencimiento"].isna(),
        "Cobrado sin fecha de pago": (a["_cobrado"] > 0) & a["_fecha_pago"].isna(),
        "Sin obra social": a["_obra_social"] == "SIN OBRA SOCIAL",
        "Sin procedimiento": a["_procedimiento"] == "SIN PROCEDIMIENTO",
    }


def _detalle_registros(df: pd.DataFrame) -> pd.DataFrame:
    def fecha(s: pd.Series) -> pd.Series:
        return pd.to_datetime(s, errors="coerce").dt.strftime("%d/%m/%Y").fillna("")
    d = df.sort_values("_fecha_base", ascending=False)
    return pd.DataFrame({
        "Mes": fecha(d["_fecha_base"]),
        "Paciente / afiliado": d["_paciente"],
        "Obra social": d["_obra_social"],
        "Procedimiento": d["_procedimiento"],
        "Médico": d["_medico"],
        "N° factura": d["_numero_factura"],
        "Vencimiento": fecha(d["_vencimiento"]),
        "Fecha pago": fecha(d["_fecha_pago"]),
        "Facturado": d["_monto"],
        "Cobrado": d["_cobrado"],
        "Pendiente": d["_pendiente"],
        "Estado": d["_estado_ejecutivo"],
    })


def _seccion_calidad(f: pd.DataFrame, cols: dict[str, str | None], key: str) -> None:
    tabla, puntaje = _quality(f, cols)
    mascaras = _mascaras_calidad(f)
    con_problemas = [c for c in tabla["Control"] if int(mascaras[c].sum()) > 0]
    filas_afectadas = int(pd.concat(list(mascaras.values()), axis=1).any(axis=1).sum()) if mascaras else 0

    m1, m2, m3 = st.columns(3)
    m1.metric("Índice de calidad", f"{puntaje:.0f}/100")
    m2.metric("Controles con problemas", f"{len(con_problemas)} de {len(tabla)}")
    m3.metric("Filas a revisar", f"{filas_afectadas:,}".replace(",", "."))

    if not con_problemas:
        st.success("No se encontraron problemas en los registros de esta vista.")
    else:
        _subtitulo("Qué revisar", "Corregí estos datos en Registros. Los totales se muestran igual, pero algunos indicadores (vencido, días de cobro) dependen de estas fechas.")
        resumen = pd.DataFrame({
            "Control": con_problemas,
            "Filas": [int(mascaras[c].sum()) for c in con_problemas],
            "Qué significa": [_DESCRIPCION_CONTROLES.get(c, "") for c in con_problemas],
        }).sort_values("Filas", ascending=False)
        st.dataframe(resumen, use_container_width=True, hide_index=True)

        elegido = st.selectbox("Ver las filas de:", resumen["Control"].tolist(), key=f"{key}_cal_ver")
        activos = f[~f["_cancelado"]]
        filas = activos[mascaras[elegido]]
        detalle_filas = _detalle_registros(filas)
        if es_movil():
            detalle_filas = detalle_filas[["Mes", "Paciente / afiliado", "Obra social", "N° factura", "Facturado"]]
        _tabla_montos(detalle_filas, ["Facturado", "Cobrado", "Pendiente"], height=min(42 + 35 * len(filas), 420))

    with st.expander("Columnas de la planilla que se están leyendo", expanded=False):
        mapa = pd.DataFrame({
            "Dato": ["Mes", "Paciente / afiliado", "Obra social", "Procedimiento", "Médico", "Fecha factura",
                     "N° factura", "Vencimiento", "Fecha pago", "Importe", "Estado"],
            "Columna en la planilla": [cols.get(k) or "— no encontrada —" for k in
                                       ["mes", "paciente", "obra_social", "procedimiento", "medico", "fecha_factura",
                                        "numero_factura", "vencimiento", "fecha_pago", "monto", "estado"]],
        })
        st.dataframe(mapa, use_container_width=True, hide_index=True)


def _registros_del_periodo(f: pd.DataFrame, detalle: str) -> None:
    """Los registros que corresponden al período y filtros elegidos."""
    st.divider()
    _subtitulo("Registros del período", detalle)
    tabla = _detalle_registros(f)
    if es_movil():
        tabla = tabla[["Mes", "Paciente / afiliado", "Obra social", "Facturado", "Estado"]]
    _tabla_montos(tabla, ["Facturado", "Cobrado", "Pendiente"], height=min(42 + 35 * len(tabla), 460))


def _seccion_exportar(
    f: pd.DataFrame,
    metrics: dict[str, float],
    module_name: str,
    cfg: Dict[str, Any],
    table: str,
    key: str,
    gestion_renderer: Callable[..., None] | None,
) -> None:
    _subtitulo("Vista filtrada", "Lo que estás viendo con los filtros de arriba.")
    detalle = _detalle_registros(f)
    e1, e2 = st.columns(2)
    e1.download_button(
        "Registros filtrados (CSV)",
        icon=":material/description:",
        data=detalle.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{table}_vista.csv",
        mime="text/csv",
        use_container_width=True,
        key=f"{key}_csv",
    )
    excel = _excel_export(metrics, _payer_table(f), _group_table(f, "_procedimiento", "Procedimiento"),
                          _group_table(f, "_medico", "Médico"), _aging(f), _priority_queue(f), _quality(f, {})[0], _monthly(f))
    if excel:
        e2.download_button(
            "Informe para dirección (Excel)",
            icon=":material/table_chart:",
            data=excel,
            file_name=f"{table}_informe.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key=f"{key}_xlsx",
            help="Resumen, evolución mensual, obras sociales, prácticas, médicos, antigüedad, prioridades de cobro y calidad.",
        )
    else:
        e2.info("El informe Excel no está disponible; el CSV sí.")
    with st.expander(f"Ver los {len(detalle):,} registros".replace(",", "."), expanded=False):
        _tabla_montos(detalle, ["Facturado", "Cobrado", "Pendiente"], height=460)

    if gestion_renderer is not None:
        st.divider()
        _subtitulo("Planilla completa", "Todos los registros tal como están en Google Sheets, sin filtros.")
        gestion_renderer(module_name, cfg, workspace="Exportar", embebido=True)
        with st.expander("Elegir y renombrar columnas de la planilla completa", icon=":material/tune:", expanded=False):
            gestion_renderer(module_name, cfg, workspace="Columnas", embebido=True)


def render_facturacion_ultra_pro(
    module_name: str,
    cfg: Dict[str, Any],
    legacy_renderer: Callable[..., None] | None = None,
    header_renderer: Callable[[], None] | None = None,
) -> None:
    """
    Pantalla única de Facturación VM / VMR.

    - ``legacy_renderer``: flujo existente de gestión (``render_facturacion_pro``),
      llamado con ``workspace=`` y ``embebido=True`` para Registros, Cargar,
      Importar y Exportar.
    - ``header_renderer``: encabezado común de la app (login/permisos, barra de
      sesión y copiloto).
    """
    table = str(cfg.get("table", "") or "")
    if not _is_facturacion(module_name, table):
        if legacy_renderer is not None:
            legacy_renderer(module_name, cfg)
        return

    st.session_state["vitae_current_module"] = str(module_name)
    if header_renderer is not None:
        header_renderer()

    company = "VMR" if "vmr" in _norm(f"{module_name} {table}") else "VM"
    key = f"fx_{_norm(table or module_name)}"
    _css()
    _css_pantalla()
    if encabezado_modulo is not None:
        encabezado_modulo(module_name, cfg)
    else:
        _encabezado(module_name, company, key)

    secciones = _SECCIONES_ANALISIS + (_SECCIONES_GESTION if legacy_renderer is not None else [":material/download: Exportar"])
    if es_movil():
        # En el celular 8 botones no entran en una fila: desplegable compacto.
        seccion = st.selectbox(
            "Sección", secciones, key=f"{key}_seccion_movil", label_visibility="collapsed",
            format_func=sin_icono,
        )
    else:
        seleccion = st.segmented_control(
            "Sección", secciones, default=secciones[0], key=f"{key}_seccion", label_visibility="collapsed"
        )
        seccion = seleccion or secciones[0]

    # ---- Gestión: flujo existente, siempre sobre la planilla completa ----
    if seccion in _WORKSPACE_GESTION and legacy_renderer is not None:
        legacy_renderer(module_name, cfg, workspace=_WORKSPACE_GESTION[seccion], embebido=True)
        return

    # ---- Análisis (y Exportar): lectura + filtros compartidos ----
    if get_df is None:
        st.error("No se pudo conectar con la base de datos.")
        return
    try:
        df_raw = get_df(table)
    except Exception as exc:
        st.error(f"No se pudo leer la planilla «{table}» de Google Sheets: {exc}")
        return
    if df_raw is None or df_raw.empty:
        st.info("Todavía no hay registros. Podés cargarlos desde Cargar o Importar.")
        return

    data, cols = _prepare_facturacion(df_raw)
    data = _ajustar_estado_manual(data)
    filtrado, detalle = _panel_filtros(data, key)

    if seccion == ":material/download: Exportar":
        _seccion_exportar(filtrado, _metrics(filtrado), module_name, cfg, table, key, legacy_renderer)
        return

    if filtrado.empty:
        st.info("Ningún registro coincide con los filtros. Probá ampliar el período o tocar «Limpiar».")
        return

    metrics = _metrics(filtrado)
    _tarjetas(metrics)
    st.caption(detalle)

    if seccion == ":material/insights: Resumen":
        _seccion_resumen(filtrado, metrics, module_name, key)
        _registros_del_periodo(filtrado, detalle)
    elif seccion == ":material/payments: Cobranza":
        _seccion_cobranza(filtrado, metrics, key)
    elif seccion == ":material/stethoscope: Producción":
        _seccion_produccion(filtrado)
    elif seccion == ":material/verified: Calidad":
        _seccion_calidad(filtrado, cols, key)
