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
WATCHLIST_FILE = "watchlist.json"
CUSTOM_NAMES_FILE = "custom_names.json"
DEFAULT_WATCHLIST = ["NVDA", "AAPL", "600519.SS", "002594.SZ", "TSLA", "AMD", "0700.HK", "SPY"]

def robust_load_json(file_path, default_val):
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            # 文件损坏，尝试读取备份
            bak_path = file_path + ".bak"
            if os.path.exists(bak_path):
                try:
                    with open(bak_path, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except: pass
    return default_val

def robust_save_json(file_path, data):
    tmp_path = file_path + ".tmp"
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        if os.path.exists(file_path):
            shutil.copy(file_path, file_path + ".bak") # 备份老文件
        os.replace(tmp_path, file_path) # 原子替换
    except Exception as e:
        st.sidebar.error(f"本地存储异常: {e}")

# 重试装饰器
def retry_on_exception(retries=3, delay=1):
    def decorator(func):
        def wrapper(*args, **kwargs):
            for i in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if i == retries - 1:
                        return pd.DataFrame()
                    time.sleep(delay)
            return pd.DataFrame()
        return wrapper
    return decorator

# 初始化 Session
if 'watchlist' not in st.session_state: st.session_state.watchlist = robust_load_json(WATCHLIST_FILE, DEFAULT_WATCHLIST)
if 'custom_names' not in st.session_state: st.session_state.custom_names = robust_load_json(CUSTOM_NAMES_FILE, {})

def get_stock_name(ticker): return st.session_state.custom_names.get(ticker, ticker)

# ================= 2. 页面与侧边栏动态参数 =================
st.set_page_config(page_title="全天候量化逃顶系统 v2.0", layout="wide")
st.title("📈 强势股逃顶择时量化系统 v2.0")

st.sidebar.header("⚙️ 动态参数与引擎设置")

# 时间周期设置
interval_option = st.sidebar.selectbox("K线级别", ["1d (日线)", "60m (小时线)", "30m (半小时)"])
interval_map = {"1d (日线)": "1d", "60m (小时线)": "60m", "30m (半小时)": "30m"}
interval = interval_map[interval_option]

lookback_days = st.sidebar.slider("拉取回溯天数 (美股分钟级限730天)", 100, 730, 400)

# 动态阈值设置
st.sidebar.subheader("指标阈值调优")
param_window = st.sidebar.number_input("动量/波动计算周期", 10, 60, 20)
param_crowd_pct = st.sidebar.slider("极端拥挤度分位数阈值", 0.80, 0.99, 0.90)
param_vol_ratio = st.sidebar.slider("短期波动异常放大倍数", 1.2, 3.0, 1.5)

benchmark_ticker = st.sidebar.selectbox("选择对标大盘基准", ["SPY (标普500)", "000300.SS (沪深300)", "^HSI (恒生指数)"])
bm_map = {"SPY (标普500)": "SPY", "000300.SS (沪深300)": "000300.SS", "^HSI (恒生指数)": "^HSI"}
benchmark = bm_map[benchmark_ticker]

st.sidebar.markdown("---")
st.sidebar.header("📁 自选池管理")
new_stock = st.sidebar.text_input("➕ 添加自选 (标准代码):", placeholder="例如: AAPL, 600519.SS")
if st.sidebar.button("添加", use_container_width=True):
    if new_stock and new_stock not in st.session_state.watchlist:
        st.session_state.watchlist.append(new_stock.strip().upper())
        robust_save_json(WATCHLIST_FILE, st.session_state.watchlist)
        st.rerun()

to_remove = st.sidebar.multiselect("➖ 移除自选:", st.session_state.watchlist)
if st.sidebar.button("确认移除", use_container_width=True) and to_remove:
    st.session_state.watchlist = [s for s in st.session_state.watchlist if s not in to_remove]
    robust_save_json(WATCHLIST_FILE, st.session_state.watchlist)
    st.rerun()

tickers = list(dict.fromkeys(st.session_state.watchlist + [benchmark]))

# ================= 3. 异步数据拉取与清洗引擎 =================
@retry_on_exception(retries=3)
def fetch_single_ticker(ticker, start, end, inv):
    df = yf.download(ticker, start=start, end=end, interval=inv, progress=False)
    if df.empty or len(df) < param_window + 5: return ticker, pd.DataFrame()
    
    # 扁平化多层索引 (yfinance最新版本特性)
    if isinstance(df.columns, pd.MultiIndex):
        df = pd.DataFrame({'Open': df['Open'][ticker], 'High': df['High'][ticker], 'Low': df['Low'][ticker], 'Close': df['Close'][ticker], 'Volume': df['Volume'][ticker]})
    else:
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        
    # 核心清洗：剔除全天停牌或一字涨跌停的数据（最高价==最低价），避免波动率和成交量失真
    df = df[df['High'] != df['Low']]
    return ticker, df

@st.cache_data(ttl=900) # 15分钟缓存
def fetch_all_data(tickers_list, days, inv):
    end_date = datetime.date.today() + datetime.timedelta(days=1)
    start_date = end_date - datetime.timedelta(days=days)
    data_dict = {}
    
    with st.spinner('🚀 正在启用线程池异步并发拉取行情...'):
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(fetch_single_ticker, t, start_date, end_date, inv) for t in tickers_list]
            for future in as_completed(futures):
                ticker, df = future.result()
                if not df.empty: data_dict[ticker] = df
    return data_dict

