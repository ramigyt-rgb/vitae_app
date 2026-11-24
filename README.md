# Vitae – Sistema de Gestión

Aplicación interna de gestión para Vitae, hecha en **Streamlit**. Usa **Google Sheets** como base de datos y **Gemini** para el asistente con IA.

## Módulos

| Área | Archivo principal |
|---|---|
| Inicio / dashboard, login, configuración y vistas generales | `views.py` |
| Facturación (KPIs, aging, pronóstico, exportación) | `facturacion_ultra_pro.py` |
| Farmacia | `farmacia_pro.py` |
| Agenda de quirófano | `agenda_quirofano_ultra_pro.py` |
| Copiloto Vitae (preguntas a la IA) | `assistant.py`, `assistant_full_access_patch.py` |
| Director IA / briefing ejecutivo | `director_ia.py` |
| Definición de tablas y campos | `modules.py` |
| Acceso a Google Sheets | `database.py` |
| Importación de planillas | `importers.py` |
| Utilidades (fechas, montos, filtros) | `helpers.py` |
| Configuración y secretos | `config.py` |

`app.py` es el punto de entrada.

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
