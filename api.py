# bridge.py — Sync API <-> TXT (agenda.c) + Clientes + Fotos
# versão reforçada / mais "à prova de bala"
# CORREÇÃO:
# - mantém IDs dos clientes iguais aos do PC
# - nunca tenta "recriar sem id"
# - espera que a API aceite upsert com id inexistente e crie com esse mesmo id

import os
import time
import json
import hashlib
import re
import threading
import mimetypes
import unicodedata

import requests

from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote_plus, urlparse, parse_qs

# =========================
# CONFIG
# =========================
API_BASE = os.environ.get("API_BASE", "https://barbearia-api-ki25.onrender.com").rstrip("/")
BRIDGE_SECRET = (os.environ.get("BRIDGE_SECRET", "neves-12345") or "").strip()
API_ADMIN_TOKEN = (os.environ.get("API_ADMIN_TOKEN", "") or "").strip()

DATA_DIR = os.environ.get("BRIDGE_DATA_DIR", "barbearia_data")
AGENDA_DIR = os.path.join(DATA_DIR, "agenda")
CLIENTS_DIR = os.path.join(DATA_DIR, "clientes")

STATE_FILE = os.path.join(DATA_DIR, "bridge_state.json")
REMOTE_CACHE_FILE = os.path.join(DATA_DIR, "remote_cache.json")

PULL_LIMIT = 300
SLEEP_SEC = 5
RESEED_IF_REMOTE_BOOKINGS_LESS_THAN = 2

HIDE_STATUSES_IN_TXT = {"Cancelado", "Desbloqueado"}

BRIDGE_HTTP_HOST = os.environ.get("BRIDGE_HTTP_HOST", "0.0.0.0")
BRIDGE_HTTP_PORT = int(os.environ.get("BRIDGE_HTTP_PORT", "8765"))

LOCAL_BEFORE_NAME = "antes.jpg"
LOCAL_AFTER_NAME = "depois.jpg"

DEBUG_PHOTOS = int(os.environ.get("DEBUG_PHOTOS", "1") or "1")
DEBUG_CLIENTS = int(os.environ.get("DEBUG_CLIENTS", "1") or "1")

FORCE_PHOTOS = int(os.environ.get("FORCE_PHOTOS", "0") or "0")
FORCE_PUSH_CLIENTS = int(os.environ.get("FORCE_PUSH_CLIENTS", "0") or "0")
FORCE_PUSH_PHOTOS = int(os.environ.get("FORCE_PUSH_PHOTOS", "0") or "0")
FORCE_RELINK_BOOKINGS = int(os.environ.get("FORCE_RELINK_BOOKINGS", "0") or "0")

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(AGENDA_DIR, exist_ok=True)
os.makedirs(CLIENTS_DIR, exist_ok=True)

SESSION = requests.Session()

# =========================
# Helpers
# =========================
def dprint(*args):
    if DEBUG_PHOTOS:
        print(*args, flush=True)

def cprint(*args):
    if DEBUG_CLIENTS:
        print(*args, flush=True)

def jload(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default

def jsave(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()

def http_json(method, url, timeout=30, **kwargs):
    r = SESSION.request(method, url, timeout=timeout, **kwargs)
    if not r.ok:
        body = (r.text or "")[:1000]
        raise RuntimeError(f"[HTTP {r.status_code}] {r.reason} for {r.url}\n{body}")
    return r.json()

def http_get(url, timeout=60, stream=False, **kwargs):
    r = SESSION.get(url, timeout=timeout, stream=stream, **kwargs)
    if not r.ok:
        raise RuntimeError(f"[HTTP {r.status_code}] {r.reason} for {r.url}\n{(r.text or '')[:300]}")
    return r

def norm_phone(p: str) -> str:
    p = (p or "").strip()
    if not p:
        return ""
    digits = re.sub(r"\D+", "", p)
    if len(digits) == 12 and digits.startswith("351"):
        digits = digits[3:]
    return digits

def norm_email(e: str) -> str:
    return (e or "").strip().lower()

def norm_name(n: str) -> str:
    s = (n or "").strip().lower()
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s).strip()
    return s

def strip_empty_fields(d: dict) -> dict:
    out = {}
    for k, v in (d or {}).items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        out[k] = v
    return out

def parse_kv_line(line: str):
    out = {}
    parts = line.strip().split("|")
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip()] = v.strip()
    if "notes" in out:
        try:
            out["notes"] = bytes(out["notes"], "utf-8").decode("unicode_escape")
        except Exception:
            pass
    return out

def escape_notes(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "")

def agenda_file_for_date(date_ymd: str) -> str:
    return os.path.join(AGENDA_DIR, f"{date_ymd}.txt")

def abs_url(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    if u.startswith("http://") or u.startswith("https://"):
        return u
    if u.startswith("/"):
        return API_BASE + u
    return API_BASE + "/" + u

def file_ok(path: str) -> bool:
    try:
        return os.path.exists(path) and os.path.getsize(path) > 0
    except Exception:
        return False

def safe_int_id(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    m = re.match(r"^\s*(\d+)", s)
    if not m:
        return ""
    try:
        return str(int(m.group(1)))
    except Exception:
        return ""

def pad6(cid_int: str) -> str:
    cid = safe_int_id(cid_int)
    if not cid:
        return ""
    return f"{int(cid):06d}"

def slug_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return ""

    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))

    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9_\-]+", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s

def best_folder_name(cid_int: str, name: str) -> str:
    p = pad6(cid_int)
    if not p:
        return ""
    sn = slug_name(name)
    return f"{p}_{sn}" if sn else p

def guess_content_type(path: str) -> str:
    ct, _ = mimetypes.guess_type(path)
    if ct:
        return ct
    return "image/jpeg"

def is_photo_file(path: str) -> bool:
    ext = os.path.splitext(path or "")[1].lower()
    return ext in PHOTO_EXTS

def folder_display_name(folder: str) -> str:
    base = os.path.basename(folder or "")
    m = re.match(r"^\d{1,6}_(.+)$", base)
    if not m:
        return ""
    raw = m.group(1).replace("_", " ").strip()
    raw = re.sub(r"\s+", " ", raw)
    return raw

def choose_local_client_name(folder: str, cliente_data: dict) -> str:
    txt_name = (cliente_data.get("nome") or cliente_data.get("name") or "").strip()
    folder_name = folder_display_name(folder)

    if folder_name and txt_name:
        if norm_name(folder_name) != norm_name(txt_name):
            return folder_name
        return txt_name

    if folder_name:
        return folder_name

    return txt_name

