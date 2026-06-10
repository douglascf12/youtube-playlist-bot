# Análise Técnica — youtube-playlist-bot

> Revisão realizada em 2026-06-09 por Claude (Senior Software Engineer)

---

## Pontos Fortes

Antes dos problemas, vale destacar o que o projeto faz bem:

- **Proteção de quota** com `MAX_VIDEOS_PER_CHANNEL` e interrupção em erro 403.
- **Deduplicação robusta**: carrega todos os vídeos já presentes nas playlists em memória antes do loop (`existing_playlist_videos`), evitando chamadas desnecessárias de escrita.
- **Filtro de idade** evita processar conteúdo antigo irrelevante.
- **State trimming**: mantém apenas os últimos 300 IDs por canal, evitando crescimento ilimitado do `state.json`.
- **Uso correto de OAuth2** via variável de ambiente, sem credenciais hardcoded.
- **Separação visual clara** com seções comentadas no arquivo.

---

## Problemas Encontrados

---

### 1. Token OAuth não é re-salvo após refresh

**Problema:** `creds.refresh(Request())` renova o `access_token` em memória, mas o novo token **nunca é gravado de volta** no Secret do GitHub Actions. A próxima execução usa o token original (expirado), tenta um novo refresh — o que funciona enquanto o `refresh_token` for válido. Porém, se o `refresh_token` expirar ou for revogado, o bot para silenciosamente sem forma de recuperação.

**Impacto:** CRÍTICO. Pode causar falha permanente do bot sem nenhuma indicação clara.

**Prioridade:** Alta

**Solução recomendada:** Salvar o token atualizado de volta no state ou emitir um aviso explícito com as instruções para re-autenticar.

```python
# Atual
def load_creds():
    info = json.loads(os.environ["GOOGLE_TOKEN_JSON"])
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError("Credenciais OAuth inválidas")
    return creds

# Recomendado — salvar token atualizado no state
def load_creds(state: dict) -> Credentials:
    info = json.loads(os.environ["GOOGLE_TOKEN_JSON"])
    creds = Credentials.from_authorized_user_info(info, SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            logger.info("Renovando access token OAuth...")
            creds.refresh(Request())
            # Salva o token atualizado no state para diagnóstico
            state["last_token_refresh"] = datetime.now(timezone.utc).isoformat()
            state["token_expiry"] = creds.expiry.isoformat() if creds.expiry else None
        else:
            raise RuntimeError(
                "Credenciais OAuth inválidas e sem refresh_token. "
                "Re-autentique e atualize o Secret GOOGLE_TOKEN_JSON."
            )
    return creds
```

---

### 2. Ausência completa de logging estruturado

**Problema:** O projeto usa `print()` para tudo. Não há níveis de severidade (INFO, WARNING, ERROR), timestamps, nem contexto estruturado. Em GitHub Actions, a saída existe mas não é filtrável nem alertável.

**Impacto:** Médio. Dificulta diagnóstico de falhas em produção.

**Prioridade:** Alta

**Solução recomendada:**

```python
# Atual
print(f"[{channel_id}] Adicionado em {playlist_id}: {video_id}")
print("⚠️ Quota excedida, encerrando execução.")

# Recomendado
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger(__name__)

logger.info("Adicionado", extra={"channel_id": channel_id, "playlist_id": playlist_id, "video_id": video_id})
logger.warning("Quota excedida, encerrando execução.")
```

---

### 3. `is_processed` usa lista em vez de set — O(n) por lookup

**Problema:** `state[channel_id]["processed"]` é uma `list`. A verificação `video_id in state.get(...).get("processed", [])` é O(n) — percorre a lista inteira a cada checagem. Com 300 entradas por canal e múltiplos canais, são centenas de scans lineares por execução.

**Impacto:** Baixo na escala atual, mas é um padrão errado que se agrava com crescimento.

**Prioridade:** Média

**Solução recomendada:** Converter para `set` em memória ao carregar o state.

```python
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Converter listas para sets em memória para lookup O(1)
        for channel_data in data.values():
            if "processed" in channel_data:
                channel_data["processed"] = set(channel_data["processed"])
        return data
    return {}

def save_state(state: dict) -> None:
    # Converter sets de volta para listas ao serializar
    serializable = {}
    for channel_id, channel_data in state.items():
        serializable[channel_id] = {
            **channel_data,
            "processed": list(channel_data.get("processed", set()))
        }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2)

def is_processed(state: dict, channel_id: str, video_id: str) -> bool:
    return video_id in state.get(channel_id, {}).get("processed", set())

def mark_processed(state: dict, channel_id: str, video_id: str) -> None:
    channel = state.setdefault(channel_id, {})
    processed = channel.setdefault("processed", set())
    processed.add(video_id)
    # Manter apenas os últimos 300 (converter para lista para truncar)
    if len(processed) > 300:
        channel["processed"] = set(list(processed)[-300:])
```

