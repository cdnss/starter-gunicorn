import os
import logging
import subprocess
import json
import sys
import asyncio
import shutil
import re # Impor modul regex (mungkin tidak lagi utama jika menggunakan progres JSON, tapi jaga jika perlu parsing lain)

# Impor dari Pyrogram
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode # Import ParseMode untuk formatting pesan

# Impor aiohttp untuk server health check
import aiohttp
import aiohttp.web

# --- Konfigurasi Logger ---
# Mengatur format dan level logging untuk output konsol
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

# --- Konfigurasi Cookies ---
# Path ke file cookies.txt di dalam container
# Dibaca dari Environment Variable COOKIES_FILE_PATH
COOKIES_FILE_PATH = os.environ.get("COOKIES_FILE_PATH")
if COOKIES_FILE_PATH:
    logging.info(f"COOKIES_FILE_PATH disetel: {COOKIES_FILE_PATH}")
    # Cek keberadaan file saat startup (opsional, tapi membantu deteksi masalah awal)
    if not os.path.exists(COOKIES_FILE_PATH):
         logging.warning(f"PERINGATAN: File cookies tidak ditemukan di path yang disetel: {COOKIES_FILE_PATH}")
         # Biarkan COOKIES_FILE_PATH tetap disetel, yt-dlp akan error jika file tidak ada saat dipanggil.


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

