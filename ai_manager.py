import json
from datetime import datetime

from openai import AsyncOpenAI

class AIManager:

    def __init__(self, api_key):
        self.client = AsyncOpenAI(api_key=api_key)

    async def understand_message(
        self,
        user_text,
        conversation_state=None
    ):

        if conversation_state is None:
            conversation_state = {}

        today = datetime.now().strftime("%Y-%m-%d")
        weekday = datetime.now().strftime("%A")

        prompt = f"""
Ти модуль розуміння повідомлень для системи бронювання
барбершопу.

Сьогодні: {today}
День тижня: {weekday}

Поточний стан розмови:
{json.dumps(conversation_state, ensure_ascii=False)}

Повідомлення клієнта:
{user_text}

Твоє завдання — визначити намір клієнта.

Можливі intent:

general
booking
choose_time
confirm
cancel
cancel_booking
reschedule_booking
my_bookings
next_booking

Якщо в повідомленні є слова "скасуй", "скасувати", "відміни",
"видали запис", "не прийду" і користувач говорить про вже існуючий запис,
intent ЗАВЖДИ має бути cancel_booking.

Якщо користувач хоче ПЕРЕНЕСТИ або ЗМІНИТИ вже існуючий запис,
intent = reschedule_booking.

Для reschedule_booking розрізняй СТАРІ параметри запису
та НОВІ бажані параметри:

old_date = дата існуючого запису, якщо користувач її назвав.
old_time = час існуючого запису, якщо користувач його назвав.
new_date = нова бажана дата, якщо користувач її назвав.
new_time = новий бажаний час, якщо користувач його назвав.

Приклади:

"перенеси мій запис завтра на 5"
-> intent = reschedule_booking
-> old_date = завтра
-> new_time = "17:00"

"перенеси мою стрижку в понеділок з 4 на 5"
-> intent = reschedule_booking
-> service = haircut
-> old_date = понеділок
-> old_time = "16:00"
-> new_date = понеділок
-> new_time = "17:00"

"перенеси запис з 14:00 на 16:00"
-> intent = reschedule_booking
-> old_time = "14:00"
-> new_time = "16:00"

"перенеси мій запис на суботу о 17:00"
-> intent = reschedule_booking
-> new_date = субота
-> new_time = "17:00"

"зміни час мого запису на 18:00"
-> intent = reschedule_booking
-> new_time = "18:00"

"хочу перенести свій запис"
-> intent = reschedule_booking

Не вигадуй old_date, old_time, new_date або new_time,
якщо користувач їх не вказав.

Слова "перенеси", "перенести", "зміни запис",
"перестав запис" означають зміну ВЖЕ ІСНУЮЧОГО бронювання,
а не створення нового.

Якщо користувач просить показати всі свої записи:
"мої записи",
"покажи мої записи",
"які в мене записи",
"коли я записаний"
-> intent = my_bookings

Якщо користувач питає саме про найближчий або наступний запис:
"коли в мене наступний запис",
"мій наступний запис",
"коли я наступний раз записаний",
"який мій найближчий запис"
-> intent = next_booking
Приклади:

"скасуй всі сьогоднішні записи"
-> intent = cancel_booking, date = сьогоднішня дата

"скасуй записи завтра"
-> intent = cancel_booking, date = завтрашня дата

"скасуй стрижку сьогодні"
-> intent = cancel_booking, service = haircut, date = сьогоднішня дата

"скасуй запис на 16:00"
-> intent = cancel_booking, time = "16:00"

"скасуй всі записи"
-> intent = cancel_booking

Послуги:

haircut = Стрижка
beard = Борода
combo = Стрижка + борода

Поверни ТІЛЬКИ JSON такого формату:

ВАЖЛИВО ПРО ДАТИ:

Усі поля date, old_date та new_date ЗАВЖДИ повертай
у форматі YYYY-MM-DD.

НІКОЛИ не повертай у JSON слова:
"сьогодні",
"завтра",
"післязавтра",
"понеділок",
"вівторок",
"середа",
"четвер",
"п'ятниця",
"субота",
"неділя".

Перетворюй їх у конкретну календарну дату,
використовуючи сьогоднішню дату та день тижня,
які вказані на початку prompt.

Наприклад, якщо найближчий понеділок — 2026-09-21:
"в понеділок" -> "2026-09-21".

Для intent = my_bookings:
service = null
date = null
time = null
after_time = null
old_date = null
old_time = null
new_date = null
new_time = null

Для intent = next_booking:
service = null
date = null
time = null
after_time = null
old_date = null
old_time = null
new_date = null
new_time = null

Не перенось параметри попереднього бронювання
у my_bookings або next_booking.

{{
  "intent": "booking",
  "service": null,
  "date": null,
  "time": null,
  "after_time": null,
  "old_date": null,
  "old_time": null,
  "new_date": null,
  "new_time": null
}}

Правила:

1. Не вигадуй відсутню інформацію.
2. Якщо значення невідоме — null.
3. "підстригтися" означає haircut.
4. "борода" означає beard.
5. "стрижка і борода" означає combo.
6. Розумій:
   сьогодні,
   завтра,
   післязавтра,
   понеділок,
   вівторок,
   середу,
   четвер,
   п'ятницю,
   суботу.
7. "після 16" означає after_time = "16:00".
8. "о 16" означає time = "16:00".
9. Якщо користувач відповідає лише часом,
intent = choose_time.
Якщо написано "1" -> time = "13:00".
Якщо написано "2" -> time = "14:00".
Якщо написано "3" -> time = "15:00".
Якщо написано "4" -> time = "16:00".
Якщо написано "5" -> time = "17:00".
Якщо написано "6" -> time = "18:00".
Якщо написано "7" -> time = "19:00".
Якщо написано "14", "16", "18" то це відповідно
"14:00", "16:00", "18:00".
Не втрачай service і date з поточного стану розмови.
10. "так", "підтверджую", "давай"
    можуть означати confirm, якщо перед цим
    уже сформований запис.
11. "ні", "скасувати", "відміна"
    означають cancel.
12. Якщо користувач хоче скасувати ВЖЕ ІСНУЮЧИЙ запис,
наприклад:
"скасуй мій запис",
"відміни мій запис",
"видали мій запис",
"не прийду на стрижку",
"скасуй запис у понеділок",
то intent = cancel_booking.

Якщо користувач просто відмовляється від ПОТОЧНОГО процесу створення запису,
наприклад "ні", "скасувати", "відміна",
то intent = cancel.

13. Поверни тільки валідний JSON.
"""

        response = await self.client.responses.create(
            model="gpt-5.6-luna",
            input=prompt
        )

        text = response.output_text.strip()

        # На випадок, якщо модель обгорне JSON у markdown.
        if text.startswith("```"):
            text = text.replace("```json", "")
            text = text.replace("```", "")
            text = text.strip()

        try:
            return json.loads(text)

        except json.JSONDecodeError:

            print(
                "AI JSON ERROR. Отримано:",
                text
            )

            return {
                "intent": "general",
                "service": None,
                "date": None,
                "time": None,
                "after_time": None
            }