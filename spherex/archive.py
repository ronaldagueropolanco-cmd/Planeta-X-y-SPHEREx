"""Client for the NASA/IPAC IRSA SPHEREx archive.

Real data are served through the IVOA TAP and cutout services:

* TAP       https://irsa.ipac.caltech.edu/TAP/sync
* Cutouts   https://irsa.ipac.caltech.edu/ibe/data/...fits?center=RA,Dec&size=DEG

The level-2 products are multi-extension FITS files. The IMAGE extension holds
calibrated surface brightness in MJy/sr, FLAGS a per-pixel bitmask, VARIANCE the
variance map, ZODI the zodiacal-light model and WCS-WAVE a lookup table that maps
every pixel to its central wavelength and bandwidth.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import quote

import requests

TAP_URL = "https://irsa.ipac.caltech.edu/TAP/sync"
CUTOUT_BASE = "https://irsa.ipac.caltech.edu/"
SESAME_URL = "https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV"

CACHE_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "uploads", "cache")
)
CUTOUT_DIR = os.path.join(CACHE_ROOT, "cutouts")
os.makedirs(CUTOUT_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "SPHEREx-Sky-Viewer/2.0 (NASA Space Apps Challenge)",
    "Accept": "application/fits,text/csv,*/*",
}

_session = requests.Session()
_session.headers.update(HEADERS)


class ArchiveError(RuntimeError):
    pass


@dataclass
class Observation:
    uri: str
    mjd: float
    detector: str
    wave_min_um: float
    wave_max_um: float
    obs_id: str
    day: int

    @property
    def date(self) -> str:
        return _mjd_to_date(self.mjd)

    def to_dict(self) -> dict:
        return {
            "id": self.obs_id,
            "uri": self.uri,
            "mjd": round(self.mjd, 5),
            "day": self.day,
            "date": self.date,
            "detector": self.detector,
            "wave_min_um": round(self.wave_min_um, 4),
            "wave_max_um": round(self.wave_max_um, 4),
        }


def _mjd_to_date(mjd: float) -> str:
    # MJD 40587 = 1970-01-01
    import datetime as _dt

    try:
        return (
            _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
            + _dt.timedelta(days=float(mjd) - 40587.0)
        ).strftime("%Y-%m-%d")
    except Exception:
        return "—"


def _request(url: str, *, params: dict | None = None, timeout: int = 180,
             retries: int = 4) -> requests.Response:
    last_error = "unknown error"
    for attempt in range(retries):
        try:
            response = _session.get(url, params=params, timeout=timeout)
            if response.status_code == 200:
                return response
            last_error = f"HTTP {response.status_code}"
        except requests.RequestException as exc:  # network hiccup, retry
            last_error = str(exc)
        time.sleep(1.8 * (attempt + 1))
    raise ArchiveError(f"No se pudo contactar el archivo IRSA ({last_error})")


def tap(adql: str, timeout: int = 240) -> list[dict]:
    """Run a synchronous ADQL query and return the rows as dictionaries."""
    response = _request(
        TAP_URL,
        params={"QUERY": adql, "RESPONSEFORMAT": "CSV"},
        timeout=timeout,
    )
    text = response.text
    if not text.strip() or text.lstrip().startswith("<"):
        raise ArchiveError("Respuesta TAP vacía o inválida")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


def archive_online() -> bool:
    try:
        response = _session.get(
            "https://irsa.ipac.caltech.edu/",
            timeout=6,
            headers={"User-Agent": HEADERS["User-Agent"]},
        )
        return response.status_code < 500
    except Exception:
        return False


def query_field(
    ra: float,
    dec: float,
    *,
    mjd_min: float | None = None,
    mjd_max: float | None = None,
    limit: int = 4000,
) -> list[Observation]:
    """Return SPHEREx level-2 observations that cover a sky position."""
    clauses = [
        "1 = CONTAINS(POINT('ICRS', %.6f, %.6f), p.poly)" % (ra, dec),
    ]
    if mjd_min is not None:
        clauses.append("p.time_bounds_lower >= %.3f" % mjd_min)
    if mjd_max is not None:
        clauses.append("p.time_bounds_lower <= %.3f" % mjd_max)

    adql = f"""
SELECT TOP {int(limit)}
       a.uri,
       p.time_bounds_lower,
       p.energy_bandpassname,
       p.energy_bounds_lower,
       p.energy_bounds_upper
