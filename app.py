import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import datetime
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 1. 基础配置与记忆模块 =================
st.set_page_config(page_title="短线量化逃顶系统 v3.0", layout="wide")

WATCHLIST_FILE = "watchlist_short.json"
DEFAULT_WATCHLIST = ["NVDA", "AAPL", "600519.SS", "002594.SZ", "TSLA", "AMD"]

# 行业板块映射 (用于真实板块联动判定)
SECTOR_MAP = {
    "NVDA": "半导体", "AMD": "半导体", "SMCI": "半导体",
    "AAPL": "消费电子", "TSLA": "新能源", "BYD": "新能源", "002594.SZ": "新能源",
    "600519.SS": "白酒", "000858.SZ": "白酒",
    "SPY": "大盘指数", "000300.SS": "大盘指数"
}

def robust_load_json(file_path, default_val):
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f: return json.load(f)
        except: pass
    return default_val

def robust_save_json(file_path, data):
    tmp_path = file_path + ".tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False)
    if os.path.exists(file_path): shutil.copy(file_path, file_path + ".bak")
    os.replace(tmp_path, file_path)

if 'watchlist' not in st.session_state: 
    st.session_state.watchlist = robust_load_json(WATCHLIST_FILE, DEFAULT_WATCHLIST)

# ================= 2. 侧边栏：短线参数引擎 =================
st.sidebar.header("⚙️ 短线战法参数")
interval = st.sidebar.selectbox("K线级别", ["1d", "60m", "30m"], index=0)
lookback_days = st.sidebar.slider("拉取回溯天数", 100, 400, 250)

st.sidebar.subheader("动态自适应阈值")
param_window = st.sidebar.number_input("短线动量/波动周期", 5, 20, 10, help="短线建议缩短至10天或5天")
percentile_threshold = st.sidebar.slider("极端历史分位数触发线", 0.80, 0.99, 0.95, help="指标超过历史自身多少分位数才算极值")

benchmark = st.sidebar.selectbox("选择对标基准", ["SPY", "000300.SS", "^HSI"])

st.sidebar.markdown("---")
new_stock = st.sidebar.text_input("➕ 添加短线自选 (标准代码):")
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

tickers = list(dict.fromkeys(st.session_state.watchlist + [benchmark]))

# ================= 3. 异步获取数据 (解决复权与换手率痛点) =================
def fetch_single_ticker(ticker, start, end, inv):
    df = yf.download(ticker, start=start, end=end, interval=inv, progress=False)
    if df.empty or len(df) < param_window + 10: return ticker, pd.DataFrame(), None
    
    # 彻底解决复权陷阱：优先使用 Adj Close
    if isinstance(df.columns, pd.MultiIndex):
        close_series = df['Adj Close'][ticker] if 'Adj Close' in df else df['Close'][ticker]
        df = pd.DataFrame({'Open': df['Open'][ticker], 'High': df['High'][ticker], 'Low': df['Low'][ticker], 'Close': close_series, 'Volume': df['Volume'][ticker]})
    else:
        close_series = df['Adj Close'] if 'Adj Close' in df.columns else df['Close']
        df = df[['Open', 'High', 'Low', 'Volume']].copy()
        df['Close'] = close_series
        
    df = df[df['High'] != df['Low']] # 过滤一字涨跌停与停牌
    
    # 尝试获取真实总股本计算换手率
    shares = None
    try:
        shares = yf.Ticker(ticker).fast_info.get('shares')
    except: pass
    
    return ticker, df, shares

@st.cache_data(ttl=600) # 缓存10分钟，适应短线盯盘
def fetch_all_data(tickers_list, days, inv):
    end_date = datetime.date.today() + datetime.timedelta(days=1)
    start_date = end_date - datetime.timedelta(days=days)
    data_dict, shares_dict = {}, {}
    with st.spinner('🚀 极速并发拉取修复复权与换手率数据...'):
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(fetch_single_ticker, t, start_date, end_date, inv) for t in tickers_list]
            for future in as_completed(futures):
                ticker, df, shares = future.result()
                if not df.empty: 
                    data_dict[ticker] = df
                    shares_dict[ticker] = shares
    return data_dict, shares_dict

