import streamlit as st
import pandas as pd
import requests
import io
import base64
import altair as alt
import os
import openai
import wikipedia
import tempfile
from fpdf import FPDF
import vl_convert as vlc
import datetime
import qrcode
from PIL import Image
import matplotlib.pyplot as plt
import xlsxwriter



# Configura el idioma de Wikipedia a español
wikipedia.set_lang("es")

# Recupera tu API key desde Streamlit secrets
openai.api_key = st.secrets["OPENAI_API_KEY"]

# ——————————————————————————————————————————————————————
# Helper para Base64
# ——————————————————————————————————————————————————————
def _get_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

# ——————————————————————————————————————————————————————
# 1) Determina el tema y elige el logo
# ——————————————————————————————————————————————————————
theme = st.get_option("theme.base")  # "dark" o "light"
logo_path = "pdigital.png"
logo_b64  = _get_base64(logo_path)

# ——————————————————————————————————————————————————————
# 2) Inyecta el CSS correctamente (con <style>)
# ——————————————————————————————————————————————————————
st.markdown("""
<style>
  /* Hacemos relative el sidebar para fijar el logo */
  [data-testid="stSidebar"] { position: relative !important; }

  /* Posicionamos el logo en el tope */
  [data-testid="stSidebar"] .sidebar-logo {
    position: absolute;
    top: -50px;
    width: 100%;
    text-align: center;
    pointer-events: none;
  }
  [data-testid="stSidebar"] .sidebar-logo img {
    margin-top: 4px;
    width: 190px;
  }
</style>
""", unsafe_allow_html=True)

# ——————————————————————————————————————————————————————
# 3) Renderiza el logo
# ——————————————————————————————————————————————————————
st.sidebar.markdown(f"""
<div class="sidebar-logo">
  <img src="data:image/png;base64,{logo_b64}" alt="Logo PDigital"/>
</div>
""", unsafe_allow_html=True)



# ------------------------------------------
# Funciones
# ------------------------------------------
@st.cache_data(ttl=600)
def cargar_tablas_control():
    xls = pd.ExcelFile("Tablas Control.xlsx")
    df_mun = pd.read_excel(xls, sheet_name="Tablamun")
    df_dep = pd.read_excel(xls, sheet_name="Tabladep")
    df_per = pd.read_excel(xls, sheet_name="Periodos").rename(columns={"Personalizado.1": "periodo_label"})
    df_cuentas = pd.read_excel(xls, sheet_name="Tablacontrolingresos")
    return df_mun, df_dep, df_per, df_cuentas

@st.cache_data(ttl=600, show_spinner=False)
def obtener_ingresos_filtrados(codigo_entidad, periodo=None):
    codigo_entidad = int(float(codigo_entidad))
    base_url = "https://www.datos.gov.co/resource/22ah-ddsj.csv"
    where_clause = f"codigo_entidad='{codigo_entidad}'"
    if periodo:
        where_clause += f" AND periodo = '{periodo}'"
    params = {
        "$limit": 100000,
        "$where": where_clause
    }
    resp = requests.get(base_url, params=params, timeout=60)
    if resp.status_code != 200:
        st.error(f"Error al obtener los datos. Código {resp.status_code}: {resp.text}")
        return pd.DataFrame()
    return pd.read_csv(io.StringIO(resp.text))