data_dict = fetch_all_data(tickers, lookback_days, interval)
if not data_dict:
    st.error("无法获取数据，请检查网络或回溯天数。")
    st.stop()

# ================= 4. 核心计算模块 =================
# 全局变量，用于存储基准收益率序列
bm_returns = data_dict[benchmark]['Close'].pct_change() if benchmark in data_dict else None

def run_phase_1(data_dict, window):
    results = []
    for ticker, df in data_dict.items():
        if ticker == benchmark: continue
        df = df.copy()
        df['MOM'] = df['Close'] / df['Close'].shift(window) - 1
        df['daily_ret'] = df['Close'].pct_change()
        df['vol'] = df['daily_ret'].rolling(window=window).std()
        df['RAM'] = np.where(df['vol'] > 0, df['MOM'] / df['vol'], np.nan)
        
        # 对标基准相对强度 (RS) = 个股动量 - 大盘动量
        rs_score = "N/A"
        if bm_returns is not None:
            bm_mom = bm_returns.rolling(window).sum().iloc[-1]
            rs_score = df['MOM'].iloc[-1] - bm_mom
            
        latest = df.iloc[-1]
        results.append({
            '代码': ticker, '名称': get_stock_name(ticker), '收盘价': latest['Close'],
            f'{window}日动量': latest['MOM'], f'{window}日波动': latest['vol'],
            '超额强度(RS)': rs_score, '风险调整动量(RAM)': latest['RAM']
        })
    return pd.DataFrame(results).sort_values(by='风险调整动量(RAM)', ascending=False).reset_index(drop=True)

def run_phase_2(data_dict, selected_tickers, window):
    results, raw_signals = [], {}
    for ticker in selected_tickers:
        if ticker not in data_dict: continue
        df = data_dict[ticker].copy()
        df['ret'] = df['Close'].pct_change()
        df['5d_ret'] = df['Close'].pct_change(5)
        
        # 指标1: 加速
        acc = (df['5d_ret'] - df['5d_ret'].rolling(window).mean()).iloc[-1]
        w_acc = acc > 0.05 
        
        # 指标2: 拥挤度 (区分量化/散户特征)
        vol_pct = df['Volume'].rolling(window*5).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x)>0 else np.nan).iloc[-1]
        w_crowd = vol_pct > param_crowd_pct
        
        # 指标3: 波动放大
        vol_ratio = (df['ret'].rolling(5).std() / (df['ret'].rolling(window).std() + 1e-9)).iloc[-1]
        w_vol = vol_ratio > param_vol_ratio
        
        # 指标4: 弱收盘
        range_p = df['High'] - df['Low']
        df['CloseStr'] = np.where(range_p > 0, (df['Close'] - df['Low']) / range_p, 0.5)
        str_3d = df['CloseStr'].rolling(3).mean().iloc[-1]
        w_str = str_3d < 0.4
        
        # 指标5: 量价背离 (致命信号权重为2)
        is_surge = df['Volume'].iloc[-1] > (2 * df['Volume'].rolling(window).mean().iloc[-1])
        w_diverge = is_surge and str_3d < 0.5
        
        # 散户 vs 量化特征判断 (仅作参考展示)
        crowd_type = "未极度拥挤"
        if w_crowd:
            tail_shadow = (df['High'].iloc[-1] - max(df['Open'].iloc[-1], df['Close'].iloc[-1])) / df['Close'].iloc[-1]
            if tail_shadow > 0.03: crowd_type = "量化高频洗盘 🤖"
            elif str_3d > 0.6: crowd_type = "散户追高合力 🙋‍♂️"
            else: crowd_type = "主力派发 📉"

        # 加权打分
        score = (w_diverge * 2) + (w_str * 1.5) + (w_acc * 1) + (w_crowd * 1) + (w_vol * 1)
        
        raw_signals[ticker] = df 
        results.append({
            '代码': ticker, '名称': get_stock_name(ticker), 
            '加速率': f"{acc:.3f}", '拥挤特征': crowd_type, '波动比': f"{vol_ratio:.2f}",
            '背离/破位': "🔴 致命" if w_diverge else "🟢 正常",
            '加权风险(满分6.5)': score
        })
    return pd.DataFrame(results), raw_signals

