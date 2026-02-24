FROM python:3.12-slim
WORKDIR /app

# Layer 1: webui deps (changes rarely)
COPY docker/webui/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Layer 2: library source — editable install so volume-mounted source is used directly
COPY pyproject.toml /pkg/pyproject.toml
COPY powerdns_migrator/ /pkg/powerdns_migrator/
RUN pip install --no-cache-dir -e /pkg

# Layer 3: app (changes often)
COPY docker/webui/app.py /app/app.py

EXPOSE 8080
# --reload watches /app (app.py) and /pkg (powerdns_migrator source) for changes
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--reload", "--reload-dir", "/app", "--reload-dir", "/pkg"]
