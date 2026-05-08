import os, time, hmac, hashlib, json, logging
import requests
from flask import Flask, jsonify, request, Response

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

app  = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
_sess = {}

def get_creds():
    key    = os.environ.get("MEXC_API_KEY","")    or _sess.get("api_key","")
    secret = os.environ.get("MEXC_API_SECRET","") or _sess.get("api_secret","")
    return key, secret

def sign(secret, params):
    q = "&".join(f"{k}={v}" for k,v in sorted(params.items()))
    return hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()

def mexc_get(path, extra=None):
    key, secret = get_creds()
    p = {"timestamp": int(time.time()*1000)}
    if extra: p.update(extra)
    p["signature"] = sign(secret, p)
    r = requests.get(f"https://api.mexc.com{path}", params=p, headers={"X-MEXC-APIKEY": key}, timeout=10)
    return r.status_code, r.json()

def mexc_post(path, params):
    key, secret = get_creds()
    p = {"timestamp": int(time.time()*1000)}
    p.update(params)
    p["signature"] = sign(secret, p)
    r = requests.post(f"https://api.mexc.com{path}", params=p,
                      headers={"X-MEXC-APIKEY": key, "Content-Type": "application/x-www-form-urlencoded"},
                      timeout=10)
    return r.status_code, r.json()

@app.route("/")
def index():
    html = open(os.path.join(BASE,"templates","index.html"), encoding="utf-8").read()
    return Response(html, content_type="text/html; charset=utf-8")

@app.route("/api/health")
def health():
    key, _ = get_creds()
    return jsonify({"ok":True,"has_key":bool(key),"version":"3.0-live"})

@app.route("/api/creds")
def creds():
    key, secret = get_creds()
    return jsonify({"has_key":bool(key),"has_secret":bool(secret),
        "key_hint":(key[:4]+"...."+key[-4:]) if key else "","mode":"live" if (key and secret) else "sim"})

@app.route("/api/config", methods=["POST"])
def set_config():
    data = request.get_json(silent=True) or {}
    _sess.update({k:v for k,v in data.items() if k in ["api_key","api_secret"]})
    return jsonify({"ok":True})

