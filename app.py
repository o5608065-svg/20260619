import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import networkx as nx
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import urllib.request
import os
import datetime
import warnings

warnings.filterwarnings('ignore')

# ================= 解决中文字体问题 =================
@st.cache_resource
def load_chinese_font():
    font_path = "SimHei.ttf"
    if not os.path.exists(font_path):
        try:
            font_url = "https://github.com/StellarCN/scp_zh/raw/master/fonts/SimHei.ttf"
            urllib.request.urlretrieve(font_url, font_path)
        except Exception:
            pass
    if os.path.exists(font_path):
        fm.fontManager.addfont(font_path)
        plt.rcParams['font.sans-serif'] = ['SimHei']
    else:
        plt.rcParams['font.sans-serif'] = ['SimHei', 'WenQuanYi Micro Hei', 'Microsoft YaHei']
        
load_chinese_font()
plt.rcParams['axes.unicode_minus'] = False 

# ================= 页面与基础配置 =================
st.set_page_config(page_title="强势股逃顶与共振网络系统", layout="wide")
st.title("📈 强势股逃顶与隐藏共振网络系统")
st.markdown("基于风险调整后动量、拥挤度监控与非线性互信息网络的实战框架")

# ================= 侧边栏：参数配置 =================
st.sidebar.header("⚙️ 系统参数配置")
default_tickers = "NVDA, AAPL, TSLA, 600519.SS, 000063.SZ, 002475.SZ, 688981.SS, 601138.SS"
ticker_input = st.sidebar.text_area("监控股票池 (以逗号分隔)", value=default_tickers, height=100)
tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]

st.sidebar.markdown("---")
st.sidebar.subheader("主观盘面判断")
is_sector_collapsing = st.sidebar.checkbox(
    "🚨 触发清仓级信号 (板块崩塌)", 
    help="观察到板块资金扩散：最强的龙头股高位滞涨，而边缘垃圾股突然补涨（群魔乱舞）。"
)

# ================= 核心工具函数 =================
@st.cache_data
def get_stock_names(tickers_list):
    """调用新浪接口获取A股中文名，美股保留原代码"""
    labels = {}
    for t in tickers_list:
        if t.endswith('.SS'):
            code = 'sh' + t[:6]
        elif t.endswith('.SZ'):
            code = 'sz' + t[:6]
        else:
            labels[t] = t
            continue
        try:
            url = f"http://hq.sinajs.cn/list={code}"
            req = urllib.request.Request(url, headers={'Referer': 'http://finance.sina.com.cn'})
            with urllib.request.urlopen(req, timeout=2) as response:
                res = response.read().decode('gbk')
                name = res.split(',')[0].split('"')[1]
                labels[t] = name if name else t
        except Exception:
            labels[t] = t 
    return labels

def calc_mutual_information(x, y, bins):
    c_xy, _, _ = np.histogram2d(x, y, bins=bins)
    p_xy = c_xy / float(np.sum(c_xy))
    p_x = np.sum(p_xy, axis=1)
    p_y = np.sum(p_xy, axis=0)
    p_x_p_y = p_x[:, None] * p_y[None, :]
    nzs = p_xy > 0
    return np.sum(p_xy[nzs] * np.log(p_xy[nzs] / p_x_p_y[nzs]))

# ================= 数据获取模块 =================
@st.cache_data(ttl=3600)
def fetch_market_data(tickers_list, days=400):
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=days)
    data_dict = {}
    
    with st.spinner('正在联网拉取核心行情数据...'):
        for ticker in tickers_list:
            df = yf.download(ticker, start=start_date, end=end_date, progress=False)
            if not df.empty and len(df) > 260:
                if isinstance(df.columns, pd.MultiIndex):
                    clean_df = pd.DataFrame({
                        'Close': df['Close'][ticker], 'High': df['High'][ticker],
                        'Low': df['Low'][ticker], 'Volume': df['Volume'][ticker]
                    })
                else:
                    clean_df = df[['Close', 'High', 'Low', 'Volume']].copy()
                data_dict[ticker] = clean_df
    return data_dict

data_dict = fetch_market_data(tickers)
if not data_dict:
    st.error("无法获取数据，请检查股票代码或网络连接。")
    st.stop()

