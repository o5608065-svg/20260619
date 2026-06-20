import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import datetime

# ================= 股票名称与代码映射字典 =================
# 您可以在这里自由添加您关注的股票中文名和对应的代码
STOCK_MAPPING = {
    # 美股科技巨头
    "苹果": "AAPL", "APPLE": "AAPL",
    "英伟达": "NVDA", "NVIDIA": "NVDA",
    "微软": "MSFT", "MICROSOFT": "MSFT",
    "特斯拉": "TSLA", "TESLA": "TSLA",
    "亚马逊": "AMZN", "AMAZON": "AMZN",
    "谷歌": "GOOGL", "GOOGLE": "GOOGL",
    "脸书": "META", "META": "META",
    "超微半导体": "AMD",
    # A股核心资产 (yfinance需要带 .SS 或 .SZ 后缀)
    "贵州茅台": "600519.SS", "茅台": "600519.SS",
    "宁德时代": "300750.SZ", "宁王": "300750.SZ",
    "比亚迪": "002594.SZ",
    "招商银行": "600036.SS", "招行": "600036.SS",
    "五粮液": "000858.SZ",
    "中国平安": "601318.SS",
    "立讯精密": "002475.SZ",
    "中芯国际": "688981.SS",
    "中兴通讯": "000063.SZ",
    # 港股核心资产 (yfinance需要带 .HK 后缀)
    "腾讯": "0700.HK", "腾讯控股": "0700.HK",
    "阿里": "BABA", "阿里巴巴": "BABA"
}

# 自动生成反向映射字典，用于在表格中显示中文名
TICKER_TO_NAME = {v: k for k, v in STOCK_MAPPING.items()}

def resolve_input(raw_inputs):
    """将用户输入的混合列表（名称或代码）统一解析为标准代码"""
    resolved_tickers = []
    for item in raw_inputs:
        # 去除空格并转换为大写匹配
        clean_item = item.strip().upper()
        # 如果输入的是字典里的中文名/别名，转换为代码
        if clean_item in [k.upper() for k in STOCK_MAPPING.keys()]:
            # 找到对应的大写键并取值
            for key, val in STOCK_MAPPING.items():
                if key.upper() == clean_item:
                    resolved_tickers.append(val)
                    break
        else:
            # 假设用户直接输入了标准代码，直接保留
            resolved_tickers.append(clean_item)
    # 去重并保持顺序
    return list(dict.fromkeys(resolved_tickers))

# ================= 页面与基础配置 =================
st.set_page_config(page_title="强势股逃顶择时系统", layout="wide")
st.title("📈 强势股逃顶择时量化系统")
st.markdown("基于风险调整后动量选股与拥挤度监控的实战框架")

# ================= 侧边栏：参数配置 =================
st.sidebar.header("⚙️ 系统参数配置")
st.sidebar.info("💡 提示：支持直接输入股票中文名（如：苹果, 贵州茅台）或标准代码（如：NVDA, 000858.SZ）。可混合输入。")

default_tickers = "英伟达, 苹果, 微软, 特斯拉, 贵州茅台, 宁德时代, AMD, GOOGL"
ticker_input = st.sidebar.text_area("监控股票池 (以逗号分隔)", value=default_tickers)
raw_tickers = [t.strip() for t in ticker_input.split(",") if t.strip()]

# 解析用户输入，转换为 yfinance 可识别的标准代码
tickers = resolve_input(raw_tickers)

st.sidebar.markdown("---")
st.sidebar.subheader("主观盘面判断")
is_sector_collapsing = st.sidebar.checkbox(
    "🚨 触发清仓级信号 (板块崩塌)", 
    help="观察到板块资金扩散：最强的龙头股高位滞涨，而边缘垃圾股突然补涨（群魔乱舞）。"
)

