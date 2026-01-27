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
    
    # [NOWE] Logowanie w konsoli, że bot działa
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
                kupujacy = order["buyer"]["login"]
                kwota = order["summary"]["totalToPay"]["amount"]
                waluta = order["summary"]["totalToPay"]["currency"]
                
                # Budujemy listę produktów
                produkty_tekst = ""
                for item in order["lineItems"]:
                    offer_title = item["offer"]["name"]
                    qty = item["quantity"]
                    produkty_tekst += f"• {qty}x **{offer_title}**\n"

                # --- WYSYŁANIE NA KONKRETNY KANAŁ ---
                if TARGET_CHANNEL_ID == 0:
                     print("❌ Błąd: Nie ustawiłeś TARGET_CHANNEL_ID w kodzie!")
                     return

                channel = bot.get_channel(TARGET_CHANNEL_ID)
                
                if channel:
                    embed = discord.Embed(title="💰 NOWE ZAMÓWIENIE!", color=0xf1c40f)
                    embed.add_field(name="Kupujący", value=kupujacy, inline=True)
                    embed.add_field(name="Kwota", value=f"**{kwota} {waluta}**", inline=True)
                    embed.add_field(name="📦 Produkty", value=produkty_tekst, inline=False)
                    embed.set_footer(text=f"ID: {last_order_id} | {datetime.datetime.now().strftime('%H:%M')}")
                    
                    await channel.send(content="@here Wpadła kasa! 💸", embed=embed)
                else:
                    print(f"❌ Błąd: Nie znaleziono kanału o ID {TARGET_CHANNEL_ID}. Sprawdź ID czy bot ma do niego dostęp!")
                        
    except Exception as e:
        print(f"Błąd w pętli Allegro: {e}")

# --- LOGIKA AI ---
async def pobierz_analize_live(okres, kategoria):
    teraz = datetime.datetime.now().strftime("%d.%m.%Y")
    if kategoria.lower() in ["wszystko", "all", "ogólne", "top", "hity"]:
        temat = "OGÓLNE BESTSELLERY"
        skupienie = "Cały polski rynek e-commerce."
    else:
        temat = f"Kategoria/Nisza: {kategoria}"
        skupienie = f"Skup się dokładnie na: {kategoria}. Znajdź konkretne produkty."

    prompt = f"""
    Jesteś Ekspertem E-commerce. Data: {teraz}. Analiza na: {okres}.
    TEMAT: {temat}. {skupienie}
    ZASADY: 1. Zero HTML. Używaj Markdown. 2. Format listy.
    STRUKTURA RAPORTU (5 produktów):
    **[PEŁNA NAZWA PRODUKTU]**
    • 💰 Cena: [PLN]
    • 🗓️ Start: [Data]
    • 📈 PEAK: [Data]
    • 💡 Dlaczego teraz: [Powód]
    Na końcu: ⚠️ CZEGO UNIKAĆ.
    """
    try:
        response = await perplexity_client.chat.completions.create(
            model="sonar-pro", messages=[{"role": "user", "content": prompt}]
        )
        return clean_text(response.choices[0].message.content)
    except Exception as e: return f"Błąd AI: {str(e)}"

async def generuj_opis_gpsr(produkt):
    prompt = f"Napisz tekst GPSR dla: {produkt}. Zachowaj strukturę: 1. Bezpieczeństwo, 2. Dzieci, 3. Utylizacja. Bez Markdown."
    try:
        msg = await claude_client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=2500,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text
    except Exception as e: return f"Błąd: {e}"

# --- KOMENDY ---
@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user}")
    await bot.change_presence(activity=discord.Game(name="!pomoc | E-commerce"))
    # Startujemy pętlę monitorującą
    if not allegro_monitor.is_running():
        allegro_monitor.start()

@bot.command()
async def pomoc(ctx):
    await ctx.message.delete() # [NOWE] Czyści wiadomość użytkownika
    embed = discord.Embed(title="🛠️ Menu", color=0xff9900)
    embed.add_field(name="🟠 !allegro_login", value="Krok 1: Link do logowania", inline=False)
    embed.add_field(name="🟠 !allegro_kod [kod]", value="Krok 2: Wklej kod z linku", inline=False)
    embed.add_field(name="🔥 !hity", value="Najlepsze okazje", inline=False)
    embed.add_field(name="📈 !trend", value="Analiza kategorii", inline=False)
    embed.add_field(name="💰 !marza", value="Kalkulator", inline=False)
    embed.add_field(name="📄 !gpsr", value="Tekst prawny", inline=False)
    embed.add_field(name="🧪 !test_allegro", value="Test powiadomienia", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def allegro_login(ctx):
    """Generuje link do logowania Allegro"""
    await ctx.message.delete() # [NOWE]
    if not ALLEGRO_CLIENT_ID:
        return await ctx.send("❌ Brak Client ID w ustawieniach!")
        
    url = f"https://allegro.pl/auth/oauth/authorize?response_type=code&client_id={ALLEGRO_CLIENT_ID}&redirect_uri={ALLEGRO_REDIRECT_URI}"
    
    embed = discord.Embed(title="🔐 Logowanie do Allegro", color=0xff6600)
    embed.description = (
        "1. Kliknij w link poniżej.\n"
        "2. Potwierdź logowanie na Allegro.\n"
        "3. Zostaniesz przekierowany na stronę błędu (localhost) -> **TO NORMALNE**.\n"
        "4. Skopiuj kod z paska adresu przeglądarki (wszystko po `code=`).\n"
        "5. Wpisz tutaj: `!allegro_kod TWOJ_KOD`"
    )
    embed.add_field(name="🔗 Twój Link", value=f"[KLIKNIJ TUTAJ]({url})")
    await ctx.send(embed=embed)

@bot.command()
async def allegro_kod(ctx, code: str = None):
    """Wymienia kod na token"""
    await ctx.message.delete() # [NOWE]
    global allegro_token
    if not code: return await ctx.send("❌ Podaj kod!")
    
    msg = await ctx.send("🔄 Łączę z Allegro...")
    data = await get_allegro_token(code)
    
    if data and "access_token" in data:
        allegro_token = data["access_token"]
        await msg.edit(content=f"✅ **Sukces!** Połączono z kontem Allegro.\nTeraz będę sprawdzać zamówienia co 60 sekund.")
    else:
        await msg.edit(content=f"❌ Błąd logowania. Sprawdź czy kod jest poprawny (i świeży).")

@bot.command()
async def hity(ctx, *, okres: str = None):
    await ctx.message.delete() # [NOWE]
    if not okres:
        temp = await ctx.send("📅 Podaj miesiąc:")
        try:
            msg = await bot.wait_for('message', check=lambda m: m.
