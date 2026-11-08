# Manutenção segura de skills Hermes

Procedimento para alterar uma skill sem colocar a produção em risco. Aplica-se a
qualquer skill do perfil; os caminhos abaixo usam `<skill_dir>` e `<perfil>`.

## 1. Identificar o escopo real

- Diretório da skill: `~/.hermes/profiles/<perfil>/skills/<skill>/`.
- Confirme o perfil ativo (`HERMES_HOME`) antes de qualquer leitura/escrita.
- Liste os arquivos atuais, incluindo `references/`, `scripts/` e `tests/`.

## 2. Backup integral

```bash
SKILL=<skill_dir>
BK=<base>/backups/<skill>-pre-<versao>-<timestamp>
mkdir -p "$BK"
rsync -a --exclude='.venv' --exclude='__pycache__' --exclude='.git' "$SKILL/" "$BK/"
```

Exclua `.venv`/`__pycache__` (reconstruíveis). **Não sobrescreva backups anteriores.**

## 3. Hashes SHA256

```bash
find "$BK" -type f \( -name '*.py' -o -name '*.md' -o -name '*.sh' \
  -o -name '*.toml' -o -name '*.json' \) -print0 | sort -z | xargs -0 sha256sum
```

Registre antes e depois. Se a skill estiver em git, registre o estado inicial.

## 4. Implementar

- Correção documental: **não** altere lógica.
- Correção de código: preserve arquitetura, contratos e formato de saída existentes.
- Campos novos devem ser aditivos; remover/renomear quebra consumidores.

## 5. Testes isolados

- Use o `.venv` da skill: `<skill_dir>/.venv/bin/python`.
- Sem pytest? Use `unittest` (stdlib).
- Simule HTTP com `unittest.mock` — **nunca** chamadas reais.
- Cobertura mínima: seleção, dedupe, reservas, health check, swaps e regressão de formato.

## 6. Teste de integração read-only

- Chame o loader/entrypoint com `HERMES_HOME` do perfil.
- Confirme que o diretório de estado **não** muda (mtime antes/depois).
- Compare a saída com o estado persistido (`state.json`).

## 7. Integridade da produção

- `config.yaml`, `jobs.json` e gateway: verifique mtime e estado.
- `jobs.json` é reescrito pelo scheduler a cada tick — use `updated_at` e os campos por
  job, **não** o mtime do arquivo.
- Não reinicie o gateway.

## 8. Entregáveis

Relatório de implementação, resultados de teste, compatibilidade, `SHA256SUMS` e backup.

## 9. Rollback

1. Restaure **apenas** os arquivos da skill a partir do backup.
2. Confirme os hashes.
3. Confirme que a produção segue intacta.
4. **Nunca** faça rollback sobre arquivos compartilhados (seletor, cron, config).

## Armadilhas

- **O módulo lê `HERMES_HOME` no import**: para testar com outro perfil, recarregue o
  módulo (`importlib.reload`) depois de mudar o env — o valor é congelado no import.
- **O curator automatizado pode patchear a skill**: confira o ledger
  (`skills/.curator_ledger.jsonl`) e o mtime antes de assumir que a documentação está
  atualizada.
- **Documentação fica para trás**: confirme caminhos, horários e escritores pelos
  arquivos de estado, nunca pela documentação.
