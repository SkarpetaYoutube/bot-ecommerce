import discord
from discord.ext import commands
import asyncio
import datetime
import os
from anthropic import AsyncAnthropic 
from openai import AsyncOpenAI 
from keep_alive import keep_alive 

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

# --- FUNKCJE POMOCNICZE ---
def clean_text(text):
    """Czyści tekst z tagów HTML i formatuje go pod Discorda."""
    if not text: return ""
    # Zamiana tagów HTML na znaki nowej linii lub pogrubienie
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = text.replace("<b>", "**").replace("</b>", "**")
    return text.strip()

# --- FUNKCJE AI (LOGIKA) ---

async def pobierz_analize_live(okres, kategoria):
    teraz = datetime.datetime.now().strftime("%d.%m.%Y")
    
    if kategoria.lower() in ["wszystko", "all", "ogólne", "top", "hity"]:
        temat_researchu = "OGÓLNE BESTSELLERY RYNKOWE"
        skupienie = "Przeszukaj cały polski rynek e-commerce."
    else:
        temat_researchu = f"Kategoria: {kategoria}"
        skupienie = f"Skup się wyłącznie na niszy: {kategoria}."

    prompt = f"""
    Jesteś Ekspertem E-commerce. Dziś jest {teraz}. 
    Analizowany okres: {okres}.
    KATEGORIA: {temat_researchu}.
    {skupienie}

    ZASADY FORMATOWANIA (BARDZO WAŻNE):
    1. Używaj WYŁĄCZNIE Markdown Discorda.
    2. NIGDY nie używaj tagów HTML takich jak <br>, <b>, <table>.
    3. Zamiast tabel, używaj list punktowanych.
    
    STRUKTURA RAPORTU:
    Dla każdego z 5-6 produktów napisz:
    **[NAZWA PRODUKTU]**
    • 💰 Cena: [Zakres]
    • 📅 Okres sprzedaży: [Daty]
    • 🚀 Potencjał: [Krótki opis dlaczego warto]

    Na końcu dodaj sekcję: ⚠️ CZEGO UNIKAĆ.
    """

    try:
        response = await perplexity_client.chat.completions.create(
            model="sonar-pro", 
            messages=[
                {"role": "system", "content": "Jesteś analitykiem e-commerce. Pisz konkretnie, unikaj HTML, używaj list punktowanych."},
                {"role": "user", "content": prompt},
            ]
        )
        return clean_text(response.choices[0].message.content)
    except Exception as e:
        return f"Błąd Perplexity: {str(e)}"

async def generuj_opis_gpsr(produkt):
    prompt = f"Stwórz tekst GPSR dla: {produkt}. Styl urzędowy, sekcje: Bezpieczeństwo, Dzieci, Utylizacja. Czysty tekst bez HTML."
    try:
        message = await claude_client.messages.create(
            model="claude-3-5-sonnet-20240620", 
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
    await bot.change_presence(activity=discord.Game(name="!pomoc | Analiza Rynku"))

@bot.command()
async def pomoc(ctx):
    embed = discord.Embed(title="🛠️ Centrum Dowodzenia", description="Witaj! Wybierz narzędzie:", color=0xff9900)
    embed.add_field(name="🔥 Hity", value="`!hity [miesiąc]` - Główne okazje", inline=False)
    embed.add_field(name="📈 Trendy", value="`!trend` - Raport kategorii", inline=False)
    embed.add_field(name="📄 GPSR", value="`!gpsr [nazwa]` - Tekst prawny", inline=False)
    embed.add_field(name="💰 Marża", value="`!marza [zakup] [sprzedaż]`", inline=False)
    embed.set_footer(text="Analizy oparte o Perplexity Pro & Claude 3.5")
    await ctx.send(embed=embed)

@bot.command()
async def hity(ctx, *, okres: str = None):
    if not okres:
        await ctx.send("📅 Podaj miesiąc! Np. `!hity Marzec`")
        return
    msg = await ctx.send(f"⏳ **Analizuję rynek pod kątem okazji na {okres}...**")
    raport = await pobierz_analize_live(okres, "Wszystko")
    
    embed = discord.Embed(title=f"🏆 Złote Strzały: {okres}", description=raport, color=0xe74c3c)
    await msg.edit(content=None, embed=embed)

@bot.command()
async def trend(ctx, *, okres: str = None):
    if not okres:
        await ctx.send("📅 Podaj miesiąc/okres.")
        return
    
    await ctx.send("📂 Podaj kategorię (np. *Dom i Ogród* lub *Elektronika*):")
    def check(m): return m.author == ctx.author and m.channel == ctx.channel
    try:
        kategoria_msg = await bot.wait_for('message', check=check, timeout=30)
        kategoria = kategoria_msg.content
    except: kategoria = "Wszystko"

    status_msg = await ctx.send(f"🔍 **Głęboki research dla: {kategoria}...**")
    raport = await pobierz_analize_live(okres, kategoria)
    
    embed = discord.Embed(title=f"📈 Raport: {kategoria} ({okres})", description=raport, color=0x2ecc71)
    await status_msg.edit(content=None, embed=embed)

@bot.command()
async def gpsr(ctx, *, produkt: str = None):
    if not produkt:
        await ctx.send("❌ Podaj nazwę produktu.")
        return
    msg = await ctx.send("⚖️ Generuję dokumentację GPSR...")
    tresc = await generuj_opis_gpsr(produkt)
    embed = discord.Embed(title=f"📄 GPSR: {produkt}", color=0x3498db, description=f"```text\n{tresc}\n```")
    embed.set_footer(text="Skopiuj tekst z powyższej ramki.")
    await msg.edit(content=None, embed=embed)

@bot.command()
async def marza(ctx, arg1: str = None, arg2: str = None):
    if not arg1:
        await ctx.send("❌ Użycie: `!marza [zakup]` lub `!marza [zakup] [sprzedaż]`")
        return
    try:
        zakup = float(arg1.replace(',', '.'))
        zakup_netto = zakup / 1.23
        
        if arg2 is None:
            embed = discord.Embed(title=f"📊 Kalkulacja dla zakupu: {zakup} zł", color=0x3498db)
            for cel in [20, 50, 100]:
                cena = ((zakup_netto + cel) / 0.97) * 1.23
                embed.add_field(name=f"Zysk +{cel}zł", value=f"Cena: **{cena:.2f} zł**", inline=True)
            await ctx.send(embed=embed)
        else:
            sprzedaz = float(arg2.replace(',', '.'))
            sprzedaz_netto = sprzedaz / 1.23
            zysk = (sprzedaz_netto * 0.97) - zakup_netto
            embed = discord.Embed(title="💵 Wynik finansowy", color=0x2ecc71 if zysk > 0 else 0xe74c3c)
            embed.add_field(name="Zysk na rękę", value=f"**{zysk:.2f} zł**", inline=False)
            embed.set_footer(text="VAT 23% | Ryczałt 3%. Nie uwzględnia prowizji Allegro.")
            await ctx.send(embed=embed)
    except:
        await ctx.send("❌ Wpisz poprawne liczby.")

# --- URUCHAMIANIE ---
if __name__ == "__main__":
    keep_alive()
    if not TOKEN:
        print("❌ BŁĄD: Brak DISCORD_TOKEN!")
    else:
        bot.run(TOKEN)
