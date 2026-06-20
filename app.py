import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import datetime
import json
import os

# ================= 股票名称与代码映射字典 =================
STOCK_MAPPING = {
    "苹果": "AAPL", "APPLE": "AAPL",
    "英伟达": "NVDA", "NVIDIA": "NVDA",
    "微软": "MSFT", "MICROSOFT": "MSFT",
    "特斯拉": "TSLA", "TESLA": "TSLA",
    "亚马逊": "AMZN", "AMAZON": "AMZN",
    "谷歌": "GOOGL", "GOOGLE": "GOOGL",
    "脸书": "META", "META": "META",
    "超微半导体": "AMD",
    "贵州茅台": "600519.SS", "茅台": "600519.SS",
    "宁德时代": "300750.SZ", "宁王": "300750.SZ",
    "比亚迪": "002594.SZ",
    "招商银行": "600036.SS", "招行": "600036.SS",
    "五粮液": "000858.SZ",
    "中国平安": "601318.SS",
    "立讯精密": "002475.SZ",
    "中芯国际": "688981.SS",
    "中兴通讯": "000063.SZ",
    "腾讯": "0700.HK", "腾讯控股": "0700.HK",
    "阿里": "BABA", "阿里巴巴": "BABA"
}

TICKER_TO_NAME = {v: k for k, v in STOCK_MAPPING.items()}

def resolve_input(raw_inputs):
    resolved_tickers = []
    for item in raw_inputs:
        clean_item = item.strip().upper()
        if clean_item in [k.upper() for k in STOCK_MAPPING.keys()]:
            for key, val in STOCK_MAPPING.items():
                if key.upper() == clean_item:
                    resolved_tickers.append(val)
                    break
        else:
            resolved_tickers.append(clean_item)
    return list(dict.fromkeys(resolved_tickers))

# ================= 记忆模块 (自选股持久化) =================
WATCHLIST_FILE = "watchlist.json"
DEFAULT_WATCHLIST = ["英伟达", "苹果", "微软", "特斯拉", "贵州茅台", "宁王", "AMD", "GOOGL"]

def load_watchlist():
    """从本地读取自选股清单"""
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return DEFAULT_WATCHLIST
    return DEFAULT_WATCHLIST

def save_watchlist(watchlist):
    """将自选股清单保存到本地"""
    with open(WATCHLIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(watchlist, f, ensure_ascii=False)

# 初始化 Session State
if 'watchlist' not in st.session_state:
    st.session_state.watchlist = load_watchlist()

# ================= 页面与基础配置 =================
st.set_page_config(page_title="强势股逃顶择时系统", layout="wide")
st.title("📈 强势股逃顶择时量化系统")
st.markdown("基于风险调整后动量选股与拥挤度监控的实战框架")

# ================= 侧边栏：自选股记忆模块 =================
st.sidebar.header("📁 自选股记忆模块")

# 1. 添加自选股
new_stock = st.sidebar.text_input("➕ 添加自选 (支持中文名或代码):", placeholder="例如: 腾讯 或 002594.SZ")
if st.sidebar.button("添加", use_container_width=True):
    if new_stock:
        clean_new = new_stock.strip()
        if clean_new not in st.session_state.watchlist:
            st.session_state.watchlist.append(clean_new)
            save_watchlist(st.session_state.watchlist)
            st.sidebar.success(f"已成功添加: {clean_new}")
            st.rerun() # 刷新页面更新数据
        else:
            st.sidebar.warning("该股票已在自选池中！")

st.sidebar.markdown("---")

# 2. 移除自选股
to_remove = st.sidebar.multiselect("➖ 移除自选 (可多选):", st.session_state.watchlist)
if st.sidebar.button("确认移除", use_container_width=True):
    if to_remove:
        st.session_state.watchlist = [s for s in st.session_state.watchlist if s not in to_remove]
        save_watchlist(st.session_state.watchlist)
        st.sidebar.success("移除成功！")
        st.rerun()

st.sidebar.markdown("---")

# 3. 显示当前监控池
st.sidebar.markdown(f"**当前监控池 (共 {len(st.session_state.watchlist)} 只):**")
st.sidebar.info(", ".join(st.session_state.watchlist))

st.sidebar.markdown("---")
st.sidebar.subheader("主观盘面判断")
is_sector_collapsing = st.sidebar.checkbox(
    "🚨 触发清仓级信号 (板块崩塌)", 
    help="观察到板块资金扩散：最强的龙头股高位滞涨，而边缘垃圾股突然补涨（群魔乱舞）。"
)

# 获取解析后的最终代码列表
tickers = resolve_input(st.session_state.watchlist)

# ================= 核心数据获取 (带缓存) =================
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
                        'Close': df['Close'][ticker],
                        'High': df['High'][ticker],
                        'Low': df['Low'][ticker],
                        'Volume': df['Volume'][ticker]
                    })
                else:
                    clean_df = df[['Close', 'High', 'Low', 'Volume']].copy()
                data_dict[ticker] = clean_df
    return data_dict

data_dict = fetch_market_data(tickers)

if not data_dict:
    st.error("无法获取数据。请检查自选池中是否有拼写错误，或者检查网络连接。")
    st.stop()

