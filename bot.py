import os
import logging
import subprocess
import json
import sys
import asyncio

# Impor dari Pyrogram
from pyrogram import Client, filters
from pyrogram.types import Message # Diperlukan jika menggunakan type hints atau perlu referensi tipe

# Impor aiohttp untuk server health check
import aiohttp
import aiohttp.web

# Mungkin perlu menginstal:
# pip install pyrogram aiohttp yt-dlp aria2p pyppeteer

# --- Konfigurasi Logger ---
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s',
                    level=logging.INFO)

# --- Konfigurasi Bot ---
# Pyrogram memerlukan API_ID dan API_HASH bahkan untuk bot token
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Periksa apakah konfigurasi penting sudah disetel
if not API_ID or not API_HASH or not BOT_TOKEN:
    logging.error("Error: Environment variables API_ID, API_HASH, dan BOT_TOKEN harus disetel.")
    sys.exit(1)

# Nama sesi untuk Pyrogram (untuk menyimpan sesi auth)
# Ini akan disimpan di WORKDIR (/app) berkat volume mapping
# Pyrogram akan membuat file seperti 'my_pyrogram_session.session' atau folder.
SESSION_NAME = "my_pyrogram_session" # Nama string

# Folder tempat menyimpan file yang diunduh di dalam container
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/app/downloads")
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)
    logging.info(f"Created download directory: {DOWNLOAD_DIR}")

# --- Konfigurasi Health Check Server ---
HEALTH_CHECK_PORT = int(os.environ.get("HEALTH_CHECK_PORT", 8080))

# --- Health Check Handler ---
async def health_handler(request):
    # Anda bisa menambahkan cek status Pyrogram client di sini
    # if app and app.is_connected: # Atribut is_connected di Pyrogram
    #      return aiohttp.web.Response(text="Bot connected and healthy", status=200)
    # else:
    #      logging.warning("Health check requested, but Pyrogram client is not connected.")
    #      return aiohttp.web.Response(text="Bot not connected to Telegram", status=503)
    return aiohttp.web.Response(text="Bot service is healthy", status=200)

# --- Fungsi untuk Memulai Health Check Server ---
async def start_health_server():
    app_runner = aiohttp.web.AppRunner(await create_health_app())
    await app_runner.setup()
    site = aiohttp.web.TCPSite(app_runner, host='0.0.0.0', port=HEALTH_CHECK_PORT)
    logging.info(f"Starting health check server on http://0.0.0.0:{HEALTH_CHECK_PORT}/health")
    await site.start()

# Fungsi pembantu untuk membuat aiohttp app
async def create_health_app():
    app = aiohttp.web.Application()
    app.router.add_get('/health', health_handler)
    return app


# --- Inisialisasi Pyrogram Client ---
# Pyrogram Client perlu dibuat sebelum digunakan
# Mode bot diaktifkan dengan menyediakan bot_token
app = Client(
    SESSION_NAME, # Nama sesi
    api_id=int(API_ID), # Pastikan API_ID adalah integer
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    # Anda bisa tambahkan workdir='/app' jika ingin file sesi di sub-folder
    # workdir='/app'
)
logging.info("Pyrogram Client initialized.")