@st.cache_data(ttl=600, show_spinner=False)
def obtener_datos_gastos(codigo_entidad, periodo):
    cols = [
        "periodo", "codigo_entidad", "nombre_entidad",
        "cuenta", "nombre_cuenta", "nom_seccion_presupuestal", "compromisos", "pagos", "obligaciones", "nom_vigencia_del_gasto",
        
    ]
    # Convertimos a string sin decimales para evitar errores
    codigo_entidad = str(int(float(codigo_entidad)))
    where = f"codigo_entidad='{codigo_entidad}' AND periodo='{periodo}'"
    params = {"$select": ",".join(cols), "$where": where, "$limit": 100000}
    try:
        r = requests.get("https://www.datos.gov.co/resource/4f7r-epif.csv", params=params, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        if df.empty or df.isna().all().all():
            return pd.DataFrame()
        return df
    except Exception as e:
        st.warning(f"No se pudo obtener la información de la API: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=600, show_spinner=False)
def obtener_ejecucion_ingresos(codigo_entidad, periodo):
    codigo_entidad = str(int(float(codigo_entidad)))
    url = "https://www.datos.gov.co/resource/9axr-9gnb.csv"
    params = {
        "$where": f"codigo_entidad='{codigo_entidad}' AND periodo='{periodo}'",
        "$limit": 100000
    }
    try:
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        if df.empty or df.isna().all().all():
            return pd.DataFrame()
        return df
    except Exception as e:
        st.warning(f"No se pudo obtener datos de ejecución de ingresos: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=86400)
def obtener_resumen_wikipedia(municipio: str, departamento: str) -> str:
    query = f"{municipio}, {departamento}"
    try:
        # Busca el título más relevante
        titulo = wikipedia.search(query, results=1)[0]
        # Extrae un extracto breve
        resumen = wikipedia.summary(titulo, sentences=3, auto_suggest=False)
        return resumen
    except Exception as e:
        return f"No se encontró información en Wikipedia: {e}"


# ------------------------------------------
# Página principal
# ------------------------------------------
df_mun, df_dep, df_per, df_cuentas = cargar_tablas_control()

pagina = st.sidebar.selectbox(
    "Selecciona una página:",
    ["Programación de Ingresos", "Comparativa Per Cápita", "Ejecución de Gastos", "Ejecución de Ingresos"]
)


if pagina == "Programación de Ingresos":
    st.title("Programación de Ingresos")

    nivel = st.sidebar.selectbox("Nivel geográfico:", ["Municipios", "Gobernaciones"])
    if nivel == "Municipios":
        deps = sorted(df_mun["departamento"].dropna().astype(str).unique())
        dep = st.sidebar.selectbox("Departamento:", deps)
        df_ent = df_mun[df_mun["departamento"] == dep]
        label = "Municipio"
    else:
        df_ent = df_dep
        label = "Gobernación"

    mun_dict = dict(zip(df_ent['nombre_entidad'], df_ent['codigo_entidad']))
    ent = st.sidebar.selectbox(f"{label}:", list(mun_dict.keys()))
    cod_ent = mun_dict[ent]

    # Selección de periodo (filtrado por año y trimestres completos)
    import datetime
    today = datetime.date.today()
    current_year = today.year
    current_month = today.month
    current_quarter = (current_month - 1) // 3 + 1
    last_full_quarter = current_quarter - 1 if current_quarter > 1 else 0

    # Preparamos strings de periodo
    df_per['periodo_str'] = df_per['periodo'].astype(str).str.zfill(8)
    df_per['year'] = df_per['periodo_str'].str[:4].astype(int)
    df_per['month'] = df_per['periodo_str'].str[4:6].astype(int)

    # Filtrar sólo años hasta el actual
    df_per_filt = df_per[df_per['year'] <= current_year]

    # Para el año actual, sólo hasta el último trimestre completo
    if last_full_quarter > 0:
        df_per_filt = df_per_filt[~(
            (df_per_filt['year'] == current_year) &
            (df_per_filt['month'] > last_full_quarter * 3)
        )]
    else:
        df_per_filt = df_per_filt[df_per_filt['year'] < current_year]

    # Ordenamos y armamos el dropdown
    df_per_filt = df_per_filt.sort_values('periodo')
    per_dict = dict(zip(df_per_filt['periodo_label'], df_per_filt['periodo']))
    per_lab = st.sidebar.selectbox("Período:", list(per_dict.keys()), key="per_prog")
    per     = str(per_dict[per_lab])

    if st.sidebar.button("Cargar datos de ingresos"):
        with st.spinner("Cargando datos..."):
            st.session_state['df_ingresos'] = obtener_ingresos_filtrados(cod_ent, per)

    if 'df_ingresos' in st.session_state:
        df_i = st.session_state['df_ingresos']

        with st.expander("Datos brutos", expanded=False):
            st.dataframe(df_i.drop(columns=['presupuesto_inicial', 'presupuesto_definitivo'], errors='ignore'), use_container_width=True)

        codigos = ["1", "1.1", "1.1.01.01.200", "1.1.01.02", "1.1.01.02.200", "1.1.01.02.300", "1.1.02.06.001", "1.2.06", "1.2.07", "1.1.01.01", "1.1.01.01.200", "1.1.01.02.200.01"]
        df_fil = df_i[df_i['ambito_codigo'].astype(str).isin(codigos)] if 'ambito_codigo' in df_i.columns else df_i.copy()

        resumen = df_fil.copy()
        resumen['Presupuesto Inicial']   = pd.to_numeric(resumen.get('cod_detalle_sectorial', 0), errors='coerce') / 1e6
        resumen['Presupuesto Definitivo']= pd.to_numeric(resumen.get('nom_detalle_sectorial', 0), errors='coerce') / 1e6
        resumen = resumen.rename(columns={
            'ambito_codigo': 'Ámbito Código',
            'ambito_nombre': 'Ámbito Nombre'
        })

        # <-- Aquí ordenamos por Ámbito Código ascendente
        resumen = resumen.sort_values('Ámbito Código', ascending=True)

        # Luego damos formato monetario
        resumen['Presupuesto Inicial']   = resumen['Presupuesto Inicial']  .apply(lambda x: f"$ {x:,.2f}")
        resumen['Presupuesto Definitivo']= resumen['Presupuesto Definitivo'] .apply(lambda x: f"$ {x:,.2f}")

        st.subheader("Ingresos filtrados (millones de pesos)")
        st.dataframe(
            resumen[['Ámbito Código','Ámbito Nombre','Presupuesto Inicial','Presupuesto Definitivo']],
            use_container_width=True, hide_index=True
        )

        total_presupuesto = pd.to_numeric(df_fil[df_fil['ambito_codigo'].astype(str) == '1']['nom_detalle_sectorial'], errors='coerce').sum() / 1e6
        st.subheader("Ingreso total - Presupuesto definitivo (millones de pesos)")
        st.metric(label="Total", value=f"$ {total_presupuesto:,.2f}")


        # Histórico nominal vs real
        st.subheader("Histórico Ingresos nominal vs real (millones de pesos)")
        df_hist = obtener_ingresos_filtrados(cod_ent)
        df_hist = df_hist[df_hist['ambito_nombre'].str.upper() == 'INGRESOS']

        df_hist['periodo_dt'] = pd.to_datetime(df_hist['periodo'], format='%Y%m%d', errors='coerce')
        df_hist['year'] = df_hist['periodo_dt'].dt.year
        df_hist['md'] = df_hist['periodo_dt'].dt.strftime('%m%d')

        registros = []
        current = df_hist['year'].max()
        for yr, grp in df_hist.groupby('year'):
            if yr != current:
                q4 = grp[grp['md'] == '1201']
                if not q4.empty:
                    registros.append(q4.loc[q4['periodo_dt'].idxmax()])
            else:
                registros.append(grp.loc[grp['periodo_dt'].idxmax()])

        df_sel = pd.DataFrame(registros).sort_values('periodo_dt')
        df_sel['presupuesto_definitivo'] = pd.to_numeric(df_sel['nom_detalle_sectorial'], errors='coerce')
        df_sel['Ingresos Nominales'] = df_sel['presupuesto_definitivo'] / 1e6

        ipc_map = {2021: 111.41, 2022: 126.03, 2023: 137.09, 2024: 144.88}
        df_sel['ipc'] = df_sel['periodo_dt'].dt.year.map(ipc_map)
        df_sel['Ingresos Reales'] = df_sel['Ingresos Nominales'] / df_sel['ipc'] * 100

        df_long = df_sel.melt(id_vars=['periodo_dt'], 
                              value_vars=['Ingresos Nominales', 'Ingresos Reales'],
                              var_name='Tipo', value_name='Monto')

        min_valor = df_long['Monto'].min() * 0.95

        chart = alt.Chart(df_long).mark_line(point=True).encode(
            x=alt.X('year(periodo_dt):O', title='Periodo'),
            y=alt.Y('Monto:Q', title='Ingresos Q4 (millones)', scale=alt.Scale(domainMin=min_valor), axis=alt.Axis(format='$,.0f')),
            color='Tipo:N',
            tooltip=['periodo_dt', 'Tipo', alt.Tooltip('Monto:Q', format='$,.0f')]
        ).properties(width=700, height=350)

        st.altair_chart(chart, use_container_width=True)

                                       # ————— Exportar a Excel —————
        st.markdown("")

        output = io.BytesIO()

        # 1. Preparar hoja de datos brutos
        df_brutos_export = df_i.drop(columns=['presupuesto_inicial', 'presupuesto_definitivo'], errors='ignore').copy()
        df_brutos_export = df_brutos_export.rename(columns={
            'cod_detalle_sectorial': 'presupuestoinicial',
            'nom_detalle_sectorial': 'presupuestodefinitivo'
        })

        # Ordenar por ambito_codigo ascendente
        if 'ambito_codigo' in df_brutos_export.columns:
            df_brutos_export = df_brutos_export.sort_values('ambito_codigo', ascending=True)

        # 2. Preparar hoja resumen
        df_resumen_export = resumen[['Ámbito Código','Ámbito Nombre','Presupuesto Inicial','Presupuesto Definitivo']]

        # 3. Preparar hoja histórico nominal vs real
        df_hist_export = df_sel[['periodo_dt', 'Ingresos Nominales', 'Ingresos Reales']]

        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            wb  = writer.book

            # Datos Brutos
            df_brutos_export.to_excel(writer, index=False, sheet_name='Datos Brutos')
            ws0 = writer.sheets['Datos Brutos']
            # formato de moneda solo para las dos columnas de presupuesto
            currency_fmt = wb.add_format({'num_format': '$#,##0.00'})
            for col_name in ['presupuestoinicial','presupuestodefinitivo']:
                if col_name in df_brutos_export.columns:
                    col_idx = df_brutos_export.columns.get_loc(col_name)
                    ws0.set_column(col_idx, col_idx, None, currency_fmt)

            # Resumen (mantener como antes)
            df_resumen_export.to_excel(writer, index=False, sheet_name='Resumen')
            ws1 = writer.sheets['Resumen']
            ws1.set_column(2, 3, None, currency_fmt)

            # Histórico
            df_hist_export.to_excel(writer, index=False, sheet_name='Histórico')
            ws2 = writer.sheets['Histórico']
            ws2.set_column(1, 2, None, currency_fmt)

        st.download_button(
            label="Excel",
            data=output.getvalue(),
            file_name=f"programacion_ingresos_{ent}_{per}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )





elif pagina == "Comparativa Per Cápita":
    st.title("Programación de Ingresos - Comparativa Per Cápita")

    import tempfile
    from fpdf import FPDF

    def format_cop(x):
        try:
            return f"$ {float(x):,.0f}"
        except:
            return "$ 0"

    # --- Selección de entidad y periodo ---
    nivel = st.sidebar.selectbox("Nivel geográfico:", ["Municipios", "Gobernaciones"], key="niv_geo_comp")
    # Configurar DF según nivel
    if nivel == "Municipios":
        df_entities = df_mun.copy()
        label = "Municipio"
    else:
        df_entities = df_dep.copy()
        label = "Departamento"
    # Selección de entidad
    deps = sorted(df_entities["departamento" if nivel=="Municipios" else "region"].dropna().astype(str).unique()) if "departamento" in df_entities.columns else []
    if nivel == "Municipios":
        dep = st.sidebar.selectbox("Departamento:", deps, key="dep_comp")
        df_ent = df_entities[df_entities["departamento"] == dep]
    else:
        # Para gobernaciones no filtramos por departamento
        dep = None
        df_ent = df_entities
    ent = st.sidebar.selectbox(f"{label}:", df_ent['nombre_entidad'].dropna().astype(str).unique(), key="ent_comp")
    codigo_entidad = dict(zip(df_ent['nombre_entidad'], df_ent['codigo_entidad']))[ent]

    # Selección de periodo (filtrado por año y trimestres completos)
    import datetime
    today = datetime.date.today()
    current_year = today.year
    current_month = today.month
    current_quarter = (current_month - 1) // 3 + 1
    last_full_quarter = current_quarter - 1 if current_quarter > 1 else 0
    # Preparar strings de periodo
    df_per['periodo_str'] = df_per['periodo'].astype(str).str.zfill(8)
    df_per['year'] = df_per['periodo_str'].str[:4].astype(int)
    df_per['month'] = df_per['periodo_str'].str[4:6].astype(int)
    # Filtrar años hasta el actual
    df_per_filt = df_per[df_per['year'] <= current_year]
    # Para el año actual, solo hasta el último trimestre completo
    if last_full_quarter > 0:
        df_per_filt = df_per_filt[~((df_per_filt['year'] == current_year) & (df_per_filt['month'] > last_full_quarter * 3))]
    else:
        df_per_filt = df_per_filt[df_per_filt['year'] < current_year]
    df_per_filt = df_per_filt.sort_values('periodo')
    per_dict = dict(zip(df_per_filt['periodo_label'], df_per_filt['periodo']))
    per_lab = st.sidebar.selectbox("Período:", list(per_dict.keys()), key="per_comp")
    periodo = str(per_dict[per_lab])

    st.markdown("---")
    st.header(f"Comparativa per cápita ({label})")
    cuenta_sel = st.selectbox(
        "Cuenta para comparar:",
        df_cuentas['Nombre de la Cuenta'].dropna().astype(str).unique(),
        key="cuenta_comparativa"
    )

    # Ejecutar comparativa
    if st.button("Ejecutar comparativa", key="btn_ejecutar_comp"):
        # Limpiar informe previo
        if 'informe' in st.session_state:
            del st.session_state['informe']
        # Obtener datos
        ambito_code = df_cuentas.loc[df_cuentas['Nombre de la Cuenta']==cuenta_sel,'Código Completo'].iloc[0]
        resp = requests.get(
            "https://www.datos.gov.co/resource/22ah-ddsj.csv",
            params={"$limit":100000, "$where": f"periodo='{periodo}' AND ambito_codigo='{ambito_code}'"},
            timeout=60
        )
        if resp.status_code != 200:
            st.warning("No se encontraron datos para esta cuenta.")
            st.stop()
        df_all = pd.read_csv(io.StringIO(resp.text))
        df_all['presupuesto_definitivo'] = pd.to_numeric(df_all['nom_detalle_sectorial'], errors='coerce')
        # Sumar por entidad
        df_sum = df_all.groupby('codigo_entidad', as_index=False)['presupuesto_definitivo'].sum()
        # Filtrar población por año del periodo
        year = int(periodo[:4])
        df_pop = df_entities[df_entities['año'] == year][['codigo_entidad','nombre_entidad','poblacion','categoria']]
        # Merge con población específica del año
        df_sum = df_sum.merge(
            df_pop,
            on='codigo_entidad', how='left'
        ).dropna(subset=['poblacion'])
        df_sum['per_capita'] = df_sum['presupuesto_definitivo'] / df_sum['poblacion']
        sel = df_sum[df_sum['codigo_entidad'] == codigo_entidad]
        if sel.empty:
            st.warning(f"No hay datos para la cuenta en este {label.lower()}.")
            st.stop()
        # Guardar en state
        st.session_state.update({
            'entity': ent,
            'label': label,
            'cat': sel['categoria'].iloc[0],
            'pc_sel': sel['per_capita'].iloc[0],
            'pc_cat': df_sum[df_sum['categoria']==sel['categoria'].iloc[0]]['per_capita'].mean(),
            'pc_all': df_sum['per_capita'].mean(),
            'periodo': periodo
        })
        # Preparar datos de plot
        df_plot = pd.DataFrame({
            'Tipo': [ent, f"Promedio Cat. ({st.session_state['cat']})", 'Promedio País'],
            'Value': [st.session_state['pc_sel'], st.session_state['pc_cat'], st.session_state['pc_all']]
        })
        chart = alt.Chart(df_plot).mark_bar(cornerRadius=4).encode(
    x=alt.X(
        'Tipo:N',
        title='',
        axis=alt.Axis(
            labelAngle=0,
            labelAlign='center',
            labelBaseline='middle',
            labelLimit=200,
            titleAngle=0
        )
    ),
    y=alt.Y(
        'Value:Q',
        title='COP per cápita',
        axis=alt.Axis(
            format='$,.0f',
            titleAngle=0,
            titleAlign='right'
        )
    ),
    color=alt.condition(
        alt.datum.Tipo == ent,
        alt.value('orange'),
        alt.value('steelblue')
    ),
    tooltip=[alt.Tooltip('Tipo:N'), alt.Tooltip('Value:Q', format='$,.0f')]
).properties(
    width=800,
    height=400
)
        # Guardar para mostrar y PDF
        st.session_state['chart'] = chart
        df_plot['COP per cápita'] = df_plot['Value'].map(lambda v: f"$ {v:,.0f}")
        st.session_state['df_bar_fmt'] = df_plot[['Tipo','COP per cápita']]
        df_cat = (
            df_sum[df_sum['categoria']==st.session_state['cat']][
                ['nombre_entidad','per_capita','presupuesto_definitivo']
            ]
            .rename(columns={'nombre_entidad': label, 'per_capita':'Per cápita','presupuesto_definitivo':'Valor Absoluto (millones)'})
        )
        df_cat['Valor Absoluto (millones)'] /= 1e6
        df_cat['Per cápita'] = df_cat['Per cápita'].map(lambda v: f"$ {v:,.0f}")
        df_cat['Valor Absoluto (millones)'] = df_cat['Valor Absoluto (millones)'].map(format_cop)
        st.session_state['df_cat'] = df_cat.sort_values('Per cápita', ascending=False)

    # Mostrar resultados si existen
    if 'chart' in st.session_state:
        st.subheader(f"Gráfico comparativo ({st.session_state['label']})")
        st.altair_chart(st.session_state['chart'], use_container_width=True)
        st.subheader(f"Valores per cápita ({st.session_state['label']})")
        st.dataframe(st.session_state['df_bar_fmt'], use_container_width=True, hide_index=True)
        st.subheader(f"Valores per cápita por {st.session_state['label'].lower()} en misma categoría")
        st.dataframe(st.session_state['df_cat'], use_container_width=True, hide_index=True)

    # ————— Exportar datos a Excel (previo al informe) —————
    if 'df_bar_fmt' in st.session_state and 'df_cat' in st.session_state:
        # ————— Exportar datos a Excel (previo al informe) —————
        st.markdown("")

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            st.session_state['df_bar_fmt'].to_excel(writer, index=False, sheet_name='Resumen Comparativa')
            st.session_state['df_cat'].to_excel(writer, index=False, sheet_name=f"{label}s Categoría")

        st.download_button(
            label="Excel",
            data=output.getvalue(),
            file_name=f"comparativa_percapita_{ent}_{periodo}_{st.session_state['cuenta_comparativa']}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )



    # Generar informe y PDF
    if 'chart' in st.session_state:
        if st.button("Generar Informe y PDF"):
            resumen = obtener_resumen_wikipedia(st.session_state['entity'], None)
            prompt = f"""
Actúa como un economista especializado en desarrollo territorial colombiano. A continuación se presenta un extracto de Wikipedia sobre {st.session_state['entity']}, que puede contener información útil sobre su economía o contexto territorial:

{resumen}

Redacta un informe breve y técnico, compuesto por dos partes: introducción general y análisis del indicador. El texto debe estar escrito como un cuerpo narrativo fluido, sin subtítulos ni viñetas, y con tono profesional.

Primero, presenta el contexto básico del municipio o departamento: ubicación, importancia regional, dinámica económica y aspectos territoriales relevantes. Usa solo la información del resumen si está relacionada con economía, desarrollo productivo o estructura institucional. Si no hay información útil en el resumen, escribe una breve descripción general en función del conocimiento que tengas sobre el territorio.

Después, describe los resultados del indicador per cápita '{cuenta_sel}' para {st.session_state['entity']}. Aclara explícitamente que este valor no representa ingreso por persona, sino que es una medida relativa que permite comparar el desempeño fiscal o recaudatorio entre entidades. Menciona si el valor observado para {st.session_state['entity']} (COP {st.session_state['pc_sel']:,.0f}) está por encima o por debajo del promedio de su categoría (COP {st.session_state['pc_cat']:,.0f}) y del promedio nacional (COP {st.session_state['pc_all']:,.0f}). Interpreta su posición relativa sin emitir juicios de valor ni incluir recomendaciones. No hagas suposiciones sobre informalidad, debilidad institucional o problemas de recaudo.

Evita sugerencias, recomendaciones, o valoraciones implícitas sobre si el resultado es bueno o malo. No asocies el indicador con ingreso per cápita real. Escribe con claridad, coherencia y precisión técnica.
"""

            try:
                resp = openai.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role":"system","content":"Eres un economista experto en desarrollo territorial en Colombia."},
                        {"role":"user","content":prompt}
                    ], max_tokens=800, temperature=0.7
                )
                st.session_state['informe'] = resp.choices[0].message.content.strip()
            except openai.error.RateLimitError:
                st.session_state['informe'] = 'Error: límite API excedido.'

    # Mostrar informe y PDF
if pagina == "Comparativa Per Cápita" and 'informe' in st.session_state:
    st.markdown(st.session_state['informe'])
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(True, 15)

    # 1) Logo
    pdf.image("pdigitalazul.png", x=10, y=8, w=60)
    pdf.ln(20)

    # 2) Título de variable
    pdf.set_font("Arial", "B", 14)
    pdf.set_x(10)
    pdf.cell(0, 8, st.session_state['cuenta_comparativa'], ln=True, align="C")
    pdf.ln(5)

    # 3) Informe
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, "Informe", ln=True)
    pdf.set_font("Arial", "", 10)
    for line in st.session_state['informe'].split("\n"):
        pdf.multi_cell(0, 5, line)
    pdf.ln(5)

    # 4) Gráfico con Matplotlib para el PDF
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, f"Comparativa Per Cápita - {st.session_state['entity']}", ln=True, align="L")
    pdf.ln(5)

    df_plot = st.session_state['df_bar_fmt'].copy()
    tipos = [r for r in df_plot['Tipo']]
    valores = [int(r.replace("$","").replace(" ","").replace(",","")) for r in df_plot['COP per cápita']]

    fig, ax = plt.subplots(figsize=(8, 3))
    ax.bar(tipos, valores)
    ax.set_ylabel("COP per cápita")
    ax.set_ylim(0, max(valores) * 1.1)
    ax.tick_params(axis="x", labelrotation=30)
    plt.setp(ax.get_xticklabels(), ha="right")
    ax.yaxis.set_major_formatter(lambda x, pos: f"$ {int(x):,}")
    fig.tight_layout()

    tmp_fig = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    fig.savefig(tmp_fig.name, dpi=150)
    plt.close(fig)
    pdf.image(tmp_fig.name, x=10, w=190)
    pdf.ln(20)

    # 5) Tablas
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, "Valores per cápita", ln=True)
    pdf.set_font("Arial", "", 10)
    for _, r in st.session_state['df_bar_fmt'].iterrows():
        pdf.cell(0, 6, f"{r['Tipo']}: {r['COP per cápita']}", ln=True)
    pdf.ln(5)

    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, f"Per cápita {st.session_state['label'].lower()}s categoría {st.session_state['cat']}", ln=True)
    pdf.set_font("Arial", "B", 10)
    pdf.cell(80, 6, st.session_state['label'], 1)
    pdf.cell(40, 6, "Per cápita", 1)
    pdf.cell(60, 6, "Valor Absoluto (en millones COP)", 1, ln=True)
    pdf.set_font("Arial", "", 10)
    for _, r in st.session_state['df_cat'].iterrows():
        pdf.cell(80, 6, r[st.session_state['label']], 1)
        pdf.cell(40, 6, r['Per cápita'], 1)
        pdf.cell(60, 6, r['Valor Absoluto (millones)'], 1, ln=True)

    # 6) Texto + QR
    pdf.ln(10)
    pdf.set_font("Arial", "I", 10)
    pdf.set_x(10)
    pdf.cell(0, 8, "¿Quieres llevar más potencia al desarrollo de tu territorio? Contáctanos", ln=True)

    qr = qrcode.QRCode(box_size=4, border=1)
    qr.add_data("https://potencia.com.co/")
    qr.make(fit=True)
    img_qr = qr.make_image(fill_color="#262C60", back_color="white")
    buf = io.BytesIO(); img_qr.save(buf, format="PNG"); buf.seek(0)
    tmp_qr = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_qr.write(buf.read()); tmp_qr.close()

    qr_w = 40
    x_qr = pdf.l_margin
    y_qr = pdf.get_y() + 2
    pdf.image(tmp_qr.name, x=x_qr, y=y_qr, w=qr_w)
    pdf.link(x_qr, y_qr, qr_w, qr_w, "https://potencia.com.co/")

    # 7) Descargar
    data = pdf.output(dest="S").encode("latin-1")
    st.download_button(
        "📄 Descargar Informe completo en PDF",
        data=data,
        file_name=f"reporte_comparativa_{st.session_state['entity']}_{st.session_state['cuenta_comparativa']}.pdf",
        mime="application/pdf"
    )






