# -*- coding: utf-8 -*-
"""
投資資産・配当金・増配率 管理アプリ（クラウド版）
--------------------------------------------------
データ保存: Google Sheets
ホスティング: Streamlit Community Cloud
起動: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, date
import time
import csv
import re
import io as _io

import db
import fetcher

# ═══════════════════════════════════════════════════════════════════════════════
# 初期設定
# ═══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="配当・資産管理",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
  html, body, [class*="css"] { font-family: "Noto Sans JP", sans-serif; }
  .stTabs [data-baseweb="tab-list"] { gap: 4px; flex-wrap: wrap; }
  .stTabs [data-baseweb="tab"] {
      height: 44px; padding: 0 12px;
      font-size: 13px; font-weight: 600;
  }
  .stDataFrame { overflow-x: auto; }
  .pos { color: #00c853; font-weight: bold; }
  .neg { color: #d32f2f; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

db.init_db()

# ═══════════════════════════════════════════════════════════════════════════════
# 定数・ユーティリティ
# ═══════════════════════════════════════════════════════════════════════════════
ASSET_TYPES = ["日本株", "米国株", "米国ETF", "債券", "投資信託", "その他"]
CURRENCIES  = ["JPY", "USD"]

# 配当データを自動再取得する間隔（日）。配当は年数回しか変わらないため長め。
DIV_REFRESH_DAYS = 7


def fmt_jpy(v) -> str:
    if pd.isna(v) or v is None: return "—"
    return f"¥{v:,.0f}"

def fmt_pct(v) -> str:
    if pd.isna(v) or v is None: return "—"
    return f"{'+' if v >= 0 else ''}{v:.1f}%"

def needs_manual_price(row) -> bool:
    return row.get("asset_type") in ("投資信託", "その他")

def get_prices_jpy(holdings_df: pd.DataFrame, usd_jpy: float) -> dict:
    auto_tickers = [
        r["ticker"] for _, r in holdings_df.iterrows()
        if not needs_manual_price(r) and r["ticker"]
    ]
    prices_raw = fetcher.get_prices_bulk(tuple(auto_tickers)) if auto_tickers else {}

    result = {}
    for _, row in holdings_df.iterrows():
        t = row["ticker"]
        manual = float(row.get("manual_price") or 0)
        if needs_manual_price(row):
            result[t] = manual
        else:
            raw = prices_raw.get(t)
            if raw is None or raw != raw:  # None or NaN
                result[t] = manual
            elif row.get("currency") == "USD":
                result[t] = float(raw) * usd_jpy
            else:
                result[t] = float(raw)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# タブ: ダッシュボード
# ═══════════════════════════════════════════════════════════════════════════════
def tab_dashboard():
    st.subheader("📊 ダッシュボード")

    holdings = db.get_holdings()
    if holdings.empty:
        st.info("まだ銘柄が登録されていません。「銘柄管理」タブから追加してください。")
        return

    usd_jpy   = fetcher.get_usd_jpy()
    prices_jpy = get_prices_jpy(holdings, usd_jpy)

    total_cost = total_value = total_annual_div = 0.0
    rows = []
    for _, row in holdings.iterrows():
        t        = row["ticker"]
        shares   = float(row["shares"] or 0)
        avg_cost = float(row["avg_cost"] or 0)
        cur      = str(row.get("currency") or "JPY")
        cur_jpy  = float(prices_jpy.get(t) or 0)

        cost_jpy  = avg_cost * (usd_jpy if cur == "USD" else 1) * shares
        value_jpy = cur_jpy * shares
        gain_jpy  = value_jpy - cost_jpy
        gain_pct  = (gain_jpy / cost_jpy * 100) if cost_jpy > 0 else 0

        # 投資信託等はyfinance非対応のため配当見込みは取得しない
        fwd_div = 0.0 if needs_manual_price(row) else \
            float(fetcher.get_company_info(t).get("forward_dividend") or 0)
        annual_div_jpy = fwd_div * (usd_jpy if cur == "USD" else 1) * shares

        total_cost       += cost_jpy
        total_value      += value_jpy
        total_annual_div += annual_div_jpy

        rows.append({"ticker": t, "name": row["name"],
                     "asset_type": row["asset_type"],
                     "value_jpy": value_jpy, "gain_jpy": gain_jpy,
                     "gain_pct": gain_pct})

    total_gain     = total_value - total_cost
    total_gain_pct = (total_gain / total_cost * 100) if total_cost > 0 else 0
    div_yield      = (total_annual_div / total_value * 100) if total_value > 0 else 0

    # ── メトリクス ────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("総資産評価額", fmt_jpy(total_value))
    c2.metric("評価損益",
              f"{fmt_jpy(total_gain)} ({fmt_pct(total_gain_pct)})",
              delta=f"{total_gain_pct:.1f}%")
    c3.metric("年間配当見込み", fmt_jpy(total_annual_div))
    c4.metric("配当利回り（加重）", f"{div_yield:.2f}%")
    st.caption(f"USD/JPY: {usd_jpy:.2f}　最終更新: {datetime.now().strftime('%H:%M:%S')}（5分毎自動更新）")

    # 1日1回だけ資産スナップショットを自動記録
    snapshot_key = f"asset_snapshot_{date.today().isoformat()}"
    if snapshot_key not in st.session_state:
        db.record_asset_snapshot(date.today().isoformat(), total_value)
        st.session_state[snapshot_key] = True

    st.divider()

    # ── 資産推移 ─────────────────────────────────────────────────────────
    st.markdown("#### 📈 資産推移")
    asset_hist = db.get_asset_history()
    if not asset_hist.empty:
        asset_hist = asset_hist.sort_values("date").reset_index(drop=True)
        av = asset_hist["total_value_jpy"]
        first_v, last_v = float(av.iloc[0]), float(av.iloc[-1])
        prev_v  = float(av.iloc[-2]) if len(av) >= 2 else first_v
        first_d = asset_hist["date"].iloc[0]

        a1, a2, a3 = st.columns(3)
        a1.metric("最新記録の総資産", fmt_jpy(last_v))
        a2.metric("前回記録比", fmt_jpy(last_v - prev_v),
                  delta=f"{(last_v - prev_v) / prev_v * 100:+.1f}%" if prev_v else None)
        a3.metric(f"{first_d} 比", fmt_jpy(last_v - first_v),
                  delta=f"{(last_v - first_v) / first_v * 100:+.1f}%" if first_v else None)

        if len(av) > 1:
            fig_hist = go.Figure(go.Scatter(
                x=asset_hist["date"], y=av, mode="lines+markers",
                line=dict(color="#1976D2", width=2.5), marker=dict(size=6),
                fill="tozeroy", fillcolor="rgba(25,118,210,0.10)",
                hovertemplate="%{x}<br>¥%{y:,.0f}<extra></extra>",
            ))
            fig_hist.update_layout(xaxis_title="", yaxis_title="総資産評価額(円)",
                                   margin=dict(t=10, b=20, l=20, r=20), height=300)
            st.plotly_chart(fig_hist, use_container_width=True)
        else:
            st.caption("記録が2日分以上たまると、ここに推移グラフが表示されます。")
    else:
        st.caption("アプリを開くたびに、その日の総資産が自動で記録されていきます。")

    st.divider()

    # ── チャート ─────────────────────────────────────────────────────────
    summary_df = pd.DataFrame(rows)
    cl, cr = st.columns(2)

    with cl:
        st.markdown("#### 資産配分（種類別）")
        by_type = summary_df.groupby("asset_type")["value_jpy"].sum().reset_index()
        fig = px.pie(by_type, values="value_jpy", names="asset_type",
                     color_discrete_sequence=px.colors.qualitative.Set2, hole=0.4)
        fig.update_traces(textposition="outside", textinfo="percent+label")
        fig.update_layout(showlegend=True, legend=dict(orientation="h", y=-0.1),
                          margin=dict(t=10, b=60, l=10, r=10), height=320)
        st.plotly_chart(fig, use_container_width=True)

    with cr:
        st.markdown("#### 銘柄別評価額 TOP10")
        top10 = summary_df.nlargest(10, "value_jpy")
        fig2  = px.bar(top10, x="value_jpy", y="ticker", orientation="h",
                       color="asset_type",
                       color_discrete_sequence=px.colors.qualitative.Set2,
                       labels={"value_jpy": "評価額(円)", "ticker": ""})
        fig2.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10),
                           height=320, yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig2, use_container_width=True)

    # ── 月別配当カレンダー ────────────────────────────────────────────────
    st.divider()
    st.markdown("#### 月別配当受取額（記録済み）")
    divs_recv = db.get_dividends_received()
    if not divs_recv.empty:
        divs_recv["date"]  = pd.to_datetime(divs_recv["date"])
        divs_recv["month"] = divs_recv["date"].dt.strftime("%Y-%m")
        monthly = divs_recv.groupby("month")["total_amount"].sum().reset_index()
        fig3 = px.bar(monthly, x="month", y="total_amount",
                      labels={"month": "月", "total_amount": "受取額(円)"},
                      color_discrete_sequence=["#4CAF50"])
        fig3.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=280)
        st.plotly_chart(fig3, use_container_width=True)
    else:
        st.info("配当受取実績がまだありません。「配当受取」タブから記録してください。")


# ═══════════════════════════════════════════════════════════════════════════════
# タブ: ポートフォリオ
# ═══════════════════════════════════════════════════════════════════════════════
def tab_portfolio():
    st.subheader("💼 ポートフォリオ")

    holdings = db.get_holdings()
    if holdings.empty:
        st.info("銘柄が登録されていません。")
        return

    usd_jpy   = fetcher.get_usd_jpy()
    prices_jpy = get_prices_jpy(holdings, usd_jpy)

    sel_type = st.selectbox("種類フィルター", ["全て"] + ASSET_TYPES)

    rows = []
    for _, row in holdings.iterrows():
        if sel_type != "全て" and row["asset_type"] != sel_type:
            continue
        t        = row["ticker"]
        shares   = float(row["shares"] or 0)
        avg_cost = float(row["avg_cost"] or 0)
        cur      = str(row.get("currency") or "JPY")
        cur_jpy  = float(prices_jpy.get(t) or 0)

        cost_jpy     = avg_cost * (usd_jpy if cur == "USD" else 1)
        value_jpy    = cur_jpy * shares
        gain_jpy     = (cur_jpy - cost_jpy) * shares
        gain_pct     = ((cur_jpy - cost_jpy) / cost_jpy * 100) if cost_jpy > 0 else 0

        fwd_div   = 0.0 if needs_manual_price(row) else \
            float(fetcher.get_company_info(t).get("forward_dividend") or 0)
        cur_price = cur_jpy / usd_jpy if cur == "USD" else cur_jpy
        div_yield = (fwd_div / cur_price * 100) if cur_price > 0 else 0

        rows.append({
            "種類": row["asset_type"], "コード": t, "銘柄名": row["name"],
            "保有株数": shares,
            "平均取得単価": f"${avg_cost:.2f}" if cur == "USD" else fmt_jpy(avg_cost),
            "現在値": f"${cur_jpy/usd_jpy:.2f}" if cur == "USD" else fmt_jpy(cur_jpy),
            "評価額(円)": value_jpy, "評価損益(円)": gain_jpy,
            "損益率": gain_pct, "配当利回り": div_yield,
        })

    if not rows:
        st.info("表示できる銘柄がありません。")
        return

    df = pd.DataFrame(rows)

    def _style_gain(val):
        if isinstance(val, float):
            return f"color: {'#00c853' if val >= 0 else '#d32f2f'}; font-weight:bold"
        return ""

    styled = df.style.format({
        "評価額(円)": "{:,.0f}", "評価損益(円)": "{:+,.0f}",
        "損益率": "{:+.1f}%", "配当利回り": "{:.2f}%", "保有株数": "{:,.0f}",
    }).map(_style_gain, subset=["評価損益(円)", "損益率"])

    st.dataframe(styled, use_container_width=True, height=420)

    total_value = df["評価額(円)"].sum()
    total_gain  = df["評価損益(円)"].sum()
    st.markdown(f"**合計評価額: {fmt_jpy(total_value)}　合計損益: {fmt_jpy(total_gain)}**")


# ═══════════════════════════════════════════════════════════════════════════════
# タブ: 配当・増配率
# ═══════════════════════════════════════════════════════════════════════════════
def tab_dividend_growth():
    st.subheader("💰 配当・増配率")

    holdings = db.get_holdings()
    if holdings.empty:
        st.info("銘柄が登録されていません。")
        return

    col_btn, col_info = st.columns([2, 5])
    with col_btn:
        if st.button("📥 配当データを最新取得", type="primary"):
            with st.spinner("取得中...（しばらくお待ちください）"):
                _fetch_and_store_dividends(holdings)
                db.set_meta("div_last_fetched", date.today().isoformat())
            st.success("取得完了！")
            st.cache_data.clear()
            st.rerun()
    with col_info:
        st.caption("日本株: yfinance（実績）＋株探（予想）　米国株・ETF: yfinance（実績）")

    div_hist = db.get_div_history()
    if div_hist.empty:
        st.info("「配当データを最新取得」ボタンを押してください。")
        return

    pivot = div_hist.pivot_table(
        index="ticker", columns="fiscal_year", values="dps", aggfunc="last"
    )
    all_cols = sorted(pivot.columns.tolist())
    pivot = pivot[all_cols]

    name_map = dict(zip(holdings["ticker"], holdings["name"]))
    growth_rows = []
    for ticker, row_s in pivot.iterrows():
        vals = row_s.dropna()
        base = {"コード": ticker, "銘柄名": name_map.get(ticker, ticker)}
        for col in all_cols:
            base[col] = row_s.get(col)
        yrs = vals.index.tolist()
        for i in range(1, len(yrs)):
            gr = fetcher.calc_growth_rate(vals[yrs[i]], vals[yrs[i - 1]])
            base[f"↑{yrs[i]}"] = gr
        growth_rows.append(base)

    growth_df = pd.DataFrame(growth_rows).set_index("コード")
    rate_cols = [c for c in growth_df.columns if c.startswith("↑")]
    div_cols  = [c for c in growth_df.columns if not c.startswith("↑") and c != "銘柄名"]

    def _color_rate(val):
        if pd.isna(val) or val is None: return ""
        if val > 5:  return "background-color:#c8e6c9;color:#1b5e20"
        if val > 0:  return "background-color:#e8f5e9"
        if val < 0:  return "background-color:#ffebee;color:#b71c1c"
        return ""

    fmt_dict = {c: "{:.1f}%" for c in rate_cols}
    fmt_dict.update({c: "{:.1f}" for c in div_cols if c in growth_df.columns})
    styled = growth_df.style.format(fmt_dict, na_rep="—").map(
        _color_rate, subset=rate_cols
    )
    st.dataframe(styled, use_container_width=True, height=400)

    # ── 個別銘柄チャート ──────────────────────────────────────────────────
    st.divider()
    st.markdown("#### 銘柄別配当推移")
    tickers_with_data = pivot.dropna(how="all").index.tolist()
    if tickers_with_data:
        sel = st.selectbox("銘柄を選択", tickers_with_data,
                           format_func=lambda t: f"{t}（{name_map.get(t, t)}）")
        ticker_data = pivot.loc[sel].dropna()
        if not ticker_data.empty:
            yrs = ticker_data.index.tolist()

            # ── サマリー指標（累積増配率・年平均・連続増配） ──
            if len(yrs) > 1:
                base_v, last_v = ticker_data[yrs[0]], ticker_data[yrs[-1]]
                span = len(yrs) - 1
                cum  = (last_v / base_v - 1) * 100 if base_v else None
                cagr = ((last_v / base_v) ** (1 / span) - 1) * 100 if base_v and last_v > 0 else None
                streak = 0
                for i in range(len(yrs) - 1, 0, -1):
                    if ticker_data[yrs[i]] > ticker_data[yrs[i - 1]]:
                        streak += 1
                    else:
                        break
                m1, m2, m3 = st.columns(3)
                m1.metric(f"累積増配率（{yrs[0]}→{yrs[-1]}）", fmt_pct(cum))
                m2.metric("年平均増配率（CAGR）", fmt_pct(cagr))
                m3.metric("連続増配", f"{streak}年" if streak else "—")

            fig = go.Figure(go.Bar(
                x=yrs, y=ticker_data.values.tolist(),
                marker_color="#4CAF50",
                text=[f"{v:.1f}" for v in ticker_data.values],
                textposition="outside",
            ))
            fig.update_layout(title=f"{sel}（{name_map.get(sel, sel)}）配当推移",
                              xaxis_title="会計年度", yaxis_title="1株配当(円/$)",
                              margin=dict(t=40, b=20, l=20, r=20), height=320)
            st.plotly_chart(fig, use_container_width=True)
            if len(yrs) > 1:
                rates = "　".join(
                    f"{yrs[i]}: {fmt_pct(fetcher.calc_growth_rate(ticker_data[yrs[i]], ticker_data[yrs[i-1]]))}"
                    for i in range(1, len(yrs))
                )
                st.caption(f"増配率（前年比）　{rates}")

    # ── 年間配当収入推移 ──────────────────────────────────────────────────
    st.divider()
    st.markdown("#### ポートフォリオ全体 年間配当収入推移")
    usd_jpy = fetcher.get_usd_jpy()
    yearly  = {}
    for _, row in holdings.iterrows():
        t, shares, cur = row["ticker"], float(row["shares"]), row["currency"]
        if t not in pivot.index: continue
        for fy, dps in pivot.loc[t].dropna().items():
            amt = dps * (usd_jpy if cur == "USD" else 1) * shares
            yearly[fy] = yearly.get(fy, 0) + amt

    if yearly:
        fy_sorted = sorted(yearly)
        totals    = [yearly[f] for f in fy_sorted]
        fig2 = go.Figure(go.Bar(
            x=fy_sorted, y=totals, marker_color="#1976D2",
            text=[fmt_jpy(v) for v in totals], textposition="outside",
        ))
        if len(fy_sorted) >= 2:
            gr_vals = [None] + [
                fetcher.calc_growth_rate(totals[i], totals[i-1])
                for i in range(1, len(totals))
            ]
            fig2.add_trace(go.Scatter(
                x=fy_sorted, y=gr_vals, mode="lines+markers+text",
                name="成長率(%)", yaxis="y2",
                line=dict(color="#FF5722", width=2),
                text=[f"{v:.1f}%" if v else "" for v in gr_vals],
                textposition="top center",
            ))
            fig2.update_layout(yaxis2=dict(
                title="成長率(%)", overlaying="y", side="right", showgrid=False
            ))
        fig2.update_layout(xaxis_title="会計年度", yaxis_title="配当収入合計(円)",
                           margin=dict(t=10, b=20, l=20, r=60),
                           height=320, showlegend=True)
        st.plotly_chart(fig2, use_container_width=True)


def _fetch_and_store_dividends(holdings: pd.DataFrame):
    for _, row in holdings.iterrows():
        t, atype = row["ticker"], row["asset_type"]
        if atype == "投資信託": continue
        hist = fetcher.get_annual_dividends(t)
        for fy, dps in hist.items():
            db.upsert_div_history(t, fy, dps, source="yfinance")
        if t.endswith(".T"):
            code     = t.replace(".T", "")
            forecast = fetcher.fetch_kabutan_forecast(code)
            existing = db.get_div_history()
            for fy, dps in forecast.items():
                if not existing.empty:
                    has_actual = not existing[
                        (existing["ticker"] == t) &
                        (existing["fiscal_year"] == fy) &
                        (existing["source"] == "yfinance")
                    ].empty
                    if has_actual: continue
                db.upsert_div_history(t, fy, dps, source="kabutan")
            time.sleep(0.4)


# ═══════════════════════════════════════════════════════════════════════════════
# CSVインポート（SBI証券・楽天証券ポートフォリオCSV対応）
# ═══════════════════════════════════════════════════════════════════════════════
_CODE_RE = re.compile(r"^(\d{3,4}[A-Za-z0-9]?)[\s　]+(.+)$")
_DATE_RE = re.compile(r"^(\d{4}/\d{2}/\d{2}|-+/-+/-+)$")


def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _add_stock(agg, ticker, name, atype, shares, avg_cost):
    if ticker in agg:
        agg[ticker]["shares"]   += shares
        agg[ticker]["cost_sum"] += shares * avg_cost
    else:
        agg[ticker] = {"name": name, "atype": atype,
                       "shares": shares, "cost_sum": shares * avg_cost}


def _parse_one(raw_bytes, agg_stock, agg_fund):
    """1ファイルを解析して agg_stock / agg_fund に加算。broker種別を返す。"""
    for enc in ["cp932", "shift-jis", "utf-8-sig", "utf-8"]:
        try:
            raw = raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue

        ls = raw.splitlines()
        hrow = None
        is_rakuten = False
        for i, line in enumerate(ls):
            if "保有" in line and "取得" in line:
                hrow, is_rakuten = i, True
                break
            elif "取得単価" in line:
                hrow = i
                break

        if hrow is None:
            try:
                return ("generic", pd.read_csv(_io.StringIO(raw)))
            except Exception:
                continue

        for row in csv.reader(ls[hrow + 1:]):
            if not row or not any(c.strip() for c in row):
                continue

            if is_rakuten:
                try:
                    code = str(row[1]).strip()
                    name = str(row[2]).strip()
                    if not name or name == "nan":
                        continue
                    sh, ac = _num(row[4]), _num(row[6])
                    if sh is None or ac is None or sh <= 0 or ac <= 0:
                        continue
                    if code.isdigit() and len(code) <= 4:
                        ticker, atype = code + ".T", "日本株"
                    elif code:
                        ticker, atype = code, "米国株"
                    else:
                        continue
                except Exception:
                    continue
                _add_stock(agg_stock, ticker, name, atype, sh, ac)
                continue

            # ── SBI ──
            if len(row) < 5:
                continue
            c0 = str(row[0]).strip()
            if len(row) < 2 or not _DATE_RE.match(str(row[1]).strip()):
                continue
            m = _CODE_RE.match(c0)
            if m:
                sh, ac = _num(row[2]), _num(row[3])
                if sh is None or ac is None or sh <= 0:
                    continue
                code = m.group(1)
                ticker = (code + ".T") if (len(code) <= 4 and code.isdigit()) else code
                _add_stock(agg_stock, ticker, m.group(2).strip(), "日本株", sh, ac)
            else:
                if len(row) < 10:
                    continue
                qty, value, gain = _num(row[2]), _num(row[9]), _num(row[7])
                if qty is None or qty <= 0 or value is None:
                    continue
                cost = value - gain if gain is not None else value
                if c0 in agg_fund:
                    agg_fund[c0]["value"] += value
                    agg_fund[c0]["cost"]  += cost
                else:
                    agg_fund[c0] = {"value": value, "cost": cost}

        return ("楽天証券" if is_rakuten else "SBI証券", None)

    return (None, None)


def parse_broker_csv(raw_bytes_list):
    """SBI証券・楽天証券のポートフォリオCSV（複数ファイル・複数ページ可）を解析する。

    - 個別株: 特定/NISA など同一銘柄を合算（取得単価は加重平均）。
    - 投資信託(SBI): 評価額・取得額を取り込み手動価格として登録。ファンド名で合算。
    - 集計行・見出し行は自動スキップ。SBI/楽天形式でなければ汎用CSVとして読む。

    戻り値: (DataFrame or None, broker_name)
    """
    agg_stock, agg_fund = {}, {}
    broker_name = None
    generic_df = None
    for raw_bytes in raw_bytes_list:
        broker, gdf = _parse_one(raw_bytes, agg_stock, agg_fund)
        if broker == "generic":
            generic_df = gdf if generic_df is None else generic_df
        elif broker:
            broker_name = broker

    recs = []
    for ticker, v in agg_stock.items():
        avg = v["cost_sum"] / v["shares"] if v["shares"] else 0
        recs.append({"ticker": ticker, "name": v["name"],
                     "asset_type": v["atype"], "shares": v["shares"],
                     "avg_cost": round(avg, 2), "currency": "JPY",
                     "manual_price": 0})
    for name, v in agg_fund.items():
        recs.append({"ticker": name, "name": name, "asset_type": "投資信託",
                     "shares": 1, "avg_cost": round(v["cost"], 2),
                     "currency": "JPY", "manual_price": round(v["value"], 2)})

    if recs:
        return pd.DataFrame(recs), (broker_name or "SBI証券")
    if generic_df is not None:
        return generic_df, "CSV"
    return None, "CSV"


# ═══════════════════════════════════════════════════════════════════════════════
# タブ: 銘柄管理
# ═══════════════════════════════════════════════════════════════════════════════
def tab_manage_holdings():
    st.subheader("🏦 銘柄管理")

    tab_a, tab_b = st.tabs(["銘柄一覧・削除", "新規登録"])

    with tab_a:
        holdings = db.get_holdings()
        if holdings.empty:
            st.info("登録銘柄はありません。")
        else:
            st.dataframe(holdings, use_container_width=True, height=300)
            st.markdown("#### 銘柄を削除")
            options = holdings["ticker"].unique().tolist()
            del_t = st.selectbox("削除する銘柄", options,
                                 format_func=lambda t: f"{t}（{holdings.loc[holdings['ticker']==t,'name'].values[0]}）",
                                 key="del_select")
            if st.button("削除する", type="secondary"):
                db.delete_holding(del_t)
                st.success(f"{del_t} を削除しました。")
                st.rerun()

    with tab_b:
        st.markdown("#### 新規銘柄登録")
        with st.form("add_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                new_ticker = st.text_input("ティッカー", placeholder="例: 7203.T / AAPL / VYM")
                new_name   = st.text_input("銘柄名", placeholder="例: トヨタ自動車")
                new_type   = st.selectbox("種類", ASSET_TYPES)
            with c2:
                new_shares   = st.number_input("保有株数", min_value=0.0, step=1.0)
                new_avg_cost = st.number_input("平均取得単価", min_value=0.0, step=0.01)
                new_currency = st.selectbox("通貨", CURRENCIES)
                new_manual   = st.number_input("手動価格（投資信託等）",
                                               min_value=0.0, step=1.0,
                                               help="yfinance 非対応銘柄のみ入力")
            new_notes = st.text_input("メモ（任意）")
            sub = st.form_submit_button("登録", type="primary")

        if sub:
            if not new_ticker or not new_name:
                st.error("ティッカーと銘柄名は必須です。")
            else:
                t = new_ticker.strip().upper()
                # 日本株の4桁コードは .T を自動付与（価格取得¥0対策）
                if new_type == "日本株" and not t.endswith(".T") and t[:1].isdigit():
                    t += ".T"
                db.upsert_holding(t, new_name, new_type, "手動",
                                  new_shares, new_avg_cost,
                                  new_currency, new_manual, new_notes)
                st.success(f"{t}（{new_name}）を登録しました。")
                st.rerun()

        st.divider()
        st.markdown("#### CSV一括インポート")
        st.caption(
            "SBI証券・楽天証券のポートフォリオCSV（数量・取得単価を自動判定、特定/NISAの"
            "同一銘柄は合算、SBIは投資信託も取込）または "
            "`ticker,name,asset_type,shares,avg_cost,currency` 形式に対応。"
            "**ページが分かれている場合は全ページのCSVをまとめて選択してください。**"
        )
        uploaded = st.file_uploader(
            "CSVを選択（複数可）", type=["csv"], accept_multiple_files=True)
        if uploaded:
            try:
                df_imp, broker_name = parse_broker_csv([f.getvalue() for f in uploaded])
                if df_imp is None or df_imp.empty:
                    st.warning("銘柄データが見つかりませんでした。")
                else:
                    n_stock = int((df_imp["asset_type"] != "投資信託").sum()) if "asset_type" in df_imp else len(df_imp)
                    n_fund  = int((df_imp["asset_type"] == "投資信託").sum()) if "asset_type" in df_imp else 0
                    st.dataframe(df_imp, use_container_width=True)
                    st.caption(f"取込元: {broker_name}（個別株 {n_stock}・投資信託 {n_fund}／特定・NISAは合算済み）")
                    replace_mode = st.checkbox(
                        f"{broker_name} の既存データを置き換える（再インポートしても二重登録されません）",
                        value=True)
                    if st.button("インポート実行", type="primary"):
                        if replace_mode:
                            db.delete_broker_holdings(broker_name)
                        for _, r in df_imp.iterrows():
                            ticker = str(r["ticker"]).strip()
                            if re.fullmatch(r"[A-Za-z0-9.]+", ticker):
                                ticker = ticker.upper()
                            db.upsert_holding(
                                ticker, str(r.get("name", ticker)),
                                str(r.get("asset_type", "日本株")), broker_name,
                                float(r.get("shares", 0)), float(r.get("avg_cost", 0)),
                                str(r.get("currency", "JPY")),
                                float(r.get("manual_price", 0)), "")
                        st.cache_data.clear()
                        st.success(f"{len(df_imp)} 件インポートしました。")
                        st.rerun()
            except Exception as e:
                st.error(f"読み込みエラー: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# タブ: 取引入力
# ═══════════════════════════════════════════════════════════════════════════════
def tab_transactions():
    st.subheader("📝 取引入力")

    tab_a, tab_b = st.tabs(["取引を記録", "取引履歴"])

    with tab_a:
        holdings = db.get_holdings()
        if holdings.empty:
            st.warning("銘柄を先に登録してください。")
            return

        with st.form("txn_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                txn_t    = st.selectbox("銘柄", holdings["ticker"].unique().tolist(),
                                        format_func=lambda t: f"{t}（{holdings.loc[holdings['ticker']==t,'name'].values[0]}）")
                txn_type = st.radio("売買区分", ["買い", "売り"], horizontal=True)
                txn_date = st.date_input("取引日", value=date.today())
            with c2:
                txn_shares   = st.number_input("株数", min_value=0.0, step=1.0)
                txn_price    = st.number_input("単価", min_value=0.0, step=0.01)
                txn_fee      = st.number_input("手数料", min_value=0.0, value=0.0, step=1.0)
                txn_currency = st.selectbox("通貨", CURRENCIES, key="txn_cur")
            txn_notes = st.text_input("メモ")
            sub = st.form_submit_button("記録する", type="primary")

        if sub:
            if txn_shares <= 0 or txn_price <= 0:
                st.error("株数・単価を正しく入力してください。")
            else:
                db.add_transaction(txn_t, str(txn_date), txn_type,
                                   txn_shares, txn_price, txn_fee,
                                   txn_currency, notes=txn_notes)
                st.success(f"{txn_t} {txn_type} {txn_shares}株 @ {txn_price} を記録しました。")
                st.rerun()

    with tab_b:
        txns = db.get_transactions()
        if txns.empty:
            st.info("取引履歴はありません。")
        else:
            st.dataframe(txns, use_container_width=True, height=420)
            csv = txns.to_csv(index=False).encode("utf-8-sig")
            st.download_button("CSVダウンロード", csv, "transactions.csv", "text/csv")


# ═══════════════════════════════════════════════════════════════════════════════
# タブ: 配当受取
# ═══════════════════════════════════════════════════════════════════════════════
def tab_dividends_recv():
    st.subheader("💵 配当受取入力")

    tab_a, tab_b = st.tabs(["配当を記録", "受取履歴"])

    with tab_a:
        holdings = db.get_holdings()
        if holdings.empty:
            st.warning("銘柄を先に登録してください。")
            return

        with st.form("div_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                div_t    = st.selectbox("銘柄", holdings["ticker"].unique().tolist(),
                                        format_func=lambda t: f"{t}（{holdings.loc[holdings['ticker']==t,'name'].values[0]}）",
                                        key="div_ticker")
                div_date = st.date_input("受取日", value=date.today(), key="div_date")
                div_fy   = st.text_input("会計年度", placeholder="例: FY25 / 2025")
            with c2:
                div_dps = st.number_input("1株あたり配当", min_value=0.0, step=0.01, key="div_dps")
                sel_row  = holdings[holdings["ticker"] == div_t]
                auto_sh  = float(sel_row["shares"].values[0]) if not sel_row.empty else 0.0
                div_sh   = st.number_input("株数", value=auto_sh, min_value=0.0, step=1.0, key="div_sh")
                div_cur  = st.selectbox("通貨", CURRENCIES, key="div_cur")
                div_note = st.text_input("メモ", key="div_note")

            div_total = div_dps * div_sh
            st.info(f"受取合計額（概算）: {div_total:,.2f} {div_cur}")
            sub = st.form_submit_button("記録する", type="primary")

        if sub:
            if div_dps <= 0:
                st.error("1株あたり配当を入力してください。")
            else:
                db.add_dividend_received(div_t, str(div_date), div_dps,
                                         div_total, div_cur, div_fy, div_note)
                st.success(f"{div_t} 配当 {div_total:,.2f} {div_cur} を記録しました。")
                st.rerun()

    with tab_b:
        divs = db.get_dividends_received()
        if divs.empty:
            st.info("受取履歴はありません。")
        else:
            divs["date"] = pd.to_datetime(divs["date"])
            divs["year"] = divs["date"].dt.year
            by_year = divs.groupby("year")["total_amount"].sum().reset_index()
            by_year.columns = ["年", "合計受取(円)"]
            st.markdown("**年間受取合計（円）**")
            st.dataframe(by_year, use_container_width=True, height=150)
            st.dataframe(divs.drop(columns=["year"]), use_container_width=True, height=320)
            csv = divs.drop(columns=["year"]).to_csv(index=False).encode("utf-8-sig")
            st.download_button("CSVダウンロード", csv, "dividends_received.csv", "text/csv")


# ═══════════════════════════════════════════════════════════════════════════════
# タブ: 配当履歴手動入力
# ═══════════════════════════════════════════════════════════════════════════════
def tab_manual_div_history():
    st.subheader("✏️ 配当履歴の手動入力")
    st.caption("投資信託・yfinance非対応銘柄の配当実績/予想を手動で登録できます。")

    holdings = db.get_holdings()
    if holdings.empty:
        st.warning("銘柄を先に登録してください。")
        return

    with st.form("manual_div_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            man_t = st.selectbox("銘柄", holdings["ticker"].unique().tolist(),
                                 format_func=lambda t: f"{t}（{holdings.loc[holdings['ticker']==t,'name'].values[0]}）")
        with c2:
            man_fy = st.text_input("会計年度", placeholder="例: FY25 / 2025")
        with c3:
            man_dps = st.number_input("1株配当", min_value=0.0, step=0.01)
        sub = st.form_submit_button("登録", type="primary")

    if sub:
        if not man_fy or man_dps <= 0:
            st.error("会計年度と配当金額を入力してください。")
        else:
            db.upsert_div_history(man_t, man_fy.strip(), man_dps, source="manual")
            st.success(f"{man_t} {man_fy} {man_dps:.2f} を登録しました。")
            st.rerun()

    st.divider()
    hist = db.get_div_history()
    if not hist.empty:
        st.dataframe(hist, use_container_width=True, height=350)

        st.markdown("#### エントリーを削除")
        c1, c2 = st.columns(2)
        with c1:
            del_ticker = st.selectbox("銘柄コード", sorted(hist["ticker"].unique()), key="del_hist_t")
        with c2:
            fy_opts = sorted(hist[hist["ticker"] == del_ticker]["fiscal_year"].tolist())
            del_fy  = st.selectbox("会計年度", fy_opts, key="del_hist_fy")
        if st.button("削除", type="secondary", key="del_hist_btn"):
            db.delete_div_history(del_ticker, del_fy)
            st.success(f"{del_ticker} {del_fy} を削除しました。")
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# 自動更新（方式A: アプリ起動時に、配当データが古ければ自動取得）
# ═══════════════════════════════════════════════════════════════════════════════
def maybe_auto_refresh_dividends():
    """配当データが DIV_REFRESH_DAYS 日以上古ければ自動取得する（セッション中1回）。"""
    if st.session_state.get("_div_auto_checked"):
        return
    st.session_state["_div_auto_checked"] = True

    holdings = db.get_holdings()
    if holdings.empty:
        return

    last = db.get_meta("div_last_fetched")
    need = True
    if last:
        try:
            last_date = datetime.strptime(last, "%Y-%m-%d").date()
            need = (date.today() - last_date).days >= DIV_REFRESH_DAYS
        except ValueError:
            need = True

    if need:
        with st.spinner("配当データを自動更新中...（初回は少し時間がかかります）"):
            _fetch_and_store_dividends(holdings)
            db.set_meta("div_last_fetched", date.today().isoformat())
        st.cache_data.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# メイン
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    st.title("📈 配当・資産管理アプリ")

    maybe_auto_refresh_dividends()

    col_r, col_note = st.columns([1, 6])
    with col_r:
        if st.button("🔄 データ更新"):
            st.cache_data.clear()
            st.rerun()
    with col_note:
        last = db.get_meta("div_last_fetched")
        if last:
            st.caption(f"配当データ最終取得: {last}（{DIV_REFRESH_DAYS}日ごとに自動更新／株価は5分毎に自動）")

    tabs = st.tabs([
        "📊 ダッシュボード",
        "💼 ポートフォリオ",
        "💰 配当・増配率",
        "🏦 銘柄管理",
        "📝 取引入力",
        "💵 配当受取",
        "✏️ 配当履歴入力",
    ])

    with tabs[0]: tab_dashboard()
    with tabs[1]: tab_portfolio()
    with tabs[2]: tab_dividend_growth()
    with tabs[3]: tab_manage_holdings()
    with tabs[4]: tab_transactions()
    with tabs[5]: tab_dividends_recv()
    with tabs[6]: tab_manual_div_history()


if __name__ == "__main__":
    main()
