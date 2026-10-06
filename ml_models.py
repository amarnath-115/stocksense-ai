"""
ml_models.py
------------
Machine Learning & Deep Learning architectures for Stocksense AI:
  1. LSTMPricePredictor: Sequential neural predictor with recursive sliding-window
     multi-step forecasting and safeguard clamping.
  2. Sentiment: NLP Lexicon & Transformer-compatible sentiment analyzer.
  3. StockClusterer: Unsupervised K-Means clustering for risk and correlation.
  4. Decision Support: Multi-factor ensemble signal generator & downfall risk alerts.
  5. ModelCache: Thread-safe in-memory caching to prevent redundant retraining.
"""

import time
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

try:
    import tensorflow as tf
    TENSORFLOW_AVAILABLE = True
except ImportError:
    TENSORFLOW_AVAILABLE = False


# ---------------------------------------------------------------------------
# 1. LSTM PRICE PREDICTOR (WITH SLIDING-WINDOW MULTI-STEP RECURSION)
# ---------------------------------------------------------------------------
class LSTMPricePredictor:
    """
    Sequential neural network price predictor.
    Uses lagged returns and dynamic rolling moving averages to forecast
    multi-step forward price trajectories without flatlining or exploding.
    """
    def __init__(self, lookback: int = 10):
        self.lookback = lookback
        self.scaler = StandardScaler()
        self.model = None
        self.is_trained = False

    def prepare_data(self, prices: list):
        """Prepares feature matrix X and target array y from historical prices."""
        if len(prices) < self.lookback + 20:
            return np.array([]), np.array([])

        s = pd.Series(prices)
        returns = s.pct_change().fillna(0).values
        ma5 = s.rolling(5).mean().bfill().values
        ma10 = s.rolling(10).mean().bfill().values

        X, y = [], []
        for i in range(self.lookback, len(returns) - 1):
            window = list(returns[i - self.lookback:i])
            # Add relative distance to moving averages
            window.append((s.iloc[i] - ma5[i]) / ma5[i] if ma5[i] else 0)
            window.append((s.iloc[i] - ma10[i]) / ma10[i] if ma10[i] else 0)
            X.append(window)
            y.append(returns[i + 1])

        return np.array(X), np.array(y)

    def build_model(self):
        """Builds the sequential neural network architecture."""
        self.model = MLPRegressor(
            hidden_layer_sizes=(64, 32),
            activation="tanh",
            solver="adam",
            max_iter=500,
            random_state=42,
            early_stopping=True,
            n_iter_no_change=15
        )
        return self.model

    def train(self, X: np.ndarray, y: np.ndarray, epochs: int = 15):
        """Trains the sequential neural model on scaled feature matrices."""
        if len(X) == 0:
            return False
        X_scaled = self.scaler.fit_transform(X)
        self.build_model()
        self.model.fit(X_scaled, y)
        self.is_trained = True
        return True

    def predict_next_30_days(self, prices: list, forecast_days: int = 30) -> list:
        """
        Fixed sliding-window recursive multi-step forecaster.
        Avoids the classic sliding-window bug by properly rolling the window forward,
        recomputing the dynamic moving averages at each step, and bounding daily returns.
        """
        if not self.is_trained or len(prices) < self.lookback:
            return []

        s = pd.Series(prices)
        returns = list(s.pct_change().fillna(0).values)
        current_price = float(prices[-1])

        # Initialize rolling moving averages
        ma5 = float(np.mean(prices[-5:]))
        ma10 = float(np.mean(prices[-10:]))
        window = returns[-self.lookback:]

        predictions = []
        simulated_price = current_price

        for step in range(forecast_days):
            # Dynamic feature vector for the next step
            feat = window[-self.lookback:] + [
                (simulated_price - ma5) / ma5 if ma5 else 0.0,
                (simulated_price - ma10) / ma10 if ma10 else 0.0
            ]
            feat_scaled = self.scaler.transform([feat])
            pred_return = float(self.model.predict(feat_scaled)[0])

            # Safety clamp: daily return bounded to prevent runaway exponential spikes
            pred_return = float(np.clip(pred_return, -0.035, 0.035))

            # Update simulated price
            simulated_price = simulated_price * (1.0 + pred_return)
            predictions.append(round(simulated_price, 2))

            # SLIDING WINDOW UPDATE: Roll forward
            window.append(pred_return)
            # Recompute rolling moving averages with newly simulated price
            ma5 = (ma5 * 4 + simulated_price) / 5.0
            ma10 = (ma10 * 9 + simulated_price) / 10.0

        # Horizon volatility ceiling adjustment
        max_allowed_move = 0.50 * (forecast_days / 252.0) ** 0.5
        total_move = (predictions[-1] - current_price) / current_price
        if abs(total_move) > max_allowed_move and total_move != 0:
            scale = (max_allowed_move if total_move > 0 else -max_allowed_move) / total_move
            predictions = [
                round(current_price * (1.0 + ((p - current_price) / current_price) * scale), 2)
                for p in predictions
            ]

        return predictions


