# Publicar a skill no repo público + testar o instalador

O repositório da skill é público e é instalado por terceiros. Duas regras guiam tudo:
nada desta máquina vai para o repo, e o instalador pede dependências e chaves ANTES de copiar/agendar.

## Gate de sanitização (antes de todo push)

```bash
cd <repo>
grep -rnE '/home/[a-z]|[0-9a-f]{32}|telegram:[0-9]|<nome-do-perfil-local>|@gmail' skills/ README.md
# deve vir vazio (exemplos de uso com perfil real também contam — troque por placeholder)
git log --all -S '<valor-sensível>' --oneline   # o valor já vazou em commit antigo?
```

O que costuma vazar: caminho absoluto em wrapper `.sh` (use `$(cd "$(dirname "$0")" && pwd)`),
docstring com exemplo de cron local, default `HERMES_HOME` com nome de perfil, IDs de conta
(Cloudflare account) como default de `os.environ.get`, chat_id/IDs de job em tabelas do SKILL.md,
exemplo de uso no cabeçalho do instalador. Estado local (IDs de job, chat_id) fica na memória,
não na skill. Valor já presente no histórico só sai com `git filter-repo` + force-push — pergunte
ao usuário antes; não reescreva histórico por conta própria.

## Contrato do instalador

1. Dependências (hermes no PATH, perfil existe, python3 ≥ 3.9 obrigatórios → abortar com comando
   de instalação; PyYAML e `systemctl --user` só avisam).
2. Chaves: prompt oculto (`read -s`) por chave ausente com link de onde obter, grava em
   `<perfil>/.env` chmod 600; `--check`/`--yes`/stdin não-tty só listam. Zero provedores → abortar.
3. Só então copiar scripts + criar cron. Declarar as mesmas chaves em `required_environment_variables`
   (com `optional: true` quando basta ≥ 1) para o Hermes também pedir via captura segura.

## Receita de teste em HOME limpo

- Casos: sem `hermes` no PATH (`env -i HOME=$T PATH=/usr/bin:/bin`) → rc 1 sem arquivos;
  perfil sem chaves com `--yes` → rc 1; `--check` com 1 chave → rc 0 e nada copiado.
- Fluxo interativo: dirija por `pty.fork()` em Python respondendo a cada `Enter = pular`
  (`script -qec` com stdin em pipe não aciona `-t 0` de forma confiável).
- Use um **stub** de `hermes` (`#!/bin/sh` que ecoa os args) no HOME temporário: o `hermes` real num
  HOME virgem entra no setup inicial e trava o teste. Verifique o comando `cron create` ecoado.
- Confira `stat -c %a .env` = 600 e liste o `.env` com valores mascarados (`sed 's/=.*/=<set>/'`).
- Por fim, simulação real do seletor (`--dry-run --force --no-restart`) no perfil de produção.
