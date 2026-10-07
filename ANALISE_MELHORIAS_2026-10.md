# youtube-playlist-bot — Análise e melhorias (out/2026)

Versão analisada: 2.0.0 (último commit em 16/jun/2026). Versão entregue: 2.1.0.
Resultado: 84 testes passando (eram 35), cobertura de 97%, ruff sem avisos.

## Pontos fortes

- Separação de módulos boa (`config`, `auth`, `state`, `youtube_api`, `processor`), com docstrings e type hints.
- Deduplicação dupla (state + conteúdo das playlists) e limite de vídeos por canal para poupar quota.
- Cache de vídeos curtidos com TTL e `save_state` dentro de `finally`.
- Secrets via GitHub Actions, `bootstrap.py` para gerar o token e `.gitignore` cobrindo credenciais.

## Problemas encontrados

### 1. Token do GitHub em texto puro no `.git/config` (Segurança, prioridade Alta)
- **Problema:** a URL do remote `origin` contém um Personal Access Token (`https://douglascf12:ghp_…@github.com/...`).
- **Impacto:** qualquer pessoa ou ferramenta com acesso à pasta (o backup do iCloud, por exemplo) pode fazer push e ler seus repositórios.
- **Solução:** revogar o token em github.com/settings/tokens e trocar o remote:
  `git remote set-url origin https://github.com/douglascf12/youtube-playlist-bot.git` (a autenticação fica com o Keychain ou com `gh auth login`).

### 2. O `state.json` nunca era atualizado depois do primeiro save (Bug / DevOps, prioridade Alta)
- **Problema:** `actions/cache` usava uma chave fixa (`youtube-state-refs/heads/main`). Um cache do GitHub é imutável: quando a chave já existe, o passo de salvar é ignorado.
- **Impacto:** toda execução partia do mesmo state antigo. O cache de curtidos nunca persistia (a API era chamada a cada run) e o histórico de vídeos processados ficava congelado. A deduplicação pelas playlists escondia o problema.
- **Solução:** separar `actions/cache/restore` e `actions/cache/save`, com a chave `youtube-state-${{ github.run_id }}` e `restore-keys: youtube-state-`. O save roda com `if: always()`.

### 3. Corte de 300 IDs removia itens aleatórios (Bug, prioridade Alta)
- **Problema:** `set(list(processed)[-300:])`. Um set não tem ordem, então "os últimos 300" eram 300 itens quaisquer.
- **Impacto:** vídeos recentes podiam sair do state e ser reavaliados, gastando quota.
- **Solução:** guardar `processed` como lista na ordem de inserção e cortar como fila (FIFO). Com no máximo 300 itens, a busca em O(n) é irrelevante perto do custo de uma chamada HTTP. Isso também elimina a conversão entre lista e set.

### 4. Todo 403 era tratado como falta de quota, e a falta de quota não parava os outros canais (Erros, prioridade Alta)
- **Problema:** o YouTube devolve 403 tanto para `quotaExceeded` quanto para `forbidden` (playlist sem permissão). Além disso, depois de um 403 o bot seguia para os próximos canais, que também falhavam.
- **Solução:** `is_quota_error()` lê o campo `reason` do corpo do erro. Quando é quota, uma `QuotaExceededError` interrompe a execução inteira de forma limpa.

### 5. Um canal com erro derrubava a execução inteira (Resiliência, prioridade Alta)
- **Problema:** um `RuntimeError` (canal removido) ou um `HttpError 500` em qualquer canal abortava o loop.
- **Solução:** cada canal roda isolado em `run()`. A falha é registrada, os demais canais seguem e o processo sai com exit code 1, então o run fica vermelho e o GitHub te notifica.

### 6. Chamada de API desnecessária por canal (Performance, prioridade Média)
- **Problema:** `channels.list` era chamado em todo run só para descobrir a playlist de uploads.
- **Solução:** a playlist de uploads de `UCxxxx` é sempre `UUxxxx`, então o ID é derivado sem chamar a API.

### 7. Data de publicação errada (Regra de negócio, prioridade Média)
- **Problema:** `snippet.publishedAt` em `playlistItems` é a data em que o item entrou na playlist.
- **Solução:** usar `contentDetails.videoPublishedAt`, com fallback para o campo antigo.

