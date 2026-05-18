from openai import AsyncOpenAI
from typing import Optional, List, Dict, Any
import json
import logging
import config

logger = logging.getLogger(__name__)
class NLIClient:
    """NLI-client from API and model. Using for classification: entailment / contradiction / neutral.!"""
    def __init__(self, api_key: str, model: str, base_url: str = "https://openrouter.ai/api/v1"):
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url
        )
        self.model = model
        self.temperature = 0.0
        
    async def check_contradiction(
        self, 
        premise: str, 
        hypothesis: str,
        max_tokens: int = 50
      ) -> Dict[str, Any]:
        """
        Сравнивает два утверждения и возвращает тип связи + уверенность.
        Returns:
            {"label": "entailment" | "contradiction" | "neutral", "score": float}
        """
        NLI_prompt = """Ты классификатор логических отношений между утверждениями.
        Твоя задача: сравнить Premise и Hypothesis и определить их связь.
        Возможные ответы (строго одно):
        - "entailment": Hypothesis логически следует из Premise
        - "contradiction": Hypothesis противоречит Premise  
        - "neutral": связь неочевидна или утверждения о разном
        Верни ТОЛЬКО валидный JSON без пояснений:
        {"label": "entailment|contradiction|neutral", "score": 0.0-1.0}
        Где score — твоя уверенность в ответе (0.0=не уверен, 1.0=абсолютно уверен)."""
        user_prompt = f"""Premise: "{premise[:500]}"
        Hypothesis: "{hypothesis[:500]}"
        Классифицируй:"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": NLI_prompt},
                    {"role": "user", "content": user_prompt}
                ]
                ,
                response_format={"type": "json_object"},
                temperature=self.temperature,
                max_tokens=max_tokens,
                extra_headers={
                    "HTTP-Referer": "https://github.com/KseouseGh/AI-data-labeling-Service",
                    "X-Title": "AI data labeling Service"
                }
            )
            content = response.choices[0].message.content
            result = json.loads(content)
            label = result.get("label", "neutral").lower()
            score = float(result.get("score", 0.5))
            
            if label not in ("entailment", "contradiction", "neutral"):
                logger.warning(f"Unexpected NLI label: {label}, defaulting to neutral")
                label = "neutral"
            score = max(0.0, min(1.0, score))
            return {"label": label, "score": score}
            
        except json.JSONDecodeError as e:
            logger.error(f"NLI JSON parse error: {e}, raw: {content}")
            return {"label": "neutral", "score": 0.0}
        except Exception as e:
            logger.error(f"NLI API error: {e}")
            return {"label": "neutral", "score": 0.0}
# Global client-object for service!
nli_client = NLIClient(
    api_key=config.OPENAI_API_KEY,
    model="meta-llama/llama-3.2-1b-instruct",
    base_url="https://openrouter.ai/api/v1"
)