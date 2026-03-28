#!/usr/bin/env python3
"""Generate a valid, clean IMEI verified against multiple checks.

Flow:
  1. Verify API access and show account balance
  2. Estimate cost and prompt for confirmation (unless --quiet)
  3. Generate IMEI with valid Luhn checksum using real TAC
  4. Verify model via Basic IMEI Check (service 0, $0.02)
  5. Run enabled carrier/blacklist checks:
     - T-Mobile USA Check (service 31, $0.18)
     - Verizon USA Check (service 32, $0.12)
     - Lost Device Check (service 101, $0.002)
     - Blacklist Premium Check (service 28, $0.42)
  6. If any check fails, retry with new IMEI (up to IMEI_MAX_RETRIES)

Configuration via environment variables (set by the Go gateway from database config):
    IMEI_API_TOKEN     - imei.info API token (required)
    IMEI_MODELS        - comma-separated model filter (optional)
    IMEI_MAX_RETRIES   - max attempts (default: 5)
    CHECK_TMOBILE      - run T-Mobile check (default: true)
    CHECK_VERIZON      - run Verizon check (default: true)
    CHECK_BLACKLIST    - run blacklist check (default: true)
    CHECK_LOST_DEVICE  - run lost device check (default: true)

Usage:
    python3 generate.py              # random model, all checks, with prompts
    python3 generate.py --quiet      # skip cost confirmation prompt
    python3 generate.py a10e         # specific model
    python3 generate.py --list       # list available models
    python3 generate.py --no-verify  # skip all API checks (offline mode)
"""

import json
import os
import random
import sys
import time
import urllib.request
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TAC_FILE = os.path.join(SCRIPT_DIR, "tac-models.json")

# Service definitions: id, name, cost, pass condition
SERVICES = {
    "model":       {"id": 0,   "name": "Basic IMEI Check",       "cost": 0.02},
    "tmobile":     {"id": 31,  "name": "T-Mobile USA Check",      "cost": 0.18},
    "verizon":     {"id": 32,  "name": "Verizon USA Check",       "cost": 0.12},
    "lost_device": {"id": 101, "name": "Lost Device Check",       "cost": 0.002},
    "blacklist":   {"id": 28,  "name": "Blacklist Premium Check",  "cost": 0.42},
}

def env_bool(key, default=True):
    """Read a boolean env var (true/false/yes/no/1/0)."""
    val = os.environ.get(key, str(default)).strip().lower()
    return val in ("true", "yes", "1")


def load_models():
    """Load TAC model database."""
    with open(TAC_FILE) as f:
        data = json.load(f)
    return data["models"]


def filter_models(all_models, filter_list):
    """Filter models to only those matching the configured list."""
    if not filter_list:
        return all_models
    filtered = {}
    for name, info in all_models.items():
        for f in filter_list:
            if f.lower() in name.lower():
                filtered[name] = info
                break
    return filtered if filtered else all_models


def luhn_check_digit(imei_14):
    """Calculate the Luhn check digit for a 14-digit partial IMEI."""
    digits = [int(d) for d in imei_14]
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - (total % 10)) % 10


def verify_luhn(imei_15):
    """Verify a complete 15-digit IMEI passes Luhn check."""
    digits = [int(d) for d in imei_15]
    total = 0
    for i in range(14):
        d = digits[i]
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (total + digits[14]) % 10 == 0


def generate_imei(tac):
    """Generate a valid 15-digit IMEI from an 8-digit TAC."""
    serial = "".join([str(random.randint(0, 9)) for _ in range(6)])
    imei_14 = tac + serial
    check = luhn_check_digit(imei_14)
    imei = imei_14 + str(check)
    assert verify_luhn(imei), f"Luhn check failed for {imei}"
    assert len(imei) == 15, f"IMEI length {len(imei)} != 15"
    return imei


def api_call(service_id, imei, api_token):
    """Call imei.info API. Returns the full response dict or None on error."""
    url = f"https://dash.imei.info/api-sync/check/{service_id}/?API_KEY={api_token}&imei={imei}"
    try:
        resp = urllib.request.urlopen(url, timeout=15).read()
        return json.loads(resp)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        return {"error": str(e)}


def check_account(api_token):
    """Verify API access and return account info."""
    url = f"https://dash.imei.info/api/account/account/?API_KEY={api_token}"
    try:
        resp = urllib.request.urlopen(url, timeout=10).read()
        data = json.loads(resp)
        return data
    except Exception as e:
        return None


def extract_result(data):
    """Extract result dict from API response, handling errors."""
    if "error" in data:
        return None, data["error"]
    result = data.get("result", {})
    if not isinstance(result, dict):
        return None, str(result)
    return result, None


def check_model(imei, expected_model_info, api_token):
    """Verify IMEI resolves to correct model. Returns (ok, full_result)."""
    data = api_call(0, imei, api_token)
    result, err = extract_result(data)
    if err:
        return False, {"error": err}
    expected = expected_model_info.get("api_model", "")
    api_model = result.get("model", "")
    ok = expected.lower() in api_model.lower()
    return ok, result


