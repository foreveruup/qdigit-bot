import os
import requests
import json
import time
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

        self.brand = os.environ.get("BRAND_NAME", "qdigit")
        self.support_phone = os.environ.get("SUPPORT_PHONE", "+7 777 777 77 77")
        self.price_url = os.environ.get("PRICE_FILE_URL")  # публичный URL прайса
        self.price_filename = os.environ.get("PRICE_FILE_NAME", "qdigit_price.pdf")


        self.api_key = os.environ.get("OPENAI_API_KEY")
        self.client = OpenAI(api_key=self.api_key)
        self.openai_model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

        if not all([self.instance_id, self.api_token, self.api_key]):
            raise ValueError("Не заданы переменные окружения: INSTANCE_ID/INSTANCE_TOKEN/OPENAI_API_KEY")

        # Хранилище выбранного языка для каждого чата
        self.user_language = {}  # {chat_id: 'ru'/'kk'/'en'}

        # Системные промпты для каждого языка
        self.system_prompts = {
            'ru': """Ты — тёплый и компетентный консультант компании qdigit (Казахстан).
        Ты помогаешь клиентам понять наши услуги и выбрать решение под задачу.

        НАШИ УСЛУГИ (знай и предлагай уместно):
        • Лендинги и сайты (витрины, каталоги, корпоративные)
        • Аналитика (сквозная, дашборды, метрики)
        • Автоматизация (бизнес-процессы, интеграции, RPA)
        • Дизайн (UX/UI, фирстиль, прототипирование)
        • Чат-боты (WhatsApp/Telegram), оплата, CRM, уведомления
        • Маркетинг (воронки, eCRM, ретеншн)
        • SEO (техаудит, семантика, контент)
        • Контекст (Google Ads, Яндекс РСЯ)
        • ИИ (ассистенты, генерация контента, поиск)
        • Интеграции (CRM, ERP, платежи, 1C и др.)

        ПРАВИЛА:
        • Пиши цены только в тенге (₸).
        • Если спрашивают прайс — отправь *файл прайса* и короткий комментарий.
        • Если просят поддержку — дай наш номер поддержки и предложи написать в WhatsApp.
        • Если не уверен — задай 1–2 уточняющих вопроса, не выдумывай.
        • Пиши коротко, дружелюбно, по делу. 1–3 эмодзи.
        • Маркируй ключевые пункты маркерами (•) или короткими абзацами.""",

            'kk': """Сіз qdigit компаниясының жылы әрі білікті кеңесші ботсыз (Қазақстан).
        Клиенттерге қызметтерімізді түсіндіріп, дұрыс шешім таңдауға көмектесесіз.

        ҚЫЗМЕТТЕР:
        • Лендингтер және сайттар
        • Аналитика (сквозная, дашбордтар)
        • Автоматтандыру
        • Дизайн (UX/UI)
        • Чат-боттар (WhatsApp/Telegram)
        • Маркетинг
        • SEO
        • Контекст
        • ЖИ (AI)
        • Интеграциялар (CRM, ERP, төлемдер)

        ЕРЕЖЕЛЕР:
        • Бағаларды тек теңгемен (₸) жазыңыз.
        • Баға сұраса — *прайс файлын* жіберіңіз және қысқа түсініктеме қосыңыз.
        • Қолдау керек болса — біздің қолдау нөмірін беріңіз.
        • Қысқа, достық, 1–3 эмодзи.""",

            'en': """You are a warm, competent consultant for qdigit (Kazakhstan).
        Help clients understand our services and pick the right solution.

        SERVICES:
        • Landing pages & websites
        • Analytics (end-to-end, dashboards)
        • Automation (workflows, RPA, integrations)
        • Design (UX/UI, branding)
        • Chatbots (WhatsApp/Telegram), payments, CRM
        • Marketing
        • SEO
        • PPC
        • AI (assistants, content, search)
        • Integrations (CRM/ERP/payments)

        RULES:
        • Prices only in KZT (₸).
        • If asked for price — send the *price file* and a brief note.
        • If they ask for support — provide our support number and suggest WhatsApp.
        • Be concise, friendly, 1–3 emojis."""
        }

        self.processed_messages = set()
        self.history = {}
        self.last_reply = {}

        # === ВЫБОР ЯЗЫКА ===

    def is_greeting(self, text: str) -> bool:
        """Проверка, является ли сообщение приветствием"""
        t = text.lower().strip()
        # Русские приветствия
        ru_greetings = {'привет', 'здравствуй', 'здравствуйте', 'салам', 'здорово',
                        'добрый день', 'добрый вечер', 'доброе утро', 'прив', 'здраст',
                        'дратути', 'хай', 'приветик', 'приветствую'}
        # Казахские приветствия
        kk_greetings = {'сәлем', 'салам', 'сәлеметсіз бе', 'қайырлы таң',
                        'қайырлы күн', 'қайырлы кеш'}
        # Английские приветствия
        en_greetings = {'hi', 'hello', 'hey', 'good morning', 'good day',
                        'good evening', 'greetings', 'hiya', 'howdy'}

        all_greetings = ru_greetings | kk_greetings | en_greetings

        # Проверяем точное совпадение и без знаков препинания
        return t in all_greetings or t.replace('!', '').replace(',', '').strip() in all_greetings

    def send_language_selection(self, chat_id: str) -> bool:
        url = f"{self.base_url}/sendInteractiveButtonsReply/{self.api_token}"
        payload = {
            "chatId": chat_id,
            "header": " ",
            "body": "👋 Выберите язык общения\nҚарым-қатынас тілін таңдаңыз\nChoose your language",
            "footer": self.brand,
            "buttons": [
                {"buttonId": "lang_ru", "buttonText": "🇷🇺 Русский"},
                {"buttonId": "lang_kk", "buttonText": "🇰🇿 Қазақша"},
                {"buttonId": "lang_en", "buttonText": "🇬🇧 English"}
            ]
        }

        fallback = (
            "👋 *Выберите язык общения*\n"
            "🇰🇿 *Қарым-қатынас тілін таңдаңыз*\n"
            "🇬🇧 *Choose your language*\n\n"
            "1️⃣ Русский 🇷🇺\n"
            "2️⃣ Қазақша 🇰🇿\n"
            "3️⃣ English 🇬🇧\n\n"
            "_Напишите цифру / Санды жазыңыз / Type number_"
        )

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
        """Установка языка для пользователя"""
        self.user_language[chat_id] = lang_code
        logger.info(f"🌍 Язык для {chat_id} установлен: {lang_code}")

        # Сохраняем в файл для персистентности
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
        """Загрузка сохраненных языков пользователей"""
        try:
            filename = "user_languages.json"
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    self.user_language = json.load(f)
                logger.info(f"Загружено языков: {len(self.user_language)}")
        except Exception as e:
            logger.error(f"Ошибка загрузки языков: {e}")

    def get_welcome_message(self, lang_code: str) -> str:
        """Приветственное сообщение после выбора языка"""
        messages = {
            'ru': (
                "✅ *Отлично!* 🎉\n\n"
                "Я помогу с ботами и автоматизацией бизнеса.\n\n"
                "*Что я умею:*\n"
                "• Рассказать о возможностях\n"
                "• Посчитать стоимость\n"
                "• Записать на консультацию\n\n"
                "Что вас интересует? 😊"
            ),
            'kk': (
                "✅ *Тамаша!* 🎉\n\n"
                "Мен боттар мен бизнес автоматтандыруы бойынша көмектесемін.\n\n"
                "*Не істей аламын:*\n"
                "• Мүмкіндіктер туралы айту\n"
                "• Құнды есептеу\n"
                "• Кеңеске жазу\n\n"
                "Сізді не қызықтырады? 😊"
            ),
            'en': (
                "✅ *Great!* 🎉\n\n"
                "I'll help with bots and business automation.\n\n"
                "*What I can do:*\n"
                "• Tell you about capabilities\n"
                "• Calculate costs\n"
                "• Schedule a consultation\n\n"
                "What are you interested in? 😊"
            )
        }
        return messages.get(lang_code, messages['en'])

    # === УТИЛИТЫ ===

    def _extract_text(self, message_data: dict) -> str:
        """
        Возвращает текст из разных типов входящих сообщений Green-API:
        - textMessageData.textMessage
        - extendedTextMessageData.text
        - quotedMessage (если нужно)
        - listMessage/ buttonsResponse (если решите обрабатывать)
        """
        if not message_data:
            return ""

        # 1) Простой текст
        t = message_data.get("textMessageData", {}).get("textMessage")
        if t:
            return t

        # 2) Текст из extended (часто при старте из wa.me, при наличии URL/предпросмотра)
        t = message_data.get("extendedTextMessageData", {}).get("text")
        if t:
            return t

        # 3) Иногда провайдеры кладут в "message" или "caption"
        t = message_data.get("message", "")
        if t:
            return t
        t = message_data.get("caption", "")
        if t:
            return t

        # 4) На будущее: кнопки/листы (если будете использовать)
        # selectedButtonText = message_data.get("interactiveButtonsResponse", {}).get("selectedButtonText")
        # if selectedButtonText: return selectedButtonText

        return ""

    def _normalize_text(self, text: str) -> str:
        # удаляем невидимые символы, лишние пробелы, NBSP/ZWSP
        return (text or "").replace("\u200b", "").replace("\xa0", " ").strip()

    def clear_chat_history(self, chat_id: str):
        """Очистка истории чата"""
        if chat_id in self.history:
            del self.history[chat_id]
        if chat_id in self.last_reply:
            del self.last_reply[chat_id]
        if chat_id in self.user_language:
            del self.user_language[chat_id]
        logger.info(f"История чата {chat_id} очищена")

    def send_message(self, chat_id: str, message: str) -> bool:
        """Отправка текстового сообщения"""
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
        """
        Отправка файла по публичному URL (Green-API: sendFileByUrl).
        """
        url = f"{self.base_url}/sendFileByUrl/{self.api_token}"
        payload = {
            "chatId": chat_id,
            "urlFile": file_url,
            "fileName": file_name,
            "caption": caption or ""
        }
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
        """Получение уведомления"""
        url = f"{self.base_url}/receiveNotification/{self.api_token}"
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                data = r.json()
                return data
            logger.error("receiveNotification %s %s", r.status_code, r.text)
            return None
        except Exception as e:
            logger.error(f"Ошибка получения уведомлений: {e}")
            return None

    def delete_notification(self, receipt_id: int) -> bool:
        """Удаление уведомления"""
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
        """Ответ от OpenAI с учетом выбранного языка"""
        lang_code = self.user_language.get(chat_id, 'ru')
        system_prompt = self.system_prompts.get(lang_code, self.system_prompts['ru'])

        hist = self.history.setdefault(chat_id, [])
        hist.append({"role": "user", "content": user_message})

        window = hist[-12:]

        style_rules = {
            'ru': "Говори коротко, дружелюбно и по делу. Используй эмодзи умеренно (1–3 на ответ).",
            'kk': "Қысқа, достық және іс бойынша жауап беріңіз. Эмодзиді қолданыңыз (1–3 жауапқа).",
            'en': "Speak briefly, friendly and to the point. Use emojis moderately (1–3 per response)."
        }

        system = system_prompt + "\n\nСТИЛЬ:\n" + style_rules.get(lang_code, style_rules['en'])

        messages = [{"role": "system", "content": system}] + window

        try:
            resp = self.client.chat.completions.create(
                model=self.openai_model,
                messages=messages,
                max_tokens=350,
                temperature=0.8,
                top_p=0.9,
                frequency_penalty=0.6,
                presence_penalty=0.5
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
    def route_intent(self, text: str, lang_code: str) -> Optional[str]:
        t = (text or "").lower().strip()

        # Прайс/цены
        price_kw = {
            'ru': ["цена", "стоимость", "прайс", "сколько стоит", "прайслист", "прайс-лист", "ценник"],
            'kk': ["баға", "құны", "прайс"],
            'en': ["price", "pricing", "cost", "how much", "pricelist"]
        }
        if any(k in t for k in price_kw.get(lang_code, [])):
            # Вернем специальный маркер — дальше обработаем отправку файла
            return "__INTENT_PRICE__"

        # Поддержка
        support_kw = {
            'ru': ["поддержк", "саппорт", "техпод", "help", "support", "помощь", "свяжитесь"],
            'kk': ["қолдау", "көмек", "support"],
            'en': ["support", "help", "contact", "assist"]
        }
        if any(k in t for k in support_kw.get(lang_code, [])):
            note = {
                'ru': f"Наш номер поддержки: {self.support_phone}\nНапишите в WhatsApp — быстро ответим. 📞",
                'kk': f"Біздің қолдау нөмірі: {self.support_phone}\nWhatsApp-қа жазыңыз — жылдам жауап береміз. 📞",
                'en': f"Our support number: {self.support_phone}\nWrite on WhatsApp — we’ll reply quickly. 📞"
            }
            return note.get(lang_code, note['en'])

        # Консультация (как было)
        consult_keywords = {
            'ru': ["записаться", "консультац", "созвон", "перезвон", "запишите меня"],
            'kk': ["жазылу", "кеңес", "қоңырау", "жазыңыз мені"],
            'en': ["schedule", "consultation", "appointment", "call me", "book"]
        }
        if any(kw in t for kw in consult_keywords.get(lang_code, [])):
            forms = {
                'ru': "Отлично! Запишу вас на бесплатную консультацию. Заполните, пожалуйста:\nИмя:\nКомпания:\nТелефон:\nЗадача:",
                'kk': "Тамаша! Сізді тегін кеңеске жазамын. Толтырыңыз:\nАты:\nКомпания:\nТелефон:\nМіндет:",
                'en': "Great! I'll schedule a free consultation. Please fill in:\nName:\nCompany:\nPhone:\nTask:"
            }
            return forms.get(lang_code, forms['en'])

        return None

    # === СОХРАНЕНИЕ КЛИЕНТА ===
    def save_client_data(self, phone: str, data: dict) -> bool:
        """Сохранение данных клиента"""
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
            logger.info(f"Записан клиент {phone}: {data.get('name', 'Без имени')}")
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")
            return False

    def extract_client_info(self, text: str, lang_code: str) -> dict:
        """Извлечение информации о клиенте"""
        info = {}
        keywords = {
            'ru': {'name': ['имя:', 'name:'], 'company': ['компания:', 'company:'],
                   'phone': ['телефон:', 'phone:'], 'task': ['задача:', 'задач']},
            'kk': {'name': ['аты:', 'name:'], 'company': ['компания:', 'company:'],
                   'phone': ['телефон:', 'phone:'], 'task': ['міндет:', 'міндет']},
            'en': {'name': ['name:'], 'company': ['company:'],
                   'phone': ['phone:'], 'task': ['task:']}
        }

        kw = keywords.get(lang_code, keywords['en'])

        for raw_line in text.split('\n'):
            line = raw_line.strip()
            low = line.lower()

            if any(k in low for k in kw['name']):
                info['name'] = line.split(':', 1)[1].strip()
            elif any(k in low for k in kw['company']):
                info['company'] = line.split(':', 1)[1].strip()
            elif any(k in low for k in kw['phone']):
                info['phone'] = line.split(':', 1)[1].strip()
            elif any(k in low for k in kw['task']):
                info['bot_type'] = line.split(':', 1)[1].strip()

        return info

    # === ОБРАБОТКА СООБЩЕНИЙ ===
    def process_message(self, notification: dict):
        """Основная обработка сообщения"""
        try:
            if not notification:
                return
            receipt_id = notification.get('receiptId')
            body = notification.get('body', {})
            if not body:
                return

            type_webhook = body.get('typeWebhook', '')

            # Получаем message_id на корневом уровне (исправление)
            message_id = body.get('idMessage')

            # Дедупликация (теперь работает для всех типов)
            if message_id and message_id in self.processed_messages:
                if receipt_id:
                    self.delete_notification(receipt_id)
                return

            # Игнорируем нерелевантные вебхуки
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
                    logger.warning(
                        f"Пустой текст при входящем сообщении. type={message_data.get('typeMessage')}, data={json.dumps(message_data, ensure_ascii=False)[:1000]}")
                    if chat_id not in self.user_language:
                        self.send_language_selection(chat_id)
                    else:
                        self.send_message(chat_id, "Не расслышал сообщение. Напишите, пожалуйста, ещё раз 🙂")
                    self.processed_messages.add(message_id)
                    if receipt_id:
                        self.delete_notification(receipt_id)
                    return

                logger.info(f"📩 Текстовое сообщение от {phone}: {message_text}")

                # === ADMIN КОМАНДЫ ===
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

                # === ПРОВЕРКА ВЫБОРА ЯЗЫКА ===
                if chat_id not in self.user_language:
                    if message_text.strip() in ['1', '2', '3']:
                        lang_map = {'1': 'ru', '2': 'kk', '3': 'en'}
                        lang_code = lang_map[message_text.strip()]
                        self.set_language(chat_id, lang_code)
                        welcome = self.get_welcome_message(lang_code)
                        self.send_message(chat_id, welcome)
                    elif self.is_greeting(message_text):
                        self.send_language_selection(chat_id)
                    else:
                        logger.info(f"⏸️ Игнорируем сообщение до выбора языка: {message_text[:50]}")

                    self.processed_messages.add(message_id)
                    if receipt_id:
                        self.delete_notification(receipt_id)
                    return

                lang_code = self.user_language[chat_id]

                field_keywords = ['имя:', 'компания:', 'телефон:', 'name:', 'company:', 'phone:',
                                  'аты:', 'міндет:', 'задач', 'task:']

                if any(k in message_text.lower() for k in field_keywords):
                    client_info = self.extract_client_info(message_text, lang_code)

                    need = []
                    need_messages = {
                        'ru': {'name': 'Имя', 'company': 'Компания', 'phone': 'Телефон', 'task': 'Задача'},
                        'kk': {'name': 'Аты', 'company': 'Компания', 'phone': 'Телефон', 'task': 'Міндет'},
                        'en': {'name': 'Name', 'company': 'Company', 'phone': 'Phone', 'task': 'Task'}
                    }

                    nm = need_messages.get(lang_code, need_messages['en'])

                    if not client_info.get('name'): need.append(nm['name'])
                    if not client_info.get('company'): need.append(nm['company'])
                    if not client_info.get('phone'): need.append(nm['phone'])
                    if not client_info.get('bot_type'): need.append(nm['task'])

                    if need:
                        ask_messages = {
                            'ru': f"Почти всё! Не хватает: {', '.join(need)}.\nПришлите одним сообщением.",
                            'kk': f"Барлығы дерлік! Жетіспейді: {', '.join(need)}.\nБір хабарламада жіберіңіз.",
                            'en': f"Almost there! Missing: {', '.join(need)}.\nSend in one message."
                        }
                        self.send_message(chat_id, ask_messages.get(lang_code, ask_messages['en']))
                    else:
                        if self.save_client_data(phone, client_info):
                            success_messages = {
                                'ru': (
                                    "✅ Записал вас на бесплатную консультацию!\n\n"
                                    f"👤 Имя: {client_info.get('name')}\n"
                                    f"🏢 Компания: {client_info.get('company')}\n"
                                    f"📱 Телефон: {client_info.get('phone')}\n"
                                    f"🧩 Задача: {client_info.get('bot_type')}\n\n"
                                    "Свяжемся в ближайшее время. Предпочтительнее звонок или WhatsApp? 🙂"
                                ),
                                'kk': (
                                    "✅ Сізді тегін кеңеске жаздым!\n\n"
                                    f"👤 Аты: {client_info.get('name')}\n"
                                    f"🏢 Компания: {client_info.get('company')}\n"
                                    f"📱 Телефон: {client_info.get('phone')}\n"
                                    f"🧩 Міндет: {client_info.get('bot_type')}\n\n"
                                    "Жақын арада хабарласамыз. Қоңырау немесе WhatsApp артық па? 🙂"
                                ),
                                'en': (
                                    "✅ Scheduled you for a free consultation!\n\n"
                                    f"👤 Name: {client_info.get('name')}\n"
                                    f"🏢 Company: {client_info.get('company')}\n"
                                    f"📱 Phone: {client_info.get('phone')}\n"
                                    f"🧩 Task: {client_info.get('bot_type')}\n\n"
                                    "We'll contact you soon. Do you prefer call or WhatsApp? 🙂"
                                )
                            }
                            self.send_message(chat_id, success_messages.get(lang_code, success_messages['en']))

                    self.processed_messages.add(message_id)
                    if receipt_id:
                        self.delete_notification(receipt_id)
                    return

                # Быстрая маршрутизация (ваш код)
                quick = self.route_intent(message_text, lang_code)
                if quick:
                    if quick == "__INTENT_PRICE__":
                        # Отправка прайса файлом (если есть URL), иначе fallback
                        caption_map = {
                            'ru': "Отправляю актуальный прайс qdigit. Если нужен расчёт под вашу задачу — напишите нишу и сроки 🙂",
                            'kk': "qdigit бағалар тізімін жіберемін. Нақты есеп керек болса — сала мен мерзімдерді жазыңыз 🙂",
                            'en': "Sharing qdigit pricing file. For a tailored estimate, tell your niche and timeline 🙂"
                        }
                        caption = caption_map.get(lang_code, caption_map['en'])
                        if self.price_url:
                            ok = self.send_file_by_url(chat_id, self.price_url, self.price_filename, caption=caption)
                            if not ok:
                                self.send_message(chat_id,
                                                  caption + "\n\n(Не удалось отправить файл. Вот ссылка: " + self.price_url + ")")
                        else:
                            self.send_message(chat_id,
                                              caption + "\n\n(Файл прайса пока не подключён. Укажите PRICE_FILE_URL в .env)")
                    else:
                        self.send_message(chat_id, quick)

                    self.processed_messages.add(message_id)
                    if receipt_id:
                        self.delete_notification(receipt_id)
                    return

                # Ответ через GPT (ваш код)
                response = self.get_openai_response(chat_id, message_text)
                self.send_message(chat_id, response)

                self.processed_messages.add(message_id)
                if receipt_id:
                    self.delete_notification(receipt_id)
                return

            # ОБРАБОТКА: Ответ на интерактивные кнопки
            elif message_data.get('typeMessage') == 'interactiveButtonsResponse':
                # Правильная структура: interactiveButtonsResponse содержит данные
                reply_data = message_data.get('interactiveButtonsResponse', {})
                selected_button = reply_data.get('selectedButtonId', '')
                selected_text = reply_data.get('selectedButtonText', '')

                if not selected_button:
                    logger.error(
                        f"Нет selectedButtonId в button reply для {chat_id}. Полная data: {json.dumps(message_data)}")
                    if receipt_id:
                        self.delete_notification(receipt_id)
                    return

                logger.info(f"🔘 Нажата кнопка: {selected_button} ({selected_text}) от {chat_id}")

                # Определяем язык (ваш код)
                if selected_button == 'lang_ru':
                    self.set_language(chat_id, 'ru')
                    self.send_message(chat_id, self.get_welcome_message('ru'))
                elif selected_button == 'lang_kk':
                    self.set_language(chat_id, 'kk')
                    self.send_message(chat_id, self.get_welcome_message('kk'))
                elif selected_button == 'lang_en':
                    self.set_language(chat_id, 'en')
                    self.send_message(chat_id, self.get_welcome_message('en'))

                self.processed_messages.add(message_id)
                if receipt_id:
                    self.delete_notification(receipt_id)
                return

            # Для других типов сообщений (напр. изображения, голосовые) - игнорируем
            else:
                logger.info(
                    f"Игнорируем неподдерживаемый тип сообщения: {message_data.get('typeMessage')}. Полная data: {json.dumps(message_data)}")
                if receipt_id:
                    self.delete_notification(receipt_id)
                return

        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")
            rid = notification.get('receiptId') if notification else None
            if rid:
                self.delete_notification(rid)

    # /clients команда
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
                    (
                        f"📱 {phone}\n"
                        f"👤 {data.get('name', 'Не указано')}\n"
                        f"🏢 {data.get('company', 'Не указано')}\n"
                        f"🤖 {data.get('bot_type', 'Не указано')}\n"
                        f"📅 {data.get('recorded_at', '').split('T')[0]}\n"
                    )
                )
            self.send_message(chat_id, "\n".join(response_lines))
        except Exception as e:
            self.send_message(chat_id, f"Ошибка: {e}")

    # Главный цикл
    def run(self):
        logger.info("🤖 Бот запущен!")

        # Загружаем сохраненные языки
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


if __name__ == "__main__":
    try:
        bot = WhatsAppBot()
        bot.run()
    except Exception as e:
        print(f"Ошибка запуска: {e}")
        print(
            "Проверьте переменные окружения в .env файле: INSTANCE_ID, INSTANCE_TOKEN, OPENAI_API_KEY, (опц.) OPENAI_MODEL")