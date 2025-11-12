import os, json, gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

# 1) прочитаем JSON из .env
creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
assert creds_json, "Нет GOOGLE_SERVICE_ACCOUNT_JSON в окружении"
creds = json.loads(creds_json)

print("Service account email:", creds.get("client_email"))  # полезно для проверки

# 2) авторизация со скоупами Drive + Sheets
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
credentials = Credentials.from_service_account_info(creds, scopes=scopes)
gc = gspread.authorize(credentials)

# 3) ОТКРЫВАЕМ ПО КЛЮЧУ (замени на свой ключ!)
SPREADSHEET_KEY = "1fBivLU9RomvuOD13wMFXZo5yOxa8s0-jZrj5GipdQ0Y"
sh = gc.open_by_key(SPREADSHEET_KEY)

# 4) получаем лист по имени (или создаём, если нет)
WORKSHEET_NAME = "leads_wp"
try:
    ws = sh.worksheet(WORKSHEET_NAME)
except gspread.WorksheetNotFound:
    ws = sh.add_worksheet(title=WORKSHEET_NAME, rows=1000, cols=10)

ws.append_row(["test", "works", "fine"], value_input_option="USER_ENTERED")
print("✅ запись добавлена")