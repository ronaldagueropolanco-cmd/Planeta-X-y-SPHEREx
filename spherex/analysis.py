"""Scientific analysis: coadds, difference imaging, spectra and metadata."""

from __future__ import annotations

import math
import warnings

import numpy as np
from scipy import ndimage
from astropy.wcs import WCS

from .bands import BANDS, WAVELENGTHS, nearest_band
from .frames import combine_frames

warnings.filterwarnings(
    "ignore", message="All-NaN slice encountered", category=RuntimeWarning
)


# ---------------------------------------------------------------------------
# Frame access helpers
# ---------------------------------------------------------------------------
def dataset_frames(dataset: dict, epoch_index: int | None = None) -> list[dict]:
    frames = dataset.get("frames", {})
    out = []
    for key, frame in frames.items():
        try:
            epoch = int(key.split(":", 1)[0])
        except ValueError:
            continue
        if epoch_index is None or epoch == epoch_index:
            out.append(frame)
    return out


def epoch_stack(dataset: dict, epoch_index: int) -> dict | None:
    frames = dataset_frames(dataset, epoch_index)
    if not frames:
        return None
    return combine_frames(frames)


def grid_wcs(dataset: dict) -> WCS:
    grid = dataset.get("grid") or {}
    npix = int(grid.get("npix", 192))
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [npix / 2.0 + 0.5, npix / 2.0 + 0.5]
    wcs.wcs.crval = [float(grid.get("ra", dataset.get("ra", 0.0))),
                     float(grid.get("dec", dataset.get("dec", 0.0)))]
    fov = float(grid.get("fov", dataset.get("fov", 0.2)))
    wcs.wcs.cdelt = [-fov / npix, fov / npix]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.cunit = ["deg", "deg"]
    return wcs


def pixel_scale_arcsec(dataset: dict) -> float:
    return float(dataset.get("pixel_scale", 6.15))


