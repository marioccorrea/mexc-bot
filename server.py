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
                      headers={"X-MEXC-APIKEY": key, "Content-Type": "application/json"},
                      timeout=10)
    return r.status_code, r.json()

@app.route("/")
def index():
    html = open(os.path.join(BASE,"templates","index.html"), encoding="utf-8").read()
    return Response(html, content_type="text/html; charset=utf-8")

@app.route("/api/health")
def health():
    key, _ = get_creds()
    return jsonify({"ok":True,"has_key":bool(key),"version":"3.1-live"})

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
