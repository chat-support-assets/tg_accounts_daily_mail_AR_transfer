"""
GEO Controller Bot
==================
Listens for commands in Telegram and triggers performance message sends.
"""

import asyncio
import json
import os
import pickle
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from telethon import TelegramClient, events
import openpyxl

# ============================================================
# Загрузка .env файла
# ============================================================
env_path = Path(__file__).parent / '.env'
if not env_path.exists():
    env_path = Path(__file__).parent.parent / '.env'

load_dotenv(env_path)
print(f"🔐 Loaded env from: {env_path}")

# ============================================================
# Пути для хранения данных
# ============================================================
SCRIPT_DIR = Path(__file__).parent
SESSION_DIR = SCRIPT_DIR / 'sessions'
SESSION_DIR.mkdir(exist_ok=True)

LOG_DIR = SCRIPT_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)

FAILED_EXPORT_DIR = SCRIPT_DIR / 'failed_exports'
FAILED_EXPORT_DIR.mkdir(exist_ok=True)

COMMAND_COOLDOWNS_PATH = SESSION_DIR / 'command_cooldowns.json'

# ============================================================
# Конфигурация из переменных окружения
# ============================================================
# Telegram API
API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')

# Bot tokens
INDIA_BOT_TOKEN = os.getenv('INDIA_BOT_TOKEN', '')
NEPAL_BOT_TOKEN = os.getenv('NEPAL_BOT_TOKEN', '')

# Access control
AUTHORIZED_USER_IDS = {
    int(x.strip()) for x in os.getenv('AUTHORIZED_USER_IDS', '').split(',') if x.strip()
}
ADMIN_CHAT_IDS = [
    int(x.strip()) for x in os.getenv('ADMIN_CHAT_IDS', '').split(',') if x.strip()
]

# Report destinations
INDIA_REPORT_CHAT_ID = int(os.getenv('INDIA_REPORT_CHAT_ID', 0))
OTHER_REPORT_CHAT_ID = int(os.getenv('OTHER_REPORT_CHAT_ID', 0))

# Google Sheets
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', '')
SERVICE_ACCOUNT_FILE = Path(__file__).parent / 'service-account-key.json'
GOOGLE_SERVICE_ACCOUNT_FILE = str(SERVICE_ACCOUNT_FILE)

MESSAGE_TEMPLATE_RANGE = os.getenv('MESSAGE_TEMPLATE_RANGE', 'AR_text!H3:L7')
MESSAGE_CYCLE_COLUMNS = ['H', 'I', 'J', 'K', 'L']
MESSAGE_CYCLE_START = date(2026, 4, 17)

# Geo configuration
GEO_CONFIG = {
    'india': {
        'range': 'India!A:F',
        'bot_token': INDIA_BOT_TOKEN,
        'session': 'india_sender_session',
    },
    'nepal': {
        'range': 'Nepal!A:F',
        'bot_token': NEPAL_BOT_TOKEN,
        'session': 'nepal_sender_session',
    },
    'egypt': {
        'range': 'Egypt!A:F',
        'bot_token': NEPAL_BOT_TOKEN,
        'session': 'egypt_sender_session',
    },
    'srilanka': {
        'range': 'Srilanka!A:F',
        'bot_token': NEPAL_BOT_TOKEN,
        'session': 'srilanka_sender_session',
    },
}

# Auto-trigger
AUTO_TRIGGER_TEXT = "AUTO_TRIGGER_GEO_UPDATE"
AUTO_PAUSE_SECONDS = 360
APPSCRIPT_CHAT_ID = int(os.getenv('APPSCRIPT_CHAT_ID', 0))

COMMAND_COOLDOWN_HOURS = 12

# ============================================================
# Google Sheets с Service Account
# ============================================================
SHEET_SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']


def get_google_sheets_service():
    """Получение сервиса Google Sheets через Service Account"""
    if not GOOGLE_SERVICE_ACCOUNT_FILE:
        print("❌ GOOGLE_SERVICE_ACCOUNT_FILE not set in .env")
        return None
    
    service_account_path = Path(GOOGLE_SERVICE_ACCOUNT_FILE)
    if not service_account_path.exists():
        print(f"❌ Service account file not found: {service_account_path}")
        return None
    
    try:
        credentials = service_account.Credentials.from_service_account_file(
            service_account_path,
            scopes=SHEET_SCOPES
        )
        return build('sheets', 'v4', credentials=credentials)
    except Exception as e:
        print(f"❌ Failed to build Sheets service: {e}")
        return None


