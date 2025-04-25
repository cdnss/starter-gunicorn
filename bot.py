import os
import logging
import subprocess
import json
import sys # Import sys untuk exit
from telethon import TelegramClient, events
# Mungkin perlu menginstal:
# pip install telethon yt-dlp aria2p pyppeteer

# --- Konfigurasi Logger (Opsional, tapi disarankan) ---
# Set level WARNING atau INFO untuk log Telethon, DEBUG untuk log lebih detail
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s',
                    level=logging.INFO)

# --- Konfigurasi Bot ---
# Baca dari Environment Variables yang disetel oleh Docker atau docker run
# Penting: Environment Variables di Dockerfile diakses melalui os.environ

API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Periksa apakah BOT_TOKEN sudah disetel (wajib untuk mode bot)
# Jika menggunakan userbot, periksa API_ID dan API_HASH
if not BOT_TOKEN:
    logging.error("Error: Environment variable BOT_TOKEN tidak disetel.")
    sys.exit(1) # Keluar jika konfigurasi penting tidak ada

# Nama file sesi Telethon di dalam container
# Ini akan disimpan di WORKDIR (/app) berkat volume mapping
SESSION_FILE = "/app/my_bot.session"

# Folder tempat menyimpan file yang diunduh di dalam container
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/app/downloads") # Default sesuai Dockerfile ARG/ENV
# Pastikan direktori unduhan ada
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)
    logging.info(f"Created download directory: {DOWNLOAD_DIR}")

# --- Konfigurasi Aria2c (Jika Menggunakan RPC) ---
# Baca dari Environment Variables
ARIA2_RPC_URL = os.environ.get("ARIA2_RPC_URL", "http://localhost:6800/rpc") # Default sesuai Dockerfile ENV
ARIA2_RPC_SECRET = os.environ.get("ARIA2_RPC_SECRET") # None jika tidak disetel

# --- Inisialisasi Telethon Client ---
# Gunakan SESSION_FILE dan BOT_TOKEN dari konfigurasi di atas
# API_ID dan API_HASH tetap perlu disetel, meskipun untuk mode bot kadang tidak digunakan
# secara langsung untuk otentikasi awal setelah sesi dibuat, tapi tetap best practice untuk menyertakannya.
try:
    # Jika API_ID atau API_HASH tidak disetel (misalnya hanya menggunakan BOT_TOKEN),
    # Telethon mungkin akan menggunakan nilai default atau membaca dari file konfigurasi
    # jika ada. Menyediakan nilai dari ENV tetap disarankan.
    client = TelegramClient(SESSION_FILE, int(API_ID) if API_ID else 0, API_HASH).start(bot_token=BOT_TOKEN)
except Exception as e:
    logging.error(f"Gagal terhubung ke Telegram: {e}")
    sys.exit(1)

logging.info("Bot sedang berjalan dan terhubung ke Telegram...")

