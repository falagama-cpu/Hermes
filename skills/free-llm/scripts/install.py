#!/usr/bin/env python3
"""install.py — instala o seletor de LLMs FREE (v4 + ranking Artificial Analysis)
em um perfil do Hermes Agent. Multiplataforma: Linux, macOS e Windows.

Uso:
  python install.py <perfil> [deliver] [--check] [--yes]
    <perfil>   nome do perfil (ex.: trabalho) ou "default"
    [deliver]  destino do relatório do cron: local (padrão), telegram, telegram:<chat_id>
    --check    só verifica dependências e chaves; não instala nada
    --yes      não interativo: não pergunta chaves (aborta se faltar o mínimo)

Windows: use "py install.py ..." (ou "python install.py ...") no PowerShell.

Ordem — nada é copiado nem agendado antes dos passos 1 e 2 passarem:
  1. Dependências  hermes no PATH, perfil existe, Python >= 3.9, PyYAML
  2. Chaves de API pede as ausentes (entrada oculta) e grava em <perfil>/.env
  3. Instalação    copia scripts para <perfil>/scripts/ e cria 1 job de cron
  4. Teste         mostra o comando de simulação (--dry-run), não grava config
"""
from __future__ import annotations

import getpass
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

SCRIPTS = ("hermes-free-model-selector-v4.py", "run_model_selector_v4.py", "aa_scores.py")
JOB_NAME = "choose-best-free-llm"
SCHEDULE = "0 2,8,14,20 * * *"
LEGACY_JOBS = ("update-hermes-models", "update-free-models")

KEYS = [
    ("ARTIFICIAL_ANALYSIS_API_KEY", "https://artificialanalysis.ai → API Access (grátis, 1000 req/dia; ranking por benchmark)"),
    ("OPENROUTER_API_KEY", "https://openrouter.ai/settings/keys (modelos :free)"),
    ("NVIDIA_API_KEY", "https://build.nvidia.com → Get API Key"),
    ("NOUS_API_KEY", "https://portal.nousresearch.com → API Keys"),
    ("CLOUDFLARE_API_TOKEN", "https://dash.cloudflare.com/profile/api-tokens (template 'Workers AI')"),
    ("CLOUDFLARE_ACCOUNT_ID", "https://dash.cloudflare.com → Workers AI → Account ID"),
    ("GOOGLE_API_KEY", "https://aistudio.google.com/apikey (Gemini, free tier ~20 req/dia por modelo)"),
]
PROVIDERS = ("OPENROUTER_API_KEY", "NVIDIA_API_KEY", "NOUS_API_KEY", "CLOUDFLARE_API_TOKEN",
             "GOOGLE_API_KEY")

