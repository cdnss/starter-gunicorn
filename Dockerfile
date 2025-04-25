# Gunakan base image Python slim terbaru
FROM python:3.9-slim-buster

# Set work directory
WORKDIR /app

# Copy requirements.txt dan install dependencies Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Instal Dependencies Sistem ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    aria2 \
    ffmpeg \
    ca-certificates \
    wget \
    curl \
    # Dependencies umum untuk browser headless (jika menggunakan pyppeteer/selenium)
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
    && rm -rf /var/lib/apt/lists/*

# Unduh binary yt-dlp standalone
RUN wget https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -O /usr/local/bin/yt-dlp \
    && chmod +x /usr/local/bin/yt-dlp

# --- TAMBAHKAN BARIS INI UNTUK MENYALIN FILE COOKIES ---
# Pastikan file 'cookies.txt' ada di folder yang sama dengan Dockerfile saat build!
COPY cookies.txt .
# --- AKHIR BARIS COPY COOKIES ---

# Copy kode bot Anda
COPY bot.py .

# Buat direktori unduhan
ARG DOWNLOAD_DIR="/app/downloads"
RUN mkdir -p $DOWNLOAD_DIR

# Expose port untuk server health check
EXPOSE 8080 

# Definisikan Environment Variables
ENV API_ID=""
ENV API_HASH=""
ENV BOT_TOKEN=""
ENV DOWNLOAD_DIR=$DOWNLOAD_DIR
ENV HEALTH_CHECK_PORT="8080"
# --- SETEL COOKIES_FILE_PATH KE LOKASI DI DALAM CONTAINER ---
ENV COOKIES_FILE_PATH="/app/cookies.txt" 
# --- AKHIR SETEL ENV COOKIES_FILE_PATH ---

# Perintah untuk menjalankan bot
CMD ["python", "-u", "bot.py"] 
