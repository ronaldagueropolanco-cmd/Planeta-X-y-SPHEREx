# SPHEREx Sky Time Viewer

Visor web para explorar datos en el dominio del tiempo de la misión **NASA
SPHEREx**. Descarga recortes level-2 reales del archivo **NASA/IPAC IRSA**, los
reproyecta a una cuadrícula común, compara épocas y genera imágenes de
diferencia, espectros y candidatos a objetos en movimiento, transitorios y
variables. Incluye cuentas con SQLite y un asistente experto en SPHEREx.

Proyecto para el **NASA Space Apps Challenge**. Interfaz en español, minimalista
y sin emojis.

## Qué hace

- **Datos reales.** Consulta IRSA por posición (TAP/ADQL), descarga recortes
  level-2 y lee `IMAGE`, `FLAGS`, `VARIANCE`, `ZODI` y `WCS-WAVE`.
- **102 canales infrarrojos.** 6 detectores LVF × 17 canales entre 0.75 y
  5.00 µm. La cobertura real se muestra de forma honesta: cada época muestrea
  solo una parte del espectro.
- **Reproyección y registro.** Cuadrícula TAN norte-arriba, alineado por
  correlación cruzada FFT y refinamiento subpíxel.
- **Comparación de épocas.** Resta en canales coincidentes para movimiento y
  variables; suma ponderada por varianza inversa para ganar señal/ruido.
- **Detección y espectros.** Mapa de significancia, dipolos, clasificación de
  candidatos y espectro instantáneo al hacer clic en el cielo.
- **Superposiciones.** Gaia DR3 y AllWISE por TAP.
- **URL compartible.** `?dataset=…&epoch=…&mode=…&band=…&panel=…&ai=1`.
- **Modo demo.** Cuatro escenarios sintéticos marcados como tales para funcionar
  sin conexión.
- **Cuentas.** Historial del asistente, favoritos y preferencias por usuario. El
  visor es público; solo el asistente requiere sesión.
- **Asistente.** Chat en español con panel lateral y espacio de trabajo a
  pantalla completa. Usa Google Gemini (o cualquier API compatible con OpenAI)
  y, si no hay clave, un experto local determinista.

## Requisitos

- Python 3.11 o superior (probado con 3.13).
- Internet para IRSA y las superposiciones.
- Flask, NumPy, SciPy, Pillow, Astropy, Astroquery, Reproject y Requests.
- Opcional: clave de Google Gemini u otra API compatible con OpenAI.

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Ejecución

```powershell
python server.py
```

Abrir <http://127.0.0.1:5000>. La primera vez se crean los escenarios demo y la
base de datos en `instance/spherex.db`. Si hay un dataset real en caché, se abre
el más reciente.

## Asistente con Gemini

Copia `.env.example` a `.env` y configura:

```dotenv
SPHEREX_AI_API_KEY=tu_clave
SPHEREX_AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
SPHEREX_AI_MODEL=gemini-3.5-flash-lite
SPHEREX_AI_MAX_TOKENS=1200
```

- `gemini-3.5-flash-lite` responde rápido y tiene más cuota gratuita;
  `gemini-3.5-flash` razona mejor pero se agota antes.
- Antes de cada pregunta el asistente calcula un bloque **DATOS ACTUALES DEL
  VISOR** (coordenadas, épocas, cobertura, calidad, catálogo y clasificador) y lo
  envía como contexto. Las herramientas (`detect_candidates`,
  `measure_spectrum`, `crossmatch_field`, `search_catalog`…) se usan solo para
  cálculos que no están en ese bloque.
- Si se agota la cuota (429), avisa y responde con el experto local.
- La clave vive solo en `.env`, excluido por `.gitignore`.

También se aceptan `OPENAI_API_KEY` y `OPENAI_BASE_URL`. Sin clave, el asistente
funciona en modo local.

## Precargar un campo real

```powershell
# Campo profundo por defecto, dos épocas compatibles
python ingest.py

# Campo concreto
python ingest.py --ra 210.8 --dec 54.3 --fov 0.4 --npix 192

# Épocas y detectores explícitos
python ingest.py --ra 276.2639 --dec 64.824 --fov 0.3 --npix 160 `
  --epoch-days 61200,61217 --detectors 6 --label "Campo profundo SPHEREx"

# Listar datasets en caché
python ingest.py --list
```

Opciones: `--ra`, `--dec`, `--fov` (0.03–2.0°), `--npix` (64–512), `--epochs`,
`--epoch-days`, `--detectors` (1–6) y `--label`. Los recortes quedan en
`uploads/cache/` (excluido de git).

## Estructura

```
server.py              Flask y API REST
ingest.py              Precarga de campos reales desde IRSA
spherex/
  bands.py             102 canales y 6 detectores LVF
  archive.py           TAP, cutouts, Sesame, Gaia/AllWISE
  frames.py            FITS, WCS-WAVE, reproyección, registro, coadd
  pipeline.py          Consulta → descarga → reproyección → dataset
  analysis.py          Detección, espectros, bandas y calidad
  render.py            Stretch, colormaps y PNG
  store.py             Persistencia de datasets
  demo.py              Escenarios sintéticos
  config.py            .env, sesión y ajustes del asistente
  db.py                SQLite: usuarios, chat, favoritos, preferencias
  auth.py              Sesiones, CSRF y límite de intentos
  catalog.py           Índice local y base de conocimiento BM25
  crossmatch.py        Cruce Gaia DR3 / AllWISE
  model.py             Clasificador de candidatos
  assistant.py         Agente, herramientas y experto local
data/                  Export del catálogo espectral IRSA
static/                HTML, CSS y JavaScript
instance/              SQLite y clave de sesión (se crea sola)
uploads/cache/         Recortes y datasets (se crea solo)
```

## Limitaciones

- **Cobertura parcial:** una época aporta ~6 muestras nativas (una por
  detector), no los 102 canales. La rejilla indica los canales realmente
  cubiertos.
- **Longitudes de onda variables:** la resta directa mezcla canales; por eso se
  calcula solo con canales comunes.
- **Movimiento:** los dipolos exigen al menos 4 píxeles de desplazamiento, lo
  que favorece objetos rápidos; los asteroides lentos necesitan líneas base
  mayores o píxeles más finos.
- **Clasificador:** usa etiquetas débiles y datos demo; ordena candidatos, no
  confirma descubrimientos.
- **Cuentas:** pensadas para uso local o evaluación; sin recuperación de
  contraseña ni verificación de correo.
- **Windows Smart App Control:** puede bloquear DLL sin firmar de Astropy
  (`_wcs`, `_parse_times`). Desactívalo o permite esas DLL.

## Documentación

La documentación técnica completa (arquitectura, API, flujo de datos, asistente,
índice local, clasificador y criterios del reto) está en
[`DOCUMENTACION.md`](DOCUMENTACION.md).

## Créditos

Desarrollado para el **NASA Space Apps Challenge**.

Datos: **NASA/IPAC Infrared Science Archive (IRSA)**, misión **SPHEREx**
(NASA JPL / Caltech). Catálogos: **Gaia DR3** (ESA) y **AllWISE** (NASA/IPAC).
