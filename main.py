import discord
from discord.ext import commands
import asyncio
import datetime
import os
from anthropic import AsyncAnthropic 
from openai import AsyncOpenAI 
from keep_alive import keep_alive 

# --- KONFIGURACJA (Standardowe nazwy) ---
TOKEN = os.environ.get("DISCORD_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY")

claude_client = AsyncAnthropic(api_key=CLAUDE_API_KEY)
perplexity_client = AsyncOpenAI(api_key=PERPLEXITY_API_KEY, base_url="https://api.perplexity.ai")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# --- FUNKCJE POMOCNICZE ---
def clean_text(text):
    if not text: return ""
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = text.replace("<b>", "**").replace("</b>", "**")
    return text.strip()

# --- LOGIKA AI ---
async def pobierz_analize_live(okres, kategoria):
    teraz = datetime.datetime.now().strftime("%d.%m.%Y")
    
    if kategoria.lower() in ["wszystko", "all", "ogólne", "top", "hity"]:
        temat = "OGÓLNE BESTSELLERY"
        skupienie = "Cały polski rynek e-commerce."
    else:
        temat = f"Kategoria: {kategoria}"
        skupienie = f"Nisza: {kategoria}."

    # PROMPT
    prompt = f"""
    Jesteś Ekspertem E-commerce. Data: {teraz}. Analiza na: {okres}.
    TEMAT: {temat}. {skupienie}
    
    ZASADY: 
    1. Zero HTML. Używaj Markdown.
    2. Format ma być idealnie czytelny jak lista zadań.
    
    STRUKTURA RAPORTU (Trzymaj się jej sztywno):
    Dla każdego z 5 produktów wypisz:
    
    **[PEŁNA NAZWA PRODUKTU]**
    • 💰 Cena: [zakres cenowy PLN]
    • 🗓️ Start wystawiania: [Konkretna data, np. 01.02.2026]
    • 📈 PEAK Sprzedaży: [Zakres dat, np. 10-20.02.2026]
    • 💡 Dlaczego teraz: [Krótkie uzasadnienie jednym zdaniem]
    
    Na końcu dodaj sekcję: ⚠️ CZEGO UNIKAĆ (krótko).
    """
    try:
        response = await perplexity_client.chat.completions.create(
            model="sonar-pro", 
            messages=[{"role": "user", "content": prompt}]
        )
        return clean_text(response.choices[0].message.content)
    except Exception as e:
        return f"Błąd AI: {str(e)}"

async def generuj_opis_gpsr(produkt):
    try:
        msg = await claude_client.messages.create(
            model="claude-3-5-sonnet-20240620", max_tokens=2000,
            messages=[{"role": "user", "content": f"GPSR dla: {produkt}. Sekcje: Bezpieczeństwo, Dzieci, Utylizacja."}]
        )
        return msg.content[0].text
    except Exception as e: return f"Błąd: {e}"

# --- KOMENDY ---
@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user}")
    await bot.change_presence(activity=discord.Game(name="!pomoc | E-commerce"))

@bot.command()
async def pomoc(ctx):
    # Wygląd: Centrum Dowodzenia
    embed = discord.Embed(
        title="🛠️ Centrum Dowodzenia",
        description="Witaj! Wybierz narzędzie:",
        color=0xff7600
    )
    embed.add_field(name="🔥 Hity", value="`!hity [miesiąc]` - Główne okazje na rynku", inline=False)
    embed.add_field(name="📈 Trendy", value="`!trend` - Głęboka analiza konkretnej kategorii", inline=False)
    embed.add_field(name="📄 GPSR", value="`!gpsr [nazwa]` - Gotowy tekst prawny do aukcji", inline=False)
    embed.add_field(name="💰 Marża", value="`!marza [zakup] [sprzedaż]` - Szybki kalkulator zysku", inline=False)
    embed.set_footer(text="Analizy oparte o Perplexity Pro & Claude 3.5")
    await ctx.send(embed=embed)

