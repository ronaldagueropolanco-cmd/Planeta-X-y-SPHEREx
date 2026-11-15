"""Local science index and retrieval knowledge base.

The viewer ships with a real IRSA catalogue export of SPHEREx level-2 spectral
images (``data/spherex_spectral_images.csv``).  This module loads that export
into a small SQLite database so the assistant can answer questions about where
SPHEREx observed, which detector/band was used, and when the field was scanned,
without asking the network on every turn.

It also builds a BM25 index over the project documentation.  The result is a
fully local retrieval layer: the language model (when configured) receives the
retrieved passages and live catalogue facts as context, and the offline expert
uses exactly the same material.  No external embedding service is required.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import sqlite3
import threading
import unicodedata
from datetime import datetime, timedelta, timezone

from . import config

CATALOG_DB = os.path.abspath(
    os.environ.get(
        "SPHEREX_CATALOG_DB", os.path.join(config.INSTANCE_DIR, "catalog.db")
    )
)
DATA_CSV = os.path.join(config.ROOT, "data", "spherex_spectral_images.csv")

_lock = threading.RLock()
_ready = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    obs_id        TEXT NOT NULL,
    collection    TEXT,
    ra            REAL NOT NULL,
    dec           REAL NOT NULL,
    band          TEXT,
    detector      TEXT,
    em_min_um     REAL,
    em_max_um     REAL,
    t_min         REAL,
    t_max         REAL,
    exptime       REAL,
    access_url    TEXT,
    calib_level   INTEGER,
    target_name   TEXT,
    obs_title     TEXT,
    pixel_scale   REAL,
    resolution    REAL,
    fov           REAL,
    s_region      TEXT,
    release_date  TEXT,
    created_date  TEXT,
    PRIMARY KEY (obs_id, band)
);
CREATE INDEX IF NOT EXISTS idx_observations_pos ON observations(ra, dec);
CREATE INDEX IF NOT EXISTS idx_observations_band ON observations(band);
CREATE INDEX IF NOT EXISTS idx_observations_time ON observations(t_min);

CREATE TABLE IF NOT EXISTS index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS crossmatch_cache (
    cache_key  TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_docs (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    title  TEXT NOT NULL,
    source TEXT NOT NULL,
    text   TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(CATALOG_DB), exist_ok=True)
    conn = sqlite3.connect(CATALOG_DB, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init() -> None:
    with _lock:
        with connect() as conn:
            conn.executescript(SCHEMA)
        ensure_ready()


def _meta(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT key, value FROM index_meta").fetchall()
    return {row["key"]: row["value"] for row in rows}


def _set_meta(conn: sqlite3.Connection, values: dict) -> None:
    for key, value in values.items():
        conn.execute(
            "INSERT INTO index_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(key), str(value)),
        )


def _num(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------
def ingest_csv(path: str = DATA_CSV) -> dict:
    """Load the IRSA spectral-image export into SQLite (idempotent)."""
    if not os.path.isfile(path):
        return {"ok": False, "error": f"No se encontró el catálogo: {path}"}

    rows = []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            band = (raw.get("energy_bandpassname") or "").strip()
            obs_id = (raw.get("obs_id") or "").strip()
            ra = _num(raw.get("s_ra"))
            dec = _num(raw.get("s_dec"))
            if not obs_id or not band or ra is None or dec is None:
                continue
            detector = band.replace("SPHEREx-", "").strip() or None
            em_min = _num(raw.get("em_min"))
            em_max = _num(raw.get("em_max"))
            rows.append(
                (
                    obs_id,
                    (raw.get("obs_collection") or "").strip() or None,
                    ra,
                    dec,
                    band,
                    detector,
                    em_min * 1e6 if em_min is not None else None,
                    em_max * 1e6 if em_max is not None else None,
                    _num(raw.get("t_min")),
                    _num(raw.get("t_max")),
                    _num(raw.get("t_exptime")),
                    (raw.get("access_url") or "").strip() or None,
                    int(_num(raw.get("calib_level"), 0) or 0),
                    (raw.get("target_name") or "").strip() or None,
                    (raw.get("obs_title") or "").strip() or None,
                    _num(raw.get("s_pixel_scale")),
                    _num(raw.get("s_resolution")),
                    _num(raw.get("s_fov")),
                    (raw.get("s_region") or "").strip() or None,
                    (raw.get("obs_release_date") or "").strip() or None,
                    (raw.get("obs_creation_date") or "").strip() or None,
                )
            )

    with _lock:
        with connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute("DELETE FROM observations")
            conn.executemany(
                """INSERT OR REPLACE INTO observations (
                       obs_id, collection, ra, dec, band, detector,
                       em_min_um, em_max_um, t_min, t_max, exptime, access_url,
                       calib_level, target_name, obs_title, pixel_scale,
                       resolution, fov, s_region, release_date, created_date
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            _set_meta(
                conn,
                {
                    "source": os.path.basename(path),
                    "source_path": path,
                    "ingested_at": _now(),
                    "rows": len(rows),
                },
            )
    global _ready
    _ready = True
    return {"ok": True, "rows": len(rows)}


def ensure_ready() -> bool:
    """Create the schema and load the bundled catalogue on first use."""
    global _ready
    if _ready:
        return True
    with _lock:
        if _ready:
            return True
        with connect() as conn:
            conn.executescript(SCHEMA)
            count = conn.execute("SELECT COUNT(*) AS n FROM observations").fetchone()["n"]
        if count == 0:
            ingest_csv(DATA_CSV)
        _ready = True
    return True


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def _row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def stats() -> dict:
    """Global summary of the indexed SPHEREx catalogue."""
    ensure_ready()
    with connect() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS rows,
                      COUNT(DISTINCT obs_id) AS n_obs,
                      MIN(ra) AS ra_min, MAX(ra) AS ra_max,
                      MIN(dec) AS dec_min, MAX(dec) AS dec_max,
                      MIN(t_min) AS mjd_min, MAX(t_max) AS mjd_max,
                      MIN(exptime) AS exptime_min, MAX(exptime) AS exptime_max,
                      MIN(pixel_scale) AS pixel_min, MAX(pixel_scale) AS pixel_max,
                      MIN(resolution) AS res_min, MAX(resolution) AS res_max
               FROM observations"""
        ).fetchone()
        bands = conn.execute(
            """SELECT band, detector, COUNT(*) AS rows,
                      COUNT(DISTINCT obs_id) AS n_obs,
                      MIN(em_min_um) AS em_min_um, MAX(em_max_um) AS em_max_um,
                      MIN(t_min) AS mjd_min, MAX(t_max) AS mjd_max
               FROM observations GROUP BY band ORDER BY band"""
        ).fetchall()
        collections = conn.execute(
            "SELECT collection, COUNT(*) AS rows FROM observations GROUP BY collection"
        ).fetchall()
        meta = _meta(conn)
        n_docs = conn.execute("SELECT COUNT(*) AS n FROM knowledge_docs").fetchone()["n"]

    bbox_area = 0.0
    if row and None not in (row["ra_min"], row["ra_max"], row["dec_min"], row["dec_max"]):
        width = (row["ra_max"] - row["ra_min"]) * math.cos(
            math.radians((row["ra_min"] + row["ra_max"]) / 2.0)
        )
        height = row["dec_max"] - row["dec_min"]
        bbox_area = abs(width * height)

    return {
        "source": meta.get("source", "—"),
        "ingested_at": meta.get("ingested_at"),
        "rows": int(row["rows"]) if row else 0,
        "n_observations": int(row["n_obs"]) if row else 0,
        "n_bands": len(bands),
        "bands": [_row_to_dict(item) for item in bands],
        "collections": [_row_to_dict(item) for item in collections],
        "sky": {
            "ra_min": row["ra_min"] if row else None,
            "ra_max": row["ra_max"] if row else None,
            "dec_min": row["dec_min"] if row else None,
            "dec_max": row["dec_max"] if row else None,
            "bbox_area_deg2": round(bbox_area, 2),
        },
        "time": {
            "mjd_min": row["mjd_min"] if row else None,
            "mjd_max": row["mjd_max"] if row else None,
            "days": round((row["mjd_max"] - row["mjd_min"]), 1) if row else None,
        },
        "instrument": {
            "exptime_s": row["exptime_min"] if row else None,
            "pixel_scale_arcsec": row["pixel_min"] if row else None,
            "resolution_arcsec": row["res_min"] if row else None,
        },
        "knowledge_docs": int(n_docs),
    }


def _separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle separation in arcseconds (haversine, stable at small angles)."""
    lon1, lat1, lon2, lat2 = map(
        math.radians, (ra1, dec1, ra2, dec2)
    )
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * math.asin(min(1.0, math.sqrt(a))) * 206264.806


def search(
    ra: float | None = None,
    dec: float | None = None,
    radius_deg: float = 1.0,
    *,
    band: str | None = None,
    mjd_min: float | None = None,
    mjd_max: float | None = None,
    limit: int = 120,
) -> dict:
    """Search observations by sky position, detector and time window."""
    ensure_ready()
    limit = max(1, min(int(limit), 1000))
    clauses = []
    params: list = []
    radius_deg = max(0.001, min(float(radius_deg), 30.0))

    if ra is not None and dec is not None:
        # Bounding box first so SQLite can use the position index.
        cos_dec = max(math.cos(math.radians(float(dec))), 1e-3)
        d_ra = radius_deg / cos_dec
        clauses += ["ra BETWEEN ? AND ?", "dec BETWEEN ? AND ?"]
        params += [
            float(ra) - d_ra,
            float(ra) + d_ra,
            float(dec) - radius_deg,
            float(dec) + radius_deg,
        ]
    if band:
        clauses.append("(band = ? OR detector = ?)")
        params += [band, band.replace("SPHEREx-", "")]
    if mjd_min is not None:
        clauses.append("t_max >= ?")
        params.append(float(mjd_min))
    if mjd_max is not None:
        clauses.append("t_min <= ?")
        params.append(float(mjd_max))

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM observations {where} ORDER BY t_min LIMIT ?",
            params + [limit * 8 if ra is not None else limit],
        ).fetchall()

    items = []
    for row in rows:
        item = _row_to_dict(row)
        if ra is not None and dec is not None:
            sep = _separation_arcsec(float(ra), float(dec), row["ra"], row["dec"])
            if sep > radius_deg * 3600.0:
                continue
            item["separation_arcsec"] = round(sep, 1)
        items.append(item)
        if len(items) >= limit:
            break

    items.sort(key=lambda item: item.get("separation_arcsec", 0.0))
    return {
        "count": len(items),
        "radius_deg": radius_deg if ra is not None else None,
        "query": {"ra": ra, "dec": dec, "band": band, "mjd_min": mjd_min, "mjd_max": mjd_max},
        "observations": items,
    }


