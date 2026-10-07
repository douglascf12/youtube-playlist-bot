# youtube-playlist-bot

Bot em Python que monitora canais do YouTube e adiciona automaticamente os vídeos mais recentes em playlists públicas configuradas. Roda via GitHub Actions **a cada 3 horas** (00:17, 03:17, 06:17 … UTC — 21:17, 00:17, 03:17 … em Brasília).

## Como funciona

1. Lê um mapeamento `canal → playlist` de variáveis de ambiente
2. Para cada canal, busca os uploads mais recentes
3. Filtra vídeos já processados, muito antigos, já presentes na playlist, ou curtidos pelo usuário
4. Insere os elegíveis na playlist correspondente
5. Persiste o estado entre execuções via `state.json` (cache do GitHub Actions, regravado a cada execução)
6. Publica um resumo da execução na aba **Summary** do run no GitHub Actions

Erros são isolados por canal: um canal com problema não impede os outros (o run termina em vermelho para você ser notificado). Se a quota da API acabar, o bot para de forma limpa e retoma na próxima execução.

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

> ⚠️ Se o app OAuth estiver em modo **Testing** no Google Cloud, o refresh token expira em **7 dias** e o bot passa a falhar com `invalid_grant`. Em *OAuth consent screen*, clique em **Publish app** (não precisa de verificação para uso próprio) e gere o token de novo.

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

# Lint
ruff check .

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
│   ├── config.py        # parâmetros (com override por env var) e validação dos secrets
│   ├── auth.py          # autenticação OAuth2
│   ├── state.py         # persistência atômica do estado entre execuções
│   ├── youtube_api.py   # chamadas à YouTube Data API v3 (retry + detecção de quota)
│   └── processor.py     # regras de negócio, orquestração e resumo da execução
├── tests/               # testes unitários (pytest)
├── watcher.py           # entrypoint (chamado pelo GitHub Actions)
├── bootstrap.py         # gera o GOOGLE_TOKEN_JSON localmente
├── pyproject.toml       # configuração do ruff e do pytest
├── requirements.txt     # dependências de produção
├── requirements-dev.txt # dependências de desenvolvimento
└── .github/
    ├── dependabot.yml   # atualização mensal de dependências
    └── workflows/
        ├── youtube.yml  # execução do bot a cada 3 horas
        └── ci.yml       # lint + testes em push/PR
```

## Parâmetros configuráveis

Todos têm valor padrão e podem ser alterados **sem mexer no código**, criando uma *Variable* em `Settings → Secrets and variables → Actions → Variables` (ou exportando a env var localmente):

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `MAX_VIDEOS_PER_CHANNEL` | `2` | Máximo de vídeos inseridos por canal por execução |
| `MAX_VIDEO_AGE_DAYS` | `150` | Ignora vídeos publicados há mais de N dias |
| `LIKED_CACHE_TTL_HOURS` | `6` | Tempo de vida do cache de vídeos curtidos |

## Quota da API

A quota padrão é de 10.000 unidades/dia. Cada inserção custa 50 unidades e cada leitura, 1. Com 8 execuções por dia, o limite prático é de ~190 inserções/dia — folgado para algumas dezenas de canais com `MAX_VIDEOS_PER_CHANNEL=2`.

## Agendamento

O GitHub **desativa workflows agendados em repositórios públicos após 60 dias sem commits**. Se o bot parar de rodar, vá em `Actions → YouTube -> Playlist (bot) → Enable workflow`.
