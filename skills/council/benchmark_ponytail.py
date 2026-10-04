"""Benchmark: compara council com vs sem persona ponytail.

Mesmo prompt, mesma pool (3 modelos vivos), mesma query de código.
Diferença: na run B, um membro recebe persona=ponytail.
Mede: LOC, tokens, chars, time.
"""
import asyncio, json, pathlib, re, time, sys
sys.path.insert(0, "<council-dir>")
import council

PROMPT = """Refatore esta função para remover a duplicação e melhorar performance:

def process_orders(orders):
    result = []
    for order in orders:
        if order.get("status") == "pending":
            processed = []
            for item in order.get("items", []):
                if item.get("qty", 0) > 0:
                    price = item.get("price", 0)
                    qty = item.get("qty", 0)
                    total = price * qty
                    tax = total * 0.1 if order.get("region") == "US" else 0
                    processed.append({
                        "id": item.get("id"),
                        "qty": qty,
                        "price": price,
                        "total": total,
                        "tax": tax
                    })
            order_total = sum(i["total"] + i["tax"] for i in processed)
            order["items_processed"] = processed
            order["total"] = order_total
            result.append(order)
    return result
"""

# Pool A = sem ponytail (baseline council)
pool_A = [
  {"source":"nvidia","model":"nvidia/nemotron-3-super-120b-a12b","base_url":"https://integrate.api.nvidia.com/v1","api_key_env":"NVIDIA_API_KEY"},
  {"source":"nous","model":"poolside/laguna-xs-2.1-20260625","base_url":"https://inference-api.nousresearch.com/v1","api_key_env":"NOUS_API_KEY"},
  {"source":"nvidia","model":"nvidia/nemotron-3-ultra-550b-a55b","base_url":"https://integrate.api.nvidia.com/v1","api_key_env":"NVIDIA_API_KEY"},
]

# Pool B = com ponytail no membro 2
pool_B = json.loads(json.dumps(pool_A))
pool_B[1]["persona"] = "ponytail"

def count_loc(text):
    lines = [l for l in text.splitlines() if l.strip()]
    return len(lines), len(text), len(text.split())

t0 = time.time()
rA = asyncio.run(council.run_council(PROMPT, top_n=3, pool_path=None))
if False:  # vou passar pool direto, não por path
    pass
# na verdade, carrega pool inline:
async def _run(query, members):
    s1 = await council.stage_one(query, members)
    anon, lm = council._anonymize(s1)
    s2, lm = await council.stage_two(query, s1, members)
    final = await council.stage_three(query, s1, s2, lm, members[0])
    return final, s1, s2

# build members from pool_A / pool_B
m_A = [council.Member(
    name=f"m{i+1}-{e['model'].split('/')[-1][:20]}",
    provider=e["source"], model=e["model"],
    base_url=e["base_url"].rstrip("/"),
    api_key=__import__('os').environ[e["api_key_env"]],
) for i,e in enumerate(pool_A)]

m_B = [council.Member(
    name=f"m{i+1}-{e['model'].split('/')[-1][:20]}",
    provider=e["source"], model=e["model"],
    base_url=e["base_url"].rstrip("/"),
    api_key=__import__('os').environ[e["api_key_env"]],
    persona=e.get("persona"),
) for i,e in enumerate(pool_B)]

async def main():
    t0=time.time()
    fA, s1A, _ = await _run(PROMPT, m_A)
    tA = time.time()-t0
    # resposta sem ponytail (do super-120):
    rA = [s for s in s1A if "super-120" in s.model][0]

    # pool B — super-120 sem persona, laguna com persona, ultra sem
    t0=time.time()
    fB, s1B, _ = await _run(PROMPT, m_B)
    tB = time.time()-t0
    rB_laguna = [s for s in s1B if "laguna" in s.model][0]
    rB_super = [s for s in s1B if "super-120" in s.model][0]

    a_loc, a_chars, a_words = count_loc(rA.text)
    b_loc, b_chars, b_words = count_loc(rB_laguna.text)
    b_loc2, b_chars2, b_words2 = count_loc(rB_super.text)

    print(f"=== Benchmark: refatoração de código ===")
    print(f"\n1. nemotron-super-120 (sem ponytail):")
    print(f"   {a_loc} LOC, {a_chars} chars, {a_words} palavras")
    print(f"\n2. laguna (COM ponytail):")
    print(f"   {b_loc} LOC, {b_chars} chars, {b_words} palavras")
    print(f"\n3. nemotron-super-120 (ponytail aplicado via laguna):")
    print(f"   mesmo modelo, sem persona → {b_loc2} LOC / {b_chars2} chars")
    print(f"\nChairman final (sem ponytail): {len(fA)} chars em {tA:.0f}s")
    print(f"Chairman final (laguna ponytail): {len(fB)} chars em {tB:.0f}s")
    print(f"\n--- DIFERENÇA laguna (com vs sem ponytail) ---")
    # o super-120 sem persona aparece tanto em A quanto em B
    delta_loc = b_loc - b_loc2
    delta_chars = b_chars - b_chars2
    delta_words = b_words - b_words2
    print(f"  LOC:    {b_loc2} → {b_loc}  (Δ {delta_loc:+d} = {abs(delta_loc)/b_loc2*100:.0f}% redução)")
    print(f"  Chars:  {b_chars2} → {b_chars}  (Δ {delta_chars:+d})")
    print(f"  Palavras:{b_words2} → {b_words}  (Δ {delta_words:+d})")

asyncio.run(main())