def read_sheet_data(range_name):
    """Читает данные из Google Sheets"""
    try:
        service = get_google_sheets_service()
        if service is None:
            print("❌ Google Sheets service unavailable")
            return []
        
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
        ).execute()
        values = result.get('values', [])
        print(f"📄 Read {len(values)} rows from {range_name}")
        return values
    except Exception as e:
        print(f"❌ Error reading sheet: {e}")
        return []


def get_message_cycle_column(today=None):
    """Определяет колонку для сообщений на основе дня цикла"""
    current_day = today or date.today()
    cycle_day = (current_day - MESSAGE_CYCLE_START).days % len(MESSAGE_CYCLE_COLUMNS)
    return MESSAGE_CYCLE_COLUMNS[cycle_day]


def load_fast_message_templates(today=None):
    """Загружает шаблоны сообщений из Google Sheets"""
    rows = read_sheet_data(MESSAGE_TEMPLATE_RANGE)
    if not rows or len(rows) < 5:
        print("⚠️ Message template sheet missing, using defaults")
        return None
    
    chosen_column = get_message_cycle_column(today=today)
    col_index = MESSAGE_CYCLE_COLUMNS.index(chosen_column)
    
    messages = []
    for row_num, row in enumerate(rows[:5], start=3):
        if col_index >= len(row) or not row[col_index].strip():
            print(f"⚠️ Template row {row_num} missing in column {chosen_column}")
            return None
        messages.append(str(row[col_index]).strip())
    
    template_map = {
        'gte_94': messages[0],
        'gte_90': messages[1],
        'gte_85': messages[2],
        'gte_80': messages[3],
        'lt_80': messages[4],
    }
    print(f"📝 Loaded templates from column {chosen_column}")
    return template_map


# ============================================================
# Команды и управление
# ============================================================
running_commands = set()
cancel_requested = False
pending_confirmations = {}


