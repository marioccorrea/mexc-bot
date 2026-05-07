import os, time, hmac, hashlib, json
import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))

def sign(secret, params):
    q = "&".join(f"{k}={v}" for k,v in sorted(params.items()))
    return hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()

def get_creds():
    key    = os.environ.get("MEXC_API_KEY","") or _sess.get("api_key","")
    secret = os.environ.get("MEXC_API_SECRET","") or _sess.get("api_secret","")
    return key, secret

_sess = {}

@app.route("/")
def index():
    html = open(os.path.join(BASE,"templates","index.html"), encoding="utf-8").read()
    return Response(html, content_type="text/html; charset=utf-8")

@app.route("/api/health")
def health():
    key, _ = get_creds()
    return jsonify({"ok": True, "has_key": bool(key)})

@app.route("/api/creds")
def creds():
    key, secret = get_creds()
    return jsonify({
        "has_key":    bool(key),
        "has_secret": bool(secret),
        "key_hint":   (key[:4]+"...."+key[-4:]) if key else "",
        "mode":       "live" if (key and secret) else "sim"
    })

@app.route("/api/config", methods=["POST"])
def set_config():
    data = request.get_json(silent=True) or {}
    _sess.update({k:v for k,v in data.items() if k in ["api_key","api_secret"]})
    return jsonify({"ok": True})

@app.route("/api/balance")
def balance():
    key, secret = get_creds()
    if not key or not secret:
        return jsonify({"ok":False,"error":"API Key nao configurada."})

    headers = {"X-MEXC-APIKEY": key}

    # 1) Futuros MEXC - endpoint correto
    try:
        ts = int(time.time()*1000)
        p  = {"timestamp": ts}
        p["signature"] = sign(secret, p)
        r = requests.get(
            "https://contract.mexc.com/api/v1/private/account/assets",
            params=p, headers=headers, timeout=10
        )
        d = r.json()
        print(f"[Futuros] status={r.status_code} code={d.get('code')} success={d.get('success')}")
        if r.status_code == 200 and (d.get("success") is True or d.get("code") == 0):
            assets = d.get("data") or []
            usdt = next((a for a in assets
                         if str(a.get("currency") or a.get("asset","")).upper() == "USDT"), None)
            if usdt:
                bal    = float(usdt.get("availableBalance") or usdt.get("availableMargin") or usdt.get("free") or 0)
                equity = float(usdt.get("equity") or usdt.get("walletBalance") or bal)
                print(f"[Futuros] USDT bal={bal} equity={equity}")
                return jsonify({"ok":True,"balance":bal,"equity":equity,"account":"Futuros"})
    except Exception as e:
        print(f"[Futuros] erro: {e}")

    # 2) Futuros - endpoint alternativo
    try:
        ts2 = int(time.time()*1000)
        p2  = {"timestamp": ts2}
        p2["signature"] = sign(secret, p2)
        r2 = requests.get(
            "https://contract.mexc.com/api/v1/private/account/asset/USDT",
            params=p2, headers=headers, timeout=10
        )
        d2 = r2.json()
        print(f"[Futuros2] status={r2.status_code} data={d2}")
        if r2.status_code == 200 and d2.get("success"):
            asset = d2.get("data") or {}
            bal = float(asset.get("availableBalance") or asset.get("availableMargin") or 0)
            equity = float(asset.get("equity") or bal)
            if bal > 0 or equity > 0:
                return jsonify({"ok":True,"balance":bal,"equity":equity,"account":"Futuros"})
    except Exception as e:
        print(f"[Futuros2] erro: {e}")

    # 3) Spot MEXC
    try:
        ts3 = int(time.time()*1000)
        p3  = {"timestamp": ts3}
        p3["signature"] = sign(secret, p3)
        r3 = requests.get(
            "https://api.mexc.com/api/v3/account",
            params=p3, headers=headers, timeout=10
        )
        d3 = r3.json()
        print(f"[Spot] status={r3.status_code}")
        if r3.status_code == 200:
            bals = d3.get("balances") or []
            usdt = next((b for b in bals if b.get("asset","").upper() == "USDT"), None)
            if usdt:
                free   = float(usdt.get("free",0))
                locked = float(usdt.get("locked",0))
                return jsonify({"ok":True,"balance":free,"equity":free+locked,"account":"Spot"})
        if r3.status_code in (401,403):
            msg = d3.get("msg") or d3.get("message") or ""
            return jsonify({"ok":False,"error":f"API Key invalida ({r3.status_code}): {msg}"})
        return jsonify({"ok":False,"error": d3.get("msg") or f"HTTP {r3.status_code}"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/debug")
def debug():
    key, secret = get_creds()
    if not key: return jsonify({"error":"Sem credenciais"})
    headers = {"X-MEXC-APIKEY": key}
    result = {"key_hint": key[:4]+"...."+key[-4:]}
    try:
        ts = int(time.time()*1000)
        p  = {"timestamp":ts}
        p["signature"] = sign(secret,p)
        r = requests.get("https://contract.mexc.com/api/v1/private/account/assets",
                         params=p, headers=headers, timeout=8)
        result["futures_status"] = r.status_code
        result["futures_raw"] = r.json()
    except Exception as e:
        result["futures_error"] = str(e)
    try:
        ts2 = int(time.time()*1000)
        p2  = {"timestamp":ts2}
        p2["signature"] = sign(secret,p2)
        r2 = requests.get("https://api.mexc.com/api/v3/account",
                          params=p2, headers=headers, timeout=8)
        d2 = r2.json()
        result["spot_status"] = r2.status_code
        result["spot_usdt"] = [b for b in (d2.get("balances") or []) if b.get("asset")=="USDT"]
    except Exception as e:
        result["spot_error"] = str(e)
    return jsonify(result)

@app.route("/api/tickers")
def tickers():
    try:
        r = requests.get("https://api.mexc.com/api/v3/ticker/24hr", timeout=10)
        data = [{"symbol":t["symbol"],
                 "price":float(t.get("lastPrice",0)),
                 "change":float(t.get("priceChangePercent",0))}
                for t in r.json()
                if t.get("symbol","").endswith("USDT")
                and float(t.get("quoteVolume",0))>1_000_000]
        return jsonify({"ok":True,"data":data[:40]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/zerofee")
def zerofee():
    return jsonify({"ok":True,"pairs":[
        "BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT",
        "BNBUSDT","PEPEUSDT","SHIBUSDT","WIFUSDT","BONKUSDT",
        "FLOKIUSDT","NOTUSDT","TURBOUSDT","MEMEUSDT","BRETTUSDT",
    ]})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
