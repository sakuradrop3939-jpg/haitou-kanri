# -*- coding: utf-8 -*-
"""
Google Sheets をデータベースとして使用するモジュール。
SQLite版の db.py と同じインターフェースを提供する。
"""

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
        "ticker", "name", "asset_type", "shares",
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
}

NUMERIC_COLS = {
    "holdings": ["shares", "avg_cost", "manual_price"],
    "transactions": ["shares", "price", "fee"],
    "dividends_received": ["amount_per_share", "total_amount"],
    "div_history": ["dps"],
}


# ─── 接続 ────────────────────────────────────────────────────────────────────
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


# ─── 読み込み（キャッシュ付き） ──────────────────────────────────────────────
@st.cache_data(ttl=30)
def _read_sheet(name: str) -> pd.DataFrame:
    ws = _get_ws(name)
    records = ws.get_all_records(expected_headers=HEADERS[name])
    if not records:
        return pd.DataFrame(columns=HEADERS[name])
    df = pd.DataFrame(records)
    for col in NUMERIC_COLS.get(name, []):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].astype(str)
    return df


def _clear_cache():
    _read_sheet.clear()


def _new_id() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S%f")


# ─── 初期化 ──────────────────────────────────────────────────────────────────
def init_db():
    """全シートを初期化（存在しない場合のみ作成）"""
    for name in HEADERS:
        _get_ws(name)


# ─── Holdings ────────────────────────────────────────────────────────────────
def get_holdings() -> pd.DataFrame:
    return _read_sheet("holdings")


def upsert_holding(ticker, name, asset_type, shares, avg_cost,
                   currency, manual_price=0, notes=""):
    ws = _get_ws("holdings")
    new_row = [ticker, name, asset_type, float(shares), float(avg_cost),
               currency, float(manual_price or 0), notes]

    all_vals = ws.get_all_values()
    if len(all_vals) <= 1:
        ws.append_row(new_row)
        _clear_cache()
        return

    headers = all_vals[0]
    try:
        tcol = headers.index("ticker") + 1
    except ValueError:
        ws.append_row(new_row)
        _clear_cache()
        return

    cell = ws.find(ticker, in_column=tcol)
    if cell:
        col_end = chr(ord("A") + len(HEADERS["holdings"]) - 1)
        ws.update(f"A{cell.row}:{col_end}{cell.row}", [new_row])
    else:
        ws.append_row(new_row)
    _clear_cache()


def delete_holding(ticker):
    ws = _get_ws("holdings")
    all_vals = ws.get_all_values()
    if len(all_vals) <= 1:
        return
    headers = all_vals[0]
    try:
        tcol = headers.index("ticker") + 1
    except ValueError:
        return
    cell = ws.find(ticker, in_column=tcol)
    if cell:
        ws.delete_rows(cell.row)
    _clear_cache()


# ─── Transactions ────────────────────────────────────────────────────────────
def add_transaction(ticker, date, type_, shares, price, fee, currency, notes=""):
    ws = _get_ws("transactions")
    ws.append_row([_new_id(), ticker, date, type_, float(shares),
                   float(price), float(fee), currency, notes])

    # 保有株数・平均取得単価を更新
    holdings_df = get_holdings()
    row = holdings_df[holdings_df["ticker"] == ticker]
    if not row.empty:
        r = row.iloc[0]
        old_shares = float(r["shares"])
        old_cost = float(r["avg_cost"])
        if type_ == "買い":
            new_shares = old_shares + shares
            new_cost = (
                (old_shares * old_cost + shares * price) / new_shares
                if new_shares > 0 else price
            )
        elif type_ == "売り":
            new_shares = max(0.0, old_shares - shares)
            new_cost = old_cost
        else:
            new_shares, new_cost = old_shares, old_cost
        upsert_holding(
            ticker, r["name"], r["asset_type"],
            new_shares, new_cost, r["currency"],
            float(r["manual_price"]), r["notes"],
        )
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
    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].astype(str)
    return df.pivot_table(
        index="ticker", columns="fiscal_year", values="dps", aggfunc="last"
    )
