"""Memoria de Luz: lo que va aprendiendo de cada usuario y de Vitae.

- Se guarda en la pestaña ``luz_memoria`` de Google Sheets (una fila por
  recuerdo: tipo, texto, de quién es, si está activo). Cada respuesta de Luz
  arranca leyendo su memoria: preferencias, reglas del negocio, correcciones,
  datos de proveedores o personas y pendientes que le pidieron recordar.
- Aprende de tres maneras: cuando se lo piden («acordate de…»), sola cuando
  la corrigen o aparece un dato que sirve para siempre (herramienta
  ``recordar``), y al cerrar una conversación, repasándola para anotar lo que
  valga la pena (``aprender_de_conversacion``).
- No duplica: si ya sabía algo parecido, lo actualiza.
- El usuario la ve y la corrige desde el chat (🧠): borrar o agregar recuerdos.
- La conversación también se guarda (pestaña ``luz_chat``, las últimas por
  usuario), así que no se pierde al recargar la página.

La memoria se lee una vez por sesión y se escribe al cambiar: sin borrar el
resto de los datos cargados de la app (la app sigue rápida).
"""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from datetime import datetime
from typing import Any, Callable

import pandas as pd

TABLA_MEMORIA = "luz_memoria"
TABLA_CHAT = "luz_chat"
COLUMNAS = ["id", "creado", "actualizado", "usuario", "tipo", "texto", "origen", "activa"]
COLUMNAS_CHAT = ["usuario", "fecha", "rol", "texto"]
TIPOS = {
    "preferencia": "Preferencia",
    "regla": "Regla / forma de trabajar",
    "dato": "Dato",
    "persona": "Persona / proveedor",
    "pendiente": "Pendiente",
    "aprendizaje": "Aprendizaje",
}
MAX_RECUERDOS_PROMPT = 80
MAX_CHAT_POR_USUARIO = 60
TODOS = "todos"


def _norm(v: Any) -> str:
    t = unicodedata.normalize("NFKD", str(v if v is not None else "").strip().lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9$%/., -]+", " ", re.sub(r"\s+", " ", t)).strip()


def _texto(v: Any) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v).strip()


def _ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _tipo(v: Any) -> str:
    t = _norm(v)
    for k in TIPOS:
        if t.startswith(k[:5]):
            return k
    if t.startswith(("correc", "como se", "forma")):
        return "regla"
    if t.startswith(("proveed", "pacient", "medic", "contact")):
        return "persona"
    if t.startswith(("record", "tarea", "hacer")):
        return "pendiente"
    return "dato"