# ===============================
# Página: Ejecución de Gastos
# ===============================
elif pagina == "Ejecución de Gastos":
    st.title("Ejecución de Gastos")

    # ════════════════════════════════════════════════════════════════════════
    # HELPERS GENERALES
    # ════════════════════════════════════════════════════════════════════════
    def fmt_mm(valor_pesos):
        try:
            valor_pesos = float(valor_pesos)
        except Exception:
            valor_pesos = 0.0
        m = valor_pesos / 1e6
        if abs(m) >= 1000:
            return f"$ {m/1000:,.1f} MM"
        return f"$ {m:,.1f} M"

    def pct(valor, base):
        try:
            valor = float(valor)
            base = float(base)
            return round(valor / base * 100, 1) if base > 0 else 0.0
        except Exception:
            return 0.0

    def safe_div(num, den):
        try:
            num = float(num)
            den = float(den)
            return round(num / den * 100, 1) if den > 0 else 0.0
        except Exception:
            return 0.0

    def format_cop(x):
        try:
            return f"$ {float(x):,.0f}"
        except Exception:
            return "$ 0"

    def limpiar_texto(x):
        return "" if pd.isna(x) else str(x).strip()

    # ════════════════════════════════════════════════════════════════════════
    # SIDEBAR: ENTIDAD Y PERIODO
    # ════════════════════════════════════════════════════════════════════════
    nivel = st.sidebar.selectbox(
        "Selecciona el nivel geográfico:",
        ["Municipios", "Gobernaciones"],
        key="niv_gastos"
    )

    if nivel == "Municipios":
        departamentos = sorted(df_mun["departamento"].dropna().astype(str).unique())
        dep_sel = st.sidebar.selectbox("Departamento:", departamentos, key="dep_gastos")
        df_entidades = df_mun[df_mun["departamento"] == dep_sel]
        label_ent = "Municipio"
    else:
        df_entidades = df_dep
        label_ent = "Gobernación"

    ent_sel = st.sidebar.selectbox(
        f"{label_ent}:",
        df_entidades["nombre_entidad"].dropna().astype(str).unique().tolist(),
        key="ent_gastos"
    )

    codigo_ent = df_entidades.loc[
        df_entidades["nombre_entidad"] == ent_sel,
        "codigo_entidad"
    ].iloc[0]

    import datetime
    today = datetime.date.today()
    current_year = today.year
    current_month = today.month
    current_quarter = (current_month - 1) // 3 + 1
    last_full_quarter = current_quarter - 1 if current_quarter > 1 else 0

    df_per["periodo_str"] = df_per["periodo"].astype(str).str.zfill(8)
    df_per["year"] = df_per["periodo_str"].str[:4].astype(int)
    df_per["month"] = df_per["periodo_str"].str[4:6].astype(int)

    df_per_filt = df_per[df_per["year"] <= current_year].copy()
    if last_full_quarter > 0:
        df_per_filt = df_per_filt[~(
            (df_per_filt["year"] == current_year) &
            (df_per_filt["month"] > last_full_quarter * 3)
        )]
    else:
        df_per_filt = df_per_filt[df_per_filt["year"] < current_year]

    df_per_filt = df_per_filt.sort_values("periodo")
    per_dict = dict(zip(df_per_filt["periodo_label"], df_per_filt["periodo"]))
    per_lab = st.sidebar.selectbox("Período:", list(per_dict.keys()), key="per_gastos")
    periodo = str(per_dict[per_lab])

    if st.sidebar.button("Cargar datos de gastos", key="btn_cargar_gastos"):
        with st.spinner("Obteniendo datos desde la API..."):
            df_gastos = obtener_datos_gastos(codigo_ent, periodo)
            st.session_state["df_gastos"] = df_gastos

    if "df_gastos" not in st.session_state:
        st.info("Selecciona una entidad, un período y pulsa 'Cargar datos de gastos'.")
        st.stop()

    df_raw = st.session_state["df_gastos"].copy()
    if df_raw.empty:
        st.warning(f"No se encontraron datos de gastos para la entidad '{ent_sel}' y período '{per_lab}'.")
        st.stop()

    with st.expander("Datos brutos", expanded=False):
        st.dataframe(df_raw, use_container_width=True)

    # ════════════════════════════════════════════════════════════════════════
    # LIMPIEZA INICIAL
    # ════════════════════════════════════════════════════════════════════════
    columnas_necesarias = [
        "cuenta", "nombre_cuenta", "nom_seccion_presupuestal",
        "compromisos", "obligaciones", "pagos", "nom_vigencia_del_gasto"
    ]
    faltantes = [c for c in columnas_necesarias if c not in df_raw.columns]
    if faltantes:
        st.error(f"Faltan columnas necesarias en la base de gastos: {faltantes}")
        st.stop()

    for col in ["compromisos", "obligaciones", "pagos"]:
        df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce").fillna(0.0)

    df_raw["cuenta"] = df_raw["cuenta"].astype(str).str.strip()
    df_raw["nombre_cuenta"] = df_raw["nombre_cuenta"].apply(limpiar_texto)
    df_raw["nom_seccion_presupuestal"] = df_raw["nom_seccion_presupuestal"].apply(limpiar_texto)
    df_raw["vigencia_norm"] = (
        df_raw["nom_vigencia_del_gasto"].fillna("").astype(str).str.strip().str.upper()
    )

    # ════════════════════════════════════════════════════════════════════════
    # CONTROLES ANALÍTICOS
    # ════════════════════════════════════════════════════════════════════════
    metricas = {"Compromisos": "compromisos", "Obligaciones": "obligaciones", "Pagos": "pagos"}
    metrica_label = st.sidebar.selectbox("Métrica principal:", list(metricas.keys()), index=0, key="meta_gastos")
    metrica = metricas[metrica_label]

    vigs = sorted([v for v in df_raw["vigencia_norm"].dropna().unique() if str(v).strip() != ""])
    if "VIGENCIA ACTUAL" in vigs:
        vigencias_disponibles = ["VIGENCIA ACTUAL"] + [v for v in vigs if v != "VIGENCIA ACTUAL"]
    else:
        vigencias_disponibles = vigs if vigs else ["VIGENCIA ACTUAL"]

    vigencia_analisis = st.sidebar.selectbox(
        "Vigencia para análisis principal:", vigencias_disponibles, index=0, key="vig_main_gastos"
    )

    # ════════════════════════════════════════════════════════════════════════
    # BASE AGREGADA Y ÁRBOL JERÁRQUICO
    # ════════════════════════════════════════════════════════════════════════
    base = df_raw.groupby(
        ["vigencia_norm", "cuenta", "nombre_cuenta"], as_index=False
    )[["compromisos", "obligaciones", "pagos"]].sum()

    def nivel_cuenta(c):
        return len(str(c).split("."))

    def cuenta_padre(c):
        partes = str(c).rsplit(".", 1)
        return partes[0] if len(partes) > 1 else None

    base["nivel_cuenta"] = base["cuenta"].apply(nivel_cuenta)
    base["cuenta_padre"] = base["cuenta"].apply(cuenta_padre)

    hijas_count = (
        base.dropna(subset=["cuenta_padre"])
        .groupby(["vigencia_norm", "cuenta_padre"], as_index=False)["cuenta"]
        .count()
        .rename(columns={"cuenta_padre": "cuenta", "cuenta": "n_hijas_inmediatas"})
    )

    base = base.merge(hijas_count, how="left", on=["vigencia_norm", "cuenta"])
    base["n_hijas_inmediatas"] = base["n_hijas_inmediatas"].fillna(0).astype(int)
    base["tiene_hijas"] = base["n_hijas_inmediatas"] > 0
    base["es_hoja"] = ~base["tiene_hijas"]

    def valor_exacto(df, codigo, col_metrica, vigencia="VIGENCIA ACTUAL"):
        temp = df[(df["vigencia_norm"] == vigencia) & (df["cuenta"] == codigo)]
        return float(temp[col_metrica].sum()) if not temp.empty else 0.0

    def fila_exacta(df, codigo, vigencia="VIGENCIA ACTUAL"):
        return df[(df["vigencia_norm"] == vigencia) & (df["cuenta"] == codigo)].copy()

    def hijas_inmediatas(df, codigo_padre, vigencia="VIGENCIA ACTUAL"):
        return df[(df["vigencia_norm"] == vigencia) & (df["cuenta_padre"] == codigo_padre)].copy()

    total_gasto = valor_exacto(base, "2", metrica, vigencia_analisis)
    funcionamiento = valor_exacto(base, "2.1", metrica, vigencia_analisis)
    deuda = valor_exacto(base, "2.2", metrica, vigencia_analisis)
    inversion = valor_exacto(base, "2.3", metrica, vigencia_analisis)

    if total_gasto == 0:
        hijos_2 = hijas_inmediatas(base, "2", vigencia_analisis)
        if not hijos_2.empty:
            total_gasto = float(hijos_2[metrica].sum())
            st.warning(
                "No se encontró la cuenta exacta '2' para el total de gastos. "
                "Se usó como respaldo la suma de sus hijas inmediatas. Revise la estructura de cuentas."
            )

    # ════════════════════════════════════════════════════════════════════════
    # FUNCIONES VISUALES Y TABLAS ROBUSTAS
    # ════════════════════════════════════════════════════════════════════════
    col_valor_tabla = f"Valor seleccionado ({metrica_label})"

    def render_card(titulo, valor, porcentaje, color, extra_text=None):
        extra_html = f"<div style='font-size:11px;color:#aaa;margin-top:4px;line-height:1.35;'>{extra_text}</div>" if extra_text else ""
        st.markdown(f"""
        <div style="background:#1e1e2e;border-left:4px solid {color};border-radius:10px;
                    padding:16px 18px;margin:6px 0;">
            <div style="font-size:11px;color:#aaa;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;">{titulo}</div>
            <div style="font-size:24px;font-weight:700;color:#fff;margin-bottom:6px;">{fmt_mm(valor)}</div>
            <div style="font-size:13px;color:{color};font-weight:600;">{porcentaje}% del gasto total</div>
            {extra_html}
        </div>
        """, unsafe_allow_html=True)

    def preparar_df_rubro(df, denominador_total, denominador_grupo=None, nombre_pct_grupo=None, top_n=None):
        df = df.copy()
        if df.empty:
            return df
        for c in ["compromisos", "obligaciones", "pagos"]:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
        df["valor_metrica"] = pd.to_numeric(df[metrica], errors="coerce").fillna(0.0)
        df = df[df["valor_metrica"] > 0].copy()
        if df.empty:
            return df
        df["pct_total"] = df["valor_metrica"].apply(lambda x: pct(x, denominador_total))
        df["pct_obligado_comprometido"] = df.apply(lambda row: safe_div(row["obligaciones"], row["compromisos"]), axis=1)
        df["pct_pagado_obligado"] = df.apply(lambda row: safe_div(row["pagos"], row["obligaciones"]), axis=1)
        df["pct_pagado_comprometido"] = df.apply(lambda row: safe_div(row["pagos"], row["compromisos"]), axis=1)
        if denominador_grupo is not None and nombre_pct_grupo is not None:
            df["pct_grupo"] = df["valor_metrica"].apply(lambda x: pct(x, denominador_grupo))
        df["nombre_corto"] = df["nombre_cuenta"].astype(str).str.upper().str.slice(0, 60)
        df["valor_millones"] = df["valor_metrica"] / 1e6
        df["valor_miles_millones"] = df["valor_metrica"] / 1e9
        df = df.sort_values("valor_metrica", ascending=False).reset_index(drop=True)
        if top_n is not None:
            df = df.head(top_n).reset_index(drop=True)
        return df

    def render_cards_top(df, color, denominador_total, denominador_grupo=None, etiqueta_grupo=None, n_cols=3):
        if df.empty:
            return
        for i in range(0, len(df), n_cols):
            fila = df.iloc[i:i+n_cols]
            cols = st.columns(len(fila))
            for col, (_, row) in zip(cols, fila.iterrows()):
                nombre = str(row["nombre_cuenta"]).title()
                valor = row["valor_metrica"]
                if denominador_grupo is not None and etiqueta_grupo:
                    texto_pct = f"{pct(valor, denominador_total)}% del total · {pct(valor, denominador_grupo)}% {etiqueta_grupo}"
                else:
                    texto_pct = f"{pct(valor, denominador_total)}% del total"
                with col:
                    st.markdown(f"""
                    <div style="background:#1e1e2e;border-left:4px solid {color};border-radius:10px;
                                padding:14px 16px;margin:6px 0;">
                        <div style="font-size:11px;color:#aaa;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;line-height:1.35;">{nombre}</div>
                        <div style="font-size:20px;font-weight:700;color:#fff;margin-bottom:6px;">{fmt_mm(valor)}</div>
                        <div style="font-size:12px;color:{color};">{texto_pct}</div>
                    </div>
                    """, unsafe_allow_html=True)

    def render_tabla_rubro(df, etiqueta_nombre, nombre_pct_grupo=None):
        if df.empty:
            return pd.DataFrame()
        columnas = ["cuenta", "nombre_cuenta", "valor_metrica", "pct_total"]
        if nombre_pct_grupo is not None and "pct_grupo" in df.columns:
            columnas.append("pct_grupo")
        columnas += ["compromisos", "obligaciones", "pagos", "pct_obligado_comprometido", "pct_pagado_obligado", "pct_pagado_comprometido"]
        tabla = df[columnas].copy()
        nombres = ["Cuenta", etiqueta_nombre, col_valor_tabla, "% del total"]
        if nombre_pct_grupo is not None and "pct_grupo" in df.columns:
            nombres.append(nombre_pct_grupo)
        nombres += ["Compromisos", "Obligaciones", "Pagos", "% obligado / comprometido", "% pagado / obligado", "% pagado / comprometido"]
        tabla.columns = nombres
        tabla = tabla.sort_values(col_valor_tabla, ascending=False).reset_index(drop=True)
        formato = {
            col_valor_tabla: format_cop,
            "% del total": lambda x: f"{x:.1f}%",
            "Compromisos": format_cop,
            "Obligaciones": format_cop,
            "Pagos": format_cop,
            "% obligado / comprometido": lambda x: f"{x:.1f}%",
            "% pagado / obligado": lambda x: f"{x:.1f}%",
            "% pagado / comprometido": lambda x: f"{x:.1f}%"
        }
        if nombre_pct_grupo is not None and nombre_pct_grupo in tabla.columns:
            formato[nombre_pct_grupo] = lambda x: f"{x:.1f}%"
        st.dataframe(tabla.style.format(formato), use_container_width=True, hide_index=True)
        return tabla

    def render_grafico_rubro(df, color, titulo_x=None):
        if df.empty:
            return
        df_plot = df.copy()
        df_plot = df_plot[df_plot["valor_miles_millones"] > 0].copy()
        if df_plot.empty:
            st.info("No hay valores positivos para graficar.")
            return
        df_plot = df_plot.sort_values("valor_miles_millones", ascending=True)
        max_x = df_plot["valor_miles_millones"].max() * 1.15
        chart = alt.Chart(df_plot).mark_bar(cornerRadius=4, color=color).encode(
            x=alt.X("valor_miles_millones:Q", title=titulo_x or f"{metrica_label} (miles de millones COP)", scale=alt.Scale(domain=[0, max_x]), axis=alt.Axis(format="$,.1f")),
            y=alt.Y("nombre_corto:N", sort="x", title="", axis=alt.Axis(labelLimit=280)),
            tooltip=[
                alt.Tooltip("cuenta:N", title="Cuenta"),
                alt.Tooltip("nombre_cuenta:N", title="Cuenta presupuestal"),
                alt.Tooltip("valor_miles_millones:Q", format="$,.1f", title="Miles de millones"),
                alt.Tooltip("pct_total:Q", format=".1f", title="% del total")
            ]
        ).properties(height=max(260, len(df_plot) * 42))
        st.altair_chart(chart, use_container_width=True)

    # ════════════════════════════════════════════════════════════════════════
    # TARJETAS PRINCIPALES
    # ════════════════════════════════════════════════════════════════════════
    st.subheader(f"Resumen del gasto — {ent_sel} | {per_lab}")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_card("Total gastos", total_gasto, 100.0, "#4CAF50", extra_text=f"Vigencia: {vigencia_analisis}. Métrica: {metrica_label}.")
    with c2:
        render_card("Funcionamiento", funcionamiento, pct(funcionamiento, total_gasto), "#2196F3")
    with c3:
        render_card("Servicio de la deuda", deuda, pct(deuda, total_gasto), "#FF9800")
    with c4:
        render_card("Inversión", inversion, pct(inversion, total_gasto), "#9C27B0")

    st.caption("La lectura principal usa la vigencia seleccionada y la métrica escogida. Las cuentas se tratan como jerárquicas: se usan cuentas exactas para totales e hijas inmediatas para composiciones, evitando doble conteo.")

    # ════════════════════════════════════════════════════════════════════════
    # COMPOSICIÓN GENERAL
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Composición general del gasto")
    comp_general = hijas_inmediatas(base, "2", vigencia_analisis)
    if comp_general.empty:
        comp_general = base[(base["vigencia_norm"] == vigencia_analisis) & (base["cuenta"].isin(["2.1", "2.2", "2.3"]))].copy()
    if comp_general.empty:
        st.info(f"No se encontraron cuentas inmediatas de la cuenta 2 para {vigencia_analisis}.")
        tabla_comp_general = pd.DataFrame()
    else:
        comp_general = preparar_df_rubro(comp_general, total_gasto)
        tabla_comp_general = render_tabla_rubro(comp_general, "Rubro")
        render_grafico_rubro(comp_general, "#4CAF50", f"{metrica_label} (miles de millones COP)")

    # ════════════════════════════════════════════════════════════════════════
    # FUNCIONAMIENTO
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Principales gastos de funcionamiento")
    func_df = hijas_inmediatas(base, "2.1", vigencia_analisis)
    if func_df.empty:
        st.info("No se encontraron desagregaciones inmediatas para funcionamiento.")
        tabla_func = pd.DataFrame()
    else:
        func_df = preparar_df_rubro(func_df, total_gasto, funcionamiento, "% de funcionamiento", top_n=6)
        render_cards_top(func_df, "#2196F3", total_gasto, funcionamiento, "de funcionamiento", n_cols=3)
        tabla_func = render_tabla_rubro(func_df, "Nombre cuenta", "% de funcionamiento")
        render_grafico_rubro(func_df, "#2196F3", f"{metrica_label} (miles de millones COP)")

    # ════════════════════════════════════════════════════════════════════════
    # INVERSIÓN
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Principales gastos de inversión")
    inv_df = hijas_inmediatas(base, "2.3", vigencia_analisis)
    if inv_df.empty:
        st.info("No se encontraron desagregaciones inmediatas para inversión.")
        tabla_inv = pd.DataFrame()
    else:
        inv_df = preparar_df_rubro(inv_df, total_gasto, inversion, "% de inversión", top_n=10)
        render_cards_top(inv_df, "#9C27B0", total_gasto, inversion, "de inversión", n_cols=3)
        tabla_inv = render_tabla_rubro(inv_df, "Nombre cuenta", "% de inversión")
        render_grafico_rubro(inv_df, "#9C27B0", f"{metrica_label} (miles de millones COP)")

    # ════════════════════════════════════════════════════════════════════════
    # DEUDA
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Servicio de la deuda")
    deuda_df = hijas_inmediatas(base, "2.2", vigencia_analisis)
    if deuda_df.empty:
        deuda_df = fila_exacta(base, "2.2", vigencia_analisis)
    if deuda_df.empty:
        st.info(f"No se encontraron datos de servicio de la deuda para {vigencia_analisis}.")
        tabla_deuda = pd.DataFrame()
    else:
        deuda_df = preparar_df_rubro(deuda_df, total_gasto, deuda, "% de deuda")
        tabla_deuda = render_tabla_rubro(deuda_df, "Nombre cuenta", "% de deuda")
        if len(deuda_df) > 1:
            render_grafico_rubro(deuda_df, "#FF9800", f"{metrica_label} (miles de millones COP)")

    # ════════════════════════════════════════════════════════════════════════
    # SECCIÓN PRESUPUESTAL
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Gasto por sección presupuestal")
    secc_df = df_raw[(df_raw["cuenta"] == "2") & (df_raw["nombre_cuenta"].str.upper() == "GASTOS") & (df_raw["vigencia_norm"] == vigencia_analisis)].copy()
    if secc_df.empty:
        st.info(f"No se encontraron registros de sección presupuestal para cuenta 2 y {vigencia_analisis}.")
        secc_table = pd.DataFrame()
        consolidado_secc = pd.DataFrame()
    else:
        consolidado_secc = secc_df.groupby("nom_seccion_presupuestal", as_index=False)[["compromisos", "obligaciones", "pagos"]].sum()
        consolidado_secc["valor_metrica"] = consolidado_secc[metrica]
        total_secc = consolidado_secc["valor_metrica"].sum()
        consolidado_secc["pct_total"] = consolidado_secc["valor_metrica"].apply(lambda x: pct(x, total_secc))
        consolidado_secc["obligado_comprometido"] = consolidado_secc.apply(lambda row: safe_div(row["obligaciones"], row["compromisos"]), axis=1)
        consolidado_secc["pagos_obligaciones"] = consolidado_secc.apply(lambda row: safe_div(row["pagos"], row["obligaciones"]), axis=1)
        consolidado_secc["pagos_compromisos"] = consolidado_secc.apply(lambda row: safe_div(row["pagos"], row["compromisos"]), axis=1)
        consolidado_secc["seccion_limpia"] = consolidado_secc["nom_seccion_presupuestal"].astype(str).str.replace(r"^.*?-\s*", "", regex=True).str.strip()
        consolidado_secc = consolidado_secc.sort_values("valor_metrica", ascending=False).reset_index(drop=True)
        secc_table = consolidado_secc[["seccion_limpia", "valor_metrica", "pct_total", "compromisos", "obligaciones", "pagos", "obligado_comprometido", "pagos_obligaciones", "pagos_compromisos"]].copy()
        secc_table.columns = ["Sección presupuestal", col_valor_tabla, "% del total", "Compromisos", "Obligaciones", "Pagos", "% obligaciones / compromisos", "% pagos / obligaciones", "% pagos / compromisos"]
        secc_table = secc_table.sort_values(col_valor_tabla, ascending=False).reset_index(drop=True)
        st.dataframe(secc_table.style.format({
            col_valor_tabla: format_cop,
            "% del total": lambda x: f"{x:.1f}%",
            "Compromisos": format_cop,
            "Obligaciones": format_cop,
            "Pagos": format_cop,
            "% obligaciones / compromisos": lambda x: f"{x:.1f}%",
            "% pagos / obligaciones": lambda x: f"{x:.1f}%",
            "% pagos / compromisos": lambda x: f"{x:.1f}%"
        }), use_container_width=True, hide_index=True)
        plot_secc = consolidado_secc.head(15).copy()
        plot_secc["valor_miles_millones"] = plot_secc["valor_metrica"] / 1e9
        plot_secc["nombre_corto"] = plot_secc["seccion_limpia"].astype(str).str.slice(0, 60)
        plot_secc = plot_secc[plot_secc["valor_miles_millones"] > 0].copy()
        if not plot_secc.empty:
            plot_secc = plot_secc.sort_values("valor_miles_millones", ascending=True)
            max_x = plot_secc["valor_miles_millones"].max() * 1.15
            sec_chart = alt.Chart(plot_secc).mark_bar(cornerRadius=4, color="#607D8B").encode(
                x=alt.X("valor_miles_millones:Q", title=f"{metrica_label} (miles de millones COP)", scale=alt.Scale(domain=[0, max_x]), axis=alt.Axis(format="$,.1f")),
                y=alt.Y("nombre_corto:N", sort="x", title="", axis=alt.Axis(labelLimit=280)),
                tooltip=[alt.Tooltip("seccion_limpia:N", title="Sección"), alt.Tooltip("valor_miles_millones:Q", format="$,.1f", title="Miles de millones"), alt.Tooltip("pct_total:Q", format=".1f", title="% del total")]
            ).properties(height=max(280, len(plot_secc) * 38))
            st.altair_chart(sec_chart, use_container_width=True)

    # ════════════════════════════════════════════════════════════════════════
    # INDICADORES DE EJECUCIÓN
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Indicadores de ejecución")
    indicadores = []
    for nombre, cuenta_codigo in [("Total gastos", "2"), ("Funcionamiento", "2.1"), ("Servicio de deuda", "2.2"), ("Inversión", "2.3")]:
        comp = valor_exacto(base, cuenta_codigo, "compromisos", vigencia_analisis)
        obl = valor_exacto(base, cuenta_codigo, "obligaciones", vigencia_analisis)
        pag = valor_exacto(base, cuenta_codigo, "pagos", vigencia_analisis)
        indicadores.append({
            "Rubro": nombre,
            "Cuenta": cuenta_codigo,
            "Compromisos": comp,
            "Obligaciones": obl,
            "Pagos": pag,
            "% Obligado / Comprometido": safe_div(obl, comp),
            "% Pagado / Obligado": safe_div(pag, obl),
            "% Pagado / Comprometido": safe_div(pag, comp)
        })
    df_indicadores = pd.DataFrame(indicadores)
    st.dataframe(df_indicadores.style.format({
        "Compromisos": format_cop,
        "Obligaciones": format_cop,
        "Pagos": format_cop,
        "% Obligado / Comprometido": lambda x: f"{x:.1f}%",
        "% Pagado / Obligado": lambda x: f"{x:.1f}%",
        "% Pagado / Comprometido": lambda x: f"{x:.1f}%"
    }), use_container_width=True, hide_index=True)

    # ════════════════════════════════════════════════════════════════════════
    # VIGENCIAS Y REZAGOS
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Vigencias y rezagos")
    vigencia_df = base[(base["cuenta"] == "2") & (base["nombre_cuenta"].str.upper() == "GASTOS")].copy()
    if vigencia_df.empty:
        st.info("No hay registros de la cuenta 2 con nombre GASTOS para vigencias.")
        vigencia_table = pd.DataFrame()
    else:
        vigencia_consol = vigencia_df.groupby("vigencia_norm", as_index=False)[["compromisos", "obligaciones", "pagos"]].sum()
        total_vig_comp = vigencia_consol["compromisos"].sum()
        vigencia_consol["pct_total"] = vigencia_consol["compromisos"].apply(lambda x: pct(x, total_vig_comp))
        vigencia_consol["pagos_compromisos"] = vigencia_consol.apply(lambda row: safe_div(row["pagos"], row["compromisos"]), axis=1)
        orden_map = {"VIGENCIA ACTUAL": 1, "RESERVAS": 2, "CUENTAS POR PAGAR": 3, "VIGENCIAS FUTURAS - VIGENCIA ACTUAL": 4, "VIGENCIAS FUTURAS - RESERVAS": 5}
        vigencia_consol["orden"] = vigencia_consol["vigencia_norm"].map(orden_map).fillna(99)
        vigencia_consol = vigencia_consol.sort_values(["orden", "vigencia_norm"]).reset_index(drop=True)
        vigencia_table = vigencia_consol[["vigencia_norm", "compromisos", "obligaciones", "pagos", "pct_total", "pagos_compromisos"]].copy()
        vigencia_table.columns = ["Vigencia", "Compromisos", "Obligaciones", "Pagos", "% del total de vigencias", "% Pagos / compromisos"]
        st.dataframe(vigencia_table.style.format({
            "Compromisos": format_cop,
            "Obligaciones": format_cop,
            "Pagos": format_cop,
            "% del total de vigencias": lambda x: f"{x:.1f}%",
            "% Pagos / compromisos": lambda x: f"{x:.1f}%"
        }), use_container_width=True, hide_index=True)
        st.caption("Esta sección no se mezcla con la lectura principal de la vigencia seleccionada. Sirve para identificar rezagos, reservas, cuentas por pagar y compromisos asociados a vigencias futuras.")

    # ════════════════════════════════════════════════════════════════════════
    # HISTÓRICO DEL GASTO
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Histórico del gasto — nominal vs real")

    @st.cache_data(ttl=600, show_spinner=False)
    def obtener_historico_gastos(codigo_entidad):
        codigo_entidad = str(int(float(codigo_entidad)))
        cols = "periodo,cuenta,nombre_cuenta,compromisos,pagos,obligaciones,nom_vigencia_del_gasto"
        params = {"$select": cols, "$where": f"codigo_entidad='{codigo_entidad}'", "$limit": 500000}
        try:
            r = requests.get("https://www.datos.gov.co/resource/4f7r-epif.csv", params=params, timeout=90)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            return df if not df.empty else pd.DataFrame()
        except Exception as e:
            st.warning(f"No se pudo obtener el histórico: {e}")
            return pd.DataFrame()

    with st.spinner("Cargando histórico de gastos..."):
        df_hist_raw = obtener_historico_gastos(codigo_ent)

    df_serie = pd.DataFrame()
    if df_hist_raw.empty:
        st.info("No hay histórico de gasto disponible para la entidad seleccionada.")
    else:
        for col in ["compromisos", "obligaciones", "pagos"]:
            df_hist_raw[col] = pd.to_numeric(df_hist_raw[col], errors="coerce").fillna(0.0)
        df_hist_raw["cuenta"] = df_hist_raw["cuenta"].astype(str).str.strip()
        df_hist_raw["nombre_cuenta"] = df_hist_raw["nombre_cuenta"].fillna("").astype(str).str.strip()
        df_hist_raw["vigencia_norm"] = df_hist_raw["nom_vigencia_del_gasto"].fillna("").astype(str).str.strip().str.upper()
        df_hist_raw["periodo_str"] = df_hist_raw["periodo"].astype(str).str.zfill(8)
        df_hist_raw["periodo_dt"] = pd.to_datetime(df_hist_raw["periodo_str"], format="%Y%m%d", errors="coerce")
        df_hist_raw = df_hist_raw.dropna(subset=["periodo_dt"])
        df_hist_raw["year"] = df_hist_raw["periodo_dt"].dt.year
        df_hist_raw["md"] = df_hist_raw["periodo_dt"].dt.strftime("%m%d")
        df_hist = df_hist_raw[(df_hist_raw["cuenta"] == "2") & (df_hist_raw["nombre_cuenta"].str.upper() == "GASTOS") & (df_hist_raw["vigencia_norm"] == "VIGENCIA ACTUAL")].copy()
        if df_hist.empty:
            st.info("No hay histórico para la cuenta 2 GASTOS en Vigencia Actual.")
        else:
            df_hist = df_hist.groupby(["year", "periodo", "periodo_dt", "periodo_str"], as_index=False)[["compromisos", "obligaciones", "pagos"]].sum()
            registros = []
            anio_actual_hist = int(df_hist["year"].max())
            for yr in sorted(df_hist["year"].unique()):
                grp = df_hist[df_hist["year"] == yr].copy()
                if yr != anio_actual_hist:
                    candidato = grp[grp["periodo_str"].astype(str).str.endswith("1201")]
                    if candidato.empty:
                        candidato = grp.loc[[grp["periodo_dt"].idxmax()]]
                else:
                    candidato = grp.loc[[grp["periodo_dt"].idxmax()]]
                if not candidato.empty:
                    row = candidato.iloc[-1]
                    registros.append({"year": int(row["year"]), "periodo": row["periodo_str"], "valor": float(row[metrica])})
            if registros:
                df_serie = pd.DataFrame(registros).sort_values("year").reset_index(drop=True)
                ipc_map = {2019: 97.46, 2020: 100.00, 2021: 111.41, 2022: 126.03, 2023: 137.09, 2024: 144.88, 2025: 151.00, 2026: 157.00}
                df_serie["ipc"] = df_serie["year"].map(ipc_map)
                df_serie["nominal_millones"] = df_serie["valor"] / 1e6
                df_serie["real_millones"] = df_serie.apply(lambda row: row["nominal_millones"] / row["ipc"] * 100 if pd.notna(row["ipc"]) and row["ipc"] > 0 else None, axis=1)
                df_long = pd.melt(df_serie, id_vars=["year"], value_vars=["nominal_millones", "real_millones"], var_name="serie", value_name="valor_millones").dropna(subset=["valor_millones"])
                df_long["serie"] = df_long["serie"].map({"nominal_millones": "Nominal", "real_millones": "Real"})
                chart_hist = alt.Chart(df_long).mark_line(point=True).encode(
                    x=alt.X("year:O", title="Año"),
                    y=alt.Y("valor_millones:Q", title="Millones COP", axis=alt.Axis(format="$,.0f")),
                    color=alt.Color("serie:N", title="Serie"),
                    tooltip=[alt.Tooltip("year:O", title="Año"), alt.Tooltip("serie:N", title="Serie"), alt.Tooltip("valor_millones:Q", format="$,.1f", title="Millones COP")]
                ).properties(height=380)
                st.altair_chart(chart_hist, use_container_width=True)
                if len(df_serie) >= 2:
                    inicio = df_serie.iloc[0]
                    fin = df_serie.iloc[-1]
                    cambio_nominal = round((fin["valor"] / inicio["valor"] - 1) * 100, 1) if inicio["valor"] > 0 else 0.0
                    if pd.notna(inicio.get("real_millones")) and pd.notna(fin.get("real_millones")) and inicio["real_millones"] > 0:
                        cambio_real = round((fin["real_millones"] / inicio["real_millones"] - 1) * 100, 1)
                        st.markdown(f"**Cambio {metrica_label.lower()} nominal** entre {int(inicio['year'])} y {int(fin['year'])}: **{cambio_nominal:+.1f}%**. **Cambio real:** **{cambio_real:+.1f}%**.")
                    else:
                        st.markdown(f"**Cambio {metrica_label.lower()} nominal** entre {int(inicio['year'])} y {int(fin['year'])}: **{cambio_nominal:+.1f}%**.")

    # ════════════════════════════════════════════════════════════════════════
    # ALERTAS TÉCNICAS
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    with st.expander("Alertas técnicas", expanded=False):
        alertas = []
        if total_gasto > 0:
            if pct(inversion, total_gasto) < 30.0:
                alertas.append("La inversión representa menos del 30% del gasto total. Se recomienda revisar la distribución entre inversión y funcionamiento.")
            if pct(funcionamiento, total_gasto) > 50.0:
                alertas.append("El funcionamiento representa más del 50% del gasto total. Puede indicar concentración en gasto corriente.")
            if pct(deuda, total_gasto) > 15.0:
                alertas.append("El servicio de la deuda representa más del 15% del gasto total. Debe interpretarse según el calendario presupuestal y las condiciones de la deuda.")
        total_compromisos = valor_exacto(base, "2", "compromisos", vigencia_analisis)
        total_pagos = valor_exacto(base, "2", "pagos", vigencia_analisis)
        if total_compromisos > 0 and safe_div(total_pagos, total_compromisos) < 70.0:
            alertas.append("El porcentaje de pagos sobre compromisos es menor al 70%. Se recomienda revisar el avance de ejecución financiera.")
        if "consolidado_secc" in locals() and isinstance(consolidado_secc, pd.DataFrame) and not consolidado_secc.empty:
            max_concentracion = consolidado_secc["pct_total"].max()
            if max_concentracion > 40.0:
                sec_max = consolidado_secc.loc[consolidado_secc["pct_total"].idxmax(), "seccion_limpia"]
                alertas.append(f"Una sección presupuestal concentra más del 40% del gasto seleccionado: {sec_max} ({max_concentracion:.1f}%). Puede indicar concentración institucional de la ejecución.")
        if alertas:
            for a in alertas:
                st.warning(a)
        else:
            st.success("No se activaron alertas técnicas con los umbrales definidos.")

    # ════════════════════════════════════════════════════════════════════════
    # EXPORTACIÓN A EXCEL
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    output_gastos = io.BytesIO()
    with pd.ExcelWriter(output_gastos, engine="xlsxwriter") as writer:
        df_raw.to_excel(writer, index=False, sheet_name="Datos brutos")
        arbol_cols = ["vigencia_norm", "cuenta", "nombre_cuenta", "nivel_cuenta", "cuenta_padre", "n_hijas_inmediatas", "tiene_hijas", "es_hoja", "compromisos", "obligaciones", "pagos"]
        exp_arbol = base[[c for c in arbol_cols if c in base.columns]].sort_values(["vigencia_norm", "cuenta"])
        exp_arbol.to_excel(writer, index=False, sheet_name="Arbol jerarquico")
        if "tabla_comp_general" in locals() and isinstance(tabla_comp_general, pd.DataFrame) and not tabla_comp_general.empty:
            tabla_comp_general.to_excel(writer, index=False, sheet_name="Composicion general")
        if "tabla_func" in locals() and isinstance(tabla_func, pd.DataFrame) and not tabla_func.empty:
            tabla_func.to_excel(writer, index=False, sheet_name="Funcionamiento")
        if "tabla_inv" in locals() and isinstance(tabla_inv, pd.DataFrame) and not tabla_inv.empty:
            tabla_inv.to_excel(writer, index=False, sheet_name="Inversion")
        if "tabla_deuda" in locals() and isinstance(tabla_deuda, pd.DataFrame) and not tabla_deuda.empty:
            tabla_deuda.to_excel(writer, index=False, sheet_name="Servicio deuda")
        if "secc_table" in locals() and isinstance(secc_table, pd.DataFrame) and not secc_table.empty:
            secc_table.to_excel(writer, index=False, sheet_name="Seccion presupuestal")
        if "df_indicadores" in locals() and isinstance(df_indicadores, pd.DataFrame) and not df_indicadores.empty:
            df_indicadores.to_excel(writer, index=False, sheet_name="Indicadores ejecucion")
        if "vigencia_table" in locals() and isinstance(vigencia_table, pd.DataFrame) and not vigencia_table.empty:
            vigencia_table.to_excel(writer, index=False, sheet_name="Vigencias rezagos")
        if "df_serie" in locals() and isinstance(df_serie, pd.DataFrame) and not df_serie.empty:
            df_serie.to_excel(writer, index=False, sheet_name="Historico")
        for sheet_name, ws in writer.sheets.items():
            ws.freeze_panes(1, 0)
            ws.set_column(0, 0, 18)
            ws.set_column(1, 1, 35)

    st.download_button(
        label="Excel",
        data=output_gastos.getvalue(),
        file_name=f"ejecucion_gastos_{ent_sel}_{periodo}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )



elif pagina == "Ejecución de Ingresos":
    st.title("Ejecución de Ingresos")

    # ── Helpers de formato ────────────────────────────────────────────────
    def fmt_mm(valor_pesos):
        m = valor_pesos / 1e6
        if m >= 1000:
            return f"$ {m/1000:,.1f} MM"
        return f"$ {m:,.1f} M"

    def pct(valor, base):
        return round(valor / base * 100, 1) if base > 0 else 0.0

    # ── Sidebar ───────────────────────────────────────────────────────────
    nivel = st.sidebar.selectbox("Nivel geográfico:", ["Municipios", "Gobernaciones"], key="niv_ejing")
    if nivel == "Municipios":
        deps   = sorted(df_mun["departamento"].dropna().astype(str).unique())
        dep    = st.sidebar.selectbox("Departamento:", deps, key="dep_ejing")
        df_ent = df_mun[df_mun["departamento"] == dep]
        label  = "Municipio"
    else:
        df_ent = df_dep
        label  = "Gobernación"

    mun_dict = dict(zip(df_ent['nombre_entidad'], df_ent['codigo_entidad']))
    ent      = st.sidebar.selectbox(f"{label}:", list(mun_dict.keys()), key="ent_ejing")
    cod_ent  = mun_dict[ent]

    import datetime
    today             = datetime.date.today()
    current_year      = today.year
    current_month     = today.month
    current_quarter   = (current_month - 1) // 3 + 1
    last_full_quarter = current_quarter - 1 if current_quarter > 1 else 0

    df_per['periodo_str'] = df_per['periodo'].astype(str).str.zfill(8)
    df_per['year']        = df_per['periodo_str'].str[:4].astype(int)
    df_per['month']       = df_per['periodo_str'].str[4:6].astype(int)
    df_per_filt = df_per[df_per['year'] <= current_year]
    if last_full_quarter > 0:
        df_per_filt = df_per_filt[~(
            (df_per_filt['year'] == current_year) &
            (df_per_filt['month'] > last_full_quarter * 3)
        )]
    else:
        df_per_filt = df_per_filt[df_per_filt['year'] < current_year]

    df_per_filt = df_per_filt.sort_values('periodo')
    per_dict    = dict(zip(df_per_filt['periodo_label'], df_per_filt['periodo']))
    per_lab     = st.sidebar.selectbox("Período:", list(per_dict.keys()), key="per_ejing")
    periodo     = str(per_dict[per_lab])

    if st.sidebar.button("Cargar datos de ingresos", key="btn_ejing"):
        with st.spinner("Cargando ejecución de ingresos..."):
            st.session_state['df_ejing'] = obtener_ejecucion_ingresos(cod_ent, periodo)

    if 'df_ejing' not in st.session_state:
        st.stop()

    df_raw = st.session_state['df_ejing']
    if df_raw.empty:
        st.warning(f"No se encontraron datos para '{ent}' en el período '{per_lab}'.")
        st.stop()

    with st.expander("Datos brutos", expanded=False):
        st.dataframe(df_raw, use_container_width=True)

    # ════════════════════════════════════════════════════════════════════════
    # PASO 1 — Agrupar: una fila por cuenta (elimina duplicados por fuente,
    #          tercero, detalle sectorial, etc.)
    # ════════════════════════════════════════════════════════════════════════
    df_raw['total_recaudo'] = pd.to_numeric(df_raw['total_recaudo'], errors='coerce').fillna(0)
    df_raw['cuenta']        = df_raw['cuenta'].astype(str).str.strip()

    base = df_raw.groupby(
        ['cuenta', 'nombre_cuenta'], as_index=False
    )['total_recaudo'].sum()

    # ════════════════════════════════════════════════════════════════════════
    # PASO 2 — Construir variables de jerarquía
    # ════════════════════════════════════════════════════════════════════════
    def nivel_cuenta(c):
        return len(str(c).split('.'))

    def cuenta_padre(c):
        partes = str(c).rsplit('.', 1)
        return partes[0] if len(partes) > 1 else None

    base['nivel_cuenta'] = base['cuenta'].apply(nivel_cuenta)
    base['cuenta_padre'] = base['cuenta'].apply(cuenta_padre)

    # Contar hijas inmediatas de cada cuenta
    hijas_count = base.groupby('cuenta_padre')['cuenta'].count().to_dict()
    base['n_hijas_inmediatas'] = base['cuenta'].map(hijas_count).fillna(0).astype(int)
    base['tiene_hijas']        = base['n_hijas_inmediatas'] > 0
    base['es_hoja']            = ~base['tiene_hijas']

    # Índice rápido: cuenta → fila
    idx = base.set_index('cuenta')

    def recaudo_exacto(codigo):
        """Recaudo de una cuenta EXACTA (sin sumar descendientes)."""
        if codigo in idx.index:
            return idx.loc[codigo, 'total_recaudo']
        return 0.0

    def recaudo_prefijo(codigo):
        """Recaudo de una cuenta o, si no existe exacta, de sus subcuentas."""
        exacto = recaudo_exacto(codigo)
        if exacto > 0:
            return exacto
        prefijo = f"{codigo}."
        return base[base['cuenta'].astype(str).str.startswith(prefijo)]['total_recaudo'].sum()

    def hijas_inmediatas(codigo_padre):
        """DataFrame con las hijas inmediatas de un código padre."""
        return base[base['cuenta_padre'] == codigo_padre].copy()

    # ════════════════════════════════════════════════════════════════════════
    # PASO 3 — Validación: suma de hijas inmediatas vs valor del padre
    #          (solo para cuentas con hijas)
    # ════════════════════════════════════════════════════════════════════════
    cuentas_padre = base[base['tiene_hijas']]['cuenta'].tolist()
    alertas = []
    for cp in cuentas_padre:
        val_padre  = recaudo_exacto(cp)
        suma_hijas = hijas_inmediatas(cp)['total_recaudo'].sum()
        if val_padre > 0:
            diff_abs = abs(val_padre - suma_hijas)
            diff_pct = round(diff_abs / val_padre * 100, 2)
            if diff_pct > 1.0:   # tolerancia del 1% para redondeos
                nombre = idx.loc[cp, 'nombre_cuenta'] if cp in idx.index else cp
                alertas.append({
                    'Cuenta padre': cp,
                    'Nombre': nombre,
                    'Valor padre': round(val_padre / 1e6, 1),
                    'Suma hijas': round(suma_hijas / 1e6, 1),
                    'Dif. absoluta (M)': round(diff_abs / 1e6, 1),
                    'Dif. %': diff_pct
                })

    if alertas:
        with st.expander(f"⚠️ Advertencias de consistencia ({len(alertas)} cuentas)", expanded=False):
            st.dataframe(pd.DataFrame(alertas), use_container_width=True, hide_index=True)

    # ════════════════════════════════════════════════════════════════════════
    # SECCIÓN 1 — ESTRUCTURA GENERAL
    # Usa códigos exactos de los grandes grupos (nivel 1 y 2)
    # ════════════════════════════════════════════════════════════════════════
    total_general  = recaudo_exacto('1')
    corrientes     = recaudo_exacto('1.1')
    tributarios    = recaudo_exacto('1.1.01')
    no_tributarios = recaudo_exacto('1.1.02')
    capital        = recaudo_exacto('1.2')

    # Si '1' no existe directamente, inferir del nivel superior real
    if total_general == 0:
        total_general = base[base['nivel_cuenta'] == 1]['total_recaudo'].sum()

    st.subheader(f"Recaudo total — {ent} | {per_lab}")

    def render_card(titulo, valor, pct_val, pct_lbl, color, extra_text=None):
        extra_html = f"<div style=\"font-size:11px;color:#aaa;margin-top:4px;\">{extra_text}</div>" if extra_text else ""
        st.markdown(f"""
        <div style="background:#1e1e2e;border-left:4px solid {color};border-radius:8px;
                    padding:14px 18px;margin:4px 0;">
            <div style="font-size:11px;color:#aaa;text-transform:uppercase;
                        letter-spacing:.05em;margin-bottom:6px;">{titulo}</div>
            <div style="font-size:22px;font-weight:700;color:#fff;margin-bottom:4px;">
                {fmt_mm(valor)}
            </div>
            <div style="font-size:13px;color:{color};font-weight:500;">
                {pct_val}% {pct_lbl}
            </div>
            {extra_html}
        </div>""", unsafe_allow_html=True)

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        render_card(
            "Total recaudo",
            total_general,
            100,
            "del total",
            "#4CAF50"
        )
    with c2:
        render_card(
            "Ingresos corrientes",
            corrientes,
            pct(corrientes, total_general),
            "del total",
            "#2196F3"
        )
    with c3:
        render_card(
            "Tributarios",
            tributarios,
            pct(tributarios, total_general),
            "del total",
            "#00BCD4",
            extra_text=f"{pct(tributarios, corrientes):.1f}% de corrientes"
        )
    with c4:
        render_card(
            "No tributarios",
            no_tributarios,
            pct(no_tributarios, total_general),
            "del total",
            "#9C27B0",
            extra_text=f"{pct(no_tributarios, corrientes):.1f}% de corrientes"
        )
    with c5:
        render_card(
            "Recursos de capital",
            capital,
            pct(capital, total_general),
            "del total",
            "#FF9800"
        )

    # ════════════════════════════════════════════════════════════════════════
    # SECCIÓN 2 — PRINCIPALES INGRESOS CORRIENTES
    # Se separan ingresos tributarios y no tributarios.
    # No se mezclan cuentas padre con descendientes.
    # ════════════════════════════════════════════════════════════════════════

    st.markdown("---")
    st.subheader("Principales ingresos corrientes")

    def preparar_top_cuentas(df, denominador_total, denominador_grupo, nombre_pct_grupo, top_n=9):
        """
        Prepara una tabla de principales cuentas sin doble conteo.
        El DataFrame recibido debe contener únicamente cuentas comparables:
        hijas inmediatas de una misma cuenta padre o de ramas equivalentes.
        """
        df = df.copy()

        if df.empty:
            return df

        df["total_recaudo"] = pd.to_numeric(
            df["total_recaudo"],
            errors="coerce"
        ).fillna(0)

        df = df[df["total_recaudo"] > 0].copy()

        if df.empty:
            return df

        df["pct_total"] = df["total_recaudo"].apply(
            lambda x: pct(x, denominador_total)
        )

        df["pct_grupo"] = df["total_recaudo"].apply(
            lambda x: pct(x, denominador_grupo)
        )

        df["nombre_pct_grupo"] = nombre_pct_grupo

        df = (
            df.sort_values("total_recaudo", ascending=False)
              .head(top_n)
              .reset_index(drop=True)
        )

        return df

    def render_cards_cuentas(df, color, etiqueta_grupo):
        """
        Renderiza tarjetas de cuentas principales.
        """
        if df.empty:
            st.info(f"No se encontraron registros para {etiqueta_grupo}.")
            return

        for i in range(0, len(df), 3):
            fila = df.iloc[i:i+3]
            cols = st.columns(len(fila))

            for col, (_, row) in zip(cols, fila.iterrows()):
                nombre = str(row["nombre_cuenta"]).title()
                v = row["total_recaudo"]

                with col:
                    st.markdown(f"""
                    <div style="background:#1e1e2e;border-left:4px solid {color};border-radius:8px;
                                padding:14px 18px;margin:4px 0;">
                        <div style="font-size:11px;color:#aaa;text-transform:uppercase;
                                    letter-spacing:.04em;margin-bottom:6px;line-height:1.4;">
                            {nombre}
                        </div>
                        <div style="font-size:20px;font-weight:700;color:#fff;margin-bottom:4px;">
                            {fmt_mm(v)}
                        </div>
                        <div style="font-size:12px;color:{color};">
                            <b>{row["pct_total"]}%</b> del total
                            &nbsp;·&nbsp;
                            <b>{row["pct_grupo"]}%</b> {row["nombre_pct_grupo"]}
                        </div>
                    </div>""", unsafe_allow_html=True)

    def render_tabla_cuentas(df, etiqueta_columna, nombre_pct_grupo):
        """
        Renderiza tabla de detalle con código de cuenta para auditoría.
        """
        if df.empty:
            return

        df_tabla = df[
            ["cuenta", "nombre_cuenta", "total_recaudo", "pct_total", "pct_grupo"]
        ].copy()

        df_tabla.columns = [
            "Cuenta",
            etiqueta_columna,
            "Recaudo",
            "% del total",
            nombre_pct_grupo
        ]

        df_tabla["Recaudo"] = df_tabla["Recaudo"].apply(fmt_mm)
        df_tabla["% del total"] = df_tabla["% del total"].apply(lambda x: f"{x:.1f}%")
        df_tabla[nombre_pct_grupo] = df_tabla[nombre_pct_grupo].apply(lambda x: f"{x:.1f}%")

        st.dataframe(df_tabla, use_container_width=True, hide_index=True)

    def render_grafico_cuentas(df, titulo_x, color):
        """
        Gráfico horizontal robusto.
        Usa nombres de columnas simples para evitar errores de Altair/Vega.
        """
        if df.empty:
            return

        df_ch = df.copy()

        df_ch["total_recaudo"] = pd.to_numeric(
            df_ch["total_recaudo"],
            errors="coerce"
        ).fillna(0)

        # Pasar pesos a miles de millones
        df_ch["recaudo_miles_millones"] = df_ch["total_recaudo"] / 1e9

        df_ch["nombre_corto"] = (
            df_ch["nombre_cuenta"]
            .astype(str)
            .str.upper()
            .str.replace("IMPUESTO DE ", "", regex=False)
            .str.replace("IMPUESTO ", "", regex=False)
            .str.slice(0, 45)
        )

        df_ch = df_ch[df_ch["recaudo_miles_millones"] > 0].copy()
        df_ch = df_ch.sort_values("recaudo_miles_millones", ascending=True)

        if df_ch.empty:
            st.info("No hay valores positivos para graficar.")
            return

        max_x = df_ch["recaudo_miles_millones"].max() * 1.15

        chart = alt.Chart(df_ch).mark_bar(
            cornerRadius=4,
            color=color
        ).encode(
            x=alt.X(
                "recaudo_miles_millones:Q",
                title=titulo_x,
                scale=alt.Scale(domain=[0, max_x]),
                axis=alt.Axis(format="$,.1f")
            ),
            y=alt.Y(
                "nombre_corto:N",
                sort="x",
                title="",
                axis=alt.Axis(labelLimit=260)
            ),
            tooltip=[
                alt.Tooltip("cuenta:N", title="Cuenta"),
                alt.Tooltip("nombre_cuenta:N", title="Cuenta presupuestal"),
                alt.Tooltip("recaudo_miles_millones:Q", format="$,.1f", title="Miles de millones"),
                alt.Tooltip("pct_total:Q", format=".1f", title="% del total"),
                alt.Tooltip("pct_grupo:Q", format=".1f", title="% del grupo")
            ]
        ).properties(
            height=max(260, len(df_ch) * 42)
        )

        st.altair_chart(chart, use_container_width=True)

    # ───────────────────────────────────────────────────────────────────────
    # 2.1 Principales ingresos tributarios
    # ───────────────────────────────────────────────────────────────────────

    st.markdown("### Principales ingresos tributarios")

    df_directos = hijas_inmediatas("1.1.01.01")
    df_indirectos = hijas_inmediatas("1.1.01.02")

    df_tributarios_principales = pd.concat(
        [df_directos, df_indirectos],
        ignore_index=True
    )

    df_tributarios_principales = preparar_top_cuentas(
        df=df_tributarios_principales,
        denominador_total=total_general,
        denominador_grupo=tributarios,
        nombre_pct_grupo="de tributarios",
        top_n=9
    )

    # Validación: solo deben aparecer cuentas bajo impuestos directos o indirectos
    if not df_tributarios_principales.empty:
        mask_no_tributaria = ~(
            df_tributarios_principales["cuenta"].astype(str).str.startswith("1.1.01.01.") |
            df_tributarios_principales["cuenta"].astype(str).str.startswith("1.1.01.02.")
        )

        if mask_no_tributaria.any():
            st.warning(
                "Advertencia: hay cuentas en principales tributarios que no parecen pertenecer "
                "a impuestos directos o indirectos. Revise la clasificación por código CUIPO."
            )
            st.dataframe(
                df_tributarios_principales.loc[
                    mask_no_tributaria,
                    ["cuenta", "nombre_cuenta", "total_recaudo"]
                ],
                use_container_width=True,
                hide_index=True
            )

    render_cards_cuentas(
        df=df_tributarios_principales,
        color="#00BCD4",
        etiqueta_grupo="ingresos tributarios"
    )

    st.markdown("#### Detalle tributario")
    render_tabla_cuentas(
        df=df_tributarios_principales,
        etiqueta_columna="Ingreso tributario",
        nombre_pct_grupo="% de tributarios"
    )

    render_grafico_cuentas(
        df=df_tributarios_principales,
        titulo_x="Miles de millones de pesos",
        color="#00BCD4"
    )

    # ───────────────────────────────────────────────────────────────────────
    # 2.2 Principales ingresos no tributarios
    # ───────────────────────────────────────────────────────────────────────

    st.markdown("### Principales ingresos no tributarios")

    df_no_tributarios_principales = hijas_inmediatas("1.1.02")

    df_no_tributarios_principales = preparar_top_cuentas(
        df=df_no_tributarios_principales,
        denominador_total=total_general,
        denominador_grupo=no_tributarios,
        nombre_pct_grupo="de no tributarios",
        top_n=9
    )

    # Validación: los no tributarios deben ser hijos inmediatos de 1.1.02
    if not df_no_tributarios_principales.empty:
        mask_no_no_tributaria = (
            df_no_tributarios_principales["cuenta_padre"].astype(str) != "1.1.02"
        )

        if mask_no_no_tributaria.any():
            st.warning(
                "Advertencia: hay cuentas en principales no tributarios que no son hijas inmediatas "
                "de `1.1.02`. Revise la clasificación por código CUIPO."
            )
            st.dataframe(
                df_no_tributarios_principales.loc[
                    mask_no_no_tributaria,
                    ["cuenta", "nombre_cuenta", "total_recaudo"]
                ],
                use_container_width=True,
                hide_index=True
            )

    render_cards_cuentas(
        df=df_no_tributarios_principales,
        color="#9C27B0",
        etiqueta_grupo="ingresos no tributarios"
    )

    st.markdown("#### Detalle no tributario")
    render_tabla_cuentas(
        df=df_no_tributarios_principales,
        etiqueta_columna="Ingreso no tributario",
        nombre_pct_grupo="% de no tributarios"
    )

    render_grafico_cuentas(
        df=df_no_tributarios_principales,
        titulo_x="Miles de millones de pesos",
        color="#9C27B0"
    )

    st.caption(
        "Los ingresos tributarios principales se construyen con las hijas inmediatas "
        "de impuestos directos (`1.1.01.01`) e impuestos indirectos (`1.1.01.02`). "
        "Los ingresos no tributarios principales se construyen con las hijas inmediatas "
        "de `1.1.02`. La clasificación se realiza por código CUIPO/CCPET y no por el "
        "nombre textual de la cuenta. Cada valor corresponde a la cuenta exacta mostrada, "
        "sin sumar descendientes, para evitar doble conteo."
    )

    # ════════════════════════════════════════════════════════════════════════
    # SECCIÓN 3 — SERIE DE TIEMPO nominal vs real
    # Misma lógica de jerarquía: total = recaudo_exacto('1') por período
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.subheader("Histórico del recaudo — nominal vs real (millones de pesos)")

    @st.cache_data(ttl=600, show_spinner=False)
    def obtener_historico_ingresos_ejing(codigo_entidad):
        codigo_entidad = str(int(float(codigo_entidad)))
        url = "https://www.datos.gov.co/resource/9axr-9gnb.csv"
        params = {
            '$limit': 500000,
            '$select': 'periodo,cuenta,nombre_cuenta,total_recaudo',
            '$where': f"codigo_entidad='{codigo_entidad}'"
        }
        try:
            r = requests.get(url, params=params, timeout=90)
            r.raise_for_status()
            df_h = pd.read_csv(io.StringIO(r.text))
            return df_h if not df_h.empty else pd.DataFrame()
        except Exception as e:
            st.warning(f"No se pudo obtener el histórico: {e}")
            return pd.DataFrame()

    with st.spinner("Cargando histórico..."):
        df_hist_raw = obtener_historico_ingresos_ejing(cod_ent)

    registros_hist = []
    if not df_hist_raw.empty:
        df_hist_raw['total_recaudo'] = pd.to_numeric(df_hist_raw['total_recaudo'], errors='coerce').fillna(0)
        df_hist_raw['cuenta']        = df_hist_raw['cuenta'].astype(str).str.strip()
        df_hist_raw['periodo_dt']    = pd.to_datetime(
            df_hist_raw['periodo'].astype(str).str.zfill(8), format='%Y%m%d', errors='coerce'
        )
        df_hist_raw = df_hist_raw.dropna(subset=['periodo_dt'])
        df_hist_raw['year'] = df_hist_raw['periodo_dt'].dt.year
        df_hist_raw['md']   = df_hist_raw['periodo_dt'].dt.strftime('%m%d')

        # Agrupar por período+cuenta (elimina duplicados por fuente)
        df_hg = df_hist_raw.groupby(
            ['periodo', 'periodo_dt', 'year', 'md', 'cuenta', 'nombre_cuenta'],
            as_index=False
        )['total_recaudo'].sum()

        año_actual = df_hg['year'].max()
        for yr, grp in df_hg.groupby('year'):
            if yr != año_actual:
                corte = grp[grp['md'] == '1201']
                corte = corte if not corte.empty else grp[grp['periodo_dt'] == grp['periodo_dt'].max()]
            else:
                corte = grp[grp['periodo_dt'] == grp['periodo_dt'].max()]

            # Total del período: usar cuenta exacta '1'
            total_h = corte.loc[corte['cuenta'] == '1', 'total_recaudo'].sum()
            # Si '1' no existe, usar el nivel 1 más alto disponible
            if total_h == 0:
                corte_ag = corte.groupby(['cuenta','nombre_cuenta'], as_index=False)['total_recaudo'].sum()
                corte_ag['nivel'] = corte_ag['cuenta'].apply(lambda c: len(str(c).split('.')))
                total_h = corte_ag[corte_ag['nivel'] == 1]['total_recaudo'].sum()

            if total_h > 0:
                registros_hist.append({
                    'año': yr,
                    'periodo_dt': corte['periodo_dt'].max(),
                    'recaudo': total_h
                })

    if registros_hist:
        ipc_map = {2019: 97.46, 2020: 100.00, 2021: 111.41, 2022: 126.03, 2023: 137.09, 2024: 144.88}
        df_serie = pd.DataFrame(registros_hist).sort_values('periodo_dt')
        df_serie['Recaudo Nominal'] = (df_serie['recaudo'] / 1e6).round(1)
        df_serie['ipc']             = df_serie['año'].map(ipc_map)
        df_serie['Recaudo Real']    = df_serie.apply(
            lambda r: round(r['Recaudo Nominal'] / r['ipc'] * 100, 1) if pd.notna(r['ipc']) else None,
            axis=1
        )

        df_long = df_serie.melt(
            id_vars=['periodo_dt'],
            value_vars=['Recaudo Nominal', 'Recaudo Real'],
            var_name='Tipo', value_name='Monto'
        ).dropna(subset=['Monto'])

        min_val = df_long['Monto'].min() * 0.9
        chart_hist = alt.Chart(df_long).mark_line(point=True).encode(
            x=alt.X('year(periodo_dt):O', title='Año'),
            y=alt.Y('Monto:Q',
                    title='Recaudo (millones de pesos)',
                    scale=alt.Scale(domainMin=min_val),
                    axis=alt.Axis(format='$,.0f')),
            color=alt.Color('Tipo:N', legend=alt.Legend(title='Serie')),
            tooltip=[
                alt.Tooltip('year(periodo_dt):O', title='Año'),
                alt.Tooltip('Tipo:N', title='Serie'),
                alt.Tooltip('Monto:Q', format='$,.1f', title='Millones COP'),
            ]
        ).properties(width=700, height=350)
        st.altair_chart(chart_hist, use_container_width=True)

        if len(df_serie) >= 2:
            primer  = df_serie.iloc[0]
            ultimo  = df_serie.iloc[-1]
            var_nom = round(
                (ultimo['Recaudo Nominal'] - primer['Recaudo Nominal']) / primer['Recaudo Nominal'] * 100, 1
            )
            st.markdown("**Tendencia histórica**")
            st.markdown(
                f"- El recaudo pasó de **$ {primer['Recaudo Nominal']:,.1f} M** ({int(primer['año'])}) "
                f"a **$ {ultimo['Recaudo Nominal']:,.1f} M** ({int(ultimo['año'])}), "
                f"variación nominal de **{var_nom:+.1f}%**."
            )
            if pd.notna(ultimo.get('Recaudo Real')) and pd.notna(primer.get('Recaudo Real')):
                var_real = round(
                    (ultimo['Recaudo Real'] - primer['Recaudo Real']) / primer['Recaudo Real'] * 100, 1
                )
                st.markdown(
                    f"- En términos reales (pesos constantes base 2021), "
                    f"la variación fue **{var_real:+.1f}%**."
                )
        st.caption(
            "El total histórico usa el valor exacto de la cuenta '1' (Total ingresos) reportado por la entidad, "
            "no la suma de sus descendientes."
        )
    else:
        st.info("No hay datos históricos suficientes para esta entidad.")

    # ════════════════════════════════════════════════════════════════════════
    # SECCIÓN 4 — EXPORTAR EXCEL
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    output_ejing = io.BytesIO()
    with pd.ExcelWriter(output_ejing, engine='xlsxwriter') as writer:
        wb      = writer.book
        fmt_num = wb.add_format({'num_format': '#,##0.0'})
        fmt_pct = wb.add_format({'num_format': '0.0"%"'})

        # Hoja 1: tributarios principales
        if "df_tributarios_principales" in locals() and not df_tributarios_principales.empty:
            exp_tri = df_tributarios_principales[
                ["cuenta", "nombre_cuenta", "total_recaudo", "pct_total", "pct_grupo"]
            ].copy()

            exp_tri.columns = [
                "Cuenta",
                "Ingreso tributario",
                "Recaudo (pesos)",
                "% del total",
                "% de tributarios"
            ]

            exp_tri.to_excel(writer, index=False, sheet_name="Tributarios principales")
            ws1 = writer.sheets["Tributarios principales"]
            ws1.set_column(2, 2, None, fmt_num)
            ws1.set_column(3, 4, None, fmt_pct)

        # Hoja 2: no tributarios principales
        if "df_no_tributarios_principales" in locals() and not df_no_tributarios_principales.empty:
            exp_notri = df_no_tributarios_principales[
                ["cuenta", "nombre_cuenta", "total_recaudo", "pct_total", "pct_grupo"]
            ].copy()

            exp_notri.columns = [
                "Cuenta",
                "Ingreso no tributario",
                "Recaudo (pesos)",
                "% del total",
                "% de no tributarios"
            ]

            exp_notri.to_excel(writer, index=False, sheet_name="No tributarios principales")
            ws_nt = writer.sheets["No tributarios principales"]
            ws_nt.set_column(2, 2, None, fmt_num)
            ws_nt.set_column(3, 4, None, fmt_pct)

        # Hoja 2: árbol jerárquico completo
        exp_arbol = base[['cuenta','nombre_cuenta','nivel_cuenta','tiene_hijas','es_hoja','total_recaudo']].sort_values('cuenta').copy()
        exp_arbol.to_excel(writer, index=False, sheet_name='Árbol jerárquico')
        ws2 = writer.sheets['Árbol jerárquico']
        ws2.set_column(5, 5, None, fmt_num)

        # Hoja 3: serie histórica
        if registros_hist:
            df_serie[['año','Recaudo Nominal','Recaudo Real']].to_excel(
                writer, index=False, sheet_name='Serie histórica'
            )
            ws3 = writer.sheets['Serie histórica']
            ws3.set_column(1, 2, None, fmt_num)

        # Hoja 4: alertas de consistencia
        if alertas:
            pd.DataFrame(alertas).to_excel(writer, index=False, sheet_name='Alertas consistencia')

    st.download_button(
        label="Excel",
        data=output_ejing.getvalue(),
        file_name=f"ejecucion_ingresos_{ent}_{periodo}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )













    

























