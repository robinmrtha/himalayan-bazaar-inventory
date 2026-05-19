import os, time, requests, csv, io, gzip
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

LWA_CLIENT_ID     = os.getenv("LWA_CLIENT_ID")
LWA_CLIENT_SECRET = os.getenv("LWA_CLIENT_SECRET")
REFRESH_TOKEN     = os.getenv("REFRESH_TOKEN")
SUPABASE_URL      = os.getenv("SUPABASE_URL")
SUPABASE_KEY      = os.getenv("SUPABASE_KEY")

MARKETPLACES = {
        "US": "ATVPDKIKX0DER",
        "CA": "A2EUQ1WTGCTBG2",
        "MX": "A1AM78C64UM0Y8",
}

def get_access_token():
        resp = requests.post("https://api.amazon.com/auth/o2/token", data={
                    "grant_type":    "refresh_token",
                    "refresh_token": REFRESH_TOKEN,
                    "client_id":     LWA_CLIENT_ID,
                    "client_secret": LWA_CLIENT_SECRET,
        })
        resp.raise_for_status()
        return resp.json()["access_token"]

def safe_int(val):
        if val is None: return 0
                if isinstance(val, int): return val
                        if isinstance(val, dict):
                                    for k in ("quantity", "totalUnfulfillableQuantity", "fulfillableQuantity"):
                                                    if k in val: return int(val[k] or 0)
                                                                return 0
                                            try: return int(val)
                                                    except: return 0

def get_inventory_once(token, marketplace_id):
        """Fetch FBA inventory summaries for a specific marketplace."""
    now = datetime.now(timezone.utc).isoformat()
    skus = {}
    base_url = "https://sellingpartnerapi-na.amazon.com/fba/inventory/v1/summaries"
    headers = {"x-amz-access-token": token}
    first_params = {
                "details":         "true",
                "granularityType": "Marketplace",
                "granularityId":   marketplace_id,
                "marketplaceIds":  marketplace_id,
    }
    page = 0
    next_token = None
    while True:
                page += 1
        if next_token:
                        params = dict(first_params)
            params["nextToken"] = next_token
else:
            params = first_params
        r = requests.get(base_url, headers=headers, params=params)
        if r.status_code == 400 and next_token:
                        print("  Pagination stopped at page " + str(page))
            break
        r.raise_for_status()
        data = r.json()
        payload = data.get("payload") or {}
        items = payload.get("inventorySummaries") or []
        print("  Page " + str(page) + ": " + str(len(items)) + " items")
        for item in items:
                        sku = item.get("sellerSku", "")
            if not sku: continue
                            inv = item.get("inventoryDetails") or {}
            skus[sku] = {
                                "asin":               item.get("asin", ""),
                                "fnsku":              item.get("fnSku", ""),
                                "seller_sku":         sku,
                                "product_name":       item.get("productName", ""),
                                "condition":          item.get("condition", ""),
                                "fulfillable":        safe_int(inv.get("fulfillableQuantity")),
                                "inbound_working":    safe_int(inv.get("inboundWorkingQuantity")),
                                "inbound_shipped":    safe_int(inv.get("inboundShippedQuantity")),
                                "inbound_receiving":  safe_int(inv.get("inboundReceivingQuantity")),
                                "reserved_fc":        safe_int((inv.get("reservedQuantity") or {}).get("fcProcessingQuantity")),
                                "reserved_customer":  safe_int((inv.get("reservedQuantity") or {}).get("pendingCustomerOrderQuantity")),
                                "unfulfillable":      safe_int(inv.get("unfulfillableQuantity")),
                                "total_quantity":     safe_int(item.get("totalQuantity")),
                                "last_updated_amazon": item.get("lastUpdatedTime") or None,
                                "synced_at":          now,
            }
        next_token = (data.get("pagination") or {}).get("nextToken")
        if not next_token: break
                    time.sleep(0.5)
    return skus

def request_report(token, report_type, marketplace_id):
        resp = requests.post(
                    "https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports",
                    headers={"x-amz-access-token": token, "Content-Type": "application/json"},
                    json={"reportType": report_type, "marketplaceIds": [marketplace_id]},
        )
    resp.raise_for_status()
    return resp.json()["reportId"]

def wait_for_report(token, report_id):
        for i in range(40):
                    data = requests.get(
                                    "https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports/" + report_id,
                                    headers={"x-amz-access-token": token},
                    ).json()
        status = data.get("processingStatus")
        print("  status: " + str(status))
        if status == "DONE": return data["reportDocumentId"]
elif status in ("FATAL", "CANCELLED"): return None
        time.sleep(15)
    return None

def download_report(token, doc_id):
        meta = requests.get(
                    "https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/documents/" + doc_id,
                    headers={"x-amz-access-token": token},
        ).json()
    r = requests.get(meta["url"])
    try:
                return gzip.decompress(r.content).decode("utf-8", errors="replace")
    except:
        return r.text

def parse_listings(tsv):
        tsv = tsv.replace(chr(0), "").replace(chr(13)+chr(10), chr(10)).replace(chr(13), chr(10))
    reader = csv.DictReader(io.StringIO(tsv), delimiter=chr(9))
    listings = {}
    for row in reader:
                sku = row.get("seller-sku", "") or row.get("SellerSKU", "")
        if not sku: continue
                    try: price = float(row.get("price", 0) or row.get("Price", 0) or 0)
                                except: price = 0
        listings[sku] = {
                        "listing_status": row.get("status", "") or row.get("Status", ""),
                        "price": price,
        }
    return listings