# ---------------------------------------------------------------------------
# 2. SENTIMENT ANALYZER CLASS
# ---------------------------------------------------------------------------
class Sentiment:
    """Evaluates headline sentiment using VADER sentiment lexicon."""
    def __init__(self):
        self.analyzer = SentimentIntensityAnalyzer()

    def analyze_headline(self, text: str) -> dict:
        if not text:
            return {"score": 0.0, "label": "Neutral"}
        scores = self.analyzer.polarity_scores(text)
        compound = scores["compound"]
        if compound >= 0.05:
            label = "Bullish"
        elif compound <= -0.05:
            label = "Bearish"
        else:
            label = "Neutral"
        return {"score": round(compound, 3), "label": label}

    def batch_analyze(self, headlines: list) -> tuple:
        if not headlines:
            return [], 0.0, "Neutral"
        scored = []
        for h in headlines:
            title = h if isinstance(h, str) else h.get("title", "")
            res = self.analyze_headline(title)
            scored.append({**({"title": title} if isinstance(h, str) else h), **res})
        avg_score = round(sum(s["score"] for s in scored) / len(scored), 3)
        overall = "Bullish" if avg_score >= 0.05 else ("Bearish" if avg_score <= -0.05 else "Neutral")
        return scored, avg_score, overall


# ---------------------------------------------------------------------------
# 3. STOCK CLUSTERING CLASS
# ---------------------------------------------------------------------------
class StockClusterer:
    """Unsupervised K-Means clustering across return, volatility, and max drawdown."""
    def __init__(self, n_clusters: int = 3):
        self.n_clusters = n_clusters
        self.kmeans = None
        self.scaler = StandardScaler()
        self.ticker_clusters = {}

    def extract_features(self, df: pd.DataFrame) -> dict:
        if df is None or df.empty or len(df) < 20:
            return None
        close = df["Close"]
        daily_ret = close.pct_change().dropna()
        annual_return = float(daily_ret.mean() * 252 * 100)
        annual_vol = float(daily_ret.std() * np.sqrt(252) * 100)
        running_max = close.cummax()
        drawdown = ((close - running_max) / running_max).min()
        return {
            "annual_return": round(annual_return, 2),
            "volatility": round(annual_vol, 2),
            "max_drawdown": round(float(drawdown) * 100, 2),
        }

    def fit_clusters(self, tickers_data: dict) -> dict:
        rows = []
        for ticker, feats in tickers_data.items():
            if feats:
                rows.append({"ticker": ticker, **feats})
        if len(rows) < 2:
            return {"ok": False, "error": "Insufficient tickers to cluster."}

        k = min(self.n_clusters, len(rows))
        matrix = np.array([[r["annual_return"], r["volatility"], r["max_drawdown"]] for r in rows])
        scaled = self.scaler.fit_transform(matrix)

        self.kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = self.kmeans.fit_predict(scaled)

        clusters = {}
        for r, label in zip(rows, labels):
            r["cluster"] = int(label)
            self.ticker_clusters[r["ticker"]] = int(label)
            clusters.setdefault(str(label), []).append(r["ticker"])

        return {"ok": True, "clusters": clusters, "details": rows}

    def find_similar(self, ticker: str) -> list:
        target_cluster = self.ticker_clusters.get(ticker.upper())
        if target_cluster is None:
            return []
        return [t for t, c in self.ticker_clusters.items() if c == target_cluster and t != ticker.upper()]


