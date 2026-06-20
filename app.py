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

# ================= 1. 强健的工程模块：JSON备份与重试装饰器 (完整保留) =================
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
                        # 确保返回格式与原函数一致 (解包安全)
                        return args[0] if len(args)>0 else None, pd.DataFrame()
                    time.sleep(delay)
            return args[0] if len(args)>0 else None, pd.DataFrame()
        return wrapper
    return decorator

# 初始化 Session
if 'watchlist' not in st.session_state: st.session_state.watchlist = robust_load_json(WATCHLIST_FILE, DEFAULT_WATCHLIST)
if 'custom_names' not in st.session_state: st.session_state.custom_names = robust_load_json(CUSTOM_NAMES_FILE, {})

def get_stock_name(ticker): return st.session_state.custom_names.get(ticker, ticker)

# ================= 2. 页面与侧边栏动态参数 (完整保留并小幅适配V3.0) =================
st.set_page_config(page_title="全天候量化逃顶系统 v3.0", layout="wide")
st.title("📈 强势股自适应逃顶择时系统 v3.0")
st.caption("已修复前复权数据陷阱、引入 Sortino 下行风险比率、增加非线性致命否决机制")

st.sidebar.header("⚙️ 动态参数与引擎设置")

# 时间周期设置
interval_option = st.sidebar.selectbox("K线级别", ["1d (日线)", "60m (小时线)", "30m (半小时)"])
interval_map = {"1d (日线)": "1d", "60m (小时线)": "60m", "30m (半小时)": "30m"}
interval = interval_map[interval_option]

lookback_days = st.sidebar.slider("拉取回溯天数 (美股分钟级限730天)", 100, 730, 400)

# 动态阈值设置 (替换为自适应参数说明)
st.sidebar.subheader("系统参数 (自适应计算基准)")
param_window = st.sidebar.number_input("动量/波动基准周期", 10, 60, 20)
param_crowd_pct = st.sidebar.slider("资金极值报警分位数", 0.80, 0.99, 0.90)
# 移除了固定波动放大的slider，改为自适应

benchmark_ticker = st.sidebar.selectbox("选择对标大盘基准", ["SPY (标普500)", "000300.SS (沪深300)", "^HSI (恒生指数)"])
bm_map = {"SPY (标普500)": "SPY", "000300.SS (沪深300)": "000300.SS", "^HSI (恒生指数)": "^HSI"}
benchmark = bm_map[benchmark_ticker]

st.sidebar.markdown("---")
st.sidebar.header("📁 自选池管理")
new_stock = st.sidebar.text_input("➕ 添加自选 (标准代码):", placeholder="例如: AAPL, 600519.SS")
if st.sidebar.button("添加", use_container_width=True):
    if new_stock and new_stock.strip().upper() not in st.session_state.watchlist:
        st.session_state.watchlist.append(new_stock.strip().upper())
        robust_save_json(WATCHLIST_FILE, st.session_state.watchlist)
        st.rerun()

to_remove = st.sidebar.multiselect("➖ 移除自选:", st.session_state.watchlist)
if st.sidebar.button("确认移除", use_container_width=True) and to_remove:
    st.session_state.watchlist = [s for s in st.session_state.watchlist if s not in to_remove]
    robust_save_json(WATCHLIST_FILE, st.session_state.watchlist)
    st.rerun()

tickers = list(dict.fromkeys(st.session_state.watchlist + [benchmark]))

# ================= 3. 异步数据拉取与清洗引擎 (升级：前复权与真实成交额) =================
@retry_on_exception(retries=3)
def fetch_single_ticker(ticker, start, end, inv):
    # 核心升级 1：加入 auto_adjust=True 全面启用前复权
    df = yf.download(ticker, start=start, end=end, interval=inv, auto_adjust=True, progress=False)
    if df.empty or len(df) < param_window * 3: return ticker, pd.DataFrame()
    
    # 扁平化多层索引 (yfinance最新版本特性)
    if isinstance(df.columns, pd.MultiIndex):
        df = pd.DataFrame({'Open': df['Open'][ticker], 'High': df['High'][ticker], 'Low': df['Low'][ticker], 'Close': df['Close'][ticker], 'Volume': df['Volume'][ticker]})
    else:
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        
    # 核心清洗：剔除全天停牌或一字涨跌停的数据
    df = df[df['High'] != df['Low']]
    # 核心升级 2：计算真实成交额，屏蔽纯股本扩张带来的杂音
    df['DollarVolume'] = df['Close'] * df['Volume'] 
    return ticker, df

