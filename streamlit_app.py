import streamlit as st
import requests
import re
import io
import datetime
import unicodedata
from pypdf import PdfReader

# ==========================================
# 0. CHAVE MESTRA (MODO DE TESTE)
# ==========================================
ATIVAR_MODO_TESTE = True

# ==========================================
# 1. CONFIGURAÇÕES DA API E SESSÃO
# ==========================================
BASE_URL = "https://sistemas.bombeiros.ms.gov.br"
LOGIN_URL = f"{BASE_URL}/ws-auth/fazer-login"
BUSCA_BG_URL = f"{BASE_URL}/ws-boletim-geral/publicacao"
DOWNLOAD_BG_URL = f"{BASE_URL}/ws-alfresco/arquivo/"

# timeout apenas para conectar; leitura fica sem limite
REQUEST_TIMEOUT = (15, None)

# ==========================================
# 2. ESTADO INICIAL
# ==========================================
def inicializar_estado():
    defaults = {
        "busca_concluida": False,
        "bgs_encontrados": [],
        "nome_pesquisado": "",
        "mensagem_status": "",
        "modo_teste_ativo": False,
        "falhas_processamento": [],
    }
    for chave, valor in defaults.items():
        if chave not in st.session_state:
            st.session_state[chave] = valor


inicializar_estado()

# ==========================================
# 3. HELPERS / REGEX
# ==========================================
RE_NOTA = re.compile(r"^\s*NOTA\s+N\.\s*(\d+)\s*$", re.IGNORECASE)
RE_FOOTER = re.compile(
    r"BOLETIM GERAL N\.\s+\d+.*?P[ÁA]GINA\s+\d+\s*/\s*\d+",
    re.IGNORECASE
)
RE_ATTACHMENT = re.compile(r".+\.(pdf|docx?|xlsx?|jpg|jpeg|png)$", re.IGNORECASE)

RE_MILITAR_LISTA = re.compile(
    r"^(?:\dº\s*)?(?:CEL|TEN\s*CEL|TC|MAJ|CAP|ASP|1º TEN|2º TEN|ST|1º SGT|2º SGT|3º SGT|CB|SD)\s+BM\b",
    re.IGNORECASE,
)

RE_CABECALHOS_FIXOS = [
    re.compile(r"^ESTADO DE MATO GROSSO DO SUL$", re.IGNORECASE),
    re.compile(r"^SECRETARIA DE ESTADO", re.IGNORECASE),
    re.compile(r"^CORPO DE BOMBEIROS MILITAR", re.IGNORECASE),
    re.compile(r"^BOLETIM GERAL$", re.IGNORECASE),
    re.compile(r"^ANO\s+\d{4}\s+N\.\s+\d+", re.IGNORECASE),
    re.compile(r"^COMANDANTE-GERAL:", re.IGNORECASE),
    re.compile(r"^CHEFE DO ESTADO MAIOR GERAL:", re.IGNORECASE),
]

RE_LINHAS_LIXO = [
    re.compile(r"^P[ÁA]GINA\s+\d+\s*/\s*\d+$", re.IGNORECASE),
    re.compile(r"^\d{1,2}\s+DE\s+\w+\s+DE\s+\d{4}$", re.IGNORECASE),
    re.compile(r"^BOLETIM GERAL N\.\s+\d+$", re.IGNORECASE),
]

# ==========================================
# 4. FUNÇÕES DE FORMATAÇÃO / NORMALIZAÇÃO
# ==========================================
def formatar_cpf(cpf_bruto: str) -> str:
    cpf_limpo = re.sub(r"\D", "", cpf_bruto or "")
    cpf_limpo = cpf_limpo.zfill(11)
    if len(cpf_limpo) == 11:
        return f"{cpf_limpo[:3]}.{cpf_limpo[3:6]}.{cpf_limpo[6:9]}-{cpf_limpo[9:]}"
    return cpf_bruto


