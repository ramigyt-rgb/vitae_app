"""Luz, experta en Facturación VMR y VM.

Herramientas de consulta que usan EXACTAMENTE las funciones del módulo de
Facturación (vitae/modulos/facturacion.py, con el ajuste que le hace
vistas.py): mismo criterio de fecha (el «mes» de la planilla), mismo cálculo
de cobrado / pendiente / vencido, mismas tablas por obra social, antigüedad,
cola de cobranza y controles de calidad. Así lo que dice Luz coincide siempre
con la pantalla de Facturación y con el Panel general.

Sólo se ofrecen si el usuario tiene acceso al módulo de esa empresa.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Callable

import pandas as pd

EMPRESAS = {"VMR": "Facturación VMR", "VM": "Facturación VM"}
PERIODOS = ["Mes actual", "Mes anterior", "Últimos 3 meses", "Últimos 12 meses", "Año actual", "Año anterior",
            "Todo el historial"]
AGRUPACIONES = {"obra_social": ("_obra_social", "Obra social"), "medico": ("_medico", "Médico"),
                "procedimiento": ("_procedimiento", "Procedimiento"), "estado": ("_estado_ejecutivo", "Estado"),
                "mes": ("_mes", "Mes"), "paciente": ("_paciente", "Paciente")}


def _norm(v: Any) -> str:
    t = unicodedata.normalize("NFKD", str(v if v is not None else "").strip().lower())
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c)))


def _fx():
    # Siempre a través del módulo: vistas.py le ajusta _prepare_facturacion al importar.
    import vitae.modulos.facturacion as fx

    return fx


def _r(x: Any, dec: int = 2) -> Any:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return x
    return round(f, dec) if f == f else None  # NaN → None


def pesos(v: Any) -> str:
    n = float(v or 0)
    return ("-" if n < 0 else "") + "$ " + f"{abs(n):,.0f}".replace(",", ".")


def preparar(df: pd.DataFrame) -> pd.DataFrame:
    fx = _fx()
    data, _ = fx._prepare_facturacion(df if isinstance(df, pd.DataFrame) else pd.DataFrame())
    return fx._ajustar_estado_manual(data)


def rango(periodo: str = "", desde: str = "", hasta: str = "", hoy: pd.Timestamp | None = None
          ) -> tuple[pd.Timestamp | None, pd.Timestamp | None, str]:
    """periodo: uno de PERIODOS o «AAAA-MM»; o desde/hasta (AAAA-MM o AAAA-MM-DD)."""
    hoy = (hoy or pd.Timestamp.today()).normalize()

    def fecha(t: str, fin: bool) -> pd.Timestamp | None:
        t = str(t or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}", t):
            p = pd.Period(t, freq="M")
            return (p.end_time if fin else p.start_time).normalize()
        try:
            return pd.Timestamp(t).normalize() if t else None
        except Exception:
            return None

    if desde or hasta:
        d, h = fecha(desde, False), fecha(hasta, True)
        return d, h, f"{d:%d/%m/%Y} a {h:%d/%m/%Y}" if d is not None and h is not None else (desde or hasta)
    p = str(periodo or "").strip()
    if not p or _norm(p) in {"todo", "todo el historial", "historico", "todo el historico"}:
        return None, None, "todo el historial"
    elegido = next((x for x in PERIODOS if _norm(x) == _norm(p)), p)
    d, h = _fx()._rango_periodo(elegido, hoy)
    return d, h, elegido if d is not None else "todo el historial"


def _coincide(serie: pd.Series, texto: str) -> pd.Series:
    t = _norm(texto)
    if not t:
        return pd.Series(True, index=serie.index)
    n = serie.map(_norm)
    exacto = n.eq(t)
    return exacto if exacto.any() else n.str.contains(re.escape(t), na=False)


def filtrar(data: pd.DataFrame, *, periodo: str = "", desde: str = "", hasta: str = "", obra_social: str = "",
            medico: str = "", procedimiento: str = "", paciente: str = "", hoy: pd.Timestamp | None = None
            ) -> tuple[pd.DataFrame, str]:
    d, h, etiqueta = rango(periodo, desde, hasta, hoy)
    x = data
    if d is not None and h is not None:
        x = x[x["_fecha_base"].between(d, h + pd.Timedelta(hours=23, minutes=59, seconds=59))]
    for col, valor in (("_obra_social", obra_social), ("_medico", medico), ("_procedimiento", procedimiento),
                       ("_paciente", paciente)):
        if valor:
            x = x[_coincide(x[col], valor)]
    return x, etiqueta


def _empresas(empresa: str, disponibles: dict[str, str]) -> list[str]:
    e = _norm(empresa).upper().replace(" ", "")
    pedidas = ["VMR"] if e in {"VMR", "REPRODUCTIVA", "MEDICINAREPRODUCTIVA"} else (
        ["VM"] if e in {"VM", "MEDICAL", "VITAEMEDICAL"} else ["VMR", "VM"])
    return [x for x in pedidas if EMPRESAS[x] in disponibles]


def resumen(data: pd.DataFrame) -> dict[str, Any]:
    fx = _fx()
    m = fx._metrics(data) if len(data) else {k: 0.0 for k in ("facturado", "cobrado", "pendiente", "vencido", "tasa",
                                                             "mora", "ticket", "dso", "pacientes", "registros")}
    activos = data[~data["_cancelado"]] if len(data) else data
    estados = activos["_estado_ejecutivo"].value_counts().to_dict() if len(activos) else {}
    return {
        "facturado": _r(m["facturado"]), "cobrado": _r(m["cobrado"]), "pendiente": _r(m["pendiente"]),
        "vencido": _r(m["vencido"]), "texto": (f"Facturado {pesos(m['facturado'])} · cobrado {pesos(m['cobrado'])} · "
                                               f"pendiente {pesos(m['pendiente'])} · vencido {pesos(m['vencido'])}"),
        "cobranza_pct": _r(m["tasa"], 1), "mora_pct": _r(m["mora"], 1), "ticket_promedio": _r(m["ticket"]),
        "dias_promedio_de_cobro": _r(m["dso"], 0), "pacientes": int(m["pacientes"]), "registros": int(m["registros"]),
        "por_estado": {k: int(v) for k, v in estados.items()},
        "anuladas": int(data["_cancelado"].sum()) if len(data) else 0,
    }


def _tabla(df: pd.DataFrame, limite: int) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    out = []
    for _, f in df.head(max(1, min(int(limite or 15), 200))).iterrows():
        fila = {}
        for k, v in f.items():
            if isinstance(v, pd.Timestamp):
                fila[k] = v.strftime("%d/%m/%Y") if pd.notna(v) else ""
            elif isinstance(v, float):
                fila[k] = _r(v)
            else:
                fila[k] = v if not (isinstance(v, float) and v != v) else None
        out.append(fila)
    return out


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


INSTRUCCIONES = """EXPERTA EN FACTURACIÓN VMR Y VM (sabés todo del módulo; usá SIEMPRE las herramientas facturacion_*)
- Para cualquier número de facturación usá facturacion_resumen, facturacion_agrupado, facturacion_pendientes,
  facturacion_buscar o facturacion_calidad: calculan igual que la pantalla de Facturación y el Panel general.
  No uses consultar/listar_registros para facturación.
