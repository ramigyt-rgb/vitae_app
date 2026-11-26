"""Luz, experta en Caja VMR y Caja VM.

Consultas con las MISMAS funciones del módulo de Caja (vitae/modulos/caja.py):
saldo inicial (cargado o el cierre del mes anterior), entradas, salidas y
saldo por mes, caja en pesos y en dólares por separado, rubros (General,
Alquileres, Caja gastos comunes), movimientos frecuentes y el saldo de hoy
(el mismo que muestra el Panel general).

Sólo se ofrecen las cajas que el usuario puede abrir.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from typing import Any, Callable

import pandas as pd

CAJAS = {"VMR": ("Caja VMR", "caja_vmr"), "VM": ("Caja VM", "caja_vm")}


def _norm(v: Any) -> str:
    t = unicodedata.normalize("NFKD", str(v if v is not None else "").strip().lower())
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c)))


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def caja_de(texto: str) -> str | None:
    e = re.sub(r"[^a-z]", "", _norm(texto)).replace("caja", "")
    if e in {"vmr", "reproductiva", "medicinareproductiva", "vitaemedicinareproductiva"}:
        return "VMR"
    if e in {"vm", "medical", "vitaemedical"}:
        return "VM"
    return None


def cajas_pedidas(texto: str, disponibles: dict[str, str]) -> list[str]:
    una = caja_de(texto)
    pedidas = [una] if una else ["VMR", "VM"]
    return [c for c in pedidas if CAJAS[c][0] in disponibles]


def preparar(raw: pd.DataFrame, caja: str) -> pd.DataFrame:
    from vitae.modulos import caja as cj

    return cj.preparar(raw, dolar_por_texto=CAJAS[caja][1] == "caja_vmr")


def mes_de(texto: str, hoy: date | None = None) -> str:
    """«2026-11», «11/2026», «mes actual», «mes anterior» o vacío (= mes actual) → «AAAA-MM»."""
    hoy = hoy or date.today()
    actual = pd.Period(hoy, freq="M")
    t = _norm(texto)
    if not t or t in {"mes actual", "este mes", "hoy", "actual"}:
        return str(actual)
    if t in {"mes anterior", "mes pasado", "el mes pasado"}:
        return str(actual - 1)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", t) or re.fullmatch(r"(\d{1,2})[/-](\d{4})", t)
    if m:
        anio, mm = (int(m[1]), int(m[2])) if len(m[1]) == 4 else (int(m[2]), int(m[1]))
        return f"{anio}-{mm:02d}"
    return str(actual)


def _r(x: Any) -> float:
    return round(float(x or 0.0), 2)


def resumen_caja(d: pd.DataFrame, mes: str, tabla: str = "") -> dict[str, Any]:
    from vitae.modulos import caja as cj

    internas = cj.cajas_internas(tabla)
    if len(internas) > 1:
        return resumen_con_internas(d, mes, tabla, internas)
    out: dict[str, Any] = {"mes": mes}
    for moneda in ("ARS", "USD"):
        r = cj.resumen_mes(d, mes, moneda)
        hoy = pd.Timestamp.today().normalize() + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        lb = cj.libro(d, moneda, hasta=hoy)
        actual = float(lb["saldo"].iloc[-1]) if not lb.empty else 0.0
        bloque = {"saldo_inicial": _r(r["inicial"]), "entradas": _r(r["entradas"]), "salidas": _r(r["salidas"]),
                  "saldo_del_mes": _r(r["saldo"]), "movimientos": int(r["movimientos"]), "saldo_hoy": _r(actual),
                  "texto": (f"Inicial {cj.dinero(r['inicial'], moneda)} · entradas {cj.dinero(r['entradas'], moneda)} · "
                            f"salidas {cj.dinero(r['salidas'], moneda)} · saldo {cj.dinero(r['saldo'], moneda)}")}
        if moneda == "ARS":
            bloque["por_rubro"] = {k: {kk: (_r(vv) if kk != "n" else vv) for kk, vv in v.items()}
                                   for k, v in cj.por_rubro(d, mes, moneda).items()}
            lbm = cj.libro(d, moneda)
            dif = lbm[(lbm["mes"] == mes) & (lbm["diferencia"].abs() > 0.009)]
            if not dif.empty:
                x = dif.iloc[0]
                bloque["aviso_saldo_inicial"] = (
                    f"El saldo inicial cargado ({cj.pesos(x['inicial_cargado'])}) no coincide con el cierre del mes "
                    f"anterior ({cj.pesos(x['inicial_calculado'])}): diferencia {cj.pesos(x['diferencia'])}.")
        if moneda == "USD" and not r["movimientos"] and abs(r["inicial"]) < 0.005 and abs(actual) < 0.005:
            continue
        out[moneda] = bloque
    sf = int((d["_mes"] == cj.SIN_FECHA).sum()) if not d.empty else 0
    if sf:
        out["movimientos_sin_fecha"] = sf
    return out


def resumen_con_internas(d: pd.DataFrame, mes: str, tabla: str, internas: list[str]) -> dict[str, Any]:
    """Caja con cajas internas (Caja VMR): total y reparto por caja, con disponible y frascos."""
    from vitae.modulos import caja as cj

    out: dict[str, Any] = {"mes": mes, "nota": ("El total es la suma de las cajas internas. Las transferencias entre "
                                               "ellas no son entradas ni salidas del total; los frascos no cambian "
                                               "saldos, sólo cuánto queda disponible.")}
    hasta = cj._hasta_mes(mes)
    for moneda in ("ARS", "USD"):
        tot = cj.resumen_total(d, mes, moneda, internas)
        hoy_total = sum(cj.saldo_hoy(cj.de_caja(d, c), moneda) for c in internas)
        if moneda == "USD" and not tot["movimientos"] and abs(tot["inicial"]) < 0.005 and abs(hoy_total) < 0.005:
            continue
        bloque: dict[str, Any] = {
            "total": {"saldo_inicial": _r(tot["inicial"]), "entradas": _r(tot["entradas"]), "salidas": _r(tot["salidas"]),
                      "saldo_del_mes": _r(tot["saldo"]), "saldo_hoy": _r(hoy_total)},
            "cajas_internas": {}}
        for c in internas:
            r = tot["por_caja"][c]
            fr = cj.frascos(d, c, tabla, hasta, moneda)
            info: dict[str, Any] = {
                "saldo_inicial": _r(r["inicial"]), "entradas": _r(r["entradas_propias"]),
                "salidas": _r(r["salidas_propias"]), "transferencias_recibidas": _r(r["transf_entrada"]),
                "transferencias_enviadas": _r(r["transf_salida"]), "saldo_del_mes": _r(r["saldo"]),
                "saldo_hoy": _r(cj.saldo_hoy(cj.de_caja(d, c), moneda)),
                "porcentaje_del_total": _r(r["saldo"] / tot["saldo"] * 100) if abs(tot["saldo"]) > 0.004 else 0.0}
            if fr:
                info["frascos"] = {k: _r(v) for k, v in fr.items()}
                info["disponible"] = _r(r["saldo"] - sum(fr.values()))
            bloque["cajas_internas"][c] = info
        if moneda == "ARS":
            ef = cj.de_caja(d, cj.EFECTIVO)
            bloque["por_rubro_efectivo"] = {k: {kk: (_r(vv) if kk != "n" else vv) for kk, vv in v.items()}
                                            for k, v in cj.por_rubro(ef, mes, moneda).items()}
            avisos = []
            for c in internas:
                lb = cj.libro(cj.de_caja(d, c), moneda)
                dif = lb[(lb["mes"] == mes) & (lb["diferencia"].abs() > 0.009)]
                if not dif.empty:
                    avisos.append(f"{c}: el saldo inicial cargado no coincide con el cierre del mes anterior "
                                  f"(diferencia {cj.pesos(dif.iloc[0]['diferencia'])}).")
            if avisos:
                bloque["avisos"] = avisos
        out[moneda] = bloque
    sf = int((d["_mes"] == cj.SIN_FECHA).sum()) if not d.empty else 0
    if sf:
        out["movimientos_sin_fecha"] = sf
    return out


def movimientos(d: pd.DataFrame, *, mes: str = "", tipo: str = "", rubro: str = "", buscar: str = "",
                moneda: str = "", limite: int = 40, caja_interna: str = "") -> list[dict[str, Any]]:
    x = d[~d["_inicial"]] if not d.empty else d
    if caja_interna and "_cuenta" in x.columns:
        from vitae.modulos import caja as cj

        x = x[x["_cuenta"] == cj.normalizar_cuenta(caja_interna)]
    if mes:
        x = x[x["_mes"] == mes]
    t = _norm(tipo)
    if t.startswith("entr") or t.startswith("ingr"):
        x = x[x["_entrada"] > 0]
    elif t.startswith("sal") or t.startswith("egr") or t.startswith("gast"):
        x = x[x["_salida"] > 0]
    if rubro:
        x = x[x["_rubro"].map(_norm).str.contains(re.escape(_norm(rubro)), na=False)]
    if moneda:
        x = x[x["_moneda"] == moneda.upper()]
    b = _norm(buscar)
    if b:
        texto = (x["_concepto"] + " " + x["_quien"] + " " + x["_obs"]).map(_norm)
        x = x[texto.str.contains(re.escape(b), na=False)]
    x = x.sort_values(["_fecha", "_orden"], na_position="last")
    filas = []
    for _, f in x.tail(max(1, min(int(limite or 40), 300))).iterrows():
        filas.append({"fecha": f["_fecha"].strftime("%d/%m/%Y") if pd.notna(f["_fecha"]) else "sin fecha",
                      "descripcion": f["_concepto"], "entrada": _r(f["_entrada"]) or None,
                      "salida": _r(f["_salida"]) or None, "moneda": f["_moneda"], "rubro": f["_rubro"],
                      "quien": f["_quien"], "medio": f["_medio"], "observaciones": f["_obs"],
                      "caja_interna": f.get("_cuenta", "Efectivo"), "frasco": f.get("_frasco", ""),
                      "es": ("frasco (no cambia saldo)" if f.get("_es_frasco") else
                             "transferencia entre cajas" if f.get("_transferencia") else "movimiento")})
    return filas


INSTRUCCIONES = """EXPERTA EN CAJA VMR Y CAJA VM (usá SIEMPRE las herramientas caja_*)
- caja_resumen da, para un mes: saldo inicial, entradas, salidas, saldo del mes y saldo de hoy, en pesos y (si hay)
  en dólares, más lo que entró y salió por rubro. Es lo mismo que muestra la pantalla de Caja y el Panel general.
