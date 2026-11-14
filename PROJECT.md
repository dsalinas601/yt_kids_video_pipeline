# PROJECT.md — yt_kids_video_pipeline

> Fuente de verdad: `ARCHITECTURE_BRIEF.md`. Modo de bootstrap: **greenfield** (2026-11-14).

## Identidad

| Campo | Valor |
|---|---|
| Nombre | `yt_kids_video_pipeline` |
| Tipo | Batch-Script / CLI (módulos `.py` locales, ejecución remota en Google Colab Pro GPU sobre Google Drive) |
| Descripción | Pipeline automatizado de postproducción de video para YouTube Kids: recorta silencios en grabaciones multipista, analiza eventos multimodales con Gemini API, realiza auditoría COPPA y renderiza el video final con efectos, música y miniatura mediante FFmpeg. |
| Proveedor cloud | GCP (Google AI Studio / Gemini API + Google Colab Pro GPU) |
| Punto de entrada | `src/main.py:run_pipeline` (`python -m src.main --input <mkv> --base-dir <dir>`) |

## Objetivo

Eliminar el cuello de botella manual de edición en CapCut para videos de gaming infantil de ~30 min. Entrada: `.mkv` multipista (OBS, 1080p60, Pista 1 = Voz, Pista 2 = Juego). Salida desatendida: `final_video.mp4`, `thumbnail.jpg`, `metadata_and_audit.json`, listos para YouTube Kids.

## Stack técnico

| Aspecto | Elección |
|---|---|
| Lenguaje | Python 3.11+ |
| Testing | `pytest` + `pytest-mock` + `pytest-cov` |
| Formato / Lint | `ruff format .` / `ruff check . --fix` |
| Dependencias | `poetry` (`pyproject.toml`) + `requirements.txt` para Colab |
| Librerías clave | `google-genai`, `pydantic`, `auto-editor`, `Pillow`, `pyyaml` |
| Binarios de sistema | `ffmpeg`, `ffprobe` (vía `subprocess`) |
| Cobertura | Dominio puro 100% · Global 85% |

## Componentes (Arquitectura Hexagonal)

| Capa | Ruta | Responsabilidad |
|---|---|---|
| Domain | `src/domain/` | Entidades, Value Objects, reglas de negocio, constructores puros de expresiones FFmpeg. Sin I/O. |
| Application | `src/application/` | Casos de uso (`ProcessVideoUseCase`) y definición de puertos. |
| Infrastructure | `src/infrastructure/` | Adaptadores: `AutoEditorCliAdapter`, `GeminiApiAdapter`, `FfmpegCliAdapter`, `PillowThumbnailAdapter`, `LocalOrDriveFsAdapter`. |
| Entry point | `src/main.py` | CLI, composición de dependencias (wiring). |

## Alcance

- **Incluye:** ingesta/validación `.mkv`, `clean_base.mp4` sin silencios (solo Pista 1), proxy 360p/15fps, extracción estructurada con Gemini (`hook_clip`, `dynamic_events`, `safety_flags`, `metadata`), render FFmpeg `filter_complex` (`h264_nvenc` / fallback `libx264`), miniatura con Pillow.
- **Fuera de alcance:** subida a YouTube Data API v3, lienzo doble 3840x1080, GUI/web, exportación XML/EDL, subtítulos palabra por palabra, fine-tuning, procesamiento en tiempo real.

## Documentos del harness

- [`docs/architecture.md`](docs/architecture.md) — capas, matriz de dependencias, restricciones de I/O, reglas de negocio.
- [`docs/conventions.md`](docs/conventions.md) — estilo, nombres, testing.
- [`docs/verification.md`](docs/verification.md) — cómo se verifica una feature.
- [`CHECKPOINTS.md`](CHECKPOINTS.md) — criterios de calidad.
- [`init.sh`](init.sh) — gatekeeper.
- [`feature_list.json`](feature_list.json) — máquina de estados de features.
- [`progress/`](progress/) — sesión actual e historial.
