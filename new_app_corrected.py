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
    ["Programación de Ingresos", "Comparativa Per Cápita", "Ejecución de Gastos"]
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

    # ————— Registrar Montserrat —————
    FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
    pdf.add_font('Montserrat',   '',  os.path.join(FONT_DIR, 'Montserrat-Regular.ttf'),   uni=True)
    pdf.add_font('Montserrat',   'B', os.path.join(FONT_DIR, 'Montserrat-Bold.ttf'),      uni=True)
    pdf.add_font('Montserrat',   'I', os.path.join(FONT_DIR, 'Montserrat-Italic.ttf'),    uni=True)

    # 1) Logo
    pdf.image("pdigitalazul.png", x=10, y=8, w=60)
    pdf.ln(20)

    # 2) Título de variable
    pdf.set_font("Montserrat", "B", 14)
    pdf.set_x(10)
    pdf.cell(0, 8, st.session_state['cuenta_comparativa'], ln=True, align="C")
    pdf.ln(5)

    # 3) Informe
    pdf.set_font("Montserrat", "B", 12)
    pdf.cell(0, 8, "Informe", ln=True)
    pdf.set_font("Montserrat", "", 10)
    for line in st.session_state['informe'].split("\n"):
        pdf.multi_cell(0, 5, line)
    pdf.ln(5)

    # 4) Gráfico con Matplotlib para el PDF
    pdf.set_font("Montserrat", "B", 12)
    pdf.cell(0, 10, f"Comparativa Per Cápita - {st.session_state['entity']}", ln=True, align="L")
    pdf.ln(5)

    # Preparamos datos
    df_plot = st.session_state['df_bar_fmt'].copy()
    # 'df_bar_fmt' sólo tiene Tipo y COP per cápita como string,
    # así que volvemos a los números:
    tipos = [r for r in df_plot['Tipo']]
    valores = [int(r.replace("$","").replace(" ","").replace(",","")) for r in df_plot['COP per cápita']]

    # Creamos figura
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.bar(tipos, valores)
    ax.set_ylabel("COP per cápita")
    ax.set_ylim(0, max(valores) * 1.1)
    # Creamos figura
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.bar(tipos, valores)
    ax.set_ylabel("COP per cápita")
    ax.set_ylim(0, max(valores) * 1.1)

    # Rotamos etiquetas con labelrotation y alineamos con setp
    ax.tick_params(axis="x", labelrotation=30)
    plt.setp(ax.get_xticklabels(), ha="right")

    # Formateamos las etiquetas del eje Y
    ax.yaxis.set_major_formatter(lambda x, pos: f"$ {int(x):,}")
    fig.tight_layout()

    # Guardamos a PNG (fondo blanco sin alpha)
    tmp_fig = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    fig.savefig(tmp_fig.name, dpi=150)
    plt.close(fig)

    # Insertamos en el PDF
    pdf.image(tmp_fig.name, x=10, w=190)
    pdf.ln(20)



    # 5) Tablas
    pdf.set_font("Montserrat", "B", 12)
    pdf.cell(0, 8, "Valores per cápita", ln=True)
    pdf.set_font("Montserrat", "", 10)
    for _, r in st.session_state['df_bar_fmt'].iterrows():
        pdf.cell(0, 6, f"{r['Tipo']}: {r['COP per cápita']}", ln=True)
    pdf.ln(5)

    pdf.set_font("Montserrat", "B", 12)
    pdf.cell(0, 8, f"Per cápita {st.session_state['label'].lower()}s categoría {st.session_state['cat']}", ln=True)
    pdf.set_font("Montserrat", "B", 10)
    pdf.cell(80, 6, st.session_state['label'], 1)
    pdf.cell(40, 6, "Per cápita", 1)
    pdf.cell(60, 6, "Valor Absoluto", 1, ln=True)
    pdf.set_font("Montserrat", "", 10)
    for _, r in st.session_state['df_cat'].iterrows():
        pdf.cell(80, 6, r[st.session_state['label']], 1)
        pdf.cell(40, 6, r['Per cápita'], 1)
        pdf.cell(60, 6, r['Valor Absoluto (millones)'], 1, ln=True)

    # 6) Texto + QR
    pdf.ln(10)
    pdf.set_font("Montserrat", "I", 10)
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
        data=pdf.output(dest="S").encode("latin-1"),
        file_name=f"reporte_comparativa_{st.session_state['entity']}_{st.session_state['cuenta_comparativa']}.pdf",
        mime="application/pdf"
    )