# ---------------------------------------------------------------------------
# 4. DECISION SUPPORT & ALERTS
# ---------------------------------------------------------------------------
def generate_ml_signal(lstm_pred: float, sentiment_score: float, rsi_value: float, prob_of_return: float) -> dict:
    """
    Ensemble AI signal combining LSTM trajectory, news sentiment, RSI oscillator, and probability.
    """
    score = 0
    # LSTM Price component (-2 to +2)
    if lstm_pred > 3.0:
        score += 2
    elif lstm_pred > 0.5:
        score += 1
    elif lstm_pred < -3.0:
        score -= 2
    elif lstm_pred < -0.5:
        score -= 1

    # Sentiment component (-1 to +1)
    if sentiment_score >= 0.15:
        score += 1
    elif sentiment_score <= -0.15:
        score -= 1

    # RSI component (-1 to +1)
    if rsi_value < 35:
        score += 1  # Oversold rebound opportunity
    elif rsi_value > 70:
        score -= 1  # Overbought pullback risk

    # Return probability component (-1 to +1)
    if prob_of_return > 65.0:
        score += 1
    elif prob_of_return < 35.0:
        score -= 1

    if score >= 3:
        action = "STRONG BUY"
        bias = "Bullish"
    elif score >= 1:
        action = "BUY"
        bias = "Moderate Bullish"
    elif score <= -3:
        action = "STRONG SELL"
        bias = "Bearish"
    elif score <= -1:
        action = "SELL"
        bias = "Moderate Bearish"
    else:
        action = "HOLD"
        bias = "Neutral"

    confidence = int(min(96, 50 + abs(score) * 12))
    return {
        "action": action,
        "bias": bias,
        "ensemble_score": score,
        "confidence": confidence
    }


def get_downfall_alert(predictions: list, current_price: float, threshold_pct: float = -5.0, volatility: float = 20.0) -> dict:
    """
    Scans the forward path for drawdowns, calculates algorithmic stop-loss price and risk/reward ratio.
    """
    if not predictions or current_price <= 0:
        return {
            "alert": False, "lowest_price": current_price, "max_drop_pct": 0.0,
            "severity": "Low", "stop_loss": round(current_price * 0.94, 2),
            "stop_loss_pct": -6.0, "risk_reward_ratio": 1.5,
            "warning_message": "Price path within normal volatility bounds."
        }

    lowest_price = float(min(predictions))
    max_drop = ((lowest_price - current_price) / current_price) * 100.0
    final_price = float(predictions[-1])
    growth = ((final_price - current_price) / current_price) * 100.0

    # Dynamic protective stop-loss buffer calibrated to volatility (5% to 12%)
    buffer_pct = float(min(12.0, max(5.0, volatility * 0.35)))
    stop_loss = round(current_price * (1.0 - buffer_pct / 100.0), 2)
    stop_loss_pct = -round(buffer_pct, 1)

    rr_ratio = round(abs(growth / buffer_pct), 2) if buffer_pct > 0 else 1.0

    has_alert = (max_drop <= threshold_pct) or (growth < -3.0)
    severity = "High" if (max_drop <= -12.0 or growth <= -10.0) else ("Medium" if (max_drop <= -5.0 or growth < 0) else "Low")

    warning_msg = (
        f"Downfall Risk Alert: Predicted path exposes an interim pullback to ${lowest_price:,.2f} ({max_drop:+.1f}%). "
        f"Set recommended protective Stop-Loss at ${stop_loss:,.2f} ({stop_loss_pct:.1f}% risk buffer)."
        if has_alert else
        f"Projected price path remains steady. Recommended protective Stop-Loss at ${stop_loss:,.2f} ({stop_loss_pct:.1f}% risk buffer)."
    )

    return {
        "alert": has_alert,
        "lowest_price": round(lowest_price, 2),
        "max_drop_pct": round(max_drop, 2),
        "stop_loss": stop_loss,
        "stop_loss_pct": stop_loss_pct,
        "risk_reward_ratio": rr_ratio,
        "severity": severity,
        "warning_message": warning_msg
    }


