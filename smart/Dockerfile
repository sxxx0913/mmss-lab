# One image for the emulator, the subscriber and the gateway.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# The scripts keep the layout of the repository: smart/<script>.py. They locate
# the data directory as parents[1]/data, which is /app/data here and the data/
# directory of the repository on the host. Copying them straight to /app would
# make that expression resolve to /data, outside the mounted volume.
COPY requirements.txt ./smart/requirements.txt
RUN pip install --no-cache-dir -r smart/requirements.txt

COPY sensors.py collector.py gateway.py stats.py ./smart/

# data/ is a volume: the databases and the boot counter survive a restart of a
# container and stay out of the repository
RUN mkdir -p /app/data
VOLUME ["/app/data"]

CMD ["python", "smart/sensors.py"]
