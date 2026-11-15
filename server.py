"""SPHEREx Sky Time Viewer — Flask backend.

Real NASA/IPAC IRSA data are queried through TAP, downloaded as level-2 cutouts
and reprojected onto a common grid.  Demo datasets remain available when the
archive cannot be reached.
"""

from __future__ import annotations

import io
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np
from flask import Flask, Response, g, jsonify, request, send_file, stream_with_context
from flask.json.provider import DefaultJSONProvider

from spherex import (
    analysis,
    archive,
    assistant,
    auth,
    bands,
    catalog,
    config,
    crossmatch,
    db,
    demo,
    frames,
    model,
    pipeline,
    render,
    store,
)

VERSION = "2.3"


def _finite_json(value):
    """Replace NaN/Inf (invalid JSON) with null, recursively."""
    if isinstance(value, dict):
        return {key: _finite_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_json(item) for item in value]
    if isinstance(value, np.floating):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.ndarray):
        return _finite_json(value.tolist())
    return value


class SafeJSONProvider(DefaultJSONProvider):
    def dumps(self, obj, **kwargs):
        return super().dumps(_finite_json(obj), **kwargs)


app = Flask(__name__, static_folder="static", static_url_path="")
app.json = SafeJSONProvider(app)
app.config["MAX_CONTENT_LENGTH"] = 96 * 1024 * 1024
app.secret_key = config.secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
db.init()
store.ensure_demo_datasets()
catalog.init()

_render_cache: dict[tuple, bytes] = {}
_online_cache = {"value": None, "time": 0.0}


# ---------------------------------------------------------------------------
# Errors & helpers
# ---------------------------------------------------------------------------
class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@app.errorhandler(ApiError)
def _handle_api_error(error: ApiError):
    return jsonify(error=error.message), error.status


@app.errorhandler(auth.AuthError)
def _handle_auth_error(error: auth.AuthError):
    return jsonify(error=error.message), error.status


@app.errorhandler(404)
def _handle_404(_error):
    if request.path.startswith("/api/"):
        return jsonify(error="Recurso no encontrado"), 404
    return app.send_static_file("index.html")


@app.errorhandler(500)
def _handle_500(error):
    if request.path.startswith("/api/"):
        return jsonify(error=f"Error interno: {error}"), 500
    return jsonify(error="Error interno"), 500


def _dataset(dataset_id: str) -> dict:
    dataset = store.get(dataset_id)
    if dataset is None:
        raise ApiError("Conjunto de datos no encontrado", 404)
    return dataset


def _float_arg(name: str, default: float | None = None) -> float | None:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ApiError(f"Parámetro '{name}' inválido") from exc


def _int_arg(name: str, default: int, low: int | None = None,
             high: int | None = None) -> int:
    raw = request.args.get(name)
    try:
        value = int(raw) if raw not in (None, "") else default
    except ValueError as exc:
        raise ApiError(f"Parámetro '{name}' inválido") from exc
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _payload() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return request.form.to_dict()


def _cached_png(key: tuple, builder) -> bytes:
    cached = _render_cache.get(key)
    if cached is not None:
        return cached
    png = builder()
    if len(_render_cache) > 96:
        _render_cache.clear()
    _render_cache[key] = png
    return png


def _png_response(png: bytes, **headers) -> object:
    response = send_file(io.BytesIO(png), mimetype="image/png", max_age=120)
    for name, value in headers.items():
        response.headers[name.replace("_", "-")] = str(value)
    return response


def _coadd(dataset: dict, epoch: int) -> dict | None:
    return analysis.epoch_stack(dataset, epoch)


def _epoch_arg(dataset: dict, name: str, default: int) -> int:
    value = _int_arg(name, default, 0)
    n = max(len(dataset.get("epochs", [])), 1)
    return min(value, n - 1)


def _online() -> bool | None:
    now = time.time()
    if _online_cache["value"] is not None and now - _online_cache["time"] < 90:
        return _online_cache["value"]
    try:
        value = archive.archive_online()
    except Exception:
        value = False
    _online_cache["value"] = value
    _online_cache["time"] = now
    return value


def _frame_meta(dataset: dict) -> list[dict]:
    out = []
    for key, frame in dataset.get("frames", {}).items():
        epoch, detector = key.split(":", 1)
        meta = frame.get("meta", {})
        wave = frame.get("wave")
        wave_range = [None, None]
        if wave is not None and np.isfinite(wave).any():
            wave_range = [round(float(np.nanmin(wave)), 4),
                          round(float(np.nanmax(wave)), 4)]
        out.append(
            {
                "key": key,
                "epoch": int(epoch),
                "detector": meta.get("detector", detector),
                "obs_id": meta.get("obs_id"),
                "date": meta.get("date_obs"),
                "mjd": meta.get("mjd"),
                "exptime": meta.get("exptime"),
                "valid_fraction": meta.get("valid_fraction"),
                "wave_range_um": wave_range,
                "flags_fraction": meta.get("flags_fraction"),
                "zodi_median": meta.get("zodi_median"),
            }
        )
    out.sort(key=lambda f: (f["epoch"], str(f["detector"])))
    return out


