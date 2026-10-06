import os
import random
import time
from datetime import datetime
from functools import wraps

import pytz
import requests
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file

import data_fetcher as df_lib
import ml_models as ml_lib
import db
import sms_service

app = Flask(__name__)
app.secret_key = "stocksense-ai-dev-secret-change-in-production"
# High security: sessions expire when browser is closed, requiring OTP authentication every visit
app.config["SESSION_PERMANENT"] = False

IST = pytz.timezone("Asia/Kolkata")

# In-memory cache for LSTM forecasts
FORECAST_CACHE = {}
FORECAST_CACHE_TTL = 60 * 60  # 1 hour

MARKET_INDICES = {
    "NIFTY 50": "^NSEI",
    "SENSEX": "^BSESN",
    "NASDAQ": "^IXIC",
    "S&P 500": "^GSPC",
}

SENTIMENT_BASKET = ["AAPL", "MSFT", "RELIANCE.NS", "TCS.NS", "NVDA"]


@app.context_processor
def inject_user():
    user = session.get("user")
    user_name = None
    if isinstance(user, dict):
        user_name = user.get("name") or user.get("username")
    elif isinstance(user, str):
        user_name = user
    return {"user": user, "user_name": user_name}


@app.context_processor
def inject_ticker_catalog():
    return {"ticker_catalog": df_lib.POPULAR_CATALOG}


@app.template_filter("format_inr")
def filter_format_inr(val):
    try:
        val = float(val)
    except (ValueError, TypeError):
        return val
    sign = "-" if val < 0 else ""
    abs_val = abs(val)
    s = f"{abs_val:.2f}"
    int_part, dec_part = s.split(".")
    if len(int_part) <= 3:
        formatted = int_part
    else:
        last3 = int_part[-3:]
        rest = int_part[:-3]
        groups = []
        while len(rest) > 2:
            groups.append(rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.append(rest)
        groups.reverse()
        formatted = ",".join(groups) + "," + last3
    return f"{sign}₹ {formatted}" if dec_part == "00" else f"{sign}₹ {formatted}.{dec_part}"


@app.template_filter("inr_words")
def filter_inr_words(val):
    try:
        val = float(val)
    except (ValueError, TypeError):
        return val
    sign = "-" if val < 0 else ""
    abs_val = abs(val)
    if abs_val >= 10000000:
        cr = abs_val / 10000000.0
        text = f"{cr:.2f}".rstrip("0").rstrip(".") + " Crore" + ("s" if cr > 1 else "")
    elif abs_val >= 100000:
        lk = abs_val / 100000.0
        text = f"{lk:.2f}".rstrip("0").rstrip(".") + " Lakhs"
    elif abs_val >= 1000:
        k = abs_val / 1000.0
        text = f"{k:.1f}".rstrip("0").rstrip(".") + " Thousand"
    else:
        text = f"{abs_val:.0f}"
    return f"{sign}{text}"


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = session.get("user")
        if not user:
            return redirect(url_for("login"))
        if not user.get("id"):
            existing = db.get_user_by_mobile(user.get("mobile", ""))
            if existing:
                user["id"] = existing["id"]
                session["user"] = user
            else:
                new_u = db.get_or_create_user(user.get("mobile", "9999999999"), user.get("name", "Trader"))
                user["id"] = new_u["id"]
                session["user"] = user
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# PUBLIC PAGES & AUTHENTICATION
# ---------------------------------------------------------------------------
@app.route("/")
def landing():
    if session.get("user") and session["user"].get("id"):
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


@app.route("/login")
def login():
    if session.get("user") and session["user"].get("id"):
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))


@app.route("/api/auth/send-otp", methods=["POST"])
def send_otp():
    data = request.get_json(force=True)
    raw_mobile = str(data.get("mobile", "")).strip()
    mobile = sms_service.normalize_phone_number(raw_mobile)
    if len(mobile) < 7:
        return jsonify({"ok": False, "error": "Enter a valid 10-digit mobile number."}), 400

    # Rate limiting: Maximum 10 OTP logins per 24 hours per mobile
    DAILY_LOGIN_LIMIT = 10
    daily_count = db.get_daily_otp_count(mobile)
    if daily_count >= DAILY_LOGIN_LIMIT:
        return jsonify({
            "ok": False,
            "error": f"Daily security limit reached: Maximum {DAILY_LOGIN_LIMIT} logins per day per mobile number. Please try again later."
        }), 429

    otp = f"{random.randint(100000, 999999)}"
    db.save_otp(mobile, otp, validity_minutes=5)

    ip = request.remote_addr or "127.0.0.1"
    ua = request.headers.get("User-Agent", "Unknown")[:255]
    db.log_login_attempt(mobile, status="OTP_SENT", ip_address=ip, user_agent=ua)

    # Dispatch via Real SMS Gateway (Fast2SMS / Twilio / 2Factor) or Fallback
    sms_res = sms_service.send_otp_sms(mobile, otp)
    existing_user = db.get_user_by_mobile(mobile)

    session["pending_mobile"] = mobile

    return jsonify({
        "ok": True,
        "is_existing_user": bool(existing_user),
        "user_name": existing_user["name"] if existing_user else None,
        "sent": sms_res.get("sent", False),
        "provider": sms_res.get("provider", "demo"),
        "demo_otp": sms_res.get("demo_otp"),
        "message": sms_res.get("message")
    })


