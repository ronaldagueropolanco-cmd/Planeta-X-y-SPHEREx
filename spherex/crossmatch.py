"""Gaia DR3 / AllWISE cross-match for candidate validation.

The cross-match is deliberately lightweight: one TAP query per field for each
catalogue, cached in ``instance/catalog.db``.  It is used to answer the first
question any astronomer asks about a difference-imaging residual: is there a
known star at that position?  When the archive is unreachable the rest of the
viewer keeps working and the result is marked as unavailable.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from . import archive, catalog

FIELD_LIMIT = 1200
CACHE_DAYS = 30.0
FAILURE_CACHE_DAYS = 0.25


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (ra1, dec1, ra2, dec2))
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * math.asin(min(1.0, math.sqrt(a))) * 206264.806


def _float(value, default=float("nan")) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _query_gaia(ra: float, dec: float, radius_deg: float) -> list[dict]:
    adql = f"""
SELECT TOP {FIELD_LIMIT}
       ra, dec, phot_g_mean_mag, parallax, pmra, pmdec
FROM gaia_dr3_source
WHERE 1 = CONTAINS(POINT('ICRS', ra, dec),
                   CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.5f}))
"""
    rows = archive.tap(adql, timeout=60)
    out = []
    for row in rows:
        ra_v = _float(row.get("ra"))
        dec_v = _float(row.get("dec"))
        if not (math.isfinite(ra_v) and math.isfinite(dec_v)):
            continue
        out.append(
            {
                "ra": round(ra_v, 7),
                "dec": round(dec_v, 7),
                "mag": _float(row.get("phot_g_mean_mag")),
                "parallax": _float(row.get("parallax")),
                "pmra": _float(row.get("pmra")),
                "pmdec": _float(row.get("pmdec")),
            }
        )
    return out


def _query_wise(ra: float, dec: float, radius_deg: float) -> list[dict]:
    adql = f"""
SELECT TOP {FIELD_LIMIT}
       ra, dec, w1mpro, w2mpro, w3mpro, w4mpro
FROM allwise_p3as_psr
WHERE 1 = CONTAINS(POINT('ICRS', ra, dec),
                   CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.5f}))
