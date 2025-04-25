import os
import logging
import subprocess
import json
import sys
import asyncio # Import asyncio
from telethon import TelegramClient, events

# Import aiohttp untuk server health check
import aiohttp
import aiohttp.web

# Mungkin perlu menginstal:
# pip install telethon yt-dlp aiohttp aria2p pyppeteer

# --- Konfigurasi Logger (Opsional, tapi disarankan) ---
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s',
                    level=logging.INFO) # Naikkan level ke INFO atau WARNING untuk mengurangi log

# --- Konfigurasi Bot ---
API_ID = 25315175 #os.environ.get("API_ID")
API_HASH = "69f20e99df186f7c694fc3ad69b7ecc4" #os.environ.get("API_HASH")
BOT_TOKEN = "6605145904:AAEUT22p5oi_JK7U93Ld5_Ts_CK8euEHYao"#os.environ.get("BOT_TOKEN")

if not BOT_TOKEN:
    logging.error("Error: Environment variable BOT_TOKEN tidak disetel.")
    sys.exit(1)

SESSION_FILE = "/app/my_bot.session"
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/app/downloads")
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)
    logging.info(f"Created download directory: {DOWNLOAD_DIR}")

# --- Konfigurasi Aria2c (Jika Menggunakan RPC - Opsi 1: Tidak Menggunakan RPC) ---
# Kita mengasumsikan Anda tidak menggunakan RPC di skrip ini untuk Opsi 1
# Jadi, bagian inisialisasi Aria2c RPC di bawah ini bisa dihapus atau dibiarkan seperti adanya
# (dengan catatan tidak akan terhubung jika ARIA2_RPC_URL tidak disetel atau salah)
ARIA2_RPC_URL = os.environ.get("ARIA2_RPC_URL") # Baca tapi mungkin tidak digunakan
ARIA2_RPC_SECRET = os.environ.get("ARIA2_RPC_SECRET") # Baca tapi mungkin tidak digunakan

aria2 = None # Setel ke None karena tidak akan menggunakan RPC
if ARIA2_RPC_URL:
     logging.info("ARIA2_RPC_URL disetel, tapi mode saat ini mengasumsikan tidak menggunakan Aria2c RPC.")
     # Anda bisa hapus semua kode inisialisasi Aria2p di sini jika yakin tidak menggunakannya

# --- Konfigurasi Health Check Server ---
# Port yang akan didengarkan oleh server health check
HEALTH_CHECK_PORT = int(os.environ.get("HEALTH_CHECK_PORT", 8080)) # Default ke port 8080

# --- Health Check Handler (Fungsi yang akan dipanggil saat /health diakses) ---
async def health_handler(request):
    # Anda bisa menambahkan logika di sini untuk memeriksa status bot yang lebih dalam
    # Misalnya, cek apakah klien Telethon terhubung
    # if client and client.is_connected():
    #     return aiohttp.web.Response(text="Bot connected to Telegram", status=200)
    # else:
    #     # Jika bot tidak terhubung, bisa dianggap tidak sehat
    #     logging.warning("Health check requested, but bot is not connected to Telegram.")
    #     return aiohttp.web.Response(text="Bot not connected to Telegram", status=503)

    # Untuk health check sederhana, cukup kembalikan status 200 OK
    return aiohttp.web.Response(text="Bot service is healthy", status=200)

# --- Fungsi untuk Memulai Health Check Server ---
async def start_health_server():
    app = aiohttp.web.Application()
    # Daftarkan handler untuk jalur /health
    app.router.add_get('/health', health_handler)

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    # Mulai server mendengarkan di semua antarmuka (host='0.0.0.0') pada port yang dikonfigurasi
    site = aiohttp.web.TCPSite(runner, host='0.0.0.0', port=HEALTH_CHECK_PORT)
    logging.info(f"Starting health check server on http://0.0.0.0:{HEALTH_CHECK_PORT}/health")
    await site.start()
    # site.start() adalah non-blocking, server berjalan di background asyncio loop

# --- Inisialisasi Telethon Client ---
try:
    # client = TelegramClient(SESSION_FILE, int(API_ID) if API_ID else 0, API_HASH).start(bot_token=BOT_TOKEN)
     # Menggunakan async with untuk Telethon agar lebih rapi dalam manajemen siklus hidup
    client = TelegramClient(SESSION_FILE, int(API_ID) if API_ID else 0, API_HASH)

