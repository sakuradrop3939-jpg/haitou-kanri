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

        info    = fetcher.get_company_info(t)
        fwd_div = float(info.get("forward_dividend") or 0)
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

        info      = fetcher.get_company_info(t)
        fwd_div   = float(info.get("forward_dividend") or 0)
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
                st.caption(f"増配率　{rates}")

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
            options = holdings["ticker"].tolist()
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
                db.upsert_holding(new_ticker.strip().upper(), new_name,
                                  new_type, new_shares, new_avg_cost,
                                  new_currency, new_manual, new_notes)
                st.success(f"{new_ticker.strip().upper()}（{new_name}）を登録しました。")
                st.rerun()

        st.divider()
        st.markdown("#### CSV一括インポート")
        st.caption("SBI証券・楽天証券CSV または ticker,name,asset_type,shares,avg_cost,currency 形式")
        uploaded = st.file_uploader("CSVを選択", type=["csv"])
        if uploaded:
            try:
                import io as _io, csv as _csv
                raw_bytes = uploaded.getvalue()
                df_imp = None
                for enc in ["cp932", "shift-jis", "utf-8-sig", "utf-8"]:
                    try:
                        raw = raw_bytes.decode(enc)
                        ls = raw.splitlines()
                        hrow = None
                        is_rakuten = False
                        for i, line in enumerate(ls):
                            if "保有" in line and "取得" in line:
                                hrow = i
                                is_rakuten = True
                                break
                            elif "取得単価" in line:
                                hrow = i
                                is_rakuten = False
                                break
                        if hrow is not None:
                            reader = _csv.reader(ls[hrow:])
                            next(reader)
                            recs = []
                            for row in reader:
                                if not row or not any(r.strip() for r in row): continue
                                try:
                                    if is_rakuten:
                                        code = str(row[1]).strip()
                                        name = str(row[2]).strip()
                                        if not name or name == "nan": continue
                                        sh = float(str(row[4]).replace(",",""))
                                        ac = float(str(row[6]).replace(",",""))
                                        if sh <= 0 or ac <= 0: continue
                                        if code and code.isdigit() and len(code) <= 4:
                                            ticker = code + ".T"
                                            atype = "日本株"
                                        elif code and code.strip():
                                            ticker = code
                                            atype = "外国株"
                                        else:
                                            continue
                                    else:
                                        c0 = str(row[0]).strip()
                                        if not c0 or c0 == "nan": continue
                                        pts = c0.split(None, 1)
                                        if not pts: continue
                                        code = pts[0].strip()
                                        name = pts[1].strip() if len(pts)>1 else code
                                        sh = float(str(row[2]).replace(",",""))
                                        ac = float(str(row[3]).replace(",",""))
                                        if sh <= 0 or ac <= 0: continue
                                        if len(code) <= 4 and code.isdigit():
                                            ticker = code + ".T"
                                            atype = "日本株"
                                        elif code.isdigit():
                                            ticker = code
                                            atype = "投資信託"
                                        else:
                                            continue
                                except Exception: continue
                                recs.append({"ticker":ticker,"name":name,"asset_type":atype,"shares":sh,"avg_cost":ac,"currency":"JPY"})
                            if recs:
                                df_imp = pd.DataFrame(recs)
                                break
                        else:
                            df_imp = pd.read_csv(_io.StringIO(raw))
                            break
                    except UnicodeDecodeError: continue
                if df_imp is None or df_imp.empty:
                    st.warning("データが見つかりませんでした")
                else:
                    st.dataframe(df_imp)
                    merge_mode = st.checkbox("既存データに追加する（同じ銘柄は株数を合算）", value=True)
                    if st.button("インポート実行"):
                        for _, r in df_imp.iterrows():
                        ticker = str(r["ticker"]).strip().upper()
                        if merge_mode:
                            ex = db.get_holdings()
                            ex_row = ex[ex["ticker"] == ticker]
                            if not ex_row.empty:
                                er = ex_row.iloc[0]
                                old_sh = float(er["shares"]); old_ac = float(er["avg_cost"])
                                new_sh = float(r.get("shares",0)); new_ac = float(r.get("avg_cost",0))
                                total_sh = old_sh + new_sh
                                merged_ac = (old_sh*old_ac + new_sh*new_ac)/total_sh if total_sh>0 else new_ac
                                db.upsert_holding(ticker,str(er["name"]),str(er["asset_type"]),total_sh,merged_ac,str(er["currency"]),float(er.get("manual_price",0)),str(er.get("notes","")))
                                continue
                        db.upsert_holding(ticker,str(r.get("name",ticker)),str(r.get("asset_type","日本株")),float(r.get("shares",0)),float(r.get("avg_cost",0)),str(r.get("currency","JPY")),float(r.get("manual_price",0)),str(r.get("notes","")))
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
                txn_t    = st.selectbox("銘柄", holdings["ticker"].tolist(),
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
                                   txn_currency, txn_notes)
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
                div_t    = st.selectbox("銘柄", holdings["ticker"].tolist(),
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
            man_t = st.selectbox("銘柄", holdings["ticker"].tolist(),
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
# メイン
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    st.title("📈 配当・資産管理アプリ")

    col_r, _ = st.columns([1, 6])
    with col_r:
        if st.button("🔄 データ更新"):
            st.cache_data.clear()
            st.rerun()

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