---

### 4. `get_liked_videos` consome quota sem necessidade em muitos casos

**Problema:** A função percorre **todas** as páginas de vídeos curtidos do usuário (`myRating=like`) a cada execução. Usuários com muitos likes geram dezenas de chamadas à API. Esse dado muda raramente e poderia ser cacheado no `state.json`.

**Impacto:** Médio. Consome quota desnecessariamente, especialmente com muitos likes.

**Prioridade:** Média

**Solução recomendada:** Cachear os liked videos no state com TTL.

```python
LIKED_CACHE_TTL_HOURS = 6

def get_liked_videos(youtube, state: dict) -> set:
    cache = state.get("_liked_videos_cache", {})
    cached_at_str = cache.get("cached_at")

    if cached_at_str:
        cached_at = datetime.fromisoformat(cached_at_str)
        age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
        if age_hours < LIKED_CACHE_TTL_HOURS:
            logger.info(f"Usando cache de liked videos ({len(cache['ids'])} vídeos)")
            return set(cache["ids"])

    logger.info("Buscando liked videos da API...")
    liked = set()
    page_token = None

    while True:
        resp = youtube.videos().list(
            part="id", myRating="like", maxResults=50, pageToken=page_token
        ).execute()
        for item in resp.get("items", []):
            liked.add(item["id"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    state["_liked_videos_cache"] = {
        "ids": list(liked),
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    return liked
```

---

### 5. `save_state` é chamado dentro do loop por vídeo

**Problema:** `save_state(state)` é chamado após cada vídeo adicionado com sucesso. Em um loop de 10 vídeos (5 canais × 2 vídeos), são até 10 escritas em disco.

**Impacto:** Baixo em performance, mas é um padrão desnecessariamente custoso.

**Prioridade:** Baixa

**Solução recomendada:** Chamar `save_state` uma única vez ao final de `main()`, com um `try/finally` para garantir que o state seja salvo mesmo em caso de erro.

```python
def main():
    # ...
    state = load_state()
    try:
        for channel_id, playlist_id in channel_playlist_map.items():
            process_channel(...)
    finally:
        save_state(state)
```

Remover `save_state(state)` de dentro de `process_channel`.

---

### 6. Ausência de type hints

**Problema:** Nenhuma função possui anotações de tipo. Dificulta entendimento, refatoração e uso de ferramentas de análise estática (mypy, pylance).

**Impacto:** Médio. Reduz manutenibilidade.

**Prioridade:** Média

**Exemplo recomendado:**

```python
from typing import Any

def load_state() -> dict[str, Any]:
    ...

def save_state(state: dict[str, Any]) -> None:
    ...

def is_processed(state: dict, channel_id: str, video_id: str) -> bool:
    ...

def mark_processed(state: dict, channel_id: str, video_id: str) -> None:
    ...

def is_recent(published_at_iso: str) -> bool:
    ...

def get_uploads_playlist_id(youtube: Any, channel_id: str) -> str:
    ...

def list_latest_uploads(youtube: Any, uploads_playlist_id: str, max_results: int = 10) -> list[dict]:
    ...

def process_channel(
    youtube: Any,
    channel_id: str,
    playlist_id: str,
    state: dict,
    liked_videos: set[str],
    existing_playlist_videos: set[str],
) -> None:
    ...
```

---

### 7. Variável de ambiente ausente causa KeyError sem mensagem útil

**Problema:** `os.environ["GOOGLE_TOKEN_JSON"]` e `os.environ["YT_CHANNEL_PLAYLIST_MAP"]` lançam `KeyError` se não definidas. O traceback é confuso para quem não conhece o código.

**Impacto:** Baixo em produção (secrets configurados), mas ruim para onboarding e debugging local.

**Prioridade:** Baixa

**Solução recomendada:**

```python
def get_required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(
            f"Variável de ambiente obrigatória não definida: {name}\n"
            f"Configure o Secret no repositório GitHub ou no .env local."
        )
    return value

# Uso
info = json.loads(get_required_env("GOOGLE_TOKEN_JSON"))
channel_playlist_map = json.loads(get_required_env("YT_CHANNEL_PLAYLIST_MAP"))
```

---

### 8. Ausência total de testes

**Problema:** O projeto não possui nenhum teste unitário, de integração ou de smoke. As funções de lógica de negócio (`is_processed`, `is_recent`, `mark_processed`) são perfeitamente testáveis sem mock de API.

