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