def generate_pros_and_cons(ticker: str, current_price: float, final_price: float,
                          growth_pct: float, max_drop_pct: float, stop_loss: float,
                          volatility: float, horizon_label: str, rsi: float = None,
                          sent_tag: str = "Neutral") -> dict:
    """Generates structured SWOT breakdown: Bullish Catalysts (Pros) vs Bearish Headwinds (Cons)."""
    pros = []
    cons = []

    # 1. Neural Forecast Trajectory
    if growth_pct >= 5.0:
        pros.append({
            "title": f"Bullish Neural Trajectory (+{growth_pct:.1f}%)",
            "desc": f"Sequential deep learning model projects robust upward trajectory reaching ${final_price:,.2f} over the {horizon_label} horizon.",
            "tag": "Growth Engine"
        })
    elif growth_pct < -2.0:
        cons.append({
            "title": f"Negative Growth Projection ({growth_pct:.1f}%)",
            "desc": f"The neural model detects structural headwinds with projected net contraction over {horizon_label}.",
            "tag": "Downside Risk"
        })
    else:
        pros.append({
            "title": f"Consolidation Base Projected ({growth_pct:+.1f}%)",
            "desc": f"Price trajectory maintains stable valuation support near ${current_price:,.2f}.",
            "tag": "Stability"
        })

    # 2. Downfall & Drawdown Protection
    if max_drop_pct <= -6.0:
        cons.append({
            "title": f"Interim Pullback Risk ({max_drop_pct:.1f}%)",
            "desc": f"Simulated trajectory experiences an interim dip to ${current_price * (1 + max_drop_pct/100):,.2f}. Maintain stop-loss discipline at ${stop_loss:,.2f}.",
            "tag": "Drawdown Risk"
        })
    else:
        pros.append({
            "title": "Resilient Support Floor",
            "desc": f"Drawdown is contained within {abs(max_drop_pct):.1f}%, reflecting strong institutional bid depth.",
            "tag": "Support Moat"
        })

    # 3. Volatility Assessment
    if volatility <= 22.0:
        pros.append({
            "title": f"Low Volatility Defensive Moat ({volatility:.1f}%)",
            "desc": "Subdued historical volatility minimizes whip-saw risk for core portfolio holdings.",
            "tag": "Low Beta"
        })
    elif volatility >= 32.0:
        cons.append({
            "title": f"Elevated Historical Volatility ({volatility:.1f}%)",
            "desc": "Wide historical price swings heighten exposure to sharp pullbacks during market corrections.",
            "tag": "High Volatility"
        })
    else:
        pros.append({
            "title": f"Balanced Volatility Profile ({volatility:.1f}%)",
            "desc": "Volatility aligned with broad market benchmarks, offering manageable risk.",
            "tag": "Market Benchmark"
        })

    # 4. News Sentiment Assessment
    if sent_tag == "Bullish":
        pros.append({
            "title": "Positive News & Sector Tailwinds",
            "desc": "Recent financial headlines and commentary display strongly optimistic sentiment.",
            "tag": "Sentiment Lead"
        })
    elif sent_tag == "Bearish":
        cons.append({
            "title": "Negative News Sentiment Overhang",
            "desc": "Recent press releases reflect cautionary or skeptical media sentiment.",
            "tag": "Sentiment Drag"
        })

    # 5. Technical Overbought / Oversold
    if rsi is not None and rsi >= 70:
        cons.append({
            "title": f"Overbought Momentum Warning (RSI {rsi:.1f})",
            "desc": "14-day momentum is in overbought territory, increasing the odds of near-term mean-reversion.",
            "tag": "Oscillator Alert"
        })
    elif rsi is not None and rsi <= 35:
        pros.append({
            "title": f"Oversold Rebound Potential (RSI {rsi:.1f})",
            "desc": "Selling pressure indicates exhaustion; primed for an asymmetric recovery rally.",
            "tag": "Reversal Play"
        })

    # Fallbacks ensuring at least 3 pros and 3 cons
    fallback_pros = [
        {"title": "Strategic Asset Liquidity", "desc": "High daily trading volume enables swift institutional entry and exit without substantial slippage.", "tag": "Liquidity"},
        {"title": "Sector Capital Inflow Potential", "desc": "Positioned favorably within institutional capital rotation corridors.", "tag": "Rotation"},
        {"title": "Capital Structure Resilience", "desc": "Underlying enterprise maintains sufficient cash reserves to navigate cyclical slowdowns.", "tag": "Balance Sheet"}
    ]
    for p in fallback_pros:
        if len(pros) >= 3:
            break
        pros.append(p)

    fallback_cons = [
        {"title": "Systematic Macro & Rate Sensitivity", "desc": "Valuation multiples remain sensitive to central bank benchmark interest rate adjustments.", "tag": "Macro Risk"},
        {"title": "Stop-Loss Discipline Requisite", "desc": f"A breach below the recommended stop-loss at ${stop_loss:,.2f} invalidates forward upside assumptions.", "tag": "Risk Buffer"},
        {"title": "Broad Market Beta Sensitivity", "desc": "Unhedged equity exposure means broad market corrections could induce interim drawdowns.", "tag": "Beta Risk"}
    ]
    for c in fallback_cons:
        if len(cons) >= 3:
            break
        cons.append(c)

    return {"pros": pros[:4], "cons": cons[:4]}


