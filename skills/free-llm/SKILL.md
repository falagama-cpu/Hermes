---
name: free-llm
description: "Sistema de seleção e troca automática de modelos LLM FREE via cron para qualquer perfil do Hermes Agent. Detecta modelos gratuitos no OpenRouter, NVIDIA NIM, Nous Portal e Cloudflare Workers AI. Atualiza config.yaml com fallback chain, e reinicia o gateway para assumir novos modelos."
---

# Free LLM Selection

Sistema automatizado de seleção e troca de modelos LLM gratuitos entre 4 provedores:
- **OpenRouter** — modelos `:free` ou pricing 0 via catálogo `/models`, validados por probe HTTP
- **NVIDIA NIM** — previews free identificados por probe HTTP (integrate.api.nvidia.com)
- **Nous Portal** — modelos com tag `:free` autenticados por Bearer key (inference-api.nousresearch.com)
- **Cloudflare Workers AI** — modelos free via API (api.cloudflare.com) com Bearer token

## Como funciona

1. O cron roda os scripts em `scripts/` que sondam os provedores via HTTP
2. Filtram modelos com preço zero ou tag `:free`
3. Montam uma fallback chain na ordem: NVIDIA → OpenRouter → Nous → Cloudflare (até `TOP_N`)
4. Atualizam `config.yaml`:
   - `fallback_providers` (OpenRouter + NVIDIA + Nous + Cloudflare, agent-managed auth quando aplicável)
   - `model.default` (NVIDIA only, text-only)
   - `moa.reference_models` (NVIDIA only, text-only — NOT Nous/Cloudflare)
   - `moa.aggregator` (**multimodal MoE preferencial** — sondado em dois passos: (1) NIM: `moonshotai/kimi-k3`, `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, `meta/muse-glimmer-30b`; (2) OpenRouter `:free`: `deepseek-ai/deepseek-v4.1-flash:free`, `z-ai/glm-5-3-flash:free`; fallback = text-only high-priority NVIDIA)
   - **⚠️ Guarda paid-model (`assert_free_models`)**: aborta com exit 2 antes de tocar o config se qualquer modelo selecionado tiver prefixo de provedor pago (`anthropic/`, `openai/`, `google/gemini`, etc.)
5. Reiniciam o gateway para aplicar as mudanças

**⚠️ Regra Crítica:** Modelos Nous e Cloudflare (`provider: custom` + `key_env`) ficam **apenas em `fallback_providers`**, nunca em `moa.reference_models`. O MOA reference_models não resolve `key_env` para providers custom corretamente. Além disso, Cloudflare Workers AI usa endpoint `/ai/run/@cf/...`, que não é OpenAI-compatible; só promover Cloudflare para rota principal/MOA se houver adaptador/provider OpenAI-compatible validado end-to-end.

**Cloudflare Workers AI:** O token atual (`CLOUDFLARE_API_TOKEN`) não tem escopoAccount ID necessário para o endpoint OpenAI-compatível (`api.cloudflare.com/client/v4/accounts/{id}/ai/v1`). O usuário quer Cloudflare no fallback chain quando configurado, mas requer: (1) `CLOUDFLARE_ACCOUNT_ID` no `.env`, (2) token com escopo `AI Gateway:Read`, (3) gateway criado via `gateway.ai.cloudflare.com`.

## Pré-requisitos

- Hermes Agent instalado com pelo menos 1 perfil
- `NVIDIA_API_KEY` no `.env` do perfil (obter em https://integrate.nvidia.com)
- `NOUS_API_KEY` no `.env` do perfil (obter em https://inference-api.nousresearch.com)
- `CLOUDFLARE_API_TOKEN` no `.env` do perfil (obter em https://dash.cloudflare.com/profile/api-tokens)
- `OPENROUTER_API_KEY` no `.env` do perfil (obter em https://openrouter.ai/keys)

## Instalação

```bash
# Clonar o repositório
git clone https://github.com/falagama-cpu/Hermes.git /tmp/hermes-repo

# Instalar no perfil desejado
bash /tmp/hermes-repo/skills/free-llm/scripts/install_free_model_selection.sh <profile>
```

**Nota:** O instalador detecta automaticamente o perfil ativo se nenhum argumento for passado.

O script de instalação:
- Copia os scripts para `~/.hermes/profiles/<profile>/scripts/`
- Cria 3 cron jobs no perfil
- Verifica se as API keys existem no `.env`

## Cron Jobs

| Job | Horário | Script | O que atualiza |
|-----|---------|--------|----------------|
| `update-free-models-14h` | 08:00, 20:00 | `update_free_models.py` | `fallback_providers` |
| `choose-best-free-llm` | 02:00, 14:00 | `choose_best_free_llm.py` | wrapper para o anterior |
| `update-hermes-models` | 09:00, 21:00 | `update_models.py` | `model.default`, `reference_models`, `aggregator` |

## Scripts

### `update_free_models.py`
- Sonda OpenRouter (catálogo `:free`/pricing 0, ranking por `OPENROUTER_PREFERRED` e depois contexto, até 8 probes), NVIDIA (descoberta dinâmica via `/models`; `NVIDIA_PREFERRED` só define prioridade; até 6 probes), Nous (catálogo; `is_free` estrito: sem `pricing` explícito = não free) e Cloudflare Workers AI (endpoint OpenAI-compatível `/ai/v1`, o mesmo que o probe testa)
- Monta fallback chain na ordem atual: NVIDIA primeiro, depois OpenRouter, depois Nous, depois Cloudflare até `TOP_N`
- Atualiza `fallback_providers` no config.yaml (aplicado em runtime pelo gateway, sem restart)
- **NVIDIA dinâmico**: modelo novo entra sozinho pelo `/models`, filtrado por `SKIP_AGENT` (exclui cosmos, detector, speaker, ising, kumo, riva, voicechat, diffusiongemma, transfer, embed, safety). `NVIDIA_PREFERRED` só ordena. O `/models` não informa "Free Endpoint": o probe filtra 402/403. Teto da NVIDIA na cadeia: 3 (`add(nvidia, 3)`). Sintoma de problema: contagem baixa de "previews respondendo" ou `[probe] <modelo>: HTTP <código>` no stderr (HTTP 0 = timeout/rede).
- **Probe**: aceita 200 e 429 (429 = modelo vivo, só limitado; evita a cadeia oscilar e reescrever o config). `_request` preserva o último status transitório.
- **Sem restart do gateway**: o script não reinicia; o gateway relê `fallback_providers` do disco. Backup do config com timestamp (`config.yaml.pre-update-free-models-<ts>`).
- **Troca automática em erro (runtime do Hermes, `agent/error_classifier.py`)**: não exige configuração extra; o gateway percorre `fallback_providers` em ordem. Verificado com `classify_api_error`: **troca imediata** em 400, 401 (rotaciona credencial e cai no fallback), 402, 403, 410 e 422; **429** faz backoff e troca (imediato se o pool de credenciais não puder se recuperar); **408/500/502/503/504/522/529** tentam de novo e trocam após 2 falhas de transporte; **404 sem mensagem reconhecível** não troca (classificado `unknown`), mas `Model ... not found` na mensagem troca.
- **Rodar manualmente**: `HERMES_HOME=$HOME/.hermes/profiles/<profile> python3 <skill-dir>/scripts/update_free_models.py`. O default do script é `~/.hermes/profiles/pesquisa` — sem `HERMES_HOME` ele edita o `config.yaml` do perfil errado.

### `update_models.py`
- Descobre o catálogo NVIDIA via `/models` (filtrado por `SKIP_AGENT`), põe `NVIDIA_CANDIDATES` na frente como prioridade e sonda até `NVIDIA_PROBE_CAP` (14); vivo = 200 ou 429; falhas imprimem `[probe] <id>: HTTP <código>`. Só reinicia o gateway se o config mudou, e reinicia o serviço DO PERFIL (`hermes-gateway-<perfil>.service`; perfil default = `hermes-gateway.service`), derivado de `HERMES_HOME`. Antes reiniciava sempre `hermes-gateway.service` (gateway do perfil default), deixando o do perfil pesquisa intacto.
- Reconciliação de órfãos: só é órfão o modelo que sumiu do `/models` (`NVIDIA_DISCOVERED`). Timeout/429 no probe não remove nada do config; se a descoberta falhar, não reconcilia. Sem isso, um ReadTimeout apagava o modelo de `fallback_providers` e `moa.reference_models`.
- Limitação: probe com timeout de 20 s; modelos lentos (kimi-k3, deepseek-v4.1-flash, às vezes nemotron-3.5-lightning) dão ReadTimeout e podem ficar fora do ranking de uma execução (mas não são mais removidos do config).
- Sonda Nous Portal catálogo (`model-catalog.json`) + valida candidatos free conhecidos (`NOUS_FREE_CANDIDATES`) via probe 200
- **NVIDIA by design**: `model.default`, `moa.reference_models` (preset + root) são **exclusivamente NVIDIA text-only**
- **Aggregator multimodal**: o `moa.aggregator` prefere multimodal MoE em dois estágios — primeiro sonda `MULTIMODAL_AGG_CANDIDATES_NIM` via NIM (probe 200), depois `MULTIMODAL_AGG_CANDIDATES_OR` via OpenRouter (probe 200 **ou 400** — 400 = modelo existe mas rejeita input text-only, esperado para multimodal-only); fallback = text-only high-priority NVIDIA. **`z-ai/glm-5-3-flash` e `deepseek-ai/deepseek-v4.1-flash` são modelos OpenRouter, não NIM** — probe NIM retorna 404/timeout para eles; adicioná-los apenas a `MULTIMODAL_AGG_CANDIDATES_OR` com sufixo `:free`. Modelos com sufixo `:free` no ID → `patch_moa_aggregator` grava `provider: openrouter` + `base_url: openrouter.ai/api/v1`
- Ranking: probe 200 → separa text-only vs multimodal → model.default/ref_models = text-only high-priority → aggregator = multimodal preferencial → fallback text-only high-priority
- Reconciliação: remove entradas `provider: nvidia` órfãs (que saíram da lista NIM atual) do `moa.reference_models` (preset e root) antes de reescrever
- Atualiza cache `provider_models_cache.json` (NVIDIA + Nous)
- Patch YAML via regex (não via PyYAML) — mantém formatação/anchors do config; regex atualizados para indentation real do config (8 spaces para preset, anchor `&id001`)
- Reinicia o gateway ao final via `systemctl --user --no-block restart` (ver seção Gateway Restart)

O aggregator é selecionado automaticamente pelo script (nunca fixo em modelo pago):
- **Multimodal MoE preferencial NIM**: `moonshotai/kimi-k3`, `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, `meta/muse-glimmer-30b`
- **Multimodal MoE preferencial OpenRouter**: `deepseek-ai/deepseek-v4.1-flash:free`, `z-ai/glm-5-3-flash:free`
- **Fallback text-only**: high-priority NVIDIA (próximo após default+refs)
- **Guarda paid-model**: `assert_free_models()` aborta exit 2 se qualquer selecionado for pago

Nous/Cloudflare aparecem **apenas** em `fallback_providers` via `update_free_models.py`.

### `choose_best_free_llm.py`
- Thin wrapper que chama `update_free_models.py` (mantido para compatibilidade com cron antigo)

## Config Keys Atualizadas

| Key | Formato | Escrito por |
|-----|---------|-------------|
| `fallback_providers` | lista de `{provider, model, base_url, key_env?}` | `update_free_models.py` |
| `model.default` | string (model ID) | `update_models.py` |
| `moa.presets.default.reference_models` | lista de `{provider, model, enabled}` | `update_models.py` |
| `moa.reference_models` (root) | mesmo formato acima | `update_models.py` |
| moa.aggregator | {provider, model} | `update_models.py` — **multimodal MoE preferencial** (ex.: `moonshotai/kimi-k3`, `z-ai/glm-5-3-flash`, `deepseek-ai/deepseek-v4.1-flash`, `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, `meta/muse-glimmer-30b`) quando disponível no NIM; caso contrário text-only high-priority NVIDIA (próximo após default+refs) |

**Nota:** `moa.aggregator` é NVIDIA (ex.: `deepseek-ai/deepseek-v4-flash-0731`), **não** fixo em `anthropic/claude-sonnet-5`. O script `update_models.py` seleciona automaticamente. Nous/Cloudflare ficam apenas em `fallback_providers`.

## API Keys Necessárias

```
# Em ~/.hermes/profiles/<profile>/.env
NVIDIA_API_KEY=nvapi-...
NOUS_API_KEY=sk-nous-...
OPENROUTER_API_KEY=sk-or-v1-...
ANTHROPIC_API_KEY=sk-ant-...
```

Obter em:
- NVIDIA: https://integrate.nvidia.com/rocfm/api/key (conta NVIDIA gratuita)
- Nous: https://portal.nousresearch.com (conta Nous Research)
- OpenRouter: https://openrouter.ai/keys (conta OpenRouter gratuita)
- Anthropic: https://console.anthropic.com/ (conta Anthropic)

## Gateway Restart

O gateway atual relê `config.yaml` do disco a cada mensagem (`_load_gateway_config()` sem cache): `model.default` entra na assinatura do agente em cache (mudou → agente recriado na próxima mensagem) e `fallback_providers` é relido em runtime. Por isso `update_free_models.py` não precisa de restart. Exceção: sessão com `/model` fixado mantém o modelo até ser reiniciada.

O usuário optou por manter um restart no `update_models.py`, com estas regras:
- **Use sempre `systemctl --user --no-block restart hermes-gateway.service`** (timeout curto, ~30 s). Sem `--no-block` o `systemctl` espera o gateway drenar as sessões ativas, estoura o timeout do `subprocess.run` (`TimeoutExpired`) e o cron é marcado como falho mesmo com o config já gravado.
- **Um único restart por fluxo**: o restart fica só dentro do `update_models.py`; o `update_models_wrapper.sh` não reinicia de novo (antes reiniciava duas vezes).
- O `hermes-gateway.service` é o gateway que atende o Telegram deste perfil: rodar `hermes cron run <id-do-update-hermes-models>` de dentro do chat derruba a própria conversa. Teste pelo terminal ou espere a execução agendada e leia `~/.hermes/profiles/<profile>/cron/output/<job-id>/`.

Verificar: `hermes cron list` (campo `Last run: ... ok`) e o último `.md` em `cron/output/<job-id>/`.

## Troubleshooting

**Todos os crons do perfil falham juntos com `cron external worker exited before ownership acknowledgement`:**
- O script nem executa: o worker do cron quebra ao importar o módulo `cron` (traceback termina em `ModuleNotFoundError` de dependência do próprio Hermes, ex. `ruamel`). Leia o traceback completo em `hermes cron list` (campo `Last run`), não só a notificação.
- Diagnóstico: se vários jobs não relacionados começaram a falhar no mesmo horário, compare com `git -C ~/.hermes/hermes-agent log -3 --format='%h %ci'` — é regressão de update do Hermes, não bug dos scripts.
- Correção: rodar `hermes update` de novo (resolveu); só depois considerar instalar o pacote no Python embutido. Valide com `hermes cron run <id-do-choose-best-free-llm>` (não reinicia gateway) e confira `Last run: ... ok`.

**Cron falha com "Network is unreachable":**
- O perfil não tem acesso à internet no momento do cron
- Verifique proxy/firewall

**Nenhum modelo NVIDIA encontrado:**
- `NVIDIA_API_KEY` ausente ou inválida
- Verifique: `grep NVIDIA_API_KEY ~/.hermes/profiles/<profile>/.env`

**Editar o script não muda o que o cron executa:**
- Os scripts existem em duas cópias: a do repositório/skill (`~/Hermes/skills/free-llm/scripts/`, o `scripts/` do diretório da skill) e a que o cron realmente roda (`~/.hermes/profiles/<profile>/scripts/`). O campo `script` de `cron/jobs.json` é resolvido contra o diretório de scripts do perfil.
- Consequência: corrigir `NVIDIA_CANDIDATES` só na cópia do repo deixa o cron sondando os IDs velhos e reportando sucesso — o sintoma volta na próxima execução agendada.
- Depois de qualquer edição, copie o script para `~/.hermes/profiles/<profile>/scripts/` e confirme com `hermes cron runs <job-id> --profile <profile>` que a execução seguinte já usa a lista nova.

**Gateway não reinicia:**
- O comando `hermes` não está no PATH do ambiente cron
- Use caminho absoluto: `$(which hermes) gateway restart`

**`update-hermes-models` falha com `ERRO: sem modelos free`:**
- Isso indica probe NVIDIA vazio no horário do cron, geralmente falha transitória de rede/provider.
- O script **é fail-soft**: se `get_nvidia_models()` retorna vazio, mantém o config atual e sai 0 (não quebra o cron).

**`ModuleNotFoundError: No module named 'requests'` nos crons:**
- O cron roda com python3 do sistema; o `requests/` instalado localmente em `scripts/` não está no `sys.path` automaticamente
- **Fix**: garantir que `update_models_wrapper.sh` exporte `PYTHONPATH` antes de chamar o script:
  ```bash
  export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"
  ```
- Se o wrapper já tem isso e ainda falha, instale via pip com target explícito: `pip3 install requests --target=~/.hermes/profiles/<profile>/scripts/`

**Regex de patch YAML sensível à indentação:**
- O `patch_moa_aggregator()` usa regex ancorado no anchor YAML `&id001` (preset) e na indentação real do config (8 spaces para provider/model sob `presets.default.aggregator`)
- Se a estrutura do config.yaml mudar (ex.: anchor removido, indentação alterada), o regex falha silenciosamente
- Verifique com: `grep -n 'aggregator: &id001' config.yaml` antes de rodar o script

**NVIDIA NIM rate limit** (conta falagama@gmail.com): até **40 rpm**. Os scripts sondam ~19 req/run no `update_free_models` e ~12 no `update_models` — dentro do limite para run isolado. Não agrupe runs no mesmo minuto (crons já espaçados).

**model.default como "default" (string literal):**
- Indica que `update_models.py` não rodou com sucesso
- Rode manualmente: `HERMES_HOME=$HOME/.hermes/profiles/<profile> python3 update_models.py --check`

**moa.aggregator não é um multimodal MoE esperado:**
- Indica que nenhum candidato multimodal respondeu (NIM nem OpenRouter) no momento do probe
- O script cai no fallback text-only high-priority NVIDIA (ex.: `deepseek-ai/deepseek-v4-flash-0731`)
- Para adicionar novo candidato NIM: adicione o ID em `MULTIMODAL_AGG_CANDIDATES_NIM` **e** em `NVIDIA_CANDIDATES`
- Para adicionar novo candidato OpenRouter: adicione `modelo/id:free` em `MULTIMODAL_AGG_CANDIDATES_OR` apenas (não em `NVIDIA_CANDIDATES`)
- Verifique NIM: `cd ~/.hermes/profiles/<profile> && python3 -c "from scripts.update_models import get_nvidia_models, MULTIMODAL_AGG_CANDIDATES_NIM; print([m['id'] for m in get_nvidia_models() if m['id'] in MULTIMODAL_AGG_CANDIDATES_NIM])"`
- Verifique OR: `cd ~/.hermes/profiles/<profile> && python3 -c "from scripts.update_models import get_openrouter_multimodal_models; print([m['id'] for m in get_openrouter_multimodal_models()])"`

**assert_free_models abortou (exit 2):**
- Um modelo com prefixo pago (`anthropic/`, `openai/`, `google/gemini`, `cohere/`, `mistralai/command`) foi selecionado
- Indica bug na lógica de seleção ou candidato indevidamente adicionado
- Nenhuma alteração foi feita no config — inspecione os logs do cron e corrija `NVIDIA_CANDIDATES`/`MULTIMODAL_AGG_CANDIDATES_*`

## Desinstalação

```bash
# Remover scripts
rm -f ~/.hermes/profiles/<profile>/scripts/update_free_models.py
rm -f ~/.hermes/profiles/<profile>/scripts/update_models.py
rm -f ~/.hermes/profiles/<profile>/scripts/choose_best_free_llm.py

# Remover cron jobs
# Edite ~/.hermes/profiles/<profile>/cron/jobs.json e remova os 3 jobs
# Ou simplesmente delete jobs.json e recrie os jobs manualmente
```

## Referências

- [NVIDIA NIM Documentation](https://docs.nvidia.com/nim/)
- [Nous Portal](https://portal.nousresearch.com)
- [OpenRouter Documentation](https://openrouter.ai/docs)
- [Hermes Agent Docs](https://hermes-agent.nousresearch.com/docs)
