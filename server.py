import os, time, hmac, hashlib, json, logging
import requests
from flask import Flask, jsonify, request, Response

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

app  = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
_sess = {}

FUTURES_BASE = "https://contract.mexc.com"

def get_creds():
    key    = os.environ.get("MEXC_API_KEY","")    or _sess.get("api_key","")
    secret = os.environ.get("MEXC_API_SECRET","") or _sess.get("api_secret","")
    return key, secret

# ===== SPOT HELPERS =====
def sign_spot(secret, params):
    q = "&".join(f"{k}={v}" for k,v in params.items())
    return hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()

def mexc_get(path, extra=None):
    key, secret = get_creds()
    p = {}
    if extra: p.update(extra)
    p["timestamp"] = int(time.time()*1000)
    p["signature"] = sign_spot(secret, p)
    r = requests.get(f"https://api.mexc.com{path}", params=p,
                     headers={"X-MEXC-APIKEY": key}, timeout=10)
    return r.status_code, r.json()

def mexc_post(path, params):
    key, secret = get_creds()
    p = dict(params)
    p["timestamp"] = int(time.time()*1000)
    query = "&".join(f"{k}={v}" for k,v in p.items())
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"https://api.mexc.com{path}?{query}&signature={sig}"
    r = requests.post(url, headers={"X-MEXC-APIKEY": key}, timeout=10)
    log.info(f"[SPOT POST] {path} status={r.status_code}")
    return r.status_code, r.json()

# ===== FUTURES HELPERS =====
def sign_futures(secret, ts, body_str=""):
    msg = secret + ts + secret
    if body_str:
        msg = secret + ts + body_str + secret
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

def futures_get(path, params=None):
    key, secret = get_creds()
    ts = str(int(time.time()*1000))
    headers = {
        "ApiKey": key,
        "Request-Time": ts,
        "Signature": sign_futures(secret, ts),
        "Content-Type": "application/json"
    }
    r = requests.get(f"{FUTURES_BASE}{path}", params=params or {}, headers=headers, timeout=10)
    log.info(f"[FUT GET] {path} status={r.status_code}")
    return r.status_code, r.json()

def futures_post(path, body):
    key, secret = get_creds()
    ts = str(int(time.time()*1000))
    body_str = json.dumps(body, separators=(',',':'))
    headers = {
        "ApiKey": key,
        "Request-Time": ts,
        "Signature": sign_futures(secret, ts, body_str),
        "Content-Type": "application/json"
    }
    r = requests.post(f"{FUTURES_BASE}{path}", data=body_str, headers=headers, timeout=10)
    log.info(f"[FUT POST] {path} status={r.status_code} body={body_str[:80]}")
    return r.status_code, r.json()

# ===== ROTAS GERAIS =====
@app.route("/")
def index():
    html = open(os.path.join(BASE,"templates","index.html"), encoding="utf-8").read()
    return Response(html, content_type="text/html; charset=utf-8")

@app.route("/api/health")
def health():
    key, _ = get_creds()
    return jsonify({"ok":True,"has_key":bool(key),"version":"4.0-futures"})

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

