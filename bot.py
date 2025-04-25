import os
import logging
import subprocess
import json
import sys
import asyncio
import shutil # Untuk membersihkan direktori unduhan jika diperlukan

# Impor dari Pyrogram
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode # Import ParseMode untuk formatting pesan

# Impor aiohttp untuk server health check
import aiohttp
import aiohttp.web

# --- Konfigurasi Logger ---
# Mengatur format dan level logging
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s',
                    level=logging.INFO) # Level INFO mencakup INFO, WARNING, ERROR, CRITICAL

# --- Konfigurasi Bot ---
# Membaca konfigurasi dari Environment Variables yang disetel oleh Docker atau platform hosting
# Pyrogram memerlukan API_ID dan API_HASH bahkan untuk bot token
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Memeriksa apakah konfigurasi penting sudah disetel
if not API_ID or not API_HASH or not BOT_TOKEN:
    logging.error("Error: Environment variables API_ID, API_HASH, dan BOT_TOKEN harus disetel.")
    sys.exit(1) # Keluar jika konfigurasi kritis tidak lengkap

# Mengonversi API_ID menjadi integer
try:
    API_ID = int(API_ID)
except ValueError:
    logging.error("Error: Environment variable API_ID harus berupa angka.")
    sys.exit(1)

# Nama sesi untuk Pyrogram (untuk menyimpan sesi otentikasi)
# File sesi akan disimpan di WORKDIR (/app)
SESSION_NAME = "my_pyrogram_session"

# Folder tempat menyimpan file yang diunduh di dalam container
# Default ke /app/downloads, sesuai dengan Dockerfile dan volume mount
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/app/downloads")
# Memastikan direktori unduhan ada saat startup
if not os.path.exists(DOWNLOAD_DIR):
    try:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True) # exist_ok=True mencegah error jika direktori sudah ada
        logging.info(f"Created download directory: {DOWNLOAD_DIR}")
    except Exception as e:
        logging.error(f"Gagal membuat direktori unduhan {DOWNLOAD_DIR}: {e}")
        sys.exit(1)

# --- Konfigurasi Health Check Server ---
# Port yang akan didengarkan oleh server health check
# Dibaca dari Environment Variable, default ke 8080
HEALTH_CHECK_PORT = int(os.environ.get("HEALTH_CHECK_PORT", 8080))

# --- Health Check Handler (Fungsi yang akan dipanggil saat /health diakses) ---
async def health_handler(request):
    """
    Handler HTTP untuk health check. Merespons status bot.
    """
    # Anda bisa menambahkan logika yang lebih canggih di sini
    # Misalnya, cek apakah klien Pyrogram masih terhubung ke Telegram
    # if app and app.is_connected:
    #      return aiohttp.web.Response(text="Bot connected and healthy", status=200)
    # else:
    #      logging.warning("Health check requested, but Pyrogram client is not connected.")
    #      return aiohttp.web.Response(text="Bot not connected to Telegram", status=503)

    # Untuk health check sederhana, cukup kembalikan status 200 OK
    return aiohttp.web.Response(text="Bot service is healthy", status=200)

# --- Fungsi pembantu untuk membuat aiohttp app ---
async def create_health_app():
    """
    Membuat instance aiohttp web application.
    """
    app = aiohttp.web.Application()
    # Menambahkan route untuk /health
    app.router.add_get('/health', health_handler)
    return app

# --- Fungsi untuk Memulai Health Check Server ---
async def start_health_server():
    """
    Menyiapkan dan memulai aiohttp web server.
    """
    # Membuat dan menyiapkan runner aiohttp app
    app_runner = aiohttp.web.AppRunner(await create_health_app())
    await app_runner.setup()
    # Membuat situs TCP yang mendengarkan di semua antarmuka (0.0.0.0) pada port yang dikonfigurasi
    site = aiohttp.web.TCPSite(app_runner, host='0.0.0.0', port=HEALTH_CHECK_PORT)
    logging.info(f"Starting health check server on http://0.0.0.0:{HEALTH_CHECK_PORT}/health")
    # Memulai server. Ini adalah awaitable, tetapi server akan berjalan di background.
    await site.start()

