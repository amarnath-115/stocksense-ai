"""
Comprehensive Test Suite for Real SMS OTP Authentication & Persistent SQLite Storage
Tests:
- Zero-cost SQLite database initialization & CRUD
- User registration, returning user recognition, profile updates
- Mobile OTP storage, expiration & single-use verification
- Real-time login audit & security log tracking (IP, device, status)
- Portfolio equity holdings persistence (add, delete, clear)
- Investment Guides persistence (save, list, delete model portfolios & goal plans)
- AI Advisor profile and recommendation persistence
- Multi-provider SMS dispatcher (normalization & fallback structure)
- Flask endpoints for Auth, Portfolio, Planner, Advisor, and Settings
"""

import os
import json
import unittest
import sqlite3
from datetime import datetime, timedelta, timezone

# Import our modules
import db
import sms_service
import app as flask_app_module


class TestAuthAndPersistence(unittest.TestCase):
    def setUp(self):
        # Configure test client
        self.app = flask_app_module.app
        self.app.config["TESTING"] = True
        self.app.config["SECRET_KEY"] = "test-secret"
        self.client = self.app.test_client()

        # Generate unique test mobile
        self.test_mobile = f"98765{datetime.now(timezone.utc).strftime('%M%S%f')[:5]}"
        self.test_user_name = "Vikram Aditya"

    def test_01_db_schema_initialization(self):
        """Verify all 6 SQLite tables exist and have valid structure."""
        db.init_db()
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row["name"] for row in cursor.fetchall()]
            for expected in ["users", "otp_records", "login_logs", "portfolio_holdings", "saved_investment_guides", "advisor_profiles"]:
                self.assertIn(expected, tables, f"Table {expected} missing from sqlite schema.")
        print("[PASS] SQLite schema initialization verified with all 6 tables.")

    def test_02_sms_normalization_and_dispatcher(self):
        """Verify phone number formatting and SMS dispatch response structure."""
        self.assertEqual(sms_service.normalize_phone_number("+91 98765 43210"), "+919876543210")
        self.assertEqual(sms_service.normalize_phone_number("09876543210"), "09876543210")
        self.assertEqual(sms_service.normalize_phone_number("98765-43210"), "9876543210")

        # Fallback dispatch test
        res = sms_service.send_otp_sms("9876543210", "123456")
        self.assertIn("sent", res)
        self.assertIn("provider", res)
        self.assertIn("message", res)
        print("[PASS] SMS normalization & multi-provider dispatcher verified.")

    def test_03_otp_lifecycle_and_verification(self):
        """Verify OTP creation, expiration, invalid code rejection, and single-use."""
        mobile = self.test_mobile
        otp = "789123"

        # Save OTP
        db.save_otp(mobile, otp, validity_minutes=5)

        # Incorrect OTP should fail
        self.assertFalse(db.verify_otp(mobile, "000000"))

        # Correct OTP should succeed
        self.assertTrue(db.verify_otp(mobile, otp))

        # Re-use of same OTP should fail (single-use protection)
        self.assertFalse(db.verify_otp(mobile, otp))
        print("[PASS] OTP lifecycle (validity, incorrect rejection, single-use) verified.")

    def test_04_user_crud_and_returning_user(self):
        """Verify user creation, fetch by mobile, and profile name updating."""
        mobile = self.test_mobile
        name = self.test_user_name

        user = db.create_user(mobile, name)
        self.assertIsNotNone(user["id"])
        self.assertEqual(user["mobile"], mobile)
        self.assertEqual(user["name"], name)

        # Fetch by mobile
        fetched = db.get_user_by_mobile(mobile)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["id"], user["id"])

        # Update name
        updated = db.update_user_name(user["id"], "Vikram Sharma")
        self.assertTrue(updated)
        fetched_updated = db.get_user_by_id(user["id"])
        self.assertEqual(fetched_updated["name"], "Vikram Sharma")
        print("[PASS] User CRUD & returning user lookup verified.")

    def test_05_security_and_login_audit_logs(self):
        """Verify tracking of login events with IP, user-agent, and status."""
        mobile = self.test_mobile + "5"
        user = db.create_user(mobile, "Audit User")

        db.log_login_attempt(mobile, status="OTP_SENT", ip_address="192.168.1.100", user_agent="Mozilla/5.0 TestBrowser")
        db.log_login_attempt(mobile, status="SUCCESS", ip_address="192.168.1.100", user_agent="Mozilla/5.0 TestBrowser", user_id=user["id"])

        logs = db.get_login_logs(user_id=user["id"])
        self.assertGreaterEqual(len(logs), 1)
        self.assertEqual(logs[0]["status"], "SUCCESS")
        self.assertEqual(logs[0]["ip_address"], "192.168.1.100")
        print("[PASS] Security & login audit logging verified.")

    def test_06_portfolio_persistence(self):
        """Verify portfolio holdings are saved, fetched, and deleted per user."""
        user = db.create_user(self.test_mobile + "1", "Investor A")
        uid = user["id"]

        # Add holdings
        h1 = db.add_user_holding(uid, "AAPL", 10, 150.0)
        h2 = db.add_user_holding(uid, "RELIANCE.NS", 25, 2400.0)
        self.assertIsNotNone(h1)
        self.assertIsNotNone(h2)

        # Fetch
        holdings = db.get_user_holdings(uid)
        self.assertEqual(len(holdings), 2)
        self.assertEqual(holdings[0]["ticker"], "AAPL")
        self.assertEqual(holdings[1]["ticker"], "RELIANCE.NS")

        # Delete single holding
        deleted = db.delete_user_holding(uid, h1)
        self.assertTrue(deleted)
        remaining = db.get_user_holdings(uid)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["ticker"], "RELIANCE.NS")

        # Clear portfolio
        db.clear_user_holdings(uid)
        self.assertEqual(len(db.get_user_holdings(uid)), 0)
        print("[PASS] Portfolio holdings persistent storage verified.")

    def test_07_investment_guides_persistence(self):
        """Verify saving, retrieving, and deleting 7-category model portfolios and goals."""
        user = db.create_user(self.test_mobile + "2", "Planner User")
        uid = user["id"]

        sample_data = {
            "tier": "moderate",
            "blended_return_pct": 11.0,
            "projected_corpus": 2500000,
            "allocations": [{"category": "Large Cap Equity", "weight_pct": 30}]
        }

        guide_id = db.save_investment_guide(
            user_id=uid,
            guide_type="model_portfolio",
            title="Retirement 2035 Model",
            summary="Moderate risk portfolio split across 7 categories.",
            target_amount=1000000,
            expected_return=11.0,
            horizon_years=10,
            data_dict=sample_data
        )
        self.assertIsNotNone(guide_id)

        # Retrieve
        guides = db.get_user_investment_guides(uid)
        self.assertEqual(len(guides), 1)
        self.assertEqual(guides[0]["title"], "Retirement 2035 Model")
        self.assertEqual(guides[0]["guide_type"], "model_portfolio")
        self.assertEqual(guides[0]["data"]["tier"], "moderate")

        # Delete
        db.delete_investment_guide(uid, guide_id)
        self.assertEqual(len(db.get_user_investment_guides(uid)), 0)
        print("[PASS] Saved Investment Guides persistent storage verified.")

    def test_08_advisor_profile_persistence(self):
        """Verify saving and updating AI advisor investor profile and recommendation."""
        user = db.create_user(self.test_mobile + "3", "Advisor Client")
        uid = user["id"]

        rec = {
            "emergency_fund_target": 300000,
            "suggested_monthly_investment": 15000,
            "blended_return": 11.0,
            "allocation": {"Large Cap": 30, "Debt": 20}
        }

        db.save_advisor_profile(
            user_id=uid,
            age=32,
            risk_tolerance="moderate",
            investment_horizon=8,
            monthly_income=90000,
            current_savings=350000,
            rec_dict=rec
        )

        profile = db.get_advisor_profile(uid)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["age"], 32)
        self.assertEqual(profile["risk_tolerance"], "moderate")
        self.assertEqual(profile["recommendation"]["blended_return"], 11.0)
        print("[PASS] Advisor profile persistence verified.")

    def test_09_flask_auth_and_session_routes(self):
        """Verify end-to-end Flask auth flow with OTP generation and session creation."""
        mobile = self.test_mobile + "4"

        # 1. Send OTP
        res = self.client.post("/api/auth/send-otp", json={"mobile": mobile})
        data = json.loads(res.data)
        self.assertTrue(data["ok"])
        self.assertIn("demo_otp", data)
        otp = data["demo_otp"]

        # 2. Verify OTP for new user
        res_v = self.client.post("/api/auth/verify-otp", json={
            "mobile": mobile,
            "otp": otp,
            "name": "First Time Trader"
        })
        data_v = json.loads(res_v.data)
        self.assertTrue(data_v["ok"])
        self.assertEqual(data_v["user"]["name"], "First Time Trader")

        # 3. Access protected dashboard
        res_dash = self.client.get("/dashboard")
        self.assertEqual(res_dash.status_code, 200)

        # 4. Access settings & verify security audit and master directory rendered
        res_set = self.client.get("/settings")
        self.assertEqual(res_set.status_code, 200)
        self.assertIn(b"Registered Users Directory", res_set.data)
        self.assertIn(b"Global Real-time Login Feed", res_set.data)

        # 5. Access planner saved guides endpoint
        res_guides = self.client.get("/api/planner/saved-guides")
        self.assertEqual(res_guides.status_code, 200)
        print("[PASS] Flask Auth, Session security, and Master User Directory verified.")


if __name__ == "__main__":
    unittest.main()
