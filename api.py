from flask import Flask, request, jsonify
from flask_cors import CORS
import os, time, secrets, json, re, sqlite3

app = Flask(__name__)

CORS(
    app,
    resources={r"/*": {"origins": "*"}},
    allow_headers=["Content-Type", "X-Admin-Token"],
    expose_headers=["Content-Type"],
)

# =========================
# CONFIG / SECRETS
# =========================
BRIDGE_SECRET = os.environ.get("BRIDGE_SECRET", "neves-12345").strip()
ADMIN_TOKEN   = os.environ.get("ADMIN_TOKEN", "neves-12345").strip()

# =========================
# Helpers
# =========================
def now_id():
    return str(int(time.time() * 1_000_000)) + "-" + secrets.token_hex(3)

def bad(msg, code=400):
    return jsonify({"error": msg}), code

def is_admin(req):
    return (req.headers.get("X-Admin-Token", "") or "").strip() == ADMIN_TOKEN

def is_bridge(req):
    secret = (req.args.get("secret", "") or "").strip()
    return secret == BRIDGE_SECRET

def norm_str(x):
    return (x or "").strip()

def norm_email(x):
    return (x or "").strip().lower()

def slugify_name(name: str) -> str:
    s = (name or "").strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^0-9A-Za-zÀ-ÿ_]+", "", s)
    return s[:80] or "Cliente"

