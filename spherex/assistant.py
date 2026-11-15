"""SPHEREx expert assistant.

The assistant talks to any OpenAI-compatible chat-completions endpoint and can
call read-only tools that inspect the datasets currently loaded in the viewer
(coverage, quality, spectra, difference imaging).  When no API key is configured
it falls back to a deterministic local expert that answers from the project
knowledge base and the live dataset metadata.
"""

from __future__ import annotations

import json
import re
import time

import numpy as np
import requests

from . import analysis, catalog, config, crossmatch, model, store
from .bands import BANDS

MAX_TOOL_STEPS = 4
HISTORY_MESSAGES = 14
MAX_TOOL_CHARS = 7000

SYSTEM_PROMPT = """Eres el asistente científico de SPHEREx Sky Time Viewer, una
aplicación del NASA Space Apps Challenge que explora datos reales del
telescopio NASA SPHEREx desde el archivo IRSA.

Tu especialidad:
- La misión SPHEREx: 6 detectores LVF, 102 canales entre 0.75 y 5.0 µm,
  barridos del cielo, calibración level-2 y productos IRSA.
- Análisis en el dominio del tiempo: coadd por varianza inversa, diferencia de
  épocas en canales comunes, detección de dipolos (objetos en movimiento),
  transitorios, variables, espectros y luz zodiacal.
- El funcionamiento de esta aplicación: paneles, modos de imagen, URL, API,
  cobertura de canales, índice de contaminación y catálogos Gaia/AllWISE.
- El índice local de SPHEREx, la base de conocimiento del proyecto y el
  clasificador de candidatos entrenado con datos demo y etiquetas débiles de
  Gaia DR3/AllWISE.

Reglas:
- Responde en español, de forma clara y breve. Usa Markdown sencillo.
- Conversa con naturalidad: saluda si te saludan, mantén el hilo del historial y
  resuelve referencias como «eso», «el candidato» o «la última época».
- Recibirás un bloque «DATOS ACTUALES DEL VISOR» con cifras calculadas en el
  momento (coordenadas, épocas, cobertura, calidad, catálogo y clasificador).
  Ese bloque es la verdad del turno: úsalo para responder con cifras concretas.
- Llama a las herramientas solo cuando necesites algo que el bloque no trae:
  candidatos por diferencia de épocas, espectro en una coordenada, cruce con
  Gaia/AllWISE o búsquedas en el índice y la base de conocimiento.
- Cuando la pregunta dependa de los datos abiertos, usa las herramientas antes
  de responder. No inventes cifras: cita las que devuelvan las herramientas.
- Explica qué significan los datos y propone el siguiente paso concreto en el
  visor (panel, botón o herramienta), no solo el número.
- Para preguntas de estudio o conceptos, usa search_knowledge y cita la fuente.
- Distingue siempre entre datos reales de IRSA, demostraciones sintéticas,
  cargas propias y etiquetas débiles del clasificador.
- El clasificador devuelve probabilidades de artefacto, movimiento, transitorio
  o variable. No es una confirmación física: explícalo cuando lo uses.
- Si algo no se puede determinar con los datos disponibles, dilo y explica qué
  haría falta (más épocas, otra banda, mayor FOV, seguimiento).
- No prometas detecciones físicas definitivas: el visor es un cribado.
- Si la pregunta no es astronómica ni trata del proyecto, dilo en una frase."""