@app.route("/api/auth/verify-otp", methods=["POST"])
def verify_otp():
    data = request.get_json(force=True)
    otp = str(data.get("otp", "")).strip()
    mobile = session.get("pending_mobile") or sms_service.normalize_phone_number(str(data.get("mobile", "")).strip())
    name = str(data.get("name", "")).strip()

    ip = request.remote_addr or "127.0.0.1"
    ua = request.headers.get("User-Agent", "Unknown")[:255]

    if not mobile:
        return jsonify({"ok": False, "error": "Session expired. Please request a new OTP."}), 400

    if not db.verify_otp(mobile, otp):
        db.log_login_attempt(mobile, status="FAILED_OTP", ip_address=ip, user_agent=ua)
        return jsonify({"ok": False, "error": "Incorrect or expired OTP. Please try again."}), 400

    # User verified! Fetch or create in SQLite database
    user = db.get_user_by_mobile(mobile)
    if user:
        db.update_user_last_login(user["id"])
        if name and name != user["name"]:
            db.update_user_name(user["id"], name)
            user["name"] = name
    else:
        user = db.create_user(mobile, name or "Trader")

    # Record successful login in security audit log
    db.log_login_attempt(mobile, status="SUCCESS", ip_address=ip, user_agent=ua, user_id=user["id"])

    # High-security non-permanent session
    session.permanent = False
    session["user"] = {"id": user["id"], "mobile": user["mobile"], "name": user["name"]}
    session.pop("pending_mobile", None)
    return jsonify({"ok": True, "redirect": url_for("dashboard"), "user": session["user"]})



# ---------------------------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    status = df_lib.check_market_status()
    now_ist = datetime.now(IST)

    scores = []
    for t in SENTIMENT_BASKET:
        try:
            _, avg_sent, _ = df_lib.get_stock_news_sentiment(t)
            scores.append(avg_sent)
        except Exception:
            pass
    avg_sentiment = round(sum(scores) / len(scores), 3) if scores else 0.0
    sentiment_label = "Bullish" if avg_sentiment >= 0.05 else ("Bearish" if avg_sentiment <= -0.05 else "Neutral")
    confidence = int(min(97, 55 + abs(avg_sentiment) * 400))

    index_data = {}
    for name, sym in MARKET_INDICES.items():
        d = df_lib.fetch_index_data(sym, period="1mo")
        if d:
            index_data[name] = d
    index_names = list(index_data.keys())

    return render_template(
        "dashboard.html",
        user_name=session["user"]["name"],
        now_ist=now_ist,
        nse_open=status["nse_open"], nyse_open=status["nyse_open"],
        sentiment_label=sentiment_label, avg_sentiment=avg_sentiment, confidence=confidence,
        index_names=index_names, index_data=index_data
    )


# ---------------------------------------------------------------------------
# COMPANY ANALYSIS & MANUAL TIME/RETURN PREDICTOR
# ---------------------------------------------------------------------------
@app.route("/company_analysis")
@login_required
def company_analysis():
    ticker = request.args.get("ticker", "AAPL").strip().upper()
    period = request.args.get("period", "6mo")

    # Manual Target Return & Duration settings (1 week, 1 month, 3 months, 6 months, 1 year)
    try:
        target_return = float(request.args.get("target_return", 10.0))
    except (TypeError, ValueError):
        target_return = 10.0
    try:
        horizon_days = int(request.args.get("days", 30))
    except (TypeError, ValueError):
        horizon_days = 30
    horizon_days = max(1, min(horizon_days, 365))

    stock_df, info = df_lib.fetch_stock_data(ticker, period=period)
    if stock_df is None or stock_df.empty:
        return render_template("company_analysis.html", found=False, ticker=ticker,
                                period=period, catalog=df_lib.POPULAR_CATALOG,
                                target_return=target_return, horizon_days=horizon_days)

    usd_inr = df_lib.get_usd_to_inr_rate()
    currency = info.get("currency", "USD")
    current_price = float(stock_df["Close"].iloc[-1])
    prev_close = float(stock_df["Close"].iloc[-2]) if len(stock_df) > 1 else current_price
    change = round(current_price - prev_close, 2)
    change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0
    price_usd = round(current_price if currency == "USD" else current_price / usd_inr, 2)
    price_inr = round(current_price if currency == "INR" else current_price * usd_inr, 2)

    prob, exp_return, volatility, signal = df_lib.calculate_return_probability(
        stock_df, target_return_pct=target_return, days=horizon_days
    )
    news, avg_sent, sent_tag = df_lib.get_stock_news_sentiment(ticker)

    rsi = df_lib.calculate_rsi(stock_df).fillna(50)
    latest_rsi = round(rsi.iloc[-1], 1) if len(rsi) else 50.0
    rsi_tag = "Overbought" if latest_rsi >= 70 else ("Oversold" if latest_rsi <= 30 else "Neutral")

    labels = [d.strftime("%d %b") for d in stock_df.index]
    prices = [round(p, 2) for p in stock_df["Close"].tolist()]

    backend_company = info.get("backend_company") or db.get_company_by_ticker(ticker)

    return render_template(
        "company_analysis.html", found=True, ticker=ticker, period=period, catalog=df_lib.POPULAR_CATALOG,
        name=info.get("longName", ticker), sector=info.get("sector", "N/A"),
        price_usd=price_usd, price_inr=price_inr, change=change, change_pct=change_pct, currency=currency,
        sent_tag=sent_tag, avg_sent=avg_sent, signal=signal, prob=prob,
        exp_return=exp_return, volatility=volatility,
        target_return=target_return, horizon_days=horizon_days,
        latest_rsi=latest_rsi, rsi_tag=rsi_tag,
        market_cap=info.get("marketCap", 0), pe=info.get("trailingPE", "N/A"),
        week_high=info.get("fiftyTwoWeekHigh", "N/A"), week_low=info.get("fiftyTwoWeekLow", "N/A"),
        news=news, labels=labels, prices=prices, backend_company=backend_company
    )



