# perform.py
from __future__ import annotations

import io
import math
from datetime import date, datetime, timedelta
from typing import Dict, List, Any

import requests
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

from utils import BASE_URL_API, CARTEIRAS

# yfinance só é usado se você habilitar benchmarks externos específicos
try:
    import yfinance as yf
    _HAS_YF = True
except Exception:
    _HAS_YF = False


# --------------------------
# Estilo (herdado do simul.py)
# --------------------------
CSS = """
<style>
.block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
section[data-testid="stSidebar"] .block-container { padding-top: 1rem !important; }
.card {
  border: 1px solid rgba(0,0,0,0.08);
  border-radius: 12px;
  padding: 14px 14px;
  background: white;
}
.card-muted {
  border: 1px dashed rgba(0,0,0,0.10);
  border-radius: 12px;
  padding: 12px 12px;
  background: rgba(0,0,0,0.02);
}
.h-label {
  font-weight: 600; font-size: 0.95rem; margin: 0 0 6px 0;
}
.help {
  color: #6b7280; font-size: 0.85rem; margin-top: -4px; margin-bottom: 8px;
}
.hr { height: 1px; background: rgba(0,0,0,0.06); margin: 10px 0 14px 0; }
.indicator-card {
  border: 1px solid rgba(0,0,0,0.08);
  border-radius: 12px;
  padding: 16px 14px;
  background: white;
  text-align: center;
}
.comparison-positive { color: #00a86b; font-weight: 600; }
.comparison-negative { color: #ff4b4b; font-weight: 600; }
.comparison-neutral { color: #6b7280; }
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 8px;
  margin: 10px 0;
}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# --------------------------
# Utilitários
# --------------------------
def _to_date(x) -> date:
    return pd.to_datetime(x).date()

def _periods(today: pd.Timestamp):
    today = pd.Timestamp(today).normalize()
    start_month = today.replace(day=1)
    start_year  = today.replace(month=1, day=1)
    d12 = today - pd.DateOffset(months=12)
    d24 = today - pd.DateOffset(months=24)
    d36 = today - pd.DateOffset(months=36)
    d60 = today - pd.DateOffset(months=60)
    return {
        "D":   (today, today),
        "MTD": (start_month, today),
        "YTD": (start_year, today),
        "12m": (d12, today),
        "24m": (d24, today),
        "36m": (d36, today),
        "60m": (d60, today),
    }

def _comp(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty: return np.nan
    return (1.0 + s).prod() - 1.0

def _fmt_pct(x) -> str:
    return "" if pd.isna(x) else f"{x*100:.2f}%"

def _fmt_pct_color(x) -> str:
    """Formata porcentagem com cores, convertendo para float primeiro"""
    try:
        # Converte para float se for string
        if isinstance(x, str):
            # Remove % e converte vírgula para ponto
            x_clean = x.replace('%', '').replace(',', '.').strip()
            x_float = float(x_clean) / 100.0
        else:
            x_float = float(x)
    except (ValueError, TypeError):
        x_float = 0.0
    
    if pd.isna(x_float):
        return ""
    
    color_class = "comparison-positive" if x_float > 0 else "comparison-negative" if x_float < 0 else "comparison-neutral"
    return f'<span class="{color_class}">{x_float*100:.2f}%</span>'

def _fmt_diff_color(x) -> str:
    """Formata diferença com cores, convertendo para float primeiro"""
    try:
        # Converte para float se for string
        if isinstance(x, str):
            # Remove pp e converte vírgula para ponto
            x_clean = x.replace('pp', '').replace(',', '.').strip()
            x_float = float(x_clean) / 100.0
        else:
            x_float = float(x)
    except (ValueError, TypeError):
        x_float = 0.0
    
    if pd.isna(x_float):
        return ""
    
    color_class = "comparison-positive" if x_float > 0 else "comparison-negative" if x_float < 0 else "comparison-neutral"
    symbol = "+" if x_float > 0 else ""
    return f'<span class="{color_class}">{symbol}{x_float*100:.2f}pp</span>'

def _parse_percentage_value(value) -> float:
    """Converte valores de porcentagem (string ou número) para float decimal"""
    if pd.isna(value):
        return 0.0
    try:
        if isinstance(value, str):
            # Remove caracteres não numéricos e converte vírgula para ponto
            cleaned = value.replace('%', '').replace(',', '.').strip()
            return float(cleaned) / 100.0
        elif isinstance(value, (int, float)):
            return float(value) / 100.0
        else:
            return 0.0
    except (ValueError, TypeError):
        return 0.0

def _equity_curve(ret: pd.Series) -> pd.Series:
    eq = (1.0 + ret.fillna(0)).cumprod()
    return eq/eq.dropna().iloc[0]

def _drawdown(ret: pd.Series) -> pd.Series:
    eq = _equity_curve(ret)
    peak = eq.cummax()
    return (eq/peak) - 1.0


# --------------------------
# API dos Indicadores Econômicos
# --------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_cdi_data() -> Dict[str, Any]:
    """Busca dados do CDI do Banco Central"""
    try:
        # Taxa CDI atual (SELIC meta)
        url_selic = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/1?formato=json"
        r_selic = requests.get(url_selic, timeout=10)
        r_selic.raise_for_status()
        selic_data = r_selic.json()
        selic_value = float(selic_data[0]['valor']) if selic_data else 0.0
        
        # CDI acumulado no ano
        current_year = datetime.now().year
        url_cdi_acum = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.4391/dados?formato=json&dataInicial=01/01/{current_year}"
        r_cdi_acum = requests.get(url_cdi_acum, timeout=10)
        r_cdi_acum.raise_for_status()
        cdi_acum_data = r_cdi_acum.json()
        
        if cdi_acum_data:
            cdi_acum = float(cdi_acum_data[-1]['valor'])
        else:
            cdi_acum = 0.0
            
        return {
            'selic_meta': selic_value,
            'cdi_acum_ano': cdi_acum,
            'fonte': 'Banco Central do Brasil'
        }
    except Exception as e:
        st.error(f"Erro ao buscar CDI: {e}")
        return {'selic_meta': 0.0, 'cdi_acum_ano': 0.0, 'fonte': 'Erro'}

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_imab_data() -> Dict[str, Any]:
    """Busca dados do IMA-B via ANBIMA ou ETF IMAB11"""
    try:
        # Usando o ETF IMAB11 como proxy para IMA-B
        if _HAS_YF:
            imab = yf.Ticker("IMAB11.SA")
            hist = imab.history(period="1y")
            if not hist.empty:
                price_current = hist['Close'].iloc[-1]
                price_prev = hist['Close'].iloc[-2] if len(hist) > 1 else price_current
                daily_change = ((price_current - price_prev) / price_prev) * 100
                
                # Calcular YTD
                current_year = datetime.now().year
                year_start = f"{current_year}-01-01"
                hist_ytd = imab.history(start=year_start)
                if len(hist_ytd) > 1:
                    ytd_change = ((hist_ytd['Close'].iloc[-1] - hist_ytd['Close'].iloc[0]) / hist_ytd['Close'].iloc[0]) * 100
                else:
                    ytd_change = 0.0
                    
                return {
                    'valor_atual': price_current,
                    'variacao_dia': daily_change,
                    'variacao_ytd': ytd_change,
                    'fonte': 'YFinance (IMAB11)'
                }
    except Exception as e:
        st.error(f"Erro ao buscar IMA-B: {e}")
    
    return {'valor_atual': 0.0, 'variacao_dia': 0.0, 'variacao_ytd': 0.0, 'fonte': 'Erro'}

@st.cache_data(ttl=300, show_spinner=False)
def _fetch_dolar_data() -> Dict[str, Any]:
    """Busca cotação do dólar"""
    try:
        # Via Banco Central
        url_bcb = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.1/dados/ultimos/1?formato=json"
        r_bcb = requests.get(url_bcb, timeout=10)
        r_bcb.raise_for_status()
        bcb_data = r_bcb.json()
        
        if bcb_data:
            dolar_bcb = float(bcb_data[0]['valor'])
            # Para variação, buscamos o valor anterior
            url_bcb_prev = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.1/dados/ultimos/2?formato=json"
            r_bcb_prev = requests.get(url_bcb_prev, timeout=10)
            r_bcb_prev.raise_for_status()
            bcb_prev_data = r_bcb_prev.json()
            
            if len(bcb_prev_data) == 2:
                dolar_prev = float(bcb_prev_data[0]['valor'])
                variation = ((dolar_bcb - dolar_prev) / dolar_prev) * 100
            else:
                variation = 0.0
                
            return {
                'cotacao': dolar_bcb,
                'variacao': variation,
                'fonte': 'Banco Central do Brasil'
            }
    except Exception as e:
        st.error(f"Erro ao buscar dólar BCB: {e}")
        # Fallback para Yahoo Finance
        if _HAS_YF:
            try:
                usdbrl = yf.Ticker("USDBRL=X")
                hist = usdbrl.history(period="2d")
                if len(hist) >= 2:
                    price_current = hist['Close'].iloc[-1]
                    price_prev = hist['Close'].iloc[-2]
                    variation = ((price_current - price_prev) / price_prev) * 100
                    return {
                        'cotacao': price_current,
                        'variacao': variation,
                        'fonte': 'YFinance (Fallback)'
                    }
            except Exception as yf_error:
                st.error(f"Erro ao buscar dólar YFinance: {yf_error}")
    
    return {'cotacao': 0.0, 'variacao': 0.0, 'fonte': 'Erro'}

@st.cache_data(ttl=300, show_spinner=False)
def _fetch_sp500_data() -> Dict[str, Any]:
    """Busca dados do S&P 500"""
    try:
        if _HAS_YF:
            sp500 = yf.Ticker("^GSPC")
            hist = sp500.history(period="2d")
            if len(hist) >= 2:
                price_current = hist['Close'].iloc[-1]
                price_prev = hist['Close'].iloc[-2]
                daily_change = ((price_current - price_prev) / price_prev) * 100
                
                # Calcular YTD
                current_year = datetime.now().year
                year_start = f"{current_year}-01-01"
                hist_ytd = sp500.history(start=year_start)
                if len(hist_ytd) > 1:
                    ytd_change = ((hist_ytd['Close'].iloc[-1] - hist_ytd['Close'].iloc[0]) / hist_ytd['Close'].iloc[0]) * 100
                else:
                    ytd_change = 0.0
                    
                return {
                    'valor_atual': price_current,
                    'variacao_dia': daily_change,
                    'variacao_ytd': ytd_change,
                    'fonte': 'YFinance'
                }
    except Exception as e:
        st.error(f"Erro ao buscar S&P 500: {e}")
    
    return {'valor_atual': 0.0, 'variacao_dia': 0.0, 'variacao_ytd': 0.0, 'fonte': 'Erro'}

def _display_indicators():
    """Exibe os indicadores econômicos em cards"""
    st.markdown("### 📊 Indicadores Econômicos")
    
    # Buscar dados
    with st.spinner("Atualizando indicadores..."):
        cdi_data = _fetch_cdi_data()
        imab_data = _fetch_imab_data()
        dolar_data = _fetch_dolar_data()
        sp500_data = _fetch_sp500_data()
    
    # Layout em colunas
    col1, col2, col3, col4 = st.columns(4)
    
    # CDI
    with col1:
        st.markdown('<div class="indicator-card">', unsafe_allow_html=True)
        st.markdown('<div class="h-label">💰 CDI/SELIC</div>', unsafe_allow_html=True)
        st.metric(
            label="Meta Anual",
            value=f"{cdi_data['selic_meta']:.2f}%",
            delta=f"{cdi_data['cdi_acum_ano']:.2f}% (YTD)"
        )
        st.caption(f"Fonte: {cdi_data['fonte']}")
        st.markdown('</div>', unsafe_allow_html=True)
    
    # IMA-B
    with col2:
        st.markdown('<div class="indicator-card">', unsafe_allow_html=True)
        st.markdown('<div class="h-label">📈 IMA-B</div>', unsafe_allow_html=True)
        imab_variation = imab_data['variacao_dia']
        st.metric(
            label="Valor",
            value=f"R$ {imab_data['valor_atual']:.2f}",
            delta=f"{imab_variation:.2f}%"
        )
        st.caption(f"YTD: {imab_data['variacao_ytd']:.2f}% | {imab_data['fonte']}")
        st.markdown('</div>', unsafe_allow_html=True)
    
    # Dólar
    with col3:
        st.markdown('<div class="indicator-card">', unsafe_allow_html=True)
        st.markdown('<div class="h-label">💵 Dólar</div>', unsafe_allow_html=True)
        dolar_variation = dolar_data['variacao']
        st.metric(
            label="USD/BRL",
            value=f"R$ {dolar_data['cotacao']:.2f}",
            delta=f"{dolar_variation:.2f}%"
        )
        st.caption(f"Fonte: {dolar_data['fonte']}")
        st.markdown('</div>', unsafe_allow_html=True)
    
    # S&P 500
    with col4:
        st.markdown('<div class="indicator-card">', unsafe_allow_html=True)
        st.markdown('<div class="h-label">🌎 S&P 500</div>', unsafe_allow_html=True)
        sp500_variation = sp500_data['variacao_dia']
        st.metric(
            label="Índice",
            value=f"{sp500_data['valor_atual']:.0f}",
            delta=f"{sp500_variation:.2f}%"
        )
        st.caption(f"YTD: {sp500_data['variacao_ytd']:.2f}% | {sp500_data['fonte']}")
        st.markdown('</div>', unsafe_allow_html=True)


# --------------------------
# API
# --------------------------
def _post_positions(start_date: date, end_date: date, portfolio_ids: List[str], headers: Dict[str, str]) -> pd.DataFrame:
    payload = {
        "start_date": str(start_date),
        "end_date": str(end_date),
        "instrument_position_aggregation": 3,
        "portfolio_ids": portfolio_ids
    }
    try:
        r = requests.post(
            f"{BASE_URL_API}/portfolio_position/positions/get",
            json=payload,
            headers=st.session_state.headers
        )
        r.raise_for_status()
        resultado = r.json()
        dados = resultado.get("objects", {})    
        registros = []
        for item in dados.values():
            if isinstance(item, list):
                registros.extend(item)
            else:
                registros.append(item)  
        df = pd.json_normalize(registros)
        st.session_state.df = df    
        # Renomeia colunas externas (overview / principais)
        df.rename(columns={
            "profitability_start_date": "%Dt Início",
            "profitability_in_day": "%Dia",
            "profitability_in_month": "%Mês",
            "profitability_in_semester": "%Semestre",
            "profitability_in_6_months": "%6 Meses",
            "profitability_in_year": "%Ano",
            "profitability_in_12_months": "%12 Meses",
            "profitability_in_18_months": "%18 Meses",
            "profitability_in_24_months": "%24 Meses",
            "profitability_in_30_months": "%30 Meses",
            "profitability_in_36_months": "%36 Meses",
            "profitability_in_48_months": "%48 Meses",
            "profitability_in_60_months": "%60 Meses",                    
            "net_asset_value": "PL",
            "portfolio_id": "ID Carteira",
            "overview_type": "Tipo de Overview",
            "date": "Data",
            "name": "Carteira",
            "instrument_positions": "Ativos",
            "last_shares": "Qtd. Cotas D-1",
            "is_opening": "Carteira de Abertura",
            'id': 'ID Overview',
            "navps": "Cota Líquida",
            "gross_navps": "Cota bruta",
            "shares" : "Qtd. Cotas",
            "fixed_shares": "Qtd. Cotas Fixas",
            "portfolio_average_duration": "Duração Média Carteira",
            "created_on": "Data de Criação",
            "benchmark_profitability.profitability_in_day": "Bench %Dia",
            "benchmark_profitability.profitability_in_month": "Bench %Mês",
            "benchmark_profitability.profitability_in_year": "Bench %Ano",
            "benchmark_profitability.profitability_in_12_months": "Bench %12 Meses",
            "benchmark_profitability.profitability_start_date": "Bench %Dt Início",
            "benchmark_profitability.profitability_in_semester": "Bench %Semestre",
            "benchmark_profitability.profitability_in_6_months": "Bench %6 Meses",
            "benchmark_profitability.profitability_in_18_months": "Bench %18 Meses",
            "benchmark_profitability.profitability_in_24_months": "Bench %24 Meses",
            "benchmark_profitability.profitability_in_30_months": "Bench %30 Meses",
            "benchmark_profitability.profitability_in_36_months": "Bench %36 Meses",
            "benchmark_profitability.profitability_in_48_months": "Bench %48 Meses",
            "benchmark_profitability.profitability_in_60_months": "Bench %60 Meses", 
            "modified_on": "Modificado em",
            "released_on": "Data de Liberação",
            "benchmark_profitability.symbol": "Nome Bench",
            "gross_asset_value": "Valor Bruto",
            "asset_value_for_allocation": "Valor para Alocação",
            "last_net_asset_value": "PL D-1",
            "last_navps": "Cota Líquida D-1",
            "fixed_navps": "Cota Fixa",
            "financial_transaction_positions": "CPR",
            "attribution.portfolio_beta.financial_value": "PnL Beta",
            "attribution_portfolio_beta_percentage": "zzzzzzzzz_Repetido",
            "attribution_portfolio_beta_financial": "zzzzzzz_Repetido",
            "attribution.portfolio_beta.percentage_value": "PnL % Beta",
            "attribution.total.financial_value": "PnL Total",
            "attribution.total.percentage_value": "PnL % Total",
            "attribution_total_financial": "zzzzzz_Repetido",
            "attribution_total_percentage": "zzzzz_Repetido",
            "attribution.currency.financial_value": "PnL Moeda",
            "attribution.currency.percentage_value": "PnL % Moeda",
            "attribution_currency_financial": "zzzz_Repetido",
            "attribution_currency_percentage": "zzz_Repetido",
            "attribution_maximums.par_price": "PnL Máximo Preço Par",
            "attribution_maximums.portfolio_beta": "PnL Máximo Beta da Carteira",
            "attribution_maximums.total": "PnL Máximo Total",
            "attribution_maximums.total_hedged": "PnL Máximo Total Hedgeado",
            "corp_actions_adjusted_navps": "Cota Líquida Ajustada por Eventos Societários",
            "corp_actions_factor": "Fator de Ajuste por Eventos Societários",
            "equity_exposure": "Exposição em Renda Variável",
            "is_system_generated": "Gerado pelo Sistema",
            "navps_admin_status": "Status Administrativo da Cota Líquida",
            "navps_one_day_return": "Retorno Diário da Cota Líquida",
            "navps_status": "Status da Cota Líquida",
            "net_liabilities_transactions_financial_value": "Valor Financeiro das Transações de Passivo Líquido",
            "overview_status": "Status do Overview",
            "pct_lent_exposure": "Exposição % Doada",
            "portfolio_average_term": "Prazo Médio da Carteira",
            "attribution_maximums.corp_actions": "PnL Máximo Eventos Societários",
            "attribution_maximums.currency": "PnL Máximo  Moeda"
        }, inplace=True)    
        # Após o dicionário de renomeação, adicione:
        cols_to_drop = [col for col in df.columns if 'repetido' in col.lower() or 'Repetido' in col]
        df = df.drop(columns=cols_to_drop)
        
        # Converter colunas de porcentagem para numérico
        percentage_cols = [col for col in df.columns if col.startswith('%') or col.startswith('Bench %')]
        for col in percentage_cols:
            df[col] = df[col].apply(_parse_percentage_value)
            
    except Exception as e:
        st.error(f"Erro ao buscar dados: {e}")

    return df


# --------------------------
# Funções de Comparação
# --------------------------
def _calculate_benchmark_returns(periods_dict: Dict, benchmark_name: str) -> Dict[str, float]:
    """Calcula retornos dos benchmarks para diferentes períodos"""
    if not _HAS_YF:
        return {}
    
    today = datetime.now().date()
    returns = {}
    
    try:
        if benchmark_name == "CDI":
            # Para CDI, usamos uma aproximação baseada na taxa anual
            cdi_data = _fetch_cdi_data()
            cdi_daily = (1 + cdi_data['selic_meta']/100) ** (1/252) - 1
            
            for period_name, (start_date, end_date) in periods_dict.items():
                if period_name == "D":
                    returns[period_name] = cdi_daily
                else:
                    # Aproximação simples - em produção você buscaria dados históricos
                    business_days = np.busday_count(start_date.date(), end_date.date())
                    returns[period_name] = (1 + cdi_daily) ** business_days - 1
                    
        elif benchmark_name == "IMA-B (IMAB11)":
            imab = yf.Ticker("IMAB11.SA")
            for period_name, (start_date, end_date) in periods_dict.items():
                hist = imab.history(start=start_date.date(), end=(end_date + pd.Timedelta(days=1)).date())
                if not hist.empty and len(hist) > 1:
                    start_price = hist['Close'].iloc[0]
                    end_price = hist['Close'].iloc[-1]
                    returns[period_name] = (end_price - start_price) / start_price
                else:
                    returns[period_name] = 0.0
                    
        elif benchmark_name == "S&P 500":
            sp500 = yf.Ticker("^GSPC")
            for period_name, (start_date, end_date) in periods_dict.items():
                hist = sp500.history(start=start_date.date(), end=(end_date + pd.Timedelta(days=1)).date())
                if not hist.empty and len(hist) > 1:
                    start_price = hist['Close'].iloc[0]
                    end_price = hist['Close'].iloc[-1]
                    returns[period_name] = (end_price - start_price) / start_price
                else:
                    returns[period_name] = 0.0
                    
        elif benchmark_name == "USD/BRL":
            usdbrl = yf.Ticker("USDBRL=X")
            for period_name, (start_date, end_date) in periods_dict.items():
                hist = usdbrl.history(start=start_date.date(), end=(end_date + pd.Timedelta(days=1)).date())
                if not hist.empty and len(hist) > 1:
                    start_price = hist['Close'].iloc[0]
                    end_price = hist['Close'].iloc[-1]
                    returns[period_name] = (end_price - start_price) / start_price
                else:
                    returns[period_name] = 0.0
                    
    except Exception as e:
        st.error(f"Erro ao calcular {benchmark_name}: {e}")
    
    return returns

def _create_comparison_dataframe(df_perf: pd.DataFrame, selected_carteiras: List[str], selected_benchmarks: List[str]) -> pd.DataFrame:
    """Cria DataFrame comparativo entre carteiras e benchmarks"""
    periods = _periods(pd.Timestamp.now())
    
    # Coletar dados das carteiras
    carteira_data = []
    for carteira in selected_carteiras:
        carteira_df = df_perf[df_perf["Carteira"] == carteira].copy()
        if not carteira_df.empty:
            latest = carteira_df.sort_values("Data").iloc[-1]
            
            # Garantir que os valores são numéricos
            dia_val = latest.get("%Dia", 0)
            mes_val = latest.get("%Mês", 0)
            ytd_val = latest.get("%Ano", 0)
            m12_val = latest.get("%12m", 0)
            m24_val = latest.get("%24m", 0)
            m36_val = latest.get("%36m", 0)
            
            # Converter para float se necessário
            try:
                dia_val = float(dia_val) if not isinstance(dia_val, (int, float)) else dia_val
            except (ValueError, TypeError):
                dia_val = 0.0
                
            try:
                mes_val = float(mes_val) if not isinstance(mes_val, (int, float)) else mes_val
            except (ValueError, TypeError):
                mes_val = 0.0
                
            try:
                ytd_val = float(ytd_val) if not isinstance(ytd_val, (int, float)) else ytd_val
            except (ValueError, TypeError):
                ytd_val = 0.0
                
            try:
                m12_val = float(m12_val) if not isinstance(m12_val, (int, float)) else m12_val
            except (ValueError, TypeError):
                m12_val = 0.0
                
            try:
                m24_val = float(m24_val) if not isinstance(m24_val, (int, float)) else m24_val
            except (ValueError, TypeError):
                m24_val = 0.0
                
            try:
                m36_val = float(m36_val) if not isinstance(m36_val, (int, float)) else m36_val
            except (ValueError, TypeError):
                m36_val = 0.0
            
            carteira_data.append({
                "Nome": carteira,
                "Tipo": "Carteira",
                "D": dia_val,
                "Mês": mes_val,
                "YTD": ytd_val,
                "12m": m12_val,
                "24m": m24_val,
                "36m": m36_val,
            })
    
    # Coletar dados dos benchmarks
    benchmark_data = []
    for benchmark in selected_benchmarks:
        bench_returns = _calculate_benchmark_returns(periods, benchmark)
        benchmark_data.append({
            "Nome": benchmark,
            "Tipo": "Benchmark",
            "D": bench_returns.get("D", 0),
            "Mês": bench_returns.get("MTD", 0),
            "YTD": bench_returns.get("YTD", 0),
            "12m": bench_returns.get("12m", 0),
            "24m": bench_returns.get("24m", 0),
            "36m": bench_returns.get("36m", 0),
        })
    
    # Combinar dados
    all_data = carteira_data + benchmark_data
    comparison_df = pd.DataFrame(all_data)
    
    # Garantir que todas as colunas numéricas são float
    numeric_cols = ["D", "Mês", "YTD", "12m", "24m", "36m"]
    for col in numeric_cols:
        comparison_df[col] = pd.to_numeric(comparison_df[col], errors='coerce').fillna(0.0)
    
    return comparison_df

def _render_comparison_table(comparison_df: pd.DataFrame):
    """Renderiza tabela de comparação com cores"""
    if comparison_df.empty:
        return
    
    # Criar cópia para formatação
    display_df = comparison_df.copy()
    
    # Formatar porcentagens
    periods = ["D", "Mês", "YTD", "12m", "24m", "36m"]
    for period in periods:
        display_df[period] = display_df[period].apply(_fmt_pct_color)
    
    # Reorganizar colunas
    display_df = display_df[["Nome", "Tipo"] + periods]
    
    # Exibir tabela
    st.markdown("### 📊 Comparação de Performance")
    
    # Adicionar CSS para tabela
    st.markdown("""
    <style>
    .dataframe td {
        text-align: center !important;
    }
    .dataframe th {
        text-align: center !important;
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.write(display_df.to_html(escape=False, index=False), unsafe_allow_html=True)

def _render_performance_chart(comparison_df: pd.DataFrame):
    """Renderiza gráfico de performance comparada"""
    if comparison_df.empty or len(comparison_df) <= 1:
        return
    
    # Preparar dados para o gráfico
    chart_df = comparison_df.copy()
    chart_df = chart_df.set_index("Nome")
    
    # Selecionar períodos para o gráfico
    periods = ["D", "Mês", "YTD", "12m", "24m", "36m"]
    
    # Criar gráfico de barras agrupadas
    fig = go.Figure()
    
    colors = px.colors.qualitative.Set3
    for i, period in enumerate(periods):
        fig.add_trace(go.Bar(
            name=period,
            x=chart_df.index,
            y=chart_df[period] * 100,  # Converter para porcentagem
            text=chart_df[period].apply(lambda x: f"{x*100:.1f}%"),
            textposition='auto',
            marker_color=colors[i % len(colors)]
        ))
    
    fig.update_layout(
        title="Performance Comparada por Período",
        xaxis_title="",
        yaxis_title="Retorno (%)",
        barmode='group',
        height=500,
        showlegend=True
    )
    
    st.plotly_chart(fig, use_container_width=True)

def _render_detailed_comparison(df_perf: pd.DataFrame, selected_carteiras: List[str], selected_benchmarks: List[str]):
    """Renderiza visão detalhada de comparação"""
    if not selected_carteiras or not selected_benchmarks:
        return
    
    st.markdown("### 🎯 Análise Detalhada por Carteira")
    
    for carteira in selected_carteiras:
        carteira_df = df_perf[df_perf["Carteira"] == carteira].copy()
        if carteira_df.empty:
            continue
            
        latest = carteira_df.sort_values("Data").iloc[-1]
        
        col1, col2 = st.columns([1, 1])
        
        with col1:
            st.markdown(f'<div class="card"><h4>📈 {carteira}</h4>', unsafe_allow_html=True)
            
            # Garantir que os valores são numéricos
            dia_val = latest.get("%Dia", 0)
            mes_val = latest.get("%Mês", 0)
            ytd_val = latest.get("%Ano", 0)
            m12_val = latest.get("%12m", 0)
            m24_val = latest.get("%24m", 0)
            m36_val = latest.get("%36m", 0)
            
            # Métricas da carteira
            metrics_html = """
            <div class="stats-grid">
                <div><strong>Dia:</strong><br>{dia}</div>
                <div><strong>Mês:</strong><br>{mes}</div>
                <div><strong>YTD:</strong><br>{ytd}</div>
                <div><strong>12m:</strong><br>{m12}</div>
                <div><strong>24m:</strong><br>{m24}</div>
                <div><strong>36m:</strong><br>{m36}</div>
            </div>
            """.format(
                dia=_fmt_pct_color(dia_val),
                mes=_fmt_pct_color(mes_val),
                ytd=_fmt_pct_color(ytd_val),
                m12=_fmt_pct_color(m12_val),
                m24=_fmt_pct_color(m24_val),
                m36=_fmt_pct_color(m36_val)
            )
            st.markdown(metrics_html, unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        
        with col2:
            st.markdown('<div class="card"><h4>📊 Vs Benchmarks</h4>', unsafe_allow_html=True)
            
            # Calcular diferenças vs benchmarks
            periods_data = _periods(pd.Timestamp.now())
            comparison_data = []
            
            # Garantir que o valor de 12m é numérico
            try:
                carteira_12m = float(latest.get("%12m", 0)) if not isinstance(latest.get("%12m", 0), (int, float)) else latest.get("%12m", 0)
            except (ValueError, TypeError):
                carteira_12m = 0.0
            
            for benchmark in selected_benchmarks:
                bench_returns = _calculate_benchmark_returns(periods_data, benchmark)
                bench_12m = bench_returns.get("12m", 0)
                
                diff_12m = carteira_12m - bench_12m
                
                comparison_data.append({
                    "Benchmark": benchmark,
                    "Diff 12m": diff_12m
                })
            
            # Exibir comparação
            for comp in comparison_data:
                st.markdown(f"""
                **{comp['Benchmark']}**: {_fmt_diff_color(comp['Diff 12m'])}
                """, unsafe_allow_html=True)
            
            st.markdown('</div>', unsafe_allow_html=True)


# --------------------------
# Tela – Performance
# --------------------------
def tela_performance() -> None:
    if "headers" not in st.session_state or not st.session_state.headers:
        st.warning("Faça login para consultar os dados.")
        return

    st.markdown("### 📈 Performance de Carteiras")

    # ----- Indicadores Econômicos -----
    _display_indicators()
    
    st.markdown("---")

    # ----- Filtros -----
    with st.container():
        c_f1, c_f2, c_f3, c_f4 = st.columns([1.1, 2, 1, 1])
        with c_f1:
            st.markdown('<div class="h-label">Janela</div>', unsafe_allow_html=True)
            janela = st.date_input("", value=[date.today() - timedelta(days=365), date.today()], 
                                 key="perf_janela", label_visibility="collapsed")
            if isinstance(janela, list) and len(janela) == 2:
                d_ini, d_fim = janela
            else:
                d_ini = date.today() - timedelta(days=365)
                d_fim = date.today()
        with c_f2:
            st.markdown('<div class="h-label">Carteiras</div>', unsafe_allow_html=True)
            carteiras_nomes = st.multiselect(
                "", sorted(CARTEIRAS.values()),
                default=[],
                key="perf_carteiras",
                label_visibility="collapsed"
            )
        with c_f3:
            st.markdown('<div class="h-label">Ações</div>', unsafe_allow_html=True)
            carregar = st.button("Carregar", key="perf_btn_carregar", use_container_width=True)
        with c_f4:
            st.markdown('<div class="h-label">Benchmarks</div>', unsafe_allow_html=True)
            bmarks = st.multiselect(
                "", ["CDI", "IMA-B (IMAB11)", "USD/BRL", "S&P 500"],
                default=["CDI", "IMA-B (IMAB11)"],
                key="perf_bmarks",
                label_visibility="collapsed"
            )

    # Mapeia nomes -> ids
    carteiras_ids = [k for k, v in CARTEIRAS.items() if v in carteiras_nomes]

    if carregar:
        if not carteiras_ids:
            st.error("Selecione ao menos uma carteira.")
            return
        try:
            with st.spinner("Buscando dados das carteiras..."):
                df = _post_positions(d_ini, d_fim, carteiras_ids, st.session_state.headers)
            if df.empty:
                st.info("Nenhum dado retornado para os filtros informados.")
                return
            st.session_state.df_perf = df
            st.session_state.selected_carteiras = carteiras_nomes
            st.session_state.selected_benchmarks = bmarks
            st.success("Dados carregados com sucesso!")
        except Exception as e:
            st.error(f"Erro ao buscar dados: {e}")
            return

    # ----- Comparação -----
    if "df_perf" in st.session_state and not st.session_state.df_perf.empty:
        df = st.session_state.df_perf
        
        # Criar comparação
        comparison_df = _create_comparison_dataframe(
            df, 
            st.session_state.get("selected_carteiras", []),
            st.session_state.get("selected_benchmarks", [])
        )
        
        if not comparison_df.empty:
            # Tabela de comparação
            _render_comparison_table(comparison_df)
            
            st.markdown("---")
            
            # Gráfico de performance
            _render_performance_chart(comparison_df)
            
            st.markdown("---")
            
            # Análise detalhada
            _render_detailed_comparison(
                df,
                st.session_state.get("selected_carteiras", []),
                st.session_state.get("selected_benchmarks", [])
            )
            
            st.markdown("---")
            
            # Dados brutos (opcional)
            with st.expander("📋 Visualizar Dados Brutos"):
                display_df = df[['Carteira','Data','%Dia','%Mês','%Ano','%12m','%24m','%36m','Cota Líquida']].copy()
                # Formatar porcentagens para exibição
                for col in ['%Dia','%Mês','%Ano','%12m','%24m','%36m']:
                    display_df[col] = display_df[col].apply(lambda x: f"{x*100:.2f}%" if pd.notna(x) else "")
                st.dataframe(display_df.sort_values(["Carteira","Data"]))
    else:
        st.info("👆 Selecione as carteiras e benchmarks, depois clique em 'Carregar' para ver a análise comparativa.")