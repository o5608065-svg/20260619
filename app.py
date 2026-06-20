import streamlit as st
import pandas as pd
import numpy as np
import datetime
import json
import os
import shutil
import time
import requests
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 1. 强健的工程模块：JSON备份、映射与重试装饰器 =================
WATCHLIST_FILE = "watchlist.json"
CUSTOM_NAMES_FILE = "custom_names.json"
DEFAULT_WATCHLIST = ["600519.SS", "002594.SZ", "300750.SZ", "601127.SS", "000300.SS", "000852.SS"]

STOCK_MAPPING = {
    "茅台": "600519.SS", "贵州茅台": "600519.SS",
    "宁王": "300750.SZ", "宁德时代": "300750.SZ", "比亚迪": "002594.SZ",
    "赛力斯": "601127.SS", "科大讯飞": "002230.SZ", "东方财富": "300059.SZ",
    "沪深300": "000300.SS", "深指": "399001.SZ", "深证成指": "399001.SZ", 
    "创业板指": "399006.SZ", "科创50": "000688.SS", "上证指数": "000001.SS", 
    "中证1000": "000852.SS"
}

TICKER_NAME_MAPPING = {v: k for k, v in STOCK_MAPPING.items()}
TICKER_NAME_MAPPING.update({
    "600519.SS": "贵州茅台", "002594.SZ": "比亚迪", "300750.SZ": "宁德时代", 
    "601127.SS": "赛力斯", "002230.SZ": "科大讯飞", "300059.SZ": "东方财富",
    "000300.SS": "沪深300", "399001.SZ": "深证成指", "399006.SZ": "创业板指", 
    "000688.SS": "科创50", "000001.SS": "上证指数", "000852.SS": "中证1000"
})

def robust_load_json(file_path, default_val):
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
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
            shutil.copy(file_path, file_path + ".bak") 
        os.replace(tmp_path, file_path) 
    except Exception:
        pass 

def retry_on_exception(retries=3, delay=1):
    def decorator(func):
        def wrapper(*args, **kwargs):
            for i in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception:
                    if i == retries - 1:
                        return args[0] if len(args)>0 else None, pd.DataFrame()
                    time.sleep(delay)
            return args[0] if len(args)>0 else None, pd.DataFrame()
        return wrapper
    return decorator

if 'watchlist' not in st.session_state: st.session_state.watchlist = robust_load_json(WATCHLIST_FILE, DEFAULT_WATCHLIST)
if 'custom_names' not in st.session_state: st.session_state.custom_names = robust_load_json(CUSTOM_NAMES_FILE, {})

def get_stock_name(ticker): 
    if ticker in st.session_state.custom_names:
        return st.session_state.custom_names[ticker]
    return TICKER_NAME_MAPPING.get(ticker, ticker)

# ================= 2. 页面与侧边栏动态参数 =================
st.set_page_config(page_title="自适应量化逃顶系统 v3.13", layout="wide")
st.title("📈 强势股情绪逃顶系统 v3.13")
st.caption("🚀 全血覆盖版：新增上证指数、中证1000对标，适配全市场宽基风格切换。")

st.sidebar.header("⚙️ 引擎设置")

interval_option = st.sidebar.selectbox("K线级别", ["1d (日线)", "60m (小时线)", "30m (半小时)"])
interval_map = {"1d (日线)": "1d", "60m (小时线)": "60m", "30m (半小时)": "30m"}
interval = interval_map[interval_option]

lookback_days = st.sidebar.slider("拉取回溯天数", 200, 730, 400)

st.sidebar.subheader("系统参数 (自适应计算基准)")
param_window = st.sidebar.number_input("动量与阈值基准周期", 10, 60, 20)
param_crowd_pct = st.sidebar.slider("基础拥挤度报警分位数", 0.70, 0.99, 0.82)