# ================= 核心数据获取 (带缓存) =================
@st.cache_data(ttl=3600) # 缓存1小时
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
    st.error("无法获取数据，请检查股票拼写、代码后缀或网络连接。")
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
            '股票代码': ticker,
            '股票名称': stock_name,
            '最新收盘价': latest['Close'],
            '20日动量': latest['MOM20'],
            '20日波动率': latest['volatility_20d'],
            '风险调整后动量 (RAM)': latest['RAM']
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
        
        # 1. 加速率 (ACC)
        df['5d_return_20d_avg'] = df['5d_return'].rolling(window=20).mean()
        acc = (df['5d_return'] - df['5d_return_20d_avg']).iloc[-1]
        warn_acc = acc > 0.05 
        
        # 2. 交易拥挤度
        vol_pct = df['Volume'].rolling(252).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x)>0 else np.nan
        ).iloc[-1]
        warn_crowd = vol_pct > 0.90
        
        # 3. 波动放大率
        vol_5d = df['daily_return'].rolling(5).std().iloc[-1]
        vol_20d = df['daily_return'].rolling(20).std().iloc[-1]
        vol_ratio = vol_5d / vol_20d if vol_20d > 0 else 0
        warn_vol = vol_ratio > 1.5
        
        # 4. 收盘强度
        range_p = df['High'] - df['Low']
        df['CloseStrength'] = np.where(range_p > 0, (df['Close'] - df['Low']) / range_p, 0.5)
        strength_3d = df['CloseStrength'].rolling(3).mean().iloc[-1]
        warn_strength = strength_3d < 0.5
        
        # 5. 量价背离
        vol_20d_avg = df['Volume'].rolling(20).mean().iloc[-1]
        is_surge = df['Volume'].iloc[-1] > (2 * vol_20d_avg)
        warn_diverge = is_surge and (df['CloseStrength'].iloc[-1] < 0.5)
        
        score = sum([warn_acc, warn_crowd, warn_vol, warn_strength, warn_diverge])
        stock_name = TICKER_TO_NAME.get(ticker, ticker)
        
        results.append({
            '股票代码': ticker,
            '股票名称': stock_name,
            '加速率 (>0.05)': f"{acc:.3f} {'🔴' if warn_acc else '🟢'}",
            '拥挤度 (>90%)': f"{vol_pct:.2%} {'🔴' if warn_crowd else '🟢'}",
            '波动率比 (>1.5)': f"{vol_ratio:.2f} {'🔴' if warn_vol else '🟢'}",
            '收盘强度 (<0.5)': f"{strength_3d:.2f} {'🔴' if warn_strength else '🟢'}",
            '量价背离': f"{'是 🔴' if warn_diverge else '否 🟢'}",
            '风险得分 (0-5)': score
        })
    return pd.DataFrame(results)

def run_phase_3(risk_df, is_collapsing):
    orders = []
    if is_collapsing:
        for _, row in risk_df.iterrows():
            orders.append({
                '股票代码': row['股票代码'], 
                '股票名称': row['股票名称'],
                '警报级别': '🚨 清仓级', 
                '执行动作': '清仓离场 (板块崩塌)', 
                '目标仓位': '0%'
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
            '股票代码': row['股票代码'], 
            '股票名称': row['股票名称'],
            '风险得分': score, 
            '警报级别': level, 
            '执行动作': action, 
            '目标仓位': pos
        })
    return pd.DataFrame(orders)

# ================= UI 渲染与切签页 =================
tab1, tab2, tab3 = st.tabs(["📊 阶段一：选股建仓", "🕵️‍♂️ 阶段二：日常监控", "⚡ 阶段三：行动准则"])

with tab1:
    st.subheader("核心指标：寻找上涨平稳、波动率低的健康标的")
    phase1_df = run_phase_1(data_dict)
    st.dataframe(phase1_df.style.format({
        '20日动量': '{:.2%}', '20日波动率': '{:.4f}', '风险调整后动量 (RAM)': '{:.4f}'
    }), use_container_width=True)

with tab2:
    st.subheader("高危预警系统：5大维度透视资金结构")
    selected_tickers = phase1_df['股票代码'].tolist()
    phase2_df = run_phase_2(data_dict, selected_tickers)
    st.dataframe(phase2_df, use_container_width=True)
    st.caption("注：红色圆点 🔴 代表该指标触及警戒阈值，绿色 🟢 代表处于健康状态。")

with tab3:
    st.subheader("仓位管理：基于水温严格执行纪律")
    if is_sector_collapsing:
        st.error("【全局警报】检测到板块内部轮动崩塌（龙头滞涨，边缘股补涨）！资金已无法推高龙头。")
    
    phase3_df = run_phase_3(phase2_df, is_sector_collapsing)
    st.dataframe(phase3_df, use_container_width=True)
    st.info("量化逃顶核心哲学：不要预测明天是涨是跌，而是测量当下的“水温”。")