def normalize_phone_pt9(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    digits = re.sub(r"\D", "", s)
    if digits.startswith("351") and len(digits) >= 12:
        digits = digits[3:]
    if len(digits) > 9:
        digits = digits[-9:]
    return digits if len(digits) == 9 else ""

def norm_time_hhmm(raw: str) -> str:
    t = (raw or "").strip()
    if len(t) >= 5:
        t = t[:5]
    return t

# -------------------------
# STORAGE: SQLite (um ficheiro)
# -------------------------
def pick_data_dir():
    cand = (os.environ.get("DATA_DIR", "") or "").strip()
    candidates = []
    if cand:
        candidates.append(cand)
    candidates.append(os.path.join(os.path.dirname(__file__), "data"))
    candidates.append("/tmp/barbearia_data")

    for p in candidates:
        try:
            os.makedirs(p, exist_ok=True)
            testf = os.path.join(p, ".write_test")
            with open(testf, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(testf)
            return p
        except Exception:
            continue
    return None

DATA_DIR = pick_data_dir()
DB_PATH  = os.path.join(DATA_DIR, "barbearia.sqlite") if DATA_DIR else ":memory:"

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def db_init():
    conn = db()
    cur = conn.cursor()

    # bookings
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
      id TEXT PRIMARY KEY,
      data TEXT NOT NULL
    )
    """)

    # clients (perfil)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS clients (
      id TEXT PRIMARY KEY,
      phone9 TEXT UNIQUE,
      name TEXT,
      email TEXT,
      data TEXT NOT NULL,
      updated_at INTEGER NOT NULL
    )
    """)

    # phone index (login)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS phone_index (
      phone9 TEXT PRIMARY KEY,
      client_id TEXT NOT NULL
    )
    """)

    # changes feed (bookings) para bridge
    cur.execute("""
    CREATE TABLE IF NOT EXISTS changes (
      seq INTEGER PRIMARY KEY AUTOINCREMENT,
      op TEXT NOT NULL,
      payload TEXT NOT NULL,
      ts INTEGER NOT NULL
    )
    """)

    # changes feed (clients) para bridge
    cur.execute("""
    CREATE TABLE IF NOT EXISTS client_changes (
      seq INTEGER PRIMARY KEY AUTOINCREMENT,
      op TEXT NOT NULL,
      payload TEXT NOT NULL,
      ts INTEGER NOT NULL
    )
    """)

    conn.commit()
    conn.close()

db_init()

def push_change(op: str, payload: dict):
    conn = db()
    conn.execute(
        "INSERT INTO changes(op,payload,ts) VALUES(?,?,?)",
        (op, json.dumps(payload, ensure_ascii=False), int(time.time()))
    )
    conn.commit()
    conn.close()

def push_client_change(op: str, payload: dict):
    conn = db()
    conn.execute(
        "INSERT INTO client_changes(op,payload,ts) VALUES(?,?,?)",
        (op, json.dumps(payload, ensure_ascii=False), int(time.time()))
    )
    conn.commit()
    conn.close()

def db_put_booking(b: dict):
    bid = norm_str(b.get("id"))
    conn = db()
    conn.execute("INSERT OR REPLACE INTO bookings(id,data) VALUES(?,?)",
                 (bid, json.dumps(b, ensure_ascii=False)))
    conn.commit()
    conn.close()

def db_del_booking(bid: str):
    conn = db()
    conn.execute("DELETE FROM bookings WHERE id=?", (bid,))
    conn.commit()
    conn.close()

def db_all_bookings() -> list:
    conn = db()
    rows = conn.execute("SELECT data FROM bookings").fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            out.append(json.loads(r["data"]))
        except Exception:
            pass
    return out

def db_get_booking(bid: str) -> dict:
    conn = db()
    row = conn.execute("SELECT data FROM bookings WHERE id=?", (bid,)).fetchone()
    conn.close()
    if not row:
        return {}
    try:
        return json.loads(row["data"])
    except Exception:
        return {}

def db_bookings_count() -> int:
    conn = db()
    row = conn.execute("SELECT COUNT(*) AS n FROM bookings").fetchone()
    conn.close()
    return int(row["n"] or 0)

# -------------------------
# CLIENTS in DB
# -------------------------
def next_client_id() -> str:
    # 000001 increment
    conn = db()
    row = conn.execute("SELECT id FROM clients ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if not row:
        return "000001"
    try:
        n = int(row["id"])
        return f"{n+1:06d}"
    except Exception:
        return f"{int(time.time())%1000000:06d}"

def get_or_create_client_id_by_phone9(phone9: str) -> tuple[str, bool]:
    phone9 = normalize_phone_pt9(phone9)
    if not phone9:
        return "", False

    conn = db()
    row = conn.execute("SELECT client_id FROM phone_index WHERE phone9=?", (phone9,)).fetchone()
    if row:
        cid = row["client_id"]
        conn.close()
        return cid, True

    cid = next_client_id()
    conn.execute("INSERT OR REPLACE INTO phone_index(phone9,client_id) VALUES(?,?)", (phone9, cid))
    conn.commit()
    conn.close()
    return cid, False

def db_get_client_by_id(cid: str) -> dict:
    conn = db()
    row = conn.execute("SELECT data FROM clients WHERE id=?", (cid,)).fetchone()
    conn.close()
    if not row:
        return {}
    try:
        return json.loads(row["data"])
    except Exception:
        return {}

def db_get_client_by_phone9(phone9: str) -> dict:
    phone9 = normalize_phone_pt9(phone9)
    conn = db()
    row = conn.execute("SELECT data FROM clients WHERE phone9=?", (phone9,)).fetchone()
    conn.close()
    if not row:
        return {}
    try:
        return json.loads(row["data"])
    except Exception:
        return {}

def db_upsert_client_profile(cid: str, phone9: str, name: str, email: str = "") -> tuple[dict, bool, str]:
    phone9 = normalize_phone_pt9(phone9)
    if not phone9:
        return {}, False, "Telefone inválido"
    name = (name or "").strip()
    if not name:
        return {}, False, "Nome obrigatório"
    email = norm_email(email)

    # validar duplicado phone9
    conn = db()
    row = conn.execute("SELECT id FROM clients WHERE phone9=? AND id<>?", (phone9, cid)).fetchone()
    if row:
        conn.close()
        return {}, False, "Este telemóvel já existe noutro cliente"

    cur = conn.execute("SELECT data FROM clients WHERE id=?", (cid,)).fetchone()
    created = not bool(cur)

    client = {}
    if cur:
        try:
            client = json.loads(cur["data"])
        except Exception:
            client = {}

    client["id"] = cid
    client["nome"] = name
    client["telefone"] = phone9
    if email:
        client["email"] = email
    else:
        client["email"] = norm_email(client.get("email", ""))

    payload = dict(client)

    conn.execute("""
      INSERT OR REPLACE INTO clients(id, phone9, name, email, data, updated_at)
      VALUES(?,?,?,?,?,?)
    """, (cid, phone9, name, client.get("email",""), json.dumps(payload, ensure_ascii=False), int(time.time())))

    conn.execute("INSERT OR REPLACE INTO phone_index(phone9, client_id) VALUES(?,?)", (phone9, cid))
    conn.commit()
    conn.close()

    push_client_change("upsert", {
        "id": cid,
        "name": name,
        "phone": phone9,
        "email": client.get("email",""),
    })

    return payload, created, ""

def db_all_clients() -> list:
    conn = db()
    rows = conn.execute("SELECT data FROM clients").fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            out.append(json.loads(r["data"]))
        except Exception:
            pass
    return out

def db_clients_count() -> int:
    conn = db()
    row = conn.execute("SELECT COUNT(*) AS n FROM clients").fetchone()
    conn.close()
    return int(row["n"] or 0)

# =========================
# HOME / HEALTH
# =========================
@app.get("/")
def home():
    return jsonify({
        "ok": True,
        "service": "barbearia-api",
        "bookings": db_bookings_count(),
        "clients": db_clients_count(),
        "data_dir": DATA_DIR,
        "db_path": DB_PATH,
    })

@app.get("/health")
def health():
    return jsonify({"ok": True})

# =========================
# CLIENT (SEM OTP): /client/login
# =========================
@app.post("/client/login")
def client_login():
    data = request.get_json(silent=True) or {}
    phone9 = normalize_phone_pt9(norm_str(data.get("phone")))
    if not phone9:
        return bad("Telefone inválido (usa 9 dígitos PT)")

    # se já existe perfil, devolve
    existing_profile = db_get_client_by_phone9(phone9)
    if existing_profile:
        return jsonify({"ok": True, "existing": True, "client": existing_profile})

    # se não existe perfil, devolve id (index) mas NÃO cria perfil
    client_id, existing_index = get_or_create_client_id_by_phone9(phone9)
    return jsonify({
        "ok": True,
        "existing": False,
        "client": {"id": client_id, "nome": "", "telefone": phone9, "email": ""},
        "has_index": bool(existing_index),
    })

@app.get("/client/<client_id>")
def client_get(client_id):
    cid = norm_str(client_id)
    if not cid:
        return bad("id inválido")
    c = db_get_client_by_id(cid)
    if not c:
        return bad("not found", 404)
    return jsonify({"ok": True, "client": c})

@app.post("/client/upsert")
def client_upsert():
    data = request.get_json(silent=True) or {}
    cid = norm_str(data.get("id"))
    phone9 = normalize_phone_pt9(norm_str(data.get("phone")))
    name = norm_str(data.get("name"))
    email = norm_email(data.get("email"))

    if not cid:
        return bad("id obrigatório")
    if not phone9:
        return bad("phone obrigatório")
    if not name:
        return bad("name obrigatório")

    client, created, err = db_upsert_client_profile(cid, phone9, name, email=email)
    if err:
        return bad(err)
    return jsonify({"ok": True, "created": created, "client": client})

# =========================
# BOOKINGS (CLIENT)
# =========================
@app.post("/book")
def book():
    data = request.get_json(silent=True) or {}

    phone9 = normalize_phone_pt9(norm_str(data.get("phone")))
    if not phone9:
        return bad("Telefone inválido")

    required = ["service", "barber", "date", "time", "dur", "client_id"]
    for k in required:
        if not norm_str(str(data.get(k, ""))):
            return bad(f"Campo obrigatório em falta: {k}")

    cid = norm_str(data.get("client_id"))
    c = db_get_client_by_id(cid)
    if not c or not norm_str(c.get("nome")):
        return bad("Cliente sem perfil. Cria primeiro (nome).")

    bid = norm_str(data.get("id")) or now_id()
    t = norm_time_hhmm(data.get("time"))

    item = {
        "id": bid,
        "name": norm_str(data.get("name")) or norm_str(c.get("nome")),
        "phone": phone9,
        "email": norm_email(data.get("email")) or norm_email(c.get("email")),
        "service": norm_str(data.get("service")),
        "barber": norm_str(data.get("barber")),
        "date": norm_str(data.get("date")),
        "time": t,
        "dur": int(float(data.get("dur", 45))),
        "notes": norm_str(data.get("notes")),
        "status": "Marcado",
        "client_id": cid,
        "client": norm_str(c.get("nome")),
        "created_at": int(time.time()),
    }

    db_put_booking(item)
    push_change("upsert", item)
    return jsonify({"ok": True, "id": bid, "client_id": item["client_id"]})

# =========================
# DAY (CLIENT VIEW, sem dados pessoais)
# =========================
def _day_items_for_clients(date: str = "", barber: str = ""):
    out = []
    for b in db_all_bookings():
        if date and b.get("date") != date:
            continue
        if barber and b.get("barber") != barber:
            continue
        st = b.get("status", "Marcado")
        if st in ["Cancelado", "Desbloqueado"]:
            continue
        is_block = (st == "Bloqueado") or (b.get("service") == "INDISPONIVEL")
        out.append({
            "id": b.get("id", ""),
            "time": b.get("time", ""),
            "dur": b.get("dur", 45),
            "status": st,
            "label": "INDISPONÍVEL" if is_block else "OCUPADO",
            "service": b.get("service", ""),
            "notes": b.get("notes", ""),
        })
    return out

@app.get("/day")
def day():
    date = request.args.get("date", "")
    barber = request.args.get("barber", "")
    return jsonify({"ok": True, "items": _day_items_for_clients(date, barber)})

# =========================
# ✅ ADMIN ENDPOINTS
# =========================
@app.get("/admin/bookings")
def admin_bookings():
    if not is_admin(request):
        return bad("unauthorized", 401)

    date = norm_str(request.args.get("date", ""))
    barber = norm_str(request.args.get("barber", ""))

    items = []
    for b in db_all_bookings():
        if date and b.get("date") != date:
            continue
        if barber and b.get("barber") != barber:
            continue
        items.append(b)

    items.sort(key=lambda x: (x.get("date",""), x.get("time","")))
    return jsonify({"ok": True, "items": items})

@app.get("/admin/booking/<bid>")
def admin_booking_get(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)
    bid = norm_str(bid)
    b = db_get_booking(bid)
    if not b:
        return bad("not found", 404)
    return jsonify({"ok": True, "item": b})

@app.post("/admin/block")
def admin_block():
    if not is_admin(request):
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    date = norm_str(data.get("date"))
    time_s = norm_time_hhmm(data.get("time"))
    barber = norm_str(data.get("barber"))

    if not date or not time_s or not barber:
        return bad("date/time/barber obrigatórios")

    bid = now_id()
    item = {
        "id": bid,
        "name": "—",
        "phone": "",
        "email": "",
        "service": "INDISPONIVEL",
        "barber": barber,
        "date": date,
        "time": time_s,
        "dur": 45,
        "notes": "Bloqueado pelo barbeiro",
        "status": "Bloqueado",
        "client_id": "",
        "client": "",
        "created_at": int(time.time()),
    }
    db_put_booking(item)
    push_change("upsert", item)
    return jsonify({"ok": True, "id": bid})

@app.post("/admin/unblock/<bid>")
def admin_unblock(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    bid = norm_str(bid)
    b = db_get_booking(bid)
    if not b:
        return bad("not found", 404)

    b["status"] = "Desbloqueado"
    db_put_booking(b)
    push_change("upsert", b)
    return jsonify({"ok": True})

@app.post("/admin/cancel/<bid>")
def admin_cancel(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    bid = norm_str(bid)
    b = db_get_booking(bid)
    if not b:
        return bad("not found", 404)

    b["status"] = "Cancelado"
    db_put_booking(b)
    push_change("upsert", b)
    return jsonify({"ok": True})

# =========================
# ✅ BRIDGE ENDPOINTS (BOOKINGS)
# =========================
@app.get("/pull")
def bridge_pull():
    if not is_bridge(request):
        return bad("unauthorized", 401)

    try:
        cursor = int(request.args.get("cursor", "0") or "0")
    except Exception:
        cursor = 0
    try:
        limit = int(request.args.get("limit", "300") or "300")
    except Exception:
        limit = 300

    if cursor < 0: cursor = 0
    if limit < 1: limit = 1
    if limit > 2000: limit = 2000

    conn = db()
    rows = conn.execute(
        "SELECT seq, op, payload, ts FROM changes WHERE seq > ? ORDER BY seq ASC LIMIT ?",
        (cursor, limit)
    ).fetchall()
    conn.close()

    items = []
    last_seq = cursor
    for r in rows:
        last_seq = int(r["seq"])
        try:
            payload = json.loads(r["payload"])
        except Exception:
            payload = {}
        items.append({"op": r["op"], "payload": payload, "ts": int(r["ts"])})

    return jsonify({
        "ok": True,
        "items": items,
        "cursor": last_seq,   # cursor agora é seq
        "cursor_reset": False
    })

@app.post("/sync")
def bridge_sync():
    if not is_bridge(request):
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    changes = data.get("changes") or []
    if not isinstance(changes, list):
        return bad("changes deve ser lista")

    applied = 0

    for ev in changes:
        if not isinstance(ev, dict):
            continue
        op = (ev.get("op") or "").strip()
        payload = ev.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}

        bid = norm_str(payload.get("id"))
        if not bid:
            continue

        if op == "delete":
            db_del_booking(bid)
            push_change("delete", {"id": bid})
            applied += 1
            continue

        if op == "upsert":
            cur = db_get_booking(bid) or {}
            if not isinstance(cur, dict):
                cur = {}
            cur.update(payload)

            if cur.get("phone"):
                cur["phone"] = normalize_phone_pt9(cur.get("phone"))
            if cur.get("email"):
                cur["email"] = norm_email(cur.get("email"))
            if cur.get("time"):
                cur["time"] = norm_time_hhmm(cur.get("time"))
            if cur.get("dur") is not None:
                try:
                    cur["dur"] = int(float(cur.get("dur")))
                except Exception:
                    cur["dur"] = 45
            if not cur.get("created_at"):
                cur["created_at"] = int(time.time())

            db_put_booking(cur)
            push_change("upsert", cur)
            applied += 1
            continue

    return jsonify({"ok": True, "applied": applied})

@app.post("/replace_all")
def bridge_replace_all():
    if not is_bridge(request):
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if not isinstance(items, list):
        return bad("items deve ser lista")

    # apagar tudo
    conn = db()
    conn.execute("DELETE FROM bookings")
    conn.execute("DELETE FROM changes")
    conn.commit()
    conn.close()

    count = 0
    for b in items:
        if not isinstance(b, dict):
            continue
        bid = norm_str(b.get("id"))
        if not bid:
            continue

        if b.get("phone"):
            b["phone"] = normalize_phone_pt9(b.get("phone"))
        if b.get("email"):
            b["email"] = norm_email(b.get("email"))
        if b.get("time"):
            b["time"] = norm_time_hhmm(b.get("time"))
        if b.get("dur") is not None:
            try:
                b["dur"] = int(float(b.get("dur")))
            except Exception:
                b["dur"] = 45
        if not b.get("created_at"):
            b["created_at"] = int(time.time())

        db_put_booking(b)
        push_change("upsert", b)
        count += 1

    return jsonify({"ok": True, "count": count})

# =========================
# ✅ BRIDGE ENDPOINTS (CLIENTS)  <<<<< O QUE FALTAVA
# =========================
@app.get("/clients/all")
def clients_all():
    if not is_bridge(request):
        return bad("unauthorized", 401)
    # devolve lista simples para a bridge importar
    out = []
    for c in db_all_clients():
        out.append({
            "id": c.get("id",""),
            "name": c.get("nome",""),
            "phone": c.get("telefone",""),
            "email": c.get("email",""),
        })
    return jsonify({"ok": True, "clients": out})

@app.get("/clients/pull")
def clients_pull():
    if not is_bridge(request):
        return bad("unauthorized", 401)

    try:
        cursor = int(request.args.get("cursor", "0") or "0")
    except Exception:
        cursor = 0
    try:
        limit = int(request.args.get("limit", "300") or "300")
    except Exception:
        limit = 300

    if cursor < 0: cursor = 0
    if limit < 1: limit = 1
    if limit > 2000: limit = 2000

    conn = db()
    rows = conn.execute(
        "SELECT seq, op, payload, ts FROM client_changes WHERE seq > ? ORDER BY seq ASC LIMIT ?",
        (cursor, limit)
    ).fetchall()
    conn.close()

    items = []
    last_seq = cursor
    for r in rows:
        last_seq = int(r["seq"])
        try:
            payload = json.loads(r["payload"])
        except Exception:
            payload = {}
        items.append({"op": r["op"], "payload": payload, "ts": int(r["ts"])})

    return jsonify({"ok": True, "items": items, "cursor": last_seq})

@app.post("/clients/replace_all")
def clients_replace_all():
    if not is_bridge(request):
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    clients = data.get("clients") or []
    if not isinstance(clients, list):
        return bad("clients deve ser lista")

    # apagar tudo (clients + indices + changes)
    conn = db()
    conn.execute("DELETE FROM clients")
    conn.execute("DELETE FROM phone_index")
    conn.execute("DELETE FROM client_changes")
    conn.commit()
    conn.close()

    count = 0
    for c in clients:
        if not isinstance(c, dict):
            continue
        cid = norm_str(c.get("id"))
        name = norm_str(c.get("name"))
        phone9 = normalize_phone_pt9(c.get("phone"))
        email = norm_email(c.get("email"))

        if not cid:
            continue
        if phone9 and len(phone9) != 9:
            phone9 = ""

        payload = {"id": cid, "nome": name, "telefone": phone9, "email": email}

        # grava
        conn = db()
        conn.execute("""
          INSERT OR REPLACE INTO clients(id, phone9, name, email, data, updated_at)
          VALUES(?,?,?,?,?,?)
        """, (cid, phone9 or None, name, email, json.dumps(payload, ensure_ascii=False), int(time.time())))
        if phone9:
            conn.execute("INSERT OR REPLACE INTO phone_index(phone9, client_id) VALUES(?,?)", (phone9, cid))
        conn.commit()
        conn.close()

        push_client_change("upsert", {"id": cid, "name": name, "phone": phone9, "email": email})
        count += 1

    return jsonify({"ok": True, "count": count})

@app.post("/clients/sync")
def clients_sync():
    if not is_bridge(request):
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    changes = data.get("changes") or []
    if not isinstance(changes, list):
        return bad("changes deve ser lista")

    applied = 0

    for ev in changes:
        if not isinstance(ev, dict):
            continue
        op = norm_str(ev.get("op"))
        payload = ev.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}

        if op == "delete":
            cid = norm_str(payload.get("id"))
            if not cid:
                continue
            conn = db()
            conn.execute("DELETE FROM clients WHERE id=?", (cid,))
            conn.commit()
            conn.close()
            push_client_change("delete", {"id": cid})
            applied += 1
            continue

        if op == "upsert":
            cid = norm_str(payload.get("id"))
            name = norm_str(payload.get("name") or payload.get("nome"))
            phone9 = normalize_phone_pt9(payload.get("phone") or payload.get("telefone"))
            email = norm_email(payload.get("email"))

            if not cid:
                continue

            doc = {"id": cid, "nome": name, "telefone": phone9, "email": email}

            conn = db()
            # garantir unicidade do phone
            if phone9:
                row = conn.execute("SELECT id FROM clients WHERE phone9=? AND id<>?", (phone9, cid)).fetchone()
                if row:
                    # ignora para não corromper
                    conn.close()
                    continue

            conn.execute("""
              INSERT OR REPLACE INTO clients(id, phone9, name, email, data, updated_at)
              VALUES(?,?,?,?,?,?)
            """, (cid, phone9 or None, name, email, json.dumps(doc, ensure_ascii=False), int(time.time())))

            if phone9:
                conn.execute("INSERT OR REPLACE INTO phone_index(phone9, client_id) VALUES(?,?)", (phone9, cid))

            conn.commit()
            conn.close()

            push_client_change("upsert", {"id": cid, "name": name, "phone": phone9, "email": email})
            applied += 1

    return jsonify({"ok": True, "applied": applied})

# =========================
# RUN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