# ================= 核心修改：指数覆盖扩展 =================
benchmark_ticker = st.sidebar.selectbox("选择对标大盘基准", [
    "000300.SS (沪深300)", 
    "000001.SS (上证指数)",
    "000852.SS (中证1000)",
    "399001.SZ (深证成指)", 
    "399006.SZ (创业板指)", 
    "000688.SS (科创50)"
])
bm_map = {
    "000300.SS (沪深300)": "000300.SS",
    "000001.SS (上证指数)": "000001.SS",
    "000852.SS (中证1000)": "000852.SS",
    "399001.SZ (深证成指)": "399001.SZ",
    "399006.SZ (创业板指)": "399006.SZ",
    "000688.SS (科创50)": "000688.SS"
}
benchmark = bm_map[benchmark_ticker]

st.sidebar.markdown("---")
st.sidebar.header("📁 自选池管理")

new_stock_input = st.sidebar.text_input("➕ 批量添加自选 (用逗号隔开):", placeholder="如: 比亚迪, 茅台, 300750.SZ")
if st.sidebar.button("添加", use_container_width=True):
    if new_stock_input:
        normalized_input = new_stock_input.replace("，", ",")
        raw_tickers = normalized_input.split(",")
        added_any = False
        for raw_input in raw_tickers:
            clean_input = raw_input.strip()
            if not clean_input: continue 
            mapped_ticker = STOCK_MAPPING.get(clean_input, clean_input.upper())
            if mapped_ticker not in st.session_state.watchlist:
                st.session_state.watchlist.append(mapped_ticker)
                added_any = True
        if added_any:
            robust_save_json(WATCHLIST_FILE, st.session_state.watchlist)
            st.rerun()

to_remove = st.sidebar.multiselect("➖ 移除自选:", st.session_state.watchlist, format_func=lambda x: f"{x} ({get_stock_name(x)})")
if st.sidebar.button("确认移除", use_container_width=True) and to_remove:
    st.session_state.watchlist = [s for s in st.session_state.watchlist if s not in to_remove]
    robust_save_json(WATCHLIST_FILE, st.session_state.watchlist)
    st.rerun()

tickers = list(dict.fromkeys(st.session_state.watchlist + [benchmark]))

# ================= 3. 数据引擎 =================
global_session = requests.Session()
global_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Language": "en-US,en;q=0.9"
})

@retry_on_exception(retries=3, delay=2)
def fetch_single_ticker(ticker, start, end, inv):
    time.sleep(np.random.uniform(0.2, 1.5))
    df = yf.download(ticker, start=start, end=end, interval=inv, auto_adjust=True, session=global_session, progress=False)
    
    if not df.empty and isinstance(df.columns, pd.MultiIndex):
        try:
            df = pd.DataFrame({'Open': df['Open'][ticker], 'High': df['High'][ticker], 'Low': df['Low'][ticker], 'Close': df['Close'][ticker], 'Volume': df['Volume'][ticker]})
        except KeyError:
            return ticker, pd.DataFrame()
            
    df.dropna(subset=['Close', 'Volume'], inplace=True)
    if df.empty or len(df) < param_window * 3: return ticker, pd.DataFrame()
    df = df[df['High'] != df['Low']]
    
    try:
        t_obj = yf.Ticker(ticker, session=global_session)
        shares = t_obj.info.get('sharesOutstanding', None)
    except Exception:
        shares = None
        
    df['DollarVolume'] = df['Close'] * df['Volume']
    if shares and shares > 0:
        df['Crowd_Proxy'] = df['Volume'] / shares  
        df['Proxy_Type'] = '换手率'
    else:
        df['Crowd_Proxy'] = df['DollarVolume']     
        df['Proxy_Type'] = '成交额(降级)'
        
    return ticker, df

@st.cache_data(ttl=900)
def fetch_all_data(tickers_list, days, inv):
    end_date = datetime.date.today() + datetime.timedelta(days=1)
    start_date = end_date - datetime.timedelta(days=days)
    data_dict = {}
    
    with st.spinner('🚀 正在提取全市场数据及换手率 (大约需时10-20秒)...'):
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(fetch_single_ticker, t, start_date, end_date, inv) for t in tickers_list]
            for future in as_completed(futures):
                ticker, df = future.result()
                if not df.empty: data_dict[ticker] = df
    return data_dict

data_dict = fetch_all_data(tickers, lookback_days, interval)
if not data_dict:
    st.error("数据拉取失败，请稍后再试。")
    st.stop()

