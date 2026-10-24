# AIBook

Traductor de libros **PDF y EPUB** con modelos de IA que corren en tu equipo. Conserva títulos, tablas, imágenes y maquetación: solo cambian las palabras. Idiomas: inglés, español, francés, alemán, italiano, japonés y chino (todas las combinaciones).

Plataformas: **macOS Apple Silicon**, **Windows x64** y **Windows ARM64**.

## Cómo funciona

```
Tauri 2 (React UI + Rust)  ──HTTP/WS──►  motor Python (FastAPI)  ──OpenAI API──►  llama-server (llama.cpp)
  hardware · descargas · sidecars          EPUB · PDF (BabelDOC) · OCR              Metal / Vulkan / CPU ARM64
                                           pipeline literario · SQLite
```

**Pipeline literario** (`engine/src/folio_engine/translate/`):

1. **Biblia del libro**: el modelo lee el libro completo y extrae personajes (voz, registro, género), lugares y términos inventados, además del tono y el narrador. Se puede revisar y editar antes de traducir.
2. **Traducción con contexto**: lotes por capítulo con los párrafos anteriores ya traducidos, el glosario pertinente y reglas por idioma (modismos por sentido, voz de cada personaje, convenciones de diálogo: raya en español, « » en francés, 「」 en japonés…).
3. **Revisión editorial** (modo Impecable): una segunda pasada corrige omisiones, calcos y errores de sentido.

Cada segmento pasa validadores (marcadores de formato, números, proporción de longitud e idioma de salida) y se guarda en SQLite, de modo que la traducción se reanuda si se interrumpe.

**Formato**:
- **EPUB**: solo cambian los nodos de texto. El formato inline viaja como marcadores `⟦1⟧…⟦/1⟧` y se reconstruye. CSS, imágenes y fuentes se copian byte a byte.
- **PDF**: BabelDOC reconstruye cada página desde una representación intermedia, y la biblia se le inyecta como system prompt y glosario.
- **Escaneados**: OCR (RapidOCR) → traducción → se repinta sobre el fondo original.

**ETA**: se planifica en tokens leídos y generados. Las velocidades reales se aprenden con medias móviles exponenciales y se recalculan en cada petición. Las descargas usan la misma idea: media móvil de la velocidad, reanudación con HTTP Range y verificación sha256.

## Desarrollo

Requisitos: Node 22+, pnpm, Rust estable, [uv](https://docs.astral.sh/uv/).

```bash
python3 scripts/fetch_llama.py          # llama-server para tu plataforma
cd engine && uv sync --extra pdf && uv run pytest -q
cd ../app && pnpm install && pnpm tauri dev
```

En desarrollo la app lanza el motor con `uv run`. En las versiones de release usa el binario congelado con PyInstaller.

Prueba de punta a punta con un modelo real:

```bash
uv run --project engine python scripts/e2e_smoke.py ruta/al/modelo.gguf
```

## Catálogo de modelos

`catalog/models.json` se genera con `python3 scripts/build_catalog.py`. El script toma de Hugging Face el tamaño y el sha256, y lee la cabecera GGUF por streaming para obtener la geometría de atención, con la que la app estima la RAM/VRAM que necesita cada modelo. Para cambiar los modelos, edita la lista `MODELS` del script.

## Releases y actualizaciones

- **`ci.yml`**: en cada PR y en cada push a `main`, ejecuta los tests del motor y compila la app en las 3 plataformas.
- **`release.yml`**: al subir una etiqueta `vX.Y.Z`, compila en runners nativos (`macos-14`, `windows-latest`, `windows-11-arm`). Descarga llama.cpp, congela el motor y comprueba que arranca, empaqueta, **firma** las actualizaciones y publica la Release de GitHub con los instaladores y `latest.json`.
- **En la app**: comprueba si hay versión nueva al abrir y cada 6 h. Si la hay, muestra un aviso en la barra lateral con el progreso y el tiempo restante de la descarga. Antes de instalar detiene el motor y `llama-server`, verifica la firma ed25519 y se reinicia. Si hay una traducción en curso, espera a que termine.

Configuración inicial (una sola vez):

1. Crea el repo `github.com/sorodriguezz/AIBook`. Si usas otro nombre, cambia `plugins.updater.endpoints` en `app/src-tauri/tauri.conf.json`.
2. Añade el secreto `TAURI_SIGNING_PRIVATE_KEY` con el contenido de `~/.tauri/folio.key`, y `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` vacío. **No pierdas esa clave**: sin ella, las versiones instaladas no aceptarán actualizaciones nuevas.

Publicar una versión:

```bash
python3 scripts/bump_version.py 0.2.0
```

```bash
git commit -am "Release v0.2.0" && git tag v0.2.0 && git push --follow-tags
```

Los binarios no tienen firma de sistema operativo (Apple/Microsoft). En macOS hay que permitir la app la primera vez en *Ajustes → Privacidad y seguridad*, y Windows SmartScreen mostrará un aviso.

## Licencias

Uso personal / open source. BabelDOC y PyMuPDF son AGPL-3.0, por lo que AIBook se distribuye bajo AGPL-3.0. Cada modelo conserva su propia licencia; la app la muestra en su tarjeta. Por ejemplo, Hunyuan-MT no está licenciado para la UE, el Reino Unido ni Corea del Sur.