def get_awd_inventory(token):
        awd = {}
    url = "https://sellingpartnerapi-na.amazon.com/awd/2024-05-09/inventory"
    headers = {"x-amz-access-token": token}
    params = {"details": "SHOW", "marketplaceId": "ATVPDKIKX0DER", "maxResults": 100}
    while True:
                r = requests.get(url, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()
        for item in data.get("inventory", []):
                        sku = item.get("sku", "")
            if not sku: continue
                            det = item.get("inventoryDetails") or {}
            awd[sku] = {
                                "awd_quantity": int(det.get("availableDistributableQuantity") or 0),
                                "awd_inbound":  int(item.get("totalInboundQuantity") or 0),
                                "awd_reserved": int(det.get("reservedDistributableQuantity") or 0),
            }
        next_token = data.get("nextToken")
        if not next_token: break
                    params = {"nextToken": next_token, "details": "SHOW", "marketplaceId": "ATVPDKIKX0DER"}
        time.sleep(0.5)
    return awd

def update_narf_flags():
        """
            Mark a CA/MX row as NARF only when the SKU has no active own listing in
                that marketplace (listing_status is blank or 'Inactive') AND it has
                    fulfillable stock — meaning it is selling via NARF from the US pool.
                        A SKU with an Active listing in CA/MX is NOT NARF even if quantities match,
                            because it has its own marketplace presence.
                                """
    hdrs = {
                "apikey":        SUPABASE_KEY,
                "Authorization": "Bearer " + SUPABASE_KEY,
                "Content-Type":  "application/json",
    }
    res = requests.get(
                SUPABASE_URL + "/rest/v1/inventory?select=seller_sku,marketplace,fulfillable,listing_status",
                headers=hdrs,
    )
    all_rows = res.json()

    # Build a set of SKUs that have an active listing in the US
    us_active = {
                r["seller_sku"]
                for r in all_rows
                if r["marketplace"] == "US" and (r.get("listing_status") or "").lower() == "active"
    }

    updated = 0
    for r in all_rows:
                if r["marketplace"] not in ("CA", "MX"):
                                continue
        sku = r["seller_sku"]
        listing = (r.get("listing_status") or "").lower()
        fulfillable = r.get("fulfillable") or 0

        # NARF = the SKU exists in US as active, has stock, but has no active
        # own listing in this CA/MX marketplace
        is_narf = (
                        sku in us_active
                        and fulfillable > 0
                        and listing != "active"
        )
        requests.patch(
                        SUPABASE_URL + "/rest/v1/inventory?seller_sku=eq."
                        + requests.utils.quote(sku)
                        + "&marketplace=eq." + r["marketplace"],
                        headers=hdrs,
                        json={"is_narf": is_narf},
        )
        updated += 1
    print("  NARF flags updated for " + str(updated) + " rows")

def save_to_supabase(rows):
        hdrs = {
                    "apikey":        SUPABASE_KEY,
                    "Authorization": "Bearer " + SUPABASE_KEY,
                    "Content-Type":  "application/json",
                    "Prefer":        "resolution=merge-duplicates",
        }
    resp = requests.post(
                SUPABASE_URL + "/rest/v1/inventory?on_conflict=seller_sku,marketplace",
                headers=hdrs,
                json=rows,
    )
    if resp.status_code in (200, 201, 204):
                print("  Saved " + str(len(rows)) + " rows")
else:
        print("  Save error " + str(resp.status_code) + ": " + resp.text[:200])

if __name__ == "__main__":
        print("Getting access token...")
    token = get_access_token()

    print("Fetching AWD inventory...")
    try:
                awd_data = get_awd_inventory(token)
        print("  AWD SKUs found: " + str(len(awd_data)))
except Exception as e:
        print("  AWD error: " + str(e))
        awd_data = {}

    all_rows = []
    for country, marketplace_id in MARKETPLACES.items():
                print("[" + country + "] Fetching FBA inventory for marketplace " + marketplace_id + "...")
        try:
                        market_skus = get_inventory_once(token, marketplace_id)
            print("  Total SKUs in FBA for " + country + ": " + str(len(market_skus)))
except Exception as e:
            print("  FBA inventory error for " + country + ": " + str(e))
            market_skus = {}

        print("[" + country + "] Requesting listings report...")
        try:
                        rid = request_report(token, "GET_MERCHANT_LISTINGS_ALL_DATA", marketplace_id)
            doc_id = wait_for_report(token, rid)
            listings = {}
            if doc_id:
                                listings = parse_listings(download_report(token, doc_id))
                print("  Listings: " + str(len(listings)) + " SKUs with status/price")
else:
                print("  Listings report failed")
except Exception as e:
            print("  Listings error: " + str(e))
            listings = {}

        for sku, inv in market_skus.items():
                        row = dict(inv)
            row["marketplace"] = country
            if sku in listings:
                                row["listing_status"] = listings[sku]["listing_status"]
                row["price"] = listings[sku]["price"]
else:
                row["listing_status"] = ""
                row["price"] = 0
            if country == "US":
                                awd = awd_data.get(sku, {})
                row["awd_quantity"] = awd.get("awd_quantity", 0)
                row["awd_inbound"]  = awd.get("awd_inbound", 0)
                row["awd_reserved"] = awd.get("awd_reserved", 0)
else:
                row["awd_quantity"] = 0
                row["awd_inbound"]  = 0
                row["awd_reserved"] = 0
            all_rows.append(row)

        if country != "MX":
                        print("  Waiting 30s before next market...")
            time.sleep(30)

    print("Saving " + str(len(all_rows)) + " rows to Supabase...")
    save_to_supabase(all_rows)

    print("Updating NARF flags...")
    update_narf_flags()
    print("Done! Total rows: " + str(len(all_rows)))