def _detail(dataset: dict) -> dict:
    summary = store.summary(dataset)
    summary["band_coverage"] = analysis.band_coverage(dataset, 0)
    summary["features"] = analysis.spectral_features()
    summary["frame_meta"] = _frame_meta(dataset)
    summary["has_cube"] = dataset.get("cube") is not None
    summary["bands"] = bands.BANDS
    summary["detectors"] = bands.DETECTORS
    return summary


# ---------------------------------------------------------------------------
# Application / metadata routes
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/status")
def status():
    datasets = store.list_datasets()
    try:
        index = catalog.status()
    except Exception:  # noqa: BLE001
        index = {"catalog": {"rows": 0}, "knowledge": {"documents": 0}}
    classifier = model.status()
    return jsonify(
        status="ok",
        version=VERSION,
        archive_online=_online(),
        archive_service="NASA/IPAC IRSA · SPHEREx level-2",
        datasets=len(datasets),
        demo_datasets=sum(1 for d in datasets if d["kind"] == "demo"),
        real_datasets=sum(1 for d in datasets if d["kind"] != "demo"),
        accounts=db.count_users(),
        assistant=config.public_ai_status()["mode"],
        bands=len(bands.WAVELENGTHS),
        detectors=[d["id"] for d in bands.DETECTORS],
        knowledge=index,
        classifier={
            "available": classifier.get("available"),
            "version": classifier.get("version"),
            "accuracy": classifier.get("accuracy"),
        },
        capabilities={
            "tap_query": True,
            "cutout_download": True,
            "reprojection": frames.reproject_interp is not None,
            "spectra": True,
            "difference_imaging": True,
            "moving_object_detection": True,
            "fits_upload": True,
            "local_catalog_index": index.get("catalog", {}).get("rows", 0) > 0,
            "candidate_classifier": classifier.get("available"),
            "gaia_wise_crossmatch": True,
        },
        server_time=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/api/bands")
def band_catalog():
    return jsonify(
        channels=bands.BANDS,
        detectors=bands.DETECTORS,
        total=len(bands.WAVELENGTHS),
        range_um=[bands.WAVELENGTHS[0], bands.WAVELENGTHS[-1]],
        channels_per_detector=bands.CHANNELS_PER_DETECTOR,
    )


@app.get("/api/datasets")
def list_datasets():
    return jsonify(datasets=store.list_datasets())


@app.get("/api/datasets/<dataset_id>")
def dataset_detail(dataset_id: str):
    return jsonify(_detail(_dataset(dataset_id)))


@app.get("/api/scenarios")
def legacy_scenarios():
    store.ensure_demo_datasets()
    items = [d for d in store.list_datasets() if d["kind"] == "demo"]
    return jsonify(scenarios=items)


@app.get("/api/scenario/<scenario_id>/observations")
def legacy_observations(scenario_id: str):
    dataset = store.get(f"demo_{scenario_id}") or store.get(scenario_id)
    if dataset is None:
        raise ApiError("Escenario no encontrado", 404)
    return jsonify(observations=store.summary(dataset)["epochs"])


# ---------------------------------------------------------------------------
# Archive query / resolve / load
# ---------------------------------------------------------------------------
def _resolve_payload(payload: dict) -> tuple[float, float, str | None]:
    name = (payload.get("name") or "").strip() or None
    ra = payload.get("ra")
    dec = payload.get("dec")
    if (ra in (None, "") or dec in (None, "")) and name:
        resolved = archive.resolve_name(name)
        if resolved is None:
            raise ApiError(f"No se pudo resolver el nombre '{name}'", 404)
        return float(resolved["ra"]), float(resolved["dec"]), resolved["name"]
    if ra in (None, "") or dec in (None, ""):
        raise ApiError("Indica un nombre o coordenadas RA/Dec")
    ra_f, dec_f = float(ra), float(dec)
    if not (-360.0 <= ra_f <= 360.0) or not (-90.0 <= dec_f <= 90.0):
        raise ApiError("Coordenadas fuera de rango")
    return ra_f % 360.0, dec_f, name


@app.post("/api/resolve")
def resolve_name():
    payload = _payload()
    name = (payload.get("name") or "").strip()
    if not name:
        raise ApiError("Nombre vacío")
    resolved = archive.resolve_name(name)
    if resolved is None:
        raise ApiError("Objeto no encontrado en Sesame", 404)
    return jsonify(resolved)


@app.post("/api/archive/query")
def archive_query():
    payload = _payload()
    try:
        ra, dec, name = _resolve_payload(payload)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(str(exc)) from exc

    mjd_min = payload.get("mjd_min")
    mjd_max = payload.get("mjd_max")
    try:
        observations = archive.query_field(
            ra,
            dec,
            mjd_min=float(mjd_min) if mjd_min not in (None, "") else None,
            mjd_max=float(mjd_max) if mjd_max not in (None, "") else None,
            limit=int(payload.get("limit", 20000)),
        )
    except archive.ArchiveError as exc:
        raise ApiError(str(exc), 502) from exc

    epochs = archive.group_epochs(observations)
    epoch_rows = [
        {
            "day": epoch["day"],
            "mjd": epoch["mjd"],
            "date": epoch["date"],
            "detectors": epoch["detectors"],
            "n_detectors": len(epoch["detectors"]),
            "n_observations": len(epoch.get("observations", [])),
            "observation_ids": epoch.get("observation_ids", []),
        }
        for epoch in epochs
    ]
    mjds = [obs.mjd for obs in observations]
    suggested = pipeline.select_epochs(epochs, max_epochs=2)
    return jsonify(
        ra=round(ra, 6),
        dec=round(dec, 6),
        name=name,
        n_observations=len(observations),
        n_epochs=len(epoch_rows),
        mjd_range=[round(min(mjds), 3), round(max(mjds), 3)] if mjds else None,
        detectors=sorted({obs.detector for obs in observations}),
        epochs=epoch_rows,
        suggested={
            "epoch_days": [epoch["day"] for epoch in suggested],
            "max_detectors": 4,
        },
    )


@app.post("/api/archive/load")
def archive_load():
    payload = _payload()
    ra, dec, name = _resolve_payload(payload)
    epoch_days = payload.get("epoch_days") or payload.get("days")
    if isinstance(epoch_days, str):
        epoch_days = [d for d in epoch_days.replace(" ", "").split(",") if d]
    if epoch_days:
        try:
            epoch_days = [int(float(d)) for d in epoch_days]
        except (TypeError, ValueError) as exc:
            raise ApiError("epoch_days inválido") from exc
    else:
        epoch_days = None

    detectors = payload.get("detectors")
    if isinstance(detectors, str):
        detectors = [d.strip() for d in detectors.split(",") if d.strip()]
    if not isinstance(detectors, list):
        detectors = None

    messages: list[str] = []

    def progress(message: str) -> None:
        messages.append(message)
        app.logger.info(message)

    try:
        dataset = pipeline.build_archive_dataset(
            ra,
            dec,
            fov=float(payload.get("fov", 0.35)),
            npix=int(payload.get("npix", 192)),
            max_epochs=int(payload.get("max_epochs", 2)),
            max_detectors=int(payload.get("max_detectors", 4)),
            epoch_days=epoch_days,
            detectors=detectors,
            cutout_size=payload.get("cutout_size"),
            label=payload.get("label") or (f"Campo {name}" if name else None),
            progress=progress,
        )
    except pipeline.PipelineError as exc:
        raise ApiError(str(exc), 422) from exc
    except archive.ArchiveError as exc:
        raise ApiError(str(exc), 502) from exc
    except Exception as exc:
        app.logger.exception("archive load failed")
        raise ApiError(f"No se pudo cargar el campo: {exc}", 500) from exc

    return jsonify(dataset=_detail(dataset), log=messages)


# ---------------------------------------------------------------------------
# Image rendering
# ---------------------------------------------------------------------------
def _render_params():
    return {
        "cmap": request.args.get("cmap", "mono"),
        "stretch": request.args.get("stretch", "asinh"),
        "low": _float_arg("low", 0.5),
        "high": _float_arg("high", 99.6),
        "gamma": _float_arg("gamma", 1.0),
        "size": _int_arg("size", 768, 128, 1400),
    }


@app.get("/api/datasets/<dataset_id>/image.png")
def dataset_image(dataset_id: str):
    dataset = _dataset(dataset_id)
    epoch = _epoch_arg(dataset, "epoch", 0)
    detector = request.args.get("detector", "all")
    params = _render_params()

    if request.args.get("mode") == "diff":
        return dataset_diff(dataset_id)

    def build() -> bytes:
        image = None
        if detector and detector.lower() != "all":
            frame = dataset.get("frames", {}).get(f"{epoch}:{detector}")
            if frame is not None:
                image = frame["image"]
        if image is None:
            stack = _coadd(dataset, epoch)
            if stack is None:
                raise ApiError("Época sin datos", 404)
            image = stack["image"]
        return render.render_image(
            image,
            cmap=params["cmap"],
            method=params["stretch"],
            low=params["low"],
            high=params["high"],
            gamma=params["gamma"],
            size=params["size"],
        )

    key = (
        dataset_id,
        dataset.get("created"),
        epoch,
        detector,
        params["cmap"],
        params["stretch"],
        params["low"],
        params["high"],
        params["gamma"],
        params["size"],
    )
    png = _cached_png(key, build)
    return _png_response(png, X_Epoch=epoch, X_Detector=detector)


@app.get("/api/datasets/<dataset_id>/diff.png")
def dataset_diff(dataset_id: str):
    dataset = _dataset(dataset_id)
    epoch_a = _epoch_arg(dataset, "epoch_a", 0)
    epoch_b = _epoch_arg(dataset, "epoch_b", max(len(dataset.get("epochs", [])) - 1, 0))
    if epoch_a == epoch_b:
        raise ApiError("Selecciona dos épocas distintas")
    size = _int_arg("size", 768, 128, 1400)
    scale_sigma = _float_arg("scale_sigma", 5.0)

    matched = None
    if dataset.get("kind") != "demo":
        matched = analysis.matched_pair(dataset, epoch_a, epoch_b)

    def build() -> bytes:
        if matched is not None:
            diff = matched["diff"]
            variance = matched["variance"]
        else:
            stack_a = _coadd(dataset, epoch_a)
            stack_b = _coadd(dataset, epoch_b)
            if stack_a is None or stack_b is None:
                raise ApiError("Épocas sin datos", 404)
            diff = stack_b["image"] - stack_a["image"]
            variance = None
            if stack_a.get("variance") is not None and stack_b.get("variance") is not None:
                variance = stack_a["variance"] + stack_b["variance"]
        return render.render_difference(
            diff, variance=variance, size=size, scale_sigma=scale_sigma
        )

    key = (dataset_id, dataset.get("created"), "diff", epoch_a, epoch_b, size, scale_sigma)
    png = _cached_png(key, build)

    baseline = 0.0
    try:
        baseline = abs(
            float(dataset["epochs"][epoch_b]["mjd"]) - float(dataset["epochs"][epoch_a]["mjd"])
        )
    except Exception:
        pass
    headers = {
        "X_Epoch_A": epoch_a,
        "X_Epoch_B": epoch_b,
        "X_Baseline_Days": round(baseline, 2),
        "X_Spectral_Method": "matched-bands" if matched is not None else "coadd",
    }
    if matched is not None:
        headers["X_Matched_Bands"] = len(matched["bands"])
        headers["X_Matched_Pixels"] = matched["valid_pixels"]
    return _png_response(png, **headers)


@app.get("/api/datasets/<dataset_id>/sum.png")
def dataset_sum(dataset_id: str):
    """Inverse-variance coadd of two epochs (image addition across time)."""
    dataset = _dataset(dataset_id)
    epoch_a = _epoch_arg(dataset, "epoch_a", 0)
    epoch_b = _epoch_arg(dataset, "epoch_b", max(len(dataset.get("epochs", [])) - 1, 0))
    if epoch_a == epoch_b:
        raise ApiError("Selecciona dos épocas distintas")
    params = _render_params()

    matched = None
    if dataset.get("kind") != "demo":
        matched = analysis.matched_pair(dataset, epoch_a, epoch_b)

    def build() -> bytes:
        if matched is not None:
            var_a = matched["variance_a"]
            var_b = matched["variance_b"]
            ok = np.isfinite(var_a) & np.isfinite(var_b) & (var_a > 0) & (var_b > 0)
            image = np.full(var_a.shape, np.nan, dtype=np.float32)
            if np.count_nonzero(ok):
                wa = 1.0 / var_a[ok]
                wb = 1.0 / var_b[ok]
                image[ok] = (
                    (matched["image_a"][ok] * wa + matched["image_b"][ok] * wb) / (wa + wb)
                ).astype(np.float32)
        else:
            stack_a = _coadd(dataset, epoch_a)
            stack_b = _coadd(dataset, epoch_b)
            if stack_a is None or stack_b is None:
                raise ApiError("Épocas sin datos", 404)
            image_a = stack_a["image"]
            image_b = stack_b["image"]
            var_a = stack_a.get("variance")
            var_b = stack_b.get("variance")
            if var_a is not None and var_b is not None:
                ok = (
                    np.isfinite(image_a) & np.isfinite(image_b)
                    & np.isfinite(var_a) & np.isfinite(var_b)
                    & (var_a > 0) & (var_b > 0)
                )
                image = np.full(image_a.shape, np.nan, dtype=np.float32)
                if np.count_nonzero(ok):
                    wa = 1.0 / var_a[ok]
                    wb = 1.0 / var_b[ok]
                    image[ok] = (
                        (image_a[ok] * wa + image_b[ok] * wb) / (wa + wb)
                    ).astype(np.float32)
            else:
                image = np.nanmean(np.stack([image_a, image_b]), axis=0)
        return render.render_image(
            image,
            cmap=params["cmap"],
            method=params["stretch"],
            low=params["low"],
            high=params["high"],
            gamma=params["gamma"],
            size=params["size"],
        )

    key = (dataset_id, dataset.get("created"), "sum", epoch_a, epoch_b,
           params["cmap"], params["stretch"], params["low"], params["high"],
           params["gamma"], params["size"])
    png = _cached_png(key, build)
    headers = {
        "X_Epoch_A": epoch_a,
        "X_Epoch_B": epoch_b,
        "X_Spectral_Method": "matched-bands" if matched is not None else "coadd",
    }
    if matched is not None:
        headers["X_Matched_Bands"] = len(matched["bands"])
    return _png_response(png, **headers)


@app.get("/api/datasets/<dataset_id>/band/<int:band_index>.png")
def dataset_band_image(dataset_id: str, band_index: int):
    dataset = _dataset(dataset_id)
    if not 0 <= band_index < len(bands.WAVELENGTHS):
        raise ApiError("Canal fuera de rango", 404)
    epoch = _epoch_arg(dataset, "epoch", 0)
    if dataset.get("kind") == "demo":
        demo.ensure_demo_cube(dataset)
    params = _render_params()

    def build() -> bytes:
        image = analysis.band_image(dataset, band_index, epoch)
        if image is None:
            raise ApiError("Canal sin cobertura en esta época", 404)
        return render.render_image(
            image,
            cmap=params["cmap"],
            method=params["stretch"],
            low=params["low"],
            high=params["high"],
            gamma=params["gamma"],
            size=params["size"],
        )

    key = (dataset_id, dataset.get("created"), "band", band_index, epoch,
           params["cmap"], params["stretch"], params["size"])
    return _png_response(_cached_png(key, build), X_Band=band_index)


@app.get("/api/datasets/<dataset_id>/bands")
def dataset_bands(dataset_id: str):
    dataset = _dataset(dataset_id)
    epoch = request.args.get("epoch")
    epoch_index = int(epoch) if epoch not in (None, "") else None
    coverage = analysis.band_coverage(dataset, epoch_index)
    covered = sum(1 for c in coverage if c["covered"])
    return jsonify(
        channels=coverage,
        covered=covered,
        total=len(coverage),
        fraction=round(covered / max(len(coverage), 1), 3),
    )


# ---------------------------------------------------------------------------
# Science analysis
# ---------------------------------------------------------------------------
@app.get("/api/datasets/<dataset_id>/spectrum")
def dataset_spectrum(dataset_id: str):
    dataset = _dataset(dataset_id)
    epoch_raw = request.args.get("epoch")
    epoch = int(epoch_raw) if epoch_raw not in (None, "") else None

    ra = _float_arg("ra")
    dec = _float_arg("dec")
    if ra is None or dec is None:
        x = _float_arg("x")
        y = _float_arg("y")
        if x is None or y is None:
            ra = float(dataset.get("ra", 0.0))
            dec = float(dataset.get("dec", 0.0))
        else:
            wcs = analysis.grid_wcs(dataset)
            try:
                ra, dec = wcs.pixel_to_world_values(x, y)
            except Exception as exc:
                raise ApiError("Coordenadas de píxel inválidas") from exc

    if dataset.get("kind") == "demo":
        demo.ensure_demo_cube(dataset)

    result = analysis.extract_spectrum(dataset, float(ra), float(dec), epoch_index=epoch)
    result["dataset_id"] = dataset_id
    result["label"] = dataset.get("label")
    result["epoch"] = epoch
    result["bunit"] = "MJy/sr" if dataset.get("kind") != "demo" else "u.a."
    return jsonify(result)


@app.get("/api/datasets/<dataset_id>/detect")
def dataset_detect(dataset_id: str):
    dataset = _dataset(dataset_id)
    epochs = dataset.get("epochs", [])
    if len(epochs) < 2:
        raise ApiError("Se necesitan al menos dos épocas para comparar", 422)
    epoch_a = _epoch_arg(dataset, "epoch_a", 0)
    epoch_b = _epoch_arg(dataset, "epoch_b", len(epochs) - 1)
    if epoch_a == epoch_b:
        raise ApiError("Selecciona dos épocas distintas")
    threshold = _float_arg("threshold", 4.0)
    min_area = _int_arg("min_area", 3, 1, 200)

    result = analysis.detect_candidates(
        dataset, epoch_a, epoch_b, threshold_sigma=float(threshold), min_area=min_area
    )
    context = model.context_from_result(result, dataset)
    want_catalog = request.args.get("catalog") in ("1", "true", "yes")
    enrichment = model.enrich_candidates(
        result.get("candidates", []),
        context,
        annotate_catalog=want_catalog,
        dataset=dataset,
    )
    result["epoch_a"] = epoch_a
    result["epoch_b"] = epoch_b
    result["dates"] = [epochs[epoch_a].get("date"), epochs[epoch_b].get("date")]
    result["classifier"] = {
        "available": bool(enrichment.get("model")),
        "version": (model.status() or {}).get("version"),
        "catalog": enrichment.get("catalog"),
    }
    return jsonify(result)


@app.get("/api/datasets/<dataset_id>/stack")
def dataset_stack(dataset_id: str):
    dataset = _dataset(dataset_id)
    epoch = _epoch_arg(dataset, "epoch", 0)
    frame_list = analysis.dataset_frames(dataset, epoch)
    if not frame_list:
        raise ApiError("Época sin datos", 404)

    detector_rows = []
    for frame in frame_list:
        image = frame["image"]
        variance = frame["variance"]
        valid = np.isfinite(image)
        values = image[valid]
        row = {
            "detector": frame.get("meta", {}).get("detector", "?"),
            "n_valid": int(values.size),
            "valid_fraction": round(float(valid.mean()), 4),
            "median": round(float(np.median(values)), 6) if values.size else None,
            "sigma": round(float(np.std(values)), 6) if values.size else None,
            "variance_median": (
                round(float(np.nanmedian(variance[valid])), 8)
                if np.isfinite(variance[valid]).any()
                else None
            ),
        }
        detector_rows.append(row)

    combined = frames.combine_frames(frame_list)
    valid = np.isfinite(combined["image"])
    values = combined["image"][valid]
    return jsonify(
        epoch=epoch,
        date=dataset["epochs"][epoch].get("date"),
        n_frames=len(frame_list),
        detectors=detector_rows,
        snr_gain=round(math.sqrt(len(frame_list)), 2),
        coadd={
            "median": round(float(np.median(values)), 6) if values.size else None,
            "sigma": round(float(np.std(values)), 6) if values.size else None,
            "valid_fraction": round(float(valid.mean()), 4),
        },
        method="Coadición ponderada por varianza inversa",
    )


@app.get("/api/datasets/<dataset_id>/meta")
def dataset_meta(dataset_id: str):
    dataset = _dataset(dataset_id)
    epoch = _epoch_arg(dataset, "epoch", 0)
    report = analysis.quality_report(dataset, epoch)
    coverage = analysis.band_coverage(dataset, epoch)
    covered = sum(1 for c in coverage if c["covered"])
    return jsonify(
        dataset=store.summary(dataset),
        epoch=epoch,
        quality=report,
        band_coverage={
            "covered": covered,
            "total": len(coverage),
            "fraction": round(covered / max(len(coverage), 1), 3),
        },
        frames=_frame_meta(dataset),
        source=dataset.get("source", {}),
        generated=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Upload & export
# ---------------------------------------------------------------------------
@app.post("/api/upload")
def upload():
    if "file" not in request.files:
        raise ApiError("No se envió ningún archivo")
    upload_file = request.files["file"]
    if not upload_file.filename:
        raise ApiError("Nombre de archivo vacío")
    raw = upload_file.read()
    if not raw:
        raise ApiError("Archivo vacío")
    path = pipeline.save_upload(raw, upload_file.filename)
    payload = request.form

    def _optional_float(name):
        value = payload.get(name)
        if value in (None, ""):
            return None
        try:
            return float(value)
        except ValueError:
            return None

    try:
        dataset = pipeline.build_upload_dataset(
            path,
            label=payload.get("label") or upload_file.filename,
            ra=_optional_float("ra"),
            dec=_optional_float("dec"),
            date=payload.get("date_obs") or None,
            mjd=_optional_float("mjd"),
        )
    except Exception as exc:
        app.logger.exception("upload failed")
        raise ApiError(f"No se pudo procesar el archivo: {exc}") from exc

    return jsonify(dataset=_detail(dataset))


@app.get("/api/datasets/<dataset_id>/export")
def dataset_export(dataset_id: str):
    dataset = _dataset(dataset_id)
    epoch = _epoch_arg(dataset, "epoch", 0)
    include_detection = request.args.get("detect", "1") not in ("0", "false", "")
    report = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "generator": "SPHEREx Sky Time Viewer",
        "version": VERSION,
        "dataset": store.summary(dataset),
        "quality": analysis.quality_report(dataset, epoch),
        "band_coverage": analysis.band_coverage(dataset, epoch),
        "frames": _frame_meta(dataset),
        "source": dataset.get("source", {}),
    }
    if include_detection and len(dataset.get("epochs", [])) >= 2:
        report["detection"] = analysis.detect_candidates(
            dataset, 0, len(dataset["epochs"]) - 1
        )
    payload = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
    return send_file(
        io.BytesIO(payload),
        mimetype="application/json",
        as_attachment=True,
        download_name=f"{dataset_id}_spherex_report.json",
    )


@app.get("/api/export_report")
def legacy_export():
    dataset_id = request.args.get("dataset") or request.args.get("id")
    if dataset_id:
        return dataset_export(dataset_id)
    raise ApiError("Indica el conjunto de datos a exportar", 400)


@app.get("/api/datasets/<dataset_id>/catalogs")
def dataset_catalogs(dataset_id: str):
    """Gaia DR2 / AllWISE sources over the dataset footprint (IRSA TAP)."""
    dataset = _dataset(dataset_id)
    catalog = request.args.get("catalog", "gaia")
    limit = _int_arg("limit", 600, 1, 2000)
    fov = float(dataset.get("fov", 0.2))
    radius = max(0.01, min(fov * 0.75, 1.5))
    try:
        sources = archive.query_catalog(
            catalog, float(dataset["ra"]), float(dataset["dec"]), radius, limit=limit
        )
    except archive.ArchiveError as exc:
        raise ApiError(str(exc), 502) from exc

    wcs = analysis.grid_wcs(dataset)
    npix = int(dataset.get("grid", {}).get("npix", dataset.get("npix", 192)))
    plotted = []
    for source in sources:
        try:
            x, y = wcs.world_to_pixel_values(source["ra"], source["dec"])
        except Exception:
            continue
        if not (0 <= x < npix and 0 <= y < npix):
            continue
        item = dict(source)
        item["x"] = round(float(x), 2)
        item["y"] = round(float(y), 2)
        plotted.append(item)

    return jsonify(
        catalog=catalog,
        count=len(plotted),
        total=len(sources),
        fov=fov,
        radius_deg=round(radius, 4),
        npix=npix,
        sources=plotted,
    )


@app.get("/api/catalog/overlay")
def catalog_overlay():
    """Compatibility alias for the catalog overlay endpoint."""
    dataset_id = request.args.get("dataset", "")
    if not dataset_id:
        raise ApiError("Indica el conjunto de datos", 400)
    return dataset_catalogs(dataset_id)


# ---------------------------------------------------------------------------
# Local science index & learned classifier
# ---------------------------------------------------------------------------
@app.get("/api/catalog/stats")
def catalog_stats():
    try:
        data = catalog.stats()
    except Exception as exc:  # noqa: BLE001
        raise ApiError(f"Índice local no disponible: {exc}", 503) from exc
    return jsonify(
        catalog=data,
        knowledge=catalog.knowledge_stats(),
        model=model.status(),
    )


@app.get("/api/catalog/search")
def catalog_search():
    ra = _float_arg("ra", None)
    dec = _float_arg("dec", None)
    radius = _float_arg("radius", 1.0)
    mjd_min = _float_arg("mjd_min", None)
    mjd_max = _float_arg("mjd_max", None)
    band = request.args.get("band") or None
    limit = _int_arg("limit", 120, 1, 1000)
    try:
        result = catalog.search(
            ra,
            dec,
            radius or 1.0,
            band=band,
            mjd_min=mjd_min,
            mjd_max=mjd_max,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        raise ApiError(f"No se pudo consultar el índice: {exc}", 503) from exc
    return jsonify(result)


@app.get("/api/catalog/field")
def catalog_field():
    ra = _float_arg("ra", None)
    dec = _float_arg("dec", None)
    if ra is None or dec is None:
        raise ApiError("Indica ra y dec para resumir el campo", 400)
    radius = _float_arg("radius", 0.5)
    return jsonify(catalog.field_summary(ra, dec, radius or 0.5))


@app.get("/api/catalog/crossmatch")
def catalog_crossmatch():
    ra = _float_arg("ra", None)
    dec = _float_arg("dec", None)
    if ra is None or dec is None:
        raise ApiError("Indica ra y dec para consultar Gaia/WISE", 400)
    radius = _float_arg("radius", 0.5)
    force = request.args.get("force") in ("1", "true", "yes")
    return jsonify(
        crossmatch.field_report(ra, dec, radius or 0.5)
        if not force
        else crossmatch.field_sources(ra, dec, radius or 0.5, force=True)
    )


@app.get("/api/model/status")
def model_status():
    return jsonify(model.status())


@app.post("/api/model/train")
@auth.login_required
def model_train():
    auth.require_csrf()
    payload = _payload()
    include_archive = payload.get("include_archive", True)
    if isinstance(include_archive, str):
        include_archive = include_archive.lower() not in ("0", "false", "no")
    try:
        metrics = model.train(include_archive=bool(include_archive))
    except Exception as exc:  # noqa: BLE001
        raise ApiError(f"No se pudo entrenar el clasificador: {exc}", 500) from exc
    return jsonify(metrics=metrics, status=model.status())


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
@app.get("/api/auth/me")
def auth_me():
    user = auth.current_user()
    if user is None:
        return jsonify(user=None, csrf=None, favorites=[], preferences={})
    return jsonify(
        user=auth.public_user(user),
        csrf=auth.csrf_token(),
        favorites=db.list_favorites(int(user["id"])),
        preferences=db.get_preferences(int(user["id"])),
    )


@app.post("/api/auth/register")
def auth_register():
    payload = _payload()
    key = auth.throttle_key(str(payload.get("username", "")))
    auth.check_throttle(key)
    try:
        user = auth.create_account(payload)
    except auth.AuthError:
        auth.record_failure(key)
        raise
    token = auth.login_session(user)
    return jsonify(
        user=auth.public_user(user),
        csrf=token,
        favorites=[],
        preferences={},
    ), 201


@app.post("/api/auth/login")
def auth_login():
    payload = _payload()
    login = str(
        payload.get("login") or payload.get("username") or payload.get("email") or ""
    )
    key = auth.throttle_key(login)
    auth.check_throttle(key)
    user = auth.authenticate(login, str(payload.get("password", "")))
    if user is None:
        auth.record_failure(key)
        raise ApiError("Usuario o contraseña incorrectos.", 401)
    auth.clear_failures(key)
    token = auth.login_session(user)
    return jsonify(
        user=auth.public_user(user),
        csrf=token,
        favorites=db.list_favorites(int(user["id"])),
        preferences=db.get_preferences(int(user["id"])),
    )


@app.post("/api/auth/logout")
def auth_logout():
    auth.logout_session()
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Assistant
# ---------------------------------------------------------------------------
def _sse(event: dict) -> str:
    return (
        f"event: {event.get('type', 'message')}\n"
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    )


@app.get("/api/assistant/status")
def assistant_status():
    status = config.public_ai_status()
    status["topics"] = assistant.knowledge_topics()
    status["tools"] = [tool["function"]["name"] for tool in assistant.TOOLS]
    status["knowledge"] = catalog.status()
    status["classifier"] = model.status()
    return jsonify(status)


@app.get("/api/assistant/conversations")
@auth.login_required
def assistant_conversations():
    return jsonify(conversations=db.list_conversations(int(g.user["id"])))


@app.post("/api/assistant/conversations")
@auth.login_required
def assistant_conversation_create():
    auth.require_csrf()
    payload = _payload()
    title = str(payload.get("title") or "Nueva conversación").strip()[:120]
    conversation = db.create_conversation(
        int(g.user["id"]), title or "Nueva conversación", payload.get("dataset_id")
    )
    return jsonify(conversation=conversation), 201


@app.get("/api/assistant/conversations/<int:conversation_id>")
@auth.login_required
def assistant_conversation_detail(conversation_id: int):
    conversation = db.get_conversation(conversation_id, int(g.user["id"]))
    if conversation is None:
        raise ApiError("Conversación no encontrada", 404)
    return jsonify(
        conversation=conversation,
        messages=db.list_messages(conversation_id),
    )


@app.delete("/api/assistant/conversations/<int:conversation_id>")
@auth.login_required
def assistant_conversation_delete(conversation_id: int):
    auth.require_csrf()
    if not db.delete_conversation(conversation_id, int(g.user["id"])):
        raise ApiError("Conversación no encontrada", 404)
    return jsonify(ok=True)


@app.post("/api/assistant/chat")
@auth.login_required
def assistant_chat():
    auth.require_csrf()
    payload = _payload()
    message = str(payload.get("message", "")).strip()
    if not message:
        raise ApiError("Escribe una pregunta para el asistente.", 400)
    if len(message) > 4000:
        raise ApiError("La pregunta es demasiado larga (máximo 4000 caracteres).", 400)

    user_id = int(g.user["id"])
    dataset_id = payload.get("dataset_id") or None
    if dataset_id and store.get(str(dataset_id)) is None:
        dataset_id = None

    conversation = None
    raw_id = payload.get("conversation_id")
    if raw_id not in (None, "", 0, "0"):
        try:
            conversation = db.get_conversation(int(raw_id), user_id)
        except (TypeError, ValueError):
            conversation = None
        if conversation is None:
            raise ApiError("Conversación no encontrada", 404)

    if conversation is None:
        title = message[:60] + ("…" if len(message) > 60 else "")
        conversation = db.create_conversation(user_id, title, dataset_id)

    conversation_id = int(conversation["id"])
    history = db.list_messages(conversation_id)
    db.add_message(conversation_id, "user", message)

    def generate():
        answer = ""
        try:
            yield _sse(
                {
                    "type": "meta",
                    "conversation_id": conversation_id,
                    "title": conversation["title"],
                }
            )
            for event in assistant.stream_reply(message, history, dataset_id):
                if event.get("type") == "delta":
                    answer += event.get("text", "")
                yield _sse(event)
            if answer.strip():
                db.add_message(conversation_id, "assistant", answer)
            yield _sse(
                {
                    "type": "done",
                    "conversation_id": conversation_id,
                    "title": conversation["title"],
                }
            )
        except Exception as exc:  # noqa: BLE001 - delivered to the browser
            app.logger.exception("assistant stream failed")
            yield _sse({"type": "error", "error": str(exc) or "Error del asistente."})

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    # The stream is one-shot: close the connection after the ``done`` event so
    # clients do not try to reuse a half-closed keep-alive socket for the next
    # question.
    response.headers["Connection"] = "close"
    return response


# ---------------------------------------------------------------------------
# Favorites & preferences
# ---------------------------------------------------------------------------
@app.get("/api/favorites")
@auth.login_required
def favorites_list():
    ids = db.list_favorites(int(g.user["id"]))
    favorites = []
    for dataset_id in ids:
        dataset = store.get(dataset_id)
        if dataset is None:
            favorites.append({"id": dataset_id, "label": dataset_id, "missing": True})
        else:
            favorites.append(store.summary(dataset))
    return jsonify(favorites=favorites, ids=ids)


@app.post("/api/favorites/<dataset_id>")
@auth.login_required
def favorites_toggle(dataset_id: str):
    auth.require_csrf()
    if store.get(dataset_id) is None:
        raise ApiError("Conjunto de datos no encontrado", 404)
    saved = db.toggle_favorite(int(g.user["id"]), dataset_id)
    return jsonify(favorite=saved, ids=db.list_favorites(int(g.user["id"])))


@app.get("/api/prefs")
@auth.login_required
def preferences_get():
    return jsonify(preferences=db.get_preferences(int(g.user["id"])))


@app.put("/api/prefs")
@auth.login_required
def preferences_put():
    auth.require_csrf()
    payload = _payload()
    allowed = {"dataset", "cmap", "stretch", "detector", "showGrid", "showCandidates"}
    clean = {key: value for key, value in payload.items() if key in allowed}
    return jsonify(preferences=db.set_preferences(int(g.user["id"]), clean))


if __name__ == "__main__":
    store.ensure_demo_datasets()
    print("=" * 66)
    print("SPHEREx Sky Time Viewer · NASA Space Apps Challenge")
    print("Datos reales: NASA/IPAC IRSA (TAP + cutouts level-2)")
    print("Servidor: http://127.0.0.1:5000")
    print("=" * 66)
    app.run(debug=True, port=5000, host="0.0.0.0")