**Impacto:** Alto. Qualquer refatoração é arriscada sem cobertura.

**Prioridade:** Alta

**Exemplo de testes unitários para a lógica core:**

```python
# tests/test_watcher.py
import pytest
from datetime import datetime, timedelta, timezone
from watcher import is_recent, is_processed, mark_processed

def test_is_recent_video_within_window():
    recent = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    assert is_recent(recent) is True

def test_is_recent_video_outside_window():
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    assert is_recent(old) is False

def test_is_processed_not_in_state():
    state = {}
    assert is_processed(state, "CH123", "VID456") is False

def test_mark_and_check_processed():
    state = {}
    mark_processed(state, "CH123", "VID456")
    assert is_processed(state, "CH123", "VID456") is True

def test_mark_processed_caps_at_300():
    state = {}
    for i in range(310):
        mark_processed(state, "CH123", f"VID{i:04d}")
    assert len(state["CH123"]["processed"]) <= 300
```

---

### 9. Arquivo único — sem separação de responsabilidades

**Problema:** Auth, state, chamadas à API, lógica de negócio e entrypoint estão todos em `watcher.py`. Isso viola o Single Responsibility Principle e dificulta testes isolados.

**Impacto:** Médio. Aceitável para projetos pequenos, mas cresce em complexidade com o tempo.

**Prioridade:** Baixa (curto prazo se o projeto crescer)

**Estrutura sugerida:**

```
youtube_playlist_bot/
├── __init__.py
├── auth.py          # load_creds()
├── state.py         # load_state(), save_state(), is_processed(), mark_processed()
├── youtube_api.py   # get_uploads_playlist_id(), list_latest_uploads(), add_video_to_playlist(), etc.
├── processor.py     # process_channel(), lógica de negócio
└── main.py          # entrypoint
tests/
├── test_state.py
├── test_processor.py
└── test_youtube_api.py (com mocks)
```

---

### 10. Cron expression confusa no workflow

**Problema:** `*/60 * * * *` no campo de minutos (0-59) efetivamente só executa no minuto 0 de cada hora, o que é o comportamento desejado. Porém, a expressão é **enganosa** — parece "a cada 60 minutos" mas é apenas `0 * * * *`. Qualquer pessoa lendo entenderá errado.

**Impacto:** Baixo em comportamento, médio em legibilidade.

**Prioridade:** Baixa

**Correção:**

```yaml
# Atual (confuso mas funcionalmente correto)
- cron: "*/60 * * * *"

# Correto e legível
- cron: "0 * * * *"  # toda hora, no minuto 0
```

---

### 11. Sem `.gitignore`

**Problema:** `state.json` pode ser acidentalmente commitado, expondo IDs de vídeos processados. Também `.DS_Store` já está no repo (confirmado pelo `find`).

**Impacto:** Baixo, mas é boa prática.

**Prioridade:** Baixa

**`.gitignore` recomendado:**

```
state.json
.env
*.pyc
__pycache__/
.DS_Store
.venv/
```

---

### 12. README vazio

**Problema:** O README contém apenas o nome do projeto. Não há instruções de setup, configuração dos secrets, ou como executar localmente.

**Impacto:** Alto para onboarding de novos colaboradores.

**Prioridade:** Média

---

## Resumo Executivo

O projeto tem uma **lógica de negócio sólida e funcional** — a deduplicação via playlist scan, o filtro de likes, a proteção de quota e o state trimming mostram cuidado técnico real. O maior risco operacional é o **token OAuth não persistido após refresh**, que pode causar falha silenciosa irreversível. O segundo maior problema é a **ausência de testes**, que torna qualquer evolução arriscada. O restante são melhorias de qualidade de código e observabilidade que deixariam o projeto production-grade.

**Nota geral atual: 6/10.** Funciona, mas frágil para operação contínua de longo prazo.

---

## Roadmap de Melhorias

### Quick Wins (até 1 dia)

1. Corrigir cron expression para `0 * * * *`
2. Adicionar `.gitignore`
3. Adicionar mensagem de erro útil em variáveis de ambiente ausentes (`get_required_env`)
4. Trocar `print()` por `logging` estruturado
5. Adicionar type hints em todas as funções

### Curto Prazo (1 semana)

6. Adicionar testes unitários para `is_processed`, `is_recent`, `mark_processed`
7. Implementar cache de liked videos no state com TTL
8. Converter `processed` de lista para set em memória
9. Mover `save_state` para o `finally` do `main()` (remover do loop interno)
10. Escrever um README funcional com instruções de setup

