import discord
from discord.ext import commands
import asyncio
import datetime
import os
from anthropic import AsyncAnthropic 
from openai import AsyncOpenAI 
from keep_alive import keep_alive 

# --- KONFIGURACJA ---
# Zabezpieczenie: kod sam sprawdzi czy klucz nazywa się _KEY czy _TOKEN
TOKEN = os.environ.get("DISCORD_TOKEN")
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY") or os.environ.get("CLAUDE_TOKEN")
PERPLEXITY_KEY = os.environ.get("PERPLEXITY_API_KEY") or os.environ.get("PERPLEXITY_TOKEN")

if not CLAUDE_KEY or not PERPLEXITY_KEY:
    print("⚠️ OSTRZEŻENIE: Brakuje kluczy API w zmiennych środowiskowych!")

# Inicjalizacja klientów
claude_client = AsyncAnthropic(api_key=CLAUDE_KEY)
perplexity_client = AsyncOpenAI(api_key=PERPLEXITY_KEY, base_url="https://api.perplexity.ai")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# --- FUNKCJE POMOCNICZE ---
def clean_text(text):
    if not text: return ""
    text = text.replace("**", "").replace("##", "").replace("###", "")
    return text.strip()

# --- LOGIKA AI ---
async def pobierz_analize_live(okres, kategoria):
    teraz = datetime.datetime.now().strftime("%d.%m.%Y")
    
    if kategoria.lower() in ["wszystko", "all", "ogólne", "top", "hity"]:
        temat = "OGÓLNE BESTSELLERY"
        skupienie = "Cały polski rynek e-commerce."
    else:
        # Tutaj prompt dostanie informację np. "Dom i Ogród -> Meble"
        temat = f"Kategoria/Nisza: {kategoria}"
        skupienie = f"Skup się dokładnie na: {kategoria}. Znajdź konkretne produkty."

    prompt = f"""
    Jesteś Ekspertem E-commerce. Data: {teraz}. Analiza na: {okres}.
    TEMAT: {temat}. {skupienie}
    
    ZASADY: 
    1. Zero HTML. Używaj Markdown.
    2. Format ma być idealnie czytelny jak lista zadań.
    
    STRUKTURA RAPORTU:
    Dla każdego z 5 produktów (największy potencjał sprzedażowy) wypisz:
    
    **[PEŁNA NAZWA PRODUKTU]**
    • 💰 Cena: [zakres cenowy PLN]
    • 🗓️ Start wystawiania: [Konkretna data]
    • 📈 PEAK Sprzedaży: [Zakres dat]
    • 💡 Dlaczego teraz: [Krótkie uzasadnienie]
    
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
    prompt = f"""
    Napisz profesjonalny tekst GPSR (General Product Safety Regulation) dla produktu: {produkt}.
    
    BARDZO WAŻNE ZASADY FORMATOWANIA:
    1. NIE używaj żadnego Markdowna (żadnych pogrubień **, żadnych kratek #, żadnych tabel).
    2. Tekst ma być czysty, prosty i gotowy do wklejenia.
    3. Zachowaj numerację 1., 2., 3. i nazwy sekcji dokładnie jak we wzorze poniżej.

    WZÓR (Tak ma wyglądać wynik końcowy):
    GPSR – [NAZWA PRODUKTU DUŻYMI LITERAMI]

    1. Bezpieczeństwo
    Główne zagrożenia
    [Tu wymień konkretne zagrożenia dla tego produktu]
    Zasady bezpiecznego użytkowania
    [Tu konkretne zasady użytkowania]
    Materiały i zgodność
    Produkt wykonany z materiałów bezpiecznych dla użytkownika i zgodnych z normami UE.

    2. Dzieci
    Zastosowanie
    [Dla jakiego wieku jest ten produkt]
    Zasady bezpieczeństwa dla dzieci
    [Czy wymagany nadzór dorosłych, ostrzeżenia o małych elementach itp.]

    3. Utylizacja
    Postępowanie z zużytym produktem
    [Jak wyrzucić/segregować ten konkretny produkt]
    Rekomendacje dla konsumenta
    W razie wątpliwości sprawdzić lokalne zasady segregacji odpadów.
    """
    
    try:
        # Zostawiamy Haiku 4.5 zgodnie z Twoim wyborem
        msg = await claude_client.messages.create(
            model="claude-haiku-4-5-20251001", 
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text
    except Exception as e: return f"Błąd: {e}"

# --- KOMENDY ---
@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user}")
    await bot.change_presence(activity=discord.Game(name="!pomoc | E-commerce"))

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    raise error

@bot.command()
async def pomoc(ctx):
    embed = discord.Embed(title="🛠️ Menu", color=0xff9900)
    embed.add_field(name="🔥 !hity", value="Najlepsze okazje (Ogólne)", inline=False)
    embed.add_field(name="📈 !trend", value="Analiza kategorii (Można doprecyzować)", inline=False)
    embed.add_field(name="💰 !marza", value="Kalkulator cen", inline=False)
    embed.add_field(name="📄 !gpsr [produkt]", value="Tekst prawny (czysty tekst)", inline=False)
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
    
    # LIMIT ZMNIEJSZONY DO 3000 (Bezpiecznik Discorda)
    if len(raport) > 3000: raport = raport[:3000] + "\n\n(...) [Ucięto limit]"
    
    embed = discord.Embed(title=f"🏆 Hity: {okres}", description=raport, color=0xe74c3c)
    await msg.edit(content=None, embed=embed)

@bot.command()
async def trend(ctx, *, okres: str = None):
    def check(m): return m.author == ctx.author and m.channel == ctx.channel
    
    # 1. Pytanie o OKRES
    if not okres:
        await ctx.send("📅 Jaki okres analizujemy? (np. *Luty*):")
        try:
            okres_msg = await bot.wait_for('message', check=check, timeout=30)
            okres = okres_msg.content
        except asyncio.TimeoutError:
            return await ctx.send("⏰ Czas minął.")

    # 2. Pytanie o GŁÓWNĄ KATEGORIĘ
    await ctx.send(f"📂 Ok, okres: **{okres}**. Podaj główną kategorię (np. *Dom i Ogród*):")
    try:
        kat_msg = await bot.wait_for('message', check=check, timeout=30)
        glowna_kategoria = kat_msg.content
    except asyncio.TimeoutError:
        return await ctx.send("⏰ Czas minął.")

    # 3. Pytanie o DOPRECYZOWANIE (Nowość)
    await ctx.send(
        f"🎯 Czy chcesz doprecyzować w **{glowna_kategoria}**? (np. wpisz *Narzędzia*, *Meble*)\n"
        "👉 Jeśli wolisz ogólny trend dla całej kategorii, wpisz **nie**."
    )
    try:
        sub_msg = await bot.wait_for('message', check=check, timeout=30)
        doprecyzowanie = sub_msg.content
    except asyncio.TimeoutError:
        return await ctx.send("⏰ Czas minął.")

    # 4. Ustalanie co wysłać do AI
    if doprecyzowanie.lower() in ['nie', 'no', '-', 'brak', 'ogólne', 'wszystko']:
        final_kategoria = glowna_kategoria
        info_msg = f"🔍 **Analizuję ogólnie: {final_kategoria} ({okres})...**"
    else:
        # Łączymy kategorię z podkategorią dla lepszego promptu
        final_kategoria = f"{glowna_kategoria} -> {doprecyzowanie}"
        info_msg = f"🔍 **Analizuję niszę: {doprecyzowanie} (w: {glowna_kategoria})...**"

    status = await ctx.send(info_msg)
    raport = await pobierz_analize_live(okres, final_kategoria)
    
    # LIMIT ZMNIEJSZONY DO 3000
    if len(raport) > 3000: raport = raport[:3000] + "\n\n(...) [Ucięto limit]"

    embed = discord.Embed(title=f"📈 Trend: {final_kategoria}", description=raport, color=0x2ecc71)
    await status.edit(content=None, embed=embed)

@bot.command()
async def gpsr(ctx, *, produkt: str = None):
    if not produkt:
        await ctx.send("❌ Podaj nazwę produktu!")
        return
    msg = await ctx.send("⚖️ Piszę GPSR (wzór tekstowy)...")
    tresc = await generuj_opis_gpsr(produkt)
    
    # LIMIT 3000
    if len(tresc) > 3000: 
        tresc = tresc[:3000] + "\n\n⚠️ [Tekst przycięty - limit Discorda]"

    embed = discord.Embed(description=f"```text\n{tresc}\n```", color=0x3498db)
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
    bot.run(TOKEN)
