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

# --- PĘTLA AUTO-RESPONDERA ---
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
    embed.add_field(name="🔑 Allegro", value="`!allegro_login`\n`!ostatnie`", inline=False)
    embed.add_field(name="🤖 Auto-Responder", value="`!auto_start`\n`!tryb_live`\n`!tryb_test`\n`!test_msg` (Symulacja)", inline=False)
    embed.add_field(name="🧠 Narzędzia", value="`!marza [zakup] [prowizja]` - Wylicz ceny\n`!marza [zakup] [sprzedaz] [prowizja]` - Sprawdź zysk\n`!trend` - Badanie rynku\n`!gpsr` - Teksty prawne", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def auto_start(ctx):
    await ctx.message.delete()
    global responder_active
    responder_active = True
    status = "TESTOWY (Bezpieczny)" if tryb_testowy else "LIVE (Wysyła wiadomości!)"
    await ctx.send(f"✅ Auto-Responder AKTYWOWANY. Tryb: **{status}**.")

@bot.command()
async def auto_stop(ctx):
    await ctx.message.delete()
    global responder_active
    responder_active = False
    await ctx.send("🛑 Auto-Responder ZATRZYMANY.")

@bot.command()
async def tryb_live(ctx):
    await ctx.message.delete()
    global tryb_testowy
    tryb_testowy = False
    await ctx.send("🔥 **UWAGA! Tryb LIVE włączony.** Bot będzie teraz automatycznie odpisywał klientom na Allegro!")

@bot.command()
async def tryb_test(ctx):
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

# --- NOWA LOGIKA MARŻY (VAT 23% + Ryczałt 3%) ---

@bot.command()
async def marza(ctx, arg1: str = None, arg2: str = None, arg3: str = None):
    """
    Kalkulator marży dla VAT-owca na ryczałcie 3%.
    Użycie:
    1. !marza [zakup] [prowizja_%] -> Pokaże tabelę cen.
    2. !marza [zakup] [sprzedaz] [prowizja_%] -> Obliczy dokładny zysk.
    """
    await ctx.message.delete()
    if not arg1 or not arg2:
        return await ctx.send("❌ Błąd. Użyj: `!marza [zakup] [prowizja]` LUB `!marza [zakup] [sprzedaz] [prowizja]`")
    
    try:
        zakup_brutto = float(arg1.replace(',', '.'))
        zakup_netto = zakup_brutto / 1.23
        
        # Wariant 1: !marza [zakup] [prowizja] -> Tabela sugerowanych cen
        # Jeśli arg2 jest mały (np. < 50), traktujemy go jako % prowizji, a nie cenę sprzedaży.
        # Chyba że podano 3 argumenty - wtedy wchodzimy w wariant 2.
        
        is_table_mode = (arg3 is None)
        
        if is_table_mode:
            prowizja_proc = float(arg2.replace(',', '.')) / 100.0
            
            embed = discord.Embed(title=f"📊 Kalkulacja (VAT + Ryczałt 3%)", color=0x3498db)
            embed.description = f"Zakup: **{zakup_brutto} zł**. Prowizja Allegro: **{prowizja_proc*100:.1f}%**"
            
            for cel in [10, 20, 30, 50, 100]:
                # Wzór odwrócony:
                # Cena Brutto = (Zysk_Cel * 1.23 + Zakup_Brutto) / (0.97 - Prowizja)
                # Wyjaśnienie: 0.97 to (1 - 0.03 ryczałtu).
                
                mianownik = 0.97 - prowizja_proc
                if mianownik <= 0:
                    cena_brutto = 0 # Zabezpieczenie przed dzieleniem przez zero/minus
                else:
                    cena_brutto = (cel * 1.23 + zakup_brutto) / mianownik

                embed.add_field(name=f"Zysk {cel} zł", value=f"Sprzedaj za: **{cena_brutto:.2f} zł**", inline=True)
            
            embed.set_footer(text="Ceny uwzględniają: VAT 23% (odliczony), Prowizję i Ryczałt 3%.")
            await ctx.send(embed=embed)
            
        else:
            # Wariant 2: !marza [zakup] [sprzedaz] [prowizja]
            sprzedaz_brutto = float(arg2.replace(',', '.'))
            prowizja_proc = float(arg3.replace(',', '.')) / 100.0
            
            sprzedaz_netto = sprzedaz_brutto / 1.23
            
            # Koszty
            prowizja_allegro_netto = (sprzedaz_brutto * prowizja_proc) / 1.23
            ryczalt = sprzedaz_netto * 0.03 # Ryczałt 3% od przychodu netto
            
            zysk_na_czysto = sprzedaz_netto - zakup_netto - prowizja_allegro_netto - ryczalt
            
            kolor = 0x2ecc71 if zysk_na_czysto > 0 else 0xe74c3c
            
            embed = discord.Embed(title="Wynik Transakcji (VAT + Ryczałt)", color=kolor)
            embed.add_field(name="1. Zakup", value=f"{zakup_brutto:.2f} zł", inline=True)
            embed.add_field(name="2. Sprzedaż", value=f"{sprzedaz_brutto:.2f} zł", inline=True)
            embed.add_field(name="3. Prowizja", value=f"{prowizja_proc*100:.1f}%", inline=True)
            
            embed.add_field(name="---", value="---", inline=False)
            
            details = (
                f"Zakup Netto: {zakup_netto:.2f} zł\n"
                f"Sprzedaż Netto: {sprzedaz_netto:.2f} zł\n"
                f"Koszt Allegro (netto): -{prowizja_allegro_netto:.2f} zł\n"
                f"Podatek Ryczałt (3%): -{ryczalt:.2f} zł"
            )
            embed.add_field(name="Szczegóły", value=details, inline=False)
            embed.add_field(name="ZYSK NA CZYSTO", value=f"💰 **{zysk_na_czysto:.2f} zł**", inline=False)
            
            await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"❌ Błąd: {e}\nUżyj: `!marza 100 200 10` (Kupno, Sprzedaż, Prowizja%)")

