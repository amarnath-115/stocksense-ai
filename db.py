"""
Stocksense AI - Zero-Cost SQLite Database Layer
Handles persistent storage for:
- Users & profile metadata
- Mobile OTP verification records
- Real-time login audit & security log (IP, device/browser, status, timestamp)
- Portfolio equity holdings
- Saved Investment Guides & Model Portfolio Roadmaps
- AI Wealth Advisor personal profiles & recommendations
"""

import os
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "stocksense.db")


def get_connection() -> sqlite3.Connection:
    """Returns a SQLite connection with row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Initializes SQLite database tables if they do not exist."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # 1. Users table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mobile TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # 2. OTP records table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS otp_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mobile TEXT NOT NULL,
            otp_code TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            is_used INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # 3. Security Audit & Login Logs table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS login_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            mobile TEXT NOT NULL,
            ip_address TEXT,
            user_agent TEXT,
            status TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """)

        # 4. Portfolio holdings table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            qty REAL NOT NULL,
            buy_price REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)

        # 5. Saved Investment Guides table (Model Portfolios, Required Returns, Goal SIPs)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS saved_investment_guides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            guide_type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            target_amount REAL DEFAULT 0,
            expected_return REAL DEFAULT 0,
            horizon_years INTEGER DEFAULT 5,
            data_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)

        # 6. Advisor profiles table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS advisor_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            age INTEGER,
            risk_tolerance TEXT,
            investment_horizon INTEGER,
            monthly_income REAL,
            current_savings REAL,
            recommendation_json TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)

        # 7. Backend Stored Company Profiles Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            sector TEXT NOT NULL,
            industry TEXT NOT NULL,
            headquarters TEXT,
            founded_year INTEGER,
            founder_ceo TEXT,
            business_summary TEXT,
            key_products TEXT,
            market_presence TEXT,
            website TEXT,
            market_cap_tier TEXT,
            pe_ratio TEXT,
            annual_revenue TEXT,
            net_profit TEXT,
            employees TEXT,
            competitive_advantages TEXT,
            data_json TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()

    # Seed initial companies if table is empty or missing key entries
    seed_default_companies()



# ===========================================================================
# USER MANAGEMENT
# ===========================================================================
def get_user_by_mobile(mobile: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE mobile = ?", (str(mobile).strip(),))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def create_user(mobile: str, name: str) -> Dict[str, Any]:
    with get_connection() as conn:
        cursor = conn.cursor()
        clean_mobile = str(mobile).strip()
        clean_name = str(name).strip() or "Trader"
        cursor.execute(
            "INSERT INTO users (mobile, name, created_at, last_login) VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (clean_mobile, clean_name)
        )
        conn.commit()
        user_id = cursor.lastrowid
        return {"id": user_id, "mobile": clean_mobile, "name": clean_name}


def get_or_create_user(mobile: str, name: str = "Trader") -> Dict[str, Any]:
    existing = get_user_by_mobile(mobile)
    if existing:
        return existing
    return create_user(mobile, name)



def update_user_name(user_id: int, new_name: str) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET name = ? WHERE id = ?", (new_name.strip(), user_id))
        conn.commit()
        return cursor.rowcount > 0


def update_user_last_login(user_id: int):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
        conn.commit()


# ===========================================================================
# OTP MANAGEMENT
# ===========================================================================
def save_otp(mobile: str, otp_code: str, validity_minutes: int = 5):
    with get_connection() as conn:
        cursor = conn.cursor()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=validity_minutes)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO otp_records (mobile, otp_code, expires_at, is_used) VALUES (?, ?, ?, 0)",
            (str(mobile).strip(), str(otp_code).strip(), expires_at)
        )
        conn.commit()


def verify_otp(mobile: str, otp_code: str) -> bool:
    """Checks if the OTP matches, is not expired, and has not been used."""
    with get_connection() as conn:
        cursor = conn.cursor()
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            """
            SELECT id FROM otp_records
            WHERE mobile = ? AND otp_code = ? AND is_used = 0 AND expires_at >= ?
            ORDER BY id DESC LIMIT 1
            """,
            (str(mobile).strip(), str(otp_code).strip(), now_str)
        )
        row = cursor.fetchone()
        if row:
            record_id = row["id"]
            cursor.execute("UPDATE otp_records SET is_used = 1 WHERE id = ?", (record_id,))
            conn.commit()
            return True
        return False


# ===========================================================================
# SECURITY & LOGIN AUDIT LOGS
# ===========================================================================
def log_login_attempt(mobile: str, status: str, ip_address: str = "Unknown", user_agent: str = "Unknown", user_id: Optional[int] = None):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO login_logs (user_id, mobile, ip_address, user_agent, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, str(mobile).strip(), ip_address, user_agent, status)
        )
        conn.commit()


