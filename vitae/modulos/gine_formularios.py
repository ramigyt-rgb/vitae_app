"""Gine Vitae · Formularios: pacientes que se registran en el formulario web.

Lee el archivo que publica el formulario (por defecto
https://vitae.com.ar/ginevitae.txt: Fecha;Nombre;Apellido;Edad;Obra Social;
Telefono;Email;) y agrega solas las inscripciones nuevas a la pestaña
``gine_vitae_formularios`` de Google Sheets. Ahí cada paciente suma un
estado (Nuevo, Contactado, Con turno...) y notas, que se editan desde la app.

La dirección del archivo se puede cambiar con el secret GINE_FORMULARIO_URL.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import re
import urllib.request
from datetime import datetime
from typing import Any, Callable

import pandas as pd
import streamlit as st

from vitae.modulos.pagos_pendientes import MESES_ES, _sin_acentos, _texto, nombre_mes

try:
    from vitae.core.helpers import leer_fechas_ar
except Exception:  # pragma: no cover
    def leer_fechas_ar(valores):
        return pd.to_datetime(pd.Series(valores), dayfirst=True, errors="coerce")

try:
    from vitae.core.movil import es_movil
except Exception:  # pragma: no cover
    def es_movil() -> bool:
        return False

try:
    from vitae.core.ui import tarjetas_kpi
except Exception:  # pragma: no cover
    def tarjetas_kpi(tarjetas):
        for col, (t, v, p, _) in zip(st.columns(len(tarjetas)), tarjetas):
            col.metric(t, v, p)


URL_POR_DEFECTO = "https://vitae.com.ar/ginevitae.txt"
TABLA = "gine_vitae_formularios"
ESTADOS = ["Nuevo", "Contactado", "Con turno", "No interesada", "Duplicado"]
COLOR_ESTADO = {"Nuevo": "#8C2853", "Contactado": "#2F6F8F", "Con turno": "#2F7D4F",
                "No interesada": "#8A7F85", "Duplicado": "#B9AEB4"}
COLUMNAS = ["id", "fecha", "nombre", "apellido", "edad", "obra_social", "telefono", "email",
            "estado", "notas", "agregado"]
_CAMPOS = ["fecha", "nombre", "apellido", "edad", "obra_social", "telefono", "email"]
_ALIAS = {"fecha": "fecha", "nombre": "nombre", "nombres": "nombre", "apellido": "apellido", "apellidos": "apellido",
          "edad": "edad", "obra social": "obra_social", "obra_social": "obra_social", "cobertura": "obra_social",
          "telefono": "telefono", "celular": "telefono", "tel": "telefono", "email": "email", "mail": "email",
          "correo": "email", "e-mail": "email"}


def url_formulario() -> str:
    try:
        return str(st.secrets.get("GINE_FORMULARIO_URL", "") or URL_POR_DEFECTO)
    except Exception:
        return URL_POR_DEFECTO


def descargar(url: str, timeout: float = 20.0) -> str:
    pedido = urllib.request.Request(url, headers={"User-Agent": "VitaeApp/1.0", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(pedido, timeout=timeout) as resp:  # noqa: S310 (URL fija o de secrets)
        crudo = resp.read()
    for codificacion in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return crudo.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return crudo.decode("utf-8", errors="replace")


@st.cache_data(ttl=300, show_spinner=False)
def _descargar_cache(url: str, vuelta: int) -> tuple[str, str]:
    """Texto del formulario (se vuelve a leer cada 5 minutos o con «Actualizar»)."""
    return descargar(url), datetime.now().strftime("%H:%M")


def fecha_iso(valor: Any) -> str:
    """Fecha del formulario web a AAAA-MM-DD.

    El archivo trae mes/día/año (11/25/2026 = 25 de noviembre). Si el primer
    número no puede ser un mes (> 12) se toma como día. Las fechas que ya
    están en AAAA-MM-DD quedan igual.
    """
    t = _texto(valor)
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        anio, mes, dia = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})(?!\d)", t)
        if not m:
            return t
        mes, dia, anio = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if anio < 100:
            anio += 2000
        if mes > 12 and dia <= 12:
            mes, dia = dia, mes
    try:
        return datetime(anio, mes, dia).strftime("%Y-%m-%d")
    except ValueError:
        return t


def _id(fila: dict[str, str]) -> str:
    clave = "|".join(_sin_acentos(fila.get(c, "")) for c in _CAMPOS)
    return hashlib.sha1(clave.encode("utf-8")).hexdigest()[:12]


def leer_texto(texto: str) -> pd.DataFrame:
    """Convierte el archivo del formulario en una tabla (una fila por inscripción)."""
    texto = (texto or "").lstrip("﻿")
    lineas = [l for l in texto.splitlines() if l.strip()]
    if not lineas:
        return pd.DataFrame(columns=_CAMPOS + ["id", "orden"])
    primera = lineas[0]
    sep = ";" if primera.count(";") >= primera.count(",") and ";" in primera else ("\t" if "\t" in primera else ",")
    filas = list(csv.reader(io.StringIO("\n".join(lineas)), delimiter=sep))
    cabecera = [_ALIAS.get(_sin_acentos(c).replace("_", " "), _ALIAS.get(_sin_acentos(c), "")) for c in filas[0]]
    if {"fecha", "nombre"} <= set(cabecera):
        datos = filas[1:]
    else:  # sin encabezado: el orden del formulario
        cabecera, datos = _CAMPOS[:], filas
    registros = []
    for n, fila in enumerate(datos, start=1):
        reg = {c: "" for c in _CAMPOS}
        for i, valor in enumerate(fila):
            if i < len(cabecera) and cabecera[i] in reg:
                reg[cabecera[i]] = re.sub(r"\s+", " ", str(valor)).strip()
        if not any(reg[c] for c in ("nombre", "apellido", "telefono", "email")):
            continue
        reg["id"] = _id(reg)  # con la fecha tal como viene: no cambia aunque se corrija el formato
        reg["fecha"] = fecha_iso(reg["fecha"])
        reg["orden"] = n
        registros.append(reg)
    df = pd.DataFrame(registros, columns=_CAMPOS + ["id", "orden"])
    return df.drop_duplicates("id", keep="first").reset_index(drop=True)


def normalizar_guardado(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=COLUMNAS)
    out = df.copy().astype(object)
    out.columns = [str(c).strip() for c in out.columns]
    for c in COLUMNAS:
        if c not in out.columns:
            out[c] = ""
    out = out.where(out.notna(), "")
    sin_id = out["id"].map(_texto) == ""
    if sin_id.any():
        out.loc[sin_id, "id"] = [_id({c: _texto(f[c]) for c in _CAMPOS}) for _, f in out.loc[sin_id].iterrows()]
    out["estado"] = [(_texto(e) if _texto(e) in ESTADOS else "Nuevo") for e in out["estado"]]
    out["fecha"] = out["fecha"].map(fecha_iso)
    return out.drop_duplicates("id", keep="first").reset_index(drop=True)


def sincronizar(guardado: pd.DataFrame, web: pd.DataFrame, ahora: str | None = None) -> tuple[pd.DataFrame, int]:
    """Agrega al final las inscripciones de la web que todavía no están guardadas."""
    base = normalizar_guardado(guardado)
    if web is None or web.empty:
        return base, 0
    nuevas = web[~web["id"].isin(set(base["id"]))]
    if nuevas.empty:
        return base, 0
    ahora = ahora or datetime.now().strftime("%Y-%m-%d %H:%M")
    agregar = nuevas[_CAMPOS + ["id"]].copy()
    agregar["estado"] = "Nuevo"
    agregar["notas"] = ""
    agregar["agregado"] = ahora
    extras = [c for c in base.columns if c not in COLUMNAS]
    combinado = pd.concat([base, agregar.reindex(columns=COLUMNAS + extras, fill_value="")], ignore_index=True)
    return combinado[COLUMNAS + extras], int(len(agregar))


def whatsapp(telefono: Any) -> str:
    """Link de WhatsApp para un celular argentino (0387 15… / 387… / +54 9…)."""
    d = re.sub(r"\D", "", _texto(telefono))
    if not d:
        return ""
    if d.startswith("54"):
        d = d[2:]
    d = d.lstrip("0")
    if d.startswith("9") and len(d) == 11:
        d = d[1:]
    m = re.fullmatch(r"(\d{2,4})15(\d{6,8})", d)
    if m and len(m.group(1)) + len(m.group(2)) == 10:
        d = m.group(1) + m.group(2)
    return f"https://wa.me/549{d}" if len(d) == 10 else ""


def preparar(guardado: pd.DataFrame) -> pd.DataFrame:
    d = normalizar_guardado(guardado)
    if d.empty:
        return d.assign(_fecha=pd.Series(dtype="datetime64[ns]"), _mes="", paciente="", whatsapp="", veces=0)
    d["_fecha"] = pd.to_datetime(d["fecha"].map(fecha_iso), format="%Y-%m-%d", errors="coerce")
    d["_mes"] = [str(pd.Period(f, freq="M")) if pd.notna(f) else "" for f in d["_fecha"]]
    d["paciente"] = [", ".join(x for x in (_texto(a).upper(), _texto(n).title()) if x) for a, n in zip(d["apellido"], d["nombre"])]
    d["whatsapp"] = d["telefono"].map(whatsapp)
    clave = [(_texto(e).lower() or re.sub(r"\D", "", _texto(t))[-8:] or p) for e, t, p in zip(d["email"], d["telefono"], d["paciente"])]
    d["_persona"] = clave
    d["veces"] = d.groupby("_persona")["id"].transform("count").astype(int)
    d["_orden"] = range(len(d))
    return d.sort_values(["_fecha", "_orden"], ascending=[False, False], na_position="last")


def resumen(d: pd.DataFrame, hoy: pd.Timestamp | None = None) -> dict[str, int]:
    hoy = (hoy or pd.Timestamp.today()).normalize()
    if d.empty:
        return {"total": 0, "personas": 0, "nuevos": 0, "mes": 0, "semana": 0}
    return {
        "total": int(len(d)),
        "personas": int(d["_persona"].nunique()),
        "nuevos": int((d["estado"] == "Nuevo").sum()),
        "mes": int((d["_mes"] == str(pd.Period(hoy, freq="M"))).sum()),
        "semana": int((d["_fecha"] >= hoy - pd.Timedelta(days=6)).sum()),
    }


_CSS = """
<style>
.gf-card{border:1px solid #E8E3E6;border-radius:12px;padding:.65rem .8rem;margin-bottom:.5rem;background:#fff}
.gf-card .n{font-weight:650;color:#1F1A1D}
.gf-card .m{font-size:.78rem;color:#7A6F75;margin-top:.1rem}
.gf-card .a{display:flex;gap:.45rem;flex-wrap:wrap;margin-top:.45rem}
.gf-card .a a{font-size:.8rem;text-decoration:none;padding:.22rem .6rem;border-radius:999px;border:1px solid #E2D6DC;color:#5E1A39}
.gf-card .a a.wa{background:#E8F5EC;border-color:#BFE0C9;color:#1F6B3A}
.gf-chip{display:inline-block;padding:.08rem .5rem;border-radius:999px;font-size:.7rem;font-weight:600;color:#fff;margin-left:.35rem;vertical-align:middle}
</style>
"""


def _tarjeta(f: pd.Series) -> str:
    e = html.escape
    fecha = f["_fecha"].strftime("%d/%m/%Y") if pd.notna(f["_fecha"]) else _texto(f["fecha"])
    meta = " · ".join(x for x in (fecha, f"{_texto(f['edad'])} años" if _texto(f["edad"]) else "", _texto(f["obra_social"])) if x)
    acciones = []
    if f["whatsapp"]:
        acciones.append(f'<a class="wa" href="{e(f["whatsapp"])}" target="_blank">WhatsApp</a>')
    if _texto(f["telefono"]):
        acciones.append(f'<a href="tel:{e(re.sub(r"[^0-9+]", "", _texto(f["telefono"])))}">Llamar {e(_texto(f["telefono"]))}</a>')
    if _texto(f["email"]):
        acciones.append(f'<a href="mailto:{e(_texto(f["email"]))}">{e(_texto(f["email"]))}</a>')
    repetido = f' · se registró {int(f["veces"])} veces' if int(f["veces"]) > 1 else ""
    chip = f'<span class="gf-chip" style="background:{COLOR_ESTADO.get(f["estado"], "#777")}">{e(f["estado"])}</span>'
    notas = f'<div class="m">📝 {e(_texto(f["notas"]))}</div>' if _texto(f["notas"]) else ""
    return (f'<div class="gf-card"><div class="n">{e(f["paciente"] or "(sin nombre)")}{chip}</div>'
            f'<div class="m">{e(meta)}{e(repetido)}</div>{notas}<div class="a">{"".join(acciones)}</div></div>')


def render_formularios(
    *,
    leer_guardado: Callable[[], pd.DataFrame],
    guardar: Callable[[pd.DataFrame], Any],
    url: str | None = None,
    mostrar_error: Callable[[str, Exception], None] | None = None,
) -> None:
    url = url or url_formulario()
    k = "gf_"

    def _error(msg: str, exc: Exception) -> None:
        (mostrar_error or (lambda m, e: st.error(f"{m} {e}")))(msg, exc)

    st.markdown(_CSS, unsafe_allow_html=True)
    corregir_fechas = False
    try:
        crudo = leer_guardado()
        guardado = normalizar_guardado(crudo)
        if isinstance(crudo, pd.DataFrame) and "fecha" in crudo.columns and not crudo.empty:
            antes = crudo.drop_duplicates("id", keep="first")["fecha"].map(_texto).tolist() if "id" in crudo.columns \
                else crudo["fecha"].map(_texto).tolist()
            corregir_fechas = antes != guardado["fecha"].map(_texto).tolist()[:len(antes)]
    except Exception as exc:
        _error("No se pudo leer la pestaña de formularios en Google Sheets.", exc)
        guardado = normalizar_guardado(None)

    vuelta = int(st.session_state.get(f"{k}vuelta", 0))
    web, hora, error_web = pd.DataFrame(), "", None
    try:
        texto, hora = _descargar_cache(url, vuelta)
        web = leer_texto(texto)
    except Exception as exc:  # sin conexión con la web: se muestra lo guardado
        error_web = exc

    datos, nuevos = sincronizar(guardado, web)
    if corregir_fechas and not nuevos:
        try:
            guardar(datos)  # deja las fechas guardadas como AAAA-MM-DD (una sola vez)
        except Exception:
            pass
    if nuevos:
        try:
            guardar(datos)
            st.toast(f"{nuevos} inscripción(es) nueva(s) agregadas desde el formulario", icon=":material/person_add:")
        except Exception as exc:
            _error(f"Llegaron {nuevos} inscripciones nuevas pero no se pudieron guardar en Google Sheets "
                   "(se muestran igual).", exc)

    c1, c2 = st.columns([3, 1])
    with c1:
        if error_web is not None:
            st.warning(f"No se pudo leer el formulario web ({html.escape(str(error_web))[:120]}). "
                       "Se muestran las inscripciones ya guardadas.")
        else:
            st.caption(f":material/sync: Se actualiza sola desde el formulario web cada 5 minutos · "
                       f"última lectura {hora} · {len(web)} inscripciones en la web"
                       + (f" · {nuevos} nuevas agregadas ahora" if nuevos else ""))
    with c2:
        if st.button("Actualizar ahora", key=f"{k}actualizar", icon=":material/refresh:", use_container_width=True):
            st.session_state[f"{k}vuelta"] = vuelta + 1
            st.rerun()

    d = preparar(datos)
    if d.empty:
        st.info("Todavía no hay inscripciones del formulario.")
        return
    r = resumen(d)
    tarjetas_kpi([
        ("Inscripciones", f"{r['total']:,}".replace(",", "."), f"{r['personas']:,} personas".replace(",", "."), "#8C2853"),
        ("Sin contactar", f"{r['nuevos']:,}".replace(",", "."), "Estado «Nuevo»", "#B14E5E"),
        ("Este mes", f"{r['mes']:,}".replace(",", "."), nombre_mes(str(pd.Period(pd.Timestamp.today(), freq="M"))), "#2F6F8F"),
        ("Últimos 7 días", f"{r['semana']:,}".replace(",", "."), "Inscripciones", "#2F7D4F"),
    ])

    meses_d = sorted({m for m in d["_mes"] if m}, reverse=True)
    f1, f2, f3 = st.columns([1.4, 1, 1])
    with f1:
        buscar = st.text_input("Buscar", key=f"{k}buscar", placeholder="Nombre, teléfono o email")
    with f2:
        mes = st.selectbox("Mes", ["Todos"] + meses_d, key=f"{k}mes",
                           format_func=lambda m: m if m == "Todos" else nombre_mes(m))
    with f3:
        obras = sorted({_texto(o).upper() for o in d["obra_social"] if _texto(o)})
        obra = st.selectbox("Obra social", ["Todas"] + obras, key=f"{k}obra")
    estado = st.pills("Estado", ["Todos"] + ESTADOS, default="Todos", key=f"{k}estado") or "Todos"

    vista = d
    if buscar.strip():
        b = _sin_acentos(buscar)
        digitos = re.sub(r"\D", "", buscar)
        vista = vista[[
            b in _sin_acentos(f"{p} {_texto(e)} {_texto(o)}") or (len(digitos) >= 4 and digitos in re.sub(r"\D", "", _texto(t)))
            for p, e, o, t in zip(vista["paciente"], vista["email"], vista["obra_social"], vista["telefono"])
        ]]
    if mes != "Todos":
        vista = vista[vista["_mes"] == mes]
    if obra != "Todas":
        vista = vista[vista["obra_social"].map(lambda o: _texto(o).upper()) == obra]
    if estado != "Todos":
        vista = vista[vista["estado"] == estado]
    st.caption(f"{len(vista):,} inscripciones".replace(",", ".") + (" con estos filtros" if len(vista) != len(d) else ""))
    if vista.empty:
        st.info("No hay inscripciones con esos filtros.")
        return

    if es_movil():
        st.markdown("".join(_tarjeta(f) for _, f in vista.head(60).iterrows()), unsafe_allow_html=True)
        if len(vista) > 60:
            st.caption("Se muestran las 60 más recientes. Usá los filtros para encontrar otras.")
        contenedor = st.expander("Cambiar estado o notas", icon=":material/edit_note:")
    else:
        contenedor = st.container()

    with contenedor:
        tabla = vista[["id", "_fecha", "paciente", "edad", "obra_social", "telefono", "whatsapp", "email", "veces",
                       "estado", "notas"]].copy().set_index("id")
        tabla["_fecha"] = tabla["_fecha"].dt.date
        tabla["notas"] = tabla["notas"].map(_texto)
        editada = st.data_editor(
            tabla, hide_index=True, use_container_width=True, num_rows="fixed",
            height=min(80 + 36 * len(tabla), 640),
            disabled=["_fecha", "paciente", "edad", "obra_social", "telefono", "whatsapp", "email", "veces"],
            column_config={
                "_fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY", width="small"),
                "paciente": st.column_config.TextColumn("Paciente", width="medium"),
                "edad": st.column_config.TextColumn("Edad", width="small"),
                "obra_social": st.column_config.TextColumn("Obra social"),
                "telefono": st.column_config.TextColumn("Teléfono"),
                "whatsapp": st.column_config.LinkColumn("WhatsApp", display_text="Escribir", width="small"),
                "email": st.column_config.TextColumn("Email"),
                "veces": st.column_config.NumberColumn("Veces", help="Cuántas veces se registró la misma persona",
                                                       width="small"),
                "estado": st.column_config.SelectboxColumn("Estado", options=ESTADOS, required=True),
                "notas": st.column_config.TextColumn("Notas", width="medium"),
            },
            key=f"{k}editor_{int(st.session_state.get(f'{k}rev', 0))}",
        )
        cambios = {i: (f["estado"], _texto(f["notas"])) for i, f in editada.iterrows()
                   if (f["estado"], _texto(f["notas"])) != (tabla.at[i, "estado"], tabla.at[i, "notas"])}
        b1, b2 = st.columns(2)
        with b1:
            if st.button(f"Guardar cambios ({len(cambios)})", type="primary", disabled=not cambios,
                         key=f"{k}guardar", icon=":material/save:", use_container_width=True):
                nuevo = datos.copy()
                for i, (est, nota) in cambios.items():
                    nuevo.loc[nuevo["id"] == i, "estado"] = est
                    nuevo.loc[nuevo["id"] == i, "notas"] = nota
                try:
                    guardar(nuevo)
                except Exception as exc:
                    _error("No se pudieron guardar los cambios.", exc)
                else:
                    st.session_state[f"{k}rev"] = int(st.session_state.get(f"{k}rev", 0)) + 1
                    st.toast("Cambios guardados", icon=":material/check_circle:")
                    st.rerun()
        with b2:
            salida = io.BytesIO()
            exportar = vista.assign(fecha=vista["_fecha"].dt.strftime("%d/%m/%Y").fillna(""))[["fecha", "apellido", "nombre", "edad", "obra_social", "telefono", "email", "estado",
                              "notas"]].rename(columns={"obra_social": "obra social"})
            exportar.columns = [c.capitalize() for c in exportar.columns]
            exportar.to_excel(salida, index=False, sheet_name="Formularios")
            st.download_button("Descargar Excel", data=salida.getvalue(), file_name="gine_vitae_formularios.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               key=f"{k}xls", icon=":material/download:", use_container_width=True)