# --- Inisialisasi Pyrogram Client ---
# Instance Pyrogram Client yang akan digunakan di seluruh bot
app = Client(
    SESSION_NAME, # Nama sesi untuk file sesi
    api_id=API_ID, # API ID (sudah dikonversi ke integer)
    api_hash=API_HASH, # API Hash (string)
    bot_token=BOT_TOKEN, # Token Bot (string)
    # workdir='/app' # Opsional: jika Anda ingin file sesi di sub-folder /app
)
logging.info("Pyrogram Client initialized.")

# --- Fungsi untuk Memanggil yt-dlp ---
# Fungsi ini menjalankan yt-dlp sebagai subprocess.
# Ini adalah blocking I/O, jadi harus dijalankan di executor saat dipanggil dari konteks async.
# --- Konfigurasi Cookies (untuk situs seperti YouTube yang butuh login/verifikasi) ---
# Path ke file cookies.txt di dalam container
# Setel Environment Variable COOKIES_FILE_PATH saat menjalankan container Docker/Koyeb
COOKIES_FILE_PATH = os.environ.get("COOKIES_FILE_PATH")
if COOKIES_FILE_PATH:
    logging.info(f"Cookies file path set: {COOKIES_FILE_PATH}")
    # Opsional: Periksa apakah file cookies benar-benar ada saat startup bot
    # Ini bisa membantu debugging, tapi mungkin file belum di-mount saat kode ini dijalankan.
    # if not os.path.exists(COOKIES_FILE_PATH):
    #    logging.warning(f"File cookies tidak ditemukan di: {COOKIES_FILE_PATH}")
    #    # Tidak setel ke None di sini, biarkan yt-dlp yang error jika file tidak ada saat dipanggil.


# --- Fungsi untuk Memanggil yt-dlp ---
def download_with_ytdlp(url):
    """
    Menjalankan yt-dlp untuk mengunduh video dari URL yang diberikan.
    Mengembalikan path file yang diunduh atau None jika gagal, beserta pesan error.
    """
    try:
        logging.info(f"Menjalankan yt-dlp untuk mengunduh: {url}")

        output_template = os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s")

        # Base command
        ytdlp_command = [
            "yt-dlp",
            "--ignore-errors",
            "--restrict-filenames",
            "--no-warnings",
            "--progress",
            "-o", output_template,
            "--external-downloader", "aria2c",
            "--external-downloader-args", "aria2c:\"-x16 -s16 -k1M\"",
            # Argumen tambahan lainnya jika diperlukan, misalnya:
            # --geo-bypass # Untuk mencoba bypass pembatasan geografis
            # --limit-rate 100K # Membatasi kecepatan (contoh: 100 KB/s)
            # --retries 5 # Coba ulang unduhan yang gagal
        ]

        # --- Tambahkan argumen cookies jika COOKIES_FILE_PATH disetel ---
        if COOKIES_FILE_PATH and os.path.exists(COOKIES_FILE_PATH): # Pastikan path disetel dan file ada
             logging.info(f"Menambahkan argumen cookies: --cookies {COOKIES_FILE_PATH}")
             ytdlp_command.extend(["--cookies", COOKIES_FILE_PATH]) # Menambahkan 2 elemen ke list
        elif COOKIES_FILE_PATH:
             # Jika COOKIES_FILE_PATH disetel tapi file tidak ada, log peringatan
             logging.warning(f"COOKIES_FILE_PATH disetel ({COOKIES_FILE_PATH}), tetapi file tidak ditemukan. Unduhan mungkin gagal untuk situs yang membutuhkan cookies.")
        # --- Akhir penambahan argumen cookies ---

        # Tambahkan URL sebagai elemen terakhir
        ytdlp_command.append(url)

        logging.info(f"Perintah dijalankan: {' '.join(ytdlp_command)}")

        # Menjalankan subprocess.run bersifat blocking
        process = subprocess.run(ytdlp_command, capture_output=True, text=True)

        # ... (sisa kode untuk memeriksa return code, parsing info -j, menemukan file, dan return) ...
        # Pastikan logic return None, error_message tetap dipertahankan di bagian bawah fungsi.

        if process.returncode != 0:
            error_message = process.stderr.strip() or f"yt-dlp failed with exit code {process.returncode}"
            logging.error(f"Gagal mengunduh {url}. Error: {error_message}")
            return None, error_message # Kembalikan None dan pesan error

        # --- Menemukan File yang Diunduh ---
        # Kode ini tetap sama, mengambil info dari yt-dlp -j untuk menemukan path file
        # ... (kode parsing info_process dan menemukan downloaded_file_path) ...
        info_command = ["yt-dlp", "-j", url]
        info_process = subprocess.run(info_command, capture_output=True, text=True)
        # ... (lanjutkan parsing info_process dan return path/error) ...

        downloaded_file_path = None
        if info_process.returncode == 0:
            try:
                info = json.loads(info_process.stdout)
                downloaded_file_path = info.get('filepath')

                if not downloaded_file_path:
                     expected_filename = f"{info.get('title', 'download')}.{info.get('ext', 'mp4')}"
                     downloaded_file_path = os.path.join(DOWNLOAD_DIR, expected_filename)
                     logging.warning(f"Properti 'filepath' tidak ditemukan di output -j. Mencoba merekonstruksi path: {downloaded_file_path}. Ini mungkin tidak akurat.")

                if downloaded_file_path and os.path.exists(downloaded_file_path):
                    logging.info(f"File ditemukan: {downloaded_file_path}")
                    return downloaded_file_path, None
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
             error_message = f"Gagal mendapatkan info yt-dlp (-j). Error: {info_process.stderr.strip()}"
             logging.error(error_message)
             return None, error_message

    except Exception as e:
        error_message = f"Terjadi kesalahan umum saat mengunduh {url}: {e}"
        logging.error(error_message)
        return None, error_message