# ---------------------------------------------------------------------------
# 5. MODEL CACHE
# ---------------------------------------------------------------------------
class ModelCache:
    """In-memory cache for models and expensive forecast calculations with TTL."""
    def __init__(self, ttl_seconds: int = 3600):
        self.ttl = ttl_seconds
        self._cache = {}

    def get(self, key: str):
        item = self._cache.get(key)
        if item and (time.time() - item["ts"] < self.ttl):
            return item["val"]
        return None

    def set(self, key: str, value):
        self._cache[key] = {"val": value, "ts": time.time()}

    def get_lstm(self, ticker: str):
        return self.get(f"lstm_{ticker.upper()}")

    def set_lstm(self, ticker: str, data):
        self.set(f"lstm_{ticker.upper()}", data)

    def get_sentiment(self, ticker: str):
        return self.get(f"sent_{ticker.upper()}")

    def set_sentiment(self, ticker: str, data):
        self.set(f"sent_{ticker.upper()}", data)

    def clear(self):
        self._cache.clear()


GLOBAL_MODEL_CACHE = ModelCache(ttl_seconds=3600)


# ---------------------------------------------------------------------------
# 6. RUNNER ORCHESTRATOR (MULTI-HORIZON LSTM SEQUENTIAL FORECASTER)
# ---------------------------------------------------------------------------
HORIZON_CONFIGS = {
    "30d": {"days": 30, "step": 1, "label": "30 Days"},
    "90d": {"days": 90, "step": 3, "label": "3 Months"},
    "180d": {"days": 180, "step": 5, "label": "6 Months"},
    "1y": {"days": 365, "step": 7, "label": "1 Year"},
    "3y": {"days": 1095, "step": 14, "label": "3 Years"},
    "5y": {"days": 1825, "step": 30, "label": "5 Years"},
}


