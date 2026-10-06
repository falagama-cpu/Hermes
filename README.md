# Hermes — skills públicas

Skills para o [Hermes Agent](https://hermes-agent.nousresearch.com/docs) (Nous Research).
Funcionam em **Linux, macOS e Windows**. Guia de instalação completo: [`INSTALL.md`](INSTALL.md).

| Skill | O que faz |
|---|---|
| [`free-llm`](skills/free-llm/SKILL.md) | Um job de cron escolhe o modelo principal (MAIN), o MoA (aggregator + reference models) e 3 fallbacks **somente entre modelos gratuitos** de OpenRouter, NVIDIA NIM, Nous Portal e Cloudflare Workers AI, ranqueados pelos benchmarks da [Artificial Analysis](https://artificialanalysis.ai), e grava no `config.yaml` do perfil. |
| [`council`](skills/council/SKILL.md) | Conselho de LLMs: os modelos do perfil respondem em paralelo, ranqueiam as respostas uns dos outros de forma anônima e um chairman sintetiza a resposta final. |

## Instalação rápida

```bash
# Linux / macOS / WSL2
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes profile create <perfil>
git clone https://github.com/falagama-cpu/Hermes.git
python3 Hermes/skills/free-llm/scripts/install.py <perfil>
```

```powershell
# Windows (PowerShell)
iex (irm https://hermes-agent.nousresearch.com/install.ps1)
hermes profile create <perfil>
git clone https://github.com/falagama-cpu/Hermes.git
py Hermes\skills\free-llm\scripts\install.py <perfil>
```

O instalador verifica as dependências, pede as chaves de API ausentes com entrada oculta
(nunca aparecem na tela nem vão para o repositório), copia os scripts e cria o job de cron.
Detalhes, verificação, council e desinstalação: [`INSTALL.md`](INSTALL.md).

## Privacidade

Nenhuma chave de API, ID de conta ou dado pessoal faz parte deste repositório. As chaves
ficam só no `.env` do seu perfil Hermes, na sua máquina.

Licença MIT. Dados de ranking: Artificial Analysis (atribuição exigida pelos termos).
