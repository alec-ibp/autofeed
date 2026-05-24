# SPEC — ai-digest

> Agente de research que recolecta contenido de IA de múltiples fuentes y entrega
> semanalmente un **Top 10** rankeado y resumido por un LLM, calibrado a un perfil
> personal. Diseñado para implementarse con Claude Code.

> **Versión 2** — incorpora P0/P1 sobre la v1: fuentes research-first (arXiv, HF Daily
> Papers, GitHub releases), memoria entre semanas, dedup semántico, ranking en dos
> pasadas, perfil estructurado y entrega por email opcional. Cambios resumidos al final.

---

## 1. Contexto y objetivo

Mantenerme a la vanguardia en IA y desarrollo de software agéntico sin leer 50 fuentes a mano.

El sistema debe, una vez por semana:
1. Recolectar artículos/posts/discusiones/papers de blogs, Substacks, Reddit, Hacker News, arXiv, Hugging Face Papers y releases de GitHub.
2. Deduplicar (incluido semánticamente y contra semanas anteriores) y pre-filtrar por señal (recencia + popularidad).
3. Pedir a un LLM que elija el **Top 10 más útil para mi perfil** y escriba un "por qué importa" por item.
4. Entregar un digest en Markdown (commit al repo) y, opcionalmente, por email.

**Restricciones duras:**
- **Costo ~$0**: usar tiers gratuitos. Arrancar con Gemini.
- **Model-agnostic**: cambiar de proveedor (Gemini → Anthropic → OpenAI → otro) debe ser cambiar **una variable de entorno**, sin tocar código.
- **Sin servidor**: correr en GitHub Actions (cron semanal). Nada de infra propia.
- **Resiliente**: una fuente caída no debe tumbar el run.

---

## 2. Alcance

**Dentro:**
- Fuentes: RSS/Atom, Reddit (lectura pública), Hacker News (API Algolia), **arXiv (RSS oficial)**, **Hugging Face Daily Papers (API)**, **GitHub releases (API)**.
- Ranking + resúmenes vía LLM con abstracción multi-proveedor.
- **Memoria entre semanas**: estado persistido (JSON) de URLs ya cubiertas en las últimas N semanas.
- **Dedup semántico** vía embeddings (Gemini free tier).
- Entrega: archivo Markdown commiteado al repo y, opcionalmente, email via Resend.
- Configuración por archivo (`feeds.yaml`): perfil estructurado, modelo, fuentes, umbrales, delivery.

**Fuera (roadmap, ver §13):**
- X / Twitter (sin tier gratuito de lectura desde feb-2026; pay-per-use). Fase 2.
- Bluesky como fuente.
- Delivery por Slack.
- Persistencia/DB completa, dashboard web.
- Feedback loop (marcar items útiles para ajustar el perfil automáticamente).

---

## 3. Decisiones técnicas (con justificación)

