"""High-level ingestion pipeline: archive query -> cutouts -> common grid."""

from __future__ import annotations

import hashlib
import io
import os

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from PIL import Image

from . import archive, frames, store
from .bands import WAVELENGTHS

UPLOAD_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "uploads", "cache", "uploads")
)
os.makedirs(UPLOAD_ROOT, exist_ok=True)

DETECTOR_PREFERENCE = ["D1", "D2", "D4", "D5", "D3", "D6"]


class PipelineError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Epoch / detector selection
# ---------------------------------------------------------------------------
def select_epochs(epochs: list[dict], max_epochs: int = 2,
                  epoch_days: list[int] | None = None) -> list[dict]:
    if not epochs:
        return []
    if epoch_days:
        wanted = {int(day) for day in epoch_days}
        chosen = [e for e in epochs if int(e["day"]) in wanted]
        if chosen:
            return sorted(chosen, key=lambda e: e["mjd"])[: max(1, max_epochs)]

    if max_epochs <= 1 or len(epochs) <= max_epochs:
        if max_epochs <= 1:
            best = max(epochs, key=lambda e: (len(e.get("detectors", [])), -e["mjd"]))
            return [best]
        return sorted(epochs, key=lambda e: e["mjd"])

    good = [e for e in epochs if len(e.get("detectors", [])) >= 3] or epochs
    if max_epochs == 2:
        return [good[0], good[-1]]

    step = (len(good) - 1) / (max_epochs - 1)
    picked = [good[round(i * step)] for i in range(max_epochs)]
    # De-duplicate while keeping order.
    unique = []
    for epoch in picked:
        if epoch not in unique:
            unique.append(epoch)
    return unique


def choose_detectors(epoch: dict, max_detectors: int = 4,
                     detectors: list[str] | None = None) -> list[str]:
    available = list(epoch.get("detectors", []))
    if detectors:
        wanted = [d for d in detectors if d in available]
        if wanted:
            return wanted[:max_detectors]
    ordered = [d for d in DETECTOR_PREFERENCE if d in available]
    ordered += [d for d in available if d not in ordered]
    return ordered[: max(1, max_detectors)]