- Empresas: VMR = Vitae Medicina Reproductiva, VM = Vitae Medical. Se informan por separado; «ambas» las suma.
- Período: cada fila cuenta en el mes de la columna «mes» de la planilla (la fecha base). periodo puede ser
  Mes actual, Mes anterior, Últimos 3 meses, Últimos 12 meses, Año actual, Año anterior, Todo el historial o
  AAAA-MM; o desde/hasta.
- Definiciones: Facturado = importe de la fila. Cobrado = lo cobrado (si la fila dice cobrado/pagado o tiene
  fecha de pago, se toma cobrado total). Pendiente = facturado − cobrado. Vencido = pendiente con vencimiento
  pasado o marcado «Vencido». Anuladas/rechazadas no suman. «Sin importe» = filas sin monto.
  Cobranza % = cobrado / facturado. Mora % = vencido / pendiente. Días de cobro = promedio entre factura y pago.
- Estados: Cobrado, Pendiente, Vencido, Anulado, Sin importe.
- Cuando hablen de «lo que nos deben», es el pendiente; «lo atrasado», el vencido.
- CONTROL: para «qué tengo que hacer», «qué se me pasa», alertas o el resumen de la mañana usá facturacion_control
  (bandeja por urgencia: cirugías realizadas sin cargar, sin facturar con plazo de 60 días vencido o por vencer,
  facturas vencidas o por vencer, débitos para reclamar, datos incompletos). Empezá por lo urgente. Las cirugías
  sin cargar van a la empresa de su procedimiento: en VMR sólo las de VMR (reproducción); el resto, en VM.
- Débitos: lo que la obra social pagó de menos (columnas debito, debito_estado: A reclamar, Reclamado, Recuperado,
  Aceptado; debito_motivo). Un débito no recuperado no es cobrado; si se aceptó, deja de ser deuda.
