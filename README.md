# youtube-playlist-bot

Bot em Python que monitora canais do YouTube e adiciona automaticamente os vídeos mais recentes em playlists públicas configuradas. Roda via GitHub Actions a cada hora.

## Como funciona

1. Lê um mapeamento `canal → playlist` de variáveis de ambiente
2. Para cada canal, busca os uploads mais recentes
3. Filtra vídeos já processados, muito antigos, já presentes na playlist, ou curtidos pelo usuário
4. Insere os elegíveis na playlist correspondente
5. Persiste o estado entre execuções via `state.json` (cacheado no GitHub Actions)

## Configuração

### Secrets do GitHub Actions (obrigatórios)

Acesse `Settings → Secrets and variables → Actions` e crie:

| Secret | Descrição |
|--------|-----------|
| `GOOGLE_TOKEN_JSON` | Credenciais OAuth2 serializadas em JSON (veja abaixo como gerar) |
| `YT_CHANNEL_PLAYLIST_MAP` | JSON mapeando channel_id → playlist_id |

**Exemplo de `YT_CHANNEL_PLAYLIST_MAP`:**
```json
{
  "UCxxxxxxxxxxxxxxxxxxxxxx": "PLyyyyyyyyyyyyyyyyyyyyyy",
  "UCaaaaaaaaaaaaaaaaaaaaa": "PLbbbbbbbbbbbbbbbbbbbbbb"
}
```

### Gerando o `GOOGLE_TOKEN_JSON`

1. No [Google Cloud Console](https://console.cloud.google.com/), crie um projeto e ative a **YouTube Data API v3**
2. Crie credenciais OAuth2 do tipo **Desktop app** e baixe o `client_secret.json`
3. Execute o fluxo de autorização localmente:

```bash
pip install google-auth-oauthlib
python - <<'EOF'
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube"]
flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)
print(creds.to_json())
EOF
```

4. Copie o JSON impresso e cole no Secret `GOOGLE_TOKEN_JSON`

## Execução local

```bash
# Instalar dependências
pip install -r requirements.txt

# Configurar variáveis de ambiente
export GOOGLE_TOKEN_JSON='{"token": "...", "refresh_token": "...", ...}'
export YT_CHANNEL_PLAYLIST_MAP='{"UCxxxxxx": "PLyyyyyy"}'

# Rodar
python watcher.py
```

## Desenvolvimento

```bash
# Instalar dependências de desenvolvimento
pip install -r requirements-dev.txt

# Rodar os testes
pytest tests/ -v

# Rodar com cobertura
pytest tests/ --cov=youtube_bot --cov-report=term-missing
```

## Estrutura do projeto

```
youtube-playlist-bot/
├── youtube_bot/
│   ├── __init__.py      # versão do pacote
│   ├── config.py        # constantes e leitura de env vars
│   ├── auth.py          # autenticação OAuth2
│   ├── state.py         # persistência do estado entre execuções
│   ├── youtube_api.py   # chamadas à YouTube Data API v3
│   └── processor.py     # lógica de negócio e entrypoint
├── tests/
│   ├── conftest.py      # fixtures compartilhadas
│   ├── test_state.py    # testes do módulo state
│   └── test_processor.py # testes do módulo processor
├── watcher.py           # entrypoint (chamado pelo GitHub Actions)
├── requirements.txt     # dependências de produção
├── requirements-dev.txt # dependências de desenvolvimento
└── .github/
    └── workflows/
        └── youtube.yml  # pipeline CI/CD (testes + execução horária)
```

## Parâmetros configuráveis

Edite `youtube_bot/config.py` para ajustar:

| Constante | Padrão | Descrição |
|-----------|--------|-----------|
| `MAX_VIDEOS_PER_CHANNEL` | `2` | Máximo de vídeos inseridos por canal por execução |
| `MAX_VIDEO_AGE_DAYS` | `150` | Ignora vídeos publicados há mais de N dias |
| `LIKED_CACHE_TTL_HOURS` | `6` | Tempo de vida do cache de vídeos curtidos |