def find_best_photo_in_folder(folder: str, kind: str) -> str:
    if not folder or not os.path.isdir(folder):
        return ""

    try:
        names = os.listdir(folder)
    except Exception:
        return ""

    files = []
    for fn in names:
        full = os.path.join(folder, fn)
        if not os.path.isfile(full):
            continue
        if not is_photo_file(full):
            continue
        files.append((fn, full))

    if not files:
        return ""

    if kind == "before":
        preferred = [
            "antes.jpg", "antes.jpeg", "antes.png", "antes.webp", "antes.heic", "antes.heif",
            "foto_antes.jpg", "foto_antes.jpeg", "foto_antes.png", "foto_antes.webp", "foto_antes.heic", "foto_antes.heif",
            "before.jpg", "before.jpeg", "before.png", "before.webp", "before.heic", "before.heif",
        ]
        keywords = ["antes", "foto_antes", "before"]
    else:
        preferred = [
            "depois.jpg", "depois.jpeg", "depois.png", "depois.webp", "depois.heic", "depois.heif",
            "foto_depois.jpg", "foto_depois.jpeg", "foto_depois.png", "foto_depois.webp", "foto_depois.heic", "foto_depois.heif",
            "after.jpg", "after.jpeg", "after.png", "after.webp", "after.heic", "after.heif",
        ]
        keywords = ["depois", "foto_depois", "after"]

    by_lower = {fn.lower(): full for fn, full in files}
    for p in preferred:
        if p in by_lower:
            return by_lower[p]

    keyword_hits = []
    for fn, full in files:
        fl = fn.lower()
        if any(k in fl for k in keywords):
            keyword_hits.append((fn, full))

    if keyword_hits:
        keyword_hits.sort(key=lambda x: x[0].lower())
        return keyword_hits[0][1]

    files.sort(key=lambda x: x[0].lower())
    return files[0][1]

def resolve_client_photo_paths(folder: str, cliente_data: dict):
    before_rel = (cliente_data.get("foto_antes") or "").strip()
    after_rel = (cliente_data.get("foto_depois") or "").strip()

    before_path = ""
    after_path = ""

    if before_rel:
        p = os.path.join(folder, before_rel)
        if file_ok(p):
            before_path = p

    if after_rel:
        p = os.path.join(folder, after_rel)
        if file_ok(p):
            after_path = p

    if not before_path:
        p = os.path.join(folder, LOCAL_BEFORE_NAME)
        if file_ok(p):
            before_path = p

    if not after_path:
        p = os.path.join(folder, LOCAL_AFTER_NAME)
        if file_ok(p):
            after_path = p

    if not before_path:
        before_path = find_best_photo_in_folder(folder, "before")

    if not after_path:
        after_path = find_best_photo_in_folder(folder, "after")

    return before_path or "", after_path or ""

# =========================
# DEBUG CLIENTES
# =========================
def debug_list_local_clients():
    items = list_clients_from_disk()
    cprint("\n[DEBUG] ===== CLIENTES LIDOS DO DISCO =====")
    cprint(f"[DEBUG] total list_clients_from_disk() = {len(items)}")
    for c in items:
        cprint(
            f"[DEBUG] id={c.get('id','')} "
            f"name='{c.get('name','')}' "
            f"phone='{c.get('phone','')}' "
            f"email='{c.get('email','')}' "
            f"folder='{c.get('folder','')}'"
        )
    cprint("[DEBUG] ===== FIM CLIENTES LIDOS DO DISCO =====\n")

def debug_duplicate_phone_email():
    items = list_clients_from_disk()

    by_phone = {}
    by_email = {}

    for c in items:
        phone = norm_phone(c.get("phone") or "")
        email = norm_email(c.get("email") or "")
        if phone:
            by_phone.setdefault(phone, []).append(c)
        if email:
            by_email.setdefault(email, []).append(c)

    cprint("\n[DEBUG] ===== TELEFONES REPETIDOS =====")
    found = False
    for phone, arr in sorted(by_phone.items()):
        if len(arr) > 1:
            found = True
            cprint(f"[DEBUG] telefone {phone} aparece {len(arr)}x")
            for c in arr:
                cprint(f"        id={c['id']} nome='{c['name']}' pasta='{c['folder']}'")
    if not found:
        cprint("[DEBUG] sem telefones repetidos")

    cprint("\n[DEBUG] ===== EMAILS REPETIDOS =====")
    found = False
    for email, arr in sorted(by_email.items()):
        if len(arr) > 1:
            found = True
            cprint(f"[DEBUG] email {email} aparece {len(arr)}x")
            for c in arr:
                cprint(f"        id={c['id']} nome='{c['name']}' pasta='{c['folder']}'")
    if not found:
        cprint("[DEBUG] sem emails repetidos")

    cprint("[DEBUG] ===== FIM REPETIÇÕES =====\n")

def debug_clients_to_push(local_clients, remote_by_id):
    cprint("\n[DEBUG] ===== CLIENTES QUE A BRIDGE VAI ANALISAR PARA PUSH =====")
    cprint(f"[DEBUG] locais={len(local_clients)} remotos_por_id={len(remote_by_id)}")

    for lc in local_clients:
        cid_int = safe_int_id(lc.get("id") or "")
        remote = remote_by_id.get(cid_int) or {}

        cprint(
            f"[DEBUG] cid={cid_int} "
            f"local_name='{lc.get('name','')}' "
            f"local_phone='{lc.get('phone','')}' "
            f"local_email='{lc.get('email','')}' "
            f"folder='{lc.get('folder','')}' "
            f"remote_exists={'YES' if remote else 'NO'} "
            f"remote_name='{remote.get('name','') if remote else ''}' "
            f"remote_phone='{remote.get('phone','') if remote else ''}' "
            f"remote_email='{remote.get('email','') if remote else ''}'"
        )

    cprint("[DEBUG] ===== FIM CLIENTES PARA PUSH =====\n")

# =========================
# Clientes (ler/escrever cliente.txt)
# =========================
def parse_cliente_file(path: str) -> dict:
    c = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.rstrip("\n").rstrip("\r")
                if not line or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = (k or "").strip().lower()
                v = (v or "").rstrip("\r").strip()
                if k == "notas":
                    v = v.replace("\\n", "\n")
                c[k] = v
    except Exception:
        return {}
    return c