| Decisión | Elección | Por qué |
|---|---|---|
| Lenguaje | Python 3.11+ | Ecosistema de feeds/HTTP maduro. |
| Abstracción LLM | **LiteLLM** | Interfaz única OpenAI-style para ~100 proveedores. Cambiar proveedor = cambiar el string `<provider>/<model>` + su env var. Lee las API keys de variables de entorno por convención (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …). |
| Modelo inicial | `gemini/gemini-2.5-flash` | Free tier vigente (10 RPM / 250 RPD / 1M tokens de contexto). **No usar `gemini-2.0-flash`: deprecado, retirado el 2026-03-03.** Alternativa de mayor cuota: `gemini-2.5-flash-lite` (15 RPM / 1.000 RPD). |
| Embeddings | `gemini/gemini-embedding-001` vía LiteLLM | Free tier; 3072 dims; suficiente para dedup semántico con <500 items. Misma abstracción, mismo env var. (Batch limit Gemini = 100 → `dedup.py` chunkea automáticamente.) |
| RSS | `feedparser` | Estándar de facto, tolerante a feeds rotos. |
| Reddit | endpoint público `.json` (sin API key) | Lectura pública permitida para uso personal/no-comercial. **Requiere `User-Agent` custom** o Reddit responde 429. Evita el API de pago. Plan B si se rompe: PRAW con app read-only OAuth (gratis, requiere registro). |
| Hacker News | API Algolia (`hn.algolia.com`) | Pública, sin auth, sin rate limit relevante para uso semanal. |
| arXiv | RSS oficial (`rss.arxiv.org/rss/{cat}`) | Sin auth, oficial, alimenta `feedparser` igual que otros RSS. Donde está la investigación primaria. (El subdominio `export.arxiv.org/rss` redirige 301 y devuelve feed vacío — usar `rss.arxiv.org`.) |
| Hugging Face Daily Papers | API JSON pública (`huggingface.co/api/daily_papers`) | Papers ya curados por la comunidad con upvotes. Sin auth para lectura. Validar endpoint exacto en implementación (alternativa: scrapear `huggingface.co/papers`). |
| GitHub Releases | API REST (`api.github.com/repos/{owner}/{repo}/releases`) | Usar `GITHUB_TOKEN` (provisto gratis por Actions, 1.000 req/h). Donde aparecen primero las herramientas reales (vLLM, llama.cpp, langgraph, etc.). |
| Memoria entre semanas | JSON local en `digests/state/seen_urls.json` | Sin DB. Suficiente para retención de pocas semanas. Versionado en el repo. |
| Email delivery | Resend (free tier 3k emails/mes) | API simple; opcional, detrás de un flag. |
| Scheduler | GitHub Actions (`schedule: cron`) | Gratis, ya estoy en GitHub, todo versionado. |
| Config | YAML | Editar fuentes/perfil sin tocar código. |

---

## 4. Arquitectura (pipeline)

```
feeds.yaml ─┐
            ▼
   [1] Collect ──► [2] Dedup ──► [3] Prefilter ──► [4] Rank (LLM) ──► [5] Render ──┬─► digests/YYYY-Www.md (commit)
   (RSS/Reddit/HN/    │           │                  (1 o 2 pasadas)                │
    arXiv/HF/GitHub)  │           │                                                 └─► email (opcional, Resend)
                      │           │
                      ▼           ▼
              URL+título    embeddings (semántico)
              normalizados  + memoria semanas previas
                            (digests/state/seen_urls.json)
```

Cada etapa es un módulo desacoplado y testeable de forma aislada. **Dedup** ahora tiene tres niveles: exacto por URL/título normalizado, semántico por embeddings, e histórico contra `seen_urls.json`.

---

## 5. Estructura del proyecto

```
ai-digest/
├── feeds.yaml                  # config (perfil, modelo, fuentes, umbrales, delivery)
├── pyproject.toml              # deps (runtime + dev) + config de pytest
├── uv.lock                     # lockfile reproducible (commiteado)
├── .python-version             # 3.11 (pin para uv)
├── src/
│   ├── models.py               # dataclass Item, RankedItem
│   ├── sources.py              # collectors: RSS, Reddit, HN, arXiv, HF Papers, GitHub
│   ├── dedup.py                # normalización + dedup exacto + dedup semántico
│   ├── memory.py               # seen_urls.json: cargar, filtrar, persistir
│   ├── ranker.py               # abstracción LLM (LiteLLM) + ranking 1-pass / 2-pass
│   ├── render.py               # Item[] -> Markdown
│   ├── email.py                # delivery por Resend (opcional)
│   └── digest.py               # orquestador / entrypoint (python -m src.digest)
├── digests/                    # salida (.md commiteados)
│   └── state/
│       └── seen_urls.json      # memoria de URLs cubiertas en semanas previas
├── tests/                      # unit tests (ver §12)
├── .github/workflows/weekly.yml
└── README.md
```

---

## 6. Modelo de datos