# ---------------------------------------------------------------------------
# Detection of moving objects, transients and variables
# ---------------------------------------------------------------------------
def band_arrays(dataset: dict, epoch_index: int,
                band_index: int) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Return the image and variance of one spectral channel for an epoch.

    A channel can be covered by more than one detector near the overlap
    regions, so the planes are combined with a NaN-aware median.
    """
    wl = WAVELENGTHS[band_index]
    half = _channel_halfwidth(band_index)
    images = []
    variances = []
    for frame in dataset_frames(dataset, epoch_index):
        wave = frame.get("wave")
        image = frame.get("image")
        if wave is None or image is None:
            continue
        mask = np.isfinite(wave) & (np.abs(wave - wl) <= half)
        if not mask.any():
            continue
        plane = np.full(image.shape, np.nan, dtype=np.float32)
        plane[mask] = image[mask]
        images.append(plane)

        variance = frame.get("variance")
        var_plane = np.full(image.shape, np.nan, dtype=np.float32)
        if variance is not None:
            var_plane[mask] = variance[mask]
        variances.append(var_plane)

    if not images:
        return None, None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        image = np.nanmedian(np.stack(images), axis=0)
        variance = (
            np.nanmedian(np.stack(variances), axis=0)
            if variances
            else np.full(image.shape, np.nan, dtype=np.float32)
        )
    return image, variance


def matched_pair(dataset: dict, epoch_a: int, epoch_b: int,
                 min_pixels: int = 64) -> dict | None:
    """Build epoch images from the spectral channels observed in *both* epochs.

    SPHEREx scans the same field at different detector offsets, so the same sky
    position is often recorded at a different wavelength on different dates.
    Subtracting full detector coadds then mixes spectral slices and leaves
    residuals at every bright star.  Here both epochs are resampled onto the
    same set of channels and only pixels covered by both are kept.
    """
    frames_a = dataset_frames(dataset, epoch_a)
    frames_b = dataset_frames(dataset, epoch_b)
    if not frames_a or not frames_b:
        return None
    shape = frames_a[0]["image"].shape

    acc_a = np.zeros(shape, dtype=np.float64)
    acc_b = np.zeros(shape, dtype=np.float64)
    weight_a = np.zeros(shape, dtype=np.float64)
    weight_b = np.zeros(shape, dtype=np.float64)
    used = []

    for band_index in range(len(WAVELENGTHS)):
        image_a, var_a = band_arrays(dataset, epoch_a, band_index)
        image_b, var_b = band_arrays(dataset, epoch_b, band_index)
        if image_a is None or image_b is None:
            continue
        ok = (
            np.isfinite(image_a)
            & np.isfinite(image_b)
            & np.isfinite(var_a)
            & np.isfinite(var_b)
            & (var_a > 0)
            & (var_b > 0)
        )
        if np.count_nonzero(ok) < min_pixels:
            continue
        wa = 1.0 / var_a[ok]
        wb = 1.0 / var_b[ok]
        acc_a[ok] += image_a[ok] * wa
        acc_b[ok] += image_b[ok] * wb
        weight_a[ok] += wa
        weight_b[ok] += wb
        used.append(band_index)

    valid = (weight_a > 0) & (weight_b > 0)
    if not used or np.count_nonzero(valid) < min_pixels:
        return None

    image_a = np.full(shape, np.nan, dtype=np.float32)
    image_b = np.full(shape, np.nan, dtype=np.float32)
    var_a = np.full(shape, np.nan, dtype=np.float32)
    var_b = np.full(shape, np.nan, dtype=np.float32)
    image_a[valid] = (acc_a[valid] / weight_a[valid]).astype(np.float32)
    image_b[valid] = (acc_b[valid] / weight_b[valid]).astype(np.float32)
    var_a[valid] = (1.0 / weight_a[valid]).astype(np.float32)
    var_b[valid] = (1.0 / weight_b[valid]).astype(np.float32)

    diff = np.where(valid, image_b - image_a, np.nan)
    variance = np.where(valid, var_a + var_b, np.nan)
    return {
        "image_a": image_a,
        "image_b": image_b,
        "diff": diff,
        "variance": variance,
        "variance_a": var_a,
        "variance_b": var_b,
        "bands": used,
        "valid_pixels": int(np.count_nonzero(valid)),
    }


def detect_candidates(dataset: dict, epoch_a: int, epoch_b: int,
                      threshold_sigma: float = 4.0, min_area: int = 3) -> dict:
    stack_a = epoch_stack(dataset, epoch_a)
    stack_b = epoch_stack(dataset, epoch_b)
    if stack_a is None or stack_b is None:
        return {"error": "Épocas sin datos", "candidates": [], "total_candidates": 0}

    matched = None
    if dataset.get("kind") != "demo":
        matched = matched_pair(dataset, epoch_a, epoch_b)

    if matched is not None:
        a = matched["image_a"]
        b = matched["image_b"]
        method = "matched-bands"
        bands_used = [int(index) + 1 for index in matched["bands"]]
        valid_pixels = matched["valid_pixels"]
    else:
        a = stack_a["image"]
        b = stack_b["image"]
        method = "coadd"
        bands_used = []
        valid_pixels = int(np.count_nonzero(np.isfinite(a) & np.isfinite(b)))
    valid = np.isfinite(a) & np.isfinite(b)
    if valid.sum() < 16:
        return {"error": "Solapamiento insuficiente entre épocas", "candidates": [],
                "total_candidates": 0}

    diff = np.where(valid, b - a, np.nan)
    values = diff[valid]
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median))) or 1.0
    sigma = 1.4826 * mad
    if sigma <= 0:
        sigma = float(np.std(values)) or 1.0

    # Combine the global scatter with the per-pixel variance maps so bright
    # stars with large formal errors are not flagged as high-significance.
    variance = np.full(diff.shape, np.nan, dtype=np.float64)
    if matched is not None:
        combined = np.asarray(matched["variance"], dtype=np.float64)
        variance[valid] = np.maximum(combined[valid], 0.0)
    else:
        var_a = stack_a.get("variance")
        var_b = stack_b.get("variance")
        if var_a is not None and var_b is not None:
            combined = np.asarray(var_a, dtype=np.float64) + np.asarray(var_b, dtype=np.float64)
            variance[valid] = np.maximum(combined[valid], 0.0)
    sigma_pixel = np.sqrt(np.maximum(variance, sigma * sigma))

    significance = np.zeros_like(diff)
    significance[valid] = (diff[valid] - median) / sigma_pixel[valid]

    pos_mask = (significance > threshold_sigma) & valid
    neg_mask = (significance < -threshold_sigma) & valid

    pos_labeled, n_pos = ndimage.label(pos_mask)
    neg_labeled, n_neg = ndimage.label(neg_mask)

    def components(labeled, count, source, sign):
        out = []
        if count == 0:
            return out
        centers = ndimage.center_of_mass(np.nan_to_num(source), labeled, range(1, count + 1))
        areas = ndimage.sum(np.ones_like(source), labeled, range(1, count + 1))
        for idx, (cy, cx) in enumerate(centers, 1):
            if not np.isfinite(cy) or not np.isfinite(cx) or areas[idx - 1] < min_area:
                continue
            # Sample the brightest component pixel; the centroid may fall on an
            # invalid (NaN) neighbour for thin or diagonal sources.
            ys, xs = np.where(labeled == idx)
            if ys.size == 0:
                continue
            peak = int(np.argmax(np.abs(significance[ys, xs])))
            iy, ix = int(ys[peak]), int(xs[peak])
            if not (0 <= iy < source.shape[0] and 0 <= ix < source.shape[1]):
                continue
            if not (np.isfinite(a[iy, ix]) and np.isfinite(b[iy, ix])):
                continue
            out.append({
                "x": round(float(cx), 2),
                "y": round(float(cy), 2),
                "snr": round(float(abs(significance[iy, ix])), 1),
                "flux_a": round(float(a[iy, ix]), 5),
                "flux_b": round(float(b[iy, ix]), 5),
                "delta": round(float(diff[iy, ix]), 5),
                "area": int(areas[idx - 1]),
                "matched": False,
            })
        return out

    pos = components(pos_labeled, n_pos, diff, 1)
    neg = components(neg_labeled, n_neg, -diff, -1)

    wcs = grid_wcs(dataset)
    scale = pixel_scale_arcsec(dataset)
    dt_days = 1.0
    try:
        mjd_a = float(dataset["epochs"][epoch_a]["mjd"])
        mjd_b = float(dataset["epochs"][epoch_b]["mjd"])
        dt_days = max(abs(mjd_b - mjd_a), 1e-3)
    except Exception:
        pass

    def world(x, y):
        try:
            sky = wcs.pixel_to_world(x, y)
            return round(float(sky.ra.deg), 5), round(float(sky.dec.deg), 5)
        except Exception:
            return None, None

    candidates = []

    # Dipoles: a source at epoch A (negative) that moved by epoch B (positive).
    # A real moving object keeps roughly the same brightness and size, so only
    # pair lobes with consistent flux and area.  Without this, every residual
    # from a bright star can be paired with a nearby artefact.
    # Cap the search radius by a generous proper-motion limit (5 arcsec/day)
    # instead of a fixed fraction of the field.
    max_pair_distance = min(160.0, max(6.0, dt_days * 5.0 / max(scale, 1e-6)))
    max_flux_ratio = 4.0
    max_area_ratio = 5.0
    for p in pos:
        best = None
        best_dist = 1e9
        best_score = 1e9
        for n in neg:
            if n["matched"]:
                continue
            dist = math.hypot(p["x"] - n["x"], p["y"] - n["y"])
            if not 4.0 <= dist <= max_pair_distance:
                continue
            flux_p = abs(float(p["flux_b"]))
            flux_n = abs(float(n["flux_a"]))
            if flux_p <= 0 or flux_n <= 0:
                continue
            flux_ratio = max(flux_p, flux_n) / min(flux_p, flux_n)
            if flux_ratio > max_flux_ratio:
                continue
            area_ratio = max(p["area"], n["area"]) / max(min(p["area"], n["area"]), 1)
            if area_ratio > max_area_ratio:
                continue
            score = dist * (1.0 + 0.45 * math.log(flux_ratio))
            if score < best_score:
                best, best_dist, best_score = n, dist, score
        if best is None:
            continue
        best["matched"] = True
        p["matched"] = True
        dx = p["x"] - best["x"]
        dy = p["y"] - best["y"]
        distance_arcsec = best_dist * scale
        angle = math.degrees(math.atan2(dy, dx))
        ra, dec = world(p["x"], p["y"])
        ra_a, dec_a = world(best["x"], best["y"])
        candidates.append({
            "id": None,
            "type": "moving",
            "type_label": "Objeto en movimiento",
            "confidence": "Dipolo confirmado",
            "position": {"x": p["x"], "y": p["y"], "ra": ra, "dec": dec},
            "position_a": {"x": best["x"], "y": best["y"], "ra": ra_a, "dec": dec_a},
            "snr": max(p["snr"], best["snr"]),
            "flux_a": best["flux_a"],
            "flux_b": p["flux_b"],
            "delta_flux": p["delta"],
            "motion": {
                "dx_px": round(dx, 2),
                "dy_px": round(dy, 2),
                "arcsec": round(distance_arcsec, 1),
                "angle_deg": round(angle, 1),
                "rate_arcsec_day": round(distance_arcsec / dt_days, 3),
                "baseline_days": round(dt_days, 2),
            },
            "note": (
                f"Desplazamiento aparente de {distance_arcsec:.1f}\" en {dt_days:.1f} días "
                f"({distance_arcsec / dt_days:.2f}\"/día). Compatible con un asteroide o cometa."
            ),
        })

    # Positive sources without a counterpart: transients or variables.
    for p in pos:
        if p["matched"]:
            continue
        ra, dec = world(p["x"], p["y"])
        increase = (p["flux_b"] - p["flux_a"]) / max(abs(p["flux_a"]), 1e-6)
        if p["flux_a"] < 3.0 * sigma or increase > 2.0:
            kind, label = "transient", "Transitorio / Supernova"
            note = "Aparición de flujo sin contraparte en la época A."
        else:
            kind, label = "variable", "Estrella variable"
            note = f"Variación de brillo de {increase * 100:+.0f}% en una posición fija."
        candidates.append({
            "id": None,
            "type": kind,
            "type_label": label,
            "confidence": "Alta" if p["snr"] > 6 else "Media",
            "position": {"x": p["x"], "y": p["y"], "ra": ra, "dec": dec},
            "position_a": {"x": p["x"], "y": p["y"], "ra": ra, "dec": dec},
            "snr": p["snr"],
            "flux_a": p["flux_a"],
            "flux_b": p["flux_b"],
            "delta_flux": p["delta"],
            "motion": None,
            "note": note,
        })

    candidates.sort(key=lambda c: c["snr"], reverse=True)
    for idx, cand in enumerate(candidates, 1):
        prefix = {"moving": "MOV", "transient": "TRN", "variable": "VAR"}[cand["type"]]
        cand["id"] = f"SPX-{prefix}-{idx:03d}"

    return {
        "noise_sigma": round(sigma, 6),
        "threshold_sigma": threshold_sigma,
        "baseline_days": round(dt_days, 2),
        "total_candidates": len(candidates),
        "candidates": candidates[:60],
        "method": method,
        "matched_bands": bands_used,
        "matched_pixels": valid_pixels,
        "spectral_note": (
            f"Diferencia en {len(bands_used)} canales observados en ambas épocas "
            f"({valid_pixels} píxeles en común)."
            if method == "matched-bands"
            else "Diferencia sobre el coadd completo de detectores."
        ),
    }


# ---------------------------------------------------------------------------
# Spectra
# ---------------------------------------------------------------------------
def _aperture_pixels(wcs: WCS, ra: float, dec: float, radius_px: float, shape) -> tuple[slice, slice]:
    try:
        x, y = wcs.world_to_pixel_values(ra, dec)
    except Exception:
        return slice(0, 0), slice(0, 0)
    if not (np.isfinite(x) and np.isfinite(y)):
        return slice(0, 0), slice(0, 0)
    y0 = max(0, int(math.floor(y - radius_px)))
    y1 = min(shape[0], int(math.ceil(y + radius_px + 1)))
    x0 = max(0, int(math.floor(x - radius_px)))
    x1 = min(shape[1], int(math.ceil(x + radius_px + 1)))
    return slice(y0, y1), slice(x0, x1)


def extract_spectrum(dataset: dict, ra: float, dec: float,
                     epoch_index: int | None = None, radius_px: float = 2.2) -> dict:
    if dataset.get("kind") == "demo":
        return _demo_spectrum(dataset, ra, dec, epoch_index)

    wcs = grid_wcs(dataset)
    native = []
    for key, frame in dataset.get("frames", {}).items():
        try:
            epoch = int(key.split(":", 1)[0])
        except ValueError:
            continue
        if epoch_index is not None and epoch != epoch_index:
            continue
        ys, xs = _aperture_pixels(wcs, ra, dec, radius_px, frame["image"].shape)
        if ys.stop <= ys.start or xs.stop <= xs.start:
            continue
        image = frame["image"][ys, xs]
        wave = frame["wave"][ys, xs]
        variance = frame["variance"][ys, xs]
        ok = np.isfinite(image) & np.isfinite(wave)
        if ok.sum() < 1:
            continue
        flux = float(np.median(image[ok]))
        wl = float(np.median(wave[ok]))
        if not np.isfinite(wl):
            continue
        sigma = float(np.sqrt(np.nanmedian(variance[ok]))) if np.isfinite(variance[ok]).any() else 0.0
        native.append({
            "wavelength_um": round(wl, 4),
            "flux": round(flux, 6),
            "uncertainty": round(sigma, 6),
            "detector": frame["meta"].get("detector", "?"),
            "epoch": epoch,
            "date": dataset["epochs"][epoch]["date"] if epoch < len(dataset.get("epochs", [])) else "",
        })

    native.sort(key=lambda p: p["wavelength_um"])

    # Aggregate native samples onto the 102 reference channels.
    buckets: dict[int, list[dict]] = {}
    for point in native:
        index = nearest_band(point["wavelength_um"])
        if index is None:
            continue
        buckets.setdefault(index, []).append(point)

    channels = []
    for index, points in buckets.items():
        weights = [1.0 / max(p["uncertainty"], 1e-9) ** 2 for p in points]
        flux = sum(p["flux"] * w for p, w in zip(points, weights)) / sum(weights)
        channels.append({
            "index": index,
            "channel": index + 1,
            "wavelength_um": WAVELENGTHS[index],
            "flux": round(float(flux), 6),
            "n_samples": len(points),
            "detector": points[0]["detector"],
        })

    channels.sort(key=lambda c: c["index"])
    coverage = len({c["index"] for c in channels})
    return {
        "ra": round(ra, 5),
        "dec": round(dec, 5),
        "kind": "archive",
        "channels": channels,
        "native": native[:400],
        "coverage": coverage,
        "coverage_total": len(WAVELENGTHS),
        "features": spectral_features(),
    }


def _demo_spectrum(dataset: dict, ra: float, dec: float, epoch_index: int | None) -> dict:
    cube = dataset.get("cube")
    if not cube:
        try:
            from .demo import ensure_demo_cube

            cube = ensure_demo_cube(dataset)
        except Exception:  # noqa: BLE001 - demo cube is optional
            cube = None
    if not cube:
        return {"channels": [], "native": [], "coverage": 0, "coverage_total": 102}
    epoch = 0 if epoch_index is None else epoch_index
    epoch = max(0, min(epoch, len(cube) - 1))
    volume = cube[epoch]
    wcs = grid_wcs(dataset)
    try:
        x, y = wcs.world_to_pixel_values(ra, dec)
        ix, iy = int(round(x)), int(round(y))
    except Exception:
        ix = iy = 0
    h, w = volume.shape[1:]
    ix = max(0, min(ix, w - 1))
    iy = max(0, min(iy, h - 1))
    values = volume[:, iy, ix]
    channels = [
        {
            "index": i,
            "channel": i + 1,
            "wavelength_um": WAVELENGTHS[i],
            "flux": round(float(values[i]), 6),
            "n_samples": 1,
            "detector": BANDS[i]["detector"],
        }
        for i in range(len(WAVELENGTHS))
        if np.isfinite(values[i])
    ]
    return {
        "ra": round(ra, 5),
        "dec": round(dec, 5),
        "kind": "demo",
        "channels": channels,
        "native": [
            {"wavelength_um": c["wavelength_um"], "flux": c["flux"],
             "detector": c["detector"], "epoch": epoch,
             "date": dataset["epochs"][epoch]["date"] if epoch < len(dataset["epochs"]) else ""}
            for c in channels
        ],
        "coverage": len(channels),
        "coverage_total": len(WAVELENGTHS),
        "features": spectral_features(),
    }


def spectral_features() -> list[dict]:
    return [
        {"name": "Hielo de agua", "formula": "H₂O", "wavelength_um": 3.05},
        {"name": "Hidrocarburos aromáticos", "formula": "PAH", "wavelength_um": 3.30},
        {"name": "Hielo de CO₂", "formula": "CO₂", "wavelength_um": 4.27},
        {"name": "Hielo de CO", "formula": "CO", "wavelength_um": 4.67},
    ]


# ---------------------------------------------------------------------------
# Band explorer
# ---------------------------------------------------------------------------
def band_coverage(dataset: dict, epoch_index: int | None = None) -> list[dict]:
    if dataset.get("kind") == "demo":
        return [
            {"index": b["index"], "channel": b["channel"],
             "wavelength_um": b["wavelength_um"], "detector": b["detector"],
             "covered": True, "samples": 1}
            for b in BANDS
        ]

    counts = np.zeros(len(WAVELENGTHS), dtype=int)
    for frame in dataset_frames(dataset, epoch_index):
        wave = frame.get("wave")
        if wave is None:
            continue
        valid = np.isfinite(wave)
        if not valid.any():
            continue
        values = wave[valid]
        for index, wl in enumerate(WAVELENGTHS):
            half = _channel_halfwidth(index)
            counts[index] += int(np.count_nonzero(np.abs(values - wl) <= half))

    grid = dataset.get("grid") or {}
    npix = int(grid.get("npix", dataset.get("npix", 192)))
    # A channel touched by only a handful of edge pixels is not useful for
    # display; require at least 0.5 % of the grid to call it covered.
    min_samples = max(16, int(0.005 * npix * npix))
    return [
        {
            "index": b["index"],
            "channel": b["channel"],
            "wavelength_um": b["wavelength_um"],
            "detector": b["detector"],
            "covered": bool(counts[b["index"]] >= min_samples),
            "samples": int(counts[b["index"]]),
            "min_samples": min_samples,
        }
        for b in BANDS
    ]


def _channel_halfwidth(index: int) -> float:
    if index + 1 < len(WAVELENGTHS):
        return abs(WAVELENGTHS[index + 1] - WAVELENGTHS[index]) / 2.0 + 1e-4
    return 0.02


def band_image(dataset: dict, band_index: int,
               epoch_index: int | None = None) -> np.ndarray | None:
    if dataset.get("kind") == "demo":
        cube = dataset.get("cube")
        if not cube:
            return None
        epoch = 0 if epoch_index is None else max(0, min(epoch_index, len(cube) - 1))
        volume = cube[epoch]
        if band_index >= volume.shape[0]:
            return None
        return volume[band_index]

    wl = WAVELENGTHS[band_index]
    half = _channel_halfwidth(band_index)
    planes = []
    for frame in dataset_frames(dataset, epoch_index):
        wave = frame.get("wave")
        image = frame.get("image")
        if wave is None or image is None:
            continue
        mask = np.isfinite(wave) & (np.abs(wave - wl) <= half)
        if not mask.any():
            continue
        plane = np.full(image.shape, np.nan, dtype=np.float32)
        plane[mask] = image[mask]
        planes.append(plane)
    if not planes:
        return None
    stack = np.stack(planes)
    return np.nanmedian(stack, axis=0)


# ---------------------------------------------------------------------------
# Quality / contamination metadata
# ---------------------------------------------------------------------------
def quality_report(dataset: dict, epoch_index: int) -> dict:
    frames = dataset_frames(dataset, epoch_index)
    if not frames:
        return {"available": False}

    stack = combine_frames(frames)
    image = stack["image"]
    flags = stack["flags"]
    variance = stack["variance"]
    valid = np.isfinite(image)
    values = image[valid]
    if values.size == 0:
        return {"available": False}

    median = float(np.median(values))
    sigma = float(1.4826 * np.median(np.abs(values - median))) or 1.0
    # Only the low-order bits describe pixel quality.  Bit 21 is set on most
    # SPHEREx pixels for bookkeeping and must not be counted as contamination.
    quality_mask = 0b1111
    quality_bits = flags[valid].astype(np.int64) & quality_mask
    flagged = float(np.count_nonzero(quality_bits > 0) / max(values.size, 1))
    informational = float(
        np.count_nonzero(flags[valid].astype(np.int64) & ~quality_mask)
        / max(values.size, 1)
    )
    cosmic = float(np.count_nonzero(values > median + 8 * sigma) / max(values.size, 1))
    saturation = float(np.count_nonzero(values >= np.percentile(values, 99.95)) / max(values.size, 1))

    zodi_level = None
    for frame in frames:
        if frame.get("meta", {}).get("zodi_median") is not None:
            zodi_level = frame["meta"]["zodi_median"]
            break

    contamination = min(100.0, (flagged * 60.0 + cosmic * 220.0 + saturation * 30.0))
    if zodi_level and median > 0:
        contamination = min(100.0, contamination + 10.0 * (zodi_level / max(median, 1e-9)))

    return {
        "available": True,
        "background_median": round(median, 6),
        "background_sigma": round(sigma, 6),
        "flagged_fraction_pct": round(flagged * 100.0, 2),
        "informational_flag_pct": round(informational * 100.0, 2),
        "quality_flag_mask": quality_mask,
        "cosmic_ray_proxy_pct": round(cosmic * 100.0, 2),
        "saturation_proxy_pct": round(saturation * 100.0, 2),
        "zodiacal_level": round(float(zodi_level), 6) if zodi_level is not None else None,
        "contamination_index": round(contamination, 1),
        "contamination_flag": "ALTA" if contamination > 35 else ("MODERADA" if contamination > 12 else "BAJA"),
        "n_detectors": len(frames),
        "valid_fraction_pct": round(100.0 * values.size / image.size, 1),
        "snr_gain": round(float(np.sqrt(len(frames))), 2),
        "variance_median": round(float(np.nanmedian(variance)), 8),
    }