# --- Fungsi untuk Memanggil yt-dlp ---
def download_with_ytdlp(url, chat_id):
    try:
        logging.info(f"Memulai unduhan dengan yt-dlp untuk: {url}")
        # Memberi tahu pengguna bahwa unduhan dimulai
        # Menggunakan await karena ini adalah fungsi async (dari event handler)
        client.send_message(chat_id, f"Memulai unduhan untuk: {url}")

        # Opsi yt-dlp:
        # -o: Lokasi output. Menggunakan %(title)s dan %(ext)s untuk nama file otomatis di DOWNLOAD_DIR.
        # --restrict-filenames: Membersihkan nama file.
        # --no-warnings: Jangan tampilkan peringatan.
        # --progress: Tampilkan progress di stderr (bisa kita tangkap nanti).
        # --external-downloader aria2c: Menggunakan aria2c sebagai downloader eksternal (jika terinstal dan di PATH).
        # Jika Anda ingin yt-dlp hanya memberikan info, gunakan `-j` dan jangan --external-downloader

        # Perhatikan path output - pastikan menggunakan variabel DOWNLOAD_DIR
        output_template = os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s")

        ytdlp_command = [
            "yt-dlp",
            "--ignore-errors", # Lanjutkan jika ada error kecil
            "--restrict-filenames",
            "--no-warnings",
            "--progress",
            "-o", output_template,
            # Uncomment baris di bawah jika ingin yt-dlp menggunakan aria2c sebagai downloader
            # Pastikan aria2c terinstal dan ada di PATH di Dockerfile
            # "--external-downloader", "aria2c",
            # "--external-downloader-args", "aria2c:\"-x16 -s16 -k1M\"", # Contoh argumen aria2c
            url
        ]

        logging.info(f"Menjalankan perintah: {' '.join(ytdlp_command)}")

        # Menjalankan perintah yt-dlp
        # capture_output=True menangkap stdout dan stderr
        # text=True mengembalikan output sebagai string
        # Perlu proses lebih lanjut untuk mem-parsing progress dari stderr secara real-time
        # Ini membutuhkan pendekatan yang lebih canggok dari subprocess.run
        # Untuk contoh ini, kita akan menggunakan subprocess.run dan menunggu hasilnya
        process = subprocess.run(ytdlp_command, capture_output=True, text=True)

        logging.debug(f"yt-dlp stdout:\n{process.stdout}")
        logging.debug(f"yt-dlp stderr:\n{process.stderr}")

        if process.returncode != 0:
            error_message = f"Gagal mengunduh {url}. Error: {process.stderr}"
            logging.error(error_message)
            client.send_message(chat_id, error_message)
            return None # Indikasi gagal

        # --- Menemukan File yang Diunduh ---
        # Ini adalah bagian yang tricky. yt-dlp menentukan nama file.
        # Cara paling reliable adalah mendapatkan info JSON dari yt-dlp,
        # lalu menggunakan nama file yang diharapkan dari info tersebut.

        info_command = ["yt-dlp", "-j", url]
        info_process = subprocess.run(info_command, capture_output=True, text=True)

        downloaded_file_path = None
        if info_process.returncode == 0:
            try:
                info = json.loads(info_process.stdout)
                # yt-dlp menggunakan format output -o. Kita perlu merekonstruksi path file.
                # yt-dlp v2023.11.16 dan setelahnya memiliki 'filepath' di output -j jika menggunakan -o
                # Cek properti 'filepath' terlebih dahulu
                downloaded_file_path = info.get('filepath')

                if not downloaded_file_path:
                     # Jika 'filepath' tidak ada, coba rekonstruksi dari template output dan info
                     # Ini bisa kurang akurat jika template output sangat kompleks
                     # Contoh rekonstruksi sederhana:
                     expected_filename = f"{info.get('title', 'download')}.{info.get('ext', 'mp4')}"
                     # Bersihkan nama file dari karakter yang tidak diizinkan oleh --restrict-filenames
                     # (yt-dlp --restrict-filenames mengubah spasi menjadi _, menghapus karakter non-ASCII, dll)
                     # Rekonstruksi ini mungkin tidak 100% sama, jadi properti 'filepath' lebih baik jika tersedia.
                     # Jika Anda yakin dengan template output sederhana, Anda bisa coba ini:
                     # cleaned_title = info.get('title', 'download').replace(' ', '_').replace('/', '_') # Contoh sederhana
                     # expected_filename = f"{cleaned_title}.{info.get('ext', 'mp4')}"
                     downloaded_file_path = os.path.join(DOWNLOAD_DIR, expected_filename)
                     logging.warning(f"Properti 'filepath' tidak ditemukan di output -j. Mencoba merekonstruksi path: {downloaded_file_path}. Ini mungkin tidak akurat.")


                logging.info(f"Diperkirakan file terunduh di: {downloaded_file_path}")

                # Pastikan file benar-benar ada setelah download selesai
                if downloaded_file_path and os.path.exists(downloaded_file_path):
                    logging.info(f"File ditemukan: {downloaded_file_path}")
                    return downloaded_file_path
                else:
                    logging.error(f"File tidak ditemukan setelah unduhan selesai: {downloaded_file_path}")
                    client.send_message(chat_id, f"Unduhan selesai, tetapi tidak dapat menemukan file untuk {url}.")
                    return None

            except json.JSONDecodeError:
                 logging.error("Gagal mem-parse output info JSON dari yt-dlp.")
                 client.send_message(chat_id, f"Gagal mendapatkan info file dari yt-dlp untuk {url}.")
                 return None
            except Exception as e:
                 logging.error(f"Terjadi kesalahan saat memproses info yt-dlp: {e}")
                 client.send_message(chat_id, f"Terjadi kesalahan saat memproses info file untuk {url}.")
                 return None
        else:
             logging.error(f"Gagal mendapatkan info yt-dlp (-j). Error: {info_process.stderr}")
             client.send_message(chat_id, f"Gagal mendapatkan info file dari yt-dlp untuk {url}.")
             return None


    except Exception as e:
        error_message = f"Terjadi kesalahan umum saat mengunduh {url}: {e}"
        logging.error(error_message)
        client.send_message(chat_id, error_message)
        return None

