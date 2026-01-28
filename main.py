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
from keep_alive import keep_alive  # <--- To musi być w pliku keep_alive.py

# --- KONFIGURACJA! ---
TOKEN = os.environ.get("DISCORD_TOKEN")
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY") or os.environ.get("CLAUDE_TOKEN")
PERPLEXITY_KEY = os.environ.get("PERPLEXITY_API_KEY") or os.environ.get("PERPLEXITY_TOKEN")

# Konfiguracja Allegro
ALLEGRO_CLIENT_ID = os.environ.get("ALLEGRO_CLIENT_ID")
ALLEGRO_CLIENT_SECRET = os.environ.get("ALLEGRO_CLIENT_SECRET")
ALLEGRO_REDIRECT_URI = "http://localhost:8000"

# --- ID KANAŁÓW ---
KANAL_ZAMOWIENIA_ID = 1464959293681045658
KANAL_WIADOMOSCI_ID = 1465688093808922728 

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
# Zmienne globalne
allegro_token = None
processed_order_ids = set()  # <--- NOWA ZMIENNA: Zbiór obsłużonych ID
processed_msg_ids = set()
tryb_testowy = True
responder_active = False

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
    czas_pl = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    return czas_pl.strftime('%H:%M')

def czy_swieze_zamowienie(data_str):
    try:
        data_zamowienia = datetime.datetime.fromisoformat(data_str.replace('Z', '+00:00'))
        teraz_utc = datetime.datetime.now(datetime.timezone.utc)
        roznica = teraz_utc - data_zamowienia
        # ZMIANA: Wydłużono czas do 3600 sekund (1h) na wypadek restartu bota
        return roznica.total_seconds() < 3600
    except Exception as e:
        print(f"⚠️ Błąd daty: {e}")
        return True 

def parsuj_liczbe(tekst):
    if not tekst: return 0.0
    tekst = str(tekst).replace(',', '.').replace('%', '').strip()
    try:
        return float(tekst)
    except ValueError:
        return 0.0

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

async def pobierz_wiadomosci():
    global allegro_token
    if not allegro_token: return None
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
    global allegro_token
    url = f"https://api.allegro.pl/messaging/threads/{thread_id}/read"
    headers = {"Authorization": f"Bearer {allegro_token}", "Accept": "application/vnd.allegro.public.v1+json", "Content-Type": "application/vnd.allegro.public.v1+json"}
    payload = {"lastSeenMessageId": last_msg_id}
    async with aiohttp.ClientSession() as session:
        await session.put(url, headers=headers, json=payload)

# --- PĘTLA AUTO-RESPONDERA ---
@tasks.loop(minutes=2) 
async def allegro_responder():
    global allegro_token, tryb_testowy, responder_active, processed_msg_ids
    if not allegro_token: return

    try:
        data = await pobierz_wiadomosci()
        if not data or "threads" not in data: return

        for thread in data["threads"]:
            last_msg = thread["lastMessage"]
            msg_id = last_msg["id"]
            author_role = last_msg["author"]["role"]
            thread_id = thread["id"]
            
            is_fresh = czy_swieze_zamowienie(last_msg["createdAt"]) 

            # POWIADOMIENIE
            if author_role == "BUYER" and is_fresh and msg_id not in processed_msg_ids:
                processed_msg_ids.add(msg_id)
                
                channel = bot.get_channel(KANAL_WIADOMOSCI_ID)
                if channel:
                    embed = discord.Embed(title="📩 NOWA WIADOMOŚĆ", color=0x3498db)
                    embed.add_field(name="Klient", value=thread["interlocutor"]["login"], inline=True)
                    embed.add_field(name="Treść", value=f"*{last_msg['text']}*", inline=False)
                    
                    status_ar = "✅ Włączony" if responder_active else "❌ Wyłączony (Tylko powiadomienie)"
                    if thread["read"]: status_ar += " (Odczytana na Allegro)"
                    
                    embed.set_footer(text=f"Auto-Reply: {status_ar} | {polski_czas()}")
                    await channel.send(content="@here Klient pisze!", embed=embed)
                    print(f"✅ Wysłano powiadomienie o wiadomości ID: {msg_id}")

            # AUTO-REPLY
            if responder_active and thread["read"] == False and author_role == "BUYER":
                if tryb_testowy:
                    print(f"🛡️ [TEST] Bot odpisałby na wątek {thread_id}")
                    pass 
                else:
                    sukces = await wyslij_odpowiedz(thread_id, AUTO_REPLY_MSG)
                    if sukces:
                        print(f"🤖 Odpisano automatycznie do wątku {thread_id}")
                        await oznacz_jako_przeczytane(thread_id, msg_id)
                        
                        channel = bot.get_channel(KANAL_WIADOMOSCI_ID)
                        if channel:
                            await channel.send(f"🤖 **Auto-Reply:** Wysłano odpowiedź do klienta.")
                    else:
                        print(f"❌ Błąd wysyłania odpowiedzi do {thread_id}")

    except Exception as e:
        print(f"Błąd Respondera: {e}")

