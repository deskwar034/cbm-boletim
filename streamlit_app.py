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
# 4. LÓGICA DE REQUISIÇÕES E LOGS
# ==========================================
if btn_buscar:
    if not usuario or not senha or not nome_busca:
        st.warning("Preencha todos os campos obrigatórios.")
    else:
        data_inicial_iso = data_inicial.strftime("%Y-%m-%dT00:00:00.000Z")
        data_final_iso = data_final.strftime("%Y-%m-%dT23:59:59.999Z")
        
        # Variáveis para armazenar logs que serão exibidos no final
        logs_debug = []
        
        with st.spinner("Conectando e buscando..."):
            sessao = requests.Session()
            sessao.headers.update({"Content-Type": "application/json"})
            payload_login = {"login": usuario, "senha": senha}
            
            try:
                resposta_login = sessao.post(LOGIN_URL, json=payload_login)
                logs_debug.append(f"HTTP Status Login: {resposta_login.status_code}")
                
                if resposta_login.status_code == 200:
                    dados_login = resposta_login.json()
                    
                    token = None
                    if isinstance(dados_login, dict): token = dados_login.get("token")
                    elif isinstance(dados_login, list) and len(dados_login) > 0 and isinstance(dados_login[0], dict): token = dados_login[0].get("token")
                    if not token: token = resposta_login.headers.get("token")
                    if token: sessao.headers.update({"token": token})
                        
                    # PARÂMETROS DA BUSCA (Adicionado um chute para a chave do texto)
                    params_busca = {
                        "de": data_inicial_iso,
                        "ate": data_final_iso,
                        "tipo": "buscaExata",
                        "palavra": nome_busca, # Testando se a API aceita 'palavra'
                        "texto": nome_busca    # Testando se a API aceita 'texto'
                    }
                    
                    resposta_busca = sessao.get(BUSCA_BG_URL, params=params_busca)
                    logs_debug.append(f"URL de Busca Chamada: {resposta_busca.url}")
                    logs_debug.append(f"HTTP Status Busca: {resposta_busca.status_code}")
                    
                    if resposta_busca.status_code == 200:
                        publicacoes_brutas = resposta_busca.json()
                        lista_pubs = publicacoes_brutas if isinstance(publicacoes_brutas, list) else publicacoes_brutas.get("content", publicacoes_brutas.get("data", []))
                        
                        logs_debug.append(f"Total de itens retornados pela API: {len(lista_pubs)}")
                        
                        if not lista_pubs:
                            st.warning("Nenhum Boletim Geral encontrado neste período.")
                        else:
                            encontrou_algo = False
                            st.write(f"Baixando e analisando {len(lista_pubs)} boletim(ns)...")
                            
                            for index, pub in enumerate(lista_pubs):
                                if not isinstance(pub, dict): continue
                                
                                upload_id = pub.get("upload", {}).get("id") if isinstance(pub.get("upload"), dict) else pub.get("uploadId")
                                num_bg = pub.get("numero", "S/N")
                                logs_debug.append(f"Processando BG Nº {num_bg} | Upload ID: {upload_id}")
                                
                                if upload_id:
                                    url_pdf = f"{DOWNLOAD_BG_URL}{upload_id}"
                                    resposta_pdf = sessao.get(url_pdf)
                                    
                                    if resposta_pdf.status_code == 200:
                                        arquivo_pdf = io.BytesIO(resposta_pdf.content)
                                        leitor = PdfReader(arquivo_pdf)
                                        texto_completo = "".join(pagina.extract_text() + "\n" for pagina in leitor.pages)
                                        
                                        # LOG ESPECIAL: Mostra um pedaço do PDF do primeiro BG para vermos se a extração funcionou
                                        if index == 0:
                                            logs_debug.append(f"\n--- AMOSTRA DE TEXTO EXTRAÍDO DO BG {num_bg} ---\n{texto_completo[:1000]}\n-----------------------------------------")
                                            
                                        # LOG ESPECIAL: Tenta achar o nome puro no texto cru para ver se é culpa do Regex
                                        if nome_busca.lower() in texto_completo.lower():
                                            logs_debug.append(f"O nome '{nome_busca}' FOI ENCONTRADO no texto bruto do BG {num_bg} (o erro é no Regex).")
                                        else:
                                            logs_debug.append(f"O nome '{nome_busca}' NÃO ESTÁ no texto bruto do BG {num_bg} (a API baixou um arquivo que não tem seu nome).")
                                            
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
                                st.info(f"Busca finalizada! O nome '{nome_busca}' não foi identificado pelo extrator de texto.")
                    else:
                        st.error("Erro ao buscar a lista de boletins.")
                else:
                    st.error("Falha no login.")
                    
            except Exception as e:
                st.error("Erro de comunicação com o servidor.")
                logs_debug.append(f"EXCEÇÃO: {str(e)}")

        # Exibe o painel de logs para você copiar e colar
        with st.expander("🛠️ Logs de Depuração (Copie e cole para o chat)", expanded=False):
            st.code("\n".join(logs_debug), language="text")
