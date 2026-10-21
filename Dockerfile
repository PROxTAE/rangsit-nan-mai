# ภาพเดียวใช้ร่วมกันทั้ง worker / web / cleanup (ดู docker-compose.yml)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Bangkok

# tzdata: ชื่อโฟลเดอร์รายวันและ timestamp ใช้เวลาไทยเหมือนตอนรันบน Windows
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-web.txt ./
# opencv-python ต้องใช้ libGL ซึ่งไม่มีในเซิร์ฟเวอร์ ใช้รุ่น headless แทน (โค้ดที่รันใน container ไม่เปิดหน้าต่าง)
RUN sed -i 's/^opencv-python$/opencv-python-headless/' requirements.txt \
    && pip install -r requirements-web.txt

COPY . .
RUN mkdir -p data/raw results

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