# ... (kode fungsi bypass_cloudflare, event handlers Pyrogram, dan if __name__ == '__main__':)
# --- Fungsi untuk Menangani Cloudflare (Sangat Kompleks, Hanya Kerangka) ---
# Fungsi ini akan sangat bervariasi tergantung situs dan metode bypass yang digunakan (pyppeteer/selenium)
# Jika Anda mengimplementasikan ini, pastikan library yang relevan terinstal dan dependencies sistem ada di Dockerfile.
async def bypass_cloudflare(url):
   """
   Placeholder untuk fungsi bypass Cloudflare menggunakan browser headless.
   """
   logging.info(f"Mencoba melewati Cloudflare untuk: {url}")
   logging.warning("Fungsi bypass Cloudflare belum diimplementasikan sepenuhnya.")
   return None # Kembalikan data bypass (misalnya cookie, final URL) jika berhasil

# --- Event Handler untuk Pesan Masuk (Pyrogram) ---

# Handler untuk perintah /start
@app.on_message(filters.command("start") & filters.private) # Hanya merespons /start di chat pribadi
async def handle_start_command(client: Client, message: Message):
    """
    Menangani perintah /start. Mengirim pesan sambutan.
    """
    logging.info(f"Received /start command from chat ID: {message.chat.id}")

    welcome_message = """
Halo! 👋 Saya adalah bot pengunduh video.

Saya bisa mengunduh video dari berbagai platform menggunakan yt-dlp.

**Cara Menggunakan:**
Kirimkan perintah `/download` diikuti dengan link video yang ingin Anda unduh.

Contoh:
`/download https://www.youtube.com/watch?v=example`

Saya akan berusaha mengunduh video tersebut dan mengirimkannya kepada Anda.

*Pastikan Anda menggunakan perintah ini di chat pribadi dengan bot.*
    """
    # Mengirim pesan balasan ke pengguna menggunakan Markdown
    # Menggunakan parse_mode=ParseMode.MARKDOWN
    await message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)


