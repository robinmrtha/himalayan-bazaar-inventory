diff --git a/app.py b/app.py
new file mode 100644
index 0000000000000000000000000000000000000000..ef51a205b43933c734ae06b6dcdc93849b7f14d6
--- /dev/null
+++ b/app.py
@@ -0,0 +1,173 @@
+import os
+import subprocess
+import sys
+import threading
+from datetime import datetime
+
+from apscheduler.schedulers.background import BackgroundScheduler
+from apscheduler.triggers.cron import CronTrigger
+from dotenv import load_dotenv
+from flask import Flask, jsonify, render_template, request
+from supabase import create_client
+
+load_dotenv()
+app = Flask(__name__)
+
+SUPABASE_URL = os.getenv("SUPABASE_URL")
+SUPABASE_KEY = os.getenv("SUPABASE_KEY")
+supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
+
+
+def safe_int(val):
+    if isinstance(val, dict):
+        return 0
+    try:
+        return int(val or 0)
+    except (TypeError, ValueError):
+        return 0
+
+
+@app.route("/")
+def index():
+    return render_template("index.html")
+
+
+@app.route("/api/inventory")
+def get_inventory():
+    market = request.args.get("market", "")
+    search = request.args.get("search", "")
+
+    try:
+        q = supabase.table("inventory").select("*")
+        if market:
+            q = q.eq("marketplace", market)
+        if search:
+            q = q.ilike("seller_sku", f"%{search}%")
+
+        res = q.order("seller_sku").limit(5000).execute()
+        rows = res.data or []
+
+        total_skus = 0
+        total_fulfillable = 0
+        total_inbound = 0
+        total_unfulfillable = 0
+
+        for row in rows:
+            if row.get("is_narf"):
+                continue
+            total_skus += 1
+            total_fulfillable += safe_int(row.get("fulfillable"))
+            total_inbound += (
+                safe_int(row.get("inbound_working", 0))
+                + safe_int(row.get("inbound_shipped", 0))
+                + safe_int(row.get("inbound_receiving", 0))
+            )
+            total_unfulfillable += safe_int(row.get("unfulfillable"))
+
+        return jsonify(
+            {
+                "rows": rows,
+                "stats": {
+                    "total_skus": total_skus,
+                    "total_fulfillable": total_fulfillable,
+                    "total_inbound": total_inbound,
+                    "total_unfulfillable": total_unfulfillable,
+                },
+            }
+        )
+    except Exception as e:
+        return jsonify({"error": str(e)}), 500
+
+
+@app.route("/api/views", methods=["GET"])
+def get_views():
+    try:
+        res = supabase.table("saved_views").select("*").order("name").execute()
+        return jsonify(res.data or [])
+    except Exception as e:
+        return jsonify({"error": str(e)}), 500
+
+
+@app.route("/api/views", methods=["POST"])
+def save_view():
+    try:
+        body = request.get_json() or {}
+        name = body.get("name", "").strip()
+        filters = body.get("filters", {})
+        if not name:
+            return jsonify({"error": "Name required"}), 400
+
+        supabase.table("saved_views").upsert(
+            {"name": name, "filters": filters}, on_conflict="name"
+        ).execute()
+        return jsonify({"ok": True})
+    except Exception as e:
+        return jsonify({"error": str(e)}), 500
+
+
+@app.route("/api/views/<name>", methods=["DELETE"])
+def delete_view(name):
+    try:
+        supabase.table("saved_views").delete().eq("name", name).execute()
+        return jsonify({"ok": True})
+    except Exception as e:
+        return jsonify({"error": str(e)}), 500
+
+
+# ---- Sync state ----
+_sync_status = {
+    "running": False,
+    "last_started": None,
+    "last_finished": None,
+    "last_error": None,
+}
+
+
+def run_sync():
+    global _sync_status
+    if _sync_status["running"]:
+        return
+
+    _sync_status["running"] = True
+    _sync_status["last_started"] = datetime.utcnow().isoformat() + "Z"
+    _sync_status["last_error"] = None
+
+    try:
+        subprocess.run([sys.executable, "inventory.py"], check=True, timeout=600)
+        subprocess.run([sys.executable, "sales.py"], check=True, timeout=1200)
+        _sync_status["last_finished"] = datetime.utcnow().isoformat() + "Z"
+    except Exception as e:
+        _sync_status["last_error"] = str(e)
+    finally:
+        _sync_status["running"] = False
+
+
+@app.route("/api/sync-status")
+def sync_status():
+    return jsonify(_sync_status)
+
+
+@app.route("/api/sync-now", methods=["POST"])
+def sync_now():
+    if _sync_status["running"]:
+        return jsonify({"ok": False, "msg": "Sync already running"}), 409
+
+    threading.Thread(target=run_sync, daemon=True).start()
+    return jsonify({"ok": True, "msg": "Sync started"})
+
+
+# ---- Daily auto-sync scheduler ----
+# Runs every day at 07:00 UTC (adjust hour as needed)
+scheduler = BackgroundScheduler()
+scheduler.add_job(
+    func=lambda: threading.Thread(target=run_sync, daemon=True).start(),
+    trigger=CronTrigger(hour=7, minute=0),
+    id="daily_sync",
+    name="Daily inventory sync at 07:00 UTC",
+    replace_existing=True,
+)
+scheduler.start()
+
+
+if __name__ == "__main__":
+    app.run(debug=True, port=8080)
