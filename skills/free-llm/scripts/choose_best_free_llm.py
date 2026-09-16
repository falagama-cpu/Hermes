#!/usr/bin/env python3
"""choose_best_free_llm.py — servia de cron das 2:00 para escolher o melhor
modelo free e gravar fallback_providers no config.yaml.

STATUS: descontinuado como lógica própria. A selecao (ping 200 + ranking por
gravidade + gravacao de fallback_providers) foi consolidada e melhorada no
update_free_models.py, que cobre os 2 provedores (NVIDIA, Nous)
e roda as 14:00.

Este arquivo existe apenas para o job 'choose-best-free-llm' (0 2 * * *) nao
falhar com "Script not found"; ele e um thin wrapper que reaproveita o script
principal. A gravacao em config.yaml e idempotente (nada muda se os fallbacks
ja estiverem corretos), entao a janela das 2:00 e so uma re-verificacao.

Se nao quiser a execucao redundante das 2:00, remova o job. Nao remova este
script sem tambem remover/repontar o job.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

HERMES_HOME = Path(__file__).resolve().parent
PRINCIPAL = HERMES_HOME / "update_free_models.py"


def main() -> int:
    if not PRINCIPAL.exists():
        print(
            f"[choose_best_free_llm] script principal nao encontrado: {PRINCIPAL}",
            file=sys.stderr,
        )
        return 1
    # repassa argv (aceita --check) e executa o script principal no processo atual
    sys.argv = [str(PRINCIPAL), *sys.argv[1:]]
    runpy.run_path(str(PRINCIPAL), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())