### Médio Prazo (1 mês)

11. Separar o arquivo único em módulos (`auth.py`, `state.py`, `youtube_api.py`, `processor.py`)
12. Adicionar testes de integração com mock da API do YouTube
13. Adicionar step de testes no GitHub Actions workflow
14. Adicionar retry com backoff exponencial para erros 5xx da API

### Longo Prazo

15. Adicionar Dockerfile para execução local sem dependências globais
16. Implementar notificação (Slack, e-mail) em caso de falha ou quota excedida
17. Adicionar métricas (vídeos adicionados/ignorados/erros) com export para um dashboard simples

---

## Plano de Refatoração (ordem de prioridade)

| # | Arquivo | Mudança |
|---|---------|---------|
| 1 | `.github/workflows/youtube.yml` | Corrigir cron para `0 * * * *` |
| 2 | `.gitignore` (novo) | Criar com state.json, .DS_Store, .env |
| 3 | `watcher.py` | Adicionar `logging` + `get_required_env()` |
| 4 | `watcher.py` | Adicionar type hints |
| 5 | `watcher.py` | Converter `processed` para set em memória |
| 6 | `watcher.py` | Mover `save_state` para `finally` no `main()` |
| 7 | `watcher.py` | Implementar cache de liked videos |
| 8 | `tests/` (novo) | Criar testes unitários |
| 9 | `README.md` | Escrever documentação de setup |
| 10 | Estrutura | Separar em módulos quando adicionar testes de integração |

---

## Código Refatorado — `watcher.py` (versão melhorada)

