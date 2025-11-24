import os
import requests
import json
import time
import re
import logging
from datetime import datetime
from dotenv import load_dotenv
from typing import Optional
from openai import OpenAI

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('whatsapp_bot')


class WhatsAppBot:
    def __init__(self):
        self.instance_id = os.environ.get("INSTANCE_ID")
        self.api_token = os.environ.get("INSTANCE_TOKEN")
        self.base_url = f"https://api.green-api.com/waInstance{self.instance_id}"

        # ДЕФОЛТЫ, чтобы не было None в тексте
        self.brand = os.environ.get("BRAND_NAME") or "qdigit"
        self.support_phone = os.environ.get("SUPPORT_PHONE") or "+7 777 777 77 77"

        # Прайс — публичный прямой URL (см. инструкцию ниже) + дефолтное имя
        self.price_url = os.environ.get("PRICE_FILE_URL")
        self.price_filename = os.environ.get("PRICE_FILE_NAME") or "qdigit_price.pdf"

        # OpenAI
        self.api_key = os.environ.get("OPENAI_API_KEY")
        self.client = OpenAI(api_key=self.api_key)
        self.openai_model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

        if not all([self.instance_id, self.api_token, self.api_key]):
            raise ValueError("Не заданы переменные окружения: INSTANCE_ID/INSTANCE_TOKEN/OPENAI_API_KEY")

        # Хранилище выбранного языка для каждого чата
        self.user_language = {}  # {chat_id: 'ru'/'kk'/'en'}

        # ✅ СТАРОЕ: флаг ожидания формы (больше не используем для парсинга, но оставим для совместимости)
        self.awaiting_form = {}  # {chat_id: True/False}

        # ✅ НОВОЕ: пошаговая форма консультации
        # {chat_id: {"step": 1..4, "data": {"name":..., "company":..., "phone":..., "bot_type":...}}}
        self.form_state = {}

        # Системные промпты (RU/KK/EN) — всегда говорить от лица бренда и кратко
        self.system_prompts = {
            'ru': f"""Ты — тёплый и компетентный консультант компании {self.brand} (Казахстан).
Всегда начинай первое предложение с упоминания компании {self.brand}.
Говори кратко: максимум 4–5 пунктов или 3 коротких абзаца, без «простыней».

НАШИ УСЛУГИ (предлагай уместно):
• Лендинги и сайты
• Аналитика и дашборды
• Автоматизация и интеграции
• Чат-боты (WA/TG), оплаты, CRM
• Маркетинг, SEO, контекст
• ИИ (ассистенты, генерация, поиск)

ПРАВИЛА:
• Цены только в тенге (₸).
• Если спрашивают прайс — предложи и отправь файл прайса (файл отправляет система).
• Если просят поддержку — дай номер и WhatsApp.
• Если не уверен — задай 1 уточняющий вопрос.
• Коротко, дружелюбно, по делу. 1–2 эмодзи.
• Используй маркеры (•) и короткие строки.""",

            'kk': f"""{self.brand} компаниясының жылы әрі білікті кеңесшісісіз (Қазақстан).
Алғашқы сөйлемде міндетті түрде {self.brand} атауын айтыңыз.
Қысқа жазыңыз: ең көбі 4–5 тармақ немесе 3 қысқа абзац.

ҚЫЗМЕТТЕР:
• Лендингтер/сайттар
• Аналитика, дашбордтар
• Автоматтандыру және интеграциялар
• Чат-боттар (WA/TG), төлемдер, CRM
• Маркетинг, SEO, контекст
• ЖИ (көмекшілер, генерация, іздеу)

ЕРЕЖЕЛЕР:
• Бағалар тек теңгемен (₸).
• Баға сұраса — прайс файлын ұсыныңыз (файлды жүйе жібереді).
• Қолдау керек болса — біздің нөмірді беріңіз.
• Қысқа әрі нақты болыңыз. 1–2 эмодзи.""",

            'en': f"""You are a warm, competent consultant of {self.brand} (Kazakhstan).
Always start the first sentence by mentioning {self.brand}.
Keep it brief: max 4–5 bullets or 3 short paragraphs.

SERVICES:
• Landing pages & websites
• Analytics & dashboards
• Automation & integrations
• Chatbots (WA/TG), payments, CRM
• Marketing, SEO, PPC
• AI (assistants, generation, search)

RULES:
• Prices in KZT (₸) only.
• When asked for pricing — offer and send the price file (system sends the file).
• If support is requested — share our phone & WhatsApp.
• Ask 1 clarifying question if unsure.
• Be concise and friendly. 1–2 emojis."""
        }

        self.processed_messages = set()
        self.history = {}
        self.last_reply = {}

        # ==== (опционально) Быстрая самопроверка ссылки прайса
        self._check_price_link()

    # === ВЫБОР ЯЗЫКА ===

    def is_greeting(self, text: str) -> bool:
        t = (text or "").lower().strip()
        ru_greetings = {'привет', 'здравствуй', 'здравствуйте', 'салам', 'здорово',
                        'добрый день', 'добрый вечер', 'доброе утро', 'прив', 'здраст',
                        'дратути', 'хай', 'приветик', 'приветствую'}
        kk_greetings = {'сәлем', 'салам', 'сәлеметсіз бе', 'қайырлы таң', 'қайырлы күн', 'қайырлы кеш'}
        en_greetings = {'hi', 'hello', 'hey', 'good morning', 'good day', 'good evening', 'greetings', 'hiya', 'howdy'}
        all_greetings = ru_greetings | kk_greetings | en_greetings
        base = t.replace('!', '').replace(',', '').strip()
        return t in all_greetings or base in all_greetings

    # === ЕДИНОЕ ПРИВЕТСТВИЕ + КНОПКИ (после выбора языка) ===
    def send_welcome_with_actions(self, chat_id: str, lang_code: str) -> bool:
        """
        Отправляет одно сообщение с приветствием и интерактивными кнопками:
        Прайс / Консультация / Наши услуги.
        """
        url = f"{self.base_url}/sendInteractiveButtonsReply/{self.api_token}"

        bodies = {
            'ru': (
                f"👋 Здравствуйте! Вас приветствует *{self.brand}*.\n"
                "Мы делаем чат-боты, автоматизацию и сайты для бизнеса в Казахстане.\n\n"
                "Чем помочь? Выберите действие ниже:"
            ),
            'kk': (
                f"👋 Сәлеметсіз бе! Сізді *{self.brand}* қарсы алады.\n"
                "Біз Қазақстандағы бизнеске чат-боттар, автоматтандыру және сайттар жасаймыз.\n\n"
                "Қалай көмектесейін? Төменнен таңдаңыз:"
            ),
            'en': (
                f"👋 Hello! *{self.brand}* here.\n"
                "We build chatbots, automation and websites for businesses in Kazakhstan.\n\n"
                "How can we help? Pick an option:"
            )
        }
        labels = {
            'ru': {"price": "📄 Прайс", "consult": "📞 Консультация", "services": "💬 Наши услуги"},
            'kk': {"price": "📄 Прайс", "consult": "📞 Кеңес алу", "services": "💬 Қызметтер"},
            'en': {"price": "📄 Pricing", "consult": "📞 Consultation", "services": "💬 Services"},
        }
        body = bodies.get(lang_code, bodies['en'])
        l = labels.get(lang_code, labels['en'])

        payload = {
            "chatId": chat_id,
            "header": " ",
            "body": body,
            "footer": self.brand,
            "buttons": [
                {"buttonId": "get_price", "buttonText": l["price"]},
                {"buttonId": "book_consult", "buttonText": l["consult"]},
                {"buttonId": "short_services", "buttonText": l["services"]},
            ],
        }

        try:
            r = requests.post(url, json=payload, timeout=30)
            ok = r.status_code == 200
            if not ok:
                logger.error(f"Ошибка send_welcome_with_actions: {r.status_code} {r.text}")
            return ok
        except Exception as e:
            logger.error(f"Ошибка отправки welcome+actions: {e}")
            return False

    def send_language_selection(self, chat_id: str) -> bool:
        url = f"{self.base_url}/sendInteractiveButtonsReply/{self.api_token}"
        body = (
            "👋 *Здравствуйте!* Вас приветствует компания *{brand}*.\n"
            "👋 *Сәлеметсіз бе!* Сізді *{brand}* компаниясы қарсы алады.\n"
            "👋 *Hello!* You're welcomed by *{brand}*.\n\n"
            "Пожалуйста, выберите удобный язык общения:\n"
            "Өзіңізге ыңғайлы тілді таңдаңыз:\n"
            "Please choose your language:"
        ).format(brand=self.brand)

        payload = {
            "chatId": chat_id,
            "header": " ",
            "body": body,
            "footer": self.brand,
            "buttons": [
                {"buttonId": "lang_ru", "buttonText": "🇷🇺 Русский"},
                {"buttonId": "lang_kk", "buttonText": "🇰🇿 Қазақша"},
                {"buttonId": "lang_en", "buttonText": "🇬🇧 English"}
            ]
        }

        fallback = (
            f"👋 *Здравствуйте!* Вас приветствует компания *{self.brand}*.\n"
            "👋 *Сәлеметсіз бе!* Сізді *{brand}* компаниясы қарсы алады.\n"
            "👋 *Hello!* You're welcomed by *{brand}*.\n\n"
            "1️⃣ Русский 🇷🇺\n"
            "2️⃣ Қазақша 🇰🇿\n"
            "3️⃣ English 🇬🇧\n\n"
            "_Напишите цифру / Санды жазыңыз / Type number_"
        ).replace("{brand}", self.brand)

        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code == 200:
                logger.info(f"✅ Отправлены кнопки выбора языка для {chat_id}")
                return True
            else:
                logger.error(f"Ошибка отправки кнопок: {r.status_code} {r.text}")
                self.send_message(chat_id, fallback)
                return False
        except Exception as e:
            logger.error(f"Ошибка отправки кнопок: {e}")
            self.send_message(chat_id, fallback)
            return False

    def set_language(self, chat_id: str, lang_code: str):
        self.user_language[chat_id] = lang_code
        logger.info(f"🌍 Язык для {chat_id} установлен: {lang_code}")
        try:
            filename = "user_languages.json"
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    langs = json.load(f)
            else:
                langs = {}
            langs[chat_id] = lang_code
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(langs, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения языка: {e}")

    def load_user_languages(self):
        try:
            filename = "user_languages.json"
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    self.user_language = json.load(f)
                logger.info(f"Загружено языков: {len(self.user_language)}")
        except Exception as e:
            logger.error(f"Ошибка загрузки языков: {e}")

    # === УТИЛИТЫ ===

    def _extract_text(self, message_data: dict) -> str:
        if not message_data:
            return ""
        t = message_data.get("textMessageData", {}).get("textMessage")
        if t:
            return t
        t = message_data.get("extendedTextMessageData", {}).get("text")
        if t:
            return t
        t = message_data.get("message", "")
        if t:
            return t
        t = message_data.get("caption", "")
        if t:
            return t
        return ""

    def _normalize_text(self, text: str) -> str:
        return (text or "").replace("\u200b", "").replace("\xa0", " ").strip()

    def clear_chat_history(self, chat_id: str):
        if chat_id in self.history:
            del self.history[chat_id]
        if chat_id in self.last_reply:
            del self.last_reply[chat_id]
        if chat_id in self.user_language:
            del self.user_language[chat_id]
        if chat_id in self.awaiting_form:
            del self.awaiting_form[chat_id]
        # ✅ чистим и пошаговую форму
        if chat_id in self.form_state:
            del self.form_state[chat_id]
        logger.info(f"История чата {chat_id} очищена")

    def send_message(self, chat_id: str, message: str) -> bool:
        url = f"{self.base_url}/sendMessage/{self.api_token}"
        payload = {"chatId": chat_id, "message": message}
        try:
            r = requests.post(url, json=payload, timeout=10)
            ok = r.status_code == 200
            if not ok:
                logger.error("Ошибка отправки: %s %s", r.status_code, r.text)
            return ok
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
            return False

    def send_file_by_url(self, chat_id: str, file_url: str, file_name: str, caption: str = "") -> bool:
        url = f"{self.base_url}/sendFileByUrl/{self.api_token}"
        payload = {"chatId": chat_id, "urlFile": file_url, "fileName": file_name, "caption": caption or ""}
        try:
            r = requests.post(url, json=payload, timeout=15)
            ok = r.status_code == 200
            if not ok:
                logger.error("Ошибка отправки файла: %s %s", r.status_code, r.text)
            return ok
        except Exception as e:
            logger.error(f"Ошибка отправки файла: {e}")
            return False

    def get_notification(self) -> Optional[dict]:
        url = f"{self.base_url}/receiveNotification/{self.api_token}"
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                return r.json()
            logger.error("receiveNotification %s %s", r.status_code, r.text)
            return None
        except Exception as e:
            logger.error(f"Ошибка получения уведомлений: {e}")
            return None

    def delete_notification(self, receipt_id: int) -> bool:
        url = f"{self.base_url}/deleteNotification/{self.api_token}/{receipt_id}"
        try:
            r = requests.delete(url, timeout=10)
            ok = r.status_code == 200
            if not ok:
                logger.error("deleteNotification %s %s", r.status_code, r.text)
            return ok
        except Exception as e:
            logger.error(f"Ошибка удаления уведомления: {e}")
            return False

    # === LLM ===
    def get_openai_response(self, chat_id: str, user_message: str) -> str:
        lang_code = self.user_language.get(chat_id, 'ru')
        system_prompt = self.system_prompts.get(lang_code, self.system_prompts['ru'])

        hist = self.history.setdefault(chat_id, [])
        hist.append({"role": "user", "content": user_message})
        window = hist[-12:]

        style_rules = {
            'ru': "Говори коротко, дружелюбно и по делу. Используй 1–2 эмодзи.",
            'kk': "Қысқа, достық және нақты. 1–2 эмодзи.",
            'en': "Be brief, friendly, to the point. Use 1–2 emojis."
        }
        system = system_prompt + "\n\nСТИЛЬ:\n" + style_rules.get(lang_code, style_rules['en'])
        messages = [{"role": "system", "content": system}] + window

        try:
            resp = self.client.chat.completions.create(
                model=self.openai_model,
                messages=messages,
                max_tokens=220,
                temperature=0.7,
                top_p=0.9,
                frequency_penalty=0.6,
                presence_penalty=0.4
            )
            answer = resp.choices[0].message.content.strip()
            hist.append({"role": "assistant", "content": answer})
            self.history[chat_id] = hist[-24:]
            logger.info(f"🧠 GPT ответил: {answer[:80]}...")
            return answer
        except Exception as e:
            logger.error(f"Ошибка OpenAI: {e}")
            error_messages = {
                'ru': "Простите, произошёл технический сбой. Попробуйте ещё раз через минуту 🙏",
                'kk': "Кешіріңіз, техникалық ақау орын алды. Бір минуттан кейін қайталап көріңіз 🙏",
                'en': "Sorry, a technical error occurred. Please try again in a minute 🙏"
            }
            return error_messages.get(lang_code, error_messages['en'])

    # === МАРШРУТИЗАЦИЯ ===
    def route_intent(self, text: str, lang_code: str, chat_id: str = None) -> Optional[str]:
        """
        Маршрутизация по быстрым интентам (прайс, поддержка, консультация).
        Для консультации теперь запускаем пошаговую форму.
        """
        t = (text or "").lower().strip()

        price_kw = {
            'ru': ["цена", "стоимость", "прайс", "сколько стоит", "прайслист", "прайс-лист", "ценник",
                   "давай", "давайте", "скинь", "скиньте", "пришли", "прайс пожалуйста", "прайс пж", "ок", "окей"],
            'kk': ["баға", "құны", "прайс", "иә", "болсын", "жібер", "жібере сал", "ок"],
            'en': ["price", "pricing", "cost", "how much", "pricelist", "send price", "ok", "okay", "yes",
                   "share price"]
        }
        if any(k in t for k in price_kw.get(lang_code, [])):
            return "__INTENT_PRICE__"

        support_kw = {
            'ru': ["поддержк", "саппорт", "техпод", "help", "support", "помощь", "свяжитесь"],
            'kk': ["қолдау", "көмек", "support"],
            'en': ["support", "help", "contact", "assist"]
        }
        if any(k in t for k in support_kw.get(lang_code, [])):
            note = {
                'ru': f"Наш номер поддержки: {self.support_phone}\nНапишите в WhatsApp — быстро ответим. 📞",
                'kk': f"Біздің қолдау нөмірі: {self.support_phone}\nWhatsApp-қа жазыңыз — жылдам жауап береміз. 📞",
                'en': f"Our support number: {self.support_phone}\nWrite on WhatsApp — we'll reply quickly. 📞"
            }
            return note.get(lang_code, note['en'])

        consult_keywords = {
            'ru': ["записаться", "консультац", "созвон", "перезвон", "запишите меня", "запишите"],
            'kk': ["жазылу", "кеңес", "қоңырау", "жазыңыз мені"],
            'en': ["schedule", "consultation", "appointment", "call me", "book"]
        }

        if any(kw in t for kw in consult_keywords.get(lang_code, [])):
            # ✅ Запускаем пошаговую форму
            if chat_id:
                self.form_state[chat_id] = {"step": 1, "data": {}}
            forms_start = {
                'ru': "📞 *Давайте согласуем консультацию!*\n\nКак вас зовут? 🙂",
                'kk': "📞 *Кеңесті келісейік!*\n\nАтыңыз кім? 🙂",
                'en': "📞 *Let's arrange your consultation!*\n\nWhat is your name? 🙂",
            }
            return forms_start.get(lang_code, forms_start['en'])

        return None

    # === СОХРАНЕНИЕ КЛИЕНТА ===
    def save_client_data(self, phone: str, data: dict) -> bool:
        """Локально JSON + (опционально) запись в Google Sheets/CSV (см. ниже)."""
        try:
            filename = "client_records.json"
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    clients = json.load(f)
            else:
                clients = {}

            clients[phone] = {**data, 'recorded_at': datetime.now().isoformat(), 'status': 'new'}

            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(clients, f, ensure_ascii=False, indent=2)

            # Доп. канал — Google Sheets / CSV
            self._persist_to_sheets_and_csv(clients[phone])

            logger.info(f"Записан клиент {phone}: {data.get('name', 'Без имени')}")
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")
            return False

    def _persist_to_sheets_and_csv(self, row: dict):
        """Опционально: отправка в Google Sheets (если настроено), + append в CSV."""
        # CSV (просто и полезно для Excel)
        try:
            import csv
            csv_exists = os.path.exists("client_records.csv")
            with open("client_records.csv", "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["recorded_at", "name", "company", "phone", "bot_type", "status"])
                if not csv_exists:
                    writer.writeheader()
                writer.writerow({
                    "recorded_at": row.get("recorded_at"),
                    "name": row.get("name"),
                    "company": row.get("company"),
                    "phone": row.get("phone"),
                    "bot_type": row.get("bot_type"),
                    "status": row.get("status", "new"),
                })
        except Exception as e:
            logger.warning(f"Ошибка записи в CSV: {e}")

        # Google Sheets (если заданы переменные)
        try:
            g_enable = os.environ.get("GOOGLE_SHEETS_ENABLED", "").lower() == "true"
            creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
            sheet_name = os.environ.get("GOOGLE_SHEETS_SPREADSHEET")
            worksheet = os.environ.get("GOOGLE_SHEETS_WORKSHEET", "Leads")

            if g_enable and creds_json and sheet_name:
                import gspread
                from google.oauth2.service_account import Credentials

                creds_dict = json.loads(creds_json)
                scopes = [
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ]
                credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
                gc = gspread.authorize(credentials)
                sh = gc.open(sheet_name)

                # Создать лист, если его нет
                if worksheet not in [w.title for w in sh.worksheets()]:
                    ws = sh.add_worksheet(title=worksheet, rows=1000, cols=10)
                else:
                    ws = sh.worksheet(worksheet)

                # Заголовки (всегда на первой строке)
                headers = ["Дата", "Имя", "Компания", "Телефон", "Задача", "Источник", "Статус"]
                first_row = ws.row_values(1)
                if not first_row or first_row != headers:
                    ws.update("A1:G1", [headers])
                    logger.info("🧾 Заголовок таблицы обновлён")

                # Добавляем новую строку
                ws.append_row([
                    datetime.now().strftime("%d.%m.%Y %H:%M"),
                    row.get("name"),
                    row.get("company"),
                    row.get("phone"),
                    row.get("bot_type"),
                    "WhatsApp",
                    row.get("status", "new"),
                ], value_input_option="USER_ENTERED")

                logger.info("✅ Добавлено в Google Sheets")
        except Exception as e:
            logger.warning(f"Google Sheets недоступен или не настроен: {e}")

    # === СТАРЫЙ extract_client_info можно оставить, но он больше не нужен для консультации ===
    def extract_client_info(self, text: str, lang_code: str) -> dict:
        """
        Оставлено для совместимости (если решишь где-то ещё использовать),
        но пошаговая форма консультации работает без этого.
        """
        info = {}
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        keywords = {
            'ru': {'name': ['имя:', 'name:'], 'company': ['компания:', 'company:'],
                   'phone': ['телефон:', 'phone:'], 'task': ['задача:', 'задач']},
            'kk': {'name': ['аты:', 'name:'], 'company': ['компания:', 'company:'],
                   'phone': ['телефон:', 'phone:'], 'task': ['міндет:', 'міндет']},
            'en': {'name': ['name:'], 'company': ['company:'], 'phone': ['phone:'], 'task': ['task:']}
        }
        kw = keywords.get(lang_code, keywords['en'])

        for raw_line in lines:
            low = raw_line.lower()
            if any(k in low for k in kw['name']):
                info['name'] = raw_line.split(':', 1)[1].strip()
            elif any(k in low for k in kw['company']):
                info['company'] = raw_line.split(':', 1)[1].strip()
            elif any(k in low for k in kw['phone']):
                info['phone'] = raw_line.split(':', 1)[1].strip()
            elif any(k in low for k in kw['task']):
                info['bot_type'] = raw_line.split(':', 1)[1].strip()

        if info.get("name") and info.get("phone") and info.get("bot_type"):
            return info

        phone_pattern = re.compile(r'[\+\d\(\)\-\s]{7,}')

        for idx, line in enumerate(lines):
            if phone_pattern.search(line) and not info.get('phone'):
                info['phone'] = line
                continue

            if not info.get('name') and not any(c.isdigit() for c in line):
                info['name'] = line
                continue

            if info.get('name') and info.get('phone'):
                if not info.get('company'):
                    info['company'] = line
                elif not info.get('bot_type'):
                    info['bot_type'] = line

        if not info.get('company') and info.get('name'):
            info['company'] = "—"

        return info

    # === НОВОЕ: обработка шагов формы консультации ===
    def handle_form_step(self, chat_id: str, phone: str, message_text: str, lang_code: str):
        """
        Пошаговый опрос: Имя -> Компания (необязательно) -> Телефон -> Задача
        """
        state = self.form_state.get(chat_id)
        if not state:
            return

        step = state.get("step", 1)
        data = state.setdefault("data", {})

        txt = message_text.strip()

        # Шаг 1 — имя
        if step == 1:
            if len(txt) < 2:
                msgs = {
                    'ru': "Не расслышал имя, напишите, пожалуйста, как вас зовут 🙂",
                    'kk': "Атыңызды толық жазыңызшы 🙂",
                    'en': "I didn't catch your name, please write it again 🙂"
                }
                self.send_message(chat_id, msgs.get(lang_code, msgs['en']))
                return

            data["name"] = txt
            state["step"] = 2

            msgs = {
                'ru': "Отлично, {name}! Теперь укажите *название вашей компании* "
                      "(если нет — напишите прочерк или «нет»):",
                'kk': "Жақсы, {name}! Енді *компания атауын* жазыңыз "
                      "(егер жоқ болса — сызықша немесе «жоқ» деп жазыңыз):",
                'en': "Great, {name}! Now please write your *company name* "
                      "(if none — type a dash or 'none'):",
            }
            self.send_message(chat_id, msgs.get(lang_code, msgs['en']).format(name=data["name"]))
            return

        # Шаг 2 — компания (необязательно)
        if step == 2:
            if txt.lower() in {"", "-", "—", "нет", "no", "none", "жоқ"}:
                data["company"] = "—"
            else:
                data["company"] = txt

            state["step"] = 3

            msgs = {
                'ru': "Укажите, пожалуйста, ваш номер телефона 📱",
                'kk': "Енді телефон нөміріңізді жазыңыз 📱",
                'en': "Please share your phone number 📱",
            }
            self.send_message(chat_id, msgs.get(lang_code, msgs['en']))
            return

        # Шаг 3 — телефон
        if step == 3:
            # Простая проверка: хотя бы 7 цифр
            digits = re.sub(r"\D", "", txt)
            if len(digits) < 7:
                msgs = {
                    'ru': "Похоже, номер короткий. Пришлите, пожалуйста, номер полностью (с кодом):",
                    'kk': "Нөмір қысқа сияқты. Толық нөмірді (кодымен бірге) жазыңызшы:",
                    'en': "The number seems too short. Please send the full phone number (with code):",
                }
                self.send_message(chat_id, msgs.get(lang_code, msgs['en']))
                return

            data["phone"] = txt
            state["step"] = 4

            msgs = {
                'ru': "Круто! Теперь кратко опишите задачу: что вам нужно — сайт, бот, автоматизация, маркетинг? 🙂",
                'kk': "Керемет! Енді қысқаша жазыңыз: не қажет — сайт, бот, автоматтандыру, маркетинг? 🙂",
                'en': "Nice! Now briefly describe your task: website, bot, automation, marketing, etc.? 🙂",
            }
            self.send_message(chat_id, msgs.get(lang_code, msgs['en']))
            return

        # Шаг 4 — задача
        if step == 4:
            data["bot_type"] = txt

            # Сохраняем
            if self.save_client_data(phone, data):
                msgs = {
                    'ru': ("✅ *Записал вас на бесплатную консультацию!*\n\n"
                           f"👤 Имя: {data.get('name')}\n"
                           f"🏢 Компания: {data.get('company')}\n"
                           f"📱 Телефон: {data.get('phone')}\n"
                           f"🧩 Задача: {data.get('bot_type')}\n\n"
                           "Наш менеджер свяжется с вами в ближайшее время 🙌"),
                    'kk': ("✅ *Сізді тегін кеңеске жаздым!*\n\n"
                           f"👤 Аты: {data.get('name')}\n"
                           f"🏢 Компания: {data.get('company')}\n"
                           f"📱 Телефон: {data.get('phone')}\n"
                           f"🧩 Міндет: {data.get('bot_type')}\n\n"
                           "Менеджер жақын арада хабарласады 🙌"),
                    'en': ("✅ *You're booked for a free consultation!*\n\n"
                           f"👤 Name: {data.get('name')}\n"
                           f"🏢 Company: {data.get('company')}\n"
                           f"📱 Phone: {data.get('phone')}\n"
                           f"🧩 Task: {data.get('bot_type')}\n\n"
                           "Our manager will reach out soon 🙌")
                }
                self.send_message(chat_id, msgs.get(lang_code, msgs['en']))

            # Чистим состояние формы
            if chat_id in self.form_state:
                del self.form_state[chat_id]
            return

    # === ОБРАБОТКА СООБЩЕНИЙ ===
    def process_message(self, notification: dict):
        try:
            if not notification:
                return
            receipt_id = notification.get('receiptId')
            body = notification.get('body', {})
            if not body:
                return

            type_webhook = body.get('typeWebhook', '')
            message_id = body.get('idMessage')

            if message_id and message_id in self.processed_messages:
                if receipt_id:
                    self.delete_notification(receipt_id)
                return

            if type_webhook != 'incomingMessageReceived':
                if receipt_id:
                    self.delete_notification(receipt_id)
                return

            message_data = body.get('messageData', {})
            sender_data = body.get('senderData', {})
            chat_id = sender_data.get('chatId', '')
            phone = sender_data.get('sender', '')

            if not chat_id:
                if receipt_id:
                    self.delete_notification(receipt_id)
                return

            if message_data.get('typeMessage') in ('textMessage', 'extendedTextMessage') or \
                    ('textMessageData' in message_data or 'extendedTextMessageData' in message_data):

                raw_text = self._extract_text(message_data)
                message_text = self._normalize_text(raw_text)

                if not message_text:
                    logger.warning(f"Пустой текст при входящем сообщении. type={message_data.get('typeMessage')}")
                    if chat_id not in self.user_language:
                        self.send_language_selection(chat_id)
                    else:
                        self.send_message(chat_id, "Не расслышал сообщение. Напишите, пожалуйста, ещё раз 🙂")
                    self.processed_messages.add(message_id)
                    if receipt_id:
                        self.delete_notification(receipt_id)
                    return

                logger.info(f"📩 Текстовое сообщение от {phone}: {message_text}")

                # === ADMIN
                if message_text.strip().startswith('/clients'):
                    if phone.replace('+', '') in {"77776463138"}:
                        self.handle_clients_command(chat_id)
                    else:
                        self.send_message(chat_id, "У вас нет доступа к этой команде")
                    self.processed_messages.add(message_id)
                    if receipt_id:
                        self.delete_notification(receipt_id)
                    return

                if message_text.strip() == '/reset':
                    if phone.replace('+', '') in {"77776463138"}:
                        self.clear_chat_history(chat_id)
                        self.send_message(chat_id, "✅ История чата очищена")
                    self.processed_messages.add(message_id)
                    if receipt_id:
                        self.delete_notification(receipt_id)
                    return

                # === ЯЗЫК
                if chat_id not in self.user_language:
                    if message_text.strip() in ['1', '2', '3']:
                        lang_map = {'1': 'ru', '2': 'kk', '3': 'en'}
                        lang_code = lang_map[message_text.strip()]
                        self.set_language(chat_id, lang_code)
                        self.send_welcome_with_actions(chat_id, lang_code)
                    elif self.is_greeting(message_text):
                        self.send_language_selection(chat_id)
                    else:
                        logger.info(f"⏸️ Игнорируем сообщение до выбора языка: {message_text[:50]}")
                    self.processed_messages.add(message_id)
                    if receipt_id:
                        self.delete_notification(receipt_id)
                    return

                lang_code = self.user_language[chat_id]

                # ✅ ЕСЛИ ПОЛЬЗОВАТЕЛЬ СЕЙЧАС В ПРОЦЕССЕ ЗАПОЛНЕНИЯ ФОРМЫ — ОБРАБАТЫВАЕМ ШАГ
                if chat_id in self.form_state:
                    self.handle_form_step(chat_id, phone, message_text, lang_code)
                    self.processed_messages.add(message_id)
                    if receipt_id:
                        self.delete_notification(receipt_id)
                    return

                # === Быстрая маршрутизация
                quick = self.route_intent(message_text, lang_code, chat_id)
                if quick:
                    if quick == "__INTENT_PRICE__":
                        self._send_price(chat_id, lang_code)
                    else:
                        self.send_message(chat_id, quick)
                    self.processed_messages.add(message_id)
                    if receipt_id:
                        self.delete_notification(receipt_id)
                    return

                # === Ответ через GPT
                response = self.get_openai_response(chat_id, message_text)
                self.send_message(chat_id, response)

                self.processed_messages.add(message_id)
                if receipt_id:
                    self.delete_notification(receipt_id)
                return

            # === КНОПКИ
            elif message_data.get('typeMessage') == 'interactiveButtonsResponse':
                reply_data = message_data.get('interactiveButtonsResponse', {})
                selected_button = reply_data.get('selectedButtonId', '')
                selected_text = reply_data.get('selectedButtonText', '')
                if not selected_button:
                    logger.error(f"Нет selectedButtonId: {json.dumps(message_data)}")
                    if receipt_id:
                        self.delete_notification(receipt_id)
                    return

                logger.info(f"🔘 Нажата кнопка: {selected_button} ({selected_text}) от {chat_id}")

                if selected_button == 'lang_ru':
                    self.set_language(chat_id, 'ru')
                    self.send_welcome_with_actions(chat_id, 'ru')
                elif selected_button == 'lang_kk':
                    self.set_language(chat_id, 'kk')
                    self.send_welcome_with_actions(chat_id, 'kk')
                elif selected_button == 'lang_en':
                    self.set_language(chat_id, 'en')
                    self.send_welcome_with_actions(chat_id, 'en')
                elif selected_button == 'get_price':
                    lang = self.user_language.get(chat_id, 'ru')
                    self._send_price(chat_id, lang)
                elif selected_button == 'book_consult':
                    # ✅ Запускаем пошаговую форму с кнопки
                    lang = self.user_language.get(chat_id, 'ru')
                    self.form_state[chat_id] = {"step": 1, "data": {}}
                    forms_start = {
                        'ru': "📞 *Давайте согласуем консультацию!*\n\nКак вас зовут? 🙂",
                        'kk': "📞 *Кеңесті келісейік!*\n\nАтыңыз кім? 🙂",
                        'en': "📞 *Let's arrange your consultation!*\n\nWhat is your name? 🙂",
                    }
                    self.send_message(chat_id, forms_start.get(lang, forms_start['en']))
                elif selected_button == 'short_services':
                    brief = {
                        'ru': "Наши основные услуги:\n• Чат-боты (WA/TG) и интеграции\n• Автоматизация процессов\n• Сайты/лендинги\n• Аналитика и дашборды\n• AI-ассистенты\n\nЧто нужно именно вам? 🙂",
                        'kk': "Басты қызметтер:\n• Чат-боттар және интеграциялар\n• Процестерді автоматтандыру\n• Сайттар/лендингтер\n• Аналитика, дашбордтар\n• AI көмекшілері\n\nСізге нақты не қажет? 🙂",
                        'en': "Core services:\n• Chatbots & integrations\n• Workflow automation\n• Websites/landing pages\n• Analytics dashboards\n• AI assistants\n\nWhat do you need? 🙂"
                    }
                    self.send_message(chat_id, brief.get(self.user_language.get(chat_id, 'ru')))

                self.processed_messages.add(message_id)
                if receipt_id:
                    self.delete_notification(receipt_id)
                return

            else:
                logger.info(f"Игнорируем неподдерживаемый тип: {message_data.get('typeMessage')}")
                if receipt_id:
                    self.delete_notification(receipt_id)
                return

        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")
            rid = notification.get('receiptId') if notification else None
            if rid:
                self.delete_notification(rid)

    def _send_price(self, chat_id: str, lang_code: str):
        caption_map = {
            'ru': "Отправляю актуальный прайс *{brand}*. Если нужен расчёт под вашу задачу — напишите нишу и сроки 🙂",
            'kk': "*{brand}* прайсын жіберемін. Дәл есеп керек болса — сала мен мерзімдерді жазыңыз 🙂",
            'en': "Sharing *{brand}* pricing file. For a tailored estimate, tell your niche and timeline 🙂"
        }
        caption = caption_map.get(lang_code, caption_map['en']).format(brand=self.brand)

        if self.price_url:
            ok = self.send_file_by_url(chat_id, self.price_url, self.price_filename, caption=caption)
            if not ok:
                self.send_message(chat_id, caption + "\n\n" + self.price_url)
        else:
            self.send_message(chat_id, caption + "\n\n(Файл прайса пока не подключён. Укажите PRICE_FILE_URL в .env)")

    def handle_clients_command(self, chat_id: str):
        try:
            filename = "client_records.json"
            if not os.path.exists(filename):
                self.send_message(chat_id, "📭 Записей пока нет")
                return
            with open(filename, 'r', encoding='utf-8') as f:
                clients = json.load(f)
            if not clients:
                self.send_message(chat_id, "📭 Записей пока нет")
                return
            recent = list(clients.items())[-3:]
            response_lines = ["📋 Последние записи:\n"]
            for phone, data in recent:
                response_lines.append(
                    (f"📱 {phone}\n"
                     f"👤 {data.get('name', 'Не указано')}\n"
                     f"🏢 {data.get('company', 'Не указано')}\n"
                     f"🤖 {data.get('bot_type', 'Не указано')}\n"
                     f"📅 {data.get('recorded_at', '').split('T')[0]}\n")
                )
            self.send_message(chat_id, "\n".join(response_lines))
        except Exception as e:
            self.send_message(chat_id, f"Ошибка: {e}")

    def run(self):
        logger.info("🤖 Бот запущен!")
        self.load_user_languages()

        try:
            settings_url = f"{self.base_url}/setSettings/{self.api_token}"
            settings = {"incomingWebhook": "yes", "pollMessageWebhook": "yes"}
            requests.post(settings_url, json=settings, timeout=10)
        except Exception as e:
            logger.warning(f"Не удалось применить setSettings: {e}")

        while True:
            try:
                notification = self.get_notification()
                if notification:
                    self.process_message(notification)
                else:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("⛔ Бот остановлен")
                break
            except Exception as e:
                logger.error(f"Ошибка в главном цикле: {e}")
                time.sleep(5)

    def _check_price_link(self):
        try:
            if not self.price_url:
                logger.warning("PRICE_FILE_URL не задан")
                return
            r = requests.head(self.price_url, timeout=8, allow_redirects=True)
            logger.info(f"PRICE_FILE_URL check: status={r.status_code}, size={r.headers.get('Content-Length')}")
        except Exception as e:
            logger.warning(f"Проверка PRICE_FILE_URL упала: {e}")


if __name__ == "__main__":
    try:
        bot = WhatsAppBot()
        bot.run()
    except Exception as e:
        print(f"Ошибка запуска: {e}")
        print(
            "Проверьте переменные окружения: INSTANCE_ID, INSTANCE_TOKEN, OPENAI_API_KEY, BRAND_NAME, SUPPORT_PHONE, PRICE_FILE_URL, PRICE_FILE_NAME")