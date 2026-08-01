def render_farmacia_pro(
    df_stock,
    df_movimientos=None,
    guardar_stock_callback=None,
    guardar_movimientos_callback=None,
):
    """
    Gestión profesional de Farmacia VITAE.

    Usa un catálogo único de artículos y mantiene dos existencias independientes:
    - Stock Quirófano
    - Stock Farmacia

    Parámetros
    ----------
    df_stock:
        DataFrame principal con el catálogo compartido y ambos stocks.
    df_movimientos:
        DataFrame con el historial de ingresos, consumos, ajustes, transferencias
        y controles diarios.
    guardar_stock_callback:
        Función que recibe el DataFrame completo de stock actualizado.
    guardar_movimientos_callback:
        Función que recibe el DataFrame completo de movimientos actualizado.

    Retorna
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        Stock y movimientos preparados/actualizados.
    """

    import html
    import unicodedata
    import uuid
    from datetime import date, datetime, timedelta

    import pandas as pd
    import plotly.express as px
    import streamlit as st

    # =====================================================
    # CONFIGURACIÓN
    # =====================================================

    CAMPOS_STOCK = {
        "id_articulo": "",
        "codigo": "",
        "articulo": "",
        "principio_activo": "",
        "categoria": "Insumos generales",
        "presentacion": "",
        "unidad": "unidad",
        "proveedor": "",
        "costo_unitario": 0.0,
        "requiere_frio": False,
        "medicamento_controlado": False,
        "activo": True,
        "stock_quirofano": 0.0,
        "stock_minimo_quirofano": 0.0,
        "ubicacion_quirofano": "",
        "lote_quirofano": "",
        "vencimiento_quirofano": "",
        "stock_farmacia": 0.0,
        "stock_minimo_farmacia": 0.0,
        "ubicacion_farmacia": "",
        "lote_farmacia": "",
        "vencimiento_farmacia": "",
        "ultima_actualizacion": "",
        "observaciones": "",
    }

    CAMPOS_MOVIMIENTOS = {
        "id_movimiento": "",
        "fecha": "",
        "hora": "",
        "sector": "",
        "tipo": "",
        "id_articulo": "",
        "articulo": "",
        "cantidad": 0.0,
        "stock_anterior": 0.0,
        "stock_nuevo": 0.0,
        "sector_destino": "",
        "stock_destino_anterior": 0.0,
        "stock_destino_nuevo": 0.0,
        "lote": "",
        "vencimiento": "",
        "responsable": "",
        "motivo": "",
        "observaciones": "",
    }

    CATEGORIAS = [
        "Medicamentos",
        "Anestesia",
        "Antibióticos",
        "Analgésicos y antiinflamatorios",
        "Soluciones y sueros",
        "Descartables",
        "Curaciones",
        "Suturas",
        "Elementos de protección personal",
        "Insumos de fertilidad",
        "Insumos de ginecología",
        "Insumos de quirófano",
        "Limpieza y desinfección",
        "Insumos generales",
        "Otros",
    ]

    TIPOS_MOVIMIENTO = [
        "Ingreso / compra",
        "Consumo / salida",
        "Ajuste positivo",
        "Ajuste negativo",
        "Transferencia al otro stock",
    ]

    # =====================================================
    # FUNCIONES INTERNAS SEGURAS
    # =====================================================

    def normalizar_columna(valor):
        texto = str(valor or "").strip().lower()
        texto = unicodedata.normalize("NFKD", texto)
        texto = "".join(
            caracter
            for caracter in texto
            if not unicodedata.combining(caracter)
        )
        reemplazos = {
            " ": "_",
            "-": "_",
            "/": "_",
            ".": "",
            "(": "",
            ")": "",
            "°": "",
            "º": "",
        }
        for anterior, nuevo in reemplazos.items():
            texto = texto.replace(anterior, nuevo)
        while "__" in texto:
            texto = texto.replace("__", "_")
        return texto.strip("_")

    def texto_limpio(valor):
        if valor is None:
            return ""
        try:
            if pd.isna(valor):
                return ""
        except Exception:
            pass
        texto = str(valor).strip()
        if texto.lower() in {"nan", "none", "nat"}:
            return ""
        return texto

    def numero_seguro(valor, default=0.0):
        try:
            if valor is None or texto_limpio(valor) == "":
                return float(default)
            if isinstance(valor, str):
                limpio = valor.strip().replace("$", "").replace(" ", "")
                if "," in limpio and "." in limpio:
                    if limpio.rfind(",") > limpio.rfind("."):
                        limpio = limpio.replace(".", "").replace(",", ".")
                    else:
                        limpio = limpio.replace(",", "")
                elif "," in limpio:
                    limpio = limpio.replace(",", ".")
                valor = limpio
            return float(valor)
        except Exception:
            return float(default)

    def booleano_seguro(valor, default=False):
        if isinstance(valor, bool):
            return valor
        texto = texto_limpio(valor).lower()
        if texto in {"1", "true", "si", "sí", "yes", "x", "activo"}:
            return True
        if texto in {"0", "false", "no", "", "inactivo"}:
            return False
        return bool(default)

    def fecha_segura(valor):
        if texto_limpio(valor) == "":
            return pd.NaT
        return pd.to_datetime(valor, errors="coerce", dayfirst=True)

    def fecha_para_guardar(valor):
        fecha = fecha_segura(valor)
        if pd.isna(fecha):
            return ""
        return fecha.strftime("%Y-%m-%d")

    def escapar(valor):
        return html.escape(texto_limpio(valor))

    def preparar_stock(df_original):
        if not isinstance(df_original, pd.DataFrame):
            df_original = pd.DataFrame()

        data = df_original.copy()
        data = data.rename(
            columns={columna: normalizar_columna(columna) for columna in data.columns}
        )

        alias = {
            "id": "id_articulo",
            "producto": "articulo",
            "insumo": "articulo",
            "medicamento": "articulo",
            "nombre": "articulo",
            "stock_qx": "stock_quirofano",
            "stock_quirofano_actual": "stock_quirofano",
            "minimo_qx": "stock_minimo_quirofano",
            "stock_minimo_qx": "stock_minimo_quirofano",
            "stock_farm": "stock_farmacia",
            "minimo_farmacia": "stock_minimo_farmacia",
            "precio": "costo_unitario",
            "costo": "costo_unitario",
            "frio": "requiere_frio",
            "controlado": "medicamento_controlado",
        }
        for actual, estandar in alias.items():
            if actual in data.columns and estandar not in data.columns:
                data = data.rename(columns={actual: estandar})

        for columna, default in CAMPOS_STOCK.items():
            if columna not in data.columns:
                data[columna] = default

        if not data.empty:
            ids_vacios = data["id_articulo"].apply(texto_limpio).eq("")
            for indice in data.index[ids_vacios]:
                data.at[indice, "id_articulo"] = f"ART-{uuid.uuid4().hex[:8].upper()}"

        columnas_texto = [
            "id_articulo",
            "codigo",
            "articulo",
            "principio_activo",
            "categoria",
            "presentacion",
            "unidad",
            "proveedor",
            "ubicacion_quirofano",
            "lote_quirofano",
            "ubicacion_farmacia",
            "lote_farmacia",
            "ultima_actualizacion",
            "observaciones",
        ]
        for columna in columnas_texto:
            data[columna] = data[columna].apply(texto_limpio)

        columnas_numericas = [
            "costo_unitario",
            "stock_quirofano",
            "stock_minimo_quirofano",
            "stock_farmacia",
            "stock_minimo_farmacia",
        ]
        for columna in columnas_numericas:
            data[columna] = data[columna].apply(numero_seguro).clip(lower=0)

        for columna in ["requiere_frio", "medicamento_controlado", "activo"]:
            data[columna] = data[columna].apply(booleano_seguro)

        for columna in ["vencimiento_quirofano", "vencimiento_farmacia"]:
            data[columna] = data[columna].apply(fecha_para_guardar)

        columnas_ordenadas = list(CAMPOS_STOCK.keys())
        columnas_extra = [c for c in data.columns if c not in columnas_ordenadas]
        return data.reindex(columns=columnas_ordenadas + columnas_extra)

    def preparar_movimientos(df_original):
        if not isinstance(df_original, pd.DataFrame):
            df_original = pd.DataFrame()

        data = df_original.copy()
        data = data.rename(
            columns={columna: normalizar_columna(columna) for columna in data.columns}
        )

        for columna, default in CAMPOS_MOVIMIENTOS.items():
            if columna not in data.columns:
                data[columna] = default

        columnas_texto = [
            "id_movimiento",
            "fecha",
            "hora",
            "sector",
            "tipo",
            "id_articulo",
            "articulo",
            "sector_destino",
            "lote",
            "vencimiento",
            "responsable",
            "motivo",
            "observaciones",
        ]
        for columna in columnas_texto:
            data[columna] = data[columna].apply(texto_limpio)

        columnas_numericas = [
            "cantidad",
            "stock_anterior",
            "stock_nuevo",
            "stock_destino_anterior",
            "stock_destino_nuevo",
        ]
        for columna in columnas_numericas:
            data[columna] = data[columna].apply(numero_seguro)

        data["fecha"] = data["fecha"].apply(fecha_para_guardar)
        data["vencimiento"] = data["vencimiento"].apply(fecha_para_guardar)

        columnas_ordenadas = list(CAMPOS_MOVIMIENTOS.keys())
        columnas_extra = [c for c in data.columns if c not in columnas_ordenadas]
        return data.reindex(columns=columnas_ordenadas + columnas_extra)

    def guardar_un_dataframe(callback, dataframe, session_key, nombre):
        if callable(callback):
            callback(dataframe.copy())
        else:
            st.session_state[session_key] = dataframe.copy()
            st.warning(
                f"{nombre} quedó actualizado solamente durante esta sesión. "
                "Conectá el callback para guardarlo definitivamente en Google Sheets."
            )

    def guardar_cambios(stock_nuevo, movimientos_nuevos, mensaje):
        try:
            guardar_un_dataframe(
                guardar_stock_callback,
                stock_nuevo,
                "farmacia_pro_stock_sesion",
                "El stock",
            )
            guardar_un_dataframe(
                guardar_movimientos_callback,
                movimientos_nuevos,
                "farmacia_pro_movimientos_sesion",
                "El historial",
            )
            st.cache_data.clear()
            st.success(mensaje)
            return True
        except Exception as error:
            st.error("No se pudieron guardar los cambios de Farmacia.")
            st.exception(error)
            return False

    def agregar_movimiento(movs, datos):
        fila = {columna: default for columna, default in CAMPOS_MOVIMIENTOS.items()}
        fila.update(datos)
        fila["id_movimiento"] = fila.get("id_movimiento") or f"MOV-{uuid.uuid4().hex[:10].upper()}"
        fila["fecha"] = fila.get("fecha") or date.today().strftime("%Y-%m-%d")
        fila["hora"] = fila.get("hora") or datetime.now().strftime("%H:%M:%S")
        nuevo = pd.DataFrame([fila])
        return pd.concat([movs, nuevo], ignore_index=True)

    def estado_stock(fila, sector):
        col_stock = f"stock_{sector}"
        col_minimo = f"stock_minimo_{sector}"
        stock = numero_seguro(fila.get(col_stock))
        minimo = numero_seguro(fila.get(col_minimo))
        if stock <= 0:
            return "🔴 Sin stock"
        if minimo > 0 and stock <= minimo:
            return "🟠 Stock bajo"
        return "🟢 Normal"

    def estado_vencimiento(fila, sector):
        fecha = fecha_segura(fila.get(f"vencimiento_{sector}"))
        if pd.isna(fecha):
            return "⚪ Sin fecha"
        dias = (fecha.normalize() - pd.Timestamp.today().normalize()).days
        if dias < 0:
            return "🔴 Vencido"
        if dias <= 30:
            return f"🟠 Vence en {dias} días"
        if dias <= 90:
            return f"🟡 Vence en {dias} días"
        return "🟢 Vigente"

    def formato_cantidad(valor):
        numero = numero_seguro(valor)
        if float(numero).is_integer():
            return f"{int(numero):,}".replace(",", ".")
        return f"{numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def formato_pesos(valor):
        numero = numero_seguro(valor)
        return "$ " + f"{numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def tabla_sector(data, sector):
        if data.empty:
            return pd.DataFrame()
        tabla = pd.DataFrame(
            {
                "Código": data["codigo"],
                "Artículo": data["articulo"],
                "Categoría": data["categoria"],
                "Presentación": data["presentacion"],
                "Unidad": data["unidad"],
                "Stock": data[f"stock_{sector}"],
                "Mínimo": data[f"stock_minimo_{sector}"],
                "Estado": data.apply(lambda fila: estado_stock(fila, sector), axis=1),
                "Ubicación": data[f"ubicacion_{sector}"],
                "Lote": data[f"lote_{sector}"],
                "Vencimiento": data[f"vencimiento_{sector}"].apply(
                    lambda valor: fecha_segura(valor).strftime("%d/%m/%Y")
                    if pd.notna(fecha_segura(valor))
                    else ""
                ),
                "Control vencimiento": data.apply(
                    lambda fila: estado_vencimiento(fila, sector), axis=1
                ),
                "Proveedor": data["proveedor"],
                "Requiere frío": data["requiere_frio"].map({True: "Sí", False: "No"}),
                "Controlado": data["medicamento_controlado"].map({True: "Sí", False: "No"}),
            }
        )
        return tabla

    # =====================================================
    # PREPARAR DATOS
    # =====================================================

    stock_entrada = preparar_stock(df_stock)
    movimientos_entrada = preparar_movimientos(df_movimientos)

    if guardar_stock_callback is None:
        if "farmacia_pro_stock_sesion" not in st.session_state:
            st.session_state["farmacia_pro_stock_sesion"] = stock_entrada.copy()
        stock = preparar_stock(st.session_state["farmacia_pro_stock_sesion"])
    else:
        stock = stock_entrada.copy()

    if guardar_movimientos_callback is None:
        if "farmacia_pro_movimientos_sesion" not in st.session_state:
            st.session_state["farmacia_pro_movimientos_sesion"] = movimientos_entrada.copy()
        movimientos = preparar_movimientos(
            st.session_state["farmacia_pro_movimientos_sesion"]
        )
    else:
        movimientos = movimientos_entrada.copy()

    # =====================================================
    # ESTILO
    # =====================================================

    st.markdown(
        """
        <style>
        .farmacia-hero {
            padding: 22px 24px;
            border: 1px solid rgba(120,120,120,.18);
            border-radius: 18px;
            margin-bottom: 18px;
            background: linear-gradient(135deg, rgba(20,130,110,.10), rgba(40,110,220,.06));
        }
        .farmacia-titulo {
            font-size: 31px;
            line-height: 1.15;
            font-weight: 850;
            margin-bottom: 6px;
        }
        .farmacia-subtitulo {
            color: rgba(95,95,105,.95);
            font-size: 15px;
        }
        .farmacia-alerta {
            border-radius: 13px;
            padding: 12px 14px;
            margin: 8px 0;
            border: 1px solid rgba(220,120,30,.20);
            background: rgba(255,170,40,.07);
        }
        .farmacia-ok {
            border-radius: 13px;
            padding: 12px 14px;
            margin: 8px 0;
            border: 1px solid rgba(20,150,90,.18);
            background: rgba(20,150,90,.06);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="farmacia-hero">
            <div class="farmacia-titulo">💊 Farmacia y Stock Clínico PRO</div>
            <div class="farmacia-subtitulo">
                Catálogo único de artículos · existencias independientes para Quirófano y Farmacia ·
                movimientos auditados · vencimientos · reposición · control diario.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # =====================================================
    # RESUMEN GENERAL
    # =====================================================

    activos = stock[stock["activo"]].copy()
    total_articulos = len(activos)
    unidades_qx = activos["stock_quirofano"].sum() if not activos.empty else 0
    unidades_farm = activos["stock_farmacia"].sum() if not activos.empty else 0

    bajo_qx = (
        (
            (activos["stock_quirofano"] <= activos["stock_minimo_quirofano"])
            & (activos["stock_minimo_quirofano"] > 0)
        ).sum()
        if not activos.empty
        else 0
    )
    bajo_farm = (
        (
            (activos["stock_farmacia"] <= activos["stock_minimo_farmacia"])
            & (activos["stock_minimo_farmacia"] > 0)
        ).sum()
        if not activos.empty
        else 0
    )

    hoy = pd.Timestamp.today().normalize()
    vencimientos = []
    for sector in ["quirofano", "farmacia"]:
        fechas = activos[f"vencimiento_{sector}"].apply(fecha_segura)
        vencimientos.append((fechas.notna() & (fechas <= hoy + pd.Timedelta(days=90))).sum())
    vencen_90 = int(sum(vencimientos))

    valor_total = (
        (
            activos["stock_quirofano"] + activos["stock_farmacia"]
        )
        * activos["costo_unitario"]
    ).sum() if not activos.empty else 0

    r1, r2, r3, r4, r5 = st.columns(5)
    r1.metric("📦 Artículos activos", total_articulos)
    r2.metric("🏥 Unidades Quirófano", formato_cantidad(unidades_qx))
    r3.metric("💊 Unidades Farmacia", formato_cantidad(unidades_farm))
    r4.metric("🚨 Stocks críticos", int(bajo_qx + bajo_farm))
    r5.metric("💰 Valor estimado", formato_pesos(valor_total))

    if vencen_90 > 0:
        st.warning(
            f"Hay {vencen_90} registros de lote que vencen dentro de los próximos 90 días "
            "o ya están vencidos."
        )

    # =====================================================
    # DOS MÓDULOS INTERNOS
    # =====================================================

    tab_quirofano, tab_farmacia = st.tabs(
        ["🏥 Stock Quirófano", "💊 Stock Farmacia"]
    )

    def render_sector(sector, titulo, icono):
        col_stock = f"stock_{sector}"
        col_minimo = f"stock_minimo_{sector}"
        col_lote = f"lote_{sector}"
        col_vencimiento = f"vencimiento_{sector}"
        otro_sector = "farmacia" if sector == "quirofano" else "quirofano"
        nombre_otro = "Farmacia" if sector == "quirofano" else "Quirófano"
        nombre_sector = "Quirófano" if sector == "quirofano" else "Farmacia"

        data = stock[stock["activo"]].copy()
        data["_estado_stock"] = data.apply(
            lambda fila: estado_stock(fila, sector), axis=1
        )
        data["_fecha_vencimiento"] = data[col_vencimiento].apply(fecha_segura)
        data["_dias_vencimiento"] = (
            data["_fecha_vencimiento"] - pd.Timestamp.today().normalize()
        ).dt.days

        total_unidades = data[col_stock].sum() if not data.empty else 0
        sin_stock = int((data[col_stock] <= 0).sum()) if not data.empty else 0
        stock_bajo = int(
            (
                (data[col_stock] > 0)
                & (data[col_minimo] > 0)
                & (data[col_stock] <= data[col_minimo])
            ).sum()
        ) if not data.empty else 0
        vencidos = int((data["_dias_vencimiento"] < 0).sum()) if not data.empty else 0
        proximos = int(
            (
                (data["_dias_vencimiento"] >= 0)
                & (data["_dias_vencimiento"] <= 90)
            ).sum()
        ) if not data.empty else 0

        st.markdown(f"## {icono} {titulo}")
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Unidades disponibles", formato_cantidad(total_unidades))
        m2.metric("Sin stock", sin_stock)
        m3.metric("Stock bajo", stock_bajo)
        m4.metric("Vencidos", vencidos)
        m5.metric("Vencen ≤ 90 días", proximos)

        fecha_hoy = date.today().strftime("%Y-%m-%d")
        controles_hoy = movimientos[
            movimientos["fecha"].eq(fecha_hoy)
            & movimientos["sector"].str.lower().eq(nombre_sector.lower())
            & movimientos["tipo"].eq("Control diario")
        ]
        if controles_hoy.empty:
            st.markdown(
                '<div class="farmacia-alerta"><b>⚠️ Control diario pendiente.</b> '
                "Todavía no se registró la revisión de este sector hoy.</div>",
                unsafe_allow_html=True,
            )
        else:
            ultimo_responsable = controles_hoy.iloc[-1]["responsable"]
            ultima_hora = controles_hoy.iloc[-1]["hora"]
            st.markdown(
                '<div class="farmacia-ok"><b>✅ Control diario realizado.</b> '
                f"{escapar(ultimo_responsable)} · {escapar(ultima_hora)}</div>",
                unsafe_allow_html=True,
            )

        st.markdown("### 🔎 Buscar y filtrar inventario")
        f1, f2, f3 = st.columns([2.2, 1.4, 1.4])
        buscar = f1.text_input(
            "Buscar artículo, código, principio activo o lote",
            key=f"farmacia_buscar_{sector}",
        ).strip().lower()
        categorias_disponibles = sorted(
            [c for c in data["categoria"].dropna().astype(str).unique() if c.strip()]
        )
        categoria = f2.selectbox(
            "Categoría",
            ["Todas"] + categorias_disponibles,
            key=f"farmacia_categoria_{sector}",
        )
        situacion = f3.selectbox(
            "Situación",
            [
                "Todos",
                "Sin stock",
                "Stock bajo",
                "Normal",
                "Vencidos",
                "Vencen en 30 días",
                "Vencen en 90 días",
                "Cadena de frío",
                "Controlados",
            ],
            key=f"farmacia_situacion_{sector}",
        )

        filtrado = data.copy()
        if buscar:
            mascara_busqueda = pd.Series(False, index=filtrado.index)
            for columna in [
                "codigo",
                "articulo",
                "principio_activo",
                "categoria",
                "presentacion",
                col_lote,
                "proveedor",
            ]:
                mascara_busqueda = mascara_busqueda | filtrado[columna].astype(str).str.lower().str.contains(
                    buscar, na=False, regex=False
                )
            filtrado = filtrado[mascara_busqueda]

        if categoria != "Todas":
            filtrado = filtrado[filtrado["categoria"].eq(categoria)]

        if situacion == "Sin stock":
            filtrado = filtrado[filtrado[col_stock] <= 0]
        elif situacion == "Stock bajo":
            filtrado = filtrado[
                (filtrado[col_stock] > 0)
                & (filtrado[col_minimo] > 0)
                & (filtrado[col_stock] <= filtrado[col_minimo])
            ]
        elif situacion == "Normal":
            filtrado = filtrado[
                (filtrado[col_stock] > filtrado[col_minimo])
                | ((filtrado[col_minimo] <= 0) & (filtrado[col_stock] > 0))
            ]
        elif situacion == "Vencidos":
            filtrado = filtrado[filtrado["_dias_vencimiento"] < 0]
        elif situacion == "Vencen en 30 días":
            filtrado = filtrado[
                (filtrado["_dias_vencimiento"] >= 0)
                & (filtrado["_dias_vencimiento"] <= 30)
            ]
        elif situacion == "Vencen en 90 días":
            filtrado = filtrado[
                (filtrado["_dias_vencimiento"] >= 0)
                & (filtrado["_dias_vencimiento"] <= 90)
            ]
        elif situacion == "Cadena de frío":
            filtrado = filtrado[filtrado["requiere_frio"]]
        elif situacion == "Controlados":
            filtrado = filtrado[filtrado["medicamento_controlado"]]

        st.markdown("### 📋 Inventario operativo")
        tabla = tabla_sector(filtrado, sector)
        if tabla.empty:
            st.info("No hay artículos que coincidan con los filtros seleccionados.")
        else:
            st.dataframe(
                tabla,
                use_container_width=True,
                hide_index=True,
                height=470,
                column_config={
                    "Stock": st.column_config.NumberColumn(format="%.2f"),
                    "Mínimo": st.column_config.NumberColumn(format="%.2f"),
                },
            )

        st.download_button(
            f"⬇️ Descargar inventario de {nombre_sector}",
            data=tabla.to_csv(index=False, encoding="utf-8-sig"),
            file_name=f"stock_{sector}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            key=f"farmacia_descargar_{sector}",
            use_container_width=True,
        )

        # -------------------------------------------------
        # REPOSICIÓN SUGERIDA
        # -------------------------------------------------

        reposicion = data.copy()
        reposicion["pedido_sugerido"] = (
            (reposicion[col_minimo] * 2) - reposicion[col_stock]
        ).clip(lower=0)
        reposicion = reposicion[
            (reposicion[col_minimo] > 0) & (reposicion["pedido_sugerido"] > 0)
        ].sort_values(["pedido_sugerido", "articulo"], ascending=[False, True])

        with st.expander("🛒 Pedido sugerido y faltantes", expanded=not reposicion.empty):
            if reposicion.empty:
                st.success("No hay reposición sugerida según los mínimos configurados.")
            else:
                pedido = pd.DataFrame(
                    {
                        "Artículo": reposicion["articulo"],
                        "Categoría": reposicion["categoria"],
                        "Stock actual": reposicion[col_stock],
                        "Stock mínimo": reposicion[col_minimo],
                        "Pedido sugerido": reposicion["pedido_sugerido"],
                        "Unidad": reposicion["unidad"],
                        "Proveedor": reposicion["proveedor"],
                    }
                )
                st.dataframe(pedido, use_container_width=True, hide_index=True)
                st.download_button(
                    "⬇️ Descargar pedido sugerido",
                    data=pedido.to_csv(index=False, encoding="utf-8-sig"),
                    file_name=f"pedido_sugerido_{sector}_{date.today().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                    key=f"pedido_sugerido_descarga_{sector}",
                )

        # -------------------------------------------------
        # MOVIMIENTO Y CONTROL DIARIO
        # -------------------------------------------------

        izquierda, derecha = st.columns(2)

        with izquierda:
            st.markdown("### 🔄 Registrar movimiento")
            articulos_disponibles = stock[stock["activo"] & stock["articulo"].ne("")].copy()
            opciones = articulos_disponibles["id_articulo"].tolist()

            def etiqueta_articulo(id_articulo):
                fila = articulos_disponibles[
                    articulos_disponibles["id_articulo"].eq(id_articulo)
                ]
                if fila.empty:
                    return id_articulo
                registro = fila.iloc[0]
                codigo = texto_limpio(registro["codigo"])
                prefijo = f"{codigo} · " if codigo else ""
                return f"{prefijo}{registro['articulo']} · stock {formato_cantidad(registro[col_stock])}"

            if not opciones:
                st.info("Primero cargá al menos un artículo en el catálogo compartido.")
            else:
                with st.form(f"farmacia_movimiento_{sector}", clear_on_submit=True):
                    id_elegido = st.selectbox(
                        "Artículo",
                        opciones,
                        format_func=etiqueta_articulo,
                    )
                    tipo = st.selectbox("Tipo de movimiento", TIPOS_MOVIMIENTO)
                    cantidad = st.number_input(
                        "Cantidad",
                        min_value=0.01,
                        value=1.0,
                        step=1.0,
                    )
                    motivo = st.text_input(
                        "Motivo / procedimiento / comprobante",
                        placeholder="Ej.: consumo cirugía, compra proveedor, ajuste por conteo",
                    )
                    responsable = st.text_input(
                        "Responsable *",
                        placeholder="Nombre de quien registra",
                    )
                    actualizar_lote = st.checkbox(
                        "Actualizar lote y vencimiento de este sector"
                    )
                    lote_nuevo = st.text_input("Lote", disabled=not actualizar_lote)
                    vencimiento_nuevo = st.date_input(
                        "Vencimiento",
                        value=date.today() + timedelta(days=365),
                        disabled=not actualizar_lote,
                    )
                    observaciones = st.text_area("Observaciones")
                    enviar = st.form_submit_button(
                        "Guardar movimiento",
                        type="primary",
                        use_container_width=True,
                    )

                if enviar:
                    if not responsable.strip():
                        st.error("Tenés que indicar quién registra el movimiento.")
                    else:
                        stock_nuevo = stock.copy()
                        movimientos_nuevos = movimientos.copy()
                        indices = stock_nuevo.index[
                            stock_nuevo["id_articulo"].eq(id_elegido)
                        ].tolist()
                        if not indices:
                            st.error("No se encontró el artículo seleccionado.")
                        else:
                            indice = indices[0]
                            articulo = stock_nuevo.at[indice, "articulo"]
                            anterior = numero_seguro(stock_nuevo.at[indice, col_stock])
                            cantidad_num = numero_seguro(cantidad)
                            destino_anterior = 0.0
                            destino_nuevo = 0.0
                            sector_destino = ""

                            if tipo in {"Ingreso / compra", "Ajuste positivo"}:
                                nuevo = anterior + cantidad_num
                            else:
                                nuevo = anterior - cantidad_num

                            if tipo in {
                                "Consumo / salida",
                                "Ajuste negativo",
                                "Transferencia al otro stock",
                            } and cantidad_num > anterior:
                                st.error(
                                    f"No hay stock suficiente. Disponible: {formato_cantidad(anterior)}."
                                )
                            else:
                                stock_nuevo.at[indice, col_stock] = max(nuevo, 0)

                                if tipo == "Transferencia al otro stock":
                                    col_destino = f"stock_{otro_sector}"
                                    destino_anterior = numero_seguro(
                                        stock_nuevo.at[indice, col_destino]
                                    )
                                    destino_nuevo = destino_anterior + cantidad_num
                                    stock_nuevo.at[indice, col_destino] = destino_nuevo
                                    sector_destino = nombre_otro

                                if actualizar_lote:
                                    stock_nuevo.at[indice, col_lote] = lote_nuevo.strip()
                                    stock_nuevo.at[indice, col_vencimiento] = (
                                        vencimiento_nuevo.strftime("%Y-%m-%d")
                                    )

                                stock_nuevo.at[indice, "ultima_actualizacion"] = (
                                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                )

                                movimientos_nuevos = agregar_movimiento(
                                    movimientos_nuevos,
                                    {
                                        "sector": nombre_sector,
                                        "tipo": tipo,
                                        "id_articulo": id_elegido,
                                        "articulo": articulo,
                                        "cantidad": cantidad_num,
                                        "stock_anterior": anterior,
                                        "stock_nuevo": max(nuevo, 0),
                                        "sector_destino": sector_destino,
                                        "stock_destino_anterior": destino_anterior,
                                        "stock_destino_nuevo": destino_nuevo,
                                        "lote": lote_nuevo.strip() if actualizar_lote else stock_nuevo.at[indice, col_lote],
                                        "vencimiento": vencimiento_nuevo.strftime("%Y-%m-%d") if actualizar_lote else stock_nuevo.at[indice, col_vencimiento],
                                        "responsable": responsable.strip(),
                                        "motivo": motivo.strip(),
                                        "observaciones": observaciones.strip(),
                                    },
                                )

                                if guardar_cambios(
                                    preparar_stock(stock_nuevo),
                                    preparar_movimientos(movimientos_nuevos),
                                    "Movimiento registrado correctamente.",
                                ):
                                    st.rerun()

        with derecha:
            st.markdown("### ✅ Control diario")
            with st.form(f"farmacia_control_diario_{sector}", clear_on_submit=True):
                responsable_control = st.text_input(
                    "Responsable del control *",
                    key=f"responsable_control_{sector}",
                )
                conteo_ok = st.checkbox(
                    "Conteo físico revisado",
                    key=f"conteo_control_{sector}",
                )
                vencimientos_ok = st.checkbox(
                    "Lotes y vencimientos revisados",
                    key=f"vencimientos_control_{sector}",
                )
                almacenamiento_ok = st.checkbox(
                    "Orden, almacenamiento y cadena de frío revisados",
                    key=f"almacenamiento_control_{sector}",
                )
                faltantes_informados = st.checkbox(
                    "Faltantes y productos críticos informados",
                    key=f"faltantes_control_{sector}",
                )
                observacion_control = st.text_area(
                    "Novedades del control",
                    key=f"observacion_control_{sector}",
                )
                guardar_control = st.form_submit_button(
                    "Registrar control de hoy",
                    type="primary",
                    use_container_width=True,
                )

            if guardar_control:
                if not responsable_control.strip():
                    st.error("Indicá quién realizó el control diario.")
                elif not all(
                    [conteo_ok, vencimientos_ok, almacenamiento_ok, faltantes_informados]
                ):
                    st.error(
                        "Para cerrar el control diario deben quedar confirmados los cuatro puntos. "
                        "Si existe una novedad, describila en observaciones."
                    )
                else:
                    movimientos_nuevos = agregar_movimiento(
                        movimientos.copy(),
                        {
                            "sector": nombre_sector,
                            "tipo": "Control diario",
                            "articulo": "CONTROL GENERAL DEL SECTOR",
                            "cantidad": 0,
                            "responsable": responsable_control.strip(),
                            "motivo": "Control operativo diario completo",
                            "observaciones": observacion_control.strip(),
                        },
                    )
                    if guardar_cambios(
                        stock.copy(),
                        preparar_movimientos(movimientos_nuevos),
                        f"Control diario de {nombre_sector} registrado.",
                    ):
                        st.rerun()

        # -------------------------------------------------
        # GRÁFICO DE PRIORIDADES
        # -------------------------------------------------

        prioridades = data[data[col_minimo] > 0].copy()
        prioridades["Cobertura sobre mínimo"] = prioridades.apply(
            lambda fila: (
                numero_seguro(fila[col_stock]) / numero_seguro(fila[col_minimo])
                if numero_seguro(fila[col_minimo]) > 0
                else 0
            ),
            axis=1,
        )
        prioridades = prioridades.sort_values("Cobertura sobre mínimo").head(12)
        if not prioridades.empty:
            st.markdown("### 📊 Artículos con menor cobertura")
            grafico = prioridades[
                ["articulo", col_stock, col_minimo]
            ].rename(
                columns={
                    "articulo": "Artículo",
                    col_stock: "Stock actual",
                    col_minimo: "Stock mínimo",
                }
            )
            grafico_largo = grafico.melt(
                id_vars="Artículo",
                value_vars=["Stock actual", "Stock mínimo"],
                var_name="Indicador",
                value_name="Cantidad",
            )
            figura = px.bar(
                grafico_largo,
                x="Artículo",
                y="Cantidad",
                color="Indicador",
                barmode="group",
                title=f"Stock actual vs mínimo · {nombre_sector}",
            )
            figura.update_layout(height=430, xaxis_tickangle=-35)
            st.plotly_chart(
                figura,
                use_container_width=True,
                key=f"farmacia_grafico_cobertura_{sector}",
            )

    with tab_quirofano:
        render_sector("quirofano", "Control y gestión de Stock Quirófano", "🏥")

    with tab_farmacia:
        render_sector("farmacia", "Control y gestión de Stock Farmacia", "💊")

    # =====================================================
    # CATÁLOGO COMPARTIDO
    # =====================================================

    st.divider()
    with st.expander("🧾 Catálogo compartido de artículos", expanded=stock.empty):
        st.caption(
            "El artículo se carga una sola vez. Después aparece tanto en Stock Quirófano "
            "como en Stock Farmacia, cada uno con su propia cantidad, mínimo, lote y vencimiento."
        )

        st.markdown("### ➕ Nuevo artículo")
        with st.form("farmacia_nuevo_articulo", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            codigo = c1.text_input("Código interno")
            articulo = c2.text_input("Artículo / medicamento *")
            principio_activo = c3.text_input("Principio activo")

            c4, c5, c6 = st.columns(3)
            categoria = c4.selectbox("Categoría", CATEGORIAS)
            presentacion = c5.text_input("Presentación")
            unidad = c6.text_input("Unidad de medida", value="unidad")

            c7, c8, c9 = st.columns(3)
            proveedor = c7.text_input("Proveedor habitual")
            costo = c8.number_input("Costo unitario", min_value=0.0, value=0.0)
            activo_nuevo = c9.checkbox("Artículo activo", value=True)

            frio, controlado = st.columns(2)
            requiere_frio = frio.checkbox("Requiere cadena de frío")
            medicamento_controlado = controlado.checkbox("Medicamento controlado")

            st.markdown("#### Stock inicial Quirófano")
            q1, q2, q3, q4 = st.columns(4)
            stock_qx = q1.number_input("Stock inicial Qx", min_value=0.0, value=0.0)
            minimo_qx = q2.number_input("Mínimo Qx", min_value=0.0, value=0.0)
            ubicacion_qx = q3.text_input("Ubicación Qx")
            lote_qx = q4.text_input("Lote Qx")
            venc_qx = st.date_input(
                "Vencimiento Qx",
                value=None,
                key="farmacia_nuevo_venc_qx",
            )

            st.markdown("#### Stock inicial Farmacia")
            f1, f2, f3, f4 = st.columns(4)
            stock_f = f1.number_input("Stock inicial Farmacia", min_value=0.0, value=0.0)
            minimo_f = f2.number_input("Mínimo Farmacia", min_value=0.0, value=0.0)
            ubicacion_f = f3.text_input("Ubicación Farmacia")
            lote_f = f4.text_input("Lote Farmacia")
            venc_f = st.date_input(
                "Vencimiento Farmacia",
                value=None,
                key="farmacia_nuevo_venc_farm",
            )

            observaciones_nuevo = st.text_area("Observaciones")
            crear = st.form_submit_button(
                "Crear artículo compartido",
                type="primary",
                use_container_width=True,
            )

        if crear:
            if not articulo.strip():
                st.error("El nombre del artículo es obligatorio.")
            elif (
                (
                    stock["articulo"]
                    .astype(str)
                    .str.strip()
                    .str.lower()
                    .eq(articulo.strip().lower())
                )
                & (
                    stock["presentacion"]
                    .astype(str)
                    .str.strip()
                    .str.lower()
                    .eq(presentacion.strip().lower())
                )
            ).any():
                st.error(
                    "Ya existe un artículo con el mismo nombre y presentación. "
                    "Editá el existente para evitar duplicados."
                )
            else:
                ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                id_nuevo = f"ART-{uuid.uuid4().hex[:8].upper()}"
                fila = {
                    "id_articulo": id_nuevo,
                    "codigo": codigo.strip(),
                    "articulo": articulo.strip(),
                    "principio_activo": principio_activo.strip(),
                    "categoria": categoria,
                    "presentacion": presentacion.strip(),
                    "unidad": unidad.strip() or "unidad",
                    "proveedor": proveedor.strip(),
                    "costo_unitario": numero_seguro(costo),
                    "requiere_frio": requiere_frio,
                    "medicamento_controlado": medicamento_controlado,
                    "activo": activo_nuevo,
                    "stock_quirofano": numero_seguro(stock_qx),
                    "stock_minimo_quirofano": numero_seguro(minimo_qx),
                    "ubicacion_quirofano": ubicacion_qx.strip(),
                    "lote_quirofano": lote_qx.strip(),
                    "vencimiento_quirofano": venc_qx.strftime("%Y-%m-%d") if venc_qx else "",
                    "stock_farmacia": numero_seguro(stock_f),
                    "stock_minimo_farmacia": numero_seguro(minimo_f),
                    "ubicacion_farmacia": ubicacion_f.strip(),
                    "lote_farmacia": lote_f.strip(),
                    "vencimiento_farmacia": venc_f.strftime("%Y-%m-%d") if venc_f else "",
                    "ultima_actualizacion": ahora,
                    "observaciones": observaciones_nuevo.strip(),
                }
                stock_nuevo = pd.concat(
                    [stock, pd.DataFrame([fila])], ignore_index=True
                )
                movimientos_nuevos = movimientos.copy()

                if numero_seguro(stock_qx) > 0:
                    movimientos_nuevos = agregar_movimiento(
                        movimientos_nuevos,
                        {
                            "sector": "Quirófano",
                            "tipo": "Stock inicial",
                            "id_articulo": id_nuevo,
                            "articulo": articulo.strip(),
                            "cantidad": numero_seguro(stock_qx),
                            "stock_anterior": 0,
                            "stock_nuevo": numero_seguro(stock_qx),
                            "lote": lote_qx.strip(),
                            "vencimiento": venc_qx.strftime("%Y-%m-%d") if venc_qx else "",
                            "responsable": "Carga inicial",
                            "motivo": "Alta de artículo",
                        },
                    )

                if numero_seguro(stock_f) > 0:
                    movimientos_nuevos = agregar_movimiento(
                        movimientos_nuevos,
                        {
                            "sector": "Farmacia",
                            "tipo": "Stock inicial",
                            "id_articulo": id_nuevo,
                            "articulo": articulo.strip(),
                            "cantidad": numero_seguro(stock_f),
                            "stock_anterior": 0,
                            "stock_nuevo": numero_seguro(stock_f),
                            "lote": lote_f.strip(),
                            "vencimiento": venc_f.strftime("%Y-%m-%d") if venc_f else "",
                            "responsable": "Carga inicial",
                            "motivo": "Alta de artículo",
                        },
                    )

                if guardar_cambios(
                    preparar_stock(stock_nuevo),
                    preparar_movimientos(movimientos_nuevos),
                    "Artículo creado y compartido entre ambos stocks.",
                ):
                    st.rerun()

        if not stock.empty:
            st.markdown("### ✏️ Configuración del catálogo")
            st.caption(
                "Acá se editan datos maestros, mínimos, ubicaciones, lotes y vencimientos. "
                "Las cantidades de stock se modifican únicamente mediante movimientos, para conservar auditoría."
            )

            columnas_editor = [
                "id_articulo",
                "codigo",
                "articulo",
                "principio_activo",
                "categoria",
                "presentacion",
                "unidad",
                "proveedor",
                "costo_unitario",
                "requiere_frio",
                "medicamento_controlado",
                "activo",
                "stock_quirofano",
                "stock_minimo_quirofano",
                "ubicacion_quirofano",
                "lote_quirofano",
                "vencimiento_quirofano",
                "stock_farmacia",
                "stock_minimo_farmacia",
                "ubicacion_farmacia",
                "lote_farmacia",
                "vencimiento_farmacia",
                "observaciones",
            ]
            editor = stock[columnas_editor].copy()
            editor["vencimiento_quirofano"] = editor["vencimiento_quirofano"].apply(fecha_segura)
            editor["vencimiento_farmacia"] = editor["vencimiento_farmacia"].apply(fecha_segura)

            editado = st.data_editor(
                editor,
                use_container_width=True,
                hide_index=True,
                height=520,
                key="farmacia_editor_catalogo",
                disabled=["id_articulo", "stock_quirofano", "stock_farmacia"],
                column_config={
                    "id_articulo": st.column_config.TextColumn("ID", width="small"),
                    "codigo": st.column_config.TextColumn("Código", width="small"),
                    "articulo": st.column_config.TextColumn("Artículo", required=True, width="large"),
                    "principio_activo": st.column_config.TextColumn("Principio activo"),
                    "categoria": st.column_config.SelectboxColumn("Categoría", options=CATEGORIAS),
                    "costo_unitario": st.column_config.NumberColumn("Costo unitario", min_value=0.0, format="$ %.2f"),
                    "stock_quirofano": st.column_config.NumberColumn("Stock Qx", format="%.2f"),
                    "stock_minimo_quirofano": st.column_config.NumberColumn("Mínimo Qx", min_value=0.0, format="%.2f"),
                    "vencimiento_quirofano": st.column_config.DateColumn("Vencimiento Qx", format="DD/MM/YYYY"),
                    "stock_farmacia": st.column_config.NumberColumn("Stock Farmacia", format="%.2f"),
                    "stock_minimo_farmacia": st.column_config.NumberColumn("Mínimo Farmacia", min_value=0.0, format="%.2f"),
                    "vencimiento_farmacia": st.column_config.DateColumn("Vencimiento Farmacia", format="DD/MM/YYYY"),
                },
            )

            if st.button(
                "Guardar configuración del catálogo",
                type="primary",
                key="farmacia_guardar_catalogo",
                use_container_width=True,
            ):
                if editado["articulo"].astype(str).str.strip().eq("").any():
                    st.error("No puede haber artículos sin nombre.")
                elif editado["id_articulo"].duplicated().any():
                    st.error("Se detectaron IDs de artículo duplicados.")
                else:
                    stock_nuevo = stock.copy()
                    editado_limpio = editado.copy()
                    editado_limpio["vencimiento_quirofano"] = editado_limpio[
                        "vencimiento_quirofano"
                    ].apply(fecha_para_guardar)
                    editado_limpio["vencimiento_farmacia"] = editado_limpio[
                        "vencimiento_farmacia"
                    ].apply(fecha_para_guardar)
                    editado_limpio["ultima_actualizacion"] = datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )

                    for indice, fila_editada in editado_limpio.iterrows():
                        id_articulo = fila_editada["id_articulo"]
                        mascara = stock_nuevo["id_articulo"].eq(id_articulo)
                        if mascara.any():
                            for columna in editado_limpio.columns:
                                if columna in stock_nuevo.columns:
                                    stock_nuevo.loc[mascara, columna] = fila_editada[columna]

                    if guardar_cambios(
                        preparar_stock(stock_nuevo),
                        movimientos.copy(),
                        "Catálogo actualizado correctamente.",
                    ):
                        st.rerun()

    # =====================================================
    # HISTORIAL Y AUDITORÍA
    # =====================================================

    with st.expander("🕵️ Historial de movimientos y auditoría", expanded=False):
        if movimientos.empty:
            st.info("Todavía no hay movimientos registrados.")
        else:
            historial = movimientos.copy()
            historial["_fecha_dt"] = historial["fecha"].apply(fecha_segura)

            h1, h2, h3, h4 = st.columns([1.4, 1.2, 1.4, 2])
            fechas_validas = historial["_fecha_dt"].dropna()
            fecha_minima = fechas_validas.min().date() if not fechas_validas.empty else date.today()
            fecha_maxima = fechas_validas.max().date() if not fechas_validas.empty else date.today()
            desde = h1.date_input("Desde", value=fecha_minima, key="farmacia_hist_desde")
            hasta = h2.date_input("Hasta", value=fecha_maxima, key="farmacia_hist_hasta")
            sector_filtro = h3.selectbox(
                "Sector",
                ["Todos", "Quirófano", "Farmacia"],
                key="farmacia_hist_sector",
            )
            buscar_hist = h4.text_input(
                "Buscar artículo, responsable o motivo",
                key="farmacia_hist_buscar",
            ).strip().lower()

            historial = historial[
                historial["_fecha_dt"].notna()
                & (historial["_fecha_dt"].dt.date >= desde)
                & (historial["_fecha_dt"].dt.date <= hasta)
            ]
            if sector_filtro != "Todos":
                historial = historial[historial["sector"].eq(sector_filtro)]
            if buscar_hist:
                mascara = pd.Series(False, index=historial.index)
                for columna in ["articulo", "responsable", "motivo", "observaciones", "tipo"]:
                    mascara = mascara | historial[columna].astype(str).str.lower().str.contains(
                        buscar_hist, na=False, regex=False
                    )
                historial = historial[mascara]

            historial = historial.sort_values(
                ["_fecha_dt", "hora"], ascending=[False, False]
            )

            entradas = historial[
                historial["tipo"].isin(["Ingreso / compra", "Ajuste positivo", "Stock inicial"])
            ]["cantidad"].sum()
            salidas = historial[
                historial["tipo"].isin(["Consumo / salida", "Ajuste negativo"])
            ]["cantidad"].sum()
            transferencias = int(
                historial["tipo"].eq("Transferencia al otro stock").sum()
            )
            controles = int(historial["tipo"].eq("Control diario").sum())

            a1, a2, a3, a4 = st.columns(4)
            a1.metric("Entradas", formato_cantidad(entradas))
            a2.metric("Salidas", formato_cantidad(salidas))
            a3.metric("Transferencias", transferencias)
            a4.metric("Controles diarios", controles)

            tabla_historial = historial[
                [
                    "fecha",
                    "hora",
                    "sector",
                    "tipo",
                    "articulo",
                    "cantidad",
                    "stock_anterior",
                    "stock_nuevo",
                    "sector_destino",
                    "stock_destino_anterior",
                    "stock_destino_nuevo",
                    "lote",
                    "vencimiento",
                    "responsable",
                    "motivo",
                    "observaciones",
                ]
            ].copy()
            tabla_historial["fecha"] = tabla_historial["fecha"].apply(
                lambda valor: fecha_segura(valor).strftime("%d/%m/%Y")
                if pd.notna(fecha_segura(valor))
                else ""
            )
            tabla_historial["vencimiento"] = tabla_historial["vencimiento"].apply(
                lambda valor: fecha_segura(valor).strftime("%d/%m/%Y")
                if pd.notna(fecha_segura(valor))
                else ""
            )
            tabla_historial = tabla_historial.rename(
                columns={
                    "fecha": "Fecha",
                    "hora": "Hora",
                    "sector": "Sector",
                    "tipo": "Movimiento",
                    "articulo": "Artículo",
                    "cantidad": "Cantidad",
                    "stock_anterior": "Stock anterior",
                    "stock_nuevo": "Stock nuevo",
                    "sector_destino": "Destino",
                    "stock_destino_anterior": "Stock destino anterior",
                    "stock_destino_nuevo": "Stock destino nuevo",
                    "lote": "Lote",
                    "vencimiento": "Vencimiento",
                    "responsable": "Responsable",
                    "motivo": "Motivo",
                    "observaciones": "Observaciones",
                }
            )

            st.dataframe(
                tabla_historial,
                use_container_width=True,
                hide_index=True,
                height=520,
            )
            st.download_button(
                "⬇️ Descargar historial filtrado",
                data=tabla_historial.to_csv(index=False, encoding="utf-8-sig"),
                file_name=f"movimientos_farmacia_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv",
                key="farmacia_descargar_historial",
                use_container_width=True,
            )

    return preparar_stock(stock), preparar_movimientos(movimientos)
