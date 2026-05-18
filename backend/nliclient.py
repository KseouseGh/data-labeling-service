from openai import AsyncOpenAI
from typing import Optional, List, Dict, Any
import json
import logging
import config
import urllib.parse
import httpx

logger = logging.getLogger(__name__)
class NLIClient:
    """NLI-client from API and model. Using for classification: entailment / contradiction / neutral.!"""
    def __init__(self, api_key: str, model: str, base_url: str = None): # Base_url optional with hf-configuration!
        self.api_key = api_key
        self.model = model
        encoded_model = urllib.parse.quote(model, safe="")
        self.api_url = f"https://router.huggingface.co/hf-inference/models/{encoded_model}" # Encoder-based model!
        self.temperature = 0.0
        
    async def check_contradiction(
        self, 
        premise: str, 
        hypothesis: str,
        max_tokens: int = 50 # Ignored param for classification!
       ) -> Dict[str, Any]:
        """Сравнивает два утверждения через HF Cross-Encoder. Returns:
        {"label": "entailment" | "contradiction" | "neutral", "score": float}!
        """        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.api_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                json={
                    "inputs": {
                        "text": premise[:512],
                        "text_pair": hypothesis[:512]
                    }
                }
                )
                response.raise_for_status()
                result = response.json()
                # Config of hf-using returns list [{"label": "...", "score": 0.95}, ...]!
                if isinstance(result, list) and len(result) > 0:
                    best = max(result, key=lambda x: x["score"])
                    label = best["label"].lower()
                    score = float(best["score"]) # Normalization of labels!
                    label_map = {
                        "entailment": "entailment",
                        "contradiction": "contradiction",
                        "neutral": "neutral"
                    }
                    clean_label = label_map.get(label, "neutral")
                    score = max(0.0, min(1.0, score))
                    return {"label": clean_label, "score": score}
                # Fallback for error of request NLI-checking!
                logger.warning(f"Unexpected NLI response: {result}!")
                return {"label": "neutral", "score": 0.5}
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400: # Logging body of NLI-model's response!
                try:
                    error_body = e.response.json()
                    logger.error(f"NLI API 400 error body: {error_body}!")
                except:
                    logger.error(f"NLI API 400 error text: {e.response.text[:500]}!")
            if e.response.status_code == 503:
                logger.warning("NLI model loading (503), returning neutral")
            logger.error(f"NLI API error: {e}!")
            return {"label": "neutral", "score": 0.0}
        except Exception as e:
            logger.error(f"NLI API error: {e}!")
            return {"label": "neutral", "score": 0.0}