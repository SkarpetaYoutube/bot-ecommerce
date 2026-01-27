import discord
from discord.ext import commands, tasks
import asyncio
import datetime
import os
import aiohttp
import base64
import json
import random
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

# TREŚĆ AUTOMATYCZNEJ ODPOWIEDZI
AUTO_REPLY_MSG = (
    "Dzień dobry! Dziękujemy za wiadomość. Właśnie ją odebraliśmy. "
    "Obecnie weryfikujemy sprawę i wrócimy z konkretną odpowiedzią najszybciej jak to możliwe. "
    "Pozdrawiamy!"
)

# Klienci AI
claude_client = AsyncAnthropic(api_key=CLAUDE_KEY)
perplexity_client = AsyncOpenAI(api_key=PERPLEXITY_KEY, base_url="https://api.perplexity.ai")

# Zmienne globalne
allegro_token = None
last_order_id = None
tryb_testowy = True  # DOMYŚLNIE TRUE (BEZPIECZNIE)
responder_active = False # Czy auto-responder jest włączony

# Konfiguracja bota
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# --- FUNKCJE POMOCNICZE ---
def clean_text(text):
    if not text: return ""
    text = text.replace("**", "").replace("##", "").replace("###", "")
    return text.strip()

def polski_czas():
    """Zwraca godzinę w polskiej strefie czasowej (UTC+1)"""
    czas_pl = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    return czas_pl.strftime('%H:%M')

def czy_swieze_zamowienie(data_str):
    """Sprawdza, czy zamówienie jest młodsze niż 20 minut"""
    try:
        data_zamowienia = datetime.datetime.fromisoformat(data_str.replace('Z', '+00:00'))
        teraz_utc = datetime.datetime.now(datetime.timezone.utc)
        roznica = teraz_utc - data_zamowienia
        return roznica.total_seconds() < 1200
    except Exception as e:
        print(f"⚠️ Błąd daty: {e}")
        return True 

# --- LOGIKA ALLEGRO (API) ---
async def get_allegro_token(auth_code):
    auth_str = f"{ALLEGRO_CLIENT_ID}:{ALLEGRO_CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    url = "https://allegro.pl/auth/oauth/token"
    headers = {"Authorization": f"Basic {b64_auth}"}
    data = {"grant_type": "authorization_code", "code": auth_code, "redirect_uri": ALLEGRO_REDIRECT_URI}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=data) as resp:
            if resp.status == 200: return await resp.json()
            return None

