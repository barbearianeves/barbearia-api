import os
from urllib.parse import quote_plus, unquote_plus

DATA_DIR = os.environ.get("DATA_DIR", "/tmp")  # no Render: ideal é um disco persistente
AGENDA_FILE = os.path.join(DATA_DIR, "agenda.txt")

FIELDS = ["id","time","dur","client_id","client","service","barber","status","notes"]

def parse_line(line: str) -> dict:
    line = line.strip()
    if not line:
        return {}
    parts = line.split("|")
    obj = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            obj[k] = unquote_plus(v)
    return obj

def to_line(obj: dict) -> str:
    # mantém o mesmo estilo: key=value|key=value...
    safe = {}
    for k in FIELDS:
        if k in obj and obj[k] is not None:
            safe[k] = quote_plus(str(obj[k]))
    # garante que id existe
    if "id" not in safe:
        raise ValueError("missing id")
    # permite campos extra, se quiseres
    for k, v in obj.items():
        if k not in safe and v is not None:
            safe[k] = quote_plus(str(v))
    return "|".join([f"{k}={safe[k]}" for k in safe.keys()])

def read_all() -> list[dict]:
    if not os.path.exists(AGENDA_FILE):
        return []
    with open(AGENDA_FILE, "r", encoding="utf-8") as f:
        return [parse_line(line) for line in f.read().splitlines() if line.strip()]

def write_all(items: list[dict]) -> None:
    os.makedirs(os.path.dirname(AGENDA_FILE), exist_ok=True)
    with open(AGENDA_FILE, "w", encoding="utf-8") as f:
        for it in items:
            f.write(to_line(it) + "\n")

def upsert(item: dict) -> dict:
    items = read_all()
    found = False
    for i in range(len(items)):
        if items[i].get("id") == item.get("id"):
            items[i] = {**items[i], **item}
            found = True
            break
    if not found:
        items.append(item)
    write_all(items)
    return item

def delete(item_id: str) -> None:
    items = [it for it in read_all() if it.get("id") != item_id]
    write_all(items)