def field_summary(ra: float, dec: float, radius_deg: float = 0.5) -> dict:
    """Compact description of the archive coverage around a position."""
    result = search(ra, dec, radius_deg, limit=1000)
    rows = result["observations"]
    if not rows:
        return {
            "available": False,
            "ra": ra,
            "dec": dec,
            "radius_deg": radius_deg,
            "message": "El índice local no contiene observaciones SPHEREx en esa posición.",
        }
    bands = sorted({row["band"] for row in rows})
    obs_ids = sorted({row["obs_id"] for row in rows})
    mjds = [row["t_min"] for row in rows if row["t_min"] is not None]
    epochs: dict[str, list[str]] = {}
    for row in rows:
        if row["t_min"] is None:
            continue
        day = (
            datetime(1970, 1, 1, tzinfo=timezone.utc)
            + timedelta(days=row["t_min"] - 40587.0)
        ).strftime("%Y-%m-%d")
        epochs.setdefault(day, []).append(row["detector"] or row["band"])
    timeline = [
        {"date": day, "detectors": sorted(set(values)), "n": len(values)}
        for day, values in sorted(epochs.items())
    ]
    return {
        "available": True,
        "ra": ra,
        "dec": dec,
        "radius_deg": radius_deg,
        "n_observations": len(obs_ids),
        "n_rows": len(rows),
        "bands": bands,
        "n_bands": len(bands),
        "mjd_min": min(mjds) if mjds else None,
        "mjd_max": max(mjds) if mjds else None,
        "timeline": timeline[:40],
        "nearest": rows[0] if rows else None,
        "collections": sorted({row["collection"] for row in rows if row["collection"]}),
        "note": (
            "Índice local construido con el catálogo IRSA de imágenes espectrales "
            "SPHEREx. No sustituye una consulta TAP en vivo, pero permite responder "
            "sin red y sirve de contexto al agente."
        ),
    }


