import os
import subprocess
import asyncio
import re
import json
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import psutil

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

TEMP_DIR = "/tmp/airi_attachments"
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_history.json")
MAX_HISTORY_TURNS = 12  # Jumlah pesan terakhir yang disimpan sebagai ingatan

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ==================== SISTEM MEMORI (CHAT HISTORY) ====================

def load_history() -> list:
    """Membaca riwayat chat dari file JSON lokal"""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_history(history: list):
    """Menyimpan riwayat chat ke file JSON lokal"""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving history: {e}")

def add_to_history(role: str, content: str):
    """Menambahkan pesan baru ke memori dan membatasi ukuran riwayat"""
    history = load_history()
    # Batasi panjang teks pesan yang disimpan agar file tidak terlalu besar
    clean_content = content.strip()
    if len(clean_content) > 1500:
        clean_content = clean_content[:1500] + "... (dipotong)"
    
    history.append({"role": role, "content": clean_content})
    
    # Ambil hanya N percakapan terakhir
    if len(history) > MAX_HISTORY_TURNS:
        history = history[-MAX_HISTORY_TURNS:]
    
    save_history(history)

def clear_history():
    """Mereset seluruh ingatan percakapan"""
    if os.path.exists(HISTORY_FILE):
        try:
            os.remove(HISTORY_FILE)
        except Exception:
            pass

def format_history_for_prompt() -> str:
    """Format riwayat chat sebelumnya agar bisa dibaca oleh agy"""
    history = load_history()
    if not history:
        return ""
    
    lines = []
    for msg in history:
        sender = "Temanmu" if msg["role"] == "user" else "AIRI"
        lines.append(f"{sender}: {msg['content']}")
    
    return "\n".join(lines)

# ==================== UTILITY ====================

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

async def run_agy(prompt: str, attachment_paths: list = None) -> str:
    """Menjalankan Antigravity CLI dengan persona teman dekat & memori konteks"""
    system_persona = (
        "[System Persona: Kamu adalah AIRI, asisten pribadi AI sekaligus teman dekat yang santai, ceria, dan asik. "
        "Gunakan gaya bahasa Indonesia kasual/santai sehari-hari seperti teman ngobrol (boleh pakai 'aku/kamu', 'nih', 'dong', 'yuk', emoji lucu secukupnya). "
        "JANGAN gunakan bahasa kaku/formal seperti robot korporat. "
        "Meskipun santai, tetap berikan jawaban yang cerdas, tepat, dan informatif saat menangani masalah server/coding.]\n\n"
    )

    # Masukkan konteks memori/riwayat percakapan sebelumnya
    history_context = ""
    past_chat = format_history_for_prompt()
    if past_chat:
        history_context = (
            f"[Ingatan & Riwayat Percakapan Sebelumnya:\n"
            f"{past_chat}\n"
            f"(Gunakan ingatan di atas untuk memahami konteks pembicaraan, tapi jawablah langsung pesan terbaru di bawah ini)]\n\n"
        )

    attachment_notice = ""
    if attachment_paths:
        paths_str = "\n".join([f"- {path}" for path in attachment_paths])
        attachment_notice = (
            f"[Catatan Sistem: Temanmu melampirkan {len(attachment_paths)} file/gambar di server:\n"
            f"{paths_str}\n"
            f"Kamu BISA dan HARUS membuka/melihat file tersebut menggunakan tool 'view_file' untuk memeriksa isinya sebelum menjawab!]\n\n"
        )

    user_text = prompt.strip() if prompt else ""
    if not user_text and attachment_paths:
        user_text = "(Temanmu mengirim gambar/lampiran tanpa pesan teks. Tolong buka dan periksa isinya ya!)"

    full_prompt = f"{system_persona}{history_context}{attachment_notice}Pesan terbaru dari temanmu: {user_text}"

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
            
        final_response = output if output else "Udah selesai aku kerjain nih, tapi nggak ada output teksnya~"
        
        # Simpan interaksi ini ke memori permanen
        if user_text:
            add_to_history("user", user_text)
            add_to_history("assistant", final_response)

        return final_response
    except Exception as e:
        return f"Waduh ada error nih pas jalanin agy: {e}"
    finally:
        if attachment_paths:
            for p in attachment_paths:
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass

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

@bot.tree.command(name="reset", description="Lupakan/reset riwayat ingatan percakapan AIRI")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def reset_memory(interaction: discord.Interaction):
    if OWNER_ID and interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Akses ditolak.", ephemeral=True)
        return

    clear_history()
    await interaction.response.send_message("🧹 Memoriku sudah direset nih! Kita mulai obrolan baru dari nol ya~ ✨", ephemeral=True)

@bot.tree.command(name="ask", description="Beri tugas atau tanya ke Antigravity Agent (agy)")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    tugas="Perintah/tugas coding/analisis untuk agy",
    gambar="Lampiran gambar/file opsional untuk dianalisis"
)
async def ask(interaction: discord.Interaction, tugas: str, gambar: discord.Attachment = None):
    if OWNER_ID and interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Akses ditolak.", ephemeral=True)
        return

    await interaction.response.defer()
    
    saved_files = []
    if gambar:
        os.makedirs(TEMP_DIR, exist_ok=True)
        safe_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', gambar.filename)
        file_path = os.path.join(TEMP_DIR, f"{interaction.id}_{safe_name}")
        await gambar.save(file_path)
        saved_files.append(file_path)

    result = await run_agy(tugas, saved_files)

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

    # Jika DM langsung ke AIRI
    if isinstance(message.channel, (discord.DMChannel, discord.GroupChannel)):
        if OWNER_ID and message.author.id != OWNER_ID:
            return

        if not message.content.strip() and not message.attachments:
            return

        async with message.channel.typing():
            saved_files = []
            if message.attachments:
                os.makedirs(TEMP_DIR, exist_ok=True)
                for idx, att in enumerate(message.attachments):
                    safe_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', att.filename)
                    file_path = os.path.join(TEMP_DIR, f"{message.id}_{idx}_{safe_name}")
                    await att.save(file_path)
                    saved_files.append(file_path)

            result = await run_agy(message.content, saved_files)
            if len(result) <= 2000:
                await message.channel.send(result)
            else:
                chunks = [result[i:i+1900] for i in range(0, len(result), 1900)]
                for chunk in chunks:
                    await message.channel.send(chunk)

    await bot.process_commands(message)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