# --- Fungsi untuk Memanggil yt-dlp dan Melaporkan Progres ---
# Fungsi ini sekarang adalah async function karena menggunakan subprocess async dan edit pesan async
async def download_with_ytdlp(url, status_message: Message):
    """
    Menjalankan yt-dlp sebagai subprocess non-blocking dan melaporkan progres di pesan status.
    Mengembalikan path file yang diunduh atau None jika gagal, beserta pesan error.
    """
    logging.info(f"Memulai unduhan async dengan yt-dlp untuk: {url}")
    last_update_time = 0 # Untuk membatasi frekuensi update pesan Telegram
    last_progress_text = "" # Untuk menghindari edit pesan jika progress sama

    output_template = os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s")

    # Base command untuk yt-dlp
    ytdlp_command = [
        "yt-dlp",
        "--ignore-errors",
        "--restrict-filenames",
        "--no-warnings",
        "--newline", # Penting: memastikan setiap line output diakhiri newline
        "--progress", # Aktifkan output progres
        "--progress-template", "%(progress)j", # Output progres dalam format JSON
        "-o", output_template,
        "--external-downloader", "aria2c", # Menggunakan aria2c (pastikan terinstal di Dockerfile)
        "--external-downloader-args", "aria2c:\"-x 16 -s 16 -k 1M\"", # Argumen untuk aria2c (sudah diperbaiki)
        # Argumen tambahan lainnya jika diperlukan...
    ]

    # --- Tambahkan argumen cookies jika COOKIES_FILE_PATH disetel ---
    # Periksa lagi keberadaan file cookies saat menjalankan subprocess, lebih aman
    if COOKIES_FILE_PATH and os.path.exists(COOKIES_FILE_PATH):
         logging.info(f"Menambahkan argumen cookies: --cookies {COOKIES_FILE_PATH}")
         ytdlp_command.extend(["--cookies", COOKIES_FILE_PATH])
    elif COOKIES_FILE_PATH:
         # Jika COOKIES_FILE_PATH disetel tapi file tidak ada saat proses download dimulai
         logging.warning(f"COOKIES_FILE_PATH disetel ({COOKIES_FILE_PATH}), tetapi file TIDAK ditemukan saat mencoba unduh. Unduhan mungkin gagal.")
    # --- Akhir penambahan argumen cookies ---

    # Tambahkan URL sebagai elemen terakhir
    ytdlp_command.append(url)

    logging.info(f"Perintah dijalankan: {' '.join(ytdlp_command)}")

    process = None # Inisialisasi proses di luar try untuk cleanup
    try:
        # Menjalankan subprocess yt-dlp secara async
        process = await asyncio.create_subprocess_exec(
            *ytdlp_command, # Gunakan * untuk meneruskan list sebagai argumen terpisah
            stdout=asyncio.subprocess.PIPE, # Ambil stdout jika diperlukan
            stderr=asyncio.subprocess.PIPE # Ambil stderr untuk progres
        )

        # --- Membaca dan Mem-parsing Progres dari stderr ---
        # Membaca output stderr line by line secara async
        while True:
            line_bytes = await process.stderr.readline()
            if not line_bytes:
                break # Keluar loop jika stream stderr ditutup

            line = line_bytes.decode('utf-8', errors='ignore').strip()
            if not line:
                continue # Lewati baris kosong

            # yt-dlp --progress-template "%(progress)j" output adalah JSON
            try:
                progress_data = json.loads(line)

                # Cek status unduhan
                status = progress_data.get("status")
                if status == "finished":
                    logging.info(f"Unduhan selesai: {url}")
                    # Kirim update progres terakhir sebelum keluar loop
                    final_progress_text = f"Mengunduh: `{url}`\n**✅ Selesai**"
                    if final_progress_text != last_progress_text:
                         try:
                              await status_message.edit_text(final_progress_text, parse_mode=ParseMode.MARKDOWN)
                              last_progress_text = final_progress_text
                         except Exception as edit_e:
                              logging.warning(f"Gagal mengedit pesan final progres untuk {url}: {edit_e}")

                    break # Keluar loop jika status finished
                elif status == "downloading" or status == "extracting":
                    # Parsing data progres untuk status downloading/extracting
                    percent = progress_data.get("fraction_downloaded") # 0.0 - 1.0
                    speed = progress_data.get("speed") # byte/detik
                    eta = progress_data.get("eta") # detik
                    downloaded_bytes = progress_data.get("downloaded_bytes")
                    total_bytes = progress_data.get("total_bytes") or progress_data.get("total_bytes_estimate") # total bisa estimasi

                    if percent is not None:
                        percent_str = f"{percent * 100:.1f}%"
                        speed_str = "N/A"
                        if speed is not None and speed > 0:
                             speed_str = f"{speed/1024/1024:.2f} MiB/s" if speed > 1024*1024 else f"{speed/1024:.2f} KiB/s"
                        eta_str = "N/A"
                        if eta is not None:
                             minutes, seconds = divmod(int(eta), 60)
                             eta_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"

                        downloaded_str = "N/A"
                        if downloaded_bytes is not None:
                             downloaded_str = f"{downloaded_bytes/1024/1024:.2f} MiB" if downloaded_bytes > 1024*1024 else f"{downloaded_bytes/1024:.2f} KiB"
                        total_str = "N/A"
                        if total_bytes is not None:
                             total_str = f"{total_bytes/1024/1024:.2f} MiB" if total_bytes > 1024*1024 else f"{total_bytes/1024:.2f} KiB"


                        progress_text = (
                            f"Mengunduh: `{url}`\n"
                            f"Status: **{status.capitalize()}**\n" # Status seperti Downloading/Extracting
                            f"Progress: **{percent_str}**\n"
                            f"Sudah terunduh: {downloaded_str} / {total_str}\n"
                            f"Kecepatan: {speed_str}\n"
                            f"ETA: {eta_str}"
                        )

                        # Update pesan Telegram
                        # Batasi frekuensi edit pesan untuk menghindari FloodWait Telegram
                        current_time = asyncio.get_event_loop().time()
                        # Update jika > 3 detik sejak update terakhir ATAU progresnya tepat 100%
                        if current_time - last_update_time > 3 or percent == 1.0:
                             if progress_text != last_progress_text: # Hanya edit jika teks berubah
                                  try:
                                      # await status_message.edit_text(progress_text, parse_mode=ParseMode.MARKDOWN)
                                      # Menggunakan edit_text dengan disable_web_page_preview=True karena URL di pesan
                                      await status_message.edit_text(progress_text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
                                      last_update_time = current_time
                                      last_progress_text = progress_text
                                      # logging.debug(f"Pesan progres diupdate untuk {url}: {percent_str}")
                                  except Exception as edit_e:
                                      # Tangani FloodWait atau error edit lainnya
                                      # logging.warning(f"Gagal mengedit pesan progres untuk {url}: {edit_e}")
                                      # Jika error adalah FloodWait, Pyrogram sering menghandle internal, tapi mungkin perlu logic retry
                                      # Jika error lain, mungkin ada masalah dengan pesan atau koneksi Telegram
                                      pass # Biarkan logging di atas yang handle

                else:
                    # Log status atau info lain dari yt-dlp (misal: destination, downloading f...)
                    logging.info(f"Info yt-dlp: {line}")

            except json.JSONDecodeError:
                # Jika output bukan JSON (misalnya, pesan error lain dari yt-dlp yang tidak dalam format JSON)
                logging.warning(f"Output non-JSON dari yt-dlp stderr: {line}")
                # Anda bisa menambahkan logic untuk menampilkan pesan non-progress penting ini ke user
                # Tapi hati-hati agar tidak spam chat.
                # Contoh: await status_message.reply_text(f"Info dari downloader: {line}")

        # --- Menunggu Proses yt-dlp Selesai dan Memeriksa Return Code ---
        returncode = await process.wait() # Tunggu proses yt-dlp selesai sepenuhnya
        logging.info(f"Proses yt-dlp selesai dengan kode {returncode} untuk {url}")

        if returncode != 0:
            # Baca sisa output error jika ada
            remaining_stderr = await process.stderr.read()
            error_output = remaining_stderr.decode('utf-8', errors='ignore').strip()
            # Ambil error dari stderr yt-dlp jika return code bukan 0
            error_message = error_output or f"yt-dlp exited with code {returncode} without stderr output."
            logging.error(f"Unduhan gagal untuk {url}. Error: {error_message}")
            return None, error_message # Kembalikan None dan pesan error

        # --- Menemukan File yang Diunduh Setelah Sukses ---
        # Setelah yt-dlp selesai mengunduh (return code 0), dapatkan info -j untuk menemukan path file.
        # Ini juga harus dijalankan secara async.
        info_command = ["yt-dlp", "-j", url]
        logging.info(f"Menjalankan perintah info (-j): {' '.join(info_command)}")

        info_process = await asyncio.create_subprocess_exec(
            *info_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE # Biarkan stderr agar bisa lihat error info
        )
        info_stdout_bytes, info_stderr_bytes = await info_process.communicate() # Tunggu info selesai
        info_returncode = info_process.returncode

        info_stdout = info_stdout_bytes.decode('utf-8', errors='ignore')
        info_stderr = info_stderr_bytes.decode('utf-8', errors='ignore').strip()


        downloaded_file_path = None
        if info_returncode == 0:
            try:
                info = json.loads(info_stdout)
                # yt-dlp v2023.11.16+ menambahkan properti 'filepath' saat menggunakan -o
                downloaded_file_path = info.get('filepath')

                if not downloaded_file_path:
                     # Fallback jika 'filepath' tidak ada (kurang handal)
                     # Perlu memastikan template output di awal sama persis dengan yang dipakai yt-dlp
                     # dan karakter ilegal dihandle seperti --restrict-filenames
                     # Mengandalkan 'filepath' di JSON adalah cara terbaik
                     logging.warning("Properti 'filepath' tidak ditemukan di output -j. Rekonstruksi path mungkin tidak akurat.")
                     # Jika tidak ada filepath, Anda mungkin perlu logika yang lebih canggih
                     # atau asumsikan nama file berdasarkan title dan ext jika template -o sederhana.
                     # Contoh rekonstruksi sangat sederhana (bisa salah):
                     title = info.get('title', 'download')
                     ext = info.get('ext', 'mp4')
                     # Membersihkan nama file agar sesuai dengan --restrict-filenames butuh regex
                     # Pola yt-dlp bisa bervariasi, ini hanya contoh
                     cleaned_title = re.sub(r'[^\w\s.-]', '', title).replace(' ', '_')
                     downloaded_file_path = os.path.join(DOWNLOAD_DIR, f"{cleaned_title}.{ext}")
                     logging.warning(f"Mencoba merekonstruksi path: {downloaded_file_path}")


                logging.info(f"Diperkirakan file terunduh di: {downloaded_file_path}")

                # Pastikan file benar-benar ada dan bisa diakses
                if downloaded_file_path and os.path.exists(downloaded_file_path):
                    logging.info(f"File ditemukan di: {downloaded_file_path}")
                    return downloaded_file_path, None # Sukses, kembalikan path dan None error
                else:
                    error_message = f"File {downloaded_file_path} tidak ditemukan di direktori unduhan setelah yt-dlp selesai."
                    logging.error(error_message)
                    return None, error_message

            except json.JSONDecodeError:
                 error_message = f"Gagal mem-parse output info JSON dari yt-dlp:\n{info_stdout}"
                 logging.error(error_message)
                 return None, error_message
            except Exception as e:
                 error_message = f"Terjadi kesalahan saat memproses info yt-dlp: {e}"
                 logging.error(error_message)
                 return None, error_message
        else:
             error_message = f"Gagal mendapatkan info yt-dlp (-j). Return code: {info_returncode}. Stderr:\n{info_stderr}"
             logging.error(error_message)
             return None, error_message


    except Exception as e:
        # Tangani error saat membuat subprocess atau membaca stream
        error_message = f"Terjadi kesalahan saat menjalankan proses yt-dlp: {e}"
        logging.error(error_message)
        # Coba terminasi proses jika sempat dibuat
        if process and process.returncode is None:
            try:
                logging.warning("Mencoba terminasi proses yt-dlp yang sedang berjalan...")
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=5) # Tunggu sebentar setelah terminate
                logging.warning("Proses yt-dlp berhasil di-terminate.")
            except asyncio.TimeoutError:
                 logging.error("Proses yt-dlp tidak terminate setelah diberi sinyal, mencoba kill.")
                 try:
                     process.kill()
                     await asyncio.wait_for(process.wait(), timeout=5)
                     logging.error("Proses yt-dlp berhasil di-kill.")
                 except Exception as kill_e:
                     logging.error(f"Gagal membunuh proses yt-dlp: {kill_e}")
            except Exception as term_e:
                logging.error(f"Gagal terminate proses yt-dlp: {term_e}")
        return None, error_message


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
`/download https://www.youtube.com/watch?v=dQw4w9WgXcQ`

