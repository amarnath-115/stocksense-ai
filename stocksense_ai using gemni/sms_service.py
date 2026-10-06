"""
Stocksense AI - SMS OTP Service
Multi-provider dispatcher for Real Mobile OTP delivery:
- Fast2SMS (India Quick SMS / OTP route)
- Twilio (Global SMS API)
- 2Factor.in (India Transactional OTP)
- Resilient Demo Fallback (when API keys are not yet configured)
"""

import os
import re
import logging
import requests

logger = logging.getLogger("stocksense.sms")

# Try loading .env if manually present
def _load_env_file():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k not in os.environ:
                            os.environ[k] = v
        except Exception as e:
            logger.warning(f"Error parsing .env file: {e}")

_load_env_file()


def normalize_phone_number(mobile: str) -> str:
    """Strips all non-digit characters except leading +."""
    mobile = str(mobile).strip()
    if mobile.startswith("+"):
        digits = re.sub(r"[^\d]", "", mobile[1:])
        return f"+{digits}"
    digits = re.sub(r"[^\d]", "", mobile)
    return digits


def get_configured_provider() -> str:
    """Detects which SMS provider has valid credentials configured."""
    _load_env_file()
    if os.environ.get("FAST2SMS_API_KEY"):
        return "fast2sms"
    if os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN"):
        return "twilio"
    if os.environ.get("TWOFACTOR_API_KEY"):
        return "2factor"
    return "demo"


def send_otp_sms(mobile: str, otp: str) -> dict:
    """
    Sends OTP via real SMS gateway or returns demo fallback.
    Returns:
        dict: {
            "sent": bool,
            "provider": str,
            "demo_otp": str or None,
            "message": str
        }
    """
    cleaned_mobile = normalize_phone_number(mobile)
    provider = get_configured_provider()

    # 1. Fast2SMS (Popular in India)
    if provider == "fast2sms":
        api_key = os.environ.get("FAST2SMS_API_KEY", "").strip()
        target_number = cleaned_mobile[-10:] if len(cleaned_mobile) >= 10 else cleaned_mobile
        try:
            url = "https://www.fast2sms.com/dev/bulkV2"
            payload = {
                "route": "otp",
                "variables_values": str(otp),
                "numbers": target_number
            }
            headers = {
                "authorization": api_key,
                "Content-Type": "application/json"
            }
            resp = requests.post(url, json=payload, headers=headers, timeout=8)
            resp_data = resp.json()
            if resp.status_code == 200 and resp_data.get("return") is True:
                return {
                    "sent": True,
                    "provider": "Fast2SMS",
                    "demo_otp": None,
                    "message": f"Real SMS OTP dispatched to +91 {target_number} via Fast2SMS."
                }
            else:
                err_msg = resp_data.get("message", ["Unknown Fast2SMS error"])[0] if isinstance(resp_data.get("message"), list) else str(resp_data.get("message", ""))
                logger.warning(f"Fast2SMS error: {err_msg}. Falling back to demo mode.")
                return {
                    "sent": False,
                    "provider": "demo",
                    "demo_otp": otp,
                    "message": f"Fast2SMS note: {err_msg}. For testing, use OTP: {otp}"
                }
        except Exception as e:
            logger.error(f"Fast2SMS request failed: {e}")
            return {
                "sent": False,
                "provider": "demo",
                "demo_otp": otp,
                "message": f"Fast2SMS connection error ({e}). Fallback OTP: {otp}"
            }

    # 2. Twilio (Global)
    elif provider == "twilio":
        account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
        from_number = os.environ.get("TWILIO_PHONE_NUMBER", "").strip()
        target_number = cleaned_mobile if cleaned_mobile.startswith("+") else f"+91{cleaned_mobile[-10:]}"
        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
            data = {
                "From": from_number,
                "To": target_number,
                "Body": f"Your Stocksense AI security verification code is {otp}. Valid for 5 minutes. Do not share this code."
            }
            resp = requests.post(url, data=data, auth=(account_sid, auth_token), timeout=8)
            if resp.status_code in (200, 201):
                return {
                    "sent": True,
                    "provider": "Twilio",
                    "demo_otp": None,
                    "message": f"Real SMS OTP dispatched to {target_number} via Twilio."
                }
            else:
                resp_json = resp.json()
                err_msg = resp_json.get("message", "Twilio delivery failure")
                logger.warning(f"Twilio error: {err_msg}")
                return {
                    "sent": False,
                    "provider": "demo",
                    "demo_otp": otp,
                    "message": f"Twilio note: {err_msg}. Fallback OTP: {otp}"
                }
        except Exception as e:
            logger.error(f"Twilio request failed: {e}")
            return {
                "sent": False,
                "provider": "demo",
                "demo_otp": otp,
                "message": f"Twilio connection error ({e}). Fallback OTP: {otp}"
            }

    # 3. 2Factor.in (India)
    elif provider == "2factor":
        api_key = os.environ.get("TWOFACTOR_API_KEY", "").strip()
        target_number = cleaned_mobile[-10:]
        try:
            url = f"https://2factor.in/API/V1/{api_key}/SMS/{target_number}/{otp}/OTP1"
            resp = requests.get(url, timeout=8)
            resp_data = resp.json()
            if resp.status_code == 200 and resp_data.get("Status") == "Success":
                return {
                    "sent": True,
                    "provider": "2Factor",
                    "demo_otp": None,
                    "message": f"Real SMS OTP dispatched to +91 {target_number} via 2Factor."
                }
            else:
                details = resp_data.get("Details", "2Factor error")
                return {
                    "sent": False,
                    "provider": "demo",
                    "demo_otp": otp,
                    "message": f"2Factor: {details}. Fallback OTP: {otp}"
                }
        except Exception as e:
            return {
                "sent": False,
                "provider": "demo",
                "demo_otp": otp,
                "message": f"2Factor connection error ({e}). Fallback OTP: {otp}"
            }

    # 4. Default Demo Fallback Mode
    return {
        "sent": False,
        "provider": "demo",
        "demo_otp": otp,
        "message": f"Demo Mode (no SMS Gateway API key configured in .env): your verification OTP is {otp}"
    }
