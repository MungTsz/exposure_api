FROM python:3.11-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# This checkout has no __init__-less parent folder of its own, so it's copied
# in as the "exposure_api" package (see CLAUDE.md: "this directory is itself
# the importable package"), with /srv as the parent dir uvicorn runs from.
COPY . /srv/exposure_api

EXPOSE 8000

CMD ["sh", "-c", "uvicorn exposure_api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