@st.cache_data(ttl=900) # 15分钟缓存 (完整保留)
def fetch_all_data(tickers_list, days, inv):
    end_date = datetime.date.today() + datetime.timedelta(days=1)
    start_date = end_date - datetime.timedelta(days=days)
    data_dict = {}
    
    with st.spinner('🚀 正在启用线程池异步并发拉取复权行情...'):
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

# ================= 4. 核心计算模块 (全面升级：下行风险、动态阈值、非线性计分) =================
# 全局变量，用于存储基准收益率序列
bm_returns = data_dict[benchmark]['Close'].pct_change() if benchmark in data_dict else None

def run_phase_1(data_dict, window):
    results = []
    for ticker, df in data_dict.items():
        if ticker == benchmark: continue
        df = df.copy()
        df['MOM'] = df['Close'] / df['Close'].shift(window) - 1
        df['daily_ret'] = df['Close'].pct_change()
        
        # 核心升级 3：Sortino 下行波动率逻辑。只惩罚下跌，不惩罚向上突破
        df['downside_ret'] = df['daily_ret'].clip(upper=0)
        df['down_vol'] = df['downside_ret'].rolling(window=window).std() * np.sqrt(252)
        
        # 风险调整动量 (RAM)
        df['RAM'] = np.where(df['down_vol'] > 1e-6, df['MOM'] / df['down_vol'], df['MOM'] / 1e-6)
        
        # 对标基准相对强度 (RS) = 个股动量 - 大盘动量
        rs_score = "N/A"
        if bm_returns is not None:
            bm_mom = bm_returns.rolling(window).sum().iloc[-1]
            rs_score = df['MOM'].iloc[-1] - bm_mom
            
        latest = df.iloc[-1]
        results.append({
            '代码': ticker, '名称': get_stock_name(ticker), '收盘价': latest['Close'],
            f'{window}日动量': latest['MOM'], '下行风险(Sortino)': latest['down_vol'],
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
        
        # 核心升级 4：动态自适应阈值 (基于自身历史分布计算)
        history_window = window * 5 # 过去一段时间的历史作为分布基准
        
        # 指标1: 加速异动 (判断是否超过自身历史 90% 的加速期)
        df['acc'] = df['5d_ret'] - df['5d_ret'].rolling(window).mean()
        acc_threshold = df['acc'].rolling(history_window).quantile(0.90).iloc[-1]
        w_acc = df['acc'].iloc[-1] > (acc_threshold if pd.notna(acc_threshold) else 0.05)
        
        # 指标2: 拥挤度 (基于真实的成交金额 DollarVolume)
        vol_pct = df['DollarVolume'].rolling(history_window).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x)>0 else np.nan).iloc[-1]
        w_crowd = vol_pct > param_crowd_pct
        
        # 指标3: 波动放大 (动态阈值比对)
        df['vol_ratio'] = df['ret'].rolling(5).std() / (df['ret'].rolling(window).std() + 1e-9)
        vol_ratio_threshold = df['vol_ratio'].rolling(history_window).quantile(0.90).iloc[-1]
        w_vol = df['vol_ratio'].iloc[-1] > (vol_ratio_threshold if pd.notna(vol_ratio_threshold) else 1.5)
        
        # 指标4: 弱收盘 (形态保留)
        range_p = df['High'] - df['Low']
        df['CloseStr'] = np.where(range_p > 0, (df['Close'] - df['Low']) / range_p, 0.5)
        str_3d = df['CloseStr'].rolling(3).mean().iloc[-1]
        w_str = str_3d < 0.4
        
        # 核心升级 5：非线性的一票否决机制 (Fatal Override)
        dollar_vol_mean = df['DollarVolume'].rolling(window).mean().iloc[-1]
        is_surge = df['DollarVolume'].iloc[-1] > (3 * dollar_vol_mean)
        fatal_diverge = is_surge and (str_3d < 0.3 or df['ret'].iloc[-1] < -0.02)
        
        # 形态描述客观化 (保留分析维度，替换主观臆测)
        pattern_desc = "正常波动"
        if w_crowd:
            tail_shadow = (df['High'].iloc[-1] - max(df['Open'].iloc[-1], df['Close'].iloc[-1])) / df['Close'].iloc[-1]
            if tail_shadow > 0.03: pattern_desc = "高位长影拒斥 📉"
            elif str_3d > 0.6: pattern_desc = "趋势放量冲刺 🚀"
            else: pattern_desc = "放量滞涨/派发 ⚠️"
            
        if fatal_diverge: pattern_desc = "🛑 史诗级断头背离"

        # 加权打分机制
        if fatal_diverge:
            score = 6.5 # 触发一票否决，直接满分
        else:
            score = (w_str * 1.5) + (w_acc * 1.0) + (w_crowd * 1.0) + (w_vol * 1.0)
            if is_surge and not fatal_diverge: score += 1.0 
        
        raw_signals[ticker] = df 
        results.append({
            '代码': ticker, '名称': get_stock_name(ticker), 
            '加速率(异动)': "是" if w_acc else "否", '客观形态特征': pattern_desc, '自适应波动比': f"{df['vol_ratio'].iloc[-1]:.2f}",
            '背离/破位': "🔴 致命熔断" if fatal_diverge else "🟢 正常",
            '加权风险(满分6.5)': min(score, 6.5)
        })
    return pd.DataFrame(results), raw_signals