- Si te preguntan cómo hacer algo en la pantalla: Facturación VMR/VM tiene Control (bandeja de hoy, controles y
  cierre de mes, donde se resuelve todo ahí mismo), Resumen, Cobranza, Producción y Calidad (análisis) y Registros, Cargar, Importar y Exportar (gestión). En Registros («Editar registros») se
  filtra por obra social y por cobro (Pendientes, Vencidos, Cobrados, Sin importe) y muestra el total facturado y
  pendiente de lo filtrado."""


def herramientas(datos: Any, hoy: pd.Timestamp | None = None) -> list[Callable[..., str]]:
    disponibles = datos.modulos()
    if not any(n in disponibles for n in EMPRESAS.values()):
        return []
    cache: dict[str, pd.DataFrame] = {}

    def base(emp: str) -> pd.DataFrame:
        if emp not in cache:
            cache[emp] = preparar(datos.df(EMPRESAS[emp]))
        return cache[emp]

    def registrar(texto: str) -> None:
        try:
            datos.consultas.append(texto)
        except Exception:
            pass

    def facturacion_resumen(empresa: str = "ambas", periodo: str = "Mes actual", desde: str = "", hasta: str = "",
                            obra_social: str = "", medico: str = "", procedimiento: str = "") -> str:
        """Totales de facturación como en la pantalla: facturado, cobrado, pendiente, vencido, cobranza %, mora %,
        ticket promedio, días de cobro, pacientes y cantidad por estado. empresa: VMR, VM o ambas.
        periodo: Mes actual, Mes anterior, Últimos 3 meses, Últimos 12 meses, Año actual, Año anterior,
        Todo el historial o AAAA-MM (o usar desde/hasta). Filtros opcionales por obra social, médico o procedimiento."""
        registrar(f"facturacion_resumen({empresa}, {periodo or desde})")
        salida: dict[str, Any] = {}
        juntas = []
        for emp in _empresas(empresa, disponibles):
            x, etiqueta = filtrar(base(emp), periodo=periodo, desde=desde, hasta=hasta, obra_social=obra_social,
                                  medico=medico, procedimiento=procedimiento, hoy=hoy)
            salida[emp] = resumen(x)
            salida["periodo"] = etiqueta
            juntas.append(x)
        if len(juntas) > 1:
            salida["ambas"] = resumen(pd.concat(juntas, ignore_index=True))
        if not juntas:
            return _json({"error": "No tenés acceso a Facturación VMR ni VM."})
        return _json(salida)

    def facturacion_agrupado(agrupar: str = "obra_social", empresa: str = "ambas", periodo: str = "Mes actual",
                             desde: str = "", hasta: str = "", obra_social: str = "", medico: str = "",
                             procedimiento: str = "", limite: int = 15) -> str:
        """Facturado, cobrado, pendiente y vencido agrupado. agrupar: obra_social, medico, procedimiento, estado,
        mes o paciente. Para obra_social agrega cobranza %, mora %, días de cobro y estado interno (Fuerte, Sano,
        Atención, Crítico). Ordenado por facturado."""
        registrar(f"facturacion_agrupado({agrupar}, {empresa}, {periodo or desde})")
        fx = _fx()
        col, etiqueta_col = AGRUPACIONES.get(_norm(agrupar).replace(" ", "_"), AGRUPACIONES["obra_social"])
        salida: dict[str, Any] = {}
        for emp in _empresas(empresa, disponibles):
            x, etiqueta = filtrar(base(emp), periodo=periodo, desde=desde, hasta=hasta, obra_social=obra_social,
                                  medico=medico, procedimiento=procedimiento, hoy=hoy)
            salida["periodo"] = etiqueta
            if x.empty:
                salida[emp] = []
                continue
            if col == "_obra_social":
                t = fx._payer_table(x)
            elif col == "_mes":
                t = fx._monthly(x).drop(columns=["Etiqueta"], errors="ignore")
                t["mes"] = t["mes"].dt.strftime("%Y-%m")
            else:
                t = fx._group_table(x, col, etiqueta_col)
            salida[emp] = _tabla(t, limite)
        return _json(salida)

    def facturacion_pendientes(empresa: str = "ambas", obra_social: str = "", solo_vencidas: bool = False,
                               limite: int = 20) -> str:
        """Lo que falta cobrar, factura por factura, ordenado por prioridad de cobranza (Crítica, Alta, Media,
        Preventiva) como en la pestaña Cobranza, más la antigüedad de la deuda por tramos (A vencer, 1-30, 31-60,
        61-90, +90 días, Sin vencimiento). Todo el historial (lo pendiente no depende del mes)."""
        registrar(f"facturacion_pendientes({empresa}, {obra_social}, vencidas={solo_vencidas})")
        fx = _fx()
        salida: dict[str, Any] = {}
        for emp in _empresas(empresa, disponibles):
            x, _ = filtrar(base(emp), obra_social=obra_social, hoy=hoy)
            if solo_vencidas:
                x = x[x["_vencido"]]
            cola = fx._priority_queue(x)
            antig = fx._aging(x)
            salida[emp] = {
                "total_pendiente": _r(x.loc[~x["_cancelado"], "_pendiente"].sum()),
                "total_vencido": _r(x.loc[~x["_cancelado"] & x["_vencido"], "_pendiente"].sum()),
                "facturas_con_saldo": int(((x["_pendiente"] > 0) & ~x["_cancelado"]).sum()),
                "antiguedad": _tabla(antig.assign(Tramo=antig["Tramo"].astype(str)) if len(antig) else antig, 10),
                "facturas": _tabla(cola.drop(columns=["Score", "Impacto"], errors="ignore"), limite),
            }
        return _json(salida)

    def facturacion_buscar(texto: str, empresa: str = "ambas", limite: int = 20) -> str:
        """Busca facturas por paciente/afiliado, N° de factura, obra social, médico o procedimiento y muestra su
        estado calculado (Cobrado, Pendiente, Vencido…), facturado, cobrado, pendiente, vencimiento y fecha de pago."""
        registrar(f"facturacion_buscar({texto})")
        fx = _fx()
        t = _norm(texto)
        salida: dict[str, Any] = {}
        for emp in _empresas(empresa, disponibles):
            x = base(emp)
            campos = (x["_paciente"] + " " + x["_numero_factura"] + " " + x["_obra_social"] + " " + x["_medico"]
                      + " " + x["_procedimiento"]).map(_norm)
            hallados = x[campos.str.contains(re.escape(t), na=False)] if t else x.iloc[0:0]
            salida[emp] = {"encontradas": int(len(hallados)), "filas": _tabla(fx._detalle_registros(hallados), limite)}
        return _json(salida)

    def facturacion_calidad(empresa: str = "ambas", periodo: str = "Todo el historial") -> str:
        """Controles de calidad de la planilla (pestaña Calidad): filas sin importe, sin mes, sin N° de factura,
        facturas repetidas, pendientes sin vencimiento, cobradas sin fecha de pago, sin obra social, sin
        procedimiento. Devuelve cuántas hay de cada una y ejemplos."""
        registrar(f"facturacion_calidad({empresa})")
        fx = _fx()
        salida: dict[str, Any] = {}
        for emp in _empresas(empresa, disponibles):
            x, etiqueta = filtrar(base(emp), periodo=periodo, hoy=hoy)
            mascaras = fx._mascaras_calidad(x)
            activos = x[~x["_cancelado"]]
            salida[emp] = {"periodo": etiqueta, "controles": {
                nombre: {"cantidad": int(m.sum()), "que_es": fx._DESCRIPCION_CONTROLES.get(nombre, ""),
                         "ejemplos": _tabla(fx._detalle_registros(activos[m]), 5)}
                for nombre, m in mascaras.items() if int(m.sum())}}
        return _json(salida)

    def facturacion_control(empresa: str = "ambas", limite: int = 25) -> str:
        """La bandeja de control (sección Control): qué hay que hacer hoy, por urgencia. Cirugías realizadas en la
        Agenda sin cargar en facturación, registros sin facturar (plazo de presentación de 60 días desde la
        prestación: vencido o por vencer), facturas vencidas o por vencer, débitos para reclamar, cobros parciales
        sin débito anotado, facturas repetidas y datos incompletos. Usala para «¿qué tengo que hacer?», «¿qué se me
        está pasando?» o el resumen de la mañana."""
        registrar(f"facturacion_control({empresa})")
        from vitae.modulos import control_facturacion as cf

        salida: dict[str, Any] = {}
        pedidas = _empresas(empresa, disponibles)
        agenda = None
        if "Agenda Quirófano" in disponibles:
            try:
                agenda = datos.df("Agenda Quirófano")
            except Exception:
                agenda = None
        for emp in pedidas:
            # Las cirugías sin cargar de cada empresa son sólo las de sus procedimientos.
            otras = [datos.df(EMPRESAS[o]) for o in EMPRESAS if o != emp and EMPRESAS[o] in disponibles]
            r = cf.resumen(datos.df(EMPRESAS[emp]), agenda, otras, hoy.date() if hoy is not None else None,
                           empresa=emp)
            salida[emp] = {
                "urgentes": r["urgentes"], "esta_semana": r["esta_semana"], "sin_facturar": r["sin_facturar"],
                "sin_facturar_plazo_vencido": r["sin_facturar_vencido"], "cirugias_sin_cargar": r["agenda_sin_cargar"],
                "debitos_abiertos": _r(r["debitos"]), "facturas_vencidas": r["vencidas"],
                "controles": _tabla(r["controles"].drop(columns=["_clave", "_nivel", ""], errors="ignore"), 20),
                "bandeja": _tabla(r["bandeja"].drop(columns=["_fila"], errors="ignore"), limite),
            }
        if not salida:
            return _json({"error": "No tenés acceso a Facturación VMR ni VM."})
        return _json(salida)

    return [facturacion_resumen, facturacion_agrupado, facturacion_pendientes, facturacion_buscar,
            facturacion_calidad, facturacion_control]