# ================= 业务逻辑模块 =================
def run_phase_1(data_dict, window=20):
    results = []
    for ticker, df in data_dict.items():
        temp_df = df[['Close']].copy()
        temp_df['MOM20'] = temp_df['Close'] / temp_df['Close'].shift(window) - 1
        temp_df['daily_return'] = temp_df['Close'].pct_change()
        temp_df['volatility_20d'] = temp_df['daily_return'].rolling(window=window).std()
        temp_df['RAM'] = np.where(temp_df['volatility_20d'] > 0, temp_df['MOM20'] / temp_df['volatility_20d'], np.nan)
        
        latest = temp_df.iloc[-1]
        results.append({
            '代码': ticker, '最新收盘': latest['Close'], '20日动量': latest['MOM20'],
            '20日波动': latest['volatility_20d'], 'RAM': latest['RAM']
        })
    df_res = pd.DataFrame(results).dropna()
    return df_res.sort_values(by='RAM', ascending=False).reset_index(drop=True)

def run_phase_2(data_dict, selected_tickers):
    results = []
    for ticker in selected_tickers:
        if ticker not in data_dict: continue
        df = data_dict[ticker].copy()
        df['daily_return'] = df['Close'].pct_change()
        df['5d_return'] = df['Close'].pct_change(5)
        
        acc = (df['5d_return'] - df['5d_return'].rolling(20).mean()).iloc[-1]
        vol_pct = df['Volume'].rolling(252).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x)>0 else np.nan).iloc[-1]
        vol_ratio = (df['daily_return'].rolling(5).std() / df['daily_return'].rolling(20).std()).iloc[-1]
        
        range_p = df['High'] - df['Low']
        df['CloseStrength'] = np.where(range_p > 0, (df['Close'] - df['Low']) / range_p, 0.5)
        strength_3d = df['CloseStrength'].rolling(3).mean().iloc[-1]
        is_surge = df['Volume'].iloc[-1] > (2 * df['Volume'].rolling(20).mean().iloc[-1])
        
        warns = [acc > 0.05, vol_pct > 0.90, vol_ratio > 1.5, strength_3d < 0.5, is_surge and strength_3d < 0.5]
        
        results.append({
            '代码': ticker, '加速率': f"{acc:.3f} {'🔴' if warns[0] else '🟢'}",
            '拥挤度': f"{vol_pct:.2%} {'🔴' if warns[1] else '🟢'}",
            '波动率比': f"{vol_ratio:.2f} {'🔴' if warns[2] else '🟢'}",
            '收盘强度': f"{strength_3d:.2f} {'🔴' if warns[3] else '🟢'}",
            '量价背离': f"{'是 🔴' if warns[4] else '否 🟢'}", '风险得分': sum(warns)
        })
    return pd.DataFrame(results)

def run_phase_3(risk_df, is_collapsing):
    orders = []
    for _, row in risk_df.iterrows():
        if is_collapsing:
            level, action, pos = "🚨 清仓级", "清仓离场(网络崩塌)", "0%"
        else:
            score = row['风险得分']
            if score >= 3:
                level, action, pos = "🔴 危险级", "立刻减仓", "50%"
            elif score >= 1:
                level, action, pos = "🟠 警告级", "停止加仓", "维持"
            else:
                level, action, pos = "🟢 安全级", "正常持有", "维持"
        orders.append({'代码': row['代码'], '风险得分': 'N/A' if is_collapsing else row['风险得分'], '级别': level, '动作': action, '仓位': pos})
    return pd.DataFrame(orders)

@st.cache_data
def analyze_network(close_prices_df):
    returns_df = np.log(close_prices_df / close_prices_df.shift(1)).dropna()
    tickers = returns_df.columns.tolist()
    N_samples = len(returns_df)
    N_stocks = len(tickers)
    
    bins = max(3, int(np.floor(N_samples ** (1/3))))
    rho_matrix = returns_df.corr(method='pearson').values
    delta_I_matrix = np.zeros((N_stocks, N_stocks))
    
    for i in range(N_stocks):
        for j in range(i + 1, N_stocks):
            x = returns_df.iloc[:, i].values
            y = returns_df.iloc[:, j].values
            I_emp = calc_mutual_information(x, y, bins)
            rho = np.clip(rho_matrix[i, j], -0.999, 0.999) 
            I_g = -0.5 * np.log(1 - rho**2)
            delta_I = max(0, I_emp - I_g)
            delta_I_matrix[i, j] = delta_I_matrix[j, i] = delta_I
            
    delta_I_df = pd.DataFrame(delta_I_matrix, index=tickers, columns=tickers)
    mean_delta_I = delta_I_df.sum(axis=1) / (N_stocks - 1)
    return delta_I_df, mean_delta_I

