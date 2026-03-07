import streamlit as st
import requests
import re
import io
import datetime
from pypdf import PdfReader

# ==========================================
# 1. CONFIGURAÇÕES DA API (Endpoints)
# ==========================================
BASE_URL = "https://sistemas.bombeiros.ms.gov.br"
LOGIN_URL = f"{BASE_URL}/ws-auth/fazer-login"
USUARIO_LOGADO_URL = f"{BASE_URL}/ws-auth/usuario-logado"
BUSCA_BG_URL = f"{BASE_URL}/ws-boletim-geral/publicacao"
DOWNLOAD_BG_URL = f"{BASE_URL}/ws-alfresco/arquivo/"

# ==========================================
# 2. FUNÇÃO DE EXTRAÇÃO (Regex Refinado)
# ==========================================
def extrair_alteracoes_exatas(texto_bg, nome_militar):
    blocos_de_nota = re.split(r'(?i)NOTA N\.\s*', texto_bg)
    resultados = []
    
    for bloco in blocos_de_nota:
        if re.search(nome_militar, bloco, re.IGNORECASE):
            match_nota = re.match(r'(\d+)', bloco)
            numero_nota = match_nota.group(1) if match_nota else "Desconhecida"
            
            cabecalho = re.split(r'\(\d+\)', bloco)[0]
            cabecalho_limpo = re.sub(r'\s+', ' ', cabecalho).strip()
            
            itens = re.split(r'(?=\(\d+\))', bloco)
            itens_encontrados = []
            
            for item in itens:
                if re.search(nome_militar, item, re.IGNORECASE):
                    item_limpo = re.sub(r'\s+', ' ', item).strip()
                    item_limpo = re.sub(r';$', '.', item_limpo)
                    itens_encontrados.append(item_limpo)
            
            if itens_encontrados:
                resultados.append({
                    "nota": numero_nota,
                    "cabecalho": cabecalho_limpo,
                    "itens": itens_encontrados
                })
                
    return resultados

# ==========================================
# 3. INTERFACE DO APLICATIVO (Streamlit)
# ==========================================
st.set_page_config(page_title="Buscador de BG - CBMMS", page_icon="🚒")

st.title("🚒 Buscador Inteligente do BG - CBMMS")
st.markdown("Busca automatizada de alterações funcionais no Boletim Geral.")

with st.form("login_form"):
    st.subheader("1. Credenciais de Acesso")
    usuario = st.text_input("Login (CPF)")
    senha = st.text_input("Senha", type="password")
    
    st.subheader("2. Parâmetros da Busca")
    nome_busca = st.text_input("Nome exato para buscar", value="Geraldo Roberto Dias")
    
    col1, col2 = st.columns(2)
    with col1:
        data_inicial = st.date_input("Data Inicial", datetime.date.today() - datetime.timedelta(days=30))
    with col2:
        data_final = st.date_input("Data Final", datetime.date.today())
        
    btn_buscar = st.form_submit_button("Entrar e Buscar")

# ==========================================
# 4. LÓGICA DE NAVEGAÇÃO E REQUISIÇÕES
# ==========================================
if btn_buscar:
    if not usuario or not senha or not nome_busca:
        st.warning("Preencha todos os campos obrigatórios.")
    else:
        # Formata as datas para o padrão ISO exigido pela API
        data_inicial_iso = data_inicial.strftime("%Y-%m-%dT00:00:00.000Z")
        data_final_iso = data_final.strftime("%Y-%m-%dT23:59:59.999Z")
        
        with st.spinner("Autenticando no sistema CBMMS..."):
            sessao = requests.Session()
            sessao.headers.update({"Content-Type": "application/json"})
            
            payload_login = {
                "login": usuario,
                "senha": senha
            }
            
            try:
                # Passo 1: Login
                resposta_login = sessao.post(LOGIN_URL, json=payload_login)
                
                if resposta_login.status_code == 200:
                    dados_login = resposta_login.json()
                    
                    # Captura o token se a API retornar no corpo do JSON ou Header
                    token = dados_login.get("token") or resposta_login.headers.get("token")
                    if token:
                        sessao.headers.update({"token": token})
                        
                    st.success("Autenticação realizada com sucesso!")
                    
                    # Passo 2: Buscar BGs no intervalo de datas
                    st.info(f"Buscando boletins entre {data_inicial.strftime('%d/%m/%Y')} e {data_final.strftime('%d/%m/%Y')}...")
                    params_busca = {
                        "de": data_inicial_iso,
                        "ate": data_final_iso,
                        "tipo": "buscaPorPalavra"
                    }
                    
                    resposta_busca = sessao.get(BUSCA_BG_URL, params=params_busca)
                    
                    if resposta_busca.status_code == 200:
                        publicacoes = resposta_busca.json()
                        
                        if not publicacoes:
                            st.warning("Nenhum Boletim Geral encontrado neste período.")
                        else:
                            st.write(f"Encontrados {len(publicacoes)} boletins. Iniciando leitura...")
                            
                            encontrou_algo = False
                            
                            # Passo 3: Baixar e processar cada PDF encontrado
                            for pub in publicacoes:
                                # A API pode retornar o id dentro de um objeto 'upload'
                                upload_id = pub.get("upload", {}).get("id") or pub.get("uploadId")
                                num_bg = pub.get("numero", "S/N")
                                
                                if upload_id:
                                    url_pdf = f"{DOWNLOAD_BG_URL}{upload_id}"
                                    resposta_pdf = sessao.get(url_pdf)
                                    
                                    if resposta_pdf.status_code == 200:
                                        arquivo_pdf = io.BytesIO(resposta_pdf.content)
                                        leitor = PdfReader(arquivo_pdf)
                                        
                                        texto_completo = ""
                                        for pagina in leitor.pages:
                                            texto_completo += pagina.extract_text() + "\n"
                                            
                                        # Extrai as informações usando o Regex
                                        resultados = extrair_alteracoes_exatas(texto_completo, nome_busca)
                                        
                                        # Passo 4: Exibe os resultados
                                        if resultados:
                                            encontrou_algo = True
                                            st.subheader(f"📄 BG Nº {num_bg}")
                                            for res in resultados:
                                                with st.expander(f"📌 NOTA N. {res['nota']}", expanded=True):
                                                    st.markdown(f"**Contexto:** {res['cabecalho']}")
                                                    for item in res['itens']:
                                                        st.error(item) # st.error deixa o bloco destacado em vermelho/rosa
                                                        
                            if not encontrou_algo:
                                st.success(f"Busca finalizada! O nome '{nome_busca}' não consta nos boletins deste período.")
                    else:
                        st.error("Erro ao buscar a lista de boletins.")
                else:
                    st.error("Falha no login. Verifique seu usuário e senha.")
                    
            except Exception as e:
                st.error(f"Ocorreu um erro de comunicação com o servidor: {e}")