# ================= 5. UI 呈现 =================
tab1, tab2, tab3, tab4 = st.tabs(["📊 阶段一：选股与超额", "🕵️ 阶段二：加权预警", "⚡ 阶段三：执行与崩塌监测", "⏱️ 信号回溯测算"])

with tab1:
    st.subheader("核心指标：寻找平稳高动量、具有大盘超额收益的标的")
    phase1_df = run_phase_1(data_dict, param_window)
    st.dataframe(phase1_df.style.format({f'{param_window}日动量': '{:.2%}', f'{param_window}日波动': '{:.4f}', '超额强度(RS)': '{:.4f}', '风险调整动量(RAM)': '{:.4f}'}), use_container_width=True)

with tab2:
    st.subheader(f"多维高危预警与资金属性分析 (周期: {interval})")
    phase2_df, raw_dfs = run_phase_2(data_dict, phase1_df['代码'].tolist(), param_window)
    st.dataframe(phase2_df.style.background_gradient(subset=['加权风险(满分6.5)'], cmap='Reds'), use_container_width=True)
    
with tab3:
    st.subheader("全局监控与仓位指令")
    
    # 自动感知板块崩塌：如果平均加权风险 > 3.0，判定为板块高危
    avg_risk = phase2_df['加权风险(满分6.5)'].mean() if not phase2_df.empty else 0
    auto_collapse = avg_risk > 3.0
    
    col1, col2 = st.columns([1, 2])
    col1.metric("当前股票池平均危险指数", f"{avg_risk:.2f} / 6.5")
    if auto_collapse:
        col2.error("🚨 【系统自动判定：触发全局清仓警报】股票池整体热度崩塌，请无差别降仓！")
    else:
        col2.success("✅ 【系统状态正常】未监测到大面积资金出逃，依据个股信号操作。")

    orders = []
    for _, row in phase2_df.iterrows():
        if auto_collapse:
            level, action, pos = "🚨 崩塌清仓", "清仓离场", "0%"
        else:
            score = row['加权风险(满分6.5)']
            if score >= 4.0: level, action, pos = "🔴 致命危险", "立刻减仓 80%", "20%"
            elif score >= 2.5: level, action, pos = "🟠 高度警告", "减仓锁定利润", "50%"
            elif score >= 1.0: level, action, pos = "🟡 温和预警", "停止加仓，收紧止损", "维持"
            else: level, action, pos = "🟢 安全趋势", "正常持有", "维持满仓"
        orders.append({'代码': row['代码'], '名称': row['名称'], '加权风险': row['加权风险(满分6.5)'], '级别': level, '动作': action, '目标仓位': pos})
    st.dataframe(pd.DataFrame(orders), use_container_width=True)

with tab4:
    st.subheader("简易胜率回溯测算 (历史红灯预警的后续表现)")
    st.markdown(f"统计过去 `{lookback_days}` 天内，标的触发 **致命背离预警** 后 5 个周期的涨跌情况。")
    if st.button("▶️ 开始回溯计算"):
        bt_results = []
        for ticker in phase1_df['代码'].tolist():
            if ticker not in raw_dfs: continue
            df_bt = raw_dfs[ticker].copy()
            vol_ma_bt = df_bt['Volume'].rolling(param_window).mean()
            # 找到所有的触发点
            signals = (df_bt['Volume'] > 2 * vol_ma_bt) & (df_bt['CloseStr'] < 0.5)
            signal_dates = df_bt.index[signals]
            
            for date in signal_dates:
                idx = df_bt.index.get_loc(date)
                if idx + 5 < len(df_bt): # 确保后面有5天的数据
                    price_at_signal = df_bt['Close'].iloc[idx]
                    price_after_5 = df_bt['Close'].iloc[idx + 5]
                    ret_5d = (price_after_5 / price_at_signal) - 1
                    bt_results.append({'代码': ticker, '信号日期': date.strftime("%Y-%m-%d"), '5周期后跌幅(避险成功率)': ret_5d})
                    
        if bt_results:
            bt_df = pd.DataFrame(bt_results)
            success_avoid = len(bt_df[bt_df['5周期后跌幅(避险成功率)'] < 0]) # 信号出现后真的跌了，说明避险成功
            st.metric("红点预警防守胜率 (发出信号后后续5周期确实下跌的比例)", f"{(success_avoid / len(bt_df)):.2%}", f"共触发 {len(bt_df)} 次历史信号")
            st.dataframe(bt_df.style.format({'5周期后跌幅(避险成功率)': '{:.2%}'}), use_container_width=True)
        else:
            st.info("在所选周期内，未找到触发极端背离预警的历史数据。")
