from google import genai

import pandas as pd

from config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

def preguntar_ia(modulo: str, df: pd.DataFrame, pregunta: str) -> str:

    datos = df.head(1000).to_string(index=False)

    prompt = f"""

Sos el asistente del sistema Vitae.

Módulo:

{modulo}

Datos:

{datos}

Pregunta:

{pregunta}

Respondé únicamente usando la información disponible.

"""

    respuesta = client.models.generate_content(

        model="gemini-3.5-flash-lite",

        contents=prompt,

    )

    return respuesta.text
def preguntar_dashboard(data: dict, pregunta: str) -> str:

    contexto = ""

    for nombre, df in data.items():

        if not df.empty:

            contexto += f"\n\n===== {nombre.upper()} =====\n"

            contexto += df.head(1000).to_string(index=False)

    prompt = f"""

Sos el Director General de Vitae.

Tenés acceso a TODOS los módulos del sistema.

Podés relacionar información entre ellos.

Reglas:

- Respondé únicamente con la información disponible.

- Si un dato no existe, decilo.

- Si necesitás combinar módulos, hacelo.

- Contestá de forma clara y profesional.

Información del sistema:

{contexto}

Pregunta:

{pregunta}

"""

    respuesta = client.models.generate_content(

        model="gemini-3.5-flash-lite",

        contents=prompt,

    )

    return respuesta.text
