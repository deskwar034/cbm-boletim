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
# 2. FUNÇÃO DE EXTRAÇÃO
# ==========================================
def extrair_alteracoes_exatas(texto_bg, nome_militar):
    blocos_de_nota = re.split(r'(?i)NOTA N\.\s*', texto_bg)
    resultados = []
    
    for bloco in blocos_de_nota:
        # Busca flexível ignorando quebras de linha no meio do nome
        nome_regex = re.escape(nome_militar).replace(r'\ ', r'\s+')
        if re.search(nome_regex, bloco, re.IGNORECASE):
            match_nota = re.match(r'(\d+)', bloco)
            numero_nota = match_nota.group(1) if match_nota else "Desconhecida"
            
            cabecalho = re.split(r'\(\d+\)', bloco)[0]
            cabecalho_limpo = re.sub(r'\s+', ' ', cabecalho).strip()
            
            itens = re.split(r'(?=\(\d+\))', bloco)
            itens_encontrados = []
            
            for item in itens:
                if re.search(nome_regex, item, re.IGNORECASE):
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
# 3. INTERFACE DO APLICATIVO
# ==========================================
st.set_page_config(page_title="Buscador de BG - CBMMS", page_icon="🚒")

st.title("🚒 Buscador Inteligente do BG - CBMMS")

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
# 4. LÓGICA DE REQUISIÇÕES
# ==========================================
if btn_buscar:
    if not usuario or not senha or not nome_busca:
        st.warning("Preencha todos os campos obrigatórios.")
    else:
        # Ajustado para o formato exato da requisição interceptada (com 03:00:00.000Z)
        data_inicial_iso = data_inicial.strftime("%Y-%m-%dT03:00:00.000Z")
        data_final_iso = data_final.strftime("%Y-%m-%dT03:00:00.000Z")
        
        with st.spinner("Conectando e buscando..."):
            sessao = requests.Session()
            sessao.headers.update({"Content-Type": "application/json"})
            payload_login = {"login": usuario, "senha": senha}
            
            try:
                resposta_login = sessao.post(LOGIN_URL, json=payload_login)
                
                if resposta_login.status_code == 200:
                    dados_login = resposta_login.json()
                    
                    token = None
                    if isinstance(dados_login, dict): token = dados_login.get("token")
                    elif isinstance(dados_login, list) and len(dados_login) > 0 and isinstance(dados_login[0], dict): token = dados_login[0].get("token")
                    if not token: token = resposta_login.headers.get("token")
                    if token: sessao.headers.update({"token": token})
                        
                    # ==============================================
                    # PARÂMETROS CORRETOS IDENTIFICADOS NO SISTEMA
                    # ==============================================
                    params_busca = {
                        "de": data_inicial_iso,
                        "ate": data_final_iso,
                        "tipo": "buscaExata",
                        "conteudo": nome_busca
                    }
                    
                    # O "requests" cuida de transformar os espaços em %20 automaticamente
                    resposta_busca = sessao.get(BUSCA_BG_URL, params=params_busca)
                    
                    if resposta_busca.status_code == 200:
                        publicacoes_brutas = resposta_busca.json()
                        lista_pubs = publicacoes_brutas if isinstance(publicacoes_brutas, list) else publicacoes_brutas.get("content", publicacoes_brutas.get("data", []))
                        
                        if not lista_pubs:
                            st.warning(f"Nenhum boletim encontrado contendo o nome '{nome_busca}' neste período.")
                        else:
                            encontrou_algo = False
                            st.write(f"A API do sistema retornou {len(lista_pubs)} boletim(ns) com o seu nome! Baixando e extraindo a nota exata...")
                            
                            for pub in lista_pubs:
                                if not isinstance(pub, dict): continue
                                
                                upload_id = pub.get("upload", {}).get("id") if isinstance(pub.get("upload"), dict) else pub.get("uploadId")
                                num_bg = pub.get("numero", "S/N")
                                
                                if upload_id:
                                    url_pdf = f"{DOWNLOAD_BG_URL}{upload_id}"
                                    resposta_pdf = sessao.get(url_pdf)
                                    
                                    if resposta_pdf.status_code == 200:
                                        arquivo_pdf = io.BytesIO(resposta_pdf.content)
                                        leitor = PdfReader(arquivo_pdf)
                                        texto_completo = "".join(pagina.extract_text() + "\n" for pagina in leitor.pages)
                                            
                                        resultados = extrair_alteracoes_exatas(texto_completo, nome_busca)
                                        
                                        if resultados:
                                            encontrou_algo = True
                                            st.subheader(f"📄 BG Nº {num_bg}")
                                            for res in resultados:
                                                with st.expander(f"📌 NOTA N. {res['nota']}", expanded=True):
                                                    st.markdown(f"**Contexto:** {res['cabecalho']}")
                                                    for item in res['itens']:
                                                        st.error(item)
                                                        
                            if not encontrou_algo:
                                st.info("O sistema encontrou o Boletim, mas o extrator não conseguiu isolar o parágrafo (Pode haver quebras de linha complexas no PDF).")
                    else:
                        st.error("Erro ao buscar a lista de boletins. Verifique se o sistema está online.")
                else:
                    st.error("Falha no login. Verifique suas credenciais.")
                    
            except Exception as e:
                st.error(f"Erro de comunicação com o servidor: {str(e)}")
