"""Typed output schemas and enum sets for prompt validators."""

from typing import Dict, List, Literal, Optional, TypedDict, Union


Confidence = Literal["low", "medium", "high"]
Quality = Literal["good", "uncertain", "garbage"]
PageClass = Literal["flight_only", "flight_hotel_package", "garbage_page", "irrelevant_page", "unknown"]
TripProduct = Literal["flight_only", "flight_hotel_package", "unknown"]
Support = Literal["strong", "weak", "none"]

CONFIDENCE_ENUM = {"low", "medium", "high"}
QUALITY_ENUM = {"good", "uncertain", "garbage"}
PAGE_CLASS_ENUM = {"flight_only", "flight_hotel_package", "garbage_page", "irrelevant_page", "unknown"}
TRIP_PRODUCT_ENUM = {"flight_only", "flight_hotel_package", "unknown"}
SUPPORT_ENUM = {"strong", "weak", "none"}
ACTION_ENUM = {"fill", "click", "wait"}


class SelectorHint(TypedDict):
    css: str
    attribute: str
    stability: str


class PriceExtractionOutput(TypedDict):
    price: Optional[float]
    currency: Optional[str]
    confidence: Confidence
    selector_hint: Optional[SelectorHint]
    reason: str


class HtmlQualityOutput(TypedDict):
    quality: Quality
    reason: str


class TripProductGuardOutput(TypedDict):
    page_class: PageClass
    trip_product: TripProduct
    reason: str


class VlmPriceExtractionOutput(TypedDict):
    price: Optional[float]
    currency: Optional[str]
    confidence: Confidence
    route_bound: bool
    page_class: PageClass
    trip_product: TripProduct
    visible_price_text: Optional[str]
    reason: str


class VlmPriceVerificationOutput(TypedDict):
    accept: bool
    support: Support
    reason: str


class VlmMultimodalOutput(TypedDict):
    price: Optional[float]
    currency: Optional[str]
    confidence: Confidence
    page_class: PageClass
    trip_product: TripProduct
    route_bound: bool
    selector_hint: Optional[SelectorHint]
    reason: str


class ModeLabels(TypedDict):
    domestic: Optional[List[str]]
    international: Optional[List[str]]


class FillLabels(TypedDict):
    origin: Optional[List[str]]
    dest: Optional[List[str]]
    depart: Optional[List[str]]
    return_: Optional[List[str]]
    search: Optional[List[str]]


class VlmUiAssistOutput(TypedDict):
    page_scope: Literal["domestic", "international", "mixed", "unknown"]
    page_class: PageClass
    trip_product: TripProduct
    blocked_by_modal: bool
    mode_labels: Optional[Dict[str, Optional[List[str]]]]
    product_labels: Optional[List[str]]
    fill_labels: Optional[Dict[str, Optional[List[str]]]]
    reason: str


class RoiField(TypedDict):
    bbox: Optional[List[float]]
    visible_text: Optional[str]
    confidence: Confidence


class VlmFillRoiOutput(TypedDict):
    origin: RoiField
    dest: RoiField
    depart: RoiField
    return_: RoiField
    reason: str


class VlmRoiValueOutput(TypedDict):
    value: Optional[str]
    confidence: Confidence
    reason: str


class PlanStep(TypedDict, total=False):
    action: Literal["fill", "click", "wait"]
    selector: Union[str, List[str]]
    value: str
    optional: bool


class PlanPayload(TypedDict, total=False):
    steps: List[PlanStep]
    notes: str
