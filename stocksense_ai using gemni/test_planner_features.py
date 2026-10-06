"""
test_planner_features.py
Comprehensive test suite verifying all core data calculations, ML architectures,
financial planning models, and Flask REST endpoints in Stocksense AI.
"""

import sys
import os
import unittest
import numpy as np

import data_fetcher as df_lib
import ml_models as ml_lib
from app import app


class TestStocksenseAI(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess["user"] = {"name": "Test Trader", "mobile": "9999999999"}

    # -----------------------------------------------------------------------
    # 1. CORE & COMPANY ANALYTICS (CUSTOM RETURN & DURATION)
    # -----------------------------------------------------------------------
    def test_custom_return_and_duration_math(self):
        import pandas as pd
        # Create a synthetic 1-year historical price series
        dates = pd.date_range("2024-01-01", periods=200)
        prices = [100.0 + i * 0.2 + (i % 5) for i in range(200)]
        df = pd.DataFrame({"Close": prices}, index=dates)

        # 30-day forecast with 10% target
        prob_30, exp_30, vol_30, sig_30 = df_lib.calculate_return_probability(df, target_return_pct=10.0, days=30)
        self.assertGreater(prob_30, 0)
        self.assertLess(prob_30, 100)

        # 365-day forecast (1 year) with 25% target
        prob_365, exp_365, vol_365, sig_365 = df_lib.calculate_return_probability(df, target_return_pct=25.0, days=365)
        self.assertGreater(prob_365, 0)
        self.assertLess(prob_365, 100)
        print("[PASS] Custom return & duration probability test passed.")

    # -----------------------------------------------------------------------
    # 2. FEATURE 1: REQUIRED RETURN CALCULATOR
    # -----------------------------------------------------------------------
    def test_required_return_calculator_safe(self):
        res = df_lib.find_required_portfolio_mix(7.0)
        self.assertFalse(res["is_unrealistic"])
        self.assertEqual(res["tier_weights"]["safe"], 100.0)
        print("[PASS] Required return (Safe tier) passed.")

    def test_required_return_calculator_blended(self):
        res = df_lib.find_required_portfolio_mix(10.0)
        self.assertFalse(res["is_unrealistic"])
        # Should be a linear blend between Safe and Moderate
        total_weight = res["tier_weights"]["safe"] + res["tier_weights"]["moderate"]
        self.assertAlmostEqual(total_weight, 100.0, places=1)
        self.assertIn("Large Cap", res["asset_allocation"])
        print("[PASS] Required return (Blended tier) passed.")

    def test_required_return_calculator_unrealistic(self):
        res = df_lib.find_required_portfolio_mix(28.0)
        self.assertTrue(res["is_unrealistic"])
        self.assertIn("exceeds historical", res["warning"])
        self.assertGreater(res["shortfall_probability"], 60.0)
        print("[PASS] Required return (Unrealistic target warning) passed.")

    def test_required_return_from_goal_reverse_solver(self):
        res = df_lib.calculate_required_return_from_goal(target_corpus=5000000, monthly_sip=20000, years=10)
        self.assertTrue(res["ok"])
        self.assertGreater(res["required_rate"], 0.0)
        print(f"[PASS] Reverse SIP goal solver passed (Required Rate: {res['required_rate']}%).")

    # -----------------------------------------------------------------------
    # 3. FEATURE 2: MODEL PORTFOLIO BUILDER
    # -----------------------------------------------------------------------
    def test_model_portfolio_builder(self):
        for tier in ["safe", "moderate", "risky"]:
            amount = 100000.0
            res = df_lib.build_model_portfolio(amount, tier=tier, years=5)
            self.assertTrue(res["ok"])
            self.assertEqual(res["invested_amount"], amount)

            # Sum of allocations must strictly equal 100%
            total_weight = sum(a["weight_pct"] for a in res["allocations"])
            self.assertAlmostEqual(total_weight, 100.0, places=1)

            # Sum of amounts must equal total_amount
            total_allocated = sum(a["amount"] for a in res["allocations"])
            self.assertAlmostEqual(total_allocated, amount, places=1)

            # Tickers must be suggested for allocated categories
            for a in res["allocations"]:
                if a["amount"] > 0:
                    self.assertGreater(len(a["suggested_tickers"]), 0)

            self.assertGreater(res["projected_corpus"], amount)
            self.assertGreater(res["simplified_risk_score"], 0)
        print("[PASS] Model Portfolio Builder (Safe, Moderate, Risky) passed.")

    # -----------------------------------------------------------------------
    # 4. ADVISOR & GOAL PLANNER MODULES
    # -----------------------------------------------------------------------
    def test_advisor_and_goal_planner(self):
        adv = df_lib.generate_advisor_recommendation(age=30, risk_tolerance="moderate", investment_horizon=5)
        self.assertEqual(adv["recommended_tier"], "moderate")
        self.assertGreater(adv["emergency_fund_target"], 0)

        plan = df_lib.generate_investment_plan("Retirement", 10000000, 15, 100000, "moderate")
        self.assertGreater(plan["required_monthly_sip"], 0)
        self.assertEqual(plan["target_amount"], 10000000)

        swp = df_lib.simulate_swp(5000000, 30000, expected_return_pct=9.0, years=10)
        self.assertTrue(swp["is_sustainable"])
        print("[PASS] Advisor & Goal Planning tests passed.")

    # -----------------------------------------------------------------------
    # 5. ML ARCHITECTURES (LSTM, SLIDING WINDOW, CLUSTERING, ALERTS)
    # -----------------------------------------------------------------------
    def test_ml_models(self):
        # Test synthetic prices
        prices = [150.0 + (i * 0.5) + (np.sin(i / 5.0) * 4) for i in range(120)]

        predictor = ml_lib.LSTMPricePredictor(lookback=10)
        X, y = predictor.prepare_data(prices)
        self.assertGreater(len(X), 0)

        trained = predictor.train(X, y)
        self.assertTrue(trained)

        # Test sliding window recursive 30-day forecast
        preds = predictor.predict_next_30_days(prices, forecast_days=30)
        self.assertEqual(len(preds), 30)
        # Ensure it does NOT flatline (consecutive identical numbers)
        self.assertNotEqual(preds[0], preds[-1])

        # Test downfall alert
        alert = ml_lib.get_downfall_alert(preds, prices[-1], threshold_pct=-5.0)
        self.assertIn("alert", alert)

        # Test sentiment class
        sent = ml_lib.Sentiment()
        res = sent.analyze_headline("Apple reports record-breaking quarterly revenue growth")
        self.assertEqual(res["label"], "Bullish")

        # Test ensemble signal
        ens = ml_lib.generate_ml_signal(lstm_pred=4.0, sentiment_score=0.3, rsi_value=45, prob_of_return=75)
        self.assertIn("BUY", ens["action"])

        print("[PASS] ML sequential predictor, sliding-window, sentiment & alert tests passed.")

    # -----------------------------------------------------------------------
    # 6. FLASK REST ENDPOINTS
    # -----------------------------------------------------------------------
    def test_flask_endpoints(self):
        # Predict API
        r_pred = self.client.post("/api/predict", json={"ticker": "AAPL", "target_return": 12.0, "days": 90})
        self.assertIn(r_pred.status_code, [200, 400])  # 200 or network fallback

        # Required return API (direct)
        r_req = self.client.post("/api/planner/required-return", json={"target_return_pct": 11.5})
        self.assertEqual(r_req.status_code, 200)
        self.assertTrue(r_req.get_json()["ok"])

        # Required return API (reverse)
        r_rev = self.client.post("/api/planner/required-return", json={"target_corpus": 3000000, "monthly_sip": 15000, "years": 8})
        self.assertEqual(r_rev.status_code, 200)
        self.assertTrue(r_rev.get_json()["ok"])

        # Model portfolio API
        r_mod = self.client.post("/api/planner/model-portfolio", json={"amount": 250000, "tier": "moderate", "years": 5})
        self.assertEqual(r_mod.status_code, 200)
        self.assertTrue(r_mod.get_json()["ok"])

        # Goal API
        r_goal = self.client.post("/api/planner/goal", json={"goal_name": "Car", "target_amount": 1500000, "years": 5})
        self.assertEqual(r_goal.status_code, 200)

        # Advisor API
        r_adv = self.client.post("/api/advisor/analyze", json={"age": 28, "risk_tolerance": "moderate", "monthly_income": 80000})
        self.assertEqual(r_adv.status_code, 200)

        # GET pages
        self.assertEqual(self.client.get("/planner").status_code, 200)
        self.assertEqual(self.client.get("/advisor").status_code, 200)
        self.assertEqual(self.client.get("/compare").status_code, 200)
        self.assertEqual(self.client.get("/forecast?ticker=AAPL").status_code, 200)

        print("[PASS] All Flask REST API routes and page renders passed.")

    # -----------------------------------------------------------------------
    # 7. DYNAMIC EXPERT ALLOCATION ENGINE TESTS
    # -----------------------------------------------------------------------
    def test_dynamic_expert_investment_split(self):
        # 1. Test short horizon (1 year): debt/gold should dominate, small cap 0%
        res_short = df_lib.build_model_portfolio(100000, tier="moderate", years=1, goal="preservation")
        self.assertTrue(res_short["ok"])
        allocs_short = {a["category"]: a["weight_pct"] for a in res_short["allocations"]}
        self.assertEqual(allocs_short["Small Cap"], 0.0)
        self.assertGreaterEqual(allocs_short["Debt/Cash"] + allocs_short["Gold"], 50.0)
        total_wt = sum(a["weight_pct"] for a in res_short["allocations"])
        self.assertAlmostEqual(total_wt, 100.0, places=1)
        total_amt = sum(a["amount"] for a in res_short["allocations"])
        self.assertAlmostEqual(total_amt, 100000.0, places=1)

        # 2. Test long horizon (10 years): equities should dominate
        res_long = df_lib.build_model_portfolio(500000, tier="risky", years=10, goal="wealth")
        self.assertTrue(res_long["ok"])
        allocs_long = {a["category"]: a["weight_pct"] for a in res_long["allocations"]}
        equity_total = allocs_long["Large Cap"] + allocs_long["Mid Cap"] + allocs_long["Small Cap"]
        self.assertGreaterEqual(equity_total, 65.0)
        self.assertIn("roadmap", res_long["expert_roadmap"][0]["phase"].lower()) if "roadmap" in res_long else self.assertGreater(len(res_long["expert_roadmap"]), 0)

        # 3. Test exact rupee and percentage formats
        for a in res_long["allocations"]:
            self.assertIn("₹", a["amount_formatted"])
            self.assertIn("%", a["pct_and_amount"])
            self.assertTrue(len(a["expert_rationale"]) > 0)

        print("[PASS] Dynamic expert allocation engine (time horizon & goal scaling) passed.")

    # -----------------------------------------------------------------------
    # 8. INDIAN CURRENCY FORMATTER JINJA FILTERS
    # -----------------------------------------------------------------------
    def test_indian_currency_filters(self):
        from app import filter_format_inr, filter_inr_words

        # Lakhs
        self.assertEqual(filter_inr_words(100000), "1 Lakhs")
        self.assertEqual(filter_inr_words(250000), "2.5 Lakhs")
        self.assertIn("2,50,000", filter_format_inr(250000))

        # Crores
        self.assertEqual(filter_inr_words(10000000), "1 Crore")
        self.assertEqual(filter_inr_words(55000000), "5.5 Crores")
        self.assertIn("1,00,00,000", filter_format_inr(10000000))

        # Thousands
        self.assertEqual(filter_inr_words(25000), "25 Thousand")

        print("[PASS] Indian currency formatting filters passed.")

    # -----------------------------------------------------------------------
    # 9. MULTI-HORIZON FORECAST & DOWNFALL RISK ALERT
    # -----------------------------------------------------------------------
    def test_multi_horizon_forecast_and_alerts(self):
        prices = [150.0 + (i * 0.4) + (np.sin(i / 6.0) * 3) for i in range(120)]
        for hz in ["30d", "90d", "1y", "5y"]:
            res = ml_lib.run_lstm_forecast("TEST", prices, epochs=8, horizon=hz)
            self.assertTrue(res["ok"])
            self.assertIn("predictions", res)
            self.assertIn("upper_band", res)
            self.assertIn("lower_band", res)
            self.assertIn("alert", res)
            self.assertIn("stop_loss", res["alert"])
            self.assertIn("pros_and_cons", res)
            self.assertGreaterEqual(len(res["pros_and_cons"]["pros"]), 3)
            self.assertGreaterEqual(len(res["pros_and_cons"]["cons"]), 3)

        print("[PASS] Multi-horizon forecast (up to 5 years), confidence corridor & SWOT passed.")


if __name__ == "__main__":
    unittest.main()