def get_login_logs(user_id: Optional[int] = None, mobile: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        if user_id:
            cursor.execute(
                """
                SELECT l.id, l.user_id, l.mobile, l.ip_address, l.user_agent, l.status, l.timestamp,
                       COALESCE(u.name, 'Trader') as user_name
                FROM login_logs l
                LEFT JOIN users u ON l.user_id = u.id OR l.mobile = u.mobile
                WHERE l.user_id = ? ORDER BY l.id DESC LIMIT ?
                """,
                (user_id, limit)
            )
        elif mobile:
            cursor.execute(
                """
                SELECT l.id, l.user_id, l.mobile, l.ip_address, l.user_agent, l.status, l.timestamp,
                       COALESCE(u.name, 'Trader') as user_name
                FROM login_logs l
                LEFT JOIN users u ON l.user_id = u.id OR l.mobile = u.mobile
                WHERE l.mobile = ? ORDER BY l.id DESC LIMIT ?
                """,
                (mobile, limit)
            )
        else:
            cursor.execute(
                """
                SELECT l.id, l.user_id, l.mobile, l.ip_address, l.user_agent, l.status, l.timestamp,
                       COALESCE(u.name, 'Visitor') as user_name
                FROM login_logs l
                LEFT JOIN users u ON l.user_id = u.id OR l.mobile = u.mobile
                ORDER BY l.id DESC LIMIT ?
                """,
                (limit,)
            )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def get_daily_otp_count(mobile: str) -> int:
    """Returns number of OTP requests made for this mobile in the past 24 hours."""
    with get_connection() as conn:
        cursor = conn.cursor()
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "SELECT COUNT(*) as c FROM otp_records WHERE mobile = ? AND created_at >= ?",
            (str(mobile).strip(), since)
        )
        row = cursor.fetchone()
        return row["c"] if row else 0


def get_all_users_summary() -> List[Dict[str, Any]]:
    """Returns a directory of all registered users with their mobile numbers and names."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 
                u.id, u.mobile, u.name, u.created_at, u.last_login,
                (SELECT COUNT(*) FROM portfolio_holdings WHERE user_id = u.id) as holdings_count,
                (SELECT COUNT(*) FROM saved_investment_guides WHERE user_id = u.id) as guides_count,
                (SELECT COUNT(*) FROM login_logs WHERE (user_id = u.id OR mobile = u.mobile) AND status = 'SUCCESS') as logins_count
            FROM users u
            ORDER BY u.id DESC
            """
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def get_today_logins_count() -> int:
    """Returns total successful logins across the platform in the last 24 hours."""
    with get_connection() as conn:
        cursor = conn.cursor()
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "SELECT COUNT(*) as c FROM login_logs WHERE status = 'SUCCESS' AND timestamp >= ?",
            (since,)
        )
        row = cursor.fetchone()
        return row["c"] if row else 0



# ===========================================================================
# PORTFOLIO HOLDINGS MANAGEMENT
# ===========================================================================
def get_user_holdings(user_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, ticker, qty, buy_price, created_at FROM portfolio_holdings WHERE user_id = ? ORDER BY id ASC",
            (user_id,)
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def add_user_holding(user_id: int, ticker: str, qty: float, buy_price: float) -> int:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO portfolio_holdings (user_id, ticker, qty, buy_price) VALUES (?, ?, ?, ?)",
            (user_id, ticker.strip().upper(), float(qty), float(buy_price))
        )
        conn.commit()
        return cursor.lastrowid


