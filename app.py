import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import datetime
import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 1. 强健的工程模块：JSON备份与重试装饰器 =================
WATCHLIST_FILE = "watchlist_short.json"
CUSTOM_NAMES_FILE = "custom_names.json"
DEFAULT_WATCHLIST = ["NVDA", "AAPL", "600519.SS", "002594.SZ", "TSLA", "AMD"]

def robust_load_json(file_path, default_val):
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f: return json.load(f)
        except Exception:
            bak_path = file_path + ".bak"
            if os.path.exists(bak_path):
                try:
                    with open(bak_path, 'r', encoding='utf-8') as f: return json.load(f)
                except: pass
    return default_val

def robust_save_json(file_path, data):
    tmp_path = file_path + ".tmp"
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False)
        if os.path.exists(file_path): shutil.copy(file_path, file_path + ".bak")
        os.replace(tmp_path, file_path)
    except Exception as e: st.sidebar.error(f"本地存储异常: {e}")

# 重试装饰器
def retry_on_exception(retries=3, delay=1):
    def decorator(func):
        def wrapper(*args, **kwargs):
            for i in range(retries):
                try: return func(*args, **kwargs)
                except: 
                    if i == retries - 1: return None, pd.DataFrame(), None
                    time.sleep(delay)
            return None, pd.DataFrame(), None
        return wrapper
    return decorator

# 初始化 Session
if 'watchlist' not in st.session_state: st.session_state.watchlist = robust_load_json(WATCHLIST_FILE, DEFAULT_WATCHLIST)
if 'custom_names' not in st.session_state: st.session_state.custom_names = robust_load_json(CUSTOM_NAMES_FILE, {})

def get_stock_name(ticker): return st.session_state.custom_names.get(ticker, ticker)

# ================= 2. 页面与侧边栏参数引擎 =================
st.set_page_config(page_title="短线逃顶系统", layout="wide")
st.title("📈 短线量化逃顶与板块联动监控")

st.sidebar.header("⚙️ 战法参数调整")
param_window = st.sidebar.number_input("动量计算窗口", 5, 30, 10)
percentile_threshold = st.sidebar.slider("极端极值阈值", 0.80, 0.99, 0.95)

st.sidebar.markdown("---")
st.sidebar.header("📁 自选池记忆模块")
new_stock = st.sidebar.text_input("➕ 添加自选:", placeholder="代码如: AAPL 或 000063.SZ")
if st.sidebar.button("添加", use_container_width=True) and new_stock:
    if new_stock not in st.session_state.watchlist:
        st.session_state.watchlist.append(new_stock.strip().upper())
        robust_save_json(WATCHLIST_FILE, st.session_state.watchlist)
        st.rerun()

to_remove = st.sidebar.multiselect("➖ 移除自选:", st.session_state.watchlist)
if st.sidebar.button("确认移除", use_container_width=True) and to_remove:
    st.session_state.watchlist = [s for s in st.session_state.watchlist if s not in to_remove]
    robust_save_json(WATCHLIST_FILE, st.session_state.watchlist)
    st.rerun()

# ================= 3. 数据处理引擎 =================
@retry_on_exception(retries=3)
def fetch_single_ticker(ticker):
    df = yf.download(ticker, period="1y", interval="1d", progress=False)
    if df.empty or len(df) < 30: return ticker, pd.DataFrame(), None
    
    # 统一使用前复权价格
    df['Close'] = df['Adj Close'] if 'Adj Close' in df else df['Close']
    df = df[df['High'] != df['Low']] # 过滤无效停牌
    shares = yf.Ticker(ticker).fast_info.get('shares', 1e9)
    return ticker, df, shares

@st.cache_data(ttl=600)
def fetch_all(tickers):
    data_dict, shares_dict = {}, {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fetch_single_ticker, t): t for t in tickers}
        for f in as_completed(futures):
            t, df, s = f.result()
            if not df.empty:
                data_dict[t] = df
                shares_dict[t] = s
    return data_dict, shares_dict

data_dict, shares_dict = fetch_all(st.session_state.watchlist)

# ================= 4. 逻辑模块 =================
def run_analytics(data_dict, shares_dict):
    results = []
    for ticker, df in data_dict.items():
        # 1. 不对称RAM (下行惩罚)
        ret = df['Close'].pct_change()
        mom = df['Close'] / df['Close'].shift(param_window) - 1
        downside = ret.copy()
        downside[downside > 0] = 0
        ram = (mom.iloc[-1] / (downside.rolling(param_window).std().iloc[-1] + 1e-9))
        
        # 2. 短线预警计算
        turnover = df['Volume'].iloc[-1] / shares_dict.get(ticker, 1e9)
        crowd_limit = df['Volume'].rolling(100).quantile(percentile_threshold).iloc[-1]
        
        # 加权风险得分
        w_crowd = df['Volume'].iloc[-1] > crowd_limit
        str_3d = ((df['Close']-df['Low'])/(df['High']-df['Low'])).rolling(3).mean().iloc[-1]
        
        score = (w_crowd * 2) + (str_3d < 0.4) * 2
        
        results.append({
            '股票代码': ticker, '股票名称': get_stock_name(ticker),
            '风险调整动量(RAM)': round(ram, 2),
            '拥挤程度': "🔴 高危" if w_crowd else "🟢 健康",
            '收盘强度': f"{str_3d:.2f} {'🔴' if str_3d < 0.4 else '🟢'}",
            '综合风险得分': score
        })
    return pd.DataFrame(results)

# ================= 5. 主程序渲染 =================
results_df = run_analytics(data_dict, shares_dict)

st.subheader("📊 短线量化监控台 (双击股票名称可修改)")
# 人性化：支持直接编辑表格名称并持久化保存
editor = st.data_editor(results_df, use_container_width=True, hide_index=True)

# 处理名称修改
for idx, row in editor.iterrows():
    if row['股票名称'] != results_df.at[idx, '股票名称']:
        st.session_state.custom_names[row['股票代码']] = row['股票名称']
        robust_save_json(CUSTOM_NAMES_FILE, st.session_state.custom_names)
        st.rerun()

st.markdown("---")
st.subheader("⚡ 交易执行纪律")
st.dataframe(editor[['股票名称', '综合风险得分']].assign(建议=lambda x: np.where(x['综合风险得分'] >= 3, '🚨 立刻减仓', '✅ 正常持有')), use_container_width=True)