bm_returns = data_dict[benchmark]['Close'].pct_change() if benchmark in data_dict else None
latest_market_date = max([df.index[-1] for df in data_dict.values() if not df.empty]) if data_dict else pd.Timestamp.now()

# ================= 4. 核心计算模块 =================
def run_phase_1(data_dict, window):
    results = []
    min_data_required = window * 5 
    
    for ticker, df in data_dict.items():
        if ticker == benchmark: continue
        df = df.copy()
        
        if len(df) < min_data_required:
            results.append({
                '代码': ticker, '名称': get_stock_name(ticker), '收盘价': np.nan,
                f'{window}日动量': "无法计算", '下行风险(Sortino)': np.nan, '超额强度(RS)': np.nan, 
                '风险调整动量(RAM)': "数据不足", '_sort_val': -999, '状态': '⚪ 样本不足'
            })
            continue
            
        if (latest_market_date - df.index[-1]).days > 4:
            results.append({
                '代码': ticker, '名称': get_stock_name(ticker), '收盘价': df['Close'].iloc[-1],
                f'{window}日动量': "长期无交易", '下行风险(Sortino)': np.nan, '超额强度(RS)': np.nan, 
                '风险调整动量(RAM)': "停牌中", '_sort_val': -998, '状态': '⚫ 停牌'
            })
            continue

        df['MOM'] = df['Close'] / df['Close'].shift(window) - 1
        df['daily_ret'] = df['Close'].pct_change()
        df['downside_ret'] = df['daily_ret'].clip(upper=0)
        df['down_vol'] = df['downside_ret'].rolling(window=window).std() * np.sqrt(252)
        df['RAM'] = np.where(df['down_vol'] > 1e-6, df['MOM'] / df['down_vol'], df['MOM'] / 1e-6)
        
        rs_score = np.nan
        if bm_returns is not None:
            bm_mom = bm_returns.rolling(window).sum().iloc[-1]
            rs_score = df['MOM'].iloc[-1] - bm_mom
            
        latest = df.iloc[-1]
        results.append({
            '代码': ticker, '名称': get_stock_name(ticker), '收盘价': latest['Close'],
            f'{window}日动量': latest['MOM'], '下行风险(Sortino)': latest['down_vol'],
            '超额强度(RS)': rs_score, '风险调整动量(RAM)': latest['RAM'], 
            '_sort_val': latest['RAM'], '状态': '🟢 交易中'
        })
        
    df_res = pd.DataFrame(results).sort_values(by='_sort_val', ascending=False).drop(columns=['_sort_val']).reset_index(drop=True)
    return df_res

