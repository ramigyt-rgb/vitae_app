# CLAUDE.md — Vitae

Guía para Claude Code. Resume cómo trabajamos con Ramiro y todo lo que se decidió en el desarrollo
de Vitae. Leela entera antes de tocar código.

## Qué es

Sistema interno de gestión de **Vitae** (Salta, Argentina): **VMR** = Vitae Medicina Reproductiva,
**VM** = Vitae Medical. Streamlit 1.60 + **Google Sheets como base de datos** (gspread) + **Gemini**
(google-genai) para la IA. Se publica en Streamlit Cloud desde `main` del repo
`ramigyt-rgb/vitae_app` (**repo público**: nada de datos ni claves en el código).

```
streamlit run app.py
```
Secrets (`.streamlit/secrets.toml`, nunca al repo): `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SHEET_ID`,
`GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_MODEL_AVANZADO`, `GINE_FORMULARIO_URL`, y opcionales
`LUZ_USUARIOS`, `LUZ_CLAVE` / `LUZ_CLAVE_HASH`, `PACIENTES_USUARIOS`, `PACIENTES_SHEET_ID`.

## Cómo trabajar con Ramiro (reglas)

- Hablar en **castellano rioplatense**, simple, sin tecnicismos. Ramiro decide; explicar qué cambia
  para él, no cómo está hecho por dentro.
- **Una rama por pedido → PR → merge SÓLO cuando Ramiro dice «mergealo»** (o lo pide explícitamente
  en el mismo mensaje: «dejalo listo y mergealo»). Nunca pushear a `main` directo.
- Commits como `ramigyt-rgb <ramigyt-rgb@users.noreply.github.com>`.
- No commitear `assets/logo_vitae_icono.png` (archivo suelto que no va) ni planillas de datos
  (`.gitignore` ya bloquea `*Personas*.xls*`, `*pacientes*`).
- Antes de cerrar: correr las pruebas (ver abajo), y si cambia algo visible, mostrar una vista previa.
- Cuando Ramiro manda capturas de su planilla, **el módulo tiene que quedar igual a la planilla** y
  los números tienen que dar exacto (verificarlo con los valores de la captura).

## Principios de la app

1. **Una sola cuenta por número**: el Panel general usa las MISMAS funciones que cada módulo
   (`vitae/modulos/panel.py`). Si el Panel y un módulo dicen cosas distintas, es un error.
2. **Leer las planillas como vienen del borrador** (Excel exportado de Sheets): respetar lo visible
   (filas/columnas ocultas), los colores, las celdas combinadas y las fórmulas pegadas (hay un
   evaluador en `cuenta_corriente._Libro`).
3. Dólares nunca se suman a pesos.
4. Datos sensibles fuera del alcance de la IA (ver Pacientes).

## Mapa del código

- `app.py`: menú (grupos en `vitae/core/navegacion.py`), login, página Pacientes aparte.
- `vitae/vistas.py` (~25k líneas, legado): login/permisos, dispatch de módulos en
  `render_facturacion_pro` (ahí se deriva a los módulos nuevos), Luz, Pacientes, tablero viejo
  (`total_mod`).
- `vitae/core/database.py`: `get_df(tabla)` (caché; precarga juntas todas las tablas de `MODULES`),
  `sync_df_to_sheet(tabla, df, limpiar_cache=True)` reescribe la pestaña entera.
- `vitae/core/tablas.py`: `MODULES` (cada módulo → pestaña).
- Módulos nuevos (limpios, con pruebas):
  - `modulos/caja.py`: **Caja VM, Caja VMR y Banco Galicia VM** (misma planilla mensual: saldo
    inicial, entradas, salidas, saldo, rubros por color —verde Alquileres, salmón Caja gastos
    comunes—, «Quién»/«Observación»). Caja VMR tiene 3 cajas internas (Efectivo, Mercado Pago,
    Medicación) y frascos (apartan plata sin que salga). **Saldo único: `saldo_planilla`** = SALDO de
    la planilla del mes (incluye lo cargado con fecha posterior a hoy; los sin fecha no suman).
    Al importar, si la planilla tiene colores manda el color (blanco = General).
  - `modulos/cuenta_corriente.py`: Cuenta Corriente VMR/VM (sólo celdas visibles, sincroniza con el
    borrador, `dejar_como_planilla`, «todo lo anterior está pagado»).
  - `modulos/planes.py`: Planes de pagos y préstamos como «DEUDAS ASUMIDAS MES A MES»: una fila por
    cuota; bloques MEDICAL / ADRIAN EN CTA DE MEDICAL → VM, REPRODUCTIVA → VMR; verde pagada, rojo
    impaga. Totales como la columna TOTAL de la planilla = todas las cuotas desde 3 meses atrás
    (VMR verificado: $ 37.764.555,92). Importa filas ocultas (planes terminados: no se muestran en
    la grilla pero suman en TOTALES).
  - `modulos/pagos_pendientes.py`, `deudas_impositivas.py`, `facturacion.py`, `panel.py`.
  - `modulos/pacientes.py` + `pacientes_analisis.py`: ver Pacientes.