def plot_mst(delta_I_df):
    dist_matrix = 1 / (delta_I_df + 1e-5)
    tickers = delta_I_df.columns.tolist()
    name_labels = get_stock_names(tickers)
    
    G = nx.Graph()
    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):
            if delta_I_df.iloc[i, j] > 0:
                G.add_edge(tickers[i], tickers[j], weight=dist_matrix.iloc[i, j])
                
    MST = nx.minimum_spanning_tree(G)
    fig, ax = plt.subplots(figsize=(10, 6))
    pos = nx.spring_layout(MST, k=0.5, seed=42)
    
    nx.draw_networkx_nodes(MST, pos, ax=ax, node_size=800, node_color='#ADD8E6', alpha=0.9, edgecolors='white')
    nx.draw_networkx_labels(MST, pos, labels=name_labels, ax=ax, font_size=11, font_weight='bold', font_color='black')
    nx.draw_networkx_edges(MST, pos, ax=ax, width=2, alpha=0.6, edge_color='#ff7f0e')
    ax.axis('off')
    return fig

# ================= UI 渲染与切签页 =================
tab1, tab2, tab3, tab4 = st.tabs(["📊 阶段一：选股", "🕵️ 阶段二：预警", "⚡ 阶段三：执行", "🕸️ 阶段四：共振网络"])

with tab1:
    st.subheader("寻找上涨平稳、波动率低的健康标的")
    phase1_df = run_phase_1(data_dict)
    name_dict_1 = get_stock_names(phase1_df['代码'].tolist())
    phase1_df.insert(1, '名称', phase1_df['代码'].map(name_dict_1))
    st.dataframe(phase1_df, use_container_width=True)

with tab2:
    st.subheader("5大维度透视资金结构")
    phase2_df = run_phase_2(data_dict, phase1_df['代码'].tolist())
    name_dict_2 = get_stock_names(phase2_df['代码'].tolist())
    phase2_df.insert(1, '名称', phase2_df['代码'].map(name_dict_2))
    st.dataframe(phase2_df, use_container_width=True)

with tab3:
    st.subheader("基于水温严格执行纪律")
    phase3_df = run_phase_3(phase2_df, is_sector_collapsing)
    st.dataframe(phase3_df, use_container_width=True)

with tab4:
    st.subheader("剥离大盘同涨同跌后的核心共振标的")
    # 提取收盘价数据池用于网络分析
    close_prices = pd.DataFrame({t: d['Close'] for t, d in data_dict.items()}).dropna()
    
    if close_prices.shape[1] < 2:
        st.warning("有效标的不足两只，无法构建共振网络。")
    else:
        with st.spinner("正在执行矩阵张量运算，寻找隐藏共振网络..."):
            delta_i_matrix, factor_scores = analyze_network(close_prices)
            
            col1, col2 = st.columns([1, 2])
            with col1:
                st.markdown("##### 🏆 隐藏共动得分排行")
                factor_df = factor_scores.sort_values(ascending=False).reset_index()
                factor_df.columns = ['代码', '共动得分']
                name_dict_net = get_stock_names(factor_df['代码'].tolist())
                factor_df.insert(1, '名称', factor_df['代码'].map(name_dict_net))
                st.dataframe(factor_df, use_container_width=True, height=400)
                
            with col2:
                st.markdown("##### 🕸️ 最小生成树 (MST) 拓扑结构")
                fig = plot_mst(delta_i_matrix)
                st.pyplot(fig)
                st.caption("网络说明：连线越短/得分越高的节点，代表在剔除大盘因素后，存在着极其紧密的非线性资金抱团。如果高危股处于核心节点，极易引发板块连锁闪崩。")
