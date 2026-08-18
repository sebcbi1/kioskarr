FROM python:3.12-slim

WORKDIR /app

# unar: CBR cover extraction (kioskarr/covers.py) needs a real RAR-reading capability — no
# pure-Python option exists. unar (The Unarchiver's CLI engine) specifically, not the
# official unrar tool: fully LGPL, clean-room implementation, no unRAR-derived licensing
# restrictions (unlike unrar itself, which Debian/Ubuntu exclude from their main repos for
# exactly that reason).
RUN apt-get update && apt-get install -y --no-install-recommends unar \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY kioskarr ./kioskarr
RUN pip install --no-cache-dir .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "kioskarr.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