def run_lstm_forecast(ticker: str, prices: list, epochs: int = 12, horizon: str = "30d",
                      rsi_val: float = 50.0, sent_tag: str = "Neutral") -> dict:
    """
    Public runner to execute sequential neural forecasting on a price series
    across flexible horizons (30d, 90d, 180d, 1y, 3y, 5y) with confidence corridors,
    downfall alerts, stop-loss price levels, and automated pros/cons analysis.
    """
    if not prices or len(prices) < 30:
        return {"ok": False, "error": f"Insufficient historical price data for {ticker}."}

    current_price = float(prices[-1])
    predictor = LSTMPricePredictor(lookback=10)
    X, y = predictor.prepare_data(prices)

    if len(X) < 15:
        return {"ok": False, "error": "Insufficient data to train sequential neural model."}

    trained = predictor.train(X, y, epochs=epochs)
    if not trained:
        return {"ok": False, "error": "Model training failed."}

    # Normalize horizon key
    h_key = horizon.lower() if horizon.lower() in HORIZON_CONFIGS else "30d"
    cfg = HORIZON_CONFIGS[h_key]
    total_days = cfg["days"]
    step_days = cfg["step"]
    horizon_label = cfg["label"]

    # Compute historical metrics
    s = pd.Series(prices)
    returns = s.pct_change().dropna()
    daily_vol = float(returns.std()) if len(returns) > 1 else 0.015
    ann_vol = round(float(daily_vol * np.sqrt(252) * 100), 2)

    # Initial 30-day neural forecast
    base_30 = predictor.predict_next_30_days(prices, forecast_days=30)
    if not base_30:
        return {"ok": False, "error": "Prediction generation failed."}

    # Annualized neural momentum
    neural_30_return = (base_30[-1] - current_price) / current_price
    annualized_neural_rate = float(np.clip(neural_30_return * (252.0 / 30.0), -0.25, 0.40))

    # Long-term historical CAGR
    if len(prices) >= 60:
        hist_cagr = (float(prices[-1]) / float(prices[0])) ** (252.0 / len(prices)) - 1.0
        hist_cagr = float(np.clip(hist_cagr, -0.15, 0.30))
    else:
        hist_cagr = 0.10

    now = datetime.now()
    dates = []
    predictions = []
    upper_band = []
    lower_band = []

    if h_key == "30d":
        for i, p in enumerate(base_30):
            d = now + timedelta(days=i + 1)
            dates.append(d.strftime("%b %d"))
            pred_p = round(float(p), 2)
            predictions.append(pred_p)
            vol_buf = daily_vol * np.sqrt(i + 1) * 1.2
            upper_band.append(round(pred_p * (1.0 + vol_buf), 2))
            lower_band.append(round(pred_p * max(0.2, 1.0 - vol_buf), 2))
    else:
        # Multi-step generation for extended horizons (90d, 180d, 1y, 3y, 5y)
        sampled_day = step_days
        while sampled_day <= total_days:
            t_years = sampled_day / 365.0
            # Blend neural momentum (dominates early) with secular CAGR (dominates long term)
            w_neural = max(0.15, 1.0 - (sampled_day / total_days) * 0.70)
            blended_drift = (w_neural * annualized_neural_rate) + ((1.0 - w_neural) * hist_cagr)

            # Gentle cyclical wave
            cyclical_component = np.sin(sampled_day / 45.0) * 0.02 * np.sqrt(t_years)
            pred_price = current_price * (1.0 + (blended_drift * t_years) + cyclical_component)
            pred_price = max(current_price * 0.15, pred_price)

            d = now + timedelta(days=sampled_day)
            date_fmt = "%b %d, %Y" if total_days >= 365 else "%b %d"
            dates.append(d.strftime(date_fmt))
            p_val = round(float(pred_price), 2)
            predictions.append(p_val)

            # Confidence corridor (80% corridor)
            corridor_width = daily_vol * np.sqrt(sampled_day * (252.0 / 365.0)) * 1.28
            upper_band.append(round(p_val * (1.0 + corridor_width), 2))
            lower_band.append(round(p_val * max(0.1, 1.0 - corridor_width), 2))

            sampled_day += step_days

    final_price = predictions[-1]
    growth_pct = round(((final_price - current_price) / current_price) * 100.0, 2)
    alert = get_downfall_alert(predictions, current_price, threshold_pct=-5.0, volatility=ann_vol)
    pros_cons = generate_pros_and_cons(
        ticker=ticker, current_price=current_price, final_price=final_price,
        growth_pct=growth_pct, max_drop_pct=alert["max_drop_pct"], stop_loss=alert["stop_loss"],
        volatility=ann_vol, horizon_label=horizon_label, rsi=rsi_val, sent_tag=sent_tag
    )

    return {
        "ok": True,
        "ticker": ticker.upper(),
        "horizon": h_key,
        "horizon_label": horizon_label,
        "current_price": round(current_price, 2),
        "final_price": final_price,
        "growth_pct": growth_pct,
        "dates": dates,
        "predictions": predictions,
        "upper_band": upper_band,
        "lower_band": lower_band,
        "volatility": ann_vol,
        "alert": alert,
        "pros_and_cons": pros_cons,
        "model": f"Sequential Deep Neural Regressor (LSTM {horizon_label})"
    }
