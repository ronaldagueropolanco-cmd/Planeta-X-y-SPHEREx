"""FITS handling, WCS reprojection and frame assembly for SPHEREx data."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
import astropy.units as u
from scipy import ndimage
from scipy.signal import fftconvolve

try:
    from reproject import reproject_interp
except Exception:  # pragma: no cover - optional dependency
    reproject_interp = None


_SIP_KEY = re.compile(r"^(A|B|AP|BP)_(ORDER|DMAX|\d+_\d+)$")


def _without_sip(header):
    """SPHEREx level-2 images are already resampled; SIP is not applied."""
    cleaned = header.copy()
    for key in list(cleaned.keys()):
        if _SIP_KEY.match(key):
            del cleaned[key]
    return cleaned


class FrameError(RuntimeError):
    pass


@dataclass
class Cutout:
    image: np.ndarray
    variance: np.ndarray
    flags: np.ndarray
    zodi: np.ndarray
    wave: np.ndarray
    bandwidth: np.ndarray
    wcs: WCS
    meta: dict = field(default_factory=dict)


def load_cutout(path: str) -> Cutout:
    """Read a SPHEREx level-2 MEF cutout and derive its wavelength map."""
    with fits.open(path, memmap=False) as hdul:
        if len(hdul) < 2 or hdul[1].data is None:
            raise FrameError("El FITS no contiene una extensión IMAGE")

        hdu = hdul[1]
        header = hdu.header
        image = np.asarray(hdu.data, dtype=np.float32)
        ny, nx = image.shape

        def extension(name: str) -> np.ndarray:
            for h in hdul:
                if h.name.upper() == name and h.data is not None:
                    arr = np.asarray(h.data, dtype=np.float32)
                    if arr.shape == image.shape:
                        return arr
            return np.zeros_like(image)

        variance = extension("VARIANCE")
        flags = extension("FLAGS").astype(np.int32)
        zodi = extension("ZODI")

        try:
            wcs = WCS(_without_sip(header), hdul)
        except Exception as exc:
            raise FrameError(f"WCS inválido: {exc}") from exc

        wave = np.full_like(image, np.nan)
        bandwidth = np.full_like(image, np.nan)
        try:
            wave_wcs = WCS(_without_sip(header), hdul, key="W")
            yy, xx = np.mgrid[0:ny, 0:nx]
            world = wave_wcs.pixel_to_world(xx, yy)
            if isinstance(world, (tuple, list)) and len(world) == 2:
                wave = np.asarray(world[0].to_value(u.micron), dtype=np.float32)
                bandwidth = np.asarray(world[1].to_value(u.micron), dtype=np.float32)
        except Exception:
            pass

        meta = {
            "detector": str(header.get("DETECTOR", "?")),
            "obs_id": str(header.get("OBSID", "")),
            "date_obs": str(header.get("DATE-OBS", "")),
            "mjd": float(header.get("MJD-OBS", np.nan)),
            "exptime": float(header.get("XPOSURE", header.get("EXPTIME", 0.0)) or 0.0),
            "bunit": str(header.get("BUNIT", "MJy/sr")),
            "psf_fwhm": float(header.get("PSF_FWHM", 0.0) or 0.0),
            "pixel_scale": float(np.mean(np.abs(proj_plane_pixel_scales(wcs))) * 3600.0),
            "shape": [int(ny), int(nx)],
            "wave_min": float(np.nanmin(wave)) if np.isfinite(wave).any() else None,
            "wave_max": float(np.nanmax(wave)) if np.isfinite(wave).any() else None,
            "zodi_median": float(np.nanmedian(zodi)) if np.isfinite(zodi).any() else None,
            "flags_fraction": float(np.count_nonzero(flags) / max(flags.size, 1)),
            "ra": float(wcs.wcs.crval[0]),
            "dec": float(wcs.wcs.crval[1]),
        }

    return Cutout(
        image=image,
        variance=variance,
        flags=flags,
        zodi=zodi,
        wave=wave,
        bandwidth=bandwidth,
        wcs=wcs,
        meta=meta,
    )


def common_wcs(ra: float, dec: float, fov_deg: float, npix: int = 192) -> WCS:
    """North-up TAN grid used to compare frames from different pointings."""
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [npix / 2.0 + 0.5, npix / 2.0 + 0.5]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.cdelt = [-fov_deg / npix, fov_deg / npix]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.cunit = ["deg", "deg"]
    return wcs


def reproject_cutout(cutout: Cutout, target_wcs: WCS, shape: tuple[int, int]) -> dict:
    """Reproject a cutout (image, variance, flags, wavelength) to a common grid."""
    if reproject_interp is None:
        raise FrameError("El paquete 'reproject' no está instalado")

    source_wcs = cutout.wcs
    try:
        source_wcs.sip = None  # already distortion corrected in level-2 products
    except Exception:
        pass

    def warp(array: np.ndarray, order: str = "bilinear") -> tuple[np.ndarray, np.ndarray]:
        try:
            data, footprint = reproject_interp(
                (array, source_wcs),
                target_wcs,
                shape_out=shape,
                order=order,
                return_footprint=True,
            )
        except Exception:
            return np.full(shape, np.nan, dtype=np.float32), np.zeros(shape, dtype=np.float32)
        return np.asarray(data, dtype=np.float32), np.asarray(footprint, dtype=np.float32)

    image, footprint = warp(cutout.image)
    variance, _ = warp(cutout.variance)
    flags, _ = warp(cutout.flags, order="nearest-neighbor")
    wave, _ = warp(cutout.wave)
    bandwidth, _ = warp(cutout.bandwidth)

    # Pixels outside the detector footprint are invalid.
    invalid = footprint <= 0.01
    image[invalid] = np.nan
    variance[invalid] = np.nan
    wave[invalid] = np.nan
    bandwidth[invalid] = np.nan
    flags[invalid] = -1

    return {
        "image": image,
        "variance": variance,
        "flags": np.nan_to_num(flags, nan=-1).astype(np.int32),
        "wave": wave,
        "bandwidth": bandwidth,
        "footprint": footprint,
        "meta": dict(cutout.meta),
    }


def _normalise_for_registration(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    filled = np.where(mask, image, np.nanmedian(image[mask]))
    low, high = np.percentile(filled[mask], [5.0, 99.0])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        low, high = float(np.min(filled[mask])), float(np.max(filled[mask]))
    if not np.isfinite(high) or high <= low:
        return np.zeros_like(filled)
    return np.clip((filled - low) / (high - low), 0.0, 1.0)


def _parabolic_offset(before: float, peak: float, after: float) -> float:
    denominator = before - 2.0 * peak + after
    if denominator == 0 or not np.isfinite(denominator):
        return 0.0
    return float(np.clip(0.5 * (before - after) / denominator, -0.75, 0.75))


def estimate_shift(image: np.ndarray, reference: np.ndarray,
                   max_shift: float = 12.0) -> tuple[float, float]:
    """Estimate the (dx, dy) shift that aligns ``image`` onto ``reference``.

    FFT cross-correlation of the normalised images, followed by a parabolic
    refinement of the correlation peak, gives a robust sub-pixel offset.  The
    offset is capped because the reprojected grid should already be close.
    """
    mask = np.isfinite(image) & np.isfinite(reference)
    if mask.mean() < 0.15 or mask.sum() < 64:
        return 0.0, 0.0

    a = _normalise_for_registration(np.asarray(reference, dtype=np.float32), mask)
    b = _normalise_for_registration(np.asarray(image, dtype=np.float32), mask)
    a = a - a.mean()
    b = b - b.mean()
    if not np.any(a) or not np.any(b):
        return 0.0, 0.0

    correlation = fftconvolve(b, a[::-1, ::-1], mode="same")
    cy, cx = np.unravel_index(int(np.argmax(correlation)), correlation.shape)
    center_y, center_x = correlation.shape[0] // 2, correlation.shape[1] // 2
    dx = float(cx - center_x)
    dy = float(cy - center_y)

    if 0 < cy < correlation.shape[0] - 1:
        dy += _parabolic_offset(
            correlation[cy - 1, cx], correlation[cy, cx], correlation[cy + 1, cx]
        )
    if 0 < cx < correlation.shape[1] - 1:
        dx += _parabolic_offset(
            correlation[cy, cx - 1], correlation[cy, cx], correlation[cy, cx + 1]
        )

    if abs(dx) > max_shift or abs(dy) > max_shift:
        return 0.0, 0.0
    # ``image`` is displaced by (+dx, +dy) relative to the reference.
    return -dx, -dy


def shift_frame(frame: dict, dx: float, dy: float) -> None:
    """Apply a sub-pixel translation to every array of a frame in place."""
    if abs(dx) < 1e-3 and abs(dy) < 1e-3:
        return
    shift = (float(dy), float(dx))
    for field in ("image", "variance", "wave"):
        array = frame.get(field)
        if array is None:
            continue
        frame[field] = ndimage.shift(
            array, shift, order=1, mode="constant", cval=np.nan
        ).astype(np.float32)
    if frame.get("bandwidth") is not None:
        frame["bandwidth"] = ndimage.shift(
            frame["bandwidth"], shift, order=1, mode="constant", cval=np.nan
        ).astype(np.float32)
    frame["flags"] = ndimage.shift(
        frame["flags"], shift, order=0, mode="constant", cval=-1
    ).astype(np.int32)
    frame["footprint"] = ndimage.shift(
        frame.get("footprint", np.ones_like(frame["image"])),
        shift, order=0, mode="constant", cval=0,
    ).astype(np.float32)
    meta = frame.setdefault("meta", {})
    meta["shift_px"] = [round(float(dx), 3), round(float(dy), 3)]


def align_frames(frames: list[dict], progress=None) -> list[float]:
    """Register frames to the first one via cross-correlation.

    Returns the applied shifts as ``[dx, dy]`` pairs for reporting.
    """
    if len(frames) < 2:
        return []
    reference = frames[0].get("image")
    if reference is None:
        return []
    applied: list[list[float]] = [[0.0, 0.0]]
    for frame in frames[1:]:
        image = frame.get("image")
        if image is None:
            applied.append([0.0, 0.0])
            continue
        dx, dy = estimate_shift(image, reference)
        shift_frame(frame, dx, dy)
        applied.append([round(dx, 3), round(dy, 3)])
        if progress and (abs(dx) > 0.05 or abs(dy) > 0.05):
            progress(
                f"  · {frame.get('meta', {}).get('detector', '?')} alineado "
                f"{dx:+.2f}, {dy:+.2f} px"
            )
    return applied


def combine_frames(frames: list[dict]) -> dict:
    """Inverse-variance weighted coadd of several detector frames."""
    if not frames:
        raise FrameError("No hay frames para combinar")

    shape = frames[0]["image"].shape
    numerator = np.zeros(shape, dtype=np.float64)
    denominator = np.zeros(shape, dtype=np.float64)
    wave_sum = np.zeros(shape, dtype=np.float64)
    wave_weight = np.zeros(shape, dtype=np.float64)
    flags = np.full(shape, -1, dtype=np.int32)

    for frame in frames:
        image = frame["image"]
        variance = frame["variance"]
        valid = np.isfinite(image)
        weight = np.zeros(shape, dtype=np.float64)
        weight[valid] = 1.0 / np.maximum(variance[valid], 1e-8)
        weight[~np.isfinite(weight)] = 0.0
        numerator += np.nan_to_num(image) * weight
        denominator += weight

        wave = frame["wave"]
        ok = np.isfinite(wave)
        wave_sum[ok] += wave[ok]
        wave_weight[ok] += 1.0

        frame_flags = frame["flags"]
        flags = np.where(flags < 0, frame_flags, flags | np.maximum(frame_flags, 0))

    image = np.full(shape, np.nan, dtype=np.float32)
    good = denominator > 0
    image[good] = (numerator[good] / denominator[good]).astype(np.float32)
    variance = np.full(shape, np.nan, dtype=np.float32)
    variance[good] = (1.0 / denominator[good]).astype(np.float32)
    wave = np.full(shape, np.nan, dtype=np.float32)
    ok = wave_weight > 0
    wave[ok] = (wave_sum[ok] / wave_weight[ok]).astype(np.float32)

    return {
        "image": image,
        "variance": variance,
        "flags": flags,
        "wave": wave,
        "footprint": good.astype(np.float32),
        "meta": {
            "n_frames": len(frames),
            "detectors": [f["meta"].get("detector") for f in frames],
            "snr_improvement": round(float(np.sqrt(len(frames))), 2),
        },
    }


def frame_to_serializable(frame: dict) -> dict:
    return {
        "image": np.nan_to_num(frame["image"], nan=-9999.0).astype(np.float32),
        "variance": np.nan_to_num(frame["variance"], nan=-9999.0).astype(np.float32),
        "flags": frame["flags"].astype(np.int32),
        "wave": np.nan_to_num(frame["wave"], nan=-9999.0).astype(np.float32),
        "footprint": np.nan_to_num(frame.get("footprint", np.ones_like(frame["image"]))).astype(np.float32),
    }


def frame_from_serializable(data: dict, meta: dict) -> dict:
    def clean(arr, nan_value=-9999.0):
        arr = np.asarray(arr, dtype=np.float32)
        arr[arr == nan_value] = np.nan
        return arr

    return {
        "image": clean(data["image"]),
        "variance": clean(data["variance"]),
        "flags": np.asarray(data["flags"], dtype=np.int32),
        "wave": clean(data["wave"]),
        "footprint": np.asarray(data["footprint"], dtype=np.float32),
        "meta": meta,
    }
