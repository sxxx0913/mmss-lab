# One image for the emulator, the subscriber and the gateway.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sensors.py collector.py gateway.py stats.py ./

# data/ is mounted as a volume: the databases and the boot counter live there
RUN mkdir -p /app/data
VOLUME ["/app/data"]

CMD ["python", "sensors.py"]