# --- PĘTLA SPRAWDZAJĄCA ZAMÓWIENIA (POPRAWIONA) ---
# --- PĘTLA SPRAWDZAJĄCA ZAMÓWIENIA (POPRAWIONA v2) ---
@tasks.loop(seconds=60)
async def allegro_monitor():
    global processed_order_ids, allegro_token
    
    if not allegro_token:
        return 

    try:
        data = await fetch_orders()
        if not data or "checkoutForms" not in data: return
        
        orders = data["checkoutForms"]
        if not orders: return
        
        # Sortujemy od najstarszego, żeby przetwarzać chronologicznie
        orders.sort(key=lambda x: x["updatedAt"])
        
        # --- INICJALIZACJA PO RESTARCIE ---
        # Jeśli zbiór jest pusty (bot dopiero wstał), dodajemy obecne zamówienia do pamięci,
        # żeby nie spamował starymi, ALE przepuszczamy te bardzo świeże (np. z ostatnich 5 min).
        if not processed_order_ids:
            print("⚙️ Inicjalizacja bazy zamówień...")
            for order in orders:
                processed_order_ids.add(order["id"])
            # Po pierwszym przebiegu kończymy, żeby nie wysłać powiadomień o starych
            # (Chyba że chcesz, aby po restarcie wysłał ostatnie - wtedy usuń 'return' poniżej)
            return 

        for order in orders:
            order_id = order["id"]

            # SPRAWDZENIE: Czy już to widzieliśmy? (Zamiast > porównujemy obecność w zbiorze)
            if order_id in processed_order_ids:
                continue # Już było, pomijamy
            
            # Jeśli nie było, dodajemy do bazy "widzianych"
            processed_order_ids.add(order_id)
            
            # Czyścimy pamięć, żeby nie urosła w nieskończoność (trzymamy ostatnie 100)
            if len(processed_order_ids) > 100:
                processed_order_ids.pop()

            # Sprawdzamy czy zamówienie jest świeże czasowo
            if not czy_swieze_zamowienie(order["updatedAt"]):
                continue 
            
            # --- TWORZENIE POWIADOMIENIA ---
            kupujacy = order["buyer"]["login"]
            kwota = order["summary"]["totalToPay"]["amount"]
            waluta = order["summary"]["totalToPay"]["currency"]
            
            produkty_tekst = ""
            for item in order["lineItems"]:
                nazwa_oferty = item['offer']['name']
                if len(nazwa_oferty) > 45: nazwa_oferty = nazwa_oferty[:45] + "..."
                produkty_tekst += f"• {item['quantity']}x **{nazwa_oferty}**\n"
            
            channel = bot.get_channel(KANAL_ZAMOWIENIA_ID)
            if channel:
                embed = discord.Embed(title="💰 NOWE ZAMÓWIENIE!", color=0xf1c40f)
                embed.add_field(name="Kupujący", value=kupujacy, inline=True)
                embed.add_field(name="Kwota", value=f"**{kwota} {waluta}**", inline=True)
                embed.add_field(name="📦 Produkty", value=produkty_tekst, inline=False)
                embed.set_footer(text=f"ID: {order_id} | {polski_czas()}")
                
                await channel.send(content="@here Wpadła kasa! 💸", embed=embed)
                print(f"✅ Wysłano powiadomienie o zamówieniu {order_id}")

    except Exception as e:
        print(f"Błąd w pętli Allegro: {e}")

