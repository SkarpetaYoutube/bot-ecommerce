import discord
from discord.ext import commands, tasks
import asyncio
import datetime
import os
import aiohttp
import base64
import json
from anthropic import AsyncAnthropic 
from openai import AsyncOpenAI 
from keep_alive import keep_alive 

# --- KONFIGURACJA ---
TOKEN = os.environ.get("DISCORD_TOKEN")
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY") or os.environ.get("CLAUDE_TOKEN")
PERPLEXITY_KEY = os.environ.get("PERPLEXITY_API_KEY") or os.environ.get("PERPLEXITY_TOKEN")

# Konfiguracja Allegro
ALLEGRO_CLIENT_ID = os.environ.get("ALLEGRO_CLIENT_ID")
ALLEGRO_CLIENT_SECRET = os.environ.get("ALLEGRO_CLIENT_SECRET")
ALLEGRO_REDIRECT_URI = "http://localhost:8000"

# --- ID KANAŁU ---
# Upewnij się, że to ID jest poprawne (powinno być liczbą, nie stringiem)
TARGET_CHANNEL_ID = 1464959293681045658

if not CLAUDE_KEY or not PERPLEXITY_KEY:
    print("⚠️ OSTRZEŻENIE: Brakuje kluczy AI!")
if not ALLEGRO_CLIENT_ID:
    print("⚠️ OSTRZEŻENIE: Brakuje Client ID Allegro!")

# Klienci AI
claude_client = AsyncAnthropic(api_key=CLAUDE_KEY)
perplexity_client = AsyncOpenAI(api_key=PERPLEXITY_KEY, base_url="https://api.perplexity.ai")

# Zmienne globalne Allegro (przechowywane w pamięci)
allegro_token = None
last_order_id = None

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# --- FUNKCJE POMOCNICZE ---
def clean_text(text):
    if not text: return ""
    text = text.replace("**", "").replace("##", "").replace("###", "")
    return text.strip()

# --- LOGIKA ALLEGRO ---
async def get_allegro_token(auth_code):
    """Wymienia kod z linku na token dostępu"""
    auth_str = f"{ALLEGRO_CLIENT_ID}:{ALLEGRO_CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    
    url = "https://allegro.pl/auth/oauth/token"
    headers = {"Authorization": f"Basic {b64_auth}"}
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": ALLEGRO_REDIRECT_URI
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=data) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                return None

async def fetch_orders():
    """Pobiera ostatnie zamówienia z Allegro"""
    global allegro_token
    if not allegro_token: return None
    
    url = "https://api.allegro.pl/order/checkout-forms?limit=5"
    headers = {
        "Authorization": f"Bearer {allegro_token}",
        "Accept": "application/vnd.allegro.public.v1+json"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                return await resp.json()
            return None

# --- PĘTLA SPRAWDZAJĄCA ZAMÓWIENIA (POLLING) ---
@tasks.loop(seconds=60)
async def allegro_monitor():
    global last_order_id, allegro_token
    
    # [LOGI] Logowanie w konsoli, że bot działa (Serce bota)
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔍 Sprawdzam Allegro...")

    if not allegro_token: return # Nie jesteśmy zalogowani

    try:
        data = await fetch_orders()
        if not data or "checkoutForms" not in data: return

        orders = data["checkoutForms"]
        if not orders: return

        # Sortujemy od najstarszego do najnowszego
        orders.sort(key=lambda x: x["updatedAt"])
        
        # Jeśli to pierwsze uruchomienie, zapamiętujemy najnowsze i nie spamujemy
        if last_order_id is None:
            last_order_id = orders[-1]["id"]
            print(f"✅ Allegro połączone. Ostatnie zamówienie ID: {last_order_id}")
            return

        # Szukamy nowych zamówień
        for order in orders:
            # Sprawdzamy czy to zamówienie jest nowsze niż ostatnie zapamiętane
            if order["id"] > last_order_id:
                last_order_id = order["id"]
                
                # Wyciągamy dane do powiadomienia
                kup