# Handler untuk perintah /download
@app.on_message(filters.command("download") & filters.private) # Hanya merespons /download di chat pribadi
async def handle_download_command(client: Client, message: Message):
    """
    Menangani perintah /download. Memproses link, memulai unduhan, dan mengirim file.
    """
    chat_id = message.chat.id
    logging.info(f"Received /download command from chat ID: {chat_id}")

    # Memeriksa apakah URL disediakan setelah perintah
    if len(message.command) < 2:
        # Menggunakan parse_mode=ParseMode.MARKDOWN
        await message.reply_text("Mohon berikan URL setelah perintah /download. Contoh: `/download <link_video>`", parse_mode=ParseMode.MARKDOWN)
        logging.warning(f"Received /download command without URL from chat ID: {chat_id}")
        return

    # Mengambil URL dari argumen perintah
    url = message.command[1].strip()

    logging.info(f"Processing download request for URL: {url}")

    # Memberi tahu pengguna bahwa proses unduhan dimulai
    status_message = await message.reply_text(f"Memulai unduhan untuk: `{url}`", parse_mode=ParseMode.MARKDOWN)

    # --- Alur Logika Unduhan ---
    # Menjalankan fungsi download_with_ytdlp (yang blocking) di thread pool executor
    # agar tidak memblokir loop asyncio Pyrogram/aiohttp.
    loop = asyncio.get_event_loop()
    downloaded_file_path, error_message = await loop.run_in_executor(
        None, # Menggunakan default ThreadPoolExecutor
        download_with_ytdlp, # Fungsi yang akan dijalankan
        url # Argumen untuk fungsi download_with_ytdlp
    )

    # Menghapus pesan status "Memulai unduhan"
    try:
        await status_message.delete()
    except Exception as e:
        logging.warning(f"Gagal menghapus pesan status {status_message.id}: {e}")


    # --- Mengirim File Setelah Unduhan Selesai ---
    if downloaded_file_path:
        logging.info(f"Unduhan lokal selesai: {downloaded_file_path}. Mengirim file ke {chat_id}.")
        try:
            await message.reply_text("Unduhan selesai. Mengunggah file ke Telegram...")

            # Mengunggah file menggunakan Pyrogram
            # send_document lebih cocok untuk file media
            await client.send_document(
                chat_id=chat_id, # ID chat tujuan
                document=downloaded_file_path, # Path ke file lokal
                caption=f"✅ Unduhan selesai:\n`{url}`", # Contoh caption dengan Markdown
                parse_mode=ParseMode.MARKDOWN # Menggunakan ParseMode.MARKDOWN
                # Tambahkan thumbnail, progress callback, dll. jika diperlukan
            )
            logging.info(f"File {downloaded_file_path} berhasil dikirim ke {chat_id}")

            # --- Cleanup ---
            # Opsional: Hapus file lokal setelah dikirim
            try:
                logging.info(f"Menghapus file lokal: {downloaded_file_path}")
                os.remove(downloaded_file_path)
                logging.info(f"File {downloaded_file_path} dihapus.")
            except Exception as e:
                 logging.error(f"Gagal menghapus file lokal {downloaded_file_path}: {e}")

        except Exception as e:
            logging.error(f"Gagal mengirim file {downloaded_file_path} ke {chat_id}: {e}")
            # Menggunakan parse_mode=ParseMode.MARKDOWN
            await message.reply_text(f"❌ Gagal mengirim file `{os.path.basename(downloaded_file_path)}`:\n`{e}`", parse_mode=ParseMode.MARKDOWN)
            # Penting: Jika pengiriman gagal, file lokal mungkin masih ada. Anda mungkin ingin menghapusnya di sini juga.
            try:
                 logging.info(f"Mencoba menghapus file lokal setelah gagal kirim: {downloaded_file_path}")
                 os.remove(downloaded_file_path)
                 logging.info(f"File {downloaded_file_path} dihapus setelah gagal kirim.")
            except Exception as del_e:
                 logging.error(f"Gagal menghapus file lokal {downloaded_file_path} setelah gagal kirim: {del_e}")


    else:
        # Jika unduhan gagal (error_message sudah diisi)
        logging.error(f"Unduhan gagal untuk {url}. Error: {error_message}")
        # Menggunakan parse_mode=ParseMode.MARKDOWN
        await message.reply_text(f"❌ Unduhan gagal untuk `{url}`.\nError: `{error_message}`", parse_mode=ParseMode.MARKDOWN)

    # Opsional: Membersihkan direktori unduhan secara berkala atau setelah setiap unduhan
    # Perlu logika tambahan jika ingin membersihkan direktori (hati-hati agar tidak menghapus file yang masih diunduh)
    # Contoh: Hapus semua file di DOWNLOAD_DIR (TIDAK DISARANKAN jika multiple downloads concurrently)
    # try:
    #      logging.info(f"Membersihkan direktori unduhan: {DOWNLOAD_DIR}")
    #      shutil.rmtree(DOWNLOAD_DIR) # Menghapus direktori dan isinya
    #      os.makedirs(DOWNLOAD_DIR, exist_ok=True) # Membuat ulang direktori kosong
    #      logging.info("Direktori unduhan dibersihkan.")
    # except Exception as e:
    #      logging.error(f"Gagal membersihkan direktori unduhan {DOWNLOAD_DIR}: {e}")


