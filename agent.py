import os
import json
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
from openai import OpenAI
from tavily import TavilyClient

# --- سرور سلامت برای Render (جلوگیری از Sleep) ---
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args): pass

def run_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Health server on port {port}")
    server.serve_forever()

Thread(target=run_health_server, daemon=True).start()
# ---------------------------------------------------

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o")
TAVILY_KEY = os.getenv("TAVILY_API_KEY")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))
PROXY_URL = os.getenv("PROXY_URL", None)

client = OpenAI(api_key=OPENAI_KEY, base_url=BASE_URL)
tavily = TavilyClient(api_key=TAVILY_KEY) if TAVILY_KEY else None

TASKS_FILE = "tasks.json"
MEMORY_FILE = "memory.json"

def load_json(file, default):
    if os.path.exists(file):
        try:
            with open(file, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return default
    return default

def save_json(file, data):
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def search_web(query: str):
    if not tavily:
        return "کلید Tavily تنظیم نشده"
    try:
        result = tavily.search(query=query, max_results=5, include_answer=True)
        answer = result.get('answer', '')
        results = result.get('results', [])[:2]
        return answer + "\n\n" + str(results)
    except Exception as e:
        return f"خطا در جستجو: {e}"

def manage_task(action: str, task: str = "", task_id: int = 0):
    tasks = load_json(TASKS_FILE, [])
    if action == "add":
        tasks.append({"id": len(tasks)+1, "task": task, "status": "pending"})
        save_json(TASKS_FILE, tasks)
        return f"تسک اضافه شد: {task}"
    elif action == "list":
        if not tasks: return "هیچ تسکی وجود ندارد"
        return "\n".join([f"{t['id']}. {t['task']} - {t['status']}" for t in tasks])
    elif action == "done":
        for t in tasks:
            if t['id'] == task_id: t['status'] = "done"
        save_json(TASKS_FILE, tasks)
        return f"تسک {task_id} انجام شد"
    elif action == "delete":
        tasks = [t for t in tasks if t['id'] != task_id]
        save_json(TASKS_FILE, tasks)
        return f"تسک {task_id} حذف شد"
    return "عملیات نامعتبر"

def remember_info(key: str, value: str = "", action: str = "save"):
    memory = load_json(MEMORY_FILE, {})
    if action == "save":
        memory[key] = value
        save_json(MEMORY_FILE, memory)
        return f"ذخیره شد: {key} = {value}"
    elif action == "get":
        return memory.get(key, "چیزی یافت نشد")
    elif action == "list":
        if not memory: return "حافظه خالی است"
        return json.dumps(memory, ensure_ascii=False, indent=2)
    return "خطا"

tools = [
    {"type": "function", "function": {"name": "search_web", "description": "جستجو در اینترنت برای اطلاعات روز", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "manage_task", "description": "مدیریت تسک ها و کارها", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["add", "list", "done", "delete"]}, "task": {"type": "string"}, "task_id": {"type": "integer"}}, "required": ["action"]}}},
    {"type": "function", "function": {"name": "remember_info", "description": "ذخیره یا یادآوری اطلاعات مهم کاربر", "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}, "action": {"type": "string", "enum": ["save", "get", "list"]}}, "required": ["key", "action"]}}}
]

SYSTEM_PROMPT = """
تو یک دستیار هوشمند خودمختار و بسیار دقیق هستی.
وظیفه تو اینه که دستورات کاربر که از تلگرام میاد رو با فکر کردن مرحله به مرحله و استفاده از ابزارها اجرا کنی.
- اگر کاربر گفت "یاد بگیر" یا "ذخیره کن" از remember_info استفاده کن
- اگر تسکی داد از manage_task استفاده کن
- اگر سوالی پرسید که نیاز به اطلاعات روز داره از search_web استفاده کن
- همیشه فارسی و صمیمی جواب بده و در آخر گزارش بده چه کردی
"""

async def agent_brain(user_text: str):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_text}]
    try:
        response = client.chat.completions.create(model=MODEL_NAME, messages=messages, tools=tools, tool_choice="auto")
        msg = response.choices[0].message

        if msg.tool_calls:
            messages.append(msg)
            executed_tools = []
            for tool_call in msg.tool_calls:
                name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                print(f"Agent در حال اجرای ابزار: {name} {args}")
                if name == "search_web": result = search_web(args["query"])
                elif name == "manage_task": result = manage_task(args["action"], args.get("task",""), args.get("task_id",0))
                elif name == "remember_info": result = remember_info(args["key"], args.get("value",""), args["action"])
                else: result = "ابزار یافت نشد"
                executed_tools.append(f"✅ {name}: {result}")
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
            
            final = client.chat.completions.create(model=MODEL_NAME, messages=messages)
            final_answer = final.choices[0].message.content
            if not final_answer or final_answer.strip() == "":
                return "\n".join(executed_tools)
            return final_answer
        else:
            return msg.content if msg.content else "متوجه نشدم، میشه دوباره بگی؟"
    except Exception as e:
        print(f"خطا در agent_brain: {e}")
        return f"متاسفانه خطایی رخ داد: {str(e)[:200]}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID: return
    await update.message.reply_text(
        "سلام! من دستیار هوشمند شما هستم 🤖\n\n"
        "دستورات:\n"
        "/task [متن تسک] - افزودن تسک جدید\n"
        "/tasks - لیست تسک ها\n"
        "/done [شماره] - انجام شد\n"
        "یا هر پیامی بفرست، من میفهمم چیکار کنم!\n"
        "مثلا بگو: 'یاد بگیر پروژه من فروشگاه اینترنتیه' یا 'قیمت دلار امروز چنده؟'"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("⛔ شما دسترسی ندارید")
        return
    user_text = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    if user_text.startswith("/task "):
        result = manage_task("add", task=user_text[6:])
        await update.message.reply_text(result)
        return
    if user_text.startswith("/tasks"):
        result = manage_task("list")
        await update.message.reply_text(result)
        return
    if user_text.startswith("/done "):
        try:
            tid = int(user_text.split()[1])
            result = manage_task("done", task_id=tid)
            await update.message.reply_text(result)
        except: await update.message.reply_text("فرمت: /done 1")
        return
    answer = await agent_brain(user_text)
    if not answer or answer.strip() == "":
        answer = "انجام شد ✅"
    await update.message.reply_text(answer)

def main():
    request_kwargs = {
        "connection_pool_size": 8,
        "connect_timeout": 30.0,
        "read_timeout": 30.0
    }
    if PROXY_URL:
        request_kwargs["proxy_url"] = PROXY_URL
        print(f"استفاده از پروکسی: {PROXY_URL}")
    
    request = HTTPXRequest(**request_kwargs)
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.COMMAND, handle_message))
    print(f"Agent آماده است | مدل: {MODEL_NAME} | پروایدر: {BASE_URL}")
    app.run_polling()

if __name__ == "__main__":
    main()
