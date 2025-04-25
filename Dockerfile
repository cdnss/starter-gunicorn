FROM python:3.9-slim-buster

# Set work directory di dalam container
WORKDIR /app

# Copy requirements.txt dan install dependencies Python
# Gunakan pip install --no-cache-dir untuk menghindari cache dan mengurangi ukuran image
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Instal Dependencies Sistem ---
# Instal aria2c, ffmpeg (untuk post-processing oleh yt-dlp), dan wget/curl
# Instal library yang dibutuhkan untuk browser headless (jika Anda menggunakannya)
# Daftar ini mungkin perlu disesuaikan tergantung pada browser dan base image
RUN apt-get update && apt-get install -y --no-install-recommends \
    aria2 \
    ffmpeg \
    ca-certificates \
    wget \
    curl \
    # Dependencies umum untuk menjalankan browser headless (Chromium/Chrome)
    # Daftar ini bisa sangat panjang dan tergantung pada distro Linux di base image
    libnss3 \
    libfontconfig1 \
    libdbus-glib-1-2 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libgbm1 \
    libxkbcommon0 \
    libpangocairo-1.0-0 \
    libgtk-3-0 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libexpat1 \
    libu2f-udev \
    libvulkan1 \
    fonts-liberation \
    xdg-utils \
    # Membersihkan cache apt untuk mengurangi ukuran image
    && rm -rf /var/lib/apt/lists/*

# Unduh binary yt-dlp standalone (disarankan)
# Ganti dengan versi terbaru jika perlu
RUN wget https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -O /usr/local/bin/yt-dlp \
    && chmod +x /usr/local/bin/yt-dlp

# (Alternatif) Jika Anda menginstal yt-dlp melalui pip, Anda tidak perlu mengunduh binary di atas
# Namun, binary standalone seringkali lebih mudah dikelola dan diperbarui.

# Copy kode bot Anda ke dalam container
COPY bot.py . # Ganti 'bot.py' jika nama file Anda berbeda

# Buat direktori unduhan
ARG DOWNLOAD_DIR="/app/downloads"
RUN mkdir -p $DOWNLOAD_DIR

# Expose port jika Anda menjalankan aria2c RPC server di dalam container (tidak umum)
EXPOSE 6800 # Contoh port default aria2c RPC

# Definisikan Environment Variables untuk konfigurasi bot
# Nilai default bisa dikosongkan atau diisi placeholder
ENV API_ID="25315175"
ENV API_HASH="69f20e99df186f7c694fc3ad69b7ecc4"
ENV BOT_TOKEN="6605145904:AAEUT22p5oi_JK7U93Ld5_Ts_CK8euEHYao"
ENV ARIA2_RPC_URL="http://localhost:6800/rpc" # Ganti jika aria2c berjalan di tempat lain
ENV ARIA2_RPC_SECRET="" # Kosongkan jika tidak ada password
ENV DOWNLOAD_DIR=$DOWNLOAD_DIR # Gunakan ARG DOWNLOAD_DIR yang didefinisikan di atas

# Perintah untuk menjalankan bot saat container dijalankan
# Gunakan python -u untuk unbuffered output (agar log muncul langsung)
CMD ["python", "-u", "bot.py"]
