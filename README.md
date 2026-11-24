# Vitae – Sistema de Gestión

Aplicación interna de gestión para Vitae, hecha en **Streamlit**. Usa **Google Sheets** como base de datos y **Gemini** para el asistente con IA.

## Estructura

```
app.py                          Punto de entrada (streamlit run app.py)
vitae/
  vistas.py                     Dashboard, login/usuarios, configuración y vistas generales
  core/
    config.py                   Configuración y lectura de secretos
    database.py                 Acceso a Google Sheets
    tablas.py                   Definición de tablas y campos (MODULES)
    helpers.py                  Utilidades: fechas, montos, filtros
    importadores.py             Importación de planillas
  modulos/
    facturacion.py              Facturación: KPIs, aging, pronóstico, exportación
    farmacia.py                 Farmacia: stock quirófano/farmacia y movimientos
    quirofano.py                Agenda de quirófano
  ia/
    copiloto.py                 Copiloto Vitae (preguntas a la IA)
    copiloto_acceso_total.py    Amplía el acceso del copiloto a todos los módulos
    director.py                 Director IA / briefing ejecutivo
assets/logo_vitae.png
plantillas/                     Encabezados de planillas para importar
```

## Correr en local

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # y completar
streamlit run app.py
```

## Secretos necesarios

Ver `.streamlit/secrets.toml.example`:

- `GOOGLE_SHEET_ID` – planilla que funciona como base de datos.
- `GOOGLE_SERVICE_ACCOUNT_JSON` – credenciales de la cuenta de servicio (debe tener acceso de editor a la planilla).
- `GEMINI_API_KEY` – clave para el asistente con IA (opcional; sin ella la IA queda deshabilitada).
- `GEMINI_MODEL` – opcional.

También pueden definirse como variables de entorno con el mismo nombre.

## Deploy

Pensado para **Streamlit Community Cloud**: archivo principal `app.py`, Python 3.11 (`runtime.txt`), secretos cargados en *Settings → Secrets*.