data_dict, shares_dict = fetch_all_data(tickers, lookback_days, interval)
if not data_dict: st.stop()
bm_returns = data_dict[benchmark]['Close'].pct_change() if benchmark in data_dict else None

# ================= 4. 核心逻辑：不对称RAM与一票否决 =================
def run_phase_1(data_dict, window):
    results = []
    for ticker, df in data_dict.items():
        if ticker == benchmark: continue
        df = df.copy()
        
        # 短线只看近期动量
        df['MOM'] = df['Close'] / df['Close'].shift(window) - 1
        df['daily_ret'] = df['Close'].pct_change()
        
        # 【核心修正】：不对称下行风险 (Downside Deviation)
        # 向上猛涨的阳线波动不计入惩罚，只惩罚向下的振幅
        downside_ret = df['daily_ret'].copy()
        downside_ret[downside_ret > 0] = 0 
        df['downside_vol'] = downside_ret.rolling(window=window).std()
        
        # 短线不对称风险动量 (Short-term Sortino RAM)
        df['RAM_Short'] = np.where(df['downside_vol'] > 0, df['MOM'] / df['downside_vol'], np.nan)
            
        latest = df.iloc[-1]
        sector = SECTOR_MAP.get(ticker, "独立标的")
        
        results.append({
            '代码': ticker, '所属板块': sector, '最新价': latest['Close'],
            f'{window}期涨幅': f"{latest['MOM']:.2%}", '下行惩罚风险': f"{latest['downside_vol']:.4f}",
            '短线风险动量(RAM)': round(latest['RAM_Short'], 2)
        })
    return pd.DataFrame(results).sort_values(by='短线风险动量(RAM)', ascending=False).reset_index(drop=True)

