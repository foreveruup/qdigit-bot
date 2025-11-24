import time
import re
import random
import pandas as pd
from main import WhatsAppBot   # <-- имя твоего файла с классом бота


EXCEL_PATH = "dent-clients.xlsx"
SHEET_NAME = "Sheet1"


def normalize_chat_id(raw_phone: str) -> str | None:
    """Переводим номер в формат 7707xxxxxxx@c.us"""
    if not raw_phone:
        return None

    raw_phone = str(raw_phone).strip()

    # уже готовый chatId?
    if raw_phone.endswith("@c.us"):
        return raw_phone

    digits = re.sub(r"\D", "", raw_phone)
    if len(digits) < 7:
        return None

    return f"{digits}@c.us"


def make_message(brand: str) -> str:
    """Рандомизируем приветствие + мягкий оффер."""
    greetings = [
        "Добрый день! 😊\n\n",
        "Здравствуйте! 😊\n\n",
        "Приветствую! 👋\n\n",
        "Добрый день! 🌿\n\n"
    ]

    intros = [
        f"На связи команда *{brand}*. ",
        f"Пишет команда *{brand}* из Казахстана. ",
        f"Это команда *{brand}*. ",
    ]

    services = [
        "Мы помогаем стоматологиям с WhatsApp-ботами консультантами, автоматизацией записи пациентов и настройке автоворонок.\n\n",
        "Помогаем клиникам увеличивать поток пациентов через WhatsApp-ботов и автоматизацию.\n\n",
        "Делаем для стоматологий чат-боты, автоворонки, напоминания и онлайн-запись.\n\n",
    ]

    cta = [
        "Если хотите пример под вашу клинику — давайте созвонимся, менеджер подберёт подходящий сценарий 🔥",
        "Готовы помочь с проектом — можем назначить консультацию, менеджер всё расскажет и подберёт решение 😊",
        "Если хотите посмотреть, как это работает — запишитесь на консультацию, наш менеджер свяжется с вами и мы приступим к обсуждению проекта для вас 🙌",
    ]

    return (
        random.choice(greetings)
        + random.choice(intros)
        + random.choice(services)
        + random.choice(cta)
    )


def main():
    bot = WhatsAppBot()
    brand = bot.brand

    df = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_NAME)

    if "Номер" not in df.columns:
        print("❌ В таблице нет колонки 'Номер'")
        return

    # перемешиваем порядок
    df = df.sample(frac=1).reset_index(drop=True)

    success, failed = 0, 0

    for idx, row in df.iterrows():
        raw_phone = row["Номер"]
        chat_id = normalize_chat_id(raw_phone)

        if not chat_id:
            print(f"[SKIP] плохой номер: {raw_phone}")
            failed += 1
            continue

        message = make_message(brand)

        print(f"[SEND] {chat_id} → {message[:50]}...")
        ok = bot.send_message(chat_id, message)

        if ok:
            success += 1
        else:
            failed += 1

        # Антибан-пауза
        time.sleep(random.uniform(15, 35))

    print(f"\nГотово 👍\nУспешно: {success}\nОшибок: {failed}")


if __name__ == "__main__":
    main()