# --- Fungsi untuk Menangani Cloudflare (Sangat Kompleks, Hanya Kerangka) ---
# Fungsi ini akan sangat bervariasi tergantung situs dan metode bypass yang digunakan (pyppeteer/selenium)
# Implementasi nyata untuk Docker perlu memastikan browser dan drivernya berjalan dengan benar di container.
async def bypass_cloudflare(url):
    logging.info(f"Mencoba melewati Cloudflare untuk: {url}")
    # --- Kode untuk menggunakan pyppeteer atau selenium di sini ---
    # Ini memerlukan instalasi library dan driver/browser di Dockerfile
    # dan kode Python untuk mengontrol browser headless
    # Contoh menggunakan pyppeteer (membutuhkan asyncio):
    # from pyppeteer import launch
    # browser = await launch(args=['--no-sandbox']) # --no-sandbox sering dibutuhkan di Docker
    # page = await browser.newPage()
    # await page.goto(url)
    # # Tunggu hingga Cloudflare selesai (misalnya, menunggu elemen spesifik muncul)
    # # await page.waitForSelector('selector_setelah_cloudflare')
    # cookies = await page.cookies()
    # final_url = page.url # URL setelah redirect jika ada
    # await browser.close()
    # return {'cookies': cookies, 'url': final_url} # Kembalikan data yang dibutuhkan

    logging.warning("Fungsi bypass Cloudflare belum diimplementasikan sepenuhnya.")
    return None # Kembalikan cookie atau URL jika berhasil, None jika gagal

# --- Fungsi untuk Menambahkan Tugas ke Aria2c RPC (Jika Menggunakan RPC) ---
# Membutuhkan library aria2p
# pip install aria2p
import aria2p

aria2 = None
# Coba inisialisasi koneksi Aria2c RPC saat bot dimulai
if ARIA2_RPC_URL:
    try:
        # Jika ARIA2_RPC_URL adalah "http://host.docker.internal:port", itu akan berfungsi
        # jika aria2c berjalan di host dan Anda menggunakan Docker Desktop
        # Jika aria2c berjalan di container lain di jaringan Docker yang sama,
        # ARIA2_RPC_URL harus menjadi nama service/container aria2c.
        aria2 = aria2p.API(aria2p.Client(host=ARIA2_RPC_URL, secret=ARIA2_RPC_SECRET))
        # Cek apakah aria2c RPC berjalan dengan mencoba memanggil fungsi
        version = aria2.client.get_version()
        logging.info(f"Terhubung ke Aria2c RPC versi: {version.version} di {ARIA2_RPC_URL}")
    except Exception as e:
        logging.error(f"Gagal terhubung ke Aria2c RPC di {ARIA2_RPC_URL}: {e}")
        aria2 = None # Setel kembali ke None jika koneksi gagal

def add_to_aria2(url, chat_id):
    if not aria2:
        client.send_message(chat_id, "Layanan Aria2c RPC tidak tersedia atau gagal terhubung.")
        return None

    try:
        logging.info(f"Menambahkan unduhan ke Aria2c untuk: {url}")
        # Menambahkan URL unduhan ke aria2c
        # Pastikan direktori unduhan di aria2c sesuai dengan volume mount di Docker host
        # Jika aria2c berjalan di host dan volumenya di-mount ke container bot,
        # maka direktori unduhan di aria2c harus path di host.
        # Jika aria2c berjalan di container terpisah dengan volume yang sama di-mount,
        # direktori unduhan di aria2c harus path di container aria2c.
        # Ini adalah kompleksitas tambahan saat menggunakan aria2c RPC terpisah.
        # Menggunakan --external-downloader aria2c di yt-dlp seringkali lebih mudah
        # karena yt-dlp yang memanggil aria2c lokal di container bot.

        # Untuk contoh RPC ini, kita asumsikan Aria2c dapat menulis ke DOWNLOAD_DIR
        # di lingkungan tempat Aria2c berjalan.
        download_options = {"dir": DOWNLOAD_DIR} # Gunakan DOWNLOAD_DIR container

        download = aria2.add_uri(url, options=download_options)
        logging.info(f"Unduhan ditambahkan ke Aria2c: {url}\nGID: {download.gid}")
        client.send_message(chat_id, f"Unduhan ditambahkan ke Aria2c: {url}\nGID: {download.gid}\nAnda perlu memantau progress secara terpisah.")

        # Mengembalikan objek download aria2p
        return download

    except Exception as e:
        error_message = f"Gagal menambahkan unduhan ke Aria2c untuk {url}: {e}"
        logging.error(error_message)
        client.send_message(chat_id, error_message)
        return None

