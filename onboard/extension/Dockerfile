# DeepVertex Raw Capture Agent — BlueOS extension (scaffold; verifikasi di Langkah C)
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        gstreamer1.0-tools \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
    && rm -rf /var/lib/apt/lists/*

# salin dulu dari folder induk:  cp ../raw_agent.py .
COPY raw_agent.py /app/raw_agent.py
WORKDIR /app

EXPOSE 8765
CMD ["python3", "/app/raw_agent.py", "--port", "8765", "--dir", "/data/raw", \
     "--device-left", "/dev/video0", "--device-right", "/dev/video2", \
     "--chunk-mb", "1024", "--min-free-mb", "3000"]