### 8. Sem retry para erros transitórios (Resiliência, prioridade Média)
- **Solução:** `execute(num_retries=3)`, que faz backoff exponencial em respostas 5xx e 429.

### 9. Validação de configuração fraca (Segurança / Validação, prioridade Média)
- **Problema:** um `YT_CHANNEL_PLAYLIST_MAP` malformado estourava com `JSONDecodeError` no meio do fluxo. Um `GOOGLE_TOKEN_JSON` inválido podia vazar trechos do secret no traceback.
- **Solução:** `load_channel_playlist_map()` valida o JSON e o formato dos IDs, e as mensagens de erro nunca incluem o conteúdo do secret. Um `RefreshError` (`invalid_grant`) agora vira uma mensagem que diz o que fazer.

### 10. Escrita do state não atômica (Confiabilidade, prioridade Média)
- **Solução:** gravar em um arquivo temporário e usar `os.replace()`. Um state corrompido é logado e o bot recomeça do zero, sem quebrar.

### 11. Pipeline (DevOps, prioridade Média)
- Agora o bot roda a cada 3 horas, às `17 */3 * * *`. O minuto 17 evita o pico do minuto 0, quando o GitHub costuma atrasar execuções agendadas.
- `concurrency` impede duas execuções simultâneas, e `timeout-minutes` evita jobs travados.
- Os testes saíram do job agendado (rodavam 24 vezes por dia sem necessidade) e foram para um `ci.yml` disparado em push e PR, junto com o ruff.
- Foram adicionados cache de pip, `permissions: contents: read` e o `dependabot.yml`.
- Os parâmetros podem ser sobrescritos por *Variables* do repositório, sem mexer no código.

### 12. Observabilidade (prioridade Média)
- Um resumo em Markdown é publicado na aba Summary de cada run (adicionados, ignorados por motivo e erros por canal).
- `_last_run` é gravado no state, e os logs usam formatação lazy (`%s`) com timestamps realmente em UTC.

### 13. Problemas de ambiente (prioridade Média)
- O repositório está em `Documents`, sincronizado pelo iCloud. Os arquivos ficam só na nuvem, e há `HEAD.lock`, `maintenance.lock` e `tmp_obj_*` órfãos dentro do `.git`. Isso faz o git falhar com "File exists".
- **Solução:** mover o projeto para fora do iCloud (por exemplo, `~/dev/`).
- O GitHub desativa workflows agendados de repositórios públicos depois de 60 dias sem commits. Como o último commit foi em 16/jun, vale conferir se o agendamento está ativo.
- Se o app OAuth estiver em modo Testing, o refresh token expira em 7 dias. Para evitar isso, publique o app ("In production").

## Roadmap

**Quick wins (até 1 dia):** já entregues
- Itens 2 a 12.
- Item 1: revogar o token (ação sua).

**Curto prazo (1 semana)**
- Filtrar Shorts e lives (`videos.list` com `contentDetails.duration` e `liveBroadcastContent`).
- Alerta por e-mail ou Telegram quando houver falha de auth ou quota.

**Médio prazo (1 mês)**
- Guardar o state fora do cache do Actions, que pode ser evictado após 7 dias sem acesso. Opções: um Gist privado ou uma branch `state` no próprio repo.
- Criar uma interface `YouTubeClient` (Protocol) para tirar o `Any` dos tipos e facilitar fakes nos testes.

**Longo prazo**
- Usar o feed RSS dos canais (`/feeds/videos.xml?channel_id=`), que não gasta quota, para detectar vídeos novos. A API ficaria só para inserir.
- Configuração em YAML versionado (canal, playlist e filtros por canal).

## Plano de refatoração (ordem aplicada)

1. `config.py`: validação e parâmetros por env var
2. `state.py`: lista ordenada e escrita atômica
3. `youtube_api.py`: `_execute` com retry, detecção de quota e derivação UC→UU
4. `auth.py`: erros acionáveis e sem vazamento do secret
5. `processor.py`: `ChannelResult`/`RunReport`, isolamento por canal e resumo
6. `watcher.py`: exit code e logs em UTC
7. Workflows (`youtube.yml`, `ci.yml`, `dependabot.yml`)
8. Testes e README
