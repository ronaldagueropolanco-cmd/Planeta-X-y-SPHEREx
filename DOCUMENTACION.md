# SPHEREx Sky Time Viewer — Documentación funcional y técnica

Documento de referencia de todo lo que hace la aplicación: controles de la
interfaz, paneles, modos de visualización, procesamiento científico, cuentas,
asistente, API HTTP y flujo de datos.

Proyecto para el **NASA Space Apps Challenge**. Interfaz en español, minimalista,
sin emojis e iconografía SVG propia.

---

## Índice

1. [Qué es la aplicación](#1-qué-es-la-aplicación)
2. [Resumen de funciones](#2-resumen-de-funciones)
3. [Puesta en marcha](#3-puesta-en-marcha)
4. [Mapa de la interfaz](#4-mapa-de-la-interfaz)
5. [Barra superior](#5-barra-superior)
6. [Visor central](#6-visor-central)
7. [Barra lateral de herramientas](#7-barra-lateral-de-herramientas)
8. [Línea de tiempo y reproducción](#8-línea-de-tiempo-y-reproducción)
9. [Zoom, paneo y coordenadas](#9-zoom-paneo-y-coordenadas)
10. [Paneles en detalle](#10-paneles-en-detalle)
11. [Modos de visualización](#11-modos-de-visualización)
12. [Interacciones y atajos de teclado](#12-interacciones-y-atajos-de-teclado)
13. [Estado en la URL](#13-estado-en-la-url)
14. [Datos y procedencia](#14-datos-y-procedencia)
15. [Procesamiento científico](#15-procesamiento-científico)
16. [API HTTP](#16-api-http)
17. [Cuentas, base de datos y asistente](#17-cuentas-base-de-datos-y-asistente)
18. [Flujo completo de datos](#18-flujo-completo-de-datos)
19. [Criterios del reto y dónde se demuestran](#19-criterios-del-reto-y-dónde-se-demuestran)
20. [Limitaciones y notas científicas](#20-limitaciones-y-notas-científicas)
21. [Glosario](#21-glosario)
22. [Archivos del proyecto](#22-archivos-del-proyecto)

---

## 1. Qué es la aplicación

SPHEREx Sky Time Viewer es un visor web de datos en el dominio del tiempo de la
misión **NASA SPHEREx**. La aplicación:

- Consulta el archivo **NASA/IPAC IRSA** por posición (TAP/ADQL).
- Descarga recortes **level-2 reales** de SPHEREx.
- Los reproyecta a una cuadrícula común norte-arriba.
- Los registra y coadiciona para comparar épocas.
- Genera imágenes, mapas de diferencia, sumas, espectros y candidatos a objetos
  en movimiento, transitorios y variables.
- Permite superponer catálogos **Gaia DR3** y **AllWISE**.
- Permite cargar FITS o imágenes propias.
- Guarda el estado de la vista en la URL para compartirla.
- Ofrece cuentas opcionales con SQLite para guardar historial de chat, favoritos
  y preferencias de visualización.
- Incluye un asistente experto en SPHEREx que usa los datos del campo abierto,
  con modelo externo opcional y modo local sin conexión.

La interfaz está en español y usa una estética oscura, sobria y minimalista, con
iconos SVG lineales y sin emojis.

---

## 2. Resumen de funciones

| Acción del usuario | Resultado |
| --- | --- |
| Escribir un nombre o `RA Dec` y pulsar **Ir** | Resuelve el nombre (Sesame) o toma las coordenadas y consulta el archivo IRSA. |
| Pulsar **Consultar archivo** | Muestra cuántas observaciones y épocas hay para esa posición, con una lista de épocas seleccionables. |
| Pulsar **Cargar datos reales** | Descarga los recortes level-2, los reproyecta, los registra, los coadiciona y abre el nuevo dataset. |
| Elegir un campo de demostración | Abre un escenario sintético de tres épocas con los 102 canales disponibles. |
| Cambiar el conjunto de datos en **Capas** | Carga otro dataset y reinicia la vista. |
| Cambiar mapa de color o estiramiento | Vuelve a renderizar la imagen actual. |
| Elegir un detector | Muestra solo ese detector en la época actual; si se elige **Todos**, muestra el coadd. |
| Cambiar entre **Imagen**, **Suma** y **Diferencia** | Cambia el producto científico que se muestra en el visor. |
| Mover el deslizador de **Escala de diferencia** | Ajusta el contraste del mapa bipolar de diferencia. |
| Reproducir la línea de tiempo | Itera automáticamente por las épocas. |
| Hacer clic sobre la imagen | Extrae y dibuja el espectro del punto seleccionado. |
| Hacer clic en una celda de **102 canales** | Muestra la imagen de ese canal en la época actual. |
| Pulsar **Analizar movimiento** | Resta dos épocas y lista dipolos, transitorios y variables. |
| Pulsar un candidato | Hace zoom sobre él y lo resalta en la imagen. |
| Activar **Gaia DR3** o **AllWISE** | Consulta el catálogo por TAP y dibuja sus fuentes sobre la imagen. |
| Pulsar **Exportar informe JSON** | Descarga metadatos, calidad, cobertura, frames y detección. |
| Pulsar el icono de descarga del espectro | Descarga el espectro en CSV. |
| Arrastrar un FITS o imagen a **Cargar archivo** | Lo reproyecta a 192 × 192 y lo abre como dataset propio. |
| Copiar la URL | Restaura dataset, época, modo, canal, panel y coordenadas. |
| Pulsar el botón de cuenta y **Crear cuenta** | Registra un usuario y guarda sesión, favoritos y preferencias. |
| Pulsar **Guardar** en **Campos favoritos** | Añade o quita el dataset actual de favoritos del usuario. |
| Abrir el **Asistente SPHEREx** | Conversa sobre el campo abierto; exige sesión iniciada. |
| Pulsar el icono de expandir del panel o del rail | Abre el **Espacio de trabajo IA** a pantalla completa. |
| Pulsar un tema sugerido | Envía una pregunta predefinida al asistente. |
| Cambiar colormap, stretch o detector con sesión | Guarda la preferencia y la restaura al volver. |

---

## 3. Puesta en marcha

### Requisitos

- Python 3.11 o superior (probado con 3.13).
- Conexión a Internet para el archivo IRSA, Sesame, Gaia y AllWISE.
- Dependencias de `requirements.txt`: Flask, NumPy, SciPy, Pillow, Astropy,
  Astroquery, Reproject y Requests.

```powershell
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python server.py
```

Abrir <http://127.0.0.1:5000>.

Al arrancar:

1. Se generan en memoria los cuatro escenarios de demostración.
2. Se cargan los datasets persistidos en `uploads/cache/datasets/`.
3. Se inicializa SQLite y se crean las tablas si no existen.
4. El frontend abre el dataset de archivo más reciente; si no hay ninguno,
   abre `demo_neo_asteroid`.

### Cuentas, base de datos y configuración

- La base SQLite vive en `instance/spherex.db` y se crea automáticamente. Se
  puede cambiar con `SPHEREX_DB`.
- La clave de firma de sesiones se genera una vez en `instance/secret_key` si no
  se define `SPHEREX_SECRET_KEY`.
- Copia `.env.example` a `.env` para configurar el asistente y la base. Sin
  `.env`, el visor y las cuentas funcionan y el asistente responde en modo local.
- El visor es público. Solo el asistente y las rutas de favoritos, preferencias
  e historial requieren sesión.

### Precarga de campos reales

```powershell
# Campo profundo por defecto (dos épocas)
python ingest.py

# Campo concreto
python ingest.py --ra 210.8 --dec 54.3 --fov 0.4 --npix 192

# Épocas y detectores explícitos
python ingest.py --ra 276.2639 --dec 64.824 --fov 0.3 --npix 160 `
  --epoch-days 61200,61217 --detectors 6 --label "Campo profundo SPHEREx"

# Listar datasets en caché
python ingest.py --list
```

| Opción | Descripción |
| --- | --- |
| `--ra`, `--dec` | Centro del campo en grados ICRS. |
| `--fov` | Campo visual en grados (se limita a 0.03–2.0). |
| `--npix` | Tamaño de la cuadrícula en píxeles (64–512). |
| `--epochs` | Número máximo de épocas. |
| `--epoch-days` | Días MJD concretos, separados por comas. |
| `--detectors` | Número máximo de detectores por época (1–6). |
| `--label` | Nombre visible del dataset. |

Los recortes se guardan en `uploads/cache/cutouts/` y los datasets en
`uploads/cache/datasets/<id>/` (`meta.json` + `frames.npz`).

---

## 4. Mapa de la interfaz

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Barra superior: marca · buscador · estado IRSA · información · cuenta    │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   ┌──────────────────────────────────────────────┐   ┌───────────────┐   │
│   │                                              │   │               │   │
│   │              Visor de la imagen              │   │    Panel      │   │
│   │        (imagen + retícula + candidatos)      │   │  contextual   │   │
│   │                                              │   │               │   │
│   └──────────────────────────────────────────────┘   └───────────────┘   │
│                                                                          │
│   Rail de herramientas (8 botones)                                       │
│                                                                          │
│   Línea de tiempo: play · ticks de épocas · lectura de fecha             │
│                                                                          │
│   Zoom: + · − · reset                    Lectura RA / Dec / FOV          │
└──────────────────────────────────────────────────────────────────────────┘
```

Elementos fijos:

- **Barra superior:** marca, buscador global, chip de estado, botón de
  información y botón de cuenta/sesión.
- **Escenario central (`stage`):** fondo con retícula decorativa, visor,
  rail, panel, línea de tiempo, controles de zoom y lectura de coordenadas.
- **Visor (`viewport`):** contiene la imagen (`view-image`) y un canvas de
  superposiciones (`overlay`).
- **Panel (`aside`):** se abre a la derecha; en pantallas menores de 720 px se
  superpone.
- **Capa de carga:** tarjeta con spinner y mensajes rotativos.
- **Toast:** aviso breve de éxito o error en la parte inferior.

### Adaptación a pantallas

- En escritorio, el panel mide 390 px y el visor reserva 410 px a la derecha.
- Por debajo de 980 px, el panel se limita al ancho disponible.
- Por debajo de 720 px, el rail pasa a la parte inferior, el panel se superpone
  a la imagen y la línea de tiempo ocupa el ancho completo.

---

## 5. Barra superior

### Marca

Muestra el logotipo SVG de SPHEREx y el nombre **SPHEREx Sky Time Viewer**.

### Buscador global

- Campo con el texto `Buscar objeto o RA, Dec…`.
- Acepta dos formatos:
  - **Nombre de objeto:** `M31`, `NGC 4567`, `3C 273`, `R Draconis`…
  - **Dos números:** `276.2639 64.8240` o `276.2639, 64.8240`.
- Al pulsar **Ir**:
  - Si son dos números, rellena RA y Dec y limpia el nombre.
  - Si es texto, rellena el nombre y limpia RA/Dec.
  - Abre el panel **Buscar campo**.
  - Ejecuta una consulta al archivo (`/api/archive/query`), no una descarga.
- Si el campo está vacío, solo abre el panel de búsqueda.

### Chip de estado

- Consulta `/api/status` al cargar.
- Muestra `IRSA en línea · 102 bandas` si el archivo responde.
- Muestra `IRSA sin conexión · demo` si no responde.
- Muestra `Servidor sin conexión` si falla la API local.
- Al pulsarlo abre el panel **Buscar campo**.

### Botón de información

- Icono `i`.
- Abre el panel **Metadatos** del dataset actual.

### Botón de cuenta

- Muestra `Entrar` si no hay sesión y el nombre visible del usuario si la hay.
- Al pulsarlo abre el panel **Cuenta**.
- El panel alterna entre formularios de acceso/registro y el perfil con
  estadísticas (conversaciones, mensajes y favoritos).
- Incluye **Cerrar sesión**, que invalida la cookie y limpia favoritos y
  preferencias del estado local.

---

## 6. Visor central

### Imagen

- Se carga desde el endpoint PNG correspondiente al modo activo.
- Se precarga en memoria antes de sustituir la imagen visible para evitar
  parpadeos.
- Si falla, se muestra el aviso `No se pudo generar la imagen para esta vista.`

### Superposiciones (canvas)

Se dibujan en un canvas de 1024 × 1024 que se escala con la imagen:

1. **Retícula ecuatorial** (activable):
   - Líneas verticales y horizontales con paso “bonito” cercano a FOV/4.
   - Etiquetas de RA en el borde superior y de Dec en el borde izquierdo.
   - Barra de escala en la esquina inferior izquierda.
2. **Gaia DR3** (activable): círculos azules.
3. **AllWISE** (activable): rombos dorados.
4. **Candidatos** (activable):
   - Círculos azules con el identificador `SPX-…`.
   - El candidato seleccionado se dibuja en blanco y más grande.
   - Si es un objeto en movimiento, se dibuja una línea desde la posición en la
     época A hasta la posición en la época B.
5. **Último punto espectral:** cruz y círculo blancos en la coordenada medida.

### Comportamiento del puntero

- **Arrastrar:** panea la imagen.
- **Rueda:** zoom continuo (factor 1.16 por evento).
- **Doble clic:** zoom ×1.6.
- **Clic sin arrastrar:** convierte el píxel a RA/Dec, guarda la coordenada,
  abre el panel **Espectro** si no está abierto y carga el espectro.
- **Mover el puntero:** actualiza la lectura de RA/Dec en tiempo real.

---

## 7. Barra lateral de herramientas

Ocho botones, cada uno abre o cierra su panel. Si se pulsa el botón del panel
ya abierto, se cierra.

| Botón | Panel | Función |
| --- | --- | --- |
| Lupa | **Buscar campo** | Consultar el archivo, cargar datos reales y elegir demos. |
| Capas | **Capas y visualización** | Dataset, colormap, stretch, detector, coadd, favoritos, superposiciones y modos. |
| Rectángulo dividido | **Comparar épocas** | Seleccionar épocas, umbral y ejecutar detección. |
| Línea espectral | **Espectro** | Gráfico, estadísticas, líneas y exportación CSV. |
| Cuadrícula | **102 canales** | Rejilla 6 × 17, cobertura y selección de canal. |
| Información | **Metadatos** | Campo, calidad, contaminación, procedencia, frames y exportación JSON. |
| Agente | **Asistente SPHEREx** | Chat experto sobre el campo abierto; requiere sesión. |
| Flecha hacia arriba | **Cargar archivo** | Subir FITS o imagen propia. |

Al abrir un panel en escritorio, el visor se desplaza 410 px a la izquierda para
dejar espacio. En móvil, el panel se superpone.

---

## 8. Línea de tiempo y reproducción

### Ticks

- Un tick por época del dataset.
- El tick activo se resalta.
- En modo **Diferencia** o **Suma** se marcan además las épocas A y B.
- Al pulsar un tick se cambia de época:
  - En modo imagen o canal, cambia la época visible.
  - En modo diferencia o suma, cambia la época B y evita que coincida con A.

### Lectura

- Modo imagen/canal: `fecha · MJD`.
- Modo diferencia: `fecha A → fecha B · Δ días`.
- Modo suma: `fecha A + fecha B · coadd`.

### Reproducción

- Botón play/pausa.
- Avanza una época cada 1.1 segundos.
- En modo diferencia/suma avanza la época B, saltándose la época A.
- Requiere al menos dos épocas.
- El icono cambia entre play y pausa.

---

## 9. Zoom, paneo y coordenadas

### Controles de zoom

| Control | Acción |
| --- | --- |
| `+` | Acerca ×1.3. |
| `−` | Aleja ×1/1.3. |
| Reset | Vuelve a zoom 1 y centra la imagen. |

- Rango de zoom: 1× a 10×.
- El paneo se limita para no sacar la imagen del encuadre.
- El zoom también se controla con rueda, doble clic y teclado.

### Lectura de coordenadas

Muestra:

- `RA` del puntero o del último punto espectral.
- `Dec` del puntero o del último punto espectral.
- `FOV` del dataset y escala de píxel en arcosegundos.

Si no hay coordenada seleccionada, muestra el centro del campo.

### Enfoque de candidatos

Al pulsar un candidato:

- Se guarda su coordenada.
- Se aplica un zoom mínimo de 2.4×.
- Se centra el píxel del candidato.
- Se redibuja la superposición y se resalta.
- Si el panel de espectro está abierto, se carga el espectro de esa posición.

---

## 10. Paneles en detalle

### 10.1 Buscar campo

Controles:

| Control | Descripción |
| --- | --- |
| **Nombre del objeto** | Resolución por Sesame (CDS). |
| **RA (°)** | Ascensión recta en grados; se normaliza a 0–360. |
| **Dec (°)** | Declinación en grados; debe estar entre −90 y +90. |
| **FOV (°)** | Campo visual solicitado; 0.05–2.0; por defecto 0.35. |
| **Épocas** | 1, 2, 3 o 4 épocas máximas. |
| **Detectores** | 2, 4 o 6 detectores máximos por época. |
| **Consultar archivo** | Solo consulta: no descarga datos. |
| **Cargar datos reales** | Descarga y construye el dataset. |

#### Consulta al archivo

Al pulsar **Consultar archivo**:

1. Resuelve el nombre si hace falta.
2. Valida las coordenadas.
3. Llama a `POST /api/archive/query`.
4. Muestra un resumen:
   - Posición resuelta.
   - Número de observaciones.
   - Número de épocas.
   - Rango de MJD.
   - Detectores disponibles.
5. Lista las épocas disponibles con:
   - Fecha.
   - Detectores.
   - Número de detectores.
   - Casilla de selección.
6. Si hay más de 14 épocas, muestra las 14 con más detectores más las
   sugeridas por el backend.
7. Las épocas sugeridas vienen marcadas por defecto.
8. Un contador indica cuántas épocas están seleccionadas.

Si no se marca ninguna época, el backend elige automáticamente dos barridos
separados en el tiempo.

#### Carga de datos reales

Al pulsar **Cargar datos reales**:

1. Toma la posición, FOV, número de épocas, detectores y épocas marcadas.
2. Llama a `POST /api/archive/load`.
3. Muestra una capa de carga con mensajes rotativos:
   - Consultando el archivo IRSA.
   - Descargando recortes level-2.
   - Reproyectando a una cuadrícula común.
   - Coadicionando detectores por varianza inversa.
4. Al terminar, actualiza la lista de datasets y abre el nuevo.
5. Cierra el panel y muestra un aviso con frames y épocas.

#### Campos de demostración

Lista los cuatro escenarios sintéticos:

| ID | Título | Tipo | Épocas |
| --- | --- | --- | --- |
| `demo_neo_asteroid` | Asteroide NEO 2025 SPX-42 | Asteroide cercano a la Tierra | 3 |
| `demo_supernova_transient` | Supernova 2025spx | Supernova tipo Ia | 3 |
| `demo_brown_dwarf` | Enana marrón WISEA J1854 | Enana marrón tipo Y | 3 |
| `demo_active_comet` | Cometa P/2025 SPHEREx-1 | Cometa activo | 3 |

Cada tarjeta muestra etiqueta, nombre, descripción, épocas, frames y
coordenadas. Al pulsarla se abre ese dataset.

---

### 10.2 Capas y visualización

#### Conjunto de datos

Selector agrupado en:

- **Datos reales · IRSA**
- **Demostración**
- **Cargas propias**

Cada opción muestra el nombre y el número de épocas. Al cambiar de dataset se
reinician época, zoom, paneo, candidatos, catálogos y espectro.

#### Mapa de color

| Valor | Nombre |
| --- | --- |
| `mono` | Monocromo |
| `ir` | Infrarrojo |
| `inferno` | Inferno |
| `ice` | Cielo profundo |

#### Estiramiento

| Valor | Método |
| --- | --- |
| `asinh` | Arcoseno hiperbólico (por defecto, estándar en infrarrojo). |
| `log` | Logarítmico. |
| `sqrt` | Raíz cuadrada. |
| `linear` | Lineal. |
| `zscale` | ZScale basado en mediana y MAD. |

El render usa percentiles 0.5 y 99.6 por defecto y `gamma = 1.0`.

#### Detector

- **Todos (coadd):** combina todos los detectores de la época.
- **D1…D6:** muestra solo ese detector.
- Al cambiar de detector se vuelve al modo imagen.

#### Coadd de detectores

Tabla por detector con:

- Detector.
- Ruido (desviación estándar).
- Mediana.
- Porcentaje de píxeles válidos.

Debajo:

- Número de frames.
- Método: varianza inversa.
- Ganancia teórica de SNR: `sqrt(N frames)`.

#### Campos favoritos

- Muestra el dataset abierto y un botón **Guardar** / **Guardado**.
- Con sesión, añade o quita el dataset de favoritos en SQLite.
- La lista de favoritos abre cada campo con un clic.
- Sin sesión, invita a iniciar sesión; no se guarda nada de forma anónima.
- Si un favorito apunta a un dataset que ya no existe, se marca como ausente.

#### Superposiciones

| Interruptor | Efecto |
| --- | --- |
| **Retícula ecuatorial** | Rejilla, etiquetas RA/Dec y barra de escala. |
| **Marcadores de candidatos** | Círculos, identificadores y vectores de movimiento. |
| **Gaia DR3** | Consulta TAP y dibuja círculos azules; muestra el número de fuentes. |
| **AllWISE** | Consulta TAP y dibuja rombos dorados; muestra el número de fuentes. |

Los catálogos se consultan una vez por dataset y se reutilizan al activar y
desactivar.

#### Vista

- **Imagen**
- **Suma**
- **Diferencia**

En **Suma** y **Diferencia** aparecen los selectores de época A y B. En
**Diferencia** aparece además el deslizador de escala de 2σ a 12σ (por defecto
6σ).

---

### 10.3 Comparar épocas

Controles:

| Control | Descripción |
| --- | --- |
| **Época A** | Primera época de la comparación. |
| **Época B** | Segunda época; no puede ser igual a A. |
| **Umbral (σ)** | 2–12σ; por defecto 6σ en la interfaz. |
| **Analizar movimiento** | Ejecuta la detección. |

Al ejecutar:

1. Valida que haya al menos dos épocas y que A ≠ B.
2. Llama a `/detect` con `epoch_a`, `epoch_b` y `threshold`.
3. Cambia el visor al modo **Diferencia**.
4. Muestra un resumen:
   - Número de candidatos.
   - Ruido global estimado.
   - Línea base en días.
   - Nota sobre el método espectral usado.
5. Lista los candidatos ordenados por SNR.

Cada tarjeta de candidato muestra:

- Identificador (`SPX-MOV-001`, `SPX-TRN-001`, `SPX-VAR-001`).
- SNR en sigma.
- Tipo: objeto en movimiento, transitorio/supernova o estrella variable.
- Nota descriptiva.
- Si es móvil: desplazamiento en arcosegundos, tasa en ″/día y ángulo.

Al pulsar una tarjeta se enfoca el candidato en la imagen.

Si no hay candidatos, se sugiere bajar el umbral. Si aún no se ha ejecutado el
análisis, se muestra una explicación del método.

---

### 10.4 Espectro

#### Origen del espectro

- Al abrir el panel sin coordenada previa, mide el centro del campo.
- Al hacer clic en la imagen, mide ese punto.
- Al pulsar un candidato con el panel abierto, mide la posición del candidato.

#### Cabecera

- Coordenada: `RA …° · Dec …°`.
- Tipo: `Demo · 102 canales sintéticos` o `Archivo real · cobertura nativa`.
- Época medida.

#### Gráfico

- Eje X: longitud de onda en µm, de 0.7 a 5.1.
- Eje Y: flujo en MJy/sr (o unidades arbitrarias en demo).
- Línea azul con puntos por canal.
- La línea se corta cuando falta un canal, para no unir datos inexistentes.
- Líneas verticales discontinuas en las firmas espectrales.
- Tooltip al pasar el ratón: longitud de onda, flujo y detector.
- Si no hay cobertura: `Sin cobertura espectral en este punto`.

#### Estadísticas

- Cobertura: canales con datos / 102.
- Número de detectores implicados.
- Flujo mediano.
- Número de muestras nativas.

#### Líneas espectrales

Se marcan como referencia, no como detección automática:

| Nombre | Fórmula | Longitud de onda |
| --- | --- | --- |
| Hielo de agua | H₂O | 3.05 µm |
| Hidrocarburos aromáticos | PAH | 3.30 µm |
| Hielo de CO₂ | CO₂ | 4.27 µm |
| Hielo de CO | CO | 4.67 µm |

#### Exportación CSV

El botón de descarga genera un archivo
`<dataset>_spectrum.csv` con columnas:

```csv
channel,wavelength_um,flux,detector
```

Si no hay espectro, muestra `No hay espectro para exportar.`

---

### 10.5 102 canales

#### Rejilla

- Una fila por detector (D1–D6).
- 17 celdas por fila.
- Total: 102 canales.
- Cada celda muestra el número de canal.
- Las celdas cubiertas se pueden pulsar; las no cubiertas están deshabilitadas.
- El canal activo se resalta.

#### Cobertura

- Cabecera: `N / 102 canales · fecha de la época`.
- Un canal se considera cubierto si al menos el **0.5 % de los píxeles** de la
  cuadrícula tiene datos, con un mínimo absoluto de 16 píxeles.
- El tooltip de una celda cubierta muestra:
  - Canal.
  - Longitud de onda.
  - Detector.
  - Número de píxeles con datos.
- El tooltip de una celda no cubierta indica `sin cobertura suficiente`.

#### Imagen de canal

Al pulsar una celda:

1. Cambia al modo `band`.
2. Carga `/band/<índice>.png` para la época actual.
3. Redibuja la línea de tiempo y la rejilla.
4. Actualiza la URL.

El botón **Volver a imagen** restaura el modo imagen normal.

#### Nota inferior

- En demo: los 102 canales están disponibles en cada época.
- En datos reales: los recortes level-2 cubren una porción estrecha del LVF y
  solo se marcan los canales con cobertura suficiente.

---

### 10.6 Metadatos

#### Tarjeta de identificación

- Insignia: **Datos reales · IRSA**, **Demostración** o **Carga propia**.
- Nombre del dataset.
- Descripción.

#### Tarjeta Campo

| Campo | Contenido |
| --- | --- |
| RA | Centro en grados. |
| Dec | Centro en grados. |
| FOV | Campo visual en grados. |
| Píxel | Escala en arcosegundos por píxel. |
| Cuadrícula | `npix × npix`. |
| Épocas | Número de épocas. |
| Frames | Número de frames. |
| Cobertura | Canales cubiertos / 102. |

#### Tarjeta Calidad de la época

- **Índice de contaminación** de 0 a 100.
- Insignia **BAJA**, **MODERADA** o **ALTA**.
- Barra proporcional al índice.
- Fondo mediano.
- Ruido de fondo (MAD escalado).
- Porcentaje de flags de calidad (bits 0–3).
- Porcentaje de flags informativos (bits fuera de 0–3, por ejemplo el bit 21).
- Porcentaje de residuales altos (proxy de rayos cósmicos).
- Porcentaje de saturación (proxy).
- Nivel de luz zodiacal.
- Ganancia SNR por coadd.
- Cobertura válida de la imagen.

#### Tarjeta Procedencia

- Archivo.
- Servicio.
- Consulta.
- Rango de MJD.
- Tamaño del recorte.
- Número de observaciones.

#### Tarjeta Frames

Tabla con:

- Época.
- Detector.
- Fecha.
- Rango de longitudes de onda en µm.
- Porcentaje válido.
- Mediana de luz zodiacal.

#### Exportación JSON

El botón **Exportar informe JSON** descarga
`<dataset>_spherex_report.json` con:

- Fecha de generación y versión.
- Resumen del dataset.
- Calidad de la época.
- Cobertura de bandas.
- Frames.
- Procedencia.
- Detección entre la primera y la última época (si hay al menos dos).

---

### 10.7 Cargar archivo

#### Zona de carga

- Arrastrar y soltar, o pulsar **Elegir archivo**.
- Formatos aceptados: `.fits`, `.fit`, `.fts`, `.png`, `.jpg`, `.jpeg`.
- Tamaño máximo: 96 MB.
- Muestra nombre y tamaño del archivo.
- Si no se ha escrito una etiqueta, se propone el nombre del archivo sin
  extensión.

#### Campos

| Campo | Descripción |
| --- | --- |
| **Etiqueta** | Nombre visible del dataset. |
| **RA (°)** | Centro opcional; si falta, se usa el WCS o el encabezado. |
| **Dec (°)** | Centro opcional. |
| **Fecha** | Fecha de observación opcional. |
| **Procesar archivo** | Sube y construye el dataset. |

#### Procesamiento

1. Guarda el archivo con un nombre derivado de un hash.
2. Intenta leerlo como producto SPHEREx level-2.
3. Si falla, lo trata como FITS 2D genérico o imagen.
4. Usa el WCS del encabezado si existe; si no, crea un WCS TAN genérico con
   6.15″/píxel.
5. Reproyecta a una cuadrícula norte-arriba de 192 × 192.
6. Crea un dataset de una sola época con detector `UPLOAD`.
7. Lo abre en el visor.

#### Limitaciones de una carga

- Una sola época: no hay comparación, suma, diferencia ni detección.
- Sin información espectral: la rejilla de bandas y el espectro aparecen sin
  cobertura.
- El producto es para visualización, no un producto calibrado.

---

### 10.8 Cuenta

El panel **Cuenta** se abre desde el botón superior derecho. Tiene dos estados.

#### Sin sesión

Dos pestañas: **Entrar** y **Crear cuenta**.

| Campo | Reglas |
| --- | --- |
| **Nombre visible** | Opcional, máximo 60 caracteres. |
| **Usuario** | 3–32 caracteres: letras, números, `.`, `-` o `_`. |
| **Correo** | Formato de correo válido y único. |
| **Contraseña** | 8–128 caracteres. |

Al registrarse o entrar se guarda una cookie de sesión y el panel muestra el
perfil. Un aviso confirma la operación; los errores se muestran como toast.

#### Con sesión

- Tarjeta con inicial, nombre visible, usuario, correo y fecha de alta.
- Estadísticas: conversaciones, mensajes y favoritos.
- Botón **Cerrar sesión**.
- La sesión dura 30 días y usa `SameSite=Lax`; las acciones que escriben envían
  el token CSRF.
- Tras 8 intentos fallidos en 10 minutos se bloquea temporalmente el acceso.

### 10.9 Asistente SPHEREx

El panel **Asistente SPHEREx** se abre desde el rail. Sin sesión muestra una
puerta de entrada con el botón **Entrar o crear cuenta**; el formulario de envío
queda deshabilitado.

Con sesión muestra:

| Elemento | Función |
| --- | --- |
| **Modo** | `Modo local · sin clave de API` o el modelo y proveedor configurados. |
| **Selector de conversaciones** | Recupera conversaciones guardadas en SQLite. |
| **Nueva conversación** | Empieza un hilo vacío. |
| **Eliminar** | Borra la conversación actual tras confirmación. |
| **Mensajes** | Burbujas de usuario y asistente con markdown ligero. |
| **Estado de herramienta** | Indica qué herramienta se está consultando (`tool`). |
| **Temas sugeridos** | Seis preguntas de ejemplo sobre el campo abierto. |
| **Campo de texto** | Hasta 4000 caracteres; `Enter` envía y `Shift+Enter` hace salto de línea. |

El asistente recibe el dataset abierto y el historial reciente. El motor local
puntúa la intención de cada mensaje y compone la respuesta con hasta tres
secciones que ejecutan análisis reales: resumen del campo, cobertura de canales,
calidad, comparación de épocas, espectro, cruce Gaia/WISE, búsqueda en el índice
local, misión, procedencia y criterios de validación. También extrae parámetros
de la pregunta (RA/Dec, radio, banda, épocas, umbral, ventana MJD o fecha) y
arrastra coordenadas, épocas y candidatos de turnos anteriores. Si la pregunta
menciona configuración, clave, modelo, OpenAI, Gemini o instalación, añade una
nota con las variables de `.env`. Las respuestas se guardan automáticamente y la
primera pregunta crea la conversación con un título derivado.

### 10.10 Espacio de trabajo IA

El **Espacio de trabajo IA** es una vista a pantalla completa dentro del área
del visor. Se abre desde el botón de expandir del panel del asistente o desde el
botón dedicado del rail, y se cierra con `Esc` o con su botón de cerrar. La URL
`?ai=1` restaura la vista al recargar.

| Zona | Contenido |
| --- | --- |
| **Cabecera** | Título, modo del asistente, **Nueva conversación** y cerrar. |
| **Puerta de sesión** | Si no hay usuario: explicación y botón **Entrar o crear cuenta**; al iniciar sesión se reabre el espacio automáticamente. |
| **Columna de conversaciones** | Historial con título y fecha, selección y borrado por fila. |
| **Conversación** | Mensajes con markdown ligero, estado de herramienta, sugerencias y compositor con `Enter` / `Shift+Enter`. |
| **Contexto** | Campo abierto con RA/Dec, FOV, cuadrícula, épocas, frames, cobertura y detectores. |
| **Acciones** | **Analizar cambios** (envía un prompt con las fechas), **Comparar épocas**, **Ver diferencia** y **Abrir espectro**. |
| **Datos y modelo** | Registros del índice local, observaciones, bandas, rango MJD, documentos y tamaño del texto indexado; versión, exactitud y muestras del clasificador. Incluye **Reentrenar modelo** y **Explicar datos**. |
| **Estudio guiado** | Atajos agrupados en Desafío, Astronomía, Imágenes y Datos y modelo. |
| **Herramientas** | Lista de herramientas del agente o aviso de modo local. |

El panel lateral y el espacio de trabajo comparten el mismo estado
(`state.assistant`): mensajes, conversación activa, streaming y control de
cancelación. Mientras se transmite una respuesta, el campo de texto sigue
habilitado para escribir, el botón de envío se bloquea y aparece **Detener**.
Abrir cualquier panel desde la barra superior cierra el espacio de trabajo.

En escritorio se muestran tres columnas; por debajo de 1180 px se oculta la
columna de herramientas y por debajo de 860 px también el historial. Por debajo
de 560 px se ocultan las etiquetas largas de la cabecera.

El experto local no usa plantillas fijas: `_select_intents()` puntúa la
pregunta y elige hasta tres secciones; cada sección ejecuta su propia consulta
(cobertura, calidad, detección, espectro, catálogo, cruce). Incluye secciones
específicas para el desafío de NASA Space Apps, el contexto astronómico de
SPHEREx, la procedencia de los datos y los criterios para distinguir un
artefacto de un transitorio. Cuando hay clasificador entrenado, cita sus
métricas y advierte que es una ayuda al cribado. La tarjeta **Datos y modelo**
resume el índice local y permite reentrenar el clasificador sin salir de la
vista (requiere sesión y CSRF).

---

## 11. Modos de visualización

| Modo | Endpoint | Qué muestra | Requisitos |
| --- | --- | --- | --- |
| **Imagen** | `/image.png` | Época actual, coadd completo o un detector concreto. | Una época. |
| **Suma** | `/sum.png` | Coadd por varianza inversa de dos épocas. | Dos épocas distintas. |
| **Diferencia** | `/diff.png` | Residuo B − A en canales comunes, en significancia bipolar. | Dos épocas distintas. |
| **Canal** | `/band/<k>.png` | Imagen de un canal espectral concreto. | Canal cubierto. |

### Imagen

- Si se elige un detector, se usa su frame.
- Si se elige **Todos**, se usa el coadd por varianza inversa de la época.
- Se aplica estiramiento, mapa de color y volteo vertical para que el norte
  quede arriba.

### Suma

- Para datos reales, usa `matched_pair()` y combina las dos épocas con pesos
  `1/varianza`.
- Para demo o si no hay canales comunes, combina los coadds completos.
- Aumenta la exposición efectiva y reduce el ruido.
- Devuelve cabeceras `X-Epoch-A`, `X-Epoch-B`, `X-Spectral-Method` y
  `X-Matched-Bands`.

### Diferencia

- Para datos reales, usa solo canales observados en ambas épocas.
- La imagen es `B − A`.
- Se convierte a significancia usando la varianza combinada.
- Colores:
  - **Naranja:** flujo positivo, aparición o aumento de brillo.
  - **Cian:** flujo negativo, desaparición o disminución de brillo.
- El deslizador de escala controla el rango de 2σ a 12σ.
- Devuelve cabeceras con épocas, línea base, método, canales y píxeles comunes.

### Canal

- Selecciona los píxeles cuyo mapa `WCS-WAVE` cae dentro del semiancho del canal.
- Combina los planos de varios frames con mediana ignorando NaN.
- En demo, toma directamente la rebanada del cubo sintético.
- Si no hay cobertura, devuelve error 404 y el frontend avisa.

---

## 12. Interacciones y atajos de teclado

### Ratón

| Acción | Efecto |
| --- | --- |
| Clic en la imagen | Extrae espectro y abre el panel. |
| Arrastrar | Panea. |
| Rueda | Zoom. |
| Doble clic | Zoom ×1.6. |
| Clic en tick | Cambia de época. |
| Clic en candidato | Enfoca y resalta. |
| Clic en celda de banda | Muestra el canal. |
| Hover en el gráfico | Tooltip del canal más cercano. |

### Teclado

Los atajos se ignoran mientras se escribe en un `input`, `select` o
`textarea`.

| Tecla | Acción |
| --- | --- |
| `←` | Época anterior. |
| `→` | Época siguiente. |
| `Espacio` | Reproducir / pausar. |
| `+` o `=` | Acercar. |
| `-` | Alejar. |
| `0` | Restablecer zoom y paneo. |
| `Esc` | Cerrar el panel. |

### Avisos

- **Toast de éxito:** carga completada, nombre resuelto, catálogo cargado,
  candidatos encontrados.
- **Toast de error:** coordenadas inválidas, archivo no procesable, época
  repetida, falta de conexión, etc.
- **Capa de carga:** bloquea la interacción y rota mensajes cada 4.2 s.

---

## 13. Estado en la URL

La URL se actualiza con `history.replaceState`, sin recargar la página.

### Parámetros que escribe la aplicación

| Parámetro | Ejemplo | Significado |
| --- | --- | --- |
| `dataset` | `archive_6811f5771b` | Dataset activo. |
| `epoch` | `1` | Índice de época visible. |
| `mode` | `diff` | `image`, `sum`, `diff` o `band`. Se omite en imagen. |
| `band` | `42` | Índice de canal, solo en modo `band`. Es cero-based: 0–101. |
| `panel` | `spectrum` | Panel abierto. |
| `ra` | `276.26390` | Última coordenada medida. |
| `dec` | `64.82400` | Última coordenada medida. |

### Parámetros que también lee al restaurar

| Parámetro | Uso |
| --- | --- |
| `fov` | Rellena el campo FOV del buscador. |
| `name` | Rellena el nombre del buscador. |

### Comportamiento

- Si hay `dataset`, se abre ese dataset.
- Si el dataset no existe, se usa el dataset por defecto.
- Si hay `ra` y `dec`, se guarda la coordenada y se dibuja la cruz.
- Si no hay `dataset` pero hay `ra`, `dec` o `name`, se abre el buscador y se
  consulta el archivo automáticamente.
- Si hay `panel`, se abre ese panel.
- Si `ai=1`, se abre el **Espacio de trabajo IA** (con puerta de sesión si no
  hay usuario).
- Si `mode=band` y hay `band`, se carga la imagen del canal.
- En modo **Suma** o **Diferencia**, las épocas A y B no se guardan en la URL:
  al restaurar se usan la primera y la última época del dataset. `epoch` sigue
  refiriéndose a la época visible en los modos de imagen y canal.

### Ejemplos

```
http://127.0.0.1:5000/?dataset=archive_6811f5771b&epoch=1
http://127.0.0.1:5000/?dataset=archive_6811f5771b&mode=diff&panel=compare
http://127.0.0.1:5000/?dataset=demo_active_comet&mode=band&band=54&panel=bands
http://127.0.0.1:5000/?ra=276.2639&dec=64.8240&fov=0.3
http://127.0.0.1:5000/?name=M31&panel=search
```

---

## 14. Datos y procedencia

### Fuente principal

- **Archivo:** NASA/IPAC Infrared Science Archive (IRSA).
- **Servicio:** SPHEREx level-2 cutouts, vía TAP + IBE.
- **TAP:** `https://irsa.ipac.caltech.edu/TAP/sync`.
- **Cutouts:** `https://irsa.ipac.caltech.edu/<uri>?center=RA,Dec&size=DEG`.
- **Resolución de nombres:** CDS Sesame,
  `https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV?<target>`.
- **Catálogos:** `gaia_dr3_source` y `allwise_p3as_psr` por TAP.

### Extensiones level-2 leídas

| Extensión | Contenido |
| --- | --- |
| `IMAGE` | Brillo superficial calibrado en MJy/sr. |
| `FLAGS` | Bitmask de calidad por píxel. |
| `VARIANCE` | Mapa de varianza. |
| `ZODI` | Modelo de luz zodiacal. |
| `WCS-WAVE` | Mapa de longitud de onda y ancho de banda por píxel. |

También se leen metadatos del encabezado: `DETECTOR`, `OBSID`, `DATE-OBS`,
`MJD-OBS`, `XPOSURE`/`EXPTIME`, `BUNIT` y `PSF_FWHM`.

### Consulta TAP

La consulta usa:

- `spherex.artifact` unido a `spherex.plane`.
- `CONTAINS(POINT('ICRS', ra, dec), p.poly)` para saber si la posición cae en el
  polígono del plano.
- Filtros opcionales de `time_bounds_lower`.
- Orden por tiempo.
- Límite de observaciones.

Cada observación aporta URI, MJD, detector, rango de energía y un identificador
derivado del nombre del archivo.

### Agrupación en épocas

- Se agrupa por día entero de MJD.
- Para cada detector y día se conserva la primera observación.
- Cada época resultante tiene fecha, MJD medio, detectores y observaciones.

### Selección automática de épocas y detectores

Si el usuario no marca épocas concretas:

- Si se pide una sola época, se elige la que tiene más detectores y, en empate,
  la más reciente.
- Si hay menos épocas que el máximo solicitado, se usan todas en orden temporal.
- Si hay más épocas que el máximo, se descartan las que tienen menos de tres
  detectores (salvo que no haya otras). Con dos épocas se eligen la primera y la
  última de la lista útil; con más de dos se reparten uniformemente a lo largo
  de la línea temporal.

Los detectores se eligen con la preferencia `D1, D2, D4, D5, D3, D6`, hasta el
máximo solicitado. Si el usuario indica detectores concretos, se respeta ese
orden filtrando por los disponibles.

El tamaño del recorte solicitado a IRSA es `max(0.5, FOV × 1.7)` grados. Se
descartan los frames reproyectados con menos del 0.2 % de píxeles válidos.

### Tipos de dataset

| Tipo | ID | Persistencia |
| --- | --- | --- |
| Archivo real | `archive_<sha1[:10]>` | `meta.json` + `frames.npz`. |
| Demostración | `demo_<escenario>` | En memoria; se regenera al arrancar. |
| Carga propia | `upload_<uuid[:10]>` | `meta.json` + `frames.npz`. |

El identificador de archivo se deriva de RA, Dec, FOV y días de las épocas
seleccionadas, de modo que la misma consulta produce el mismo ID.

### Dataset de ejemplo incluido

El proyecto puede incluir campos reales precargados. Un ejemplo es:

| Campo | Valor |
| --- | --- |
| ID | `archive_6811f5771b` |
| Nombre | Campo profundo SPHEREx |
| RA | 276.2639° |
| Dec | 64.8240° |
| FOV | 0.3° |
| Cuadrícula | 160 × 160 |
| Épocas | 2 (MJD 61200 y 61217) |
| Detectores | 6 |
| Frames | 12 |
| Línea base | 17.06 días |
| Canales comunes | 14 |

La aplicación abre el dataset de archivo más reciente; si hay varios, el orden
es por fecha de creación descendente.

### Índice local del catálogo

`data/spherex_spectral_images.csv` es un export real del catálogo IRSA de
imágenes espectrales SPHEREx. `spherex.catalog` lo carga en
`instance/catalog.db` (o `SPHEREX_CATALOG_DB`) al arrancar:

| Dato | Valor |
| --- | --- |
| Registros | 355 |
| Observaciones | 182 |
| Detectores/bandas | 6 (`SPHEREx-D1` … `SPHEREx-D6`) |
| Rango temporal | MJD 60889–61277 (~388 días) |
| Cobertura | RA 249.26–253.85°, Dec −45.41–−41.79° |
| Exposición | 113.58 s |
| Píxel / resolución | 6.15″ / 3.08″ |

La tabla `observations` guarda posición, banda, detector, rango espectral, MJD,
URL de acceso, calibración y metadatos. `catalog.search()` filtra por caja de
posición, banda y ventana temporal y calcula la separación angular con
haversine. `field_summary()` devuelve observaciones, bandas, línea temporal y la
observación más cercana. La misma base incluye `crossmatch_cache` para guardar
30 días de resultados Gaia/WISE.

### Base de conocimiento y clasificador

- **BM25 local.** Las notas curadas del proyecto y los archivos
  `DOCUMENTACION.md` y `README.md` se trocean por encabezados y se indexan en
  `knowledge_docs` (157 pasajes, ~87 KB). `search_knowledge()` puntúa con Okapi
  BM25 sin servicios externos.
- **Clasificador.** `spherex.model` extrae 15 características del candidato y su
  contexto (SNR, área, cociente de flujo, dipolo, tasa de movimiento, línea
  base, canales, umbral, contrapartidas Gaia/WISE y escala de píxel) y ajusta
  una regresión logística multiclase con descenso de gradiente y pesos de clase
  balanceados. Clases: `artifact`, `moving`, `transient`, `variable`.
- **Entrenamiento.** Posiciones conocidas de los campos demo, etiquetas débiles
  de Gaia/WISE en campos reales y aumento de datos. Los pesos se guardan en
  `instance/models/candidate_model.npz` y las métricas en
  `candidate_model.json`.
- **Uso.** `/api/datasets/<id>/detect` añade `ml` a cada candidato y, con
  `?catalog=1`, también `catalog` con la contrapartida más cercana. La interfaz
  muestra una etiqueta de clase y su confianza.

```powershell
.\venv\Scripts\python.exe -m spherex.model --train
.\venv\Scripts\python.exe -m spherex.model --status
```

---

## 15. Procesamiento científico

### 15.1 Reproyección

Cada recorte se lleva a una cuadrícula común:

- Proyección TAN.
- Norte arriba.
- Centro en RA/Dec del campo.
- `cdelt = [-FOV/npix, FOV/npix]`.
- Píxeles de 64 a 512; por defecto 192.

Se usa `reproject_interp`:

- Bilineal para imagen, varianza, longitud de onda y ancho de banda.
- Vecino más cercano para flags.
- Los píxeles con huella menor o igual a 0.01 se marcan inválidos (NaN en
  imagen, varianza y onda; −1 en flags).

Los productos level-2 ya están corregidos de distorsión, así que se eliminan
los coeficientes SIP antes de reproyectar.

### 15.2 Registro subpíxel

Después de reproyectar quedan pequeños residuos astrométricos. Para corregirlos:

1. Se normaliza cada imagen con percentiles 5–99.
2. Se resta la media.
3. Se calcula la correlación cruzada por FFT.
4. Se busca el pico.
5. Se refina con interpolación parabólica en X e Y.
6. Se limita el desplazamiento a ±12 px.
7. Se aplica un desplazamiento bilineal a imagen, varianza y onda; vecino más
   cercano a flags.
8. Se guarda el desplazamiento en `meta.shift_px`.

Todos los frames se alinean contra el primero.

### 15.3 Coadd por varianza inversa

Para combinar varios detectores:

```
peso_i = 1 / max(varianza_i, 1e-8)
imagen = Σ(imagen_i × peso_i) / Σ(peso_i)
varianza = 1 / Σ(peso_i)
```

- La longitud de onda se promedia solo donde existe.
- Los flags se combinan con OR.
- La ganancia teórica de SNR es `sqrt(N frames)`.
- Es la base del modo **Todos (coadd)** y del panel de coadd.

### 15.4 Canales espectrales

- 6 detectores LVF.
- 17 canales por detector.
- 102 canales en total.
- Rango: 0.7455–5.0 µm.
- Dentro de cada detector, las longitudes de onda se distribuyen
  geométricamente entre su mínimo y su máximo.

| Detector | Rango aproximado |
| --- | --- |
| D1 | 0.7455–1.1167 µm |
| D2 | 1.1033–1.6499 µm |
| D3 | 1.6400–2.4200 µm |
| D4 | 2.4027–3.8473 µm |
| D5 | 3.8113–4.4300 µm |
| D6 | 4.4200–5.0000 µm |

El semiancho de un canal es la mitad de la distancia al siguiente canal, con un
mínimo de 1e-4 µm; el último canal usa 0.02 µm.

### 15.5 Cobertura de bandas

Para cada canal:

1. Se cuenta cuántos píxeles de todos los frames de la época tienen una longitud
   de onda dentro del semiancho.
2. Se exige al menos `max(16, 0.5 % × npix²)` píxeles.
3. Si se cumple, el canal está cubierto.

Esto evita marcar como disponible un canal tocado solo por unos pocos píxeles
del borde.

### 15.6 Imagen de un canal

- Se seleccionan los píxeles cuyo `WCS-WAVE` cae dentro del semiancho.
- Se crea un plano por frame con NaN fuera de la máscara.
- Se combinan los planos con mediana ignorando NaN.
- En demo se usa directamente la rebanada del cubo.

### 15.7 Pares de épocas con canales comunes

SPHEREx observa el mismo cielo a longitudes de onda distintas en barridos
distintos. Restar coadds completos mezcla canales y deja residuos en estrellas
brillantes. Por eso la diferencia real usa `matched_pair()`:

1. Para cada uno de los 102 canales, extrae la imagen y la varianza en ambas
   épocas.
2. Conserva solo píxeles finitos con varianza positiva en las dos.
3. Exige al menos 64 píxeles comunes por canal.
4. Acumula con pesos `1/varianza`.
5. Devuelve imágenes A y B, diferencia `B − A`, varianza suma y lista de
   canales usados.

Si no hay suficientes canales o píxeles comunes, la detección cae al coadd
completo.

### 15.8 Suma de épocas

- Con canales comunes: media ponderada por `1/varianza` de las imágenes A y B.
- Sin canales comunes: media de los coadds completos.
- El resultado reduce el ruido y aumenta la exposición efectiva.

### 15.9 Detección de candidatos

#### Paso 1: diferencia y ruido

- Se obtiene la diferencia `B − A` en los píxeles válidos.
- Si hay menos de 16 píxeles en común, se devuelve
  `Solapamiento insuficiente entre épocas`.
- Se calcula la mediana y la MAD.
- `sigma = 1.4826 × MAD`; si es cero, se usa la desviación estándar.

#### Paso 2: significancia por píxel

```
sigma_pixel = sqrt(max(varianza, sigma²))
significancia = (diferencia − mediana) / sigma_pixel
```

Así se evita marcar estrellas brillantes con errores formales grandes.

#### Paso 3: componentes conexas

- Píxeles positivos por encima del umbral.
- Píxeles negativos por debajo del umbral negativo.
- Etiquetado de componentes conexas.
- Se descartan componentes con área menor que `min_area` (3 px por defecto).
- Se calcula el centro de masa y se muestrea el píxel de mayor significancia.

#### Paso 4: dipolos de movimiento

Un objeto que se mueve deja una componente negativa en la época A y una positiva
en la época B. Se emparejan si:

- La distancia está entre 4 px y
  `min(160, max(6, días × 5 / escala_px))`.
- El cociente de flujos es menor o igual a 4.
- El cociente de áreas es menor o igual a 5.
- La puntuación `distancia × (1 + 0.45 × ln(cociente_flujo))` es mínima.

El radio máximo equivale a un límite generoso de 5″/día de movimiento propio.

Para cada dipolo se calcula:

- Posición en B y posición en A.
- Desplazamiento en píxeles.
- Desplazamiento en arcosegundos.
- Ángulo.
- Tasa en ″/día.
- Línea base en días.

#### Paso 5: transitorios y variables

Las componentes positivas sin pareja se clasifican:

- **Transitorio / Supernova** si el flujo en A es menor que `3σ` o el aumento
  supera el 200 %.
- **Estrella variable** en caso contrario.

La confianza es **Alta** si SNR > 6 y **Media** si no.

#### Paso 6: resultados

- Los candidatos se ordenan por SNR descendente.
- Se devuelven como máximo 60.
- Identificadores:
  - `SPX-MOV-NNN` para movimiento.
  - `SPX-TRN-NNN` para transitorio.
  - `SPX-VAR-NNN` para variable.
- Cada candidato incluye flujo A, flujo B, delta, área, SNR, posición, tipo,
  confianza y nota.

#### Diferencia entre demo y datos reales

- En demo se usa el coadd completo, porque el cubo sintético tiene los 102
  canales en todas las épocas.
- En datos reales se intenta primero `matched_pair()`.

### 15.10 Espectro instantáneo

Para datos reales:

1. Se convierte RA/Dec a píxel.
2. Se abre una apertura de radio 2.2 px.
3. En cada frame de la época se toma la mediana de la imagen y de la longitud de
   onda dentro de la apertura.
4. La incertidumbre se estima con la raíz de la mediana de la varianza.
5. Se obtiene una muestra nativa por frame.
6. Cada muestra se asigna al canal de referencia más cercano.
7. Las muestras de un mismo canal se combinan con pesos `1/incertidumbre²`.
8. Se devuelven hasta 400 muestras nativas y los canales agregados.

Para demo:

- Se toma el píxel del cubo en la época.
- Se devuelven los 102 canales sintéticos.

El espectro es una medida de visualización: no se hace ajuste de modelo ni
extracción óptima de fuente.

### 15.11 Calidad y contaminación

Se calcula sobre el coadd de la época:

| Métrica | Cálculo |
| --- | --- |
| Fondo mediano | Mediana de los píxeles válidos. |
| Ruido de fondo | `1.4826 × MAD`. |
| Flags de calidad | Porcentaje de píxeles con bits 0–3 activos. |
| Flags informativos | Porcentaje con bits fuera de 0–3. |
| Residuales altos | Porcentaje por encima de mediana + 8σ. |
| Saturación | Porcentaje en el percentil 99.95 o superior. |
| Luz zodiacal | Mediana de `ZODI` del primer frame que la tenga. |
| Ganancia SNR | `sqrt(n_frames)`. |
| Cobertura válida | Porcentaje de píxeles finitos. |

Índice de contaminación:

```
contaminacion = min(100, flags×60 + residuales×220 + saturacion×30)
si hay luz zodiacal y fondo > 0:
    contaminacion = min(100, contaminacion + 10 × zodi / fondo)
```

Interpretación:

| Índice | Etiqueta |
| --- | --- |
| > 35 | ALTA |
| > 12 | MODERADA |
| ≤ 12 | BAJA |

El bit 21 se trata como informativo y no cuenta como contaminación.

### 15.12 Renderizado

- Estiramiento por percentiles (`low`, `high`) y `gamma`.
- Métodos: asinh, log, sqrt, linear, zscale.
- Mapas de color: monocromo, infrarrojo, inferno, cielo profundo.
- La imagen FITS se voltea verticalmente para que el norte quede arriba.
- La diferencia se convierte a significancia y se pinta con escala bipolar.
- El suelo de color es 3σ; por encima se aplica asinh para comprimir los picos.
- Los PNG se cachean en memoria (máximo 96) y se sirven con `max-age=120`.

### 15.13 Catálogos

- **Gaia DR3:** `ra`, `dec`, `phot_g_mean_mag`, `parallax`, `pmra`, `pmdec`.
- **AllWISE:** `ra`, `dec`, `w1mpro`, `w2mpro`, `w3mpro`, `w4mpro`.
- Radio de búsqueda: `max(0.01, min(FOV × 0.75, 1.5))` grados.
- Solo se dibujan fuentes dentro de la cuadrícula.
- Se devuelven coordenadas de píxel además de las celestes.

Las superposiciones son visuales. Para la validación de candidatos se usa
`spherex.crossmatch`, que consulta Gaia DR3 y AllWISE por TAP, guarda el
resultado 30 días en `crossmatch_cache` y marca cada candidato con la
contrapartida más cercana dentro de 8″. Esa anotación alimenta las
características del clasificador.

---

## 16. API HTTP

Todas las respuestas de error de la API tienen la forma:

```json
{ "error": "Mensaje" }
```

Las rutas no-API desconocidas devuelven la página principal para permitir
navegación directa.

### Aplicación y catálogo

| Método | Ruta | Descripción |
| --- | --- | --- |
| GET | `/` | Interfaz web. |
| GET | `/api/status` | Estado, versión, capacidades y conteo de datasets. |
| GET | `/api/bands` | Catálogo de 102 canales y 6 detectores. |
| GET | `/api/datasets` | Lista de datasets. |
| GET | `/api/datasets/<id>` | Detalle con épocas, frames, cobertura y bandas. |
| GET | `/api/scenarios` | Alias legacy de los demos. |
| GET | `/api/scenario/<id>/observations` | Alias legacy de épocas de un demo. |

### Archivo y resolución

| Método | Ruta | Cuerpo / parámetros |
| --- | --- | --- |
| POST | `/api/resolve` | `{ "name": "M31" }`. |
| POST | `/api/archive/query` | `ra`, `dec` o `name`; `mjd_min`, `mjd_max`, `limit`. |
| POST | `/api/archive/load` | `ra`, `dec` o `name`; `fov`, `npix`, `max_epochs`, `max_detectors`, `epoch_days`, `detectors`, `cutout_size`, `label`. |

`/api/archive/query` devuelve observaciones, épocas, rango de MJD, detectores y
una selección sugerida.

`/api/archive/load` devuelve el dataset construido y un registro de progreso.

### Imágenes y visualización

| Método | Ruta | Parámetros |
| --- | --- | --- |
| GET | `/api/datasets/<id>/image.png` | `epoch`, `detector`, `cmap`, `stretch`, `low`, `high`, `gamma`, `size`, `mode`. |
| GET | `/api/datasets/<id>/diff.png` | `epoch_a`, `epoch_b`, `size`, `scale_sigma`. |
| GET | `/api/datasets/<id>/sum.png` | `epoch_a`, `epoch_b`, `cmap`, `stretch`, `low`, `high`, `gamma`, `size`. |
| GET | `/api/datasets/<id>/band/<k>.png` | `epoch`, `cmap`, `stretch`, `low`, `high`, `gamma`, `size`. |

Parámetros de render:

| Parámetro | Valores | Defecto |
| --- | --- | --- |
| `cmap` | `mono`, `ir`, `inferno`, `ice` | `mono` |
| `stretch` | `asinh`, `log`, `sqrt`, `linear`, `zscale` | `asinh` |
| `low` | percentil | `0.5` |
| `high` | percentil | `99.6` |
| `gamma` | número | `1.0` |
| `size` | 128–1400 px | `768` (frontend usa 1024) |
| `scale_sigma` | sigma de diferencia | `5` en API, `6` en UI |

### Ciencia

| Método | Ruta | Parámetros |
| --- | --- | --- |
| GET | `/api/datasets/<id>/bands` | `epoch` opcional. |
| GET | `/api/datasets/<id>/spectrum` | `ra`, `dec` o `x`, `y`; `epoch`. |
| GET | `/api/datasets/<id>/detect` | `epoch_a`, `epoch_b`, `threshold`, `min_area`, `catalog`. |
| GET | `/api/datasets/<id>/stack` | `epoch`. |
| GET | `/api/datasets/<id>/meta` | `epoch`. |
| GET | `/api/datasets/<id>/catalogs` | `catalog=gaia|wise`, `limit`. |
| GET | `/api/catalog/overlay` | Alias: `dataset`, `catalog`, `limit`. |
| GET | `/api/catalog/stats` | Índice local, conocimiento y clasificador. |
| GET | `/api/catalog/search` | `ra`, `dec`, `radius`, `band`, `mjd_min`, `mjd_max`, `limit`. |
| GET | `/api/catalog/field` | `ra`, `dec`, `radius`. |
| GET | `/api/catalog/crossmatch` | `ra`, `dec`, `radius`, `force`. |
| GET | `/api/model/status` | Métricas y metadatos del clasificador. |
| POST | `/api/model/train` | `{ include_archive }`; requiere sesión y CSRF. |

Notas:

- `detect` exige al menos dos épocas y `epoch_a ≠ epoch_b`.
- `threshold` por defecto 4 en la API y 6 en la interfaz.
- `min_area` por defecto 3; rango 1–200.
- `catalog=1` en `detect` añade la anotación Gaia/WISE a cada candidato.
- `spectrum` usa el centro del campo si no se dan coordenadas.
- `catalogs` limita `limit` a 1–2000; por defecto 600.

### Exportación y carga

| Método | Ruta | Descripción |
| --- | --- | --- |
| GET | `/api/datasets/<id>/export` | Informe JSON; `detect=0` omite la detección. |
| GET | `/api/export_report` | Alias legacy: `dataset` o `id`. |
| POST | `/api/upload` | `multipart/form-data` con `file`, `label`, `ra`, `dec`, `date_obs`, `mjd`. |

La carga máxima es de 96 MB.

### Cabeceras de respuesta de imágenes

| Cabecera | Endpoint | Significado |
| --- | --- | --- |
| `X-Epoch` | `image.png` | Época renderizada. |
| `X-Detector` | `image.png` | Detector o `all`. |
| `X-Epoch-A`, `X-Epoch-B` | `diff.png`, `sum.png` | Épocas comparadas. |
| `X-Baseline-Days` | `diff.png` | Línea base en días. |
| `X-Spectral-Method` | `diff.png`, `sum.png` | `matched-bands` o `coadd`. |
| `X-Matched-Bands` | `diff.png`, `sum.png` | Número de canales comunes. |
| `X-Matched-Pixels` | `diff.png` | Píxeles comunes. |
| `X-Band` | `band/<k>.png` | Índice del canal. |

### Cuentas

| Método | Ruta | Cuerpo / respuesta |
| --- | --- | --- |
| GET | `/api/auth/me` | `{ user, csrf, favorites, preferences }`; `user=null` sin sesión. |
| POST | `/api/auth/register` | `{ username, email, password, display_name? }` → 201 con sesión. |
| POST | `/api/auth/login` | `{ login \| username \| email, password }` → sesión. |
| POST | `/api/auth/logout` | `{ ok: true }`. |

La sesión se guarda en una cookie firmada. Los errores de validación devuelven
`400`; credenciales incorrectas, `401`; duplicados, `409`; exceso de intentos,
`429`.

### Asistente

| Método | Ruta | Descripción |
| --- | --- | --- |
| GET | `/api/assistant/status` | Modo, modelo, proveedor, temas, herramientas, índice local y clasificador. |
| GET | `/api/assistant/conversations` | Lista de conversaciones del usuario. |
| POST | `/api/assistant/conversations` | Crea una conversación (`title`, `dataset_id`). |
| GET | `/api/assistant/conversations/<id>` | Conversación y mensajes. |
| DELETE | `/api/assistant/conversations/<id>` | Elimina la conversación. |
| POST | `/api/assistant/chat` | `{ message, conversation_id?, dataset_id? }` → SSE. |

`/api/assistant/chat` responde con `text/event-stream` y estos eventos:

| Evento | Contenido |
| --- | --- |
| `meta` | `conversation_id` y título. |
| `delta` | Fragmento de texto de la respuesta. |
| `tool` | Nombre y etiqueta de la herramienta usada. |
| `done` | Cierre con `conversation_id` y título. |
| `error` | Mensaje de error. |

### Favoritos y preferencias

| Método | Ruta | Cuerpo / respuesta |
| --- | --- | --- |
| GET | `/api/favorites` | `{ favorites: [resumen], ids: [...] }`. |
| POST | `/api/favorites/<dataset_id>` | Alterna el favorito → `{ favorite, ids }`. |
| GET | `/api/prefs` | `{ preferences }`. |
| PUT | `/api/prefs` | `{ dataset?, cmap?, stretch?, detector?, showGrid?, showCandidates? }`. |

Todas estas rutas requieren sesión. `POST`, `PUT` y `DELETE` exigen la cabecera
`X-CSRF-Token` con el token de `/api/auth/me`; si falta o no coincide, la API
responde `403`.

---

## 17. Cuentas, base de datos y asistente

### Módulos

| Archivo | Responsabilidad |
| --- | --- |
| `spherex/config.py` | Carga de `.env`, clave de sesión y ajustes del asistente. |
| `spherex/db.py` | Esquema SQLite y operaciones de usuarios, chat, favoritos y preferencias. |
| `spherex/auth.py` | Sesiones, CSRF, validación, hash de contraseñas y límite de intentos. |
| `spherex/catalog.py` | Índice local del catálogo IRSA, caché de cruces y BM25. |
| `spherex/crossmatch.py` | Consulta Gaia DR3 / AllWISE y anotación de candidatos. |
| `spherex/model.py` | Extracción de características, entrenamiento y clasificación. |
| `spherex/assistant.py` | Prompt de sistema, herramientas, cliente SSE y experto local. |

### Autenticación

- **Contraseñas:** `generate_password_hash` / `check_password_hash` de Werkzeug
  (PBKDF2/scrypt). El hash nunca sale de la base.
- **Sesión:** cookie firmada con la clave de Flask. `HttpOnly`, `SameSite=Lax`
  y caducidad de 30 días.
- **CSRF:** al iniciar sesión se genera un token aleatorio y se guarda en la
  sesión. Las rutas que escriben lo comparan con `hmac.compare_digest`.
- **Límite de intentos:** 8 fallos por usuario/correo en 10 minutos. Un login
  correcto limpia el contador.
- **Tiempo constante:** si el usuario no existe se verifica un hash ficticio
  para no revelar la existencia de la cuenta.
- **Sesión pública:** `/api/auth/me` devuelve `user=null` sin sesión y nunca
  incluye el hash ni datos internos.

### Esquema SQLite

Archivo único en `instance/spherex.db` (o `SPHEREX_DB`). Cada operación abre una
conexión corta; la base usa `PRAGMA foreign_keys = ON`, `journal_mode = WAL` y un
candado en proceso para las escrituras.

```sql
users(id, username UNIQUE, email UNIQUE, password_hash, display_name,
      role, created_at, last_login_at)

conversations(id, user_id → users, title, dataset_id, created_at, updated_at)

messages(id, conversation_id → conversations, role, content, tool_name,
         created_at)

favorites(user_id → users, dataset_id, created_at,
          PRIMARY KEY (user_id, dataset_id))

preferences(user_id → users, data JSON, updated_at)
```

`ON DELETE CASCADE` elimina el historial, los favoritos y las preferencias al
borrar una cuenta. Las conversaciones se ordenan por `updated_at`.

El índice científico vive en una base aparte, `instance/catalog.db` (o
`SPHEREX_CATALOG_DB`):

```sql
observations(obs_id, band, ra, dec, detector, em_min_um, em_max_um,
             t_min, t_max, exptime, access_url, calib_level, target_name,
             obs_title, pixel_scale, resolution, fov, s_region, ...,
             PRIMARY KEY (obs_id, band))

index_meta(key PRIMARY KEY, value)

crossmatch_cache(cache_key PRIMARY KEY, payload JSON, created_at)

knowledge_docs(id, title, source, text)
```

### Asistente

Dos modos según la configuración:

1. **LLM:** `POST {base_url}/chat/completions` con `stream=true`. El cliente lee
   el SSE del proveedor, reenvía los fragmentos y ejecuta las llamadas a
   herramientas que pida el modelo. Funciona con Google Gemini (endpoint
   `https://generativelanguage.googleapis.com/v1beta/openai`) y con cualquier
   API compatible con OpenAI. Gemini 3 devuelve un `thought_signature` dentro
   de cada llamada a herramienta; `_stream_completion()` lo conserva y
   `stream_reply()` lo reenvía solo a proveedores Google (los demás no lo
   aceptan). Sin ese campo Gemini responde 400.
2. **Local:** si no hay `SPHEREX_AI_API_KEY`, `offline_answer()` interpreta la
   pregunta, ejecuta consultas reales sobre el dataset abierto y la base de
   conocimiento, y compone la respuesta según la intención detectada. Mantiene
   el contexto del historial (coordenadas, épocas y candidatos) y varía el
   encabezado y las sugerencias entre turnos. Añade la nota de configuración
   solo si la pregunta lo requiere.

   Flujo interno del modo local:

   | Paso | Función | Efecto |
   | --- | --- | --- |
   | Parseo | `_parse_request()` | Extrae RA/Dec, radio, banda, épocas, umbral, MJD/fecha y candidato. |
   | Intención | `_intent_scores()` / `_select_intents()` | Puntúa reglas y elige hasta tres secciones. |
   | Ejecución | `_SECTION_HANDLERS` | Cada sección consulta análisis, catálogo o conocimiento. |
   | Composición | `offline_answer()` | Une secciones, añade sugerencia de siguiente paso. |

Límites del agente:

| Constante | Valor | Efecto |
| --- | --- | --- |
| `HISTORY_MESSAGES` | 14 | Mensajes previos enviados al modelo. |
| `MAX_TOOL_STEPS` | 4 | Rondas de herramientas por respuesta. |
| `MAX_TOOL_CHARS` | 7000 | Recorte del resultado de cada herramienta. |

Herramientas de solo lectura:

| Herramienta | Devuelve |
| --- | --- |
| `list_datasets` | Datasets disponibles con tipo y épocas. |
| `dataset_summary` | Posición, FOV, épocas, frames y cobertura. |
| `band_coverage` | Estado de los 102 canales en una época. |
| `quality_report` | Contaminación, fondo, flags y luz zodiacal. |
| `detect_candidates` | Comparación de épocas, candidatos y clasificación ML. |
| `measure_spectrum` | Espectro en RA/Dec con canales y firmas. |
| `search_catalog` | Índice local por posición, banda y tiempo. |
| `catalog_stats` | Resumen del índice, conocimiento y clasificador. |
| `search_knowledge` | Pasajes BM25 de la documentación. |
| `crossmatch_field` | Gaia DR3 y AllWISE alrededor de una posición. |

El servidor guarda el mensaje del usuario, transmite la respuesta y, al
terminar, guarda el texto del asistente. Los eventos SSE son `meta`, `delta`,
`tool`, `done` y `error`.

### Configuración

`.env` se lee al importar `spherex` y **no sobrescribe** variables ya definidas
en el entorno. Esto permite que los tests y el despliegue tengan prioridad.

| Variable | Defecto | Uso |
| --- | --- | --- |
| `SPHEREX_AI_API_KEY` | vacío | Activa el modo LLM. |
| `SPHEREX_AI_BASE_URL` | `https://api.openai.com/v1` | Endpoint compatible. Gemini: `https://generativelanguage.googleapis.com/v1beta/openai`. |
| `SPHEREX_AI_MODEL` | `gpt-4o-mini` | Modelo de chat. Gemini: `gemini-3.5-flash-lite` (más cuota) o `gemini-3.5-flash` (más razonamiento). |
| `SPHEREX_AI_MAX_TOKENS` | `1200` | Tope de respuesta (200–4000). |
| `SPHEREX_SECRET_KEY` | generada | Firma de cookies. |
| `SPHEREX_DB` | `instance/spherex.db` | Ruta de SQLite. |
| `SPHEREX_CATALOG_DB` | `instance/catalog.db` | Índice local y caché de cruces. |
| `SPHEREX_MODEL_DIR` | `instance/models` | Pesos y métricas del clasificador. |
| `OPENAI_API_KEY` | vacío | Alternativa a `SPHEREX_AI_API_KEY`. |
| `OPENAI_BASE_URL` | vacío | Alternativa a `SPHEREX_AI_BASE_URL`. |

#### Google Gemini

Configuración mínima en `.env`:

```dotenv
SPHEREX_AI_API_KEY=tu_clave_de_google_ai_studio
SPHEREX_AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
SPHEREX_AI_MODEL=gemini-3.5-flash-lite
SPHEREX_AI_MAX_TOKENS=1200
```

Notas:

- La clave se crea en Google AI Studio y se queda en `.env` (excluido por
  `.gitignore`); el navegador solo recibe `configured`, `model` y `provider`.
- No todos los modelos listados están disponibles para todas las claves. Los
  modelos retirados responden 404 y algunos previews pueden agotar el tiempo de
  espera en el endpoint compatible con OpenAI. `gemini-3.5-flash-lite` responde
  rápido y suele tener más cuota gratuita; `gemini-3.5-flash` razona mejor pero
  se agota antes. Si cambias de modelo, prueba primero con una pregunta corta.
- Antes de cada turno, `_viewer_context()` calcula un bloque **DATOS ACTUALES DEL
  VISOR** con coordenadas, épocas, cobertura de canales, calidad, catálogo y
  clasificador, y lo inyecta en el mensaje de sistema. El modelo recibe así el
  contexto real del campo abierto y puede responder la mayoría de preguntas en
  una sola petición.
- Gemini no consulta IRSA, Gaia o AllWISE por su cuenta. Cuando hace falta un
  cálculo que no está en el bloque (candidatos, espectro, cruce, búsqueda), el
  agente llama a las herramientas del visor (`search_catalog`,
  `crossmatch_field`, `detect_candidates`, `measure_spectrum`, etc.) y recibe
  los resultados reales; el modelo solo razona y redacta sobre ellos.
- Los modelos Gemini 3 gastan tokens en razonamiento interno. Con 1200 tokens de
  tope suele alcanzar para respuestas breves; súbelo a 2000 si truncas.
- El plan gratuito limita las peticiones por día y por modelo. Cuando se agota,
  el proveedor responde 429 con el tiempo de espera; `_completion_error()` lo
  traduce y `stream_reply()` continúa con el experto local para no dejar la
  pregunta sin respuesta. Solo se reintenta un 429 con `Retry-After` corto
  (≤ 10 s); las cuotas diarias fallan de inmediato.
- El análisis de imágenes y la comparación visual asistida por el modelo quedan
  como trabajo futuro: hoy la detección de cambios la hace el pipeline numérico
  del visor, no el LLM.

### Seguridad y alcance

- El visor, la búsqueda, las imágenes y los informes son públicos.
- Solo el asistente, el historial, los favoritos y las preferencias requieren
  sesión.
- La aplicación está pensada para ejecutarse en local o en una red de confianza
  durante la evaluación; el servidor de desarrollo de Flask no es un despliegue
  de producción.

---

## 18. Flujo completo de datos

### Flujo real

```
Usuario elige nombre o RA/Dec
        │
        ▼
POST /api/archive/query
        │
        ├── Sesame resuelve el nombre (si hace falta)
        ├── TAP consulta spherex.artifact + spherex.plane
        └── Agrupa observaciones en épocas diarias
        │
        ▼
Usuario elige épocas y pulsa "Cargar datos reales"
        │
        ▼
POST /api/archive/load
        │
        ├── Selecciona épocas y detectores
        ├── Descarga recortes level-2 (con caché)
        ├── Lee IMAGE, FLAGS, VARIANCE, ZODI, WCS-WAVE
        ├── Reproyecta a cuadrícula TAN norte-arriba
        ├── Descarta frames sin solapamiento útil
        ├── Reindexa épocas
        ├── Registra frames por FFT subpíxel
        └── Persiste meta.json + frames.npz
        │
        ▼
Frontend abre el dataset
        │
        ├── GET /image.png        → imagen de la época
        ├── GET /stack            → coadd y ganancia SNR
        ├── GET /bands            → cobertura de 102 canales
        ├── GET /spectrum         → espectro al hacer clic
        ├── GET /detect           → candidatos al comparar
        ├── GET /meta             → calidad y contaminación
        └── GET /catalogs         → Gaia / AllWISE
```

### Flujo de demostración

```
Arranque del servidor
        │
        ▼
store.ensure_demo_datasets()
        │
        ├── Genera 4 escenarios sintéticos
        ├── Cada uno con 3 épocas y 192 × 192 px
        └── Mantiene los datasets en memoria
        │
        ▼
Al pedir espectro o canal:
demo.ensure_demo_cube()
        │
        └── Construye un cubo 102 × 192 × 192 bajo demanda
```

### Flujo de carga propia

```
Archivo del usuario
        │
        ▼
POST /api/upload
        │
        ├── Guarda el archivo con hash
        ├── Intenta leerlo como SPHEREx level-2
        ├── Si falla, lo lee como FITS 2D o imagen
        ├── Usa WCS del encabezado o WCS TAN genérico
        ├── Reproyecta a 192 × 192
        └── Crea dataset de una época
```

---

## 19. Criterios del reto y dónde se demuestran

| Criterio | Implementación |
| --- | --- |
| Actualización sencilla con datos recientes | `POST /api/archive/load`, `ingest.py` y apertura del dataset más reciente. |
| Visualización creativa de las 102 bandas | Rejilla 6 × 17, imagen por canal y cobertura real por época. |
| Suma/coadd independiente de los 6 detectores para SNR | `frames.combine_frames()` con varianza inversa; tabla de coadd y ganancia `sqrt(N)`. |
| Generación instantánea de espectros | Clic en el cielo → `/spectrum` con muestras nativas y canales de referencia. |
| Suma/resta de imágenes entre épocas | `sum.png` y `diff.png`; detección en canales comunes. |
| FOV configurable | Campo FOV en la búsqueda, `--fov` y `--npix` en la CLI. |
| Compromiso FOV / movimiento / transferencia | Recortes de tamaño controlado, cuadrícula configurable y solo épocas/detectores pedidos. |
| Superposición de Gaia y WISE | `/catalogs` con TAP de `gaia_dr3_source` y `allwise_p3as_psr`. |
| Consultas por coordenadas en la URL | Parámetros `ra`, `dec`, `dataset`, `epoch`, `mode`, `band`, `panel`. |
| Metadatos e indicadores de contaminación | `/meta`: flags, residuales, saturación, luz zodiacal e índice. |
| Astropy | FITS, WCS, WCS-WAVE, reproyección y coordenadas. |
| Aladin (CDS) | Se evaluó; se optó por superposiciones TAP nativas para mantener el visor autocontenido. |

---

## 20. Limitaciones y notas científicas

- **Cobertura espectral parcial.** Un recorte level-2 contiene la porción del
  detector que cayó sobre esa posición. En una época se obtienen
  aproximadamente 6 muestras nativas (una por detector), no 102. La rejilla de
  bandas indica qué canales están realmente cubiertos.
- **Longitudes de onda variables entre épocas.** El mismo cielo se observa a
  distinta longitud de onda según el barrido. La resta directa de coadds mezcla
  canales y deja residuos; por eso la diferencia usa canales comunes.
- **Movimiento mínimo.** La detección de dipolos exige al menos 4 píxeles de
  separación. Con píxeles de ~6.15–6.75″ esto favorece objetos con movimiento
  rápido; para asteroides lentos se necesitan líneas base mayores o píxeles más
  finos.
- **Flags.** El bit 21 aparece en la mayoría de los píxeles SPHEREx y es
  informativo; el índice de contaminación usa solo los bits 0–3.
- **Modo demo.** Los escenarios sintéticos están marcados como `demo` y no deben
  interpretarse como observaciones reales.
- **Cargas propias.** Tienen una sola época, no tienen información espectral y
  no permiten comparación, suma, diferencia ni detección.
- **Detección estadística.** Es un cribado, no una determinación de órbita ni
  una clasificación física definitiva. Los candidatos requieren seguimiento.
- **Índice de contaminación.** Es una heurística 0–100 para orientar al usuario,
  no un producto calibrado del archivo.
- **Render PNG.** Las imágenes son para visualización; no sustituyen a los FITS
  científicos ni a una fotometría calibrada.
- **Catálogos.** Gaia DR3 y AllWISE se usan como superposiciones y como
  contrapartidas débiles para el clasificador. No hay corrección de movimiento
  propio en la imagen y una coincidencia no confirma ni descarta un candidato.
- **Clasificador.** Es una regresión logística pequeña entrenada con etiquetas
  demo, contrapartidas Gaia/WISE (débiles) y aumento de datos. La exactitud de
  la interfaz se mide sobre un test reducido y no garantiza generalización;
  úsalo para ordenar candidatos, no como descubrimiento.
- **Disponibilidad del archivo.** La cobertura de SPHEREx depende de la posición
  y la fecha; algunos campos pueden tener una sola época.
- **URL.** La aplicación escribe `dataset`, `epoch`, `mode`, `band`, `panel`,
  `ra` y `dec`. No persiste zoom, paneo, colormap, stretch, detector ni
  catálogos activos en la URL; con sesión, colormap, stretch, detector, grid y
  candidatos se guardan en las preferencias.
- **Cuentas.** No hay recuperación de contraseña, verificación de correo ni
  roles administrativos. Están pensadas para uso local o evaluación.
- **Asistente.** Puede equivocarse. El modo local es determinista: interpreta
  intenciones y ejecuta análisis reales, pero no genera razonamiento libre ni
  cubre preguntas fuera de sus reglas; en esos casos busca documentación o pide
  una pista. No sustituye un análisis científico, y el modo LLM depende de la
  API externa configurada.
- **Servidor local.** El servidor de desarrollo de Flask es para evaluación, no
  un despliegue de producción. Las cuentas y el asistente no están pensados para
  exponerse a Internet sin un servidor WSGI, HTTPS y copias de seguridad.
- **Windows Smart App Control.** En algunas instalaciones de Windows 11 esta
  política bloquea las DLL sin firmar de Astropy (`_wcs`, `_parse_times`) con el
  error «Una directiva de Control de aplicaciones bloqueó este archivo». Para
  ejecutar el servidor con datos reales hay que desactivar Smart App Control o
  permitir esas DLL. El resto de módulos (NumPy, SciPy, Pillow, Flask) no se ven
  afectados.
- **Aladin.** Se evaluó su integración, pero se prefirió mantener el visor
  autocontenido con consultas TAP propias.

---

## 21. Glosario

| Término | Significado |
| --- | --- |
| **ADQL** | Astronomical Data Query Language, dialecto SQL para TAP. |
| **Apertura** | Región circular alrededor de una coordenada para medir flujo. |
| **Asinh** | Estiramiento no lineal suave, habitual en infrarrojo. |
| **Coadd** | Combinación de varias imágenes para mejorar SNR. |
| **Dipolo** | Par negativo/positivo que indica una fuente que se movió. |
| **FOV** | Field of View, campo visual. |
| **IRSA** | NASA/IPAC Infrared Science Archive. |
| **LVF** | Linear Variable Filter, filtro de longitud de onda variable. |
| **MAD** | Median Absolute Deviation, medida robusta de dispersión. |
| **MJD** | Modified Julian Date. |
| **PSF** | Point Spread Function. |
| **SNR** | Signal-to-Noise Ratio, relación señal/ruido. |
| **TAN** | Proyección tangencial. |
| **TAP** | Table Access Protocol. |
| **Transitorio** | Fuente que aparece o cambia de forma extrema entre épocas. |
| **Variable** | Fuente que cambia de brillo en una posición fija. |
| **WCS** | World Coordinate System. |
| **WCS-WAVE** | WCS que mapea píxeles a longitud de onda. |
| **Zodi** | Luz zodiacal. |

---

## 22. Archivos del proyecto

```
spherex-viewer/
├── server.py                 # Aplicación Flask y API REST
├── ingest.py                 # Precarga de campos reales desde IRSA
├── requirements.txt          # Dependencias
├── .env.example              # Plantilla de configuración opcional
├── README.md                 # Resumen del proyecto
├── DOCUMENTACION.md          # Este documento
├── spherex/
│   ├── bands.py              # 102 canales y 6 detectores LVF
│   ├── archive.py            # TAP, cutouts, Sesame, Gaia/AllWISE
│   ├── frames.py             # FITS, WCS-WAVE, reproyección, registro, coadd
│   ├── pipeline.py           # Consulta → descarga → reproyección → dataset
│   ├── analysis.py           # Detección, espectros, bandas, calidad
│   ├── render.py             # Stretch, colormaps, PNG de imagen y diferencia
│   ├── store.py              # Registro y persistencia de datasets
│   ├── demo.py               # Escenarios sintéticos
│   ├── config.py             # .env, clave de sesión y ajustes del asistente
│   ├── db.py                 # SQLite: usuarios, chat, favoritos, preferencias
│   ├── auth.py               # Sesiones, CSRF, validación y límite de intentos
│   ├── catalog.py            # Índice local SPHEREx y BM25
│   ├── crossmatch.py         # Gaia DR3 / AllWISE por TAP
│   ├── model.py              # Clasificador de candidatos
│   └── assistant.py          # Agente, herramientas y experto local
├── data/
│   └── spherex_spectral_images.csv   # Export real del catálogo IRSA
├── static/
│   ├── index.html            # Estructura de la interfaz
│   ├── css/style.css         # Estilos
│   └── js/app.js             # Controlador del frontend
├── instance/                 # SQLite, índice, clave de sesión y modelos
└── uploads/cache/
    ├── cutouts/              # Recortes FITS descargados
    ├── datasets/             # Datasets persistidos
    └── uploads/              # Archivos subidos por el usuario
```

---

## Créditos

Desarrollado para el **NASA Space Apps Challenge**.

Datos: **NASA/IPAC Infrared Science Archive (IRSA)**, misión **SPHEREx**
(NASA JPL / Caltech). Catálogos: **Gaia DR3** (ESA) y **AllWISE**
(NASA/IPAC).
