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
    from database import get_df
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
    formatted = f"{val:,.2f}"
    formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"$ {formatted}"


def _fmt_pct(value: float | int | None) -> str:
    try:
        return f"{float(value):.1f}%"
    except Exception:
        return "0.0%"


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
                radial-gradient(circle at 8% 3%, rgba(224,171,196,.24) 0, rgba(224,171,196,0) 29%),
                radial-gradient(circle at 93% 10%, rgba(193,105,145,.14) 0, rgba(193,105,145,0) 26%),
                linear-gradient(180deg,#EED5E1 0%,#F5E5ED 48%,#FAF2F6 100%) !important;
            color:#3F2430;
        }
        [data-testid="stHeader"]{background:rgba(237,211,222,.96) !important;border-bottom:1px solid rgba(177,86,126,.09)}
        [data-testid="stMainBlockContainer"], .block-container{background:transparent !important}

        /* Hero integrado: ya no es bordó oscuro; usa el mismo rosa suave de VITAE */
        .fx-hero{
            background:linear-gradient(135deg,rgba(255,255,255,.70) 0%,rgba(248,230,239,.76) 56%,rgba(231,193,211,.62) 100%);
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
# Render principal
# -----------------------------------------------------------------------------
def render_facturacion_ultra_pro(
    module_name: str,
    cfg: Dict[str, Any],
    legacy_renderer: Callable[[str, Dict[str, Any]], None] | None = None,
) -> None:
    """
    Centro inteligente para Facturación VM / VMR.

    - No escribe en Google Sheets.
    - Lee la misma tabla definida en MODULES.
    - Mantiene disponible la vista/gestión anterior mediante ``legacy_renderer``.
    """
    table = str(cfg.get("table", "") or "")
    if not _is_facturacion(module_name, table):
        if legacy_renderer is not None:
            legacy_renderer(module_name, cfg)
        return

    company = "VMR" if "vmr" in _norm(f"{module_name} {table}") else "VM"
    key = f"fx_ultra_{_norm(table or module_name)}"
    _css()

    mode_options = ["🚀 Centro inteligente", "🧾 Gestión y carga actual"] if legacy_renderer else ["🚀 Centro inteligente"]
    mode = st.radio("Vista de facturación", mode_options, horizontal=True, key=f"{key}_mode", label_visibility="collapsed")
    if mode == "🧾 Gestión y carga actual" and legacy_renderer is not None:
        legacy_renderer(module_name, cfg)
        return

    _hero(company)

    if get_df is None:
        st.error("No se pudo importar database.get_df.")
        return
    try:
        df_raw = get_df(table)
    except Exception as exc:
        st.error(f"No se pudo leer Google Sheets para {table}: {exc}")
        return
    if df_raw is None or df_raw.empty:
        st.warning("No hay registros cargados en esta planilla.")
        return

    data, cols = _prepare_facturacion(df_raw)
    filtered = _filter_panel(data, key)
    if filtered.empty:
        st.info("No hay registros que coincidan con los filtros seleccionados.")
        return

    metrics = _metrics(filtered)
    monthly = _monthly(filtered)
    payer = _payer_table(filtered)
    proc = _group_table(filtered, "_procedimiento", "Procedimiento")
    med = _group_table(filtered, "_medico", "Médico")
    aging = _aging(filtered)
    queue = _priority_queue(filtered)
    quality_table, quality_score = _quality(filtered, cols)
    forecast = _forecast(monthly)
    health = _health_dimensions(metrics, payer, quality_score)

    # Header KPIs
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        _kpi("💰 Facturado activo", _fmt_money(metrics["facturado"]), f"{metrics['registros']} registros")
    with k2:
        _kpi("✅ Cobrado", _fmt_money(metrics["cobrado"]), f"{_fmt_pct(metrics['tasa'])} del facturado")
    with k3:
        _kpi("⌛ Pendiente", _fmt_money(metrics["pendiente"]), f"{_fmt_pct(100-metrics['tasa'])} aún no cobrado")
    with k4:
        _kpi("🚨 Vencido", _fmt_money(metrics["vencido"]), f"{_fmt_pct(metrics['mora'])} de la cartera pendiente")

    k5, k6, k7, k8 = st.columns(4)
    with k5:
        _kpi("🧾 Ticket promedio", _fmt_money(metrics["ticket"]), "Promedio por registro con importe")
    with k6:
        _kpi("⏱ Días de cobro", f"{metrics['dso']:.1f}" if metrics["dso"] else "Sin dato", "Promedio ponderado observado")
    with k7:
        _kpi("👥 Pacientes", f"{metrics['pacientes']}", "Pacientes/afiliados únicos detectados")
    with k8:
        _kpi("🎯 Calidad de datos", f"{quality_score:.0f}/100", "Índice interno de completitud")

    tabs = st.tabs([
        "🧠 Dirección",
        "💸 Cobranza",
        "🏥 Producción",
        "🔭 Proyección",
        "🛡️ Auditoría",
        "📋 Detalle",
    ])

    # ------------------------------------------------------------------
    # Dirección
    # ------------------------------------------------------------------
    with tabs[0]:
        st.markdown('<div class="fx-section">Pulso ejecutivo</div>', unsafe_allow_html=True)
        brief = _executive_brief(metrics, monthly, payer, proc, quality_score)
        left, right = st.columns([1.25, 1])
        with left:
            for msg in brief:
                st.markdown(f"- {msg}")
        with right:
            fig_radar = go.Figure()
            fig_radar.add_trace(go.Scatterpolar(
                r=list(health.values()) + [next(iter(health.values()))],
                theta=list(health.keys()) + [next(iter(health.keys()))],
                fill="toself",
                name="Salud",
                line=dict(color=VITAE_PINK),
                fillcolor="rgba(177,86,126,.20)",
            ))
            fig_radar.update_layout(
                height=310,
                margin=dict(l=25, r=25, t=30, b=20),
                polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
                showlegend=False,
                paper_bgcolor="rgba(0,0,0,0)",
                title="Radar interno de salud",
            )
            st.plotly_chart(fig_radar, use_container_width=True, config={"displayModeBar": False})

        if not monthly.empty:
            melted = monthly.melt(id_vars=["mes", "Etiqueta"], value_vars=["Facturado", "Cobrado", "Pendiente"], var_name="Serie", value_name="Importe")
            fig = px.bar(melted, x="Etiqueta", y="Importe", color="Serie", barmode="group", title="Evolución mensual: facturado, cobrado y pendiente")
            st.plotly_chart(_clean_plot(fig, 410), use_container_width=True, config={"displaylogo": False})

        c1, c2 = st.columns(2)
        with c1:
            if not payer.empty:
                top = payer.head(8).sort_values("Facturado")
                fig = px.bar(top, x="Facturado", y="Obra social", orientation="h", title="Top financiadores por facturación")
                st.plotly_chart(_clean_plot(fig, 365), use_container_width=True, config={"displaylogo": False})
        with c2:
            comp = pd.DataFrame({"Estado": ["Cobrado", "Pendiente no vencido", "Vencido"], "Importe": [metrics["cobrado"], max(0, metrics["pendiente"] - metrics["vencido"]), metrics["vencido"]]})
            fig = px.pie(comp, names="Estado", values="Importe", hole=.58, title="Composición económica de la cartera")
            st.plotly_chart(_clean_plot(fig, 365), use_container_width=True, config={"displaylogo": False})

        st.markdown('<div class="fx-section">Plan de acción 7–30–60–90</div>', unsafe_allow_html=True)
        st.dataframe(_action_plan(metrics, queue, payer, quality_score, forecast), use_container_width=True, hide_index=True)

    # ------------------------------------------------------------------
    # Cobranza
    # ------------------------------------------------------------------
    with tabs[1]:
        st.markdown('<div class="fx-section">Motor de cobranza y caja recuperable</div>', unsafe_allow_html=True)
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Vencido", _fmt_money(metrics["vencido"]), _fmt_pct(metrics["mora"]))
        a2.metric("Recupero 30%", _fmt_money(metrics["vencido"] * .30))
        a3.metric("Recupero 50%", _fmt_money(metrics["vencido"] * .50))
        a4.metric("Recupero 70%", _fmt_money(metrics["vencido"] * .70))

        sim = st.slider("Simulador de recupero sobre deuda vencida", 0, 100, 50, 5, key=f"{key}_recovery")
        st.info(f"Con un recupero de {sim}% sobre la cartera vencida se liberarían aproximadamente **{_fmt_money(metrics['vencido'] * sim / 100)}** de caja.")

        c1, c2 = st.columns([1, 1.15])
        with c1:
            if not aging.empty:
                fig = px.bar(aging, x="Tramo", y="Pendiente", text_auto=".2s", title="Aging de cartera pendiente")
                st.plotly_chart(_clean_plot(fig, 365), use_container_width=True, config={"displaylogo": False})
        with c2:
            if not payer.empty:
                p = payer.head(12).copy()
                p["Días cobro plot"] = p["Días cobro"].replace(0, pd.NA)
                fig = px.scatter(
                    p,
                    x="Días cobro plot",
                    y="Cobranza %",
                    size="Facturado",
                    color="Estado interno",
                    hover_name="Obra social",
                    hover_data={"Pendiente": ":,.0f", "Vencido": ":,.0f", "Facturado": ":,.0f"},
                    title="Matriz financiador: velocidad vs cobranza",
                    color_discrete_map={"Fuerte": VITAE_GREEN, "Sano": VITAE_PINK, "Atención": VITAE_AMBER, "Crítico": VITAE_RED},
                )
                st.plotly_chart(_clean_plot(fig, 365), use_container_width=True, config={"displaylogo": False})

        if not payer.empty:
            view = payer.copy()
            for c in ["Facturado", "Cobrado", "Pendiente", "Vencido"]:
                view[c] = view[c].map(_fmt_money)
            for c in ["Cobranza %", "Mora %", "Participación %"]:
                view[c] = view[c].map(_fmt_pct)
            view["Días cobro"] = view["Días cobro"].map(lambda x: f"{x:.1f}" if x else "Sin dato")
            st.markdown("#### Scorecard por obra social")
            st.dataframe(view, use_container_width=True, hide_index=True, height=390)

        st.markdown("#### Cola priorizada de cobranza")
        if queue.empty:
            st.success("No hay saldos pendientes en la vista seleccionada.")
        else:
            q = queue.head(30).copy()
            q["Vencimiento"] = pd.to_datetime(q["Vencimiento"], errors="coerce").dt.strftime("%d/%m/%Y").fillna("Sin fecha")
            q["Pendiente"] = q["Pendiente"].map(_fmt_money)
            st.dataframe(q.drop(columns=["Impacto"]), use_container_width=True, hide_index=True, height=430)

    # ------------------------------------------------------------------
    # Producción
    # ------------------------------------------------------------------
    with tabs[2]:
        st.markdown('<div class="fx-section">Producción económica y concentración</div>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            if not proc.empty:
                top = proc.head(12).sort_values("Facturado")
                fig = px.bar(top, x="Facturado", y="Procedimiento", orientation="h", title="Prácticas por peso económico")
                st.plotly_chart(_clean_plot(fig, 405), use_container_width=True, config={"displaylogo": False})
        with c2:
            if not med.empty:
                top = med.head(12).sort_values("Facturado")
                fig = px.bar(top, x="Facturado", y="Médico", orientation="h", title="Médicos por peso económico")
                st.plotly_chart(_clean_plot(fig, 405), use_container_width=True, config={"displaylogo": False})

        ptab, mtab = st.tabs(["Procedimientos", "Médicos"])
        with ptab:
            if not proc.empty:
                v = proc.copy()
                for c in ["Facturado", "Cobrado", "Pendiente", "Vencido"]:
                    v[c] = v[c].map(_fmt_money)
                v["Cobranza %"] = v["Cobranza %"].map(_fmt_pct)
                v["Participación %"] = v["Participación %"].map(_fmt_pct)
                st.dataframe(v, use_container_width=True, hide_index=True, height=430)
        with mtab:
            if not med.empty:
                v = med.copy()
                for c in ["Facturado", "Cobrado", "Pendiente", "Vencido"]:
                    v[c] = v[c].map(_fmt_money)
                v["Cobranza %"] = v["Cobranza %"].map(_fmt_pct)
                v["Participación %"] = v["Participación %"].map(_fmt_pct)
                st.dataframe(v, use_container_width=True, hide_index=True, height=430)

        if not proc.empty:
            cumulative = proc[["Procedimiento", "Facturado"]].copy().sort_values("Facturado", ascending=False)
            total = cumulative["Facturado"].sum()
            cumulative["Acumulado %"] = 100 * cumulative["Facturado"].cumsum() / total if total else 0
            n80 = int((cumulative["Acumulado %"] < 80).sum() + 1) if not cumulative.empty else 0
            st.info(f"Las primeras **{n80}** prácticas explican aproximadamente el 80% del valor facturado de la vista. Útil para enfocar capacidad, convenios y seguimiento de cobro sin confundir facturación con rentabilidad.")

    # ------------------------------------------------------------------
    # Proyección
    # ------------------------------------------------------------------
    with tabs[3]:
        st.markdown('<div class="fx-section">Run-rate, tendencia y escenarios</div>', unsafe_allow_html=True)
        f1, f2, f3, f4 = st.columns(4)
        f1.metric("Run-rate 12 meses", _fmt_money(float(forecast["run_rate"])), "Promedio reciente ponderado")
        f2.metric("Cierre anual estimado", _fmt_money(float(forecast["year_close"])), "YTD + ritmo reciente")
        f3.metric("Escenario conservador", _fmt_money(float(forecast["conservative"])), "Ajustado por volatilidad")
        f4.metric("Escenario expansivo", _fmt_money(float(forecast["expansive"])), "Ajustado por volatilidad")

        if not monthly.empty:
            plot = monthly[["mes", "Facturado"]].copy()
            future_start = plot["mes"].max() + pd.offsets.MonthBegin(1)
            future_dates = pd.date_range(future_start, periods=6, freq="MS")
            base = float(forecast["base_month"])
            fut = pd.DataFrame({"mes": future_dates, "Facturado": [base] * 6, "Tipo": "Proyección base"})
            hist = plot.assign(Tipo="Observado")
            combined = pd.concat([hist, fut], ignore_index=True)
            fig = px.line(combined, x="mes", y="Facturado", color="Tipo", markers=True, title="Facturación observada + proyección base a 6 meses")
            st.plotly_chart(_clean_plot(fig, 400), use_container_width=True, config={"displaylogo": False})

            if len(monthly) >= 3:
                recent_avg = monthly.tail(3)["Facturado"].mean()
                prev_avg = monthly.iloc[-6:-3]["Facturado"].mean() if len(monthly) >= 6 else monthly.iloc[:-3]["Facturado"].mean()
                if prev_avg and not math.isnan(prev_avg):
                    momentum = 100 * (recent_avg - prev_avg) / prev_avg
                    st.caption(f"Momentum: el promedio de los últimos 3 meses está {momentum:+.1f}% frente al bloque anterior comparable.")

        st.markdown("#### Alertas de tendencia")
        alerts = []
        if forecast["trend"] is not None:
            tr = float(forecast["trend"])
            if tr <= -20:
                alerts.append(f"Caída relevante: el último mes completo bajó {abs(tr):.1f}% frente al anterior.")
            elif tr >= 20:
                alerts.append(f"Aceleración relevante: el último mes completo creció {tr:.1f}% frente al anterior.")
            else:
                alerts.append(f"Movimiento mensual dentro de una banda moderada: {tr:+.1f}%.")
        if not payer.empty and payer.iloc[0]["Participación %"] >= 40:
            alerts.append(f"Concentración comercial: {payer.iloc[0]['Obra social']} explica {_fmt_pct(payer.iloc[0]['Participación %'])} de la facturación.")
        if metrics["mora"] >= 25:
            alerts.append(f"Presión de mora: {_fmt_pct(metrics['mora'])} de la cartera pendiente ya está vencida.")
        if metrics["dso"] > 60:
            alerts.append(f"Velocidad de cobro: el promedio observado es {metrics['dso']:.1f} días.")
        if not alerts:
            alerts.append("No se detectan desvíos fuertes con los datos visibles.")
        for a in alerts:
            st.markdown(f"- {a}")

    # ------------------------------------------------------------------
    # Auditoría
    # ------------------------------------------------------------------
    with tabs[4]:
        st.markdown('<div class="fx-section">Auditoría de trazabilidad y consistencia</div>', unsafe_allow_html=True)
        q1, q2, q3 = st.columns(3)
        q1.metric("Calidad de datos", f"{quality_score:.0f}/100")
        q2.metric("Controles con incidencias", int((quality_table["Registros"] > 0).sum()))
        q3.metric("Incidencias detectadas", int(quality_table["Registros"].sum()))
        st.dataframe(quality_table, use_container_width=True, hide_index=True)

        detected = pd.DataFrame({
            "Dato": ["Mes", "Paciente / afiliado", "Obra social", "Procedimiento", "Médico", "Fecha servicio", "Fecha factura", "N.º factura", "Vencimiento", "Fecha pago", "Importe", "Cobrado", "Saldo", "Estado"],
            "Columna detectada": [cols.get("mes"), cols.get("paciente"), cols.get("obra_social"), cols.get("procedimiento"), cols.get("medico"), cols.get("fecha_servicio"), cols.get("fecha_factura"), cols.get("numero_factura"), cols.get("vencimiento"), cols.get("fecha_pago"), cols.get("monto"), cols.get("cobrado"), cols.get("saldo"), cols.get("estado")],
        }).fillna("No encontrada")
        with st.expander("🧭 Mapa de lectura del Google Sheet", expanded=False):
            st.dataframe(detected, use_container_width=True, hide_index=True)

        if quality_score < 90:
            st.warning("Antes de comparar finamente velocidad de cobro o rendimiento entre financiadores, conviene corregir vencimientos, fechas de pago y facturas faltantes. Los totales económicos siguen mostrándose, pero esas métricas dependen de la trazabilidad temporal.")
        else:
            st.success("La base presenta un nivel alto de completitud para análisis ejecutivo.")

    # ------------------------------------------------------------------
    # Detalle + export + IA
    # ------------------------------------------------------------------
    with tabs[5]:
        st.markdown('<div class="fx-section">Detalle ejecutivo filtrado</div>', unsafe_allow_html=True)
        detail_source = filtered.sort_values("_fecha_base", ascending=False).copy()
        detail = pd.DataFrame({
            "Fecha": detail_source["_fecha_base"].dt.strftime("%d/%m/%Y").fillna(""),
            "Paciente / afiliado": detail_source["_paciente"],
            "Obra social": detail_source["_obra_social"],
            "Procedimiento": detail_source["_procedimiento"],
            "Médico": detail_source["_medico"],
            "Factura": detail_source["_numero_factura"],
            "Vencimiento": detail_source["_vencimiento"].dt.strftime("%d/%m/%Y").fillna(""),
            "Fecha pago": detail_source["_fecha_pago"].dt.strftime("%d/%m/%Y").fillna(""),
            "Facturado": detail_source["_monto"],
            "Cobrado": detail_source["_cobrado"],
            "Pendiente": detail_source["_pendiente"],
            "Estado": detail_source["_estado_ejecutivo"],
        })
        show = detail.copy()
        for c in ["Facturado", "Cobrado", "Pendiente"]:
            show[c] = show[c].map(_fmt_money)
        st.dataframe(show, use_container_width=True, hide_index=True, height=520)

        e1, e2 = st.columns(2)
        e1.download_button(
            "📥 Descargar vista CSV",
            data=detail.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{table}_revenue_intelligence.csv",
            mime="text/csv",
            use_container_width=True,
            key=f"{key}_csv",
        )
        excel = _excel_export(metrics, payer, proc, med, aging, queue, quality_table, monthly)
        if excel:
            e2.download_button(
                "📊 Descargar informe Excel",
                data=excel,
                file_name=f"{table}_informe_ejecutivo.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"{key}_xlsx",
            )
        else:
            e2.info("Exportación Excel no disponible; el CSV sí está listo.")

        st.divider()
        with st.expander("🤖 Copiloto IA de facturación", expanded=False):
            st.caption("Usa el asistente ya existente en tu proyecto y únicamente la vista filtrada.")
            question = st.text_input(
                "Pregunta",
                placeholder="Ej.: ¿Qué obra social debería priorizar esta semana y por qué?",
                key=f"{key}_ai_q",
            )
            if st.button("Analizar", key=f"{key}_ai_btn", use_container_width=False):
                if not question.strip():
                    st.warning("Escribí una pregunta.")
                else:
                    try:
                        from assistant import preguntar_ia
                        ai_df = filtered[["_fecha_base", "_paciente", "_obra_social", "_procedimiento", "_medico", "_numero_factura", "_vencimiento", "_fecha_pago", "_monto", "_cobrado", "_pendiente", "_estado_ejecutivo"]].copy()
                        ai_df.columns = ["fecha", "paciente", "obra_social", "procedimiento", "medico", "factura", "vencimiento", "fecha_pago", "facturado", "cobrado", "pendiente", "estado"]
                        answer = preguntar_ia(module_name, ai_df, question)
                        st.markdown(answer)
                    except Exception as exc:
                        st.error(f"El Copiloto IA no pudo responder: {exc}")

    st.caption("Centro Revenue Intelligence · Solo lectura · Los scores, semáforos, escenarios y prioridades son indicadores internos calculados sobre la base visible; no reemplazan validación contable ni contractual.")