```python
@dataclass
class Item:
    source: str          # "Latent Space", "r/LocalLLaMA", "Hacker News", "arXiv:cs.AI", "HF Papers", "GitHub:vllm-project/vllm"
    source_type: str     # "rss" | "reddit" | "hn" | "arxiv" | "hf_papers" | "github"
    title: str
    url: str             # URL canónica del contenido
    published: datetime | None  # tz-aware UTC; None si la fuente no la expone
    score: int           # señal cruda: upvotes (reddit/HF) / points (HN) / 0 (rss/arxiv/github)
    summary: str         # snippet/descripción corta (puede ir vacío)
```

`Item` debe ser serializable a dict para inyectarlo al prompt y para tests.

---

## 7. Configuración (`feeds.yaml`)

El schema completo va comentado en el propio archivo. Bloques requeridos:

### 7.1 `profile` (estructurado)

```yaml
profile:
  priorities:
    - "Frameworks y herramientas para agentes (langgraph, smolagents, openai-agents, claude-code, etc.)"
    - "Patrones de producción para LLMs: evals, memoria, tool use, RAG avanzado"
    - "Papers con implementación o resultados reproducibles"
    - "Releases de modelos open-weight relevantes"
    - "Metodología y filosofía de diseño con LLMs"
  anti_priorities:
    - "Anuncios de marketing sin profundidad técnica"
    - "Contenido para principiantes (qué es un LLM, prompt engineering 101)"
    - "Hype sin sustancia, hot takes de Twitter"
    - "Listicles ('top 10 prompts')"
  exclude_keywords: ["productivity hacks", "ChatGPT prompts that"]
  extra_notes: |
    Ingeniero senior. Asumo conocimiento técnico de LLMs.
    Prefiero artículos con código sobre artículos solo conceptuales.
```

El `profile` se renderiza al prompt como bloque estructurado (no como prosa libre): da al LLM más palancas concretas y mejora la consistencia del ranking.

### 7.2 `settings`

```yaml
settings:
  model: "gemini/gemini-2.5-flash"
  embedding_model: "gemini/text-embedding-004"
  top_n: 10
  days_back: 7
  max_candidates: 150
  temperature: 0.3
  # Ranking
  ranking_strategy: "auto"      # "single" | "two-pass" | "auto" (two-pass si candidatos > two_pass_threshold)
  two_pass_threshold: 50
  # Dedup
  semantic_dedup: true
  semantic_dedup_threshold: 0.85   # cosine similarity
  # Memoria
  memory_weeks: 4               # cuántas semanas hacia atrás recordar para evitar repetidos
```

### 7.3 Fuentes

```yaml
rss:
  - name: "Simon Willison"
    url: "https://simonwillison.net/atom/everything/"
  # … (Latent Space, HF Blog, DeepMind, BAIR, Anthropic, OpenAI, Mistral, Meta AI, AI News)

reddit:
  subs: ["LocalLLaMA", "MachineLearning", "LLMDevs"]
  min_score: 50
  limit_per_sub: 25

hackernews:
  queries: ["AI agent", "agentic", "LLM", "Claude", "RAG"]
  min_points: 50
  hits_per_query: 20

arxiv:
  categories: ["cs.AI", "cs.CL", "cs.LG"]
  max_per_category: 30

hf_papers:
  days_back: 7
  min_upvotes: 3

github:
  repos:
    - "langchain-ai/langgraph"
    - "huggingface/smolagents"
    - "openai/openai-agents-python"
    - "vllm-project/vllm"
    - "ggerganov/llama.cpp"
    - "anthropics/claude-code"
  include_prereleases: false
```

### 7.4 Delivery

```yaml
delivery:
  commit: true                  # commitear digest al repo (default)
  email:
    enabled: false              # activar para enviar por Resend; requiere RESEND_API_KEY
    from: "ai-digest@example.com"
    to: ["you@example.com"]
    subject_template: "AI Digest — Semana {week_label}"
```

