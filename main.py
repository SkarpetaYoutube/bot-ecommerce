import discord
from discord.ext import commands
import asyncio
import datetime
import os   # <--- WAŻNE: Do bezpiecznego pobierania kluczy
from anthropic import AsyncAnthropic 
from openai import AsyncOpenAI 
from keep_alive import keep_alive  # <--- POPRAWIONE (było 'd')

# --- KONFIGURACJA (BEZPIECZNA) ---
TOKEN = os.environ.get("DISCORD_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY")

# --- KLIENCI AI ---
claude_client = AsyncAnthropic(api_key=CLAUDE_API_KEY)
perplexity_client = AsyncOpenAI(
    api_key=PERPLEXITY_API_KEY,
    base_url="https://api.perplexity.ai"
)

# Ustawienia bota
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# --- FUNKCJE AI (LOGIKA) ---

async def pobierz_analize_live(okres, kategoria):
    teraz = datetime.datetime.now().strftime("%d.%m.%Y")
    
    if kategoria.lower() in ["wszystko", "all", "ogólne", "top", "hity"]:
        temat_researchu = "OGÓLNE BESTSELLERY RYNKOWE (Wszystkie branże)"
        skupienie = "Przeszukaj cały rynek e-commerce w Polsce. Wybierz absolutne hity sprzedażowe z różnych kategorii."
    else:
        temat_researchu = f"produkty konkretnie z kategorii '{kategoria}'"
        skupienie = "Ignoruj inne kategorie. Skup się TYLKO na tej jednej niszy."

    prompt = f"""
    Jesteś Ekspertem E-commerce i Analitykiem Allegro.
    Dziś jest: {teraz}. 
    Analizowany okres: {okres}.
    ANALIZOWANA KATEGORIA: {temat_researchu.upper()}.
    
    Twoim zadaniem jest znaleźć "Złote Strzały" - produkty o wysokim potencjale zysku.
    {skupienie}
    
    Wypisz 5-8 KONKRETNYCH produktów.
    
    FORMAT TABELI (Markdown):
    1. **[Pełna Nazwa Produktu]**
       * 💰 Cena: [Zakres PLN]
       * 📅 Start wystawiania: [Data]
       * 📈 PEAK Sprzedaży: [Data]
       * 🚀 Dlaczego teraz: [Powód]
       
    Na końcu sekcja: "⚠️ CZEGO UNIKAĆ".
    """

    try:
        response = await perplexity_client.chat.completions.create(
            model="sonar-pro", 
            messages=[
                {"role": "system", "content": "Jesteś analitykiem, który daje konkretne daty i liczby."},
                {"role": "user", "content": prompt},
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Błąd Perplexity: {str(e)}"

async def generuj_opis_gpsr(produkt):
    prompt = f"""
    Jesteś specjalistą ds. bezpieczeństwa produktów (Compliance Officer).
    Stwórz tekst "Informacje dotyczące bezpieczeństwa produktu" (GPSR) dla: "{produkt}".
    
    ZASADY:
    1. Styl urzędowy, bezosobowy.
    2. Format CZYSTY TEKST (bez pogrubień w nagłówkach).
    3. Obowiązkowe sekcje: Dzieci, Czyszczenie, Utylizacja.
    """

    try:
        message = await claude_client.messages.create(
            model="claude-haiku-4-5-20251001", 
            max_tokens=4000,
            temperature=0.3, 
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        return f"Błąd API: {str(e)}"

# --- KOMENDY BOTA ---

@bot.event
async def on_ready():
    print(f"✅ Bot online! Zalogowano jako: {bot.user}")
    await bot.change_presence(activity=discord.Game(name="Szukanie Okazji"))

@bot.command()
async def pomoc(ctx):
    embed = discord.Embed(title="🛠️ Centrum Dowodzenia", color=0xff9900)
    embed.add_field(name="🔥 Hity", value="`!hity [miesiąc]`", inline=False)
    embed.add_field(name="📈 Trendy", value="`!trend` (interaktywne)", inline=False)
    embed.add_field(name="📄 GPSR", value="`!gpsr [produkt]`", inline=False)
    embed.add_field(name="💰 Marża", value="`!marza [zakup] [sprzedaz]`", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def hity(ctx, *, okres: str = None):
    if not okres:
        await ctx.send("📅 Podaj miesiąc! Np. `!hity Marzec`")
        return
    msg = await ctx.send(f"🔥 **Szukam ogólnych bestsellerów ({okres})...**")
    raport = await pobierz_analize_live(okres, "Wszystko")
    embed = discord.Embed(title=f"🏆 Hity: {okres}", description=raport, color=0xe74c3c)
    await msg.edit(content=None, embed=embed)

@bot.command()
async def trend(ctx, *, okres: str = None):
    if not okres:
        await ctx.send("📅 Krok 1: Podaj okres (np. *Luty*).")
        def check(m): return m.author == ctx.author and m.channel == ctx.channel
        try:
            okres = (await bot.wait_for('message', check=check, timeout=30)).content
        except: return
    
    await ctx.send("📂 Krok 2: Podaj kategorię (lub *Wszystko*).")
    def check(m): return m.author == ctx.author and m.channel == ctx.channel
    try:
        kategoria = (await bot.wait_for('message', check=check, timeout=30)).content
    except: kategoria = "Wszystko"

    status_msg = await ctx.send(f"🔍 **Analizuję: {kategoria}...**")
    raport = await pobierz_analize_live(okres, kategoria)
    embed = discord.Embed(title=f"📈 Raport: {kategoria}", description=raport, color=0x2ecc71)
    await status_msg.edit(content=None, embed=embed)

@bot.command()
async def gpsr(ctx, *, produkt: str = None):
    if not produkt:
        await ctx.send("❌ Podaj produkt! Np. `!gpsr Lampa`")
        return
    msg = await ctx.send("⚖️ Piszę GPSR...")
    tresc = await generuj_opis_gpsr(produkt)
    embed = discord.Embed(title="📄 GPSR (Copy-Paste)", color=0x2ecc71, description=f"```text\n{tresc}\n```")
    await msg.edit(content=None, embed=embed)

@bot.command()
async def marza(ctx, arg1: str = None, arg2: str = None):
    if not arg1:
        await ctx.send("❌ Użycie: `!marza 100` lub `!marza 100 150`")
        return
    try:
        zakup = float(arg1.replace(',', '.'))
        zakup_netto = zakup / 1.23
        
        if arg2 is None:
            embed = discord.Embed(title=f"📋 Cennik (Zakup: {zakup} zł)", color=0x3498db)
            for cel in [20, 30, 50, 100]:
                cena = ((zakup_netto + cel) / 0.97) * 1.23
                embed.add_field(name=f"Zysk +{cel}zł", value=f"Wystaw za: **{cena:.2f}**", inline=True)
            await ctx.send(embed=embed)
        else:
            sprzedaz = float(arg2.replace(',', '.'))
            sprzedaz_netto = sprzedaz / 1.23
            podatek = sprzedaz_netto * 0.03
            zysk = (sprzedaz_netto * 0.97) - zakup_netto
            embed = discord.Embed(title="💵 Wynik", color=0x2ecc71 if zysk > 0 else 0xe74c3c)
            embed.add_field(name="Zysk (na rękę)", value=f"**{zysk:.2f} zł**")
            embed.set_footer(text="Uwzględnia: VAT 23%, Ryczałt 3%. Bez prowizji Allegro.")
            await ctx.send(embed=embed)
    except:
        await ctx.send("❌ Błąd liczb.")

# --- URUCHAMIANIE ---
if __name__ == "__main__":
    keep_alive() # Uruchamia "oszukiwacza" dla Rendera
    if not TOKEN:
        print("❌ BŁĄD: Brak kluczy w zmiennych środowiskowych!")
    else:
        bot.run(TOKEN)