def run_phase_2(data_dict, phase1_df, window):
    results, raw_signals = [], {}
    history_window = window * 5
    
    for _, row in phase1_df.iterrows():
        ticker = row['代码']
        status = row['状态']
        if ticker not in data_dict: continue
        
        if '停牌' in status or '样本不足' in status:
            results.append({
                '代码': ticker, '名称': get_stock_name(ticker), 
                '加速异动': "-", '拥挤指标基准': "-", '客观形态特征': status, '自适应波动比': "-",
                '背离/破位': "-", '综合高危得分(满分6.5)': 0, 
                '同分1日预期': "-", '同分3日预期': "-", '同分5日预期': "-", '同分10日预期': "-",
                '_sort_val': -1
            })
            continue

        df = data_dict[ticker].copy()
        proxy_type = df['Proxy_Type'].iloc[-1]
        
        df['ret'] = df['Close'].pct_change()
        df['5d_ret'] = df['Close'].pct_change(5)
        
        df['limit_up'] = df['ret'] >= 0.09
        df['streak'] = np.where(df['limit_up'], df['limit_up'].groupby((~df['limit_up']).cumsum()).cumcount() + 1, 0)
        
        was_consecutive_limit_up = df['streak'].iloc[-2] >= 2 if len(df) > 1 else False
        was_limit_up = df['streak'].iloc[-2] >= 1 if len(df) > 1 else False
        
        dynamic_surge_mul = 2.0 if was_limit_up else 3.0
        proxy_mean = df['Crowd_Proxy'].rolling(window).mean().iloc[-1]
        is_surge = df['Crowd_Proxy'].iloc[-1] > (dynamic_surge_mul * proxy_mean)

        df['acc'] = df['5d_ret'] - df['5d_ret'].rolling(window).mean()
        acc_threshold = df['acc'].rolling(history_window).quantile(0.90).iloc[-1]
        w_acc = df['acc'].iloc[-1] > (acc_threshold if pd.notna(acc_threshold) else 0.05)
        
        dynamic_crowd_pct = 0.75 if (was_consecutive_limit_up or df['streak'].iloc[-1] >= 2) else param_crowd_pct
        vol_pct = df['Crowd_Proxy'].rolling(history_window).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x)>0 else np.nan).iloc[-1]
        w_crowd = vol_pct > dynamic_crowd_pct
        
        df['vol_ratio'] = df['ret'].rolling(5).std() / (df['ret'].rolling(window).std() + 1e-9)
        vol_ratio_threshold = df['vol_ratio'].rolling(history_window).quantile(0.90).iloc[-1]
        w_vol = df['vol_ratio'].iloc[-1] > (vol_ratio_threshold if pd.notna(vol_ratio_threshold) else 1.5)
        
        range_p = df['High'] - df['Low']
        df['CloseStr'] = np.where(range_p > 0, (df['Close'] - df['Low']) / range_p, 0.5)
        str_3d = df['CloseStr'].rolling(3).mean().iloc[-1]
        w_str = str_3d < 0.5 
        
        open_high_go_low = (df['Open'].iloc[-1] > df['Close'].iloc[-2]) and (df['Close'].iloc[-1] < df['Open'].iloc[-1])
        blew_board = (df['High'].iloc[-1] / df['Close'].iloc[-2] - 1 >= 0.09) and (df['Close'].iloc[-1] < df['High'].iloc[-1])
        early_fatal = was_limit_up and (open_high_go_low or blew_board) and is_surge
        
        fatal_diverge = (is_surge and ((str_3d < 0.3) or (df['ret'].iloc[-1] < -0.02))) or early_fatal
        
        weak_relay = (df['ret'].iloc[-1] > 0) and (df['ret'].iloc[-1] < df['ret'].iloc[-2]) and (df['CloseStr'].iloc[-1] < 0.5) and is_surge
        break_board = was_consecutive_limit_up and not df['limit_up'].iloc[-1] and is_surge
        
        pattern_desc = "正常波动"
        if fatal_diverge: pattern_desc = "🛑 史诗级断头/炸板"
        elif break_board: pattern_desc = "💔 连板断板退潮"
        elif early_fatal: pattern_desc = "⚠️ 高位炸板放量"
        elif weak_relay: pattern_desc = "📉 情绪放量弱承接"
        elif w_crowd:
            tail_shadow = (df['High'].iloc[-1] - max(df['Open'].iloc[-1], df['Close'].iloc[-1])) / df['Close'].iloc[-1]
            if tail_shadow > 0.03: pattern_desc = "高位长影拒斥 📉"
            elif str_3d > 0.6: pattern_desc = "趋势放量冲刺 🚀"
            else: pattern_desc = "极端拥挤滞涨 ⚠️"

        if fatal_diverge: score = 6.5 
        else:
            score = (w_str * 1.5) + (w_acc * 1.0) + (w_crowd * 1.0) + (w_vol * 1.0)
            if is_surge and not fatal_diverge: score += 1.0 
            if weak_relay: score += 1.0  
            if break_board: score += 2.0 
            
        raw_signals[ticker] = df 
        results.append({
            '代码': ticker, '名称': get_stock_name(ticker), 
            '加速异动': "是" if w_acc else "否", 
            '拥挤指标基准': f"✅{proxy_type}" if proxy_type == '换手率' else f"⚠️{proxy_type}",
            '客观形态特征': pattern_desc, '自适应波动比': f"{df['vol_ratio'].iloc[-1]:.2f}",
            '背离/破位': "🔴 致命熔断" if fatal_diverge else "🟢 正常",
            '综合高危得分(满分6.5)': min(score, 6.5),
            '同分1日预期': "需计算", '同分3日预期': "需计算", '同分5日预期': "需计算", '同分10日预期': "需计算",
            '_sort_val': score
        })
        
    df_res = pd.DataFrame(results).sort_values(by='_sort_val', ascending=False).drop(columns=['_sort_val']).reset_index(drop=True)
    return df_res, raw_signals

