import os
import subprocess
import asyncio
import re
import json
import time
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import psutil

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

TEMP_DIR = "/tmp/airi_attachments"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(BASE_DIR, "chat_history.json")
CONFIG_FILE = os.path.join(BASE_DIR, "bot_config.json")
MAX_HISTORY_TURNS = 12  # Jumlah pesan terakhir yang disimpan sebagai ingatan
MODEL_CACHE_TTL = 300   # Cache daftar model agy selama 5 menit

_model_cache = {"timestamp": 0.0, "models": []}

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

# ==================== KONFIGURASI & MODEL AGY ====================

def load_config() -> dict:
    """Membaca konfigurasi bot (seperti model aktif) dari file JSON"""
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_config(config: dict):
    """Menyimpan konfigurasi bot ke file JSON"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}")

def get_active_model() -> str | None:
    """Mengambil ID model yang sedang aktif (None berarti default agy)"""
    return load_config().get("model")

def set_active_model(model_id: str | None):
    """Menyimpan ID model aktif secara permanen"""
    config = load_config()
    if model_id:
        config["model"] = model_id
    else:
        config.pop("model", None)
    save_config(config)

async def get_available_models(force_refresh: bool = False) -> list[tuple[str, str]]:
    """Mengambil daftar model langsung dari `agy models` secara dinamis."""
    now = time.time()
    if not force_refresh and _model_cache["models"] and (now - _model_cache["timestamp"] < MODEL_CACHE_TTL):
        return _model_cache["models"]

    try:
        process = await asyncio.create_subprocess_exec(
            os.path.expanduser("~/.local/bin/agy"),
            "models",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        raw_text = stdout.decode("utf-8", errors="ignore")
        # Bersihkan ANSI escape codes & carriage return dari spinner CLI
        clean_text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw_text)

        models = []
        for segment in re.split(r"[\r\n]+", clean_text):
            line = segment.strip()
            if not line or "Fetching available models" in line:
                continue
            parts = line.split(None, 1)
            if len(parts) == 2 and re.match(r"^[a-z0-9][a-z0-9._-]+$", parts[0]):
                models.append((parts[0], parts[1].strip()))

        if models:
            _model_cache["models"] = models
            _model_cache["timestamp"] = now
        return models
    except Exception as e:
        print(f"Error fetching agy models: {e}")
        return _model_cache["models"]

def resolve_model_query(query: str, models: list[tuple[str, str]]) -> tuple[tuple[str, str] | None, list[tuple[str, str]]]:
    """Mencocokkan input user (exact ID atau potongan seperti 'hydrogen-medium') ke model agy."""
    q = query.strip().lower()
    for m_id, m_name in models:
        if m_id.lower() == q:
            return (m_id, m_name), []

    matches = [
        (m_id, m_name) for m_id, m_name in models
        if q in m_id.lower() or q in m_name.lower()
    ]
    if len(matches) == 1:
        return matches[0], []
    return None, matches

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
        cmd = [
            os.path.expanduser("~/.local/bin/agy"),
            "--prompt", full_prompt,
            "--dangerously-skip-permissions",
        ]
        active_model = get_active_model()
        if active_model:
            cmd.extend(["--model", active_model])

        process = await asyncio.create_subprocess_exec(
            *cmd,
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
    models = await get_available_models(force_refresh=True)
    print(f"🧠 Terdeteksi {len(models)} model dari agy (aktif: {get_active_model() or 'default'}).")
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

async def apply_model_selection(query: str | None, force_refresh: bool = False) -> str:
    """Memproses pergantian atau pengecekan model agy secara adaptif."""
    models = await get_available_models(force_refresh=force_refresh)
    active = get_active_model()

    if not query or query.strip().lower() == "list":
        lines = [f"🤖 **Model aktif saat ini:** `{active or 'default (bawaan agy)'}`\n"]
        if models:
            lines.append("**Daftar model tersedia dari `agy`:**")
            for m_id, m_name in models:
                marker = " ✅" if m_id == active else ""
                lines.append(f"• `{m_id}` — {m_name}{marker}")
            lines.append("\nGunakan `/model <nama>` atau ketik `/model-hydrogen-medium` di DM, atau `/model default` untuk reset.")
        else:
            lines.append("⚠️ Gagal mengambil daftar model dari `agy models`.")
        return "\n".join(lines)

    if query.strip().lower() == "default":
        set_active_model(None)
        return "🔄 Model dikembalikan ke **default bawaan `agy`**!"

    if not models:
        return "⚠️ Daftar model `agy` lagi kosong atau gagal diambil nih."

    matched, candidates = resolve_model_query(query, models)
    if matched:
        m_id, m_name = matched
        set_active_model(m_id)
        return f"✅ Siap! Model AIRI sekarang diganti ke **{m_name}** (`{m_id}`)."

    if candidates:
        opts = "\n".join([f"• `{m_id}` — {m_name}" for m_id, m_name in candidates[:10]])
        return f"🤔 Ada beberapa model yang cocok dengan `{query}`:\n{opts}\nPilih yang lebih spesifik ya!"

    return f"❌ Model `{query}` nggak ketemu di daftar `agy models`. Cek pakai `/model` dulu yuk!"

@bot.tree.command(name="model", description="Lihat atau ganti model AI yang dipakai AIRI (adaptif sesuai agy)")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(nama="Pilih model (kosongkan untuk lihat daftar, atau 'default' untuk reset)")
async def model_cmd(interaction: discord.Interaction, nama: str = None):
    if OWNER_ID and interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Akses ditolak.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    # Refresh daftar model jika user mengecek list atau memilih model baru
    reply = await apply_model_selection(nama, force_refresh=(nama is None))
    if len(reply) <= 2000:
        await interaction.followup.send(reply, ephemeral=True)
    else:
        for i in range(0, len(reply), 1900):
            await interaction.followup.send(reply[i:i+1900], ephemeral=True)

@model_cmd.autocomplete("nama")
async def model_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if OWNER_ID and interaction.user.id != OWNER_ID:
        return []

    models = await get_available_models()
    q = current.strip().lower()
    choices = []

    if not q or "default".startswith(q):
        choices.append(app_commands.Choice(name="default (Bawaan agy)", value="default"))

    for m_id, m_name in models:
        if not q or q in m_id.lower() or q in m_name.lower():
            label = f"{m_name} ({m_id})"
            choices.append(app_commands.Choice(name=label[:100], value=m_id[:100]))
        if len(choices) >= 25:
            break

    return choices

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

        # Shortcut teks DM seperti /model-hydrogen-medium atau /model
        content_stripped = message.content.strip()
        if not message.attachments and (content_stripped == "/model" or content_stripped.startswith("/model-")):
            query = None if content_stripped == "/model" else content_stripped[len("/model-"):]
            async with message.channel.typing():
                reply = await apply_model_selection(query, force_refresh=(query is None))
                for i in range(0, len(reply), 1900):
                    await message.channel.send(reply[i:i+1900])
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