@app.route("/api/predict", methods=["POST"])
@login_required
def predict():
    """Dynamically recalculates probability & returns for custom target % and duration."""
    data = request.get_json(force=True)
    ticker = str(data.get("ticker", "")).strip().upper()
    try:
        target_return = float(data.get("target_return", 10.0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Target return must be a number."}), 400
    try:
        days = int(data.get("days", 30))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Duration must be a number of days."}), 400
    days = max(1, min(days, 365))

    if not ticker:
        return jsonify({"ok": False, "error": "Ticker is required."}), 400

    stock_df, _ = df_lib.fetch_stock_data(ticker, period="1y")
    if stock_df is None or stock_df.empty:
        return jsonify({"ok": False, "error": f"No data available for {ticker}."}), 400

    prob, exp_return, volatility, signal = df_lib.calculate_return_probability(
        stock_df, target_return_pct=target_return, days=days
    )
    return jsonify({
        "ok": True,
        "prob": round(prob, 1),
        "exp_return": round(exp_return, 2),
        "volatility": round(volatility, 2),
        "signal": signal,
    })


@app.route("/api/company/<path:ticker>")
@login_required
def api_company_details(ticker):
    """Returns stored backend institutional dossier for any requested ticker."""
    comp = db.get_company_by_ticker(ticker)
    if comp:
        return jsonify({"ok": True, "company": comp})
    return jsonify({"ok": False, "error": f"No stored backend dossier for ticker {ticker}"}), 404


@app.route("/api/companies/stored")
@login_required
def api_stored_companies():
    """Lists all permanently stored companies in the backend SQLite database."""
    comps = db.get_all_stored_companies()
    return jsonify({"ok": True, "count": len(comps), "companies": comps})


# ---------------------------------------------------------------------------
# COMPARE UP TO 3 COMPANIES SIDE-BY-SIDE

# ---------------------------------------------------------------------------
@app.route("/compare")
@login_required
def compare():
    tickers = [t.strip().upper() for t in request.args.getlist("ticker") if t.strip()]
    if not tickers:
        raw = request.args.get("tickers", "")
        tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
    tickers = tickers[:3]
    compare_data = df_lib.compare_stocks(tickers) if tickers else {"results": [], "chart_data": None, "highlights": {}}
    return render_template(
        "compare.html",
        results=compare_data.get("results", []),
        chart_data=compare_data.get("chart_data"),
        highlights=compare_data.get("highlights", {}),
        tickers=tickers,
        catalog=df_lib.POPULAR_CATALOG
    )


# ---------------------------------------------------------------------------
# AI FORECAST - DEEP LEARNING (LSTM SEQUENTIAL PREDICTOR)
# ---------------------------------------------------------------------------
@app.route("/forecast")
@login_required
def forecast_page():
    ticker = request.args.get("ticker", "AAPL").strip().upper()
    return render_template(
        "forecast.html",
        ticker=ticker,
        catalog=df_lib.POPULAR_CATALOG,
        tf_available=ml_lib.TENSORFLOW_AVAILABLE,
    )


@app.route("/api/forecast/lstm", methods=["POST"])
@login_required
def lstm_forecast_api():
    data = request.get_json(force=True)
    ticker = str(data.get("ticker", "")).strip().upper()
    horizon = str(data.get("horizon", "30d")).strip().lower()
    force_refresh = bool(data.get("refresh", False))

    if not ticker:
        return jsonify({"ok": False, "error": "Ticker is required."}), 400

    cache_key = f"{ticker}_{horizon}"
    cached = FORECAST_CACHE.get(cache_key)
    if cached and not force_refresh and (time.time() - cached["ts"] < FORECAST_CACHE_TTL):
        return jsonify({**cached["data"], "cached": True})

    stock_df, info = df_lib.fetch_stock_data(ticker, period="2y")
    if stock_df is None or stock_df.empty:
        return jsonify({"ok": False, "error": f"No historical data found for {ticker}."}), 400

    prices = stock_df["Close"].tolist()

    # News sentiment & technical indicators
    _, avg_sent, sent_tag = df_lib.get_stock_news_sentiment(ticker)
    rsi_series = df_lib.calculate_rsi(stock_df).fillna(50)
    latest_rsi = float(rsi_series.iloc[-1]) if len(rsi_series) else 50.0

    lstm_result = ml_lib.run_lstm_forecast(
        ticker, prices, epochs=12, horizon=horizon, rsi_val=latest_rsi, sent_tag=sent_tag
    )
    if not lstm_result.get("ok"):
        return jsonify(lstm_result), 400

    prob, _, volatility, _ = df_lib.calculate_return_probability(
        stock_df, target_return_pct=max(abs(lstm_result["growth_pct"]), 5.0), days=30
    )

    ensemble = ml_lib.generate_ml_signal(
        lstm_pred=lstm_result["growth_pct"],
        sentiment_score=avg_sent,
        rsi_value=latest_rsi,
        prob_of_return=prob,
    )

    result = {
        **lstm_result,
        "name": info.get("longName", ticker),
        "currency": info.get("currency", "USD"),
        "pe": info.get("trailingPE", "N/A"),
        "volatility": round(volatility, 2),
        "sent_tag": sent_tag,
        "ensemble": ensemble,
    }
    FORECAST_CACHE[cache_key] = {"data": result, "ts": time.time()}
    return jsonify(result)


# ---------------------------------------------------------------------------
# INVESTMENT PLANNER & PORTFOLIO STUDIO
# ---------------------------------------------------------------------------
@app.route("/planner")
@login_required
def planner():
    return render_template("planner.html")


@app.route("/api/planner/required-return", methods=["POST"])
@login_required
def api_required_return():
    """
    Feature 1: Solves for required return & portfolio mix either directly
    or reverse-engineered from { target_corpus, monthly_sip, years }.
    """
    data = request.get_json(force=True)
    if "target_corpus" in data and "monthly_sip" in data:
        target_corpus = float(data.get("target_corpus", 0))
        monthly_sip = float(data.get("monthly_sip", 0))
        years = int(data.get("years", 5))
        result = df_lib.calculate_required_return_from_goal(target_corpus, monthly_sip, years)
        return jsonify(result)
    else:
        target_return_pct = float(data.get("target_return_pct", 12.0))
        result = df_lib.find_required_portfolio_mix(target_return_pct)
        return jsonify({"ok": True, **result})


@app.route("/api/planner/model-portfolio", methods=["POST"])
@login_required
def api_model_portfolio():
    """
    Feature 2: Builds 7-category dynamic model portfolio with real tickers, exact rupee split, and compounding growth.
    """
    data = request.get_json(force=True)
    amount = float(data.get("amount", 100000))
    tier = str(data.get("tier", "moderate")).lower()
    years = int(data.get("years", 5))
    goal = str(data.get("goal", "wealth")).lower()
    result = df_lib.build_model_portfolio(amount, tier=tier, years=years, goal=goal)
    return jsonify(result)



@app.route("/api/planner/goal", methods=["POST"])
@login_required
def api_planner_goal():
    """Goal-Based Wealth Planning SIP Calculator."""
    data = request.get_json(force=True)
    goal_name = str(data.get("goal_name", "Wealth Goal"))
    target_amount = float(data.get("target_amount", 1000000))
    years = int(data.get("years", 10))
    initial_investment = float(data.get("initial_investment", 0))
    risk_tier = str(data.get("risk_tier", "moderate"))
    result = df_lib.generate_investment_plan(goal_name, target_amount, years, initial_investment, risk_tier)
    return jsonify({"ok": True, **result})


# ---------------------------------------------------------------------------
# INVESTMENT PLANNER SAVED GUIDES API
# ---------------------------------------------------------------------------
@app.route("/api/planner/save-guide", methods=["POST"])
@login_required
def api_save_investment_guide():
    data = request.get_json(force=True)
    user_id = session["user"]["id"]
    guide_type = str(data.get("guide_type", "model_portfolio")).strip()
    title = str(data.get("title", "My Investment Plan")).strip()
    summary = str(data.get("summary", ""))
    target_amount = float(data.get("target_amount", 0))
    expected_return = float(data.get("expected_return", 0))
    horizon_years = int(data.get("horizon_years", 5))
    guide_data = data.get("data", {})

    guide_id = db.save_investment_guide(
        user_id=user_id,
        guide_type=guide_type,
        title=title,
        summary=summary,
        target_amount=target_amount,
        expected_return=expected_return,
        horizon_years=horizon_years,
        data_dict=guide_data
    )
    return jsonify({"ok": True, "guide_id": guide_id, "message": "Investment guide saved to your profile."})


@app.route("/api/planner/saved-guides", methods=["GET"])
@login_required
def api_get_saved_investment_guides():
    user_id = session["user"]["id"]
    guides = db.get_user_investment_guides(user_id)
    return jsonify({"ok": True, "guides": guides})


@app.route("/api/planner/saved-guides/<int:guide_id>", methods=["DELETE", "POST"])
@login_required
def api_delete_saved_investment_guide(guide_id):
    user_id = session["user"]["id"]
    ok = db.delete_investment_guide(user_id, guide_id)
    return jsonify({"ok": ok})


# ---------------------------------------------------------------------------
# AI ADVISOR
# ---------------------------------------------------------------------------
@app.route("/advisor")
@login_required
def advisor_page():
    user_id = session["user"]["id"]
    saved_profile = db.get_advisor_profile(user_id)
    return render_template("advisor.html", saved_profile=saved_profile)


@app.route("/api/advisor/analyze", methods=["POST"])
@login_required
def api_advisor_analyze():
    data = request.get_json(force=True)
    age = int(data.get("age", 30))
    risk_tolerance = str(data.get("risk_tolerance", "moderate"))
    investment_horizon = int(data.get("investment_horizon", 5))
    monthly_income = float(data.get("monthly_income", 75000))
    current_savings = float(data.get("current_savings", 200000))
    result = df_lib.generate_advisor_recommendation(
        age, risk_tolerance, investment_horizon, monthly_income, current_savings
    )
    # Automatically persist investor profile & advice in SQLite for this user
    user_id = session["user"]["id"]
    db.save_advisor_profile(
        user_id=user_id,
        age=age,
        risk_tolerance=risk_tolerance,
        investment_horizon=investment_horizon,
        monthly_income=monthly_income,
        current_savings=current_savings,
        rec_dict=result
    )
    return jsonify({"ok": True, **result})


# ---------------------------------------------------------------------------
# PORTFOLIO
# ---------------------------------------------------------------------------
@app.route("/portfolio")
@login_required
def portfolio():
    user_id = session["user"]["id"]
    holdings = db.get_user_holdings(user_id)
    results, invested, current, pnl, pnl_pct = df_lib.calculate_portfolio_metrics(holdings)
    return render_template("portfolio.html", holdings=results, total_invested=invested,
                            total_current=current, total_pnl=pnl, total_pnl_pct=pnl_pct)


@app.route("/api/portfolio/data")
@login_required
def api_portfolio_data():
    """Returns the same numbers the /portfolio page renders with, as JSON.
    Used by portfolio views to refresh data without reloading the whole page."""
    user_id = session["user"]["id"]
    holdings = db.get_user_holdings(user_id)
    results, invested, current, pnl, pnl_pct = df_lib.calculate_portfolio_metrics(holdings)
    return jsonify({
        "ok": True,
        "holdings": results,
        "total_invested": invested,
        "total_current": current,
        "total_pnl": pnl,
        "total_pnl_pct": pnl_pct,
    })


@app.route("/api/portfolio/add", methods=["POST"])
@login_required
def add_holding():
    data = request.get_json(force=True)
    ticker = str(data.get("ticker", "")).strip().upper()
    try:
        qty = float(data.get("qty", 0))
        buy_price = float(data.get("buy_price", 0))
    except ValueError:
        return jsonify({"ok": False, "error": "Quantity and price must be numbers."}), 400
    if not ticker or qty <= 0 or buy_price <= 0:
        return jsonify({"ok": False, "error": "Fill in ticker, quantity and buy price."}), 400

    user_id = session["user"]["id"]
    db.add_user_holding(user_id, ticker, qty, buy_price)
    return jsonify({"ok": True})


@app.route("/api/portfolio/delete/<int:holding_id>", methods=["POST", "DELETE"])
@login_required
def delete_holding(holding_id):
    user_id = session["user"]["id"]
    ok = db.delete_user_holding(user_id, holding_id)
    return jsonify({"ok": ok})


@app.route("/api/portfolio/clear", methods=["POST"])
@login_required
def clear_portfolio():
    user_id = session["user"]["id"]
    db.clear_user_holdings(user_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# MACRO PULSE
# ---------------------------------------------------------------------------
@app.route("/macro")
@login_required
def macro():
    data = df_lib.fetch_macro_data()
    usd_inr = round(df_lib.get_usd_to_inr_rate(), 2)
    status = df_lib.check_market_status()
    return render_template("macro.html", macro_data=data, usd_inr=usd_inr,
                            nse_open=status["nse_open"], nyse_open=status["nyse_open"],
                            ist_time=status["ist_time"], et_time=status["et_time"])


# ---------------------------------------------------------------------------
# FINANCIAL TOOLS
# ---------------------------------------------------------------------------
@app.route("/tools")
@login_required
def tools():
    return render_template("tools.html")


@app.route("/api/calc/sip", methods=["POST"])
@login_required
def calc_sip():
    data = request.get_json(force=True)
    result = df_lib.calculate_sip(float(data.get("monthly", 10000)),
                                   float(data.get("return_pct", 12)),
                                   int(data.get("years", 10)))
    return jsonify({"ok": True, **result})


@app.route("/api/calc/lumpsum", methods=["POST"])
@login_required
def calc_lumpsum():
    data = request.get_json(force=True)
    result = df_lib.calculate_lumpsum(float(data.get("amount", 100000)),
                                       float(data.get("return_pct", 12)),
                                       int(data.get("years", 10)))
    return jsonify({"ok": True, **result})


@app.route("/api/calc/fees", methods=["POST"])
@login_required
def calc_fees():
    data = request.get_json(force=True)
    result = df_lib.calculate_fees(float(data.get("amount", 100000)),
                                    float(data.get("brokerage", 0.05)),
                                    float(data.get("stamp_duty", 0.015)),
                                    float(data.get("gst", 18)))
    return jsonify({"ok": True, **result})


# ---------------------------------------------------------------------------
# SETTINGS & SECURITY AUDIT LOGS
# ---------------------------------------------------------------------------
@app.route("/settings")
@login_required
def settings():
    user_id = session["user"]["id"]
    user_stats = db.get_user_stats(user_id)
    my_login_logs = db.get_login_logs(user_id=user_id, limit=25)
    all_users = db.get_all_users_summary()
    global_login_logs = db.get_login_logs(limit=50)
    today_logins = db.get_today_logins_count()
    sms_provider = sms_service.get_configured_provider()
    return render_template(
        "settings.html",
        user_stats=user_stats,
        login_logs=my_login_logs,
        all_users=all_users,
        global_login_logs=global_login_logs,
        today_logins=today_logins,
        daily_limit=10,
        sms_provider=sms_provider
    )


@app.route("/api/user/update-name", methods=["POST"])
@login_required
def update_user_name():
    data = request.get_json(force=True)
    new_name = str(data.get("name", "")).strip()
    if not new_name:
        return jsonify({"ok": False, "error": "Name cannot be empty."}), 400
    user_id = session["user"]["id"]
    db.update_user_name(user_id, new_name)
    session["user"]["name"] = new_name
    return jsonify({"ok": True, "name": new_name})


@app.route("/standalone")
def standalone_app():
    return send_file("stocksense_ai_complete.html")



# ---------------------------------------------------------------------------
# GEMINI AI COPILOT ENDPOINTS
# ---------------------------------------------------------------------------
CUSTOM_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

def get_gemini_key():
    return session.get("gemini_key") or os.environ.get("GEMINI_API_KEY", "") or CUSTOM_GEMINI_KEY

def find_company_in_query(query: str):
    """Detects if any stored company is referenced in the query."""
    q = query.lower()
    
    # Priority keyword matchers
    if any(k in q for k in ["lalitha", "lalitha jewellery", "kiran kumar", "gubeer"]):
        return db.get_company_by_ticker("LALITHA.NS")
    if any(k in q for k in ["titan", "tanishq", "caratlane", "zoya", "mia"]):
        return db.get_company_by_ticker("TITAN.NS")
    if any(k in q for k in ["kalyan", "kalyan jewellers", "kalyanaraman", "candere"]):
        return db.get_company_by_ticker("KALYANKJIL.NS")
    if any(k in q for k in ["thangamayil", "thangamayl"]):
        return db.get_company_by_ticker("THANGAMAYL.NS")
    if any(k in q for k in ["reliance", "mukesh ambani", "jio", "ril"]):
        return db.get_company_by_ticker("RELIANCE.NS")
    if any(k in q for k in ["tcs", "tata consultancy", "krithivasan"]):
        return db.get_company_by_ticker("TCS.NS")
    if any(k in q for k in ["infosys", "infy", "narayana murthy", "salil parekh"]):
        return db.get_company_by_ticker("INFY.NS")
    if any(k in q for k in ["tata motors", "tatamotors", "jlr", "jaguar", "nexon ev"]):
        return db.get_company_by_ticker("TATAMOTORS.NS")
    if any(k in q for k in ["hdfc", "hdfc bank"]):
        return db.get_company_by_ticker("HDFCBANK.NS")
    if any(k in q for k in ["apple", "aapl", "iphone", "tim cook", "macbook"]):
        return db.get_company_by_ticker("AAPL")
    if any(k in q for k in ["nvidia", "nvda", "jensen huang", "blackwell", "h100"]):
        return db.get_company_by_ticker("NVDA")
    if any(k in q for k in ["microsoft", "msft", "satya nadella", "azure"]):
        return db.get_company_by_ticker("MSFT")
    if any(k in q for k in ["tesla", "tsla", "elon musk", "cybertruck"]):
        return db.get_company_by_ticker("TSLA")
    if any(k in q for k in ["google", "googl", "alphabet", "sundar pichai", "gemini"]):
        return db.get_company_by_ticker("GOOGL")
    if any(k in q for k in ["gold", "gld", "bullion", "jewellery market"]):
        return db.get_company_by_ticker("LALITHA.NS") or db.get_company_by_ticker("TITAN.NS")
    
    # General lookup across all stored companies in SQLite
    stored = db.get_all_stored_companies()
    for c in stored:
        ticker_base = c["ticker"].lower().replace(".ns", "")
        if ticker_base in q or c["name"].lower() in q:
            return c
    return None


def generate_smart_fallback(query: str, holdings_text: str, portfolio_summary: str) -> str:
    """Delivers comprehensive, institutional-grade responses across project features and company analysis."""
    q = query.lower()

    # 1. Company Analysis Query
    comp = find_company_in_query(query)
    if comp:
        name = comp.get("name")
        ticker = comp.get("ticker")
        sector = comp.get("sector")
        hq = comp.get("headquarters")
        leader = comp.get("founder_ceo")
        year = comp.get("founded_year")
        rev = comp.get("annual_revenue")
        profit = comp.get("net_profit")
        pe = comp.get("pe_ratio")
        presence = comp.get("market_presence")
        products = comp.get("key_products")
        moats = comp.get("competitive_advantages")
        summary = comp.get("business_summary")

        return (
            f"### 🏛️ Institutional Company Dossier: {name} (`{ticker}`)\n\n"
            f"> **Verified Backend Storage**: Sourced directly from Stocksense SQLite Database.\n\n"
            f"#### 1. Corporate Profile & Leadership\n"
            f"- **Sector / Industry**: {sector} &bull; {comp.get('industry')}\n"
            f"- **Key Leadership / Founder**: {leader}\n"
            f"- **Headquarters**: {hq} (Founded in {year})\n"
            f"- **Market Classification**: {comp.get('market_cap_tier')}\n\n"
            f"#### 2. Business Model & Core Operations\n"
            f"{summary}\n\n"
            f"#### 3. Core Product Portfolio & Offerings\n"
            f"- **Offerings**: {products}\n"
            f"- **Footprint & Reach**: {presence}\n\n"
            f"#### 4. Financial Benchmarks (Backend Stored)\n"
            f"| Metric | Valuation Benchmark |\n"
            f"| :--- | :--- |\n"
            f"| **Annual Revenue** | `{rev}` |\n"
            f"| **Net Profit** | `{profit}` |\n"
            f"| **P/E Ratio** | `{pe}` |\n"
            f"| **Artisans / Workforce** | `{comp.get('employees')}` |\n\n"
            f"#### 5. Institutional Competitive Moats\n"
            f"{moats}\n\n"
            f"💡 **Stocksense Tactical Insight**: You can inspect real-time chart projections, news sentiment, and custom duration probability for `{ticker}` on the **Company Analysis** tab or compare with sector peers on the **Compare** page."
        )

    # 2. Portfolio Intelligence & Allocation Inquiries
    if any(w in q for w in ["portfolio", "holdings", "stocks", "allocation", "weight", "asset", "position"]):
        return (
            "### 💼 Stocksense AI Portfolio Intelligence\n\n"
            f"- **Your Active Portfolio**: {portfolio_summary}\n"
            f"- **Holdings Monitored**:\n{holdings_text}\n\n"
            "**Key Capabilities**:\n"
            "- **Live Asset Allocation**: Computes exact percentage weights and current market valuations for each position.\n"
            "- **Real-Time P&L**: Tracks net unrealized gains/losses in both absolute currency and percentage return.\n"
            "- **Dynamic Management**: Instantly add new positions or trim holdings with automatic metric re-calculation.\n"
            "- **Risk Diversification**: Monitor concentration risk to avoid over-exposure to any single sector or ticker."
        )

    # 3. Security, Authentication & Database Persistence Inquiries
    if any(w in q for w in ["sms", "otp", "login", "auth", "security", "database", "sqlite", "audit", "storage"]):
        return (
            "### 🔐 Real SMS OTP & Persistent Database Architecture\n\n"
            "Stocksense AI runs an institutional, zero-cost persistent security layer powered by SQLite (`stocksense.db`):\n\n"
            "1. **Real SMS OTP Authentication**:\n"
            "   - Dispatches real 6-digit verification codes to user mobile numbers via SMS Gateways (Fast2SMS for India, Twilio globally, 2Factor.in).\n"
            "   - Features an intelligent development fallback (on-screen demo OTP) if gateway credentials are unconfigured.\n"
            "2. **Persistent User Data Storage**:\n"
            "   - All user holdings, model portfolios, goal SIP roadmaps, and personal advisor profiles are permanently linked to the verified mobile number in `stocksense.db`.\n"
            "   - Your past work is automatically restored on every login session.\n"
            "3. **Security Audit & Login Logs**:\n"
            "   - Tracks login audit records (IP address, device browser/OS user agent, timestamp, and status) accessible on the Settings tab.\n"
            "4. **Backend Company Repository**:\n"
            "   - Institutional company dossiers (e.g. Lalitha Jewellery, Titan, Reliance, Apple) are permanently stored in the `companies` table, ensuring zero downtime even if external APIs are unreachable."
        )

    # 4. Machine Learning & Quantitative Models Inquiries
    if any(w in q for w in ["model", "lstm", "ml", "machine learning", "neural", "monte carlo", "vader", "forecast", "predict"]):
        return (
            "### 🧠 Stocksense AI Quantitative & ML Toolkit\n\n"
            "Stocksense AI integrates five institutional quantitative and machine learning models:\n\n"
            "1. **Deep LSTM Neural Network (`/forecast`)**: Multi-layer Long Short-Term Memory architecture predicting future price trajectories with confidence bands.\n"
            "2. **Monte Carlo Probability Engine (`/company_analysis`)**: Simulates 1,000 Geometric Brownian Motion price paths to compute statistical probability for custom target returns and horizons (1 week to 1 year).\n"
            "3. **VADER News Sentiment Analysis**: Natural language processing scoring live financial headlines to classify market sentiment as Bullish, Bearish, or Neutral.\n"
            "4. **Technical Oscillators**: Real-time 14-day Relative Strength Index (RSI), MACD signal lines, and Bollinger Bands with standard deviation channels.\n"
            "5. **Asset Allocation Matrix Optimizer (`/planner`)**: Blended 7-category risk-parity model balancing Large Cap, Mid Cap, Small Cap, Gold, Silver, Energy, and Debt/Cash."
        )

    # 5. AI Wealth Advisor & Goal Planning Inquiries
    if any(w in q for w in ["advisor", "planner", "sip", "swp", "retirement", "goal", "corpus", "roadmap"]):
        return (
            "### 💰 AI Wealth Advisor & Goal Roadmap\n\n"
            "Stocksense AI provides institutional wealth management capabilities:\n\n"
            "- **Goal SIP Calculator**: Solves the exact monthly SIP required to achieve any financial milestone (e.g. ₹1 Crore in 10 years).\n"
            "- **SWP Retirement Simulator**: Simulates monthly withdrawals from a lump sum corpus to evaluate portfolio longevity against inflation.\n"
            "- **Required Return Solver**: Determines the exact annualized return required to hit your target corpus from a given monthly investment.\n"
            "- **Personalized Asset Allocation**: Recommends customized allocation splits across 3 distinct risk tiers:\n"
            "  - **Conservative Wealth Preservation** (Higher Debt/Gold safety)\n"
            "  - **Balanced Growth & Stability** (Blended equities and defensive hedges)\n"
            "  - **Aggressive Capital Appreciation** (Higher growth equities and alpha momentum)\n"
            "- **One-Click Guide Persistence**: Save any calculated roadmap to your profile for permanent tracking."
        )

    # 6. Portfolio Risk, Rebalancing & Advice Inquiries
    if any(w in q for w in ["risk", "diversif", "safety", "protect", "rebalanc", "trim", "allocate", "advice", "suggest"]):
        return (
            "### 🛡️ Portfolio Risk & Rebalancing Assessment\n\n"
            f"- **Your Active Portfolio**: {portfolio_summary}\n"
            f"- **Holdings Monitored**:\n{holdings_text}\n\n"
            "**Institutional Rule-of-Thumb Guidelines**:\n"
            "1. **Concentration Risk**: No individual equity should exceed 25–30% of your total equity portfolio.\n"
            "2. **Defensive Hedges**: Maintain a 15–20% allocation in Liquid/Gold assets (e.g. Gold ETFs or Jewellery retail leaders like Lalitha / Titan) to buffer against equity drawdowns.\n"
            "3. **Systematic Rebalancing**: Review quarterly; harvest gains on positions up >30% and reallocate into high-yielding defensive anchors.\n"
            "4. **Stop-Loss Discipline**: Align stops with 14-day RSI (trim when RSI > 75; accumulate when RSI < 30)."
        )

    # 7. Macro Market & Sentiment Inquiries
    if any(w in q for w in ["sentiment", "market", "nifty", "nasdaq", "outlook", "trend", "macro", "inflation"]):
        status = df_lib.check_market_status()
        return (
            "### 🌐 Live Market Macro Outlook\n\n"
            f"- **NSE Market Status**: {'🟢 Open' if status.get('nse_open') else '🔴 Closed'} (India)\n"
            f"- **NYSE Market Status**: {'🟢 Open' if status.get('nyse_open') else '🔴 Closed'} (US)\n"
            f"- **USD/INR Benchmark**: ₹ {df_lib.get_usd_to_inr_rate():.2f}\n"
            "- **Macro Perspective**: Central bank rate decisions continue to anchor currency valuation. Consumer discretionary and retail sectors (including jewellery titans like Lalitha Jewellery and Titan) benefit from strong domestic festive demand, while IT services adapt to enterprise AI transformation."
        )

    # 8. Default Comprehensive Fallback
    return (
        f"### 🤖 Stocksense AI Financial Copilot\n\n"
        f"Analyzing your query: **\"{query}\"**\n\n"
        f"**Your Portfolio Snapshot**:\n{portfolio_summary}\n\n"
        f"**Holdings Tracked**:\n{holdings_text}\n\n"
        "**What I Can Help You With**:\n"
        "- **Deep Company Analysis**: Ask about any company (e.g., *'Analyze Lalitha Jewellery'*, *'Evaluate Titan'*, *'Review Reliance'*).\n"
        "- **Portfolio Analytics**: Ask about position weights, asset allocation, unrealized returns, or concentration risk.\n"
        "- **Quant Models & Predictions**: Ask how our LSTM neural forecasting, Monte Carlo simulation, or RSI indicators work.\n"
        "- **Wealth Planning**: Ask for SIP goal roadmaps, SWP retirement simulations, or risk-adjusted asset allocations."
    )


@app.route("/api/ai/get-key-status")
def api_ai_key_status():
    key = get_gemini_key()
    masked = f"{key[:4]}...{key[-4:]}" if len(key) >= 8 else ("Configured" if key else "")
    return jsonify({"has_key": bool(key), "preview": masked})


@app.route("/api/ai/set-key", methods=["POST"])
def api_ai_set_key():
    global CUSTOM_GEMINI_KEY
    data = request.get_json(force=True)
    new_key = str(data.get("key", "")).strip()
    session["gemini_key"] = new_key
    CUSTOM_GEMINI_KEY = new_key
    return jsonify({"ok": True, "has_key": bool(new_key)})


@app.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    data = request.get_json(force=True)
    user_msg = str(data.get("message", "")).strip()
    history = data.get("history", [])

    if not user_msg:
        return jsonify({"ok": False, "error": "Message cannot be empty."}), 400

    user_id = session.get("user", {}).get("id")
    holdings_text = "No saved holdings yet."
    portfolio_summary = "No active portfolio."
    if user_id:
        user_holdings = db.get_user_holdings(user_id)
        if user_holdings:
            results, invested, current, pnl, pnl_pct = df_lib.calculate_portfolio_metrics(user_holdings)
            h_lines = [f"- {h['ticker']}: {h['qty']} shares, Avg Buy ${h['buy_price']:.2f}, Current ${h['current_price']:.2f}, P&L {h['pnl']:+.2f} ({h['pnl_pct']:+.2f}%)" for h in results]
            holdings_text = "\n".join(h_lines)
            portfolio_summary = f"Total Invested: ${invested:,.2f}, Current Valuation: ${current:,.2f}, Net Return: ${pnl:+,.2f} ({pnl_pct:+.2f}%)"

    status = df_lib.check_market_status()
    mkt_info = f"NSE Open: {status.get('nse_open')}, NYSE Open: {status.get('nyse_open')}, USD/INR: {df_lib.get_usd_to_inr_rate():.2f}"

    # Check if a company was mentioned to inject deep backend company facts into the LLM context
    comp_context = ""
    mentioned_comp = find_company_in_query(user_msg)
    if mentioned_comp:
        comp_context = f"""
Mentioned Company Dossier from Stocksense Database:
- Ticker: {mentioned_comp.get('ticker')}
- Name: {mentioned_comp.get('name')}
- Sector: {mentioned_comp.get('sector')}
- Industry: {mentioned_comp.get('industry')}
- Headquarters: {mentioned_comp.get('headquarters')} (Founded {mentioned_comp.get('founded_year')})
- Leadership: {mentioned_comp.get('founder_ceo')}
- Business Model & Operations: {mentioned_comp.get('business_summary')}
- Core Products: {mentioned_comp.get('key_products')}
- Market Presence: {mentioned_comp.get('market_presence')}
- Stored Financials: Revenue {mentioned_comp.get('annual_revenue')}, Net Profit {mentioned_comp.get('net_profit')}, P/E {mentioned_comp.get('pe_ratio')}, Workforce {mentioned_comp.get('employees')}
- Competitive Moats: {mentioned_comp.get('competitive_advantages')}
"""

    system_instruction = f"""You are Stocksense AI, a world-class institutional fintech and equity analysis copilot.
User's Current Portfolio:
{portfolio_summary}
Holdings Breakdown:
{holdings_text}

Market Environment:
{mkt_info}
{comp_context}

Project Knowledge Base:
- Portfolio Analytics: Real-time equity portfolio tracking with live valuations, profit/loss tracking, asset allocation breakdown, and concentration risk metrics.
- Authentication & Data: Real SMS OTP authentication via Fast2SMS/Twilio with persistent SQLite database (`stocksense.db`) storing user holdings, guides, and login audit logs.
- Company Analysis: Combines live yfinance streaming with permanently stored backend SQLite company dossiers (e.g. Lalitha Jewellery, Titan, Reliance, Apple), interactive return calculators, and news sentiment.
- AI Wealth Advisor & Planner: Goal SIP calculators, SWP retirement longevity simulations, and 3-tier asset allocation matrices.

Instructions:
- Provide clear, direct, insightful, and actionable financial advice.
- When referencing the user's portfolio, use their exact numbers and holdings.
- If the user asks about a company (like Lalitha Jewellery, Titan, Reliance, Apple, etc.), utilize the provided company dossier to give a thorough fundamental and strategic breakdown.
- If the user asks about project features (Portfolio Analytics, SMS OTP, Database, Models), explain the architecture accurately.
- Format with clean Markdown (bullet points, bold highlights, tables).
- Keep answers concise and punchy (under 300 words unless asked for a deep report)."""

    gemini_key = get_gemini_key()
    if gemini_key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
            
            contents = []
            for item in history[-4:]:
                role = "model" if item.get("sender") == "ai" else "user"
                contents.append({"role": role, "parts": [{"text": item.get("text", "")}]})
            
            prompt_with_context = f"{system_instruction}\n\nUser Question: {user_msg}"
            contents.append({"role": "user", "parts": [{"text": prompt_with_context}]})

            res = requests.post(url, json={
                "contents": contents,
                "generationConfig": {
                    "temperature": 0.7,
                    "maxOutputTokens": 800
                }
            }, timeout=12)

            res_data = res.json()
            if res.status_code == 200 and "candidates" in res_data and len(res_data["candidates"]) > 0:
                reply = res_data["candidates"][0]["content"]["parts"][0]["text"]
                return jsonify({"ok": True, "reply": reply, "source": "gemini"})
            else:
                err_msg = res_data.get("error", {}).get("message", "Gemini API error")
                fallback_reply = generate_smart_fallback(user_msg, holdings_text, portfolio_summary)
                return jsonify({
                    "ok": True,
                    "reply": f"{fallback_reply}\n\n> ⚠️ *Notice: Gemini API returned: `{err_msg[:60]}`. Switched to Stocksense Offline Quant Engine.*",
                    "source": "fallback"
                })
        except Exception as e:
            fallback_reply = generate_smart_fallback(user_msg, holdings_text, portfolio_summary)
            return jsonify({
                "ok": True,
                "reply": f"{fallback_reply}\n\n> ⚠️ *Notice: Could not connect to Gemini API ({str(e)[:40]}). Switched to Stocksense Offline Quant Engine.*",
                "source": "fallback"
            })
    else:
        fallback_reply = generate_smart_fallback(user_msg, holdings_text, portfolio_summary)
        return jsonify({
            "ok": True,
            "reply": f"{fallback_reply}\n\n💡 *Tip: Connect your free Google Gemini API key using the key icon above for full unrestricted LLM reasoning!*",
            "source": "smart_engine"
        })




if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