# ===== SPOT ROTAS =====
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
                return jsonify({"ok":True,"balance":free,"equity":free+locked,"account":"Spot"})
            return jsonify({"ok":False,"error":"Sem USDT na conta Spot."})
        return jsonify({"ok":False,"error": d.get("msg") or f"HTTP {status}"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/wallet")
def wallet():
    key, secret = get_creds()
    if not key or not secret:
        return jsonify({"ok":False,"error":"Sem credenciais"})
    try:
        status, d = mexc_get("/api/v3/account")
        if status != 200:
            return jsonify({"ok":False,"error": d.get("msg","")})
        balances = d.get("balances") or []
        nonzero = [b for b in balances if float(b.get("free",0))+float(b.get("locked",0))>0.000001]
        result = []
        for b in nonzero:
            asset = b.get("asset","")
            free = float(b.get("free",0)); locked = float(b.get("locked",0)); total = free+locked
            if asset == "USDT":
                result.append({"asset":asset,"free":free,"locked":locked,"total":total,"price":1.0,"value_usdt":total,"symbol":"-"})
                continue
            sym = f"{asset}USDT"
            try:
                rp = requests.get("https://api.mexc.com/api/v3/ticker/price", params={"symbol":sym}, timeout=5)
                price = float(rp.json().get("price",0))
                value = total * price
                result.append({"asset":asset,"free":free,"locked":locked,"total":total,"price":price,"value_usdt":round(value,4),"symbol":sym})
            except:
                result.append({"asset":asset,"free":free,"locked":locked,"total":total,"price":0,"value_usdt":0,"symbol":sym})
        result.sort(key=lambda x: x["value_usdt"], reverse=True)
        total_usdt = sum(x["value_usdt"] for x in result)
        return jsonify({"ok":True,"assets":result,"total_usdt":round(total_usdt,4)})
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
        if status == 200:
            fills = d.get("fills",[{}]); price = fills[0].get("price","0") if fills else "0"
            return jsonify({"ok":True,"orderId":d.get("orderId"),"symbol":d.get("symbol"),"qty":d.get("executedQty"),"price":price,"side":"BUY"})
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
        status, d = mexc_post("/api/v3/order", {"symbol":symbol,"side":"SELL","type":"MARKET","quantity":str(quantity)})
        if status == 200:
            return jsonify({"ok":True,"orderId":d.get("orderId"),"symbol":d.get("symbol"),"qty":d.get("executedQty"),"side":"SELL"})
        return jsonify({"ok":False,"error":d.get("msg") or f"HTTP {status}","raw":d})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

# ===== FUTURES ROTAS =====
@app.route("/api/futures/balance")
def futures_balance():
    try:
        status, d = futures_get("/api/v1/private/account/assets")
        if status == 200:
            data = d.get("data") or []
            usdt = next((a for a in data if a.get("currency","").upper()=="USDT"), None)
            if usdt:
                avail = float(usdt.get("availableBalance",0))
                equity = float(usdt.get("equity",avail))
                return jsonify({"ok":True,"balance":avail,"equity":equity,"account":"Futures"})
            return jsonify({"ok":False,"error":"Sem USDT em Futures"})
        return jsonify({"ok":False,"error": d.get("message","") or f"HTTP {status}"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/futures/positions")
def futures_positions():
    """Retorna todas as posicoes abertas em Futures"""
    try:
        status, d = futures_get("/api/v1/private/position/open_positions")
        if status == 200:
            positions = d.get("data") or []
            result = []
            for p in positions:
                symbol = p.get("symbol","")
                side = "LONG" if p.get("positionType",1)==1 else "SHORT"
                qty = float(p.get("holdVol",0))
                entry = float(p.get("openAvgPrice",0))
                liq = float(p.get("liquidatePrice",0))
                margin = float(p.get("im",0))
                leverage = int(p.get("leverage",2))
                # Busca preco atual
                try:
                    rp = requests.get(f"{FUTURES_BASE}/api/v1/contract/ticker",
                                      params={"symbol":symbol}, timeout=5)
                    last = float(rp.json().get("data",{}).get("lastPrice",entry))
                except:
                    last = entry
                # Calcula PnL
                if entry > 0:
                    if side == "LONG":
                        pnl_pct = (last - entry) / entry * 100 * leverage
                    else:
                        pnl_pct = (entry - last) / entry * 100 * leverage
                    pnl_usdt = round((pnl_pct/100) * margin, 4)
                else:
                    pnl_pct = 0; pnl_usdt = 0
                result.append({
                    "symbol": symbol, "side": side, "qty": qty,
                    "entry": entry, "last": last, "liq": liq,
                    "margin": margin, "leverage": leverage,
                    "pnl_pct": round(pnl_pct,2), "pnl_usdt": pnl_usdt
                })
            return jsonify({"ok":True,"positions":result})
        return jsonify({"ok":False,"error": d.get("message","") or f"HTTP {status}", "raw":d})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/futures/open", methods=["POST"])
def futures_open():
    """Abre posicao LONG ou SHORT em Futures"""
    key, secret = get_creds()
    if not key or not secret: return jsonify({"ok":False,"error":"Sem credenciais"})
    data = request.get_json(silent=True) or {}
    symbol   = data.get("symbol","BTC_USDT")   # formato Futures: BTC_USDT
    side     = data.get("side","LONG")          # LONG ou SHORT
    amount   = float(data.get("amount",1))     # USDT a alocar
    leverage = int(data.get("leverage",2))
    try:
        # 1. Define alavancagem
        futures_post("/api/v1/private/position/change_leverage", {
            "symbol": symbol, "leverage": leverage, "openType": 1
        })
        # 2. Pega preco atual para calcular quantidade
        rp = requests.get(f"{FUTURES_BASE}/api/v1/contract/ticker",
                          params={"symbol":symbol}, timeout=5)
        price = float(rp.json().get("data",{}).get("lastPrice",0))
        if price <= 0:
            return jsonify({"ok":False,"error":"Nao foi possivel obter preco"})
        # 3. Calcula vol em contratos (1 contrato = tamanho minimo)
        # Para USDT perp: vol = (amount * leverage) / price
        # Ajusta para o minimo da MEXC (geralmente 1 contrato)
        vol = max(1, round((amount * leverage) / price, 4))
        # 4. Abre ordem
        order_side = 1 if side == "LONG" else 3  # 1=abrir long, 3=abrir short
        body = {
            "symbol": symbol,
            "side": order_side,    # 1=open long, 2=close long, 3=open short, 4=close short
            "orderType": 5,        # 5=market
            "vol": vol,
            "leverage": leverage,
            "openType": 1,         # 1=isolated
            "priceProtect": "0"
        }
        status, d = futures_post("/api/v1/private/order/submit", body)
        log.info(f"[FUT OPEN] {symbol} {side} vol={vol} price={price} resp={d}")
        if status == 200 and d.get("success"):
            return jsonify({"ok":True,"orderId":d.get("data"),"symbol":symbol,
                            "side":side,"vol":vol,"price":price,"leverage":leverage,"amount":amount})
        return jsonify({"ok":False,"error":d.get("message","") or f"HTTP {status}","raw":d})
    except Exception as e:
        log.error(f"[FUT OPEN] erro: {e}")
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/futures/close", methods=["POST"])
def futures_close():
    """Fecha posicao Futures"""
    key, secret = get_creds()
    if not key or not secret: return jsonify({"ok":False,"error":"Sem credenciais"})
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol","")
    side   = data.get("side","LONG")   # side da posicao ABERTA
    vol    = data.get("vol",0)
    if not symbol or not vol:
        return jsonify({"ok":False,"error":"symbol e vol obrigatorios"})
    try:
        close_side = 2 if side == "LONG" else 4  # 2=fechar long, 4=fechar short
        body = {
            "symbol": symbol,
            "side": close_side,
            "orderType": 5,   # market
            "vol": float(vol),
            "openType": 1,
            "priceProtect": "0"
        }
        status, d = futures_post("/api/v1/private/order/submit", body)
        log.info(f"[FUT CLOSE] {symbol} {side} vol={vol} resp={d}")
        if status == 200 and d.get("success"):
            return jsonify({"ok":True,"orderId":d.get("data"),"symbol":symbol,"side":side})
        return jsonify({"ok":False,"error":d.get("message","") or f"HTTP {status}","raw":d})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/futures/symbols")
def futures_symbols():
    """Lista pares disponiveis em Futures"""
    try:
        r = requests.get(f"{FUTURES_BASE}/api/v1/contract/detail", timeout=10)
        data = r.json().get("data") or []
        symbols = [{"symbol":s.get("symbol"),"baseCoin":s.get("baseCoin"),
                    "quoteCoin":s.get("quoteCoin"),"minVol":s.get("minVol")}
                   for s in data if s.get("quoteCoin","")=="USDT"]
        return jsonify({"ok":True,"symbols":symbols[:50]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

# ===== ROTAS COMUNS =====
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
                for t in r.json() if t.get("symbol","").endswith("USDT") and float(t.get("quoteVolume",0))>1000000]
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
                if float(b.get("free",0))+float(b.get("locked",0))>0][:15]
        else:
            result["spot_error"] = d.get("msg","")
    except Exception as e:
        result["spot_error"] = str(e)
    return jsonify(result)

if __name__ == "__main__":
    port = int(os.environ.get("PORT",5000))
    log.info(f"MEXC Bot v4.0 Futures+Spot porta {port}")
    app.run(host="0.0.0.0", port=port)