# ===============================
# Página: Ejecución de Gastos
# ===============================
elif pagina == "Ejecución de Gastos":
    st.title("Ejecución de Gastos")

    def format_cop(x):
        try:
            return f"$ {float(x):,.0f}"
        except:
            return "$ 0"

    nivel = st.sidebar.selectbox("Selecciona el nivel", ["Municipios", "Gobernaciones"])
    if nivel == "Municipios":
        departamentos = sorted(df_mun["departamento"].dropna().astype(str).unique())
        dep_sel = st.sidebar.selectbox("Selecciona el departamento", departamentos)
        df_entidades = df_mun[df_mun["departamento"] == dep_sel]
        label_ent = "Selecciona el municipio"
    else:
        df_entidades = df_dep
        label_ent = "Selecciona la gobernación"

    ent_sel = st.sidebar.selectbox(label_ent, df_entidades['nombre_entidad'].dropna().astype(str).unique().tolist())
    codigo_ent = df_entidades.loc[df_entidades['nombre_entidad'] == ent_sel, 'codigo_entidad'].iloc[0]

     # Selección de periodo (filtrado por años y trimestres completos)
    import datetime
    today = datetime.date.today()
    current_year = today.year
    current_month = today.month
    current_quarter = (current_month - 1) // 3 + 1
    last_full_quarter = current_quarter - 1 if current_quarter > 1 else 0

    df_per['periodo_str'] = df_per['periodo'].astype(str).str.zfill(8)
    df_per['year']       = df_per['periodo_str'].str[:4].astype(int)
    df_per['month']      = df_per['periodo_str'].str[4:6].astype(int)

    df_per_filt = df_per[df_per['year'] <= current_year]
    if last_full_quarter > 0:
        df_per_filt = df_per_filt[~(
            (df_per_filt['year'] == current_year) &
            (df_per_filt['month'] > last_full_quarter * 3)
        )]
    else:
        df_per_filt = df_per_filt[df_per_filt['year'] < current_year]

    df_per_filt = df_per_filt.sort_values('periodo')
    per_dict = dict(zip(df_per_filt['periodo_label'], df_per_filt['periodo']))
    per_lab  = st.sidebar.selectbox("Período:", list(per_dict.keys()), key="per_gastos")
    periodo  = str(per_dict[per_lab])

    # Botón con key único
    if st.sidebar.button("Cargar datos de gastos", key="btn_cargar_gastos"):
        with st.spinner("Obteniendo datos desde la API..."):
            df_gastos = obtener_datos_gastos(codigo_ent, periodo)
            st.session_state['df_gastos'] = df_gastos

    if 'df_gastos' in st.session_state:
        df_raw = st.session_state['df_gastos']
        if df_raw.empty:
            st.warning(
                f"No se encontraron datos de gastos para la entidad '{ent_sel}' "
                f"y periodo '{per_lab}'."
            )
            st.stop()

        with st.expander("Datos brutos"):
            st.dataframe(df_raw.style.format({
                'compromisos': format_cop,
                'pagos': format_cop,
                'obligaciones': format_cop
            }), use_container_width=True, hide_index=True)

        cuentas_filtro = [
            "2", "2.1.1", "2.1.2.01.01.001", "2.1.2.01.01.003", "2.1.2.01.01.004",
            "2.1.2.01.01.005", "2.1.2.01.02", "2.1.2.01.03", "2.1.2.02.01",
            "2.1.2.02.02", "2.1.3.01", "2.1.3.02.01", "2.1.3.02.02", "2.1.3.02.03",
            "2.1.3.02.04", "2.1.3.02.05", "2.1.3.02.06", "2.1.3.02.07", "2.1.3.02.08",
            "2.1.3.02.09", "2.1.3.02.10", "2.1.3.02.11", "2.1.3.02.12", "2.1.3.02.13",
            "2.1.3.02.14", "2.1.3.02.15", "2.1.3.02.16", "2.1.3.02.17", "2.1.3.02.18",
            "2.1.3.03", "2.1.3.04", "2.1.3.05.01", "2.1.3.05.04", "2.1.3.05.07",
            "2.1.3.05.08", "2.1.3.05.09", "2.1.3.06", "2.1.3.07.02", "2.1.3.07.03",
            "2.1.3.08", "2.1.3.09", "2.1.3.10", "2.1.3.11.02", "2.1.3.11.03",
            "2.1.3.12", "2.1.3.13", "2.1.3.14", "2.1.4.02", "2.1.4.03", "2.1.4.04",
            "2.1.4.07", "2.1.5.01", "2.1.5.02", "2.1.6.01", "2.1.6.02", "2.1.6.03",
            "2.1.7.01", "2.1.7.02", "2.1.7.03", "2.1.7.04", "2.1.7.05", "2.1.7.06",
            "2.1.7.09", "2.1.8", "2.2.1", "2.2.2", "2.3.1", "2.3.2.01.01.001",
            "2.3.2.01.01.003", "2.3.2.01.01.004", "2.3.2.01.01.005", "2.3.2.01.02",
            "2.3.2.01.03", "2.3.2.02.01", "2.3.2.02.02", "2.3.3.01.02", "2.3.3.01.04",
            "2.3.3.02.01", "2.3.3.02.02", "2.3.3.02.03", "2.3.3.02.04", "2.3.3.02.05",
            "2.3.3.02.06", "2.3.3.02.07", "2.3.3.02.08", "2.3.3.02.09", "2.3.3.02.10",
            "2.3.3.02.11", "2.3.3.02.12", "2.3.3.02.13", "2.3.3.02.14", "2.3.3.02.15",
            "2.3.3.02.16", "2.3.3.02.17", "2.3.3.02.18", "2.3.3.03", "2.3.3.04",
            "2.3.3.05", "2.3.3.06", "2.3.3.07.01", "2.3.3.07.02", "2.3.3.08",
            "2.3.3.09", "2.3.3.11", "2.3.3.12", "2.3.3.13", "2.3.3.14", "2.3.4.01",
            "2.3.4.02", "2.3.4.03", "2.3.4.04", "2.3.4.07", "2.3.4.09", "2.3.5.01",
            "2.3.5.02", "2.3.6.01", "2.3.6.02", "2.3.6.03", "2.3.7.01", "2.3.7.05",
            "2.3.7.06", "2.3.8"
        ]

        df_filtered = df_raw[
            df_raw['cuenta'].isin(cuentas_filtro) &
            df_raw['nom_vigencia_del_gasto'].fillna('').str.strip().str.upper().eq('VIGENCIA ACTUAL') &
            df_raw['nombre_cuenta'].str.upper().ne('GASTOS')
        ]

        resumen = df_filtered.groupby(['cuenta', 'nombre_cuenta'], as_index=False)[['compromisos', 'pagos', 'obligaciones']].sum()
        totales = resumen[['compromisos', 'pagos', 'obligaciones']].sum().to_dict()
        resumen.loc[len(resumen.index)] = ['', 'TOTAL', totales['compromisos'], totales['pagos'], totales['obligaciones']]
        resumen[['compromisos','pagos','obligaciones']] = resumen[['compromisos','pagos','obligaciones']] / 1_000_000
        resumen = resumen.rename(columns={
            'cuenta': 'Cuenta',
            'nombre_cuenta': 'Nombre cuenta',
            'compromisos': 'Compromisos',
            'pagos': 'Pagos',
            'obligaciones': 'Obligaciones'
        })
        st.subheader("Resumen por cuenta (millones de pesos) - Vigencia Actual")
        st.dataframe(resumen.style.format({
            'Compromisos': format_cop,
            'Pagos': format_cop,
            'Obligaciones': format_cop
        }), use_container_width=True, hide_index=True)

        # ========= CONSOLIDADO por tipo de vigencia =========
        vigencias = ["VIGENCIA ACTUAL","RESERVAS","VIGENCIAS FUTURAS - RESERVAS","CUENTAS POR PAGAR","VIGENCIAS FUTURAS - VIGENCIA ACTUAL"]
        df_consol = df_raw[df_raw['nombre_cuenta'].str.upper() == 'GASTOS']
        df_consol = df_consol[df_consol['nom_vigencia_del_gasto'].str.upper().isin(vigencias)]
        consolidado = df_consol.groupby("nom_vigencia_del_gasto", as_index=False)[['compromisos','pagos','obligaciones']].sum()
        tot = consolidado[['compromisos','pagos','obligaciones']].sum().to_dict()
        consolidado.loc[len(consolidado.index)] = ['TOTAL', tot['compromisos'], tot['pagos'], tot['obligaciones']]
        consolidado[['compromisos','pagos','obligaciones']] = consolidado[['compromisos','pagos','obligaciones']] / 1_000_000
        consolidado = consolidado.rename(columns={
            'nom_vigencia_del_gasto': 'Vigencia del gasto',
            'compromisos': 'Compromisos',
            'pagos': 'Pagos',
            'obligaciones': 'Obligaciones'
        })
        st.subheader("Consolidado por tipo de vigencia (millones de pesos)")
        st.dataframe(consolidado.style.format({
            'Compromisos': format_cop,
            'Pagos': format_cop,
            'Obligaciones': format_cop
        }), use_container_width=True, hide_index=True)

        st.metric("Total compromisos todas las vigencias", format_cop(tot['compromisos']/ 1e6))

                # --- Consolidado por sección presupuestal ---
        df_sec = df_raw[
            (df_raw['cuenta'] == '2') &
            (df_raw['nombre_cuenta'].str.upper() == 'GASTOS') &
            (df_raw['nom_vigencia_del_gasto'].fillna('').str.strip().str.upper() == 'VIGENCIA ACTUAL')
        ]
        consolidado_secc = df_sec.groupby(
            'nom_seccion_presupuestal',
            as_index=False
        )[['compromisos','pagos','obligaciones']].sum()
        # agregamos fila TOTAL
        tot_secc = consolidado_secc[['compromisos','pagos','obligaciones']].sum().to_dict()
        consolidado_secc.loc[len(consolidado_secc)] = [
            'TOTAL',
            tot_secc['compromisos'],
            tot_secc['pagos'],
            tot_secc['obligaciones']
        ]
        # pasamos a millones y renombramos columnas
        consolidado_secc[['compromisos','pagos','obligaciones']] = consolidado_secc[['compromisos','pagos','obligaciones']] / 1_000_000
        consolidado_secc = consolidado_secc.rename(columns={
            'nom_seccion_presupuestal': 'Sección presupuestal',
            'compromisos': 'Compromisos',
            'pagos': 'Pagos',
            'obligaciones': 'Obligaciones'
        })
        # **Aquí eliminamos todo lo anterior al guión, dejando sólo el texto posterior**
        consolidado_secc['Sección presupuestal'] = consolidado_secc['Sección presupuestal']\
            .str.replace(r'^.*?-\s*', '', regex=True)
        # finalmente ordenamos y reseteamos índice
        consolidado_secc = consolidado_secc.sort_values('Compromisos', ascending=False).reset_index(drop=True)

        st.subheader("Consolidado por sección presupuestal (millones de pesos) - Vigencia Actual")
        st.dataframe(
            consolidado_secc.style.format({
                'Compromisos': format_cop,
                'Pagos': format_cop,
                'Obligaciones': format_cop
            }),
            use_container_width=True,
            hide_index=True
        )


        # ————— más espacio antes de la gráfica —————
        st.markdown("<br><br>", unsafe_allow_html=True)

       # gráfica de barras de compromisos por sección presupuestal 
        df_plot_sec = consolidado_secc[
            consolidado_secc['Sección presupuestal'] != 'TOTAL'
        ][['Sección presupuestal', 'Compromisos']]

        max_val = df_plot_sec['Compromisos'].max() * 1.1  # un 10% de margen

        chart_sec = alt.Chart(df_plot_sec).mark_bar(cornerRadius=4).encode(
            x=alt.X(
                'Sección presupuestal:N',
                sort='-y',
                title='',  
                axis=alt.Axis(
                    labels=False,   # oculta los nombres de cada barra
                    ticks=False     # oculta las marcas de tick
                )
            ),
            y=alt.Y(
                'Compromisos:Q',
                title='Compromisos (millones)',
                scale=alt.Scale(type='sqrt', domain=[0, max_val]),  # raíz cuadrada
                axis=alt.Axis(format='$,.0f')
            ),
            tooltip=[
                alt.Tooltip('Sección presupuestal:N'),
                alt.Tooltip('Compromisos:Q', format='$,.0f')
            ]
        ).properties(width=700, height=400)

        st.altair_chart(chart_sec, use_container_width=True)

                















    

