# ================= 逻辑模块 =================
def run_phase_1(data_dict, window=20):
    results = []
    for ticker, df in data_dict.items():
        temp_df = df[['Close']].copy()
        temp_df['MOM20'] = temp_df['Close'] / temp_df['Close'].shift(window) - 1
        temp_df['daily_return'] = temp_df['Close'].pct_change()
        temp_df['volatility_20d'] = temp_df['daily_return'].rolling(window=window).std()
        
        temp_df['RAM'] = np.where(temp_df['volatility_20d'] > 0, 
                                  temp_df['MOM20'] / temp_df['volatility_20d'], np.nan)
        
        latest = temp_df.iloc[-1]
        stock_name = TICKER_TO_NAME.get(ticker, ticker)
        
        results.append({
            '股票代码': ticker, '股票名称': stock_name, '最新收盘价': latest['Close'],
            '20日动量': latest['MOM20'], '20日波动率': latest['volatility_20d'], '风险调整后动量 (RAM)': latest['RAM']
        })
    df_res = pd.DataFrame(results).dropna()
    return df_res.sort_values(by='风险调整后动量 (RAM)', ascending=False).reset_index(drop=True)

def run_phase_2(data_dict, selected_tickers):
    results = []
    for ticker in selected_tickers:
        if ticker not in data_dict: continue
        df = data_dict[ticker].copy()
        df['daily_return'] = df['Close'].pct_change()
        df['5d_return'] = df['Close'].pct_change(5)
        
        df['5d_return_20d_avg'] = df['5d_return'].rolling(window=20).mean()
        acc = (df['5d_return'] - df['5d_return_20d_avg']).iloc[-1]
        warn_acc = acc > 0.05 
        
        vol_pct = df['Volume'].rolling(252).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x)>0 else np.nan).iloc[-1]
        warn_crowd = vol_pct > 0.90
        
        vol_ratio = (df['daily_return'].rolling(5).std().iloc[-1]) / (df['daily_return'].rolling(20).std().iloc[-1] + 1e-9)
        warn_vol = vol_ratio > 1.5
        
        range_p = df['High'] - df['Low']
        df['CloseStrength'] = np.where(range_p > 0, (df['Close'] - df['Low']) / range_p, 0.5)
        strength_3d = df['CloseStrength'].rolling(3).mean().iloc[-1]
        warn_strength = strength_3d < 0.5
        
        vol_20d_avg = df['Volume'].rolling(20).mean().iloc[-1]
        warn_diverge = (df['Volume'].iloc[-1] > (2 * vol_20d_avg)) and (df['CloseStrength'].iloc[-1] < 0.5)
        
        score = sum([warn_acc, warn_crowd, warn_vol, warn_strength, warn_diverge])
        stock_name = TICKER_TO_NAME.get(ticker, ticker)
        
        results.append({
            '股票代码': ticker, '股票名称': stock_name, '加速率 (>0.05)': f"{acc:.3f} {'🔴' if warn_acc else '🟢'}",
            '拥挤度 (>90%)': f"{vol_pct:.2%} {'🔴' if warn_crowd else '🟢'}", '波动率比 (>1.5)': f"{vol_ratio:.2f} {'🔴' if warn_vol else '🟢'}",
            '收盘强度 (<0.5)': f"{strength_3d:.2f} {'🔴' if warn_strength else '🟢'}", '量价背离': f"{'是 🔴' if warn_diverge else '否 🟢'}",
            '风险得分 (0-5)': score
        })
    return pd.DataFrame(results)

def run_phase_3(risk_df, is_collapsing):
    orders = []
    if is_collapsing:
        for _, row in risk_df.iterrows():
            orders.append({
                '股票代码': row['股票代码'], '股票名称': row['股票名称'],
                '警报级别': '🚨 清仓级', '执行动作': '清仓离场 (板块崩塌)', '目标仓位': '0%'
            })
        return pd.DataFrame(orders)
        
    for _, row in risk_df.iterrows():
        score = row['风险得分 (0-5)']
        if score >= 3:
            level, action, pos = "🔴 危险级", "立刻减仓锁定利润", "50%"
        elif score >= 1:
            level, action, pos = "🟠 警告级", "停止加仓，密切观察", "维持现有仓位"
        else:
            level, action, pos = "🟢 安全级", "正常持有", "维持现有仓位"
            
        orders.append({
            '股票代码': row['股票代码'], '股票名称': row['股票名称'], '风险得分': score, 
            '警报级别': level, '执行动作': action, '目标仓位': pos
        })
    return pd.DataFrame(orders)

# ================= UI 渲染 =================
tab1, tab2, tab3 = st.tabs(["📊 阶段一：选股建仓", "🕵️‍♂️ 阶段二：日常监控", "⚡ 阶段三：行动准则"])

with tab1:
    st.subheader("核心指标：寻找上涨平稳、波动率低的健康标的")
    phase1_df = run_phase_1(data_dict)
    st.dataframe(phase1_df.style.format({'20日动量': '{:.2%}', '20日波动率': '{:.4f}', '风险调整后动量 (RAM)': '{:.4f}'}), use_container_width=True)

with tab2:
    st.subheader("高危预警系统：5大维度透视资金结构")
    phase2_df = run_phase_2(data_dict, phase1_df['股票代码'].tolist())
    st.dataframe(phase2_df, use_container_width=True)
    st.caption("注：红色圆点 🔴 代表该指标触及警戒阈值，绿色 🟢 代表处于健康状态。")

with tab3:
    st.subheader("仓位管理：基于水温严格执行纪律")
    if is_sector_collapsing:
        st.error("【全局警报】检测到板块内部轮动崩塌（龙头滞涨，边缘股补涨）！资金已无法推高龙头。")
    phase3_df = run_phase_3(phase2_df, is_sector_collapsing)
    st.dataframe(phase3_df, use_container_width=True)
