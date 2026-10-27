"""Reproduz a receita da free-llm skill (references/failure-triage.md:42-58)
contra o error_classifier do gateway ATIVO, pra confirmar a classif. 429/402
que embasa o veredito B-hardening (sem rede real)."""
import os, sys
_AGENT = os.environ.get("HERMES_AGENT_HOME", "/home/fabio/.hermes/hermes-agent")
sys.path.insert(0, _AGENT)
try:
    import httpx
    from openai import APIStatusError
    from agent.error_classifier import classify_api_error
except Exception as e:                      # hermes-agent ausente/erro -> pula, não falha CI
    print(f"SKIP recipe: hermes-agent unavailable ({e})")
    sys.exit(0)


def classify(status, msg, provider="custom"):
    body = {"error": {"message": msg}}
    req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    resp = httpx.Response(status, request=req, json=body,
                          headers={"content-type": "application/json"})
    v = classify_api_error(APIStatusError(f"Error code: {status}", response=resp, body=body),
                            provider=provider, model="probe")
    return (v.reason.value, v.retryable, v.should_fallback, v.should_rotate_credential, v.error_context)


print("CF 4006 neurons/dia 429 ->", classify(429, "you have used up your daily free allocation of 10,000 neurons"))
print("Google 402 credits      ->", classify(402, "you have run out of code executions!"))
print("402 try-again 5min      ->", classify(402, "rate limit reached, try again in 5 minutes"))
print("429 generic upstream    ->", classify(429, "Rate limit exceeded"))