def write_cliente_txt(folder: str, cid_int: str, name: str, phone: str, email: str,
                      before_exists: bool, after_exists: bool, extra: dict = None):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "cliente.txt")

    extra = extra or {}

    cid_padded = pad6(cid_int) or (cid_int or "")
    foto_antes = LOCAL_BEFORE_NAME if before_exists else ""
    foto_depois = LOCAL_AFTER_NAME if after_exists else ""

    profissao = (extra.get("profissao") or "").strip()
    idade = (extra.get("idade") or "").strip()
    notas = (extra.get("notas") or "").replace("\r", "").replace("\n", "\\n")

    lines = []
    lines.append(f"id={cid_padded}")
    lines.append(f"nome={(name or '').strip()}")
    lines.append(f"telefone={norm_phone(phone or '')}")
    lines.append(f"email={norm_email(email or '')}")
    lines.append(f"profissao={profissao}")
    lines.append(f"idade={idade}")
    lines.append(f"notas={notas}")
    lines.append(f"foto_antes={foto_antes}")
    lines.append(f"foto_depois={foto_depois}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def fix_local_client_names_from_folders(verbose=True):
    fixed = 0

    try:
        for entry in os.listdir(CLIENTS_DIR):
            folder = os.path.join(CLIENTS_DIR, entry)
            if not os.path.isdir(folder):
                continue

            cliente_path = os.path.join(folder, "cliente.txt")
            c = parse_cliente_file(cliente_path) if os.path.exists(cliente_path) else {}

            cid_int = safe_int_id(c.get("id") or "") or safe_int_id(entry)
            if not cid_int:
                continue

            folder_name = folder_display_name(folder)
            if not folder_name:
                continue

            old_name = (c.get("nome") or "").strip()
            chosen_name = choose_local_client_name(folder, c)

            before_path, after_path = resolve_client_photo_paths(folder, c)

            if norm_name(old_name) == norm_name(chosen_name) and os.path.exists(cliente_path):
                continue

            if verbose:
                cprint(
                    f"[DEBUG] FIX_NAME cid={cid_int} pasta='{os.path.basename(folder)}' "
                    f"old_nome='{old_name}' -> new_nome='{chosen_name}'"
                )

            write_cliente_txt(
                folder=folder,
                cid_int=cid_int,
                name=chosen_name,
                phone=norm_phone(c.get("telefone") or ""),
                email=norm_email(c.get("email") or ""),
                before_exists=file_ok(before_path),
                after_exists=file_ok(after_path),
                extra={
                    "profissao": c.get("profissao") or "",
                    "idade": c.get("idade") or "",
                    "notas": c.get("notas") or "",
                },
            )
            fixed += 1
    except Exception as e:
        cprint(f"[DEBUG] fix_local_client_names_from_folders erro: {e}")

    return fixed

def _folder_score(cid_int: str, folder: str, c: dict) -> int:
    score = 0

    cliente_path = os.path.join(folder, "cliente.txt")
    if os.path.exists(cliente_path):
        score += 100

    before_path, after_path = resolve_client_photo_paths(folder, c)
    if file_ok(before_path):
        score += 300
    if file_ok(after_path):
        score += 300

    folder_name = os.path.basename(folder)
    if folder_name.startswith((pad6(cid_int) or "") + "_"):
        score += 100

    expected = best_folder_name(cid_int, choose_local_client_name(folder, c))
    if expected and folder_name == expected:
        score += 200

    try:
        score += int(os.path.getmtime(folder))
    except Exception:
        pass

    return score

def build_client_folder_map():
    grouped = {}
    idx_tel, idx_mail, idx_nome = {}, {}, {}

    try:
        for entry in os.listdir(CLIENTS_DIR):
            folder = os.path.join(CLIENTS_DIR, entry)
            if not os.path.isdir(folder):
                continue

            path = os.path.join(folder, "cliente.txt")
            c = parse_cliente_file(path) if os.path.exists(path) else {}

            cid_int = safe_int_id(c.get("id") or "")
            if not cid_int:
                cid_int = safe_int_id(entry)

            if not cid_int:
                continue

            item = {
                "folder": folder,
                "data": c,
                "score": _folder_score(cid_int, folder, c),
            }
            grouped.setdefault(cid_int, []).append(item)

        cid_to_folder = {}
        canonical_by_id = {}

        for cid_int, candidates in grouped.items():
            candidates.sort(key=lambda x: x["score"], reverse=True)
            best = candidates[0]
            cid_to_folder[cid_int] = best["folder"]

            merged = dict(best["data"])
            merged["id"] = cid_int

            best_folder = best["folder"]
            before_path, after_path = resolve_client_photo_paths(best_folder, merged)

            if not file_ok(before_path) or not file_ok(after_path):
                for cand in candidates[1:]:
                    cand_folder = cand["folder"]
                    cand_before, cand_after = resolve_client_photo_paths(cand_folder, cand["data"])

                    if not file_ok(before_path) and file_ok(cand_before):
                        before_path = cand_before
                    if not file_ok(after_path) and file_ok(cand_after):
                        after_path = cand_after

            merged["__before_path"] = before_path if file_ok(before_path) else ""
            merged["__after_path"] = after_path if file_ok(after_path) else ""
            merged["__folder"] = best_folder
            merged["nome"] = choose_local_client_name(best_folder, merged)

            canonical_by_id[cid_int] = merged

        for cid_int, c in canonical_by_id.items():
            tel = norm_phone(c.get("telefone", ""))
            mail = norm_email(c.get("email", ""))
            nome = (c.get("nome", "") or "").strip()
            nome_norm = norm_name(nome)

            item = {
                **c,
                "id": cid_int,
                "name": nome,
                "phone": tel,
                "email_norm": mail,
                "name_norm": nome_norm,
            }

            if tel:
                idx_tel[tel] = item
            if mail:
                idx_mail[mail] = item
            if nome_norm:
                idx_nome.setdefault(nome_norm, []).append(item)

        return cid_to_folder, idx_tel, idx_mail, idx_nome

    except Exception:
        return {}, {}, {}, {}

def list_clients_from_disk():
    items = []
    cid_to_folder, _, _, _ = build_client_folder_map()

    seen_ids = set()

    for cid_int, folder in cid_to_folder.items():
        if cid_int in seen_ids:
            continue
        seen_ids.add(cid_int)

        path = os.path.join(folder, "cliente.txt")
        c = parse_cliente_file(path) if os.path.exists(path) else {}

        name = choose_local_client_name(folder, c)
        phone = norm_phone(c.get("telefone") or "")
        email = norm_email(c.get("email") or "")

        before_path, after_path = resolve_client_photo_paths(folder, c)

        items.append({
            "id": cid_int,
            "id_padded": pad6(cid_int),
            "name": name,
            "phone": phone,
            "email": email,
            "notes": c.get("notas", "") or "",
            "profession": c.get("profissao", "") or "",
            "age": c.get("idade", "") or "",
            "has_before": bool(before_path and os.path.exists(before_path)),
            "has_after": bool(after_path and os.path.exists(after_path)),
            "folder": os.path.basename(folder),
            "folder_path": folder,
            "before_path": before_path if before_path and os.path.exists(before_path) else "",
            "after_path": after_path if after_path and os.path.exists(after_path) else "",
        })

    def _sortkey(x):
        try:
            return (x.get("name", ""), int(x.get("id", "0") or "0"))
        except Exception:
            return (x.get("name", ""), 999999999)

    items.sort(key=_sortkey)
    return items

def _all_indexed_clients(idx_tel, idx_mail, idx_nome):
    out = {}
    for item in idx_tel.values():
        cid = safe_int_id(item.get("id") or "")
        if cid:
            out[cid] = item
    for item in idx_mail.values():
        cid = safe_int_id(item.get("id") or "")
        if cid:
            out[cid] = item
    for arr in idx_nome.values():
        for item in arr:
            cid = safe_int_id(item.get("id") or "")
            if cid:
                out[cid] = item
    return out

def match_client_from_indexes(name, phone, email, idx_tel, idx_mail, idx_nome):
    phone = norm_phone(phone or "")
    email = norm_email(email or "")
    name_norm = norm_name(name or "")

    if email and email in idx_mail:
        return idx_mail[email]

    if phone and phone in idx_tel:
        return idx_tel[phone]

    if name_norm:
        hits = idx_nome.get(name_norm, [])
        if len(hits) == 1:
            return hits[0]

        if hits:
            def _score(x):
                score = 0
                if x.get("phone"):
                    score += 10
                if x.get("email_norm"):
                    score += 10
                if x.get("__before_path"):
                    score += 5
                if x.get("__after_path"):
                    score += 5
                return score
            hits = sorted(hits, key=_score, reverse=True)
            return hits[0]

    return None

def enrich_booking_from_clientes(b: dict, idx_tel, idx_mail, idx_nome):
    name = (b.get("name") or b.get("client") or "").strip()
    phone = norm_phone(b.get("phone") or b.get("telefone") or "")
    email = norm_email(b.get("email") or "")
    client_id = safe_int_id(b.get("client_id") or "")

    c = None

    if client_id:
        all_clients = _all_indexed_clients(idx_tel, idx_mail, idx_nome)
        c = all_clients.get(client_id)

    if not c:
        c = match_client_from_indexes(name, phone, email, idx_tel, idx_mail, idx_nome)

    if not c:
        return

    cid_int = safe_int_id(c.get("id") or "")
    cname = (c.get("nome") or c.get("name") or "").strip()
    cphone = norm_phone(c.get("telefone") or c.get("phone") or "")
    cemail = norm_email(c.get("email") or c.get("email_norm") or "")

    if cid_int:
        b["client_id"] = cid_int

    if cname:
        b["name"] = cname
        b["client"] = cname

    if cphone:
        b["phone"] = cphone

    if cemail:
        b["email"] = cemail

# =========================
# TXT formatting (agenda)
# =========================
def booking_to_txt_line(b: dict) -> str:
    notes = escape_notes(b.get("notes", ""))
    client = b.get("name", "") or b.get("client", "") or ""
    phone = norm_phone(b.get("phone", ""))
    email = norm_email(b.get("email", ""))
    cid_int = safe_int_id(b.get("client_id", "")) or (b.get("client_id", "") or "")

    return (
        f"id={b.get('id', '')}"
        f"|time={b.get('time', '')}"
        f"|dur={b.get('dur', 30)}"
        f"|client_id={cid_int}"
        f"|client={client}"
        f"|phone={phone}"
        f"|email={email}"
        f"|service={b.get('service', '')}"
        f"|barber={b.get('barber', '')}"
        f"|status={b.get('status', 'Marcado')}"
        f"|notes={notes}"
    )

# =========================
# State
# =========================
def load_state():
    return jload(STATE_FILE, {
        "cursor": 0,
        "pushed_hash": {},
        "known_ids": [],
        "seeded_once": False,
        "photo_cache": {},
        "client_push_cache": {},
        "photo_push_cache": {},
        "booking_link_cache": {},
    })

def load_remote_cache():
    return jload(REMOTE_CACHE_FILE, {})

# =========================
# API calls
# =========================
def api_info():
    return http_json("GET", f"{API_BASE}/", timeout=15)

def api_pull(cursor: int, limit: int):
    url = f"{API_BASE}/pull"
    params = {"secret": BRIDGE_SECRET, "cursor": cursor, "limit": limit}
    return http_json("GET", url, params=params)

def api_sync(changes: list):
    url = f"{API_BASE}/sync"
    params = {"secret": BRIDGE_SECRET}
    return http_json("POST", url, params=params, json={"changes": changes})

def api_replace_all(items: list):
    url = f"{API_BASE}/replace_all"
    params = {"secret": BRIDGE_SECRET}
    return http_json("POST", url, params=params, json={"items": items})

def api_bridge_clients_auto():
    candidates = [
        "/bridge/clients",
        "/bridge_clients",
        "/clients",
        "/clients/list",
        "/bridge/clients/list",
    ]

    last_err = None
    for path in candidates:
        url = f"{API_BASE}{path}"
        try:
            params = {"secret": BRIDGE_SECRET}
            dprint(f"[photos] a tentar endpoint: {url}?secret=***")
            data = http_json("GET", url, params=params, timeout=25)

            if isinstance(data, dict):
                if "items" in data and isinstance(data.get("items"), list):
                    dprint(f"[photos] endpoint OK: {path} (items={len(data.get('items') or [])})")
                    return data
                if "clients" in data and isinstance(data.get("clients"), list):
                    items = data.get("clients") or []
                    dprint(f"[photos] endpoint OK: {path} (clients={len(items)}) -> normalizado para items")
                    return {"items": items}

            last_err = RuntimeError(f"endpoint {path} respondeu mas sem formato esperado")
        except Exception as e:
            last_err = e
            msg = str(e)
            if "HTTP 404" in msg:
                dprint(f"[photos] endpoint 404: {path}")
            else:
                dprint(f"[photos] endpoint falhou: {path} -> {e}")

    raise last_err or RuntimeError("nenhum endpoint de clientes disponível")

def api_admin_clients_list():
    if not API_ADMIN_TOKEN:
        raise RuntimeError("API_ADMIN_TOKEN não definido")
    url = f"{API_BASE}/admin/clients"
    headers = {"X-Admin-Token": API_ADMIN_TOKEN}
    data = http_json("GET", url, headers=headers, timeout=25)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise RuntimeError("admin /admin/clients sem items")
    return data

def api_admin_client_upsert(item: dict):
    if not API_ADMIN_TOKEN:
        raise RuntimeError("API_ADMIN_TOKEN não definido")
    url = f"{API_BASE}/admin/clients"
    headers = {"X-Admin-Token": API_ADMIN_TOKEN}
    return http_json("POST", url, headers=headers, json=item, timeout=30)

def api_admin_client_upload_photo(cid_int: str, kind: str, local_path: str):
    if not API_ADMIN_TOKEN:
        raise RuntimeError("API_ADMIN_TOKEN não definido")

    cid_int = safe_int_id(cid_int)
    if not cid_int:
        raise RuntimeError("cid inválido para upload foto")

    url = f"{API_BASE}/admin/client/{cid_int}/photo"
    headers = {"X-Admin-Token": API_ADMIN_TOKEN}

    filename = os.path.basename(local_path)
    content_type = guess_content_type(local_path)

    with open(local_path, "rb") as f:
        files = {
            "file": (filename, f, content_type),
        }
        data = {
            "kind": kind,
        }
        r = SESSION.post(url, headers=headers, data=data, files=files, timeout=120)
        if not r.ok:
            raise RuntimeError(f"[HTTP {r.status_code}] {r.reason} for {r.url}\n{(r.text or '')[:1000]}")
        return r.json()

def api_admin_bookings_list(date_=None, barber=None):
    if not API_ADMIN_TOKEN:
        raise RuntimeError("API_ADMIN_TOKEN não definido")
    url = f"{API_BASE}/admin/bookings"
    headers = {"X-Admin-Token": API_ADMIN_TOKEN}
    params = {}
    if date_:
        params["date"] = date_
    if barber:
        params["barber"] = barber
    data = http_json("GET", url, headers=headers, params=params, timeout=30)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise RuntimeError("admin /admin/bookings sem items")
    return data

def api_admin_link_booking_client(bid: str, cid_int: str):
    if not API_ADMIN_TOKEN:
        raise RuntimeError("API_ADMIN_TOKEN não definido")
    bid = (bid or "").strip()
    cid_int = safe_int_id(cid_int)
    if not bid or not cid_int:
        raise RuntimeError("bid/cid inválido")
    url = f"{API_BASE}/admin/booking/{bid}/link-client"
    headers = {"X-Admin-Token": API_ADMIN_TOKEN}
    return http_json("POST", url, headers=headers, json={"client_id": cid_int}, timeout=30)

# =========================
# Remote -> local TXT (agenda)
# =========================
def apply_remote_changes_to_cache(remote_cache: dict, items: list, idx_tel, idx_mail, idx_nome):
    for ev in items:
        op = ev.get("op")
        payload = ev.get("payload") or {}
        bid = payload.get("id")
        if not bid:
            continue

        if op == "delete":
            remote_cache.pop(bid, None)
            continue

        cur = remote_cache.get(bid, {})
        cur.update(payload)
        enrich_booking_from_clientes(cur, idx_tel, idx_mail, idx_nome)
        remote_cache[bid] = cur

def write_agenda_txt_from_cache(remote_cache: dict):
    by_date = {}
    for b in remote_cache.values():
        st = (b.get("status") or "").strip()
        if st in HIDE_STATUSES_IN_TXT:
            continue

        date = (b.get("date") or "").strip()
        time_ = (b.get("time") or "").strip()
        if not date or not time_:
            continue

        by_date.setdefault(date, []).append(b)

    for date, items in by_date.items():
        items.sort(key=lambda x: (x.get("time", ""), x.get("barber", "")))
        lines = [booking_to_txt_line(b) for b in items]
        path = agenda_file_for_date(date)
        content = "\n".join(lines) + "\n"

        old = ""
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    old = f.read()
            except Exception:
                old = ""

        if old != content:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

    existing_files = [fn for fn in os.listdir(AGENDA_DIR) if fn.endswith(".txt")]
    valid_dates = set(by_date.keys())
    for fn in existing_files:
        date = fn[:-4]
        if date not in valid_dates:
            try:
                os.remove(os.path.join(AGENDA_DIR, fn))
            except Exception:
                pass

# =========================
# Local TXT -> items (agenda)
# =========================
def read_all_local_agenda_items():
    out = []
    if not os.path.isdir(AGENDA_DIR):
        return out

    for fn in os.listdir(AGENDA_DIR):
        if not fn.endswith(".txt"):
            continue
        date = fn[:-4]
        path = os.path.join(AGENDA_DIR, fn)

        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    kv = parse_kv_line(line)
                    bid = (kv.get("id", "") or "").strip()
                    if not bid:
                        continue

                    client = unquote_plus(kv.get("client", "") or "")
                    service = unquote_plus(kv.get("service", "") or "")
                    barber = unquote_plus(kv.get("barber", "") or "")
                    phone = norm_phone(unquote_plus(kv.get("phone", "") or ""))
                    email = norm_email(unquote_plus(kv.get("email", "") or ""))

                    b = {
                        "id": bid,
                        "date": date,
                        "time": (kv.get("time", "") or "")[:5],
                        "dur": int(float(kv.get("dur", "30") or "30")),
                        "barber": barber,
                        "service": service,
                        "status": (kv.get("status", "Marcado") or "Marcado"),
                        "notes": (kv.get("notes") or ""),
                        "client_id": safe_int_id(kv.get("client_id") or "") or (kv.get("client_id") or ""),
                        "client": client,
                        "name": client,
                        "phone": phone,
                        "email": email,
                    }
                    out.append(b)
        except Exception:
            continue

    return out

def compute_booking_hash(b: dict) -> str:
    key = json.dumps({
        "id": b.get("id", ""),
        "date": b.get("date", ""),
        "time": b.get("time", ""),
        "dur": b.get("dur", 30),
        "barber": b.get("barber", ""),
        "service": b.get("service", ""),
        "status": b.get("status", ""),
        "notes": b.get("notes", ""),
        "client_id": safe_int_id(b.get("client_id", "")) or b.get("client_id", ""),
        "client": b.get("client", ""),
        "name": b.get("name", ""),
        "phone": norm_phone(b.get("phone", "")),
        "email": norm_email(b.get("email", "")),
    }, ensure_ascii=False, sort_keys=True)
    return sha1(key)

# =========================
# Push local upserts + deletes
# =========================
def push_local_changes(state: dict, idx_tel, idx_mail, idx_nome):
    local_items = read_all_local_agenda_items()
    for b in local_items:
        enrich_booking_from_clientes(b, idx_tel, idx_mail, idx_nome)

    local_by_id = {b["id"]: b for b in local_items}
    local_ids = set(local_by_id.keys())

    known_ids = set(state.get("known_ids") or [])
    deleted_ids = sorted(list(known_ids - local_ids))

    changes = []
    pushed = 0

    for bid in deleted_ids:
        changes.append({"op": "delete", "payload": {"id": bid}})
        state["pushed_hash"].pop(bid, None)
        pushed += 1
        if len(changes) >= 200:
            api_sync(changes)
            changes = []

    for bid, b in local_by_id.items():
        b = strip_empty_fields(b)
        h = compute_booking_hash(b)
        if state["pushed_hash"].get(bid) == h:
            continue

        changes.append({"op": "upsert", "payload": b})
        state["pushed_hash"][bid] = h
        pushed += 1

        if len(changes) >= 200:
            api_sync(changes)
            changes = []

    if changes:
        api_sync(changes)

    state["known_ids"] = sorted(list(local_ids))
    return pushed

# =========================
# Seed (TXT -> API)
# =========================
def seed_remote_from_txt(remote_cache: dict, state: dict, idx_tel, idx_mail, idx_nome):
    items = read_all_local_agenda_items()
    for b in items:
        enrich_booking_from_clientes(b, idx_tel, idx_mail, idx_nome)

    resp = api_replace_all(items)

    remote_cache.clear()
    for b in items:
        remote_cache[b["id"]] = b

    state["known_ids"] = sorted(list({b["id"] for b in items}))

    jsave(REMOTE_CACHE_FILE, remote_cache)
    write_agenda_txt_from_cache(remote_cache)
    return resp, len(items)

# =========================
# Fotos: API -> Disco do PC
# =========================
def update_cliente_txt_photo_fields(folder: str, before_exists: bool, after_exists: bool):
    path = os.path.join(folder, "cliente.txt")
    c = parse_cliente_file(path) if os.path.exists(path) else {}

    new_before = LOCAL_BEFORE_NAME if before_exists else ""
    new_after = LOCAL_AFTER_NAME if after_exists else ""

    old_before = (c.get("foto_antes", "") or "").strip()
    old_after = (c.get("foto_depois", "") or "").strip()

    if old_before == new_before and old_after == new_after and os.path.exists(path):
        return False

    cid_int = safe_int_id(c.get("id", "")) or safe_int_id(os.path.basename(folder)) or ""
    cid_padded = pad6(cid_int) if cid_int else (c.get("id", "") or "")

    nome = choose_local_client_name(folder, c)
    tel = c.get("telefone", "")
    mail = c.get("email", "")
    prof = c.get("profissao", "")
    idade = c.get("idade", "")
    notas = c.get("notas", "")

    lines = []
    lines.append(f"id={cid_padded}")
    lines.append(f"nome={nome}")
    lines.append(f"telefone={norm_phone(tel)}")
    lines.append(f"email={norm_email(mail)}")
    lines.append(f"profissao={prof}")
    lines.append(f"idade={idade}")
    lines.append(f"notas={(notas or '').replace(chr(10), '\\n')}")
    lines.append(f"foto_antes={new_before}")
    lines.append(f"foto_depois={new_after}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    dprint(f"[photos] cliente.txt atualizado: {folder}")
    dprint(f"         foto_antes: '{old_before}' -> '{new_before}'")
    dprint(f"         foto_depois: '{old_after}' -> '{new_after}'")
    return True

def download_to_path(url: str, out_path: str):
    r = http_get(url, timeout=60, stream=True)
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)
    os.replace(tmp, out_path)

def fix_local_photo_fields_all_clients(verbose=True):
    cid_to_folder, _, _, _ = build_client_folder_map()
    fixed = 0

    for cid_int, folder in cid_to_folder.items():
        cliente_path = os.path.join(folder, "cliente.txt")
        c = parse_cliente_file(cliente_path) if os.path.exists(cliente_path) else {}

        before_path, after_path = resolve_client_photo_paths(folder, c)

        before_exists = file_ok(before_path)
        after_exists = file_ok(after_path)

        if verbose:
            dprint(f"[photos] local check cid={cid_int} folder={os.path.basename(folder)}")
            dprint(f"         before_path={before_path} ok={before_exists}")
            dprint(f"         after_path ={after_path} ok={after_exists}")

        if update_cliente_txt_photo_fields(folder, before_exists=before_exists, after_exists=after_exists):
            fixed += 1

    return fixed

def _extract_photo_urls(client_dict: dict):
    before = (
        client_dict.get("photo_before_url")
        or client_dict.get("foto_antes_url")
        or client_dict.get("before_url")
        or ""
    )
    after = (
        client_dict.get("photo_after_url")
        or client_dict.get("foto_depois_url")
        or client_dict.get("after_url")
        or ""
    )
    return abs_url(before), abs_url(after)

def ensure_client_on_disk(cid_int: str, c: dict, cid_to_folder: dict):
    cid_int = safe_int_id(cid_int)
    if not cid_int:
        return None

    if cid_int in cid_to_folder and os.path.isdir(cid_to_folder[cid_int]):
        folder = cid_to_folder[cid_int]
    else:
        name = (c.get("name") or "").strip()
        folder_name = best_folder_name(cid_int, name) or pad6(cid_int) or cid_int
        folder = os.path.join(CLIENTS_DIR, folder_name)
        os.makedirs(folder, exist_ok=True)
        cid_to_folder[cid_int] = folder

    existing = parse_cliente_file(os.path.join(folder, "cliente.txt"))
    before_path, after_path = resolve_client_photo_paths(folder, existing)

    final_name = (c.get("name") or "").strip() or choose_local_client_name(folder, existing)

    extra = {
        "profissao": (c.get("profession") or existing.get("profissao") or "").strip(),
        "idade": (c.get("age") or existing.get("idade") or "").strip(),
        "notas": (c.get("notes") or existing.get("notas") or "").strip(),
    }

    write_cliente_txt(
        folder=folder,
        cid_int=cid_int,
        name=final_name,
        phone=norm_phone(c.get("phone") or existing.get("telefone") or ""),
        email=norm_email(c.get("email") or existing.get("email") or ""),
        before_exists=file_ok(before_path),
        after_exists=file_ok(after_path),
        extra=extra,
    )

    return folder

def sync_photos_from_api(state: dict):
    dprint("[photos] ===== INICIO SYNC FOTOS API -> PC =====")
    dprint(f"[photos] DEBUG_PHOTOS={DEBUG_PHOTOS} FORCE_PHOTOS={FORCE_PHOTOS}")
    dprint(f"[photos] API_BASE={API_BASE}")

    cid_to_folder, _, _, _ = build_client_folder_map()
    cache = state.get("photo_cache") or {}
    pulled = 0

    try:
        data = api_bridge_clients_auto()
        items = data.get("items") or []
        dprint(f"[photos] API (bridge clients) devolveu items={len(items)}")
    except Exception as e1:
        dprint(f"[photos] API bridge clients falhou: {e1}")
        try:
            data = api_admin_clients_list()
            items = data.get("items") or []
            dprint(f"[photos] API (admin fallback) devolveu items={len(items)}")
        except Exception as e2:
            dprint(f"[photos] fallback admin também falhou: {e2}")
            fixed = fix_local_photo_fields_all_clients(verbose=True)
            dprint(f"[photos] corrigidos localmente: {fixed}")
            dprint("[photos] ===== FIM SYNC FOTOS API -> PC (API falhou) =====")
            return 0

    for c in items:
        cid_int = safe_int_id(c.get("id") or "")
        if not cid_int:
            continue

        folder = ensure_client_on_disk(cid_int, c, cid_to_folder)
        if not folder:
            continue

        before_url, after_url = _extract_photo_urls(c)

        before_path = os.path.join(folder, LOCAL_BEFORE_NAME)
        after_path = os.path.join(folder, LOCAL_AFTER_NAME)

        dprint(f"[photos] cid={cid_int} pasta={os.path.basename(folder)}")
        dprint(f"         before_url='{before_url}'")
        dprint(f"         after_url ='{after_url}'")

        if before_url:
            key = f"{cid_int}:before"
            exists_ok = file_ok(before_path)
            cache_match = (cache.get(key) == before_url)
            need = bool(FORCE_PHOTOS or (not cache_match) or (not exists_ok))
            dprint(f"         before decision: need={need} exists_ok={exists_ok} cache_match={cache_match}")
            if need:
                try:
                    dprint(f"         a descarregar BEFORE -> {before_path}")
                    download_to_path(before_url, before_path)
                    if file_ok(before_path):
                        cache[key] = before_url
                        pulled += 1
                        dprint("         OK download BEFORE")
                except Exception as e:
                    dprint(f"         ERRO download BEFORE: {e}")

        if after_url:
            key = f"{cid_int}:after"
            exists_ok = file_ok(after_path)
            cache_match = (cache.get(key) == after_url)
            need = bool(FORCE_PHOTOS or (not cache_match) or (not exists_ok))
            dprint(f"         after decision: need={need} exists_ok={exists_ok} cache_match={cache_match}")
            if need:
                try:
                    dprint(f"         a descarregar AFTER -> {after_path}")
                    download_to_path(after_url, after_path)
                    if file_ok(after_path):
                        cache[key] = after_url
                        pulled += 1
                        dprint("         OK download AFTER")
                except Exception as e:
                    dprint(f"         ERRO download AFTER: {e}")

        update_cliente_txt_photo_fields(
            folder,
            before_exists=file_ok(before_path),
            after_exists=file_ok(after_path),
        )

    fixed_all = fix_local_photo_fields_all_clients(verbose=True)
    dprint(f"[photos] corrigidos localmente (scan todos): {fixed_all}")

    state["photo_cache"] = cache
    dprint(f"[photos] ===== FIM SYNC FOTOS API -> PC (pulled={pulled}) =====")
    return pulled

# =========================
# PC -> API clientes/fotos
# =========================
def compute_client_hash(local_client: dict) -> str:
    payload = {
        "id": safe_int_id(local_client.get("id", "")),
        "name": (local_client.get("name") or "").strip(),
        "phone": norm_phone(local_client.get("phone") or ""),
        "email": norm_email(local_client.get("email") or ""),
        "notes": (local_client.get("notes") or "").strip(),
        "profession": (local_client.get("profession") or "").strip(),
        "age": (local_client.get("age") or "").strip(),
        "has_before": bool(local_client.get("has_before")),
        "has_after": bool(local_client.get("has_after")),
    }
    return sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True))