@bot.command()
async def hity(ctx, *, okres: str = None):
    if not okres:
        await ctx.send("📅 Podaj miesiąc (np. *Marzec*):")
        try:
            msg = await bot.wait_for('message', check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=30)
            okres = msg.content
        except asyncio.TimeoutError:
            return await ctx.send("⏰ Czas minął.")

    msg = await ctx.send(f"⏳ **Szukam ogólnych hitów na: {okres}...**")
    raport = await pobierz_analize_live(okres, "Wszystko")
    if len(raport) > 4000: raport = raport[:4000] + "..."
    
    embed = discord.Embed(title=f"🏆 Hity: {okres}", description=raport, color=0xe74c3c)
    await msg.edit(content=None, embed=embed)

@bot.command()
async def trend(ctx, *, okres: str = None):
    def check(m): return m.author == ctx.author and m.channel == ctx.channel
    
    if not okres:
        await ctx.send("📅 O jaki miesiąc lub okres pytasz? (np. *Luty*):")
        try:
            okres_msg = await bot.wait_for('message', check=check, timeout=30)
            okres = okres_msg.content
        except asyncio.TimeoutError:
            return await ctx.send("⏰ Czas minął.")

    await ctx.send(f"📂 Jaka kategoria Cię interesuje? (np. *Dom i Ogród*, *Elektronika*):")
    try:
        kat_msg = await bot.wait_for('message', check=check, timeout=30)
        kategoria = kat_msg.content
    except asyncio.TimeoutError:
        return await ctx.send("⏰ Czas minął.")

    status = await ctx.send(f"🔍 **Analizuję: {kategoria} ({okres})...**")
    raport = await pobierz_analize_live(okres, kategoria)
    if len(raport) > 4000: raport = raport[:4000] + "..."

    embed = discord.Embed(title=f"📈 Raport: {kategoria} ({okres})", description=raport, color=0x2ecc71)
    await status.edit(content=None, embed=embed)

@bot.command()
async def gpsr(ctx, *, produkt: str = None):
    if not produkt:
        await ctx.send("❌ Podaj nazwę produktu!")
        return
    msg = await ctx.send("⚖️ Piszę GPSR...")
    tresc = await generuj_opis_gpsr(produkt)
    embed = discord.Embed(title=f"📄 GPSR: {produkt}", description=f"```text\n{tresc}\n```", color=0x3498db)
    await msg.edit(content=None, embed=embed)

@bot.command()
async def marza(ctx, arg1: str = None, arg2: str = None):
    if not arg1:
        return await ctx.send("❌ Wpisz cenę zakupu, np. `!marza 100`")
    try:
        zakup = float(arg1.replace(',', '.'))
        zakup_netto = zakup / 1.23
        
        if arg2 is None:
            embed = discord.Embed(title=f"📊 Kalkulacja (Zakup: {zakup} zł)", color=0x3498db)
            progi = [20, 30, 40, 50, 60, 70, 100] 
            for cel in progi:
                cena = ((zakup_netto + cel) / 0.97) * 1.23
                embed.add_field(name=f"+{cel} zł", value=f"**{cena:.2f} zł**", inline=True)
            embed.set_footer(text="Ceny brutto (z VAT i prowizją).")
            await ctx.send(embed=embed)
        else:
            sprzedaz = float(arg2.replace(',', '.'))
            zysk = (sprzedaz / 1.23 * 0.97) - zakup_netto
            embed = discord.Embed(title="Wynik", color=0x2ecc71 if zysk > 0 else 0xe74c3c)
            embed.add_field(name="Zysk na rękę", value=f"**{zysk:.2f} zł**")
            await ctx.send(embed=embed)
    except: await ctx.send("❌ Błąd liczb.")

if __name__ == "__main__":
    keep_alive()
    if not TOKEN:
        print("❌ Brak tokena!")
    else:
        bot.run(TOKEN)