except Exception as e:
    logging.error(f"Gagal menginisialisasi Telegram Client: {e}")
    sys.exit(1)

logging.info("Telegram Client initialized.")


# --- Fungsi untuk Memanggil yt-dlp ---
# (Kode fungsi download_with_ytdlp tetap sama seperti sebelumnya)
def download_with_ytdlp(url, chat_id):
    try:
        logging.info(f"Memulai unduhan dengan yt-dlp untuk: {url}")
        # Memberi tahu pengguna bahwa unduhan dimulai
        # Menggunakan await karena ini adalah fungsi async (dari event handler)
        # PENTING: Jika fungsi ini dipanggil dari handler async, send_message harus diawait
        # Namun, fungsi download_with_ytdlp ini sendiri BUKAN async function.
        # Jadi, jika Anda memanggilnya dari handler async, Anda perlu mempertimbangkan
        # bagaimana send_message di dalamnya dieksekusi.
        # Untuk kesederhanaan, kita asumsikan fungsi ini akan dipanggil dari async context.
        # Anda mungkin perlu menyesuaikan jika tidak.
        asyncio.run_coroutine_threadsafe(client.send_message(chat_id, f"Memulai unduhan untuk: {url}"), client.loop)
        # Atau jalankan di executor jika ini blocking call dari async context
        # await client.send_message(chat_id, f"Memulai unduhan untuk: {url}") # Ini jika download_with_ytdlp adalah async

        # Opsi yt-dlp:
        output_template = os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s")

        ytdlp_command = [
            "yt-dlp",
            "--ignore-errors",
            "--restrict-filenames",
            "--no-warnings",
            "--progress",
            "-o", output_template,
            # Mengaktifkan aria2c sebagai downloader eksternal (jika terinstal dan di PATH)
            "--external-downloader", "aria2c",
            "--external-downloader-args", "aria2c:\"-x16 -s16 -k1M\"", # Contoh argumen
            url
        ]

        logging.info(f"Menjalankan perintah: {' '.join(ytdlp_command)}")

        # Menjalankan subprocess.run bersifat blocking.
        # Untuk menjaga bot responsif, ini sebaiknya dijalankan di thread atau proses terpisah
        # menggunakan asyncio's loop.run_in_executor() jika dipanggil dari context async.
        # Untuk contoh sederhana ini, kita gunakan subprocess.run yang blocking.
        # Jika bot menjadi tidak responsif saat unduhan berjalan, inilah alasannya.
        process = subprocess.run(ytdlp_command, capture_output=True, text=True)

        logging.debug(f"yt-dlp stdout:\n{process.stdout}")
        logging.debug(f"yt-dlp stderr:\n{process.stderr}")

        if process.returncode != 0:
            error_message = f"Gagal mengunduh {url}. Error: {process.stderr}"
            logging.error(error_message)
            asyncio.run_coroutine_threadsafe(client.send_message(chat_id, error_message), client.loop)
            return None # Indikasi gagal

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
                    return downloaded_file_path
                else:
                    logging.error(f"File tidak ditemukan setelah unduhan selesai: {downloaded_file_path}")
                    asyncio.run_coroutine_threadsafe(client.send_message(chat_id, f"Unduhan selesai, tetapi tidak dapat menemukan file untuk {url}."), client.loop)
                    return None

            except json.JSONDecodeError:
                 logging.error("Gagal mem-parse output info JSON dari yt-dlp.")
                 asyncio.run_coroutine_threadsafe(client.send_message(chat_id, f"Gagal mendapatkan info file dari yt-dlp untuk {url}."), client.loop)
                 return None
            except Exception as e:
                 logging.error(f"Terjadi kesalahan saat memproses info yt-dlp: {e}")
                 asyncio.run_coroutine_threadsafe(client.send_message(chat_id, f"Terjadi kesalahan saat memproses info file untuk {url}."), client.loop)
                 return None
        else:
             logging.error(f"Gagal mendapatkan info yt-dlp (-j). Error: {info_process.stderr}")
             asyncio.run_coroutine_threadsafe(client.send_message(chat_id, f"Gagal mendapatkan info file dari yt-dlp untuk {url}."), client.loop)
             return None

    except Exception as e:
        error_message = f"Terjadi kesalahan umum saat mengunduh {url}: {e}"
        logging.error(error_message)
        # Gunakan run_coroutine_threadsafe karena kita di luar fungsi async
        asyncio.run_coroutine_threadsafe(client.send_message(chat_id, error_message), client.loop)
        return None