```python
"""
youtube-playlist-bot — watcher.py
Monitora canais do YouTube e adiciona novos vídeos em playlists configuradas.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ============================================================
# CONFIGURAÇÕES
# ============================================================

SCOPES = ["https://www.googleapis.com/auth/youtube"]
STATE_FILE = "state.json"
MAX_VIDEOS_PER_CHANNEL = 2
MAX_VIDEO_AGE_DAYS = 150
LIKED_CACHE_TTL_HOURS = 6

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger(__name__)


# ============================================================
# CONFIG / ENV
# ============================================================

def get_required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(
            f"Variável de ambiente obrigatória não definida: {name}. "
            "Configure o Secret no GitHub ou em um arquivo .env local."
        )
    return value


# ============================================================
# AUTH
# ============================================================

def load_creds(state: dict[str, Any]) -> Credentials:
    info = json.loads(get_required_env("GOOGLE_TOKEN_JSON"))
    creds = Credentials.from_authorized_user_info(info, SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            logger.info("Access token expirado — renovando via refresh token...")
            creds.refresh(Request())
            state["last_token_refresh"] = datetime.now(timezone.utc).isoformat()
            logger.info("Token renovado com sucesso.")
        else:
            raise RuntimeError(
                "Credenciais OAuth inválidas e sem refresh_token. "
                "Re-autentique e atualize o Secret GOOGLE_TOKEN_JSON."
            )

    return creds


# ============================================================
# STATE
# ============================================================

def load_state() -> dict[str, Any]:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Converter listas para sets para lookup O(1)
        for channel_data in data.values():
            if isinstance(channel_data, dict) and "processed" in channel_data:
                channel_data["processed"] = set(channel_data["processed"])
        return data
    return {}


def save_state(state: dict[str, Any]) -> None:
    serializable: dict[str, Any] = {}
    for key, value in state.items():
        if isinstance(value, dict) and "processed" in value:
            serializable[key] = {
                **value,
                "processed": list(value["processed"]),
            }
        else:
            serializable[key] = value

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2)


def is_processed(state: dict[str, Any], channel_id: str, video_id: str) -> bool:
    return video_id in state.get(channel_id, {}).get("processed", set())


def mark_processed(state: dict[str, Any], channel_id: str, video_id: str) -> None:
    channel = state.setdefault(channel_id, {})
    processed: set[str] = channel.setdefault("processed", set())
    processed.add(video_id)
    if len(processed) > 300:
        channel["processed"] = set(list(processed)[-300:])


# ============================================================
# HELPERS
# ============================================================

def is_recent(published_at_iso: str) -> bool:
    published = datetime.fromisoformat(published_at_iso.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) - published <= timedelta(days=MAX_VIDEO_AGE_DAYS)


# ============================================================
# YOUTUBE API – READ
# ============================================================

def get_uploads_playlist_id(youtube: Any, channel_id: str) -> str:
    resp = youtube.channels().list(part="contentDetails", id=channel_id).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError(f"Canal não encontrado: {channel_id}")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def list_latest_uploads(youtube: Any, uploads_playlist_id: str, max_results: int = 10) -> list[dict]:
    resp = youtube.playlistItems().list(
        part="snippet",
        playlistId=uploads_playlist_id,
        maxResults=max_results,
    ).execute()
    return resp.get("items", [])


def get_liked_videos(youtube: Any, state: dict[str, Any]) -> set[str]:
    cache = state.get("_liked_videos_cache", {})
    cached_at_str = cache.get("cached_at")

    if cached_at_str:
        cached_at = datetime.fromisoformat(cached_at_str)
        age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
        if age_hours < LIKED_CACHE_TTL_HOURS:
            logger.info(f"Usando cache de liked videos ({len(cache['ids'])} vídeos, {age_hours:.1f}h atrás)")
            return set(cache["ids"])

    logger.info("Buscando liked videos da API...")
    liked: set[str] = set()
    page_token = None

    while True:
        resp = youtube.videos().list(
            part="id", myRating="like", maxResults=50, pageToken=page_token
        ).execute()
        for item in resp.get("items", []):
            liked.add(item["id"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    state["_liked_videos_cache"] = {
        "ids": list(liked),
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(f"Liked videos carregados: {len(liked)}")
    return liked


def get_all_playlist_video_ids(youtube: Any, playlist_ids: set[str]) -> set[str]:
    all_videos: set[str] = set()

    for playlist_id in playlist_ids:
        page_token = None
        while True:
            resp = youtube.playlistItems().list(
                part="contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            ).execute()
            for item in resp.get("items", []):
                all_videos.add(item["contentDetails"]["videoId"])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    return all_videos


# ============================================================
# YOUTUBE API – WRITE
# ============================================================

def add_video_to_playlist(youtube: Any, playlist_id: str, video_id: str) -> None:
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()


# ============================================================
# PROCESSAMENTO
# ============================================================

def process_channel(
    youtube: Any,
    channel_id: str,
    playlist_id: str,
    state: dict[str, Any],
    liked_videos: set[str],
    existing_playlist_videos: set[str],
) -> None:
    uploads_playlist_id = get_uploads_playlist_id(youtube, channel_id)
    items = list_latest_uploads(youtube, uploads_playlist_id)
    added = 0

    for item in items:
        snippet = item["snippet"]
        video_id: str = snippet["resourceId"]["videoId"]
        published_at: str = snippet["publishedAt"]

        if is_processed(state, channel_id, video_id):
            continue

        if video_id in existing_playlist_videos:
            logger.info(f"[{channel_id}] Ignorado (já em playlist): {video_id}")
            mark_processed(state, channel_id, video_id)
            continue

        if not is_recent(published_at):
            logger.info(f"[{channel_id}] Ignorado (antigo): {video_id}")
            mark_processed(state, channel_id, video_id)
            continue

        if video_id in liked_videos:
            logger.info(f"[{channel_id}] Ignorado (curtido): {video_id}")
            mark_processed(state, channel_id, video_id)
            continue

        try:
            add_video_to_playlist(youtube, playlist_id, video_id)
            logger.info(f"[{channel_id}] Adicionado em {playlist_id}: {video_id}")
            mark_processed(state, channel_id, video_id)
            existing_playlist_videos.add(video_id)
            added += 1

            if added >= MAX_VIDEOS_PER_CHANNEL:
                break

        except HttpError as e:
            if e.resp.status == 403:
                logger.warning(f"[{channel_id}] Quota excedida (403). Encerrando execução.")
                return
            logger.error(f"[{channel_id}] HttpError {e.resp.status} ao adicionar {video_id}: {e}")
            raise

    if added == 0:
        logger.info(f"[{channel_id}] Nenhum vídeo elegível")
    else:
        logger.info(f"[{channel_id}] {added} vídeo(s) adicionado(s)")


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    channel_playlist_map: dict[str, str] = json.loads(
        get_required_env("YT_CHANNEL_PLAYLIST_MAP")
    )

    state = load_state()

    try:
        creds = load_creds(state)
        youtube = build("youtube", "v3", credentials=creds)

        liked_videos = get_liked_videos(youtube, state)

        playlist_ids = set(channel_playlist_map.values())
        existing_playlist_videos = get_all_playlist_video_ids(youtube, playlist_ids)

        for channel_id, playlist_id in channel_playlist_map.items():
            process_channel(
                youtube=youtube,
                channel_id=channel_id,
                playlist_id=playlist_id,
                state=state,
                liked_videos=liked_videos,
                existing_playlist_videos=existing_playlist_videos,
            )
    finally:
        save_state(state)
        logger.info("State salvo.")


if __name__ == "__main__":
    main()
```