def band_summary() -> list[dict]:
    ensure_ready()
    with connect() as conn:
        rows = conn.execute(
            """SELECT band, detector, COUNT(*) AS rows,
                      COUNT(DISTINCT obs_id) AS n_obs,
                      MIN(em_min_um) AS em_min_um, MAX(em_max_um) AS em_max_um,
                      MIN(t_min) AS mjd_min, MAX(t_max) AS mjd_max
               FROM observations GROUP BY band ORDER BY band"""
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def timeline(limit_days: int = 120) -> list[dict]:
    ensure_ready()
    with connect() as conn:
        rows = conn.execute(
            """SELECT CAST(t_min AS INTEGER) AS day, band, detector, COUNT(*) AS n
               FROM observations GROUP BY day, band ORDER BY day"""
        ).fetchall()
    buckets: dict[int, dict] = {}
    for row in rows:
        bucket = buckets.setdefault(
            int(row["day"]), {"mjd": int(row["day"]), "rows": 0, "bands": []}
        )
        bucket["rows"] += int(row["n"])
        bucket["bands"].append(row["band"])
    items = []
    for day in sorted(buckets)[-limit_days:]:
        bucket = buckets[day]
        items.append(
            {
                "mjd": day,
                "date": (
                    datetime(1970, 1, 1, tzinfo=timezone.utc)
                    + timedelta(days=day - 40587.0)
                ).strftime("%Y-%m-%d"),
                "rows": bucket["rows"],
                "bands": sorted(set(bucket["bands"])),
            }
        )
    return items


def catalog_facts() -> str:
    """A short Spanish fact sheet injected into prompts and offline answers."""
    try:
        data = stats()
    except Exception:
        return "Índice local del catálogo SPHEREx no disponible."
    if not data.get("rows"):
        return "Índice local del catálogo SPHEREx vacío."
    time_info = data["time"]
    sky = data["sky"]
    lines = [
        f"Índice local SPHEREx: {data['rows']} registros de {data['n_observations']} "
        f"observaciones, {data['n_bands']} detectores/bandas, "
        f"fuente {data['source']}.",
        f"Cobertura: RA {sky['ra_min']:.2f}–{sky['ra_max']:.2f}°, "
        f"Dec {sky['dec_min']:.2f}–{sky['dec_max']:.2f}° "
        f"(~{sky['bbox_area_deg2']:.1f} deg² de caja).",
        f"Tiempo: MJD {time_info['mjd_min']:.1f}–{time_info['mjd_max']:.1f} "
        f"({time_info['days']:.0f} días).",
        f"Instrumento: {data['instrument']['exptime_s']:.1f} s por exposición, "
        f"{data['instrument']['pixel_scale_arcsec']:.2f}\"/px, "
        f"resolución {data['instrument']['resolution_arcsec']:.2f}\".",
        "Bandas: " + ", ".join(
            f"{item['band']} ({item['rows']})" for item in data["bands"]
        ),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cross-match cache (shared with spherex.crossmatch)
# ---------------------------------------------------------------------------
def cache_get(key: str, max_age_days: float = 30.0) -> dict | None:
    ensure_ready()
    with connect() as conn:
        row = conn.execute(
            "SELECT payload, created_at FROM crossmatch_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
    if row is None:
        return None
    try:
        created = datetime.fromisoformat(row["created_at"])
        age = (datetime.now(timezone.utc) - created).total_seconds() / 86400.0
        if age > max_age_days:
            return None
        value = json.loads(row["payload"])
        return value if isinstance(value, dict) else None
    except (ValueError, TypeError):
        return None


def cache_put(key: str, payload: dict) -> None:
    ensure_ready()
    with _lock:
        with connect() as conn:
            conn.execute(
                """INSERT INTO crossmatch_cache (cache_key, payload, created_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(cache_key) DO UPDATE SET payload = excluded.payload,
                                                        created_at = excluded.created_at""",
                (key, json.dumps(payload, ensure_ascii=False), _now()),
            )


# ---------------------------------------------------------------------------
# Knowledge base (BM25 over project documentation)
# ---------------------------------------------------------------------------
_STOPWORDS = {
    "de", "la", "el", "los", "las", "un", "una", "unos", "unas", "y", "o", "u",
    "en", "con", "por", "para", "sin", "sobre", "entre", "que", "como", "cómo",
    "es", "son", "ser", "está", "esta", "están", "estan", "hay", "ha", "han",
    "del", "al", "se", "su", "sus", "lo", "le", "les", "más", "mas", "pero",
    "si", "sí", "no", "ni", "ya", "muy", "también", "tambien", "desde", "hasta",
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "with", "this", "that", "it", "as", "at", "by", "be", "from", "we", "you",
}

_CURATED = [
    {
        "title": "Qué es SPHEREx",
        "source": "base de conocimiento",
        "text": (
            "SPHEREx (Spectro-Photometer for the History of the Universe, Epoch of "
            "Reionization and Ices Explorer) es un telescopio espacial de la NASA "
            "lanzado en 2025. Barre todo el cielo en el infrarrojo cercano con 102 "
            "canales entre 0.75 y 5.0 micrómetros y una resolución espacial de unos "
            "6 arcosegundos por píxel. Sus objetivos científicos son la inflación "
            "cósmica, el origen del agua y las moléculas orgánicas, y la historia de "
            "la formación de galaxias. Los datos level-2 se publican en NASA/IPAC "
            "IRSA como imágenes espectrales calibradas en MJy/sr."
        ),
    },
    {
        "title": "Los seis detectores LVF",
        "source": "base de conocimiento",
        "text": (
            "SPHEREx usa seis detectores con filtro lineal de longitud de onda "
            "variable (LVF), etiquetados D1 a D6. Cada detector cubre una franja "
            "espectral distinta y juntos suman 102 canales. En una sola exposición "
            "no se obtienen las 102 bandas: el barrido desplaza la franja espectral "
            "sobre el cielo y cada píxel registra una longitud de onda concreta. Por "
            "eso una época puede cubrir solo una parte del espectro. La tabla "
            "WCS-WAVE del FITS level-2 asigna a cada píxel su longitud de onda "
            "central y su ancho de banda."
        ),
    },
    {
        "title": "Productos level-2 de IRSA",
        "source": "base de conocimiento",
        "text": (
            "Cada recorte level-2 de SPHEREx es un FITS multi-extensión. IMAGE "
            "contiene la brilldez superficial calibrada en MJy/sr; FLAGS es una "
            "máscara de bits por píxel; VARIANCE da la varianza; ZODI modela la luz "
            "zodiacal; WCS-WAVE mapea píxel a longitud de onda. El visor consulta "
            "spherex.artifact y spherex.plane por TAP, descarga recortes con el "
            "servicio IBE, los reproyecta a una cuadrícula TAN norte-arriba y los "
            "registra por correlación cruzada FFT antes de comparar épocas."
        ),
    },
    {
        "title": "Gaia y WISE como referencia",
        "source": "base de conocimiento",
        "text": (
            "Gaia DR3 y AllWISE se consultan por TAP alrededor del campo abierto. "
            "Gaia aporta posiciones ópticas de alta precisión, paralaje y movimiento "
            "propio; AllWISE aporta fotometría infrarroja W1–W4. En el visor se "
            "dibujan como superposiciones y se usan para descartar que un residuo "
            "de diferencia sea una estrella conocida mal restada. Un candidato sin "
            "contrapartida en Gaia ni WISE, con dipolo y desplazamiento coherente, "
            "es más interesante que un residuo centrado en una estrella brillante."
        ),
    },
    {
        "title": "Diferencia de épocas y dipolos",
        "source": "base de conocimiento",
        "text": (
            "La detección compara dos épocas solo en los canales observados en "
            "ambas, convierte el residuo a significancia por píxel usando la "
            "varianza combinada y busca componentes positivas y negativas. Un "
            "dipolo negativo/positivo con flujo y área parecidos indica un objeto "
            "que se movió entre las dos fechas. Una componente positiva sin pareja "
            "puede ser un transitorio o una variable. Un residuo centrado en una "
            "estrella brillante suele ser un artefacto de sustracción. El radio "
            "máximo del dipolo se limita a unos 5 arcosegundos por día."
        ),
    },
    {
        "title": "Clasificador de candidatos",
        "source": "base de conocimiento",
        "text": (
            "El clasificador local de candidatos es una regresión logística "
            "multiclase entrenada con características extraídas del motor de "
            "detección: relación señal-ruido, área, cociente de flujo, presencia de "
            "dipolo, tasa de movimiento, línea base, número de canales y "
            "contrapartidas en Gaia/WISE. Devuelve probabilidades para cuatro "
            "clases: artefacto, objeto en movimiento, transitorio y variable. Es "
            "una ayuda al cribado, no una confirmación física: la validación "
            "requiere revisar flags, espectro, registro y catálogos."
        ),
    },
    {
        "title": "Cómo estudiar un cambio de imagen",
        "source": "base de conocimiento",
        "text": (
            "Para analizar un cambio entre épocas: primero comprueba que el canal "
            "existe en ambas fechas; segundo revisa flags de calidad, saturación y "
            "rayos cósmicos; tercero verifica el registro astrométrico; cuarto mide "
            "el espectro y busca contrapartidas Gaia/WISE. Un cambio repetido en "
            "varios canales gana confianza; uno que aparece en un solo canal suele "
            "ser ruido. Documenta siempre la línea base, el umbral en sigma y la "
            "versión del método para que el resultado sea reproducible."
        ),
    },
    {
        "title": "Límites del visor",
        "source": "base de conocimiento",
        "text": (
            "El visor es una herramienta de cribado y divulgación. No mide "
            "astrometría de precisión, no hace fotometría absoluta de catálogo y no "
            "confirma descubrimientos. Las épocas disponibles dependen de la "
            "cobertura del archivo y del tamaño del recorte. Los datos demo son "
            "simulaciones claramente etiquetadas. Cualquier candidato debe "
            "validarse con datos adicionales, seguimiento y análisis independiente."
        ),
    },
]

_docs_cache: list[dict] | None = None
_docs_mtime: float = 0.0


def _tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    tokens = re.findall(r"[a-z0-9µμ][a-z0-9µμ._\-]{1,}", text.lower())
    return [token for token in tokens if token not in _STOPWORDS and len(token) > 1]


def _chunk_markdown(text: str, source: str, max_chars: int = 1500) -> list[dict]:
    chunks = []
    title = source
    buffer: list[str] = []

    def flush():
        nonlocal buffer
        body = "\n".join(buffer).strip()
        if len(body) >= 120:
            chunks.append({"title": title, "source": source, "text": body[:max_chars]})
        buffer = []

    for line in text.splitlines():
        if re.match(r"^#{1,4}\s+", line):
            flush()
            title = re.sub(r"^#{1,4}\s+", "", line).strip() or source
        buffer.append(line)
        if sum(len(item) for item in buffer) > max_chars:
            flush()
    flush()
    return chunks


def _build_docs() -> list[dict]:
    docs = [dict(item) for item in _CURATED]
    for name in ("DOCUMENTACION.md", "README.md"):
        path = os.path.join(config.ROOT, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            continue
        docs.extend(_chunk_markdown(text, name))
    return docs


def rebuild_knowledge(force: bool = False) -> int:
    """Recreate the document table from the curated notes and docs."""
    global _docs_cache, _docs_mtime
    ensure_ready()
    docs = _build_docs()
    with _lock:
        with connect() as conn:
            conn.execute("DELETE FROM knowledge_docs")
            conn.executemany(
                "INSERT INTO knowledge_docs (title, source, text) VALUES (?, ?, ?)",
                [(item["title"], item["source"], item["text"]) for item in docs],
            )
    _docs_cache = None
    _docs_mtime = max(
        (
            os.path.getmtime(os.path.join(config.ROOT, name))
            for name in ("DOCUMENTACION.md", "README.md")
            if os.path.isfile(os.path.join(config.ROOT, name))
        ),
        default=0.0,
    )
    return len(docs)


def _load_docs() -> list[dict]:
    global _docs_cache, _docs_mtime
    ensure_ready()
    mtime = max(
        (
            os.path.getmtime(os.path.join(config.ROOT, name))
            for name in ("DOCUMENTACION.md", "README.md")
            if os.path.isfile(os.path.join(config.ROOT, name))
        ),
        default=0.0,
    )
    if _docs_cache is not None and mtime <= _docs_mtime:
        return _docs_cache
    with connect() as conn:
        rows = conn.execute(
            "SELECT title, source, text FROM knowledge_docs ORDER BY id"
        ).fetchall()
    if not rows:
        rebuild_knowledge()
        with connect() as conn:
            rows = conn.execute(
                "SELECT title, source, text FROM knowledge_docs ORDER BY id"
            ).fetchall()
    _docs_cache = [_row_to_dict(row) for row in rows]
    _docs_mtime = mtime
    return _docs_cache


def knowledge_stats() -> dict:
    docs = _load_docs()
    sources: dict[str, int] = {}
    for doc in docs:
        sources[doc["source"]] = sources.get(doc["source"], 0) + 1
    return {
        "documents": len(docs),
        "characters": sum(len(doc["text"]) for doc in docs),
        "sources": sources,
        "method": "BM25 local",
    }


def search_knowledge(query: str, k: int = 5) -> list[dict]:
    """Rank documentation passages with Okapi BM25."""
    terms = _tokenize(query)
    if not terms:
        return []
    docs = _load_docs()
    if not docs:
        return []
    tokenized = [_tokenize(doc["text"] + " " + doc["title"]) for doc in docs]
    n_docs = len(docs)
    avg_len = sum(len(tokens) for tokens in tokenized) / max(n_docs, 1)
    df: dict[str, int] = {}
    for tokens in tokenized:
        for term in set(tokens):
            df[term] = df.get(term, 0) + 1

    k1, b = 1.5, 0.75
    scores = []
    for index, tokens in enumerate(tokenized):
        length = len(tokens) or 1
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        score = 0.0
        for term in terms:
            freq = counts.get(term, 0)
            if not freq:
                continue
            idf = math.log(1.0 + (n_docs - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
            score += idf * (freq * (k1 + 1.0)) / (
                freq + k1 * (1.0 - b + b * length / max(avg_len, 1e-9))
            )
        if score > 0:
            scores.append((score, index))
    scores.sort(reverse=True)
    results = []
    for score, index in scores[: max(1, min(int(k), 12))]:
        doc = docs[index]
        results.append(
            {
                "title": doc["title"],
                "source": doc["source"],
                "text": doc["text"],
                "score": round(float(score), 3),
            }
        )
    return results


def knowledge_context(query: str, k: int = 4, max_chars: int = 3200) -> str:
    """Concatenate the best passages for injection into a prompt."""
    passages = search_knowledge(query, k=k)
    if not passages:
        return ""
    parts = []
    used = 0
    for passage in passages:
        block = f"[{passage['source']} · {passage['title']}]\n{passage['text']}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def status() -> dict:
    """Public summary used by the assistant status endpoint."""
    try:
        data = stats()
        knowledge = knowledge_stats()
        return {
            "catalog": {
                "rows": data["rows"],
                "observations": data["n_observations"],
                "bands": data["n_bands"],
                "source": data["source"],
                "mjd_min": data["time"]["mjd_min"],
                "mjd_max": data["time"]["mjd_max"],
            },
            "knowledge": knowledge,
        }
    except Exception as exc:  # noqa: BLE001 - status must never break the app
        return {"catalog": {"rows": 0}, "knowledge": {"documents": 0}, "error": str(exc)}