def delete_user_holding(user_id: int, holding_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM portfolio_holdings WHERE user_id = ? AND id = ?", (user_id, holding_id))
        conn.commit()
        return cursor.rowcount > 0


def clear_user_holdings(user_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM portfolio_holdings WHERE user_id = ?", (user_id,))
        conn.commit()
        return True


# ===========================================================================
# SAVED INVESTMENT GUIDES & PLANS
# ===========================================================================
def save_investment_guide(
    user_id: int,
    guide_type: str,
    title: str,
    summary: str,
    target_amount: float,
    expected_return: float,
    horizon_years: int,
    data_dict: dict
) -> int:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO saved_investment_guides
            (user_id, guide_type, title, summary, target_amount, expected_return, horizon_years, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                guide_type,
                title.strip(),
                summary.strip() if summary else "",
                float(target_amount or 0),
                float(expected_return or 0),
                int(horizon_years or 5),
                json.dumps(data_dict)
            )
        )
        conn.commit()
        return cursor.lastrowid


def get_user_investment_guides(user_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, guide_type, title, summary, target_amount, expected_return, horizon_years, data_json, created_at
            FROM saved_investment_guides
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (user_id,)
        )
        rows = cursor.fetchall()
        guides = []
        for r in rows:
            d = dict(r)
            try:
                d["data"] = json.loads(d["data_json"])
            except Exception:
                d["data"] = {}
            guides.append(d)
        return guides


def get_investment_guide_by_id(user_id: int, guide_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, guide_type, title, summary, target_amount, expected_return, horizon_years, data_json, created_at
            FROM saved_investment_guides
            WHERE user_id = ? AND id = ?
            """,
            (user_id, guide_id)
        )
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["data"] = json.loads(d["data_json"])
        except Exception:
            d["data"] = {}
        return d


def delete_investment_guide(user_id: int, guide_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM saved_investment_guides WHERE user_id = ? AND id = ?", (user_id, guide_id))
        conn.commit()
        return cursor.rowcount > 0


# ===========================================================================
# ADVISOR PROFILES & RECOMMENDATIONS
# ===========================================================================
def save_advisor_profile(
    user_id: int,
    age: int,
    risk_tolerance: str,
    investment_horizon: int,
    monthly_income: float,
    current_savings: float,
    rec_dict: dict
):
    with get_connection() as conn:
        cursor = conn.cursor()
        rec_json = json.dumps(rec_dict)
        cursor.execute(
            """
            INSERT INTO advisor_profiles
            (user_id, age, risk_tolerance, investment_horizon, monthly_income, current_savings, recommendation_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                age=excluded.age,
                risk_tolerance=excluded.risk_tolerance,
                investment_horizon=excluded.investment_horizon,
                monthly_income=excluded.monthly_income,
                current_savings=excluded.current_savings,
                recommendation_json=excluded.recommendation_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (user_id, int(age), str(risk_tolerance), int(investment_horizon), float(monthly_income), float(current_savings), rec_json)
        )
        conn.commit()


def get_advisor_profile(user_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM advisor_profiles WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["recommendation"] = json.loads(d["recommendation_json"])
        except Exception:
            d["recommendation"] = {}
        return d


# ===========================================================================
# USER DASHBOARD STATISTICS
# ===========================================================================
def get_user_stats(user_id: int) -> Dict[str, Any]:
    with get_connection() as conn:
        cursor = conn.cursor()

        # Holdings count
        cursor.execute("SELECT COUNT(*) as c FROM portfolio_holdings WHERE user_id = ?", (user_id,))
        holdings_count = cursor.fetchone()["c"]

        # Guides count
        cursor.execute("SELECT COUNT(*) as c FROM saved_investment_guides WHERE user_id = ?", (user_id,))
        guides_count = cursor.fetchone()["c"]

        # Logins count
        cursor.execute("SELECT COUNT(*) as c FROM login_logs WHERE user_id = ? AND status = 'SUCCESS'", (user_id,))
        logins_count = cursor.fetchone()["c"]

        # User row
        cursor.execute("SELECT mobile, name, created_at, last_login FROM users WHERE id = ?", (user_id,))
        user_row = cursor.fetchone()

        return {
            "holdings_count": holdings_count,
            "guides_count": guides_count,
            "logins_count": logins_count,
            "mobile": user_row["mobile"] if user_row else "N/A",
            "name": user_row["name"] if user_row else "Trader",
            "created_at": user_row["created_at"] if user_row else "N/A",
            "last_login": user_row["last_login"] if user_row else "N/A"
        }


# ===========================================================================
# BACKEND COMPANY METADATA & INSTITUTIONAL DOSSIERS
# ===========================================================================
def get_company_by_ticker(ticker: str) -> Optional[Dict[str, Any]]:
    """Retrieves full stored backend company details by ticker or symbol alias."""
    if not ticker:
        return None
    raw_ticker = str(ticker).strip().upper()
    # Normalize ticker (e.g. check both LALITHA and LALITHA.NS)
    lookup_keys = [raw_ticker]
    if not raw_ticker.endswith(".NS") and not "." in raw_ticker:
        lookup_keys.append(f"{raw_ticker}.NS")
    elif raw_ticker.endswith(".NS"):
        lookup_keys.append(raw_ticker[:-3])

    with get_connection() as conn:
        cursor = conn.cursor()
        for key in lookup_keys:
            cursor.execute("SELECT * FROM companies WHERE ticker = ? OR ticker LIKE ?", (key, f"%{key}%"))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                try:
                    d["data_json_parsed"] = json.loads(d["data_json"]) if d.get("data_json") else {}
                except Exception:
                    d["data_json_parsed"] = {}
                return d
    return None


def get_all_stored_companies() -> List[Dict[str, Any]]:
    """Returns a list of all companies permanently stored in the backend database."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM companies ORDER BY sector ASC, name ASC")
        rows = cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["data_json_parsed"] = json.loads(d["data_json"]) if d.get("data_json") else {}
            except Exception:
                d["data_json_parsed"] = {}
            result.append(d)
        return result


def upsert_company(comp: Dict[str, Any]) -> bool:
    """Inserts or updates an institutional company dossier in the database."""
    ticker = str(comp.get("ticker", "")).strip().upper()
    if not ticker:
        return False
    data_json = comp.get("data_json")
    if isinstance(data_json, (dict, list)):
        data_json = json.dumps(data_json)
    elif not data_json:
        data_json = "{}"

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO companies (
            ticker, name, sector, industry, headquarters, founded_year,
            founder_ceo, business_summary, key_products, market_presence,
            website, market_cap_tier, pe_ratio, annual_revenue, net_profit,
            employees, competitive_advantages, data_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(ticker) DO UPDATE SET
            name = excluded.name,
            sector = excluded.sector,
            industry = excluded.industry,
            headquarters = excluded.headquarters,
            founded_year = excluded.founded_year,
            founder_ceo = excluded.founder_ceo,
            business_summary = excluded.business_summary,
            key_products = excluded.key_products,
            market_presence = excluded.market_presence,
            website = excluded.website,
            market_cap_tier = excluded.market_cap_tier,
            pe_ratio = excluded.pe_ratio,
            annual_revenue = excluded.annual_revenue,
            net_profit = excluded.net_profit,
            employees = excluded.employees,
            competitive_advantages = excluded.competitive_advantages,
            data_json = excluded.data_json,
            updated_at = CURRENT_TIMESTAMP
        """, (
            ticker,
            comp.get("name", ticker),
            comp.get("sector", "Consumer / Equities"),
            comp.get("industry", "General Industry"),
            comp.get("headquarters", "India / Global"),
            int(comp.get("founded_year", 2000)),
            comp.get("founder_ceo", "Executive Management"),
            comp.get("business_summary", ""),
            comp.get("key_products", ""),
            comp.get("market_presence", ""),
            comp.get("website", ""),
            comp.get("market_cap_tier", "Mid Cap"),
            str(comp.get("pe_ratio", "N/A")),
            str(comp.get("annual_revenue", "N/A")),
            str(comp.get("net_profit", "N/A")),
            str(comp.get("employees", "N/A")),
            comp.get("competitive_advantages", ""),
            data_json
        ))
        conn.commit()
        return True


def seed_default_companies():
    """Seeds rich, institutional dossiers for prominent companies including Lalitha Jewellery."""
    default_companies = [
        {
            "ticker": "LALITHA.NS",
            "name": "Lalitha Jewellery Mart Private Limited",
            "sector": "Consumer Discretionary / Gems & Jewellery",
            "industry": "Jewellery Manufacturing & Retail Chain",
            "headquarters": "Chennai, Tamil Nadu, India",
            "founded_year": 1985,
            "founder_ceo": "M. Kiran Kumar (Founder, Chairman & Managing Director)",
            "business_summary": (
                "Lalitha Jewellery Mart Private Limited is one of South India's largest, most prestigious, and widely recognized retail jewellery giants. "
                "Celebrated for its pioneering consumer advocacy campaigns and the famous transparent slogan 'Gubeer Gubeer' led personally by MD Kiran Kumar, "
                "Lalitha Jewellery dismantled traditional opaque jewellery markups by instituting transparent per-gram cost breakdowns, zero-wastage (V.A.) charges, "
                "and strictly verified BIS 916 hallmarked gold bullion standards. Operating monumental standalone mega-marts spanning up to 50,000 sq.ft across "
                "Tamil Nadu, Andhra Pradesh, Telangana, Karnataka, and Kerala, the enterprise provides unmatched mass-market volume, festive bridal collections, "
                "and consumer gold-savings schemes (Swarna Labham)."
            ),
            "key_products": (
                "BIS 916 Hallmarked 22K Gold Ornaments, Certified VVS-EF Natural Diamonds, Pure 92.5 Sterling Silver Articles & Pooja Items, "
                "Antique Temple Jewellery, Swarna Labham 11-Month Monthly Gold Savings Scheme, Platinum Couple Bands, Lightweight Dailywear Gold"
            ),
            "market_presence": (
                "50+ Massive Super-Store Showrooms across key urban hubs: Chennai (Panagal Park T. Nagar flagship, Anna Nagar, T. Nagar North), "
                "Coimbatore, Madurai, Tiruchirappalli, Salem, Tirunelveli, Hyderabad (Somajiguda, Kukatpally, Dilsukhnagar), Vijayawada, Visakhapatnam, "
                "Bengaluru, and Kochi. Serving over 15+ million families across South India."
            ),
            "website": "https://www.lalithajewellery.com",
            "market_cap_tier": "Large Private Retailer (Estimated Valuation ₹12,500+ Cr)",
            "pe_ratio": "22.5 (Industry Median Benchmark)",
            "annual_revenue": "₹14,500+ Crores (FY24-25 Run Rate)",
            "net_profit": "₹640+ Crores",
            "employees": "8,500+ Dedicated Craftsmen, Artisans & Staff",
            "competitive_advantages": (
                "1. Zero-Wastage / Nil Value Addition (V.A.) pricing pioneer in South India.\n"
                "2. Direct bullion refinery procurement ensuring competitive per-gram board rates.\n"
                "3. High retail brand equity and direct founder-driven consumer transparency marketing.\n"
                "4. Enormous floor inventory per showroom offering unprecedented selection variety.\n"
                "5. High cash-flow customer advance schemes (Swarna Labham) generating sustained liquidity."
            ),
            "data_json": {
                "gold_purity_standard": "100% BIS 916 Hallmarked (22 Karat)",
                "diamond_certification": "IGI / SGL Certified VVS-DEF",
                "savings_scheme": "Swarna Labham (11 Months advance with bonus contribution)",
                "benchmark_price_inr": 845.50,
                "gold_peg_equivalent_g": 0.115,
                "annual_gold_turnover_tonnes": "18.5+ Metric Tonnes",
                "key_executives": ["M. Kiran Kumar (CMD)", "K. Hemanth Kumar (Director)"],
                "tagline": "Truth alone triumphs — Buy gold by weight, not by blind trust."
            }
        },
        {
            "ticker": "TITAN.NS",
            "name": "Titan Company Limited",
            "sector": "Consumer Discretionary / Luxury & Retail",
            "industry": "Jewellery, Precision Watches & Eyewear (Tata Group)",
            "headquarters": "Bengaluru, Karnataka, India",
            "founded_year": 1984,
            "founder_ceo": "Xerxes Desai (Founding MD) • C.K. Venkataraman (Managing Director) • Tata Sons & TIDCO",
            "business_summary": (
                "Titan Company Limited, an iconic flagship enterprise of the Tata Group, is India's preeminent lifestyle and luxury retail powerhouse. "
                "Through its flagship brand Tanishq, along with Zoya, Mia, and CaratLane, Titan commands undisputed leadership in the organized Indian jewellery market. "
                "The company revolutionized Indian jewellery retailing by introducing the Karatmeter (non-destructive XRF gold purity tester) and institutional trust. "
                "Titan also dominates Indian precision watchmaking (Titan, Fastrack, Sonata, Nebula, Edge, Xylys), Titan Eye+ prescription eyewear, and Taneira handcrafted ethnic wear."
            ),
            "key_products": (
                "Tanishq 22K/18K Gold & Polki Wedding Jewellery, CaratLane Everyday Diamond Jewellery, Zoya Luxury Haute Joaillerie, Mia Contemporary Workwear Gold, "
                "Titan Smartwatches & Mechanical Chronographs, Fastrack Youth Accessories, Titan Eye+ Eyewear, Taneira Handcrafted Sarees, Skinn Fine Fragrances"
            ),
            "market_presence": (
                "3,050+ Exclusive Retail Stores spanning over 400+ cities in India, with international boutique showrooms in Dubai, Abu Dhabi, Singapore, and New Jersey USA."
            ),
            "website": "https://www.titancompany.in",
            "market_cap_tier": "Mega / Large Cap (₹2,95,000+ Crores)",
            "pe_ratio": "84.5",
            "annual_revenue": "₹51,080 Crores",
            "net_profit": "₹3,496 Crores",
            "employees": "12,200+ Corporate & Retail Employees",
            "competitive_advantages": (
                "1. The invincible integrity and customer trust of the Tata brand.\n"
                "2. Largest organized retail store footprint in India with omnichannel CaratLane integration.\n"
                "3. Karatmeter 100% accurate gold purity standard.\n"
                "4. Proven customer loyalty with Golden Harvest monthly purchase plans.\n"
                "5. Exceptional design intellectual property and artisan clusters across India."
            ),
            "data_json": {
                "tata_group_holding": "52.9% (Tata Sons + TIDCO)",
                "tanishq_market_share": "Organized Jewellery Leader (~38% of organized market)",
                "sub_brands": ["Tanishq", "CaratLane", "Mia", "Zoya", "Fastrack", "Sonata", "Taneira", "Titan Eye+"]
            }
        },
        {
            "ticker": "KALYANKJIL.NS",
            "name": "Kalyan Jewellers India Limited",
            "sector": "Consumer Discretionary / Gems & Jewellery",
            "industry": "Pan-India Jewellery Retail Chain",
            "headquarters": "Thrissur, Kerala, India",
            "founded_year": 1993,
            "founder_ceo": "T.S. Kalyanaraman (Founder, Chairman & MD)",
            "business_summary": (
                "Kalyan Jewellers India Ltd is one of India's largest and most expansive retail jewellery networks. Founded in Thrissur, Kerala by T.S. Kalyanaraman, "
                "the brand pioneered regional curation, catering to distinct cultural bridal preferences across 21 Indian states and the Arabian Gulf. "
                "Backed by Warburg Pincus, Kalyan operates through large-format experiential showrooms, its grassroots 'My Kalyan' neighborhood ambassador network, "
                "and digital-first fine jewellery brand Candere."
            ),
            "key_products": (
                "Muhurat Bridal Wedding Collection, Mudhra Handcrafted Antique Jewellery, Anokhi Uncut Natural Diamonds, Glo Dancing Diamonds, "
                "Nimah Heritage Temple Jewellery, Rang Precious Gemstones, Candere Everyday Diamond & Gold Collections"
            ),
            "market_presence": "285+ Mega Showrooms across India, UAE, Qatar, Kuwait, and Oman, supported by 1,000+ My Kalyan customer centers.",
            "website": "https://www.kalyanjewellers.net",
            "market_cap_tier": "Large Cap (₹58,500+ Crores)",
            "pe_ratio": "68.2",
            "annual_revenue": "₹18,550 Crores",
            "net_profit": "₹596 Crores",
            "employees": "9,200+ Employees",
            "competitive_advantages": (
                "1. Deep regional hyper-localization of jewellery designs across diverse linguistic states.\n"
                "2. 4-Level Assurance Certification on gold purity, exchange value, and life-long maintenance.\n"
                "3. Strategic Middle East presence capturing lucrative NRI remittance inflows.\n"
                "4. Rapidly growing franchise-light (FOCO) showroom expansion model."
            ),
            "data_json": {
                "international_presence": "40+ Gulf Showrooms",
                "franchise_model": "Capital-efficient FOCO expansion",
                "e_commerce": "Candere.com Omnichannel"
            }
        },
        {
            "ticker": "THANGAMAYL.NS",
            "name": "Thangamayil Jewellery Limited",
            "sector": "Consumer Discretionary / Retail",
            "industry": "Regional Jewellery Superstore Chain",
            "headquarters": "Madurai, Tamil Nadu, India",
            "founded_year": 2000,
            "founder_ceo": "Balarama Govinda Das (Chairman & Managing Director)",
            "business_summary": (
                "Thangamayil Jewellery Limited is a premier regional jewellery retailer dominating Tier-2, Tier-3, and rural hubs across Tamil Nadu. "
                "With deep heritage rooted in Madurai, Thangamayil built a high-turnover hub-and-spoke showroom model bringing pure BIS hallmarked gold, "
                "elaborate silver temple wares, and diamond ornaments to semi-urban families who previously lacked access to certified organized retail."
            ),
            "key_products": (
                "Traditional Tamil Bridal Gold, 925 Hallmarked Silver Articles, Diamond Jewellery, Thangamayil Super Gold Savings Schemes, Antique Ornaments"
            ),
            "market_presence": "60+ Showrooms strategically distributed across southern and central Tamil Nadu.",
            "website": "https://www.thangamayil.com",
            "market_cap_tier": "Small/Mid Cap (₹2,650+ Crores)",
            "pe_ratio": "29.4",
            "annual_revenue": "₹3,820 Crores",
            "net_profit": "₹118 Crores",
            "employees": "2,200+ Personnel",
            "competitive_advantages": "Unrivaled grassroots brand loyalty in southern Tamil Nadu; low operating showroom costs; high inventory turn ratio.",
            "data_json": {
                "regional_dominance": "Tier-2/3 Tamil Nadu Specialist",
                "silver_segment": "Major market share in South Indian silver vessels"
            }
        },
        {
            "ticker": "RELIANCE.NS",
            "name": "Reliance Industries Limited",
            "sector": "Energy, Telecommunications & Retail",
            "industry": "Diversified Conglomerate (Fortune 500)",
            "headquarters": "Mumbai, Maharashtra, India",
            "founded_year": 1966,
            "founder_ceo": "Dhirubhai Ambani (Founder) • Mukesh D. Ambani (Chairman & Managing Director)",
            "business_summary": (
                "Reliance Industries Limited is India's largest private sector corporation and a Fortune Global 500 powerhouse. "
                "Its diverse operations encompass hydrocarbon exploration, petroleum refining (Jamnagar, world's largest refining complex), "
                "petrochemicals, India's largest telecom and digital ecosystem (Jio Infocomm with 470M+ subscribers), India's largest organized retail network "
                "(Reliance Retail with 18,000+ stores), and new green energy gigafactories."
            ),
            "key_products": (
                "Jio 5G Network & Digital Cloud Services, Reliance Retail / JioMart, Petrochemicals (Polymers, Polyesters), Jamnagar Transportation Fuels, "
                "Green Energy Solar PV & Hydrogen Gigafactories, Media & Entertainment (JioCinema / Viacom18)"
            ),
            "market_presence": "Operations across 100+ countries, 18,500+ retail stores in India, pan-India 5G wireless footprint.",
            "website": "https://www.ril.com",
            "market_cap_tier": "Mega Cap (₹20,50,000+ Crores / #1 in India)",
            "pe_ratio": "28.5",
            "annual_revenue": "₹10,00,000+ Crores",
            "net_profit": "₹79,000+ Crores",
            "employees": "389,000+ Employees",
            "competitive_advantages": "Unmatched scale, Jamnagar integrated complexity, pan-India 5G standalone digital dominance, massive supply chain moat.",
            "data_json": {"jio_subscribers": "470M+", "retail_stores": "18,500+"}
        },
        {
            "ticker": "TCS.NS",
            "name": "Tata Consultancy Services Limited",
            "sector": "Information Technology / Consulting",
            "industry": "Global IT Services & Digital Transformation",
            "headquarters": "Mumbai, Maharashtra, India",
            "founded_year": 1968,
            "founder_ceo": "F.C. Kohli (Father of Indian IT) • K. Krithivasan (CEO & MD) • Tata Sons",
            "business_summary": (
                "Tata Consultancy Services is the flagship IT enterprise of the Tata Group and the world's second-largest IT services company by market value. "
                "Providing end-to-end consulting, cloud migration, cybersecurity, and enterprise AI transformation to 80% of Fortune 500 corporations."
            ),
            "key_products": "Cognitive Enterprise Solutions, Cloud Migration (AWS/Azure/GCP), BaNCS Banking Software, Enterprise Generative AI, Cybersecurity",
            "market_presence": "Offices and delivery centers across 55 countries, serving 1,400+ enterprise clients globally.",
            "website": "https://www.tcs.com",
            "market_cap_tier": "Mega Cap (₹15,20,000+ Crores)",
            "pe_ratio": "31.2",
            "annual_revenue": "₹240,890 Crores",
            "net_profit": "₹46,099 Crores",
            "employees": "601,000+ Software Engineers",
            "competitive_advantages": "Highest operating margins in the IT sector (~26%), Tata brand trust, massive talent pool, industry-low attrition.",
            "data_json": {"attrition_rate": "12.1%", "patents": "7,800+"}
        },
        {
            "ticker": "INFY.NS",
            "name": "Infosys Limited",
            "sector": "Information Technology",
            "industry": "Next-Generation Digital Services & Consulting",
            "headquarters": "Bengaluru, Karnataka, India",
            "founded_year": 1981,
            "founder_ceo": "N.R. Narayana Murthy, Nandan Nilekani (Founders) • Salil Parekh (CEO & MD)",
            "business_summary": (
                "Infosys is a global leader in next-generation digital services and consulting. Pioneers of the Global Delivery Model, "
                "Infosys empowers enterprise clients in more than 56 countries to navigate cloud transformations and generative AI through Infosys Topaz and Cobalt."
            ),
            "key_products": "Infosys Topaz (Generative AI Suite), Infosys Cobalt (Cloud Solution), Finacle Core Banking, Digital Engineering, Enterprise Agile",
            "market_presence": "Clients across 56 countries, major tech centers in Bengaluru, Hyderabad, Pune, London, and San Jose.",
            "website": "https://www.infosys.com",
            "market_cap_tier": "Mega Cap (₹7,80,000+ Crores)",
            "pe_ratio": "28.0",
            "annual_revenue": "₹153,670 Crores",
            "net_profit": "₹26,248 Crores",
            "employees": "317,000+ Technologists",
            "competitive_advantages": "Finacle banking software powering 1.3B customer accounts worldwide, strong digital engineering suite.",
            "data_json": {"finacle_users": "1.3B Bank Accounts"}
        },
        {
            "ticker": "TATAMOTORS.NS",
            "name": "Tata Motors Limited",
            "sector": "Automotive / Mobility",
            "industry": "Automobile Manufacturer, Electric Vehicles & Luxury Cars",
            "headquarters": "Mumbai, Maharashtra, India",
            "founded_year": 1945,
            "founder_ceo": "J.R.D. Tata (Founder) • Shailesh Chandra (MD Tata Motors Passenger & EV) • PB Balaji",
            "business_summary": (
                "Tata Motors is a $44 billion global automotive giant leading India's electric vehicle revolution with >70% EV passenger market share "
                "(Nexon EV, Tiago EV, Punch EV, Curvv EV) and operating the luxury brand Jaguar Land Rover (Range Rover, Defender, Jaguar)."
            ),
            "key_products": "Range Rover & Defender SUVs, Tata Nexon EV & Punch EV, Prima Commercial Trucks, Starbus Buses, Gen-3 Pure EV Architectures",
            "market_presence": "Commercial and passenger vehicle sales across 125+ countries with assembly operations in UK, India, and Slovakia.",
            "website": "https://www.tatamotors.com",
            "market_cap_tier": "Large Cap (₹3,60,000+ Crores)",
            "pe_ratio": "10.8",
            "annual_revenue": "₹437,928 Crores",
            "net_profit": "₹31,807 Crores",
            "employees": "81,000+ Employees",
            "competitive_advantages": "Monopolistic EV market share in India, high-margin JLR luxury order bank, integrated battery supply via Agratas.",
            "data_json": {"ev_market_share_india": "71%", "jlr_order_bank": "148,000+ Vehicles"}
        },
        {
            "ticker": "HDFCBANK.NS",
            "name": "HDFC Bank Limited",
            "sector": "Financial Services / Banking",
            "industry": "Commercial & Retail Banking",
            "headquarters": "Mumbai, Maharashtra, India",
            "founded_year": 1994,
            "founder_ceo": "Aditya Puri (Founding MD) • Sashidhar Jagdishan (MD & CEO)",
            "business_summary": (
                "HDFC Bank is India's largest private sector bank and one of the world's top 10 most capitalized banking institutions following its mega-merger with HDFC Ltd. "
                "Serving over 90+ million customers with pristine credit discipline, deep digital distribution, and robust mortgages and corporate lending."
            ),
            "key_products": "Home Loans, Credit Cards, Corporate Working Capital, SME Loans, Wealth Management, Digital Banking (PayZapp, SmartBuy)",
            "market_presence": "8,800+ Branches and 20,000+ ATMs across India.",
            "website": "https://www.hdfcbank.com",
            "market_cap_tier": "Mega Cap (₹12,80,000+ Crores)",
            "pe_ratio": "18.6",
            "annual_revenue": "₹285,000+ Crores",
            "net_profit": "₹60,810 Crores",
            "employees": "213,000+ Bankers",
            "competitive_advantages": "Lowest non-performing asset (NPA) ratios in Indian banking history, massive CASA deposit base, leader in retail credit cards.",
            "data_json": {"gross_npa": "1.24%", "customers": "93 Million+"}
        },
        {
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "sector": "Information Technology / Consumer Tech",
            "industry": "Consumer Electronics, Software & Cloud Services",
            "headquarters": "Cupertino, California, USA",
            "founded_year": 1976,
            "founder_ceo": "Steve Jobs & Steve Wozniak (Founders) • Tim Cook (CEO)",
            "business_summary": (
                "Apple Inc. is the world's most valuable consumer technology company, designing iconic hardware, custom Apple Silicon processors, "
                "and a walled-garden digital services ecosystem spanning 2.2+ billion active devices globally."
            ),
            "key_products": "iPhone, Mac, iPad, Apple Watch, AirPods, Apple Vision Pro, iOS, macOS, App Store, iCloud, Apple Pay, Apple Intelligence",
            "market_presence": "530+ Apple Retail Stores globally with distribution in every continent.",
            "website": "https://www.apple.com",
            "market_cap_tier": "Mega Cap ($3.4+ Trillion USD)",
            "pe_ratio": "33.8",
            "annual_revenue": "$385.6 Billion USD",
            "net_profit": "$100.3 Billion USD",
            "employees": "161,000+ Employees",
            "competitive_advantages": "Unrivaled brand loyalty, high switching costs, Apple Silicon M-series/A-series silicon efficiency, massive high-margin Services revenue.",
            "data_json": {"active_devices": "2.2 Billion+", "services_gross_margin": "74%"}
        },
        {
            "ticker": "NVDA",
            "name": "NVIDIA Corporation",
            "sector": "Semiconductors / Artificial Intelligence",
            "industry": "Accelerated Computing Hardware & AI Platforms",
            "headquarters": "Santa Clara, California, USA",
            "founded_year": 1993,
            "founder_ceo": "Jensen Huang (Founder, President & CEO)",
            "business_summary": (
                "NVIDIA pioneered GPU-accelerated computing and has become the indispensable bedrock of modern Generative AI. "
                "Its Hopper (H100/H200) and Blackwell (B200/GB200) architectures, combined with the proprietary CUDA software stack, power global AI hyperscalers."
            ),
            "key_products": "Blackwell GB200 NVL72 AI Supercomputers, Hopper H100/H200 GPUs, CUDA Software Ecosystem, GeForce RTX Gaming GPUs, Mellanox Quantum InfiniBand",
            "market_presence": "Global presence powering AWS, Microsoft Azure, Google Cloud, Meta, Oracle, and Tier-1 AI researchers worldwide.",
            "website": "https://www.nvidia.com",
            "market_cap_tier": "Mega Cap ($3.2+ Trillion USD)",
            "pe_ratio": "52.4",
            "annual_revenue": "$120+ Billion USD",
            "net_profit": "$65+ Billion USD",
            "employees": "30,000+ AI Architects & Engineers",
            "competitive_advantages": "CUDA software ecosystem creating massive developer lock-in, full-stack data center interconnects (NVLink/InfiniBand), generational computing lead.",
            "data_json": {"data_center_share": "90%+ of AI training workload"}
        },
        {
            "ticker": "MSFT",
            "name": "Microsoft Corporation",
            "sector": "Information Technology / Cloud",
            "industry": "Enterprise Software, Cloud Computing & AI",
            "headquarters": "Redmond, Washington, USA",
            "founded_year": 1975,
            "founder_ceo": "Bill Gates & Paul Allen (Founders) • Satya Nadella (Chairman & CEO)",
            "business_summary": (
                "Microsoft Corporation is a global technology powerhouse delivering mission-critical enterprise software, Azure Cloud infrastructure, "
                "and world-leading Generative AI integrations via Copilot and its deep partnership with OpenAI."
            ),
            "key_products": "Microsoft Azure, Microsoft 365 Copilot, Windows 11, LinkedIn, GitHub, Xbox Gaming Platform, Dynamics 365, Teams",
            "market_presence": "Offices in 190+ countries serving 95% of Fortune 500 enterprises.",
            "website": "https://www.microsoft.com",
            "market_cap_tier": "Mega Cap ($3.1+ Trillion USD)",
            "pe_ratio": "34.5",
            "annual_revenue": "$245 Billion USD",
            "net_profit": "$88 Billion USD",
            "employees": "228,000+ Employees",
            "competitive_advantages": "Immense enterprise lock-in with Windows & Office, rapid Azure AI scaling, GitHub developer moat.",
            "data_json": {"azure_revenue_growth": "29% YoY"}
        },
        {
            "ticker": "TSLA",
            "name": "Tesla Inc.",
            "sector": "Automotive / Clean Energy & AI",
            "industry": "Electric Vehicles, Energy Storage & Autonomous Robotics",
            "headquarters": "Austin, Texas, USA",
            "founded_year": 2003,
            "founder_ceo": "Martin Eberhard & Marc Tarpenning (Founders) • Elon Musk (Technoking & CEO)",
            "business_summary": (
                "Tesla is an automotive, artificial intelligence, and clean energy pioneer producing high-volume electric vehicles (Model Y, Model 3, Cybertruck), "
                "utility-scale energy storage (Megapack, Powerwall), Full Self-Driving (FSD) neural vision models, and the Optimus humanoid robot."
            ),
            "key_products": "Model Y (World's Best-Selling Car), Model 3, Cybertruck, Megapack 2XL Utility Storage, Supercharger Network, Full Self-Driving (FSD) Supervised, Optimus Humanoid Robot",
            "market_presence": "Gigafactories in Austin, Fremont, Shanghai, Berlin, and Nevada, with 55,000+ Supercharger stalls globally.",
            "website": "https://www.tesla.com",
            "market_cap_tier": "Large Cap ($750+ Billion USD)",
            "pe_ratio": "62.0",
            "annual_revenue": "$97 Billion USD",
            "net_profit": "$14 Billion USD",
            "employees": "140,000+ Workers",
            "competitive_advantages": "World-class EV manufacturing cost structure (Unboxed process), largest real-world video dataset for autonomous AI training, global Supercharger standard (NACS).",
            "data_json": {"fsd_miles_driven": "2+ Billion Miles"}
        }
    ]

    for comp in default_companies:
        try:
            upsert_company(comp)
        except Exception as e:
            print(f"Error seeding company {comp.get('ticker')}: {e}")

    # Also add short-ticker alias for Lalitha Jewellery so search for LALITHA or LALITHA.NS works identically
    try:
        lalitha_alias = dict(default_companies[0])
        lalitha_alias["ticker"] = "LALITHA"
        upsert_company(lalitha_alias)
    except Exception:
        pass


# Automatically initialize on module import
init_db()