- caja_movimientos lista los movimientos (filtrás por mes, entradas/salidas, rubro, moneda o texto);
  caja_historial da el libro mes a mes; caja_frecuentes, los movimientos que más se repiten.
- Definiciones: cada mes arranca con un SALDO INICIAL (si está cargado como fila «SALDO INICIAL» se usa ése;
  si no, el cierre del mes anterior). Saldo = inicial + entradas − salidas. El saldo inicial NO es una entrada.
  Pesos y dólares son cajas separadas: nunca los sumes. En Caja VMR, un texto con «dólar» es un movimiento en USD.
- Rubros: General, Alquileres y Caja gastos comunes (se colorean en la planilla). «Quién» es el responsable.
- Si el saldo inicial cargado no coincide con el cierre del mes anterior, avisalo. Si hay movimientos sin fecha,
  también (se corrigen en Editar).
- La pantalla de Caja siempre abre en el mes actual. Secciones: Planilla, Cargar movimiento, Editar, Historial e
  Importar.
- Caja VMR por dentro tiene 3 cajas internas: Efectivo (la de siempre), Mercado Pago y Medicación, cada una con sus
  movimientos y su saldo; el total de VMR es la suma. Las transferencias entre cajas internas no son entradas ni
  salidas del total. Los frascos (p. ej. Reserva en Mercado Pago) apartan plata sin que salga: el saldo no cambia,
  cambia lo «disponible». caja_resumen de VMR trae el total, cada caja interna con su % del total, y sus frascos."""


def herramientas(datos: Any, hoy: date | None = None) -> list[Callable[..., str]]:
    disponibles = datos.modulos()
    if not any(n in disponibles for n, _ in CAJAS.values()):
        return []
    cache: dict[str, pd.DataFrame] = {}

    def base(c: str) -> pd.DataFrame:
        if c not in cache:
            cache[c] = preparar(datos.df(CAJAS[c][0]), c)
        return cache[c]

    def registrar(texto: str) -> None:
        try:
            datos.consultas.append(texto)
        except Exception:
            pass

    def caja_resumen(caja: str = "ambas", mes: str = "") -> str:
        """Resumen de la caja en un mes (vacío = mes actual; «mes anterior» o AAAA-MM): saldo inicial, entradas,
        salidas, saldo del mes, saldo de hoy, en pesos y dólares por separado, y entradas/salidas por rubro.
        caja: VMR, VM o ambas."""
        registrar(f"caja_resumen({caja}, {mes})")
        m = mes_de(mes, hoy)
        salida = {c: resumen_caja(base(c), m, CAJAS[c][1]) for c in cajas_pedidas(caja, disponibles)}
        return _json(salida or {"error": "No tenés acceso a Caja VMR ni Caja VM."})

    def caja_movimientos(caja: str = "ambas", mes: str = "", tipo: str = "", rubro: str = "", buscar: str = "",
                         moneda: str = "", limite: int = 40, caja_interna: str = "") -> str:
        """Movimientos de la caja (sin las filas de saldo inicial). mes: vacío = mes actual, «todos» = todo.
        tipo: entradas o salidas. rubro: General, Alquileres o Caja gastos comunes. moneda: ARS o USD.
        buscar: texto en descripción, quién u observaciones. caja_interna (Caja VMR): Efectivo, Mercado Pago o
        Medicación."""
        registrar(f"caja_movimientos({caja}, {mes}, {buscar})")
        m = "" if _norm(mes) in {"todos", "todo", "todo el historial"} else mes_de(mes, hoy)
        return _json({c: movimientos(base(c), mes=m, tipo=tipo, rubro=rubro, buscar=buscar, moneda=moneda,
                                     limite=limite, caja_interna=caja_interna) for c in cajas_pedidas(caja, disponibles)})

    def caja_historial(caja: str = "ambas", moneda: str = "ARS", ultimos: int = 12) -> str:
        """Libro mes a mes: saldo inicial, entradas, salidas, saldo y cantidad de movimientos; marca si el saldo
        inicial cargado no coincide con el cierre del mes anterior. moneda: ARS o USD."""
        from vitae.modulos import caja as cj

        registrar(f"caja_historial({caja}, {moneda})")
        salida = {}
        for c in cajas_pedidas(caja, disponibles):
            internas = cj.cajas_internas(CAJAS[c][1])
            mon = (moneda or "ARS").upper()
            lb = (cj.libro_total(base(c), mon, internas) if len(internas) > 1 else cj.libro(base(c), mon)
                  ).tail(max(1, min(int(ultimos or 12), 60)))
            salida[c] = [{k: (v if k == "mes" or k == "movimientos" else (_r(v) if v is not None else None))
                          for k, v in f.items()} for f in lb.to_dict("records")]
        return _json(salida)

    def caja_frecuentes(caja: str = "ambas") -> str:
        """Los movimientos que más se repiten (descripción, entrada/salida, rubro, quién, moneda e importe de la
        última vez): sirven para cargar rápido y escribir las descripciones igual que siempre."""
        from vitae.modulos import caja as cj

        registrar(f"caja_frecuentes({caja})")
        return _json({c: cj.frecuentes(base(c), 25) for c in cajas_pedidas(caja, disponibles)})

    return [caja_resumen, caja_movimientos, caja_historial, caja_frecuentes]
