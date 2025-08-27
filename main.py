# -------------------------------------------
# Dashboard Longview – Streamlit puro
# -------------------------------------------
import requests
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

# ==================================================
# CONFIGURAÇÕES GERAIS
# ==================================================
st.set_page_config(page_title="Dashboard Longview", layout="wide")


from utils import BASE_URL_API, CLIENT_SECRET,CLIENT_ID
from aloc import tela_alocacao

# ==================================================
# ESTADO E HELPERS
# ==================================================
if "pagina_atual" not in st.session_state:
    st.session_state.pagina_atual = "login"
if "token" not in st.session_state:
    st.session_state.token = None
if "headers" not in st.session_state:
    st.session_state.headers = {}
if "token_expira_em" not in st.session_state:
    st.session_state.token_expira_em = None

def ir_para(pagina: str):
    st.session_state.pagina_atual = pagina

def limpar_sessao():
    st.session_state.token = None
    st.session_state.headers = {}
    st.session_state.token_expira_em = None
    ir_para("login")

def token_valido() -> bool:
    exp = st.session_state.get("token_expira_em")
    if not st.session_state.token or not exp:
        return False
    # margem de segurança de 30s
    return datetime.utcnow() < (exp - timedelta(seconds=30))

# ==================================================
# AUTENTICAÇÃO
# ==================================================
def autenticar(email: str, senha: str):
    """
    Autentica no backend e retorna token + headers.
    Ajuste a rota se necessário: /auth/token
    """
    url = f"{BASE_URL_API.rstrip('/')}/auth/token"

    client_headers = {}

    client_headers = {
            "CF-Access-Client-Id": CLIENT_ID,
            "CF-Access-Client-Secret": CLIENT_SECRET,
        }

    data = {"username": email.strip(), "password": senha}
    resp = requests.post(url, data=data, headers=client_headers, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    token = payload.get("access_token")
    expires_in = int(payload.get("expires_in", 3600))  # fallback 1h
    if not token:
        raise ValueError("Resposta sem access_token.")

    headers = {**client_headers, "Authorization": f"Bearer {token}"}
    expira_em = datetime.utcnow() + timedelta(seconds=expires_in)
    return token, headers, expira_em

# ==================================================
# TELAS
# ==================================================
def tela_login():
    # Centraliza a coluna com os inputs (sem CSS)
    left, center, right = st.columns([1, 2, 1])
    with center:
        st.title("📈 Dashboards Longview")
        st.subheader("Login")

        email = st.text_input("E-mail")
        senha = st.text_input("Senha de aplicação", type="password")

        if st.button("Entrar", use_container_width=True):
            if not email or not senha:
                st.error("Informe e‑mail e senha.")
                return
            try:
                with st.spinner("Autenticando..."):
                    token, headers, expira_em = autenticar(email, senha)
                st.session_state.token = token
                st.session_state.headers = headers
                st.session_state.token_expira_em = expira_em
                st.success("Login bem-sucedido!")
                ir_para("menu")
                st.rerun()
            except requests.HTTPError as http_err:
                status = getattr(http_err.response, "status_code", None)
                try:
                    msg_api = http_err.response.json().get("detail") or http_err.response.json().get("message")
                except Exception:
                    msg_api = None
                if status == 401:
                    st.error("Credenciais inválidas. Verifique e‑mail e senha.")
                elif status == 403:
                    st.error("Acesso negado. Verifique as credenciais Cloudflare e permissões.")
                else:
                    st.error(f"Erro HTTP {status or ''}".strip() + (f": {msg_api}" if msg_api else ""))
            except requests.Timeout:
                st.error("Tempo de conexão esgotado. Tente novamente.")
            except requests.ConnectionError:
                st.error("Falha de conexão. Verifique sua rede ou a disponibilidade da API.")
            except Exception as e:
                st.error(f"Erro ao autenticar: {e}")

def tela_menu():
    st.sidebar.success("Sessão autenticada")
    st.sidebar.button("Sair", on_click=limpar_sessao, use_container_width=True)

    op = st.sidebar.radio(
        "Telas",
        ["Tela 1 – Alocação e Métricas", "Tela 2 – Simulação", "Tela 3 – Performance"],
        index=0,
    )

    if op.startswith("Tela 1"):
        tela_alocacao()
    elif op.startswith("Tela 2"):
        tela_simulacao()
    elif op.startswith("Tela 3"):
        tela_performance()

# ---------------------------
# Tela 1: Alocação
# ---------------------------


# ---------------------------
# Tela 2: Simulação
# ---------------------------
def tela_simulacao():
    st.header("🧮 Simulação de Cenários")
    st.write("Simule compra/venda e veja impacto nas métricas.")

    # Placeholders
    ativo = st.text_input("Ativo (ticker ou nome)")
    ajuste = st.number_input("Ajuste (positivo=compra, negativo=venda)", value=0.0, step=1000.0)

    if st.button("Aplicar Simulação"):
        # Aqui você duplicaria o DF base, aplicaria o ajuste e recalcularia métricas
        st.success(f"Simulação aplicada para {ativo} no valor {ajuste:,.2f}")

# ---------------------------
# Tela 3: Performance
# ---------------------------
def tela_performance():
    st.header("📈 Performance da Carteira")
    st.write("Retornos vs Benchmarks, atribuição e volatilidade.")

    # Placeholder até plugar o endpoint real
    st.line_chart(pd.DataFrame({
        "Carteira": [1.00, 1.02, 1.05, 1.03, 1.08],
        "CDI":      [1.00, 1.01, 1.015, 1.02, 1.025],
    }))

# ==================================================
# APP (roteamento)
# ==================================================
if st.session_state.pagina_atual == "login" and not token_valido():
    tela_login()
elif st.session_state.pagina_atual == "menu":
    tela_menu()
