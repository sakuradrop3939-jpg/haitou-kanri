# -*- coding: utf-8 -*-
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import streamlit as st
from datetime import datetime, date


# ─── 為替レート ────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def get_usd_jpy() -> float:
    try:
        hist = yf.Ticker("USDJPY=X").history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return 155.0


# ─── 現在株価 ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def get_current_price(ticker: str) -> float | None:
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


@st.cache_data(ttl=300)
def get_prices_bulk(tickers: tuple) -> dict:
    """複数銘柄の現在価格を一括取得"""
    result = {}
    if not tickers:
        return result
    try:
        data = yf.download(
            list(tickers), period="5d", auto_adjust=True,
            progress=False, threads=True
        )
        close = data["Close"] if "Close" in data.columns else data
        for t in tickers:
            try:
                if len(tickers) == 1:
                    series = close
                else:
                    series = close[t]
                series = series.dropna()
                if not series.empty:
                    result[t] = float(series.iloc[-1])
            except Exception:
                pass
    except Exception:
        for t in tickers:
            result[t] = get_current_price(t)
    return result


# ─── 年間配当履歴（yfinance） ────────────────────────────────────────────
@st.cache_data(ttl=3600)
def get_annual_dividends(ticker: str, years: int = 6) -> dict:
    """
    yfinance から年間配当を取得。
    日本株: FY表記（FY23 = 2023年4月〜2024年3月）
    米国株: 暦年表記（2023, 2024, ...）
    戻り値: {"FY24": 80.0, "FY25": 90.0, ...} or {"2023": 1.5, ...}
    """
    try:
        divs = yf.Ticker(ticker).dividends
        if divs is None or divs.empty:
            return {}
        if divs.index.tz is not None:
            divs.index = divs.index.tz_localize(None)

        current_year = datetime.now().year
        result = {}

        if ticker.endswith(".T"):
            # 日本株: 4月〜翌3月を1会計年度とする
            for fy_start in range(current_year - years, current_year + 1):
                start = pd.Timestamp(f"{fy_start}-04-01")
                end = pd.Timestamp(f"{fy_start + 1}-03-31")
                subset = divs[(divs.index >= start) & (divs.index <= end)]
                if not subset.empty:
                    fy_label = f"FY{str(fy_start + 1)[2:]}"
                    result[fy_label] = round(float(subset.sum()), 2)
        else:
            # 米国株: 暦年
            for year in range(current_year - years, current_year + 1):
                start = pd.Timestamp(f"{year}-01-01")
                end = pd.Timestamp(f"{year}-12-31")
                subset = divs[(divs.index >= start) & (divs.index <= end)]
                if not subset.empty:
                    result[str(year)] = round(float(subset.sum()), 4)
        return result
    except Exception:
        return {}


# ─── 銘柄情報（会社名・通貨・配当利回り） ─────────────────────────────────
@st.cache_data(ttl=3600)
def get_company_info(ticker: str) -> dict:
    ticker = str(ticker)
    try:
        info = yf.Ticker(ticker).info
        return {
            "name": info.get("longName") or info.get("shortName", ticker),
            "sector": info.get("sector", ""),
            "currency": info.get("currency", "JPY"),
            "forward_dividend": info.get("dividendRate") or 0,
            "dividend_yield": (info.get("dividendYield") or 0) * 100,
        }
    except Exception:
        return {
            "name": ticker,
            "sector": "",
            "currency": "JPY" if ticker.endswith(".T") else "USD",
            "forward_dividend": 0,
            "dividend_yield": 0,
        }


# ─── 株探から日本株配当予想を取得 ─────────────────────────────────────────
@st.cache_data(ttl=3600)
def fetch_kabutan_forecast(code: str) -> dict:
    """
    株探の財務ページから配当予想を取得。
    code: 4桁の証券コード（例: "7203"）
    戻り値: {"FY25": 90.0, "FY26": 100.0, ...}
    """
    url = f"https://kabutan.jp/stock/finance?code={code}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120.0"
        )
    }
    result = {}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        current_year = datetime.now().year
        # FY期間マッピング（4月始まり）
        fy_ranges = {}
        for offset in range(-1, 4):
            fy_start = current_year + offset
            fy_label = f"FY{str(fy_start + 1)[2:]}"
            fy_ranges[fy_label] = (
                date(fy_start, 4, 1),
                date(fy_start + 1, 3, 31),
            )

        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = [
                    td.get_text(strip=True)
                    for td in row.find_all(["td", "th"])
                ]
                if len(cells) < 5:
                    continue
                m = re.match(r"^(\d{4})\.(\d{2})$", cells[1])
                if not m:
                    continue
                year, month = int(m.group(1)), int(m.group(2))
                try:
                    period_end = date(year, month, 1)
                except ValueError:
                    continue

                div_val = None
                for c in reversed(cells):
                    c_clean = (
                        c.replace("*", "").replace("#", "")
                        .replace(",", "").replace("円", "").strip()
                    )
                    try:
                        v = float(c_clean)
                        if 0 < v < 100_000:
                            div_val = v
                            break
                    except (ValueError, TypeError):
                        continue

                if div_val is not None:
                    for fy_label, (start, end) in fy_ranges.items():
                        if start <= period_end <= end:
                            result[fy_label] = div_val
    except Exception:
        pass
    return result


# ─── 増配率計算ユーティリティ ─────────────────────────────────────────────
def calc_growth_rate(new_val, old_val) -> float | None:
    try:
        if old_val and new_val and old_val != 0:
            return round((new_val - old_val) / abs(old_val) * 100, 1)
    except Exception:
        pass
    return None