# --- Fungsi untuk Memanggil yt-dlp ---
# (Kode fungsi download_with_ytdlp tetap sama seperti sebelumnya, tidak perlu diubah)
# Catatan: Fungsi ini blocking karena subprocess.run. Nanti akan dijalankan di executor.
def download_with_ytdlp(url): # Tidak perlu chat_id di sini, pesan dikirim di handler
    try:
        logging.info(f"Memulai unduhan dengan yt-dlp untuk: {url}")
        # Pesan "Memulai unduhan" akan dikirim di handler Pyrogram

        # Opsi yt-dlp:
        output_template = os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s")

        ytdlp_command = [
            "yt-dlp",
            "--ignore-errors",
            "--restrict-filenames",
            "--no-warnings",
            "--progress",
            "-o", output_template,
            "--external-downloader", "aria2c",
            "--external-downloader-args", "aria2c:\"-x16 -s16 -k1M\"", # Contoh argumen
            url
        ]

        logging.info(f"Menjalankan perintah: {' '.join(ytdlp_command)}")

        process = subprocess.run(ytdlp_command, capture_output=True, text=True)

        logging.debug(f"yt-dlp stdout:\n{process.stdout}")
        logging.debug(f"yt-dlp stderr:\n{process.stderr}")

        if process.returncode != 0:
            error_message = f"Gagal mengunduh {url}. Error: {process.stderr}"
            logging.error(error_message)
            return None, error_message # Kembalikan None dan pesan error

        # --- Menemukan File yang Diunduh ---
        info_command = ["yt-dlp", "-j", url]
        info_process = subprocess.run(info_command, capture_output=True, text=True)

        downloaded_file_path = None
        if info_process.returncode == 0:
            try:
                info = json.loads(info_process.stdout)
                downloaded_file_path = info.get('filepath')

                if not downloaded_file_path:
                     expected_filename = f"{info.get('title', 'download')}.{info.get('ext', 'mp4')}"
                     downloaded_file_path = os.path.join(DOWNLOAD_DIR, expected_filename)
                     logging.warning(f"Properti 'filepath' tidak ditemukan di output -j. Mencoba merekonstruksi path: {downloaded_file_path}. Ini mungkin tidak akurat.")

                logging.info(f"Diperkirakan file terunduh di: {downloaded_file_path}")

                if downloaded_file_path and os.path.exists(downloaded_file_path):
                    logging.info(f"File ditemukan: {downloaded_file_path}")
                    return downloaded_file_path, None # Kembalikan path dan None (tidak ada error)
                else:
                    error_message = f"File tidak ditemukan setelah unduhan selesai: {downloaded_file_path}"
                    logging.error(error_message)
                    return None, error_message

            except json.JSONDecodeError:
                 error_message = "Gagal mem-parse output info JSON dari yt-dlp."
                 logging.error(error_message)
                 return None, error_message
            except Exception as e:
                 error_message = f"Terjadi kesalahan saat memproses info yt-dlp: {e}"
                 logging.error(error_message)
                 return None, error_message
        else:
             error_message = f"Gagal mendapatkan info yt-dlp (-j). Error: {info_process.stderr}"
             logging.error(error_message)
             return None, error_message

    except Exception as e:
        error_message = f"Terjadi kesalahan umum saat mengunduh {url}: {e}"
        logging.error(error_message)
        return None, error_message

# --- Fungsi untuk Menangani Cloudflare (Sangat Kompleks, Hanya Kerangka) ---
# (Tetap sama, independen dari library Telegram)
async def bypass_cloudflare(url):
   logging.info(f"Mencoba melewati Cloudflare untuk: {url}")
   logging.warning("Fungsi bypass Cloudflare belum diimplementasikan sepenuhnya.")
   return None

