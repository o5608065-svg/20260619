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
DEFAULT_WATCHLIST = ["NVDA", "AAPL", "600519.SS", "002594.SZ", "TSLA", "AMD", "0700.HK", "SPY"]

STOCK_MAPPING = {
    "茅台": "600519.SS", "贵州茅台": "600519.SS",
    "英伟达": "NVDA", "苹果": "AAPL", "特斯拉": "TSLA", "微软": "MSFT",
    "谷歌": "GOOGL", "亚马逊": "AMZN", "脸书": "META", "超微": "AMD",
    "宁王": "300750.SZ", "宁德时代": "300750.SZ", "比亚迪": "002594.SZ",
    "腾讯": "0700.HK", "腾讯控股": "0700.HK", "阿里": "BABA", "阿里巴巴": "BABA",
    "标普": "SPY", "标普500": "SPY", "纳指": "QQQ", "沪深300": "000300.SS"
}

TICKER_NAME_MAPPING = {v: k for k, v in STOCK_MAPPING.items()}
TICKER_NAME_MAPPING.update({
    "NVDA": "英伟达", "AAPL": "苹果", "600519.SS": "贵州茅台", 
    "002594.SZ": "比亚迪", "TSLA": "特斯拉", "AMD": "超微半导体", 
    "0700.HK": "腾讯控股", "SPY": "标普500", "000300.SS": "沪深300"
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
st.set_page_config(page_title="自适应量化逃顶系统 v3.10", layout="wide")
st.title("📈 强势股情绪逃顶系统 v3.10")
st.caption("🚀 终极回测版：支持 1 日（次日）溢价/核按钮测试，精确检验超短线接力胜率。")

st.sidebar.header("⚙️ 引擎设置")

interval_option = st.sidebar.selectbox("K线级别", ["1d (日线)", "60m (小时线)", "30m (半小时)"])
interval_map = {"1d (日线)": "1d", "60m (小时线)": "60m", "30m (半小时)": "30m"}
interval = interval_map[interval_option]

lookback_days = st.sidebar.slider("拉取回溯天数", 200, 730, 400)

st.sidebar.subheader("系统参数 (自适应计算基准)")
param_window = st.sidebar.number_input("动量与阈值基准周期", 10, 60, 20)
param_crowd_pct = st.sidebar.slider("基础拥挤度报警分位数", 0.70, 0.99, 0.82)

benchmark_ticker = st.sidebar.selectbox("选择对标大盘基准", ["000300.SS (沪深300)", "SPY (标普500)", "^HSI (恒生指数)"])
bm_map = {"SPY (标普500)": "SPY", "000300.SS (沪深300)": "000300.SS", "^HSI (恒生指数)": "^HSI"}
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
                '背离/破位': "-", '综合高危得分(满分6.5)': 0, '_sort_val': -1
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
            '综合高危得分(满分6.5)': min(score, 6.5), '_sort_val': score
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
    st.caption("💡 **操作指引：直接双击下方表格中的【名称】列即可手动修改别名，按回车自动保存。**")
    
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
    
    locked_cols = [col for col in phase1_df.columns if col != '名称']
    
    st.data_editor(
        phase1_df.style.apply(highlight_suspended, axis=1).format(format_dict),
        disabled=locked_cols,
        use_container_width=True,
        hide_index=True,
        key="phase1_editor",
        on_change=on_name_edit 
    )

with tab2:
    st.subheader(f"连板妖股与接力情绪预警监控 (周期: {interval_option})")
    phase2_df, raw_dfs = run_phase_2(data_dict, phase1_df, param_window)
    st.dataframe(phase2_df.style.apply(highlight_suspended, axis=1).background_gradient(subset=['综合高危得分(满分6.5)'], cmap='Reds', vmin=0, vmax=6.5), use_container_width=True)
    
with tab3:
    st.subheader("组合全局监控与仓位指令")
    st.info("注：此处反映的是您当前【自定义选股池】整体资金出逃热度，不包含停牌及样本不足标的。")
    
    valid_scores = phase2_df[phase2_df['综合高危得分(满分6.5)'] > 0]['综合高危得分(满分6.5)']
    avg_risk = valid_scores.mean() if not valid_scores.empty else 0
    pool_overheated = avg_risk > 3.5 
    
    col1, col2 = st.columns([1, 2])
    col1.metric("选股池有效平均热度", f"{avg_risk:.2f} / 6.5")
    if pool_overheated:
        col2.error("🚨 【系统自动判定：触发组合降温警报】短线连板情绪崩塌，建议无差别降仓保利润！")
    else:
        col2.success("✅ 【组合状态平稳】短线情绪健康，依据个股信号正常接力或持有。")

    orders = []
    for _, row in phase2_df.iterrows():
        score = row['综合高危得分(满分6.5)']
        if score == 0: continue 
        
        if pool_overheated:
            level, action, pos = "🚨 组合避险", "整体核按钮离场", "0-10%"
        else:
            if score >= 6.0: level, action, pos = "🔴 致命危险", "无条件核按钮离场", "0%"
            elif score >= 4.0: level, action, pos = "🟠 高度警告", "断板即走/减仓止盈", "20-40%"
            elif score >= 2.5: level, action, pos = "🟡 温和预警", "停止接力，上移止损", "维持现有"
            else: level, action, pos = "🟢 安全趋势", "正常做多/格局", "满仓或上移止损"
        orders.append({'代码': row['代码'], '名称': row['名称'], '加权风险': score, '级别': level, '动作': action, '推荐仓位': pos})
    
    if orders: st.dataframe(pd.DataFrame(orders), use_container_width=True)

with tab4:
    st.subheader("历史信号多维成效测算（支持双向验证）")
    
    col_a, col_b = st.columns(2)
    bt_score_threshold = col_a.selectbox("选择回测信号触发条件：", [
        "满分 6.5 (史诗级断头/炸板)", 
        ">= 4.0分 (断板退潮/弱承接)", 
        ">= 2.5分 (温和预警)",
        "<= 2.0分 (安全持仓/低风险)",
        "== 0.0分 (完美安全/零风险绝佳点)"
    ])
    # 核心更新：加入 1 日表现选项
    bt_period = col_b.radio("观察信号触发后表现窗口：", [1, 3, 5, 10], index=2, horizontal=True)
    
    is_safe_test = "<=" in bt_score_threshold or "==" in bt_score_threshold
    
    if "6.5" in bt_score_threshold: bt_thresh_val = 6.5
    elif "4.0" in bt_score_threshold: bt_thresh_val = 4.0
    elif "2.5" in bt_score_threshold: bt_thresh_val = 2.5
    elif "2.0" in bt_score_threshold: bt_thresh_val = 2.0
    else: bt_thresh_val = 0.0
    
    st.markdown(f"统计过去 `{lookback_days}` 天内，标的触发 **[{bt_score_threshold}]** 后 `{bt_period}` 个周期的表现。系统已开启冷却期机制以防重复计算。")
    
    if st.button("▶️ 开始全量回溯计算", type="primary"):
        bt_results = []
        with st.spinner("正在后台进行向量推演..."):
            for ticker in phase1_df['代码'].tolist():
                if ticker not in raw_dfs: continue
                df_bt = raw_dfs[ticker].copy()
                history_window = param_window * 5
                
                df_bt['limit_up'] = df_bt['ret'] >= 0.09
                df_bt['streak'] = df_bt['limit_up'].groupby((~df_bt['limit_up']).cumsum()).cumcount()
                df_bt['streak'] = np.where(df_bt['limit_up'], df_bt['streak'] + 1, 0)
                
                was_limit_up = df_bt['streak'].shift(1) >= 1
                was_consec_limit_up = df_bt['streak'].shift(1) >= 2
                
                dynamic_surge_mul = np.where(was_limit_up, 2.0, 3.0)
                proxy_mean = df_bt['Crowd_Proxy'].rolling(param_window).mean()
                is_surge = df_bt['Crowd_Proxy'] > (dynamic_surge_mul * proxy_mean)
                
                df_bt['5d_ret'] = df_bt['Close'].pct_change(5)
                df_bt['acc'] = df_bt['5d_ret'] - df_bt['5d_ret'].rolling(param_window).mean()
                acc_threshold = df_bt['acc'].rolling(history_window).quantile(0.90)
                w_acc = df_bt['acc'] > acc_threshold.fillna(0.05)
                
                dynamic_crowd_pct = np.where((was_consec_limit_up) | (df_bt['streak'] >= 2), 0.75, param_crowd_pct)
                vol_pct = df_bt['Crowd_Proxy'].rolling(history_window).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x)>0 else np.nan)
                w_crowd = vol_pct > dynamic_crowd_pct
                
                df_bt['vol_ratio'] = df_bt['ret'].rolling(5).std() / (df_bt['ret'].rolling(param_window).std() + 1e-9)
                vol_ratio_threshold = df_bt['vol_ratio'].rolling(history_window).quantile(0.90)
                w_vol = df_bt['vol_ratio'] > vol_ratio_threshold.fillna(1.5)
                
                range_p = df_bt['High'] - df_bt['Low']
                df_bt['CloseStr'] = np.where(range_p > 0, (df_bt['Close'] - df_bt['Low']) / range_p, 0.5)
                str_3d = df_bt['CloseStr'].rolling(3).mean()
                w_str = str_3d < 0.5 
                
                open_high_go_low = (df_bt['Open'] > df_bt['Close'].shift(1)) & (df_bt['Close'] < df_bt['Open'])
                blew_board = (df_bt['High'] / df_bt['Close'].shift(1) - 1 >= 0.09) & (df_bt['Close'] < df_bt['High'])
                early_fatal = was_limit_up & (open_high_go_low | blew_board) & is_surge
                
                fatal_diverge = (is_surge & ((str_3d < 0.3) | (df_bt['ret'] < -0.02))) | early_fatal
                
                weak_relay = (df_bt['ret'] > 0) & (df_bt['ret'] < df_bt['ret'].shift(1)) & (df_bt['CloseStr'] < 0.5) & is_surge
                break_board = was_consec_limit_up & (~df_bt['limit_up']) & is_surge
                
                scores = (w_str * 1.5) + (w_acc * 1.0) + (w_crowd * 1.0) + (w_vol * 1.0) + ((is_surge & ~fatal_diverge) * 1.0)
                scores = scores + np.where(weak_relay, 1.0, 0) + np.where(break_board, 2.0, 0)
                scores = np.where(fatal_diverge, 6.5, scores)
                scores = np.clip(scores, 0, 6.5)
                
                if "==" in bt_score_threshold: signals = scores == 0.0
                elif "<=" in bt_score_threshold: signals = scores <= bt_thresh_val
                elif bt_thresh_val == 6.5: signals = scores >= 6.5
                else: signals = scores >= bt_thresh_val
                    
                signal_dates = df_bt.index[signals]
                
                last_idx = -999
                for date in signal_dates:
                    idx = df_bt.index.get_loc(date)
                    if idx - last_idx < bt_period: continue 
                    
                    if idx + bt_period < len(df_bt): 
                        price_at_signal = df_bt['Close'].iloc[idx]
                        price_after = df_bt['Close'].iloc[idx + bt_period]
                        ret_period = (price_after / price_at_signal) - 1
                        bt_results.append({'代码': ticker, '名称': get_stock_name(ticker), '信号日期': date.strftime("%Y-%m-%d"), '触发得分': f"{scores[idx]:.1f}", f'{bt_period}周期后表现': ret_period})
                        last_idx = idx 
                        
        if bt_results:
            bt_df = pd.DataFrame(bt_results)
            
            if is_safe_test:
                success_count = len(bt_df[bt_df[f'{bt_period}周期后表现'] > 0])
                metric_label = f"做多胜率 (发出低风险/零风险信号后确实上涨的比例)"
            else:
                success_count = len(bt_df[bt_df[f'{bt_period}周期后表现'] < 0]) 
                metric_label = f"防守胜率 (发出高危信号后确实下跌避开回调的比例)"
                
            st.metric(metric_label, f"{(success_count / len(bt_df)):.2%}", f"全量历史共发现 {len(bt_df)} 次有效信号")
            st.dataframe(bt_df.style.format({f'{bt_period}周期后表现': '{:.2%}'}).background_gradient(subset=[f'{bt_period}周期后表现'], cmap='RdYlGn_r'), use_container_width=True)
        else:
            st.info(f"在您选择的阈值 [{bt_score_threshold}] 下，未捕捉到任何历史信号。")
