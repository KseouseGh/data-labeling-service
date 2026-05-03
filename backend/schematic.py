from pydantic import BaseModel, Field, field_validator
from typing import Optional

class SyntheticExample(BaseModel):
    question: str = Field(..., description="Вопрос к юзеру на основе контекста.", min_length=5)
    answer: str = Field(..., description="Ожидаемый правильный ответ.", min_length=5)
    confidence: float = Field(
        default=0.5, 
        ge=0.0, 
        le=1.0, 
        description="Уверенность LLM-ассистента в корректности ответа (в интервале 0.0-1.0)."
    )
    source_span: Optional[str] = Field(
        default=None, 
        description="Часть исходного текста, на которой основан ответ."
    )

    @field_validator('confidence', mode='before')
    @classmethod
    def ensure_confidence(cls, v):

        if v is None:
            return 0.5 #If not confidence_value in answer, then by default setting (((0.5)))!
        return float(v)