# ---------------------------------------------------------------------------
# Archive ingestion
# ---------------------------------------------------------------------------
def build_archive_dataset(
    ra: float,
    dec: float,
    *,
    fov: float = 0.35,
    npix: int = 192,
    max_epochs: int = 2,
    max_detectors: int = 4,
    epoch_days: list[int] | None = None,
    detectors: list[str] | None = None,
    cutout_size: float | None = None,
    label: str | None = None,
    progress=None,
) -> dict:
    """Download real SPHEREx level-2 cutouts and assemble a comparable dataset."""
    ra = float(ra)
    dec = float(dec)
    fov = max(0.03, min(float(fov), 2.0))
    npix = max(64, min(int(npix), 512))
    cutout_size = float(cutout_size or max(0.5, fov * 1.7))

    if progress:
        progress("Consultando el archivo IRSA…")
    observations = archive.query_field(ra, dec, limit=20000)
    if not observations:
        raise PipelineError(
            "El archivo no devolvió observaciones SPHEREx para esa posición."
        )

    epochs = archive.group_epochs(observations)
    selected = select_epochs(epochs, max_epochs=max_epochs, epoch_days=epoch_days)
    if not selected:
        raise PipelineError("No hay épocas utilizables para esa posición.")

    target_wcs = frames.common_wcs(ra, dec, fov, npix)
    shape = (npix, npix)
    stored_frames: dict[str, dict] = {}
    stored_epochs: list[dict] = []

    for epoch_index, epoch in enumerate(selected):
        epoch_dets = choose_detectors(
            epoch, max_detectors=max_detectors, detectors=detectors
        )
        if progress:
            progress(
                f"Época {epoch['date']}: descargando {len(epoch_dets)} detectores "
                f"({', '.join(epoch_dets)})…"
            )

        frame_count = 0
        for obs in epoch.get("observations", []):
            if obs["detector"] not in epoch_dets:
                continue
            try:
                path = archive.download_cutout(obs["uri"], ra, dec, cutout_size)
                cutout = frames.load_cutout(path)
                warped = frames.reproject_cutout(cutout, target_wcs, shape)
            except Exception as exc:  # network / FITS problems: skip this frame
                if progress:
                    progress(f"  · {obs['detector']} omitido: {exc}")
                continue

            valid = np.isfinite(warped["image"])
            if valid.sum() < 0.002 * npix * npix:
                if progress:
                    progress(f"  · {obs['detector']} sin solapamiento útil")
                continue

            meta = dict(warped.get("meta", {}))
            meta.update(
                {
                    "detector": obs["detector"],
                    "obs_id": obs.get("id"),
                    "date_obs": obs.get("date"),
                    "mjd": obs.get("mjd"),
                    "epoch": epoch_index,
                    "epoch_day": epoch["day"],
                    "uri": obs.get("uri"),
                    "valid_fraction": round(float(valid.mean()), 4),
                }
            )
            warped["meta"] = meta
            stored_frames[f"{epoch_index}:{obs['detector']}"] = warped
            frame_count += 1

        if frame_count:
            stored_epochs.append(
                {
                    "index": len(stored_epochs),
                    "original_index": epoch_index,
                    "day": epoch["day"],
                    "mjd": epoch["mjd"],
                    "date": epoch["date"],
                    "detectors": sorted(
                        {
                            key.split(":", 1)[1]
                            for key in stored_frames
                            if key.startswith(f"{epoch_index}:")
                        }
                    ),
                    "observations": [
                        obs
                        for obs in epoch.get("observations", [])
                        if obs["detector"] in epoch_dets
                    ],
                }
            )

    # Re-index frames after epochs without usable data were dropped.
    if not stored_frames:
        raise PipelineError(
            "Los recortes descargados no cubren el campo solicitado. "
            "Prueba con un FOV menor o una posición distinta."
        )

    final_frames: dict[str, dict] = {}
    for new_index, epoch in enumerate(stored_epochs):
        old_index = epoch.pop("original_index", epoch["index"])
        for key, frame in stored_frames.items():
            old_epoch, detector = key.split(":", 1)
            if int(old_epoch) == old_index:
                frame["meta"]["epoch"] = new_index
                final_frames[f"{new_index}:{detector}"] = frame
        epoch["index"] = new_index

    # Reprojection leaves small astrometric residuals between epochs; register
    # every frame to the first one so difference imaging is not dominated by
    # shifted point sources.
    ordered = [
        final_frames[key]
        for key in sorted(
            final_frames,
            key=lambda k: (int(k.split(":", 1)[0]), k.split(":", 1)[1]),
        )
    ]
    if len(ordered) > 1 and progress:
        progress("Alineando frames por correlación cruzada…")
    registration = frames.align_frames(ordered, progress=progress)

    mjds = [float(e["mjd"]) for e in stored_epochs]
    digest = hashlib.sha1(
        f"{ra:.5f}|{dec:.5f}|{fov:.3f}|{','.join(str(e['day']) for e in stored_epochs)}".encode()
    ).hexdigest()[:10]

    dataset = {
        "id": f"archive_{digest}",
        "kind": "archive",
        "label": label or f"Campo SPHEREx RA {ra:.3f} Dec {dec:+.3f}",
        "tag": "Archivo IRSA",
        "description": (
            "Recortes level-2 reales del archivo NASA/IPAC IRSA, reproyectados a "
            "una cuadrícula norte-arriba común y coadicionados por varianza inversa."
        ),
        "object_type": "Campo SPHEREx level-2",
        "ra": ra,
        "dec": dec,
        "fov": fov,
        "npix": npix,
        "pixel_scale": round(fov / npix * 3600.0, 3),
        "bands": WAVELENGTHS,
        "epochs": stored_epochs,
        "frames": final_frames,
        "grid": {"ra": ra, "dec": dec, "fov": fov, "npix": npix},
        "source": {
            "archive": "NASA/IPAC IRSA",
            "service": "SPHEREx level-2 cutouts (TAP + IBE)",
            "query": f"RA={ra:.5f}, Dec={dec:+.5f}",
            "cutout_size_deg": round(cutout_size, 3),
            "n_observations": len(observations),
            "mjd_range": [round(min(mjds), 3), round(max(mjds), 3)],
            "detectors": sorted(
                {key.split(":", 1)[1] for key in final_frames}
            ),
            "registration": registration,
            "real_data": True,
        },
        "cube": None,
    }
    store.put(dataset)
    if progress:
        progress(
            f"Listo: {len(final_frames)} frames reales en "
            f"{len(stored_epochs)} épocas."
        )
    return dataset


# ---------------------------------------------------------------------------
# Upload ingestion
# ---------------------------------------------------------------------------
def _generic_wcs(shape, ra, dec, pixel_scale_arcsec=6.15) -> WCS:
    ny, nx = shape
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [nx / 2.0 + 0.5, ny / 2.0 + 0.5]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.cdelt = [-pixel_scale_arcsec / 3600.0, pixel_scale_arcsec / 3600.0]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.cunit = ["deg", "deg"]
    return wcs