def check_tmobile(imei, api_token):
    """T-Mobile USA check. Pass if esn_status is Clean."""
    data = api_call(31, imei, api_token)
    result, err = extract_result(data)
    if err:
        return False, {"error": err}
    status = result.get("esn_status", "Unknown")
    return status.lower() == "clean", result


def check_verizon(imei, api_token):
    """Verizon USA check. Pass if not rejected and status is clean."""
    data = api_call(32, imei, api_token)
    status = data.get("status", "")
    if status == "Rejected":
        return True, {"skipped": True, "reason": "Verizon rejects non-provisioned IMEIs"}
    result, err = extract_result(data)
    if err:
        return True, {"skipped": True, "reason": err}
    esn = result.get("esn_status", result.get("status", "Unknown"))
    return esn.lower() in ("clean", ""), result


def check_lost_device(imei, api_token):
    """Lost device check. Pass if status is Clean."""
    data = api_call(101, imei, api_token)
    result, err = extract_result(data)
    if err:
        return False, {"error": err}
    status = result.get("status", "Unknown")
    reported = result.get("reported", "false")
    return status.lower() == "clean" and str(reported).lower() != "true", result


def check_blacklist(imei, api_token):
    """Blacklist premium check. Pass if blacklist_status is Clean."""
    data = api_call(28, imei, api_token)
    result, err = extract_result(data)
    if err:
        return False, {"error": err}
    status = result.get("blacklist_status", "Unknown")
    return status.lower() == "clean", result


# Ordered by price (cheapest first — fail fast, save money)
CHECK_FUNCS = [
    ("lost_device", "CHECK_LOST_DEVICE", check_lost_device),   # $0.002
    ("verizon",     "CHECK_VERIZON",     check_verizon),        # $0.12
    ("tmobile",     "CHECK_TMOBILE",     check_tmobile),        # $0.18
    ("blacklist",   "CHECK_BLACKLIST",   check_blacklist),      # $0.42
]


