# Publicar a skill num repo público + testar o instalador

Duas regras: nada da máquina de quem publica vai para o repo, e o instalador pede
dependências e chaves ANTES de copiar/agendar.

## Gate de sanitização (antes de todo push)

```bash
cd <repo>
# 1. caminhos, IDs, contatos e nomes locais (troque <...> pelos seus valores)
git grep -nE '/home/[a-z]|/Users/[A-Za-z]|C:\\\\Users\\\\|[0-9a-f]{32}|telegram:[0-9]|@gmail|<seu-usuario>|<seu-hostname>|<nome-do-perfil-local>'
# 2. formatos de chave de API
git grep -nE 'sk-or-v1-[A-Za-z0-9]{20}|nvapi-[A-Za-z0-9_-]{20}|sk-ant-|ghp_[A-Za-z0-9]{30}|AIza[0-9A-Za-z_-]{30}|xai-[A-Za-z0-9]{30}|[0-9]{8,10}:AA[A-Za-z0-9_-]{30}'
# 3. o valor já vazou em commit antigo?
git log --all -p -S '<valor-sensível>' --oneline
```

Os dois `git grep` devem vir vazios (exemplos de uso com perfil real também contam — troque
por placeholder). O que costuma vazar:
- caminho absoluto em wrapper ou `sys.path.insert` → use `Path(__file__).resolve().parent`;
- default de `HERMES_HOME` com nome de perfil → perfil dono do script ou home padrão do Hermes;
- IDs de conta (Cloudflare account) como default de `os.environ.get` → leia do `.env`;
- chat_id, IDs de job e nomes de projeto em tabelas/exemplos do SKILL.md → placeholders;
- e-mail de conta em notas de rate limit; nome de usuário/máquina em logs colados.

Estado local (IDs de job, chat_id) fica na memória do agente, não na skill.

**Histórico**: valor que já está em commit antigo só sai reescrevendo o histórico
(`git filter-repo --replace-text` + `--replace-message` + force-push). Pergunte ao dono do repo
antes e faça `git clone --mirror` de backup. Depois do push, clones antigos precisam de
`git fetch && git reset --hard origin/main`. Commits órfãos podem continuar acessíveis por SHA no
GitHub até a coleta de lixo — chave que vazou deve ser **revogada**, não só removida.

## Contrato do instalador (`scripts/install.py`, multiplataforma)

1. Dependências: `hermes` no PATH, perfil existe, Python ≥ 3.9 → abortar com o comando de
   instalação; PyYAML e systemd só avisam.
2. Chaves: `getpass` por chave ausente com link de onde obter, grava em `<perfil>/.env`
   (chmod 600 em POSIX); `--check`/`--yes`/stdin não-tty só listam. Zero provedores → abortar.
3. Só então copiar scripts + criar cron. Declarar as mesmas chaves em
   `required_environment_variables` (com `optional: true` quando basta ≥ 1) para o Hermes
   também pedir via captura segura.
4. Home do Hermes por plataforma: Linux/macOS `~/.hermes`, Windows `%LOCALAPPDATA%\hermes`;
   respeitar `HERMES_HOME`.

## Receita de teste em HOME limpo

- Casos: sem `hermes` no PATH (`env -i HOME=$T PATH=/usr/bin:/bin`) → rc 1 sem arquivos;
  perfil sem chaves com `--yes` → rc 1; `--check` com 1 chave → rc 0 e nada copiado.
  Leia o rc sem pipe (`| tail` devolve o rc do `tail`).
- Use um **stub** de `hermes` (`#!/bin/sh` que registra os args num arquivo) no PATH: o `hermes`
  real num HOME virgem entra no setup inicial e trava o teste. Confira o `cron create` registrado.
- Windows: `py install.py <perfil> --check` num perfil de teste em
  `%LOCALAPPDATA%\hermes\profiles\<perfil>`. Sem Windows à mão, valide ao menos a resolução de
  caminhos trocando `sys.platform = "win32"` **depois** dos imports (antes, o `shutil` tenta
  importar `_winapi`) e definindo `LOCALAPPDATA`.
- Confira a permissão do `.env` (`stat -c %a` = 600) e liste-o mascarado (`sed 's/=.*/=<set>/'`).
- Por fim, simulação real do seletor (`--dry-run --force --no-restart`).
