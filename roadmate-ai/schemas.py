from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 프론트 연동: Pleos STT·TTS locale 값(es-US·es-MX는 같은 스페인어). 통역은 한국어와 외국어 한 쌍, fr-CA는 주행 안내만
Locale = Literal["ko-KR", "en-US", "es-US", "es-MX"]
Scenario = Literal["gas_station", "parking", "rental_counter", "accident", "general"]
Ident = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:\-]+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _clean(v: str) -> str:
    v = " ".join(v.split())
    if not v:
        raise ValueError("empty text")
    return v


# 백엔드 연동: POST /internal/v1/translate 요청
class TranslateRequest(_Strict):
    request_id: str = Ident
    session_id: str = Ident
    session_epoch: int = Field(ge=0)
    turn_id: int = Field(ge=0)
    source_locale: Locale
    target_locale: Locale
    scenario: Scenario
    text: str = Field(min_length=1, max_length=2000)
    engine_bundle: str = Field(min_length=1, max_length=64)

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        return _clean(v)

    @model_validator(mode="after")
    def _locales_differ(self):
        if self.source_locale == self.target_locale:
            raise ValueError("source_locale and target_locale must differ")
        if "ko-KR" not in (self.source_locale, self.target_locale):
            raise ValueError("one side must be ko-KR (ko-KR <-> en-US / es-US / es-MX)")
        return self


class TranslateResponse(BaseModel):
    request_id: str
    session_id: str
    session_epoch: int
    turn_id: int
    status: Literal["ok", "clarification_required"]
    translated_text: Optional[str]
    clarification_question: Optional[str]
    engine_bundle: str
    model_id: str
    prompt_version: str
    glossary_version: str
    latency_ms: int


class AppTranslateRequest(_Strict):
    text: str = Field(min_length=1, max_length=2000)
    source_locale: Locale = Field(alias="from")
    target_locale: Locale = Field(alias="to")
    scenario: Scenario | Literal["rental"] = Field(alias="situation")


class AppTranslateResponse(BaseModel):
    text: str


class AppTranslateClarification(BaseModel):
    clarification: str
    clarification_locale: Locale


# 백엔드 연동: POST /internal/v1/parse-destination 요청
class DestinationRequest(_Strict):
    request_id: str = Ident
    session_id: str = Ident
    session_epoch: int = Field(ge=0)
    turn_id: int = Field(ge=0)
    source_locale: Locale
    text: str = Field(min_length=1, max_length=2000)
    engine_bundle: str = Field(min_length=1, max_length=64)

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        return _clean(v)


class PoiCandidate(BaseModel):
    poi_id: str
    name_ko: str
    name_en: str
    name_es: str
    region_ko: str
    lat: float
    lon: float
    verified: bool
    score: float


class DestinationResponse(BaseModel):
    request_id: str
    session_id: str
    session_epoch: int
    turn_id: int
    status: Literal["ok", "clarification_required", "not_found", "not_destination"]
    intent: Literal["set_destination", "other"]
    query_original: Optional[str]
    query_ko_candidate: Optional[str]
    region_hint: Optional[str]
    needs_confirmation: bool
    candidates: list[PoiCandidate]
    clarification_question: Optional[str]
    engine_bundle: str
    model_id: str
    prompt_version: str
    poi_version: str
    latency_ms: int


# 백엔드 연동: POST /internal/v1/cancel 요청
class CancelRequest(_Strict):
    session_id: str = Ident
    session_epoch: int = Field(ge=0)


class CancelResponse(BaseModel):
    session_id: str
    cancelled_through_epoch: int
    cancelled_inflight: int


class ErrorBody(BaseModel):
    error_code: str
    request_id: Optional[str] = None
    message: str = ""