async def fetch_orders():
    global allegro_token
    if not allegro_token: return None
    url = "https://api.allegro.pl/order/checkout-forms?limit=5"
    headers = {"Authorization": f"Bearer {allegro_token}", "Accept": "application/vnd.allegro.public.v1+json"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200: return await resp.json()
            return None

# --- AUTO-RESPONDER LOGIKA ---
async def pobierz_wiadomosci():
    global allegro_token
    if not allegro_token: return None
    # Pobieramy wątki, które są nieprzeczytane (limit 5 wystarczy)
    url = "https://api.allegro.pl/messaging/threads?limit=5" 
    headers = {"Authorization": f"Bearer {allegro_token}", "Accept": "application/vnd.allegro.public.v1+json"}
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200: return await resp.json()
            return None

async def wyslij_odpowiedz(thread_id, text):
    global allegro_token
    url = f"https://api.allegro.pl/messaging/threads/{thread_id}/messages"
    headers = {
        "Authorization": f"Bearer {allegro_token}",
        "Accept": "application/vnd.allegro.public.v1+json",
        "Content-Type": "application/vnd.allegro.public.v1+json"
    }
    payload = {"text": text}
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            return resp.status == 201

async def oznacz_jako_przeczytane(thread_id, last_msg_id):
    # To jest ważne, żeby bot nie odpisywał w kółko na to samo
    global allegro_token
    url = f"https://api.allegro.pl/messaging/threads/{thread_id}/read"
    headers = {"Authorization": f"Bearer {allegro_token}", "Accept": "application/vnd.allegro.public.v1+json", "Content-Type": "application/vnd.allegro.public.v1+json"}
    payload = {"lastSeenMessageId": last_msg_id}
    async with aiohttp.ClientSession() as session:
        await session.put(url, headers=headers, json=payload)

# --- PĘTLA AUTO-RESPONDERA (NOWOŚĆ) ---
@tasks.loop(minutes=3) # Sprawdza co 3 minuty
async def allegro_responder():
    global allegro_token, tryb_testowy, responder_active
    
    if not responder_active or not allegro_token: return

    try:
        data = await pobierz_wiadomosci()
        if not data or "threads" not in data: return

        for thread in data["threads"]:
            # Sprawdzamy czy wątek jest nieprzeczytany
            if thread["read"] == False:
                last_msg = thread["lastMessage"]
                author_role = last_msg["author"]["role"]
                thread_id = thread["id"]
                
                # BEZPIECZEŃSTWO: Odpisujemy TYLKO jeśli ostatni napisał KUPUJĄCY (BUYER)
                if author_role == "BUYER":
                    
                    if tryb_testowy:
                        # --- TRYB TESTOWY (TYLKO DISCORD) ---
                        channel = bot.get_channel(TARGET_CHANNEL_ID)
                        if channel:
                            embed = discord.Embed(title="🛡️ AUTO-RESPONDER (TEST)", color=0x3498db)
                            embed.description = f"Klient napisał: *{last_msg['text']}*\n\n**W trybie LIVE bot odpisałby:**\n{AUTO_REPLY_MSG}"
                            embed.set_footer(text="Wpisz !tryb_live aby włączyć wysyłanie.")
                            await channel.send(embed=embed)
                        
                        # Oznaczamy jako przeczytane w systemie bota (żeby nie spamował na DC), ale nie na Allegro
                        # W trybie testowym to trudne, bo nie chcemy ingerować w Allegro.
                        # Dlatego w trybie testowym bot może powtórzyć powiadomienie na DC co 3 minuty, dopóki sam nie odpiszesz.
                        pass 
                    
                    else:
                        # --- TRYB LIVE (PRAWDZIWE WYSYŁANIE) ---
                        sukces = await wyslij_odpowiedz(thread_id, AUTO_REPLY_MSG)
                        if sukces:
                            print(f"✅ Odpisano automatycznie do wątku {thread_id}")
                            # Oznaczamy jako przeczytane, żeby nie odpisać 2 razy
                            await oznacz_jako_przeczytane(thread_id, last_msg["id"])
                            
                            # Info na Discord
                            channel = bot.get_channel(TARGET_CHANNEL_ID)
                            if channel:
                                await channel.send(f"🤖 **Auto-Reply wysłane!** Odpisałem klientowi na wiadomość.")
                        else:
                            print(f"❌ Błąd wysyłania odpowiedzi do {thread_id}")

    except Exception as e:
        print(f"Błąd Responderea: {e}")


# --- PĘTLA SPRAWDZAJĄCA ZAMÓWIENIA (POLLING) ---
@tasks.loop(seconds=60)
async def allegro_monitor():
    global last_order_id, allegro_token
    if not allegro_token: return 
    try:
        data = await fetch_orders()
        if not data or "checkoutForms" not in data: return
        orders = data["checkoutForms"]
        if not orders: return
        orders.sort(key=lambda x: x["updatedAt"])
        if last_order_id is None:
            last_order_id = orders[-1]["id"]
            print(f"✅ Baza zamówień ustawiona na ID: {last_order_id}")
            return
        for order in orders:
            if order["id"] > last_order_id:
                last_order_id = order["id"] 
                if not czy_swieze_zamowienie(order["updatedAt"]):
                    print(f"⏳ Pominięto stare zamówienie (ID: {order['id']})")
                    continue 
                kupujacy = order["buyer"]["login"]
                kwota = order["summary"]["totalToPay"]["amount"]
                waluta = order["summary"]["totalToPay"]["currency"]
                produkty_tekst = ""
                for item in order["lineItems"]:
                    produkty_tekst += f"• {item['quantity']}x **{item['offer']['name']}**\n"
                channel = bot.get_channel(TARGET_CHANNEL_ID)
                if channel:
                    embed = discord.Embed(title="💰 NOWE ZAMÓWIENIE!", color=0xf1c40f)
                    embed.add_field(name="Kupujący", value=kupujacy, inline=True)
                    embed.add_field(name="Kwota", value=f"**{kwota} {waluta}**", inline=True)
                    embed.add_field(name="📦 Produkty", value=produkty_tekst, inline=False)
                    embed.set_footer(text=f"ID: {last_order_id} | {polski_czas()}")
                    await channel.send(content="@here Wpadła kasa! 💸", embed=embed)
    except Exception as e:
        print(f"Błąd w pętli Allegro: {e}")

# --- LOGIKA AI ---
async def pobierz_analize_live(okres, kategoria):
    teraz = datetime.datetime.now().strftime("%d.%m.%Y")
    prompt = f"Ekspert E-commerce. Data: {teraz}. Analiza: {okres}. Temat: {kategoria}. Wymień 5 hitów sprzedażowych w Polsce (Markdown, lista)."
    try:
        if not PERPLEXITY_KEY: return "❌ Brak klucza Perplexity."
        response = await perplexity_client.chat.completions.create(model="sonar-pro", messages=[{"role": "user", "content": prompt}])
        return clean_text(response.choices[0].message.content)
    except Exception as e: return f"Błąd AI: {str(e)}"

async def generuj_opis_gpsr(produkt):
    prompt = f"Napisz tekst GPSR dla: {produkt}. Struktura: 1. Bezpieczeństwo, 2. Dzieci, 3. Utylizacja."
    try:
        if not CLAUDE_KEY: return "❌ Brak klucza Claude."
        msg = await claude_client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=2500, messages=[{"role": "user", "content": prompt}])
        return msg.content[0].text
    except Exception as e: return f"Błąd: {e}"