- `vitae/ia/`: Luz (ver abajo).

## Luz (asistente / secretaria de Ramiro)

- Chat flotante tipo Messenger (`ia/asistente.py`), motor Gemini (`ia/motor.py`). **Sólo la ve
  Ramiro Char** (`LUZ_USUARIOS`). Será la jefa de futuras «secretarias» por área.
- Expertas: `experta_facturacion.py`, `experta_caja.py`. Acciones con confirmación
  (`acciones.py`): agendar cirugías, registrar/modificar facturación, cargar/corregir caja,
  transferencias y frascos. `editora.py`: alta/modificación/baja en cualquier módulo permitido.
- **Clave de Luz**: cambios libres y borrados piden clave en la tarjeta. Sólo está el hash PBKDF2 en
  `ia/clave.py` (repo público); se cambia con `LUZ_CLAVE` / `LUZ_CLAVE_HASH`. Si se escribe en el
  chat se tapa y nunca va a la IA, al historial ni a la memoria.
- **Memoria** (`ia/memoria.py`): pestañas `luz_memoria` y `luz_chat`, por usuario; aprende al tocar
  ✎; nunca guarda claves ni datos clínicos.
- La IA **nunca** lee `usuarios_vitae`, `pacientes`, `pacientes_accesos` (`motor._TABLAS_PROHIBIDAS`).

## Pacientes (base institucional, datos sensibles)

- 59.512 personas importadas del sistema de turnos (`GP_Personas_ABM`). **Es INSTITUCIONAL: no se
  cruza con Facturación, Quirófano ni ninguna otra área** (decisión explícita de Ramiro).
- **Sólo lo ve el usuario exacto `rchar`** (`PACIENTES_USUARIOS`), ni siquiera otros admins. Está
  FUERA de `MODULES` (ni precarga, ni Panel, ni IA). Caché y escritura propias (RAW, por tandas).
- Ley 25.326 (datos sensibles) y 26.529: sólo datos administrativos (las observaciones libres no se
  importan), sin listados completos, registro de accesos en `pacientes_accesos`, exportaciones
  anonimizadas. Recomendado: archivo de Sheets aparte (`PACIENTES_SHEET_ID`), Gemini pago antes de
  darle algo de pacientes a la IA, y consulta con abogado (AAIP).
- Inteligencia: demografía, etapas de vida, canales, documentación, edad estimada por número de DNI
  (asignación cronológica; error mediano 0,2 años), investigación. Nada sale de la app.

## Pruebas

Se corren con un Streamlit simulado (no hace falta Streamlit ni Google):
`python3 <prueba>.py <raiz_del_repo>`. La batería está en una carpeta aparte (no en el repo, porque
algunas usan datos reales de las planillas y el repo es público): pedírsela a Ramiro o, si el repo
pasa a privado, sumarla en `pruebas/`. Regresión: `test_todos_modulos.py <raiz> <salida.json>`
abre los 25 módulos. `test_guardado.py` ya fallaba antes (diagnóstico de permisos), no es nuevo.

## Pendiente

- Banco Macro VMR igual que Banco Galicia VM (esperar la planilla de Ramiro).
- Planes: verificar con el .xlsx real el TOTAL de VM ($ 5.781.897,15; con las capturas da
  $ 4.635.612,02 → la fórmula de BQ15 probablemente suma meses ocultos) y los préstamos
  ($ 36.395.131,37 = $ 12.517.349,14 + $ 23.877.782,23, no figuran en las capturas); revisar el bloque
  ADELANTO DE CUOTAS con el archivo.
- Cuenta corriente VM con su planilla.
- Más acciones de Luz (cuenta corriente, pagos pendientes) y las secretarias por área.
- Considerar pasar el repo a privado.
