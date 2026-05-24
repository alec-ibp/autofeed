# ai-digest

Agente de research que recolecta contenido de IA de RSS, Reddit, Hacker News, arXiv, Hugging Face Daily Papers y releases de GitHub, y entrega un **Top 10 semanal** rankeado por un LLM (default Gemini 2.5 Flash) calibrado a un perfil personal. Diseñado para correr en GitHub Actions, costo ~$0, model-agnostic vía LiteLLM.

Spec completo en [`SPEC.md`](SPEC.md).

## Quickstart local (con uv)

Requiere [uv](https://docs.astral.sh/uv/) instalado.

```bash
# Sync: crea .venv con Python 3.11 (pinned en .python-version) e instala todo
uv sync

# Tests
uv run pytest

# Corrida heurística (sin LLM, sin gastar cuota)
uv run python -m src.digest --no-llm

# Corrida con LLM (necesita la API key del provider que esté en feeds.yaml)
export GEMINI_API_KEY=...
uv run python -m src.digest
```

Output: `digests/{año}-W{semana}.md` + `digests/state/seen_urls.json`.

## Model-agnostic: cómo funciona el swap

El código nunca conoce qué provider está activo. Solo llama a `litellm.completion(model=settings.model, ...)`. LiteLLM mira el prefijo del modelo (`gemini/...`, `anthropic/...`, `openai/...`) y lee la env var correspondiente.

**Cambiar de provider = 2 cosas, cero código:**

1. En `feeds.yaml`:
   ```yaml
   settings:
     model: "anthropic/claude-sonnet-4-6"
     # opcional: embedding_model en otro provider
     embedding_model: "gemini/text-embedding-004"
   ```
2. Setear la env var local **o** agregar el secret en GitHub (`Settings → Secrets → Actions`):

| Provider del modelo | Env var / GitHub Secret |
|---|---|
| `gemini/...` | `GEMINI_API_KEY` |
| `anthropic/...` | `ANTHROPIC_API_KEY` |
| `openai/...` | `OPENAI_API_KEY` |
| `groq/...` | `GROQ_API_KEY` |
| `mistral/...` | `MISTRAL_API_KEY` |
| `deepseek/...` | `DEEPSEEK_API_KEY` |

El workflow ya pasa estas seis keys desde secrets — si un secret no existe en tu repo, la env var llega vacía y LiteLLM la ignora. **No tienes que editar el workflow YAML para cambiar de provider.**

¿Por qué Gemini de default? Porque tiene free tier sin tarjeta y aguanta holgadamente 1 run/semana (250 RPD).

## Configuración (`feeds.yaml`)

- **`profile`**: estructurado en `priorities`, `anti_priorities`, `exclude_keywords`, `extra_notes`. Es la palanca para que el LLM rankee según lo que te importa.
- **`settings`**: modelo, top_n, dedup, estrategia de ranking (`single` | `two-pass` | `auto`), memoria entre semanas.
- **Fuentes**: `rss`, `reddit`, `hackernews`, `arxiv`, `hf_papers`, `github`.
- **`delivery`**: commit (siempre) + email opcional via Resend.

## Agregar fuentes

### RSS
```yaml
rss:
  - name: "Mi blog favorito"
    url: "https://blog.com/feed.xml"
```

### Newsletters por email → RSS
1. Crea un feed en [Kill the Newsletter](https://kill-the-newsletter.com) → te da una dirección de email y un feed RSS.
2. Suscríbete a la newsletter con esa dirección.
3. Agrega el feed al bloque `rss` de `feeds.yaml`.

### Releases de GitHub
```yaml
github:
  repos:
    - "owner/repo"
```

## Variables de entorno (.env)

El repo es público — los valores sensibles (API keys, email destinatario) viven en `.env` (gitignored) y en GitHub Secrets, **no en `feeds.yaml`**. Copia `.env.example` a `.env` y rellena solo lo que uses.

`feeds.yaml` interpola `${VAR_NAME}` desde el entorno al cargarse. Si una env var falta, se reemplaza por string vacío y el componente que la usa degrada gracilmente (p.ej. email se omite con warning, no rompe el pipeline).

| Env var | Cuándo | Para qué |
|---|---|---|
| `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / ... | provider activo | LiteLLM completion + embeddings |
| `RESEND_API_KEY` | si `delivery.email.enabled = true` | Envío vía Resend |
| `EMAIL_TO` | si email habilitado | Destinatario del digest (interpolado en `feeds.yaml`) |

## Email opcional (Resend)

1. Crea cuenta en [resend.com](https://resend.com) (free tier 3k/mes) y genera una API key.
2. Agrega como GitHub Secrets: `RESEND_API_KEY` y `EMAIL_TO` (tu email).
3. En `feeds.yaml`:
   ```yaml
   delivery:
     email:
       enabled: true
       from: "onboarding@resend.dev"      # default Resend (para testing sin dominio)
       to: ["${EMAIL_TO}"]                # se resuelve desde env
   ```

> **Sin dominio verificado** (testing): `from: "onboarding@resend.dev"` solo envía al email registrado en tu cuenta Resend. **Con dominio verificado** (DNS): `from: "ai-digest@tudominio.com"` envía a cualquier `to`.

## Estructura

```
src/
├── models.py        # Item, RankedItem
├── sources.py       # 6 colectores
├── dedup.py         # exact + semántico
├── memory.py        # seen_urls.json
├── ranker.py        # heurístico + LLM (single & two-pass)
├── render.py        # markdown
├── email.py         # Resend (opcional)
└── digest.py        # orchestrator
```

## Manejo de dependencias

- `pyproject.toml` declara deps en `[project.dependencies]` y dev-deps en `[dependency-groups.dev]`.
- `uv.lock` (commiteado) garantiza builds reproducibles.
- `.python-version` pinea la versión de Python (3.11).
- Agregar una dep: `uv add <pkg>`. Dev: `uv add --dev <pkg>`. Actualizar: `uv lock --upgrade`.

## GitHub Actions

El workflow (`.github/workflows/weekly.yml`) corre los domingos 23:00 UTC y se puede disparar manualmente desde la pestaña Actions. Usa `astral-sh/setup-uv` + `uv sync --frozen` + `uv run`.

## Resiliencia

- Cualquier fuente caída (404/429/timeout) → warning + lista vacía, el run continúa.
- LLM falla o devuelve JSON inválido → 1 reintento + fallback heurístico (ranking por score+recencia).
- Embeddings fallan → dedup semántico se omite.
- `seen_urls.json` corrupto → se trata como vacío.
- Cero items en la semana → digest se genera con nota explícita.
- Resend falla → log de error, digest ya quedó commiteado.

## Costos

Con `gemini/gemini-2.5-flash` y un run semanal:
- 1-6 llamadas de completion + 1 batch de embeddings → muy holgadamente dentro del free tier (250 RPD).
- 0 infra: GitHub Actions gratuito + repo público.

Total esperado: **$0/mes**.