# --- Event Handler untuk Pesan Masuk (Pyrogram) ---
# Menggunakan decorator @app.on_message
@app.on_message(filters.command("download") & filters.private) # Contoh: Hanya merespons perintah /download di chat pribadi
# Anda bisa tambahkan filter lain seperti filters.group, filters.user, dll.
async def handle_download_command(client: Client, message: Message):
    # 'client' adalah instance Pyrogram Client
    # 'message' adalah objek Message yang berisi detail pesan masuk

    # message.text berisi teks lengkap pesan
    # message.command akan mengembalikan list: ['download', '<url>'] jika ada argumen
    # Jadi kita ambil elemen kedua (indeks 1)
    if len(message.command) < 2:
        await message.reply_text("Mohon berikan URL setelah perintah /download. Contoh: `/download <link_video>`")
        return

    url = message.command[1].strip() # Ambil URL dari argumen perintah

    logging.info(f"Menerima perintah unduh untuk: {url} dari chat ID: {message.chat.id}")

    if not url:
        await message.reply_text("Mohon berikan URL setelah perintah /download.")
        return

    # Beri tahu pengguna bahwa proses dimulai
    status_message = await message.reply_text(f"Memulai unduhan untuk: {url}")

    # Karena download_with_ytdlp bersifat blocking (menggunakan subprocess.run)
    # kita harus menjalankannya di executor agar tidak memblokir loop asyncio Pyrogram.
    loop = asyncio.get_event_loop()
    downloaded_file_path, error_message = await loop.run_in_executor(
        None, # Gunakan default ThreadPoolExecutor
        download_with_ytdlp, # Fungsi yang akan dijalankan
        url # Argumen untuk fungsi download_with_ytdlp
    )

    # Hapus pesan status awal jika ada
    # await status_message.delete() # Hati-hati, mungkin tidak selalu berhasil atau diinginkan

    # --- Mengirim File Setelah Unduhan Selesai ---
    if downloaded_file_path:
        logging.info(f"Unduhan lokal selesai: {downloaded_file_path}")
        try:
            await message.reply_text("Unduhan selesai. Mengunggah file ke Telegram...")

            # Mengunggah file menggunakan Pyrogram
            # send_document atau send_video lebih cocok untuk file media
            # file_path: path ke file lokal
            # chat_id: ID chat tujuan (message.chat.id)
            # caption (opsional): teks caption
            await client.send_document(
                chat_id=message.chat.id,
                document=downloaded_file_path,
                caption=f"Unduhan selesai: {url}" # Contoh caption
            )
            logging.info(f"File {downloaded_file_path} berhasil dikirim ke {message.chat.id}")

            # Opsional: Hapus file lokal setelah dikirim
            # logging.info(f"Menghapus file lokal: {downloaded_file_path}")
            # os.remove(downloaded_file_path)
            # logging.info(f"File {downloaded_file_path} dihapus.")

        except Exception as e:
            logging.error(f"Gagal mengirim file {downloaded_file_path} ke {message.chat.id}: {e}")
            await message.reply_text(f"Gagal mengirim file {os.path.basename(downloaded_file_path)}: {e}")

    else:
        # Jika unduhan gagal, error_message sudah dikembalikan dari download_with_ytdlp
        logging.error(f"Unduhan gagal untuk {url}. Error: {error_message}")
        await message.reply_text(f"Unduhan gagal untuk {url}.\nError: {error_message}")


# --- Menjalankan Bot dan Health Check Server ---
if __name__ == '__main__':
    logging.info("Memulai aplikasi bot dan health check server...")
    loop = asyncio.get_event_loop()

    try:
        # 1. Mulai Health Check Server sebagai task asyncio
        health_server_task = loop.create_task(start_health_server())
        logging.info("Health check server task created.")

        # 2. Jalankan Pyrogram Client
        # app.run() adalah metode blocking di Pyrogram
        # yang akan menjalankan loop asyncio secara penuh
        # dan menghandle koneksi serta event.
        # Task health_server_task akan berjalan di loop yang sama.
        logging.info("Running Pyrogram client...")
        app.run()

    except Exception as e:
        # Tangkap exception yang mungkin terjadi saat startup atau selama app.run()
        logging.error(f"Error fatal saat menjalankan bot: {e}")
        sys.exit(1)
    finally:
        # Kode cleanup ini akan berjalan jika bot berhenti (misalnya, karena error fatal)
        logging.info("Shutting down...")
        # Batalkan task asyncio yang tersisa (misalnya, health server)
        logging.info("Cancelling remaining asyncio tasks...")
        tasks = asyncio.all_tasks(loop=loop)
        for task in tasks:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        logging.info("Asyncio tasks cancelled.")

        # app.run() seharusnya menutup koneksi Pyrogram.
        # Jika perlu menutup secara eksplisit:
        # loop.run_until_complete(app.stop())
        # logging.info("Pyrogram client stopped.")

        logging.info("Bot shutdown complete.")
