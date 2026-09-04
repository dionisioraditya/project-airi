import os
import subprocess
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import psutil

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def get_raspi_temp():
    try:
        output = subprocess.check_output(["vcgencmd", "measure_temp"]).decode("utf-8")
        return output.replace("temp=", "").strip()
    except Exception:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp = float(f.read().strip()) / 1000.0
                return f"{temp:.1f}'C"
        except Exception:
            return "N/A"

async def run_agy(prompt: str) -> str:
    """Menjalankan Antigravity CLI dengan persona teman dekat yang asik"""
    system_persona = (
        "[System Persona: Kamu adalah AIRI, asisten pribadi AI sekaligus teman dekat yang santai, ceria, dan asik. "
        "Gunakan gaya bahasa Indonesia kasual/santai sehari-hari seperti teman ngobrol (boleh pakai 'aku/kamu', 'nih', 'dong', 'yuk', emoji lucu secukupnya). "
        "JANGAN gunakan bahasa kaku/formal seperti robot korporat. "
        "Meskipun santai, tetap berikan jawaban yang cerdas, tepat, dan informatif saat menangani masalah server/coding.]\n\n"
    )
    
    full_prompt = f"{system_persona}Pesan dari temanmu: {prompt}"

    try:
        process = await asyncio.create_subprocess_exec(
            os.path.expanduser("~/.local/bin/agy"),
            "--prompt", full_prompt,
            "--dangerously-skip-permissions",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        output = stdout.decode("utf-8").strip()
        if not output and stderr:
            output = stderr.decode("utf-8").strip()
        return output if output else "Udah selesai aku kerjain nih, tapi nggak ada output teksnya~"
    except Exception as e:
        return f"Waduh ada error nih pas jalanin agy: {e}"

@bot.event
async def on_ready():
    print(f"✨ AIRI (Powered by Antigravity CLI) sudah online sebagai: {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Berhasil sinkronisasi {len(synced)} perintah slash commands.")
    except Exception as e:
        print(f"Error sync: {e}")

# ==================== COMMANDS ====================

@bot.tree.command(name="status", description="Cek status & suhu Raspberry Pi")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def status(interaction: discord.Interaction):
    if OWNER_ID and interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Akses ditolak.", ephemeral=True)
        return

    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    temp = get_raspi_temp()

    embed = discord.Embed(title="🍓 Status Raspberry Pi (AIRI)", color=discord.Color.blue())
    embed.add_field(name="🌡️ Suhu CPU", value=temp, inline=True)
    embed.add_field(name="⚙️ CPU Usage", value=f"{cpu}%", inline=True)
    embed.add_field(name="🧠 RAM", value=f"{ram.used // (1024**2)}MB / {ram.total // (1024**2)}MB ({ram.percent}%)", inline=False)
    embed.add_field(name="💾 Disk", value=f"{disk.used // (1024**3)}GB / {disk.total // (1024**3)}GB ({disk.percent}%)", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="ask", description="Beri tugas atau tanya ke Antigravity Agent (agy)")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(tugas="Perintah/tugas coding/analisis untuk agy")
async def ask(interaction: discord.Interaction, tugas: str):
    if OWNER_ID and interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Akses ditolak.", ephemeral=True)
        return

    await interaction.response.defer()  # Beri waktu agy memproses
    result = await run_agy(tugas)

    # Kirim respons ke Discord (potong jika lebih dari 2000 char)
    if len(result) <= 2000:
        await interaction.followup.send(result)
    else:
        chunks = [result[i:i+1900] for i in range(0, len(result), 1900)]
        for chunk in chunks:
            await interaction.followup.send(chunk)

# ==================== DM HANDLER (CHAT LANGSUNG KE AGY) ====================

@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    # Jika Anda DM langsung ke AIRI
    if isinstance(message.channel, discord.DMChannel):
        if OWNER_ID and message.author.id != OWNER_ID:
            return

        async with message.channel.typing():
            result = await run_agy(message.content)
            if len(result) <= 2000:
                await message.channel.send(result)
            else:
                chunks = [result[i:i+1900] for i in range(0, len(result), 1900)]
                for chunk in chunks:
                    await message.channel.send(chunk)

    await bot.process_commands(message)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