# ================= 5. UI 呈现 (原版优秀UI 100%保留) =================
tab1, tab2, tab3, tab4 = st.tabs(["📊 阶段一：选股与超额", "🕵️ 阶段二：加权预警", "⚡ 阶段三：执行与组合热度", "⏱️ 信号回溯测算"])

with tab1:
    st.subheader("核心指标：寻找平稳高动量、具有大盘超额收益的标的 (采用Sortino惩罚)")
    phase1_df = run_phase_1(data_dict, param_window)
    st.dataframe(phase1_df.style.format({f'{param_window}日动量': '{:.2%}', '下行风险(Sortino)': '{:.4f}', '超额强度(RS)': '{:.4f}', '风险调整动量(RAM)': '{:.4f}'}), use_container_width=True)

with tab2:
    st.subheader(f"多维高危预警与资金形态分析 (周期: {interval})")
    phase2_df, raw_dfs = run_phase_2(data_dict, phase1_df['代码'].tolist(), param_window)
    st.dataframe(phase2_df.style.background_gradient(subset=['加权风险(满分6.5)'], cmap='Reds'), use_container_width=True)
    
with tab3:
    st.subheader("组合全局监控与仓位指令")
    st.info("注：此处反映的是您当前【自定义选股池】整体资金出逃热度，而非大盘的绝对崩塌。")
    
    # 自动感知组合过热
    avg_risk = phase2_df['加权风险(满分6.5)'].mean() if not phase2_df.empty else 0
    auto_collapse = avg_risk > 3.5
    
    col1, col2 = st.columns([1, 2])
    col1.metric("当前选股池平均危险指数", f"{avg_risk:.2f} / 6.5")
    if auto_collapse:
        col2.error("🚨 【系统自动判定：触发组合降温警报】选股池内多只标的高危共振，建议无差别降仓应对系统性风险！")
    else:
        col2.success("✅ 【系统状态正常】未监测到组合内大面积资金出逃，依据个股信号操作即可。")

    orders = []
    for _, row in phase2_df.iterrows():
        if auto_collapse:
            level, action, pos = "🚨 组合避险", "整体降仓防御", "10-20%"
        else:
            score = row['加权风险(满分6.5)']
            if score >= 6.0: level, action, pos = "🔴 致命危险", "无条件大幅减仓", "10-20%"
            elif score >= 4.0: level, action, pos = "🟠 高度警告", "减仓锁定利润", "40%"
            elif score >= 2.5: level, action, pos = "🟡 温和预警", "停止加仓，收紧止损", "维持现有"
            else: level, action, pos = "🟢 安全趋势", "正常持有", "满仓或上移止损"
        orders.append({'代码': row['代码'], '名称': row['名称'], '加权风险': row['加权风险(满分6.5)'], '级别': level, '动作': action, '目标仓位': pos})
    st.dataframe(pd.DataFrame(orders), use_container_width=True)

with tab4:
    st.subheader("简易胜率回溯测算 (历史极端熔断预警的后续表现)")
    st.markdown(f"统计过去 `{lookback_days}` 天内，标的触发 **史诗级断头背离 (致命熔断)** 后 5 个周期的涨跌情况。")
    if st.button("▶️ 开始回溯计算"):
        bt_results = []
        for ticker in phase1_df['代码'].tolist():
            if ticker not in raw_dfs: continue
            df_bt = raw_dfs[ticker].copy()
            dollar_vol_mean = df_bt['DollarVolume'].rolling(param_window).mean()
            str_3d = df_bt['CloseStr'].rolling(3).mean()
            
            # 找到所有的触发点 (严格匹配V3.0一票否决逻辑)
            signals = (df_bt['DollarVolume'] > 3 * dollar_vol_mean) & ((str_3d < 0.3) | (df_bt['ret'] < -0.02))
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
            st.info("在所选周期内，未找到触发极端背离预警的历史数据。说明当前参数下股票池历史运行较为平稳。")