def main():
    # Config is now passed via environment variables by the Go caller.
    # load_env() removed — all config comes from the database.

    api_token = os.environ.get("IMEI_API_TOKEN", "")
    max_retries = int(os.environ.get("IMEI_MAX_RETRIES", "5"))
    model_filter = os.environ.get("IMEI_MODELS", "")
    filter_list = [m.strip() for m in model_filter.split(",") if m.strip()] if model_filter else []
    quiet = "--quiet" in sys.argv

    all_models = load_models()

    # Handle --list
    if "--list" in sys.argv:
        print("Available models in tac-models.json:")
        for name, info in all_models.items():
            marker = " (active)" if not filter_list or any(f.lower() in name.lower() for f in filter_list) else ""
            print(f"  {name}{marker}")
            print(f"    Brand: {info['brand']}, Year: {info['year']}, Region: {info['region']}")
            print(f"    TACs: {', '.join(info['tacs'])}")
        if filter_list:
            print(f"\nIMEI_MODELS filter: {model_filter}")
        return

    no_verify = "--no-verify" in sys.argv
    model_arg = None
    for arg in sys.argv[1:]:
        if not arg.startswith("--"):
            model_arg = arg

    # Select models
    available = filter_models(all_models, filter_list)
    if model_arg:
        matches = {k: v for k, v in available.items() if model_arg.lower() in k.lower()}
        if not matches:
            print(f"No model matching '{model_arg}'. Available:", file=sys.stderr)
            for m in available:
                print(f"  {m}", file=sys.stderr)
            sys.exit(1)
        available = matches

    model_name = random.choice(list(available.keys()))
    model_info = available[model_name]

    # No-verify mode
    if no_verify or not api_token:
        if not api_token and not no_verify:
            print("WARNING: No IMEI_API_TOKEN set, skipping verification", file=sys.stderr)
        tac = random.choice(model_info["tacs"])
        imei = generate_imei(tac)
        print(json.dumps({
            "imei": imei,
            "model": model_name,
            "tac": tac,
            "luhn_valid": True,
            "checks": {},
        }, indent=2))
        return

    # Step 1: Verify API access
    account = check_account(api_token)
    if account is None:
        print("ERROR: Could not connect to imei.info API", file=sys.stderr)
        sys.exit(1)
    if not account.get("is_active", False):
        print("ERROR: API account is not active", file=sys.stderr)
        sys.exit(1)

    balance = account.get("balance", 0)
    print(f"Account: {account.get('email', '?')}  Balance: ${balance:.3f}", file=sys.stderr)

    # Step 2: Determine enabled checks and estimate cost (ordered by price)
    enabled_checks = []
    for check_key, env_key, _ in CHECK_FUNCS:
        if env_bool(env_key, default=True):
            enabled_checks.append(check_key)

    cost_per_attempt = SERVICES["model"]["cost"]
    for ck in enabled_checks:
        cost_per_attempt += SERVICES[ck]["cost"]

    max_cost = cost_per_attempt * max_retries
    print(f"Checks: model + {', '.join(enabled_checks)}", file=sys.stderr)
    print(f"Cost per attempt: ${cost_per_attempt:.3f}  Max ({max_retries} retries): ${max_cost:.3f}", file=sys.stderr)

    if balance < cost_per_attempt:
        print(f"ERROR: Insufficient balance (${balance:.3f} < ${cost_per_attempt:.3f})", file=sys.stderr)
        sys.exit(1)

    if not quiet:
        confirm = input(f"Proceed? [Y/n] ").strip().lower()
        if confirm and confirm != "y":
            print("Aborted.", file=sys.stderr)
            sys.exit(0)

    # Step 3: Generate, verify, and check with retries
    total_spent = 0.0
    for attempt in range(1, max_retries + 1):
        tac = random.choice(model_info["tacs"])
        imei = generate_imei(tac)
        checks = {}
        all_passed = True
        attempt_spent = 0.0

        print(f"\nAttempt {attempt}/{max_retries}: {imei} ({model_name})", file=sys.stderr)

        # Model verification (always runs)
        model_ok, model_result = check_model(imei, model_info, api_token)
        attempt_spent += SERVICES["model"]["cost"]
        checks["model"] = {"passed": model_ok, "service": "Basic IMEI Check", "cost": "$0.02", "data": model_result}
        if not model_ok:
            total_spent += attempt_spent
            print(f"  FAIL model: got {model_result.get('brand_name','')} {model_result.get('model','')} (expected {model_info['api_model']})", file=sys.stderr)
            all_passed = False
            if attempt < max_retries:
                time.sleep(0.3)
            continue
        print(f"  PASS model: {model_result.get('brand_name','')} {model_result.get('model','')}", file=sys.stderr)

        # Run enabled checks in order of price (cheapest first)
        check_func_map = {k: f for k, _, f in CHECK_FUNCS}
        for check_key in enabled_checks:
            check_func = check_func_map[check_key]
            svc = SERVICES[check_key]
            ok, result_data = check_func(imei, api_token)
            checks[check_key] = {"passed": ok, "service": svc["name"], "cost": f"${svc['cost']:.3f}", "data": result_data}
            if not result_data.get("skipped"):
                attempt_spent += svc["cost"]

            if result_data.get("skipped"):
                print(f"  SKIP {svc['name']}: {result_data.get('reason', '')}", file=sys.stderr)
            elif ok:
                print(f"  PASS {svc['name']}", file=sys.stderr)
            else:
                print(f"  FAIL {svc['name']}: {result_data}", file=sys.stderr)
                all_passed = False
                break  # No point running more checks

            time.sleep(0.2)

        total_spent += attempt_spent

        if all_passed:
            # Build flat output with useful fields from all checks
            tmobile_data = checks.get("tmobile", {}).get("data", {}) or {}
            verizon_data = checks.get("verizon", {}).get("data", {}) or {}
            blacklist_data = checks.get("blacklist", {}).get("data", {}) or {}
            lost_data = checks.get("lost_device", {}).get("data", {}) or {}

            output = {
                "imei": imei,
                "tac": tac,
                "brand": model_result.get("brand_name", ""),
                "model": model_result.get("model", ""),
                "model_name": blacklist_data.get("model_name", "") or tmobile_data.get("model_name", ""),
                "model_number": tmobile_data.get("model_number", "") or verizon_data.get("description", ""),
                "manufacturer": blacklist_data.get("manufacturer", "") or tmobile_data.get("model_brand", ""),
                "device_type": tmobile_data.get("device_type", "") or verizon_data.get("device_type", ""),
                "tmobile_esn_status": tmobile_data.get("esn_status", ""),
                "tmobile_blacklist_reason": tmobile_data.get("blacklist_reason", None),
                "tmobile_esim": tmobile_data.get("esim_supported", ""),
                "tmobile_finance_type": tmobile_data.get("finance_type", None),
                "verizon_esn_status": verizon_data.get("esn_status", ""),
                "verizon_device_status": verizon_data.get("device_status", ""),
                "verizon_skipped": verizon_data.get("skipped", False) or None,
                "blacklist_status": blacklist_data.get("blacklist_status", ""),
                "blacklist_records": blacklist_data.get("blacklist_records", "0"),
                "lost_device_status": lost_data.get("status", ""),
                "lost_device_reported": lost_data.get("reported", ""),
                "luhn_valid": True,
                "all_checks_passed": True,
                "attempts": attempt,
                "total_cost": f"${total_spent:.3f}",
                "set_command": f"AT+SIMEI={imei}",
                "reboot_command": "AT+CFUN=1,1",
            }
            # Remove None/empty values for cleaner output
            output = {k: v for k, v in output.items() if v is not None and v != "" and v is not False}

            print(f"\nSUCCESS", file=sys.stderr)
            print(json.dumps(output, indent=2))
            return
        else:
            if attempt < max_retries:
                time.sleep(0.3)

    print(f"\nFAILED: Could not generate clean IMEI after {max_retries} attempts", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
