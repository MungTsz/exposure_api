FROM python:3.11-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /srv/app/

# Road centrelines (~38 MB, Lands Department via CSDI), loaded at startup.
# Rebuild the image to pick up a newer version.
RUN python -m app.services.road_service

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