@app.route("/api/balance")
def balance():
    key, secret = get_creds()
    if not key or not secret:
        return jsonify({"ok":False,"error":"API Key nao configurada."})
    try:
        status, d = mexc_get("/api/v3/account")
        if status == 200:
            usdt = next((b for b in (d.get("balances") or []) if b.get("asset")=="USDT"), None)
            if usdt:
                free = float(usdt.get("free",0)); locked = float(usdt.get("locked",0))
                log.info(f"[Balance] USDT free={free:.4f}")
                return jsonify({"ok":True,"balance":free,"equity":free+locked,"account":"Spot"})
            return jsonify({"ok":False,"error":"Sem USDT na conta Spot."})
        return jsonify({"ok":False,"error": d.get("msg") or f"HTTP {status}"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/order/buy", methods=["POST"])
def order_buy():
    key, secret = get_creds()
    if not key or not secret: return jsonify({"ok":False,"error":"Sem credenciais"})
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol",""); amount = float(data.get("quoteOrderQty",10))
    if not symbol: return jsonify({"ok":False,"error":"Symbol obrigatorio"})
    try:
        status, d = mexc_post("/api/v3/order",
            {"symbol":symbol,"side":"BUY","type":"MARKET","quoteOrderQty":str(round(amount,2))})
        log.info(f"[BUY] {symbol} status={status} resp={d}")
        if status == 200:
            fills = d.get("fills",[{}])
            price = fills[0].get("price","0") if fills else d.get("price","0")
            return jsonify({"ok":True,"orderId":d.get("orderId"),"symbol":d.get("symbol"),
                            "qty":d.get("executedQty"),"price":price,"side":"BUY"})
        return jsonify({"ok":False,"error":d.get("msg") or f"HTTP {status}","raw":d})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/order/sell", methods=["POST"])
def order_sell():
    key, secret = get_creds()
    if not key or not secret: return jsonify({"ok":False,"error":"Sem credenciais"})
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol",""); quantity = data.get("quantity","")
    if not symbol or not quantity: return jsonify({"ok":False,"error":"symbol e quantity obrigatorios"})
    try:
        status, d = mexc_post("/api/v3/order",
            {"symbol":symbol,"side":"SELL","type":"MARKET","quantity":str(quantity)})
        log.info(f"[SELL] {symbol} qty={quantity} status={status} resp={d}")
        if status == 200:
            return jsonify({"ok":True,"orderId":d.get("orderId"),"symbol":d.get("symbol"),
                            "qty":d.get("executedQty"),"side":"SELL"})
        return jsonify({"ok":False,"error":d.get("msg") or f"HTTP {status}","raw":d})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/symbol/info")
def symbol_info():
    symbol = request.args.get("symbol","BTCUSDT")
    try:
        r = requests.get("https://api.mexc.com/api/v3/exchangeInfo",params={"symbol":symbol},timeout=8)
        syms = r.json().get("symbols",[])
        if syms:
            s = syms[0]; filters = {f["filterType"]:f for f in s.get("filters",[])}; lot = filters.get("LOT_SIZE",{})
            return jsonify({"ok":True,"baseAsset":s.get("baseAsset"),
                "minQty":lot.get("minQty","0.00001"),"stepSize":lot.get("stepSize","0.00001")})
        return jsonify({"ok":False,"error":"Par nao encontrado"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/price")
def price():
    symbol = request.args.get("symbol","BTCUSDT")
    try:
        r = requests.get("https://api.mexc.com/api/v3/ticker/price",params={"symbol":symbol},timeout=5)
        return jsonify({"ok":True,"symbol":symbol,"price":float(r.json().get("price",0))})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/tickers")
def tickers():
    try:
        r = requests.get("https://api.mexc.com/api/v3/ticker/24hr",timeout=10)
        data = [{"symbol":t["symbol"],"price":float(t.get("lastPrice",0)),
                 "change":float(t.get("priceChangePercent",0)),"volume":float(t.get("quoteVolume",0))}
                for t in r.json() if t.get("symbol","").endswith("USDT") and float(t.get("quoteVolume",0))>1_000_000]
        return jsonify({"ok":True,"data":data[:50]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/klines")
def klines():
    sym=request.args.get("symbol","BTCUSDT"); inv=request.args.get("interval","5m"); lim=request.args.get("limit","100")
    try:
        r = requests.get("https://api.mexc.com/api/v3/klines",params={"symbol":sym,"interval":inv,"limit":lim},timeout=8)
        return jsonify({"ok":True,"data":r.json()})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/zerofee")
def zerofee():
    return jsonify({"ok":True,"pairs":["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT",
        "BNBUSDT","PEPEUSDT","SHIBUSDT","WIFUSDT","BONKUSDT","FLOKIUSDT","NOTUSDT","TURBOUSDT","MEMEUSDT","BRETTUSDT"]})

@app.route("/api/debug")
def debug():
    key, _ = get_creds()
    if not key: return jsonify({"error":"Sem credenciais"})
    result = {"key_hint": key[:4]+"...."+key[-4:]}
    try:
        status, d = mexc_get("/api/v3/account")
        result["spot_status"] = status
        if status == 200:
            result["spot_balances"] = [b for b in (d.get("balances") or [])
                if float(b.get("free",0))+float(b.get("locked",0))>0][:10]
        else:
            result["spot_error"] = d.get("msg","")
    except Exception as e:
        result["spot_error"] = str(e)
    return jsonify(result)

if __name__ == "__main__":
    port = int(os.environ.get("PORT",5000))
    log.info(f"MEXC Bot v3.0 LIVE porta {port}")
    app.run(host="0.0.0.0", port=port)