def normalizar_unicode(texto: str) -> str:
    if not texto:
        return ""

    substituicoes = {
        "\u00A0": " ",
        "\u00AD": "",
        "\u200B": "",
        "\ufeff": "",
        "–": "-",
        "—": "-",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
    }

    for antigo, novo in substituicoes.items():
        texto = texto.replace(antigo, novo)

    mapa_letras = str.maketrans({
        "Ν": "N", "О": "O", "Ο": "O", "Τ": "T", "Α": "A",
        "М": "M", "С": "C", "Р": "P", "І": "I",
    })
    texto = texto.translate(mapa_letras)

    texto = re.sub(r"[ΝN][ΟO][ΤT][ΑA]\s+[NΝ]\.", "NOTA N.", texto, flags=re.IGNORECASE)
    return texto


def normalizar_para_match(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = normalizar_unicode(texto)
    texto = texto.lower()
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def regex_nome_flexivel(nome: str) -> str:
    partes = [re.escape(p) for p in re.split(r"\s+", nome.strip()) if p]
    return r"\b" + r"\s+".join(partes) + r"\b"


def nome_aparece_no_bloco(texto_bloco: str, nome_militar: str) -> bool:
    if not texto_bloco or not nome_militar:
        return False

    texto_match = normalizar_para_match(texto_bloco)
    nome_match = normalizar_para_match(nome_militar)

    if nome_match in texto_match:
        return True

    try:
        if re.search(regex_nome_flexivel(nome_match), texto_match, flags=re.IGNORECASE):
            return True
    except re.error:
        pass

    tokens_nome = [t for t in nome_match.split() if len(t) >= 3]
    if not tokens_nome:
        return False

    hits = sum(1 for t in tokens_nome if t in texto_match)
    return (hits / len(tokens_nome)) >= 0.75


def limpar_texto_para_exibicao(texto: str) -> str:
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()

# ==========================================
# 5. EXTRAÇÃO DO PDF
# ==========================================
def linha_eh_titulo(linha: str) -> bool:
    linha = (linha or "").strip()
    if not linha:
        return False
    if RE_NOTA.match(linha):
        return False
    if RE_ATTACHMENT.match(linha):
        return False
    if RE_MILITAR_LISTA.match(linha):
        return False
    if re.search(r"^ADM\.$|Aprovado por:|Militares Relacionados com a Nota|Responsável pelo ato", linha, re.IGNORECASE):
        return False
    if linha.endswith((".", ";", ":")):
        return False
    if len(linha) > 100:
        return False

    letras = [c for c in linha if c.isalpha()]
    if not letras:
        return False

    maiusculas = sum(1 for c in letras if c.isupper())
    proporcao = maiusculas / len(letras)

    return proporcao >= 0.65 or (proporcao >= 0.45 and len(linha.split()) <= 7)


def limpar_linhas_pagina(texto_pagina: str) -> list[str]:
    texto_pagina = (texto_pagina or "").replace("\r", "\n")
    linhas_originais = texto_pagina.splitlines()
    linhas = []

    for linha in linhas_originais:
        linha = normalizar_unicode(linha)
        linha = re.sub(r"[ \t]+", " ", linha).strip()

        if not linha:
            continue

        if RE_FOOTER.search(linha):
            continue

        if any(p.search(linha) for p in RE_CABECALHOS_FIXOS):
            continue

        if any(p.search(linha) for p in RE_LINHAS_LIXO):
            continue

        linhas.append(linha)

    return linhas


def extrair_linhas_do_pdf(pdf_bytes: bytes) -> list[str]:
    leitor = PdfReader(io.BytesIO(pdf_bytes))
    linhas = []

    for pagina in leitor.pages:
        texto = pagina.extract_text() or ""
        linhas.extend(limpar_linhas_pagina(texto))

    return linhas


def extrair_contexto_pre_nota(linhas: list[str], idx_nota: int) -> tuple[str, str, int]:
    j = idx_nota - 1
    assunto = []

    while j >= 0 and linha_eh_titulo(linhas[j]):
        assunto.insert(0, linhas[j])
        j -= 1
        if len(assunto) >= 3:
            break

    setor = []
    k = j
    while k >= 0 and len(setor) < 2:
        linha = linhas[k].strip()
        if not linha:
            k -= 1
            continue

        if RE_NOTA.match(linha):
            break
        if RE_ATTACHMENT.match(linha):
            break
        if RE_MILITAR_LISTA.match(linha):
            break
        if re.search(r"^ADM\.$|Aprovado por:|Militares Relacionados com a Nota|Responsável pelo ato", linha, re.IGNORECASE):
            break
        if len(linha) > 100 or linha.endswith((".", ";", ":")):
            break

        setor.insert(0, linha)
        k -= 1

    assunto_str = " ".join(assunto).strip()
    setor_str = " ".join(setor).strip()
    idx_inicio_bloco = idx_nota - len(assunto) if assunto else idx_nota

    return setor_str, assunto_str, idx_inicio_bloco


def aparar_bloco_ate_ultimo_militar(linhas_bloco: list[str]) -> list[str]:
    marcador = None
    for i, linha in enumerate(linhas_bloco):
        if re.search(r"^Militares Relacionados com a Nota$", linha, re.IGNORECASE):
            marcador = i
            break

    if marcador is None:
        return linhas_bloco

    ultimo_indice_util = marcador

    for i in range(marcador + 1, len(linhas_bloco)):
        linha = linhas_bloco[i].strip()

        if not linha:
            continue

        if RE_ATTACHMENT.match(linha):
            break

        if RE_NOTA.match(linha):
            break

        if re.search(r"^ADM\.$", linha, re.IGNORECASE):
            ultimo_indice_util = i
            continue

        if RE_MILITAR_LISTA.match(linha):
            ultimo_indice_util = i
            continue

        if linha_eh_titulo(linha):
            break

        if i > marcador + 1:
            break

    return linhas_bloco[:ultimo_indice_util + 1]


def montar_blocos_de_notas(linhas_pdf: list[str]) -> list[dict]:
    notas = []
    indices_notas = [i for i, linha in enumerate(linhas_pdf) if RE_NOTA.match(linha)]

    if not indices_notas:
        return notas

    metadados = []
    for idx_nota in indices_notas:
        setor, assunto, idx_inicio = extrair_contexto_pre_nota(linhas_pdf, idx_nota)
        match_num = RE_NOTA.match(linhas_pdf[idx_nota])
        numero = match_num.group(1) if match_num else "Desconhecida"

        metadados.append({
            "idx_nota": idx_nota,
            "idx_inicio": idx_inicio,
            "numero": numero,
            "setor": setor,
            "cabecalho": assunto or "Sem assunto identificado",
        })

    for pos, meta in enumerate(metadados):
        inicio = meta["idx_inicio"]
        fim = metadados[pos + 1]["idx_inicio"] if pos + 1 < len(metadados) else len(linhas_pdf)

        linhas_bloco = linhas_pdf[inicio:fim]
        linhas_bloco = aparar_bloco_ate_ultimo_militar(linhas_bloco)

        texto_completo = "\n".join(linhas_bloco).strip()

        notas.append({
            "nota": meta["numero"],
            "setor": meta["setor"],
            "cabecalho": meta["cabecalho"],
            "texto_completo": limpar_texto_para_exibicao(texto_completo),
        })

    return notas


def extrair_notas_do_militar(pdf_bytes: bytes, nome_militar: str) -> list[dict]:
    linhas_pdf = extrair_linhas_do_pdf(pdf_bytes)
    blocos = montar_blocos_de_notas(linhas_pdf)

    resultados = []
    for bloco in blocos:
        if nome_aparece_no_bloco(bloco["texto_completo"], nome_militar):
            resultados.append(bloco)

    return resultados

# ==========================================
# 6. RELATÓRIO TXT
# ==========================================
def gerar_relatorio_txt(bgs_com_resultados: list[dict], nome_busca: str) -> str:
    linhas = []
    linhas.append("=" * 70)
    linhas.append("RELATÓRIO DE ALTERAÇÕES FUNCIONAIS - CBMMS")
    linhas.append("=" * 70)
    linhas.append(f"Militar pesquisado: {nome_busca}")
    linhas.append(f"Data da emissão: {datetime.datetime.now().strftime('%d/%m/%Y às %H:%M:%S')}")
    linhas.append("=" * 70)
    linhas.append("")

    for bg in bgs_com_resultados:
        linhas.append(f"BOLETIM GERAL N. {bg['numero_bg']}")
        linhas.append("-" * 70)

        for res in bg["resultados"]:
            if res.get("setor"):
                linhas.append(res["setor"])
            if res.get("cabecalho"):
                linhas.append(res["cabecalho"])
            linhas.append(f"NOTA N. {res['nota']}")
            linhas.append("")
            linhas.append(res["texto_completo"])
            linhas.append("")
            linhas.append("-" * 70)
            linhas.append("")

    linhas.append("=" * 70)
    linhas.append("Documento gerado automaticamente pelo Buscador Inteligente do BG.")
    return "\n".join(linhas)

# ==========================================
# 7. COMUNICAÇÃO COM A API
# ==========================================
def autenticar(sessao: requests.Session, usuario: str, senha: str) -> None:
    """
    Mantém a mesma lógica do script anterior:
    - se status_code == 200, considera login OK
    - usa token se vier
    - não falha se o token não vier
    """
    payload_login = {"login": usuario, "senha": senha}
    resposta = sessao.post(LOGIN_URL, json=payload_login, timeout=REQUEST_TIMEOUT)

    if resposta.status_code != 200:
        raise ValueError("Falha no login. Verifique as suas credenciais.")

    token = None

    try:
        dados_login = resposta.json()
        if isinstance(dados_login, dict):
            token = dados_login.get("token")
        elif isinstance(dados_login, list) and len(dados_login) > 0 and isinstance(dados_login[0], dict):
            token = dados_login[0].get("token")
    except Exception:
        token = None

    if not token:
        token = resposta.headers.get("token")

    if token:
        sessao.headers.update({"token": token})


def buscar_publicacoes(sessao: requests.Session, nome_busca: str, data_inicial, data_final) -> list[dict]:
    params_busca = {
        "de": data_inicial.strftime("%Y-%m-%dT03:00:00.000Z"),
        "ate": data_final.strftime("%Y-%m-%dT03:00:00.000Z"),
        "tipo": "buscaExata",
        "conteudo": nome_busca,
    }

    resposta = sessao.get(BUSCA_BG_URL, params=params_busca, timeout=REQUEST_TIMEOUT)

    if resposta.status_code != 200:
        raise ValueError("Erro ao pesquisar a lista de boletins. Verifique se o sistema está online.")

    publicacoes_brutas = resposta.json()
    if isinstance(publicacoes_brutas, list):
        return publicacoes_brutas

    return publicacoes_brutas.get("content", publicacoes_brutas.get("data", []))


def baixar_pdf(sessao: requests.Session, upload_id: str) -> bytes:
    resposta = sessao.get(f"{DOWNLOAD_BG_URL}{upload_id}", timeout=REQUEST_TIMEOUT)

    if resposta.status_code != 200:
        raise ValueError(f"Não foi possível baixar o PDF do upload {upload_id}.")

    return resposta.content

# ==========================================
# 8. INTERFACE
# ==========================================
st.set_page_config(page_title="Buscador de BG - CBMMS", page_icon="🚒")
st.title("🚒 Buscador Inteligente do BG - CBMMS")

st.info("""
**🛡️ Segurança e Privacidade (Open Source)**  
As suas credenciais comunicam diretamente com os servidores do CBMMS.  
Nenhuma senha é salva ou registrada nesta aplicação.
""")

if ATIVAR_MODO_TESTE:
    with st.expander("🔑 Possui um Código de Convite?"):
        st.write("Insira o código fornecido para testar a ferramenta sem utilizar o seu login pessoal.")
        codigo_convite = st.text_input("Código de Convite", type="password")
        colA, colB = st.columns([1, 4])

        with colA:
            if st.button("Validar Código"):
                try:
                    if codigo_convite == st.secrets["senhaAPP"]:
                        st.session_state.modo_teste_ativo = True
                        st.success("Acesso de teste ativado com sucesso!")
                    else:
                        st.error("Código inválido.")
                except Exception:
                    st.error("Erro ao validar o código de convite nos Secrets.")

        with colB:
            if st.session_state.modo_teste_ativo:
                if st.button("Sair do Modo de Teste"):
                    st.session_state.modo_teste_ativo = False
                    st.rerun()

with st.form("login_form"):
    st.subheader("1. Credenciais de Acesso")
    bloquear_campos = st.session_state.modo_teste_ativo

    if bloquear_campos:
        st.success("✅ Utilizando credenciais de teste seguras. Não é necessário preencher o CPF.")
    else:
        st.caption("Você pode inserir o seu CPF com ou sem pontuação.")

    usuario = st.text_input("Login (CPF)", disabled=bloquear_campos)
    senha = st.text_input("Senha", type="password", disabled=bloquear_campos)

    st.subheader("2. Parâmetros da Pesquisa")
    nome_busca = st.text_input("Nome completo exato a procurar", placeholder="Ex: João da Silva")

    col1, col2 = st.columns(2)

    data_limite_inferior = datetime.date(2018, 7, 17)
    texto_ajuda_data = (
        "Busca por conteúdo nas publicações, exceto nos suplementos, somente a partir de 17/07/2018. "
        "Boletins anteriores continuam disponíveis no sistema legado."
    )

    with col1:
        data_inicial = st.date_input(
            "Data Inicial",
            value=data_limite_inferior,
            min_value=data_limite_inferior,
            format="DD/MM/YYYY",
            help=texto_ajuda_data,
        )

    with col2:
        data_final = st.date_input(
            "Data Final",
            value=datetime.date.today(),
            min_value=data_limite_inferior,
            format="DD/MM/YYYY",
        )

    btn_buscar = st.form_submit_button("Entrar e Buscar")

# ==========================================
# 9. LÓGICA PRINCIPAL
# ==========================================
if btn_buscar:
    if st.session_state.modo_teste_ativo:
        usuario_final = st.secrets["userteste"]
        senha_final = st.secrets["senhateste"]
    else:
        usuario_final = formatar_cpf(usuario)
        senha_final = senha

    if not usuario_final or not senha_final or not nome_busca:
        st.warning("Preencha todos os campos obrigatórios.")
    elif data_final < data_inicial:
        st.warning("A data final não pode ser menor que a data inicial.")
    else:
        st.session_state.busca_concluida = False
        st.session_state.bgs_encontrados = []
        st.session_state.nome_pesquisado = nome_busca
        st.session_state.mensagem_status = ""
        st.session_state.falhas_processamento = []

        with st.status("Iniciando processo...", expanded=True) as status_box:
            login_mostrado = "CONTA_DE_TESTE" if st.session_state.modo_teste_ativo else usuario_final
            st.write(f"🔐 Conectando ao sistema CBMMS com o usuário: {login_mostrado}")

            sessao = requests.Session()
            sessao.headers.update({"Content-Type": "application/json"})

            try:
                autenticar(sessao, usuario_final, senha_final)
                st.write("✅ Autenticação realizada com sucesso!")

                status_box.update(label="Consultando boletins...", state="running", expanded=True)
                aviso_demora = st.info("⏳ Consultando os boletins no período informado...")
                lista_pubs = buscar_publicacoes(sessao, nome_busca, data_inicial, data_final)
                aviso_demora.empty()

                if not lista_pubs:
                    st.session_state.busca_concluida = True
                    st.session_state.mensagem_status = (
                        f"Nenhum boletim encontrado contendo o nome '{nome_busca}' neste período."
                    )
                    status_box.update(label="Pesquisa concluída sem resultados.", state="complete", expanded=True)
                else:
                    st.write(f"📥 A API retornou {len(lista_pubs)} boletim(ns).")
                    status_box.update(label="Baixando e extraindo PDFs...", state="running", expanded=True)

                    barra_progresso = st.progress(0)
                    texto_progresso = st.empty()
                    bgs_com_resultados = []

                    for i, pub in enumerate(lista_pubs, start=1):
                        if not isinstance(pub, dict):
                            barra_progresso.progress(i / len(lista_pubs))
                            continue

                        upload_id = pub.get("upload", {}).get("id") if isinstance(pub.get("upload"), dict) else pub.get("uploadId")
                        num_bg = pub.get("numeroDaPublicacao", "S/N")

                        texto_progresso.text(f"🔍 Processando BG N. {num_bg} ({i}/{len(lista_pubs)})...")

                        if not upload_id:
                            st.session_state.falhas_processamento.append(
                                f"BG {num_bg}: publicação sem upload_id."
                            )
                            barra_progresso.progress(i / len(lista_pubs))
                            continue

                        try:
                            pdf_bytes = baixar_pdf(sessao, upload_id)
                            resultados = extrair_notas_do_militar(pdf_bytes, nome_busca)

                            if resultados:
                                bgs_com_resultados.append({
                                    "numero_bg": num_bg,
                                    "resultados": resultados,
                                })

                        except Exception as erro_pdf:
                            st.session_state.falhas_processamento.append(
                                f"BG {num_bg}: {str(erro_pdf)}"
                            )

                        barra_progresso.progress(i / len(lista_pubs))

                    texto_progresso.text("✅ Processamento de todos os PDFs finalizado!")
                    status_box.update(label="Pesquisa finalizada com sucesso!", state="complete", expanded=True)

                    st.session_state.bgs_encontrados = bgs_com_resultados
                    st.session_state.busca_concluida = True

                    if not bgs_com_resultados:
                        st.session_state.mensagem_status = (
                            "O sistema encontrou boletins, mas o extrator não conseguiu isolar a nota completa."
                        )

            except ValueError as erro_negocio:
                status_box.update(label="Erro no processo.", state="error", expanded=True)
                st.error(str(erro_negocio))

            except requests.ConnectTimeout:
                status_box.update(label="Tempo de conexão excedido.", state="error", expanded=True)
                st.error("O servidor demorou demais para aceitar a conexão.")

            except requests.ConnectionError as erro_conexao:
                status_box.update(label="Erro de conexão.", state="error", expanded=True)
                st.error(f"Erro de conexão com o servidor: {erro_conexao}")

            except requests.RequestException as erro_http:
                status_box.update(label="Erro de comunicação.", state="error", expanded=True)
                st.error(f"Erro de comunicação com o servidor: {erro_http}")

            except Exception as erro_geral:
                status_box.update(label="Erro interno.", state="error", expanded=True)
                st.error(f"Erro interno: {erro_geral}")

# ==========================================
# 10. EXIBIÇÃO DOS RESULTADOS
# ==========================================
if st.session_state.busca_concluida:
    st.divider()

    bgs_resultados = st.session_state.bgs_encontrados
    nome_pesquisado = st.session_state.nome_pesquisado

    if bgs_resultados:
        total_notas = sum(len(bg["resultados"]) for bg in bgs_resultados)
        st.success(f"Encontramos {total_notas} nota(s) em {len(bgs_resultados)} boletim(ns)!")

        texto_relatorio = gerar_relatorio_txt(bgs_resultados, nome_pesquisado)

        st.download_button(
            label="📥 Baixar Relatório Completo (.txt)",
            data=texto_relatorio,
            file_name=f"Relatorio_BG_{nome_pesquisado.replace(' ', '_')}.txt",
            mime="text/plain",
        )

        st.markdown("---")

        for bg_encontrado in bgs_resultados:
            st.subheader(f"📄 BG N. {bg_encontrado['numero_bg']}")

            for res in bg_encontrado["resultados"]:
                titulo_expander = f"📌 NOTA N. {res['nota']}"
                if res.get("cabecalho"):
                    titulo_expander += f" - {res['cabecalho']}"

                with st.expander(titulo_expander, expanded=True):
                    if res.get("setor"):
                        st.markdown(f"**Setor:** {res['setor']}")
                    if res.get("cabecalho"):
                        st.markdown(f"**Assunto:** {res['cabecalho']}")

                    st.code(res["texto_completo"], language=None)

        if st.session_state.falhas_processamento:
            with st.expander("⚠️ Ocorrências durante o processamento"):
                for falha in st.session_state.falhas_processamento:
                    st.warning(falha)

    else:
        if st.session_state.mensagem_status:
            st.warning(st.session_state.mensagem_status)