def compute_file_hash(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def build_remote_client_index():
    by_id = {}
    try:
        data = api_admin_clients_list()
        for c in data.get("items") or []:
            cid = safe_int_id(c.get("id") or "")
            if cid:
                by_id[cid] = c
    except Exception as e:
        dprint(f"[push-clients] aviso: não consegui ler admin clients: {e}")
    return by_id

def sync_clients_from_disk_to_api(state: dict):
    if not API_ADMIN_TOKEN:
        dprint("[push-clients] API_ADMIN_TOKEN não definido, salto sync clientes -> API")
        return 0

    local_clients = list_clients_from_disk()
    remote_by_id = build_remote_client_index()
    debug_clients_to_push(local_clients, remote_by_id)

    cache = state.get("client_push_cache") or {}
    pushed = 0

    for lc in local_clients:
        cid_int = safe_int_id(lc.get("id") or "")
        if not cid_int:
            continue

        h = compute_client_hash(lc)
        remote = remote_by_id.get(cid_int) or {}

        remote_name = (remote.get("name") or "").strip()
        remote_phone = norm_phone(remote.get("phone") or "")
        remote_email = norm_email(remote.get("email") or "")

        need = False
        reason = []

        if FORCE_PUSH_CLIENTS:
            need = True
            reason.append("FORCE_PUSH_CLIENTS")
        if cache.get(cid_int) != h:
            need = True
            reason.append("hash_changed")
        if not remote:
            need = True
            reason.append("remote_missing")
        if remote_name != (lc.get("name") or "").strip():
            need = True
            reason.append("name_diff")
        if remote_phone != norm_phone(lc.get("phone") or ""):
            need = True
            reason.append("phone_diff")
        if remote_email != norm_email(lc.get("email") or ""):
            need = True
            reason.append("email_diff")

        # IMPORTANTE:
        # Para manter IDs iguais ao PC, enviamos sempre o id local.
        payload = {
            "id": cid_int,
            "name": (lc.get("name") or "").strip(),
            "phone": norm_phone(lc.get("phone") or ""),
            "email": norm_email(lc.get("email") or ""),
            "notes": (lc.get("notes") or "").strip(),
            "profession": (lc.get("profession") or "").strip(),
            "age": (lc.get("age") or "").strip(),
        }
        payload = strip_empty_fields(payload)

        cprint(
            f"[DEBUG] PUSH_CHECK cid={cid_int} "
            f"need={'YES' if need else 'NO'} "
            f"reason={','.join(reason) if reason else 'no_change'} "
            f"name='{payload.get('name','')}' "
            f"phone='{payload.get('phone','')}' "
            f"email='{payload.get('email','')}'"
        )

        if not need:
            continue

        try:
            dprint(f"[push-clients] upsert cid={cid_int} name='{payload.get('name', '')}'")
            resp = api_admin_client_upsert(payload)
            item = (resp or {}).get("item") or {}

            returned_id = safe_int_id(item.get("id") or resp.get("id") or "")
            if returned_id and returned_id != cid_int:
                raise RuntimeError(
                    f"API devolveu id diferente do PC: local={cid_int} remote={returned_id}. "
                    f"A API tem de criar/atualizar com o mesmo id."
                )

            cache[cid_int] = h
            pushed += 1

        except Exception as e:
            dprint(f"[push-clients] ERRO cid={cid_int}: {e}")

    state["client_push_cache"] = cache

    try:
        after = api_admin_clients_list()
        cprint(f"[DEBUG] API clientes depois do push: {len(after.get('items') or [])}")
    except Exception as e:
        cprint(f"[DEBUG] não consegui ler API depois do push: {e}")

    return pushed

def sync_photos_from_disk_to_api(state: dict):
    if not API_ADMIN_TOKEN:
        dprint("[push-photos] API_ADMIN_TOKEN não definido, salto sync fotos -> API")
        return 0

    local_clients = list_clients_from_disk()
    remote_by_id = build_remote_client_index()
    cache = state.get("photo_push_cache") or {}

    pushed = 0

    for lc in local_clients:
        cid_int = safe_int_id(lc.get("id") or "")
        if not cid_int:
            continue

        remote = remote_by_id.get(cid_int) or {}

        tasks = [
            ("before", lc.get("before_path") or "", remote.get("photo_before_url") or remote.get("before_url") or ""),
            ("after", lc.get("after_path") or "", remote.get("photo_after_url") or remote.get("after_url") or ""),
        ]

        for kind, local_path, remote_url in tasks:
            if not local_path or not file_ok(local_path):
                continue

            key = f"{cid_int}:{kind}"

            try:
                fh = compute_file_hash(local_path)
            except Exception as e:
                dprint(f"[push-photos] ERRO hash {local_path}: {e}")
                continue

            need = False
            if FORCE_PUSH_PHOTOS:
                need = True
            elif cache.get(key) != fh:
                need = True
            elif not (remote_url or "").strip():
                need = True

            if not need:
                continue

            try:
                dprint(f"[push-photos] upload cid={cid_int} kind={kind} file={local_path}")
                api_admin_client_upload_photo(cid_int, kind, local_path)
                cache[key] = fh
                pushed += 1
            except Exception as e:
                dprint(f"[push-photos] ERRO cid={cid_int} kind={kind}: {e}")

    state["photo_push_cache"] = cache
    return pushed

# =========================
# RE-LINK bookings -> clientes na API
# =========================
def compute_link_signature(bid: str, cid_int: str) -> str:
    return sha1(json.dumps({"bid": bid, "cid": safe_int_id(cid_int)}, sort_keys=True))

def relink_remote_bookings_with_clients(state: dict, idx_tel, idx_mail, idx_nome):
    if not API_ADMIN_TOKEN:
        dprint("[relink] API_ADMIN_TOKEN não definido, salto relink")
        return 0

    try:
        data = api_admin_bookings_list()
        items = data.get("items") or []
    except Exception as e:
        dprint(f"[relink] não consegui ler bookings admin: {e}")
        return 0

    cache = state.get("booking_link_cache") or {}
    fixed = 0

    for b in items:
        bid = (b.get("id") or "").strip()
        if not bid:
            continue

        status = (b.get("status") or "").strip()
        if status in ("Cancelado", "Desbloqueado"):
            continue

        current_cid = safe_int_id(b.get("client_id") or "")
        name = (b.get("name") or b.get("client") or "").strip()
        phone = norm_phone(b.get("phone") or "")
        email = norm_email(b.get("email") or "")

        matched = match_client_from_indexes(name, phone, email, idx_tel, idx_mail, idx_nome)
        if not matched:
            continue

        target_cid = safe_int_id(matched.get("id") or "")
        if not target_cid:
            continue

        sig = compute_link_signature(bid, target_cid)

        need = False
        if FORCE_RELINK_BOOKINGS:
            need = True
        elif current_cid != target_cid:
            need = True
        elif cache.get(bid) != sig:
            need = True

        if not need:
            continue

        try:
            api_admin_link_booking_client(bid, target_cid)
            cache[bid] = sig
            fixed += 1
            dprint(f"[relink] booking {bid} -> client {target_cid} OK")
        except Exception as e:
            dprint(f"[relink] ERRO booking {bid} -> client {target_cid}: {e}")

    state["booking_link_cache"] = cache
    return fixed

# =========================
# Bridge HTTP server
# =========================
class BridgeHandler(BaseHTTPRequestHandler):
    def _send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            u = urlparse(self.path)
            if u.path != "/clients":
                return self._send_json({"ok": False, "error": "not found"}, 404)

            qs = parse_qs(u.query)
            secret = (qs.get("secret", [""])[0] or "").strip()
            if secret != BRIDGE_SECRET:
                return self._send_json({"ok": False, "error": "unauthorized"}, 401)

            items = list_clients_from_disk()
            return self._send_json({"ok": True, "items": items})
        except Exception as e:
            return self._send_json({"ok": False, "error": str(e)}, 500)

    def log_message(self, fmt, *args):
        return

def start_http_server():
    srv = HTTPServer((BRIDGE_HTTP_HOST, BRIDGE_HTTP_PORT), BridgeHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    print(f"[bridge-http] http://{BRIDGE_HTTP_HOST}:{BRIDGE_HTTP_PORT}/clients?secret=***", flush=True)
    return srv

# =========================
# Main
# =========================
def main():
    print(f"[bridge] API_BASE={API_BASE}", flush=True)
    print(f"[bridge] DATA_DIR={DATA_DIR}", flush=True)
    print(f"[bridge] AGENDA_DIR={AGENDA_DIR}", flush=True)
    print(f"[bridge] CLIENTS_DIR={CLIENTS_DIR}", flush=True)
    print(f"[bridge] DEBUG_PHOTOS={DEBUG_PHOTOS} FORCE_PHOTOS={FORCE_PHOTOS}", flush=True)
    print(f"[bridge] DEBUG_CLIENTS={DEBUG_CLIENTS}", flush=True)
    print(f"[bridge] FORCE_PUSH_CLIENTS={FORCE_PUSH_CLIENTS} FORCE_PUSH_PHOTOS={FORCE_PUSH_PHOTOS}", flush=True)
    print(f"[bridge] FORCE_RELINK_BOOKINGS={FORCE_RELINK_BOOKINGS}", flush=True)
    print(f"[bridge] API_ADMIN_TOKEN={'SET' if API_ADMIN_TOKEN else 'NOT_SET'}", flush=True)

    start_http_server()

    st = load_state()
    remote_cache = load_remote_cache()

    fixed_names = fix_local_client_names_from_folders(verbose=True)
    print(f"[bridge] nomes corrigidos pelo nome da pasta: {fixed_names}", flush=True)

    cid_to_folder, idx_tel, idx_mail, idx_nome = build_client_folder_map()
    print(f"[bridge] clientes no PC: {len(cid_to_folder)}", flush=True)

    debug_list_local_clients()
    debug_duplicate_phone_email()

    info = api_info()
    print(f"[bridge] API OK: {info}", flush=True)

    fix_local_photo_fields_all_clients(verbose=True)

    if not st.get("seeded_once", False):
        resp, n = seed_remote_from_txt(remote_cache, st, idx_tel, idx_mail, idx_nome)
        st["seeded_once"] = True
        jsave(STATE_FILE, st)
        print(f"[bridge] SEED inicial: replace_all enviou {n} itens. Resp={resp}", flush=True)

    pulled_total = 0
    pushed_total = 0
    pushed_clients_total = 0
    pushed_photos_total = 0
    relink_total = 0

    while True:
        try:
            fixed_names = fix_local_client_names_from_folders(verbose=False)
            if fixed_names:
                cprint(f"[DEBUG] nomes corrigidos neste ciclo: {fixed_names}")

            cid_to_folder, idx_tel, idx_mail, idx_nome = build_client_folder_map()

            info = api_info()
            remote_bookings = int(info.get("bookings", 0) or 0)
            remote_changes = int(info.get("changes", 0) or 0)
            remote_clients = int(info.get("clients", 0) or 0)

            local_clients_count = len(list_clients_from_disk())

            cprint(
                f"[DEBUG] LOOP_STATUS local_clients={local_clients_count} "
                f"remote_clients={remote_clients} remote_bookings={remote_bookings} "
                f"remote_changes={remote_changes}"
            )

            if remote_bookings < RESEED_IF_REMOTE_BOOKINGS_LESS_THAN and remote_changes == 0:
                resp, n = seed_remote_from_txt(remote_cache, st, idx_tel, idx_mail, idx_nome)
                print(f"[bridge] RE-SEED bookings: replace_all {n} itens. Resp={resp}", flush=True)
                st["cursor"] = 0

            if remote_clients < local_clients_count and API_ADMIN_TOKEN:
                print(f"[bridge] API parece ter menos clientes ({remote_clients}) que o PC ({local_clients_count}) -> vai repor", flush=True)

            cursor = int(st.get("cursor", 0) or 0)
            pulled_now = 0

            while True:
                resp = api_pull(cursor, PULL_LIMIT)
                items = resp.get("items") or []
                cursor = int(resp.get("cursor", cursor))
                st["cursor"] = cursor

                if items:
                    apply_remote_changes_to_cache(remote_cache, items, idx_tel, idx_mail, idx_nome)
                    pulled_now += len(items)

                if len(items) < PULL_LIMIT:
                    break

            if pulled_now:
                jsave(REMOTE_CACHE_FILE, remote_cache)
                write_agenda_txt_from_cache(remote_cache)

            pulled_photos = sync_photos_from_api(st)
            pushed_clients_now = sync_clients_from_disk_to_api(st)
            pushed_photos_now = sync_photos_from_disk_to_api(st)
            pushed_now = push_local_changes(st, idx_tel, idx_mail, idx_nome)
            relink_now = relink_remote_bookings_with_clients(st, idx_tel, idx_mail, idx_nome)

            jsave(STATE_FILE, st)

            pulled_total += pulled_now
            pushed_total += pushed_now
            pushed_clients_total += pushed_clients_now
            pushed_photos_total += pushed_photos_now
            relink_total += relink_now

            if pulled_now or pushed_now or pulled_photos or pushed_clients_now or pushed_photos_now or relink_now:
                print(
                    f"[OK] sync: "
                    f"pulled_bookings={pulled_now} "
                    f"pushed_bookings={pushed_now} "
                    f"pulled_photos={pulled_photos} "
                    f"pushed_clients={pushed_clients_now} "
                    f"pushed_photos={pushed_photos_now} "
                    f"relinked_bookings={relink_now} "
                    f"(totals bookings_pull={pulled_total} bookings_push={pushed_total} "
                    f"clients_push={pushed_clients_total} photos_push={pushed_photos_total} relink={relink_total})",
                    flush=True
                )
            else:
                print("[OK] sync: sem mudanças", flush=True)

        except KeyboardInterrupt:
            print("\n[bridge] parado pelo utilizador.", flush=True)
            break
        except Exception as e:
            print(f"⚠️ sync erro: {e}", flush=True)

        time.sleep(SLEEP_SEC)

if __name__ == "__main__":
    main()
