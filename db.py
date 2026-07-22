# -*- coding: utf-8 -*-
import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials
from datetime import datetime

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = {
    "holdings": [
        "ticker", "name", "asset_type", "broker", "shares",
        "avg_cost", "currency", "manual_price", "notes",
    ],
    "transactions": [
        "id", "ticker", "date", "type", "shares",
        "price", "fee", "currency", "notes",
    ],
    "dividends_received": [
        "id", "ticker", "date", "amount_per_share",
        "total_amount", "currency", "fiscal_year", "notes",
    ],
    "div_history": ["ticker", "fiscal_year", "dps", "source"],
    "asset_history": ["date", "total_value_jpy"],
    "meta": ["key", "value"],
}

NUMERIC_COLS = {
    "holdings": ["shares", "avg_cost", "manual_price"],
    "transactions": ["shares", "price", "fee"],
    "dividends_received": ["amount_per_share", "total_amount"],
    "div_history": ["dps"],
    "asset_history": ["total_value_jpy"],
}


@st.cache_resource
def _get_client():
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=SCOPES
    )
    return gspread.authorize(creds)


@st.cache_resource
def _get_ss():
    return _get_client().open_by_key(st.secrets["sheets"]["spreadsheet_id"])


def _get_ws(name: str):
    ss = _get_ss()
    try:
        return ss.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=name, rows=2000, cols=20)
        ws.append_row(HEADERS[name])
        return ws


@st.cache_data(ttl=30)
def _read_sheet(name: str) -> pd.DataFrame:
    import time
    for attempt in range(3):
        try:
            ws = _get_ws(name)
            records = ws.get_all_records(expected_headers=HEADERS[name])
            break
        except Exception:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                return pd.DataFrame(columns=HEADERS[name])
    if not records:
        return pd.DataFrame(columns=HEADERS[name])
    df = pd.DataFrame(records)
    for col in NUMERIC_COLS.get(name, []):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].astype(str).str.strip()
    return df


def _clear_cache():
    _read_sheet.clear()


def _new_id() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S%f")


def _pad_body(rows, width, old_row_count):
    """各行を width 列に揃え、元の行数まで空行で埋める。

    clear() せずに1回のupdateで上書きしきるためのヘルパー。
    （clear+updateの2回書き込みは、途中で失敗するとデータが消える危険がある）
    """
    body = []
    for r in rows:
        r = [("" if c is None else c) for c in list(r)[:width]]
        r += [""] * (width - len(r))
        body.append(r)
    while len(body) < old_row_count:
        body.append([""] * width)
    return body


def _migrate_holdings_sheet():
    """broker列がなければholdingsシートをリセット"""
    ws = _get_ws("holdings")
    all_vals = ws.get_all_values()
    if all_vals and "broker" not in all_vals[0]:
        ws.clear()
        ws.append_row(HEADERS["holdings"])
        _clear_cache()


@st.cache_resource
def init_db():
    """シート初期化。毎rerunで走るとSheets APIを浪費するため1回だけ実行する。
    一時的なAPIエラー（クォータ超過等）でアプリ全体が落ちないよう握りつぶす
    （各read関数はリトライ＋空DataFrameのフォールバックを持つ）。"""
    try:
        for name in HEADERS:
            _get_ws(name)
        _migrate_holdings_sheet()
    except Exception:
        pass
    return True


# ─── Holdings ────────────────────────────────────────────────────────────────

def get_holdings() -> pd.DataFrame:
    """証券会社ごとの生データ（複数行同一ティッカーあり）"""
    return _read_sheet("holdings")


def get_holdings_aggregated() -> pd.DataFrame:
    """同一ティッカーを合算（表示・計算用）"""
    df = get_holdings()
    if df.empty:
        return df
    result = []
    for ticker, group in df.groupby("ticker", sort=False):
        total_shares = group["shares"].sum()
        weighted_avg = (
            (group["shares"] * group["avg_cost"]).sum() / total_shares
            if total_shares > 0 else 0.0
        )
        first = group.iloc[0]
        brokers = "\u30fb".join(str(b) for b in group["broker"].unique())
        result.append({
            "ticker": ticker,
            "name": first["name"],
            "asset_type": first["asset_type"],
            "broker": brokers,
            "shares": total_shares,
            "avg_cost": weighted_avg,
            "currency": first["currency"],
            "manual_price": first["manual_price"],
            "notes": first["notes"],
        })
    return pd.DataFrame(result)