# --- Menjalankan Bot dan Health Check Server ---
if __name__ == '__main__':
    logging.info("Memulai aplikasi bot dan health check server...")
    loop = asyncio.get_event_loop() # Mendapatkan event loop asyncio yang sedang berjalan

    try:
        # 1. Mulai Health Check Server sebagai task asyncio.
        # create_task() memungkinkan server berjalan di background tanpa memblokir.
        health_server_task = loop.create_task(start_health_server())
        logging.info("Health check server task created.")

        # 2. Jalankan Pyrogram Client.
        # app.run() adalah metode blocking di Pyrogram yang akan:
        # - Menghubungkan ke Telegram.
        # - Menjalankan event loop asyncio secara penuh.
        # - Mendengarkan update (pesan, dll.).
        # Task asyncio lain (seperti health_server_task) akan berjalan di loop yang sama.
        logging.info("Running Pyrogram client...")
        app.run() # Ini adalah titik utama eksekusi yang memblokir

    except Exception as e:
        # Menangkap exception yang mungkin terjadi saat startup atau selama app.run()
        logging.error(f"Error fatal saat menjalankan bot: {e}")
        # Kode di dalam blok 'finally' akan dieksekusi sebelum proses keluar
        sys.exit(1) # Keluar dari proses dengan kode error

    finally:
        # Bagian ini akan dijalankan jika app.run() berhenti (misalnya, karena sinyal shutdown)
        logging.info("Bot sedang melakukan proses shutdown...")

        # Hentikan Pyrogram client secara elegan
        try:
            logging.info("Menghentikan Pyrogram client...")
            # app.run() biasanya menghandle stop saat receive sinyal shutdown (SIGINT, SIGTERM)
            # Tapi memanggil stop() secara eksplisit di sini juga bisa.
            # asyncio.run(app.stop()) # Perlu menjalankan stop di loop jika tidak otomatis
            logging.info("Pyrogram client dihentikan.")
        except Exception as e:
            logging.error(f"Gagal menghentikan Pyrogram client: {e}")


        # Batalkan task asyncio yang tersisa (misalnya, health server)
        logging.info("Membatalkan task asyncio yang tersisa...")
        tasks = asyncio.all_tasks(loop=loop) # Dapatkan semua task di loop
        # Hapus task yang sudah selesai atau task utama jika perlu
        tasks = [t for t in tasks if not t.done()]

        if tasks:
            for task in tasks:
                task.cancel() # Kirim sinyal pembatalan
            # Tunggu task selesai dibatalkan
            logging.info(f"Menunggu {len(tasks)} task selesai dibatalkan...")
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            logging.info("Semua task asyncio selesai dibatalkan.")
        else:
            logging.info("Tidak ada task asyncio yang tersisa untuk dibatalkan.")


        # Menutup loop asyncio (opsional, app.run() mungkin sudah melakukannya)
        # Hati-hati: menutup loop yang sudah tertutup akan menimbulkan error
        # if not loop.is_closed():
        #      logging.info("Menutup loop asyncio...")
        #      loop.close()
        #      logging.info("Loop asyncio ditutup.")


        logging.info("Proses shutdown bot selesai.")
