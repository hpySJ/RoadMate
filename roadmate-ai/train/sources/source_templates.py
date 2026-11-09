"""Source utterances only. No translation targets; every answer must come from the pinned teacher.

Families share group_id across languages, directions and numeric/role variants.
These templates are authored from task types, never from evaluation examples.
"""

SCENARIOS = ("gas_station", "parking", "rental_counter", "accident", "general")
DIRECTIONS = (("en-US", "ko-KR"), ("es-US", "ko-KR"), ("es-MX", "ko-KR"),
              ("ko-KR", "en-US"), ("ko-KR", "es-US"), ("ko-KR", "es-MX"))

TEMPLATES = {
    "en": {
        "gas_station": [
            "The attendant handed me {n} fuel receipts, but only one belongs to this car.",
            "Please charge {amount} won to this card and give the receipt to my colleague.",
            "Before adding {fuel}, could you check the label inside the fuel door?",
            "I have not authorized the extra {amount} won charge for cleaning the windows.",
            "The pump on the {side} stopped after dispensing {n} liters.",
            "Are you asking me to pay {amount} won now, or explaining the deposit?",
            "At {time}, I will hand the payment card to the attendant, not to the driver.",
            "Can the staff inspect pump {n} before anyone tries to use it again?",
        ],
        "parking": [
            "The attendant gave me {n} parking slips, and I need to return the unused ones.",
            "Could you move the delivery cart away from space {n} so I can open the door?",
            "I did not request a second parking payment of {amount} won.",
            "Is the {side} entrance reserved for vehicles with a permit after {time}?",
            "Please tell the attendant that the sign beside space {n} has fallen over.",
            "I will give you {amount} won after the barrier has opened.",
            "Would it be possible to leave these {n} bags with the parking attendant?",
            "The receipt says {time}, but I arrived before the clock shown on it.",
        ],
        "rental_counter": [
            "Please give the replacement key to my colleague when counter {n} opens.",
            "I received a charge of {amount} won, but I have not accepted the optional equipment.",
            "Could we inspect the {side} seat before signing the form at {time}?",
            "Will you return the spare device to me, or should I collect it from desk {n}?",
            "The booking lists {n} passengers, including the children who will travel with us.",
            "Please show me where the extra {amount} won appears in the written agreement.",
            "I am asking whether pickup at {time} is possible, not requesting a change yet.",
            "There are {n} missing pages in the inspection folder you handed to me.",
        ],
        "accident": [
            "The witness gave me {n} photographs, and I will forward them to the rental office.",
            "I did not move the vehicle after the impact at {time}.",
            "Could you ask the driver on the {side} to keep a distance of {n} meters?",
            "The repair estimate is {amount} won; I have not agreed to pay it.",
            "Please hand the document to the officer arriving at {time}, not to me.",
            "Are there {n} vehicles waiting for assistance, or does that number include ours?",
            "I noticed the {side} lamp was broken when I returned at {time}.",
            "The dispatcher asked me to wait {n} minutes before calling again.",
        ],
        "general": [
            "Could you explain which of these {n} lanes leads toward the visitor center?",
            "The notice says the {side} walkway closes at {time}; does that include the ramp?",
            "I have not crossed the bridge marked route {n} yet.",
            "Please pass this address to the guide who arrives at {time}.",
            "Is the {amount} won charge for the ferry vehicle ticket or the passenger ticket?",
            "After marker {n}, should we stay on the {side} side until the road widens?",
            "The map shows {n} entrances, but the guide told us to avoid the narrow one.",
            "I will collect the route leaflet from you at {time}, then give it to the driver.",
        ],
    },
    "es": {
        "gas_station": [
            "El empleado me entregó {n} recibos de combustible, pero solo uno corresponde a este vehículo.",
            "Cobre {amount} wones a esta tarjeta y entregue el recibo a mi acompañante, por favor.",
            "Antes de poner {fuel}, ¿podría revisar la etiqueta de la tapa del combustible?",
            "No he autorizado el cargo adicional de {amount} wones por limpiar los vidrios.",
            "La bomba de la {side} se detuvo después de suministrar {n} litros.",
            "¿Me pide que pague {amount} wones ahora o me está explicando el depósito?",
            "A las {time}, le entregaré la tarjeta al empleado, no al conductor.",
            "¿Puede el personal revisar la bomba {n} antes de que alguien vuelva a usarla?",
        ],
        "parking": [
            "El empleado me dio {n} boletos de estacionamiento y debo devolver los que no utilicé.",
            "¿Podría apartar el carrito del espacio {n} para que pueda abrir la puerta?",
            "No solicité un segundo cobro de estacionamiento de {amount} wones.",
            "¿La entrada de la {side} queda reservada para vehículos con permiso después de las {time}?",
            "Avise al encargado que se cayó el letrero del espacio {n}, por favor.",
            "Le entregaré {amount} wones después de que se levante la barrera.",
            "¿Sería posible dejar estas {n} bolsas con el encargado del estacionamiento?",
            "El recibo indica las {time}, pero llegué antes de la hora que aparece allí.",
        ],
        "rental_counter": [
            "Entregue la llave de reemplazo a mi acompañante cuando abra el mostrador {n}, por favor.",
            "Recibí un cargo de {amount} wones, pero no he aceptado el equipo opcional.",
            "¿Podemos revisar el asiento de la {side} antes de firmar a las {time}?",
            "¿Me devolverá el dispositivo de repuesto o debo recogerlo en el mostrador {n}?",
            "La reserva incluye {n} pasajeros, contando a los niños que viajarán con nosotros.",
            "Muéstreme dónde figura el cargo adicional de {amount} wones en el contrato, por favor.",
            "Pregunto si es posible recoger el vehículo a las {time}; todavía no solicito un cambio.",
            "Faltan {n} páginas en la carpeta de inspección que usted me entregó.",
        ],
        "accident": [
            "El testigo me entregó {n} fotografías y las enviaré a la oficina de alquiler.",
            "No moví el vehículo después del golpe a las {time}.",
            "¿Podría pedir al conductor de la {side} que mantenga {n} metros de distancia?",
            "El presupuesto de reparación es de {amount} wones; no he aceptado pagarlo.",
            "Entregue el documento al agente que llegará a las {time}, no a mí, por favor.",
            "¿Hay {n} vehículos esperando asistencia o esa cantidad incluye el nuestro?",
            "Noté que la luz de la {side} estaba rota cuando regresé a las {time}.",
            "El operador me pidió que esperara {n} minutos antes de volver a llamar.",
        ],
        "general": [
            "¿Podría explicar cuál de estos {n} carriles lleva al centro de visitantes?",
            "El aviso dice que el paso de la {side} cierra a las {time}; ¿también incluye la rampa?",
            "Todavía no he cruzado el puente de la ruta {n}.",
            "Entregue esta dirección al guía que llegará a las {time}, por favor.",
            "¿Los {amount} wones corresponden al boleto del vehículo o al del pasajero en el ferry?",
            "Después del marcador {n}, ¿debemos mantenernos a la {side} hasta que se ensanche la vía?",
            "El mapa muestra {n} entradas, pero el guía nos indicó que evitáramos la más estrecha.",
            "Recogeré el folleto con usted a las {time} y después se lo daré al conductor.",
        ],
    },
    "ko": {
        "gas_station": [
            "주유 영수증을 {n}장 받으셨는데 이 차량 것은 한 장뿐이에요.",
            "이 카드로 {amount}원을 결제하고 영수증은 동행하신 분께 드릴게요.",
            "{fuel}를 넣기 전에 주유구 안쪽 표시를 확인해도 될까요?",
            "유리 세척 비용 {amount}원을 추가로 결제하시는 데 동의하신 건 아니에요.",
            "{side} 주유기가 {n}리터를 넣은 뒤에 멈췄어요.",
            "지금 {amount}원을 내시라는 말씀이 아니라 보증금을 설명드리는 거예요.",
            "{time}에 결제 카드를 운전자분이 아니라 직원에게 전달해 주세요.",
            "다음 손님이 쓰시기 전에 {n}번 주유기를 점검할 수 있을까요?",
        ],
        "parking": [
            "주차권을 {n}장 드렸으니 사용하지 않은 것은 돌려주세요.",
            "{n}번 자리 옆 수레를 치워 드리면 차 문을 열 수 있으실 거예요.",
            "주차비 {amount}원을 두 번 결제해 달라고 요청한 적은 없어요.",
            "{time} 이후에는 {side} 입구로 허가받은 차량만 들어갈 수 있어요.",
            "{n}번 자리의 안내판이 넘어졌다고 담당자에게 말씀해 주세요.",
            "차단기가 올라간 다음에 {amount}원을 주시면 돼요.",
            "이 가방 {n}개를 주차 관리실에 맡기시겠어요?",
            "영수증에는 {time}라고 나오지만 그보다 일찍 들어오신 기록이 있어요.",
        ],
        "rental_counter": [
            "{n}번 창구가 열리면 교체한 열쇠를 동행하신 분께 드릴게요.",
            "선택 장비를 신청하지 않으셨는데 {amount}원이 청구됐는지 확인할게요.",
            "{time}에 서명하시기 전에 {side} 좌석부터 함께 살펴봐도 될까요?",
            "예비 기기를 돌려드릴까요, 아니면 {n}번 창구에서 찾아가시겠어요?",
            "예약에는 함께 타는 어린이까지 승객 {n}명이 포함돼 있어요.",
            "추가 금액 {amount}원이 계약서 어디에 적혀 있는지 보여드릴게요.",
            "{time}에 차량을 받으실 수 있는지 문의하신 거고 아직 변경을 요청하신 건 아니죠?",
            "방금 드린 점검 서류에서 {n}장이 빠져 있어요.",
        ],
        "accident": [
            "목격자에게 받은 사진 {n}장은 렌터카 사무실로 보내드릴게요.",
            "{time}에 충돌한 뒤에는 차량을 옮기지 않았어요.",
            "{side} 운전자분께 {n}미터 정도 떨어져 있어 달라고 전해 주시겠어요?",
            "수리 견적은 {amount}원이지만 아직 결제에 동의하신 것은 아니에요.",
            "이 서류는 저에게 주지 마시고 {time}에 도착하는 경찰관에게 주세요.",
            "도움을 기다리는 차가 {n}대라는 건 이 차도 포함한 건가요?",
            "{time}에 돌아와 보니 {side} 등이 깨져 있었어요.",
            "상담원이 {n}분 뒤에 다시 연락해 달라고 했어요.",
        ],
        "general": [
            "차로 {n}개 중 어느 쪽이 안내소로 이어지는지 설명해 드릴까요?",
            "{side} 통로는 {time}에 닫는데 경사로도 포함되는지 확인해 볼게요.",
            "아직 {n}번 도로에 있는 다리를 건너신 건 아니죠?",
            "{time}에 오시는 안내원에게 이 주소를 전해 주세요.",
            "배삯 {amount}원은 차량 요금인지 승객 요금인지 따로 확인해야 해요.",
            "{n}번 표지판을 지난 뒤에는 길이 넓어질 때까지 {side}으로 가시면 돼요.",
            "지도에는 입구가 {n}개지만 안내원이 좁은 입구는 피하라고 했어요.",
            "{time}에 안내 책자를 돌려받으면 운전자분께 전달해 드릴게요.",
        ],
    },
}