def upsert_holding(ticker, name, asset_type, broker, shares, avg_cost,
                   currency, manual_price=0, notes=""):
    """(ticker, broker) の組み合わせでupsert"""
    ws = _get_ws("holdings")
    new_row = [ticker, name, asset_type, broker, float(shares), float(avg_cost),
               currency, float(manual_price or 0), notes]

    all_vals = ws.get_all_values()
    if len(all_vals) <= 1:
        ws.append_row(new_row)
        _clear_cache()
        return

    headers = all_vals[0]
    try:
        tcol = headers.index("ticker")
        bcol = headers.index("broker")
    except ValueError:
        ws.append_row(new_row)
        _clear_cache()
        return

    found_row = None
    for i, row in enumerate(all_vals[1:], start=2):
        if len(row) > max(tcol, bcol) and row[tcol] == ticker and row[bcol] == broker:
            found_row = i
            break

    if found_row:
        col_end = chr(ord("A") + len(HEADERS["holdings"]) - 1)
        ws.update(f"A{found_row}:{col_end}{found_row}", [new_row])
    else:
        ws.append_row(new_row)
    _clear_cache()


def delete_holding(ticker, broker=None):
    """ticker（とbroker）の行を削除"""
    ws = _get_ws("holdings")
    all_vals = ws.get_all_values()
    if len(all_vals) <= 1:
        return
    headers = all_vals[0]
    try:
        tcol = headers.index("ticker")
    except ValueError:
        return
    broker_col = headers.index("broker") if "broker" in headers else None

    rows_to_delete = []
    for i, row in enumerate(all_vals[1:], start=2):
        if len(row) > tcol and row[tcol] == ticker:
            if broker is None or broker_col is None:
                rows_to_delete.append(i)
            elif len(row) > broker_col and row[broker_col] == broker:
                rows_to_delete.append(i)

    for row_num in reversed(rows_to_delete):
        ws.delete_rows(row_num)
    _clear_cache()


def bulk_upsert_holdings(broker: str, records, replace: bool = True):
    """CSVインポート用の一括書き込み。

    1件ずつ upsert すると (全行読み込み+書き込み) × 件数 で Sheets API の
    クォータ(読み取り60回/分)を超えるため、読み1回・書き2回にまとめる。

    replace=True  : 指定brokerの既存行をすべて置き換える
    replace=False : 同一 (ticker, broker) の行だけ差し替え、他は残す
    """
    ws = _get_ws("holdings")
    all_vals = ws.get_all_values()
    headers = all_vals[0] if all_vals else list(HEADERS["holdings"])
    try:
        tcol = headers.index("ticker")
        bcol = headers.index("broker")
    except ValueError:
        tcol, bcol = 0, 3

    incoming = {str(r["ticker"]).strip() for r in records}
    kept = []
    for row in all_vals[1:]:
        if not row or not any(str(c).strip() for c in row):
            continue
        is_target_broker = len(row) > bcol and row[bcol] == broker
        if is_target_broker:
            if replace:
                continue
            if len(row) > tcol and row[tcol] in incoming:
                continue
        kept.append(row)

    new_rows = [[
        str(r["ticker"]).strip(), str(r.get("name", r["ticker"])),
        str(r.get("asset_type", "日本株")), broker,
        float(r.get("shares", 0)), float(r.get("avg_cost", 0)),
        str(r.get("currency", "JPY")), float(r.get("manual_price") or 0),
        str(r.get("notes", "")),
    ] for r in records]

    body = _pad_body([list(HEADERS["holdings"])] + kept + new_rows,
                     len(HEADERS["holdings"]), len(all_vals))
    ws.update(values=body, range_name="A1")
    _clear_cache()
    return len(new_rows)


def delete_broker_holdings(broker: str):
    """指定した証券会社の全保有銘柄を削除"""
    ws = _get_ws("holdings")
    all_vals = ws.get_all_values()
    if len(all_vals) <= 1:
        return
    headers = all_vals[0]
    if "broker" not in headers:
        return
    bcol = headers.index("broker")

    rows_to_delete = []
    for i, row in enumerate(all_vals[1:], start=2):
        if len(row) > bcol and row[bcol] == broker:
            rows_to_delete.append(i)

    for row_num in reversed(rows_to_delete):
        ws.delete_rows(row_num)
    _clear_cache()


# ─── Transactions ────────────────────────────────────────────────────────────

def add_transaction(ticker, date, type_, shares, price, fee, currency,
                    broker="\u624b\u52d5", notes=""):
    ws = _get_ws("transactions")
    ws.append_row([_new_id(), ticker, date, type_, float(shares),
                   float(price), float(fee), currency, notes])

    holdings_df = get_holdings()
    rows = holdings_df[holdings_df["ticker"] == ticker]
    if not rows.empty:
        broker_rows = rows[rows["broker"] == broker]
        r = broker_rows.iloc[0] if not broker_rows.empty else rows.iloc[0]
        use_broker = r["broker"]
        old_shares = float(r["shares"])
        old_cost = float(r["avg_cost"])
        if type_ == "\u8cb7\u3044":
            new_shares = old_shares + shares
            new_cost = (
                (old_shares * old_cost + shares * price) / new_shares
                if new_shares > 0 else price
            )
        elif type_ == "\u58f2\u308a":
            new_shares = max(0.0, old_shares - shares)
            new_cost = old_cost
        else:
            new_shares, new_cost = old_shares, old_cost
        upsert_holding(ticker, r["name"], r["asset_type"], use_broker,
                       new_shares, new_cost, r["currency"],
                       float(r["manual_price"]), r["notes"])
    _clear_cache()


