import os, time, hmac, hashlib, json
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))

def sign(secret, params):
    q = "&".join(f"{k}={v}" for k,v in sorted(params.items()))
    return hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()

@app.route("/")
def index():
    return open(os.path.join(BASE,"templates","index.html"), encoding="utf-8").read()

@app.route("/api/health")
def health():
    key = os.environ.get("MEXC_API_KEY","")
    return jsonify({"ok": True, "has_key": bool(key)})

@app.route("/api/creds")
def creds():
    key    = os.environ.get("MEXC_API_KEY","")
    secret = os.environ.get("MEXC_API_SECRET","")
    return jsonify({
        "has_key":    bool(key),
        "has_secret": bool(secret),
        "key_hint":   (key[:4]+"••••"+key[-4:]) if key else "",
        "mode":       "live" if (key and secret) else "sim"
    })

@app.route("/api/balance")
def balance():
    key    = os.environ.get("MEXC_API_KEY","") or _sess.get("api_key","")
    secret = os.environ.get("MEXC_API_SECRET","") or _sess.get("api_secret","")
    if not key or not secret:
        return jsonify({"ok":False,"error":"API Key não configurada. Adicione MEXC_API_KEY e MEXC_API_SECRET nas variáveis do Render (Environment → Add Variable)."})
    headers = {"X-MEXC-APIKEY": key}
    # Tenta Spot
    try:
        ts = int(time.time()*1000)
        p  = {"timestamp": ts}
        p["signature"] = sign(secret, p)
        r = requests.get("https://api.mexc.com/api/v3/account",
                         params=p, headers=headers, timeout=10)
        d = r.json()
        if r.status_code == 200:
            bals = d.get("balances") or []
            usdt = next((b for b in bals if b.get("asset")=="USDT"), None)
            if usdt:
                free   = float(usdt.get("free",0))
                locked = float(usdt.get("locked",0))
                return jsonify({"ok":True,"balance":free,"equity":free+locked,"account":"Spot"})
            return jsonify({"ok":False,"error":"Sem USDT na conta Spot."})
        msg = d.get("msg") or d.get("message") or f"HTTP {r.status_code}"
        if r.status_code in (401,403):
            return jsonify({"ok":False,"error":f"API Key inválida: {msg}"})
        return jsonify({"ok":False,"error":msg})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

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

_sess = {}

@app.route("/api/config", methods=["POST"])
def set_config():
    data = request.get_json(silent=True) or {}
    _sess.update({k:v for k,v in data.items() if k in ["api_key","api_secret"]})
    return jsonify({"ok": True})

# Override balance to use session creds if no env vars
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
