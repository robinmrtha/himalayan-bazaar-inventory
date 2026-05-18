import os, requests, json, gzip, time, sys, csv
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client

csv.field_size_limit(sys.maxsize)

load_dotenv()

LWA_CLIENT_ID     = os.getenv("LWA_CLIENT_ID")
LWA_CLIENT_SECRET = os.getenv("LWA_CLIENT_SECRET")
REFRESH_TOKEN     = os.getenv("REFRESH_TOKEN")
SUPABASE_URL      = os.getenv("SUPABASE_URL")
SUPABASE_KEY      = os.getenv("SUPABASE_KEY")

BASE_URL = "https://sellingpartnerapi-na.amazon.com"

MARKETS = {
    "US": "ATVPDKIKX0DER",
    "CA": "A2EUQ1WTGCTBG2",
    "MX": "A1AM78C64UM0Y8"
}

def get_token():
    r = requests.post("https://api.amazon.com/auth/o2/token", data={
        "grant_type":    "refresh_token",
        "refresh_token": REFRESH_TOKEN,
        "client_id":     LWA_CLIENT_ID,
        "client_secret": LWA_CLIENT_SECRET,
    })
    r.raise_for_status()
    return r.json()["access_token"]

def request_report(token, mid, start, end, retries=5):
    h = {"x-amz-access-token": token, "Content-Type": "application/json"}
    b = {
        "reportType": "GET_SALES_AND_TRAFFIC_REPORT",
        "marketplaceIds": [mid],
        "dataStartTime": start,
        "dataEndTime":   end,
        "reportOptions": {"dateGranularity": "TOTAL", "asinGranularity": "CHILD"}
    }
    for attempt in range(retries):
        r = requests.post(f"{BASE_URL}/reports/2021-06-30/reports", headers=h, json=b)
        print(f"    requested: {r.status_code}")
        if r.status_code == 202:
            return r.json().get("reportId")
        if r.status_code == 429:
            wait = 60 * (attempt + 1)
            print(f"    rate limited, waiting {wait}s...")
            time.sleep(wait)
        else:
            print(f"    error: {r.text[:200]}")
            return None
    return None

def wait_report(token, rid, max_tries=18):
    h = {"x-amz-access-token": token}
    for _ in range(max_tries):
        r = requests.get(f"{BASE_URL}/reports/2021-06-30/reports/{rid}", headers=h).json()
        s = r.get("processingStatus", "")
        print(f"    status: {s}")
        if s == "DONE":                return r.get("reportDocumentId")
        if s in ("FATAL","CANCELLED"): return None
        time.sleep(10)
    return None

def fetch_report(token, doc_id):
    h = {"x-amz-access-token": token}
    doc = requests.get(f"{BASE_URL}/reports/2021-06-30/documents/{doc_id}", headers=h).json()
    raw = requests.get(doc["url"])
    if doc.get("compressionAlgorithm") == "GZIP":
        return gzip.decompress(raw.content).decode("utf-8", errors="replace")
    return raw.text

def parse_units(raw):
    data = json.loads(raw)
    out = {}
    for item in data.get("salesAndTrafficByAsin", []):
        asin  = item.get("childAsin") or item.get("parentAsin", "")
        units = item.get("salesByAsin", {}).get("unitsOrdered", 0)
        if asin:
            out[asin] = out.get(asin, 0) + units
    return out

def reorder_label(vel, days):
    if vel  == 0:  return "no_sales"
    if days <= 0:  return "out_of_stock"
    if days <= 30: return "reorder_now"
    if days <= 60: return "order_soon"
    return "healthy"

def main():
    print("Getting access token...")
    token = get_token()
    sb    = create_client(SUPABASE_URL, SUPABASE_KEY)
    now   = datetime.now(timezone.utc)
    end   = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    s30   = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    s7    = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    for country, mid in MARKETS.items():
        print(f"\n=== {country} ===")

        print("  30-day report...")
        rid = request_report(token, mid, s30, end)
        did = wait_report(token, rid) if rid else None
        sales30 = parse_units(fetch_report(token, did)) if did else {}
        print(f"  {len(sales30)} ASINs with 30d sales")
        print("  Waiting 30s...")
        time.sleep(30)

        print("  7-day report...")
        rid = request_report(token, mid, s7, end)
        did = wait_report(token, rid) if rid else None
        sales7 = parse_units(fetch_report(token, did)) if did else {}
        print(f"  {len(sales7)} ASINs with 7d sales")

        rows = sb.table("inventory").select("id,asin,fulfillable").eq("marketplace", country).execute().data
        print(f"  Updating {len(rows)} rows...")
        saved = 0
        for row in rows:
            asin = row["asin"]
            s30v = sales30.get(asin, 0)
            s7v  = sales7.get(asin, 0)
            vel  = round(s30v / 30.0, 2)
            fba  = row["fulfillable"] or 0
            days = int(fba / vel) if vel > 0 else 9999
            sb.table("inventory").update({
                "units_sold_7d":  s7v,
                "units_sold_30d": s30v,
                "velocity":       vel,
                "days_of_stock":  min(days, 9999),
                "reorder_status": reorder_label(vel, days)
            }).eq("id", row["id"]).execute()
            saved += 1

        print(f"  Saved {saved} rows")

        if country != "MX":
            print("  Waiting 60s before next market...")
            time.sleep(90)

    print("\nAll done!")

main()