def get_transactions() -> pd.DataFrame:
    return _read_sheet("transactions")


# ─── Dividends Received ──────────────────────────────────────────────────────

def add_dividend_received(ticker, date, amount_per_share, total_amount,
                          currency, fiscal_year, notes=""):
    ws = _get_ws("dividends_received")
    ws.append_row([_new_id(), ticker, date, float(amount_per_share),
                   float(total_amount), currency, fiscal_year, notes])
    _clear_cache()


def get_dividends_received() -> pd.DataFrame:
    return _read_sheet("dividends_received")


# ─── Dividend History ────────────────────────────────────────────────────────

def upsert_div_history(ticker, fiscal_year, dps, source="yfinance"):
    ws = _get_ws("div_history")
    all_vals = ws.get_all_values()

    found_row = None
    if len(all_vals) > 1:
        for i, row in enumerate(all_vals[1:], start=2):
            if len(row) >= 2 and row[0] == ticker and row[1] == fiscal_year:
                found_row = i
                break

    new_row = [ticker, fiscal_year, float(dps), source]
    if found_row:
        ws.update(f"A{found_row}:D{found_row}", [new_row])
    else:
        ws.append_row(new_row)
    _clear_cache()


def bulk_upsert_div_history(entries):
    """配当履歴の一括書き込み。entries: [(ticker, fiscal_year, dps, source), ...]

    1件ずつ upsert すると銘柄×年度の回数だけAPIを叩きクォータを超過するため、
    読み1回・書き2回にまとめる。
    """
    if not entries:
        return 0
    ws = _get_ws("div_history")
    all_vals = ws.get_all_values()
    merged = {}
    for row in all_vals[1:]:
        if len(row) >= 2 and str(row[0]).strip():
            r = list(row[:4])
            r += [""] * (4 - len(r))
            merged[(r[0], r[1])] = r
    for ticker, fy, dps, source in entries:
        merged[(ticker, fy)] = [ticker, fy, float(dps), source]

    body = _pad_body([list(HEADERS["div_history"])] + list(merged.values()),
                     len(HEADERS["div_history"]), len(all_vals))
    ws.update(values=body, range_name="A1")
    _clear_cache()
    return len(entries)


def get_div_history() -> pd.DataFrame:
    return _read_sheet("div_history")


def delete_div_history(ticker: str, fiscal_year: str):
    ws = _get_ws("div_history")
    all_vals = ws.get_all_values()
    if len(all_vals) <= 1:
        return
    for i, row in enumerate(all_vals[1:], start=2):
        if len(row) >= 2 and row[0] == ticker and row[1] == fiscal_year:
            ws.delete_rows(i)
            _clear_cache()
            return


def get_div_pivot() -> pd.DataFrame:
    df = get_div_history()
    if df.empty:
        return pd.DataFrame()
    return df.pivot_table(
        index="ticker", columns="fiscal_year", values="dps", aggfunc="last"
    )


# ─── Asset History（資産推移スナップショット） ──────────────────────────────

def record_asset_snapshot(date_str: str, total_value_jpy: float):
    """同じ日付の行があれば上書き、なければ追記する。"""
    ws = _get_ws("asset_history")
    all_vals = ws.get_all_values()
    found_row = None
    if len(all_vals) > 1:
        for i, row in enumerate(all_vals[1:], start=2):
            if len(row) >= 1 and row[0] == date_str:
                found_row = i
                break
    new_row = [date_str, float(total_value_jpy)]
    if found_row:
        ws.update(f"A{found_row}:B{found_row}", [new_row])
    else:
        ws.append_row(new_row)
    _clear_cache()


def get_asset_history() -> pd.DataFrame:
    return _read_sheet("asset_history")


# ─── Meta（key-value: 最終取得日などの状態保存） ────────────────────────────

def get_meta(key: str):
    """キャッシュ経由で読む（毎回のrerunでAPIを叩かないため）。"""
    df = _read_sheet("meta")
    if df.empty or "key" not in df.columns:
        return None
    hit = df[df["key"].astype(str) == key]
    return str(hit.iloc[0]["value"]) if not hit.empty else None


def set_meta(key: str, value: str):
    ws = _get_ws("meta")
    all_vals = ws.get_all_values()
    found_row = None
    for i, row in enumerate(all_vals[1:], start=2):
        if len(row) >= 1 and row[0] == key:
            found_row = i
            break
    if found_row:
        ws.update(f"A{found_row}:B{found_row}", [[key, str(value)]])
    else:
        ws.append_row([key, str(value)])
    _clear_cache()
