# Auditoria de compatibilidade — Council × seletor free-llm

Fluxo READ-ONLY para verificar se o Council continua lendo corretamente o que o
seletor grava. Não execute o seletor, nem o Council, nem o cron.

## 1. Identificar as implementações

Não presuma nomes de diretórios. Localize:

- a skill consumidora (`skills/council/`);
- a skill produtora (no perfil: `skills/data-science/free-llm/` = V4; o V5 fica no
  repositório Hermes: `../../free-llm-v5/` — confirme pelo symlink do script);
- o script que o cron realmente executa — confirme pelo symlink em `<perfil>/scripts/`
  (ex.: `run_model_selector_v5.py` → `hermes-free-model-selector-v5-integrated.py`);
- `config.yaml`, `model-selector/catalog.json`, `reliability.json`, `not_free.json`,
  `state.json`.

## 2. Auditar o loader do consumidor

Leia a função que monta os membros (`load_council_members`) e registre:

- chaves exatas do config lidas (MAIN, MoA, references, fallbacks);
- como chairman/MAIN/references/fallbacks são montados;
- dedupe (por modelo? por provider? ignora `:free`?);
- limite de membros por provider;
- de onde vêm as reservas.

## 3. Comparar as versões produtoras

Compare o **formato**, não só os nomes das chaves:

- quais funções o wrapper substitui (`apply_config`? `write_catalog`? `record_reliability`?);
- se o schema do catálogo é o mesmo;
- se as chaves de `reliability.json` usam a mesma composição (`source::family`);
- se há mudança de semântica em arquivos compartilhados (ex.: TTL de `not_free.json`).

Um wrapper que importa a versão anterior e só substitui as funções de seleção
**herda** o formato do config/catálogo — a compatibilidade é estrutural, não
acidental. Verifique o que **não** foi substituído.

## 4. Verificar o catálogo

- Confirme quem escreve o catálogo (o wrapper reusa o writer da versão anterior?).
- Compare `generated_at` com o horário da última execução do cron.
- Confira se os filtros do consumidor (quarentena, não-free) usam os mesmos thresholds.
- Verifique se as reservas pertencem ao pool atual.

## 5. Reproduzir a seleção read-only

`load_council_members()` só lê arquivos — chame-o num subprocesso com `HERMES_HOME` do
perfil e confirme que o diretório de estado não muda (mtime antes/depois). Compare a
saída com `state.json`.

## 6. Atualização dinâmica

Verifique no código (não execute):

- o consumidor relê o config a cada execução ou mantém cache?
- usa o catálogo corrente ou cópia antiga?
- depende de restart do gateway?

## 7. Classificação

Para cada componente: COMPATÍVEL / PARCIALMENTE COMPATÍVEL / INCOMPATÍVEL /
NÃO COMPROVADO, sempre com path+linha.

## 8. Armadilhas

- **Documentação fica para trás**: uma skill pode referenciar a versão anterior do
  seletor (horário, job, link). Confirme sempre pelo `cron/jobs.json`
  (`enabled`/`state`/`last_run_at`) e pelo `updated_at` do `state.json`, nunca pela
  documentação.
- **Mudança de semântica em arquivo compartilhado**: um TTL zerado no produtor faz o
  arquivo ser write-only para ele, mas um consumidor que lê o arquivo direto (sem o
  mesmo filtro) continua aplicando a exclusão. Compare a **semântica**, não só o schema.
- **Dedupe por modelo esconde o MAIN**: se o aggregator e o MAIN são o mesmo modelo
  (ignorando `:free`), o MAIN não participa. Confirme a composição real, não a
  documentada.
- **`mtime` de `jobs.json` não é evidência**: o scheduler reescreve o arquivo a cada
  tick. Use `updated_at` e os campos por job.
