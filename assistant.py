from openai import OpenAI

import pandas as pd

from config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)

def preguntar_ia(modulo: str, df: pd.DataFrame, pregunta: str) -> str:

    """

    IA de solo lectura para un módulo de Vitae.

    """

    datos = df.head(100).to_string(index=False)

    prompt = f"""

Sos el asistente del sistema Vitae.

Estás dentro del módulo:

{modulo}

Solo podés responder utilizando la información de este módulo.

Estos son los datos:

{datos}

Pregunta del usuario:

{pregunta}

Respondé en español, de forma clara y concreta.

"""

    respuesta = client.responses.create(

        model="gpt-4.1-mini",

        input=prompt,

    )

    return respuesta.output_text