**Set inicial de fuentes RSS** (verificar URLs en la primera corrida; las que fallen se saltan):
Simon Willison, Latent Space, Hugging Face Blog, Google DeepMind, BAIR, Anthropic News, OpenAI Blog, Mistral, Meta AI, AI News (Smol AI).

> Newsletters por email: el spec NO las consume directamente. Se integran convirtiéndolas a RSS con [Kill the Newsletter](https://kill-the-newsletter.com) y agregando el feed resultante al bloque `rss`. Documentar esto en el README.

---

## 8. Componentes — detalle de implementación

### 8.1 `sources.py`

Seis funciones puras que devuelven `list[Item]`. Todas envuelven sus llamadas de red en try/except: ante fallo, **loggean un warning y devuelven lista vacía** (nunca lanzan hacia arriba).

- **`fetch_rss(feeds, since) -> list[Item]`** — como en v1.
- **`fetch_reddit(cfg, since) -> list[Item]`** — como en v1. `User-Agent` obligatorio.
- **`fetch_hn(cfg, since) -> list[Item]`** — como en v1.

- **`fetch_arxiv(cfg, since) -> list[Item]`**
  - Por cada categoría: `GET https://rss.arxiv.org/rss/{category}` (parsear con `feedparser`).
  - `source = "arXiv:{category}"`, `source_type = "arxiv"`.
  - `score = 0`. `published` desde el feed (UTC tz-aware).
  - `summary` = abstract truncado a ~500 chars.
  - Limitar a `max_per_category` items más recientes.

- **`fetch_hf_papers(cfg, since) -> list[Item]`**
  - `GET https://huggingface.co/api/daily_papers?days={days_back}` (validar endpoint exacto en implementación; si la API oficial no expone esto, fallback a parsear HTML de `huggingface.co/papers`).
  - `source = "HF Papers"`, `source_type = "hf_papers"`.
  - `url` = link al paper en HF (`https://huggingface.co/papers/{id}`).
  - `score` = upvotes; filtrar `score >= min_upvotes`.
  - `published` desde `publishedAt` o equivalente.
  - `summary` = abstract truncado a ~500 chars.

- **`fetch_github(cfg, since) -> list[Item]`**
  - Por cada repo: `GET https://api.github.com/repos/{repo}/releases?per_page=10` con header `Authorization: Bearer {GITHUB_TOKEN}` si está disponible (Actions lo expone gratis).
  - Filtrar releases con `published_at >= since`.
  - Si `include_prereleases = false`, saltar `prerelease = true` y `draft = true`.
  - `source = "GitHub:{repo}"`, `source_type = "github"`.
  - `url = release.html_url`, `title = "{repo} {tag_name}"`, `summary = body[:500]`.
  - `score = 0` (los releases no tienen señal cuantitativa nativa; el LLM evalúa por contenido).

### 8.2 `dedup.py`

- **`normalize_url(url) -> str`**: minúsculas en host, quitar querystring de tracking (`utm_*`, `ref`, etc.), quitar slash final y fragmento `#`.

- **`dedup_exact(items) -> list[Item]`**: dedup por URL normalizada **y** por título normalizado (lower + strip). Ante duplicado, conservar el de mayor `score`.

- **`dedup_semantic(items, threshold, embedding_model) -> list[Item]`** *(P1)*
  - Si `len(items) <= 1` o `semantic_dedup = false`, retornar `items` sin cambios.
  - Generar embeddings de `title + " " + summary[:200]` en una sola llamada batch a `litellm.embedding(model=embedding_model, input=[...])`.
  - Agrupar items con cosine similarity ≥ `threshold` (clustering simple por unión de pares; no se necesita HDBSCAN).
  - De cada cluster conservar el de mayor `score`; en empate, el más reciente.
  - **Fallback**: si la llamada de embeddings falla, loggear warning y retornar `items` sin cambios (no fallar el pipeline).

- **`prefilter(items, max_candidates) -> list[Item]`**: si `len > max_candidates`, ordenar por `published` desc (los `None` al final) y cortar a `max_candidates`.

### 8.3 `memory.py` *(P0, nuevo)*

Maneja `digests/state/seen_urls.json`. Formato:

```json
{
  "2026-W21": {"generated_at": "2026-05-23T23:00:00Z", "urls": ["https://...", "..."]},
  "2026-W20": {...}
}
```

- **`load_seen_urls(path, memory_weeks) -> set[str]`**: lee el archivo (vacío si no existe); retorna unión de las URLs normalizadas de las últimas `memory_weeks` semanas.
- **`filter_unseen(items, seen_urls) -> list[Item]`**: descarta items cuya URL normalizada esté en `seen_urls`.
- **`persist_week(path, week_label, items, memory_weeks)`**: agrega la semana actual con sus URLs normalizadas; trunca a las últimas `memory_weeks` semanas; escribe atómicamente (write a temp + rename).

Aplicar en este orden: `collect → dedup_exact → filter_unseen → dedup_semantic → prefilter → rank`. La memoria filtra **antes** del dedup semántico para no gastar embeddings en items que ya van a quedar fuera.

### 8.4 `ranker.py` — núcleo model-agnostic

- **`rank(items, profile, settings) -> list[RankedItem]`**

  **Estrategia** (decidida según `settings.ranking_strategy`):

  - **`single`** (o `auto` con candidatos ≤ `two_pass_threshold`): un solo prompt como en v1.

  - **`two-pass`** (o `auto` con candidatos > `two_pass_threshold`) *(P0)*:
    - **Pasada 1 (rough cut)**: dividir candidatos en lotes de ~30. Para cada lote, prompt corto que pide clasificar cada item como `keep | maybe | discard` según el `profile`. Output JSON minimal (solo índice + label). Conservar todos los `keep` y `maybe` (descartar `discard`).
    - **Pasada 2 (fine ranking)**: prompt completo (como en v1) sobre el conjunto reducido para producir el Top N final con `why`.
    - Total llamadas: ~4-6 (lotes pasada 1 + 1 pasada 2). Sigue dentro del free tier de Gemini Flash (250 RPD).

  - Llama `litellm.completion(model=settings.model, messages=[...], temperature=settings.temperature)`. La API key se toma del entorno por convención de LiteLLM según el provider del `model` — **no** se pasa en código.

  - Parsea la respuesta como JSON. Tolerar fences ` ```json ` (stripear antes de parsear).

  - **Fallback**: si la pasada final falla o el JSON es inválido tras 1 reintento, devolver el Top N heurístico (ordenado por `score` desc, luego recencia) con `why = ""` y loggear el error. Si falla la pasada 1 de un lote, ese lote pasa entero como `maybe` (no se descarta ciegamente).

  **Render del `profile` al prompt**: bloque estructurado, no prosa:
  ```
  PRIORITIES (in order):
  - <priority 1>
  ...
  ANTI-PRIORITIES (avoid):
  - <anti 1>
  ...
  EXCLUDE if title/summary contains: <comma-separated>
  EXTRA NOTES: <free text>
  ```

  **Contrato de salida del LLM** (igual a v1, sin cambios):
  ```json
  {
    "items": [
      {
        "rank": 1,
        "title": "…",
        "url": "…",
        "source": "…",
        "category": "Agentes | Frameworks | Metodología | Releases | Paper | Otro",
        "why": "1–2 frases: por qué es útil para mi perfil, concreto y sin relleno."
      }
    ]
  }
  ```
  - `items` tiene exactamente `min(top_n, n_candidatos)` elementos.
  - `url` debe ser una de las URLs de entrada (no inventar). Validar contra candidatos; descartar las que no.

### 8.5 `render.py`

- **`render_markdown(ranked, week_label, generated_at, model, sources_summary) -> str`**.
- Formato del digest:
  - Encabezado: `# AI Digest — Semana {week_label}` + línea con fecha de generación y modelo usado.
  - Por item: `## {rank}. {title}` → línea `Fuente · Categoría` → el `why` → enlace `[Leer]({url})`.
  - Pie: conteo de items y fuentes procesadas (qué colectores corrieron y cuántos items aportó cada uno).

### 8.6 `email.py` *(P1, opcional)*

- **`send_digest(markdown, html, cfg, week_label)`**: si `delivery.email.enabled = true` y `RESEND_API_KEY` está presente, envía via Resend API (`POST https://api.resend.com/emails`).
- HTML simple: convertir el Markdown a HTML con un template básico (puede usarse `markdown` lib o un convertidor mínimo manual).
- Si falla el envío, loggear error pero **no romper el run** (el commit ya ocurrió).

### 8.7 `digest.py` (entrypoint)

Orquesta:
1. Cargar YAML.
2. `since = now_utc - days_back`.
3. **Collect** (las 6 fuentes en paralelo si conviene; secuencial está bien para el volumen).
4. **`dedup_exact`**.
5. **`filter_unseen`** contra memoria.
6. **`dedup_semantic`** (si está habilitado).
7. **`prefilter`** a `max_candidates`.
8. **`rank`** (single o two-pass según estrategia).
9. **`render_markdown`** → escribir `digests/{YYYY}-W{semana ISO}.md`.
10. **`persist_week`** → actualizar `digests/state/seen_urls.json` con las URLs del Top N (no las descartadas: solo lo que realmente apareció en el digest).
11. **`send_digest`** si email habilitado.
12. Imprimir ruta del digest en stdout.

Idempotente: si el archivo de esa semana ya existe, sobrescribir; y `persist_week` sobreescribe la entrada de esa semana en el estado.

---

## 9. GitHub Actions (`weekly.yml`)

- Triggers: `schedule` (cron semanal, p.ej. domingos 23:00 UTC) **y** `workflow_dispatch` (run manual).
- `permissions: contents: write` (para commitear el digest y el state).
- Steps: checkout → `astral-sh/setup-uv` (cache habilitada) → `uv sync --frozen` → `uv run python -m src.digest` → commit & push de `digests/*.md` y `digests/state/seen_urls.json` con el bot `github-actions[bot]` (no fallar si no hay cambios).
- Env: el workflow expone keys de varios providers desde secrets — `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `DEEPSEEK_API_KEY`, `RESEND_API_KEY`. Los secrets que no existan llegan como string vacío y LiteLLM los ignora. **Cambiar de proveedor LLM = agregar el secret correspondiente + actualizar `settings.model` en `feeds.yaml`; el workflow no se toca.**
- `GITHUB_TOKEN` lo provee Actions automáticamente.

---

## 10. Variables de entorno / secrets

| Var | Cuándo | Notas |
|---|---|---|
| `GEMINI_API_KEY` | proveedor inicial (completion + embeddings) | Free tier, sin tarjeta. Cargar como GitHub Secret. |
| `GITHUB_TOKEN` | siempre (collector de releases) | Provisto por Actions; en local, usar un PAT con scope `public_repo` o ninguno si solo se leen repos públicos. |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / … | al hacer swap | Solo se añade el secret y se cambia `settings.model`. |
| `RESEND_API_KEY` | si `delivery.email.enabled = true` | Free tier 3k/mes. |

Nunca hardcodear keys. `digest.py` no debe loggear el contenido de variables de entorno.

---

## 11. Manejo de errores y edge cases

- Fuente individual caída (RSS 404, Reddit 429, HN timeout, arXiv 503, HF down, GitHub 401) → warning + lista vacía, el run continúa.
- Cero candidatos en la semana → generar digest con nota "sin items relevantes esta semana"; no fallar.
- Feed sin fechas → items con `published=None` pasan el filtro temporal pero van al final del prefiltro.
- LLM devuelve <`top_n` items o JSON malformado → 1 reintento, luego fallback heurístico.
- LLM inventa una URL → se descarta ese item (validación contra candidatos).
- Rate limit del LLM (429) → backoff exponencial (1s/2s/4s) con máx. 3 intentos antes del fallback.
- **Embeddings fallan** → semantic dedup se omite, pipeline continúa con dedup exacto solamente.
- **`seen_urls.json` corrupto o ausente** → se trata como vacío (warning, no error).
- **Pasada 1 del two-pass falla en algún lote** → ese lote pasa entero a la pasada 2 como `maybe` (degradación grácil).
- **Resend falla** → log de error, no romper el run (digest ya commiteado).
- **GitHub API rate limit (60/h sin token o 1000/h con token)** → con `GITHUB_TOKEN` no debería ocurrir para <20 repos; si pasa, saltarse el resto de repos y loggear.

---

## 12. Tests (mínimos)

- `dedup_exact`: dos items con misma URL canónica (con/sin `utm_`) colapsan a 1, conservando el de mayor score.
- `normalize_url`: casos con querystring de tracking, fragmento y slash final.
- `dedup_semantic` (con embeddings mockeados): items con similarity > threshold colapsan; bajo threshold no; fallo en embedding retorna input sin cambios.
- `memory.filter_unseen`: items con URL normalizada en `seen_urls` se excluyen; el resto pasa.
- `memory.persist_week`: tras N+1 escrituras, solo quedan las últimas N semanas; archivo es JSON válido.
- `prefilter`: con `max_candidates=N` y >N items, devuelve N y respeta orden por recencia.
- `ranker` (con LLM mockeado):
  - Single-pass: parsea JSON correcto; con JSON malformado cae al fallback heurístico; descarta items con URL no presente en candidatos.
  - Two-pass: con `candidatos > threshold`, ejecuta dos pasadas; con `candidatos ≤ threshold`, una sola; si la pasada 1 falla en un lote, ese lote llega a la pasada 2 entero.
- `render_markdown`: snapshot del formato con 2 items de ejemplo.

---

## 13. Criterios de aceptación

**Funcionales**
- [ ] `python -m src.digest` corre de punta a punta y crea `digests/{año}-W{semana}.md` + actualiza `digests/state/seen_urls.json`.
- [ ] El digest contiene exactamente `top_n` items (o menos si hubo menos candidatos), cada uno con título, fuente, categoría, "por qué importa" y enlace funcional a una URL que vino de las fuentes.
- [ ] Solo se incluyen items publicados dentro de `days_back` días (excepto los sin fecha, permitidos por diseño).
- [ ] No hay items duplicados (misma URL canónica, mismo título, ni clusters semánticos colapsados) en el digest.
- [ ] **No aparecen URLs que ya estuvieron en el Top N de las últimas `memory_weeks` semanas.**
- [ ] El "por qué importa" refleja el `profile` (prioriza priorities; descarta lo de anti-priorities y `exclude_keywords`).
- [ ] Se recolectan items de las seis familias de fuentes: RSS, Reddit, HN, arXiv, HF Papers, GitHub releases (las que devuelvan ≥1 item esa semana).

**Model-agnostic**
- [ ] Cambiar `settings.model` de `gemini/gemini-2.5-flash` a `anthropic/claude-sonnet-4-6` (o el modelo vigente) + setear `ANTHROPIC_API_KEY` produce un digest equivalente **sin ningún cambio de código**.
- [ ] Cambiar `settings.embedding_model` a un proveedor distinto (p.ej. `openai/text-embedding-3-small`) funciona igual.
- [ ] Ninguna API key aparece en el código fuente; todas se leen del entorno.

**Resiliencia**
- [ ] Si una fuente falla (simular 404/429/timeout en cualquiera de las 6), el run termina con éxito y genera digest con el resto.
- [ ] Si el LLM falla o devuelve JSON inválido, se genera igual el digest vía fallback heurístico, con log del error.
- [ ] Si los embeddings fallan, el dedup semántico se omite y el run continúa.
- [ ] Si `seen_urls.json` no existe o está corrupto, el run no falla.
- [ ] Si no hay candidatos en la semana, el run no falla y deja una nota en el digest.
- [ ] Si Resend falla, el commit ya ocurrió y el run no se marca como fallido por el email.

**Operación**
- [ ] El workflow corre en `schedule` y en `workflow_dispatch` manual.
- [ ] El workflow commitea el `.md` nuevo **y** el `seen_urls.json` actualizado, y no falla cuando no hay cambios.
- [ ] El run semanal se mantiene dentro del free tier del proveedor (≤6 llamadas LLM + 1 batch de embeddings/semana con `gemini-2.5-flash` está holgadamente dentro de 250 RPD).
- [ ] Con `delivery.email.enabled = true` y `RESEND_API_KEY` configurado, llega un email con el digest renderizado.

**Calidad de código**
- [ ] Cada módulo de §5 existe con la responsabilidad descrita y es testeable aislado.
- [ ] Pasan los tests de §12.
- [ ] README documenta: setup local, cargar el secret, cambiar de modelo, agregar fuentes (incl. newsletters vía Kill the Newsletter), activar email.

---

## 14. Roadmap (fuera de alcance ahora)

1. **Feedback loop**: marcar items útiles/no útiles en el digest commiteado; un script lee el feedback y ajusta `profile` (o pesos) automáticamente.
2. **Bluesky** como fuente (API pública sin auth disponible).
3. **X / Twitter** como fuente: requiere pay-per-use; integrar como módulo opcional detrás de un flag y su propia key.
4. **Z-score por fuente** en el prefiltro (normalizar señal entre subreddits/fuentes con varianza muy distinta).
5. **Delivery por Slack** vía webhook.
6. **Diversidad forzada por categoría** en el output del LLM (mínimo X papers, X releases, etc.).
7. **Freshness decay** explícito en prefiltro (`score_ajustado = score * exp(-días/3)`).

---

## 15. Notas para Claude Code (orden sugerido de implementación)

1. `models.py` + `feeds.yaml` + `pyproject.toml` (deps + `uv sync`).
2. `sources.py` con las 6 fuentes (probar cada una aislada con un script rápido antes de seguir).
3. `dedup.py` (solo exacto inicialmente) + `memory.py` + sus tests.
4. `render.py` + su test de snapshot (permite ver el formato sin LLM).
5. `digest.py` orquestando hasta acá (Top N heurístico, sin LLM).
6. `ranker.py` con LiteLLM, single-pass primero con mock, luego con la key real.
7. Two-pass ranking en `ranker.py` (cuando single-pass funciona).
8. Dedup semántico en `dedup.py` con embeddings mockeados, luego reales.
9. `email.py` (opcional, último).
10. `weekly.yml` y README al final, cuando el local ya funciona.

Recomendado: tener el "Top N heurístico" funcionando (pasos 1–5) antes de tocar LLM. Así hay valor entregable temprano y se valida el pipeline de datos sin gastar cuota de modelo.

---

## Changelog v1 → v2

**Agregado (P0):**
- Fuentes: arXiv (cs.AI/cs.CL/cs.LG vía RSS), Hugging Face Daily Papers (API), GitHub releases (API).
- Memoria entre semanas (`digests/state/seen_urls.json`) — evita repetir items de las últimas N semanas.
- Ranking en dos pasadas (`ranking_strategy: auto/single/two-pass`) — mejora calidad cuando hay >50 candidatos.

**Agregado (P1):**
- Dedup semántico vía embeddings de Gemini (`semantic_dedup`).
- Perfil estructurado en YAML (`priorities`, `anti_priorities`, `exclude_keywords`, `extra_notes`).
- Email delivery opcional via Resend (`delivery.email`).

**Movido a roadmap (nuevo):** feedback loop, Bluesky, z-score por fuente, Slack, diversidad forzada por categoría, freshness decay.

**Sin cambios:** LiteLLM como abstracción, GitHub Actions, fallback heurístico ante fallo LLM, validación de URLs contra candidatos, estructura modular, YAML config, integración de newsletters via Kill the Newsletter.