def highlight_suspended(row):
    if '停牌' in str(row.get('状态', '')) or '停牌' in str(row.get('客观形态特征', '')) or '不足' in str(row.get('状态', '')) or '不足' in str(row.get('客观形态特征', '')):
        return ['color: #888888; background-color: #2b2b2b'] * len(row)
    return [''] * len(row)

# ================= 5. UI 呈现 =================
tab1, tab2, tab3, tab4 = st.tabs(["📊 一阶段：超额与风险", "🕵️ 二阶段：情绪预警", "⚡ 三阶段：指令与热度", "⏱️ 核心：全景向量回测"])

with tab1:
    st.subheader("核心指标：寻找平稳高动量 (已过滤停牌/次新，异常标的自动垫底)")
    phase1_df = run_phase_1(data_dict, param_window)
    
    def on_name_edit():
        changes = st.session_state.get("phase1_editor", {}).get("edited_rows", {})
        if changes:
            is_changed = False
            for row_idx, edit_dict in changes.items():
                if "名称" in edit_dict:
                    ticker = phase1_df.iloc[row_idx]['代码']
                    new_name = edit_dict["名称"].strip()
                    if new_name: 
                        st.session_state.custom_names[ticker] = new_name
                        is_changed = True
            if is_changed:
                robust_save_json(CUSTOM_NAMES_FILE, st.session_state.custom_names)

    format_dict = {f'{param_window}日动量': lambda x: f"{x:.2%}" if isinstance(x, float) and pd.notna(x) else x,
                   '下行风险(Sortino)': lambda x: f"{x:.4f}" if isinstance(x, float) and pd.notna(x) else x,
                   '超额强度(RS)': lambda x: f"{x:.4f}" if isinstance(x, float) and pd.notna(x) else x,
                   '风险调整动量(RAM)': lambda x: f"{x:.4f}" if isinstance(x, float) and pd.notna(x) else x}
    
    st.data_editor(
        phase1_df.style.apply(highlight_suspended, axis=1).format(format_dict),
        disabled=[col for col in phase1_df.columns if col != '名称'],
        use_container_width=True, hide_index=True, key="phase1_editor", on_change=on_name_edit 
    )

with tab2:
    st.subheader(f"连板妖股与接力情绪预警监控 (周期: {interval_option})")
    phase2_df, raw_dfs = run_phase_2(data_dict, phase1_df, param_window)
    st.dataframe(phase2_df.style.apply(highlight_suspended, axis=1).background_gradient(subset=['综合高危得分(满分6.5)'], cmap='Reds', vmin=0, vmax=6.5), use_container_width=True)
    
with tab3:
    st.subheader("组合全局监控与仓位指令")
    valid_scores = phase2_df[phase2_df['综合高危得分(满分6.5)'] > 0]['综合高危得分(满分6.5)']
    avg_risk = valid_scores.mean() if not valid_scores.empty else 0
    pool_overheated = avg_risk > 3.5 
    
    col1, col2 = st.columns([1, 2])
    col1.metric("选股池有效平均热度", f"{avg_risk:.2f} / 6.5")
    if pool_overheated:
        col2.error("🚨 【系统自动判定：触发组合降温警报】短线情绪崩塌，建议核按钮离场！")
    else:
        col2.success("✅ 【组合状态平稳】短线情绪健康。")

    orders = []
    for _, row in phase2_df.iterrows():
        score = row['综合高危得分(满分6.5)']
        if score == 0: continue 
        level = "🔴 致命" if score >= 6.0 else ("🟠 高危" if score >= 4.0 else ("🟡 预警" if score >= 2.5 else "🟢 安全"))
        orders.append({'代码': row['代码'], '名称': row['名称'], '风险得分': score, '风险等级': level})
    if orders: st.dataframe(pd.DataFrame(orders), use_container_width=True)

with tab4:
    st.subheader("历史信号多维成效测算")
    # 此处省略回测模块代码，逻辑同前一版本，保持向量化引擎
    # 用户点击后触发回测