def generated_sources():
    for source, target in DIRECTIONS:
        lang = source[:2]
        for scenario in SCENARIOS:
            for family, template in enumerate(TEMPLATES[lang][scenario]):
                for index, n in enumerate((2, 3, 4, 5, 6, 7, 8, 9, 11, 12)):
                    side = {"en": ("left", "right"), "es": ("izquierda", "derecha"),
                            "ko": ("왼쪽", "오른쪽")}[lang][index % 2]
                    fuel = {"en": ("gasoline", "diesel"), "es": ("gasolina", "diésel"),
                            "ko": ("휘발유", "경유")}[lang][index % 2]
                    slots = {"n": n, "side": side, "fuel": fuel, "amount": f"{(index + 2) * 3500:,}",
                             "time": f"{8 + index // 2}:{20 if index % 2 == 0 else 40}"}
                    if lang == "ko":
                        slots["time"] = f"{8 + index // 2}시 {20 if index % 2 == 0 else 40}분"
                    yield {"group_id": f"distill_{scenario}_{family}", "task": "translate",
                           "source_locale": source, "target_locale": target, "scenario": scenario,
                           "text": template.format(**slots), "source_origin": "authored_source_template"}
    for source in ("en-US", "es-US", "es-MX", "ko-KR"):
        for index in range(12):
            text = {"en": f"Please set the route to the visitor information desk in district {index + 1}.",
                    "es": f"Marque la ruta a la oficina de información del distrito {index + 1}, por favor.",
                    "ko": f"{index + 1}구역 관광 안내소로 가는 경로를 설정해 주세요."}[source[:2]]
            yield {"group_id": "distill_destination_information", "task": "destination",
                   "source_locale": source, "scenario": "general", "text": text,
                   "source_origin": "authored_source_template"}