def load_command_cooldowns():
    try:
        with open(COMMAND_COOLDOWNS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_command_cooldowns(cooldowns):
    try:
        with open(COMMAND_COOLDOWNS_PATH, 'w', encoding='utf-8') as f:
            json.dump(cooldowns, f, indent=2, sort_keys=True)
    except Exception as e:
        print(f"⚠️ Could not save cooldowns: {e}")


def get_command_cooldown_remaining(command_key):
    cooldowns = load_command_cooldowns()
    last_run = cooldowns.get(command_key)
    if not last_run:
        return None
    
    try:
        last_run_dt = datetime.fromisoformat(last_run)
    except ValueError:
        return None
    
    next_allowed = last_run_dt + timedelta(hours=COMMAND_COOLDOWN_HOURS)
    now = datetime.now()
    return None if now >= next_allowed else next_allowed - now


def mark_command_success(command_key):
    cooldowns = load_command_cooldowns()
    cooldowns[command_key] = datetime.now().isoformat(timespec='seconds')
    save_command_cooldowns(cooldowns)


async def check_command_cooldown(event, command_key, command_label):
    remaining = get_command_cooldown_remaining(command_key)
    if not remaining:
        return False
    
    hours, remainder = divmod(int(remaining.total_seconds()), 3600)
    minutes = (remainder + 59) // 60
    if minutes == 60:
        hours += 1
        minutes = 0
    
    wait_text = f"{hours}h {minutes}m" if hours else f"{minutes}m"
    await event.reply(
        f"⏳ {command_label} already completed recently. "
        f"Please wait {wait_text} before running it again."
    )
    return True


# ============================================================
# Построение сообщений
# ============================================================
def parse_percent(value):
    if not value or str(value).strip() in ('(Пусто)', '-', '—', ''):
        return None
    try:
        return float(str(value).replace('%', '').replace(',', '.').strip())
    except ValueError:
        return None


DEFAULT_FAST_MESSAGES = {
    'gte_94': "Your performance during the first 5 minutes was perfect. Keep it up!",
    'gte_90': "Your performance during the first 5 minutes was great. Good job!",
    'gte_85': "Your 5-minute approval speed was below our standards. Please try to improve it and get it above 90%.",
    'gte_80': "Your performance during the first 5 minutes was slower than we expected. It must always stay above 90%. We may apply a penalty with this AR, so please try to improve it.",
    'lt_80': "Your speed during the first 5 minutes is very slow. The AR should stay above 90%. We may consider closing your project if you don't make improvements, so please improve it.",
}


def get_fast_message(fast_pct, fast_messages=None):
    messages = fast_messages or DEFAULT_FAST_MESSAGES
    if fast_pct >= 94:
        return messages['gte_94']
    elif fast_pct >= 90:
        return messages['gte_90']
    elif fast_pct >= 85:
        return messages['gte_85']
    elif fast_pct >= 80:
        return messages['gte_80']
    else:
        return messages['lt_80']


def get_slow_message(slow_pct, slow_raw):
    if slow_pct and slow_pct > 2.5:
        return f"{slow_raw} of all orders yesterday were approved after 120 minutes. You need to keep it under 2.5%."
    return None


def build_message(total, fast_pct, slow_pct, slow_raw, fast_messages=None):
    fast_display = f"{fast_pct:.1f}"
    fast_line = get_fast_message(fast_pct, fast_messages=fast_messages)
    slow_line = get_slow_message(slow_pct, slow_raw)
    
    lines = [
        f"Yesterday, you approved a total of {total} deposit requests.",
        "",
        f"• 5 minute approval speed is {fast_display}% — {fast_line}",
    ]
    if slow_line:
        lines.append(f"• 120 min+: {slow_line}")
    
    return "\n".join(lines)


# ============================================================
# Отправка сообщений
# ============================================================
async def send_to_rows(geo: str, rows_data: list, label: str, skipped_rows=None, fast_messages=None):
    global cancel_requested
    cancel_requested = False
    
    cfg = GEO_CONFIG[geo]
    success = 0
    failed = 0
    skipped = 0
    skipped_rows = skipped_rows or []
    failed_rows = []
    fast_messages = fast_messages or load_fast_message_templates()
    
    session_path = SESSION_DIR / cfg['session']
    sender = TelegramClient(str(session_path), API_ID, API_HASH)
    await sender.start(bot_token=cfg['bot_token'])
    
    async with sender:
        for idx, t in enumerate(rows_data, 1):
            if cancel_requested:
                print(f"🛑 Cancel requested — stopping after {idx - 1} messages")
                break
            
            fast_pct = parse_percent(t['fast_raw'])
            if fast_pct is None:
                print(f"[{idx}/{len(rows_data)}] ⚠️ Cannot parse 5min: '{t['fast_raw']}' — skipping")
                skipped_rows.append({
                    'agent': t.get('agent', str(t['chat_id'])),
                    'reason': f"Cannot parse 5min%: '{t['fast_raw']}'"
                })
                skipped += 1
                continue
            
            slow_pct = parse_percent(t['slow_raw'])
            slow_display = t['slow_raw'].replace(',', '.') if t['slow_raw'] else '0%'
            
            message = build_message(
                total=t['total'],
                fast_pct=fast_pct,
                slow_pct=slow_pct or 0.0,
                slow_raw=slow_display,
                fast_messages=fast_messages,
            )
            
            print(f"[{idx}/{len(rows_data)}] Sending to {t['chat_id']} / topic {t['topic_id']}")
            
            try:
                await sender.send_message(
                    entity=t['chat_id'],
                    message=message,
                    reply_to=t['topic_id'],
                )
                success += 1
            except Exception as e:
                print(f"   ❌ Failed: {e}")
                failed += 1
                failed_rows.append({**t, 'error': str(e)})
            
            await asyncio.sleep(1.5)
    
    # Сохраняем failed rows в Excel
    if failed_rows:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Failed"
        ws.append(['Chat ID', 'Topic ID', '5min %', '120min %', 'Total', 'Error'])
        for r in failed_rows:
            ws.append([r['chat_id'], r['topic_id'], r['fast_raw'], r['slow_raw'], r['total'], r['error']])
        
        filename = f"failed_{geo}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        export_path = FAILED_EXPORT_DIR / filename
        wb.save(str(export_path))
        print(f"📁 Failed saved to: {export_path}")
    
    # Формируем отчет
    summary_lines = [
        f"📊 *{label}* {'🛑 CANCELLED' if cancel_requested else 'complete'}",
        f"📋 Total targets: {len(rows_data)}",
        f"✅ Sent: {success}",
        f"❌ Failed: {failed}",
        f"⏭️ Skipped (bad data): {skipped}",
    ]
    
    if failed_rows:
        summary_lines.append("\n❌ Failed:")
        for r in failed_rows[:10]:  # Показываем не более 10
            summary_lines.append(f"  • {r.get('agent', str(r['chat_id']))} — {r['error']}")
        if len(failed_rows) > 10:
            summary_lines.append(f"  • ... and {len(failed_rows) - 10} more")
    
    if skipped_rows:
        summary_lines.append("\n⚠️ Skipped:")
        for r in skipped_rows[:5]:
            summary_lines.append(f"  • {r['agent']} — {r['reason']}")
    
    return "\n".join(summary_lines)


def parse_rows(sheet_rows, start=1, end=None):
    """Парсит строки из Google Sheets"""
    if not sheet_rows:
        return [], []
    
    header = [col.strip() for col in sheet_rows[0]]
    
    try:
        idx_chat_id = header.index('Chat ID')
        idx_topic_id = header.index('Topic ID')
        idx_total = header.index('Refill Count')
        idx_fast = header.index('5 min refill')
        idx_slow = header.index('120 min refill')
    except ValueError as e:
        print(f"❌ Column not found: {e}")
        print(f"   Available: {header}")
        return [], []
    
    idx_agent = header.index('By Agent') if 'By Agent' in header else None
    max_col = max(idx_chat_id, idx_topic_id, idx_total, idx_fast, idx_slow,
                  idx_agent if idx_agent is not None else 0)
    
    data = sheet_rows[1:]
    slice_data = data[start - 1:end] if end else data[start - 1:]
    
    targets = []
    skipped_rows = []
    
    for row_num, row in enumerate(slice_data, start=start + 1):
        row = list(row) + [''] * (max_col + 1 - len(row))
        agent = row[idx_agent].strip() if idx_agent is not None else ''
        
        if not agent or agent == '#N/A':
            continue
        
        try:
            chat_id = int(str(row[idx_chat_id]).strip().replace(' ', '').replace('\xa0', ''))
            topic_id = int(str(row[idx_topic_id]).strip().replace(' ', '').replace('\xa0', ''))
        except ValueError:
            print(f"⚠️ Row {row_num}: Invalid Chat ID or Topic ID — skipping")
            skipped_rows.append({'agent': agent, 'reason': 'Invalid Chat ID or Topic ID'})
            continue
        
        targets.append({
            'chat_id': chat_id,
            'topic_id': topic_id,
            'total': row[idx_total].strip() or '0',
            'fast_raw': row[idx_fast].strip(),
            'slow_raw': row[idx_slow].strip(),
            'agent': agent,
        })
    
    return targets, skipped_rows


async def send_report_summary(controller, summary, geo='other'):
    chat_id = INDIA_REPORT_CHAT_ID if geo == 'india' else OTHER_REPORT_CHAT_ID
    if chat_id:
        await controller.send_message(entity=chat_id, message=summary, parse_mode='md')


# ============================================================
# Основная функция
# ============================================================
async def main():
    # Проверка обязательных переменных
    missing = []
    if not API_ID:
        missing.append('API_ID')
    if not API_HASH:
        missing.append('API_HASH')
    
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
    
    if not AUTHORIZED_USER_IDS:
        print("⚠️ WARNING: AUTHORIZED_USER_IDS is empty! No one can use commands.")
    
    controller_session_path = SESSION_DIR / "controller_session"
    controller = TelegramClient(str(controller_session_path), API_ID, API_HASH)
    
    def auth_only(func):
        async def wrapper(event):
            if event.sender_id not in AUTHORIZED_USER_IDS:
                await event.reply("⛔ You are not authorized to use this bot.")
                return
            await func(event)
        return wrapper
    
    # Обработчик подтверждений
    @controller.on(events.NewMessage(incoming=True))
    async def handle_confirmation(event):
        global cancel_requested
        if event.sender_id not in AUTHORIZED_USER_IDS:
            return
        
        user_id = event.sender_id
        text = event.raw_text.strip().lower()
        if text.startswith('/'):
            return
        
        if user_id not in pending_confirmations:
            return
        
        pending = pending_confirmations.pop(user_id)
        command_key = pending['command_key']
        action = pending['action']
        
        if text not in ('да', 'нет'):
            await event.reply("⚠️ Пожалуйста, ответь *да* или *нет*.")
            pending_confirmations[user_id] = pending
            return
        
        if text == 'нет':
            await event.reply("❌ Отменено.")
            return
        
        if action == 'cancel':
            if not running_commands:
                await event.reply("ℹ️ Нет активных команд.")
                return
            cancel_requested = True
            active = ', '.join(running_commands)
            await event.reply(f"🛑 Отмена запрошена! Остановка после текущего сообщения... (активно: {active})")
            return
        
        # Запуск команды
        if command_key in running_commands:
            await event.reply(f"⚠️ {command_key} уже выполняется.")
            return
        
        if await check_command_cooldown(event, command_key, pending['label']):
            return
        
        running_commands.add(command_key)
        try:
            await event.reply(f"⏳ {pending['start_msg']}")
            rows = read_sheet_data(pending['range'])
            targets, skipped_rows = parse_rows(rows, start=pending['start'], end=pending['end'])
            
            if not targets and not skipped_rows:
                await event.reply("❌ Данные не найдены.")
                return
            
            summary = await send_to_rows(pending['geo'], targets, pending['label'],
                                        skipped_rows=skipped_rows)
            await event.reply(summary)
            await send_report_summary(controller, summary, geo=pending['geo'])
            mark_command_success(command_key)
        finally:
            running_commands.discard(command_key)
    
    def ask_confirmation(event, command_key, geo, label, start_msg, range_, start, end):
        pending_confirmations[event.sender_id] = {
            'command_key': command_key,
            'action': 'send',
            'geo': geo,
            'label': label,
            'start_msg': start_msg,
            'range': range_,
            'start': start,
            'end': end,
        }
    
    # Регистрация команд
    @controller.on(events.NewMessage(pattern='/INRfirst90rows', incoming=True))
    @auth_only
    async def cmd_inr_first(event):
        ask_confirmation(event, 'INRfirst90rows', 'india', 'India rows 1–90',
                        'Запуск India rows 1–90...', GEO_CONFIG['india']['range'], 1, 90)
        await event.reply("📋 Файл Approval_speed обновлен с актуальной информацией?")
    
    @controller.on(events.NewMessage(pattern='/INRsecond90rows', incoming=True))
    @auth_only
    async def cmd_inr_second(event):
        ask_confirmation(event, 'INRsecond90rows', 'india', 'India rows 91–180',
                        'Запуск India rows 91–180...', GEO_CONFIG['india']['range'], 91, 180)
        await event.reply("📋 Файл Approval_speed обновлен с актуальной информацией?")
    
    @controller.on(events.NewMessage(pattern='/INRlast', incoming=True))
    @auth_only
    async def cmd_inr_last(event):
        ask_confirmation(event, 'INRlast', 'india', 'India rows 181+',
                        'Запуск India rows 181+...', GEO_CONFIG['india']['range'], 181, None)
        await event.reply("📋 Файл Approval_speed обновлен с актуальной информацией?")
    
    @controller.on(events.NewMessage(pattern='/nepal', incoming=True))
    @auth_only
    async def cmd_nepal(event):
        ask_confirmation(event, 'nepal', 'nepal', 'Nepal all rows',
                        'Запуск Nepal (все строки)...', GEO_CONFIG['nepal']['range'], 1, None)
        await event.reply("📋 Файл Approval_speed обновлен с актуальной информацией?")
    
    @controller.on(events.NewMessage(pattern='/egypt', incoming=True))
    @auth_only
    async def cmd_egypt(event):
        ask_confirmation(event, 'egypt', 'egypt', 'Egypt all rows',
                        'Запуск Egypt (все строки)...', GEO_CONFIG['egypt']['range'], 1, None)
        await event.reply("📋 Файл Approval_speed обновлен с актуальной информацией?")
    
    @controller.on(events.NewMessage(pattern='/srilanka', incoming=True))
    @auth_only
    async def cmd_srilanka(event):
        ask_confirmation(event, 'srilanka', 'srilanka', 'Sri Lanka all rows',
                        'Запуск Sri Lanka (все строки)...', GEO_CONFIG['srilanka']['range'], 1, None)
        await event.reply("📋 Файл Approval_speed обновлен с актуальной информацией?")
    
    @controller.on(events.NewMessage(pattern='/cancel', incoming=True))
    @auth_only
    async def cmd_cancel(event):
        if not running_commands:
            await event.reply("ℹ️ Нет активных команд.")
            return
        pending_confirmations[event.sender_id] = {'action': 'cancel', 'command_key': 'cancel'}
        await event.reply("⚠️ Уверен, что хочешь отменить?")
    
    print("🤖 GEO Controller Bot is running...")
    print(f"✅ Authorized users: {AUTHORIZED_USER_IDS}")
    print("📡 Listening for commands: /INRfirst90rows | /INRsecond90rows | /INRlast | /nepal | /egypt | /srilanka | /cancel")
    
    # Автоматический триггер
    async def run_wave(wave_label, pairs):
        fast_messages = load_fast_message_templates()
        
        async def run_one(command_key, geo, label, range_, start, end):
            if command_key in running_commands:
                return f"⚠️ {label} already running — skipped"
            running_commands.add(command_key)
            try:
                rows = read_sheet_data(range_)
                targets, skipped_rows = parse_rows(rows, start=start, end=end)
                if not targets and not skipped_rows:
                    return f"❌ {label}: no data found"
                summary = await send_to_rows(geo, targets, label, skipped_rows=skipped_rows,
                                            fast_messages=fast_messages)
                mark_command_success(command_key)
                return summary
            except Exception as e:
                return f"❌ {label} crashed: {e}"
            finally:
                running_commands.discard(command_key)
        
        summaries = await asyncio.gather(*[
            run_one(ck, geo, lbl, rng, s, e)
            for ck, geo, lbl, rng, s, e in pairs
        ])
        
        for (ck, geo, lbl, rng, s, e), summary in zip(pairs, summaries):
            await send_report_summary(controller, summary, geo=geo)
        
        wave_summary = f"✅ *{wave_label} complete*\n\n" + "\n\n---\n\n".join(summaries)
        for admin_id in ADMIN_CHAT_IDS:
            try:
                await controller.send_message(admin_id, wave_summary, parse_mode='md')
            except Exception:
                pass
    
    async def run_auto_sequence():
        waves = [
            ("Wave 1 — INRfirst90rows + Nepal", [
                ('INRfirst90rows', 'india', 'India rows 1–90', GEO_CONFIG['india']['range'], 1, 90),
                ('nepal', 'nepal', 'Nepal all rows', GEO_CONFIG['nepal']['range'], 1, None),
            ]),
            ("Wave 2 — INRsecond90rows + Egypt", [
                ('INRsecond90rows', 'india', 'India rows 91–180', GEO_CONFIG['india']['range'], 91, 180),
                ('egypt', 'egypt', 'Egypt all rows', GEO_CONFIG['egypt']['range'], 1, None),
            ]),
            ("Wave 3 — INRlast + Sri Lanka", [
                ('INRlast', 'india', 'India rows 181+', GEO_CONFIG['india']['range'], 181, None),
                ('srilanka', 'srilanka', 'Sri Lanka all rows', GEO_CONFIG['srilanka']['range'], 1, None),
            ]),
        ]
        
        for i, (wave_label, pairs) in enumerate(waves):
            for admin_id in ADMIN_CHAT_IDS:
                try:
                    await controller.send_message(admin_id, f"🚀 *Auto-run: {wave_label} starting...*", parse_mode='md')
                except Exception:
                    pass
            
            await run_wave(wave_label, pairs)
            
            if i < len(waves) - 1:
                for admin_id in ADMIN_CHAT_IDS:
                    try:
                        await controller.send_message(admin_id, f"⏳ Waiting {AUTO_PAUSE_SECONDS // 60} minutes before next wave...")
                    except Exception:
                        pass
                await asyncio.sleep(AUTO_PAUSE_SECONDS)
        
        for admin_id in ADMIN_CHAT_IDS:
            try:
                await controller.send_message(admin_id, "🏁 *Auto-run complete. All waves done.*", parse_mode='md')
            except Exception:
                pass
    
    @controller.on(events.NewMessage(incoming=True))
    async def handle_auto_trigger(event):
        if event.raw_text.strip() != AUTO_TRIGGER_TEXT:
            return
        if APPSCRIPT_CHAT_ID == 0 or event.sender_id != APPSCRIPT_CHAT_ID:
            return
        print("🤖 Auto-trigger received — starting wave sequence.")
        asyncio.create_task(run_auto_sequence())
    
    await controller.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
