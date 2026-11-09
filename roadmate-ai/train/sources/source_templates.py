"""Source locales/scenarios and a small destination-retention supplement.

Bulk conversational sources are written by the teacher, not expanded templates.
No translation targets are stored here.
"""

SCENARIOS = ("gas_station", "parking", "rental_counter", "accident", "general")
DIRECTIONS = (("en-US", "ko-KR"), ("es-US", "ko-KR"), ("es-MX", "ko-KR"),
              ("ko-KR", "en-US"), ("ko-KR", "es-US"), ("ko-KR", "es-MX"))

def generated_sources():
    for source in ("en-US", "es-US", "es-MX", "ko-KR"):
        for index in range(2):
            text = {"en": f"Please set the route to the visitor information desk in district {index + 1}.",
                    "es": f"Marque la ruta a la oficina de información del distrito {index + 1}, por favor.",
                    "ko": f"{index + 1}구역 관광 안내소로 가는 경로를 설정해 주세요."}[source[:2]]
            yield {"group_id": "distill_destination_information", "task": "destination",
                   "source_locale": source, "scenario": "general", "text": text,
                   "source_origin": "authored_source_template"}