TOOL_LABELS = {
    "list_datasets": "Revisando conjuntos de datos",
    "dataset_summary": "Resumiendo el campo",
    "band_coverage": "Revisando cobertura de canales",
    "quality_report": "Evaluando calidad y contaminación",
    "detect_candidates": "Comparando épocas y clasificando candidatos",
    "measure_spectrum": "Midiendo el espectro",
    "search_catalog": "Consultando el índice local SPHEREx",
    "catalog_stats": "Resumiendo la base de datos SPHEREx",
    "search_knowledge": "Buscando en la base de conocimiento",
    "crossmatch_field": "Cruzando con Gaia DR3 y AllWISE",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_datasets",
            "description": "Lista los conjuntos de datos cargados en el visor (reales, demo o subidos).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dataset_summary",
            "description": (
                "Resumen del campo abierto: coordenadas, FOV, cuadrícula, épocas, "
                "frames y canales cubiertos. Úsalo para preguntas generales sobre el campo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID del conjunto de datos."},
                    "epoch": {"type": "integer", "description": "Índice de época (0-based)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "band_coverage",
            "description": (
                "Cobertura de los 102 canales espectrales en una época: cuántos están "
                "cubiertos, cuáles y con cuántos píxeles."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "epoch": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quality_report",
            "description": (
                "Calidad de una época: índice de contaminación, flags, rayos cósmicos, "
                "saturación, luz zodiacal, ruido de fondo y ganancia SNR del coadd."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "epoch": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_candidates",
            "description": (
                "Resta dos épocas y devuelve candidatos a objetos en movimiento, "
                "transitorios y variables con su SNR y desplazamiento."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "epoch_a": {"type": "integer"},
                    "epoch_b": {"type": "integer"},
                    "threshold": {
                        "type": "number",
                        "description": "Umbral en sigma (2-12); por defecto 6.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "measure_spectrum",
            "description": (
                "Mide el espectro en una coordenada celeste y devuelve los canales con "
                "flujo, detector y cobertura."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "ra": {"type": "number", "description": "Ascensión recta en grados."},
                    "dec": {"type": "number", "description": "Declinación en grados."},
                    "epoch": {"type": "integer"},
                },
                "required": ["ra", "dec"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_catalog",
            "description": (
                "Consulta el índice local del catálogo IRSA de imágenes espectrales "
                "SPHEREx por posición, detector/banda y rango de tiempo. Devuelve "
                "observaciones reales con separación angular y MJD."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ra": {"type": "number"},
                    "dec": {"type": "number"},
                    "radius_deg": {"type": "number", "description": "Radio en grados (0.001-30)."},
                    "band": {"type": "string", "description": "SPHEREx-D1 ... SPHEREx-D6."},
                    "mjd_min": {"type": "number"},
                    "mjd_max": {"type": "number"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "catalog_stats",
            "description": (
                "Resumen global de la base de datos local: número de registros, "
                "observaciones, bandas, rango temporal, área de cielo y estadísticas "
                "del clasificador entrenado."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "Busca pasajes en la base de conocimiento del proyecto (misión, "
                "instrumentos, análisis de cambios, Gaia/WISE, desafío). Úsala para "
                "preguntas de estudio o para citar documentación."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer", "description": "Número de pasajes (1-8)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crossmatch_field",
            "description": (
                "Consulta Gaia DR3 y AllWISE alrededor de una posición y devuelve "
                "cuántas fuentes de referencia hay. Sirve para validar candidatos y "
                "descartar estrellas conocidas."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ra": {"type": "number"},
                    "dec": {"type": "number"},
                    "radius_deg": {"type": "number"},
                },
                "required": ["ra", "dec"],
            },
        },
    },
]


class AssistantError(RuntimeError):
    """Failure that can be shown to the user, optionally with an HTTP status."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, np.ndarray):
        return _clean(value.tolist())
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return round(number, 6) if np.isfinite(number) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def _dataset(dataset_id: str | None) -> dict:
    if dataset_id:
        dataset = store.get(str(dataset_id))
        if dataset is not None:
            return dataset
    datasets = store.list_datasets()
    if not datasets:
        raise AssistantError("No hay conjuntos de datos cargados en el visor.")
    fallback = datasets[0]["id"]
    dataset = store.get(fallback)
    if dataset is None:
        raise AssistantError("El conjunto de datos solicitado no está disponible.")
    return dataset


def _epoch(dataset: dict, raw, default: int = 0) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    n = max(len(dataset.get("epochs", [])), 1)
    return max(0, min(value, n - 1))


def _dataset_summary(dataset: dict, epoch: int | None = None) -> dict:
    grid = dataset.get("grid") or {}
    epochs = dataset.get("epochs", [])
    index = _epoch(dataset, epoch, 0) if epoch is not None else 0
    coverage = analysis.band_coverage(dataset, index)
    covered = [c for c in coverage if c["covered"]]
    return {
        "id": dataset.get("id"),
        "label": dataset.get("label"),
        "kind": dataset.get("kind"),
        "object_type": dataset.get("object_type"),
        "description": dataset.get("description"),
        "ra": dataset.get("ra"),
        "dec": dataset.get("dec"),
        "fov_deg": dataset.get("fov"),
        "npix": grid.get("npix", dataset.get("npix")),
        "pixel_scale_arcsec": dataset.get("pixel_scale"),
        "n_frames": len(dataset.get("frames", {})),
        "epochs": [
            {
                "index": i,
                "date": e.get("date"),
                "mjd": e.get("mjd"),
                "detectors": e.get("detectors", []),
            }
            for i, e in enumerate(epochs)
        ],
        "bands_covered": len(covered),
        "bands_total": len(coverage),
        "covered_channels": [
            {
                "channel": c["channel"],
                "wavelength_um": c["wavelength_um"],
                "detector": c["detector"],
                "samples": c["samples"],
            }
            for c in covered
        ],
    }


def _coverage(dataset: dict, epoch: int) -> dict:
    coverage = analysis.band_coverage(dataset, epoch)
    covered = [c for c in coverage if c["covered"]]
    by_detector: dict[str, int] = {}
    for channel in covered:
        by_detector[channel["detector"]] = by_detector.get(channel["detector"], 0) + 1
    ranges: dict[str, dict] = {}
    for detector in {c["detector"] for c in covered}:
        values = [c["wavelength_um"] for c in covered if c["detector"] == detector]
        ranges[detector] = {"min_um": min(values), "max_um": max(values)}
    return {
        "epoch": epoch,
        "bands_covered": len(covered),
        "bands_total": len(coverage),
        "by_detector": by_detector,
        "wavelength_ranges": ranges,
        "channels": [
            {
                "channel": c["channel"],
                "wavelength_um": c["wavelength_um"],
                "detector": c["detector"],
                "samples": c["samples"],
            }
            for c in covered
        ],
        "min_samples_per_channel": coverage[0].get("min_samples") if coverage else None,
        "note": (
            "Un canal se considera cubierto con al menos 0.5 % de píxeles "
            "válidos de la cuadrícula (mínimo 16)."
        ),
    }


def _detect(dataset: dict, epoch_a: int, epoch_b: int, threshold: float,
            with_catalog: bool = False) -> dict:
    if len(dataset.get("epochs", [])) < 2:
        raise AssistantError("Se necesitan al menos dos épocas para comparar.")
    if epoch_a == epoch_b:
        epoch_b = (epoch_a + 1) % len(dataset["epochs"])
    result = analysis.detect_candidates(
        dataset, epoch_a, epoch_b, threshold_sigma=threshold, min_area=3
    )
    result["candidates"] = result.get("candidates", [])[:10]
    context = model.context_from_result(result, dataset)
    try:
        enrichment = model.enrich_candidates(
            result["candidates"],
            context,
            annotate_catalog=with_catalog,
            dataset=dataset,
        )
        result["classifier"] = {
            "available": bool(enrichment.get("model")),
            "catalog": enrichment.get("catalog"),
        }
    except Exception as exc:  # noqa: BLE001 - classification is optional
        result["classifier"] = {"available": False, "error": str(exc)}
    return result


def _spectrum(dataset: dict, ra: float, dec: float, epoch: int) -> dict:
    result = analysis.extract_spectrum(dataset, ra, dec, epoch)
    channels = result.get("channels", [])
    return {
        "ra": result.get("ra"),
        "dec": result.get("dec"),
        "epoch": epoch,
        "kind": result.get("kind"),
        "coverage": result.get("coverage"),
        "coverage_total": result.get("coverage_total"),
        "channels": channels,
        "native_samples": len(result.get("native", [])),
        "note": (
            "Flujo en MJy/sr para datos reales. En modo demo las unidades son "
            "arbitrarias y los 102 canales son sintéticos."
        ),
    }


def _run_tool(name: str, args: dict, default_dataset_id: str | None) -> dict:
    if name == "list_datasets":
        return {
            "datasets": [
                {
                    "id": d["id"],
                    "label": d.get("label"),
                    "kind": d.get("kind"),
                    "n_epochs": d.get("n_epochs"),
                    "n_frames": d.get("n_frames"),
                    "ra": d.get("ra"),
                    "dec": d.get("dec"),
                }
                for d in store.list_datasets()
            ]
        }

    dataset = _dataset(args.get("dataset_id") or default_dataset_id)

    if name == "dataset_summary":
        epoch = args.get("epoch")
        return _dataset_summary(
            dataset, _epoch(dataset, epoch, 0) if epoch is not None else None
        )
    if name == "band_coverage":
        return _coverage(dataset, _epoch(dataset, args.get("epoch"), 0))
    if name == "quality_report":
        epoch = _epoch(dataset, args.get("epoch"), 0)
        report = analysis.quality_report(dataset, epoch)
        report["epoch"] = epoch
        report["date"] = (
            dataset["epochs"][epoch].get("date")
            if epoch < len(dataset.get("epochs", []))
            else None
        )
        return report
    if name == "detect_candidates":
        total = max(len(dataset.get("epochs", [])), 1)
        epoch_a = _epoch(dataset, args.get("epoch_a"), 0)
        epoch_b = _epoch(dataset, args.get("epoch_b"), total - 1)
        try:
            threshold = float(args.get("threshold", 6))
        except (TypeError, ValueError):
            threshold = 6.0
        return _detect(
            dataset,
            epoch_a,
            epoch_b,
            max(2.0, min(threshold, 12.0)),
            with_catalog=True,
        )
    if name == "measure_spectrum":
        try:
            ra = float(args["ra"])
            dec = float(args["dec"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AssistantError("La herramienta de espectro necesita RA y Dec válidas.") from exc
        return _spectrum(dataset, ra, dec, _epoch(dataset, args.get("epoch"), 0))

    if name == "search_catalog":
        try:
            ra = float(args["ra"]) if args.get("ra") is not None else None
            dec = float(args["dec"]) if args.get("dec") is not None else None
        except (TypeError, ValueError) as exc:
            raise AssistantError("La búsqueda de catálogo necesita RA y Dec válidas.") from exc
        radius = float(args.get("radius_deg", 1.0) or 1.0)
        limit = int(args.get("limit", 40) or 40)
        result = catalog.search(
            ra,
            dec,
            max(0.001, min(radius, 30.0)),
            band=args.get("band") or None,
            mjd_min=args.get("mjd_min"),
            mjd_max=args.get("mjd_max"),
            limit=max(1, min(limit, 200)),
        )
        result["observations"] = result.get("observations", [])[:40]
        return result

    if name == "catalog_stats":
        return {
            "catalog": catalog.stats(),
            "knowledge": catalog.knowledge_stats(),
            "classifier": model.status(),
        }

    if name == "search_knowledge":
        query = str(args.get("query") or "").strip()
        if not query:
            raise AssistantError("La búsqueda de conocimiento necesita una consulta.")
        k = int(args.get("k", 4) or 4)
        return {
            "query": query,
            "passages": catalog.search_knowledge(query, k=max(1, min(k, 8))),
        }

    if name == "crossmatch_field":
        try:
            ra = float(args["ra"])
            dec = float(args["dec"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AssistantError("El cruce de catálogos necesita RA y Dec válidas.") from exc
        radius = float(args.get("radius_deg", 0.4) or 0.4)
        return crossmatch.field_report(ra, dec, max(0.01, min(radius, 2.0)))

    raise AssistantError(f"Herramienta desconocida: {name}")


# ---------------------------------------------------------------------------
# Language model client
# ---------------------------------------------------------------------------
def _chat_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _is_google(settings: dict) -> bool:
    """True for Gemini endpoints that require ``extra_content`` round-tripping."""
    haystack = f"{settings.get('base_url', '')} {settings.get('provider', '')}".lower()
    return "googleapis.com" in haystack or "google" in haystack


def _completion_error(response: requests.Response) -> AssistantError:
    detail = ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, list):
        # Gemini returns ``[{"error": {...}}]`` instead of a single object.
        body = body[0] if body else {}
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            detail = error.get("message") or error.get("status") or ""
        else:
            detail = error or body.get("message") or ""
    else:
        detail = response.text[:400]
    detail = str(detail).strip()
    status = response.status_code
    if status == 401:
        return AssistantError(
            "La clave del asistente no es válida (401). Revisa SPHEREX_AI_API_KEY.", status
        )
    if status == 404:
        return AssistantError(
            "El modelo o la URL del asistente no existen (404). Revisa la configuración.", status
        )
    if status == 429:
        message = "El proveedor del asistente alcanzó su límite de uso (429)."
        hint = re.search(r"retry in ([0-9hms.\s]+)", detail, re.IGNORECASE)
        if hint:
            message += f" Vuelve a intentarlo en {hint.group(1).strip()}."
        elif "per day" in detail.lower():
            message += " Se agotó la cuota diaria del plan gratuito."
        else:
            message += " Espera unos segundos y vuelve a intentarlo."
        return AssistantError(message, status)
    message = f"El proveedor del asistente devolvió {status}."
    if detail:
        message += f" {detail[:300]}"
    return AssistantError(message, status)


def _stream_completion(messages: list[dict], tools: list[dict], settings: dict):
    """Yield ``("delta", text)`` events and a final ``("result", payload)``."""
    payload = {
        "model": settings["model"],
        "messages": messages,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if settings.get("max_tokens"):
        payload["max_tokens"] = settings["max_tokens"]
    headers = {
        "Authorization": f"Bearer {settings['api_key']}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    def attempt(body: dict):
        return requests.post(
            _chat_url(settings["base_url"]),
            json=body,
            headers=headers,
            stream=True,
            timeout=(15, 120),
        )

    def open_stream(body: dict):
        """Open the SSE stream, retrying only a short rate limit once.

        Providers that exhaust a daily quota send no ``Retry-After`` (or a huge
        one); retrying then only delays the error, so the request fails fast.
        """
        response = attempt(body)
        if response.status_code != 429:
            return response
        try:
            wait = float(response.headers.get("retry-after", ""))
        except (TypeError, ValueError):
            return response
        if not 0 < wait <= 10:
            return response
        response.close()
        time.sleep(wait)
        return attempt(body)

    try:
        response = open_stream(payload)
        if response.status_code == 400:
            detail = response.text.lower()
            if "max_tokens" in detail or "max_completion_tokens" in detail:
                response.close()
                payload.pop("max_tokens", None)
                response = open_stream(payload)
        if response.status_code >= 400:
            try:
                raise _completion_error(response)
            finally:
                response.close()
    except requests.RequestException as exc:
        raise AssistantError(
            "No se pudo conectar con el proveedor del asistente. Revisa la URL y la conexión."
        ) from exc

    content_parts: list[str] = []
    calls: dict[int, dict] = {}
    try:
        for raw in response.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except ValueError:
                continue
            choices = event.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            text = delta.get("content")
            if text:
                content_parts.append(text)
                yield "delta", text
            for item in delta.get("tool_calls") or []:
                index = int(item.get("index", 0))
                slot = calls.setdefault(
                    index,
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if item.get("id"):
                    slot["id"] = item["id"]
                if item.get("extra_content"):
                    # Gemini 3 returns a thought_signature that must be echoed
                    # back in the assistant tool-call message.
                    slot["extra_content"] = item["extra_content"]
                function = item.get("function") or {}
                if function.get("name"):
                    slot["function"]["name"] = function["name"]
                if function.get("arguments"):
                    slot["function"]["arguments"] += function["arguments"]
    finally:
        response.close()

    ordered = [calls[key] for key in sorted(calls)]
    for index, call in enumerate(ordered):
        if not call["id"]:
            call["id"] = f"call_{index}"
    yield "result", {"content": "".join(content_parts), "tool_calls": ordered}


def _viewer_context(dataset_id: str | None) -> str:
    """Live data brief that travels with every question.

    The model receives the numbers it needs up front, so a normal question can
    be answered in a single request. Tools remain available for anything that
    needs a fresh computation (difference imaging, spectra, catalog crossmatch).
    """
    try:
        datasets = store.list_datasets()
    except Exception:
        datasets = []
    lines = ["DATOS ACTUALES DEL VISOR (calculados ahora; son la verdad del turno):"]
    dataset = None
    if datasets:
        try:
            dataset = _dataset(dataset_id)
        except AssistantError:
            dataset = None

    if dataset is not None:
        summary = _dataset_summary(dataset)
        lines.append(
            f"Conjunto abierto: {summary['label']} ({summary['id']}) · "
            f"{summary['kind']} · {summary.get('object_type') or 'campo'}"
        )
        if summary.get("description"):
            lines.append(f"Descripción: {summary['description']}")
        lines.append(
            f"Centro RA {summary['ra']}° Dec {summary['dec']}° · FOV {summary['fov_deg']}° · "
            f"escala {summary['pixel_scale_arcsec']}″/px · rejilla {summary['npix']}² · "
            f"{summary['n_frames']} frames"
        )
        epochs = summary["epochs"]
        if epochs:
            lines.append(
                "Épocas: "
                + " | ".join(
                    f"{e['index']}: {e['date']} (MJD {e['mjd']}; "
                    f"detectores {', '.join(e['detectors'])})"
                    for e in epochs
                )
            )
        try:
            coverage = _coverage(dataset, 0)
            ranges = ", ".join(
                f"{detector} {info['min_um']:.2f}–{info['max_um']:.2f} µm "
                f"({coverage['by_detector'].get(detector, 0)} canales)"
                for detector, info in sorted(coverage["wavelength_ranges"].items())
            )
            lines.append(
                f"Cobertura época 0: {coverage['bands_covered']}/{coverage['bands_total']} canales"
                + (f" · {ranges}" if ranges else "")
            )
        except Exception:
            pass
        try:
            quality = analysis.quality_report(dataset, 0)
            if quality.get("available"):
                lines.append(
                    f"Calidad época 0: contaminación {quality['contamination_index']} "
                    f"({quality['contamination_flag']}) · flags "
                    f"{quality['flagged_fraction_pct']} % · rayos cósmicos "
                    f"{quality['cosmic_ray_proxy_pct']} % · válidos "
                    f"{quality['valid_fraction_pct']} % · fondo "
                    f"{quality['background_median']} ± {quality['background_sigma']}"
                )
        except Exception:
            pass
    else:
        lines.append("No hay un conjunto abierto indicado.")

    lines.append("Conjuntos disponibles:")
    for item in datasets[:12]:
        marker = " (abierto)" if dataset is not None and item["id"] == dataset.get("id") else ""
        lines.append(
            f"- {item['id']} · {item.get('label')} · {item.get('kind')} · "
            f"{item.get('n_epochs')} épocas{marker}"
        )

    lines.append(
        "Qué hacer con estos datos: responde con estas cifras, compáralas entre "
        "épocas, interpreta la calidad y la cobertura, y propone el siguiente paso "
        "concreto en el visor. Si falta un cálculo (candidatos, espectro, cruce, "
        "búsqueda), llama a la herramienta; no inventes valores."
    )
    try:
        lines.append("\n" + catalog.catalog_facts())
    except Exception:
        pass
    try:
        status = model.status()
        if status.get("available"):
            lines.append(
                f"\nClasificador de candidatos v{status.get('version')}: "
                f"exactitud {status.get('accuracy')} en {status.get('n_test')} pruebas, "
                f"entrenado con {status.get('n_train')} muestras. "
                "Es una ayuda al cribado, no una confirmación."
            )
        else:
            lines.append("\nClasificador de candidatos no entrenado.")
    except Exception:
        pass
    return "\n".join(lines)


def stream_reply(question: str, history: list[dict], dataset_id: str | None):
    """Run the agent loop, yielding ``{"type": ...}`` events."""
    settings = config.ai_settings()
    if not settings["configured"]:
        answer = offline_answer(question, dataset_id, history=history)
        yield {"type": "delta", "text": answer}
        return

    try:
        passages = catalog.knowledge_context(question, k=4, max_chars=3200)
    except Exception:
        passages = ""
    system = SYSTEM_PROMPT + "\n\n" + _viewer_context(dataset_id)
    if passages:
        system += (
            "\n\nPasajes recuperados de la base de conocimiento del proyecto "
            "(cita la fuente cuando los uses):\n" + passages
        )
    messages = [{"role": "system", "content": system}]
    for message in history[-HISTORY_MESSAGES:]:
        role = message.get("role")
        content = message.get("content") or ""
        if role in ("user", "assistant") and content.strip():
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})

    answer = ""
    try:
        for _step in range(MAX_TOOL_STEPS):
            result = None
            for kind, value in _stream_completion(messages, TOOLS, settings):
                if kind == "delta":
                    answer += value
                    yield {"type": "delta", "text": value}
                else:
                    result = value
            if result is None:
                break
            calls = result["tool_calls"]
            if not calls:
                break

            assistant_calls = calls
            if not _is_google(settings):
                assistant_calls = [
                    {key: value for key, value in call.items() if key != "extra_content"}
                    for call in calls
                ]
            messages.append(
                {
                    "role": "assistant",
                    "content": result["content"] or None,
                    "tool_calls": assistant_calls,
                }
            )
            for call in calls:
                name = call["function"]["name"]
                yield {"type": "tool", "name": name, "label": TOOL_LABELS.get(name, name)}
                try:
                    arguments = json.loads(call["function"]["arguments"] or "{}")
                    if not isinstance(arguments, dict):
                        arguments = {}
                except ValueError:
                    arguments = {}
                try:
                    data = _run_tool(name, arguments, dataset_id)
                    content = json.dumps(_clean(data), ensure_ascii=False)[:MAX_TOOL_CHARS]
                except Exception as exc:  # noqa: BLE001 - reported back to the model
                    content = json.dumps({"error": str(exc)}, ensure_ascii=False)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "name": name,
                        "content": content,
                    }
                )
    except AssistantError as exc:
        # A quota limit should not leave the user without an answer: the local
        # expert reads the same datasets and needs no external service.
        if exc.status != 429:
            raise
        yield {
            "type": "delta",
            "text": (
                "El modelo externo alcanzó su límite de uso. "
                "Respondo con el experto local del visor.\n\n"
            ),
        }
        if not answer.strip():
            yield {
                "type": "delta",
                "text": offline_answer(question, dataset_id, history=history),
            }
        return

    if not answer.strip():
        answer = offline_answer(question, dataset_id, history=history)
        yield {"type": "delta", "text": answer}


# ---------------------------------------------------------------------------
# Local expert (no API key required)
# ---------------------------------------------------------------------------
def _facts(dataset_id: str | None) -> dict:
    try:
        dataset = _dataset(dataset_id)
    except Exception:
        return {}
    try:
        coverage = analysis.band_coverage(dataset, 0)
        covered = sum(1 for c in coverage if c["covered"])
        quality = analysis.quality_report(dataset, 0)
    except Exception:
        covered = 0
        quality = {}
    return {
        "id": dataset.get("id"),
        "label": dataset.get("label"),
        "kind": dataset.get("kind"),
        "ra": dataset.get("ra"),
        "dec": dataset.get("dec"),
        "fov": dataset.get("fov"),
        "npix": (dataset.get("grid") or {}).get("npix", dataset.get("npix")),
        "epochs": len(dataset.get("epochs", [])),
        "frames": len(dataset.get("frames", {})),
        "covered": covered,
        "contamination": quality.get("contamination_index"),
        "contamination_flag": quality.get("contamination_flag"),
    }


def _dataset_line(facts: dict) -> str:
    if not facts:
        return "No hay un campo abierto ahora mismo."
    kind = {
        "archive": "datos reales de IRSA",
        "demo": "demostración sintética",
        "upload": "carga propia",
    }.get(facts.get("kind"), facts.get("kind"))
    return (
        f"**{facts.get('label')}** (`{facts.get('id')}`), {kind}. "
        f"RA {facts.get('ra')}°, Dec {facts.get('dec')}°, FOV {facts.get('fov')}°, "
        f"{facts.get('npix')} × {facts.get('npix')} px, "
        f"{facts.get('epochs')} épocas y {facts.get('frames')} frames."
    )


CONFIG_HINT = """**Modo local.** Respondo con la base de conocimiento del proyecto, el
índice SPHEREx y análisis deterministas sobre el campo abierto. No hay un modelo
de lenguaje externo conectado, así que no improviso texto libre: ejecuto
consultas y explico los resultados.

Para que las respuestas las genere un LLM, crea un archivo `.env` en la raíz:

```ini
SPHEREX_AI_BASE_URL=https://api.openai.com/v1
SPHEREX_AI_API_KEY=tu_clave
SPHEREX_AI_MODEL=gpt-4o-mini
```

También sirve cualquier endpoint compatible con OpenAI, incluido un servidor
local como Ollama (`SPHEREX_AI_BASE_URL=http://127.0.0.1:11434/v1`)."""


# ---------------------------------------------------------------------------
# Question parsing
# ---------------------------------------------------------------------------
_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

_EPOCH_WORDS = {
    "primer": 0, "primera": 0, "inicial": 0,
    "segund": 1, "segunda": 1,
    "tercer": 2, "tercera": 2,
    "cuart": 3, "cuarta": 3,
}


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _fmt(value, digits: int = 1) -> str:
    number = _as_float(value)
    if number is None:
        return "—"
    if abs(number) >= 10000:
        return f"{number:,.0f}".replace(",", " ")
    text = f"{number:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _fmt_int(value) -> str:
    number = _as_float(value)
    if number is None:
        return "—"
    return f"{int(round(number)):,}".replace(",", " ")


def _kind_label(kind: str | None) -> str:
    return {
        "archive": "datos reales de IRSA",
        "demo": "demostración sintética",
        "upload": "carga propia",
    }.get(kind or "", kind or "dataset")


def _epoch_desc(dataset: dict | None, index: int) -> str:
    if not dataset:
        return f"época {index}"
    try:
        epoch = dataset["epochs"][index]
    except (KeyError, IndexError, TypeError):
        return f"época {index}"
    date = epoch.get("date") or "sin fecha"
    detectors = " ".join(epoch.get("detectors") or [])
    return f"época {index} · {date}" + (f" · {detectors}" if detectors else "")


def _separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    import math

    lon1, lat1, lon2, lat2 = map(math.radians, (ra1, dec1, ra2, dec2))
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * math.asin(min(1.0, math.sqrt(a))) * 206264.806


def _mjd_from_date(year: int, month: int = 1, day: int = 1) -> float:
    from datetime import datetime, timezone

    epoch = datetime(1858, 11, 17, tzinfo=timezone.utc)
    moment = datetime(year, month, day, tzinfo=timezone.utc)
    return (moment - epoch).total_seconds() / 86400.0


def _parse_coords(text: str) -> tuple[float | None, float | None]:
    ra = dec = None
    match = re.search(r"\bra\s*[:=]?\s*([-+]?\d+(?:[.,]\d+)?)", text, re.I)
    if match:
        ra = _as_float(match.group(1))
    match = re.search(
        r"\bdec(?:linaci[oó]n)?\s*[:=]?\s*([-+]?\d+(?:[.,]\d+)?)", text, re.I
    )
    if match:
        dec = _as_float(match.group(1))
    if ra is None or dec is None:
        pair = re.search(
            r"(\d{1,3}[.,]\d+)\s*(?:°|deg|grados?)?\s*[,;/ ]\s*"
            r"([-+]?\d{1,2}[.,]\d+)\s*(?:°|deg|grados?)?",
            text,
            re.I,
        )
        if pair:
            ra2, dec2 = _as_float(pair.group(1)), _as_float(pair.group(2))
            if ra is None and ra2 is not None:
                ra = ra2
            if dec is None and dec2 is not None:
                dec = dec2
    if ra is not None and not 0.0 <= ra <= 360.0:
        ra = None
    if dec is not None and not -90.0 <= dec <= 90.0:
        dec = None
    if ra is None or dec is None:
        return None, None
    return ra, dec


def _parse_radius(text: str) -> float | None:
    match = re.search(
        r"(?:radio|radius)\s*(?:de\s*)?(\d+(?:[.,]\d+)?)\s*(?:°|deg|grados?)?",
        text,
        re.I,
    )
    if not match:
        return None
    value = _as_float(match.group(1))
    if value is None:
        return None
    return max(0.01, min(value, 5.0))


def _parse_band(text: str) -> str | None:
    match = re.search(
        r"\b(?:detector|banda|band)\s*(?:D\s*)?([1-6])\b", text, re.I
    )
    if match:
        return f"SPHEREx-D{match.group(1)}"
    match = re.search(r"\bD([1-6])\b", text)
    if match:
        return f"SPHEREx-D{match.group(1)}"
    match = re.search(r"(?:canal|channel)\s*(\d{1,3})", text, re.I)
    if match:
        index = int(match.group(1))
        if 1 <= index <= len(BANDS):
            return BANDS[index - 1]["detector"]
    match = re.search(
        r"(\d+(?:[.,]\d+)?)\s*(?:µm|um|micras?|micr[oó]metros?)\b", text, re.I
    )
    if match:
        wavelength = _as_float(match.group(1))
        if wavelength is not None:
            best = min(BANDS, key=lambda band: abs(band["wavelength_um"] - wavelength))
            return best["detector"]
    return None


def _parse_epochs(text: str, n_epochs: int) -> list[int]:
    found: list[int] = []
    for raw in re.findall(
        r"(?:época|epoca|epoch|observaci[oó]n)\s*(?:n[uú]mero\s*)?(\d+)",
        text,
        re.I,
    ):
        value = int(raw)
        if n_epochs and 0 <= value < n_epochs:
            found.append(value)
    for word, index in _EPOCH_WORDS.items():
        if re.search(rf"\b{word}[oa]?\b", text, re.I) and n_epochs and index < n_epochs:
            found.append(index)
    if re.search(r"\b(última|ultima|final|reciente)\b", text, re.I) and n_epochs:
        found.append(n_epochs - 1)
    return list(dict.fromkeys(found))


def _parse_threshold(text: str) -> float | None:
    match = re.search(
        r"(?:umbral|threshold)\s*(?:de\s*)?(\d+(?:[.,]\d+)?)", text, re.I
    )
    if not match:
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:σ|sigma)", text, re.I)
    if not match:
        return None
    value = _as_float(match.group(1))
    if value is None:
        return None
    return max(2.0, min(value, 12.0))


def _parse_mjd_window(text: str) -> tuple[float | None, float | None]:
    match = re.search(
        r"mjd\s*(\d{5}(?:\.\d+)?)\s*(?:[-–—a]\s*(\d{5}(?:\.\d+)?))?",
        text,
        re.I,
    )
    if match:
        start = _as_float(match.group(1))
        end = _as_float(match.group(2)) if match.group(2) else start
        if start is not None and end is not None:
            if end < start:
                start, end = end, start
            return start, end
    match = re.search(r"(20\d{2})-(\d{2})(?:-(\d{2}))?", text)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3)) if match.group(3) else None
        if day:
            center = _mjd_from_date(year, month, day)
            return center - 1.5, center + 1.5
        center = _mjd_from_date(year, month, 15)
        return center - 20.0, center + 20.0
    for name, month in _MONTHS.items():
        match = re.search(rf"\b{name}\b(?:\s*(?:de\s*)?(20\d{{2}}))?", text, re.I)
        if match:
            year = int(match.group(1)) if match.group(1) else 2026
            center = _mjd_from_date(year, month, 15)
            return center - 20.0, center + 20.0
    match = re.search(r"\b(20\d{2})\b", text)
    if match:
        center = _mjd_from_date(int(match.group(1)), 7, 1)
        return center - 183.0, center + 183.0
    return None, None


def _parse_candidate_ref(text: str) -> int | None:
    match = re.search(
        r"candidat[oa]\s*(?:n[uú]mero\s*)?(\d+)", text, re.I
    )
    if match:
        try:
            return max(0, int(match.group(1)) - 1)
        except ValueError:
            return None
    match = re.search(r"\b(primer[oa]?|segund[oa]?|tercer[oa]?|cuart[oa]?)\b", text, re.I)
    if match:
        word = match.group(1).lower()
        for stem, index in _EPOCH_WORDS.items():
            if word.startswith(stem):
                return index
    return None


def _parse_request(question: str, dataset: dict | None, history: list[dict]) -> dict:
    text = question or ""
    q = text.lower()
    n_epochs = len(dataset.get("epochs", [])) if dataset else 0
    ra, dec = _parse_coords(text)
    previous = " ".join(
        message.get("content", "")
        for message in history[-6:]
        if message.get("role") == "user"
    )
    if ra is None and re.search(
        r"ah[ií]|esa posici[oó]n|ese punto|el mismo|la misma|tambi[eé]n", q
    ):
        ra, dec = _parse_coords(previous)
    epochs = _parse_epochs(text, n_epochs)
    if not epochs and re.search(r"esa [eé]poca|la misma [eé]poca", q):
        epochs = _parse_epochs(previous, n_epochs)
    mjd_min, mjd_max = _parse_mjd_window(text)
    return {
        "ra": ra,
        "dec": dec,
        "radius": _parse_radius(text),
        "band": _parse_band(text),
        "epochs": epochs,
        "epoch": epochs[0] if epochs else None,
        "epoch_a": epochs[0] if epochs else None,
        "epoch_b": epochs[1] if len(epochs) > 1 else None,
        "threshold": _parse_threshold(text),
        "mjd_min": mjd_min,
        "mjd_max": mjd_max,
        "candidate": _parse_candidate_ref(text),
    }


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------
_INTENT_RULES = [
    ("greeting", 5, [
        r"^(hola|buenas|hey|saludos|qu[eé] tal|buenos d[ií]as|buenas tardes)\b|"
        r"\bc[oó]mo est[aá]s\b",
    ]),
    ("thanks", 4, [r"\b(gracias|perfecto|genial|excelente|muy bien|de acuerdo)\b"]),
    ("capabilities", 4, [
        r"qu[eé] puedes|qu[eé] sabes|para qu[eé] sirves|qu[eé] haces|"
        r"c[oó]mo me puedes|c[oó]mo funciona|ayuda|capacidades|"
        r"qui[eé]n eres|qu[eé] eres|a qu[eé] te dedicas",
    ]),
    ("datasets", 4, [
        r"qu[eé] datasets|conjuntos de datos|campos disponibles|lista de campos|"
        r"qu[eé] campos|datasets",
    ]),
    ("field", 3, [
        r"resume|resumen|describe|qu[eé] campo|qu[eé] estoy viendo|"
        r"informaci[oó]n del campo|datos del campo|estado del campo",
    ]),
    ("bands", 3, [
        r"banda|canal|cobertura|102|longitud de onda|wavelength|µm|micras|"
        r"micr[oó]metros",
    ]),
    ("validation", 5, [
        r"c[oó]mo (?:distingo|diferencio|s[eé] si|valido|reviso|descarto|confirmo)|"
        r"criterios|validar|validaci[oó]n|falso positivo|"
        r"artefacto.*(?:transitori|real)|(?:transitori|real).*artefacto|"
        r"qu[eé] revisar|se[nñ]ales de",
    ]),
    ("quality", 2, [
        r"calidad|contamina|flag|ruido|satura|zodi|rayo c[oó]smico|artefacto",
    ]),
    ("detect", 4, [
        r"detect|candidat|movimiento|compara|comparar|diferencia|resta|"
        r"transitori|variable|dipolo|asteroide|cometa|imagen",
    ]),
    ("spectrum", 4, [
        r"espectro|spectrum|firma|absorci[oó]n|emisi[oó]n|l[ií]nea espectral|"
        r"l[ií]neas espectrales",
    ]),
    ("crossmatch", 3, [r"gaia|wise|contrapartida|cruce|crossmatch|cat[aá]logo"]),
    ("catalog", 3, [
        r"busca|b[uú]squeda|observaciones|[ií]ndice|archivo|cu[aá]ndo|fecha|mjd|"
        r"disponible|cobertura real",
    ]),
    ("mission", 2, [
        r"spher|nasa|misi[oó]n|infrarrojo|universo|cosmos|galaxia|estrella|"
        r"polvo|hielo|astrof[ií]sica|ciencia",
    ]),
    ("challenge", 3, [
        r"desaf[ií]o|space apps|reto|jurado|pitch|entrega|demo day",
    ]),
    ("classifier", 2, [
        r"clasificador|machine learning|entren|etiqueta|inteligencia artificial",
    ]),
    ("provenance", 2, [
        r"\bdatos\b|irsa|fits|level-2|nivel 2|fuente|procedencia|descarga|tap",
    ]),
    ("accounts", 3, [
        r"cuenta|login|usuario|registro|contrase[nñ]a|sesi[oó]n",
    ]),
    ("config", 3, [
        r"\bapi\b|clave|openai|configurar|activar el agente|modo local|ollama|"
        r"modelo externo",
    ]),
]
_INTENT_ORDER = [name for name, _, _ in _INTENT_RULES]


def _intent_scores(text: str) -> dict[str, int]:
    q = (text or "").lower()
    scores: dict[str, int] = {}
    for name, weight, patterns in _INTENT_RULES:
        hits = sum(1 for pattern in patterns if re.search(pattern, q, re.I))
        if hits:
            scores[name] = scores.get(name, 0) + weight + min(hits - 1, 2)
    return scores


def _select_intents(text: str) -> list[str]:
    scores = _intent_scores(text)
    if not scores:
        return ["fallback"]
    ranked = sorted(
        scores.items(), key=lambda item: (-item[1], _INTENT_ORDER.index(item[0]))
    )
    primary = ranked[0][0]
    selected = [primary]
    for name, score in ranked[1:]:
        if len(selected) >= 3:
            break
        if score >= max(2, scores[primary] * 0.7):
            selected.append(name)
    if "greeting" in selected and "capabilities" in selected:
        selected.remove("capabilities")
    if "validation" in selected:
        # "¿Cómo distingo un artefacto?" is a method question, not a request to
        # run a new detection or quality report.
        for name in ("detect", "quality"):
            if name in selected:
                selected.remove(name)
    if (
        "spectrum" in selected
        and "detect" in selected
        and re.search(r"espectro.*candidat|candidat.*espectro", (text or "").lower())
    ):
        # The spectrum handler already locates the candidate from the previous
        # turn; repeating the full detection would only add noise.
        selected.remove("detect")
    return selected


# ---------------------------------------------------------------------------
# Answer sections
# ---------------------------------------------------------------------------
def _candidate_lines(candidates: list[dict], limit: int = 5) -> str:
    lines = []
    for index, candidate in enumerate(candidates[:limit], 1):
        ml = candidate.get("ml") or {}
        cat = candidate.get("catalog") or {}
        details = [f"SNR {_fmt(candidate.get('snr'), 1)}σ"]
        motion = candidate.get("motion") or {}
        if motion:
            details.append(
                f"{_fmt(motion.get('arcsec'), 1)}″ en "
                f"{_fmt(motion.get('baseline_days'), 1)} días "
                f"({_fmt(motion.get('rate_arcsec_day'), 2)}″/día)"
            )
        if ml.get("available"):
            details.append(
                f"clasificador: {ml.get('label_es')} "
                f"({_fmt((ml.get('confidence') or 0) * 100, 0)} %)"
            )
        if cat.get("available"):
            details.append(
                "con contrapartida Gaia/WISE"
                if cat.get("has_counterpart")
                else "sin contrapartida Gaia/WISE"
            )
        position = candidate.get("position") or {}
        if position.get("ra") is not None:
            details.append(
                f"RA {_fmt(position['ra'], 4)}°, Dec {_fmt(position['dec'], 4)}°"
            )
        lines.append(
            f"{index}. **{candidate.get('id')}** · {candidate.get('type_label')} · "
            + " · ".join(details)
        )
    return "\n".join(lines)


def _section_greeting(ctx: dict) -> str:
    facts = ctx["facts"]
    turns = ctx["turns"]
    variant = ctx["variant"]
    if turns == 0:
        opener = [
            "Hola. Soy el asistente científico del visor.",
            "Hola. ¿Qué quieres mirar hoy?",
            "Hola. Estoy listo para revisar los datos contigo.",
        ][variant % 3]
    else:
        opener = [
            "Hola de nuevo.",
            "Seguimos con los datos.",
            "Aquí estoy otra vez.",
        ][variant % 3]
    text = opener
    if facts:
        text += (
            f" Tienes abierto **{facts.get('label')}** "
            f"(`{facts.get('id')}`), {_kind_label(facts.get('kind'))} con "
            f"{facts.get('epochs')} épocas."
        )
    epochs = facts.get("epochs", 0) if facts else 0
    if epochs > 1:
        suggestions = [
            "Compara la primera y la última época y clasifica los candidatos.",
            "Resume la cobertura de canales y la calidad de la época 0.",
            "Mide el espectro en el centro del campo.",
        ]
    else:
        suggestions = [
            "Resume este campo y su cobertura.",
            "¿Qué canales están cubiertos y por qué no los 102?",
            "Busca observaciones SPHEREx en el índice local.",
        ]
    text += "\n\nPuedes pedirme, por ejemplo:\n" + "\n".join(
        f"- «{item}»" for item in suggestions
    )
    return text


def _section_capabilities(ctx: dict) -> str:
    facts = ctx["facts"]
    lines = ["Puedo hacer cuatro cosas con los datos que tienes cargados:"]
    lines.append(
        "- **Analizar el campo**: resumir coordenadas, épocas, cobertura de "
        "canales, calidad y contaminación."
    )
    lines.append(
        "- **Comparar imágenes entre épocas**: restar canales comunes, detectar "
        "dipolos y clasificar candidatos como movimiento, transitorio, variable "
        "o artefacto."
    )
    lines.append(
        "- **Medir espectros** en el centro del campo o en una coordenada, y "
        "cruzar la posición con Gaia DR3 y AllWISE."
    )
    lines.append(
        "- **Buscar en el índice local** de 355 observaciones SPHEREx y explicar "
        "la misión con la base de conocimiento del proyecto."
    )
    if facts:
        lines.append(
            f"\nAhora mismo trabajo sobre **{facts.get('label')}** "
            f"({_kind_label(facts.get('kind'))}, {facts.get('epochs')} épocas)."
        )
    lines.append(
        "\nEstoy en **modo local determinista**: ejecuto los análisis y explico "
        "cifras reales. Con una API compatible con OpenAI conectada, la redacción "
        "la genera un LLM y además puede encadenar herramientas."
    )
    return "\n".join(lines)


def _section_field(ctx: dict) -> str:
    facts = ctx["facts"]
    dataset = ctx["dataset"]
    if not facts:
        return (
            "No hay un campo abierto. Abre uno desde **Buscar campo** o carga un "
            "FITS y vuelvo a intentarlo."
        )
    lines = [
        f"**{facts.get('label')}** (`{facts.get('id')}`) · "
        f"{_kind_label(facts.get('kind'))}",
        f"- RA {_fmt(facts.get('ra'), 4)}° · Dec {_fmt(facts.get('dec'), 4)}° · "
        f"FOV {_fmt(facts.get('fov'), 2)}° · {facts.get('npix')} × "
        f"{facts.get('npix')} px · {facts.get('frames')} frames",
    ]
    if dataset:
        epochs = dataset.get("epochs", [])
        detail = "; ".join(_epoch_desc(dataset, index) for index in range(min(len(epochs), 6)))
        if len(epochs) > 6:
            detail += " …"
        lines.append(f"- Épocas: {len(epochs)} — {detail}")
    lines.append(f"- Cobertura época 0: {facts.get('covered')}/102 canales")
    lines.append(
        f"- Calidad: índice {_fmt(facts.get('contamination'), 1)} "
        f"({facts.get('contamination_flag') or 'sin calcular'})"
    )
    parsed = ctx["parsed"]
    ra, dec = parsed["ra"], parsed["dec"]
    if ra is not None and dec is not None:
        radius = parsed["radius"] or 0.5
        try:
            summary = catalog.field_summary(ra, dec, radius)
        except Exception:
            summary = {}
        if summary.get("available"):
            nearest = summary.get("nearest") or {}
            extra = (
                f"\nEn el índice local hay **{summary.get('n_observations')} "
                f"observaciones** ({summary.get('n_rows')} filas) en "
                f"{summary.get('n_bands')} bandas entre MJD "
                f"{_fmt_int(summary.get('mjd_min'))} y "
                f"{_fmt_int(summary.get('mjd_max'))} dentro de {radius}°"
            )
            if nearest.get("separation_arcsec") is not None:
                extra += (
                    f"; la más cercana está a "
                    f"{_fmt(nearest['separation_arcsec'], 1)}″."
                )
            else:
                extra += "."
            lines.append(extra)
    if facts.get("epochs", 0) > 1:
        lines.append(
            "\nHay varias épocas: puedo comparar la primera y la última y "
            "clasificar los candidatos."
        )
    return "\n".join(lines)


def _section_bands(ctx: dict) -> str:
    dataset = ctx["dataset"]
    if not dataset:
        return "No hay campo abierto: la cobertura de canales depende del dataset."
    epoch = ctx["parsed"]["epoch"] or 0
    try:
        coverage = analysis.band_coverage(dataset, epoch)
    except Exception as exc:
        return f"No pude calcular la cobertura de canales: {exc}"
    covered = [item for item in coverage if item.get("covered")]
    by_detector: dict[str, list[int]] = {}
    for item in coverage:
        detector = item.get("detector") or "?"
        bucket = by_detector.setdefault(detector, [0, 0])
        bucket[1] += 1
        bucket[0] += int(bool(item.get("covered")))
    detail = ", ".join(
        f"{detector} {values[0]}/{values[1]}"
        for detector, values in sorted(by_detector.items())
    )
    text = (
        f"En la **{_epoch_desc(dataset, epoch)}** hay "
        f"**{len(covered)}/102 canales** cubiertos ({detail})."
    )
    band = ctx["parsed"]["band"]
    if band:
        detector = band.replace("SPHEREx-", "")
        subset = [item for item in coverage if item.get("detector") == detector]
        wavelengths = [
            item["wavelength_um"] for item in subset if item.get("covered")
        ]
        if wavelengths:
            text += (
                f" El detector **{detector}** cubre "
                f"{len(wavelengths)}/{len(subset)} canales entre "
                f"{min(wavelengths):.2f} y {max(wavelengths):.2f} µm."
            )
        else:
            text += f" El detector **{detector}** no tiene canales cubiertos en esta época."
    if dataset.get("kind") == "demo":
        text += (
            " En esta demostración sintética los 102 canales están completos a "
            "propósito; en datos reales cada barrido cubre solo una franja."
        )
    else:
        text += (
            " SPHEREx usa 6 detectores LVF de 17 canales; un recorte level-2 "
            "muestrea una franja estrecha del espectro, no los 102 canales a la "
            "vez. Un canal se marca cubierto cuando al menos el 0.5 % de la "
            "cuadrícula tiene datos."
        )
    return text


def _section_quality(ctx: dict) -> str:
    dataset = ctx["dataset"]
    if not dataset:
        return "No hay campo abierto: la calidad se calcula por época."
    epoch = ctx["parsed"]["epoch"] or 0
    try:
        report = analysis.quality_report(dataset, epoch)
    except Exception as exc:
        return f"No pude calcular la calidad: {exc}"
    if not report.get("available"):
        return f"La {_epoch_desc(dataset, epoch)} no tiene frames utilizables."
    lines = [
        f"La **{_epoch_desc(dataset, epoch)}** tiene un índice de contaminación "
        f"**{_fmt(report.get('contamination_index'), 1)}** "
        f"({report.get('contamination_flag')}).",
        f"- Flags de calidad (bits 0–3): {_fmt(report.get('flagged_fraction_pct'), 2)} % de píxeles",
        f"- Rayos cósmicos (proxy): {_fmt(report.get('cosmic_ray_proxy_pct'), 2)} %",
        f"- Saturación (proxy): {_fmt(report.get('saturation_proxy_pct'), 2)} %",
        f"- Fondo: {_fmt(report.get('background_median'), 6)} ± "
        f"{_fmt(report.get('background_sigma'), 6)} MJy/sr",
        f"- Cobertura válida: {_fmt(report.get('valid_fraction_pct'), 1)} % · "
        f"{report.get('n_detectors')} detectores · ganancia SNR "
        f"{_fmt(report.get('snr_gain'), 2)}×",
    ]
    if (report.get("contamination_index") or 0) > 35:
        lines.append(
            "\nLa contaminación es alta: antes de creer un candidato, revisa "
            "flags, saturación y el patrón en varios canales."
        )
    return "\n".join(lines)


def _section_validation(ctx: dict) -> str:
    lines = [
        "**Artefacto o transitorio: criterios de validación.** Un residuo en la "
        "diferencia no es un descubrimiento hasta pasar estos filtros:",
        "- **Dipolo espacial**: un candidato real deja un par negativo/positivo "
        "en la misma posición; un artefacto suele ser una mancha o una franja "
        "alineada con el detector.",
        "- **Persistencia entre canales**: el residuo debe repetirse en varios "
        "canales o detectores. Un pico en un solo canal huele a rayo cósmico o "
        "píxel caliente.",
        "- **Flags y saturación**: revisa los bits 0–3 de `FLAGS`; los píxeles "
        "saturados o contaminados no sirven como evidencia.",
        "- **Forma y tamaño**: el área debe ser compatible con la PSF; un objeto "
        "extendido o un borde de detector es artefacto.",
        "- **Movimiento**: un asteroide o cometa se desplaza de forma lineal "
        "entre épocas; un transitorio aparece o desaparece sin desplazamiento; "
        "una variable cambia de brillo en el mismo píxel.",
        "- **Contrapartida Gaia/WISE**: una estrella conocida en la posición "
        "sugiere residuo de sustracción; la ausencia de contrapartida no prueba "
        "nada por sí sola.",
    ]
    dataset = ctx["dataset"]
    if dataset:
        try:
            report = analysis.quality_report(dataset, 0)
            index = report.get("contamination_index")
            if index is not None:
                verdict = (
                    "conviene ser conservador."
                    if (index or 0) > 35
                    else "el entorno es razonablemente limpio."
                )
                lines.append(
                    f"\nEn tu campo, la época 0 tiene un índice de contaminación "
                    f"**{_fmt(index, 1)}** ({report.get('contamination_flag')}); "
                    + verdict
                )
        except Exception:  # noqa: BLE001 - validation text is advisory
            pass
    ids: list[str] = []
    for message in reversed(ctx["history"]):
        if message.get("role") != "assistant":
            continue
        found = re.findall(r"SPX-[A-Z0-9-]+", message.get("content") or "")
        if found:
            # Keep the order of the most recent detection so the transient or
            # the first candidate is not silently dropped.
            ids = list(dict.fromkeys(found))
            break
    if ids:
        lines.append(
            "\nYa tienes candidatos en la conversación ("
            + ", ".join(ids[:4])
            + "). Pídeme el espectro de uno o su cruce Gaia/WISE para seguir "
            "validando."
        )
    return "\n".join(lines)


def _section_detect(ctx: dict) -> str:
    dataset = ctx["dataset"]
    if not dataset:
        return "No hay campo abierto: necesito dos épocas para comparar."
    n_epochs = len(dataset.get("epochs", []))
    if n_epochs < 2:
        return (
            "Este campo tiene una sola época, así que no puedo restar imágenes. "
            "Abre un dataset con al menos dos épocas o usa la demostración."
        )
    parsed = ctx["parsed"]
    epoch_a = parsed["epoch_a"] if parsed["epoch_a"] is not None else 0
    epoch_b = parsed["epoch_b"] if parsed["epoch_b"] is not None else n_epochs - 1
    epoch_a = max(0, min(epoch_a, n_epochs - 1))
    epoch_b = max(0, min(epoch_b, n_epochs - 1))
    if epoch_a == epoch_b:
        epoch_b = (epoch_a + 1) % n_epochs
    threshold = parsed["threshold"] or 6.0
    try:
        result = _detect(dataset, epoch_a, epoch_b, threshold)
    except Exception as exc:
        return f"No pude comparar las épocas: {exc}"
    candidates = result.get("candidates", [])
    if result.get("error"):
        return (
            f"La comparación no produjo una diferencia válida: {result['error']}. "
            "Prueba con otras épocas o un campo con más solapamiento."
        )
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.get("type")] = counts.get(candidate.get("type"), 0) + 1
    labels = {
        "moving": "movimiento",
        "transient": "transitorios",
        "variable": "variables",
        "artifact": "artefactos",
    }
    count_text = ", ".join(
        f"{value} {labels.get(key, key)}" for key, value in counts.items()
    )
    text = (
        f"Comparé la **{_epoch_desc(dataset, epoch_a)}** con la "
        f"**{_epoch_desc(dataset, epoch_b)}**: línea base de "
        f"{_fmt(result.get('baseline_days'), 1)} días, umbral {_fmt(threshold, 1)}σ, "
        f"método `{result.get('method')}`. "
        f"Encontré **{result.get('total_candidates', len(candidates))} candidatos**"
    )
    text += f" ({count_text})." if count_text else "."
    if result.get("matched_bands"):
        text += (
            f" La diferencia usa {len(result['matched_bands'])} canales comunes "
            f"y {_fmt_int(result.get('matched_pixels'))} píxeles."
        )
    if candidates:
        text += "\n\n" + _candidate_lines(candidates)
        text += (
            "\n\nEl clasificador es una ayuda al cribado, no una confirmación "
            "física. El siguiente paso es revisar el espectro, los flags y la "
            "contrapartida Gaia/WISE del candidato que te interese."
        )
    else:
        text += (
            "\n\nNo aparecieron residuos por encima del umbral. Puedes bajar a "
            "4σ o usar épocas más separadas en el tiempo; un cambio real débil "
            "puede quedar por debajo de 6σ."
        )
    return text


def _history_has_candidate(history: list[dict]) -> bool:
    return any(
        "SPX-" in (message.get("content") or "")
        for message in history
        if message.get("role") == "assistant"
    )


def _resolve_position(
    ctx: dict, allow_candidate: bool = True
) -> tuple[float | None, float | None, str, int | None]:
    parsed = ctx["parsed"]
    if parsed["ra"] is not None and parsed["dec"] is not None:
        return parsed["ra"], parsed["dec"], "las coordenadas indicadas", None
    dataset = ctx["dataset"]
    if (
        allow_candidate
        and parsed["candidate"] is not None
        and dataset
        and len(dataset.get("epochs", [])) > 1
        and _history_has_candidate(ctx["history"])
    ):
        try:
            epoch_b = len(dataset["epochs"]) - 1
            result = _detect(
                dataset, 0, epoch_b, parsed["threshold"] or 6.0
            )
            candidates = result.get("candidates", [])
            index = parsed["candidate"]
            if 0 <= index < len(candidates):
                position = candidates[index].get("position") or {}
                if position.get("ra") is not None:
                    return (
                        position["ra"],
                        position["dec"],
                        f"el candidato {candidates[index].get('id')}",
                        epoch_b,
                    )
        except Exception:
            pass
    if dataset:
        return dataset.get("ra"), dataset.get("dec"), "el centro del campo", None
    return None, None, "", None


def _section_spectrum(ctx: dict) -> str:
    dataset = ctx["dataset"]
    if not dataset:
        return "No hay campo abierto: el espectro se mide sobre un dataset cargado."
    ra, dec, where, epoch_hint = _resolve_position(ctx)
    epoch = ctx["parsed"]["epoch"]
    if epoch is None:
        epoch = epoch_hint if epoch_hint is not None else 0
    if ra is None or dec is None:
        return "Necesito una posición (RA/Dec) o un campo abierto para medir el espectro."
    try:
        spectrum = _spectrum(dataset, ra, dec, epoch)
    except Exception as exc:
        return f"No pude medir el espectro: {exc}"
    channels = spectrum.get("channels", [])
    if not channels:
        return (
            f"No hay cobertura espectral suficiente en {where} "
            f"(RA {_fmt(ra, 4)}°, Dec {_fmt(dec, 4)}°) para la época {epoch}."
        )
    fluxes = [item["flux"] for item in channels if item.get("flux") is not None]
    median = sorted(fluxes)[len(fluxes) // 2] if fluxes else None
    top = sorted(
        channels, key=lambda item: abs(item.get("flux") or 0.0), reverse=True
    )[:3]
    text = (
        f"Medí el espectro en {where} (RA {_fmt(ra, 4)}°, Dec {_fmt(dec, 4)}°, "
        f"{_epoch_desc(dataset, epoch)}): **{spectrum.get('coverage')}/102 canales** "
        f"con datos y {spectrum.get('native_samples')} muestras nativas."
    )
    if median is not None:
        text += (
            f" Flujo mediano **{_fmt(median, 4)} MJy/sr**, entre "
            f"{_fmt(min(fluxes), 4)} y {_fmt(max(fluxes), 4)}."
        )
    text += "\n\nCanales más brillantes:\n" + "\n".join(
        f"- {_fmt(item.get('wavelength_um'), 2)} µm · detector "
        f"{item.get('detector')} · {_fmt(item.get('flux'), 4)} MJy/sr"
        for item in top
    )
    if dataset.get("kind") == "demo":
        text += (
            "\n\nEn la demostración las unidades son arbitrarias y los 102 "
            "canales son sintéticos; sirve para practicar, no para astrofísica."
        )
    else:
        text += (
            "\n\nCon cobertura parcial en una época no infiero composición "
            "química. Para eso hacen falta más canales, una segunda época de "
            "referencia y comparación con modelos espectrales."
        )
    return text


def _section_crossmatch(ctx: dict) -> str:
    dataset = ctx["dataset"]
    ra, dec, where, _epoch_hint = _resolve_position(ctx, allow_candidate=False)
    if ra is None or dec is None:
        return "Necesito una posición (RA/Dec) o un campo abierto para cruzar catálogos."
    radius = ctx["parsed"]["radius"] or 0.5
    try:
        sources = crossmatch.field_sources(ra, dec, radius)
    except Exception as exc:
        return f"No pude consultar Gaia DR3/AllWISE: {exc}"
    if not sources.get("available"):
        errors = "; ".join(sources.get("errors") or [])
        return (
            "No pude consultar Gaia DR3/AllWISE ahora mismo"
            + (f" ({errors})" if errors else "")
            + ". El visor seguirá funcionando con el índice local y el "
            "clasificador; inténtalo de nuevo cuando haya red."
        )
    gaia = sources.get("gaia", [])
    wise = sources.get("wise", [])
    text = (
        f"Consulté Gaia DR3 y AllWISE en {where} (RA {_fmt(ra, 4)}°, "
        f"Dec {_fmt(dec, 4)}°, radio {radius}°): **{len(gaia)} fuentes Gaia** y "
        f"**{len(wise)} fuentes WISE**."
    )
    if sources.get("cached"):
        text += " El resultado viene de la caché local de 30 días."
    nearest_gaia = sorted(
        gaia, key=lambda item: _separation_arcsec(ra, dec, item["ra"], item["dec"])
    )[:3]
    if nearest_gaia:
        text += "\n\nGaia más cercanas:\n" + "\n".join(
            f"- {_fmt(_separation_arcsec(ra, dec, item['ra'], item['dec']), 1)}″ · "
            f"G {_fmt(item.get('mag'), 2)} · paralaje {_fmt(item.get('parallax'), 2)} mas"
            for item in nearest_gaia
        )
    nearest_wise = sorted(
        wise, key=lambda item: _separation_arcsec(ra, dec, item["ra"], item["dec"])
    )[:3]
    if nearest_wise:
        text += "\n\nWISE más cercanas:\n" + "\n".join(
            f"- {_fmt(_separation_arcsec(ra, dec, item['ra'], item['dec']), 1)}″ · "
            f"W1 {_fmt(item.get('w1'), 2)} · W2 {_fmt(item.get('w2'), 2)}"
            for item in nearest_wise
        )
    text += (
        "\n\nUna contrapartida no confirma ni descarta un candidato: sirve para "
        "separar estrellas conocidas de residuos de sustracción."
    )
    return text


def _section_catalog(ctx: dict) -> str:
    parsed = ctx["parsed"]
    ra, dec = parsed["ra"], parsed["dec"]
    dataset = ctx["dataset"]
    if ra is None and dec is None and dataset:
        ra, dec = dataset.get("ra"), dataset.get("dec")
    radius = parsed["radius"] or 0.5
    try:
        result = catalog.search(
            ra,
            dec,
            radius,
            band=parsed["band"],
            mjd_min=parsed["mjd_min"],
            mjd_max=parsed["mjd_max"],
            limit=200,
        )
    except Exception as exc:
        return f"No pude consultar el índice local: {exc}"
    rows = result.get("observations", [])
    if not rows:
        stats = catalog.stats()
        sky = stats.get("sky") or {}
        return (
            "No encontré observaciones con esos filtros. El índice local cubre "
            f"RA {_fmt(sky.get('ra_min'), 2)}–{_fmt(sky.get('ra_max'), 2)}°, "
            f"Dec {_fmt(sky.get('dec_min'), 2)}–{_fmt(sky.get('dec_max'), 2)}° y "
            f"MJD {_fmt_int((stats.get('time') or {}).get('mjd_min'))}–"
            f"{_fmt_int((stats.get('time') or {}).get('mjd_max'))}. "
            "Prueba con otra posición, banda o ventana temporal."
        )
    n_obs = len({row.get("obs_id") for row in rows})
    bands = sorted({row.get("band") for row in rows if row.get("band")})
    mjds = [row.get("t_min") for row in rows if row.get("t_min") is not None]
    where = "en el índice local"
    if ra is not None and dec is not None:
        where = f"en {radius}° alrededor de RA {_fmt(ra, 3)}°, Dec {_fmt(dec, 3)}°"
    text = (
        f"Hay **{len(rows)} filas** ({n_obs} observaciones) {where} en "
        f"{len(bands)} bandas ({', '.join(bands)})."
    )
    if mjds:
        text += (
            f" Fechas entre MJD {_fmt_int(min(mjds))} y {_fmt_int(max(mjds))}."
        )
    text += "\n\nMás cercanas:\n" + "\n".join(
        f"- {row.get('obs_id')} · {row.get('band')} · "
        f"{row.get('detector')} · MJD {_fmt(row.get('t_min'), 2)}"
        + (
            f" · {_fmt(row.get('separation_arcsec'), 1)}″"
            if row.get("separation_arcsec") is not None
            else ""
        )
        for row in rows[:5]
    )
    if parsed["band"]:
        text += f"\n\nFiltré por la banda **{parsed['band']}**."
    return text


def _section_mission(ctx: dict) -> str:
    q = ctx["q"]
    if re.search(r"hielo|agua|co2|mon[oó]xido|pah|hidrocarburo", q):
        text = (
            "**Qué busca SPHEREx en el infrarrojo.** La banda de 3.0–3.1 µm "
            "contiene la absorción del hielo de agua; el CO2 aparece cerca de "
            "4.26 µm, el CO cerca de 4.67 µm y los PAH tienen una banda fuerte "
            "en 3.3 µm. SPHEREx reparte ese rango en 102 canales, así que puede "
            "separar el continuo de estas firmas en muchas líneas de visión."
        )
    elif re.search(r"galaxia|cosmos|universo|cosmolog|mapa|redshift", q):
        text = (
            "**SPHEREx como mapa del universo.** El telescopio barre todo el "
            "cielo en el infrarrojo cercano y mide un espectro de baja resolución "
            "en cada posición. Eso permite estimar distancias de galaxias y "
            "construir un mapa tridimensional, además de estudiar la luz de "
            "fondo y la historia de formación estelar."
        )
    elif re.search(r"instrumento|detector|lvf|telescopio|espejo", q):
        text = (
            "**Instrumento.** SPHEREx usa un telescopio de 20 cm y seis "
            "detectores con filtros de longitud de onda variable (LVF). Cada "
            "detector cubre 17 canales y juntos suman 102 entre 0.75 y 5 µm. El "
            "barrido continuo del cielo es lo que permite comparar épocas y "
            "buscar cambios."
        )
    else:
        text = (
            "**SPHEREx en una frase.** Es un telescopio espacial de NASA que "
            "barre el cielo en el infrarrojo cercano con 102 canales entre 0.75 "
            "y 5 µm para estudiar hielos, polvo, galaxias y la historia del "
            "universo. En este visor ves recortes level-2 reales de IRSA y puedes "
            "comparar épocas para buscar objetos que se mueven o cambian."
        )
    text += (
        "\n\nEn tus datos, la cobertura es parcial por época: cada barrido "
        "muestrea una franja del espectro. Puedo decirte exactamente qué canales "
        "tienes cubiertos ahora."
    )
    return text


def _section_challenge(ctx: dict) -> str:
    facts = ctx["facts"]
    text = (
        "**Para el reto de NASA Space Apps.** Defiende tres ideas: (1) datos "
        "reales de NASA/IPAC IRSA, no una maqueta; (2) una interfaz que hace "
        "visible lo que un FITS esconde; (3) un método reproducible que va de la "
        "imagen al candidato y reconoce sus límites."
    )
    if facts and facts.get("epochs", 0) > 1:
        text += (
            f"\n\nCon **{facts.get('label')}** puedes demostrar en vivo la "
            "diferencia entre épocas, la clasificación de candidatos y el cruce "
            "con Gaia/WISE. Cierra con una limitación honesta: el clasificador "
            "es un cribado, no un descubrimiento."
        )
    else:
        text += (
            "\n\nAbre un campo con dos épocas para demostrar la comparación y "
            "la detección; después explica la cobertura parcial y el índice de "
            "contaminación."
        )
    return text


def _section_classifier(ctx: dict) -> str:
    classifier = ctx["classifier"]
    if not classifier.get("available"):
        return (
            "El clasificador aún no está entrenado. Puedes generarlo con "
            "`python -m spherex.model --train` o desde la tarjeta **Datos y "
            "modelo** del espacio de trabajo IA."
        )
    per_class = classifier.get("per_class") or {}
    class_lines = [
        f"- {classifier.get('classes_es', {}).get(label, label)}: "
        f"F1 {values.get('f1')} · soporte {values.get('support')}"
        for label, values in per_class.items()
    ]
    return (
        "**Clasificador de candidatos.** Es una regresión logística multiclase "
        "entrenada con 15 características del motor de detección: SNR, área, "
        "cociente de flujo, dipolo, tasa de movimiento, línea base, canales, "
        "umbral y contrapartidas Gaia/WISE.\n\n"
        f"- Versión {classifier.get('version')} · entrenado "
        f"{classifier.get('trained_at')}\n"
        f"- Exactitud en prueba **{classifier.get('accuracy')}** con "
        f"{classifier.get('n_test')} muestras ({classifier.get('n_train')} de "
        "entrenamiento)\n"
        + "\n".join(class_lines)
        + "\n\nLas etiquetas combinan posiciones conocidas de los campos demo, "
        "etiquetas débiles de Gaia/WISE y aumento de datos. La exactitud alta "
        "sobre un test pequeño no garantiza generalización: úsalo para ordenar "
        "candidatos, no como confirmación física."
    )


def _section_provenance(ctx: dict) -> str:
    stats = catalog.stats()
    time = stats.get("time") or {}
    sky = stats.get("sky") or {}
    instrument = stats.get("instrument") or {}
    return (
        "**Procedencia de los datos.** Los recortes vienen de NASA/IPAC IRSA: "
        "consulta TAP a `spherex.artifact` y `spherex.plane`, descarga de "
        "level-2 y lectura de `IMAGE`, `FLAGS`, `VARIANCE`, `ZODI` y "
        "`WCS-WAVE`. Después se reproyectan a una cuadrícula TAN y se registran "
        "por correlación cruzada FFT.\n\n"
        f"- Índice local: {stats.get('rows')} filas, "
        f"{stats.get('n_observations')} observaciones, "
        f"{stats.get('n_bands')} bandas\n"
        f"- Cielo: RA {_fmt(sky.get('ra_min'), 2)}–{_fmt(sky.get('ra_max'), 2)}°, "
        f"Dec {_fmt(sky.get('dec_min'), 2)}–{_fmt(sky.get('dec_max'), 2)}°\n"
        f"- Tiempo: MJD {_fmt_int(time.get('mjd_min'))}–"
        f"{_fmt_int(time.get('mjd_max'))} ({_fmt_int(time.get('days'))} días)\n"
        f"- Exposición {_fmt(instrument.get('exptime_s'), 2)} s · píxel "
        f"{_fmt(instrument.get('pixel_scale_arcsec'), 2)}″ · resolución "
        f"{_fmt(instrument.get('resolution_arcsec'), 2)}″"
    )


def _section_accounts(ctx: dict) -> str:
    return (
        "Las cuentas son opcionales para el visor y obligatorias para el "
        "asistente. Al registrarte se guardan el historial de conversaciones, "
        "los campos favoritos y las preferencias de visualización en una base "
        "SQLite local (`instance/spherex.db`). La sesión usa cookie firmada, "
        "CSRF y límite de intentos."
    )


def _section_datasets(ctx: dict) -> str:
    try:
        datasets = store.list_datasets()
    except Exception as exc:
        return f"No pude listar los datasets: {exc}"
    if not datasets:
        return "No hay datasets cargados. Usa **Buscar campo** o carga un FITS."
    lines = []
    for dataset in datasets[:8]:
        lines.append(
            f"- **{dataset.get('label')}** (`{dataset.get('id')}`) · "
            f"{_kind_label(dataset.get('kind'))} · "
            f"{dataset.get('n_epochs')} épocas · "
            f"RA {_fmt(dataset.get('ra'), 2)}°, Dec {_fmt(dataset.get('dec'), 2)}°"
        )
    return "Datasets disponibles:\n" + "\n".join(lines)


def _section_config(ctx: dict) -> str:
    return CONFIG_HINT


def _trim_text(text: str, limit: int = 700) -> str:
    cleaned = re.sub(r"^#{1,6}\s*", "", text or "", flags=re.M)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rsplit(" ", 1)[0] + "…"
    return cleaned


def _section_fallback(ctx: dict) -> str:
    question = ctx["question"]
    q = ctx["q"]
    if re.search(
        r"chiste|broma|clima|f[uú]tbol|pol[ií]tica|receta|poema|canci[oó]n|"
        r"cu[eé]ntame algo",
        q,
    ):
        return (
            "Eso queda fuera de mi especialidad. Estoy aquí para los datos de "
            "SPHEREx: puedo resumir el campo abierto, comparar épocas, medir un "
            "espectro o buscar observaciones en el índice local."
        )
    try:
        passages = catalog.search_knowledge(question, k=3)
    except Exception:
        passages = []
    if passages:
        top = passages[0]
        text = (
            f"No tengo una respuesta cerrada, pero lo más relevante de la base "
            f"de conocimiento es **{top.get('title')}** ({top.get('source')}):"
            f"\n\n{_trim_text(top.get('text'))}"
        )
        if len(passages) > 1:
            text += (
                f"\n\nTambién aparece **{passages[1].get('title')}** "
                f"({passages[1].get('source')})."
            )
        text += (
            "\n\nSi tu pregunta era sobre el campo abierto, dame una pista "
            "(época, banda o coordenada) y ejecuto el análisis."
        )
        return text
    return (
        "No encontré nada concreto para esa pregunta en la documentación. "
        "Puedo resumir el campo abierto, explicar la cobertura de canales, "
        "comparar épocas, medir un espectro o buscar en el índice local de "
        "SPHEREx. Dime cuál te interesa."
    )


_SECTION_HANDLERS = {
    "greeting": _section_greeting,
    "thanks": lambda ctx: (
        "De nada. Si quieres, seguimos con otra época, un espectro o una "
        "búsqueda en el índice local."
    ),
    "capabilities": _section_capabilities,
    "datasets": _section_datasets,
    "field": _section_field,
    "bands": _section_bands,
    "quality": _section_quality,
    "validation": _section_validation,
    "detect": _section_detect,
    "spectrum": _section_spectrum,
    "crossmatch": _section_crossmatch,
    "catalog": _section_catalog,
    "mission": _section_mission,
    "challenge": _section_challenge,
    "classifier": _section_classifier,
    "provenance": _section_provenance,
    "accounts": _section_accounts,
    "config": _section_config,
    "fallback": _section_fallback,
}


def _followup_line(ctx: dict, intents: list[str]) -> str:
    facts = ctx["facts"]
    epochs = facts.get("epochs", 0) if facts else 0
    if "detect" in intents:
        options = [
            "Pídeme el espectro del candidato 1, subir el umbral o cruzar el campo con Gaia/WISE.",
            "Puedo repetir la comparación con otras épocas o con umbral 4σ.",
        ]
    elif "validation" in intents:
        options = [
            "Puedo medir el espectro de un candidato y cruzar su posición con Gaia/WISE.",
            "Puedo repetir la comparación con otro umbral para ver si el residuo persiste.",
        ]
    elif "spectrum" in intents:
        options = [
            "Puedo medir el mismo punto en otra época o buscar su contrapartida Gaia/WISE.",
            "Si me das otra coordenada, repito el espectro.",
        ]
    elif "catalog" in intents:
        options = [
            "Puedo filtrar por banda o por una ventana de fechas concreta.",
            "Dime otra posición y busco en el índice local.",
        ]
    elif "bands" in intents:
        options = [
            "Puedo revisar la cobertura de otra época o medir el espectro del centro.",
            "Si quieres, comparo qué canales comparten dos épocas.",
        ]
    elif "crossmatch" in intents:
        options = [
            "Puedo repetir el cruce con otro radio o explicar cómo interpretar la contrapartida.",
            "También puedo medir el espectro en esa posición.",
        ]
    elif epochs > 1:
        options = [
            "Puedo comparar épocas y clasificar candidatos.",
            "Puedo resumir la cobertura de canales o medir un espectro.",
        ]
    else:
        options = [
            "Puedo resumir el campo abierto o buscar en el índice local.",
            "Puedo explicar la misión o la cobertura de canales.",
        ]
    return options[ctx["variant"] % len(options)]


def offline_answer(
    question: str, dataset_id: str | None, history: list[dict] | None = None
) -> str:
    """Deterministic local expert that runs real queries instead of templates."""
    history = history or []
    try:
        dataset = _dataset(dataset_id)
    except Exception:
        dataset = None
    facts = _facts(dataset_id)
    try:
        classifier = model.status()
    except Exception:
        classifier = {}
    parsed = _parse_request(question, dataset, history)
    turns = sum(1 for message in history if message.get("role") == "assistant")
    variant = (len(question or "") + turns * 3) % 3
    ctx = {
        "question": question or "",
        "q": (question or "").lower(),
        "history": history,
        "dataset": dataset,
        "facts": facts,
        "classifier": classifier,
        "parsed": parsed,
        "turns": turns,
        "variant": variant,
    }
    intents = _select_intents(question or "")
    sections: list[str] = []
    used: set[str] = set()
    for intent in intents:
        if intent in used:
            continue
        handler = _SECTION_HANDLERS.get(intent)
        if handler is None:
            continue
        try:
            section = handler(ctx)
        except Exception as exc:  # noqa: BLE001 - never break the chat
            section = f"No pude completar esa parte del análisis: {exc}"
        if section:
            sections.append(section)
            used.add(intent)
    if not sections:
        sections.append(_section_fallback(ctx))
        used.add("fallback")
    answer = "\n\n".join(sections)
    followup = _followup_line(ctx, list(used))
    if followup:
        answer += "\n\n**Siguiente paso:** " + followup
    return answer


def knowledge_topics() -> list[dict]:
    """Short suggested prompts for the chat panel."""
    return [
        {"label": "Resume este campo", "prompt": "Resume el campo abierto: coordenadas, épocas y cobertura."},
        {"label": "Cobertura de bandas", "prompt": "¿Qué canales están cubiertos en la época 0 y por qué no los 102?"},
        {"label": "Calidad de la época", "prompt": "¿Cómo está la calidad y la contaminación de la época 0?"},
        {"label": "Buscar movimiento", "prompt": "Compara la primera y la última época y dime si hay candidatos a objetos en movimiento."},
        {"label": "Espectro del centro", "prompt": "Mide el espectro en el centro del campo y descríbelo."},
        {"label": "Base de datos", "prompt": "¿Con qué datos SPHEREx, Gaia y WISE estás entrenado? Cita cifras del índice local."},
        {"label": "Cómo funciona", "prompt": "Explícame cómo se combinan los detectores y por qué se usa varianza inversa."},
    ]