def run_phase_2_and_3(data_dict, shares_dict, selected_tickers, window, pct_thresh):
    results, orders = [], []
    sector_risk = {} # 用于计算真实板块崩塌
    
    for ticker in selected_tickers:
        if ticker not in data_dict: continue
        df = data_dict[ticker].copy()
        shares = shares_dict.get(ticker)
        
        df['ret'] = df['Close'].pct_change()
        df['3d_ret'] = df['Close'].pct_change(3) # 短线看3天加速
        
        # 1. 动态自适应加速预警
        df['acc'] = df['3d_ret'] - df['3d_ret'].rolling(window*2).mean()
        acc_latest = df['acc'].iloc[-1]
        acc_limit = df['acc'].rolling(100).quantile(pct_thresh).iloc[-1] # 突破历史N%分位
        w_acc = acc_latest > acc_limit and acc_latest > 0
        
        # 2. 真实换手率 / 动态成交量极值
        if shares:
            df['turnover'] = df['Volume'] / shares
            crowd_metric = df['turnover']
            metric_name = "真实换手率"
        else:
            crowd_metric = df['Volume']
            metric_name = "绝对成交量"
            
        crowd_latest = crowd_metric.iloc[-1]
        crowd_limit = crowd_metric.rolling(100).quantile(pct_thresh).iloc[-1]
        w_crowd = crowd_latest > crowd_limit
        
        # 3. 动态波动率放大
        vol_ratio = (df['ret'].rolling(3).std() / (df['ret'].rolling(window).std() + 1e-9)).iloc[-1]
        vol_limit = (df['ret'].rolling(3).std() / (df['ret'].rolling(window).std() + 1e-9)).rolling(100).quantile(pct_thresh).iloc[-1]
        w_vol = vol_ratio > vol_limit
        
        # 4. 收盘强度与一票否决
        range_p = df['High'] - df['Low']
        str_latest = np.where(range_p > 0, (df['Close'] - df['Low']) / range_p, 0.5)[-1]
        
        # 【一票否决系统 (Veto)】：史诗级天量 (突破99%分位) + 收盘价在底部 (< 0.3) -> 主力核按钮出货
        fatal_crowd_limit = crowd_metric.rolling(100).quantile(0.99).iloc[-1]
        is_fatal_veto = (crowd_latest > fatal_crowd_limit) and (str_latest < 0.3)
        
        score = w_acc + w_crowd + w_vol + (str_latest < 0.4)
        
        # 判定短线级别
        if is_fatal_veto:
            level, action, pos = "☠️ 一票否决", "无条件清仓核按钮", "0%"
        elif score >= 3:
            level, action, pos = "🔴 危险赶顶", "立刻大幅减仓", "轻仓"
        elif score >= 1:
            level, action, pos = "🟡 异动警告", "停止买入，随跌止盈", "半仓"
        else:
            level, action, pos = "🟢 趋势健康", "正常持股", "满仓"
            
        sector = SECTOR_MAP.get(ticker, "独立标的")
        if sector not in sector_risk: sector_risk[sector] = []
        sector_risk[sector].append(score)
        
        results.append({
            '代码': ticker, '拥挤度类型': metric_name, 
            '动态加速异动': "🔴 破极值" if w_acc else "🟢 正常",
            '动态拥挤极值': "🔴 破极值" if w_crowd else "🟢 正常",
            '收盘强度': f"{str_latest:.2f} {'🔴' if str_latest < 0.4 else '🟢'}",
            '综合短线风险': "☠️ 致命背离" if is_fatal_veto else f"{score} / 4"
        })
        orders.append({'代码': ticker, '所属板块': sector, '综合风险': '☠️ VETO' if is_fatal_veto else score, '短线指令': level, '执行动作': action})

    return pd.DataFrame(results), pd.DataFrame(orders), sector_risk

# ================= 5. UI 呈现 =================
st.title("⚡ 短线龙虎榜：动量追随与极值逃顶")

tab1, tab2 = st.tabs(["🚀 第一阶段：短线上攻榜 (不对称RAM)", "⚔️ 第二/三阶段：极值预警与执行"])

with tab1:
    st.subheader(f"短线爆发力与抗回撤筛选 (计算周期: {param_window})")
    st.markdown("> **改良逻辑**：只惩罚下跌的波动，完全放行连板和向上跳空，找出回撤极小的纯粹主升浪。")
    phase1_df = run_phase_1(data_dict, param_window)
    st.dataframe(phase1_df, use_container_width=True)

with tab2:
    st.subheader(f"自适应动态极值预警 (极值线: 历史 {percentile_threshold*100}%)")
    st.markdown("> **改良逻辑**：废除拍脑袋的固定阈值。只有当个股短线加速、换手率突破**它自己过去一年的历史极值**时，才会亮红灯。")
    
    phase2_df, orders_df, sector_risk = run_phase_2_and_3(data_dict, shares_dict, phase1_df['代码'].tolist(), param_window, percentile_threshold)
    st.dataframe(phase2_df, use_container_width=True)
    
    st.markdown("### 🚨 短线纪律与板块崩塌监测")
    col1, col2 = st.columns(2)
    with col1:
        st.write("**真实板块联动监测：**")
        for sec, risks in sector_risk.items():
            avg_sec_risk = np.mean(risks)
            if avg_sec_risk >= 2.5 and sec != "独立标的":
                st.error(f"🔥 【{sec}】板块平均危险度极高 ({avg_sec_risk:.1f}/4)，板块资金正集体出逃！")
            else:
                st.success(f"✅ 【{sec}】板块情绪尚可。")
                
    with col2:
        st.write("**个股短线操作指令表：**")
        st.dataframe(orders_df, use_container_width=True)
