import streamlit as st
import requests
import re
import io
import datetime
from pypdf import PdfReader

# ==========================================
# 1. CONFIGURAÇÕES DA API E SESSÃO
# ==========================================
BASE_URL = "https://sistemas.bombeiros.ms.gov.br"
LOGIN_URL = f"{BASE_URL}/ws-auth/fazer-login"
USUARIO_LOGADO_URL = f"{BASE_URL}/ws-auth/usuario-logado"
BUSCA_BG_URL = f"{BASE_URL}/ws-boletim-geral/publicacao"
DOWNLOAD_BG_URL = f"{BASE_URL}/ws-alfresco/arquivo/"

if "busca_concluida" not in st.session_state:
    st.session_state.busca_concluida = False
if "bgs_encontrados" not in st.session_state:
    st.session_state.bgs_encontrados = []
if "nome_pesquisado" not in st.session_state:
    st.session_state.nome_pesquisado = ""
if "mensagem_status" not in st.session_state:
    st.session_state.mensagem_status = ""

# ==========================================
# 2. FUNÇÕES DE FORMATAÇÃO E EXTRAÇÃO
# ==========================================
def formatar_cpf(cpf_bruto):
    """Limpa a entrada do usuário e aplica a máscara oficial do CPF."""
    # Remove tudo que não for número
    cpf_limpo = re.sub(r'\D', '', cpf_bruto)
    # Completa com zeros à esquerda caso falte algum número (opcional)
    cpf_limpo = cpf_limpo.zfill(11)
    # Se o tamanho estiver correto (11 dígitos), aplica a máscara
    if len(cpf_limpo) == 11:
        return f"{cpf_limpo[:3]}.{cpf_limpo[3:6]}.{cpf_limpo[6:9]}-{cpf_limpo[9:]}"
    return cpf_bruto # Retorna do jeito que veio se tiver tamanho inválido para o sistema barrar

def extrair_alteracoes_exatas(texto_bg, nome_militar):
    blocos_de_nota = re.split(r'(?i)NOTA N\.\s*', texto_bg)
    resultados = []
    for bloco in blocos_de_nota:
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

def gerar_relatorio_txt(bgs_com_resultados, nome_busca):
    linhas = []
    linhas.append("=" * 70)
    linhas.append("RELATÓRIO DE ALTERAÇÕES FUNCIONAIS - CBMMS")
    linhas.append("=" * 70)
    linhas.append(f"Militar pesquisado: {nome_busca}")
    linhas.append(f"Data da emissão: {datetime.datetime.now().strftime('%d/%m/%Y às %H:%M:%S')}")
    linhas.append("=" * 70)
    linhas.append("")
    
    for bg in bgs_com_resultados:
        linhas.append(f"📄 BOLETIM GERAL Nº {bg['numero_bg']}")
        linhas.append("-" * 70)
        for res in bg['resultados']:
            linhas.append(f"📌 NOTA N. {res['nota']}")
            linhas.append(f"Contexto: {res['cabecalho']}")
            linhas.append("Publicação Exata:")
            for item in res['itens']:
                linhas.append(f" -> {item}")
            linhas.append("")
        linhas.append("")
        
    linhas.append("=" * 70)
    linhas.append("Documento gerado automaticamente pelo Buscador Inteligente do BG.")
    return "\n".join(linhas)

# ==========================================
# 3. INTERFACE DO APLICATIVO
# ==========================================
st.set_page_config(page_title="Buscador de BG - CBMMS", page_icon="🚒")

st.title("🚒 Buscador Inteligente do BG - CBMMS")