# --- Event Handler untuk Pesan Masuk ---
@client.on(events.NewMessage(pattern='/download (.+)'))
async def handle_download_command(event):
    # Ambil chat ID pengguna
    chat_id = event.chat_id
    # Ambil URL dari pesan
    url = event.pattern_match.group(1).strip() # strip() untuk menghapus spasi di awal/akhir
    logging.info(f"Menerima perintah unduh untuk: {url} dari chat ID: {chat_id}")

    if not url:
        await client.send_message(chat_id, "Mohon berikan URL setelah perintah /download.")
        return

    # --- Alur Logika Unduhan ---

    downloaded_file_path = None
    aria2_download_obj = None # Untuk menyimpan objek aria2p jika menggunakan RPC

    # Metode 1: Coba dengan yt-dlp langsung (opsional menggunakan aria2c via --external-downloader)
    # Ini adalah cara paling umum dan seringkali berhasil
    logging.info(f"Mencoba metode 1: Unduh langsung dengan yt-dlp untuk {url}")
    downloaded_file_path = download_with_ytdlp(url, chat_id)

    # --- Jika yt-dlp gagal (dan Anda curiga karena Cloudflare) ---
    # Logika ini perlu diimplementasikan jika Anda memiliki fungsi bypass_cloudflare yang berfungsi
    # if downloaded_file_path is None:
    #     logging.warning("yt-dlp gagal. Mencoba bypass Cloudflare...")
    #     # Panggil fungsi bypass Cloudflare
    #     cloudflare_data = await bypass_cloudflare(url) # Perlu await karena fungsi bypass adalah async

    #     if cloudflare_data and cloudflare_data.get('url'):
    #         logging.info("Cloudflare bypass berhasil. Mencoba unduh ulang menggunakan URL akhir.")
    #         # Gunakan URL akhir yang didapat dari bypass
    #         # Anda bisa memilih untuk mengulang yt-dlp atau menambahkannya ke aria2c RPC
    #         # Contoh: Coba lagi dengan yt-dlp dan cookies (jika fungsi download_with_ytdlp bisa menerima cookies)
    #         # downloaded_file_path = download_with_ytdlp_with_cookies(cloudflare_data['url'], cloudflare_data.get('cookies'), chat_id)
    #         # Contoh: Tambahkan ke aria2c RPC dengan URL akhir
    #         if aria2:
    #             aria2_download_obj = add_to_aria2(cloudflare_data['url'], chat_id)
    #             if aria2_download_obj:
    #                 downloaded_file_path = "Downloading_via_Aria2c" # Marker sukses menambah ke aria2c
    #         else:
    #             await client.send_message(chat_id, f"Cloudflare bypass berhasil, tetapi Aria2c RPC tidak tersedia untuk melanjutkan unduhan URL akhir {cloudflare_data['url']}.")

    #     elif cloudflare_data:
    #          logging.warning("Cloudflare bypass berhasil, tetapi tidak mendapatkan URL akhir atau data yang berguna.")
    #          await client.send_message(chat_id, f"Cloudflare bypass berhasil, tetapi tidak mendapatkan link unduhan yang valid untuk {url}.")

    #     else:
    #         logging.error("Gagal melewati Cloudflare.")
    #         await client.send_message(chat_id, f"Gagal melewati Cloudflare untuk {url}.")


    # --- Atau jika Anda ingin menggunakan aria2c RPC secara eksplisit sejak awal ---
    # Uncomment dan sesuaikan jika ingin menambah ke aria2c RPC secara manual
    # dengan mengambil info dari yt-dlp terlebih dahulu (-j)
    # if downloaded_file_path is None and not aria2_download_obj and aria2: # Jika belum berhasil dan aria2 RPC tersedia
    #     logging.info(f"Mencoba metode 2: Ambil info yt-dlp (-j) lalu tambah ke Aria2c RPC untuk {url}")
    #     info_command = ["yt-dlp", "-j", url]
    #     info_process = subprocess.run(info_command, capture_output=True, text=True)

    #     if info_process.returncode == 0:
    #         try:
    #             info = json.loads(info_process.stdout)
    #             # Dapatkan URL unduhan terbaik atau list URL
    #             # Ini memerlukan logika untuk memilih format dan mendapatkan URL dari info json
    #             # yt-dlp -j output structure: https://github.com/yt-dlp/yt-dlp/blob/master/README.md#json-output
    #             # Contoh sederhana mengambil URL dari 'url' properti utama atau format pertama:
    #             download_url = info.get('url')
    #             if not download_url and info.get('formats'):
    #                 # Cari format video/audio yang diinginkan
    #                 for f in info['formats']:
    #                      # Contoh: Cari format dengan vcodec dan acodec (kombinasi video+audio)
    #                      if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
    #                          download_url = f.get('url')
    #                          break # Ambil format pertama yang cocok

    #             if download_url:
    #                  aria2_download_obj = add_to_aria2(download_url, chat_id)
    #                  if aria2_download_obj:
    #                      downloaded_file_path = "Downloading_via_Aria2c" # Marker
    #                      logging.info(f"Unduhan ditambahkan ke Aria2c untuk {url} dari info yt-dlp.")
    #                  # Perlu logika tambahan untuk memantau aria2_download_obj dan mengirimkan file setelah selesai.
    #             else:
    #                 logging.error(f"Tidak dapat menemukan URL unduhan yang cocok di info yt-dlp untuk {url}.")
    #                 await client.send_message(chat_id, f"Tidak dapat menemukan URL unduhan di info yt-dlp untuk {url}.")
    #         except json.JSONDecodeError:
    #             logging.error("Gagal mem-parse output info JSON dari yt-dlp saat mencoba menambah ke Aria2c.")
    #             await client.send_message(chat_id, f"Gagal memproses info file dari yt-dlp untuk {url} (untuk Aria2c).")
    #         except Exception as e:
    #             logging.error(f"Terjadi kesalahan saat memproses info yt-dlp atau menambah ke Aria2c untuk {url}: {e}")
    #             await client.send_message(chat_id, f"Terjadi kesalahan saat memproses info file untuk {url} (untuk Aria2c): {e}")

    #     else:
    #         logging.error(f"Gagal mendapatkan info yt-dlp (-j) untuk {url} saat mencoba menambah ke Aria2c. Error: {info_process.stderr}")
    #         await client.send_message(chat_id, f"Gagal mendapatkan info file dari yt-dlp untuk {url} (untuk Aria2c).")


    # --- Mengirim File Setelah Unduhan Selesai ---
    # Bagian ini hanya berjalan jika metode unduhan langsung (yt-dlp tanpa RPC) berhasil
    # dan menghasilkan downloaded_file_path yang valid.
    # Jika menggunakan Aria2c RPC, Anda perlu mekanisme terpisah untuk memantau
    # status Aria2c dan memanggil fungsi pengiriman file saat unduhan selesai.
    if downloaded_file_path and downloaded_file_path != "Downloading_via_Aria2c":
        logging.info(f"Unduhan lokal selesai: {downloaded_file_path}")
        try:
            # Memberi tahu pengguna sebelum mengunggah
            await client.send_message(chat_id, "Unduhan selesai. Mengunggah file ke Telegram...")

            # Mengunggah file. Telethon akan menangani file besar dengan memecahnya.
            await client.send_file(chat_id, downloaded_file_path)
            logging.info(f"File {downloaded_file_path} berhasil dikirim ke {chat_id}")

            # Opsional: Hapus file lokal setelah dikirim
            # logging.info(f"Menghapus file lokal: {downloaded_file_path}")
            # os.remove(downloaded_file_path)
            # logging.info(f"File {downloaded_file_path} dihapus.")

        except Exception as e:
            logging.error(f"Gagal mengirim file {downloaded_file_path} ke {chat_id}: {e}")
            await client.send_message(chat_id, f"Gagal mengirim file {os.path.basename(downloaded_file_path)}: {e}")

    elif downloaded_file_path == "Downloading_via_Aria2c":
         # Pesan ini sudah dikirim di fungsi add_to_aria2
         logging.info("Unduhan sedang diproses oleh Aria2c. Menunggu penyelesaian untuk pengiriman.")
         # Di sini Anda perlu menambahkan logika pemantauan Aria2c dan pengiriman file setelah selesai.
         pass # Tidak perlu aksi lebih lanjut di handler ini untuk metode RPC

    else:
        # Jika downloaded_file_path adalah None setelah semua upaya
        logging.error(f"Semua metode unduhan gagal untuk {url}.")
        # Pesan error spesifik seharusnya sudah dikirim oleh fungsi download_with_ytdlp atau add_to_aria2
        pass # Tidak perlu pesan error tambahan di sini


# --- Menjalankan Bot ---
if __name__ == '__main__':
    logging.info("Menjalankan bot...")
    # client.run_until_disconnected() akan memblokir dan menjaga bot tetap berjalan
    # Bot akan otomatis terhubung saat start().
    client.run_until_disconnected()