# --- AI HELPERS ---
async def generuj_opis_gpsr(produkt):
    # Nowy, profesjonalny prompt wzorowany na przykładzie nagrzewnicy
    prompt = (
        f"Jesteś specjalistą ds. bezpieczeństwa produktów (Compliance Officer). "
        f"Napisz profesjonalną instrukcję bezpieczeństwa GPSR dla produktu: {produkt}. "
        f"Tekst ma być surowy, bez pogrubień markdown (**), gotowy do wklejenia w dokument.\n\n"
        f"Zachowaj DOKŁADNIE ten schemat sekcji:\n"
        f"1. Informacje dotyczące bezpieczeństwa produktu – {produkt} (Opis przeznaczenia, informacja że to nie zabawka)\n"
        f"2. Bezpieczeństwo użytkowania (Zasady ogólne, zapoznanie z instrukcją)\n"
        f"3. Ryzyka specyficzne (Dopasuj do produktu: np. Ryzyko poparzeń, Zasilanie, Stabilność, Ryzyko zadławienia - zależnie co to jest)\n"
        f"4. Użytkowanie i konserwacja\n"
        f"5. Przechowywanie\n"
        f"6. Informacje dodatkowe (Opakowanie, utylizacja, kontakt w razie awarii)\n\n"
        f"Styl: Formalny, nakazowy, krótki i konkretny. Używaj myślników jako punktorów."
    )
    
    try:
        if not CLAUDE_KEY: return "❌ Brak klucza Claude."
        # Zmienilem model na haiku (szybszy) lub sonnet (dokladniejszy) - zostawiam haiku dla szybkosci
        msg = await claude_client.messages.create(
            model="claude-3-haiku-20240307", 
            max_tokens=3000, 
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text
    except Exception as e: return f"Błąd: {e}"

# --- EVENTY ---
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
    embed.add_field(name="🔑 Allegro", value="`!allegro_login`\n`!ostatnie`\n`!status`", inline=False)
    embed.add_field(name="🤖 Auto-Responder", value="`!auto_start`\n`!tryb_live`\n`!tryb_test`", inline=False)
    embed.add_field(name="🧠 Narzędzia", value="`!marza [zakup]`\n`!marza [zakup] [sprzedaz] [prowizja]`\n`!trend`\n`!gpsr`", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def status(ctx):
    token_status = "✅ POŁĄCZONY" if allegro_token else "❌ ROZŁĄCZONY"
    ilosc_w_pamieci = len(processed_order_ids)
    await ctx.send(f"🤖 **Status Bota:**\nAllegro Token: {token_status}\nZamówień w pamięci podręcznej: {ilosc_w_pamieci}")

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
    await ctx.send("🔥 **UWAGA! Tryb LIVE włączony.** Bot będzie odpisywał klientom!")

@bot.command()
async def tryb_test(ctx):
    await ctx.message.delete()
    global tryb_testowy
    tryb_testowy = True
    await ctx.send("🛡️ Tryb TESTOWY włączony. Tylko powiadomienia na Discord.")

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
async def ostatnie(ctx):
    await ctx.message.delete()
    
    # Sprawdzenie czy jesteśmy zalogowani
    if not allegro_token:
        return await ctx.send("❌ Najpierw zaloguj się: `!allegro_login`")

    status_msg = await ctx.send("⏳ Pobieram listę ostatnich zamówień...")

    try:
        data = await fetch_orders()
        
        if not data or "checkoutForms" not in data:
            await status_msg.edit(content="❌ Błąd pobierania danych z Allegro.")
            return

        orders = data["checkoutForms"]
        
        if not orders:
            await status_msg.edit(content="📭 Brak zamówień na liście.")
            return

        # Sortujemy od najnowszych
        orders.sort(key=lambda x: x["updatedAt"], reverse=True)

        embed = discord.Embed(title="📦 Ostatnie 5 zamówień", color=0x3498db)

        for i, order in enumerate(orders[:5]):
            kupujacy = order["buyer"]["login"]
            kwota = order["summary"]["totalToPay"]["amount"]
            waluta = order["summary"]["totalToPay"]["currency"]
            status = order["status"]
            
            produkty_lista = ""
            for item in order["lineItems"]:
                produkty_lista += f"• {item['quantity']}x {item['offer']['name']}\n"
            
            if len(produkty_lista) > 1000: produkty_lista = produkty_lista[:1000] + "..."

            embed.add_field(
                name=f"{i+1}. {kupujacy} ({kwota} {waluta}) [{status}]",
                value=produkty_lista,
                inline=False
            )

        embed.set_footer(text=f"Wygenerowano: {polski_czas()}")
        await status_msg.edit(content=None, embed=embed)

    except Exception as e:
        await status_msg.edit(content=f"❌ Wystąpił błąd: {e}")

@bot.command()
async def marza(ctx, *args):
    """
    !marza 100            -> Sugerowane ceny
    !marza 100 150 12     -> Oblicz zysk (zakup, sprzedaż, prowizja)
    """
    await ctx.message.delete()
    
    if len(args) == 0:
        return await ctx.send("❌ Użyj: `!marza [zakup] [sprzedaz] [prowizja%]`")

    try:
        # Parsowanie liczb
        zakup_brutto = parsuj_liczbe(args[0])
        zakup_netto = zakup_brutto / 1.23

        # --- OPCJA 1: TYLKO ZAKUP (SUGESTIE) ---
        if len(args) == 1:
            cele_zysku = [10, 20, 30, 50, 100]
            embed = discord.Embed(title=f"🛒 Zakup: {zakup_brutto:.2f} zł brutto", color=0x3498db)
            embed.description = "**Sugerowane ceny sprzedaży** (bez prowizji Allegro!):"
            
            tekst_sugestii = ""
            for cel in cele_zysku:
                # Wzór: (Zysk + Zakup_netto) / (1 - podatek_dochodowy) * VAT
                # Tutaj uproszczone pod ryczałt 3% od przychodu
                sprzedaz_netto_wymagana = (cel + zakup_netto) / 0.97
                sprzedaz_brutto_wymagana = sprzedaz_netto_wymagana * 1.23
                tekst_sugestii += f"Zysk **{cel} zł** → Sprzedaj za: **{sprzedaz_brutto_wymagana:.2f} zł**\n"
            
            embed.add_field(name="Kalkulacja (VAT 23% + Ryczałt 3%)", value=tekst_sugestii, inline=False)
            await ctx.send(embed=embed)

        # --- OPCJA 2: OBLICZ ZYSK ---
        elif len(args) >= 2:
            sprzedaz_brutto = parsuj_liczbe(args[1])
            prowizja_procent = parsuj_liczbe(args[2]) if len(args) > 2 else 0.0
            
            sprzedaz_netto = sprzedaz_brutto / 1.23
            
            # Koszty
            prowizja_kwota = sprzedaz_brutto * (prowizja_procent / 100)
            ryczalt_kwota = sprzedaz_netto * 0.03  # ZMIENNA POPRAWIONA
            
            # Zysk
            zysk = sprzedaz_netto - zakup_netto - ryczalt_kwota - prowizja_kwota
            
            kolor = 0x2ecc71 if zysk > 0 else 0xe74c3c
            emoji = "✅" if zysk > 0 else "⚠️"

            embed = discord.Embed(title=f"{emoji} Wynik Transakcji", color=kolor)
            embed.add_field(name="1. Ceny", value=f"Zakup: **{zakup_brutto:.2f} zł**\nSprzedaż: **{sprzedaz_brutto:.2f} zł**", inline=False)
            
            koszty_txt = (
                f"• Towar netto: {zakup_netto:.2f} zł\n"
                f"• Prowizja Allegro ({prowizja_procent}%): **-{prowizja_kwota:.2f} zł**\n"
                f"• Ryczałt (3%): -{ryczalt_kwota:.2f} zł\n"
                f"• VAT (23%): wliczony w netto"
            )
            embed.add_field(name="2. Koszty i Podatki", value=koszty_txt, inline=False)
            embed.add_field(name="3. ZYSK NA RĘKĘ", value=f"💰 **{zysk:.2f} zł**", inline=False)
            
            if prowizja_procent == 0:
                embed.set_footer(text="⚠️ Uwaga: Obliczono bez prowizji Allegro! Dodaj trzecią liczbę.")
            else:
                embed.set_footer(text=f"Uwzględniono prowizję: {prowizja_procent}%")

            await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"❌ Błąd obliczeń: {str(e)}")

@bot.command()
async def trend(ctx, *, okres: str = None):
    # KROK 1: Walidacja
    if not okres:
        await ctx.message.delete()
        return await ctx.send("❌ Podaj miesiąc, np. `!trend Luty`")

    # KROK 2: Pytanie o kategorię
    pytanie = await ctx.send(
        f"📅 **Analiza na okres: {okres}**\n"
        f"Podaj konkretną kategorię (np. *Zabawki*, *Dom*) lub wpisz **nie** dla ogólnych hitów."
    )

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    try:
        wiadomosc = await bot.wait_for('message', check=check, timeout=60.0)
        kategoria_input = wiadomosc.content
    except asyncio.TimeoutError:
        await pytanie.delete()
        return await ctx.send("⏰ Czas minął.")

    # KROK 3: Ustalanie tematu
    if kategoria_input.lower().replace("!", "").strip() in ['nie', 'no', 'brak', 'wszystko']:
        kategoria_final = "Ogólne bestsellery (Wszystkie kategorie)"
        temat_prompt = "Wszystkie kategorie FIZYCZNYCH produktów"
    else:
        kategoria_final = kategoria_input
        temat_prompt = f"Kategoria produktu fizycznego: {kategoria_input}"

    status_msg = await ctx.send(f"⏳ **Szukam produktów na {okres}...**\nKategoria: *{kategoria_final}*")

    # KROK 4: PROMPT DO AI (Poprawiony, żeby nie zwracał softwaru)
    teraz = datetime.datetime.now().strftime("%d.%m.%Y")
    
    prompt = (
        f"Jesteś ekspertem e-commerce w Polsce. Data: {teraz}. Okres: {okres}. "
        f"Temat: {temat_prompt}. "
        f"Twoim zadaniem jest znalezienie 5 FIZYCZNYCH PRODUKTÓW do dropshippingu/sprzedaży (physical goods ONLY). "
        f"BARDZO WAŻNE: Ignoruj oprogramowanie, usługi SaaS, bramki płatności i aplikacje. Interesują mnie tylko przedmioty, które można zapakować w paczkę. "
        f"Format odpowiedzi (Markdown): "
        f"1. **Nazwa Produktu**\n2. **Dlaczego teraz?**\n3. **Cena sprzedaży (PLN)**\n4. **Potencjał**\n"
        f"Podaj same konkrety."
    )

    try:
        if not PERPLEXITY_KEY:
            await status_msg.edit(content="❌ Brak klucza API Perplexity.")
            return

        response = await perplexity_client.chat.completions.create(
            model="sonar-pro",
            messages=[{"role": "user", "content": prompt}]
        )
        
        raport = clean_text(response.choices[0].message.content)
        if len(raport) > 4000: raport = raport[:4000] + "..."

        embed = discord.Embed(title=f"📈 Raport Trendów: {okres}", description=raport, color=0x9b59b6)
        embed.set_footer(text=f"Kategoria: {kategoria_final}")
        
        await status_msg.edit(content=None, embed=embed)

    except Exception as e:
        await status_msg.edit(content=f"❌ Błąd API: {str(e)}")

@bot.command()
async def gpsr(ctx, *, produkt: str = None):
    if not produkt: 
        return await ctx.send("❌ Podaj nazwę produktu, np. `!gpsr Fotelik samochodowy`")
    
    msg = await ctx.send(f"✍️ **Generuję profesjonalny GPSR dla:** `{produkt}`...\nTo może chwilę potrwać.")
    
    opis = await generuj_opis_gpsr(produkt)
    
    # Usuwamy ewentualne podwójne entery lub śmieci na początku
    opis = opis.strip()

    # Tworzymy Embed
    embed = discord.Embed(
        title="📄 Dokumentacja GPSR", 
        color=0x2ecc71  # Twój zielony kolor
    )
    
    # WRZUCAMY TEKST W BLOK KODU (```) - To daje przycisk "Copy" i czysty wygląd
    # Używamy ```yaml dla ładnego, czytelnego fontu, lub ```text dla zwykłego
    tekst_do_kopiowania = f"```yaml\n{opis}\n```"
    
    embed.description = tekst_do_kopiowania
    embed.set_footer(text="Skopiuj treść przyciskiem lub zaznaczając tekst.")

    await msg.edit(content=None, embed=embed)

# --- START BOTA ---
keep_alive()  # <--- TO JEST KLUCZOWE DLA RENDER.COM
bot.run(TOKEN)

