import asyncio
import logging
from typing import List, Dict, Any
from backend.nliclient import nli_client
from db.dbvector_model import search_memories
import config

logger = logging.getLogger(__name__)
NLI_SEMAPHORE = asyncio.Semaphore(4) # Parallelism-secure!

async def _check(premise: str, hypothesis: str) -> Dict[str, Any]:
    """Box for NLI-call, controlling concurrency and fallback!"""
    async with NLI_SEMAPHORE:
        try:
            return await nli_client.check_contradiction(premise, hypothesis)
        except Exception as e:
            logger.error(f"NLI API error: {e}") # Error-no!
            return {"label": "neutral", "score": 0.0, "error": str(e)}

async def validate_local(examples: List[Dict[str, Any]]) -> List[Dict]:
    """First validation on locals!"""
    conflicts = []
    tasks = []
    for i in range(len(examples)):
        for j in range(i + 1, len(examples)):
            tasks.append((_check(examples[i]["answer"], examples[j]["answer"]), i, j))
            tasks.append((_check(examples[j]["answer"], examples[i]["answer"]), j, i))

    if not tasks:
        return conflicts

    results = await asyncio.gather(*[t[0] for t in tasks], return_exceptions=True) # Validation with good mapping of indexes!
    for res, (idx_i, idx_j) in zip(results, [(t[1], t[2]) for t in tasks]):
        if isinstance(res, Exception):
            continue

        label = res.get("label", "neutral")
        score = res.get("score", 0.0)
        
        if label == "contradiction" and score > config.NLI_THRESHOLD:

            conflicts.append({
                "type": "local",
                "idx_1": idx_i,
                "idx_2": idx_j,
                "score": score,
                "preview_1": examples[idx_i]["answer"][:80],
                "preview_2": examples[idx_j]["answer"][:80]
            })
    return conflicts

async def validate_global(examples: List[Dict[str, Any]], user_id: int) -> List[Dict]:
    """Second validation on globals!"""
    conflicts = []
    for idx, ex in enumerate(examples):
        query = ex.get("question") or ex.get("answer", "")
        chunks = await search_memories(user_id=user_id, query=query, k=1)

        if not chunks:
            continue

        premise = chunks[0] # Searching context fragments!
        res = await _check(premise, ex["answer"])
        label = res.get("label", "neutral")
        score = res.get("score", 0.0)
        
        if label == "contradiction" and score > config.NLI_THRESHOLD:
            conflicts.append({
                "type": "global",
                "example_idx": idx,
                "score": score,
                "preview_answer": ex["answer"][:80],
                "source_preview": premise[:80]
            })
    return conflicts

async def run_session_validation(examples: List[Dict], user_id: int) -> Dict:
    """Celery-worker's task returting structured JSON to Redis-broker!"""
    logger.info(f"Starting NLI validation: {len(examples)} examples, user={user_id}")
    local_conflicts = await validate_local(examples)

    if local_conflicts:
        logger.warning(f"Local conflicts found: {len(local_conflicts)}")
        return {"status": "failed", "stage": "local", "conflicts": local_conflicts}

    global_conflicts = await validate_global(examples, user_id)
    if global_conflicts:
        logger.warning(f"Global conflicts found: {len(global_conflicts)}")
        return {"status": "failed", "stage": "global", "conflicts": global_conflicts}
        
    logger.info("NLI validation passed. Dataset is clean.")
    return {"status": "passed", "stage": "complete", "conflicts": []}