Saya akan berusaha mengunduh video tersebut dan mengirimkannya kepada Anda.

*Pastikan Anda menggunakan perintah ini di chat pribadi dengan bot.*
    """
    # Mengirim pesan balasan ke pengguna menggunakan Markdown
    # Menggunakan parse_mode=ParseMode.MARKDOWN
    await message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True) # disable_web_page_preview=True agar link contoh tidak menampilkan preview


# Handler untuk perintah /download
@app.on_message(filters.command("download") & filters.private) # Hanya merespons /download di chat pribadi
async def handle_download_command(client: Client, message: Message):
    """
    Menangani perintah /download. Memproses link, memulai unduhan async, dan mengirim file.
    """
    chat_id = message.chat.id
    logging.info(f"Received /download command from chat ID: {chat_id}")

    # Memeriksa apakah URL disediakan setelah perintah
    if len(message.command) < 2:
        # Menggunakan parse_mode=ParseMode.MARKDOWN
        await message.reply_text("Mohon berikan URL setelah perintah /download. Contoh: `/download <link_video>`", parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        logging.warning(f"Received /download command without URL from chat ID: {chat_id}")
        return

    # Mengambil URL dari argumen perintah
    url = message.command[1].strip()

    logging.info(f"Processing download request for URL: {url}")

    # Memberi tahu pengguna bahwa proses unduhan dimulai dan simpan objek pesan ini
    # Pesan ini akan diupdate dengan progres
    try:
        status_message = await message.reply_text(f"Memulai unduhan untuk: `{url}`", parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception as e:
        logging.error(f"Gagal mengirim pesan status awal ke {chat_id}: {e}")
        # Jika gagal mengirim pesan status, tidak bisa update progres. Berikan pesan error fatal.
        await client.send_message(chat_id, f"❌ Gagal memulai proses unduhan. Tidak dapat mengirim pesan status awal: `{e}`", parse_mode=ParseMode.MARKDOWN)
        return


    # --- Alur Logika Unduhan ---
    # Memanggil fungsi unduhan yang sekarang async dan melaporkan progres ke status_message
    downloaded_file_path, error_message = await download_with_ytdlp(url, status_message)


    # --- Mengirim File Setelah Unduhan Selesai atau Melaporkan Error ---
    if downloaded_file_path:
        logging.info(f"Unduhan lokal selesai: {downloaded_file_path}. Mengirim file ke {chat_id}.")
        try:
            # Update pesan status terakhir sebelum upload
            try:
                await status_message.edit_text("✅ Unduhan selesai. Mengunggah file ke Telegram...")
            except Exception as e:
                 logging.warning(f"Gagal mengedit pesan status sebelum upload: {e}")
                 # Kirim pesan baru jika edit gagal
                 await client.send_message(chat_id, "✅ Unduhan selesai. Mengunggah file ke Telegram...", parse_mode=ParseMode.MARKDOWN)


            # Mengunggah file menggunakan Pyrogram
            # send_document lebih cocok untuk file media
            await client.send_document(
                chat_id=chat_id, # ID chat tujuan
                document=downloaded_file_path, # Path ke file lokal
                caption=f"✅ Unduhan selesai:\n`{url}`", # Contoh caption dengan Markdown
                parse_mode=ParseMode.MARKDOWN, # Menggunakan ParseMode.MARKDOWN
                # Pertimbangkan penambahan progress callback untuk upload juga jika file besar
                disable_web_page_preview=True # Disable preview link di caption
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
            try:
                 await status_message.edit_text(f"❌ Gagal mengirim file `{os.path.basename(downloaded_file_path)}`:\n`{e}`", parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
            except Exception as edit_e:
                 logging.warning(f"Gagal mengedit pesan error pengiriman: {edit_e}. Mengirim pesan error baru.")
                 await client.send_message(chat_id, f"❌ Gagal mengirim file `{os.path.basename(downloaded_file_path)}`:\n`{e}`", parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)


            # Penting: Jika pengiriman gagal, file lokal mungkin masih ada. Hapus di sini juga.
            try:
                 logging.info(f"Mencoba menghapus file lokal setelah gagal kirim: {downloaded_file_path}")
                 os.remove(downloaded_file_path)
                 logging.info(f"File {downloaded_file_path} dihapus setelah gagal kirim.")
            except Exception as del_e:
                 logging.error(f"Gagal menghapus file lokal {downloaded_file_path} setelah gagal kirim: {del_e}")


    else:
        # Jika unduhan gagal (error_message sudah diisi oleh download_with_ytdlp)
        logging.error(f"Unduhan gagal untuk {url}. Error: {error_message}")
        # Edit pesan status terakhir dengan pesan error
        try:
             await status_message.edit_text(f"❌ Unduhan gagal untuk `{url}`.\nError: `{error_message}`", parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        except Exception as edit_e:
             logging.warning(f"Gagal mengedit pesan error unduhan: {edit_e}. Mengirim pesan error baru.")
             await client.send_message(chat_id, f"❌ Unduhan gagal untuk `{url}`.\nError: `{error_message}`", parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)


    # Opsional: Membersihkan direktori unduhan secara berkala atau setelah setiap unduhan
    # Jika DOWNLOAD_DIR hanya digunakan oleh satu proses download pada satu waktu, bisa dibersihkan.
    # Jika multiple concurrent downloads mungkin terjadi, ini TIDAK AMAN.
    # Pendekatan yang lebih aman adalah memastikan setiap file dihapus setelah diproses.
    # Kode cleanup os.remove() di atas lebih disarankan.


# --- Menjalankan Bot dan Health Check Server ---
# --- Menjalankan Bot dan Health Check Server ---
# Struktur terbaik dengan Pyrogram async:
async def main():
    logging.info("Memulai aplikasi bot dan health check server...")
    # 1. Mulai Health Check Server sebagai task
    health_server_task = asyncio.create_task(start_health_server())
    logging.info("Health check server task created.")

    # 2. Start Pyrogram Client (async)
    # Ini akan terhubung dan mengotentikasi bot
    await app.start()
    logging.info("Pyrogram Client terhubung ke Telegram.")
    logging.info("Bot siap menerima perintah.")

    # Keep the event loop running indefinitely to process updates and tasks
    # This await Future() will block the main coroutine until cancelled (e.g., via signal)
    try:
        await asyncio.get_event_loop().create_future()
    except asyncio.CancelledError:
        logging.info("Main task cancelled. Starting shutdown.")
    finally:
        # Pindahkan logika cleanup ke sini, di dalam konteks async main()
        if app and app.is_connected:
            logging.info("Menghentikan Pyrogram client...")
            await app.stop() # Gunakan await di sini
            logging.info("Pyrogram client dihentikan.")


# Jalankan fungsi async main()
if __name__ == '__main__':
    logging.info("Memulai aplikasi bot dan health check server...")
    try:
        # asyncio.run() akan membuat loop baru, menjalankannya sampai main() selesai, lalu menutup loop.
        # Ini adalah cara modern yang disarankan.
        asyncio.run(main())
    except Exception as e:
        # Menangkap exception fatal saat menjalankan asyncio.run(main())
        logging.error(f"Error fatal saat menjalankan asyncio.run(main()): {e}")
        sys.exit(1) # Keluar dari proses dengan kode error

    # Blok finally di sini (level teratas) tidak lagi perlu menangani app.stop()
    # Kode cleanup ini akan berjalan jika proses Python berakhir
    logging.info("Proses shutdown bot selesai.")