"""
    rows = archive.tap(adql, timeout=60)
    out = []
    for row in rows:
        ra_v = _float(row.get("ra"))
        dec_v = _float(row.get("dec"))
        if not (math.isfinite(ra_v) and math.isfinite(dec_v)):
            continue
        out.append(
            {
                "ra": round(ra_v, 7),
                "dec": round(dec_v, 7),
                "mag": _float(row.get("w1mpro")),
                "w1": _float(row.get("w1mpro")),
                "w2": _float(row.get("w2mpro")),
                "w3": _float(row.get("w3mpro")),
                "w4": _float(row.get("w4mpro")),
            }
        )
    return out


def field_sources(
    ra: float,
    dec: float,
    radius_deg: float = 0.5,
    *,
    force: bool = False,
) -> dict:
    """Return Gaia DR3 and AllWISE sources around a field, with caching."""
    radius_deg = max(0.01, min(float(radius_deg), 5.0))
    key = f"field|{float(ra):.3f}|{float(dec):.3f}|{radius_deg:.3f}|{FIELD_LIMIT}"
    if not force:
        cached = catalog.cache_get(key, max_age_days=CACHE_DAYS)
        if cached is not None:
            cached["cached"] = True
            return cached

    result = {
        "available": False,
        "ra": round(float(ra), 5),
        "dec": round(float(dec), 5),
        "radius_deg": radius_deg,
        "gaia": [],
        "wise": [],
        "queried_at": _now(),
        "source": "Gaia DR3 + AllWISE (IRSA TAP)",
        "cached": False,
    }
    errors = []
    try:
        result["gaia"] = _query_gaia(float(ra), float(dec), radius_deg)
        result["available"] = True
    except Exception as exc:  # noqa: BLE001 - offline is a normal state
        errors.append(f"Gaia: {exc}")
    try:
        result["wise"] = _query_wise(float(ra), float(dec), radius_deg)
        result["available"] = True
    except Exception as exc:  # noqa: BLE001
        errors.append(f"WISE: {exc}")
    if errors:
        result["errors"] = errors
    result["n_gaia"] = len(result["gaia"])
    result["n_wise"] = len(result["wise"])
    result["note"] = (
        "Catálogos de referencia consultados por TAP. Una contrapartida no "
        "descarta por sí sola un candidato, pero ayuda a separar estrellas "
        "conocidas de residuos de sustracción."
    )
    catalog.cache_put(
        key, result if result["available"] else {**result, "failure": True}
    )
    return result


def _nearest(sources: list[dict], ra: float, dec: float) -> tuple[dict | None, float]:
    best = None
    best_sep = float("inf")
    for source in sources:
        sep = _separation_arcsec(ra, dec, source["ra"], source["dec"])
        if sep < best_sep:
            best = source
            best_sep = sep
    return best, best_sep


def annotate_candidates(
    candidates: list[dict],
    dataset: dict,
    *,
    max_sep_arcsec: float = 8.0,
    force: bool = False,
) -> dict:
    """Attach the nearest Gaia/WISE counterpart to every candidate."""
    grid = dataset.get("grid") or {}
    ra = grid.get("ra", dataset.get("ra"))
    dec = grid.get("dec", dataset.get("dec"))
    if ra is None or dec is None or not candidates:
        return {"available": False, "n_gaia": 0, "n_wise": 0}
    fov = float(grid.get("fov", dataset.get("fov", 0.35)) or 0.35)
    radius = max(0.08, min(1.5, fov / 2.0 + 0.08))
    sources = field_sources(float(ra), float(dec), radius, force=force)
    if not sources.get("available"):
        for candidate in candidates:
            candidate["catalog"] = {
                "available": False,
                "has_counterpart": None,
                "note": "Sin conexión con Gaia/WISE.",
            }
        return sources

    gaia = sources.get("gaia", [])
    wise = sources.get("wise", [])
    matched_gaia = 0
    matched_wise = 0
    for candidate in candidates:
        position = candidate.get("position") or {}
        cra = position.get("ra")
        cdec = position.get("dec")
        if cra is None or cdec is None:
            candidate["catalog"] = {
                "available": True,
                "has_counterpart": None,
                "note": "Candidato sin coordenada celeste.",
            }
            continue
        g_source, g_sep = _nearest(gaia, float(cra), float(cdec))
        w_source, w_sep = _nearest(wise, float(cra), float(cdec))
        has_gaia = g_source is not None and g_sep <= max_sep_arcsec
        has_wise = w_source is not None and w_sep <= max_sep_arcsec
        if has_gaia:
            matched_gaia += 1
        if has_wise:
            matched_wise += 1
        candidate["catalog"] = {
            "available": True,
            "has_counterpart": bool(has_gaia or has_wise),
            "gaia": (
                {
                    "ra": g_source["ra"],
                    "dec": g_source["dec"],
                    "separation_arcsec": round(g_sep, 2),
                    "mag": g_source.get("mag"),
                    "parallax": g_source.get("parallax"),
                    "pmra": g_source.get("pmra"),
                    "pmdec": g_source.get("pmdec"),
                }
                if g_source is not None
                else None
            ),
            "wise": (
                {
                    "ra": w_source["ra"],
                    "dec": w_source["dec"],
                    "separation_arcsec": round(w_sep, 2),
                    "w1": w_source.get("w1"),
                    "w2": w_source.get("w2"),
                }
                if w_source is not None
                else None
            ),
            "gaia_separation_arcsec": round(g_sep, 2) if g_source else None,
            "wise_separation_arcsec": round(w_sep, 2) if w_source else None,
            "max_sep_arcsec": max_sep_arcsec,
            "note": (
                "Contrapartida en Gaia/WISE dentro de "
                f"{max_sep_arcsec:.1f}\"."
                if (has_gaia or has_wise)
                else "Sin contrapartida en Gaia/WISE dentro del radio de búsqueda."
            ),
        }

    return {
        "available": True,
        "n_gaia": len(gaia),
        "n_wise": len(wise),
        "n_candidates": len(candidates),
        "matched_gaia": matched_gaia,
        "matched_wise": matched_wise,
        "radius_deg": radius,
        "cached": bool(sources.get("cached")),
        "source": sources.get("source"),
    }


def field_report(ra: float, dec: float, radius_deg: float = 0.5) -> dict:
    """A compact, public summary of the reference catalogues for a field."""
    sources = field_sources(ra, dec, radius_deg)
    return {
        "available": sources.get("available"),
        "ra": sources.get("ra"),
        "dec": sources.get("dec"),
        "radius_deg": sources.get("radius_deg"),
        "n_gaia": len(sources.get("gaia", [])),
        "n_wise": len(sources.get("wise", [])),
        "cached": bool(sources.get("cached")),
        "errors": sources.get("errors", []),
        "source": sources.get("source"),
        "note": sources.get("note"),
    }