# --- RESZTA KOMEND ---

@bot.command()
async def trend(ctx, *, kategoria: str = None):
    await ctx.message.delete()
    if not kategoria: return await ctx.send("❌ Podaj kategorię, np. `!trend Smartwatche`")
    msg = await ctx.send(f"⏳ **Analizuję: {kategoria}...**")
    raport = await pobierz_analize_live("Obecny miesiąc", kategoria)
    if len(raport) > 4000: raport = raport[:4000] + "..."
    await msg.edit(content=None, embed=discord.Embed(title=f"📈 Trend: {kategoria}", description=raport, color=0x9b59b6))

@bot.command()
async def test_allegro(ctx):
    await ctx.message.delete()
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        embed = discord.Embed(title="💰 TEST ZAMÓWIENIA", color=0xf1c40f)
        embed.add_field(name="Kupujący", value="TestUser123", inline=True)
        embed.add_field(name="Kwota", value="**149.99 PLN**", inline=True)
        embed.add_field(name="📦 Produkty", value="• 1x **Przykładowy Produkt Premium**\n• 2x **Gratis**", inline=False)
        embed.set_footer(text=f"ID: TEST-12345 | {polski_czas()}")
        await channel.send(content="@here Test! 💸", embed=embed)
    else:
        await ctx.send(f"❌ Błąd kanału ID: {TARGET_CHANNEL_ID}")

@bot.command()
async def test_msg(ctx):
    await ctx.message.delete()
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        embed = discord.Embed(title="🛡️ AUTO-RESPONDER (SYMULACJA)", color=0x3498db)
        embed.description = f"Klient napisał: *Dzień dobry, kiedy wyślecie paczkę?*\n\n**W trybie LIVE bot odpisałby:**\n{AUTO_REPLY_MSG}"
        embed.set_footer(text="To jest tylko test wyglądu.")
        await channel.send(embed=embed)
    else:
        await ctx.send("❌ Błąd kanału.")

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