# --- Fungsi untuk Menangani Cloudflare (Sangat Kompleks, Hanya Kerangka) ---
# (Fungsi bypass_cloudflare tetap sama seperti sebelumnya, belum diimplementasikan penuh)
async def bypass_cloudflare(url):
   logging.info(f"Mencoba melewati Cloudflare untuk: {url}")
   logging.warning("Fungsi bypass Cloudflare belum diimplementasikan sepenuhnya.")
   return None

# --- Event Handler untuk Pesan Masuk ---
@client.on(events.NewMessage(pattern='/download (.+)'))
async def handle_download_command(event):
    chat_id = event.chat_id
    url = event.pattern_match.group(1).strip()
    logging.info(f"Menerima perintah unduh untuk: {url} dari chat ID: {chat_id}")

    if not url:
        await client.send_message(chat_id, "Mohon berikan URL setelah perintah /download.")
        return

    # Karena download_with_ytdlp bersifat blocking (menggunakan subprocess.run)
    # kita harus menjalankannya di executor agar tidak memblokir loop asyncio Telethon.
    loop = asyncio.get_event_loop()
    downloaded_file_path = await loop.run_in_executor(
        None, # Gunakan default ThreadPoolExecutor
        download_with_ytdlp, # Fungsi yang akan dijalankan
        url, chat_id # Argumen untuk fungsi download_with_ytdlp
    )

    # --- Mengirim File Setelah Unduhan Selesai ---
    if downloaded_file_path:
        logging.info(f"Unduhan lokal selesai: {downloaded_file_path}")
        try:
            await client.send_message(chat_id, "Unduhan selesai. Mengunggah file ke Telegram...")
            await client.send_file(chat_id, downloaded_file_path)
            logging.info(f"File {downloaded_file_path} berhasil dikirim ke {chat_id}")

            # Opsional: Hapus file lokal setelah dikirim
            # logging.info(f"Menghapus file lokal: {downloaded_file_path}")
            # os.remove(downloaded_file_path)
            # logging.info(f"File {downloaded_file_path} dihapus.")

        except Exception as e:
            logging.error(f"Gagal mengirim file {downloaded_file_path} ke {chat_id}: {e}")
            await client.send_message(chat_id, f"Gagal mengirim file {os.path.basename(downloaded_file_path)}: {e}")
    else:
        # Pesan error spesifik sudah dikirim oleh fungsi download_with_ytdlp
        logging.error(f"Unduhan gagal atau file tidak ditemukan untuk {url}.")
        pass # Tidak perlu pesan error tambahan di sini


# --- Menjalankan Bot dan Health Check Server ---
if __name__ == '__main__':
    logging.info("Memulai aplikasi bot dan health check server...")
    loop = asyncio.get_event_loop()

    try:
        # 1. Mulai Health Check Server sebagai task asyncio
        health_server_task = loop.create_task(start_health_server())
        logging.info("Health check server task created.")

        # 2. Hubungkan klien Telethon
        logging.info("Connecting Telegram client...")
        loop.run_until_complete(client.connect())
        if not client.is_connected():
             logging.error("Gagal terhubung ke Telegram.")
             sys.exit(1)
        logging.info("Telegram client connected.")


        # 3. Jalankan event loop Telethon sampai terputus
        # run_until_disconnected() akan menjalankan loop asyncio,
        # jadi task health_server_task juga akan berjalan.
        logging.info("Running bot until disconnected...")
        client.run_until_disconnected()

    except Exception as e:
        logging.error(f"Error fatal saat menjalankan bot: {e}")
        sys.exit(1)
    finally:
         # Pastikan semua task asyncio dibatalkan saat shutdown
         tasks = asyncio.all_tasks(loop=loop)
         for task in tasks:
             task.cancel()
         loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
         # Tutup loop jika diperlukan (run_until_disconnected biasanya mengurus ini)
         # loop.close()
         logging.info("Bot shutdown complete.")