FROM spherex.artifact a
JOIN spherex.plane p ON a.planeid = p.planeid
WHERE {' AND '.join(clauses)}
ORDER BY p.time_bounds_lower
"""
    table = tap(adql)
    rows: list[Observation] = []
    for row in table:
        try:
            uri = str(row["uri"])
            mjd = float(row["time_bounds_lower"])
            detector = str(row["energy_bandpassname"]).replace("SPHEREx-", "")
            wmin = float(row["energy_bounds_lower"]) * 1e6
            wmax = float(row["energy_bounds_upper"]) * 1e6
        except (KeyError, TypeError, ValueError):
            continue
        match = re.search(r"(level2_[^/]+?)_spx", uri)
        obs_id = match.group(1) if match else os.path.basename(uri).split(".")[0]
        rows.append(
            Observation(
                uri=uri,
                mjd=mjd,
                detector=detector,
                wave_min_um=wmin,
                wave_max_um=wmax,
                obs_id=obs_id,
                day=int(mjd),
            )
        )
    return rows


def group_epochs(observations: Iterable[Observation]) -> list[dict]:
    """Collapse observations into daily epochs with one frame per detector."""
    by_day: dict[int, dict[str, Observation]] = {}
    for obs in observations:
        day = by_day.setdefault(obs.day, {})
        current = day.get(obs.detector)
        # Keep the first observation of the day for each detector: it is the
        # start of the scan and gives a stable, reproducible choice.
        if current is None or obs.mjd < current.mjd:
            day[obs.detector] = obs

    epochs = []
    for day in sorted(by_day):
        dets = by_day[day]
        chosen = [dets[key] for key in sorted(dets)]
        epochs.append(
            {
                "day": day,
                "mjd": round(float(np_mean([o.mjd for o in chosen])), 5),
                "date": _mjd_to_date(day),
                "detectors": [o.detector for o in chosen],
                "observation_ids": [o.obs_id for o in chosen],
                "observations": [o.to_dict() for o in chosen],
            }
        )
    return epochs


def np_mean(values: list[float]) -> float:
    return sum(values) / max(len(values), 1)


def cutout_cache_key(uri: str, ra: float, dec: float, size_deg: float) -> str:
    raw = f"{uri}|{ra:.5f}|{dec:.5f}|{size_deg:.4f}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def download_cutout(uri: str, ra: float, dec: float, size_deg: float) -> str:
    """Download (or reuse) an IRSA cutout and return the local FITS path."""
    key = cutout_cache_key(uri, ra, dec, size_deg)
    path = os.path.join(CUTOUT_DIR, key + ".fits")
    if os.path.exists(path) and os.path.getsize(path) > 100_000:
        return path

    url = CUTOUT_BASE + uri.lstrip("/")
    params = {"center": f"{ra:.6f},{dec:.6f}", "size": f"{size_deg:.4f}"}
    last_error = "unknown error"
    for attempt in range(4):
        try:
            response = _session.get(url, params=params, timeout=300)
            content = response.content
            if response.status_code == 200 and content[:6] == b"SIMPLE":
                tmp = path + ".part"
                with open(tmp, "wb") as handle:
                    handle.write(content)
                os.replace(tmp, path)
                return path
            last_error = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(2.0 * (attempt + 1))

    raise ArchiveError(f"Descarga de recorte fallida: {last_error}")


def query_catalog(kind: str, ra: float, dec: float, radius_deg: float,
                  limit: int = 600) -> list[dict]:
    """Query Gaia DR3 or AllWISE sources around a position through IRSA TAP."""
    kind = (kind or "gaia").lower()
    if kind in ("gaia", "gaia_dr3", "gaia-dr3", "gaia_dr2", "gaia-dr2"):
        table = "gaia_dr3_source"
        columns = "ra, dec, phot_g_mean_mag, parallax, pmra, pmdec"
        label = "gaia"
    elif kind in ("wise", "allwise", "allwise_p3as_psr"):
        table = "allwise_p3as_psr"
        columns = "ra, dec, w1mpro, w2mpro, w3mpro, w4mpro"
        label = "wise"
    else:
        raise ArchiveError(f"Catálogo no soportado: {kind}")

    radius_deg = max(0.005, min(float(radius_deg), 5.0))
    adql = f"""
SELECT TOP {int(limit)}
       {columns}
FROM {table}
WHERE 1 = CONTAINS(POINT('ICRS', ra, dec),
                   CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.5f}))
"""
    rows = tap(adql)
    out = []
    for row in rows:
        try:
            item = {"ra": float(row["ra"]), "dec": float(row["dec"])}
            if label == "gaia":
                item.update(
                    {
                        "mag": float(row.get("phot_g_mean_mag") or "nan"),
                        "parallax": float(row.get("parallax") or "nan"),
                        "pmra": float(row.get("pmra") or "nan"),
                        "pmdec": float(row.get("pmdec") or "nan"),
                    }
                )
            else:
                item.update(
                    {
                        "mag": float(row.get("w1mpro") or "nan"),
                        "w2": float(row.get("w2mpro") or "nan"),
                        "w3": float(row.get("w3mpro") or "nan"),
                        "w4": float(row.get("w4mpro") or "nan"),
                    }
                )
            out.append(item)
        except (TypeError, ValueError):
            continue
    return out


def resolve_name(name: str) -> dict | None:
    """Resolve an object name through CDS Sesame."""
    name = name.strip()
    if not name:
        return None
    try:
        # Sesame expects the target as the query string itself (SNV?M31), not
        # as a ``name=`` parameter; the latter is treated as a literal target.
        response = _session.get(
            f"{SESAME_URL}?{quote(name)}",
            timeout=20,
            headers={"User-Agent": HEADERS["User-Agent"]},
        )
        if response.status_code != 200:
            return None
        text = response.text
        ra = re.search(r"<jradeg>([\d.]+)</jradeg>", text)
        dec = re.search(r"<jdedeg>([-\d.]+)</jdedeg>", text)
        if not ra or not dec:
            return None
        alias = re.search(r"<oname>(.*?)</oname>", text)
        return {
            "name": alias.group(1) if alias else name,
            "ra": float(ra.group(1)),
            "dec": float(dec.group(1)),
        }
    except Exception:
        return None