with st.form("login_form"):
    st.subheader("1. Credenciais de Acesso")
    st.caption("Você pode digitar seu CPF com ou sem pontos.")
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
        st.session_state.busca_concluida = False
        st.session_state.bgs_encontrados = []
        st.session_state.nome_pesquisado = nome_busca
        st.session_state.mensagem_status = ""

        # Formatação do CPF antes de enviar
        cpf_formatado = formatar_cpf(usuario)
        
        data_inicial_iso = data_inicial.strftime("%Y-%m-%dT03:00:00.000Z")
        data_final_iso = data_final.strftime("%Y-%m-%dT03:00:00.000Z")
        
        with st.status("Iniciando processo...", expanded=True) as status_box:
            st.write(f"🔐 Conectando ao sistema CBMMS com usuário: {cpf_formatado} ...")
            sessao = requests.Session()
            sessao.headers.update({"Content-Type": "application/json"})
            payload_login = {"login": cpf_formatado, "senha": senha}
            
            try:
                resposta_login = sessao.post(LOGIN_URL, json=payload_login)
                
                if resposta_login.status_code == 200:
                    dados_login = resposta_login.json()
                    token = None
                    if isinstance(dados_login, dict): token = dados_login.get("token")
                    elif isinstance(dados_login, list) and len(dados_login) > 0 and isinstance(dados_login[0], dict): token = dados_login[0].get("token")
                    if not token: token = resposta_login.headers.get("token")
                    if token: sessao.headers.update({"token": token})
                    
                    st.write("✅ Autenticação realizada com sucesso!")
                    
                    # Atualiza a interface avisando que pode demorar
                    status_box.update(label="Aguardando resposta do servidor do CBMMS...", state="running")
                    st.write("📡 Consultando o banco de dados de Boletins Gerais...")
                    aviso_demora = st.info("⏳ Esta etapa pode demorar um pouco dependendo do intervalo de datas e do volume de publicações. Por favor, aguarde...")
                        
                    params_busca = {
                        "de": data_inicial_iso,
                        "ate": data_final_iso,
                        "tipo": "buscaExata",
                        "conteudo": nome_busca
                    }
                    
                    resposta_busca = sessao.get(BUSCA_BG_URL, params=params_busca)
                    
                    # Remove o aviso de demora assim que o servidor responde
                    aviso_demora.empty()
                    
                    if resposta_busca.status_code == 200:
                        publicacoes_brutas = resposta_busca.json()
                        lista_pubs = publicacoes_brutas if isinstance(publicacoes_brutas, list) else publicacoes_brutas.get("content", publicacoes_brutas.get("data", []))
                        
                        if not lista_pubs:
                            st.write("⚠️ Nenhum boletim encontrado com o seu nome neste período.")
                            status_box.update(label="Busca concluída sem resultados.", state="complete", expanded=True)
                            st.session_state.mensagem_status = f"Nenhum boletim encontrado contendo o nome '{nome_busca}' neste período."
                            st.session_state.busca_concluida = True
                        else:
                            status_box.update(label="Baixando e extraindo PDFs...", state="running")
                            st.write(f"📥 O servidor respondeu! A API retornou {len(lista_pubs)} boletim(ns). Preparando para extração...")
                            
                            barra_progresso = st.progress(0)
                            texto_progresso = st.empty()
                            bgs_com_resultados = []
                            
                            for i, pub in enumerate(lista_pubs):
                                if not isinstance(pub, dict): continue
                                
                                upload_id = pub.get("upload", {}).get("id") if isinstance(pub.get("upload"), dict) else pub.get("uploadId")
                                num_bg = pub.get("numero", "S/N")
                                
                                texto_progresso.text(f"🔍 Lendo e processando BG Nº {num_bg} ({i+1}/{len(lista_pubs)})...")
                                
                                if upload_id:
                                    url_pdf = f"{DOWNLOAD_BG_URL}{upload_id}"
                                    resposta_pdf = sessao.get(url_pdf)
                                    
                                    if resposta_pdf.status_code == 200:
                                        arquivo_pdf = io.BytesIO(resposta_pdf.content)
                                        leitor = PdfReader(arquivo_pdf)
                                        texto_completo = "".join(pagina.extract_text() + "\n" for pagina in leitor.pages)
                                            
                                        resultados = extrair_alteracoes_exatas(texto_completo, nome_busca)
                                        
                                        if resultados:
                                            bgs_com_resultados.append({
                                                "numero_bg": num_bg,
                                                "resultados": resultados
                                            })
                                
                                barra_progresso.progress((i + 1) / len(lista_pubs))
                                
                            texto_progresso.text("✅ Processamento de todos os PDFs finalizado!")
                            status_box.update(label="Busca finalizada com sucesso!", state="complete", expanded=True)
                            
                            st.session_state.bgs_encontrados = bgs_com_resultados
                            st.session_state.busca_concluida = True
                            if not bgs_com_resultados:
                                st.session_state.mensagem_status = "O sistema encontrou o Boletim, mas o extrator não conseguiu isolar o parágrafo."
                                
                    else:
                        status_box.update(label="Erro na busca.", state="error", expanded=True)
                        st.error("Erro ao buscar a lista de boletins. Verifique se o sistema está online.")
                else:
                    status_box.update(label="Erro de autenticação.", state="error", expanded=True)
                    st.error("Falha no login. Verifique suas credenciais.")
                    
            except Exception as e:
                status_box.update(label="Erro interno.", state="error", expanded=True)
                st.error(f"Erro de comunicação com o servidor: {str(e)}")

# ==========================================
# 5. EXIBIÇÃO PERSISTENTE
# ==========================================
if st.session_state.busca_concluida:
    st.divider() 
    
    bgs_resultados = st.session_state.bgs_encontrados
    nome_pesquisado = st.session_state.nome_pesquisado
    
    if bgs_resultados:
        st.success(f"Encontramos suas publicações em {len(bgs_resultados)} boletim(ns)!")
        
        texto_relatorio = gerar_relatorio_txt(bgs_resultados, nome_pesquisado)
        
        st.download_button(
            label="📥 Baixar Relatório Completo (.txt)",
            data=texto_relatorio,
            file_name=f"Relatorio_BG_{nome_pesquisado.replace(' ', '_')}.txt",
            mime="text/plain"
        )
        
        for bg_encontrado in bgs_resultados:
            st.subheader(f"📄 BG Nº {bg_encontrado['numero_bg']}")
            for res in bg_encontrado['resultados']:
                with st.expander(f"📌 NOTA N. {res['nota']}", expanded=True):
                    st.markdown(f"**Contexto:** {res['cabecalho']}")
                    for item in res['itens']:
                        st.error(item)
    else:
        if st.session_state.mensagem_status:
            st.warning(st.session_state.mensagem_status)