# --- EVENTY I START ---
@bot.event
async def on_ready():
    print(f"✅ ZALOGOWANO JAKO: {bot.user}")
    await bot.change_presence(activity=discord.Game(name="!pomoc | E-commerce"))
    if not allegro_monitor.is_running():
        allegro_monitor.start()
    if not allegro_responder.is_running():
        allegro_responder.start()

# --- KOMENDY ---
@bot.command()
async def pomoc(ctx):
    await ctx.message.delete()
    embed = discord.Embed(title="🛠️ Menu Bota", color=0xff9900)
    embed.add_field(name="🔑 Allegro", value="`!allegro_login` - Logowanie\n`!ostatnie` - Ost. zamówienie", inline=False)
    embed.add_field(name="🤖 Auto-Responder", value="`!auto_start` - Włącz sprawdzanie wiadomości\n`!tryb_live` - Włącz wysyłanie (OSTROŻNIE!)\n`!tryb_test` - Włącz tylko podgląd", inline=False)
    embed.add_field(name="🧠 AI", value="`!hity`, `!trend`, `!gpsr`", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def auto_start(ctx):
    """Włącza pętlę respondera"""
    await ctx.message.delete()
    global responder_active
    responder_active = True
    status = "TESTOWY (Bezpieczny)" if tryb_testowy else "LIVE (Wysyła wiadomości!)"
    await ctx.send(f"✅ Auto-Responder AKTYWOWANY. Tryb: **{status}**.")

@bot.command()
async def auto_stop(ctx):
    """Wyłącza respondera"""
    await ctx.message.delete()
    global responder_active
    responder_active = False
    await ctx.send("🛑 Auto-Responder ZATRZYMANY.")

@bot.command()
async def tryb_live(ctx):
    """Włącza prawdziwe wysyłanie wiadomości"""
    await ctx.message.delete()
    global tryb_testowy
    tryb_testowy = False
    await ctx.send("🔥 **UWAGA! Tryb LIVE włączony.** Bot będzie teraz automatycznie odpisywał klientom na Allegro!")

@bot.command()
async def tryb_test(ctx):
    """Włącza tryb bezpieczny"""
    await ctx.message.delete()
    global tryb_testowy
    tryb_testowy = True
    await ctx.send("🛡️ Tryb TESTOWY włączony. Bot nie będzie wysyłał wiadomości do klientów, tylko powiadomi na Discordzie.")

@bot.command()
async def allegro_login(ctx):
    await ctx.message.delete()
    if not ALLEGRO_CLIENT_ID: return await ctx.send("❌ Brak Client ID!")
    url = f"https://allegro.pl/auth/oauth/authorize?response_type=code&client_id={ALLEGRO_CLIENT_ID}&redirect_uri={ALLEGRO_REDIRECT_URI}"
    embed = discord.Embed(title="🔐 Logowanie", description=f"[KLIKNIJ]({url})\nSkopiuj kod i wpisz: `!allegro_kod TWÓJ_KOD`", color=0xff6600)
    await ctx.send(embed=embed)

@bot.command()
async def allegro_kod(ctx, code: str = None):
    await ctx.message.delete()
    global allegro_token
    if not code: return await ctx.send("❌ Podaj kod!")
    msg = await ctx.send("🔄 Łączę...")
    data = await get_allegro_token(code)
    if data and "access_token" in data:
        allegro_token = data["access_token"]
        await msg.edit(content="✅ **Sukces!** Połączono z Allegro.")
    else:
        await msg.edit(content="❌ Błąd logowania.")

@bot.command()
async def hity(ctx, *, okres: str = None):
    await ctx.message.delete()
    if not okres: return await ctx.send("❌ Podaj okres.")
    msg = await ctx.send(f"⏳ Szukam hitów: {okres}...")
    raport = await pobierz_analize_live(okres, "Wszystko")
    await msg.edit(content=None, embed=discord.Embed(title=f"🏆 Hity: {okres}", description=raport[:4000], color=0xe74c3c))

@bot.command()
async def gpsr(ctx, *, produkt: str = None):
    await ctx.message.delete()
    if not produkt: return await ctx.send("❌ Podaj produkt!")
    msg = await ctx.send("⚖️ Generuję GPSR...")
    tresc = await generuj_opis_gpsr(produkt)
    await msg.edit(content=None, embed=discord.Embed(description=f"```text\n{tresc}\n```", color=0x3498db))

@bot.command()
async def ostatnie(ctx):
    await ctx.message.delete()
    if not allegro_token: return await ctx.send("❌ Zaloguj się!")
    msg = await ctx.send("🔍 Pobieram...")
    try:
        data = await fetch_orders()
        if not data or "checkoutForms" not in data or not data["checkoutForms"]: return await msg.edit(content="ℹ️ Brak zamówień.")
        orders = data["checkoutForms"]
        orders.sort(key=lambda x: x["updatedAt"])
        last = orders[-1]
        prod = ", ".join([i["offer"]["name"] for i in last["lineItems"]])
        embed = discord.Embed(title="🛒 OSTATNIE", color=0x2ecc71)
        embed.add_field(name="Kwota", value=f"{last['summary']['totalToPay']['amount']} PLN")
        embed.add_field(name="Produkt", value=prod)
        embed.set_footer(text=f"ID: {last['id']}")
        await msg.edit(content=None, embed=embed)
    except Exception as e: await msg.edit(content=f"Błąd: {e}")

if __name__ == "__main__":
    keep_alive()
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"❌ START ERROR: {e}")