FAIL = False


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def warn(msg: str) -> None:
    print(f"  ! {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL = True
    print(f"  ✗ {msg}")


def default_root() -> Path:
    """Home padrão do Hermes (mesma regra do agente)."""
    if os.environ.get("HERMES_HOME", "").strip():
        p = Path(os.path.expandvars(os.path.expanduser(os.environ["HERMES_HOME"])))
        # HERMES_HOME pode já apontar para um perfil (…/profiles/<nome>)
        return p.parent.parent if p.parent.name == "profiles" else p
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        return (Path(base) if base else Path.home() / "AppData" / "Local") / "hermes"
    return Path.home() / ".hermes"


def profile_home(profile: str) -> Path:
    root = default_root()
    return root if profile == "default" else root / "profiles" / profile


def read_env(envf: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def set_key(envf: Path, name: str, value: str) -> None:
    """Grava/atualiza NOME=valor sem ecoar o valor."""
    lines = envf.read_text(encoding="utf-8").splitlines(True) if envf.exists() else []
    new, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == name:
            new.append(f"{name}={value}\n")
            done = True
        else:
            new.append(line if line.endswith("\n") else line + "\n")
    if not done:
        new.append(f"{name}={value}\n")
    envf.write_text("".join(new), encoding="utf-8")
    if os.name == "posix":
        envf.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600


def hermes(args: list[str], profile_args: list[str]) -> subprocess.CompletedProcess:
    exe = shutil.which("hermes")
    return subprocess.run([exe, *profile_args, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 1
    check = "--check" in argv
    yes = "--yes" in argv or "-y" in argv
    pos = [a for a in argv if not a.startswith("-")]
    profile = pos[0]
    deliver = pos[1] if len(pos) > 1 else "local"
    phome = profile_home(profile)
    pargs = [] if profile == "default" else ["-p", profile]
    envf = phome / ".env"
    src = Path(__file__).resolve().parent

    print("=== 1. Dependências ===")
    if shutil.which("hermes"):
        ok(f"hermes: {shutil.which('hermes')}")
    else:
        bad("hermes não encontrado no PATH. Instale o Hermes Agent primeiro:")
        print("      Linux/macOS/WSL2: curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash")
        print("      Windows (PowerShell): iex (irm https://hermes-agent.nousresearch.com/install.ps1)")
        print("      docs: https://hermes-agent.nousresearch.com/docs")
    if phome.is_dir():
        ok(f"perfil: {phome}")
    else:
        bad(f"perfil '{profile}' não existe ({phome}). Crie com: hermes profile create {profile}")
    if sys.version_info >= (3, 9):
        ok(f"Python {sys.version.split()[0]}")
    else:
        bad("Python >= 3.9 necessário")
    try:
        import yaml  # noqa: F401
        ok("PyYAML")
    except ImportError:
        warn("PyYAML ausente neste Python (o cron usa o Python do Hermes, que já inclui).")
        print("      Para execução manual: python -m pip install --user pyyaml")
    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        ok("systemctl --user (o seletor reinicia o gateway após trocar modelo)")
    else:
        warn("sem systemd (Windows/macOS): o seletor grava o config e não reinicia o gateway —")
        print("      o gateway relê config.yaml a cada mensagem; sessões abertas: /new")
    print("  (requer HTTPS de saída: openrouter.ai, integrate.api.nvidia.com,")
    print("   inference-api.nousresearch.com, api.cloudflare.com, artificialanalysis.ai)")
    if FAIL:
        print("\nABORTADO: resolva os itens marcados com ✗ e rode de novo. Nada foi instalado.")
        return 1

    print(f"\n=== 2. Chaves de API ({envf}) ===")
    env = read_env(envf)
    interactive = sys.stdin.isatty() and not yes and not check
    for name, url in KEYS:
        if env.get(name) or os.environ.get(name):
            ok(name)
            continue
        if not interactive:
            warn(f"{name} ausente — obter em: {url}")
            continue
        print(f"  ? {name} ausente — obter em: {url}")
        val = getpass.getpass("    Cole o valor (Enter = pular; não aparece na tela): ").strip()
        if val:
            set_key(envf, name, val)
            env[name] = val
            ok(f"{name} gravada")
        else:
            warn(f"{name} pulada")
    have = lambda k: bool(env.get(k) or os.environ.get(k))  # noqa: E731
    n_prov = sum(have(k) for k in PROVIDERS)
    # OpenAI entra pelo login ChatGPT (OAuth do Hermes), não por chave no .env.
    try:
        codex = '"openai-codex"' in (phome / "auth.json").read_text(encoding="utf-8")
    except OSError:
        codex = False
    if codex:
        ok("openai-codex (login ChatGPT) — fonte OpenAI ativa")
        n_prov += 1
    else:
        warn("OpenAI opcional, sem chave: faça login com  hermes auth add openai-codex")
    if have("CLOUDFLARE_API_TOKEN") and not have("CLOUDFLARE_ACCOUNT_ID"):
        warn("CLOUDFLARE_API_TOKEN sem CLOUDFLARE_ACCOUNT_ID: a fonte Cloudflare será ignorada.")
    if not have("ARTIFICIAL_ANALYSIS_API_KEY"):
        warn("sem ARTIFICIAL_ANALYSIS_API_KEY o ranking usa heurística por palavras-chave.")
    if n_prov == 0:
        print("\nABORTADO: nenhuma chave de provedor (OpenRouter/NVIDIA/Nous/Cloudflare/Google) nem login openai-codex.")
        print("Sem ao menos uma o seletor não encontra modelo algum. Nada foi instalado.")
        return 1
    ok(f"{n_prov}/{len(PROVIDERS) + 1} provedores disponíveis")
    if check:
        print("\n--check: pré-requisitos OK. Nada instalado.")
        return 0

    dst = phome / "scripts"
    print(f"\n=== 3. Instalação em {dst} ===")
    dst.mkdir(parents=True, exist_ok=True)
    for f in SCRIPTS:
        shutil.copy2(src / f, dst / f)
        compile((dst / f).read_text(encoding="utf-8"), str(dst / f), "exec")  # valida sem gerar __pycache__
    ok("scripts copiados e compilam")

    jobs = hermes(["cron", "list", "--all"], pargs).stdout
    if JOB_NAME in jobs:
        warn(f"job {JOB_NAME} já existe — não recriado (ajuste: hermes {' '.join(pargs)} cron edit <id>)")
    else:
        r = hermes(["cron", "create", SCHEDULE, "--name", JOB_NAME,
                    "--script", "run_model_selector_v4.py", "--no-agent",
                    "--deliver", deliver], pargs)
        if r.returncode != 0:
            bad(f"falha ao criar o job: {(r.stderr or r.stdout).strip()[:300]}")
            return 1
        ok(f"job {JOB_NAME} ({SCHEDULE}, deliver={deliver})")
    if any(j in jobs for j in LEGACY_JOBS):
        warn("job legado encontrado — pause-o (dois jobs gravando model.default conflitam):")
        print(f"      hermes {' '.join(pargs)} cron list ; hermes {' '.join(pargs)} cron pause <job_id>")

    print("\n=== 4. Teste (simulação, não grava config) ===")
    py = "py" if sys.platform == "win32" else "python3"
    if sys.platform == "win32":
        print(f'  cd "{dst}"; $env:HERMES_HOME="{phome}"; {py} run_model_selector_v4.py --dry-run --force --no-restart')
    else:
        print(f'  cd "{dst}" && HERMES_HOME="{phome}" {py} run_model_selector_v4.py --dry-run --force --no-restart')
    return 0


if __name__ == "__main__":
    sys.exit(main())