def parecido(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


class Memoria:
    """Memoria de un usuario (más los recuerdos para todos). ``leer(tabla)`` y ``guardar(tabla, df)``
    son las funciones de Google Sheets; ``estado`` (st.session_state) guarda la copia de la sesión."""

    def __init__(self, usuario: str, leer: Callable[[str], pd.DataFrame],
                 guardar: Callable[[str, pd.DataFrame], Any], estado: dict | None = None):
        self.usuario = _texto(usuario) or "sin usuario"
        self._leer, self._guardar = leer, guardar
        self._estado = estado if estado is not None else {}
        self._clave = f"luz_memoria_df::{self.usuario}"

    # ------------------------------------------------------------ recuerdos
    def _df(self) -> pd.DataFrame:
        clave = self._clave
        if clave not in self._estado:
            try:
                df = self._leer(TABLA_MEMORIA)
            except Exception:
                df = pd.DataFrame()
            df = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
            for c in COLUMNAS:
                if c not in df.columns:
                    df[c] = ""
            self._estado[clave] = df[COLUMNAS + [c for c in df.columns if c not in COLUMNAS]].fillna("").astype(str)
        return self._estado[clave]

    def _escribir(self, df: pd.DataFrame) -> None:
        self._estado[self._clave] = df
        self._guardar(TABLA_MEMORIA, df)

    def recuerdos(self, incluir_inactivos: bool = False) -> list[dict[str, str]]:
        df = self._df()
        mios = df[df["usuario"].isin([self.usuario, TODOS])]
        if not incluir_inactivos:
            mios = mios[mios["activa"].str.upper() != "NO"]
        return mios.to_dict("records")

    def agregar(self, texto: str, tipo: str = "dato", origen: str = "chat", para_todos: bool = False
                ) -> tuple[str, dict[str, str]]:
        """Anota un recuerdo. Si ya había uno muy parecido, lo actualiza. Devuelve (qué pasó, recuerdo)."""
        texto = re.sub(r"\s+", " ", _texto(texto))[:500]
        if len(texto) < 4:
            raise ValueError("No hay nada para recordar.")
        try:
            from vitae.ia.clave import tapar

            if tapar(texto) != texto:
                raise ValueError("Las claves no se guardan en la memoria.")
        except ImportError:  # pragma: no cover
            pass
        df = self._df().copy()
        tipo = _tipo(tipo)
        activos = df[(df["usuario"].isin([self.usuario, TODOS])) & (df["activa"].str.upper() != "NO")]
        if len(activos):
            sims = activos["texto"].map(lambda t: parecido(t, texto))
            mejor = sims.idxmax()
            if sims[mejor] >= 0.86:
                if re.sub(r"[^a-z0-9]", "", _norm(df.at[mejor, "texto"])) == re.sub(r"[^a-z0-9]", "", _norm(texto)):
                    return "ya lo sabía", df.loc[mejor].to_dict()
                df.at[mejor, "texto"], df.at[mejor, "actualizado"] = texto, _ahora()
                df.at[mejor, "tipo"] = tipo
                self._escribir(df)
                return "actualizado", df.loc[mejor].to_dict()
        nuevo = {"id": f"m{datetime.now():%y%m%d%H%M%S}{len(df):03d}", "creado": _ahora(), "actualizado": _ahora(),
                 "usuario": TODOS if para_todos else self.usuario, "tipo": tipo, "texto": texto, "origen": origen,
                 "activa": "SI"}
        df = pd.concat([df, pd.DataFrame([nuevo])], ignore_index=True).fillna("")
        self._escribir(df)
        return "anotado", nuevo

    def olvidar(self, que: str) -> list[dict[str, str]]:
        """Desactiva el recuerdo con ese id o el que más se parezca a ese texto."""
        df = self._df().copy()
        activos = df[(df["usuario"].isin([self.usuario, TODOS])) & (df["activa"].str.upper() != "NO")]
        if activos.empty:
            return []
        q = _texto(que)
        if q in set(activos["id"]):
            idx = activos.index[activos["id"] == q].tolist()
        else:
            nq = _norm(q)
            contiene = activos.index[activos["texto"].map(lambda t: bool(nq) and nq in _norm(t))].tolist()
            if len(contiene) == 1:
                idx = contiene
            else:
                sims = activos["texto"].map(lambda t: parecido(t, q))
                idx = [sims.idxmax()] if sims.max() >= 0.55 else []
        if not idx:
            return []
        for i in idx:
            df.at[i, "activa"], df.at[i, "actualizado"] = "NO", _ahora()
        self._escribir(df)
        return df.loc[idx].to_dict("records")

    def buscar(self, texto: str, limite: int = 10) -> list[dict[str, str]]:
        t = _norm(texto)
        recs = self.recuerdos()
        if not t:
            return recs[-limite:]
        puntaje = [(max(parecido(r["texto"], texto), 1.0 if t in _norm(r["texto"]) else 0.0), r) for r in recs]
        return [r for p, r in sorted(puntaje, key=lambda x: -x[0]) if p >= 0.35][:limite]

    def bloque_prompt(self) -> str:
        recs = self.recuerdos()
        if not recs:
            return ("MEMORIA: todavía no anotaste nada de este usuario. Cuando aprendas algo que sirva para "
                    "siempre, usá recordar.")
        orden = {"regla": 0, "preferencia": 1, "persona": 2, "pendiente": 3, "dato": 4, "aprendizaje": 5}
        recs = sorted(recs, key=lambda r: (orden.get(r["tipo"], 9), r["actualizado"]), reverse=False)
        if len(recs) > MAX_RECUERDOS_PROMPT:
            recs = recs[:MAX_RECUERDOS_PROMPT]
        lineas = [f"- [{r['tipo']}] {r['texto']}  (id {r['id']}, {r['actualizado'][:10]})" for r in recs]
        return "MEMORIA (lo que ya aprendiste; usalo siempre):\n" + "\n".join(lineas)

    # ------------------------------------------------------------ conversación
    def cargar_chat(self) -> list[dict[str, str]]:
        try:
            df = self._leer(TABLA_CHAT)
        except Exception:
            return []
        if not isinstance(df, pd.DataFrame) or df.empty or "usuario" not in df.columns:
            return []
        mio = df[df["usuario"].astype(str) == self.usuario].tail(MAX_CHAT_POR_USUARIO)
        return [{"rol": _texto(f.get("rol")) or "assistant", "texto": _texto(f.get("texto")),
                 "hora": _texto(f.get("fecha"))[-5:]} for _, f in mio.iterrows() if _texto(f.get("texto"))]

    def guardar_chat(self, mensajes: list[dict[str, Any]]) -> None:
        try:
            df = self._leer(TABLA_CHAT)
        except Exception:
            df = pd.DataFrame()
        df = df if isinstance(df, pd.DataFrame) and "usuario" in df.columns else pd.DataFrame(columns=COLUMNAS_CHAT)
        otros = df[df["usuario"].astype(str) != self.usuario]
        hoy = datetime.now().strftime("%Y-%m-%d")
        mios = pd.DataFrame([{"usuario": self.usuario, "fecha": f"{hoy} {m.get('hora', '')}".strip(),
                              "rol": m.get("rol", ""), "texto": _texto(m.get("texto"))[:3000]}
                             for m in mensajes[-MAX_CHAT_POR_USUARIO:] if _texto(m.get("texto"))],
                            columns=COLUMNAS_CHAT)
        self._guardar(TABLA_CHAT, pd.concat([otros[COLUMNAS_CHAT] if len(otros) else otros, mios],
                                            ignore_index=True).fillna(""))


# ---------------------------------------------------------------------------
# Herramientas para la IA
# ---------------------------------------------------------------------------
INSTRUCCIONES = """MEMORIA (tenés memoria propia y vas aprendiendo)
- Arriba está lo que ya sabés de este usuario y de Vitae: usalo sin que te lo repitan.
- Usá recordar(texto, tipo) cuando:
  · te piden que te acuerdes de algo («acordate de…», «de ahora en más…»);
  · te corrigen («no, eso se carga así», «ese proveedor es en dólares»): anotá la regla correcta (tipo regla);
  · aparece una preferencia (cómo le gusta que le respondas, qué mirar primero) o un dato que va a servir siempre
    (quién es quién, cómo se llama un proveedor, qué significa una sigla).
  Escribí el recuerdo corto, claro y en tercera persona («Ramiro prefiere…», «Los pagos a Divilab son en USD»).
  Contale al usuario en pocas palabras que lo anotaste («📝 Me lo anoto»).
- No anotes: números que cambian todo el tiempo (saldos, totales del día), contraseñas o claves, ni datos
  clínicos de pacientes.
- Si algo de tu memoria ya no es cierto, usá olvidar y, si corresponde, recordá lo nuevo.
- Si la memoria y los datos de la app no coinciden, mandan los datos de la app.
- tipos: preferencia, regla, dato, persona, pendiente, aprendizaje."""


def herramientas(memoria: Memoria) -> list[Callable[..., str]]:
    def recordar(texto: str, tipo: str = "dato") -> str:
        """Anota algo en tu memoria permanente para usarlo en las próximas conversaciones (se guarda solo).
        tipo: preferencia, regla, dato, persona, pendiente o aprendizaje."""
        try:
            que, r = memoria.agregar(texto, tipo, origen="chat")
        except Exception as exc:
            return json.dumps({"resultado": "no se anotó", "motivo": str(exc)}, ensure_ascii=False)
        return json.dumps({"resultado": que, "recuerdo": r.get("texto"), "id": r.get("id")}, ensure_ascii=False)

    def olvidar(que: str) -> str:
        """Borra de tu memoria el recuerdo con ese id o el que más se parezca a ese texto."""
        borrados = memoria.olvidar(que)
        return json.dumps({"olvidados": [b.get("texto") for b in borrados]} if borrados else
                          {"resultado": "no encontré ese recuerdo"}, ensure_ascii=False)

    def buscar_memoria(texto: str = "") -> str:
        """Busca en tu memoria lo que sabés sobre un tema (vacío = lo último que anotaste)."""
        return json.dumps([{k: r.get(k) for k in ("id", "tipo", "texto", "actualizado")} for r in memoria.buscar(texto)],
                          ensure_ascii=False)

    return [recordar, olvidar, buscar_memoria]


# ---------------------------------------------------------------------------
# Aprender al cerrar una conversación
# ---------------------------------------------------------------------------
_PROMPT_APRENDER = """Sos Luz, la asistente virtual de Vitae. Repasá esta conversación con {usuario} y anotá SÓLO lo que
convenga recordar para siempre: preferencias, correcciones que te hicieron, reglas o formas de trabajar del
negocio, datos estables de personas o proveedores, pendientes que te pidieron recordar. Nada de saldos o totales
del día, contraseñas ni datos clínicos de pacientes. Si no hay nada nuevo, devolvé una lista vacía.

Lo que ya sabés (no lo repitas):
{memoria}

Conversación:
{conversacion}

Respondé SOLO un JSON: [{{"tipo": "preferencia|regla|dato|persona|pendiente|aprendizaje", "texto": "..."}}] (máximo 5)."""


def aprender_de_conversacion(mensajes: list[dict[str, Any]], memoria: Memoria,
                             generar: Callable[[str], str]) -> list[dict[str, str]]:
    """Repasa la charla y anota lo que valga la pena. ``generar(prompt) -> texto`` llama a la IA."""
    utiles = [m for m in mensajes if _texto(m.get("texto"))]
    if sum(1 for m in utiles if m.get("rol") == "user") < 2:
        return []
    conversacion = "\n".join(f"{'Usuario' if m.get('rol') == 'user' else 'Luz'}: {_texto(m.get('texto'))[:600]}"
                             for m in utiles[-30:])
    ya = "\n".join(f"- {r['texto']}" for r in memoria.recuerdos()[-60:]) or "(nada todavía)"
    try:
        crudo = generar(_PROMPT_APRENDER.format(usuario=memoria.usuario, memoria=ya, conversacion=conversacion))
        m = re.search(r"\[.*\]", crudo or "", re.S)
        items = json.loads(m.group(0)) if m else []
    except Exception:
        return []
    anotados = []
    for it in items[:5]:
        if not isinstance(it, dict) or not _texto(it.get("texto")):
            continue
        try:
            que, r = memoria.agregar(it["texto"], it.get("tipo", "aprendizaje"), origen="auto")
        except Exception:
            continue
        if que in ("anotado", "actualizado"):
            anotados.append(r)
    return anotados