def _load_generic(path: str) -> frames.Cutout:
    """Load a non-SPHEREx FITS image or a plain raster as a one-plane cutout."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".fits", ".fit", ".fts"):
        found = False
        with fits.open(path, memmap=False) as hdul:
            for hdu in hdul:
                data = hdu.data
                if data is None:
                    continue
                while data.ndim > 2:
                    data = data[0]
                if data.ndim != 2:
                    continue
                image = np.asarray(data, dtype=np.float32)
                header = hdu.header
                try:
                    wcs = WCS(header)
                    if not wcs.has_celestial:
                        raise ValueError("no celestial WCS")
                except Exception:
                    ra = float(header.get("RA", header.get("CRVAL1", 0.0)) or 0.0)
                    dec = float(header.get("DEC", header.get("CRVAL2", 0.0)) or 0.0)
                    wcs = _generic_wcs(image.shape, ra, dec)
                found = True
                break
        if not found:
            raise PipelineError("El FITS no contiene una imagen 2D utilizable.")
    else:
        pil = Image.open(path).convert("L")
        image = np.asarray(pil, dtype=np.float32)
        wcs = _generic_wcs(image.shape, 0.0, 0.0)

    shape = image.shape
    nan = np.full(shape, np.nan, dtype=np.float32)
    return frames.Cutout(
        image=image,
        variance=np.full(shape, 1.0, dtype=np.float32),
        flags=np.zeros(shape, dtype=np.int32),
        zodi=np.zeros(shape, dtype=np.float32),
        wave=nan.copy(),
        bandwidth=nan.copy(),
        wcs=wcs,
        meta={
            "detector": "UPLOAD",
            "obs_id": os.path.basename(path),
            "date_obs": "",
            "mjd": float("nan"),
            "exptime": 0.0,
            "bunit": "arbitrary",
            "psf_fwhm": 0.0,
            "pixel_scale": 6.15,
            "shape": [int(shape[0]), int(shape[1])],
            "wave_min": None,
            "wave_max": None,
            "zodi_median": None,
            "flags_fraction": 0.0,
            "ra": float(wcs.wcs.crval[0]),
            "dec": float(wcs.wcs.crval[1]),
        },
    )


def build_upload_dataset(path: str, *, label: str | None = None,
                         ra: float | None = None, dec: float | None = None,
                         date: str | None = None, mjd: float | None = None) -> dict:
    try:
        cutout = frames.load_cutout(path)
    except Exception:
        cutout = _load_generic(path)

    meta = cutout.meta
    center_ra = float(ra if ra is not None else meta.get("ra", 0.0))
    center_dec = float(dec if dec is not None else meta.get("dec", 0.0))
    scale = float(meta.get("pixel_scale") or 6.15)
    fov = max(0.05, min(2.0, max(cutout.image.shape) * scale / 3600.0))
    npix = 192

    target = frames.common_wcs(center_ra, center_dec, fov, npix)
    warped = frames.reproject_cutout(cutout, target, (npix, npix))
    warped["meta"].update(
        {
            "detector": "UPLOAD",
            "epoch": 0,
            "date_obs": date or meta.get("date_obs") or "",
            "mjd": mjd if mjd is not None else meta.get("mjd"),
            "upload": True,
        }
    )

    dataset = {
        "id": store.new_id("upload"),
        "kind": "upload",
        "label": label or os.path.basename(path),
        "tag": "Carga propia",
        "description": "Archivo FITS o imagen aportada por el usuario.",
        "object_type": "Dato de usuario",
        "ra": center_ra,
        "dec": center_dec,
        "fov": fov,
        "npix": npix,
        "pixel_scale": round(fov / npix * 3600.0, 3),
        "bands": WAVELENGTHS,
        "epochs": [
            {
                "index": 0,
                "day": int(mjd) if mjd else 0,
                "mjd": mjd or 0.0,
                "date": date or meta.get("date_obs") or "—",
                "detectors": ["UPLOAD"],
                "observations": [],
            }
        ],
        "frames": {"0:UPLOAD": warped},
        "grid": {"ra": center_ra, "dec": center_dec, "fov": fov, "npix": npix},
        "source": {
            "archive": "Carga propia",
            "service": "Archivo local",
            "query": os.path.basename(path),
            "real_data": True,
        },
        "cube": None,
    }
    store.put(dataset)
    return dataset


def save_upload(raw: bytes, filename: str) -> str:
    digest = hashlib.sha1(raw[:4096] + filename.encode("utf-8")).hexdigest()[:12]
    ext = os.path.splitext(filename)[1].lower() or ".bin"
    path = os.path.join(UPLOAD_ROOT, f"{digest}{ext}")
    if not os.path.exists(path):
        with open(path, "wb") as handle:
            handle.write(raw)
    return path
