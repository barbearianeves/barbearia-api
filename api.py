import os
import time
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Segredos (configura no Render -> Environment)
BRIDGE_SECRET = os.environ.get("BRIDGE_SECRET", "neves-12345")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "muda-isto")

# "Base de dados" em memória (para já)
BOOKINGS = {}  # id -> booking dict

# Fila de alterações para a bridge puxar (Render -> PC)
CHANGES = []   # lista de {"id": int, "op": "upsert"/"delete", "payload": {...}}
CHANGE_ID = 0


def bad(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def push_change(op, payload):
    """Regista uma alteração para a bridge puxar via /pull"""
    global CHANGE_ID
    CHANGE_ID += 1
    CHANGES.append({"id": CHANGE_ID, "op": op, "payload": payload})


def is_bridge(req):
    return req.args.get("secret", "") == BRIDGE_SECRET


def is_admin(req):
    return req.headers.get("X-Admin-Token", "") == ADMIN_TOKEN


def normalize_booking(b):
    """Garante campos mínimos e defaults."""
    out = dict(b or {})
    out.setdefault("status", "Marcado")
    out.setdefault("dur", 30)
    out.setdefault("notes", "")
    out.setdefault("client_id", "")
    out.setdefault("client", "")
    return out


def generate_id():
    return str(int(time.time() * 1000))


@app.get("/health")
def health():
    return jsonify({"ok": True})


# ----------------------
# CLIENTE -> API (site)
# ----------------------
@app.post("/book")
def book():
    data = request.get_json(silent=True) or {}

    # Esperados (do site):
    # name, phone, service, barber, date (YYYY-MM-DD), time (HH:MM), dur (int)
    if not data.get("date") or not data.get("time") or not data.get("barber") or not data.get("service"):
        return bad("missing fields: date/time/barber/service", 400)

    bid = data.get("id") or generate_id()
    data["id"] = bid

    booking = normalize_booking(data)
    BOOKINGS[bid] = booking

    # Notifica bridge (Render -> PC)
    push_change("upsert", booking)
    return jsonify({"ok": True, "id": bid})


@app.get("/day")
def day():
    date = (request.args.get("date") or "").strip()
    barber = (request.args.get("barber") or "").strip()
    if not date:
        return bad("missing date", 400)

    out = []
    for b in BOOKINGS.values():
        if b.get("date") != date:
            continue
        if barber and b.get("barber") != barber:
            continue
        # por defeito, não mostrar cancelados
        if str(b.get("status", "")).lower() == "cancelado":
            continue
        out.append(b)

    out.sort(key=lambda x: x.get("time", ""))
    return jsonify({"ok": True, "items": out})


@app.get("/busy")
def busy():
    date = (request.args.get("date") or "").strip()
    barber = (request.args.get("barber") or "").strip()
    if not date or not barber:
        return bad("missing date/barber", 400)

    out = []
    for b in BOOKINGS.values():
        if b.get("date") == date and b.get("barber") == barber:
            if str(b.get("status", "")).lower() != "cancelado":
                out.append({"time": b.get("time"), "dur": int(b.get("dur", 30))})

    out.sort(key=lambda x: x.get("time", ""))
    return jsonify({"ok": True, "items": out})


# ----------------------
# BRIDGE (PC) <-> API
# ----------------------

# Render -> PC (bridge puxa mudanças)
@app.get("/pull")
def pull():
    if not is_bridge(request):
        return bad("unauthorized", 401)

    cursor = int(request.args.get("cursor", "0") or "0")
    limit = int(request.args.get("limit", "300") or "300")

    items = [ev for ev in CHANGES if ev["id"] > cursor]
    items = items[: max(1, min(limit, 1000))]

    new_cursor = cursor
    if items:
        new_cursor = items[-1]["id"]

    return jsonify({"ok": True, "cursor": new_cursor, "items": items})


# PC -> Render (bridge envia mudanças detectadas nos TXT)
@app.post("/sync")
def sync():
    if not is_bridge(request):
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    changes = data.get("changes") or []
    if not isinstance(changes, list):
        return bad("changes must be a list", 400)

    for ch in changes:
        op = ch.get("op")
        payload = ch.get("payload") or {}
        if op == "upsert":
            bid = payload.get("id")
            if not bid:
                continue
            BOOKINGS[bid] = normalize_booking(payload)
            # IMPORTANTE: não fazemos push_change aqui para evitar loop infinito.
        elif op == "delete":
            bid = (payload or {}).get("id")
            if bid:
                BOOKINGS.pop(bid, None)
                # não push_change aqui também

    return jsonify({"ok": True})


# ----------------------
# ADMIN (Barbeiro)
# ----------------------

@app.get("/admin/bookings")
def admin_list():
    if not is_admin(request):
        return bad("unauthorized", 401)

    date = (request.args.get("date") or "").strip()
    barber = (request.args.get("barber") or "").strip()

    out = []
    for b in BOOKINGS.values():
        if date and b.get("date") != date:
            continue
        if barber and b.get("barber") != barber:
            continue
        out.append(b)

    out.sort(key=lambda x: x.get("time", ""))
    return jsonify({"ok": True, "items": out})


@app.patch("/admin/bookings/<bid>")
def admin_patch(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    if bid not in BOOKINGS:
        return bad("not found", 404)

    data = request.get_json(silent=True) or {}
    BOOKINGS[bid] = normalize_booking({**BOOKINGS[bid], **data})

    # Notifica bridge (Render -> PC)
    push_change("upsert", BOOKINGS[bid])
    return jsonify({"ok": True})


@app.delete("/admin/bookings/<bid>")
def admin_delete(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    existed = bid in BOOKINGS
    BOOKINGS.pop(bid, None)

    if existed:
        push_change("delete", {"id": bid})
    return jsonify({"ok": True})


if __name__ == "__main__":
    # Local dev: python api.py
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
